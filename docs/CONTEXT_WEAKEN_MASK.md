# Fixed context masks

In **Plan Studio → Context → Picture**, select an earlier scene's picture
block. With **Masked AV**, **Feathered AV** or **Audio Feather AV** selected,
click **Weaken context…** beneath its video preview.

1. Select the context window as usual (for example, five frames for a hard cut).
2. Paint over the element whose appearance should be less constrained. The
   video pauses for painting; the orange overlay shows the mask.
3. Adjust **Release %**, **Brush radius (cells)** and **Soft edge %**. Use
   **Erase**, **Undo stroke** or **Reset mask** to correct it.
4. Click **Done painting**, save/update your active branch as usual, and generate
   the scene using that context.

**Release 0%** keeps the normal context lock. **50%** partially releases the
fully painted cells. **100%** lets those cells denoise fully; unpainted cells
keep the existing AV mask. Feathered AV's existing temporal release is never
reduced by the brush. A soft edge uses intermediate strengths.

The same spatial mask applies to every frame in that block. There is no
tracking: if an element moves, paint an area covering its movement. Other
blocks have independent masks. Changing the source scene clears that block's
mask; moving its window within the same source keeps the mask.

## What stays unchanged

- The source clip/checkpoint is never painted over or rewritten.
- Audio samples and their masks are unaffected, including locked lip-sync audio.
- Raw frame count, context-prefix length, delivered timing, editorial trims,
  and the full-frame geometry used by DeRoPE/upscaling remain unchanged.
- Old Plans without a mask keep the existing behavior and history contract.

This affects a **new generation**, not an already-rendered clip or a later
upscale of that old clip. Existing renders remain available; a changed mask is
recorded as a changed generation input, like other context settings.

## Saving and limits

Strokes are stored on release in `visual_context_blocks[].weaken_mask` inside
the Plan, using a compact relative grid. Workflow/branch saving uses the normal
Plan controls. Masks also travel with saved checkpoint metadata and are restored
with that take's settings. Opening the editor does not change the Plan. There
are no separate mask image files, startup scans, or per-frame paint buffers.

The brush snaps to H3 spatial tokens (normally 32×32 pixels at generation
resolution), with 17 coverage levels. This is a coarse context control, not a
pixel-accurate inpainting tool. Reducing the lock does not guarantee that an
object disappears: the source latent, prompt and other references can still
influence the result. Visual effectiveness depends on the scene and settings.

Mask painting is disabled outside the three supported AV modes. Remove a saved
mask before queueing under another mode; it is never silently ignored.

## Tests

- `node tests/_context_mask_js_test.mjs` — brush, persistence, restored takes,
  timing and pointer lifecycle.
- `python tests/_context_mask_unit_test.py` and
  `python tests/_masked_prefix_unit_test.py` — CPU tensor checks, history and
  branch recovery; requires the usual test Python dependencies.
- Optional: `CHROME_BINARY=/path/to/chrome node tests/_context_mask_browser_test.mjs`
  — real pointer painting, reload and reset/undo using synthetic video and a
  temporary browser profile, without accessing a ComfyUI project.
