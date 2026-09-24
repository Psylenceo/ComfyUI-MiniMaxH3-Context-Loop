# Reattaching and cleaning up a saved path

After replacing a scene (for example S12), attach the saved downstream scenes to
the new path. Reuse is allowed when their recorded context sources are unchanged.
Assign the completed path before removing the old one.

Assignment checks the revision identities, lineage, file availability and prompt
sidecars. It does not reread all videos and latent files to hash their contents.
Generation, resume and output consumers retain full integrity checks. Connected
Plan Studio views refresh immediately, including standalone Studio nodes; they
do not need to wait for the periodic checkpoint poll after assignment.

To remove the obsolete S12 and redundant old downstream links:

1. Select the old S12 in Checkpoint Manager.
2. Click **Delete obsolete path…**.
3. Review the exact revisions and files to delete and the retained replacements.
4. Confirm only if that is the path you intend to remove.

Cleanup is blocked if any downstream scene has not been reattached, a current
assignment or sealed chapter snapshot still uses the old path, or another saved
scene needs an input without an equivalent retained copy. Reattached scenes,
shared media/checkpoints/prompts, Plan/workflow archives, project references and
assembled exports remain intact. Obsolete unique files listed as **Delete** are
permanently removed; this is not an undoable action. A changed preview must be
requested again before deletion.

This is an explicit action, not a startup, polling or automatic storage cleanup.

## Selecting several checkpoints for deletion

On the Checkpoint Manager's **Original** tab (including its ALT cards):

- **Ctrl/Cmd-click** toggles an individual saved take.
- **Shift-click** selects the visible range from the last clicked take; Ctrl/Cmd
  plus Shift adds that range to the current selection.
- **Shift-drag** draws a selection rectangle. Ctrl/Cmd plus Shift-drag adds to
  the existing selection. The rectangle respects the graph and canvas zoom.
- **Escape** or **Clear selection** clears the selection. Normal clicking still
  previews a clip and establishes the next range anchor.

The amber selection is only for bulk deletion: it does not assign a branch,
change the preview cursor, or change the workflow's output pin. Changing the
project, working branch, stage or chapter tab clears it; hidden or disappeared
cards are dropped from the selection. It is not saved in the workflow.

Choose **Delete selected…**, inspect the exact revisions and files, then choose
**Confirm bulk deletion**. Only those revisions are proposed, never an implicit
branch or all descendants. A connected selected group can be removed together;
unselected dependents, retained working branches and sealed chapter snapshots
still block unsafe deletion. Selected active-tail pointers are explicitly
listed before removal. Shared files are kept, including files shared between
selected revisions. A changed selection or stale preview requires a new preview.

Bulk selection currently covers generated checkpoints and ALTs. Processed takes
keep their existing separate single-take deletion controls.
