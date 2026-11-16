"""Column extraction with positional slot splitting."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Optional

import numpy as np
from sklearn.cluster import AgglomerativeClustering

from .config import DiscoveryConfig
from .features import (
    _ATOM_WEIGHTS,
    FeatureVectorizer,
    column_features,
    node_features,
    value_types,
)
from .labels import column_id, column_label, path_str
from .model import Atom, Node
from .policy import Interfaces


def _path_key(path: tuple) -> tuple:
    return tuple((tag, tuple(sorted(classes))) for tag, classes in path)


def _ensure_unique_column_ids(columns: list[dict]) -> None:
    """Disambiguate columns that share path + atype + slot.

    Different semantic clusters can come from the same DOM path (for example
    screen size, CPU and RAM all being ``p.text-gray-700``).  ``build_table``
    keys cells by column id, so duplicate ids would silently overwrite each
    other.  Keep every cluster and append a deterministic suffix instead.
    """
    seen: dict[str, int] = {}
    for column in columns:
        base = column["id"]
        count = seen.get(base, 0)
        seen[base] = count + 1
        if count:
            column["id"] = f"{base}#{count + 1}"


def _is_struck_price_group(group: list[tuple[int, Atom]]) -> bool:
    """True for an old-price column (``line-through`` numeric values)."""
    if not group:
        return False
    path = group[0][1].path
    if not any("line-through" in classes for _, classes in path):
        return False
    values = [atom.value for _, atom in group]
    numeric = sum(1 for value in values if value_types(value)["is_numeric"])
    return numeric >= max(1, len(values) // 2)


def _merge_same_path_groups(
    groups: list[list[tuple[int, Atom]]],
    atom_order: dict[int, int],
) -> list[list[tuple[int, Atom]]]:
    """Recombine clusters that came from the same DOM path.

    Repeated siblings such as ``p.text-gray-700`` (screen, CPU, RAM) are one
    positional list.  Clustering may split them semantically, but they must be
    slot-split by position instead of becoming colliding columns.
    """
    merged: dict[tuple, list[tuple[int, Atom]]] = {}
    order: list[tuple] = []
    for group in groups:
        if not group:
            continue
        key = (group[0][1].path, group[0][1].atype)
        if key not in merged:
            merged[key] = []
            order.append(key)
        merged[key].extend(group)
    result = []
    for key in order:
        group = merged[key]
        group.sort(
            key=lambda item: (item[0], atom_order.get(id(item[1]), 0))
        )
        result.append(group)
    return result


def _make_column(
    path: tuple,
    atype: str,
    slot: Optional[int],
    entries: list[tuple[int, Atom]],
    coverage: int,
    total_rows: int,
    features: Optional[dict] = None,
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
    if features is not None:
        column["features"] = features
    return column


def _column_feature_dict(
    entries: list[tuple[int, Atom]], total_rows: int, atype: str
) -> dict:
    values = [atom.value for _, atom in entries]
    node_feats = [atom.features for _, atom in entries]
    positions = [row for row, _ in entries]
    return column_features(
        values,
        node_feats=node_feats,
        total_rows=total_rows,
        atype=atype,
        positions=positions,
    )


def _cluster_atoms_complete_linkage(
    vectors: np.ndarray,
    path_sim_matrix: np.ndarray,
    distance_threshold: float,
    path_weight: float,
) -> np.ndarray:
    """Cluster atoms with complete-linkage.

    Library: sklearn.cluster.AgglomerativeClustering.
    Guarantee: ∀C, ∀a,b ∈ C : distance(a,b) ≤ distance_threshold.
    """
    if len(vectors) <= 1:
        return np.zeros(len(vectors), dtype=int)

    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    unit = vectors / norms

    cosine_sim = unit @ unit.T
    combined_sim = (
        (1.0 - path_weight) * cosine_sim + path_weight * path_sim_matrix
    )
    distance = np.clip(1.0 - combined_sim, 0.0, None)
    np.fill_diagonal(distance, 0.0)
    distance = (distance + distance.T) / 2.0

    model = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=distance_threshold,
        linkage="complete",
        metric="precomputed",
    )
    return model.fit_predict(distance)


def _is_pure(
    cluster_atoms: list[tuple[int, Atom]],
    total_rows: int,
    atype: str,
    config: DiscoveryConfig,
) -> bool:
    feats = _column_feature_dict(cluster_atoms, total_rows, atype)
    if feats["type_entropy"] > config.max_type_entropy:
        return False
    if (
        feats["discovered_units"]
        and feats["unit_consistency"] < config.min_unit_consistency
    ):
        return False
    tags = Counter(a.node.tag for _, a in cluster_atoms if a.node is not None)
    if tags:
        top_ratio = tags.most_common(1)[0][1] / sum(tags.values())
        if top_ratio < config.min_tag_majority:
            return False
    return True


def _cluster_pure(
    indices: list[int],
    vectors: np.ndarray,
    path_sim_matrix: np.ndarray,
    threshold: float,
    config: DiscoveryConfig,
    flat: list[tuple[int, Atom]],
    total_rows: int,
    atype: str,
) -> list[list[tuple[int, Atom]]]:
    """Cluster one atype bucket and split impure clusters with a halved τ."""
    if len(indices) <= 1:
        return [[flat[i] for i in indices]]

    sub_vectors = vectors[indices]
    sub_path_sim = path_sim_matrix[np.ix_(indices, indices)]
    labels = _cluster_atoms_complete_linkage(
        sub_vectors, sub_path_sim, threshold, config.path_feature_weight
    )

    groups: list[list[tuple[int, Atom]]] = []
    for label in sorted(set(labels)):
        cluster_indices = [
            indices[k] for k in range(len(indices)) if labels[k] == label
        ]
        cluster_atoms = [flat[i] for i in cluster_indices]
        if len(cluster_indices) <= 1 or _is_pure(
            cluster_atoms, total_rows, atype, config
        ):
            groups.append(cluster_atoms)
        elif threshold / 2.0 >= 0.05:
            groups.extend(
                _cluster_pure(
                    cluster_indices,
                    vectors,
                    path_sim_matrix,
                    threshold / 2.0,
                    config,
                    flat,
                    total_rows,
                    atype,
                )
            )
        else:
            groups.append(cluster_atoms)
    return groups


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
                        atoms.append(
                            Atom(
                                path,
                                atype,
                                value,
                                node=node,
                                features={
                                    **node_features(
                                        node, root=row, atype=atype
                                    ),
                                    **value_types(value, atype),
                                },
                            )
                        )
                for child in node.children:
                    collect(child)

            collect(row)
            row_atoms.append(atoms)

        flat = [(i, atom) for i, atoms in enumerate(row_atoms) for atom in atoms]
        if not flat:
            return []

        buckets: dict[str, list[int]] = defaultdict(list)
        for i, (_, atom) in enumerate(flat):
            buckets[atom.atype].append(i)

        total_rows = len(rows)

        # 1. Build weighted feature vectors.
        atom_feature_dicts = [
            {**atom.features, **value_types(atom.value, atom.atype)}
            for _, atom in flat
        ]
        vectorizer = FeatureVectorizer(
            "atom", weights=_ATOM_WEIGHTS
        ).fit(atom_feature_dicts)
        vectors = vectorizer.transform(atom_feature_dicts)

        # 2. Build the soft path_sim matrix.
        n = len(flat)
        path_sim_matrix = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                if flat[i][1].atype != flat[j][1].atype:
                    continue
                score = interfaces.path_sim(
                    flat[i][1].path, flat[j][1].path
                )
                path_sim_matrix[i, j] = path_sim_matrix[j, i] = score

        # 3. Cluster per atype bucket with complete-linkage.
        groups: list[list[tuple[int, Atom]]] = []
        for atype, idxs in buckets.items():
            groups.extend(
                _cluster_pure(
                    idxs,
                    vectors,
                    path_sim_matrix,
                    config.distance_threshold,
                    config,
                    flat,
                    total_rows,
                    atype,
                )
            )

        atom_order = {id(atom): i for i, (_, atom) in enumerate(flat)}
        groups = _merge_same_path_groups(groups, atom_order)

        columns: list[dict] = []

        for group in groups:
            row_set = {i for i, _ in group}
            keep_low_coverage = _is_struck_price_group(group)
            if len(row_set) < min_cover and not keep_low_coverage:
                continue

            per_row: dict[int, list[Atom]] = defaultdict(list)
            for i, atom in group:
                per_row[i].append(atom)

            max_slots = max(len(v) for v in per_row.values())
            rep_path = Counter(a.path for _, a in group).most_common(1)[0][0]
            atype = group[0][1].atype

            if max_slots == 1:
                features = _column_feature_dict(group, total_rows, atype)
                columns.append(
                    _make_column(
                        rep_path,
                        atype,
                        None,
                        group,
                        len(row_set),
                        total_rows,
                        features,
                    )
                )
                continue

            for slot in range(max_slots):
                sub = [
                    (i, per_row[i][slot])
                    for i in range(total_rows)
                    if slot < len(per_row.get(i, []))
                ]
                covered_rows = {i for i, _ in sub}
                if len(covered_rows) < min_cover and not keep_low_coverage:
                    continue
                features = _column_feature_dict(sub, total_rows, atype)
                columns.append(
                    _make_column(
                        sub[0][1].path,
                        atype,
                        slot,
                        sub,
                        len(covered_rows),
                        total_rows,
                        features,
                    )
                )

        columns.sort(
            key=lambda c: (
                len(c["path"]),
                _path_key(c["path"]),
                c["slot"] if c["slot"] is not None else -1,
            )
        )
        _ensure_unique_column_ids(columns)
        return columns


def extract_columns(
    rows: list[Node], config: DiscoveryConfig, interfaces: Interfaces
) -> list[dict]:
    """Backwards-compatible function wrapper around :class:`ColumnExtractor`."""
    return ColumnExtractor(config, interfaces).extract(rows)
