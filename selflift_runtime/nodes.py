"""Private SelfLift progressive-resolution engine (see NOTICE.md).

Run the first sampling steps on a spatially downscaled latent, lift the clean
endpoint to the target resolution with the training-free Artifact-Aware
Consistency Lift (arXiv:2609.02036), re-noise it at the transition sigma, and
finish the schedule at full resolution.

The Chain sampler uses nested H3 AV latents: audio has no spatial dimensions
and continues through the reused Euler boundary step or completed Radau step. This is experimental,
beyond the paper's image-model evaluation. No upstream nodes are registered.
"""

import logging
import os
import time

import torch

import comfy.k_diffusion.sampling
import comfy.model_management
import comfy.model_patcher
import comfy.model_sampling
import comfy.nested_tensor
import comfy.sample
import comfy.samplers
import comfy.utils
import latent_preview

from . import selflift
from .diagnostics import log_memory


_LOW_CARRY_KEY = "selflift_low_resolution_carry"
_PREVIOUS_LOW_CARRY_KEY = "selflift_previous_low_resolution_carry"


class _StageTimer:
    def __init__(self, stage, device, resolution=None, pixel_scale=1):
        self.stage = stage
        self.resolution = resolution
        self.pixel_scale = pixel_scale
        self.device = torch.device(device)
        self.synchronize = os.environ.get("SELFLIFT_TIMING_SYNC", "0") == "1" and self.device.type == "cuda"
        self.started = self.previous = self._now()
        timing_mode = "CUDA-synchronized wall time" if self.synchronize else "wall time; no forced CUDA sync"
        suffix = ""
        if resolution is not None:
            pixels = tuple(int(value * self.pixel_scale) for value in resolution)
            suffix = f" latent={resolution} pixels={pixels}"
        logging.debug("[H3 Chain SelfLift two-stage timing] %s start (%s)%s", stage, timing_mode, suffix)

    def _now(self):
        if self.synchronize:
            torch.cuda.synchronize(self.device)
        return time.perf_counter()

    def mark(self, label):
        current = self._now()
        logging.debug("[H3 Chain SelfLift two-stage timing] %s %s: %.3fs", self.stage, label, current - self.previous)
        self.previous = current

    def finish(self):
        current = self._now()
        logging.debug("[H3 Chain SelfLift two-stage timing] %s total: %.3fs; after last mark: %.3fs",
                     self.stage, current - self.started, current - self.previous)


def _streams(samples):
    if samples.is_nested:
        return list(samples.unbind()), True
    return [samples], False


def _pack(streams, nested):
    if nested:
        return comfy.nested_tensor.NestedTensor(streams)
    return streams[0]


def _splice_previous_low_tail(low_video, previous_low_carry, prefix_steps):
    """Replace the low-resolution opening with the preceding native low-res tail."""
    if isinstance(previous_low_carry, dict):
        previous_low_carry = previous_low_carry.get("samples")
    if not torch.is_tensor(previous_low_carry) or previous_low_carry.ndim != 5:
        raise ValueError("SelfLift: previous low-resolution carry must be a 5D video latent")
    expected = (low_video.shape[0], low_video.shape[1], low_video.shape[3], low_video.shape[4])
    received = (
        previous_low_carry.shape[0], previous_low_carry.shape[1],
        previous_low_carry.shape[3], previous_low_carry.shape[4],
    )
    if received != expected:
        raise ValueError(
            "SelfLift: previous low-resolution carry does not match the current low grid; "
            f"got {tuple(previous_low_carry.shape)}, expected [B,C,T,{low_video.shape[3]},{low_video.shape[4]}]"
        )
    prefix_steps = int(prefix_steps)
    if prefix_steps < 1 or prefix_steps > min(low_video.shape[2], previous_low_carry.shape[2]):
        raise ValueError(
            "SelfLift: previous low-resolution carry cannot provide the requested continuation prefix"
        )
    output = low_video.clone()
    output[:, :, :prefix_steps] = previous_low_carry[:, :, -prefix_steps:].to(
        device=output.device, dtype=output.dtype
    )
    return output


def _validate_sampling(model_sampling, sampler):
    if not isinstance(model_sampling, comfy.model_sampling.CONST):
        raise ValueError("SelfLift requires a rectified-flow model")
    if isinstance(sampler, comfy.samplers.KSAMPLER):
        if sampler.sampler_function is comfy.k_diffusion.sampling.sample_euler:
            if sampler.extra_options.get("s_churn", 0.0) != 0.0:
                raise ValueError("SelfLift requires Euler with s_churn=0")
            return None  # Keep the original Euler handoff and saved-hunt identity.
        from .radau import is_radau, contract
        if is_radau(sampler):
            return contract(sampler)
    raise ValueError("SelfLift requires the standard Euler sampler or experimental "
                     "RES4LYF ClownSampler Radau IA 2s (eta=0).")


def _effective_patch_size(model, config):
    """Compare the loaded architecture, not optional checkpoint JSON fields."""
    diffusion = getattr(model.model, "diffusion_model", None)
    value = getattr(diffusion, "patch_size", None)
    if value is None:
        # Native H3 detection omits this key without transformer metadata;
        # MiniMaxH3Model then uses its (1, 2, 2) constructor default. Keep
        # explicit invalid values (including None) distinct from an omission.
        default = (1, 2, 2) if config.get("image_model") == "minimax_h3" else None
        value = config.get("patch_size", default)
    return tuple(value) if isinstance(value, (list, tuple)) else value


