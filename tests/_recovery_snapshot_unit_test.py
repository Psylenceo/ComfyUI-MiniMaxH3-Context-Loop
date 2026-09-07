"""Stable backport: exact per-take recovery with transactional publication."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from _upscale_chain_unit_test import load_package, folder_paths, torch, av_latent, audio_for_frames

package, chain, _ = load_package()


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        folder_paths.output_directory = temporary.name
        plan = chain.MiniMaxH3ChainPlan().build(
            json.dumps({"shots": [{"id": "one", "prompt": "Original scene.",
                                  "length": 5, "steps": 2, "seed": "7"}]}),
            "snapshots", "unit-test", 32, 32, 1, "video", "head", "disabled",
            "generated_audio", 1, 5 / 24, 2, 7, 18, 0, "guide")[0]
        self.plan = chain._plan_with_source_audio(chain._plan_with_external_context(plan, None), None)
        self.run = self.root / "h3_chains/snapshots"

    def save(self, plan=None):
        plan = plan or self.plan
        return chain.MiniMaxH3ChainSegmentSave().save(
            chain._initial_state(plan, 1), torch.zeros(5, 32, 32, 3),
            av_latent(0.1), audio_for_frames(5), denoised_latent=av_latent(0.2),
            prompt={"refs": {"class_type": "MiniMaxH3TaggedReferenceToVideo",
                             "inputs": {"ref_image_size": "max"}}},
            extra_pnginfo={"workflow": {"nodes": []}})["result"][0]

    def files(self):
        return {str(p.relative_to(self.run)): p.read_bytes()
                for p in self.run.rglob("*") if p.is_file()}

    def test_each_save_has_its_own_immutable_snapshot_and_legacy_root_copy(self):
        first = self.save()
        archives = {k: self.root / v for k, v in first["archives"].items()}
        before = {k: p.read_bytes() for k, p in archives.items()}
        second = self.save()
        self.assertNotEqual(first["archives"], second["archives"])
        self.assertEqual(before, {k: p.read_bytes() for k, p in archives.items()})
        for key, path in archives.items():
            self.assertEqual(path.parent.name, first["revision"])
            self.assertEqual((self.run / path.name).read_bytes(), (self.root / second["archives"][key]).read_bytes())
        # A later Run-level edit must not replace the historical output Plan.
        (self.run / "plan.json").write_text('{"shots": []}')
        manifest = chain._checkpoint_selection_manifest({
            "run_name": "snapshots", "output_mode": "workflow_local",
            "lineage": [{"scene": 1, "revision": first["revision"]}]})
        self.assertEqual(manifest["archives"], first["archives"])

    def test_alternate_snapshot_does_not_overwrite_active_root(self):
        base = self.save()
        before = {p.name: p.read_bytes() for p in self.run.glob("*.json")}
        plan = chain._alternate_take_plan(self.plan, {"alternate_draft": {
            "enabled": True, "scene": 1, "scene_id": "one", "base_revision": base["revision"],
            "prompt": "Alternate scene.", "seed": 91}})
        alternate = self.save(plan)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.run.glob("*.json")})
        archive = self.root / alternate["archives"]["plan"]
        self.assertEqual(archive.parent.name, alternate["revision"])
        self.assertEqual(json.loads(archive.read_text())["shots"][0]["prompt"], "Alternate scene.")

    def test_failed_checkpoint_commit_removes_only_new_artifacts_and_snapshot(self):
        self.save()
        before = self.files()
        original = chain._atomic_json

        def fail_pointer(path, value):
            if Path(path) == self.run / "checkpoints/clip_0001.json":
                raise OSError("injected pointer failure")
            return original(path, value)

        with patch.object(chain, "_atomic_json", side_effect=fail_pointer):
            with self.assertRaisesRegex(OSError, "pointer failure"):
                self.save()
        self.assertEqual(before, self.files())

    def test_advisory_refresh_failures_keep_committed_take(self):
        self.save()
        root_before = (self.run / "plan.json").read_bytes()
        with patch.object(chain, "_promote_run_archive_snapshot", side_effect=OSError("root failure")), \
                patch.object(chain, "_editorial_after_base_revision_change", side_effect=OSError("editorial failure")):
            segment = self.save()
        metadata = chain._read_json(str(self.root / segment["metadata"]))
        self.assertEqual(metadata["segment"]["revision"], segment["revision"])
        self.assertEqual(root_before, (self.run / "plan.json").read_bytes())
        chain._verify_segment_artifacts(segment, 1)
        self.assertEqual(chain._checkpoint_run_archives(self.plan, metadata), segment["archives"])

    def test_existing_snapshot_cannot_be_overwritten(self):
        segment = self.save()
        before = self.files()
        with self.assertRaises(FileExistsError):
            chain._write_run_archives(self.plan, revision=segment["revision"])
        self.assertEqual(before, self.files())

    def test_root_only_legacy_descriptors_are_still_readable(self):
        self.save()
        legacy = {"segment": {"revision": "a" * 32}, "archives": {
            key: chain._relative_output_path(path)
            for key, path in chain._run_archive_paths(self.plan).items()}}
        self.assertEqual(chain._checkpoint_run_archives(self.plan, legacy), {})


if __name__ == "__main__":
    unittest.main(argv=[__file__])
