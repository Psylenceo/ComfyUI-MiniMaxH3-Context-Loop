"""Opt-in, targeted stage retirement through ComfyUI's model lifecycle.

Never evict execution caches or unload every model in the middle of a node.
Classic offloading moves weights into RAM, so only DynamicVRAM's managed
host-buffer release is used here. Graph-owned model objects stay reloadable.
"""
import logging

import torch
import comfy.model_management as mm

from .diagnostics import log_memory


def _protected_models(patchers):
    protected, visited = set(), set()
    pending = list(patchers)
    while pending:
        patcher = pending.pop()
        if patcher is None or id(patcher) in visited:
            continue
        visited.add(id(patcher))
        model = getattr(patcher, "model", None)
        if model is not None:
            protected.add(id(model))
        dependencies = getattr(patcher, "model_patches_models", None)
        if callable(dependencies):
            pending.extend(dependencies())
    return protected


def release_stage_models(patchers, *, keep_models=(), stage):
    """Retire only loaded, unshared DynamicVRAM targets after successful work.

    Match the underlying model, not a temporary stage clone. Fence its CUDA
    transfers and honor native cancellation BEFORE detaching anything. Errors
    propagate: this is deliberately not exception/finally cleanup.
    """
    mm.throw_exception_if_processing_interrupted()
    protected = _protected_models(keep_models)
    targets = {id(p.model) for p in patchers if getattr(p, "model", None) is not None}
    shared = len(targets & protected)
    targets -= protected
    selected, non_dynamic, seen = [], set(), set()
    for loaded in list(mm.current_loaded_models):
        patcher = loaded.model
        model = getattr(patcher, "model", None)
        if model is None or id(model) not in targets:
            continue
        seen.add(id(model))
        if not callable(getattr(patcher, "is_dynamic", None)) or not patcher.is_dynamic():
            non_dynamic.add(id(model))
            continue
        # Keep the weakly registered patcher alive through its unload.
        selected.append((loaded, patcher))

    devices = {torch.device(p.load_device) for _, p in selected}
    for device in devices:
        if device.type == "cuda":
            torch.cuda.synchronize(device)
    mm.throw_exception_if_processing_interrupted()
    log_device = next(iter(devices), torch.device("cpu"))
    log_memory(f"{stage} cleanup before", log_device, force=True)
    unloaded = 0
    for loaded, patcher in selected:
        mm.throw_exception_if_processing_interrupted()
        if loaded.model_unload():
            # LoadedModel.__eq__ compares patchers, not registry identity.
            mm.current_loaded_models[:] = [entry for entry in mm.current_loaded_models if entry is not loaded]
            unloaded += 1
    log_memory(f"{stage} cleanup after", log_device, force=True)
    logging.info("[SelfLift cleanup] %s: unloaded=%d; shared=%d; non_dynamic=%d; not_loaded=%d. "
                 "Graph-owned weights remain reloadable; no saved files or execution caches removed.",
                 stage, unloaded, shared, len(non_dynamic), len(targets - seen))
    return unloaded
