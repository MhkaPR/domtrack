"""Replay half of domtrack: feed a captured trace back into a page.

Public surface: :class:`Replayer` (+ :class:`ReplayConfig`,
:class:`ReplayStats`), the mutation filters, the time policies, and the
element locator.
"""

from .models import Mutation, NodeInfo, Target, Trace, TraceMeta
from .locators import ElementLocator
from .third_party import ThirdPartyDetector
from .appliers import ApplierRegistry, ApplyResult
from .filters import (
    CompositeFilter,
    FilterResult,
    MutationFilter,
    StructureOnlyFilter,
    ThirdPartyFilter,
)
from .timing import (
    CappedTimePolicy,
    FaithfulTimePolicy,
    InstantTimePolicy,
    SkipAwareTimePolicy,
    TimeContext,
    TimePolicy,
    build_policy,
)
from .recording import RecordingConfig, ReplayRecorder
from .replayer import Replayer, ReplayConfig, ReplayStats
from .compare import CompareResult, ReplayComparer, compare_replay, compare_traces

__all__ = [
    "Trace", "TraceMeta", "Mutation", "Target", "NodeInfo",
    "ElementLocator",
    "ThirdPartyDetector",
    "ApplierRegistry", "ApplyResult",
    "MutationFilter", "FilterResult", "CompositeFilter",
    "ThirdPartyFilter", "StructureOnlyFilter",
    "TimePolicy", "TimeContext",
    "FaithfulTimePolicy", "InstantTimePolicy",
    "CappedTimePolicy", "SkipAwareTimePolicy",
    "build_policy",
    "Replayer", "ReplayConfig", "ReplayStats",
    "RecordingConfig", "ReplayRecorder",
    "CompareResult", "ReplayComparer", "compare_replay", "compare_traces",
]

__version__ = "2.0.0"
