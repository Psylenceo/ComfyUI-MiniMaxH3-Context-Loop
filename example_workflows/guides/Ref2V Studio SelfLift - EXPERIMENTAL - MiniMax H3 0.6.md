# Ref2V Studio SelfLift - EXPERIMENTAL - MiniMax H3 0.6

Dedicated **experimental nightly** workflow. Existing workflow files are unchanged.

Setup controls come first, followed by numbered generation columns. Recovery is disabled by default. Enable it only to assemble saved clips without sampling.

EXPERIMENTAL SELFLIFT CHAIN — NIGHTLY ONLY

Open this separate workflow; your existing workflows do not need rewiring. It reuses the catalog's neutral courier example and assets. Use a NEW run name or a duplicate project for initial tests.

## Project switch
SelfLift Project is after Plan Studio and before Loop Start.
- OFF (the shipped default): ordinary single-stage Euler sampling at Plan resolution.
- ON: early steps at half spatial resolution, learned H3 latent lift, then the selected number of final steps at Plan resolution.
- Plan width/height are the FINAL dimensions. The low latent grid is rounded to H3's even spatial token grid.
- Pick a compatible H3 3D-convolution learned checkpoint in models/latent_upscale_models. The live smoke test used minimax_h3_latent_upscaler_3d_conv_v1_bf16.safetensors from https://huggingface.co/LBH-123-AI/Minimax_h3_latent_Upscaler. Generic image/LTX and H3 2D upscalers are not interchangeable with this 3D runtime.
- high_resolution_steps must be less than the scene's total steps, including scene overrides. Example: 20 total / 5 high = 15 low + 5 high; for an 8-step model use 2 high.
- Euler is required when ON. This first integration does not use TST or spatial tiling.
- This is an experimental H3 adaptation of SelfLift's progressive sampling, using the learned latent route (rho=0), not the paper's pixel/VAE correction route or a guarantee of equal quality.

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
The H3 model, text encoder and VAEs are the same as Ref2V Studio. Add the learned H3 latent-upscaler checkpoint separately; no automatic download is performed.
The private runtime is adapted from facok/comfyui-SelfLift and Songssx/ComfyUI-MiniMaxH3-TimelineDirector. Neither pack needs to be installed, and neither ComfyUI core nor upstream custom nodes are modified. See selflift_runtime/NOTICE.md for source revision and licensing.
The bundled VAE pixel-correction helper is not enabled by this workflow. No weight loading or inference runs when opening the project tab.
