"""Layout/conversion contracts without ComfyUI, GPU or any live project data."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chain_layout as layout
from chain_layout_conversion import preview, convert_copy
from branch_scope import working_directory, branch_scope
from checkpoint_manager import CheckpointGraphManager
from working_branches import WorkingBranches
from checkpoint_variants import saved_checkpoint_variants
from storage_fixture import composite_project, snapshot, RUN, BASE, ALT, TIP, BRANCH, SEED


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "old"
        self.copy_output = Path(self.temp.name) / "copy"
        layout.forget_layout()

    def test_read_never_creates_or_scans(self):
        root = self.output / "h3_chains/new"
        with patch("os.walk", side_effect=AssertionError("no scan")), \
                patch("os.scandir", side_effect=AssertionError("no scan")):
            self.assertEqual(layout.state_root(root), str(root))
            self.assertFalse(root.exists())
            layout.create_project(root)
            self.assertEqual(layout.state_root(root), str(root / ".h3"))
            with patch.object(Path, "open", side_effect=AssertionError("cached marker")):
                for _ in range(1000):
                    self.assertEqual(layout.resolve_path(root / "segments/clip_0001.mp4"),
                                     str(root / "generation/clips/clip_0001.mp4"))
        self.assertEqual({p.relative_to(root).as_posix() for p in root.rglob("*")},
                         {".h3", ".h3/layout.json"})

    def test_existing_empty_project_is_legacy(self):
        root = self.output / "h3_chains/old"
        root.mkdir(parents=True)
        layout.create_project(root)
        self.assertEqual(layout.state_root(root), str(root))
        self.assertEqual(list(root.iterdir()), [])

    def test_copy_preserves_every_byte_and_historical_address(self):
        root = composite_project(self.output)
        before = snapshot(root)
        moves = preview(root)
        self.assertEqual(snapshot(root), before)
        report = convert_copy(root, self.copy_output)
        target = Path(report["destination"])
        self.assertTrue(report["verified"])
        self.assertFalse(report["activated"])
        self.assertEqual(snapshot(root), before)
        for move in moves:
            physical = target / move.destination
            self.assertEqual(physical.read_bytes(), (root / move.source).read_bytes())
            for address in (str(root / move.source), "h3_chains/" + RUN + "/" + move.source.as_posix(),
                            ("h3_chains/" + RUN + "/" + move.source.as_posix()).replace("/", "\\")):
                self.assertEqual(layout.output_path(self.copy_output, address), str(physical))
        self.assertTrue((target / "generation/clips").is_dir())
        self.assertTrue((target / "processing/original__01_first/hq/clips").is_dir())
        self.assertTrue((target / "exports/frames/original/generation/export_2/frame_00000001.png").is_file())
        self.assertFalse((target / "segments").exists())

    def test_copied_branches_checkpoints_alt_and_processing(self):
        root = composite_project(self.output)
        convert_copy(root, self.copy_output)
        target = self.copy_output / "h3_chains" / RUN
        before = snapshot(target)
        manager = CheckpointGraphManager(str(self.copy_output))
        self.assertEqual(manager.active_selection(RUN)[0], {1: BASE, 2: TIP})
        with branch_scope(RUN, BRANCH):
            self.assertEqual(manager.active_selection(RUN)[0], {1: BASE})
            self.assertEqual(working_directory(target, RUN), str(target / ".h3/branches" / BRANCH))
        store = WorkingBranches(str(self.copy_output), RUN)
        self.assertEqual(store._load_record(BRANCH)["authoring"]["plan"]["seed"], SEED)
        graph = manager.graph(RUN, adopt_legacy=False)
        self.assertTrue(graph)
        original = json.loads((target / ".h3/checkpoints" / ("clip_0001." + ALT + ".json")).read_text())["segment"]
        variants = saved_checkpoint_variants(self.copy_output, RUN, [
            {"scene": 1, "revision": ALT, "checkpoint_sha256": original["checkpoint_sha256"]}])
        self.assertEqual(variants["warnings"], [])
        self.assertEqual(len(variants["variants"]), 1)
        self.assertEqual(snapshot(target), before)

    def test_reject_existing_target_and_links(self):
        root = composite_project(self.output)
        report = convert_copy(root, self.copy_output)
        with self.assertRaises(ValueError):
            convert_copy(root, self.copy_output)
        self.assertTrue(Path(report["destination"]).is_dir())
        (root / "link").symlink_to(root / "segments", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "links"):
            preview(root)

    def test_converted_deletion_keeps_context_dependencies_and_original_copy(self):
        from _processing_checkpoint_delete_unit_test import DeleteTests, module
        fixture = DeleteTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        first = fixture.save()
        last = fixture.save(revision="b" * 32, scene=2, prefix=[first])
        source = fixture.root / "h3_chains/demo"
        before = snapshot(source)
        convert_copy(source, self.copy_output)
        manager = module.ProcessingCheckpointManager(self.copy_output)
        self.assertFalse(manager.deletion_preview("demo", first["revision_metadata"])["allowed"])
        previewed = manager.deletion_preview("demo", last["revision_metadata"])
        self.assertTrue(previewed["allowed"])
        manager.delete("demo", last["revision_metadata"], previewed["snapshot"])
        self.assertTrue(Path(layout.output_path(self.copy_output, first["checkpoint"])).is_file())
        self.assertFalse(Path(layout.output_path(self.copy_output, last["checkpoint"])).exists())
        self.assertEqual(snapshot(source), before)

    def test_failed_copy_never_installs_a_marker_or_changes_source(self):
        root = composite_project(self.output)
        before = snapshot(root)
        with patch("chain_layout_conversion.shutil.copy2", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                convert_copy(root, self.copy_output)
        self.assertEqual(snapshot(root), before)
        self.assertFalse((self.copy_output / "h3_chains" / RUN / ".h3/layout.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
