"""CPU packed-AV Radau handoff tests; synthetic tensors, no projects or weights."""
import importlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import _selflift_unit_test as fixtures
from _selflift_unit_test import (PACKAGE, Model, Euler, Flow, Nested,
                                nodes, runtime, state, lift, torch, sampler_double)
import comfy.k_diffusion.sampling
import comfy.samplers
import comfy.utils

radau = importlib.import_module(PACKAGE + ".selflift_runtime.radau")
store = importlib.import_module(PACKAGE + ".selflift_hunt_store")
STAGES = []


def pack(values):
    return torch.cat([v.reshape(v.shape[0], 1, -1) for v in values], -1)


def unpack(value, shapes):
    offset = 0
    result = []
    for shape in shapes:
        count = shape[1:].numel() if isinstance(shape, torch.Size) else int(torch.tensor(shape[1:]).prod())
        result.append(value[..., offset:offset + count].reshape(shape))
        offset += count
    return result


def sample_rk_beta(model, x, sigmas, extra_args=None, callback=None, disable=None, **options):
    """IA 2s tableau, post-step/repeated callbacks and deliberately wrong previews.

    This tests adapter semantics, not RES4LYF's quality/BongMath implementation.
    """
    for i, (sigma, next_sigma) in enumerate(zip(sigmas[:-1], sigmas[1:])):
        h = next_sigma - sigma
        sub_sigma = sigma + (2 / 3) * h
        # Solve this synthetic affine denoiser's two implicit stages exactly.
        p0 = model(x, sigma.expand(x.shape[0]), **extra_args)
        p1 = model(x, sub_sigma.expand(x.shape[0]), **extra_args)
        d0, d1 = (x - p0) / sigma, (x - p1) / sub_sigma
        a11, a12 = 1 - h / (4 * sigma), h / (4 * sigma)
        a21, a22 = -h / (4 * sub_sigma), 1 - 5 * h / (12 * sub_sigma)
        determinant = a11 * a22 - a12 * a21
        k0 = (a22 * d0 - a12 * d1) / determinant
        k1 = (a11 * d1 - a21 * d0) / determinant
        x = x + h * (k0 / 4 + 3 * k1 / 4)
        info = {"x": x, "denoised": torch.full_like(x, -999), "i": i,
                "sigma": sigma, "sigma_next": next_sigma}
        callback(info)
        callback(info)  # implicit iteration can report again at the same step
    callback({**info, "i": i + 1, "final": True})
    model.record["endpoint"] = x.clone()
    model.record["original_calls"] = len(model.record["evaluations"])
    # Simulate upstream options bookkeeping. Must never touch the connected sampler.
    options["etas"].add_(1)
    return x


class Radau(Euler):
    sampler_function = staticmethod(sample_rk_beta)

    def __init__(self):
        self.extra_options = {"rk_type": "euler", "implicit_sampler_name": "radau_ia_2s",
                              "eta": 0., "eta_substep": 0., "etas": torch.zeros(10),
                              "BONGMATH": True}
        self.inpaint_options = {}


def packed_sampler(model, noise, positive, negative, cfg, device, sampler, sigmas,
                   model_options, latent_image, denoise_mask=None, callback=None, **kwargs):
    if sampler.sampler_function is Euler.sampler_function:
        return sampler_double(model, noise, positive, negative, cfg, device, sampler, sigmas,
                              model_options, latent_image, denoise_mask, callback, **kwargs)
    anchors = latent_image.unbind()
    shapes = [tuple(v.shape) for v in anchors]
    anchor = pack(anchors)
    masks = pack([m.expand_as(a) for m, a in zip(denoise_mask.unbind(), anchors)]) if denoise_mask else torch.ones_like(anchor)
    record = {"shape": shapes[0], "anchors": anchors, "masks": masks.clone(),
              "evaluations": [], "sigmas": sigmas.clone(), "positive": positive}
    STAGES.append(record)

    def denoiser(x, sigma, **extra_args):
        current_mask = masks
        if "denoise_mask_function" in model_options:
            current_mask = model_options["denoise_mask_function"](sigma, masks, {"sigmas": sigmas})
        record["evaluations"].append({"sigma": float(sigma[0]), "mask": current_mask.clone()})
        # Vary prediction by sigma so a stale substage preview is detectably wrong.
        return (.75 + sigma[0] * .1) * current_mask + anchor * (1 - current_mask)

    denoiser.record = record
    def report(info):
        if callback:
            callback(info["i"], Nested(unpack(info["denoised"], shapes)),
                     Nested(unpack(info["x"], shapes)), len(sigmas) - 1)

    start = Flow().noise_scaling(sigmas[0], pack(noise.unbind()), anchor)
    result = sampler.sampler_function(denoiser, start, sigmas, extra_args={"denoise_mask": masks},
                                     callback=report, disable=True, **sampler.extra_options)
    return Nested(unpack(Flow().inverse_noise_scaling(sigmas[-1], result), shapes))


