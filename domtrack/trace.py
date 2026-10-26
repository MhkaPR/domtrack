"""Trace format helpers.

``domtrack`` stores captures in an `rrweb <https://rrweb.com/docs/>`_-style
shape:

``{ "meta": {...}, "snapshot": {...}, "records": [...], "events": [...] }``

* ``snapshot`` is a **FullSnapshot** — the serialized DOM tree where every
  node carries a numeric ``id``.
* ``records`` is the **incremental** stream. Each record only describes what
  changed, referencing nodes by ``id`` instead of re-embedding the whole
  subtree/text of the target.

Traces produced by ``domtrack`` < 2.0 (a flat ``"mutations"`` list where each
record embedded ``target.text`` / ``target.html`` / sibling HTML) are still
readable: :func:`load_raw` detects them and :func:`to_mutation_dicts`
normalises both shapes into the dict layout consumed by
``domtrack.replay.models.Mutation``.

See ``docs/trace-format.md`` for the full specification.
"""

from __future__ import annotations

import json
from typing import Any, Iterator, Optional


# ── rrweb constants ────────────────────────────────────────────────────────

class RecordType:
    """rrweb ``RecordType`` values used by domtrack."""

    FULL_SNAPSHOT = 2
    INCREMENTAL_SNAPSHOT = 3


class IncrementalSource:
    """rrweb ``IncrementalSource`` values used by domtrack."""

    MUTATION = 0


class NodeType:
    """rrweb ``NodeType`` values."""

    DOCUMENT = 0
    DOCUMENT_TYPE = 1
    ELEMENT = 2
    TEXT = 3
    COMMENT = 5


VOID_ELEMENTS = frozenset(
    {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }
)


# ── detection / loading ────────────────────────────────────────────────────

def is_rrweb_trace(raw: dict) -> bool:
    """True when ``raw`` uses the rrweb-style snapshot + records layout."""
    return "records" in raw or "snapshot" in raw


def load_raw(path: str) -> dict:
    """Read a trace file and return the raw JSON dict."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── node index & HTML reconstruction ───────────────────────────────────────

def index_snapshot(node: Optional[dict]) -> dict[int, dict]:
    """Flatten a serialized node tree into ``{node_id: node}``."""
    out: dict[int, dict] = {}

    def walk(n: Optional[dict]) -> None:
        if not isinstance(n, dict):
            return
        nid = n.get("id")
        if isinstance(nid, int):
            out[nid] = n
        for child in n.get("childNodes") or []:
            walk(child)

    walk(node)
    return out


def node_to_html(node: Optional[dict]) -> str:
    """Render a serialized node (and its children) back to an HTML string."""
    if not isinstance(node, dict):
        return ""
    ntype = node.get("type")
    if ntype == NodeType.TEXT:
        return _escape_text(node.get("textContent") or "")
    if ntype == NodeType.COMMENT:
        return f"<!--{node.get('textContent') or ''}-->"
    if ntype == NodeType.DOCUMENT_TYPE:
        name = node.get("name") or "html"
        return f"<!DOCTYPE {name}>"
    if ntype != NodeType.ELEMENT:
        return ""

    tag = (node.get("tagName") or "").lower()
    if tag.startswith("#"):
        return ""
    attrs = "".join(
        f' {k}="{_escape_attr(v)}"'
        for k, v in (node.get("attributes") or {}).items()
    )
    children = "".join(node_to_html(c) for c in node.get("childNodes") or [])
    if tag in VOID_ELEMENTS and not children:
        return f"<{tag}{attrs}/>"
    return f"<{tag}{attrs}>{children}</{tag}>"


def _escape_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(value: Any) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ── rrweb record -> replay mutation dict ───────────────────────────────────

def _target_from_ref(ref: Optional[dict]) -> Optional[dict]:
    if not ref:
        return None
    return {
        "tag": ref.get("tag"),
        "id": ref.get("id"),
        "selector": ref.get("selector"),
    }


def _node_info(node: Optional[dict], index: dict[int, dict]) -> Optional[dict]:
    """Build a replay ``NodeInfo`` dict (id/selector/html) from a serialized node.

    ``node`` is either a full serialized node (newly added element) or a
    bare ``{"id": n}`` reference to a node already present in the snapshot.
    """
    if not isinstance(node, dict):
        return None
    nid = node.get("id")
    resolved = node
    if node.get("type") is None and nid is not None and nid in index:
        resolved = index[nid]
    attrs = resolved.get("attributes") or {}
    node_id = attrs.get("id")
    selector = f"#{node_id}" if node_id else None
    return {"id": node_id, "selector": selector, "html": node_to_html(resolved)}


def _convert_record(rec: dict, index: dict[int, dict]) -> Optional[dict]:
    data = rec.get("data") or {}
    t = int(rec.get("t", 0))
    target = _target_from_ref(data.get("target"))

    rtype = data.get("type")
    attributes = data.get("attributes")
    texts = data.get("texts")
    adds = data.get("adds")
    removes = data.get("removes")

    if rtype == "attributes" or attributes:
        attr = attributes[0]
        return {
            "t": t,
            "type": "attributes",
            "target": target or {"tag": None, "id": None, "selector": None},
            "attributeName": attr.get("name"),
            "oldValue": attr.get("oldValue"),
            "newValue": attr.get("value"),
            "addedCount": 0,
            "removedCount": 0,
            "added": [],
            "removed": [],
        }

    if rtype == "characterData" or texts:
        text = texts[0]
        return {
            "t": t,
            "type": "characterData",
            "target": target or {"tag": None, "id": None, "selector": None},
            "oldValue": text.get("oldValue"),
            "newValue": text.get("value"),
            "addedCount": 0,
            "removedCount": 0,
            "added": [],
            "removed": [],
        }

    if rtype == "childList" or adds is not None or removes is not None:
        added = [n for n in (_node_info(a.get("node"), index) for a in (adds or [])) if n]
        removed = []
        for r in removes or []:
            nid = r.get("nodeId")
            removed.append(
                {
                    "id": r.get("id"),
                    "selector": r.get("selector"),
                    "html": node_to_html(index.get(nid)) if nid in index else None,
                }
            )
        return {
            "t": t,
            "type": "childList",
            "target": target or {"tag": None, "id": None, "selector": None},
            "addedCount": data.get("addedCount", len(added)),
            "removedCount": data.get("removedCount", len(removed)),
            "added": added,
            "removed": removed,
        }

    return None


def to_mutation_dicts(raw: dict) -> list[dict]:
    """Normalise a raw trace into replay ``Mutation`` dicts.

    Handles both the rrweb-style layout and the legacy flat ``mutations``
    layout, so old trace files keep replaying unchanged.
    """
    if not is_rrweb_trace(raw):
        return list(raw.get("mutations") or [])

    index = index_snapshot((raw.get("snapshot") or {}).get("node"))
    out: list[dict] = []
    for rec in raw.get("records") or []:
        if rec.get("type") != RecordType.INCREMENTAL_SNAPSHOT:
            continue
        data = rec.get("data") or {}
        if data.get("source") != IncrementalSource.MUTATION:
            continue
        conv = _convert_record(rec, index)
        if conv is not None:
            out.append(conv)
    return out


def iter_mutations(raw: dict) -> Iterator[dict]:
    """Yield replay ``Mutation`` dicts for ``raw`` (see :func:`to_mutation_dicts`)."""
    yield from to_mutation_dicts(raw)
