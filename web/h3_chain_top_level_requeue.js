import {app} from "/scripts/app.js";
import {api} from "/scripts/api.js";
import {activeSceneFromOutput} from "./h3_chain_cancel_reroll_core.mjs?v=0.6.5";
import {
    DEFAULT_CLEANUP_DELAY_MS,
    HANDOFF_API_BASE,
    checkpointPredecessorReady,
    cleanupDelayMs,
    isQueueSafe,
    pendingNextSceneHandoffs,
    predecessorScene,
    resumeHint,
} from "./h3_chain_top_level_requeue_core.mjs?v=0.6.5";
import {createNotificationStack} from "./h3_notification_stack_core.mjs?v=0.6.2";

// Top-level scene requeue coordinator (M3, candidate_count = 1).
//
// After a successful top-level terminal event, the previous heavyweight H3
// prompt is done and its Loop End has already written a durable next_scene
// handoff (instead of recursively expanding the next scene). This extension:
//   1. waits for a safe queue state (no running/queued jobs),
//   2. waits the configurable cleanup interval measured from terminal success,
//   3. re-validates the visible workflow, run_name, and predecessor checkpoint,
//   4. claims the pending handoff exactly once,
//   5. sets the existing Loop Start resume widgets (start_clip / scene_range),
//   6. queues the SAME existing workflow as a NEW top-level prompt,
//   7. marks the handoff queued, then consumed when the new prompt's Loop
//      Start executes.
//
// The Plan JSON is never modified by this coordinator: it only drives the
// existing Loop Start resume controls. Failures keep the durable handoff and
// tell the user how to resume manually. A browser/server restart never
// auto-runs stale work; it only shows the pending recoverable state.

const SETTING_ID = "MiniMaxH3ContextLoop.topLevelRequeue";
const DELAY_SETTING_ID =
    "MiniMaxH3ContextLoop.topLevelRequeueCleanupDelay";
const START_TYPE = "MiniMaxH3ChainLoopStart";
const END_TYPE = "MiniMaxH3ChainLoopEnd";
const CURRENT_TYPES = new Set([
    "MiniMaxH3ChainCurrent",
    "MiniMaxH3CurrentTaggedReferenceScene",
]);
const PLAN_TYPES = new Set(["MiniMaxH3ChainPlan", "MiniMaxH3ChainPlanModern"]);
const QUEUE_POLL_INTERVAL_MS = 500;
const QUEUE_WAIT_TIMEOUT_MS = 10 * 60 * 1000;
const MAX_OBSERVED_PROMPTS = 100;
const TRANSIENT_NOTICE_MS = 5000;

let notifications = null;
let pumpActive = false;
let continuationWait = null; // {runName, handoffId} after queuePrompt
const sceneRecords = new Map();   // prompt_id -> observed H3 scene record
const requeueQueue = [];
const hintedRuns = new Set();     // run_names already shown a pending hint

function nodeType(node) {
    return node?.comfyClass ?? node?.type ?? null;
}

function findNodeByDisplayId(qid) {
    if (!app.graph || qid == null) return null;
    const parts = String(qid).split(":");
    let graph = app.graph;
    for (let index = 0; index < parts.length - 1; index += 1) {
        const id = Number(parts[index]);
        const parent = Number.isFinite(id) ? graph?.getNodeById?.(id) : null;
        if (!parent?.subgraph) return null;
        graph = parent.subgraph;
    }
    const leaf = Number(parts.at(-1));
    return Number.isFinite(leaf) ? graph?.getNodeById?.(leaf) ?? null : null;
}

function activeWorkflowIdentity() {
    const workflow = app.extensionManager?.workflow?.activeWorkflow;
    const value = workflow?.path
        ?? workflow?.activeState?.id
        ?? workflow?.filename
        ?? null;
    if (value == null || String(value).trim() === "") return null;
    return String(value);
}

function findUpstreamNode(start, wantedType) {
    const queue = [start];
    const seen = new Set();
    while (queue.length) {
        const node = queue.shift();
        if (!node || seen.has(node)) continue;
        seen.add(node);
        const matches = wantedType instanceof Set
            ? wantedType.has(nodeType(node)) : nodeType(node) === wantedType;
        if (node !== start && matches) return node;
        for (const input of node.inputs ?? []) {
            if (input.link == null) continue;
            const link = node.graph?.links?.[input.link];
            const parent = link ? node.graph?.getNodeById?.(link.origin_id) : null;
            if (parent) queue.push(parent);
        }
    }
    return null;
}

function widgetByName(node, name) {
    return node?.widgets?.find((item) => item.name === name);
}

