#!/usr/bin/env python3
"""Exercise Chain's real carry, resume, selected-window and gate paths on temp data."""
import asyncio
import importlib
import json
from pathlib import Path
import tempfile
import types
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

    def test_marked_takes_save_as_revisions_then_review_together_with_main_last(self):
        hunt = importlib.import_module(PACKAGE + ".selflift_hunt")
        selection = importlib.import_module(PACKAGE + ".selflift_selection")
        self.assertEqual(selection.MAX_FINISHED_TAKES, chain.MAX_REVIEW_CANDIDATES)
        store = importlib.import_module(PACKAGE + ".selflift_hunt_store").HuntStore(self.root)
        plan = chain._normalize_plan(json.dumps({"shots": [
            {"id": "walk", "prompt": "A dog walks.", "length": 90}]}),
            "marked_takes", 64, 64, 5, "video", "head", "disabled", "generated_audio",
            5, 1., 8, 11, 18, "test", 0, "latent_guide")
        state = chain._initial_state(plan, 1)
        base_seed = plan["shots"][0]["seed"]
        record = store.create({"id": "c" * 64, "run_name": plan["run_name"], "branch_id": "main",
            "scene": 1, "phase": "awaiting_save", "selected": 1, "main": 1,
            "selected_ordinals": [1, 2], "selection_id": "selection-1", "finishing_id": None})
        store.update(record["id"], lambda r: r.update(selected=1, candidates=[
            {"ordinal": 1, "seed": str(base_seed)}, {"ordinal": 2, "seed": str(base_seed + 1)}]))
        folder = store.locate(record["id"])
        (folder / "source.safetensors").write_bytes(b"temporary source")
        canonical = Path(chain._artifact_paths(plan, 1)["metadata"])
        images = torch.zeros(90, 64, 64, 3)
        audio = {"sample_rate": 8000, "waveform": torch.zeros(1, 2, 30000)}

        def inputs(ordinal):
            chosen = hunt.selected_state(state, base_seed + ordinal - 1)
            marker = {"id": record["id"], "created_at": record["created_at"],
                "selection_id": "selection-1", "finishing_id": None, "main": 1,
                "ordinals": [2, 1], "ordinal": ordinal, "source_plan": plan}
            chosen[selection.BATCH_STATE] = marker
            chosen[hunt.CLEANUP_STATE] = {"id": record["id"], "created_at": record["created_at"],
                "scene": 1, "selected": 1, "selection_id": "selection-1", "finishing_id": None}
            latent = {**self.latent, selection.BATCH_STATE: {
                k: marker[k] for k in ("id", "selection_id", "ordinal")}}
            return chosen, latent

        def video_fixture(_images, path, *_args, **_kwargs):
            Path(path).write_bytes(b"saved scene video fixture")

        secondary, secondary_latent = inputs(2)
        primary, primary_latent = inputs(1)
        saver = chain.MiniMaxH3ChainSegmentSave()
        with patch.object(chain, "_write_segment_video", side_effect=video_fixture):
            alternate = saver.save(secondary, images, secondary_latent, audio)["result"][0]
            self.assertFalse(canonical.exists(), "Alternate must not activate the scene for resume")
            self.assertTrue(folder.is_dir(), "No early cleanup of unfinished marked takes")
            self.assertTrue((self.root / alternate["revision_metadata"]).is_file())
            self.assertNotIn(selection.NEXT_TAKE, json.loads(
                (self.root / alternate["revision_metadata"]).read_text())["segment"])
            bypassed = asyncio.run(chain.MiniMaxH3ChainReview().review(
                secondary, alternate, True, False, 0, False, False, "none", candidate_count=8))
            self.assertIs(bypassed["result"][0], alternate)
            with patch.object(chain.MiniMaxH3ChainLoopEnd, "_recurse", return_value={"expand": {}}) as recurse:
                chain.MiniMaxH3ChainLoopEnd().end(
                    ["start", 0], secondary, images, secondary_latent, alternate)
                continued = recurse.call_args.args[1]
                self.assertEqual(continued["index"], 1)
                self.assertEqual(continued["plan"], plan)
                self.assertEqual(continued["segments"], [])
            atomic_json = chain._atomic_json
            def fail_main_commit(path, value):
                if Path(path) == canonical:
                    raise OSError("main commit failed")
                return atomic_json(path, value)
            with patch.object(chain, "_atomic_json", side_effect=fail_main_commit):
                with self.assertRaisesRegex(OSError, "main commit failed"):
                    saver.save(primary, images, primary_latent, audio)
            self.assertTrue(folder.exists(), "A failed main save keeps the entire hunt")
            self.assertTrue((self.root / alternate["checkpoint"]).is_file())
            self.assertEqual(selection.next_take(store, store.read(record["id"]), None), 1)
            main = saver.save(primary, images, primary_latent, audio)["result"][0]

        self.assertFalse(folder.exists(), "Cleanup may run only after both full saves")
        self.assertEqual(json.loads(canonical.read_text())["segment"]["revision"], main["revision"])
        self.assertEqual([s["revision"] for s in main[selection.FINISHED_TAKES]],
                         [alternate["revision"], main["revision"]])
        for item in main[selection.FINISHED_TAKES]:
            self.assertTrue((self.root / item["checkpoint"]).is_file())
        sent = []

        def send(event, payload, client_id=None):
            if event != "minimax_h3_context_loop_review":
                return
            sent.append(payload)
            chain._PENDING_REVIEWS[payload["token"]]["future"].set_result({
                "action": "approve", "candidate_revision": main["revision"],
                "kept_candidate_revisions": payload["kept_candidate_revisions"]})

        with patch.object(chain, "PromptServer", types.SimpleNamespace(instance=types.SimpleNamespace(
                send_sync=send, client_id="test"))), \
                patch.object(chain, "_review_video", return_value=({"filename": "fixture.mp4"}, True, "")):
            reviewed = asyncio.run(chain.MiniMaxH3ChainReview().review(
                primary, main, True, False, 0, False, False, "none", candidate_count=8, unique_id="review"))
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["candidate_count"], 2, "Do not run additional expensive seed hunts")
        self.assertEqual(sent[0]["candidate_index"], 2, "Main is the initially displayed candidate")
        self.assertTrue(sent[0]["candidate_generation_complete"])
        self.assertEqual(sent[0]["kept_candidate_revisions"], [alternate["revision"], main["revision"]])
        self.assertEqual(reviewed["result"][0]["revision"], main["revision"])
        # A later Review choice can still promote an alternate's own checkpoint.
        selected, chosen = chain._select_review_candidate(primary, main,
                                                          {"candidate_revision": alternate["revision"]})
        self.assertEqual(selected["revision"], alternate["revision"])
        self.assertEqual(chosen["plan"]["shots"][0]["seed"], base_seed + 1)
        self.assertEqual(selected["_h3_review_decision"]["action"], "candidate_selected")

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
