#!/usr/bin/env node

import assert from "node:assert/strict";
import fs from "node:fs";
import {
    DEFAULT_CLEANUP_DELAY_MS,
    EXECUTION_MODES,
    HANDOFF_API_BASE,
    LEGACY_MODE,
    REQUEUE_MODE,
    checkpointPredecessorReady,
    cleanupDelayMs,
    executionModeFromValue,
    isQueueSafe,
    isRequeueMode,
    pendingNextSceneHandoffs,
    predecessorScene,
    resumeHint,
} from "../web/h3_chain_top_level_requeue_core.mjs";

// --- mode contract ---------------------------------------------------------

assert.deepEqual(EXECUTION_MODES, [LEGACY_MODE, REQUEUE_MODE]);
assert.equal(EXECUTION_MODES[0], "recursive_legacy");
assert.equal(EXECUTION_MODES[1], "top_level_requeue");
assert.equal(executionModeFromValue("top_level_requeue"), REQUEUE_MODE);
assert.equal(executionModeFromValue(" top_level_requeue "), REQUEUE_MODE);
assert.equal(executionModeFromValue("recursive_legacy"), LEGACY_MODE);
assert.equal(executionModeFromValue(""), LEGACY_MODE);
assert.equal(executionModeFromValue(null), LEGACY_MODE);
assert.equal(executionModeFromValue("banana"), LEGACY_MODE);
assert.equal(isRequeueMode("top_level_requeue"), true);
assert.equal(isRequeueMode("recursive_legacy"), false);
assert.equal(HANDOFF_API_BASE, "/minimax_h3_context_loop");
assert.equal(DEFAULT_CLEANUP_DELAY_MS, 10750);

// --- cleanup interval ------------------------------------------------------

assert.equal(cleanupDelayMs(10750), 10750);
assert.equal(cleanupDelayMs(0), 0);
assert.equal(cleanupDelayMs("5000"), 5000);
assert.equal(cleanupDelayMs("-5"), DEFAULT_CLEANUP_DELAY_MS);
assert.equal(cleanupDelayMs("banana"), DEFAULT_CLEANUP_DELAY_MS);
assert.equal(cleanupDelayMs(Number.NaN), DEFAULT_CLEANUP_DELAY_MS);
assert.equal(cleanupDelayMs(null), DEFAULT_CLEANUP_DELAY_MS);

// --- handoff list interpretation -------------------------------------------

const handoffList = {
    run_name: "run",
    handoffs: [
        {
            handoff_id: "next_scene_0002",
            action: "next_scene",
            status: "pending",
            start_clip: 2,
            resume: {
                start_clip: 2,
                scene_range: "",
                end_clip: 3,
                total_scenes: 3,
            },
        },
        {
            handoff_id: "next_scene_0001",
            action: "next_scene",
            status: "consumed",
            start_clip: 1,
            resume: {
                start_clip: 1,
                scene_range: "",
                end_clip: 3,
                total_scenes: 3,
            },
        },
        {
            handoff_id: "next_candidate_0001",
            action: "next_candidate",
            status: "pending",
            start_clip: 1,
        },
        {
            handoff_id: "",
            action: "next_scene",
            status: "pending",
        },
    ],
};
assert.deepEqual(
    pendingNextSceneHandoffs(handoffList).map((item) => item.handoff_id),
    ["next_scene_0002"],
);
assert.deepEqual(pendingNextSceneHandoffs({handoffs: "nope"}), []);
assert.deepEqual(pendingNextSceneHandoffs(null), []);
assert.deepEqual(pendingNextSceneHandoffs({}), []);

assert.deepEqual(resumeHint(handoffList.handoffs[0]), {
    startClip: 2,
    sceneRange: "",
    endClip: 3,
    totalScenes: 3,
});
assert.deepEqual(resumeHint({
    resume: {start_clip: 2, scene_range: "2:4", end_clip: 4, total_scenes: 6},
}), {startClip: 2, sceneRange: "2:4", endClip: 4, totalScenes: 6});
assert.equal(resumeHint({}), null);
assert.equal(resumeHint({resume: {start_clip: 0}}), null);
assert.equal(resumeHint({resume: {start_clip: "x"}}), null);

// --- predecessor checkpoint validation --------------------------------------

assert.equal(predecessorScene({startClip: 2}), 1);
assert.equal(predecessorScene({startClip: 1}), 0);
assert.equal(predecessorScene(null), 0);
assert.equal(checkpointPredecessorReady([{ready: true, scene: 1}], 0), true);
assert.equal(checkpointPredecessorReady([{ready: true, scene: 1}], 1), true);
assert.equal(
    checkpointPredecessorReady([{ready: false, scene: 1}], 1),
    false,
);
assert.equal(checkpointPredecessorReady([], 2), false);
assert.equal(checkpointPredecessorReady(null, 2), false);
assert.equal(checkpointPredecessorReady("nope", 2), false);

