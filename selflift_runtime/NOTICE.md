# Private SelfLift runtime

Adapted from Songssx/ComfyUI-MiniMaxH3-TimelineDirector, commit
`03915aae320d186f1498d12a847689af0902785d` (2026-09-17), `selflift_runtime/`.
https://github.com/Songssx/ComfyUI-MiniMaxH3-TimelineDirector

TimelineDirector's runtime is derived from facok/comfyui-SelfLift
https://github.com/facok/comfyui-SelfLift and references the learned H3
latent-upscaler architecture by LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler.

Distributed under this repository's GPL-3.0 license, with attribution to the
original authors. The model checkpoint is a separate asset, not bundled here.

Local changes: private/lazy loading, no public upstream node registrations,
no TST, opt-in high-stage spatial tiling, Chain-owned stage-aware Drift-Control integration,
native AV/painted-context carry, checkpoint persistence, avoiding a second
blend of native fractional masks, masked pixel-anchor edge-case repair, and
a dedicated project-switch workflow. The private Radau IA 2s adapter calls
the user's connected RES4LYF sampler without patching it. It captures the
completed low-resolution state, evaluates a fresh boundary prediction, and
uses a separately versioned durable handoff; Euler retains its original path.
Neither ComfyUI core nor installed upstream node packs are modified.

## High-resolution denoiser tiling

`h3_tiling.py` adapts the spatial partitioning, packed-layout coordinate mapping,
CPU blending and per-model memory-planning wrappers from facok/comfyui-SelfLift's
`h3_tiling.py` (inspected 2026-09-20):
https://github.com/facok/comfyui-SelfLift/blob/master/h3_tiling.py

Local changes: explicit opt-in settings with fixed tile count/direction,
native continuation/painted-mask cropping, mixed keyframe/reference handling,
zero-overlap guards, cancellation checks, and high-stage-only integration with
durable hunt finishing identities. TST is not included.

## Tr1dae clean-latent upscaler

`h3_clean_upscaler.py` is the unmodified architecture/metadata implementation
from mamad8c/ComfyUI-H3-Latent-Upscaler-Mamad8, commit
`e98237773011523528353a8beb4863e65b099a38` (apart from its attribution header).
Copyright (c) 2026 Mamad8, MIT license; the full notice is included in
`H3_CLEAN_UPSCALER_LICENSE.txt`.
https://github.com/mamad8c/ComfyUI-H3-Latent-Upscaler-Mamad8

The `tridae` option uses Tr1dae's separately distributed epoch200 weights:
https://huggingface.co/Tridae/H3LatentUpscaler
The checkpoint revision and SHA-256 are pinned in `selflift_upscalers.py`.
Weights are not bundled. The new private adapter uses FP32, legacy full-load
ModelPatcher, and full temporal context, as in the validated diagnostic.
https://github.com/Tr1dae/ComfyUI-MiniMaxH3_LatentUpscaler
