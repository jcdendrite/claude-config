"""Tests for transcript_analysis/plan_boundary.py: boundary-turn location, the three arms' repricing helpers, the
work-inflation breakeven, and the report end to end."""
import importlib.util
import re
import sys
from datetime import date
from pathlib import Path

import pytest

from ._handoff_nudge_helpers import _ramp_curve_from_records
from .conftest import _asst, _exit_plan_mode, _priced, _skill_use, _user_msg, _write_cost_root, _write_jsonl

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


def _extract_arm_dollars(out: str, arm_label: str) -> float:
    """Read plan-boundary's per-arm dollar figure (e.g. arm_label='C: fresh
    Sonnet handoff') by row-label prefix, not by the row's full formatted
    text -- survives cosmetic changes to column width/precision."""
    match = re.search(rf"^{re.escape(arm_label)}\s+([\d,]+\.\d\d)\s*$", out, re.MULTILINE)
    assert match is not None, f"no row found for arm {arm_label!r}"
    return float(match.group(1).replace(",", ""))


def _plan_boundary_turn_index(records: list[dict]) -> int | None:
    """Run _extract_rearm_session_turns + _plan_boundary_turn_index end to
    end -- the shape _plan_boundary_report itself uses."""
    data = _mod.handoff_nudge._extract_rearm_session_turns(records)
    return _mod.plan_boundary._plan_boundary_turn_index(data["deduped"], data["main_thread_record_positions"])