def _validate_hires_model(model, model_hires, sampler):
    """Reject incompatible coordinate/conditioning systems before sampling.

    No weights are read or loaded. Quantization and LoRA patches may differ;
    changing latent/audio scaling or model families at this boundary may not.
    Comfy creates the sampling class dynamically, so compare its bases rather
    than requiring identical class objects.
    """
    if model_hires is None or model_hires is model:
        return
    low_sampling = model.get_model_object("model_sampling")
    high_sampling = model_hires.get_model_object("model_sampling")
    _validate_sampling(high_sampling, sampler)
    if (type(model.model) is not type(model_hires.model)
            or type(low_sampling).__mro__[1:] != type(high_sampling).__mro__[1:]):
        raise ValueError("SelfLift model_hires must use the same H3 model family and flow sampling as model.")
    for name, default in (("noise_scale", 1.0), ("audio_scale", 1.0),
                          ("shift", None), ("audio_shift", None), ("multiplier", None)):
        if getattr(low_sampling, name, default) != getattr(high_sampling, name, default):
            raise ValueError(f"SelfLift model_hires has incompatible {name}; match the base model's sampling settings.")
    low_format = model.get_model_object("latent_format")
    high_format = model_hires.get_model_object("latent_format")
    if type(low_format) is not type(high_format):
        raise ValueError("SelfLift model_hires must use the same H3 latent format and VAE as model.")
    for name in ("latent_channels", "latent_dimensions", "spacial_downscale_ratio",
                 "temporal_downscale_ratio", "scale_factor", "shift_factor"):
        if getattr(low_format, name, None) != getattr(high_format, name, None):
            raise ValueError(f"SelfLift model_hires has incompatible latent {name}.")
    configs = [getattr(getattr(m.model, "model_config", None), "unet_config", {})
               for m in (model, model_hires)]
    for name in ("image_model", "latents_dim", "audio_latents_dim", "text_dim"):
        if configs[0].get(name) != configs[1].get(name):
            raise ValueError(f"SelfLift model_hires has incompatible {name}; both checkpoints must accept the same AV latents and conditioning.")
    low_patch = _effective_patch_size(model, configs[0])
    high_patch = _effective_patch_size(model_hires, configs[1])
    if low_patch != high_patch:
        raise ValueError(
            f"SelfLift model_hires has incompatible patch_size (model={low_patch!r}, "
            f"model_hires={high_patch!r}); both checkpoints must accept the same AV latents and conditioning.")


def _stage_latent_transform(model, samples, direction):
    """Apply H3's packed-AV scaling even on a freshly loaded finishing model.

    Comfy normally sets latent_shapes inside sampling, but our resume-noise
    reconstruction happens before that. Restore the prior value on every exit.
    """
    inner = model.model
    transform = getattr(inner, "process_latent_" + direction)
    if not hasattr(inner, "latent_shapes"):
        return transform(samples)
    previous = {"latent_shapes": inner.latent_shapes}
    try:
        # ModelPatcher applies object patches while loading. This checkpoint
        # need not have been loaded yet (and a shared model may carry another
        # patcher's sampling settings), so use this patcher's own objects.
        for name in ("model_sampling", "latent_format"):
            if hasattr(inner, name):
                previous[name] = getattr(inner, name)
                setattr(inner, name, model.get_model_object(name))
        inner.latent_shapes = [s.shape for s in _streams(samples)[0]]
        return transform(samples)
    finally:
        for name, value in previous.items():
            setattr(inner, name, value)


def _validate_schedule(sigmas, transition_step):
    if sigmas.ndim != 1 or not sigmas.is_floating_point():
        raise ValueError("SelfLift: sigmas must be a one-dimensional floating-point tensor")
    if not torch.isfinite(sigmas).all() or (sigmas < 0).any():
        raise ValueError("SelfLift: sigmas must be finite and nonnegative")
    if sigmas.numel() < 2:
        return
    if not isinstance(transition_step, int) or not 1 <= transition_step <= sigmas.numel() - 2:
        raise ValueError(f"SelfLift: transition_step {transition_step} out of range for {sigmas.numel() - 1} steps")
    if (sigmas[1:] > sigmas[:-1]).any():
        raise ValueError("SelfLift: sigmas must be non-increasing")
    if (sigmas[:-1] <= 0).any():
        raise ValueError("SelfLift: only the final sigma may be zero")
    if sigmas[transition_step] >= 1:
        raise ValueError("SelfLift: the high-resolution starting sigma must be less than 1")


