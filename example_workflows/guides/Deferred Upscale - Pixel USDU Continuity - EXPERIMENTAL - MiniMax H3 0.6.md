# Deferred Upscale - Pixel USDU Continuity - EXPERIMENTAL - MiniMax H3 0.6

For **0.7**: experimental file-backed USDU continuity. Mark only direct continuations in Checkpoint Manager; hard cuts remain independent.

Local validation (2026-09-17): real USDU GPU runs on a duplicated masked-AV dog continuation reduced the boundary frame-difference metric by about 16%. This is not a perceptual quality score. The 2-scene save/assembly loop retained all 209 delivered frames and bit-identical checkpoint audio. Fresh DLSS worker startup stalled locally, including after releasing H3 models; the successful loop used saved DLSS outputs from the earlier research. Fresh DLSS-through-assembly execution is therefore NOT validated. See [test notes](../../docs/pixel-continuity-research.md) and the limits below.

Setup controls come first, followed by numbered generation columns.

PIXEL CONTINUITY — 0.7 EXPERIMENTAL

Choose the complete saved branch in Checkpoint Manager. In the Original scene inspector, tick “Continue previous shot (pixel upscale)” on each incoming continuous-shot boundary. This is a workflow-local export choice, saved with selection_json. It does not change generation, the Plan, audio, or project files. Both adjacent revision identities must still match. Picture-only ALTs do not inherit a base take's mark. Unmarked shots use independent USDU even when they were generated with masked AV context.

Current Scene -> DLSS VIDEO -> Protect Tail -> VIDEO Conditioning -> USDU VIDEO -> Finish -> Save + Loop End -> Assemble.

Protect Tail reads the previous saved HQ segment from this same upscale profile, including when resuming. It replaces the current RAW repeated head with the exact matching HQ tail, outputs a black-head/white-future mask, and enables USDU anchor_context. Only a single immediate previous-tail visual window is supported. Multiple reference blocks, non-tail windows and differing visual/trim lengths need independent processing. If the previous HQ scene is missing, start earlier instead of silently substituting unrelated footage.

Finish restores the protected prefix exactly after USDU and can correct a small RGB tone bias at the start of the new footage. It compares corresponding source/refined frames, caps each channel shift to 0.03, and smoothly fades it over 39 frames. No optical flow, duplicated delivery frames, image crossfade or smoothing of detail is used. Set tone_strength=0 to disable this experimental correction. Source lighting changes can still make automatic matching inappropriate; inspect your join.

Save and Loop End receive the SAME final RAW VIDEO. Existing save logic removes the repeated head once and preserves checkpoint audio. Plan editorial trims still apply at final assembly. Protection uses the generated overlap, never shortened presentation trims. The previous saved HQ file is used consistently in live and resumed runs, so its export compression is also the carry quality; choose your segment CRF accordingly.

Start with a NEW profile, backend=pixel and save_latent=false. Resume the same profile at the first unfinished scene. If a continuity mark changes on an already-upscaled scene, reprocess from there; generation does not need to run again. The recipe_json describes the settings but cannot track arbitrary edits to external nodes: use a new profile or update the recipe when changing model, DLSS, USDU or tone controls.

File-backed VIDEO and disk canvas avoid full-scene float RGB tensors between nodes. Decoding, disk I/O, per-frame masks and model tile tensors still cost memory and time. Set canvas_directory to a fast local SSD, not tmpfs/network storage. The helper performs no project scanning or work during workflow loading.

Dependencies: ComfyUI-DLSS5-Enhancer; ComfyUI_UltimateSDUpscaleGuider_H3; ComfyUI-ContextAnchoredTile-videopath. The latter supplies file-backed transport; CAT refinement is NOT used. Choose your installed H3 model, Qwen encoder, video VAE and Turbo v4 LoRA. Ref2VA is included for source reference recovery; the small dog mechanism test used FL2VA. Use the model/LoRA setup appropriate to your original generation.

VALIDATION

CPU/JS coverage checks selection persistence, revision isolation, hard-cut bypass, protected frames, unchanged RAW counts and resume contracts. GPU research uses a real 2-part masked-AV dog continuation (512x288 -> 1024x576), not an artificial cut of one clip. This is a limited mechanism test, not proof of seamless production-resolution results. Start with a short representative range. The independent pixel workflow remains available unchanged.

OPTIONAL BOUNDARY EXPERIMENT

On the Boundary Experiment node, enabled=false (default) keeps the existing workflow. Set enabled=true BEFORE processing a NEW upscale profile to try a short joint refinement after assembly. Only Checkpoint Manager's marked direct continuations qualify; hard cuts remain independent. Keep Boundary Sources saves just 17 pre-USDU/DLSS frames per side by default in the child profile. The final pass uses a 73-frame window: 22 fixed HQ frames, 34 editable pre-USDU frames spanning the join, and 17 fixed HQ frames. It pastes only the middle 34 frames into a SEPARATE lossless _boundary_*.mkv. Original assembly and all HQ checkpoints stay unchanged. Audio is stream-copied from the assembled output; there is no crossfade or interpolation.

frames_per_side supports 17/34/51 on the H3 temporal grid. Denoise defaults to 0.20, steps to 3; sampler er_sde/beta, fixed 512x288 tiles with 64 padding and disk canvas. Empty canvas_directory uses ComfyUI temp: choose a local SSD if temp is RAM-backed. These controls are automatically added to the Adapter recipe when enabled. Switching ON/OFF or changing experiment settings requires a new profile or reprocessing, not resuming incompatible HQ clips. Missing samples require re-upscaling the affected pair with the switch ON.

Experimental Boundary Export runs after Assemble. Use ITS video_path output for the experimental result; Assemble's output is still the baseline. Joins removed by editorial trims, gaps/reordering, too-short scenes or overlapping windows are skipped and reported. The pass rebuilds the incoming scene's prompt/references with the connected shared model + Turbo setup; whole-scene keyframes are not reused on the shorter window. It does not replay per-scene LoRA routes. Normal assembly settings (no extra blend/tone/stabilization) are recommended for this comparison.

This experiment costs another USDU pass per eligible join plus a lossless export of the whole film (potentially large). It does nothing at workflow load and no extra processing when OFF. In the small dog test, the pre-USDU-center variant improved the join metric ~6%, but worsened both replacement-edge metrics ~6–8%. It is an A/B option, NOT a guaranteed seam fix. Only the 17-frame-per-side configuration has been GPU-tested.
