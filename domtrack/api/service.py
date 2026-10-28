"""Business logic behind the REST API.

This module is the seam between HTTP and domtrack's CLI code: it takes plain
Python arguments (already validated by Pydantic), calls the same classes and
functions the command line uses, and returns JSON-serialisable results. The
routers stay thin and this layer stays testable without a server.

Browser-backed operations (capture / replay / compare) use Playwright's
*synchronous* API, so routers call them through ``run_in_threadpool`` to keep
the event loop responsive.
"""

from __future__ import annotations

import os
from dataclasses import asdict
from typing import Any, Optional

from .. import trace as tf
from ..config import DEFAULT_DYNAMIC_CLASSES, TracerConfig
from ..diff.core import diff_traces, format_report
from ..recorder import Recorder
from ..replay.compare import ReplayComparer
from ..replay.models import Trace
from ..replay.recording import RecordingConfig
from ..replay.replayer import Replayer, ReplayConfig
from ..replay.third_party import ThirdPartyDetector
from ..tables.discovery import DiscoveryConfig, discover_tables
from ..tables.labeling import API_KEY_ENV, LabelConfig, label_tables, resolve_api_key
from ..tables.render import (
    RenderOptions,
    render_csv,
    render_markdown,
    render_plain,
    render_rich,
)
from .config import ApiSettings, get_settings
from .schemas import (
    CaptureRequest,
    CompareRequest,
    DiffRequest,
    HtmlRequest,
    ReplayRequest,
    TablesExtractRequest,
    TablesLabelRequest,
    TablesShowRequest,
    TraceSource,
)


