"""Column labels, paths, row identity, and table de-duplication."""

from __future__ import annotations

from .model import Atom


def best_class(classes: frozenset[str]) -> str:
    return sorted(classes, key=lambda c: (len(c), c), reverse=True)[0] if classes else ""


def path_str(path: tuple) -> str:
    return " > ".join(
        f"{tag}.{best_class(cls)}" if cls else tag for tag, cls in path
    )


def column_id(column: dict) -> str:
    base = f"{path_str(column['path'])}@{column['atype']}"
    slot = column.get("slot")
    return f"{base}#{slot}" if slot is not None else base


def column_label(column: dict) -> str:
    path = column["path"]
    if not path:
        return column["atype"]
    tag, classes = path[-1]
    base = best_class(classes) or tag
    label = base if column["atype"] == "text" else f"{base}.{column['atype']}"
    slot = column.get("slot")
    return f"{label}#{slot}" if slot is not None else label


def row_identity(atoms: list[Atom]) -> tuple[str, str]:
    for atype in ("href", "src", "title", "alt", "text"):
        values = sorted(a.value for a in atoms if a.atype == atype)
        if values:
            return (atype, values[0])
    return ("empty", "")


def table_fingerprint(table: dict) -> frozenset:
    """Data fingerprint of a table: its non-empty ``(column_id, value)`` cells."""
    fingerprint = set()
    for row in table.get("rows") or []:
        for cid, value in row.items():
            if value:
                fingerprint.add((cid, value))
    return frozenset(fingerprint)


def fingerprint_similarity(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_subset_of(a: frozenset, b: frozenset, ratio: float = 0.85) -> bool:
    if not a:
        return False
    return len(a & b) / len(a) >= ratio


def dedupe(tables: list[dict], threshold: float) -> list[dict]:
    kept: list[dict] = []
    for table in sorted(tables, key=lambda t: -t["score"]):
        fingerprint = table["_fingerprint"]
        if not fingerprint:
            continue
        duplicate = False
        for other in kept:
            other_fp = other["_fingerprint"]
            if fingerprint_similarity(fingerprint, other_fp) >= threshold:
                duplicate = True
                break
            if is_subset_of(fingerprint, other_fp) or is_subset_of(other_fp, fingerprint):
                duplicate = True
                break
        if not duplicate:
            kept.append(table)
    return kept
