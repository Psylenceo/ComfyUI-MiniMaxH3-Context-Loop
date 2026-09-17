import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import * as core from "../web/h3_chain_plan_core.mjs";
import * as studioCore from "../web/h3_chain_plan_studio_core.mjs";

const carousel = fs.readFileSync(new URL("../web/h3_project_asset_manager.js", import.meta.url), "utf8");
const studio = fs.readFileSync(new URL("../web/h3_chain_plan_studio.js", import.meta.url), "utf8");
function handler(source, name) {
    const match = source.match(new RegExp(`^    (?:async )?function ${name}\\([^]*?^    }$`, "m"));
    assert.ok(match, name);
    return match[0];
}

// Exercise the real handlers, including their await boundaries (not regex-only checks).
function carouselContext() {
    const requests = [];
    const runNameWidget = {value:"run_a"};
    const context = vm.createContext({
        state:{catalog:{project:"run_a"}, selected:"keep", uploading:false},
        runNameWidget, runNameInput:{value:"run_a"}, catalogWidget:{},
        node:{graph:{setDirtyCanvas(){}}}, project:() => runNameWidget.value,
        ownership:{runName:"run_a", async select(run) { this.runName = run; }},
        projectMutationOptions:async (_node, _run, options) => options,
        jsonRequest:(route, options) => new Promise((resolve, reject) => requests.push({route, options, resolve, reject})),
        syncDownstreamPlan:(_node, run) => { context.planRun = run; },
        publishProjectAssetCatalogChanged(){}, render(){}, setStatus(){},
        promptTag:asset => asset.tag, window:{confirm:() => true},
        FormData:class { constructor() { this.fields = {}; } append(key, value) { this.fields[key] = value; } },
    });
    vm.runInContext("let projectEpoch = 0; let projectDisposed = false;\n" + [
        "captureProjectOperation", "isCurrentProjectOperation", "requireCurrentProjectOperation",
        "mutationRequest", "persistCatalog", "importAsset", "uploadFiles",
        "updateAsset", "reorderAssets", "deleteAsset", "duplicateAsset", "folderRequest",
    ].map(name => handler(carousel, name)).join("\n"), context);
    return {context, requests, switchRun(run) {
        vm.runInContext("projectEpoch += 1", context);
        runNameWidget.value = run; context.runNameInput.value = run; context.planRun = run;
    }};
}
const tick = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
const result = {catalog:{project:"run_a", assets:[]}, asset:{id:"late", tag:"ref", role:"picture"}};
for (const returnToA of [false, true]) {
    for (const method of ["importAsset", "updateAsset", "reorderAssets", "deleteAsset", "duplicateAsset", "folderRequest"]) {
        const fixture = carouselContext();
        const pending = fixture.context[method]({id:"asset", tag:"ref"}, {});
        // importAsset intentionally lets its dialog handle errors.
        const settled = Promise.resolve(pending).catch(error => assert.equal(error.staleProject, true));
        await tick();
        assert.equal(fixture.requests.length, 1);
        fixture.switchRun("run_b");
        if (returnToA) fixture.switchRun("run_a");
        fixture.requests[0].resolve(result);
        await settled;
        assert.equal(fixture.context.state.selected, "keep", method);
        assert.equal(fixture.context.planRun, returnToA ? "run_a" : "run_b", method);
        assert.equal(fixture.context.state.catalog.assets, undefined, method);
    }
}
{
    const fixture = carouselContext();
    const pending = fixture.context.uploadFiles([{name:"one.png"}, {name:"two.png"}]);
    await tick();
    assert.equal(fixture.requests[0].options.body.fields.project, "run_a");
    fixture.switchRun("run_b");
    fixture.requests[0].resolve(result);
    await pending;
    assert.equal(fixture.requests.length, 1, "remaining files must not leak into B");
    assert.equal(fixture.context.state.uploading, false);
    assert.equal(fixture.context.planRun, "run_b");
}
{
    const fixture = carouselContext();
    const pending = fixture.context.importAsset({source:"input"});
    await tick(); fixture.requests[0].resolve(result); await pending;
    assert.equal(fixture.context.state.selected, "late", "current-run import still applies");
    const token = fixture.context.captureProjectOperation();
    fixture.switchRun("run_b");
    await assert.rejects(fixture.context.mutationRequest("unused", {}, token), /Run changed/);
    assert.equal(fixture.requests.length, 1, "stale dialogs must not send a mutation");
    vm.runInContext("projectDisposed = true", fixture.context);
    await assert.rejects(fixture.context.importAsset({}), /Run changed/);
}

