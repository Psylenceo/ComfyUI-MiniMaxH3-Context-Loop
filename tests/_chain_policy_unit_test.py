#!/usr/bin/env python3
"""The compact policy is one wire with identical resolved Plan semantics."""

import importlib.util
import json
import pathlib
import sys
import types
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = "h3_chain_policy_unit"

folder_paths = types.ModuleType("folder_paths")
folder_paths.get_output_directory = lambda: str(ROOT)
folder_paths.get_temp_directory = lambda: str(ROOT)
folder_paths.get_input_directory = lambda: str(ROOT)
folder_paths.get_annotated_filepath = lambda value: str(value)
sys.modules["folder_paths"] = folder_paths

package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package

shared_nodes = types.ModuleType(PACKAGE + ".nodes")
shared_nodes.MiniMaxH3MotionContext = object
shared_nodes._claim_inline_patch_ownership = lambda _conditioning=None: "test patch owner"
shared_nodes._prepare_native_guide_conditioning = lambda value: value
shared_nodes._resize = lambda *args: None
shared_nodes._streams_from_latent = lambda *args: None
sys.modules[shared_nodes.__name__] = shared_nodes

spec = importlib.util.spec_from_file_location(
    PACKAGE + ".chain_nodes", ROOT / "chain_nodes.py")
chain = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = chain
spec.loader.exec_module(chain)


PLAN_JSON = json.dumps({
    "shots": [
        {"id": "one", "prompt": "First scene.", "length": 73},
        {"id": "two", "prompt": "Second scene.", "length": 73},
    ],
})


def make_plan(*, combined=None, audio_context_length=22):
    return chain._normalize_plan(
        PLAN_JSON, "compact-policy-test", 64, 64, 22,
        "video", "head", "disabled", "generated_audio",
        audio_context_length,
        3.0, 8, 7, 18, "model-stack", 0, "guide",
        combined)


combined = chain._contract_chain_policy("soft_av", "source", "on", "off", False)
locked = chain._contract_chain_policy("soft_av", "source", "on", "on", True)

profile_node = chain.MiniMaxH3GenerationProfile()
profile_inputs = profile_node.INPUT_TYPES()["required"]
assert profile_node.INPUT_TYPES()["optional"]["lip_sync_options"][0] == (
    chain.LIP_SYNC_OPTIONS_TYPE)
assert tuple(profile_inputs["scene_continuity"][0]) == (
    "Visual continuity", "Independent scenes",
    "Hard picture + protected audio",
    "Hard picture + smooth audio")
assert tuple(profile_inputs["audio_profile"][0]) == (
    "Generate audio", "Generate fresh audio per scene",
    "Lip-sync to source audio", "Generate audio from source guide",
    "Use source soundtrack only", "No final audio")
assert profile_inputs["scene_continuity"][1]["display_name"] == (
    "Scene continuity")
assert profile_inputs["audio_profile"][1]["display_name"] == "Audio profile"
generated_profile, generated_profile_status = profile_node.build()
assert generated_profile["audio_policy"] == chain._contract_audio_policy(
    "generated", "off", "on")
assert generated_profile["transition_policy"] == (
    chain._contract_transition_policy("guide"))
assert generated_profile["audio_context_length"] == 22
assert "Generate audio" in generated_profile_status
lip_sync_profile, lip_sync_status = profile_node.build(
    "Hard picture + smooth audio", "Lip-sync to source audio")
assert lip_sync_profile["audio_policy"] == chain._contract_audio_policy(
    "source", "off", "off", "locked")
assert lip_sync_profile["transition_policy"] == (
    chain._contract_transition_policy("soft_av"))
assert lip_sync_profile["audio_context_length"] == 39
assert "source timeline required" in lip_sync_status
options_node = chain.MiniMaxH3LipSyncOptions()
lip_options, passthrough_voice, options_status = options_node.build()
assert passthrough_voice is None
assert lip_options["version"] == chain.LIP_SYNC_OPTIONS_VERSION
assert lip_options["preroll_seconds"] == 1.0
assert lip_options["lookahead_seconds"] == 0.2
assert "vocal stem not connected" in options_status
contextual_profile, contextual_status = profile_node.build(
    "Hard picture + smooth audio", "Lip-sync to source audio", lip_options)
