// Compact, Plan-owned token-grid masks. No browser or ComfyUI dependencies.
export const CONTEXT_MASK_MODES = Object.freeze([
    "masked_av", "feathered_av", "audio_feathered_av",
]);
export const MASK_LEVELS = 16;

export function normalizeContextMask(value) {
    if (value == null) return null;
    if (typeof value !== "object" || Array.isArray(value)) {
        throw new Error("Context weaken mask must be an object.");
    }
    const {columns, rows, cells, strength = 0.5} = value;
    if (![columns, rows].every((n) => Number.isInteger(n) && n >= 1 && n <= 512)) {
        throw new Error("Context weaken mask needs 1–512 columns and rows.");
    }
    if (!Array.isArray(cells) || cells.length !== columns * rows
            || !cells.every((n) => Number.isInteger(n) && n >= 0 && n <= MASK_LEVELS)) {
        throw new Error("Context weaken mask cells must match its grid (values 0–16).");
    }
    if (typeof strength !== "number" || !Number.isFinite(strength) || strength < 0 || strength > 1) {
        throw new Error("Context weaken strength must be between 0 and 1.");
    }
    if (!cells.some(Boolean)) return null;
    return {columns, rows, cells:[...cells], strength};
}

export function contextMaskGrid(width, height, value = null) {
    const columns = Math.ceil(width / 32), rows = Math.ceil(height / 32);
    const previous = normalizeContextMask(value);
    const cells = Array.from({length:columns * rows}, (_, index) => {
        if (!previous) return 0;
        const x = Math.floor((index % columns) * previous.columns / columns);
        const y = Math.floor(Math.floor(index / columns) * previous.rows / rows);
        return previous.cells[y * previous.columns + x];
    });
    return {columns, rows, cells, strength:previous?.strength ?? value?.strength ?? 0.5};
}

export function paintContextMask(mask, from, to, {radius = 2, softness = 0.5, erase = false} = {}) {
    // Coordinates and radius are in token cells. Cover the whole pointer
    // segment so fast drags don't leave holes between input events.
    const dx = to.x - from.x, dy = to.y - from.y;
    const length2 = dx * dx + dy * dy;
    for (let y = Math.max(0, Math.floor(Math.min(from.y, to.y) - radius));
        y < Math.min(mask.rows, Math.ceil(Math.max(from.y, to.y) + radius)); y++) {
        for (let x = Math.max(0, Math.floor(Math.min(from.x, to.x) - radius));
            x < Math.min(mask.columns, Math.ceil(Math.max(from.x, to.x) + radius)); x++) {
            const t = length2 ? Math.max(0, Math.min(1,
                ((x + 0.5 - from.x) * dx + (y + 0.5 - from.y) * dy) / length2)) : 0;
            const distance = Math.hypot(x + 0.5 - from.x - t * dx, y + 0.5 - from.y - t * dy);
            if (distance > radius) continue;
            const coverage = softness > 0
                ? Math.min(1, (radius - distance) / (radius * softness)) : 1;
            const level = Math.round(MASK_LEVELS * coverage);
            const index = y * mask.columns + x;
            mask.cells[index] = erase ? Math.min(mask.cells[index], MASK_LEVELS - level)
                : Math.max(mask.cells[index], level);
        }
    }
    return mask;
}
