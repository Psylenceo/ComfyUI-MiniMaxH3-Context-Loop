#!/usr/bin/env python3
"""Fixed context masks: lattice mapping, persistence and dependency identity."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

import torch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("context_mask_test_helpers", ROOT / "tests/_checkpoint_revision_unit_test.py")
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
chain = helpers.chain
from importlib import import_module
masking = import_module(chain.__package__ + ".context_mask")

MASK = {"columns":2, "rows":1, "cells":[16, 0], "strength":0.5}


def plan(mask=MASK, mode="masked_av"):
    block = {"source":"one", "frames":5}
    if mask is not None:
        block["weaken_mask"] = copy.deepcopy(mask)
    return chain._normalize_plan(json.dumps({"shots":[
        {"id":"one", "prompt":"Opening", "length":39, "audio_context_length":0},
        {"id":"two", "prompt":"Hard cut", "length":39,
         "audio_context_length":0, "visual_context_blocks":[block]},
    ]}), "mask-test", 64, 64, 5, "video", "head", "disabled",
        "generated_audio", 0, 1.0, 8, 11, 18, "", 0, mode)


class ContextMaskTests(unittest.TestCase):
    def test_validation_and_empty_mask(self):
        self.assertIsNone(masking.normalize_context_mask(None))
        self.assertIsNone(masking.normalize_context_mask({**MASK, "cells":[0, 0]}))
        self.assertEqual(masking.normalize_context_mask(MASK), MASK)
        for bad in ([], {**MASK, "columns":True}, {**MASK, "rows":513},
                    {**MASK, "cells":[16]}, {**MASK, "cells":[17, 0]},
                    {**MASK, "cells":[False, 0]}, {**MASK, "strength":float("nan")},
                    {**MASK, "strength":True}, {**MASK, "strength":-1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                masking.normalize_context_mask(bad)

    def test_two_blocks_only_second_is_released(self):
        mask = torch.zeros(1, 1, 12, 4, 4)
        mask[:, :, 7:] = 1
        masking.release_context_regions(mask, [{"frames":5}, {"frames":17, "weaken_mask":MASK}])
        self.assertEqual(torch.count_nonzero(mask[:, :, :2]), 0)
        self.assertTrue(torch.all(mask[:, :, 2:7, :, :2] == 0.5))
        self.assertEqual(torch.count_nonzero(mask[:, :, 2:7, :, 2:]), 0)
        self.assertTrue(torch.all(mask[:, :, 7:] == 1))

    def test_soft_cells_token_snap_and_feather_preserved(self):
        mask = torch.zeros(1, 1, 7, 3, 5)
        mask[:, :, 1] = 0.9
        masking.release_context_regions(mask, [{"frames":5, "weaken_mask":{
            "columns":3, "rows":2, "cells":[16, 8, 0, 0, 4, 16], "strength":1}}])
        self.assertTrue(torch.all(mask[:, :, 0, :2, :2] == 1))
        self.assertTrue(torch.all(mask[:, :, 0, :2, 2:4] == 0.5))
        self.assertTrue(torch.all(mask[:, :, 1] >= 0.9))
        self.assertEqual(torch.count_nonzero(mask[:, :, 2:]), 0)

    def test_unaligned_block_rejected(self):
        with self.assertRaisesRegex(ValueError, "latent-aligned"):
            masking.release_context_regions(torch.zeros(1, 1, 7, 2, 2),
                                            [{"frames":3, "weaken_mask":MASK}])

    def test_normalize_archive_and_history_roundtrip(self):
        painted, clean = plan(), plan(None)
        self.assertEqual(painted["shots"][1]["visual_context_blocks"][0]["weaken_mask"], MASK)
        archived = chain._effective_editor_plan(painted)
        self.assertEqual(json.loads(json.dumps(archived))["shots"][1]["visual_context_blocks"][0]["weaken_mask"], MASK)
        self.assertEqual(chain._history_hash(painted, 1), chain._history_hash(clean, 1))
        self.assertNotEqual(chain._history_hash(painted, 2), chain._history_hash(clean, 2))
        self.assertEqual(painted["shots"][1]["raw_frames"], clean["shots"][1]["raw_frames"])
        self.assertEqual(painted["shots"][1]["delivered_frames"], clean["shots"][1]["delivered_frames"])
        painted_dep = chain._scene_dependency_record(painted, 2)
        clean_dep = chain._scene_dependency_record(clean, 2)
        self.assertNotEqual(painted_dep["generation_hash"], clean_dep["generation_hash"])
        for scope in ("global_generation", "scene_generation", "assembly_only"):
            self.assertEqual(painted_dep["scopes"][scope], clean_dep["scopes"][scope])
        # Clearing a mask is exactly the old unpainted history contract.
        self.assertEqual(chain._history_hash(plan({**MASK, "cells":[0, 0]}), 2),
                         chain._history_hash(clean, 2))

    def test_invalid_mode_not_silently_ignored(self):
        with self.assertRaisesRegex(ValueError, "require Masked AV"):
            plan(mode="guide")

    def test_chain_context_forwards_authored_mask_to_av_prefix(self):
        # Drive the real node's routing; tensor application is tested separately
        # in _masked_prefix_unit_test, without ComfyUI model imports.
        prefix = Mock(return_value=(["conditioned"], {"prepared":True}, 5))
        module = types.ModuleType(chain.__package__ + ".masked_context")
        module.apply_masked_prefix = prefix
        previous = {"previous_latent":{"saved":True}}
        with patch.dict(sys.modules, {module.__name__:module}), \
                patch.object(chain, "_selected_context_state", return_value=previous), \
                patch.object(chain, "_previous_context_frames", return_value="preview"):
            for authored_mask in (MASK, None):
                prefix.reset_mock()
                normalized = plan(authored_mask)
                result = chain.MiniMaxH3ChainContext().apply(
                    {"plan":normalized, "index":2}, ["input"], object(), {"target":True})
                self.assertEqual(result[1:4], (5, True, {"prepared":True}))
                args = prefix.call_args.kwargs
                self.assertIs(args["previous_latent"], previous["previous_latent"])
                self.assertFalse(args["preserve_audio_prefix"])
                if authored_mask:
                    self.assertEqual(args["context_masks"][0]["weaken_mask"], MASK)
                else:
                    self.assertIsNone(args["context_masks"])

    def test_saved_checkpoint_revision_retains_independent_mask_copy(self):
        segment = {"index":2, "id":"two", "segment":"h3_chains/mask-test/segments/clip_0002.mp4",
                   "visual_context_blocks":[{"source_id":"one", "frames":5, "weaken_mask":copy.deepcopy(MASK)}]}
        revision = chain._checkpoint_plan_revision(segment)
        mask = revision["visual_context_blocks"][0]["weaken_mask"]
        self.assertEqual(mask, MASK)
        mask["cells"][0] = 0
        self.assertEqual(segment["visual_context_blocks"][0]["weaken_mask"], MASK)

    def test_branch_assignment_recovery_retains_mask_and_future_edits(self):
        recover = import_module(chain.__package__ + ".branch_authoring_recovery").recover_authoring
        authoring = {"plan_json":json.dumps({"shots":[
            {"id":"one", "prompt":"Opening"}, {"id":"two", "prompt":"Edited"},
            {"id":"three", "prompt":"Unrendered future", "note":"Keep"},
        ]})}
        segment = {"index":2, "id":"two", "seed":"18446744073709551615", "steps":8,
                   "raw_frames":39, "scene_prompt":"Saved prompt", "context_length":5,
                   "audio_context_length":0, "continuation_mode":"masked_av",
                   "visual_context_blocks":[{"source_id":"one", "frames":5,
                                             "weaken_mask":copy.deepcopy(MASK)}]}
        recovered = json.loads(recover(authoring, {2:{"segment":segment}})["plan_json"])
        self.assertEqual(recovered["shots"][1]["visual_context_blocks"][0]["weaken_mask"], MASK)
        self.assertEqual(recovered["shots"][1]["seed"], segment["seed"])
        self.assertEqual(recovered["shots"][1]["prompt"], "Saved prompt")
        self.assertEqual(recovered["shots"][2], json.loads(authoring["plan_json"])["shots"][2])
        self.assertEqual(segment["visual_context_blocks"][0]["weaken_mask"], MASK)


if __name__ == "__main__":
    unittest.main()
