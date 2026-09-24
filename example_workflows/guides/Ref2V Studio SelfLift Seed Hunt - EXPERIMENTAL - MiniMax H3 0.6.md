# Ref2V Studio SelfLift Seed Hunt - EXPERIMENTAL - MiniMax H3 0.6

Experimental **0.7** SelfLift example, using Seed Hunt for both automatic and reviewed low-to-high generation. It replaces the separate ordinary SelfLift example, not the sampler node or existing user workflows. KJNodes and taeh3.safetensors in models/vae_approx are needed only for enabled seed-review previews; review_enabled=false does not require them. Tiny previews are approximate, silent inspection aids.

Setup controls come first, followed by numbered generation columns. Recovery is disabled by default. Enable it only to assemble saved clips without sampling.

EXPERIMENTAL SELFLIFT CHAIN — 0.7

Open this separate workflow; your existing workflows do not need rewiring. It reuses the catalog's neutral courier example and assets. Use a NEW run name or a duplicate project for initial tests.

## Project switch
SelfLift Project is after Plan Studio and before Loop Start.
- OFF (the shipped default): ordinary single-stage Euler sampling at Plan resolution.
- ON: early steps at lowres_scale times the final width/height (default 0.5), the selected latent lift, then the final steps at Plan resolution.
- Plan width/height are the FINAL dimensions. The low latent grid is rounded to H3's even spatial token grid.
- Pick a compatible H3 3D-convolution learned checkpoint in models/latent_upscale_models. The live smoke test used minimax_h3_latent_upscaler_3d_conv_v1_bf16.safetensors from https://huggingface.co/LBH-123-AI/Minimax_h3_latent_Upscaler. Generic image/LTX and H3 2D upscalers are not interchangeable with this 3D runtime.
- high_resolution_steps must be less than the scene's total steps, including scene overrides. Example: 20 total / 5 high = 15 low + 5 high; for an 8-step model use 2 high.
- Euler is the default. Experimental RES4LYF fully_implicit/radau_ia_2s is also supported with its matching setup. Optional MiniMax H3 SelfLift Tiling — Experimental connects to highres_tiling on Seed Hunt and tiles the high-resolution denoising pass, not the learned latent lift. Leave it disconnected for normal full-frame execution. No TST is used.
- This experimental H3 adaptation defaults to the direct latent route (rho=0). SelfLift Project exposes lowres_scale, rho, w_min and w_max. Enabling rho adds a video VAE decode / pixel resize / VAE encode; it costs time/memory and is not a guaranteed artifact fix. Keep 0 <= w_min <= w_max <= 1; defaults are 0.5 / 0 / 0.5 / 1.

## References, masks and audio
Ordinary tagged Ref2VA assets stay at their native reference representation. Spatial keyframe/guide latents resize for the low stage; the high stage receives the untouched target-resolution guides. Neither time nor audio length is scaled.
Native AV masks, painted context masks and locked source audio are carried through both passes. Chain Context has BOTH VAEs wired. Choose the usual Generation Profile and per-scene context/audio settings; hard cuts remain hard cuts.
The Scene LoRA Scheduler feeds both stages. Connect your existing Base/A-Z model routes here; the chosen scene route is reused at both resolutions. You can package those routes in a subgraph as usual.

## Gate, checkpoints and resuming
The usual Review Gate, approve/stop and Loop End remain in place.
New SelfLift checkpoints contain the standard final AV latent plus an optional native low-resolution video carry (about 25% extra video-latent storage at half width/height).
The accepted gate candidate's own carry is saved and restored. Selected visual-context windows crop the same time interval from both resolutions.
Detail-taper, color-corrected and spatial-proxy prefixes use their already transformed full-resolution context instead of overwriting it with an unmodified low carry.
Older checkpoints remain loadable. Missing low carry, changed spatial grids or a different upscaler fall back to the existing full-resolution context for the low stage. Switching SelfLift does not delete or automatically regenerate earlier scenes.
Pixel/latent upscale, de-rope and exports continue to use the normal final-resolution checkpoint payload; they do not need this sampler.

## Installation and scope
The H3 model, text encoder and VAEs are the same as Ref2V Studio. Install an LBH H3 3D latent-upscaler checkpoint separately. Selecting tridae downloads pinned, verified weights on first execution; bilinear needs no weights. These alternatives are comparison options, not guaranteed quality improvements.
The private runtime is adapted from facok/comfyui-SelfLift and Songssx/ComfyUI-MiniMaxH3-TimelineDirector. Neither pack needs to be installed, and neither ComfyUI core nor upstream custom nodes are modified. See selflift_runtime/NOTICE.md for source revision and licensing.
Pixel/VAE correction is off by default; enable it explicitly with rho > 0 and w_max > 0. Tr1dae requires lowres_scale=0.5 and final dimensions divisible by 64. Old saved hunts resume with default controls; changed lift controls create a distinct hunt. No weight loading or inference runs when opening the project tab.

## Automatic or reviewed sampling
Use this one workflow for both modes. With SelfLift enabled and review_enabled=false, a fresh batch processes only the input seed without tiny previews or a seed-review pause; existing approved batches retain their resume selections. With review_enabled=true, inspect low-pass candidates, optionally Preview upscale to look for localized color patches, mark one or more candidates for finishing, and choose one main. Keep the final Review Gate at one candidate: it is a separate review stage. Low/high handoffs remain saved for recovery in either mode. SelfLift Project OFF bypasses the low/high hunt and runs ordinary single-stage sampling.

Localized learned-lift color patches remain an unresolved quality limitation. Preview upscale can help reject affected seeds before spending the high-pass sampling time; tiny previews are not final-quality validation. See docs/selflift-seed-hunt.md for installation, Radau, tiling and resume details.