class TestPlanBoundaryTurnIndex:
    def test_exit_plan_mode_signal_marks_the_boundary_turn(self):
        records = [
            _priced("claude-opus-5", output=10, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-opus-5", output=20, content=[_exit_plan_mode("epm1")], ts="2026-05-19T10:00:01.000Z"),
            _priced("claude-opus-5", output=30, ts="2026-05-19T10:00:02.000Z"),
        ]
        assert _plan_boundary_turn_index(records) == 1

    def test_plan_review_skill_invocation_signal_marks_the_boundary_turn(self):
        records = [
            _priced("claude-opus-5", output=10, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-opus-5", output=20, content=[_skill_use("s1", "plan-review")], ts="2026-05-19T10:00:01.000Z"),
            _priced("claude-opus-5", output=30, ts="2026-05-19T10:00:02.000Z"),
        ]
        assert _plan_boundary_turn_index(records) == 1

    def test_no_boundary_signal_returns_none(self):
        records = [
            _priced("claude-opus-5", output=10, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-opus-5", output=20, ts="2026-05-19T10:00:01.000Z"),
        ]
        assert _plan_boundary_turn_index(records) is None

    def test_boundary_as_the_session_final_turn_still_resolves_a_turn_index(self):
        """The divide-by-zero guard against zero post-boundary turns lives in
        the report, not here -- this function must still return the correct
        index when the boundary turn is the session's last one."""
        records = [
            _priced("claude-opus-5", output=10, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-opus-5", output=20, content=[_exit_plan_mode("epm1")], ts="2026-05-19T10:00:01.000Z"),
        ]
        assert _plan_boundary_turn_index(records) == 1

    def test_boundary_inside_sidechain_is_ignored(self):
        records = [
            _priced("claude-opus-5", output=10, ts="2026-05-19T10:00:00.000Z"),
            _asst("claude-opus-5", sidechain=True, content=[_exit_plan_mode("epm1")], ts="2026-05-19T10:00:01.000Z"),
            _priced("claude-opus-5", output=20, content=[_exit_plan_mode("epm2")], ts="2026-05-19T10:00:02.000Z"),
        ]
        assert _plan_boundary_turn_index(records) == 1

    def test_multiple_boundaries_first_occurrence_wins(self):
        """A session's later ExitPlanMode/plan-review calls are re-planning
        inside work this measurement already treats as post-boundary -- the
        first occurrence is the boundary, not the last."""
        records = [
            _priced("claude-opus-5", output=10, content=[_exit_plan_mode("epm1")], ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-opus-5", output=20, ts="2026-05-19T10:00:01.000Z"),
            _priced("claude-opus-5", output=30, content=[_skill_use("s1", "plan-review")], ts="2026-05-19T10:00:02.000Z"),
        ]
        assert _plan_boundary_turn_index(records) == 0

    def test_no_usage_record_before_boundary_does_not_desync_boundary_index(self):
        """A main-thread assistant record with no usage block sitting before
        the real boundary turn must not shift the boundary's own mapped
        main_thread_turns index -- main_thread_record_positions gives
        the boundary's own "deduped" record index directly, so no manual
        turn-counting desync (the class of bug
        test_synthetic_no_usage_record_does_not_desync_boundaries_from_main_thread_turns
        guards in the sibling rearm-backtest code) is possible here."""
        records = [
            _priced("claude-opus-5", output=10, ts="2026-05-19T10:00:00.000Z"),
            _asst("claude-opus-5", ts="2026-05-19T10:00:01.000Z"),  # no usage block
            _priced("claude-opus-5", output=20, content=[_exit_plan_mode("epm1")], ts="2026-05-19T10:00:02.000Z"),
            _priced("claude-opus-5", output=30, ts="2026-05-19T10:00:03.000Z"),
        ]
        assert _plan_boundary_turn_index(records) == 1

    def test_no_usage_record_at_the_trigger_itself_cannot_be_mapped_and_returns_none(self):
        """A boundary signal on a record with no usage block has no entry in
        main_thread_record_positions to map onto -- undetectable, not
        silently mapped to the wrong main_thread_turns index."""
        records = [
            _priced("claude-opus-5", output=10, ts="2026-05-19T10:00:00.000Z"),
            _asst("claude-opus-5", content=[_exit_plan_mode("epm1")], ts="2026-05-19T10:00:01.000Z"),  # no usage
            _priced("claude-opus-5", output=20, ts="2026-05-19T10:00:02.000Z"),
        ]
        assert _plan_boundary_turn_index(records) is None


class TestArmBBoundaryPlusOneDollars:
    def test_charges_sonnet_cache_write_over_boundary_context_and_own_new_tokens_no_cache_read(self):
        """Component-level pin: expected values derive from
        _model_rates("claude-sonnet-5") per token class, never a hardcoded
        multiplier -- a hardcoded value would pass even if the price table
        drifted."""
        usage = {
            "input_tokens": 500, "output_tokens": 1000,
            "cache_read_input_tokens": 999_000,  # must be ignored entirely, never scaled
            "cache_creation_input_tokens": 0,
        }
        boundary_context_tokens = 200_000
        result = _mod.plan_boundary._arm_b_boundary_plus_one_dollars(usage, boundary_context_tokens)

        rates = _mod._model_rates("claude-sonnet-5")
        expected = (
            boundary_context_tokens / 1_000_000 * rates["cache_write_5m"]
            + 500 / 1_000_000 * rates["input"]
            + 1000 / 1_000_000 * rates["output"]
        )
        assert result == pytest.approx(expected)

    def test_cache_read_never_changes_the_result(self):
        """Regression guard against scaling the observed cache-read and also
        charging a cache-write over the same tokens -- double-billing them
        as both a read and a write."""
        usage_no_read = {"input_tokens": 500, "output_tokens": 1000, "cache_read_input_tokens": 0}
        usage_high_read = {"input_tokens": 500, "output_tokens": 1000, "cache_read_input_tokens": 500_000}
        assert _mod.plan_boundary._arm_b_boundary_plus_one_dollars(usage_no_read, 200_000) == pytest.approx(
            _mod.plan_boundary._arm_b_boundary_plus_one_dollars(usage_high_read, 200_000)
        )


class TestArmBLaterTurnDollars:
    def test_prices_observed_read_write_split_at_sonnet_rates(self):
        """Component-level pin: every turn after boundary+1 carries the
        observed read/write split forward, priced at Sonnet rates via
        _model_rates, not the turn's real (Opus) model."""
        usage = {
            "input_tokens": 200, "output_tokens": 800,
            "cache_read_input_tokens": 50_000,
            "cache_creation_input_tokens": 3_000,
            "cache_creation": {"ephemeral_1h_input_tokens": 1_000, "ephemeral_5m_input_tokens": 2_000},
        }
        result = _mod.plan_boundary._arm_b_later_turn_dollars(usage)

        rates = _mod._model_rates("claude-sonnet-5")
        expected = (
            200 / 1_000_000 * rates["input"]
            + 800 / 1_000_000 * rates["output"]
            + 50_000 / 1_000_000 * rates["cache_read"]
            + 1_000 / 1_000_000 * rates["cache_write_1h"]
            + 2_000 / 1_000_000 * rates["cache_write_5m"]
        )
        assert result == pytest.approx(expected)

    def test_zero_cache_creation_prices_cleanly(self):
        usage = {
            "input_tokens": 100, "output_tokens": 100,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
        }
        rates = _mod._model_rates("claude-sonnet-5")
        expected = 100 / 1_000_000 * rates["input"] + 100 / 1_000_000 * rates["output"]
        assert _mod.plan_boundary._arm_b_later_turn_dollars(usage) == pytest.approx(expected)


class TestArmCTurnDollars:
    def test_prices_via_multiply_back_convention(self):
        """Arm C prices (output_tokens/1000) * the ramp curve's own bucket
        rate -- never a scaled actual-dollars figure, which would double-
        count the model-price gap already embedded in the observed dollars."""
        ramp_curve = {"0-5": {"rate": 3.5, "mean_context": 10_000}}
        assert _mod.plan_boundary._arm_c_turn_dollars(2_000, 0, ramp_curve) == pytest.approx((2_000 / 1000) * 3.5)

    def test_falls_back_to_zero_rate_when_ramp_curve_has_no_bucket_entry(self):
        assert _mod.plan_boundary._arm_c_turn_dollars(1_000, 0, {}) == 0.0


class TestPlanBoundaryWorkInflationBreakeven:
    def test_breakeven_pin_against_hand_computed_delta(self):
        """Hand-computed fixture: a $6 cheaper arm with a $4 delta over 100
        post-boundary turns / 10,000 output tokens absorbs pct=4/6 more work
        (~66.67 extra turns, ~6,666.7 extra output tokens) before the
        advantage disappears."""
        result = _mod.plan_boundary._plan_boundary_work_inflation_breakeven(6.0, 4.0, 100, 10_000)
        assert result["pct"] == pytest.approx(4.0 / 6.0)
        assert result["extra_turns"] == pytest.approx(100 * (4.0 / 6.0))
        assert result["extra_output_tokens"] == pytest.approx(10_000 * (4.0 / 6.0))

    def test_zero_cheaper_dollars_returns_none_fields(self):
        result = _mod.plan_boundary._plan_boundary_work_inflation_breakeven(0.0, 4.0, 100, 10_000)
        assert result == {"pct": None, "extra_turns": None, "extra_output_tokens": None}


def _plan_boundary_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    since: str | None = None,
    no_redact: bool = False,
    extra_config_dirs: list[str] | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
        "no_redact": no_redact,
        "extra_config_dirs": extra_config_dirs,
    })()


def _opus_boundary_session(*, boundary_content: list | None = None) -> list[dict]:
    """One Opus-anchored session with a plan boundary at main-thread turn
    index 1 and two post-boundary turns -- the shared fixture for
    plan-boundary's end-to-end report tests."""
    boundary_content = boundary_content if boundary_content is not None else [_exit_plan_mode("epm1")]
    return [
        _priced("claude-opus-5", input=1000, output=100, cache_read=50_000, ts="2026-05-19T10:00:00.000Z"),
        _user_msg("approve the plan", ts="2026-05-19T10:00:01.000Z"),
        _priced("claude-opus-5", input=2000, output=200, cache_read=100_000, content=boundary_content,
                ts="2026-05-19T10:00:02.000Z"),
        _user_msg("go", ts="2026-05-19T10:00:03.000Z"),
        _priced("claude-opus-5", input=500, output=300, cache_read=150_000, ts="2026-05-19T10:00:04.000Z"),
        _user_msg("go", ts="2026-05-19T10:00:05.000Z"),
        _priced("claude-opus-5", input=500, output=400, cache_read=155_000, ts="2026-05-19T10:00:06.000Z"),
    ]


class TestPlanBoundaryReport:
    def test_unpriced_sonnet_model_fails_at_report_start_not_mid_scan(self, fake_projects, monkeypatch, capsys):
        """Arms B and C reprice every post-boundary turn at
        _PLAN_BOUNDARY_SONNET_MODEL's rates; an unpriced model must fail the
        whole report up front with a named message, not crash mid-scan on an
        arbitrary turn."""
        monkeypatch.setattr(_mod.plan_boundary, "_PLAN_BOUNDARY_SONNET_MODEL", "claude-sonnet-5-unpriced-test-double")
        _write_jsonl(fake_projects / "sess.jsonl", _opus_boundary_session())
        with pytest.raises(SystemExit) as exc_info:
            _mod.plan_boundary._plan_boundary_report(_plan_boundary_args(), date(2026, 8, 16), roots=[fake_projects.parent])
        assert exc_info.value.code != 0
        assert "claude-sonnet-5-unpriced-test-double" in capsys.readouterr().err

    def test_redacts_by_default(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", _opus_boundary_session())
        _mod.plan_boundary._plan_boundary_report(_plan_boundary_args(), date(2026, 8, 16), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert _mod._DO_NOT_PUBLISH_BANNER not in out

    def test_no_redact_prints_do_not_publish_banner(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", _opus_boundary_session())
        _mod.plan_boundary._plan_boundary_report(
            _plan_boundary_args(no_redact=True), date(2026, 8, 16), roots=[fake_projects.parent]
        )
        out = capsys.readouterr().out
        assert _mod._DO_NOT_PUBLISH_BANNER in out

    def test_no_redact_refused_by_report_itself_when_multi_root(self, tmp_path):
        """Defense-in-depth, mirroring _cache_rebuild_report's own version of
        this test: every test in this module calls _plan_boundary_report
        directly, bypassing _resolve_cost_roots' CLI-level enforcement."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a", _opus_boundary_session())
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b", _opus_boundary_session())
        with pytest.raises(SystemExit) as exc_info:
            _mod.plan_boundary._plan_boundary_report(
                _plan_boundary_args(no_redact=True), date(2026, 8, 16), roots=[root_a, root_b]
            )
        assert exc_info.value.code == 2

    def test_no_redact_refused_with_multi_root(self, tmp_path, monkeypatch, capsys, fake_config_dir_factory):
        """CLI-level enforcement, mirroring the sibling cost/context-distribution/
        edit-format/subagent-mix tests of the same name: cmd_plan_boundary itself
        (via _resolve_cost_roots), not just _plan_boundary_report, refuses the
        combination."""
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        with pytest.raises(SystemExit) as exc_info:
            _mod.plan_boundary.cmd_plan_boundary(_plan_boundary_args(no_redact=True, extra_config_dirs=[str(acct_b)]))
        assert exc_info.value.code == 2
        assert "--no-redact" in capsys.readouterr().err

    def test_output_never_contains_plan_text_or_plan_file_path(self, fake_projects, capsys):
        """Negative assertion: a fixture record carrying plan text and
        planFilePath must never surface in this report's output, redacted or
        not -- boundary records are consumed for type and position only."""
        secret_plan_text = "REDACT-ME-PLAN-BODY"
        secret_plan_file_path = "REDACT-ME-PLAN-FILE-PATH"
        session = _opus_boundary_session(
            boundary_content=[{
                "type": "tool_use", "id": "epm1", "name": "ExitPlanMode",
                "input": {"plan": secret_plan_text, "planFilePath": secret_plan_file_path},
            }]
        )
        _write_jsonl(fake_projects / "sess.jsonl", session)
        for no_redact in (False, True):
            _mod.plan_boundary._plan_boundary_report(
                _plan_boundary_args(no_redact=no_redact), date(2026, 8, 16), roots=[fake_projects.parent]
            )
            out = capsys.readouterr().out
            assert secret_plan_text not in out
            assert secret_plan_file_path not in out
            assert "planFilePath" not in out

    def test_output_never_contains_a_per_session_breakdown(self, fake_projects, tmp_path, monkeypatch, capsys):
        """Aggregate-only claim: across the whole argument surface (default
        redact, --no-redact, --since, multi-root, --this-repo), no output
        ever names an individual session."""
        _write_jsonl(fake_projects / "sess-one.jsonl", _opus_boundary_session())
        _write_jsonl(fake_projects / "sess-two.jsonl", _opus_boundary_session())
        single_root = [fake_projects.parent]

        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a", _opus_boundary_session())
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b", _opus_boundary_session())
        multi_root = [root_a, root_b]

        monkeypatch.setattr(_mod.scope, "_repo_scoped_project_slugs", lambda *a, **k: ["-home-user-testrepo"])

        for kwargs, roots in (
            ({}, single_root),
            ({"no_redact": True}, single_root),
            ({"since": "365d"}, single_root),
            ({}, multi_root),
            ({"this_repo": True}, single_root),
        ):
            _mod.plan_boundary._plan_boundary_report(_plan_boundary_args(**kwargs), date(2026, 8, 16), roots=roots)
            out = capsys.readouterr().out
            assert "sess-one" not in out
            assert "sess-two" not in out
            assert "sess-a" not in out
            assert "sess-b" not in out
            assert "session-" not in out  # _redact_session_id's own placeholder shape

    def test_unpriced_post_boundary_model_lands_in_unpriced_turns_not_priced_at_zero(self, fake_projects, capsys):
        """A post-boundary turn on a model with no price-table entry is
        excluded from arm A's own dollar total and counted as unpriced, not
        silently priced at $0 and folded in as if genuinely free."""
        session = [
            _priced("claude-opus-5", input=1000, output=100, cache_read=50_000, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-opus-5", input=2000, output=200, cache_read=100_000,
                    content=[_exit_plan_mode("epm1")], ts="2026-05-19T10:00:01.000Z"),
            _priced("claude-opus-4-9-unpriced", input=500, output=300, ts="2026-05-19T10:00:02.000Z"),
        ]
        _write_jsonl(fake_projects / "sess.jsonl", session)
        _mod.plan_boundary._plan_boundary_report(_plan_boundary_args(), date(2026, 8, 16), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "Plan-boundary sessions repriced: 1" in out
        assert "1 unpriced turns" in out

    def test_unpriced_post_boundary_turn_contributes_zero_dollars_to_every_arm_symmetrically(
        self, fake_projects, capsys
    ):
        """An unpriced post-boundary turn among priced ones must be excluded
        from arms B and C the same way it's already excluded from arm A --
        repricing it from raw tokens would bias every dollar comparison
        toward arm A."""
        boundary_turn = _priced(
            "claude-opus-5", input=2000, output=200, cache_read=100_000,
            content=[_exit_plan_mode("epm1")], ts="2026-05-19T10:00:01.000Z",
        )
        priced_offset0 = _priced(
            "claude-opus-5", input=500, output=300, cache_read=150_000, ts="2026-05-19T10:00:02.000Z"
        )
        unpriced_offset1 = _priced(
            "claude-opus-4-9-unpriced", input=600, output=400, cache_read=99_000, ts="2026-05-19T10:00:03.000Z"
        )
        priced_offset2 = _priced(
            "claude-opus-5", input=500, output=250, cache_read=160_000, ts="2026-05-19T10:00:04.000Z"
        )
        session = [
            _priced("claude-opus-5", input=1000, output=100, cache_read=50_000, ts="2026-05-19T10:00:00.000Z"),
            boundary_turn,
            priced_offset0,
            unpriced_offset1,
            priced_offset2,
        ]
        _write_jsonl(fake_projects / "sess.jsonl", session)
        ramp_curve, _ramp_tokens = _ramp_curve_from_records(session)
        boundary_context_tokens = _mod._context_at_turn(boundary_turn["message"]["usage"])

        def priced_dollars(rec: dict) -> float:
            dollars_by_class, _c, _u = _mod._price_turn("claude-opus-5", rec["message"]["usage"])
            return sum(dollars_by_class.values())

        expected_arm_a = priced_dollars(priced_offset0) + priced_dollars(priced_offset2)
        expected_arm_b = (
            _mod.plan_boundary._arm_b_boundary_plus_one_dollars(priced_offset0["message"]["usage"], boundary_context_tokens)
            + _mod.plan_boundary._arm_b_later_turn_dollars(priced_offset2["message"]["usage"])
        )
        expected_arm_c = (
            _mod.plan_boundary._arm_c_turn_dollars(300, 0, ramp_curve)
            + _mod.plan_boundary._arm_c_turn_dollars(250, 2, ramp_curve)
        )

        _mod.plan_boundary._plan_boundary_report(_plan_boundary_args(), date(2026, 8, 16), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "1 unpriced turns" in out
        assert _extract_arm_dollars(out, "A: continue on Opus") == pytest.approx(expected_arm_a, abs=0.005)
        assert _extract_arm_dollars(out, "B: switch to Sonnet") == pytest.approx(expected_arm_b, abs=0.005)
        assert _extract_arm_dollars(out, "C: fresh Sonnet handoff") == pytest.approx(expected_arm_c, abs=0.005)

    def test_boundary_as_final_turn_is_excluded_without_dividing_by_zero(self, fake_projects, capsys):
        session = [
            _priced("claude-opus-5", input=1000, output=100, cache_read=50_000, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-opus-5", input=2000, output=200, cache_read=100_000,
                    content=[_exit_plan_mode("epm1")], ts="2026-05-19T10:00:01.000Z"),
        ]
        _write_jsonl(fake_projects / "sess.jsonl", session)
        _mod.plan_boundary._plan_boundary_report(_plan_boundary_args(), date(2026, 8, 16), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "final main-thread turn (excluded, no post-boundary work): 1" in out
        assert "No plan-boundary sessions with post-boundary work found in scope." in out

    def test_no_boundary_session_is_excluded_and_counted(self, fake_projects, capsys):
        session = [
            _priced("claude-opus-5", input=1000, output=100, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-opus-5", input=2000, output=200, ts="2026-05-19T10:00:01.000Z"),
        ]
        _write_jsonl(fake_projects / "sess.jsonl", session)
        _mod.plan_boundary._plan_boundary_report(_plan_boundary_args(), date(2026, 8, 16), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "No plan boundary detected: 1" in out

    def test_multiple_post_boundary_model_switches_do_not_break_ground_truth_check(self, fake_projects, capsys):
        """A session with more than one post-boundary model switch must not
        crash or miscount the boundary+1 ground-truth check, which looks
        only at the single boundary_index -> boundary_index+1 transition."""
        session = [
            _priced("claude-opus-5", input=1000, output=100, cache_read=50_000, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-opus-5", input=2000, output=200, cache_read=100_000,
                    content=[_exit_plan_mode("epm1")], ts="2026-05-19T10:00:01.000Z"),
            _priced("claude-sonnet-5", input=500, output=300, cache_read=0, ts="2026-05-19T10:00:02.000Z"),
            _priced("claude-opus-5", input=500, output=300, cache_read=150_000, ts="2026-05-19T10:00:03.000Z"),
            _priced("claude-sonnet-5", input=500, output=300, cache_read=0, ts="2026-05-19T10:00:04.000Z"),
        ]
        _write_jsonl(fake_projects / "sess.jsonl", session)
        _mod.plan_boundary._plan_boundary_report(_plan_boundary_args(), date(2026, 8, 16), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "Sessions with a real model change observed at boundary+1: 1 of 1" in out

    def test_ground_truth_buckets_previous_message_not_found_reason(self, fake_projects, capsys):
        """A missing/malformed cache_miss_reason, including the
        previous_message_not_found variant (which carries no
        cache_missed_input_tokens field), must be reported rather than
        raising or silently dropped."""
        boundary_plus_one = _priced(
            "claude-sonnet-5", input=500, output=300, cache_read=0, ts="2026-05-19T10:00:02.000Z"
        )
        boundary_plus_one["message"]["diagnostics"] = {"cache_miss_reason": {"type": "previous_message_not_found"}}
        session = [
            _priced("claude-opus-5", input=1000, output=100, cache_read=50_000, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-opus-5", input=2000, output=200, cache_read=100_000,
                    content=[_exit_plan_mode("epm1")], ts="2026-05-19T10:00:01.000Z"),
            boundary_plus_one,
        ]
        _write_jsonl(fake_projects / "sess.jsonl", session)
        _mod.plan_boundary._plan_boundary_report(_plan_boundary_args(), date(2026, 8, 16), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "previous_message_not_found: 1" in out

    def test_zero_sessions_in_scope_prints_zero_state_without_crashing(self, fake_projects, capsys):
        _mod.plan_boundary._plan_boundary_report(_plan_boundary_args(), date(2026, 8, 16), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "Sessions scanned: 0" in out
        assert "No plan-boundary sessions with post-boundary work found in scope." in out

    def test_arm_c_ramp_curve_is_scoped_to_sonnet_anchored_sessions_not_pooled(self, fake_projects, capsys):
        """Arm C models a fresh Sonnet handoff, so its ramp curve must come
        from Sonnet-anchored ramp sessions only -- a curve pooled across both
        families would misprice arm C toward Opus's own rate."""
        opus_ramp_session = [
            _priced("claude-opus-5", input=1000, output=1000, cache_read=50_000, ts="2026-05-19T09:00:00.000Z"),
            _priced("claude-opus-5", input=1000, output=1000, cache_read=50_000, ts="2026-05-19T09:00:01.000Z"),
        ]
        sonnet_ramp_session = [
            _priced("claude-sonnet-5", input=1000, output=1000, cache_read=50_000, ts="2026-05-19T09:01:00.000Z"),
            _priced("claude-sonnet-5", input=1000, output=1000, cache_read=50_000, ts="2026-05-19T09:01:01.000Z"),
        ]
        boundary_session = _opus_boundary_session()
        _write_jsonl(fake_projects / "ramp-opus.jsonl", opus_ramp_session)
        _write_jsonl(fake_projects / "ramp-sonnet.jsonl", sonnet_ramp_session)
        _write_jsonl(fake_projects / "boundary.jsonl", boundary_session)

        sonnet_only_curve, _sonnet_only_tokens = _ramp_curve_from_records(sonnet_ramp_session)
        pooled_curve, _pooled_tokens = _ramp_curve_from_records(opus_ramp_session, sonnet_ramp_session, boundary_session)
        # Sanity check: if the two curves' "0-5" rates happened to agree, this
        # test couldn't distinguish Sonnet-only scoping from pooling.
        assert sonnet_only_curve["0-5"]["rate"] != pooled_curve["0-5"]["rate"]

        # _opus_boundary_session's two post-boundary turns (output 300, 400)
        # both fall in the "0-5" turns-since-boundary bucket.
        expected_arm_c_dollars = (
            _mod.plan_boundary._arm_c_turn_dollars(300, 0, sonnet_only_curve)
            + _mod.plan_boundary._arm_c_turn_dollars(400, 1, sonnet_only_curve)
        )

        _mod.plan_boundary._plan_boundary_report(_plan_boundary_args(), date(2026, 8, 16), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert _extract_arm_dollars(out, "C: fresh Sonnet handoff") == pytest.approx(expected_arm_c_dollars, abs=0.005)

    def test_sonnet_anchored_session_is_excluded_from_opus_anchored_count(self, fake_projects, capsys):
        """A session whose first main-thread turn is on Sonnet, not Opus, is
        excluded from repricing entirely -- Opus-anchored-only scope is this
        measurement's own design choice, not a general corpus convention."""
        session = [
            _priced("claude-sonnet-5", input=1000, output=100, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-sonnet-5", input=2000, output=200, content=[_exit_plan_mode("epm1")],
                    ts="2026-05-19T10:00:01.000Z"),
            _priced("claude-sonnet-5", input=500, output=300, ts="2026-05-19T10:00:02.000Z"),
        ]
        _write_jsonl(fake_projects / "sess.jsonl", session)
        _mod.plan_boundary._plan_boundary_report(_plan_boundary_args(), date(2026, 8, 16), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "Sessions scanned: 1" in out
        assert "Opus-anchored: 0" in out
        assert "No plan-boundary sessions with post-boundary work found in scope." in out


class TestPlanBoundaryArgparseWiring:
    def test_registers_plan_boundary_subcommand_with_expected_defaults(self):
        parser = _mod.build_parser()
        args = parser.parse_args(["plan-boundary"])
        assert args.since is None
        assert args.extra_config_dirs is None
        assert args.no_redact is False
        assert args.func == _mod.plan_boundary.cmd_plan_boundary
