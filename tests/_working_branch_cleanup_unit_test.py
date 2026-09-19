"""Branch-path cleanup: ownership, dependency closure, preview fences and rollback."""
import asyncio
import importlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cleanup_helpers", ROOT / "tests/_checkpoint_revision_unit_test.py")
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
chain = helpers.chain
module = importlib.import_module(chain.__package__ + ".working_branch_cleanup")
Cleanup = module.WorkingBranchCleanup


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fixture(output):
    helpers.folder_paths.output_directory = str(output)
    run = "cleanup_test"
    root = output / "h3_chains" / run
    one, _ = helpers.write_revision(root, 1, "1" * 32, 1, active=True, run_name=run)
    two, _ = helpers.write_revision(root, 2, "2" * 32, 2, active=True, predecessor=one, run_name=run)
    alternate, _ = helpers.write_revision(root, 1, "3" * 32, 3, run_name=run)
    alternate["segment"].update(take_kind="editorial_alternate", alternate_of_revision="1" * 32)
    write(root / "checkpoints" / ("clip_0001." + "3" * 32 + ".json"), alternate)
    other, other_files = helpers.write_revision(root, 1, "4" * 32, 4, run_name=run)
    store = chain.WorkingBranches(str(output), run)
    authoring = {"plan_json": json.dumps({"shots": [{"id": "scene_1"}, {"id": "scene_2"}]})}
    store.save("main", authoring, "")
    keep = store.create("main", "SelfLift", authoring)["id"]
    write(root / "branches" / keep / "checkpoints/clip_0001.json", other)
    write(root / "editorial.json", {"replacements": [{"scene": 1, "base_revision": "1" * 32,
                                                      "alternate_revision": "3" * 32}]})
    snapshot = root / "chapters/01_chapter/manifests" / ("a" * 32 + ".json")
    write(snapshot, {"format": "h3_chain_chapter_manifest_v1", "run_name": run,
                     "chapter": {"number": 1}, "segments": [one["segment"], two["segment"]]})
    return root, store, keep, one, two, other_files, snapshot


