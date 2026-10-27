# REST API

`domtrack` ships a REST API so the same capture / replay / diff / tables
pipeline the CLI exposes can be driven over HTTP. The API is a thin skin over
the existing modules — every endpoint maps one-to-one to a CLI command — so
results are identical to running the command locally.

The server needs the `api` extra:

```bash
pip install 'domtrack[api]'
playwright install chromium   # only for capture / replay / compare
```

## Running the server

The canonical way is the unified CLI:

```bash
python -m domtrack serve
```

That starts a Uvicorn server on `http://127.0.0.1:8000`. Options:

| Flag | Default | Meaning |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Bind host |
| `--port` | `8000` | Bind port |
| `--reload` | off | Auto-reload on code changes (dev) |
| `--workers` | `1` | Worker processes |
| `--trace-dir` | `traces` | Directory for traces written by the API |
| `--log-level` | `info` | Uvicorn log level |

```bash
python -m domtrack serve --host 0.0.0.0 --port 8080 --trace-dir /data/traces
python -m domtrack serve --reload          # development
```

Equivalent entry points, if you prefer them:

```bash
python -m domtrack.api --port 8000         # same server, module entry point
domtrack serve                             # installed console script
domtrack-serve --port 8000                 # installed console script
uvicorn domtrack.api:app --port 8000       # any ASGI server, using `app`
```

Interactive docs are served at `/docs` (Swagger UI) and `/redoc`, and the raw
OpenAPI schema at `/openapi.json`.

## Configuration

Settings are read from `DOMTRACK_API_*` environment variables, so the same
image can be reconfigured without code changes:

| Variable | Default | Meaning |
| --- | --- | --- |
| `DOMTRACK_API_TRACE_DIR` | `traces` | Directory for traces |
| `DOMTRACK_API_MAX_CAPTURE_SECONDS` | `3600` | Upper bound on a single capture |
| `DOMTRACK_API_HEADLESS` | `1` | Default headless mode for browser calls |
| `DOMTRACK_API_CORS_ORIGINS` | `*` | Comma-separated allowed CORS origins |

## Referencing traces

Every endpoint that consumes a trace accepts a `trace` object with **exactly
one** of two forms:

```jsonc
{ "path": "trace_20260101_120000.json" }   // server-side file, relative to the trace dir
{ "trace": { "meta": ..., "snapshot": ..., "records": ... } }  // inline
```

A relative `path` is resolved against the trace directory; an absolute path is
used as-is. Inline traces handed to `/replay` and `/compare` are written to the
trace directory first (those flows need a file on disk).

## Endpoints

### `GET /health`

Liveness probe. Returns the service name, version, and trace directory.

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok","name":"domtrack API","version":"1.0.0","trace_dir":"traces"}
```

### `GET /traces`

List the `.json` traces in the server trace directory.

```bash
curl http://127.0.0.1:8000/traces
```

### `POST /capture` — `domtrack capture <url>`

Opens a browser, records for `duration_s` seconds, and writes a trace. Unlike
the CLI, `duration_s` is **required**: an HTTP request cannot wait at the
interactive "press Resume" pause.

```bash
curl -X POST http://127.0.0.1:8000/capture \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com","duration_s":5,"headless":true}'
```

```json
{
  "url": "https://example.com",
  "json_path": "traces/trace_20260101_120000.json",
  "csv_path": "traces/trace_20260101_120000.csv",
  "mutation_count": 42,
  "event_count": 7
}
```

Fields mirror `domtrack capture`: `selector`, `viewport_w`, `viewport_h`,
`headless`, `timeout_ms`, `max_text`, `max_nodes`, `snapshot_max_depth`,
`snapshot_max_nodes`, `scroll_throttle_ms`, `capture_events`,
`dynamic_classes`, `output_dir`.

### `POST /replay` — `domtrack replay <trace.json>`

Replays a trace on a fresh page. Set `record: true` to also capture the
replayed page as a new trace.

```bash
curl -X POST http://127.0.0.1:8000/replay \
  -H 'Content-Type: application/json' \
  -d '{"trace":{"path":"trace_20260101_120000.json"},"speed":2,"headless":true}'
```

Fields mirror `domtrack replay`: `speed`, `mode`, `replay_mode`,
`structure_only`, `time_policy`, `gap_cap_ms`, `fast_load`, `headless`,
`wait_for_ids_ms`, `close_delay_ms`, `wait_before_replay_ms`, `skip_events`,
`third_party_hints`, plus the recording knobs `record`, `record_dir`,
`record_selector`, `record_events`, `record_snapshot_max_nodes`.

Response:

```json
{
  "url": "https://example.com",
  "mutation_count": 42,
  "event_count": 7,
  "stats": {"applied": 42, "skipped": 0, "failed": 0, "skip_reasons": {}, "failures": []},
  "recorded_json_path": null,
  "recorded_csv_path": null
}
```

### `POST /compare` — `domtrack compare <trace.json>`

Replay → record → diff in one call. Returns the replay summary plus the
content diff against the original.

```bash
curl -X POST http://127.0.0.1:8000/compare \
  -H 'Content-Type: application/json' \
  -d '{"trace":{"path":"before.json"},"speed":2,"headless":true}'
