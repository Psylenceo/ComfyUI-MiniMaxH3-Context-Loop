import {contextMaskGrid, normalizeContextMask, paintContextMask, MASK_LEVELS} from "./h3_context_mask_core.mjs?v=0.7.1";

// The video remains the preview: only the small fixed mask is drawn to canvas.
export function contextMaskEditor(video, initialMask, {enabled, onChange}) {
    const make = (tag, text = "") => {
        const item = document.createElement(tag);
        item.textContent = text;
        return item;
    };
    const host = make("div");
    host.className = "h3studio-context-mask";
    const stage = make("div");
    stage.style.position = "relative";
    Object.assign(video.style, {minHeight:"0", maxHeight:"none", height:"auto"});
    const canvas = make("canvas");
    canvas.setAttribute("aria-label", "Paint a fixed context release mask");
    Object.assign(canvas.style, {position:"absolute", inset:"0", width:"100%", height:"100%",
        pointerEvents:"none", touchAction:"none", imageRendering:"pixelated", cursor:"crosshair"});
    stage.append(video, canvas);
    const toggle = make("button", "Weaken context…");
    toggle.type = "button";
    toggle.disabled = !enabled;
    toggle.setAttribute("aria-expanded", "false");
    const toolbar = make("div");
    toolbar.className = "h3studio-context-mask-tools";
    toolbar.hidden = true;
    const help = make("div", enabled
        ? "Painted areas may change more. Fixed across this block; audio and source files stay unchanged."
        : "Weaken context requires Masked AV, Feathered AV or Audio Feather AV.");
    help.className = "h3studio-context-help";
    let grid = null, editing = false, stroke = null, stored = normalizeContextMask(initialMask);
    let undo = [];
    const control = (label, min, max, step, value) => {
        const wrap = make("label"), input = make("input"), readout = make("span");
        input.type = "range";
        Object.assign(input, {min:String(min), max:String(max), step:String(step), value:String(value)});
        input.setAttribute("aria-label", label);
        const show = () => { readout.textContent = `${label}: ${input.value}`; };
        input.addEventListener("input", show);
        show(); wrap.append(readout, input); toolbar.append(wrap);
        return input;
    };
    const strength = control("Release %", 0, 100, 5, Math.round((stored?.strength ?? 0.5) * 100));
    const radius = control("Brush radius (cells)", 1, 10, 1, 2);
    const softness = control("Soft edge %", 0, 100, 10, 50);
    const erase = make("button", "Erase: off"), reset = make("button", "Reset mask");
    const undoButton = make("button", "Undo stroke");
    const remove = make("button", "Remove saved mask");
    remove.type = "button";
    remove.hidden = enabled || !stored;
    for (const button of [erase, reset, undoButton]) button.type = "button";
    let erasing = false;
    erase.addEventListener("click", () => {
        erasing = !erasing; erase.textContent = `Erase: ${erasing ? "on" : "off"}`;
        erase.setAttribute("aria-pressed", String(erasing));
    });
    toolbar.append(erase, undoButton, reset);
    const summary = make("div");
    summary.className = "h3studio-context-help";
    const draw = () => {
        if (!grid) return;
        canvas.width = Math.ceil(video.videoWidth / 16);
        canvas.height = Math.ceil(video.videoHeight / 16);
        const ctx = canvas.getContext("2d");
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        grid.cells.forEach((level, index) => {
            if (!level) return;
            ctx.fillStyle = `rgba(255,90,40,${0.15 + 0.5 * level / MASK_LEVELS * grid.strength})`;
            ctx.fillRect((index % grid.columns) * 2, Math.floor(index / grid.columns) * 2, 2, 2);
        });
        const count = grid.cells.filter(Boolean).length;
        summary.textContent = count
            ? `${count} painted cells · ${Math.round(grid.strength * 100)}% release · fixed for the whole block`
            : "No painted areas · original AV context unchanged";
        toggle.textContent = editing ? "Done painting" : count ? "Weaken context · mask saved" : "Weaken context…";
        undoButton.disabled = !undo.length;
        remove.hidden = enabled || !count;
    };
    const save = () => {
        if (!grid) return;
        grid.strength = Number(strength.value) / 100;
        stored = normalizeContextMask(grid);
        onChange(stored); draw();
    };
    const ready = () => {
        if (!video.videoWidth || !video.videoHeight) return;
        grid = contextMaskGrid(video.videoWidth, video.videoHeight, stored);
        grid.strength = Number(strength.value) / 100;
        toggle.disabled = !enabled;
        draw();
    };
    toggle.disabled = true; // Wait for real preview geometry, never guess it.
    video.addEventListener("loadedmetadata", ready);
    ready();
    toggle.addEventListener("click", () => {
        if (!grid || !enabled) return;
        editing = !editing; toolbar.hidden = !editing;
        canvas.style.pointerEvents = editing ? "auto" : "none";
        toggle.setAttribute("aria-expanded", String(editing));
        if (editing) video.pause();
        video.controls = !editing;
        draw();
    });
    strength.addEventListener("input", () => {
        if (grid) { grid.strength = Number(strength.value) / 100; draw(); }
    });
    strength.addEventListener("change", save);
    const remember = () => { undo.push([...grid.cells]); if (undo.length > 20) undo.shift(); };
    reset.addEventListener("click", () => { if (grid) { remember(); grid.cells.fill(0); save(); } });
    remove.addEventListener("click", () => {
        if (grid) { grid.cells.fill(0); save(); }
        else { stored = null; onChange(null); remove.hidden = true; }
    });
    undoButton.addEventListener("click", () => { if (grid && undo.length) { grid.cells = undo.pop(); save(); } });
    const point = (event) => {
        const box = canvas.getBoundingClientRect();
        return {x:(event.clientX - box.left) / box.width * video.videoWidth / 32,
            y:(event.clientY - box.top) / box.height * video.videoHeight / 32};
    };
    const paint = (event) => {
        const next = point(event);
        paintContextMask(grid, stroke.point, next, {radius:Number(radius.value),
            softness:Number(softness.value) / 100, erase:erasing});
        stroke.point = next; draw();
    };
    canvas.addEventListener("pointerdown", (event) => {
        if (!editing || !grid || event.button !== 0) return;
        event.preventDefault(); event.stopPropagation();
        remember(); stroke = {id:event.pointerId, point:point(event)};
        canvas.setPointerCapture(event.pointerId); paint(event);
    });
    canvas.addEventListener("pointermove", (event) => {
        if (!stroke || stroke.id !== event.pointerId) return;
        event.preventDefault(); event.stopPropagation(); paint(event);
    });
    const finish = (event) => {
        if (!stroke || stroke.id !== event.pointerId) return;
        event.stopPropagation(); stroke = null;
        if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
        save();
    };
    canvas.addEventListener("pointerup", finish);
    canvas.addEventListener("pointercancel", finish);
    canvas.addEventListener("lostpointercapture", finish);
    host.append(stage, toggle, remove, toolbar, summary, help);
    return host;
}
