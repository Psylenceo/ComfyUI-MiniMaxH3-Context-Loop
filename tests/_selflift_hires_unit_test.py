"""Separate finishing checkpoint contracts; synthetic CPU latents only."""
import copy
import importlib
import types
import unittest
from unittest.mock import patch

import _selflift_unit_test as fixtures
from _selflift_unit_test import (CALLS, Model, Flow, IdentityFormat, Euler, Nested,
                                nodes, runtime, lift, torch, PACKAGE)


class Checkpoint(Model):
    def __init__(self, name):
        super().__init__()
        self.name = name
        self.sampling = Flow()
        self.format = IdentityFormat()
        self.model.model_config = types.SimpleNamespace(unet_config={
            "image_model": "minimax_h3", "latents_dim": 24, "audio_latents_dim": 32,
            "text_dim": 5120, "patch_size": [1, 2, 2]})
        self.model_options["test_checkpoint"] = name

    def get_model_object(self, name):
        return self.sampling if name == "model_sampling" else self.format

    def clone(self):
        result = copy.copy(self)
        result.model_options = self.model_options.copy()
        result.model_options["wrappers"] = dict(self.model_options.get("wrappers", {}))
        return result


class HiresTests(unittest.TestCase):
    setUp = fixtures.SelfLiftTests.setUp

    def run_wrapper(self, low, high=None, settings=None):
        upscaler = importlib.import_module(PACKAGE + ".selflift_runtime.h3_upscaler")
        with patch.object(nodes, "upscaler_models", return_value=["h3_test.safetensors"]), \
             patch.object(upscaler, "learned_latent_lift", side_effect=lambda z, hw, *a, **kw: lift(z, hw, **kw), create=True):
            return nodes.MiniMaxH3ChainSelfLiftSampler().sample(
                {"plan": {nodes.SETTINGS_KEY: self.settings if settings is None else settings}},
                low, [], object(), self.latent, Euler(), self.sigmas, 42, model_hires=high)

    def test_optional_input_default_and_stage_dispatch(self):
        self.assertEqual(nodes.MiniMaxH3ChainSelfLiftSampler.INPUT_TYPES()["optional"]["model_hires"][0], "MODEL")
        low, high = Checkpoint("low"), Checkpoint("high")
        high.load_device = torch.device("cpu")
        callbacks = []
        with patch.object(runtime.comfy.samplers, "sample", wraps=fixtures.sampler_double) as sample, \
             patch.object(runtime.latent_preview, "prepare_callback", side_effect=lambda model, *a: callbacks.append(model.name) or (lambda *a: None)):
            output, status = self.run_wrapper(low, high)
        self.assertEqual([call.args[0].name for call in sample.call_args_list], ["low", "high"])
        self.assertIs(sample.call_args_list[-1].args[5], high.load_device)
        self.assertEqual(callbacks, ["low", "high"])
        self.assertIn("separate finishing checkpoint", status)
        torch.testing.assert_close(output["samples"].unbind()[1], self.audio)
        torch.testing.assert_close(output["samples"].unbind()[0][:, :, :2], self.video[:, :, :2], rtol=0, atol=0)
        self.assertEqual([c["shape"][-2:] for c in CALLS], [(4, 6), (8, 12)])

    def test_unconnected_keeps_existing_output_and_disabled_ignores_high(self):
        low = Checkpoint("low")
        default, _ = self.run_wrapper(low)
        explicit, _ = self.run_wrapper(low, Checkpoint("high"))
        for a, b in zip(default["samples"].unbind(), explicit["samples"].unbind()):
            torch.testing.assert_close(a, b, rtol=0, atol=0)
        CALLS.clear()
        _, status = self.run_wrapper(low, object(), {"enabled": False})
        self.assertIn("OFF", status)
        self.assertEqual([c["steps"] for c in CALLS], [4])

    def test_incompatible_checkpoints_rejected_before_any_sampling(self):
        changes = [
            lambda m: setattr(m, "sampling", object()),
            lambda m: setattr(m.sampling, "audio_scale", 4.),
            lambda m: setattr(m.sampling, "noise_scale", 2.),
            lambda m: setattr(m.sampling, "shift", 10.),
            lambda m: setattr(m.format, "scale_factor", .5),
            lambda m: setattr(m.format, "latent_channels", 16),
            lambda m: setattr(m, "format", object()),
            lambda m: m.model.model_config.unet_config.update(text_dim=4096),
            lambda m: m.model.model_config.unet_config.update(image_model="other"),
        ]
        for change in changes:
            high = Checkpoint("high")
            change(high)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.run_wrapper(Checkpoint("low"), high)
            self.assertEqual(CALLS, [])

    def test_dynamically_created_sampling_classes_are_compatible(self):
        low, high = Checkpoint("low"), Checkpoint("high")
        low.sampling = type("ModelSampling", (Flow,), {})()
        high.sampling = type("ModelSampling", (Flow,), {})()
        runtime._validate_hires_model(low, high, Euler())

    def test_equivalent_patch_size_metadata_keeps_stage_output(self):
        baseline, _ = self.run_wrapper(Checkpoint("low"), Checkpoint("high"))
        # Comfy detects H3's dimensions from weights but leaves patch_size at
        # its constructor default unless checkpoint JSON metadata supplies it.
        for low_value, high_value in ((None, [1, 2, 2]), ([1, 2, 2], None),
                                      ((1, 2, 2), [1, 2, 2]), (None, None)):
            with self.subTest(low=low_value, high=high_value):
                low, high = Checkpoint("low"), Checkpoint("high")
                for model, value in ((low, low_value), (high, high_value)):
                    config = model.model.model_config.unet_config
                    if value is None:
                        config.pop("patch_size")
                    else:
                        config["patch_size"] = value
                CALLS.clear()
                result, _ = self.run_wrapper(low, high)
                self.assertEqual([c["shape"][-2:] for c in CALLS], [(4, 6), (8, 12)])
                for actual, expected in zip(result["samples"].unbind(), baseline["samples"].unbind()):
                    torch.testing.assert_close(actual, expected, rtol=0, atol=0)

    def test_effective_patch_size_uses_loaded_model_without_reading_weights(self):
        low, high = Checkpoint("low"), Checkpoint("high")
        low.model.model_config.unet_config.pop("patch_size")
        low.model.diffusion_model = types.SimpleNamespace(patch_size=(1, 2, 2))
        high.model.diffusion_model = types.SimpleNamespace(patch_size=[1, 2, 2])
        runtime._validate_hires_model(low, high, Euler())
        # The actual loaded architecture wins even if metadata is absent or
        # stale. Never turn a genuine mismatch into a default-sized model.
        high.model.diffusion_model.patch_size = (1, 4, 4)
        with self.assertRaisesRegex(ValueError, r"patch_size.*model=\(1, 2, 2\).*model_hires=\(1, 4, 4\)"):
            self.run_wrapper(low, high)
        self.assertEqual(CALLS, [])

    def test_different_patch_sizes_still_fail_before_sampling(self):
        low = Checkpoint("low")
        low.model.model_config.unet_config.pop("patch_size")
        for value in ([1, 4, 4], [2, 2, 2], [2, 2], None):
            with self.subTest(value=value):
                high = Checkpoint("high")
                high.model.model_config.unet_config["patch_size"] = value
                with self.assertRaisesRegex(ValueError, "incompatible patch_size"):
                    self.run_wrapper(low, high)
                self.assertEqual(CALLS, [])

    def test_continuity_policy_inherited_without_copying_base_engine_or_loras(self):
        drift = importlib.import_module(PACKAGE + ".drift_control")
        low, high = Checkpoint("low"), Checkpoint("high")
        low_state = drift._DriftControlMaskState(self.video.shape, 12, schedule_override=self.sigmas)
        low.model_options[drift._WRAPPER_KEY] = low_state
        low.model_options["base_only_lora"] = object()
        high.model_options["high_only_lora"] = object()
        high.model_options["wrappers"] = {("apply", "external_high_engine"): object()}
        with patch.object(runtime.comfy.samplers, "sample", wraps=fixtures.sampler_double) as sample:
            self.run_wrapper(low, high)
        staged_low, staged_high = [call.args[0] for call in sample.call_args_list]
        for stage, shape in ((staged_low, (4, 6)), (staged_high, (8, 12))):
            policy = stage.model_options[drift._WRAPPER_KEY]
            self.assertEqual(policy.video_shape[-2:], shape)
            self.assertEqual(policy.prefix_steps, 12)
            self.assertIsNot(policy, low_state)
        self.assertIn("base_only_lora", staged_low.model_options)
        self.assertNotIn("base_only_lora", staged_high.model_options)
        self.assertIn("high_only_lora", staged_high.model_options)
        self.assertIn(("apply", "external_high_engine"), staged_high.model_options["wrappers"])
        self.assertNotIn(drift._WRAPPER_KEY, high.model_options)
        self.assertIsNone(low_state.current_video_mask)

    def test_cold_av_model_transform_scales_audio_and_restores_shape_on_error(self):
        model = Checkpoint("cold")
        model.model.latent_shapes = None
        original_sampling = Flow()
        model.model.model_sampling = original_sampling
        model.sampling.audio_scale = 4
        def convert(samples):
            self.assertEqual([tuple(s) for s in model.model.latent_shapes], [tuple(self.video.shape), tuple(self.audio.shape)])
            video, audio = samples.unbind()
            return Nested([video, audio * model.model.model_sampling.audio_scale])
        model.model.process_latent_in = convert
        result = runtime._stage_latent_transform(model, self.latent["samples"], "in")
        torch.testing.assert_close(result.unbind()[1], self.audio * 4)
        self.assertIsNone(model.model.latent_shapes)
        self.assertIs(model.model.model_sampling, original_sampling)
        model.model.process_latent_out = lambda _: (_ for _ in ()).throw(RuntimeError("conversion failed"))
        with self.assertRaisesRegex(RuntimeError, "conversion failed"):
            runtime._stage_latent_transform(model, self.latent["samples"], "out")
        self.assertIsNone(model.model.latent_shapes)
        self.assertIs(model.model.model_sampling, original_sampling)

    def test_cold_and_rebooted_av_handoff_matches_same_model_with_audio_scaling(self):
        def checkpoint(name):
            model = Checkpoint(name)
            model.sampling.audio_scale = 4.
            inner = model.model
            inner.model_sampling = model.sampling
            inner.latent_shapes = None
            def transform(samples, inverse=False):
                video, audio = samples.unbind()
                scale = inner.model_sampling.audio_scale if inner.latent_shapes is not None else 1.
                return Nested([video, audio / scale if inverse else audio * scale])
            inner.process_latent_in = transform
            inner.process_latent_out = lambda samples: transform(samples, inverse=True)
            return model
        def sample(model, noise, positive, negative, cfg, device, sampler, sigmas,
                   model_options, latent_image, **kwargs):
            model.model.latent_shapes = [s.shape for s in latent_image.unbind()]
            internal = model.model.process_latent_in(latent_image)
            output = fixtures.sampler_double(model, noise, positive, negative, cfg, device, sampler,
                                            sigmas, model_options, internal, **kwargs)
            return model.model.process_latent_out(output)
        def run(low, latent, high=None, **kwargs):
            return runtime.progressive_sample(low, [], [], object(), latent, Euler(), self.sigmas,
                42, 1., 2, .5, 0., .5, 1., "nearest", latent_lifter=lift, model_hires=high, **kwargs)
        self.am[..., :10] = 0
        self.am[..., 10:20] = .4
        self.am[..., 20:] = 1
        with patch.object(runtime.comfy.samplers, "sample", side_effect=sample):
            for latent in (self.latent, {"samples": self.latent["samples"]}):
                expected = run(checkpoint("low"), latent)
                switched = run(checkpoint("low"), latent, checkpoint("cold high"))
                middle = run(checkpoint("low"), latent, stop_after_low=True)
                restored = run(checkpoint("reboot low"), latent, checkpoint("reboot high"), handoff=middle)
                for result in (switched, restored):
                    for a, b in zip(expected["samples"].unbind(), result["samples"].unbind()):
                        torch.testing.assert_close(a, b, rtol=0, atol=0)

    def test_conflicting_finishing_mask_not_silently_overwritten(self):
        drift = importlib.import_module(PACKAGE + ".drift_control")
        low, high = Checkpoint("low"), Checkpoint("high")
        low.model_options[drift._WRAPPER_KEY] = drift._DriftControlMaskState(self.video.shape, 12)
        high.model_options["denoise_mask_function"] = lambda *args: None
        with self.assertRaisesRegex(ValueError, "dynamic denoise-mask"):
            self.run_wrapper(low, high)
        self.assertEqual(CALLS, [])
        del high.model_options["denoise_mask_function"]
        high.model_options[drift._WRAPPER_KEY] = low.model_options.pop(drift._WRAPPER_KEY)
        with self.assertRaisesRegex(ValueError, "base model does not"):
            self.run_wrapper(low, high)
        self.assertEqual(CALLS, [])

    def test_radau_dispatch_and_saved_middle_resume(self):
        import _selflift_radau_unit_test as radau
        low, high = Checkpoint("low"), Checkpoint("high")
        def run(**kwargs):
            return runtime.progressive_sample(low, [], [], object(), self.latent, radau.Radau(),
                self.sigmas, 42, 1., 2, .5, 0., .5, 1., "nearest", latent_lifter=lift,
                model_hires=high, **kwargs)
        with patch.object(runtime.comfy.k_diffusion.sampling, "sample_rk_beta", radau.sample_rk_beta, create=True), \
             patch.object(runtime.comfy.utils, "unpack_latents", radau.unpack, create=True), \
             patch.object(runtime.comfy.samplers, "sample", wraps=radau.packed_sampler) as sample:
            expected = run()
            self.assertEqual([c.args[0].name for c in sample.call_args_list], ["low", "high"])
            middle = run(stop_after_low=True)
            sample.reset_mock()
            result = run(handoff=middle)
            self.assertEqual([c.args[0].name for c in sample.call_args_list], ["high"])
        for a, b in zip(expected["samples"].unbind(), result["samples"].unbind()):
            torch.testing.assert_close(a, b, rtol=0, atol=0)


if __name__ == "__main__":
    unittest.main()
