"""Table discovery: find repeated, tabular structures in a trace's DOM.

Browser-free. The trace is folded with :class:`domtrack.trace.DomState` —
the shared snapshot + incremental-record reconstructor — and adapted to a
light :class:`Node` view. A fixed algorithm then discovers *tables*: a
container whose children repeat with a similar structural shape, projected
into rows and columns.

Pluggable interfaces (the only site-specific knowledge)
-------------------------------------------------------

Four functions are supplied by the caller and carry every piece of
site-specific logic. Everything else is fixed.

``sig(A, B) -> path | None``
    Structural signature of ``B`` relative to root ``A``. ``path`` is any
    hashable value comparable with ``path_sim``. Returning ``None`` excludes
    ``B`` and its whole subtree. Default: the tuple of ``(tag, classes)`` from
    ``A`` down to ``B`` (``None`` for :data:`SKIP_TAGS` subtrees).

``values(B) -> list[(type, value)]``
    Extractable atoms of ``B``. Default: non-empty direct text plus the
    ``href`` / ``src`` / ``alt`` / ``title`` / ``value`` attributes. Text is
    attributed to the element that owns it, so text nodes yield no atoms.

``path_sim(p1, p2) -> [0, 1]``
    Similarity of two ``sig`` outputs. Default: mean of the component-wise
    Jaccard similarity, scaled by the length ratio.

``is_visible(B) -> bool``
    Whether ``B`` participates at all. Default: ``True``. A trace-aware
    predicate built from ``compute_ever_visible`` is available for callers that
    want to drop nodes that were never rendered.

The algorithm
-------------

Per document (state such as the ``covered`` set is per-document, never shared):

1. ``shape(A)`` collects the ``sig`` of every node under ``A`` that carries
   values — the structural fingerprint of a subtree.
2. ``is_grouping(c, parent)`` recognises a *row container* in a grid: its
   children are similar cells, none of which is itself a row container, and
   ``c`` has ≥ 2 similar siblings (i.e. it is one of several rows).
3. ``should_skip_as_row(node)`` recognises a *cell* of a larger repeated
   structure: ``node`` has ≥ ``skip_row_threshold`` similar siblings (counting
   itself), so the parent — not ``node`` — should form the table.
4. ``cluster_children(A)`` collects ``A``'s visible, uncovered children. When a
   child is a *grouping* (a row container), it is replaced by its children
   (the cells). Remaining candidates are grouped by shape (union-find,
   ``shape_threshold``).
5. ``extract_columns(rows)`` clusters the per-row atoms by ``path_sim`` (only
   atoms of the same ``type`` compare), keeps columns covering at least
   ``min_cover`` rows, and splits multi-atom columns into ``slot`` sub-columns.
6. ``build_table`` projects the rows and columns into the JSON table shape.
7. A discovered table marks its whole subtree ``covered``, so an ancestor can
   never re-detect it.
8. Nodes are processed bottom-up (post-order); each node emits at most one
   table (the largest valid cluster) and then stops. Cells of a larger repeated
   structure are skipped so their parent forms the table instead.

This means:

- Flat lists (footer links, chips, accordion items, shortcut rows) work as
  before: each flat ``<a>`` / ``<li>`` is a *cell* (skipped), the containing
  list is the *table*.
- Grids (a virtualised list of rows, each row containing N similar cards) are
  also supported: each *row* is a *grouping*, so the grid container clusters
  the **cards** directly, producing one large table instead of one table per
  row.

``discover_across_documents`` runs the above over many documents and returns
per-document tables plus aggregate stats; there is no cross-document memory.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Union

from ..trace import DomState, NodeType


# ── tree view ──────────────────────────────────────────────────────────────

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
    """Ids of every element node visible at least once during the trace.

    A node is visible if: it's visible in the initial snapshot, OR a record
    flips it hidden→visible, OR it's inserted with no hiding attributes, OR
    it descends from an ever-visible non-hidden node in the final tree.
    """
    ever_visible: set[int] = set()

    # 1. Initial snapshot.
    _walk_initial(initial_root, ever_visible, parent_hidden=False)

    # 2. Replay mutations.
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

    # 3. Propagate on the final tree (post-flip descendants).
    _propagate_descendants(final_root or initial_root, ever_visible)
    return ever_visible


def ever_visible_predicate(ids: Iterable[int]) -> Callable[[Node], bool]:
    """Build an ``is_visible`` from a set of ever-visible node ids."""
    allowed = set(ids)
    return lambda node: (not node.tag) or node.id in allowed


# ── atoms ──────────────────────────────────────────────────────────────────

@dataclass
class Atom:
    """One comparable value found in a row: text or a data attribute."""

    path: tuple
    atype: str
    value: str


_PLACEHOLDER_VALUES: frozenset[str] = frozenset(
    {"#", "javascript:void(0)", "javascript:;"}
)


# ── pluggable interfaces + defaults ────────────────────────────────────────

def default_values(node: Node) -> list[tuple[str, str]]:
    """Non-empty direct text plus ``href`` / ``src`` / ``alt`` / ``title`` /
    ``value`` atoms owned by ``node``.

    Text is attributed to the element that owns it; text nodes yield nothing.
    """
    if not node.tag:
        return []
    out: list[tuple[str, str]] = []
    direct = clean("".join(c.text for c in node.children if not c.tag))
    if direct:
        out.append(("text", direct))
    for attr, atype in VALUE_ATTRS:
        value = node.attrs.get(attr)
        if value and value not in _PLACEHOLDER_VALUES:
            out.append((atype, clean(value)))
    return out


def default_sig(root: Node, node: Node) -> Optional[tuple]:
    """``(tag, classes)`` from ``root`` down to ``node`` (``node`` inclusive).

    Returns ``None`` for nodes inside a skipped subtree (or outside ``root``),
    which excludes them and their subtrees from the algorithm.
    """
    if node.tag in SKIP_TAGS:
        return None
    chain: list[tuple[str, frozenset[str]]] = []
    current: Optional[Node] = node
    while current is not None:
        chain.append((current.tag, current.classes))
        if current is root:
            break
        current = current.parent
    if current is None:
        return None
    chain.reverse()
    return tuple(chain)


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 1.0
    union = len(a | b)
    return len(a & b) / union if union else 1.0


def component_similarity(c1: tuple, c2: tuple) -> float:
    tag1, set1 = c1
    tag2, set2 = c2
    return jaccard(set1, set2) if tag1 == tag2 else 0.0


def default_path_sim(p1: tuple, p2: tuple) -> float:
    """Mean of the component-wise Jaccard similarity × length ratio."""
    if not p1 or not p2:
        return 1.0 if p1 == p2 else 0.0
    depth = min(len(p1), len(p2))
    sims = [component_similarity(p1[-i], p2[-i]) for i in range(1, depth + 1)]
    return (sum(sims) / len(sims)) * (depth / max(len(p1), len(p2)))


# Backwards-compatible alias for the default similarity metric.
path_similarity = default_path_sim


def default_is_visible(node: Node) -> bool:
    return True


@dataclass(frozen=True)
class Interfaces:
    """The four resolved pluggable functions the algorithm runs on."""

    sig: Callable[[Node, Node], Optional[tuple]]
    values: Callable[[Node], list[tuple[str, str]]]
    path_sim: Callable[[tuple, tuple], float]
    is_visible: Callable[[Node], bool]


# ── structural shape of a subtree ──────────────────────────────────────────

def shape(root: Node, interfaces: Interfaces) -> frozenset:
    """The set of ``sig`` paths under ``root`` that carry values."""
    found: set = set()

    def walk(node: Node) -> None:
        path = interfaces.sig(root, node)
        if path is None:
            return
        if interfaces.values(node):
            found.add(path)
        for child in node.children:
            walk(child)

    walk(root)
    return frozenset(found)


def shape_similarity(s1: frozenset, s2: frozenset, path_sim: Callable) -> float:
    """Symmetric mean of the best-match ``path_sim`` between two shapes."""
    if not s1 and not s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    total, count = 0.0, 0
    for p in s1:
        total += max((path_sim(p, q) for q in s2), default=0.0)
        count += 1
    for q in s2:
        total += max((path_sim(p, q) for p in s1), default=0.0)
        count += 1
    return total / count if count else 0.0


def group_similarity(rows: list[Node], interfaces: Interfaces) -> float:
    """Mean pairwise shape similarity of a row cluster."""
    shapes = [shape(row, interfaces) for row in rows]
    sims = [
        shape_similarity(shapes[i], shapes[j], interfaces.path_sim)
        for i in range(len(rows))
        for j in range(i + 1, len(rows))
    ]
    return sum(sims) / len(sims) if sims else 1.0


# ── config ─────────────────────────────────────────────────────────────────

@dataclass
class DiscoveryConfig:
    """Tuning knobs plus the pluggable interfaces for :func:`discover`.

    ``sig`` / ``values`` / ``path_sim`` / ``is_visible`` default to the module
    implementations (``None`` -> default). ``ever_visible`` is a convenience:
    when set and ``is_visible`` is ``None``, an ever-visible predicate is built.

    ``grouping_min_values`` and ``skip_row_threshold`` control grid handling:

    - A *row container* (grid row) is a container whose children are similar
      cells, none of which is itself a row container, and which has at least
      two similar siblings. Set ``grouping_min_values`` to the minimum average
      number of values per cell required to treat the container as a row.
    - A *cell* is a node with at least ``skip_row_threshold`` similar siblings
      (including itself). Cells do not form their own tables; the parent does.
      For a 3-column grid, ``skip_row_threshold=3`` works. For a 2-column
      grid, use ``2``.

    Note: ``skip_row_threshold`` is intentionally conservative. With the
    default ``3``, a node with only one similar sibling is *not* skipped — it
    may still form a table of its own. This preserves existing behaviour for
    flat 2-item repeated structures (which are usually not grids). Raising
    the threshold makes discovery more aggressive about consolidating into
    parent tables; lowering it makes it more likely to emit sub-tables.
    """

    min_rows: int = 3
    min_cols: int = 2
    min_coverage_ratio: float = 0.5
    shape_threshold: float = 0.6
    path_threshold: float = 0.6
    dedupe: bool = True
    dedupe_threshold: float = 0.8

    # Grid-aware knobs.
    grouping_min_values: int = 1
    skip_row_threshold: int = 3

    # Pluggable, site-specific interfaces (None -> module default).
    sig: Optional[Callable[[Node, Node], Optional[tuple]]] = None
    values: Optional[Callable[[Node], list[tuple[str, str]]]] = None
    path_sim: Optional[Callable[[tuple, tuple], float]] = None
    is_visible: Optional[Callable[[Node], bool]] = None

    # Convenience: an ever-visible id set to build ``is_visible`` from.
    ever_visible: Optional[set[int]] = None

    # Legacy alias: ``threshold`` sets both shape and path thresholds.
    threshold: Optional[float] = None

    def __post_init__(self) -> None:
        if self.threshold is not None:
            self.shape_threshold = self.threshold
            self.path_threshold = self.threshold

    def interfaces(self) -> Interfaces:
        """Resolve the pluggable functions, filling in module defaults."""
        is_visible = self.is_visible
        if is_visible is None:
            if self.ever_visible is not None:
                is_visible = ever_visible_predicate(self.ever_visible)
            else:
                is_visible = default_is_visible
        return Interfaces(
            sig=self.sig or default_sig,
            values=self.values or default_values,
            path_sim=self.path_sim or default_path_sim,
            is_visible=is_visible,
        )


# ── grid helpers ───────────────────────────────────────────────────────────

def _value_count(node: Node, interfaces: Interfaces) -> int:
    """Total number of value atoms produced by ``node`` and its descendants."""
    total = len(interfaces.values(node))
    for child in node.children:
        total += _value_count(child, interfaces)
    return total


def _has_similar_children(
    node: Node, config: DiscoveryConfig, interfaces: Interfaces
) -> bool:
    """Whether ``node``'s children all share a similar shape."""
    kids = [
        k
        for k in node.children
        if interfaces.is_visible(k)
        and k.tag
        and interfaces.sig(node, k) is not None
    ]
    if len(kids) < 2:
        return False
    shapes = [shape(k, interfaces) for k in kids]
    base = shapes[0]
    return all(
        shape_similarity(base, s, interfaces.path_sim) >= config.shape_threshold
        for s in shapes[1:]
    )