def check():
    with tempfile.TemporaryDirectory() as tmp:
        root, store, keep, one, two, other_files, snapshot = fixture(Path(tmp))
        cleanup = Cleanup(tmp, store.run)
        before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        preview = cleanup.preview("main", keep)
        assert preview["allowed"] and len(preview["revisions"]) == 3, preview
        assert preview["retired_snapshots"] == 1 and not preview["retained_revisions"]
        assert all(p.read_bytes() == data for p, data in before.items()), "Preview wrote project data"
        try:
            cleanup.preview(keep, keep)
            raise AssertionError("Current branch accepted")
        except ValueError:
            pass
        # Any new branch pin invalidates the preview; shared prefix stays.
        another = store.create("main", "Keep prefix", store.load()["authoring"], 1)["id"]
        try:
            cleanup.delete("main", keep, preview["snapshot"])
            raise AssertionError("Stale preview accepted")
        except chain.CheckpointDeleteBlocked:
            pass
        preview = cleanup.preview("main", keep)
        assert {i["revision"] for i in preview["retained_revisions"]} == {"1" * 32}
        assert {i["revision"] for i in preview["revisions"]} == {"2" * 32, "3" * 32}
        stable = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        original_replace = module.os.replace
        calls = 0
        def fail_once(source, destination):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("simulated storage error")
            return original_replace(source, destination)
        with patch.object(module.os, "replace", side_effect=fail_once):
            try:
                cleanup.delete("main", keep, preview["snapshot"])
                raise AssertionError("Failure was hidden")
            except OSError:
                pass
        assert all(p.read_bytes() == data for p, data in stable.items()), "Failed staging lost data"
        preview = cleanup.preview("main", keep)
        result = cleanup.delete("main", keep, preview["snapshot"])
        assert len(result["deleted_revisions"]) == 2
        assert not (root / "checkpoints/clip_0001.json").exists()
        assert not (root / "checkpoints/clip_0002.json").exists()
        assert not (root / "editorial.json").exists() and not snapshot.exists()
        assert (snapshot.parent.parent / "retired_manifests" / snapshot.name).is_file()
        assert all(p.exists() for p in other_files)
        assert (root / "checkpoints" / ("clip_0001." + "1" * 32 + ".json")).is_file()
        assert not (root / "checkpoints" / ("clip_0002." + "2" * 32 + ".json")).exists()
        assert store._pointers(keep)[1]["segment"]["revision"] == "4" * 32
        assert store._pointers(another)[1]["segment"]["revision"] == "1" * 32
        assert store.load()["authoring"] and store.listing()["default_branch"] == "main"
        assert (Path(tmp) / result["recovery_path"]).is_dir()
        assert not cleanup.preview("main", keep)["allowed"], "Already cleared branch should be empty"

    with tempfile.TemporaryDirectory() as tmp:
        root, store, keep, one, two, _, _ = fixture(Path(tmp))
        cleanup = Cleanup(tmp, store.run)
        # A retained branch's later scene pins its full dependency chain.
        write(root / "branches" / keep / "checkpoints/clip_0002.json", two)
        preview = cleanup.preview("main", keep)
        assert {i["revision"] for i in preview["retained_revisions"]} == {"1" * 32, "2" * 32}
        # Editorial use of the ALT also keeps its base.
        write(root / "branches" / keep / "editorial.json", {"replacements": [{"alternate_revision": "3" * 32}]})
        preview = cleanup.preview("main", keep)
        assert not preview["revisions"] and len(preview["retained_revisions"]) == 3
        retained = {p: p.read_bytes() for p in (root / "branches" / keep).rglob("*") if p.is_file()}
        cleanup.delete("main", keep, preview["snapshot"])
        assert all(p.read_bytes() == data for p, data in retained.items())

    with tempfile.TemporaryDirectory() as tmp:
        root, store, keep, one, two, _, snapshot = fixture(Path(tmp))
        cleanup = Cleanup(tmp, store.run)
        # A chapter snapshot owned by the retained branch remains pinned.
        write(root / "branches" / keep / "chapters/01_kept/manifests" / ("b" * 32 + ".json"),
              json.loads(snapshot.read_text()))
        preview = cleanup.preview("main", keep)
        assert {i["revision"] for i in preview["retained_revisions"]} == {"1" * 32, "2" * 32}
        (root / "branches" / keep / "editorial.json").symlink_to(root / "editorial.json")
        try:
            cleanup.preview("main", keep)
            raise AssertionError("Symlink accepted")
        except ValueError as exc:
            assert "symlink" in str(exc)
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as converted:
        root, store, keep, one, two, _, _ = fixture(Path(tmp))
        conversion = importlib.import_module(chain.__package__ + ".chain_layout_conversion")
        layout = importlib.import_module(chain.__package__ + ".chain_layout")
        source_before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        conversion.convert_copy(root, converted)
        cleanup = Cleanup(converted, store.run)
        preview = cleanup.preview("main", keep)
        assert len(preview["revisions"]) == 3
        result = cleanup.delete("main", keep, preview["snapshot"])
        assert len(result["deleted_revisions"]) == 3
        physical = Path(layout.state_root(Path(converted) / "h3_chains" / store.run))
        assert not (physical / "checkpoints/clip_0001.json").exists()
        assert (physical / "branches" / keep / "checkpoints/clip_0001.json").is_file()
        assert all(p.read_bytes() == data for p, data in source_before.items())
    print("Working branch cleanup: shared dependencies, ALT, snapshots, read-only preview, stale fence, rollback and isolation pass")


async def check_api():
    class Request(helpers.JsonRequest):
        method = "POST"
        query = {}

    # Inline executor isolates route semantics from CUDA/event-loop imports.
    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    with tempfile.TemporaryDirectory() as tmp, patch.object(chain.asyncio, "to_thread", inline):
        root, store, keep, one, two, _, _ = fixture(Path(tmp))
        body = {"action": "delete-path-preview", "run_name": store.run,
                "branch_id": "main", "keep_branch_id": keep}
        response = await chain._working_branch_command(Request(body))
        assert response.status == 200, response.text
        preview = json.loads(response.text)
        body.update(action="delete-path", snapshot=preview["snapshot"])
        with patch.object(chain, "_project_write_rejection", return_value=chain.web.json_response({"error": "read only"}, status=423)):
            response = await chain._working_branch_command(Request(body))
            assert response.status == 423
        busy = SimpleNamespace(prompt_queue=SimpleNamespace(get_tasks_remaining=lambda: 1))
        with patch.object(chain.PromptServer, "instance", busy):
            response = await chain._working_branch_command(Request(body))
            assert response.status == 409 and "generation" in response.text
        assert (root / "checkpoints/clip_0001.json").exists()
        response = await chain._working_branch_command(Request(dict(body, snapshot="stale")))
        assert response.status == 409
        response = await chain._working_branch_command(Request(body))
        assert response.status == 200, response.text
        assert len(json.loads(response.text)["deleted_revisions"]) == 3
    print("Branch cleanup routes: explicit preview, ownership, busy queue and stale confirmation checks pass")


if __name__ == "__main__":
    check()
    asyncio.run(check_api())
