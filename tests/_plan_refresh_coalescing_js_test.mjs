#!/usr/bin/env node
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import {coalescedRefresh} from "../web/h3_coalesced_refresh.mjs";

function clock() {
    const timers = new Map();
    let id = 0;
    return {
        timers,
        setTimer:callback => { timers.set(++id, callback); return id; },
        clearTimer:timer => timers.delete(timer),
        tick() {
            const pending = [...timers];
            for (const [key, callback] of pending) {
                if (!timers.delete(key)) continue;
                callback();
            }
        },
    };
}

const timer = clock();
let configuring = true, alive = true;
const calls = [];
const refresh = coalescedRefresh(full => calls.push(full), {
    ...timer, isConfiguring:() => configuring, isAlive:() => alive,
});
for (let i = 0; i < 100; i++) refresh(i === 25);
assert.equal(timer.timers.size, 1, "one scheduled task for a connection storm");
for (let i = 0; i < 3; i++) timer.tick();
assert.deepEqual(calls, [], "wait for asynchronous graph restoration to finish");
assert.equal(timer.timers.size, 1);
configuring = false;
timer.tick();
assert.deepEqual(calls, [true], "the strongest request survives later light refreshes");
assert.equal(timer.timers.size, 0, "no idle/background polling");
refresh(); timer.tick();
assert.deepEqual(calls, [true, false], "later interactive edits still refresh");
refresh(true); refresh.cancel(); timer.tick();
assert.equal(calls.length, 2, "removal cancels the pending rebuild");
refresh(); timer.tick();
assert.equal(calls.at(-1), false, "cancel clears the saved reload flag");
alive = false; refresh(true); timer.tick();
assert.equal(calls.length, 3, "already queued callbacks cannot render detached nodes");
alive = true; refresh(); timer.tick();
assert.equal(calls.length, 4, "the scheduler can be reused after a node is re-added");

let secondCalls = 0;
const other = coalescedRefresh(() => secondCalls++, timer);
refresh(true); other(); timer.tick();
assert.equal(secondCalls, 1, "each node owns an independent pending refresh");
const reentrant = coalescedRefresh(() => {
    secondCalls++;
    if (secondCalls === 2) reentrant();
}, timer);
reentrant(); timer.tick(); timer.tick();
assert.equal(secondCalls, 3, "changes during a flush are not lost");

// Execute the real editor wiring with a tiny fake DOM/render boundary. This
// tests the scheduling code used by restoration, branch reload and socket hooks,
// rather than merely asserting that the source imports a debounce helper.
const editorSource = fs.readFileSync(new URL("../web/h3_chain_plan_editor.js", import.meta.url), "utf8");
const editorClock = clock(), app = {configuringGraph:true};
const node = {graph:{}}, state = {};
let savedPlan = {prompt:"saved prompt", seed:"2948817042231741711"};
const loaded = []; let renders = 0, synchronized = 0;
const editorContext = vm.createContext({
    app, node, state, planWidget:{}, modern:true,
    coalescedRefresh:(callback, options) => coalescedRefresh(callback, {...editorClock, ...options}),
    collapseWidget(){}, collapseModernBackingWidgets(){},
    planLayout:() => ({advanced:true, jsonOpen:false, settingsOpen:false}),
    syncProjectAssetManagedWidgets:() => synchronized++,
    loadFromWidget:force => { assert.equal(force, true); loaded.push(structuredClone(savedPlan)); renders++; },
    render:() => renders++, scheduleResponsiveSize(){}, applyResponsiveSize(){},
});
vm.runInContext(editorSource.slice(editorSource.indexOf("    const refreshEditor ="),
    editorSource.indexOf("    const onLoRARoutesChanged =")), editorContext);
node._h3ChainEditorRefresh();
for (let i = 0; i < 40; i++) node._h3ChainEditorConnectionRefresh();
editorClock.tick();
assert.equal(renders, 0);
savedPlan = {prompt:"final restored prompt", seed:"18446744073709551615"};
app.configuringGraph = false; editorClock.tick();
assert.equal(renders, 1);
assert.equal(synchronized, 1);
assert.deepEqual(loaded, [savedPlan], "read the final widgets; never capture an intermediate Plan or seed");
assert.deepEqual(state, {advanced:true, jsonOpen:false, settingsOpen:false});
node._h3ChainEditorConnectionRefresh(); editorClock.tick();
assert.equal(renders, 2);
assert.equal(loaded.length, 1, "a normal socket refresh does not reload prompt text");
node._h3ChainEditorRefresh(); editorClock.tick();
assert.equal(loaded.length, 2, "explicit saved-branch reload still reads the Plan");
assert.match(editorSource, /node\.onRemoved = function \(\) \{\s+refreshEditor\.cancel\(\);/);

const studioSource = fs.readFileSync(new URL("../web/h3_chain_plan_studio.js", import.meta.url), "utf8");
const studioClock = clock(), studioState = {disposed:false};
let studioLoads = 0, published = 0, nativeConnections = 0;
const studioNode = {graph:{}, onConnectionsChange() { nativeConnections++; return "native"; }};
const studioContext = vm.createContext({
    app, node:studioNode, state:studioState,
    coalescedRefresh:(callback, options) => coalescedRefresh(callback, {...studioClock, ...options}),
    loadPlan:force => { assert.equal(force, true); studioLoads++; },
    publishActiveScene:() => published++,
});
vm.runInContext(studioSource.slice(studioSource.indexOf("    const refreshStudio ="),
    studioSource.indexOf("    const onPromptExecuted =")), studioContext);
vm.runInContext(studioSource.match(/    node\._h3PlanStudioRefresh = [^\n]+/)[0], studioContext);
app.configuringGraph = true;
studioNode._h3PlanStudioRefresh();
for (let i = 0; i < 40; i++) assert.equal(studioNode.onConnectionsChange(), "native");
studioClock.tick(); assert.equal(studioLoads, 0);
app.configuringGraph = false; studioClock.tick();
assert.equal(nativeConnections, 40, "preserve all host connection callbacks");
assert.equal(studioLoads, 1); assert.equal(published, 1);
studioNode.onConnectionsChange(); studioClock.tick();
assert.equal(studioLoads, 2, "interactive rewiring refreshes the selected scene");
studioNode._h3PlanStudioRefresh(); studioState.disposed = true; studioClock.tick();
assert.equal(studioLoads, 2, "no stale publish/render after switching workflow tabs");
assert.match(studioSource, /state\.disposed = true;\s+refreshStudio\.cancel\(\);/);
assert.match(studioSource, /state\.pollTimer = setInterval\(\(\) => \{\s+if \(app\.configuringGraph\) return;/);
console.log("Plan refresh batching: restoration, live edits, saved values, strongest request, removal and isolation pass");
