#!/usr/bin/env python3
"""Standalone prompt-history persistence and branching checks."""

import json
import pathlib
import tempfile

from prompt_history import PromptHistoryStore


def expect_value_error(action, message):
    try:
        action()
    except ValueError as error:
        assert message in str(error)
    else:
        raise AssertionError("Expected ValueError containing %r" % message)


def main():
    with tempfile.TemporaryDirectory() as temporary:
        store = PromptHistoryStore(temporary)
        first = store.save_draft("project", "scene_one", "First draft.")
        first_id = first["revision"]["id"]

        edited = store.save_draft(
            "project", "scene_one", "First executed prompt.", first_id)
        assert edited["revision"]["id"] == first_id
        executed = store.mark_executed(
            "project", "scene_one", "First executed prompt.")
        assert executed["revision"]["executed_at"]
        assert executed["history"]["active_revision_state"] == "executed"
        assert executed["history"]["latest_executed_revision"] == first_id
        repeated = store.mark_executed(
            "project", "scene_one", "  First executed prompt.\n")
        assert repeated["revision"]["id"] == first_id
        assert repeated["revision"]["execution_count"] == 2

        child = store.save_draft(
            "project", "scene_one", "Second version.", first_id)
        child_id = child["revision"]["id"]
        assert child_id != first_id
        assert child["revision"]["parent_id"] == first_id
        assert child["history"]["active_revision_state"] == "draft"
        assert child["history"]["latest_executed_revision"] == first_id

        # Continue typing in the unexecuted child instead of creating a file
        # for every keystroke.
        child_edit = store.save_draft(
            "project", "scene_one", "Second version, edited.", child_id)
        assert child_edit["revision"]["id"] == child_id
        assert len(child_edit["history"]["revisions"]) == 2

        restored = store.activate("project", "scene_one", first_id)
        assert restored["revision"]["prompt"] == "First executed prompt."
        assert restored["history"]["active_revision"] == first_id
        alternate = store.save_draft(
            "project", "scene_one", "Alternate branch.", first_id)
        alternate_id = alternate["revision"]["id"]
        assert alternate_id not in (first_id, child_id)
        assert alternate["revision"]["parent_id"] == first_id
        assert len(alternate["history"]["revisions"]) == 3

        # Restoring an already executed branch prompt navigates to its exact
        # history revision. Checkpoint activation must not append another
        # prompt merely because the Plan editor is being synchronized.
        reselected = store.save_draft(
            "project", "scene_one", "First executed prompt.", alternate_id)
        assert reselected["revision"]["id"] == first_id
        assert reselected["history"]["active_revision"] == first_id
        assert len(reselected["history"]["revisions"]) == 3
        store.activate("project", "scene_one", alternate_id)

        labeled = store.set_label(
            "project", "scene_one", first_id, "  Opening   performance  ")
        assert labeled["revision"]["label"] == "Opening performance"
        assert next(item for item in labeled["history"]["revisions"]
                    if item["id"] == first_id)["label"] == "Opening performance"

        expect_value_error(
            lambda: store.set_archived(
                "project", "scene_one", alternate_id, True),
            "active prompt revision cannot be archived")
        archived = store.set_archived(
            "project", "scene_one", child_id, True)
        assert archived["revision"]["archived_at"]
        assert archived["history"]["archived_revision_count"] == 1
        restored_child = store.activate("project", "scene_one", child_id)
        assert restored_child["revision"]["archived_at"] is None
        assert restored_child["history"]["archived_revision_count"] == 0

        store.activate("project", "scene_one", alternate_id)
        expect_value_error(
            lambda: store.delete_draft(
                "project", "scene_one", first_id),
            "Executed prompt history is protected")
        deleted_child = store.delete_draft(
            "project", "scene_one", child_id)
        assert all(item["id"] != child_id
                   for item in deleted_child["history"]["revisions"])
        assert not (pathlib.Path(
            temporary, "h3_chains", "project", "prompt_history", "scene_one",
            f"{child_id}.json").exists())

        scene_dir = pathlib.Path(
            temporary, "h3_chains", "project", "prompt_history", "scene_one")
        index = json.loads((scene_dir / "index.json").read_text(encoding="utf-8"))
        assert all("prompt" not in item for item in index["revisions"])
        assert (scene_dir / f"{first_id}.json").is_file()
        assert (scene_dir / f"{alternate_id}.json").is_file()

    print("H3 prompt history: labels, safe archive/delete and executed branches pass")


def basic_draft_matching():
    with tempfile.TemporaryDirectory() as temporary:
        store = PromptHistoryStore(temporary)

        # A different basic draft on the same H3 text must fork a new
        # revision rather than mutate an already-executed one.
        old = store.mark_executed(
            "project", "scene_one", "same H3 result", "original basic draft")
        old_id = old["revision"]["id"]
        new = store.save_draft(
            "project", "scene_one", "same H3 result", old_id,
            "different basic draft")
        new_id = new["revision"]["id"]
        assert new_id != old_id, (
            "a different basic draft on executed H3 text must fork, not "
            "overwrite the executed revision")
        assert new["revision"]["parent_id"] == old_id
        assert new["revision"]["executed_at"] is None
        reread_old = store.get("project", "scene_one", old_id)
        assert reread_old["basic_prompt"] == "original basic draft", (
            "the original executed revision's basic draft must survive")
        assert new["revision"]["basic_prompt"] == "different basic draft"

        # The same fork-on-difference rule applies to mark_executed: calling
        # it with the same H3 text but a new basic draft against an already
        # executed revision must not silently rewrite that revision either.
        reexecuted = store.mark_executed(
            "project", "scene_one", "same H3 result", "yet another basic")
        assert reexecuted["revision"]["id"] not in (old_id,)
        reread_old_again = store.get("project", "scene_one", old_id)
        assert reread_old_again["basic_prompt"] == "original basic draft"

        # Re-executing with the *same* basic draft as an executed revision
        # is not a fork - it is the same take running again.
        rerun = store.mark_executed(
            "project", "scene_one", "same H3 result", "original basic draft")
        assert rerun["revision"]["id"] == old_id
        assert rerun["revision"]["execution_count"] == 2

        # An unexecuted draft's basic_prompt stays mutable in place.
        draft = store.save_draft(
            "project", "scene_two", "draft text", None, "draft A")
        draft_id = draft["revision"]["id"]
        updated = store.save_draft(
            "project", "scene_two", "draft text", draft_id, "draft B")
        assert updated["revision"]["id"] == draft_id, (
            "an unexecuted draft must remain mutable in place")
        assert updated["revision"]["basic_prompt"] == "draft B"

    print("H3 prompt history: basic-draft matching against executed revisions pass")


if __name__ == "__main__":
    main()
    basic_draft_matching()
