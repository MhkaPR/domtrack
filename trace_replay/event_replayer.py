from __future__ import annotations

from dataclasses import dataclass, field

from playwright.sync_api import Page

from .models import Event
from .timing import TimeContext, TimePolicy


_KEY_MAP: dict[str, str] = {
    " ": "Space", "Esc": "Escape", "Del": "Delete",
    "Left": "ArrowLeft", "Right": "ArrowRight",
    "Up": "ArrowUp", "Down": "ArrowDown",
}

_BUTTON_NAME = {0: "left", 1: "middle", 2: "right"}


@dataclass
class EventReplayStats:
    applied: int = 0
    failed: int = 0
    failures: list[str] = field(default_factory=list)


class EventReplayer:
    def __init__(self, events: list[Event], policy: TimePolicy):
        self.events = sorted(events, key=lambda e: e.t)
        self.policy = policy
        self.stats = EventReplayStats()

    # ── public ───────────────────────────────────────────────

    def replay(self, page: Page) -> EventReplayStats:
        prev_t = 0
        for ev in self.events:
            wait = self.policy.compute_wait(
                TimeContext(prev_t=prev_t, curr_t=ev.t, bucket_has_work=True)
            )
            if wait > 0:
                page.wait_for_timeout(wait)
            prev_t = ev.t

            try:
                self._dispatch(page, ev)
                self.stats.applied += 1
            except Exception as e:
                self.stats.failed += 1
                self.stats.failures.append(f"t={ev.t} {ev.kind}: {e}")
        return self.stats

    def _dispatch(self, page: Page, ev: Event) -> None:
        handler = getattr(self, f"_on_{ev.kind}", None)
        if handler is None:
            return
        handler(page, ev)

    # ── pointer ──────────────────────────────────────────────
    # Only mouse.down() / mouse.up() are dispatched. Chromium then fires
    # the full gesture (pointerdown, mousedown for the former;
    # pointerup, mouseup, click for the latter). Adding explicit
    # _on_click / _on_dblclick / _on_contextmenu handlers would fire
    # the gesture a second time. mousemove and pointermove are not
    # captured at all (see js_scripts.EVENT_INIT).

    def _on_mousedown(self, page: Page, ev: Event) -> None:
        if ev.client_x is not None:
            page.mouse.move(ev.client_x, ev.client_y)
        self._with_modifiers(page, ev, lambda: page.mouse.down(
            button=_BUTTON_NAME.get(ev.button or 0, "left")))

    def _on_mouseup(self, page: Page, ev: Event) -> None:
        self._with_modifiers(page, ev, lambda: page.mouse.up(
            button=_BUTTON_NAME.get(ev.button or 0, "left")))

    def _on_wheel(self, page: Page, ev: Event) -> None:
        page.mouse.wheel(ev.delta_x or 0, ev.delta_y or 0)

    # ── keyboard ─────────────────────────────────────────────

    def _on_keydown(self, page: Page, ev: Event) -> None:
        if ev.repeat:
            return
        page.keyboard.down(_KEY_MAP.get(ev.key or "", ev.key or ""))

    def _on_keyup(self, page: Page, ev: Event) -> None:
        page.keyboard.up(_KEY_MAP.get(ev.key or "", ev.key or ""))

    # ── input / form ─────────────────────────────────────────

    def _on_input(self, page: Page, ev: Event) -> None:
        # Safety net for IME / paste where no keydowns were recorded
        if ev.value is None or ev.target is None or not ev.target.selector:
            return
        try:
            loc = page.locator(ev.target.selector).first
            if loc.count() and loc.input_value() != ev.value:
                loc.fill(ev.value)
        except Exception:
            pass

    def _on_change(self, page: Page, ev: Event) -> None:
        # change is synthesized by the browser after blur/enter
        pass

    # ── scroll ───────────────────────────────────────────────

    def _on_scroll(self, page: Page, ev: Event) -> None:
        if ev.is_document_scroll:
            page.evaluate(
                "([x, y]) => window.scrollTo(x, y)",
                [ev.scroll_x or 0, ev.scroll_y or 0],
            )
            return
        sel = (ev.target.selector if ev.target else None) or ""
        if not sel:
            return
        page.evaluate(
            "([s, x, y]) => { const el = document.querySelector(s);"
            " if (el) el.scrollTo(x, y); }",
            [sel, ev.scroll_x or 0, ev.scroll_y or 0],
        )

    # ── helpers ──────────────────────────────────────────────

    @staticmethod
    def _with_modifiers(page: Page, ev: Event, fn) -> None:
        held: list[str] = []
        try:
            if ev.ctrl:  page.keyboard.down("Control"); held.append("Control")
            if ev.shift: page.keyboard.down("Shift");   held.append("Shift")
            if ev.alt:   page.keyboard.down("Alt");     held.append("Alt")
            if ev.meta:  page.keyboard.down("Meta");    held.append("Meta")
            fn()
        finally:
            for k in reversed(held):
                page.keyboard.up(k)