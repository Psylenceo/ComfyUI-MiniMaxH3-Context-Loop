// Real Studio, synthetic node-editor input and an isolated browser; no ComfyUI server.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import {spawn} from "node:child_process";

const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "h3-studio-prompt-sync-"));
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
        "--headless", "--disable-gpu", "--no-first-run", "--disable-extensions",
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
    const waitFor = async predicate => {
        for (let i = 0; i < 150; i++) { if (predicate()) return; await wait(10); }
        throw Error("Timed out: " + document.body.innerText.slice(-700));
    };
    window.addEventListener("error", event => report.failures.push(event.message));
    window.addEventListener("unhandledrejection", event => report.failures.push(String(event.reason?.stack || event.reason)));
    window.requestAnimationFrame = callback => setTimeout(() => callback(performance.now()), 16);
    window.cancelAnimationFrame = clearTimeout;
    const graph = {_nodes:[], links:{}, setDirtyCanvas() {}, getNodeById(id) { return this._nodes.find(n => n.id === id); }};
    window.app = {graph, configuringGraph:false, registerExtension(value) { window.extension = value; }};
    let requests = 0;
    window.api = Object.assign(new EventTarget(), {
        apiURL:value => value,
        fetchApi() { requests++; throw Error("Unexpected API request"); },
    });
    const settings = () => ({plan_json:JSON.stringify({shots:[
        {id:"s1", prompt:["Original scene"], length:243, seed:"1"},
        {id:"s2", prompt:["Second scene"], length:243, seed:"2"},
    ]}), run_name:"", width:960, height:544, working_branch_id:"main",
        context_length:5, default_duration_seconds:5, default_steps:8,
        base_seed:1, continuation_mode:"guide", video_blend_frames:0});
    const widgets = () => Object.entries(settings()).map(([name, value]) => ({name, value}));
    try {
        await import("/web/h3_chain_plan_studio.js");
        class Node {
            constructor(owner) {
                this.id = graph._nodes.length + 1;
                this.type = this.comfyClass = "MiniMaxH3ChainPlanStudio";
                this.graph = graph; this.inputs = []; this.outputs = []; this.size = [1100, 1000];
                this.properties = {h3_plan_studio_view:"scene"}; this.widgets = widgets();
                if (owner) {
                    const link = this.id;
                    graph.links[link] = {origin_id:owner.id, target_id:this.id};
                    this.inputs.push({name:"plan", type:"H3_CHAIN_PLAN", link});
                }
                graph._nodes.push(this);
            }
            setSize(value) { this.size = value; }
            addDOMWidget(name, type, root) {
                this.root = root; this.host = document.createElement("div");
                this.host.className = "host"; this.host.append(root); document.body.append(this.host);
                return {element:root};
            }
        }
        await window.extension.beforeRegisterNodeDef(Node, {name:"MiniMaxH3ChainPlanStudio"});
        for (const connected of [false, true]) {
            let owner;
            if (connected) {
                owner = {id:graph._nodes.length + 1, type:"MiniMaxH3ChainPlan", widgets:widgets(), graph};
                graph._nodes.push(owner);
            }
            const node = new Node(owner); node.onNodeCreated();
            await waitFor(() => node.root?.querySelector(".h3studio-prompt"));
            await wait(100);
            const state = node._h3PlanStudioState;
            clearInterval(state.checkpointTimer);
            const planWidget = (owner ?? node).widgets.find(widget => widget.name === "plan_json");
            const shot = state.plan.shots[0], plan = state.plan;
            const field = node.root.querySelector(".h3studio-prompt");
            const timeline = state.timelineContent;
            const player = node.root.querySelector("video");
            const editor = document.createElement("textarea");
            document.body.append(editor); editor.focus();
            editor.addEventListener("input", () => {
                const live = JSON.parse(planWidget.value);
                live.shots[0].prompt = [editor.value];
                // The notification is deliberately withheld beyond the poll:
                // this reproduces the immediate-write/debounced-sync race.
                planWidget.value = JSON.stringify(live);
            });
            for (const text of ["a", "ab", "abc"]) {
                editor.value = text; editor.setSelectionRange(text.length, text.length);
                editor.dispatchEvent(new Event("input", {bubbles:true}));
                await wait(550);
                check(state.plan === plan && state.plan.shots[0] === shot, "Polling preserves Plan/shot object identity");
                check(node.root.querySelector(".h3studio-prompt") === field && field.value === text, "Polling updates prompt without rebuilding the field");
                check(state.timelineContent === timeline && node.root.querySelector("video") === player, "Polling leaves timeline/player mounted");
                check(document.activeElement === editor && editor.selectionStart === text.length, "Typing keeps editor focus and caret");
                if (owner) node._h3PromptCompanionSetScenePrompt(owner, 0, "stale broadcast");
                check(field.value === text, "Delayed notification cannot replace the live prompt");
            }
            // Another scene's concurrent prompt is copied too, without losing
            // existing form closures or requiring a separate notification.
            const live = JSON.parse(planWidget.value); live.shots[1].prompt = ["Another edit"];
            planWidget.value = JSON.stringify(live); await wait(550);
            check(state.plan.shots[1].prompt[0] === "Another edit" && state.timelineContent === timeline, "All prompt fields are synchronized together");
            field.value = "Edit in Studio"; field.dispatchEvent(new Event("input", {bubbles:true}));
            check(JSON.parse(planWidget.value).shots[0].prompt[0] === "Edit in Studio", "Studio input still writes through the original shot closure");
            live.shots[0].seed = "123"; planWidget.value = JSON.stringify(live); await wait(550);
            check(state.plan.shots[0].seed === "123" && node.root.querySelector(".h3studio-prompt") !== field, "Seed changes still refresh the complete form");
            const view = label => [...node.root.querySelectorAll("button")].find(button => button.textContent === label).click();
            view("JSON");
            const jsonArea = node.root.querySelector(".h3studio-json");
            live.shots[0].prompt = ["Update JSON view"]; planWidget.value = JSON.stringify(live); await wait(550);
            check(node.root.querySelector(".h3studio-json") === jsonArea
                && JSON.parse(jsonArea.value).shots[0].prompt[0] === "Update JSON view", "Clean JSON view updates in place");
            const draft = jsonArea.value + " ";
            jsonArea.value = draft; editor.focus();
            live.shots[0].prompt = ["Leave draft alone"]; planWidget.value = JSON.stringify(live); await wait(550);
            check(jsonArea.value === draft && node.root.querySelector(".h3studio-json") === jsonArea, "Unapplied JSON draft survives prompt updates even when unfocused");
            view("Scene prompt");
            const valid = planWidget.value;
            planWidget.value = "{"; await wait(550);
            check(Boolean(node.root.querySelector(".h3studio-error")), "Invalid JSON is reported");
            planWidget.value = valid; await wait(550);
            check(Boolean(node.root.querySelector(".h3studio-prompt")), "Restoring identical valid JSON recovers the form after an error");
            // Integration review: basic drafts must be consumed by the same
            // poll as H3 prompts, before a debounced companion push arrives.
            const draftUpdate = JSON.parse(planWidget.value);
            draftUpdate.shots[0].basic_prompt = "New basic draft from another editor";
            planWidget.value = JSON.stringify(draftUpdate);
            await wait(550);
            report.checks += 3;
            if (state.plan.shots[0].basic_prompt !== draftUpdate.shots[0].basic_prompt) {
                report.failures.push(`connected=${connected}: polling did not adopt basic_prompt`);
            }
            if (node.root.querySelector(".h3studio-basic-prompt").value !== draftUpdate.shots[0].basic_prompt) {
                report.failures.push(`connected=${connected}: polling left the basic draft textarea stale`);
            }
            const currentPrompt = node.root.querySelector(".h3studio-prompt");
            currentPrompt.value = "H3-only edit after draft update";
            currentPrompt.dispatchEvent(new Event("input", {bubbles:true}));
            if (JSON.parse(planWidget.value).shots[0].basic_prompt !== draftUpdate.shots[0].basic_prompt) {
                report.failures.push(`connected=${connected}: Studio H3 edit overwrote the newer saved basic draft`);
            }
            if (owner) {
                node._h3PromptCompanionSetBasicPrompt(owner, 0, "stale draft broadcast");
                check(state.plan.shots[0].basic_prompt === draftUpdate.shots[0].basic_prompt,
                    "Delayed basic notification cannot replace the live draft");
            }
            const clearedDraft = JSON.parse(planWidget.value);
            delete clearedDraft.shots[0].basic_prompt;
            clearedDraft.shots[1].basic_prompt = "Other scene draft";
            planWidget.value = JSON.stringify(clearedDraft); await wait(550);
            check(!Object.hasOwn(state.plan.shots[0], "basic_prompt")
                && node.root.querySelector(".h3studio-basic-prompt").value === "",
                "Clearing a basic draft is reflected in state and the existing field");
            check(state.plan.shots[1].basic_prompt === "Other scene draft",
                "Inactive scene basic drafts synchronize too");
            const immediate = JSON.parse(planWidget.value);
            immediate.shots[0].basic_prompt = "Saved just before Studio edits";
            planWidget.value = JSON.stringify(immediate);
            currentPrompt.value = "H3 edit before polling";
            currentPrompt.dispatchEvent(new Event("input", {bubbles:true}));
            check(JSON.parse(planWidget.value).shots[0].basic_prompt === immediate.shots[0].basic_prompt,
                "A write before polling also preserves the external basic draft");
            const ownDraft = node.root.querySelector(".h3studio-basic-prompt");
            check(ownDraft.value === immediate.shots[0].basic_prompt,
                "The pre-poll write also updates the displayed basic draft");
            ownDraft.value = "Intentional local draft edit";
            ownDraft.dispatchEvent(new Event("input", {bubbles:true}));
            check(JSON.parse(planWidget.value).shots[0].basic_prompt === ownDraft.value,
                "Studio can still intentionally replace its basic draft");
            check(requests === 0, "Typing triggers no backend refresh requests");
            node.onRemoved?.(); editor.remove(); node.host.remove();
        }
    } catch (error) { report.failures.push(error.stack || String(error)); }
    document.body.setAttribute("data-report", btoa(JSON.stringify(report)));
}
