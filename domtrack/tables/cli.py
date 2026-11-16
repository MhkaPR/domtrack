"""``domtrack tables`` — extract, label, and display HTML tables from a trace.

Four subcommands over the :mod:`domtrack.tables` helpers:

* ``extract`` — discover repeated tabular structures in a trace (no browser).
* ``label``   — attach semantic titles / column labels with an LLM.
* ``normalize`` — split mixed columns into single-concept columns.
* ``show``    — render labeled tables as rich / plain / markdown / csv.
"""

from __future__ import annotations

import argparse
import json
import sys

from .. import trace as tf
from .discovery import DiscoveryConfig, discover_tables
from .labeling import API_KEY_ENV, LabelConfig, label_tables, resolve_api_key
from .normalize import NormalizeConfig, normalize_table, normalize_tables
from .render import RenderOptions, render


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="domtrack tables",
        description="Extract, label, and display HTML tables from a trace.",
    )
    sub = p.add_subparsers(dest="command", required=True)
    _add_extract(sub)
    _add_label(sub)
    _add_normalize(sub)
    _add_show(sub)
    return p


def _add_extract(sub) -> None:
    p = sub.add_parser("extract", help="discover tables in a trace")
    p.add_argument("inputs", nargs="+", metavar="FILE",
                   help="combined trace, or <snapshot.json> <records.json>")
    p.add_argument("-o", "--out", default=None,
                   help="write JSON here (default: stdout)")
    p.add_argument("--all", action="store_true",
                   help="keep overlapping tables (skip de-duplication)")
    p.add_argument("--min-rows", type=int, default=3,
                   help="minimum rows a table must keep (default: 3)")
    p.add_argument("--min-cols", type=int, default=2,
                   help="minimum columns a table must keep (default: 2)")
    p.add_argument("--verbose", action="store_true",
                   help="print a summary to stderr")


def _add_label(sub) -> None:
    p = sub.add_parser("label", help="add semantic labels with an LLM")
    p.add_argument("input", help="JSON from `tables extract`")
    p.add_argument("-o", "--output", default="tables_labeled.json",
                   help="output file (default: tables_labeled.json)")
    p.add_argument("--provider", choices=["openai", "compatible", "anthropic"],
                   default="openai")
    p.add_argument("--model", default=None)
    p.add_argument("--base-url", default=None)
    p.add_argument("--api-key", default=None)
    p.add_argument("--lang", default="fa", help="output language (fa/en/…)")
    p.add_argument("--batch-size", type=int, default=25,
                   help="tables per model call (default: 25)")
    p.add_argument("--dry-run", action="store_true",
                   help="print the payload and write nothing")


def _add_show(sub) -> None:
    p = sub.add_parser("show", help="render labeled tables")
    p.add_argument("input", help="JSON from `tables label`")
    p.add_argument("--only", default=None,
                   help="only these tables, e.g. 1,3,5 (1-based)")
    p.add_argument("--format", choices=["rich", "plain", "markdown", "csv"],
                   default="rich")
    p.add_argument("-o", "--output", default=None,
                   help="output file (for markdown/csv)")
    p.add_argument("--max-rows", type=int, default=0,
                   help="max rows per table (0 = all)")
    p.add_argument("--max-chars", type=int, default=80,
                   help="max cell length (default: 80)")


def _add_normalize(sub) -> None:
    p = sub.add_parser(
        "normalize",
        help="split heterogeneous columns into single-concept columns",
    )
    p.add_argument("input", help="JSON from `tables extract` or a single table")
    p.add_argument("-o", "--output", default="tables_normalized.json",
                   help="output file (default: tables_normalized.json)")
    p.add_argument("--model", default=None, help="embedding model name")
    p.add_argument("--device", default=None, help="torch device, e.g. cuda/cpu")
    p.add_argument("--batch-size", type=int, default=64,
                   help="embedding batch size (default: 64)")
    p.add_argument("--threshold", type=float, default=0.04,
                   help="cosine distance threshold (default: 0.04)")
    p.add_argument("--homogeneity-threshold", type=float, default=0.04,
                   help="max mean distance from centroid (default: 0.04)")
    p.add_argument("--min-cluster-size", type=int, default=3,
                   help="clusters smaller than this are rare (default: 3)")
    p.add_argument("--max-workers", type=int, default=None,
                   help="parallel column embedding workers")
    p.add_argument("--no-merge", action="store_true",
                   help="do not merge related split columns")
    p.add_argument("--no-half", action="store_true",
                   help="disable half precision on GPU")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "extract":
        return _run_extract(args)
    if args.command == "label":
        return _run_label(args)
    if args.command == "normalize":
        return _run_normalize(args)
    if args.command == "show":
        return _run_show(args)
    return 2


