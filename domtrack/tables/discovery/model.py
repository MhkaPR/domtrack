"""Light DOM model and trace folding for table discovery."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from ...trace import DomState, NodeType


SKIP_TAGS: frozenset[str] = frozenset(
    {
        "script", "style", "noscript", "svg", "path", "defs", "use",
        "symbol", "canvas", "template", "meta", "link", "head", "title",
    }
)

VALUE_ATTRS: tuple[tuple[str, str], ...] = (
    ("href", "href"), ("src", "src"), ("alt", "alt"),
    ("title", "title"), ("value", "value"),
)

_WS = re.compile(r"\s+")


def clean(text: Optional[str]) -> str:
    """Collapse whitespace and strip ``text``."""
    return _WS.sub(" ", text or "").strip()


@dataclass
class Node:
    """A light, mutable view of one serialized DOM node."""

    id: int
    tag: str = ""
    attrs: dict[str, str] = field(default_factory=dict)
    text: str = ""
    children: list["Node"] = field(default_factory=list, repr=False)
    parent: Optional["Node"] = field(default=None, repr=False)

    @property
    def classes(self) -> frozenset[str]:
        return frozenset((self.attrs.get("class") or "").split())

    @classmethod
    def from_serialized(cls, raw: Optional[dict]) -> Optional["Node"]:
        if not isinstance(raw, dict):
            return None
        node = cls(id=int(raw.get("id", 0)))
        ntype = raw.get("type")
        if ntype == NodeType.ELEMENT:
            node.tag = (raw.get("tagName") or "").lower()
            node.attrs = dict(raw.get("attributes") or {})
        elif ntype == NodeType.TEXT:
            node.text = raw.get("textContent") or ""
        for child in raw.get("childNodes") or []:
            built = cls.from_serialized(child)
            if built is not None:
                built.parent = node
                node.children.append(built)
        return node


def build_tree(raw: dict) -> Node:
    """Fold ``raw`` (snapshot + records) and adapt it to a :class:`Node` tree."""
    state = DomState.from_trace(raw)
    state.apply_up_to(raw.get("records") or [])
    root = Node.from_serialized(state.root)
    if root is None:
        raise ValueError("trace has no snapshot")
    return root


def skip_subtree(node: Node) -> bool:
    return node.tag in SKIP_TAGS


# ── visibility (a ready-made, trace-aware ``is_visible``) ───────────────────

_HIDDEN_CLASS_TOKENS: frozenset[str] = frozenset({"d-none", "hidden", "hide"})
_HIDDEN_STYLE_TOKENS: tuple[str, ...] = ("display:none", "visibility:hidden")
_VISIBILITY_ATTRS: frozenset[str] = frozenset({"class", "style", "hidden"})


def is_hidden_by_attrs(attrs: Optional[dict[str, str]]) -> bool:
    """True when ``attrs`` alone hide the element (ancestors not checked)."""
    if not attrs:
        return False
    style = (attrs.get("style") or "").replace(" ", "").lower()
    if any(tok in style for tok in _HIDDEN_STYLE_TOKENS):
        return True
    classes = (attrs.get("class") or "").split()
    if _HIDDEN_CLASS_TOKENS.intersection(classes):
        return True
    if "hidden" in attrs:
        return True
    return False


def _walk_initial(node: Node, ever_visible: set[int], parent_hidden: bool) -> None:
    hidden = parent_hidden or is_hidden_by_attrs(node.attrs)
    if not hidden and node.tag:
        ever_visible.add(node.id)
    for child in node.children:
        _walk_initial(child, ever_visible, hidden)


def _parse_attr_flip(record: dict) -> Optional[tuple[int, bool, bool]]:
    """Return ``(node_id, was_hidden, now_hidden)`` for a visibility flip."""
    data = record.get("data") or {}
    if data.get("type") != "attributes":
        return None
    node_id = (data.get("target") or {}).get("nodeId")
    if node_id is None:
        return None

    was_hidden = False
    now_hidden = False
    touched = False
    for attr in data.get("attributes") or []:
        name = attr.get("name")
        if name not in _VISIBILITY_ATTRS:
            continue
        touched = True
        old_val = attr.get("oldValue")
        new_val = attr.get("value")
        if name in ("class", "style"):
            if old_val and is_hidden_by_attrs({name: old_val}):
                was_hidden = True
            if new_val and is_hidden_by_attrs({name: new_val}):
                now_hidden = True
        elif name == "hidden":
            was_hidden = old_val is not None
            now_hidden = new_val is not None
    return (int(node_id), was_hidden, now_hidden) if touched else None


def _mark_subtree_visible(
    raw_node: dict, ever_visible: set[int], parent_hidden: bool = False
) -> None:
    if not isinstance(raw_node, dict):
        return
    if raw_node.get("type") != NodeType.ELEMENT:
        return
    attrs = raw_node.get("attributes") or {}
    hidden = parent_hidden or is_hidden_by_attrs(attrs)
    if not hidden:
        ever_visible.add(int(raw_node["id"]))
    for child in raw_node.get("childNodes") or []:
        _mark_subtree_visible(child, ever_visible, hidden)


def _apply_child_list(record: dict, ever_visible: set[int]) -> None:
    data = record.get("data") or {}
    parent_id = (data.get("target") or {}).get("nodeId")
    parent_hidden = parent_id is not None and int(parent_id) not in ever_visible
    for add in data.get("adds") or []:
        _mark_subtree_visible(add.get("node") or {}, ever_visible, parent_hidden)


def _mark_visible_descendants(node: Node, ever_visible: set[int]) -> None:
    for child in node.children:
        if child.tag and not is_hidden_by_attrs(child.attrs):
            ever_visible.add(child.id)
            _mark_visible_descendants(child, ever_visible)


def _propagate_descendants(root: Node, ever_visible: set[int]) -> None:
    """Descendants of any ever-visible node (if not hidden) are also visible."""
    def walk(node: Node) -> None:
        if node.tag and node.id in ever_visible:
            _mark_visible_descendants(node, ever_visible)
        for child in node.children:
            walk(child)

    walk(root)


def compute_ever_visible(
    initial_root: Node,
    records: Iterable[dict],
    final_root: Optional[Node] = None,
) -> set[int]:
    """Ids of every element node visible at least once during the trace."""
    ever_visible: set[int] = set()

    _walk_initial(initial_root, ever_visible, parent_hidden=False)

    for record in records:
        rtype = (record.get("data") or {}).get("type")
        if rtype == "attributes":
            flip = _parse_attr_flip(record)
            if flip is None or flip[0] in ever_visible:
                continue
            _, was_hidden, now_hidden = flip
            if was_hidden and not now_hidden:
                ever_visible.add(flip[0])
        elif rtype == "childList":
            _apply_child_list(record, ever_visible)

    _propagate_descendants(final_root or initial_root, ever_visible)
    return ever_visible


def ever_visible_predicate(ids: Iterable[int]) -> Callable[[Node], bool]:
    """Build an ``is_visible`` from a set of ever-visible node ids."""
    allowed = set(ids)
    return lambda node: (not node.tag) or node.id in allowed


@dataclass
class Atom:
    """One comparable value found in a row: text or a data attribute."""

    path: tuple
    atype: str
    value: str


@dataclass
class Document:
    """One document to scan: an id plus its folded DOM tree."""

    id: str
    root: Node
