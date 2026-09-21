# Manual Tagged workflows

These examples use explicit reference-loader nodes instead of Project Asset
Carousel. They remain supported; they are not required for the recommended
[Basic / Carousel starting points](../README.md#start-here-basic-or-carousel).

| Workflow | Use it for |
|---|---|
| [Ref2V Tagged](<Ref2V Tagged - MiniMax H3 0.6.json>) | Picture loaders with prompt-selected `@tag` references. |
| [Ref2V Tagged Source Audio](<Ref2V Tagged Source Audio - MiniMax H3 0.6.json>) | Separate picture and audio loaders, with Source Timeline already wired. Select the full soundtrack in Load Audio. |
| [Sequential Motion — Experimental](<Ref2V Sequential Motion - EXPERIMENTAL - MiniMax H3 0.6.json>) | Explicit sequential video and paired-audio reference nodes. |

Setup and wiring notes are under [guides/](guides/). Shared example media stays
in [../assets/](../assets/); copy it to `ComfyUI/input/` where requested.
Moving these examples does not change their workflow IDs, runtime behavior,
or existing saved user workflows.