assert contextual_profile["audio_policy"]["lip_sync_options"] == lip_options
assert "contextual song options active" in contextual_status
ignored_options = profile_node.build(
    "Visual continuity", "Generate audio", lip_options)[0]
assert "lip_sync_options" not in ignored_options["audio_policy"]
source_guide_profile = profile_node.build(
    "Independent scenes", "Generate audio from source guide")[0]
assert source_guide_profile["audio_policy"] == chain._contract_audio_policy(
    "generated", "on", "off")
assert source_guide_profile["audio_context_length"] == 0

advanced = chain.MiniMaxH3AdvancedPolicy()
advanced_inputs = advanced.INPUT_TYPES()["required"]
assert advanced_inputs["chain_policy"][0] == chain.CHAIN_POLICY_TYPE
assert tuple(advanced_inputs["incoming_transition"][0]) == (
    "cut", "guide", "tone_guide", "latent_guide", "detail_guide",
    "detail_av", "drift_av", "color_drift_av", "hard_av", "soft_av")
drift, drift_status = advanced.apply(combined, "drift_av")
assert drift["audio_policy"] == combined["audio_policy"]
assert drift["transition_policy"] == chain._contract_transition_policy(
    "drift_av")
assert drift["audio_context_length"] == 39
assert "advanced override" in drift_status
assert "audio preserved" in drift_status
locked_drift = advanced.apply(locked, "drift_av")[0]
assert locked_drift["audio_policy"] == locked["audio_policy"]
contextual_drift = advanced.apply(contextual_profile, "drift_av")[0]
assert contextual_drift["audio_policy"]["lip_sync_options"] == lip_options
color_drift, color_status = advanced.apply(combined, "color_drift_av")
assert color_drift["transition_policy"][
    "continuation_mode"] == "color_stable_drift_av"
assert color_drift["audio_policy"] == combined["audio_policy"]
assert "Color-Stable Drift AV" in color_status

combined_plan = make_plan(combined=combined)
assert "chain_policy" not in combined_plan["compatibility"]
contextual_plan = make_plan(combined=contextual_profile)
assert contextual_plan["compatibility"]["audio_policy"][
    "lip_sync_options"] == lip_options
context_optional = chain.MiniMaxH3ChainContext.INPUT_TYPES()["optional"]
assert list(context_optional) == [
    "audio_vae", "model", "drift_sigmas", "lip_sync_voice"]
assert context_optional["lip_sync_voice"][0] == "AUDIO"

plan_inputs = chain.MiniMaxH3ChainPlan.INPUT_TYPES()
assert plan_inputs["optional"]["chain_policy"][0] == chain.CHAIN_POLICY_TYPE
assert "audio_policy" not in plan_inputs["optional"]
assert "transition_policy" not in plan_inputs["optional"]
assert chain.CHAIN_NODE_CLASS_MAPPINGS[
    "MiniMaxH3LipSyncOptions"] is chain.MiniMaxH3LipSyncOptions
assert chain.CHAIN_NODE_CLASS_MAPPINGS[
    "MiniMaxH3GenerationProfile"] is chain.MiniMaxH3GenerationProfile
assert chain.CHAIN_NODE_CLASS_MAPPINGS[
    "MiniMaxH3AdvancedPolicy"] is chain.MiniMaxH3AdvancedPolicy
assert chain.CHAIN_NODE_DISPLAY_NAME_MAPPINGS[
    "MiniMaxH3LipSyncOptions"] == "MiniMax H3 Lip-Sync Options"
assert chain.CHAIN_NODE_DISPLAY_NAME_MAPPINGS[
    "MiniMaxH3GenerationProfile"] == "MiniMax H3 Generation Profile"
assert chain.CHAIN_NODE_DISPLAY_NAME_MAPPINGS[
    "MiniMaxH3AdvancedPolicy"] == "MiniMax H3 Advanced Policy Override"

lora_plan = chain._normalize_plan(
    json.dumps({"shots": [
        {"id": "base", "prompt": "Base scene.", "length": 73,
         "lora_route": "base"},
        {"id": "hero", "prompt": "Hero scene.", "length": 73,
         "lora_route": "A"},
        {"id": "final", "prompt": "Final style.", "length": 73,
         "lora_route": "Z"},
    ]}),
    "lora-route-test", 64, 64, 22, "video", "head", "disabled",
    "generated_audio", 22, 3.0, 8, 7, 18, "model-stack", 0,
    "guide")
