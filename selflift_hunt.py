"""Durable low-pass seed review; no work runs until this node executes."""
from __future__ import annotations

import asyncio
import copy
import threading

_ACTIVE = set()
_CLEANING = set()
_ACTIVE_LOCK = threading.Lock()
CLEANUP_STATE = "_h3_selflift_hunt_cleanup"


def source_recipe(prompt, unique_id, dynprompt=None):
    """Hash relevant upstream settings, independent of virtual/subgraph node IDs.

    State/Plan are covered by the explicit scene/history contract instead of
    walking the recursive loop. UI properties and downstream nodes don't count.
    """
    from .selflift_hunt_store import digest
    prompt = prompt or {}
    def node(key):
        try:
            return dynprompt.get_node(str(key)) if dynprompt is not None else prompt.get(str(key), {})
        except KeyError:
            return {}
    seen, memo = set(), {}
    def visit(key):
        key = str(key)
        if key in memo:
            return memo[key]
        data = node(key)
        if key in seen:
            return digest({"class_type": data.get("class_type"), "loop": True})
        seen.add(key)
        inputs = {}
        for name, value in sorted(data.get("inputs", {}).items()):
            if name in ("state", "plan", "flow"):
                continue
            if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int) and node(value[0]):
                inputs[name] = {"source": visit(value[0]), "output": value[1]}
            else:
                inputs[name] = value
        seen.remove(key)
        memo[key] = digest({"class_type": data.get("class_type"), "inputs": inputs})
        return memo[key]
    own = node(unique_id)
    return {name: {"source": visit(value[0]), "output": value[1]}
            for name, value in own.get("inputs", {}).items()
            if name in ("model", "positive", "negative", "latent", "vae", "sampler", "sigmas")
            and isinstance(value, list) and len(value) == 2}


def recovery_prompt(prompt):
    """Copy the API prompt without Comfy's execution-only cache fingerprints.

    Comfy adds node['is_changed'] during execution; our own IS_CHANGED returns
    NaN to invalidate cached results. Those markers are neither JSON data nor
    generation inputs. Do not mutate the live prompt or sanitize real inputs.
    """
    if prompt is None:
        return None
    return copy.deepcopy({node_id: {k: v for k, v in node.items() if k != "is_changed"}
                          for node_id, node in prompt.items()})


async def _work(function, *args, **kwargs):
    # Never leave a detached GPU worker behind if the owning execution cancels.
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        finally:
            raise


def selected_state(state, seed):
    scene = int(state["index"])
    plan = state["plan"]
    shot = plan["shots"][scene - 1]
    result = dict(state)
    if int(shot["seed"]) != int(seed):
        from .chain_nodes import _plan_with_review_revision
        result["plan"] = _plan_with_review_revision(plan, scene,
            shot.get("scene_prompt_template", shot["scene_prompt"]), int(seed))
    return result


def approve(store, key, ordinal):
    def change(record):
        if record.get("phase") == "high" and key in _ACTIVE:
            raise ValueError("The selected take is already being upscaled; wait for it to finish.")
        take = next((v for v in record["candidates"] if v["ordinal"] == ordinal), None)
        if take is None or not take.get("preview"):
            raise ValueError("Choose a completed preview.")
        if not (store.locate(key) / take["checkpoint"]).is_file():
            raise ValueError("This take's middle-pass file is missing.")
        record["selected"] = ordinal
    with _ACTIVE_LOCK:
        if key in _CLEANING:
            raise ValueError("This saved hunt is being cleaned.")
        return store.update(key, change)


def clean_saved_hunt(store, key, expected=None):
    with _ACTIVE_LOCK:
        if key in _ACTIVE or key in _CLEANING:
            raise ValueError("Stop the running hunt before cleaning its saved takes.")
        _CLEANING.add(key)
    try:
        return store.remove(key, expected)
    finally:
        with _ACTIVE_LOCK:
            _CLEANING.discard(key)


def cleanup_after_segment_save(state, output_root, logger):
    """Called only after Segment Save commits a durable normal checkpoint.

    A decode/save OOM leaves the full hunt intact. A stale state from another
    scene, selection or recreated batch cannot delete a newer saved hunt.
    Cleanup failure is advisory: never fail an already saved scene.
    """
    marker = state.get(CLEANUP_STATE)
    if not isinstance(marker, dict) or marker.get("scene") != state.get("index"):
        return None
    from .selflift_hunt_store import HuntStore
    plan = state["plan"]
    try:
        return clean_saved_hunt(HuntStore(output_root), marker["id"], {
            "created_at": marker["created_at"], "selected": marker["selected"], "phase": "finished",
            "run_name": plan["run_name"], "branch_id": plan.get("_branch_id", "main"),
            "scene": state["index"],
        })
    except (OSError, ValueError, KeyError) as exc:
        logger.warning("H3 SelfLift scene saved; temporary hunt cleanup skipped: %s", exc)
        return None


