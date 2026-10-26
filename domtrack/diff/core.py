"""Content-level diff between two captures of the same page.

Browser-free: each side is folded from its ``FullSnapshot`` plus incremental
records with :class:`domtrack.trace.DomState`, reduced to a path-keyed
*content view* (text + data attributes), and the two views are compared.

The path scheme is ``/html[0]/body[0]/ul[0]/li[2]/h2[0]``: a slash-joined chain
of ``tag[index]`` segments, where the index counts same-tag siblings.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Optional

from ..trace import DomState


# ── content filters ────────────────────────────────────────────────────────

NON_DATA_ATTRS: frozenset[str] = frozenset(
    {
        "class", "style", "id",
        "width", "height", "tabindex", "role", "draggable", "translate",
        "spellcheck", "crossorigin", "integrity", "async", "defer", "loading",
        "rel", "aria-hidden", "aria-expanded", "aria-pressed",
        "aria-controls", "aria-describedby", "aria-labelledby",
        "data-contact-form-bound", "data-contact-form-observer",
    }
)

VOLATILE_ATTR_NAME: tuple[re.Pattern[str], ...] = (
    re.compile(r"nonce$", re.I),
    re.compile(r"csrf", re.I),
    re.compile(r"token", re.I),
    re.compile(r"session", re.I),
    re.compile(r"^value$"),
    re.compile(r"^data-(gtm|ga|analytics|track)", re.I),
)

VOLATILE_TEXT: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*$"),
    re.compile(r"^\d{2}:\d{2}(:\d{2})?\s*$"),
)


def is_volatile_attr(name: str) -> bool:
    """True for attribute names that carry no comparable content."""
    return any(p.search(name) for p in VOLATILE_ATTR_NAME)


def is_volatile_text(text: str) -> bool:
    """True for text that carries no comparable content (blank, clock)."""
    return any(p.match(text) for p in VOLATILE_TEXT)


# ── tree walking ───────────────────────────────────────────────────────────

def iter_nodes(
    node: Optional[dict], path: str = "", counters: Optional[dict[str, int]] = None
) -> Iterator[tuple[dict, str, str]]:
    """Yield ``(node, path, tag)`` for every node in ``node``'s subtree."""
    if not isinstance(node, dict):
        return
    if counters is None:
        counters = defaultdict(int)
    tag = (node.get("tagName") or "").lower()
    my_path = path
    if tag:
        key = f"{path}/{tag}"
        index = counters[key]
        counters[key] += 1
        my_path = f"{key}[{index}]"
    yield node, my_path, tag
    for child in node.get("childNodes") or []:
        yield from iter_nodes(child, my_path, counters)


def direct_text(node: dict) -> str:
    """Concatenate the node's direct child text nodes (type 3)."""
    return "".join(
        (c.get("textContent") or "")
        for c in (node.get("childNodes") or [])
        if c.get("type") == 3
    ).strip()


# ── content view ───────────────────────────────────────────────────────────

@dataclass
class ContentEntry:
    node_id: Optional[int]
    tag: str
    text: str
    attrs: dict[str, str] = field(default_factory=dict)

    def to_dict(self, path: str) -> dict:
        return {"path": path, "nodeId": self.node_id, "tag": self.tag,
                "text": self.text, "attrs": self.attrs}


def content_view(root: Optional[dict]) -> dict[str, ContentEntry]:
    """Reduce a reconstructed DOM tree to ``{path: ContentEntry}``.

    Only elements that carry non-volatile direct text are kept; the ``attrs``
    of each entry are the data-bearing attributes left after the filters.
    """
    view: dict[str, ContentEntry] = {}
    for node, path, tag in iter_nodes(root):
        if not tag:
            continue
        text = direct_text(node)
        if not text or is_volatile_text(text):
            continue
        attrs = {
            k: v for k, v in (node.get("attributes") or {}).items()
            if k not in NON_DATA_ATTRS and not is_volatile_attr(k)
        }
        view[path] = ContentEntry(
            node_id=node.get("id"), tag=tag, text=text, attrs=attrs
        )
    return view


