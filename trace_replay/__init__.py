"""
trace_replay — Replay a MutationObserver trace on a live page.

Example:
    from trace_replay import Trace, Replayer, ReplayConfig

    trace = Trace.load("trace.json")
    Replayer(trace, ReplayConfig(speed=2.0)).run()
"""
from .models import Mutation, NodeInfo, Target, Trace, TraceMeta
from .locators import ElementLocator
from .third_party import ThirdPartyDetector
from .appliers import ApplierRegistry, ApplyResult
from .replayer import Replayer, ReplayConfig, ReplayStats

__all__ = [
    "Trace", "TraceMeta", "Mutation", "Target", "NodeInfo",
    "ElementLocator",
    "ThirdPartyDetector",
    "ApplierRegistry", "ApplyResult",
    "Replayer", "ReplayConfig", "ReplayStats",
]

__version__ = "1.0.0"