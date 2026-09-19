#!/usr/bin/env node
// Regression for: editing one prompt field through a second, companion-synced
// editor could silently discard a newer, already-saved edit to the OTHER
// field, because the companion receiver patched only the pushed field and
// then marked the live Plan text "already synced" (state.lastValue) without
// actually reconciling the field it never touched. The next save from that
// editor would then skip rebasing (since it now believed itself in sync) and
// republish its own stale copy of the untouched field over whatever changed
// elsewhere.
//
// This test drives the REAL, unmodified production functions extracted
// verbatim from both editor files (dirty, flushPlanEffects, writePlan (and
// commitPlan for the simple editor), historySceneKey, promptUndoForScene,
// planRunName, and both _h3PromptCompanionSetScenePrompt/
// _h3PromptCompanionSetBasicPrompt receivers) - not a reimplementation, and
// not a direct call to rebaseScenePrompt alone - wired to the real
// publishCompanionPrompt/rebaseScenePrompt/planHasNonPromptChanges/
// markShotFieldEdited exports, through a real two-node graph, so the full
// A-save -> B-receive -> B-save sequence exercises the same code path a
// live workflow would.
import assert from "node:assert/strict";
import fs from "node:fs";
import {
    parsePlanJson, planToJson, promptValueToText,
} from "../web/h3_chain_plan_core.mjs";
import {
    rebaseScenePrompt, planHasNonPromptChanges, markShotFieldEdited,
    beginTrackingShotFields, commitShotFields, publishCompanionPrompt,
} from "../web/h3_prompt_companion_sync.mjs";
import {PromptUndoHistory} from "../web/h3_rich_prompt_editor_core.mjs";
import {workingBranchId} from "../web/h3_working_branches.mjs";

globalThis.window = globalThis.window || globalThis;
globalThis.app = globalThis.app || {graph: null};
globalThis.document = globalThis.document || {activeElement: null};

function extract(source, startMarker, endMarker, label) {
    const start = source.indexOf(startMarker);
    assert.notEqual(start, -1, `missing ${label} start marker`);
    const end = source.indexOf(endMarker, start);
    assert.notEqual(end, -1, `missing ${label} end marker`);
    return source.slice(start, end);
}

const RICH_MARKERS = [
    ["    function dirty() {", "    function persistView() {", "dirty"],
    ["    function flushPlanEffects() {", "    function schedulePlanEffects(pending) {", "flushPlanEffects"],
    ["    function writePlan(", "    function historySceneKey(runName, shotId) {", "writePlan"],
    ["    function historySceneKey(runName, shotId) {", "    function promptUndoForScene(shotId, text, {external = false} = {}) {", "historySceneKey"],
    ["    function promptUndoForScene(shotId, text, {external = false} = {}) {", "    function recordPromptReplacement(sceneIndex, shot, text) {", "promptUndoForScene"],
    ["    function planRunName() {", "    function flushPlanEffects() {", "planRunName"],
    ["    node._h3PromptCompanionSetScenePrompt = (planNode, index, text) => {", "    node._h3PromptCompanionSetBasicPrompt = (planNode, index, text) => {", "receiver:SetScenePrompt"],
    ["    node._h3PromptCompanionSetBasicPrompt = (planNode, index, text) => {", "    node._h3RichPromptRefresh = () => loadPlan(true);", "receiver:SetBasicPrompt"],
];

const SIMPLE_MARKERS = [
    ["    function dirty() {", "    function persistView() {", "dirty"],
    ["    function flushPlanEffects() {", "    function schedulePlanEffects(pending) {", "flushPlanEffects"],
    ["    function commitPlan(", "    function writePlan(status, options = {}) {", "commitPlan"],
    ["    function writePlan(status, options = {}) {", "    function planRunName() {", "writePlan"],
    ["    function historySceneKey(runName, shotId) {", "    function promptUndoForScene(shotId, text, {external = false} = {}) {", "historySceneKey"],
    ["    function promptUndoForScene(shotId, text, {external = false} = {}) {", "    async function historyRequest(query = {}, body = null) {", "promptUndoForScene"],
    ["    function planRunName() {", "    function historySceneKey(runName, shotId) {", "planRunName"],
    ["    node._h3PromptCompanionSetScenePrompt = (planNode, index, text) => {", "    node._h3PromptCompanionSetBasicPrompt = (planNode, index, text) => {", "receiver:SetScenePrompt"],
    ["    node._h3PromptCompanionSetBasicPrompt = (planNode, index, text) => {", "    node._h3ScenePromptEditorRefresh = () => loadPlan(true);", "receiver:SetBasicPrompt"],
];

