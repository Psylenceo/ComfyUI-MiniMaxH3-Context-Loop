#!/usr/bin/env python3
"""Delete obsolete 12..18 only after every saved tail scene was reattached."""
import asyncio
import importlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "reattribution", Path(__file__).with_name("_checkpoint_context_reattribution_unit_test.py"))
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
chain = h.chain
cleanup = importlib.import_module(chain.__package__ + ".obsolete_checkpoint_path")


async def check(run, manager, originals, selected):
    root = run.parent.parent
    cleaner = cleanup.ObsoleteCheckpointPathManager(root)
    old = {i:originals[i]["segment"]["revision"] for i in range(12, 19)}
    before = h.h.snapshot(run)
    with patch.object(cleaner.graph, "_scan", wraps=cleaner.graph._scan) as scan:
        preview = cleaner.preview(run.name, 12, old[12])
        assert scan.call_count == 1, "one shared scan, not one per revision"
    assert preview["allowed"], preview["blockers"]
    assert [item["scene"] for item in preview["revisions"]] == list(range(12, 19))
    assert {item["revision"] for item in preview["retained_revisions"]} == {selected[i] for i in range(13, 19)}
    assert h.h.snapshot(run) == before, "preview must be read-only"

    async def route(action, body):
        request = h.h.h.JsonRequest(body)
        request.method = "POST"
        request.path = "/minimax_h3_context_loop/checkpoint-revisions/obsolete-" + action
        # The fake ComfyUI server has no running worker-wakeup integration.
        # Exercise real route validation and worker logic without that host.
        async def inline(function, *args):
            return function(*args)
        with patch.object(chain.asyncio, "to_thread", side_effect=inline):
            return await chain._obsolete_checkpoint_path(request)

    body = {"run_name":run.name, "scene":12, "revision":old[12]}
    response = await route("preview", body)
    assert response.status == 200, response.text
    assert json.loads(response.text)["snapshot"] == preview["snapshot"]
    assert (await route("delete", body)).status == 409
    assert (await route("preview", [])).status == 400
    assert (await route("preview", dict(body, run_name="../escape"))).status == 400
    if hasattr(chain, "_project_write_rejection"):
        with patch.object(chain, "_project_write_rejection", return_value=chain.web.json_response(
                {"error":"read only"}, status=423)):
            assert (await route("delete", dict(body, snapshot=preview["snapshot"]))).status == 423
    assert h.h.snapshot(run) == before, "rejected API calls changed files"

    # Sealed deliveries and surviving context consumers must retain their
    # exact old inputs, even if the active path has been reassigned.
    snapshot_path = run / "chapters" / "02_two" / "manifests" / ("e" * 32 + ".json")
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(json.dumps({"format":"h3_chain_chapter_manifest_v1",
        "run_name":run.name, "chapter":{"number":2, "title":"Two"},
        "segments":[originals[12]["segment"]]}))
    try:
        invalid = cleaner.preview(run.name, 12, old[12])
        assert not invalid["allowed"] and any("Sealed Chapter" in b for b in invalid["blockers"])
    finally:
        snapshot_path.unlink()
        snapshot_path.parent.rmdir()
        snapshot_path.parent.parent.rmdir()
        snapshot_path.parent.parent.parent.rmdir()

    scan = manager._scan(run.name)
    scan["records"][(18, selected[18])]["_dependencies"].append((12, old[12]))
    with patch.object(cleaner.graph, "_scan", return_value=scan):
        invalid = cleaner.preview(run.name, 12, old[12])
        assert not invalid["allowed"] and any("still needs scene 12" in b for b in invalid["blockers"])

    def blocked(call, message):
        try:
            call()
        except cleanup.CheckpointDeleteBlocked as error:
            assert message.lower() in str(error).lower(), error
        else:
            raise AssertionError("unsafe deletion accepted")

    for snapshot in ("", "stale"):
        blocked(lambda: cleaner.delete(run.name, 12, old[12], snapshot), "preview")
    assert not cleaner.preview(run.name, 12, selected[12])["allowed"]

    alias_path = run / "checkpoints" / f"clip_0018.{selected[18]}.json"
    held = alias_path.with_suffix(".held")
    alias_path.rename(held)
    try:
        invalid = cleaner.preview(run.name, 12, old[12])
        assert not invalid["allowed"] and any("reattached" in b for b in invalid["blockers"])
    finally:
        held.rename(alias_path)

    # A second retained named branch can pin the old path even after the
    # currently browsed branch moved. No directory from the real project is used.
    if "working_dir" in manager._scan(run.name):
        branch_pointer = run / "branches" / ("f" * 32) / "checkpoints" / "clip_0012.json"
        branch_pointer.parent.mkdir(parents=True)
        branch_pointer.write_text(json.dumps(originals[12]))
        try:
            assert any("Working branch" in b for b in cleaner.preview(run.name, 12, old[12])["blockers"])
            blocked(lambda: cleaner.delete(run.name, 12, old[12], preview["snapshot"]), "Working branch")
        finally:
            branch_pointer.unlink()
            branch_pointer.parent.rmdir()
            branch_pointer.parent.parent.rmdir()
            branch_pointer.parent.parent.parent.rmdir()

    # Changed metadata invalidates the exact preview, without deleting anything.
    metadata = run / "checkpoints" / f"clip_0012.{old[12]}.json"
    content = metadata.read_bytes()
    try:
        metadata.write_bytes(content + b"\n")
        blocked(lambda: cleaner.delete(run.name, 12, old[12], preview["snapshot"]), "preview")
    finally:
        metadata.write_bytes(content)
    preview = cleaner.preview(run.name, 12, old[12])
    before = h.h.snapshot(run)

    # Failure before deletion begins restores all staged files.
    replace = cleanup.os.replace
    calls = 0
    def fail_second(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("staging failure")
        return replace(source, destination)
    with patch.object(cleanup.os, "replace", side_effect=fail_second):
        try:
            cleaner.delete(run.name, 12, old[12], preview["snapshot"])
        except OSError as error:
            assert "staging failure" in str(error)
        else:
            raise AssertionError("expected staging failure")
    assert h.h.snapshot(run) == before, "staging failure must roll back"

    # Multiple retained aliases of 14 must not lose 15's context dependency
    # when the original 14 link disappears. Prefer the alias on its own path.
    other12, _ = h.h.h.write_revision(run, 12, "b" * 32, 1201,
        predecessor=originals[11], run_name=run.name, context_length=0, audio_context_length=0)
    other13 = manager.attribute(run.name, 12, other12["segment"]["revision"], 13, old[13])
    manager.attribute(run.name, 13, other13["revision"], 14, old[14])
    preview = cleaner.preview(run.name, 12, old[12])
    assert preview["allowed"], preview["blockers"]
    before = h.h.snapshot(run)
    response = await route("delete", dict(body, snapshot=preview["snapshot"]))
    assert response.status == 200, response.text
    deleted = json.loads(response.text)
    after = h.h.snapshot(run)
    assert deleted["cleanup_pending"] == 0
    assert len(deleted["deleted_revisions"]) == 7
    expected_paths = {str((root / f["path"]).relative_to(run))
                      for f in preview["files"] if f["owned"] and f["exists"]}
    assert set(before) - set(after) == expected_paths
    assert all(before[path] == data for path, data in after.items())
    for scene in range(12, 19):
        assert not (run / "checkpoints" / f"clip_{scene:04d}.{old[scene]}.json").exists()
        assert (run / "checkpoints" / f"clip_{scene:04d}.{selected[scene]}.json").exists()
    records = manager._scan(run.name)["records"]
    assert (14, selected[14]) in records[(15, selected[15])]["_dependencies"]
    selection = {"run_name":run.name, "output_mode":"workflow_local",
        "output_scope":"chapter", "scope_start_scene":8, "scope_end_scene":18,
        "lineage":[{"scene":i, "revision":selected[i]} for i in range(1, 19)]}
    manifest = chain.MiniMaxH3ChainCheckpointManager().passthrough(json.dumps(selection))[0]
    assert manifest["segments"][-1]["revision"] == selected[18]
    response = await chain._restore_checkpoint_revisions(h.h.h.JsonRequest({
        "run_name":run.name, "activate_only":True, "resume_scene":19,
        "scope_start_scene":8, "scope_end_scene":18,
        "revisions":selection["lineage"][7:]}))
    assert response.status == 200, response.text
    print("Obsolete path: preview, pins, missing aliases, stale snapshot, rollback, shared media and retained chapter consumption pass")


if __name__ == "__main__":
    asyncio.run(h.main(check))
