from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterable

from .models import Mutation
from .third_party import ThirdPartyDetector


@dataclass
class FilterResult:
    skip: bool
    reason: str = ""

    @classmethod
    def allow(cls) -> "FilterResult":
        return cls(False)

    @classmethod
    def block(cls, reason: str) -> "FilterResult":
        return cls(True, reason)


class MutationFilter(ABC):
    """Base filter — return FilterResult(True, reason) to skip a mutation."""

    @abstractmethod
    def check(self, mutation: Mutation) -> FilterResult:
        ...


class ThirdPartyFilter(MutationFilter):
    def __init__(self, detector: ThirdPartyDetector):
        self.detector = detector

    def check(self, mutation: Mutation) -> FilterResult:
        sel = mutation.target.selector or ""
        el_id = mutation.target.id or ""
        if self.detector.is_third_party(sel, el_id):
            return FilterResult.block("third-party widget")
        return FilterResult.allow()


class StructureOnlyFilter(MutationFilter):
    """
    Keep ONLY mutations that add or remove elements.
    Everything else (attributes, characterData, empty childList) is skipped.
    """

    def check(self, mutation: Mutation) -> FilterResult:
        if mutation.type != "childList":
            return FilterResult.block(f"{mutation.type} (no DOM add/remove)")
        if mutation.added_count == 0 and mutation.removed_count == 0:
            return FilterResult.block("empty childList")
        return FilterResult.allow()


class CompositeFilter(MutationFilter):
    def __init__(self, filters: Iterable[MutationFilter]):
        self.filters = list(filters)

    def check(self, mutation: Mutation) -> FilterResult:
        for f in self.filters:
            r = f.check(mutation)
            if r.skip:
                return r
        return FilterResult.allow()