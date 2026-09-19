"""Optional SelfLift checkpoint payload; ordinary AV checkpoints stay unchanged."""
from __future__ import annotations

import hashlib
import json

import torch

from .selflift_settings import canonical_settings, lift_settings

LOW_CARRY = "selflift_low_resolution_carry"
SIGNATURE = "selflift_signature"
PREVIOUS_LOW = "selflift_previous_low_resolution_carry"
PREFIX_STEPS = "selflift_previous_prefix_steps"


def settings_signature(settings):
    # A change of lifter/grid must not reuse an unrelated native low-res chain.
    controls = lift_settings(settings)
    payload = {"version": 1, "scale": controls["lowres_scale"],
               "upscaler_model": str(settings.get("upscaler_model", ""))}
    payload.update({key: value for key, value in canonical_settings(controls).items()
                    if key != "lowres_scale"})
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def metadata(latent, clone=False):
    if not isinstance(latent, dict) or not torch.is_tensor(latent.get(LOW_CARRY)):
        return {}
    low = latent[LOW_CARRY]
    if clone:
        low = low.detach().cpu().contiguous().clone()
    return {LOW_CARRY: low, SIGNATURE: str(latent.get(SIGNATURE, ""))}


def checkpoint_payload(latent):
    extra = metadata(latent)
    if not extra:
        return {}
    return {LOW_CARRY: extra[LOW_CARRY], SIGNATURE: torch.tensor(
        list(extra[SIGNATURE].encode("utf-8")), dtype=torch.uint8)}


def checkpoint_latent(tensors):
    result = {"samples": [tensors["video"], tensors["audio"]]}
    if LOW_CARRY in tensors:
        signature = tensors.get(SIGNATURE)
        result.update({LOW_CARRY: tensors[LOW_CARRY], SIGNATURE:
                       bytes(signature.tolist()).decode("utf-8")
                       if signature is not None else ""})
    return result


def with_previous_context(latent, previous, frames):
    """Carry the SAME selected window as Chain Context, never another scene."""
    extra = metadata(previous)
    if not extra or int(frames) < 5:
        return latent
    steps = 2 + 5 * ((int(frames) - 5) // 17)
    low = extra[LOW_CARRY]
    if low.ndim != 5 or low.shape[2] < steps:
        return latent
    return {**latent, PREVIOUS_LOW: low[:, :, -steps:].detach().contiguous(),
            PREFIX_STEPS: steps, SIGNATURE: extra[SIGNATURE]}


def prepare_previous_context(latent, settings):
    """Old checkpoints/changed grids fall back to their saved HQ context."""
    result = dict(latent)
    low = result.get(PREVIOUS_LOW)
    samples = result["samples"]
    video = list(samples.unbind())[0] if hasattr(samples, "unbind") else samples[0]
    scale = lift_settings(settings)["lowres_scale"]
    h, w = (max(2, round(int(size) * scale / 2) * 2)
            for size in video.shape[-2:])
    compatible = (torch.is_tensor(low) and low.ndim == 5
                  and tuple(low.shape[:2]) == tuple(video.shape[:2])
                  and tuple(low.shape[-2:]) == (h, w)
                  and result.get(SIGNATURE) == settings_signature(settings))
    if not compatible:
        result.pop(PREVIOUS_LOW, None)
        result.pop(PREFIX_STEPS, None)
    result.pop(LOW_CARRY, None)
    result.pop(SIGNATURE, None)
    return result
