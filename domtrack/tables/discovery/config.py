"""Configuration for table discovery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .model import Node, ever_visible_predicate
from .policy import DiscoveryPolicy, FunctionPolicy, Interfaces


@dataclass
class DiscoveryConfig:
    """Tuning knobs plus the extension policy for :class:`TableDiscoverer`.

    The preferred extension point is ``policy``: subclass
    :class:`~domtrack.tables.discovery.policy.DiscoveryPolicy` and pass an
    instance here. The legacy ``sig`` / ``values`` / ``path_sim`` /
    ``is_visible`` callables remain supported and override the policy.
    """

    min_rows: int = 3
    min_cols: int = 2
    min_coverage_ratio: float = 0.5
    nullable_columns: bool = True
    min_column_coverage_ratio: float = 0.1
    min_column_rows: int = 2
    shape_threshold: float = 0.6
    path_threshold: float = 0.6
    feature_threshold: float = 0.1
    distance_threshold: float = 0.3
    path_feature_weight: float = 0.1
    max_type_entropy: float = 0.5
    min_unit_consistency: float = 0.7
    min_tag_majority: float = 0.8
    row_feature_weight: float = 0.3
    dedupe: bool = True
    dedupe_threshold: float = 0.8

    grouping_min_values: int = 1
    skip_row_threshold: int = 3

    policy: Optional[DiscoveryPolicy] = None
    sig: Optional[Callable[[Node, Node], Optional[tuple]]] = None
    values: Optional[Callable[[Node], list[tuple[str, str]]]] = None
    path_sim: Optional[Callable[[tuple, tuple], float]] = None
    is_visible: Optional[Callable[[Node], bool]] = None
    ever_visible: Optional[set[int]] = None

    threshold: Optional[float] = None

    def __post_init__(self) -> None:
        if self.threshold is not None:
            self.shape_threshold = self.threshold
            self.path_threshold = self.threshold

    def resolved_policy(self) -> DiscoveryPolicy:
        """Return the effective policy after applying legacy overrides."""
        base = self.policy or DiscoveryPolicy()
        is_visible = self.is_visible
        if is_visible is None and self.ever_visible is not None:
            is_visible = ever_visible_predicate(self.ever_visible)

        if (
            self.sig is None
            and self.values is None
            and self.path_sim is None
            and is_visible is None
        ):
            return base

        return FunctionPolicy(
            sig=self.sig or base.sig,
            values=self.values or base.values,
            path_sim=self.path_sim or base.path_sim,
            is_visible=is_visible or base.is_visible,
        )

    def interfaces(self) -> Interfaces:
        """Resolve the policy into the internal callable view."""
        return Interfaces.from_policy(self.resolved_policy())
