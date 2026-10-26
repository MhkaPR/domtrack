# Replay — `domtrack.replay`

The replay half reads a trace and reproduces the session on a fresh page.

```python
from domtrack.replay import Replayer, ReplayConfig, ReplayStats
```

## ReplayConfig

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `speed` | `float` | `1.0` | Time multiplier (must be `> 0`) |
| `mode` | `"all" or "clean"` | `"all"` | `"clean"` skips third-party widgets |
| `replay_mode` | `"events" or "mutations" or "both"` | `"events"` | What to do |
| `structure_only` | `bool` | `False` | Replay only childList add/remove mutations |
| `time_policy` | `"faithful" or "instant" or "capped" or "skip-aware"` | `"skip-aware"` | Wait strategy |
| `gap_cap_ms` | `int or None` | `None` | Per-wait cap for capped/skip-aware |
| `fast_load` | `bool` | `False` | Skip networkidle wait; shorter initial delay |
| `headless` | `bool` | `False` | Headless browser |
| `wait_for_ids_ms` | `int` | `2000` | Max time to wait for target ids on load |
| `close_delay_ms` | `int` | `5000` | Keep the browser open after replay |
| `wait_before_replay_ms` | `int` | `0` | Pause after load before replaying |
| `skip_events` | `tuple[str, ...]` | `()` | Event kinds not to replay |
| `recording` | `RecordingConfig or None` | `None` | Record the replayed page as a trace (see below) |

Validation: `mode`, `replay_mode`, and `speed` are checked in `__post_init__`.

## Replay modes

* **events** (default) — dispatch the captured interactions with Playwright
  (mouse, keyboard, scroll, wheel). This exercises the real app logic.
* **mutations** — force DOM patches: locate each mutation's element and apply
  the change directly. Use when you need the exact recorded DOM state.
* **both** — replay events, then *verify* (not apply) that the mutations
  occurred on the live page.

## Replayer

```python
replayer = Replayer(trace, config=ReplayConfig(speed=2.0))
# or:
replayer = Replayer.from_file("traces/trace_x.json", config=ReplayConfig())
stats: ReplayStats = replayer.run()
replayer.print_report()
```

`run()` opens a page at `trace.meta.url`, optionally waits for known target ids,
replays according to the mode, and returns `ReplayStats`.

### ReplayStats

| Field | Meaning |
| --- | --- |
| `applied` | Mutations applied successfully |
| `skipped` | Mutations skipped by a filter |
| `failed` | Mutations that could not be applied |
| `skip_reasons` | `Counter` of skip reasons |
| `failures` | List of `Failure(time_ms, mutation_type, selector, reason)` |

`print_report()` prints applied/skipped/failed counts, skip reasons, the lookup
strategies used, and up to ten failures.

## Recording during a replay

A replay is automation, so you often want to know whether it produced the same
page a human did. Enable recording and the replay captures the live page with
the **exact same** scripts a human capture uses (shared
`domtrack.recorder.CaptureSession`), writing a normal domtrack trace file:

```python
from domtrack.replay import Replayer, ReplayConfig, RecordingConfig

cfg = ReplayConfig(
    speed=2.0,
    recording=RecordingConfig(enabled=True, output_dir="traces"),
)
replayer = Replayer.from_file("traces/before.json", config=cfg)
replayer.run()
print(replayer.record_result.json_path, replayer.record_result.mutation_count)
```

Capture scripts are installed right after the page loads (before any replayed
event), so the recorded `FullSnapshot` is the loaded page and the records are
everything the replay caused. `replayer.record_result` is a
`CaptureResult(json_path, csv_path, mutation_count, event_count)`.

