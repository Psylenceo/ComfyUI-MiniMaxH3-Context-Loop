"""Deterministic paths for legacy and organized H3 projects.

This is a path adapter, not an inventory or an artifact database. Reading a
layout opens one small marker (cached); it never walks a project or opens media.
Only explicit writers call ``create_project``. Existing directories stay legacy.
"""

from functools import lru_cache
import json
import os
from pathlib import Path


FORMAT = "h3_directory_layout"
VERSION = 1
PUBLIC = frozenset({"generation", "processing", "exports", "assets", "extras"})
INTERNAL = frozenset({
    "branches", "chapters", "checkpoints", "blend_segments", "upscaled",
    "recovery_archives", "reviews", "pending_reviews", "prompt_history",
    "authoring_backups", "reference_cache", "references", "source",
    "source_timeline", "alternates", "partial", "orchestration",
})


@lru_cache(maxsize=512)
def _layout(project):
    marker = Path(project) / ".h3" / "layout.json"
    try:
        with marker.open(encoding="utf-8") as handle:
            value = json.load(handle)
    except (FileNotFoundError, NotADirectoryError):
        return None
    if marker.is_symlink() or marker.parent.is_symlink():
        raise ValueError("H3 layout marker and .h3 directory must not be symlinks.")
    if (not isinstance(value, dict) or value.get("format") != FORMAT
            or value.get("version") != VERSION
            or not isinstance(value.get("legacy_project_roots", []), list)
            or not all(isinstance(item, str) and os.path.isabs(item)
                       for item in value.get("legacy_project_roots", []))):
        raise ValueError("Unrecognized H3 directory layout: %s" % marker)
    return value


def organized(project):
    return _layout(str(project)) is not None


def forget_layout():
    """Called after explicit creation/conversion, never from a polling loop."""
    _layout.cache_clear()


def create_project(project):
    """Create an organized project, leaving every existing project unchanged.

    Call under the existing project write lock, before constructing save paths.
    No layout is inferred from the contents of an existing directory.
    """
    project = Path(project).absolute()
    if project.parent.name != "h3_chains":
        raise ValueError("Create projects directly inside h3_chains.")
    # Reuse the existing cross-process write lock, only on a write operation.
    # In particular, an asset import cannot observe another writer halfway
    # through installing a new project's marker and start writing legacy paths.
    try:
        from .checkpoint_manager import checkpoint_run_lock
    except ImportError:
        from checkpoint_manager import checkpoint_run_lock
    with checkpoint_run_lock(str(project.parent.parent), project.name):
        if project.exists():
            if _layout(str(project)) is None and (project / ".h3/layout.json").is_file():
                forget_layout()
            return
        project.mkdir(parents=True, exist_ok=False)
        try:
            install_marker(project)
        except Exception:
            # Keep a partially created directory for diagnosis, never remove it.
            forget_layout()
            raise


def install_marker(project, *, legacy_project_roots=()):
    """For new projects and the explicit copy converter only."""
    folder = Path(project) / ".h3"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "layout.json").open("x", encoding="utf-8") as handle:
        value = {"format": FORMAT, "version": VERSION}
        if legacy_project_roots:
            value["legacy_project_roots"] = list(map(str, legacy_project_roots))
        json.dump(value, handle)
        handle.write("\n")
    forget_layout()


def project_root(path):
    """Locate the enclosing project lexically; no directory traversal/I/O."""
    path = Path(os.path.abspath(path))
    parts = path.parts
    for index in range(len(parts) - 2, -1, -1):
        if parts[index] == "h3_chains" and index + 1 < len(parts):
            return Path(*parts[:index + 2])
    return None


def state_root(project):
    project = Path(project)
    # Accept an already selected state root (working_directory's callers).
    if project.name == ".h3":
        return str(project)
    return str(project / ".h3" if organized(str(project)) else project)


