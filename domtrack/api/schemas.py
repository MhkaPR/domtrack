"""Pydantic request/response models for the domtrack REST API.

Field names and defaults track the CLI flags one-to-one so the mapping
between ``domtrack <command> --flag`` and a JSON body is obvious. Where the
CLI uses milliseconds we keep milliseconds; where it uses seconds
(``--duration``) we use ``duration_s`` to make the unit explicit.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


# ── shared ─────────────────────────────────────────────────────────────────

class TraceSource(BaseModel):
    """A trace supplied either by server-side path or inline as JSON.

    Exactly one of ``path`` / ``trace`` must be given. ``path`` is resolved
    against the API trace directory (or taken as-is when absolute), letting
    clients reference traces they previously captured on the server. ``trace``
    carries an rrweb-style trace object directly in the request body.
    """

    path: Optional[str] = Field(
        default=None, description="Server-side trace path (relative to the trace dir)."
    )
    trace: Optional[dict[str, Any]] = Field(
        default=None, description="Inline rrweb-style trace object."
    )

    @model_validator(mode="after")
    def _exactly_one(self) -> "TraceSource":
        if (self.path is None) == (self.trace is None):
            raise ValueError("provide exactly one of 'path' or 'trace'")
        return self


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    name: str
    version: str
    trace_dir: str


class TraceInfo(BaseModel):
    name: str
    path: str
    size_bytes: int
    modified: float


class TraceListResponse(BaseModel):
    trace_dir: str
    count: int
    traces: list[TraceInfo]


# ── capture ────────────────────────────────────────────────────────────────

class CaptureRequest(BaseModel):
    """Mirror of ``domtrack capture``.

    Set ``interactive=true`` with a headed browser to record until the operator
    presses Resume in the Playwright Inspector (the CLI's ``page.pause()``
    behaviour). Otherwise ``duration_s`` is required.
    """

    url: str
    selector: Optional[str] = None
    output_dir: Optional[str] = None
    viewport_w: int = 1440
    viewport_h: int = 900
    headless: Optional[bool] = None
    duration_s: Optional[float] = Field(
        default=None,
        gt=0,
        description="Seconds to record before stopping (required unless interactive).",
    )
    interactive: bool = Field(
        default=False,
        description="Wait for the operator to press Resume in the Inspector (headed only).",
    )
    timeout_ms: int = 360_000
    max_text: int = 500
    max_nodes: int = 50
    snapshot_max_depth: int = 20
    snapshot_max_nodes: int = 20_000
    scroll_throttle_ms: int = 100
    capture_events: bool = True
    dynamic_classes: Optional[list[str]] = None


class CaptureResponse(BaseModel):
    url: str
    json_path: str
    csv_path: str
    mutation_count: int
    event_count: int


# ── replay ─────────────────────────────────────────────────────────────────

class ReplayRequest(BaseModel):
    """Mirror of ``domtrack replay`` (plus optional recording)."""

    trace: TraceSource
    speed: float = Field(default=1.0, gt=0)
    mode: Literal["all", "clean"] = "all"
    replay_mode: Literal["events", "mutations", "both"] = "events"
    structure_only: bool = False
    time_policy: Literal["faithful", "instant", "capped", "skip-aware"] = "skip-aware"
    gap_cap_ms: Optional[int] = None
    fast_load: bool = False
    headless: Optional[bool] = None
    wait_for_ids_ms: int = 2000
    close_delay_ms: int = 5000
    wait_before_replay_ms: int = 0
    skip_events: list[str] = Field(default_factory=list)
    third_party_hints: list[str] = Field(default_factory=list)

    # Recording (``domtrack replay --record ...``).
    record: bool = False
    record_dir: Optional[str] = None
    record_selector: str = "body"
    record_events: bool = False
    record_snapshot_max_nodes: int = 20_000


class ReplayStatsModel(BaseModel):
    applied: int
    skipped: int
    failed: int
    skip_reasons: dict[str, int]
    failures: list[dict[str, Any]]


class ReplayResponse(BaseModel):
    url: str
    mutation_count: int
    event_count: int
    stats: ReplayStatsModel
    recorded_json_path: Optional[str] = None
    recorded_csv_path: Optional[str] = None


# ── compare ────────────────────────────────────────────────────────────────

class CompareRequest(BaseModel):
    """Mirror of ``domtrack compare`` (replay → record → diff)."""

    trace: TraceSource
    speed: float = Field(default=1.0, gt=0)
    mode: Literal["all", "clean"] = "all"
    replay_mode: Literal["events", "mutations", "both"] = "events"
    time_policy: Literal["faithful", "instant", "capped", "skip-aware"] = "skip-aware"
    gap_cap_ms: Optional[int] = None
    fast_load: bool = False
    headless: Optional[bool] = None
    wait_before_replay_ms: int = 0
    close_delay_ms: int = 0
    skip_events: list[str] = Field(default_factory=list)
    third_party_hints: list[str] = Field(default_factory=list)
    record_dir: Optional[str] = None
    record_selector: str = "body"
    record_events: bool = False


class CompareResponse(BaseModel):
    before_path: str
    after_path: str
    summary: dict[str, Any]
    diff: dict[str, Any]


# ── browser-free: html / diff / tables ─────────────────────────────────────

class HtmlRequest(BaseModel):
    trace: TraceSource
    at_ms: Optional[int] = None
    at_index: Optional[int] = None

    @model_validator(mode="after")
    def _not_both(self) -> "HtmlRequest":
        if self.at_ms is not None and self.at_index is not None:
            raise ValueError("at_ms and at_index are mutually exclusive")
        return self


class HtmlResponse(BaseModel):
    html: str
    stats: dict[str, Any]


class DiffRequest(BaseModel):
    a: TraceSource
    b: TraceSource
    no_records: bool = False
    at_ms_a: Optional[int] = None
    at_ms_b: Optional[int] = None
    format: Literal["json", "text"] = "json"


class DiffResponse(BaseModel):
    summary: dict[str, int]
    added: list[dict[str, Any]] = Field(default_factory=list)
    removed: list[dict[str, Any]] = Field(default_factory=list)
    changed: list[dict[str, Any]] = Field(default_factory=list)
    report: Optional[str] = Field(
        default=None, description="Human-readable report (format=text only)."
    )


class TablesExtractRequest(BaseModel):
    trace: TraceSource
    all: bool = False
    min_rows: int = 3
    min_cols: int = 2


class TablesExtractResponse(BaseModel):
    total_candidates: int
    tables_returned: int
    tables: list[dict[str, Any]]


class TablesLabelRequest(BaseModel):
    """Mirror of ``domtrack tables label``.

    Supply the extracted ``tables`` payload directly, or a ``trace`` to run
    extraction first. ``api_key`` is normally omitted and read from the
    provider's environment variable on the server.
    """

    tables: Optional[dict[str, Any]] = None
    trace: Optional[TraceSource] = None
    min_rows: int = 3
    min_cols: int = 2
    provider: Literal["openai", "compatible", "anthropic"] = "openai"
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    lang: str = "fa"
    batch_size: int = 25
    dry_run: bool = False

    @model_validator(mode="after")
    def _exactly_one(self) -> "TablesLabelRequest":
        if (self.tables is None) == (self.trace is None):
            raise ValueError("provide exactly one of 'tables' or 'trace'")
        return self


class TablesLabelResponse(BaseModel):
    tables_returned: int
    tables: list[dict[str, Any]]
    dry_run: bool = False


class TablesShowRequest(BaseModel):
    data: dict[str, Any]
    only: Optional[list[int]] = None
    format: Literal["rich", "plain", "markdown", "csv"] = "plain"
    max_rows: int = 0
    max_chars: int = 80


class TablesShowResponse(BaseModel):
    format: str
    content: str


class ErrorResponse(BaseModel):
    detail: str
