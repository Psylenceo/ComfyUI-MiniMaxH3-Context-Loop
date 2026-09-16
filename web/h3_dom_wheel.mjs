// DOM widgets are overlays, not children of the LiteGraph canvas. An inactive
// panel must explicitly forward wheel input to the canvas's own zoom/pan handler.
export function bindNodeWheel(root, node, app) {
    // Also opt focused controls into native wheel capture in the Vue renderer.
    root.setAttribute("data-capture-wheel", "true");

    root.addEventListener("wheel", (event) => {
        const canvas = app.canvas;
        const target = canvas?.canvas;
        if (!target || !node.graph || canvas.graph !== node.graph || root.contains(target)) return;
        // Do not process a gesture already handled by the host frontend.
        if (event.defaultPrevented) return;
        const selected = canvas.selectedItems?.has(node)
            || canvas.selected_nodes?.[node.id] === node;
        const focused = root.contains(root.ownerDocument.activeElement);
        if (!canvas.read_only && (selected || focused)) return;

        event.preventDefault();
        event.stopPropagation();
        const Wheel = target.ownerDocument.defaultView.WheelEvent;
        // Non-bubbling, as in Comfy's forwarding: only the actual canvas handles
        // the clone. Preserve position, units and modifiers for its nav settings.
        target.dispatchEvent(new Wheel("wheel", {
            cancelable:true,
            clientX:event.clientX, clientY:event.clientY,
            screenX:event.screenX, screenY:event.screenY,
            deltaX:event.deltaX, deltaY:event.deltaY, deltaZ:event.deltaZ,
            deltaMode:event.deltaMode,
            ctrlKey:event.ctrlKey, metaKey:event.metaKey,
            shiftKey:event.shiftKey, altKey:event.altKey,
        }));
    }, {capture:true, passive:false});
    // Active panels keep native scrolling and their descendant wheel controls
    // (e.g. Studio timeline zoom) without also moving the workflow canvas.
    root.addEventListener("wheel", (event) => event.stopPropagation());
}
