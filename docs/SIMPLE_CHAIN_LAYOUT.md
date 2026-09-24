# Simple chain layout

This replaces the **proposal**, not the implementation, in the earlier storage
migration documents. It does not bring back the experimental storage database,
startup inventory, automatic repair, or per-load media verification.

## What changes

New projects created by H3 use this layout. Existing project directories keep
their current layout, including when more scenes or processing takes are saved.
Opening a workflow never runs conversion or an additional storage inventory.

```text
h3_chains/<run>/
  generation/
    clips/                         generated scene MP4s, including ALT takes
    audio/                         generated scene WAVs
  processing/
    original__01_chapter/<pass>/    original branch, first chapter, named pass
      clips/
      audio/
  exports/
    videos/<scope>/generation/      assembled generation MP4s
    videos/<scope>/pass-<pass>/     assembled processed MP4s
    frames/<scope>/generation/<export>/
    frames/<scope>/pass-<pass>/<export>/
    audio/<scope>/generation/<export>/  new audio-only exports
  assets/                          output-side Project Asset backup
  extras/                          unrecognized legacy integration folders
  .h3/
    layout.json                    small cached layout selector
    checkpoints/                   shared immutable tensors and metadata
    branches/                      branch assignments and authored settings
    chapters/                      chapter snapshots and processing state
    upscaled/                      unchaptered processing state
    recovery_archives/             original immutable Plan/workflow snapshots
    reference_cache/               saved conditioning required for recovery
    ...                            other existing internal state
```

Folders are created when used, not as a large empty scaffold. `.h3` is essential
project data, **not** a disposable cache. Original input assets remain under
ComfyUI's input folder; they are not relocated.

Scopes are `original`, `original__<chapter>`, `branch-<branch-id>`, or
`branch-<branch-id>__<chapter>`. A stable branch ID avoids changing file locations
when a branch is renamed. Each processing scope contains the readable pass name.
This adds one scope level to the initial sketch to keep names unambiguous without
a name registry or per-request branch lookups.

Existing filenames, revision suffixes, frame numbers, and export collision
numbering are preserved. A scene is in `generation/clips`; the assembled full
clip is in `exports/videos`. Technical processing latents/manifests stay in
`.h3`, not among the MP4s. This changes paths, not trim, context, sampling, seeds,
prompts, or which ALT supplies the final picture.

PNG sequences keep their synchronized `audio.wav` beside their frames. New
audio-only exports use `exports/audio`; converted older audio-only exports keep
their historic export directory and can still be reused there.

## Convert an existing project

Conversion is optional and **copy-only**. Run the tool from this repository;
it does not require ComfyUI, torch, or model loading.

1. Preview without changing either project or destination:

   ```bash
   python tools/convert_chain_layout.py /output/h3_chains/my_project
   ```

2. Stop ComfyUI and any other writer of this project. Create a separate copy:

   ```bash
   python tools/convert_chain_layout.py /output/h3_chains/my_project \
     --copy-to-output /migration-test/output --writers-stopped
   ```

   The result is `/migration-test/output/h3_chains/my_project`. The run name
   is unchanged. Every copied file is byte-compared with its source. Immutable
   JSON is **not** rewritten: historical relative paths and the original
   project's absolute prefix are resolved by the path adapter. The source is
   rechecked for additions/removals/changes before the marker is installed.

3. Test the copy before replacing anything. With ComfyUI stopped, keep the old
   project folder as a backup **outside the active `h3_chains` directory**, then
   place the copied `my_project` folder at the original project's exact path.
   Restart ComfyUI. Activation and removal of backups are deliberately not
   performed by the converter.

Conversion refuses an existing destination, links/junctions, a previously
organized source, collisions, or insufficient reported disk space. If copying
fails, the source stays intact and an incomplete destination may remain without
a layout marker; do not activate it. It is not silently deleted or resumed.

Only this project's files are copied. Shared `h3_reference_cache` objects and
input assets outside the project stay where they are. For isolated testing with
a different output root, separately supply those external dependencies; after
activation at the original output root they remain available as before.

H3's readers resolve historical paths. External editors and stock ComfyUI loaders
with literal moved-file paths may need relinking; this tool does not modify
external workflows or create compatibility symlinks.

## Runtime boundary

`chain_layout.py` selects the layout from one cached marker and computes paths.
It does not list directories, hash files, load tensors, or repair metadata.
Creation uses the existing project write lock. Scans and byte comparison belong
only to the explicitly invoked converter. Existing execution-time integrity and
deletion protections remain in their existing feature owners.

Regression tests include legacy and organized saves, byte-preserving conversion,
branch/ALT selection, independent processing ranges, source audio and DeRoPE
reuse, export numbering, and dependency-protected deletion. Tests use temporary
fixtures/CPU checkpoints, not the live user project or a GPU generation job.

## Integration checks

The integration also covers durable Gate recovery on Original and named branches,
post-assembly checkpoint cleanup, and obsolete-path removal after scene
reattribution. Cleanup recognizes both historical and `.h3` checkpoint addresses;
shared ownership, changed-file, symlink and incomplete-export protections remain.

Focused tests can be repeated from the repository root with its usual test
dependencies installed:

```bash
python -B tests/_chain_layout_unit_test.py
python -B tests/_chain_layout_review_unit_test.py
python -B tests/_chain_layout_reattribution_unit_test.py
for layout in legacy organized; do
  H3_TEST_LAYOUT="$layout" python -B tests/_upscale_chain_unit_test.py || exit
done
for layout in legacy organized converted; do
  H3_TEST_LAYOUT="$layout" python -B tests/_assembly_checkpoint_cleanup_unit_test.py || exit
  H3_TEST_LAYOUT="$layout" python -B tests/_upscale_alternate_unit_test.py || exit
  H3_TEST_LAYOUT="$layout" python -B tests/_chapter_incremental_export_unit_test.py || exit
done
```

Before deploying, test a separate, completed project copy with isolated output
and input directories. Check workflow reload and assets, branch/chapter selection,
Gate recovery, upscale/DeRoPE resume, and final MP4/PNG/audio export. Compare UI
responsiveness with the legacy copy. Automated fixtures do not replace this real
workflow check; never use the original or the only backup as the test destination.
