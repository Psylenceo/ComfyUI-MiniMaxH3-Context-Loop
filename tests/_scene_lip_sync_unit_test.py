#!/usr/bin/env python3
"""Scene-local dialogue: real carousel files, isolated CPU-only projects."""
import copy
import json
import pathlib
import tempfile
import types

import torch

from _project_asset_manager_unit_test import ACTIVE, chain, folder_paths


def audio(value, seconds=10, rate=24000, channels=1):
    return {"waveform": torch.full((1, channels, round(seconds * rate)), value),
            "sample_rate": rate}


def plan(selection=None):
    policy = chain._contract_compose_chain_policy(
        chain._contract_audio_policy("generated", "off", "off", "off"),
        chain._contract_transition_policy("cut"), audio_context_length=0)
    shots = [{"id": name, "prompt": "A person speaks.", "length": 73}
             for name in ("before", "dialogue", "after")]
    if selection:
        shots[1].update(source_audio_target="locked", lip_sync_source=selection)
    return chain._normalize_plan(json.dumps({"shots": shots}),
        "dialogue_test", 64, 64, 1, "video", "head", "disabled",
        "generated_audio", 0, 1., 8, 7, 18, "model-stack", 0, "guide", policy)


with tempfile.TemporaryDirectory(prefix="h3-scene-dialogue-") as temporary:
    ACTIVE["root"] = temporary
    root = pathlib.Path(temporary)
    chain.PromptHistoryStore = lambda _root: types.SimpleNamespace(
        mark_executed=lambda *_args, **_kwargs: None)
    root.joinpath("input").mkdir()
    dialogue_path = root / "input" / "dialogue.wav"
    waveform = audio(.1, seconds=3)
    waveform["waveform"][..., :24000] = .4
    chain._write_wav(waveform, str(dialogue_path))
    store = chain.ProjectAssetStore(folder_paths.get_input_directory(), folder_paths.get_output_directory())
    imported = store.import_file("dialogue_test", dialogue_path, role="audio_reference", tag="dialogue")
    asset = imported["asset"]
    # Explicit selection is independent of availability to prompt tags.
    store.update("dialogue_test", asset["id"], {"enabled": False})
    selection = {"asset_id": asset["id"], "start_seconds": 1., "final_audio": "mix"}
    p = plan(selection)
    old = plan()
    assert not chain._audio_policy_requires_source(p)
    assert chain._audio_source_requirements(p) == []
    assert chain._effective_editor_plan(p)["shots"][1]["lip_sync_source"] == selection
    for index in (1, 3):
        assert chain._scene_dependency_record(p, index) == chain._scene_dependency_record(old, index)
    assert chain._canonical_source_reference_dependency(p, 1) is None
    dep = chain._canonical_source_reference_dependency(p, 2)
    assert dep["route"] == "scene_audio_asset"
    s = {"plan": p, "index": 2, "segments": []}
    result = chain.MiniMaxH3ChainCurrent().current(s)["result"]
    state = result[0]
    assert result[12] is None, "Locked dialogue must not leak into Ref2VA reference audio"
    target = state["current_source_audio_target"]
    start = round(state["current_source_audio_target_clip_start_seconds"] * 24000)
    assert abs(float(target["waveform"][..., start:start + 24000].mean()) - .1) < 1e-4
    assert torch.count_nonzero(target["waveform"][..., start + 48000:]) == 0, "Short dialogue pads with silence"
    next_state = chain.MiniMaxH3ChainCurrent().current({**state, "index": 3})["result"][0]
    assert "current_source_audio_target" not in next_state
    assert "current_lip_sync_source_asset" not in next_state
    # Global timeline remains the source for other locked scenes.
    global_timeline = chain._make_source_timeline(source_audio=audio(.25))
    p["shots"][0]["source_audio_target"] = "locked"
    assert [r["scene"] for r in chain._audio_source_requirements(p)] == [1]
    global_plan, global_timeline = chain._plan_with_source_timeline(p, global_timeline)
    global_state = chain.MiniMaxH3ChainCurrent().current(
        {**s, "plan": global_plan, "index": 1, "source_timeline": global_timeline})["result"][0]
    assert abs(float(global_state["current_source_audio_target"]["waveform"].mean()) - .25) < 1e-6
    # AV overlap is before the file offset, never a shift of delivered dialogue.
    av_shot = {**p["shots"][1], "raw_frames": 78, "delivered_frames": 73,
               "lip_sync_source": {**selection, "start_seconds": 0}}
    target, clip_start, descriptor = chain._scene_lip_sync_target(p, av_shot)
    delivered_start = round((clip_start + 5 / 24) * 24000)
    assert torch.count_nonzero(target["waveform"][..., :delivered_start]) == 0
    assert abs(float(target["waveform"][..., delivered_start:delivered_start + 1000].mean()) - .4) < 1e-4
    # Assembly mixes only the selected scene; generated policy does not double it.
    segments = [{"index": i, "id": shot["id"], "delivered_frames": 73}
                for i, shot in enumerate(p["shots"], 1)]
    segments[1].update(lip_sync_source=selection, lip_sync_source_asset=descriptor)
    manifest = {"run_name": p["run_name"], "segments": segments,
                "total_delivered_frames": 219}
    base = audio(.2, 219 / 24, rate=48000, channels=2)
    mixed = chain._audio_with_scene_lip_sync(base, manifest, "source")["waveform"]
    left, right = 73 * 2000, 146 * 2000
    assert torch.equal(mixed[..., :left], base["waveform"][..., :left])
    assert torch.equal(mixed[..., right:], base["waveform"][..., right:])
    assert abs(float(mixed[..., left:left + 48000].mean()) - .3) < 1e-4
    assert chain._audio_with_scene_lip_sync(base, {"segments": []}, "source") is base
    assert chain._audio_with_scene_lip_sync(None, manifest, "none") is None
    generated = chain._audio_with_scene_lip_sync(base, manifest, "generated")["waveform"]
    assert abs(float(generated[..., left:left + 48000].mean()) - .1) < 1e-4
    segments[1]["lip_sync_source"] = {**selection, "final_audio": "replace"}
    replaced = chain._audio_with_scene_lip_sync(base, manifest, "source")["waveform"]
    assert torch.equal(replaced, generated)
    # Trims, slip and gaps apply after dialogue insertion, just like the picture.
    records = [{"kind": "scene", "scene": 1, "frame_count": 12,
                "source_start_frame": 0, "source_frame_count": 73, "start_frame": 0},
               {"kind": "scene", "scene": 2, "frame_count": 24,
                "source_start_frame": 73 + 12, "source_frame_count": 73,
                "start_frame": 36},
               {"kind": "scene", "scene": 3, "frame_count": 12,
                "source_start_frame": 146, "source_frame_count": 73, "start_frame": 60}]
    edited = chain._audio_with_editorial_timeline(
        {"waveform": mixed, "sample_rate": 48000}, records, 219, 72, "test")
    assert torch.count_nonzero(edited["waveform"][..., 24000:72000]) == 0
    assert abs(float(edited["waveform"][..., 72000:120000].mean()) - .3) < 1e-4
    public = chain._public_segment(segments[1])
    assert public["lip_sync_source_asset"] == descriptor
    # Disabled scene mode must neither read nor apply a stale source selection.
    disabled = plan({"asset_id": "missing", "start_seconds": 0})
    disabled["shots"][1]["source_audio_target"] = "off"
    assert chain._canonical_source_reference_dependency(disabled, 2) is None
    for bad in ({"asset_id": "missing"}, {**selection, "start_seconds": 10}):
        try:
            chain._scene_lip_sync_target(plan(bad), plan(bad)["shots"][1])
        except (FileNotFoundError, ValueError):
            pass
        else:
            raise AssertionError("Missing/out-of-range dialogue was accepted")

print("Scene lip-sync: routing, persistence, overlap, padding, isolated mixing and editorial timing pass")
