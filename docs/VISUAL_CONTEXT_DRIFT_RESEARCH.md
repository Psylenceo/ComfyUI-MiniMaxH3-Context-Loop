# MiniMax H3 recursive visual-context drift research

> Historical research record. The nightly-only Visual Context Schedule, Joint
> Boundary Anchors, Guide-noise and future-anchor controls were removed in 0.7.
> This document is not a current node/setup guide.

Status: active experiment, August 2026. This note records the mechanism and
the tests needed before changing a production preset.

## Symptom and discriminators

The observed continuation trajectory becomes warmer/redder and increasingly
processed-looking. The change is already visible in an early latent preview,
while the same target scene without visual continuation begins with a neutral
trajectory. The result reproduced with short and normal Guide lengths and with
the alternate attention backend removed, making packed-row count, SolAttn, and
video duration poor primary explanations.

The strongest discriminator is `visual_cond_noise_aug=0.000`: at the second
sampling evaluation the red takeover disappears. This preserves the visual
condition rows, their positions, and the rest of the graph, but replaces their
content with seeded noise and removes their near-clean timestep pin. Therefore
the predecessor visual content/timestep, rather than the mere existence of the
packed rows, is causal.

`0.950` looking like `0.999` does not contradict that result. In ComfyUI's H3
implementation the value is a clean fraction, not a denoise strength:

```text
condition = aug * clean_latent + (1 - aug) * seeded_noise
condition_t = max(target_t, aug)
```

At `0.950`, the condition is still 95% clean and labelled at timestep 0.95
during the nearly-noisy first target evaluations.

## Current ComfyUI behavior

The relevant current core paths are:

- [`comfy/model_base.py`](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy/model_base.py)
  builds one MiniMax payload and copies `minimax_visual_cond_noise_aug` into a
  single `visual_cond_noise_aug` scalar.
- [`comfy/ldm/minimax/model.py`](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy/ldm/minimax/model.py)
  applies that scalar to every visual condition latent, then assigns the same
  condition timestep to every `cond` and `ref_img` packed segment.
