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
