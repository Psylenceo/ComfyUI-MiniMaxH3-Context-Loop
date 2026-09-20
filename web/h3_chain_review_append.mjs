// Prevent the regular top-level coordinator from resetting Loop Start while
// an approved extension is waiting for this exact prompt to finish.
export const appendedReviewPrompts = new Set();

// Only extend an open-ended run at its old final scene. The next top-level
// prompt uses normal checkpoint resume/preflight, never rerenders that scene.
export function appendedReviewScene(review, plan, sceneRange = "") {
    const count = Number(review?.clip_count);
    if (!Number.isInteger(count) || count < 1 || Number(review.clip_index) !== count
            || Number(review.end_clip) !== count || review.scene_range_explicit !== false
            || String(sceneRange ?? "").trim()
            || (plan?._branch_id ?? "main") !== (review._branch_id ?? "main")
            || !Array.isArray(plan?.shots) || plan.shots.length <= count
            || !Array.isArray(review.plan_scene_ids) || review.plan_scene_ids.length !== count) return null;
    // Match the backend's _safe_name, including IDs for plain-string scenes.
    for (let i = 0; i < count; i++) {
        const fallback = `clip_${String(i + 1).padStart(4, "0")}`;
        const id = (String(plan.shots[i]?.id || "").trim()
            .replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^[._-]+|[._-]+$/g, "") || fallback).slice(0, 96);
        if (id !== review.plan_scene_ids[i]) return null;
    }
    return count + 1;
}

export async function continueAppendedReview({promptId, current, history, queued, sleep, prepare, submit}) {
    if (!promptId) throw new Error("The reviewed prompt could not be identified.");
    let missing = 0;
    for (;;) {
        current();
        const record = await history(promptId);
        current();
        if (record) {
            const status = record.status;
            const failed = status?.messages?.some(([kind]) =>
                kind === "execution_error" || kind === "execution_interrupted");
            if (!status?.completed || status.status_str !== "success" || failed) {
                throw new Error("The reviewed prompt did not finish successfully; no appended scene was queued.");
            }
            break;
        }
        // History is authoritative even when approval happens in another tab
        // that doesn't receive the original client's terminal websocket event.
        if (await queued(promptId)) missing = 0;
        else if (++missing >= 2) throw new Error("The reviewed prompt is no longer available; resume manually from its saved checkpoint.");
        await sleep();
    }
    current();
    prepare();
    current();
    return await submit(); // Never automatically retry an uncertain submission.
}