class MiniMaxH3SelfLiftSeedHunt:
    @classmethod
    def INPUT_TYPES(cls):
        from .selflift_nodes import MiniMaxH3ChainSelfLiftSampler
        from .selflift_preview import tiny_models
        schema = copy.deepcopy(MiniMaxH3ChainSelfLiftSampler.INPUT_TYPES())
        schema["required"].update({
            "candidate_count": ("INT", {"default": 4, "min": 1, "max": 100}),
            "batch_name": ("STRING", {"default": "hunt_1", "tooltip":
                "Keep this and the seed unchanged to resume saved takes. Change it for a fresh hunt."}),
            "tiny_vae": (tiny_models(),),
        })
        schema.setdefault("optional", {})["auto_remove_saved_takes"] = ("BOOLEAN", {
            "default": False, "label_on": "Clean after scene save", "label_off": "Keep saved takes",
            "tooltip": "Delete this hunt's temporary latents and previews only after Segment Save "
                       "successfully saves the chosen clip/checkpoint. Keep off to upscale multiple takes. "
                       "Requires selected_state connected to Segment Save. Failed runs keep recovery files.",
        })
        schema["hidden"] = {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO",
                            "unique_id": "UNIQUE_ID", "dynprompt": "DYNPROMPT"}
        return schema

    RETURN_TYPES = ("LATENT", "STRING", "H3_CHAIN_STATE")
    RETURN_NAMES = ("output", "status", "selected_state")
    FUNCTION = "sample"
    CATEGORY = "sampling/minimax/context_loop"
    DESCRIPTION = ("Experimental SelfLift seed hunt: saves each low-resolution pass before tiny-VAE preview. "
        "Choose a take to run only its remaining high-resolution steps. Saved takes survive OOM/restart. "
        "Choose early to finish saving the current candidate, skip the rest and upscale your selection. "
        "Keep seed fixed to resume. Wire selected_state to Segment Save and Review Gate/Loop End. "
        "Requires KJNodes' TAEH3 decoder; previews are silent and approximate, not final-quality renders.")

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")  # A durable review choice is external to widget values.

    async def sample(self, state, model, positive, vae, latent, sampler, sigmas, seed,
                     cfg=1.0, negative=None, candidate_count=4, batch_name="hunt_1",
                     tiny_vae="taeh3.safetensors", prompt=None, extra_pnginfo=None,
                     unique_id=None, dynprompt=None, auto_remove_saved_takes=False):
        import folder_paths
        import torch
        import comfy.nested_tensor
        from comfy.model_management import throw_exception_if_processing_interrupted
        from .selflift_nodes import MiniMaxH3ChainSelfLiftSampler, _stage_model, upscaler_models
        from .selflift_state import prepare_previous_context, settings_signature, SIGNATURE
        from .selflift_hunt_store import HuntStore, digest, save_bundle, load_bundle, atomic_json
        from .selflift_preview import check_preview, save_preview

        plan = state["plan"]
        settings = plan.get("selflift_sampling", {})
        if not settings.get("enabled"):
            def ordinary():
                with torch.inference_mode():
                    return MiniMaxH3ChainSelfLiftSampler().sample(
                        state, model, positive, vae, latent, sampler, sigmas, seed, cfg, negative)
            output, status = await _work(ordinary)
            return output, status, state
        total = int(sigmas.numel()) - 1
        high = int(settings.get("high_resolution_steps", 2))
        if not 1 <= high < total:
            raise ValueError("SelfLift Seed Hunt needs at least one low and one high step.")
        name = str(settings.get("upscaler_model", "none"))
        if name == "none" or name not in upscaler_models():
            raise ValueError("Select an installed H3 latent upscaler on SelfLift Project.")
        if not 1 <= int(candidate_count) <= 100:
            raise ValueError("SelfLift candidate count must be 1..100.")
        from .selflift_runtime.nodes import progressive_sample, _validate_sampling
        _validate_sampling(model.get_model_object("model_sampling"), sampler)
        store = HuntStore(folder_paths.get_output_directory())
        scene = int(state["index"])
        shot = plan["shots"][scene - 1]
        recipe = source_recipe(prompt, unique_id, dynprompt)
        contract = {"version": 1, "run_name": plan["run_name"], "branch_id": plan.get("_branch_id", "main"),
            "scene": scene, "shot": shot, "width": plan.get("width"), "height": plan.get("height"),
            "compatibility": plan.get("compatibility", {}), "settings": settings,
            "history": [{k: s.get(k) for k in ("index", "revision", "checkpoint")}
                        for s in state.get("segments", [])],
            "recipe": recipe, "seed": str(int(seed)), "cfg": float(cfg),
            "sigmas": sigmas.detach().cpu().tolist(), "batch_name": str(batch_name)}
        key = digest(contract)
        with _ACTIVE_LOCK:
            if key in _ACTIVE or key in _CLEANING:
                raise ValueError("This SelfLift hunt is already running.")
            _ACTIVE.add(key)
        try:
            record = await _work(store.create, {"id": key, "run_name": plan["run_name"],
                "branch_id": contract["branch_id"], "scene": scene, "scene_name": shot.get("id", str(scene)),
                "batch_name": str(batch_name), "base_seed": str(int(seed)), "phase": "saved",
                "low_steps": total - high, "high_steps": high})
            folder = store.locate(key)
            def update(**values):
                return store.update(key, lambda r: r.update(values))
            def notify():
                try:
                    from server import PromptServer
                    PromptServer.instance.send_sync("h3-selflift-hunt", {"id": key, "node": str(unique_id)})
                except (ImportError, AttributeError):
                    pass
            notify()
            source_path = folder / "source.safetensors"
            if not source_path.is_file():
                prepared = dict(latent)
                if isinstance(prepared["samples"], (list, tuple)):
                    prepared["samples"] = comfy.nested_tensor.NestedTensor(prepared["samples"])
                prepared = prepare_previous_context(prepared, settings)
                source = {"latent": prepared, "positive": positive, "negative": positive if negative is None else negative}
                # The large tensors are written once, never by polling/UI routes.
                await _work(atomic_json, folder / "recovery.json", {"plan": plan, "contract": contract,
                    "prompt": recovery_prompt(prompt), "workflow": (extra_pnginfo or {}).get("workflow")})
                await _work(save_bundle, source_path, source)
                del source, prepared
            source = await _work(load_bundle, source_path)
            if source["latent"].get("noise_mask") is not None:
                from .masking_support import require_h3_mask_support
                require_h3_mask_support()
            def run(take_seed, **options):
                from .selflift_runtime.h3_upscaler import learned_latent_lift
                def lift(z, hw, temporal_split=None):
                    return learned_latent_lift(z, hw, name, temporal_split=temporal_split)
                with torch.inference_mode():
                    staged = _stage_model(model, source["latent"], sigmas)
                    return progressive_sample(staged, source["positive"], source["negative"], vae,
                        source["latent"], sampler, sigmas, take_seed, float(cfg), total-high,
                        .5, 0., .5, 1., "nearest", latent_lifter=lift, **options)

            # An already approved batch jumps directly to the selected high pass.
            if record.get("selected") is None:
                await _work(check_preview, tiny_vae)
                for ordinal in range(1, int(candidate_count) + 1):
                    throw_exception_if_processing_interrupted()
                    # Check approval and claim the next candidate under the same
                    # lock. A choice made after this claim waits for this candidate
                    # to be saved; a choice made before it starts no further work.
                    def begin_candidate(r):
                        if r.get("selected") is None:
                            r.update(phase="low", current=ordinal, error=None)
                    record = await _work(store.update, key, begin_candidate)
                    if record.get("selected") is not None:
                        break
                    notify()
                    take_seed = (int(seed) + ordinal - 1) % (1 << 64)
                    checkpoint = "take_%04d.safetensors" % ordinal
                    path = folder / checkpoint
                    if not path.is_file():
                        middle = await _work(run, take_seed, stop_after_low=True)
                        # Durable BEFORE preview, lifter, or any high-resolution work.
                        await _work(save_bundle, path, middle)
                    else:
                        middle = await _work(load_bundle, path)
                    preview = store.preview_path(record, ordinal)
                    if not preview.is_file():
                        await _work(update, phase="preview", current=ordinal)
                        raw = int(shot["raw_frames"])
                        trim = max(0, raw - int(shot.get("delivered_frames", raw)))
                        await _work(save_preview, middle["video_prediction"], preview, tiny_vae, raw, trim)
                    del middle
                    take = {"ordinal": ordinal, "seed": str(take_seed), "checkpoint": checkpoint,
                            "preview": preview.relative_to(store.root).as_posix()}
                    def append(r):
                        r["candidates"] = [v for v in r["candidates"] if v["ordinal"] != ordinal] + [take]
                    record = await _work(store.update, key, append)
                    notify()
                    if record.get("selected") is not None:
                        break
                await _work(update, phase="waiting", current=None)
                notify()
            while True:
                throw_exception_if_processing_interrupted()
                record = await _work(store.read, key)
                if record.get("selected") is not None:
                    # Claim the choice under the same lock as approval so a
                    # second client cannot change it between read and sampling.
                    record = await _work(update, phase="high", current=None, error=None)
                    break
                await asyncio.sleep(.5)
            selected = next(v for v in record["candidates"] if v["ordinal"] == record["selected"])
            finished_path = folder / ("finished_%04d.safetensors" % selected["ordinal"])
            await _work(update, phase="high", error=None)
            notify()
            if finished_path.is_file():
                output = await _work(load_bundle, finished_path)
            else:
                middle = await _work(load_bundle, folder / selected["checkpoint"])
                output = await _work(run, int(selected["seed"]), handoff=middle)
                output[SIGNATURE] = settings_signature(settings)
                await _work(save_bundle, finished_path, output)
            chosen_state = selected_state(state, int(selected["seed"]))
            chosen_state.pop(CLEANUP_STATE, None)
            if auto_remove_saved_takes:
                chosen_state[CLEANUP_STATE] = {"id": key, "created_at": record["created_at"],
                    "selected": selected["ordinal"], "scene": scene}
            await _work(update, phase="finished")
            notify()
            return {"ui": {"h3_selflift_hunt": [key]}, "result": (output,
                "SelfLift take %d; seed %s; %d low + %d high steps" %
                (selected["ordinal"], selected["seed"], total-high, high), chosen_state)}
        except BaseException as exc:
            try:
                await _work(store.update, key, lambda r: r.update(phase="paused", error=str(exc)[:500]))
            except Exception:
                pass
            raise
        finally:
            with _ACTIVE_LOCK:
                _ACTIVE.discard(key)


