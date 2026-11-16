"""Complete-linkage column clustering tests."""

from __future__ import annotations

import numpy as np

from domtrack.tables.discovery import DiscoveryConfig
from domtrack.tables.discovery import columns as columns_module
from domtrack.tables.discovery.columns import (
    ColumnExtractor,
    _cluster_atoms_complete_linkage,
)
from domtrack.tables.discovery.features import (
    _ATOM_BOOLEAN,
    _ATOM_WEIGHTS,
    FeatureVectorizer,
    node_features,
    value_types,
)
from domtrack.tables.discovery.engine import build_table
from domtrack.tables.discovery.model import Node


class _Builder:
    def __init__(self) -> None:
        self.next_id = 0

    def fresh(self) -> int:
        self.next_id += 1
        return self.next_id

    def cell(self, value: str, tag: str = "span") -> Node:
        node = Node(self.fresh(), tag=tag, attrs={"class": "cell"})
        text = Node(self.fresh(), text=value)
        text.parent = node
        node.children.append(text)
        return node

    def row(self, values: list[str], tag: str = "li") -> Node:
        row = Node(self.fresh(), tag=tag, attrs={"class": "row"})
        for value in values:
            cell = self.cell(value)
            cell.parent = row
            row.children.append(cell)
        return row


def _rows(rows: list[list[str]]) -> list[Node]:
    builder = _Builder()
    return [builder.row(values) for values in rows]


def _extract(rows: list[Node], **overrides):
    config = DiscoveryConfig(min_cols=2, **overrides)
    return ColumnExtractor(config, config.interfaces()).extract(rows)


def _values(column: dict) -> list[str]:
    return [atom.value for _, atom in column["entries"]]


def test_no_chaining():
    vectors = np.array([[1.0, 0.0], [1.0, 0.5], [1.0, 1.0]])
    path_sim = np.eye(3)

    labels = _cluster_atoms_complete_linkage(
        vectors, path_sim, distance_threshold=0.2, path_weight=0.0
    )

    assert len(set(labels)) == 2
    assert labels[0] != labels[2]


def _atom_distance_matrix(rows: list[Node]):
    config = DiscoveryConfig()
    interfaces = config.interfaces()
    flat = []
    for row_index, row in enumerate(rows):
        def collect(node: Node) -> None:
            path = interfaces.sig(row, node)
            if path is None:
                return
            for atype, value in interfaces.values(node):
                if value != "":
                    flat.append(
                        (
                            row_index,
                            path,
                            atype,
                            value,
                            node.id,
                            {
                                **node_features(node, root=row, atype=atype),
                                **value_types(value, atype),
                            },
                        )
                    )
            for child in node.children:
                collect(child)

        collect(row)

    vectorizer = FeatureVectorizer("atom", weights=_ATOM_WEIGHTS).fit(
        [item[5] for item in flat]
    )
    vectors = vectorizer.transform([item[5] for item in flat])
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    unit = vectors / norms
    cosine = unit @ unit.T

    n = len(flat)
    path_sim = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            if flat[i][2] == flat[j][2]:
                score = interfaces.path_sim(flat[i][1], flat[j][1])
                path_sim[i, j] = path_sim[j, i] = score

    distance = 1.0 - (
        (1.0 - config.path_feature_weight) * cosine
        + config.path_feature_weight * path_sim
    )
    index = {
        (item[0], item[4], item[2], item[3]): i
        for i, item in enumerate(flat)
    }
    return distance, index


def test_distance_guarantee():
    rows = _rows(
        [
            ["1,200,000 تومان", "Apple"],
            ["2,300,000 تومان", "Samsung"],
            ["3,400,000 تومان", "Sony"],
            ["4,500,000 تومان", "LG"],
        ]
    )
    config = DiscoveryConfig(min_cols=2)
    columns = ColumnExtractor(config, config.interfaces()).extract(rows)
    distance, index = _atom_distance_matrix(rows)

    for column in columns:
        indices = [
            index[(row_index, atom.node.id, atom.atype, atom.value)]
            for row_index, atom in column["entries"]
        ]
        for i, left in enumerate(indices):
            for right in indices[i + 1 :]:
                assert distance[left, right] <= config.distance_threshold + 1e-9


def test_currency_vs_brand():
    rows = _rows(
        [
            ["1,200,000 تومان", "Apple"],
            ["2,300,000 تومان", "Samsung"],
            ["3,400,000 تومان", "Sony"],
            ["4,500,000 تومان", "LG"],
        ]
    )

    columns = _extract(rows)

    assert len(columns) == 2
    value_sets = [set(_values(column)) for column in columns]
    assert {"1,200,000 تومان", "2,300,000 تومان"} <= value_sets[0] | value_sets[1]
    for values in value_sets:
        assert not (any("تومان" in value for value in values) and "Apple" in values)


def test_deterministic():
    rows = _rows(
        [
            ["1,200,000 تومان", "Apple"],
            ["2,300,000 تومان", "Samsung"],
            ["3,400,000 تومان", "Sony"],
            ["4,500,000 تومان", "LG"],
        ]
    )

    first = _extract(rows)
    second = _extract(rows)

    def signature(columns):
        return [
            (column["id"], column["slot"], tuple(_values(column)))
            for column in columns
        ]

    assert signature(first) == signature(second)


def test_slot_splitting():
    rows = _rows(
        [
            ["100 تومان", "200 تومان"],
            ["300 تومان", "400 تومان"],
            ["500 تومان", "600 تومان"],
        ]
    )

    columns = _extract(rows)

    assert len(columns) == 2
    assert {column["slot"] for column in columns} == {0, 1}


