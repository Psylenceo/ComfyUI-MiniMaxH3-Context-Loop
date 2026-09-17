// Pure event-routing coverage for h3_dom_wheel.mjs's forwarding decision,
// runnable in plain Node with no browser (Chrome or otherwise). This does
// NOT verify real native scrolling actually occurs on an inactive-vs-active
// panel — that requires a real layout/scroll engine and stays covered only
// by tests/_dom_wheel_browser_test.mjs (which needs a Chromium browser).
import assert from "node:assert/strict";

import {resolveWheelRouting} from "../web/h3_dom_wheel_core.mjs";

const base = {
    hasTarget: true, sameGraph: true, targetInsideRoot: false,
    alreadyHandled: false, readOnly: false, selected: false, focused: false,
};

assert.equal(resolveWheelRouting(base), true, "Inactive panel with an active canvas forwards");

for (const overrides of [{hasTarget: false}, {sameGraph: false}, {targetInsideRoot: true}]) {
    assert.equal(resolveWheelRouting({...base, ...overrides}), false,
        `No active canvas on this graph must not forward: ${JSON.stringify(overrides)}`);
}

assert.equal(resolveWheelRouting({...base, alreadyHandled: true}), false,
    "A gesture already handled by the host frontend is not forwarded again");

for (const overrides of [{selected: true}, {focused: true}, {selected: true, focused: true}]) {
    assert.equal(resolveWheelRouting({...base, ...overrides}), false,
        `Active (selected/focused) panel keeps native scroll: ${JSON.stringify(overrides)}`);
}

for (const overrides of [{selected: true}, {focused: true}]) {
    assert.equal(resolveWheelRouting({...base, ...overrides, readOnly: true}), true,
        `Hand/read-only mode overrides selection or focus: ${JSON.stringify(overrides)}`);
}

console.log("H3 DOM wheel routing: forwarding decision matrix passes (no browser required)");
