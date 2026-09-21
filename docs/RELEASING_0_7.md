# 0.7 release validation

This is a release-candidate checklist, **not a publication announcement**.
The September 21, 2026 hardening pass was performed on nightly. Do not infer
main-branch parity or Registry publication from a passing local check.

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

The September 21 pass covers **229 CPU regression scripts**, including:

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
the September 21 run. These are isolated browser checks,
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

## Remaining gates before publishing

- [ ] Clean-install smoke run using the intended release's ComfyUI and
  companion-pack versions; open a maintained Normal workflow and verify the
  loaders, one sampled clip, review and export. The current environment's
  passing import/schema tests are not a fresh-environment installation test.
- [ ] Representative pretrained-weight, production-resolution visual/audio
  acceptance run on the final release candidate, including stop/resume and a
  multi-scene assembly. The synthetic GPU test is not a substitute.
- [ ] Native Windows smoke test. Path, cross-drive, descriptor and locking
  regressions are covered on Linux with modeled Windows semantics; this is
  not a full Windows installation/render validation.
- [ ] Review the exact main/nightly diff and choose what ships; preserve
  explicit experimental labels rather than silently changing defaults.
- [ ] Explicit approval for merge/tag/publish. Updating `pyproject.toml` on
  main can trigger `.github/workflows/publish_action.yml`; do not use a
  version-file push as a harmless release rehearsal.

Companion feature requests are not release blockers merely because they are
open. Recheck GitHub reports against the candidate before publishing; do not
close issues or merge draft PRs based only on this checklist.
