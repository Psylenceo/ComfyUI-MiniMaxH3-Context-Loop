import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import {
    connectedProjectAssetPlans, projectAssetManagers,
    syncManagedPlanRunName, syncProjectAssetPlanRun,
} from "../web/h3_project_asset_sync_core.mjs";
import {inputSource as resolvedInputSource, nodeType} from "../web/h3_reference_preview_core.mjs";

const read = name => fs.readFileSync(new URL("../web/" + name, import.meta.url), "utf8");
const carouselSource = read("h3_project_asset_manager.js");
const runManagerSource = read("h3_chain_run_manager.js");
const studioSource = read("h3_chain_plan_studio.js");
function handler(source, name, indent = "") {
    const match = source.match(new RegExp(`^${indent}(?:async )?function ${name}\\([^]*?^${indent}}$`, "m"));
    assert.ok(match, name);
    return match[0];
}

const widget = (node, name) => node?.widgets?.find(item => item.name === name);
function graph(map = false) {
    return {_nodes:[], links:map ? new Map() : {}, setDirtyCanvas(){},
        getNodeById(id) { return this._nodes.find(node => String(node.id) === String(id)); }};
}
function add(graph, type, id, values = {}) {
    const node = {graph, type, id, inputs:[], outputs:[],
        widgets:Object.entries(values).map(([name, value]) => ({name, value}))};
    graph._nodes.push(node);
    return node;
}
let nextLink = 1;
function connect(source, target, name, sourceSlot = 0) {
    const id = nextLink++, graph = source.graph;
    while (source.outputs.length <= sourceSlot) source.outputs.push({links:[]});
    const targetSlot = target.inputs.length;
    target.inputs.push({name, link:id});
    source.outputs[sourceSlot].links.push(id);
    const link = {origin_id:source.id, origin_slot:sourceSlot, target_id:target.id, target_slot:targetSlot};
    if (graph.links instanceof Map) graph.links.set(id, link); else graph.links[id] = link;
    return id;
}
function subgraph(root, host) {
    const child = graph(root.links instanceof Map);
    child.rootGraph = root;
    host.subgraph = child;
    child.inputs = []; child.outputs = [];
    return child;
}

for (const map of [false, true]) {
    const root = graph(map);
    const carousel = add(root, "MiniMaxH3ProjectAssetManager", 1, {run_name:"bob"});
    const modern = add(root, "MiniMaxH3ChainPlanModern", 2, {run_name:"h3_chain", plan_json:'{"shots":[{"prompt":"Keep me"}]}'});
    const oldManager = add(root, "MiniMaxH3ChainRunManager", 3);
    const studio = add(root, "MiniMaxH3ChainPlanStudio", 4, {run_name:"h3_chain"});
    const unrelated = add(root, "MiniMaxH3ChainPlan", 5, {run_name:"unrelated"});
    connect(carousel, modern, "project_assets");
    connect(modern, oldManager, "plan");
    connect(oldManager, studio, "plan");
    // Sharing a different socket must not make a Plan owned by this Carousel.
    connect(carousel, unrelated, "references");
    let notifications = 0, paints = 0;
    widget(modern, "run_name").callback = () => notifications++;
    modern._h3ChainEditorConnectionRefresh = () => paints++;
    assert.deepEqual(connectedProjectAssetPlans(carousel), [modern]);
    assert.deepEqual(syncProjectAssetPlanRun(carousel, "bob"), [modern]);
    assert.equal(widget(modern, "run_name").value, "bob");
    assert.equal(widget(unrelated, "run_name").value, "unrelated");
    assert.equal(widget(modern, "plan_json").value, '{"shots":[{"prompt":"Keep me"}]}');
    assert.equal(paints, 1, "Modern Plan's custom field must be repainted");
    assert.equal(notifications, 1, "Old Run Manager's backing-widget watcher must be notified");
    syncProjectAssetPlanRun(carousel, "bob");
    assert.equal(notifications, 1, "Unchanged catalogs must not trigger editor rebuilds");
    assert.equal(paints, 1);

    // Use the actual legacy Run Manager and Studio graph readers from the report.
    for (const source of [runManagerSource, studioSource]) {
        const context = vm.createContext({resolvedInputSource, nodeType,
            PLAN_NAMES:new Set(["MiniMaxH3ChainPlan", "MiniMaxH3ChainPlanModern"])});
        vm.runInContext(handler(source, "upstreamPlanNode"), context);
        assert.equal(context.upstreamPlanNode(source === runManagerSource ? oldManager : studio), modern);
    }
    const context = vm.createContext({node:oldManager, nodeType, resolvedInputSource,
        PLAN_NAMES:new Set(["MiniMaxH3ChainPlan", "MiniMaxH3ChainPlanModern"]),
        syncManagedPlanRunName, widgetByName:widget});
    vm.runInContext(handler(runManagerSource, "upstreamPlanNode") + "\n"
        + handler(runManagerSource, "activeRunName", "    "), context);
    // Recreated/restored nodes still carry the old default in the screenshot.
    widget(modern, "run_name").value = "h3_chain";
    assert.equal(context.activeRunName(), "bob");
    assert.equal(widget(modern, "run_name").value, "bob");

    const reload = vm.createContext({node:carousel, connectedProjectAssetPlans, widget,
        syncProjectAssetPlanRun, project:() => widget(carousel, "run_name").value});
    vm.runInContext(handler(carouselSource, "syncDownstreamPlan") + "\n"
        + handler(carouselSource, "downstreamPlanRunName") + "\n"
        + handler(carouselSource, "adoptConnectedRunName", "    "), reload);
    widget(modern, "run_name").value = "h3_chain";
    assert.equal(reload.adoptConnectedRunName(), false);
    assert.equal(widget(modern, "run_name").value, "bob", "A named Carousel must resync before catalog fetch/execution");

    const second = add(root, "MiniMaxH3ProjectAssetTree", 6, {run_name:"other"});
    const relay = add(root, "Reroute (rgthree)", 7);
    connect(second, relay, "input");
    connect(relay, modern, "unused");
    // Replace only the project_assets link (its old output list may be stale).
    modern.inputs.find(item => item.name === "project_assets").link = modern.inputs.find(item => item.name === "unused").link;
    assert.deepEqual(connectedProjectAssetPlans(carousel), []);
    syncProjectAssetPlanRun(carousel, "do_not_leak");
    assert.equal(widget(modern, "run_name").value, "bob");
    syncManagedPlanRunName(modern);
    assert.equal(widget(modern, "run_name").value, "other");
    assert.deepEqual(projectAssetManagers(root), [carousel, second]);
}