assert "lora_route" not in lora_plan["shots"][0]
assert lora_plan["shots"][1]["lora_route"] == "a"
assert lora_plan["shots"][2]["lora_route"] == "z"
assert chain._effective_editor_plan(lora_plan)["shots"][1][
    "lora_route"] == "a"

# A scene's plain-language basic_prompt draft must survive round-tripping
# through _effective_editor_plan, since that is what gets embedded back into
# a saved/archived workflow's Plan JSON - dropping it there wipes out an
# unexecuted scene's basic draft the moment the user reloads that checkpoint.
basic_prompt_plan = chain._normalize_plan(
    json.dumps({"shots": [
        {"id": "with_draft", "prompt": "Formatted H3 prompt.", "length": 73,
         "basic_prompt": "A plain-language idea for later."},
        {"id": "without_draft", "prompt": "Other scene.", "length": 73},
    ]}),
    "basic-prompt-test", 64, 64, 22, "video", "head", "disabled",
    "generated_audio", 22, 3.0, 8, 7, 18, "model-stack", 0,
    "guide")
effective_basic_prompt_plan = chain._effective_editor_plan(basic_prompt_plan)
assert effective_basic_prompt_plan["shots"][0]["basic_prompt"] == (
    "A plain-language idea for later.")
assert "basic_prompt" not in effective_basic_prompt_plan["shots"][1]
assert chain._public_segment(chain._prompt_fields(basic_prompt_plan, 1))[
    "basic_prompt"] == "A plain-language idea for later."
other_basic = json.loads(json.dumps(basic_prompt_plan))
other_basic["shots"][0]["basic_prompt"] = "Different authoring draft."
assert chain._history_hash(other_basic, 1) == chain._history_hash(basic_prompt_plan, 1)
retry_basic = chain._plan_with_review_revision(
    basic_prompt_plan, 1, "Formatted H3 prompt.",
    basic_prompt_plan["shots"][0]["seed"], basic_prompt="Retry draft.")
assert retry_basic["shots"][0]["basic_prompt"] == "Retry draft."
cleared_basic = chain._plan_with_review_revision(
    retry_basic, 1, "Formatted H3 prompt.",
    basic_prompt_plan["shots"][0]["seed"], basic_prompt="")
assert "basic_prompt" not in cleared_basic["shots"][0]
assert basic_prompt_plan["shots"][0]["basic_prompt"] == "A plain-language idea for later."

assert chain._shot_lora_route(lora_plan["shots"][0]) == "base"
try:
    chain._shot_lora_route({"lora_route": "hero"})
except ValueError as exc:
    assert "base" in str(exc) and "a" in str(exc)
else:
    raise AssertionError("unknown scene LoRA route was accepted")
try:
    chain._normalize_plan(
        json.dumps({"shots": [{
            "id": "invalid", "prompt": "Invalid route.", "length": 73,
            "lora_route": "hero",
        }]}),
        "invalid-lora-route-test", 64, 64, 22, "video", "head",
        "disabled", "generated_audio", 22, 3.0, 8, 7, 18,
        "model-stack", 0, "guide")
except ValueError as exc:
    assert "Shot 1" in str(exc) and "LoRA route" in str(exc)
else:
    raise AssertionError("Plan accepted an unknown scene LoRA route")

scheduler = chain.MiniMaxH3ChainLoRAScheduler()
scheduler_inputs = scheduler.INPUT_TYPES()
assert str(scheduler_inputs["required"]["state"][0]) == (
    "H3_CHAIN_UPSCALE_STATE,H3_CHAIN_STATE")
assert scheduler_inputs["required"]["base_model"][1]["lazy"] is True
assert scheduler_inputs["optional"]["lora_a"][1]["lazy"] is True
assert scheduler_inputs["optional"]["lora_z"][1]["lazy"] is True
base_state = {"index": 1, "plan": lora_plan}
hero_state = {"index": 2, "plan": lora_plan}
final_state = {"index": 3, "plan": lora_plan}
base_model = object()
hero_model = object()
assert scheduler.check_lazy_status(base_state, None) == ["base_model"]
assert scheduler.check_lazy_status(
    hero_state, None, lora_a=None) == ["lora_a"]
