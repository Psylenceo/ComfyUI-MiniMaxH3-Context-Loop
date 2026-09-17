#!/usr/bin/env python3
"""Pointer assignment never reads large media; consuming it still verifies it."""
import asyncio
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "reattribution", Path(__file__).with_name("_checkpoint_context_reattribution_unit_test.py"))
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
chain = h.chain


async def check(run, manager, originals, selected):
    request = h.h.h.JsonRequest({
        "run_name":run.name, "activate_only":True, "resume_scene":19,
        "scope_start_scene":8, "scope_end_scene":18,
        "revisions":[{"scene":i, "revision":selected[i]} for i in range(8, 19)],
    })
    root = run.parent.parent
    hashed = []
    hash_file = chain._file_sha256

    def small_sidecars_only(path):
        assert Path(path).suffix in (".json", ".txt"), f"assignment read media: {path}"
        hashed.append(path)
        return hash_file(path)

    with patch.object(chain, "_file_sha256", side_effect=small_sidecars_only):
        response = await chain._restore_checkpoint_revisions(request)
    assert response.status == 200, response.text
    assert hashed, "prompt integrity must still be checked"

    # Availability checks still reject missing media before changing pointers.
    segment = originals[18]["segment"]
    for key in ("segment", "checkpoint", "prompt_file"):
        path = root / segment[key]
        held = path.with_suffix(path.suffix + ".held")
        before = h.h.snapshot(run)
        path.rename(held)
        try:
            response = await chain._restore_checkpoint_revisions(request)
            assert response.status == 404, response.text
        finally:
            held.rename(path)
        assert h.h.snapshot(run) == before

    # Activation doesn't consume a latent; execution/export must still reject
    # its corrupted bytes (even though its metadata and path remain valid).
    path = root / segment["checkpoint"]
    data = path.read_bytes()
    try:
        path.write_bytes(b"corrupt")
        with patch.object(chain, "_file_sha256", side_effect=small_sidecars_only):
            response = await chain._restore_checkpoint_revisions(request)
        assert response.status == 200, response.text
        try:
            chain._load_checkpoint_revision(run.name, 18, selected[18])
        except ValueError as error:
            assert "SHA-256" in str(error)
        else:
            raise AssertionError("execution accepted corrupt media")
    finally:
        path.write_bytes(data)
    print("Assignment: 11-scene path, metadata/prompt reads only, missing-file rejection and full consumer hash checks pass")


if __name__ == "__main__":
    asyncio.run(h.main(check))
