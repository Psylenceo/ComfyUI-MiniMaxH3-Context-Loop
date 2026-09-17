import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { bindNodeWheel } from "./h3_dom_wheel.mjs";

const panels = new Set();
let pending = null;
let timer = null;
let batches = [];

function visible(panel) {
    return panel.root.isConnected && !document.hidden &&
        (!panel.root.checkVisibility || panel.root.checkVisibility({checkVisibilityCSS:true}));
}

async function refresh() {
    if (pending) return pending;
    pending = (async () => {
        try {
            const response = await api.fetchApi("/h3/selflift/hunts");
            if (!response.ok) throw new Error(`Saved hunts: HTTP ${response.status}`);
            batches = (await response.json()).batches || [];
            for (const panel of panels) if (visible(panel)) panel.render();
        } catch (error) {
            for (const panel of panels) if (visible(panel)) panel.status.textContent = error.message;
        } finally { pending = null; }
    })();
    return pending;
}

function text(tag, value, className = "") {
    const element = document.createElement(tag);
    element.textContent = value;
    element.className = className;
    if (tag === "button") element.type = "button";
    return element;
}

function injectStyles() {
    if (document.getElementById("h3-selflift-review-style")) return;
    const style = text("style", `
        .h3sh-root { box-sizing:border-box; display:flex; flex-direction:column; gap:8px;
            width:100%; height:100%; min-height:0; max-height:100%; overflow:auto; padding:9px;
            border:1px solid #56637e; border-radius:8px; background:#181a20;
            color:#e8eaf0; font:12px/1.35 system-ui,sans-serif; }
        .h3sh-root * { box-sizing:border-box; }
        .h3sh-root > * { flex-shrink:0; min-width:0; }
        .h3sh-root [hidden] { display:none !important; }
        .h3sh-head, .h3sh-meta { display:flex; align-items:center; justify-content:space-between;
            flex-wrap:wrap; gap:6px; }
        .h3sh-title { font-weight:750; color:#a9c2ff; }
        .h3sh-badge { color:#9ca8bc; font-size:11px; }
        .h3sh-toolbar { display:flex; flex-wrap:wrap; gap:6px; }
        .h3sh-select { flex:1 1 240px; width:0; min-width:100px; max-width:100%; padding:6px;
            border:1px solid #56637e; border-radius:5px; background:#101218; color:#eef1f7; font:inherit; }
        .h3sh-button { padding:7px 10px; border:1px solid #63708b; border-radius:5px;
            background:#292e3a; color:#eef1f7; cursor:pointer; font:inherit; }
        .h3sh-button:hover { background:#343b4b; }
        .h3sh-root button:disabled { opacity:.42; cursor:not-allowed; }
        .h3sh-root button:focus-visible, .h3sh-root summary:focus-visible,
        .h3sh-root select:focus-visible { outline:2px solid #a9c2ff; outline-offset:2px; }
        .h3sh-clean { border-color:#8a6171; background:#3b252d; }
        .h3sh-root .h3sh-player { flex:1 1 240px; min-height:160px; display:flex; flex-direction:column;
            overflow:hidden; border:1px solid #343b4b; border-radius:6px; background:#08090c; }
        .h3sh-video { width:100%; height:0; flex:1 1 0; min-height:0; display:block; object-fit:contain; }
        .h3sh-grip { height:11px; flex:0 0 11px; cursor:ns-resize; touch-action:none; position:relative;
            border-top:1px solid #343b4b; background:linear-gradient(180deg,#252a35,#171a21); }
        .h3sh-grip::after { content:""; position:absolute; left:calc(50% - 20px); top:4px;
            width:40px; height:2px; border-top:1px solid #7e899f; border-bottom:1px solid #4f586b; }
        .h3sh-grip:hover { background:#313848; }
        .h3sh-candidates { display:flex; flex-direction:column; gap:7px; padding:8px;
            border:1px solid #4c6388; border-radius:6px; background:#172033; }
        .h3sh-nav { display:grid; grid-template-columns:auto minmax(0,1fr) auto; align-items:center; gap:7px; }
        .h3sh-take-title { text-align:center; font-weight:700; color:#a9c2ff; }
        .h3sh-arrow { width:38px; padding:4px; font-size:16px; }
        .h3sh-seed { overflow-wrap:anywhere; color:#b9c4d9; font-size:11px; }
        .h3sh-chosen { color:#91d7af; font-size:11px; }
        .h3sh-dots { display:flex; flex-wrap:wrap; justify-content:center; gap:7px; padding:3px; }
        .h3sh-dot { width:12px; height:12px; padding:0; border:1px solid #71809c; border-radius:50%;
            background:#343b4b; cursor:pointer; }
        .h3sh-dot:hover { background:#526078; }
        .h3sh-dot[aria-pressed="true"] { outline:2px solid #a9c2ff; outline-offset:2px; background:#6f8fd0; }
        .h3sh-dot[data-chosen="true"] { border-color:#70d39c; background:#347a54; }
        .h3sh-approve { width:100%; border-color:#4b9d72; background:#204332; font-weight:650; }
        .h3sh-approve:hover { background:#2b5740; }
        .h3sh-status, .h3sh-notice { margin:0; color:#aeb5c5; white-space:pre-wrap; overflow-wrap:anywhere; }
        .h3sh-notice { color:#f2bd67; }
        .h3sh-empty { margin:0; padding:20px 12px; border:1px dashed #3f4759; border-radius:6px;
            text-align:center; color:#aeb5c5; }
        .h3sh-help { color:#9ca8bc; font-size:11px; }
        .h3sh-help summary { cursor:pointer; }
        .h3sh-help p { margin:7px 0; }
        .h3sh-help a { color:#99bcff; }
    `);
    style.id = "h3-selflift-review-style";
    document.head.appendChild(style);
}

