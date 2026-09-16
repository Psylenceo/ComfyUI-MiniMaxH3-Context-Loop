"""Opt-in, post-export disposal of manifest-owned checkpoint payloads only.

No startup hooks, media traversal, metadata rewriting or tensor loading. The
caller has verified the manifest before taking this snapshot. Recovery from
deleted checkpoints is deliberately unavailable; saved videos remain intact.
"""

import json
from pathlib import Path
import re
import stat

from .artifact_paths import artifact_address, is_link_or_junction
from .chain_layout import resolve_path, state_root
from .checkpoint_manager import checkpoint_run_lock, checkpoint_revision_token, _strict_run_name
from .processing_persistence import sync_file, sync_directory


_CHECKPOINT = re.compile(r"clip_\d{4}(?:\.[0-9a-f]{32})?\.safetensors")
_REVISION_JSON = re.compile(r"clip_\d{4}\.[0-9a-f]{32}\.json")
_FULL_FORMATS = {"h3_chain_manifest_v3", "h3_chain_chapter_manifest_v1",
                 "h3_chain_upscale_manifest_v1"}


def _stamp(path):
    value = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(value.st_mode):
        raise ValueError("checkpoint is not a regular file")
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
            value.st_ctime_ns, value.st_nlink)


def _strings(value):
    if isinstance(value, str):
        yield value.replace("\\", "/")
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


