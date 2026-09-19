#!/usr/bin/env node

import assert from "node:assert/strict";
import fs from "node:fs";
import {
    activeSceneIndexAfterRefresh,
    adjacentPlanCompanions,
    beginTrackingShotFields,
    commitShotFields,
    connectedPlanStudios,
    connectedPromptEditors,
    markShotFieldEdited,
    planHasNonPromptChanges,
    publishCompanionPrompt,
    publishCompanionScene,
    publishPlanCompanionScene,
    rebaseScenePrompt,
} from "../web/h3_prompt_companion_sync.mjs";

assert.equal(activeSceneIndexAfterRefresh(
    {shots:[{id:"one"}, {id:"two"}, {id:"three"}]},
    {shots:[{id:"three"}, {id:"one"}, {id:"two"}]},
    1,
), 2);
assert.equal(activeSceneIndexAfterRefresh(
    {shots:[{id:"one"}, {id:"removed"}, {id:"three"}]},
    {shots:[{id:"one"}, {id:"three"}]},
    1,
), 1);
assert.equal(activeSceneIndexAfterRefresh(null, {shots:[{id:"one"}]}, 8), 0);
assert.equal(activeSceneIndexAfterRefresh(null, {shots:[]}, 8), 0);

const plan = {id:1, type:"MiniMaxH3ChainPlan"};
const studio = {id:2, type:"MiniMaxH3ChainPlanStudio", inputs:[{link:10}], outputs:[{links:[11]}]};
const editor = {id:3, type:"MiniMaxH3ChainRichScenePromptEditor", inputs:[{link:11}], outputs:[]};
const nodes = new Map([[1,plan],[2,studio],[3,editor]]);
const links = {
    10:{origin_id:1,target_id:2},
    11:{origin_id:2,target_id:3},
};
const graph = {links, _nodes:[plan, studio, editor], getNodeById:(id) => nodes.get(id)};
for (const node of nodes.values()) node.graph = graph;

assert.deepEqual(adjacentPlanCompanions(studio), [plan, editor]);
assert.deepEqual(connectedPromptEditors(studio), [editor]);
assert.deepEqual(connectedPlanStudios(editor), [studio]);

let received = null;
editor._h3PromptCompanionSetActiveScene = (receivedPlan, index, source) => {
    received = {receivedPlan,index,source};
};
assert.equal(publishCompanionScene(studio, plan, 2.9), 1);
assert.deepEqual(received, {receivedPlan:plan,index:2,source:studio});

editor._h3PromptCompanionSetActiveScene = () => false;
assert.equal(publishCompanionScene(studio, plan, 4), 0);

const review = {id:4, type:"MiniMaxH3ChainReview", graph};
graph._nodes.push(review);
editor._h3PromptCompanionSetActiveScene = (receivedPlan, index, source) => {
    received = {receivedPlan,index,source};
    return receivedPlan === plan;
};
assert.equal(publishPlanCompanionScene(review, plan, 1), 1);
assert.deepEqual(received, {receivedPlan:plan,index:1,source:review});

let promptReceived = null;
editor._h3PromptCompanionSetScenePrompt = (receivedPlan, index, prompt, source) => {
    promptReceived = {receivedPlan,index,prompt,source};
    return receivedPlan === plan;
};
assert.equal(publishCompanionPrompt(review, plan, 1, "Edited\r\nprompt."), 1);
assert.deepEqual(promptReceived, {
    receivedPlan:plan, index:1, prompt:"Edited\nprompt.", source:review,
});

const localPlan = {shared:"old", shots:[
    {id:"one", prompt:["old one"], seed:"1"},
    {id:"two", prompt:["edited two"], seed:"2"},
]};
const editedShot = localPlan.shots[1];
const livePlan = {shared:"new", shots:[
    {id:"two", prompt:["stale two"], seed:"22", steps:20},
    {id:"one", prompt:["live one"], seed:"11"},
]};
assert.equal(rebaseScenePrompt(localPlan, livePlan, 1), 0);
assert.equal(localPlan.shared, "new");
assert.equal(localPlan.shots[0], editedShot, "active shot identity survives rebase");
assert.deepEqual(localPlan.shots[0], {id:"two", prompt:["edited two"], seed:"22", steps:20});
assert.deepEqual(localPlan.shots[1], {id:"one", prompt:["live one"], seed:"11"});
assert.equal(rebaseScenePrompt({shots:[{id:"gone",prompt:[]}]}, livePlan, 0), -1);