def view_of(
    raw: dict, at_ms: Optional[int] = None, apply_records: bool = True
) -> dict[str, ContentEntry]:
    """Fold a trace and return its content view.

    ``apply_records=False`` uses only the initial snapshot; ``at_ms`` caps the
    records that are folded in (see :meth:`domtrack.trace.DomState.apply_up_to`).
    """
    state = DomState.from_trace(raw)
    if apply_records:
        state.apply_up_to(raw.get("records") or [], at_ms)
    return content_view(state.root)


# ── diff ───────────────────────────────────────────────────────────────────

@dataclass
class Change:
    path: str
    tag: str
    old_text: Optional[str] = None
    new_text: Optional[str] = None
    attrs: dict[str, dict[str, Optional[str]]] = field(default_factory=dict)

    @property
    def text_changed(self) -> bool:
        return self.old_text != self.new_text


@dataclass
class DiffResult:
    a: dict[str, ContentEntry]
    b: dict[str, ContentEntry]
    added: list[tuple[str, ContentEntry]] = field(default_factory=list)
    removed: list[tuple[str, ContentEntry]] = field(default_factory=list)
    changed: list[Change] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        return {
            "a_nodes": len(self.a),
            "b_nodes": len(self.b),
            "added": len(self.added),
            "removed": len(self.removed),
            "changed": len(self.changed),
        }

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "added": [v.to_dict(p) for p, v in self.added],
            "removed": [v.to_dict(p) for p, v in self.removed],
            "changed": [
                {
                    "path": c.path,
                    "tag": c.tag,
                    "text": ({"old": c.old_text, "new": c.new_text}
                             if c.text_changed else None),
                    "attrs": c.attrs,
                }
                for c in self.changed
            ],
        }


def diff_views(
    a: dict[str, ContentEntry], b: dict[str, ContentEntry]
) -> DiffResult:
    """Compare two content views."""
    result = DiffResult(a=a, b=b)
    result.added = [(p, b[p]) for p in b if p not in a]
    result.removed = [(p, a[p]) for p in a if p not in b]
    for path, entry_a in a.items():
        entry_b = b.get(path)
        if entry_b is None:
            continue
        attrs: dict[str, dict[str, Optional[str]]] = {}
        for key in set(entry_a.attrs) | set(entry_b.attrs):
            old, new = entry_a.attrs.get(key), entry_b.attrs.get(key)
            if old != new:
                attrs[key] = {"old": old, "new": new}
        if entry_a.text != entry_b.text or attrs:
            result.changed.append(
                Change(
                    path=path,
                    tag=entry_a.tag,
                    old_text=entry_a.text,
                    new_text=entry_b.text,
                    attrs=attrs,
                )
            )
    return result


def diff_traces(
    raw_a: dict,
    raw_b: dict,
    at_ms_a: Optional[int] = None,
    at_ms_b: Optional[int] = None,
    apply_records: bool = True,
) -> DiffResult:
    """Diff two traces at the content level (snapshot + records, no browser)."""
    return diff_views(
        view_of(raw_a, at_ms=at_ms_a, apply_records=apply_records),
        view_of(raw_b, at_ms=at_ms_b, apply_records=apply_records),
    )


# ── human-readable report ──────────────────────────────────────────────────

def format_report(result: DiffResult, out) -> None:
    """Write the classic text report for ``result`` to ``out``."""
    s = result.summary
    out.write(f"nodes with text/data:  A={s['a_nodes']}  B={s['b_nodes']}\n")
    out.write(
        f"added: {s['added']}   removed: {s['removed']}   "
        f"changed: {s['changed']}\n\n"
    )

    for title, items in (("ADDED (only in B)", result.added),
                         ("REMOVED (only in A)", result.removed)):
        if not items:
            continue
        out.write(f"## {title}\n")
        for path, entry in items:
            line = f"  {path}  <{entry.tag}>  {entry.text[:120]!r}"
            if entry.attrs:
                line += f"  {entry.attrs}"
            out.write(line + "\n")
        out.write("\n")

    if result.changed:
        out.write("## CHANGED\n")
        for c in result.changed:
            out.write(f"  ~ {c.path}  <{c.tag}>\n")
            if c.text_changed:
                out.write(f"      text:  {c.old_text[:120]!r}\n")
                out.write(f"          →  {c.new_text[:120]!r}\n")
            for key in sorted(c.attrs):
                pair = c.attrs[key]
                out.write(f"      attr {key}: {pair['old']!r} → {pair['new']!r}\n")
            out.write("\n")
