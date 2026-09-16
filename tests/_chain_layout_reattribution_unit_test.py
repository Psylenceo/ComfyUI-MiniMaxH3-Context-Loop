"""Converted reattached chapters retain shared media after obsolete-path cleanup."""
import asyncio
import importlib
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import _checkpoint_context_reattribution_unit_test as fixture

chain = fixture.chain
layout = importlib.import_module(chain.__package__ + ".chain_layout")
conversion = importlib.import_module(chain.__package__ + ".chain_layout_conversion")
cleanup = importlib.import_module(chain.__package__ + ".obsolete_checkpoint_path")


async def check(source, _manager, originals, selected):
    before_source = fixture.h.snapshot(source)
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary)
        conversion.convert_copy(source, output)
        project = output / "h3_chains" / source.name
        state = Path(layout.state_root(project))
        cleaner = cleanup.ObsoleteCheckpointPathManager(output)
        old = originals[12]["segment"]["revision"]
        before = fixture.h.snapshot(project)
        preview = cleaner.preview(source.name, 12, old)
        assert preview["allowed"], preview["blockers"]
        assert [item["scene"] for item in preview["revisions"]] == list(range(12, 19))
        assert fixture.h.snapshot(project) == before, "preview changed converted data"
        result = cleaner.delete(source.name, 12, old, preview["snapshot"])
        assert len(result["deleted_revisions"]) == 7
        assert result["cleanup_pending"] == 0
        for scene in range(13, 19):
            assert (state / "checkpoints" / f"clip_{scene:04d}.{selected[scene]}.json").is_file()
            for key in ("checkpoint", "segment", "prompt_file"):
                assert Path(layout.output_path(output, originals[scene]["segment"][key])).is_file(), key
        after = fixture.h.snapshot(project)
        assert all(before[path] == value for path, value in after.items())
        selection = {"run_name": source.name, "output_mode": "workflow_local",
                     "output_scope": "chapter", "scope_start_scene": 8, "scope_end_scene": 18,
                     "lineage": [{"scene": i, "revision": selected[i]} for i in range(1, 19)]}
        with patch.object(chain, "_output_root", return_value=str(output)):
            manifest = chain.MiniMaxH3ChainCheckpointManager().passthrough(json.dumps(selection))[0]
        assert [item["index"] for item in manifest["segments"]] == list(range(8, 19))
        assert manifest["segments"][-1]["revision"] == selected[18]
        assert fixture.h.snapshot(project) == after, "chapter output changed converted data"
    assert fixture.h.snapshot(source) == before_source, "migration test changed its source"
    print("Converted storage: obsolete path cleanup, shared reattached media and chapter output pass")


if __name__ == "__main__":
    asyncio.run(fixture.main(check))
