// Execute the actual Studio transport handlers, not an imitation player.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import * as core from "../web/h3_chain_plan_studio_core.mjs";
import * as chapters from "../web/h3_studio_chapters.mjs";
const source = fs.readFileSync(new URL("../web/h3_chain_plan_studio.js", import.meta.url), "utf8");
const handler = name => source.match(new RegExp(`^    function ${name}\\([^]*?^    }$`, "m"))[0];
const closure = name => source.match(new RegExp(`^        const ${name} = [^]*?^        };$`, "m"))[0];
const rows = Array.from({length:6}, (_, index) => ({id:`s${index + 1}`, deliveredFrames:240}));
const segments = core.studioTimelineSegments(rows, [{scene_id:"s3", start_frame:600}], 3000,
    [{scene_id:"s1", in_frame:24, out_frame:144}]);
const model = {result:{shots:rows}, segments, totalSeconds:125, workspaceEndFrame:3000};
const group = chapters.studioChapterGroups([{id:"first", start_scene_id:"s1"},
    {id:"next", start_scene_id:"s4"}], rows, segments)[0];
let focused = true, now = 0, nextFrame = 0;
const frames = new Map(), preloads = [];
function media(url = "") {
    const events = new Map();
    return {dataset:url ? {source:url} : {}, currentTime:0, duration:100,
        isConnected:true, paused:true,
        play() { this.paused = false; return Promise.resolve(); },
        pause() { this.paused = true; }, load() {},
        removeAttribute(name) { delete this[name]; },
        addEventListener(name, callback) { events.set(name, callback); },
        metadata() { const callback = events.get("loadedmetadata"); events.delete("loadedmetadata"); callback?.(); },
    };
}
const video = media(), generatedAudio = media(), sourceVideo = media(), sourceTimelineAudio = media();
const state = {plan:{shots:rows}, active:0, playerIndex:0, playerSegmentKey:"scene:0",
    player:video, playerAudio:generatedAudio, sourcePlayer:sourceVideo, sourceAudioPlayer:sourceTimelineAudio,
    editorialClockFrame:null, mediaClockFrame:null, timelinePosition:0, contextPlayers:[],
    checkpoints:new Map(rows.map((row, index) => [index + 1, {scene:index + 1, scene_id:row.id,
        ready:true, delivered_frames:240, video:`base${index}.mp4`,
        ...(index === 0 ? {presentation_video:"alt.mp4"} : {}), audio:`original${index}.wav`}]))};