def _validate_latent_input(latent_image):
    """Validate the latent template; return normalized masks for every AV stream.

    Accepted mask shapes: [B, H, W], [B, 1, H, W], [B, 1, T, H, W] (1 = generate,
    0 = keep the original content). Spatial dims must match the latent; a time
    length of 1 shares the mask over all frames. Normalized to [B, 1, H, W] for
    image latents and [B, 1, T|1, H, W] for video latents.
    """
    streams, _ = _streams(latent_image["samples"])
    if not streams or streams[0].ndim not in (4, 5):
        raise ValueError("SelfLift: expected a 4D image or 5D video latent size template")
    for stream in streams:
        if stream.ndim == 0 or any(size == 0 for size in stream.shape) or stream.shape[0] != streams[0].shape[0]:
            raise ValueError("SelfLift: latent streams must be nonempty and have the same batch size")
    raw_mask = latent_image.get("noise_mask")
    if raw_mask is None:
        return None
    video = streams[0].ndim == 5
    b, H, W = streams[0].shape[0], streams[0].shape[-2], streams[0].shape[-1]
    if getattr(raw_mask, "is_nested", False):
        masks = list(raw_mask.unbind())
        if len(masks) != len(streams):
            raise ValueError("SelfLift: nested noise_mask must match the H3 AV streams")
        mask = masks[0]
    else:
        mask = raw_mask
        masks = [mask] + [None] * (len(streams) - 1)
    if mask.ndim == 3:  # [B, H, W]
        mask = mask[:, None]
    if video and mask.ndim == 4:  # [B, 1, H, W] shared over time
        mask = mask[:, :, None]
    if mask.ndim != (5 if video else 4) or mask.shape[1] != 1:
        raise ValueError("SelfLift: noise_mask must have shape [B, H, W], [B, 1, H, W], or [B, 1, T, H, W]")
    if mask.shape[0] != b:
        raise ValueError(f"SelfLift: noise_mask batch {mask.shape[0]} does not match the latent batch {b}")
    if tuple(mask.shape[-2:]) != (H, W):
        # masks are accepted at any resolution and resized to the latent grid,
        # matching ComfyUI's Set Latent Noise Mask convention
        lead = mask.shape[:-2]
        mask = torch.nn.functional.interpolate(
            mask.reshape(-1, 1, *mask.shape[-2:]).float(), size=(H, W), mode="bilinear", align_corners=False
        ).reshape(*lead, H, W)
    if video and mask.shape[2] not in (1, streams[0].shape[2]):
        raise ValueError(f"SelfLift: noise_mask time length {mask.shape[2]} does not match the latent frames {streams[0].shape[2]}")
    if not torch.isfinite(mask).all():
        raise ValueError("SelfLift: noise_mask must be finite")
    video_mask = mask.float().clamp(0.0, 1.0)
    auxiliary_masks = []
    for stream, auxiliary in zip(streams[1:], masks[1:]):
        if auxiliary is None:
            auxiliary = torch.ones(
                (stream.shape[0], 1, *stream.shape[2:]),
                dtype=torch.float32, device=stream.device,
            )
        if not torch.is_tensor(auxiliary) or auxiliary.ndim != stream.ndim:
            raise ValueError("SelfLift: each nested auxiliary noise mask must match its latent stream")
        if auxiliary.shape[0] != stream.shape[0] or auxiliary.shape[1] not in (1, stream.shape[1]):
            raise ValueError("SelfLift: auxiliary noise-mask batch/channel shape is invalid")
        if any(mask_size not in (1, stream_size) for mask_size, stream_size in zip(
            auxiliary.shape[2:], stream.shape[2:]
        )):
            raise ValueError("SelfLift: auxiliary noise-mask dimensions must be broadcastable to the latent stream")
        if not torch.isfinite(auxiliary).all():
            raise ValueError("SelfLift: auxiliary noise_mask must be finite")
        auxiliary_masks.append(auxiliary.float().clamp(0.0, 1.0))
    return video_mask, auxiliary_masks


def _mask_blend_fn(anchor, mask):
    """post-CFG hook pinning x0 to the clean anchor outside the generate region.

    Exact under Euler: with the unmasked x0 pinned to the original latent each
    step, the unmasked state tracks original + the step's noise exactly.
    """
    def fn(args):
        d = args["denoised"]
        m = mask.to(device=d.device, dtype=d.dtype)
        return d * m + anchor.to(device=d.device, dtype=d.dtype) * (1.0 - m)
    return fn


def _dynamic_mask_blend_fn(anchor, state, generated_tail_elements=0):
    def fn(args):
        denoised = args["denoised"]
        mask = state.current_packed_mask
        if mask is None:
            raise RuntimeError("SelfLift Drift-Control did not prepare its dynamic mask")
        mask = mask.to(denoised)
        if generated_tail_elements:
            # Locked H3 audio is handled by the sampler's native AV inpaint
            # path.  Do not let this video continuity hook replace the native
            # audio prediction with a clean x0 anchor a second time.
            mask = torch.cat((
                mask[..., :-generated_tail_elements],
                torch.ones_like(mask[..., -generated_tail_elements:]),
            ), dim=-1)
        return denoised * mask + anchor.to(denoised) * (1.0 - mask)
    return fn


_DRIFT_CONTROL_KEY = "h3_chain_selflift_drift_control"

_CONTINUATION_DC_CLAMP = 0.05


def _match_continuation_latent_dc(lifted, previous, prefix_steps,
                                  clamp=_CONTINUATION_DC_CLAMP):
    """Remove a per-channel SelfLift seam offset without mixing frame content.

    The disposable continuation prefix gives us two representations of the same
    scene: the preceding segment's true full-resolution latent and SelfLift's
    lifted prediction. Their robust per-channel median delta is the brightness/
    colour bias introduced by the lift. Correct only the disposable prefix.
    Extending this correction into retained generated tokens creates a visible
    dark-to-bright rebound after the timeline finalizer trims the overlap.
    """
    if lifted.ndim != 5 or previous.ndim != 5:
        return lifted, None, 0
    if lifted.shape[:2] != previous.shape[:2] or lifted.shape[-2:] != previous.shape[-2:]:
        return lifted, None, 0

    compare_steps = min(
        max(0, int(prefix_steps)), int(lifted.shape[2]), int(previous.shape[2])
    )
    if compare_steps == 0:
        return lifted, None, 0

    # Work channel-by-channel so the robust median does not materialize one
    # full float32 copy of a large video latent in scarce VRAM.
    channel_delta = []
    for channel in range(int(lifted.shape[1])):
        difference = (
            lifted[:, channel, :compare_steps].float()
            - previous[:, channel, :compare_steps].to(lifted.device).float()
        )
        channel_delta.append(difference.reshape(-1).median())
        del difference
    dc = torch.stack(channel_delta).clamp(-float(clamp), float(clamp))
    dc = dc.to(device=lifted.device, dtype=lifted.dtype).view(1, -1, 1, 1, 1)

    corrected = lifted.clone()
    corrected[:, :, :compare_steps].sub_(dc)
    return corrected, dc, 0


def _euler_step(state, denoised, sigma, sigma_next):
    step = ((sigma_next - sigma) / sigma).to(device=state.device, dtype=state.dtype)
    return state + (state - denoised.to(state)) * step


