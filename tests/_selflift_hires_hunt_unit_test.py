"""Finishing-model cache identity/recovery; no production files or GPU."""
import copy
import importlib
import json
import logging
import unittest
from unittest.mock import patch

import _selflift_hunt_unit_test as fixtures
from _selflift_hires_unit_test import Checkpoint


class HiresHuntTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.HuntTests.setUp
    fake_preview = fixtures.HuntTests.fake_preview

    def prompt(self, high=None, strength=1.):
        own = {"model": ["low", 0]}
        if high is not None:
            own["model_hires"] = ["high_lora", 0]
        return {
            "low": {"class_type": "UNETLoader", "inputs": {"unet_name": "base.safetensors"}},
            "high": {"class_type": "UNETLoader", "inputs": {"unet_name": high}},
            "high_lora": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["high", 0], "strength_model": strength}},
            "hunt": {"class_type": "MiniMaxH3SelfLiftSeedHunt", "inputs": own},
        }

    async def run_node(self, high=None, prompt=None, **kwargs):
        kwargs.setdefault("review_enabled", False)
        return await fixtures.hunt.MiniMaxH3SelfLiftSeedHunt().sample(
            self.state, Checkpoint("low"), self.positive, object(), self.latent,
            fixtures.Euler(), self.sigmas, 42, candidate_count=1,
            model_hires=Checkpoint(high) if high is not None else None,
            prompt=self.prompt(high) if prompt is None else prompt,
            unique_id="hunt", extra_pnginfo={"workflow": {"nodes": [], "test_finishing_checkpoint": high}}, **kwargs)

    async def test_swap_high_checkpoint_or_loras_reuses_low_not_finished(self):
        # Populate the legacy, unconnected high result first.
        await self.run_node()
        record = self.store.list()[0]
        folder = self.store.locate(record["id"])
        low_before = (folder / "take_0001.safetensors").read_bytes()
        default_before = (folder / "finished_0001.safetensors").read_bytes()
        for name, strength in (("A.safetensors", 1.), ("B.safetensors", 1.), ("B.safetensors", .5)):
            fixtures.CALLS.clear()
            prompt = self.prompt(name, strength)
            with patch.object(fixtures.runtime.comfy.samplers, "sample", wraps=fixtures.runtime.comfy.samplers.sample) as sample:
                result = await self.run_node(name, prompt)
            self.assertEqual([c.args[0].name for c in sample.call_args_list], [name])
            self.assertEqual([c["shape"][-2:] for c in fixtures.CALLS], [(8, 12)])
            self.assertIn("separate finishing checkpoint", result["result"][1])
            fixtures.CALLS.clear()
            # Simulate restart/new model/store objects: exact high recipe reuses.
            self.store = fixtures.HuntStore(self.root)
            await self.run_node(name, prompt)
            self.assertEqual(fixtures.CALLS, [])
            self.assertEqual([r["id"] for r in self.store.list()], [record["id"]])
            recovery = json.loads((folder / "recovery.json").read_text())
            self.assertEqual(recovery["workflow"]["test_finishing_checkpoint"], name)
            self.assertEqual(recovery["prompt"], prompt)
        self.assertEqual(len(list(folder.glob("finished_0001.*.safetensors"))), 3)
        self.assertEqual((folder / "take_0001.safetensors").read_bytes(), low_before)
        self.assertEqual((folder / "finished_0001.safetensors").read_bytes(), default_before)
        fixtures.CALLS.clear()
        await self.run_node()  # Disconnecting restores the original cached high.
        self.assertEqual(fixtures.CALLS, [])

    async def test_missing_vs_explicit_patch_metadata_samples_and_reuses_hunt(self):
        checkpoint_type = Checkpoint
        def checkpoint(name):
            model = checkpoint_type(name)
            if name == "low":
                model.model.model_config.unet_config.pop("patch_size")
            return model
        with patch(__name__ + ".Checkpoint", side_effect=checkpoint):
            await self.run_node("A.safetensors")
            self.assertEqual([c["shape"][-2:] for c in fixtures.CALLS], [(4, 6), (8, 12)])
            record = self.store.list()[0]
            fixtures.CALLS.clear()
            await self.run_node("A.safetensors")
            self.assertEqual(fixtures.CALLS, [])
            self.assertEqual([r["id"] for r in self.store.list()], [record["id"]])

    async def test_high_oom_then_changed_checkpoint_recovers_only_high(self):
        original = fixtures.runtime.progressive_sample
        def fail(*args, **kwargs):
            if kwargs.get("handoff") is not None:
                raise RuntimeError("high OOM")
            self.assertIsNone(kwargs.get("model_hires"), "Low hunting must not stage the finishing model")
            return original(*args, **kwargs)
        with patch.object(fixtures.runtime, "progressive_sample", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "high OOM"):
                await self.run_node("A.safetensors")
        record = self.store.list()[0]
        self.assertEqual((record["selected"], record["phase"]), (1, "paused"))
        fixtures.CALLS.clear()
        await self.run_node("B.safetensors")
        self.assertEqual([c["shape"][-2:] for c in fixtures.CALLS], [(8, 12)])
        self.assertEqual([r["id"] for r in self.store.list()], [record["id"]])

    async def test_cleanup_toggle_keeps_legacy_completed_hunt_identity(self):
        await self.run_node("A.safetensors")
        record = self.store.list()[0]
        fixtures.CALLS.clear()
        memory = importlib.import_module(fixtures.PACKAGE + ".selflift_runtime.memory")
        with patch.object(memory, "release_stage_models") as release:
            for enabled in (False, True, False):
                self.settings["cleanup_between_stages"] = enabled
                await self.run_node("A.safetensors")
                self.assertEqual([r["id"] for r in self.store.list()], [record["id"]])
                self.assertEqual(fixtures.CALLS, [])
            release.assert_not_called()

    async def test_cleanup_failure_preserves_saved_take_then_resume_only_high(self):
        self.settings["cleanup_between_stages"] = True
        memory = importlib.import_module(fixtures.PACKAGE + ".selflift_runtime.memory")
        upscaler = importlib.import_module(fixtures.PACKAGE + ".selflift_runtime.h3_upscaler")
        class Interrupted(Exception):
            pass
        with patch.object(memory, "release_stage_models", side_effect=Interrupted("cancel before unload")) as release:
            with self.assertRaises(Interrupted):
                await self.run_node("A.safetensors")
            # No cleanup during the candidate; exactly one during finishing.
            release.assert_called_once()
        record = self.store.list()[0]
        self.assertEqual((record["phase"], record["selected"]), ("paused", 1))
        folder = self.store.locate(record["id"])
        before = (folder / "take_0001.safetensors").read_bytes()
        self.assertEqual(list(folder.glob("finished*")), [])
        fixtures.CALLS.clear()
        def lift(z, hw, name, **kwargs):
            self.assertTrue(kwargs.pop("cleanup_after"))
            return fixtures.lift(z, hw, **kwargs)
        with patch.object(memory, "release_stage_models") as release, \
                patch.object(upscaler, "learned_latent_lift", side_effect=lift) as lifter:
            await self.run_node("A.safetensors")
            release.assert_called_once()
            lifter.assert_called_once()
        self.assertEqual([c["shape"][-2:] for c in fixtures.CALLS], [(8, 12)])
        self.assertEqual([r["id"] for r in self.store.list()], [record["id"]])
        self.assertEqual((folder / "take_0001.safetensors").read_bytes(), before)

    async def test_subgraph_recipe_independent_of_virtual_ids(self):
        prompt = self.prompt("A.safetensors")
        await self.run_node("A.safetensors", prompt)
        moved = {"virtual/" + key: copy.deepcopy(value) for key, value in prompt.items()}
        for node in moved.values():
            for name, value in node["inputs"].items():
                if isinstance(value, list):
                    value[0] = "virtual/" + value[0]
        # DYNPROMPT exposes the loop/subgraph-expanded nodes; the visible root
        # forwards to the same recipe with virtual upstream IDs.
        moved["hunt"] = moved.pop("virtual/hunt")
        class Dynamic:
            def get_node(self, key):
                return moved[key]
        fixtures.CALLS.clear()
        await self.run_node("A.safetensors", {}, dynprompt=Dynamic())
        self.assertEqual(fixtures.CALLS, [])
        self.assertEqual(len(self.store.list()), 1)

    async def test_missing_finishing_recipe_fails_closed_before_sampling(self):
        for prompt in ({}, {"hunt": self.prompt("A")["hunt"]}):
            with self.subTest(prompt=prompt), self.assertRaisesRegex(ValueError, "recipe"):
                await self.run_node("A", prompt)
            self.assertEqual(fixtures.CALLS, [])
            self.assertEqual(self.store.list(), [])

    async def test_disabled_selflift_ignores_finishing_input_and_recipe(self):
        self.settings["enabled"] = False
        _, status, _ = await self.run_node("unused", {})
        self.assertIn("OFF", status)
        self.assertEqual([c["steps"] for c in fixtures.CALLS], [4])
        self.assertEqual(self.store.list(), [])

    async def test_cleanup_recognizes_variants_and_rejects_stale_finish(self):
        first = await self.run_node("A", auto_remove_saved_takes=True)
        second = await self.run_node("B", auto_remove_saved_takes=True)
        record = self.store.list()[0]
        folder = self.store.locate(record["id"])
        logger = logging.getLogger("test-hunt-finishing-cleanup")
        with self.assertLogs(logger, "WARNING"):
            removed = fixtures.hunt.cleanup_after_segment_save(first["result"][2], self.root, logger)
        self.assertIsNone(removed)
        self.assertTrue(folder.exists())
        removed = fixtures.hunt.cleanup_after_segment_save(second["result"][2], self.root, logger)
        self.assertEqual(removed["files"], 6)
        self.assertFalse(folder.exists())


if __name__ == "__main__":
    unittest.main()
