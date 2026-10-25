from __future__ import annotations

import argparse
import sys

from .config import DEFAULT_DYNAMIC_CLASSES, TracerConfig
from .recorder import Recorder


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="trace_capture",
        description="Capture DOM mutations from a live page via Playwright.",
    )
    p.add_argument("url", help="URL to open and trace")
    p.add_argument("-s", "--selector", default=None,
                   help="CSS selector of subtree to observe (default: body)")
    p.add_argument("-o", "--output-dir", default="traces",
                   help="Directory for trace files (default: traces)")
    p.add_argument("--viewport-w", type=int, default=1440)
    p.add_argument("--viewport-h", type=int, default=900)
    p.add_argument("--headless", action="store_true",
                   help="Run headless (requires --duration)")
    p.add_argument("--duration", type=float, default=None,
                   help="Seconds to record before stopping (default: interactive pause)")
    p.add_argument("--timeout", type=int, default=360_000,
                   help="Navigation timeout in ms (default: 360000)")
    p.add_argument("--max-text", type=int, default=500)
    p.add_argument("--max-html", type=int, default=2000)
    p.add_argument("--max-nodes", type=int, default=50)
    p.add_argument("--dynamic-class", action="append", default=None,
                   help="Dynamic class name (repeatable; overrides defaults)")
    p.add_argument("--no-events", action="store_true",
               help="Disable event capture (mutations only)")
    p.add_argument("--no-mutations", action="store_true",
                help="Disable mutation capture (events only)")
    p.add_argument("--move-throttle-ms", type=int, default=40,
                help="Mousemove throttle in ms (default: 40 = ~25 Hz)")
    p.add_argument("--scroll-throttle-ms", type=int, default=100,
                help="Scroll throttle in ms (default: 100 = 10 Hz)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    duration_ms = None if args.duration is None else int(args.duration * 1000)
    dynamic_classes = (
        tuple(args.dynamic_class)
        if args.dynamic_class
        else DEFAULT_DYNAMIC_CLASSES
    )

    cfg = TracerConfig(
        url=args.url,
        target_selector=args.selector,
        viewport={"width": args.viewport_w, "height": args.viewport_h},
        headless=args.headless,
        goto_timeout_ms=args.timeout,
        max_text=args.max_text,
        max_html=args.max_html,
        max_nodes=args.max_nodes,
        dynamic_classes=dynamic_classes,
        duration_ms=duration_ms,
        output_dir=args.output_dir,
    )

    try:
        Recorder(cfg).run()
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"ERROR during capture: {e}", file=sys.stderr)
        return 1
    return 0