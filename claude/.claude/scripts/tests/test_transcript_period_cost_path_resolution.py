"""period_cost.py's per-transcript path resolution: a transcript or root whose `Path.resolve` raises."""
from pathlib import Path

import pytest
from transcript_analysis import period_cost

from ._period_cost_helpers import _RESOLVE_ERRORS


class TestDistinctSessionsResolveFailures:
    """A transcript or root whose `Path.resolve` raises is handled without aborting the scan. On some interpreter versions
    non-strict `Path.resolve` does not raise on a symlink loop, so the injection stands in for the `RuntimeError` and
    `OSError` cases of older interpreters."""

    _ROOT = Path("/srv/account/projects")
    _TRANSCRIPT = _ROOT / "project-a" / "session-1.jsonl"

    @staticmethod
    def _resolve_raises_for(monkeypatch, failing_path: Path, error: Exception) -> None:
        def resolve(self, strict=False):
            if self == failing_path:
                raise error
            return self

        monkeypatch.setattr(Path, "resolve", resolve)

    @pytest.mark.parametrize("error", _RESOLVE_ERRORS)
    def test_a_transcript_whose_path_cannot_be_resolved_is_dropped_and_counted(self, monkeypatch, error):
        self._resolve_raises_for(monkeypatch, self._TRANSCRIPT, error)
        tally = period_cost._ScanTally()

        kept = list(period_cost._distinct_sessions([(self._TRANSCRIPT, [])], self._ROOT, tally))

        assert kept == []
        assert (tally.dropped, tally.transcripts) == (1, 0)

    @pytest.mark.parametrize("error", _RESOLVE_ERRORS)
    def test_an_unresolvable_root_falls_back_to_its_given_path_for_the_outside_root_count(self, monkeypatch, error):
        outside = Path("/srv/elsewhere/session-2.jsonl")
        self._resolve_raises_for(monkeypatch, self._ROOT, error)
        tally = period_cost._ScanTally()

        kept = list(period_cost._distinct_sessions([(self._TRANSCRIPT, []), (outside, [])], self._ROOT, tally))

        assert kept == [(self._TRANSCRIPT, []), (outside, [])]
        assert (tally.dropped, tally.transcripts, tally.outside_root_transcripts) == (0, 2, 1)
