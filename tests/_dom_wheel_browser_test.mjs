// Isolated DOM + trusted mouse-wheel regression. Never connects to live ComfyUI.
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import {spawn} from "node:child_process";

const web = new URL("../web/", import.meta.url);
const panels = fs.readdirSync(web).filter(name => name.endsWith(".js")
    && fs.readFileSync(new URL(name, web), "utf8").includes("node.addDOMWidget("));
for (const name of panels) {
    const source = fs.readFileSync(new URL(name, web), "utf8");
    assert.match(source, /import \{bindNodeWheel\} from "\.\/h3_dom_wheel\.mjs";/, name);
    assert.match(source, /bindNodeWheel\(root, node, app\);/, name);
    assert.doesNotMatch(source, /root\.addEventListener\("wheel"/, name);
}
// Exercise the actual Studio listener, not a copy of its zoom/scroll behavior.
const studio = fs.readFileSync(new URL("h3_chain_plan_studio.js", web), "utf8");
const start = studio.indexOf('timelineViewport.addEventListener("wheel",');
const end = studio.indexOf('}, {passive:false});', start);
assert.ok(start >= 0 && end > start);
const timelineListener = studio.slice(start, end + '}, {passive:false});'.length);
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "h3-dom-wheel-"));
const html = `<!doctype html><meta charset="utf-8"><style>
body{margin:0;height:100vh;overflow:hidden;background:#202124;color:#ddd}
canvas{position:absolute;inset:0;width:100%;height:100%}
.panel{position:absolute;left:30px;top:30px;width:400px;height:220px;overflow:auto;background:#30343a}
#timeline{left:470px}.long{height:1400px}.wide{width:1800px;height:100px}
</style><canvas></canvas><div class="panel" id="editor"><input><div class="long">Panel contents</div></div>
<div class="panel" id="timeline"><div class="wide">Timeline</div></div>
<script type="module">import {bindNodeWheel} from '/web/h3_dom_wheel.mjs';
(${browserChecks.toString()})(bindNodeWheel, ${JSON.stringify(timelineListener)});</script>`;
const server = http.createServer((req, res) => {
    if (req.url === "/") { res.setHeader("Content-Type", "text/html"); res.end(html); return; }
    if (req.url === "/web/h3_dom_wheel.mjs") {
        res.setHeader("Content-Type", "text/javascript");
        res.end(fs.readFileSync(new URL("h3_dom_wheel.mjs", web))); return;
    }
    if (req.url === "/web/h3_dom_wheel_core.mjs") {
        res.setHeader("Content-Type", "text/javascript");
        res.end(fs.readFileSync(new URL("h3_dom_wheel_core.mjs", web))); return;
    }
    res.writeHead(404); res.end();
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
let chrome, socket;
const timeout = setTimeout(() => { chrome?.kill(); }, 25000);
try {
    chrome = spawn(process.env.H3_TEST_BROWSER || "/usr/bin/google-chrome-stable", [
        "--headless", "--disable-gpu", "--no-first-run", "--disable-extensions",
        "--disable-background-networking", "--disable-component-update", "--disable-sync",
        "--user-data-dir=" + path.join(temporary, "profile"), "--remote-debugging-port=0",
        "--window-size=1000,650", "about:blank",
    ], {stdio:["ignore", "ignore", "pipe"]});
    const debugUrl = await new Promise((resolve, reject) => {
        let stderr = "";
        chrome.stderr.on("data", chunk => {
            stderr += chunk;
            const url = stderr.match(/DevTools listening on (ws:\/\/[^\s]+)/)?.[1];
            if (url) resolve(url);
        });
        chrome.once("error", reject);
        chrome.once("exit", () => reject(Error("Browser exited: " + stderr.slice(-1000))));
    });
    socket = new WebSocket(debugUrl);
    await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
    let serial = 0;
    const pending = new Map();
    socket.onmessage = event => {
        const message = JSON.parse(event.data), waiter = pending.get(message.id);
        if (!waiter) return;
        pending.delete(message.id);
        if (message.error) waiter.reject(Error(JSON.stringify(message.error)));
        else waiter.resolve(message.result);
    };
    const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
        const id = ++serial; pending.set(id, {resolve, reject});
        socket.send(JSON.stringify({id, method, params, sessionId}));
    });
    const {targetId} = await send("Target.createTarget", {url:"about:blank"});
    const {sessionId} = await send("Target.attachToTarget", {targetId, flatten:true});
    const call = (method, params) => send(method, params, sessionId);
    const evaluate = async expression => {
        const result = await call("Runtime.evaluate", {expression, awaitPromise:true, returnByValue:true});
        assert.equal(result.exceptionDetails, undefined, JSON.stringify(result.exceptionDetails));
        return result.result.value;
    };
    await call("Page.navigate", {url:`http://127.0.0.1:${server.address().port}/`});
    for (let i = 0; i < 100; i++) {
        if (await evaluate("Boolean(window.wheelReport)")) break;
        await new Promise(resolve => setTimeout(resolve, 20));
    }
    const report = await evaluate("window.wheelReport");
    assert.ok(report, "Browser fixture did not load");
    assert.deepEqual(report.failures, []);
    // Real browser input verifies default scrolling, which dispatchEvent alone cannot.
    async function trustedWheel(setup) {
        await evaluate(`f.reset(); ${setup}`);
        await call("Input.dispatchMouseEvent", {type:"mouseWheel", x:150, y:100, deltaX:0, deltaY:120});
        await evaluate("new Promise(resolve => setTimeout(resolve, 180))");
        return evaluate("({canvas:f.events.length, scroll:f.root.scrollTop, page:window.scrollY})");
    }
    assert.deepEqual(await trustedWheel(""), {canvas:1, scroll:0, page:0}, "Inactive panel zooms canvas only");
    for (const setup of ["f.canvas.selected_nodes[f.node.id] = f.node", "f.input.focus()",
        "f.canvas.selectedItems = new Set([f.node])"]) {
        const result = await trustedWheel(setup);
        assert.equal(result.canvas, 0, "Active panel must not zoom canvas");
        assert.ok(result.scroll > 0, "Active panel retains native scrolling");
        assert.equal(result.page, 0);
    }
    assert.deepEqual(await trustedWheel("f.input.focus(); f.canvas.read_only = true"),
        {canvas:1, scroll:0, page:0}, "Hand mode wins over editor focus");
    console.log(`Wheel routing: ${report.checks} DOM checks + 5 trusted-wheel cases passed; ${panels.length} panels wired.`);
} finally {
    clearTimeout(timeout);
    socket?.close();
    chrome?.kill();
    server.close();
}

