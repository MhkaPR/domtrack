"""Unified ``domtrack`` command line.

``domtrack capture <url>``  → record a trace (delegates to
``domtrack.capture.cli``).

``domtrack replay <trace.json>`` → replay a trace (delegates to
``domtrack.replay.cli``).

``domtrack html <trace.json>`` → rebuild the DOM as HTML at a record point
(delegates to ``domtrack.html.cli``).

``domtrack diff <A.json> <B.json>`` → content-level diff of two captures
(delegates to ``domtrack.diff.cli``).

Each subcommand accepts the exact same flags as its standalone
``domtrack-*`` entry point.
"""

from __future__ import annotations

import sys


USAGE = """domtrack <command> [options]

Commands:
  capture <url>          Record DOM mutations and user events from a page.
  replay  <trace.json>   Replay a captured trace on the live page.
  html    <trace.json>   Rebuild the DOM as HTML at a record point (no browser).
  diff    <A> <B>        Compare two captures at the content level (no browser).

Run `domtrack <command> --help` for options.
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
    if command == "html":
        from .html.cli import main as html_main
        return html_main(rest)
    if command == "diff":
        from .diff.cli import main as diff_main
        return diff_main(rest)

    print(f"Unknown command: {command}\n", file=sys.stderr)
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