- ComfyUI PR
  [#15439](https://github.com/Comfy-Org/ComfyUI/pull/15439) added native H3
  arbitrary-position Guide records, but did not add per-Guide strength.
- ComfyUI PR
  [#15375](https://github.com/Comfy-Org/ComfyUI/pull/15375) fixed target
  video/audio latent masks on the packed token grid. It is relevant to AV
  prefixes, but it does not change persistent Guide/reference conditioning.

The reviewed `_forward` source hash remains
`14bdfccd6860f252005b8d43ab446aa9a938a13dc819061724b8f914218f5fd1`
on ComfyUI master `9db05e0e1f035d1902ffc256fe7a336e549ced34` (August 23,
2026), matching the installed core contract used for this experiment.

The scalar behavior is internally consistent for ordinary authored visual
references. The problem is scope and recursion: predecessor Guide, character
stills, endpoint keyframes, and motion-video references cannot use independent
strengths or schedules, and core has no policy for feeding a generated output
back as a nearly clean condition at the next scene's noisiest model call. That
is a core capability gap rather than evidence that attention or layout is
mis-indexed.

This is also not a newly introduced ComfyUI regression. The
[first public H3 model commit](https://github.com/Comfy-Org/ComfyUI/commit/57500fc5bc92566a63f2046824f522cd55c335ca)
already used the `0.999` static clean/noise mixture and the same
condition-timestep pin. Later Guide and mask work expanded where conditions
could be placed and how target rows could be preserved; it did not create this
static-condition behavior. The
[RunningHub implementation](https://github.com/HM-RunningHub/ComfyUI_RH_MinMaxH3)
likewise builds a single static `0.999` visual anchor, restores it after solver
updates, and pins all image/video-condition rows to that floor. The failure is
exposed by a use case neither static path specially handles: feeding generated
output back as the next generation's visual condition over several scenes.

The same static convention is also present in
[Wan2GP](https://github.com/deepbeepmeep/Wan2GP/blob/main/models/minimax_h3/pipeline.py),
[DiffSynth Studio](https://github.com/modelscope/DiffSynth-Studio/blob/main/docs/en/Model_Details/MiniMax-H3.md),
and other independent H3 ports. This makes a ComfyUI-only regression unlikely.
It does not make the recursive result correct: these implementations inherited
an authored-reference convention, while recursive continuation repeatedly
feeds model output back through it.

Community observations independently match the symptom. In the H3 inpainting
thread, A.I.Warper reports that a reference-video result looks correct through
roughly the first half of diffusion and then
[slowly bleeds back to the source video](https://discord.com/channels/1076117621407223829/1539132102585356359/1540100466090709012).
Their practical workaround was to add image-space noise to the reference;
they subsequently reported a
[much higher hit rate](https://discord.com/channels/1076117621407223829/1539132102585356359/1540102557563887636).
That is not the same implementation as timestep-matched latent conditioning,
but it independently supports the same causal direction: an overly clean
visual condition can take over the late trajectory. It also explains why a
static noisy reference can help while sacrificing color and micro-expression
detail.

A later
[visual-condition augmentation discussion](https://discord.com/channels/1076117621407223829/1539132102585356359/1540476856187490436)
identifies `minimax_visual_cond_noise_aug` and the stock `0.999` value. The
reply correctly notes that stock references remain effectively pinned near
clean throughout sampling. The discussion tests static weakening, not the
per-sigma schedule proposed here, so it is corroborating evidence rather than
validation of this exact fix.

T8 independently exposes that same scalar in an
[experimental Visual Reference Strength node](https://github.com/T8mars/comfyui-minimax-h3-audio-T8/blob/main/nodes_visual_reference_exp.py).
Its warning is important for this investigation: the control is global, so it
weakens first/last-frame keyframes and Ref2VA media together with recursive
context. That independently confirms the current public ComfyUI API cannot
perform the selective A/B required here; it does not validate a dynamic
schedule by itself.

## Other implementation differences

- The released MiniMax VAE helper samples the visual posterior. Current
  [Diffusers H3 conditioning](https://github.com/huggingface/diffusers/blob/main/src/diffusers/modular_pipelines/minimax_h3/encoders.py)
  documents the complete released recipe explicitly: sample with the fixed
  `keyframe_encode_seed`, round the sampled latent through fp16, then normalize.
  RunningHub follows the same deterministic path. Current ComfyUI H3 VAE
  encode instead returns the posterior mean, and has done so since the first
  H3 integration; this is an upstream-parity difference, not a recent
  regression. It can change ordinary RGB Guide texture every time a recursive
  tail is decoded and re-encoded. It cannot be the complete cause because
  direct sampled-latent Guide and AV target-prefix paths bypass that encode.
- [RunningHub's direct runtime](https://github.com/HM-RunningHub/ComfyUI_RH_MinMaxH3/blob/main/minimax_h3_nodes/runtime/sampler_core.py),
  [DiffSynth's H3 pipeline](https://github.com/modelscope/DiffSynth-Studio/blob/main/diffsynth/pipelines/minimax_h3_audio_video.py),
  and [SGLang's H3 condition-noise stage](https://github.com/sgl-project/sglang/blob/main/python/sglang/multimodal_gen/runtime/pipelines_core/stages/model_specific_stages/minimax_h3/condition_noise.py)
  construct condition noise in latent shape before patchifying. For each
  condition they restart a CPU generator at the request seed, draw temporal
  length `target_latent_T + number_of_visual_conditions`, slice the
  condition's `T` prefix, and then patchify. SGLang calls this its
  **dependent-noise policy**. ComfyUI instead patchifies the clean latent first
  and draws an equal-shaped IID Gaussian directly in row shape. Both draws are
  normally distributed, but their seeded realization and spatial ordering
  differ. That difference is negligible at `0.999` and is not a persuasive
  cause of the original color drift. It becomes a meaningful secondary parity
  variable when late reveal makes the condition mostly noise. The first live
  test deliberately retains ComfyUI's exact row-space behavior because static
  `0.000` removed the red cast on that path. If late reveal fixes color but
  loses continuity, `dependent_latent` is the next isolated A/B—not something
  to combine into the first experiment.
- Current ComfyUI also uses one generator sequentially for target video and
  target audio noise. The reviewed SGLang and vLLM H3 pipelines independently
  restart the same request seed for video and audio. This gives ComfyUI a
  different initial target-audio noise realization and can affect exact
  runtime parity or joint A/V motion, but it is not the context-only color
  cause: the neutral no-context control uses the same ComfyUI audio draw.
- Clean recursive reconditioning is independently reported to accumulate
  texture/palette error. The current MiniMax H3 Multishot workflow documents a
  residual texture increase per join and retracts small extra `pin_noise` as a
  general fix:
  [MiniMax-H3-Multishot-Workflow](https://huggingface.co/joeygambino/MiniMax-H3-Multishot-Workflow/blob/main/README.md).
- Static context corruption can sometimes soften this feedback, as explored by
  [ComfyUI-H3-Context-Noise](https://github.com/beijinren/ComfyUI-H3-Context-Noise),
  but changing pixels/latents without changing the model's condition timestep
  creates a content/label mismatch.
- No released MiniMax training report was found that documents a distribution
  of visual-condition timesteps. The public inference ports consistently call
  `0.999` the reference/default anchor. DiffSynth additionally warns that the
  released model is CFG-distilled and recommends retaining that value for
  ordinary references. Dynamic recursive scheduling is therefore a targeted
  inference experiment, not a claim about the training distribution. This is
  why authored keyframes and Ref2VA media stay at `0.999`, and why a full
  continuity evaluation must follow the successful step-2 color discriminator.

## Saved-latent and output audit

The chain is not accidentally carrying the sampler's last preview estimate.
`Chain Segment Save` stores the terminal sampled AV latent, and `Loop End`
carries that same terminal result into the next iteration. The optional
`denoised_latent` input is checkpointed separately for deferred upscaling and
is not used as recursive continuation state. With a shifted schedule the last
model evaluation is still at nonzero sigma and the solver then advances to
zero, so substituting that evaluation's predicted clean latent would not be a
principled correction.

The active four-scene checkpoints also show no exploding latent distribution:
video-latent standard deviation stayed near `1.04` and mean moved only from
about `0.029` to `0.058-0.066`. Whole-frame decoded YUV averages did not become
monotonically red either; later clips were slightly cooler by that coarse
metric. The visible problem is therefore an early, spatially local sampling
trajectory and recursive texture/palette feedback—not a simple final-assembly
color transform, corrupt saved latent, or progressively increasing global
latent magnitude. These aggregate checks do not invalidate the user's local
skin/background observation; they narrow where the mechanism operates.

## Scheduled late reveal

For flow sampling, a clean latent at target timestep `t = 1 - sigma` has the
forward state:

```text
x_t = t * clean + (1 - t) * noise
```

H3's existing visual-condition formula is exactly that formula when
`visual_cond_noise_aug=t`. The **Guide Late Reveal** research node therefore
sets, at every diffusion call:

```text
aug = clamp(max(1 - sigma, floor), ceiling)
```

The exact matched preset uses floor `0.000` and ceiling `0.999`. At the
observed second evaluation (`sigma ~= 0.996`) the condition is only about
0.004 clean, closely matching the successful static-zero discriminator. As
sampling proceeds it reveals the same predecessor along one stable seeded
noise trajectory. The MODEL clone shares its underlying weights, no latent or
second DiT pass is added, and stock `0.999` already generated condition noise
on every call. Selective mode normally adds only one distinct timestep row to
the small modulation table, so the expected runtime and VRAM difference is
negligible. Absolute sigma keeps the rule continuous through a split-sigma
sampler.

The archived production graph uses H3 shift `12`, 20 `simple` steps, and a
16/4 split. On that exact schedule, current-step matching progresses from
`0.000` at evaluation 1 through `0.004` at evaluation 2, but reaches only
`0.613` at evaluation 20 because the final model call is at `sigma ~= 0.387`
before the solver jumps to zero. This is nevertheless the recommended first
A/B because it is the only preset that exactly matches the target diffusion
time at every model evaluation, and it most directly tests the successful
static-zero observation.

The experimental `next_step` preset instead computes the mixture from the
next lower endpoint of the original unsplit schedule. It starts at `0.004`, is
about `0.009` at evaluation 2, and reaches `0.999` at evaluation 20. It is the
fallback if `matched` removes the red trajectory but leaves final continuity
too weak. Content mixture and condition timestep remain matched to each other
inside both presets, but only `matched` is also aligned exactly with the target
timestep.

The `manual` preset is for discontinuous causal probes rather than a proposed
production trajectory. It accepts exact clean fractions indexed by the
original unsplit sigma schedule and holds the last supplied value for all
remaining steps. For example, `0, 0.999` makes the recursive Guide pure seeded
noise on scheduler step 1, then restores the stock near-clean mixture and
timestep pin on step 2 and every later step. `full_sigmas` is mandatory, so
the mapping remains absolute across split model branches and does not drift
when a solver performs extra evaluations inside one step.

The optional Chain Context `future_end_anchor` probe tests a more selective
way to retain composition without restoring the complete Guide prefix to
`0.999`. It reuses only the predecessor context's final prepared latent step
as one stock-clean visual condition at the first temporal position after the
target. In Guide modes, the normal recursive prefix is unchanged and remains
the only visual condition controlled by Guide Late Reveal, so it may stay
`matched` or use a weak/manual schedule. In AV modes, the preserved prefix
remains entirely inside the masked target latent; the suffix is copied after
AV preparation from that exact prefix, including spatial-proxy or latent-colour
treatment, then added through the Guide system without changing the AV mask.
Condition rows are not decoded target frames; this future suffix therefore
does not change raw or delivered scene length and requires no extra trim. It is
not colour-only, so it may preserve background/camera geometry while also
pulling the ending pose toward the predecessor. Keep Chain Context's base
`visual_cond_noise_aug=0.999` and Late Reveal's selective
`scope=chain_context_only`; `all_visual_conditions` or a weakened base value
would schedule/weaken the suffix too.

To retain the suffix only during early composition, use the renamed **Visual
Context Schedule (Research)** node with `preset=manual`,
`scope=future_anchor_only`, and connect the original unsplit `full_sigmas`.
`manual_schedule=0.999, 0.999, 0` keeps the suffix stock-clean for sampler
steps one and two, then replaces its content with the same stable seeded noise
and target-matched timestep on step three and every later step. A gentler
handoff can use `0.999, 0.999, 0.5, 0`. The conditioning rows are deliberately
not physically removed: keeping the packed layout fixed avoids changing row
positions, attention layout, or split-sampler caches in the middle of a run.
This scope targets only `h3_chain_future_end_anchor`; the recursive Guide
prefix, authored keyframes, Ref2VA media, and an AV preserved prefix are
unchanged.

This differs from simply inserting Guide at the end. The model sees low-SNR
structure while establishing the new scene, then receives increasingly precise
identity/pose/seam evidence while details form. A post-sampling reinsertion can
hide one boundary but cannot prevent palette feedback during generation.

## Joint boundary-anchor prepass

The optional **Joint Boundary Anchor Prepass** tests a different hypothesis:
future composition is more stable when every planned endpoint is established
inside one H3 sample instead of being invented independently by four scene
runs. It derives each endpoint from the Plan's cumulative delivered-frame
timeline and lazily packs a short synchronized source reel:

```text
5 establishment frames + 17 endpoint frames per scene
4 scenes = 73 RGB frames = 22 H3 video-latent steps
endpoint latent steps = 6, 11, 16, 21
```

The prepass does not discard motion or sound. It replaces the ordinary
scene-by-scene motion-reference clock with exact 17-frame endpoint windows and
packs the matching audio samples on the same 24 fps reel. Identity,
environment, lighting, lens, and color references remain available through
their existing prompt tags. The reel is sampled once; **Extract Joint Boundary
Anchors** then slices one video-latent step per endpoint directly from the
sampled latent. There is no VAE decode/re-encode. Generated transition frames
and generated prepass audio are discarded.

Connecting the resulting registry to **Chain Context** adds the current
scene's precomputed endpoint as a native Guide immediately after that scene's
target timeline. It works on scene 1 and continuation scenes and takes priority
over the legacy copied-prefix `future_end_anchor` toggle. The ordinary scene
conditioning, source motion/audio windows, transition mode, target mask,
length, and Loop Trim remain unchanged. A changed Plan timing contract,
Source Timeline, resolution, or latent geometry is rejected instead of using
a stale anchor.

This remains an explicit research branch. It spends one additional short H3
sample up front and can overconstrain endpoint pose or camera if the prepass
prompt is too literal. Its purpose is to determine whether jointly generated
future composition prevents the progressive color/background feedback that a
copied predecessor suffix cannot solve.

## Minimal live test

1. Use the same predecessor checkpoint, target prompt, seed, scheduler, steps,
   model, references, and Guide context as the known red run.
2. Restore Chain Context `visual_cond_noise_aug` to `0.999`.
3. Route the H3 model through **MiniMax H3 Visual Context Schedule (Research)** with
   Current Shot state, preset `matched`, scope `chain_context_only`, and noise
   backend `comfy_rows`, then route its MODEL output into every sampler stage.
   `matched` does not need `full_sigmas`. With switched models, use one patched
   node per model branch.
4. Disable Reference Video Fade for the first A/B so it does not add a second
   scheduled variable.
5. Compare the same early preview frame after evaluation 2 or 3. Abort there
   if the red trajectory remains; a full render is unnecessary. Confirm the
   server log reports the first three `target sigma`, `basis sigma`, and
   `context clean fraction/timestep` values. On the archived 20-step schedule,
   `matched` should begin approximately `0.000`, `0.004`, `0.009`.
6. If color is neutral but final motion/identity continuity is too weak, try
   `next_step` with the original unsplit scheduler output connected to
   `full_sigmas`. If that is still weak, repeat `matched` with noise backend
   `dependent_latent` to reproduce public runtimes' dependent latent-space
   noise draw for Chain Context only. Only then try custom floors `0.050` and
   `0.100`. Do not jump to `0.950`: that recreates a nearly clean early
   condition.
7. Only if the early trajectory and final continuity both pass, run scenes 2,
   3, and 4 to measure recursive color and detail drift.
8. As a separate VAE-path discriminator, repeat the passing `matched` setup
   once with ordinary `guide` and once with `latent_guide`, using the same
   saved predecessor latent. If only RGB `guide` degrades, the extra
   decode/resize/Comfy-mean re-encode cycle is materially contributing. If
   both behave alike, posterior-sampling parity is not required for this fix.

## Compatibility validation completed

- The selective forward is source-hash gated to the reviewed ComfyUI H3 core;
  a changed or competing `_forward` fails before sampling.
- With the selective schedule fixed to the stock `0.999`, a tiny packed
  text/context/keyframe/reference/audio/video sequence was run through both the
  exact current ComfyUI `_forward` source and the compatibility forward. Both
  output streams and every modulation segment were bit-identical.
- With a dynamic value at `sigma=0.996`, the recursive context block received
  `0.004`, while an authored keyframe and Ref2VA image stayed at `0.999`.
- Native multi-frame Guide placement was checked against the merged
  [ComfyUI Add Guide implementation](https://github.com/Comfy-Org/ComfyUI/pull/15439).
  A 5/22/39-frame Guide at frame zero remains one multi-frame condition block;
  its temporal/spatial RoPE positions intentionally match the first target
  positions. Chain Context emits that exact native form. There is no reviewed
  off-by-one or accidental split in this path. The duplicate positions instead
  make the clean-versus-noisy state mismatch especially relevant: fixed
  context and noisy target rows coexist at the same coordinates.
- The default `comfy_rows` backend is bit-identical to current ComfyUI row
  construction. The optional `dependent_latent` backend reproduces the
  reviewed dependent-noise tensor shape, seed restart, temporal slice, and
  patch order only for marked Chain Context rows; ordinary keyframes and
  references retain ComfyUI's exact row-noise path.
- Focused native-Guide, payload-owner, transition-policy, masked-prefix,
  Drift-Control, workflow-catalog, and node-import tests pass. This establishes
  software compatibility; it does not replace the live visual A/B above.

## AV interpretation

An AV prefix is not a `cond` segment. It occupies target-video rows and uses a
denoise mask, so the Visual Context Schedule node cannot schedule that prefix.
Its `future_anchor_only` scope can nevertheless schedule the separate suffix
Guide now optionally attached to AV. A hard AV prefix is near-clean at early
sampling and can produce the same feedback class. Drift-Control AV is the
corresponding target-row experiment: it presents most carried rows at the next
scheduler level and tapers the seam end to clean while keeping H3's per-row
timestep label synchronized.

## Long-term core fix

The clean upstream design is a per-visual-condition augmentation list aligned
with `cond_video_latents` and the corresponding `cond`/`ref_img` layout
segments. Core would:

1. accept a backward-compatible `minimax_visual_cond_noise_augs` list;
2. noise each condition latent with its own value;
3. assign each packed visual segment its matching timestep;
4. retain the scalar as the default when the list is absent.

The minimal reviewed prototype changes only `comfy/model_base.py` and
`comfy/ldm/minimax/model.py`:

```text
external conditioning: minimax_visual_cond_noise_augs
H3 payload:            visual_cond_noise_augs
ordering:               cond_video_latents = keyframe visuals, then refs
```

The model validates a one-to-one list/latent count, uses value `i` when
forward-noising latent `i`, and consumes the values in that same order while
building the `cond`/`ref_img` modulation segments. Both halves are required:
changing only the mixture but not its timestep label is mathematically
inconsistent. If the list is absent, it expands the existing scalar across all
visual latents, preserving current workflows exactly. A small capability
marker, `PER_CONDITION_VISUAL_COND_NOISE_AUGS`, lets extensions select the
public path without guessing from a ComfyUI version number.

Chain Context now marks only its own predecessor Guide rows. The local research
node supplies the proposed behavior through a source-hash-gated compatibility
forward on current core, leaving character/keyframe/motion references at their
existing strength; an unknown core forward is rejected. It automatically uses
a normal diffusion-model wrapper instead when the complete per-condition core
contract is detected. This is suitable for validating the model behavior but
is not a substitute for the smaller upstream API.
T8's independently implemented per-keyframe augmentation demonstrates the same
segment-level model math and informed the compatibility structure:
[T8 multikeyframe implementation](https://github.com/T8mars/comfyui-minimax-h3-audio-T8/blob/main/multikeyframe_advanced.py).

Until the live `matched` test passes, neither the research node nor a
proposed core API should become the default continuation policy.

A second, independent upstream-parity proposal could reseed target video and
audio noise separately, as the SGLang and vLLM pipelines do. It must remain a
separate experiment: combining it with the first visual-context schedule A/B
would prevent attributing the result, and the existing no-context control says
it is not necessary to explain the red trajectory.

No equivalent open ComfyUI issue or pull request was found in the August 23,
2026 upstream search. The current Guide documentation describes guides as
re-injected anchors but exposes no independent guide-noise schedule. Existing
H3 reports about black/noisy output and 17-frame stutter concern different
failure modes and do not invalidate the clean-vs-noised recursive-context
discriminator used here.
