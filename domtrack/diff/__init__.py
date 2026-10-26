"""``domtrack diff`` — content-level diff between two captures of one page.

Browser-free: folds each trace with :class:`domtrack.trace.DomState`, reduces
it to a path-keyed content view, and compares text + data attributes.
"""

from .core import (
    NON_DATA_ATTRS,
    VOLATILE_ATTR_NAME,
    VOLATILE_TEXT,
    Change,
    ContentEntry,
    DiffResult,
    content_view,
    diff_traces,
    diff_views,
    direct_text,
    format_report,
    is_volatile_attr,
    is_volatile_text,
    iter_nodes,
    view_of,
)

__all__ = [
    "NON_DATA_ATTRS", "VOLATILE_ATTR_NAME", "VOLATILE_TEXT",
    "ContentEntry", "Change", "DiffResult",
    "iter_nodes", "direct_text", "content_view", "view_of",
    "diff_views", "diff_traces", "format_report",
    "is_volatile_attr", "is_volatile_text",
]
