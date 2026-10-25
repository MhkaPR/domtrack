from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class TimeContext:
    prev_t: int
    curr_t: int
    bucket_has_work: bool


class TimePolicy(ABC):
    @abstractmethod
    def compute_wait(self, ctx: TimeContext) -> int:
        """Return milliseconds to wait before this time bucket."""
        ...


class FaithfulTimePolicy(TimePolicy):
    """Original behaviour: wait the real gap, divided by speed."""

    def __init__(self, speed: float = 1.0):
        self.speed = max(0.01, speed)

    def compute_wait(self, ctx: TimeContext) -> int:
        return max(0, int((ctx.curr_t - ctx.prev_t) / self.speed))


class InstantTimePolicy(TimePolicy):
    """No delays at all — apply everything as fast as possible."""

    def compute_wait(self, ctx: TimeContext) -> int:
        return 0


class CappedTimePolicy(TimePolicy):
    """Real gap / speed, but each wait is capped at cap_ms."""

    def __init__(self, speed: float = 1.0, cap_ms: int = 200):
        self.speed = max(0.01, speed)
        self.cap_ms = max(0, cap_ms)

    def compute_wait(self, ctx: TimeContext) -> int:
        wait = int((ctx.curr_t - ctx.prev_t) / self.speed)
        return min(self.cap_ms, max(0, wait))


class SkipAwareTimePolicy(TimePolicy):
    """
    Wait ONLY for buckets that contain work.
    Empty buckets cost 0ms.
    The gap before a work bucket is measured from the LAST WORK bucket.
    If cap_ms is set, each wait is also capped.
    """

    def __init__(self, speed: float = 1.0, cap_ms: int | None = None):
        self.speed = max(0.01, speed)
        self.cap_ms = cap_ms
        self._last_work_t = 0

    def compute_wait(self, ctx: TimeContext) -> int:
        if not ctx.bucket_has_work:
            return 0
        gap = ctx.curr_t - self._last_work_t
        self._last_work_t = ctx.curr_t
        wait = int(gap / self.speed)
        if self.cap_ms is not None:
            return min(self.cap_ms, max(0, wait))
        return max(0, wait)


def build_policy(name: str, speed: float, cap_ms: int | None = None) -> TimePolicy:
    if name == "faithful":
        return FaithfulTimePolicy(speed)
    if name == "instant":
        return InstantTimePolicy()
    if name == "capped":
        return CappedTimePolicy(speed, cap_ms if cap_ms is not None else 200)
    if name == "skip-aware":
        return SkipAwareTimePolicy(speed, cap_ms)
    raise ValueError(f"unknown time policy: {name}")