class ApiError(Exception):
    """Raised for expected, client-facing failures (mapped to 4xx by routers)."""

    def __init__(self, detail: str, status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


# ── trace resolution ───────────────────────────────────────────────────────

def resolve_trace(source: TraceSource, settings: Optional[ApiSettings] = None) -> dict:
    """Turn a :class:`TraceSource` into a loaded rrweb trace dict."""
    settings = settings or get_settings()
    if source.trace is not None:
        raw = source.trace
    else:
        path = _resolve_path(source.path, settings)
        try:
            raw = tf.load_raw(path)
        except FileNotFoundError:
            raise ApiError(f"trace file not found: {source.path}", status_code=404)
        except Exception as e:  # pragma: no cover - defensive
            raise ApiError(f"could not load trace: {e}")

    if not tf.is_rrweb_trace(raw):
        raise ApiError("not an rrweb-style trace (need 'snapshot' + 'records')")
    if not raw.get("snapshot"):
        raise ApiError("trace snapshot not found")
    return raw


def _resolve_path(path: Optional[str], settings: ApiSettings) -> str:
    if not path:
        raise ApiError("trace path is required")
    if os.path.isabs(path):
        return path
    return os.path.join(settings.trace_dir, path)


def _materialise_trace(source: TraceSource, settings: ApiSettings) -> tuple[str, dict]:
    """Return ``(path, raw)`` for a trace, writing inline traces to disk.

    Replay / compare need a trace *file* because the underlying classes take a
    path. Inline traces are therefore persisted into the trace directory first.
    """
    if source.trace is not None:
        os.makedirs(settings.trace_dir, exist_ok=True)
        from ..storage import save_json, timestamp

        path = save_json(source.trace, settings.trace_dir, stamp=timestamp())
        return path, source.trace
    path = _resolve_path(source.path, settings)
    if not os.path.exists(path):
        raise ApiError(f"trace file not found: {source.path}", status_code=404)
    raw = tf.load_raw(path)
    if not tf.is_rrweb_trace(raw) or not raw.get("snapshot"):
        raise ApiError("not an rrweb-style trace (need 'snapshot' + 'records')")
    return path, raw


# ── traces listing ─────────────────────────────────────────────────────────

def list_traces(settings: Optional[ApiSettings] = None) -> dict:
    settings = settings or get_settings()
    trace_dir = settings.trace_dir
    out: list[dict[str, Any]] = []
    if os.path.isdir(trace_dir):
        for name in sorted(os.listdir(trace_dir)):
            if not name.endswith(".json"):
                continue
            full = os.path.join(trace_dir, name)
            try:
                stat = os.stat(full)
            except OSError:
                continue
            out.append(
                {
                    "name": name,
                    "path": os.path.relpath(full, trace_dir),
                    "size_bytes": stat.st_size,
                    "modified": stat.st_mtime,
                }
            )
    return {"trace_dir": trace_dir, "count": len(out), "traces": out}


# ── capture ────────────────────────────────────────────────────────────────

def run_capture(req: CaptureRequest, settings: Optional[ApiSettings] = None) -> dict:
    settings = settings or get_settings()
    if req.duration_s is not None and req.duration_s > settings.max_capture_seconds:
        raise ApiError(
            f"duration_s exceeds the {settings.max_capture_seconds}s limit"
        )

    headless = settings.headless_default if req.headless is None else req.headless
    if req.duration_s is None and (not req.interactive or headless):
        raise ApiError(
            "duration_s is required unless interactive=true uses a headed browser",
            status_code=422,
        )
    dynamic_classes = (
        tuple(req.dynamic_classes) if req.dynamic_classes else DEFAULT_DYNAMIC_CLASSES
    )
    cfg = TracerConfig(
        url=req.url,
        target_selector=req.selector,
        viewport={"width": req.viewport_w, "height": req.viewport_h},
        headless=headless,
        goto_timeout_ms=req.timeout_ms,
        max_text=req.max_text,
        max_nodes=req.max_nodes,
        snapshot_max_depth=req.snapshot_max_depth,
        snapshot_max_nodes=req.snapshot_max_nodes,
        dynamic_classes=dynamic_classes,
        capture_events=req.capture_events,
        scroll_throttle_ms=req.scroll_throttle_ms,
        duration_ms=None if req.duration_s is None else int(req.duration_s * 1000),
        output_dir=req.output_dir or settings.trace_dir,
    )
    try:
        result = Recorder(cfg).run()
    except Exception as e:
        raise ApiError(f"capture failed: {e}", status_code=502)
    return {
        "url": req.url,
        "json_path": result.json_path,
        "csv_path": result.csv_path,
        "mutation_count": result.mutation_count,
        "event_count": result.event_count,
    }


# ── replay ─────────────────────────────────────────────────────────────────

def _replay_config(req: ReplayRequest, settings: ApiSettings) -> ReplayConfig:
    headless = settings.headless_default if req.headless is None else req.headless
    recording = RecordingConfig(
        enabled=req.record,
        output_dir=req.record_dir or settings.trace_dir,
        target_selector=req.record_selector,
        capture_events=req.record_events,
        snapshot_max_nodes=req.record_snapshot_max_nodes,
    )
    return ReplayConfig(
        speed=req.speed,
        mode=req.mode,
        replay_mode=req.replay_mode,
        structure_only=req.structure_only,
        time_policy=req.time_policy,
        gap_cap_ms=req.gap_cap_ms,
        fast_load=req.fast_load,
        headless=headless,
        wait_for_ids_ms=req.wait_for_ids_ms,
        close_delay_ms=req.close_delay_ms,
        wait_before_replay_ms=req.wait_before_replay_ms,
        skip_events=tuple(req.skip_events),
        recording=recording,
    )


def _stats_to_dict(stats) -> dict:
    return {
        "applied": stats.applied,
        "skipped": stats.skipped,
        "failed": stats.failed,
        "skip_reasons": dict(stats.skip_reasons),
        "failures": [asdict(f) for f in stats.failures],
    }


def run_replay(req: ReplayRequest, settings: Optional[ApiSettings] = None) -> dict:
    settings = settings or get_settings()
    path, raw = _materialise_trace(req.trace, settings)

    if not (raw.get("meta") or {}).get("url"):
        raise ApiError("URL not found in trace")

    detector = ThirdPartyDetector(hints=req.third_party_hints or None)
    try:
        replayer = Replayer.from_file(
            path, config=_replay_config(req, settings), third_party_detector=detector
        )
        stats = replayer.run()
    except Exception as e:
        raise ApiError(f"replay failed: {e}", status_code=502)

    record_result = replayer.record_result
    return {
        "url": replayer.trace.meta.url,
        "mutation_count": len(replayer.trace.mutations),
        "event_count": len(replayer.trace.events),
        "stats": _stats_to_dict(stats),
        "recorded_json_path": record_result.json_path if record_result else None,
        "recorded_csv_path": record_result.csv_path if record_result else None,
    }


# ── compare ────────────────────────────────────────────────────────────────

def run_compare(req: CompareRequest, settings: Optional[ApiSettings] = None) -> dict:
    settings = settings or get_settings()
    path, _ = _materialise_trace(req.trace, settings)

    headless = settings.headless_default if req.headless is None else req.headless
    replay_config = ReplayConfig(
        speed=req.speed,
        mode=req.mode,
        replay_mode=req.replay_mode,
        time_policy=req.time_policy,
        gap_cap_ms=req.gap_cap_ms,
        fast_load=req.fast_load,
        headless=headless,
        wait_before_replay_ms=req.wait_before_replay_ms,
        close_delay_ms=req.close_delay_ms,
        skip_events=tuple(req.skip_events),
    )
    recording = RecordingConfig(
        enabled=True,
        output_dir=req.record_dir or settings.trace_dir,
        target_selector=req.record_selector,
        capture_events=req.record_events,
    )
    detector = ThirdPartyDetector(hints=req.third_party_hints or None)
    try:
        result = ReplayComparer(
            path, replay_config=replay_config, recording=recording,
            third_party_detector=detector,
        ).run()
    except Exception as e:
        raise ApiError(f"compare failed: {e}", status_code=502)

    return {
        "before_path": result.before_path,
        "after_path": result.after_path,
        "summary": result.summary,
        "diff": result.diff.to_dict(),
    }


# ── html ───────────────────────────────────────────────────────────────────

def run_html(req: HtmlRequest, settings: Optional[ApiSettings] = None) -> dict:
    settings = settings or get_settings()
    raw = resolve_trace(req.trace, settings)
    state = tf.DomState.from_trace(raw)
    records = raw.get("records") or []
    if req.at_index is not None:
        for record in records[: req.at_index + 1]:
            state.apply(record)
    else:
        state.apply_up_to(records, req.at_ms)
    return {"html": state.to_html(), "stats": dict(state.stats)}


# ── diff ───────────────────────────────────────────────────────────────────

def run_diff(req: DiffRequest, settings: Optional[ApiSettings] = None) -> dict:
    settings = settings or get_settings()
    raw_a = resolve_trace(req.a, settings)
    raw_b = resolve_trace(req.b, settings)
    result = diff_traces(
        raw_a,
        raw_b,
        at_ms_a=req.at_ms_a,
        at_ms_b=req.at_ms_b,
        apply_records=not req.no_records,
    )
    payload = result.to_dict()
    if req.format == "text":
        import io

        buf = io.StringIO()
        format_report(result, buf)
        payload["report"] = buf.getvalue()
    return payload


# ── tables ─────────────────────────────────────────────────────────────────

def run_tables_extract(
    req: TablesExtractRequest, settings: Optional[ApiSettings] = None
) -> dict:
    settings = settings or get_settings()
    raw = resolve_trace(req.trace, settings)
    config = DiscoveryConfig(min_rows=req.min_rows, min_cols=req.min_cols, dedupe=not req.all)
    try:
        return discover_tables(raw, config)
    except ValueError as e:
        raise ApiError(str(e))


def run_tables_label(
    req: TablesLabelRequest, settings: Optional[ApiSettings] = None
) -> dict:
    settings = settings or get_settings()
    if req.tables is not None:
        data = req.tables
    else:
        raw = resolve_trace(req.trace, settings)  # type: ignore[arg-type]
        config = DiscoveryConfig(min_rows=req.min_rows, min_cols=req.min_cols)
        try:
            data = discover_tables(raw, config)
        except ValueError as e:
            raise ApiError(str(e))

    if not data.get("tables"):
        raise ApiError("no tables found in input")

    config = LabelConfig(
        provider=req.provider,
        model=req.model,
        base_url=req.base_url,
        api_key=req.api_key,
        lang=req.lang,
        batch_size=req.batch_size,
        dry_run=req.dry_run,
    )
    if not config.api_key:
        config.api_key = resolve_api_key(config)
    if not config.api_key and not config.dry_run:
        env = API_KEY_ENV.get(config.provider, "the provider API key")
        raise ApiError(f"missing API key: set {env} on the server or pass api_key")

    try:
        merged = label_tables(data, config)
    except Exception as e:
        raise ApiError(f"labeling failed: {e}", status_code=502)

    tables = merged.get("tables") or []
    return {"tables_returned": len(tables), "tables": tables, "dry_run": req.dry_run}


def run_tables_show(
    req: TablesShowRequest, settings: Optional[ApiSettings] = None
) -> dict:
    if not req.data.get("tables"):
        raise ApiError("no tables found in input")
    options = RenderOptions(
        only=set(req.only) if req.only else None,
        max_rows=req.max_rows,
        max_chars=req.max_chars,
    )
    import contextlib
    import io

    buf = io.StringIO()
    fmt = req.format
    # Call the renderers with an explicit stream: markdown/csv accept `out`,
    # and plain/rich write to stdout, which we capture. (Going through
    # `render()` would bind its `sys.stdout` default at import time.)
    try:
        if fmt == "markdown":
            render_markdown(req.data, options, out=buf)
        elif fmt == "csv":
            render_csv(req.data, options, out=buf)
        else:
            with contextlib.redirect_stdout(buf):
                if fmt == "rich":
                    try:
                        render_rich(req.data, options)
                    except ImportError:
                        render_plain(req.data, options)
                else:
                    render_plain(req.data, options)
    except ValueError as e:
        raise ApiError(str(e))
    content = buf.getvalue()
    if fmt == "rich":
        # Rich emits ANSI escapes; keep plain text for API consumers.
        content = _strip_ansi(content)
    return {"format": fmt, "content": content}


def _strip_ansi(text: str) -> str:
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", text)
