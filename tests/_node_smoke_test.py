"""Smoke test: run MiniMaxH3MotionContext.apply() end to end with fakes.

Fakes ComfyUI's modules and tensor ops (numpy-backed) and drives the node
exactly as a graph would: a 124-frame clip at 480x864, 22 context frames,
audio from the previous clip's LATENT. Starts with Ref2VA image/video refs and
checks that they survive ahead of the appended Motion Context audio ref, plus
the keyframe count and indices, audio step count, and fractional end_frame
carrying the grid-overhang compensation.
"""

import sys
import types

import numpy as np

import os
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PKG_DIR = os.path.dirname(_TESTS_DIR)  # repo root, where the package lives
sys.path.insert(0, _TESTS_DIR)
from _mock_harness import make_mm, make_torch  # noqa: E402


class T:
    """Minimal numpy-backed tensor stand-in."""

    def __init__(self, a):
        self.a = np.asarray(a)

    @property
    def shape(self):
        return self.a.shape

    @property
    def ndim(self):
        return self.a.ndim

    def __getitem__(self, idx):
        return T(self.a[idx])

    def movedim(self, src, dst):
        return T(np.moveaxis(self.a, src, dst))

    def unsqueeze(self, d):
        return T(np.expand_dims(self.a, d))

    def clone(self):
        return T(self.a.copy())

    def cpu(self):
        return self

    def contiguous(self):
        return T(np.ascontiguousarray(self.a))


class Nested:
    def __init__(self, parts):
        self.parts = parts

    def unbind(self):
        return list(self.parts)


