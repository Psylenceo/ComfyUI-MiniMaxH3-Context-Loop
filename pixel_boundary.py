"""Optional post-assembly USDU join experiment. Never rewrites saved HQ clips.

Only a small pre-USDU head/tail is retained per marked boundary. The export
streams RGB16 frames and replaces the editable middle exactly, without fades.
"""
from contextlib import ExitStack, closing
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from .pixel_continuity import _checked_frames, _video_io, continuity_window

LEFT_ANCHOR, RIGHT_ANCHOR = 22, 17
CATEGORY = "conditioning/minimax/context_loop/upscale"


def _contract(options):
    return {key: options[key] for key in ("version", "frames_per_side", "denoise", "steps")}


def _cache_path(state, source, role, options):
    from . import upscale_nodes as upscale
    contract = [upscale._upscale_source_contract(source),
                state["profile_config"]["config_hash"], _contract(options)]
    key = hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()[:24]
    root = Path(upscale._profile_dir(state["run_name"], state["profile"], state["source_manifest"]))
    return root / "boundary_sources" / ("scene_%04d_%s_%s.mkv" % (source["index"], key, role))


def _check_recipe(state, options):
    recorded = state["profile_config"].get("recipe", {}).get("boundary_refinement")
    if recorded != _contract(options):
        raise ValueError("Connect Boundary Experiment recipe_json to the Upscale Adapter. "
                         "Enable the switch before upscaling into a new profile.")


def _pairs(source):
    for previous, current in zip(source["segments"], source["segments"][1:]):
        mark = current.get("pixel_continuity") or {}
        if (mark and mark.get("previous_revision") == previous.get("revision")
                and mark.get("previous_checkpoint_sha256") == previous.get("checkpoint_sha256")):
            continuity_window(current, previous)
            yield previous, current


def _jobs(source, records, n):
    """Use the delivered editorial clock, never generation or raw offsets."""
    pairs = {(int(a["index"]), int(b["index"])): (a, b) for a, b in _pairs(source)}
    jobs = []
    skipped = len(pairs)
    for before, after in zip(records, records[1:]):
        if before["kind"] != "scene" or after["kind"] != "scene":
            continue
        pair = pairs.get((int(before["scene"]), int(after["scene"])))
        if pair is None:
            continue
        # Trimming either side of the actual generated join destroys its
        # correspondence with the cached DLSS samples. Hard cuts/gaps stay put.
        if (before["source_in_frame"] + before["frame_count"] != before["source_frame_count"]
                or after["source_in_frame"] != 0
                or before["frame_count"] < n + LEFT_ANCHOR
                or after["frame_count"] < n + RIGHT_ANCHOR):
            continue
        join = int(after["start_frame"])
        if before["start_frame"] + before["frame_count"] != join:
            continue
        start, end = join - n - LEFT_ANCHOR, join + n + RIGHT_ANCHOR
        # Keep every refinement window independent of all other replacements.
        if jobs and start < jobs[-1]["end"]:
            continue
        jobs.append(dict(previous=pair[0], current=pair[1], join=join, start=start, end=end))
        skipped -= 1
    return jobs, skipped


def _records(manifest):
    from . import upscale_nodes as upscale
    from . import chain_nodes as chain
    assembly = upscale._assembly_manifest(manifest, manifest["segments"])
    _, records, count = chain._editorial_timeline_records(
        assembly["run_name"], assembly["segments"], chain._manifest_editorial(assembly))
    return records, count


def _windows(io, baseline, info, count, jobs, caches, n):
    """HQ anchors around pre-USDU centers, with ONE forward baseline reader.

    Do not restart decoding the whole film for every join in a long chapter.
    Sampling is sequential, so only one window and its caches are open at once.
    """
    width, height = info["width"], info["height"]
    with closing(_checked_frames(io, baseline, count, width, height)) as source:
        cursor = 0
        for job, (tail, head) in zip(jobs, caches):
            for _ in range(cursor, job["start"]):
                next(source)
            with ExitStack() as stack:
                a = stack.enter_context(closing(_checked_frames(io, tail, n, width, height)))
                b = stack.enter_context(closing(_checked_frames(io, head, n, width, height)))
                writer, result = stack.enter_context(io.output_video(Fraction(24)))
                for frame in range(job["start"], job["end"]):
                    image, _ = next(source)
                    if job["join"] - n <= frame < job["join"]:
                        image, _ = next(a)
                    elif job["join"] <= frame < job["join"] + n:
                        image, _ = next(b)
                    writer.write(image)
                if next(a, None) is not None or next(b, None) is not None:
                    raise ValueError("Boundary source cache contains extra frames.")
            cursor = job["end"]
            yield job, result


