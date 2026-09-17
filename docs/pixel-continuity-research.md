# Pixel USDU continuity — nightly experiment

## Use

Open **Deferred Upscale - Pixel USDU Continuity - EXPERIMENTAL - MiniMax H3 0.6**.
In that workflow's Checkpoint Manager, choose the saved output branch, inspect an
Original scene, and tick **Continue previous shot (pixel upscale)** for each
incoming boundary that is a true continuation. Leave hard cuts off.

Marks are saved in that manager's workflow selection, not in project files.
Both scenes must belong to the selected output scope. A mark is discarded if
either selected take changes; picture-only ALT footage does not inherit it.
Use a new upscale profile initially. To resume, include the preceding source
scene in the selection and retain its saved HQ result in the same profile.

The two helpers bracket USDU:

1. **Protect Tail** replaces the repeated RAW head with the previous saved HQ
   tail and supplies USDU's temporal mask and `anchor_context` input.
2. **Finish** restores that head exactly, then optionally fades a small per-channel
   tone correction into the new footage. Set `tone_strength=0` to disable it.

Save and Loop End receive the same final RAW video. Existing save logic removes
the repeated head once and uses the original checkpoint audio. Editorial trims
remain an assembly operation. No source generation needs to be repeated.

Unmarked scenes return the existing video unchanged: no mask allocation, media
decode or tail processing in Prepare. Existing workflows are unchanged.

## Optional boundary-refinement switch

In that same example, **Pixel Continuity • Boundary Experiment → enabled** is
**OFF by default**. OFF performs no capture/export work and preserves the
existing recipe unchanged. ON is an experiment, not a replacement for tail
protection. Enable it **before processing a new upscale profile**.

The workflow keeps only small pre-USDU/DLSS fragments in that child profile's
`boundary_sources/` directory. Once normal assembly finishes, **Experimental
Boundary Export** jointly refines a short window across each marked join:

- 22 fixed HQ frames before the editable center;
- 17 pre-USDU frames from each side of the join (34 editable frames);
- 17 fixed HQ frames after the center.

Only those middle 34 frames are pasted into a separate `_boundary_*.mkv`.
Use the Boundary Export node's `video_path`, not Assemble's baseline path.
All other decoded RGB16 frames stay exact, and the assembled audio is
stream-copied. Neither the original assembly nor any HQ checkpoint is rewritten.
There is no crossfade, interpolation or frame-count change.

Controls: `frames_per_side` = 17/34/51 (H3 grid), `denoise` = 0.20, `steps` = 3.
Fixed sampler settings are er_sde/beta, 512×288 tiles, padding 64, disk canvas.
Choose a fast local SSD for `canvas_directory`; blank uses ComfyUI temp.
Only the default 17-frame configuration has GPU validation. Larger windows
increase sampling memory. The final lossless MKV can be much larger than MP4.

Changing the switch or its refinement settings changes the Adapter recipe.
Use a new profile or reprocess rather than resume incompatible saved results.
An old completed profile has no pre-USDU fragments; the affected clips need
pixel upscaling again with the switch ON, not source generation again.
External model/DLSS/USDU changes still require a new profile or recipe update.

Hard cuts and unmarked boundaries are untouched. Editorial gaps, nonadjacent
reordering, trims removing the generated join, insufficient context, and
overlapping windows are skipped and counted in the status. Remaining joins
use their final editorial positions, including chapter-local offsets. The
baseline is decoded forward once for all windows, not restarted per join.

The pass rebuilds the incoming scene's prompt and available references with
the workflow's shared model/LoRA input. Whole-scene keyframes are excluded from
this shorter window; it does **not** replay per-scene LoRA routes. Keep normal
assembly blending/tone/stabilization off when comparing this experiment.

### Boundary experiment results

On the copied dog continuation, refining an already-HQ center at denoise
0.10/0.20 did not improve the join metric. Using pre-USDU frames in the center
at 0.20 reduced its mean absolute RGB difference from 0.035590 to 0.033435
(about 6%). However, the two replacement-edge differences increased about
6% and 7.5%. These motion-sensitive numbers are **not** a quality score;
inspect all three boundaries, not only the original join.

