# Module reference

One section per file in the `domtrack` package. Signatures are the public
surface; private helpers (leading `_`) are omitted unless useful.

## `domtrack/__init__.py`

Top-level package. Re-exports `TracerConfig`, the trace constants
(`RecordType`, `IncrementalSource`, `NodeType`), and trace helpers
(`load_raw`, `is_rrweb_trace`, `index_snapshot`, `node_to_html`,
`to_mutation_dicts`, `DomState`, `render_node`, `rebuild_html`). Provides lazy
access to the subpackages `domtrack.capture`, `domtrack.replay`,
`domtrack.html`, `domtrack.diff`, `domtrack.compare`, and `domtrack.tables` via PEP 562
`__getattr__` so importing the package does not import Playwright.
`__version__ = "2.0.0"`.

## `domtrack/__main__.py`

Runs `domtrack.cli.main` (`python -m domtrack`).

## `domtrack/cli.py`

Unified dispatcher.

```python
main(argv: list[str] | None = None) -> int
```

`argv[0]` selects `capture`, `replay`, `html`, `diff`, `compare`, or `tables`;
the rest is passed to the matching subcommand's `main`. No args (or
`-h`/`--help`/`help`) prints usage.

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

class CaptureSession:
    def __init__(self, config: TracerConfig): ...
    def install(self, page) -> None            # helpers + observer + listeners
    def collect(self, page) -> dict            # raw browser payload
    def capture(self, page) -> dict            # install + collect
    @staticmethod
    def build_payload(raw) -> dict             # rrweb-style layout
    def save(self, payload, out_dir=None, stamp=None) -> tuple[str, str]
    def save_result(self, payload, out_dir=None, stamp=None) -> CaptureResult

class Recorder:
    def __init__(self, config: TracerConfig): ...
    def capture(self) -> dict          # raw browser payload
    def save(self, payload, stamp=None) -> tuple[str, str]
    def run(self) -> CaptureResult
```

`CaptureSession` is the shared capture core, used both by `Recorder` (human
capture) and by `domtrack.replay.recording.ReplayRecorder` (recording during a
replay). `install()` runs helpers → observer → events; `collect()` disconnects
the observer and returns the payload; `build_payload()` wraps it into
`{meta, version, snapshot, records, events, count}`. `Recorder` delegates to a
session, and its `_build_payload`/`_install_capture` are thin shims for
back-compat.

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
def load_trace_inputs(paths: list[str]) -> dict
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

`load_trace_inputs(paths)` accepts either one combined trace file or a
`[snapshot.json, records.json]` pair and is shared by the `html` and `tables`
CLIs.

## `domtrack/capture/__init__.py`

Re-exports `TracerConfig`, `DEFAULT_DYNAMIC_CLASSES`, `Recorder`,
`CaptureResult`, `CaptureSession`, `build_init_script`, `COLLECT_SCRIPT`,
`EVENT_INIT`, `HELPERS_INIT`, `save_json`, `save_csv`, `timestamp`.

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

## `domtrack/diff/core.py`

Browser-free content diff. Each side is folded with
`domtrack.trace.DomState`, reduced to a path-keyed content view, and compared.

```python
NON_DATA_ATTRS: frozenset[str]
VOLATILE_ATTR_NAME: tuple[re.Pattern, ...]
VOLATILE_TEXT: tuple[re.Pattern, ...]

def is_volatile_attr(name) -> bool
def is_volatile_text(text) -> bool
def iter_nodes(node, path="", counters=None) -> Iterator[(node, path, tag)]
def direct_text(node) -> str

@dataclass class ContentEntry: node_id, tag, text, attrs
@dataclass class Change: path, tag, old_text, new_text, attrs  (+ text_changed)
@dataclass class DiffResult:
    a, b, added, removed, changed
    summary: dict           # a_nodes, b_nodes, added, removed, changed
    to_dict() -> dict       # JSON-ready

