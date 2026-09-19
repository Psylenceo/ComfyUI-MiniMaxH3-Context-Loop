import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import {MODERN_PLAN_NODE, MODERN_PLAN_WIDGET_NAMES} from "../web/h3_plan_upgrade_core.mjs";

const source = fs.readFileSync(new URL("../web/h3_chain_plan_editor.js", import.meta.url), "utf8");
const context = vm.createContext({MODERN_NODE_NAME:MODERN_PLAN_NODE, MODERN_BACKING_WIDGETS:MODERN_PLAN_WIDGET_NAMES});
for (const name of ["collapseWidget", "collapseModernBackingWidgets", "setProjectAssetManagedWidget"]) {
    vm.runInContext(source.match(new RegExp(`^function ${name}\\([^]*?^}`, "m"))[0], context);
}
// ComfyUI's canvas checks widget.hidden. Its Vue renderer instead extracts
// options.hidden in safeWidgetMapper/computeProcessedWidgets (frontend 1.52.7).
const canvasWidgets = node => node.widgets.filter(widget => !widget.hidden);
const vueWidgets = node => node.widgets.filter(widget => !widget.options?.hidden);
const makeWidget = (name, value) => ({name, value, type:"number", options:{precision:0}, callback() {}});
const node = {comfyClass:MODERN_PLAN_NODE, inputs:[], size:[1100, 1600], widgets:
    MODERN_PLAN_WIDGET_NAMES.map((name, index) => makeWidget(name, index))};
const editor = {name:"h3_chain_scene_editor", type:"h3-chain-editor", options:{serialize:false}};
node.widgets.push(editor);
const originalWidgets = [...node.widgets];
const originalValues = node.widgets.map(widget => widget.value);
const originalOptions = node.widgets.map(widget => widget.options);
const callbacks = node.widgets.map(widget => widget.callback);
for (let i = 0; i < 3; i++) context.collapseModernBackingWidgets(node);
assert.deepEqual(canvasWidgets(node), [editor], "Canvas allocates space only for the editor");
assert.deepEqual(vueWidgets(node), [editor], "Vue must not leave empty backing-widget placeholders");
assert.deepEqual(node.widgets, originalWidgets, "Do not remove/reorder serialized widgets");
assert.deepEqual(node.widgets.map(widget => widget.value), originalValues);
assert.deepEqual(node.widgets.map(widget => widget.callback), callbacks);
node.widgets.forEach((widget, index) => assert.equal(widget.options, originalOptions[index], "Preserve shared widget-store options"));
assert.deepEqual(node.size, [1100, 1600], "Keep the user-selected viewport size");

// Recent ComfyUI creates a socket for EVERY ordinary widget; its presence is
// not evidence of an explicit conversion. This was missing from the old test
// and let the JSON textarea's blank space and all native settings reappear.
node.inputs = MODERN_PLAN_WIDGET_NAMES.map(name => ({name, widget:{name}, link:null}));
const autoInputs = [...node.inputs];
for (let i = 0; i < 3; i++) context.collapseModernBackingWidgets(node);
assert.deepEqual(canvasWidgets(node), [editor], "Automatic sockets must not unhide native controls or the JSON spacer");
assert.deepEqual(vueWidgets(node), [editor], "Automatic sockets must not leave Vue rows behind");
assert.deepEqual(node.inputs, autoInputs, "Keep core's automatic inputs available for existing links");

// Turning a backing control into a socket must restore it, including when the
// original computeSize/draw were undefined and several refreshes have occurred.
const width = node.widgets.find(widget => widget.name === "width");
const widthInput = node.inputs.find(input => input.name === "width");
widthInput.link = 42;
context.collapseModernBackingWidgets(node);
assert.equal(width.hidden, false);
assert.equal(width.options.hidden, undefined);
assert.equal(width.type, "number");
assert.equal(width.computeSize, undefined);
assert.equal(width.draw, undefined);
assert.deepEqual(vueWidgets(node), [width, editor]);
assert.deepEqual(canvasWidgets(node), [width, editor]);
assert.equal(widthInput.link, 42, "Do not disturb a real connection");
widthInput.link = null;
context.collapseModernBackingWidgets(node);
assert.deepEqual(vueWidgets(node), [editor], "Disconnect must hide the now ordinary automatic socket again");
assert.deepEqual(canvasWidgets(node), [editor]);
width.type = "converted-widget";
const convertedSize = () => [0, -4];
width.computeSize = convertedSize;
context.collapseModernBackingWidgets(node);
assert.equal(width.options.hidden, undefined, "Core conversion cannot leave our Vue hiding flag behind");
assert.equal(width.computeSize, convertedSize, "Do not overwrite core's converted socket layout");
assert.equal(width.type, "converted-widget");
assert.equal(width.hidden, false);

// A legacy conversion may be collapsed before workflow configuration restores
// its input. Recover the original conversion, not a duplicate native control.
node.inputs = [];
const converted = makeWidget("generation_fingerprint", "keep-fingerprint");
converted.type = "converted-widget";
converted.computeSize = convertedSize;
context.collapseWidget(converted);
const convertedNode = {type:MODERN_PLAN_NODE, widgets:[converted],
    inputs:[{name:converted.name, widget:{name:converted.name}, link:null}]};
context.collapseModernBackingWidgets(convertedNode);
assert.equal(converted.hidden, false);
assert.equal(converted.type, "converted-widget");
assert.equal(converted.computeSize, convertedSize);

const legacy = {type:"MiniMaxH3ChainPlan", widgets:[makeWidget("width", 960)],
    inputs:[{name:"width", widget:{name:"width"}, link:null}]};
context.collapseModernBackingWidgets(legacy);
assert.deepEqual(canvasWidgets(legacy), legacy.widgets, "Legacy Plan keeps its native settings");

// The shared legacy Plan path must restore editable identity controls when
// Project Assets is disconnected, without unhiding Modern Plan backing ones.
for (const initiallyHidden of [undefined, false, true]) {
    const widget = makeWidget("run_name", "keep_project");
    widget.options.hidden = initiallyHidden;
    const options = widget.options;
    context.setProjectAssetManagedWidget(widget, true);
    assert.equal(widget.hidden, true);
    assert.equal(widget.options.hidden, true);
    context.setProjectAssetManagedWidget(widget, true);
    context.setProjectAssetManagedWidget(widget, false);
    assert.equal(widget.options.hidden, initiallyHidden);
    assert.equal(widget.hidden, undefined);
    assert.equal(widget.disabled, undefined);
    assert.equal(widget.options, options);
    assert.equal(widget.value, "keep_project");
}
const modernName = node.widgets.find(widget => widget.name === "run_name");
context.setProjectAssetManagedWidget(modernName, true);
context.setProjectAssetManagedWidget(modernName, false);
assert.equal(modernName.options.hidden, true);

// DOM surface removal is still required on graph re-add, and hiding never
// disables serialization of the plan JSON itself.
let removals = 0;
const textarea = {style:{setProperty() {}}, setAttribute() {}};
const plan = {name:"plan_json", type:"customtext", value:'{"shots":[]}', element:textarea,
    id:"plan-widget", options:{getMinHeight:() => 200}, onRemove() { removals++; }};
context.collapseWidget(plan);
context.collapseWidget(plan);
assert.equal(removals, 2);
assert.equal(plan.options.hidden, true);
assert.equal(plan.options.getMinHeight(), 200);
assert.equal(plan.value, '{"shots":[]}');
assert.notEqual(plan.serialize, false);
console.log("Plan widget visibility: canvas/Vue layout, converted sockets, managed fields and serialization pass");