The actual switch/capture/export nodes were also exercised on local ComfyUI:
OFF completed without evaluating the model loaders; ON completed a 73-frame
GPU pass using the copied DLSS clips and a separate test profile. The result
retained 209 delivered frames, exact RGB16 pixels outside frames [107,141),
and bit-identical decoded PCM audio. A second GPU export reused the persisted
samples without rerunning capture or DLSS and passed the same checks.
CPU tests additionally cover disk cache binding,
resume identity, no-op paths, trims/gaps/reorder, multi-join forward decoding,
exact replacement and cancellation without changing the baseline.

## Local experiment, 2026-09-17

Only private copies of the previous dog research were used. No production
project, original research checkpoint, or installed H3 node-pack was rewritten.
Tests used RTX 5090 local ComfyUI on port 8189, not the Docker instance.

The source was a real native masked-AV continuation: 512×288, two RAW clips of
124 frames each, with 39 repeated context frames in the second clip. Delivery
is 124 + 85 = 209 frames at 24 fps. Target size was 1024×576.

The controlled USDU comparison used the same saved DLSS inputs, FL2VA INT8,
Turbo v4 at 0.75, er_sde/beta, 3 steps, denoise 0.2, 512×288 tiles, and disk
canvas storage. The provided workflow defaults to Ref2VA for reference recovery;
choose models and LoRAs appropriate to the original generation.

| Measurement | Independent USDU | Protection + tone fade |
| --- | ---: | ---: |
| Mean absolute RGB difference at the join | 0.042417 | 0.035590 |
| Join difference / nearby-frame difference | 1.2823 | 1.0798 |
| Three-frame mean brightness drop | 0.002995 | 0.001856 |

Boundary difference decreased **16.1%**. This simple, motion-sensitive diagnostic
is not a perceptual score and cannot establish general visual quality. The
restored 39-frame prefix matched the prior HQ tail exactly in decoded RGB16.

The 2-scene GPU loop completed through conditioning, USDU, protection, save,
loop advance and final assembly. Frame counts and 24 fps were retained;
delivered audio tensors were bit-identical to both source checkpoints. Hashes
of every pre-existing file in the duplicated source project remained unchanged.
Resuming from scene two also completed successfully, reusing the saved first HQ
scene without changing its checkpoint. The resumed export retained the same
frame count and exact source audio.

### Important validation limitation

Fresh DLSS stalled during its external worker startup. Releasing H3 models freed
about 26 GB of VRAM, but a second attempt still stalled. Only those test workers
were stopped; ComfyUI itself was not restarted. The cause was not established.

The successful full loop therefore substituted the saved DLSS outputs from the
earlier dog experiment. **Fresh DLSS-through-assembly execution is not validated.**
The shipped workflow still contains the normal DLSS node, not a cached-test
substitute. No DLSS runtime or driver changes are bundled with this feature.

## Coverage and limits

- CPU tests: exact-take binding, ALT isolation, unmarked bypass, 5/22-frame
  context windows, bounded mask storage, RAW counts, protected prefix, tone
  disable/fade, missing HQ, and resume contracts.
- JS/browser tests: checkbox persistence, turning it off, selection changes,
  project isolation, and no project write requests.
- Workflow tests: matching save/loop VIDEO, original audio, disk canvas,
  schemas, generated-file consistency and non-overlapping layout.
- Unsupported marks: arbitrary mid-clip references, multiple visual blocks,
  unrelated preceding takes, or mismatched visual-context/trim lengths.
- Tone matching remains experimental. Lighting changes can make it undesirable.
  It adjusts RGB bias only; it cannot repair different motion or geometry.
- File-backed streaming bounds helper memory, but USDU tile/model memory and
  video decoding/disk I/O still have costs. Use a fast SSD canvas directory.
- Change the profile or recipe when changing external processing settings. If
  marks change, reprocess that scene and its following continuations.
- No helper performs project scans, media reads or model work on workflow load.

Dependencies are `ComfyUI-DLSS5-Enhancer`,
`ComfyUI_UltimateSDUpscaleGuider_H3`, and
`ComfyUI-ContextAnchoredTile-videopath`. The latter supplies VIDEO transport;
CAT refinement is not used.
