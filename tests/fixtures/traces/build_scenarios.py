"""Generate rrweb-style trace fixtures for discovery scenario tests.

Run from the repository root:

    python tests/fixtures/traces/build_scenarios.py
"""

from __future__ import annotations

import json
from pathlib import Path


class Builder:
    def __init__(self) -> None:
        self.next_id = 10

    def _id(self) -> int:
        self.next_id += 1
        return self.next_id

    def text(self, value: str) -> dict:
        return {"id": self._id(), "type": 3, "textContent": value}

    def el(self, tag: str, classes: str = "", children=None, **attrs) -> dict:
        attributes = {"class": classes} if classes else {}
        attributes.update(attrs)
        return {
            "id": self._id(),
            "type": 2,
            "tagName": tag.upper(),
            "attributes": attributes,
            "childNodes": children or [],
        }

    def trace(self, body: dict) -> dict:
        html = self.el("html", "h-full scroll-smooth", [body])
        document = {
            "id": 1,
            "type": 0,
            "childNodes": [html],
        }
        return {
            "meta": {"url": "https://example.test/scenario", "version": 2},
            "version": 2,
            "snapshot": {"type": 2, "node": document},
            "records": [],
            "events": [],
        }


PAGE_MAIN = (
    "mx-auto max-w-7xl px-4 py-10 sm:px-6 lg:px-8 "
    "bg-gradient-to-b from-slate-50 to-white"
)
GRID = (
    "grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3 "
    "divide-y divide-slate-200 lg:divide-y-0"
)
CARD = (
    "group relative flex flex-col gap-3 rounded-2xl border border-slate-200 "
    "bg-white p-5 shadow-sm transition hover:-translate-y-0.5 "
    "hover:border-indigo-200 hover:shadow-lg"
)
ROW = "flex flex-wrap items-center justify-between gap-3"
PRICE = (
    "text-lg font-bold tracking-tight text-slate-900 "
    "tabular-nums sm:text-xl"
)
BADGE = (
    "inline-flex items-center rounded-full bg-emerald-500 px-2.5 py-0.5 "
    "text-xs font-semibold text-white shadow-sm ring-1 ring-emerald-600/20"
)
TITLE = "text-base font-semibold leading-6 text-slate-800 line-clamp-2"
WRAP = (
    "flex w-full items-center justify-between gap-2 rounded-xl "
    "bg-slate-50 p-2 ring-1 ring-inset ring-slate-200"
)
CELL = (
    "inline-flex min-w-0 items-center rounded-lg bg-white px-3 py-1.5 "
    "text-sm font-medium text-slate-700 shadow-sm ring-1 ring-slate-200"
)


def _card(b: Builder, title: str, price: str, badge: str, wrapped: bool) -> dict:
    price_node = b.el("span", PRICE, [b.text(price)])
    badge_node = b.el("span", BADGE, [b.text(badge)])
    if wrapped:
        slots = b.el("div", WRAP, [price_node, badge_node])
    else:
        slots = b.el("div", ROW, [price_node, badge_node])
    return b.el(
        "li",
        CARD,
        [b.el("h3", TITLE, [b.text(title)]), slots],
    )


def wrapper_inserted() -> dict:
    b = Builder()
    products = [
        ("هدفون بی‌سیم استودیویی", "۲۵۰,۰۰۰ تومان", "جدید", False),
        ("کیف چرم دست‌دوز", "۴۸۰,۰۰۰ تومان", "جدید", True),
        ("ساعت هوشمند ورزشی", "۳۲۰,۰۰۰ تومان", "جدید", False),
        ("عینک آفتابی پلاریزه", "۱۹۰,۰۰۰ تومان", "جدید", False),
    ]
    cards = [_card(b, *product) for product in products]
    body = b.el("body", "min-h-screen bg-slate-50 antialiased", [
        b.el("main", PAGE_MAIN, [
            b.el("ul", GRID, cards),
        ])
    ])
    return b.trace(body)


def wrapper_removed() -> dict:
    b = Builder()
    products = [
        ("هدفون بی‌سیم استودیویی", "۲۵۰,۰۰۰ تومان", "جدید", True),
        ("کیف چرم دست‌دوز", "۴۸۰,۰۰۰ تومان", "جدید", True),
        ("ساعت هوشمند ورزشی", "۳۲۰,۰۰۰ تومان", "جدید", True),
        ("عینک آفتابی پلاریزه", "۱۹۰,۰۰۰ تومان", "جدید", False),
    ]
    cards = [_card(b, *product) for product in products]
    body = b.el("body", "min-h-screen bg-slate-50 antialiased", [
        b.el("main", PAGE_MAIN, [
            b.el("ul", GRID, cards),
        ])
    ])
    return b.trace(body)


def _sparse_row(b: Builder, values: list[str]) -> dict:
    cells = [b.el("span", CELL, [b.text(value)]) for value in values]
    return b.el("li", CARD, [b.el("div", ROW, cells)])


def sparse_slots_fa() -> dict:
    b = Builder()
    rows = [
        _sparse_row(b, ["۲۵۰,۰۰۰ تومان", "جدید"]),
        _sparse_row(b, ["جدید"]),
        _sparse_row(b, ["۴۸۰,۰۰۰ تومان"]),
        _sparse_row(b, ["افزودن به علاقه‌مندی"]),
    ]
    body = b.el("body", "min-h-screen bg-slate-50 antialiased", [
        b.el("main", PAGE_MAIN, [b.el("ul", GRID, rows)])
    ])
    return b.trace(body)


def sparse_slots_en() -> dict:
    b = Builder()
    rows = [
        _sparse_row(b, ["250,000 Toman", "New"]),
        _sparse_row(b, ["New"]),
        _sparse_row(b, ["480,000 Toman"]),
        _sparse_row(b, ["Heart Icon"]),
    ]
    body = b.el("body", "min-h-screen bg-slate-50 antialiased", [
        b.el("main", PAGE_MAIN, [b.el("ul", GRID, rows)])
    ])
    return b.trace(body)


def sparse_many() -> dict:
    b = Builder()
    rows = [_sparse_row(b, ["۲۵۰,۰۰۰ تومان", "جدید"])]
    rows += [_sparse_row(b, ["جدید"]) for _ in range(8)]
    rows += [_sparse_row(b, ["۴۸۰,۰۰۰ تومان"]) for _ in range(8)]
    rows.append(_sparse_row(b, ["افزودن به علاقه‌مندی"]))
    body = b.el("body", "min-h-screen bg-slate-50 antialiased", [
        b.el("main", PAGE_MAIN, [b.el("ul", GRID, rows)])
    ])
    return b.trace(body)


SCENARIOS = {
    "wrapper_inserted": wrapper_inserted,
    "wrapper_removed": wrapper_removed,
    "sparse_slots_fa": sparse_slots_fa,
    "sparse_slots_en": sparse_slots_en,
    "sparse_many": sparse_many,
}


def main() -> None:
    out_dir = Path(__file__).resolve().parent
    for name, build in SCENARIOS.items():
        path = out_dir / f"{name}.json"
        path.write_text(
            json.dumps(build(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