function requeueIcon() {
    const namespace = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(namespace, "svg");
    svg.classList.add("h3trq-icon");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(namespace, "path");
    path.setAttribute("d",
        "M20 7v5h-5M4 17v-5h5M6.1 8.2A7 7 0 0 1 18.7 7M17.9 15.8A7 7 0 0 1 5.3 17");
    svg.append(path);
    return svg;
}

function ensureNotifications() {
    notifications ??= createNotificationStack({
        anchorSelector: ".h3cr-root",
    });
    return notifications;
}

function showTransient(message) {
    ensureNotifications().show("requeue-transient", message, "info", {
        durationMs: TRANSIENT_NOTICE_MS,
    });
}

function showWarning(message) {
    ensureNotifications().clear("requeue-transient");
    ensureNotifications().show("requeue-warning", message, "warning");
}

function showError(message) {
    ensureNotifications().clear("requeue-transient");
    ensureNotifications().show("requeue-error", message, "error");
}

function clearNotifications() {
    notifications?.clearAll();
}

function settingEnabled() {
    return app.ui?.settings?.getSettingValue?.(SETTING_ID) === true;
}

function sleep(milliseconds) {
    return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function safeJson(response) {
    try {
        return await response.json();
    } catch (_error) {
        return null;
    }
}

function trimObservedPrompts() {
    while (sceneRecords.size > MAX_OBSERVED_PROMPTS) {
        const oldest = sceneRecords.keys().next().value;
        sceneRecords.delete(oldest);
    }
}

function onExecuted(detail) {
    const promptId = String(detail?.prompt_id ?? "");
    if (!promptId || detail?.display_node == null) return;
    const node = findNodeByDisplayId(detail.display_node);
    const type = nodeType(node);
    if (type === START_TYPE && continuationWait
        && !sceneRecords.has(promptId)) {
        // The first node of the continuation prompt executed: the claimed
        // handoff is now genuinely consumed. Clear the transient queueing
        // notice here so the browser can actually render it long enough to
        // verify the dedicated stack during the requeue window.
        clearNotifications();
        const wait = continuationWait;
        continuationWait = null;
        void (async () => {
            try {
                await postHandoffTransition(
                    wait.runName, wait.handoffId, "consumed");
            } catch (error) {
                // The scene is running; bookkeeping stays durable.
                showError(`Marking the handoff consumed failed: `
                    + `${error?.message || error}`);
            }
        })();
    }
    let record = sceneRecords.get(promptId);
    if (!record) {
        record = {
            promptId,
            runName: "",
            clipIndex: 0,
            clipCount: 0,
            endClip: 0,
            shotId: "",
            loopEndExecuted: false,
            displayNode: null,
            workflowIdentity: activeWorkflowIdentity(),
        };
        sceneRecords.set(promptId, record);
        trimObservedPrompts();
    }
    if (CURRENT_TYPES.has(type)) {
        const scene = activeSceneFromOutput(detail?.output);
        if (scene) {
            record.runName = scene.runName;
            record.clipIndex = scene.clipIndex;
            record.clipCount = scene.clipCount;
            record.endClip = scene.endClip;
            record.shotId = scene.shotId;
            record.displayNode = String(detail.display_node);
        }
    } else if (type === END_TYPE) {
        record.loopEndExecuted = true;
    }
}

function enqueueRequeue(record) {
    requeueQueue.push(record);
    void pumpRequeues();
}

async function pumpRequeues() {
    if (pumpActive) return;
    pumpActive = true;
    try {
        while (requeueQueue.length) {
            const record = requeueQueue.shift();
            await processRequeue(record);
        }
    } finally {
        pumpActive = false;
    }
}

function onExecutionSuccess(detail) {
    const promptId = String(detail?.prompt_id ?? "");
    const record = sceneRecords.get(promptId);
    if (continuationWait && sceneRecords.has(promptId)
        && !record?.loopEndExecuted) {
        // Continuation prompt finished before its Loop Start event arrived;
        // the consumed bookkeeping is best-effort and stays durable.
        continuationWait = null;
    }
    if (!record) return;
    sceneRecords.delete(promptId);
    if (record.runName && Number(record.clipIndex) < Number(record.endClip || record.clipCount)) {
        enqueueRequeue(record);
    }
}

function onTerminalFailure(kind, detail) {
    const promptId = String(detail?.prompt_id ?? "");
    sceneRecords.delete(promptId);
    if (continuationWait && !sceneRecords.has(promptId)) {
        // The continuation prompt ended before/without a recorded Loop Start
        // execution. The handoff deliberately stays queued: no auto-retry.
        const wait = continuationWait;
        continuationWait = null;
        showError(
            `The requeued prompt for run "${wait.runName}" ended `
            + `${kind === "interrupted" ? "interrupted" : "with an error"} `
            + "before its scene started. The handoff stays queued; set Loop "
            + `Start to the handoff scene and queue manually to resume.`);
    }
}

function requireVisibleWorkflow(record) {
    const workflowIdentity = activeWorkflowIdentity();
    if (record.workflowIdentity && workflowIdentity
        && workflowIdentity !== record.workflowIdentity) {
        throw new Error("Return to the running H3 workflow before requeueing.");
    }
    const currentNode = record.displayNode
        ? findNodeByDisplayId(record.displayNode) : null;
    const startNode = currentNode
        ? findUpstreamNode(currentNode, START_TYPE) : null;
    const planNode = currentNode
        ? findUpstreamNode(currentNode, PLAN_TYPES) : null;
    const runName = String(widgetByName(planNode, "run_name")?.value ?? "").trim();
    if (!currentNode || !startNode || !planNode
        || !record.runName || runName !== record.runName) {
        throw new Error("Return to the running H3 workflow before requeueing.");
    }
    return {startNode, planNode, runName};
}

async function waitForSafeQueue() {
    const started = Date.now();
    for (;;) {
        let safe = false;
        try {
            const response = await api.fetchApi("/api/queue");
            if (response.ok) safe = isQueueSafe(await response.json());
        } catch (_error) {
            safe = false;
        }
        if (safe) return;
        if (Date.now() - started > QUEUE_WAIT_TIMEOUT_MS) {
            throw new Error(
                "The queue did not reach a safe state within 10 minutes.");
        }
        await sleep(QUEUE_POLL_INTERVAL_MS);
    }
}

async function verifyPredecessorCheckpoint(runName, predecessor) {
    if (predecessor < 1) return;
    const response = await api.fetchApi(
        `${HANDOFF_API_BASE}/checkpoints?run_name=${encodeURIComponent(runName)}`);
    const body = await safeJson(response);
    if (!response.ok) {
        throw new Error(
            `Cannot verify checkpoint ${predecessor} `
            + `(HTTP ${response.status}).`);
    }
    if (!checkpointPredecessorReady(body?.checkpoints, predecessor)) {
        throw new Error(
            `Checkpoint ${predecessor} is not ready; resume manually once `
            + "the segment transaction has saved it.");
    }
}

async function postHandoffTransition(runName, handoffId, status) {
    const response = await api.fetchApi(
        `${HANDOFF_API_BASE}/handoffs/transition`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                run_name: runName,
                handoff_id: handoffId,
                status,
            }),
        });
    if (!response.ok) {
        const body = await safeJson(response);
        throw new Error(body?.error
            || `Handoff transition failed (HTTP ${response.status}).`);
    }
}

