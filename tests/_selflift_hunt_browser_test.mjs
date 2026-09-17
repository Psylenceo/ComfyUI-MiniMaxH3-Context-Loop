// Real review DOM in an isolated layout host; no live ComfyUI or user projects.
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import {spawn} from "node:child_process";

const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "h3-hunt-layout-"));
const html = `<!doctype html><meta charset="utf-8"><style>
body{margin:12px;background:#15161a;color:#ddd}.node{background:#393939;box-sizing:border-box;padding:10px}
.native{height:300px}.review-host{position:relative}
</style><script type="module">(${browserChecks.toString()})();</script>`;
const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    if (url.pathname === "/") { res.setHeader("Content-Type", "text/html"); res.end(html); return; }
    res.setHeader("Content-Type", "text/javascript");
    if (["/scripts/app.js", "/scripts/api.js"].includes(url.pathname)) {
        const name = path.basename(url.pathname, ".js");
        res.end(`export const ${name} = window.${name};`); return;
    }
    if (/^\/web\/[\w.-]+\.(mjs|js)$/.test(url.pathname)) {
        try { res.end(fs.readFileSync(new URL(".." + url.pathname, import.meta.url))); return; }
        catch { /* Surface missing modules in the browser report. */ }
    }
    res.writeHead(404); res.end();
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
let chrome;
try {
    chrome = spawn(process.env.H3_TEST_BROWSER || "/usr/bin/google-chrome-stable", [
        "--headless", "--disable-gpu", "--no-first-run", "--disable-extensions",
        "--disable-background-networking", "--disable-component-update", "--disable-sync",
        "--user-data-dir=" + path.join(temporary, "profile"), "--virtual-time-budget=6000",
        "--window-size=1100,1150", "--screenshot=" + path.join(temporary, "review.png"),
        "--dump-dom", `http://127.0.0.1:${server.address().port}/`,
    ], {stdio:["ignore", "pipe", "pipe"]});
    let stdout = "", stderr = "";
    chrome.stdout.on("data", chunk => stdout += chunk);
    chrome.stderr.on("data", chunk => stderr += chunk);
    const code = await new Promise((resolve, reject) => {
        const timer = setTimeout(() => { chrome.kill(); reject(Error("Browser fixture timed out")); }, 25000);
        chrome.once("error", error => { clearTimeout(timer); reject(error); });
        chrome.once("exit", code => { clearTimeout(timer); resolve(code); });
    });
    assert.equal(code, 0, stderr);
    const encoded = stdout.match(/data-report="([^"]+)"/)?.[1];
    assert.ok(encoded, "Missing browser report: " + stdout.slice(-2000) + stderr.slice(-1000));
    const report = JSON.parse(Buffer.from(encoded, "base64").toString());
    console.log(report);
    console.log("Isolated screenshot: " + path.join(temporary, "review.png"));
    assert.deepEqual(report.failures, []);
} finally {
    chrome?.kill(); server.close();
}

async function browserChecks() {
    const report = {checks:0, failures:[]};
    const check = (value, message) => { report.checks++; if (!value) throw Error(message); };
    window.addEventListener("error", event => report.failures.push(event.message));
    const graph = {};
    const batch = {id:"fixture", run_name:"layout_test", scene:1, scene_name:"dog_walk", batch_name:"hunt_1",
        active:true, phase:"waiting", selected:null, low_steps:20, high_steps:5, candidates:[]};
    const takes = count => Array.from({length:count}, (_, i) => ({ordinal:i+1, seed:String(i+1), preview:`test/take_${i+1}.mp4`}));
    batch.candidates = takes(1);
    window.app = {graph, registerExtension(value) { window.extension = value; }};
    window.api = {apiURL:url => url, addEventListener() {}, async fetchApi() {
        return {ok:true, json:async () => ({batches:[batch]})};
    }};
    try {
        await import("/web/h3_selflift_hunt.js");
        class Node {
            constructor(size) { this.size = size; this.graph = graph; this.properties = {}; }
            addDOMWidget(name, type, root, options) {
                this.root = root; this.host = document.createElement("div"); this.host.className = "node";
                const native = document.createElement("div"); native.className = "native";
                native.textContent = "SelfLift Seed Hunt — synthetic layout fixture (native controls above)";
                this.viewport = document.createElement("div"); this.viewport.className = "review-host";
                this.viewport.append(root); this.host.append(native, this.viewport); document.body.append(this.host);
                return this.widget = {options};
            }
            setSize(size) {
                this.size = [...size]; this.host.style.width = size[0] + "px"; this.host.style.height = size[1] + "px";
                // Legacy LiteGraph prioritizes fixed computeSize; flexible DOM
                // widgets receive the remaining body height above their minimum.
                const height = this.widget.computeSize ? this.widget.computeSize(size[0])[1] + 4
                    : Math.max(this.widget.options.getMinHeight?.() ?? 50, size[1] - 320);
                this.viewport.style.height = height + "px";
            }
        }
        window.extension.beforeRegisterNodeDef(Node, {name:"MiniMaxH3SelfLiftSeedHunt"});
        const node = new Node([840, 1000]); node.onNodeCreated();
        await new Promise(resolve => setTimeout(resolve, 80));
        const root = node.root, video = root.querySelector("video");
        check(Boolean(video), "Saved take renders");
        check(root.clientHeight === 680, "Panel fills the enlarged node instead of leaving a dead strip");
        check(root.scrollHeight <= root.clientHeight, "One complete take and help text fit without scrolling");
        check(root.lastElementChild.getBoundingClientRect().bottom <= root.getBoundingClientRect().bottom,
            "Help text is visible, not clipped at the bottom");
        node.setSize([840, 1200]);
        check(root.clientHeight === 880, "Panel grows when dragged taller");
        check(root.querySelector("video") === video, "Resize does not rebuild or interrupt playback");
        node.setSize([620, 900]);
        check(root.clientHeight === 580, "Panel also follows a deliberate shrink");
        check(root.scrollWidth <= root.clientWidth, "No horizontal scrollbar after narrowing");
        batch.candidates = takes(12); node._h3SelfLiftHunt.render();
        check(root.scrollHeight > root.clientHeight, "Many takes remain scrollable in a small node");
        node.setSize([840, 2000]);
        check(root.scrollHeight <= root.clientHeight, "Enlarging the node can show all take rows without scrolling");
        const selected = root.querySelector('button[data-ordinal="2"]');
        batch.phase = "high"; batch.selected = 2; node._h3SelfLiftHunt.render();
        check(selected.disabled && selected.style.outline, "Chosen take/high-pass locking survives resizing");
        batch.candidates = takes(1); node._h3SelfLiftHunt.render(); node.setSize([840, 1000]);
        node.onRemoved();
        check(root.querySelector("video").getAttribute("src") === null, "Removed nodes release preview sources");
    } catch (error) { report.failures.push(error.stack || String(error)); }
    document.body.setAttribute("data-report", btoa(JSON.stringify(report)));
}
