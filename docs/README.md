# domtrack — documentation index (LLM-oriented)

This directory documents the `domtrack` package. It is written to be consumed
by a language model in a single pass: every module, its public API, and how the
pieces connect are described here and in the linked documents, with
self-contained, copy-pasteable examples.

If you are an LLM answering questions about or generating code against
`domtrack`, read this file and [`trace-format.md`](trace-format.md) first.

---

## 1. What domtrack is

`domtrack` has two independent halves that communicate through one artifact: a
**trace file** (JSON).

| Half | Package | Job | Browser? |
| --- | --- | --- | --- |
| Capture | `domtrack.capture` | Drive a real page, record DOM mutations + user events into a trace | yes |
| Replay | `domtrack.replay` | Read a trace and reproduce the session on a fresh page | yes |
| Record during replay | `domtrack.replay.recording` | Capture the replayed page as a normal trace | yes |
| Compare | `domtrack.replay.compare` | Replay → record → diff the result vs the original | yes |
| Rebuild | `domtrack.html` | Fold a trace into HTML at any record point | no |
| Diff | `domtrack.diff` | Compare the content of two captures | no |

Capture and replay depend on `playwright`. The trace **format** helpers
(`domtrack.trace`, including the `DomState` reconstructor), the **diff**
helpers (`domtrack.diff`), and the storage helpers (`domtrack.storage`) have no
browser dependency and can be used standalone. The `domtrack html` and
`domtrack diff` subcommands are thin CLI front-ends over those.

## 2. Data flow

```
 live page ──► [capture JS: MutationObserver + event listeners]
                    │  full snapshot + incremental records + events
                    ▼
              trace_<stamp>.json  (rrweb-style)  ──► trace_<stamp>.csv (flat summary)
                    │
                    ▼
   domtrack.trace.to_mutation_dicts()  ──► replay Mutation objects
                    │
                    ▼
              [Replayer] ──► events mode: real interactions
                          ──► mutations mode: forced DOM patches
                          ──► both mode: events + verification
                    │
                    ├─► (recording on) capture the page ──► trace_<stamp>.json
                    │
                    ▼
   domtrack.replay.compare ──► diff(before, recorded) ──► CompareResult
```

## 3. Package map

```
domtrack/
├── __init__.py        top-level exports + lazy `capture` / `replay` access
├── __main__.py        `python -m domtrack`
├── cli.py             unified `domtrack <capture|replay|html|diff|compare>` dispatcher
├── config.py          TracerConfig, DEFAULT_DYNAMIC_CLASSES   (capture config)
├── js_scripts.py      browser-side JS: snapshot + mutation + event capture
├── recorder.py        Recorder / CaptureResult — orchestrates a capture
├── storage.py         save_json / save_csv / timestamp
├── trace.py           trace format: rrweb constants, conversion, DomState HTML rebuild
├── capture/
│   ├── __init__.py    capture public surface
│   ├── cli.py         `domtrack capture` argument parser + main()
│   └── __main__.py    `python -m domtrack.capture`
├── html/
│   ├── __init__.py    html public surface (`build_parser`, `main`)
│   ├── cli.py         `domtrack html` argument parser + main()
│   └── __main__.py    `python -m domtrack.html`
├── diff/
│   ├── __init__.py    diff public surface (content view, diff, report)
│   ├── core.py        content view + content-level DiffResult
│   ├── cli.py         `domtrack diff` argument parser + main()
│   └── __main__.py    `python -m domtrack.diff`
├── compare/
│   ├── __init__.py    compare public surface (`build_parser`, `main`)
│   ├── cli.py         `domtrack compare` argument parser + main()
│   └── __main__.py    `python -m domtrack.compare`
└── replay/
    ├── __init__.py    replay public surface
    ├── cli.py         `domtrack replay` argument parser + main()
    ├── __main__.py    `python -m domtrack.replay`
    ├── models.py      Trace, TraceMeta, Mutation, Target, NodeInfo, Event
    ├── locators.py    ElementLocator — multi-strategy element lookup
    ├── selectors.py   selector rewriting helpers (strip dynamic classes)
    ├── appliers.py    ApplierRegistry + per-type mutation appliers
    ├── filters.py     MutationFilter, ThirdPartyFilter, StructureOnlyFilter
    ├── third_party.py ThirdPartyDetector — widget heuristics
    ├── timing.py      TimePolicy family (faithful/instant/capped/skip-aware)
    ├── event_replayer.py EventReplayer — replays user events
    ├── replayer.py    Replayer, ReplayConfig, ReplayStats, Failure
    ├── recording.py   ReplayRecorder, RecordingConfig — record the replay
    └── compare.py     ReplayComparer, compare_replay — replay+record+diff
```

