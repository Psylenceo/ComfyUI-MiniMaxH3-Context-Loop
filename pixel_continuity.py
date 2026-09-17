"""Opt-in, file-backed HQ-tail protection for continuous-shot pixel upscales.

Selections are workflow-local and revision-bound. No generation checkpoint or
project is rewritten. All large intermediates stay on disk; masks are views.
"""
from fractions import Fraction
import importlib


def original_segment(segment):
    while True:
        # DeRoPE preserves the original scene contract. A picture-only ALT is
        # different footage and must NOT inherit a base take's continuity mark.
        source = segment.get("processing_source") or {}
        if not isinstance(source.get("original"), dict):
            return segment
        segment = source["original"]


def apply_selection(manifest):
    """Attach only marks for this exact adjacent source pair, after ALT resolution."""
    marks = manifest.get("pixel_continuity") or []
    segments = manifest.get("segments") or []
    for segment in segments:
        segment.pop("pixel_continuity", None)
    for previous, current in zip(segments, segments[1:]):
        before, after = original_segment(previous), original_segment(current)
        if any(isinstance(mark, dict) and mark.get("scene") == current["index"]
               and mark.get("revision") == after.get("revision")
               and mark.get("previous_revision") == before.get("revision") for mark in marks):
            current["pixel_continuity"] = {
                "previous_revision": previous["revision"],
                "previous_checkpoint_sha256": previous.get("checkpoint_sha256"),
                "version": 1,
            }
    return manifest


def continuity_window(source, previous):
    """Resolve an actual saved immediate-tail window, not an arbitrary reference."""
    current, prior = original_segment(source), original_segment(previous)
    count = int(source["raw_frames"]) - int(source["delivered_frames"])
    visual = int(current.get("resolved_context_length", current.get("context_length", 0)))
    if count <= 0 or visual != count:
        raise ValueError("Continuous-shot upscale needs a visual overlap matching the RAW trim. "
                         "Leave hard cuts/reference-only scenes unmarked.")
    end = int(prior["delivered_frames"])
    blocks = current.get("visual_context_blocks")
    if blocks:
        if len(blocks) != 1:
            raise ValueError("Continuous-shot upscale needs one previous-shot tail, not multiple visual references.")
        block = blocks[0]
        scene = block.get("source_scene")
        revision = block.get("source_revision")
        start = int(block.get("resolved_start_frame", -1))
        if int(block.get("frames", 0)) != count:
            raise ValueError("Continuous-shot upscale context length changed.")
    else:
        scene = current.get("visual_context_source_scene", int(source["index"]) - 1)
        revision = current.get("visual_context_source_revision", current.get("predecessor_revision"))
        end = int(current.get("visual_context_source_editorial_out_frames",
                              current.get("predecessor_editorial_out_frames", end)))
        start = int(current.get("visual_context_start_frame", end - count))
        if current.get("visual_context_lead_frames"):
            raise ValueError("Continuous-shot upscale does not combine separate lead references.")
    if (int(scene or 0) != int(previous["index"]) or revision != prior.get("revision")
            or start != int(prior["delivered_frames"]) - count or start < 0):
        raise ValueError("This scene's saved context is not the selected previous scene's ending. "
                         "Leave it unmarked to use independent USDU.")
    return start, count


def _video_io():
    # The same bounded VIDEO transport used by the example's DLSS/USDU nodes;
    # no CAT refinement is involved. Resolve by registration, not install path.
    import nodes
    cls = nodes.NODE_CLASS_MAPPINGS.get("CATH3UpscaleVideoCurrent")
    if cls is None:
        raise RuntimeError("Pixel continuity VIDEO requires ComfyUI-ContextAnchoredTile-videopath "
                           "for file-backed transport (not CAT refinement).")
    return importlib.import_module(cls.__module__.rsplit(".", 1)[0] + ".video_io")


def _checked_frames(io, path, count, width, height):
    origin, seen = None, 0
    for image, timestamp in io.frames(path):
        if origin is None:
            origin = timestamp
        if (seen >= count or tuple(image.shape[1:3]) != (height, width)
                or abs(timestamp - origin - Fraction(seen, 24)) > Fraction(1, 1000)):
            raise ValueError("Continuity VIDEO must keep the exact scene frame count, size and 24 fps clock.")
        yield image, timestamp
        seen += 1
    if seen != count:
        raise ValueError("Continuity VIDEO frame count does not match the saved scene.")


