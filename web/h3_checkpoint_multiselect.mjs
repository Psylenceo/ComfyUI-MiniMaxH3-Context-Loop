// UI-only selection. Never writes the workflow's output/branch selection.
export function checkpointClickSelection(selected, ordered, anchor, key, event) {
    const additive = event.ctrlKey || event.metaKey;
    if (event.shiftKey && ordered.includes(anchor)) {
        const a = ordered.indexOf(anchor), b = ordered.indexOf(key);
        const next = new Set(additive ? selected : []);
        if (b >= 0) for (const item of ordered.slice(Math.min(a, b), Math.max(a, b) + 1)) next.add(item);
        return next;
    }
    const next = new Set(additive ? selected : []);
    if (additive && next.has(key)) next.delete(key);
    else next.add(key);
    return next;
}

export function checkpointBoxHits(cards, box, viewport) {
    return cards.filter(({rect}) => {
        const left = Math.max(rect.left, viewport.left), right = Math.min(rect.right, viewport.right);
        const top = Math.max(rect.top, viewport.top), bottom = Math.min(rect.bottom, viewport.bottom);
        return right > left && bottom > top && right >= box.left && left <= box.right
            && bottom >= box.top && top <= box.bottom;
    }).map(({key}) => key);
}

export function mountCheckpointMultiSelect(container, {enabled, onChange}) {
    let selected = new Set(), anchor = null, gesture = null, suppressClick = false, clickTimer;
    const cards = () => [...container.querySelectorAll('[data-bulk-key]')];
    function paint() {
        for (const card of cards()) {
            const active = selected.has(card.dataset.bulkKey);
            card.classList.toggle('h3cm-bulk-selected', active);
            card.setAttribute('aria-pressed', String(active));
        }
    }
    function update(next) {
        const changed = selected.size !== next.size || [...selected].some(key => !next.has(key));
        selected = next;
        paint();
        if (changed) onChange([...selected]);
    }
    function click(event) {
        if (suppressClick) { event.preventDefault(); event.stopImmediatePropagation(); return; }
        if (!enabled()) return;
        const card = event.target.closest('[data-bulk-key]');
        if (!card || !container.contains(card)) return;
        const key = card.dataset.bulkKey;
        if (event.ctrlKey || event.metaKey || event.shiftKey) {
            event.preventDefault(); event.stopImmediatePropagation();
            update(checkpointClickSelection(selected, cards().map(c => c.dataset.bulkKey), anchor, key, event));
            if (!event.shiftKey || !anchor) anchor = key;
        } else {
            anchor = key;
            update(new Set()); // Normal click continues through to the preview handler.
        }
    }
    function down(event) {
        if (!enabled() || event.button !== 0 || !event.shiftKey) return;
        const scroll = event.target.closest('.h3cm-fork-scroll');
        if (!scroll || !container.contains(scroll)) return;
        if (event.target.closest('button,a,input,select,[role="button"]')
                && !event.target.closest('[data-bulk-key]')) return;
        event.stopPropagation();
        gesture = {x:event.clientX, y:event.clientY, scroll, id:event.pointerId,
            base:new Set(event.ctrlKey || event.metaKey ? selected : []), before:new Set(selected), overlay:null};
    }
    function move(event) {
        if (!gesture || event.pointerId !== gesture.id) return;
        if (!gesture.overlay && Math.hypot(event.clientX - gesture.x, event.clientY - gesture.y) < 4) return;
        event.preventDefault(); event.stopPropagation();
        if (!gesture.overlay) {
            gesture.overlay = document.createElement('div');
            gesture.overlay.className = 'h3cm-selection-box';
            document.body.append(gesture.overlay);
            try { gesture.scroll.setPointerCapture?.(gesture.id); } catch { /* synthetic pointer or detached view */ }
        }
        const box = {left:Math.min(gesture.x,event.clientX), top:Math.min(gesture.y,event.clientY),
            right:Math.max(gesture.x,event.clientX), bottom:Math.max(gesture.y,event.clientY)};
        Object.assign(gesture.overlay.style, {left:box.left+'px', top:box.top+'px',
            width:(box.right-box.left)+'px', height:(box.bottom-box.top)+'px'});
        const hits = checkpointBoxHits([...gesture.scroll.querySelectorAll('[data-bulk-key]')].map(card => ({
            key:card.dataset.bulkKey, rect:card.getBoundingClientRect(),
        })), box, gesture.scroll.getBoundingClientRect());
        update(new Set([...gesture.base, ...hits]));
    }
    function finish(event, cancel = false) {
        if (!gesture || (event?.pointerId != null && event.pointerId !== gesture.id)) return;
        if (gesture.overlay) {
            gesture.overlay.remove();
            try { gesture.scroll.releasePointerCapture?.(gesture.id); } catch { /* pointer already released */ }
            suppressClick = true;
            clearTimeout(clickTimer);
            clickTimer = setTimeout(() => { suppressClick = false; }, 0);
            if (cancel) update(gesture.before);
            else anchor = [...selected].at(-1) ?? anchor;
            event?.preventDefault(); event?.stopPropagation();
        }
        gesture = null;
    }
    const up = event => finish(event);
    const cancel = event => finish(event, true);
    const blur = () => finish(null, true);
    const escapeGesture = event => { if (gesture) keydown(event); };
    function keydown(event) {
        if (event.key !== 'Escape') return;
        if (!gesture && !selected.size) return;
        event.preventDefault(); event.stopPropagation();
        if (gesture) finish(null, true);
        else update(new Set());
    }
    container.addEventListener('click', click, true);
    container.addEventListener('pointerdown', down, true);
    container.addEventListener('keydown', keydown);
    window.addEventListener('pointermove', move, true);
    window.addEventListener('pointerup', up, true);
    window.addEventListener('pointercancel', cancel, true);
    window.addEventListener('keydown', escapeGesture, true);
    window.addEventListener('blur', blur);
    return {
        keys:() => [...selected],
        select(keys) { finish(null, true); anchor = keys.at(-1) ?? null; update(new Set(keys)); },
        clear() { finish(null, true); anchor = null; update(new Set()); },
        reconcile() {
            finish(null, true);
            const visible = new Set(cards().map(c => c.dataset.bulkKey));
            if (!visible.has(anchor)) anchor = null;
            update(new Set([...selected].filter(key => visible.has(key))));
        },
        destroy() {
            finish(null, true); clearTimeout(clickTimer);
            container.removeEventListener('click', click, true);
            container.removeEventListener('pointerdown', down, true);
            container.removeEventListener('keydown', keydown);
            window.removeEventListener('pointermove', move, true);
            window.removeEventListener('pointerup', up, true);
            window.removeEventListener('pointercancel', cancel, true);
            window.removeEventListener('keydown', escapeGesture, true);
            window.removeEventListener('blur', blur);
        },
    };
}
