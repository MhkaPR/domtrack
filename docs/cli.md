# Command line

Five entry points are installed by the package:

| Command | Equivalent |
| --- | --- |
| `domtrack <capture\|replay\|html\|diff> ...` | unified dispatcher (`domtrack.cli`) |
| `domtrack-capture ...` | `domtrack capture` (`domtrack.capture.cli`) |
| `domtrack-replay ...` | `domtrack replay` (`domtrack.replay.cli`) |
| `domtrack-html ...` | `domtrack html` (`domtrack.html.cli`) |
| `domtrack-diff ...` | `domtrack diff` (`domtrack.diff.cli`) |

All of them also work as modules: `python -m domtrack`, `python -m domtrack.capture`,
`python -m domtrack.replay`, `python -m domtrack.html`, `python -m domtrack.diff`.

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

Examples:

```bash
# Replay as real interactions, twice as fast.
domtrack replay traces/trace_x.json --speed 2

# Forced DOM patches, headless, ignore third-party widgets.
domtrack replay traces/trace_x.json --replay-mode mutations -m clean --headless

# Replay events but ignore wheel/scroll noise.
domtrack replay traces/trace_x.json --skip-event wheel --skip-event scroll
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
