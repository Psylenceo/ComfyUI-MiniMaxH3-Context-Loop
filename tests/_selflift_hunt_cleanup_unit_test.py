"""Bounded cleanup on disposable projects only; no real video/model required."""
import asyncio
import errno
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from _selflift_hunt_unit_test import HuntStore, hunt, layout, folder_paths, store_module, torch


class CleanupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = HuntStore(self.root)

    def batch(self, key="a", run="test", branch="main", legacy=False):
        project = self.root / "h3_chains" / run
        if legacy:
            (project / "checkpoints").mkdir(parents=True, exist_ok=True)
            (project / "checkpoints" / "clip_0001.json").write_text("{}")
        else:
            layout.create_project(project)
        if branch != "main":
            path = Path(layout.state_root(project)) / "branches" / branch
            path.mkdir(parents=True, exist_ok=True)
            (path / "branch.json").write_text("{}")
        record = self.store.create({"id": key * 64, "run_name": run, "branch_id": branch,
                                   "scene": 1, "phase": "finished"})
        folder = self.store.locate(record["id"])
        for name in ("source.safetensors", "recovery.json", "take_0001.safetensors",
                     "take_0002.safetensors", "finished_0001.safetensors",
                     "source.safetensors." + "f" * 32 + ".tmp"):
            (folder / name).write_bytes(b"temporary fixture")
        preview = self.store.preview_path(record, 1)
        preview.parent.mkdir(parents=True)
        preview.write_bytes(b"tiny preview fixture")
        preview.with_name("take_0002." + "f" * 32 + ".tmp.mp4").write_bytes(b"interrupted preview")
        return record, folder, preview

    def test_only_selected_batch_files_deleted_in_organized_legacy_and_named_branches(self):
        for index, (legacy, branch) in enumerate(((False, "main"), (True, "main"), (False, "d" * 32))):
            with self.subTest(legacy=legacy, branch=branch):
                run = "run_%d" % index
                record, folder, preview = self.batch(str(index + 1), run, branch, legacy)
                other, other_folder, other_preview = self.batch(str(index + 4), run, branch, legacy)
                final = self.root / "h3_chains" / run / "generation" / "clips" / "keep.mp4"
                final.parent.mkdir(parents=True, exist_ok=True)
                final.write_bytes(b"normal saved scene")
                checkpoint = final.with_suffix(".safetensors")
                checkpoint.write_bytes(b"normal checkpoint")
                # Stored candidate paths are not cleanup authority.
                self.store.update(record["id"], lambda r: r.update(candidates=[{
                    "ordinal": 1, "preview": str(final), "checkpoint": str(checkpoint)}]))
                result = hunt.clean_saved_hunt(self.store, record["id"], {"created_at": record["created_at"]})
                self.assertEqual(result["files"], 9)
                self.assertGreater(result["bytes"], 0)
                self.assertFalse(folder.exists())
                self.assertFalse(preview.parent.exists())
                self.assertEqual(final.read_bytes(), b"normal saved scene")
                self.assertEqual(checkpoint.read_bytes(), b"normal checkpoint")
                self.assertTrue(other_folder.is_dir() and other_preview.is_file())
                self.assertNotIn(record["id"], self.store._index())
                self.assertIn(other["id"], self.store._index())
                self.assertEqual(hunt.clean_saved_hunt(self.store, record["id"])["files"], 0)

    def test_stale_confirmation_cannot_delete_recreated_batch(self):
        record, folder, _ = self.batch()
        with self.assertRaisesRegex(ValueError, "changed"):
            hunt.clean_saved_hunt(self.store, record["id"], {"created_at": record["created_at"] - 1})
        self.assertTrue((folder / "source.safetensors").is_file())

    def test_lifted_previews_and_interrupted_encodes_are_cleaned_with_the_hunt(self):
        record, folder, preview = self.batch()
        lifted = self.store.preview_path(record, 1, upscale=True)
        lifted.write_bytes(b"lifted preview")
        lifted.with_name("take_0002.upscale." + "f" * 32 + ".tmp.mp4").write_bytes(b"partial")
        result = hunt.clean_saved_hunt(self.store, record["id"])
        self.assertEqual(result["files"], 11)
        self.assertFalse(folder.exists())
        self.assertFalse(preview.parent.exists())

    def test_unknown_contents_or_symlinks_refused_before_any_deletion(self):
        record, folder, preview = self.batch()
        foreign = preview.parent / "keep.txt"
        foreign.write_text("not a hunt artifact")
        before = (folder / "batch.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "Unexpected file"):
            hunt.clean_saved_hunt(self.store, record["id"])
        self.assertEqual((folder / "batch.json").read_bytes(), before)
        foreign.unlink()
        target = self.root / "keep.safetensors"
        target.write_bytes(b"never delete")
        (folder / "take_0099.safetensors").symlink_to(target)
        with self.assertRaisesRegex(ValueError, "Unexpected file"):
            hunt.clean_saved_hunt(self.store, record["id"])
        self.assertEqual(target.read_bytes(), b"never delete")
        self.assertEqual((folder / "batch.json").read_bytes(), before)

    def test_linked_directory_is_not_followed(self):
        record, folder, preview = self.batch()
        moved = folder.with_name("fixture_original")
        folder.rename(moved)
        folder.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            hunt.clean_saved_hunt(self.store, record["id"])
        self.assertTrue((moved / "source.safetensors").is_file())
        self.assertTrue(preview.is_file())

    def test_delete_error_leaves_index_for_retry_and_releases_claim(self):
        record, folder, _ = self.batch()
        with patch.object(Path, "unlink", side_effect=PermissionError("busy file")):
            with self.assertRaises(PermissionError):
                hunt.clean_saved_hunt(self.store, record["id"])
        self.assertTrue(folder.is_dir())
        self.assertIn(record["id"], self.store._index())
        self.assertNotIn(record["id"], hunt._CLEANING)
        self.assertGreater(hunt.clean_saved_hunt(self.store, record["id"])["files"], 0)

    def test_partial_io_failure_is_persisted_and_remaining_files_can_be_retried(self):
        record, folder, preview = self.batch()
        target = folder / "take_0002.safetensors"
        original_unlink = Path.unlink

        def delete(path, *args, **kwargs):
            if path == target:
                raise OSError(errno.EIO, "Input/output error", str(path))
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", delete):
            with self.assertRaises(OSError):
                hunt.clean_saved_hunt(self.store, record["id"], {"created_at": record["created_at"]})
        saved = HuntStore(self.root).read(record["id"])
        self.assertEqual(saved["phase"], "finished")
        self.assertIn("Input/output error", saved["cleanup_error"])
        self.assertIn("take_0002.safetensors", saved["cleanup_error"])
        self.assertTrue(target.exists())
        self.assertNotIn(record["id"], hunt._CLEANING)
        self.assertGreater(hunt.clean_saved_hunt(self.store, record["id"])["files"], 0)
        self.assertFalse(folder.exists())
        self.assertFalse(preview.exists())
        self.assertEqual(self.store.list(), [])

    @unittest.skipUnless(Path("/proc/self/maps").is_file(), "Linux mapping inspection")
    def test_cleanup_can_delete_bundles_while_downstream_tensors_remain_cached(self):
        record, folder, _ = self.batch()
        target = folder / "source.safetensors"
        expected = torch.arange(24, dtype=torch.float32)
        store_module.save_bundle(target, {"samples": expected})
        cached_output = store_module.load_bundle(target)
        original_unlink = Path.unlink

        def refuse_mapped_file(path, *args, **kwargs):
            # Simulate a share that refuses unlink on an open memory mapping.
            if path == target and str(path) in Path("/proc/self/maps").read_text():
                raise OSError(errno.EIO, "Input/output error", str(path))
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", refuse_mapped_file):
            self.assertGreater(hunt.clean_saved_hunt(self.store, record["id"])["files"], 0)
        self.assertFalse(folder.exists())
        torch.testing.assert_close(cached_output["samples"], expected, rtol=0, atol=0)

    def test_mark_routes_persist_drafts_fence_stale_tabs_and_hide_published_metadata(self):
        record, folder, preview = self.batch()
        self.store.update(record["id"], lambda r: r.update(candidates=[
            {"ordinal": n, "seed": str(n), "checkpoint": "take_%04d.safetensors" % n,
             "preview": preview.relative_to(self.root).as_posix(),
             "published": {"default": {"scene_prompt": "private large metadata"}}}
            for n in (1, 2)]))
        handlers = {}
        class Routes:
            def get(self, path):
                return lambda handler: handlers.setdefault(path, handler)
            post = get
        server = types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=types.SimpleNamespace(routes=Routes())))
        class Request:
            def __init__(self, body): self.body = body
            async def json(self): return self.body
        async def run():
            with patch.dict(sys.modules, {"server": server}), \
                    patch.object(folder_paths, "get_output_directory", return_value=str(self.root)), \
                    patch.object(store_module, "load_bundle", side_effect=AssertionError("No tensor reads in routes")):
                hunt.register_routes()
                mark = handlers["/h3/selflift/selection"]
                choose = handlers["/h3/selflift/choose"]
                body = {"id": record["id"], "created_at": record["created_at"],
                        "selection_version": 0, "main": 2, "ordinals": [1]}
                self.assertEqual((await mark(Request({"id": record["id"]}))).status, 400)
                self.assertEqual((await mark(Request(body))).status, 200)
                saved = HuntStore(self.root).read(record["id"])
                self.assertEqual(saved["marked"], [1, 2])
                self.assertEqual(saved["main"], 2)
                self.assertIsNone(saved["selected"])
                self.assertEqual((await mark(Request(body))).status, 400, "Stale tab must refresh")
                selection = {**body, "ordinal": 2, "ordinals": [1, 2]}
                self.assertEqual((await choose(Request(selection))).status, 400)
                selection["selection_version"] = 1
                (folder / "take_0002.safetensors").unlink()
                self.assertEqual((await choose(Request(selection))).status, 400, "Missing low must fail before approval")
                self.assertIsNone(self.store.read(record["id"])["selected"])
                (folder / "take_0002.safetensors").write_bytes(b"restored fixture")
                self.assertEqual((await choose(Request(selection))).status, 200)
                self.assertEqual(self.store.read(record["id"])["selected_ordinals"], [1, 2])
                self.store.update(record["id"], lambda r: r.update(cleanup_error="Input/output error"))
                response = await handlers["/h3/selflift/hunts"](Request({}))
                public = json.loads(response.body)["batches"][0]
                self.assertEqual(public["marked"], [1, 2])
                self.assertEqual(public["main"], 2)
                self.assertEqual(public["max_marked"], 20)
                self.assertNotIn("published", public["candidates"][0])
                self.assertTrue(public["candidates"][0]["saved"])
                self.assertEqual(public["cleanup_error"], "Input/output error")
        asyncio.run(run())

    def test_clean_route_requires_confirmation_and_matching_batch(self):
        record, folder, _ = self.batch()
        handlers = {}
        class Routes:
            def get(self, path):
                return lambda handler: handlers.setdefault(path, handler)
            post = get
        server = types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=types.SimpleNamespace(routes=Routes())))
        class Request:
            def __init__(self, body): self.body = body
            async def json(self): return self.body
        async def run():
            with patch.dict(sys.modules, {"server": server}), \
                    patch.object(folder_paths, "get_output_directory", return_value=str(self.root)):
                hunt.register_routes()
                clean = handlers["/h3/selflift/clean"]
                response = await clean(Request({"id": record["id"]}))
                self.assertEqual(response.status, 400)
                self.assertTrue(folder.is_dir())
                response = await clean(Request({"id": record["id"], "confirm": True, "created_at": -1}))
                self.assertEqual(response.status, 400)
                response = await clean(Request({"id": record["id"], "confirm": True, "created_at": record["created_at"]}))
                self.assertEqual(response.status, 200)
                self.assertTrue(json.loads(response.body)["ok"])
                self.assertFalse(folder.exists())
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
