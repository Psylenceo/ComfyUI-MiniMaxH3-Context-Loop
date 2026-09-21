"""Empty-branch removal retains data and never bypasses saved-work protections."""
import asyncio
import importlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("empty_branch_helpers", ROOT / "tests/_checkpoint_revision_unit_test.py")
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
chain = helpers.chain
module = importlib.import_module(chain.__package__ + ".working_branches")
scope = importlib.import_module(chain.__package__ + ".branch_scope")


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fixture(output, organized=False):
    helpers.folder_paths.output_directory = str(output)
    run = "empty_branches"
    root = Path(output) / "h3_chains" / run
    if organized:
        layout = importlib.import_module(chain.__package__ + ".chain_layout")
        layout.create_project(root)
    else:
        root.mkdir(parents=True)
    store = chain.WorkingBranches(output, run)
    authoring = {"plan_json": json.dumps({"shots": [{"id": "scene_1", "prompt": "Keep my Plan"}]})}
    authoring = store.save("main", authoring, "")["authoring"]
    keep = store.create("main", "SelfLift", authoring)["id"]
    empty = store.create("main", "960x544", authoring)["id"]
    return root, store, keep, empty, authoring


def rejected(call, message):
    try:
        call()
    except ValueError as exc:
        assert message in str(exc), str(exc)
    else:
        raise AssertionError("Unsafe operation accepted: " + message)


def check():
    for organized in (False, True):
        with tempfile.TemporaryDirectory() as tmp:
            root, store, keep, empty, authoring = fixture(tmp, organized)
            branch_dir = store.folder / empty
            saved_empty = store.load(empty)
            saved_keep = store.load(keep)
            # Immutable inventory is shared, not a pointer owned by this branch.
            write(store.folder.parent / "checkpoints/clip_0001.aaaaaaaa.json", {"shared": True})
            before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            preview = store.empty_branch_preview(empty, keep)
            assert preview["allowed"] and preview["action"] == "delete-empty"
            assert all(p.read_bytes() == data for p, data in before.items()), "Preview modified data"
            result = store.retire_empty(empty, keep, preview["snapshot"])
            assert store.retire_empty(empty, keep, preview["snapshot"]) == result, "Retry is not idempotent"
            assert empty not in {b["id"] for b in store.listing()["branches"]}
            assert all(p.read_bytes() == data for p, data in before.items()), "Removal erased saved metadata"
            assert json.loads((branch_dir / "branch.json").read_text())["authoring"] == saved_empty["authoring"]
            rejected(lambda: store.load(empty), "deleted")
            rejected(lambda: store.save(empty, authoring, ""), "deleted")
            rejected(lambda: scope.working_directory(root, store.run, empty), "deleted")
            assert store.load(keep) == saved_keep

            hide = store.empty_branch_preview("main", keep)
            assert hide["allowed"] and hide["changes_default"]
            store.retire_empty("main", keep, hide["snapshot"])
            listing = store.listing()
            assert listing["default_branch"] == keep and listing["branches"][0]["hidden"]
            assert store.load("main")["authoring"] == authoring
            # Showing Original must not reset the user's preferred branch.
            store.show_original()
            assert store.listing()["default_branch"] == keep
            assert not store.listing()["branches"][0].get("hidden")
            store.make_default("main")
            hide = store.empty_branch_preview("main", keep)
            store.retire_empty("main", keep, hide["snapshot"])
            store.make_default("main")
            assert store.listing()["default_branch"] == "main"
            assert not store.listing()["branches"][0].get("hidden")

    with tempfile.TemporaryDirectory() as tmp:
        root, store, keep, empty, authoring = fixture(tmp)
        # Last/current branch, unsafe IDs and hidden keep targets are rejected.
        rejected(lambda: store.empty_branch_preview(empty, empty), "Switch Plan Studio")
        rejected(lambda: store.empty_branch_preview("../other", keep), "Invalid")
        hide = store.empty_branch_preview("main", keep)
        store.retire_empty("main", keep, hide["snapshot"])
        rejected(lambda: store.empty_branch_preview(empty, "main"), "visible branch")
        # New work makes Original visible, without stealing the project default.
        pointer = store.folder.parent / "checkpoints/clip_0001.json"
        write(pointer, {})
        listing = store.listing()
        assert not listing["branches"][0].get("hidden") and listing["default_branch"] == keep
        assert not store.empty_branch_preview("main", keep)["allowed"]
        # A removed default follows its explicit surviving keep target.
        store.make_default(empty)
        preview = store.empty_branch_preview(empty, keep)
        store.retire_empty(empty, keep, preview["snapshot"])
        assert store.listing()["default_branch"] == keep
        # Original -> removed empty -> keep -> Original must not form a cycle.
        preview = store.empty_branch_preview(keep, "main")
        store.retire_empty(keep, "main", preview["snapshot"])
        assert store.listing()["default_branch"] == "main"
        assert [b["id"] for b in store.listing()["branches"]] == ["main"]

    for relative, value, expected in (
        ("checkpoints/clip_0001.json", {}, "assigned clips"),
        ("checkpoints/clip_10000.json", {}, "assigned clips"),
        ("checkpoints/.transactions/other.json", {}, "recovery is pending"),
        ("editorial.json", {"replacements": [{"scene": 1}]}, "saved cut"),
        ("editorial.json", {"trims": {"1": {"end": 20}}}, "saved cut"),
        ("editorial.json", {"locked_scene_ids": ["scene_1"]}, "saved cut"),
        ("editorial.json", {"alternate_draft": {"scene": 1}}, "saved cut"),
        ("chapters/01_test/manifests/snapshot.json", {}, "sealed chapter"),
        ("upscaled/test/checkpoints/clip_0001.json", {}, "processing/upscale"),
        ("upscaled/test/upscale_manifest.json", {}, "processing/upscale"),
        ("upscaled/test/partial/part.json", {}, "processing/upscale"),
        ("chapters/01_test/upscaled/test/checkpoints/clip_0001.json", {}, "processing/upscale"),
        ("reviews/selflift/pending.json", {}, "review/recovery"),
        ("pending_reviews/take.json", {}, "review/recovery"),
        ("orchestration/review_pending.json", {"format": "h3_review_snapshot_v1", "status": "pending"}, "handoff recovery"),
    ):
        with tempfile.TemporaryDirectory() as tmp:
            _, store, keep, empty, _ = fixture(tmp)
            preview = store.empty_branch_preview(empty, keep)
            path = store.folder / empty / relative
            write(path, value)
            blocked = store.empty_branch_preview(empty, keep)
            assert not blocked["allowed"] and any(expected in b for b in blocked["blockers"]), blocked
            rejected(lambda: store.retire_empty(empty, keep, preview["snapshot"]), expected)
            assert path.exists() and not (store.folder / empty / "deleted.json").exists()

    with tempfile.TemporaryDirectory() as tmp:
        _, store, keep, empty, _ = fixture(tmp)
        path = store.folder.parent / "orchestration/handoff.json"
        handoff = {"format": "h3_top_level_handoff_v1", "status": "pending", "working_branch_id": keep}
        write(path, handoff)
        assert store.empty_branch_preview(empty, keep)["allowed"], "Another branch's queue must not block this one"
        write(path, dict(handoff, working_branch_id=empty, status="uncertain"))
        assert not store.empty_branch_preview(empty, keep)["allowed"]
        write(path, dict(handoff, working_branch_id=empty, status="cancelled"))
        write(store.folder / empty / "orchestration/review_done.json",
              {"format": "h3_review_snapshot_v1", "status": "decided"})
        assert store.empty_branch_preview(empty, keep)["allowed"], "Resolved recovery history is retained, not a blocker"

    with tempfile.TemporaryDirectory() as tmp:
        root, store, keep, empty, authoring = fixture(tmp)
        preview = store.empty_branch_preview(empty, keep)
        saved = store.load(empty)
        store.save(empty, dict(authoring, width=960), saved["revision"])
        rejected(lambda: store.retire_empty(empty, keep, preview["snapshot"]), "changed")
        preview = store.empty_branch_preview(empty, keep)
        with patch.object(module, "atomic_json", side_effect=OSError("write failed")):
            try:
                store.retire_empty(empty, keep, preview["snapshot"])
            except OSError:
                pass
            else:
                raise AssertionError("Write failure was hidden")
        assert empty in {b["id"] for b in store.listing()["branches"]}
        # Failed durable acknowledgement can be retried without another write.
        with patch(module.__package__ + ".processing_persistence.sync_directory", side_effect=OSError("sync failed")):
            try:
                store.retire_empty(empty, keep, preview["snapshot"])
            except OSError:
                pass
            else:
                raise AssertionError("Durability failure was hidden")
        store.retire_empty(empty, keep, preview["snapshot"])
        assert empty not in {b["id"] for b in store.listing()["branches"]}

    with tempfile.TemporaryDirectory() as tmp:
        root, store, keep, empty, _ = fixture(tmp)
        (store.folder / empty / "editorial.json").symlink_to(root / "absent.json")
        rejected(lambda: store.empty_branch_preview(empty, keep), "symlink")
    print("Empty branches: both layouts, retained Plans, hidden Original, stable defaults, stale fences and saved-work guards pass")


