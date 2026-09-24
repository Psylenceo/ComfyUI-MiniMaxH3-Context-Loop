# 0.7 release validation

Release validation and acceptance record for **0.7.0**, approved September 24,
2026. The maintainer accepted the release based on sustained real-world use of
nightly, with identical nightly/RC content promoted to main. A passing local
check alone does not establish Registry publication; verify the publishing job.

## Scope and compatibility

- Keep the original Context Loop Plan and its existing sockets. Production
  Plan and Plan Studio are alternatives, not mandatory replacements.
- Use [0.7 migration notes](MIGRATING_TO_0_7.md) for removed nodes/controls.
  The maintained **0.6-named** examples identify the catalog baseline; their
  filenames do not mean they need the retired archive.
- Workflow ownership locking remains on by default, with a server-wide opt-out
  in ComfyUI settings. Test both policies; transaction locks, dependency checks
  and deletion protections must survive either setting.
- Exact authored frame counts are preserved. Seconds-based browser timing now
  matches execution: for example, 1/6/12 seconds round to 39/158/294 raw frames
  on H3's grid. Carried overlap can reduce delivered frames separately.
- SelfLift, alternate lifters and high-resolution denoising tiling remain
  experimental. [Known learned-lift artifacts](selflift-seed-hunt.md#known-quality-limitation)
  are **not fixed** by this release. Tiling is off by default and is not TST.

## Automated validation

Run from the repository root using the **ComfyUI Python environment**, with
Node.js and ffmpeg on PATH. These checks use disposable fixtures; they do not
delete or migrate real productions, submit server jobs or load model weights.

```bash
python tools/check_release.py --comfy-root /path/to/ComfyUI
python tools/build_v06_workflows.py --check
```

The runner limits CPU threads, disables CUDA for its children and enables
Node's VM module support. Each script has a 120-second timeout; adjust
`--timeout` or `--jobs` for slower machines. Any failure returns a nonzero exit
status. Browser and GPU checks are deliberately separate.

### September 24 stable-release cut

After finalizing stable installation instructions, release notes, branding and
the shareable improvements card, the release runner again passed **236 CPU
regression scripts with zero failures**. All **25 maintained workflows** passed
the deterministic rebuild check; local paths and anchors passed in **74 Markdown
documents**. These release-finalization changes affect documentation and assets,
not sampling code or workflow defaults.

### September 24 RC documentation and wiring pass

On `0.7-rc`, the following checks passed in the ComfyUI Python 3.13 environment:

- **236 CPU regression scripts; zero failures**, using the release runner above
  with `--jobs 4`, including the real-ComfyUI chain smoke test.
- Deterministic rebuild/check of all **25 maintained workflows**.
- Local file and heading links in **73 shipped Markdown documents**.
- Tagged conditioning and every preflight path share the same registry in all
  six applicable workflows; the test also rejects a disconnected Loop Start.
- Comparison against the pre-cleanup catalog preserves all workflow UUIDs,
  non-note widget values, node layouts and modes. Only four missing Loop Start
  registry edges were added; generated link numbers are regenerated normally.

Browser, GPU, native Windows and fresh-install tests were **not rerun** in
this pass. The results below remain dated historical evidence, not validation
of every production workflow on the final candidate.

### September 21 hardening baseline

The September 21 pass covered **229 CPU regression scripts**, including:

- Save, interruption, resume, checkpoint integrity and final assembly. The
  chain smoke test imports real ComfyUI modules and encodes/assembles tiny
  H.264 clips with source and generated audio; it does not sample a model.
- Candidate review, multi-take SelfLift recovery, saved low/high handoffs,
  upscale previews, processing variants and partial exports.
- Working branches, final-cut selection, bulk and obsolete-path deletion,
  sealed recovery pins and shared-file protection.
- Ownership enabled/disabled, stale-proof rejection, requeue fencing and
  run-switch isolation.
- Legacy checkpoints, reference-cache migration, source-audio recovery,
  original Plan preservation and removed-node contracts.
- Python/browser parity across **7,829 durations**, six compiled Plan cases
  and unchanged exact-frame authoring.
- All **25 maintained workflows**, including `tagged/`: schemas, widget order, links, layout,
  safe defaults and deterministic recipe/guide output.

### Browser checks

Use an installed Chrome/Chromium binary. Every test starts its own temporary
profile and synthetic UI/media; none uses a personal browser profile or live
ComfyUI project. For example:

```bash
export H3_TEST_BROWSER=/path/to/chrome
export CHROME_BINARY="$H3_TEST_BROWSER"
export CHROME_PATH="$H3_TEST_BROWSER"
node tests/_checkpoint_manager_browser_js_test.mjs --browser
node tests/_prompt_editor_browser_js_test.mjs --browser
node tests/_project_asset_layout_browser_test.mjs
node tests/_project_asset_run_sync_browser_test.mjs
node tests/_studio_prompt_refresh_browser_test.mjs
node tests/_studio_chapters_browser_test.mjs
node tests/_plan_collapse_browser_test.mjs
node tests/_selflift_hunt_browser_test.mjs
node tests/_review_relay_browser_test.mjs
node tests/_context_mask_browser_test.mjs
node tests/_branch_recovery_storage_browser_test.mjs
node tests/_dom_wheel_browser_test.mjs
```

Coverage includes checkpoint controls/selection, both prompt editors, Plan
collapse, asset layout, non-refreshing prompt edits, chapter navigation,
SelfLift selection, relay approval/seek, context-mask editing, wheel routing
and browser-storage recovery/conflicts. All eleven browser scripts passed in
the September 21 run; the command list above now includes an additional
project-run synchronization regression. These are isolated browser checks,
not an exhaustive end-to-end run of every workflow in ComfyUI's canvas.

### GPU integration

With the GPU idle, run this separately in the ComfyUI Python environment:

```bash
python tests/selflift_tiling_comfy_smoke.py /path/to/ComfyUI cuda /path/to/RES4LYF
```

The September 21 RTX 5090 check passed with real ComfyUI and RES4LYF runtime
code, using a tiny randomly initialized H3 network: Euler and Radau IA 2s,
packed layouts, keyframes/references, width/height tiles, painted/continuation
masks, locked audio, high-pass-only wrappers and bit-identical disabled path.
It uses no pretrained weights and establishes **runtime correctness, not
image quality or production-resolution memory requirements**.

## Release branch parity

Prepare release fixes, documentation and final version/branding changes on
`nightly`, then fast-forward `0.7-rc` to the same tested commit. At publication,
the two branches must match exactly: code, examples, documentation, defaults
and package metadata. Do not make RC-only release edits or selectively omit
nightly changes without revisiting the agreed release scope.

Before publication, fetch the remote refs and verify a clean working tree,
identical `origin/nightly` and `origin/0.7-rc` commit IDs, and an empty diff:

```bash
git status --short
git rev-parse origin/nightly origin/0.7-rc
git diff --exit-code origin/nightly origin/0.7-rc
```

If the branches diverge, reconcile and revalidate them before publishing;
do not silently overwrite either history. Freeze the agreed commit through
publication so a later nightly change cannot slip into the release untested.

## Stable-release decision and publication checks

On September 24 the maintainer explicitly approved replacing main with the
complete tested 0.7/nightly content and releasing **0.7.0** (main was 0.6.11).
The RC already carried version 0.7.0; promoting it supplies the stable version
bump. Release documentation, installation instructions and branding are
finalized on nightly, with experimental labels and defaults retained.

The branches have divergent history. Preserve both histories with a merge
that keeps the complete 0.7 tree, then fast-forward main and 0.7-rc to that
commit. Verify the merge adds no content change, all three branches have the
same tree and commit, and push them together. No force-push is needed.

Publishing checks remain operational requirements, not assumed test results:

- Rerun the CPU release suite and deterministic workflow check on the final tree.
- Verify clean worktrees and identical pushed main/nightly/0.7-rc commits.
- Confirm the main version change triggers `.github/workflows/publish_action.yml`
  and check its result and the Registry version. Do not dispatch duplicate jobs.

### Validation limits accepted for this cut

- Sustained maintainer use of nightly is the real-world acceptance evidence.
  No separate, freshly instrumented pretrained-weight production-resolution
  acceptance run was performed for the final documentation-only changes.
- Clean-install and native Windows smoke tests were not performed for this
  cut. Linux path/locking tests with modeled Windows semantics are not a full
  Windows installation/render validation.
- The earlier isolated browser and synthetic GPU results remain dated evidence;
  they are not claims of exhaustive production quality validation.

These limits were accepted for publication, not marked as passing tests.
Open companion requests and the reports below are outside this release's scope;
do not close issues or merge draft PRs based only on this release decision.

## Open compatibility reports reviewed for the candidate

As of September 24, 2026:

- [#95 — reference/preflight behavior](https://github.com/ethanfel/ComfyUI-MiniMaxH3-Context-Loop/issues/95):
  four maintained Tagged/Studio recipes omitted Loop Start's registry input
  even though conditioning and Studio/Preflight were connected. The RC now
  supplies the same registry to all validators, with a disconnected-input
  regression. Existing user graphs need the missing wire added manually.
  The broader duplication and audio-policy symptoms remain unconfirmed
  without the reporter's workflow; do not close the entire report on this basis.
- [#96 — color shift](https://github.com/ethanfel/ComfyUI-MiniMaxH3-Context-Loop/issues/96):
  remains a reported quality issue; this documentation/wiring pass does not
  establish its cause or resolve it.
- [#97 — AudioRefine joins](https://github.com/ethanfel/ComfyUI-MiniMaxH3-Context-Loop/issues/97):
  diagnostic workflow requested. Keep the possible refiner/assembly interaction
  separate from ordinary generated-audio regression coverage; no fix is claimed.

The maintainer explicitly deferred the remaining scope of these reports from
0.7 publication. They remain open for diagnosis; the release claims no fixes
beyond the specific reference wiring correction described above.
