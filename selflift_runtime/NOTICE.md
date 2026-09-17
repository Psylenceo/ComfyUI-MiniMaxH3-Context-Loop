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
no TST or spatial tiling, Chain-owned stage-aware Drift-Control integration,
native AV/painted-context carry, checkpoint persistence, avoiding a second
blend of native fractional masks, masked pixel-anchor edge-case repair, and
a dedicated project-switch workflow. Neither ComfyUI
core nor installed upstream node packs are modified.
