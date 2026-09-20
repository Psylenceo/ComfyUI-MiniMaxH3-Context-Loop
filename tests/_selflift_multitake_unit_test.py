"""Durable multi-take finishing with tiny CPU sampler doubles and temp artifacts."""
import asyncio
import copy
import importlib
import logging
import sys
import types
import unittest
from unittest.mock import patch

from _selflift_hunt_unit_test import HuntTests, HuntStore, hunt, PACKAGE, CALLS, runtime

selection = importlib.import_module(PACKAGE + ".selflift_selection")


class MultiTakeTests(unittest.IsolatedAsyncioTestCase):
    run_node = HuntTests.run_node
    fake_preview = HuntTests.fake_preview

    def setUp(self):
        HuntTests.setUp(self)
        self.original_state = self.state
        self.state["previous_latent"] = self.latent

        def selected(state, seed):
            result = dict(state)
            result["plan"] = copy.deepcopy(state["plan"])
            result["plan"]["shots"][0]["seed"] = seed
            return result

        for item in (patch.object(hunt, "selected_state", side_effect=selected),
                     patch.dict(sys.modules, {PACKAGE + ".chain_nodes": types.SimpleNamespace(
                         _public_segment=lambda segment: {k: v for k, v in segment.items()
                                                         if not k.startswith("_h3_")})})):
            item.start()
            self.addCleanup(item.stop)

    async def start_marked(self, main=1, marked=None, **kwargs):
        marked = [1, 3] if marked is None else marked
        task = asyncio.create_task(self.run_node(3, **kwargs))
        async with asyncio.timeout(5):
            while not task.done():
                rows = self.store.list()
                if rows and rows[0].get("phase") == "waiting":
                    record = rows[0]
                    updated = hunt.mark_takes(self.store, record["id"], main, marked,
                                             record["created_at"], 0)
                    self.assertIsNone(updated.get("selected"), "Draft marks must not release the gate")
                    self.assertEqual(len(CALLS), 3, "Marking must never sample")
                    reloaded = HuntStore(self.root).read(record["id"])
                    self.assertEqual(reloaded["marked"], sorted(set(marked + [main])))
                    with self.assertRaisesRegex(ValueError, "changed"):
                        hunt.approve(self.store, record["id"], main, marked, record["created_at"], 0)
                    hunt.approve(self.store, record["id"], main, marked, record["created_at"], 1)
                    break
                await asyncio.sleep(.01)
            return await task

    def saved(self, result):
        latent, _, state = result["result"]
        selection.validate_save(state, latent, self.root)
        marker = state[selection.BATCH_STATE]
        ordinal = marker["ordinal"]
        segment = {"index": 1, "revision": str(ordinal) * 32,
                   "seed": state["plan"]["shots"][0]["seed"]}
        for name, extension in (("segment", "mp4"), ("checkpoint", "safetensors"),
                                ("revision_metadata", "json"), ("generated_audio", "wav")):
            path = self.root / "h3_chains" / self.state["plan"]["run_name"] / f"saved_{ordinal}.{extension}"
            path.write_bytes(b"committed fixture")
            segment[name] = path.relative_to(self.root).as_posix()
        return selection.after_save(state, segment, self.root)

    async def test_marks_main_and_sequential_saved_progress_survive_restarts(self):
        result = await self.start_marked(auto_remove_saved_takes=True, run_mode="regenerate")
        marker = result["result"][2][selection.BATCH_STATE]
        self.assertEqual(marker["ordinals"], [3, 1])
        self.assertEqual(marker["ordinal"], 3)
        self.assertEqual(result["result"][2]["plan"]["shots"][0]["seed"], 44)
        self.assertEqual([v["shape"][-2:] for v in CALLS], [(4, 6)] * 3 + [(8, 12)])
        with self.assertRaisesRegex(ValueError, "awaiting scene save"):
            hunt.clean_saved_hunt(self.store, marker["id"], {"created_at": marker["created_at"]})
        with self.assertRaisesRegex(ValueError, "selected_state"):
            selection.validate_save(self.original_state, result["result"][0], self.root)
        with self.assertRaisesRegex(ValueError, "selected_state"):
            selection.validate_save(result["result"][2], self.latent, self.root)

        # A downstream failure before Save reuses the finished high latent.
        CALLS.clear()
        self.store = HuntStore(self.root)
        restored = await self.run_node(3, auto_remove_saved_takes=True)
        self.assertEqual(CALLS, [])
        self.assertEqual(restored["result"][2][selection.BATCH_STATE]["ordinal"], 3)
        alternate = self.saved(restored)
        self.assertIn(selection.NEXT_TAKE, alternate)
        self.assertNotIn(selection.FINISHED_TAKES, alternate)
        self.assertIsNone(hunt.cleanup_after_segment_save(restored["result"][2], self.root,
                                                         logging.getLogger("multitake-test")))
        self.assertTrue(self.store.locate(marker["id"]).is_dir())
        continued = selection.continuation_state(restored["result"][2], alternate[selection.NEXT_TAKE])
        self.assertEqual(continued["plan"], self.original_state["plan"])
        self.assertIs(continued["previous_latent"], self.latent)
        self.assertEqual(continued["segments"], [])
        self.assertNotIn(hunt.CLEANUP_STATE, continued)
        self.state = continued
        # The internal same-scene recursion must ignore explicit regenerate.
        main = await self.run_node(3, auto_remove_saved_takes=True, run_mode="regenerate")
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual([v["shape"][-2:] for v in CALLS], [(8, 12)])
        self.assertEqual(main["result"][2][selection.BATCH_STATE]["ordinal"], 1)
        completed = self.saved(main)
        self.assertNotIn(selection.NEXT_TAKE, completed)
        self.assertEqual([s["seed"] for s in completed[selection.FINISHED_TAKES]], [44, 42])
        self.assertEqual(completed["seed"], 42)
        removed = hunt.cleanup_after_segment_save(main["result"][2], self.root,
                                                 logging.getLogger("multitake-test"))
        self.assertGreater(removed["files"], 4)
        self.assertEqual(self.store.list(), [])
        for take in completed[selection.FINISHED_TAKES]:
            self.assertTrue((self.root / take["checkpoint"]).is_file())

    async def test_partial_batch_restart_skips_saved_alternate_and_retries_only_failed_high(self):
        first = await self.start_marked(main=2, marked=[1, 2, 3])
        self.assertEqual(first["result"][2][selection.BATCH_STATE]["ordinal"], 1)
        self.saved(first)
        original = runtime.progressive_sample

        def fail_high(*args, **kwargs):
            if kwargs.get("handoff") is not None:
                raise RuntimeError("second high failed")
            return original(*args, **kwargs)

        CALLS.clear()
        with patch.object(runtime, "progressive_sample", side_effect=fail_high):
            with self.assertRaisesRegex(RuntimeError, "second high failed"):
                await self.run_node(3)
        self.assertEqual(CALLS, [])
        self.assertEqual(self.store.list()[0]["selected_ordinals"], [1, 2, 3])
        self.store = HuntStore(self.root)
        second = await self.run_node(3)
        self.assertEqual(second["result"][2][selection.BATCH_STATE]["ordinal"], 3)
        self.assertEqual([v["shape"][-2:] for v in CALLS], [(8, 12)])
        self.saved(second)
        main = await self.run_node(3)
        completed = self.saved(main)
        self.assertEqual([s["seed"] for s in completed[selection.FINISHED_TAKES]], [42, 44, 43])
        self.assertEqual(completed["seed"], 43)

    async def test_missing_saved_audio_reuses_high_latent_to_repair_alternate(self):
        result = await self.start_marked()
        alternate = self.saved(result)
        (self.root / alternate["generated_audio"]).unlink()
        CALLS.clear()
        restored = await self.run_node(3)
        self.assertEqual(CALLS, [], "A missing artifact needs decoding/saving, not another high pass")
        self.assertEqual(restored["result"][2][selection.BATCH_STATE]["ordinal"], 3)
        self.saved(restored)
        main = await self.run_node(3)
        self.assertEqual(main["result"][2][selection.BATCH_STATE]["ordinal"], 1)

    def test_validation_automatically_includes_main_and_rejects_invalid_marks(self):
        record = {"candidates": [{"ordinal": 1, "preview": "one.mp4"},
                                 {"ordinal": 2, "preview": "two.mp4"}, {"ordinal": 3}]}
        self.assertEqual(selection.selection(record, 1, [2, 2]), [1, 2])
        for main, marked in ((True, [1]), (1, []), (1, [True]), (1, [3]), (1, [999])):
            with self.subTest(main=main, marked=marked), self.assertRaises(ValueError):
                selection.selection(record, main, marked)
        large = {"candidates": [{"ordinal": n, "preview": "preview.mp4"} for n in range(1, 101)]}
        self.assertEqual(len(selection.selection(large, 1, list(range(1, 21)))), 20)
        with self.assertRaisesRegex(ValueError, "at most 20"):
            selection.selection(large, 1, list(range(2, 22)))


if __name__ == "__main__":
    unittest.main()
