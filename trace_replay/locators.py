from __future__ import annotations

from collections import Counter
from typing import Optional

from playwright.sync_api import ElementHandle, Page

from .models import Mutation
from .selectors import (
    mutation_dynamic_classes,
    strip_all_classes,
    strip_classes,
)


class ElementLocator:
    """Find the target element of a mutation using multiple strategies."""

    def __init__(self, page: Page):
        self.page = page
        self._strategy_count: Counter[str] = Counter()

    def find(self, mutation: Mutation) -> tuple[Optional[ElementHandle], Optional[str]]:
        target = mutation.target
        sel = target.selector
        tag = (target.tag or "").lower()

        # 1) by id
        if target.id:
            el = self._try_query_one("#" + target.id)
            if el:
                return self._hit(el, "id")

        # 2) original selector
        if sel:
            el = self._try_query_one(sel)
            if el:
                return self._hit(el, "orig")

        # 3) strip only classes changed by THIS mutation
        dyn = mutation_dynamic_classes(mutation)
        if sel and dyn:
            san = strip_classes(sel, dyn)
            if san and san != sel:
                el, how = self._try_disambiguate(san, tag, "dyn")
                if el:
                    return self._hit(el, how)

        # 4) strip all classes
        if sel:
            stripped = strip_all_classes(sel)
            if stripped and stripped != sel:
                el, how = self._try_disambiguate(stripped, tag, "all")
                if el:
                    return self._hit(el, how)

        return None, None

    @property
    def strategy_stats(self) -> dict[str, int]:
        return dict(self._strategy_count)

    def _try_query_one(self, selector: str) -> Optional[ElementHandle]:
        try:
            return self.page.query_selector(selector)
        except Exception:
            return None

    def _try_disambiguate(
        self, selector: str, tag: str, base: str
    ) -> tuple[Optional[ElementHandle], Optional[str]]:
        try:
            els = self.page.query_selector_all(selector)
        except Exception:
            return None, None
        if len(els) == 1:
            return els[0], base
        if tag:
            matches = [e for e in els if self._safe_tag(e) == tag]
            if len(matches) == 1:
                return matches[0], f"{base}+tag"
        return None, None

    @staticmethod
    def _safe_tag(element: ElementHandle) -> str:
        try:
            return element.evaluate("(n) => n.tagName.toLowerCase()")
        except Exception:
            return ""

    def _hit(self, el: ElementHandle, strategy: str):
        self._strategy_count[strategy] += 1
        return el, strategy