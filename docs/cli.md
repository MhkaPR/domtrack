# Command line

Eight entry points are installed by the package:

| Command | Equivalent |
| --- | --- |
| `domtrack <capture\|replay\|html\|diff\|compare\|tables\|serve> ...` | unified dispatcher (`domtrack.cli`) |
| `domtrack-capture ...` | `domtrack capture` (`domtrack.capture.cli`) |
| `domtrack-replay ...` | `domtrack replay` (`domtrack.replay.cli`) |
| `domtrack-html ...` | `domtrack html` (`domtrack.html.cli`) |
| `domtrack-diff ...` | `domtrack diff` (`domtrack.diff.cli`) |
| `domtrack-compare ...` | `domtrack compare` (`domtrack.compare.cli`) |
| `domtrack-tables ...` | `domtrack tables` (`domtrack.tables.cli`) |
| `domtrack-serve ...` | `domtrack serve` (`domtrack.api.__main__`) |

All of them also work as modules: `python -m domtrack`, `python -m domtrack.capture`,
`python -m domtrack.replay`, `python -m domtrack.html`, `python -m domtrack.diff`,
`python -m domtrack.compare`, `python -m domtrack.tables`, `python -m domtrack.api`.

## `python -m domtrack serve`

Run the REST API server (needs `pip install 'domtrack[api]'`).

| Flag | Default | Meaning |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Bind host |
| `--port` | `8000` | Bind port |
| `--reload` | off | Auto-reload on code changes (dev) |
| `--workers` | `1` | Worker processes |
| `--trace-dir` | `traces` | Directory for traces written by the API |
| `--log-level` | `info` | Uvicorn log level |

```bash
python -m domtrack serve
python -m domtrack serve --host 0.0.0.0 --port 8080 --trace-dir /data/traces
```

Every REST endpoint maps to a CLI command. Full reference: [`api.md`](api.md).

## `domtrack capture <url>`

Record DOM mutations and user events from a live page.

| Flag | Default | Meaning |
| --- | --- | --- |
| `url` (positional) | — | Page to open and trace |
| `-s`, `--selector` | `body` | CSS selector of the subtree to observe |
| `-o`, `--output-dir` | `traces` | Directory for trace files |
| `--viewport-w` | `1440` | Viewport width |
| `--viewport-h` | `900` | Viewport height |
| `--headless` | off | Run headless (requires `--duration`) |
| `--duration` | none | Seconds to record (default: interactive pause) |
| `--timeout` | `360000` | Navigation timeout (ms) |
| `--max-text` | `500` | Max chars of text per snapshot node |
| `--max-nodes` | `50` | Max added/removed nodes per childList record |
| `--snapshot-max-depth` | `20` | Max depth of the serialized snapshot |
| `--snapshot-max-nodes` | `20000` | Max nodes in the serialized snapshot |
| `--scroll-throttle-ms` | `100` | Scroll throttle (ms) |
| `--no-events` | off | Disable event capture (mutations only) |
| `--dynamic-class` | defaults | Dynamic class name (repeatable; overrides defaults) |

Examples:

```bash
# Interactive: opens a browser, press Resume when done.
domtrack capture https://example.com -o traces

# Headless, 20 seconds, observe only <main>.
domtrack capture https://example.com --headless --duration 20 -s main

# Mutations only, tighter limits.
domtrack capture https://example.com --headless --duration 10 \
    --no-events --max-nodes 20 --snapshot-max-nodes 5000
```

## `domtrack replay <trace_file>`

Replay a captured trace on the live page.

