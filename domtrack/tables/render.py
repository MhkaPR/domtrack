"""Render labeled tables as rich / plain / markdown / csv.

Consumes the output of :func:`domtrack.tables.labeling.label_tables`. Columns
are headed by ``semantic_label`` when present, falling back to ``label`` and
then the raw column id.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass
class RenderOptions:
    """Display options shared by every renderer."""

    only: Optional[set[int]] = None   # 1-based table numbers to show
    max_rows: int = 0                 # 0 = all rows
    max_chars: int = 80               # 0 = no truncation


def get_cell(row: dict, column_id: str) -> str:
    value = row.get(column_id, "")
    return "" if value is None else str(value)


def column_headers(table: dict) -> list[tuple[str, str]]:
    """``(column_id, header)`` pairs; header = semantic_label ← label ← id."""
    out: list[tuple[str, str]] = []
    for column in table.get("columns", []):
        header = (
            column.get("semantic_label")
            or column.get("label")
            or (column.get("id", "")[:40])
        )
        out.append((column["id"], header))
    return out


def truncate(value: str, limit: int) -> str:
    if limit <= 0 or len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def iter_tables(data: dict, only: Optional[set[int]] = None) -> Iterable[tuple[int, dict]]:
    for i, table in enumerate(data.get("tables", []), 1):
        if only is not None and i not in only:
            continue
        yield i, table


# ── rich ───────────────────────────────────────────────────────────────────

def render_rich(data: dict, options: RenderOptions) -> None:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()
    for index, table in iter_tables(data, options.only):
        meta = table.get("meta", {})
        title = meta.get("title") or "(بدون عنوان)"
        purpose = meta.get("purpose") or ""

        header = f"[bold]{title}[/bold]"
        if purpose:
            header += f"\n[dim]{purpose}[/dim]"
        console.print(Panel.fit(header, title=f"جدول {index}", border_style="cyan"))

        columns = column_headers(table)
        grid = Table(show_header=True, header_style="bold magenta", box=box.SIMPLE_HEAVY)
        grid.add_column("#", style="dim", justify="right", width=3)
        for _, heading in columns:
            grid.add_column(heading, overflow="fold")

        rows = table.get("rows", [])
        shown = rows[: options.max_rows] if options.max_rows else rows
        for ri, row in enumerate(shown, 1):
            values = [
                truncate(get_cell(row, cid), options.max_chars) for cid, _ in columns
            ]
            grid.add_row(str(ri), *values)

        console.print(grid)
        if options.max_rows and len(rows) > options.max_rows:
            console.print(f"[dim]… {len(rows) - options.max_rows} ردیف دیگر[/dim]")
        console.print()


# ── plain ──────────────────────────────────────────────────────────────────

def render_plain(data: dict, options: RenderOptions) -> None:
    try:
        from tabulate import tabulate
    except ImportError:
        tabulate = None

    for index, table in iter_tables(data, options.only):
        meta = table.get("meta", {})
        title = meta.get("title") or "(بدون عنوان)"
        purpose = meta.get("purpose") or ""
        print(f"\n=== جدول {index}: {title} ===")
        if purpose:
            print(purpose)
        print()

        columns = column_headers(table)
        headers = ["#"] + [heading for _, heading in columns]

        rows = table.get("rows", [])
        shown = rows[: options.max_rows] if options.max_rows else rows
        body = [
            [str(ri)]
            + [truncate(get_cell(row, cid), options.max_chars) for cid, _ in columns]
            for ri, row in enumerate(shown, 1)
        ]

        if tabulate:
            print(tabulate(body, headers=headers, tablefmt="github"))
        else:
            widths = [len(h) for h in headers]
            for row in body:
                for i, cell in enumerate(row):
                    widths[i] = max(widths[i], len(cell))
            print(" | ".join(h.ljust(w) for h, w in zip(headers, widths)))
            print("-+-".join("-" * w for w in widths))
            for row in body:
                print(" | ".join(c.ljust(w) for c, w in zip(row, widths)))

        if options.max_rows and len(rows) > options.max_rows:
            print(f"... {len(rows) - options.max_rows} ردیف دیگر")
        print()


# ── markdown ───────────────────────────────────────────────────────────────

def render_markdown(data: dict, options: RenderOptions, out=sys.stdout) -> None:
    for index, table in iter_tables(data, options.only):
        meta = table.get("meta", {})
        title = meta.get("title") or "(بدون عنوان)"
        purpose = meta.get("purpose") or ""
        out.write(f"\n## جدول {index}: {title}\n\n")
        if purpose:
            out.write(f"_{purpose}_\n\n")

        columns = column_headers(table)
        headers = ["#"] + [heading for _, heading in columns]
        out.write("| " + " | ".join(headers) + " |\n")
        out.write("|" + "|".join(["---"] * len(headers)) + "|\n")

        rows = table.get("rows", [])
        shown = rows[: options.max_rows] if options.max_rows else rows
        for ri, row in enumerate(shown, 1):
            values = [str(ri)] + [
                truncate(get_cell(row, cid), options.max_chars)
                .replace("|", "\\|")
                .replace("\n", " ")
                for cid, _ in columns
            ]
            out.write("| " + " | ".join(values) + " |\n")
        out.write("\n")


# ── csv ────────────────────────────────────────────────────────────────────

def render_csv(data: dict, options: RenderOptions, out) -> None:
    writer = csv.writer(out)
    for index, table in iter_tables(data, options.only):
        meta = table.get("meta", {})
        writer.writerow([f"# جدول {index}: {meta.get('title', '')}"])
        if meta.get("purpose"):
            writer.writerow([meta["purpose"]])
        columns = column_headers(table)
        writer.writerow(["#"] + [heading for _, heading in columns])
        rows = table.get("rows", [])
        shown = rows[: options.max_rows] if options.max_rows else rows
        for ri, row in enumerate(shown, 1):
            writer.writerow([ri] + [get_cell(row, cid) for cid, _ in columns])
        writer.writerow([])


# ── dispatch ───────────────────────────────────────────────────────────────

def render(
    data: dict,
    fmt: str,
    options: Optional[RenderOptions] = None,
    out_path: Optional[str] = None,
) -> None:
    """Render ``data`` in ``fmt``; write to ``out_path`` when given."""
    options = options or RenderOptions()
    if fmt == "rich":
        try:
            render_rich(data, options)
        except ImportError:
            print("[!] rich not installed → falling back to plain", file=sys.stderr)
            render_plain(data, options)
    elif fmt == "plain":
        render_plain(data, options)
    elif fmt == "markdown":
        if out_path:
            with open(out_path, "w", encoding="utf-8") as f:
                render_markdown(data, options, out=f)
            print(f"[ok] wrote {out_path}", file=sys.stderr)
        else:
            render_markdown(data, options)
    elif fmt == "csv":
        if out_path:
            with open(out_path, "w", encoding="utf-8", newline="") as f:
                render_csv(data, options, out=f)
            print(f"[ok] wrote {out_path}", file=sys.stderr)
        else:
            render_csv(data, options, out=sys.stdout)
    else:
        raise ValueError(f"unknown format: {fmt}")
