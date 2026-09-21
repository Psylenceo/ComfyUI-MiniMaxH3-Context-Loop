"""Optional workflow fencing must not disable transaction safety."""
import json
import pathlib
import tempfile
import threading

import project_ownership as ownership

OWNER_A = "settings-owner-a-1234567890"
OWNER_B = "settings-owner-b-1234567890"


def rejected(callback, exception=ownership.ProjectOwnershipError):
    try:
        callback()
    except exception:
        return
    raise AssertionError(f"Expected {exception.__name__}")


def main():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as other:
        assert ownership.project_ownership_settings(root)["enabled"] is True
        claimed = ownership.claim_project_ownership(root, "film", OWNER_A)
        proof = {"owner_id": OWNER_A, "epoch": claimed["epoch"]}
        path = pathlib.Path(ownership.ownership_path(root, "film"))
        original = path.read_bytes()
        rejected(lambda: ownership.require_project_ownership(root, "film", None))
        disabled = ownership.set_project_ownership_enabled(root, False)
        assert disabled["epoch"] == 1 and disabled["enabled"] is False
        assert ownership.project_ownership_settings(root) == disabled
        assert ownership.project_ownership_settings(other)["enabled"] is True
        assert ownership.set_project_ownership_enabled(root, False) == disabled
        assert ownership.require_project_ownership(root, "film", None) is None
        assert ownership.require_project_ownership(root, "film", {"epoch": -100}) is None
        for result in (
            ownership.ownership_status(root, "film", OWNER_A),
            ownership.claim_project_ownership(root, "film", OWNER_B),
            ownership.claim_project_ownership(root, "film", OWNER_B, force=True),
            ownership.heartbeat_project_ownership(root, "film", OWNER_A, proof["epoch"]),
            ownership.release_project_ownership(root, "film", OWNER_A, proof["epoch"]),
        ):
            assert result["locking_enabled"] is False
            assert result["owned_by_requester"] is False
            assert result["policy_epoch"] == disabled["epoch"]
        assert path.read_bytes() == original, "Disabling must preserve the recorded fence"

        # A run named settings cannot alias the global preference.
        ownership.claim_project_ownership(root, "settings", OWNER_A)
        assert not pathlib.Path(ownership.ownership_path(root, "settings")).exists()
        policy_path = path.parent / ".settings.json"
        assert json.loads(policy_path.read_text())["enabled"] is False

        # Neither another commit nor a mode change can split a commit, even off.
        for action in (
            lambda: ownership.require_project_ownership(root, "film", None),
            lambda: ownership.set_project_ownership_enabled(root, True),
        ):
            started, finished = threading.Event(), threading.Event()
            errors = []

            def worker():
                started.set()
                try:
                    action()
                except Exception as error:
                    errors.append(error)
                finally:
                    finished.set()

            with ownership.project_write_guard(root, "film", None):
                thread = threading.Thread(target=worker)
                thread.start()
                assert started.wait(1)
                assert not finished.wait(.1), "The durable commit was not serialized"
            thread.join(2)
            assert not thread.is_alive() and not errors

        enabled = ownership.project_ownership_settings(root)
        assert enabled["enabled"] is True and enabled["epoch"] == 2
        assert ownership.set_project_ownership_enabled(root, True) == enabled
        assert path.read_bytes() == original, "Toggle must not rewrite every run"
        status = ownership.ownership_status(root, "film", OWNER_A)
        assert status["available"] and not status["owned_by_requester"]
        rejected(lambda: ownership.require_project_ownership(root, "film", proof))
        rejected(lambda: ownership.require_project_ownership(root, "film", None))
        assert not ownership.heartbeat_project_ownership(
            root, "film", OWNER_A, proof["epoch"])["owned_by_requester"]
        fresh = ownership.claim_project_ownership(root, "film", OWNER_B)
        assert fresh["epoch"] == proof["epoch"] + 1
        assert fresh["policy_epoch"] == enabled["epoch"]
        assert ownership.require_project_ownership(root, "film", {
            "owner_id": OWNER_B, "epoch": fresh["epoch"],
        })
        rejected(lambda: ownership.require_project_ownership(root, "film", proof))
        for invalid in (None, 0, 1, "false", [], {}):
            rejected(lambda: ownership.set_project_ownership_enabled(root, invalid), ValueError)
        assert ownership.project_ownership_settings(root) == enabled

        # Invalid persisted settings fail closed, even when bypass was intended.
        for malformed in ("{", "[]", '{"enabled":false}', json.dumps({
            "format": ownership.OWNERSHIP_SETTINGS_FORMAT, "enabled": False, "epoch": True,
        })):
            policy_path.write_text(malformed)
            rejected(lambda: ownership.require_project_ownership(root, "film", proof), ValueError)
            rejected(lambda: ownership.set_project_ownership_enabled(root, False), ValueError)


if __name__ == "__main__":
    main()
    print("Ownership settings: default, persistence, bypass, mutex, fresh claims and fail-closed pass")
