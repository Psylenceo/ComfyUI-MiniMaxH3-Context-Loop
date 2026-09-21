import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {RETIRED_SOURCE_AUDIO_NODES, retireSourceAudioInput} from "../web/h3_retired_source_audio_core.mjs";

for (const type of RETIRED_SOURCE_AUDIO_NODES) {
    const links = {42: {target_slot: 2}};
    const node = {
        type,
        inputs: [{name: "plan", link: 1}, {name: "source_audio", link: null},
            {name: "source_timeline", link: 42}],
        removeInput(index) {
            this.inputs.splice(index, 1);
            for (const link of Object.values(links)) if (link.target_slot > index) link.target_slot--;
        },
    };
    assert.equal(retireSourceAudioInput(node), true);
    assert.deepEqual(node.inputs.map(input => input.name), ["plan", "source_timeline"]);
    assert.equal(node.inputs[1].link, 42);
    assert.equal(links[42].target_slot, 1);
    assert.equal(retireSourceAudioInput(node), false);
    node.inputs.splice(1, 0, {name: "source_audio", link: 99});
    assert.equal(retireSourceAudioInput(node), false);
    assert.equal(node.inputs[1].link, 99, "Connected legacy wires must remain visible for explicit rewiring");
    assert.match(node.inputs[1].label, /REMOVED/);
}
for (const type of ["MiniMaxH3SourceTimeline", "MiniMaxH3ChainExternalVideo",
    "MiniMaxH3ReferenceVideoPrepare", "MiniMaxH3ChainPass2Prepare"]) {
    const node = {type, inputs: [{name: "source_audio", link: null}], removeInput() {throw Error("Active input removed");}};
    assert.equal(retireSourceAudioInput(node), false);
}
const extension = readFileSync(new URL("../web/h3_retired_source_audio.js", import.meta.url), "utf8");
assert.match(extension, /afterConfigureGraph/);
assert.doesNotMatch(extension, /beforeConfigureGraph/);
console.log("Retired source audio: unused sockets removed after loading; other links and active inputs preserved");
