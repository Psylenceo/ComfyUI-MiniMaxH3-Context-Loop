// Real Carousel, mocked server and a delayed mocked provider; no ComfyUI
// server and no real provider calls. Regression for a generated description
// arriving while the user is typing: the review panel must not reset the
// live draft, and later autosaves must keep every typed sentence.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import {spawn} from "node:child_process";

const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "h3-description-race-"));
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
        "--headless", "--window-size=1600,1000", "--disable-gpu", "--no-first-run", "--disable-extensions",
        "--disable-background-networking", "--disable-component-update", "--disable-sync",
        "--user-data-dir=" + path.join(temporary, "profile"), "--virtual-time-budget=30000",
        "--dump-dom", `http://127.0.0.1:${server.address().port}/`,
    ], {stdio:["ignore", "pipe", "pipe"]});
    let stdout = "", stderr = "";
    chrome.stdout.on("data", chunk => stdout += chunk);
    chrome.stderr.on("data", chunk => stderr += chunk);
    const code = await new Promise((resolve, reject) => {
        const timer = setTimeout(() => { chrome.kill(); reject(Error("Browser test timed out")); }, 60000);
        chrome.once("error", error => { clearTimeout(timer); reject(error); });
        chrome.once("exit", code => { clearTimeout(timer); resolve(code); });
    });
    assert.equal(code, 0, stderr);
    const encoded = stdout.match(/data-report="([^"]+)"/)?.[1];
    assert.ok(encoded, "Browser did not finish: " + stdout.slice(-1500) + stderr.slice(-500));
    const report = JSON.parse(Buffer.from(encoded, "base64").toString());
    console.log(report);
    assert.deepEqual(report.failures, []);
} finally { chrome?.kill(); server.close(); fs.rmSync(temporary, {recursive:true, force:true}); }

