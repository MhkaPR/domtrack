"""Column extraction with positional slot splitting."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Optional

from .config import DiscoveryConfig
from .labels import column_id, column_label, path_str
from .model import Atom, Node
from .policy import Interfaces


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


class ColumnExtractor:
    """Cluster per-row atoms into columns and split multi-atom columns by slot."""

    def __init__(self, config: DiscoveryConfig, interfaces: Interfaces) -> None:
        self.config = config
        self.interfaces = interfaces

    def extract(self, rows: list[Node]) -> list[dict]:
        config = self.config
        interfaces = self.interfaces
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
                        sub[0][1].path,
                        atype,
                        slot,
                        sub,
                        len(covered_rows),
                        total_rows,
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


def extract_columns(
    rows: list[Node], config: DiscoveryConfig, interfaces: Interfaces
) -> list[dict]:
    """Backwards-compatible function wrapper around :class:`ColumnExtractor`."""
    return ColumnExtractor(config, interfaces).extract(rows)
