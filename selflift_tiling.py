"""Lightweight, opt-in controls for the SelfLift high-resolution denoiser."""
from __future__ import annotations

TILING_TYPE = "H3_SELFLIFT_TILING"


def tiling_settings(value=None):
    if value is None or value is False:
        return None
    if not isinstance(value, dict) or type(value.get("enabled")) is not bool:
        raise ValueError("Connect SelfLift Tiling settings to highres_tiling.")
    if not value["enabled"]:
        return None
    tiles, overlap, axis = value.get("tiles", 2), value.get("overlap", 8), value.get("axis", "longest")
    if type(tiles) is not int or not 2 <= tiles <= 8:
        raise ValueError("SelfLift tiling needs 2..8 tiles.")
    if type(overlap) is not int or not 0 <= overlap <= 64 or overlap % 2:
        raise ValueError("SelfLift tile overlap must be an even number of latent pixels from 0 to 64.")
    if axis not in ("longest", "width", "height"):
        raise ValueError("SelfLift tile axis must be longest, width or height.")
    return {"enabled": True, "tiles": tiles, "overlap": overlap, "axis": axis}


class MiniMaxH3SelfLiftTiling:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "enabled": ("BOOLEAN", {"default": False,
                "tooltip": "Opt in to spatial tiling of SelfLift's final high-resolution denoising steps only. Not TST, VAE tiling or tiling of the learned latent lift."}),
            "tiles": ("INT", {"default": 2, "min": 2, "max": 8,
                "tooltip": "Fixed tile count along one spatial axis. More tiles reduce attention workspace but lose cross-tile context and add overhead. Small grids use fewer tiles."}),
            "overlap": ("INT", {"default": 8, "min": 0, "max": 64, "step": 2,
                "tooltip": "Context margin at each tile edge, in latent pixels (normally 16 output pixels each). Adjacent margins overlap and blend. Clamped on small tiles."}),
            "axis": (["longest", "width", "height"], {
                "tooltip": "Split along the longest spatial dimension, or choose width/height. Every tile retains the full video timeline and audio."}),
        }}

    RETURN_TYPES = (TILING_TYPE,)
    RETURN_NAMES = ("tiling",)
    FUNCTION = "configure"
    CATEGORY = "sampling/minimax/context_loop"
    DESCRIPTION = ("Experimental high-resolution SelfLift tiling. Connect to highres_tiling on Chain SelfLift "
                   "Sampler or SelfLift Seed Hunt. Off/unconnected preserves existing behavior. Tiles do not "
                   "share attention; only the first tile's audio prediction is kept. May introduce seams or "
                   "change motion/audio quality. ControlNet and regional conditioning are unsupported.")

    def configure(self, enabled=False, tiles=2, overlap=8, axis="longest"):
        settings = tiling_settings({"enabled": enabled, "tiles": tiles, "overlap": overlap, "axis": axis})
        return (settings or {"enabled": False},)
