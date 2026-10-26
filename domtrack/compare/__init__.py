"""``domtrack compare`` — replay + record + diff in one command.

Thin CLI over :class:`domtrack.replay.compare.ReplayComparer`.
"""

from .cli import build_parser, main

__all__ = ["build_parser", "main"]
