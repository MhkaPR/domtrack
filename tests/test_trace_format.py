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


def _write_sample(tmp_path, raw=None):
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(raw or _sample_rrweb_trace()), encoding="utf-8")
    return str(path)


def test_html_cli_rebuilds_at_ms(tmp_path):
    from domtrack.html import cli

    trace_path = _write_sample(tmp_path)
    out = tmp_path / "out.html"

    rc = cli.main([trace_path, "--at-ms", "250", "--out", str(out)])
    assert rc == 0
    html = out.read_text(encoding="utf-8")
    assert ">bye<" in html and "<span" not in html
    assert html == tf.rebuild_html(_sample_rrweb_trace(), at_ms=250)


def test_html_cli_at_index_matches_at_ms(tmp_path):
    from domtrack.html import cli

    raw = _sample_rrweb_trace()
    trace_path = _write_sample(tmp_path, raw)
    out = tmp_path / "out.html"

    # index 2 is the childList record at t=300.
    assert cli.main([trace_path, "--at-index", "2", "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == tf.rebuild_html(raw, at_ms=300)


def test_html_cli_split_inputs(tmp_path):
    from domtrack.html import cli

    raw = _sample_rrweb_trace()
    snap = tmp_path / "snapshot.json"
    recs = tmp_path / "records.json"
    snap.write_text(json.dumps(raw["snapshot"]), encoding="utf-8")
    recs.write_text(json.dumps(raw["records"]), encoding="utf-8")
    out = tmp_path / "out.html"

    rc = cli.main([str(snap), str(recs), "--out", str(out)])
    assert rc == 0
    assert out.read_text(encoding="utf-8") == tf.rebuild_html(raw)


def test_html_cli_argument_errors(tmp_path):
    from domtrack.html import cli

    trace_path = _write_sample(tmp_path)
    assert cli.main([trace_path, "--at-ms", "1", "--at-index", "1"]) == 2
    assert cli.main(["a.json", "b.json", "c.json"]) == 2
    assert cli.main([str(tmp_path / "missing.json")]) == 1

    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"mutations": []}), encoding="utf-8")
    assert cli.main([str(legacy)]) == 1


# ── content diff (domtrack.diff) ───────────────────────────────────────────

def _price_trace(items, extra=None):
    """Trace with an <h1> and a <ul> of <li><h2>name</h2><p>price</p></li>."""
    counter = iter(range(10, 10_000))
    children = []
    for name, price in items:
        children.append(
            {"type": 2, "id": next(counter), "tagName": "LI", "attributes": {},
             "childNodes": [
                 {"type": 2, "id": next(counter), "tagName": "H2", "attributes": {},
                  "childNodes": [{"type": 3, "id": next(counter), "textContent": name}]},
                 {"type": 2, "id": next(counter), "tagName": "P", "attributes": {},
                  "childNodes": [{"type": 3, "id": next(counter), "textContent": price}]},
             ]}
        )
    body_children = [
        {"type": 2, "id": next(counter), "tagName": "H1", "attributes": {},
         "childNodes": [{"type": 3, "id": next(counter), "textContent": "Plans"}]},
        {"type": 2, "id": next(counter), "tagName": "UL", "attributes": {},
         "childNodes": children},
    ]
    if extra:
        body_children.append(extra)
    return {
        "version": 2,
        "snapshot": {
            "type": tf.RecordType.FULL_SNAPSHOT,
            "node": {"id": 1, "type": tf.NodeType.DOCUMENT, "childNodes": [
                {"id": 2, "type": tf.NodeType.ELEMENT, "tagName": "HTML",
                 "attributes": {}, "childNodes": [
                     {"id": 3, "type": tf.NodeType.ELEMENT, "tagName": "BODY",
                      "attributes": {}, "childNodes": body_children}]}]},
        },
        "records": [],
    }


def test_content_view_paths_and_filters():
    from domtrack import diff

    raw = _price_trace([("Bronze", "$5"), ("Silver", "$10")])
    view = diff.view_of(raw)
    # class/id/style are non-data attributes and are dropped.
    assert "/html[0]/body[0]/h1[0]" in view
    assert view["/html[0]/body[0]/ul[0]/li[0]/h2[0]"].text == "Bronze"
    assert view["/html[0]/body[0]/ul[0]/li[1]/p[0]"].text == "$10"
    # A blank/whitespace-only text node yields no entry.
    assert all(entry.text.strip() for entry in view.values())


