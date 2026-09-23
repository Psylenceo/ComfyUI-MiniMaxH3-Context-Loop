"""Durable low-pass seed review; no work runs until this node executes."""
from __future__ import annotations

import asyncio
import copy
import logging
import threading

_ACTIVE = set()
_CLEANING = set()
_ACTIVE_LOCK = threading.Lock()
CLEANUP_STATE = "_h3_selflift_hunt_cleanup"


def source_recipe(prompt, unique_id, dynprompt=None, *, input_names=None, require_complete=False):
    """Hash relevant upstream settings, independent of virtual/subgraph node IDs.

    State/Plan are covered by the explicit scene/history contract instead of
    walking the recursive loop. UI properties and downstream nodes don't count.
    """
    from .selflift_hunt_store import digest
    prompt = prompt or {}
    if input_names is None:
        input_names = ("model", "positive", "negative", "latent", "vae", "sampler", "sigmas")
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
        if require_complete and (not data or key in seen):
            raise ValueError("SelfLift cannot identify the model_hires upstream recipe; queue the complete workflow to save/reuse finishing passes safely.")
        if key in seen:
            return digest({"class_type": data.get("class_type"), "loop": True})
        seen.add(key)
        inputs = {}
        for name, value in sorted(data.get("inputs", {}).items()):
            if name in ("state", "plan", "flow"):
                continue
            linked = isinstance(value, list) and len(value) == 2 and isinstance(value[1], int)
            if linked and (node(value[0]) or (require_complete and isinstance(value[0], str))):
                inputs[name] = {"source": visit(value[0]), "output": value[1]}
            else:
                inputs[name] = value
        seen.remove(key)
        memo[key] = digest({"class_type": data.get("class_type"), "inputs": inputs})
        return memo[key]
    own = node(unique_id)
    return {name: {"source": visit(value[0]), "output": value[1]}
            for name, value in own.get("inputs", {}).items()
            if name in input_names
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


def approve(store, key, ordinal, ordinals=None, created_at=None, version=None):
    from .selflift_selection import check_edit, selection, set_selection
    def change(record):
        check_edit(record, created_at, version)
        if record.get("upscale_request") is not None and key in _ACTIVE:
            raise ValueError("Wait for the upscale preview, then choose a take to finish.")
        if record.get("phase") == "high" and key in _ACTIVE:
            raise ValueError("The selected take is already being upscaled; wait for it to finish.")
        if record.get("review_enabled", True) is False and key in _ACTIVE:
            raise ValueError("Review gate is off for this running hunt; wait for it to finish.")
        marked = selection(record, ordinal, [ordinal] if ordinals is None else ordinals)
        for take in record["candidates"]:
            if take["ordinal"] in marked and not (store.locate(key) / take["checkpoint"]).is_file():
                raise ValueError("A marked take's middle-pass file is missing.")
        set_selection(record, ordinal, marked, approve=True)
    with _ACTIVE_LOCK:
        if key in _CLEANING:
            raise ValueError("This saved hunt is being cleaned.")
        return store.update(key, change)


def mark_takes(store, key, main, ordinals, created_at, version):
    from .selflift_selection import check_edit, selection, set_selection
    def change(record):
        check_edit(record, created_at, version)
        if key in _ACTIVE and (record.get("selected") is not None or record.get("review_enabled", True) is False):
            raise ValueError("Wait for the approved takes to finish before changing the selection.")
        set_selection(record, main, selection(record, main, ordinals))
    with _ACTIVE_LOCK:
        if key in _CLEANING:
            raise ValueError("This saved hunt is being cleaned.")
        return store.update(key, change)


def request_upscale_preview(store, key, ordinal, created_at):
    """Request only: GPU work stays on the executing hunt's serialized worker."""
    def change(record):
        if record.get("created_at") != created_at:
            raise ValueError("This saved hunt changed; refresh before previewing it.")
        if (record.get("review_enabled", True) is False or record.get("selected") is not None
                or record.get("phase") not in ("low", "preview", "waiting", "upscale_preview")):
            raise ValueError("Preview an unapproved take while its review gate is running.")
        take = next((v for v in record["candidates"] if v["ordinal"] == ordinal), None)
        if take is None or not take.get("preview"):
            raise ValueError("Choose a completed low-resolution preview first.")
        if not (store.locate(key) / take["checkpoint"]).is_file():
            raise ValueError("This take's middle-pass file is missing.")
        if record.get("upscale_request") not in (None, ordinal):
            raise ValueError("Wait for the current upscale preview to finish.")
        record["upscale_request"] = ordinal
        take.pop("upscale_error", None)
    with _ACTIVE_LOCK:
        if key not in _ACTIVE or key in _CLEANING:
            raise ValueError("Queue the matching workflow in resume mode to preview its upscale.")
        return store.update(key, change)


def clean_saved_hunt(store, key, expected=None):
    with _ACTIVE_LOCK:
        if key in _ACTIVE or key in _CLEANING:
            raise ValueError("Stop the running hunt before cleaning its saved takes.")
        if key in store._index() and store.read(key).get("phase") == "awaiting_save":
            raise ValueError("Marked takes are awaiting scene save; resume the matching workflow before cleanup.")
        _CLEANING.add(key)
    try:
        return store.remove(key, expected)
    except OSError as exc:
        # Preserve the original failure, but make it durable and visible in
        # every gate/tab. A failed delete can already have removed some scratch
        # files; do not claim the remaining batch is an intact recovery set.
        message = str(exc)
        def remember_failure(record):
            if expected and any(record.get(k) != v for k, v in expected.items()):
                return  # Never annotate a recreated/stale selection.
            record["cleanup_error"] = message
        try:
            store.update(key, remember_failure)
        except (OSError, ValueError, KeyError):
            pass  # A storage outage may also prevent writing the warning.
        raise
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
            "finishing_id": marker.get("finishing_id"),
            "selection_id": marker.get("selection_id"),
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
                       "successfully saves every marked clip/checkpoint, including the main take. "
                       "Keep off to retain low passes for later choices. "
                       "Requires selected_state connected to Segment Save. Failed runs keep recovery files.",
        })
        # Append optional controls to preserve positional values in old workflows.
        schema["optional"]["review_enabled"] = ("BOOLEAN", {
            "default": True, "label_on": "Review gate on", "label_off": "Review gate off",
            "tooltip": "On: generate candidates and wait for a choice. Off: upscale the saved chosen take, "
                       "or run only the first/input seed automatically (ignores candidate count). "
                       "Middle passes still save for recovery; no Tiny-VAE preview is required. "
                       "Applies when queued; does not change an already running hunt or the final Review Gate.",
        })
        schema["optional"]["run_mode"] = (["resume", "regenerate"], {
            "default": "resume",
            "tooltip": "Resume reuses this scene's latest saved attempt (including finished results). "
                       "Regenerate starts a fresh attempt each time this node executes, keeping older takes "
                       "and saved clips. After a failure, switch back to Resume to recover the new attempt. "
                       "Applies to each scene in the queued range; seed and sampling settings stay unchanged.",
        })
        schema["hidden"] = {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO",
                            "unique_id": "UNIQUE_ID", "dynprompt": "DYNPROMPT"}
        return schema

    RETURN_TYPES = ("LATENT", "STRING", "H3_CHAIN_STATE")
    RETURN_NAMES = ("output", "status", "selected_state")
    FUNCTION = "sample"
    CATEGORY = "sampling/minimax/context_loop"
    DESCRIPTION = ("Experimental SelfLift seed hunt: saves each low-resolution pass before tiny-VAE preview. "
        "Supports Euler or experimental RES4LYF Radau IA 2s (eta=0). "
        "Mark one or more takes and choose a main; finish their remaining high-resolution steps sequentially. "
        "Saved takes survive OOM/restart. "
        "Choose early to finish saving the current candidate, skip the rest and upscale your selection. "
        "Turn Review gate off to run one take without a pause, retaining middle-pass recovery. "
        "Keep seed fixed to resume. Wire selected_state to Segment Save and Review Gate/Loop End. "
        "Review previews require KJNodes' TAEH3 decoder and are silent and approximate, not final-quality renders.")

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")  # A durable review choice is external to widget values.

    async def sample(self, state, model, positive, vae, latent, sampler, sigmas, seed,
                     cfg=1.0, negative=None, candidate_count=4, batch_name="hunt_1",
                     tiny_vae="taeh3.safetensors", prompt=None, extra_pnginfo=None,
                     unique_id=None, dynprompt=None, auto_remove_saved_takes=False, review_enabled=True,
                     model_hires=None, run_mode="resume", highres_tiling=None):
        import folder_paths
        import torch
        import comfy.nested_tensor
        from comfy.model_management import throw_exception_if_processing_interrupted
        from .selflift_nodes import MiniMaxH3ChainSelfLiftSampler, _stage_model, upscaler_models
        from .selflift_upscalers import validate_upscaler_grid
        from .selflift_settings import canonical_settings, lift_settings
        from .selflift_state import prepare_previous_context, settings_signature, SIGNATURE
        from .selflift_hunt_store import HuntStore, digest, save_bundle, load_bundle, atomic_json
        from .selflift_preview import check_preview, save_preview
        from .selflift_selection import BATCH_STATE, ordered_takes, next_take, validate_marker

        plan = state["plan"]
        if run_mode not in ("resume", "regenerate"):
            raise ValueError("SelfLift run_mode must be resume or regenerate.")
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
            raise ValueError("Select tridae, bilinear, or an installed H3 latent upscaler on SelfLift Project.")
        controls = lift_settings(settings)
        validate_upscaler_grid(name, latent, controls["lowres_scale"])
        if not 1 <= int(candidate_count) <= 100:
            raise ValueError("SelfLift candidate count must be 1..100.")
        from .selflift_runtime.nodes import progressive_sample, _validate_sampling, _validate_hires_model
        sampler_contract = _validate_sampling(model.get_model_object("model_sampling"), sampler)
        _validate_hires_model(model, model_hires, sampler)
        from .selflift_tiling import tiling_settings
        tiling = tiling_settings(highres_tiling)
        if tiling:
            from .selflift_runtime.h3_tiling import validate_target
            samples = latent["samples"]
            streams = samples if isinstance(samples, (list, tuple)) else samples.unbind()
            validate_target(model_hires if model_hires is not None else model,
                            [tuple(s.shape) for s in streams], positive, negative)
        finishing_recipe = None
        if model_hires is not None:
            finishing_recipe = source_recipe(prompt, unique_id, dynprompt,
                input_names=("model_hires",), require_complete=True)
            if not finishing_recipe:
                raise ValueError("SelfLift Seed Hunt needs the model_hires upstream prompt recipe to safely resume a separate finishing checkpoint.")
        # Keep existing low batches/finished files byte-for-byte compatible.
        # Only explicit finishing setups get their own high-result namespace.
        finishing_id = digest({"version": 1, "recipe": finishing_recipe}) if finishing_recipe else None
        if tiling:
            finishing_id = digest({"version": 1, "recipe": finishing_recipe, "tiling": tiling})
        store = HuntStore(folder_paths.get_output_directory())
        scene = int(state["index"])
        shot = plan["shots"][scene - 1]
        recipe = source_recipe(prompt, unique_id, dynprompt)
        contract = {"version": 1, "run_name": plan["run_name"], "branch_id": plan.get("_branch_id", "main"),
            "scene": scene, "shot": shot, "width": plan.get("width"), "height": plan.get("height"),
            # Memory policy does not change a take's sampling identity. Also
            # preserve the exact settings contract of older saved hunts.
            "compatibility": plan.get("compatibility", {}),
            "settings": {k: v for k, v in canonical_settings(settings).items() if k != "cleanup_between_stages"},
            "history": [{k: s.get(k) for k in ("index", "revision", "checkpoint")}
                        for s in state.get("segments", [])],
            "recipe": recipe, "seed": str(int(seed)), "cfg": float(cfg),
            "sigmas": sigmas.detach().cpu().tolist(), "batch_name": str(batch_name)}
        if sampler_contract is not None:
            # Euler identities remain byte-for-byte compatible with old hunts.
            contract["sampler_contract"] = sampler_contract
        base_key = digest(contract)
        with _ACTIVE_LOCK:
            if base_key in _ACTIVE or base_key in _CLEANING:
                raise ValueError("This SelfLift hunt is already running.")
            _ACTIVE.add(base_key)
        key = base_key
        claimed = False
        try:
            continuing = state.get(BATCH_STATE)
            if continuing:
                if continuing.get("base_key") != base_key or continuing.get("finishing_id") != finishing_id:
                    raise ValueError("The SelfLift finishing recipe changed; resume the matching saved workflow.")
                await _work(validate_marker, store, state, continuing)
                key = continuing["id"]
            else:
                key = await _work(store.attempt_key, {"id": base_key,
                    "run_name": plan["run_name"], "branch_id": contract["branch_id"]},
                    regenerate=run_mode == "regenerate")
            with _ACTIVE_LOCK:
                if key != base_key and (key in _ACTIVE or key in _CLEANING):
                    raise ValueError("This SelfLift attempt is already running or being cleaned.")
                _ACTIVE.add(key)
                claimed = True
            logging.info("[SelfLift] scene %d: %s attempt %s", scene, run_mode, key[:12])
            record = await _work(store.create, {"id": key, "run_name": plan["run_name"],
                "branch_id": contract["branch_id"], "scene": scene, "scene_name": shot.get("id", str(scene)),
                "batch_name": str(batch_name), "base_seed": str(int(seed)), "phase": "saved",
                "low_steps": total - high, "high_steps": high, "review_enabled": bool(review_enabled)})
            # Gate preference is not part of the sampling contract: switching
            # it off must reuse a saved low pass/choice, not create a new hunt.
            if record.get("review_enabled", True) != bool(review_enabled):
                record = await _work(store.update, key, lambda r: r.update(review_enabled=bool(review_enabled)))
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
            # Keep recovery aligned with the CURRENT finishing checkpoint even
            # when the source and chosen low pass came from an earlier queue.
            await _work(atomic_json, folder / "recovery.json", {"plan": plan, "contract": contract,
                "finishing_recipe": finishing_recipe, "finishing_id": finishing_id,
                "highres_tiling": tiling,
                "prompt": recovery_prompt(prompt), "workflow": (extra_pnginfo or {}).get("workflow")})
            # A stale request left by interruption is not an approval, nor a
            # reason to repeat GPU work automatically on the next queue.
            await _work(update, finishing_id=finishing_id, highres_tiling=tiling, upscale_request=None)
            source_path = folder / "source.safetensors"
            if not source_path.is_file():
                prepared = dict(latent)
                if isinstance(prepared["samples"], (list, tuple)):
                    prepared["samples"] = comfy.nested_tensor.NestedTensor(prepared["samples"])
                prepared = prepare_previous_context(prepared, settings)
                source = {"latent": prepared, "positive": positive, "negative": positive if negative is None else negative}
                # The large tensors are written once, never by polling/UI routes.
                await _work(save_bundle, source_path, source)
                del source, prepared
            source = await _work(load_bundle, source_path)
            if source["latent"].get("noise_mask") is not None:
                from .masking_support import require_h3_mask_support
                require_h3_mask_support()
            def run(take_seed, **options):
                from .selflift_runtime.h3_upscaler import learned_latent_lift
                cleanup = bool(settings.get("cleanup_between_stages", False))
                def lift(z, hw, temporal_split=None):
                    lift_options = {"cleanup_after": True} if cleanup else {}
                    return learned_latent_lift(z, hw, name, temporal_split=temporal_split, **lift_options)
                with torch.inference_mode():
                    staged = _stage_model(model, source["latent"], sigmas)
                    staged_hires = (_stage_model(model_hires, source["latent"], sigmas, continuity_model=model)
                                    if model_hires is not None and not options.get("stop_after_low")
                                    and not options.get("stop_after_lift") else None)
                    return progressive_sample(staged, source["positive"], source["negative"], vae,
                        source["latent"], sampler, sigmas, take_seed, float(cfg), total-high,
                        controls["lowres_scale"], controls["rho"], controls["w_min"], controls["w_max"],
                        "nearest", latent_lifter=lift, model_hires=staged_hires,
                        highres_tiling=(tiling if not options.get("stop_after_low")
                                        and not options.get("stop_after_lift") else None),
                        cleanup_between_stages=cleanup, **options)

            async def preview_requested():
                # Called only between low candidates or while waiting at the
                # gate: never concurrently with low/high sampling or cleanup.
                pending = await _work(store.read, key)
                if pending.get("upscale_request") is None or pending.get("selected") is not None:
                    return
                def claim(r):
                    if r.get("upscale_request") is not None and r.get("selected") is None:
                        r.update(phase="upscale_preview", current=r["upscale_request"])
                pending = await _work(store.update, key, claim)
                ordinal = pending.get("upscale_request")
                if ordinal is None or pending.get("selected") is not None:
                    return
                notify()
                take = next(v for v in pending["candidates"] if v["ordinal"] == ordinal)
                path = store.preview_path(pending, ordinal, upscale=True)
                error = None
                try:
                    if not path.is_file():
                        middle = await _work(load_bundle, folder / take["checkpoint"])
                        lifted = await _work(run, int(take["seed"]), handoff=middle, stop_after_lift=True)
                        del middle
                        raw = int(shot["raw_frames"])
                        trim = max(0, raw - int(shot.get("delivered_frames", raw)))
                        try:
                            await _work(save_preview, lifted, path, tiny_vae, raw, trim)
                        finally:
                            del lifted
                except Exception as exc:
                    # An optional preview failure must not lose the low take or
                    # close the gate. Cancellation/Comfy interruption propagate.
                    error = str(exc)[:500]
                    logging.warning("[SelfLift] take %d upscale preview failed: %s", ordinal, error)
                def complete(r):
                    candidate = next(v for v in r["candidates"] if v["ordinal"] == ordinal)
                    if error is None:
                        candidate["upscale_preview"] = path.relative_to(store.root).as_posix()
                        candidate.pop("upscale_error", None)
                    else:
                        candidate["upscale_error"] = error
                    r.update(upscale_request=None, phase="waiting", current=None)
                await _work(store.update, key, complete)
                notify()

            # An already approved batch jumps directly to the selected high pass.
            if record.get("selected") is None:
                if review_enabled:
                    await _work(check_preview, tiny_vae)
                count = int(candidate_count) if review_enabled else 1
                for ordinal in range(1, count + 1):
                    throw_exception_if_processing_interrupted()
                    await preview_requested()
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
                    if review_enabled and not preview.is_file():
                        await _work(update, phase="preview", current=ordinal)
                        raw = int(shot["raw_frames"])
                        trim = max(0, raw - int(shot.get("delivered_frames", raw)))
                        await _work(save_preview, middle["video_prediction"], preview, tiny_vae, raw, trim)
                    del middle
                    take = {"ordinal": ordinal, "seed": str(take_seed), "checkpoint": checkpoint,
                            "preview": preview.relative_to(store.root).as_posix() if preview.is_file() else None}
                    def append(r):
                        previous = next((v for v in r["candidates"] if v["ordinal"] == ordinal), {})
                        r["candidates"] = [v for v in r["candidates"] if v["ordinal"] != ordinal] + [{**previous, **take}]
                        if not review_enabled and r.get("selected") is None:
                            r["selected"] = ordinal
                    record = await _work(store.update, key, append)
                    notify()
                    if record.get("selected") is not None:
                        break
                if record.get("selected") is None:
                    await _work(update, phase="waiting", current=None)
                    notify()
            while True:
                throw_exception_if_processing_interrupted()
                await preview_requested()
                record = await _work(store.read, key)
                if record.get("selected") is not None:
                    # Claim the choice under the same lock as approval so a
                    # second client cannot change it between read and sampling.
                    record = await _work(update, phase="high", current=None, error=None)
                    break
                await asyncio.sleep(.5)
            order = ordered_takes(record)
            multiple = len(order) > 1
            ordinal = await _work(next_take, store, record, finishing_id) if multiple else record["selected"]
            selected = next(v for v in record["candidates"] if v["ordinal"] == ordinal)
            suffix = "." + finishing_id if finishing_id else ""
            finished_path = folder / ("finished_%04d%s.safetensors" % (selected["ordinal"], suffix))
            await _work(update, phase="high", current=ordinal, error=None)
            notify()
            if finished_path.is_file():
                logging.info("[SelfLift] scene %d: reusing finished take %d; no sampling", scene, selected["ordinal"])
                output = await _work(load_bundle, finished_path)
            else:
                middle = await _work(load_bundle, folder / selected["checkpoint"])
                output = await _work(run, int(selected["seed"]), handoff=middle)
                output[SIGNATURE] = settings_signature(settings)
                await _work(save_bundle, finished_path, output)
            chosen_state = selected_state(state, int(selected["seed"]))
            chosen_state.pop(CLEANUP_STATE, None)
            if multiple:
                marker = {"id": key, "created_at": record["created_at"], "base_key": base_key,
                          "selection_id": record["selection_id"], "finishing_id": finishing_id,
                          "main": record["selected"], "ordinals": order, "ordinal": ordinal,
                          "source_plan": plan}
                chosen_state[BATCH_STATE] = marker
                output = dict(output)
                output[BATCH_STATE] = {k: marker[k] for k in ("id", "selection_id", "ordinal")}
            if auto_remove_saved_takes:
                chosen_state[CLEANUP_STATE] = {"id": key, "created_at": record["created_at"],
                    "selected": record["selected"], "scene": scene, "finishing_id": finishing_id,
                    "selection_id": record.get("selection_id")}
            await _work(update, phase="awaiting_save" if multiple else "finished")
            notify()
            status = "SelfLift take %d; seed %s; %d low + %d high steps" % (
                selected["ordinal"], selected["seed"], total-high, high)
            status += "; %s attempt %s" % (run_mode, key[:12])
            if multiple:
                status += "; %d marked takes; main take %d (finished last)" % (len(order), record["selected"])
            if model_hires is not None:
                status += "; separate finishing checkpoint"
            if tiling:
                status += "; tiled high-resolution denoising"
            if not review_enabled:
                status += "; review gate off"
            if sampler_contract is not None:
                status += "; experimental Radau IA 2s (+1 low-resolution boundary evaluation)"
            return {"ui": {"h3_selflift_hunt": [key]}, "result": (output,
                status, chosen_state)}
        except BaseException as exc:
            error = str(exc)[:500]
            try:
                if claimed:
                    await _work(store.update, key, lambda r: r.update(phase="paused", error=error))
            except Exception:
                pass
            raise
        finally:
            with _ACTIVE_LOCK:
                if claimed:
                    _ACTIVE.discard(key)
                _ACTIVE.discard(base_key)


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
        from .selflift_selection import MAX_FINISHED_TAKES
        store = HuntStore(folder_paths.get_output_directory())
        rows = await asyncio.to_thread(store.list)
        # No prompts/conditioning, media probing, or tensor reads on UI requests.
        fields = ("id", "run_name", "branch_id", "scene", "scene_name", "batch_name", "created_at",
                  "phase", "current", "selected", "candidates", "error", "low_steps", "high_steps", "review_enabled",
                  "upscale_request", "marked", "main", "selected_ordinals", "selection_version", "cleanup_error")
        batches = []
        for row in rows:
            public = dict({k: row.get(k) for k in fields}, active=row["id"] in _ACTIVE,
                          max_marked=MAX_FINISHED_TAKES)
            public["candidates"] = [{**{k: v for k, v in take.items() if k != "published"},
                "saved": bool(take.get("published", {}).get(row.get("finishing_id") or "default"))}
                for take in row["candidates"]]
            batches.append(public)
        return web.json_response({"batches": batches}, headers={"Cache-Control": "no-store"})

    async def choose(request):
        try:
            body = await request.json()
            store = HuntStore(folder_paths.get_output_directory())
            ordinal = body.get("ordinal")
            if type(ordinal) is not int:
                raise ValueError("Choose a take number.")
            await asyncio.to_thread(approve, store, str(body.get("id", "")), ordinal,
                                   body.get("ordinals"), body.get("created_at"), body.get("selection_version"))
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

    async def mark(request):
        try:
            body = await request.json()
            if "created_at" not in body or type(body.get("selection_version")) is not int:
                raise ValueError("Refresh the saved hunt before marking takes.")
            await asyncio.to_thread(mark_takes, HuntStore(folder_paths.get_output_directory()),
                                   str(body.get("id", "")), body.get("main"), body.get("ordinals"),
                                   body["created_at"], body["selection_version"])
            return web.json_response({"ok": True})
        except (ValueError, KeyError, FileNotFoundError) as exc:
            return web.json_response({"error": str(exc)}, status=400)

    async def upscale_preview(request):
        try:
            body = await request.json()
            if type(body.get("ordinal")) is not int or "created_at" not in body:
                raise ValueError("Choose a saved take to preview.")
            await asyncio.to_thread(request_upscale_preview, HuntStore(folder_paths.get_output_directory()),
                str(body.get("id", "")), body["ordinal"], body["created_at"])
            return web.json_response({"ok": True})
        except (OSError, ValueError, KeyError) as exc:
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
    routes.post("/h3/selflift/selection")(mark)
    routes.post("/h3/selflift/upscale-preview")(upscale_preview)
    routes.get("/h3/selflift/workflow")(workflow)
    routes.post("/h3/selflift/clean")(clean)