for (const type of ["Reroute", "Reroute (rgthree)", "SetNode"]) {
    const root = graph(true);
    const carousel = add(root, "MiniMaxH3ProjectAssetManager", 1, {run_name:"rerouted"});
    const relay = add(root, type, 2, {name:"assets"});
    const plan = add(root, "MiniMaxH3ChainPlan", 3, {run_name:"old"});
    connect(carousel, relay, "input");
    const output = type === "SetNode" ? add(root, "GetNode", 4, {name:"assets"}) : relay;
    connect(output, plan, "project_assets");
    syncProjectAssetPlanRun(carousel, "rerouted");
    assert.equal(widget(plan, "run_name").value, "rerouted", type);
}

{
    const root = graph(true);
    const carousel = add(root, "MiniMaxH3ProjectAssetManager", 1, {run_name:"nested"});
    const host = add(root, "NativeSubgraph", 2);
    const child = subgraph(root, host);
    const inside = add(child, "MiniMaxH3ChainPlanModern", 1, {run_name:"old"});
    connect(carousel, host, "assets");
    connect({id:-10, graph:child, outputs:[]}, inside, "project_assets");
    assert.deepEqual(connectedProjectAssetPlans(carousel), [inside]);
    syncProjectAssetPlanRun(carousel, "nested");
    assert.equal(widget(inside, "run_name").value, "nested");
    // The reverse boundary: an internal Carousel drives a Plan outside.
    const innerCarousel = add(child, "MiniMaxH3ProjectAssetTree", 3, {run_name:"inside"});
    connect(innerCarousel, {id:-20, graph:child, inputs:[]}, "assets");
    const outside = add(root, "MiniMaxH3ChainPlan", 4, {run_name:"old"});
    connect(host, outside, "project_assets");
    syncProjectAssetPlanRun(innerCarousel, "inside");
    assert.equal(widget(outside, "run_name").value, "inside");
    assert.deepEqual(projectAssetManagers(root), [carousel, innerCarousel]);
}

{
    const root = graph();
    const carousel = add(root, "MiniMaxH3ProjectAssetManager", 1, {run_name:"h3_project",
        catalog_json:JSON.stringify({project:"saved_name",assets:[],reference_slots:[]})});
    const plan = add(root, "MiniMaxH3ChainPlanStudio", 2, {run_name:"h3_chain"});
    connect(carousel, plan, "project_assets");
    assert.equal(syncManagedPlanRunName(plan), true);
    assert.equal(widget(plan, "run_name").value, "saved_name");
    widget(carousel, "catalog_json").value = "";
    assert.equal(syncManagedPlanRunName(plan), false, "An unconfigured Carousel cannot erase a named Plan");
    const loop = add(root, "Reroute", 3);
    connect(loop, loop, "input");
    connect(loop, plan, "cycle");
    plan.inputs[0].link = plan.inputs[1].link;
    assert.equal(syncManagedPlanRunName(plan), false, "Cycles must terminate without changing identity");
}

assert.match(read("h3_chain_plan_editor.js"), /function syncProjectAssetManagedWidgets\(\) \{\s*syncManagedPlanRunName\(node\)/);
assert.match(studioSource, /syncManagedPlanRunName\(planOwner\);\s*const currentRun/);
console.log("Project run sync: screenshot topology, legacy Run Manager, Modern Plan refresh, Map/object links, reload, reroutes, Set/Get, subgraphs and isolation pass");
