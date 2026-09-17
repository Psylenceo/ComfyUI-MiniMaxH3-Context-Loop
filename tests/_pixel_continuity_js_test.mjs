import assert from "node:assert/strict";
import {checkpointContinuitySelection as normalize, checkpointSetContinuity as set} from "../web/h3_checkpoint_manager_core.mjs";

const a = {scene:1, revision:"a"}, b = {scene:2, revision:"b"}, c = {scene:3, revision:"c"};
const selection = JSON.stringify({run_name:"demo", lineage:[a,b,c], output_mode:"workflow_local"});
const marked = set(selection, b, true);
assert.equal(JSON.parse(marked).pixel_continuity.length, 1);
assert.equal(JSON.parse(normalize(selection, marked)).pixel_continuity.length, 1, "refresh preserves marks");
assert.equal(JSON.parse(normalize(marked)).output_mode, "workflow_local");
const off = normalize(set(marked, b, false), marked);
assert.equal(JSON.parse(off).pixel_continuity, undefined, "turning off must not inherit the previous mark");
assert.equal(JSON.parse(normalize(JSON.stringify({run_name:"other", lineage:[a,b,c]}), marked)).pixel_continuity, undefined);
for (const lineage of [[{...a,revision:"x"},b,c], [a,{...b,revision:"x"},c], [a]]) {
    assert.equal(JSON.parse(normalize(JSON.stringify({run_name:"demo",lineage}), marked)).pixel_continuity, undefined);
}
assert.throws(() => set(selection,a,true), /previous scene/);
assert.throws(() => set(selection,{...b,revision:"x"},true), /output branch/);
console.log("Pixel continuity selection: refresh, disable, project isolation and revision binding passed");