def _resize_keyframes(cond, h, w):
    """Keyframe cond latents share the generation grid; resize them to the low-res one."""
    out = []
    for tensor, d in cond:
        kfs = d.get("minimax_keyframes")
        if kfs is None:
            out.append((tensor, d))
            continue
        d = d.copy()
        resized = []
        for kf in kfs:
            kf = dict(kf)
            lat = kf.get("latent")
            if lat is not None and (lat.shape[-2] != h or lat.shape[-1] != w):
                if lat.ndim == 5:
                    # spatial-only resize per frame; never interpolates across time
                    batch, channels, frames = lat.shape[:3]
                    resized_latent = torch.nn.functional.interpolate(
                        lat.float().permute(0, 2, 1, 3, 4).reshape(batch * frames, channels, lat.shape[-2], lat.shape[-1]),
                        size=(h, w), mode="bilinear", align_corners=False
                    )
                    resized_latent = resized_latent.reshape(batch, frames, channels, h, w).permute(0, 2, 1, 3, 4)
                else:
                    resized_latent = torch.nn.functional.interpolate(
                        lat.float(), size=(h, w), mode="bilinear", align_corners=False
                    )
                # per-channel, per-frame mean match: fixes bilinear's color drift without
                # restoring variance the low-res grid cannot represent
                source_mean = lat.float().mean(dim=(-2, -1), keepdim=True)
                resized_mean = resized_latent.mean(dim=(-2, -1), keepdim=True)
                kf["latent"] = (resized_latent + (source_mean - resized_mean)).to(lat)
            resized.append(kf)
        d["minimax_keyframes"] = resized
        out.append((tensor, d))
    return out


def _debug_dump(vae, latents):
    """Decode transition intermediates to PNGs when SELFLIFT_DEBUG=1."""
    if os.environ.get("SELFLIFT_DEBUG", "0") != "1":
        return
    out_dir = os.path.join(os.path.dirname(__file__), "debug")
    os.makedirs(out_dir, exist_ok=True)
    logging.warning("SelfLift: debug decoding enabled; intermediate video decodes can substantially increase time and memory")
    from PIL import Image
    for name, lat in latents.items():
        if lat is None:
            continue
        img = vae.decode(lat)
        if img.ndim == 5:
            img = img.reshape(-1, img.shape[-3], img.shape[-2], img.shape[-1])
        frame = (img[0].float().cpu().numpy().clip(0.0, 1.0) * 255).round().astype("uint8")
        Image.fromarray(frame).save(os.path.join(out_dir, name + ".png"))


