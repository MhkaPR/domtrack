"""Tests for the domtrack REST API (browser-free endpoints + wiring).

These exercise the HTTP surface without launching Playwright: capture / replay
/ compare are covered by their service layer and CLI tests, while everything
that touches only the trace format is driven through the ASGI app.
"""

from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from domtrack import trace as tf  # noqa: E402
from domtrack.api.app import create_app  # noqa: E402
from domtrack.api.config import ApiSettings  # noqa: E402


def _sample_trace() -> dict:
    """A tiny rrweb trace: one <div id="box"> whose class and text change."""
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
                                            {
                                                "id": 5,
                                                "type": tf.NodeType.TEXT,
                                                "textContent": "hi",
                                            }
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
                    "type": "characterData",
                    "target": {"nodeId": 5, "tag": "DIV", "id": None, "selector": "#box"},
                    "texts": [{"id": 5, "oldValue": "hi", "value": "bye"}],
                },
            }
        ],
        "events": [],
    }


def _card_trace() -> dict:
    """A <ul> of three <li> rows — enough for table discovery."""
    def text(nid, value):
        return {"id": nid, "type": tf.NodeType.TEXT, "textContent": value}

    def node(nid, tag, attrs, children):
        return {
            "id": nid,
            "type": tf.NodeType.ELEMENT,
            "tagName": tag.upper(),
            "attributes": attrs,
            "childNodes": children,
        }

    next_id = 10

    def card(title, price):
        nonlocal next_id
        base = next_id
        next_id += 3
        return node(
            base, "li", {"class": "card"},
            [
                node(base + 1, "span", {"class": "title"}, [text(base + 100, title)]),
                node(base + 2, "span", {"class": "price"}, [text(base + 200, price)]),
            ],
        )

    cards = [card("Alpha", "10"), card("Beta", "20"), card("Gamma", "30")]
    body = node(3, "body", {}, [node(4, "ul", {"class": "cards"}, cards)])
    html = node(2, "html", {}, [body])
    return {
        "meta": {"url": "https://example.com"},
        "version": 2,
        "snapshot": {
            "type": tf.RecordType.FULL_SNAPSHOT,
            "node": {"id": 1, "type": tf.NodeType.DOCUMENT, "childNodes": [html]},
        },
        "records": [],
    }


@pytest.fixture()
def client(tmp_path):
    settings = ApiSettings(trace_dir=str(tmp_path), cors_origins=("*",))
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture()
def trace_dir(tmp_path):
    return tmp_path


# ── service metadata ───────────────────────────────────────────────────────

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"]


def test_openapi_lists_all_cli_endpoints(client):
    schema = client.get("/openapi.json").json()
    paths = set(schema["paths"])
    assert {
        "/capture", "/replay", "/compare", "/html", "/diff",
        "/tables/extract", "/tables/label", "/tables/show",
    } <= paths


def test_traces_empty_then_listed(client, trace_dir):
    r = client.get("/traces")
    assert r.json()["count"] == 0

    (trace_dir / "trace_x.json").write_text(json.dumps(_sample_trace()), encoding="utf-8")
    r = client.get("/traces")
    body = r.json()
    assert body["count"] == 1
    assert body["traces"][0]["name"] == "trace_x.json"


# ── html ───────────────────────────────────────────────────────────────────

def test_html_inline_trace(client):
    r = client.post("/html", json={"trace": {"trace": _sample_trace()}})
    assert r.status_code == 200
    body = r.json()
    assert "bye" in body["html"]
    assert body["stats"]


def test_html_at_ms(client):
    r = client.post("/html", json={"trace": {"trace": _sample_trace()}, "at_ms": 0})
    assert r.status_code == 200
    assert "hi" in r.json()["html"]
    assert "bye" not in r.json()["html"]


def test_html_rejects_both_cutoffs(client):
    r = client.post(
        "/html",
        json={"trace": {"trace": _sample_trace()}, "at_ms": 0, "at_index": 0},
    )
    assert r.status_code == 422


def test_html_missing_trace_404(client):
    r = client.post("/html", json={"trace": {"path": "nope.json"}})
    assert r.status_code == 404


def test_trace_source_requires_exactly_one(client):
    r = client.post("/html", json={"trace": {}})
    assert r.status_code == 422


# ── diff ───────────────────────────────────────────────────────────────────

def test_diff_identical_traces(client):
    r = client.post(
        "/diff",
        json={"a": {"trace": _sample_trace()}, "b": {"trace": _sample_trace()}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["added"] == 0
    assert body["summary"]["removed"] == 0
    assert body["summary"]["changed"] == 0


def test_diff_detects_change(client):
    a = _sample_trace()
    b = _sample_trace()
    b["records"][0]["data"]["texts"][0]["value"] = "changed"
    r = client.post("/diff", json={"a": {"trace": a}, "b": {"trace": b}})
    body = r.json()
    assert body["summary"]["changed"] == 1
    assert body["changed"][0]["text"]["new"] == "changed"


def test_diff_text_format_includes_report(client):
    r = client.post(
        "/diff",
        json={
            "a": {"trace": _sample_trace()},
            "b": {"trace": _sample_trace()},
            "format": "text",
        },
    )
    body = r.json()
    assert "report" in body
    assert "added:" in body["report"]


# ── tables ─────────────────────────────────────────────────────────────────

def test_tables_extract_finds_grid(client):
    r = client.post("/tables/extract", json={"trace": {"trace": _card_trace()}})
    assert r.status_code == 200
    body = r.json()
    assert body["tables_returned"] >= 1


def test_tables_extract_min_rows_filters(client):
    r = client.post(
        "/tables/extract",
        json={"trace": {"trace": _card_trace()}, "min_rows": 10},
    )
    assert r.json()["tables_returned"] == 0


def test_tables_show_plain(client):
    extract = client.post(
        "/tables/extract", json={"trace": {"trace": _card_trace()}}
    ).json()
    r = client.post("/tables/show", json={"data": extract, "format": "plain"})
    assert r.status_code == 200
    body = r.json()
    assert body["format"] == "plain"
    assert "Alpha" in body["content"]


def test_tables_show_markdown(client):
    extract = client.post(
        "/tables/extract", json={"trace": {"trace": _card_trace()}}
    ).json()
    r = client.post("/tables/show", json={"data": extract, "format": "markdown"})
    assert r.status_code == 200
    assert "|" in r.json()["content"]


def test_tables_show_empty_400(client):
    r = client.post("/tables/show", json={"data": {"tables": []}})
    assert r.status_code == 400


def test_tables_label_dry_run(client):
    extract = client.post(
        "/tables/extract", json={"trace": {"trace": _card_trace()}}
    ).json()
    r = client.post(
        "/tables/label", json={"tables": extract, "dry_run": True}
    )
    assert r.status_code == 200
    assert r.json()["dry_run"] is True


def test_tables_label_requires_source(client):
    r = client.post("/tables/label", json={})
    assert r.status_code == 422


def test_tables_label_missing_key_400(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    extract = client.post(
        "/tables/extract", json={"trace": {"trace": _card_trace()}}
    ).json()
    r = client.post("/tables/label", json={"tables": extract})
    assert r.status_code == 400
    assert "API key" in r.json()["detail"]
