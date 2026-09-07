// Testable delivery primitive shared by the browser coordinator.  It keeps
// prompt submission certainty separate from the UI/event wiring.
export async function submitWithPromptIdentity({app, api}) {
    if (typeof app.graphToPrompt === "function" && typeof api.queuePrompt === "function") {
        const prompt = await app.graphToPrompt(app.graph);
        const result = await api.queuePrompt(0, prompt);
        const promptId = String(result?.prompt_id ?? "");
        return promptId ? {kind: "accepted", promptId} : {kind: "rejected", promptId: ""};
    }
    const result = await app.queuePrompt(0, 1);
    if (result === false) return {kind: "rejected", promptId: ""};
    const promptId = typeof result === "object" ? String(result.prompt_id ?? result.promptId ?? "") : "";
    return promptId ? {kind: "accepted", promptId} : {kind: "uncertain", promptId: ""};
}

// Shared async cancellation boundary.  Browser wiring supplies the concrete
// queue/checkpoint/workflow adapters; Node tests use deterministic fakes.
export async function runRequeueLifecycle({current, waitSafe, cleanup, select, claim, prepare, submit, release, uncertain}) {
    current(); await waitSafe(); current(); await cleanup(); current();
    const handoff = await select();
    if (!handoff) return null;
    await claim(handoff);
    try {
        current();
        await prepare(handoff); current();
        const result = await submit(handoff);
        if (result?.kind === "rejected") { await release(handoff); return result; }
        if (result?.kind !== "accepted") { await uncertain(handoff); return {kind: "uncertain"}; }
        return result;
    } catch (error) {
        if (error?.preDelivery === true || submissionFailure(error) === "rejected") { await release(handoff); return {kind: "rejected"}; }
        await uncertain(handoff); return {kind: "uncertain"};
    }
}

// Identity selection is kept in the coordinator boundary so callers cannot
// accidentally fall back to list order. `match` is the shared strict matcher.
export function resolveProjectRun(planNode) {
    const widget = (node, name) => node?.widgets?.find((item) => item.name === name)?.value;
    const input = planNode?.inputs?.find((item) => item.name === "project_assets");
    const link = input?.link != null ? planNode?.graph?.links?.[input.link] : null;
    const manager = link ? planNode?.graph?.getNodeById?.(link.origin_id) : null;
    const managed = manager?.comfyClass === "MiniMaxH3ProjectAssetManager"
        ? String(widget(manager, "run_name") ?? "").trim() : "";
    return managed || String(widget(planNode, "run_name") ?? "").trim();
}

export async function selectAndClaim({record, handoffs, match, resolveRun, claim}) {
    const runName = resolveRun(record);
    if (!runName || runName !== record.runName) return null;
    const handoff = match(handoffs, record);
    if (!handoff) return null;
    await claim(runName, handoff.handoff_id);
    return handoff;
}

export function submissionFailure(error) {
    return Number(error?.status) >= 400 && Number(error?.status) < 500
        ? "rejected" : "uncertain";
}

// Prompt-specific continuation bookkeeping is deliberately tiny and has no
// browser dependency, so the production wrapper and deterministic tests share
// the same acceptance/start/failure identity rules.
export function createContinuationTracker({transition, reportError = () => {}}) {
    let waiting = null;
    return {
        track(runName, handoffId, promptId) {
            waiting = {runName, handoffId, promptId: String(promptId)};
        },
        async started(promptId) {
            if (!waiting || waiting.promptId !== String(promptId)) return false;
            const current = waiting;
            waiting = null;
            try {
                await transition(current.runName, current.handoffId, "consumed");
                return true;
            } catch (error) {
                reportError(error);
                return false;
            }
        },
        failed(promptId) {
            if (!waiting || waiting.promptId !== String(promptId)) return null;
            const current = waiting;
            waiting = null;
            return current;
        },
        current() { return waiting; },
    };
}
