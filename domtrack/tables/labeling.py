"""Semantic labeling of extracted tables with an LLM.

Takes the output of :func:`domtrack.tables.discovery.discover_tables` (the
``tables`` key) and returns the same JSON with semantic fields added: a table
``title`` / ``purpose`` and a ``semantic_label`` per column.

Providers:

* ``openai``      — OpenAI SDK with a strict ``json_schema`` response format.
* ``compatible``  — any OpenAI-compatible API (OpenRouter / Together / vLLM / …).
* ``anthropic``   — Anthropic SDK with ``tool_use``.

The provider SDKs are imported lazily, so importing this module never requires
them. Set ``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY`` (or pass ``api_key``).
"""

from __future__ import annotations

import copy
import json
import os
import sys
from dataclasses import dataclass
from typing import Optional


# ── compact payload ────────────────────────────────────────────────────────

def truncate(value, limit: int = 120):
    if not isinstance(value, str):
        return value
    value = value.strip().replace("\n", " ")
    return value if len(value) <= limit else value[: limit - 1] + "…"


def compact_table(
    index: int, table: dict, max_samples: int = 3, max_rows: int = 3, max_chars: int = 120
) -> dict:
    """Reduce one table to the compact shape sent to the model."""
    columns = table.get("columns") or []
    rows = table.get("rows") or []

    column_samples: list[list[str]] = []
    for column in columns:
        values: list[str] = []
        for row in rows:
            value = row.get(column["id"], "")
            if value:
                values.append(truncate(value, max_chars))
            if len(values) >= max_samples:
                break
        column_samples.append(values)

    row_samples = [
        [truncate(row.get(c["id"], ""), max_chars) for c in columns]
        for row in rows[:max_rows]
    ]

    return {
        "index": index,
        "parent": {
            "tag": table["meta"]["parent_tag"],
            "classes": table["meta"]["parent_classes"][:3],
        },
        "row_count": table["meta"]["row_count"],
        "columns": [
            {
                "index": ci,
                "hint": c.get("label", ""),
                "atype": c.get("atype", ""),
                "path_tail": (c.get("path") or "").split(" > ")[-1],
                "samples": column_samples[ci],
            }
            for ci, c in enumerate(columns)
        ],
        "row_samples": row_samples,
    }


def build_payload(tables: list[dict], start_index: int) -> dict:
    return {
        "tables": [
            compact_table(start_index + i, t) for i, t in enumerate(tables)
        ]
    }


# ── JSON schema ────────────────────────────────────────────────────────────

COLUMN_LABEL_SCHEMA = {
    "type": "object",
    "properties": {
        "column_index": {"type": "integer"},
        "label": {"type": "string"},
    },
    "required": ["column_index", "label"],
    "additionalProperties": False,
}

TABLE_LABEL_SCHEMA = {
    "type": "object",
    "properties": {
        "index": {"type": "integer"},
        "title": {"type": "string"},
        "purpose": {"type": "string"},
        "columns": {"type": "array", "items": COLUMN_LABEL_SCHEMA},
    },
    "required": ["index", "title", "purpose", "columns"],
    "additionalProperties": False,
}

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "tables": {"type": "array", "items": TABLE_LABEL_SCHEMA},
    },
    "required": ["tables"],
    "additionalProperties": False,
}

JSON_SCHEMA_WRAPPER = {
    "name": "table_labels",
    "strict": True,
    "schema": RESPONSE_SCHEMA,
}

SYSTEM_PROMPT = (
    "You label extracted HTML tables. "
    "For every table you receive its parent element, its columns (hint, sample "
    "values, path tail), and a few sample rows. "
    "Return for each table: a concise semantic 'title' (max ~40 chars), "
    "a one-sentence 'purpose', and a short semantic 'label' for EVERY column "
    "(max ~20 chars, no path fragments, no raw class names). "
    "Write everything in {lang}. "
    "Rules: do NOT invent data; do NOT drop tables; do NOT reorder tables; "
    "keep the SAME index for tables and for columns as provided; "
    "return exactly one column entry per column, in the same order."
)


