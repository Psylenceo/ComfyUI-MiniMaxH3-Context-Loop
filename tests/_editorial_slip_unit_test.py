#!/usr/bin/env python3
"""Editorial slip: real PNG/WAV/video writes, immutable checkpoints and resume."""
import copy
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest
import wave

import av
import numpy as np
from PIL import Image
import torch
from safetensors.torch import save_file

from _png_export_unit_test import chain, folder_paths


class VideoVAE:
    def decode(self, _video):
        return (torch.arange(362).remainder(256).float() / 255).reshape(
            362, 1, 1, 1).expand(362, 16, 16, 3).clone()


class AudioVAE:
    audio_sample_rate = 24000

    def decode(self, _audio):
        return torch.linspace(-0.5, 0.5, 362000).reshape(1, 1, -1)


class SlipTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder_paths.output_directory = temporary.name
        self.root = Path(temporary.name)
        self.run = self.root / "h3_chains" / "slip"
        self.run.mkdir(parents=True)
        self.checkpoint = self.run / "clip.safetensors"
        self.tensors = {
            "video": torch.zeros(1, 24, 107, 1, 1),
            "audio": torch.zeros(1, 32, 2, 603),
            "context_frames": torch.arange(22).reshape(22, 1, 1, 1).float(),
        }
        save_file(self.tensors, self.checkpoint)
        self.video = self.run / "clip.mkv"
        writer = chain._StreamingLosslessRGBWriter(str(self.video), 24, {})
        for index in range(340):
            writer.write(np.full((16, 16, 3), (index + 22) % 256, dtype=np.uint8))
        writer.close()
        self.segment = {
            "index": 1, "id": "one", "revision": "1" * 32,
            "raw_frames": 362, "delivered_frames": 340,
            "history_hash": "saved", "blend_frames": 0,
            "checkpoint": chain._relative_output_path(str(self.checkpoint)),
            "checkpoint_sha256": chain._file_sha256(str(self.checkpoint)),
            "segment": chain._relative_output_path(str(self.video)),
            "segment_sha256": chain._file_sha256(str(self.video)),
        }
        self.editorial = {"format": "h3_chain_editorial_v1", "run_name": "slip",
            "scene_order": [{"scene": 1, "scene_id": "one"}],
            "trims": [{"scene_id": "one", "in_frame": 51, "out_frame": 123}]}
        self.manifest = {"format": "h3_chain_manifest_v3", "run_name": "slip",
            "clip_count": 1, "total_delivered_frames": 340,
            "compatibility": {"width": 16, "height": 16, "continuation_mode": "guide"},
            "segments": [self.segment], "editorial": self.editorial}

    def test_grid_old_trims_and_fixed_timeline(self):
        normalized = chain._normalize_run_editorial(self.editorial, "slip")
        self.assertEqual(normalized["trims"][0]["in_frame"], 51)
        before = copy.deepcopy(self.segment)
        view = chain._editorial_trimmed_segment(self.segment, normalized)
        self.assertEqual(chain._editorial_segment_delivered_frames(view), 72)
        self.assertEqual(self.segment, before)
        downstream = {"index": 2, "id": "two", "raw_frames": 239,
            "delivered_frames": 234, "resolved_context_length": 22,
            "predecessor_editorial_out_frames": 340}
        segments = [self.segment, downstream]
        _, slipped, total = chain._editorial_timeline_records("slip", segments, normalized)
        old = {**normalized, "trims": [{"scene_id": "one", "out_frame": 72}]}
        _, right, right_total = chain._editorial_timeline_records("slip", segments, old)
        self.assertEqual(slipped[1], right[1])
        self.assertEqual(total, right_total)
        self.assertEqual(slipped[0]["source_start_frame"], 51)
        assembly = chain._apply_editorial_timeline_records([
            {"kind": "segment", "scene_index": index, "path": str(self.video),
             "input_frames": 340, "delivered_frames": 340,
             "blend_frames": 0 if index == 1 else 5, "skip_frames": 0}
            for index in (1, 2)], slipped, [self.segment,
                {**downstream, "segment": self.segment["segment"]}], {})
        self.assertEqual(assembly[1]["blend_frames"], 0,
                         "changed cut must not reintroduce an old overlap")
        # Source and endpoint validity match the UI's video/audio boundary grid.
        for start, end in [(1, 123), (51, 124), (123, 123), (-3, 72), (0, 341)]:
            with self.assertRaises(ValueError):
                chain._editorial_trimmed_segment(self.segment, {"trims": [{
                    "scene_id": "one", "in_frame": start, "out_frame": end}]})
        for start, end in [(0, 340), (51, 340), (0, 72), (51, 123)]:
            view = chain._editorial_trimmed_segment(self.segment, {"trims": [{
                "scene_id": "one", "in_frame": start, "out_frame": end}]})
            self.assertEqual(chain._editorial_segment_delivered_frames(view), end - start)

    def test_png_wav_offsets_and_same_length_cache_invalidation(self):
        node = chain.MiniMaxH3ChainExportPNG()
        self.editorial["chapters"] = [{"id": "chapter_one", "title": "One",
            "start_scene": 1, "start_scene_id": "one"}]
        def export():
            chapter = chain._chapter_manifest_from_manifest(self.manifest, 1)[0]
            result = node.export(chapter, VideoVAE(), "slip", 1, 1, False,
                save_workers=2, audio_vae=AudioVAE(), reuse_existing=True)["result"]
            return Path(result[0])
        path = export()
        with Image.open(path / "frame_00000001.png") as frame:
            self.assertEqual(frame.getpixel((0, 0))[0], 73)  # 22 technical + 51 in
        with Image.open(path / "frame_00000072.png") as frame:
            self.assertEqual(frame.getpixel((0, 0))[0], 144)
        with wave.open(str(path / "audio.wav"), "rb") as audio:
            self.assertEqual(audio.getnframes(), 72000)
            self.assertEqual(audio.getframerate(), 24000)
            samples = np.frombuffer(audio.readframes(1), dtype="<i2")
            expected = float(AudioVAE().decode(None).flatten()[73000]) * 32767
            self.assertAlmostEqual(float(samples[0]), expected, delta=2)
        with patch.object(VideoVAE, "decode", side_effect=AssertionError("cached PNG decode")), \
                patch.object(AudioVAE, "decode", side_effect=AssertionError("cached WAV decode")):
            self.assertEqual(export(), path)
        self.editorial["trims"][0].update(in_frame=102, out_frame=174)
        moved = export()
        self.assertNotEqual(moved, path)
        with Image.open(moved / "frame_00000001.png") as frame:
            self.assertEqual(frame.getpixel((0, 0))[0], 124)
        self.assertEqual(self.segment["checkpoint_sha256"], chain._file_sha256(str(self.checkpoint)))

    def test_assembly_backend_honors_both_edges(self):
        _, timeline, total = chain._editorial_timeline_records(
            "slip", [self.segment], self.editorial)
        records = chain._apply_editorial_timeline_records([{
            "kind": "segment", "scene_index": 1, "path": str(self.video),
            "input_frames": 340, "delivered_frames": 340,
            "blend_frames": 0, "skip_frames": 0,
        }], timeline, [self.segment], self.manifest["compatibility"])
        self.assertEqual(records[0]["skip_frames"], 51)
        output = self.run / "pyav.mp4"
        chain._pyav_blend_video(records, str(output), {}, total, 0)
        with av.open(str(output)) as container:
            pixels = [int(frame.to_ndarray(format="rgb24")[0, 0, 0])
                      for frame in container.decode(video=0)]
        self.assertEqual(len(pixels), 72)
        self.assertAlmostEqual(pixels[0], 73, delta=2)
        self.assertAlmostEqual(pixels[-1], 144, delta=2)
        ffmpeg = chain._usable_ffmpeg()
        if ffmpeg:
            metadata = self.run / "metadata.txt"
            chain._write_ffmetadata(str(metadata), {})
            output = self.run / "ffmpeg.mp4"
            chain._ffmpeg_blend_video(ffmpeg, records, str(output), str(metadata), total, 0)
            with av.open(str(output)) as container:
                pixels = [int(frame.to_ndarray(format="rgb24")[0, 0, 0])
                          for frame in container.decode(video=0)]
            self.assertEqual(len(pixels), 72)
            self.assertAlmostEqual(pixels[0], 73, delta=2)
            self.assertAlmostEqual(pixels[-1], 144, delta=2)

    def test_resume_uses_full_checkpoint_after_slip(self):
        metadata = self.run / "metadata.json"
        metadata.write_text(json.dumps({"history_hash": "saved", "segment": self.segment}))
        chain._atomic_json(chain._run_editorial_path("slip"), self.editorial)
        plan = {"run_name": "slip", "shots": [{"id": "one"}, {"id": "two"}]}
        with patch.object(chain, "_recover_checkpoint_pointer_transactions"), \
                patch.object(chain.CheckpointGraphManager, "active_selection", return_value=({}, [])), \
                patch.object(chain, "_resume_context_predecessors", return_value={"scenes": [1]}), \
                patch.object(chain, "_validate_scene_resolution_boundary"), \
                patch.object(chain, "_artifact_paths", return_value={"metadata": str(metadata)}), \
                patch.object(chain, "_prompt_fields", return_value={}), \
                patch.object(chain, "_plan_context_storage_length", return_value=22), \
                patch.object(chain, "_streams_from_latent", side_effect=lambda latent: latent["samples"]):
            state = chain._load_resume_state(plan, 2)
        self.assertNotIn("_editorial_out_frames", state["segments"][0])
        torch.testing.assert_close(state["previous_frames"], self.tensors["context_frames"])
        torch.testing.assert_close(state["previous_latent"]["samples"][0], self.tensors["video"])
        torch.testing.assert_close(state["previous_latent"]["samples"][1], self.tensors["audio"])


if __name__ == "__main__":
    unittest.main()
