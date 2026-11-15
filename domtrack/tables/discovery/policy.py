"""Class-based extension points for table discovery.

Subclass :class:`DiscoveryPolicy` to customize site-specific knowledge:

    class ShopPolicy(DiscoveryPolicy):
        def values(self, node):
            return super().values(node) + [("sku", node.attrs.get("data-sku", ""))]

        def path_sim(self, p1, p2):
            return lcs_path_sim(p1, p2)

Pass an instance through ``DiscoveryConfig(policy=ShopPolicy())``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional

from .model import (
    SKIP_TAGS,
    VALUE_ATTRS,
    Atom,
    Node,
    clean,
)


_PLACEHOLDER_VALUES: frozenset[str] = frozenset(
    {"#", "javascript:void(0)", "javascript:;"}
)


def default_values(node: Node) -> list[tuple[str, str]]:
    """Non-empty direct text plus data attributes owned by ``node``."""
    if not node.tag:
        return []
    out: list[tuple[str, str]] = []
    direct = clean("".join(c.text for c in node.children if not c.tag))
    if direct:
        out.append(("text", direct))
    for attr, atype in VALUE_ATTRS:
        value = node.attrs.get(attr)
        if value and value not in _PLACEHOLDER_VALUES:
            out.append((atype, clean(value)))
    return out


def default_sig(root: Node, node: Node) -> Optional[tuple]:
    """``(tag, classes)`` from ``root`` down to ``node`` (``node`` inclusive)."""
    if node.tag in SKIP_TAGS:
        return None
    chain: list[tuple[str, frozenset[str]]] = []
    current: Optional[Node] = node
    while current is not None:
        chain.append((current.tag, current.classes))
        if current is root:
            break
        current = current.parent
    if current is None:
        return None
    chain.reverse()
    return tuple(chain)


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 1.0
    union = len(a | b)
    return len(a & b) / union if union else 1.0


def component_similarity(c1: tuple, c2: tuple) -> float:
    tag1, set1 = c1
    tag2, set2 = c2
    return jaccard(set1, set2) if tag1 == tag2 else 0.0


def lcs_path_sim(p1: tuple, p2: tuple) -> float:
    """Weighted LCS similarity normalized by the geometric mean length."""
    if not p1 or not p2:
        return 1.0 if p1 == p2 else 0.0

    n, m = len(p1), len(p2)
    prev = [0.0] * (m + 1)
    for i in range(1, n + 1):
        cur = [0.0] * (m + 1)
        c1 = p1[i - 1]
        for j in range(1, m + 1):
            match = prev[j - 1] + component_similarity(c1, p2[j - 1])
            cur[j] = max(prev[j], cur[j - 1], match)
        prev = cur

    denom = math.sqrt(n * m)
    return prev[m] / denom if denom else 0.0


def default_is_visible(node: Node) -> bool:
    return True


# Backwards-compatible aliases.
default_path_sim = lcs_path_sim
path_similarity = lcs_path_sim


class DiscoveryPolicy:
    """Base class for site-specific discovery knowledge.

    Override any method in a subclass. The defaults reproduce the historical
    function-based behaviour.
    """

    sig = staticmethod(default_sig)
    values = staticmethod(default_values)
    path_sim = staticmethod(lcs_path_sim)
    is_visible = staticmethod(default_is_visible)


class FunctionPolicy(DiscoveryPolicy):
    """Adapter that lets callables override individual policy methods."""

    def __init__(
        self,
        sig: Optional[Callable] = None,
        values: Optional[Callable] = None,
        path_sim: Optional[Callable] = None,
        is_visible: Optional[Callable] = None,
    ) -> None:
        if sig is not None:
            self.sig = sig
        if values is not None:
            self.values = values
        if path_sim is not None:
            self.path_sim = path_sim
        if is_visible is not None:
            self.is_visible = is_visible


@dataclass(frozen=True)
class Interfaces:
    """Resolved callable view used by the engine internals."""

    sig: Callable[[Node, Node], Optional[tuple]]
    values: Callable[[Node], list[tuple[str, str]]]
    path_sim: Callable[[tuple, tuple], float]
    is_visible: Callable[[Node], bool]

    @classmethod
    def from_policy(cls, policy: DiscoveryPolicy) -> "Interfaces":
        return cls(
            sig=policy.sig,
            values=policy.values,
            path_sim=policy.path_sim,
            is_visible=policy.is_visible,
        )
