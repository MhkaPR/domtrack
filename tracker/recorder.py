from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import Page, sync_playwright

from .config import TracerConfig
from .js_scripts import COLLECT_SCRIPT, build_init_script
from .storage import save_csv, save_json, timestamp


@dataclass
class CaptureResult:
    json_path: str
    csv_path: str
    mutation_count: int


class Recorder:
    def __init__(self, config: TracerConfig):
        self.config = config

    def capture(self) -> dict:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.config.headless)
            try:
                page = browser.new_page(viewport=self.config.viewport)
                page.goto(
                    self.config.url,
                    wait_until=self.config.wait_until,
                    timeout=self.config.goto_timeout_ms,
                )

                page.evaluate(
                    build_init_script(self.config),
                    self.config.target_selector,
                )

                self._wait_for_user(page)

                payload = page.evaluate(COLLECT_SCRIPT)
            finally:
                browser.close()
        return payload

    def _wait_for_user(self, page: Page) -> None:
        if self.config.duration_ms is not None:
            print(f"Recording for {self.config.duration_ms / 1000:.1f}s ...")
            page.wait_for_timeout(self.config.duration_ms)
            return
        if self.config.headless:
            raise ValueError(
                "interactive capture requires a non-headless browser "
                "(page.pause); pass duration_ms or disable headless"
            )
        print("Trace started. Interact with the page, then press Resume.")
        page.pause()

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

        count = payload.get("count", len(payload.get("mutations", [])))
        print(f"Captured {count} mutations.")
        print(f"  JSON: {json_path}")
        print(f"  CSV : {csv_path}")

        return CaptureResult(
            json_path=json_path,
            csv_path=csv_path,
            mutation_count=count,
        )