# domtrack

Record DOM mutations and user events from a live page, then replay them with
Playwright.

`domtrack` captures what a page actually *does* — every DOM change and every
user interaction — into a compact, `rrweb`-style trace, and can feed that trace
back into a fresh browser to reproduce the session.

> **For LLM agents / AI tooling:** this repository is documented specifically
> so it can be read and used by language models. Start with
> [`docs/README.md`](docs/README.md), which is written as a single-pass,
> LLM-oriented index of every module, its public API, and usage examples. The
> machine-readable trace contract lives in
> [`docs/trace-format.md`](docs/trace-format.md). Read those two files before
> generating code against this package.

## Why

Modern pages mutate constantly (sliders, lazy widgets, chat bubbles, price
updates). Screenshots and HTML dumps capture a single frame; `domtrack` captures
the *timeline*:

* a **full snapshot** of the DOM, where every node gets a stable numeric id, and
* a stream of **incremental records** that only describe what changed, referring
  to nodes by id instead of re-embedding their text/HTML.

The result is small, diff-friendly, and directly replayable.

## Install

```bash
pip install domtrack
playwright install chromium
```

From a checkout:

```bash
pip install -e .
playwright install chromium
```

Requires Python 3.10+.

## Quickstart

### CLI

```bash
# 1. Record. Opens a browser; interact with the page, then press Resume.
domtrack capture https://example.com -o traces

# 2. Replay the newest trace as real interactions.
domtrack replay traces/trace_20260101_120000.json
```

The two standalone entry points `domtrack-capture` and `domtrack-replay` are
equivalent.

### Python API

```python
from domtrack import TracerConfig
from domtrack.capture import Recorder
from domtrack.replay import Replayer, ReplayConfig

# Record 10 seconds of a headless session.
cfg = TracerConfig(url="https://example.com", headless=True, duration_ms=10_000)
result = Recorder(cfg).run()
print(result.json_path, result.mutation_count)

# Replay it.
replayer = Replayer.from_file(result.json_path, config=ReplayConfig(speed=2.0))
replayer.run()
replayer.print_report()
```

## What you get

A trace file is a single JSON document:

```jsonc
{
  "meta":     { "url": "...", "title": "...", "viewport": {"w": 1440, "h": 900} },
  "version":  2,
  "snapshot": { "type": 2, "node": { "id": 1, "type": 0, "childNodes": [ ... ] } },
  "records":  [ { "t": 350, "type": 3, "data": { "source": 0, "type": "attributes", ... } } ],
  "events":   [ { "t": 120, "kind": "click", ... } ]
}
```

* `snapshot` is an rrweb **FullSnapshot**.
* `records` is the rrweb **incremental** stream (mutation source).
* `events` are the captured user interactions.

Traces written by `domtrack` 1.x (a flat `"mutations"` array) still load and
replay unchanged — the loader auto-detects the old layout.

See [`docs/trace-format.md`](docs/trace-format.md) for the full field-by-field
spec.

## Rebuild HTML without a browser

Fold a trace's snapshot + incremental records back into HTML at any point —
useful for grabbing the DOM state a slider/price/chat widget reached mid-session:

```python
from domtrack import trace

raw = trace.load_raw("traces/trace_x.json")
html_at_1200ms = trace.rebuild_html(raw, at_ms=1200)  # t <= 1200ms
html_final     = trace.rebuild_html(raw)              # all records
```

Or use the CLI subcommand (`domtrack-html` is the standalone equivalent):

```bash
domtrack html traces/trace_x.json --at-ms 1200 --out at_1200.html
domtrack html snapshot.json records.json          # split inputs
```

## Diff two captures without a browser

Compare the *content* of two captures of the same page — element text plus
data-bearing attributes — after folding each trace's records:

```python
from domtrack import trace, diff

result = diff.diff_traces(
    trace.load_raw("traces/before.json"),
    trace.load_raw("traces/after.json"),
)
print(result.summary)   # {a_nodes, b_nodes, added, removed, changed}
for path, entry in result.added:
    print("+", path, entry.text)
```

```bash
domtrack diff traces/before.json traces/after.json
domtrack diff traces/before.json traces/after.json --json
```

## Replay, record, and diff (automation check)

Record the page a replay drives — with the same capture scripts a human
capture uses — then diff it against the original. One call does the whole loop:

```python
from domtrack.replay import ReplayConfig, compare_replay

result = compare_replay("traces/before.json", ReplayConfig(speed=2.0, headless=True))
print(result.summary)        # replay counts + {added, removed, changed}
print(result.after_path)     # the trace recorded from the automated run
for path, entry in result.diff.added:
    print("+", path, entry.text)
```

```bash
domtrack compare traces/before.json --headless -s 2     # replay + record + diff
domtrack compare traces/before.json --headless --json   # JSON
domtrack replay traces/before.json --record             # record only
```

## Documentation

All docs live in [`docs/`](docs/). They are written to be read in full by an
LLM in a single pass, with stable headings and self-contained examples.

| Document | Contents |
| --- | --- |
| [`docs/README.md`](docs/README.md) | LLM-oriented index: architecture, module map, API summary, recipes |
| [`docs/architecture.md`](docs/architecture.md) | How capture and replay fit together, data flow |
| [`docs/trace-format.md`](docs/trace-format.md) | The trace JSON contract (rrweb-style snapshot + records) |
| [`docs/capture.md`](docs/capture.md) | `domtrack.capture`, `TracerConfig`, `Recorder`, capture JS |
| [`docs/replay.md`](docs/replay.md) | `domtrack.replay`, `Replayer`, `ReplayConfig`, modes, filters, timing, replay recording, compare |
| [`docs/cli.md`](docs/cli.md) | Every command-line flag for all subcommands |
| [`docs/modules.md`](docs/modules.md) | Per-module reference for every file in the package |
| [`docs/examples.md`](docs/examples.md) | Copy-paste recipes (record, replay, clean mode, custom policies) |

## Development

```bash
pip install -e '.[dev]'
pytest
```

The test suite covers the trace format (snapshot indexing, HTML
reconstruction, record → mutation conversion, and legacy loading) and does not
need a browser.

## License

MIT — see [`LICENSE`](LICENSE).
