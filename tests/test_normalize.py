"""Tests for semantic table normalization."""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")
pytest.importorskip("sklearn")

from domtrack.tables import normalize


class FakeModel:
    def __init__(self, mapping, default=(0.5, 0.5, 0.5, 0.5)):
        self.mapping = mapping
        self.default = default
        self.texts: list[str] = []

    def encode(self, texts, **kwargs):
        import numpy as np

        self.texts.extend(texts)
        vectors = []
        for text in texts:
            value = text.split(" | ", 1)[-1]
            vectors.append(self.mapping.get(value, self.default))
        return np.asarray(vectors, dtype=np.float32)


def _embedder(mapping, **kwargs):
    return normalize.CellEmbedder(model=FakeModel(mapping), **kwargs)


def _table(values_by_column: dict[str, list[str]], **column_meta) -> dict:
    columns = []
    for column_id in values_by_column:
        columns.append({"id": column_id, "label": column_id, **column_meta})
    row_count = max(len(values) for values in values_by_column.values())
    rows = [
        {
            column_id: values[index] if index < len(values) else ""
            for column_id, values in values_by_column.items()
        }
        for index in range(row_count)
    ]
    return {"columns": columns, "rows": rows}


CITY_A = (1.0, 0.0, 0.0, 0.0)
CITY_B = (0.98, 0.02, 0.0, 0.0)
POSTAL = (0.0, 1.0, 0.0, 0.0)
PHONE = (0.0, 0.0, 1.0, 0.0)
TYPO = (0.0, 0.0, 0.0, 1.0)


def test_embedding_text_includes_column_identity():
    model = FakeModel({"Tehran": CITY_A})
    embedder = normalize.CellEmbedder(model=model)
    embedder.encode_column("city", ["Tehran"])
    assert model.texts == ["query: city | Tehran"]


def test_heterogeneous_column_splits_into_concepts():
    table = _table({"mixed": ["Tehran", "Shiraz", "25", "30", "0912", "021"]})
    embedder = _embedder(
        {
            "Tehran": CITY_A,
            "Shiraz": CITY_B,
            "25": POSTAL,
            "30": POSTAL,
            "0912": PHONE,
            "021": PHONE,
        }
    )
    config = normalize.NormalizeConfig(min_cluster_size=2, merge_related=False)
    result = normalize.normalize_table(table, config, embedder)

    assert result["meta"]["input_columns"] == 1
    assert result["meta"]["output_columns"] == 3
    assert all(column["split"] for column in result["columns"])
    assert all(column["id"].startswith("mixed__c") for column in result["columns"])
    assert all(row["_row_index"] == index for index, row in enumerate(result["rows"]))

    values = {
        column["id"]: [row[column["id"]] for row in result["rows"]]
        for column in result["columns"]
    }
    assert sorted(values.values()) == sorted(
        [
            ["Tehran", "Shiraz", "", "", "", ""],
            ["", "", "25", "30", "", ""],
            ["", "", "", "", "0912", "021"],
        ]
    )


def test_homogeneous_column_keeps_original_id():
    table = _table({"city": ["Tehran", "Shiraz", "Mashhad"]})
    embedder = _embedder({"Tehran": CITY_A, "Shiraz": CITY_B, "Mashhad": CITY_A})
    result = normalize.normalize_table(
        table, normalize.NormalizeConfig(min_cluster_size=2), embedder
    )

    assert len(result["columns"]) == 1
    column = result["columns"][0]
    assert column["id"] == "city"
    assert column["split"] is False
    assert [row["city"] for row in result["rows"]] == ["Tehran", "Shiraz", "Mashhad"]


def test_small_cluster_is_marked_rare_and_not_merged():
    table = _table({"mixed": ["Tehran", "Shiraz", "Mashhad", "Isfahan", "Teharn"]})
    embedder = _embedder(
        {
            "Tehran": CITY_A,
            "Shiraz": CITY_B,
            "Mashhad": CITY_A,
            "Isfahan": CITY_B,
            "Teharn": TYPO,
        }
    )
    result = normalize.normalize_table(
        table, normalize.NormalizeConfig(min_cluster_size=3), embedder
    )

    rare = [column for column in result["columns"] if column["rare"]]
    clean = [column for column in result["columns"] if not column["rare"]]
    assert len(rare) == 1
    assert len(clean) == 1
    assert "Teharn" in [row[rare[0]["id"]] for row in result["rows"]]
    assert "Teharn" not in [row[clean[0]["id"]] for row in result["rows"]]