def test_diff_detects_added_removed_changed():
    from domtrack import diff

    a = _price_trace([("Bronze", "$5"), ("Silver", "$10")])
    b = _price_trace([("Free", "$0"), ("Bronze", "$5"), ("Silver", "$10")])
    result = diff.diff_traces(a, b)
    assert result.summary["added"] == 2
    assert result.summary["removed"] == 0
    assert result.summary["changed"] == 4

    changed_paths = {c.path for c in result.changed}
    assert "/html[0]/body[0]/ul[0]/li[0]/h2[0]" in changed_paths

    # Removed: compare the other direction.
    back = diff.diff_traces(b, a)
    assert back.summary["removed"] == 2


def _price_landmarks(raw):
    """Locate the <ul>, its first <p> and that <p>'s text node id."""
    from domtrack import diff

    ids = {}
    for node, path, tag in diff.iter_nodes(raw["snapshot"]["node"]):
        if tag == "ul":
            ids["ul"] = node["id"]
        if tag == "p" and path.endswith("/li[0]/p[0]"):
            ids["p"] = node["id"]
            ids["price_text"] = node["childNodes"][0]["id"]
    return ids


def test_diff_applies_records_childlist_and_text():
    from domtrack import diff

    a = _price_trace([("Bronze", "$5")])
    b = _price_trace([("Bronze", "$5")])
    ids = _price_landmarks(b)
    # Simulate B changing the existing price and appending a new <li>.
    b["records"] = [
        {"t": 100, "type": tf.RecordType.INCREMENTAL_SNAPSHOT, "data": {
            "source": tf.IncrementalSource.MUTATION, "type": "characterData",
            "target": {"nodeId": ids["p"], "tag": "P", "id": None, "selector": "p"},
            "texts": [{"id": ids["price_text"], "oldValue": "$5", "value": "$0"}]}},
        {"t": 200, "type": tf.RecordType.INCREMENTAL_SNAPSHOT, "data": {
            "source": tf.IncrementalSource.MUTATION, "type": "childList",
            "target": {"nodeId": ids["ul"], "tag": "UL", "id": None, "selector": "ul"},
            "addedCount": 1, "removedCount": 0, "removes": [],
            "adds": [{"parentId": ids["ul"], "nextId": None, "node": {
                "type": 2, "id": 900, "tagName": "LI", "attributes": {},
                "childNodes": [
                    {"type": 2, "id": 901, "tagName": "H2", "attributes": {},
                     "childNodes": [{"type": 3, "id": 902, "textContent": "Silver"}]},
                    {"type": 2, "id": 903, "tagName": "P", "attributes": {},
                     "childNodes": [{"type": 3, "id": 904, "textContent": "$10"}]},
                ]}}]}},
    ]
    result = diff.diff_traces(a, b)
    assert result.summary["added"] == 2          # Silver li + its p
    assert result.summary["changed"] == 1       # $5 -> $0

    # --no-records ignores the record stream.
    snap_only = diff.diff_traces(a, b, apply_records=False)
    assert snap_only.summary["added"] == 0
    assert snap_only.summary["changed"] == 0


def test_diff_at_ms_caps_records():
    from domtrack import diff

    a = _price_trace([("Bronze", "$5")])
    b = _price_trace([("Bronze", "$5")])
    ids = _price_landmarks(b)
    b["records"] = [
        {"t": 100, "type": tf.RecordType.INCREMENTAL_SNAPSHOT, "data": {
            "source": tf.IncrementalSource.MUTATION, "type": "characterData",
            "target": {"nodeId": ids["p"], "tag": "P", "id": None, "selector": "p"},
            "texts": [{"id": ids["price_text"], "oldValue": "$5", "value": "$0"}]}},
    ]
    assert diff.diff_traces(a, b, at_ms_b=50).summary["changed"] == 0
    assert diff.diff_traces(a, b, at_ms_b=100).summary["changed"] == 1


def test_diff_cli_text_json_and_errors(tmp_path):
    from domtrack.diff import cli

    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps(_price_trace([("Bronze", "$5")])), encoding="utf-8")
    b.write_text(json.dumps(_price_trace([("Free", "$0")])), encoding="utf-8")

    assert cli.main([str(a), str(b)]) == 0
    assert cli.main([str(a), str(b), "--json"]) == 0
    assert cli.main([str(a), str(b), "--no-records"]) == 0
    assert cli.main([str(a), str(tmp_path / "missing.json")]) == 1

    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"mutations": []}), encoding="utf-8")
    assert cli.main([str(a), str(legacy)]) == 1


# ── replay-time recording & compare (no browser needed) ────────────────────

