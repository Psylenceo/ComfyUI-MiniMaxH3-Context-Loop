export const RETIRED_SOURCE_AUDIO_NODES = new Set([
    "MiniMaxH3ChainPlanStudio", "MiniMaxH3ChainPreflight",
    "MiniMaxH3ChainLoopStart", "MiniMaxH3ChainCurrent",
    "MiniMaxH3ChainReview", "MiniMaxH3ChainManifestLoad",
    "MiniMaxH3ChainLatentVideoAdapter", "MiniMaxH3ChainAssemble",
]);

export function retireSourceAudioInput(node) {
    if (!RETIRED_SOURCE_AUDIO_NODES.has(node?.comfyClass ?? node?.type)) return false;
    const index = node.inputs?.findIndex(input => input.name === "source_audio") ?? -1;
    if (index < 0) return false;
    const input = node.inputs[index];
    if (input.link != null) {
        // Do not silently discard a soundtrack or repurpose its connection.
        input.label = "REMOVED: route audio through Source Timeline";
        input.hidden = false;
        return false;
    }
    // Run after graph loading, when LiteGraph can also update target_slot on
    // subsequent links. Never splice the saved input array before link restore.
    if (typeof node.removeInput !== "function") return false;
    node.removeInput(index);
    return true;
}
