"""domtrack — capture DOM mutations + user events, then replay them.

Two halves, each importable on its own:

* :mod:`domtrack.capture` — drive a real browser, record an rrweb-style
  trace (full snapshot + incremental records) plus user events.
* :mod:`domtrack.replay` — feed that trace back into a fresh page with
  Playwright, either as real interactions or as forced DOM patches.

Top-level names are resolved lazily (PEP 562) so that importing the package
never drags in Playwright until you actually need it.
"""

from __future__ import annotations

from . import config, storage, trace
from .config import TracerConfig
from .trace import (
    DomState,
    IncrementalSource,
    NodeType,
    RecordType,
    index_snapshot,
    is_rrweb_trace,
    load_raw,
    node_to_html,
    rebuild_html,
    render_node,
    to_mutation_dicts,
)

__all__ = [
    "config", "storage", "trace",
    "TracerConfig",
    "RecordType", "IncrementalSource", "NodeType",
    "DomState", "render_node", "rebuild_html",
    "load_raw", "is_rrweb_trace", "index_snapshot",
    "node_to_html", "to_mutation_dicts",
    "capture", "replay",
]

__version__ = "2.0.0"


def __getattr__(name: str):
    # Lazy subpackage access: `domtrack.capture` / `domtrack.replay`.
    if name == "capture":
        from . import capture
        return capture
    if name == "replay":
        from . import replay
        return replay
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
