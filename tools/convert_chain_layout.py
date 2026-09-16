"""Preview or create an organized copy; never modifies the source project."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chain_layout_conversion import preview, convert_copy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Existing h3_chains/<project> directory")
    parser.add_argument("--copy-to-output", type=Path,
                        help="Separate output directory; copy keeps the original run name")
    parser.add_argument("--writers-stopped", action="store_true",
                        help="Confirm ComfyUI and other project writers are stopped")
    args = parser.parse_args()
    if args.copy_to_output:
        if not args.writers_stopped:
            parser.error("Stop project writers first, then pass --writers-stopped.")
        report = convert_copy(args.source, args.copy_to_output,
                              progress=lambda done, total, move: print(
                                  "%d/%d %s" % (done, total, move.destination), file=sys.stderr))
    else:
        moves = preview(args.source)
        report = {"dry_run": True, "files": len(moves), "bytes": sum(m.size for m in moves),
                  "moves": [{"from": str(m.source), "to": str(m.destination), "bytes": m.size}
                            for m in moves]}
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