const slider = {}, clock = {}, play = {};
let sourceAudioEnabled = false;
const context = vm.createContext({...core, ...chapters, state, video, generatedAudio, sourceVideo,
    sourceTimelineAudio, slider, clock, play, FPS:24, Event, setTimeout:callback => callback(),
    performance:{now:() => now},
    requestAnimationFrame:callback => { frames.set(++nextFrame, callback); return nextFrame; },
    cancelAnimationFrame:id => frames.delete(id),
    playbackModel:() => chapters.studioChapterPlayback(model, focused ? group : null),
    timelineModel:() => model, timing:() => model.result,
    sourceAudio:() => sourceAudioEnabled ? {available_duration_seconds:125} : null,
    sourceAudioUrl:() => sourceAudioEnabled ? "source.wav" : "",
    sourceReference:() => null, sourcePreviewUrl:() => "", videoUrl:value => value,
    root:{querySelector:() => null, querySelectorAll:() => []},
    formatClock:value => String(value), trimForScene:() => null,
    persistView(){}, renderSourceTimeline(){}, renderSourceAudioTimeline(){},
    updateTimelineSelection(){}, revealActiveTimelineScene(){}, publishActiveScene(){},
    positionTimelinePlayhead(){}, updateSubtitleOverlay(){},
    captureHandoffFrame(){}, promotePrimedSegment:index => preloads.push(index),
    synchronizeGeneratedAudio(){}, synchronizeSourceTimelineAudio(){}, syncSource(){},
    synchronizeSceneDialogue:() => null, // These fixtures have no per-scene dialogue.
    pausePlayerMonitors() { generatedAudio.pause(); sourceVideo.pause(); sourceTimelineAudio.pause(); },
    extendTimelineWorkspace() { throw Error("Chapter playback must not extend the project workspace"); },
});
vm.runInContext([
    handler("playerCheckpoint"), handler("seekTimeline"),
    "let videoAdvancePending = false;",
    ...["updateTransportPosition", "stopAtChapterEnd", "sourceTimelineSecond", "playerTimelineSecond",
        "stopMediaClock", "stopEditorialClock", "advanceVideoSegment", "refreshVideoTransport",
        "refreshSourceTransport", "startEditorialClock", "togglePlayerPlayback"].map(closure),
    ...["advanceVideoSegment", "refreshVideoTransport", "refreshSourceTransport", "startEditorialClock",
        "togglePlayerPlayback", "stopAtChapterEnd"].map(name => `globalThis.${name} = ${name};`),
    "state.updatePlayerPosition = updateTransportPosition;",
].join("\n"), context);
state.playerSlider = slider;
function seek(seconds) { context.seekTimeline(seconds); video.metadata(); generatedAudio.metadata(); sourceTimelineAudio.metadata(); }
seek(0);
assert.equal(video.dataset.source, "alt.mp4", "chapter playback uses the selected ALT");
assert.equal(generatedAudio.dataset.source, "original0.wav", "ALT keeps original scene audio");
assert.equal(video.currentTime, 1, "trim/slip offset is applied");
assert.equal(generatedAudio.currentTime, 1);
assert.equal(slider.value, "0");
video.currentTime = 6; video.paused = false;
context.refreshVideoTransport(); video.metadata();
assert.equal(state.playerIndex, 1, "trim endpoint advances to the next scene");
assert.equal(state.timelinePosition, 5);
assert.equal(slider.value, "120", "chapter slider uses integer local frames");
seek(14.99); video.paused = false; video.currentTime = 10;
context.refreshVideoTransport();
assert.ok(state.playerSegmentKey.startsWith("gap:"), "scene end enters the internal black gap");
assert.equal(state.timelinePosition, 15);
context.startEditorialClock();
const tick = [...frames.values()].at(-1); frames.clear(); now = 10100; tick(now);
video.metadata();
assert.equal(state.playerIndex, 2, "black gap clock reaches the next saved scene");
assert.ok(state.timelinePosition >= 25);
seek(34); video.paused = false; video.currentTime = 10;
generatedAudio.paused = sourceVideo.paused = sourceTimelineAudio.paused = false;
context.refreshVideoTransport();
assert.equal(state.timelinePosition, 35);
assert.equal(slider.value, String(group.durationSeconds * 24));
assert.ok([video, generatedAudio, sourceVideo, sourceTimelineAudio].every(item => item.paused),
    "all monitors stop at the chapter boundary");
assert.equal(state.playerIndex, 2, "the next chapter is not loaded");
assert.equal(context.advanceVideoSegment(), false, "a late ended event cannot cross the boundary");
context.togglePlayerPlayback(); video.metadata();
assert.equal(state.playerIndex, 0, "replay starts the same chapter");
assert.equal(video.dataset.source, "alt.mp4");
seek(999);
assert.ok(Math.abs(video.currentTime - (10 - 1 / 24)) < 1e-8, "end scrub holds the last used frame");
context.refreshVideoTransport();
assert.equal(state.timelinePosition, 35, "timeupdate keeps the exclusive-end clock while holding its frame");

// Source-only and silent/unrendered chapters stop too, even when a soundtrack
// continues into the next chapter or open black workspace.
state.checkpoints.clear(); sourceAudioEnabled = true; seek(34);
sourceTimelineAudio.currentTime = 35.1; sourceTimelineAudio.paused = false;
context.refreshSourceTransport();
assert.equal(state.timelinePosition, 35); assert.equal(sourceTimelineAudio.paused, true);
sourceAudioEnabled = false; seek(34);
context.startEditorialClock();
const finalTick = [...frames.values()].at(-1); frames.clear(); now += 2000; finalTick(now);
assert.equal(state.timelinePosition, 35);
assert.equal(state.editorialClockFrame, null);
assert.equal(frames.size, 0);
focused = false; seek(80);
assert.equal(state.timelinePosition, 80, "returning to full timeline restores project-wide seeking");
assert.equal(slider.value, "80");
console.log("Chapter transport: real seek/handoff, ALT audio, trim/slip, gaps, all clocks, stop/replay and full timeline pass");
