"""Runtime settings for the REST API server.

Kept deliberately small: an output directory for traces plus a couple of
guard rails. Everything can be overridden with ``DOMTRACK_API_*`` environment
variables so the same image can be configured without touching code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class ApiSettings:
    """Server-wide settings, resolved once per process."""

    #: Default directory for traces written by the API.
    trace_dir: str = "traces"
    #: Hard ceiling on a single capture, in seconds. Prevents an accidental
    #: ``duration_s=10**9`` from pinning a browser forever.
    max_capture_seconds: int = 3600
    #: Whether browser-backed endpoints launch headless by default.
    headless_default: bool = True
    #: CORS origins allowed to call the API (``*`` by default, dev-friendly).
    cors_origins: tuple[str, ...] = ("*",)
    #: Title/description shown in the OpenAPI docs.
    title: str = "domtrack API"
    api_version: str = "1.0.0"

    @classmethod
    def from_env(cls) -> "ApiSettings":
        origins = os.environ.get("DOMTRACK_API_CORS_ORIGINS", "*")
        parsed = tuple(o.strip() for o in origins.split(",") if o.strip()) or ("*",)
        return cls(
            trace_dir=os.environ.get("DOMTRACK_API_TRACE_DIR", "traces"),
            max_capture_seconds=_env_int("DOMTRACK_API_MAX_CAPTURE_SECONDS", 3600),
            headless_default=os.environ.get(
                "DOMTRACK_API_HEADLESS", "1"
            ).lower() not in ("0", "false", "no"),
            cors_origins=parsed,
        )


@lru_cache(maxsize=1)
def get_settings() -> ApiSettings:
    """Process-wide settings singleton (cached)."""
    return ApiSettings.from_env()
