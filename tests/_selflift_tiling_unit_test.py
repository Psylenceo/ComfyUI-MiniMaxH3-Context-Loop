"""CPU tiling geometry, blending, masks, settings and stage-scoping contracts."""
import importlib
import math
import types
import unittest
from unittest.mock import patch

from _selflift_unit_test import (PACKAGE, Model, Euler, SelfLiftTests,
                                nodes, runtime, lift, stub, torch, CALLS)

controls = importlib.import_module(PACKAGE + ".selflift_tiling")
tiling = importlib.import_module(PACKAGE + ".selflift_runtime.h3_tiling")
ON = {"enabled": True, "tiles": 2, "overlap": 2, "axis": "longest"}
stub("comfy.model_base", MiniMaxH3=types.SimpleNamespace)
runtime.comfy.patcher_extension.WrappersMP.DIFFUSION_MODEL = "diffusion"
runtime.comfy.patcher_extension.WrappersMP.PREPARE_SAMPLING = "prepare"
runtime.comfy.model_management.throw_exception_if_processing_interrupted = lambda: None


class TilingTests(unittest.TestCase):
    def test_schema_opt_in_and_registration(self):
        cls = nodes.NODE_CLASS_MAPPINGS["MiniMaxH3SelfLiftTiling"]
        self.assertIs(cls, controls.MiniMaxH3SelfLiftTiling)
        self.assertEqual(cls().configure(), ({"enabled": False},))
        self.assertEqual(cls().configure(True, 2, 2), (ON,))
        schema = nodes.MiniMaxH3ChainSelfLiftSampler.INPUT_TYPES()
        self.assertEqual(list(schema["optional"]), ["negative", "model_hires", "highres_tiling"])
        self.assertEqual(schema["optional"]["highres_tiling"][0], cls.RETURN_TYPES[0])
        for value in (None, False, {"enabled": False, "tiles": -1}):
            self.assertIsNone(controls.tiling_settings(value))
        for value in (True, {}, {**ON, "enabled": 1}, {**ON, "tiles": 1},
                      {**ON, "tiles": True}, {**ON, "overlap": 3}, {**ON, "axis": "time"}):
            with self.assertRaises(ValueError):
                controls.tiling_settings(value)

    def test_regions_cover_even_odd_tiny_grids_and_aligned_starts(self):
        for length in (1, 2, 3, 4, 5, 8, 15, 68, 120):
            for tiles in (2, 3, 8):
                for overlap in (0, 2, 8, 64):
                    for axis in ("height", "width"):
                        cfg = {**ON, "tiles": tiles, "overlap": overlap, "axis": axis}
                        dim, regions = tiling.tile_regions((1, 24, 3, length, length), cfg)
                        self.assertEqual(dim, 3 if axis == "height" else 4)
                        coverage = torch.zeros(length)
                        for start, end in regions:
                            self.assertEqual(start % 2, 0)
                            self.assertLess(start, end)
                            self.assertLessEqual(end, length)
                            self.assertTrue(end % 2 == 0 or end == length)
                            coverage[start:end] += 1
                        self.assertTrue(torch.all(coverage > 0))
                        self.assertLessEqual(len(regions), tiles)

    def test_identity_stitch_native_masks_first_audio_and_no_mutations(self):
        for axis in ("height", "width", "longest"):
            for margin in (0, 2, 8):
                video = torch.randn(1, 24, 3, 9, 17, dtype=torch.float64)
                audio = torch.randn(1, 32, 2, 12)
                mask = torch.rand(1, 1, 3, 9, 17)
                mask[:, :, 0] = 0
                am = torch.rand(1, 1, 2, 12)
                cfg = {**ON, "tiles": 3, "axis": axis, "overlap": margin}
                dim, regions = tiling.tile_regions(video.shape, cfg)
                original_options, payload = {"unrelated": 123}, {"refs": ["sentinel"]}
                calls = []
                def inner(streams, t, context, transformer_options, minimax_payload, **kw):
                    i = len(calls)
                    start, end = regions[i]
                    torch.testing.assert_close(kw["denoise_mask"], mask.narrow(dim, start, end-start))
                    self.assertIs(kw["audio_denoise_mask"], am)
                    self.assertIs(streams[1], audio)
                    calls.append(streams[0].shape)
                    transformer_options["temporary"] = 1
                    return streams[0], audio + i
                with patch.object(tiling, "tile_payload", return_value={}) as build:
                    out = tiling.tiled_forward(inner, [video, audio], torch.ones(1),
                        torch.ones(1, 2, 8), original_options, payload, settings=cfg,
                        denoise_mask=mask, audio_denoise_mask=am)
                torch.testing.assert_close(out[0], video, rtol=1e-6, atol=1e-6)
                torch.testing.assert_close(out[1], audio, rtol=0, atol=0)
                self.assertEqual(out[0].dtype, video.dtype)
                self.assertEqual(len(calls), len(regions))
                self.assertEqual(build.call_count, len(regions))
                self.assertEqual(original_options, {"unrelated": 123})
                self.assertEqual(payload, {"refs": ["sentinel"]})

    def test_overlap_blends_instead_of_overwriting_and_zero_margin(self):
        video, audio = torch.zeros(1, 24, 1, 4, 16), torch.zeros(1, 32, 2, 4)
        for overlap in (0, 2):
            calls = []
            def inner(streams, *a, **kw):
                calls.append(1)
                return torch.full_like(streams[0], len(calls)-1), audio
            cfg = {**ON, "overlap": overlap}
            with patch.object(tiling, "tile_payload", return_value={}):
                result = tiling.tiled_forward(inner, [video, audio], None, None, {}, settings=cfg)[0]
            self.assertTrue(torch.all(result[..., :6] == 0))
            self.assertTrue(torch.all(result[..., 10:] == 1))
            expected = torch.tensor([.125, .375, .625, .875]) if overlap else torch.tensor([0., 0., 1., 1.])
            torch.testing.assert_close(result[0, 0, 0, 0, 6:10], expected)

    def test_interrupt_and_unsupported_conditions(self):
        video, audio = torch.zeros(1, 24, 1, 8, 12), torch.zeros(1, 32, 2, 4)
        with patch.object(runtime.comfy.model_management, "throw_exception_if_processing_interrupted",
                          side_effect=RuntimeError("cancelled")):
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                tiling.tiled_forward(None, [video, audio], None, None, {}, settings=ON)
        for metadata in ({"control": object()}, {"area": (2, 2, 0, 0)}):
            with self.assertRaisesRegex(ValueError, "ControlNet or regional"):
                tiling.validate_target(Model(), [video.shape, audio.shape], [[None, metadata]])

    def test_model_scoped_wrappers_and_disabled_identity(self):
        model = Model()
        shapes = [(1, 24, 3, 8, 12), (1, 32, 2, 16)]
        self.assertIs(tiling.tiled_model(model, shapes, None), model)
        tiled = tiling.tiled_model(model, shapes, ON)
        self.assertEqual(model.model_options, {})
        self.assertEqual(len(tiled.model_options["wrappers"]), 2)
        twice = tiling.tiled_model(tiled, shapes, ON)
        self.assertEqual(len(twice.model_options["wrappers"]), 2)

    def test_memory_plan_includes_whole_sampler_buffers_and_forwards_flags(self):
        model = Model()
        model.model.memory_required = lambda shape: math.prod(shape) * 1024
        shapes = [(1, 24, 5, 68, 120), (1, 32, 2, 160)]
        count = sum(math.prod(s[1:]) for s in shapes)
        conds, options, seen = {"positive": ["preserved"]}, {}, []
        def executor(m, shape, c, **kw):
            seen.append((m, shape, c, kw))
            return "prepared"
        self.assertEqual(tiling.prepare_sampling(executor, model, (1, 1, count), conds,
            options, True, shapes=shapes, settings=ON), "prepared")
        self.assertIs(seen[0][2], conds)
        self.assertIs(seen[0][3]["model_options"], options)
        self.assertTrue(seen[0][3]["force_full_load"])
        expected = 24 * 5 * 68 * 62 + 32 * 2 * 160 + math.ceil(count * 32 / 1024)
        self.assertEqual(seen[0][1], (1, 1, expected))
        self.assertLess(expected, count)
        tiling.prepare_sampling(executor, model, (1, 1, count), conds,
            force_offload=True, shapes=shapes, settings=ON)
        self.assertEqual(seen[-1][1], (1, 1, count))


