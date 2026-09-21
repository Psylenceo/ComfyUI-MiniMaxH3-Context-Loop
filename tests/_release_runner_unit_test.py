"""Release checks must surface failed scripts, not print a successful exit."""
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_checks", ROOT / "tools/check_release.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
paths = runner.discover(ROOT)
assert paths and all("browser" not in p.name and p.name != "_mock_harness.py" for p in paths)
assert ROOT / "tests/_chain_smoke_test.py" in paths
assert ROOT / "tests/_plan_duration_parity_test.py" in paths

with patch.object(runner, "discover", return_value=paths[:2]), patch.object(runner, "run") as run:
    run.side_effect = [("one.py", 0, "ok"), ("two.mjs", 1, "broken")]
    with contextlib.redirect_stdout(io.StringIO()) as output:
        assert runner.main([]) == 1
    assert "broken" in output.getvalue() and "1 failed" in output.getvalue()
    env = run.call_args.args[1]
    assert env["CUDA_VISIBLE_DEVICES"] == ""
    assert env["OMP_NUM_THREADS"] == "1" and "--experimental-vm-modules" in env["NODE_OPTIONS"]
    run.side_effect = [("one.py", 0, "ok"), ("two.mjs", 0, "ok")]
    with contextlib.redirect_stdout(io.StringIO()):
        assert runner.main([]) == 0

for error in (FileNotFoundError("node missing"), subprocess.TimeoutExpired("node", 1)):
    with patch.object(runner.subprocess, "run", side_effect=error):
        assert runner.run(paths[0], {}, 1)[1] != 0
print("Release runner: discovery, environment, failures, success and timeouts pass")
