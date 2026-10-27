"""FastAPI routers, one module per CLI domain."""

from __future__ import annotations

from . import browser_free, capture, health, replay

__all__ = ["browser_free", "capture", "health", "replay"]
