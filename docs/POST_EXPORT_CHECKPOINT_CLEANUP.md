# Post-export checkpoint cleanup

For disposable overnight batches, enable `delete_checkpoints_after_assembly`
on **H3 Chain Assemble**. It is **off by default**, including in older workflows
and all shipped examples.

This permanently deletes the completed manifest's unshared `.safetensors`
checkpoint payloads after the final video, audio/subtitle sidecars and any
requested output copies have been saved. The Assemble status reports the file
count, freed space, and any retained files or reason for skipping cleanup.

Keep it **off** if you plan to resume, create alternate takes from those
checkpoints, latent-upscale, or reassemble through Load Manifest later. Those
operations require the checkpoint payloads even though the metadata remains.
Deleted payloads cannot be recovered without a backup or regeneration.
Saved scene/final videos remain available for playback or ordinary video editing.

## Scope and safeguards

- Keeps scene/final videos, prompts, metadata, references and all other assets.
- Only considers checkpoint paths listed in this completed manifest. Does not
  purge old takes, caches, other projects or the whole checkpoints directory.
- Keeps checkpoints referenced by other saved takes, branches or processing
  manifests, as well as hard-linked or changed files. Space savings can therefore
  be smaller than the total checkpoint size.
- Supports completed generation, chapter and upscale/DeRoPE assemblies. An
  upscale assembly considers its processed checkpoints, not its original
  generation checkpoints.
- Skips partial assemblies and automatic partial review previews. An encode,
  output-copy or file-flush failure leaves the checkpoint payloads intact.
- If ownership metadata is unreadable or an assignment is pending, skips
  cleanup and reports why; the already-saved export remains usable.

There are no new startup hooks or background scans. Only an opted-in successful
assembly inspects the project's known metadata folders for shared checkpoints;
it does not traverse frames, assets or other projects.
