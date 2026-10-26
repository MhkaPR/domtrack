# Architecture

`domtrack` is deliberately split into a capture half, a replay half, and a
browser-free format core. The only shared artifact is the trace file.

## Components

| Component | Module | Depends on | Responsibility |
| --- | --- | --- | --- |
| Config | `domtrack.config` | — | `TracerConfig` capture settings |
| Browser JS | `domtrack.js_scripts` | `config` | JS installed into the page to observe and record |
| Recorder | `domtrack.recorder` | `config`, `js_scripts`, `storage`, `trace` | Launch browser, install JS, collect, write files |
| Storage | `domtrack.storage` | — | `save_json`, `save_csv`, `timestamp` |
| Trace core | `domtrack.trace` | — | rrweb constants, snapshot indexing, HTML rebuild, conversion |
| Replay models | `domtrack.replay.models` | `trace` | Dataclasses for a loaded trace |
| Locator | `domtrack.replay.locators` | `models`, `selectors` | Find a mutation's element (id → selector → stripped classes) |
| Appliers | `domtrack.replay.appliers` | `models`, `locators` | Apply a mutation to the live DOM |
| Filters | `domtrack.replay.filters` | `models`, `third_party` | Decide whether a mutation should be replayed |
| Third-party | `domtrack.replay.third_party` | — | Heuristic widget detection |
| Timing | `domtrack.replay.timing` | — | Wait policy between records |
| Event replay | `domtrack.replay.event_replayer` | `models`, `timing` | Replay user events |
| Replayer | `domtrack.replay.replayer` | all of the above | Orchestrate a replay |

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
