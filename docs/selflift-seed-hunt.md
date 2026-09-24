# SelfLift Seed Hunt (experimental)

Use **MiniMax H3 SelfLift Seed Hunt** in place of the Chain SelfLift Sampler,
or open the **Ref2V Studio SelfLift Seed Hunt** example. It is now the one
shipped SelfLift example, covering both automatic and reviewed sampling.
Set `review_enabled=false` for automatic mode without tiny previews or a
seed-review pause; new batches use the input seed and existing approved batches
retain their resume selections. Existing user workflows, the original sampler
node and the ordinary Review Gate remain supported.

1. Enable SelfLift Project and select its H3 upscaler (see choices below). Use Euler
   (the default) or the experimental Radau setup below, with the same
   total/high-step split used by ordinary SelfLift.
2. For review previews, install current KJNodes and select `taeh3.safetensors` from `models/vae_approx`.
   The decoder is the same one used by Model Preview Override KJ. The
   [Kijai H3 TAE weights](https://huggingface.co/Kijai/MiniMax-H3-TAE/blob/main/vae_approx/taeh3.safetensors)
   and the temporal TAEH3 format supported by KJNodes both work.
3. Set candidate count and a batch name. Keep the base seed **fixed**. Queue.
   Candidate seeds are base seed, base seed + 1, etc., wrapping at uint64.
4. Browse the low-pass videos with the Review Gate-style arrows or dots.
   Optionally click **Preview upscale** to inspect the lifted latent using the
   Tiny VAE before spending high-resolution denoising steps. Then
   click **Use take — finish upscale** for one take, or **Mark for upscale** on
   several takes, choose **Make main**, then **Finish N marked**. The main is
   always included. Browsing and marking do not release the gate.
   The single player fills the node; drag
   its lower handle to resize it, or double-click the handle to restore auto-fit.
   Only approved takes get the remaining high-resolution steps, one at a time.
   These are silent, approximate motion/composition previews, not final-detail
   or audio-quality previews. Flat 2D TAE frames repeat at H3's token timing;
   playback duration is correct, but motion has fewer distinct frames.
5. Wire `selected_state` to downstream Trim, Segment Save, final Review Gate
   and Loop End. This records the chosen seed and carries it to later scenes.
   The example already does this. Keep the final Review Gate's candidate count
   at **1** for single-take hunts. A multi-take selection supplies its own
   finished candidate set and overrides that count; it never starts extra hunts.

## Finish several takes with one main

Marking the first take also makes it main. Mark additional takes, or use
**Make main** on another completed preview; the previous main stays marked
until you unmark it. The main cannot be unmarked without choosing another main.
Mark up to 20 takes per finished review (you can still hunt up to 100 low passes).
Marks and the main choice are stored on the server and shared across tabs.

**Finish N marked** commits the selection. Alternates are upscaled, fully
decoded and saved first; the main is finished last. Each uses the same original
scene context, never the preceding alternate. Alternates are saved revisions,
but do not replace the branch's active scene or advance the loop.

The final Review Gate shows all finished takes, initially displaying the main
and keeping all marked takes. You can change which take continues the chain or
unkeep alternatives there using the existing review controls. If final review
is disabled, the main continues and the other finished revisions remain saved.
No new nodes or additional high-resolution latent batch in memory are needed.

After interruption, queue the matching scene/settings in **resume** mode:
saved alternates are skipped and completed high latents are reused. A failed
high pass restarts from its saved low pass. Auto-remove waits until **every**
marked clip/checkpoint, including the main, has been committed. The selection
is locked while finishing; browsing remains available.

## Preview the latent upscale before approval

**Preview upscale** sits beside **Use take — finish upscale**. It reuses the
saved low pass, applies the configured upscaler and lift corrections (including
`rho` when enabled), and decodes a silent full-resolution Tiny-VAE preview.
It does **not** run the high denoiser, change the seed, select the candidate,
or release the gate. With `rho=0`, it does not need a full-VAE round trip.

- Requests made while another low candidate is running wait for it to finish
  and save. GPU work is serialized inside the executing hunt, not an HTTP handler.
- Use **Show low preview** / **Show upscale preview** to compare the two saved
  videos. Reopening the upscale preview does not repeat the lift or decode.
- Wait for the preview to finish before approving. A failed preview keeps the
  gate open and the low pass intact, with a retry button and error message.
- To make a new upscale preview after interruption/restart, queue the matching
  workflow in **resume** mode. Existing previews remain viewable while stopped.
- Hunt cleanup includes these extra temporary previews; normal saved scenes
  are unaffected.

This is an artifact-screening aid, not a final-quality guarantee: the tiny
decoder is approximate, and artifacts can also appear during high denoising.
No upscale-preview computation happens unless explicitly requested.

### Known quality limitation

Some learned-lift results develop localized colored patches or blocky detail
that were absent in the low-resolution latent. This has been observed with
the LBH lift and is not resolved by this release. A clean low preview alone
does not guarantee a clean upscale: inspect **Preview upscale**, then check
the fully decoded final take before keeping it. Another seed can help, but is
not a guaranteed fix. Tr1dae and bilinear remain comparison options, not
equivalent-quality replacements. High-resolution denoising tiles do not change
the learned lift and should not be presented as a fix for its artifacts.

## Latent upscaler choices

Use the existing **SelfLift Project → upscaler_model** dropdown; no rewiring is
needed. This applies to both Chain SelfLift Sampler and SelfLift Seed Hunt.
Existing checkpoint selections and the disabled/default `none` are unchanged.

- **An installed LBH checkpoint:** the existing 3D-convolution lift, unchanged.
- **`tridae`:** Tr1dae's `h3_clean_latent_upscaler_film_epoch200.safetensors`,
  run in FP32 using the clean H3 VAE-space latent. No LBH normalization,
  temporal chunks, or artificial prefix split is applied. The complete native
  temporal context goes through the model; the sampler still restores its
  existing video/audio masks and context anchors afterward.
- **`bilinear`:** independent FP32 bilinear spatial interpolation for each video
  time token (`align_corners=False`). No weights, downloads, temporal mixing,
  VAE round trip (unless you explicitly enable `rho` below), or learned correction. The normal high-resolution sampling
  steps still run; this is an experimental comparison option, not a promise of
  equivalent final quality.

Tr1dae is a fixed **2x** model. Keep `lowres_scale=0.5` and use final Plan dimensions divisible by **64**,
such as **1920×1088**. An incompatible grid is rejected before the low pass;
there is no hidden second resize. Bilinear supports the existing target grids.

On first **execution** of the Tr1dae lift, the pinned ~59 MB checkpoint is
downloaded into `models/latent_upscale_models` and SHA-256 verified. Schema/UI
loading never downloads or loads weights. Existing copies in registered
`latent_upscale_models` or `h3_latent_upscalers` directories are reused when
their checksum matches; a mismatching user file is never overwritten. No
additional custom-node pack is needed. Weights are separate from this repo:
[Tridae/H3LatentUpscaler](https://huggingface.co/Tridae/H3LatentUpscaler).

Keep the seed, conditioning, sampler and finishing steps fixed for comparisons.
Changing the upscaler creates a distinct Seed Hunt identity, so a finished take
from the old upscaler is not returned as a new test. Previous saved clips are
not regenerated automatically: explicitly regenerate the scene you want to
compare. Changing the lifter also makes continuation fall back to saved HQ
context rather than reusing an incompatible native low-resolution carry.

With **cleanup_between_stages** enabled, Tr1dae's small legacy patcher is
specifically offloaded to CPU after the successful lift. Other loaded models
are not targeted. Bilinear has no model to retire. This does not change the
existing DynamicVRAM cleanup behavior for the diffusion models or LBH.

## Lift controls

**SelfLift Project** exposes the same controls to both samplers:

| Control | Default | Meaning |
| --- | --- | --- |
| `lowres_scale` | `0.5` | First-stage width/height relative to final Plan size, rounded to the even latent grid; range `0.25..1`. Time/audio are not scaled. Tr1dae requires an exact 2x grid. |
| `rho` | `0` | Fraction of the most inconsistent latent locations selected for pixel/VAE correction; range `0..1`. Zero disables correction. |
| `w_min` | `0.5` | Minimum correction strength at selected locations. |
| `w_max` | `1` | Maximum correction strength at the most inconsistent locations. Require `0 <= w_min <= w_max <= 1`. |

Defaults preserve the existing direct lift. **`rho > 0` with `w_max > 0` adds a
full video VAE decode → pixel resize → VAE encode at the transition.** This
costs time and memory and can change color/detail; it is experimental, not a
guaranteed fix for upscaler artifacts. The weights do nothing when `rho=0`.
Both weights set to zero also skip the pixel/VAE pass.

The new widgets are appended after the existing controls. Old workflows,
native low carries and saved Seed Hunts remain compatible at default values.
Changing a lift control creates a distinct Seed Hunt identity and falls back
to saved HQ continuation context instead of using a carry from different lift
settings. Requeue with unchanged controls to resume a matching hunt. This does
not delete or automatically regenerate clips that were already saved.

## Run without the review gate

**Review gate** defaults to **on**, including in existing workflows. Turn it
**off** to run automatically without stopping to choose a candidate:

- If this matching batch already has approved takes, resume that selection,
  including all marked takes and its main.
- Otherwise, generate or reuse just **take 1**, using the input seed. Candidate
  count is ignored; the node does not generate a batch that nobody will review.
- Tiny-VAE decoding is skipped, so the tiny model and KJNodes decoder are not
  required for this mode. Existing previews are kept; new automatic takes show
  their saved metadata without a video preview.

The middle pass and finished high latent are still saved for OOM/reboot recovery.
The switch does not change the batch identity or invalidate saved work. Turning
it back on also keeps an already chosen take; use a new batch name for a fresh
hunt. Auto-remove and manual cleanup work as before, with auto-remove deferred
until Segment Save succeeds.

This is a **queue-time** setting, not a way to interrupt a running hunt. It only
disables the Seed Hunt selection pause, not the separate final Review Gate.

## Experimental Radau IA 2s

Connect RES4LYF **ClownSampler** to the existing `sampler` input on either
Chain SelfLift Sampler or Seed Hunt. Select **fully_implicit/radau_ia_2s**
(not IIA 3s), set **eta = 0**, and keep your BongMath setting. A connected
ClownSampler Selector takes precedence over the sampler's stored dropdown.
Keep the existing sigma scheduler, including Beta. The example workflows
still default to Euler; no new project switch, model or upstream patch is needed.

This first adapter supports the plain ClownSampler setup without separate
RES4LYF guides, schedule overrides, sampler swaps or free-text extra options.
H3's own AV masks, locked audio, tagged references and continuation remain on
the Chain's native paths. The connected Radau sampler is used for both stages.

Radau finishes its low-resolution interval, then one additional low-resolution
model evaluation creates the clean prediction for lifting and tiny preview.
Audio keeps its actual noisy boundary state; it is not advanced again with
Euler. The high pass starts a fresh solver at that same noise level, using the
existing full-resolution context anchors. Model-call cost is therefore not
the displayed step count and will be higher than Euler's; quality is experimental.

Radau takes have a distinct handoff format and sampler-settings fingerprint.
Resume with the same sampler/options. Old Euler takes retain their format and
identity; switching samplers creates a separate hunt, never converts old takes.
As with Euler, recovery restarts an unfinished high pass from its saved boundary,
not from an internal Radau substage.

## Separate finishing checkpoint

Both **Chain SelfLift Sampler** and **SelfLift Seed Hunt** accept an optional
`model_hires` **MODEL** input. Connect a second H3 diffusion-model loader's
MODEL output there to use a different compatible checkpoint for the remaining
full-resolution steps. Keep the original low-pass model connected to `model`.
Leaving `model_hires` unconnected keeps the previous same-model behavior;
SelfLift off ignores it. No existing workflow needs rewiring.

The second checkpoint must accept the same H3 AV latents, VAE and text/reference
conditioning, with matching flow/audio scaling and sampling settings. Incompatible
families, latent formats and conditioning dimensions are rejected before sampling.
The sampler (Euler or supported Radau), CFG, sigma schedule and step split remain
shared. This is not a generic cross-model-family refiner or a second latent-upscaler
checkpoint; the latent upscaler is still selected on SelfLift Project.

The high model keeps its own LoRAs and engine patches. Connect any desired LoRA
setup to that loader separately; base-model LoRAs are not copied automatically.
Chain Drift Control is rebound for the high grid from the base stage's continuity
policy, and native video/audio masks and source-audio locks remain in use.
ComfyUI manages each stage's model loading; a second checkpoint can still add
considerable CPU RAM use and model-swap time.

### Optional stage memory cleanup

Enable **cleanup_between_stages** on **SelfLift Project** to retire the distinct
low-pass checkpoint before the learned lift, then unload the learned upscaler
after it succeeds. This applies to both SelfLift samplers and is **off by
default**. It uses ComfyUI's managed DynamicVRAM unload to release reloadable
host/pinned buffers and GPU allocations. Shared checkpoints (including clones
used by the finishing model), its model dependencies, and unrelated loaded
models are protected. Classic/non-dynamic models are skipped: moving their
weights to CPU could increase RAM use instead.

Look for `[SelfLift memory] ... cleanup before/after` and `[SelfLift cleanup]`
in the console. They report process RSS, available system RAM, GPU statistics
where applicable, and unloaded/skipped counts without requiring an environment
variable. Graph-owned weights, conditioning, and OS file caches can remain in
RAM; this is not a guarantee that all model memory disappears. The next scene
may take longer to reload its base model/upscaler.

Candidate hunting keeps its low model loaded until finishing starts. Saved
takes, selection, and finished-result identity are unchanged by this switch,
so it can be enabled while resuming a paused hunt. A cached finished result
needs no stage cleanup. No files or execution caches are deleted. Cleanup
checks cancellation and fences CUDA transfers before unloading; it is never
used as an exception handler for a failed/cancelled sampler or upscaler. This
does not fix an underlying CUDA allocator/driver cancellation crash.

In Seed Hunt, changing **only** `model_hires` or its upstream LoRA settings reuses
the same saved low takes and selected seed, but gets a separate finished-latent
cache. Switching back can reuse that finishing setup's completed result. Keep
**Auto-remove saved takes off** to compare multiple finishes. Disconnecting the
input can still use the original same-model finished file. The saved workflow
download is updated to the latest queued finishing setup. The upstream recipe
is required for safe high-pass caching, including virtual/subgraph node recipes;
direct Python calls without that prompt recipe fail rather than reuse an
unidentified finishing checkpoint. Models replaced in-place under the same name
still require a fresh batch name.

## Optional high-resolution denoising tiles

Add **MiniMax H3 SelfLift Tiling — Experimental**, connect its `tiling` output
to **`highres_tiling`** on either SelfLift Seed Hunt or Chain SelfLift Sampler,
and turn **enabled** on. Off or unconnected preserves the existing full-frame
path and cache identity. SelfLift Project itself must also be enabled.

- **tiles**: 2–8 strips along one spatial axis; small grids use fewer tiles.
- **overlap**: context margin on each side, in latent pixels (normally 16 output
  pixels per latent pixel). Default 8; even values 0–64. Margins are clamped on
  small tiles and adjacent predictions are linearly blended.
- **axis**: longest dimension automatically, or explicitly width / height.

Only the final **high-resolution denoising steps** are tiled. The low seed hunt,
learned latent lift, tiny-VAE **Preview upscale**, and normal VAE decoding are
unchanged. This does **not** add TST. Every tile keeps the full timeline; audio
and independent reference grids stay whole. Native video masks are cropped to
each tile while the outer sampler retains continuation and audio-lock ownership.
Keyframes are cropped with the video, retaining their original full-frame
positional coordinates. ControlNet and regional conditioning are rejected.

This is a memory/quality tradeoff, not an artifact fix: tiles have no cross-tile
attention, and the first tile supplies the audio prediction. Seams, motion or
audio quality can change. CPU FP32 accumulation reduces GPU workspace but adds
transfers; more tiles are not necessarily faster. Full-size sampler buffers and
the separate latent lift still need memory.

Changing tiling settings reuses saved low candidates but creates a distinct
finished-result cache, like changing `model_hires`. Turning tiling off restores
the untiled cache. Keep **Auto-remove saved takes off** when comparing. Resume
an in-progress multi-take finishing batch with its matching saved settings.
Existing workflows need no new connection and are not rewritten automatically.

## Choosing before the batch finishes

As soon as a completed preview appears, click **Use take N now**. No need to
stop or requeue: the candidate currently generating finishes its low pass and
preview, both are saved, then all remaining candidates are skipped and your
chosen take is upscaled. If no candidate is in progress, it proceeds directly.
The panel shows the pending choice while the current candidate finishes.
Once approved, the panel locks that selection for this execution.

The early choice is saved immediately. If the current candidate fails or the
server restarts before the upscale, queue the same workflow/settings again:
it goes straight to the selected saved take, without generating the rest.

## OOM, restart, and refresh

Every completed low pass is saved **before** tiny decode or learned upscale.
Refresh the browser to see saved previews; it does not start computation.
After stopping, OOM, or server reboot, **queue the same workflow again**, with
the same batch name, base seed, model/LoRAs, references, prompt, context and
schedule. Completed low passes are loaded, not generated again. If you had
already approved a take, the job proceeds straight to its high pass.

The completed high latent is also saved before downstream full-VAE decoding,
so a decode/export failure can reuse it. A crash *during* the high pass restarts
that high pass, not the completed low pass. A crash during an unfinished low
candidate must rerun that candidate; earlier completed candidates remain saved.
This is boundary recovery, not per-denoising-step checkpointing.

The saved-batch dropdown can review and select a take even without a running
job. Browsing is separate from choosing: changing previews never approves a
take. Incoming candidates and ordinary polls do not interrupt playback, and
you can still browse while the chosen take is being upscaled.
**Download saved workflow**, under **Help & recovery**, provides the latest queued canvas snapshot if
needed. Selecting a saved take does not automatically queue a workflow or
replace the current canvas. The matching workflow must still be queued.
In a multi-scene run, set Loop Start to that saved batch's scene when recovering
mid-run (the downloaded canvas retains the original Loop Start setting).

Change `batch_name` for a fresh hunt. Changes to the generation recipe create
a separate batch, except finishing-model-only changes described above. Files replaced in-place under the same model/reference names
are not rehashed: keep them unchanged for resume, or use a new batch name.
Restore with the same node/model versions for consistent results.

## Storage and cost

Organized projects keep handoffs and input conditioning under
`.h3/reviews/selflift/<batch-id>/`; named branches use their corresponding
`.h3/branches/<branch-id>/reviews/selflift/` folder. The lightweight index is
`h3_chains/.selflift_reviews.json`. Legacy projects keep their existing layout.
Preview videos go in the project's `processing/<scope>/selflift_seed_hunt/clips/`.

One shared safetensors bundle stores masks, full-size context anchors and
conditioning. Each take stores the low-resolution clean video prediction,
the noisy audio handoff, seed, schedule and grid metadata. The selected final
latent has its own bundle per finishing setup. These are real disk files and can use substantial
space; rejected takes are **kept by default**. Model weights are
not copied into a batch. Unsupported non-tensor conditioning objects fail
before low-pass sampling rather than being pickled.

Temporary CPU tensor copies used to write each bundle are released when the
save finishes or fails, without waiting for cyclic garbage collection. This
does not delete saved takes or flush ComfyUI's model/output caches.

### Cleanup

- **Auto-remove saved takes** defaults to **off** (Keep saved takes). Leave it
  off to select and upscale another version from the same hunt later.
- Turn it **on** (Clean after scene save) to remove that hunt's temporary
  bundles, tiny previews and recovery snapshot after **Segment Save** commits
  every approved clip and its normal checkpoint, with the main committed last.
  Keep `selected_state` connected
  to Segment Save, as in the example. Choosing a take or finishing its high
  pass alone does not delete anything: a decode/save OOM still has recovery.
- **Clean saved takes**, beside Refresh, permanently removes only the batch
  selected in the dropdown after confirmation. It is disabled while that hunt
  is running. A multi-take selection awaiting downstream scene saves is also
  protected; resume the matching scene to finish those saves before cleanup.

Both paths keep normal scene videos, checkpoints, project assets and other
hunts. Cleanup cannot be undone: that batch can no longer resume or provide
another version without rerunning its low passes. Cleanup errors leave the
saved scene successful and are reported in the log and the saved hunt's gate.
Use **Clean saved takes** to retry removing the remaining temporary files; a
failed cleanup may already have removed part of the scratch/recovery set.
Loaded bundle tensors own their CPU memory rather than holding temporary files
memory-mapped, allowing cleanup while ComfyUI retains downstream outputs on
SMB/Windows too. After upgrading, restart ComfyUI to release mappings made by
the old loader before retrying cleanup of previously affected hunts.
The auto-clean choice does not change the hunt's identity; switching it on
can reuse an existing saved hunt with otherwise matching settings.

No migration, media scan, weight loading or tensor loading runs on workflow
load. The browser requests only small saved-review manifests and loads video
when played. Polling is shared between visible hunt widgets; preview elements
are preserved so polling does not interrupt playback.
