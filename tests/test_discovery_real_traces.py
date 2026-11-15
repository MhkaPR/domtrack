"""Real-trace scenario tests for table discovery.

These fixtures are rrweb-style traces (no HTML) with Tailwind-heavy markup.
They exercise the LCS path similarity against wrapper insertions/removals.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from domtrack.tables import discovery


FIXTURES = Path(__file__).parent / "fixtures" / "traces"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _first_table(name: str) -> dict:
    result = discovery.discover_tables(
        _load(name), discovery.DiscoveryConfig(min_cols=2)
    )
    assert result["tables_returned"] >= 1, f"{name}: no table discovered"
    return result["tables"][0]


def _column_values(table: dict, column: dict) -> list[str]:
    return [row[column["id"]] for row in table["rows"]]


@pytest.mark.parametrize("name", ["wrapper_inserted", "wrapper_removed"])
def test_wrapper_scenarios_keep_all_rows_and_columns(name):
    table = _first_table(name)
    assert table["meta"]["row_count"] == 4
    assert table["meta"]["columns"] == 3

    by_path = {c["path"]: c for c in table["columns"]}
    title_col = next(
        c
        for c in table["columns"]
        if any("هدفون" in value for value in _column_values(table, c))
    )
    price_col = next(
        c
        for c in table["columns"]
        if any("تومان" in value for value in _column_values(table, c))
    )
    badge_col = next(
        c
        for c in table["columns"]
        if any(value == "جدید" for value in _column_values(table, c))
    )

    assert _column_values(table, title_col) == [
        "هدفون بی‌سیم استودیویی",
        "کیف چرم دست‌دوز",
        "ساعت هوشمند ورزشی",
        "عینک آفتابی پلاریزه",
    ]
    assert _column_values(table, price_col) == [
        "۲۵۰,۰۰۰ تومان",
        "۴۸۰,۰۰۰ تومان",
        "۳۲۰,۰۰۰ تومان",
        "۱۹۰,۰۰۰ تومان",
    ]
    assert _column_values(table, badge_col) == ["جدید", "جدید", "جدید", "جدید"]
    assert len(by_path) == 3