// An edit to one prompt field must not overwrite a newer, concurrent edit
// made to the *other* field through a different UI (e.g. Plan Studio).
{
    const h3OnlyEdit = {id:"one", prompt:"new H3 edit", basic_prompt:"old basic"};
    markShotFieldEdited(h3OnlyEdit, "prompt");
    const local = {shots:[h3OnlyEdit]};
    const live = {shots:[{id:"one", prompt:"old H3", basic_prompt:"new basic from Studio"}]};
    assert.equal(rebaseScenePrompt(local, live, 0), 0);
    assert.equal(live.shots[0].prompt, "new H3 edit");
    assert.equal(live.shots[0].basic_prompt, "new basic from Studio",
        "an H3-only edit must not discard a newer basic-draft edit");
}
{
    const basicOnlyEdit = {id:"one", prompt:"stale H3", basic_prompt:"new basic edit"};
    markShotFieldEdited(basicOnlyEdit, "basic_prompt");
    const local = {shots:[basicOnlyEdit]};
    const live = {shots:[{id:"one", prompt:"newer H3 from elsewhere", basic_prompt:"old basic"}]};
    assert.equal(rebaseScenePrompt(local, live, 0), 0);
    assert.equal(live.shots[0].prompt, "newer H3 from elsewhere",
        "a basic-only edit must not carry forward stale H3 text");
    assert.equal(live.shots[0].basic_prompt, "new basic edit");
}
{
    // Without any edited-field tracking (a caller that predates it), both
    // fields still get copied, preserving the pre-existing behavior.
    const untracked = {id:"one", prompt:"untracked H3", basic_prompt:"untracked basic"};
    const local = {shots:[untracked]};
    const live = {shots:[{id:"one", prompt:"old H3", basic_prompt:"old basic"}]};
    assert.equal(rebaseScenePrompt(local, live, 0), 0);
    assert.equal(live.shots[0].prompt, "untracked H3");
    assert.equal(live.shots[0].basic_prompt, "untracked basic");
}
{
    // beginTrackingShotFields is the opposite default a companion RECEIVER
    // needs: it has not edited anything itself, so an untouched field must
    // adopt the live value rather than keep clobbering it with whatever the
    // receiver's own stale local copy happens to hold.
    const receiverShot = {id:"one", prompt:"stale local H3", basic_prompt:"stale local basic"};
    beginTrackingShotFields(receiverShot);
    const local = {shots:[receiverShot]};
    const live = {shots:[{id:"one", prompt:"live H3", basic_prompt:"live basic"}]};
    assert.equal(rebaseScenePrompt(local, live, 0), 0);
    assert.equal(live.shots[0].prompt, "live H3",
        "an untracked-but-tracking-began shot must adopt the live prompt");
    assert.equal(live.shots[0].basic_prompt, "live basic",
        "an untracked-but-tracking-began shot must adopt the live basic_prompt");
}
{
    // beginTrackingShotFields must never clear a field already genuinely
    // touched (a receiver can have its own in-progress, unflushed edit).
    const midEditShot = {id:"one", prompt:"my in-progress H3 edit", basic_prompt:"stale local basic"};
    markShotFieldEdited(midEditShot, "prompt");
    beginTrackingShotFields(midEditShot);
    const local = {shots:[midEditShot]};
    const live = {shots:[{id:"one", prompt:"live H3", basic_prompt:"live basic"}]};
    assert.equal(rebaseScenePrompt(local, live, 0), 0);
    assert.equal(live.shots[0].prompt, "my in-progress H3 edit",
        "a field already marked edited must survive beginTrackingShotFields");
    assert.equal(live.shots[0].basic_prompt, "live basic",
        "a field never touched must still adopt the live value");
}
{
    // commitShotFields must retire tracking for fields already written into
    // the Plan JSON, so an ordinary write that never rebases (because the
    // live Plan had not diverged) does not leave a stale "edited" mark that
    // a later external push for that same field would wrongly treat as an
    // unsaved local edit still needing to win.
    const committedShot = {id:"one", prompt:"saved H3", basic_prompt:"saved basic"};
    markShotFieldEdited(committedShot, "prompt");
    markShotFieldEdited(committedShot, "basic_prompt");
    commitShotFields(committedShot);
    const local = {shots:[committedShot]};
    const live = {shots:[{id:"one", prompt:"newer H3 from elsewhere", basic_prompt:"newer basic from elsewhere"}]};
    assert.equal(rebaseScenePrompt(local, live, 0), 0);
    assert.equal(live.shots[0].prompt, "newer H3 from elsewhere",
        "a committed field must adopt a later external value instead of clobbering it");
    assert.equal(live.shots[0].basic_prompt, "newer basic from elsewhere",
        "commitShotFields must retire tracking for both fields it commits");
}
{
    // commitShotFields must be a no-op on a shot with no tracking Set yet.
    commitShotFields({id:"one", prompt:"untracked"});
    commitShotFields(null);
}