# ── extract ────────────────────────────────────────────────────────────────

def _run_extract(args) -> int:
    if len(args.inputs) > 2:
        print("ERROR: expected one combined file or two split files", file=sys.stderr)
        return 2
    try:
        raw = tf.load_trace_inputs(args.inputs)
    except FileNotFoundError as e:
        print(f"ERROR: file not found: {e.filename}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ERROR: could not load trace: {e}", file=sys.stderr)
        return 1
    if not raw.get("snapshot"):
        print("ERROR: snapshot not found in input", file=sys.stderr)
        return 1

    config = DiscoveryConfig(
        min_rows=args.min_rows,
        min_cols=args.min_cols,
        dedupe=not args.all,
    )
    result = discover_tables(raw, config)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)
    if args.verbose:
        _print_extract_summary(result)
    return 0


def _print_extract_summary(result: dict) -> None:
    print(
        f"\n[result] {result['tables_returned']} unique tables "
        f"of {result['total_candidates']} candidates",
        file=sys.stderr,
    )
    for i, table in enumerate(result["tables"], 1):
        meta = table["meta"]
        classes = ".".join(meta["parent_classes"][:2])
        print(
            f"  {i:2d}. <{meta['parent_tag']}.{classes}> "
            f"rows={meta['row_count']} cols={meta['columns']} "
            f"sim={meta['avg_sim']} score={table['score']}",
            file=sys.stderr,
        )


# ── label ──────────────────────────────────────────────────────────────────

def _run_label(args) -> int:
    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)
    if not data.get("tables"):
        print("ERROR: no tables found in input", file=sys.stderr)
        return 1

    config = LabelConfig(
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        lang=args.lang,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )
    if not config.api_key:
        config.api_key = resolve_api_key(config)
        if not config.api_key and not config.dry_run:
            print(
                f"ERROR: missing API key: set {API_KEY_ENV[config.provider]} "
                f"or pass --api-key",
                file=sys.stderr,
            )
            return 1

    print(f"[in ] {len(data['tables'])} tables from {args.input}", file=sys.stderr)
    merged = label_tables(data, config)
    if config.dry_run:
        print("\n[dry-run] nothing written", file=sys.stderr)
        return 0

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"[out] wrote {args.output}", file=sys.stderr)
    _print_label_summary(merged)
    return 0


def _print_label_summary(merged: dict) -> None:
    print("\n[summary]", file=sys.stderr)
    for i, table in enumerate(merged["tables"], 1):
        meta = table.get("meta", {})
        print(
            f"  {i:2d}. {meta.get('title', '?'):<40s}  "
            f"rows={meta.get('row_count')}  cols={meta.get('columns')}",
            file=sys.stderr,
        )


# ── normalize ──────────────────────────────────────────────────────────────


def _run_normalize(args) -> int:
    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)

    config = NormalizeConfig(
        model_name=args.model or NormalizeConfig.model_name,
        batch_size=args.batch_size,
        device=args.device,
        half_precision=not args.no_half,
        max_workers=args.max_workers,
        distance_threshold=args.threshold,
        homogeneity_threshold=args.homogeneity_threshold,
        min_cluster_size=args.min_cluster_size,
        merge_related=not args.no_merge,
    )

    if isinstance(data, dict) and data.get("tables"):
        result = normalize_tables(data, config)
        tables = result["tables"]
    else:
        result = normalize_table(data, config)
        tables = [result]

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[out] wrote {args.output}", file=sys.stderr)
    for index, table in enumerate(tables, 1):
        report = table.get("report", {})
        validation = table.get("validation", {})
        print(
            f"  {index:2d}. rows={report.get('rows')} "
            f"cols={report.get('input_columns')}→{report.get('output_columns')} "
            f"coverage={report.get('coverage')} "
            f"ok={validation.get('ok')}",
            file=sys.stderr,
        )
    return 0


# ── show ───────────────────────────────────────────────────────────────────

def _run_show(args) -> int:
    only = None
    if args.only:
        try:
            only = {int(x.strip()) for x in args.only.split(",") if x.strip()}
        except ValueError:
            print("ERROR: --only must be a list of numbers, e.g. 1,3,5", file=sys.stderr)
            return 2

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)
    if not data.get("tables"):
        print("ERROR: no tables found in file", file=sys.stderr)
        return 1

    options = RenderOptions(
        only=only, max_rows=args.max_rows, max_chars=args.max_chars
    )
    render(data, args.format, options, out_path=args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
