# Migrating to 0.7

The original **Context Loop Plan remains supported**, with its existing inputs,
audio/continuation controls and optional policy connection. Modern Plan and
standalone Plan Studio remain available too.

## Retired authoring nodes

| Removed | Supported route |
| --- | --- |
| Manual Chain Policy (Legacy) | Generation Profile; original Plan controls/per-scene JSON for custom intent |
| Legacy 0.4 Policy Adapter | Original Plan controls or current Generation Profile/Advanced Policy |
| Scheduled Picture / Video / Audio Ref | Tagged references or Project Asset Carousel |
| Scheduled Ref2VA | Tagged Ref2VA |
| Lazy Motion AV Loader | Source Timeline; Tagged Motion Ref (Source Timeline) |
| Upscale Merger (Legacy) | Context Loop Assemble |

Numeric reference schedules are not equivalent to prompt activation. Translate
the intended scene membership into registered tags used by those scene prompts.
Do not simply rename old node types in a saved workflow.

The pre-0.6 example archive and the old 0.5 workflow migration tool were removed.
They remain available in Git history. The current 0.6-named examples are the
maintained catalog, rebuilt against this checkout's schemas.

## Retired controls

- `reference_schedule` on Plan Studio, Preflight and Loop Start. Connect
  `tagged_references` instead.
- Loop Trim's manual `retain_overlap_frames`. Connect Current Shot state for
  per-scene overlap; without state, Trim delivers fully trimmed frames.
  Output positions are unchanged.
- Masked Target's `legacy trilinear` choice. H3-exact causal/token-max masking
  remains. Existing latent-mask resizing remains supported internally.
- Prompt editing inside Review Gate. Use Scene Prompt Editor or Rich Scene
  Prompt Editor, then Retry/Reroll. The gate still uses the current Plan prompt.
- The discarded Carousel/Tree `tagged_scene_options` API argument.
- Nightly only: Chapter Delivery's already-hidden, ignored `chapter_number`
  argument. The chapter-number output and Chapter Recovery Load remain.
  Main's functional explicit chapter selector is unchanged.

Old API prompts containing removed arguments must be updated. Removed
behavior is not silently substituted during generation; start a new revision
when changing conditioning/masking settings.

## Retired nightly-only degradation experiments

Visual Context Schedule / Guide Late Reveal, Joint Boundary Anchor Prepass,
Extract Joint Boundary Anchors, and Chain Context's `visual_cond_noise_aug`,
`future_end_anchor` and `boundary_anchors` inputs were removed. Their historical
research notes are not instructions for current workflows.

Experiments already available on main remain: Tone/Latent/Detail Guide,
Detail/Drift/Color Drift AV, spatial proxy, Reference Video Fade, and explicit
assembly tone/color options. SelfLift, alternate lift backends, seed hunting,
upscale previews, deferred upscale, De-Rope and pixel continuity also remain.

## Preserved recovery behavior

No user runs, checkpoint files, working branches, sealed chapters, or recovery
snapshots are deleted by this source cleanup. Historical checkpoint/plan readers
and the active Source Audio import/recovery fallbacks remain. Restoring an old
graph containing retired nodes still requires updating that graph before
execution; preserving saved media is not a promise that every old node executes.
