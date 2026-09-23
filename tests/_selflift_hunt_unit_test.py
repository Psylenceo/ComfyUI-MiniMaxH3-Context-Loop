"""Isolated disk/OOM/requeue tests; no real project, model or GPU involved."""
import asyncio
import importlib
import json
import math
import logging
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from _selflift_unit_test import (PACKAGE, Model, Euler, Nested, CALLS, lift,
                                nodes, runtime, state as carry, stub, torch)

folder_paths = stub("folder_paths", get_output_directory=lambda: "unused")
import comfy.model_management
comfy.model_management.throw_exception_if_processing_interrupted = lambda: None
store_module = importlib.import_module(PACKAGE + ".selflift_hunt_store")
hunt = importlib.import_module(PACKAGE + ".selflift_hunt")
preview = importlib.import_module(PACKAGE + ".selflift_preview")
layout = importlib.import_module(PACKAGE + ".chain_layout")
HuntStore = store_module.HuntStore


class HuntTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = HuntStore(self.root)
        self.settings = {"enabled": True, "upscaler_model": "test.safetensors", "high_resolution_steps": 2}
        self.shot = {"id": "walk", "seed": 42, "raw_frames": 56, "delivered_frames": 51,
                     "scene_prompt": "A dog walks.", "prompt": "A dog walks."}
        self.state = {"index": 1, "plan": {"run_name": "isolated_seed_hunt", "shots": [self.shot],
                    "selflift_sampling": self.settings, "width": 192, "height": 128}, "segments": []}
        self.video = torch.randn(1, 24, 17, 8, 12)
        self.audio = torch.randn(1, 32, 2, 80)
        vm = torch.ones(1, 1, 17, 8, 12); vm[:, :, :2] = 0; vm[:, :, 2:4] = .3
        am = torch.ones(1, 1, 2, 80); am[..., :10] = 0; am[..., 10:20] = .4
        self.latent = {"samples": Nested([self.video, self.audio]), "noise_mask": Nested([vm, am])}
        self.sigmas = torch.tensor([1., .8, .6, .3, 0.])
        self.positive = [[torch.randn(1, 4), {"minimax_keyframes": [{"latent": self.video, "frame_idx": 0}]}]]
        self.patches = [
            patch.object(folder_paths, "get_output_directory", return_value=str(self.root)),
            patch.object(nodes, "upscaler_models", return_value=["test.safetensors"]),
            patch.object(preview, "check_preview"),
            patch.object(preview, "save_preview", side_effect=self.fake_preview),
            patch.object(importlib.import_module(PACKAGE + ".selflift_runtime.h3_upscaler"),
                "learned_latent_lift", side_effect=lambda z, hw, name, **kw: lift(z, hw, **kw), create=True),
        ]
        for p in self.patches:
            p.start(); self.addCleanup(p.stop)
        CALLS.clear()

    def fake_preview(self, video, path, tiny, raw, trim):
        # It must already be possible to restore the low stage if decode dies.
        batch = self.store.list()[0]
        candidate = self.store.locate(batch["id"]) / ("take_%04d.safetensors" % batch["current"])
        self.assertTrue(candidate.is_file())
        self.assertEqual((raw, trim), (56, 5))
        store_module.atomic_json(path, {"fake_preview": True})

    async def run_node(self, candidates=2, **kwargs):
        kwargs.setdefault("prompt", {})
        kwargs.setdefault("extra_pnginfo", {"workflow": {"nodes": []}})
        sampler = kwargs.pop("sampler", Euler())
        return await hunt.MiniMaxH3SelfLiftSeedHunt().sample(
            self.state, Model(), self.positive, object(), self.latent, sampler, self.sigmas,
            42, candidate_count=candidates, **kwargs)

    async def select_when_ready(self, task, ordinal=1):
        async with asyncio.timeout(5):
            while not task.done():
                rows = self.store.list()
                if rows and rows[0].get("phase") == "waiting":
                    hunt.approve(self.store, rows[0]["id"], ordinal)
                    break
                await asyncio.sleep(.01)
            return await task

    async def test_oom_during_preview_resumes_saved_middle_and_completed_high(self):
        with patch.object(preview, "save_preview", side_effect=RuntimeError("simulated decode OOM")):
            with self.assertRaisesRegex(RuntimeError, "simulated decode OOM"):
                await self.run_node(1)
        self.assertEqual([v["steps"] for v in CALLS], [2])
        saved = self.store.list()[0]
        self.assertEqual(saved["phase"], "paused")
        self.assertNotIn(saved["id"], hunt._ACTIVE)
        # New node/store instances simulate loss of all in-memory review state.
        self.store = HuntStore(self.root)
        CALLS.clear()
        result = await self.select_when_ready(asyncio.create_task(self.run_node(1)))
        self.assertEqual([v["shape"][-2:] for v in CALLS], [(8, 12)])
        self.assertEqual(result["result"][2], self.state)
        self.assertEqual(self.store.list()[0]["phase"], "finished")
        CALLS.clear()
        restored = await self.run_node(1)
        self.assertEqual(CALLS, [])  # Downstream full VAE OOM doesn't repeat HIGH either.
        for a, b in zip(result["result"][0]["samples"].unbind(), restored["result"][0]["samples"].unbind()):
            torch.testing.assert_close(a, b, rtol=0, atol=0)

    async def test_disabled_is_ordinary_sampling_without_saved_hunt(self):
        self.settings["enabled"] = False
        output, status, selected = await self.run_node(3)
        self.assertIn("OFF", status)
        self.assertIs(selected, self.state)
        self.assertEqual([v["steps"] for v in CALLS], [4])
        self.assertEqual(self.store.list(), [])
        self.assertFalse((self.root / "h3_chains").exists())

    async def test_review_gate_schema_appends_default_on_for_old_workflows(self):
        with patch.object(preview, "tiny_models", return_value=["taeh3.safetensors"]):
            optional = hunt.MiniMaxH3SelfLiftSeedHunt.INPUT_TYPES()["optional"]
        self.assertEqual(list(optional)[-3:], ["auto_remove_saved_takes", "review_enabled", "run_mode"])
        self.assertIs(optional["review_enabled"][1]["default"], True)
        self.assertEqual(optional["run_mode"][1]["default"], "resume")

    async def test_regenerate_keeps_old_attempt_and_resume_reuses_new_finished_result(self):
        def prompt(mode):
            return {"model": {"class_type": "UNETLoader", "inputs": {"unet_name": "test"}},
                    "hunt": {"class_type": "MiniMaxH3SelfLiftSeedHunt",
                             "inputs": {"model": ["model", 0], "run_mode": mode}}}
        await self.run_node(1, review_enabled=False, prompt=prompt("resume"), unique_id="hunt")
        old = self.store.list()[0]
        old_folder = self.store.locate(old["id"])
        old_files = {p.name: p.read_bytes() for p in old_folder.iterdir()}
        CALLS.clear()
        await self.run_node(1, review_enabled=False, run_mode="regenerate",
                            prompt=prompt("regenerate"), unique_id="hunt")
        self.assertEqual([v["shape"][-2:] for v in CALLS], [(4, 6), (8, 12)])
        latest = self.store.list()[0]
        self.assertNotEqual(latest["id"], old["id"])
        self.assertEqual({p.name: p.read_bytes() for p in old_folder.iterdir()}, old_files)
        self.store = HuntStore(self.root)
        CALLS.clear()
        resumed = await self.run_node(1, review_enabled=False, run_mode="resume",
                                      prompt=prompt("resume"), unique_id="hunt")
        self.assertEqual(resumed["ui"]["h3_selflift_hunt"], [latest["id"]])
        self.assertEqual(CALLS, [])
        self.assertEqual(len(self.store.list()), 2)

    async def test_each_explicit_regeneration_creates_another_attempt(self):
        first = await self.run_node(1, review_enabled=False, run_mode="regenerate")
        CALLS.clear()
        second = await self.run_node(1, review_enabled=False, run_mode="regenerate")
        self.assertNotEqual(first["ui"], second["ui"])
        self.assertEqual([v["shape"][-2:] for v in CALLS], [(4, 6), (8, 12)])
        self.assertEqual(len(self.store.list()), 2)

    async def test_invalid_attempt_pointer_fails_without_sampling_or_falling_back(self):
        await self.run_node(1, review_enabled=False, run_mode="regenerate")
        latest = self.store.list()[0]
        pointers = self.store.locate(latest["id"]).parent / "attempts"
        pointer = next(pointers.glob("*.json"))
        original = json.loads(pointer.read_text())
        for invalid in ({**original, "id": "../escape"}, {**original, "base": "wrong"}):
            store_module.atomic_json(pointer, invalid)
            CALLS.clear()
            with self.assertRaisesRegex(ValueError, "attempt pointer"):
                await self.run_node(1, review_enabled=False)
            self.assertEqual(CALLS, [])
            self.assertFalse(hunt._ACTIVE)
            self.assertEqual(self.store.read(latest["id"])["phase"], "finished")

    async def test_regenerated_attempt_resumes_after_high_failure_without_repeating_low(self):
        await self.run_node(1, review_enabled=False)
        old = self.store.list()[0]
        original = runtime.progressive_sample
        def fail_high(*args, **kwargs):
            if kwargs.get("handoff") is not None:
                raise RuntimeError("new attempt high OOM")
            return original(*args, **kwargs)
        with patch.object(runtime, "progressive_sample", side_effect=fail_high):
            with self.assertRaisesRegex(RuntimeError, "new attempt high OOM"):
                await self.run_node(1, review_enabled=False, run_mode="regenerate")
        latest = self.store.list()[0]
        self.assertNotEqual(latest["id"], old["id"])
        self.store = HuntStore(self.root)
        CALLS.clear()
        result = await self.run_node(1, review_enabled=False, run_mode="resume")
        self.assertEqual([v["shape"][-2:] for v in CALLS], [(8, 12)])
        self.assertEqual(result["ui"]["h3_selflift_hunt"], [latest["id"]])
        self.assertEqual(self.store.read(old["id"])["phase"], "finished")
        self.assertFalse(hunt._ACTIVE)

    async def test_cleanup_of_new_attempt_never_falls_back_to_old_finished_result(self):
        await self.run_node(1, review_enabled=False)
        old = self.store.list()[0]
        result = await self.run_node(1, review_enabled=False, run_mode="regenerate",
                                     auto_remove_saved_takes=True)
        latest = self.store.list()[0]
        hunt.cleanup_after_segment_save(result["result"][2], self.root, logging.getLogger("hunt-test"))
        self.assertTrue(self.store.locate(old["id"]).is_dir())
        CALLS.clear()
        resumed = await self.run_node(1, review_enabled=False)
        self.assertEqual([v["shape"][-2:] for v in CALLS], [(4, 6), (8, 12)])
        self.assertEqual(resumed["ui"]["h3_selflift_hunt"], [latest["id"]])

    async def test_gate_off_runs_one_take_without_preview_and_keeps_recovery(self):
        with patch.object(preview, "check_preview", side_effect=AssertionError("No decoder needed")), \
             patch.object(preview, "save_preview", side_effect=AssertionError("No preview needed")):
            result = await asyncio.wait_for(self.run_node(4, review_enabled=False), 5)
        self.assertEqual([v["shape"][-2:] for v in CALLS], [(4, 6), (8, 12)])
        record = self.store.list()[0]
        self.assertEqual((record["phase"], record["selected"], record["review_enabled"]), ("finished", 1, False))
        self.assertEqual(len(record["candidates"]), 1)
        self.assertIsNone(record["candidates"][0]["preview"])
        folder = self.store.locate(record["id"])
        for name in ("source.safetensors", "take_0001.safetensors", "finished_0001.safetensors", "recovery.json"):
            self.assertTrue((folder / name).is_file(), name)
        self.assertEqual(result["result"][2], self.state)
        self.assertIn("review gate off", result["result"][1])
        self.assertNotIn(hunt.CLEANUP_STATE, result["result"][2])
        expected = runtime.progressive_sample(Model(), self.positive, self.positive, object(),
            self.latent, Euler(), self.sigmas, 42, 1., 2, .5, 0., .5, 1., "nearest", latent_lifter=lift)
        for a, b in zip(expected["samples"].unbind(), result["result"][0]["samples"].unbind()):
            torch.testing.assert_close(a, b, rtol=0, atol=0)
        CALLS.clear()
        self.store = HuntStore(self.root)
        await asyncio.wait_for(self.run_node(4, review_enabled=False), 5)
        self.assertEqual(CALLS, [], "Reboot/downstream failure must reuse the finished take")
        await asyncio.wait_for(self.run_node(4, review_enabled=True), 5)
        self.assertEqual(CALLS, [], "Turning review on does not invalidate an existing chosen take")
        self.assertEqual([r["id"] for r in self.store.list()], [record["id"]])

    async def test_gate_off_resumes_saved_middle_after_failed_review_preview(self):
        with patch.object(preview, "save_preview", side_effect=RuntimeError("preview OOM")):
            with self.assertRaisesRegex(RuntimeError, "preview OOM"):
                await self.run_node(3)
        saved = self.store.list()[0]
        CALLS.clear()
        await asyncio.wait_for(self.run_node(3, review_enabled=False), 5)
        self.assertEqual([v["shape"][-2:] for v in CALLS], [(8, 12)])
        self.assertEqual([r["id"] for r in self.store.list()], [saved["id"]])

    async def test_gate_off_respects_an_existing_chosen_take(self):
        await self.select_when_ready(asyncio.create_task(self.run_node(2)))
        saved = self.store.list()[0]
        hunt.approve(self.store, saved["id"], 2)
        CALLS.clear()
        with patch.object(hunt, "selected_state", side_effect=lambda state, seed: dict(state)) as selected:
            result = await asyncio.wait_for(self.run_node(4, review_enabled=False), 5)
        self.assertEqual([v["shape"][-2:] for v in CALLS], [(8, 12)])
        selected.assert_called_once_with(self.state, 43)
        self.assertIn("take 2; seed 43", result["result"][1])
        self.assertEqual(len(self.store.list()[0]["candidates"]), 2)
        self.assertEqual(self.store.list()[0]["id"], saved["id"])

    async def test_gate_off_high_oom_resumes_and_cleans_only_after_scene_save(self):
        original = runtime.progressive_sample
        def fail_high(*args, **kwargs):
            if kwargs.get("handoff") is not None:
                raise RuntimeError("automatic upscale OOM")
            return original(*args, **kwargs)
        with patch.object(runtime, "progressive_sample", side_effect=fail_high):
            with self.assertRaisesRegex(RuntimeError, "automatic upscale OOM"):
                await asyncio.wait_for(self.run_node(4, review_enabled=False, auto_remove_saved_takes=True), 5)
        saved = self.store.list()[0]
        self.assertEqual((saved["phase"], saved["selected"]), ("paused", 1))
        folder = self.store.locate(saved["id"])
        self.assertTrue((folder / "take_0001.safetensors").is_file())
        self.assertNotIn(saved["id"], hunt._ACTIVE)
        CALLS.clear()
        self.store = HuntStore(self.root)
        result = await asyncio.wait_for(self.run_node(4, review_enabled=False, auto_remove_saved_takes=True), 5)
        self.assertEqual([v["shape"][-2:] for v in CALLS], [(8, 12)])
        self.assertTrue(folder.exists(), "Decode/Segment Save have not succeeded yet")
        removed = hunt.cleanup_after_segment_save(result["result"][2], self.root, logging.getLogger("hunt-test"))
        self.assertGreaterEqual(removed["files"], 5)
        self.assertFalse(folder.exists())

    async def test_gate_on_recovers_unapproved_previewless_middle_without_resampling(self):
        original = HuntStore.update
        def fail_append(store, key, transform):
            if transform.__name__ == "append":
                raise RuntimeError("interrupted after durable low save")
            return original(store, key, transform)
        with patch.object(HuntStore, "update", fail_append):
            with self.assertRaisesRegex(RuntimeError, "durable low save"):
                await asyncio.wait_for(self.run_node(1, review_enabled=False), 5)
        saved = self.store.list()[0]
        self.assertIsNone(saved["selected"])
        CALLS.clear()
        await self.select_when_ready(asyncio.create_task(self.run_node(1)))
        self.assertEqual([v["shape"][-2:] for v in CALLS], [(8, 12)])
        self.assertTrue(self.store.list()[0]["candidates"][0]["preview"])
        self.assertEqual(self.store.list()[0]["id"], saved["id"])

    async def test_gate_off_locks_manual_choice_while_automatic_low_runs(self):
        await self.select_when_ready(asyncio.create_task(self.run_node(2)))
        saved = self.store.list()[0]
        self.store.update(saved["id"], lambda r: r.update(selected=None))
        original = HuntStore.update
        def attempt_choice(store, key, transform):
            if transform.__name__ == "begin_candidate":
                with self.assertRaisesRegex(ValueError, "Review gate is off"):
                    hunt.approve(store, key, 2)
            return original(store, key, transform)
        CALLS.clear()
        with patch.object(HuntStore, "update", attempt_choice):
            await asyncio.wait_for(self.run_node(4, review_enabled=False), 5)
        self.assertEqual(self.store.list()[0]["selected"], 1)
        self.assertEqual(CALLS, [], "Reuse the first saved take, including its finished high pass")

    async def test_auto_clean_waits_for_saved_scene_and_reuses_existing_hunt(self):
        # Default/off retains every candidate, including a completed high pass.
        first = await self.select_when_ready(asyncio.create_task(self.run_node(2)))
        self.assertNotIn(hunt.CLEANUP_STATE, first["result"][2])
        record = self.store.list()[0]
        folder = self.store.locate(record["id"])
        before = {path: path.read_bytes() for path in folder.iterdir()}
        CALLS.clear()
        result = await self.run_node(2, auto_remove_saved_takes=True)
        chosen = result["result"][2]
        self.assertEqual(CALLS, [], "changing cleanup preference must not start a new hunt")
        self.assertNotIn(hunt.CLEANUP_STATE, self.state)
        self.assertEqual({path: path.read_bytes() for path in folder.iterdir()}, before)
        # A downstream decode failure has not called the save hook: rerun
        # still reuses the finished high latent without doing any sampling.
        await self.run_node(2, auto_remove_saved_takes=True)
        self.assertEqual(CALLS, [])
        logger = logging.getLogger("hunt-test")
        self.assertIsNone(hunt.cleanup_after_segment_save(dict(chosen, index=2), self.root, logger))
        self.assertTrue(folder.exists(), "old cleanup marker cannot affect the next scene")
        removed = hunt.cleanup_after_segment_save(chosen, self.root, logger)
        self.assertGreater(removed["files"], 4)
        self.assertFalse(folder.exists())
        self.assertEqual(self.store.list(), [])
        self.assertTrue(all(not (self.root / take["preview"]).exists() for take in record["candidates"]))

    async def test_cleanup_disabled_allows_upscaling_second_version(self):
        await self.select_when_ready(asyncio.create_task(self.run_node(2)))
        record = self.store.list()[0]
        hunt.approve(self.store, record["id"], 2)
        CALLS.clear()
        # Use a same-seed shot here: selected_state's real plan revision behavior
        # is covered by the checkpoint suite with the complete Chain schema.
        with patch.object(hunt, "selected_state", side_effect=lambda state, seed: dict(state)):
            await self.run_node(2)
        self.assertEqual([v["shape"][-2:] for v in CALLS], [(8, 12)])
        folder = self.store.locate(record["id"])
        self.assertTrue((folder / "finished_0001.safetensors").is_file())
        self.assertTrue((folder / "finished_0002.safetensors").is_file())
        self.assertEqual(len(self.store.list()[0]["candidates"]), 2)

    async def test_cleanup_refuses_active_hunt_and_fences_sampling_while_deleting(self):
        await self.select_when_ready(asyncio.create_task(self.run_node(1)))
        record = self.store.list()[0]
        key = record["id"]
        hunt._ACTIVE.add(key)
        try:
            with self.assertRaisesRegex(ValueError, "running hunt"):
                hunt.clean_saved_hunt(self.store, key)
        finally:
            hunt._ACTIVE.discard(key)
        original = self.store.remove
        def deleting(*args):
            self.assertIn(key, hunt._CLEANING)
            with self.assertRaisesRegex(ValueError, "being cleaned"):
                hunt.approve(self.store, key, 1)
            with self.assertRaisesRegex(ValueError, "running hunt"):
                hunt.clean_saved_hunt(self.store, key)
            return original(*args)
        with patch.object(self.store, "remove", side_effect=deleting):
            hunt.clean_saved_hunt(self.store, key, {"created_at": record["created_at"]})
        self.assertNotIn(key, hunt._CLEANING)

    async def test_cleanup_failure_does_not_fail_the_saved_scene(self):
        result = await self.select_when_ready(asyncio.create_task(self.run_node(1, auto_remove_saved_takes=True)))
        with patch.object(HuntStore, "remove", side_effect=OSError("read-only disk")):
            with self.assertLogs("cleanup-test", level="WARNING"):
                self.assertIsNone(hunt.cleanup_after_segment_save(result["result"][2], self.root,
                    logging.getLogger("cleanup-test")))
        self.assertEqual(self.store.list()[0]["phase"], "finished")
        self.assertEqual(self.store.list()[0]["cleanup_error"], "read-only disk")
        self.assertEqual(hunt._CLEANING, set())

    async def test_cleanup_warning_write_failure_does_not_fail_the_saved_scene(self):
        result = await self.select_when_ready(asyncio.create_task(self.run_node(1, auto_remove_saved_takes=True)))
        with patch.object(HuntStore, "remove", side_effect=OSError("read-only disk")), \
                patch.object(HuntStore, "update", side_effect=OSError("cannot write warning")):
            with self.assertLogs("cleanup-test", level="WARNING") as logs:
                self.assertIsNone(hunt.cleanup_after_segment_save(result["result"][2], self.root,
                    logging.getLogger("cleanup-test")))
            self.assertIn("read-only disk", logs.output[0])
        self.assertEqual(self.store.list()[0]["phase"], "finished")
        self.assertEqual(hunt._CLEANING, set())

    async def test_recovery_omits_runtime_cache_fingerprints_and_reuses_saved_batch(self):
        prompt = {
            "1": {"class_type": "Loader", "inputs": {"model": "h3"},
                  "is_changed": [float("nan")]},
            "2141": {"class_type": "MiniMaxH3SelfLiftSeedHunt", "inputs": {"model": ["1", 0]},
                     "_meta": {"title": "My seed hunt"},
                     "is_changed": [hunt.MiniMaxH3SelfLiftSeedHunt.IS_CHANGED()]},
            "other:0": {"class_type": "OtherNode", "inputs": {"is_changed": "a real input"},
                        "is_changed": float("nan")},
        }
        workflow = {"nodes": [{"id": 2141, "type": "MiniMaxH3SelfLiftSeedHunt",
                               "pos": [200, 300], "widgets_values": [18446744073709551615]}],
                    "links": [], "extra": {"note": "Keep the editable canvas"}}
        expected_prompt = {key: {k: v for k, v in node.items() if k != "is_changed"}
                           for key, node in prompt.items()}
        recipe = hunt.source_recipe(prompt, "2141")
        args = {"prompt": prompt, "unique_id": "2141", "extra_pnginfo": {"workflow": workflow}}
        await self.select_when_ready(asyncio.create_task(self.run_node(1, **args)))
        saved = self.store.list()[0]
        def reject_nonfinite(value):
            raise AssertionError("Recovery snapshot contains non-JSON constant " + value)
        snapshot = json.loads((self.store.locate(saved["id"]) / "recovery.json").read_text(),
                              parse_constant=reject_nonfinite)
        self.assertEqual(snapshot["prompt"], expected_prompt)
        self.assertEqual(snapshot["workflow"], workflow)
        self.assertEqual(snapshot["plan"], self.state["plan"])
        self.assertEqual(snapshot["contract"]["recipe"], recipe)
        self.assertEqual(store_module.digest(snapshot["contract"]), saved["id"])
        self.assertTrue(math.isnan(prompt["2141"]["is_changed"][0]), "do not mutate Comfy's cache")
        self.assertTrue(math.isnan(prompt["other:0"]["is_changed"]))
        prompt["1"]["is_changed"] = ["a different runtime fingerprint"]
        prompt["2141"]["is_changed"] = [hunt.MiniMaxH3SelfLiftSeedHunt.IS_CHANGED()]
        CALLS.clear()
        await self.run_node(1, **args)
        self.assertEqual(CALLS, [])
        self.assertEqual([row["id"] for row in self.store.list()], [saved["id"]])

    async def test_snapshot_keeps_real_nonfinite_inputs_invalid_and_atomic(self):
        self.assertIsNone(hunt.recovery_prompt(None))
        path = self.root / "recovery.json"
        store_module.atomic_json(path, {"previous": "valid snapshot"})
        before = path.read_bytes()
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                prompt = {"1": {"class_type": "Sampler", "inputs": {"cfg": value},
                                "is_changed": [float("nan")]}}
                snapshot = hunt.recovery_prompt(prompt)
                self.assertNotIn("is_changed", snapshot["1"])
                self.assertFalse(math.isfinite(snapshot["1"]["inputs"]["cfg"]))
                with self.assertRaises(ValueError):
                    store_module.atomic_json(path, {"prompt": snapshot})
                with self.assertRaises(ValueError):
                    store_module.digest({"cfg": value})
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(list(self.root.glob("*.tmp")), [])
                snapshot["1"]["inputs"]["cfg"] = 1.0
                self.assertFalse(math.isfinite(prompt["1"]["inputs"]["cfg"]),
                                 "snapshot must be detached from the live prompt")

    async def test_all_candidates_low_only_high_runs_once_and_preserves_masks(self):
        result = await self.select_when_ready(asyncio.create_task(self.run_node(3)))
        self.assertEqual([v["shape"][-2:] for v in CALLS], [(4, 6)] * 3 + [(8, 12)])
        record = self.store.list()[0]
        self.assertEqual([c["seed"] for c in record["candidates"]], ["42", "43", "44"])
        output = result["result"][0]
        torch.testing.assert_close(output["samples"].unbind()[0][:, :, :2], self.video[:, :, :2], rtol=0, atol=0)
        torch.testing.assert_close(output["samples"].unbind()[1][..., :10], self.audio[..., :10])
        expected = runtime.progressive_sample(Model(), self.positive, self.positive, object(),
            self.latent, Euler(), self.sigmas, 42, 1., 2, .5, 0., .5, 1., "nearest", latent_lifter=lift)
        for a, b in zip(expected["samples"].unbind(), output["samples"].unbind()):
            torch.testing.assert_close(a, b, rtol=0, atol=0)
        self.assertIn(carry.LOW_CARRY, output)
        self.assertTrue(layout.organized(self.root / "h3_chains" / "isolated_seed_hunt"))
        self.assertIn("/processing/", record["candidates"][0]["preview"])
        with patch.object(store_module, "load_bundle", side_effect=AssertionError("poll must not load tensors")):
            self.assertEqual(len(HuntStore(self.root).list()), 1)

    async def test_high_oom_keeps_approval_and_skips_all_low_work_on_retry(self):
        original = runtime.progressive_sample
        def fail_high(*args, **kwargs):
            if kwargs.get("handoff") is not None:
                raise RuntimeError("simulated upscale OOM")
            return original(*args, **kwargs)
        with patch.object(runtime, "progressive_sample", side_effect=fail_high):
            with self.assertRaisesRegex(RuntimeError, "simulated upscale OOM"):
                await self.select_when_ready(asyncio.create_task(self.run_node(1, auto_remove_saved_takes=True)))
        self.assertEqual(self.store.list()[0]["selected"], 1)
        CALLS.clear()
        await self.run_node(1)
        self.assertEqual([v["shape"][-2:] for v in CALLS], [(8, 12)])

    async def test_early_choice_during_low_or_preview_saves_current_and_skips_rest(self):
        for stage in ("low", "preview"):
            with self.subTest(stage=stage):
                CALLS.clear()
                entered, release = threading.Event(), threading.Event()
                original = runtime.progressive_sample

                def wait_for_choice():
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError("Early selection did not release the worker")

                def sampling(*args, **kwargs):
                    if stage == "low" and kwargs.get("stop_after_low") and args[7] == 43:
                        wait_for_choice()
                    return original(*args, **kwargs)

                def decoding(*args):
                    if stage == "preview" and self.store.list()[0]["current"] == 2:
                        wait_for_choice()
                    self.fake_preview(*args)

                with patch.object(runtime, "progressive_sample", side_effect=sampling), \
                     patch.object(preview, "save_preview", side_effect=decoding):
                    task = asyncio.create_task(self.run_node(4, batch_name="early_" + stage))
                    try:
                        async with asyncio.timeout(5):
                            while not entered.is_set() and not task.done():
                                await asyncio.sleep(.01)
                        self.assertTrue(entered.is_set())
                        saved = self.store.list()[0]
                        self.assertEqual(saved["phase"], stage)
                        self.assertEqual([v["ordinal"] for v in saved["candidates"]], [1])
                        with self.assertRaisesRegex(ValueError, "completed preview"):
                            hunt.approve(self.store, saved["id"], 2)
                        hunt.approve(self.store, saved["id"], 1)
                        # Choice is durable even while the sampling worker is busy.
                        self.assertEqual(HuntStore(self.root).read(saved["id"])["selected"], 1)
                        self.assertFalse(task.done())
                    finally:
                        release.set()
                        result = await asyncio.wait_for(task, 5)

                saved = self.store.list()[0]
                self.assertEqual(saved["phase"], "finished")
                self.assertEqual([v["ordinal"] for v in saved["candidates"]], [1, 2])
                for take in saved["candidates"]:
                    self.assertTrue((self.store.locate(saved["id"]) / take["checkpoint"]).is_file())
                    self.assertTrue((self.root / take["preview"]).is_file())
                self.assertFalse((self.store.locate(saved["id"]) / "take_0003.safetensors").exists())
                self.assertEqual([v["shape"][-2:] for v in CALLS], [(4, 6), (4, 6), (8, 12)])
                self.assertIn("take 1; seed 42", result["result"][1])

    async def test_choice_at_next_candidate_boundary_starts_no_extra_low_pass(self):
        original = HuntStore.update
        claims = 0
        def select_before_next_claim(store, key, transform):
            nonlocal claims
            if transform.__name__ == "begin_candidate":
                claims += 1
                if claims == 2:
                    hunt.approve(store, key, 1)
            return original(store, key, transform)
        with patch.object(HuntStore, "update", select_before_next_claim):
            await asyncio.wait_for(self.run_node(4), 5)
        self.assertEqual([v["shape"][-2:] for v in CALLS], [(4, 6), (8, 12)])
        self.assertEqual(len(self.store.list()[0]["candidates"]), 1)

    async def test_early_choice_survives_current_candidate_oom_and_requeue(self):
        for stage in ("low", "preview"):
            with self.subTest(stage=stage):
                original = runtime.progressive_sample
                def choose_then_fail():
                    saved = self.store.list()[0]
                    hunt.approve(self.store, saved["id"], 1)
                    raise RuntimeError("simulated current candidate OOM")
                def sampling(*args, **kwargs):
                    if stage == "low" and kwargs.get("stop_after_low") and args[7] == 43:
                        choose_then_fail()
                    return original(*args, **kwargs)
                def decoding(*args):
                    if stage == "preview" and self.store.list()[0]["current"] == 2:
                        choose_then_fail()
                    self.fake_preview(*args)
                name = "early_oom_" + stage
                with patch.object(runtime, "progressive_sample", side_effect=sampling), \
                     patch.object(preview, "save_preview", side_effect=decoding):
                    with self.assertRaisesRegex(RuntimeError, "current candidate OOM"):
                        await self.run_node(4, batch_name=name)
                saved = HuntStore(self.root).list()[0]
                self.assertEqual((saved["phase"], saved["selected"]), ("paused", 1))
                self.assertNotIn(saved["id"], hunt._ACTIVE)
                CALLS.clear()
                await self.run_node(4, batch_name=name)
                self.assertEqual([v["shape"][-2:] for v in CALLS], [(8, 12)])
                self.assertEqual(len(self.store.list()[0]["candidates"]), 1)

    async def test_cannot_change_selection_once_high_pass_has_started(self):
        original = runtime.progressive_sample
        def sampling(*args, **kwargs):
            if kwargs.get("handoff") is not None:
                saved = self.store.list()[0]
                self.assertEqual(saved["phase"], "high")
                with self.assertRaisesRegex(ValueError, "already being upscaled"):
                    hunt.approve(self.store, saved["id"], 2)
                self.assertEqual(self.store.read(saved["id"])["selected"], 1)
            return original(*args, **kwargs)
        with patch.object(runtime, "progressive_sample", side_effect=sampling):
            await self.select_when_ready(asyncio.create_task(self.run_node(2)))

    async def test_bundle_roundtrip_and_interrupted_atomic_save(self):
        path = self.root / "middle.safetensors"
        value = {"latent": self.latent, "positive": self.positive, "sigmas": self.sigmas,
                 "tuple": (1, None), "ints": {5: "five"}}
        store_module.save_bundle(path, value)
        before = path.read_bytes()
        with patch.object(store_module.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                store_module.save_bundle(path, {"changed": torch.ones(5)})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.root.glob("*.tmp")), [])
        loaded = store_module.load_bundle(path)
        self.assertEqual(loaded["tuple"], (1, None))
        self.assertEqual(loaded["ints"], {5: "five"})
        torch.testing.assert_close(loaded["latent"]["samples"].unbind()[0], self.video)
        torch.testing.assert_close(loaded["positive"][0][1]["minimax_keyframes"][0]["latent"], self.video)
        with self.assertRaises(TypeError):
            store_module.save_bundle(path, {"unsafe": object()})
        self.assertEqual(path.read_bytes(), before)

    async def test_recipe_is_virtual_id_independent_but_tracks_lora_settings(self):
        prompt = {"1": {"class_type": "Loader", "inputs": {"model": "h3"}},
                  "2": {"class_type": "Lora", "inputs": {"model": ["1", 0], "strength": .8}},
                  "3": {"class_type": "Hunt", "inputs": {"model": ["2", 0], "candidate_count": 2}}}
        expected = hunt.source_recipe(prompt, "3")
        prompt["3"]["inputs"]["auto_remove_saved_takes"] = True
        prompt["3"]["inputs"]["review_enabled"] = False
        self.assertEqual(hunt.source_recipe(prompt, "3"), expected)
        moved = {"v"+k: json.loads(json.dumps(v)) for k, v in prompt.items()}
        moved["v2"]["inputs"]["model"][0] = "v1"
        moved["v3"]["inputs"]["model"][0] = "v2"
        self.assertEqual(hunt.source_recipe(moved, "v3"), expected)
        moved["v2"]["inputs"]["strength"] = .4
        self.assertNotEqual(hunt.source_recipe(moved, "v3"), expected)

    async def test_tiny_token_timing_and_no_full_clip_decode(self):
        class Decoder:
            latent_channels = 24
            def decode(self, latent):
                return torch.ones(1, 3, 8, 12) * float(latent[0, 0, 0, 0])
        video = torch.arange(7).reshape(1, 1, 7, 1, 1).expand(1, 24, 7, 2, 2) / 10
        frames = list(preview.preview_frames(Decoder(), video, 22))
        self.assertEqual(len(frames), 22)
        self.assertEqual([int(f[0, 0, 0]) for f in frames], [0] + [25]*4 + [51]*4 + [76]*4 + [102]*4 + [127] + [153]*4)


if __name__ == "__main__":
    unittest.main()