| Flag | Default | Meaning |
| --- | --- | --- |
| `trace_file` (positional) | — | Path to trace JSON |
| `-s`, `--speed` | `1.0` | Speed multiplier |
| `-m`, `--mode` | `all` | `clean` skips third-party widgets |
| `--structure-only` | off | Replay only mutations that add/remove elements |
| `--time-policy` | `skip-aware` | `faithful`/`instant`/`capped`/`skip-aware` |
| `--gap-cap-ms` | none | Hard cap per wait (capped/skip-aware) |
| `--fast-load` | off | Skip networkidle wait; shorter initial delay |
| `--no-close-delay` | off | Close the browser immediately after replay |
| `--headless` | off | Run headless |
| `--wait-for-ids-ms` | `2000` | Max time to wait for target ids on load |
| `--close-delay-ms` | `5000` | Keep the browser open after replay |
| `--third-party-hint` | none | Extra third-party widget name (repeatable) |
| `--replay-mode` | `events` | `events`/`mutations`/`both` |
| `--wait-before-replay-ms` | `0` | Pause after page load before replay |
| `--skip-event` | none | Do not replay this event kind (repeatable) |
| `--record` | off | Record the replayed page as a trace (same format as capture) |
| `--record-dir` | `traces` | Directory for the recorded replay trace |
| `--record-selector` | `body` | Subtree to observe while recording |
| `--record-events` | off | Also record user events in the replayed page |
| `--record-snapshot-max-nodes` | `20000` | Max nodes in the recorded snapshot |

Examples:

```bash
# Replay as real interactions, twice as fast.
domtrack replay traces/trace_x.json --speed 2

# Forced DOM patches, headless, ignore third-party widgets.
domtrack replay traces/trace_x.json --replay-mode mutations -m clean --headless

# Replay events but ignore wheel/scroll noise.
domtrack replay traces/trace_x.json --skip-event wheel --skip-event scroll

# Replay and save what the automated run produced.
domtrack replay traces/before.json --record --record-dir traces
```

The CLI warns when `--skip-event` includes `mousedown`/`mouseup` (clicks will
not fire) or `keydown`/`keyup` (text input and shortcuts will not work).

## `domtrack html <trace_file>`

