"""CLI: python -m scripts.eval [--output DIR] [--references FILE] [--out DIR]"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .report import aggregate, load_sessions, render_markdown, score_session


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.eval", description=__doc__)
    ap.add_argument("--output", type=Path, default=Path("output"),
                    help="directory holding <id>/dub_session.json (default: output)")
    ap.add_argument("--references", type=Path, default=None,
                    help="JSON file of reference translations/transcripts, keyed by session id")
    ap.add_argument("--out", type=Path, default=Path("docs/eval"),
                    help="where to write report.md and report.json (default: docs/eval)")
    ap.add_argument("--fail-on-overrun", type=float, default=None, metavar="RATE",
                    help="exit non-zero if the overall overrunning rate exceeds RATE (e.g. 0.05)")
    args = ap.parse_args(argv)

    if not args.output.is_dir():
        print(f"error: {args.output} is not a directory", file=sys.stderr)
        return 2

    sessions = load_sessions(args.output)
    if not sessions:
        print(f"error: no dub_session.json with cues found under {args.output}", file=sys.stderr)
        return 2

    refs = None
    if args.references:
        try:
            refs = json.loads(args.references.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: could not read references: {exc}", file=sys.stderr)
            return 2

    rows = [score_session(s, refs) for s in sessions]
    agg = aggregate(sessions)

    args.out.mkdir(parents=True, exist_ok=True)
    md = render_markdown(rows, agg)
    (args.out / "report.md").write_text(md, encoding="utf-8")
    (args.out / "report.json").write_text(
        json.dumps({"sessions": rows, "aggregate": agg}, indent=2), encoding="utf-8"
    )
    print(md)
    print(f"\nWrote {args.out/'report.md'} and {args.out/'report.json'}")

    if args.fail_on_overrun is not None:
        rate = agg["overall"]["overrun_rate"]
        if rate > args.fail_on_overrun:
            print(f"FAIL: overrunning rate {rate:.4f} exceeds threshold {args.fail_on_overrun}",
                  file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
