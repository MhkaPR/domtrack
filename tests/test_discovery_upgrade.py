"""Tests for the LCS path metric and positional column extraction."""

from __future__ import annotations

import pytest

from domtrack import trace as tf
from domtrack.tables import discovery


def _text(nid, value):
    return {"id": nid, "type": tf.NodeType.TEXT, "textContent": value}


def _node(nid, tag, attrs=None, children=None):
    return {
        "id": nid,
        "type": tf.NodeType.ELEMENT,
        "tagName": tag.upper(),
        "attributes": attrs or {},
        "childNodes": children or [],
    }


def _sparse_trace() -> dict:
    """Rows whose cells share one path but vary in count (slot-shift risk)."""
    next_id = 100

    def fresh():
        nonlocal next_id
        next_id += 1
        return next_id

    def row(values):
        cells = [
            _node(fresh(), "span", {"class": "cell"}, [_text(fresh(), value)])
            for value in values
        ]
        wrapper = _node(fresh(), "div", {"class": "cells"}, cells)
        return _node(fresh(), "li", {"class": "row"}, [wrapper])

    rows = [
        row(["100", "New"]),
        row(["New"]),
        row(["200"]),
        row(["Heart Icon"]),
    ]
    body = _node(3, "body", {}, [_node(4, "ul", {"class": "rows"}, rows)])
    html = _node(2, "html", {}, [body])
    return {
        "meta": {"url": "https://example.com"},
        "version": 2,
        "snapshot": {
            "type": tf.RecordType.FULL_SNAPSHOT,
            "node": {
                "id": 1,
                "type": tf.NodeType.DOCUMENT,
                "childNodes": [html],
            },
        },
        "records": [],
    }


def _wrapper_trace() -> dict:
    """Three rows; the last wraps its cells in an extra ``div.flex``."""
    next_id = 500

    def fresh():
        nonlocal next_id
        next_id += 1
        return next_id

    def row(values, wrapper_class):
        cells = [
            _node(fresh(), "span", {"class": "cell"}, [_text(fresh(), value)])
            for value in values
        ]
        wrapper = _node(fresh(), "div", {"class": wrapper_class}, cells)
        return _node(fresh(), "li", {"class": "row"}, [wrapper])

    rows = [
        row(["100", "New"], "cells"),
        row(["200", "New"], "cells"),
        row(["300", "New"], "flex"),
    ]
    body = _node(3, "body", {}, [_node(4, "ul", {"class": "rows"}, rows)])
    html = _node(2, "html", {}, [body])
    return {
        "meta": {"url": "https://example.com"},
        "version": 2,
        "snapshot": {
            "type": tf.RecordType.FULL_SNAPSHOT,
            "node": {
                "id": 1,
                "type": tf.NodeType.DOCUMENT,
                "childNodes": [html],
            },
        },
        "records": [],
    }


def test_lcs_path_sim_tolerates_wrapper_insertion():
    base = (
        ("ul", frozenset()),
        ("li", frozenset()),
        ("span", frozenset({"price"})),
    )
    wrapped = (
        ("ul", frozenset()),
        ("li", frozenset()),
        ("div", frozenset({"flex"})),
        ("span", frozenset({"price"})),
    )
    assert discovery.lcs_path_sim(base, wrapped) >= 0.6
    assert discovery.lcs_path_sim(base, base) == 1.0


def test_wrapper_row_still_groups_with_siblings():
    result = discovery.discover_tables(
        _wrapper_trace(), discovery.DiscoveryConfig(min_cols=2)
    )
    assert result["tables_returned"] == 1
    assert result["tables"][0]["meta"]["row_count"] == 3


def test_default_path_sim_is_lcs():
    interfaces = discovery.DiscoveryConfig().interfaces()
    assert interfaces.path_sim is discovery.lcs_path_sim


def test_custom_policy_subclass_is_used():
    class CountingPolicy(discovery.DiscoveryPolicy):
        def __init__(self):
            self.calls = 0

        def path_sim(self, p1, p2):
            self.calls += 1
            return discovery.lcs_path_sim(p1, p2)

    policy = CountingPolicy()
    discovery.discover_tables(
        _sparse_trace(),
        discovery.DiscoveryConfig(policy=policy, min_cols=2),
    )
    assert policy.calls > 0


