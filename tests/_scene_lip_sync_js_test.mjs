import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import {normalizeSceneLipSyncSource, sceneLipSyncPlayback} from "../web/h3_scene_lip_sync.mjs";
import {parsePlanJson, planToJson} from "../web/h3_chain_plan_core.mjs";
import {applySceneLipSync} from "../web/h3_policy_core.mjs";

const selection = {asset_id:"dialogue", start_seconds:1, final_audio:"mix"};
const shots = [{id:"before", prompt:"unchanged"},
    {id:"talk", prompt:"speak", source_audio_target:"locked", lip_sync_source:selection},
    {id:"after", prompt:"unchanged"}];
const plan = parsePlanJson(JSON.stringify({shots}));
assert.deepEqual(parsePlanJson(planToJson(plan)).shots[1].lip_sync_source, selection);
assert.equal(Object.hasOwn(plan.shots[0], "lip_sync_source"), false);
assert.equal(normalizeSceneLipSyncSource({...selection, start_seconds:.05}).start_seconds, 1/24);
for (const bad of [{}, {...selection, start_seconds:-1}, {...selection, start_seconds:Infinity}, {...selection, final_audio:"x"}]) {
    assert.throws(() => normalizeSceneLipSyncSource(bad));
}
const policy = {sourceAudioTarget:"off", sourceReference:"off", generatedContinuity:"off", finalAudio:"source"};
const segments = [
    {kind:"scene", sceneIndex:0, startSeconds:0, durationSeconds:3},
    {kind:"gap", startSeconds:3, durationSeconds:1},
    {kind:"scene", sceneIndex:1, startSeconds:4, durationSeconds:2, sourceInFrame:12},
    {kind:"scene", sceneIndex:2, startSeconds:6, durationSeconds:3},
];
assert.equal(sceneLipSyncPlayback(shots, policy, segments, 2), null);
assert.equal(sceneLipSyncPlayback(shots, policy, segments, 3.5), null);
assert.equal(sceneLipSyncPlayback(shots, policy, segments, 4).seconds, 1.5);
assert.equal(sceneLipSyncPlayback(shots, policy, segments, 5).seconds, 2.5);
assert.equal(sceneLipSyncPlayback(shots, policy, segments, 6), null);
applySceneLipSync(shots[1], "off");
assert.equal(sceneLipSyncPlayback(shots, policy, segments, 5), null);
assert.deepEqual(shots[1].lip_sync_source, selection);
applySceneLipSync(shots[1], "on");

// Execute the actual Studio source controls: choose, offset, mix, inherit.
const source = fs.readFileSync(new URL("../web/h3_chain_plan_studio.js", import.meta.url), "utf8");
const begin = source.indexOf("const localSource = normalizeSceneLipSyncSource(shot.lip_sync_source);");
const end = source.indexOf("function audioOverrideSelect", begin);
const controls = [];
const makeElement = (tag, cls, text) => {
    const item = {tag, cls, text, children:[], value:"", listeners:{},
        append(...children) { this.children.push(...children); },
        addEventListener(name, callback) { this.listeners[name] = callback; },
        setCustomValidity() {}, reportValidity() {}};
    controls.push(item); return item;
};
let saves = 0, renders = 0, loads = 0;
const scene = {id:"talk", prompt:"Keep prompt", length:73};
const sandbox = {shot:scene, normalizeSceneLipSyncSource, applySceneLipSync,
    state:{sceneAudioAssetsRun:"test", sceneAudioAssets:[{id:"dialogue", name:"Dialogue"}]},
    runName:() => "test", element:makeElement, field:(_name, value) => value,
    button:() => ({}), form:{append() {}}, FPS:24,
    writePlan:() => saves++, renderPanel:() => renders++, renderStatus() {},
    loadSceneAudioAssets:() => loads++, sceneAudioAssetUrl:id => `/audio/${id}`};
function render() { controls.length = 0; vm.runInNewContext(source.slice(begin, end), {...sandbox}); }
render();
let select = controls.find(c => c.tag === "select");
select.value = "dialogue"; select.listeners.change();
assert.equal(scene.source_audio_target, "locked");
assert.equal(scene.lip_sync_source.asset_id, "dialogue");
render();
const mix = controls.filter(c => c.tag === "select")[1];
mix.value = "replace"; mix.listeners.change();
const offset = controls.find(c => c.tag === "input");
offset.value = "1.5"; offset.listeners.change();
assert.equal(scene.lip_sync_source.start_seconds, 1.5);
assert.equal(scene.lip_sync_source.final_audio, "replace", "Offset changes must preserve mix choice");
select = controls.find(c => c.tag === "select");
select.value = ""; select.listeners.change();
assert.equal(scene.lip_sync_source, undefined);
assert.equal(scene.prompt, "Keep prompt");
assert.equal(saves, 4);
assert.equal(renders, 2);
assert.equal(loads, 0, "Cached catalogs must not reload on every render");

// Exercise the production transport synchronizer, not only its clock helper.
const syncBegin = source.indexOf("const synchronizeSceneDialogue = ");
const syncEnd = source.indexOf("const synchronizeSourceTimelineAudio = ", syncBegin);
const media = () => ({dataset:{}, currentTime:0, duration:20, paused:true,
    loads:0, plays:0, muted:false, playbackRate:1,
    pause() { this.paused = true; }, play() { this.paused = false; this.plays++; return Promise.resolve(); },
    load() { this.loads++; }, removeAttribute(key) { delete this[key]; }});
const dialoguePlayer = media(), video = media(), generated = media(), project = media();
const monitor = {checked:true};
const transport = vm.createContext({
    state:{plan:{shots}, playerSegmentKey:"scene:1", playerIndex:1, sourceVolume:.7},
    settings:() => ({audioPolicy:policy}), playbackModel:() => ({segments}),
    sceneLipSyncPlayback, sceneAudioAssetUrl:id => `/audio/${id}`,
    sceneDialogue:dialoguePlayer, sourceTimelineAudio:project,
    video, generatedAudio:generated, sourceToggle:monitor, generatedToggle:{checked:true},
    sourceAudioMuted:() => false,
});
vm.runInContext(`${source.slice(syncBegin, syncEnd)}\nthis.sync = synchronizeSceneDialogue;`, transport);
transport.sync(true, 4.5);
assert.equal(dialoguePlayer.src, "/audio/dialogue");
assert.equal(dialoguePlayer.currentTime, 2);
assert.equal(dialoguePlayer.plays, 1);
assert.equal(dialoguePlayer.volume, .7);
assert.equal(video.muted, true);
assert.equal(generated.muted, true, "Do not double baked dialogue");
assert.equal(project.muted, false);
shots[1].lip_sync_source = {...selection, final_audio:"replace"};
transport.sync(true, 5);
assert.equal(project.muted, true);
transport.sync(true, 6);
assert.equal(dialoguePlayer.paused, true);
assert.equal(project.muted, false, "Next scene keeps the project track");
assert.equal(generated.muted, false);
const priorLoads = dialoguePlayer.loads;
transport.sync(true, 7);
assert.equal(dialoguePlayer.loads, priorLoads, "No repeated loads outside the selected scene");
console.log("Scene lip-sync UI: selection, offsets, reload, trim/gap playback and inheritance pass");