async function processRequeue(record) {
    const startedAt = Date.now();
    try {
        if (!record.runName) {
            throw new Error("The active H3 run_name is empty.");
        }
        showTransient("Waiting for a safe queue state…");
        await waitForSafeQueue();
        const delay = cleanupDelayMs(
            app.ui?.settings?.getSettingValue?.(DELAY_SETTING_ID));
        const remaining = delay - (Date.now() - startedAt);
        if (remaining > 0) {
            showTransient(`Waiting for the cleanup interval… `
                + `${Math.ceil(remaining / 1000)}s`);
            await sleep(remaining);
        }
        showTransient("Checking the workflow and predecessor checkpoint…");
        const {startNode, runName} = requireVisibleWorkflow(record);
        const listResponse = await api.fetchApi(
            `${HANDOFF_API_BASE}/handoffs?run_name=${encodeURIComponent(runName)}`);
        if (!listResponse.ok) {
            throw new Error(
                `The handoff list is unavailable (HTTP ${listResponse.status}).`);
        }
        const pending = pendingNextSceneHandoffs(await listResponse.json());
        if (!pending.length) {
            // Legacy recursion already handled this scene (or another client
            // already did). The backend is the source of truth: nothing to do.
            clearNotifications();
            return;
        }
        const handoff = pending[0];
        const resume = resumeHint(handoff);
        if (!resume) {
            throw new Error(
                "The handoff has no resume hint; resume the scene manually.");
        }
        await verifyPredecessorCheckpoint(
            runName, predecessorScene(resume));
        const claimResponse = await api.fetchApi(
            `${HANDOFF_API_BASE}/handoffs/claim`, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    run_name: runName,
                    handoff_id: handoff.handoff_id,
                    source_prompt_id: record.promptId,
                }),
            });
        if (claimResponse.status === 409) {
            showWarning("The handoff was already claimed; nothing was queued.");
            return;
        }
        if (!claimResponse.ok) {
            const errorBody = await safeJson(claimResponse);
            throw new Error(errorBody?.error
                || `Claiming the handoff failed (HTTP ${claimResponse.status}).`);
        }
        let queued = false;
        try {
            const startWidget = widgetByName(startNode, "start_clip");
            const rangeWidget = widgetByName(startNode, "scene_range");
            if (!startWidget) {
                throw new Error(
                    "Loop Start is missing its start_clip widget.");
            }
            startWidget.value = resume.startClip;
            startWidget.callback?.(resume.startClip);
            if (rangeWidget) {
                rangeWidget.value = resume.sceneRange;
                rangeWidget.callback?.(resume.sceneRange);
            }
            startNode.graph?.setDirtyCanvas?.(true, true);
            showTransient(
                `Queueing scene ${resume.startClip} as a new top-level prompt…`);
            await app.queuePrompt(0, 1);
            queued = true;
            continuationWait = {
                runName,
                handoffId: String(handoff.handoff_id),
            };
            await postHandoffTransition(
                runName, handoff.handoff_id, "queued");
        } catch (error) {
            if (!queued) {
                try {
                    await api.fetchApi(
                        `${HANDOFF_API_BASE}/handoffs/release`, {
                            method: "POST",
                            headers: {"Content-Type": "application/json"},
                            body: JSON.stringify({
                                run_name: runName,
                                handoff_id: handoff.handoff_id,
                                reason: String(error?.message || error),
                            }),
                        });
                } catch (_releaseError) {
                    // Keep the durable claim; manual recovery can release it.
                }
            }
            throw error;
        }
    } catch (error) {
        showError(
            `Top-level requeue did not queue: ${error?.message || error} `
            + "The run's checkpoints are intact; set Loop Start to the "
            + "handoff scene and queue the workflow manually.");
    }
}

