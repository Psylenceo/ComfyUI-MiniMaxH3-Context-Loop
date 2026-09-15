#!/usr/bin/env node
// Actual Studio handlers with an isolated DOM fixture; no project mutation.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import * as core from "../web/h3_chain_plan_studio_core.mjs";
import * as chapters from "../web/h3_studio_chapters.mjs";

const rows = [{id:"one", rawFrames:362, deliveredFrames:340},
    {id:"two", rawFrames:239, deliveredFrames:234}];
const trim = [{scene_id:"one", out_frame:72}];
const slip = [{scene_id:"one", in_frame:51, out_frame:123}];
const before = core.studioTimelineSegments(rows, [], null, trim);
const after = core.studioTimelineSegments(rows, [], null, slip);
assert.equal(after[0].durationFrames, 72);
assert.deepEqual(after[1], before[1], "slip must not move later scenes");
assert.equal(core.studioPlayerSegmentClock(after, "scene:0", 51 / 24).timelineSeconds, 0);
assert.equal(core.studioPlayerSegmentClock(after, "scene:0", 123 / 24).boundaryReached, true);
assert.equal(core.studioPlayerSegmentClock(after, "scene:0", 122 / 24).boundaryReached, false);
assert.deepEqual(core.studioLatentSafeSlipStarts(362, 340, 340), [0]);
assert.deepEqual(core.studioLatentSafeSlipStarts(362, 340, 0), []);
for (const [raw, delivered] of [[362, 340], [239, 234], [153, 153]]) {
    const ends = core.studioLatentSafeOutFrames(raw, delivered);
    for (const duration of ends) {
        for (const start of core.studioLatentSafeSlipStarts(raw, delivered, duration)) {
            assert.ok(start === 0 || (start % 3 === 0 && ends.includes(start)));
            assert.ok(ends.includes(start + duration));
            assert.ok(start + duration <= delivered);
        }
    }
}

const source = fs.readFileSync(new URL("../web/h3_chain_plan_studio.js", import.meta.url), "utf8");
function handler(name) {
    const match = source.match(new RegExp(`^    (?:async )?function ${name}\\([^]*?^    }$`, "m"));
    assert.ok(match, name);
    return match[0];
}
function dom() {
    const listeners = new Map();
    const props = new Map([["--h3-scene-width", "144px"]]);
    return {
        disabled:false, textContent:"↔", style:{
            setProperty:(key, value) => props.set(key, value),
            getPropertyValue:(key) => props.get(key) ?? "",
        },
        // Simulates ComfyUI at 50% canvas zoom.
        getBoundingClientRect:() => ({width:72}), offsetWidth:144,
        setAttribute() {}, setPointerCapture() {}, releasePointerCapture() {},
        addEventListener(type, callback) { listeners.set(type, callback); },
        removeEventListener(type) { listeners.delete(type); },
        dispatch(type, values = {}) {
            listeners.get(type)?.({type, button:0, pointerId:1, clientX:0,
                preventDefault() {}, stopPropagation() {}, ...values});
        },
    };
}
let saves = 0, locked = false;
const state = {editorial:{trims:structuredClone(trim)}};
const context = vm.createContext({...core, state, FPS:24,
    timing:() => ({shots:rows}), sceneLocked:() => locked,
    scheduleEditorialSave:() => saves++, renderShell() {},
});
vm.runInContext(["trimForScene", "setSceneTrim", "enableSceneSlipDrag", "enableSceneLatentTrimDrag"]
    .map(handler).join("\n"), context);
