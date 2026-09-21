#!/usr/bin/env python3
"""Exact manual context takes: synthetic checkpoints only, no GPU or projects."""
import asyncio
import json
import unittest
from unittest.mock import patch

import _resume_verification_unit_test as resume

chain, fixture = resume.chain, resume.fixture


class ContextTakeTests(unittest.TestCase):
    setUp = resume.ResumeVerificationTests.setUp

    def pin(self):
        metadata, _ = fixture.write_revision(
            self.run, 2, "a" * 32, 42, run_name=self.run.name,
            compatibility=self.plan["compatibility"], context_length=0,
            audio_context_length=0, generated_continuity="off")
        segment = metadata["segment"]
        checkpoint = self.root / segment["checkpoint"]
        from h3_checkpoint_revision_unit.selflift_state import LOW_CARRY, SIGNATURE
        tensors = {
            "context_frames": chain.torch.full((5, 2, 2, 3), 0.75),
            "video": chain.torch.full((1, 24, 12, 1, 1), 7.),
            "audio": chain.torch.full((1, 32, 2, 65), 8.),
            LOW_CARRY: chain.torch.full((1, 24, 12, 1, 1), 9.),
            SIGNATURE: chain.torch.tensor(list(b"test-grid"), dtype=chain.torch.uint8),
        }
        chain._st_save(tensors, str(checkpoint))
        segment["checkpoint_sha256"] = fixture.digest(checkpoint)
        segment["history_hash"] = metadata["history_hash"] = "saved-alternate-history"
        (self.root / segment["revision_metadata"]).write_text(json.dumps(metadata))
        shot = self.plan["shots"][2]
        shot.pop("visual_context_source", None)
        shot["context_take"] = {"source": "scene_2", "revision": "a" * 32}
        return segment

    def test_exact_take_context_leaves_edit_pointers_and_low_carry_intact(self):
        segment = self.pin()
        before = {p: p.read_bytes() for p in self.run.rglob("*") if p.is_file()}
        state = chain._load_resume_state(self.plan, 3, context_only=True)
        selected = chain._selected_context_state(state)
        self.assertEqual(selected["segments"][-1]["revision"], "a" * 32)
        self.assertEqual(state["segments"][-1]["revision"], "%032x" % 2)
        self.assertTrue(chain.torch.all(selected["previous_latent"]["samples"][0] == 7))
        self.assertTrue(chain.torch.all(selected["previous_latent"]["samples"][1] == 8))
        self.assertTrue(chain.torch.all(state["previous_latent"]["samples"][0] == 0))
        from h3_checkpoint_revision_unit.selflift_state import LOW_CARRY, SIGNATURE
        self.assertTrue(chain.torch.all(selected["previous_latent"][LOW_CARRY] == 9))
        self.assertEqual(selected["previous_latent"][SIGNATURE], "test-grid")
        self.assertIs(chain._selected_context_state(state), selected)
        self.assertEqual(before, {p: p.read_bytes() for p in self.run.rglob("*") if p.is_file()})
        preview = chain._context_take_preview(self.run.name, 2, "a" * 32)
        self.assertIn("a" * 32, preview["video"]["filename"])
        self.assertEqual(segment["checkpoint"], selected["segments"][-1]["checkpoint"])

    def test_resume_uses_saved_context_despite_source_prompt_change(self):
        self.pin()
        self.plan["shots"][1]["prompt"] = "Different current prompt"
        report = {"errors": [], "warnings": []}
        result = chain._preflight_resume(self.plan, 3, True, report)
        self.assertTrue(result["eligible"], report)
        self.assertFalse(report["errors"])
        state = chain._load_resume_state(self.plan, 3, context_only=True)
        self.assertEqual(chain._selected_context_state(state)["segments"][-1]["revision"], "a" * 32)

    def test_missing_or_corrupt_exact_take_never_falls_back(self):
        segment = self.pin()
        self.plan["shots"][2]["context_take"]["revision"] = "b" * 32
        with self.assertRaises((ValueError, FileNotFoundError)):
            chain._load_resume_state(self.plan, 3, context_only=True)
        self.plan["shots"][2]["context_take"]["revision"] = "a" * 32
        (self.root / segment["checkpoint"]).write_bytes(b"corrupt")
        report = {"errors": [], "warnings": []}
        result = chain._preflight_resume(self.plan, 3, True, report)
        self.assertFalse(result["eligible"])
        self.assertIn("context_take_unavailable", [item["code"] for item in report["errors"]])

    def test_pin_hash_and_recovery_are_separate_from_source_recipe(self):
        before = chain._history_hash(self.plan, 2)
        self.pin()
        self.assertEqual(before, chain._history_hash(self.plan, 2))
        first = chain._scene_dependency_record(self.plan, 3)
        self.plan["shots"][2]["context_take"]["revision"] = "b" * 32
        second = chain._scene_dependency_record(self.plan, 3)
        diffs = chain._scene_dependency_diffs(first, second)
        self.assertTrue(diffs)
        self.assertEqual({item["scope"] for item in diffs}, {"incoming_boundary"})
        pin = self.plan["shots"][2]["context_take"]
        self.assertEqual(chain._effective_editor_plan(self.plan)["shots"][2]["context_take"], pin)
        saved = {**self.segments[1], "context_take": pin}
        self.assertEqual(chain._public_segment(saved)["context_take"], pin)
        self.assertEqual(chain._checkpoint_plan_revision(saved)["context_take"], pin)

    def test_disabled_context_does_not_load_unused_pin(self):
        self.pin()
        self.plan["shots"][2]["context_length"] = 0
        state = {"plan": self.plan, "index": 3, "segments": self.segments}
        with patch.object(chain, "_load_checkpoint_revision", side_effect=AssertionError("unused")):
            self.assertIs(chain._context_take_state(state), state)

    def test_revision_validation_and_read_only_preview_route(self):
        self.pin()
        from h3_checkpoint_revision_unit.context_take import context_take
        for value in ([], True, {"source": True, "revision": "a" * 32},
                      {"source": 1, "revision": "../bad"}):
            with self.assertRaises(ValueError):
                context_take(value)
        request = type("Request", (), {"query": {"run_name": self.run.name,
            "context_scene": "2", "context_revision": "a" * 32}})()
        response = asyncio.run(chain._list_saved_checkpoints(request))
        self.assertEqual(response.status, 200, response.text)
        self.assertEqual(json.loads(response.text)["context_take"]["revision"], "a" * 32)

    def test_saved_consumers_protect_pin_without_requiring_it_in_final_cut(self):
        alternate = self.pin()
        parent = chain._read_json(str(self.root / self.segments[1]["metadata"]))
        parent["segment"].update(predecessor_revision=self.segments[0]["revision"],
            predecessor_checkpoint_sha256=self.segments[0]["checkpoint_sha256"])
        for key in ("metadata", "revision_metadata"):
            (self.root / parent["segment"][key]).write_text(json.dumps(parent))
        metadata, _ = fixture.write_revision(self.run, 3, "c" * 32, 99,
            predecessor=parent, run_name=self.run.name, context_length=5,
            audio_context_length=5, generated_continuity="on")
        segment = metadata["segment"]
        segment.update(context_take={"source":"scene_2", "revision":"a" * 32},
            resolved_context_length=5, resolved_audio_context_length=5,
            continuation_mode="guide", generated_continuity="on")
        for kind in ("visual", "audio"):
            segment.update({kind + "_context_source_scene":2,
                kind + "_context_source_revision":"a" * 32,
                kind + "_context_source_checkpoint_sha256":alternate["checkpoint_sha256"],
                kind + "_context_source_editorial_out_frames":340})
        (self.root / segment["revision_metadata"]).write_text(json.dumps(metadata))
        manager = chain.CheckpointGraphManager(str(self.root))
        records = manager._scan(self.run.name, adopt_legacy=False)["records"]
        consumer = records[(3, "c" * 32)]
        self.assertIn((2, "a" * 32), consumer["_dependencies"])
        self.assertIn((3, "c" * 32), records[(2, "a" * 32)]["_dependents"])
        audio_only = {**consumer, "context_length":0,
                      "_segment":{**segment, "resolved_context_length":0}}
        self.assertEqual(manager._recorded_dependency_keys(
            audio_only, (2, "%032x" % 2), records, {}), [(2, "a" * 32)])
        self.assertIsNone(manager._attribution_blocker(records, (2, "%032x" % 2), consumer))
        assigned = {2: {**self.segments[1], "_editorial_out_frames":100}}
        self.assertEqual(chain._editorial_dependency_mismatches(segment, 3, assigned), [])
        self.assertEqual(chain._editorial_dependency_sources(segment, 3), set())
        del segment["context_take"]
        self.assertTrue(chain._editorial_dependency_mismatches(segment, 3, assigned))

    def test_normalization_keeps_exact_pin(self):
        self.pin()
        document = chain._effective_editor_plan(self.plan)
        normalized = chain._normalize_plan(json.dumps(document), self.run.name,
            64, 64, 1, "video", "head", "disabled", "generated_audio", 0,
            2.0, 8, 7, 18, "test-stack", 0, "guide")
        self.assertEqual(normalized["shots"][2]["context_take"],
                         self.plan["shots"][2]["context_take"])


if __name__ == "__main__":
    unittest.main()