function findPlanRunName() {
    const graph = app.graph;
    if (!graph?.nodes) return null;
    for (const node of graph.nodes) {
        if (!PLAN_TYPES.has(nodeType(node))) continue;
        const value = String(widgetByName(node, "run_name")?.value ?? "").trim();
        if (value) return value;
    }
    return null;
}

async function checkPendingHandoffs() {
    const runName = findPlanRunName();
    if (!runName || hintedRuns.has(runName)) return;
    try {
        const response = await api.fetchApi(
            `${HANDOFF_API_BASE}/handoffs?run_name=${encodeURIComponent(runName)}`);
        if (!response.ok) return;
        const pending = pendingNextSceneHandoffs(await response.json());
        if (pending.length) {
            const resume = resumeHint(pending[0]);
            hintedRuns.add(runName);
            showWarning(
                `Pending H3 handoff for run "${runName}": scene `
                + `${resume?.startClip ?? "?"} is resumable. Set Loop Start to `
                + `scene ${resume?.startClip ?? "?"} and queue manually, or `
                + "enable top-level auto requeue to let it claim after "
                + "a terminal success. Nothing auto-runs on startup.");
        }
    } catch (_error) {
        // Server not ready yet; the next graph change retries.
    }
}

app.registerExtension({
    name: "minimax_h3_context_loop.top_level_requeue",
    init() {
        app.ui?.settings?.addSetting?.({
            id: SETTING_ID,
            category: [
                "MiniMax H3 Context Loop", "Interface", "Top-level requeue",
            ],
            name: "Auto requeue next scene as a new top-level prompt",
            tooltip: "After a successful H3 terminal event, wait for a safe queue and the cleanup interval, claim the next-scene handoff, set Loop Start, and queue the same workflow as a new prompt.",
            type: "boolean",
            defaultValue: false,
            onChange() {
                if (!settingEnabled()) clearNotifications();
            },
        });
        app.ui?.settings?.addSetting?.({
            id: DELAY_SETTING_ID,
            category: [
                "MiniMax H3 Context Loop", "Interface", "Top-level requeue",
            ],
            name: "Requeue cleanup interval (ms)",
            tooltip: `Milliseconds to wait after the top-level terminal success before queueing the next scene (default ${DEFAULT_CLEANUP_DELAY_MS}).`,
            type: "number",
            defaultValue: DEFAULT_CLEANUP_DELAY_MS,
        });
    },
    setup() {
        api.addEventListener("executed", (event) => onExecuted(event.detail));
        api.addEventListener("execution_success", (event) =>
            onExecutionSuccess(event.detail));
        api.addEventListener("execution_error", (event) =>
            onTerminalFailure("error", event.detail));
        api.addEventListener("execution_interrupted", (event) =>
            onTerminalFailure("interrupted", event.detail));
        api.addEventListener("graphChanged", () => {
            void checkPendingHandoffs();
        });
        // A recovered/opened workflow may load before extension setup; retry
        // shortly after load. Still only shows state — never auto-runs.
        window.setTimeout(() => {
            void checkPendingHandoffs();
        }, 1500);
    },
});
