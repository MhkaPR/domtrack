# Capture — `domtrack.capture`

The capture half drives a real Chromium page, installs observation scripts, and
writes a trace file.

```python
from domtrack import TracerConfig
from domtrack.capture import Recorder, CaptureResult
```

## TracerConfig

Defined in `domtrack/config.py` and re-exported from `domtrack` and
`domtrack.capture`.

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `url` | `str` | required | Page to open |
| `target_selector` | `str or None` | `None` | Subtree to observe (default: `body`) |
| `viewport` | `dict` | `{"width": 1440, "height": 900}` | Browser viewport |
| `headless` | `bool` | `False` | Headless browser |
| `goto_timeout_ms` | `int` | `360000` | Navigation timeout |
| `wait_until` | `str` | `"domcontentloaded"` | Playwright navigation condition |
| `max_text` | `int` | `500` | Max chars of text kept per snapshot node |
| `max_nodes` | `int` | `50` | Max added/removed nodes captured per childList record |
| `snapshot_max_depth` | `int` | `20` | Max depth of the serialized snapshot |
| `snapshot_max_nodes` | `int` | `20000` | Max nodes in the serialized snapshot |
| `dynamic_classes` | `tuple[str, ...]` | `DEFAULT_DYNAMIC_CLASSES` | Classes treated as volatile (excluded from selectors) |
| `capture_events` | `bool` | `True` | Capture user events |
| `scroll_throttle_ms` | `int` | `100` | Scroll event throttle |
| `duration_ms` | `int or None` | `None` | Fixed recording length; `None` = interactive pause |
| `output_dir` | `str` | `"traces"` | Output directory |

`DEFAULT_DYNAMIC_CLASSES = ("d-none", "d-tablecell", "d-block", "active",
"addctived", "show", "hidden", "fade", "collapse", "collapsing", "open",
"closed")`.

Validation: `url` is required; `duration_ms` must be `>= 0` if set.

## Recorder

```python
cfg = TracerConfig(url="https://example.com", headless=True, duration_ms=5000)
recorder = Recorder(cfg)
result = recorder.run()          # capture + save
```

| Method | Returns | Behaviour |
| --- | --- | --- |
| `capture()` | `dict` | Launch, navigate, install scripts, wait, collect raw payload |
| `save(payload, stamp=None)` | `(json_path, csv_path)` | Write JSON + CSV |
| `run()` | `CaptureResult` | `capture()` then `save()`; prints a summary |

`CaptureResult` fields: `json_path`, `csv_path`, `mutation_count`, `event_count`.

### CaptureSession (shared capture core)

`Recorder` is a thin driver over `CaptureSession`, which is also what records
the page during a replay (`domtrack.replay.recording`). Install the scripts
before driving a page and collect after:

```python
from domtrack.capture import CaptureSession
from domtrack import TracerConfig

session = CaptureSession(TracerConfig(url="https://example.com", headless=True))
session.install(page)                    # snapshot + observers start
#   ... drive the page (human or replay) ...
payload = session.build_payload(session.collect(page))
result = session.save_result(payload, out_dir="traces")
```

| Method | Behaviour |
| --- | --- |
| `install(page)` | Run `HELPERS_INIT`, build the observer + snapshot, attach event listeners |
| `collect(page)` | Disconnect the observer and return the raw browser payload |
| `capture(page)` | `install` + `collect` |
| `build_payload(raw)` | Wrap the raw payload into the rrweb-style trace layout |
| `save(payload, out_dir=None, stamp=None)` | Write JSON + CSV |
| `save_result(payload, ...)` | `save` + summarise as `CaptureResult` |

### Recording modes

* `duration_ms` set -> record for that long, then stop (works headless).
* `duration_ms` is `None` and not headless -> `page.pause()` for interactive
  control ("press Resume").
* `duration_ms` is `None` and headless -> raises `ValueError` (you must give a
  duration).

## Browser-side scripts (`domtrack.js_scripts`)

| Name | Purpose |
| --- | --- |
| `HELPERS_INIT` | Defines `window.__cssPath` (stable selector builder) |
| `build_init_script(cfg)` | Renders `INIT_TEMPLATE` with config values; installs the `MutationObserver` and serializes the full snapshot |
| `EVENT_INIT` | Attaches user-event listeners |
| `COLLECT_SCRIPT` | Disconnects the observer and returns `{meta, count, snapshot, records, events}` |

Install order matters: `HELPERS_INIT` must run before `EVENT_INIT` (which uses
`window.__cssPath`).

### Selector generation

`cssPath(el)` prefers `#id`; otherwise it walks ancestors building
`tag.class1.class2:nth-of-type(n)` segments, skipping `dynamic_classes` and
keeping at most three classes per segment. This is the selector stored in
`target.selector` and used at replay time.

## Storage (`domtrack.storage`)

* `save_json(payload, out_dir, stamp=None) -> path`
* `save_csv(records, out_dir, stamp=None) -> path`
* `timestamp() -> "YYYYMMDD_HHMMSS"`

`save_csv` accepts rrweb-style records or legacy mutation dicts and flattens
them to columns: `t, type, target.tag, target.id, target.selector,
attributeName, oldValue, newValue, addedCount, removedCount`.

## Example: programmatic capture

```python
from domtrack import TracerConfig
from domtrack.capture import Recorder

cfg = TracerConfig(
    url="https://example.com",
    target_selector="main",      # observe only <main>
    headless=True,
    duration_ms=15_000,
    max_nodes=100,
    output_dir="traces",
)
result = Recorder(cfg).run()
print(result.json_path, result.mutation_count, result.event_count)
```
