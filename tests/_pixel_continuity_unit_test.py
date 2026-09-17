"""Bounded synthetic VIDEO tests: no GPU, models, server or project changes."""
import copy
from contextlib import contextmanager
from fractions import Fraction
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

import torch

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("pixel_test")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location("pixel_test.pixel_continuity", ROOT / "pixel_continuity.py")
pixel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pixel)


class IO:
    def __init__(self):
        self.files = {}
        self.reads = []
    def source_path(self, video): return video
    def from_path(self, path): return path
    def probe(self, path): return {"width": 4, "height": 4, "rate": Fraction(24), "time_base": Fraction(1, 24)}
    def frames(self, path):
        self.reads.append(path)
        for i, image in enumerate(self.files[path]):
            yield image, Fraction(i, 24)
    @contextmanager
    def output_video(self, rate, **kwargs):
        path = "output%d" % len(self.files)
        self.files[path] = []
        yield types.SimpleNamespace(write=lambda image, timestamp: self.files[path].append(image.clone())), path


def scene(index, revision, raw, delivered, **kwargs):
    return dict(index=index, revision=revision, raw_frames=raw, delivered_frames=delivered,
                context_length=raw-delivered, checkpoint_sha256=revision*64, **kwargs)


class ContinuityTest(unittest.TestCase):
    def setUp(self):
        self.a = scene(1, "a", 22, 22)
        self.b = scene(2, "b", 22, 17, predecessor_revision="a")
        self.manifest = {"segments": [self.a, self.b], "pixel_continuity": [
            {"scene": 2, "revision": "b", "previous_revision": "a"}]}
        pixel.apply_selection(self.manifest)
        self.state = {"index": 2, "source_manifest": self.manifest,
                      "segments": [{**self.a, "segment": "hq", "source_revision": "a",
                                    "source_checkpoint_sha256": self.a["checkpoint_sha256"]}]}
        self.io = IO()
        self.io.files["hq"] = [torch.full((1, 4, 4, 3), .4 + i / 1000) for i in range(22)]
        self.io.files["dlss"] = [torch.full((1, 4, 4, 3), .40) for _ in range(22)]
        upscale = types.ModuleType("pixel_test.upscale_nodes")
        upscale._source_segment = lambda state, index=None: state["source_manifest"]["segments"][(index or state["index"])-1]
        upscale._source_bounds = lambda manifest: (1, 2)
        def verify(metadata, source, index):
            if metadata["segment"]["source_revision"] != source["revision"]:
                raise ValueError("different source revision")
        upscale._verify_upscale_source = verify
        chain = types.ModuleType("pixel_test.chain_nodes")
        chain._absolute_output_path = lambda path: path
        self.patch = patch.dict(sys.modules, {upscale.__name__: upscale, chain.__name__: chain})
        self.patch.start()
        self.transport = patch.object(pixel, "_video_io", lambda: self.io)
        self.transport.start()
    def tearDown(self):
        self.transport.stop()
        self.patch.stop()
    def test_selection_exact_pair_and_alt(self):
        self.assertTrue(self.b.get("pixel_continuity"))
        for update in ({"revision": "x"}, {"processing_source": {"original": {"revision": "x"}}}):
            manifest = copy.deepcopy(self.manifest)
            current = manifest["segments"][1]
            current.update(update)
            pixel.apply_selection(manifest)
            self.assertNotIn("pixel_continuity", current)
        unmarked = copy.deepcopy(self.manifest)
        unmarked["pixel_continuity"] = []
        pixel.apply_selection(unmarked)
        self.assertNotIn("pixel_continuity", unmarked["segments"][1])
        alt = copy.deepcopy(self.b)
        alt.update(revision="alt", presentation_source={"original": self.b})
        alt.pop("pixel_continuity")
        self.assertEqual(pixel.original_segment(alt)["revision"], "alt")
        self.assertEqual(pixel.original_segment({"processing_source": {"original": self.b}}), self.b)
    def test_unmarked_no_processing(self):
        self.b.pop("pixel_continuity")
        result = pixel.MiniMaxH3PixelContinuityPrepare().prepare(self.state, "dlss")
        self.assertEqual(result[:4], ("dlss", None, False, None))
        self.assertEqual(pixel.MiniMaxH3PixelContinuityFinish().finish("dlss", None)[0], "dlss")
        self.assertEqual(self.io.reads, [])
    def test_head_mask_raw_count_tone_and_resume(self):
        prepare = pixel.MiniMaxH3PixelContinuityPrepare()
        video, mask, anchor, context, _ = prepare.prepare(self.state, "dlss")
        self.assertTrue(anchor)
        self.assertEqual(tuple(mask.shape), (22, 4, 4))
        self.assertEqual(mask.untyped_storage().nbytes(), 22 * 4, "expanded view, not full-frame allocation")
        self.assertEqual(mask[:5].sum(), 0)
        self.assertTrue(torch.all(mask[5:] == 1))
        for a, b in zip(self.io.files["hq"][-5:], self.io.files[video][:5]):
            self.assertTrue(torch.equal(a, b))
        self.assertEqual(len(self.io.files[video]), 22)
        resumed = prepare.prepare(copy.deepcopy(self.state), "dlss")
        self.assertTrue(all(torch.equal(a,b) for a,b in zip(self.io.files[video], self.io.files[resumed[0]])))
        self.io.files["refined"] = [image + .01 for image in self.io.files[video]]
        result = pixel.MiniMaxH3PixelContinuityFinish().finish("refined", context, 1, 5)[0]
        self.assertEqual(len(self.io.files[result]), 22)
        for a,b in zip(self.io.files[video][:5], self.io.files[result][:5]):
            self.assertTrue(torch.equal(a,b), "head restored even if refiner changes it")
        self.assertGreater(self.io.files[result][5].mean(), self.io.files["refined"][5].mean())
        self.assertTrue(torch.equal(self.io.files[result][9], self.io.files["refined"][9]), "tone fades out")
        off = pixel.MiniMaxH3PixelContinuityFinish().finish("refined", context, 0, 5)[0]
        self.assertTrue(torch.equal(self.io.files[off][5], self.io.files["refined"][5]))
    def test_missing_and_wrong_hq(self):
        self.state["segments"] = []
        with self.assertRaisesRegex(ValueError, "previous HQ"):
            pixel.MiniMaxH3PixelContinuityPrepare().prepare(self.state, "dlss")
        self.state["segments"] = [{"index": 1, "source_revision": "x"}]
        with self.assertRaisesRegex(ValueError, "different source"):
            pixel.MiniMaxH3PixelContinuityPrepare().prepare(self.state, "dlss")
    def test_reference_windows_not_continuations(self):
        for values in ({"visual_context_start_frame": 0}, {"predecessor_revision": "x"},
                       {"context_length": 22}, {"visual_context_lead_frames": 1},
                       {"visual_context_blocks": [{}, {}]}):
            with self.assertRaises(ValueError):
                pixel.continuity_window({**self.b, **values}, self.a)
        block = {"source_scene": 1, "source_revision": "a", "frames": 5, "resolved_start_frame": 17}
        self.assertEqual(pixel.continuity_window({**self.b, "visual_context_blocks": [block]}, self.a), (17, 5))
        self.assertEqual(pixel.continuity_window(scene(2,"b",39,17,predecessor_revision="a"), self.a), (0,22))
    def test_bad_raw_media(self):
        self.io.files["dlss"].pop()
        with self.assertRaisesRegex(ValueError, "frame count"):
            pixel.MiniMaxH3PixelContinuityPrepare().prepare(self.state, "dlss")


if __name__ == "__main__":
    unittest.main()
