"""Small durable SelfLift review index and atomic, pickle-free middle passes.

No startup discovery, media scans or tensor reads. Reviews only read manifests;
large tensors are opened by the executing sampler, never the browser poller.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

from .branch_scope import working_directory, branch_id
from .checkpoint_manager import checkpoint_run_lock, _strict_run_name
from .chain_layout import resolve_path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _sync_directory(path):
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass  # Windows does not support directory fsync.
    finally:
        os.close(fd)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def save_bundle(path, value):
    """One committed file contains both the tensor tree and its schema.

    A process killed before rename leaves only an ignored temporary file. A
    process killed after rename can resume even before the manifest is updated.
    """
    import torch
    from safetensors.torch import save_file
    tensors, memo = {}, {}

    def encode(item):
        if getattr(item, "is_nested", False):
            return {"kind": "nested", "items": [encode(v) for v in item.unbind()]}
        if torch.is_tensor(item):
            key = memo.get(id(item))
            if key is None:
                key = "tensor_%06d" % len(tensors)
                memo[id(item)] = key
                tensors[key] = item.detach().cpu().contiguous().clone()
            return {"kind": "tensor", "key": key}
        if isinstance(item, dict):
            return {"kind": "dict", "items": [[encode(k), encode(v)] for k, v in item.items()]}
        if isinstance(item, (tuple, list)):
            return {"kind": "tuple" if isinstance(item, tuple) else "list",
                    "items": [encode(v) for v in item]}
        if item is None or isinstance(item, (str, bool, int, float)):
            return {"kind": "value", "value": item}
        raise TypeError("SelfLift handoff cannot persist %s; keep conditioning tensor-based." % type(item).__name__)

    tree = json.dumps(encode(value), allow_nan=False, separators=(",", ":"))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        save_file(tensors, str(temporary), metadata={"format": "h3_selflift_bundle_v1", "tree": tree})
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def load_bundle(path):
    from safetensors import safe_open
    with safe_open(str(path), framework="pt", device="cpu") as handle:
        meta = handle.metadata() or {}
        if meta.get("format") != "h3_selflift_bundle_v1":
            raise ValueError("Not a SelfLift middle-pass bundle.")

        def decode(item):
            kind = item["kind"]
            if kind == "tensor":
                return handle.get_tensor(item["key"])
            if kind == "value":
                return item["value"]
            if kind == "dict":
                return {decode(k): decode(v) for k, v in item["items"]}
            values = [decode(v) for v in item["items"]]
            if kind == "tuple":
                return tuple(values)
            if kind == "list":
                return values
            if kind == "nested":
                from comfy.nested_tensor import NestedTensor
                return NestedTensor(values)
            raise ValueError("Invalid SelfLift bundle tree.")
        return decode(json.loads(meta["tree"]))


class HuntStore:
    def __init__(self, output_root):
        self.root = Path(output_root).resolve()
        self.index_path = self.root / "h3_chains" / ".selflift_reviews.json"

    def _index(self):
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    def directory(self, record):
        run = _strict_run_name(record["run_name"])
        branch = branch_id(record.get("branch_id", "main"))
        key = str(record["id"])
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ValueError("Invalid SelfLift review id.")
        folder = Path(working_directory(self.root / "h3_chains" / run, run, branch)) / "reviews" / "selflift" / key
        if not folder.resolve().is_relative_to(self.root / "h3_chains" / run):
            raise ValueError("SelfLift review directory escapes the project.")
        return folder

    def locate(self, key):
        record = self._index().get(str(key))
        if record is None:
            raise ValueError("Saved SelfLift review not found.")
        return self.directory(record)

    def read(self, key):
        return json.loads((self.locate(key) / "batch.json").read_text(encoding="utf-8"))

    def create(self, record):
        from .chain_layout import create_project
        create_project(self.root / "h3_chains" / _strict_run_name(record["run_name"]))
        folder = self.directory(record)
        # Register before sampling. An interrupted creation is harmless and
        # can be completed on rerun; no recursive recovery scan is needed.
        with checkpoint_run_lock(str(self.root), "selflift_review_index"):
            index = self._index()
            index[record["id"]] = {key: record[key] for key in ("id", "run_name", "branch_id", "scene")}
            atomic_json(self.index_path, index)
        with checkpoint_run_lock(str(self.root), record["run_name"]):
            if not (folder / "batch.json").exists():
                atomic_json(folder / "batch.json", dict(record, created_at=time.time(), candidates=[], selected=None))
        return self.read(record["id"])

    def update(self, key, transform):
        record = self.read(key)
        with checkpoint_run_lock(str(self.root), record["run_name"]):
            record = self.read(key)
            transform(record)
            atomic_json(self.locate(key) / "batch.json", record)
        return record

    def preview_path(self, record, ordinal):
        run = record["run_name"]
        working = Path(working_directory(self.root / "h3_chains" / run, run, record["branch_id"]))
        return Path(resolve_path(working / "upscaled" / "selflift_seed_hunt" / "segments" /
                                 record["id"] / ("take_%04d.mp4" % ordinal)))

    def remove(self, key, expected=None):
        """Delete only this batch's scratch bundles/previews, never scene assets.

        Callers exclude executing hunts. Preflight both flat directories before
        deleting anything; never trust checkpoint/preview paths from batch JSON.
        Leave the index until last so an interrupted cleanup can be retried.
        """
        entry = self._index().get(str(key))
        if entry is None:
            return {"files": 0, "bytes": 0}
        run = _strict_run_name(entry["run_name"])
        with checkpoint_run_lock(str(self.root), run):
            if expected:
                record = self.read(key)
                if any(record.get(k) != v for k, v in expected.items()):
                    raise ValueError("This saved hunt changed; refresh before cleaning it.")
            folder = self.directory(entry)
            previews = self.preview_path(entry, 1).parent
            project = self.root / "h3_chains" / run
            bundle_name = r"(?:batch\.json|recovery\.json|source\.safetensors|(?:take|finished)_\d{4,}\.safetensors)(?:\.[0-9a-f]{32}\.tmp)?"
            preview_name = r"take_\d{4,}(?:\.[0-9a-f]{32}\.tmp)?\.mp4"
            files = []
            for directory, pattern in ((folder, bundle_name), (previews, preview_name)):
                # Reject linked directories (including parents) and unknown
                # contents instead of recursively deleting an arbitrary tree.
                if (directory.name != str(key) or directory.resolve() != directory
                        or not directory.is_relative_to(project)):
                    raise ValueError("Unsafe SelfLift cleanup directory.")
                if not directory.exists():
                    continue
                for path in directory.iterdir():
                    if not re.fullmatch(pattern, path.name) or path.is_symlink() or not path.is_file():
                        raise ValueError("Unexpected file in SelfLift cleanup directory: " + path.name)
                    files.append(path)
            result = {"files": 0, "bytes": 0}
            for path in sorted(files, key=lambda p: p.name == "batch.json"):
                size = path.stat().st_size
                path.unlink()
                result["files"] += 1
                result["bytes"] += size
            for directory in (previews, folder):
                if directory.exists():
                    directory.rmdir()
            with checkpoint_run_lock(str(self.root), "selflift_review_index"):
                index = self._index()
                index.pop(str(key), None)
                atomic_json(self.index_path, index)
            return result

    def list(self):
        result = []
        for key in self._index():
            try:
                result.append(self.read(key))
            except (FileNotFoundError, ValueError):
                continue
        return sorted(result, key=lambda item: item["created_at"], reverse=True)
