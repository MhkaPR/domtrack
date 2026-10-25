from __future__ import annotations

import argparse
import sys

from .models import Trace
from .replayer import Replayer, ReplayConfig
from .third_party import ThirdPartyDetector


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="trace_replay",
        description="Replay a MutationObserver trace on a live page.",
    )
    p.add_argument("trace_file", help="Path to trace JSON file")
    p.add_argument("-s", "--speed", type=float, default=1.0,
                   help="Speed multiplier (default: 1.0)")
    p.add_argument("-m", "--mode", choices=("all", "clean"), default="all",
                   help="clean skips third-party widgets")
    p.add_argument("--headless", action="store_true",
                   help="Run headless (no visible browser)")
    p.add_argument("--wait-for-ids-ms", type=int, default=2000,
                   help="Max time to wait for target IDs on load")
    p.add_argument("--close-delay-ms", type=int, default=5000,
                   help="Keep browser open after replay before closing")
    p.add_argument("--third-party-hint", action="append", default=[],
                   help="Extra third-party widget name (repeatable)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        trace = Trace.load(args.trace_file)
    except FileNotFoundError:
        print(f"ERROR: file not found: {args.trace_file}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ERROR: could not load trace: {e}", file=sys.stderr)
        return 1

    if not trace.meta.url:
        print("ERROR: URL not found in trace", file=sys.stderr)
        return 1

    print(f"File      : {args.trace_file}")
    print(f"URL       : {trace.meta.url}")
    print(f"Mutations : {len(trace.mutations)} | speed={args.speed}x | mode={args.mode}\n")

    config = ReplayConfig(
        speed=args.speed,
        mode=args.mode,
        headless=args.headless,
        wait_for_ids_ms=args.wait_for_ids_ms,
        close_delay_ms=args.close_delay_ms,
    )
    detector = ThirdPartyDetector(hints=args.third_party_hint or None)
    replayer = Replayer(trace, config=config, third_party_detector=detector)

    try:
        replayer.run()
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        return 130
    except Exception as e:
        print(f"ERROR during replay: {e}", file=sys.stderr)
        return 1

    replayer.print_report()
    return 0