from __future__ import annotations

import re
from typing import Optional

from .models import Mutation

_CLASS_TOKEN_RE = re.compile(r"\.[\w-]+")


def mutation_dynamic_classes(mutation: Mutation) -> set[str]:
    """Classes added or removed by a single class-attribute mutation."""
    if mutation.type != "attributes" or mutation.attribute_name != "class":
        return set()
    old = set((mutation.old_value or "").split())
    new = set((mutation.new_value or "").split())
    return old.symmetric_difference(new)


def strip_classes(selector: Optional[str], classes: set[str]) -> Optional[str]:
    """Remove only the specified classes from a selector."""
    if not selector or not classes:
        return selector
    out: list[str] = []
    for part in selector.split(" > "):
        for cls in classes:
            part = re.sub(rf"\.{re.escape(cls)}(?=[.:#\s>]|$)", "", part)
        if part.strip("."):
            out.append(part)
    return " > ".join(out) if out else None


def strip_all_classes(selector: Optional[str]) -> Optional[str]:
    """Universal fallback: remove every .class token from a selector."""
    if not selector:
        return None
    out: list[str] = []
    for part in selector.split(" > "):
        stripped = _CLASS_TOKEN_RE.sub("", part)
        if stripped and stripped != ".":
            out.append(stripped)
    return " > ".join(out) if out else None