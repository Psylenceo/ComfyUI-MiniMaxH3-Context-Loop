"""Exposed lift controls: CPU-only schema, validation, carry and sampling tests."""
import hashlib
import importlib
import json
import unittest
from unittest.mock import patch

import _selflift_unit_test as f

settings_module = importlib.import_module(f.PACKAGE + ".selflift_settings")
choices = importlib.import_module(f.PACKAGE + ".selflift_upscalers")
DEFAULTS = settings_module.LIFT_DEFAULTS


class LiftControlsTests(unittest.TestCase):
    setUp = f.SelfLiftTests.setUp

    def run_node(self, *, vae=None, **controls):
        settings = {**self.settings, **controls}
        upscaler = importlib.import_module(f.PACKAGE + ".selflift_runtime.h3_upscaler")
        with patch.object(f.nodes, "upscaler_models", return_value=["h3_test.safetensors"]), \
                patch.object(upscaler, "learned_latent_lift",
                             side_effect=lambda z, hw, name, **kw: f.lift(z, hw, **kw), create=True):
            return f.nodes.MiniMaxH3ChainSelfLiftSampler().sample(
                {"plan": {f.nodes.SETTINGS_KEY: settings}}, f.Model(), [], vae or object(),
                self.latent, f.Euler(), self.sigmas, 42)

    def test_new_widgets_append_after_legacy_widgets(self):
        with patch.object(f.nodes, "upscaler_models", return_value=["none"]):
            schema = f.nodes.MiniMaxH3SelfLiftProject.INPUT_TYPES()
        self.assertEqual(list(schema["required"]),
                         ["plan", "enabled", "upscaler_model", "high_resolution_steps"])
        self.assertEqual(list(schema["optional"]), ["cleanup_between_stages", *DEFAULTS])
        for name, default in DEFAULTS.items():
            self.assertEqual(schema["optional"][name][1]["default"], default)

    def test_project_explicit_defaults_preserve_old_settings(self):
        project = f.nodes.MiniMaxH3SelfLiftProject()
        old, _ = project.configure({}, True, "h3_test.safetensors", 2)
        explicit, status = project.configure({}, True, "h3_test.safetensors", 2, **DEFAULTS)
        self.assertEqual(old, explicit)
        self.assertEqual(explicit[f.nodes.SETTINGS_KEY], {**self.settings, "cleanup_between_stages": False})
        self.assertIn("correction OFF", status)

    def test_project_stores_changed_controls_without_mutating_plan(self):
        plan = {"shots": [], "selflift_sampling": {"enabled": False}}
        controls = dict(lowres_scale=.25, rho=.2, w_min=.3, w_max=.8)
        result, status = f.nodes.MiniMaxH3SelfLiftProject().configure(plan, True, **controls)
        self.assertEqual(plan, {"shots": [], "selflift_sampling": {"enabled": False}})
        for name, value in controls.items():
            self.assertEqual(result[f.nodes.SETTINGS_KEY][name], value)
        self.assertIn("25% resolution", status)
        self.assertIn("rho=0.2", status)

    def test_invalid_controls_fail_before_sampling(self):
        for controls in ({"lowres_scale": .1}, {"lowres_scale": 1.1}, {"lowres_scale": float("nan")},
                         {"rho": -.1}, {"rho": 1.1}, {"rho": None}, {"w_min": .9, "w_max": .5},
                         {"w_min": -.1}, {"w_max": float("inf")}, {"w_min": float("nan")}):
            with self.subTest(controls=controls):
                with self.assertRaisesRegex(ValueError, "SelfLift"):
                    f.nodes.MiniMaxH3SelfLiftProject().configure({}, True, **controls)
                with self.assertRaisesRegex(ValueError, "SelfLift"):
                    self.run_node(**controls)
        self.assertEqual(f.CALLS, [])

    def test_legacy_carry_signature_is_byte_for_byte_unchanged(self):
        payload = {"version": 1, "scale": .5, "upscaler_model": "h3_test.safetensors"}
        expected = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        self.assertEqual(f.state.settings_signature(self.settings), expected)
        self.assertEqual(f.state.settings_signature({**self.settings, **DEFAULTS}), expected)
        for name, value in (("lowres_scale", .25), ("rho", .2), ("w_min", .3), ("w_max", .8)):
            self.assertNotEqual(f.state.settings_signature({**self.settings, name: value}), expected)

    def test_previous_native_carry_uses_selected_scale_and_signature(self):
        settings = {**self.settings, "lowres_scale": .25}
        low = f.torch.zeros(1, 24, 2, 2, 4)
        latent = {**self.latent, f.state.PREVIOUS_LOW: low, f.state.PREFIX_STEPS: 2,
                  f.state.SIGNATURE: f.state.settings_signature(settings)}
        prepared = f.state.prepare_previous_context(latent, settings)
        self.assertIs(prepared[f.state.PREVIOUS_LOW], low)
        self.assertNotIn(f.state.PREVIOUS_LOW, f.state.prepare_previous_context(latent, self.settings))
        self.assertNotIn(f.state.PREVIOUS_LOW,
                         f.state.prepare_previous_context(latent, {**settings, "rho": .2}))

    def test_tridae_rejects_changed_grid_but_bilinear_and_lbh_accept_it(self):
        choices.validate_upscaler_grid("tridae", self.latent)
        with self.assertRaisesRegex(ValueError, "lowres_scale to 0.5"):
            choices.validate_upscaler_grid("tridae", self.latent, .25)
        for name in ("bilinear", "h3_test.safetensors"):
            choices.validate_upscaler_grid(name, self.latent, .25)

    def test_scale_changes_only_spatial_low_grid(self):
        with patch.object(f.runtime.selflift, "_pixel_anchor_video", side_effect=AssertionError("no VAE pass")):
            result, _ = self.run_node(lowres_scale=.25)
        self.assertEqual([c["shape"][-2:] for c in f.CALLS], [(2, 4), (8, 12)])
        self.assertEqual(result[f.state.LOW_CARRY].shape, (1, 24, 17, 2, 4))
        f.torch.testing.assert_close(result["samples"].unbind()[1], self.audio)

    def test_correction_parameters_reach_real_transition_and_preserve_locks(self):
        def pixel(z, vae, hw):
            return f.lift(z, hw) + .2
        with patch.object(f.runtime.selflift, "_pixel_anchor_video", side_effect=pixel) as anchor, \
                patch.object(f.runtime.selflift, "artifact_aware_consistency_lift",
                             wraps=f.runtime.selflift.artifact_aware_consistency_lift) as correction:
            result, _ = self.run_node(lowres_scale=.25, rho=.2, w_min=.3, w_max=.8)
        anchor.assert_called_once()
        self.assertEqual(correction.call_args.args[2:], (.2, .3, .8))
        self.assertEqual(anchor.call_args.args[0].shape[-2:], (2, 4))
        f.torch.testing.assert_close(result["samples"].unbind()[1], self.audio)
        f.torch.testing.assert_close(result["samples"].unbind()[0][:, :, :2], self.video[:, :, :2], rtol=0, atol=0)

    def test_zero_coverage_or_weights_skip_pixel_pass(self):
        with patch.object(f.runtime.selflift, "_pixel_anchor_video", side_effect=AssertionError("no VAE pass")):
            self.run_node(rho=0, w_min=.3, w_max=.8)
            self.run_node(rho=1, w_min=0, w_max=0)

    def test_correction_runs_actual_pixel_resize_with_cpu_vae_double(self):
        class VAE:
            device = f.torch.device("cpu")
            vae_dtype = f.torch.float32

            def __init__(self):
                self.calls = []

            def decode(self, z):
                self.calls.append(("decode", tuple(z.shape)))
                return z[0, :3].permute(1, 2, 3, 0)

            def encode(self, frames):
                self.calls.append(("encode", tuple(frames.shape)))
                return frames.permute(3, 0, 1, 2).unsqueeze(0).repeat(1, 8, 1, 1, 1)

        vae = VAE()
        result, _ = self.run_node(vae=vae, rho=.2)
        self.assertEqual(vae.calls, [("decode", (1, 24, 17, 4, 6)), ("encode", (17, 8, 12, 3))])
        video, audio = result["samples"].unbind()
        self.assertTrue(f.torch.isfinite(video).all())
        f.torch.testing.assert_close(audio, self.audio)
        f.torch.testing.assert_close(video[:, :, :2], self.video[:, :, :2], rtol=0, atol=0)

    def test_changed_scale_handoff_resumes_and_rejects_wrong_grid(self):
        args = (f.Model(), [], [], object(), self.latent, f.Euler(), self.sigmas, 42, 1., 2)
        middle = f.runtime.progressive_sample(*args, .25, 0., .5, 1., "nearest", stop_after_low=True)
        f.CALLS.clear()
        f.runtime.progressive_sample(*args, .25, 0., .5, 1., "nearest", handoff=middle, latent_lifter=f.lift)
        self.assertEqual([c["shape"][-2:] for c in f.CALLS], [(8, 12)])
        f.CALLS.clear()
        with self.assertRaisesRegex(ValueError, "incompatible video/audio shapes"):
            f.runtime.progressive_sample(*args, .5, 0., .5, 1., "nearest", handoff=middle, latent_lifter=f.lift)
        self.assertEqual(f.CALLS, [])


if __name__ == "__main__":
    unittest.main()
