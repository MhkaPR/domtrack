"""Discovery engine: shapes, row clustering, and table assembly."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable, Optional, Union

from ...trace import DomState
from .columns import ColumnExtractor
from .config import DiscoveryConfig
from .labels import dedupe, table_fingerprint
from .model import (
    Document,
    Node,
    build_tree,
    compute_ever_visible,
)
from .policy import Interfaces


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


def shape_similarity(s1: frozenset, s2: frozenset, path_sim) -> float:
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


# ── grid helpers ───────────────────────────────────────────────────────────

def _value_count(node: Node, interfaces: Interfaces) -> int:
    total = len(interfaces.values(node))
    for child in node.children:
        total += _value_count(child, interfaces)
    return total


def _has_similar_children(
    node: Node, config: DiscoveryConfig, interfaces: Interfaces
) -> bool:
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
    """Whether ``node`` is a row container in a homogeneous grid."""
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
    """Whether ``node`` is a cell of a larger repeated structure."""
    similar = _count_similar_siblings(node, config, interfaces)
    return (1 + similar) >= config.skip_row_threshold


# ── cluster children by shape ──────────────────────────────────────────────

def cluster_children(
    parent: Node, config: DiscoveryConfig, interfaces: Interfaces, covered: set[int]
) -> list[list[Node]]:
    """Group ``parent``'s visible, uncovered children by shape (union-find)."""
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


def mark_covered(node: Node, covered: set[int]) -> None:
    """Lock ``node`` and its whole subtree so ancestors can't re-detect it."""
    covered.add(node.id)
    for child in node.children:
        mark_covered(child, covered)


# ── orchestrator ───────────────────────────────────────────────────────────

class TableDiscoverer:
    """Library-first discovery facade.

    Inject a custom :class:`DiscoveryPolicy` through ``DiscoveryConfig``.
    """

    def __init__(self, config: Optional[DiscoveryConfig] = None) -> None:
        self.config = config or DiscoveryConfig()
        self._refresh()

    def _refresh(self) -> None:
        self.policy = self.config.resolved_policy()
        self.interfaces = Interfaces.from_policy(self.policy)
        self.column_extractor = ColumnExtractor(self.config, self.interfaces)

    def discover(self, root: Node) -> list[dict]:
        tables, _ = self._discover(root)
        return tables

    def _discover(self, root: Node) -> tuple[list[dict], int]:
        if root is None:
            return [], 0

        config = self.config
        interfaces = self.interfaces
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
            if should_skip_as_row(node, config, interfaces):
                return

            clusters = cluster_children(node, config, interfaces, covered)
            if not clusters:
                return
            candidates += len(clusters)
            clusters.sort(key=lambda g: (-len(g), min(c.id for c in g)))

            for cluster in clusters:
                columns = self.column_extractor.extract(cluster)
                if len(columns) < config.min_cols:
                    continue
                avg_sim = group_similarity(cluster, interfaces)
                tables.append(build_table(node, cluster, columns, avg_sim))
                mark_covered(node, covered)
                return

        def post_order(node: Node) -> None:
            for child in node.children:
                post_order(child)
            process(node)

        post_order(root)
        return tables, candidates

    def discover_tables(self, raw: dict) -> dict:
        """Discover unique tables in a trace."""
        config = self.config
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
            self._refresh()

        tables, candidates = self._discover(final_root)

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

    def discover_documents(
        self, documents: Iterable[Union[Document, Node, dict]]
    ) -> dict:
        """Run discovery over many documents with per-document state."""
        config = self.config
        all_tables: list[dict] = []
        stats = {"docs": 0, "docs_with_tables": 0, "total_tables": 0}

        for document in documents:
            doc_id, root = _resolve_document(document)
            tables, _ = self._discover(root)
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


# ── function entry points (backwards compatible) ───────────────────────────

def _discover(
    root: Node, config: DiscoveryConfig, interfaces: Interfaces
) -> tuple[list[dict], int]:
    """Run the algorithm on one document tree; return ``(tables, candidates)``."""
    discoverer = TableDiscoverer(config)
    discoverer.interfaces = interfaces
    discoverer.column_extractor = ColumnExtractor(config, interfaces)
    return discoverer._discover(root)


def discover(root: Node, config: Optional[DiscoveryConfig] = None) -> list[dict]:
    """Discover the tables in one document tree."""
    return TableDiscoverer(config).discover(root)


def discover_tables(raw: dict, config: Optional[DiscoveryConfig] = None) -> dict:
    """Discover unique tables in a trace."""
    return TableDiscoverer(config).discover_tables(raw)


def _resolve_document(document: Union[Document, Node, dict]) -> tuple[str, Node]:
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
    """Run discovery over many documents with per-document state."""
    return TableDiscoverer(config).discover_documents(documents)
