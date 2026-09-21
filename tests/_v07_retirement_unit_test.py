"""0.7 retirement keeps current schemas and the original Plan intact."""
import importlib
import inspect
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from workflow_schema import load_schemas
schemas = load_schemas()
package = "_h3_release_workflow_schema."
chain = importlib.import_module(package + "chain_nodes")
nodes = importlib.import_module(package + "nodes")

removed = {
    "MiniMaxH3ChainPolicy", "MiniMaxH3Legacy04PolicyAdapter",
    "MiniMaxH3ScheduledPictureReference", "MiniMaxH3ScheduledVideoReference",
    "MiniMaxH3ScheduledAudioReference", "MiniMaxH3ScheduledReferenceToVideo",
    "MiniMaxH3LazyMotionAVLoader", "MiniMaxH3ChainUpscaleMerge",
    "MiniMaxH3BoundaryAnchorPrepass", "MiniMaxH3ExtractBoundaryAnchors",
    "MiniMaxH3VisualContextLateRevealModelPatch",
}
assert not removed.intersection(schemas)
for name in removed:
    assert not hasattr(chain, name), name
for name in ("MiniMaxH3MotionContextSaveLatent", "MiniMaxH3MotionContextLoadLatent"):
    assert not hasattr(nodes, name)
assert hasattr(nodes, "MiniMaxH3MotionContext")

original = chain.MiniMaxH3ChainPlan
assert chain.CHAIN_NODE_CLASS_MAPPINGS["MiniMaxH3ChainPlan"] is original
assert issubclass(chain.MiniMaxH3ChainPlanModern, original)
assert set(original.INPUT_TYPES()["required"]) == {
    "plan_json", "run_name", "generation_fingerprint", "width", "height",
    "context_length", "encode_mode", "anchor_mode", "crop", "audio_mode",
    "audio_context_length", "default_duration_seconds", "default_steps",
    "base_seed", "segment_crf", "video_blend_frames", "continuation_mode",
}
assert "chain_policy" in original.INPUT_TYPES()["optional"]
assert "before" in original.INPUT_TYPES()["required"]["anchor_mode"][0]
for name in ("MiniMaxH3ChainPlanStudio", "MiniMaxH3ChainPreflight",
             "MiniMaxH3ChainLoopStart"):
    cls = getattr(chain, name)
    fields = cls.INPUT_TYPES()
    assert "reference_schedule" not in fields.get("optional", {})
    assert "tagged_references" in fields.get("optional", {})
    assert "reference_schedule" not in inspect.signature(getattr(cls, cls.FUNCTION)).parameters

tagged = chain.MiniMaxH3TaggedReferenceToVideo.INPUT_TYPES()
assert "references" in tagged["required"]
assert "prompt" in tagged["required"]
assert "cache_for_upscale" in tagged["optional"]
assert tagged["required"]["width"][1]["default"] == 960
for name in ("MiniMaxH3ProjectAssetManager", "MiniMaxH3ProjectAssetTree"):
    cls = getattr(chain, name)
    assert "tagged_scene_options" not in inspect.signature(cls.build).parameters
    assert "catalog_json" in cls.INPUT_TYPES()["required"]

context = schemas["MiniMaxH3ChainContext"]["input"]["optional"]
assert not {"boundary_anchors", "future_end_anchor", "visual_cond_noise_aug"} & set(context)
assert {"audio_vae", "model", "drift_sigmas", "lip_sync_voice"} <= set(context)
assert "retain_overlap_frames" not in schemas["MiniMaxH3LoopTrim"]["input"]["optional"]
assert nodes.MiniMaxH3LoopTrim.RETURN_NAMES == (
    "images", "audio", "images_with_overlap", "overlap_frames")
assert schemas["MiniMaxH3ContexMaskedTarget"]["input"]["optional"]["mask_conversion"][0] == [
    "H3 exact (causal/token max)"]
assert "source_audio" in schemas["MiniMaxH3SourceTimeline"]["input"]["optional"]
for name in (
        "MiniMaxH3ChainPlanStudio", "MiniMaxH3ChainPreflight",
        "MiniMaxH3ChainLoopStart", "MiniMaxH3ChainCurrent",
        "MiniMaxH3ChainReview", "MiniMaxH3ChainManifestLoad",
        "MiniMaxH3ChainLatentVideoAdapter", "MiniMaxH3ChainAssemble"):
    cls = getattr(chain, name)
    assert "source_audio" not in schemas[name]["input"].get("optional", {})
    assert "source_audio" not in inspect.signature(getattr(cls, cls.FUNCTION)).parameters
for name in ("_plan_with_source_audio", "_validate_source_audio_hash",
             "_plan_with_recoverable_legacy_source_audio"):
    assert not hasattr(chain, name)
assert not list((ROOT/"example_workflows/Archive").rglob("*.json"))
assert not (ROOT/"visual_context_schedule.py").exists()
print("0.7 retirement: original Plan, Tagged schemas, current imports and output slots preserved")