Rebuild a trace's DOM as HTML at a record point, with no browser. It folds the
`FullSnapshot` plus the incremental records up to a chosen point and serializes
the result (see [`trace-format.md`](trace-format.md#rebuilding-html-at-a-record-point)).

| Flag | Default | Meaning |
| --- | --- | --- |
| `FILE...` (positional) | — | One combined trace, or `<snapshot.json> <records.json>` |
| `--at-ms` | all | Rebuild only up to this ms since capture start |
| `--at-index` | all | Rebuild only through record `N` (0-based, inclusive) |
| `-o`, `--out` | stdout | Write HTML to this file |
| `--stats` | off | Print reconstruction stats to stderr |

`--at-ms` and `--at-index` are mutually exclusive. Exit codes: `0` ok, `1` load
error (missing file, not an rrweb-style trace, no snapshot, write error), `2`
bad arguments.

Examples:

```bash
# DOM state once the page settled at 1.2s.
domtrack html traces/trace_x.json --at-ms 1200 --out at_1200.html

# Just after the 5th incremental record.
domtrack html traces/trace_x.json --at-index 4 --stats -o /dev/null

# Snapshot + records stored as two files.
domtrack html snapshot.json records.json
```

## `domtrack diff <A.json> <B.json>`

Compare the *content* of two captures of the same page, with no browser. Each
trace is folded (snapshot + records) and reduced to a path-keyed view of
element text plus data-bearing attributes (see
[`modules.md`](modules.md#domtrackdiffcorepy)).

| Flag | Default | Meaning |
| --- | --- | --- |
| `A.json` (positional) | — | Baseline trace |
| `B.json` (positional) | — | Trace compared against A |
| `--no-records` | off | Compare only the initial snapshots (skip records) |
| `--at-ms-a` | all | Fold A only up to this ms |
| `--at-ms-b` | all | Fold B only up to this ms |
| `--json` | off | Emit `{summary, added, removed, changed}` as JSON |

Exit codes: `0` ok, `1` load error (missing file, not an rrweb-style trace, no
snapshot).

Examples:

```bash
# What changed between two captures of the same page.
domtrack diff traces/A.json traces/B.json

# Machine-readable, for pipelines.
domtrack diff traces/before.json traces/after.json --json

# Compare the two initial DOMs, ignoring the record streams.
domtrack diff traces/A.json traces/B.json --no-records
```

`added`/`removed` are elements present only in B / only in A; `changed` are
matched paths whose text or data attributes differ. Paths look like
`/html[0]/body[0]/ul[0]/li[2]/h2[0]`.

## `domtrack compare <BEFORE.json>`

Replay a human-captured trace, **record** the automated run as a trace, and
print the content **diff** against the original — the whole loop in one
command, no manual capture needed.

| Flag | Default | Meaning |
| --- | --- | --- |
| `BEFORE.json` (positional) | — | Human trace to replay + compare against |
| `-s`, `--speed` | `1.0` | Replay speed multiplier |
| `-m`, `--mode` | `all` | `clean` skips third-party widgets |
| `--replay-mode` | `events` | `events`/`mutations`/`both` |
| `--time-policy` | `skip-aware` | Timing strategy |
| `--gap-cap-ms` | none | Hard cap per wait |
| `--fast-load` | off | Skip networkidle wait |
| `--headless` | off | Run headless |
| `--wait-before-replay-ms` | `0` | Pause after load before replay |
| `--close-delay-ms` | `0` | Keep the browser open after replay |
| `--skip-event` | none | Do not replay this event kind (repeatable) |
| `--third-party-hint` | none | Extra widget name (repeatable) |
| `-o`, `--record-dir` | `traces` | Directory for the recorded replay trace |
| `--record-selector` | `body` | Subtree to observe while recording |
| `--record-events` | off | Also record user events in the replayed page |
| `--json` | off | Emit replay counts + the diff as JSON |

Exit codes: `0` ok, `1` load/run error, `130` interrupted.

Examples:

```bash
# Replay, record, and show what changed vs the human capture.
domtrack compare traces/before.json --headless -s 2

# Machine-readable, recorded trace written under traces/.
domtrack compare traces/before.json --headless --json -o traces
```

The recorded trace is an ordinary domtrack file (same format as `domtrack
capture`), so you can also re-run `domtrack diff traces/before.json <recorded>`
later.

## `domtrack tables <extract|label|show>`

Discover, label, and display the tabular structures in a trace. Browser-free
discovery reuses the `DomState` folding core; labeling calls an LLM; display
renders the result. See [`tables.md`](tables.md) for the full pipeline.

### `domtrack tables extract <FILE...>`

| Flag | Default | Meaning |
| --- | --- | --- |
| `FILE...` (positional) | — | One combined trace, or `<snapshot.json> <records.json>` |
| `-o`, `--out` | stdout | Write JSON here |
| `--all` | off | Keep overlapping tables (skip de-duplication) |
| `--min-rows` | `3` | Minimum rows a table must keep |
| `--min-cols` | `2` | Minimum columns a table must keep |
| `--verbose` | off | Print a per-table summary to stderr |

Exit codes: `0` ok, `1` load error, `2` bad arguments.

```bash
domtrack tables extract traces/trace_x.json -o tables.json --verbose
domtrack tables extract snapshot.json records.json
```

### `domtrack tables label <tables.json>`

| Flag | Default | Meaning |
| --- | --- | --- |
| `input` (positional) | — | JSON from `tables extract` |
| `-o`, `--output` | `tables_labeled.json` | Output file |
| `--provider` | `openai` | `openai` / `compatible` / `anthropic` |
| `--model` | provider default | Model id |
| `--base-url` | — | Base URL for `compatible` |
| `--api-key` | env | API key (else `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`) |
| `--lang` | `fa` | Output language |
| `--batch-size` | `25` | Tables per model call |
| `--dry-run` | off | Print payloads, write nothing |

Exit codes: `0` ok, `1` missing key / no tables.

```bash
domtrack tables label tables.json -o labeled.json
domtrack tables label tables.json --provider anthropic --model claude-sonnet-4-5
domtrack tables label tables.json --dry-run
```

### `domtrack tables show <labeled.json>`

| Flag | Default | Meaning |
| --- | --- | --- |
| `input` (positional) | — | JSON from `tables label` |
| `--only` | all | Only these tables, e.g. `1,3,5` (1-based) |
| `--format` | `rich` | `rich` / `plain` / `markdown` / `csv` |
| `-o`, `--output` | stdout | Output file (for `markdown`/`csv`) |
| `--max-rows` | `0` (all) | Max rows per table |
| `--max-chars` | `80` | Max cell length |

Exit codes: `0` ok, `1` no tables, `2` bad `--only`.

```bash
domtrack tables show labeled.json
domtrack tables show labeled.json --format markdown -o tables.md
domtrack tables show labeled.json --format csv -o tables.csv
```
