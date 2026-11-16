"""Column extraction with order-preserving slot alignment."""

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
from .features import cosine_similarity



class SequenceAligner:
    """Align atom sequences to stable, ordered slot profiles.

    - Uses monotonic dynamic programming for atom-to-slot alignment.
    - Uses Fisher scores only when enough complete rows are available.
    - Estimates thresholds using leave-one-out profiles.
    - Inserts new slots only when the PTA neighbor constraint is satisfied.
    - Never falls back to positional assignment.
    - Preserves unmatched atoms in `pending`.
    """

    def __init__(self, threshold: float) -> None:
        self.fallback_threshold = 1.0 - threshold
        self.ambiguous = False
        self.pending: list[tuple[int, Atom]] = []

    @staticmethod
    def _cosine(left: np.ndarray, right: np.ndarray) -> float:
        if left.shape != right.shape or left.size == 0:
            return 0.0

        if not np.all(np.isfinite(left)) or not np.all(np.isfinite(right)):
            return 0.0

        left_norm = np.linalg.norm(left)
        right_norm = np.linalg.norm(right)

        if left_norm <= 1e-12 or right_norm <= 1e-12:
            return 0.0

        return float(np.dot(left, right) / (left_norm * right_norm))

    @staticmethod
    def _profiles(
        assignments: list[list[Optional[Atom]]],
    ) -> list[np.ndarray]:
        if not assignments:
            return []

        template = next(
            (
                atom.vector
                for row in assignments
                for atom in row
                if atom is not None
            ),
            None,
        )

        if template is None:
            return []

        width = len(assignments[0])
        profiles = []

        for slot in range(width):
            vectors = [
                atom.vector
                for row in assignments
                if (atom := row[slot]) is not None
            ]

            if vectors:
                profiles.append(np.mean(vectors, axis=0))
            else:
                profiles.append(np.zeros_like(template))

        return profiles

    @staticmethod
    def _complete_rows(
        assignments: list[list[Optional[Atom]]],
    ) -> list[list[Atom]]:
        if not assignments or not assignments[0]:
            return []

        return [
            row
            for row in assignments
            if all(atom is not None for atom in row)
        ]

    def _weights(
        self,
        assignments: list[list[Optional[Atom]]],
    ) -> np.ndarray:
        complete = self._complete_rows(assignments)

        if not complete:
            self.ambiguous = True
            width = len(assignments[0]) if assignments else 0
            return np.ones(
                len(next(
                    atom.vector
                    for row in assignments
                    for atom in row
                    if atom is not None
                )) if width else 0
            )

        if len(complete) < 3:
            self.ambiguous = True
            return np.ones_like(complete[0][0].vector)

        slot_count = len(complete[0])

        by_slot = [
            np.vstack([row[slot].vector for row in complete])
            for slot in range(slot_count)
        ]

        all_vectors = np.vstack(by_slot)
        center = np.mean(all_vectors, axis=0)

        between = np.zeros_like(center)
        within = np.zeros_like(center)

        for vectors in by_slot:
            slot_mean = np.mean(vectors, axis=0)

            between += len(vectors) * (slot_mean - center) ** 2

            within += np.sum(
                (vectors - slot_mean) ** 2,
                axis=0,
            )

        scores = np.divide(
            between,
            within,
            out=np.zeros_like(between),
            where=within > 1e-12,
        )

        # A dimension with different slot means but zero within-slot
        # variance is highly discriminative.
        scores[(within <= 1e-12) & (between > 1e-12)] = 10.0

        positive = scores[scores > 0]

        if not positive.size:
            self.ambiguous = True
            return np.ones_like(scores)

        scores /= float(np.mean(positive))

        return np.clip(scores, 0.0, 10.0)

    def _threshold(
        self,
        assignments: list[list[Optional[Atom]]],
        weights: np.ndarray,
    ) -> float:
        complete = self._complete_rows(assignments)

        if len(complete) < 2:
            self.ambiguous = True
            return self.fallback_threshold

        root = np.sqrt(weights)

        own: list[float] = []
        other: list[float] = []

        # Leave-one-out: never compare an atom against a profile
        # containing that same atom.
        for row_index, row in enumerate(complete):
            other_rows = [
                candidate
                for index, candidate in enumerate(complete)
                if index != row_index
            ]

            if not other_rows:
                continue

            for slot, atom in enumerate(row):
                own_profile = np.mean(
                    [
                        candidate[slot].vector
                        for candidate in other_rows
                    ],
                    axis=0,
                )

                own_score = self._cosine(
                    atom.vector * root,
                    own_profile * root,
                )

                own.append(own_score)

                for other_slot in range(len(row)):
                    if other_slot == slot:
                        continue

                    other_profile = np.mean(
                        [
                            candidate[other_slot].vector
                            for candidate in other_rows
                        ],
                        axis=0,
                    )

                    other.append(
                        self._cosine(
                            atom.vector * root,
                            other_profile * root,
                        )
                    )

        if not own or not other:
            self.ambiguous = True
            return self.fallback_threshold

        own_mean = float(np.mean(own))
        other_mean = float(np.mean(other))

        if not math.isfinite(own_mean) or not math.isfinite(other_mean):
            self.ambiguous = True
            return self.fallback_threshold

        if own_mean <= other_mean:
            self.ambiguous = True
            return self.fallback_threshold

        return float(
            np.clip(
                (own_mean + other_mean) / 2.0,
                -1.0,
                1.0,
            )
        )

    def _align(
            self,
            atoms: list[Atom],
            profiles: list[np.ndarray],
            weights: np.ndarray,
            threshold: float,
            slot_values: Optional[list[set[str]]] = None,
    ) -> tuple[list[Optional[Atom]], list[Atom]]:
        n = len(atoms)
        m = len(profiles)

        if not n:
            return [None] * m, []

        if not m:
            return [], list(atoms)

        root = np.sqrt(weights)
        gap_penalty = 0.15
        exact_match_bonus = 1.0

        scores = np.full((n, m), -np.inf, dtype=float)

        for i, atom in enumerate(atoms):
            vector = atom.vector * root
            normalized_value = str(atom.value).strip().casefold()

            for j, profile in enumerate(profiles):
                similarity = self._cosine(
                    vector,
                    profile * root,
                )

                exact_match = (
                        slot_values is not None
                        and j < len(slot_values)
                        and normalized_value in slot_values[j]
                )

                if similarity >= threshold or exact_match:
                    scores[i, j] = similarity + (
                        exact_match_bonus if exact_match else 0.0
                    )

        # Global alignment: gaps have a non-zero cost.
        dp = np.full((n + 1, m + 1), -np.inf, dtype=float)
        trace = np.zeros((n + 1, m + 1), dtype=np.int8)

        # 1 = skip atom
        # 2 = skip slot
        # 3 = match

        dp[0, 0] = 0.0

        for i in range(1, n + 1):
            dp[i, 0] = -gap_penalty * i
            trace[i, 0] = 1

        for j in range(1, m + 1):
            dp[0, j] = -gap_penalty * j
            trace[0, j] = 2

        for i in range(1, n + 1):
            for j in range(1, m + 1):
                skip_atom = dp[i - 1, j] - gap_penalty
                skip_slot = dp[i, j - 1] - gap_penalty

                similarity = scores[i - 1, j - 1]

                match = (
                    dp[i - 1, j - 1] + similarity
                    if np.isfinite(similarity)
                    else -np.inf
                )

                # Prefer keeping earlier sequence positions when scores tie.
                if n < m:
                    choices = (
                        (skip_slot, 2),
                        (match, 3),
                        (skip_atom, 1),
                    )
                elif n > m:
                    choices = (
                        (skip_atom, 1),
                        (match, 3),
                        (skip_slot, 2),
                    )
                else:
                    choices = (
                        (match, 3),
                        (skip_atom, 1),
                        (skip_slot, 2),
                    )

                best_score, direction = max(
                    choices,
                    key=lambda item: item[0],
                )

                dp[i, j] = best_score
                trace[i, j] = direction

        aligned: list[Optional[Atom]] = [None] * m
        matched_indices: set[int] = set()

        i, j = n, m

        while i > 0 or j > 0:
            direction = trace[i, j]

            if direction == 3 and i > 0 and j > 0:
                aligned[j - 1] = atoms[i - 1]
                matched_indices.add(i - 1)
                i -= 1
                j -= 1

            elif direction == 1 and i > 0:
                i -= 1

            elif direction == 2 and j > 0:
                j -= 1

            else:
                break

        unmatched = [
            atom
            for index, atom in enumerate(atoms)
            if index not in matched_indices
        ]

        return aligned, unmatched

    @staticmethod
    def _value_profiles(
            assignments: list[list[Optional[Atom]]],
    ) -> list[set[str]]:
        if not assignments:
            return []

        width = len(assignments[0])
        profiles: list[set[str]] = []

        for slot in range(width):
            values = {
                str(atom.value).strip().casefold()
                for row in assignments
                if (atom := row[slot]) is not None
            }
            profiles.append(values)

        return profiles


    def _insert_new_slots(
        self,
        assignments: list[list[Optional[Atom]]],
        per_row: dict[int, list[Atom]],
        unmatched: dict[int, list[Atom]],
        seed_size: int,
    ) -> list[Optional[int]]:

        # Each original seed slot keeps its original identifier.
        # Inserted slots have None as their origin.
        slot_origins: list[Optional[int]] = list(range(seed_size))

        for row_index in sorted(unmatched):
            sequence = per_row[row_index]

            positions = {
                id(atom): position
                for position, atom in enumerate(sequence)
            }

            for atom in sorted(
                unmatched[row_index],
                key=lambda item: positions[id(item)],
            ):
                position = positions[id(atom)]

                left = sequence[position - 1] if position > 0 else None

                right = (
                    sequence[position + 1]
                    if position + 1 < len(sequence)
                    else None
                )

                row_slots = {
                    id(value): slot
                    for slot, value in enumerate(assignments[row_index])
                    if value is not None
                }

                left_slot = (
                    row_slots.get(id(left))
                    if left is not None
                    else None
                )

                right_slot = (
                    row_slots.get(id(right))
                    if right is not None
                    else None
                )

                insertion = None

                if left_slot is not None and right_slot is not None:
                    left_origin = slot_origins[left_slot]
                    right_origin = slot_origins[right_slot]

                    if (
                        left_origin is not None
                        and right_origin is not None
                        and right_origin == left_origin + 1
                    ):
                        insertion = right_slot

                elif left is None and right_slot is not None:
                    if slot_origins[right_slot] == 0:
                        insertion = right_slot

                elif right is None and left_slot is not None:
                    if slot_origins[left_slot] == seed_size - 1:
                        insertion = left_slot + 1

                if insertion is None:
                    self.pending.append((row_index, atom))
                    continue

                # Insert a new column into every row.
                for row in assignments:
                    row.insert(insertion, None)

                assignments[row_index][insertion] = atom

                slot_origins.insert(insertion, None)

        return slot_origins

    def align_group(
        self,
        group: list[tuple[int, Atom]],
        vectors: dict[int, np.ndarray],
        atom_order: dict[int, int],
        total_rows: int,
    ) -> list[list[Optional[Atom]]]:

        self.ambiguous = False
        self.pending = []

        if not group:
            return [[] for _ in range(total_rows)]

        per_row: dict[int, list[Atom]] = defaultdict(list)

        for row_index, atom in group:
            atom.vector = np.nan_to_num(
                vectors[id(atom)],
                nan=0.0,
                posinf=0.0,
                neginf=0.0,
            )

            per_row[row_index].append(atom)

        for atoms in per_row.values():
            atoms.sort(
                key=lambda atom: atom_order[id(atom)]
            )

        # Select the longest row as seed.
        seed_row = min(
            per_row,
            key=lambda index: (-len(per_row[index]), index),
        )

        seed = per_row[seed_row]
        seed_size = len(seed)

        if seed_size == 0:
            return [[] for _ in range(total_rows)]

        assignments: list[list[Optional[Atom]]] = [
            [None] * seed_size
            for _ in range(total_rows)
        ]

        assignments[seed_row] = list(seed)

        # First pass: align against seed profiles.
        seed_profiles = [
            atom.vector.copy()
            for atom in seed
        ]

        weights = np.ones_like(seed[0].vector)

        first_unmatched: dict[int, list[Atom]] = {}

        for row_index in sorted(per_row):
            if row_index == seed_row:
                continue
            seed_values = [
                {str(atom.value).strip().casefold()}
                for atom in seed
            ]

            aligned, unmatched = self._align(
                per_row[row_index],
                seed_profiles,
                weights,
                self.fallback_threshold,
                slot_values=seed_values,
            )

            assignments[row_index] = aligned
            first_unmatched[row_index] = unmatched

        # Refine profiles using first-pass assignments.
        profiles = self._profiles(assignments)

        weights = self._weights(assignments)

        threshold = self._threshold(
            assignments,
            weights,
        )

        # Second pass: re-align against refined profiles.
        second_assignments = [
            list(row)
            for row in assignments
        ]

        profiles = self._profiles(assignments)
        slot_values = self._value_profiles(assignments)

        weights = self._weights(assignments)
        for row_index in sorted(per_row):
            if row_index == seed_row:
                continue

            aligned, unmatched = self._align(
                per_row[row_index],
                profiles,
                weights,
                threshold,
                slot_values=slot_values,
            )

            second_assignments[row_index] = aligned
            first_unmatched[row_index] = unmatched

        assignments = second_assignments

        # Insert unmatched atoms only when the PTA neighbor
        # constraint proves a valid insertion position.
        self._insert_new_slots(
            assignments,
            per_row,
            first_unmatched,
            seed_size,
        )

        # Retry pending atoms only against currently empty slots.
        # Never overwrite an existing assignment.
        if self.pending:
            refreshed_profiles = self._profiles(assignments)
            refreshed_values = self._value_profiles(assignments)
            refreshed_weights = self._weights(assignments)

            refreshed_threshold = self._threshold(
                assignments,
                refreshed_weights,
            )

            remaining_pending = []

            for row_index in sorted(per_row):
                row_pending = [
                    atom
                    for ri, atom in self.pending
                    if ri == row_index
                ]

                if not row_pending:
                    continue

                available_slots = [
                    slot
                    for slot, value in enumerate(assignments[row_index])
                    if value is None
                ]

                if not available_slots:
                    remaining_pending.extend(
                        (row_index, atom)
                        for atom in row_pending
                    )
                    continue

                available_profiles = [
                    refreshed_profiles[slot]
                    for slot in available_slots
                ]

                available_values = [
                    refreshed_values[slot]
                    for slot in available_slots
                ]
                aligned, unmatched = self._align(
                    row_pending,
                    available_profiles,
                    refreshed_weights,
                    refreshed_threshold,
                    slot_values=available_values,
                )

                for local_slot, atom in enumerate(aligned):
                    if atom is None:
                        continue

                    actual_slot = available_slots[local_slot]

                    if assignments[row_index][actual_slot] is None:
                        assignments[row_index][actual_slot] = atom
                    else:
                        unmatched.append(atom)

                remaining_pending.extend(
                    (row_index, atom)
                    for atom in unmatched
                )

            self.pending = remaining_pending

        # Any remaining pending atoms are intentionally not
        # assigned to arbitrary columns.
        return assignments



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
    nullable: bool = False,
) -> dict:
    column = {
        "path": path,
        "atype": atype,
        "slot": slot,
        "entries": entries,
        "coverage": coverage,
        "coverage_ratio": coverage / total_rows if total_rows else 0.0,
        "nullable": nullable,
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
        column_min_cover = (
            max(
                config.min_column_rows,
                math.ceil(len(rows) * config.min_column_coverage_ratio),
            )
            if config.nullable_columns
            else min_cover
        )

        row_atoms: list[list[Atom]] = []
        atom_order: dict[int, int] = {}
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
                        atom_order[id(atoms[-1])] = len(atom_order)
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

        atom_order = {id(atom): order for order, atom in enumerate(atom for _, atom in flat)}
        groups = _merge_same_path_groups(groups, atom_order)

        vector_by_atom = {id(atom): vectors[i] for i, (_, atom) in enumerate(flat)}

        columns: list[dict] = []

        for group in groups:
            row_set = {i for i, _ in group}
            keep_low_coverage = _is_struck_price_group(group)
            if len(row_set) < column_min_cover and not keep_low_coverage:
                continue

            rep_path = Counter(a.path for _, a in group).most_common(1)[0][0]
            atype = group[0][1].atype

            if max(len([atom for row_index, atom in group if row_index == row]) for row in range(total_rows)) == 1:
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
                        config.nullable_columns,
                    )
                )
                continue

            aligner = SequenceAligner(config.distance_threshold)
            aligned = aligner.align_group(
                group,
                vector_by_atom,
                atom_order,
                total_rows,
            )
            for slot in range(len(aligned[0]) if aligned else 0):
                sub = [(row_index, aligned[row_index][slot]) for row_index in range(total_rows) if aligned[row_index][slot] is not None]
                covered_rows = {i for i, _ in sub}
                if (
                    len(covered_rows) < column_min_cover
                    and not keep_low_coverage
                ):
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
                        config.nullable_columns,
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
