"""Discard one branch's saved paths, retaining other branches and their inputs."""

import json
import os
from pathlib import Path
import re
import uuid

from .branch_scope import branch_id, branch_scope, working_directory
from .chain_layout import state_root, resolve_path
from .checkpoint_manager import CheckpointDeleteBlocked, _fingerprint, checkpoint_run_lock
from .working_branches import WorkingBranches


class WorkingBranchCleanup:
    def __init__(self, output_root, run):
        self.store = WorkingBranches(output_root, run)

    def _safe(self, path):
        path = Path(resolve_path(path))
        relative = path.relative_to(self.store.root)
        current = self.store.root
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                raise ValueError("Branch cleanup cannot follow symlinks.")
        if not path.resolve().is_relative_to(self.store.root):
            raise ValueError("Branch cleanup path escapes the project.")
        return path

    @staticmethod
    def _closure(records, seeds, edge):
        found, pending = set(), list(seeds)
        while pending:
            key = pending.pop()
            if key in found or key not in records:
                continue
            found.add(key)
            pending.extend(records[key][edge])
        return found

    def _plan(self, selected, keep_branch):
        from .checkpoint_manager import CheckpointGraphManager

        selected, keep_branch = branch_id(selected), branch_id(keep_branch)
        if selected == keep_branch:
            raise ValueError("Keep the branch open in Plan Studio; select an obsolete branch to delete.")
        store = self.store
        listing = store.listing()
        store._load_record(keep_branch)
        selected_record = store._load_record(selected)
        manager = CheckpointGraphManager(str(store.output))
        with branch_scope(store.run, selected):
            scan = manager._scan(store.run, adopt_legacy=False)
        records = scan["records"]
        by_revision = {}
        for key in records:
            by_revision.setdefault(key[1], set()).add(key)
        observed, archive, seeds, protected = {}, set(), set(), set()
        chapters = 0

        def read(path):
            path = self._safe(path)
            if not path.exists():
                observed[str(path)] = None
                return None
            data = path.read_bytes()
            stat = path.stat()
            observed[str(path)] = [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
                                    _fingerprint(data.hex())]
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError("Cannot verify branch metadata: " + path.name)
            return value

        def references(value):
            # Includes archived artifact filenames, not just revision fields.
            # Superseded/audit refs conservatively retain extra files.
            return {key for token in re.findall(
                r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])", json.dumps(value).lower())
                for key in by_revision.get(token, ())}

        for item in listing["branches"]:
            identity = item["id"]
            directory = self._safe(working_directory(str(store.root), store.run, identity))
            pointer_dir = self._safe(directory / "checkpoints")
            if list((pointer_dir / ".transactions").glob("restore.*.json")):
                raise ValueError("Checkpoint assignment recovery is pending; refresh first.")
            destinations = seeds if identity == selected else protected
            paths = list(pointer_dir.glob("clip_????.json"))
            paths.append(directory / "editorial.json")
            chapter_dir = self._safe(directory / "chapters")
            if chapter_dir.exists():
                for child in chapter_dir.iterdir():
                    manifests = self._safe(child / "manifests")
                    if manifests.is_dir():
                        paths.extend(manifests.glob("*.json"))
            authoring = read(store._path(identity))
            if identity != selected:
                # A retained branch may have authored context-source choices
                # even before its first new clip has been generated.
                protected.update(references(authoring))
                protected.update(references(read(directory / "plan.json")))
            for path in sorted(paths):
                value = read(path)
                if value is None:
                    continue
                if path.parent.name == "manifests" and (
                        value.get("format") != "h3_chain_chapter_manifest_v1"
                        or value.get("run_name") != store.run
                        or not isinstance(value.get("chapter"), dict)
                        or not isinstance(value.get("segments"), list)
                        or not value["segments"]):
                    raise ValueError("Cannot verify chapter snapshot " + path.name)
                refs = references(value)
                if path.name.startswith("clip_"):
                    segment = value.get("segment") or {}
                    key = (int(path.stem[5:]), segment.get("revision"))
                    if key not in records:
                        raise ValueError("Cannot verify assigned checkpoint " + path.name)
                    refs.add(key)
                destinations.update(refs)
                if identity == selected:
                    archive.add(self._safe(path))
                    chapters += path.parent.name == "manifests"
                    if path.parent.name == "manifests":
                        retired = self._safe(path.parent.parent / "retired_manifests" / path.name)
                        if retired.exists():
                            raise ValueError("A retired snapshot copy already exists: " + path.name)

        # Do not silently ignore malformed immutable metadata skipped by _scan.
        for path in self._safe(Path(state_root(store.root)) / "checkpoints").glob("clip_*.*.json"):
            self._safe(path)
            match = re.fullmatch(r"clip_(\d{4})\.([0-9a-f]{32})\.json", path.name)
            if match and (int(match[1]), match[2]) not in records:
                raise ValueError("Cannot verify saved checkpoint " + path.name)
        for record in records.values():
            read(record["_metadata_path"])

        candidates = self._closure(records, seeds, "_dependents")
        # Everything outside the discarded paths stays, including unswitched
        # takes. Follow their dependencies backwards so shared inputs stay too.
        retained = self._closure(records, (set(records) - candidates) | protected, "_dependencies")
        deleting = candidates - retained
        artifacts = {}
        for key in sorted(deleting):
            record = records[key]
            if record.get("_lineage_issue"):
                raise ValueError("Cannot verify saved path: " + str(record["_lineage_issue"]))
            for artifact in manager._artifacts(scan, record):
                path = self._safe(artifact["_path"])
                if artifact["kind"].startswith("archive_"):
                    artifact = dict(artifact, owned=False)
                previous = artifacts.get(str(path))
                if previous:
                    artifact = dict(artifact, owned=artifact["owned"] and previous["owned"],
                                    shared=artifact["shared"] or previous["shared"])
                artifacts[str(path)] = artifact
        files = [{key: value for key, value in item.items() if not key.startswith("_")}
                 for item in artifacts.values()]
        owned = [item for item in files if item["owned"] and item["exists"]]
        targets = lambda keys: [{"scene": key[0], "revision": key[1]} for key in sorted(keys)]
        preview = {"ok": True, "allowed": bool(archive or deleting), "run_name": store.run,
            "branch_id": selected, "branch_name": selected_record["name"], "keep_branch_id": keep_branch,
            "revisions": targets(deleting), "retained_revisions": targets(candidates & retained),
            "retired_snapshots": chapters, "files": files,
            "owned_file_count": len(owned), "reclaimed_bytes": sum(item["size_bytes"] for item in owned),
            "released_files": [path.relative_to(store.output).as_posix() for path in sorted(archive)],
            "message": "Clear this branch's saved assignments and edit, and retire its chapter snapshots. "
                       "Delete only unused takes on those paths. Other branches, shared inputs, "
                       "processing results, exports and authored Plans stay unchanged. The branch remains available for new work."}
        preview["snapshot"] = _fingerprint({"preview": preview, "observed": observed,
            "branches": listing, "artifacts": [[path, item["_mtime_ns"]] for path, item in artifacts.items()]})
        return preview, archive, artifacts

    def preview(self, selected, keep_branch):
        with checkpoint_run_lock(str(self.store.output), self.store.run):
            return self._plan(selected, keep_branch)[0]

    def delete(self, selected, keep_branch, expected_snapshot):
        store = self.store
        with checkpoint_run_lock(str(store.output), store.run):
            preview, archive, artifacts = self._plan(selected, keep_branch)
            if not preview["allowed"] or not expected_snapshot or expected_snapshot != preview["snapshot"]:
                raise CheckpointDeleteBlocked("Branch files or dependencies changed; preview deletion again.", preview)
            transaction = uuid.uuid4().hex
            recovery = self._safe(Path(state_root(store.root)) / "branches" / "discarded_paths" / transaction)
            staged, garbage = [], []
            try:
                for path in sorted(archive):
                    destination = self._safe(path.parent.parent / "retired_manifests" / path.name
                        if path.parent.name == "manifests" else recovery / path.relative_to(store.root))
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(path, destination)
                    staged.append((path, destination))
                for path, item in artifacts.items():
                    if not item["owned"] or not item["exists"]:
                        continue
                    source = Path(path)
                    temporary = source.with_name(source.name + ".delete." + transaction + ".tmp")
                    os.replace(source, temporary)
                    staged.append((source, temporary))
                    garbage.append((temporary, item["size_bytes"]))
            except Exception:
                failed = []
                for source, destination in reversed(staged):
                    try:
                        os.replace(destination, source)
                    except OSError:
                        failed.append(str(destination))
                if failed:
                    raise OSError("Branch cleanup rollback needs recovery from: " + ", ".join(failed))
                raise
            pending, reclaimed = 0, 0
            for path, size in garbage:
                try:
                    path.unlink()
                    reclaimed += size
                except OSError:
                    pending += 1
            return {"ok": True, "reclaimed_bytes": reclaimed, "cleanup_pending": pending,
                "deleted_revisions": preview["revisions"], "retained_revisions": preview["retained_revisions"],
                "recovery_path": recovery.relative_to(store.output).as_posix(),
                "message": "Cleared %s's saved paths; deleted %d unused takes and kept %d shared takes.%s" % (
                    preview["branch_name"], len(preview["revisions"]), len(preview["retained_revisions"]),
                    " Some staged files still need cleanup." if pending else "")}