async function browserChecks() {
    const report = {checks:0, failures:[]};
    const check = (value, message) => { report.checks++; if (!value) throw Error(message); };
    const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
    const until = async (predicate, message, limit = 200) => {
        for (let i = 0; i < limit && !predicate(); i++) await wait(10);
        check(predicate(), message);
    };
    window.addEventListener("error", event => report.failures.push(event.message));
    window.addEventListener("unhandledrejection", event => report.failures.push(String(event.reason?.stack || event.reason)));
    const catalog = {project:"synthetic", revision:"test", folders:[], assets:[
        {id:"cabinet", tag:"cabinet", kind:"image", role:"picture", enabled:true,
            tag_type:"object", description:"Original.", input_path:"h3_projects/synthetic/cabinet.png"},
    ]};
    const saves = [];
    const describeCalls = [];
    let releaseDescribe;
    const graph = {_nodes:[], links:{}, setDirtyCanvas() {}, getNodeById(id) { return this._nodes.find(n => n.id === id); }};
    const settings = {
        "MiniMaxH3ContexLoop.PromptOptimizer.Backend": "direct",
        "MiniMaxH3ContexLoop.PromptOptimizer.ApiFormat": "openai",
        "MiniMaxH3ContexLoop.PromptOptimizer.ApiUrl": "https://api.openai.com/v1",
        "MiniMaxH3ContexLoop.PromptOptimizer.Model": "mock-model",
        "MiniMaxH3ContexLoop.PromptOptimizer.AllowMedia": true,
    };
    window.app = {graph, ui:{settings:{getSettingValue: id => settings[id]}},
        registerExtension(extension) { window.extension = extension; }};
    const json = (value, status = 200) => ({ok:status < 400, status, json:async () => structuredClone(value)});
    window.api = {apiURL: value => value, addEventListener() {}, removeEventListener() {}, async fetchApi(route, options = {}) {
        if (route.includes("/project-ownership")) return json({locking_enabled:false});
        if (route.endsWith("/project-assets/describe")) {
            describeCalls.push(JSON.parse(options.body));
            // The mocked provider answers only when the test releases it.
            await new Promise(resolve => { releaseDescribe = resolve; });
            return json({description:"is a generated cabinet."});
        }
        if (route.endsWith("/project-assets/update")) {
            const body = JSON.parse(options.body);
            const asset = catalog.assets.find(item => item.id === body.asset_id);
            if ("expected_description" in body.changes
                    && body.changes.expected_description.trim() !== String(asset.description ?? "").trim()) {
                return json({error:"changed", code:"h3_project_asset_conflict"}, 409);
            }
            if ("description" in body.changes) asset.description = body.changes.description;
            saves.push(body.changes.description);
            return json({catalog, asset});
        }
        if (route.endsWith("/projects")) return json({projects:[]});
        return json(catalog);
    }};
    let node;
    try {
        await import("/web/h3_project_asset_manager.js");
        class Node {
            constructor() {
                this.type = this.comfyClass = "MiniMaxH3ProjectAssetManager"; this.id = 1;
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
        await window.extension.beforeRegisterNodeDef(Node, {name:"MiniMaxH3ProjectAssetManager"});
        node = new Node(); node.onNodeCreated();
        await until(() => node.root?.querySelector(".h3pa-card"), "Carousel mounts");
        node.root.querySelector(".h3pa-card").click();
        const field = () => node.root.querySelector("textarea.h3pa-description");
        await until(() => field(), "Selecting the asset shows its description");
        check(field().value === "Original.", "The saved description is shown");

        // 1. Generate from the saved text.
        const generate = [...node.root.querySelectorAll("button")]
            .find(item => item.textContent === "Generate description");
        check(Boolean(generate), "Generate description is available");
        generate.click();
        await until(() => describeCalls.length === 1 && releaseDescribe, "Generate sends one describe request");
        check(describeCalls[0].allow_media === true, "Media access was explicitly enabled for this request");

        // 2. Type while it runs, without waiting for autosave.
        const first = "Original. Manual first sentence.";
        field().focus();
        field().value = first;
        field().setSelectionRange(first.length, first.length);
        field().dispatchEvent(new InputEvent("input", {bubbles:true, inputType:"insertText"}));

        // 3. The provider answers before the 1.2-second autosave fires.
        releaseDescribe();
        await until(() => node.root.querySelector(".h3pa-description-review"),
            "A late result is held for review");
        check(saves.length === 0, "Nothing is saved before the autosave delay");
        check(field().value === first, "Showing the review keeps the live draft in the field");
        check(document.activeElement === field(), "Showing the review keeps focus in the field");
        check(field().selectionStart === first.length, "Showing the review keeps the caret position");
        check(node.root.querySelector(".h3pa-description-review").textContent.includes("is a generated cabinet."),
            "The review panel shows the generated text");

        // 4. Autosave keeps the first sentence and the field still shows it.
        await until(() => saves.length === 1, "Autosave fires after the delay", 300);
        check(saves[0] === first, "Autosave saves the first sentence");
        check(field().value === first, "The field still shows the saved draft");

        // 5. Continue typing; the next save keeps both sentences.
        const second = `${first} Manual second sentence.`;
        field().value = second;
        field().dispatchEvent(new InputEvent("input", {bubbles:true, inputType:"insertText"}));
        await until(() => saves.length === 2, "The second edit autosaves", 300);
        check(saves[1] === second, "The next save keeps the first sentence");
        check(catalog.assets[0].description === second, "The stored description has both sentences");
        check(!saves.includes("is a generated cabinet."), "The generated text was never saved");

        // 6. Discarding the generated text leaves the typed description.
        [...node.root.querySelectorAll(".h3pa-description-review button")]
            .find(item => item.textContent === "Discard").click();
        await until(() => !node.root.querySelector(".h3pa-description-review"), "Discard removes the review");
        check(field().value === second, "Discard keeps the typed description");
        await wait(1500);
        check(saves.length === 2, "No further saves after discard");
    } catch (error) { report.failures.push(error.stack || String(error)); }
    finally { node?.onRemoved?.(); node?.host?.remove(); }
    document.body.setAttribute("data-report", btoa(JSON.stringify(report)));
}
