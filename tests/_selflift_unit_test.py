#!/usr/bin/env python3
"""CPU SelfLift contracts with an Euler/AV sampler double; no weights or projects."""
import copy
import importlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import torch
from safetensors.torch import save_file, load_file

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "h3_selflift_test"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
nodes = importlib.import_module(PACKAGE + ".selflift_nodes")
state = importlib.import_module(PACKAGE + ".selflift_state")
assert PACKAGE + ".selflift_runtime.nodes" not in sys.modules


def stub(name, **values):
    module = types.ModuleType(name)
    module.__dict__.update(values)
    sys.modules[name] = module
    if "." in name:
        parent, child = name.rsplit(".", 1)
        if parent in sys.modules:
            setattr(sys.modules[parent], child, module)
    return module


class Nested:
    is_nested = True

    def __init__(self, values):
        self.values = list(values)

    def unbind(self):
        return self.values

    def to(self, *args, **kwargs):
        return Nested(v.to(*args, **kwargs) for v in self.values)


class Flow:
    noise_scale = 1.0

    def noise_scaling(self, sigma, noise, latent):
        return sigma * noise + (1 - sigma) * latent

    def inverse_noise_scaling(self, sigma, latent):
        return latent / (1 - sigma)


class Euler:
    extra_options = {}


def sample_euler():
    pass


Euler.sampler_function = staticmethod(sample_euler)


def prepare_noise(samples, seed, batch_index=None):
    gen = torch.Generator().manual_seed(seed)
    if getattr(samples, "is_nested", False):
        return Nested(torch.randn(x.shape, generator=gen) for x in samples.unbind())
    return torch.randn(samples.shape, generator=gen)


stub("comfy")
stub("comfy.k_diffusion")
stub("comfy.k_diffusion.sampling", sample_euler=sample_euler)
stub("comfy.model_management", intermediate_device=lambda: "cpu", intermediate_dtype=lambda: torch.float32)
stub("comfy.model_patcher")
stub("comfy.model_sampling", CONST=Flow)
stub("comfy.nested_tensor", NestedTensor=Nested)
stub("comfy.sample", prepare_noise=prepare_noise, fix_empty_latent_channels=lambda model, samples, *args: samples)
stub("comfy.utils", PROGRESS_BAR_ENABLED=False)
stub("comfy.patcher_extension", WrappersMP=types.SimpleNamespace(APPLY_MODEL="apply", SAMPLER_SAMPLE="sample"))
stub("latent_preview", prepare_callback=lambda *args: lambda *args: None)
stub(PACKAGE + ".selflift_runtime.h3_upscaler")
stub(PACKAGE + ".masking_support", require_h3_mask_support=lambda: None)


class IdentityFormat:
    spacial_downscale_ratio = 16
    process_in = staticmethod(lambda value: value)
    process_out = staticmethod(lambda value: value)


class Model:
    load_device = "cpu"

    def __init__(self):
        self.model_options = {}
        self.model = types.SimpleNamespace(process_latent_in=lambda x: x, process_latent_out=lambda x: x)

    def get_model_object(self, name):
        return Flow() if name == "model_sampling" else IdentityFormat()

    def clone(self):
        out = Model()
        out.model_options = copy.copy(self.model_options)
        out.model_options["wrappers"] = dict(self.model_options.get("wrappers", {}))
        return out

    def remove_wrappers_with_key(self, kind, key):
        self.model_options["wrappers"].pop((kind, key), None)

    def add_wrapper_with_key(self, kind, key, value):
        self.model_options["wrappers"][(kind, key)] = value

    def set_model_denoise_mask_function(self, value):
        self.model_options["denoise_mask_function"] = value


CALLS = []


def sampler_double(model, noise, positive, negative, cfg, device, sampler, sigmas,
                   model_options, latent_image, denoise_mask=None, callback=None, **kwargs):
    """Native AV mask/clean-anchor contract and pre-Euler callback ordering."""
    anchors = latent_image.unbind()
    masks = [torch.ones_like(x) for x in anchors] if denoise_mask is None else [
        m.expand_as(x) for x, m in zip(anchors, denoise_mask.unbind())]
    CALLS.append({"shape": tuple(anchors[0].shape), "anchors": [x.clone() for x in anchors],
                  "masks": [x.clone() for x in masks], "positive": positive, "steps": len(sigmas)-1})
    values = [Flow().noise_scaling(sigmas[0], n, a) for n, a in zip(noise.unbind(), anchors)]
    for index, (sigma, next_sigma) in enumerate(zip(sigmas[:-1], sigmas[1:])):
        dynamic = model_options.get("denoise_mask_function")
        current_masks = masks
        if dynamic:
            packed = torch.cat([m.reshape(m.shape[0], 1, -1) for m in masks], -1)
            packed = dynamic(sigma[None], packed, {"sigmas": sigmas})
            current_masks, offset = [], 0
            for a in anchors:
                count = a[0].numel()
                current_masks.append(packed[..., offset:offset + count].reshape(a.shape))
                offset += count
        # A constant fake denoiser makes mask and resume invariants measurable.
        predicted = [torch.full_like(a, 0.75) * m + a * (1 - m)
                     for a, m in zip(anchors, current_masks)]
        callback(index, Nested(predicted), Nested(values), len(sigmas)-1)
        values = [x + (x - p) * ((next_sigma - sigma) / sigma)
                  for x, p in zip(values, predicted)]
    return Nested(values)