async def check_api():
    class Request(helpers.JsonRequest):
        method = "POST"
        query = {}

    with tempfile.TemporaryDirectory() as tmp:
        _, store, keep, empty, _ = fixture(tmp)
        body = {"action": "empty-preview", "run_name": store.run,
                "branch_id": empty, "keep_branch_id": keep}
        response = await chain._working_branch_command(Request(body))
        assert response.status == 200, response.text
        body.update(action="delete-empty", snapshot=json.loads(response.text)["snapshot"])
        get = Request({})
        get.method, get.query = "GET", body
        assert (await chain._working_branch_command(get)).status == 405
        with patch.object(chain, "_project_write_rejection", return_value=chain.web.json_response({"error": "read only"}, status=423)):
            assert (await chain._working_branch_command(Request(body))).status == 423
        busy = SimpleNamespace(prompt_queue=SimpleNamespace(get_tasks_remaining=lambda: 1))
        with patch.object(chain.PromptServer, "instance", busy):
            assert (await chain._working_branch_command(Request(body))).status == 409
        assert (await chain._working_branch_command(Request(dict(body, snapshot="stale")))).status == 400
        response = await chain._working_branch_command(Request(body))
        assert response.status == 200, response.text
        assert (await chain._working_branch_command(Request(body))).status == 200
        assert (await chain._working_branch_command(Request(dict(body, branch_id="main")))).status == 400
        assert (await chain._working_branch_command(Request({"action": "show-original", "run_name": store.run}))).status == 200
    print("Empty branch routes: preview, explicit POST, queue, ownership, retry and stale snapshot checks pass")


if __name__ == "__main__":
    check()
    asyncio.run(check_api())