class MiniMaxH3PixelContinuityPrepare:
    EXPERIMENTAL = True
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"state": ("H3_CHAIN_UPSCALE_STATE",), "video": ("VIDEO",)}}

    RETURN_TYPES = ("VIDEO", "MASK", "BOOLEAN", "H3_PIXEL_CONTINUITY", "STRING")
    RETURN_NAMES = ("video", "mask", "anchor_context", "continuity", "status")
    FUNCTION = "prepare"
    CATEGORY = "conditioning/minimax/context_loop/upscale"
    DESCRIPTION = ("After DLSS, carry the previous saved HQ tail only for scenes explicitly marked "
                   "Continue previous shot in Checkpoint Manager. Unmarked scenes pass through.")

    def prepare(self, state, video):
        import torch
        from . import upscale_nodes as upscale
        from . import chain_nodes as chain
        source = upscale._source_segment(state)
        mark = source.get("pixel_continuity")
        if not mark:
            # USDU's optional mask stays absent, exactly as in the old graph.
            # No media decode, mask allocation or disk I/O for hard cuts.
            return video, None, False, None, "Independent shot — unchanged USDU path"
        index = int(source["index"])
        if index == upscale._source_bounds(state["source_manifest"])[0]:
            raise ValueError("Include the previous scene in the source selection to protect this continuous shot.")
        previous = upscale._source_segment(state, index - 1)
        _, count = continuity_window(source, previous)
        saved = next((item for item in reversed(state.get("segments") or [])
                      if int(item["index"]) == index - 1), None)
        if not saved:
            raise ValueError("Continuous scene %d needs the previous HQ scene in this profile. "
                             "Start one scene earlier, or resume a profile that already contains it." % index)
        # The adapter already verified saved per-scene contracts on resume;
        # loop-produced segments are freshly saved. Check the immediate binding
        # here without rescanning/checksumming all artifacts a second time.
        if (saved.get("source_revision") != previous.get("revision")
                or saved.get("source_checkpoint_sha256") != previous.get("checkpoint_sha256")
                or saved.get("delivered_frames") != previous.get("delivered_frames")):
            raise ValueError("Previous HQ scene belongs to a different source revision or timing.")
        io = _video_io()
        path = io.source_path(video)
        info = io.probe(path)
        if info["rate"] != 24:
            raise ValueError("Pixel continuity expects 24 fps RAW H3 video.")
        width, height = info["width"], info["height"]
        raw = int(source["raw_frames"])
        prior_path = chain._absolute_output_path(saved["segment"])
        prior_count = int(previous["delivered_frames"])
        # Stream past earlier frames. Retain neither the full clip nor the tail
        # as tensors; the generator supplies one matching frame at a time.
        prior_frames = _checked_frames(io, prior_path, prior_count, width, height)
        for _ in range(prior_count - count):
            next(prior_frames)
        bias = torch.zeros(3)
        samples = min(5, count)
        try:
            with io.output_video(info["rate"], source=path, time_base=info["time_base"]) as (writer, result):
                for frame, (image, timestamp) in enumerate(_checked_frames(io, path, raw, width, height)):
                    if frame < count:
                        carried, _ = next(prior_frames)
                        if frame >= count - samples:
                            bias += (carried - image).mean(dim=(0, 1, 2)) / samples
                        image = carried
                        if frame == count - 1:
                            # Force final validation, including unexpected extra frames.
                            if next(prior_frames, None) is not None:
                                raise ValueError("Previous HQ video contains extra frames.")
                    writer.write(image, timestamp)
        finally:
            prior_frames.close()
        mask = torch.cat((torch.zeros(count), torch.ones(raw - count)))
        mask = mask[:, None, None].expand(raw, height, width)
        context = {"prepared": str(result), "reference": str(path), "frames": raw,
                   "prefix": count, "width": width, "height": height, "bias": bias.tolist()}
        return (io.from_path(result), mask, True, context,
                "Continuous shot — %d previous HQ frames protected; RAW timing unchanged" % count)


class MiniMaxH3PixelContinuityFinish:
    EXPERIMENTAL = True
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"video": ("VIDEO",), "continuity": ("H3_PIXEL_CONTINUITY",),
                             "tone_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05}),
                             "fade_frames": ("INT", {"default": 39, "min": 1, "max": 240})}}

    RETURN_TYPES = ("VIDEO", "STRING", "STRING")
    RETURN_NAMES = ("video", "video_path", "status")
    FUNCTION = "finish"
    CATEGORY = MiniMaxH3PixelContinuityPrepare.CATEGORY
    DESCRIPTION = ("After USDU, restore the protected head exactly and optionally ease the new-shot "
                   "RGB tone bias toward the prior HQ tail. No frame blending, retiming or audio change. "
                   "Unmarked shots pass through untouched. Experimental; 0 disables tone correction.")

    def finish(self, video, continuity, tone_strength=1.0, fade_frames=39):
        io = _video_io()
        path = io.source_path(video)
        if not continuity:
            return video, str(path), "Independent shot — unchanged USDU output"
        import torch
        c = continuity
        count, raw = c["prefix"], c["frames"]
        def frames(source):
            return _checked_frames(io, source, raw, c["width"], c["height"])
        correction = torch.zeros(3)
        if tone_strength > 0:
            # Same-frame comparison removes source lighting/motion changes from
            # the estimate. Bounded RGB offset, not matching adjacent pictures.
            samples = min(5, raw - count)
            reference, refined = frames(c["reference"]), frames(path)
            try:
                for frame in range(count + samples):
                    before, _ = next(reference)
                    after, _ = next(refined)
                    if frame >= count:
                        correction += (after - before).mean(dim=(0, 1, 2)) / samples
            finally:
                reference.close()
                refined.close()
            correction = (torch.tensor(c["bias"]) - correction).clamp(-0.03, 0.03) * float(tone_strength)
        prepared = frames(c["prepared"])
        info = io.probe(path)
        try:
            with io.output_video(info["rate"], source=path, time_base=info["time_base"]) as (writer, result):
                for frame, (image, timestamp) in enumerate(frames(path)):
                    if frame < count:
                        image, _ = next(prepared)
                    elif tone_strength > 0:
                        weight = max(0.0, 1 - (frame - count) / max(1, int(fade_frames) - 1))
                        weight = weight * weight * (3 - 2 * weight)
                        image = (image + correction * weight).clamp(0, 1)
                    writer.write(image, timestamp)
        finally:
            prepared.close()
        return io.from_path(result), str(result), "Protected head restored; RGB correction %s" % correction.tolist()


NODE_CLASS_MAPPINGS = {cls.__name__: cls for cls in (
    MiniMaxH3PixelContinuityPrepare, MiniMaxH3PixelContinuityFinish)}
NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3PixelContinuityPrepare": "MiniMax H3 Pixel Continuity • Protect Tail",
    "MiniMaxH3PixelContinuityFinish": "MiniMax H3 Pixel Continuity • Finish",
}
