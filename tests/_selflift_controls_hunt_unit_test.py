"""Lift controls must preserve old hunt identities and distinguish changed takes."""
import importlib
import unittest
from unittest.mock import patch

import _selflift_hunt_unit_test as f

controls_module = importlib.import_module(f.PACKAGE + ".selflift_settings")


class LiftControlsHuntTests(unittest.IsolatedAsyncioTestCase):
    setUp = f.HuntTests.setUp
    fake_preview = f.HuntTests.fake_preview
    run_node = f.HuntTests.run_node

    async def test_explicit_defaults_resume_legacy_finished_hunt(self):
        original = await self.run_node(1, review_enabled=False)
        self.settings.update(controls_module.LIFT_DEFAULTS)
        f.CALLS.clear()
        resumed = await self.run_node(1, review_enabled=False)
        self.assertEqual(resumed["ui"], original["ui"])
        self.assertEqual(f.CALLS, [])
        self.assertEqual(len(self.store.list()), 1)

    async def test_changed_controls_get_distinct_hunts_and_resume_without_resampling(self):
        original = await self.run_node(1, review_enabled=False)
        ids = {original["ui"]["h3_selflift_hunt"][0]}
        def pixel(z, vae, hw):
            return f.lift(z, hw) + .2
        with patch.object(f.runtime.selflift, "_pixel_anchor_video", side_effect=pixel), \
                patch.object(f.runtime, "progressive_sample", wraps=f.runtime.progressive_sample) as sampling:
            for name, value in (("lowres_scale", .25), ("rho", .2), ("w_min", .3), ("w_max", .8)):
                self.settings[name] = value
                f.CALLS.clear()
                result = await self.run_node(1, review_enabled=False)
                key = result["ui"]["h3_selflift_hunt"][0]
                self.assertNotIn(key, ids)
                ids.add(key)
                self.assertEqual([c["shape"][-2:] for c in f.CALLS], [(2, 4), (8, 12)])
                expected = controls_module.lift_settings(self.settings)
                self.assertEqual(sampling.call_args.args[10:14], tuple(expected.values()))
                f.CALLS.clear()
                resumed = await self.run_node(1, review_enabled=False)
                self.assertEqual(resumed["ui"], result["ui"])
                self.assertEqual(f.CALLS, [])
        self.assertEqual(len(self.store.list()), 5)

    async def test_invalid_weights_or_tridae_scale_create_no_files_or_sampling(self):
        self.settings.update(w_min=.9, w_max=.1)
        with self.assertRaisesRegex(ValueError, "w_min <= w_max"):
            await self.run_node(1, review_enabled=False)
        self.settings.update(w_min=.5, w_max=1., lowres_scale=.25, upscaler_model="tridae")
        with patch.object(f.nodes, "upscaler_models", return_value=["tridae"]):
            with self.assertRaisesRegex(ValueError, "lowres_scale to 0.5"):
                await self.run_node(1, review_enabled=False)
        self.assertEqual(f.CALLS, [])
        self.assertEqual(self.store.list(), [])

    async def test_correction_failure_resumes_saved_low_pass_with_same_controls(self):
        self.settings.update(lowres_scale=.25, rho=.2, w_min=.3, w_max=.8)
        with patch.object(f.runtime.selflift, "_pixel_anchor_video", side_effect=RuntimeError("VAE OOM")):
            with self.assertRaisesRegex(RuntimeError, "VAE OOM"):
                await self.run_node(1, review_enabled=False)
        self.assertEqual([c["shape"][-2:] for c in f.CALLS], [(2, 4)])
        original = self.store.list()[0]["id"]
        f.CALLS.clear()
        with patch.object(f.runtime.selflift, "_pixel_anchor_video", side_effect=lambda z, vae, hw: f.lift(z, hw)):
            result = await self.run_node(1, review_enabled=False)
        self.assertEqual(result["ui"]["h3_selflift_hunt"], [original])
        self.assertEqual([c["shape"][-2:] for c in f.CALLS], [(8, 12)])


if __name__ == "__main__":
    unittest.main()