# ── providers ──────────────────────────────────────────────────────────────

def call_openai(
    api_key: str,
    model: str,
    base_url: Optional[str],
    payload: dict,
    lang: str,
    use_strict: bool = True,
) -> dict:
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url)
    kwargs = dict(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT.format(lang=lang)},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        temperature=0.1,
    )
    if use_strict:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": JSON_SCHEMA_WRAPPER,
        }
    else:
        kwargs["response_format"] = {"type": "json_object"}
    response = client.chat.completions.create(**kwargs)
    return json.loads(response.choices[0].message.content)


def call_anthropic(api_key: str, model: str, payload: dict, lang: str) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=8192,
        system=SYSTEM_PROMPT.format(lang=lang),
        tools=[
            {
                "name": "submit_labels",
                "description": "Submit semantic labels for the extracted tables.",
                "input_schema": RESPONSE_SCHEMA,
            }
        ],
        tool_choice={"type": "tool", "name": "submit_labels"},
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
    )
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_labels":
            return block.input
    raise RuntimeError("Anthropic did not return submit_labels tool_use")


# ── batching + merge ───────────────────────────────────────────────────────

def run_llm(tables: list[dict], config: "LabelConfig") -> list[dict]:
    """Label ``tables`` in batches; returns the flattened label list."""
    labels: list[dict] = []
    batch = max(1, config.batch_size)
    for start in range(0, len(tables), batch):
        chunk = tables[start:start + batch]
        payload = build_payload(chunk, start)
        if config.dry_run:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            continue
        if config.provider == "anthropic":
            response = call_anthropic(config.api_key, config.model, payload, config.lang)
        else:
            response = call_openai(
                config.api_key,
                config.model,
                config.base_url,
                payload,
                config.lang,
                use_strict=(config.provider == "openai"),
            )
        labels.extend(response.get("tables", []))
        print(
            f"[batch] {start + len(chunk)}/{len(tables)} tables labeled",
            file=sys.stderr,
        )
    return labels


def merge_labels(data: dict, labels: list[dict]) -> dict:
    """Merge model labels back into a copy of ``data``."""
    by_index = {t["index"]: t for t in labels}
    out = copy.deepcopy(data)
    for i, table in enumerate(out.get("tables", [])):
        label = by_index.get(i)
        if not label:
            continue
        table.setdefault("meta", {})["title"] = label.get("title", "")
        table["meta"]["purpose"] = label.get("purpose", "")
        column_by_index = {
            c["column_index"]: c.get("label", "") for c in label.get("columns", [])
        }
        for ci, column in enumerate(table.get("columns", [])):
            if ci in column_by_index:
                column["semantic_label"] = column_by_index[ci]
    return out


# ── public entry point ─────────────────────────────────────────────────────

DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "compatible": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-5",
}

API_KEY_ENV: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "compatible": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


@dataclass
class LabelConfig:
    """Settings for :func:`label_tables`."""

    provider: str = "openai"
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    lang: str = "fa"
    batch_size: int = 25
    dry_run: bool = False


def resolve_api_key(config: LabelConfig) -> Optional[str]:
    """Read the provider's API key from the environment."""
    env = API_KEY_ENV.get(config.provider)
    return os.environ.get(env) if env else None


def label_tables(data: dict, config: Optional[LabelConfig] = None) -> dict:
    """Label every table in ``data`` and return the merged result.

    With ``config.dry_run`` the payloads are printed and ``data`` is returned
    unchanged (nothing is sent to a model).
    """
    config = config or LabelConfig()
    if not config.model:
        config.model = DEFAULT_MODELS[config.provider]
    tables = data.get("tables") or []
    labels = run_llm(tables, config)
    if config.dry_run:
        return data
    return merge_labels(data, labels)
