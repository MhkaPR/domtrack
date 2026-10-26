#!/usr/bin/env python3
"""
build_html.py — rebuild the DOM at any point of a domtrack trace.

A trace is a FullSnapshot plus an incremental record stream. This demo uses
`domtrack.trace.DomState` to fold that stream into a live tree and serialize
the HTML *as it existed at a chosen record point* — no browser needed.

Usage:
  python build_html.py combined.json [--at-ms 1200] [--out final.html]
  python build_html.py snapshot.json records.json [--at-ms 1200]

  --at-ms   stop after this many ms since capture start (default: all records)
  --out     write HTML to a file instead of stdout

Input shape (see docs/trace-format.md):
  { "snapshot": {"type": 2, "node": {...}}, "records": [...], "events": [...] }
"""

from __future__ import annotations

import argparse
import sys

from domtrack import trace as tf


def load_inputs(paths: list[str]) -> dict:
    """Return a trace dict from either one combined file or two split files."""
    if len(paths) == 1:
        raw = tf.load_raw(paths[0])
        if not tf.is_rrweb_trace(raw):
            raise SystemExit("Error: input is not an rrweb-style trace "
                             "(need 'snapshot' + 'records').")
        return raw

    snapshot = tf.load_raw(paths[0])
    records = tf.load_raw(paths[1])
    if isinstance(snapshot, dict) and "snapshot" in snapshot:
        snapshot = snapshot["snapshot"]
    if isinstance(records, dict):
        records = records.get("records") or []
    return {"snapshot": snapshot, "records": records}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Rebuild a trace's DOM as HTML at a given record point.",
    )
    p.add_argument("inputs", nargs="+", metavar="FILE",
                   help="combined trace, or <snapshot.json> <records.json>")
    p.add_argument("--at-ms", type=int, default=None,
                   help="rebuild only up to this ms since capture start")
    p.add_argument("--out", default=None,
                   help="write HTML to this file (default: stdout)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if len(args.inputs) > 2:
        raise SystemExit("Error: expected one combined file or two split files.")

    raw = load_inputs(args.inputs)
    if not raw.get("snapshot"):
        raise SystemExit("Error: snapshot not found in input.")

    state = tf.DomState.from_trace(raw)
    state.apply_up_to(raw.get("records") or [], args.at_ms)

    html = state.to_html()
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(html)
    else:
        try:
            sys.stdout.buffer.write(html.encode("utf-8"))
            sys.stdout.buffer.write(b"\n")
        except BrokenPipeError:
            return 0

    point = "all records" if args.at_ms is None else f"t<={args.at_ms}ms"
    print(f"[build_html] applied up to {point}; stats={state.stats}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
