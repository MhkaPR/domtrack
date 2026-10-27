"""FastAPI application factory for the domtrack REST API."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import ApiSettings, get_settings
from .routers import browser_free, capture, health, replay
from .service import ApiError

DESCRIPTION = """\
REST API for **domtrack** — capture, replay, diff, and mine DOM traces.

Every endpoint mirrors a `domtrack` CLI command so anything you can do from a
shell you can do over HTTP:

| Endpoint | CLI equivalent |
| --- | --- |
| `POST /capture` | `domtrack capture <url>` |
| `POST /replay` | `domtrack replay <trace.json>` |
| `POST /compare` | `domtrack compare <trace.json>` |
| `POST /html` | `domtrack html <trace.json>` |
| `POST /diff` | `domtrack diff <A> <B>` |
| `POST /tables/extract` | `domtrack tables extract <trace.json>` |
| `POST /tables/label` | `domtrack tables label <tables.json>` |
| `POST /tables/show` | `domtrack tables show <tables.json>` |

Traces can be referenced by server-side path (`{"path": "trace_...json"}`) or
passed inline (`{"trace": {...}}`).
"""


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    """Build the ASGI application.

    Passing ``settings`` makes the app configurable in tests; by default the
    process-wide environment-derived settings are used.
    """
    settings = settings or get_settings()
    application = FastAPI(
        title=settings.title,
        description=DESCRIPTION,
        version=settings.api_version,
    )
    application.state.settings = settings

    if settings.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @application.exception_handler(ApiError)
    async def _api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    application.include_router(health.router)
    application.include_router(capture.router)
    application.include_router(replay.router)
    application.include_router(browser_free.router)
    return application


app = create_app()