function resizablePlayer(node, player, grip) {
    const property = "h3_selflift_preview_height";
    node.properties ||= {};
    function setHeight(height, persist = false) {
        const value = Math.round(Math.max(160, Math.min(1200, Number(height) || 300)));
        player.style.flex = "0 0 auto";
        player.style.height = `${value}px`;
        if (persist) node.properties[property] = value;
    }
    if (Number.isFinite(node.properties[property])) setHeight(node.properties[property]);
    let drag = null;
    grip.addEventListener("pointerdown", event => {
        event.preventDefault();
        const height = player.offsetHeight;
        drag = {id:event.pointerId, y:event.clientY, height,
            scale:height > 0 ? player.getBoundingClientRect().height / height || 1 : 1};
        grip.setPointerCapture?.(event.pointerId);
    });
    grip.addEventListener("pointermove", event => {
        if (!drag || drag.id !== event.pointerId) return;
        event.preventDefault();
        setHeight(drag.height + (event.clientY - drag.y) / drag.scale);
    });
    function finish(event) {
        if (!drag || drag.id !== event.pointerId) return;
        setHeight(player.offsetHeight, true);
        drag = null;
        grip.releasePointerCapture?.(event.pointerId);
        node.graph?.setDirtyCanvas?.(true, true);
    }
    grip.addEventListener("pointerup", finish);
    grip.addEventListener("pointercancel", finish);
    grip.addEventListener("dblclick", event => {
        event.preventDefault();
        delete node.properties[property];
        player.style.flex = "";
        player.style.height = "";
        node.graph?.setDirtyCanvas?.(true, true);
    });
}

