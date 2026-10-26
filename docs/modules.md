# Module reference

One section per file in the `domtrack` package. Signatures are the public
surface; private helpers (leading `_`) are omitted unless useful.

## `domtrack/__init__.py`

Top-level package. Re-exports `TracerConfig`, the trace constants
(`RecordType`, `IncrementalSource`, `NodeType`), and trace helpers
(`load_raw`, `is_rrweb_trace`, `index_snapshot`, `node_to_html`,
`to_mutation_dicts`). Provides lazy access to `domtrack.capture` and
`domtrack.replay` via PEP 562 `__getattr__` so importing the package does not
import Playwright. `__version__ = "2.0.0"`.

## `domtrack/__main__.py`

Runs `domtrack.cli.main` (`python -m domtrack`).

## `domtrack/cli.py`

Unified dispatcher.

```python
main(argv: list[str] | None = None) -> int
```

`argv[0]` selects `capture` or `replay`; the rest is passed to the matching
subcommand's `main`. No args (or `-h`/`--help`/`help`) prints usage.

## `domtrack/config.py`

```python
DEFAULT_DYNAMIC_CLASSES: tuple[str, ...]
@dataclass
class TracerConfig: ...
```

Capture settings (see [capture.md](capture.md)). Validates `url` and
`duration_ms` in `__post_init__`.

## `domtrack/js_scripts.py`

Browser-side JavaScript templates plus the constants mirroring
`domtrack.trace`.

```python
RT_INCREMENTAL_SNAPSHOT, SOURCE_MUTATION        # record/source constants
NT_DOCUMENT, NT_DOCUMENT_TYPE, NT_ELEMENT, NT_TEXT, NT_COMMENT
HELPERS_INIT: str          # defines window.__cssPath
INIT_TEMPLATE: str         # %-formatted snapshot + observer script
COLLECT_SCRIPT: str        # returns {meta, count, snapshot, records, events}
EVENT_INIT: str            # user-event listeners
def build_init_script(cfg: TracerConfig) -> str
```

`build_init_script` substitutes `max_text`, `max_nodes`,
`snapshot_max_depth`, `snapshot_max_nodes`, `dynamic_classes`, and the
incremental record type. The script assigns numeric node ids (`WeakMap`),
serializes the full snapshot once, and pushes rrweb-style records.

## `domtrack/recorder.py`

```python
@dataclass
class CaptureResult:
    json_path: str; csv_path: str; mutation_count: int; event_count: int = 0

class Recorder:
    def __init__(self, config: TracerConfig): ...
    def capture(self) -> dict          # raw browser payload
    def save(self, payload, stamp=None) -> tuple[str, str]
    def run(self) -> CaptureResult
```

`_build_payload` wraps the raw payload into `{meta, version, snapshot,
records, events, count}`. `_install_capture` runs helpers → observer → events.

## `domtrack/storage.py`

```python
CSV_FIELDS: tuple[str, ...]
def ensure_dir(path) -> None
def timestamp() -> str
def save_json(payload, out_dir, stamp=None) -> str
def save_csv(records, out_dir, stamp=None) -> str
```

`save_csv` flattens rrweb-style records or legacy mutation dicts to a fixed
column set (`_flatten`, `_record_to_row`).

## `domtrack/trace.py`

Browser-free format core.

```python
class RecordType: FULL_SNAPSHOT=2, INCREMENTAL_SNAPSHOT=3
class IncrementalSource: MUTATION=0
class NodeType: DOCUMENT=0, DOCUMENT_TYPE=1, ELEMENT=2, TEXT=3, COMMENT=5
VOID_ELEMENTS: frozenset[str]

def is_rrweb_trace(raw: dict) -> bool
def load_raw(path: str) -> dict
def index_snapshot(node) -> dict[int, dict]
def render_node(node) -> str
def node_to_html(node) -> str
def rebuild_html(raw: dict, at_ms: int | None = None) -> str
def to_mutation_dicts(raw: dict) -> list[dict]
def iter_mutations(raw: dict) -> Iterator[dict]

class DomState:
    def __init__(self, snapshot=None): ...
    @classmethod
    def from_trace(cls, raw: dict) -> "DomState": ...
    @classmethod
    def at(cls, raw: dict, at_ms: int | None = None) -> "DomState": ...
    def load_snapshot(self, snapshot) -> None
    def apply(self, record: dict) -> None
    def apply_up_to(self, records, at_ms: int | None = None) -> None
    def to_html(self) -> str
    root: dict | None
    mirror: dict[int, dict]
    clock: int
    stats: dict[str, int]
```

`to_mutation_dicts` is the bridge between capture output and replay input: it
handles both the rrweb layout and the legacy flat `mutations` array.

`DomState` is the browser-free reconstructor: seed it with a `FullSnapshot`
(`from_trace` / `load_snapshot`), feed the incremental records (`apply` /
`apply_up_to`), and serialize the tree at any point with `to_html()`.
`rebuild_html(raw, at_ms)` is the one-call helper; `render_node` is the shared
serializer (also used by `node_to_html`) and additionally renders a document
root by concatenating its children.

## `domtrack/capture/__init__.py`

Re-exports `TracerConfig`, `DEFAULT_DYNAMIC_CLASSES`, `Recorder`,
`CaptureResult`, `build_init_script`, `COLLECT_SCRIPT`, `EVENT_INIT`,
`HELPERS_INIT`, `save_json`, `save_csv`, `timestamp`.

## `domtrack/capture/cli.py`

```python
def build_parser() -> argparse.ArgumentParser
def main(argv=None) -> int
```

Argument parser for `domtrack capture` (see [cli.md](cli.md)).

## `domtrack/capture/__main__.py`

