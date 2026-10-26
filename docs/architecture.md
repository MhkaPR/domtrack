# Architecture

`domtrack` is deliberately split into a capture half, a replay half, and a
browser-free format core. The only shared artifact is the trace file.

## Components

| Component | Module | Depends on | Responsibility |
| --- | --- | --- | --- |
| Config | `domtrack.config` | — | `TracerConfig` capture settings |
| Browser JS | `domtrack.js_scripts` | `config` | JS installed into the page to observe and record |
| Recorder | `domtrack.recorder` | `config`, `js_scripts`, `storage`, `trace` | Launch browser, install JS, collect, write files |
| Capture core | `domtrack.recorder.CaptureSession` | `config`, `js_scripts`, `storage` | Install/collect a trace payload on a page (shared by capture + replay recording) |
| Storage | `domtrack.storage` | — | `save_json`, `save_csv`, `timestamp` |
| Trace core | `domtrack.trace` | — | rrweb constants, snapshot indexing, HTML rebuild (`DomState`), conversion |
| Content diff | `domtrack.diff.core` | `trace` | Fold two traces into content views and compare text/attributes |
| Table discovery | `domtrack.tables.discovery` | `trace` | Fold a trace and find repeated tabular structures |
| Table labeling | `domtrack.tables.labeling` | — | Add semantic titles / column labels with an LLM |
| Table rendering | `domtrack.tables.render` | — | Render labeled tables (rich / plain / markdown / csv) |
| Replay models | `domtrack.replay.models` | `trace` | Dataclasses for a loaded trace |
| Locator | `domtrack.replay.locators` | `models`, `selectors` | Find a mutation's element (id → selector → stripped classes) |
| Appliers | `domtrack.replay.appliers` | `models`, `locators` | Apply a mutation to the live DOM |
| Filters | `domtrack.replay.filters` | `models`, `third_party` | Decide whether a mutation should be replayed |
| Third-party | `domtrack.replay.third_party` | — | Heuristic widget detection |
| Timing | `domtrack.replay.timing` | — | Wait policy between records |
| Event replay | `domtrack.replay.event_replayer` | `models`, `timing` | Replay user events |
| Replayer | `domtrack.replay.replayer` | all of the above | Orchestrate a replay |
| Replay recording | `domtrack.replay.recording` | `recorder`, `config` | Record the replayed page as a trace |
| Compare | `domtrack.replay.compare` | `replayer`, `recording`, `diff` | Replay → record → diff loop |

## Capture flow

1. `Recorder.capture()` opens a Chromium page and navigates to `config.url`.
2. It evaluates `HELPERS_INIT` (defines `window.__cssPath`), then
   `build_init_script(config)` (installs the `MutationObserver` and serializes
   the initial DOM into `window.__domtrack.snapshot`), then `EVENT_INIT`
   (attaches user-event listeners) unless `capture_events=False`.
3. The observer pushes an rrweb-style record for every mutation into
   `window.capturedMutations`; events go into `window.__events`.
4. `COLLECT_SCRIPT` disconnects the observer and returns
   `{meta, count, snapshot, records, events}`.
5. `Recorder._build_payload()` wraps that into `{meta, version, snapshot,
   records, events, count}` and `save()` writes JSON + CSV.

Node identity is assigned on the JS side: every node gets a numeric id via a
`WeakMap`, and the full snapshot serializes the tree once. Later records only
carry ids and the minimal reference needed to re-locate the element
(`tag`, `id`, `selector`).

## Replay flow

1. `Trace.load(path)` reads the JSON and normalises it to `Mutation` objects
   through `trace.to_mutation_dicts()` (handles both formats).
2. `Replayer.run()` opens a page at `trace.meta.url`.
3. Depending on `ReplayConfig.replay_mode`:
   * `events` — `EventReplayer` dispatches captured interactions.
   * `mutations` — each mutation is applied to the DOM via `ApplierRegistry`
     (after `ElementLocator` finds the target and filters decide to keep it).
   * `both` — events first, then mutations are *verified* (not applied).