def test_missing_intermediate_atom_does_not_shift_later_slot():
    rows = _rows([["A", "B", "C"], ["A", "C"], ["A", "B", "C"]])
    columns = _extract(rows)
    slots = {column["slot"]: _values(column) for column in columns if column["slot"] is not None}
    assert slots[0] == ["A", "A", "A"]
    assert slots[1] == ["B", "B"]
    assert slots[2] == ["C", "C", "C"]


def test_missing_first_atom_keeps_remaining_slots_aligned():
    columns = _extract(_rows([["A", "B", "C"], ["B", "C"]]), min_column_rows=1)
    slots = {column["slot"]: _values(column) for column in columns if column["slot"] is not None}
    assert slots[0] == ["A"]
    assert slots[1] == ["B", "B"]
    assert slots[2] == ["C", "C"]


def test_missing_last_atom_keeps_leading_slots_aligned():
    columns = _extract(_rows([["A", "B", "C"], ["A", "B"]]))
    slots = {column["slot"]: _values(column) for column in columns if column["slot"] is not None}
    assert slots[0] == ["A", "A"]
    assert slots[1] == ["B", "B"]


def test_multiple_missing_atoms_preserve_seed_slot_ids():
    columns = _extract(
        _rows([["A", "B", "C", "D"], ["A", "D"], ["A", "B", "D"]]),
        min_column_rows=1,
    )
    slots = {column["slot"]: _values(column) for column in columns if column["slot"] is not None}
    assert slots[0] == ["A", "A", "A"]
    assert slots[1] == ["B", "B"]
    assert slots[2] == ["C"]
    assert slots[3] == ["D", "D", "D"]


def test_dp_rejects_matches_below_threshold():
    from domtrack.tables.discovery.columns import SequenceAligner
    from domtrack.tables.discovery.model import Atom

    atom = Atom((), "text", "x")
    atom.vector = np.array([1.0, 0.0])
    aligned, unmatched = SequenceAligner(0.3)._align(
        [atom], [np.array([0.0, 1.0])], np.ones(2), 0.8
    )
    assert aligned == [None]
    assert unmatched == [atom]


def test_same_path_list_keeps_every_element():
    rows = _rows(
        [
            ["15.6", "Core i3", "8GB"],
            ["15.3", "Core i5", "16GB"],
            ["14", "Celeron", "4GB"],
        ]
    )

    columns = _extract(rows)
    slot_columns = {
        column["slot"]: _values(column)
        for column in columns
        if column["slot"] is not None
    }

    assert slot_columns == {
        0: ["15.6", "15.3", "14"],
        1: ["Core i3", "Core i5", "Celeron"],
        2: ["8GB", "16GB", "4GB"],
    }


def test_nullable_column_keeps_missing_cells_null():
    builder = _Builder()
    rows = []
    for badge in ["x", None, "y"]:
        row = builder.row(["title"])
        if badge is not None:
            badge_cell = builder.cell(badge, tag="em")
            badge_cell.parent = row
            row.children.append(badge_cell)
        rows.append(row)

    columns = _extract(rows)
    badge_column = next(
        column for column in columns if "em" in column["path_str"]
    )
    assert badge_column["nullable"] is True
    assert badge_column["coverage"] == 2

    table = build_table(rows[0], rows, columns, 1.0)
    assert table["rows"][0][badge_column["id"]] == "x"
    assert table["rows"][1][badge_column["id"]] is None
    assert table["rows"][2][badge_column["id"]] == "y"


def test_impure_cluster_split():
    rows = _rows(
        [
            ["1,200,000 تومان", "Apple"],
            ["2,300,000 تومان", "Samsung"],
            ["3,400,000 تومان", "Sony"],
            ["4,500,000 تومان", "LG"],
        ]
    )

    columns = _extract(rows)

    assert len(columns) == 2
    assert sum(
        any("تومان" in value for value in _values(column))
        for column in columns
    ) == 1
    assert sum(
        "Apple" in _values(column) for column in columns
    ) == 1


def test_uses_sklearn(monkeypatch):
    calls = []
    real_cluster = columns_module.AgglomerativeClustering

    class RecordingCluster(real_cluster):
        def __init__(self, **kwargs):
            calls.append(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(
        columns_module, "AgglomerativeClustering", RecordingCluster
    )

    rows = _rows(
        [
            ["1,200,000 تومان", "Apple"],
            ["2,300,000 تومان", "Samsung"],
            ["3,400,000 تومان", "Sony"],
        ]
    )
    _extract(rows)

    assert calls
    assert all(call["n_clusters"] is None for call in calls)
    assert all(call["linkage"] == "complete" for call in calls)
    assert all(call["metric"] == "precomputed" for call in calls)
    assert all("distance_threshold" in call for call in calls)


def test_weights_applied():
    items = [
        {"tag": "span", "is_numeric": True, "text_len": 10},
        {"tag": "div", "is_numeric": False, "text_len": 20},
    ]
    plain = FeatureVectorizer("atom").fit(items).transform_one(items[0])
    weighted = (
        FeatureVectorizer("atom", weights=_ATOM_WEIGHTS)
        .fit(items)
        .transform_one(items[0])
    )

    assert not np.allclose(plain, weighted)
    assert "is_numeric" in _ATOM_BOOLEAN
    assert _ATOM_WEIGHTS["is_numeric"] == 2.0