def _refine(io, window, job, manifest, options, model, clip, video_vae,
            prompt_override, tagged_references):
    import nodes
    import torch
    import comfy.samplers
    from comfy_extras.nodes_custom_sampler import BasicScheduler, Guider_Basic
    from . import upscale_nodes as upscale
    cls = nodes.NODE_CLASS_MAPPINGS.get("UltimateSDUpscaleNoUpscaleGuiderVideo")
    if cls is None:
        raise RuntimeError("Boundary refinement requires the H3 USDU Guider VIDEO node.")
    info = io.probe(window)
    width, height = info["width"], info["height"]
    state = {**manifest, "index": int(job["current"]["index"])}
    positive, _, _, _ = upscale.MiniMaxH3ChainUpscaleReferenceConditioning().condition(
        state, clip, video_vae=video_vae, _target_size=(width, height),
        prompt_override=prompt_override, tagged_references=tagged_references)
    geometry = upscale._source_geometry(job["current"], manifest["source_manifest"].get("compatibility") or {})
    positive = upscale._sync_h3_conditioning(
        positive, width / geometry["width"], height / geometry["height"], "bilinear", "conditioning_policy")
    # Scene keyframe positions refer to its whole RAW clock, not this two-scene
    # window. The protected HQ anchors are the boundary pass's visual keyframes.
    positive = [[value, {k: v for k, v in metadata.items() if k != "minimax_keyframes"}]
                for value, metadata in positive]
    guider = Guider_Basic(model)
    guider.set_conds(positive)
    sigmas = BasicScheduler().get_sigmas(model, "beta", options["steps"], options["denoise"])[0]
    n = options["frames_per_side"]
    count = LEFT_ANCHOR + 2 * n + RIGHT_ANCHOR
    mask = torch.cat((torch.zeros(LEFT_ANCHOR), torch.ones(2*n), torch.zeros(RIGHT_ANCHOR)))
    mask = mask[:, None, None].expand(count, height, width)
    result = cls().refine(
        io.from_path(window), canvas_storage="disk", canvas_directory=options["canvas_directory"],
        guider=guider, sampler=comfy.samplers.sampler_object("er_sde"), sigmas=sigmas, vae=video_vae,
        seed=int(job["current"].get("seed", 0)), mode_type="Linear", tile_width=512, tile_height=288,
        mask_blur=8, tile_padding=64, seam_fix_mode="None", seam_fix_denoise=0.15,
        seam_fix_width=32, seam_fix_mask_blur=4, seam_fix_padding=16,
        tile_overlap_mode="Reprocess Overlap", tiled_decode=False, batch_size=1,
        anchor_context=True, mask=mask)
    return io.source_path(result[0])


def _splice(io, baseline, info, count, patches, n):
    """One lossless output pass, original audio stream copied, no crossfade."""
    width, height = info["width"], info["height"]
    with ExitStack() as stack:
        source = stack.enter_context(closing(_checked_frames(io, baseline, count, width, height)))
        writer, result = stack.enter_context(io.output_video(
            info["rate"], source=baseline, time_base=info["time_base"]))
        pending = iter(patches)
        patch = next(pending, None)
        frames = None
        for index, (image, timestamp) in enumerate(source):
            if patch and index == patch[0]["join"] - n:
                frames = stack.enter_context(closing(_checked_frames(
                    io, patch[1], LEFT_ANCHOR + 2*n + RIGHT_ANCHOR, width, height)))
                for _ in range(LEFT_ANCHOR):
                    next(frames)
            if frames is not None:
                image, _ = next(frames)
                if index == patch[0]["join"] + n - 1:
                    # Read/validate the unused right anchor as well.
                    for _ in frames:
                        pass
                    frames = None
                    patch = next(pending, None)
            writer.write(image, timestamp)
    return result


