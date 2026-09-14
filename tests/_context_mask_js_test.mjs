import assert from "node:assert/strict";
import {contextMaskGrid, normalizeContextMask, paintContextMask} from "../web/h3_context_mask_core.mjs";
import {calculatePlanTiming, parsePlanJson, planToJson} from "../web/h3_chain_plan_core.mjs";
import {applyCheckpointRevisionSet} from "../web/h3_chain_review_core.mjs";
import {contextMaskEditor} from "../web/h3_context_mask_editor.mjs";

const mask = {columns:2, rows:1, cells:[16, 0], strength:0.5};
assert.deepEqual(normalizeContextMask(mask), mask);
assert.notEqual(normalizeContextMask(mask).cells, mask.cells);
assert.equal(normalizeContextMask({...mask, cells:[0, 0]}), null);
for (const bad of [[], {...mask, columns:true}, {...mask, rows:513},
    {...mask, cells:[16]}, {...mask, cells:[17, 0]}, {...mask, cells:[false, 0]},
    {...mask, strength:NaN}, {...mask, strength:true}, {...mask, strength:-1}]) {
    assert.throws(() => normalizeContextMask(bad));
}
assert.deepEqual(contextMaskGrid(128, 64, mask), {
    columns:4, rows:2, cells:[16, 16, 0, 0, 16, 16, 0, 0], strength:0.5,
});
const painted = contextMaskGrid(320, 160);
paintContextMask(painted, {x:0.5, y:2.5}, {x:9.5, y:2.5}, {radius:1, softness:0});
assert.deepEqual(painted.cells.slice(20, 30), Array(10).fill(16), "fast drags have no holes");
assert.ok(painted.cells.slice(0, 10).every((n) => n === 0), "unpainted cells stay locked");
paintContextMask(painted, {x:4.5, y:2.5}, {x:4.5, y:2.5}, {radius:1, erase:true});
assert.equal(painted.cells[24], 0);
const soft = contextMaskGrid(320, 160);
paintContextMask(soft, {x:4.5, y:2.5}, {x:4.5, y:2.5}, {radius:2, softness:1});
assert.ok(soft.cells.some((n) => n > 0 && n < 16));
assert.ok(soft.cells.every(Number.isInteger), "soft brush uses bounded quantized mask levels");

const plan = parsePlanJson(JSON.stringify({shots:[
    {id:"one", prompt:"Opening", length:39},
    {id:"two", prompt:"Hard cut", length:39, visual_context_blocks:[
        {source:"one", frames:5, weaken_mask:mask},
    ]},
]}));
const roundtrip = parsePlanJson(planToJson(plan));
assert.deepEqual(roundtrip.shots[1].visual_context_blocks[0].weaken_mask, mask);
const settings = {contextLength:5, audioContextLength:0, encodeMode:"video",
    anchorMode:"head", continuationMode:"masked_av", generatedContinuity:"off"};
const timing = calculatePlanTiming(roundtrip, settings);
assert.deepEqual(timing.errors, []);
const clean = structuredClone(roundtrip);
delete clean.shots[1].visual_context_blocks[0].weaken_mask;
const cleanTiming = calculatePlanTiming(clean, settings);
for (let i = 0; i < 2; i++) {
    for (const key of ["rawFrames", "deliveredFrames", "audioContextLength", "startFrame", "endFrame"]) {
        assert.equal(timing.shots[i][key], cleanTiming.shots[i][key], key);
    }
}
assert.match(calculatePlanTiming(roundtrip, {...settings, continuationMode:"guide"}).errors.join("\n"),
    /Context weaken masks require Masked AV/);
const revision = {scene:2, scene_id:"two", scene_prompt:"Hard cut", seed:"303", raw_frames:39,
    steps:8, context_length:5, audio_context_length:0, continuation_mode:"masked_av",
    visual_context_blocks:[{source:"one", frames:5, weaken_mask:mask}]};
const restored = applyCheckpointRevisionSet(structuredClone(clean), [revision]);
assert.deepEqual(restored.shots[1].visual_context_blocks[0].weaken_mask, mask);
restored.shots[1].visual_context_blocks[0].weaken_mask.cells[0] = 0;
assert.equal(mask.cells[0], 16, "restoring does not mutate saved take metadata");
const legacyRevision = structuredClone(revision);
delete legacyRevision.visual_context_blocks[0].weaken_mask;
assert.equal(applyCheckpointRevisionSet(structuredClone(plan), [legacyRevision])
    .shots[1].visual_context_blocks[0].weaken_mask, undefined, "restoring a clean take clears the mask");