def _raw_payload():
    """A browser-shaped payload as COLLECT_SCRIPT would return it."""
    return {
        "meta": {"url": "https://example.com", "title": "t",
                 "viewport": {"w": 1000, "h": 700}},
        "count": 1,
        "snapshot": {"node": {"id": 1, "type": 0, "childNodes": []},
                     "initialOffset": {"top": 0, "left": 0}},
        "records": [{"t": 5, "type": 3, "data": {
            "source": 0, "type": "attributes",
            "target": {"nodeId": 1, "tag": "BODY", "id": None, "selector": "body"},
            "attributes": [{"id": 1, "name": "data-x", "value": "1"}]}}],
        "events": [{"t": 1, "kind": "click"}],
    }


def test_capture_session_build_payload_and_save(tmp_path):
    from domtrack.capture import CaptureSession
    from domtrack import TracerConfig

    session = CaptureSession(TracerConfig(url="https://example.com", headless=True))
    payload = session.build_payload(_raw_payload())

    # rrweb layout with a FullSnapshot wrapper + records + events.
    assert payload["version"] == 2
    assert payload["snapshot"]["type"] == tf.RecordType.FULL_SNAPSHOT
    assert payload["snapshot"]["node"]["id"] == 1
    assert payload["count"] == 1
    assert len(payload["records"]) == 1
    assert payload["events"][0]["kind"] == "click"

    result = session.save_result(payload, out_dir=str(tmp_path), stamp="fixed")
    assert result.mutation_count == 1
    assert result.event_count == 1
    written = json.loads(open(result.json_path, encoding="utf-8").read())
    assert written["count"] == 1
    assert tf.is_rrweb_trace(written)


def test_recorder_uses_shared_capture_session():
    from domtrack import TracerConfig
    from domtrack.recorder import CaptureSession, Recorder

    cfg = TracerConfig(url="https://example.com", headless=True)
    rec = Recorder(cfg)
    assert isinstance(rec.session, CaptureSession)
    # The legacy static helper still delegates to the shared payload builder.
    assert rec._build_payload(_raw_payload()) == \
        CaptureSession.build_payload(_raw_payload())


def test_recording_config_builds_tracer_config():
    from domtrack.replay.recording import RecordingConfig

    rc = RecordingConfig(output_dir="out", capture_events=True, max_nodes=7)
    cfg = rc.to_tracer_config("https://x.test", viewport={"width": 800, "height": 600})
    assert cfg.url == "https://x.test"
    assert cfg.viewport == {"width": 800, "height": 600}
    assert cfg.output_dir == "out"
    assert cfg.capture_events is True
    assert cfg.max_nodes == 7
    assert cfg.headless is True

    # An explicit tracer wins outright.
    from domtrack import TracerConfig

    explicit = TracerConfig(url="https://y.test", headless=True)
    rc2 = RecordingConfig(tracer=explicit)
    assert rc2.to_tracer_config("https://ignored.test") is explicit


def test_replay_recorder_requires_start():
    from domtrack.replay.recording import RecordingConfig, ReplayRecorder

    recorder = ReplayRecorder(RecordingConfig(enabled=True))
    with pytest.raises(RuntimeError):
        recorder.finish(page=None)          # session not started yet
    with pytest.raises(RuntimeError):
        recorder.save({})


def test_replay_config_accepts_recording():
    from domtrack.replay.recording import RecordingConfig
    from domtrack.replay.replayer import ReplayConfig

    assert ReplayConfig().recording is None
    rc = RecordingConfig(enabled=True, output_dir="out")
    assert ReplayConfig(recording=rc).recording is rc


def test_compare_traces_and_result_summary(tmp_path):
    from domtrack.replay.compare import compare_traces
    from domtrack.replay.compare import CompareResult
    from domtrack.replay.replayer import ReplayStats
    from domtrack import diff as diff_mod

    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps(_price_trace([("Bronze", "$5")])), encoding="utf-8")
    b.write_text(json.dumps(_price_trace([("Free", "$0")])), encoding="utf-8")

    result = compare_traces(str(a), str(b))
    assert result.summary["changed"] == 2

    # A non-rrweb file is rejected.
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"mutations": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        compare_traces(str(a), str(legacy))

    merged = CompareResult(
        before_path=str(a), after_path=str(b), after_csv_path=None,
        stats=ReplayStats(applied=3, skipped=1, failed=2),
        diff=result,
        replay_mutations=10, recorded_mutations=4, recorded_events=0,
    )
    s = merged.summary
    assert s["replay_mutations"] == 10
    assert s["recorded_mutations"] == 4
    assert s["changed"] == result.summary["changed"]
