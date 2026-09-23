import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { bindNodeWheel } from "./h3_dom_wheel.mjs?v=0.7.1";

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
        .h3sh-dot[data-marked="true"] { box-shadow:0 0 0 2px #87be97; }
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
        .h3sh-actions { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:6px; }
        .h3sh-actions > button { min-width:0; overflow-wrap:anywhere; }
        .h3sh-view { color:#b9c4d9; font-size:11px; }
        .h3sh-approve { border-color:#4b9d72; background:#204332; font-weight:650; }
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
    const mediaStatus = text("p", "", "h3sh-notice h3sh-media-notice");
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
    const upscale = text("button", "Preview upscale", "h3sh-button h3sh-upscale-preview");
    const mark = text("button", "Mark for upscale", "h3sh-button h3sh-mark");
    const main = text("button", "Make main", "h3sh-button h3sh-main");
    const actions = text("div", "", "h3sh-actions");
    actions.append(mark, main, upscale, choose);
    const viewLabel = text("span", "", "h3sh-view");
    candidates.append(nav, meta, dots, viewLabel, actions);
    const empty = text("p", "Completed motion previews will appear here.", "h3sh-empty");
    const cleanupStatus = text("p", "", "h3sh-notice");
    cleanupStatus.hidden = true;
    cleanupStatus.setAttribute("role", "status");
    const cleanupWarning = text("p", "", "h3sh-notice h3sh-cleanup-warning");
    cleanupWarning.hidden = true;
    cleanupWarning.setAttribute("role", "status");
    const gateNotice = text("p", "Review gate off for the next queue: upscale the saved choice, or run just the first seed automatically. Middle-pass recovery stays enabled. This does not bypass the final Review Gate.", "h3sh-notice h3sh-gate-notice");
    gateNotice.hidden = true;
    const help = text("details", "", "h3sh-help");
    help.append(text("summary", "Silent motion preview · Help & recovery"),
        text("p", "Tiny-VAE previews show approximate motion and composition, not final detail or sound. Browse with the arrows or dots, then choose a take. The current candidate finishes and saves before the rest are skipped."),
        text("p", "Preview upscale runs the configured latent lift and corrections, then the Tiny VAE, without high-resolution denoising or approving the take. Inspect lift artifacts and switch back to the low preview. A clean preview does not guarantee a clean final render."),
        text("p", "Mark several takes for upscale and choose one main take. Finish marked saves the alternates first and the main last, then shows all finished choices in Review Gate. Only the accepted main continues the scene chain."),
        text("p", "After OOM/restart, keep batch name, seed and settings fixed and queue the matching workflow in resume mode. Saved low/high passes and completed clips are reused. Selection changes are shared across tabs; cleanup waits until every marked take is saved."), recover);
    toolbar.append(select, reload, clean);
    root.append(head, gateNotice, toolbar, player, empty, mediaStatus, candidates, status, cleanupWarning, cleanupStatus, help);
    let optionsKey = "";
    let dotsKey = "";
    let previewKey = "";
    let cleaning = false;
    let choosing = false;
    let requesting = false;
    let marking = false;
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
        const reviewEnabled = node.widgets?.find(widget => widget.name === "review_enabled")?.value !== false;
        gateNotice.hidden = reviewEnabled;
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
        cleanupWarning.hidden = !batch?.cleanup_error;
        cleanupWarning.textContent = batch?.cleanup_error
            ? `Temporary-file cleanup failed: ${batch.cleanup_error}\nSaved scene files are not affected. Use Clean saved takes to retry removing the remaining temporary files.`
            : "";
        clean.disabled = cleaning || choosing || requesting || marking || !batch || batch.active
            || batch.phase === "awaiting_save";
        select.disabled = cleaning || choosing || requesting || marking;
        if (!batch) {
            recover.hidden = true;
            clearVideo(); dots.replaceChildren(); dotsKey = "";
            currentTake = null;
            player.hidden = candidates.hidden = true;
            empty.hidden = false;
            badge.textContent = reviewEnabled ? "Tiny-VAE preview" : "Automatic upscale";
            empty.textContent = reviewEnabled ? "Completed motion previews will appear here."
                : "Queue to run one take without waiting for review.";
            status.textContent = reviewEnabled ? "No saved takes. Queue to start a new hunt."
                : "Review gate off · the middle pass will still be saved for recovery.";
            return;
        }
        recover.hidden = false;
        recover.href = api.apiURL(`/h3/selflift/workflow?id=${encodeURIComponent(key)}`);
        badge.textContent = `${batch.low_steps} low + ${batch.high_steps} high steps`;
        const hunting = batch.active && ["low", "preview"].includes(batch.phase);
        const previewPending = batch.active && batch.upscale_request != null;
        const automatic = batch.review_enabled === false;
        const marked = batch.marked ?? (batch.selected != null ? [batch.selected] : []);
        const mainOrdinal = batch.main ?? batch.selected;
        const locked = batch.phase === "awaiting_save" || (batch.active &&
            (batch.phase === "high" || batch.selected != null || automatic));
        status.textContent = batch.error ? `Paused — ${batch.error}`
            : batch.active && automatic ? batch.phase === "high"
                ? `Review gate off · upscaling take ${batch.selected} automatically.`
                : "Review gate off · saving one low-resolution take, then upscaling automatically."
            : hunting ? `Generating take ${batch.current || batch.candidates.length + 1} · ${batch.candidates.length} ready to review.`
            : batch.active && batch.phase === "upscale_preview" ? `Previewing take ${batch.upscale_request}'s latent upscale · no high denoising or approval.`
            : batch.phase === "awaiting_save" ? `Saving marked takes in sequence · main take ${batch.selected} finishes last. Resume the matching workflow if interrupted.`
            : batch.active && batch.phase === "high" ? `Upscaling take ${batch.current ?? batch.selected}. Main take ${batch.selected}. You can still browse saved previews.`
            : batch.active ? "Choose a take to finish its upscale."
            : batch.phase === "finished" ? "Upscale finished. Saved takes are available for another version."
            : "Saved takes · queue the matching workflow to resume.";
        if (hunting && batch.selected && !automatic) {
            const finishing = (batch.selected_ordinals?.length ?? 1) > 1
                ? `${batch.selected_ordinals.length} marked takes (main ${batch.selected} last)` : `take ${batch.selected}`;
            status.textContent += ` · Finishing and saving take ${batch.current}; then upscale ${finishing} and skip remaining candidates.`;
        }
        if (hunting && previewPending) status.textContent += ` · Upscale preview for take ${batch.upscale_request} is queued after the current candidate is saved.`;
        const remembered = node.properties.h3_selflift_preview;
        let ordinal = remembered?.id === key && remembered.created_at === batch.created_at
            ? remembered.ordinal : batch.selected;
        const take = batch.candidates.find(c => c.ordinal === ordinal) || batch.candidates[0];
        currentTake = take;
        const hasPreview = Boolean(take?.preview);
        player.hidden = !hasPreview;
        candidates.hidden = !take;
        empty.hidden = hasPreview;
        empty.textContent = take ? "No Tiny-VAE preview was generated with the review gate off. The saved take can resume without a preview."
            : "Completed motion previews will appear here.";
        if (!take) { clearVideo(); return; }
        const wantsUpscale = remembered?.id === key && remembered.created_at === batch.created_at
            && remembered.ordinal === take.ordinal && remembered.upscale === true;
        const showingUpscale = wantsUpscale && Boolean(take.upscale_preview);
        const mediaPath = showingUpscale ? take.upscale_preview : take.preview;
        node.properties.h3_selflift_preview = {id:key, created_at:batch.created_at, ordinal:take.ordinal, upscale:wantsUpscale};
        viewLabel.textContent = showingUpscale ? "Upscaled latent · Tiny VAE · before high denoising"
            : "Low-resolution latent · Tiny VAE";
        if (take.upscale_error) status.textContent += `\nUpscale preview failed — ${take.upscale_error}. The saved low take is intact; retry the preview.`;
        const position = batch.candidates.indexOf(take);
        takeTitle.textContent = `Take ${take.ordinal} · ${position + 1} of ${batch.candidates.length} ready`;
        seedLabel.textContent = `Seed ${take.seed}`;
        chosenLabel.textContent = mainOrdinal === take.ordinal ? "Main take · included in upscale"
            : marked.includes(take.ordinal) ? "Marked for upscale" : "";
        previous.disabled = position === 0;
        next.disabled = position === batch.candidates.length - 1;
        // Only change the source when the viewed take changes. New candidates,
        // approvals and status polls must not reset the user's playback.
        const sourceKey = JSON.stringify([key, batch.created_at, take.ordinal, mediaPath]);
        if (!hasPreview) {
            clearVideo();
        } else if (sourceKey !== previewKey) {
            clearVideo();
            previewKey = sourceKey;
            const slash = mediaPath.lastIndexOf("/");
            const query = new URLSearchParams({ filename:mediaPath.slice(slash+1),
                subfolder:mediaPath.slice(0, slash), type:"output", h3_hunt_created:String(batch.created_at ?? "") });
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
            dot.dataset.marked = String(marked.includes(Number(dot.dataset.ordinal)));
        }
        mark.textContent = marked.includes(take.ordinal) ? "Marked for upscale ✓" : "Mark for upscale";
        mark.setAttribute("aria-pressed", String(marked.includes(take.ordinal)));
        const markLimit = batch.max_marked ?? 20;
        const atLimit = marked.length >= markLimit && !marked.includes(take.ordinal);
        mark.disabled = cleaning || choosing || requesting || marking || locked || !hasPreview
            || mainOrdinal === take.ordinal || atLimit;
        mark.title = atLimit ? `Mark at most ${markLimit} takes for one final review.`
            : mainOrdinal === take.ordinal ? "The main take is always included. Choose another main before unmarking this take."
            : "Keep this take for full high-resolution finishing; this does not release the gate.";
        main.textContent = mainOrdinal === take.ordinal ? "Main take ★" : "Make main";
        main.disabled = cleaning || choosing || requesting || marking || locked || !hasPreview
            || mainOrdinal === take.ordinal || atLimit;
        main.title = atLimit ? `Unmark another take first; the main must be included in the ${markLimit}-take limit.`
            : "Choose the take that continues the chain; automatically includes it in the marked set.";
        upscale.textContent = showingUpscale ? "Show low preview" : take.upscale_preview ? "Show upscale preview"
            : previewPending && batch.upscale_request === take.ordinal ? "Preparing upscale preview…" : "Preview upscale";
        upscale.disabled = cleaning || choosing || requesting || marking || !hasPreview || (!take.upscale_preview &&
            (!batch.active || automatic || batch.selected != null || previewPending ||
                !["low", "preview", "waiting"].includes(batch.phase)));
        upscale.title = take.upscale_preview ? "Switch between the saved low and upscale previews; no generation."
            : !batch.active ? "Queue the matching workflow in resume mode to preview its upscale."
            : "Run the latent lift and Tiny VAE only. Keep the gate open; do not approve or run high denoising.";
        choose.disabled = cleaning || choosing || requesting || marking || !hasPreview || previewPending || locked;
        choose.textContent = !hasPreview ? "Automatic take — no review required"
            : marked.length ? `Finish ${marked.length} marked · main take ${mainOrdinal}`
            : hunting ? `Use take ${take.ordinal} now` : `Use take ${take.ordinal} — finish upscale`;
        choose.title = marked.length ? "Finish the marked takes sequentially, with the main last."
            : hunting ? "Finish and save the current candidate, skip the rest, then upscale this take."
            : "Select this take for upscale. If the hunt is stopped, queue its matching workflow to continue.";
    }};
    function move(offset) {
        const position = currentBatch?.candidates.indexOf(currentTake) ?? -1;
        const take = currentBatch?.candidates[position + offset];
        if (take) browse(take.ordinal);
    }
    previous.onclick = () => move(-1);
    next.onclick = () => move(1);
    async function saveMarks(makeMain) {
        if (!currentBatch || !currentTake || (makeMain ? main : mark).disabled) return;
        const batch = currentBatch, ordinal = currentTake.ordinal;
        let marked = [...(batch.marked ?? (batch.selected != null ? [batch.selected] : []))];
        const mainOrdinal = makeMain ? ordinal : batch.main ?? batch.selected ?? ordinal;
        marked = makeMain || !marked.includes(ordinal) ? [...new Set([...marked, ordinal, mainOrdinal])]
            : marked.filter(value => value !== ordinal);
        marking = true; panel.render();
        let errorMessage = "";
        try {
            const response = await api.fetchApi("/h3/selflift/selection", {method:"POST",
                headers:{"Content-Type":"application/json"}, body:JSON.stringify({id:batch.id,
                    created_at:batch.created_at, selection_version:batch.selection_version ?? 0,
                    main:mainOrdinal, ordinals:marked})});
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "Could not update marked takes");
        } catch (error) { errorMessage = error.message; }
        finally { await refresh(); marking = false; panel.render(); }
        if (errorMessage) status.textContent = errorMessage;
    }
    mark.onclick = () => saveMarks(false);
    main.onclick = () => saveMarks(true);
    nav.addEventListener("keydown", event => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault(); move(event.key === "ArrowLeft" ? -1 : 1);
    });
    upscale.onclick = async () => {
        if (!currentBatch || !currentTake || upscale.disabled) return;
        if (currentTake.upscale_preview) {
            node.properties.h3_selflift_preview.upscale = !node.properties.h3_selflift_preview.upscale;
            panel.render(); return;
        }
        const key = currentBatch.id, ordinal = currentTake.ordinal, created_at = currentBatch.created_at;
        requesting = true; panel.render();
        let errorMessage = "";
        try {
            const response = await api.fetchApi("/h3/selflift/upscale-preview", {method:"POST",
                headers:{"Content-Type":"application/json"}, body:JSON.stringify({id:key, ordinal, created_at})});
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "Could not request upscale preview");
            // Do not jump to another take if the user browsed while requesting.
            const viewed = node.properties.h3_selflift_preview;
            if (viewed?.id === key && viewed.created_at === created_at && viewed.ordinal === ordinal) viewed.upscale = true;
            await refresh();
        } catch (error) { errorMessage = error.message; }
        finally { requesting = false; panel.render(); }
        if (errorMessage) status.textContent = errorMessage;
    };
    choose.onclick = async () => {
        if (!currentBatch || !currentTake || choose.disabled) return;
        const key = currentBatch.id;
        const ordinal = currentBatch.marked?.length ? currentBatch.main : currentTake.ordinal;
        const ordinals = currentBatch.marked?.length ? [...currentBatch.marked] : [ordinal];
        const created_at = currentBatch.created_at, selection_version = currentBatch.selection_version ?? 0;
        choosing = true; panel.render();
        let errorMessage = "";
        try {
            const response = await api.fetchApi("/h3/selflift/choose", {method:"POST",
                headers:{"Content-Type":"application/json"}, body:JSON.stringify({id:key, ordinal, ordinals,
                    created_at, selection_version})});
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
        if (!batch || batch.active || cleaning || choosing || requesting) return;
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
    const review = node.widgets?.find(widget => widget.name === "review_enabled");
    if (review) {
        review.label = "Review gate";
        const callback = review.callback;
        review.callback = function () {
            const result = callback?.apply(this, arguments);
            panel.render();
            return result;
        };
    }
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
