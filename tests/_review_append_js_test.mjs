import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import {appendedReviewPrompts, appendedReviewScene, continueAppendedReview} from "../web/h3_chain_review_append.mjs";
import {parsePlanJson} from "../web/h3_chain_plan_core.mjs";
import {submitWithPromptIdentity} from "../web/h3_chain_top_level_requeue_coordinator.mjs";

const review = {
    token: "review", prompt_id: "running", run_name: "project", _branch_id: "branch",
    clip_index: 2, clip_count: 2, end_clip: 2, scene_range_explicit: false,
    plan_scene_ids: ["one", "two"],
};
const plan = {_branch_id: "branch", shots: [{id: "one"}, {id: "two"}, {id: "three"}]};
const next = (r = review, p = plan, range = "") => appendedReviewScene(r, p, range);
assert.equal(next(), 3, "resume the first appended scene, never the saved second scene");
assert.equal(next({...review, clip_index: 1}), null);
assert.equal(next({...review, end_clip: 1}), null);
assert.equal(next({...review, scene_range_explicit: true}), null);
assert.equal(next(review, plan, "1:2"), null);
assert.equal(next(review, {...plan, shots: plan.shots.slice(0, 2)}), null);
assert.equal(next(review, {...plan, _branch_id: "other"}), null);
assert.equal(next(review, {...plan, shots: [plan.shots[1], plan.shots[0], plan.shots[2]]}), null);
assert.equal(next({...review, plan_scene_ids: undefined}), null);
assert.equal(next({...review, plan_scene_ids: ["clip_0001", "clip_0002"]},
    {...plan, shots: [{}, {}, {}]}), 3);
assert.equal(next({...review, plan_scene_ids: ["one_scene", "two"]},
    {...plan, shots: [{id: "one scene"}, {id: "two"}, {}]}), 3);

async function scenario({histories = [null, {status: {completed: true, status_str: "success"}}],
    present = true, cancel = false, submission = {kind: "accepted", promptId: "new"}} = {}) {
    const calls = [];
    let checks = 0;
    const outcome = await continueAppendedReview({
        promptId: "running",
        current() { if (cancel && ++checks > 1) throw new Error("Workflow changed"); },
        async history(id) { assert.equal(id, "running"); calls.push("history"); return histories.shift() ?? null; },
        async queued(id) { assert.equal(id, "running"); return present; },
        async sleep() { calls.push("wait"); },
        prepare() { calls.push("prepare 3"); },
        async submit() { calls.push("submit"); return submission; },
    }).catch(error => ({error: error.message}));
    return {calls, outcome};
}
let result = await scenario();
assert.deepEqual(result.calls, ["history", "wait", "history", "prepare 3", "submit"]);
assert.equal(result.outcome.promptId, "new");
for (const status of [
    {completed: false, status_str: "error"},
    {completed: true, status_str: "error"},
    {completed: true, status_str: "success", messages: [["execution_interrupted", {}]]},
]) {
    result = await scenario({histories: [{status}]});
    assert.match(result.outcome.error, /did not finish successfully/);
    assert.ok(!result.calls.includes("submit"));
}
result = await scenario({present: false, histories: []});
assert.match(result.outcome.error, /no longer available/);
assert.ok(!result.calls.includes("prepare 3"));
result = await scenario({cancel: true});
assert.match(result.outcome.error, /Workflow changed/);
assert.ok(!result.calls.includes("submit"));
result = await scenario({submission: {kind: "uncertain"}});
assert.equal(result.outcome.kind, "uncertain");
assert.equal(result.calls.filter(c => c === "submit").length, 1, "never retry uncertain delivery");

// Exercise the actual browser adapter with a synthetic graph and queue. No
// ComfyUI server, models, user files, or real prompt submissions are involved.
const browserSource = fs.readFileSync(new URL("../web/h3_chain_review_final.js", import.meta.url), "utf8")
    .replace(/^import\s[\s\S]*?from\s["'][^"']+["'];\n/gm, "");