def main():
    # fake modules the package imports
    mm = make_mm()
    for name in ("comfy", "comfy.ldm", "comfy.ldm.minimax"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["comfy.ldm.minimax.model"] = mm
    sys.modules["comfy"].ldm = sys.modules["comfy.ldm"]
    sys.modules["comfy.ldm"].minimax = sys.modules["comfy.ldm.minimax"]
    sys.modules["comfy.ldm.minimax"].model = mm
    torch_stub = make_torch()
    torch_nn = types.ModuleType("torch.nn")
    torch_functional = types.ModuleType("torch.nn.functional")
    torch_nn.functional = torch_functional
    torch_stub.nn = torch_nn
    sys.modules["torch"] = torch_stub
    sys.modules["torch.nn"] = torch_nn
    sys.modules["torch.nn.functional"] = torch_functional

    cu = types.ModuleType("comfy.utils")
    cu.common_upscale = lambda s, w, h, m, c: T(
        np.zeros((s.shape[0], 3, h, w), dtype=np.float32))
    sys.modules["comfy.utils"] = cu
    sys.modules["comfy"].utils = cu

    mb = types.ModuleType("comfy.model_base")

    class MiniMaxH3:
        def extra_conds(self, **kw):
            # Faithful shape of the stock overwrite behavior: keyframe video
            # latents are assigned first, then refs replace them.
            payload = {}
            keyframes = kw.get("minimax_keyframes")
            refs = kw.get("minimax_refs")
            if keyframes is not None:
                payload["cond_video_latents"] = [
                    kf["latent"] for kf in keyframes if "latent" in kf]
            if refs is not None:
                payload["cond_video_latents"] = [
                    ref["latent"] for ref in refs if "latent" in ref]
                payload["cond_audio_latents"] = [
                    ref["audio_latent"] for ref in refs
                    if ref.get("audio_latent") is not None]
            return {"minimax_payload": types.SimpleNamespace(cond=payload)}
    mb.MiniMaxH3 = MiniMaxH3
    sys.modules["comfy.model_base"] = mb
    sys.modules["comfy"].model_base = mb

    captured = {}
    nh = types.ModuleType("node_helpers")

    def conditioning_set_values(cond, values, append=False):
        out = []
        for item in cond:
            meta = item[1].copy()
            for key, incoming in values.items():
                value = incoming
                if append and meta.get(key) is not None:
                    value = meta[key] + incoming
                meta[key] = value
            out.append([item[0], meta])
        captured.clear()
        if out:
            captured.update(out[0][1])
        return out
    nh.conditioning_set_values = conditioning_set_values
    sys.modules["node_helpers"] = nh

    import os
    import tempfile
    outdir = tempfile.mkdtemp()
    fp = types.ModuleType("folder_paths")
    fp.get_output_directory = lambda: outdir

    def get_save_image_path(prefix, out, *a):
        sub, name = os.path.split(prefix)
        folder = os.path.join(out, sub)
        os.makedirs(folder, exist_ok=True)
        counter = 1 + sum(1 for f in os.listdir(folder)
                          if f.startswith(name))
        return folder, name, counter, sub, prefix
    fp.get_save_image_path = get_save_image_path
    sys.modules["folder_paths"] = fp

    st = types.ModuleType("safetensors")
    stt = types.ModuleType("safetensors.torch")

    def save_file(d, path, metadata=None):
        np.savez(path + ".npz", **{k: v.a for k, v in d.items()})
        open(path, "w").write(path + ".npz")

    def load_file(path):
        real = open(path).read()
        z = np.load(real)
        return {k: T(z[k]) for k in z.files}
    stt.save_file, stt.load_file = save_file, load_file
    st.torch = stt
    sys.modules["safetensors"] = st
    sys.modules["safetensors.torch"] = stt

    stock_layout_init = mm.PackedLayout.__init__
    stock_extra_conds = MiniMaxH3.extra_conds

    # import the package by file location so it works whatever the repo
    # folder is called (ComfyUI-H3-Motion-Context, h3_motion_context, ...)
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "h3mc_pkg", os.path.join(_PKG_DIR, "__init__.py"),
        submodule_search_locations=[_PKG_DIR])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["h3mc_pkg"] = pkg
    spec.loader.exec_module(pkg)  # registers nodes; patches remain opt-in
    nodes = sys.modules["h3mc_pkg.nodes"]
    patch_layout = sys.modules["h3mc_pkg.patch_layout"]
    patch_payload = sys.modules["h3mc_pkg.patch_payload"]
    assert pkg.NODE_CLASS_MAPPINGS
    assert "MiniMaxH3ContexLoopSeamProbe" in pkg.NODE_CLASS_MAPPINGS
    assert "MiniMaxH3MotionContextSeamProbe" not in pkg.NODE_CLASS_MAPPINGS
    assert mm.PackedLayout.__init__ is stock_layout_init
    assert MiniMaxH3.extra_conds is stock_extra_conds
    assert not patch_layout.is_applied()
    assert not patch_payload.is_applied()
    print("import isolation: registering the pack leaves stock H3 untouched")

    trim = nodes.MiniMaxH3LoopTrim()
    numbered = T(np.arange(10, dtype=np.float32).reshape(10, 1, 1, 1))
    delivered, _, with_overlap, retained = trim.trim(
        numbered, 4)
    assert delivered.a[:, 0, 0, 0].tolist() == list(range(4, 10))
    assert with_overlap.a[:, 0, 0, 0].tolist() == list(range(4, 10))
    assert retained == 0
    delivered, _, with_overlap, retained = trim.trim(
        numbered, 4, state={"index": 1, "plan": {
            "shots": [{"video_blend_frames": 99}], "compatibility": {}}})
    assert delivered.a[:, 0, 0, 0].tolist() == list(range(4, 10))
    assert with_overlap.a[:, 0, 0, 0].tolist() == list(range(10))
    assert retained == 4
    scene_state = {
        "index": 2,
        "plan": {
            "shots": [{}, {"video_blend_frames": 3}],
            "compatibility": {"video_blend_frames": 0},
        },
    }
    delivered, _, with_overlap, retained = trim.trim(
        numbered, 4, state=scene_state)
    assert delivered.a[:, 0, 0, 0].tolist() == list(range(4, 10))
    assert with_overlap.a[:, 0, 0, 0].tolist() == list(range(1, 10))
    assert retained == 3
    first_scene_state = {
        "index": 1,
        "plan": {
            "shots": [{
                "raw_frames": 6,
                "delivered_frames": 6,
                "video_blend_frames": 39,
            }],
            "compatibility": {"video_blend_frames": 39},
        },
    }
    delivered, _, with_overlap, retained = trim.trim(
        numbered[4:], 0, state=first_scene_state)
    assert delivered.a[:, 0, 0, 0].tolist() == list(range(4, 10))
    assert with_overlap.a[:, 0, 0, 0].tolist() == list(range(4, 10))
    assert retained == 0
    exact_audio = {
        "waveform": T(np.arange(10, dtype=np.float32).reshape(1, 1, 10)),
        "sample_rate": 24,
    }
    _, trimmed_audio, _, _ = trim.trim(
        numbered, 4, exact_audio, 24.0, True)
    assert trimmed_audio["waveform"].a.reshape(-1).tolist() == list(range(4, 10))
    assert trimmed_audio[
        nodes.AUDIO_WITH_OVERLAP_WAVEFORM_KEY
    ].a.reshape(-1).tolist() == list(range(10))
    assert trimmed_audio[nodes.AUDIO_WITH_OVERLAP_FRAMES_KEY] == 10
    assert trimmed_audio[nodes.AUDIO_TRIM_FRAMES_KEY] == 4
    print("trim overlap: hard output unchanged; optional prefix retained and "
          "clamped to repeated context; full decoded audio rides privately "
          "with the delivered AUDIO value")

    # a 124-frame clip: latent_t 37 (7 full 17-frame groups + 1 + 4),
    # audio grid ceil(124 * 5/3) = 207 steps, overhang exactly 1/3
    latent_t, frames, audio_t = 37, 124, 207
    assert nodes._pixel_frames(latent_t) == frames
    h, w = 480 // 16, 864 // 16
    target = {"samples": Nested([
        T(np.zeros((1, 16, latent_t, h, w), dtype=np.float32)),
        T(np.zeros((1, 32, 2, audio_t), dtype=np.float32)),
    ])}
    # previous clip's sampler latent (same dims in this setup)
    prev = {"samples": Nested([
        T(np.zeros((1, 16, latent_t, h, w), dtype=np.float32)),
        T(np.arange(1 * 32 * 2 * audio_t, dtype=np.float32
                    ).reshape(1, 32, 2, audio_t)),
    ])}
    # 260 valid H3 frames use 77 video steps and round 433.33 audio steps
    # down to 433. The signed grid offset must remain -1/3 rather than being
    # discarded, or generated-audio context lands 0.2 video frames late.
    underhang_latent = {"samples": Nested([
        T(np.zeros((1, 16, 77, 2, 2), dtype=np.float32)),
        T(np.zeros((1, 32, 2, 433), dtype=np.float32)),
    ])}
    _, _, signed_offset = nodes._audio_tail_from_latent(underhang_latent, 22)
    assert abs(signed_offset + 1.0 / 3.0) < 1e-9, signed_offset
    print("audio grid: 260-frame signed underhang preserved at -1/3 step")
    context = T(np.zeros((124, 480, 864, 3), dtype=np.float32))

    class VAE:
        def encode(self, x):
            n = x.shape[0]
            steps = max(1, (n - 5) // 17 * 5 + 2)
            return T(np.zeros((1, 16, steps, h, w), dtype=np.float32))

    node = nodes.MiniMaxH3MotionContext()
    assert list(node.INPUT_TYPES()["optional"])[-1] == "video_context_latent"
    # Simulate conditioning produced by MiniMaxH3ReferenceToVideo. Motion
    # Context must append its timeline-audio block without dropping either
    # existing Ref2VA block.
    r2v_refs = [
        {"kind": "image", "latent_h": h, "latent_w": w,
         "latent": T(np.zeros((1, 16, 1, h, w), dtype=np.float32))},
        {"kind": "video", "latent_t": 2, "latent_h": h, "latent_w": w,
         "ref_audio_t": 0,
         "latent": T(np.zeros((1, 16, 2, h, w), dtype=np.float32))},
        {"kind": "audio", "ref_audio_t": 3,
         "audio_latent": T(np.zeros((1, 32, 2, 3), dtype=np.float32))},
    ]
    r2v_conditioning = [["c", {"minimax_refs": r2v_refs}]]
    legacy_warnings = []
    original_warning = nodes._LOG.warning
    nodes._LOG.warning = lambda message, *args: legacy_warnings.append(
        message % args if args else message)
    try:
        out, trim = node.apply(
            conditioning=r2v_conditioning, vae=VAE(), latent=target,
            context_frames=context, context_length=22, encode_mode="video",
            anchor_mode="head", crop="disabled", audio_context_length=22,
            audio_mode="timeline", context_latent=prev)
        # Repeated scenes must not flood the server log with the upgrade notice.
        nodes._activate_inline_patches()
    finally:
        nodes._LOG.warning = original_warning

    update_warnings = [message for message in legacy_warnings
                       if "Comfy-Org/ComfyUI PR #15439" in message]
    assert len(update_warnings) == 1, update_warnings
    assert "Update ComfyUI" in update_warnings[0]

    assert patch_layout.is_applied() and patch_payload.is_applied()
    assert nodes._claim_inline_patch_ownership() == (
        "legacy guides; layout owned by this pack; "
        "payload owned by this pack")
    assert mm.PackedLayout.__init__ is not stock_layout_init
    assert MiniMaxH3.extra_conds is not stock_extra_conds

    video_context_capture = captured.copy()
    audio_only, audio_only_trim = node.apply(
        conditioning=r2v_conditioning, vae=VAE(), latent=target,
        context_frames=context[:0], context_length=0, encode_mode="video",
        anchor_mode="head", crop="disabled", audio_context_length=33,
        audio_mode="timeline", context_latent=prev)
    assert audio_only_trim == 0
    audio_only_refs = audio_only[0][1]["minimax_refs"]
    assert audio_only_refs[:3] == r2v_refs
    assert audio_only_refs[-1]["kind"] == "audio"
    assert nodes.MC_AUDIO_KEY in audio_only_refs[-1]
    captured.clear()
    captured.update(video_context_capture)
    print("audio-only context: zero video blocks, prior latent sound retained")

    # Once an opted-in graph has installed the wrappers, an ordinary H3 graph
    # with stock keyframes + refs must still get stock overwrite semantics.
    ordinary = MiniMaxH3().extra_conds(
        minimax_keyframes=[{"latent": "stock-keyframe"}],
        minimax_refs=[{"latent": "stock-ref"}],
    )["minimax_payload"].cond
    assert ordinary["cond_video_latents"] == ["stock-ref"]

    # Motion Context's private marker opts this payload into coexistence.
    marked = MiniMaxH3().extra_conds(
        minimax_keyframes=[{
            "latent": "motion-keyframe", nodes.MC_KEY: 0,
        }],
        minimax_refs=[{"latent": "stock-ref"}],
        minimax_frame_count=frames,
    )["minimax_payload"].cond
    assert marked["cond_video_latents"] == ["motion-keyframe", "stock-ref"]
    assert marked["frame_count"] == frames
    print("inline activation: marked payload opts in; unmarked H3 stays stock")

    kfs = captured["minimax_keyframes"]
    assert len(kfs) == 7, len(kfs)
    idx = [kf[nodes.MC_KEY] for kf in kfs]
    assert idx == [0, 1, 5, 9, 13, 17, 18], idx
    assert captured["minimax_frame_count"] == frames
    assert trim == 22
    original_context_capture = captured.copy()

    # Latent Guide keeps the ordinary Guide layout but supplies its video
    # blocks directly from the previous sampler output. The video VAE must
    # not run, and the target latent remains a separate input to the node.
    direct_video = np.broadcast_to(
        np.arange(latent_t, dtype=np.float32).reshape(1, 1, latent_t, 1, 1),
        (1, 16, latent_t, h, w),
    ).copy()
    direct_prev = {"samples": Nested([
        T(direct_video), prev["samples"].parts[1],
    ])}

    class RefusingVAE:
        def encode(self, _value):
            raise AssertionError("Latent Guide unexpectedly called video VAE")

    captured.clear()
    direct_out, direct_trim = node.apply(
        conditioning=[["c", {}]], vae=RefusingVAE(), latent=target,
        context_frames=context, context_length=22, encode_mode="video",
        anchor_mode="head", crop="disabled", audio_context_length=0,
        audio_mode="timeline", video_context_latent=direct_prev)
    direct_keyframes = direct_out[0][1]["minimax_keyframes"]
    assert direct_trim == 22
    assert len(direct_keyframes) == 7
    assert [value[nodes.MC_KEY] for value in direct_keyframes] == idx
    assert [float(value["latent"].a[0, 0, 0, 0, 0])
            for value in direct_keyframes] == list(range(30, 37))
    print("latent Guide: 22-frame phase-aligned tail reused as 7 direct "
          "conditioning blocks without a video-VAE round trip")

    class CountingVAE(VAE):
        def __init__(self):
            self.calls = 0

        def encode(self, value):
            self.calls += 1
            return super().encode(value)

    incompatible_prev = {"samples": Nested([
        T(np.zeros((1, 16, latent_t, h - 1, w), dtype=np.float32)),
        prev["samples"].parts[1],
    ])}
    fallback_vae = CountingVAE()
    captured.clear()
    fallback_out, _fallback_trim = node.apply(
        conditioning=[["c", {}]], vae=fallback_vae, latent=target,
        context_frames=context, context_length=22, encode_mode="video",
        anchor_mode="head", crop="disabled", audio_context_length=0,
        audio_mode="timeline", video_context_latent=incompatible_prev)
    assert fallback_vae.calls == 1
    assert len(fallback_out[0][1]["minimax_keyframes"]) == 7
    print("RGB Guide fallback: original context-frame VAE path retained")
    captured.clear()
    captured.update(original_context_capture)

    refs = captured["minimax_refs"]
    assert refs[:3] == r2v_refs
    assert len(refs) == 4
    assert out[0][1]["minimax_refs"] == refs
    assert r2v_conditioning[0][1]["minimax_refs"] == r2v_refs  # no mutation
    ref = refs[-1]
    assert ref["kind"] == "audio"
    assert ref["ref_audio_t"] == 37, ref["ref_audio_t"]  # round(22/24*40)
    tail = ref["audio_latent"]
    assert tuple(tail.shape) == (1, 32, 2, 37), tail.shape
    # tail must be the LAST 37 steps of the source
    assert float(tail.a[0, 0, 0, -1]) == float(prev["samples"].parts[1]
                                               .a[0, 0, 0, -1])
    overhang = audio_t - nodes.FRAME_RESCALE * frames  # 207 - 206.667
    want_end = 22 + overhang / nodes.FRAME_RESCALE
    got_end = ref[nodes.MC_AUDIO_KEY]
    assert abs(got_end - want_end) < 1e-9, (got_end, want_end)
    assert abs(got_end - 22.2) < 1e-6, got_end
    print("Ref2VA latent path: image/video/audio refs preserved + MC audio; "
          "7 cond blocks at %s, audio 37 steps sliced from latent tail, "
          "end_frame %.4f (overhang-compensated)" % (idx, got_end))

    # Upstream 0.3.0 compatibility: keep a stock last_frame guide while the
    # carried head replaces a conflicting first_frame guide.
    import logging as _logging

    class _Catcher(_logging.Handler):
        def __init__(self):
            _logging.Handler.__init__(self)
            self.messages = []

        def emit(self, record):
            self.messages.append(record.getMessage())

    incoming_keyframes = [
        {"resolved_frame_index": 0, "latent": "first"},
        {"resolved_frame_index": frames - 1, "latent": "last"},
    ]
    anchored = [["c", {
        "minimax_keyframes": [dict(value) for value in incoming_keyframes],
        "minimax_frame_count": frames,
    }]]
    catcher = _Catcher()
    nodes._LOG.addHandler(catcher)
    captured.clear()
    try:
        anchored_out, anchored_trim = node.apply(
            conditioning=anchored, vae=VAE(), latent=target,
            context_frames=context, context_length=22, encode_mode="video",
            anchor_mode="head", crop="disabled", audio_context_length=22,
            audio_mode="timeline", context_latent=prev)
    finally:
        nodes._LOG.removeHandler(catcher)
    merged = anchored_out[0][1]["minimax_keyframes"]
    assert anchored_trim == 22
    assert len(merged) == 8
    assert merged[0]["latent"] == "last"
    assert merged[0][nodes.MC_KEY] == frames - 1
    assert [value[nodes.MC_KEY] for value in merged[1:]] == idx
    assert any("dropped 1 keyframe anchor" in message
               for message in catcher.messages)
    assert nodes.MC_KEY not in anchored[0][1]["minimax_keyframes"][1]
    try:
        node.apply(
            conditioning=[["c", {
                "minimax_keyframes": [dict(incoming_keyframes[1])],
                "minimax_frame_count": frames + 17,
            }]],
            vae=VAE(), latent=target, context_frames=context,
            context_length=22, encode_mode="video", anchor_mode="head",
            crop="disabled", audio_context_length=22,
            audio_mode="timeline", context_latent=prev)
    except ValueError as exc:
        assert "resolved for a" in str(exc)
    else:
        raise AssertionError("mismatched last_frame conditioning was accepted")
    print("last_frame: final anchor retained, conflicting first anchor dropped, "
          "and mismatched frame-count wiring refused")

    captured.clear()
    _, trim_56 = node.apply(
        conditioning=[["c", {}]], vae=VAE(), latent=target,
        context_frames=context, context_length=56, encode_mode="video",
        anchor_mode="head", crop="disabled", audio_context_length=22,
        audio_mode="timeline", context_latent=prev)
    context_56 = captured["minimax_keyframes"]
    assert trim_56 == 56 and len(context_56) == 17
    assert [value[nodes.MC_KEY] for value in context_56][-1] == 52
    print("56-frame context: represented by 17 aligned video latent steps")

    captured.clear()
    _, trim_90 = node.apply(
        conditioning=[["c", {}]], vae=VAE(), latent=target,
        context_frames=context, context_length=90, encode_mode="video",
        anchor_mode="head", crop="disabled", audio_context_length=22,
        audio_mode="timeline", context_latent=prev)
    context_90 = captured["minimax_keyframes"]
    assert trim_90 == 90 and len(context_90) == 27
    assert nodes._pixel_frames(len(context_90)) == 90
    print("90-frame context: represented by 27 aligned video latent steps")

    # decoded-audio path must still work and carry integer end_frame
    captured.clear()

    class AudioVAE:
        audio_sample_rate = 32000

        def encode(self, x):
            steps = int(round(x.shape[-2] / 32000 * 40))
            return T(np.zeros((1, 32, 2, steps), dtype=np.float32))

    audio = {"waveform": T(np.zeros((1, 2, 32000), dtype=np.float32)),
             "sample_rate": 32000}
    node.apply(
        conditioning=[["c", {}]], vae=VAE(), latent=target,
        context_frames=context, context_length=22, encode_mode="video",
        anchor_mode="head", crop="disabled", audio_context_length=22,
        audio_mode="timeline", audio_vae=AudioVAE(), context_audio=audio)
    ref2 = captured["minimax_refs"][0]
    assert abs(ref2[nodes.MC_AUDIO_KEY] - 22.0) < 1e-9
    print("vae path: unchanged, end_frame %.1f" % ref2[nodes.MC_AUDIO_KEY])

    assert not hasattr(nodes, "MiniMaxH3MotionContextSaveLatent")
    assert not hasattr(nodes, "MiniMaxH3MotionContextLoadLatent")

    print("smoke test passed")


if __name__ == "__main__":
    main()
