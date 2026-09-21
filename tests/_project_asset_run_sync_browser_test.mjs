// Real Modern Plan and legacy Run Manager DOM, isolated from ComfyUI/user data.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import {spawn} from "node:child_process";

const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "h3-project-run-sync-"));
const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    if (url.pathname === "/") {
        res.setHeader("Content-Type", "text/html; charset=utf-8");
        res.end(`<!doctype html><style>.host{width:920px;height:860px}</style>
            <script type="module">(${browserChecks.toString()})();</script>`);
        return;
    }
    res.setHeader("Content-Type", "text/javascript; charset=utf-8");
    if (["/scripts/app.js", "/scripts/api.js"].includes(url.pathname)) {
        const name = path.basename(url.pathname, ".js");
        res.end(`export const ${name} = window.${name};`); return;
    }
    if (/^\/web\/[\w.-]+\.(mjs|js)$/.test(url.pathname)) {
        try { res.end(fs.readFileSync(new URL(".." + url.pathname, import.meta.url))); return; }
        catch { /* Missing imports fail the browser check. */ }
    }
    res.writeHead(404); res.end();
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
let chrome;
try {
    chrome = spawn(process.env.H3_TEST_BROWSER || "/opt/google/chrome/chrome", [
        "--headless", "--disable-gpu", "--no-first-run", "--disable-extensions",
        "--disable-background-networking", "--disable-component-update", "--disable-sync",
        "--user-data-dir=" + path.join(temporary, "profile"), "--virtual-time-budget=12000",
        "--dump-dom", `http://127.0.0.1:${server.address().port}/`,
    ], {stdio:["ignore", "pipe", "pipe"]});
    let stdout = "", stderr = "";
    chrome.stdout.on("data", chunk => stdout += chunk);
    chrome.stderr.on("data", chunk => stderr += chunk);
    const code = await new Promise((resolve, reject) => {
        const timer = setTimeout(() => { chrome.kill(); reject(Error("Browser test timed out")); }, 25000);
        chrome.once("error", error => { clearTimeout(timer); reject(error); });
        chrome.once("exit", code => { clearTimeout(timer); resolve(code); });
    });
    assert.equal(code, 0, stderr);
    const encoded = stdout.match(/data-report="([^"]+)"/)?.[1];
    assert.ok(encoded, "Browser did not finish: " + stdout.slice(-1500) + stderr.slice(-500));
    const report = JSON.parse(Buffer.from(encoded, "base64").toString());
    console.log(report);
    assert.deepEqual(report.failures, []);
} finally { chrome?.kill(); server.close(); }