Runs `domtrack.capture.cli.main`.

## `domtrack/html/cli.py`

```python
def build_parser() -> argparse.ArgumentParser
def main(argv=None) -> int
def load_inputs(paths: list[str]) -> dict
```

Browser-free `domtrack html` command: reads a combined trace (or split
snapshot/records files), rebuilds the DOM with `domtrack.trace.DomState` up to
`--at-ms` / `--at-index`, and writes HTML to stdout or `--out`. See
[cli.md](cli.md).

## `domtrack/html/__init__.py`

Re-exports `build_parser` and `main` from `domtrack.html.cli`.

## `domtrack/html/__main__.py`

Runs `domtrack.html.cli.main`.

## `domtrack/replay/__init__.py`

Re-exports the replay public surface: models (`Trace`, `TraceMeta`,
`Mutation`, `Target`, `NodeInfo`), `ElementLocator`, `ThirdPartyDetector`,
`ApplierRegistry`, `ApplyResult`, the filters, the time policies and
`build_policy`, and `Replayer`/`ReplayConfig`/`ReplayStats`.

## `domtrack/replay/models.py`

```python
@dataclass class Target: tag, id, selector           # from_dict
@dataclass class NodeInfo: id, selector, html        # from_dict
@dataclass class Mutation: t, type, target, attribute_name, old_value,
    new_value, added, removed, added_count, removed_count   # from_dict
@dataclass class EventTarget: tag, id, selector, text
@dataclass class Event: t, kind, target, client_x/y, button(s), key, code,
    repeat, value, scroll_x/y, is_document_scroll, delta_x/y, ctrl/shift/alt/meta
@dataclass class TraceMeta: url, viewport, title, captured_at
@dataclass class Trace: meta, mutations, events
    @classmethod load(path) -> Trace
    def group_by_time() -> dict[int, list[Mutation]]
```

`Trace.load` uses `domtrack.trace.load_raw` + `to_mutation_dicts`, so it reads
both formats. `TraceMeta.from_dict` accepts `viewport` as `w`/`h` or
`width`/`height`.

## `domtrack/replay/selectors.py`

```python
def mutation_dynamic_classes(mutation) -> set[str]
def strip_classes(selector, classes) -> str | None
def strip_all_classes(selector) -> str | None
```

Selector rewriting used by the locator: `mutation_dynamic_classes` returns the
classes a single class-attribute mutation added/removed; `strip_classes`
removes only those; `strip_all_classes` removes every `.class` token.

## `domtrack/replay/locators.py`

```python
class ElementLocator:
    def __init__(self, page): ...
    def find(self, mutation) -> tuple[ElementHandle | None, str | None]
    @property
    def strategy_stats(self) -> dict[str, int]
```

Multi-strategy lookup: id → original selector → selector minus the mutated
classes → selector minus all classes (disambiguated by tag).

## `domtrack/replay/appliers.py`

```python
@dataclass class ApplyResult: ok, strategy, error    # success()/failure()
class BaseApplier(ABC): apply(page, mutation) -> ApplyResult
class AttributesApplier(BaseApplier)      # class uses add/remove deltas
class ChildListApplier(BaseApplier)       # remove then add
class CharacterDataApplier(BaseApplier)   # rewrites first text child
class ApplierRegistry:
    def __init__(self, page, locator): ...
    def apply(self, page, mutation) -> ApplyResult
```

## `domtrack/replay/filters.py`

```python
@dataclass class FilterResult: skip, reason     # allow()/block()
class MutationFilter(ABC): check(mutation) -> FilterResult
class ThirdPartyFilter(MutationFilter)
class StructureOnlyFilter(MutationFilter)
class CompositeFilter(MutationFilter)
```

## `domtrack/replay/third_party.py`

```python
class ThirdPartyDetector:
    DEFAULT_HINTS: tuple[str, ...]
    def __init__(self, hints=None): ...
    def is_third_party(self, selector="", element_id="") -> bool
```

Substring match of widget hints (flowchat, goftino, raychat, crisp, intercom,
tawk, livechat, ...) against the selector + id.

## `domtrack/replay/timing.py`

```python
@dataclass class TimeContext: prev_t, curr_t, bucket_has_work
class TimePolicy(ABC): compute_wait(ctx) -> int
class FaithfulTimePolicy(TimePolicy)
class InstantTimePolicy(TimePolicy)
class CappedTimePolicy(TimePolicy)
class SkipAwareTimePolicy(TimePolicy)
def build_policy(name, speed, cap_ms=None) -> TimePolicy
```

## `domtrack/replay/event_replayer.py`

```python
@dataclass class EventReplayStats: applied, failed, failures
class EventReplayer:
    def __init__(self, events, policy): ...
    def replay(self, page) -> EventReplayStats
```

Dispatches by `_on_<kind>`; only `mouse.down`/`mouse.up` for pointer events so
Chromium generates the full gesture.

## `domtrack/replay/replayer.py`

```python
@dataclass class Failure: time_ms, mutation_type, selector, reason
@dataclass class ReplayStats: applied, skipped, failed, skip_reasons, failures
@dataclass class ReplayConfig: ...          # see replay.md
class Replayer:
    def __init__(self, trace, config=None, third_party_detector=None): ...
    @classmethod
    def from_file(cls, trace_path, config=None, third_party_detector=None) -> Replayer
    def run(self) -> ReplayStats
    def print_report(self) -> None
```

## `domtrack/replay/cli.py`

```python
def build_parser() -> argparse.ArgumentParser
def main(argv=None) -> int
```

Argument parser for `domtrack replay` (see [cli.md](cli.md)).

## `domtrack/replay/__main__.py`

Runs `domtrack.replay.cli.main`.