class StageTests(unittest.TestCase):
    setUp = SelfLiftTests.setUp

    def test_only_high_denoiser_is_patched_and_preview_is_not(self):
        args = (Model(), [], [], object(), self.latent, Euler(), self.sigmas,
                42, 1., 2, .5, 0., .5, 1., "nearest")
        with patch.object(tiling, "tiled_model", wraps=tiling.tiled_model) as patched:
            middle = runtime.progressive_sample(*args, stop_after_low=True,
                highres_tiling=ON, latent_lifter=lift)
            patched.assert_not_called()
            runtime.progressive_sample(*args, handoff=middle, stop_after_lift=True,
                highres_tiling=ON, latent_lifter=lift)
            patched.assert_not_called()
            output = runtime.progressive_sample(*args, handoff=middle,
                highres_tiling=ON, latent_lifter=lift)
            self.assertEqual(patched.call_count, 1)
        self.assertEqual([c["shape"][-2:] for c in CALLS], [(4, 6), (8, 12)])
        torch.testing.assert_close(output["samples"].unbind()[1], self.audio, rtol=0, atol=1e-6)
        torch.testing.assert_close(output["samples"].unbind()[0][:, :, :2], self.video[:, :, :2], rtol=0, atol=0)
        self.assertEqual(args[0].model_options, {})


if __name__ == "__main__":
    unittest.main()
