"""Unified ``domtrack`` command line.

``domtrack capture <url>``  → record a trace (delegates to
``domtrack.capture.cli``).

``domtrack replay <trace.json>`` → replay a trace (delegates to
``domtrack.replay.cli``).

Both subcommands accept the exact same flags as the standalone
``domtrack-capture`` / ``domtrack-replay`` entry points.
"""

from __future__ import annotations

import sys


USAGE = """domtrack <command> [options]

Commands:
  capture <url>          Record DOM mutations and user events from a page.
  replay  <trace.json>   Replay a captured trace on the live page.

Run `domtrack capture --help` or `domtrack replay --help` for options.
"""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0

    command, rest = args[0], args[1:]
    if command == "capture":
        from .capture.cli import main as capture_main
        return capture_main(rest)
    if command == "replay":
        from .replay.cli import main as replay_main
        return replay_main(rest)

    print(f"Unknown command: {command}\n", file=sys.stderr)
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
