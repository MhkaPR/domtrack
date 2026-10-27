"""Service metadata: health check and trace listing."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..config import ApiSettings
from ..deps import get_api_settings
from ..schemas import HealthResponse, TraceListResponse
from ..service import list_traces

router = APIRouter(tags=["service"])


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
def health(settings: ApiSettings = Depends(get_api_settings)) -> HealthResponse:
    return HealthResponse(
        name=settings.title,
        version=settings.api_version,
        trace_dir=settings.trace_dir,
    )


@router.get(
    "/traces",
    response_model=TraceListResponse,
    summary="List trace files on the server",
)
def traces(settings: ApiSettings = Depends(get_api_settings)) -> TraceListResponse:
    return TraceListResponse(**list_traces(settings))
