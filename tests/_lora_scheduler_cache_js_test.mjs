#!/usr/bin/env -S node --experimental-vm-modules
// Run with: node --experimental-vm-modules tests/_lora_scheduler_cache_js_test.mjs
// Keep the real ES-module imports: stripping them hid the upgrade failure.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

assert.equal(typeof vm.SourceTextModule, "function",
    "Run this test with node --experimental-vm-modules");
const web = new URL("../web/", import.meta.url);
const read = name => fs.readFileSync(new URL(name, web), "utf8");
const base = "https://comfy.test/extensions/h3/";
const entryName = "h3_chain_lora_scheduler.js";
const coreName = "h3_lora_scheduler_core.mjs";
const previewName = "h3_reference_preview_core.mjs";
const coreImport = /\.\/h3_lora_scheduler_core\.mjs(?:\?v=[^"']+)?/g;
const previewImport = /\.\/h3_reference_preview_core\.mjs(?:\?v=[^"']+)?/g;

// Model pre-update browser cache entries by omitting the newly exported APIs.
const oldCore = read(coreName).replace(
    "export function loraSchedulerNodes", "function loraSchedulerNodes",
).replace(/^import[^\n]*inputSource[^\n]*\n/m, "");
const oldPreview = read(previewName).replace(
    "export function inputSource", "function inputSource",
);
const staleCache = new Map([
    [new URL(`${coreName}?v=0.7.0`, base).href, oldCore],
    [new URL(previewName, base).href, oldPreview],
    [new URL(`${previewName}?v=0.7.3`, base).href, oldPreview],
]);

function scheduler(id, connected) {
    return {
        id, type:"MiniMaxH3ChainLoRAScheduler", properties:{},
        inputs:[
            {name:"state", type:"H3_CHAIN_STATE", link:100},
            {name:"base_model", type:"MODEL", link:101},
            ...[..."abcdefghijklmnopqrstuvwxyz"].map((route, index) => ({
                name:`lora_${route}`, type:"MODEL",
                link:connected.includes(route) ? index + 102 : null,
            })),
        ],
        addInput(name, type) { this.inputs.push({name, type, link:null}); },
        removeInput(index) {
            assert.equal(this.inputs[index].link, null,
                "Never remove a connected lane during cleanup");
            this.inputs.splice(index, 1);
        },
    };
}

async function load({cached = true, oldCoreUrl = false, oldPreviewUrl = false} = {}) {
    const top = scheduler(2024, ["a", "b", "c", "d"]);
    const nested = scheduler(2024, ["b", "z"]);
    const graph = {_nodes:[top, {subgraph:{_nodes:[nested]}}]};
    const timers = [];
    const registered = [];
    const app = {graph, registerExtension:extension => registered.push(extension)};
    const context = vm.createContext({
        setTimeout:callback => timers.push(callback),
        document:{dispatchEvent() {}},
        CustomEvent:class { constructor(type, options) { Object.assign(this, {type}, options); } },
    });
    const modules = new Map();
    function moduleFor(url) {
        if (modules.has(url)) return modules.get(url);
        let module;
        if (url === "https://comfy.test/scripts/app.js") {
            module = new vm.SyntheticModule(["app"], function () {
                this.setExport("app", app);
            }, {context, identifier:url});
        } else {
            assert.ok(url.startsWith(base), `Unexpected module ${url}`);
            const name = new URL(url).pathname.split("/").at(-1);
            let source = cached && staleCache.has(url) ? staleCache.get(url) : read(name);
            // Negative controls reproduce either half of the old release bug.
            if (name === entryName && oldCoreUrl) {
                source = source.replace(coreImport, `./${coreName}?v=0.7.0`);
            }
            if (name === coreName && oldPreviewUrl) {
                source = source.replace(previewImport, `./${previewName}`);
            }
            module = new vm.SourceTextModule(source, {context, identifier:url});
        }
        modules.set(url, module);
        return module;
    }
    const entry = moduleFor(new URL(entryName, base).href);
    await entry.link((specifier, parent) => moduleFor(new URL(specifier, parent.identifier).href));
    await entry.evaluate();
    assert.equal(registered.length, 1, "Scheduler extension must register after an update");
    const extension = registered[0];
    const links = node => node.inputs.filter(input => input.link != null)
        .map(({name, link}) => ({name, link}));
    const linksBefore = [top, nested].map(links);
    function flush() {
        let count = 0;
        while (timers.length) {
            assert.ok(++count < 100, "Cleanup must settle without endless retries");
            timers.shift()();
        }
    }
    await extension.afterConfigureGraph();
    flush();
    assert.deepEqual(top.inputs.map(input => input.name), [
        "state", "base_model", "lora_a", "lora_b", "lora_c", "lora_d", "lora_e",
    ], "A-D plus the next E socket; no unused F-Z sockets");
    assert.equal(top.inputs.at(-1).label, "Connect next LoRA route · E");
    assert.deepEqual(nested.inputs.map(input => input.name), [
        "state", "base_model", "lora_a", "lora_b", "lora_z",
    ], "Nested scheduler also cleans up without losing non-contiguous routes");
    // Restoring or repeatedly configuring the graph must preserve every wire.
    await extension.afterConfigureGraph();
    flush();
    for (const [index, node] of [top, nested].entries()) {
        assert.deepEqual(links(node), linksBefore[index]);
        assert.equal(node.inputs[0].type, "H3_CHAIN_UPSCALE_STATE,H3_CHAIN_STATE");
    }
    // Recreate instances from serialized inputs, as on workflow reload.
    const restored = [top, nested].map(node => Object.assign(scheduler(node.id, []), {
        inputs:JSON.parse(JSON.stringify(node.inputs)),
        properties:JSON.parse(JSON.stringify(node.properties)),
    }));
    graph._nodes = [restored[0], {subgraph:{_nodes:[restored[1]]}}];
    await extension.afterConfigureGraph();
    flush();
    for (const [index, node] of restored.entries()) {
        assert.deepEqual(links(node), linksBefore[index]);
        assert.equal(node.inputs.length, index === 0 ? 7 : 5);
    }
    const nextInput = restored[0].inputs.at(-1);
    nextInput.link = 200;
    restored[0].onConnectionsChange();
    flush();
    assert.equal(nextInput.label, "LoRA E");
    assert.equal(restored[0].inputs.at(-1).name, "lora_f");
    assert.equal(restored[0].inputs.length, 8, "Connecting E exposes only the next F lane");
    return modules;
}

await load({cached:false});
const upgraded = await load();
await assert.rejects(load({oldCoreUrl:true}), /does not provide an export named 'loraSchedulerNodes'/);
await assert.rejects(load({oldPreviewUrl:true}), /does not provide an export named 'inputSource'/);
for (const staleUrl of staleCache.keys()) {
    assert.equal(upgraded.has(staleUrl), false, `Upgrade must bypass ${staleUrl}`);
}

// Plan, Studio and prompt editors must share the same fresh helper URLs.
for (const [pattern, minimum] of [[coreImport, "0.7.25"], [previewImport, "0.7.25"]]) {
    const urls = new Set();
    for (const name of fs.readdirSync(web).filter(name => /\.(?:mjs|js)$/.test(name))) {
        for (const match of read(name).matchAll(pattern)) urls.add(match[0]);
    }
    assert.equal(urls.size, 1, "All consumers must agree on the helper cache key");
    const version = new URL([...urls][0], base).searchParams.get("v");
    assert.ok(version?.localeCompare(minimum, undefined, {numeric:true}) >= 0,
        `Expected cache version ${minimum} or newer, got ${version}`);
}

console.log("LoRA cached upgrade: real imports, both stale-dependency controls, socket cleanup and nested reload pass");
