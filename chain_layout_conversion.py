"""Explicit, offline, copy-only layout conversion. Never imported by UI routes.

Historical JSON/tensor/media bytes are not rewritten. Existing embedded relative
addresses resolve through chain_layout; a single source-root alias also supports
absolute project addresses while testing the copy in a separate output root.
"""

from dataclasses import dataclass
from pathlib import Path
import os
import shutil

try:
    from .chain_layout import install_marker, organized_relative, organized
except ImportError:
    from chain_layout import install_marker, organized_relative, organized


@dataclass(frozen=True)
class Move:
    source: Path
    destination: Path
    size: int
    mtime_ns: int


def preview(source):
    """One explicit directory walk; no model loading, JSON rewriting or hashing."""
    source = Path(source).absolute()
    if source.is_symlink() or source.resolve() != source or not source.is_dir():
        raise ValueError("Choose a real project directory, not a symlink.")
    if source.parent.name != "h3_chains":
        raise ValueError("Choose the project directly inside h3_chains.")
    if organized(source) or (source / ".h3").exists():
        raise ValueError("Source already has internal .h3 storage; no conversion was made.")
    moves, destinations = [], set()
    for folder, directories, files in os.walk(source, followlinks=False):
        for name in directories + files:
            path = Path(folder) / name
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise ValueError("Conversion does not follow links: %s" % path)
        for name in files:
            path = Path(folder) / name
            if not path.is_file():
                raise ValueError("Conversion only copies regular files: %s" % path)
            relative = path.relative_to(source)
            destination = organized_relative(relative)
            if destination in destinations:
                raise ValueError("Two files would use %s; source was not changed." % destination)
            destinations.add(destination)
            stat = path.stat()
            moves.append(Move(relative, destination, stat.st_size, stat.st_mtime_ns))
    for destination in destinations:
        if any(parent in destinations for parent in destination.parents):
            raise ValueError("File/directory collision at %s." % destination)
    return sorted(moves, key=lambda item: item.source.as_posix())


def _unchanged(path, move):
    stat = path.stat()
    if (stat.st_size, stat.st_mtime_ns) != (move.size, move.mtime_ns):
        raise ValueError("Source changed during conversion: %s. Stop its writers and retry." % path)


def _identical(source, destination):
    with source.open("rb") as left, destination.open("rb") as right:
        while True:
            block = left.read(1024 * 1024)
            if block != right.read(1024 * 1024):
                raise OSError("Copy verification failed: %s" % destination)
            if not block:
                break


def convert_copy(source, destination_output, *, progress=None):
    """Copy into a NEW output/h3_chains/<same-run> for isolated testing.

    This does not activate the copy, delete backups or change the source. On
    failure the incomplete destination is retained without a layout marker.
    ComfyUI and other project writers must be stopped by the caller.
    """
    source = Path(source).absolute()
    destination_output = Path(destination_output).absolute()
    destination = destination_output / "h3_chains" / source.name
    if (destination.resolve() != destination or destination.is_relative_to(source)
            or source.is_relative_to(destination) or destination.exists()):
        raise ValueError("Destination must be a new, separate project path without symlinks.")
    moves = preview(source)
    # Locate the destination filesystem without creating a project on dry run.
    existing_parent = destination_output
    while not existing_parent.exists():
        existing_parent = existing_parent.parent
    required = sum(item.size for item in moves)
    if shutil.disk_usage(existing_parent).free < required:
        raise OSError("Not enough space for the verified copy (%d bytes)." % required)
    destination.mkdir(parents=True, exist_ok=False)
    for offset, move in enumerate(moves, 1):
        src, dst = source / move.source, destination / move.destination
        _unchanged(src, move)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst, follow_symlinks=False)
        _identical(src, dst)
        _unchanged(src, move)
        if progress is not None:
            progress(offset, len(moves), move)
    # Detect added/removed/changed files too; this work is conversion-only.
    if preview(source) != moves:
        raise ValueError("Project changed during conversion; the incomplete copy was retained.")
    install_marker(destination, legacy_project_roots=[source])
    return {"source": str(source), "destination": str(destination),
            "files": len(moves), "bytes": required, "verified": True,
            "activated": False, "source_unchanged": True}
