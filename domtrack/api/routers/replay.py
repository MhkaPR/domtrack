"""Browser-backed replay and compare endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from ..config import ApiSettings
from ..deps import get_api_settings
from ..schemas import CompareRequest, CompareResponse, ReplayRequest, ReplayResponse
from ..service import run_compare, run_replay

router = APIRouter(tags=["replay"])


@router.post(
    "/replay",
    response_model=ReplayResponse,
    summary="Replay a trace on a live page",
    description=(
        "Equivalent to `domtrack replay <trace.json>`. Accepts a trace by "
        "server path or inline, drives a fresh browser page, and returns the "
        "replay stats. Set `record=true` to also record the replayed page."
    ),
)
async def replay(
    req: ReplayRequest, settings: ApiSettings = Depends(get_api_settings)
) -> ReplayResponse:
    result = await run_in_threadpool(run_replay, req, settings)
    return ReplayResponse(**result)


@router.post(
    "/compare",
    response_model=CompareResponse,
    summary="Replay, record, and diff against the original",
    description=(
        "Equivalent to `domtrack compare <trace.json>`: replay a captured "
        "trace, record what the automated run produced, and return the "
        "content diff against the original."
    ),
)
async def compare(
    req: CompareRequest, settings: ApiSettings = Depends(get_api_settings)
) -> CompareResponse:
    result = await run_in_threadpool(run_compare, req, settings)
    return CompareResponse(**result)
