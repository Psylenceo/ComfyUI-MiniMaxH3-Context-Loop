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
