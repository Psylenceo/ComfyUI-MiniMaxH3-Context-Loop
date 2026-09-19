"""Opt-in spatial bilinear and Tr1dae clean-latent lifts for SelfLift.

No UI-time downloads, global patches, VAE calls, sampling or temporal resampling.
Tr1dae uses the privately bundled MIT Mamad8 architecture and raw VAE-space
latents, not LBH's per-channel normalization or continuation chunking.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
import tempfile
import threading
import urllib.request

import torch
import torch.nn.functional as F

from ..selflift_upscalers import BILINEAR, TRIDAE, TRIDAE_CHECKPOINT, TRIDAE_SHA256, TRIDAE_URL

_download_lock = threading.Lock()
_patcher_cache = {}


def _verify_checkpoint(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != TRIDAE_SHA256:
        raise ValueError(
            f"Tr1dae checkpoint checksum mismatch: {path}. Expected the original "
            f"{TRIDAE_CHECKPOINT}; the existing file was not changed."
        )


def _download_checkpoint(destination):
    import comfy.model_management as mm
    with _download_lock:
        if destination.is_file():
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        logging.info("[SelfLift Tr1dae] downloading pinned checkpoint to %s", destination)
        # Unique, same-directory temporary file: incomplete downloads never
        # appear in the model list, and a user's installed file is not replaced.
        fd, partial = tempfile.mkstemp(prefix=".tridae-", suffix=".tmp", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as output:
                request = urllib.request.Request(TRIDAE_URL, headers={"User-Agent": "H3-Chain-SelfLift"})
                with urllib.request.urlopen(request, timeout=30) as response:
                    while chunk := response.read(1024 * 1024):
                        mm.throw_exception_if_processing_interrupted()
                        output.write(chunk)
            _verify_checkpoint(partial)
            mm.throw_exception_if_processing_interrupted()
            try:
                os.link(partial, destination)
            except FileExistsError:
                _verify_checkpoint(destination)
        finally:
            # This exact file was created by this download attempt only.
            Path(partial).unlink(missing_ok=True)
    return destination


def _checkpoint_path():
    import folder_paths
    paths = []
    for folder in ("latent_upscale_models", "h3_latent_upscalers"):
        try:
            paths.extend(Path(path) for path in folder_paths.get_folder_paths(folder))
        except KeyError:
            pass
    paths.extend(Path(folder_paths.models_dir) / folder
                 for folder in ("latent_upscale_models", "h3_latent_upscalers"))
    for folder in paths:
        installed = folder / TRIDAE_CHECKPOINT
        if installed.is_file():
            return installed
    return _download_checkpoint(paths[0] / TRIDAE_CHECKPOINT)


def _load_tridae(device):
    import comfy.model_patcher
    from safetensors.torch import load_file
    from . import h3_clean_upscaler as architecture

    path = _checkpoint_path()
    stat = path.stat()
    key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size, str(device))
    if key in _patcher_cache:
        return _patcher_cache[key]
    _verify_checkpoint(path)
    info = architecture.read_checkpoint_info(path)
    state = {name: tensor.clone() for name, tensor in load_file(str(path), device="cpu").items()}
    model = architecture.build_upscaler(state, info)
    del state
    model.to(device="cpu", dtype=torch.float32)
    # Vanilla Conv3d/MHA need real GPU parameters, not DynamicVRAM staging for
    # comfy.ops. Same FP32/full-load contract as the diagnostic/reference path.
    patcher = comfy.model_patcher.ModelPatcher(
        model, load_device=device, offload_device=torch.device("cpu"))
    _patcher_cache.clear()
    _patcher_cache[key] = patcher
    logging.info("[SelfLift Tr1dae] loaded %s; FP32; full temporal context", path.name)
    return patcher


def _retire_tridae(patcher):
    """Offload only our small legacy patcher, after successful inference."""
    import comfy.model_management as mm
    mm.throw_exception_if_processing_interrupted()
    selected = [entry for entry in list(mm.current_loaded_models)
                if getattr(entry.model, "model", None) is patcher.model]
    if not selected:
        return
    device = torch.device(patcher.load_device)
    if device.type == "cuda":
        torch.cuda.synchronize(device)
    mm.throw_exception_if_processing_interrupted()
    for entry in selected:
        if entry.model_unload():
            mm.current_loaded_models[:] = [loaded for loaded in mm.current_loaded_models if loaded is not entry]
    logging.info("[SelfLift Tr1dae] targeted lift-to-high offload; CPU weights remain reloadable")


def spatial_bilinear(z, out_hw):
    """FP32 independent 2D interpolation at each B,T; time is never mixed."""
    b, c, t, h, w = z.shape
    frames = z.float().permute(0, 2, 1, 3, 4).reshape(b * t, c, h, w)
    frames = F.interpolate(frames, size=tuple(out_hw), mode="bilinear", align_corners=False)
    return frames.reshape(b, t, c, *out_hw).permute(0, 2, 1, 3, 4).contiguous()


def _tridae_lift(z, out_hw, device):
    import comfy.model_management as mm
    if tuple(out_hw) != tuple(int(size) * 2 for size in z.shape[-2:]):
        raise ValueError("Tr1dae requires exactly 2x spatial upscaling; no automatic extra resize is applied.")
    patcher = _load_tridae(device)
    model = patcher.model
    allowance = z.numel() * 4 * 512
    mm.load_models_gpu([patcher], memory_required=patcher.model_size() + allowance, force_full_load=True)
    if any(p.device != torch.device(device) or p.dtype != torch.float32 for p in model.parameters()):
        raise RuntimeError("Tr1dae parameters were not fully loaded in FP32 on the requested device.")
    # Full-context temporal attention: artificial chunks/splits change the
    # model's result. Keep the native context masks/anchor restoration in the
    # existing high-stage sampler, not inside this raw clean lift.
    with torch.inference_mode(), torch.autocast(device_type=torch.device(device).type, enabled=False):
        result = model(z.to(device=device, dtype=torch.float32))
        expected = (*z.shape[:-2], *out_hw)
        if tuple(result.shape) != expected:
            raise RuntimeError(f"Tr1dae returned {tuple(result.shape)}, expected {expected}.")
        if not torch.isfinite(result).all():
            raise RuntimeError("Tr1dae returned non-finite latent values.")
        result = result.to(device=mm.intermediate_device(), dtype=torch.float32)
    return result, patcher


def latent_lift(z, out_hw, name, device=None, temporal_split=None, *, cleanup_after=False):
    import comfy.model_management as mm
    if z.ndim != 5 or z.shape[1] != 24 or not z.is_floating_point():
        raise ValueError("SelfLift alternatives require a floating-point Bx24xTxHxW H3 video latent.")
    if len(out_hw) != 2 or any(int(size) != size or size < 1 for size in out_hw):
        raise ValueError("SelfLift target latent height/width must be positive integers.")
    out_hw = tuple(int(size) for size in out_hw)
    if name == BILINEAR:
        # No model load/download and no temporal mixing, even at a prefix split.
        with torch.inference_mode():
            return spatial_bilinear(z, out_hw).to(mm.intermediate_device())
    if name != TRIDAE:
        raise ValueError(f"Unknown SelfLift alternative upscaler: {name}")
    device = mm.get_torch_device() if device is None else device
    result, patcher = _tridae_lift(z, out_hw, device)
    if cleanup_after:
        _retire_tridae(patcher)
    return result
