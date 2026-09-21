import {app} from "../../scripts/app.js";
import {bindNodeWheel} from "./h3_dom_wheel.mjs?v=0.7.1";
import {api} from "../../scripts/api.js";

const NODE_NAME = "MiniMaxH3ReviewRelay";
const ENDPOINT = "/minimax_h3_context_loop/review-relay";

function element(tag, text = "") {
    const item = document.createElement(tag);
    item.textContent = text;
    return item;
}

function gateIdentity(review) {
    return JSON.stringify([review.run_name, review.branch_id, review.clip_index, review.node_id]);
}

function setOptions(select, entries, value) {
    const signature = JSON.stringify(entries);
    if (select._h3Options !== signature) {
        select.replaceChildren(...entries.map(([key, text]) => {
            const option = element("option", text); option.value = key; return option;
        }));
        select._h3Options = signature;
    }
    select.value = value;
}

function mount(node) {
    if (node._h3ReviewRelay) return;
    const root = element("div");
    root.className = "h3-review-relay";
    root.style.cssText = "box-sizing:border-box;display:flex;flex-direction:column;gap:8px;padding:10px;" +
        "height:100%;min-height:480px;overflow:auto;background:var(--comfy-menu-bg,#202124);" +
        "color:var(--input-text,#eee);font:13px sans-serif;";
    const title = element("strong", "Review Gate Relay — live jobs on this server");
    const help = element("div", "No wires or queueing. Decisions go to the original waiting Gate.");
    const gate = element("select"); gate.title = "Project / branch / scene / Gate";
    const refreshButton = element("button", "Refresh live gates");
    const status = element("div", "Looking for live gates…"); status.setAttribute("role", "status");
    const video = element("video"); video.controls = true; video.preload = "none";
    video.style.cssText = "width:100%;height:280px;flex-shrink:0;object-fit:contain;background:#08090c;";
    const navigation = element("div"); navigation.style.cssText = "display:flex;gap:6px;";
    const previous = element("button", "◀"), next = element("button", "▶");
    const candidates = element("select"); candidates.style.cssText = "flex:1;min-width:0;";
    navigation.append(previous, candidates, next);
    const metadata = element("div");
    const prompt = element("textarea"); prompt.readOnly = true; prompt.rows = 7;
    prompt.title = "Saved candidate prompt (read-only)";
    prompt.style.cssText = "width:100%;box-sizing:border-box;min-height:100px;resize:vertical;";
    const keep = element("input"); keep.type = "checkbox";
    const keepLabel = element("label"); keepLabel.append(keep, element("span", " Keep this take"));
    const keepSummary = element("div");
    const actions = element("div"); actions.style.cssText = "display:flex;gap:6px;flex-wrap:wrap;";
    const approve = element("button", "Approve selected"), stop = element("button", "Select & stop");
    const more = element("button", "Next candidate");
    actions.append(approve, stop, more);
    root.append(title, help, gate, refreshButton, status, video, navigation, metadata, prompt,
        keepLabel, keepSummary, actions);
    for (const control of [gate, candidates, prompt, refreshButton, previous, next, approve, stop, more]) {
        control.style.background = "var(--comfy-input-bg,#303338)";
        control.style.color = "var(--input-text,#eee)";
        control.style.border = "1px solid var(--border-color,#555)";
        control.style.borderRadius = "4px";
        control.style.padding = "5px";
    }
    for (const name of ["pointerdown", "mousedown", "keydown"]) {
        root.addEventListener(name, event => event.stopPropagation());
    }
    bindNodeWheel(root, node, app);
    const domWidget = node.addDOMWidget("h3_review_relay", "h3-review-relay", root, {serialize:false});
    domWidget.computeSize = width => [width, 690];
    if (node.size?.[0] < 520 || node.size?.[1] < 720) node.setSize?.([620, 760]);

    let disposed = false, current = null, identity = "", revision = "", busy = false;
    let timer = null, controller = null, serial = 0, source = "", fresh = false;
    const keepChoices = new Map();
    const active = () => !disposed && Boolean(node.graph) && root.isConnected &&
        document.visibilityState !== "hidden" && !app.configuringGraph;

    function selection() { return current?.candidate ?? null; }
    function keptRevisions() {
        return (current?.candidates ?? []).filter(item => keepChoices.get(item.revision) !== false)
            .map(item => item.revision);
    }
    function controls() {
        const candidate = selection();
        const ready = fresh && !busy && current?.actionable && Boolean(candidate?.revision);
        approve.disabled = stop.disabled = !ready;
        more.hidden = !current?.review_each_candidate || !(current?.candidate_remaining > 0);
        more.disabled = !ready;
        gate.disabled = candidates.disabled = busy;
        refreshButton.disabled = busy;
        keep.disabled = busy || !candidate;
        keep.checked = candidate ? keepChoices.get(candidate.revision) !== false : false;
        const index = current?.candidates?.findIndex(item => item.revision === revision) ?? -1;
        previous.disabled = busy || index <= 0;
        next.disabled = busy || index < 0 || index >= (current?.candidates?.length ?? 0) - 1;
        keepSummary.textContent = current
            ? `${keptRevisions().length}/${current.candidates.length} takes marked to keep. ` +
                "The selected take is always kept. Unchecked takes may be deleted by the Gate."
            : "";
    }
    function clearPreview() {
        video.pause(); video.removeAttribute("src"); video.load(); source = "";
        prompt.value = ""; metadata.textContent = "";
    }
    function showPreview() {
        const candidate = selection();
        const item = candidate?.video;
        const url = item?.filename ? api.apiURL(`/view?${new URLSearchParams({
            filename:item.filename, subfolder:item.subfolder ?? "", type:item.type ?? "output",
        })}`) : "";
        if (source !== url) {
            clearPreview();
            if (url) { source = url; video.src = url; video.preload = "metadata"; video.load(); }
        }
        const text = candidate?.scene_prompt ?? "";
        if (prompt.value !== text) prompt.value = text;
        metadata.textContent = candidate
            ? `Take ${candidate.number} · seed ${candidate.seed} · ${candidate.raw_frames} raw frames · ` +
                `${candidate.has_audio ? "audio included" : "silent preview"} · ${candidate.revision}`
            : "No saved candidate yet.";
    }
    function renderSelected(selected) {
        current = selected;
        if (current) {
            const nextIdentity = gateIdentity(current);
            if (identity !== nextIdentity) keepChoices.clear();
            identity = nextIdentity;
            revision = current.candidate?.revision ?? "";
        }
        setOptions(candidates, (current?.candidates ?? []).map(item =>
            [item.revision, `Take ${item.number} · seed ${item.seed}`]), revision);
        showPreview(); controls();
    }
    function schedule() {
        if (!disposed) timer = window.setTimeout(refresh, 2000);
    }
    async function refresh() {
        if (busy || disposed) return;
        window.clearTimeout(timer); timer = null;
        if (!active()) { schedule(); return; }
        controller?.abort(); controller = new AbortController();
        const generation = ++serial;
        try {
            const query = new URLSearchParams({token:gate.value ?? "", candidate_revision:revision});
            const response = await api.fetchApi(`${ENDPOINT}?${query}`, {signal:controller.signal});
            const body = await response.json();
            if (generation !== serial || disposed) return;
            if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
            const reviews = body.reviews ?? [];
            let token = reviews.some(item => item.token === gate.value) ? gate.value : "";
            if (!token && identity) {
                const matches = reviews.filter(item => gateIdentity(item) === identity);
                if (matches.length === 1) token = matches[0].token;
            }
            if (!token && !identity && reviews.length === 1) token = reviews[0].token;
            setOptions(gate, [["", "Choose a live gate…"], ...reviews.map(item => [item.token,
                `${item.run_name} · ${item.branch_id === "main" ? "Original" : item.branch_id.slice(0, 8)} · ` +
                `scene ${item.clip_index} ${item.shot_id ?? ""} · Gate ${item.node_id} · ` +
                `${item.actionable ? "waiting" : "generating"} ${item.generated_count}/${item.candidate_count}`])], token);
            fresh = true;
            renderSelected(body.selected?.token === token ? body.selected : null);
            if (token && !current) { void refresh(); return; }
            status.textContent = current
                ? (current.actionable ? "Gate waiting for your decision." : "Generating candidates; decisions unlock when the Gate waits.") +
                    (current.deadline ? ` Auto-approval at ${new Date(current.deadline * 1000).toLocaleTimeString()}.` : "") +
                    (current.warning ? ` ${current.warning}` : "")
                : reviews.length ? "Choose the project and Gate you want to review."
                    : "No live gate. Waiting for a job; ended/restarted or deferred jobs need the original workflow's checkpoint recovery.";
        } catch (error) {
            if (generation !== serial || disposed || error.name === "AbortError") return;
            fresh = false; controls();
            status.textContent = `Connection unavailable: ${error.message}. Reconnecting…`;
        } finally {
            if (generation === serial) schedule();
        }
    }
    function chooseCandidate(value) {
        revision = value; fresh = false; current = null; controls(); clearPreview(); void refresh();
    }
    gate.addEventListener("change", () => {
        identity = ""; revision = ""; current = null; fresh = false; keepChoices.clear();
        controls(); clearPreview(); void refresh();
    });
    candidates.addEventListener("change", () => chooseCandidate(candidates.value));
    previous.addEventListener("click", () => {
        const index = current.candidates.findIndex(item => item.revision === revision);
        if (index > 0) chooseCandidate(current.candidates[index - 1].revision);
    });
    next.addEventListener("click", () => {
        const index = current.candidates.findIndex(item => item.revision === revision);
        if (index < current.candidates.length - 1) chooseCandidate(current.candidates[index + 1].revision);
    });
    keep.addEventListener("change", () => { keepChoices.set(revision, keep.checked); controls(); });
    refreshButton.addEventListener("click", () => void refresh());

    async function submit(action) {
        if (busy || !fresh || !current?.actionable || !selection()) return;
        const body = {token:current.token, run_name:current.run_name, branch_id:current.branch_id,
            clip_index:current.clip_index, action, candidate_revision:revision,
            candidate_revisions:keptRevisions()};
        const removed = current.candidates.filter(item => item.revision !== revision &&
            !body.candidate_revisions.includes(item.revision)).length;
        if (removed && !window.confirm(`The Gate may delete ${removed} unchecked take(s). Send this decision?`)) return;
        busy = true; ++serial; controller?.abort(); window.clearTimeout(timer); controls();
        const generation = serial;
        status.textContent = "Sending decision to the original Gate…";
        try {
            // Never queuePrompt(), mirror a Plan, or claim ownership here.
            const response = await api.fetchApi(ENDPOINT, {method:"POST",
                headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
            const result = await response.json();
            if (disposed || generation !== serial) return;
            if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
            fresh = false; current = null;
            status.textContent = action === "stop" ? "Selection sent; the original Gate will stop."
                : action === "next_candidate" ? "The original job will generate the next candidate."
                    : "Selection sent; the original job will resume. Its configured loop mode controls further scenes.";
        } catch (error) {
            if (disposed || generation !== serial) return;
            fresh = false;
            status.textContent = `${error.message}. Refresh before trying again; the decision may already have arrived.`;
        } finally {
            busy = false;
            if (!disposed) { controls(); schedule(); }
        }
    }
    approve.addEventListener("click", () => void submit("approve"));
    stop.addEventListener("click", () => void submit("stop"));
    more.addEventListener("click", () => void submit("next_candidate"));
    const wake = () => { if (active()) void refresh(); else { video.pause(); } };
    window.addEventListener("focus", wake);
    document.addEventListener("visibilitychange", wake);
    node._h3ReviewRelay = {root, refresh, dispose() {
        disposed = true; ++serial; controller?.abort(); window.clearTimeout(timer); clearPreview();
        window.removeEventListener("focus", wake);
        document.removeEventListener("visibilitychange", wake);
    }};
    controls(); timer = window.setTimeout(refresh, 0);
}

app.registerExtension({
    name:"minimax.h3.reviewRelay",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = created?.apply(this, arguments); mount(this); return result;
        };
        const removed = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function () {
            this._h3ReviewRelay?.dispose(); this._h3ReviewRelay = null;
            return removed?.apply(this, arguments);
        };
    },
});
