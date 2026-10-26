"""Record the live page while a replay drives it.

A replay is automation: it drives a fresh page with the captured
interactions. To compare what the *automated* run produced against what the
*human* run captured, we install the very same capture scripts a human capture
uses (via :class:`domtrack.recorder.CaptureSession`) *before* the replay starts
and collect *after* it ends. The result is an ordinary domtrack trace file,
byte-compatible with a hand capture — so it can be diffed with
:mod:`domtrack.diff`.

    recorder = ReplayRecorder(RecordingConfig(enabled=True))
    recorder.start(page, url=url, viewport=viewport)   # snapshot + observers
    ...                                                # replay drives the page
    result = recorder.finish_and_save(page)            # trace_*.json / .csv
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import Page

from ..config import TracerConfig
from ..recorder import CaptureResult, CaptureSession


@dataclass
class RecordingConfig:
    """Knobs for recording during a replay.

    ``url`` / ``viewport`` are normally taken from the replayed trace; pass
    ``tracer`` to reuse a fully-populated :class:`~domtrack.config.TracerConfig`
    instead (its viewport/url/limits win over the trace's).
    """

    enabled: bool = False
    output_dir: str = "traces"
    target_selector: Optional[str] = None
    capture_events: bool = False
    max_text: int = 500
    max_nodes: int = 50
    snapshot_max_depth: int = 20
    snapshot_max_nodes: int = 20000
    tracer: Optional[TracerConfig] = None

    def to_tracer_config(
        self, url: str, viewport: Optional[dict] = None
    ) -> TracerConfig:
        """Build the capture config used while recording."""
        if self.tracer is not None:
            return self.tracer
        return TracerConfig(
            url=url,
            target_selector=self.target_selector,
            viewport=viewport or {"width": 1440, "height": 900},
            headless=True,
            capture_events=self.capture_events,
            max_text=self.max_text,
            max_nodes=self.max_nodes,
            snapshot_max_depth=self.snapshot_max_depth,
            snapshot_max_nodes=self.snapshot_max_nodes,
            output_dir=self.output_dir,
            duration_ms=0,
        )


class ReplayRecorder:
    """Install capture scripts on a replay page, then collect + save a trace."""

    def __init__(self, config: Optional[RecordingConfig] = None):
        self.config = config or RecordingConfig()
        self.session: Optional[CaptureSession] = None
        self.result: Optional[CaptureResult] = None

    def start(
        self, page: Page, url: str, viewport: Optional[dict] = None
    ) -> None:
        """Install capture scripts; call before the replay drives the page."""
        tracer = self.config.to_tracer_config(url, viewport)
        self.session = CaptureSession(tracer)
        self.session.install(page)

    def finish(self, page: Page) -> dict:
        """Collect the recorded payload; call after the replay finishes."""
        session = self._require_session()
        return session.build_payload(session.collect(page))

    def save(self, payload: dict, stamp: Optional[str] = None) -> CaptureResult:
        """Write the recorded payload to disk and return its paths/counts."""
        session = self._require_session()
        self.result = session.save_result(payload, stamp=stamp)
        return self.result

    def finish_and_save(
        self, page: Page, stamp: Optional[str] = None
    ) -> CaptureResult:
        """``finish`` + ``save`` in one call."""
        return self.save(self.finish(page), stamp=stamp)

    def _require_session(self) -> CaptureSession:
        if self.session is None:
            raise RuntimeError("ReplayRecorder.start() must be called first")
        return self.session
