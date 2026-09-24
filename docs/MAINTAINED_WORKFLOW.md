# Execution modes

The maintained examples use **recursive** execution by default. Top-level
requeue is an optional job-boundary mode, not a different Plan or model stack.

| Mode | Between accepted scenes | Requirements |
|---|---|---|
| `recursive` (default) | Continue inside the same top-level prompt | Normal Loop Start / Loop End wiring |
| `top_level_requeue` | Finish the prompt, then queue the next scene separately | Loop End mode plus the frontend setting below |

## Enable top-level requeue

1. Set Loop End's `execution_mode` to `top_level_requeue`.
2. Under **Settings → MiniMax H3 Context Loop → Interface → Top-level requeue**,
   enable **Auto requeue next scene as a new top-level prompt**.
3. Keep the workflow open. After the accepted scene and downstream outputs
   finish successfully, the frontend waits for safe-queue/cleanup checks,
   claims the durable handoff once, and submits the next scene.

The cleanup delay is adjustable with **Requeue cleanup interval (ms)** in the
adjacent **Top-level requeue cleanup** category. Turning off automatic requeue
does not turn a top-level Loop End into recursive execution; it leaves
continuation manual.

This boundary is **between accepted scenes only**. Candidate generation,
retries and review decisions still happen within the live prompt. Splitting
jobs can reduce between-scene executor retention; it is not a guarantee
against out-of-memory errors within one scene or a candidate batch.

## What stays the same

Prompts, seeds, the creative model stack, the Plan schema and saved checkpoint
semantics are unchanged. Handoffs live in the run's orchestration state
(`.h3/orchestration/` for new-layout runs; `orchestration/` for legacy runs).
See [storage layout](SIMPLE_CHAIN_LAYOUT.md).

The handoff is bound to the workflow, working branch and committed predecessor.
Cancelling, changing branches or disabling the setting invalidates waiting
automatic work. A stale handoff after refresh/restart is recovery history,
not permission to submit another job: inspect the saved scene and resume
manually. See [top-level recovery details](RUNS_AND_RECOVERY.md#top-level-prompt-lifecycle).

On WSL2, `--disable-pinned-memory` is a separate host-pinning workaround. It
does not enable this execution mode or fix every memory failure.
