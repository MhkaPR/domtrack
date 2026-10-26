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
from .trace import RecordType
from .storage import save_csv, save_json, timestamp


@dataclass
class CaptureResult:
    json_path: str
    csv_path: str
    mutation_count: int
    event_count: int = 0


class CaptureSession:
    """Install the capture scripts on a live page and collect a trace payload.

    This is the piece shared by the human capture (:class:`Recorder`) and the
    replay-time recorder (:class:`domtrack.replay.recording.ReplayRecorder`):
    the same helpers, the same ``MutationObserver`` and event listeners, the
    same ``COLLECT_SCRIPT``. A trace captured while a replay drives the page is
    therefore the exact same shape as one captured by hand.

    Typical use separates installation (before the page is driven) from
    collection (afterwards)::

        session = CaptureSession(config)
        session.install(page)     # snapshot + observers start here
        ...                       # drive the page (human or replay)
        payload = session.build_payload(session.collect(page))
        session.save(payload)
    """

    def __init__(self, config: TracerConfig):
        self.config = config

    def install(self, page: Page) -> None:
        """Install helpers, the mutation observer, and event listeners."""
        # 1) HELPERS_INIT first — defines window.__cssPath for EVENT_INIT
        page.evaluate(HELPERS_INIT, list(self.config.dynamic_classes))

        # 2) Mutation observer + full snapshot
        page.evaluate(
            build_init_script(self.config),
            self.config.target_selector,
        )

        # 3) Event listeners (no mousemove)
        if self.config.capture_events:
            page.evaluate(EVENT_INIT, {
                "scrollThrottleMs": self.config.scroll_throttle_ms,
            })

    def collect(self, page: Page) -> dict:
        """Disconnect the observer and return the raw browser payload."""
        return page.evaluate(COLLECT_SCRIPT)

    def capture(self, page: Page) -> dict:
        """Install and immediately collect (single-shot capture)."""
        self.install(page)
        return self.collect(page)

    @staticmethod
    def build_payload(raw: dict) -> dict:
        """Wrap the browser payload in the rrweb-style trace layout."""
        snapshot = raw.get("snapshot")
        records = list(raw.get("records") or [])
        payload: dict = {
            "meta": raw.get("meta") or {},
            "version": 2,
            "snapshot": None,
            "records": records,
            "events": list(raw.get("events") or []),
        }
        if snapshot:
            payload["snapshot"] = {
                "type": RecordType.FULL_SNAPSHOT,
                "node": snapshot.get("node"),
                "initialOffset": snapshot.get("initialOffset"),
            }
        payload["count"] = len(records)
        return payload

    def save(
        self, payload: dict, out_dir: str | None = None, stamp: str | None = None
    ) -> tuple[str, str]:
        """Write ``payload`` as JSON + CSV and return both paths."""
        stamp = stamp or timestamp()
        out_dir = out_dir or self.config.output_dir
        json_path = save_json(payload, out_dir, stamp)
        csv_path = save_csv(payload.get("records", []), out_dir, stamp)
        return json_path, csv_path

    def save_result(
        self, payload: dict, out_dir: str | None = None, stamp: str | None = None
    ) -> CaptureResult:
        """Save ``payload`` and summarise it as a :class:`CaptureResult`."""
        json_path, csv_path = self.save(payload, out_dir, stamp)
        return CaptureResult(
            json_path=json_path,
            csv_path=csv_path,
            mutation_count=payload.get("count", len(payload.get("records") or [])),
            event_count=len(payload.get("events") or []),
        )


class Recorder:
    def __init__(self, config: TracerConfig):
        self.config = config
        self.session = CaptureSession(config)

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

                payload = self.session.collect(page)
            finally:
                browser.close()
        return payload

    def _install_capture(self, page: Page) -> None:
        self.session.install(page)

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

    @staticmethod
    def _build_payload(raw: dict) -> dict:
        """Wrap the browser payload in the rrweb-style trace layout."""
        return CaptureSession.build_payload(raw)

    def save(self, payload: dict, stamp: str | None = None) -> tuple[str, str]:
        return self.session.save(payload, stamp=stamp)

    def run(self) -> CaptureResult:
        raw = self.capture()
        payload = self._build_payload(raw)
        json_path, csv_path = self.save(payload)

        n_mu = payload["count"]
        n_ev = len(payload["events"])
        snap_node = (payload.get("snapshot") or {}).get("node") or {}
        n_snap = len(snap_node.get("childNodes") or [])
        print(f"Captured {n_mu} mutation records, {n_ev} events, snapshot root children={n_snap}.")
        print(f"  JSON: {json_path}")
        print(f"  CSV : {csv_path}")

        return CaptureResult(
            json_path=json_path,
            csv_path=csv_path,
            mutation_count=n_mu,
            event_count=n_ev,
        )
