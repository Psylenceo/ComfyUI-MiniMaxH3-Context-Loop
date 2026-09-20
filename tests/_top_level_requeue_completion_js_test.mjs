#!/usr/bin/env node
// Issue #75: actual browser coordinator with deterministic ComfyUI adapters.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import * as core from "../web/h3_chain_top_level_requeue_core.mjs";
import * as coordinator from "../web/h3_chain_top_level_requeue_coordinator.mjs";
import {appendedReviewPrompts} from "../web/h3_chain_review_append.mjs";
import {activeSceneFromOutput} from "../web/h3_chain_cancel_reroll_core.mjs";

const source = fs.readFileSync(new URL("../web/h3_chain_top_level_requeue.js", import.meta.url), "utf8");
function harness({startClip = 1, sceneRange = "", endClip = 3, total = 3, nested = false, branchId = "main"} = {}) {
    let extension, scene = startClip, queued = 0, promptId = "initial";
    const start = {id: 1, comfyClass: "MiniMaxH3ChainLoopStart", widgets: [
        {name: "start_clip", value: startClip}, {name: "scene_range", value: sceneRange},
    ]};
    const plan = {id: 2, comfyClass: "MiniMaxH3ChainPlan", widgets: [
        {name: "run_name", value: "run"}, {name: "plan_json", value: JSON.stringify({_branch_id: branchId})},
    ]};
    const current = {id: 3, comfyClass: "MiniMaxH3ChainCurrent", inputs: [{link: 1}, {link: 2}]};
    const end = {id: 4, comfyClass: "MiniMaxH3ChainLoopEnd", inputs: [{link: 3}]};
    const graph = {
        nodes: [start, plan, current, end],
        links: {1: {origin_id: 1}, 2: {origin_id: 2}, 3: {origin_id: 3}},
        getNodeById(id) { return this.nodes.find(node => node.id === id); },
        setDirtyCanvas() {},
    };
    graph.nodes.forEach(node => { node.graph = graph; });
    const root = nested ? {nodes: [{id: 9, subgraph: graph}], getNodeById(id) { return this.nodes.find(node => node.id === id); }} : graph;
    const notices = [], submissions = [];
    const response = body => ({ok: true, status: 200, json: async () => body});
    const app = {
        graph: root,
        registerExtension(value) { extension = value; },
        extensionManager: {workflow: {activeWorkflow: {path: "workflow-a"}}, setting: {get: id => id.includes("Cleanup") ? 0 : true}},
        graphToPrompt: async () => ({start: start.widgets[0].value, range: start.widgets[1].value}),
    };
    const api = {
        queuePrompt: async (_position, prompt) => { submissions.push(prompt); queued++; return {prompt_id: "auto-" + queued}; },
        fetchApi: async (url, options = {}) => {
            if (url === "/api/queue") return response({queue_running: [], queue_pending: []});
            if (url.includes("/checkpoints?")) return response({checkpoints: [{scene, ready: true, revision: "rev", metadata_sha256: "sha"}]});
            if (url.includes("/handoffs?")) return response({handoffs: [{
                action: "next_scene", status: "pending", handoff_id: "handoff-" + scene,
                predecessor_scene: scene, start_clip: scene + 1, end_clip: endClip,
                workflow_fingerprint: "fp", source_revision: "rev", source_checkpoint_sha256: "sha",
                working_branch_id: branchId,
                resume: {start_clip: scene + 1, scene_range: endClip < total ? (scene + 1 === endClip ? String(endClip) : (scene + 1) + ":" + endClip) : ""},
            }]});
            if (url.endsWith("/claim") || url.endsWith("/transition") || url.endsWith("/release")) return response({});
            throw new Error("Unexpected API call " + url);
        },
    };
    const context = {
        ...core, ...coordinator, app, api, activeSceneFromOutput, appendedReviewPrompts,
        createNotificationStack: () => ({show: (...args) => notices.push(args), clear() {}, clearAll() {}}),
        projectMutationOptions: async (_node, _run, options) => options,
        window: {setTimeout}, console,
    };
    vm.createContext(context);
    vm.runInContext(source.replace(/^import[\s\S]*?from "[^"]+";\n/gm, "").replace(/^export /gm, "") +
        "\nglobalThis.handlers = {onExecuted, onExecutionSuccess, onTerminalFailure, onContinuationStart};", context);
    const handlers = context.handlers;
    const selection = () => ({start: start.widgets[0].value, range: start.widgets[1].value});
    const prefix = nested ? "9:" : "";
    function active(index, id = "auto-" + queued) {
        scene = index;
        promptId = id;
        handlers.onContinuationStart({prompt_id: id});
        handlers.onExecuted({prompt_id: id, display_node: prefix + "3", output: {h3_chain_active_scene: [{
            run_name: "run", clip_index: scene, clip_count: total, end_clip: endClip, shot_id: "scene-" + scene,
            workflow_fingerprint: "fp", _branch_id: branchId,
        }]}});
    }
    const emitEnd = (output, display = prefix + "4") => handlers.onExecuted({prompt_id: promptId, display_node: display, output});
    function finished(overrides = {}) {
        emitEnd({h3_chain_top_level_complete: [{
            run_name: "run", scene, end_clip: endClip, workflow_fingerprint: "fp",
            working_branch_id: branchId, ...overrides,
        }]});
    }
    const success = () => handlers.onExecutionSuccess({prompt_id: promptId});
    async function advance() {
        const previousQueued = queued;
        emitEnd({h3_chain_top_level_requeue: [{
            run_name: "run", predecessor_scene: scene, scene: scene + 1, end_clip: endClip,
            workflow_fingerprint: "fp", handoff_id: "handoff-" + scene,
        }]});
        success();
        await new Promise(setImmediate);
        assert.equal(notices.some(args => args[0] === "requeue-error"), false, JSON.stringify(notices));
        assert.equal(queued, previousQueued + 1, "a real next-scene prompt was submitted");
        active(scene + 1);
    }
    active(startClip, "initial");
    assert.equal(extension.name, "minimax_h3_context_loop.top_level_requeue");
    return {selection, advance, active, finished, success, emitEnd, start, plan, graph, app,
        fail: kind => handlers.onTerminalFailure(kind, {prompt_id: promptId}),
        submissions, queueCount: () => queued,
    };
}

