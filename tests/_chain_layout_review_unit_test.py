"""Original and named-branch Gate recovery across all storage layouts."""
import asyncio
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import _review_gate_unit_test as helper

chain = helper.chain
layout = importlib.import_module(chain.__package__ + ".chain_layout")
conversion = importlib.import_module(chain.__package__ + ".chain_layout_conversion")


class ReviewLayoutTests(unittest.TestCase):
    def test_restart_recovers_original_and_branch_without_reenabling_actions(self):
        for mode in ("legacy", "organized", "converted"):
            with self.subTest(layout=mode), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "output"
                project = output / "h3_chains/review_test"
                if mode == "organized":
                    layout.create_project(project)
                else:
                    project.mkdir(parents=True)
                branch = chain.WorkingBranches(output, "review_test").create(
                    "main", "Alternate", {"plan_json": json.dumps({"shots": [
                        {"id": "scene_1", "prompt": "Test"}]}), "width": 64, "height": 64})["id"]
                for scope in ("main", branch):
                    directory = Path(layout.state_root(project))
                    if scope != "main":
                        directory /= "branches/" + scope
                    helper.review_inv.write_review_snapshot(
                        directory, "pending-" + scope, "review_test", 1,
                        [{"number": 1, "revision": "a" * 32, "seed": "9", "has_audio": False}],
                        deadline=None, server_now=10.0)
                before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
                if mode == "converted":
                    destination = Path(temporary) / "converted"
                    conversion.convert_copy(project, destination)
                    output = destination
                with patch.object(chain, "_output_root", return_value=str(output)), \
                        patch.dict(chain._PENDING_REVIEWS, {}, clear=True), \
                        patch.dict(chain._ACTIVE_CANDIDATE_BATCHES, {}, clear=True):
                    response = asyncio.run(chain._list_pending_reviews(None))
                    self.assertEqual(response.status, 200)
                    recovered = json.loads(response.text)["reviews"]
                    self.assertEqual({item["_branch_id"] for item in recovered}, {"main", branch})
                    for item in recovered:
                        self.assertTrue(item["durable"])
                        self.assertFalse(item["actionable"])
                        self.assertEqual(item["candidates"][0]["revision"], "a" * 32)
                        self.assertIsNone(item["video"])
                    directory = Path(layout.state_root(output / "h3_chains/review_test"))
                    helper.review_inv.mark_review_snapshot_decided(
                        directory, "pending-main", "approve", 11.0)
                    response = asyncio.run(chain._list_pending_reviews(None))
                    self.assertEqual([item["_branch_id"] for item in json.loads(response.text)["reviews"]], [branch])
                if mode == "converted":
                    self.assertEqual(before, {p: p.read_bytes() for p in project.rglob("*") if p.is_file()})


if __name__ == "__main__":
    unittest.main()
