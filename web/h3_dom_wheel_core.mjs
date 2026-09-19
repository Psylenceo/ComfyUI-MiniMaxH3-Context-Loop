// Pure routing decision for h3_dom_wheel.mjs, kept DOM-free so the decision
// logic (as opposed to the real native-scroll wiring) can be unit tested
// without a browser. See tests/_dom_wheel_routing_test.mjs.
export function resolveWheelRouting({
    hasTarget, sameGraph, targetInsideRoot, alreadyHandled, readOnly, selected, focused,
}) {
    // No active LiteGraph canvas on this node's own graph (or the canvas
    // element is itself nested inside this widget) — nothing to forward to.
    if (!hasTarget || !sameGraph || targetInsideRoot) return false;
    // A gesture the host frontend already consumed must not be forwarded again.
    if (alreadyHandled) return false;
    // A selected/focused panel keeps native scrolling and its own descendant
    // controls, unless the canvas is in hand/read-only mode.
    if (!readOnly && (selected || focused)) return false;
    return true;
}
