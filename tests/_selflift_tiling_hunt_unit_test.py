"""Tiling only changes finishing cache identity; old low passes remain reusable."""
import importlib
import json
import types
import unittest
from unittest.mock import patch

import _selflift_hunt_unit_test as fixtures

tiling = importlib.import_module(fixtures.PACKAGE + ".selflift_runtime.h3_tiling")
ON = {"enabled": True, "tiles": 2, "overlap": 2, "axis": "longest"}
fixtures.stub("comfy.model_base", MiniMaxH3=types.SimpleNamespace)
fixtures.runtime.comfy.patcher_extension.WrappersMP.DIFFUSION_MODEL = "diffusion"
fixtures.runtime.comfy.patcher_extension.WrappersMP.PREPARE_SAMPLING = "prepare"


class TilingHuntTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.HuntTests.setUp
    fake_preview = fixtures.HuntTests.fake_preview
    run_node = fixtures.HuntTests.run_node

    async def test_finishing_identity_reuses_low_and_disconnect_restores_legacy(self):
        await self.run_node(1, review_enabled=False)
        original = self.store.list()[0]
        folder = self.store.locate(original["id"])
        low_bytes = (folder / "take_0001.safetensors").read_bytes()
        high_bytes = (folder / "finished_0001.safetensors").read_bytes()
        previous_ids = set()
        for settings in (ON, {**ON, "tiles": 3}, {**ON, "overlap": 0}, {**ON, "axis": "height"}):
            fixtures.CALLS.clear()
            with patch.object(tiling, "tiled_model", wraps=tiling.tiled_model) as patched:
                result = await self.run_node(1, review_enabled=False, highres_tiling=settings)
                patched.assert_called_once()
            self.assertIn("tiled high-resolution", result["result"][1])
            self.assertEqual([c["shape"][-2:] for c in fixtures.CALLS], [(8, 12)])
            record = self.store.list()[0]
            self.assertEqual(record["id"], original["id"])
            self.assertNotIn(record["finishing_id"], previous_ids)
            previous_ids.add(record["finishing_id"])
            recovery = json.loads((folder / "recovery.json").read_text())
            self.assertEqual(recovery["highres_tiling"], settings)
            fixtures.CALLS.clear()
            await self.run_node(1, review_enabled=False, highres_tiling=settings)
            self.assertEqual(fixtures.CALLS, [])
        for disabled in (None, {"enabled": False}):
            await self.run_node(1, review_enabled=False, highres_tiling=disabled)
            self.assertEqual(fixtures.CALLS, [])
            self.assertIsNone(self.store.list()[0]["finishing_id"])
        self.assertEqual((folder / "take_0001.safetensors").read_bytes(), low_bytes)
        self.assertEqual((folder / "finished_0001.safetensors").read_bytes(), high_bytes)

    async def test_tiled_failure_preserves_low_for_retry(self):
        with patch.object(tiling, "tiled_model", side_effect=RuntimeError("tiled GPU failure")):
            with self.assertRaisesRegex(RuntimeError, "tiled GPU failure"):
                await self.run_node(1, review_enabled=False, highres_tiling=ON)
        self.assertEqual(self.store.list()[0]["phase"], "paused")
        fixtures.CALLS.clear()
        await self.run_node(1, review_enabled=False, highres_tiling=ON)
        self.assertEqual([c["shape"][-2:] for c in fixtures.CALLS], [(8, 12)])

    async def test_invalid_setup_fails_before_creating_hunt(self):
        self.positive[0][1]["control"] = object()
        with self.assertRaisesRegex(ValueError, "ControlNet or regional"):
            await self.run_node(1, review_enabled=False, highres_tiling=ON)
        self.assertEqual(self.store.list(), [])
        self.assertEqual(fixtures.CALLS, [])


if __name__ == "__main__":
    unittest.main()
