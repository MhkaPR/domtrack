"""Format-level tests for domtrack trace handling (no browser needed)."""

from __future__ import annotations

import json

import pytest

from domtrack import trace as tf
from domtrack.replay.models import Mutation, Trace


def _sample_rrweb_trace() -> dict:
    return {
        "meta": {"url": "https://example.com", "viewport": {"w": 1440, "h": 900}},
        "version": 2,
        "snapshot": {
            "type": tf.RecordType.FULL_SNAPSHOT,
            "node": {
                "id": 1,
                "type": tf.NodeType.DOCUMENT,
                "childNodes": [
                    {
                        "id": 2,
                        "type": tf.NodeType.ELEMENT,
                        "tagName": "HTML",
                        "attributes": {},
                        "childNodes": [
                            {
                                "id": 3,
                                "type": tf.NodeType.ELEMENT,
                                "tagName": "BODY",
                                "attributes": {},
                                "childNodes": [
                                    {
                                        "id": 4,
                                        "type": tf.NodeType.ELEMENT,
                                        "tagName": "DIV",
                                        "attributes": {"id": "box", "class": "a b"},
                                        "childNodes": [
                                            {"id": 5, "type": tf.NodeType.TEXT, "textContent": "hi"}
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
        },
        "records": [
            {
                "t": 100,
                "type": tf.RecordType.INCREMENTAL_SNAPSHOT,
                "data": {
                    "source": tf.IncrementalSource.MUTATION,
                    "type": "attributes",
                    "target": {"nodeId": 4, "tag": "DIV", "id": "box", "selector": "#box"},
                    "attributes": [
                        {"id": 4, "name": "class", "oldValue": "a b", "value": "a c"}
                    ],
                },
            },
            {
                "t": 200,
                "type": tf.RecordType.INCREMENTAL_SNAPSHOT,
                "data": {
                    "source": tf.IncrementalSource.MUTATION,
                    "type": "characterData",
                    "target": {"nodeId": 5, "tag": "DIV", "id": None, "selector": "#box"},
                    "texts": [{"id": 5, "oldValue": "hi", "value": "bye"}],
                },
            },
            {
                "t": 300,
                "type": tf.RecordType.INCREMENTAL_SNAPSHOT,
                "data": {
                    "source": tf.IncrementalSource.MUTATION,
                    "type": "childList",
                    "target": {"nodeId": 4, "tag": "DIV", "id": "box", "selector": "#box"},
                    "addedCount": 1,
                    "removedCount": 1,
                    "adds": [
                        {
                            "parentId": 4,
                            "nextId": None,
                            "node": {
                                "id": 6,
                                "type": tf.NodeType.ELEMENT,
                                "tagName": "SPAN",
                                "attributes": {"id": "new"},
                                "childNodes": [],
                            },
                        }
                    ],
                    "removes": [{"parentId": 4, "nodeId": 4, "tag": "DIV", "id": "box", "selector": "#box"}],
                },
            },
        ],
        "events": [],
    }


def test_is_rrweb_trace_detects_new_and_legacy():
    assert tf.is_rrweb_trace(_sample_rrweb_trace())
    assert not tf.is_rrweb_trace({"mutations": []})


def test_index_and_html_reconstruction():
    raw = _sample_rrweb_trace()
    index = tf.index_snapshot(raw["snapshot"]["node"])
    assert set(index) >= {1, 2, 3, 4, 5}
    html = tf.node_to_html(index[4])
    assert html.startswith('<div id="box" class="a b">')
    assert "hi" in html


def test_records_convert_to_mutation_dicts():
    raw = _sample_rrweb_trace()
    muts = tf.to_mutation_dicts(raw)
    assert len(muts) == 3
    assert muts[0]["type"] == "attributes"
    assert muts[0]["attributeName"] == "class"
    assert muts[0]["newValue"] == "a c"
    assert muts[1]["type"] == "characterData"
    assert muts[1]["newValue"] == "bye"
    assert muts[2]["type"] == "childList"
    assert muts[2]["addedCount"] == 1
    assert muts[2]["added"][0]["id"] == "new"
    assert "<span" in muts[2]["added"][0]["html"]


def test_trace_load_new_format(tmp_path):
    path = tmp_path / "t.json"
    path.write_text(json.dumps(_sample_rrweb_trace()), encoding="utf-8")
    trace = Trace.load(str(path))
    assert trace.meta.url == "https://example.com"
    assert len(trace.mutations) == 3
    assert all(isinstance(m, Mutation) for m in trace.mutations)
    by_t = trace.group_by_time()
    assert set(by_t) == {100, 200, 300}


def test_trace_load_legacy_format(tmp_path):
    legacy = {
        "meta": {"url": "https://old.example", "viewport": {"w": 800, "h": 600}},
        "count": 1,
        "mutations": [
            {
                "t": 5,
                "type": "attributes",
                "target": {"tag": "DIV", "id": "x", "selector": "#x", "text": "..."},
                "attributeName": "class",
                "oldValue": "a",
                "newValue": "b",
            }
        ],
    }
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")
    trace = Trace.load(str(path))
    assert trace.meta.url == "https://old.example"
    assert len(trace.mutations) == 1
    assert trace.mutations[0].new_value == "b"


def test_rebuild_html_at_each_record_point():
    raw = _sample_rrweb_trace()

    # t=0: only the full snapshot.
    snap_only = tf.rebuild_html(raw, at_ms=0)
    assert 'class="a b"' in snap_only
    assert ">hi<" in snap_only
    assert "<span" not in snap_only

    # t=150: class attribute mutation applied, text untouched.
    at_150 = tf.rebuild_html(raw, at_ms=150)
    assert 'class="a c"' in at_150
    assert ">hi<" in at_150

    # t=250: characterData applied -> "hi" became "bye".
    at_250 = tf.rebuild_html(raw, at_ms=250)
    assert ">bye<" in at_250
    assert ">hi<" not in at_250

    # t>=300: childList added the <span id="new">.
    at_300 = tf.rebuild_html(raw, at_ms=300)
    assert '<span id="new"></span>' in at_300
    assert at_300.startswith("<html>")

    # all records == no cutoff.
    assert tf.rebuild_html(raw) == at_300


def test_dom_state_is_idempotent_for_repeated_adds():
    raw = _sample_rrweb_trace()
    state = tf.DomState.at(raw, 300)
    before = state.to_html()
    state.apply(raw["records"][2])  # re-apply the same childList add
    assert state.to_html() == before
    assert state.stats["skipped_duplicates"] >= 1


def test_dom_state_remove_unregisters_node():
    raw = _sample_rrweb_trace()
    state = tf.DomState.at(raw, 300)
    assert 6 in state.mirror  # span added at t=300
    # A remove of a node reuses the snapshot index path; build a small record.
    state.apply(
        {
            "t": 400,
            "type": tf.RecordType.INCREMENTAL_SNAPSHOT,
            "data": {
                "source": tf.IncrementalSource.MUTATION,
                "type": "childList",
                "target": {"nodeId": 4, "tag": "DIV", "id": "box", "selector": "#box"},
                "addedCount": 0,
                "removedCount": 1,
                "adds": [],
                "removes": [
                    {"parentId": 4, "nodeId": 6, "isParent": True,
                     "tag": "SPAN", "id": "new", "selector": "#new"}
                ],
            },
        }
    )
    assert "<span" not in state.to_html()
    assert 6 not in state.mirror


def test_render_node_document_root():
    raw = _sample_rrweb_trace()
    assert tf.render_node(raw["snapshot"]["node"]).startswith("<html>")
    assert tf.render_node(None) == ""
