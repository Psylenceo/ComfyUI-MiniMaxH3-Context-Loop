"""Spatial high-stage H3 evaluation, adapted from facok/comfyui-SelfLift.

See NOTICE.md. Uses per-model wrappers, never a global forward patch. Native
video masks are cropped with each tile; audio masks and reference grids remain
whole. The outer sampler still owns AV masks, CFG and continuation anchors.
"""
from __future__ import annotations

from functools import partial
import inspect
import logging
import math

import torch

from ..selflift_tiling import tiling_settings

KEY = "h3_chain_selflift_highres_tiling"


def tile_regions(shape, settings):
    axis = {"height": 3, "width": 4}.get(settings["axis"])
    if axis is None:
        axis = 3 if shape[3] >= shape[4] else 4
    length = shape[axis]
    patches = (length + 1) // 2
    count = min(settings["tiles"], max(1, patches // 2))
    if count == 1:
        return axis, [(0, length)]
    margin = min(settings["overlap"] // 2, (patches // count) // 2)
    regions = [(max(0, i * patches // count - margin) * 2,
                min(length, (min(patches, (i + 1) * patches // count + margin)) * 2))
               for i in range(count)]
    return axis, regions


def validate_target(model, shapes, positive=(), negative=()):
    import comfy.model_base
    from comfy.patcher_extension import WrappersMP
    if not isinstance(model.model, comfy.model_base.MiniMaxH3):
        raise ValueError("SelfLift high-resolution tiling requires a MiniMax H3 model.")
    if len(shapes) != 2 or len(shapes[0]) != 5 or len(shapes[1]) != 4:
        raise ValueError("SelfLift tiling requires native H3 video and audio latent streams.")
    if shapes[0][0] != 1 or shapes[1][0] != 1:
        raise ValueError("SelfLift H3 tiling requires batch size 1.")
    if not all(hasattr(WrappersMP, name) for name in ("DIFFUSION_MODEL", "PREPARE_SAMPLING")):
        raise ValueError("This ComfyUI build lacks the model-scoped wrappers required for SelfLift tiling.")
    for conditioning in (positive, negative):
        for _, metadata in conditioning or ():
            if any(metadata.get(key) is not None for key in ("control", "area", "mask")):
                raise ValueError("SelfLift high-resolution tiling does not support ControlNet or regional conditioning.")


def _layout(signature, payload):
    from comfy.ldm.minimax.model import PackedLayout
    options = {key: payload.get(key) for key in ("keyframes", "refs")}
    if "frame_count" in inspect.signature(PackedLayout).parameters:
        options["frame_count"] = payload.get("frame_count")
    return PackedLayout(*signature, **options)


def tile_payload(payload, context, video, audio, axis, start, end):
    from comfy.ldm.common_dit import pad_to_patch_size
    height, width = video.shape[-2:]
    padded_h, padded_w = (height + 1) // 2 * 2, (width + 1) // 2 * 2
    signature = (context.shape[1], video.shape[2], padded_h, padded_w, audio.shape[-1])
    full = payload.get("layout")
    if full is None or full.signature != signature:
        full = _layout(signature, payload)
    tiled = dict(payload)
    if payload.get("keyframes"):
        keyframes = []
        for keyframe in payload["keyframes"]:
            item = dict(keyframe)
            latent = item.get("latent")
            if latent is not None:
                if latent.ndim != 5 or latent.shape[-2:] != (height, width):
                    raise ValueError("SelfLift tiled keyframes must match the full target video grid.")
                item["latent"] = pad_to_patch_size(latent.narrow(axis, start, end - start), (1, 2, 2)).contiguous()
            keyframes.append(item)
        tiled["keyframes"] = keyframes
        # Keyframes precede independent references in the packed conditioning.
        # References keep their own grids, even when both kinds are present.
        tiled["cond_video_latents"] = [k["latent"] for k in keyframes if k.get("latent") is not None]
        tiled["cond_video_latents"] += [r["latent"] for r in (payload.get("refs") or ()) if r.get("latent") is not None]
    tile_h, tile_w = (end - start, width) if axis == 3 else (height, end - start)
    layout = _layout((context.shape[1], video.shape[2], (tile_h + 1) // 2 * 2,
                      (tile_w + 1) // 2 * 2, audio.shape[-1]), tiled)
    if len(full.segments) != len(layout.segments):
        raise ValueError("SelfLift tiling cannot map this H3 packed conditioning layout.")
    for (a, b, kind), (c, d, tile_kind) in zip(full.segments, layout.segments):
        if kind != tile_kind:
            raise ValueError("SelfLift tiling encountered incompatible H3 conditioning segments.")
        positions = full.position_ids[a:b]
        if kind in ("cond", "video"):
            positions = positions.reshape(-1, padded_h // 2, padded_w // 2, 3)
            positions = positions.narrow(axis - 2, start // 2, (end - start + 1) // 2).reshape(-1, 3)
        layout.position_ids[c:d].copy_(positions)
    tiled["layout"] = layout
    return tiled


def tiled_forward(executor, streams, timestep, context, transformer_options, minimax_payload=None,
                  *, settings, **kwargs):
    video, audio = streams
    axis, regions = tile_regions(video.shape, settings)
    if len(regions) == 1:
        return executor(streams, timestep, context, transformer_options, minimax_payload=minimax_payload, **kwargs)
    if kwargs.get("control") is not None:
        raise ValueError("SelfLift high-resolution tiling does not support ControlNet.")
    result = torch.zeros(video.shape, dtype=torch.float32, device="cpu")
    weights = torch.zeros(video.shape[axis], dtype=torch.float32, device="cpu")
    audio_result = None
    shape = [1] * video.ndim
    for index, (start, end) in enumerate(regions):
        import comfy.model_management
        comfy.model_management.throw_exception_if_processing_interrupted()
        options = dict(kwargs)
        mask = options.get("denoise_mask")
        if mask is not None:
            if mask.ndim != 5 or mask.shape[axis] not in (1, video.shape[axis]):
                raise ValueError("SelfLift tiling received an incompatible native video mask.")
            options["denoise_mask"] = mask if mask.shape[axis] == 1 else mask.narrow(axis, start, end - start).contiguous()
        payload = tile_payload(minimax_payload or {}, context, video, audio, axis, start, end)
        tile = video.narrow(axis, start, end - start).contiguous()
        predicted, predicted_audio = executor([tile, audio], timestep, context,
            transformer_options.copy(), minimax_payload=payload, **options)
        if predicted.shape != tile.shape or predicted_audio.shape != audio.shape:
            raise ValueError("SelfLift tiled model returned incompatible video/audio shapes.")
        window = torch.ones(end - start, dtype=torch.float32, device="cpu")
        if index:
            overlap = max(0, min(end, regions[index - 1][1]) - start)
            if overlap:
                window[:overlap] *= (torch.arange(overlap) + .5) / overlap
        if index + 1 < len(regions):
            overlap = max(0, end - regions[index + 1][0])
            if overlap:
                window[-overlap:] *= 1 - (torch.arange(overlap) + .5) / overlap
        shape[axis] = end - start
        result.narrow(axis, start, end - start).addcmul_(predicted.float().cpu(), window.view(shape))
        weights[start:end].add_(window)
        if audio_result is None:
            audio_result = predicted_audio.detach().clone()
        del tile, payload, predicted, predicted_audio
    shape[axis] = video.shape[axis]
    result.div_(weights.view(shape))
    return [result.to(video), audio_result.to(audio)]


def prepare_sampling(executor, model, noise_shape, conds, model_options=None,
                     force_full_load=False, force_offload=False, *, shapes, settings):
    video, audio = shapes
    full_elements = math.prod(video[1:]) + math.prod(audio[1:])
    if tuple(noise_shape) != (video[0], 1, full_elements):
        raise ValueError("SelfLift tiling memory plan does not match the packed AV latent.")
    axis, regions = tile_regions(video, settings)
    budget_shape = noise_shape
    if len(regions) > 1 and not force_offload:
        tile = list(video)
        tile[axis] = max(end - start for start, end in regions)
        tile[3], tile[4] = (tile[3] + 1) // 2 * 2, (tile[4] + 1) // 2 * 2
        # Comfy's ordinary estimator still includes conditioning/additional
        # models. Reserve full-size sampler/CFG buffers in addition to tile work.
        per_element = float(model.model.memory_required((1, 1, 1)))
        if not math.isfinite(per_element) or per_element <= 0:
            raise ValueError("SelfLift tiling needs a valid H3 memory estimator.")
        reserve = math.ceil(full_elements * 4 * 8 / per_element)
        budget_shape = (noise_shape[0], 1, math.prod(tile[1:]) + math.prod(audio[1:]) + reserve)
    return executor(model, budget_shape, conds, model_options=model_options,
                    force_full_load=force_full_load, force_offload=force_offload)


def tiled_model(model, shapes, settings):
    from comfy.patcher_extension import WrappersMP
    settings = tiling_settings(settings)
    if settings is None:
        return model
    validate_target(model, shapes)
    axis, regions = tile_regions(shapes[0], settings)
    patched = model.clone()
    for kind in (WrappersMP.DIFFUSION_MODEL, WrappersMP.PREPARE_SAMPLING):
        patched.remove_wrappers_with_key(kind, KEY)
    patched.add_wrapper_with_key(WrappersMP.DIFFUSION_MODEL, KEY, partial(tiled_forward, settings=settings))
    patched.add_wrapper_with_key(WrappersMP.PREPARE_SAMPLING, KEY,
        partial(prepare_sampling, shapes=tuple(tuple(s) for s in shapes), settings=settings))
    logging.info("[SelfLift tiling] high denoiser only: %d tiles along %s; overlap margin %d latent pixels; "
                 "native masks retained, first tile supplies audio; no cross-tile attention",
                 len(regions), "height" if axis == 3 else "width", settings["overlap"])
    return patched