## 4. Public API at a glance

### Capture

```python
from domtrack import TracerConfig
from domtrack.capture import Recorder, CaptureResult, save_json, save_csv

cfg = TracerConfig(url="https://example.com", headless=True, duration_ms=5000)
result: CaptureResult = Recorder(cfg).run()
```

* `TracerConfig` — all capture knobs (see [`capture.md`](capture.md)).
* `Recorder(config).run()` → `CaptureResult(json_path, csv_path, mutation_count, event_count)`.
* `Recorder(config).capture()` → raw payload dict; `.save(payload)` writes files.

### Replay

```python
from domtrack.replay import Replayer, ReplayConfig

replayer = Replayer.from_file("traces/trace_x.json", config=ReplayConfig(speed=2.0))
replayer.run()          # executes the replay
replayer.print_report() # human-readable summary
```

* `Replayer(trace, config=..., third_party_detector=...)` or `Replayer.from_file(path, ...)`.
* `ReplayConfig` — speed, mode, timing policy, headless, skip lists (see [`replay.md`](replay.md)).
* `replayer.run()` → `ReplayStats`; `replayer.print_report()` prints it.

### Format helpers (no browser)

```python
from domtrack import trace

raw = trace.load_raw("traces/trace_x.json")
assert trace.is_rrweb_trace(raw)
index = trace.index_snapshot(raw["snapshot"]["node"])   # {node_id: node}
html = trace.node_to_html(index[4])                     # rebuild element HTML
muts = trace.to_mutation_dicts(raw)                     # replay-ready dicts

# Rebuild the whole DOM at a record point (no browser):
html_at_1200 = trace.rebuild_html(raw, at_ms=1200)
html_final   = trace.rebuild_html(raw)                  # fold all records

# Or drive it step by step:
state = trace.DomState.from_trace(raw)
state.apply_up_to(raw["records"], at_ms=1200)
print(state.to_html(), state.stats)

# Compare two captures at the content level (no browser):
from domtrack import diff

result = diff.diff_traces(trace.load_raw("A.json"), trace.load_raw("B.json"))
print(result.summary)          # {a_nodes, b_nodes, added, removed, changed}
for path, entry in result.added:
    print("added", path, entry.text)
```

## 5. Minimal end-to-end example

```python
from domtrack import TracerConfig
from domtrack.capture import Recorder
from domtrack.replay import Replayer, ReplayConfig

cfg = TracerConfig(url="https://example.com", headless=True, duration_ms=8000,
                   output_dir="traces")
result = Recorder(cfg).run()

Replayer.from_file(result.json_path, config=ReplayConfig(speed=3.0)).run()
```

### Replay → record → diff (automation check)

```python
from domtrack.replay import ReplayConfig, compare_replay

result = compare_replay("traces/before.json", ReplayConfig(speed=2.0, headless=True))
print(result.summary)             # replay counts + {added, removed, changed}
print(result.after_path)          # the trace recorded from the automated run
for path, entry in result.diff.added:
    print("+", path, entry.text)
```

## 6. Trace format in one paragraph

A trace is `{meta, version, snapshot, records, events}`. `snapshot` is an
rrweb **FullSnapshot**: a tree of nodes, each with a numeric `id`, a `type`
(0 document, 1 doctype, 2 element, 3 text, 5 comment), and either `attributes`/
`childNodes` (elements) or `textContent` (text/comment). `records` is the
rrweb **incremental** stream: `{t, type: 3, data: {source: 0, type, target,
...}}` where `type` is `attributes` | `characterData` | `childList`. Records
reference nodes by id and never re-embed the target's full text/HTML. Full spec:
[`trace-format.md`](trace-format.md).

## 7. Versioning & compatibility

* Current trace format: `version: 2` (rrweb-style).
* `domtrack` 1.x traces (flat `"mutations"` array) are auto-detected and
  converted on load — replay code is unchanged.
* `Mutation`/`Trace`/`Replayer`/`ReplayConfig` keep their 1.x semantics.

## 8. Document index

* [`architecture.md`](architecture.md) — component responsibilities & flow.
* [`trace-format.md`](trace-format.md) — the JSON contract, field by field.
* [`capture.md`](capture.md) — capture API, config, and the browser JS.
* [`replay.md`](replay.md) — replay API, modes, filters, timing, stats.
* [`cli.md`](cli.md) — all command-line flags.
* [`modules.md`](modules.md) — per-file module reference.
* [`examples.md`](examples.md) — recipes.
