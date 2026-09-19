"""One preview/confirmation for an explicit set of generated checkpoint takes."""
import os
import re
import uuid

from .checkpoint_manager import (
    CheckpointDeleteBlocked, CheckpointGraphManager, _fingerprint,
    _strict_run_name, checkpoint_run_lock,
)
from .branch_scope import current_branch, working_directory


class BulkCheckpointManager:
    def __init__(self, output_root):
        self.graph = CheckpointGraphManager(output_root)

    @staticmethod
    def _keys(revisions):
        if not isinstance(revisions, list) or not revisions:
            raise ValueError("Select at least one saved checkpoint revision.")
        keys = set()
        for item in revisions:
            if (not isinstance(item, dict) or type(item.get("scene")) is not int
                    or item["scene"] < 1 or not isinstance(item.get("revision"), str)
                    or not re.fullmatch(r"[0-9a-f]{32}", item["revision"])):
                raise ValueError("Each selection needs a scene number and exact revision id.")
            keys.add((item["scene"], item["revision"]))
        return keys

    def _editorial_plan(self, scan, keys):
        """Release only this branch's selections for the exact deletion set."""
        path = os.path.join(scan["working_dir"], "editorial.json")
        parent = path
        while self.graph._inside(scan["run_dir"], parent):
            if os.path.islink(parent):
                raise ValueError("Cannot change a symlinked final cut during deletion.")
            parent = os.path.dirname(parent)
        if not os.path.exists(path):
            return path, None, [], None
        document = self.graph._read_json(path)
        if not isinstance(document, dict) or not isinstance(document.get("replacements", []), list):
            raise ValueError("Cannot verify this branch's final-cut selections.")
        released, retained = [], []
        for item in document.get("replacements", []):
            if not isinstance(item, dict):
                raise ValueError("Cannot verify this branch's final-cut selections.")
            scene = self.graph._integer(item.get("scene"))
            if scene < 1 or any(not re.fullmatch(r"[0-9a-f]{32}", str(item.get(field) or "").lower())
                                for field in ("base_revision", "alternate_revision")):
                raise ValueError("Cannot verify this branch's final-cut selections.")
            selected = any((scene, str(item.get(field) or "").lower()) in keys
                           for field in ("base_revision", "alternate_revision"))
            (released if selected else retained).append(item)
        return path, document, released, dict(document, replacements=retained)

    def preview(self, run_name, revisions):
        run = _strict_run_name(run_name)
        keys = self._keys(revisions)
        manager = self.graph
        with checkpoint_run_lock(manager.output_root, run):
            scan = manager._scan(run, adopt_legacy=False)
            _path, editorial, releases, _updated = self._editorial_plan(scan, keys)
            files, blockers, snapshots, rollback = {}, [], [], []
            for scene, revision in sorted(keys):
                plan = manager.deletion_preview(
                    run, scene, revision, _scan=scan, _deleting=keys,
                    _release_editorial=True)
                snapshots.append(plan["snapshot"])
                blockers.extend("S%d · %s: %s" % (scene, revision[:8], reason)
                                for reason in plan["blockers"])
                if plan["rollback"]:
                    rollback.append(scene)
                for part in plan["files"]:
                    previous = files.get(part["path"])
                    # If any revision says a file is shared, keep it. In
                    # particular, never remove media used by a retained alias.
                    if previous:
                        part = dict(part, owned=part["owned"] and previous["owned"],
                                    shared=part["shared"] or previous["shared"])
                    files[part["path"]] = part
            targets = [{"scene":scene, "revision":revision} for scene, revision in sorted(keys)]
            owned = [part for part in files.values() if part["owned"] and part["exists"]]
            return {
                "ok":True, "run_name":run, "revisions":targets,
                "allowed":not blockers, "blockers":blockers,
                "files":sorted(files.values(), key=lambda item: item["path"]),
                "rollback_scenes":rollback, "owned_file_count":len(owned),
                "editorial_releases":releases,
                "reclaimed_bytes":sum(part["size_bytes"] for part in owned),
                "snapshot":_fingerprint({"operation":"bulk_checkpoint_delete", "run":run,
                    "branch":current_branch(run), "targets":targets,
                    "snapshots":snapshots, "blockers":blockers, "editorial":editorial}),
                "not_deleted":["Unselected checkpoints and their shared files",
                    "References, processing results and assembled exports",
                    "Run-level archives, Plans and prompt histories"],
            }

    def delete(self, run_name, revisions, expected_snapshot=""):
        run = _strict_run_name(run_name)
        manager = self.graph
        with checkpoint_run_lock(manager.output_root, run):
            preview = self.preview(run, revisions)
            if not preview["allowed"]:
                raise CheckpointDeleteBlocked(" ".join(preview["blockers"]), preview)
            if not expected_snapshot or expected_snapshot != preview["snapshot"]:
                raise CheckpointDeleteBlocked(
                    "The selection, files or dependencies changed. Preview bulk deletion again.", preview)
            run_dir, _run = manager._run_dir(run)
            editorial_path, _editorial, releases, updated = self._editorial_plan(
                {"run_dir":run_dir, "working_dir":working_directory(run_dir, run)}, self._keys(revisions))
            transaction, staged = uuid.uuid4().hex, []
            editorial_backup = None
            try:
                for part in preview["files"]:
                    if not part["owned"] or not part["exists"]:
                        continue
                    path = manager._artifact_path(part["path"])
                    temporary = "%s.delete.%s.tmp" % (path, transaction)
                    os.replace(path, temporary)
                    staged.append((path, temporary, part["size_bytes"]))
                if releases:
                    temporary = "%s.delete.%s.tmp" % (editorial_path, transaction)
                    os.replace(editorial_path, temporary)
                    editorial_backup = temporary
                    manager._atomic_json(editorial_path, updated)
            except Exception:
                failed = []
                if editorial_backup:
                    try:
                        os.replace(editorial_backup, editorial_path)
                    except OSError:
                        failed.append(editorial_backup)
                for original, temporary, _size in reversed(staged):
                    try:
                        os.replace(temporary, original)
                    except OSError:
                        failed.append(temporary)
                if failed:
                    raise OSError("Bulk deletion staging failed; recovery files remain: " + ", ".join(failed))
                raise
            pending = []
            for _original, temporary, _size in staged:
                try:
                    os.unlink(temporary)
                except OSError:
                    pending.append(temporary)
            editorial_pending = False
            if editorial_backup:
                try:
                    os.unlink(editorial_backup)
                except OSError:
                    editorial_pending = True
            return {"ok":True, "deleted_revisions":preview["revisions"],
                "editorial_releases":releases,
                "deleted_files":len(staged) - len(pending), "cleanup_pending":len(pending) + int(editorial_pending),
                "reclaimed_bytes":sum(size for _path, temp, size in staged if temp not in pending),
                "message":"Deleted %d selected checkpoint revisions. Unselected takes and shared files were kept.%s" %
                    (len(preview["revisions"]), " Some staged files still need cleanup." if pending or editorial_pending else "")}
