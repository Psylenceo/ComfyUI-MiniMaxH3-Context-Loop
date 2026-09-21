import {app} from "/scripts/app.js";
import {graphNodes} from "./h3_legacy_widget_width_fix_core.mjs?v=0.7.0";
import {retireSourceAudioInput} from "./h3_retired_source_audio_core.mjs?v=0.7.0";

app.registerExtension({
    name: "MiniMaxH3ContextLoop.RetiredSourceAudio",
    afterConfigureGraph() {
        for (const node of graphNodes(app.graph)) retireSourceAudioInput(node);
    },
});
