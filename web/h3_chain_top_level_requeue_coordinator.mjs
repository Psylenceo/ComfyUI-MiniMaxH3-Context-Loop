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
