# Review Gate Relay

Use **MiniMax H3 Review Gate Relay** when you reconnect remotely and cannot
access the canvas that started a candidate batch.

1. Open ComfyUI on the **same server** as the running job.
2. Add the Relay node to any canvas, including an empty workflow. No wires,
   Plan, model loaders or **Queue** click are needed.
3. Select the project, branch, scene and Gate in the top selector.
4. Browse takes with the selector or arrows. Play/scrub the selected video;
   its saved prompt and full seed are shown below. Only that preview loads.
5. Choose **Approve selected** to resume the waiting job, or **Select & stop**
   to select that take and stop through the original Gate's normal behavior.

All takes are kept by default in the Relay. Uncheck **Keep this take** for
takes you want the Gate to remove. The selected take is always retained, and
the Relay asks for confirmation before sending a decision that could delete
unchecked takes. Cleanup follows the original Gate's existing rules.

With **Review each candidate** enabled, **Next candidate** tells the waiting
job to generate the next take. During an automatic batch, completed takes can
be inspected, but decisions unlock only when the Gate actually waits. The
Relay does not cancel an in-flight candidate or queue a replacement workflow.

## Reconnection and limits

- The visible Relay checks lightweight in-memory state every two seconds.
  It does not scan chain folders, read checkpoints while polling, decode video
  on the server, or reload an unchanged video while you inspect it.
- Browser refreshes, disconnected sessions and other canvases can reconnect
  to a still-running Gate. Multiple waiting jobs are identified separately;
  a decision cannot silently switch to another project or branch.
- The original job retains project ownership. No **Force ownership** is
  needed. An explicit ownership takeover still invalidates that job, and a
  Relay cannot bypass it. The host ComfyUI's normal access controls apply.
- The original Gate's auto-approval timeout remains in effect; the Relay
  displays its deadline but does not extend it.
- A decision wakes the original execution. Recursive loops continue there.
  An opt-in **top_level_requeue** workflow still needs its original continuation
  controller/canvas for later prompts; the Relay never queues its own canvas.
- After ComfyUI itself restarts, or a batch was saved with **Pending Review**,
  there is no live execution to wake. Use the original workflow's saved-review
  or checkpoint recovery controls. Those jobs are not listed as live gates.
- Prompts and seeds are read-only in the Relay. It selects saved takes; it
  does not copy another workflow's Plan or settings into the current canvas.
