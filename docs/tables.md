# Tables (`domtrack.tables`)

`domtrack tables` discovers, labels, and displays the **tabular structures** in
a capture — product grids, pricing tables, result lists, card layouts — with no
browser and no duplicate code. It reuses the same folding core as
`domtrack html` and `domtrack diff` (`domtrack.trace.DomState`), so the DOM it
sees is exactly the DOM the records describe.

Three stages, each usable as a library call or a subcommand:

| Stage | Subcommand | Module | Browser? | Network? |
| --- | --- | --- | --- | --- |
| Discover | `domtrack tables extract` | `domtrack.tables.discovery` | no | no |
| Label | `domtrack tables label` | `domtrack.tables.labeling` | no | yes (LLM API) |
| Display | `domtrack tables show` | `domtrack.tables.render` | no | no |

## How discovery works

The algorithm is **fixed**. Everything site-specific lives in four *pluggable
interfaces* supplied by the caller (`DiscoveryConfig.sig` / `values` /
`path_sim` / `is_visible`); each has a sensible default, so the common case
needs no configuration.

1. **Fold** — the trace's `FullSnapshot` plus incremental records are folded
   with `DomState` and adapted to a light `Node` view (`build_tree`).
2. **Shape** — `shape(X)` is the set of `sig(X, n)` paths under `X` whose node
   `n` carries `values`. Siblings whose shapes are mutually similar cluster
   together (union-find, `cluster_children`) — the signature of a table, list,
   or card grid.
3. **Columns** — per-row atoms are clustered by `path_sim` (only the same
   `atype` compares); a column must cover at least `min_coverage_ratio` of the
   rows. A column built from several same-path atoms per row splits into
   `slot` sub-columns so every value survives (`extract_columns`).
4. **Assemble** — the row cluster and columns project into a table record
   (`build_table`); the discovered container's whole subtree is then marked
   `covered`, so an ancestor can never re-detect it. Nodes are processed
   bottom-up and each node emits at most one table (`discover`).
5. **De-duplicate** — overlapping tables collapse by a data fingerprint of
   their non-empty cells (`dedupe`).

### The pluggable interfaces

| Interface | Signature | Default |
| --- | --- | --- |
| `sig` | `(root, node) -> path \| None` | `(tag, classes)` tuple from `root` down to `node`; `None` excludes a subtree |
| `values` | `(node) -> [(type, value)]` | non-empty direct text + `href`/`src`/`alt`/`title`/`value` |
| `path_sim` | `(p1, p2) -> [0, 1]` | mean component-wise Jaccard × length ratio |
| `is_visible` | `(node) -> bool` | `True` (trace folding installs an ever-visible predicate) |

`path` is any hashable value comparable with `path_sim` — swap in your own
tuple/schema to teach discovery a different notion of "same structure".

The result is JSON:

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
        { "id": "…@href", "label": "type--secondary.href", "path": "…",
          "atype": "href", "coverage": 1.0 }
      ],
      "rows": [ { "…@href": "https://…", "_extra": {} } ],
      "score": 12.34
    }
  ]
}
```

### Library

```python
from domtrack import trace
from domtrack.tables import (
    discover_tables, discover, discover_across_documents, DiscoveryConfig,
)

raw = trace.load_raw("traces/trace_x.json")
result = discover_tables(raw)                       # defaults
result = discover_tables(raw, DiscoveryConfig(min_rows=4, dedupe=False))

for table in result["tables"]:
    print(table["meta"]["row_count"], table["meta"]["columns"])

# Pure tree API — no trace folding, no visibility filter:
from domtrack import tables
root = tables.build_tree(raw)
for table in discover(root, DiscoveryConfig()):
    print(table["meta"])

# Many documents, per-document state (no cross-document memory):
multi = discover_across_documents([{"id": "a", "trace": raw}])
print(multi["stats"])          # {"docs": 1, "docs_with_tables": 1, "total_tables": N}

