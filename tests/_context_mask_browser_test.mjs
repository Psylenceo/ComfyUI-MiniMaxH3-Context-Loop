// Optional real-Chrome smoke test. Synthetic video, isolated browser profile,
// no ComfyUI server, project data or generation queue is touched.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import {spawn, spawnSync} from "node:child_process";

const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "h3-context-mask-browser-"));
const profile = path.join(temporary, "chrome-profile");
const movie = path.join(temporary, "synthetic.mp4");
const generated = spawnSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
    "testsrc2=s=640x352:r=24:d=1", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
    "-movflags", "+faststart", movie]);
assert.equal(generated.status, 0, generated.stderr?.toString());
const server = http.createServer((req, res) => {
    const name = new URL(req.url, "http://localhost").pathname;
    if (["/h3_context_mask_core.mjs", "/h3_context_mask_editor.mjs"].includes(name)) {
        res.setHeader("Content-Type", "text/javascript");
        res.end(fs.readFileSync(new URL(`../web${name}`, import.meta.url))); return;
    }
    if (name === "/synthetic.mp4") {
        res.setHeader("Content-Type", "video/mp4"); res.end(fs.readFileSync(movie)); return;
    }
    res.setHeader("Content-Type", "text/html; charset=utf-8");
    res.end(`<!doctype html><html><head><title>Context mask test</title><style>
        body {background:#20242a;color:#ddd;font:14px sans-serif;margin:24px}
        #host {width:640px} video {display:block;width:100%}
        button,input {margin:6px} button {background:#343b44;color:#ddd;padding:8px;border:1px solid #778}
        .h3studio-context-mask-tools:not([hidden]) {display:flex;flex-wrap:wrap;gap:8px;align-items:center}
        .h3studio-context-mask-tools label {display:flex;flex-direction:column;min-width:100px;flex:1}
        .h3studio-context-mask-tools input {width:100%}
        .h3studio-context-help {padding:6px;color:#abb}
    </style></head><body><h2>Picture block · 5 frames · Masked AV</h2><div id="host"></div>
    <script type="module">
        import {contextMaskEditor} from '/h3_context_mask_editor.mjs';
        window.saved=JSON.parse(localStorage.getItem('mask') || 'null'); window.saveCount=0;
        const video=document.createElement('video');video.controls=true;video.muted=true;
        video.src='/synthetic.mp4';video.preload='auto';
        document.querySelector('#host').append(contextMaskEditor(video,saved,{enabled:true,onChange(mask) {
            window.saved=mask;window.saveCount++;localStorage.setItem('mask',JSON.stringify(mask));
        }}));
    </script></body></html>`);
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
let chrome, socket;
try {
    chrome = spawn(process.env.CHROME_BINARY || "google-chrome", ["--headless=new", "--no-first-run",
        "--disable-extensions", "--disable-gpu", "--disable-background-networking", "--disable-dev-shm-usage",
        "--window-size=1000,900", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"],
    {stdio:["ignore", "ignore", "pipe"]});
    const debuggerUrl = await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(Error("Private Chrome startup timed out")), 10000);
        chrome.once("error", (error) => { clearTimeout(timeout); reject(error); });
        chrome.stderr.on("data", chunk => {
            const match = chunk.toString().match(/DevTools listening on (ws:\/\/\S+)/);
            if (match) { clearTimeout(timeout); resolve(match[1]); }
        });
    });
    socket = new WebSocket(debuggerUrl);
    await new Promise((resolve, reject) => {
        socket.addEventListener("open", resolve, {once:true}); socket.addEventListener("error", reject, {once:true});
    });
    let sequence = 0; const pending = new Map();
    socket.addEventListener("message", event => {
        const value = JSON.parse(event.data), task = pending.get(value.id);
        if (task) { pending.delete(value.id); value.error ? task.reject(Error(JSON.stringify(value.error))) : task.resolve(value.result); }
    });
    const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
        const id = ++sequence; pending.set(id, {resolve, reject}); socket.send(JSON.stringify({id, method, params, sessionId}));
    });
    const {targetId} = await send("Target.createTarget", {url:"about:blank"});
    const {sessionId} = await send("Target.attachToTarget", {targetId, flatten:true});
    const evaluate = async expression => {
        const result = await send("Runtime.evaluate", {expression, awaitPromise:true, returnByValue:true}, sessionId);
        if (result.exceptionDetails) throw Error(JSON.stringify(result.exceptionDetails));
        return result.result.value;
    };
    const waitFor = async expression => {
        for (let attempt = 0; attempt < 100; attempt++) {
            if (await evaluate(`Boolean(${expression})`)) return;
            await new Promise(resolve => setTimeout(resolve, 50));
        }
        throw Error(`Timed out: ${expression}`);
    };
    await send("Page.navigate", {url:`http://127.0.0.1:${server.address().port}/`}, sessionId);
    await waitFor("document.querySelector('video')?.readyState>=2 && !document.querySelector('button')?.disabled");
    assert.equal(await evaluate("saveCount"), 0);
    const button = text => evaluate(`[...document.querySelectorAll('button')].find(x=>x.textContent.includes(${JSON.stringify(text)})).click()`);
    await button("Weaken context");
    assert.equal(await evaluate("document.querySelector('video').controls"), false);
    const rect = await evaluate("document.querySelector('canvas').getBoundingClientRect().toJSON()");
    const videoRect = await evaluate("document.querySelector('video').getBoundingClientRect().toJSON()");
    assert.equal(rect.height, videoRect.height); assert.equal(rect.width, videoRect.width);
    const pointer = (type, x) => send("Input.dispatchMouseEvent", {type, x:rect.x + x,
        y:rect.y + 128, button:type === "mouseMoved" ? "none" : "left",
        buttons:type === "mouseReleased" ? 0 : 1, clickCount:1}, sessionId);
    await pointer("mousePressed", 128); await pointer("mouseMoved", 256);
    assert.equal(await evaluate("saveCount"), 0, "no Plan writes while dragging");
    await pointer("mouseReleased", 256);
    await waitFor("saveCount===1");
    const authored = await evaluate("saved");
    assert.ok(authored.cells.some(Boolean));
    const screenshot = await send("Page.captureScreenshot", {format:"png"}, sessionId);
    fs.writeFileSync(path.join(temporary, "mask.png"), Buffer.from(screenshot.data, "base64"));
    await send("Page.reload", {}, sessionId);
    await waitFor("document.querySelector('video')?.readyState>=2 && document.querySelector('button')?.textContent.includes('mask saved')");
    assert.deepEqual(await evaluate("saved"), authored);
    assert.equal(await evaluate("saveCount"), 0, "reopening must not dirty the Plan");
    assert.equal(await evaluate("document.querySelector('.h3studio-context-mask-tools').hidden"), true);
    await button("Weaken context"); await button("Reset mask");
    assert.equal(await evaluate("saved"), null);
    await button("Undo stroke"); assert.deepEqual(await evaluate("saved"), authored);
    await button("Done painting"); assert.equal(await evaluate("document.querySelector('video').controls"), true);
    console.log(`Real Chrome: fixed mask painting, overlay alignment, save-on-release, reload, reset/undo and video controls pass. Screenshot: ${temporary}/mask.png`);
} finally {
    socket?.close();
    if (chrome && chrome.exitCode === null) { chrome.kill("SIGTERM"); await new Promise(resolve => chrome.once("exit", resolve)); }
    await new Promise(resolve => server.close(resolve));
    // Only the isolated profile created by this test; retain the tiny fixture/screenshot.
    fs.rmSync(profile, {recursive:true, force:true});
}
