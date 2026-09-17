"""Boundary switch, disk-cache resume, editorial joins and exact replacement (CPU)."""
from contextlib import contextmanager
from fractions import Fraction
import copy
import importlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import torch

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("boundary_test")
pkg.__path__ = [str(ROOT)]
sys.modules[pkg.__name__] = pkg
boundary = importlib.import_module(pkg.__name__ + ".pixel_boundary")
pixel = importlib.import_module(pkg.__name__ + ".pixel_continuity")


class IO:
    """Tiny file-backed stand-in: captures survive real copy/move/reload paths."""
    def __init__(self, root):
        self.root, self.calls, self.number = root, [], 0
    def source_path(self, video): return Path(video)
    def from_path(self, path): return Path(path)
    def probe(self, path):
        return dict(width=32, height=32, rate=Fraction(24), time_base=Fraction(1, 24))
    def frames(self, path):
        self.calls.append(str(path))
        for i, image in enumerate(torch.load(path, weights_only=True)):
            yield image, Fraction(i, 24)
    @contextmanager
    def output_video(self, rate, source=None, **kwargs):
        self.number += 1
        result = self.root / ("temp_%d.mkv" % self.number)
        images = []
        self.calls.append(("write", str(source)))
        writer = types.SimpleNamespace(write=lambda image, timestamp=None: images.append(image.clone()))
        yield writer, result
        torch.save(images, result)
    def create(self, name, count, offset):
        path = self.root / name
        torch.save([torch.full((1, 32, 32, 3), offset + frame/1000) for frame in range(count)], path)
        return path


class BoundaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.io = IO(self.root)
        self.a = dict(index=8, revision="a", checkpoint_sha256="aaa", raw_frames=124,
                      delivered_frames=124, context_length=0)
        self.b = dict(index=9, revision="b", checkpoint_sha256="bbb", raw_frames=124,
                      delivered_frames=85, context_length=39, predecessor_revision="a")
        self.source = dict(segments=[self.a, self.b], pixel_continuity=[
            dict(scene=9, revision="b", previous_revision="a")])
        pixel.apply_selection(self.source)
        self.options, recipe = boundary.MiniMaxH3PixelBoundarySettings().settings(True)
        self.manifest = dict(source_manifest=self.source, run_name="duplicate", profile="test",
                             profile_config=dict(config_hash="config", recipe=json.loads(recipe)))
        self.records = [dict(kind="scene", scene=8, start_frame=0, frame_count=124,
                             source_in_frame=0, source_frame_count=124),
                        dict(kind="scene", scene=9, start_frame=124, frame_count=85,
                             source_in_frame=0, source_frame_count=85)]
        upscale = types.ModuleType(pkg.__name__ + ".upscale_nodes")
        upscale._profile_dir = lambda *args: str(self.root / "profile")
        upscale._upscale_source_contract = lambda source: json.dumps(source, sort_keys=True)
        self.modules = patch.dict(sys.modules, {upscale.__name__: upscale})
        self.modules.start()
        self.transport = patch.object(boundary, "_video_io", lambda: self.io)
        self.transport.start()
        self.record_patch = patch.object(boundary, "_records", lambda manifest: (self.records, 209))
        self.record_patch.start()

    def tearDown(self):
        self.record_patch.stop()
        self.transport.stop()
        self.modules.stop()
        self.tmp.cleanup()

    def capture(self):
        node = boundary.MiniMaxH3PixelBoundaryCapture()
        a = self.io.create("dlss_a.mkv", 124, .1)
        b = self.io.create("dlss_b.mkv", 124, .2)
        for source, video in ((self.a, a), (self.b, b)):
            self.assertEqual(node.capture({**self.manifest, "index": source["index"]}, video, self.options)[0], video)
        return a, b

    def test_off_is_exact_recipe_and_no_io_or_models(self):
        node = boundary.MiniMaxH3PixelBoundarySettings()
        original = '{ "recipe" : "unchanged" }'
        off, recipe = node.settings(False, base_recipe_json=original)
        self.assertEqual(recipe, original)
        self.assertFalse(off["enabled"])
        self.assertEqual(boundary.MiniMaxH3PixelBoundaryCapture().capture(None, "video", off)[0], "video")
        export = boundary.MiniMaxH3PixelBoundaryExport()
        self.assertEqual(export.check_lazy_status("original", None, off), [])
        self.assertEqual(export.export("original", None, off)["result"][0], "original")
        self.assertEqual(self.io.calls, [])
        with self.assertRaises(ValueError): node.settings(True, frames_per_side=18)

    def test_unmarked_no_cache_export_or_gpu(self):
        self.b.pop("pixel_continuity")
        self.assertEqual(boundary.MiniMaxH3PixelBoundaryCapture().capture(
            {**self.manifest, "index": 8}, "not-a-file", self.options)[0], "not-a-file")
        export = boundary.MiniMaxH3PixelBoundaryExport()
        self.assertEqual(export.check_lazy_status("unused", self.manifest, self.options), [])
        self.assertEqual(export.export("unused", self.manifest, self.options)["result"][0], "unused")
        self.assertEqual(self.io.calls, [])

    def test_disk_capture_raw_trim_and_resume_identity(self):
        a, b = self.capture()
        for scene, role, original, first in ((self.a, "tail", a, 107), (self.b, "head", b, 39)):
            path = boundary._cache_path(self.manifest, scene, role, self.options)
            actual = torch.stack(torch.load(path, weights_only=True))
            expected = torch.stack(torch.load(original, weights_only=True)[first:first+17])
            self.assertTrue(torch.equal(actual, expected))
            self.assertEqual(path, boundary._cache_path(copy.deepcopy(self.manifest), copy.deepcopy(scene), role, dict(self.options)))
            self.assertNotEqual(path, boundary._cache_path(self.manifest, {**scene, "revision": "changed"}, role, self.options))
        changed = {**self.manifest, "profile_config": {**self.manifest["profile_config"], "config_hash": "changed"}}
        self.assertNotEqual(boundary._cache_path(self.manifest, self.a, "tail", self.options),
                            boundary._cache_path(changed, self.a, "tail", self.options))
        self.assertEqual(len(list((self.root / "profile/boundary_sources").iterdir())), 2)

    def test_editorial_selection(self):
        jobs, skipped = boundary._jobs(self.source, self.records, 17)
        self.assertEqual((len(jobs), skipped), (1, 0))
        self.assertEqual((jobs[0]["start"], jobs[0]["join"], jobs[0]["end"]), (85, 124, 158))
        for variant in ([], list(reversed(self.records)),
                        [self.records[0], dict(kind="gap"), self.records[1]],
                        [dict(self.records[0], frame_count=107), self.records[1]],
                        [self.records[0], dict(self.records[1], source_in_frame=17)],
                        [self.records[0], dict(self.records[1], frame_count=20)]):
            self.assertEqual(boundary._jobs(self.source, variant, 17), ([], 1))
        moved = [dict(self.records[0], start_frame=24), dict(self.records[1], start_frame=148)]
        self.assertEqual(boundary._jobs(self.source, moved, 17)[0][0]["join"], 148)
        self.b["pixel_continuity"]["previous_revision"] = "different"
        self.assertEqual(boundary._jobs(self.source, self.records, 17), ([], 0))

    def test_window_has_hq_anchors_and_pre_usdu_center(self):
        a, b = self.capture()
        baseline = self.io.create("baseline.mkv", 209, .4)
        job = boundary._jobs(self.source, self.records, 17)[0][0]
        tail = boundary._cache_path(self.manifest, self.a, "tail", self.options)
        head = boundary._cache_path(self.manifest, self.b, "head", self.options)
        window = list(boundary._windows(self.io, baseline, self.io.probe(baseline), 209, [job], [(tail, head)], 17))[0][1]
        load = lambda path: torch.load(path, weights_only=True)
        expected = load(baseline)[85:107] + load(a)[107:124] + load(b)[39:56] + load(baseline)[141:158]
        self.assertTrue(torch.equal(torch.stack(load(window)), torch.stack(expected)))

    def test_export_exact_region_audio_and_immutable_source(self):
        self.capture()
        baseline = self.io.create("baseline.mkv", 209, .4)
        original = baseline.read_bytes()
        refined = self.io.create("refined.mkv", 73, .7)
        expected_middle = torch.load(refined, weights_only=True)[22:56]
        exporter = boundary.MiniMaxH3PixelBoundaryExport()
        self.assertEqual(exporter.check_lazy_status(str(baseline), self.manifest, self.options), ["model", "clip", "video_vae"])
        with patch.object(boundary, "_refine", return_value=refined) as refine:
            result = exporter.export(str(baseline), self.manifest, self.options)["result"][0]
            refine.assert_called_once()
        self.assertNotEqual(result, str(baseline))
        self.assertEqual(original, baseline.read_bytes())
        before, after = [torch.load(path, weights_only=True) for path in (baseline, result)]
        self.assertEqual(len(after), 209)
        self.assertTrue(torch.equal(torch.stack(before[:107]), torch.stack(after[:107])))
        self.assertTrue(torch.equal(torch.stack(before[141:]), torch.stack(after[141:])))
        self.assertTrue(torch.equal(torch.stack(expected_middle), torch.stack(after[107:141])))
        self.assertIn(("write", str(baseline)), self.io.calls, "original audio source forwarded for stream copy")
        self.assertFalse(list(self.root.glob("h3_boundary_*")), "scratch directory removed")

    def test_missing_cache_or_changed_settings_cannot_silently_resume(self):
        exporter = boundary.MiniMaxH3PixelBoundaryExport()
        with self.assertRaisesRegex(ValueError, "Missing pre-USDU samples"):
            exporter.export("not-read", self.manifest, self.options)
        with self.assertRaisesRegex(ValueError, "recipe_json"):
            exporter.export("not-read", self.manifest, {**self.options, "denoise": .3})
        self.assertEqual(self.io.calls, [])

    def test_multiple_joins_decode_baseline_once_and_skip_overlaps(self):
        a, b = self.capture()
        c = dict(self.b, index=10, revision="c", predecessor_revision="b")
        c["pixel_continuity"] = dict(previous_revision="b", previous_checkpoint_sha256="bbb", version=1)
        source = dict(self.source, segments=[self.a, self.b, c])
        records = self.records + [dict(kind="scene", scene=10, start_frame=209, frame_count=85,
                                      source_in_frame=0, source_frame_count=85)]
        jobs, skipped = boundary._jobs(source, records, 17)
        self.assertEqual((len(jobs), skipped), (2, 0))
        baseline = self.io.create("long.mkv", 294, .3)
        tail = boundary._cache_path(self.manifest, self.a, "tail", self.options)
        head = boundary._cache_path(self.manifest, self.b, "head", self.options)
        windows = list(boundary._windows(self.io, baseline, self.io.probe(baseline), 294,
                                         jobs, [(tail, head)] * 2, 17))
        self.assertEqual(len(windows), 2)
        self.assertEqual(self.io.calls.count(str(baseline)), 1)
        # A short middle clip can fit both individual edits but leave the second
        # window touching the first. Keep the first, don't silently overlap.
        short = [records[0], dict(records[1], frame_count=51, source_frame_count=51),
                 dict(records[2], start_frame=175)]
        self.assertEqual(len(boundary._jobs(source, short, 17)[0]), 1)

    def test_failed_refinement_keeps_baseline_and_removes_scratch(self):
        self.capture()
        baseline = self.io.create("baseline.mkv", 209, .4)
        original = baseline.read_bytes()
        with patch.object(boundary, "_refine", side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                boundary.MiniMaxH3PixelBoundaryExport().export(str(baseline), self.manifest, self.options)
        self.assertEqual(baseline.read_bytes(), original)
        self.assertFalse(list(self.root.glob("h3_boundary_*")))
        self.assertFalse(list(self.root.glob("baseline_boundary_*")))


if __name__ == "__main__":
    unittest.main()
