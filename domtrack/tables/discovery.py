"""Table discovery: find repeated, tabular structures in a trace's DOM.

Browser-free. The trace is folded with :class:`domtrack.trace.DomState` —
the shared snapshot + incremental-record reconstructor — and adapted to a
light :class:`Node` view. The tree is then scanned for *candidate* containers
whose children repeat with a similar shape: the structural signature of a
table, list, or card grid.

Each candidate is scored, its columns are clustered from the per-row atoms
(text / href / src / …), and the surviving tables are de-duplicated by a
data fingerprint so overlapping containers collapse into one.

Multi-atom columns — like several ``<p class="about_host">`` siblings that
all cluster under one path — are split into ``slot`` sub-columns so that
every distinct value survives extraction (CPU, traffic, datacenter, annual
price, discount badge, …).

Visibility rule: a node is kept if it was **visible at least once** during
the recording (initial snapshot visible, hidden→visible flip, or inserted
with no hiding attributes). All visibility logic lives here — no external
module.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional

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
    ("title", "title"), ("value", "value"), ("small","small"), ("large", "large")
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


# ── visibility (self-contained) ────────────────────────────────────────────

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


# ── atoms ──────────────────────────────────────────────────────────────────

@dataclass
class Atom:
    """One comparable value found in a row: text or a data attribute."""

    path: tuple
    atype: str
    value: str


def collect_atoms(
    root: Node, ever_visible: Optional[set[int]] = None
) -> list[Atom]:
    """Collect every text / attribute atom under ``root``, in document order.

    When ``ever_visible`` is provided, subtrees whose root element was never
    visible are skipped. Nodes hidden in the final frame but visible earlier
    are kept.
    """
    out: list[Atom] = []

    def walk(node: Node, path: tuple) -> None:
        if skip_subtree(node):
            return
        if ever_visible is not None and node.tag and node.id not in ever_visible:
            return
        cur = path + ((node.tag, node.classes),) if node.tag else path
        if node.tag:
            direct = clean("".join(c.text for c in node.children if not c.tag))
            if direct:
                out.append(Atom(cur, "text", direct))

            has_elem_child = any(
                c.tag and not skip_subtree(c) for c in node.children
            )
            if not has_elem_child:
                full = clean(node.text)
                if full and full != direct:
                    out.append(Atom(cur, "text", full))

            for attr, atype in VALUE_ATTRS:
                value = node.attrs.get(attr)
                if value and value not in ("#", "javascript:void(0)", "javascript:;"):
                    out.append(Atom(cur, atype, clean(value)))
        for child in node.children:
            walk(child, cur)

    walk(root, ())
    return out


# ── similarity ─────────────────────────────────────────────────────────────

def jaccard(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 1.0
    union = len(a | b)
    return len(a & b) / union if union else 1.0


def component_similarity(c1: tuple, c2: tuple) -> float:
    tag1, set1 = c1
    tag2, set2 = c2
    return jaccard(set1, set2) if tag1 == tag2 else 0.0


def path_similarity(p1: tuple, p2: tuple) -> float:
    if not p1 or not p2:
        return 1.0 if p1 == p2 else 0.0
    depth = min(len(p1), len(p2))
    sims = [component_similarity(p1[-i], p2[-i]) for i in range(1, depth + 1)]
    return (sum(sims) / len(sims)) * (depth / max(len(p1), len(p2)))


def shape(
    node: Node,
    depth: int = 6,
    ever_visible: Optional[set[int]] = None,
) -> frozenset:
    """The set of leaf paths under ``node`` — its structural signature."""
    out: set = set()

    def walk(current: Node, path: tuple, level: int) -> None:
        if level > depth or skip_subtree(current):
            return
        if ever_visible is not None and current.tag and current.id not in ever_visible:
            return
        cur = path + ((current.tag, current.classes),) if current.tag else path
        has_child = any(c.tag and not skip_subtree(c) for c in current.children)
        if not has_child and (
            clean(current.text)
            or any(current.attrs.get(a) for a, _ in VALUE_ATTRS)
        ):
            out.add(cur)
        for child in current.children:
            walk(child, cur, level + 1)

    walk(node, (), 0)
    return frozenset(out)


def shape_similarity(s1: frozenset, s2: frozenset) -> float:
    if not s1 or not s2:
        return 1.0 if s1 == s2 else 0.0
    total, count = 0.0, 0
    for p in s1:
        total += max((path_similarity(p, q) for q in s2), default=0.0)
        count += 1
    for q in s2:
        total += max((path_similarity(p, q) for p in s1), default=0.0)
        count += 1
    return total / count if count else 0.0


# ── candidates ─────────────────────────────────────────────────────────────

@dataclass
class Candidate:
    """A container whose children repeat with a similar shape."""

    parent: Node
    rows: list[Node]
    avg_sim: float


def cluster_siblings(
    children: list[Node], config: "DiscoveryConfig"
) -> list[list[Node]]:
    """Group sibling nodes whose shapes are mutually similar (union-find).

    Invisible siblings are dropped first so hidden stubs don't drag visible
    rows into a false candidate.
    """
    ever_visible = config.ever_visible
    if ever_visible is not None:
        children = [c for c in children if not c.tag or c.id in ever_visible]

    n = len(children)
    if n < 2:
        return []

    shapes = [shape(c, ever_visible=ever_visible) for c in children]
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            if shape_similarity(shapes[i], shapes[j]) >= config.threshold:
                union(i, j)

    groups: dict[int, list[Node]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(children[i])
    return [g for g in groups.values() if len(g) >= config.min_group]


def find_candidates(root: Node, config: "DiscoveryConfig") -> list[Candidate]:
    """Scan the tree (post-order) for repeated-child containers."""
    out: list[Candidate] = []
    ever_visible = config.ever_visible

    def walk(node: Node) -> None:
        if ever_visible is not None and node.tag and node.id not in ever_visible:
            return
        for child in node.children:
            walk(child)
        if len(node.children) >= config.min_group:
            for group in cluster_siblings(node.children, config):
                shapes = [shape(c, ever_visible=ever_visible) for c in group]
                sims = [
                    shape_similarity(shapes[i], shapes[j])
                    for i in range(len(group))
                    for j in range(i + 1, len(group))
                ]
                out.append(
                    Candidate(node, group, sum(sims) / len(sims) if sims else 1.0)
                )

    walk(root)
    return out


# ── columns ────────────────────────────────────────────────────────────────

def _path_key(path: tuple) -> tuple:
    return tuple((tag, tuple(sorted(classes))) for tag, classes in path)


def build_columns(
    candidate: Candidate, config: "DiscoveryConfig"
) -> tuple[list[dict], list[list[Atom]]]:
    """Cluster per-row atoms into columns, then split multi-atom columns by
    document position (slot) so no value is silently dropped.

    Returns ``(columns, row_atoms)`` with each column carrying
    ``{path, atype, atoms, coverage, slot}``.
    """
    ever_visible = config.ever_visible
    row_atoms = [collect_atoms(row, ever_visible) for row in candidate.rows]
    flat = [(ri, a) for ri, atoms in enumerate(row_atoms) for a in atoms]
    if not flat:
        return [], row_atoms

    buckets: dict[str, list[int]] = defaultdict(list)
    for i, (_, atom) in enumerate(flat):
        buckets[atom.atype].append(i)

    parent = list(range(len(flat)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for idxs in buckets.values():
        for ii in range(len(idxs)):
            for jj in range(ii + 1, len(idxs)):
                i, j = idxs[ii], idxs[jj]
                if path_similarity(flat[i][1].path, flat[j][1].path) >= config.threshold:
                    union(i, j)

    groups: dict[int, list[tuple[int, Atom]]] = defaultdict(list)
    for i in range(len(flat)):
        groups[find(i)].append(flat[i])

    total_rows = len(candidate.rows)
    min_cover = max(2, total_rows * 0.5)
    columns: list[dict] = []

    for group in groups.values():
        covered = len({ri for ri, _ in group})
        if covered < min_cover:
            continue

        per_row: dict[int, list[Atom]] = defaultdict(list)
        for ri, atom in group:
            per_row[ri].append(atom)

        max_slots = max(len(v) for v in per_row.values())
        rep_path = Counter(a.path for _, a in group).most_common(1)[0][0]
        atype = group[0][1].atype

        if max_slots == 1:
            columns.append({
                "path": rep_path,
                "atype": atype,
                "atoms": group,
                "coverage": covered / total_rows,
                "slot": None,
            })
            continue

        for slot in range(max_slots):
            sub_atoms = [
                (ri, per_row[ri][slot])
                for ri in range(total_rows)
                if slot < len(per_row.get(ri, []))
            ]
            if len(sub_atoms) < min_cover:
                continue
            columns.append({
                "path": rep_path,
                "atype": atype,
                "atoms": sub_atoms,
                "coverage": len(sub_atoms) / total_rows,
                "slot": slot,
            })

    columns.sort(key=lambda c: (
        len(c["path"]),
        _path_key(c["path"]),
        c["slot"] if c["slot"] is not None else -1,
    ))
    return columns, row_atoms


# ── scoring ────────────────────────────────────────────────────────────────

def positional_bonus(candidate: Candidate) -> float:
    node = candidate.parent
    while node:
        if node.tag == "main":
            return 1.5
        if node.tag in ("header", "footer", "nav"):
            return 0.5
        node = node.parent
    return 1.0


def score_candidate(candidate: Candidate, columns: list[dict]) -> float:
    if not columns:
        return 0.0
    return (
        len(columns)
        * (len(candidate.rows) ** 0.5)
        * candidate.avg_sim
        * (sum(c["coverage"] for c in columns) / len(columns))
        * positional_bonus(candidate)
    )


# ── labels ─────────────────────────────────────────────────────────────────

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


# ── extraction ─────────────────────────────────────────────────────────────

def extract_table(
    candidate: Candidate, columns: list[dict], row_atoms: list[list[Atom]]
) -> dict:
    """Project a candidate + its columns into the JSON table shape."""
    used = {id(atom) for column in columns for _, atom in column["atoms"]}
    rows: list[dict] = []
    for ri, atoms in enumerate(row_atoms):
        row: dict = {}
        for column in columns:
            hits = [a for r, a in column["atoms"] if r == ri]
            row[column_id(column)] = hits[0].value if hits else ""
        row["_extra"] = {
            f"{path_str(a.path)}@{a.atype}": a.value
            for a in atoms
            if id(a) not in used
        }
        rows.append(row)
    return {
        "meta": {
            "parent_tag": candidate.parent.tag,
            "parent_classes": sorted(candidate.parent.classes),
            "row_count": len(candidate.rows),
            "avg_sim": round(candidate.avg_sim, 3),
            "columns": len(columns),
        },
        "columns": [
            {
                "id": column_id(c),
                "label": column_label(c),
                "path": path_str(c["path"]),
                "atype": c["atype"],
                "coverage": round(c["coverage"], 2),
            }
            for c in columns
        ],
        "rows": rows,
    }


# ── fingerprint + dedup ────────────────────────────────────────────────────

def row_identity(atoms: list[Atom]) -> tuple[str, str]:
    for atype in ("href", "src", "title", "alt", "text"):
        values = sorted(a.value for a in atoms if a.atype == atype)
        if values:
            return (atype, values[0])
    return ("empty", "")


def candidate_fingerprint(
    candidate: Candidate, ever_visible: Optional[set[int]] = None
) -> frozenset:
    fingerprint = set()
    for row in candidate.rows:
        ident = row_identity(collect_atoms(row, ever_visible))
        if ident[1]:
            fingerprint.add(ident)
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


# ── public entry point ─────────────────────────────────────────────────────

@dataclass
class DiscoveryConfig:
    """Tuning knobs for :func:`discover_tables`."""

    threshold: float = 0.6
    min_group: int = 3
    min_rows: int = 3
    min_cols: int = 2
    min_avg_sim: float = 0.6
    dedupe: bool = True
    dedupe_threshold: float = 0.8
    ever_visible: Optional[set[int]] = None   # populated by discover_tables


def discover_tables(
    raw: dict, config: Optional[DiscoveryConfig] = None
) -> dict:
    """Discover unique tables in a trace.

    Folds ``raw``, computes the ever-visible set, scans for repeated-child
    containers, scores and extracts them, then de-duplicates. Returns
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

    if config.ever_visible is None:
        config.ever_visible = compute_ever_visible(
            initial_root, records, final_root
        )

    candidates = find_candidates(final_root, config)

    evaluated = []
    for candidate in candidates:
        columns, row_atoms = build_columns(candidate, config)
        score = score_candidate(candidate, columns)
        evaluated.append((candidate, score, columns, row_atoms))
    evaluated.sort(key=lambda item: -item[1])

    tables: list[dict] = []
    for candidate, score, columns, row_atoms in evaluated:
        if score <= 0:
            continue
        if len(candidate.rows) < config.min_rows:
            continue
        if candidate.avg_sim < config.min_avg_sim:
            continue
        if len(columns) < config.min_cols:
            continue
        table = extract_table(candidate, columns, row_atoms)
        table["score"] = round(score, 3)
        table["_fingerprint"] = candidate_fingerprint(
            candidate, config.ever_visible
        )
        tables.append(table)

    if config.dedupe:
        tables = dedupe(tables, config.dedupe_threshold)
    for table in tables:
        table.pop("_fingerprint", None)

    return {
        "total_candidates": len(candidates),
        "tables_returned": len(tables),
        "tables": tables,
    }