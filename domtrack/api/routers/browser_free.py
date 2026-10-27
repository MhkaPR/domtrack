"""Browser-free endpoints: html rebuild, diff, and table tools.

These never launch Playwright, so they run directly in the event loop.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..config import ApiSettings
from ..deps import get_api_settings
from ..schemas import (
    DiffRequest,
    DiffResponse,
    HtmlRequest,
    HtmlResponse,
    TablesExtractRequest,
    TablesExtractResponse,
    TablesLabelRequest,
    TablesLabelResponse,
    TablesShowRequest,
    TablesShowResponse,
)
from ..service import (
    run_diff,
    run_html,
    run_tables_extract,
    run_tables_label,
    run_tables_show,
)

router = APIRouter(tags=["browser-free"])


@router.post(
    "/html",
    response_model=HtmlResponse,
    summary="Rebuild a trace's DOM as HTML",
    description=(
        "Equivalent to `domtrack html <trace.json>`. Folds the snapshot plus "
        "records and serializes the HTML at a chosen point (no browser)."
    ),
)
def html(
    req: HtmlRequest, settings: ApiSettings = Depends(get_api_settings)
) -> HtmlResponse:
    return HtmlResponse(**run_html(req, settings))


@router.post(
    "/diff",
    response_model=DiffResponse,
    summary="Content-level diff of two captures",
    description=(
        "Equivalent to `domtrack diff <A> <B>`. Compares two traces at the "
        "content level. Use `format=text` to also get the human-readable report."
    ),
)
def diff(
    req: DiffRequest, settings: ApiSettings = Depends(get_api_settings)
) -> DiffResponse:
    return DiffResponse(**run_diff(req, settings))


@router.post(
    "/tables/extract",
    response_model=TablesExtractResponse,
    summary="Discover tables in a trace",
    description="Equivalent to `domtrack tables extract <trace.json>`.",
)
def tables_extract(
    req: TablesExtractRequest, settings: ApiSettings = Depends(get_api_settings)
) -> TablesExtractResponse:
    return TablesExtractResponse(**run_tables_extract(req, settings))


@router.post(
    "/tables/label",
    response_model=TablesLabelResponse,
    summary="Label tables with an LLM",
    description=(
        "Equivalent to `domtrack tables label`. Provide extracted `tables` or "
        "a `trace` to extract first. The API key is read from the server "
        "environment unless passed explicitly."
    ),
)
def tables_label(
    req: TablesLabelRequest, settings: ApiSettings = Depends(get_api_settings)
) -> TablesLabelResponse:
    return TablesLabelResponse(**run_tables_label(req, settings))


@router.post(
    "/tables/show",
    response_model=TablesShowResponse,
    summary="Render labeled tables",
    description=(
        "Equivalent to `domtrack tables show`. Renders labeled tables as "
        "plain text, markdown, or CSV (rich is downgraded to plain text)."
    ),
)
def tables_show(
    req: TablesShowRequest, settings: ApiSettings = Depends(get_api_settings)
) -> TablesShowResponse:
    return TablesShowResponse(**run_tables_show(req, settings))