function buildEditorFixture(fileName, {planWidget, planNode, graph, initialPlanJson}) {
    const source = fs.readFileSync(new URL(`../${fileName}`, import.meta.url), "utf8");
    const markers = fileName.includes("rich") ? RICH_MARKERS : SIMPLE_MARKERS;
    const extracted = markers.map(([startMarker, endMarker, label]) =>
        extract(source,
            startMarker.replace("historySceneKey(runName, shotId)", "historySceneKey(runName, shotId, branchId = planBranchId())"),
            endMarker.replace("historySceneKey(runName, shotId)", "historySceneKey(runName, shotId, branchId = planBranchId())"),
            `${fileName}:${label}`)).join("\n\n");
    const body = `
        ${extracted}
        function loadPlan(force) { throw new Error("loadPlan(" + force + ") should not run in this test"); }
        return {
            writePlan: (a, b) => writePlan(a, b),
            node,
        };
    `;
    const factory = new Function(
        "parsePlanJson", "planToJson", "promptValueToText",
        "rebaseScenePrompt", "planHasNonPromptChanges", "markShotFieldEdited",
        "beginTrackingShotFields", "commitShotFields", "publishCompanionPrompt",
        "PromptUndoHistory", "workingBranchId",
        "node", "state", "root",
        body,
    );
    const node = {properties: {}, graph};
    const state = {
        plan: parsePlanJson(initialPlanJson),
        planWidget, planNode, active: 0, lastValue: initialPlanJson,
        undoByScene: new Map(), planSyncTimer: null, planSyncPending: null,
        disposed: false, editor: null, status: null,
    };
    const root = {querySelector: () => null};
    const handle = factory(
        parsePlanJson, planToJson, promptValueToText,
        rebaseScenePrompt, planHasNonPromptChanges, markShotFieldEdited,
        beginTrackingShotFields, commitShotFields, publishCompanionPrompt,
        PromptUndoHistory, workingBranchId,
        node, state, root,
    );
    graph._nodes.push(node);
    return {node, state, writePlan: handle.writePlan};
}

function runScenario(fileA, fileB) {
    const initial = {shots: [
        {id: "one", prompt: "Original H3 text.", basic_prompt: "old basic"},
    ]};
    const initialPlanJson = planToJson(initial);
    const planWidget = {value: initialPlanJson};
    const planNode = {};
    const graph = {_nodes: []};

    const editorA = buildEditorFixture(fileA, {planWidget, planNode, graph, initialPlanJson});
    const editorB = buildEditorFixture(fileB, {planWidget, planNode, graph, initialPlanJson});

    // Step 1: A saves a new basic draft. Its companion notification (the
    // real publishCompanionPrompt call inside flushPlanEffects) reaches B.
    editorA.state.plan.shots[0].basic_prompt = "new basic from A";
    markShotFieldEdited(editorA.state.plan.shots[0], "basic_prompt");
    editorA.writePlan("Basic prompt saved to Plan");

    assert.equal(
        editorB.state.plan.shots[0].basic_prompt, "new basic from A",
        `${fileB}: companion push did not adopt the new basic draft`);

    // Step 2: B edits ONLY the H3 prompt and saves.
    editorB.state.plan.shots[0].prompt = ["New H3 text from B."];
    markShotFieldEdited(editorB.state.plan.shots[0], "prompt");
    editorB.writePlan("H3 edit saved to Plan");

    const finalPlan = parsePlanJson(planWidget.value);
    assert.equal(
        finalPlan.shots[0].prompt.join("\n"), "New H3 text from B.",
        `${fileA} -> ${fileB}: B's own H3 edit was lost`);
    assert.equal(
        finalPlan.shots[0].basic_prompt, "new basic from A",
        `${fileA} -> ${fileB}: B's save reverted A's newer basic draft - ` +
        "the companion receiver must synchronize both fields before " +
        "treating the live Plan as already in sync");
}

