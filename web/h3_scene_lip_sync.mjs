import {sceneAudioPolicy} from "./h3_policy_core.mjs?v=0.7.10";

export function normalizeSceneLipSyncSource(value) {
    if (value == null) return null;
    if (!value || typeof value !== "object" || !String(value.asset_id ?? "").trim()) {
        throw new Error("Scene lip-sync source needs a carousel audio asset ID.");
    }
    const start = Number(value.start_seconds ?? 0);
    if (!Number.isFinite(start) || start < 0) {
        throw new Error("Scene lip-sync source start must be finite and non-negative.");
    }
    const mode = value.final_audio ?? "mix";
    if (!["mix", "replace"].includes(mode)) throw new Error("Choose mix or replace for scene dialogue.");
    return {asset_id:String(value.asset_id).trim(),
        start_seconds:Math.round(start * 24) / 24, final_audio:mode};
}

export function sceneLipSyncPlayback(shots, policy, segments, seconds) {
    const segment = segments.find(item => item.kind === "scene"
        && seconds >= item.startSeconds
        && seconds < item.startSeconds + item.durationSeconds);
    if (!segment) return null;
    const shot = shots[segment.sceneIndex];
    if (!shot?.lip_sync_source || sceneAudioPolicy(shot, policy).sourceAudioTarget !== "locked") return null;
    const selection = normalizeSceneLipSyncSource(shot.lip_sync_source);
    return {...selection, sceneIndex:segment.sceneIndex,
        seconds:selection.start_seconds + (Number(segment.sourceInFrame) || 0) / 24
            + seconds - segment.startSeconds};
}
