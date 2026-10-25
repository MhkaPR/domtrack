from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from typing import Iterable


CSV_FIELDS: tuple[str, ...] = (
    "t", "type",
    "target.tag", "target.id", "target.selector",
    "attributeName", "oldValue", "newValue",
    "addedCount", "removedCount",
)

_TRUNCATE = 200


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def save_json(payload: dict, out_dir: str, stamp: str | None = None) -> str:
    ensure_dir(out_dir)
    stamp = stamp or timestamp()
    path = os.path.join(out_dir, f"trace_{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def save_csv(
    mutations: Iterable[dict],
    out_dir: str,
    stamp: str | None = None,
) -> str:
    ensure_dir(out_dir)
    stamp = stamp or timestamp()
    path = os.path.join(out_dir, f"trace_{stamp}.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for m in mutations:
            writer.writerow(_flatten(m))
    return path


def _flatten(m: dict) -> dict:
    tgt = m.get("target") or {}
    return {
        "t": m.get("t"),
        "type": m.get("type"),
        "target.tag": tgt.get("tag"),
        "target.id": tgt.get("id"),
        "target.selector": _truncate(tgt.get("selector")),
        "attributeName": m.get("attributeName"),
        "oldValue": _truncate(m.get("oldValue")),
        "newValue": _truncate(m.get("newValue")),
        "addedCount": m.get("addedCount"),
        "removedCount": m.get("removedCount"),
    }


def _truncate(v) -> str | None:
    if v is None:
        return None
    s = str(v).replace("\n", " ")
    return s if len(s) <= _TRUNCATE else s[: _TRUNCATE] + "…"