stub("comfy.samplers", KSAMPLER=Euler, sample=sampler_double)
runtime = importlib.import_module(PACKAGE + ".selflift_runtime.nodes")


def lift(z, size, temporal_split=None):
    return torch.nn.functional.interpolate(z, size=(z.shape[2], *size), mode="nearest")


class SelfLiftTests(unittest.TestCase):
    def setUp(self):
        CALLS.clear()
        self.video = torch.randn(1, 24, 17, 8, 12)
        self.audio = torch.randn(1, 32, 2, 80)
        self.vm = torch.ones(1, 1, 17, 8, 12)
        self.vm[:, :, :2] = 0
        self.vm[:, :, 2:4, :4] = 0.3  # painted feather, not just a temporal lock
        self.am = torch.zeros(1, 1, 2, 80)
        self.latent = {"samples": Nested([self.video, self.audio]), "noise_mask": Nested([self.vm, self.am])}
        self.settings = {"enabled": True, "upscaler_model": "h3_test.safetensors", "high_resolution_steps": 2}
        self.sigmas = torch.tensor([1., .8, .6, .3, 0.])

    def run_runtime(self, latent=None, model=None, positive=None):
        return runtime.progressive_sample(
            model or Model(), positive or [], [], object(), latent or self.latent,
            Euler(), self.sigmas, 42, 1., 2, .5, 0., .5, 1., "nearest", latent_lifter=lift)

    def test_native_masks_and_locked_audio_both_stages(self):
        result = self.run_runtime()
        self.assertEqual([x["shape"][-2:] for x in CALLS], [(4, 6), (8, 12)])
        self.assertEqual([x["steps"] for x in CALLS], [2, 2])
        for call in CALLS:
            torch.testing.assert_close(call["masks"][1], self.am.expand_as(self.audio))
            torch.testing.assert_close(call["anchors"][1], self.audio)
            self.assertTrue(torch.all(call["masks"][0][:, :, :2] == 0))
            self.assertTrue(torch.any((call["masks"][0] > 0) & (call["masks"][0] < 1)))
        torch.testing.assert_close(result["samples"].unbind()[1], self.audio)
        torch.testing.assert_close(result["samples"].unbind()[0][:, :, :2], self.video[:, :, :2], rtol=0, atol=0)
        expected = self.video * (1 - self.vm) + .75 * self.vm
        torch.testing.assert_close(result["samples"].unbind()[0], expected)
        self.assertEqual(result[state.LOW_CARRY].shape, (1, 24, 17, 4, 6))
        torch.testing.assert_close(self.latent["samples"].unbind()[0], self.video)

    def test_middle_pass_resumes_exactly_without_low_sampling(self):
        # Include generated and feathered audio, not only a completely locked track.
        self.am[..., :20] = .25
        self.am[..., 20:] = 1
        expected = self.run_runtime()
        CALLS.clear()
        args = (Model(), [], [], object(), self.latent, Euler(), self.sigmas,
                42, 1., 2, .5, 0., .5, 1., "nearest")
        with patch.object(runtime, "_pixel_anchor", side_effect=AssertionError("no decode"), create=True):
            middle = runtime.progressive_sample(*args, stop_after_low=True,
                latent_lifter=lambda *a, **kw: self.fail("low stage must not lift"))
        self.assertEqual([v["steps"] for v in CALLS], [2])
        CALLS.clear()
        result = runtime.progressive_sample(*args, handoff=middle, latent_lifter=lift)
        self.assertEqual([v["shape"][-2:] for v in CALLS], [(8, 12)])
        self.assertEqual([v["steps"] for v in CALLS], [2])
        for a, b in zip(expected["samples"].unbind(), result["samples"].unbind()):
            torch.testing.assert_close(a, b, rtol=0, atol=0)
        torch.testing.assert_close(expected[state.LOW_CARRY], result[state.LOW_CARRY])
        with self.assertRaisesRegex(ValueError, "middle pass"):
            runtime.progressive_sample(*args, handoff={**middle, "seed": 43}, latent_lifter=lift)

    def test_middle_pass_resume_with_drift_control_and_native_low_carry(self):
        drift = importlib.import_module(PACKAGE + ".drift_control")
        original = Model()
        original.model_options[drift._WRAPPER_KEY] = drift._DriftControlMaskState(
            self.video.shape, 12, schedule_override=self.sigmas)
        previous = {state.LOW_CARRY: torch.randn(1, 24, 17, 4, 6),
                    state.SIGNATURE: state.settings_signature(self.settings)}
        latent = state.prepare_previous_context(state.with_previous_context(self.latent, previous, 39), self.settings)
        def run(**kwargs):
            return runtime.progressive_sample(nodes._stage_model(original, latent, self.sigmas),
                [], [], object(), latent, Euler(), self.sigmas, 42, 1., 2, .5, 0., .5, 1.,
                "nearest", latent_lifter=lift, **kwargs)
        expected = run()
        middle = run(stop_after_low=True)
        CALLS.clear()
        result = run(handoff=middle)
        self.assertEqual([v["shape"][-2:] for v in CALLS], [(8, 12)])
        for a, b in zip(expected["samples"].unbind(), result["samples"].unbind()):
            torch.testing.assert_close(a, b, rtol=0, atol=0)

    def test_feathered_audio_is_not_replaced_with_all_ones(self):
        self.am[..., :10] = .25
        self.am[..., 10:] = 1
        self.run_runtime()
        for call in CALLS:
            torch.testing.assert_close(call["masks"][1], self.am.expand_as(self.audio))

    def test_first_scene_without_masks_keeps_full_audio_length(self):
        out = self.run_runtime({"samples": self.latent["samples"]})
        self.assertEqual(out["samples"].unbind()[0].shape, self.video.shape)
        self.assertEqual(out["samples"].unbind()[1].shape, self.audio.shape)
        self.assertEqual(out[state.LOW_CARRY].shape[-2:], (4, 6))

    def test_guides_resize_but_refs_audio_and_time_do_not(self):
        keyframe = {"latent": torch.randn(1, 24, 7, 8, 12), "frame_idx": 22}
        audio_guide = {"audio_latent": self.audio, "frame_idx": 39}
        refs = [object()]
        data = {"minimax_keyframes": [keyframe, audio_guide], "minimax_refs": refs}
        cond = [[torch.zeros(1), data]]
        self.run_runtime(positive=cond)
        low = CALLS[0]["positive"][0][1]
        self.assertEqual(low["minimax_keyframes"][0]["latent"].shape, (1, 24, 7, 4, 6))
        self.assertEqual(low["minimax_keyframes"][0]["frame_idx"], 22)
        self.assertIs(low["minimax_refs"], refs)
        self.assertIs(low["minimax_keyframes"][1]["audio_latent"], self.audio)
        self.assertIs(CALLS[1]["positive"], cond)
        self.assertEqual(keyframe["latent"].shape[-2:], (8, 12))

    def test_previous_native_low_context_used_and_not_leaked(self):
        low = torch.full((1, 24, 17, 4, 6), 5.)
        previous = {state.LOW_CARRY: low, state.SIGNATURE: state.settings_signature(self.settings)}
        prepared = state.prepare_previous_context(state.with_previous_context(self.latent, previous, 5), self.settings)
        result = self.run_runtime(prepared)
        torch.testing.assert_close(CALLS[0]["anchors"][0][:, :, :2], low[:, :, -2:])
        self.assertNotIn(state.PREVIOUS_LOW, result)
        self.assertNotIn(state.PREFIX_STEPS, result)

    def test_legacy_or_changed_grid_lifter_fallback(self):
        for signature, shape in [("old-model", (4, 6)), (state.settings_signature(self.settings), (2, 4))]:
            source = {state.LOW_CARRY: torch.zeros(1, 24, 7, *shape), state.SIGNATURE: signature}
            result = state.prepare_previous_context(state.with_previous_context(self.latent, source, 22), self.settings)
            self.assertNotIn(state.PREVIOUS_LOW, result)
        self.assertNotIn(state.PREVIOUS_LOW, state.prepare_previous_context(self.latent, self.settings))

    def test_checkpoint_roundtrip_and_no_alias(self):
        source = {state.LOW_CARRY: torch.randn(1, 24, 17, 4, 6), state.SIGNATURE: state.settings_signature(self.settings)}
        cloned = state.metadata(source, clone=True)
        self.assertNotEqual(cloned[state.LOW_CARRY].data_ptr(), source[state.LOW_CARRY].data_ptr())
        tensors = {"video": self.video, "audio": self.audio, **state.checkpoint_payload(source)}
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "duplicate.safetensors")
            save_file(tensors, path)
            restored = state.checkpoint_latent(load_file(path))
        torch.testing.assert_close(restored[state.LOW_CARRY], source[state.LOW_CARRY])
        self.assertEqual(restored[state.SIGNATURE], source[state.SIGNATURE])
        self.assertEqual(state.checkpoint_payload({"samples": []}), {})
        self.assertEqual(set(state.checkpoint_latent({"video": self.video, "audio": self.audio})), {"samples"})

    def test_project_off_and_on_do_not_modify_original_plan(self):
        original = {"shots": [{"id": "one"}], "plan_hash": "saved"}
        out, _ = nodes.MiniMaxH3SelfLiftProject().configure(original, True, "h3_test.safetensors", 2)
        self.assertNotIn(nodes.SETTINGS_KEY, original)
        self.assertEqual(out["plan_hash"], "saved")
        self.assertEqual(out[nodes.SETTINGS_KEY], {**self.settings, "cleanup_between_stages": False})

    def test_wrapper_disabled_uses_one_call_no_lifter(self):
        latent = {**self.latent, state.LOW_CARRY: self.video, state.SIGNATURE: "old"}
        out, status = nodes.MiniMaxH3ChainSelfLiftSampler().sample(
            {"plan": {}}, Model(), [], object(), latent, Euler(), self.sigmas, 42)
        self.assertIn("OFF", status)
        self.assertEqual(len(CALLS), 1)
        self.assertEqual(CALLS[0]["shape"], self.video.shape)
        self.assertNotIn(state.LOW_CARRY, out)

    def test_wrapper_enabled_calls_learned_lifter_with_temporal_split(self):
        upscaler = sys.modules[PACKAGE + ".selflift_runtime.h3_upscaler"]
        called = []
        def learned(z, hw, model_name, temporal_split=None):
            called.append((model_name, temporal_split))
            return lift(z, hw, temporal_split)
        with patch.object(nodes, "upscaler_models", return_value=["none", "h3_test.safetensors"]), \
                patch.object(upscaler, "learned_latent_lift", learned, create=True):
            out, _ = nodes.MiniMaxH3ChainSelfLiftSampler().sample(
                {"plan": {nodes.SETTINGS_KEY: self.settings}}, Model(), [], object(), self.latent, Euler(), self.sigmas, 42)
        self.assertEqual(called, [("h3_test.safetensors", None)])
        self.assertEqual(out[state.SIGNATURE], state.settings_signature(self.settings))

    def test_invalid_sampler_and_step_budget(self):
        with self.assertRaisesRegex(ValueError, "standard Euler"):
            runtime._validate_sampling(Flow(), object())
        with self.assertRaisesRegex(ValueError, "high-resolution steps"):
            nodes.MiniMaxH3ChainSelfLiftSampler().sample(
                {"plan": {nodes.SETTINGS_KEY: {**self.settings, "high_resolution_steps": 4}}},
                Model(), [], object(), self.latent, Euler(), self.sigmas, 42)

    def test_stage_aware_drift_keeps_audio_and_original_model(self):
        drift = importlib.import_module(PACKAGE + ".drift_control")
        original = Model()
        old = drift._DriftControlMaskState(self.video.shape, 12, schedule_override=self.sigmas)
        original.model_options = {drift._WRAPPER_KEY: old, "wrappers": {
            ("sample", drift._SAMPLER_WRAPPER_KEY): object(), ("apply", "external_engine"): object()}}
        prepared = nodes._stage_model(original, self.latent, self.sigmas)
        new = prepared.model_options[drift._WRAPPER_KEY]
        self.assertIsNot(new, old)
        self.assertIn(("sample", drift._SAMPLER_WRAPPER_KEY), original.model_options["wrappers"])
        self.assertNotIn(("sample", drift._SAMPLER_WRAPPER_KEY), prepared.model_options["wrappers"])
        self.assertIn(("apply", "external_engine"), prepared.model_options["wrappers"])
        out = self.run_runtime(model=prepared)
        self.assertEqual(new.video_shape, self.video.shape)
        self.assertIsNone(old.current_video_mask)
        torch.testing.assert_close(out["samples"].unbind()[1], self.audio)
        self.assertEqual(out[state.LOW_CARRY].shape[-2:], (4, 6))


if __name__ == "__main__":
    unittest.main()
