"""Real ComfyUI H3 integration, using a tiny randomly initialized H3 network.

Usage: python tests/selflift_tiling_comfy_smoke.py COMFY_ROOT [cpu|cuda] [RES4LYF_ROOT]
No server, pretrained weights, downloads, workflow edits or project writes.
This tests real packed layouts, wrappers, sampling and masks, not image quality.
"""
# ruff: noqa: E402 -- Set ComfyUI's device arguments before importing its runtime.
import importlib
from pathlib import Path
import sys
import types

sys.dont_write_bytecode = True
comfy_root = Path(sys.argv[1])
device = sys.argv[2] if len(sys.argv) > 2 else "cpu"
res_root = Path(sys.argv[3]) if len(sys.argv) > 3 else None
sys.path.insert(0, str(comfy_root))
sys.argv = [sys.argv[0], "--cpu"] if device == "cpu" else [sys.argv[0], "--use-pytorch-cross-attention"]
import comfy.options
comfy.options.enable_args_parsing()
import comfy.samplers
import comfy.supported_models
import comfy.model_base
import comfy.model_patcher
import comfy.nested_tensor
from comfy.ldm.minimax.model import PackedLayout
import torch
torch.set_num_threads(1)

package = types.ModuleType("h3_tiling_smoke")
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules[package.__name__] = package
tiling = importlib.import_module(package.__name__ + ".selflift_runtime.h3_tiling")
runtime = importlib.import_module(package.__name__ + ".selflift_runtime.nodes")
ON = {"enabled": True, "tiles": 2, "overlap": 2, "axis": "width"}


def real_radau():
    # Import only the installed solver/node implementation, not its UI startup.
    for name, path in (("res_tiling_smoke", res_root), ("res_tiling_smoke.beta", res_root / "beta")):
        module = types.ModuleType(name)
        module.__path__ = [str(path)]
        sys.modules[name] = module
    logging_stub = types.ModuleType("res_tiling_smoke.res4lyf")
    logging_stub.RESplain = lambda *args, **kwargs: None
    logging_stub.get_display_sampler_category = lambda: False
    logging_stub.is_debug_logging_enabled = lambda: False
    sys.modules[logging_stub.__name__] = logging_stub
    solver = importlib.import_module("res_tiling_smoke.beta.rk_sampler_beta")
    comfy.k_diffusion.sampling.sample_rk_beta = solver.sample_rk_beta
    clown = importlib.import_module("res_tiling_smoke.beta.samplers")
    return clown.ClownSampler_Beta.execute(
        sampler_name="fully_implicit/radau_ia_2s", eta=0., bongmath=True, seed=42).result[0]