// Real handoffs advance both controls, then only terminal success restores.
for (const nested of [false, true]) {
    const h = harness({nested});
    await h.advance();
    assert.deepEqual(h.selection(), {start: 2, range: ""});
    await h.advance();
    assert.deepEqual(h.selection(), {start: 3, range: ""});
    h.finished();
    assert.equal(h.selection().start, 3, "Loop End is not yet whole-prompt success");
    h.success();
    assert.deepEqual(h.selection(), {start: 1, range: ""});
    assert.deepEqual(h.submissions, [{start: 2, range: ""}, {start: 3, range: ""}]);
    h.success();
    assert.equal(h.queueCount(), 2, "duplicate success cannot queue/reset again");
}

// Issue #90: an appended-scene continuation must survive the old final event.
{
    const h = harness({total: 2, endClip: 2});
    await h.advance();
    appendedReviewPrompts.add("auto-1");
    h.finished(); h.success();
    assert.equal(h.selection().start, 2,
        "an approved appended-scene continuation owns the upcoming resume selection");
    assert.equal(h.queueCount(), 1, "the old coordinator must not also queue an extension");
    appendedReviewPrompts.delete("auto-1");
}

// Preserve an intentional resume point and restore the original range verbatim.
for (const options of [
    {startClip: 2, endClip: 3, total: 3},
    {startClip: 2, sceneRange: "2:4", endClip: 4, total: 6},
]) {
    const h = harness(options);
    const original = h.selection();
    while (h.selection().start < options.endClip) await h.advance();
    if (options.sceneRange) assert.deepEqual(h.selection(), {start: 4, range: "4"});
    h.finished(); h.success();
    assert.deepEqual(h.selection(), original);
}

