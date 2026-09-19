"""Lightweight SelfLift choices; safe to import while building node schemas."""

BILINEAR = "bilinear"
TRIDAE = "tridae"
TRIDAE_CHECKPOINT = "h3_clean_latent_upscaler_film_epoch200.safetensors"
TRIDAE_SHA256 = "984afb58f11d01274b90d880596ce2f93bc9c512db66fdeecd4e2d99b371d3e4"
TRIDAE_URL = (
    "https://huggingface.co/Tridae/H3LatentUpscaler/resolve/"
    "5c87ab7cf8425a2cfbc3d21da1bffbd686ce67a6/" + TRIDAE_CHECKPOINT
)


def validate_upscaler_grid(name, latent, lowres_scale=0.5):
    """Reject a fixed-2x mismatch before spending time on the low pass."""
    if name != TRIDAE:
        return
    samples = latent["samples"]
    streams = samples.unbind() if getattr(samples, "is_nested", False) else samples
    video = streams[0] if isinstance(streams, (list, tuple)) else streams
    if video.ndim != 5 or video.shape[1] != 24:
        raise ValueError("Tr1dae requires an H3 video latent with shape Bx24xTxHxW.")
    target = tuple(int(size) for size in video.shape[-2:])
    low = tuple(max(2, round(size * lowres_scale / 2) * 2) for size in target)
    if target != tuple(size * 2 for size in low):
        raise ValueError(
            "Tr1dae requires an exact 2x spatial latent grid. Set lowres_scale to 0.5 and the final Plan width and "
            "height to multiples of 64 (for example 1920x1088), or select bilinear/an LBH checkpoint."
        )