### RecordingConfig

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `enabled` | `bool` | `False` | Turn recording on |
| `output_dir` | `str` | `"traces"` | Where to write the recorded trace |
| `target_selector` | `str or None` | `None` | Subtree to observe (default: `body`) |
| `capture_events` | `bool` | `False` | Also record user events (a replay generates synthetic ones) |
| `max_text` | `int` | `500` | Max chars of text per snapshot node |
| `max_nodes` | `int` | `50` | Max added/removed nodes per childList record |
| `snapshot_max_depth` | `int` | `20` | Max depth of the recorded snapshot |
| `snapshot_max_nodes` | `int` | `20000` | Max nodes in the recorded snapshot |
| `tracer` | `TracerConfig or None` | `None` | Reuse a full capture config (wins over the trace's url/viewport) |

### ReplayRecorder

The lower-level primitive, if you want to drive the browser yourself:

```python
from domtrack.replay.recording import ReplayRecorder, RecordingConfig

recorder = ReplayRecorder(RecordingConfig(enabled=True))
recorder.start(page, url=trace.meta.url, viewport=trace.meta.viewport)
#   ... drive the page ...
result = recorder.finish_and_save(page)      # or finish(page) then save(payload)
```

## Replay → record → diff (compare)

`domtrack.replay.compare` runs the whole automation loop: replay the *before*
trace, record the automated run as an *after* trace, then content-diff the two
(see `domtrack.diff`).

```python
from domtrack.replay import ReplayConfig, compare_replay

result = compare_replay("traces/before.json", ReplayConfig(speed=2.0, headless=True))
print(result.summary)          # replay counts + diff tallies
print(result.after_path)       # the recorded replay trace
for path, entry in result.diff.added:
    print("+", path, entry.text)
```

`CompareResult` fields: `before_path`, `after_path`, `after_csv_path`, `stats`
(`ReplayStats`), `diff` (`DiffResult`), `replay_mutations`,
`recorded_mutations`, `recorded_events`; `summary` merges the replay counts with
`diff.summary`. Use `compare_traces(before_path, after_path)` when you already
have both trace files and only want the diff.

## Element lookup (`ElementLocator`)

`find(mutation) -> (ElementHandle | None, strategy | None)` tries, in order:

1. `#id` from `target.id`
2. the recorded `target.selector`
3. the selector with **only** the classes this mutation changed stripped
   (`selectors.strip_classes`)
4. the selector with **all** classes stripped (`selectors.strip_all_classes`)

Steps 3 and 4 disambiguate by tag when the stripped selector matches multiple
elements. `strategy_stats` reports how often each path was used.

## Appliers (`ApplierRegistry`)

Dispatches by mutation type:

| Type | Applier | Action |
| --- | --- | --- |
| `attributes` | `AttributesApplier` | Sets/removes the attribute; `class` uses add/remove deltas |
| `childList` | `ChildListApplier` | Removes then adds nodes (skips already-present ids) |
| `characterData` | `CharacterDataApplier` | Rewrites the first text child |

Each returns `ApplyResult(ok, strategy, error)`.

## Filters (`MutationFilter`)

* `ThirdPartyFilter(detector)` — blocks selectors/ids matching widget hints.
* `StructureOnlyFilter()` — keeps only childList mutations with adds/removes.
* `CompositeFilter([...])` — runs filters in order; first block wins.

## Timing (`TimePolicy`)

| Policy | Behaviour |
| --- | --- |
| `FaithfulTimePolicy(speed)` | Wait the real gap / speed |
| `InstantTimePolicy()` | No waits |
| `CappedTimePolicy(speed, cap_ms)` | Real gap / speed, capped per wait |
| `SkipAwareTimePolicy(speed, cap_ms)` | Wait only for buckets with work; gap measured from the last work bucket |

`build_policy(name, speed, cap_ms=None)` maps a name to an instance.

## Events (`EventReplayer`)

Replays the event stream in time order. Only `mouse.down()`/`mouse.up()` are
dispatched for pointer events — Chromium then fires the full gesture
(`pointerdown`/`mousedown`, `pointerup`/`mouseup`/`click`). Keyboard uses
`keyboard.down/up`; `input` is a safety net that fills the field if no keydowns
were recorded; `scroll` uses `window.scrollTo` / `element.scrollTo`; `wheel`
uses `mouse.wheel`.

## Example

```python
from domtrack.replay import Replayer, ReplayConfig

cfg = ReplayConfig(
    speed=2.0,
    replay_mode="events",
    mode="clean",              # skip third-party widgets
    time_policy="skip-aware",
    gap_cap_ms=300,
    skip_events=("wheel",),    # ignore wheel events
)
replayer = Replayer.from_file("traces/trace_x.json", config=cfg)
stats = replayer.run()
replayer.print_report()
```
