// Real review DOM in an isolated layout host; no live ComfyUI or user projects.
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import {spawn, spawnSync} from "node:child_process";

const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "h3-hunt-layout-"));
const preview = path.join(temporary, "preview.mp4");
const encodedPreview = spawnSync("ffmpeg", ["-v", "error", "-f", "lavfi", "-i",
    "testsrc2=size=960x544:rate=24", "-t", "2", "-c:v", "libx264", "-preset", "ultrafast",
    "-pix_fmt", "yuv420p", "-movflags", "+faststart", preview]);
assert.equal(encodedPreview.status, 0, encodedPreview.stderr?.toString());
const html = `<!doctype html><meta charset="utf-8"><style>
body{margin:12px;background:#15161a;color:#ddd}.node{background:#393939;box-sizing:border-box;padding:10px}
.native{height:300px}.review-host{position:relative}
</style><script type="module">(${browserChecks.toString()})();</script>`;
const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    if (url.pathname === "/") { res.setHeader("Content-Type", "text/html"); res.end(html); return; }
    if (url.pathname === "/view") { res.setHeader("Content-Type", "video/mp4"); res.end(fs.readFileSync(preview)); return; }
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
        active:true, phase:"waiting", selected:null, low_steps:20, high_steps:5, created_at:123, candidates:[]};
    const takes = count => Array.from({length:count}, (_, i) => ({ordinal:i+1, seed:String(i+1), preview:`test/take_${i+1}.mp4`}));
    batch.candidates = takes(1);
    window.app = {graph, registerExtension(value) { window.extension = value; }};
    window.api = {apiURL:url => url, addEventListener() {}, async fetchApi() {
        return {ok:true, json:async () => ({batches:[batch]})};
    }};
    try {
        await import("/web/h3_selflift_hunt.js");
        class Node {
            constructor(size) {
                this.size = size; this.graph = graph; this.properties = {};
                this.widgets = [{name:"review_enabled",value:true}];
            }
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
        check(root.offsetHeight === 680, "Panel fills the enlarged node instead of leaving a dead strip");
        check(root.scrollHeight <= root.clientHeight, "One complete take and help text fit without scrolling");
        check(root.lastElementChild.getBoundingClientRect().bottom <= root.getBoundingClientRect().bottom,
            "Help text is visible, not clipped at the bottom");
        const player = root.querySelector(".h3sh-player");
        const initialPlayerHeight = player.clientHeight;
        check(!root.querySelector("details").open, "Long help is tucked away by default");
        check(getComputedStyle(root).backgroundColor === "rgb(24, 26, 32)", "Review Gate palette works without mounting a normal gate");
        node.setSize([840, 1200]);
        check(root.offsetHeight === 880, "Panel grows when dragged taller");
        check(player.clientHeight > initialPlayerHeight, "Player uses the enlarged node's spare height");
        check(root.querySelector("video") === video, "Resize does not rebuild or interrupt playback");
        node.setSize([620, 900]);
        check(root.offsetHeight === 580, "Panel also follows a deliberate shrink");
        check(root.scrollWidth <= root.clientWidth, "No horizontal scrollbar after narrowing");
        const previewButton = root.querySelector('.h3sh-upscale-preview');
        const approvalButton = root.querySelector('.h3sh-approve');
        check(previewButton.textContent === "Preview upscale" && !previewButton.disabled,
            "An unapproved take has an explicit upscale-preview action");
        const previewBounds = previewButton.getBoundingClientRect(), approvalBounds = approvalButton.getBoundingClientRect();
        check(Math.abs(previewBounds.top - approvalBounds.top) < 1 && approvalBounds.left - previewBounds.right <= 7,
            "Preview and approval buttons stay adjacent on one row at 620px");
        const markBounds = root.querySelector('.h3sh-mark').getBoundingClientRect();
        const mainBounds = root.querySelector('.h3sh-main').getBoundingClientRect();
        check(Math.abs(markBounds.top - mainBounds.top) < 1 && markBounds.bottom < previewBounds.top,
            "Mark and main controls stay paired directly above preview and finish");
        batch.candidates = takes(12); node._h3SelfLiftHunt.render();
        check(root.querySelectorAll("video").length === 1, "Twelve takes use one focused player");
        check(root.querySelectorAll(".h3sh-dot").length === 12, "Every take has a navigation dot");
        check(root.scrollHeight <= root.clientHeight, "More takes do not add rows of players or require scrolling");
        root.querySelector('button[data-ordinal="2"]').click();
        check(video.src.includes("take_2.mp4"), "Dot navigation changes only the viewed source");
        check(batch.selected === null, "Browsing never approves a take");
        const viewedSrc = video.src;
        batch.candidates = takes(13); node._h3SelfLiftHunt.render();
        check(video.src === viewedSrc && node.properties.h3_selflift_preview.ordinal === 2,
            "New candidates leave the viewed take alone");
        const selected = root.querySelector('.h3sh-approve');
        batch.phase = "high"; batch.selected = 2; node._h3SelfLiftHunt.render();
        check(selected.disabled && root.querySelector('.h3sh-chosen').textContent === "Main take · included in upscale",
            "Chosen take and high-pass lock are visible");
        root.querySelector('.h3sh-nav').dispatchEvent(new KeyboardEvent("keydown", {key:"ArrowRight", bubbles:true}));
        check(video.src.includes("take_3.mp4") && batch.selected === 2,
            "Keyboard browsing remains available while upscale selection is locked");
        check(root.querySelector('button[data-ordinal="2"]').dataset.chosen === "true",
            "Chosen marker does not follow browsing");
        batch.cleanup_error = "[Errno 5] Input/output error: source.safetensors";
        node._h3SelfLiftHunt.render();
        const cleanupWarning = root.querySelector('.h3sh-cleanup-warning');
        check(cleanupWarning.checkVisibility() && cleanupWarning.textContent.includes('Clean saved takes to retry'),
            "Persistent cleanup failure is visible and explains retrying without regeneration");
        delete batch.cleanup_error; node._h3SelfLiftHunt.render();
        check(!cleanupWarning.checkVisibility(), "Healthy batch has no stale cleanup warning");
        // The drag grip uses layout pixels, not canvas-zoomed screen pixels.
        node.host.style.transformOrigin = "top left"; node.host.style.transform = "scale(.5)";
        const grip = root.querySelector('.h3sh-grip');
        // Synthetic DOM events do not register a real hardware pointer in
        // Chrome; stub only capture, leaving the resize math/DOM handlers real.
        let capturedPointer = null;
        grip.setPointerCapture = id => { capturedPointer = id; };
        grip.releasePointerCapture = id => { if (capturedPointer === id) capturedPointer = null; };
        const beforeDrag = player.offsetHeight;
        grip.dispatchEvent(new PointerEvent("pointerdown", {pointerId:7,clientY:100,bubbles:true}));
        grip.dispatchEvent(new PointerEvent("pointermove", {pointerId:7,clientY:150,bubbles:true}));
        grip.dispatchEvent(new PointerEvent("pointerup", {pointerId:7,clientY:150,bubbles:true}));
        check(Math.abs(node.properties.h3_selflift_preview_height - beforeDrag - 100) <= 1,
            "Preview height is saved correctly at half canvas zoom");
        check(capturedPointer === null, "Resize releases pointer capture");
        grip.dispatchEvent(new MouseEvent("dblclick", {bubbles:true}));
        check(!Object.hasOwn(node.properties,"h3_selflift_preview_height") && player.style.height === "",
            "Double-click restores automatic node-fit height");
        node.host.style.transform = "";
        const review = node.widgets[0];
        check(review.label === "Review gate", "The gate toggle has a readable label");
        review.value = false; review.callback();
        check(root.querySelector('.h3sh-gate-notice').checkVisibility(), "Turning the gate off explains automatic mode");
        batch.review_enabled = false;
        batch.candidates = [{ordinal:1,seed:"1",preview:null}]; batch.selected = 1;
        node._h3SelfLiftHunt.render();
        check(!player.checkVisibility() && !video.getAttribute("src"), "Previewless automatic runs hide and release the video");
        check(root.querySelector('.h3sh-empty').checkVisibility(), "Previewless recovery has an explanation");
        check(root.querySelector('.h3sh-candidates').checkVisibility() && selected.disabled,
            "Automatic take metadata is visible without inviting manual approval");
        check(root.scrollHeight <= root.clientHeight, "Automatic mode fits the panel without empty video space");
        check(root.querySelector('.h3sh-status').textContent.includes("upscaling take 1 automatically"),
            "Automatic upscale reports progress without an approval request");
        delete batch.review_enabled;
        review.value = true; review.callback();
        batch.candidates = takes(2); batch.phase = "low"; batch.current = 3; batch.selected = null;
        node._h3SelfLiftHunt.render(); node.setSize([840, 1000]);
        check(player.checkVisibility() && !root.querySelector('.h3sh-gate-notice').checkVisibility(),
            "Turning review back on restores the normal preview layout");
        root.querySelector('button[data-ordinal="2"]').click();
        // Keep a separate mounted panel for the screenshot and test disposal on
        // another instance, leaving the primary player intact in the image.
        const removed = new Node([560,780]); removed.onNodeCreated();
        await new Promise(resolve => setTimeout(resolve, 80));
        removed.onRemoved();
        check(removed.root.querySelector("video").getAttribute("src") === null, "Removed nodes release preview sources");
        removed.host.remove();
        await new Promise(resolve => setTimeout(resolve, 200));
    } catch (error) { report.failures.push(error.stack || String(error)); }
    document.body.setAttribute("data-report", btoa(JSON.stringify(report)));
}
