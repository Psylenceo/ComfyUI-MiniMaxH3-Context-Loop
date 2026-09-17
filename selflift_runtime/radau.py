"""Private RES4LYF adapter: completed-step handoff, never preview-as-state.

No RES4LYF imports or patches. The connected ClownSampler supplies its own
registered implementation and settings. Each stage gets independent options.
"""
import copy
import hashlib
import json

import torch

import comfy.k_diffusion.sampling
import comfy.samplers


KIND = "radau_ia_2s"
FORMAT = "h3_selflift_radau_middle_v1"


def is_radau(sampler):
    registered = getattr(comfy.k_diffusion.sampling, "sample_rk_beta", None)
    if registered is None or sampler.sampler_function is not registered:
        return False
    options = sampler.extra_options
    method = options.get("implicit_sampler_name", "use_explicit")
    if method in ("use_explicit", "none"):
        method = options.get("rk_type")
    return method == KIND


def contract(sampler):
    """Identify actual connected settings, also for direct (non-hunt) resumes."""
    options = sampler.extra_options
    for name in ("eta", "eta_substep", "etas", "etas_substep"):
        # RES4LYF defaults scalar eta/substep eta to .5, not zero.
        value = options.get(name, .5 if name in ("eta", "eta_substep") else None)
        if value is not None and not bool((torch.as_tensor(value) == 0).all()):
            raise ValueError("SelfLift Radau IA 2s currently requires eta=0 (including substeps).")
    if (options.get("sigmas_override") is not None or options.get("rk_swaps")
            or options.get("state_info") or options.get("steps_to_run", -1) != -1
            or options.get("guides") is not None or options.get("extra_options", "").strip()):
        raise ValueError("SelfLift Radau IA 2s currently supports the plain ClownSampler setup, "
                         "without schedule overrides, sampler swaps, guides or extra_options.")
    # These alter the schedule or model prediction outside the native Comfy
    # wrapper used for the boundary evaluation. Do not silently mix methods.
    for name, default in {"sampler_mode": "standard", "d_noise": 1., "d_noise_inv": 1.,
                          "cfg_cw": 1., "cfgpp": 0., "noise_scaling_weight": 0.,
                          "noise_scaling_eta": 0., "overshoot": 0., "overshoot_substep": 0.,
                          "noise_boost_step": 0., "noise_boost_substep": 0.}.items():
        if options.get(name, default) != default:
            raise ValueError(f"SelfLift Radau IA 2s requires the plain ClownSampler setting {name}={default}.")
    if options.get("start_at_step", -1) not in (-1, 0) or any(
            options.get(name) is not None for name in ("tile_sizes", "noise_initial", "image_initial",
                "epsilon_scales", "frame_weights_mgr", "regional_conditioning_weights",
                "noise_scaling_weights", "noise_scaling_etas")):
        raise ValueError("SelfLift Radau IA 2s does not support advanced stage overrides or automation yet.")

    def encode(value):
        if torch.is_tensor(value):
            tensor = value.detach().cpu().contiguous()
            return {"dtype": str(tensor.dtype), "shape": list(tensor.shape),
                    "sha256": hashlib.sha256(tensor.reshape(-1).view(torch.uint8).numpy().tobytes()).hexdigest()}
        raise TypeError("SelfLift Radau cannot save this sampler option: " + type(value).__name__)

    data = {key: value for key, value in options.items() if key != "state_info_out"}
    encoded = json.dumps({"options": data, "inpaint": sampler.inpaint_options},
                         default=encode, sort_keys=True, allow_nan=False).encode()
    return {"adapter": 1, "sampler": KIND, "settings": hashlib.sha256(encoded).hexdigest()}


def stage_sampler(sampler, *, boundary=None):
    """Wrap only this execution. Optional boundary gets raw packed x and x0.

    KSAMPLER normally inverse-scales its return value; capture before that.
    RES4LYF's callback may repeat a step or show a preview from a substage.
    Neither is used to construct the handoff. At a nonzero low-pass endpoint,
    evaluate x0 once through the same native inpaint/CFG model wrapper.
    """
    stage = copy.copy(sampler)
    stage.extra_options = copy.deepcopy({key: value for key, value in sampler.extra_options.items()
                                        if key not in ("state_info", "state_info_out")})
    stage.inpaint_options = copy.deepcopy(sampler.inpaint_options)
    original = sampler.sampler_function

    def sample(model, x, sigmas, extra_args=None, callback=None, disable=None, **options):
        last_step = -1
        last_sigma = None
        budget = len(sigmas) - 1

        def report(info):
            nonlocal last_step, last_sigma
            if info.get("sigma_next") is not None:
                last_sigma = info["sigma_next"]
            # RES4LYF can insert its model's minimum sigma before zero and
            # re-report the final step. Normalize progress, not solver state.
            step = min(max(int(info["i"]), 0), budget - 1)
            if callback is not None and step > last_step and not info.get("final", False):
                last_step = step
                callback({**info, "i": step})

        result = original(model, x, sigmas, extra_args=extra_args, callback=report,
                          disable=disable, **options)
        if boundary is not None:
            if last_sigma is None or not torch.isclose(torch.as_tensor(last_sigma).to(sigmas), sigmas[-1]).all():
                raise RuntimeError("SelfLift Radau stopped before the requested low-resolution boundary.")
            # Clone BEFORE the model wrapper (or a plugin hook) can touch x.
            boundary["state"] = result.detach().cpu().clone()
            sigma = sigmas[-1].to(result).expand(result.shape[0])
            predicted = model(result, sigma, **(extra_args or {}))
            boundary["x0"] = predicted.detach().cpu().clone()
        return result

    stage.sampler_function = sample
    return stage


def streams(value, shapes, nested):
    """Undo Comfy's packed AV layout; audio is never spatially resized."""
    if not nested:
        return [value]
    import comfy.utils
    return list(comfy.utils.unpack_latents(value, shapes))
