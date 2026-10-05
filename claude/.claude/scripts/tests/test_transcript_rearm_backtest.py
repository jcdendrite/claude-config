"""Tests for transcript_analysis/rearm_backtest.py: boundary detection, spacing replay, --spacings
parsing, and the report end to end."""
import argparse
import errno
import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

from .conftest import (
    _asst,
    _bash_use,
    _cost_args,
    _extract_grand_total,
    _priced,
    _table_cols,
    _tool_result,
    _user_msg,
    _write_cost_root,
    _write_jsonl,
)

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


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


def _tool_use_asst(model: str, tool_id: str, *, output: int = 100, ts: str = "2026-05-19T10:00:00.000Z") -> dict:
    """A priced main-thread assistant turn carrying a real Bash tool_use block
    -- _priced's content=[] default can't exercise _hook_observable_boundaries,
    which needs a tool_use/tool_result content shape (not just known usage) to
    tell a tool-call-only stretch apart from a genuine user message."""
    return _priced(model, output=output, ts=ts, content=[_bash_use(tool_id, "echo hi")])


class TestHookObservableBoundaries:
    def test_tool_call_only_stretch_produces_no_mid_stretch_boundary(self):
        """Three tool_use turns chained by tool_result-bearing user records,
        then one genuine user message, contribute exactly one internal
        boundary -- at position 3 (after the third turn), not one per turn --
        since Stop only fires once the agent yields back to the user."""
        records = [
            _tool_use_asst("claude-sonnet-5", "t1"),
            _user_msg([_tool_result("t1", "ok")]),
            _tool_use_asst("claude-sonnet-5", "t2"),
            _user_msg([_tool_result("t2", "ok")]),
            _tool_use_asst("claude-sonnet-5", "t3"),
            _user_msg("please continue"),
        ]
        assert _mod.rearm_backtest._hook_observable_boundaries(records) == [0, 3]

    def test_genuine_multi_turn_conversation_produces_one_boundary_per_turn(self):
        """Each turn immediately followed by a genuine user message
        contributes its own boundary."""
        records = [
            _priced("claude-sonnet-5", output=100), _user_msg("go on"),
            _priced("claude-sonnet-5", output=100), _user_msg("go on"),
            _priced("claude-sonnet-5", output=100), _user_msg("go on"),
        ]
        assert _mod.rearm_backtest._hook_observable_boundaries(records) == [0, 1, 2, 3]

    def test_session_end_with_no_trailing_user_message_still_surfaces_boundary(self):
        """A session whose last record is an assistant turn with no further
        user message still gets a boundary at session end -- the case
        nudge-handoff-near-context-cap.sh's own Stop registration exists to
        cover (docs/handoff-nudge.md: "registered on both events so a session
        that crosses the threshold on its final turn... still gets warned")."""
        records = [
            _user_msg("go"),
            _priced("claude-sonnet-5", output=100),
            _priced("claude-sonnet-5", output=100),
        ]
        assert _mod.rearm_backtest._hook_observable_boundaries(records) == [0, 2]


