domtrack

Record what a page does. Replay it. Extract the tables — no browser required.

domtrack captures every DOM mutation and every user interaction from a live page into a
compact, rrweb-style trace. That trace can be replayed in a fresh Playwright browser,
folded back into HTML at any millisecond, diffed against another capture, and — most
usefully — mined for tabular data, even when the "table" is a grid of nested <div>s
that only exists after a slider moved. No browser needed for the extraction step.

https://img.shields.io/badge/python-3.10%2B-blue
https://img.shields.io/badge/license-MIT-green

---

🚀 Install

```bash
pip install domtrack
playwright install chromium
```

That's it — Python 3.10+.

Optional extras, only if you need them:

```bash
pip install 'domtrack[tables]'          # rich + tabulate  (pretty display)
pip install 'domtrack[llm]'             # openai           (LLM table labeling)
pip install 'domtrack[llm-anthropic]'   # anthropic        (LLM table labeling)
pip install 'domtrack[all]'             # everything
```

Discovery and CSV/markdown rendering need no optional dependency.

From a checkout:

```bash
pip install -e '.[dev]'
playwright install chromium
pytest
```

The test suite covers the trace format (snapshot indexing, HTML reconstruction,
record → mutation conversion, legacy loading) and needs no browser.

---

⚡ Quickstart

```bash
# 1. Capture — opens a browser; interact with the page, then press Resume.
domtrack capture https://example.com -o traces

# 2. Replay — reproduce the session as real interactions.
domtrack replay traces/trace_20260101_120000.json

# 3. Extract — pull tables out of the trace, no browser.
domtrack tables extract traces/trace_20260101_120000.json -o tables.json --verbose
```

The same three steps in Python:

```python
from domtrack import TracerConfig, trace
from domtrack.capture import Recorder
from domtrack.replay import Replayer, ReplayConfig
from domtrack.tables import discover_tables, render_tables

# 1. Capture 10 seconds, headless.
cfg = TracerConfig(url="https://example.com", headless=True, duration_ms=10_000)
result = Recorder(cfg).run()

# 2. Replay at 2x speed.
Replayer.from_file(result.json_path, ReplayConfig(speed=2.0)).run()

# 3. Extract tabular structures — no browser.
tables = discover_tables(trace.load_raw(result.json_path))
render_tables(tables, "markdown", out_path="tables.md")
```

---

🧩 The five things domtrack does

# Feature What you get
1 Capture Record DOM mutations + user events from a live page into an rrweb-style trace (JSON + CSV).
2 Replay Feed that trace back into Playwright as real interactions, at adjustable speed.
3 Rebuild HTML Fold snapshot + records into HTML at any t, with no browser.
4 Diff Compare the content of two captures — element text plus data-bearing attributes.
5 Extract tables Discover, optionally LLM-label, and render tabular structures from a trace.

Plus a convenience on top: domtrack compare runs replay → record → diff in one call.

---

📦 Deep dive: the trace artifact

domtrack capture writes a single self-contained JSON document:

```json
{
  "meta":     { "url": "...", "title": "...", "viewport": {"w": 1440, "h": 900} },
  "version":  2,
  "snapshot": { "type": 2, "node": { "id": 1, "type": 0, "childNodes": [ ... ] } },
  "records":  [ { "t": 350, "type": 3, "data": { "source": 0, "type": "attributes", ... } } ],
  "events":   [ { "t": 120, "kind": "click", ... } ]
}
```

How it's built — and why it stays small:

· snapshot — one rrweb FullSnapshot taken at capture start. Every DOM node
  gets a stable numeric id. That's the baseline.
· records — the incremental stream. Each record describes only what changed,
  referring to nodes by id instead of re-embedding their text or HTML. A slider moving
  50 times produces 50 tiny deltas, not 50 full trees.
· events — timestamped user interactions on the same clock.
· t — every record and event carries a millisecond timestamp, so the file is a
  replayable timeline, not a static dump.

Why it matters:

· Small — the mutation stream is diff-shaped, not tree-shaped.
· Diff-friendly — two traces of the same page compare structurally.
· Directly replayable — Playwright consumes the timeline as-is.
· Version-tolerant — traces from domtrack 1.x (flat "mutations" array) still load
  and replay; the loader auto-detects the old layout.

Alongside the JSON, capture also writes a flattened CSV
(t, type, target.tag, target.id, target.selector, attributeName, oldValue, newValue,
addedCount, removedCount) for quick spreadsheet triage.

