"""Browser-backed capture endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from ..config import ApiSettings
from ..deps import get_api_settings
from ..schemas import CaptureRequest, CaptureResponse
from ..service import run_capture

router = APIRouter(tags=["capture"])


@router.post(
    "/capture",
    response_model=CaptureResponse,
    summary="Record a trace from a live page",
    description=(
        "Equivalent to `domtrack capture <url> --duration ...`. Opens a "
        "browser, records DOM mutations and user events for `duration_s` "
        "seconds, then writes a trace (JSON + CSV) into the server trace dir."
    ),
)
async def capture(
    req: CaptureRequest, settings: ApiSettings = Depends(get_api_settings)
) -> CaptureResponse:
    result = await run_in_threadpool(run_capture, req, settings)
    return CaptureResponse(**result)
