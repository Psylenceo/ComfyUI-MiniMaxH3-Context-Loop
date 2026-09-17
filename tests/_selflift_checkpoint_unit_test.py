#!/usr/bin/env python3
"""Exercise Chain's real carry, resume, selected-window and gate paths on temp data."""
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import torch
from safetensors.torch import save_file
from _png_export_unit_test import chain, folder_paths, PACKAGE

selflift = importlib.import_module(PACKAGE + ".selflift_state")


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.patch = patch.object(chain, "_streams_from_latent", lambda value: value["samples"])
        self.patch.start()
        self.addCleanup(self.patch.stop)
        folder_paths.output_directory = tmp.name
        self.video = torch.arange(27.).reshape(1, 1, 27, 1, 1).expand(1, 24, 27, 4, 4).clone()
        self.low = self.video[:, :, :, ::2, ::2].clone() + 100
        self.audio = torch.randn(1, 32, 2, 150)
        self.signature = selflift.settings_signature({"upscaler_model": "test"})
        self.latent = {"samples": [self.video, self.audio], selflift.LOW_CARRY: self.low,
                       selflift.SIGNATURE: self.signature}
        self.tensors = {"video": self.video, "audio": self.audio,
            "context_frames": torch.zeros(5, 2, 2, 3), **selflift.checkpoint_payload(self.latent)}
        self.checkpoint = self.root / "checkpoint.safetensors"
        save_file(self.tensors, str(self.checkpoint))

    def test_seed_hunt_selected_state_records_the_selected_seed(self):
        hunt = importlib.import_module(PACKAGE + ".selflift_hunt")
        plan = chain._normalize_plan(json.dumps({"shots": [
            {"id": "walk", "prompt": "A dog walks.", "length": 90}]}),
            "selflift_test", 64, 64, 5, "video", "head", "disabled", "generated_audio",
            5, 1., 8, 11, 18, "test", 0, "latent_guide")
        state = {"index": 1, "plan": plan, "segments": []}
        selected = hunt.selected_state(state, 18446744073709551614)
        self.assertEqual(selected["plan"]["shots"][0]["seed"], 18446744073709551614)
        self.assertEqual(selected["plan"]["review_overrides"]["1"]["seed"], 18446744073709551614)
        self.assertNotEqual(selected["plan"]["plan_hash"], plan["plan_hash"])
        self.assertNotEqual(plan["shots"][0]["seed"], 18446744073709551614)
        self.assertEqual(selected["plan"]["shots"][0]["prompt"], plan["shots"][0]["prompt"])

    def test_hunt_cleanup_only_after_real_segment_checkpoint_commit(self):
        hunt = importlib.import_module(PACKAGE + ".selflift_hunt")
        store = importlib.import_module(PACKAGE + ".selflift_hunt_store").HuntStore(self.root)
        for enabled in (False, True):
            with self.subTest(auto_remove=enabled):
                plan = chain._normalize_plan(json.dumps({"shots": [
                    {"id": "walk", "prompt": "A dog walks.", "length": 90}]}),
                    "cleanup_%s" % enabled, 64, 64, 5, "video", "head", "disabled", "generated_audio",
                    5, 1., 8, 11, 18, "test", 0, "latent_guide")
                state = chain._initial_state(plan, 1)
                record = store.create({"id": ("a" if enabled else "b") * 64,
                    "run_name": plan["run_name"], "branch_id": "main", "scene": 1, "phase": "finished"})
                store.update(record["id"], lambda r: r.update(selected=1))
                folder = store.locate(record["id"])
                for name in ("source.safetensors", "take_0001.safetensors", "finished_0001.safetensors"):
                    (folder / name).write_bytes(b"hunt recovery fixture")
                preview = store.preview_path(record, 1)
                preview.parent.mkdir(parents=True)
                preview.write_bytes(b"preview fixture")
                if enabled:
                    state[hunt.CLEANUP_STATE] = {"id": record["id"], "scene": 1,
                        "selected": 1, "created_at": record["created_at"]}
                images = torch.zeros(90, 64, 64, 3)
                audio = {"sample_rate": 8000, "waveform": torch.zeros(1, 2, 30000)}
                metadata_path = Path(chain._artifact_paths(plan, 1)["metadata"])
                atomic_json = chain._atomic_json

                def fail_commit(path, value):
                    if Path(path) == metadata_path:
                        raise OSError("synthetic commit failure")
                    return atomic_json(path, value)

                # Real Segment Save, checkpoint tensors and metadata; only the
                # video encoder is a tiny fixture, so no model or GPU is used.
                def video_fixture(_images, path, *_args, **_kwargs):
                    Path(path).write_bytes(b"saved scene video fixture")

                with patch.object(chain, "_write_segment_video", side_effect=video_fixture), \
                        patch.object(hunt, "cleanup_after_segment_save", wraps=hunt.cleanup_after_segment_save) as cleanup:
                    with patch.object(chain, "_atomic_json", side_effect=fail_commit):
                        with self.assertRaisesRegex(OSError, "synthetic commit failure"):
                            chain.MiniMaxH3ChainSegmentSave().save(state, images, self.latent, audio)
                    cleanup.assert_not_called()
                    self.assertTrue((folder / "finished_0001.safetensors").is_file())
                    self.assertTrue(preview.is_file())
                    result = chain.MiniMaxH3ChainSegmentSave().save(state, images, self.latent, audio)
                    self.assertEqual(cleanup.call_count, int(enabled))
                segment, status = result["result"]
                metadata = json.loads(metadata_path.read_text())
                self.assertEqual(metadata["segment"]["revision"], segment["revision"])
                for key in ("segment", "checkpoint", "revision_metadata", "generated_audio"):
                    if key in segment:
                        self.assertTrue((self.root / segment[key]).is_file(), key)
                from safetensors.torch import load_file
                saved = load_file(str(self.root / segment["checkpoint"]))
                torch.testing.assert_close(saved["selflift_low_resolution_carry"], self.low)
                self.assertEqual(folder.exists(), not enabled)
                self.assertEqual(preview.exists(), not enabled)
                self.assertEqual("SelfLift temporary" in status, enabled)

    def test_compact_and_editorial_trim_preserve_matching_time_axis(self):
        compact = chain._compact_latent(self.latent)
        self.assertNotEqual(compact[selflift.LOW_CARRY].data_ptr(), self.low.data_ptr())
        trimmed = chain._editorial_trim_latent(self.latent, {"raw_frames": 90,
            "_editorial_out_frames": 56, "_editorial_video_steps": 17, "_editorial_audio_steps": 93})
        torch.testing.assert_close(trimmed[selflift.LOW_CARRY], self.low[:, :, :17])
        self.assertEqual(trimmed["samples"][0].shape[2], 17)
        self.assertEqual(trimmed["samples"][1].shape[-1], 93)
        self.assertEqual(self.low.shape[2], 27)

    def test_selected_visual_window_uses_same_low_tokens(self):
        plan = chain._normalize_plan(json.dumps({"shots": [
            {"id": name, "prompt": name, "length": 90,
             **({"visual_context_source": "three", "visual_context_start_frame": 12,
                 "video_blend_frames": 0} if name == "five" else {})}
            for name in ("one", "two", "three", "four", "five")]}),
            "selflift_test", 64, 64, 5, "video", "head", "disabled", "generated_audio",
            5, 1., 8, 11, 18, "test", 0, "latent_guide")
        current = {"plan": plan, "index": 5, "previous_frames": torch.zeros(5, 2, 2, 3),
            "previous_latent": {"samples": [self.video, self.audio]},
            "segments": [{"index": i, "id": plan["shots"][i-1]["id"],
                "checkpoint": self.checkpoint.name, "revision": str(i), "raw_frames": 90,
                "delivered_frames": 90 if i == 1 else 85} for i in range(1, 5)]}
        selected = chain._visual_context_state(current)
        low = selected["previous_latent"][selflift.LOW_CARRY]
        high = selected["previous_latent"]["samples"][0]
        self.assertEqual(low.shape[2], 2)
        torch.testing.assert_close(low[:, :, :, 0, 0], high[:, :, :, 0, 0] + 100)
        self.assertEqual(float(low[0, 0, 0, 0, 0]), 105.)
        self.assertEqual(selected["previous_latent"][selflift.SIGNATURE], self.signature)

    def test_resume_keeps_native_carry_and_legacy_still_loads(self):
        segment = {"index": 1, "id": "one", "revision": "1" * 32,
            "raw_frames": 90, "delivered_frames": 90, "history_hash": "saved",
            "checkpoint": self.checkpoint.name}
        media = self.root / "synthetic.mp4"
        media.write_bytes(b"synthetic artifact: this test validates resume, not decoding")
        segment.update(segment=media.name, segment_sha256=chain._file_sha256(str(media)))
        metadata = self.root / "metadata.json"
        plan = {"run_name": "selflift_test", "shots": [{"id": "one"}, {"id": "two"}]}
        def resume():
            segment["checkpoint_sha256"] = chain._file_sha256(str(self.checkpoint))
            metadata.write_text(json.dumps({"history_hash": "saved", "segment": segment}))
            with patch.object(chain, "_recover_checkpoint_pointer_transactions"), \
                    patch.object(chain.CheckpointGraphManager, "active_selection", return_value=({}, [])), \
                    patch.object(chain, "_resume_context_predecessors", return_value={"scenes": [1]}), \
                    patch.object(chain, "_validate_scene_resolution_boundary"), \
                    patch.object(chain, "_artifact_paths", return_value={"metadata": str(metadata)}), \
                    patch.object(chain, "_prompt_fields", return_value={}), \
                    patch.object(chain, "_plan_context_storage_length", return_value=5):
                return chain._load_resume_state(plan, 2)["previous_latent"]
        restored = resume()
        torch.testing.assert_close(restored[selflift.LOW_CARRY], self.low)
        self.assertEqual(restored[selflift.SIGNATURE], self.signature)
        save_file({k: v for k, v in self.tensors.items() if not k.startswith("selflift_")}, str(self.checkpoint))
        self.assertNotIn(selflift.LOW_CARRY, resume())

    def test_gate_accepts_selected_candidates_own_native_carry(self):
        settings = {"enabled": True, "upscaler_model": "test", "high_resolution_steps": 2}
        plan = {"run_name": "selflift_test", "compatibility": {}, "selflift_sampling": settings}
        selected = {"index": 1, "id": "one", "revision": "b" * 32, "scene_prompt": "one",
                    "raw_frames": 90, "delivered_frames": 90, "history_hash": "saved",
                    "checkpoint": self.checkpoint.name, "selflift_sampling": settings}
        metadata = {"compatibility": {}, "segment": selected}
        with patch.object(chain, "_load_checkpoint_revision", return_value=(metadata, "")), \
                patch.object(chain, "_plan_with_review_revision", return_value=plan), \
                patch.object(chain, "_history_hash", return_value="saved"), \
                patch.object(chain, "_plan_context_storage_length", return_value=5), \
                patch.object(chain, "_artifact_paths", return_value={"metadata": str(self.root / "selected.json")}), \
                patch.object(chain, "_promote_checkpoint_run_archives"):
            accepted, next_state = chain._select_review_candidate(
                {"plan": plan, "index": 1, "candidate_batch": []}, {"revision": "a" * 32},
                {"candidate_revision": selected["revision"]})
        carried = accepted["_h3_review_decision"]["sampled_latent"]
        torch.testing.assert_close(carried[selflift.LOW_CARRY], self.low)
        self.assertEqual(carried[selflift.SIGNATURE], self.signature)
        self.assertEqual(accepted["selflift_sampling"], settings)
        self.assertNotIn("candidate_batch", next_state)


if __name__ == "__main__":
    unittest.main()