class AssemblyCheckpointCleanup:
    def __init__(self, output_root, manifest):
        self.output = Path(output_root).resolve()
        self.run_name = _strict_run_name(manifest.get("run_name"))
        self.root = self.output / "h3_chains" / self.run_name
        self.state = Path(state_root(self.root))
        source = manifest.get("source_manifest") or manifest
        self.branch = str(source.get("_branch_id", manifest.get("_branch_id", "main")))
        self.reason = ""
        self.files = {}
        chapter = source.get("chapter") or {}
        if (manifest.get("format") not in _FULL_FORMATS
                or chapter.get("complete") is False
                or (manifest.get("upscale") or {}).get("complete") is False):
            self.reason = "partial assembly; checkpoints kept"
            return
        segments = manifest.get("segments") or []
        if not segments or len(segments) != int(manifest.get("clip_count", 0)):
            self.reason = "incomplete manifest; checkpoints kept"
            return
        for segment in segments:
            address = artifact_address(segment.get("checkpoint"))
            path = self._path(self.output / address)
            parts = path.relative_to(self.state).parts
            if len(parts) > 2 and parts[0] == "branches" and re.fullmatch(r"[0-9a-f]{32}", parts[1]):
                parts = parts[2:]
            managed = (len(parts) == 2 and parts[0] == "checkpoints" or
                       len(parts) == 4 and parts[0] == "upscaled" and parts[-2] == "checkpoints" or
                       len(parts) == 6 and parts[0] == "chapters" and parts[2] == "upscaled" and parts[-2] == "checkpoints")
            if not managed or not _CHECKPOINT.fullmatch(path.name):
                raise ValueError("not a managed checkpoint payload")
            digest = str(segment.get("checkpoint_sha256") or "")
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("checkpoint has no verified identity")
            self.files[self._identity(address)] = {
                "path": path, "stamp": _stamp(path), "hash": digest,
                "revision": checkpoint_revision_token(segment.get("index"), segment),
            }

    def _path(self, path):
        # Resolve layout addresses lexically, retaining the link checks below.
        # output_path()/Path.resolve() would follow links before we check them.
        path = Path(resolve_path(path))
        # Do not follow symlinks/junctions, including parents inside the run.
        relative = path.relative_to(self.output)
        if relative.parts[:2] != ("h3_chains", self.run_name):
            raise ValueError("checkpoint path is outside this project")
        current = self.output
        for part in relative.parts:
            current /= part
            if is_link_or_junction(current):
                raise ValueError("linked checkpoint/metadata path; files kept")
        return path

    def _identity(self, value):
        # Converted immutable metadata keeps legacy addresses; newer records
        # use .h3 addresses. Both denote the same owner, without rewriting JSON.
        prefix = "h3_chains/%s/" % self.run_name
        if value.startswith(prefix + ".h3/"):
            return prefix + value[len(prefix + ".h3/"):]
        return value

    def _documents(self):
        """Read only known metadata directories, never frames/assets/caches."""
        def documents(folder, branch):
            self._path(folder)
            if not folder.exists():
                return
            for name in ("manifest.json", "upscale_manifest.json", "editorial.json"):
                path = self._path(folder / name)
                if path.is_file():
                    yield path, branch
            for name in ("checkpoints", "partial", "manifests"):
                directory = self._path(folder / name)
                if directory.is_dir():
                    pending = self._path(directory / ".transactions")
                    if pending.is_dir() and any(pending.iterdir()):
                        raise ValueError("checkpoint assignment is pending; files kept")
                    for path in sorted(directory.iterdir()):
                        if path.suffix == ".json":
                            yield self._path(path), branch
            for name in ("chapters", "upscaled", "branches"):
                directory = self._path(folder / name)
                if directory.is_dir():
                    for child in sorted(directory.iterdir()):
                        self._path(child)
                        if child.is_dir():
                            # Ignore branch authoring backups, which contain
                            # no live checkpoint assignments.
                            if name == "branches" and not re.fullmatch(r"[0-9a-f]{32}", child.name):
                                continue
                            yield from documents(child, child.name if name == "branches" else branch)
        yield from documents(self.state, "main")

    def _protected(self):
        identities = {}
        for address, item in self.files.items():
            for value in (address, item["hash"], item["revision"]):
                if value:
                    identities.setdefault(value, set()).add(address)
        protected = set()
        for path, branch in self._documents():
            with path.open(encoding="utf-8") as handle:
                document = json.load(handle)
            if not isinstance(document, dict):
                raise ValueError("unreadable checkpoint ownership metadata")
            records = ([document["segment"]] if isinstance(document.get("segment"), dict)
                       else document.get("segments") or [])
            own_records = bool(records) and all(
                isinstance(item, dict)
                and (address := self._identity(artifact_address(item.get("checkpoint")))) in self.files
                and item.get("checkpoint_sha256") == self.files[address]["hash"]
                for item in records)
            inventory_record = path.parent == self.state / "checkpoints" and _REVISION_JSON.fullmatch(path.name)
            if own_records and (branch == self.branch or inventory_record):
                # This completed lineage's pointers, immutable metadata and
                # partial snapshots are not other owners. Do not erase them.
                continue
            for value in _strings(document):
                protected.update(identities.get(self._identity(value), ()))
        return protected

    def finish(self, published_files):
        if self.reason:
            return "checkpoint cleanup skipped: " + self.reason
        try:
            if not published_files:
                raise ValueError("no completed export")
            for path in map(Path, published_files):
                if not path.is_file() or path.stat().st_size < 1:
                    raise ValueError("export is empty or missing")
                sync_file(path)
                sync_directory(path.parent)
        except (OSError, ValueError) as exc:
            return "checkpoint cleanup skipped: export not safely saved (%s)" % exc
        deleted = freed = kept = 0
        warnings = []
        with checkpoint_run_lock(str(self.output), self.run_name):
            try:
                protected = self._protected()
            except (OSError, ValueError, TypeError) as exc:
                return "checkpoint cleanup skipped: %s" % exc
            for address, item in self.files.items():
                try:
                    path = self._path(item["path"])
                    if address in protected or item["stamp"][-1] != 1:
                        kept += 1
                        continue
                    if _stamp(path) != item["stamp"]:
                        kept += 1
                        warnings.append("changed checkpoint kept")
                        continue
                    path.unlink()
                    deleted += 1
                    freed += item["stamp"][2]
                except FileNotFoundError:
                    continue  # Another completed export already removed it.
                except (OSError, ValueError) as exc:
                    kept += 1
                    warnings.append(str(exc))
        detail = "checkpoint cleanup: deleted %d file(s), freed %.2f MiB; kept %d shared/changed file(s)" % (
            deleted, freed / (1024 * 1024), kept)
        if warnings:
            detail += "; " + "; ".join(dict.fromkeys(warnings))
        return detail
