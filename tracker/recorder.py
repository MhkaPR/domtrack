from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import Page, sync_playwright

from .config import TracerConfig
from .js_scripts import (
    COLLECT_SCRIPT,
    EVENT_INIT,
    HELPERS_INIT,
    build_init_script,
)
from .storage import save_csv, save_json, timestamp


@dataclass
class CaptureResult:
    json_path: str
    csv_path: str
    mutation_count: int
    event_count: int = 0


class Recorder:
    def __init__(self, config: TracerConfig):
        self.config = config

    def capture(self) -> dict:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.config.headless)
            try:
                page = browser.new_page(viewport=self.config.viewport)

                # Surface JS errors that would otherwise silently drop events
                page.on("pageerror", lambda err: print(f"[pageerror] {err}"))
                page.on("console",
                        lambda msg: print(f"[console:{msg.type}] {msg.text}")
                        if msg.type in ("error", "warning") else None)

                page.goto(
                    self.config.url,
                    wait_until=self.config.wait_until,
                    timeout=self.config.goto_timeout_ms,
                )

                self._install_capture(page)

                self._wait_for_user(page)

                payload = page.evaluate(COLLECT_SCRIPT)
            finally:
                browser.close()
        return payload

    def _install_capture(self, page: Page) -> None:
        # 1) HELPERS_INIT first — defines window.__cssPath for EVENT_INIT
        page.evaluate(HELPERS_INIT, list(self.config.dynamic_classes))

        # 2) Mutation observer
        page.evaluate(
            build_init_script(self.config),
            self.config.target_selector,
        )

        # 3) Event listeners (no mousemove)
        if self.config.capture_events:
            page.evaluate(EVENT_INIT, {
                "scrollThrottleMs": self.config.scroll_throttle_ms,
            })

    def _wait_for_user(self, page: Page) -> None:
        if self.config.duration_ms is not None:
            print(f"Recording for {self.config.duration_ms / 1000:.1f}s ...")
            page.wait_for_timeout(self.config.duration_ms)
        elif self.config.headless:
            raise ValueError(
                "interactive capture requires a non-headless browser "
                "(page.pause); pass duration_ms or disable headless"
            )
        else:
            print("Trace started. Interact with the page, then press Resume.")
            page.pause()

        # Sanity check — proves the listeners are alive before collection
        n_ev = page.evaluate("() => (window.__events || []).length")
        n_mu = page.evaluate("() => (window.capturedMutations || []).length")
        print(f"  pre-collect: {n_ev} events, {n_mu} mutations recorded")

    def save(self, payload: dict, stamp: str | None = None) -> tuple[str, str]:
        stamp = stamp or timestamp()
        json_path = save_json(payload, self.config.output_dir, stamp)
        csv_path = save_csv(
            payload.get("mutations", []),
            self.config.output_dir,
            stamp,
        )
        return json_path, csv_path

    def run(self) -> CaptureResult:
        payload = self.capture()
        json_path, csv_path = self.save(payload)

        n_mu = payload.get("count", len(payload.get("mutations", [])))
        n_ev = len(payload.get("events", []))
        print(f"Captured {n_mu} mutations, {n_ev} events.")
        print(f"  JSON: {json_path}")
        print(f"  CSV : {csv_path}")

        return CaptureResult(
            json_path=json_path,
            csv_path=csv_path,
            mutation_count=n_mu,
            event_count=n_ev,
        )