function browserChecks(bindNodeWheel, timelineListener) {
    const report = {checks:0, failures:[]};
    const check = (value, message) => { report.checks++; if (!value) throw Error(message); };
    try {
        const root = document.querySelector("#editor"), input = root.querySelector("input");
        const target = document.querySelector("canvas"), graph = {}, node = {id:7, graph};
        const canvas = {canvas:target, graph, selected_nodes:{}, read_only:false};
        const app = {canvas}, events = [];
        target.addEventListener("wheel", event => { events.push(event); event.preventDefault(); });
        const reset = () => {
            document.activeElement?.blur(); root.scrollTop = 0; events.length = 0;
            app.canvas = canvas; canvas.graph = graph; canvas.selected_nodes = {};
            canvas.selectedItems = undefined; canvas.read_only = false;
        };
        const wheel = (element = root.lastElementChild, options = {}) => {
            const event = new WheelEvent("wheel", {bubbles:true, cancelable:true, deltaY:120,
                clientX:150, clientY:100, ...options});
            element.dispatchEvent(event); return event;
        };
        bindNodeWheel(root, node, app);
        check(root.dataset.captureWheel === "true", "Focused controls advertise native wheel capture");
        let childEvents = 0, parentEvents = 0;
        root.lastElementChild.addEventListener("wheel", event => { childEvents++; event.stopPropagation(); });
        document.body.addEventListener("wheel", () => parentEvents++);
        check(wheel().defaultPrevented && events.length === 1, "Inactive wheel is forwarded exactly once");
        check(childEvents === 0 && parentEvents === 0, "Inactive routing precedes nested controls");
        for (const options of [{deltaX:17, deltaY:-42, deltaZ:3, deltaMode:1, altKey:true},
            {ctrlKey:true}, {metaKey:true}, {shiftKey:true}, {deltaMode:2}]) {
            reset(); const event = wheel(root, options), forwarded = events[0];
            check(event.defaultPrevented && events.length === 1, "Root and modified wheel route once");
            for (const key of ["clientX", "clientY", "deltaX", "deltaY", "deltaZ", "deltaMode",
                "ctrlKey", "metaKey", "shiftKey", "altKey"]) {
                check(forwarded[key] === event[key], "Preserved canvas wheel field: " + key);
            }
        }
        for (const select of [() => canvas.selected_nodes[node.id] = node,
            () => canvas.selectedItems = new Set([node]), () => input.focus()]) {
            reset(); select(); const before = childEvents;
            check(!wheel().defaultPrevented && events.length === 0, "Selected/focused panel keeps native scroll");
            check(childEvents === before + 1, "Active nested controls still receive wheel input");
            check(!wheel(root).defaultPrevented && parentEvents === 0, "Active wheel does not reach canvas ancestors");
            canvas.read_only = true;
            check(wheel().defaultPrevented && events.length === 1, "Hand mode overrides selection/focus");
        }
        reset(); canvas.selected_nodes[node.id] = {id:node.id};
        check(wheel().defaultPrevented && events.length === 1, "A different node with the same ID is not selected");
        reset(); canvas.graph = {};
        check(!wheel().defaultPrevented && events.length === 0, "Inactive workflow/subgraph cannot drive this canvas");
        reset(); app.canvas = null;
        check(!wheel().defaultPrevented, "Missing canvas leaves normal scrolling available");
        reset(); const otherEvents = [];
        const otherCanvas = document.createElement("canvas"); document.body.append(otherCanvas);
        otherCanvas.style.pointerEvents = "none";
        otherCanvas.addEventListener("wheel", event => otherEvents.push(event));
        app.canvas = {canvas:otherCanvas, graph, selectedItems:new Set()};
        wheel(); check(otherEvents.length === 1 && events.length === 0, "Canvas replacement is resolved on each gesture");
        otherCanvas.remove(); reset();
        const consumed = new WheelEvent("wheel", {bubbles:true, cancelable:true}); consumed.preventDefault();
        root.dispatchEvent(consumed);
        check(events.length === 0, "Already handled gestures are not forwarded twice");
        const timelineViewport = document.querySelector("#timeline"), state = {timelineZoom:1};
        let zoomCalls = 0;
        const setTimelineZoom = value => { zoomCalls++; state.timelineZoom = value; };
        new Function("timelineViewport", "state", "setTimelineZoom", "noteTimelineScrollIntent", timelineListener)(
            timelineViewport, state, setTimelineZoom, () => {});
        const timelineRoot = document.createElement("div");
        timelineViewport.before(timelineRoot); timelineRoot.append(timelineViewport);
        bindNodeWheel(timelineRoot, node, app);
        for (const options of [{ctrlKey:true}, {metaKey:true}, {shiftKey:true}]) {
            reset(); timelineViewport.scrollLeft = 0;
            const before = zoomCalls;
            wheel(timelineViewport, options);
            check(events.length === 1 && zoomCalls === before && timelineViewport.scrollLeft === 0,
                "Inactive Studio controls route to canvas before changing the timeline");
            reset(); canvas.selected_nodes[node.id] = node;
            wheel(timelineViewport, options);
            check(events.length === 0, "Active Studio controls never zoom the workflow");
            check(options.shiftKey ? timelineViewport.scrollLeft === 120 : zoomCalls === before + 1,
                "Active Studio Ctrl/Cmd zoom and Shift scroll still work");
        }
        reset();
        // Model the Vue renderer's ancestor capture contract: focused controls
        // may scroll, but host gestures run before descendants. Never double-route.
        const host = document.createElement("div"); root.before(host); host.append(root);
        host.addEventListener("wheel", event => {
            const capture = event.target.closest('[data-capture-wheel="true"]');
            if (capture?.contains(document.activeElement) && !event.ctrlKey && !event.metaKey) return;
            event.preventDefault(); event.stopPropagation();
            target.dispatchEvent(new WheelEvent("wheel", {deltaY:event.deltaY}));
        }, {capture:true, passive:false});
        wheel(); check(events.length === 1, "Vue host handles an inactive gesture once");
        reset(); input.focus();
        check(!wheel().defaultPrevented && events.length === 0, "Vue capture marker allows focused native scroll");
        wheel(input, {ctrlKey:true});
        check(events.length === 1, "Vue host canvas gestures keep priority without double zoom");
        host.replaceWith(root); reset();
        window.f = {root, input, node, canvas, events, reset};
    } catch (error) { report.failures.push(error.stack || String(error)); }
    window.wheelReport = report;
}
