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

import copy
import json
from typing import Any, Iterable, Iterator, Optional


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


def render_node(node: Optional[dict]) -> str:
    """Render one serialized / reconstructed node subtree to an HTML string.

    Unlike :func:`node_to_html` this also renders a document root (``type 0``)
    by concatenating its children, which is what :class:`DomState` needs.
    """
    if not isinstance(node, dict):
        return ""
    ntype = node.get("type")
    if ntype == NodeType.DOCUMENT:
        return "".join(render_node(c) for c in node.get("childNodes") or [])
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
    children = "".join(render_node(c) for c in node.get("childNodes") or [])
    if tag in VOID_ELEMENTS and not children:
        return f"<{tag}{attrs}/>"
    return f"<{tag}{attrs}>{children}</{tag}>"


def node_to_html(node: Optional[dict]) -> str:
    """Render a serialized node (and its children) back to an HTML string."""
    return render_node(node)


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


# ── DOM reconstruction (snapshot + incremental records -> HTML) ────────────

class DomState:
    """A mutable DOM tree rebuilt from a snapshot plus incremental records.

    Feed it the ``FullSnapshot`` once, then every ``RecordType.INCREMENTAL_SNAPSHOT``
    record in order; :meth:`to_html` renders the state at whatever point you
    stopped. It reuses this module's :func:`render_node` serializer, so
    reconstruction and single-node rendering stay byte-for-byte consistent.

    >>> state = DomState.from_trace(trace.load_raw("trace.json"))
    >>> state.apply_up_to(trace.load_raw("trace.json")["records"], at_ms=1200)
    >>> html = state.to_html()
    """

    __slots__ = ("root", "mirror", "parents", "clock", "stats")

    def __init__(self, snapshot: Optional[dict] = None):
        self.root: Optional[dict] = None
        self.mirror: dict[int, dict] = {}
        self.parents: dict[int, Optional[dict]] = {}
        self.clock: int = 0
        self.stats: dict[str, int] = self._empty_stats()
        if snapshot is not None:
            self.load_snapshot(snapshot)

    # ── construction ──────────────────────────────────────────────────────

    @classmethod
    def from_trace(cls, raw: dict) -> "DomState":
        """Build a state seeded with ``raw``'s ``FullSnapshot``."""
        snap = raw.get("snapshot")
        node = snap.get("node") if isinstance(snap, dict) else snap
        return cls(node)

    def load_snapshot(self, snapshot: Optional[dict]) -> None:
        """Seed the tree from a ``FullSnapshot`` record, doc node, or ``None``."""
        if isinstance(snapshot, dict):
            node = snapshot.get("node", snapshot)
        else:
            node = snapshot
        self.root = copy.deepcopy(node) if node else None
        self.mirror.clear()
        self.parents.clear()
        self._register(self.root, None)

    def _register(self, node: Optional[dict], parent: Optional[dict]) -> None:
        if not isinstance(node, dict):
            return
        nid = node.get("id")
        if isinstance(nid, int):
            self.mirror[nid] = node
            self.parents[nid] = parent
        for child in node.get("childNodes") or []:
            self._register(child, node)

    def _unregister(self, node: dict) -> None:
        for child in node.get("childNodes") or []:
            self._unregister(child)
        nid = node.get("id")
        if isinstance(nid, int):
            self.mirror.pop(nid, None)
            self.parents.pop(nid, None)

    # ── mutation application ──────────────────────────────────────────────

    def apply(self, record: dict) -> None:
        """Apply one incremental-mutation record to the tree."""
        if record.get("type") != RecordType.INCREMENTAL_SNAPSHOT:
            return
        data = record.get("data") or {}
        if data.get("source") != IncrementalSource.MUTATION:
            return
        self.clock = int(record.get("t", self.clock))
        rtype = data.get("type")
        if rtype == "attributes":
            self._apply_attributes(data)
        elif rtype == "characterData":
            self._apply_character_data(data)
        elif rtype == "childList":
            self._apply_child_list(data)

    def apply_up_to(self, records: Iterable[dict], at_ms: Optional[int] = None) -> None:
        """Apply records in order, stopping after ``at_ms`` when given.

        With ``at_ms=None`` every record is applied. Records are expected in
        the trace's own (already time-ordered) order.
        """
        for record in records:
            if at_ms is not None and int(record.get("t", 0)) > at_ms:
                break
            self.apply(record)

    @classmethod
    def at(cls, raw: dict, at_ms: Optional[int] = None) -> "DomState":
        """Rebuild ``raw``'s DOM only up to ``at_ms`` (all records when ``None``)."""
        state = cls.from_trace(raw)
        state.apply_up_to(raw.get("records") or [], at_ms)
        return state

    def _target(self, data: dict, key: str = "nodeId") -> Optional[dict]:
        ref = data.get("target")
        nid = ref.get(key) if isinstance(ref, dict) else None
        if nid is None:
            return None
        return self.mirror.get(nid)

    def _apply_attributes(self, data: dict) -> None:
        node = self._target(data)
        if node is None:
            self.stats["missing_targets"] += 1
            return
        attrs = node.setdefault("attributes", {})
        for attr in data.get("attributes") or []:
            name = attr.get("name")
            if not name:
                continue
            value = attr.get("value")
            if value is None:
                attrs.pop(name, None)
            else:
                attrs[name] = value
            self.stats["attributes"] += 1

    def _apply_character_data(self, data: dict) -> None:
        texts = data.get("texts") or []
        nid = texts[0].get("id") if texts else None
        node = self.mirror.get(nid) if nid is not None else None
        if node is None:
            node = self._target(data)
        if node is None:
            self.stats["missing_targets"] += 1
            return
        value = texts[0].get("value") if texts else data.get("text")
        if value is not None:
            node["textContent"] = value
            node.pop("childNodes", None)
            self.stats["text"] += 1

    def _apply_child_list(self, data: dict) -> None:
        for remove in data.get("removes") or []:
            self._apply_remove(remove)
        for add in data.get("adds") or []:
            self._apply_add(add)

    def _apply_remove(self, remove: dict) -> None:
        parent = self.mirror.get(remove.get("parentId"))
        node = self.mirror.get(remove.get("nodeId"))
        if parent is None or node is None:
            self.stats["missing_targets"] += 1
            return
        children = parent.get("childNodes")
        if children and node in children:
            children.remove(node)
            self._detach(node)
            self.stats["removes"] += 1
        if remove.get("isParent"):
            self._unregister(node)

    def _apply_add(self, add: dict) -> None:
        parent = self.mirror.get(add.get("parentId"))
        if parent is None:
            self.stats["missing_targets"] += 1
            return
        node = self._resolve(add.get("node") or {})
        if node is None:
            self.stats["missing_targets"] += 1
            return

        # Already in place -> nothing to do (keeps childList replays idempotent).
        if self.parents.get(node.get("id")) is parent:
            self.stats["skipped_duplicates"] += 1
            return

        self._detach(node)
        children = parent.setdefault("childNodes", [])
        next_node = self.mirror.get(add.get("nextId"))
        if next_node is not None and next_node in children:
            children.insert(children.index(next_node), node)
        else:
            children.append(node)
        self.parents[node.get("id")] = parent
        self.stats["adds"] += 1

    def _detach(self, node: dict) -> None:
        old = self.parents.get(node.get("id"))
        if old is not None:
            siblings = old.get("childNodes") or []
            if node in siblings:
                siblings.remove(node)
        self.parents[node.get("id")] = None

    def _resolve(self, data: dict) -> Optional[dict]:
        """Return an existing node for a bare ``{id}`` ref, else build a subtree."""
        nid = data.get("id")
        if isinstance(nid, int) and nid in self.mirror:
            return self.mirror[nid]
        if "type" not in data and "tagName" not in data:
            return None
        node = copy.deepcopy(data)
        self._register(node, None)
        return node

    # ── rendering ─────────────────────────────────────────────────────────

    def to_html(self) -> str:
        """Render the current tree to an HTML string."""
        return render_node(self.root)

    @staticmethod
    def _empty_stats() -> dict[str, int]:
        return {
            "attributes": 0, "text": 0, "adds": 0, "removes": 0,
            "skipped_duplicates": 0, "missing_targets": 0,
        }


def rebuild_html(raw: dict, at_ms: Optional[int] = None) -> str:
    """Rebuild the HTML of a trace's DOM at a given record point.

    Starts from ``raw["snapshot"]`` and replays ``raw["records"]`` up to
    ``at_ms`` (milliseconds; every record when ``None``), then serializes.

    >>> html = rebuild_html(trace.load_raw("trace.json"), at_ms=1200)
    """
    return DomState.at(raw, at_ms).to_html()


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
