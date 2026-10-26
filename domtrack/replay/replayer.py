from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field

from playwright.sync_api import Browser, Page, sync_playwright

from .appliers import ApplierRegistry
from .event_replayer import EventReplayer
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
    replay_mode: str = "events"
    structure_only: bool = False
    time_policy: str = "skip-aware"
    gap_cap_ms: int | None = None
    fast_load: bool = False
    headless: bool = False
    wait_for_ids_ms: int = 2000
    close_delay_ms: int = 5000
    wait_before_replay_ms: int = 0
    skip_events: tuple[str, ...] = ()          # ← NEW

    def __post_init__(self):
        if self.mode not in ("all", "clean"):
            raise ValueError(f"mode must be 'all' or 'clean', got {self.mode!r}")
        if self.replay_mode not in ("events", "mutations", "both"):
            raise ValueError(
                f"replay_mode must be 'events', 'mutations' or 'both', "
                f"got {self.replay_mode!r}"
            )
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

    @classmethod
    def from_file(
        cls,
        trace_path: str,
        config: ReplayConfig | None = None,
        third_party_detector: ThirdPartyDetector | None = None,
    ) -> "Replayer":
        """Build a :class:`Replayer` directly from a trace file path."""
        return cls(
            Trace.load(trace_path),
            config=config,
            third_party_detector=third_party_detector,
        )

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

    # ── setup ──

    def _setup_page(self, browser: Browser) -> Page:
        print(f"Loading page: {self.trace.meta.url}")
        page = browser.new_page(viewport=self.trace.meta.viewport)
        page.goto(
            self.trace.meta.url,
            wait_until="domcontentloaded",
            timeout=60000,
        )

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

        if self.config.wait_before_replay_ms > 0:
            page.wait_for_timeout(self.config.wait_before_replay_ms)

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
            print(
                f"  Note: {len(missing)} IDs absent at start "
                f"(may be created during replay)"
            )
        else:
            print("  All target IDs present.")

    # ── orchestrator ──

    def _replay(self, page: Page) -> None:
        if self.config.replay_mode in ("events", "both"):
            events = self._select_events()
            if events:
                print(f"Replaying {len(events)} events...")
                ev_replayer = EventReplayer(events, self._policy)
                ev_stats = ev_replayer.replay(page)
                print(
                    f"  Events applied: {ev_stats.applied} | "
                    f"failed: {ev_stats.failed}"
                )
                for f in ev_stats.failures[:5]:
                    print(f"    ! {f}")
            else:
                print("No events to replay.")

        if self.config.replay_mode == "mutations":
            self._replay_mutations_only(page)
        elif self.config.replay_mode == "both":
            self._verify_mutations(page)

        if self.config.close_delay_ms:
            delay_s = self.config.close_delay_ms / 1000
            print(f"\nReplay finished. Browser closes in {delay_s:.0f}s...")
            page.wait_for_timeout(self.config.close_delay_ms)

    def _select_events(self):
        all_events = self.trace.events
        if not all_events:
            return []
        skip = set(self.config.skip_events)
        if not skip:
            return all_events
        kept = [e for e in all_events if e.kind not in skip]
        dropped = len(all_events) - len(kept)
        if dropped:
            print(
                f"Filtered out {dropped} event(s) — "
                f"skipped kinds: {', '.join(sorted(skip))}"
            )
        return kept

    # ── legacy: force-apply mutations (kept for 'mutations' mode) ──

    def _replay_mutations_only(self, page: Page) -> None:
        locator = ElementLocator(page)
        registry = ApplierRegistry(page, locator)
        by_t = self.trace.group_by_time()
        times = sorted(by_t.keys())

        print(f"Starting mutation replay (time_policy={self.config.time_policy})...")
        prev_t = 0
        for i, t in enumerate(times):
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

    # ── verification (for 'both' mode) ──

    def _verify_mutations(self, page: Page) -> None:
        print(f"Verifying {len(self.trace.mutations)} mutations...")
        matched = 0
        for m in self.trace.mutations:
            if self._verify_one(page, m):
                matched += 1
        total = len(self.trace.mutations)
        print(f"  Matched: {matched}/{total}")

    def _verify_one(self, page: Page, m: Mutation) -> bool:
        sel = m.target.selector
        if not sel:
            return True
        try:
            ok = page.evaluate(
                """([sel, attr, oldV, newV]) => {
                    const el = document.querySelector(sel);
                    if (!el) return false;
                    if (!attr) return true;
                    const cur = el.getAttribute(attr);
                    return cur === newV || (oldV !== newV && cur !== oldV);
                }""",
                [sel, m.attribute_name, m.old_value, m.new_value],
            )
            return bool(ok)
        except Exception:
            return False

    # ── report ──

    def print_report(self) -> None:
        s = self.stats
        print("\n" + "=" * 60)
        print(f"Mode    : {self.config.replay_mode}")
        if self.config.skip_events:
            print(f"Skip    : {', '.join(self.config.skip_events)}")
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
