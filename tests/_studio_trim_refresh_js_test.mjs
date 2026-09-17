import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import {
    matchingStudioCheckpoint, studioCheckpointSignature,
} from "../web/h3_chain_plan_studio_core.mjs";

const source = fs.readFileSync(new URL(
    "../web/h3_chain_plan_studio.js", import.meta.url,
), "utf8");
const nightly = source.includes("function enableSceneSlipDrag(");
const handlers = [
    "refreshCheckpointsNow", "refreshTimelineCheckpoints",
    "updateTimelineCheckpointCard", "syncTimelineTrimControls",
    "refreshSceneTrimControls",
].map(name => {
    const match = source.match(new RegExp(
        `^    (?:async )?function ${name}\\([^]*?^    }$`, "m",
    ));
    assert.ok(match, name);
    return match[0];
}).join("\n");

class Element {
    constructor(tag, className = "") {
        this.tag = tag;
        this.classes = new Set(className.split(" ").filter(Boolean));
        this.classList = {
            add: name => this.classes.add(name),
            contains: name => this.classes.has(name),
            toggle: (name, on) => on ? this.classes.add(name) : this.classes.delete(name),
        };
        this.dataset = {};
        this.children = [];
        this.listeners = new Map();
        this.disabled = false;
    }
    append(...children) {
        for (const child of children) {
            child.parent = this;
            this.children.push(child);
        }
    }
    prepend(child) {
        child.parent = this;
        this.children.unshift(child);
    }
    querySelector(selector) {
        for (const child of this.children) {
            if (child.classes.has(selector.slice(1))) return child;
            const nested = child.querySelector(selector);
            if (nested) return nested;
        }
        return null;
    }
    replaceWith(replacement) {
        const parent = this.parent;
        const index = parent.children.indexOf(this);
        parent.children[index] = replacement;
        replacement.parent = parent;
        this.parent = null;
    }
    remove() {
        if (!this.parent) return;
        this.parent.children.splice(this.parent.children.indexOf(this), 1);
        this.parent = null;
    }
    addEventListener(type, callback) {
        const listeners = this.listeners.get(type) ?? [];
        listeners.push(callback);
        this.listeners.set(type, listeners);
    }
    fire(type) {
        for (const callback of this.listeners.get(type) ?? []) callback();
    }
}

function fixture({locked = false, editorialChanged = false} = {}) {
    const row = {id:"scene_1", rawFrames:124, deliveredFrames:124};
    const record = {scene:1, scene_id:row.id, raw_frames:124,
        delivered_frames:124, ready:true, revision:"saved", video:"clip.mp4"};
    const card = new Element("button", "h3studio-card");
    card.dataset.sceneIndex = "0";
    card.append(new Element("span", "h3studio-card-copy"));
    const panel = new Element("div");
    const prompt = new Element("textarea");
    prompt.value = "Unsaved prompt text";
    prompt.selectionStart = 8;
    const usedEnd = new Element("select", "h3studio-used-end");
    usedEnd.value = "124";
    const reset = new Element("button", "h3studio-reset-used-end");
    panel.append(prompt, usedEnd, reset);
    const viewport = {scrollLeft:420};
    const state = {
        disposed:false, checkpoints:new Map(), checkpointSignature:"",
        checkpointError:"", checkpointToken:0, editorialEditEpoch:0,
        plan:{shots:[row]}, active:0, view:"scene", panelHost:panel,
        timelineHost:{querySelectorAll:() => [card]}, timelineViewport:viewport,
    };
    const stats = {panelRenders:0, timelineRenders:0, calls:0, actions:[]};
    let response = {checkpoints:[]};
    let run = "trim-refresh-test";
    let trim = null;
    const context = vm.createContext({
        state, Map, URLSearchParams, studioCheckpointSignature, matchingStudioCheckpoint,
        runName:() => run, currentBranch:() => "main",
        timing:() => ({shots:[row]}),
        sceneLocked:() => locked, trimForScene:() => trim,
        api:{fetchApi:async (_url, options) => {
            assert.equal(options.cache, "no-store");
            stats.calls++;
            return {ok:true, json:async () => response};
        }},
        applyEditorialPayload:() => editorialChanged,
        cacheStudioPresentation(){}, renderStatus(){},
        renderTimeline() {
            stats.timelineRenders++;
            card.querySelector(".h3studio-resize-handle")?.remove();
            card.querySelector(".h3studio-slip-handle")?.remove();
            context.syncTimelineTrimControls(card, 0,
                matchingStudioCheckpoint(state.checkpoints, 0, row));
        },
        renderPanel() { stats.panelRenders++; },
        checkpointThumbnailUrl:(_index, checkpoint) => checkpoint?.video ?? "",
        element:(tag, className) => new Element(tag, className),
        enableSceneLatentTrimDrag(_card, handle) {
            handle.addEventListener("pointerdown", () => stats.actions.push("trim"));
        },
        enableSceneDurationDrag(_card, handle) {
            handle.addEventListener("pointerdown", () => stats.actions.push("duration"));
        },
        enableSceneSlipDrag(_card, handle) {
            handle.addEventListener("pointerdown", () => stats.actions.push("slip"));
        },
    });
    vm.runInContext(handlers, context);
    context.syncTimelineTrimControls(card, 0, null);
    context.refreshSceneTrimControls(usedEnd, reset);
    return {
        state, record, card, panel, prompt, usedEnd, reset, stats, context,
        handle:() => card.querySelector(".h3studio-resize-handle"),
        slip:() => card.querySelector(".h3studio-slip-handle"),
        setTrim(value) { trim = value; },
        async poll(records) {
            if (records) response = {checkpoints:records};
            await context.refreshCheckpointsNow();
        },
        async clearRun() { run = ""; await context.refreshCheckpointsNow(); },
        assertEditorPreserved() {
            assert.equal(state.panelHost, panel);
            assert.equal(panel.children[0], prompt);
            assert.equal(prompt.value, "Unsaved prompt text");
            assert.equal(prompt.selectionStart, 8);
            assert.equal(viewport.scrollLeft, 420);
            assert.equal(stats.panelRenders, 0);
        },
    };
}

