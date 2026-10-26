"""``domtrack html`` — rebuild a trace's DOM as HTML at a record point.

Browser-free wrapper around :class:`domtrack.trace.DomState` /
:func:`domtrack.trace.rebuild_html`.
"""

from __future__ import annotations

import argparse
import sys

from .. import trace as tf


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="domtrack html",
        description="Rebuild a trace's DOM as HTML at a record point "
                    "(no browser needed).",
    )
    p.add_argument("inputs", nargs="+", metavar="FILE",
                   help="combined trace, or <snapshot.json> <records.json>")
    p.add_argument("--at-ms", type=int, default=None,
                   help="rebuild only up to this ms since capture start "
                        "(default: all records)")
    p.add_argument("--at-index", type=int, default=None,
                   help="rebuild only through record N (0-based, inclusive); "
                        "alternative to --at-ms")
    p.add_argument("-o", "--out", default=None,
                   help="write HTML to this file (default: stdout)")
    p.add_argument("--stats", action="store_true",
                   help="print reconstruction stats to stderr")
    return p


def load_inputs(paths: list[str]) -> dict:
    """Return a trace dict from either one combined file or two split files."""
    return tf.load_trace_inputs(paths)


def _build_state(raw: dict, at_ms: int | None, at_index: int | None) -> tf.DomState:
    state = tf.DomState.from_trace(raw)
    records = raw.get("records") or []
    if at_index is not None:
        for record in records[: at_index + 1]:
            state.apply(record)
    else:
        state.apply_up_to(records, at_ms)
    return state


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if len(args.inputs) > 2:
        print("ERROR: expected one combined file or two split files", file=sys.stderr)
        return 2
    if args.at_ms is not None and args.at_index is not None:
        print("ERROR: --at-ms and --at-index are mutually exclusive", file=sys.stderr)
        return 2

    try:
        raw = load_inputs(args.inputs)
    except FileNotFoundError as e:
        print(f"ERROR: file not found: {e.filename}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ERROR: could not load trace: {e}", file=sys.stderr)
        return 1

    if not raw.get("snapshot"):
        print("ERROR: snapshot not found in input", file=sys.stderr)
        return 1

    state = _build_state(raw, args.at_ms, args.at_index)
    html = state.to_html()

    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(html)
        except OSError as e:
            print(f"ERROR: could not write {args.out}: {e}", file=sys.stderr)
            return 1
    else:
        try:
            sys.stdout.buffer.write(html.encode("utf-8"))
            sys.stdout.buffer.write(b"\n")
        except BrokenPipeError:
            return 0

    if args.stats:
        if args.at_index is not None:
            point = f"index<={args.at_index}"
        elif args.at_ms is not None:
            point = f"t<={args.at_ms}ms"
        else:
            point = "all records"
        print(f"[domtrack html] rebuilt up to {point}; stats={state.stats}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