def test_recursive_split_handles_loose_initial_threshold():
    table = _table({"mixed": ["Tehran", "Shiraz", "25", "30"]})
    embedder = _embedder(
        {"Tehran": CITY_A, "Shiraz": CITY_B, "25": POSTAL, "30": POSTAL}
    )
    config = normalize.NormalizeConfig(
        distance_threshold=1.5,
        homogeneity_threshold=0.15,
        min_cluster_size=2,
        merge_related=False,
    )
    result = normalize.normalize_table(table, config, embedder)
    assert len(result["columns"]) == 2
    assert all(column["homogeneity"] <= 0.15 for column in result["columns"])


def test_related_split_columns_merge_across_sources():
    table = _table(
        {
            "a": ["Tehran", "Shiraz", "25", "30"],
            "b": ["Mashhad", "Isfahan", "40", "50"],
        }
    )
    embedder = _embedder(
        {
            "Tehran": CITY_A,
            "Shiraz": CITY_B,
            "Mashhad": CITY_A,
            "Isfahan": CITY_B,
            "25": POSTAL,
            "30": POSTAL,
            "40": POSTAL,
            "50": POSTAL,
        }
    )
    config = normalize.NormalizeConfig(min_cluster_size=2)
    result = normalize.normalize_table(table, config, embedder)

    assert result["report"]["merged_columns"]
    assert result["meta"]["output_columns"] == 2
    merged = [column for column in result["columns"] if column.get("merged_from")]
    assert len(merged) == 2
    city_column = next(
        column for column in merged if "Tehran" in result["rows"][0][column["id"]]
    )
    assert result["rows"][0][city_column["id"]] == "Tehran | Mashhad"


def test_report_and_validation_shape():
    table = _table({"mixed": ["Tehran", "Shiraz", "25", "30"]})
    embedder = _embedder(
        {"Tehran": CITY_A, "Shiraz": CITY_B, "25": POSTAL, "30": POSTAL}
    )
    result = normalize.normalize_table(
        table,
        normalize.NormalizeConfig(min_cluster_size=2, merge_related=False),
        embedder,
    )

    source = result["report"]["source_columns"][0]
    assert source["source_column"] == "mixed"
    assert source["total_cells"] == 4
    assert source["concepts"] == 2
    assert source["coverage"] == 1.0
    assert len(source["clusters"]) == 2
    assert all("homogeneity" in cluster for cluster in source["clusters"])
    assert result["validation"]["ok"] is True


def test_existing_semantic_label_is_carried_not_invented():
    table = _table(
        {"mixed": ["Tehran", "Shiraz", "25", "30"]},
        semantic_label="Location or code",
    )
    embedder = _embedder(
        {"Tehran": CITY_A, "Shiraz": CITY_B, "25": POSTAL, "30": POSTAL}
    )
    result = normalize.normalize_table(
        table,
        normalize.NormalizeConfig(min_cluster_size=2, merge_related=False),
        embedder,
    )
    assert all(
        column["semantic_label"] == "Location or code"
        for column in result["columns"]
    )
    assert all(column["label"] == column["id"] for column in result["columns"])


def test_cli_parser_has_normalize():
    from domtrack.tables import cli

    args = cli.build_parser().parse_args(["normalize", "in.json"])
    assert args.command == "normalize"
    assert args.threshold == 0.04


def test_normalized_output_feeds_the_existing_label_payload():
    from domtrack.tables import labeling

    table = _table({"mixed": ["Tehran", "Shiraz", "25", "30"]})
    table["meta"] = {
        "parent_tag": "div",
        "parent_classes": ["rows"],
        "row_count": 4,
    }
    embedder = _embedder(
        {"Tehran": CITY_A, "Shiraz": CITY_B, "25": POSTAL, "30": POSTAL}
    )
    result = normalize.normalize_table(
        table,
        normalize.NormalizeConfig(min_cluster_size=2, merge_related=False),
        embedder,
    )
    compact = labeling.compact_table(0, result)
    assert compact["parent"]["tag"] == "div"
    assert compact["row_count"] == 4
    assert len(compact["columns"]) == result["meta"]["output_columns"]


def test_url_atype_is_not_semantically_split():
    table = {
        "columns": [{"id": "link", "label": "link", "atype": "href"}],
        "rows": [
            {"link": "/a"},
            {"link": "/b"},
            {"link": "/c"},
            {"link": "/d"},
        ],
    }
    embedder = _embedder(
        {"/a": CITY_A, "/b": POSTAL, "/c": PHONE, "/d": TYPO}
    )
    result = normalize.normalize_table(table, embedder=embedder)
    assert len(result["columns"]) == 1
    assert result["columns"][0]["id"] == "link"
    assert result["columns"][0]["split"] is False