assert scheduler.check_lazy_status(
    hero_state, None, lora_a=hero_model) == []
assert scheduler.check_lazy_status(
    final_state, None, lora_z=None) == ["lora_z"]
assert scheduler.select(base_state, base_model)[0] is base_model
selected, selected_status = scheduler.select(
    hero_state, None, lora_a=hero_model)
assert selected is hero_model
assert "scene 2" in selected_status and "LoRA A" in selected_status
assert scheduler.select(final_state, None, lora_z=hero_model)[0] is hero_model
try:
    scheduler.select(hero_state, None)
except ValueError as exc:
    assert "lora_a input is not connected" in str(exc)
else:
    raise AssertionError("unconnected selected LoRA route was accepted")
assert chain.CHAIN_NODE_CLASS_MAPPINGS[
    "MiniMaxH3ChainLoRAScheduler"] is chain.MiniMaxH3ChainLoRAScheduler
assert chain.CHAIN_NODE_DISPLAY_NAME_MAPPINGS[
    "MiniMaxH3ChainLoRAScheduler"] == "MiniMax H3 Scene LoRA Scheduler"

# Deferred processing selects the pinned source lane, without reading files
# or loading a MODEL. Scene indexes need not start at 1 (chapter/range input).
saved = {"scene_start": 8, "scene_end": 10, "segments": [
    {"index": 8, "lora_route": "A"},
    {"index": 9, "lora_route": "z"},
    {"index": 10},  # Older checkpoint, or the implicit Base route.
]}
with patch.object(chain, "_st_load", side_effect=AssertionError("read tensors")), \
        patch.object(chain, "_read_json", side_effect=AssertionError("read project")):
    for scope in ({}, {"upscale_range": {"scene_start": 8, "scene_end": 10}},
                  {"chapter": {"start_scene": 8, "end_scene": 10}}):
        for index, route in ((8, "a"), (9, "z"), (10, "base")):
            state = {"index": index, "source_manifest": {**saved, **scope},
                     "plan": {"shots": [{"lora_route": "d"}] * 10},
                     "segments": [{"index": index, "lora_route": "d"}]}
            input_name = "base_model" if route == "base" else "lora_" + route
            waiting = {} if route == "base" else {input_name: None}
            assert scheduler.check_lazy_status(state, None, **waiting) == [input_name]
            inputs = {"lora_a": None, "lora_z": None}
            if route != "base":
                inputs[input_name] = hero_model
            selected, status = scheduler.select(state, base_model, **inputs)
            assert selected is (base_model if route == "base" else hero_model)
            assert "scene %d" % index in status
            assert scheduler.check_lazy_status(state, base_model, **inputs) == []
    # DeRoPE-derived source keeps the resolved picture's lane, not the
    # original base take used for audio in a final-cut ALT.
    processed = {"index": 8, "source_manifest": {"segments": [{
        "index": 8, "lora_route": "z", "processing_source": {
            "stage": "derope", "original": {"lora_route": "a"}},
    }]}}
    assert scheduler.select(processed, None, lora_z=hero_model)[0] is hero_model
    bad_states = [
        ({"index": 7, "source_manifest": saved}, "between 8 and 10"),
        ({"index": 11, "source_manifest": saved}, "between 8 and 10"),
        ({"index": 8, "source_manifest": {"segments": [
            {"index": 8, "lora_route": "hero"}]}}, "LoRA route"),
        ({"index": 8, "source_manifest": saved}, "lora_a input is not connected"),
        ({"index": 8, "source_manifest": {"segments": []}}, "at least one scene"),
    ]
    for bad_state, message in bad_states:
        try:
            scheduler.select(bad_state, base_model)
        except ValueError as exc:
            assert message in str(exc), str(exc)
        else:
            raise AssertionError("Invalid processing lane was silently accepted")

print(
    "generation profiles, original Plan, and dynamic lazy scene LoRA routing: "
    "clear one-wire Plan intent, canonical compatibility, and existing-loader "
    "MODEL selection pass")
