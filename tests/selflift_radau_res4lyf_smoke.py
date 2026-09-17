"""Optional CPU integration smoke using installed ComfyUI + RES4LYF code.

Usage: python tests/selflift_radau_res4lyf_smoke.py COMFY_ROOT RES4LYF_ROOT
No weights, GPU, server, workflow edits or project writes. Only RES4LYF's
logging/config frontend is stubbed; its ClownSampler and solver are real.
This checks the adapter contract, not H3 image quality.
"""
import copy
import importlib
from pathlib import Path
import sys
import types

sys.dont_write_bytecode = True
comfy_root, res_root = map(Path, sys.argv[1:])
sys.path.insert(0, str(comfy_root))
sys.argv = [sys.argv[0], "--cpu"]
import comfy.options
comfy.options.enable_args_parsing()
import comfy.samplers
import comfy.model_sampling
import torch
torch.set_num_threads(1)


def package(name, path):
    module = types.ModuleType(name)
    module.__path__ = [str(path)]
    sys.modules[name] = module
    return module


package("res_test", res_root)
package("res_test.beta", res_root / "beta")
logging_stub = types.ModuleType("res_test.res4lyf")
logging_stub.RESplain = lambda *args, **kwargs: None
logging_stub.get_display_sampler_category = lambda: False
logging_stub.is_debug_logging_enabled = lambda: False
sys.modules[logging_stub.__name__] = logging_stub
solver = importlib.import_module("res_test.beta.rk_sampler_beta")
comfy.k_diffusion.sampling.sample_rk_beta = solver.sample_rk_beta
clown = importlib.import_module("res_test.beta.samplers")
package("h3_test", Path(__file__).resolve().parents[1])
radau = importlib.import_module("h3_test.selflift_runtime.radau")
sampler = clown.ClownSampler_Beta.execute(
    sampler_name="fully_implicit/radau_ia_2s", eta=0., bongmath=True, seed=42).result[0]
assert radau.is_radau(sampler)
original_contract = radau.contract(sampler)


class Flow(comfy.model_sampling.ModelSamplingDiscreteFlow, comfy.model_sampling.CONST):
    pass


class Guider:
    def __init__(self):
        self.inner_model = types.SimpleNamespace(model_sampling=Flow(), device=torch.device("cpu"))
        self.inner_model.diffusion_model = types.SimpleNamespace()
        self.inner_model.scale_latent_inpaint = lambda *, sigma, noise, latent_image, **kw: (
            sigma.reshape(-1, 1, 1) * noise + (1 - sigma.reshape(-1, 1, 1)) * latent_image)
        self.model_patcher = types.SimpleNamespace(model=self.inner_model)
        self.conds = {"positive": [], "negative": []}
        self.cfg = 1.
        self.calls = []

    def __call__(self, x, sigma, **kwargs):
        self.calls.append(float(sigma[0]))
        return torch.full_like(x, .75) + sigma.reshape(-1, 1, 1) * .1


shapes = [(1, 24, 3, 4, 6), (1, 32, 2, 8)]
anchor, _ = comfy.utils.pack_latents([torch.zeros(shape) for shape in shapes])
mask = torch.ones_like(anchor)
mask[..., -512:] = 0  # locked audio
noise = torch.randn(anchor.shape, generator=torch.Generator().manual_seed(42))
sigmas = torch.tensor([1., .8, .6])


def run(connected, schedule, *, noise=noise, anchor=anchor):
    model, progress = Guider(), []
    output = connected.sample(model, schedule, {"seed": 42, "model_options": {}},
        lambda step, *args: progress.append(step), noise, latent_image=anchor,
        denoise_mask=mask, disable_pbar=True)
    assert torch.isfinite(output).all()
    return output, model.calls, progress


# Compare actual RES4LYF raw endpoint against the wrapped run. A KSAMPLER
# return is inverse-scaled; the adapter's durable state must NOT be.
baseline, base_calls, _ = run(copy.deepcopy(sampler), sigmas)
boundary = {}
out, calls, progress = run(radau.stage_sampler(sampler, boundary=boundary), sigmas)
torch.testing.assert_close(out, baseline, rtol=0, atol=0)
torch.testing.assert_close(boundary["state"], baseline * (1 - sigmas[-1]))
assert len(calls) == len(base_calls) + 1
assert abs(calls[-1] - .6) < 1e-6
assert progress == [0, 1], progress
parts = radau.streams(boundary["x0"], shapes, True)
torch.testing.assert_close(parts[0], torch.full(shapes[0], .81))
torch.testing.assert_close(parts[1], torch.zeros(shapes[1]))

# Resume the completed state through native inpaint. Include the real
# solver's minimum-sigma insertion and terminal duplicate callback.
end, _, high_progress = run(radau.stage_sampler(sampler), torch.tensor([.6, .3, 0.]),
                           noise=boundary["state"] / .6)
torch.testing.assert_close(end[..., -512:], torch.zeros_like(end[..., -512:]), atol=2e-5, rtol=0)
assert high_progress == [0, 1], high_progress
assert radau.contract(sampler) == original_contract
print("PASS: real ClownSampler IA 2s/BongMath, completed raw boundary, fresh prediction, "
      "native AV mask, high-stage callbacks, unchanged connected sampler (CPU; no H3 weights).")
