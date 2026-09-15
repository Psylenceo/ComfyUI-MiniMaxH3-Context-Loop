#!/usr/bin/env python3
"""Reuse a saved chapter suffix when its actual context sources are unchanged."""
import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "context_reattribution_helpers", ROOT / "tests/_checkpoint_local_output_unit_test.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
chain = h.chain


async def main(after_activation=None):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        h.h.folder_paths.output_directory = directory
        run = root / "h3_chains" / "context_reattribution"
        originals = {}
        sources = {11: 9, 12: 9, 14: 9, 15: 14, 17: 15, 18: 9}
        for scene in range(1, 19):
            item, _ = h.h.write_revision(
                run, scene, f"{scene:032x}", scene, active=True,
                predecessor=originals.get(scene - 1), run_name=run.name,
                context_length=5 if scene in sources else 0,
                audio_context_length=0, generated_continuity="on")
            segment = item["segment"]
            segment.update({"resolved_context_length":segment["context_length"],
                            "resolved_audio_context_length":0,
                            "continuation_mode":"audio_feathered_av"})
            if scene in sources:
                source = originals[sources[scene]]["segment"]
                segment["visual_context_blocks"] = [{
                    "source_scene":sources[scene], "source_revision":source["revision"],
                    "source_checkpoint_sha256":source["checkpoint_sha256"],
                    "source_editorial_out_frames":source["delivered_frames"],
                    "frames":5, "start_frame":199, "resolved_start_frame":199,
                }]
            for path in (segment["revision_metadata"], segment["metadata"]):
                (root / path).write_text(json.dumps(item))
            originals[scene] = item
        replacement, _ = h.h.write_revision(
            run, 12, "a" * 32, 1200, active=True, predecessor=originals[11],
            run_name=run.name, context_length=0, audio_context_length=0)
        (run / "plan.json").write_text(json.dumps({"run_name":run.name,
            "shots":[{"id":f"scene_{i}"} for i in range(1, 19)]}))
        (run / "editorial.json").write_text(json.dumps({
            "scene_order":[{"scene":i, "scene_id":f"scene_{i}"} for i in range(1, 19)],
            "chapters":[{"id":"one", "start_scene":1, "start_scene_id":"scene_1"},
                        {"id":"two", "start_scene":8, "start_scene_id":"scene_8"}],
        }))
        manager = chain.CheckpointGraphManager(directory)
        manager.graph(run.name)
        before = h.snapshot(run)
        selected = {i:originals[i]["segment"]["revision"] for i in range(1, 12)}
        selected[12] = replacement["segment"]["revision"]
        # The original regression: 13 is independent, 14 uses unchanged 9,
        # then 15/17 consume the SAME media through newly attributed aliases.
        for scene in range(13, 19):
            parent = selected[scene - 1]
            candidate = originals[scene]["segment"]["revision"]
            graph = manager.graph(run.name)
            branch = next(b for b in graph["branches"] if b["leaf_revision"] == parent)
            assert {"scene":scene, "revision":candidate} in branch["attribution_slot"]["candidates"], scene
            attached = manager.attribute(run.name, scene - 1, parent, scene, candidate)
            selected[scene] = attached["revision"]
            assert attached["created"]
            repeated = manager.attribute(run.name, scene - 1, parent, scene, candidate)
            assert not repeated["created"] and repeated["revision"] == attached["revision"]
            alias = json.loads((run / "checkpoints" / f"clip_{scene:04d}.{attached['revision']}.json").read_text())
            for field in ("checkpoint", "segment", "prompt_file", "seed", "prompt",
                          "checkpoint_sha256", "visual_context_blocks"):
                assert alias["segment"].get(field) == originals[scene]["segment"].get(field), field
        after = h.snapshot(run)
        assert len(after) == len(before) + 6
        assert all(after[path] == value for path, value in before.items())

        # Real output node consumes the entire attributed chapter, unchanged
        # context metadata included. It never publishes project pointers.
        selection = {"run_name":run.name, "output_mode":"workflow_local",
                     "output_scope":"chapter", "scope_start_scene":8, "scope_end_scene":18,
                     "lineage":[{"scene":i, "revision":r} for i, r in selected.items()]}
        with patch.object(chain, "_atomic_json", side_effect=AssertionError("output wrote files")):
            manifest = chain.MiniMaxH3ChainCheckpointManager().passthrough(json.dumps(selection))[0]
        assert [s["index"] for s in manifest["segments"]] == list(range(8, 19))
        assert manifest["segments"][-1]["revision"] == selected[18]
        assert h.snapshot(run) == after

        # Negative checks use an in-memory scan; graph eligibility and POST
        # must both reject changed, missing, incomplete or mismatched sources.
        scan = manager._scan(run.name)
        parent_key = (13, selected[13])
        candidate_key = (14, originals[14]["segment"]["revision"])
        source_key = (9, selected[9])

        def rejected_scan(modify, message):
            invalid = copy.deepcopy(scan)
            modify(invalid["records"])
            with patch.object(manager, "_scan", return_value=invalid):
                h.rejected(lambda: manager.attribute(run.name, 13, selected[13], 14,
                                                     candidate_key[1]), message)
            slot = manager._attribution_slot(invalid["records"], parent_key)
            assert slot and any(message in b["reason"] for b in slot["blocked_candidates"])
            assert h.snapshot(run) == after

        rejected_scan(lambda r: r[source_key].update(checkpoint_sha256="f" * 64), "different checkpoint")
        rejected_scan(lambda r: r[source_key].update(ready=False), "not available")
        rejected_scan(lambda r: r.pop(source_key), "not available")
        rejected_scan(lambda r: r[candidate_key]["_segment"].update(visual_context_blocks=[]), "consumed frames")
        rejected_scan(lambda r: r[candidate_key]["_segment"]["visual_context_blocks"][0].update(frames=12), "consumed frames")
        rejected_scan(lambda r: r[candidate_key]["_segment"]["visual_context_blocks"][0].update(
            source_revision="", source_checkpoint_sha256=""), "no revision")
        rejected_scan(lambda r: r[candidate_key]["_segment"]["visual_context_blocks"][0].update(
            source_scene="broken"), "invalid")
        rejected_scan(lambda r: r[candidate_key]["_segment"].update(resolved_audio_context_length="bad"), "cannot prove")

        # Legacy implicit predecessor, explicit single source, composed lead,
        # independent audio, and metadata-only aliases follow the same rule.
        records = scan["records"]
        record = copy.deepcopy(records[candidate_key])
        segment = record["_segment"]
        segment.pop("visual_context_blocks")
        assert manager._attribution_blocker(records, parent_key, record) is None  # reused 13
        segment.update(visual_context_source_scene=12,
                       visual_context_source_revision=originals[12]["segment"]["revision"],
                       visual_context_source_checkpoint_sha256=originals[12]["segment"]["checkpoint_sha256"])
        assert "different checkpoint" in manager._attribution_blocker(records, parent_key, record)
        segment.update(visual_context_source_scene=9, visual_context_source_revision=source_key[1],
                       visual_context_source_checkpoint_sha256=records[source_key]["checkpoint_sha256"])
        assert manager._attribution_blocker(records, parent_key, record) is None
        for kind in ("visual", "audio"):
            segment.update(resolved_context_length=5 if kind == "visual" else 0,
                           resolved_audio_context_length=5 if kind == "audio" else 0,
                           continuation_mode="guide")
            segment[f"{kind}_context_lead_frames"] = 1
            segment[f"{kind}_context_lead_source_scene"] = 12
            segment[f"{kind}_context_lead_source_revision"] = originals[12]["segment"]["revision"]
            segment[f"{kind}_context_lead_checkpoint_sha256"] = originals[12]["segment"]["checkpoint_sha256"]
            assert "different checkpoint" in manager._attribution_blocker(records, parent_key, record)
            segment[f"{kind}_context_lead_frames"] = 0
            assert manager._attribution_blocker(records, parent_key, record) is None
        segment.update(audio_context_source_scene=9, audio_context_source_revision=source_key[1],
                       audio_context_source_checkpoint_sha256=records[source_key]["checkpoint_sha256"])
        assert manager._attribution_blocker(records, parent_key, record) is None
        segment["audio_context_source_checkpoint_sha256"] = "f" * 64
        assert "different checkpoint" in manager._attribution_blocker(records, parent_key, record)
        # Same origin is not sufficient when the actual checkpoint hash differs.
        invalid = copy.deepcopy(records)
        invalid[parent_key]["checkpoint_sha256"] = "f" * 64
        implicit = copy.deepcopy(records[candidate_key])
        implicit["_segment"].pop("visual_context_blocks")
        assert "different checkpoint" in manager._attribution_blocker(invalid, parent_key, implicit)
        original = records[(13, originals[13]["segment"]["revision"])]
        alias = records[parent_key]
        assert chain.checkpoint_same_context_source(original, alias)
        assert not chain.checkpoint_same_context_source(original, invalid[parent_key])
        assert not chain.checkpoint_same_context_source(original, {**alias, "adopted_from_revision":"f" * 32})
        assert not chain.checkpoint_same_context_source(original, {**alias, "checkpoint_sha256":""})
        assert not chain.checkpoint_same_context_source(original, {**alias, "scene":12})
        # Every block is checked, including a later block and repeated sources.
        multi = copy.deepcopy(records[candidate_key])
        block = multi["_segment"]["visual_context_blocks"][0]
        multi["_segment"]["visual_context_blocks"] = [
            {**block, "frames":2}, {**block, "frames":3}]
        assert manager._attribution_blocker(records, parent_key, multi) is None
        multi["_segment"]["visual_context_blocks"][1]["source_checkpoint_sha256"] = "f" * 64
        assert "different checkpoint" in manager._attribution_blocker(records, parent_key, multi)

        response = await chain._restore_checkpoint_revisions(h.h.JsonRequest({
            "run_name":run.name, "activate_only":True, "resume_scene":19,
            "scope_start_scene":8, "scope_end_scene":18,
            "revisions":[item for item in selection["lineage"] if item["scene"] >= 8],
        }))
        assert response.status == 200, response.text
        for scene in range(1, 19):
            pointer = json.loads((run / "checkpoints" / f"clip_{scene:04d}.json").read_text())
            assert pointer["segment"]["revision"] == selected[scene]
        final = h.snapshot(run)
        assert all(final[path] == value for path, value in before.items()
                   if not (path.startswith("checkpoints/clip_") and path.count(".") == 1))
        if after_activation:
            await after_activation(run, manager, originals, selected)
    print("Context reattribution: scenes 13–18, unchanged sources and aliases, safe rejection, chapter output/activation and media preservation pass")


if __name__ == "__main__":
    asyncio.run(main())
