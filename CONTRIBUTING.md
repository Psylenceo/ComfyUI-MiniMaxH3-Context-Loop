# Contributing

Focused fixes, workflow improvements, compatibility updates, and documentation
corrections are welcome. Open an issue before a large redesign so its runtime
and saved-workflow compatibility can be discussed first.

## Preserve supported contracts

The original Context Loop Plan remains supported in 0.7. Historical checkpoint
readers are retained, but removed nodes and inputs need explicit migration:
see [Migrating to 0.7](docs/MIGRATING_TO_0_7.md). Do not promise that every 0.4
graph still executes unchanged. Unless a change includes a reviewed migration:

- do not reuse or rename an existing public node class ID;
- do not reorder existing positional inputs or outputs;
- append new optional inputs and outputs instead of inserting them;
- retain readable legacy widget values and checkpoint formats;
- keep compatible H3-Multishot, SolAttn, and shared patch markers intact.

The [Version 0.5 architecture](docs/V0_5_ARCHITECTURE.md) and
`tests/fixtures/v0_4_public_contract.json` record the historical baseline.
Current contract and removed-node tests distinguish preserved behavior from
intentional 0.7 retirements; do not restore obsolete inputs just to match an
old fixture.

## Document behavior from evidence

Use the implementation, tests, maintained workflows, and upstream source as
the authority. A user-facing claim should point to a working example or a
specific implementation path. Mark experiments as experimental and describe
their fallback or compatibility behavior.

When a change is derived from another project, update
[Feature traceability](docs/FEATURE_TRACEABILITY.md) and
[Third-party notices](THIRD_PARTY_NOTICES.md) together. Record:

1. the upstream repository and author;
2. the revision or pull request used;
3. the upstream license;
4. the local files that implement the feature;
5. whether the relationship is **adapted**, **inspired**, **integrated**, or
   **compatibility-only**.

Do not describe an integration with an upstream API as copied code, and do not
describe an adaptation as an original implementation.

## Validate a change

Run the focused test for the area you changed. Before release or a broad pull
request, use the ComfyUI Python environment with Node.js and ffmpeg on PATH:

```bash
python tools/check_release.py --comfy-root /path/to/ComfyUI
python tools/build_v06_workflows.py --check
```

Masking, source-timeline, frontend, and migration changes have additional
targeted scripts under `tests/`. JavaScript tests use Node directly, for
example:

```bash
node tests/_plan_editor_js_test.mjs
```

The runner uses CPU fixtures and disables CUDA in its children. Tests use mock
ComfyUI modules where possible; the chain smoke test imports real ComfyUI.
Browser checks, GPU integration, and production acceptance remain separate:
see [release validation](docs/RELEASING_0_7.md). Passing CPU tests does not prove
render quality or clean-install compatibility.

## Keep the README approachable

The README is a workflow chooser and quick start. Put algorithms, complete
node settings, and recovery internals in the focused guides under `docs/`.
Prefer short tables, task-based headings, and links to deeper explanations.

When adding or retiring a maintained workflow, update
`example_workflows/README.md` and `tests/_workflow_catalog_unit_test.py` in the
same change. Keep prompt and media attribution beside the workflow that uses
it.
