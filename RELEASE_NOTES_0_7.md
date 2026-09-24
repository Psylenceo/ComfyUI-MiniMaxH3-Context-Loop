# 0.7.0 — September 24, 2026

**0.7 is available on `main`, with the same content as nightly and 0.7-rc at release.**
Back up your workflows and project folders before updating.
Models are not included. Follow [Getting started](docs/GETTING_STARTED.md) for
installation and required companion packs.

## Main changes

| Area | 0.7 highlights |
|---|---|
| Project and scene authoring | Basic or Carousel / Studio starting points; synchronized project names; collapsible scene/chapter controls. The original Context Loop Plan stays supported. |
| Takes and working branches | Batch deletion previews, explicit final-cut and continuation selections, and empty-branch management with shared-file/dependency safeguards. |
| Review and recovery | Saved candidate reviews, remote Review Gate Relay, resumable processing and optional top-level requeue between accepted scenes. |
| SelfLift — experimental | Seed hunting, lift previews and several marked upscale takes with one main; optional high-resolution denoising tiling, off by default. |
| Audio and delivery | Source Timeline / Carousel routing, grouped soundtrack stems, per-scene lip-sync controls and reusable saved processing manifests. |
| Storage and safety | Clear generation/processing/export folders for new projects; legacy layouts remain readable. Workflow ownership locking can be disabled without disabling transaction locks or deletion safeguards. |
| Compatibility and examples | Frame-duration parity, reference-preflight wiring corrections, current node schemas and separate manual Tagged examples. |

The maintained files keep their **0.6** suffix and stable workflow identities;
they are rebuilt for this checkout. Start with
[Basic or Carousel / Studio](example_workflows/README.md#start-here-basic-or-carousel).
Detailed history is in the [changelog](CHANGELOG.md). For sharing, use the
[improvements table and release card](docs/0.7-major-improvements.md).

## Before updating

- Read [Migrating to 0.7](docs/MIGRATING_TO_0_7.md). Obsolete authoring nodes,
  scheduled-reference controls, 0.4 raw source-audio sockets and discarded
  degradation experiments were removed. Keeping old checkpoints readable does
  not make every old workflow executable unchanged.
- Edit prompts in Scene Prompt Editor or Rich Scene Prompt Editor; the Gate's
  old embedded prompt editor is removed.
- New projects use [the organized storage layout](docs/SIMPLE_CHAIN_LAYOUT.md).
  Existing runs are not automatically converted. Keep their hidden `.h3/`
  state when backing up new-layout projects; it is not a cache.
- Restart ComfyUI after updating and reload the frontend. For user-created
  Tagged graphs, check registry wiring to both conditioning and each preflight
  path, including Loop Start; examples do not rewire your saved graphs.

## Known limits and testing status

- SelfLift's localized learned-upscale artifacts are **not resolved**. Alternate
  lifters, previews, tiling and other experimental processing routes are not
  quality guarantees. See [SelfLift limits](docs/selflift-seed-hunt.md#known-quality-limitation).
- Examples default to recursive execution. [Top-level requeue](docs/MAINTAINED_WORKFLOW.md)
  is opt-in and separates accepted scenes, not every candidate or retry.
- Release acceptance includes the maintainer's sustained use of nightly.
  Automated fixture checks are separate from fresh pretrained-weight,
  clean-install and native Windows testing, which were not rerun for this cut.
  See the [validation record](docs/RELEASING_0_7.md).
- Reports being triaged include [reference/preflight behavior (#95)](https://github.com/ethanfel/ComfyUI-MiniMaxH3-Context-Loop/issues/95),
  [color shift (#96)](https://github.com/ethanfel/ComfyUI-MiniMaxH3-Context-Loop/issues/96),
  and [AudioRefine joins (#97)](https://github.com/ethanfel/ComfyUI-MiniMaxH3-Context-Loop/issues/97).
  The example wiring correction addresses one concrete cause of #95's warning;
  it does not establish that the entire report is fixed. The remaining reports
  are tracked separately and were explicitly deferred from this release's scope.

For feedback, attach the workflow JSON, ComfyUI/pack versions, steps to reproduce
and the full error or a small comparison. Remove credentials and private asset
paths before sharing.
