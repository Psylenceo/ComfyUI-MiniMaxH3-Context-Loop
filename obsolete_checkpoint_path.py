"""Explicit, previewed cleanup of a replaced path whose tail was reattached."""
from __future__ import annotations

import os
import re
import uuid

from .checkpoint_manager import (
    CheckpointDeleteBlocked, CheckpointGraphManager, _fingerprint, _strict_run_name,
    checkpoint_run_lock, checkpoint_same_context_source,
)


class ObsoleteCheckpointPathManager:
    def __init__(self, output_root):
        self.graph = CheckpointGraphManager(output_root)

    def preview(self, run_name, scene, revision):
        manager = self.graph
        _directory, run = manager._run_dir(_strict_run_name(run_name))
        key = (int(scene), str(revision or "").strip().lower())
        if not re.fullmatch(r"[0-9a-f]{32}", key[1]):
            raise ValueError("Checkpoint revision must be a 32-character revision id.")
        with checkpoint_run_lock(manager.output_root, run):
            scan = manager._scan(run, adopt_legacy=False)
            records = scan["records"]
            if key not in records:
                raise FileNotFoundError("The selected obsolete checkpoint is no longer available.")
            # Follow only actual lineage children, never context dependants on
            # other paths. Those remain outside the proposed deletion set.
            pending, queue = set(), [key]
            while queue:
                current = queue.pop()
                if current in pending:
                    continue
                pending.add(current)
                queue.extend(records[current]["_children"])
            survivors = {k:v for k, v in records.items() if k not in pending}
            blockers, retained, previews, files = [], {}, [], {}

            def equivalent(record):
                return next((other for other, value in sorted(survivors.items())
                             if value["ready"] and not value.get("_lineage_issue")
                             and checkpoint_same_context_source(record, value)), None)

            for current in sorted(pending):
                record = records[current]
                label = "Scene %d · %s" % (current[0], current[1][:8])
                if record["active"] or record.get("pointer_active"):
                    blockers.append(label + " is still assigned. Assign the replacement path first.")
                if record.get("take_kind") == "editorial_alternate":
                    blockers.append(label + " is an editorial ALT; manage it separately.")
                if record.get("_lineage_issue"):
                    blockers.append(label + " has unresolved lineage; cleanup cannot verify it.")
                if current != key:
                    replacement = equivalent(record)
                    if replacement is None:
                        blockers.append(label + " has not been safely reattached outside this path.")
                    else:
                        retained[current] = replacement
                # Keep the existing named-branch, ALT, snapshot, ownership and
                # file protections. Dependencies are checked for the whole set
                # below; do not rescan the run once per scene.
                preview = manager.deletion_preview(
                    run, *current, _scan=scan, _skip_dependency_check=True)
                previews.append(preview["snapshot"])
                blockers.extend(label + ": " + reason for reason in preview["blockers"])
                for part in preview["files"]:
                    if part["kind"].startswith("archive_"):
                        part = dict(part, owned=False)
                    if part["kind"] != "active_pointer":
                        files[part["path"]] = part

            for other, record in survivors.items():
                for dependency in record["_dependencies"]:
                    if dependency not in pending:
                        continue
                    replacement = equivalent(records[dependency])
                    if (record["_parent"] == dependency
                            or record.get("take_kind") == "editorial_alternate"
                            or replacement is None):
                        blockers.append("Scene %d · %s still needs scene %d · %s." %
                                        (other[0], other[1][:8], dependency[0], dependency[1][:8]))
                    else:
                        retained[dependency] = replacement

            removals = [{"scene":k[0], "revision":k[1]} for k in sorted(pending)]
            kept = [{"scene":old[0], "revision":new[1], "replaces_revision":old[1]}
                    for old, new in sorted(retained.items())]
            file_list = sorted(files.values(), key=lambda item: item["path"])
            owned = [part for part in file_list if part["owned"] and part["exists"]]
            blockers = list(dict.fromkeys(blockers))
            return {
                "ok":True, "run_name":run, "scene":key[0], "revision":key[1],
                "allowed":not blockers, "blockers":blockers, "revisions":removals,
                "retained_revisions":kept, "files":file_list,
                "owned_file_count":len(owned),
                "reclaimed_bytes":sum(part["size_bytes"] for part in owned),
                "snapshot":_fingerprint({"operation":"obsolete_path", "run":run,
                    "revisions":removals, "retained":kept, "previews":previews,
                    "blockers":blockers}),
                "not_deleted":["Reattached scenes and all their shared files",
                    "Active and other working-branch selections", "Plans and prompt histories",
                    "References, processing results and assembled exports"],
            }

    def delete(self, run_name, scene, revision, expected_snapshot=""):
        manager = self.graph
        _directory, run = manager._run_dir(_strict_run_name(run_name))
        with checkpoint_run_lock(manager.output_root, run):
            preview = self.preview(run, scene, revision)
            if not preview["allowed"]:
                raise CheckpointDeleteBlocked(" ".join(preview["blockers"]), preview)
            if not expected_snapshot or expected_snapshot != preview["snapshot"]:
                raise CheckpointDeleteBlocked(
                    "The obsolete path changed or was not previewed. Preview it again.", preview)
            # Same staged deletion as single revisions: failure to stage any
            # file restores everything already moved, before unlinking begins.
            transaction, staged = uuid.uuid4().hex, []
            try:
                for part in preview["files"]:
                    if not part["owned"] or not part["exists"]:
                        continue
                    path = manager._artifact_path(part["path"])
                    temporary = "%s.delete.%s.tmp" % (path, transaction)
                    os.replace(path, temporary)
                    staged.append((path, temporary, part["size_bytes"]))
            except Exception:
                for original, temporary, _size in reversed(staged):
                    os.replace(temporary, original)
                raise
            failed = []
            for _original, temporary, _size in staged:
                try:
                    os.unlink(temporary)
                except OSError:
                    failed.append(temporary)
            return {
                "ok":True, "run_name":run, "scene":int(scene), "revision":str(revision),
                "deleted_revisions":preview["revisions"],
                "deleted_files":len(staged) - len(failed),
                "reclaimed_bytes":sum(size for _original, temporary, size in staged
                                      if temporary not in failed),
                "cleanup_pending":len(failed),
                "message":"Deleted obsolete path (%d revision links). Reattached scenes and shared media were kept.%s" %
                    (len(preview["revisions"]), " Some staged files could not be removed." if failed else ""),
            }