assert.equal(planHasNonPromptChanges(
    {shared:"same", shots:[{id:"one", prompt:["old"], seed:"11", length:90}]},
    {shared:"same", shots:[{id:"one", prompt:["new"], seed:"11", length:90}]},
), false, "a prompt-only broadcast can update in place without rerendering");
assert.equal(planHasNonPromptChanges(
    {shared:"same", shots:[{id:"one", prompt:["old"], seed:"11", length:90}]},
    {shared:"same", shots:[{id:"one", prompt:["new"], seed:"22", length:90}]},
), true, "candidate seed acceptance must reload the complete Plan");
assert.equal(planHasNonPromptChanges(
    {shared:"same", shots:[{id:"one", prompt:["old"], seed:"11", length:90}]},
    {shared:"same", shots:[{id:"one", prompt:["new"], seed:"11", length:141}]},
), true, "candidate length acceptance must reload the complete Plan");
assert.equal(planHasNonPromptChanges(
    {shared:"old", shots:[{id:"one", prompt:["same"]}]},
    {shared:"new", shots:[{id:"one", prompt:["same"]}]},
), true, "shared and plan-level fields remain synchronized");

for (const relative of [
    "../web/h3_chain_scene_prompt_editor.js",
    "../web/h3_chain_rich_scene_prompt_editor.js",
    "../web/h3_chain_plan_studio.js",
]) {
    const source = fs.readFileSync(new URL(relative, import.meta.url), "utf8");
    assert.match(source, /import \* as promptCompanionSync/);
    // Cache freshness is enforced by _web_cache_bust_unit_test.py.
    assert.match(source, /h3_prompt_companion_sync\.mjs\?v=\d+\.\d+\.\d+/);
    assert.match(source, /promptCompanionSync\.publishCompanionPrompt\?\./);
    assert.match(source, /typeof promptCompanionSync\.planHasNonPromptChanges === "function"/);
    assert.match(source, /planHasNonPromptChanges\(state\.plan, livePlan\)/);
    assert.match(source, /loadPlan\(true\)/);
}

for (const relative of [
    "../web/h3_chain_review_final.js",
    "../web/h3_chain_checkpoint_manager.js",
]) {
    const source = fs.readFileSync(new URL(relative, import.meta.url), "utf8");
    assert.match(source, /h3_prompt_companion_sync\.mjs\?v=\d+\.\d+\.\d+/,
        "every companion publisher loads the same cache-busted helper revision");
}

for (const relative of [
    "../web/h3_chain_scene_prompt_editor.js",
    "../web/h3_chain_rich_scene_prompt_editor.js",
]) {
    const source = fs.readFileSync(new URL(relative, import.meta.url), "utf8");
    assert.match(source, /typeof promptCompanionSync\.activeSceneIndexAfterRefresh === "function"/,
        "prompt editors remain usable during a partial browser-cache update");
    const handler = source.match(/node\._h3PromptCompanionSetActiveScene = (\(planNode, index\) => \{[^]*?^    });/m)[1];
    for (const shots of [[{id:"one"}, {id:"new"}], [{id:"one"}, {id:"copy"}, {id:"two"}]]) {
        const state = {planNode:plan, plan:{shots:[{id:"one"}]}};
        let selected;
        const apply = new Function("state", "loadPlan", "navigate", "optimizerBusy", `return ${handler}`)(
            state, () => { state.plan = {shots}; },
            (_offset, index) => { selected = state.plan.shots[Math.min(index, state.plan.shots.length - 1)].id; },
            () => false);
        assert.equal(apply(plan, 1), true);
        assert.equal(selected, shots[1].id, "new/duplicated scene is selected before polling");
        assert.equal(apply({}, 1), false, "unrelated Plans cannot change selection");
    }
}

const reviewSource = fs.readFileSync(
    new URL("../web/h3_chain_review_final.js", import.meta.url), "utf8");
assert.match(reviewSource, /import \* as promptCompanionSync/);
assert.match(reviewSource, /promptCompanionSync\.publishCompanionPrompt\?\./);
assert.match(reviewSource, /promptCompanionSync\.publishPlanCompanionScene\?\./);
assert.match(reviewSource, /_h3PromptCompanionSetScenePrompt/);
assert.equal(
    reviewSource.match(/refreshRestoredPlanEditors\(planNode\)/g)?.length,
    3,
    "review edits, checkpoint revisions, and saved-input restore all refresh complete Plan data",
);

console.log("H3 prompt companions: active-scene and review prompt synchronization pass");
