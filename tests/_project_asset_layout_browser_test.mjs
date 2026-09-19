// Real Studio, synthetic node-editor input and an isolated browser; no ComfyUI server.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import {spawn} from "node:child_process";

const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "h3-carousel-layout-"));
const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    if (url.pathname === "/") {
        res.setHeader("Content-Type", "text/html; charset=utf-8");
        res.end(`<!doctype html><style>body{background:#222;color:#ddd}.host{width:1100px;height:1000px}</style>
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
        "--headless", "--window-size=" + (process.env.H3_TEST_WINDOW_SIZE || "1600,1000"), "--disable-gpu", "--no-first-run", "--disable-extensions",
        "--disable-background-networking", "--disable-component-update", "--disable-sync",
        "--user-data-dir=" + path.join(temporary, "profile"), "--virtual-time-budget=18000",
        "--dump-dom", `http://127.0.0.1:${server.address().port}/`,
    ], {stdio:["ignore", "pipe", "pipe"]});
    let stdout = "", stderr = "";
    chrome.stdout.on("data", chunk => stdout += chunk);
    chrome.stderr.on("data", chunk => stderr += chunk);
    const code = await new Promise((resolve, reject) => {
        const timer = setTimeout(() => { chrome.kill(); reject(Error("Browser test timed out")); }, 30000);
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
    window.addEventListener("error", event => report.failures.push(event.message));
    window.addEventListener("unhandledrejection", event => report.failures.push(String(event.reason?.stack || event.reason)));
    const catalog = {project:"synthetic", revision:"test", folders:[], assets:[
        {id:"parent", tag:"source", kind:"image", role:"picture", enabled:true},
        {id:"child", tag:"hero", kind:"image", role:"picture", enabled:true, parent_asset_id:"parent"},
        {id:"child2", tag:"hero-v1", kind:"image", role:"picture", enabled:true, parent_asset_id:"parent"},
    ]};
    const graph = {_nodes:[], links:{}, setDirtyCanvas() {}, getNodeById(id) { return this._nodes.find(n => n.id === id); }};
    window.app = {graph, registerExtension(extension) { window.extension = extension; }};
    window.api = {apiURL: value => value, async fetchApi(route, options) {
        check(!options?.method || options.method === "GET", "The layout test must never write project data");
        return {ok:true, json:async () => route.endsWith("/projects") ? {projects:[]} : structuredClone(catalog)};
    }};
    const created = [];
    try {
        await import("/web/h3_project_asset_manager.js");
        // Both creation orders exercise globally injected CSS without leakage.
        for (const type of ["MiniMaxH3ProjectAssetManager", "MiniMaxH3ProjectAssetTree",
            "MiniMaxH3ProjectAssetTree", "MiniMaxH3ProjectAssetManager"]) {
            class Node {
                constructor() {
                    this.type = this.comfyClass = type; this.id = graph._nodes.length + 1;
                    this.graph = graph; this.inputs = []; this.outputs = []; this.size = [1100, 700];
                    this.properties = {};
                    this.widgets = [{name:"run_name", value:"synthetic"},
                        {name:"catalog_json", value:JSON.stringify(catalog)}, {name:"operation_json", value:""}];
                    graph._nodes.push(this);
                }
                setSize(size) { this.size = size; }
                addDOMWidget(name, kind, root) {
                    this.root = root; this.host = document.createElement("div"); this.host.className = "host";
                    this.host.append(root); document.body.append(this.host);
                    return this.dom = {element:root};
                }
            }
            await window.extension.beforeRegisterNodeDef(Node, {name:type});
            const node = new Node(); node.onNodeCreated(); created.push(node);
            for (let i = 0; i < 100 && !node.root?.querySelector(".h3pa-card"); i++) await wait(10);
            check(Boolean(node.root?.querySelector(".h3pa-card")), type + " mounts with its saved catalog");
            const tree = type === "MiniMaxH3ProjectAssetTree";
            check(getComputedStyle(node.root).display === (tree ? "grid" : "flex"), type + " uses its own layout");
            check(Boolean(node.root.querySelector(".h3pa-source-col")) === tree, "Tree column is opt-in");
            check(Boolean(node.root.querySelector(".h3pa-lineage-display")) === tree, "Related assets strip is opt-in");
            check((typeof node.dom.computeLayoutSize === "function") === tree, "New resize floor does not alter the legacy node");
            const toolbar = node.root.querySelector(":scope > .h3pa-toolbar");
            const status = node.root.querySelector(":scope > .h3pa-status");
            check(status.getBoundingClientRect().top >= toolbar.getBoundingClientRect().bottom,
                type + " project status must stay below the Run-name toolbar");
            if (tree) {
                check(getComputedStyle(toolbar).gridArea === "top", "Tree toolbar has its own grid area");
                const extraRow = document.createElement("div"); extraRow.className = "h3pa-row";
                node.root.append(extraRow);
                check(getComputedStyle(extraRow).gridArea !== "top", "Other rows cannot occupy the toolbar area");
                extraRow.remove();
                check(node.dom.computeLayoutSize().minWidth > 700, "Tree node advertises its width floor");
                const toggle = node.root.querySelector(".h3pa-tree-toggle"); check(Boolean(toggle), "Source exposes its edits");
                toggle.click();
                check(Boolean(node.root.querySelector(".h3pa-family-stack")), "Expanding source shows its version family");
            } else {
                check(node.root.querySelector(".h3pa-carousel").parentElement === node.root, "Original carousel strip placement is unchanged");
                check(node.root.querySelector(".h3pa-carousel").querySelectorAll(".h3pa-card").length === 3, "Original node keeps separate flat cards");
                check(!node.root.querySelector(".h3pa-tree-node"), "Original node does not get tree grouping");
            }
            check(node._h3ProjectAssetCurrentProject() === "synthetic", "Both nodes expose authoritative project identity");
            const saved = node.widgets.find(w => w.name === "catalog_json").value;
            node.onConfigure?.(); await wait(20);
            check(node.widgets.find(w => w.name === "catalog_json").value === saved, "Reload preserves catalog contents");
        }
        check(getComputedStyle(created[0].root).display === "flex", "Mounting trees does not restyle an existing carousel");
    } catch (error) { report.failures.push(error.stack || String(error)); }
    finally { for (const node of created) { node.onRemoved?.(); node.host?.remove(); } }
    document.body.setAttribute("data-report", btoa(JSON.stringify(report)));
}
