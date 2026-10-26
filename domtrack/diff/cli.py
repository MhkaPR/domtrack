"""``domtrack diff`` — content-level diff between two captures.

Browser-free CLI over :mod:`domtrack.diff.core`.
"""

from __future__ import annotations

import argparse
import json
import sys

from .. import trace as tf
from .core import diff_traces, format_report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="domtrack diff",
        description="Compare the content of two captures of the same page "
                    "(no browser needed).",
    )
    p.add_argument("a", metavar="A.json", help="baseline trace")
    p.add_argument("b", metavar="B.json", help="trace to compare against A")
    p.add_argument("--no-records", action="store_true",
                   help="compare only the initial snapshots (skip records)")
    p.add_argument("--at-ms-a", type=int, default=None,
                   help="fold A only up to this ms since capture start")
    p.add_argument("--at-ms-b", type=int, default=None,
                   help="fold B only up to this ms since capture start")
    p.add_argument("--json", action="store_true",
                   help="emit the machine-readable diff as JSON")
    return p


def _load(path: str) -> dict:
    raw = tf.load_raw(path)
    if not tf.is_rrweb_trace(raw):
        raise ValueError(
            f"{path}: not an rrweb-style trace (need 'snapshot' + 'records')"
        )
    if not raw.get("snapshot"):
        raise ValueError(f"{path}: snapshot not found")
    return raw


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        raw_a = _load(args.a)
        raw_b = _load(args.b)
    except FileNotFoundError as e:
        print(f"ERROR: file not found: {e.filename}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ERROR: could not load trace: {e}", file=sys.stderr)
        return 1

    result = diff_traces(
        raw_a, raw_b,
        at_ms_a=args.at_ms_a,
        at_ms_b=args.at_ms_b,
        apply_records=not args.no_records,
    )

    if args.json:
        json.dump(result.to_dict(), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        try:
            format_report(result, sys.stdout)
        except BrokenPipeError:
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