class RadauTests(unittest.TestCase):
    def setUp(self):
        fixtures.SelfLiftTests.setUp(self)
        STAGES.clear()
        self.sampler = Radau()
        for p in (patch.object(comfy.k_diffusion.sampling, "sample_rk_beta", sample_rk_beta, create=True),
                  patch.object(comfy.samplers, "sample", packed_sampler),
                  patch.object(comfy.utils, "unpack_latents", unpack, create=True)):
            p.start()
            self.addCleanup(p.stop)

    def run_runtime(self, *, sampler=None, latent=None, model=None, **kwargs):
        return runtime.progressive_sample(model or Model(), [], [], object(), latent or self.latent,
            sampler or self.sampler, self.sigmas, 42, 1., 2, .5, 0., .5, 1., "nearest",
            latent_lifter=lift, **kwargs)

    def test_completed_endpoint_not_preview_or_second_euler_audio_step(self):
        middle = self.run_runtime(stop_after_low=True)
        self.assertEqual(middle["format"], radau.FORMAT)
        self.assertEqual(len(STAGES), 1)
        call = STAGES[0]
        self.assertEqual(len(call["evaluations"]), call["original_calls"] + 1)
        self.assertAlmostEqual(call["evaluations"][-1]["sigma"], .6)
        torch.testing.assert_close(middle["auxiliary_next"][0], call["endpoint"][..., -self.audio.numel():].reshape(self.audio.shape))
        self.assertGreater(float(middle["video_prediction"].mean()), -10.)
        self.assertTrue((self.sampler.extra_options["etas"] == 0).all())
        self.assertIs(self.sampler.sampler_function, sample_rk_beta)

    def test_saved_middle_resume_equals_uninterrupted_with_feathered_audio(self):
        self.am[..., :20] = .25
        self.am[..., 20:] = 1
        expected = self.run_runtime()
        middle = self.run_runtime(stop_after_low=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "take.safetensors"
            store.save_bundle(path, middle)
            middle = store.load_bundle(path)
        STAGES.clear()
        result = self.run_runtime(handoff=middle)
        self.assertEqual([v["shape"][-2:] for v in STAGES], [(8, 12)])
        for a, b in zip(expected["samples"].unbind(), result["samples"].unbind()):
            torch.testing.assert_close(a, b, rtol=0, atol=0)
        torch.testing.assert_close(expected[state.LOW_CARRY], result[state.LOW_CARRY], rtol=0, atol=0)

    def test_resume_rejects_different_method_or_settings(self):
        middle = self.run_runtime(stop_after_low=True)
        for sampler, data in ((Euler(), middle), (Radau(), {**middle, "format": "h3_selflift_middle_v1"})):
            with self.assertRaisesRegex(ValueError, "middle pass"):
                self.run_runtime(sampler=sampler, handoff=data)
        changed = Radau()
        changed.extra_options["BONGMATH"] = False
        with self.assertRaisesRegex(ValueError, "middle pass"):
            self.run_runtime(sampler=changed, handoff=middle)

    def test_stage_cleanup_keeps_radau_output_and_resume_identical(self):
        memory = importlib.import_module(PACKAGE + ".selflift_runtime.memory")
        with patch.object(memory, "release_stage_models") as release:
            expected = self.run_runtime()
            release.assert_not_called()
            enabled = self.run_runtime(cleanup_between_stages=True)
            release.assert_called_once()
            release.reset_mock()
            middle = self.run_runtime(stop_after_low=True, cleanup_between_stages=True)
            release.assert_not_called()
            resumed = self.run_runtime(handoff=middle, cleanup_between_stages=True)
            release.assert_called_once()
        for result in (enabled, resumed):
            for a, b in zip(expected["samples"].unbind(), result["samples"].unbind()):
                torch.testing.assert_close(a, b, rtol=0, atol=0)

    def test_locked_audio_and_spatial_mask_unchanged(self):
        original = self.video.clone()
        result = self.run_runtime()
        torch.testing.assert_close(result["samples"].unbind()[1], self.audio)
        torch.testing.assert_close(result["samples"].unbind()[0][:, :, :2], self.video[:, :, :2], rtol=0, atol=0)
        torch.testing.assert_close(self.video, original, rtol=0, atol=0)
        for call in STAGES:
            self.assertTrue((call["masks"][..., -self.audio.numel():] == 0).all())
            self.assertTrue(((call["masks"] > 0) & (call["masks"] < 1)).any())

    def test_drift_and_previous_native_low_context_survive_resume(self):
        drift = importlib.import_module(PACKAGE + ".drift_control")
        model = Model()
        model.model_options[drift._WRAPPER_KEY] = drift._DriftControlMaskState(
            self.video.shape, 12, schedule_override=self.sigmas)
        previous = {state.LOW_CARRY: torch.full((1, 24, 17, 4, 6), 2.),
                    state.SIGNATURE: state.settings_signature(self.settings)}
        latent = state.prepare_previous_context(state.with_previous_context(self.latent, previous, 39), self.settings)
        def run(**kwargs):
            return self.run_runtime(model=nodes._stage_model(model, latent, self.sigmas), latent=latent, **kwargs)
        expected = run()
        torch.testing.assert_close(STAGES[0]["anchors"][0][:, :, :12], previous[state.LOW_CARRY][:, :, -12:])
        middle = run(stop_after_low=True)
        STAGES.clear()
        result = run(handoff=middle)
        self.assertEqual(len(STAGES), 1)
        for a, b in zip(expected["samples"].unbind(), result["samples"].unbind()):
            torch.testing.assert_close(a, b, rtol=0, atol=0)

    def test_no_masks_hard_cut_preserves_stream_dimensions(self):
        result = self.run_runtime(latent={"samples": self.latent["samples"]})
        self.assertEqual(result["samples"].unbind()[0].shape, self.video.shape)
        self.assertEqual(result["samples"].unbind()[1].shape, self.audio.shape)

    def test_plain_setup_only_and_exact_variant(self):
        for field, value in (("eta", .5), ("eta_substep", .1), ("etas", torch.ones(10)),
                             ("rk_swaps", [{}]), ("steps_to_run", 1), ("extra_options", "x_preview"),
                             ("cfg_cw", 2.), ("d_noise", .5), ("sampler_mode", "unsample"),
                             ("start_at_step", 1), ("tile_sizes", [(4, 4)]),
                             ("implicit_sampler_name", "radau_iia_3s")):
            sampler = Radau()
            sampler.extra_options[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                runtime._validate_sampling(Flow(), sampler)
        sampler = Radau()
        del sampler.extra_options["eta_substep"]
        with self.assertRaisesRegex(ValueError, "eta=0"):
            runtime._validate_sampling(Flow(), sampler)
        self.assertIsNone(runtime._validate_sampling(Flow(), Euler()))

    def test_incomplete_solver_return_cannot_be_saved_as_a_boundary(self):
        sampler = Radau()
        def partial(model, x, sigmas, callback=None, **kwargs):
            callback({"i": 0, "sigma_next": sigmas[1]})
            return x
        sampler.sampler_function = partial
        boundary = {}
        stage = radau.stage_sampler(sampler, boundary=boundary)
        with self.assertRaisesRegex(RuntimeError, "before the requested"):
            stage.sampler_function(None, torch.zeros(1, 1, 4), self.sigmas[:3], **stage.extra_options)
        self.assertEqual(boundary, {})


if __name__ == "__main__":
    unittest.main()
