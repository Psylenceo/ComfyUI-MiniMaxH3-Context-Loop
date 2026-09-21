"""Settings route applies to real project mutation guards, not just UI state."""
import asyncio
import json
import pathlib
import runpy
import tempfile
import types

fixture = runpy.run_path(str(pathlib.Path(__file__).with_name(
    "_top_level_requeue_unit_test.py")))
chain = fixture["chain"]
FakeRequest = fixture["FakeRequest"]


async def scenario(root):
    chain._output_root = lambda: root
    events = []
    chain.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(
        send_sync=lambda event, payload: events.append((event, payload))))

    async def settings(method, body=None):
        request = FakeRequest(body=body)
        request.method = method
        return await chain._project_ownership_settings(request)

    response = await settings("GET")
    assert response.status == 200 and json.loads(response.text)["enabled"] is True
    assert response.headers["Cache-Control"] == "no-store"
    assert not events
    run = "settings_integration"
    chain.claim_project_ownership(root, run, "settings-route-owner-123456")
    handoff = chain._handoff_store().create(run, action="next_scene", scene=2)
    request = FakeRequest(body={"run_name": run, "handoff_id": handoff["handoff_id"]})
    request.headers = {}
    assert (await chain._claim_handoff(request)).status == 423
    for invalid in ({}, {"enabled": "false"}, {"enabled": 0}, []):
        assert (await settings("POST", invalid)).status == 400
    assert not events
    response = await settings("POST", {"enabled": False})
    assert response.status == 200 and json.loads(response.text)["enabled"] is False
    assert events[-1][0] == "minimax_h3_project_ownership_settings"
    assert (await chain._claim_handoff(request)).status == 200
    # Absence/failure of a websocket must not turn a committed preference into an error.
    chain.PromptServer = None
    assert (await settings("POST", {"enabled": True})).status == 200
    assert (await chain._release_handoff(request)).status == 423

    def broken_broadcast(*_args):
        raise RuntimeError("disconnected websocket")

    chain.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(send_sync=broken_broadcast))
    assert (await settings("POST", {"enabled": False})).status == 200
    assert (await chain._release_handoff(request)).status == 200


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="h3-ownership-settings-") as root:
        asyncio.run(scenario(root))
    print("Ownership settings routes: validation, broadcast and real mutation guards pass")
