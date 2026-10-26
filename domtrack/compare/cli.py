"""``domtrack compare`` — replay a trace, record the run, diff the result.

One command for the automation loop: replay a human trace on a fresh page,
record what the automated run produced (same capture format), and report the
content diff against the original.

    domtrack compare traces/before.json --speed 2 --json
"""

from __future__ import annotations

import argparse
import json
import sys

from ..replay.compare import ReplayComparer
from ..replay.recording import RecordingConfig
from ..replay.replayer import ReplayConfig
from ..replay.third_party import ThirdPartyDetector


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="domtrack compare",
        description="Replay a trace, record the run, and diff it against the "
                    "original capture (no manual capture needed).",
    )
    p.add_argument("trace_file", metavar="BEFORE.json",
                   help="Human-captured trace to replay and compare against")

    # Replay options (subset of `domtrack replay`).
    p.add_argument("-s", "--speed", type=float, default=1.0,
                   help="Replay speed multiplier (default: 1.0)")
    p.add_argument("-m", "--mode", choices=("all", "clean"), default="all",
                   help="'clean' skips third-party widgets during replay")
    p.add_argument("--replay-mode", choices=("events", "mutations", "both"),
                   default="events", help="What to replay (default: events)")
    p.add_argument("--time-policy",
                   choices=("faithful", "instant", "capped", "skip-aware"),
                   default="skip-aware", help="Timing strategy")
    p.add_argument("--gap-cap-ms", type=int, default=None,
                   help="Hard cap per wait (capped/skip-aware)")
    p.add_argument("--fast-load", action="store_true",
                   help="Skip networkidle wait; shorter initial delay")
    p.add_argument("--headless", action="store_true",
                   help="Run headless (browser invisible)")
    p.add_argument("--wait-before-replay-ms", type=int, default=0,
                   help="Pause after page load before replay")
    p.add_argument("--close-delay-ms", type=int, default=0,
                   help="Keep browser open after replay (default: 0)")
    p.add_argument("--skip-event", action="append", default=[], metavar="KIND",
                   help="Do not replay events of this kind (repeatable)")
    p.add_argument("--third-party-hint", action="append", default=[],
                   help="Extra third-party widget name (repeatable)")

    # Recording options.
    p.add_argument("-o", "--record-dir", default="traces",
                   help="Directory for the recorded replay trace (default: traces)")
    p.add_argument("--record-selector", default="body",
                   help="Observe only this subtree while recording (default: body)")
    p.add_argument("--record-events", action="store_true",
                   help="Also record user events in the replayed page")

    p.add_argument("--json", action="store_true",
                   help="Emit the diff as JSON (replay counts + content diff)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    replay_config = ReplayConfig(
        speed=args.speed,
        mode=args.mode,
        replay_mode=args.replay_mode,
        time_policy=args.time_policy,
        gap_cap_ms=args.gap_cap_ms,
        fast_load=args.fast_load,
        headless=args.headless,
        wait_before_replay_ms=args.wait_before_replay_ms,
        close_delay_ms=args.close_delay_ms,
        skip_events=tuple(args.skip_event),
        recording=RecordingConfig(
            enabled=True,
            output_dir=args.record_dir,
            target_selector=args.record_selector,
            capture_events=args.record_events,
        ),
    )
    detector = ThirdPartyDetector(hints=args.third_party_hint or None)

    try:
        result = ReplayComparer(
            args.trace_file,
            replay_config=replay_config,
            third_party_detector=detector,
        ).run()
    except FileNotFoundError as e:
        print(f"ERROR: file not found: {e.filename}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"ERROR during compare: {e}", file=sys.stderr)
        return 1

    if args.json:
        payload = result.diff.to_dict()
        payload["summary"] = result.summary
        payload["before"] = result.before_path
        payload["after"] = result.after_path
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print(f"Recorded replay trace: {result.after_path}")
        replay = result.stats
        print(f"Replay: applied={replay.applied} skipped={replay.skipped} "
              f"failed={replay.failed}")
        print()
        from ..diff.core import format_report

        try:
            format_report(result.diff, sys.stdout)
        except BrokenPipeError:
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