// --- queue-safe interpretation ----------------------------------------------

assert.equal(
    isQueueSafe({queue_running: [], queue_pending: []}),
    true,
);
assert.equal(
    isQueueSafe({queue_running: [{request_id: "1"}], queue_pending: []}),
    false,
);
assert.equal(
    isQueueSafe({queue_running: [], queue_pending: [{request_id: "2"}]}),
    false,
);
assert.equal(isQueueSafe({}), false);
assert.equal(isQueueSafe(null), false);

// --- wrapper source contract -------------------------------------------------

const source = fs.readFileSync(
    new URL("../web/h3_chain_top_level_requeue.js", import.meta.url),
    "utf8",
);

// Terminal-success-only trigger; never sampler completion.
assert.match(source, /api\.addEventListener\("execution_success"/);
assert.doesNotMatch(source, /MiniMaxH3H3Sample/);
// Failure paths keep the handoff durable and never auto-retry.
assert.match(source, /api\.addEventListener\("execution_interrupted"/);
assert.match(source, /api\.addEventListener\("execution_error"/);
assert.match(source, /The handoff stays queued/);
// Safe queue + configurable cleanup interval measured from terminal success.
assert.match(source, /\/api\/queue/);
assert.match(source, /waitForSafeQueue\(\)/);
assert.match(source, /cleanupDelayMs\(/);
assert.match(source, /MiniMaxH3ContextLoop\.topLevelRequeueCleanupDelay/);
assert.match(source, /defaultValue:\s*DEFAULT_CLEANUP_DELAY_MS/);
// Opt-in master switch.
assert.match(source, /MiniMaxH3ContextLoop\.topLevelRequeue\b/);
assert.match(source, /defaultValue:\s*false/);
assert.match(source, /getSettingValue\?\.\(SETTING_ID\) === true/);
// Workflow/run identity validation before requeue.
assert.match(source, /function requireVisibleWorkflow\(record\)/);
assert.match(source, /widgetByName\(planNode, "run_name"\)/);
assert.match(source, /workflowIdentity !== record\.workflowIdentity/);
// Predecessor checkpoint validation (spec: queue-safe requirement).
assert.match(source, /checkpoints\?run_name=/);
assert.match(source, /Checkpoint \$\{predecessor\} is not ready/);
// Claim exactly once; duplicate terminal events queue nothing.
assert.match(source, /handoffs\/claim/);
assert.match(source, /claimResponse\.status === 409/);
assert.match(source, /The handoff was already claimed; nothing was queued/);
// Existing Loop Start widgets only; the Plan JSON is never rewritten here.
assert.match(source, /widgetByName\(startNode, "start_clip"\)/);
assert.match(source, /widgetByName\(startNode, "scene_range"\)/);
assert.match(source, /startWidget\.callback\?\.\(resume\.startClip\)/);
assert.doesNotMatch(source, /plan_json/);
// Same workflow queued as a NEW top-level prompt.
assert.match(source, /await app\.queuePrompt\(0, 1\)/);
// queued/consumed lifecycle via the durable routes.
assert.match(source, /handoffs\/transition/);
assert.match(source, /"consumed"/);
assert.match(source, /"queued"/);
// Failures release the claim for manual recovery.
assert.match(source, /handoffs\/release/);
assert.match(source, /queue the workflow manually/);
// Browser/server restart: show pending state, never auto-run.
assert.match(source, /graphChanged/);
assert.match(source, /Nothing auto-runs on startup/);
assert.match(source, /Pending H3 handoff for run/);
// Offline constraint: icons/decorations are inline SVG; nothing external is
// loaded (the w3.org string is the SVG XML namespace, not a fetched asset).
assert.doesNotMatch(source, /https?:\/\/(?!www\.w3\.org\/2000\/svg)/);
assert.doesNotMatch(source, /\bsrc\s*=\s*["']http/);
assert.doesNotMatch(source, /url\(\s*["']?https?:/);
assert.doesNotMatch(source, /new Image\(\)/);
assert.match(source, /createElementNS\(namespace, "svg"\)/);

// The core module must stay import-free of ComfyUI.
const coreSource = fs.readFileSync(
    new URL("../web/h3_chain_top_level_requeue_core.mjs", import.meta.url),
    "utf8",
);
assert.doesNotMatch(coreSource, /\/scripts\/(app|api)\.js/);
assert.doesNotMatch(coreSource, /https?:\/\//);

console.log("H3 top-level requeue helpers: ok");
