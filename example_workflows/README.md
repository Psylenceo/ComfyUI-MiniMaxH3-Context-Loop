# MiniMax H3 0.7 example workflows — 0.6 catalog

These are the maintained 0.6-named workflows rebuilt for 0.7's node definitions.
They use fresh node serialization, the organized **Production Plan**, explicit
**Generation Profile**, and numbered, non-overlapping project/generation columns. Pre-0.6 examples were retired in 0.7; Git history retains them.
The **0.6** filenames identify the shared catalog baseline, not a requirement
to switch branches. This checkout's examples are intended for **0.7**.

For installation and first-run setup, see
[Getting started](../docs/GETTING_STARTED.md).

## Start here: Basic or Carousel

| Start with | What you get |
|---|---|
| [Basic](<Ref2V Basic - MiniMax H3 0.6.json>) | Direct image loaders, a Plan and scene prompts. No Carousel setup. |
| [Carousel / Studio](<Ref2V Studio - MiniMax H3 0.6.json>) | Import and tag project assets in the Carousel; edit the timeline in Plan Studio and manage takes in Checkpoint Manager. Optional source audio is already wired. |

For experimental low-to-high generation, use the single
[SelfLift Seed Hunt workflow](<Ref2V Studio SelfLift Seed Hunt - EXPERIMENTAL - MiniMax H3 0.6.json>):
review candidates or set `review_enabled=false` for automatic sampling.
See its [guide](<guides/Ref2V Studio SelfLift Seed Hunt - EXPERIMENTAL - MiniMax H3 0.6.md>).

Manual Tagged loaders are kept separately in [`tagged/`](tagged/README.md).
They are alternatives for explicit image/video/audio wiring, not extra setup
required by Basic or Carousel. Other task-specific examples are listed below.

**Sampler steps:** the Plan settings control the default; a scene's **Steps
override** takes precedence. Clear a scene override to inherit the default.
The displayed inherited value includes defaults saved inside older Plan JSON.
Changing the default in Production Plan or Plan Studio updates that saved
default too; it does not overwrite deliberate scene overrides.

Each workflow has a compact **START HERE** note. The longer setup and wiring
instructions are in its matching Markdown file under [`guides/`](guides/)
(or `tagged/guides/` for manual Tagged examples),
so they no longer take up large empty canvas panels. Titles and preview sizes
come from the layouts checked in ComfyUI for the 0.6 release. Current 0.7
sockets and controls are included in the layout checks.

The catalog is compiled from [named recipes](../tools/v06/README.md), **not**
archived workflow JSON. All H3 sockets, widget positions, and values are checked
against this **0.7 checkout**. Model files, source tracks, and extra packs still
need to be installed/selected as described below; schema validation does not
replace a GPU render test.