// Exercise the real editor handlers, including save-on-release and reload,
// without a live workflow, media files, external packages or a GPU.
class Element {
    constructor(tag) {
        this.tagName = tag; this.children = []; this.style = {}; this.events = new Map();
        this.attrs = {}; this.hidden = false; this.disabled = false; this.rects = [];
    }
    append(...children) { this.children.push(...children); }
    setAttribute(key, value) { this.attrs[key] = value; }
    addEventListener(type, handler) {
        const list = this.events.get(type) ?? []; list.push(handler); this.events.set(type, list);
    }
    fire(type, props = {}) {
        for (const handler of this.events.get(type) ?? []) {
            handler({preventDefault() {}, stopPropagation() {}, ...props});
        }
    }
    getContext() {
        return {clearRect:() => { this.rects = []; }, fillRect:(...rect) => this.rects.push(rect)};
    }
    getBoundingClientRect() { return {left:0, top:0, width:320, height:160}; }
    setPointerCapture(id) { this.pointer = id; }
    hasPointerCapture(id) { return this.pointer === id; }
    releasePointerCapture() { this.pointer = null; }
    pause() { this.paused = true; }
}
globalThis.document = {createElement:(tag) => new Element(tag)};
function mount(initialMask = null, enabled = true, ready = true) {
    const video = new Element("video");
    Object.assign(video, {videoWidth:ready ? 320 : 0, videoHeight:ready ? 160 : 0, controls:true, muted:true});
    const saved = [];
    const host = contextMaskEditor(video, initialMask, {enabled, onChange:(value) => saved.push(structuredClone(value))});
    const [stage, toggle, remove, toolbar, summary] = host.children;
    const canvas = stage.children[1];
    const [strength, radius, softness] = toolbar.children.slice(0, 3).map((label) => label.children[1]);
    const [erase, undo, reset] = toolbar.children.slice(3);
    return {host, video, canvas, toggle, remove, toolbar, summary, saved, strength, radius, softness, erase, undo, reset};
}
function stroke(ui, finish = "pointerup") {
    ui.radius.value = "1"; ui.softness.value = "0";
    ui.canvas.fire("pointerdown", {button:0, pointerId:1, clientX:48, clientY:48});
    ui.canvas.fire("pointermove", {pointerId:1, clientX:112, clientY:48});
    assert.equal(ui.saved.length, 0, "do not serialize Plan during each pointer move");
    ui.canvas.fire(finish, {pointerId:1});
}
const ui = mount();
assert.equal(ui.saved.length, 0, "opening an existing Plan must not mark it dirty");
assert.equal(ui.toolbar.hidden, true);
ui.toggle.fire("click");
assert.equal(ui.toolbar.hidden, false);
assert.equal(ui.video.paused, true);
assert.equal(ui.video.controls, false);
stroke(ui);
assert.equal(ui.saved.length, 1);
assert.ok(ui.saved[0].cells.some(Boolean));
assert.ok(ui.canvas.rects.length > 0);
assert.equal(ui.video.muted, true);
const reloaded = mount(JSON.parse(JSON.stringify(ui.saved[0])));
assert.equal(reloaded.saved.length, 0);
assert.deepEqual(reloaded.canvas.rects, ui.canvas.rects);
assert.match(reloaded.toggle.textContent, /mask saved/);
ui.strength.value = "100"; ui.strength.fire("input");
assert.equal(ui.saved.length, 1);
ui.strength.fire("change");
assert.equal(ui.saved.at(-1).strength, 1);
ui.undo.fire("click");
assert.equal(ui.saved.at(-1), null);
ui.reset.fire("click");
assert.equal(ui.saved.at(-1), null);
ui.toggle.fire("click");
assert.equal(ui.video.controls, true);
assert.equal(ui.canvas.style.pointerEvents, "none");
for (const finish of ["pointercancel", "lostpointercapture"]) {
    const interrupted = mount(); interrupted.toggle.fire("click"); stroke(interrupted, finish);
    assert.equal(interrupted.saved.length, 1, "interrupted strokes still persist");
}
const disabled = mount(mask, false, false);
assert.equal(disabled.toggle.disabled, true);
assert.equal(disabled.remove.hidden, false);
disabled.remove.fire("click");
assert.deepEqual(disabled.saved, [null], "mask can be removed after switching out of AV mode, even without media");
const unloaded = mount(null, true, false);
assert.equal(unloaded.toggle.disabled, true);
Object.assign(unloaded.video, {videoWidth:320, videoHeight:160});
unloaded.video.fire("loadedmetadata");
assert.equal(unloaded.toggle.disabled, false);
console.log("context mask: brush/erase, quantization, timing, Plan/take restore, editor persistence and pointer lifecycle passed");
