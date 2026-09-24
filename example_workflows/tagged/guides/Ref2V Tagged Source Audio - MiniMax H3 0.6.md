# Ref2V Tagged Source Audio - MiniMax H3 0.6

Maintained for **0.7** from the released **0.6 workflow catalog**. H3 settings and sockets are validated against this checkout.

Setup controls come first, followed by numbered generation columns. Recovery is disabled by default. Enable it only to assemble saved clips without sampling.

0.6 ORIGINAL REFERENCE EXAMPLE

The matching courier and greenhouse pictures were generated specifically for this catalog. They replace the old community demonstration assets and prompts.

---

REF2VA PROMPT FORMAT — SIX SECTIONS

Keep subject_definitions, summary, retention_analysis, detailed_description, overall_soundscape, and non_diegetic_music in that order. References define identity; the prompt defines the new action.

---

REF2V 0.6 QUICK START

Copy both courier PNGs to ComfyUI/input. The two picture references are activated as courier_arrival and greenhouse_delivery. Edit the Plan, then queue and approve each checkpointed scene.

---

The two amber recovery nodes are MUTED by default.

If all segments finished but final assembly did not, mute the main green Assemble node, enable both recovery nodes, and queue. They validate every SHA-256 checkpoint pair and assemble without rerendering the completed clips.

For an interrupted generation, leave recovery muted and set Loop Start's start_clip to the first unfinished clip.

---

INDEPENDENT SOURCE AUDIO — NO CAROUSEL

Upload/select your complete soundtrack in Load Audio. Copy the two supplied courier/greenhouse pictures into ComfyUI/input, or replace the Load Image selections and matching prompt tags. The soundtrack must cover the complete planned timeline; adjust scene lengths to your track.

Load Audio feeds Source Timeline and Tagged Audio Ref with the same FULL track. Source Timeline feeds both Preflight and Loop Start. Current Scene.state feeds Tagged Ref2VA.state, so source_timeline references resolve the correct window on every scene. Never feed Current Scene.source_audio_slice into Tagged Audio Ref: its fingerprint connection back to the Plan would create a cycle. Downstream nodes recover the track from state; no repeated full-AUDIO wiring is needed.

Lip-sync to source audio locks the scene's exact source audio and uses the source track in final assembly. The preset alone does not load audio. @soundtrack is available as an optional prompt reference; the supplied picture prompts do not use it. For a voice reference only (not a timed soundtrack), use Tagged Audio Ref timeline_mode=standalone with a generated-audio profile; Source Timeline is not required.

The Audio VAE must feed **both Tagged Ref2VA.audio_vae and Apply Scene Context.audio_vae**. The latter encodes and locks the source audio in the sampler target; the example now includes this connection.

To mention the soundtrack in a prompt, use the exact registered tag: `@soundtrack` by default, or rename the Tagged Audio Ref to `audio_1` when your prompt uses `@audio_1`. A matching tag supplies that scene's audio window as a native reference while lip-sync keeps the target audio locked. It does not enable automatic loose audio reference or generated-audio continuity. Leaving the tag out of the prompt still allows lip-sync through the locked target.

For an existing Ref2V Tagged workflow, add the same source connections, connect Current Scene.state to Tagged Ref2VA.state, and connect Audio VAE to Apply Scene Context.audio_vae.
