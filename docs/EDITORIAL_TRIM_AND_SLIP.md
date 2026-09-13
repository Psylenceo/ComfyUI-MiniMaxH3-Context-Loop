# Plan Studio trim and slip

For a generated scene, shorten the right edge to choose a used duration. Then
drag the small **↔** handle right to start later in the saved clip, or left to
start earlier. The handle moves both ends together: duration, timeline placement,
and later scenes' positions stay unchanged. Left/right arrow keys step between
available positions. A locked scene cannot be trimmed or slipped. **Full** (or
double-clicking the right edge) restores the complete delivered clip.

The source window snaps to H3 video-latent boundaries shared with the 24 fps /
40 Hz audio grid. The uncut source start and end are also allowed. Some lengths
have no other legal position; the handle is then disabled. Shorten the right edge
to allow a movable window. Cancelling a drag does not save it.

These are editorial edits only:

- Preview, final assembly, and checkpoint PNG/WAV exports use the selected window.
- Generated picture and audio use matching source offsets, including ALT picture
  with its original generated audio.
- Generation and resume keep the complete checkpoint and its original context.
  Existing dependent scenes are not invalidated by trim/slip edits.
- Scene upscale and the full-chain upscale source retain full source frames;
  the editorial window is applied at final assembly/export, not before processing.
- Prompts, seeds, generation lengths, source-reference clocks, and saved media
  are not rewritten. A slipped cut may look different at a scene join, but it
  does not require regeneration.

At a changed incoming/outgoing edge, final assembly uses a hard cut rather than
reintroducing trimmed frames through an overlap saved for the old boundary.

Existing `out_frame`-only trims load as a window starting at zero. New windows
store optional `in_frame` plus exclusive `out_frame` in delivered-source frame
coordinates, after the technical continuation-head trim. Their length is
`out_frame - in_frame`. Same-length slips invalidate editorial export reuse;
they do not invalidate full-source upscale caches. No folder migration is needed.

This changes the old right-trim behavior: it no longer changes the endpoint used
for subsequent generation. Old scenes and their recorded generation history stay
untouched; new continuation uses the original full source.
