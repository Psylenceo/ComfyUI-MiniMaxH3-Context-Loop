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