def check_layouts():
    video, audio = torch.zeros(1, 24, 3, 9, 17), torch.zeros(1, 32, 2, 12)
    text = torch.zeros(1, 4, 128)
    keyframes = [{"latent": video[:, :, :1], "resolved_frame_index": 0},
                 {"audio_latent": audio[..., :2], "resolved_frame_index": 2}]
    refs = [{"kind": "image", "latent_h": 4, "latent_w": 6, "latent": torch.zeros(1, 24, 1, 4, 6)},
            {"kind": "audio", "ref_audio_t": 2, "audio_latent": audio[..., :2]}]
    for references in (None, refs):
        payload = {"keyframes": keyframes, "refs": references,
                   "layout": PackedLayout(4, 3, 10, 18, 12, keyframes=keyframes, refs=references)}
        original = payload["layout"].position_ids.clone()
        for axis in (3, 4):
            start, end = (2, 9) if axis == 3 else (6, 17)
            out = tiling.tile_payload(payload, text, video, audio, axis, start, end)
            for (a, b, kind), (c, d, _) in zip(payload["layout"].segments, out["layout"].segments):
                expected = original[a:b]
                if kind in ("cond", "video"):
                    expected = expected.reshape(-1, 5, 9, 3).narrow(
                        axis-2, start//2, (end-start+1)//2).reshape(-1, 3)
                torch.testing.assert_close(out["layout"].position_ids[c:d], expected, rtol=0, atol=0)
            self_key = out["keyframes"][0]["latent"]
            assert self_key.shape[axis] == (end-start+1)//2*2
            assert out["cond_video_latents"][0] is self_key
            if references:
                assert out["cond_video_latents"][1] is refs[0]["latent"]
                assert out["refs"] is refs
            assert out["keyframes"][1]["audio_latent"] is keyframes[1]["audio_latent"]
        torch.testing.assert_close(payload["layout"].position_ids, original, rtol=0, atol=0)
        assert keyframes[0]["latent"].shape[-2:] == (9, 17)


@torch.inference_mode()
def check_sampling():
    torch.manual_seed(42)
    config = comfy.supported_models.MiniMaxH3({
        "image_model": "minimax_h3", "hidden_size": 128, "num_layers": 1,
        "token_refiner_num_layers": 1, "num_attention_heads": 1, "attention_head_dim": 128,
        "ffn_hidden_size": 256, "text_dim": 64, "timestep_input_dim": 32,
        "time_embed_hidden_size": 128, "time_embed_dim": 64, "rope_inv_freq_len": 16,
    })
    config.set_inference_dtype(torch.float32, None)
    base = comfy.model_base.MiniMaxH3(config, device=torch.device("cpu"))
    for name, parameter in base.named_parameters():
        if "norm" in name and name.endswith("weight"):
            parameter.fill_(1.)
        else:
            parameter.normal_(0, .02)
    base.diffusion_model.rope.inv_freq.copy_(torch.exp(-torch.arange(16).float() / 4))
    model = comfy.model_patcher.ModelPatcher(base, torch.device(device), torch.device("cpu"))
    video, audio = torch.randn(1, 24, 17, 8, 16), torch.randn(1, 32, 2, 12)
    vm, am = torch.ones(1, 1, 17, 8, 16), torch.zeros(1, 1, 2, 12)
    vm[:, :, :1] = 0
    vm[:, :, 1:, :4, 4:12] = .25
    latent = {"samples": comfy.nested_tensor.NestedTensor([video, audio]),
              "noise_mask": comfy.nested_tensor.NestedTensor([vm, am])}
    refs = [{"kind": "image", "latent_h": 4, "latent_w": 6, "latent": torch.randn(1, 24, 1, 4, 6)}]
    positive = [[torch.randn(1, 4, 64), {"minimax_keyframes": [
        {"latent": video[:, :, :1], "resolved_frame_index": 0}], "minimax_refs": refs}]]
    sampler = comfy.samplers.sampler_object("euler")
    sigmas = torch.tensor([1., .6, .3, 0.])
    args = (model, positive, positive, object(), latent, sampler, sigmas, 42, 1., 2, .5, 0., .5, 1., "nearest")
    calls = []
    original_forward = base.diffusion_model._forward
    def observed(x, *a, **kw):
        calls.append(tuple(x[0].shape))
        assert x[0].device.type == device
        return original_forward(x, *a, **kw)
    base.diffusion_model._forward = observed
    def lift(z, size, temporal_split=None):
        return torch.nn.functional.interpolate(z, size=(z.shape[2], *size), mode="nearest")
    middle = runtime.progressive_sample(*args, stop_after_low=True, latent_lifter=lift)
    assert [s[-2:] for s in calls] == [(4, 8), (4, 8)], calls
    calls.clear()
    normal = runtime.progressive_sample(*args, handoff=middle, latent_lifter=lift)
    assert [s[-2:] for s in calls] == [(8, 16)], calls
    calls.clear()
    disabled = runtime.progressive_sample(*args, handoff=middle, latent_lifter=lift,
                                          highres_tiling={"enabled": False})
    for a, b in zip(normal["samples"].unbind(), disabled["samples"].unbind()):
        torch.testing.assert_close(a, b, rtol=0, atol=0)
    calls.clear()
    for axis in ("width", "height"):
        out = runtime.progressive_sample(*args, handoff=middle, latent_lifter=lift,
                                        highres_tiling={**ON, "axis": axis})
        expected = [(8, 10), (8, 10)] if axis == "width" else [(6, 16), (6, 16)]
        assert [s[-2:] for s in calls] == expected, calls
        for result, source, mask in zip(out["samples"].unbind(), (video, audio), (vm, am)):
            assert result.shape == source.shape
            assert torch.isfinite(result).all()
            locked = mask.expand_as(source) == 0
            torch.testing.assert_close(result[locked], source[locked], rtol=0, atol=2e-6)
        calls.clear()
    assert model.wrappers == {}, model.wrappers
    assert refs[0]["latent"].shape[-2:] == (4, 6)
    drift = importlib.import_module(package.__name__ + ".drift_control")
    nodes = importlib.import_module(package.__name__ + ".selflift_nodes")
    vm[:, :, :12] = 0  # standard 8 matched + 4 taper steps
    model.model_options[drift._WRAPPER_KEY] = drift._DriftControlMaskState(
        video.shape, 12, schedule_override=sigmas)
    samplers = [sampler] + ([real_radau()] if res_root else [])
    for connected in samplers:
        staged = nodes._stage_model(model, latent, sigmas)
        drift_args = (staged, positive, positive, object(), latent, connected, sigmas,
                      42, 1., 2, .5, 0., .5, 1., "nearest")
        middle = runtime.progressive_sample(*drift_args, stop_after_low=True, latent_lifter=lift)
        assert all(s[-2:] == (4, 8) for s in calls), calls
        calls.clear()
        out = runtime.progressive_sample(*drift_args, handoff=middle, latent_lifter=lift, highres_tiling=ON)
        assert calls and all(s[-2:] == (8, 10) for s in calls), calls
        calls.clear()
        assert all(torch.isfinite(s).all() for s in out["samples"].unbind())
        torch.testing.assert_close(out["samples"].unbind()[1], audio, rtol=0, atol=2e-6)
        assert tiling.KEY not in repr(model.wrappers)
    print(f"PASS: real H3 on {device}; packed layouts, keyframes+refs, width/height tiles, "
          "native continuation/painted masks, locked audio, high-only wrappers, unchanged disabled path; "
          f"Drift Control + {'Euler and real Radau IA 2s' if res_root else 'Euler'}.")


check_layouts()
check_sampling()
