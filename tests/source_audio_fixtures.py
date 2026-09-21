"""Build current Source Timeline fixtures instead of retired 0.4 AUDIO wires."""


def with_source_audio(chain, plan, audio):
    if audio is None:
        return chain._plan_without_source_timeline(plan)
    return chain._plan_with_source_timeline(
        plan, chain._make_source_timeline(source_audio=audio))[0]


def bind_manifest_audio(chain, manifest, audio):
    """Persist a track using the same descriptor contract as Loop Start."""
    manifest["compatibility"] = dict(manifest.get("compatibility") or {})
    manifest["compatibility"].setdefault("audio_mode", "source_track")
    timeline = chain._materialize_source_timeline_audio(
        chain._make_source_timeline(source_audio=audio), manifest)
    manifest["source_timeline"] = chain._source_timeline_recovery_record(timeline)
    manifest["compatibility"].update({
        "source_timeline_fingerprint": timeline["fingerprints"]["timeline"],
        "source_audio_hash": timeline["fingerprints"]["audio"],
    })
    return timeline
