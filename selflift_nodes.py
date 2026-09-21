"""Opt-in, project-scoped two-stage H3 generation. No startup model patches."""
from __future__ import annotations

import logging

from .selflift_upscalers import BILINEAR, TRIDAE, TRIDAE_CHECKPOINT, validate_upscaler_grid
from .selflift_settings import canonical_settings, lift_settings
from .selflift_tiling import TILING_TYPE, MiniMaxH3SelfLiftTiling, tiling_settings

_LOG = logging.getLogger(__name__)
PLAN_TYPE = "H3_CHAIN_PLAN"
STATE_TYPE = "H3_CHAIN_STATE"
SETTINGS_KEY = "selflift_sampling"


def upscaler_models():
    # List the standard model directory only when schemas are requested. Never
    # import the inference runtime or load weights to draw a node/project tab.
    import folder_paths
    import os
    paths = getattr(folder_paths, "folder_names_and_paths", {})
    if "latent_upscale_models" not in paths and hasattr(folder_paths, "models_dir"):
        folder_paths.add_model_folder_path(
            "latent_upscale_models", os.path.join(folder_paths.models_dir, "latent_upscale_models"))
    try:
        names = list(folder_paths.get_filename_list("latent_upscale_models"))
    except (AttributeError, KeyError):
        names = []
    # Keep none first: old workflows/defaults do not opt in to a different lift.
    builtins = ["none", BILINEAR, TRIDAE]
    return builtins + sorted(name for name in names
                             if name not in (*builtins, TRIDAE_CHECKPOINT))


class MiniMaxH3SelfLiftProject:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "plan": (PLAN_TYPE,),
            "enabled": ("BOOLEAN", {"default": False,
                "tooltip": "Project-wide switch in the dedicated SelfLift workflow. Off uses ordinary single-stage sampling; saved clips are not regenerated automatically."}),
            "upscaler_model": (upscaler_models(), {
                "tooltip": "Select an installed LBH H3 3D checkpoint, tridae (FP32 clean-latent 2x; downloads verified weights on first use), or bilinear (spatial-only, no weights). Tr1dae requires final dimensions divisible by 64. Required only when enabled; generic image/LTX upscalers are not compatible."}),
            "high_resolution_steps": ("INT", {"default": 2, "min": 1, "max": 10000,
                "tooltip": "Final steps at the Plan's full resolution. Must be lower than each generated scene's total steps (e.g. 6 low + 2 high out of 8)."}),
        }, "optional": {
            "cleanup_between_stages": ("BOOLEAN", {"default": False,
                "tooltip": "Release the retired checkpoint's DynamicVRAM buffers before lifting, and unload the learned upscaler after use. Tr1dae's small legacy upscaler is specifically offloaded to CPU; other classic/non-dynamic models are skipped. Preserves shared models and saved takes; can slow next-scene reloads."}),
            "lowres_scale": ("FLOAT", {"default": 0.5, "min": 0.25, "max": 1.0, "step": 0.05,
                "tooltip": "First-stage width/height relative to the final Plan size, rounded to H3's even latent grid. 0.5 keeps the existing half-resolution behavior. Tr1dae requires an exact 2x lift: use 0.5 and final dimensions divisible by 64."}),
            "rho": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.01,
                "tooltip": "Fraction of the most inconsistent latent locations to correct toward a pixel/VAE anchor. 0 keeps the existing direct lift. Above 0 (with w_max > 0) adds a full video VAE decode, pixel resize and VAE encode; costs time/memory and can change detail/color. Experimental, not a guaranteed artifact fix."}),
            "w_min": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.0, "step": 0.05,
                "tooltip": "Minimum pixel/VAE correction strength at selected locations. Inactive when rho=0. Must be <= w_max; 0 keeps the direct lift and 1 uses the pixel/VAE anchor."}),
            "w_max": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05,
                "tooltip": "Maximum pixel/VAE correction strength at the most inconsistent locations. Inactive when rho=0. Must be >= w_min; setting both weights to 0 skips the pixel/VAE pass."}),
        }}

    RETURN_TYPES = (PLAN_TYPE, "STRING")
    RETURN_NAMES = ("plan", "status")
    FUNCTION = "configure"
    CATEGORY = "conditioning/minimax/context_loop"
    DESCRIPTION = ("Experimental SelfLift project switch for the dedicated Chain workflow. "
                   "Plan width/height are the final size; lowres_scale defaults to half-size spatial latents. "
                   "Optional pixel/VAE consistency correction is disabled by default (rho=0). "
                   "Does not patch ComfyUI or change any other workflow.")

    def configure(self, plan, enabled=False, upscaler_model="none", high_resolution_steps=2,
                  cleanup_between_stages=False, lowres_scale=0.5, rho=0.0, w_min=0.5, w_max=1.0):
        result = dict(plan)
        result[SETTINGS_KEY] = canonical_settings({"enabled": bool(enabled),
                               "upscaler_model": str(upscaler_model),
                               "high_resolution_steps": int(high_resolution_steps),
                               "cleanup_between_stages": bool(cleanup_between_stages),
                               "lowres_scale": lowres_scale, "rho": rho, "w_min": w_min, "w_max": w_max})
        controls = lift_settings(result[SETTINGS_KEY])
        status = ("SelfLift ON; %.0f%% resolution base; %d full-resolution steps; %s" %
                  (100 * controls["lowres_scale"], int(high_resolution_steps), upscaler_model) if enabled else
                  "SelfLift OFF; ordinary single-stage sampling")
        if enabled:
            status += ("; pixel/VAE correction rho=%g, weights=%g..%g" %
                       (controls["rho"], controls["w_min"], controls["w_max"])
                       if controls["rho"] > 0 and controls["w_max"] > 0 else "; pixel/VAE correction OFF")
        if enabled and cleanup_between_stages:
            status += "; targeted stage cleanup ON (DynamicVRAM)"
        return result, status


