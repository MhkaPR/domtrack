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


# ── pluggable interfaces + multi-document driver ───────────────────────────

def test_default_interfaces_resolve_to_module_defaults():
    ifaces = discovery.DiscoveryConfig().interfaces()
    assert ifaces.sig is discovery.default_sig
    assert ifaces.values is discovery.default_values
    assert ifaces.path_sim is discovery.default_path_sim
    assert ifaces.is_visible is discovery.default_is_visible


def test_default_sig_is_path_from_root():
    root = discovery.build_tree(_card_trace())
    html = root.children[0]
    body = html.children[0]
    ul = body.children[0]
    li = ul.children[0]
    path = discovery.default_sig(root, li)
    assert path is not None
    assert path[-1][0] == "li"
    assert "card" in path[-1][1]
    # A sig rooted at the <li> is just the <li> itself (A inclusive).
    assert discovery.default_sig(li, li) == (("li", li.classes),)


def test_default_sig_excludes_skip_tags():
    root = discovery.build_tree(_card_trace())
    script = discovery.Node(id=999, tag="script")
    body = root.children[0].children[0]
    body.children.append(script)
    script.parent = body
    assert discovery.default_sig(root, script) is None


def test_custom_sig_excludes_subtree():
    root = discovery.build_tree(_card_trace())

    def sig(_root, node):
        path = discovery.default_sig(_root, node)
        if path is None:
            return None
        if node.tag == "span" and "price" in node.classes:
            return None
        return path

    tables = discovery.discover(root, discovery.DiscoveryConfig(sig=sig))
    assert tables
    column_ids = [c["id"] for c in tables[0]["columns"]]
    assert not any("price" in cid for cid in column_ids)


def test_custom_values_selects_atoms():
    root = discovery.build_tree(_card_trace())

    def values(node):
        href = node.attrs.get("href")
        return [("href", href)] if href else []

    tables = discovery.discover(
        root, discovery.DiscoveryConfig(values=values, min_cols=1)
    )
    assert tables
    column_ids = [c["id"] for c in tables[0]["columns"]]
    assert all(cid.endswith("@href") for cid in column_ids)


def test_custom_path_sim_is_used():
    root = discovery.build_tree(_card_trace())
    calls = []

    def path_sim(p1, p2):
        calls.append((p1, p2))
        return discovery.default_path_sim(p1, p2)

    tables = discovery.discover(root, discovery.DiscoveryConfig(path_sim=path_sim))
    assert calls
    assert tables


def test_strict_path_threshold_drops_columns():
    root = discovery.build_tree(_card_trace())
    strict = discovery.DiscoveryConfig(path_threshold=1.01)   # nothing merges
    # Every atom is then alone in its group, below min_cover -> no table.
    assert discovery.discover(root, strict) == []


def test_is_visible_false_excludes_child_row():
    raw = _card_trace()
    root = discovery.build_tree(raw)
    cards = root.children[0].children[0].children[0].children
    assert len(cards) == 3
    hidden = {cards[2].id}

    def is_visible(node):
        return node.id not in hidden

    tables = discovery.discover(
        root, discovery.DiscoveryConfig(is_visible=is_visible, min_rows=2)
    )
    assert tables
    assert tables[0]["meta"]["row_count"] == 2


def test_multi_atom_column_splits_into_slots():
    ids = iter(range(100, 1000))

    def fresh():
        return next(ids)

    def card(title, tags):
        children = [
            _node(fresh(), "span", {"class": "title"}, [_text(fresh(), title)])
        ]
        for tag in tags:
            children.append(
                _node(fresh(), "span", {"class": "tag"}, [_text(fresh(), tag)])
            )
        return _node(fresh(), "li", {"class": "card"}, children)

    cards = [card("A", ["x", "y"]), card("B", ["p"]), card("C", ["m", "n"])]
    body = _node(3, "body", {}, [_node(4, "ul", {"class": "cards"}, cards)])
    html = _node(2, "html", {}, [body])
    raw = {
        "meta": {"url": "https://example.com"},
        "version": 2,
        "snapshot": {"type": tf.RecordType.FULL_SNAPSHOT,
                     "node": {"id": 1, "type": tf.NodeType.DOCUMENT,
                              "childNodes": [html]}},
        "records": [],
    }
    result = discovery.discover_tables(raw, discovery.DiscoveryConfig(min_cols=1))
    assert result["tables_returned"] >= 1
    table = result["tables"][0]
    slot_cols = [c for c in table["columns"] if c["slot"] is not None]
    assert slot_cols  # the tag column split into slots
    # First slot carries the first tag of every row.
    first_slot = sorted(slot_cols, key=lambda c: c["slot"])[0]
    values = [row[first_slot["id"]] for row in table["rows"]]
    assert values == ["x", "p", "m"]


def test_discover_across_documents_stats_and_ids():
    docs = [{"id": "d1", "trace": _card_trace()}, {"id": "d2", "trace": _card_trace()}]
    result = discovery.discover_across_documents(docs)
    assert result["stats"]["docs"] == 2
    assert result["stats"]["docs_with_tables"] == 2
    assert result["stats"]["total_tables"] >= 2
    assert [d["document_id"] for d in result["all_tables"]] == ["d1", "d2"]


def test_discover_across_documents_empty_document():
    raw = _card_trace()
    raw["snapshot"]["node"]["childNodes"] = []   # empty <html>-less document
    result = discovery.discover_across_documents([{"id": "empty", "trace": raw}])
    assert result["stats"]["docs"] == 1
    assert result["all_tables"][0]["tables"] == []


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