function slipHandle() {
    const card = dom(), handle = dom();
    context.enableSceneSlipDrag(card, handle, 0);
    return {card, handle};
}
{
    const {card, handle} = slipHandle();
    handle.dispatch("pointerdown");
    handle.dispatch("pointermove", {clientX:51});
    assert.equal(handle.textContent, "51 ↔ 123");
    assert.equal(card.style.getPropertyValue("--h3-scene-width"), "144px");
    handle.dispatch("pointerup");
    assert.equal(saves, 1);
    assert.deepEqual(JSON.parse(JSON.stringify(state.editorial.trims)), slip);
    assert.equal(state.timelineDragging, false);
}
{
    const {handle} = slipHandle();
    handle.dispatch("pointerdown");
    handle.dispatch("pointermove", {clientX:51});
    handle.dispatch("pointercancel");
    assert.equal(saves, 1, "cancel must not persist");
    handle.dispatch("keydown", {key:"ArrowLeft"});
    assert.equal(state.editorial.trims[0].in_frame, 42);
    assert.equal(state.editorial.trims[0].out_frame, 114);
}
{
    const card = dom(), handle = dom();
    context.enableSceneLatentTrimDrag(card, handle, 0);
    handle.dispatch("pointerdown");
    handle.dispatch("pointermove", {clientX:51});
    handle.dispatch("pointercancel");
    assert.equal(saves, 2);
    assert.equal(card.style.getPropertyValue("--h3-scene-width"), "144px");
    handle.dispatch("pointerdown");
    handle.dispatch("pointermove", {clientX:51});
    handle.dispatch("pointerup");
    assert.equal(state.editorial.trims[0].in_frame, 42, "right trim preserves in");
    assert.equal(state.editorial.trims[0].out_frame, 165);
    handle.dispatch("dblclick");
    assert.deepEqual(JSON.parse(JSON.stringify(state.editorial.trims)), []);
    assert.equal(slipHandle().handle.disabled, true, "full source cannot slip");
}
locked = true;
state.editorial.trims = structuredClone(trim);
const prior = saves;
const {handle} = slipHandle();
handle.dispatch("pointerdown"); handle.dispatch("pointermove", {clientX:100});
handle.dispatch("pointerup"); handle.dispatch("keydown", {key:"ArrowRight"});
context.setSceneTrim(0, 123, 51);
assert.equal(saves, prior, "locks protect trim and slip");
assert.deepEqual(state.editorial.trims, trim);
// Exercise the real seek handler: picture/scene audio slip together while the
// global soundtrack preview stays on the existing editorial playhead clock.
function media(url) {
    return {...dom(), dataset:{source:url}, isConnected:true, duration:100,
        currentTime:0, play:async () => {}, pause() {}, load() {},
        removeAttribute() {}};
}
state.editorial.trims = structuredClone(slip);
state.active = 0;
state.player = media("picture");
state.playerAudio = media("scene-audio");
state.sourcePlayer = media("reference");
state.sourceAudioPlayer = media("soundtrack");
Object.assign(context, {
    ...chapters,
    playbackModel:() => chapters.studioChapterPlayback({result:{shots:rows}, segments:after,
        totalSeconds:after.at(-1).endSeconds}, null),
    timelineModel:() => ({result:{shots:rows}, segments:after, totalSeconds:after.at(-1).endSeconds}),
    playerCheckpoint:() => ({video:"picture", audio:"scene-audio"}),
    videoUrl:(value) => value,
    sourceReference:() => ({seek_seconds:0, frame_count:362}),
    sourcePreviewUrl:() => "reference", sourceAudio:() => ({seek_seconds:0}),
    sourceAudioUrl:() => "soundtrack", root:{querySelector:() => null, querySelectorAll:() => []},
    positionTimelinePlayhead() {}, updateSubtitleOverlay() {},
});
vm.runInContext(handler("seekTimeline"), context);
context.seekTimeline(1);
assert.equal(state.player.currentTime, 1 + 51 / 24);
assert.equal(state.playerAudio.currentTime, state.player.currentTime);
assert.equal(state.sourcePlayer.currentTime, state.player.currentTime);
assert.equal(state.sourceAudioPlayer.currentTime, 1);
assert.equal(state.timelinePosition, 1);
console.log("Plan Studio slip: grid, fixed timing, seek clock, drag/keys, zoom, reset, cancel and locks pass");
