"""Tests for domtrack.tables (discovery, labeling payload, rendering)."""

from __future__ import annotations

import json

import pytest

from domtrack import trace as tf
from domtrack.tables import discovery, labeling, render


def _node(nid, tag, attrs=None, children=None, text=None):
    node = {"id": nid, "type": tf.NodeType.ELEMENT, "tagName": tag.upper(),
            "attributes": attrs or {}}
    if text is not None:
        node["type"] = tf.NodeType.TEXT
        node["textContent"] = text
        node.pop("tagName")
        node.pop("attributes")
        return node
    node["childNodes"] = children or []
    return node


def _text(nid, value):
    return {"id": nid, "type": tf.NodeType.TEXT, "textContent": value}


def _card_trace() -> dict:
    """A <ul> of three <li> rows, each with a title, a price, and a link."""
    next_id = 10

    def card(title, price, href):
        nonlocal next_id
        base = next_id
        next_id += 4
        return _node(
            base, "li", {"class": "card"},
            [
                _node(base + 1, "span", {"class": "title"}, [_text(base + 1_000, title)]),
                _node(base + 2, "span", {"class": "price"}, [_text(base + 2_000, price)]),
                _node(base + 3, "a", {"class": "buy", "href": href}, [_text(base + 3_000, "Buy")]),
            ],
        )

    cards = [
        card("Alpha", "10", "https://x/a"),
        card("Beta", "20", "https://x/b"),
        card("Gamma", "30", "https://x/c"),
    ]
    body = _node(3, "body", {}, [_node(4, "ul", {"class": "cards"}, cards)])
    html = _node(2, "html", {}, [body])
    return {
        "meta": {"url": "https://example.com"},
        "version": 2,
        "snapshot": {"type": tf.RecordType.FULL_SNAPSHOT,
                     "node": {"id": 1, "type": tf.NodeType.DOCUMENT, "childNodes": [html]}},
        "records": [],
    }


# ── discovery ──────────────────────────────────────────────────────────────

def test_build_tree_folds_records():
    raw = _card_trace()
    root = discovery.build_tree(raw)
    assert root.tag == ""  # document root
    html = root.children[0]
    body = html.children[0]
    assert any(n.tag == "ul" for n in body.children)


def test_build_tree_requires_snapshot():
    with pytest.raises(ValueError):
        discovery.build_tree({"records": []})


def test_discover_tables_finds_card_grid():
    result = discovery.discover_tables(_card_trace())
    assert result["tables_returned"] >= 1
    table = result["tables"][0]
    assert table["meta"]["row_count"] == 3
    assert table["meta"]["columns"] >= 2
    # The href column must be present and populated for every row.
    href_cols = [c for c in table["columns"] if c["atype"] == "href"]
    assert href_cols
    values = [row[href_cols[0]["id"]] for row in table["rows"]]
    assert values == ["https://x/a", "https://x/b", "https://x/c"]


def test_discover_tables_dedupes_overlapping():
    raw = _card_trace()
    deduped = discovery.discover_tables(raw, discovery.DiscoveryConfig(dedupe=True))
    all_tables = discovery.discover_tables(raw, discovery.DiscoveryConfig(dedupe=False))
    assert deduped["tables_returned"] <= all_tables["tables_returned"]


def test_discover_tables_min_rows_filters():
    config = discovery.DiscoveryConfig(min_rows=10)
    result = discovery.discover_tables(_card_trace(), config)
    assert result["tables_returned"] == 0


def test_row_identity_prefers_href():
    atoms = [
        discovery.Atom((("a", frozenset()),), "text", "Buy"),
        discovery.Atom((("a", frozenset()),), "href", "https://x/a"),
    ]
    assert discovery.row_identity(atoms) == ("href", "https://x/a")


def test_dedupe_removes_subset():
    big = {"score": 1.0, "_fingerprint": frozenset({("href", "a"), ("href", "b")})}
    small = {"score": 0.5, "_fingerprint": frozenset({("href", "a")})}
    kept = discovery.dedupe([small, big], threshold=0.8)
    assert kept == [big]


# ── labeling ───────────────────────────────────────────────────────────────

def _labeled_input() -> dict:
    return discovery.discover_tables(_card_trace())


def test_compact_table_shape():
    table = _labeled_input()["tables"][0]
    compact = labeling.compact_table(0, table)
    assert compact["index"] == 0
    assert compact["row_count"] == 3
    assert len(compact["row_samples"]) == 3
    assert len(compact["columns"]) == len(table["columns"])