def content_view(root) -> dict[str, ContentEntry]
def view_of(raw, at_ms=None, apply_records=True) -> dict[str, ContentEntry]
def diff_views(a, b) -> DiffResult
def diff_traces(raw_a, raw_b, at_ms_a=None, at_ms_b=None, apply_records=True) -> DiffResult
def format_report(result, out) -> None
```

The path scheme is a slash-joined chain of `tag[index]` segments, e.g.
`/html[0]/body[0]/ul[0]/li[2]/h2[0]`, where the index counts same-tag siblings.
Only elements with non-volatile direct text survive into the view;
`attrs` keeps the data-bearing attributes after `NON_DATA_ATTRS` and
`VOLATILE_ATTR_NAME` are filtered out.

## `domtrack/diff/cli.py`

```python
def build_parser() -> argparse.ArgumentParser
def main(argv=None) -> int
```

Browser-free `domtrack diff` command (see [cli.md](cli.md)).

## `domtrack/diff/__init__.py`

Re-exports the `core` public surface.

## `domtrack/diff/__main__.py`

Runs `domtrack.diff.cli.main`.

## `domtrack/compare/cli.py`

```python
def build_parser() -> argparse.ArgumentParser
def main(argv=None) -> int
```

`domtrack compare` command: replays a trace, records the run, and prints the
content diff with the original (or `--json`). Thin front-end over
`domtrack.replay.compare.ReplayComparer`. See [cli.md](cli.md).

## `domtrack/compare/__init__.py`

Re-exports `build_parser` and `main` from `domtrack.compare.cli`.

## `domtrack/compare/__main__.py`

Runs `domtrack.compare.cli.main`.

## `domtrack/tables/__init__.py`

Re-exports the tables public surface: `Node`, `Atom`, `Document`, `Interfaces`,
`DiscoveryConfig`, `build_tree`, `discover`, `discover_across_documents`,
`discover_tables`, `LabelConfig`, `label_tables`, `RenderOptions`, and
`render_tables`. The provider SDKs
(`openai` / `anthropic`) and display helpers (`rich` / `tabulate`) are imported
lazily by the underlying modules.

## `domtrack/tables/discovery.py`

Browser-free table discovery over a folded trace. The algorithm is fixed; four
pluggable interfaces (`sig`, `values`, `path_sim`, `is_visible`) carry every
piece of site-specific knowledge and are supplied through `DiscoveryConfig`.

```python
SKIP_TAGS: frozenset[str]
VALUE_ATTRS: tuple[tuple[str, str], ...]

def clean(text: str | None) -> str

@dataclass
class Node:
    id: int; tag: str; attrs: dict; text: str; children: list; parent: Node | None
    @property
    def classes(self) -> frozenset[str]: ...
    @classmethod
    def from_serialized(cls, raw: dict | None) -> "Node" | None: ...

def build_tree(raw: dict) -> Node
def skip_subtree(node: Node) -> bool

# trace-aware visibility helper + ready-made ``is_visible``
def compute_ever_visible(initial_root, records, final_root=None) -> set[int]
def ever_visible_predicate(ids) -> Callable[[Node], bool]

@dataclass
class Atom:
    path: tuple; atype: str; value: str

# pluggable interfaces and their defaults
def default_sig(root: Node, node: Node) -> tuple | None
def default_values(node: Node) -> list[tuple[str, str]]
def default_path_sim(p1, p2) -> float          # alias: path_similarity
def default_is_visible(node: Node) -> bool
def jaccard(a, b) -> float
def component_similarity(c1, c2) -> float

@dataclass(frozen=True)
class Interfaces:
    sig; values; path_sim; is_visible

# core algorithm (all phases)
def shape(root: Node, interfaces) -> frozenset
def shape_similarity(s1, s2, path_sim) -> float
def group_similarity(rows, interfaces) -> float
def cluster_children(parent, config, interfaces, covered) -> list[list[Node]]
def extract_columns(rows, config, interfaces) -> list[dict]
def build_table(parent, rows, columns, avg_sim) -> dict
def mark_covered(node, covered) -> None
def discover(root: Node, config=None) -> list[dict]

def best_class(classes) -> str
def path_str(path) -> str
def column_id(column) -> str
def column_label(column) -> str

def row_identity(atoms) -> tuple[str, str]
def table_fingerprint(table) -> frozenset
def fingerprint_similarity(a, b) -> float
def is_subset_of(a, b, ratio: float = 0.85) -> bool
def dedupe(tables, threshold) -> list[dict]

@dataclass
class DiscoveryConfig:
    min_rows: int = 3
    min_cols: int = 2
    min_coverage_ratio: float = 0.5
    shape_threshold: float = 0.6
    path_threshold: float = 0.6
    dedupe: bool = True
    dedupe_threshold: float = 0.8
    sig / values / path_sim / is_visible: Callable | None = None
    ever_visible: set[int] | None = None
    threshold: float | None = None   # legacy alias for both thresholds
    def interfaces(self) -> Interfaces: ...

def discover_tables(raw: dict, config: DiscoveryConfig | None = None) -> dict

@dataclass
class Document:
    id: str; root: Node

def discover_across_documents(documents, config=None) -> dict
```

`build_tree` folds the trace with `DomState` and adapts it to a `Node` tree.
`cluster_children` groups siblings by `shape`; `extract_columns` clusters
per-row atoms by `path_sim`; `build_table` assembles the record; `dedupe`
collapses overlapping tables by data fingerprint. `discover` runs the algorithm
on a single tree; `discover_tables` folds a trace first (auto-populating the
trace-aware `is_visible`) and returns
`{"total_candidates", "tables_returned", "tables"}`;
`discover_across_documents` runs the per-document driver and returns
`{"stats", "all_tables"}`.

## `domtrack/tables/labeling.py`

LLM semantic labeling of extracted tables.

```python
def truncate(value, limit: int = 120)
def compact_table(index, table, max_samples=3, max_rows=3, max_chars=120) -> dict
def build_payload(tables, start_index) -> dict

