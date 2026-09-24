# Plan Studio chapter folding

Click **▾** beside a chapter title on the Generated track to fold its scenes
into one compact block showing the title, scene count and used duration.
Click **▸** to expand it again. The chapter settings panel also provides
Collapse/Expand and Play chapter buttons.

Click the folded block's title to open chapter playback. Its local track fits
the whole chapter, and its playhead runs from zero to the chapter's used end.
Click a scene or black-gap block in that local track, or drag the slider to
scrub. Playback moves through the chapter and stops at its end; pressing Play
there replays the chapter. **Full timeline** returns to project-wide playback.
Clicking a scene card or scrubbing the main ruler also exits chapter focus.

- Preview uses the existing selected scene/ALT picture and original audio,
  including trim/slip windows and internal black gaps. No chapter movie is built.
- Collapsed sections compress the overview only. The main ruler still labels
  project time; source tracks, subtitles and the playhead follow that same
  compressed display mapping. Expand scenes for individual editing.
- If editorial placement interleaves chapters, a chapter can have more than one
  compact block. Its local player includes only that chapter's scenes and
  internal gaps, in their editorial order, not scenes belonging to other chapters.
- Folding and chapter focus are saved in the Studio node's workflow properties,
  separately for each run/working branch. Save the workflow to retain them.
  Existing workflows start expanded.
- This is UI state only: it does not change Plan JSON, prompts, seeds, source
  frame counts, context, upscale processing, final exports, or the chain folder.
  Folding adds no backend scans or project-file writes.