// Regression for: __h3EditedFields was only ever retired inside
// rebaseScenePrompt, which an ordinary successful write/flush never calls
// when the live Plan hasn't diverged since the writer's own last write.
// A's field-edit mark therefore outlived the save that committed it. When
// B later pushed a newer value for that SAME field, A's receiver correctly
// adopted it - but the moment A saved an edit to the OTHER field, its own
// still-"edited" (never retired) stale mark on the first field caused
// rebaseScenePrompt to keep A's old value instead of B's newer one,
// reverting B's already-saved change. commitShotFields (called right after
// writePlan/commitPlan serializes the shot into the Plan JSON) fixes this
// by retiring exactly the fields that were just committed.
function runRepeatedEditScenario(fileA, fileB, field) {
    const other = field === "prompt" ? "basic_prompt" : "prompt";
    const initial = {shots: [
        {id: "one", prompt: "Original H3 text.", basic_prompt: "old basic"},
    ]};
    const initialPlanJson = planToJson(initial);
    const planWidget = {value: initialPlanJson};
    const planNode = {};
    const graph = {_nodes: []};

    const editorA = buildEditorFixture(fileA, {planWidget, planNode, graph, initialPlanJson});
    const editorB = buildEditorFixture(fileB, {planWidget, planNode, graph, initialPlanJson});

    const valueOf = (v) => Array.isArray(v) ? v.join("\n") : v;
    const firstByA = field === "prompt" ? ["First by A."] : "First by A.";
    const newestByB = field === "prompt" ? ["Newest by B."] : "Newest by B.";
    const otherByA = other === "prompt" ? ["Other field touched by A."] : "Other field touched by A.";

    // Step 1: A saves the field first.
    editorA.state.plan.shots[0][field] = firstByA;
    markShotFieldEdited(editorA.state.plan.shots[0], field);
    editorA.writePlan(`${field} first-saved by A`);
    assert.equal(
        valueOf(editorB.state.plan.shots[0][field]), valueOf(firstByA),
        `${fileB}: companion push did not adopt A's first save of ${field}`);

    // Step 2: B updates that SAME field to a newer value and saves.
    editorB.state.plan.shots[0][field] = newestByB;
    markShotFieldEdited(editorB.state.plan.shots[0], field);
    editorB.writePlan(`${field} updated by B`);

    // Step 3: back in A, edit only the OTHER field and save.
    editorA.state.plan.shots[0][other] = otherByA;
    markShotFieldEdited(editorA.state.plan.shots[0], other);
    editorA.writePlan(`${other} edited by A`);

    const finalPlan = parsePlanJson(planWidget.value);
    assert.equal(
        valueOf(finalPlan.shots[0][field]), valueOf(newestByB),
        `${fileA} -> ${fileB}: A's edit of ${other} reverted B's already-saved ` +
        `update to ${field} - a committed field mark on A must not outlive ` +
        "the write that saved it");
    assert.equal(
        valueOf(finalPlan.shots[0][other]), valueOf(otherByA),
        `${fileA} -> ${fileB}: A's own newer edit of ${other} was lost`);
}

const RICH = "web/h3_chain_rich_scene_prompt_editor.js";
const SIMPLE = "web/h3_chain_scene_prompt_editor.js";

for (const [fileA, fileB] of [
    [RICH, RICH], [SIMPLE, SIMPLE], [RICH, SIMPLE], [SIMPLE, RICH],
]) {
    runScenario(fileA, fileB);
    runRepeatedEditScenario(fileA, fileB, "prompt");
    runRepeatedEditScenario(fileA, fileB, "basic_prompt");
}

console.log(
    "H3 prompt field sync: A's basic draft survives B's H3-only save via " +
    "the real companion receiver, in all four rich/simple combinations",
);
