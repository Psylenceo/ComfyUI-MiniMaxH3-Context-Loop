#!/usr/bin/env node
// Exercise nested graph discovery and actual scheduler reload hooks in memory.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import * as core from "../web/h3_lora_scheduler_core.mjs";

let nextLink = 1;
function graph(root = null) {
    const result = {
        _nodes:[], links:new Map(), inputs:[{name:"state", linkIds:[]}],
        outputs:[{name:"state", linkIds:[]}],
        getNodeById(id) { return this._nodes.find(node => node.id === id); },
    };
    result.rootGraph = root ?? result;
    return result;
}
function node(g, id, type) {
    const result = {id, type, graph:g, inputs:[], outputs:[],
        addInput(name, type) { this.inputs.push({name, type, link:null}); },
        removeInput(index) { this.inputs.splice(index, 1); },
    };
    g._nodes.push(result);
    return result;
}
function connect(g, originId, target, name, originSlot = 0) {
    const id = nextLink++;
    target.inputs.push({name, type:name === "state" ? "H3_CHAIN_STATE" : "MODEL", link:id});
    g.links.set(id, {origin_id:originId, origin_slot:originSlot,
        target_id:target.id, target_slot:target.inputs.length - 1});
    return id;
}
function pack(g, id) {
    const host = node(g, id, "subgraph");
    host.subgraph = graph(g.rootGraph);
    return host;
}
function railInput(host, target) {
    const id = connect(host.subgraph, -10, target, "state");
    host.subgraph.inputs[0].linkIds.push(id);
}
function railOutput(host, source) {
    const id = nextLink++;
    host.subgraph.links.set(id, {origin_id:source.id, origin_slot:0,
        target_id:-20, target_slot:0});
    host.subgraph.outputs[0].linkIds.push(id);
}
function lane(scheduler, route, loaderId) {
    const loader = node(scheduler.graph, loaderId, "LoraLoaderModelOnly");
    connect(scheduler.graph, loader.id, scheduler, "lora_" + route);
}

const root = graph();
const plan = node(root, 1, "MiniMaxH3ChainPlan");
const current = node(root, 2, "MiniMaxH3ChainCurrentShot");
connect(root, plan.id, current, "state");
const outer = pack(root, 10);
connect(root, current.id, outer, "state");
const inner = pack(outer.subgraph, 10);
railInput(outer, inner);
// IDs deliberately repeat in different graphs, as with copied subgraphs.
const scheduler = node(inner.subgraph, 1, core.LORA_SCHEDULER_NODE);
railInput(inner, scheduler);
lane(scheduler, "b", 2);
lane(scheduler, "z", 3);
const originalLinks = scheduler.inputs.map(input => input.link);
assert.deepEqual(core.availableLoRARoutes(root, [plan]), ["base", "b", "z"],
    "Plan discovers lanes through two nested state-input rails");

const otherPlan = node(root, 20, "MiniMaxH3ChainPlan");
const otherCurrent = node(root, 21, "MiniMaxH3ChainCurrentShot");
connect(root, otherPlan.id, otherCurrent, "state");
const otherPack = pack(root, 22);
connect(root, otherCurrent.id, otherPack, "state");
const otherScheduler = node(otherPack.subgraph, 1, core.LORA_SCHEDULER_NODE);
railInput(otherPack, otherScheduler);
lane(otherScheduler, "c", 2);
assert.deepEqual(core.availableLoRARoutes(root, [plan]), ["base", "b", "z"]);
assert.deepEqual(core.availableLoRARoutes(root, [otherPlan]), ["base", "c"],
    "Copied packs must not leak lanes to an unrelated Plan");

// A Plan/Current pair can itself be packed, with state passed out to a
// scheduler in the parent graph. Traverse the selected output rail, too.
const statePack = pack(root, 30);
const packedPlan = node(statePack.subgraph, 1, "MiniMaxH3ChainPlan");
const packedCurrent = node(statePack.subgraph, 2, "MiniMaxH3ChainCurrentShot");
connect(statePack.subgraph, packedPlan.id, packedCurrent, "state");
railOutput(statePack, packedCurrent);
const outsideScheduler = node(root, 31, core.LORA_SCHEDULER_NODE);
connect(root, statePack.id, outsideScheduler, "state");
lane(outsideScheduler, "a", 32);
assert.deepEqual(core.availableLoRARoutes(root, [packedPlan]), ["base", "a"]);
assert.deepEqual(core.availableLoRARoutes(root, [otherPlan]), ["base", "c"]);
assert.deepEqual(core.loraSchedulerNodes(root), [scheduler, otherScheduler, outsideScheduler]);

// Test the actual extension lifecycle, including old serialized input types.
const frontend = fs.readFileSync(new URL(
    "../web/h3_chain_lora_scheduler.js", import.meta.url,
), "utf8").replace(/^import[\s\S]*?;\s*/gm, "");
let extension;
const context = vm.createContext({...core,
    app:{graph:root, registerExtension:value => extension = value},
    setTimeout:callback => callback(),
    document:{dispatchEvent() {}},
    CustomEvent:class { constructor(type, options) { Object.assign(this, {type}, options); } },
});
vm.runInContext(frontend, context);
await extension.afterConfigureGraph();
const stateType = "H3_CHAIN_UPSCALE_STATE,H3_CHAIN_STATE";
for (const item of [scheduler, otherScheduler, outsideScheduler]) {
    assert.equal(item.inputs.find(input => input.name === "state").type, stateType);
    assert.equal(typeof item._h3LoRASchedulerRefresh, "function");
}
assert.deepEqual(scheduler.inputs.slice(0, 3).map(input => input.link), originalLinks,
    "Refreshing the socket must preserve all existing state and MODEL links");
assert.equal(scheduler.inputs.find(input => input.name === "lora_a").link, null,
    "Nested scheduler still reveals the next unused lane");
scheduler.inputs.find(input => input.name === "state").type = "H3_CHAIN_STATE";
await extension.afterConfigureGraph();
assert.equal(scheduler.inputs.find(input => input.name === "state").type, stateType,
    "Reloading a copied generation pack restores the processing-state socket");
assert.deepEqual(scheduler.inputs.slice(0, 3).map(input => input.link), originalLinks);

console.log("LoRA subgraphs: nested input/output rails, copied-pack isolation and reload sockets pass");
