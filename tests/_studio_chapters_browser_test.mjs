// Real Studio module and DOM, isolated Chrome profile, synthetic Plan only.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import {spawn} from "node:child_process";

const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "h3-studio-chapters-"));
const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    if (url.pathname === "/") {
        res.setHeader("Content-Type", "text/html; charset=utf-8");
        res.end(`<!doctype html><meta charset="utf-8"><style>
            body{margin:12px;background:#191b20;color:#ddd;font:12px sans-serif}
            .host{width:1100px;height:1000px}
            </style><script type="module">(${browserChecks.toString()})();</script>`);
        return;
    }
    res.setHeader("Content-Type", "text/javascript; charset=utf-8");
    if (["/scripts/app.js", "/scripts/api.js"].includes(url.pathname)) {
        const name = path.basename(url.pathname, ".js");
        res.end(`export const ${name} = window.${name};`); return;
    }
    if (/^\/web\/[\w.-]+\.(mjs|js)$/.test(url.pathname)) {
        try { res.end(fs.readFileSync(new URL(".." + url.pathname, import.meta.url))); return; }
        catch { /* Missing imports surface in the isolated browser. */ }
    }
    res.writeHead(404); res.end();
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
let chrome;
try {
    chrome = spawn(process.env.H3_TEST_BROWSER || "/usr/bin/google-chrome-stable", [
        "--headless", "--disable-gpu", "--no-first-run", "--disable-extensions",
        "--disable-background-networking", "--disable-component-update", "--disable-sync",
        "--user-data-dir=" + path.join(temporary, "profile"), "--virtual-time-budget=12000",
        "--window-size=1200,1150", "--screenshot=" + path.join(temporary, "chapters.png"),
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
    assert.ok(encoded, "Browser did not finish: " + stdout.slice(-2000) + stderr.slice(-1000));
    const report = JSON.parse(Buffer.from(encoded, "base64").toString());
    console.log(report);
    console.log("Isolated screenshot: " + path.join(temporary, "chapters.png"));
    assert.deepEqual(report.failures, []);
} finally { chrome?.kill(); server.close(); }

async function browserChecks() {
    const report = {checks:0, failures:[]};
    const check = (value, message) => { report.checks++; if (!value) throw Error(message); };
    const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
    const waitFor = async predicate => {
        for (let i = 0; i < 150; i++) { if (predicate()) return; await wait(10); }
        throw Error("Timed out waiting for Studio: " + document.body.innerText.slice(-700));
    };
    window.addEventListener("error", event => report.failures.push(event.message));
    window.addEventListener("unhandledrejection", event => report.failures.push(String(event.reason?.stack || event.reason)));
    window.requestAnimationFrame = callback => setTimeout(() => callback(performance.now()), 16);
    window.cancelAnimationFrame = clearTimeout;
    const graph = {_nodes:[], setDirtyCanvas() {}, getNodeById() { return null; }};
    window.app = {graph, configuringGraph:false, registerExtension(value) { window.extension = value; }};
    let requests = 0;
    window.api = Object.assign(new EventTarget(), {
        apiURL:value => value,
        fetchApi() { requests++; throw Error("Unexpected API request"); },
    });
    try {
        await import("/web/h3_chain_plan_studio.js");
        class Node {
            constructor(saved) {
                this.id = graph._nodes.length + 1;
                this.type = this.comfyClass = "MiniMaxH3ChainPlanStudio";
                this.graph = graph; this.inputs = []; this.outputs = []; this.size = [1100, 1000];
                this.properties = saved?.properties ?? {h3_plan_studio_view:"player"};
                this.widgets = Object.entries({plan_json:saved?.plan ?? JSON.stringify({
                    shots:Array.from({length:6}, (_, index) => ({id:`s${index + 1}`, length:243,
                        prompt:[`Synthetic scene ${index + 1}`], seed:String(index + 1)})),
                    chapters:[{id:"opening", title:"Opening", start_scene_id:"s1"},
                        {id:"second", title:"Chapter 2", start_scene_id:"s4"}],
                }), run_name:"", width:960, height:544, working_branch_id:"main",
                context_length:5, default_duration_seconds:5, default_steps:8,
                base_seed:1, continuation_mode:"guide", video_blend_frames:0,
                }).map(([name, value]) => ({name, value}));
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
        let node = new Node(); node.onNodeCreated();
        await waitFor(() => node.root?.querySelectorAll(".h3studio-chapter-fold").length === 2);
        await wait(150);
        let state = node._h3PlanStudioState;
        const plan = () => node.widgets.find(widget => widget.name === "plan_json").value;
        const original = plan();
        const originalSize = JSON.stringify(node.size);
        const cards = () => node.root.querySelectorAll(".h3studio-generated-timeline .h3studio-card");
        const groups = () => node.root.querySelectorAll(".h3studio-generated-timeline .h3studio-chapter-group");
        const click = text => [...node.root.querySelectorAll("button")].find(button => button.textContent === text).click();
        check(cards().length === 6 && groups().length === 0, "Old workflow starts expanded");
        state.editorial.trims = [{scene_id:"s4", in_frame:24, out_frame:144}];
        state.editorial.placements = [{scene_id:"s6", start_frame:1440}];
        document.dispatchEvent(new Event("h3-lora-routes-changed"));
        await wait(50);
        const fullWidth = Number(state.timelineContent.dataset.timelineWidth);
        node.root.querySelector('[data-chapter-id="opening"] .h3studio-chapter-fold').click();
        await wait(50);
        check(cards().length === 3 && groups().length === 1, "Opening becomes one chapter block");
        check(groups()[0].getBoundingClientRect().width <= 161, "Folded chapter is compact");
        check(Number(state.timelineContent.dataset.timelineWidth) < fullWidth, "Folding reduces timeline width");
        node.root.querySelector('[data-chapter-id="second"] .h3studio-chapter-fold').click();
        check(cards().length === 0 && groups().length === 2, "Both chapters can collapse");
        groups()[1].querySelector(".h3studio-chapter-open").click();
        await waitFor(() => node.root.querySelector(".h3studio-chapter-local-track"));
        await wait(50);
        const localTrack = node.root.querySelector(".h3studio-chapter-local-track");
        check(localTrack.querySelectorAll("button").length === 4, "Local track contains three scenes and their black gap");
        check(Number(state.playerSlider.min) === 0 && Number(state.playerSlider.max) < state.timelineSceneEndFrame,
            "Player uses a chapter-local zero-based duration");
        check(Number(state.playerSlider.value) === 0 && state.timelinePosition > 0, "Local zero seeks into chapter 2, not project start");
        state.playerSlider.value = "48"; state.playerSlider.dispatchEvent(new Event("input"));
        check(Math.abs(state.pendingSeek - 3) < 1e-8, "Local seek includes the slipped source in-frame");
        check(Number(state.playerSlider.value) === 48, "Local frame clock retains its zero origin");
        state.playerSlider.value = state.playerSlider.max; state.playerSlider.dispatchEvent(new Event("input"));
        report.end = {index:state.playerIndex, value:state.playerSlider.value, max:state.playerSlider.max,
            position:state.timelinePosition, pending:state.pendingSeek, atEnd:state.playerAtChapterEnd};
        check(state.playerIndex === 5 && Math.abs(Number(state.playerSlider.value) - Number(state.playerSlider.max)) < 1e-6,
            "Scrubbing to chapter end holds its last used frame");
        state.togglePlayerPlayback();
        await wait(80);
        check(state.playerIndex === 3 && Number(state.playerSlider.value) < 24, "Play at chapter end restarts that chapter");
        state.togglePlayerPlayback();
        const editorial = JSON.stringify(state.editorial);
        const saved = JSON.parse(JSON.stringify({plan:plan(), properties:node.properties}));
        node.onRemoved(); node.host.remove();
        node = new Node(saved); node.onNodeCreated();
        await waitFor(() => node.root && groups().length === 2);
        state = node._h3PlanStudioState;
        check(node.root.querySelector(".h3studio-chapter-local-track"), "Focused chapter and folding survive reload");
        check(plan() === original && JSON.stringify(node.size) === originalSize, "Folding preserves Plan and node size");
        // Restore fixture editorial state as the server normally would, without project writes.
        state.editorial = JSON.parse(editorial);
        document.dispatchEvent(new Event("h3-lora-routes-changed"));
        click("Expand chapter");
        check(cards().length === 3 && groups().length === 1, "Expanding restores individual chapter scene editing");
        click("Full timeline"); await wait(50);
        check(!node.root.querySelector(".h3studio-chapter-local-track"), "Full timeline exits chapter-local player");
        check(JSON.stringify(state.editorial) === editorial && plan() === original, "No changes to trims, placements, prompts or seeds");
        check(requests === 0, "No API scans, preview generation or project writes from folding/playback");
        check(groups()[0].querySelector("button").getAttribute("aria-expanded") === "false", "Accessible expand control");
        state.timelineZoomInput.value = "2";
        state.timelineZoomInput.dispatchEvent(new Event("input"));
        await wait(50);
        check(groups()[0].getBoundingClientRect().width <= 161, "Zoom keeps a folded chapter compact");
        const entry = state.timelineEntries.find(entry => entry.chapter?.id === "opening");
        const ruler = state.timelineRuler;
        const rect = ruler.getBoundingClientRect();
        const point = rect.left + (entry.left + entry.width / 2) * rect.width / Number(state.timelineContent.dataset.timelineWidth);
        ruler.dispatchEvent(new PointerEvent("pointerdown", {button:0, pointerId:1, clientX:point}));
        ruler.dispatchEvent(new PointerEvent("pointerup", {button:0, pointerId:1, clientX:point}));
        check(Math.abs(state.timelinePosition - (entry.startSeconds + entry.durationSeconds / 2)) < 1e-6,
            "Compressed main ruler maps pixels to real project time");
        const branchWidget = node.widgets.find(widget => widget.name === "working_branch_id");
        branchWidget.value = "a".repeat(32); node._h3PlanStudioRefresh(); await wait(100);
        check(groups().length === 0 && !node.root.querySelector(".h3studio-chapter-local-track"),
            "Another working branch starts expanded and unfocused");
        branchWidget.value = "main"; node._h3PlanStudioRefresh(); await wait(100);
        check(groups().length === 1, "Switching back restores this branch's folded chapters");
        check(plan() === original && requests === 0, "Chapter UI state adds no project writes or loading requests");
        // Leave an informative screenshot: compact opening, expanded second chapter, local player.
        groups()[0].querySelector(".h3studio-chapter-open").click(); await wait(50);
        clearInterval(state.pollTimer); clearInterval(state.checkpointTimer);
    } catch (error) { report.failures.push(error.stack || String(error)); }
    document.body.setAttribute("data-report", btoa(JSON.stringify(report)));
}
