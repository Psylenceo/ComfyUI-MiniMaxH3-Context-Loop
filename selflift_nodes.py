"""Opt-in, project-scoped two-stage H3 generation. No startup model patches."""
from __future__ import annotations

import logging

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
    return ["none"] + sorted(name for name in names if name != "none")


class MiniMaxH3SelfLiftProject:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "plan": (PLAN_TYPE,),
            "enabled": ("BOOLEAN", {"default": False,
                "tooltip": "Project-wide switch in the dedicated SelfLift workflow. Off uses ordinary single-stage sampling; saved clips are not regenerated automatically."}),
            "upscaler_model": (upscaler_models(), {
                "tooltip": "H3 3D-convolution learned latent-upscaler checkpoint under models/latent_upscale_models. Required only when enabled; not compatible with H3 2D or generic image/LTX upscalers."}),
            "high_resolution_steps": ("INT", {"default": 2, "min": 1, "max": 10000,
                "tooltip": "Final steps at the Plan's full resolution. Must be lower than each generated scene's total steps (e.g. 6 low + 2 high out of 8)."}),
        }}

    RETURN_TYPES = (PLAN_TYPE, "STRING")
    RETURN_NAMES = ("plan", "status")
    FUNCTION = "configure"
    CATEGORY = "conditioning/minimax/context_loop"
    DESCRIPTION = ("Experimental SelfLift project switch for the dedicated Chain workflow. "
                   "Plan width/height are the final size; the first stage uses half-size spatial latents. "
                   "Does not patch ComfyUI or change any other workflow.")

    def configure(self, plan, enabled=False, upscaler_model="none", high_resolution_steps=2):
        result = dict(plan)
        result[SETTINGS_KEY] = {"enabled": bool(enabled),
                               "upscaler_model": str(upscaler_model),
                               "high_resolution_steps": int(high_resolution_steps)}
        status = ("SelfLift ON; half-resolution base; %d full-resolution steps; %s" %
                  (int(high_resolution_steps), upscaler_model) if enabled else
                  "SelfLift OFF; ordinary single-stage sampling")
        return result, status


def _stage_model(model, latent, sigmas):
    """Rebind only OUR dynamic-prefix patch to each grid; keep engine/LoRA patches."""
    from . import drift_control as drift
    previous = model.model_options.get(drift._WRAPPER_KEY)
    if previous is None:
        return model
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
        }, "optional": {"negative": ("CONDITIONING",)}}

    RETURN_TYPES = ("LATENT", "STRING")
    RETURN_NAMES = ("output", "status")
    FUNCTION = "sample"
    CATEGORY = "sampling/minimax/context_loop"
    DESCRIPTION = ("Dedicated experimental Chain sampler, controlled by SelfLift Project. "
                   "Supports native AV masks, source-audio locks, tagged references and guides. "
                   "SelfLift ON requires Euler and a learned H3 latent upscaler; no TST or spatial tiling.")

    def sample(self, state, model, positive, vae, latent, sampler, sigmas, seed, cfg=1.0, negative=None):
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
            raise ValueError("Select an installed H3 latent-upscaler model on SelfLift Project, or turn SelfLift off.")
        # Runtime imports, model registration and weight loading happen ONLY
        # when this explicitly enabled sampler executes, never on tab load.
        from .selflift_runtime.nodes import progressive_sample
        from .selflift_runtime.h3_upscaler import learned_latent_lift
        from .masking_support import require_h3_mask_support

        if latent.get("noise_mask") is not None:
            require_h3_mask_support()
        prepared = prepare_previous_context(latent, settings)
        staged_model = _stage_model(model, prepared, sigmas)
        def lifter(z, hw, temporal_split=None):
            return learned_latent_lift(z, hw, name, temporal_split=temporal_split)
        output = progressive_sample(
            staged_model, positive, negative, vae, prepared, sampler, sigmas,
            int(seed), float(cfg), total_steps - high_steps, 0.5,
            0.0, 0.5, 1.0, "nearest", latent_lifter=lifter)
        output[SIGNATURE] = settings_signature(settings)
        status = "SelfLift: %d low-resolution + %d full-resolution steps; native AV masks" % (
            total_steps - high_steps, high_steps)
        _LOG.info(status)
        return output, status


from .selflift_hunt import MiniMaxH3SelfLiftSeedHunt, register_routes

register_routes()

NODE_CLASS_MAPPINGS = {
    "MiniMaxH3SelfLiftProject": MiniMaxH3SelfLiftProject,
    "MiniMaxH3ChainSelfLiftSampler": MiniMaxH3ChainSelfLiftSampler,
    "MiniMaxH3SelfLiftSeedHunt": MiniMaxH3SelfLiftSeedHunt,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3SelfLiftProject": "MiniMax H3 SelfLift Project — Experimental",
    "MiniMaxH3ChainSelfLiftSampler": "MiniMax H3 Chain SelfLift Sampler — Experimental",
    "MiniMaxH3SelfLiftSeedHunt": "MiniMax H3 SelfLift Seed Hunt — Experimental",
}
