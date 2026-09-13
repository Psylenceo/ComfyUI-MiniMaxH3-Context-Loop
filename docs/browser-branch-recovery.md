# Browser branch recovery

Plan Studio stores unsaved branch prompts, settings, pending cut edits and
uncertain branch requests in a dedicated IndexedDB database,
`h3-branch-recovery-v1`. These are browser-local recovery copies, not shared
branch saves. Saving a branch still uses the existing server API.

Large H3 recovery payloads no longer use `localStorage`, which ComfyUI also uses
for its automatic workflow drafts. The small workflow/node recovery identity
hint remains there; failure to write that hint does not disable recovery.

## Existing installations

On the first Plan Studio load, existing `h3-branch-draft-v1:` and
`h3-branch-pending-v1:` entries are copied, committed and read back before their
exact unchanged localStorage entries are removed. Full histories and exact seed
strings are preserved, including entries from other H3 projects/node instances.
ComfyUI drafts and other extensions' storage are not removed. If copying,
verification or storage access fails, the original recovery entries remain.
Conflicting copies left by older tabs are kept and reported, never overwritten.

After updating, allow the workflow to load once for this upgrade. If ComfyUI had
already marked its draft storage unavailable, refresh the page once more: that
frontend flag lasts for the page session. Do not clear site data to resolve it.
Update/reload other open ComfyUI tabs too, so old node code stops writing to
localStorage. No ComfyUI server restart or chain-folder migration is required.

## Limits and failure behavior

H3 recovery has a 64 MiB per-origin budget (conservatively counted as UTF-16
key/value bytes). Existing recovery is never automatically evicted to make
space. Oversized legacy histories can be imported intact, but cannot grow
further above the cap. New writes that exceed the cap fail atomically and show
an inline message directing you to save the branch or export the workflow.
There is no fallback to filling localStorage again.

Recovery writes are asynchronous. Navigation that needs a backup waits for the
transaction to commit; a failed backup leaves the current branch open. Explicit
server saves remain available if browser recovery is unavailable. The same
failed unchanged auto-draft is not retried on every 500 ms poll. As with any
browser-local recovery, save/export before closing; a final asynchronous write
cannot be guaranteed to finish if the browser is terminated.

Tests: `node tests/_working_branches_recovery_js_test.mjs` and
`CHROME_PATH=/usr/bin/google-chrome-stable node tests/_branch_recovery_storage_browser_test.mjs`.
The browser test uses a new temporary profile and loopback origin, never the
user's actual ComfyUI browser storage.