// The actual refresh path must promote a draft duration handle to a trim
// handle and enable the already-open dropdown without rebuilding its editor.
for (const editorialChanged of [false, true]) {
    const f = fixture({editorialChanged});
    assert.equal(f.usedEnd.disabled, true);
    assert.equal(f.handle().classes.has("h3studio-latent-trim"), false);
    await f.poll([f.record]);
    assert.equal(f.usedEnd.disabled, false);
    assert.equal(f.reset.disabled, true, "Full checkpoint needs no reset");
    assert.equal(f.handle().classes.has("h3studio-latent-trim"), true);
    assert.equal(Boolean(f.slip()), nightly);
    f.handle().fire("pointerdown");
    assert.deepEqual(f.stats.actions, ["trim"], "No stale duration-edit listener");
    f.assertEditorPreserved();
    if (!editorialChanged) {
        assert.equal(f.stats.timelineRenders, 0, "Readiness alone does not rebuild timeline");
        const handle = f.handle(), slip = f.slip();
        await f.poll();
        await f.poll([{...f.record, revision:"reattributed", video:"new-preview.mp4"}]);
        assert.equal(f.handle(), handle, "Unchanged mode preserves handle/listeners");
        assert.equal(f.slip(), slip);
        assert.equal(handle.listeners.get("pointerdown").length, 1);
    }
    f.setTrim({out_frame:60});
    f.context.refreshSceneTrimControls();
    assert.equal(f.reset.disabled, false, "A trimmed ready scene can restore full length");
    await f.poll([]);
    assert.equal(f.usedEnd.disabled, true);
    assert.equal(f.reset.disabled, true);
    assert.equal(f.handle().classes.has("h3studio-latent-trim"), false);
    assert.equal(f.slip(), null);
    f.handle().fire("pointerdown");
    assert.deepEqual(f.stats.actions, ["trim", "duration"]);
    f.assertEditorPreserved();
}

// Do not turn missing, unready, or mismatched saved scenes into editable media.
for (const mismatch of [
    {ready:false}, {scene_id:"other"}, {delivered_frames:102},
]) {
    const f = fixture();
    await f.poll([{...f.record, ...mismatch}]);
    assert.equal(f.usedEnd.disabled, true);
    assert.equal(f.handle().classes.has("h3studio-latent-trim"), false);
    assert.equal(f.slip(), null);
}
{
    const f = fixture({locked:true});
    await f.poll([f.record]);
    assert.equal(f.usedEnd.disabled, true);
    assert.equal(f.reset.disabled, true);
    assert.match(f.usedEnd.title, /Scene locked/);
}
{
    const f = fixture();
    await f.poll([f.record]);
    await f.clearRun();
    assert.equal(f.usedEnd.disabled, true);
    assert.equal(f.handle().classes.has("h3studio-latent-trim"), false);
    assert.equal(f.slip(), null);
    f.assertEditorPreserved();
}

// Clearing the Run during a drag also retries after release.
{
    const f = fixture();
    await f.poll([f.record]);
    const original = f.handle();
    f.state.timelineDragging = true;
    await f.clearRun();
    assert.equal(f.handle(), original);
    f.state.timelineDragging = false;
    await f.clearRun();
    assert.equal(f.handle().classes.has("h3studio-latent-trim"), false);
    assert.equal(f.state.trimRefreshPending, false);
}

// A readiness transition during a drag must not discard pointer capture.
// The unchanged next poll still applies the deferred mode change.
for (const readyBefore of [false, true]) {
    const f = fixture();
    if (readyBefore) await f.poll([f.record]);
    const original = f.handle();
    f.state.timelineDragging = true;
    await f.poll(readyBefore ? [] : [f.record]);
    assert.equal(f.handle(), original);
    assert.equal(f.state.trimRefreshPending, true);
    f.state.timelineDragging = false;
    await f.poll();
    assert.notEqual(f.handle(), original);
    assert.equal(f.handle().classes.has("h3studio-latent-trim"), !readyBefore);
    assert.equal(f.state.trimRefreshPending, false);
    assert.equal(f.stats.timelineRenders, 0);
    f.assertEditorPreserved();
}

// Keep the initial render and the async refresh on the same control setup.
assert.match(source, /const usedEnd = element\("select", "h3studio-used-end"\)/);
assert.match(source, /resetUsedEnd.classList.add\("h3studio-reset-used-end"\)/);
assert.match(source, /refreshSceneTrimControls\(usedEnd, resetUsedEnd\)/);
const renderTimeline = source.match(/^    function renderTimeline\([^]*?^    }$/m)[0];
assert.match(renderTimeline, /syncTimelineTrimControls\(card, index, checkpoint\)/);
console.log("Studio trim refresh: async readiness, removal, locks, mismatches, drag deferral, stable handlers and editor preservation pass");
