from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import ElementHandle, Page

from .locators import ElementLocator
from .models import Mutation, NodeInfo


@dataclass
class ApplyResult:
    ok: bool
    strategy: str
    error: Optional[str] = None

    @classmethod
    def success(cls, strategy: str) -> "ApplyResult":
        return cls(True, strategy)

    @classmethod
    def failure(cls, strategy: str, error: Optional[str] = None) -> "ApplyResult":
        return cls(False, strategy, error)


class BaseApplier(ABC):
    @abstractmethod
    def apply(self, page: Page, mutation: Mutation) -> ApplyResult:
        ...


class AttributesApplier(BaseApplier):
    def __init__(self, locator: ElementLocator):
        self.locator = locator

    def apply(self, page: Page, mutation: Mutation) -> ApplyResult:
        el, how = self.locator.find(mutation)
        if el is None:
            return ApplyResult.failure("not found")

        attr = mutation.attribute_name
        new_val = mutation.new_value
        try:
            if attr == "class":
                self._apply_class_delta(el, mutation)
            elif new_val is None:
                el.evaluate("(n, a) => n.removeAttribute(a)", attr)
            else:
                el.evaluate("(n, p) => n.setAttribute(p[0], p[1])", [attr, new_val])
        except Exception as e:
            return ApplyResult.failure(how or "apply-error", str(e)[:80])
        return ApplyResult.success(how or "apply-ok")

    @staticmethod
    def _apply_class_delta(el: ElementHandle, mutation: Mutation) -> None:
        old_set = set((mutation.old_value or "").split())
        new_set = set((mutation.new_value or "").split())
        add = list(new_set - old_set)
        remove = list(old_set - new_set)
        if not add and not remove:
            return
        el.evaluate(
            "(n, p) => { p[0].forEach(c => n.classList.add(c));"
            " p[1].forEach(c => n.classList.remove(c)); }",
            [add, remove],
        )


class ChildListApplier(BaseApplier):
    def apply(self, page: Page, mutation: Mutation) -> ApplyResult:
        errors: list[str] = []
        for node in mutation.removed:
            self._remove_node(page, node, errors)
        for node in mutation.added:
            self._add_node(page, mutation, node, errors)

        msg = f"rem={len(mutation.removed)} add={len(mutation.added)}"
        if errors:
            return ApplyResult.failure(msg, errors[0])
        return ApplyResult.success(msg)

    @staticmethod
    def _remove_node(page: Page, node: NodeInfo, errors: list[str]) -> None:
        query = ("#" + node.id) if node.id else node.selector
        if not query:
            return
        try:
            page.evaluate(
                "(s) => { const e = document.querySelector(s); if (e) e.remove(); }",
                query,
            )
        except Exception as e:
            errors.append(str(e)[:80])

    @staticmethod
    def _add_node(
        page: Page, mutation: Mutation, node: NodeInfo, errors: list[str]
    ) -> None:
        if node.id:
            try:
                if page.query_selector("#" + node.id):
                    return  # already present
            except Exception:
                pass
        if not node.html:
            return
        target_sel = mutation.target.selector
        if not target_sel:
            return
        try:
            page.evaluate(
                """
                ([pSel, html]) => {
                    const parent = document.querySelector(pSel);
                    if (!parent) return;
                    const tmp = document.createElement('template');
                    tmp.innerHTML = html.trim();
                    if (tmp.content.firstChild) parent.appendChild(tmp.content.firstChild);
                }
                """,
                [target_sel, node.html],
            )
        except Exception as e:
            errors.append(str(e)[:80])


class CharacterDataApplier(BaseApplier):
    def __init__(self, locator: ElementLocator):
        self.locator = locator

    def apply(self, page: Page, mutation: Mutation) -> ApplyResult:
        el, how = self.locator.find(mutation)
        if el is None:
            return ApplyResult.failure("not found")
        try:
            el.evaluate(
                "(n, v) => { const c = n.firstChild;"
                " if (c && c.nodeType === 3) c.nodeValue = v; }",
                mutation.new_value or "",
            )
        except Exception as e:
            return ApplyResult.failure(how or "apply-error", str(e)[:80])
        return ApplyResult.success(how or "apply-ok")


class ApplierRegistry:
    """Dispatch a mutation to the right applier by type."""

    def __init__(self, page: Page, locator: ElementLocator):
        self._appliers: dict[str, BaseApplier] = {
            "attributes": AttributesApplier(locator),
            "childList": ChildListApplier(),
            "characterData": CharacterDataApplier(locator),
        }

    def apply(self, page: Page, mutation: Mutation) -> ApplyResult:
        applier = self._appliers.get(mutation.type)
        if applier is None:
            return ApplyResult.failure("unknown", f"unknown type: {mutation.type}")
        return applier.apply(page, mutation)