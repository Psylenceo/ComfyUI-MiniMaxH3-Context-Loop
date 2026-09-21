"""Dedicated SelfLift wiring and offline non-overlap checks."""
import json
from pathlib import Path
from _workflow_schema_unit_test import load_schemas, validate_workflow

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "example_workflows/Ref2V Studio SelfLift Seed Hunt - EXPERIMENTAL - MiniMax H3 0.6.json"
workflow = json.loads(path.read_text())
validate_workflow(workflow, load_schemas())
nodes = {node["type"]: node for node in workflow["nodes"]}
by_id = {node["id"]: node for node in workflow["nodes"]}
links = {link[0]: link for link in workflow["links"]}


def origin(target, name):
    socket = next(s for s in nodes[target]["inputs"] if s["name"] == name)
    _, source, slot, target_id, target_slot, _ = links[socket["link"]]
    assert target_id == nodes[target]["id"]
    assert nodes[target]["inputs"][target_slot] == socket
    return by_id[source]["type"], by_id[source]["outputs"][slot]["name"]


project = "MiniMaxH3SelfLiftProject"
sampler = "MiniMaxH3SelfLiftSeedHunt"
context = "MiniMaxH3ChainContext"
current = "MiniMaxH3ChainCurrent"
assert nodes[project]["widgets_values"] == [False, "none", 5, False, 0.5, 0.0, 0.5, 1.0]
assert nodes["KSamplerSelect"]["widgets_values"] == ["euler"]
assert origin(project, "plan") == ("MiniMaxH3ChainPlanStudio", "plan")
assert origin("MiniMaxH3ChainLoopStart", "plan") == (project, "plan")
assert origin(sampler, "state") == (current, "state")
assert origin(sampler, "seed") == (current, "noise_seed")
assert origin(sampler, "model") == (context, "model")
assert origin(sampler, "latent") == (context, "latent")
assert origin(context, "drift_sigmas") == ("BasicScheduler", "SIGMAS")
assert origin(context, "audio_vae") == ("VAELoader", "VAE")
assert origin("MiniMaxH3SigmaShift", "model") == ("MiniMaxH3ChainLoRAScheduler", "model")
for target in ("MiniMaxH3ChainSegmentSave", "MiniMaxH3ChainLoopEnd"):
    assert origin(target, "sampled_latent") == (sampler, "output")
assert "BasicGuider" not in nodes and "SamplerCustomAdvanced" not in nodes
for i, a in enumerate(workflow["nodes"]):
    ax, ay = a["pos"]; aw, ah = a["size"]
    for b in workflow["nodes"][i+1:]:
        bx, by = b["pos"]; bw, bh = b["size"]
        assert not (ax < bx+bw and bx < ax+aw and ay-30 < by+bh and by-30 < ay+ah), (a["type"], b["type"])
assert "MiniMaxH3ChainSelfLiftSampler" not in nodes
assert "MiniMaxH3ChainSelfLiftSampler" in load_schemas(), "Keep the original sampler available"
for target in ("MiniMaxH3LoopTrim", "MiniMaxH3ChainSegmentSave", "MiniMaxH3ChainReview", "MiniMaxH3ChainLoopEnd"):
    assert origin(target, "state") == (sampler, "selected_state")
assert nodes[sampler]["widgets_values"] == [0, "fixed", 1, 4, "hunt_1", "taeh3.safetensors", False, True, "resume"]
assert next(s for s in nodes[sampler]["inputs"] if s["name"] == "highres_tiling")["link"] is None
assert not (ROOT / "example_workflows/Ref2V Studio SelfLift - EXPERIMENTAL - MiniMax H3 0.6.json").exists()
print("Unified SelfLift workflow: schema, opt-in switch, AV/LoRA, chosen-state wiring and layout pass")