def _count_similar_siblings(
    node: Node, config: DiscoveryConfig, interfaces: Interfaces
) -> int:
    """Number of ``node``'s siblings whose shape is similar to ``node``."""
    parent = node.parent
    if parent is None:
        return 0
    node_shape = shape(node, interfaces)
    if not node_shape:
        return 0
    count = 0
    for sib in parent.children:
        if sib.id == node.id:
            continue
        if not interfaces.is_visible(sib) or not sib.tag:
            continue
        if interfaces.sig(parent, sib) is None:
            continue
        sib_shape = shape(sib, interfaces)
        if (
            shape_similarity(node_shape, sib_shape, interfaces.path_sim)
            >= config.shape_threshold
        ):
            count += 1
    return count


def is_grouping(
    node: Node,
    parent: Optional[Node],
    config: DiscoveryConfig,
    interfaces: Interfaces,
) -> bool:
    """Whether ``node`` is a row container in a homogeneous grid.

    Conditions:

    1. ``node`` has at least two visible element children.
    2. Those children share a similar shape (a *row*).
    3. None of them is itself a row container (children are cells, not
       sub-rows — so ``node`` is the innermost row-container level).
    4. ``node`` has at least two similar siblings (it is one of several rows).
    5. The children carry on average at least ``grouping_min_values`` values.
    """
    kids = [
        k
        for k in node.children
        if interfaces.is_visible(k)
        and k.tag
        and interfaces.sig(node, k) is not None
    ]
    if len(kids) < 2:
        return False

    shapes = [shape(k, interfaces) for k in kids]
    base = shapes[0]
    if not all(
        shape_similarity(base, s, interfaces.path_sim) >= config.shape_threshold
        for s in shapes[1:]
    ):
        return False

    for kid in kids:
        if _has_similar_children(kid, config, interfaces):
            return False

    if _count_similar_siblings(node, config, interfaces) < 2:
        return False

    total_values = sum(_value_count(k, interfaces) for k in kids)
    if total_values / len(kids) < config.grouping_min_values:
        return False

    return True