class TestSimulateRearmSpacing:
    def test_hand_computed_dollar_total_with_a_single_split(self):
        """One band crossing splits the session into an actual-priced prefix
        and a ramp-priced remainder -- hand-computed against a synthetic ramp
        curve, not a bounds check against baseline or a naive reprice."""
        ramp_curve = {label: {"rate": 1.0, "mean_context": 0.0} for label in _mod.handoff_nudge._RAMP_CURVE_BUCKET_LABELS}
        ramp_curve["0-5"] = {"rate": 2.0, "mean_context": 100.0}
        turns = [
            (0, 50, 5.0),
            (50, 60, 6.0),      # abs=110 >= threshold(100) at boundary 2 -> split after this turn
            (110, 10, 999.0),   # post-split turn 0: priced at ramp rate, not the (unreachable) actual 999.0
        ]
        boundaries = [0, 1, 2, 3]
        total, _ctx_weighted, weight = _mod.rearm_backtest._simulate_rearm_spacing(
            turns, boundaries, spacing=50, ramp_curve=ramp_curve, threshold=100,
        )
        assert total == pytest.approx(5.0 + 6.0 + (10 / 1000 * 2.0))
        assert weight == 50 + 60 + 10

    def test_two_sequential_rearms_within_one_session(self):
        """A remainder that itself crosses a second band splits again -- the
        compounding re-arm this feature exists to model, distinct from a
        one-shot baseline that only ever splits once."""
        ramp_curve = {label: {"rate": 1.0, "mean_context": 0.0} for label in _mod.handoff_nudge._RAMP_CURVE_BUCKET_LABELS}
        turns = [
            (0, 50, 5.0),
            (50, 60, 6.0),       # split 1 after this turn (abs=110 >= 100)
            (110, 10, 999.0),    # ramp-priced, turns-since-restart 0
            (120, 200, 999.0),   # abs=320 >= 150 (100 + 1*50) -> split 2 after this turn
            (320, 5, 999.0),     # ramp-priced again, turns-since-restart 0 (post split 2)
        ]
        boundaries = [0, 1, 2, 3, 4, 5]
        total, _ctx_weighted, weight = _mod.rearm_backtest._simulate_rearm_spacing(
            turns, boundaries, spacing=50, ramp_curve=ramp_curve, threshold=100,
        )
        expected = 5.0 + 6.0 + (10 / 1000 * 1.0) + (200 / 1000 * 1.0) + (5 / 1000 * 1.0)
        assert total == pytest.approx(expected)
        assert weight == 50 + 60 + 10 + 200 + 5

    def test_response_lag_delays_the_split_point(self):
        """response_lag_tokens shifts a band's trigger point later -- the
        compliance-realistic model's operator-response-lag correction."""
        ramp_curve = {label: {"rate": 5.0, "mean_context": 0.0} for label in _mod.handoff_nudge._RAMP_CURVE_BUCKET_LABELS}
        turns = [
            (0, 50, 5.0),
            (50, 60, 6.0),
            (110, 20, 7.0),
        ]
        boundaries = [0, 1, 2, 3]
        total_no_lag, _c1, _w1 = _mod.rearm_backtest._simulate_rearm_spacing(
            turns, boundaries, spacing=50, ramp_curve=ramp_curve, threshold=100, response_lag_tokens=0,
        )
        total_with_lag, _c2, _w2 = _mod.rearm_backtest._simulate_rearm_spacing(
            turns, boundaries, spacing=50, ramp_curve=ramp_curve, threshold=100, response_lag_tokens=20,
        )
        # No lag: the crossing fires after turn index 1 (abs=110 >= 100), so
        # turn index 2's dollars are ramp-priced (20/1000*5.0=0.1) instead of actual (7.0).
        assert total_no_lag == pytest.approx(5.0 + 6.0 + 0.1)
        # With a 20-token lag, that same crossing isn't detectable until
        # abs>=120, which only happens after turn index 2 -- too late for any
        # turn to be re-priced, so every turn keeps its actual dollars.
        assert total_with_lag == pytest.approx(5.0 + 6.0 + 7.0)
        assert total_with_lag > total_no_lag