def register_routes():
    try:
        from server import PromptServer
        from aiohttp import web
        import folder_paths
        from .selflift_hunt_store import HuntStore
        routes = PromptServer.instance.routes
    except (ImportError, AttributeError):
        return

    async def listing(request):
        store = HuntStore(folder_paths.get_output_directory())
        rows = await asyncio.to_thread(store.list)
        # No prompts/conditioning, media probing, or tensor reads on UI requests.
        fields = ("id", "run_name", "branch_id", "scene", "scene_name", "batch_name", "created_at",
                  "phase", "current", "selected", "candidates", "error", "low_steps", "high_steps")
        return web.json_response({"batches": [dict({k: row.get(k) for k in fields},
            active=row["id"] in _ACTIVE) for row in rows]}, headers={"Cache-Control": "no-store"})

    async def choose(request):
        try:
            body = await request.json()
            store = HuntStore(folder_paths.get_output_directory())
            ordinal = body.get("ordinal")
            if type(ordinal) is not int:
                raise ValueError("Choose a take number.")
            await asyncio.to_thread(approve, store, str(body.get("id", "")), ordinal)
            return web.json_response({"ok": True})
        except (ValueError, KeyError, FileNotFoundError) as exc:
            return web.json_response({"error": str(exc)}, status=400)

    async def workflow(request):
        import json
        try:
            store = HuntStore(folder_paths.get_output_directory())
            path = store.locate(request.query.get("id", "")) / "recovery.json"
            data = await asyncio.to_thread(lambda: json.loads(path.read_text(encoding="utf-8")))
            if not data.get("workflow"):
                raise ValueError("No canvas snapshot saved; queue the original workflow with its saved settings.")
            return web.json_response(data["workflow"], headers={
                "Content-Disposition": 'attachment; filename="SelfLift-hunt-recovery.json"'})
        except (ValueError, FileNotFoundError) as exc:
            return web.json_response({"error": str(exc)}, status=400)

    async def clean(request):
        try:
            body = await request.json()
            if body.get("confirm") is not True or "created_at" not in body:
                raise ValueError("Confirm cleanup of the selected saved hunt.")
            result = await asyncio.to_thread(clean_saved_hunt,
                HuntStore(folder_paths.get_output_directory()), str(body.get("id", "")),
                {"created_at": body["created_at"]})
            return web.json_response({"ok": True, **result})
        except (OSError, ValueError, KeyError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
    routes.get("/h3/selflift/hunts")(listing)
    routes.post("/h3/selflift/choose")(choose)
    routes.get("/h3/selflift/workflow")(workflow)
    routes.post("/h3/selflift/clean")(clean)