class MiniMaxH3PixelBoundarySettings:
    EXPERIMENTAL = True
    CATEGORY = CATEGORY
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "enabled": ("BOOLEAN", {"default": False, "label_on": "Boundary experiment ON", "label_off": "Boundary experiment OFF"}),
            "frames_per_side": ("INT", {"default": 17, "min": 17, "max": 51, "step": 17}),
            "denoise": ("FLOAT", {"default": 0.2, "min": 0.01, "max": 1.0, "step": 0.01}),
            "steps": ("INT", {"default": 3, "min": 1, "max": 100}),
            "canvas_directory": ("STRING", {"default": ""}),
            "base_recipe_json": ("STRING", {"default": "{}", "multiline": True})}}
    RETURN_TYPES = ("H3_PIXEL_BOUNDARY_OPTIONS", "STRING")
    RETURN_NAMES = ("options", "recipe_json")
    FUNCTION = "settings"
    DESCRIPTION = ("OFF keeps the existing tail-protection workflow. ON retains small DLSS samples and "
                   "refines only marked joins in a separate final export. Enable before upscaling into a NEW profile. "
                   "Experimental: may improve the join but introduce artifacts at the replacement edges.")

    def settings(self, enabled=False, frames_per_side=17, denoise=0.2, steps=3,
                 canvas_directory="", base_recipe_json="{}"):
        if frames_per_side not in (17, 34, 51):
            raise ValueError("Boundary frames_per_side must be 17, 34 or 51 (H3 temporal alignment).")
        options = dict(enabled=bool(enabled), version=1, frames_per_side=frames_per_side,
                       denoise=denoise, steps=steps, canvas_directory=canvas_directory)
        if not enabled:
            return options, base_recipe_json
        recipe = json.loads(base_recipe_json or "{}")
        recipe["boundary_refinement"] = _contract(options)
        return options, json.dumps(recipe, sort_keys=True)


class MiniMaxH3PixelBoundaryCapture:
    EXPERIMENTAL = True
    CATEGORY = CATEGORY
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"state": ("H3_CHAIN_UPSCALE_STATE",), "video": ("VIDEO",),
                             "options": ("H3_PIXEL_BOUNDARY_OPTIONS",)}}
    RETURN_TYPES = ("VIDEO", "STRING")
    RETURN_NAMES = ("video", "status")
    FUNCTION = "capture"
    DESCRIPTION = "Between DLSS and Protect Tail: retain only pre-USDU boundary samples when the experiment is ON. OFF does no I/O."

    def capture(self, state, video, options):
        if not options["enabled"]:
            return video, "Boundary experiment OFF"
        source = next(item for item in state["source_manifest"]["segments"] if item["index"] == state["index"])
        roles = set()
        for previous, current in _pairs(state["source_manifest"]):
            if previous["index"] == source["index"]:
                roles.add("tail")
            if current["index"] == source["index"]:
                roles.add("head")
        if not roles:
            return video, "No marked boundary — no samples retained"
        _check_recipe(state, options)
        io = _video_io()
        path = io.source_path(video)
        info = io.probe(path)
        n = options["frames_per_side"]
        raw, delivered = int(source["raw_frames"]), int(source["delivered_frames"])
        if delivered < n:
            return video, "Scene too short for boundary refinement"
        with ExitStack() as stack:
            outputs = {}
            for role in sorted(roles):
                writer, result = stack.enter_context(io.output_video(Fraction(24)))
                outputs[role] = writer, result
            for frame, (image, _) in enumerate(_checked_frames(io, path, raw, info["width"], info["height"])):
                for role, (writer, _) in outputs.items():
                    start = raw - n if role == "tail" else raw - delivered
                    if start <= frame < start + n:
                        writer.write(image)
        for role, (_, result) in outputs.items():
            target = _cache_path(state, source, role, options)
            target.parent.mkdir(parents=True, exist_ok=True)
            # Same-filesystem atomic publication; never expose interrupted data.
            temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                shutil.copyfile(result, temporary)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
                Path(result).unlink(missing_ok=True)
        return video, "Retained %df pre-USDU %s for boundary experiment" % (n, "/".join(sorted(roles)))


