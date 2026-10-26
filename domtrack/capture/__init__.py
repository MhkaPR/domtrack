"""Capture half of domtrack: record a trace from a live page.

Public surface: :class:`Recorder`, :class:`CaptureResult`, the
:class:`~domtrack.config.TracerConfig` dataclass, and the storage helpers.
"""

from ..config import DEFAULT_DYNAMIC_CLASSES, TracerConfig
from ..js_scripts import (
    COLLECT_SCRIPT,
    EVENT_INIT,
    HELPERS_INIT,
    build_init_script,
)
from ..recorder import CaptureResult, Recorder
from ..recorder import CaptureSession
from ..storage import save_csv, save_json, timestamp

__all__ = [
    "TracerConfig", "DEFAULT_DYNAMIC_CLASSES",
    "Recorder", "CaptureResult", "CaptureSession",
    "build_init_script", "COLLECT_SCRIPT", "EVENT_INIT", "HELPERS_INIT",
    "save_json", "save_csv", "timestamp",
]
