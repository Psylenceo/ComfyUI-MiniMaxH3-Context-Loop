"""Durable take selection and checkpoint acknowledgements for SelfLift hunts.

Only the executing graph samples/decodes. HTTP selection updates touch the
small manifest; each finished take follows the normal Trim -> Save path.
"""
from __future__ import annotations

import copy
import uuid

BATCH_STATE = "_h3_selflift_finish_batch"
NEXT_TAKE = "_h3_selflift_next_take"
FINISHED_TAKES = "_h3_selflift_finished_takes"
# Match Review Gate's bounded finished-candidate set; hunting can still make 100 lows.
MAX_FINISHED_TAKES = 20


def selection(record, main, ordinals):
    if type(main) is not int or not isinstance(ordinals, list) or not 1 <= len(ordinals) <= 100:
        raise ValueError("Mark at least one completed take and choose its main take.")
    if any(type(value) is not int for value in ordinals):
        raise ValueError("Take numbers must be integers.")
    marked = sorted(set(ordinals) | {main})
    if len(marked) > MAX_FINISHED_TAKES:
        raise ValueError("Mark at most %d takes for one final review." % MAX_FINISHED_TAKES)
    available = {take["ordinal"]: take for take in record["candidates"]}
    if len(marked) > 100 or any(value not in available or not available[value].get("preview") for value in marked):
        raise ValueError("Only completed previews can be marked for upscale.")
    return marked


def check_edit(record, created_at=None, version=None):
    if ((created_at is not None and record.get("created_at") != created_at)
            or (version is not None and record.get("selection_version", 0) != version)):
        raise ValueError("This hunt selection changed; refresh before selecting takes.")
    if record.get("phase") == "awaiting_save":
        raise ValueError("The marked takes are finishing; resume the matching workflow before changing them.")


def set_selection(record, main, marked, *, approve=False):
    record.update(main=main, marked=marked,
                  selection_version=record.get("selection_version", 0) + 1)
    if approve:
        record.update(selected=main, selected_ordinals=marked,
                      selection_id=uuid.uuid4().hex)


def ordered_takes(record):
    main = record["selected"]
    return [n for n in record.get("selected_ordinals", [main]) if n != main] + [main]


def published_segment(store, record, ordinal, finishing_id):
    take = next(t for t in record["candidates"] if t["ordinal"] == ordinal)
    segment = take.get("published", {}).get(finishing_id or "default")
    if not isinstance(segment, dict) or segment.get("index") != record["scene"]:
        return None
    if str(segment.get("seed")) != str(take["seed"]):
        return None
    project = (store.root / "h3_chains" / record["run_name"]).resolve()
    # This is a lightweight presence check, not a replacement for normal
    # checkpoint compatibility/hash verification at resume or final review.
    names = ["segment", "checkpoint", "revision_metadata"]
    names.extend(name for name in ("generated_audio", "blend_segment", "prompt_file") if segment.get(name))
    for name in names:
        value = segment.get(name) or (segment.get("metadata") if name == "revision_metadata" else None)
        if not value:
            return None
        path = (store.root / value).resolve()
        if not path.is_relative_to(project) or not path.is_file():
            return None
    return segment


def next_take(store, record, finishing_id):
    order = ordered_takes(record)
    return next((n for n in order[:-1] if not published_segment(store, record, n, finishing_id)), order[-1])


def validate_marker(store, state, marker):
    record = store.read(marker["id"])
    plan = state["plan"]
    if (record.get("created_at") != marker.get("created_at")
            or record.get("selection_id") != marker.get("selection_id")
            or record.get("finishing_id") != marker.get("finishing_id")
            or record.get("selected") != marker.get("main")
            or ordered_takes(record) != marker.get("ordinals")
            or record.get("run_name") != plan["run_name"]
            or record.get("branch_id", "main") != plan.get("_branch_id", "main")
            or record.get("scene") != state["index"]):
        raise ValueError("This SelfLift finishing selection changed; resume the matching saved hunt.")
    return record


def validate_save(state, latent, output_root):
    marker = state.get(BATCH_STATE)
    signal = latent.get(BATCH_STATE) if isinstance(latent, dict) else None
    if not marker and not signal:
        return
    if not isinstance(marker, dict) or signal != {k: marker[k] for k in ("id", "selection_id", "ordinal")}:
        raise ValueError("Multi-take SelfLift requires selected_state wired to Trim, Segment Save, Review Gate and Loop End.")
    from .selflift_hunt_store import HuntStore
    validate_marker(HuntStore(output_root), state, marker)


def after_save(state, segment, output_root):
    marker = state.get(BATCH_STATE)
    if not isinstance(marker, dict):
        return segment
    from .selflift_hunt_store import HuntStore
    from .chain_nodes import _public_segment
    store = HuntStore(output_root)
    validate_marker(store, state, marker)
    public = _public_segment(segment)

    def saved(record):
        if record.get("selection_id") != marker["selection_id"]:
            raise ValueError("SelfLift selection changed during scene save.")
        take = next(t for t in record["candidates"] if t["ordinal"] == marker["ordinal"])
        if str(public.get("seed")) != str(take["seed"]):
            raise ValueError("The saved SelfLift take has the wrong seed.")
        take.setdefault("published", {})[marker.get("finishing_id") or "default"] = public
        complete = marker["ordinal"] == record["selected"] and all(
            published_segment(store, record, n, marker.get("finishing_id")) for n in ordered_takes(record))
        record.update(phase="finished" if complete else "awaiting_save", current=None)

    record = store.update(marker["id"], saved)
    result = dict(segment)
    if record["phase"] == "finished":
        result[FINISHED_TAKES] = [published_segment(store, record, n, marker.get("finishing_id"))
                                  for n in ordered_takes(record)]
    else:
        result[NEXT_TAKE] = marker
    return result


def continuation_state(state, marker):
    current = state.get(BATCH_STATE)
    if not isinstance(current, dict) or any(current.get(k) != marker.get(k) for k in (
            "id", "created_at", "selection_id", "finishing_id", "ordinal", "main", "ordinals")):
        raise ValueError("Connect SelfLift selected_state to Loop End before finishing multiple takes.")
    result = dict(state)
    # Restore the queued source recipe, not the just-finished alternate's seed.
    # Predecessor frames/latents/accepted scene list remain unchanged.
    result["plan"] = marker["source_plan"]
    result[BATCH_STATE] = copy.copy(marker)
    result.pop("candidate_batch", None)
    result.pop("_h3_selflift_hunt_cleanup", None)
    return result
