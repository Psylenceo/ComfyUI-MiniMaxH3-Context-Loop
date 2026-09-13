# Basic scene prompts

A scene's optional **Basic prompt** is a plain-language drafting field, separate
from its H3-formatted scene prompt. Editing it does not change generation text,
seeds or checkpoint dependency hashes. The Rich Scene Prompt Editor's explicit
**Optimize** action uses it to produce the H3 prompt.

The field is available in Plan, Modern Plan, Plan Studio, both dedicated prompt
editors and Review Gate (when prompt editing is enabled). Linked editors
synchronize both fields, including clearing a draft, without replacing the
other editor's newer text. Original/ALT controls are unchanged.

Basic drafts are preserved in saved Plans, checkpoint prompt metadata and
prompt history. Executed history keeps its original draft: a different basic
draft against the same executed H3 text creates a new revision. An unexecuted
draft remains editable. Review retries carry the draft into the revised Plan.

Existing Plans without this optional field still work. No migration, project
scan or new polling loop is required. Text already lost by an older version
cannot be reconstructed automatically; recover it from a saved Plan or history
revision that still contains it.

Adapted from Psylenceo's basic-prompt work and checkpoint/history fixes in
[PR #56](https://github.com/ethanfel/ComfyUI-MiniMaxH3-Context-Loop/pull/56).
The PR's Review Gate routing changes are not included.