def _scope(parts):
    """Keep readable scopes losslessly encoded; no name registry or hash lookup."""
    parts = list(parts)
    labels = ["original"]
    if len(parts) >= 2 and parts[0] == "branches":
        labels = ["branch-" + parts[1]]
        parts = parts[2:]
    if len(parts) >= 2 and parts[0] == "chapters":
        labels.append(parts[1])
        parts = parts[2:]
    return "__".join(labels), parts


def organized_relative(relative):
    """Translate one legacy project-relative address. Pure, lossless mapping.

    Internal metadata retains its old relative structure below .h3. This keeps
    branch/chapter/profile identity independent of display/media directories.
    """
    parts = Path(relative).parts
    if not parts:
        return Path(".h3")
    internal = parts[0] == ".h3"
    if internal:
        parts = parts[1:]
    elif parts[0] in PUBLIC:
        return Path(*parts)
    if not parts:
        return Path(".h3")
    if parts[0] == "segments":
        return Path("generation", "clips", *parts[1:])
    if parts[0] == "generated_audio":
        return Path("generation", "audio", *parts[1:])
    if parts[0] == "project_assets":
        return Path("assets", *parts[1:])
    scope, remaining = _scope(parts)
    if len(remaining) >= 2 and remaining[0] == "upscaled":
        profile, remaining = remaining[1], remaining[2:]
        if remaining:
            role, rest = remaining[0], remaining[1:]
            if role in {"segments", "audio"}:
                return Path("processing", scope, profile,
                            "clips" if role == "segments" else "audio", *rest)
            if role in {"frames", "final"}:
                return Path("exports", "frames" if role == "frames" else "videos",
                            scope, "pass-" + profile, *rest)
    elif remaining and remaining[0] in {"frames", "final"}:
        return Path("exports", "frames" if remaining[0] == "frames" else "videos",
                    scope, "generation", *remaining[1:])
    if (not internal and len(parts) > 1 and parts[0] not in INTERNAL
            and not parts[0].startswith(".")):
        return Path("extras", *parts)
    return Path(".h3", *parts)


def resolve_path(path):
    """Resolve saved legacy addresses as well as current physical addresses."""
    path = Path(os.path.abspath(path))
    project = project_root(path)
    if project is None or path == project or not organized(str(project)):
        return str(path)
    return str(project / organized_relative(path.relative_to(project)))


def output_path(output_root, address):
    value = str(address).replace("\\", "/")
    path = Path(value) if os.path.isabs(value) else Path(output_root) / value
    saved_project = project_root(path)
    if saved_project is not None:
        local_project = Path(output_root) / "h3_chains" / saved_project.name
        layout = _layout(str(local_project))
        if layout and str(saved_project) in layout.get("legacy_project_roots", []):
            path = local_project / path.relative_to(saved_project)
    resolved = os.path.realpath(resolve_path(path))
    root = os.path.realpath(output_root)
    try:
        contained = os.path.commonpath((root, resolved)) == root
    except ValueError:
        # Windows rejects comparisons across drives/UNC shares. Report the
        # same explicit containment error as other out-of-root addresses.
        contained = False
    if not contained:
        raise ValueError("H3 artifact path escapes the output directory.")
    return resolved


def profile_artifact(profile, role, filename=""):
    """Physical role directory/file for either layout; no existence probing."""
    return resolve_path(Path(profile) / role / filename)


def frame_export_directory(scope, audio_only=False):
    path = Path(resolve_path(Path(scope) / "frames"))
    project = project_root(path)
    if audio_only and project is not None and organized(project):
        relative = path.relative_to(project).parts
        path = project / "exports" / "audio" / Path(*relative[2:])
    return str(path)


def profile_contains(profile, path):
    path = Path(path).resolve()
    return any(path.is_relative_to(Path(parent).resolve()) for parent in (
        profile, profile_artifact(profile, "segments"),
        profile_artifact(profile, "audio")))


def logical_parts(address):
    """For existing identity checks on technical addresses, not for file I/O."""
    parts = Path(address).parts
    if len(parts) > 2 and parts[0] == "h3_chains" and parts[2] == ".h3":
        parts = parts[:2] + parts[3:]
    return parts
