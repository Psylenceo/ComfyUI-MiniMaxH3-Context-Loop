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
