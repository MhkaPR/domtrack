# domtrack

### **Finds similar structural patterns in the DOM, clusters them by statistical, structural and coordinate features to build columns, and turns them into tables — no browser required.**

**domtrack detects similar structural patterns in a page's DOM — card grids, `<div>` rows, list items, search results, pricing tables. To form the columns, it relies on clustering over statistical, structural and coordinate features, not on `<table>` tags or hardcoded selectors.** The output is a set of typed tables with `{column: value}` rows, and the whole process runs offline on a recorded trace.

To make that possible, domtrack first captures every DOM mutation and user interaction from a live page into a compact, rrweb-style trace. The trace can be folded back into the DOM at any millisecond, so you can extract the table a user saw *after* a slider moved or a filter was applied. The trace can also be replayed in Playwright, rebuilt into HTML, and diffed against another capture.

![python](https://img.shields.io/badge/python-3.10%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)

---

## 🏆 Table extraction: the core of domtrack

Modern web data almost never lives in a `<table>` tag. Product grids, pricing cards, search results, leaderboards, and dashboards are nested `<div>`s, usually rendered by JavaScript and often behind a slider, a filter, or an infinite scroll. That breaks classic scraping tools:

| Problem with classic scraping | What domtrack does instead |
| --- | --- |
| `pandas.read_html` / `BeautifulSoup("table")` only see real `<table>` tags | **Detects any similar structural pattern** — card grids, div rows, list items |
| Needs a live browser and a re-navigation for every run | The trace already holds everything; extraction is pure JSON, offline |
| Sees one frame — misses the state after a slider or filter | Folds the timeline to any millisecond, then extracts |
| Breaks when a class name or wrapper changes | Structural similarity (`sig` / `path_sim`), not selector hardcoding |
| Dumps hidden menus, mobile-only variants, and tooltips into your data | Trace-aware `ever_visible` predicate drops nodes that were never rendered |

The practical upshot: capture a page once, extract its tables as many times as you want, without a browser. Batch a thousand traces, re-run after a tuning change, or ship the extractor into a pipeline where Chromium isn't installed.

### What people use it for

- **Price & product monitoring** — capture a pricing page daily, extract the pricing grid, diff it against yesterday. No scraper maintenance when the layout shifts.
- **E-commerce catalogs** — product listings are card grids, not tables. Discovery treats them as one.
- **Dynamic content** — the table only appears after you pick a filter or drag a slider. Capture the session, fold to `t = 8s`, extract.
- **Competitive / market intelligence** — pull competitor result tables into structured JSON for a warehouse or an LLM knowledge base.
- **Compliance & archival** — prove what a page showed at a specific time, with the data extracted into a machine-readable artifact.
- **RAG pipelines** — turn captured pages into clean `{column: value}` rows instead of noisy HTML.
- **QA on data grids** — assert that a results table's structure didn't change between releases.

---

## 🚀 Install

```bash
pip install domtrack
playwright install chromium     # needed for capture/replay only, not for extraction
```

Requires Python 3.10+. Optional extras:

```bash
pip install 'domtrack[tables]'          # rich + tabulate + openpyxl (pretty display + Excel)
pip install 'domtrack[llm]'             # openai           (LLM table labeling)
pip install 'domtrack[llm-anthropic]'   # anthropic        (LLM table labeling)
pip install 'domtrack[api]'             # fastapi + uvicorn (REST API server)
pip install 'domtrack[all]'             # everything
```

Discovery needs no optional dependency. Excel export uses openpyxl from the `tables` extra; markdown and HTML output use the standard library.

From a checkout:

```bash
pip install -e '.[dev]'
playwright install chromium
pytest
```

The test suite covers the trace format (snapshot indexing, HTML reconstruction, record → mutation conversion, legacy loading) and needs no browser.

---

## ⚡ Quickstart

```bash
# 1. Capture — opens a browser; interact with the page, then press Resume.
domtrack capture https://example.com -o traces

# 2. Extract — pull repeating structures out as tables. No browser.
domtrack tables extract traces/trace_20260101_120000.json -o tables.json --verbose

# 3. Export every discovered table to its own Excel sheet...
domtrack tables show tables.json --format excel -o tables.xlsx

#    ...or inspect in a standalone searchable HTML page.
domtrack tables show tables.json --format html -o tables.html
```

The same flow in Python:

```python
from domtrack import TracerConfig, trace
from domtrack.capture import Recorder
from domtrack.tables import discover_tables, render_tables

# 1. Capture 10 seconds, headless.
cfg = TracerConfig(url="https://example.com", headless=True, duration_ms=10_000)
result = Recorder(cfg).run()

# 2. Extract tabular structures — no browser.
tables = discover_tables(trace.load_raw(result.json_path))

# 3. Render.
render_tables(tables, "markdown", out_path="tables.md")
```

---

## 🔍 How extraction works

No browser, no live DOM, no screenshots. The trace's FullSnapshot plus incremental records are folded in memory with the same core (`domtrack.trace.DomState`) that powers `domtrack html` and `domtrack diff`. The DOM discovery sees is exactly the DOM the records describe, including every intermediate state.

### The fixed algorithm

The pipeline is fixed. Site-specific knowledge lives entirely in four pluggable functions, each with a sensible default, so the common case needs zero configuration.

| Step | What happens |
| --- | --- |
| 1. **Fold** | FullSnapshot + incremental records are folded with `DomState`, then adapted to a light `Node` view (`build_tree`). |
| 2. **Shape** | `shape(X)` = the set of `sig(X, n)` paths under X whose node `n` carries values. This is the structural fingerprint of a subtree. |
| 3. **Cluster** | `cluster_children` groups a parent's visible, uncovered children whose shapes are mutually similar (union-find, `shape_threshold`). **A repeated structural pattern is the table.** |
| 4. **Columns** | Each row's text and attributes become typed atoms. **Atoms are clustered into columns using statistical, structural and coordinate features**, together with weighted value features and path similarity; repeated atoms use order-preserving sequence alignment to form slots. Coverage and nullable-column settings control sparse fields. |
| 5. **Assemble** | Rows × columns project into a table record. The discovered container's whole subtree is marked covered so an ancestor can never re-detect it. Nodes are processed bottom-up; each emits at most one table (its largest valid cluster). |
| 6. **De-duplicate** | Overlapping tables collapse by a data fingerprint of their non-empty cells (`dedupe`), with subset-collapse so a fragment inside a bigger table disappears. |

### Quality guardrails

These aren't knobs you have to turn — they're defaults that make the output usable:

- **`ever_visible` drops never-rendered nodes.** Built from the snapshot plus every attribute flip and childList insert, it filters out hidden menus, `d-none` toggles, mobile-only variants, and `display:none` blocks — so your table is the one a user actually saw.
- **`SKIP_TAGS` strips noise.** `script`, `style`, `svg`, `canvas`, `template`, `head` can never become a column.
- **Links and images become columns.** `values()` reads `href` / `src` / `alt` / `title` / `value` alongside text, so a product row's "View" link and thumbnail URL survive.
- **Row matching blends structure with content.** DOM and content features keep similar records together when classes or wrapper details vary; `shape_threshold` remains the main similarity gate.
- **Order-preserving sequence alignment** separates repeated values at the same structural path into slot columns. Unmatched values are not forced into a slot solely because they share a position.
- **Nullable-column settings preserve sparse fields.** `min_column_coverage_ratio` and `min_column_rows` control when optional columns are retained; missing cells remain null when nullable columns are enabled.
- **Covered lock prevents nested duplicates.** Once a table is found at a node, its whole subtree is off-limits to ancestors.
- **Score ranks by "how table-like".** `columns × √rows × avg_sim` — wide, tall, structurally consistent tables float to the top.

### Customizing what "structure" means

Subclass `DiscoveryPolicy` to teach discovery a different notion of structure. The four methods:

| Method | Signature | Default |
| --- | --- | --- |
| `sig` | `(root, node) -> path \| None` | `(tag, classes)` tuple from root down to node; `None` excludes a subtree |
| `values` | `(node) -> [(type, value)]` | non-empty direct text + `href` / `src` / `alt` / `title` / `value` |
| `path_sim` | `(p1, p2) -> [0, 1]` | weighted LCS over component Jaccard, geometric-mean normalized |
| `is_visible` | `(node) -> bool` | `True` (trace folding installs the `ever_visible` predicate) |

```python
from domtrack import tables
root = tables.build_tree(raw)   # pure tree API, no folding, no visibility filter

class ShopPolicy(tables.DiscoveryPolicy):
    # Teach it that <span class="price"> is not part of the structural
    # signature, so a changing price doesn't break shape clustering.
    def sig(self, root, node):
        path = super().sig(root, node)
        if path is None or (node.tag == "span" and "price" in node.classes):
            return None
        return path

found = tables.discover(root, tables.DiscoveryConfig(policy=ShopPolicy()))
```

The legacy callable fields (`sig=`, `values=`, `path_sim=`, `is_visible=`) still work and override the policy.

### What you get back

```jsonc
{
  "total_candidates": 710,
  "tables_returned": 29,
  "tables": [
    {
      "meta": {
        "parent_tag": "div",
        "parent_classes": ["px-16", "section-body"],
        "row_count": 14,
        "avg_sim": 0.956,
        "columns": 6
      },
      "columns": [
        { "id": "…@href", "label": "type--secondary.href",
          "path": "…", "atype": "href", "coverage": 1.0, "row_count": 14 }
      ],
      "rows": [ { "…@href": "https://…", "_extra": {} } ],
      "score": 12.34
    }
  ]
}
```

`total_candidates` is how many sibling clusters the algorithm considered; `tables_returned` is what survived the `min_rows` / `min_cols` / coverage and dedupe gates. A large gap can mean the structure or coverage thresholds are too strict.

### Three stages, each a library call or a subcommand

| Stage | Subcommand | Browser? | Network? | What it adds |
| --- | --- | --- | --- | --- |
| Discover | `domtrack tables extract` | no | no | The JSON above |
| Label | `domtrack tables label` | no | yes (LLM API) | `title`, `purpose`, `semantic_label` per column |
| Display | `domtrack tables show` | no | no | rich / plain / markdown / Excel / HTML |

```bash
# Discover
domtrack tables extract traces/trace_x.json -o tables.json --verbose
domtrack tables extract snapshot.json records.json       # split inputs
domtrack tables extract traces/trace_x.json --all        # keep overlaps
domtrack tables extract traces/trace_x.json --min-rows 5 --min-cols 3

# Label (turns "type--secondary.href" into "Product URL")
domtrack tables label tables.json -o labeled.json --lang en
domtrack tables label tables.json --provider anthropic --model claude-sonnet-4-5
domtrack tables label tables.json --dry-run              # inspect the payload

# Display
domtrack tables show labeled.json --format markdown -o tables.md
domtrack tables show labeled.json --format excel -o tables.xlsx
domtrack tables show labeled.json --format html -o tables.html
domtrack tables show labeled.json --only 1,3,5 --max-rows 5
```

```python
from domtrack import trace
from domtrack.tables import discover_tables, label_tables, render_tables, DiscoveryConfig

raw = trace.load_raw(".files/trace_x.json")
tables = discover_tables(raw)                                # defaults
tables = discover_tables(raw, DiscoveryConfig(min_rows=4))   # tuned
labeled = label_tables(tables)                               # optional LLM pass
render_tables(labeled, "excel", out_path="tables.xlsx")
```

Excel writes one worksheet per table and includes every row and full cell value. HTML is a standalone page with table navigation, search, and on-screen row controls; the controls do not remove rows from the saved page.

### Multi-document batches

`discover_across_documents` runs the whole pipeline over many traces with per-document state (the covered set never leaks between documents) and returns aggregate stats — useful for scanning a whole crawl at once:

```python
from domtrack.tables import discover_across_documents

multi = discover_across_documents([
    {"id": "pricing", "trace": raw_pricing},
    {"id": "results", "trace": raw_results},
])
print(multi["stats"])   # {"docs": 2, "docs_with_tables": 2, "total_tables": N}
```

Full pipeline, every flag, and every knob: [docs/tables.md](docs/tables.md)

---

## 📦 The trace: what makes browser-free extraction possible

`domtrack capture` writes a single self-contained JSON document:

```json
{
  "meta":     { "url": "...", "title": "...", "viewport": {"w": 1440, "h": 900} },
  "version":  2,
  "snapshot": { "type": 2, "node": { "id": 1, "type": 0, "childNodes": [ ... ] } },
  "records":  [ { "t": 350, "type": 3, "data": { "source": 0, "type": "attributes", ... } } ],
  "events":   [ { "t": 120, "kind": "click", ... } ]
}
```

- **snapshot** — one rrweb FullSnapshot taken at capture start. Every DOM node gets a stable numeric id. That's the baseline.
- **records** — the incremental stream. Each record describes only what changed, referring to nodes by id instead of re-embedding their text or HTML. A slider moving 50 times produces 50 tiny deltas, not 50 full trees.
- **events** — timestamped user interactions on the same clock.
- **t** — every record and event carries a millisecond timestamp, so the file is a replayable timeline, not a static dump.

Why it matters:

- **Small** — the mutation stream is diff-shaped, not tree-shaped.
- **Diff-friendly** — two traces of the same page compare structurally.
- **Directly replayable** — Playwright consumes the timeline as-is.
- **Version-tolerant** — traces from domtrack 1.x (flat `mutations` array) still load and replay; the loader auto-detects the old layout.

Alongside the JSON, capture also writes a flattened CSV (`t, type, target.tag, target.id, target.selector, attributeName, oldValue, newValue, addedCount, removedCount`) for quick spreadsheet triage.

Capture is configurable through `TracerConfig` — `target_selector` to observe just a subtree, `snapshot_max_depth` / `snapshot_max_nodes` to bound the baseline, `max_text` / `max_nodes` to cap per-record payload, and `dynamic_classes` to keep volatile class names out of generated selectors.

Field-by-field contract: [docs/trace-format.md](docs/trace-format.md) · Capture reference: [docs/capture.md](docs/capture.md)

---

## 🧩 Everything domtrack does

| # | Feature | What you get |
| --- | --- | --- |
| 1 | **Extract tables** | Discover, optionally LLM-label, and render repeating structures from a trace as HTML or multi-sheet Excel. |
| 2 | Capture | Record DOM mutations + user events from a live page into an rrweb-style trace (JSON + CSV). |
| 3 | Replay | Feed that trace back into Playwright as real interactions, at adjustable speed. |
| 4 | Rebuild HTML | Fold snapshot + records into HTML at any `t`, with no browser. |
| 5 | Diff | Compare the content of two captures — element text plus data-bearing attributes. |

Plus a convenience on top: `domtrack compare` runs replay → record → diff in one call.

```bash
# Replay a session as real interactions
domtrack replay traces/trace_20260101_120000.json

# Replay at 2x speed from Python
python -c "from domtrack.replay import Replayer, ReplayConfig; \
Replayer.from_file('traces/trace_20260101_120000.json', ReplayConfig(speed=2.0)).run()"
```

---

## 🌐 REST API

Every command is also available over HTTP. Install the extra and start the server — it runs a real ASGI (Uvicorn) server and serves interactive docs:

```bash
pip install 'domtrack[api]'
python -m domtrack serve            # http://127.0.0.1:8000  (docs at /docs)
```

Endpoints mirror the CLI one-to-one:

```bash
curl -X POST http://127.0.0.1:8000/tables/extract \
  -H 'Content-Type: application/json' \
  -d '{"trace":{"path":"trace_20260101_120000.json"}}'

curl -X POST http://127.0.0.1:8000/capture \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com","duration_s":5,"headless":true}'
```

| Endpoint | CLI equivalent |
| --- | --- |
| `POST /tables/extract` | `domtrack tables extract <trace.json>` |
| `POST /tables/label` | `domtrack tables label <tables.json>` |
| `POST /tables/show` | `domtrack tables show <tables.json>` |
| `POST /capture` | `domtrack capture <url>` |
| `POST /replay` | `domtrack replay <trace.json>` |
| `POST /compare` | `domtrack compare <trace.json>` |
| `POST /html` | `domtrack html <trace.json>` |
| `POST /diff` | `domtrack diff <A> <B>` |
| `GET /health`, `GET /traces` | — |

Traces are referenced by server-side path or passed inline. Full reference: [docs/api.md](docs/api.md).

---

## 🤖 Built to be read by LLMs

This repository is documented specifically so language models can read it and generate correct code in a single pass. Every doc file is self-contained, with stable headings and copy-paste examples — no cross-file jumping required.

Read these two first, before generating code against this package:

1. [docs/README.md](docs/README.md) — LLM-oriented index: architecture, module map, public API, recipes.
2. [docs/trace-format.md](docs/trace-format.md) — the machine-readable trace contract.

---

## 📚 Documentation

All docs live in `docs/`, written to be read in full by an LLM in a single pass.

| Document | Contents |
| --- | --- |
| [docs/tables.md](docs/tables.md) | Table discovery, LLM labeling, and rendering |
| [docs/README.md](docs/README.md) | LLM-oriented index: architecture, module map, API summary, recipes |
| [docs/architecture.md](docs/architecture.md) | How capture and replay fit together, data flow |
| [docs/trace-format.md](docs/trace-format.md) | The trace JSON contract (rrweb-style snapshot + records) |
| [docs/capture.md](docs/capture.md) | `domtrack.capture`, `TracerConfig`, `Recorder`, capture JS |
| [docs/replay.md](docs/replay.md) | `domtrack.replay`, `Replayer`, `ReplayConfig`, modes, filters, timing, replay recording, compare |
| [docs/cli.md](docs/cli.md) | Every command-line flag for all subcommands |
| [docs/api.md](docs/api.md) | REST API: run the server (`python -m domtrack serve`), endpoints, errors |
| [docs/modules.md](docs/modules.md) | Per-module reference for every file in the package |
| [docs/examples.md](docs/examples.md) | Copy-paste recipes (record, replay, clean mode, custom policies) |

---

## 🛠 Development

```bash
pip install -e '.[dev]'
pytest
```

The test suite covers the trace format (snapshot indexing, HTML reconstruction, record → mutation conversion, and legacy loading) and does not need a browser.

---

## License

MIT — see [LICENSE](LICENSE).
