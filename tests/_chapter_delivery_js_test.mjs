import assert from "node:assert/strict";
import fs from "node:fs";

// Exercise the real presentation hooks without a live graph or project.
let extension;
globalThis.h3ChapterDeliveryTestApp = {
    graph: {_nodes: []},
    registerExtension(value) { extension = value; },
};
const source = fs.readFileSync(
    new URL("../web/h3_socket_presentation.js", import.meta.url), "utf8",
).replace('import {app} from "/scripts/app.js";',
    "const app = globalThis.h3ChapterDeliveryTestApp;")
    .replace(/\.\/h3_socket_presentation_core\.mjs\?[^"']+/, () =>
        new URL("../web/h3_socket_presentation_core.mjs", import.meta.url).href);
await import("data:text/javascript;base64," + Buffer.from(source).toString("base64"));

class DeliveryNode {
    constructor() {
        this.comfyClass = "MiniMaxH3ChainChapterDelivery";
        this.widgets = [{name: "enabled", type: "toggle", value: true}];
        this.inputs = [{name: "manifest", link: 11}];
        this.outputs = [{name: "delivery_manifest", links: [12]}];
        this.properties = {};
    }
    onNodeCreated() { this.created = true; }
    onConfigure() { this.configured = true; }
}
await extension.beforeRegisterNodeDef(DeliveryNode, {name: "MiniMaxH3ChainChapterDelivery"});
for (const savedValues of [null, [true, 0], [true, 2], [false, 2]]) {
    const node = new DeliveryNode();
    node.onNodeCreated();
    assert.equal(node.created, true);
    // Saved widget values are positional: the original enabled toggle remains
    // slot 0, and there is no longer a widget to consume the trailing number.
    if (savedValues) {
        node.widgets.forEach((widget, index) => { widget.value = savedValues[index]; });
        node.onConfigure({widgets_values: savedValues});
        assert.equal(node.configured, true);
    }
    const widget = node.widgets[0];
    assert.equal(node.widgets.length, 1);
    assert.equal(widget.label, "Export current chapter");
    assert.equal(widget.name, "enabled", "Keep existing API/widget connections");
    assert.equal(widget.value, savedValues?.[0] ?? true);
    assert.notEqual(widget.hidden, true);
    widget.value = !widget.value;
    widget.callback();
    await Promise.resolve();
    assert.equal(widget.value, !(savedValues?.[0] ?? true));
    assert.deepEqual(node.inputs.map(({name, link, hidden}) => ({name, link, hidden})),
        [{name: "manifest", link: 11, hidden: false}]);
    assert.equal(node.outputs[0].links[0], 12);
}
delete globalThis.h3ChapterDeliveryTestApp;
console.log("H3 chapter delivery: toggle label, saved on/off values and connections pass");
