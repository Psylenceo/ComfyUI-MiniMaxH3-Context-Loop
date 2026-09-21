"""An explicit saved context take is independent of timeline assignments."""
from __future__ import annotations

import re


def context_take(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("context_take must contain a source scene and saved revision.")
    source = value.get("source")
    if isinstance(source, bool) or not isinstance(source, (str, int)) or not str(source).strip():
        raise ValueError("context_take requires a source scene ID or index.")
    revision = str(value.get("revision") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{32}", revision):
        raise ValueError("context_take requires an exact 32-character saved revision ID.")
    return {"source": source, "revision": revision}