def _stage_model(model, latent, sigmas, *, continuity_model=None):
    """Rebind only OUR dynamic-prefix patch to each grid; keep engine/LoRA patches."""
    from . import drift_control as drift
    # A separately loaded finishing checkpoint has no Chain Drift Control
    # wrapper. Inherit only that continuity policy, never the base model's
    # weights, LoRAs or unrelated engine patches.
    policy_model = model if continuity_model is None else continuity_model
    previous = policy_model.model_options.get(drift._WRAPPER_KEY)
    if previous is None:
        if continuity_model is not None and model.model_options.get(drift._WRAPPER_KEY) is not None:
            raise ValueError("SelfLift model_hires has Drift Control but the base model does not; connect the finishing checkpoint before its Chain Context patch.")
        return model
    existing_mask = model.model_options.get("denoise_mask_function")
    own_drift = model.model_options.get(drift._WRAPPER_KEY)
    if (continuity_model is not None and callable(existing_mask)
            and (own_drift is None or getattr(existing_mask, "__self__", None) is not own_drift)):
        raise ValueError("SelfLift model_hires has another dynamic denoise-mask patch; remove it before inheriting Chain Drift Control.")
    from comfy.patcher_extension import WrappersMP

    class StageDrift(drift._DriftControlMaskState):
        def configure_selflift_stage(self, video_shape, video_mask, audio_mask, hard_lock=False):
            self.video_shape = tuple(video_shape)
            self.current_video_mask = None

    patched = model.clone()
    # SelfLift solves its own clean-anchor transition; the ordinary same-grid
    # split-sampler handoff must not reinterpret it a second time.
    patched.remove_wrappers_with_key(WrappersMP.APPLY_MODEL, drift._WRAPPER_KEY)
    patched.remove_wrappers_with_key(WrappersMP.SAMPLER_SAMPLE, drift._SAMPLER_WRAPPER_KEY)
    video = list(latent["samples"].unbind())[0]
    state = StageDrift(tuple(video.shape), previous.prefix_steps, schedule_override=sigmas)
    patched.set_model_denoise_mask_function(state.denoise_mask_function)
    patched.add_wrapper_with_key(WrappersMP.APPLY_MODEL, drift._WRAPPER_KEY, state.apply_model_wrapper)
    patched.model_options[drift._WRAPPER_KEY] = state
    patched.model_options["h3_chain_selflift_drift_control"] = state
    return patched


