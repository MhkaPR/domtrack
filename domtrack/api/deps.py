"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import Request

from .config import ApiSettings, get_settings


def get_api_settings(request: Request) -> ApiSettings:
    """Return the settings bound to the running app.

    Falls back to the process-wide settings when the app was created without
    an explicit :class:`ApiSettings` (the normal ``uvicorn`` path).
    """
    settings = getattr(request.app.state, "settings", None)
    return settings if settings is not None else get_settings()
