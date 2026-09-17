# SelfLift Seed Hunt (nightly, experimental)

Use **MiniMax H3 SelfLift Seed Hunt** in place of the Chain SelfLift Sampler,
or open the dedicated **Ref2V Studio SelfLift Seed Hunt** example. Existing
workflows and the ordinary Review Gate are unchanged.

1. Enable SelfLift Project and select its learned H3 upscaler. Keep Euler and
   the same total/high-step split used by ordinary SelfLift.
2. Install current KJNodes and select `taeh3.safetensors` from `models/vae_approx`.
   The decoder is the same one used by Model Preview Override KJ. The
   [Kijai H3 TAE weights](https://huggingface.co/Kijai/MiniMax-H3-TAE/blob/main/vae_approx/taeh3.safetensors)
   and the temporal TAEH3 format supported by KJNodes both work.
3. Set candidate count and a batch name. Keep the base seed **fixed**. Queue.
   Candidate seeds are base seed, base seed + 1, etc., wrapping at uint64.
4. Browse the low-pass videos with the Review Gate-style arrows or dots, then
   click **Use take — finish upscale**. The single player fills the node; drag
   its lower handle to resize it, or double-click the handle to restore auto-fit.
   Only that candidate gets the learned lift and remaining high-resolution steps.
   These are silent, approximate motion/composition previews, not final-detail
   or audio-quality previews. Flat 2D TAE frames repeat at H3's token timing;
   playback duration is correct, but motion has fewer distinct frames.
5. Wire `selected_state` to downstream Trim, Segment Save, final Review Gate
   and Loop End. This records the chosen seed and carries it to later scenes.
   The example already does this. Keep the final Review Gate's candidate count
   at **1**; it reviews the finished result, not another set of expensive hunts.

## Choosing before the batch finishes

As soon as a completed preview appears, click **Use take N now**. No need to
stop or requeue: the candidate currently generating finishes its low pass and
preview, both are saved, then all remaining candidates are skipped and your
chosen take is upscaled. If no candidate is in progress, it proceeds directly.
The panel shows the pending choice while the current candidate finishes.
You can change the choice until its high-resolution pass starts; selection is
locked during that pass.

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
**Download saved workflow**, under **Help & recovery**, provides the original canvas snapshot if
needed. Selecting a saved take does not automatically queue a workflow or
replace the current canvas. The matching workflow must still be queued.
In a multi-scene run, set Loop Start to that saved batch's scene when recovering
mid-run (the downloaded canvas retains the original Loop Start setting).

Change `batch_name` for a fresh hunt. Changes to the generation recipe create
a separate batch. Files replaced in-place under the same model/reference names
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
latent has its own bundle. These are real disk files and can use substantial
space; rejected takes are **kept by default**. Model weights are
not copied into a batch. Unsupported non-tensor conditioning objects fail
before low-pass sampling rather than being pickled.

### Cleanup

- **Auto-remove saved takes** defaults to **off** (Keep saved takes). Leave it
  off to select and upscale another version from the same hunt later.
- Turn it **on** (Clean after scene save) to remove that hunt's temporary
  bundles, tiny previews and recovery snapshot after **Segment Save** commits
  the chosen clip and its normal checkpoint. Keep `selected_state` connected
  to Segment Save, as in the example. Choosing a take or finishing its high
  pass alone does not delete anything: a decode/save OOM still has recovery.
- **Clean saved takes**, beside Refresh, permanently removes only the batch
  selected in the dropdown after confirmation. It is disabled while that hunt
  is running; stop it first if you want to discard an unfinished batch.

Both paths keep normal scene videos, checkpoints, project assets and other
hunts. Cleanup cannot be undone: that batch can no longer resume or provide
another version without rerunning its low passes. Cleanup errors leave the
saved scene successful and are reported in the log/manual button response.
The auto-clean choice does not change the hunt's identity; switching it on
can reuse an existing saved hunt with otherwise matching settings.

No migration, media scan, weight loading or tensor loading runs on workflow
load. The browser requests only small saved-review manifests and loads video
when played. Polling is shared between visible hunt widgets; preview elements
are preserved so polling does not interrupt playback.