function studioContext() {
    const timers = new Map(); const writes = []; let id = 0;
    const context = vm.createContext({
        ...core, ...studioCore, console, structuredClone,
        branches:{ready:true, conflict:"", draftRecovery:null},
        state:{plan:{shots:[{id:"scene_a", prompt:["A"], length:345}], chapters:[]},
            editorial:{}, checkpoints:new Map()},
        node:{properties:{}}, alternateTakeWidget:{value:"stale"},
        runName:() => "run_b", cacheStudioPresentation(){}, dirty(){}, renderStatus(){},
        currentBranch:() => "main", scopedPath:path => path,
        timing:() => ({shots:[{id:"scene_b", rawFrames:124, deliveredFrames:124}]}),
        renderShell(){}, flushHistoryDraft:async () => {},
        setTimeout:fn => { timers.set(++id, fn); return id; }, clearTimeout:key => timers.delete(key),
        projectMutationOptions:async (_node, _run, options) => options,
        api:{fetchApi:async (_route, options) => {
            writes.push(JSON.parse(options.body));
            return {ok:true, json:async () => ({editorial:{revision:"c".repeat(32)}})};
        }},
    });
    const names = ["normalizedEditorial", "editorialPayload", "applyEditorialPayload", "refreshEditorialBinding", "syncAlternateTakeWidget", "scheduleEditorialSave",
        "sceneLocked", "trimForScene", "setSceneTrim", "setScenePlacement", "flushProjectWrites"];
    if (studio.includes("function editorialSignature(")) names.push("editorialSignature", "persistEditorial");
    vm.runInContext(names.map(name => handler(studio, name)).join("\n"), context);
    return {context, writes, async flush() {
        for (const fn of timers.values()) await fn(); timers.clear();
        await context.state.editorialSavePromise;
    }};
}
const incoming = {
    format:"h3_chain_editorial_v1", run_name:"run_b", revision:"b".repeat(32),
    chapters:[{id:"b_chapter", title:"Saved B", start_scene:1, start_scene_id:"scene_b"}],
    scene_order:[{scene:1, scene_id:"scene_b"}],
    placements:[{scene:1, scene_id:"scene_b", start_frame:48}],
    trims:[{scene:1, scene_id:"scene_b", out_frame:90}], locked_scene_ids:["scene_b"],
    subtitles:{mode:"off", asset_id:"", offset_seconds:0}, alternate_draft:null, replacements:[],
};
for (const branchId of ["main", "a".repeat(32)]) {
    const fixture = studioContext();
    const {context} = fixture;
    context.currentBranch = () => branchId;
    context.state.plan.shots = [{id:"scene_a",prompt:["A"],length:124},
        {id:"scene_b",prompt:["B"],length:124}];
    const rows = context.state.plan.shots.map(shot=>({id:shot.id,rawFrames:124,deliveredFrames:124}));
    context.timing = () => ({shots:rows});
    const packed = {...incoming,chapters:[],scene_order:rows.map((row,i)=>({scene:i+1,scene_id:row.id})),
        placements:[],trims:[],locked_scene_ids:[]};
    context.applyEditorialPayload(packed);
    const originalPlan = JSON.stringify(context.state.plan);
    let observations = 0;
    context.branches.observe = async () => { observations++; };
    context.setScenePlacement(1,200);
    assert.ok(observations>0, "a gap drop captures recovery without waiting for a form event or polling");
    context.applyEditorialPayload(packed);
    assert.equal(context.state.editorial.placements[0].start_frame,200,"an older GET cannot erase the pending gap");
    await fixture.flush();
    assert.equal(fixture.writes.length,1,"placement drop sends one editorial save");
    assert.equal(fixture.writes[0].branch_id,branchId,"gap saves remain scoped to the active branch");
    assert.ok(observations>1,"save completion updates local recovery immediately");
    assert.equal(JSON.stringify(context.state.plan),originalPlan,"black gaps do not edit generation settings or prompts");
    const saved = {...fixture.writes[0],revision:"c".repeat(32)};
    // Fresh Studio state, as on a workflow reload, rehydrates the saved cut.
    const reopened = studioContext();
    reopened.context.currentBranch = () => branchId;
    reopened.context.state.plan = JSON.parse(originalPlan);
    reopened.context.applyEditorialPayload(JSON.parse(JSON.stringify(saved)));
    const segments = studioCore.studioTimelineSegments(rows,reopened.context.state.editorial.placements);
    const gap = segments.find(segment=>segment.kind==="gap");
    assert.equal(gap.startFrame,124);
    assert.equal(gap.durationFrames,76,"the exact between-scene gap survives a fresh Studio reload");
    assert.equal(segments.at(-1).startFrame,200);
    assert.equal(reopened.writes.length,0,"reloading a saved gap is read-only");
    context.setScenePlacement(1,null); await fixture.flush();
    assert.deepEqual(fixture.writes[1].placements,[],"packing the scene again still removes its gap");
}
{
    const fixture = studioContext();
    fixture.context.state.plan.shots[0].id = "scene_b";
    fixture.context.applyEditorialPayload({...incoming, trims:[], locked_scene_ids:[]});
    fixture.context.setSceneTrim(0, 102, 51);
    await fixture.flush();
    assert.equal(fixture.writes[0].trims[0].in_frame, 51);
    assert.equal(fixture.writes[0].trims[0].out_frame, 102);
    const restored = fixture.context.normalizedEditorial(fixture.writes[0]);
    assert.equal(restored.trims[0].in_frame, 51, "reload preserves the slipped source window");
    assert.equal(restored.trims[0].out_frame, 102);
}
{
    const fixture = studioContext();
    fixture.context.applyEditorialPayload(incoming);
    await fixture.flush();
    assert.equal(fixture.writes.length, 0, "GET must never POST destination editorial");
    assert.match(fixture.context.state.editorialBindingError, /matching Plan/);
    fixture.context.scheduleEditorialSave(); await fixture.flush();
    assert.equal(fixture.writes.length, 0, "mismatched Plan must not rewrite B after an unrelated edit");
    assert.equal(fixture.context.alternateTakeWidget.value, "null", "hidden widget still synchronizes");
}
{
    const fixture = studioContext();
    fixture.context.state.plan.shots[0].id = "scene_b";
    fixture.context.applyEditorialPayload(incoming);
    fixture.context.state.plan.shots[0].seed = 42;
    fixture.context.scheduleEditorialSave(); await fixture.flush();
    assert.equal(fixture.writes.length, 0, "seed edit must not overwrite saved chapters");
    fixture.context.state.editorial.trims[0].out_frame = 81;
    fixture.context.scheduleEditorialSave();
    fixture.context.applyEditorialPayload(incoming);
    assert.equal(fixture.context.state.editorial.trims[0].out_frame, 81, "refresh must not clobber pending edits");
    await fixture.flush();
    assert.equal(fixture.writes.length, 1);
    assert.equal(fixture.writes[0].trims[0].out_frame, 81);
    assert.deepEqual(fixture.writes[0].chapters, incoming.chapters);
    assert.deepEqual(fixture.writes[0].placements, incoming.placements);
    fixture.context.applyEditorialPayload(incoming, 0);
    assert.equal(fixture.context.state.editorial.trims[0].out_frame, 81,
        "a GET started before the edit must not clobber it after POST completes");
}
// A branch guard must not silently drop an explicit trim and let the next
// checkpoint poll restore the untrimmed clip. It must retain the local edit,
// explain why it is unsaved, and prevent switching/queueing past that error.
for (const blocked of [
    {ready:false},
    {conflict:"Reload saved branch, or keep these edits as a new empty branch."},
    {draftRecovery:{authoring:{}}},
]) {
    const fixture = studioContext();
    const {context} = fixture;
    context.state.plan.shots[0].id = "scene_b";
    const full = {...incoming, trims:[], locked_scene_ids:[]};
    context.applyEditorialPayload(full);
    Object.assign(context.branches, blocked);
    context.setSceneTrim(0, 81);
    context.applyEditorialPayload(full);
    assert.equal(context.state.editorial.trims[0]?.out_frame, 81,
        "a blocked save must keep the requested 81/124 trim through refresh");
    assert.ok(context.state.editorialSaveError, "blocked saving must be visible");
    await fixture.flush();
    assert.equal(fixture.writes.length, 0, "do not bypass branch protection");
    await assert.rejects(context.flushProjectWrites(), /.+/,
        "queue/switch must not silently abandon an unsaved trim");
    Object.assign(context.branches, {ready:true, conflict:"", draftRecovery:null});
    context.scheduleEditorialSave(); await fixture.flush();
    assert.equal(fixture.writes.length, 1, "explicit retry after resolving the guard saves the trim");
    assert.equal(fixture.writes[0].trims[0].out_frame, 81);
    assert.equal(context.state.editorialSaveError, "");
}
{
    const fixture = studioContext();
    const {context} = fixture;
    context.state.plan.shots[0].id = "scene_b";
    const full = {...incoming, trims:[], locked_scene_ids:[]};
    context.applyEditorialPayload(full);
    context.setSceneTrim(0, 81); await fixture.flush();
    const saved = {...full, revision:"c".repeat(32), trims:fixture.writes[0].trims};
    context.applyEditorialPayload(saved);
    assert.equal(context.state.editorial.trims[0].out_frame, 81,
        "successful saving and a fresh GET keep the requested trim");
    context.setSceneTrim(0, 124); await fixture.flush();
    assert.deepEqual(fixture.writes[1].trims, [], "restoring full length remains supported");
}
{
    const fixture = studioContext();
    const {context} = fixture;
    context.state.plan.shots[0].id = "scene_b";
    context.applyEditorialPayload({...incoming, trims:[], locked_scene_ids:[]});
    let complete;
    context.api.fetchApi = () => new Promise(resolve => { complete = resolve; });
    context.setSceneTrim(0, 81);
    const inFlight = fixture.flush(); await tick();
    context.branches.conflict = "Reload saved branch";
    context.setSceneTrim(0, 102);
    complete({ok:true, json:async () => ({editorial:{revision:"c".repeat(32)}})});
    await inFlight;
    assert.equal(context.state.editorial.trims[0].out_frame, 102);
    assert.ok(context.state.editorialSaveError,
        "finishing an older save cannot mark a newer blocked edit as saved");
    context.applyEditorialPayload({...incoming, trims:[], locked_scene_ids:[]});
    assert.equal(context.state.editorial.trims[0].out_frame, 102);
}
assert.doesNotMatch(handler(studio, "loadPlan"), /scheduleEditorialSave\(/);

// The workflow may already contain later scenes when an older editorial
// document is loaded. They are in the local baseline, but NOT in scene_order
// on disk: a field-diff alone can send new placements with the old order.
for (const branchId of ["main", "a".repeat(32)]) {
    const fixture = studioContext();
    const {context} = fixture;
    context.currentBranch = () => branchId;
    context.state.plan.shots = [{id:"scene_a",prompt:["A"],length:124},
        {id:"scene_b",prompt:["B"],length:124}];
    context.timing = () => ({shots:context.state.plan.shots.map(shot=>({
        id:shot.id,rawFrames:124,deliveredFrames:124}))});
    const saved = {...incoming,chapters:[{id:"one",title:"Keep saved chapter",start_scene:1,start_scene_id:"scene_a"}],
        scene_order:[{scene:1,scene_id:"scene_a"}],
        placements:[{scene:1,scene_id:"scene_a",start_frame:24}],trims:[],locked_scene_ids:[]};
    const original = JSON.stringify(saved);
    context.applyEditorialPayload(saved);
    context.scheduleEditorialSave(); await fixture.flush();
    assert.equal(fixture.writes.length,0,"merely loading a longer Plan must not rewrite editorial data");
    context.setScenePlacement(1,200); await fixture.flush();
    const written = fixture.writes[0];
    assert.equal(written.branch_id,branchId);
    assert.deepEqual(written.scene_order,[{scene:1,scene_id:"scene_a"},{scene:2,scene_id:"scene_b"}],
        "new placement and its scene must be saved together even if the Plan did not change after hydration");
    for (const [index,placement] of written.placements.entries()) {
        assert.ok(written.scene_order.some(row=>row.scene_id===placement.scene_id && row.scene===placement.scene),
            `Editorial placement ${index+1} must target a scene in scene_order`);
    }
    assert.deepEqual(written.placements,[{scene:1,scene_id:"scene_a",start_frame:24},
        {scene:2,scene_id:"scene_b",start_frame:200}]);
    assert.deepEqual(written.chapters,saved.chapters,"gap saving retains untouched saved chapter notes");
    assert.equal(JSON.stringify(saved),original,"the fetched editorial document is never mutated");
    const reopened = studioContext();
    reopened.context.state.plan = structuredClone(context.state.plan);
    reopened.context.applyEditorialPayload({...written,revision:"c".repeat(32)});
    assert.equal(reopened.context.state.editorial.placements[1].start_frame,200,
        "reload retains the later-scene gap");
}

// A removed/renamed empty placeholder is not evidence of a different Run.
// Only a fresh backend inventory may establish that it has no saved renders.
const placeholderEditorial = {
    ...incoming, chapters:[], placements:[], trims:[], locked_scene_ids:[],
    scene_order:[{scene:1, scene_id:"scene_a"}, {scene:2, scene_id:"old_empty"}],
};
{
    const fixture = studioContext();
    fixture.context.state.plan.shots.push({id:"new_empty", prompt:[], length:306});
    fixture.context.applyEditorialPayload(placeholderEditorial, 0, ["old_empty"]);
    assert.equal(fixture.context.state.editorialBindingError, "");
    fixture.context.scheduleEditorialSave(); await fixture.flush();
    assert.equal(fixture.writes.length, 0, "hydration/seed edits must not reconcile on disk");
    fixture.context.state.editorial.placements.push({scene_id:"new_empty", start_frame:400});
    fixture.context.scheduleEditorialSave(); await fixture.flush();
    assert.equal(fixture.writes.length, 1);
    assert.deepEqual(fixture.writes[0].scene_order, [
        {scene:1, scene_id:"scene_a"}, {scene:2, scene_id:"new_empty"},
    ], "an explicit edit reconciles only verified unused entries");
    assert.deepEqual(placeholderEditorial.scene_order[1], {scene:2, scene_id:"old_empty"},
        "the fetched document is never mutated");
}
for (const proof of [undefined, []]) {
    const fixture = studioContext();
    fixture.context.applyEditorialPayload(placeholderEditorial, 0, proof);
    assert.match(fixture.context.state.editorialBindingError, /old_empty/,
        "missing proof or a rendered missing scene remains protected");
}
for (const protectedField of [
    {chapters:[{id:"chapter", start_scene:2, start_scene_id:"old_empty"}]},
    {placements:[{scene:2, scene_id:"old_empty", start_frame:400}]},
    {trims:[{scene:2, scene_id:"old_empty", out_frame:90}]},
    {locked_scene_ids:["old_empty"]},
    {replacements:[{scene:2, scene_id:"old_empty"}]},
    {alternate_draft:{scene:2, scene_id:"old_empty"}},
]) {
    const fixture = studioContext();
    fixture.context.applyEditorialPayload({...placeholderEditorial, ...protectedField}, 0, ["old_empty"]);
    assert.match(fixture.context.state.editorialBindingError, /old_empty/);
    fixture.context.scheduleEditorialSave(); await fixture.flush();
    assert.equal(fixture.writes.length, 0, "saved edits are never considered an unused placeholder");
}
{
    const fixture = studioContext();
    fixture.context.applyEditorialPayload(placeholderEditorial, 0, ["old_empty"]);
    fixture.context.applyEditorialPayload(placeholderEditorial, 0, []);
    assert.match(fixture.context.state.editorialBindingError, /old_empty/,
        "a later checkpoint inventory must revoke the unused-scene allowance");
}
console.log("Run-switch safety: hydration, explicit saves, stale mutations, ABA, batches and disposal passed");

// Explicit rename -> save -> undo must restore ALL saved references, including
// chapter starts and the selected alternate, with or without an intervening GET.
for (const poll of [false, true]) {
    const f = studioContext();
    const {context:c} = f;
    c.state.plan.shots[0].id = "scene_b";
    c.state.plan.chapters = structuredClone(incoming.chapters);
    const saved = {...incoming, replacements:[{scene:1, scene_id:"scene_b",
        base_revision:"1".repeat(32), alternate_revision:"2".repeat(32), media_mode:"picture_only"}]};
    c.applyEditorialPayload(saved);
    const rename = name => {
        const edit = core.renamePlanShot(c.state.plan, 0, name);
        studioCore.remapStudioEditorialSceneId(c.state.editorial, edit.previousId, edit.id);
        c.scheduleEditorialSave(0, edit);
    };
    rename("accidental"); await f.flush();
    assert.equal(f.writes.length, 1);
    assert.equal(f.writes[0].chapters[0].start_scene_id, "accidental");
    assert.equal(c.state.editorialStored.scene_order[0].scene_id, "accidental",
        "successful saves advance the saved comparison snapshot immediately");
    if (poll) c.applyEditorialPayload({...f.writes[0], revision:"c".repeat(32)});
    rename("scene_b"); await f.flush();
    assert.equal(f.writes.length, 2, "undo sends the restored cut without a workflow reload");
    const restored = f.writes[1];
    for (const field of ["scene_order", "placements", "trims", "replacements"]) {
        assert.equal(restored[field][0].scene_id, "scene_b", field);
    }
    assert.equal(restored.chapters[0].start_scene_id, "scene_b");
    assert.equal(restored.replacements[0].alternate_revision, "2".repeat(32));
    assert.equal(restored.trims[0].out_frame, 90);
    assert.equal(c.state.editorialBindingError, "");
    c.state.plan.shots[0].seed = 111;
    c.scheduleEditorialSave(); await f.flush();
    assert.equal(f.writes.length, 2, "a subsequent prompt/seed edit does not write the cut again");
}

// A mismatch pause must be re-evaluated before retrying, without using GET
// hydration to overwrite a local placement made while the name was wrong.
{
    const f = studioContext(); const c = f.context;
    c.applyEditorialPayload(incoming);
    c.state.editorial.placements = [{scene_id:"scene_a", start_frame:72}];
    c.scheduleEditorialSave(); await f.flush();
    assert.equal(f.writes.length, 0);
    assert.ok(c.state.editorialSaveError);
    c.state.plan.shots[0].id = "scene_b";
    studioCore.remapStudioEditorialSceneId(c.state.editorial, "scene_a", "scene_b");
    c.refreshEditorialBinding();
    assert.equal(c.state.editorialBindingError, "");
    assert.equal(c.applyEditorialPayload(incoming), false,
        "revalidation must not open the door to overwriting the unsaved placement");
    assert.equal(c.state.editorial.placements[0].start_frame, 72);
    c.scheduleEditorialSave(); await f.flush();
    assert.equal(f.writes.length, 1);
    assert.equal(f.writes[0].placements[0].start_frame, 72);
    assert.equal(f.writes[0].trims[0].out_frame, 90, "filtered saved edits are preserved");
    assert.equal(c.state.editorialBindingError, "");
    assert.equal(c.state.editorialSaveError, "");
}

// Renaming a known scene does NOT license removing another Run's missing scene.
{
    const f = studioContext(); const c = f.context;
    c.state.plan.shots[0].id = "scene_b";
    c.applyEditorialPayload({...incoming, scene_order:[...incoming.scene_order,
        {scene:2, scene_id:"unrelated_saved_scene"}]});
    const edit = core.renamePlanShot(c.state.plan, 0, "opening");
    studioCore.remapStudioEditorialSceneId(c.state.editorial, edit.previousId, edit.id);
    c.scheduleEditorialSave(0, edit); await f.flush();
    assert.equal(f.writes.length, 0);
    assert.match(c.state.editorialBindingError, /unrelated_saved_scene/);
}
console.log("Studio rename/undo: chapter starts, alternate takes, retry and unrelated-Run protection pass");

// Ownership/branch pauses retain the explicit rename for a later Retry, but
// never publish it while that separate authority guard is still blocking.
{
    const f = studioContext(); const c = f.context;
    c.state.plan.shots[0].id = "scene_b";
    c.state.plan.chapters = structuredClone(incoming.chapters);
    c.applyEditorialPayload(incoming);
    c.branches.ready = false;
    const edit = core.renamePlanShot(c.state.plan, 0, "opening");
    studioCore.remapStudioEditorialSceneId(c.state.editorial, edit.previousId, edit.id);
    c.scheduleEditorialSave(0, edit); await f.flush();
    assert.equal(f.writes.length, 0);
    assert.match(c.state.editorialSaveError, /branches/);
    c.branches.ready = true;
    c.scheduleEditorialSave(0); await f.flush();
    assert.equal(f.writes.length, 1);
    assert.equal(f.writes[0].scene_order[0].scene_id, "opening");
    assert.equal(f.writes[0].chapters[0].start_scene_id, "opening");
}