# Teach it your own structure: exclude price nodes from the signature.
def sig(root, node):
    path = tables.default_sig(root, node)
    if path is None or (node.tag == "span" and "price" in node.classes):
        return None
    return path

tables_ = discover(root, DiscoveryConfig(sig=sig))
```

### CLI

```bash
domtrack tables extract traces/trace_x.json -o tables.json --verbose
domtrack tables extract snapshot.json records.json     # split inputs
domtrack tables extract traces/trace_x.json --all      # keep overlaps
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `FILE...` (positional) | — | One combined trace, or `<snapshot.json> <records.json>` |
| `-o`, `--out` | stdout | Write JSON here |
| `--all` | off | Keep overlapping tables (skip de-duplication) |
| `--min-rows` | `3` | Minimum rows a table must keep |
| `--min-cols` | `2` | Minimum columns a table must keep |
| `--verbose` | off | Print a per-table summary to stderr |

Exit codes: `0` ok, `1` load error, `2` bad arguments.

## Labeling with an LLM

`label_tables` sends a compact payload (parent element, column hints + sample
values, a few sample rows) to a model and merges back a `title`, a `purpose`,
and a `semantic_label` per column. The provider SDKs are imported lazily.

Providers: `openai` (strict `json_schema`), `compatible` (any OpenAI-compatible
API — OpenRouter / Together / vLLM), `anthropic` (`tool_use`). Keys come from
`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` or `--api-key`.

```python
from domtrack.tables import label_tables, LabelConfig

labeled = label_tables(result, LabelConfig(provider="openai", lang="fa"))
```

```bash
domtrack tables label tables.json -o labeled.json
domtrack tables label tables.json --provider anthropic --model claude-sonnet-4-5
domtrack tables label tables.json --provider compatible \
    --base-url https://openrouter.ai/api/v1 --model openai/gpt-4o-mini
domtrack tables label tables.json --dry-run      # print the payload only
domtrack tables label tables.json --lang en      # English labels
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `input` (positional) | — | JSON from `tables extract` |
| `-o`, `--output` | `tables_labeled.json` | Output file |
| `--provider` | `openai` | `openai` / `compatible` / `anthropic` |
| `--model` | provider default | Model id |
| `--base-url` | — | Base URL for `compatible` |
| `--api-key` | env | API key (else read from the environment) |
| `--lang` | `fa` | Output language |
| `--batch-size` | `25` | Tables per model call |
| `--dry-run` | off | Print payloads, write nothing |

## Displaying

`render` draws labeled tables as `rich`, `plain`, `markdown`, or `csv`. Columns
are headed by `semantic_label`, falling back to `label`, then the raw id.

```python
from domtrack.tables import render_tables, RenderOptions

render_tables(labeled, "markdown", RenderOptions(max_rows=5), out_path="tables.md")
```

```bash
domtrack tables show labeled.json
domtrack tables show labeled.json --only 1,3,5
domtrack tables show labeled.json --format markdown -o tables.md
domtrack tables show labeled.json --format csv -o tables.csv
domtrack tables show labeled.json --max-rows 5 --max-chars 60
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `input` (positional) | — | JSON from `tables label` |
| `--only` | all | Only these tables, e.g. `1,3,5` (1-based) |
| `--format` | `rich` | `rich` / `plain` / `markdown` / `csv` |
| `-o`, `--output` | stdout | Output file (for `markdown`/`csv`) |
| `--max-rows` | `0` (all) | Max rows per table |
| `--max-chars` | `80` | Max cell length |

`rich` needs the `rich` extra and `plain` uses `tabulate` when present; both
fall back gracefully when the optional packages are missing.

## Optional dependencies

```bash
pip install 'domtrack[tables]'          # rich + tabulate (display)
pip install 'domtrack[llm]'             # openai (labeling)
pip install 'domtrack[llm-anthropic]'   # anthropic (labeling)
pip install 'domtrack[all]'             # everything
```

Discovery and CSV/markdown rendering need no optional dependency.
