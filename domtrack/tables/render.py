"""Render labeled tables as rich / plain / markdown / csv.

Consumes the output of :func:`domtrack.tables.labeling.label_tables`. Columns
are headed by ``semantic_label`` when present, falling back to ``label`` and
then the raw column id.
"""

from __future__ import annotations

import csv
import html
import json
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import urljoin, urlsplit


@dataclass
class RenderOptions:
    """Display options shared by every renderer."""

    only: Optional[set[int]] = None   # 1-based table numbers to show
    max_rows: int = 0                 # 0 = all rows
    max_chars: int = 80               # 0 = no truncation
    base_url: Optional[str] = None


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


def render_html(data: dict, options: RenderOptions, out) -> None:
    """Write a standalone HTML page with complete, clickable URL cells."""
    base = options.base_url or data.get("page", {}).get("page_url") or data.get("meta", {}).get("page_url") or ""
    out.write('<!doctype html><html><head><meta charset="utf-8">')
    out.write('<meta name="viewport" content="width=device-width, initial-scale=1">')
    out.write('''<title>Extracted tables</title><style>
*{box-sizing:border-box}body{font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;padding:clamp(1rem,3vw,2.5rem);color:#202633;background:#f5f7fa}
h2{margin:2rem 0 .75rem;font-size:1.25rem}.table-scroll{width:100%;overflow:auto;overscroll-behavior-x:contain;border:1px solid #d9e0e8;border-radius:10px;background:white;box-shadow:0 2px 8px #1822300d;margin-bottom:2rem}
table{border-collapse:separate;border-spacing:0;table-layout:auto;width:max-content;min-width:100%}th,td{padding:.7rem .85rem;border-bottom:1px solid #e5e9ef;text-align:left;vertical-align:top;min-width:10rem;max-width:32rem;white-space:normal;overflow-wrap:break-word;word-break:normal}
th{position:sticky;top:0;background:#edf2f7;font-weight:650;z-index:1}tbody tr:nth-child(even){background:#f8fafc}tbody tr:hover{background:#eef6ff}td:first-child,th:first-child{min-width:3rem;width:3rem;text-align:right;color:#718096}a{color:#1261a0;text-decoration:none;overflow-wrap:anywhere}a:hover{text-decoration:underline}
.cell-preview{display:block;max-width:30rem;max-height:3.1em;overflow:hidden;line-height:1.55;white-space:normal;overflow-wrap:anywhere;cursor:help}.cell-preview[title]:hover{overflow:visible}.cell-preview[title]{text-decoration:underline dotted #9aa7b8;text-underline-offset:3px}
@media(max-width:640px){body{padding:.75rem}th,td{min-width:8rem;max-width:18rem;padding:.55rem}.table-scroll{border-radius:7px}}
</style></head><body>''')
    for index, table in iter_tables(data, options.only):
        meta = table.get("meta", {})
        out.write(f'<h2>{html.escape(str(meta.get("title") or f"Table {index}"))}</h2>')
        out.write('<div class="table-scroll" role="region" aria-label="Scrollable table" tabindex="0"><table><thead><tr><th>#</th>')
        columns = column_headers(table)
        for _, heading in columns:
            out.write(f'<th>{html.escape(str(heading))}</th>')
        out.write('</tr></thead><tbody>')
        rows = table.get("rows", [])
        shown = rows[:options.max_rows] if options.max_rows else rows
        for ri, row in enumerate(shown, 1):
            out.write(f'<tr><td>{ri}</td>')
            column_by_id = {column.get("id"): column for column in table.get("columns", [])}
            for cid, _ in columns:
                value = get_cell(row, cid)
                parts = urlsplit(value)
                is_explicit_url = bool(parts.scheme or value.startswith("//"))
                is_relative_path = value.startswith(("/", "./", "../", "?", "#"))
                absolute = (
                    urljoin(base, value)
                    if base and is_relative_path
                    else value
                )
                safe_target = (
                    absolute
                    if is_explicit_url or (base and is_relative_path)
                    else ""
                )
                preview = truncate(absolute, 120) if safe_target else truncate(value, 120)
                title = f' title="{html.escape(absolute if safe_target else value, quote=True)}"' if len(value) > 120 else ""
                column = column_by_id.get(cid, {})
                is_link_column = column.get("atype") in {"href", "src"} or cid.endswith(("@href", "@src"))
                if is_link_column and safe_target:
                    cell = f'<a class="cell-preview"{title} href="{html.escape(safe_target, quote=True)}">{html.escape(preview)}</a>'
                else:
                    cell = f'<span class="cell-preview"{title}>{html.escape(preview)}</span>'
                out.write(f'<td>{cell}</td>')
            out.write('</tr>')
        out.write('</tbody></table></div>')
    out.write('</body></html>')


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
    elif fmt == "html":
        if out_path:
            with open(out_path, "w", encoding="utf-8") as f:
                render_html(data, options, f)
            print(f"[ok] wrote {out_path}", file=sys.stderr)
        else:
            render_html(data, options, out=sys.stdout)
    else:
        raise ValueError(f"unknown format: {fmt}")
