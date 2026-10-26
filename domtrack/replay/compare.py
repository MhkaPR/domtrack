"""Replay a trace, record what the automated run produced, and diff it.

This is the automation loop in one call:

1. replay a *human* trace (``before``) on a fresh page, and
2. while replaying, record the page the same way a human capture would
   (``after`` — see :mod:`domtrack.replay.recording`), then
3. diff the two content-wise (:func:`domtrack.diff.diff_traces`) and report
   what changed.

    result = compare_replay("traces/before.json", ReplayConfig(speed=2.0))
    print(result.diff.summary)
    for path, entry in result.diff.added:
        print("+", path, entry.text)
"""

from __future__ import annotations

import contextlib
import sys
from dataclasses import dataclass
from typing import Optional

from ..diff.core import DiffResult, diff_traces
from ..recorder import CaptureResult
from ..trace import is_rrweb_trace, load_raw
from .recording import RecordingConfig
from .replayer import ReplayConfig, Replayer, ReplayStats
from .third_party import ThirdPartyDetector


@dataclass
class CompareResult:
    """Outcome of a replay-with-recording comparison."""

    before_path: str
    after_path: str
    after_csv_path: Optional[str]
    stats: ReplayStats
    diff: DiffResult
    replay_mutations: int = 0
    recorded_mutations: int = 0
    recorded_events: int = 0

    @property
    def summary(self) -> dict:
        """Replay counts merged with the diff tallies."""
        return {
            "replay_mutations": self.replay_mutations,
            "recorded_mutations": self.recorded_mutations,
            "recorded_events": self.recorded_events,
            **self.diff.summary,
        }


def _require_trace(path: str, raw: dict) -> None:
    if not is_rrweb_trace(raw):
        raise ValueError(f"{path}: not an rrweb-style trace (need 'snapshot' + 'records')")


def compare_traces(before_path: str, after_path: str) -> DiffResult:
    """Content diff of two trace files (``before`` vs a recorded ``after``)."""
    before = load_raw(before_path)
    after = load_raw(after_path)
    _require_trace(before_path, before)
    _require_trace(after_path, after)
    return diff_traces(before, after)


class ReplayComparer:
    """Replay a trace, record the run, and diff it against the original.

    The replay owns the browser; recording is hooked in through
    ``ReplayConfig.recording`` so the recorded page is exactly the page the
    replay drove.
    """

    def __init__(
        self,
        before_path: str,
        replay_config: Optional[ReplayConfig] = None,
        recording: Optional[RecordingConfig] = None,
        third_party_detector: Optional[ThirdPartyDetector] = None,
    ):
        self.before_path = before_path
        self.replay_config = replay_config or ReplayConfig()
        # Precedence: explicit `recording` > recording already on the config >
        # a fresh enabled default.
        self.recording = (
            recording or self.replay_config.recording or RecordingConfig()
        )
        self.recording.enabled = True
        self.third_party = third_party_detector

    def run(self) -> CompareResult:
        before = load_raw(self.before_path)
        _require_trace(self.before_path, before)
        if not (before.get("meta") or {}).get("url"):
            raise ValueError(f"{self.before_path}: URL not found in trace")

        self.replay_config.recording = self.recording
        replayer = Replayer.from_file(
            self.before_path,
            config=self.replay_config,
            third_party_detector=self.third_party,
        )
        # Progress logs belong on stderr so a caller's stdout (e.g. the
        # `domtrack compare --json` output) stays clean.
        with contextlib.redirect_stdout(sys.stderr):
            stats = replayer.run()

        record_result: Optional[CaptureResult] = replayer.record_result
        if record_result is None:
            raise RuntimeError(
                "replay did not record a trace (recording was not enabled)"
            )

        after = load_raw(record_result.json_path)
        return CompareResult(
            before_path=self.before_path,
            after_path=record_result.json_path,
            after_csv_path=record_result.csv_path,
            stats=stats,
            diff=diff_traces(before, after),
            replay_mutations=len(replayer.trace.mutations),
            recorded_mutations=record_result.mutation_count,
            recorded_events=record_result.event_count,
        )


def compare_replay(
    before_path: str,
    replay_config: Optional[ReplayConfig] = None,
    recording: Optional[RecordingConfig] = None,
    third_party_detector: Optional[ThirdPartyDetector] = None,
) -> CompareResult:
    """One-call replay + record + diff against ``before_path``.

    >>> result = compare_replay("traces/before.json", ReplayConfig(speed=2.0))
    >>> print(result.summary)
    """
    return ReplayComparer(
        before_path,
        replay_config=replay_config,
        recording=recording,
        third_party_detector=third_party_detector,
    ).run()