Capture is configurable through TracerConfig — target_selector to observe just a
subtree, snapshot_max_depth / snapshot_max_nodes to bound the baseline,
max_text / max_nodes to cap per-record payload, and dynamic_classes to keep volatile
class names out of generated selectors.

Field-by-field contract: docs/trace-format.md ·
Capture reference: docs/capture.md

---

🏆 The headline feature: browser-free table extraction

Modern web data almost never lives in a <table> tag. Product grids, pricing cards,
search results, leaderboards, and dashboards are nested <div>s — usually rendered by
JavaScript, often behind a slider, a filter, or an infinite scroll. That breaks every
classic scraping tool:

Problem with classic scraping What domtrack does instead
pandas.read_html / BeautifulSoup("table") only sees real <table> tags Works on any repeated sibling shape — card grids, div rows, list items
Needs a live browser and a re-navigation for every run The trace already captured everything; extraction is pure JSON, offline
Sees one frame — misses what the page looked like after a slider/filter Folds the timeline to any millisecond, then extracts
Breaks whenever a class name or wrapper changes Structural similarity (sig / path_sim), not selector hardcoding
Dumps hidden menus, mobile-only variants, tooltips into your data Trace-aware ever_visible predicate drops nodes that were never rendered

The practical upshot: capture a page once, extract its tables as many times as you want,
forever, without a browser. Batch a thousand traces, re-run after a tuning change, ship
the extractor into a pipeline where Chromium isn't installed.

What people actually use it for

· Price & product monitoring — capture a pricing page daily, extract the pricing grid,
  diff it against yesterday. No scraper maintenance when the layout shifts.
· E-commerce catalogs — product listings are card grids, not tables. Discovery treats
  them as one.
· Dynamic content — the table only appears after you pick a filter or drag a slider.
  Capture the session, fold to t = 8s, extract. A screenshot scraper gets nothing.
· Competitive / market intelligence — capture competitor pages, pull their result
  tables into structured JSON for a warehouse or an LLM knowledge base.
· Compliance & archival — prove what a page showed at a specific time, with the data
  extracted into a machine-readable artifact.
· RAG pipelines — turn captured pages into clean {column: value} rows instead of
  noisy HTML for the model to re-parse.
· QA on data grids — assert that a results table's structure didn't change between
  releases, using the diff + discovery pair.

---

🔍 How the extraction actually works

No browser, no live DOM, no screenshots. The trace's FullSnapshot plus incremental
records are folded in memory with the same core (domtrack.trace.DomState) that powers
domtrack html and domtrack diff. The DOM discovery sees is exactly the DOM the
records describe — including every intermediate state.

The fixed algorithm

The pipeline is fixed. Site-specific knowledge lives entirely in four pluggable
functions, each with a sensible default, so the common case needs zero configuration.

Step What happens
1. Fold FullSnapshot + incremental records are folded with DomState, then adapted to a light Node view (build_tree).
2. Shape shape(X) = the set of sig(X, n) paths under X whose node n carries values. This is the structural fingerprint of a subtree.
3. Cluster cluster_children groups a parent's visible, uncovered children whose shapes are mutually similar (union-find, shape_threshold). Sibling repetition is the table.
4. Columns Per-row atoms are clustered by path_sim (only the same atype compares). A column must cover at least min_coverage_ratio of rows. Multi-atom columns split into slot sub-columns so no value is lost.
5. Assemble Rows × columns project into a table record. The discovered container's whole subtree is marked covered so an ancestor can never re-detect it. Nodes are processed bottom-up; each emits at most one table (its largest valid cluster).
6. De-duplicate Overlapping tables collapse by a data fingerprint of their non-empty cells (dedupe), with subset-collapse so a fragment inside a bigger table disappears.

Quality guardrails that come from the algorithm

These aren't knobs you have to turn — they're defaults that make the output usable:

· ever_visible drops never-rendered nodes. Built from the snapshot plus every
  attribute flip and childList insert, it filters out hidden menus, d-none toggles,
  mobile-only variants, and display:none blocks — so your table is the one a user
  actually saw, not the union of every state the DOM ever had.
· SKIP_TAGS strips noise. script, style, svg, canvas, template, head —
  none of them can ever become a column.
· Links and images become columns. values() reads href / src / alt / title /
  value alongside text, so a product row's "View" link and thumbnail URL survive.
· min_coverage_ratio (default 0.5) tolerates outliers. One malformed row can't kill
  a column; a column that only appears in half the rows is still kept.