def test_merge_labels_attaches_semantic_fields():
    data = _labeled_input()
    labels = [
        {
            "index": 0,
            "title": "Cards",
            "purpose": "A grid of cards.",
            "columns": [
                {"column_index": i, "label": f"col{i}"}
                for i in range(len(data["tables"][0]["columns"]))
            ],
        }
    ]
    merged = labeling.merge_labels(data, labels)
    assert merged["tables"][0]["meta"]["title"] == "Cards"
    assert merged["tables"][0]["meta"]["purpose"] == "A grid of cards."
    assert merged["tables"][0]["columns"][0]["semantic_label"] == "col0"
    # Original is untouched (deep copy).
    assert "title" not in data["tables"][0]["meta"]


def test_label_tables_dry_run_returns_input(capsys):
    data = _labeled_input()
    out = labeling.label_tables(data, labeling.LabelConfig(dry_run=True))
    assert out is data
    assert '"tables"' in capsys.readouterr().out


def test_resolve_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert labeling.resolve_api_key(labeling.LabelConfig(provider="openai")) == "sk-test"


# ── rendering ──────────────────────────────────────────────────────────────

def _labeled_data() -> dict:
    data = _labeled_input()
    table = data["tables"][0]
    table["meta"]["title"] = "Cards"
    table["meta"]["purpose"] = "A grid."
    for i, column in enumerate(table["columns"]):
        column["semantic_label"] = f"Col {i}"
    return data


def test_column_headers_prefers_semantic_label():
    table = _labeled_data()["tables"][0]
    headers = render.column_headers(table)
    assert headers[0][1] == "Col 0"


def test_render_markdown_uses_headers():
    import io

    buf = io.StringIO()
    render.render_markdown(_labeled_data(), render.RenderOptions(), out=buf)
    text = buf.getvalue()
    assert "## جدول 1: Cards" in text
    assert "Col 0" in text
    assert "https://x/a" in text


def test_render_csv_writes_rows():
    import io

    buf = io.StringIO()
    render.render_csv(_labeled_data(), render.RenderOptions(), out=buf)
    lines = buf.getvalue().splitlines()
    assert lines[0].startswith("# جدول 1")
    assert any("https://x/a" in line for line in lines)


def test_render_unknown_format_raises():
    with pytest.raises(ValueError):
        render.render({"tables": []}, "nope")


def test_iter_tables_only_filter():
    data = _labeled_data()
    assert [i for i, _ in render.iter_tables(data, only={1})] == [1]
    assert [i for i, _ in render.iter_tables(data, only={99})] == []


# ── CLI ────────────────────────────────────────────────────────────────────

def _write(tmp_path, name, payload):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_cli_extract_writes_json(tmp_path):
    from domtrack.tables import cli

    trace_path = _write(tmp_path, "trace.json", _card_trace())
    out = tmp_path / "tables.json"
    assert cli.main(["extract", trace_path, "-o", str(out)]) == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["tables_returned"] >= 1


def test_cli_extract_split_inputs(tmp_path):
    from domtrack.tables import cli

    raw = _card_trace()
    snap = _write(tmp_path, "snapshot.json", raw["snapshot"])
    recs = _write(tmp_path, "records.json", raw["records"])
    out = tmp_path / "tables.json"
    assert cli.main(["extract", snap, recs, "-o", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["tables_returned"] >= 1


def test_cli_extract_missing_file(tmp_path):
    from domtrack.tables import cli

    assert cli.main(["extract", str(tmp_path / "missing.json")]) == 1
    assert cli.main(["extract", "a.json", "b.json", "c.json"]) == 2


def test_cli_label_dry_run(tmp_path, capsys):
    from domtrack.tables import cli

    tables_path = _write(tmp_path, "tables.json", _labeled_input())
    assert cli.main(["label", tables_path, "--dry-run"]) == 0
    assert '"tables"' in capsys.readouterr().out


def test_cli_label_missing_key(tmp_path, monkeypatch):
    from domtrack.tables import cli

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    tables_path = _write(tmp_path, "tables.json", _labeled_input())
    assert cli.main(["label", tables_path]) == 1


def test_cli_show_markdown(tmp_path):
    from domtrack.tables import cli

    labeled_path = _write(tmp_path, "labeled.json", _labeled_data())
    out = tmp_path / "out.md"
    assert cli.main(["show", labeled_path, "--format", "markdown", "-o", str(out)]) == 0
    assert "Cards" in out.read_text(encoding="utf-8")


def test_cli_show_bad_only(tmp_path):
    from domtrack.tables import cli

    labeled_path = _write(tmp_path, "labeled.json", _labeled_data())
    assert cli.main(["show", labeled_path, "--only", "x"]) == 2