class MiniMaxH3PixelBoundaryExport:
    EXPERIMENTAL = True
    CATEGORY = CATEGORY
    OUTPUT_NODE = True
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "video_path": ("STRING", {"forceInput": True}), "manifest": ("H3_CHAIN_MANIFEST",),
            "options": ("H3_PIXEL_BOUNDARY_OPTIONS",),
            "model": ("MODEL", {"lazy": True}), "clip": ("CLIP", {"lazy": True}),
            "video_vae": ("VAE", {"lazy": True})}, "optional": {
            "prompt_override": ("STRING", {"forceInput": True}),
            "tagged_references": ("H3_TAGGED_REFERENCES",)}}
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("video_path", "status")
    FUNCTION = "export"
    DESCRIPTION = ("After Chain Assemble, refine each eligible marked join together using HQ outer anchors "
                   "and the retained pre-USDU center. Writes a separate lossless MKV, preserving all other frames "
                   "and the assembled audio. Trims/gaps that remove a join are skipped. OFF passes through.")

    def check_lazy_status(self, video_path, manifest, options, model=None, clip=None, video_vae=None, **kwargs):
        if options["enabled"]:
            jobs, _ = _jobs(manifest["source_manifest"], _records(manifest)[0], options["frames_per_side"])
            if jobs:
                return [name for name, value in (("model", model), ("clip", clip), ("video_vae", video_vae)) if value is None]
        return []

    def export(self, video_path, manifest, options, model=None, clip=None, video_vae=None,
               prompt_override="", tagged_references=None):
        def result(path, status):
            return {"ui": {"text": [status, str(path)]}, "result": (str(path), status)}
        if not options["enabled"]:
            return result(video_path, "Boundary experiment OFF — original assembly")
        records, count = _records(manifest)
        n = options["frames_per_side"]
        jobs, skipped = _jobs(manifest["source_manifest"], records, n)
        if not jobs:
            return result(video_path, "No eligible marked joins; original assembly (%d skipped)" % skipped)
        _check_recipe(manifest, options)
        caches = []
        for job in jobs:
            paths = [_cache_path(manifest, job[key], role, options)
                     for key, role in (("previous", "tail"), ("current", "head"))]
            if not all(path.is_file() for path in paths):
                raise ValueError("Missing pre-USDU samples for scenes %d/%d. Enable Boundary Experiment "
                                 "before upscaling this pair into a new profile." %
                                 (job["previous"]["index"], job["current"]["index"]))
            caches.append(paths)
        io = _video_io()
        info = io.probe(video_path)
        if info["rate"] != 24 or info["width"] % 32 or info["height"] % 32:
            raise ValueError("Boundary export requires 24 fps and H3-aligned dimensions.")
        # Scratch windows disappear on success, cancellation or failure. Disk
        # sampling remains bounded by one window, not the whole assembled film.
        parent = Path(video_path).resolve().parent
        with tempfile.TemporaryDirectory(prefix="h3_boundary_", dir=parent) as scratch:
            patches = []
            with closing(_windows(io, video_path, info, count, jobs, caches, n)) as windows:
                for number, (job, window) in enumerate(windows):
                    try:
                        refined = _refine(io, window, job, manifest, options, model, clip, video_vae,
                                          prompt_override, tagged_references)
                        patch = Path(scratch) / ("join_%04d.mkv" % number)
                        shutil.move(str(refined), patch)
                        patches.append((job, patch))
                    finally:
                        Path(window).unlink(missing_ok=True)
            output = _splice(io, video_path, info, count, patches, n)
            target = parent / (Path(video_path).stem + "_boundary_" + uuid.uuid4().hex[:8] + ".mkv")
            shutil.move(str(output), target)
        status = "Boundary experiment: %d join(s), %df per side; %d skipped. Separate lossless export; saved clips unchanged." % (len(jobs), n, skipped)
        return result(target, status)


NODE_CLASS_MAPPINGS = {cls.__name__: cls for cls in (
    MiniMaxH3PixelBoundarySettings, MiniMaxH3PixelBoundaryCapture, MiniMaxH3PixelBoundaryExport)}
NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3PixelBoundarySettings": "MiniMax H3 Pixel Continuity • Boundary Experiment",
    "MiniMaxH3PixelBoundaryCapture": "MiniMax H3 Pixel Continuity • Keep Boundary Sources",
    "MiniMaxH3PixelBoundaryExport": "MiniMax H3 Pixel Continuity • Experimental Boundary Export",
}