The loader defaults use the canonical filenames from the
[official Comfy-Org MiniMax H3 package](https://huggingface.co/Comfy-Org/MiniMax-H3):
put diffusion weights in `models/diffusion_models/`, the Qwen3-VL encoder in
`models/text_encoders/`, and both VAEs in `models/vae/`. The workflows do not
assume a custom model subfolder.

## Other generation and editing tasks

| Input or task | Recommended workflow |
|---|---|
| Text only | [T2V Normal](<T2V Normal - MiniMax H3 0.6.json>) |
| Text-only timeline, takes, and trims | [T2V Studio](<T2V Studio - MiniMax H3 0.6.json>) |
| One opening image | [I2V Normal](<I2V Normal - MiniMax H3 0.6.json>) |
| Image-led timeline, takes, and trims | [I2V Studio](<I2V Studio - MiniMax H3 0.6.json>) |
| First and last images | [FL2V Normal](<FL2V Normal - MiniMax H3 0.6.json>) |
| Video inpainting | [Masked Video Inpaint](<Masked Video Inpaint - MiniMax H3 0.6.json>) |
| Ref2VA-guided video inpainting | [Ref2V Masked Video Inpaint](<Ref2V Masked Video Inpaint - MiniMax H3 0.6.json>) |
| Continue existing video | [Masked AV Extension — Single Clip](<Masked AV Extension - Single Clip - MiniMax H3 0.6.json>) |
| Extend existing video over several scenes | [Masked AV Extension — Chain](<Masked AV Extension - Chain + Reference Image - MiniMax H3 0.6.json>) |
| Generate the exact gap between two clips | [Masked AV Bridge — Two Clips](<Masked AV Bridge - Two Clips - MiniMax H3 0.6.json>) |

## Normal and Studio

**Normal** workflows keep the familiar scene-column Production Plan and the
standard prompt editor. They are the smallest useful graphs.

**Studio** workflows add the Plan Studio timeline, Project Asset Carousel, rich
prompt editor, and Checkpoint Manager. The Carousel owns the project/run name,
reference lineage, and optional Source Track. Checkpoint Manager browses saved
scenes, alternate branches, trims, previews, and restoration state. These
authoring tools do not change the sampler body.

To initialize a Studio reference project:

1. Copy or import the two 0.6 courier images from [`assets/`](assets/).
2. In Project Asset Carousel, tag the arrival image `courier_arrival` and the
   delivery image `greenhouse_delivery`; assign both the **Picture** role.
3. For an optional source soundtrack, import one audio file and select
   **Audio use → Project timeline source**. In Generation Profile, select
   **Lip-sync to source audio** (and **Visual continuity** to match the former
   Source Audio preset). Otherwise leave **Generate audio** selected.
4. Edit the example scenes and queue the workflow.

For vocal-only lip-sync, import aligned full-length stems and assign them under
the Source track's **Synchronized audio tracks**. The full mix remains the
soundtrack; vocals guide generation. Use the scene **Lip-sync** selector in
Plan Studio for instrumental/action-only scenes. See [grouped audio](../docs/AUDIO_AND_CONTINUITY.md#grouped-songs-full-mix-vocals-and-instrumental).

The Carousel stores media as ordinary project assets rather than embedding
image bytes or stale file bindings into workflow JSON.

## Shared authoring contract

Every maintained recursive workflow uses:

- **MiniMax H3 Generation Profile** for scene continuity and audio intent;
- **MiniMax H3 Production Plan** for scene columns, timing, seeds, and output;
- 20-step `res_multistep` / `simple` sampling defaults;
- explicit carried-overlap language in continuation prompts, so new action
  begins after the inherited boundary rather than being cut short;
- a muted manifest/assembly recovery path where appropriate.

The retired degradation-experiment controls are no longer present; see
[0.7 migration notes](../docs/MIGRATING_TO_0_7.md). The original Plan remains supported.
Carousel ownership proofs are blank in these templates. Workflow ownership
locking defaults to on: claim ownership explicitly when editing a protected
run. It can be disabled server-wide under **Settings → MiniMax H3 Context Loop
→ Project safety → Workflow ownership locking**. This does not disable file
transaction locks or deletion safeguards.
Pending Review and Chapter Delivery remain available as optional nodes;
these baseline examples do not enable them automatically.

In I2V workflows, do not bypass **Frame Gate**. It prevents the opening picture
from being reapplied to every scene. FL2V uses Frame Index Switch plus Frame
Gate to demonstrate an A→B→A endpoint sequence.

For prompt syntax, see [Scene authoring](../docs/SCENE_AUTHORING.md). For tags,
motion references, and source media, see
[Scheduled references](../docs/SCHEDULED_REFERENCES.md).

## Masked editing and existing video

The masked workflows keep the bundled CC0 soldier-crab source because it gives
the mask, source-video, source-audio, and protected-boundary examples a legal,
reproducible common input. Copy the required files from `assets/` to
`ComfyUI/input/` before opening those workflows.

The two-clip bridge is a single masked target with an ordinary sampler. The
extension and inpaint workflows use the checkpoint/review/resume loop. See
[Masked editing](../docs/MASKED_EDITING.md) for H3 grid behavior and audio
protection.

## Deferred De-Rope and upscale

Deferred workflows start from a lineage selected in Checkpoint Manager; they
are not first-install tests.

| Workflow | Extra requirement |
|---|---|
| [De-Rope Only — source resolution](<Deferred De-Rope Only - MiniMax H3 0.6.json>) | [ComfyUI-MAINodes](https://github.com/matlowai/ComfyUI-MAINodes) v1.1.3+; no spatial upscaler |
| [De-Rope Only — Fast Turbo](<Deferred De-Rope Only - Fast Turbo - MiniMax H3 0.6.json>) | MAINodes v1.1.3+ and LightX2V 4-step v1.0 768p ComfyUI bf16 LoRA; [recipe and limits](<guides/Deferred De-Rope Only - Fast Turbo - MiniMax H3 0.6.md>) |
| [SeedVR2 Full Chain](<Deferred Upscale - SeedVR2 Full Chain - MiniMax H3 0.6.json>) | [ethanfel SeedVR2 fork](https://github.com/ethanfel/ComfyUI-SeedVR2_VideoUpscaler) |
| [H3 LBH 3D](<Deferred Upscale - H3 LBH 3D - MiniMax H3 0.6.json>) | [LBH H3 latent upscaler](https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler) |
| [H3 LBH 3D Split — Experimental](<Deferred Upscale - H3 LBH 3D Split - EXPERIMENTAL - MiniMax H3 0.6.json>) | LBH pack with **MMH3 Split Upscale** (August 28+); temporal/spatial refinement, source audio retained. **No locked previous-HQ scene prefix**; starts with one scene. [Settings, safeguards and limits](<guides/Deferred Upscale - H3 LBH 3D Split - EXPERIMENTAL - MiniMax H3 0.6.md>) |
| [H3 LBH 3D + De-Rope](<Deferred Upscale + De-Rope - H3 LBH 3D - MiniMax H3 0.6.json>) | LBH pack plus [ComfyUI-MAINodes](https://github.com/matlowai/ComfyUI-MAINodes) |
| [Pixel DLSS5 + USDU — Experimental](<Deferred Upscale - Pixel DLSS5 + USDU - EXPERIMENTAL - MiniMax H3 0.6.json>) | [DLSS5](https://github.com/Blueforcer/ComfyUI-DLSS5-Enhancer), [H3 USDU Guider fork](https://github.com/lisitskyaa/ComfyUI_UltimateSDUpscaleGuider_H3), and Turbo v4 LoRA; [setup and testing limits](<guides/Deferred Upscale - Pixel DLSS5 + USDU - EXPERIMENTAL - MiniMax H3 0.6.md>) |
| [Pixel USDU Continuity — Experimental](<Deferred Upscale - Pixel USDU Continuity - EXPERIMENTAL - MiniMax H3 0.6.json>) | Same models/DLSS5/USDU plus file-backed `ComfyUI-ContextAnchoredTile-videopath` transport (no CAT refinement). Checkpoint Manager per-scene **Continue previous shot** toggle; hard cuts bypass protection. [Setup, resume and limitations](<guides/Deferred Upscale - Pixel USDU Continuity - EXPERIMENTAL - MiniMax H3 0.6.md>) |
| [DLSS5 + LMS Guide — Experimental](<Deferred Upscale - DLSS5 + LMS Guide - EXPERIMENTAL - MiniMax H3 0.6.json>) | [LMS](https://huggingface.co/Alissonerdx/Minimax-H3-ComfyUI), Ref2V Turbo 4step v0.1, and [DLSS5](https://github.com/Blueforcer/ComfyUI-DLSS5-Enhancer). Full-scene guide refinement, fresh target, original audio; [setup and memory limits](<guides/Deferred Upscale - DLSS5 + LMS Guide - EXPERIMENTAL - MiniMax H3 0.6.md>) |

Upscaled variants never replace source checkpoints. New projects save media
under `output/h3_chains/<run>/processing/<scope>/<profile>/` and assembled
videos under `exports/videos/<scope>/pass-<profile>/`; technical manifests and
latents stay in `.h3/`. Existing projects retain their legacy paths. See
[storage layout](../docs/SIMPLE_CHAIN_LAYOUT.md) and
[Runs and recovery](../docs/RUNS_AND_RECOVERY.md).

**Assemble an already-saved upscale**

Add **MiniMax H3 Upscale Manifest Load**, paste the path to the saved
`upscale_manifest.json`, and connect its **manifest** output directly to
**H3 Chain Assemble**. This bypasses the upscale loop entirely: no scene needs
to be regenerated, and no source Plan or model loaders are required.

For a new-layout, unchaptered Original run the file is
`output/h3_chains/<run>/.h3/upscaled/<profile>/upscale_manifest.json`.
Chapter/working-branch processing state lives below `.h3/chapters/` or
`.h3/branches/`. Legacy runs use the same internal trees without the `.h3/`
prefix. Use the actual saved manifest, not a guessed path for another scope.
An unfinished run can use its saved `partial/through_clip_NNNN.manifest.json`
instead; it stays partial.
The node accepts an absolute path or one relative to ComfyUI's output folder.
It reads and verifies only when queued, without scanning or rewriting projects.
Keep the saved upscale's media and checkpoints; loading does not reconstruct
deleted artifacts. Checkpoint Manager's processing tabs remain preview-only
except for the existing DeRoPE source selection.

The SeedVR2 workflow uses the current **SeedVR2 Video Path Upscaler** node
(`SeedVR2VideoPathUpscaler`), not the retired `SeedVR2DirectVideoUpscaler` ID.
Install the base SeedVR2 model-loader pack as well as the linked video-path
extension. For LBH, the temporal-chunking and unload controls are explicitly
saved; the De-Rope injection preset is **custom**, with 20 total steps and 0.5
injection, so it does not silently override the visible schedule.

**De-Rope Only** preserves each source scene's resolution and original audio
performance. Select the Original branch, choose a new profile and scene range,
and keep full-latent saving enabled. The resulting DeRoPE branch can be selected
as the source of a later upscale in a different profile; missing processed
scenes fall back to the original source. The combined LBH + De-Rope graph remains
available when spatial enlargement and motion repair should share one pass.

The base-only graph runs 10 actual steps (`simple`, 20 total, injection 0.5);
the Fast Turbo graph runs 3 (`beta`, 6 total, injection 0.5) with
`gradient_estimation` and the matching enabled LoRA. These are distinct recipes,
not interchangeable sampler/LoRA toggles. Prefer a base-generated source for
the turbo repair; repeated deep turbo passes can damage the image.

All three De-Rope graphs expose a manual range gate before the chain boundary
guard and budget/final-frame previews. Ranges use the zero-based RAW scene clock,
including carried context; select one scene for a scene-specific edit. Budget
derives actual steps from SIGMAS and feeds Time Smear's estimate automatically.
Reports do not pause execution or promise an OOM-free render: expanded pixel
batches still reside in RAM. No experimental DyRoPE, motion-adapter or streamed
attention patch is enabled by these templates.

## Assets and provenance

The arrival/delivery pictures were generated specifically as neutral 0.6
reference assets. The masked examples use CC0 footage. Exact hashes, licenses,
copy instructions, and the older 0.5 reference provenance are documented in
[`assets/README.md`](assets/README.md).

ComfyUI cannot load arbitrary files from the custom-node repository directly;
copy them to `ComfyUI/input/` or import them through Project Asset Carousel.