class TestParseRearmSpacingsArg:
    def test_default_value_when_spacings_is_unset(self):
        """--spacings absent falls back to _REARM_BACKTEST_DEFAULT_SPACINGS."""
        assert _mod.rearm_backtest._parse_rearm_spacings_arg(argparse.Namespace(spacings=None)) == list(
            _mod.rearm_backtest._REARM_BACKTEST_DEFAULT_SPACINGS
        )

    def test_non_integer_token_exits_2(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            _mod.rearm_backtest._parse_rearm_spacings_arg(argparse.Namespace(spacings="40000,not-a-number"))
        assert exc_info.value.code == 2
        assert "expected comma-separated integers" in capsys.readouterr().err

    def test_non_positive_value_exits_2(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            _mod.rearm_backtest._parse_rearm_spacings_arg(argparse.Namespace(spacings="40000,0"))
        assert exc_info.value.code == 2
        assert "values must be positive" in capsys.readouterr().err

    def test_whitespace_only_value_exits_2_at_least_one_required(self, capsys):
        """A --spacings value that's non-empty but strips to nothing on
        every comma-separated token (all whitespace) leaves the parsed list
        empty -- the same "at least one required" exit as an entirely blank
        flag, not a silent empty result."""
        with pytest.raises(SystemExit) as exc_info:
            _mod.rearm_backtest._parse_rearm_spacings_arg(argparse.Namespace(spacings="   "))
        assert exc_info.value.code == 2
        assert "at least one spacing value is required" in capsys.readouterr().err


class TestRearmBacktestReport:
    """End-to-end coverage against .claude/plans/handoff-nudge-rearm-backtest.md's
    Verification section -- items 2, 4, and 5, encoded as pytests against a
    shared fixture corpus rather than a one-time manual run."""

    def test_baseline_dollars_match_cost_reports_own_total(self, fake_projects, capsys):
        """Verification item 2: the baseline row (today's real recorded
        totals, no re-arm simulation) must equal _cost_report's own total for
        the same fixture scope -- an independent, already-verified code path
        computing the same real, non-counterfactual dollars over the same
        corpus should agree."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=500_000, output=5_000, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-sonnet-5", input=500_000, output=5_000, ts="2026-05-19T10:01:00.000Z"),
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        cost_total = _extract_grand_total(capsys.readouterr().out)

        _mod.rearm_backtest._rearm_backtest_report(_rearm_backtest_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Spacing", row_contains="baseline")
        baseline_total = float(cols["$"].replace(",", ""))
        assert baseline_total == pytest.approx(cost_total)

    def test_prints_fixed_threshold_and_model_routing_disclosure(self, fake_projects, capsys):
        """Verification item 5: the report explicitly states that model
        routing and the fixed fire threshold are not backtested."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=100_000, output=1_000, ts="2026-05-19T10:00:00.000Z"),
        ])
        _mod.rearm_backtest._rearm_backtest_report(_rearm_backtest_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "Model routing and each session's own fire threshold" in out
        assert "NOT backtested" in out

    def test_excluded_operator_lag_count_is_reported(self, fake_projects, tmp_path, capsys):
        """Verification item 4: a nudged log line that can't be joined to any
        session in scope is counted in the excluded figure, not silently
        dropped."""
        (tmp_path / ".handoff-nudge.log").write_text(
            "nudged session=not-in-scope est=100000 model=claude-sonnet-5 window=1000000 event=Stop\n"
        )
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=100_000, output=1_000, ts="2026-05-19T10:00:00.000Z"),
        ])
        _mod.rearm_backtest._rearm_backtest_report(_rearm_backtest_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "1 excluded" in out

    def test_unresolvable_config_dir_exits_cleanly(self, capsys, monkeypatch):
        """An unresolvable config dir (e.g. $HOME unset) exits 2 with a
        diagnostic, rather than an uncaught ValueError traceback --
        _resolve_cost_roots's own stderr+exit(2) convention. Exercised via
        cmd_rearm_backtest: _rearm_backtest_report takes scan_roots as an
        already-resolved argument, so it never calls config_dir() itself
        and can't raise this error directly."""

        def _raise_value_error():
            raise ValueError("HOME is unset or empty, and CLAUDE_CONFIG_DIR is not set")

        monkeypatch.setattr(_mod.scope, "config_dir", _raise_value_error)
        with pytest.raises(SystemExit) as exc_info:
            _mod.rearm_backtest.cmd_rearm_backtest(_rearm_backtest_args())
        assert exc_info.value.code == 2
        assert "HOME is unset or empty" in capsys.readouterr().err

    def test_200k_window_session_re_arms_off_its_own_80k_threshold(self, fake_projects, capsys):
        """A session on a 200k-context-window model crosses its own real fire
        point (80,000) well under _HANDOFF_NUDGE_ABS_CAP (150,000) -- a
        report that used the cap uniformly for every session would never
        simulate a split for this session at all, understating the re-arm
        benefit on the 200k-window arm entirely."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-4-5", input=70_000, output=5_000, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-sonnet-4-5", input=80_000, output=5_000, ts="2026-05-19T10:01:00.000Z"),
            _user_msg("continue", ts="2026-05-19T10:02:00.000Z"),
            _priced("claude-sonnet-4-5", input=90_000, output=5_000, ts="2026-05-19T10:03:00.000Z"),
        ])
        _mod.rearm_backtest._rearm_backtest_report(_rearm_backtest_args(spacings="40000"), date(2026, 8, 2))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Spacing", row_contains=["40,000", "perfect"])
        assert cols["DeltaUSD"] != "-0.00", "40k-spacing row must diverge from baseline once the 80k threshold fires"

    def test_warns_when_no_priced_output_tokens_are_in_scope_for_the_ramp_curve(self, fake_projects, capsys):
        """A corpus with only unpriced-model turns can't derive a real ramp
        curve -- every re-armed remainder would otherwise be silently priced
        at $0 with nothing distinguishing "genuinely cheap ramp" from "curve
        couldn't be computed at all"."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-opus-4-7", input=100_000, output=1_000, ts="2026-05-19T10:00:00.000Z"),
        ])
        _mod.rearm_backtest._rearm_backtest_report(_rearm_backtest_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "WARNING" in out
        assert "ramp curve could not be computed" in out

    def test_synthetic_no_usage_record_does_not_desync_boundaries_from_main_thread_turns(
        self, fake_projects, capsys
    ):
        """A main-thread assistant record with no usage block (a synthetic
        error record) sits between two real, priced turns -- if it were to
        advance _hook_observable_boundaries' own turn-count position (as it
        would if that function's usage-block guard were ever lost), the
        crossing right after the first real turn would never line up with
        any boundary this report's own main_thread_turns list can use, and
        the second turn would silently keep its actual (unrepriced) dollars.
        Runs the real pipeline end to end and checks a hand-computed dollar
        total, not merely a nonzero delta, so an index mismatch between the
        two functions actually fails the test."""
        threshold = _mod.handoff_nudge._hook_effective_fire_threshold("claude-sonnet-5")
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-5", ts="2026-05-19T10:00:00.000Z"),  # no usage block
            _priced("claude-sonnet-5", input=threshold, output=5_000, ts="2026-05-19T10:00:01.000Z"),
            _user_msg("continue", ts="2026-05-19T10:00:02.000Z"),
            _priced("claude-sonnet-5", input=500, output=2_000, ts="2026-05-19T10:00:03.000Z"),
        ])
        _mod.rearm_backtest._rearm_backtest_report(_rearm_backtest_args(spacings="40000"), date(2026, 8, 2))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Spacing", row_contains=["40,000", "perfect"])
        total = float(cols["$"].replace(",", ""))

        rates = _mod._model_rates("claude-sonnet-5")
        turn0_dollars = threshold / 1_000_000 * rates["input"] + 5_000 / 1_000_000 * rates["output"]
        turn1_dollars = 500 / 1_000_000 * rates["input"] + 2_000 / 1_000_000 * rates["output"]
        # Turn 0's own abs-tokens (threshold input + 5,000 output) clears the
        # model's fire threshold (its 1M-window 40% figure exceeds
        # _HANDOFF_NUDGE_ABS_CAP, so the cap governs), so turn 1 is
        # ramp-priced at the "0-5" bucket's rate -- which, since both turns
        # land in that bucket, is their own blended $/1k-output rate.
        ramp_rate = (turn0_dollars + turn1_dollars) / ((5_000 + 2_000) / 1000)
        expected_total = turn0_dollars + (2_000 / 1000) * ramp_rate
        # abs= accounts for the table's own 2-decimal-place rounding
        # ($X,XXX.XX), not slack in the expected computation itself.
        assert total == pytest.approx(expected_total, abs=0.005)

    def test_conversion_section_never_prints_session_ids(self, fake_projects, tmp_path, capsys):
        """Redaction: the conversion section's output carries no raw session
        id, matching the pooled, pseudonymous-aggregate discipline the
        spacing table already follows. Also asserts the session was
        actually classified voluntary, not silently dropped -- redaction
        alone can't be credited if the session never reached a bucket."""
        (tmp_path / ".handoff-nudge.log").write_text(
            "nudged session=super-secret-session est=100000 model=claude-sonnet-5"
            " window=1000000 event=Stop\n"
            "handoff session=super-secret-session\n"
        )
        _write_jsonl(fake_projects / "super-secret-session.jsonl", [
            _priced("claude-sonnet-5", input=100_000, output=1_000, ts="2026-05-19T10:00:00.000Z"),
        ])
        _mod.rearm_backtest._rearm_backtest_report(_rearm_backtest_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "super-secret-session" not in out
        assert "Fired sessions in scope: 1 (0 dropped -- no in-scope trace)" in out
        assert _table_cols(out, header_contains="Bucket", row_contains="voluntary")["Count"] == "1"

    def test_root_aware_join_reads_every_root_own_log_not_just_the_default(
        self, fake_projects, fake_config_dir_factory, tmp_path, capsys
    ):
        """A session logged only under a second declared root's own log must
        still join -- a fixture that reads only the default root's log would
        leave this session's nudged line unjoined and silently understate
        both the lag sample and the conversion population. Also asserts
        redaction holds at multi-root scope: neither root's own session id
        leaks into the printed report."""
        _write_jsonl(fake_projects / "sess-a.jsonl", [
            _priced("claude-sonnet-5", input=100_000, output=1_000, ts="2026-05-19T10:00:00.000Z"),
        ])
        (tmp_path / ".handoff-nudge.log").write_text(
            "nudged session=sess-a est=100000 model=claude-sonnet-5 window=1000000 event=Stop\n"
        )

        acct_b = fake_config_dir_factory("acct-b")
        proj_b = acct_b / "projects" / "-home-user-other-repo"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _priced("claude-sonnet-5", input=100_000, output=1_000, ts="2026-05-19T10:00:00.000Z"),
        ])
        (acct_b / ".handoff-nudge.log").write_text(
            "nudged session=sess-b est=100000 model=claude-sonnet-5 window=1000000 event=Stop\n"
        )

        _mod.rearm_backtest._rearm_backtest_report(
            _rearm_backtest_args(), date(2026, 8, 2), roots=[fake_projects.parent, acct_b / "projects"]
        )
        out = capsys.readouterr().out
        assert "Operator-response-lag sample: 2 joined" in out
        cols = _table_cols(out, header_contains="Bucket", row_contains="no-compliance-observed")
        assert cols["Count"] == "2"
        # Redaction: each root's own distinctive session id must not leak
        # into the printed report, the multi-root counterpart to
        # test_conversion_section_never_prints_session_ids' single-root
        # assertion.
        assert "sess-a" not in out
        assert "sess-b" not in out

    def test_multi_root_session_pricing_aggregates_across_every_root(
        self, fake_projects, fake_config_dir_factory, capsys
    ):
        """The Spacing table's Sessions in scope count and baseline $ total
        are the report's primary output. A regression that narrowed
        session aggregation back to one root -- the mirror-image of the
        defect this diff's root-aware log join exists to fix -- would
        silently understate both. Each root contributes one priced
        session so a single-root regression halves the expected total,
        not merely rounds it."""
        _write_jsonl(fake_projects / "sess-a.jsonl", [
            _priced("claude-sonnet-5", input=500_000, output=5_000, ts="2026-05-19T10:00:00.000Z"),
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        single_session_dollars = _extract_grand_total(capsys.readouterr().out)

        acct_b = fake_config_dir_factory("acct-b")
        proj_b = acct_b / "projects" / "-home-user-other-repo"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _priced("claude-sonnet-5", input=500_000, output=5_000, ts="2026-05-19T10:00:00.000Z"),
        ])

        _mod.rearm_backtest._rearm_backtest_report(
            _rearm_backtest_args(), date(2026, 8, 2), roots=[fake_projects.parent, acct_b / "projects"]
        )
        out = capsys.readouterr().out
        assert "Sessions in scope: 2" in out
        cols = _table_cols(out, header_contains="Spacing", row_contains="baseline")
        baseline_total = float(cols["$"].replace(",", ""))
        assert baseline_total == pytest.approx(2 * single_session_dollars)

    def test_root_with_no_log_file_contributes_zero_entries_without_raising(
        self, fake_projects, fake_config_dir_factory, tmp_path, capsys
    ):
        """Exercises _read_bounded_log_lines' absent-file path: a root with
        no .handoff-nudge.log at all must contribute zero entries rather
        than raising, while a sibling root's log line still joins
        normally. Also confirms the log-less root contributes 0 bytes to
        the pooled multi-root byte total instead of omitting the root or
        erroring."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=100_000, output=1_000, ts="2026-05-19T10:00:00.000Z"),
        ])
        log_path = tmp_path / ".handoff-nudge.log"
        log_path.write_text(
            "nudged session=sess est=100000 model=claude-sonnet-5 window=1000000 event=Stop\n"
        )
        acct_b = fake_config_dir_factory("acct-b")  # no .handoff-nudge.log written for this root
        acct_b_root = acct_b / "projects"
        _mod.rearm_backtest._rearm_backtest_report(
            _rearm_backtest_args(), date(2026, 8, 2), roots=[fake_projects.parent, acct_b_root]
        )
        out = capsys.readouterr().out
        assert "Operator-response-lag sample: 1 joined" in out
        assert f"nudge logs across every resolved root: {log_path.stat().st_size:,} bytes" in out

    @pytest.mark.parametrize(
        "unreadable_error",
        [
            PermissionError(13, "Permission denied"),
            OSError(errno.ESTALE, "Stale file handle"),
        ],
        ids=["permission-denied", "estale"],
    )
    def test_unreadable_root_nudge_log_is_flagged_unreadable_without_crash_or_path_leak(
        self, fake_projects, fake_config_dir_factory, tmp_path, capsys, monkeypatch, unreadable_error
    ):
        """An OSError from an unreadable log must not crash the report or
        leak the root's path, and a sibling readable root's bytes must
        still be folded into the pooled multi-root total. Uses a targeted
        Path.exists() monkeypatch rather than chmod, since chmod-ing the
        whole account directory would also block the unrelated
        project-dir scan."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=100_000, output=1_000, ts="2026-05-19T10:00:00.000Z"),
        ])
        log_path = tmp_path / ".handoff-nudge.log"
        log_path.write_text(
            "nudged session=sess est=100000 model=claude-sonnet-5 window=1000000 event=Stop\n"
        )
        acct_b = fake_config_dir_factory("acct-b")
        acct_b_root = acct_b / "projects"
        unreadable_log = acct_b / ".handoff-nudge.log"
        unreadable_log.write_text(
            "nudged session=sess-b est=100000 model=claude-sonnet-5 window=1000000 event=Stop\n"
        )

        real_exists = Path.exists

        def fake_exists(self, *args, **kwargs):
            if self == unreadable_log:
                raise unreadable_error
            return real_exists(self, *args, **kwargs)

        monkeypatch.setattr(Path, "exists", fake_exists)

        # Unreadable root iterates first so a regression turning `continue`
        # into `break` would abort before the readable sibling ever
        # contributes to the pooled total.
        _mod.rearm_backtest._rearm_backtest_report(
            _rearm_backtest_args(), date(2026, 8, 2), roots=[acct_b_root, fake_projects.parent]
        )
        out, _err = capsys.readouterr()
        assert str(acct_b) not in out
        assert "account-" not in out  # no per-account byte-size breakdown under multi-root scope
        assert (
            f"nudge logs across every resolved root: {log_path.stat().st_size:,} bytes"
            " (some roots unreadable)" in out
        )

    def test_truncation_can_drop_an_early_block_line_causing_real_misclassification(
        self, fake_projects, tmp_path, capsys, monkeypatch
    ):
        """Truncation dropping an early `action=block` line while a later
        plain nudged line and the handoff line survive misclassifies the
        session voluntary instead of forced. Asserted against the Bucket
        table itself, not just the truncation banner."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=100_000, output=1_000, ts="2026-05-19T10:00:00.000Z"),
        ])
        early_nudge = "nudged session=sess est=100000 model=claude-sonnet-5 window=1000000 event=Stop"
        early_block = (
            "nudged session=sess est=200000 model=claude-sonnet-5 window=1000000"
            " event=PostToolBatch action=block"
        )
        later_nudge = "nudged session=sess est=300000 model=claude-sonnet-5 window=1000000 event=Stop"
        handoff_line = "handoff session=sess"
        surviving_tail = f"{later_nudge}\n{handoff_line}\n"
        log_path = tmp_path / ".handoff-nudge.log"
        log_path.write_text(f"{early_nudge}\n{early_block}\n{surviving_tail}")
        # Sized to the exact byte length of the surviving tail so the tail
        # read boundary lands on a real newline, not mid-line -- isolating
        # the early_nudge/early_block drop from any partial-line noise.
        monkeypatch.setattr(_mod.handoff_nudge, "_NUDGE_LOG_MAX_READ", len(surviving_tail.encode()))

        _mod.rearm_backtest._rearm_backtest_report(_rearm_backtest_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "[truncated -- oldest lines dropped]" in out
        cols = _table_cols(out, header_contains="Bucket", row_contains="voluntary")
        assert cols["Count"] == "1"

    def test_single_root_byte_size_line_prints_the_real_ordinal_and_byte_count(
        self, fake_projects, tmp_path, capsys
    ):
        """The single-root allow-path companion to the multi-root pooling
        tests below: the default `redact=True` byte-size line must carry
        the real ordinal and the real byte count, not just omit a raw
        path -- content, not just absence, needs a test."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=100_000, output=1_000, ts="2026-05-19T10:00:00.000Z"),
        ])
        log_path = tmp_path / ".handoff-nudge.log"
        log_path.write_text("schema-drift session=x event=Stop\n")
        _mod.rearm_backtest._rearm_backtest_report(_rearm_backtest_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        ordinal = _mod._redaction_ordinals([fake_projects.parent])[fake_projects.parent.resolve()]
        assert f"account-{ordinal} nudge log: {log_path.stat().st_size:,} bytes" in out

    def test_conversion_bucket_and_rate_arithmetic_matches_hand_computed_counts(
        self, fake_projects, tmp_path, capsys
    ):
        """The printed percentage strings (Conversion rate, Block-reach rate,
        Join validity) are verified end-to-end against a hand-computed mixed
        corpus here; the bucket-exhaustiveness and derived-rate arithmetic
        invariant itself is covered directly against
        _nudge_conversion_from_log's own return dict in
        TestNudgeConversionFromLog. The appended conversion section must
        also not disturb the pre-existing Spacing table's own `_table_cols`
        parsing. Bucket sizes are pairwise distinct (5/1/3/7), so a
        bucket-swap regression in the rate formula changes the asserted
        percentage rather than passing coincidentally."""
        sessions = (
            [f"voluntary-{i}" for i in range(1, 6)]
            + ["forced-1"]
            + [f"blocked-{i}" for i in range(1, 4)]
            + [f"nocompliance-{i}" for i in range(1, 8)]
        )
        for session_id in sessions:
            _write_jsonl(fake_projects / f"{session_id}.jsonl", [
                _priced("claude-sonnet-5", input=100, output=100, ts="2026-05-19T10:00:00.000Z"),
            ])
        # dropped-1 has a log line but no transcript -- excluded from every bucket.
        log_lines = [
            "nudged session=voluntary-1 est=100 model=claude-sonnet-5 window=1000000 event=Stop ignored=3",
            "handoff session=voluntary-1",
        ]
        for i in range(2, 6):
            log_lines += [
                f"nudged session=voluntary-{i} est=100 model=claude-sonnet-5 window=1000000 event=Stop",
                f"handoff session=voluntary-{i}",
            ]
        log_lines += [
            "nudged session=forced-1 est=100 model=claude-sonnet-5 window=1000000 event=Stop",
            "nudged session=forced-1 est=200 model=claude-sonnet-5 window=1000000 event=PostToolBatch action=block",
            "handoff session=forced-1",
        ]
        log_lines += [
            f"nudged session=blocked-{i} est=100 model=claude-sonnet-5 window=1000000 event=PostToolBatch action=block"
            for i in range(1, 4)
        ]
        log_lines += [
            f"nudged session=nocompliance-{i} est=100 model=claude-sonnet-5 window=1000000 event=Stop"
            for i in range(1, 8)
        ]
        log_lines.append("nudged session=dropped-1 est=100 model=claude-sonnet-5 window=1000000 event=Stop")
        (tmp_path / ".handoff-nudge.log").write_text("\n".join(log_lines) + "\n")

        _mod.rearm_backtest._rearm_backtest_report(_rearm_backtest_args(), date(2026, 8, 2))
        out = capsys.readouterr().out

        assert "Fired sessions in scope: 16 (1 dropped -- no in-scope trace)" in out
        assert _table_cols(out, header_contains="Bucket", row_contains="voluntary")["Count"] == "5"
        assert _table_cols(out, header_contains="Bucket", row_contains="forced")["Count"] == "1"
        assert _table_cols(out, header_contains="Bucket", row_contains="blocked-no-handoff")["Count"] == "3"
        assert _table_cols(out, header_contains="Bucket", row_contains="no-compliance-observed")["Count"] == "7"
        assert "Conversion rate (voluntary + forced / fired): 37.5% (6/16)" in out
        assert "Block-reach rate (forced + blocked-no-handoff / fired): 25.0% (4/16)" in out
        assert "Join validity (fired sessions with a matching handoff line): 6" in out
        assert (
            "Re-arms tolerated at voluntary compliance: median ignored=3 across 1 voluntary session(s)"
            " (4 voluntary session(s) missing ignored=)" in out
        )
        spacing_cols = _table_cols(out, header_contains="Spacing", row_contains="baseline")
        assert spacing_cols["$"] != ""

    def test_non_overlapping_session_ids_report_states_zero_join_validity(
        self, fake_projects, tmp_path, capsys
    ):
        """Report-level check for a systematic session-id mismatch between
        the two writers: well-formed, never-coincident session ids
        (hookid-A nudged, pidwalk-B handoff) must print join validity 0,
        not merely return it from _nudge_conversion_from_log. The
        pure-function half is pinned separately by
        TestNudgeConversionFromLog.test_non_overlapping_session_ids_yield_zero_join_validity."""
        _write_jsonl(fake_projects / "hookid-A.jsonl", [
            _priced("claude-sonnet-5", input=100_000, output=1_000, ts="2026-05-19T10:00:00.000Z"),
        ])
        (tmp_path / ".handoff-nudge.log").write_text(
            "nudged session=hookid-A est=100000 model=claude-sonnet-5"
            " window=1000000 event=Stop\n"
            "handoff session=pidwalk-B\n"
        )
        _mod.rearm_backtest._rearm_backtest_report(_rearm_backtest_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "Join validity (fired sessions with a matching handoff line): 0" in out

    def test_zero_fired_sessions_prints_the_degenerate_conversion_branch_text(self, fake_projects, capsys):
        """No .handoff-nudge.log at all (an account that has never fired a
        nudge) still yields a priced spacing table plus a conversion section
        reporting zero fired sessions -- _pct_of already guards the 0/0
        division (transcript_analysis/render.py), so this pins the exact
        wording of both degenerate-count branches rather than merely
        confirming no crash."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=100_000, output=1_000, ts="2026-05-19T10:00:00.000Z"),
        ])
        _mod.rearm_backtest._rearm_backtest_report(_rearm_backtest_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "Fired sessions in scope: 0 (0 dropped -- no in-scope trace)" in out
        assert (
            "Re-arms tolerated at voluntary compliance: no voluntary session(s) with ignored="
            " present (0 voluntary session(s) missing ignored=)" in out
        )

    def test_current_format_action_block_line_with_ignored_and_skills_parses_through_full_stack(
        self, fake_projects, tmp_path, capsys
    ):
        """Routes a current-format action=block line (ignored=/skills=, per
        nudge-handoff-near-context-cap.sh:644) through the real text-log
        parser into _nudge_conversion_from_log, so a field-name or type
        regression in that parsing path fails here."""
        _write_jsonl(fake_projects / "forced-tele.jsonl", [
            _priced("claude-sonnet-5", input=100, output=100, ts="2026-05-19T10:00:00.000Z"),
        ])
        (tmp_path / ".handoff-nudge.log").write_text(
            "nudged session=forced-tele est=100 model=claude-sonnet-5 window=1000000"
            " event=PostToolBatch ignored=2 skills=handoff,memory-skill action=block\n"
            "handoff session=forced-tele\n"
        )
        _mod.rearm_backtest._rearm_backtest_report(_rearm_backtest_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "Fired sessions in scope: 1 (0 dropped -- no in-scope trace)" in out
        assert _table_cols(out, header_contains="Bucket", row_contains="forced")["Count"] == "1"

    def test_malformed_empty_session_value_is_silently_dropped_through_the_real_parser(
        self, tmp_path
    ):
        """Routes a real .handoff-nudge.log line with an empty session=
        value (a hook payload bug shape) through the real text-log parser,
        then straight into _nudge_conversion_from_log with a hand-built
        session_traces dict -- exercising the same parsing regression
        surface as the full-stack pattern, without the report/table-text
        layer above it. _parse_nudge_log_entries checks key presence, not
        truthiness, so this line survives parsing with session=="". It
        then fails the `if session:` guard downstream, landing in no
        bucket -- not even dropped. The classification must not crash or
        inflate any count as a result."""
        log_path = tmp_path / ".handoff-nudge.log"
        log_path.write_text(
            "nudged session= est=100 model=claude-sonnet-5 window=1000000 event=Stop\n"
            "nudged session=voluntary-tele est=100 model=claude-sonnet-5 window=1000000 event=Stop\n"
            "handoff session=voluntary-tele\n"
        )
        entries = _mod.handoff_nudge._parse_nudge_log_entries(log_path)
        session_traces = {"voluntary-tele": [100]}
        result = _mod.rearm_backtest._nudge_conversion_from_log(session_traces, {log_path.parent: entries})
        assert result["voluntary"] == 1
        assert result["dropped"] == 0

    def test_no_redact_refused_with_multi_root(self, tmp_path, monkeypatch, capsys, fake_config_dir_factory):
        """--no-redact is refused when --config-dir puts more than one root
        in scope, mirroring cost's and context-distribution's own refusal --
        the pre-existing guard is exercised via cmd_rearm_backtest itself,
        before any log read."""
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        with pytest.raises(SystemExit) as exc_info:
            _mod.rearm_backtest.cmd_rearm_backtest(_rearm_backtest_args(no_redact=True, extra_config_dirs=[str(acct_b)]))
        assert exc_info.value.code == 2
        assert "--no-redact" in capsys.readouterr().err

    def test_no_redact_prints_literal_log_path_in_per_root_log_size_line(self, fake_projects, tmp_path, capsys):
        """Single-root --no-redact prints the literal .handoff-nudge.log path,
        not an account-N label, in the per-root log-size line -- exercises
        the `else str(log_path)` branch (multi-root refuses --no-redact
        outright, so this branch is only reachable at single-root scope)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=100_000, output=1_000, ts="2026-05-19T10:00:00.000Z"),
        ])
        _mod.rearm_backtest._rearm_backtest_report(_rearm_backtest_args(no_redact=True), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert str(tmp_path / ".handoff-nudge.log") in out
        assert "account-1" not in out

    def test_no_redact_still_omits_session_id_of_a_real_fired_session(
        self, fake_projects, tmp_path, capsys
    ):
        """--no-redact discloses the literal log path (the test above), but
        must not also disclose the session id of a real fired/classified
        session -- the existing --no-redact test writes no
        .handoff-nudge.log at all, so it never has a session id that could
        leak. This one constructs a real voluntary-bucket session so there's
        something to assert isn't leaking."""
        _write_jsonl(fake_projects / "leaky-session-1.jsonl", [
            _priced("claude-sonnet-5", input=100_000, output=1_000, ts="2026-05-19T10:00:00.000Z"),
        ])
        (tmp_path / ".handoff-nudge.log").write_text(
            "nudged session=leaky-session-1 est=100000 model=claude-sonnet-5"
            " window=1000000 event=Stop\n"
            "handoff session=leaky-session-1\n"
        )
        _mod.rearm_backtest._rearm_backtest_report(_rearm_backtest_args(no_redact=True), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert str(tmp_path / ".handoff-nudge.log") in out
        assert _table_cols(out, header_contains="Bucket", row_contains="voluntary")["Count"] == "1"
        assert "leaky-session-1" not in out


class TestRearmBacktestReportNoRedactGuard:
    """_rearm_backtest_report refuses multi-root --no-redact itself, ahead of any output,
    independent of _resolve_cost_roots' CLI-level refusal."""

    def test_no_redact_refused_by_report_itself_when_multi_root(self, tmp_path, capsys):
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a", [
            _priced("claude-sonnet-5", input=100_000, output=1_000, ts="2026-05-19T10:00:00.000Z"),
        ])
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b", [
            _priced("claude-sonnet-5", input=100_000, output=1_000, ts="2026-05-19T10:00:00.000Z"),
        ])
        with pytest.raises(SystemExit) as exc_info:
            _mod.rearm_backtest._rearm_backtest_report(
                _rearm_backtest_args(no_redact=True), date(2026, 8, 2), roots=[root_a, root_b]
            )
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "--no-redact" in captured.err