COLUMN_LABEL_SCHEMA, TABLE_LABEL_SCHEMA, RESPONSE_SCHEMA, JSON_SCHEMA_WRAPPER
SYSTEM_PROMPT: str

def call_openai(api_key, model, base_url, payload, lang, use_strict=True) -> dict
def call_anthropic(api_key, model, payload, lang) -> dict
def run_llm(tables, config) -> list[dict]
def merge_labels(data, labels) -> dict

DEFAULT_MODELS: dict[str, str]
API_KEY_ENV: dict[str, str]

@dataclass
class LabelConfig:
    provider: str = "openai"
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    lang: str = "fa"
    batch_size: int = 25
    dry_run: bool = False

def resolve_api_key(config) -> str | None
def label_tables(data, config=None) -> dict
```

`label_tables` batches the compact payloads through the chosen provider and
merges `title` / `purpose` / per-column `semantic_label` back into a deep copy
of `data`. With `dry_run` it prints the payloads and returns `data` unchanged.
Provider SDKs are imported inside `call_openai` / `call_anthropic`.

## `domtrack/tables/render.py`

Render labeled tables as rich / plain / markdown / csv.

```python
@dataclass
class RenderOptions:
    only: set[int] | None = None
    max_rows: int = 0
    max_chars: int = 80

def get_cell(row, column_id) -> str
def column_headers(table) -> list[tuple[str, str]]
def truncate(value, limit) -> str
def iter_tables(data, only=None) -> Iterator[tuple[int, dict]]

def render_rich(data, options) -> None
def render_plain(data, options) -> None
def render_markdown(data, options, out=sys.stdout) -> None
def render_csv(data, options, out) -> None
def render(data, fmt, options=None, out_path=None) -> None
```

`column_headers` heads each column with `semantic_label` ← `label` ← id.
`render` dispatches on `fmt` and writes to `out_path` for `markdown`/`csv`;
`rich` falls back to `plain` when `rich` is not installed.

## `domtrack/tables/cli.py`

`domtrack tables <extract|label|show>` argument parser and `main`.

```python
def build_parser() -> argparse.ArgumentParser
def main(argv: list[str] | None = None) -> int
```

Thin front-end over `discovery`, `labeling`, and `render`.

## `domtrack/tables/__main__.py`

Runs `domtrack.tables.cli.main`.

## `domtrack/replay/__init__.py`

Re-exports the replay public surface: models (`Trace`, `TraceMeta`,
`Mutation`, `Target`, `NodeInfo`), `ElementLocator`, `ThirdPartyDetector`,
`ApplierRegistry`, `ApplyResult`, the filters, the time policies and
`build_policy`, `Replayer`/`ReplayConfig`/`ReplayStats`, and the recording +
compare helpers (`RecordingConfig`, `ReplayRecorder`, `CompareResult`,
`ReplayComparer`, `compare_replay`, `compare_traces`).

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
    recorder: ReplayRecorder | None          # set when config.recording.enabled
    record_result: CaptureResult | None      # the recorded trace, after run()
```

## `domtrack/replay/recording.py`

```python
@dataclass class RecordingConfig:
    enabled, output_dir, target_selector, capture_events,
    max_text, max_nodes, snapshot_max_depth, snapshot_max_nodes, tracer
    def to_tracer_config(self, url, viewport=None) -> TracerConfig

class ReplayRecorder:
    def __init__(self, config=None): ...
    def start(self, page, url, viewport=None) -> None
    def finish(self, page) -> dict
    def save(self, payload, stamp=None) -> CaptureResult
    def finish_and_save(self, page, stamp=None) -> CaptureResult
```

Installs the shared capture scripts (`CaptureSession`) on a replay page before
the replay runs and collects afterwards, producing an ordinary trace file. See
[replay.md](replay.md#recording-during-a-replay).

## `domtrack/replay/compare.py`

```python
@dataclass class CompareResult:
    before_path, after_path, after_csv_path, stats, diff,
    replay_mutations, recorded_mutations, recorded_events
    summary: dict

def compare_traces(before_path, after_path) -> DiffResult
class ReplayComparer:
    def __init__(self, before_path, replay_config=None, recording=None,
                 third_party_detector=None): ...
    def run(self) -> CompareResult
def compare_replay(before_path, replay_config=None, recording=None,
                   third_party_detector=None) -> CompareResult
```

The replay → record → diff automation loop. See
[replay.md](replay.md#replay-record-diff-compare).

## `domtrack/replay/cli.py`

```python
def build_parser() -> argparse.ArgumentParser
def main(argv=None) -> int
```

Argument parser for `domtrack replay` (see [cli.md](cli.md)).

## `domtrack/replay/__main__.py`

Runs `domtrack.replay.cli.main`.
