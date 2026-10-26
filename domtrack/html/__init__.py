"""``domtrack html`` — rebuild a trace's DOM as HTML at a record point.

Browser-free front-end for :class:`domtrack.trace.DomState`: fold a trace's
snapshot plus incremental records and serialize the HTML as it existed at a
chosen point. Public surface is the command-line ``build_parser`` / ``main``.
"""

from .cli import build_parser, main

__all__ = ["build_parser", "main"]
