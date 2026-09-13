import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import * as sync from "../web/h3_prompt_companion_sync.mjs";
import {parsePlanJson, planToJson, promptTextToLines, promptValueToText} from "../web/h3_chain_plan_core.mjs";

function section(source, start, end) {
    const offset = source.indexOf(start);
    assert.ok(offset >= 0, start);
    const limit = source.indexOf(end, offset);
    assert.ok(limit > offset, end);
    return source.slice(offset, limit);
}

function fixture(types) {
    const initial = {shots:[
        {id:"one", prompt:["old H3"], basic_prompt:"old basic", seed:"11", length:73},
        {id:"two", prompt:["second H3"], basic_prompt:"second basic", seed:"22", length:73},
    ]};
    const planWidget = {name:"plan_json", value:planToJson(initial), callback(){}};
    const graph = {_nodes:[]};
    const planNode = {widgets:[planWidget], graph};
    graph._nodes.push(planNode);
    const editors = types.map(type => {
        const rich = type === "rich";
        const name = rich ? "h3_chain_rich_scene_prompt_editor.js" : "h3_chain_scene_prompt_editor.js";
        const source = fs.readFileSync(new URL("../web/" + name, import.meta.url), "utf8");
        const node = {properties:{}, graph};
        graph._nodes.push(node);
        const basic = {value:"old basic"};
        const state = {plan:parsePlanJson(planWidget.value), planWidget, planNode, active:0,
            lastValue:planWidget.value, planSyncTimer:null, planSyncPending:null};
        const context = vm.createContext({
            ...sync, state, node, console, parsePlanJson, planToJson, promptTextToLines, promptValueToText,
            ACTIVE_PROPERTY:"active", ACTIVE_SCENE_PROPERTY:"active",
            PROMPT_SYNC_DELAY_MS:140,
            workingBranchId:value=>value || "main", planBranchId:()=>"main",
            window:{clearTimeout(){}, setTimeout(){ return 1; }},
            root:{querySelector(selector){ return selector.includes("basic-prompt") ? basic : null; }},
            dirty(){}, promptUndoForScene(){ return {}; },
            loadPlan(){ throw new Error("Prompt-only updates must not rebuild the editor"); },
        });
        let code = section(source, "    function rebaseActivePromptOntoLivePlan()", "    function planRunName()");
        // In the simple editor planRunName follows the write helpers, while
        // the rich editor keeps it just after the rebase helper.
        if (rich) {
            code += section(source, "    function flushPlanEffects()", "    function schedulePromptAnalysis()");
            code += section(source, "    function writePlan(", "    function historySceneKey(");
        } else {
            // Drop unrelated analysis helpers enclosed between write functions.
            code = section(source, "    function rebaseActivePromptOntoLivePlan()", "    function schedulePromptAnalysis()");
            code += section(source, "    function commitPlan(", "    function planRunName()");
        }
        code += section(source, "    node._h3PromptCompanionSetScenePrompt =", "    node._h3PromptCompanionSetBasicPrompt =");
        const basicStart = source.indexOf("    node._h3PromptCompanionSetBasicPrompt =");
        code += source.slice(basicStart, source.indexOf("\n    };", basicStart) + 7);
        vm.runInContext(code, context);
        return {context, basic, state, node, edit(field, value, defer=false) {
            state.plan.shots[state.active][field] = value;
            sync.markShotFieldEdited(state.plan.shots[state.active], field);
            context.writePlan(rich ? "saved" : null, {deferEffects:defer});
        }, flush(){ context.flushPlanEffects(); }};
    });
    return {editors, planWidget, read:()=>parsePlanJson(planWidget.value)};
}

for (const types of [["rich","rich"], ["simple","simple"], ["rich","simple"], ["simple","rich"]]) {
    const f = fixture(types), [a,b] = f.editors;
    const shotIdentity = b.state.plan.shots[0];
    a.edit("basic_prompt", "new basic from A");
    assert.equal(b.basic.value, "new basic from A");
    b.edit("prompt", ["new H3 from B"]);
    assert.equal(f.read().shots[0].basic_prompt, "new basic from A");
    assert.equal(f.read().shots[0].prompt[0], "new H3 from B");
    assert.equal(b.state.plan.shots[0], shotIdentity, "keep live DOM input closures valid");
    a.edit("prompt", ["second H3 edit"]);
    b.edit("basic_prompt", "second basic edit");
    assert.equal(f.read().shots[0].prompt[0], "second H3 edit");
    assert.equal(a.basic.value, "second basic edit");
    b.edit("basic_prompt", "");
    a.edit("prompt", ["after clear"]);
    assert.equal(f.read().shots[0].basic_prompt, "", "explicit clear is synchronized");

    // A deferred notification must not resurrect the source's older field.
    a.edit("basic_prompt", "deferred basic", true);
    b.edit("prompt", ["during debounce"]);
    a.flush();
    assert.equal(f.read().shots[0].basic_prompt, "deferred basic");
    assert.equal(f.read().shots[0].prompt[0], "during debounce");

    // Source switches scene; receiver's active prompt notification must still
    // reconcile the complete basic-draft snapshot before marking it current.
    const live = f.read();
    live.shots[1].basic_prompt = "new off-screen basic";
    f.planWidget.value = planToJson(live);
    sync.publishCompanionPrompt(a.node, a.state.planNode, 0, live.shots[0].prompt.join("\n"));
    b.edit("prompt", ["keep off-screen draft"]);
    assert.equal(f.read().shots[1].basic_prompt, "new off-screen basic");
    assert.equal(f.read().shots[0].seed, "11");
}
console.log("Basic prompt editor sync: real simple/rich write, broadcast and receiver paths preserve both fields, clears, debounce and inactive scenes");
