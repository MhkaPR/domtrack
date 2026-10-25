from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field

from playwright.sync_api import Browser, Page, sync_playwright

from .appliers import ApplierRegistry
from .filters import (
    CompositeFilter,
    MutationFilter,
    StructureOnlyFilter,
    ThirdPartyFilter,
)
from .locators import ElementLocator
from .models import Mutation, Trace
from .third_party import ThirdPartyDetector
from .timing import TimeContext, TimePolicy, build_policy


@dataclass
class Failure:
    time_ms: int
    mutation_type: str
    selector: str
    reason: str


@dataclass
class ReplayStats:
    applied: int = 0
    skipped: int = 0
    failed: int = 0
    skip_reasons: Counter = field(default_factory=Counter)
    failures: list[Failure] = field(default_factory=list)


@dataclass
class ReplayConfig:
    speed: float = 1.0
    mode: str = "all"
    structure_only: bool = False
    time_policy: str = "skip-aware"
    gap_cap_ms: int | None = None
    fast_load: bool = False
    headless: bool = False
    wait_for_ids_ms: int = 2000
    close_delay_ms: int = 5000

    def __post_init__(self):
        if self.mode not in ("all", "clean"):
            raise ValueError(f"mode must be 'all' or 'clean', got {self.mode!r}")
        if self.speed <= 0:
            raise ValueError(f"speed must be > 0, got {self.speed}")


class Replayer:
    def __init__(
        self,
        trace: Trace,
        config: ReplayConfig | None = None,
        third_party_detector: ThirdPartyDetector | None = None,
    ):
        self.trace = trace
        self.config = config or ReplayConfig()
        self.third_party = third_party_detector or ThirdPartyDetector()
        self.stats = ReplayStats()
        self._strategies: dict[str, int] = {}
        self._pre_filter: MutationFilter = self._build_pre_filter()
        self._policy: TimePolicy = build_policy(
            self.config.time_policy,
            self.config.speed,
            self.config.gap_cap_ms,
        )

    def _build_pre_filter(self) -> MutationFilter:
        filters: list[MutationFilter] = []
        if self.config.structure_only:
            filters.append(StructureOnlyFilter())
        if self.config.mode == "clean":
            filters.append(ThirdPartyFilter(self.third_party))
        return CompositeFilter(filters)

    # ── public ──

    def run(self) -> ReplayStats:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.config.headless)
            try:
                page = self._setup_page(browser)
                self._replay(page)
            finally:
                browser.close()
        return self.stats

    # ── internals ──

    def _setup_page(self, browser: Browser) -> Page:
        print(f"Loading page: {self.trace.meta.url}")
        page = browser.new_page(viewport=self.trace.meta.viewport)
        page.goto(self.trace.meta.url, wait_until="domcontentloaded", timeout=60000)

        if self.config.fast_load:
            page.wait_for_timeout(500)
        else:
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(1500)

        print("Page loaded.")
        self._wait_for_target_ids(page)
        return page

    def _wait_for_target_ids(self, page: Page) -> None:
        ids: set[str] = set()
        for m in self.trace.mutations:
            tid = m.target.id
            if not tid or self.third_party.is_third_party("", tid):
                continue
            ids.add(tid)
        if not ids:
            return

        timeout = self.config.wait_for_ids_ms
        print(f"Waiting up to {timeout}ms for {len(ids)} target IDs...")
        deadline = time.time() + timeout / 1000.0
        missing = set(ids)
        while missing and time.time() < deadline:
            for tid in list(missing):
                try:
                    if page.query_selector("#" + tid):
                        missing.discard(tid)
                except Exception:
                    pass
            if missing:
                page.wait_for_timeout(100)
        if missing:
            print(f"  Note: {len(missing)} IDs absent at start (may be created during replay)")
        else:
            print("  All target IDs present.")

    def _replay(self, page: Page) -> None:
        locator = ElementLocator(page)
        registry = ApplierRegistry(page, locator)
        by_t = self.trace.group_by_time()
        times = sorted(by_t.keys())

        print(f"Starting replay (time_policy={self.config.time_policy})...")
        prev_t = 0
        for i, t in enumerate(times):
            # Does this bucket contain anything that will actually run?
            has_work = any(
                not self._pre_filter.check(m).skip for m in by_t[t]
            )

            wait_ms = self._policy.compute_wait(
                TimeContext(prev_t=prev_t, curr_t=t, bucket_has_work=has_work)
            )
            if wait_ms > 0:
                page.wait_for_timeout(wait_ms)
            prev_t = t

            for mutation in by_t[t]:
                self._apply_one(page, mutation, registry)

            if (i + 1) % 25 == 0:
                print(
                    f"  [{i+1}/{len(times)}] t={t}ms  "
                    f"ok={self.stats.applied}  "
                    f"fail={self.stats.failed}  "
                    f"skip={self.stats.skipped}"
                )

        self._strategies = locator.strategy_stats
        delay_s = self.config.close_delay_ms / 1000
        print(f"Replay finished. Browser closes in {delay_s:.0f}s...")
        if self.config.close_delay_ms:
            page.wait_for_timeout(self.config.close_delay_ms)

    def _apply_one(
        self, page: Page, mutation: Mutation, registry: ApplierRegistry
    ) -> None:
        pre = self._pre_filter.check(mutation)
        if pre.skip:
            self.stats.skipped += 1
            self.stats.skip_reasons[pre.reason] += 1
            return

        result = registry.apply(page, mutation)

        if result.ok:
            self.stats.applied += 1
            return

        if result.strategy == "not found":
            sel = mutation.target.selector or ""
            el_id = mutation.target.id or ""
            if self.third_party.is_third_party(sel, el_id):
                self.stats.skipped += 1
                self.stats.skip_reasons["third-party widget"] += 1
                return

        reason = result.strategy
        if result.error:
            reason = f"{reason}: {result.error}"
        self.stats.failed += 1
        self.stats.failures.append(
            Failure(
                time_ms=mutation.t,
                mutation_type=mutation.type,
                selector=(mutation.target.selector or "")[:100],
                reason=reason,
            )
        )

    def print_report(self) -> None:
        s = self.stats
        print("\n" + "=" * 60)
        print(f"Applied : {s.applied}")
        print(f"Skipped : {s.skipped}")
        if s.skip_reasons:
            for reason, cnt in s.skip_reasons.most_common():
                print(f"  · {cnt:>4}  {reason}")
        print(f"Failed  : {s.failed}")
        print("=" * 60)

        if self._strategies:
            print("\nLookup strategies used:")
            for name, cnt in sorted(self._strategies.items(), key=lambda x: -x[1]):
                print(f"  {cnt:>4}  {name}")

        if s.failures:
            print(f"\nRemaining failures: {len(s.failures)}")
            for f in s.failures[:10]:
                print(f"  t={f.time_ms}ms [{f.mutation_type}] {f.reason}")
                print(f"    {f.selector}")