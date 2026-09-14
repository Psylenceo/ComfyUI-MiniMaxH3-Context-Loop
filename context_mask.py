"""Small Plan-owned, static spatial release masks for AV context blocks."""

import math


CONTEXT_MASK_MODES = ("masked_av", "feathered_av", "audio_feathered_av")
MASK_LEVELS = 16


def normalize_context_mask(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Context weaken mask must be an object.")
    columns, rows = value.get("columns"), value.get("rows")
    if any(type(n) is not int or not 1 <= n <= 512 for n in (columns, rows)):
        raise ValueError("Context weaken mask needs 1–512 columns and rows.")
    cells = value.get("cells")
    if (not isinstance(cells, list) or len(cells) != columns * rows
            or any(type(n) is not int or not 0 <= n <= MASK_LEVELS for n in cells)):
        raise ValueError("Context weaken mask cells must match its grid (values 0–16).")
    strength = value.get("strength", 0.5)
    if (isinstance(strength, bool) or not isinstance(strength, (int, float))
            or not math.isfinite(strength) or not 0 <= strength <= 1):
        raise ValueError("Context weaken strength must be between 0 and 1.")
    if not any(cells):
        return None
    return {"columns": columns, "rows": rows, "cells": list(cells),
            "strength": float(strength)}


def release_context_regions(video_mask, blocks):
    """Modify only owned prefix-mask cells; never touch samples or audio.

    Grid values are deliberately quantized to 17 levels so soft brushes do
    not create thousands of unique H3 per-token timestep embeddings.
    """
    import torch
    import torch.nn.functional as F
    from .masking_ops import h3_video_latent_frame_groups

    boundaries = {0: 0}
    for step, (_, end) in enumerate(h3_video_latent_frame_groups(video_mask.shape[2]), 1):
        boundaries[end] = step
    consumed = 0
    for block in blocks:
        start = consumed
        consumed += int(block["frames"])
        mask = normalize_context_mask(block.get("weaken_mask"))
        if mask is None or mask["strength"] == 0:
            continue
        if start not in boundaries or consumed not in boundaries:
            raise ValueError("Context weaken mask block is not latent-aligned.")
        height, width = video_mask.shape[-2:]
        grid = torch.tensor(mask["cells"], device=video_mask.device,
                            dtype=video_mask.dtype).reshape(1, 1, mask["rows"], mask["columns"])
        grid = F.interpolate(grid, size=((height + 1) // 2, (width + 1) // 2), mode="nearest")
        grid = grid.repeat_interleave(2, -2).repeat_interleave(2, -1)[..., :height, :width]
        grid = grid[:, :, None] * (mask["strength"] / MASK_LEVELS)
        target = video_mask[:, :, boundaries[start]:boundaries[consumed]]
        # Explicitly release a region of the context lock. Do not lower any
        # existing temporal feather, or change the generated part of the clip.
        target.copy_(torch.maximum(target, grid))