```

```json
{
  "before_path": "traces/before.json",
  "after_path": "traces/trace_20260101_130000.json",
  "summary": {"replay_mutations": 42, "recorded_mutations": 40, "changed": 2, "...": "..."},
  "diff": {"summary": {"...": "..."}, "added": [], "removed": [], "changed": []}
}
```

### `POST /html` — `domtrack html <trace.json>`

Rebuild the DOM as HTML at a record point (no browser).

```bash
curl -X POST http://127.0.0.1:8000/html \
  -H 'Content-Type: application/json' \
  -d '{"trace":{"path":"trace.json"},"at_ms":1500}'
```

`at_ms` and `at_index` are mutually exclusive; omit both for the final state.
Response is `{"html": "<html>...", "stats": {...}}`.

### `POST /diff` — `domtrack diff <A> <B>`

Content-level diff of two captures (no browser).

```bash
curl -X POST http://127.0.0.1:8000/diff \
  -H 'Content-Type: application/json' \
  -d '{"a":{"path":"before.json"},"b":{"path":"after.json"},"format":"json"}'
```

Fields: `no_records` (compare snapshots only), `at_ms_a`, `at_ms_b`, and
`format` (`json` | `text`). With `format: "text"` the response also carries the
human-readable report in `report`.

### `POST /tables/extract` — `domtrack tables extract <trace.json>`

Discover repeated tabular structures in a trace (no browser).

```bash
curl -X POST http://127.0.0.1:8000/tables/extract \
  -H 'Content-Type: application/json' \
  -d '{"trace":{"path":"trace.json"},"min_rows":3,"min_cols":2}'
```

Fields: `all` (keep overlapping tables), `min_rows`, `min_cols`. Returns
`{"total_candidates": N, "tables_returned": M, "tables": [...]}`.

### `POST /tables/label` — `domtrack tables label <tables.json>`

Attach semantic titles / column labels with an LLM. Pass either the extracted
`tables` payload or a `trace` (extraction runs first). The provider API key is
read from the server environment (`OPENAI_API_KEY` / `ANTHROPIC_API_KEY`)
unless `api_key` is supplied; `dry_run` returns without calling a model.

```bash
curl -X POST http://127.0.0.1:8000/tables/label \
  -H 'Content-Type: application/json' \
  -d '{"trace":{"path":"trace.json"},"provider":"openai","lang":"fa"}'
```

Fields: `provider` (`openai` | `compatible` | `anthropic`), `model`,
`base_url`, `api_key`, `lang`, `batch_size`, `dry_run`, plus the extraction
knobs `min_rows` / `min_cols` when a `trace` is given.

### `POST /tables/show` — `domtrack tables show <labeled.json>`

Render labeled tables as text. Send the labeled payload as `data`.

```bash
curl -X POST http://127.0.0.1:8000/tables/show \
  -H 'Content-Type: application/json' \
  -d '{"data":{...},"format":"markdown","max_rows":0}'
```

Fields: `only` (1-based table numbers), `format` (`plain` | `markdown` |
`csv` | `rich`), `max_rows`, `max_chars`. `rich` is downgraded to plain text
with ANSI codes stripped so the response is safe for any client. Response is
`{"format": "...", "content": "..."}`.

## Errors

Expected failures return a JSON body with a `detail` string:

| Status | When |
| --- | --- |
| `400` | Bad request (e.g. missing API key, empty tables, not an rrweb trace) |
| `404` | Referenced trace file not found |
| `422` | Request body failed validation (Pydantic) |
| `502` | Browser/LLM operation failed (capture, replay, compare, labeling) |

```json
{"detail": "trace file not found: nope.json"}
```

## Using the app directly

`domtrack.api` exposes a ready-to-serve ASGI application and a factory:

```python
from domtrack.api import app, create_app
from domtrack.api.config import ApiSettings

# Point the app at a custom trace dir (e.g. in tests):
application = create_app(ApiSettings(trace_dir="/data/traces", cors_origins=("*",)))
```

Serve either with any ASGI server, e.g. `uvicorn domtrack.api:app`.

## Python client example

```python
import httpx

base = "http://127.0.0.1:8000"
with httpx.Client(base_url=base, timeout=120) as c:
    cap = c.post("/capture", json={
        "url": "https://example.com", "duration_s": 5, "headless": True,
    }).json()

    tables = c.post("/tables/extract", json={
        "trace": {"path": cap["json_path"]},
    }).json()

    md = c.post("/tables/show", json={
        "data": tables, "format": "markdown",
    }).json()["content"]
    print(md)
```
