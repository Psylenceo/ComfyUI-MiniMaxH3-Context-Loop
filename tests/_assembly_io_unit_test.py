#!/usr/bin/env python3
"""Assembly I/O regressions using tiny synthetic files, never live projects."""
from collections import Counter, OrderedDict
from contextlib import contextmanager
import errno
import importlib
import os
import threading
import unittest
from unittest.mock import patch

import _resume_verification_unit_test as fixture

chain = fixture.chain


class AssemblyIOTests(unittest.TestCase):
    def setUp(self):
        fixture.ResumeVerificationTests.setUp(self)
        cached = patch.object(chain._AssemblyArtifactVerification, "_cache", OrderedDict())
        cached.start()
        self.addCleanup(cached.stop)

    def manifest(self, count=2):
        return chain._manifest_from_segments(
            self.plan, self.segments[:count], complete=False)

    def test_growing_exports_only_hash_new_files(self):
        original = chain._file_sha256
        with patch.object(chain, "_file_sha256", wraps=original) as hashed:
            chain._validate_manifest(self.manifest(1))
            self.assertEqual(hashed.call_count, 5)
            chain._validate_manifest(self.manifest())
            self.assertEqual(hashed.call_count, 10)
            chain._validate_manifest(self.manifest())
            self.assertEqual(hashed.call_count, 10)
        self.assertEqual(set(str(call.args[0]) for call in hashed.call_args_list),
                         self.artifacts)

    def test_changed_artifacts_are_rehashed_even_with_restored_mtime(self):
        for key in ("segment", "checkpoint", "blend_segment", "generated_audio", "prompt_file"):
            with self.subTest(artifact=key):
                chain._validate_manifest(self.manifest())
                path = self.root / self.segments[0][key]
                original = path.read_bytes()
                stamp = path.stat()
                try:
                    path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
                    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                    with patch.object(chain, "_file_sha256", wraps=chain._file_sha256) as hashed:
                        with self.assertRaisesRegex(ValueError, "SHA-256"):
                            chain._validate_manifest(self.manifest())
                    self.assertIn(str(path), [str(call.args[0]) for call in hashed.call_args_list])
                finally:
                    path.write_bytes(original)

    def test_atomic_replacement_with_same_size_and_mtime_is_rehashed(self):
        chain._validate_manifest(self.manifest())
        path = self.root / self.segments[0]["checkpoint"]
        original, stamp = path.read_bytes(), path.stat()
        replacement = path.with_suffix(".replacement")
        replacement.write_bytes(b"x" * len(original))
        os.utime(replacement, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        os.replace(replacement, path)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            chain._validate_manifest(self.manifest())

    def test_deleted_cached_artifacts_still_fail(self):
        chain._validate_manifest(self.manifest())
        (self.root / self.segments[0]["checkpoint"]).unlink()
        with self.assertRaisesRegex(FileNotFoundError, "missing"):
            chain._validate_manifest(self.manifest())

    def test_changed_expected_hash_is_not_trusted(self):
        chain._validate_manifest(self.manifest())
        manifest = self.manifest()
        manifest["segments"][0]["checkpoint_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            chain._validate_manifest(manifest)

    def test_changed_manifest_duration_is_still_rejected(self):
        chain._validate_manifest(self.manifest())
        manifest = self.manifest()
        manifest["total_delivered_frames"] += 1
        with self.assertRaisesRegex(ValueError, "durations total"):
            chain._validate_manifest(manifest)

    def test_prelude_reuses_hashes_but_rejects_changed_files(self):
        segment = self.segments[0]
        manifest = {
            "compatibility": {"width": 64, "height": 64},
            "prelude": {
                "prepend": True, "frame_count": 5, "fps": 24,
                "width": 64, "height": 64,
                "video": segment["segment"],
                "video_sha256": segment["segment_sha256"],
                "audio": segment["checkpoint"],
                "audio_sha256": segment["checkpoint_sha256"],
            },
        }
        with patch.object(chain, "_file_sha256", wraps=chain._file_sha256) as hashed:
            chain._validate_prelude(manifest)
            chain._validate_prelude(manifest)
            self.assertEqual(hashed.call_count, 2)
        (self.root / segment["checkpoint"]).write_bytes(b"corrupt audio")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            chain._validate_prelude(manifest)

    def test_hash_read_error_is_not_cached(self):
        path = self.root / self.segments[0]["checkpoint"]
        verifier = chain._AssemblyArtifactVerification()
        with patch.object(chain, "_file_sha256", side_effect=OSError(errno.EIO, "test")):
            with self.assertRaises(OSError):
                verifier.sha256(path)
        with patch.object(chain, "_file_sha256", wraps=chain._file_sha256) as hashed:
            verifier.sha256(path)
            self.assertEqual(hashed.call_count, 1)

    def test_change_during_hash_is_rejected_and_not_cached(self):
        path = self.root / self.segments[0]["checkpoint"]
        verifier = chain._AssemblyArtifactVerification()
        original = chain._file_sha256

        def changed(value, **kwargs):
            digest = original(value, **kwargs)
            path.write_bytes(b"changed during hash")
            return digest

        with patch.object(chain, "_file_sha256", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "changed during integrity"):
                verifier.sha256(path)
        self.assertEqual(verifier.hashed_files, 0)
        with patch.object(chain, "_file_sha256", wraps=original) as hashed:
            self.assertEqual(verifier.sha256(path), original(path))
            self.assertEqual(hashed.call_count, 1)

    def test_cached_checks_still_honor_interruption(self):
        verifier = chain._AssemblyArtifactVerification()
        path = self.root / self.segments[0]["checkpoint"]
        verifier.sha256(path)
        with patch.object(chain, "_throw_if_review_interrupted", side_effect=RuntimeError("stop")):
            with self.assertRaisesRegex(RuntimeError, "stop"):
                verifier.sha256(path)

    def test_cache_is_bounded_and_evicted_entries_are_rechecked(self):
        paths = sorted(self.artifacts)[:3]
        with patch.object(chain._AssemblyArtifactVerification, "_cache_limit", 2):
            for path in paths:
                chain._AssemblyArtifactVerification().sha256(path)
            self.assertEqual(len(chain._AssemblyArtifactVerification._cache), 2)
            with patch.object(chain, "_file_sha256", wraps=chain._file_sha256) as hashed:
                chain._AssemblyArtifactVerification().sha256(paths[0])
                self.assertEqual(hashed.call_count, 1)

    def test_cache_lock_does_not_serialize_independent_disk_reads(self):
        paths = sorted(self.artifacts)[:2]
        original = chain._file_sha256
        barrier = threading.Barrier(2)
        errors = []

        def hashing(path, **kwargs):
            barrier.wait(timeout=3)
            return original(path, **kwargs)

        def verify(path):
            try:
                chain._AssemblyArtifactVerification().sha256(path)
            except BaseException as exc:
                errors.append(exc)

        with patch.object(chain, "_file_sha256", side_effect=hashing):
            workers = [threading.Thread(target=verify, args=(path,)) for path in paths]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=5)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])

    def test_resume_and_standalone_checks_do_not_trust_assembly_cache(self):
        chain._validate_manifest(self.manifest())
        with patch.object(chain, "_file_sha256", wraps=chain._file_sha256) as hashed:
            chain._verify_segment_artifacts(self.segments[0], 1)
            verifier = chain._ResumeArtifactVerification()
            verifier.sha256(self.root / self.segments[0]["checkpoint"])
        self.assertEqual(hashed.call_count, 6)

    def test_upscale_validation_and_assembly_share_verified_hashes(self):
        upscale = importlib.import_module(chain.__package__ + ".upscale_nodes")
        source = self.manifest()
        manifest = {
            "format": "h3_chain_upscale_manifest_v1",
            "clip_count": 2, "segments": self.segments,
            "source_manifest": source,
            "total_delivered_frames": source["total_delivered_frames"],
        }
        with patch.object(chain, "_file_sha256", wraps=chain._file_sha256) as hashed:
            upscale._validate_upscale_manifest(manifest)
            self.assertEqual(hashed.call_count, 6)
            chain._validate_manifest(source)
            self.assertEqual(hashed.call_count, 10)
            upscale._validate_upscale_manifest(manifest)
            self.assertEqual(hashed.call_count, 10)
        (self.root / self.segments[0]["checkpoint"]).write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            upscale._validate_upscale_manifest(manifest)

    def test_selective_audio_loading_preserves_exact_overlap_and_skips_visuals(self):
        torch = chain.torch
        first = torch.ones(1, 2, 100)
        delivered = torch.full((1, 2, 61), 3.0)
        overlap = torch.cat((torch.full((1, 2, 39), 2.0), delivered), dim=-1)
        checkpoints = [self.root / item["checkpoint"] for item in self.segments]
        for path, pcm in zip(checkpoints, (
                {"delivered_audio": first},
                {"delivered_audio": delivered, "audio_with_overlap": overlap})):
            chain._st_save({**pcm, "video": torch.zeros(1, 24, 2, 2, 2),
                            "context_frames": torch.zeros(5, 2, 2, 3),
                            "audio": torch.zeros(1, 32, 2, 9)}, str(path))
        manifest = {"segments": [
            {"index": 1, "checkpoint": str(checkpoints[0]), "sample_rate": 24,
             "raw_frames": 100, "delivered_frames": 100, "continuation_mode": "guide"},
            {"index": 2, "checkpoint": str(checkpoints[1]), "sample_rate": 24,
             "raw_frames": 100, "delivered_frames": 61,
             "continuation_mode": "audio_feathered_av"},
        ]}
        reads = []
        original = chain._st_safe_open

        @contextmanager
        def tracked(*args, **kwargs):
            with original(*args, **kwargs) as saved:
                class AudioOnly:
                    def keys(self):
                        return saved.keys()

                    def get_tensor(self, key):
                        self_test.assertIn(key, ("delivered_audio", "audio_with_overlap"))
                        reads.append(key)
                        return saved.get_tensor(key)
                yield AudioOnly()

        self_test = self
        with patch.object(chain, "_st_safe_open", side_effect=tracked), \
                patch.object(chain, "_st_load", side_effect=AssertionError("whole checkpoint loaded")):
            result = chain._generated_audio(manifest)
        expected = torch.cat((first[..., :61], overlap), dim=-1)
        self.assertTrue(torch.equal(result["waveform"], expected))
        self.assertEqual(Counter(reads), Counter(delivered_audio=2, audio_with_overlap=1))
        loaded = chain._load_checkpoint_audio(str(checkpoints[1]))
        self.assertTrue(torch.equal(loaded["delivered_audio"], delivered))
        # PCM clones are not views retaining the checkpoint's mapped storage.
        self.assertIsNone(loaded["delivered_audio"]._base)

    def test_missing_pcm_fails_without_loading_video(self):
        with self.assertRaisesRegex(ValueError, "has no delivered audio"):
            chain._generated_audio(self.manifest())


if __name__ == "__main__":
    unittest.main()