· slot sub-columns preserve repeats. Three tags per product become tags#0,
  tags#1, tags#2 — not one squashed string.
· covered lock prevents nested duplicates. Once a table is found at a node, its whole
  subtree is off-limits to ancestors, so you don't get the same data at three zoom levels.
· score ranks by "how table-like". columns × √rows × avg_sim — wide, tall,
  structurally consistent tables float to the top.

The four pluggable interfaces

Interface Signature Default
sig (root, node) -> path \| None (tag, classes) tuple from root down to node; None excludes a subtree
values (node) -> [(type, value)] non-empty direct text + href / src / alt / title / value
path_sim (p1, p2) -> [0, 1] mean component-wise Jaccard × length ratio
is_visible (node) -> bool True (trace folding installs the ever_visible predicate)

path is any hashable comparable with path_sim — swap in your own tuple or schema to
teach discovery a different notion of "same structure".

```python
from domtrack import tables
root = tables.build_tree(raw)   # pure tree API, no folding, no visibility filter

# Teach it that <span class="price"> is not part of the structural signature,
# so a changing price doesn't break shape clustering.
def sig(root, node):
    path = tables.default_sig(root, node)
    if path is None or (node.tag == "span" and "price" in node.classes):
        return None
    return path

found = tables.discover(root, tables.DiscoveryConfig(sig=sig))
```

What you get back

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

total_candidates tells you how many sibling clusters the algorithm considered;
tables_returned is what survived the min_rows / min_cols / min_coverage /
dedupe gates. A large gap is a signal to lower shape_threshold or min_coverage_ratio.

Three stages, each a library call or a subcommand

Stage Subcommand Browser? Network? What it adds
Discover domtrack tables extract no no The JSON above
Label domtrack tables label no yes (LLM API) title, purpose, semantic_label per column
Display domtrack tables show no no rich / plain / markdown / csv

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
domtrack tables show labeled.json --format csv -o tables.csv
domtrack tables show labeled.json --only 1,3,5 --max-rows 5
```

```python
from domtrack import trace
from domtrack.tables import discover_tables, label_tables, render_tables, DiscoveryConfig

raw     = trace.load_raw("traces/trace_x.json")
tables  = discover_tables(raw)                                  # defaults
tables  = discover_tables(raw, DiscoveryConfig(min_rows=4))     # tuned
labeled = label_tables(tables)                                  # optional LLM pass
render_tables(labeled, "markdown", out_path="tables.md")
```

Multi-document batches

discover_across_documents runs the whole pipeline over many traces with per-document
state (the covered set never leaks between documents) and returns aggregate stats —
useful for scanning a whole crawl at once:

```python
from domtrack.tables import discover_across_documents

multi = discover_across_documents([
    {"id": "pricing", "trace": raw_pricing},
    {"id": "results", "trace": raw_results},
])
print(multi["stats"])   # {"docs": 2, "docs_with_tables": 2, "total_tables": N}
```

Full pipeline, every flag, and every knob: docs/tables.md

---

🤖 Built to be read by LLMs

This repository is documented specifically so language models can read it and generate
correct code in a single pass. Every doc file is self-contained, with stable headings
and copy-paste examples — no cross-file jumping required.

Read these two first, before generating code against this package:

1. docs/README.md — LLM-oriented index: architecture, module map,
   public API, recipes.
2. docs/trace-format.md — the machine-readable trace contract.

---

📚 Documentation

All docs live in docs/, written to be read in full by an LLM in a single pass.

Document Contents
docs/README.md LLM-oriented index: architecture, module map, API summary, recipes
docs/architecture.md How capture and replay fit together, data flow
docs/trace-format.md The trace JSON contract (rrweb-style snapshot + records)
docs/capture.md domtrack.capture, TracerConfig, Recorder, capture JS
docs/replay.md domtrack.replay, Replayer, ReplayConfig, modes, filters, timing, replay recording, compare
docs/cli.md Every command-line flag for all subcommands
docs/tables.md Table discovery, LLM labeling, and rendering
docs/modules.md Per-module reference for every file in the package
docs/examples.md Copy-paste recipes (record, replay, clean mode, custom policies)

---

🛠 Development

```bash
pip install -e '.[dev]'
pytest
```

The test suite covers the trace format (snapshot indexing, HTML reconstruction,
record → mutation conversion, and legacy loading) and does not need a browser.

---

License

MIT — see LICENSE.