function browser({type = "MiniMaxH3ChainPlan", duringHistory = () => {}} = {}) {
    const widget = (name, value) => ({name, value});
    const source = {id: 1, type, widgets: [widget("plan_json", JSON.stringify(plan)), widget("run_name", "project")]};
    const start = {id: 2, type: "MiniMaxH3ChainLoopStart", inputs: [{link: 1}],
        widgets: [widget("start_clip", 1), widget("scene_range", "")]};
    const gate = {id: 3, type: "MiniMaxH3ChainReview", inputs: [{link: 2}]};
    const graph = {_nodes: [source, start, gate], links: {1: {origin_id: 1}, 2: {origin_id: 2}},
        getNodeById(id) { return this._nodes.find(n => n.id === id); }};
    for (const node of graph._nodes) node.graph = graph;
    const submitted = [], messages = [];
    const app = {graph, extensionManager: {workflow: {activeWorkflow: {path: "test"}}}, registerExtension() {},
        async graphToPrompt() { return {start: start.widgets[0].value, plan: source.widgets[0].value}; }};
    const api = {addEventListener() {}, async fetchApi(path) {
        assert.equal(path, "/history/running");
        duringHistory({app, source, start});
        return {ok: true, async json() { return {running: {status: {completed: true, status_str: "success"}}}; }};
    }, async queuePrompt(_position, prompt) { submitted.push(prompt); return {prompt_id: "appended"}; }};
    const context = vm.createContext({app, api, console, parsePlanJson, appendedReviewPrompts, appendedReviewScene, continueAppendedReview,
        submitWithPromptIdentity, window: {addEventListener() {}, setTimeout}, document: {addEventListener() {}}});
    vm.runInContext(browserSource + "\nglobalThis.appendIntent = appendedReviewIntent;", context);
    return {source, start, gate, app, submitted, messages,
        intent: () => context.appendIntent(gate, review), report: msg => messages.push(msg)};
}
for (const type of ["MiniMaxH3ChainPlan", "MiniMaxH3ChainPlanModern", "MiniMaxH3ChainPlanStudio"]) {
    const b = browser({type});
    const intent = b.intent();
    assert.equal(intent.nextIndex, 3);
    intent.arm();
    assert.ok(appendedReviewPrompts.has("running"));
    await intent.run(b.report);
    assert.ok(!appendedReviewPrompts.has("running"));
    await intent.run(b.report);
    assert.equal(b.submitted.length, 1, "one follow-up per successful review token");
    assert.equal(b.submitted[0].start, 3);
    assert.equal(JSON.parse(b.submitted[0].plan).shots.length, 3);
}
for (const change of [
    ({app}) => { app.extensionManager.workflow.activeWorkflow = {path: "other"}; },
    ({source}) => { source.widgets[0].value = JSON.stringify({...plan, _branch_id: "other"}); },
    ({start}) => { start.widgets[0].value = 2; },
    ({start}) => { start.widgets[1].value = "1:2"; },
]) {
    const b = browser({duringHistory: change});
    await b.intent().run(b.report);
    assert.equal(b.submitted.length, 0);
    assert.match(b.messages.at(-1), /changed/);
}
const disconnected = browser();
disconnected.start.inputs = [];
assert.equal(disconnected.intent(), null, "never find an unrelated Plan elsewhere on the canvas");
const linked = browser();
linked.source.inputs = [{name: "plan_json_input", link: 10}];
assert.equal(linked.intent(), null, "do not queue a stale widget when a linked STRING supplies the Plan");
const linkedStart = browser();
linkedStart.start.inputs.push({name: "start_clip", link: 11});
assert.equal(linkedStart.intent(), null, "a linked start index cannot be changed through its widget");
const studio = browser({type: "MiniMaxH3ChainPlanStudio"});
studio.source.widgets[0].value = JSON.stringify({...plan, _branch_id: undefined});
studio.source.widgets.push({name: "working_branch_id", value: "branch"});
assert.ok(studio.intent(), "standalone Studio's branch widget is authoritative");
studio.source.widgets.at(-1).value = "other";
assert.equal(studio.intent(), null);
console.log("Review append continuation tests passed");