def should_skip_as_row(
    node: Node, config: DiscoveryConfig, interfaces: Interfaces
) -> bool:
    """Whether ``node`` is a cell of a larger repeated structure.

    A node is a cell when it belongs to a group of at least
    ``skip_row_threshold`` similar siblings (including itself). Cells do not
    form their own tables — their common parent does.
    """
    similar = _count_similar_siblings(node, config, interfaces)
    return (1 + similar) >= config.skip_row_threshold


# ── cluster children by shape ──────────────────────────────────────────────

def cluster_children(
    parent: Node, config: DiscoveryConfig, interfaces: Interfaces, covered: set[int]
) -> list[list[Node]]:
    """Group ``parent``'s visible, uncovered children by shape (union-find).

    If a child is itself a *row container* (grid row), it is replaced by its
    children — so the grid container clusters the cells directly, not the
    rows.
    """
    elems: list[Node] = []
    for c in parent.children:
        if c.id in covered:
            continue
        if not interfaces.is_visible(c):
            continue
        if interfaces.sig(parent, c) is None:
            continue
        if is_grouping(c, parent, config, interfaces):
            for g in c.children:
                if g.id in covered:
                    continue
                if not interfaces.is_visible(g):
                    continue
                if interfaces.sig(parent, g) is None:
                    continue
                elems.append(g)
        else:
            elems.append(c)

    if len(elems) < config.min_rows:
        return []

    shapes = [shape(c, interfaces) for c in elems]
    n = len(elems)
    find = list(range(n))

    def root_of(x: int) -> int:
        while find[x] != x:
            find[x] = find[find[x]]
            x = find[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if (
                shape_similarity(shapes[i], shapes[j], interfaces.path_sim)
                >= config.shape_threshold
            ):
                ri, rj = root_of(i), root_of(j)
                if ri != rj:
                    find[ri] = rj

    groups: dict[int, list[Node]] = defaultdict(list)
    for i in range(n):
        groups[root_of(i)].append(elems[i])
    return [g for g in groups.values() if len(g) >= config.min_rows]


# ── build columns from a row cluster ───────────────────────────────────────

def _path_key(path: tuple) -> tuple:
    return tuple((tag, tuple(sorted(classes))) for tag, classes in path)


def _make_column(
    path: tuple,
    atype: str,
    slot: Optional[int],
    entries: list[tuple[int, Atom]],
    coverage: int,
    total_rows: int,
) -> dict:
    column = {
        "path": path,
        "atype": atype,
        "slot": slot,
        "entries": entries,
        "coverage": coverage,
        "coverage_ratio": coverage / total_rows if total_rows else 0.0,
    }
    column["id"] = column_id(column)
    column["label"] = column_label(column)
    column["path_str"] = path_str(path)
    return column


def extract_columns(
    rows: list[Node], config: DiscoveryConfig, interfaces: Interfaces
) -> list[dict]:
    """Cluster per-row atoms into columns (split multi-atom columns by slot)."""
    min_cover = max(
        config.min_cols, math.ceil(len(rows) * config.min_coverage_ratio)
    )

    row_atoms: list[list[Atom]] = []
    for row in rows:
        atoms: list[Atom] = []

        def collect(node: Node) -> None:
            path = interfaces.sig(row, node)
            if path is None:
                return
            for atype, value in interfaces.values(node):
                if value != "":
                    atoms.append(Atom(path, atype, value))
            for child in node.children:
                collect(child)

        collect(row)
        row_atoms.append(atoms)

    flat = [(i, atom) for i, atoms in enumerate(row_atoms) for atom in atoms]
    if not flat:
        return []

    # Union-find atoms by path similarity, only comparing the same atype.
    parent = list(range(len(flat)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    buckets: dict[str, list[int]] = defaultdict(list)
    for i, (_, atom) in enumerate(flat):
        buckets[atom.atype].append(i)

    for idxs in buckets.values():
        for ii in range(len(idxs)):
            for jj in range(ii + 1, len(idxs)):
                i, j = idxs[ii], idxs[jj]
                if (
                    interfaces.path_sim(flat[i][1].path, flat[j][1].path)
                    >= config.path_threshold
                ):
                    ri, rj = find(i), find(j)
                    if ri != rj:
                        parent[ri] = rj

    groups: dict[int, list[tuple[int, Atom]]] = defaultdict(list)
    for i in range(len(flat)):
        groups[find(i)].append(flat[i])

    total_rows = len(rows)
    columns: list[dict] = []

    for group in groups.values():
        row_set = {i for i, _ in group}
        if len(row_set) < min_cover:
            continue

        per_row: dict[int, list[Atom]] = defaultdict(list)
        for i, atom in group:
            per_row[i].append(atom)

        max_slots = max(len(v) for v in per_row.values())
        rep_path = Counter(a.path for _, a in group).most_common(1)[0][0]
        atype = group[0][1].atype

        if max_slots == 1:
            columns.append(
                _make_column(rep_path, atype, None, group, len(row_set), total_rows)
            )
            continue

        for slot in range(max_slots):
            sub = [
                (i, per_row[i][slot])
                for i in range(total_rows)
                if slot < len(per_row.get(i, []))
            ]
            covered_rows = {i for i, _ in sub}
            if len(covered_rows) < min_cover:
                continue
            columns.append(
                _make_column(
                    sub[0][1].path, atype, slot, sub, len(covered_rows), total_rows
                )
            )

    columns.sort(
        key=lambda c: (
            len(c["path"]),
            _path_key(c["path"]),
            c["slot"] if c["slot"] is not None else -1,
        )
    )
    return columns


# ── assemble the table record ──────────────────────────────────────────────

def build_table(
    parent: Node, rows: list[Node], columns: list[dict], avg_sim: float
) -> dict:
    """Project a container + its row cluster + columns into a JSON table."""
    table_rows: list[dict] = []
    for i in range(len(rows)):
        record: dict = {}
        for column in columns:
            hits = [a.value for ri, a in column["entries"] if ri == i]
            record[column["id"]] = hits[0] if hits else ""
        table_rows.append(record)

    score = len(columns) * math.sqrt(len(rows)) * avg_sim
    return {
        "parent": {
            "id": parent.id,
            "tag": parent.tag,
            "classes": sorted(parent.classes),
        },
        "meta": {
            "parent_tag": parent.tag,
            "parent_classes": sorted(parent.classes),
            "row_count": len(rows),
            "avg_sim": round(avg_sim, 3),
            "columns": len(columns),
        },
        "columns": [
            {
                "id": c["id"],
                "label": c["label"],
                "path": c["path_str"],
                "atype": c["atype"],
                "slot": c["slot"],
                "coverage": round(c["coverage_ratio"], 2),
                "row_count": c["coverage"],
            }
            for c in columns
        ],
        "rows": table_rows,
        "row_count": len(rows),
        "column_count": len(columns),
        "score": round(score, 3),
    }


# ── coverage lock ──────────────────────────────────────────────────────────

def mark_covered(node: Node, covered: set[int]) -> None:
    """Lock ``node`` and its whole subtree so ancestors can't re-detect it."""
    covered.add(node.id)
    for child in node.children:
        mark_covered(child, covered)


# ── per-node processing + bottom-up traversal ──────────────────────────────

def _discover(
    root: Node, config: DiscoveryConfig, interfaces: Interfaces
) -> tuple[list[dict], int]:
    """Run the algorithm on one document tree; return ``(tables, candidates)``."""
    if root is None:
        return [], 0

    tables: list[dict] = []
    covered: set[int] = set()
    candidates = 0

    def process(node: Node) -> None:
        nonlocal candidates
        if node.id in covered:
            return
        if not interfaces.is_visible(node):
            return
        if interfaces.sig(node, node) is None:
            return
        # A cell of a larger repeated structure never forms its own table.
        if should_skip_as_row(node, config, interfaces):
            return

        clusters = cluster_children(node, config, interfaces, covered)
        if not clusters:
            return
        candidates += len(clusters)
        clusters.sort(key=lambda g: (-len(g), min(c.id for c in g)))

        for cluster in clusters:
            columns = extract_columns(cluster, config, interfaces)
            if len(columns) < config.min_cols:
                continue
            avg_sim = group_similarity(cluster, interfaces)
            tables.append(build_table(node, cluster, columns, avg_sim))
            mark_covered(node, covered)
            return  # one table per node

    def post_order(node: Node) -> None:
        for child in node.children:
            post_order(child)
        process(node)

    post_order(root)
    return tables, candidates


def discover(root: Node, config: Optional[DiscoveryConfig] = None) -> list[dict]:
    """Discover the tables in one document tree (``DiscoverTables``)."""
    config = config or DiscoveryConfig()
    tables, _ = _discover(root, config, config.interfaces())
    return tables


# ── labels / paths ─────────────────────────────────────────────────────────

def best_class(classes: frozenset[str]) -> str:
    return sorted(classes, key=len, reverse=True)[0] if classes else ""


def path_str(path: tuple) -> str:
    return " > ".join(
        f"{tag}.{best_class(cls)}" if cls else tag for tag, cls in path
    )


def column_id(column: dict) -> str:
    base = f"{path_str(column['path'])}@{column['atype']}"
    slot = column.get("slot")
    return f"{base}#{slot}" if slot is not None else base


def column_label(column: dict) -> str:
    path = column["path"]
    if not path:
        return column["atype"]
    tag, classes = path[-1]
    base = best_class(classes) or tag
    label = base if column["atype"] == "text" else f"{base}.{column['atype']}"
    slot = column.get("slot")
    return f"{label}#{slot}" if slot is not None else label


# ── fingerprint + dedup ────────────────────────────────────────────────────

def row_identity(atoms: list[Atom]) -> tuple[str, str]:
    for atype in ("href", "src", "title", "alt", "text"):
        values = sorted(a.value for a in atoms if a.atype == atype)
        if values:
            return (atype, values[0])
    return ("empty", "")


def table_fingerprint(table: dict) -> frozenset:
    """Data fingerprint of a table: its non-empty ``(column_id, value)`` cells."""
    fingerprint = set()
    for row in table.get("rows") or []:
        for cid, value in row.items():
            if value:
                fingerprint.add((cid, value))
    return frozenset(fingerprint)


def fingerprint_similarity(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_subset_of(a: frozenset, b: frozenset, ratio: float = 0.85) -> bool:
    if not a:
        return False
    return len(a & b) / len(a) >= ratio


def dedupe(tables: list[dict], threshold: float) -> list[dict]:
    kept: list[dict] = []
    for table in sorted(tables, key=lambda t: -t["score"]):
        fingerprint = table["_fingerprint"]
        if not fingerprint:
            continue
        duplicate = False
        for other in kept:
            other_fp = other["_fingerprint"]
            if fingerprint_similarity(fingerprint, other_fp) >= threshold:
                duplicate = True
                break
            if is_subset_of(fingerprint, other_fp) or is_subset_of(other_fp, fingerprint):
                duplicate = True
                break
        if not duplicate:
            kept.append(table)
    return kept


# ── trace-aware entry points ───────────────────────────────────────────────

def discover_tables(
    raw: dict, config: Optional[DiscoveryConfig] = None
) -> dict:
    """Discover unique tables in a trace.

    Folds ``raw`` (snapshot + records) into a :class:`Node` tree and runs the
    algorithm. When neither ``is_visible`` nor ``ever_visible`` is supplied, the
    trace-aware ever-visible predicate is used, so nodes that were never
    rendered are dropped. Returns
    ``{"total_candidates", "tables_returned", "tables"}``.
    """
    config = config or DiscoveryConfig()

    # Single fold: keep the initial snapshot tree for visibility, then apply
    # all records for the final tree.
    state = DomState.from_trace(raw)
    initial_root = Node.from_serialized(state.root)
    if initial_root is None:
        raise ValueError("trace has no snapshot")

    records = raw.get("records") or []
    state.apply_up_to(records)
    final_root = Node.from_serialized(state.root)
    if final_root is None:
        raise ValueError("trace has no snapshot")

    if config.is_visible is None and config.ever_visible is None:
        config.ever_visible = compute_ever_visible(
            initial_root, records, final_root
        )

    tables, candidates = _discover(final_root, config, config.interfaces())

    if config.dedupe:
        for table in tables:
            table["_fingerprint"] = table_fingerprint(table)
        tables = dedupe(tables, config.dedupe_threshold)
    for table in tables:
        table.pop("_fingerprint", None)

    return {
        "total_candidates": candidates,
        "tables_returned": len(tables),
        "tables": tables,
    }


@dataclass
class Document:
    """One document to scan: an id plus its folded DOM tree."""

    id: str
    root: Node


def _resolve_document(
    document: Union[Document, Node, dict], config: DiscoveryConfig
) -> tuple[str, Node]:
    if isinstance(document, Document):
        return document.id, document.root
    if isinstance(document, Node):
        return str(document.id), document
    if isinstance(document, dict):
        if "root" in document:
            return str(document.get("id", "")), document["root"]
        trace = document.get("trace") or document
        doc_id = str(
            document.get("id") or (trace.get("meta") or {}).get("url") or ""
        )
        return doc_id, build_tree(trace)
    raise TypeError(f"unsupported document: {type(document).__name__}")


def discover_across_documents(
    documents: Iterable[Union[Document, Node, dict]],
    config: Optional[DiscoveryConfig] = None,
) -> dict:
    """Run :func:`discover` over many documents with per-document state.

    Returns ``{"stats", "all_tables"}`` where ``all_tables`` is a list of
    ``{"document_id", "tables"}``. There is no cross-document memory.
    """
    config = config or DiscoveryConfig()
    all_tables: list[dict] = []
    stats = {"docs": 0, "docs_with_tables": 0, "total_tables": 0}

    for document in documents:
        doc_id, root = _resolve_document(document, config)
        interfaces = config.interfaces()
        tables, _ = _discover(root, config, interfaces)
        if config.dedupe:
            for table in tables:
                table["_fingerprint"] = table_fingerprint(table)
            tables = dedupe(tables, config.dedupe_threshold)
        for table in tables:
            table.pop("_fingerprint", None)

        all_tables.append({"document_id": doc_id, "tables": tables})
        stats["docs"] += 1
        stats["total_tables"] += len(tables)
        if tables:
            stats["docs_with_tables"] += 1

    return {"stats": stats, "all_tables": all_tables}