#!/usr/bin/env python3
"""One-call resume hash reuse; tiny synthetic artifacts, no real projects/GPU."""
from collections import Counter
from contextlib import contextmanager
import copy
import errno
import hashlib
import json
import os
from pathlib import Path
import tempfile
import sys
import types
import unittest
from unittest.mock import patch

import _checkpoint_revision_unit_test as fixture

chain = fixture.chain


class ResumeVerificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="h3-resume-verification-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.original_root = fixture.folder_paths.output_directory
        fixture.folder_paths.output_directory = str(self.root)
        self.addCleanup(setattr, fixture.folder_paths, "output_directory", self.original_root)
        self.run = self.root / "h3_chains" / "resume_verification"
        policy = chain._contract_compose_chain_policy(
            chain._contract_audio_policy("generated", "off", "off"),
            chain._contract_transition_policy("cut"), audio_context_length=0)
        self.plan = chain._normalize_plan(
            json.dumps({"shots": [{"id": "scene_%d" % i, "prompt": "Test scene.", "length": 39}
                                  for i in range(1, 4)]}),
            self.run.name, 64, 64, 1, "video", "head", "disabled",
            "generated_audio", 0, 2.0, 8, 7, 18, "test-stack", 0, "guide", policy)
        self.plan["shots"][2].update(
            context_length=5, continuation_mode="guide", visual_context_source=1)
        self.plan["compatibility"]["context_storage_length"] = 5
        streams = patch.object(chain, "_streams_from_latent", new=lambda value: value["samples"])
        streams.start()
        self.addCleanup(streams.stop)
        self.segments = []
        for index in (1, 2):
            metadata, _ = fixture.write_revision(
                self.run, index, "%032x" % index, index, active=True,
                run_name=self.run.name, context_length=0, audio_context_length=0,
                generated_continuity="off", compatibility=self.plan["compatibility"])
            segment = metadata["segment"]
            checkpoint = self.root / segment["checkpoint"]
            chain._st_save({
                "context_frames": chain.torch.zeros(5, 2, 2, 3),
                "video": chain.torch.zeros(1, 24, 12, 1, 1),
                "audio": chain.torch.zeros(1, 32, 2, 65),
            }, str(checkpoint))
            segment["checkpoint_sha256"] = fixture.digest(checkpoint)
            metadata["history_hash"] = segment["history_hash"] = chain._history_hash(self.plan, index)
            metadata["scene_dependency"] = chain._scene_dependency_record(self.plan, index)
            for key, suffix in (("blend_segment", "blend.mp4"), ("generated_audio", "audio.wav")):
                path = self.run / "segments" / ("clip_%04d.%s" % (index, suffix))
                path.write_bytes(("saved-" + key).encode())
                segment[key] = str(path.relative_to(self.root))
                segment[key + "_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            segment["blend_frames"] = 1
            for key in ("metadata", "revision_metadata"):
                (self.root / segment[key]).write_text(json.dumps(metadata), encoding="utf-8")
            self.segments.append(segment)
        self.artifacts = {str(self.root / segment[key])
                          for segment in self.segments
                          for key in ("segment", "checkpoint", "blend_segment", "generated_audio", "prompt_file")}

    def start(self, **kwargs):
        return chain.MiniMaxH3ChainLoopStart().start(self.plan, 3, **kwargs)

    def test_append_review_resumes_after_saved_final_scene(self):
        original = copy.deepcopy(self.plan)
        original["shots"] = original["shots"][:2]
        self.assertEqual(chain._history_hash(original, 2),
                         chain._history_hash(self.plan, 2))
        before = {path: Path(path).read_bytes() for path in self.artifacts}
        _, state, _ = self.start()
        self.assertEqual(state["index"], 3)
        self.assertEqual(len(state["segments"]), 2)
        self.assertFalse(state["scene_range_explicit"])
        self.assertEqual(before, {path: Path(path).read_bytes() for path in self.artifacts})
        utils = types.ModuleType("comfy_execution.utils")
        utils.get_executing_context = lambda: types.SimpleNamespace(prompt_id="source-prompt")
        with patch.dict(sys.modules, {"comfy_execution.utils": utils}):
            public = chain._review_continuation_payload(state)
        self.assertEqual(public["prompt_id"], "source-prompt")
        self.assertEqual(public["plan_scene_ids"], ["scene_1", "scene_2", "scene_3"])
        self.assertEqual(public["end_clip"], 3)
        self.assertFalse(public["scene_range_explicit"])

    def test_explicit_final_range_cannot_be_extended_by_review(self):
        _, state, _ = self.start(scene_range="3")
        public = chain._review_continuation_payload(state)
        self.assertTrue(public["scene_range_explicit"])
        state["end_clip"] = 2
        self.assertEqual(chain._review_continuation_payload(state)["end_clip"], 2,
                         "Review must use end_clip, not the obsolete range_end key")

    def test_loop_start_reads_only_context_once_but_each_new_queue_rechecks(self):
        original = chain._file_sha256
        checkpoints = {str(self.root / segment["checkpoint"]) for segment in self.segments}
        with patch.object(chain, "_file_sha256", wraps=original) as hashed:
            for attempt in (1, 2):
                with self.assertLogs(chain._LOG, level="INFO") as logged:
                    _, state, status = self.start()
                counts = Counter(str(call.args[0]) for call in hashed.call_args_list
                                 if str(call.args[0]) in self.artifacts)
                self.assertEqual(counts, Counter({path: attempt for path in checkpoints}))
                self.assertEqual(state["index"], 3)
                self.assertEqual(len(state["segments"]), 2)
                self.assertIn("resumed from clip 2", status)
                json.dumps({key: value for key, value in state.items()
                            if key not in ("previous_frames", "previous_latent")})
                self.assertTrue(any("reused 2 unchanged file hashes" in line for line in logged.output))
                self.assertTrue(any("context checkpoint for scene 2" in line for line in logged.output))
                self.assertTrue(any("Full media verification is deferred" in line for line in logged.output))

    def between_passes(self, change):
        original = chain._initial_state

        def restore(*args, **kwargs):
            change()
            return original(*args, **kwargs)

        return patch.object(chain, "_initial_state", new=restore)

    def test_modified_artifacts_between_preflight_and_restore_are_rejected(self):
        for key in ("checkpoint",):
            path = self.root / self.segments[0][key]
            original = path.read_bytes()
            for verify_history in (True, False):
                with self.subTest(artifact=key, verify_history=verify_history):
                    try:
                        with self.between_passes(lambda: path.write_bytes(original + b"corrupt")):
                            with self.assertRaisesRegex(ValueError, "SHA-256"):
                                self.start(verify_resume_history=verify_history)
                    finally:
                        path.write_bytes(original)

    def test_missing_artifact_after_preflight_is_rejected(self):
        path = self.root / self.segments[0]["checkpoint"]
        with self.between_passes(path.unlink):
            with self.assertRaisesRegex(FileNotFoundError, "checkpoint is missing"):
                self.start()

    def test_metadata_hash_changed_between_passes_is_not_trusted(self):
        path = self.root / self.segments[0]["metadata"]

        def change():
            data = json.loads(path.read_text())
            data["segment"]["checkpoint_sha256"] = "0" * 64
            path.write_text(json.dumps(data))

        with self.between_passes(change):
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                self.start()

    def test_replaced_file_with_same_size_and_mtime_is_rehashed(self):
        path = self.root / self.segments[0]["checkpoint"]
        saved_stat = path.stat()

        def change():
            replacement = path.with_suffix(".replacement")
            replacement.write_bytes(b"x" * saved_stat.st_size)
            os.utime(replacement, ns=(saved_stat.st_atime_ns, saved_stat.st_mtime_ns))
            os.replace(replacement, path)

        with self.between_passes(change):
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                self.start()

    def test_in_place_change_with_restored_mtime_is_rehashed(self):
        path = self.root / self.segments[0]["checkpoint"]
        saved_stat = path.stat()

        def change():
            path.write_bytes(b"x" * saved_stat.st_size)
            os.utime(path, ns=(saved_stat.st_atime_ns, saved_stat.st_mtime_ns))
            self.assertNotEqual(path.stat().st_ctime_ns, saved_stat.st_ctime_ns)

        with self.between_passes(change):
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                self.start()

    def test_valid_replacement_is_rehashed_without_rejecting_resume(self):
        path = self.root / self.segments[0]["checkpoint"]
        original = path.read_bytes()

        def change():
            replacement = path.with_suffix(".replacement")
            replacement.write_bytes(original)
            os.replace(replacement, path)

        with patch.object(chain, "_file_sha256", wraps=chain._file_sha256) as hashed:
            with self.between_passes(change):
                self.start()
        self.assertEqual(sum(str(call.args[0]) == str(path) for call in hashed.call_args_list), 2)

    def test_change_during_hash_is_rejected_and_not_cached(self):
        verifier = chain._ResumeArtifactVerification()
        path = self.root / self.segments[0]["checkpoint"]
        original = chain._file_sha256

        def change_while_hashing(value, **kwargs):
            digest = original(value, **kwargs)
            path.write_bytes(b"changed during verification")
            return digest

        with patch.object(chain, "_file_sha256", new=change_while_hashing):
            with self.assertRaisesRegex(ValueError, "changed during integrity verification"):
                verifier.sha256(path)
        self.assertEqual(verifier.hashed_files, 0)
        self.assertEqual(verifier.sha256(path), original(path))
        self.assertEqual((verifier.hashed_files, verifier.reused_files), (1, 0))

    def test_read_error_does_not_create_a_cached_success(self):
        verifier = chain._ResumeArtifactVerification()
        path = self.root / self.segments[0]["checkpoint"]
        with patch.object(chain, "_file_sha256", side_effect=OSError(errno.EIO, "test read error")):
            with self.assertRaises(OSError):
                verifier.sha256(path)
        with patch.object(chain, "_file_sha256", wraps=chain._file_sha256) as hashed:
            verifier.sha256(path)
            self.assertEqual(hashed.call_count, 1)

    def test_standalone_verification_remains_uncached(self):
        with patch.object(chain, "_file_sha256", wraps=chain._file_sha256) as hashed:
            for _ in range(2):
                chain._verify_segment_artifacts(self.segments[0], 1)
        self.assertEqual(hashed.call_count, 10)

    def test_history_metadata_changed_between_passes_still_blocks_resume(self):
        path = self.root / self.segments[1]["metadata"]

        def change():
            data = json.loads(path.read_text())
            data["scene_dependency"]["scopes"]["scene_generation"]["seed"] += 1
            path.write_text(json.dumps(data))

        consumed = {"visual": 2, "audio": 2, "scenes": [2]}
        with patch.object(chain, "_resume_context_predecessors", return_value=consumed):
            with self.between_passes(change):
                with self.assertRaisesRegex(ValueError, "seed"):
                    self.start()

    def test_tensor_restore_and_shape_checks_still_run_after_hash_reuse(self):
        torch = chain.torch
        tensors = {"context_frames": torch.zeros(5, 2, 2, 3),
                   "video": torch.arange(48.).reshape(1, 24, 2, 1, 1),
                   "audio": torch.arange(640.).reshape(1, 32, 2, 10)}
        checkpoint = self.root / self.segments[1]["checkpoint"]
        consumed = {"visual": 2, "audio": 2, "scenes": [2]}
        self.plan["compatibility"]["context_storage_length"] = 5
        for invalid in (False, True):
            with self.subTest(invalid_shape=invalid):
                payload = dict(tensors)
                if invalid:
                    payload["context_frames"] = torch.zeros(1)  # Invalid RGB rank.
                chain._st_save(payload, str(checkpoint))
                for key in ("metadata", "revision_metadata"):
                    path = self.root / self.segments[1][key]
                    data = json.loads(path.read_text())
                    data["segment"]["checkpoint_sha256"] = fixture.digest(checkpoint)
                    path.write_text(json.dumps(data))
                with patch.object(chain, "_resume_context_predecessors", return_value=consumed), \
                        patch.object(chain, "_streams_from_latent", new=lambda value: value["samples"]), \
                        patch.object(chain, "_st_load", wraps=chain._st_load) as load:
                    if invalid:
                        with self.assertRaisesRegex(ValueError, "invalid context"):
                            self.start()
                    else:
                        _, state, _ = self.start()
                        torch.testing.assert_close(state["previous_frames"], tensors["context_frames"])
                        for actual, name in zip(state["previous_latent"]["samples"], ("video", "audio")):
                            torch.testing.assert_close(actual, tensors[name])
                    load.assert_called_once_with(str(checkpoint))

    def test_legacy_normalized_prompt_hash_still_validates(self):
        segment = copy.deepcopy(self.segments[0])
        path = self.root / segment["prompt_file"]
        path.write_bytes(b"first\r\nsecond\r\n")
        segment.pop("prompt_file_sha256")
        segment["prompt_hash"] = hashlib.sha256(b"first\nsecond\n").hexdigest()
        verifier = chain._ResumeArtifactVerification()
        for _ in range(2):
            chain._verify_segment_artifacts(segment, 1, artifact_verification=verifier)
        path.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "prompt sidecar"):
            chain._verify_segment_artifacts(segment, 1, artifact_verification=verifier)

    def test_cut_does_not_read_any_saved_payload(self):
        self.plan["shots"][2]["context_length"] = 0
        with patch.object(chain, "_file_sha256", wraps=chain._file_sha256) as hashed, \
                patch.object(chain, "_st_load", wraps=chain._st_load) as loaded:
            _, state, _ = self.start()
        self.assertEqual(hashed.call_count, 0)
        self.assertEqual(loaded.call_count, 0)
        self.assertIsNone(state["previous_latent"])
        self.assertEqual(len(state["segments"]), 2)

    def test_linear_continuation_skips_unrelated_checkpoint(self):
        self.plan["shots"][2]["visual_context_source"] = "previous"
        unrelated = self.root / self.segments[0]["checkpoint"]
        unrelated.write_bytes(b"corrupt but unused for this scene")
        with patch.object(chain, "_file_sha256", wraps=chain._file_sha256) as hashed:
            self.start()
        self.assertEqual([str(call.args[0]) for call in hashed.call_args_list],
                         [str(self.root / self.segments[1]["checkpoint"])])
        # Full manifest recovery still rejects the same corruption.
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            chain._load_resume_state(self.plan, 3)

    def test_unused_media_corruption_is_deferred_to_assembly(self):
        for key in ("segment", "blend_segment", "generated_audio", "prompt_file"):
            path = self.root / self.segments[0][key]
            original = path.read_bytes()
            with self.subTest(artifact=key):
                try:
                    with self.between_passes(lambda: path.write_bytes(b"corrupt media")):
                        _, state, _ = self.start()
                    manifest = chain._manifest_from_segments(
                        self.plan, state["segments"], complete=False)
                    with self.assertRaisesRegex(ValueError, "SHA-256"):
                        chain._validate_manifest(manifest)
                finally:
                    path.write_bytes(original)

    def test_missing_unused_artifacts_still_block_resume(self):
        self.plan["shots"][2]["context_length"] = 0
        for key in ("segment", "checkpoint", "blend_segment", "generated_audio", "prompt_file"):
            path = self.root / self.segments[0][key]
            original = path.read_bytes()
            with self.subTest(artifact=key):
                try:
                    path.unlink()
                    with self.assertRaisesRegex(ValueError, "missing"):
                        self.start()
                finally:
                    path.write_bytes(original)

    def test_standalone_preflight_is_context_scoped_and_reports_scope(self):
        self.plan["shots"][2]["visual_context_source"] = "previous"
        with patch.object(chain, "_file_sha256", wraps=chain._file_sha256) as hashed:
            _, report = chain._preflight_chain(self.plan, start_clip=3)
        self.assertTrue(report["ok"], report)
        self.assertEqual(hashed.call_count, 1)
        self.assertEqual(report["resume"]["checkpoint_sources"], [2])
        self.assertEqual(report["resume"]["artifact_verification"], "context_checkpoints_only")
        self.assertEqual([item["hashed_artifacts"] for item in report["resume"]["predecessors"]],
                         [[], ["checkpoint"]])

    def test_non_linear_sources_include_visual_blocks_audio_lead_and_bootstrap(self):
        plan = copy.deepcopy(self.plan)
        template = plan["shots"][0]
        plan["shots"] = [{**template, "id": "scene_%d" % i} for i in range(1, 15)]
        shot = plan["shots"][-1]
        shot.update(context_length=22, continuation_mode="guide",
                    generated_continuity="on", source_audio_target="off",
                    audio_context_length=22, audio_context_unlocked=True,
                    visual_context_blocks=[{"source": 3, "frames": 5},
                                           {"source": 8, "frames": 17}],
                    audio_context_source=9, audio_context_lead_source=6,
                    audio_context_lead_frames=5)
        sources = chain._resume_context_predecessors(plan, 14)
        self.assertEqual(sources["scenes"], [3, 6, 8, 9])
        self.assertEqual(chain._resume_checkpoint_sources(sources, 14), {3, 6, 8, 9, 13})

    @contextmanager
    def interrupt_hook(self, hook):
        # Matches ComfyUI's native BaseException, which must escape preflight's
        # ordinary validation-error aggregation without resetting/swallowing it.
        class InterruptProcessingException(BaseException):
            pass
        comfy = types.ModuleType("comfy")
        management = types.ModuleType("comfy.model_management")
        management.InterruptProcessingException = InterruptProcessingException
        management.throw_exception_if_processing_interrupted = lambda: hook(InterruptProcessingException)
        comfy.model_management = management
        with patch.dict(sys.modules, {"comfy": comfy, "comfy.model_management": management}):
            yield InterruptProcessingException

    def test_cancel_already_requested_stops_before_preflight(self):
        def cancel(exception):
            raise exception()
        with self.interrupt_hook(cancel) as exception, \
                patch.object(chain, "_preflight_chain") as preflight:
            with self.assertRaises(exception):
                self.start()
        preflight.assert_not_called()

    def test_cancel_between_scenes_escapes_preflight(self):
        cancelled = False
        visited = []
        original = chain._verify_segment_artifacts
        def verify(segment, index, **kwargs):
            nonlocal cancelled
            visited.append(index)
            original(segment, index, **kwargs)
            cancelled = True
        def check(exception):
            if cancelled:
                raise exception()
        with self.interrupt_hook(check) as exception, \
                patch.object(chain, "_verify_segment_artifacts", new=verify):
            with self.assertRaises(exception):
                chain._preflight_chain(self.plan, start_clip=3)
        self.assertEqual(visited, [1])

    def test_cancel_during_file_read_closes_file_and_does_not_cache(self):
        path = self.root / self.segments[0]["checkpoint"]
        path.write_bytes(b"x" * (4 * 1024 * 1024))
        verifier = chain._ResumeArtifactVerification()
        handle = path.open("rb")
        checks = 0
        def check(exception):
            nonlocal checks
            checks += 1
            if handle.tell() >= 1024 * 1024:
                raise exception()
        with self.interrupt_hook(check) as exception, \
                patch("builtins.open", return_value=handle):
            with self.assertRaises(exception):
                verifier.sha256(path)
        self.assertTrue(handle.closed)
        self.assertEqual(verifier.hashed_files, 0)
        self.assertEqual(verifier._hashes, {})
        self.assertEqual(checks, 3)  # entry, first block, stop before second block
        self.assertEqual(verifier.sha256(path), fixture.digest(path))

    def test_cancel_on_cached_hash_still_propagates(self):
        path = self.root / self.segments[0]["checkpoint"]
        verifier = chain._ResumeArtifactVerification()
        verifier.sha256(path)
        def cancel(exception):
            raise exception()
        with self.interrupt_hook(cancel) as exception:
            with self.assertRaises(exception):
                verifier.sha256(path)
        self.assertEqual(verifier.reused_files, 0)

    def test_cancel_before_state_restore_does_not_load_context(self):
        cancelled = False
        def cancel_later():
            nonlocal cancelled
            cancelled = True
        def check(exception):
            if cancelled:
                raise exception()
        with self.interrupt_hook(check) as exception, \
                self.between_passes(cancel_later), patch.object(chain, "_st_load") as load:
            with self.assertRaises(exception):
                self.start()
        load.assert_not_called()

    def test_audio_only_continuation_hashes_its_source(self):
        self.plan["shots"][2].update(
            context_length=0, audio_context_length=5, generated_continuity="on")
        self.assertEqual(chain._resume_context_predecessors(self.plan, 3)["scenes"], [2])
        with patch.object(chain, "_file_sha256", wraps=chain._file_sha256) as hashed:
            self.start()
        self.assertEqual([str(call.args[0]) for call in hashed.call_args_list],
                         [str(self.root / self.segments[1]["checkpoint"])])

    def test_scene_fourteen_hashes_only_thirteen_not_the_entire_run(self):
        policy = chain._contract_compose_chain_policy(
            chain._contract_audio_policy("generated", "off", "off"),
            chain._contract_transition_policy("cut"), audio_context_length=0)
        self.plan = chain._normalize_plan(
            json.dumps({"shots": [{"id": "scene_%d" % i, "prompt": "Test scene.", "length": 39}
                                  for i in range(1, 15)]}),
            self.run.name, 64, 64, 1, "video", "head", "disabled",
            "generated_audio", 0, 2.0, 8, 7, 18, "test-stack", 0, "guide", policy)
        self.plan["shots"][13].update(context_length=5, continuation_mode="guide")
        self.plan["compatibility"]["context_storage_length"] = 5
        tensors = {key: value.clone() for key, value in chain._st_load(
            str(self.root / self.segments[1]["checkpoint"])).items()}
        checkpoints = []
        for index in range(1, 14):
            metadata, _ = fixture.write_revision(
                self.run, index, "%032x" % index, index, active=True,
                run_name=self.run.name, context_length=0, audio_context_length=0,
                generated_continuity="off", compatibility=self.plan["compatibility"])
            segment = metadata["segment"]
            checkpoint = self.root / segment["checkpoint"]
            chain._st_save(tensors, str(checkpoint))
            segment["checkpoint_sha256"] = fixture.digest(checkpoint)
            metadata["history_hash"] = segment["history_hash"] = chain._history_hash(self.plan, index)
            metadata["scene_dependency"] = chain._scene_dependency_record(self.plan, index)
            for key in ("metadata", "revision_metadata"):
                (self.root / segment[key]).write_text(json.dumps(metadata))
            checkpoints.append(str(checkpoint))
        with patch.object(chain, "_file_sha256", wraps=chain._file_sha256) as hashed, \
                patch.object(chain, "_st_load", wraps=chain._st_load) as loaded:
            _, state, _ = chain.MiniMaxH3ChainLoopStart().start(self.plan, 14)
        self.assertEqual([str(call.args[0]) for call in hashed.call_args_list], [checkpoints[-1]])
        loaded.assert_called_once_with(checkpoints[-1])
        self.assertEqual(len(state["segments"]), 13)

    def test_inconsistent_unrelated_metadata_still_blocks(self):
        self.plan["shots"][2]["context_length"] = 0
        path = self.root / self.segments[0]["metadata"]
        data = json.loads(path.read_text())
        data["segment"]["history_hash"] = "wrong"
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "inconsistent history"):
            self.start()


if __name__ == "__main__":
    unittest.main()