class MiniMaxH3ChainSelfLiftSampler:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "state": (STATE_TYPE,), "model": ("MODEL",),
            "positive": ("CONDITIONING",), "vae": ("VAE",),
            "latent": ("LATENT",), "sampler": ("SAMPLER",), "sigmas": ("SIGMAS",),
            "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
            "cfg": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 100.0, "step": 0.1}),
        }, "optional": {"negative": ("CONDITIONING",), "model_hires": ("MODEL", {
            "tooltip": "Optional compatible H3 diffusion checkpoint for the final full-resolution steps. "
                       "Unconnected: reuse model. Same sampler, sigmas, CFG, VAE and conditioning; "
                       "connect desired LoRAs to this model separately. Ignored when SelfLift is off.",
        }), "highres_tiling": (TILING_TYPE, {
            "tooltip": "Optional SelfLift Tiling settings for the final high-resolution denoising steps only. "
                       "Off/unconnected preserves full-frame sampling. Not TST or latent-upscaler tiling.",
        })}}

    RETURN_TYPES = ("LATENT", "STRING")
    RETURN_NAMES = ("output", "status")
    FUNCTION = "sample"
    CATEGORY = "sampling/minimax/context_loop"
    DESCRIPTION = ("Dedicated experimental Chain sampler, controlled by SelfLift Project. "
                   "Supports native AV masks, source-audio locks, tagged references and guides. "
                   "SelfLift ON supports Euler or experimental RES4LYF Radau IA 2s (eta=0), "
                   "with an H3 learned or bilinear latent upscaler and optional high-stage spatial tiling; no TST.")

    def sample(self, state, model, positive, vae, latent, sampler, sigmas, seed, cfg=1.0, negative=None,
               model_hires=None, highres_tiling=None):
        import comfy.sample
        import comfy.samplers
        import comfy.utils
        import comfy.model_management
        import comfy.nested_tensor
        import latent_preview
        from .selflift_state import (SIGNATURE, prepare_previous_context, settings_signature)

        settings = state["plan"].get(SETTINGS_KEY, {})
        latent = dict(latent)
        if isinstance(latent["samples"], (list, tuple)):
            latent["samples"] = comfy.nested_tensor.NestedTensor(latent["samples"])
        negative = positive if negative is None else negative
        total_steps = int(sigmas.numel()) - 1
        if not settings.get("enabled", False):
            noise = comfy.sample.prepare_noise(latent["samples"], int(seed), latent.get("batch_index"))
            output = comfy.samplers.sample(
                model, noise, positive, negative, float(cfg), model.load_device,
                sampler, sigmas, model.model_options, latent_image=latent["samples"],
                denoise_mask=latent.get("noise_mask"),
                callback=latent_preview.prepare_callback(model, total_steps),
                disable_pbar=not comfy.utils.PROGRESS_BAR_ENABLED, seed=int(seed))
            # A normal result has no native low-stage prediction of its own.
            result = {key: value for key, value in latent.items() if not key.startswith("selflift_")}
            result["samples"] = output
            return result, "SelfLift OFF; ordinary single-stage sampling"

        high_steps = int(settings.get("high_resolution_steps", 2))
        if not 1 <= high_steps < total_steps:
            raise ValueError("SelfLift high-resolution steps must be between 1 and %d for this scene's %d-step schedule." %
                             (total_steps - 1, total_steps))
        name = str(settings.get("upscaler_model", "none"))
        if name == "none" or name not in upscaler_models():
            raise ValueError("Select tridae, bilinear, or an installed H3 latent-upscaler model on SelfLift Project, or turn SelfLift off.")
        controls = lift_settings(settings)
        validate_upscaler_grid(name, latent, controls["lowres_scale"])
        # Runtime imports, model registration and weight loading happen ONLY
        # when this explicitly enabled sampler executes, never on tab load.
        from .selflift_runtime.nodes import progressive_sample, _validate_hires_model
        from .selflift_runtime.h3_upscaler import learned_latent_lift
        from .masking_support import require_h3_mask_support

        _validate_hires_model(model, model_hires, sampler)
        tiling = tiling_settings(highres_tiling)
        if latent.get("noise_mask") is not None:
            require_h3_mask_support()
        prepared = prepare_previous_context(latent, settings)
        staged_model = _stage_model(model, prepared, sigmas)
        staged_hires = (_stage_model(model_hires, prepared, sigmas, continuity_model=model)
                        if model_hires is not None else None)
        cleanup = bool(settings.get("cleanup_between_stages", False))
        def lifter(z, hw, temporal_split=None):
            options = {"cleanup_after": True} if cleanup else {}
            return learned_latent_lift(z, hw, name, temporal_split=temporal_split, **options)
        output = progressive_sample(
            staged_model, positive, negative, vae, prepared, sampler, sigmas,
            int(seed), float(cfg), total_steps - high_steps, controls["lowres_scale"],
            controls["rho"], controls["w_min"], controls["w_max"], "nearest",
            latent_lifter=lifter, model_hires=staged_hires, highres_tiling=tiling,
            cleanup_between_stages=cleanup)
        output[SIGNATURE] = settings_signature(settings)
        status = "SelfLift: %d low-resolution + %d full-resolution steps; native AV masks" % (
            total_steps - high_steps, high_steps)
        if model_hires is not None:
            status += "; separate finishing checkpoint"
        if tiling:
            status += "; tiled high-resolution denoising"
        from .selflift_runtime.radau import is_radau
        if is_radau(sampler):
            status += "; experimental Radau IA 2s (+1 low-resolution boundary evaluation)"
        _LOG.info(status)
        return output, status


from .selflift_hunt import MiniMaxH3SelfLiftSeedHunt, register_routes

register_routes()

NODE_CLASS_MAPPINGS = {
    "MiniMaxH3SelfLiftTiling": MiniMaxH3SelfLiftTiling,
    "MiniMaxH3SelfLiftProject": MiniMaxH3SelfLiftProject,
    "MiniMaxH3ChainSelfLiftSampler": MiniMaxH3ChainSelfLiftSampler,
    "MiniMaxH3SelfLiftSeedHunt": MiniMaxH3SelfLiftSeedHunt,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3SelfLiftTiling": "MiniMax H3 SelfLift Tiling — Experimental",
    "MiniMaxH3SelfLiftProject": "MiniMax H3 SelfLift Project — Experimental",
    "MiniMaxH3ChainSelfLiftSampler": "MiniMax H3 Chain SelfLift Sampler — Experimental",
    "MiniMaxH3SelfLiftSeedHunt": "MiniMax H3 SelfLift Seed Hunt — Experimental",
}
