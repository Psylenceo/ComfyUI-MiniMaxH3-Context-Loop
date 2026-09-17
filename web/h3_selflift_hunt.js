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

function text(tag, value) {
    const element = document.createElement(tag);
    element.textContent = value;
    return element;
}

function mount(node) {
    if (node._h3SelfLiftHunt || typeof node.addDOMWidget !== "function") return;
    const root = document.createElement("div");
    root.style.cssText = "height:100%;overflow:auto;background:#202126;color:#eee;padding:10px;box-sizing:border-box;font:13px sans-serif;";
    const toolbar = document.createElement("div");
    toolbar.style.cssText = "display:flex;gap:6px;flex-wrap:wrap;";
    const select = document.createElement("select");
    select.style.cssText = "flex:1;min-width:100px;max-width:100%;";
    const reload = text("button", "Refresh saved takes");
    const recover = text("a", "Download saved workflow");
    recover.style.color = "#99bcff";
    recover.download = "SelfLift-hunt-recovery.json";
    const status = text("p", "Queue to generate low-resolution candidates. Completed takes are saved on disk.");
    const cards = document.createElement("div");
    cards.style.cssText = "display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px;";
    toolbar.append(select, reload);
    root.append(toolbar, status, recover, cards);
    root.append(text("p", "Tiny-VAE motion preview · silent · approximate detail. After OOM/restart, keep batch name/seed/settings fixed and queue this workflow again. Only the chosen take is upscaled."));
    let optionsKey = "";
    let cardsKey = "";
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
        if (!batch) { recover.hidden = true; return; }
        recover.hidden = false;
        recover.href = api.apiURL(`/h3/selflift/workflow?id=${encodeURIComponent(key)}`);
        const phase = batch.active ? batch.phase : (batch.phase === "finished" ? "finished" : "saved — queue matching workflow to resume");
        status.textContent = `${batch.low_steps} low + ${batch.high_steps} high steps · ${phase}${batch.current ? ` · take ${batch.current}` : ""}${batch.selected ? ` · chosen take ${batch.selected}` : ""}${batch.error ? ` · ${batch.error}` : ""}`;
        // Never rebuild video elements on ordinary polls: playback survives polling.
        const signature = JSON.stringify([key, batch.candidates.map(c => [c.ordinal, c.preview])]);
        if (signature !== cardsKey) {
            cardsKey = signature;
            for (const video of cards.querySelectorAll("video")) video.pause();
            cards.replaceChildren();
            for (const take of batch.candidates) {
                const card = document.createElement("div");
                card.style.cssText = "padding:7px;background:#303239;border-radius:6px;";
                const video = document.createElement("video");
                video.controls = true;
                video.loop = true;
                video.preload = "none";
                video.playsInline = true;
                video.style.cssText = "display:block;width:100%;max-height:270px;background:#111;";
                const slash = take.preview.lastIndexOf("/");
                const query = new URLSearchParams({ filename: take.preview.slice(slash+1),
                    subfolder: take.preview.slice(0, slash), type: "output" });
                video.src = api.apiURL(`/view?${query}`);
                const choose = text("button", `Use take ${take.ordinal} — finish upscale`);
                choose.dataset.ordinal = String(take.ordinal);
                choose.onclick = async () => {
                    choose.disabled = true;
                    try {
                        const response = await api.fetchApi("/h3/selflift/choose", { method: "POST",
                            headers: {"Content-Type": "application/json"},
                            body: JSON.stringify({ id: key, ordinal: take.ordinal }) });
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.error || "Could not select take");
                        node.properties.h3_selflift_batch = key;
                        await refresh();
                    } catch (error) { status.textContent = error.message; choose.disabled = false; }
                };
                card.append(text("div", `Take ${take.ordinal} · seed ${take.seed}`), video, choose);
                cards.append(card);
            }
        }
        const busy = batch.active && ["low", "preview", "high"].includes(batch.phase);
        for (const button of cards.querySelectorAll("button")) {
            button.disabled = busy;
            button.style.outline = Number(button.dataset.ordinal) === batch.selected ? "2px solid #76bd8a" : "";
        }
    }};
    select.onchange = () => { node.properties ||= {}; node.properties.h3_selflift_batch = select.value; panel.render(); };
    reload.onclick = refresh;
    for (const event of ["pointerdown", "mousedown", "keydown"]) root.addEventListener(event, e => e.stopPropagation());
    bindNodeWheel(root, node, app);
    const widget = node.addDOMWidget("h3_selflift_review", "h3-selflift-review", root, {serialize:false});
    widget.computeSize = width => [width, 440];
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