function mount(node) {
    if (node._h3SelfLiftHunt || typeof node.addDOMWidget !== "function") return;
    injectStyles();
    const root = text("div", "", "h3sh-root");
    const head = text("div", "", "h3sh-head");
    const title = text("span", "SelfLift Seed Review", "h3sh-title");
    const badge = text("span", "Tiny-VAE preview", "h3sh-badge");
    head.append(title, badge);
    const toolbar = text("div", "", "h3sh-toolbar");
    const select = text("select", "", "h3sh-select");
    select.setAttribute("aria-label", "Saved hunt batch");
    const reload = text("button", "Refresh saved takes", "h3sh-button");
    const clean = text("button", "Clean saved takes", "h3sh-button h3sh-clean");
    clean.title = "Permanently remove this hunt's temporary latents, previews and recovery files. Saved scene checkpoints/videos are kept.";
    clean.disabled = true;
    const recover = text("a", "Download saved workflow");
    recover.download = "SelfLift-hunt-recovery.json";
    const status = text("p", "Queue to generate low-resolution candidates. Completed takes are saved on disk.", "h3sh-status");
    status.setAttribute("role", "status");
    const player = text("div", "", "h3sh-player");
    const video = text("video", "", "h3sh-video");
    video.controls = true;
    video.loop = true;
    video.preload = "metadata";
    video.playsInline = true;
    video.title = "Silent Tiny-VAE preview: review motion and composition, not final detail.";
    const grip = text("div", "", "h3sh-grip");
    grip.title = "Drag to resize the preview. Double-click to fit the node again.";
    grip.setAttribute("role", "separator");
    grip.setAttribute("aria-label", "Resize preview");
    grip.setAttribute("aria-orientation", "horizontal");
    player.append(video, grip);
    resizablePlayer(node, player, grip);
    const mediaStatus = text("p", "", "h3sh-notice");
    mediaStatus.hidden = true;
    video.addEventListener("error", () => {
        if (!video.getAttribute("src")) return;
        mediaStatus.textContent = "Preview could not be loaded. Refresh saved takes to retry.";
        mediaStatus.hidden = false;
    });
    video.addEventListener("loadeddata", () => { mediaStatus.hidden = true; });
    const candidates = text("div", "", "h3sh-candidates");
    const nav = text("div", "", "h3sh-nav");
    const previous = text("button", "◀", "h3sh-button h3sh-arrow");
    previous.setAttribute("aria-label", "Previous take");
    const takeTitle = text("span", "", "h3sh-take-title");
    const next = text("button", "▶", "h3sh-button h3sh-arrow");
    next.setAttribute("aria-label", "Next take");
    nav.append(previous, takeTitle, next);
    const meta = text("div", "", "h3sh-meta");
    const seedLabel = text("span", "", "h3sh-seed");
    const chosenLabel = text("span", "", "h3sh-chosen");
    meta.append(seedLabel, chosenLabel);
    const dots = text("div", "", "h3sh-dots");
    dots.setAttribute("aria-label", "Browse saved takes");
    const choose = text("button", "Use this take — finish upscale", "h3sh-button h3sh-approve");
    candidates.append(nav, meta, dots, choose);
    const empty = text("p", "Completed motion previews will appear here.", "h3sh-empty");
    const cleanupStatus = text("p", "", "h3sh-notice");
    cleanupStatus.hidden = true;
    cleanupStatus.setAttribute("role", "status");
    const help = text("details", "", "h3sh-help");
    help.append(text("summary", "Silent motion preview · Help & recovery"),
        text("p", "Tiny-VAE previews show approximate motion and composition, not final detail or sound. Browse with the arrows or dots, then choose a take. The current candidate finishes and saves before the rest are skipped."),
        text("p", "After OOM/restart, keep batch name, seed and settings fixed and queue the matching workflow again. Completed takes are reused; only the chosen take is upscaled."), recover);
    toolbar.append(select, reload, clean);
    root.append(head, toolbar, player, empty, mediaStatus, candidates, status, cleanupStatus, help);
    let optionsKey = "";
    let dotsKey = "";
    let previewKey = "";
    let cleaning = false;
    let choosing = false;
    let currentBatch = null;
    let currentTake = null;
    function clearVideo() {
        if (previewKey) { video.pause(); video.removeAttribute("src"); video.load(); }
        previewKey = "";
        mediaStatus.hidden = true;
    }
    function browse(ordinal) {
        if (!currentBatch) return;
        node.properties.h3_selflift_preview = {id:currentBatch.id, created_at:currentBatch.created_at, ordinal};
        panel.render();
    }
    const panel = { root, node, status, render() {
        node.properties ||= {};
        let key = node.properties.h3_selflift_batch;
        if (!batches.some(b => b.id === key)) key = batches[0]?.id || "";
        const nextOptions = JSON.stringify(batches.map(b => [b.id, b.scene_name, b.batch_name]));
        if (nextOptions !== optionsKey) {
            optionsKey = nextOptions;
            select.replaceChildren();
            for (const batch of batches) {
                const option = text("option", `${batch.run_name} · S${batch.scene} ${batch.scene_name} · ${batch.batch_name} · ${batch.id.slice(0, 7)}`);
                option.value = batch.id;
                select.append(option);
            }
        }
        select.value = key;
        const batch = batches.find(b => b.id === key);
        currentBatch = batch;
        clean.disabled = cleaning || choosing || !batch || batch.active;
        select.disabled = cleaning || choosing;
        if (!batch) {
            recover.hidden = true;
            clearVideo(); dots.replaceChildren(); dotsKey = "";
            currentTake = null;
            player.hidden = candidates.hidden = true;
            empty.hidden = false;
            badge.textContent = "Tiny-VAE preview";
            status.textContent = "No saved takes. Queue to start a new hunt.";
            return;
        }
        recover.hidden = false;
        recover.href = api.apiURL(`/h3/selflift/workflow?id=${encodeURIComponent(key)}`);
        badge.textContent = `${batch.low_steps} low + ${batch.high_steps} high steps`;
        const hunting = batch.active && ["low", "preview"].includes(batch.phase);
        status.textContent = batch.error ? `Paused — ${batch.error}`
            : hunting ? `Generating take ${batch.current || batch.candidates.length + 1} · ${batch.candidates.length} ready to review.`
            : batch.active && batch.phase === "high" ? `Upscaling take ${batch.selected}. You can still browse saved previews.`
            : batch.active ? "Choose a take to finish its upscale."
            : batch.phase === "finished" ? "Upscale finished. Saved takes are available for another version."
            : "Saved takes · queue the matching workflow to resume.";
        if (hunting && batch.selected) {
            status.textContent += ` · Finishing and saving take ${batch.current}; then upscale take ${batch.selected} and skip remaining candidates.`;
        }
        const remembered = node.properties.h3_selflift_preview;
        let ordinal = remembered?.id === key && remembered.created_at === batch.created_at
            ? remembered.ordinal : batch.selected;
        const take = batch.candidates.find(c => c.ordinal === ordinal) || batch.candidates[0];
        currentTake = take;
        player.hidden = candidates.hidden = !take;
        empty.hidden = Boolean(take);
        if (!take) { clearVideo(); return; }
        node.properties.h3_selflift_preview = {id:key, created_at:batch.created_at, ordinal:take.ordinal};
        const position = batch.candidates.indexOf(take);
        takeTitle.textContent = `Take ${take.ordinal} · ${position + 1} of ${batch.candidates.length} ready`;
        seedLabel.textContent = `Seed ${take.seed}`;
        chosenLabel.textContent = batch.selected === take.ordinal ? "Chosen for upscale" : "";
        previous.disabled = position === 0;
        next.disabled = position === batch.candidates.length - 1;
        // Only change the source when the viewed take changes. New candidates,
        // approvals and status polls must not reset the user's playback.
        const sourceKey = JSON.stringify([key, batch.created_at, take.ordinal, take.preview]);
        if (sourceKey !== previewKey) {
            clearVideo();
            previewKey = sourceKey;
            const slash = take.preview.lastIndexOf("/");
            const query = new URLSearchParams({ filename:take.preview.slice(slash+1),
                subfolder:take.preview.slice(0, slash), type:"output", h3_hunt_created:String(batch.created_at ?? "") });
            video.src = api.apiURL(`/view?${query}`);
            video.load();
        }
        const signature = JSON.stringify([key, batch.created_at, batch.candidates.map(c => c.ordinal)]);
        if (signature !== dotsKey) {
            dotsKey = signature;
            dots.replaceChildren();
            for (const take of batch.candidates) {
                const dot = text("button", "", "h3sh-dot");
                dot.dataset.ordinal = String(take.ordinal);
                dot.title = `Take ${take.ordinal} · seed ${take.seed}`;
                dot.setAttribute("aria-label", `View take ${take.ordinal}`);
                dot.onclick = () => browse(take.ordinal);
                dots.append(dot);
            }
        }
        for (const dot of dots.querySelectorAll("button")) {
            dot.setAttribute("aria-pressed", String(Number(dot.dataset.ordinal) === take.ordinal));
            dot.dataset.chosen = String(Number(dot.dataset.ordinal) === batch.selected);
        }
        choose.disabled = cleaning || choosing || (batch.active && batch.phase === "high");
        choose.textContent = hunting ? `Use take ${take.ordinal} now` : `Use take ${take.ordinal} — finish upscale`;
        choose.title = hunting ? "Finish and save the current candidate, skip the rest, then upscale this take."
            : "Select this take for upscale. If the hunt is stopped, queue its matching workflow to continue.";
    }};
    function move(offset) {
        const position = currentBatch?.candidates.indexOf(currentTake) ?? -1;
        const take = currentBatch?.candidates[position + offset];
        if (take) browse(take.ordinal);
    }
    previous.onclick = () => move(-1);
    next.onclick = () => move(1);
    nav.addEventListener("keydown", event => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault(); move(event.key === "ArrowLeft" ? -1 : 1);
    });
    choose.onclick = async () => {
        if (!currentBatch || !currentTake || choose.disabled) return;
        const key = currentBatch.id, ordinal = currentTake.ordinal;
        choosing = true; panel.render();
        let errorMessage = "";
        try {
            const response = await api.fetchApi("/h3/selflift/choose", {method:"POST",
                headers:{"Content-Type":"application/json"}, body:JSON.stringify({id:key, ordinal})});
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "Could not select take");
            node.properties.h3_selflift_batch = key;
            await refresh();
        } catch (error) { errorMessage = error.message; }
        finally { choosing = false; panel.render(); }
        if (errorMessage) status.textContent = errorMessage;
    };
    select.onchange = () => { node.properties ||= {}; node.properties.h3_selflift_batch = select.value; panel.render(); };
    reload.onclick = async () => { await refresh(); if (video.error && previewKey) video.load(); };
    clean.onclick = async () => {
        const batch = batches.find(b => b.id === select.value);
        if (!batch || batch.active || cleaning || choosing) return;
        if (!window.confirm(`Clean all saved takes for ${batch.run_name} · S${batch.scene} ${batch.scene_name} · ${batch.batch_name}?\n\nThis permanently deletes only this hunt's temporary latents, previews and recovery files. You cannot resume or upscale another take from it afterward. Normal saved scene videos/checkpoints are kept.`)) return;
        cleaning = true; panel.render();
        try {
            const response = await api.fetchApi("/h3/selflift/clean", {method:"POST",
                headers:{"Content-Type":"application/json"},
                body:JSON.stringify({id:batch.id, created_at:batch.created_at, confirm:true})});
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || "Could not clean saved takes");
            // Drop stale preview sources immediately, even if a poll was in
            // flight. Only refresh metadata; never queue or change workflows.
            if (pending) await pending;
            batches = batches.filter(b => b.id !== batch.id);
            panel.render();
            cleanupStatus.textContent = `Cleaned ${result.files} temporary files (${(result.bytes / 1048576).toFixed(1)} MiB). Saved scenes were kept.`;
            cleanupStatus.hidden = false;
            await refresh();
        } catch (error) {
            cleanupStatus.textContent = error.message;
            cleanupStatus.hidden = false;
        } finally { cleaning = false; panel.render(); }
    };
    const autoClean = node.widgets?.find(widget => widget.name === "auto_remove_saved_takes");
    if (autoClean) autoClean.label = "Auto-remove saved takes";
    for (const event of ["pointerdown", "pointerup", "mousedown", "mouseup", "click", "dblclick", "keydown"])
        root.addEventListener(event, e => e.stopPropagation());
    bindNodeWheel(root, node, app);
    // A fixed computeSize leaves unused space below the panel when the node
    // grows. Let ComfyUI allocate all remaining height, with scrolling only
    // when expanded help or a manually enlarged player exceeds the viewport.
    const widget = node.addDOMWidget("h3_selflift_review", "h3-selflift-review", root, {
        serialize:false, getMinHeight:() => 440,
    });
    widget.serialize = false;
    node._h3SelfLiftHunt = panel;
    panels.add(panel);
    if (!timer) timer = setInterval(() => { if ([...panels].some(visible)) refresh(); }, 3000);
    const removed = node.onRemoved;
    node.onRemoved = function () {
        panels.delete(panel);
        for (const video of root.querySelectorAll("video")) { video.pause(); video.removeAttribute("src"); video.load(); }
        if (!panels.size) { clearInterval(timer); timer = null; }
        return removed?.apply(this, arguments);
    };
    // Only initial creation sets a minimum; tab reactivation must not shrink it.
    node.setSize([Math.max(node.size[0], 560), Math.max(node.size[1], 780)]);
    queueMicrotask(refresh);
}

api.addEventListener("h3-selflift-hunt", event => {
    for (const panel of panels) {
        if (String(panel.node.id) === event.detail.node && panel.node.graph === app.graph) {
            panel.node.properties ||= {};
            panel.node.properties.h3_selflift_batch = event.detail.id;
        }
    }
    if ([...panels].some(visible)) refresh();
});

app.registerExtension({ name: "MiniMaxH3.SelfLiftSeedHunt",
    beforeRegisterNodeDef(nodeType, data) {
        if (data.name !== "MiniMaxH3SelfLiftSeedHunt") return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () { const result = created?.apply(this, arguments); mount(this); return result; };
        const executed = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (data) {
            executed?.apply(this, arguments);
            const id = data?.h3_selflift_hunt?.[0];
            if (id) { this.properties ||= {}; this.properties.h3_selflift_batch = id; }
            refresh();
        };
    },
});