def progressive_sample(model, positive, negative, vae, latent_image, sampler, sigmas, seed, cfg,
                       transition_step, lowres_scale, rho, w_min, w_max, latent_upsample, latent_lifter=None,
                       highres_tiling=False, model_hires=None, *, stop_after_low=False, handoff=None,
                       cleanup_between_stages=False, stop_after_lift=False):
    if stop_after_lift and (stop_after_low or handoff is None):
        raise ValueError("SelfLift lift preview requires a saved low-pass handoff and no stop_after_low.")
    _validate_schedule(sigmas, transition_step)
    if sigmas.numel() < 2:
        return latent_image
    if not 0.25 <= lowres_scale <= 1.0:
        raise ValueError("SelfLift: lowres_scale must be between 0.25 and 1")
    if not 0.0 <= rho <= 1.0:
        raise ValueError("SelfLift: rho must be between 0 and 1")
    if not 0.0 <= w_min <= w_max <= 1.0:
        raise ValueError("SelfLift: weights must satisfy 0 <= w_min <= w_max <= 1")
    noise_masks = _validate_latent_input(latent_image)
    from ..selflift_tiling import tiling_settings
    tiling = tiling_settings(highres_tiling)

    model_sampling = model.get_model_object("model_sampling")
    sampler_contract = _validate_sampling(model_sampling, sampler)
    _validate_hires_model(model, model_hires, sampler)
    radau_mode = sampler_contract is not None
    if radau_mode:
        from . import radau

    streams, nested = _streams(comfy.sample.fix_empty_latent_channels(
        model, latent_image["samples"], latent_image.get("downscale_ratio_spacial", None),
        latent_image.get("downscale_ratio_temporal", None)))
    hires_base = model_hires if model_hires is not None else model
    high_model = hires_base
    target_shapes = [tuple(s.shape) for s in streams]
    if tiling and not (stop_after_low or stop_after_lift):
        from .h3_tiling import validate_target
        validate_target(high_model, target_shapes, positive, negative)
    video = streams[0].ndim == 5
    if video:
        b, c, t, H, W = streams[0].shape
    else:
        b, c, H, W = streams[0].shape
        t = None
    h = max(2, round(H * lowres_scale / 2) * 2)
    w = max(2, round(W * lowres_scale / 2) * 2)
    low_shape = (b, c, t, h, w) if video else (b, c, h, w)
    logging.debug("[H3 Chain SelfLift two-stage] low_latent=%s target_latent=%s spatial_lift=(%.4f, %.4f) "
                 "low_steps=%d high_steps=%d sigma_prediction=%.8g sigma_resume=%.8g "
                 "rho=%.4f weights=(%.4f, %.4f) direct_lift=%s pixel_anchor=%s cfg=%.4f mask=%s hires_model=%s",
                 low_shape, tuple(streams[0].shape), H / h, W / w,
                 transition_step, sigmas.numel() - 1 - transition_step,
                 sigmas[transition_step if radau_mode else transition_step - 1].item(), sigmas[transition_step].item(),
                 rho, w_min, w_max,
                 "skipped" if rho == 1.0 and w_min == 1.0 else "external" if latent_lifter is not None else latent_upsample,
                 rho > 0.0 and w_max > 0.0, cfg,
                 "none" if noise_masks is None else f"{tuple(noise_masks[0].shape)}",
                 "custom" if model_hires is not None else "same")

    device = comfy.model_management.intermediate_device()
    source_video = streams[0].to(device)
    audio_streams = [s.to(device) for s in streams[1:]]

    def masked_model(base, video_anchor, m, auxiliary_masks, native_audio_mask=False):
        # pin the unmasked region to the clean anchor via a post-CFG x0 blend;
        # ComfyUI's denoise_mask path is unusable here: the high-res resume
        # latent deliberately carries noise, which would contaminate its anchor
        if m is None:
            return base
        patched = base.clone()
        if nested:
            flat_mask = m.reshape(b, 1, -1).repeat(1, 1, c).to(video_anchor.device)
            flat_anchor = video_anchor.reshape(b, 1, -1)
            for s, auxiliary_mask in zip(audio_streams, auxiliary_masks):
                expanded_mask = auxiliary_mask.to(device=s.device).expand(
                    s.shape[0], s.shape[1], *s.shape[2:]
                ).reshape(b, 1, -1)
                flat_mask = torch.cat([flat_mask, expanded_mask], dim=-1)
                flat_anchor = torch.cat([flat_anchor, s.reshape(b, 1, -1)], dim=-1)
            drift_state = patched.model_options.get(_DRIFT_CONTROL_KEY)
            generated_tail_elements = (
                sum(int(s[0].numel()) for s in audio_streams)
                if native_audio_mask else 0
            )
            if native_audio_mask and callable(getattr(drift_state, "configure_selflift_stage", None)):
                # The native sampler mask below contains an all-generate video
                # stream and the real locked-audio mask.  Keep Drift-Control's
                # dynamic video mask in its model wrapper, but do not allow the
                # sampler to apply that dynamic callback to the audio mask.
                patched.model_options.pop("denoise_mask_function", None)
            hook = (
                _dynamic_mask_blend_fn(
                    flat_anchor, drift_state, generated_tail_elements
                )
                if callable(getattr(drift_state, "configure_selflift_stage", None))
                else _mask_blend_fn(flat_anchor, flat_mask)
            )
        else:
            hook = _mask_blend_fn(video_anchor, m)
        patched.model_options = comfy.model_patcher.set_model_options_post_cfg_function(patched.model_options, hook)
        return patched

    if video:
        low_video = torch.nn.functional.interpolate(
            source_video.float(), size=(t, h, w), mode="trilinear", align_corners=False
        ).to(dtype=source_video.dtype)
    else:
        low_video = torch.nn.functional.interpolate(
            source_video.float(), size=(h, w), mode="bilinear", align_corners=False
        ).to(dtype=source_video.dtype)
    m_full = m_low = None
    auxiliary_masks = []
    if noise_masks is not None:
        noise_mask, auxiliary_masks = noise_masks
        if video and noise_mask.shape[2] == 1 and t > 1:
            m_full = noise_mask.expand(b, 1, t, H, W)
        else:
            m_full = noise_mask
        if video:
            m_low = torch.nn.functional.interpolate(
                m_full.reshape(b * t, 1, H, W).float(), size=(h, w), mode="bilinear", align_corners=False
            ).reshape(b, 1, t, h, w)
        else:
            m_low = torch.nn.functional.interpolate(
                m_full.float(), size=(h, w), mode="bilinear", align_corners=False)
        m_low = m_low.clamp(0.0, 1.0)
    video_anchor = source_video
    drift_state = model.model_options.get(_DRIFT_CONTROL_KEY)
    drift_continuation = callable(
        getattr(drift_state, "configure_selflift_stage", None)
    )
    previous_low_carry = latent_image.get(_PREVIOUS_LOW_CARRY_KEY)
    if previous_low_carry is not None:
        prefix_steps = int(latent_image.get(
            "selflift_previous_prefix_steps", getattr(drift_state, "prefix_steps", 0)))
        low_video = _splice_previous_low_tail(
            low_video, previous_low_carry, prefix_steps
        )
        logging.debug(
            "[SelfLift low-res continuation] copied %d native low-resolution tail token(s) into %s",
            prefix_steps, tuple(low_video.shape),
        )
    low_anchor = low_video
    low_latent = _pack([low_video] + audio_streams, nested)
    native_av_mask = nested and bool(auxiliary_masks)
    if native_av_mask:
        # The sampler INPUT injection and H3's token timestep labels must use
        # the same masks. A post-CFG x0 blend alone leaves a noisy prefix at the
        # model input even when its video labels say "clean reference".
        # The native AV mask keeps audio on ComfyUI/H3's input-side inpaint
        # path. Timeline video continuation remains dynamic: Drift-Control
        # updates only the video prefix as sigma changes, while the audio mask
        # is carried through unchanged (locked audio therefore stays at 0).
        low_noise_mask = _pack([m_low] + auxiliary_masks, nested)
        high_noise_mask = _pack([m_full] + auxiliary_masks, nested)
        high_anchor_latent = None
        low_model = model.clone()
        high_model = high_model.clone()
        logging.debug(
            "[SelfLift continuity v3] native AV input/token masks; "
            "dynamic Drift-Control video continuation; independent audio anchor"
        )
    else:
        low_noise_mask = high_noise_mask = high_anchor_latent = None
        low_model = masked_model(model, low_anchor, m_low, auxiliary_masks)
        high_model = masked_model(high_model, video_anchor, m_full, auxiliary_masks)
    del source_video, low_video
    del streams
    noise_low = (comfy.sample.prepare_noise(low_latent, seed, latent_image.get("batch_index", None))
                 if handoff is None else None)

    total_steps = sigmas.shape[-1] - 1
    callback = latent_preview.prepare_callback(model, total_steps)
    disable_pbar = not comfy.utils.PROGRESS_BAR_ENABLED
    if video:  # keyframe cond latents share the generation grid on MiniMax H3
        positive_low = _resize_keyframes(positive, h, w)
        negative_low = _resize_keyframes(negative, h, w)
    else:
        positive_low, negative_low = positive, negative

    transition = {}
    low_evaluations = 0

    def callback_low(step, x0, x, total):
        nonlocal low_evaluations
        step = low_evaluations
        low_evaluations += 1
        if low_evaluations > transition_step:
            raise RuntimeError("SelfLift: too many low-resolution progress callbacks for the stage schedule")
        if not radau_mode and step == transition_step - 1:
            transition["state"] = x
            transition["x0"] = x0
        # Preview decoders expect the target grid.  Decode a temporary lifted
        # preview while keeping the sampler state at its true low resolution.
        preview_x0 = x0
        if video:
            preview_streams, preview_nested = _streams(x0)
            preview_video = torch.nn.functional.interpolate(
                preview_streams[0].float(), size=(t, H, W), mode="trilinear", align_corners=False
            ).to(preview_streams[0].dtype)
            preview_x0 = _pack([preview_video] + preview_streams[1:], preview_nested)
            del preview_video, preview_streams
        elif x0.ndim == 4:
            preview_x0 = torch.nn.functional.interpolate(
                x0.float(), size=(H, W), mode="bilinear", align_corners=False
            ).to(x0.dtype)
        result = callback(step, preview_x0, preview_x0, total_steps)
        del preview_x0
        low_timer.mark(f"step {step + 1}/{transition_step}" + (" (includes setup)" if step == 0 else ""))
        return result

    # The final low-resolution model evaluation is the Eq. 3 prediction. Its Euler
    # update is discarded and rebuilt after the resolution transition, preserving NFE.
    resolution_scale = (getattr(model.get_model_object("latent_format"), "spacial_downscale_ratio", 1)
                        if video else getattr(model.get_model_object("latent_format"), "spacial_downscale_ratio", 1))
    low_timer = _StageTimer("low_resolution", low_model.load_device, (h, w), resolution_scale)
    low_drift_state = low_model.model_options.get(_DRIFT_CONTROL_KEY)
    if callable(getattr(low_drift_state, "configure_selflift_stage", None)):
        low_drift_state.configure_selflift_stage(
            (b, c, t, h, w), m_low, auxiliary_masks[0], hard_lock=False
        )
    sigma_k = sigmas[transition_step - 1]
    sigma_next = sigmas[transition_step]
    latent_format = model.get_model_object("latent_format")
    if handoff is None:
        log_memory("low_resolution start", low_model.load_device)
        low_sampler = radau.stage_sampler(sampler, boundary=transition) if radau_mode else sampler
        comfy.samplers.sample(low_model, noise_low, positive_low, negative_low, cfg, low_model.load_device,
                              low_sampler, sigmas[:transition_step + 1], low_model.model_options,
                              latent_image=low_latent, denoise_mask=low_noise_mask,
                              callback=callback_low,
                              disable_pbar=disable_pbar, seed=seed)
        if not radau_mode and low_evaluations != transition_step:
            raise RuntimeError(f"SelfLift: expected {transition_step} low-resolution callbacks, received {low_evaluations}; check sampler wrappers")
        low_timer.finish()
        log_memory("low_resolution end", model.load_device)
        if radau_mode:
            shapes = [tuple(s.shape) for s in _streams(low_latent)[0]]
            low_streams = radau.streams(transition.pop("state"), shapes, nested)
            x0_streams = radau.streams(transition.pop("x0"), shapes, nested)
            # Audio already completed this interval. Never advance it again.
            auxiliary_next = [s.to(device) for s in low_streams[1:]]
        else:
            low_streams, nested = _streams(transition.pop("state"))
            x0_streams, _ = _streams(transition.pop("x0"))
            auxiliary_next = [_euler_step(state.to(device), denoised.to(device), sigma_k, sigma_next)
                              for state, denoised in zip(low_streams[1:], x0_streams[1:])]
        z0_low_vae = latent_format.process_out(x0_streams[0].float()).to(device)
        del low_streams, x0_streams
    else:
        expected_format = radau.FORMAT if radau_mode else "h3_selflift_middle_v1"
        if (handoff.get("format") != expected_format
                or (radau_mode and handoff.get("sampler_contract") != sampler_contract)
                or handoff.get("seed") != seed or handoff.get("cfg") != cfg
                or handoff.get("transition_step") != transition_step
                or handoff.get("target_shape") != list(video_anchor.shape)
                or not torch.equal(handoff["sigmas"].cpu(), sigmas.cpu())):
            raise ValueError("SelfLift middle pass does not match this sampling schedule/seed/grid.")
        z0_low_vae = handoff["video_prediction"].to(device)
        auxiliary_next = [value.to(device) for value in handoff["auxiliary_next"]]
        if (tuple(z0_low_vae.shape) != low_shape
                or [tuple(s.shape) for s in auxiliary_next] != [tuple(s.shape) for s in audio_streams]):
            raise ValueError("SelfLift middle pass has incompatible video/audio shapes.")
    del low_latent, noise_low, positive_low, negative_low
    # This boundary is BEFORE loading the learned lifter or decoding a preview.
    # The auxiliary stream is noisy audio at sigma_next, not final audio/x0.
    if stop_after_low:
        middle = {"format": radau.FORMAT if radau_mode else "h3_selflift_middle_v1",
                "seed": int(seed), "cfg": float(cfg),
                "transition_step": int(transition_step), "target_shape": list(video_anchor.shape),
                "sigmas": sigmas.detach().cpu().clone(),
                "video_prediction": z0_low_vae.detach().cpu().contiguous().clone(),
                "auxiliary_next": [value.detach().cpu().contiguous().clone() for value in auxiliary_next]}
        if radau_mode:
            middle["sampler_contract"] = sampler_contract
        return middle
    if cleanup_between_stages:
        from .memory import release_stage_models
        release_stage_models([low_model], keep_models=[high_model], stage="low-to-lift")
    transition_timer = _StageTimer("transition", model.load_device, (H, W), resolution_scale)
    transition_timer.mark("prepare_endpoint")
    log_memory("transition endpoint_ready", model.load_device)

    # Artifact-Aware Consistency Lift (Eqs. 4-9); skip branches the weights discard
    need_pix = rho > 0.0 and w_max > 0.0
    need_lat = m_full is not None or not (rho >= 1.0 and w_min >= 1.0 and w_max >= 1.0)
    temporal_split = (
        min(int(drift_state.prefix_steps), int(z0_low_vae.shape[2]))
        if drift_continuation and latent_lifter is not None else None
    )
    z_lat_vae, z_pix_vae = selflift.paired_lifts(
        z0_low_vae, vae, (H, W), latent_upsample, latent_lifter,
        need_lat=need_lat, need_pix=need_pix, temporal_split=temporal_split)
    transition_timer.mark("paired_lifts")
    log_memory("transition lifts_ready", model.load_device)
    z_lat = latent_format.process_in(z_lat_vae) if z_lat_vae is not None else None
    z_pix = latent_format.process_in(z_pix_vae) if z_pix_vae is not None else None
    # Timeline continuation has already been resolved jointly at low
    # resolution. Lift that complete prediction as one temporal volume; do not
    # paste the predecessor's original high-resolution latent back afterward.
    # Generic non-Drift inpainting retains SelfLift's original masked lift.
    transition_mask = None if drift_continuation else m_full
    z0_high = selflift.artifact_aware_consistency_lift(z_lat, z_pix, rho, w_min, w_max, mask=transition_mask)
    if transition_mask is not None and not drift_continuation:
        # restore the keep-region with the true original instead of any lifted estimate
        m_cast = transition_mask.to(device=z0_high.device, dtype=z0_high.dtype)
        clean_video_anchor = latent_format.process_in(video_anchor).to(z0_high)
        blended = z0_high * m_cast + clean_video_anchor * (1.0 - m_cast)
        z0_high = torch.where(m_cast == 0, clean_video_anchor, blended)
        del clean_video_anchor, blended
    if os.environ.get("SELFLIFT_DEBUG", "0") == "1":
        _debug_dump(vae, {
            "z0_low": z0_low_vae,
            "z_lat": z_lat_vae,
            "z_pix": z_pix_vae,
            "z0_high": latent_format.process_out(z0_high),
        })
    low_resolution_carry = (
        z0_low_vae.detach().to(
            device=comfy.model_management.intermediate_device(),
            dtype=comfy.model_management.intermediate_dtype(),
        ).clone()
        if video else None
    )
    z0_high = z0_high.to(device)
    lifted_video_anchor = (
        latent_format.process_out(z0_high).to(video_anchor)
        if drift_continuation else None
    )
    if lifted_video_anchor is not None:
        prefix_steps = min(
            int(drift_state.prefix_steps),
            int(lifted_video_anchor.shape[2]),
            int(video_anchor.shape[2]),
        )
        lifted_video_anchor, seam_dc, seam_fade = _match_continuation_latent_dc(
            lifted_video_anchor, video_anchor, prefix_steps
        )
        if seam_dc is not None:
            # Keep the clean endpoint, the re-noised resume state and the
            # high-pass anchor in the same corrected latent coordinate system.
            z0_high = latent_format.process_in(lifted_video_anchor).to(z0_high)
            logging.debug(
                "[SelfLift seam DC] prefix=%d fade=%d clamp=%.3f max_abs=%.6f",
                prefix_steps, seam_fade, _CONTINUATION_DC_CLAMP,
                float(seam_dc.float().abs().max().item()),
            )
        del seam_dc
    del z0_low_vae, z_lat_vae, z_pix_vae, z_lat, z_pix
    transition_timer.mark("correction_and_debug")
    log_memory("transition correction_ready", model.load_device)

    if stop_after_lift:
        # Exactly the clean endpoint used below (including mask/rho/seam
        # correction), in native VAE space. Never re-noise or run high sampling.
        preview = latent_format.process_out(z0_high).detach().cpu().clone()
        transition_timer.finish()
        return preview

    # Re-noise the corrected video at its prediction sigma. Euler then finishes
    # its reused interval; Radau's prediction is already at the completed boundary.
    video_noise = comfy.sample.prepare_noise(z0_high, (seed + 1) % (1 << 64),
                                             latent_image.get("batch_index", None)).to(z0_high)
    if radau_mode:
        # A clean prediction at the completed boundary: re-noise directly
        # there, without rebuilding an Euler interval that Radau already ran.
        video_state = model_sampling.noise_scaling(sigma_next, video_noise, z0_high)
        next_streams = [video_state] + auxiliary_next
    else:
        video_state = model_sampling.noise_scaling(sigma_k, video_noise, z0_high)
        next_streams = [_euler_step(video_state, z0_high, sigma_k, sigma_next)] + auxiliary_next
    del z0_high, video_noise, video_state, auxiliary_next
    if native_av_mask:
        # Maintain a second, full-resolution continuation chain alongside the
        # native low-resolution carry. The current segment's complete low-res
        # prediction is lifted first, then the preceding final HQ tail becomes
        # the clean opening anchor for the remaining high-resolution steps.
        # Drift-Control owns the temporal release; there is deliberately no
        # post-sampling hard paste of this anchor.
        high_video_anchor = video_anchor.clone()
        if drift_continuation:
            logging.debug(
                "[SelfLift HQ continuation] previous final high-resolution tail "
                "anchors %d opening token(s) through Drift-Control",
                int(drift_state.prefix_steps),
            )
        # Audio deliberately remains the original clean stream so locked
        # speech/music is preserved exactly throughout the high stage.
        high_anchor_latent = _pack([high_video_anchor] + audio_streams, nested)
        anchor_streams, _ = _streams(_stage_latent_transform(high_model, high_anchor_latent, "in"))
        noise_scale = float(getattr(model_sampling, "noise_scale", 1.0))
        # Native inpaint needs a CLEAN anchor for both streams. Reconstruct
        # resume noise so unmasked tokens start from SelfLift's lifted state:
        # state = sigma * noise_scale * noise + (1-sigma) * clean.
        # In locked video tokens use ordinary unit noise, not the potentially
        # large solved residual (H3 uses a small noise augmentation on guides).
        resume_noise_streams = []
        for stream_index, (state, anchor) in enumerate(zip(next_streams, anchor_streams)):
            sigma = sigma_next.to(device=state.device, dtype=state.dtype)
            clean = anchor.to(device=state.device, dtype=state.dtype)
            resumed_noise = (state - (1.0 - sigma) * clean) / (sigma * noise_scale)
            if stream_index == 0:
                guide_noise = comfy.sample.prepare_noise(
                    clean, (seed + 1) % (1 << 64), latent_image.get("batch_index", None)
                ).to(clean)
                resumed_noise = torch.where(m_full.to(device=state.device) == 0, guide_noise, resumed_noise)
                del guide_noise
            resume_noise_streams.append(resumed_noise)
        resume_latent = high_anchor_latent
        resume_noise = _pack(resume_noise_streams, nested)
        del anchor_streams, resume_noise_streams, resumed_noise, high_video_anchor
        del state, anchor, clean, high_anchor_latent
    else:
        resume_streams = [model_sampling.inverse_noise_scaling(sigma_next, s) for s in next_streams]
        resume_latent = _stage_latent_transform(high_model, _pack(resume_streams, nested), "out")
        resume_noise = _pack([torch.zeros_like(s) for s in resume_streams], nested)
        del resume_streams
    del next_streams
    transition_timer.mark("renoise")
    transition_timer.finish()
    log_memory("transition end / high_resolution start", high_model.load_device)

    high_evaluations = 0
    high_callback = (latent_preview.prepare_callback(high_model, total_steps)
                     if model_hires is not None else callback)

    def callback_high(step, x0, x, total):
        nonlocal high_evaluations
        step = high_evaluations
        high_evaluations += 1
        if high_evaluations > total_steps - transition_step:
            raise RuntimeError("SelfLift: too many high-resolution progress callbacks for the stage schedule")
        result = high_callback(step + transition_step, x0, x, total_steps)
        high_timer.mark(f"step {step + 1}/{total_steps - transition_step}" + (" (includes setup)" if step == 0 else ""))
        return result

    high_timer = _StageTimer("high_resolution", high_model.load_device, (H, W), resolution_scale)
    high_drift_state = high_model.model_options.get(_DRIFT_CONTROL_KEY)
    if callable(getattr(high_drift_state, "configure_selflift_stage", None)):
        high_drift_state.configure_selflift_stage(
            (b, c, t, H, W), m_full, auxiliary_masks[0], hard_lock=False
        )
    if tiling:
        from .h3_tiling import tiled_model
        high_model = tiled_model(high_model, target_shapes, tiling)
    out = comfy.samplers.sample(high_model, resume_noise, positive, negative, cfg, high_model.load_device,
                                radau.stage_sampler(sampler) if radau_mode else sampler,
                                sigmas[transition_step:], high_model.model_options,
                                latent_image=resume_latent, denoise_mask=high_noise_mask,
                                callback=callback_high,
                                disable_pbar=disable_pbar, seed=seed)
    del resume_latent, resume_noise
    if not radau_mode and high_evaluations != total_steps - transition_step:
        raise RuntimeError(f"SelfLift: expected {total_steps - transition_step} high-resolution callbacks, received {high_evaluations}; check sampler wrappers")

    if m_full is not None and not drift_continuation:
        # Final numerical ownership of the overlap belongs to the preceding
        # segment.  Restore the exact incoming full-resolution video latent
        # after both SelfLift stages; the timeline finalizer will then discard
        # this repeated prefix from the later decoded segment as before.
        out_streams, out_nested = _streams(out)
        restore_mask = m_full.to(
            device=out_streams[0].device, dtype=out_streams[0].dtype
        ).expand_as(out_streams[0])
        # Native AV inpainting already blended fractional/painted masks.
        # Applying them again would square their strength. Only enforce exact
        # ownership at fully locked pixels on that path.
        restored_video = (out_streams[0] if native_av_mask else
                          out_streams[0] * restore_mask
                          + video_anchor.to(out_streams[0]) * (1.0 - restore_mask))
        restored_video = torch.where(
            restore_mask == 0, video_anchor.to(out_streams[0]), restored_video
        )
        out = _pack([restored_video] + out_streams[1:], out_nested)
        del out_streams, restore_mask, restored_video

    del lifted_video_anchor

    result = latent_image.copy()
    result.pop(_PREVIOUS_LOW_CARRY_KEY, None)
    result.pop("selflift_previous_prefix_steps", None)
    result["samples"] = out.to(device=comfy.model_management.intermediate_device(),
                                dtype=comfy.model_management.intermediate_dtype())
    if low_resolution_carry is not None:
        result[_LOW_CARRY_KEY] = low_resolution_carry
    high_timer.finish()
    log_memory("high_resolution end", high_model.load_device)
    return result
