#!/usr/bin/env python3
"""Run CPU regression scripts without a server or user project.

Requires the ComfyUI Python environment, Node.js and ffmpeg. Browser/GPU tests
are deliberately separate; see docs/RELEASING_0_7.md. Failures return nonzero.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def discover(root):
    return sorted(path for path in (root / "tests").iterdir()
                  if path.name.startswith("_") and path.suffix in (".py", ".mjs")
                  and "browser" not in path.name and path.name != "_mock_harness.py")


def run(path, env, timeout):
    executable = "node" if path.suffix == ".mjs" else sys.executable
    try:
        result = subprocess.run([executable, str(path)], cwd=ROOT, env=env,
                                text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=timeout)
        return path.name, result.returncode, result.stdout
    except (subprocess.TimeoutExpired, OSError) as error:
        return path.name, 1, str(error)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comfy-root", type=Path,
                        help="ComfyUI checkout (or set COMFYUI_PATH)")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=120, help="Seconds per script")
    args = parser.parse_args(argv)
    if args.jobs < 1 or args.timeout < 1:
        parser.error("--jobs and --timeout must be positive")
    env = {**os.environ, "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
           "CUDA_VISIBLE_DEVICES": "", "PYTHONDONTWRITEBYTECODE": "1"}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(ROOT), env.get("PYTHONPATH"))))
    env["NODE_OPTIONS"] = env.get("NODE_OPTIONS", "") + " --experimental-vm-modules"
    if args.comfy_root:
        env["COMFYUI_PATH"] = str(args.comfy_root.resolve())
    if env.get("COMFYUI_PATH") and not (Path(env["COMFYUI_PATH"]) / "comfy/options.py").is_file():
        parser.error("COMFYUI_PATH/--comfy-root must point to a ComfyUI checkout")
    paths = discover(ROOT)
    failures = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for name, code, output in pool.map(lambda path: run(path, env, args.timeout), paths):
            print(("FAIL" if code else "PASS"), name, flush=True)
            if code:
                failures.append(name)
                print(output, flush=True)
    print(f"SUMMARY: {len(paths) - len(failures)} passed; {len(failures)} failed", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