// Approve & Stop is success with no Loop End, including on the last scene.
// End the old snapshot session without changing the current resume controls.
for (const lastScene of [false, true]) {
    const h = harness();
    await h.advance();
    if (lastScene) await h.advance();
    const paused = h.selection();
    h.success();
    assert.deepEqual(h.selection(), paused);
    if (!lastScene) {
        h.active(2, "manual-resume");
        await h.advance(); h.finished(); h.success();
        assert.deepEqual(h.selection(), paused, "new manual run restores its own original start");
    }
}

// Downstream error, interruption, and missing completion cannot cause reset.
for (const kind of ["error", "interrupted"]) {
    const h = harness();
    await h.advance(); await h.advance(); h.finished();
    h.fail(kind); h.success();
    assert.equal(h.selection().start, 3);
}
const missing = harness();
await missing.advance(); await missing.advance(); missing.success();
assert.equal(missing.selection().start, 3);

// Do not trust the scene number alone, an unrelated Loop End, or another run.
for (const invalid of [{run_name: "other"}, {scene: 2}, {end_clip: 4},
    {workflow_fingerprint: "other"}, {working_branch_id: "other"}]) {
    const h = harness();
    await h.advance(); await h.advance(); h.finished(invalid); h.success();
    assert.equal(h.selection().start, 3);
}
const unrelated = harness();
await unrelated.advance(); await unrelated.advance();
const otherCurrent = {id: 30, comfyClass: "MiniMaxH3ChainCurrent", inputs: [], graph: unrelated.graph};
const otherEnd = {id: 40, comfyClass: "MiniMaxH3ChainLoopEnd", inputs: [{link: 30}], graph: unrelated.graph};
unrelated.graph.nodes.push(otherCurrent, otherEnd);
unrelated.graph.links[30] = {origin_id: 30};
unrelated.emitEnd({h3_chain_top_level_complete: [{run_name: "run", scene: 3, end_clip: 3, workflow_fingerprint: "fp"}]}, "40");
unrelated.success();
assert.equal(unrelated.selection().start, 3);

// Manual edits and workflow/run changes must not be overwritten by completion.
for (const change of [
    h => { h.start.widgets[0].value = 2; },
    h => { h.start.widgets[1].value = "2:3"; },
    h => { h.app.extensionManager.workflow.activeWorkflow.path = "other-workflow"; },
    h => { h.plan.widgets[0].value = "other-run"; },
]) {
    const h = harness();
    await h.advance(); await h.advance();
    change(h);
    const edited = h.selection();
    h.finished(); h.success();
    assert.deepEqual(h.selection(), edited);
}

// A node from another/reloaded graph is not the one we advanced, even with
// identical node IDs and no usable frontend workflow identity.
const replaced = harness();
await replaced.advance(); await replaced.advance(); replaced.finished();
replaced.app.extensionManager.workflow.activeWorkflow = null;
const fresh = {id: 1, comfyClass: "MiniMaxH3ChainLoopStart", widgets: [
    {name: "start_clip", value: 3}, {name: "scene_range", value: ""},
], graph: replaced.graph};
replaced.graph.nodes[0] = fresh;
replaced.success();
assert.equal(fresh.widgets[0].value, 3);
assert.equal(replaced.selection().start, 3);

const single = harness({startClip: 3});
single.finished(); single.success();
assert.equal(single.selection().start, 3, "without auto-advance, leave intentional single-scene input alone");

// Nightly's working branches keep both completion and live UI restoration
// bound to the same branch. Main has no branch selector.
if (core.migrateRecursiveExecutionMode) {
    for (const switchBranch of [false, true]) {
        const h = harness({branchId: "a".repeat(32)});
        await h.advance(); await h.advance();
        if (switchBranch) h.plan.widgets[1].value = JSON.stringify({_branch_id: "b".repeat(32)});
        h.finished(); h.success();
        assert.equal(h.selection().start, switchBranch ? 3 : 1);
    }
}

console.log("Top-level completion: advance, restore, stop, failure, range, subgraph, and workflow-isolation cases pass");