4. `ReplayStats` accumulates applied/skipped/failed counts; `print_report()`
   renders them.

## Offline reconstruction & diff

Both are browser-free and reuse the same folding core
(`domtrack.trace.DomState`):

1. `DomState.from_trace(raw)` seeds the tree from the `FullSnapshot`.
2. `apply_up_to(records, at_ms)` folds the incremental records (no duplicates:
   re-adds are skipped, moves are detached first, removals unregister nodes).
3. `to_html()` serializes the state — this is `domtrack html`.

`domtrack diff` builds one such state per capture, reduces each to a path-keyed
content view (`domtrack.diff.core.content_view`) of element text + data
attributes (dropping non-data and volatile attributes/text), and reports
`added` / `removed` / `changed`. Because the records are folded through
`DomState`, structural inserts (childList) and text/attribute updates are all
reflected in the diff.

## Table discovery

`domtrack tables extract` reuses the same folding core and adds a structural
pass on top:

1. `discovery.build_tree(raw)` folds the trace with `DomState` and adapts the
   result to a light `Node` view.
2. `find_candidates` clusters siblings whose *shape* (the set of leaf paths
   under them) is mutually similar — the signature of a table, list, or card
   grid.
3. `build_columns` clusters the per-row atoms (text / `href` / `src` / …) into
   columns by path similarity; `score_candidate` ranks candidates.
4. `dedupe` collapses overlapping containers by a data fingerprint, so nested
   tables keep only the largest.

`domtrack tables label` then sends a compact payload to an LLM to attach a
`title`, `purpose`, and per-column `semantic_label`, and `domtrack tables show`
renders the labeled JSON. All three stages are browser-free.

## Replay → record → diff

Because a replay drives a real page, domtrack can capture that page exactly as
a human capture would, then diff the two:

1. `Replayer._setup_page()` loads the page; when `ReplayConfig.recording` is
   enabled it calls `ReplayRecorder.start()`, which installs the shared
   `CaptureSession` scripts — a fresh `FullSnapshot` plus observers are taken
   *before* any replayed event.
2. The replay drives the page (events / mutations / both).
3. `ReplayRecorder.finish_and_save()` disconnects the observer, collects the
   payload, and writes a normal trace file; the `CaptureResult` lands on
   `Replayer.record_result`.
4. `domtrack.replay.compare.compare_replay()` glues this together and diffs the
   recorded *after* trace against the original *before* trace with
   `domtrack.diff`, returning a `CompareResult`.

The recorded trace is byte-compatible with a hand capture because it flows
through the same `CaptureSession.build_payload` / `save` code path.

## Why the rrweb shape

The 1.x format embedded, in every record, the target's text (up to 500 chars),
its `outerHTML` (up to 2000 chars) for added/removed nodes, and sibling HTML.
On slider-heavy pages that dominates the file. The rrweb shape stores the tree
once (snapshot) and thereafter only diffs, cutting the mutation payload to
roughly a third of its size on real traces, while keeping replay semantics
identical.

## Extension points

* **Timing** — subclass `TimePolicy` and pass it via `build_policy` or supply a
  custom instance when constructing `EventReplayer`; the CLI exposes the four
  built-ins via `--time-policy`.
* **Filtering** — subclass `MutationFilter`; compose with `CompositeFilter`.
* **Third-party detection** — pass `hints=[...]` to `ThirdPartyDetector`.
* **Element lookup** — extend `ElementLocator.find()` with new strategies.
* **Content filters** — the diff's non-data/volatile attribute and text rules
  are plain module constants (`domtrack.diff.core.NON_DATA_ATTRS`,
  `VOLATILE_ATTR_NAME`, `VOLATILE_TEXT`); adjust them or pass your own view to
  `diff_views`.
