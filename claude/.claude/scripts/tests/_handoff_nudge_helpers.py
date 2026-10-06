"""Test helpers shared by the handoff-nudge family's test files and by test_transcript_analysis.py's
cross-subcommand table and plan-boundary tests."""
import argparse

from transcript_analysis import handoff_nudge


def _spend_over_threshold_args(since: str | None = None, projects: str = "*") -> argparse.Namespace:
    return type("A", (), {"projects": projects, "this_repo": False, "since": since})()


def _rearm_backtest_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    since: str | None = None,
    branches: str | None = None,
    no_redact: bool = False,
    extra_config_dirs: list[str] | None = None,
    spacings: str | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
        "branches": branches,
        "no_redact": no_redact,
        "extra_config_dirs": extra_config_dirs,
        "spacings": spacings,
    })()


def _ramp_curve_from_records(*sessions_records: list[dict]) -> tuple[dict[str, dict[str, float]], int]:
    """Build _ramp_curve_from_corpus's own input the way _rearm_backtest_report
    does -- one _extract_rearm_session_turns call per session's raw records --
    for TestRampCurveFromCorpus's synthetic-records tests."""
    return handoff_nudge._ramp_curve_from_corpus(handoff_nudge._extract_rearm_session_turns(recs) for recs in sessions_records)
