#!/usr/bin/env python3
"""Passive tone diagnostics must not enable correction in review previews."""

import copy
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch

fixture = runpy.run_path(str(
    Path(__file__).with_name("_video_blend_unit_test.py")))
chain = fixture["chain"]


class ReviewTonePolicyTest(unittest.TestCase):
    def manifest(self, mode="guide", applied=None):
        segment = {
            "index": 2,
            "guide_tone_carry": {
                "version": "h3_guide_tone_carry_v1",
                "points": [[0.0, 0.0], [0.5, 0.52], [1.0, 1.0]],
                "detected_luma_jump": -6.4,
            },
        }
        if applied is not None:
            segment["guide_tone_input_applied"] = applied
        return {"compatibility": {"continuation_mode": mode},
                "segments": [{"index": 1}, segment]}

    def test_passive_detection_stays_off_in_ordinary_modes(self):
        for mode in ("guide", "audio_feathered_av", "latent_guide", ""):
            with self.subTest(mode=mode):
                manifest = self.manifest(mode)
                before = copy.deepcopy(manifest)
                self.assertEqual(
                    chain._partial_boundary_tone_match_mode(manifest), "off")
                self.assertEqual(manifest, before)

    def test_empty_legacy_diagnostics_do_not_enable_correction(self):
        manifest = self.manifest()
        manifest["segments"][1]["guide_tone_carry"] = {}
        self.assertEqual(
            chain._partial_boundary_tone_match_mode(manifest), "off")

    def test_explicitly_unapplied_diagnostics_stay_off(self):
        self.assertEqual(chain._partial_boundary_tone_match_mode(
            self.manifest(applied=False)), "off")

    def test_explicit_tone_carry_mode_is_preserved(self):
        self.assertEqual(chain._partial_boundary_tone_match_mode(
            self.manifest("tone_carry_guide")), "auto")

    def test_actual_tone_carry_input_in_mixed_history_is_preserved(self):
        self.assertEqual(chain._partial_boundary_tone_match_mode(
            self.manifest(applied=True)), "auto")

    def assert_preview_mode(self, manifest, expected, *, audio_fails=False):
        state = {"plan": {}}
        before = copy.deepcopy(manifest)
        result = {"result": ("preview.mp4",)}
        outcomes = ([RuntimeError("test audio failure"), result]
                    if audio_fails else [result])
        with patch.object(chain, "_partial_manifest", return_value=manifest), \
                patch.object(chain, "_run_dir", return_value="/unused"), \
                patch.object(chain, "_atomic_json"), \
                patch.object(chain.MiniMaxH3ChainAssemble, "assemble",
                             side_effect=outcomes) as assemble:
            preview, warning = chain._assemble_review_partial(
                state, manifest["segments"][-1], "checkpointed", None)
        self.assertEqual(preview, "preview.mp4")
        self.assertEqual(bool(warning), audio_fails)
        self.assertEqual(assemble.call_count, 2 if audio_fails else 1)
        for call in assemble.call_args_list:
            self.assertEqual(call.kwargs["boundary_tone_match"], expected)
        self.assertEqual(manifest, before)
        self.assertEqual(state, {"plan": {}})

    def test_review_assembly_does_not_enable_passive_diagnostics(self):
        self.assert_preview_mode(self.manifest("audio_feathered_av"), "off")

    def test_silent_fallback_does_not_enable_passive_diagnostics(self):
        self.assert_preview_mode(
            self.manifest("audio_feathered_av"), "off", audio_fails=True)

    def test_review_assembly_preserves_explicit_tone_carry(self):
        self.assert_preview_mode(self.manifest("tone_carry_guide"), "auto")


if __name__ == "__main__":
    unittest.main()
