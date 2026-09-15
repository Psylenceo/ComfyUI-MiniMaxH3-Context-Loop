#!/usr/bin/env python3
"""Real tiny checkpoints, FFmpeg exports and pixel-upscale loop; CPU only."""
import copy
import json
from pathlib import Path
import tempfile

from _upscale_chain_unit_test import audio_for_frames, av_latent, folder_paths, load_package, torch


def main():
    _, chain, upscale = load_package()
    with tempfile.TemporaryDirectory(prefix="h3-dialogue-integration-") as temporary:
        folder_paths.output_directory = str(Path(temporary) / "output")
        folder_paths.input_directory = str(Path(temporary) / "input")
        Path(folder_paths.input_directory).mkdir()
        chain.PromptHistoryStore = lambda _root: type("History", (), {
            "mark_executed": lambda *_args, **_kwargs: None})()
        dialogue = audio_for_frames(24, 48000)
        dialogue["waveform"].fill_(.1)
        path = str(Path(folder_paths.input_directory) / "dialogue.wav")
        chain._write_wav(dialogue, path)
        imported = chain._project_asset_store().import_file(
            "scene_dialogue_test", path, role="audio_reference", tag="dialogue")
        selection = {"asset_id": imported["asset"]["id"], "start_seconds": 0, "final_audio": "mix"}
        shots = [{"id": f"scene_{i}", "prompt": "A quiet room.", "length": 5}
                 for i in range(1, 4)]
        shots[1].update(source_audio_target="locked", lip_sync_source=selection)
        policy = chain._contract_compose_chain_policy(
            chain._contract_audio_policy("source", "off", "off", "off"),
            chain._contract_transition_policy("cut"), audio_context_length=0)
        plan = chain._normalize_plan(json.dumps({"shots": shots}),
            "scene_dialogue_test", 32, 32, 1, "video", "head", "disabled",
            "generated_audio", 0, 5/24, 2, 7, 18, "test", 0, "guide", policy)
        backing = audio_for_frames(15, 48000)
        backing["waveform"].fill_(.2)
        plan, timeline = chain._plan_with_source_timeline(
            plan, chain._make_source_timeline(source_audio=backing))
        _, report = chain._preflight_chain(plan, source_timeline=timeline)
        assert report["ok"], report["errors"]
        lineage = []
        for i in range(1, 4):
            state = chain._initial_state(plan, i)
            state["source_timeline"] = timeline
            state = chain.MiniMaxH3ChainCurrent().current(state)["result"][0]
            saved = chain.MiniMaxH3ChainSegmentSave().save(
                state, torch.zeros(5, 32, 32, 3), av_latent(),
                audio_for_frames(5, 48000), denoised_latent=av_latent())["result"][0]
            lineage.append({"scene": i, "revision": saved["revision"]})
            if i == 2:
                metadata = chain._read_json(chain._absolute_output_path(saved["metadata"]))["segment"]
                assert metadata["lip_sync_source"] == selection
                assert metadata["lip_sync_source_asset"]["asset_id"] == selection["asset_id"]
                assert chain._checkpoint_plan_revision(metadata)["lip_sync_source"] == selection
        source = chain.MiniMaxH3ChainCheckpointManager().passthrough({
            "run_name": plan["run_name"], "lineage": lineage})[0]
        original = copy.deepcopy(source)
        expected = chain._full_chain_selected_audio(source, "source", None)
        assert torch.allclose(expected["waveform"][..., :10000], torch.full((1, 2, 10000), .2))
        assert abs(float(expected["waveform"][..., 10000:20000].mean()) - .3) < 1e-4
        result = chain.MiniMaxH3ChainAssemble().assemble(source, "source", "base", 192)
        assert Path(result["result"][0]).is_file()
        # Exercise a fresh pixel child, with audio recovered from its source snapshot.
        _, child, _, _ = upscale.MiniMaxH3ChainUpscaleAdapter().adapt(
            source, "dialogue_pixel", "pixel", "{}", 1, 0, False, 18)
        saved_children = []
        for i in range(1, 4):
            child["index"] = i
            saved_child = upscale.MiniMaxH3ChainUpscaleSegmentSave().save(
                child, torch.zeros(5, 64, 64, 3))["result"][0]
            saved_children.append(saved_child)
            child["segments"] = list(saved_children)
        child_manifest = upscale._upscale_manifest(child, saved_children, True)
        assembly = upscale._assembly_manifest(child_manifest, saved_children)
        assert assembly["segments"][1]["lip_sync_source"] == selection
        recovered = chain._full_chain_selected_audio(assembly, "source", None)
        assert torch.equal(recovered["waveform"], expected["waveform"])
        result = chain.MiniMaxH3ChainAssemble().assemble(child_manifest, "source", "upscaled", 192)
        assert Path(result["result"][0]).is_file()
        assert source == original, "Upscaling must not mutate source metadata"
    print("Scene dialogue integration: preflight, real saves/recovery, base and pixel upscale exports pass")


if __name__ == "__main__":
    main()
