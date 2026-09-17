#!/usr/bin/env node

// Run the actual triangle drag handler without a browser or project writes.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import {
    studioLatentSafeOutFrames,
    studioNearestLatentSafeOutFrame,
} from "../web/h3_chain_plan_studio_core.mjs";

const source = fs.readFileSync(new URL(
    "../web/h3_chain_plan_studio.js", import.meta.url,
), "utf8");
const handler = source.match(
    /^    function enableSceneLatentTrimDrag\([^]*?^    }$/m,
);
assert.ok(handler, "Missing production trim handler");

function fixture({raw = 124, delivered = raw, out = delivered,
    width = 144, zoom = .5, locked = false, inlineWidth = true} = {}) {
    const listeners = new Map();
    const props = new Map([["--h3-scene-width", inlineWidth ? `${width}px` : ""]]);
    const row = {rawFrames:raw, deliveredFrames:delivered};
    const saves = [];
    const state = {timelineDragging:false,
        timelineContent:{dataset:{timelineWidth:String(width * 2)}}};
    const card = {
        offsetWidth:width,
        getBoundingClientRect:() => ({width:width * zoom}),
        style:{
            getPropertyValue:key => props.get(key) ?? "",
            setProperty:(key, value) => props.set(key, value),
        },
    };
    const handle = {
        addEventListener:(type, fn) => listeners.set(type, fn),
        removeEventListener:type => listeners.delete(type),
        setPointerCapture() {}, releasePointerCapture() {},
    };
    const context = vm.createContext({
        state, FPS:24, studioLatentSafeOutFrames, studioNearestLatentSafeOutFrame,
        timing:() => ({shots:[row]}),
        // Keep the old handler's timeline dependencies available so this
        // regression fails on incorrect drag results, not a missing stub.
        timelineModel:() => ({totalSeconds:out * 2 / 24}),
        trimForScene:() => out === delivered ? null : {out_frame:out},
        sceneLocked:() => locked,
        setSceneTrim:(index, frame) => saves.push({index, frame}),
    });
    vm.runInContext(handler[0], context);
    context.enableSceneLatentTrimDrag(card, handle, 0);
    return {
        saves, state, row, handle, listeners,
        width:() => props.get("--h3-scene-width"),
        fire(type, clientX = 200, button = 0) {
            listeners.get(type)?.({type, clientX, button, pointerId:1,
                preventDefault() {}, stopPropagation() {}});
        },
    };
}

for (const scene of [
    {raw:56, delivered:56, out:56, expected:30},
    {raw:124, delivered:124, out:124, expected:60},
    {raw:362, delivered:362, out:362, expected:183},
    {raw:362, delivered:340, out:340, expected:174},
    {raw:362, delivered:340, out:102, expected:51},
]) {
    for (const zoom of [.37, .5, 1, 2]) {
        for (const width of [144, 432]) {
            const f = fixture({...scene, zoom, width});
            f.fire("pointerdown");
            assert.equal(f.state.timelineDragging, true);
            f.fire("pointermove", 200 - width * zoom / 2);
            assert.equal(f.saves.length, 0, "Only save on release");
            assert.ok(Math.abs(parseFloat(f.width())
                - width * scene.expected / scene.out) < 1e-9,
            `Preview width for ${scene.out}f at ${zoom} zoom: ${f.width()}`);
            f.fire("pointerup", 200 - width * zoom / 2);
            assert.deepEqual(f.saves, [{index:0, frame:scene.expected}],
                `Halfway trim: ${scene.out}f at ${zoom} zoom, ${width}px width`);
            assert.equal(f.width(), `${width}px`, "Restore layout pixels, not screen pixels");
            assert.equal(f.state.timelineDragging, false);
            assert.equal(f.listeners.has("pointermove"), false);
            assert.deepEqual(f.row, {rawFrames:scene.raw, deliveredFrames:scene.delivered},
                "Trimming must not change generation length");
        }
    }
}

// No-move clicks and snapped-back drags must not shrink the card or save.
for (const delta of [0, 1, 3]) {
    const f = fixture({width:800});
    f.fire("pointerdown");
    f.fire("pointermove", 200 - delta);
    f.fire("pointerup", 200 - delta);
    assert.deepEqual(f.saves, []);
    assert.equal(f.width(), "800px");
}
{
    const f = fixture();
    f.fire("pointerdown");
    f.fire("pointermove", 164);
    f.fire("pointercancel", 164);
    assert.deepEqual(f.saves, [], "Cancel must not persist a trim");
    assert.equal(f.width(), "144px");
    assert.equal(f.state.timelineDragging, false);
    assert.equal(f.listeners.has("pointerup"), false);
    f.fire("dblclick");
    assert.deepEqual(f.saves, [{index:0, frame:124}], "Double-click restores full length");
}
{
    const f = fixture({raw:362, delivered:340, out:72});
    f.fire("pointerdown"); f.fire("pointermove", 2000); f.fire("pointerup", 2000);
    assert.deepEqual(f.saves, [{index:0, frame:340}], "Cannot extend past the full clip");
}
{
    const f = fixture({inlineWidth:false});
    f.fire("pointerdown"); f.fire("pointermove", 164); f.fire("pointerup", 164);
    assert.deepEqual(f.saves, [{index:0, frame:60}], "Fall back to offsetWidth");
    assert.equal(f.width(), "", "Preserve an unset inline width");
}
for (const [locked, button] of [[true, 0], [false, 2]]) {
    const f = fixture({locked});
    f.fire("pointerdown", 200, button);
    f.fire("pointermove", 164); f.fire("pointerup", 164);
    if (locked) f.fire("dblclick");
    assert.deepEqual(f.saves, []);
    assert.equal(f.width(), "144px");
    assert.equal(f.state.timelineDragging, false);
}
assert.deepEqual(studioLatentSafeOutFrames(124, 124),
    [9, 18, 30, 39, 51, 60, 69, 81, 90, 102, 111, 120, 124],
    "Keep the existing latent-safe boundaries, including endpoints beyond 55f");

console.log("Plan Studio trim: zoom, lengths, existing trims, preview, cancel, reset and locks pass");