async function browserChecks() {
    const report = {checks:0, failures:[]};
    const check = (value, message) => { report.checks++; if (!value) throw Error(message); };
    const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
    const waitFor = async predicate => {
        for (let i = 0; i < 150; i++) { if (predicate()) return; await wait(10); }
        throw Error("Timed out: " + document.body.innerText.slice(-700));
    };
    window.addEventListener("error", event => report.failures.push(event.message));
    window.addEventListener("unhandledrejection", event => report.failures.push(String(event.reason?.stack || event.reason)));
    window.requestAnimationFrame = callback => setTimeout(() => callback(performance.now()), 16);
    window.cancelAnimationFrame = clearTimeout;
    const graph = {_nodes:[], links:new Map(), setDirtyCanvas() {},
        getNodeById(id) { return this._nodes.find(node => node.id === id); }};
    const extensions = [];
    window.app = {graph, configuringGraph:false, registerExtension(value) { extensions.push(value); }};
    const requests = [];
    window.api = Object.assign(new EventTarget(), {
        async fetchApi(route, options) {
            requests.push({route, options});
            if (route !== "/minimax_h3_context_loop/runs") throw Error("Unexpected API request: " + route);
            return {ok:true, json:async () => ({runs:[{run_name:"h3_chain", restorable:true,
                checkpoint_count:0, asset_count:0, archive_bytes:0, sources:{}}]})};
        },
    });
    const widget = (node, name) => node.widgets.find(item => item.name === name);
    let linkId = 0;
    function connect(source, target, name) {
        const id = ++linkId, input = target.inputs.find(item => item.name === name);
        if (input) input.link = id;
        else target.inputs.push({name, link:id});
        source.outputs[0] ??= {name:"output", links:[]};
        source.outputs[0].links.push(id);
        graph.links.set(id, {origin_id:source.id, origin_slot:0, target_id:target.id});
    }
    class BaseNode {
        constructor(type, settings = {}) {
            this.id = graph._nodes.length + 1; this.type = this.comfyClass = type;
            this.graph = graph; this.inputs = []; this.outputs = []; this.properties = {};
            this.size = [920, 860];
            this.widgets = Object.entries(settings).map(([name, value]) => ({name, value, type:"text", options:{}}));
            graph._nodes.push(this);
        }
        setSize(value) { this.size = value; }
        addInput(name, type) { this.inputs.push({name, type, link:null}); }
        addDOMWidget(name, type, root) {
            this.root = root; this.host = document.createElement("div");
            this.host.className = "host"; this.host.append(root); document.body.append(this.host);
            const item = {name, type, element:root, options:{}};
            this.widgets.push(item); return item;
        }
    }
    try {
        await import("/web/h3_chain_plan_editor.js");
        await import("/web/h3_chain_run_manager.js");
        const {syncProjectAssetPlanRun} = await import("/web/h3_project_asset_sync_core.mjs?v=0.7.3");
        const settings = {run_name:"h3_chain", generation_fingerprint:"", width:960, height:544,
            default_duration_seconds:5, default_steps:8, base_seed:1,
            encode_mode:"video", crop:"disabled", segment_crf:18, video_blend_frames:0,
            plan_json:JSON.stringify({shots:[{id:"one", prompt:["Keep this prompt."], length:123, seed:"1"}]})};
        class Plan extends BaseNode { constructor() { super("MiniMaxH3ChainPlanModern", settings); } }
        class Manager extends BaseNode { constructor() { super("MiniMaxH3ChainRunManager", {
            archive_images:true, archive_audio:true, archive_video:false, asset_bindings_json:"[]"}); } }
        for (const extension of extensions) {
            await extension.beforeRegisterNodeDef?.(Plan, {name:"MiniMaxH3ChainPlanModern"});
            await extension.beforeRegisterNodeDef?.(Manager, {name:"MiniMaxH3ChainRunManager"});
        }
        const carousel = new BaseNode("MiniMaxH3ProjectAssetManager", {run_name:"bob"});
        const plan = new Plan(), manager = new Manager(), unrelated = new Plan();
        widget(unrelated, "run_name").value = "unrelated";
        connect(carousel, plan, "project_assets"); connect(plan, manager, "plan");
        plan.onNodeCreated(); manager.onNodeCreated(); unrelated.onNodeCreated();
        const runField = node => [...(node.root?.querySelectorAll("label") ?? [])]
            .find(label => label.firstChild?.textContent === "Run name")?.querySelector("input");
        const activeLabel = () => manager.root?.querySelector(".h3rm-identity-active")?.textContent;
        await waitFor(() => runField(plan)?.value === "bob" && activeLabel() === "Active Plan: bob");
        check(runField(plan).disabled, "Managed Modern Plan field is disabled and shows bob on load");
        check(widget(plan, "run_name").value === "bob", "Backing run matches the visible form");
        check(runField(unrelated).value === "unrelated", "Unrelated Plan stays independent");
        const savedArchive = manager.root.querySelector(".h3rm-select").value;
        const authored = widget(plan, "plan_json").value;
        widget(carousel, "run_name").value = "alice";
        syncProjectAssetPlanRun(carousel, "alice");
        await waitFor(() => runField(plan)?.value === "alice" && activeLabel() === "Active Plan: alice");
        check(manager.root.querySelector(".h3rm-select").value === savedArchive,
            "Changing active project does not change the selected recovery archive");
        check(widget(plan, "plan_json").value === authored, "Project rename preserves authored scenes");
        check(runField(unrelated).value === "unrelated", "Project change does not rename other Plans");
        const renderedField = runField(plan);
        for (let i = 0; i < 3; i++) syncProjectAssetPlanRun(carousel, "alice");
        await wait(200);
        check(runField(plan) === renderedField, "Unchanged catalog does not rebuild Modern Plan");
        const second = new BaseNode("MiniMaxH3ProjectAssetTree", {run_name:"other_project"});
        connect(second, plan, "project_assets"); plan.onConnectionsChange();
        await waitFor(() => runField(plan)?.value === "other_project" && activeLabel() === "Active Plan: other_project");
        check(widget(plan, "run_name").value === "other_project", "Reconnection follows the new source");
        syncProjectAssetPlanRun(carousel, "wrong_project"); await wait(100);
        check(runField(plan).value === "other_project", "Former Carousel cannot rename reconnected Plan");
        widget(plan, "run_name").value = "h3_chain"; plan.onConfigure();
        await waitFor(() => widget(plan, "run_name").value === "other_project"
            && runField(plan)?.value === "other_project" && activeLabel() === "Active Plan: other_project");
        check(widget(plan, "run_name").value === "other_project", "Reload repairs stale serialized defaults");
        check(requests.length === 1 && !requests[0].options?.method,
            "Sync causes no project writes or archive reloads; only initial archive-list GET");
        for (const node of [plan, manager, unrelated]) node.onRemoved?.();
    } catch (error) { report.failures.push(String(error?.stack || error)); }
    document.body.dataset.report = btoa(JSON.stringify(report));
}
