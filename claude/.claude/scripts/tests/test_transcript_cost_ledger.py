"""Tests for transcript_analysis/cost_ledger.py: path resolution, read mode,
serialization, parser hostility, the reviewer-gap floor, record parity, write
fidelity, auto-create, idempotence, degenerate corpora, concurrency, and CLI
wiring -- see test_transcript_cost_ledger_record_gates.py for the --record
refusal-gate seam this file does not cover.
"""
import importlib.util
import stat
import sys
import threading
from datetime import date, datetime
from pathlib import Path

import pytest

from .conftest import (
    _agent_use,
    _asst,
    _cost_ledger_args,
    _cost_ledger_row,
    _edit_use,
    _hook_deny,
    _opus,
    _priced,
    _priced_opus,
    _tool_result,
    _user_msg,
    _write_jsonl,
    _write_subagent_dispatch,
    _write_subagent_jsonl,
)

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


def _reviewer_dispatch_records(
    proj: Path, session_id: str, tool_id: str, subagent_type: str, verdict_text: str,
    *, dispatch_ts: str, result_ts: str,
) -> list[dict]:
    """One reviewer-agent dispatch + its paired tool_result, at explicit
    caller-chosen timestamps (unlike _n_cited_reviewer_dispatches, which
    hardcodes 2026-05-19 and so can't be placed inside an arbitrary
    cost-ledger test week). Writes the paired subagent transcript/meta.json
    as a side effect."""
    records = [
        _asst("claude-opus-4-7", ts=dispatch_ts, content=[_agent_use(tool_id, subagent_type)]),
        _user_msg([_tool_result(tool_id, "ok")], ts=result_ts),
    ]
    _write_subagent_dispatch(
        proj, session_id, f"agent-{tool_id}", tool_id,
        [_asst("claude-sonnet-4-6", sidechain=True, content=[{"type": "text", "text": verdict_text}])],
        agent_type=subagent_type,
    )
    return records


class TestCostLedgerPathResolution:
    def test_override_absolute_honored(self, monkeypatch, tmp_path):
        override = tmp_path / "custom-ledger-location" / "cost-ledger.md"
        monkeypatch.setenv("COST_LEDGER_PATH", str(override))
        assert _mod.cost_ledger._cost_ledger_path() == override

    def test_override_relative_raises_value_error(self, monkeypatch):
        monkeypatch.setenv("COST_LEDGER_PATH", "relative/cost-ledger.md")
        with pytest.raises(ValueError, match="must be an absolute path"):
            _mod.cost_ledger._cost_ledger_path()

    def test_record_with_relative_override_exits_1_with_no_raw_value(
        self, fake_projects, monkeypatch, capsys,
    ):
        """_cost_ledger_report's own `except ValueError` around
        _cost_ledger_path -- that raising function's own message embeds
        COST_LEDGER_PATH's raw value, so this catch must not forward str(exc)
        to stderr. Mirrors pr-cost-export's identical no-raw-value discipline
        for its own copy of this catch."""
        monkeypatch.setenv("COST_LEDGER_PATH", "relative/cost-ledger.md")

        with pytest.raises(SystemExit) as exc_info:
            _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "must be an absolute path" in err
        assert "relative/cost-ledger.md" not in err

    def test_unset_falls_back_to_config_dir(self, monkeypatch, tmp_path):
        """Unset COST_LEDGER_PATH resolves against a monkeypatched
        CLAUDE_CONFIG_DIR, not this workstation's real $HOME."""
        monkeypatch.delenv("COST_LEDGER_PATH", raising=False)
        cfg_dir = tmp_path / "isolated-claude-config"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_dir))
        assert _mod.cost_ledger._cost_ledger_path() == cfg_dir / "cost-ledger.md"


class TestCostLedgerReadMode:
    def test_read_mode_lists_existing_rows_and_flags_unrecorded_live_week(
        self, fake_projects, cost_ledger_file, capsys
    ):
        """Read mode prints an existing row, plus a live-corpus week with no
        row for any machine, as a recording gap."""
        existing = _cost_ledger_row(week="2026-W20", machine="m1")
        cost_ledger_file.write_text(
            cost_ledger_file.read_text() + _mod.cost_ledger._format_cost_ledger_row(existing) + "\n"
        )
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),  # 2026-W23
        ])
        _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(), date(2026, 6, 3))
        out = capsys.readouterr().out
        assert "2026-W20" in out
        assert "m1" in out
        assert "Weeks present in the live corpus with no ledger row yet:" in out
        assert "2026-W23" in out

    def test_read_mode_missing_ledger_file_refuses(self, fake_projects, tmp_path, monkeypatch):
        """A missing docs/cost-ledger.md refuses rather than silently
        reporting an empty ledger — the file is never lazily created."""
        monkeypatch.setattr(_mod.cost_ledger, "_cost_ledger_path", lambda: tmp_path / "absent-cost-ledger.md")
        with pytest.raises(SystemExit) as exc_info:
            _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(), date(2026, 6, 3))
        assert exc_info.value.code != 0

    def test_read_mode_missing_ledger_file_prints_never_recorded_wording(
        self, fake_projects, tmp_path, monkeypatch, capsys
    ):
        """Behavioral check that the "never recorded here yet" wording is
        actually reached and printed on this code path, not just present
        somewhere in source — see the source-grep tripwire below for the
        companion check that the old wording doesn't silently return."""
        monkeypatch.setattr(_mod.cost_ledger, "_cost_ledger_path", lambda: tmp_path / "absent-cost-ledger.md")
        with pytest.raises(SystemExit):
            _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(), date(2026, 6, 3))
        err = capsys.readouterr().err
        assert "no ledger recorded here yet" in err

    def test_old_ledger_file_not_found_wording_absent_from_source(self):
        """Source-grep tripwire, not a behavioral guarantee (see the
        behavioral test above): pins against the literal old message text
        silently reappearing."""
        assert "ledger file not found" not in Path(_mod.cost_ledger.__file__).read_text()

    def test_format_read_row_renders_exact_fixed_width_columns(self):
        """_format_cost_ledger_read_row's fixed-width terminal line, checked
        cell-by-cell against a known row -- distinct from the write-side
        markdown line, which round-trips through the canonical parser
        instead (there is no parser for this display-only format)."""
        row = _cost_ledger_row(
            week="2026-W20", machine="m1", usd=1234.56, context_pct=12.3, opus_pct=45.6,
            ge200k_pct=12.3, denials=7, reviewer_gap_pp=-3.2, note="rolled out F3 fix",
        )
        assert _mod.cost_ledger._format_cost_ledger_read_row(row) == (
            "2026-W20   m1        2026-08-02      1,234.56     12.3%   45.6%    12.3%"
            "        7       -3.2pp  rolled out F3 fix"
        )

    def test_format_read_row_renders_insufficient_sentinel_in_gap_column(self):
        """The insufficient sentinel fills the widened 12-char GapPP column
        as a single whitespace-delimited token, same as a numeric gap."""
        row = _cost_ledger_row(
            week="2026-W20", machine="m1", usd=1234.56, context_pct=12.3, opus_pct=45.6,
            ge200k_pct=12.3, denials=7, reviewer_gap_pp=_mod.reviewer_yield._REVIEWER_YIELD_INSUFFICIENT,
            note="rolled out F3 fix",
        )
        assert _mod.cost_ledger._format_cost_ledger_read_row(row) == (
            "2026-W20   m1        2026-08-02      1,234.56     12.3%   45.6%    12.3%"
            "        7 insufficient  rolled out F3 fix"
        )

    def test_read_mode_still_returns_union_with_two_declared_roots(
        self, fake_projects, cost_ledger_file, monkeypatch, tmp_path, capsys
    ):
        """Mechanism 8 refuses only --record; a plain read with two declared
        roots must still return the union unchanged -- pins that mechanism 8
        does not touch read-mode semantics."""
        other = tmp_path / "acct-other"
        other_proj = other / "projects" / "-repo-main"
        other_proj.mkdir(parents=True)
        _write_jsonl(other_proj / "sess-other.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{other}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(), date(2026, 6, 3))
        out = capsys.readouterr().out
        assert "COST LEDGER SOURCES (" in out
        assert "2 roots" in out


class TestCostLedgerSerializationRoundTrip:
    @staticmethod
    def _table(row_line: str) -> str:
        return (
            _mod.cost_ledger._COST_LEDGER_HEADER_LINE + "\n"
            + _mod.cost_ledger._COST_LEDGER_SEPARATOR_LINE + "\n"
            + row_line + "\n"
        )

    def test_full_row_round_trips_through_the_markdown_line_format(self):
        """A row dict serialized to its markdown line and parsed back through
        the canonical file parser produces the identical dict — no
        dependence on any corpus scan."""
        row = _cost_ledger_row(
            week="2026-W20", machine="m1", usd=1234.56, context_pct=12.3, opus_pct=45.6,
            ge200k_pct=12.3, denials=7, reviewer_gap_pp=-3.2, note="rolled out F3 fix",
        )
        line = _mod.cost_ledger._format_cost_ledger_row(row)
        _preamble, rows = _mod.cost_ledger._parse_cost_ledger_file_text(self._table(line))
        assert rows == [row]

    def test_unmeasured_gap_and_empty_note_round_trip(self):
        """An unmeasured reviewer_gap_pp (None) and an empty note both
        round-trip through the markdown line format unchanged."""
        row = _cost_ledger_row(usd=0.0, context_pct=0.0, opus_pct=0.0, ge200k_pct=0.0,
                                denials=0, reviewer_gap_pp=None, note="")
        line = _mod.cost_ledger._format_cost_ledger_row(row)
        _preamble, rows = _mod.cost_ledger._parse_cost_ledger_file_text(self._table(line))
        assert rows == [row]

    def test_insufficient_gap_sentinel_round_trips(self):
        """The below-floor insufficient sentinel round-trips through the
        markdown line format unchanged, distinct from an unmeasured (None)
        gap -- both are non-numeric, but only one has a nonzero Active
        denominator on either side."""
        row = _cost_ledger_row(reviewer_gap_pp=_mod.reviewer_yield._REVIEWER_YIELD_INSUFFICIENT)
        line = _mod.cost_ledger._format_cost_ledger_row(row)
        _preamble, rows = _mod.cost_ledger._parse_cost_ledger_file_text(self._table(line))
        assert rows == [row]

    def test_note_with_isolated_brackets_round_trips(self):
        """A '[' and ']' pair with no immediately-following '(...)' is not
        markdown link/image syntax and must round-trip unchanged -- pins the
        markdown-link regex's negative boundary, not just its positive
        matches."""
        row = _cost_ledger_row(note="fix [WIP] rollout")
        line = _mod.cost_ledger._format_cost_ledger_row(row)
        _preamble, rows = _mod.cost_ledger._parse_cost_ledger_file_text(self._table(line))
        assert rows == [row]


class TestCostLedgerParserHostility:
    @staticmethod
    def _table(row_line: str) -> str:
        return (
            _mod.cost_ledger._COST_LEDGER_HEADER_LINE + "\n"
            + _mod.cost_ledger._COST_LEDGER_SEPARATOR_LINE + "\n"
            + row_line + "\n"
        )

    def test_wrong_column_count_rejected(self):
        with pytest.raises(_mod.cost_ledger._CostLedgerParseError, match="expected 10 columns"):
            _mod.cost_ledger._parse_cost_ledger_file_text(self._table("| 2026-W20 | m1 | too | few |"))

    def test_non_iso_week_label_rejected(self):
        row = _mod.cost_ledger._format_cost_ledger_row(_cost_ledger_row(week="2026-W99"))
        with pytest.raises(_mod.cost_ledger._CostLedgerParseError, match="malformed week label"):
            _mod.cost_ledger._parse_cost_ledger_file_text(self._table(row))

    def test_non_numeric_percentage_rejected(self):
        row = "| 2026-W20 | m1 | 2026-08-02 | 1.00 | 12.x% | 1.0% | 1.0% | 0 |  |  |"
        with pytest.raises(_mod.cost_ledger._CostLedgerParseError, match="non-numeric context_pct"):
            _mod.cost_ledger._parse_cost_ledger_file_text(self._table(row))

    def test_percentage_missing_trailing_percent_sign_rejected(self):
        row = "| 2026-W20 | m1 | 2026-08-02 | 1.00 | not-a-pct | 1.0% | 1.0% | 0 |  |  |"
        with pytest.raises(_mod.cost_ledger._CostLedgerParseError, match="malformed context_pct"):
            _mod.cost_ledger._parse_cost_ledger_file_text(self._table(row))

    def test_pipe_inside_a_cell_rejected_as_wrong_column_count(self):
        """A raw, unescaped '|' inside note (never a supported escape) splits
        into an extra column, surfacing as the same wrong-column-count error
        as any other malformed row — no separate pipe-detection code path
        is needed."""
        row = "| 2026-W20 | m1 | 2026-08-02 | 1.00 | 1.0% | 1.0% | 1.0% | 0 |  | rolled out | oops |"
        with pytest.raises(_mod.cost_ledger._CostLedgerParseError, match="expected 10 columns"):
            _mod.cost_ledger._parse_cost_ledger_file_text(self._table(row))

    def test_note_with_ansi_escape_byte_rejected(self):
        """A non-printable-ASCII byte in note (e.g. an ANSI/OSC terminal
        escape sequence) is rejected by the canonical parser itself, not
        only by --record-time validation -- a hand-edited or PR-introduced
        row must be held to the same contract."""
        row = _mod.cost_ledger._format_cost_ledger_row(_cost_ledger_row(note="\x1b]0;PWNED\x07\x1b[2J\x1b[H"))
        with pytest.raises(_mod.cost_ledger._CostLedgerParseError, match="malformed note"):
            _mod.cost_ledger._parse_cost_ledger_file_text(self._table(row))

    def test_note_with_markdown_image_syntax_rejected(self):
        """A markdown image reference in note is rejected by the canonical
        parser -- docs/cost-ledger.md is rendered by GitHub, so an image
        reference would beacon an external server on every view."""
        row = _mod.cost_ledger._format_cost_ledger_row(_cost_ledger_row(note="![](https://example.com/t.png)"))
        with pytest.raises(_mod.cost_ledger._CostLedgerParseError, match="malformed note"):
            _mod.cost_ledger._parse_cost_ledger_file_text(self._table(row))

    def test_note_with_markdown_link_syntax_rejected(self):
        """A plain markdown link (no leading '!') in note is rejected by the
        canonical parser -- docs/cost-ledger.md is rendered by GitHub, so a
        link would beacon an external server on every view just as an image
        reference would."""
        row = _mod.cost_ledger._format_cost_ledger_row(_cost_ledger_row(note="[click here](https://example.com/t)"))
        with pytest.raises(_mod.cost_ledger._CostLedgerParseError, match="malformed note"):
            _mod.cost_ledger._parse_cost_ledger_file_text(self._table(row))

    def test_gap_sentinel_case_variant_rejected(self):
        """A case-variant near-miss of the "insufficient" sentinel doesn't
        silently match it -- it falls through to the trailing-'pp' check and
        is rejected like any other malformed value."""
        row = "| 2026-W20 | m1 | 2026-08-02 | 1.00 | 1.0% | 1.0% | 1.0% | 0 | Insufficient |  |"
        with pytest.raises(_mod.cost_ledger._CostLedgerParseError, match="expected a trailing 'pp'"):
            _mod.cost_ledger._parse_cost_ledger_file_text(self._table(row))

    def test_gap_missing_trailing_pp_suffix_rejected(self):
        row = "| 2026-W20 | m1 | 2026-08-02 | 1.00 | 1.0% | 1.0% | 1.0% | 0 | 5.0 |  |"
        with pytest.raises(_mod.cost_ledger._CostLedgerParseError, match="expected a trailing 'pp'"):
            _mod.cost_ledger._parse_cost_ledger_file_text(self._table(row))

    def test_gap_non_finite_after_pp_suffix_rejected(self):
        row = "| 2026-W20 | m1 | 2026-08-02 | 1.00 | 1.0% | 1.0% | 1.0% | 0 | nanpp |  |"
        with pytest.raises(_mod.cost_ledger._CostLedgerParseError, match="non-finite reviewer_gap_pp"):
            _mod.cost_ledger._parse_cost_ledger_file_text(self._table(row))

    def test_gap_empty_prefix_before_pp_suffix_rejected(self):
        row = "| 2026-W20 | m1 | 2026-08-02 | 1.00 | 1.0% | 1.0% | 1.0% | 0 | pp |  |"
        with pytest.raises(_mod.cost_ledger._CostLedgerParseError, match="non-numeric reviewer_gap_pp"):
            _mod.cost_ledger._parse_cost_ledger_file_text(self._table(row))

    def test_unresolved_merge_conflict_marker_rejected(self):
        text = (
            _mod.cost_ledger._COST_LEDGER_HEADER_LINE + "\n"
            + _mod.cost_ledger._COST_LEDGER_SEPARATOR_LINE + "\n"
            + "<<<<<<< HEAD\n"
            + _mod.cost_ledger._format_cost_ledger_row(_cost_ledger_row(machine="m1")) + "\n"
            + "=======\n"
            + _mod.cost_ledger._format_cost_ledger_row(_cost_ledger_row(machine="m2")) + "\n"
            + ">>>>>>> branch\n"
        )
        with pytest.raises(_mod.cost_ledger._CostLedgerParseError, match="merge-conflict marker"):
            _mod.cost_ledger._parse_cost_ledger_file_text(text)


class TestReviewerGapPPFloor:
    """_reviewer_gap_pp's under-floor guard, exercised directly on
    agg2-shaped dicts rather than through a corpus fixture."""

    @staticmethod
    def _agg2(*, findings_active: int, findings_edited: int, zero_active: int, zero_edited: int) -> dict:
        return {
            ("staff-backend-engineer", _mod.reviewer_yield._REVIEWER_VERDICT_FINDINGS_FOUND): {
                "cited": findings_active, "active": findings_active, "edited": findings_edited,
            },
            ("staff-backend-engineer", _mod.reviewer_yield._REVIEWER_VERDICT_ZERO_FINDING): {
                "cited": zero_active, "active": zero_active, "edited": zero_edited,
            },
        }

    def test_both_arms_below_floor_returns_insufficient(self):
        agg2 = self._agg2(findings_active=9, findings_edited=9, zero_active=9, zero_edited=0)
        assert _mod.cost_ledger._reviewer_gap_pp(agg2) == _mod.reviewer_yield._REVIEWER_YIELD_INSUFFICIENT

    def test_both_arms_at_floor_returns_a_numeric_gap(self):
        agg2 = self._agg2(findings_active=10, findings_edited=10, zero_active=10, zero_edited=0)
        assert _mod.cost_ledger._reviewer_gap_pp(agg2) == pytest.approx(100.0)

    def test_zero_finding_arm_under_floor_returns_insufficient_even_when_findings_arm_clears_it(self):
        agg2 = self._agg2(findings_active=10, findings_edited=10, zero_active=9, zero_edited=0)
        assert _mod.cost_ledger._reviewer_gap_pp(agg2) == _mod.reviewer_yield._REVIEWER_YIELD_INSUFFICIENT

    def test_findings_arm_under_floor_returns_insufficient_even_when_zero_arm_clears_it(self):
        agg2 = self._agg2(findings_active=9, findings_edited=9, zero_active=10, zero_edited=0)
        assert _mod.cost_ledger._reviewer_gap_pp(agg2) == _mod.reviewer_yield._REVIEWER_YIELD_INSUFFICIENT

    def test_zero_finding_arm_at_zero_active_returns_none_even_when_findings_arm_is_above_floor(self):
        agg2 = self._agg2(findings_active=20, findings_edited=10, zero_active=0, zero_edited=0)
        assert _mod.cost_ledger._reviewer_gap_pp(agg2) is None

    def test_zero_finding_arm_at_zero_active_returns_none_even_when_findings_arm_is_below_floor(self):
        agg2 = self._agg2(findings_active=5, findings_edited=2, zero_active=0, zero_edited=0)
        assert _mod.cost_ledger._reviewer_gap_pp(agg2) is None


class TestCostLedgerRecordParity:
    def test_record_row_matches_the_compute_functions_independently(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, capsys
    ):
        """--record's row values equal what cost.compute_cost_trend_data,
        review_trace.compute_deny_summary_data, and
        reviewer_yield.compute_reviewer_yield_data compute
        independently for the same week — the parity check that catches
        drift between the recorder and the report subcommands it reuses.

        The fixture carries one main-thread denial and one subagent-sourced
        denial. The two use different hook names because _hook_deny derives
        its toolUseID from the hook name, and a same-named second denial
        would collapse under dedup. The deny oracle below is widened to
        include_subagents=True to match its two cost/reviewer siblings. That
        widening is what exercises agreement between the recorder's denials
        column and the oracle's own subagent-inclusive count, rather than
        only the main-thread-only count both would otherwise report."""
        proj = fake_projects
        session_id = "sess-parity"
        records = [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
            _hook_deny("require-code-review", ts="2026-06-02T10:00:00.000Z"),
        ]
        # Below _REVIEWER_YIELD_ACTIVE_FLOOR (10) dispatches per arm, reviewer_gap_pp
        # reports "insufficient" instead of a numeric gap -- this fixture uses ten
        # per arm to clear the floor.
        for i in range(10):
            records += _reviewer_dispatch_records(
                proj, session_id, f"f{i}", "staff-backend-engineer",
                f"Found 1 issue in src/foo{i}.py needing a fix",
                dispatch_ts=f"2026-06-01T09:{i:02d}:00.000Z", result_ts=f"2026-06-01T09:{i:02d}:30.000Z",
            )
            records.append(_asst("claude-opus-4-7", ts=f"2026-06-01T09:{i:02d}:40.000Z",
                                  content=[_edit_use(f"ef{i}", path=f"src/foo{i}.py")]))
        for i in range(10):
            records += _reviewer_dispatch_records(
                proj, session_id, f"z{i}", "staff-backend-engineer",
                f"Found 0 issues in src/other{i}.py after review",
                dispatch_ts=f"2026-06-01T10:{i:02d}:00.000Z", result_ts=f"2026-06-01T10:{i:02d}:30.000Z",
            )
        records.append(_asst("claude-opus-4-7", ts="2026-06-01T10:15:00.000Z",
                              content=[_edit_use("ez-final", path="src/unrelated.py")]))
        _write_jsonl(proj / f"{session_id}.jsonl", records)

        subagent_denial = _hook_deny("worktree", ts="2026-06-02T11:00:00.000Z")
        subagent_denial["isSidechain"] = True
        _write_subagent_jsonl(proj, session_id, "denial-agent", [subagent_denial])

        _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))
        capsys.readouterr()

        _preamble, rows = _mod.cost_ledger._parse_cost_ledger_file_text(cost_ledger_file.read_text())
        assert len(rows) == 1
        row = rows[0]
        assert row["week"] == "2026-W23"

        cost_iter, _scope = _mod._resolve_project_scope(_cost_ledger_args(), "cost-ledger", include_subagents=True)
        cost_weeks, _u, _t = _mod.cost.compute_cost_trend_data(cost_iter)
        week_data = cost_weeks["2026-W23"]
        assert row["usd"] == pytest.approx(week_data["total"])
        # context_pct (context-class dollar share, GH-554 F1) and ge200k_pct
        # (>=200k-context-bucket dollar share, cost-trend's own existing
        # metric) are distinct fields of cost.compute_cost_trend_data, asserted
        # independently -- the fixture's only turn is all input tokens (no
        # cache_read/cache_write) but crosses the 200k-context threshold, so
        # a regression that swaps or re-aliases the two would be caught by
        # either assertion failing, not just one.
        assert row["context_pct"] == pytest.approx(
            _mod.render._pct_value(week_data["context_class_dollars"], week_data["total"])
        )
        assert row["ge200k_pct"] == pytest.approx(_mod.render._pct_value(week_data["context_over"], week_data["total"]))
        assert row["context_pct"] == pytest.approx(0.0)
        assert row["ge200k_pct"] == pytest.approx(100.0)
        assert row["opus_pct"] == pytest.approx(_mod.render._pct_value(week_data["opus"], week_data["total"]))

        week_start = _mod.datetime(2026, 6, 1, tzinfo=_mod.UTC).timestamp()
        week_end = week_start + 7 * 86400
        deny_iter, _scope = _mod._resolve_project_scope(_cost_ledger_args(), "cost-ledger", include_subagents=True)
        deny_data = _mod.review_trace.compute_deny_summary_data(deny_iter, since_ts=week_start, until_ts=week_end)
        assert row["denials"] == sum(deny_data["hook_counts"].values())
        assert row["denials"] == 2

        reviewer_iter, _scope = _mod._resolve_project_scope(_cost_ledger_args(), "cost-ledger", include_subagents=True)
        reviewer_data = _mod.reviewer_yield.compute_reviewer_yield_data(reviewer_iter, since_ts=week_start, until_ts=week_end)
        assert row["reviewer_gap_pp"] == pytest.approx(_mod.cost_ledger._reviewer_gap_pp(reviewer_data["agg2"]))
        assert row["reviewer_gap_pp"] == pytest.approx(100.0)  # findings-found 100% edited vs. zero-finding 0%

    def test_record_row_carries_insufficient_sentinel_under_the_active_floor(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, capsys
    ):
        """A week with fewer than _REVIEWER_YIELD_ACTIVE_FLOOR Active
        dispatches on either arm records the "insufficient" sentinel, not a
        percentage-point figure computed from an underpowered sample."""
        proj = fake_projects
        session_id = "sess-parity-small"
        records = [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ]
        records += _reviewer_dispatch_records(
            proj, session_id, "f1", "staff-backend-engineer", "Found 1 issue in src/foo.py needing a fix",
            dispatch_ts="2026-06-01T09:00:00.000Z", result_ts="2026-06-01T09:00:30.000Z",
        )
        records.append(_asst("claude-opus-4-7", ts="2026-06-01T09:05:00.000Z",
                              content=[_edit_use("ef1", path="src/foo.py")]))
        records += _reviewer_dispatch_records(
            proj, session_id, "z1", "staff-backend-engineer", "Found 0 issues in src/other.py after review",
            dispatch_ts="2026-06-01T09:10:00.000Z", result_ts="2026-06-01T09:10:30.000Z",
        )
        records.append(_asst("claude-opus-4-7", ts="2026-06-01T09:15:00.000Z",
                              content=[_edit_use("ez1", path="src/unrelated.py")]))
        _write_jsonl(proj / f"{session_id}.jsonl", records)

        _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))
        capsys.readouterr()

        _preamble, rows = _mod.cost_ledger._parse_cost_ledger_file_text(cost_ledger_file.read_text())
        assert len(rows) == 1
        assert rows[0]["reviewer_gap_pp"] == _mod.reviewer_yield._REVIEWER_YIELD_INSUFFICIENT

    def test_denial_at_next_weeks_monday_boundary_excluded_from_this_weeks_row(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, capsys
    ):
        """The per-week window is [week_start_ts, week_end_ts) -- a denial
        timestamped exactly at this Monday's 00:00:00 UTC is the window's
        first included instant, while one at the following Monday's own
        00:00:00 UTC belongs to next week and must not inflate this
        week's count."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),  # 2026-W23
            _hook_deny("require-code-review", ts="2026-06-01T00:00:00.000Z"),  # week_start_ts: included
            _hook_deny("require-code-review", ts="2026-06-08T00:00:00.000Z"),  # week_end_ts: excluded
        ])
        _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))
        capsys.readouterr()

        _preamble, rows = _mod.cost_ledger._parse_cost_ledger_file_text(cost_ledger_file.read_text())
        assert len(rows) == 1
        assert rows[0]["week"] == "2026-W23"
        assert rows[0]["denials"] == 1


class TestCostLedgerWriteFidelity:
    def test_write_succeeds_for_a_dollar_amount_that_does_not_round_to_a_clean_value(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled
    ):
        """Regression test: _write_cost_ledger_file's write-verification step
        must compare the temp file's written bytes against the intended
        text, not re-parsed row dicts against the original row -- usd is
        formatted to cents and percentages to one decimal, so a row's raw
        float legitimately differs from its formatted-then-reparsed value.
        Comparing rows directly would refuse to write almost any real
        (non-round-number) week's figures. 350,000 input tokens at Sonnet
        5's $2/MTok base rate prices to $0.70 (a clean total), but the
        >=200k-bucket dollar share is 100% here — this instead exercises a
        percentage that does not land on a clean one-decimal boundary via a
        second turn that is priced but contributes an uneven opus share."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=333_333, ts="2026-06-01T10:00:00.000Z"),
            _priced_opus([], out=100, ts="2026-06-01T11:00:00.000Z"),
        ])
        _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))
        _preamble, rows = _mod.cost_ledger._parse_cost_ledger_file_text(cost_ledger_file.read_text())
        assert len(rows) == 1
        assert 0 < rows[0]["opus_pct"] < 100

    def test_write_to_nonexistent_ledger_path_leaves_mkstemp_default_mode(self, tmp_path):
        """ledger_path not existing yet (the first write against a fresh
        path) must not crash stat()'ing a nonexistent file while preserving
        permissions -- it should leave tempfile.mkstemp's own 0600 default
        in place instead."""
        ledger_path = tmp_path / "cost-ledger.md"
        preamble = _mod.cost_ledger._COST_LEDGER_HEADER_LINE + "\n" + _mod.cost_ledger._COST_LEDGER_SEPARATOR_LINE + "\n"
        _mod.cost_ledger._write_cost_ledger_file(ledger_path, preamble, [])
        assert stat.S_IMODE(ledger_path.stat().st_mode) == 0o600

    def test_write_to_existing_ledger_path_preserves_its_mode(self, tmp_path):
        """The existing-file case -- chmod to the existing file's own mode
        -- is unaffected by the ledger_path.exists() guard added for the
        nonexistent-path case above."""
        ledger_path = tmp_path / "cost-ledger.md"
        preamble = _mod.cost_ledger._COST_LEDGER_HEADER_LINE + "\n" + _mod.cost_ledger._COST_LEDGER_SEPARATOR_LINE + "\n"
        ledger_path.write_text(preamble)
        ledger_path.chmod(0o640)
        _mod.cost_ledger._write_cost_ledger_file(ledger_path, preamble, [])
        assert stat.S_IMODE(ledger_path.stat().st_mode) == 0o640


class TestCostLedgerAutoCreate:
    def test_record_creates_fresh_file_with_default_preamble_when_none_exists(
        self, fake_projects, cost_ledger_enabled, tmp_path, monkeypatch
    ):
        """--record against a path with no file yet, but an existing parent
        directory, creates the ledger fresh (default preamble, one row) --
        round-trips through the canonical parser exactly like an
        already-canonical file."""
        ledger_path = tmp_path / "cost-ledger.md"
        monkeypatch.setattr(_mod.cost_ledger, "_cost_ledger_path", lambda: ledger_path)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))

        preamble, rows = _mod.cost_ledger._parse_cost_ledger_file_text(ledger_path.read_text())
        assert preamble == _mod.cost_ledger._default_cost_ledger_preamble()
        assert len(rows) == 1
        assert rows[0]["week"] == "2026-W23"

    def test_record_creates_missing_parent_directory_too(
        self, fake_projects, cost_ledger_enabled, tmp_path, monkeypatch
    ):
        """--record against a path whose parent directory also doesn't
        exist yet (a never-before-used $CLAUDE_CONFIG_DIR) must create both
        the directory and the file -- a non-recursive mkdir() would pass
        the previous test while still crashing here."""
        ledger_path = tmp_path / "fresh-config-dir" / "cost-ledger.md"
        monkeypatch.setattr(_mod.cost_ledger, "_cost_ledger_path", lambda: ledger_path)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))

        assert ledger_path.parent.is_dir()
        _preamble, rows = _mod.cost_ledger._parse_cost_ledger_file_text(ledger_path.read_text())
        assert len(rows) == 1

    def test_record_refused_before_sentinel_check_leaves_no_directory_behind(
        self, fake_projects, tmp_path, monkeypatch
    ):
        """A guard that rejects ahead of the auto-create mkdir (here: the
        missing-sentinel check, the first guard --record hits) must leave
        zero filesystem side effects -- this is the property every other
        guard-rejection test only checks via exit code/stderr, not via the
        directory the mkdir call would have created. A future edit that
        hoisted the mkdir above a guard would pass every other test in this
        file unchanged while still failing this one."""
        ledger_path = tmp_path / "never-created-config-dir" / "cost-ledger.md"
        monkeypatch.setattr(_mod.cost_ledger, "_cost_ledger_path", lambda: ledger_path)
        cfg_dir_no_sentinel = tmp_path / "isolated-claude-config-no-sentinel"
        cfg_dir_no_sentinel.mkdir()
        monkeypatch.setattr(_mod.cost_ledger, "config_dir", lambda: cfg_dir_no_sentinel)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        with pytest.raises(SystemExit) as exc_info:
            _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))
        assert exc_info.value.code != 0
        assert not ledger_path.parent.exists()


class TestCostLedgerRecordIdempotence:
    def test_second_record_without_force_refused_and_file_byte_identical(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled
    ):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))
        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))
        assert exc_info.value.code != 0
        assert cost_ledger_file.read_text() == before

    def test_record_with_force_replaces_row_leaving_other_rows_untouched(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled
    ):
        other_row = _cost_ledger_row(week="2026-W23", machine="other1", note="unrelated machine")
        cost_ledger_file.write_text(
            cost_ledger_file.read_text() + _mod.cost_ledger._format_cost_ledger_row(other_row) + "\n"
        )
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        _mod.cost_ledger._cost_ledger_report(
            _cost_ledger_args(record=True, note="first"), date(2026, 6, 3)
        )
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-02T10:00:00.000Z"),
        ])
        _mod.cost_ledger._cost_ledger_report(
            _cost_ledger_args(record=True, force=True, note="second"), date(2026, 6, 3)
        )
        _preamble, rows = _mod.cost_ledger._parse_cost_ledger_file_text(cost_ledger_file.read_text())
        assert len(rows) == 2
        assert rows[0] == other_row
        this_machine_row = next(r for r in rows if r["machine"] == "7e57c0de")
        assert this_machine_row["note"] == "second"
        assert this_machine_row["usd"] == pytest.approx(4.0)


class TestCostLedgerDegenerateCorpora:
    def test_empty_corpus_refuses_and_writes_nothing(self, fake_projects, cost_ledger_file, cost_ledger_enabled):
        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))
        assert exc_info.value.code != 0
        assert cost_ledger_file.read_text() == before

    def test_current_week_all_turns_unpriced_refuses_and_writes_nothing(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled
    ):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=400,
                  ts="2026-06-01T10:00:00.000Z"),  # claude-opus-4-7 is deliberately unpriced
        ])
        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod.cost_ledger._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))
        assert exc_info.value.code != 0
        assert cost_ledger_file.read_text() == before

    def test_clock_skew_between_corpus_and_current_week_refuses(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled
    ):
        """The corpus's most recent activity landing in a later week than
        the machine's computed 'today' refuses rather than mislabeling the
        row under the wrong (week, machine) slot."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),  # 2026-W23
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-15T10:00:00.000Z"),  # 2026-W25
        ])
        with pytest.raises(SystemExit) as exc_info:
            _mod.cost_ledger._cost_ledger_report(
                _cost_ledger_args(record=True), date(2026, 6, 3)  # resolves to 2026-W23
            )
        assert exc_info.value.code != 0


class TestCostLedgerConcurrency:
    def test_two_racing_records_produce_exactly_one_row(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled
    ):
        """Two --record calls racing for the same (week, machine) key, run
        concurrently, leave exactly one row: the second acquires the lock
        after the first has already written and committed, sees the
        already-recorded row under the lock, and refuses the duplicate —
        not a double append, not a corrupted table."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        args = _cost_ledger_args(record=True)
        today = date(2026, 6, 3)
        exit_codes: list[int | None] = [None, None]

        def _run(i: int) -> None:
            try:
                _mod.cost_ledger._cost_ledger_report(args, today)
                exit_codes[i] = 0
            except SystemExit as exc:
                exit_codes[i] = exc.code

        threads = [threading.Thread(target=_run, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        _preamble, rows = _mod.cost_ledger._parse_cost_ledger_file_text(cost_ledger_file.read_text())
        assert len(rows) == 1
        assert sorted(exit_codes) == [0, 1]

    def test_two_racing_records_onto_not_yet_existing_parent_directory_produce_exactly_one_row(
        self, fake_projects, cost_ledger_enabled, tmp_path, monkeypatch
    ):
        """Same race as above, but onto a path whose parent directory
        doesn't exist yet -- the one directory-existence invariant this
        auto-create feature actually changes. Both threads call
        mkdir(parents=True, exist_ok=True) before acquiring the lock;
        Path.mkdir(exist_ok=True) is documented race-safe under concurrent
        creation, and this pins that property for this specific code path
        rather than relying on it being true elsewhere."""
        ledger_path = tmp_path / "fresh-config-dir" / "cost-ledger.md"
        monkeypatch.setattr(_mod.cost_ledger, "_cost_ledger_path", lambda: ledger_path)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        args = _cost_ledger_args(record=True)
        today = date(2026, 6, 3)
        exit_codes: list[int | None] = [None, None]

        def _run(i: int) -> None:
            try:
                _mod.cost_ledger._cost_ledger_report(args, today)
                exit_codes[i] = 0
            except SystemExit as exc:
                exit_codes[i] = exc.code

        threads = [threading.Thread(target=_run, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        _preamble, rows = _mod.cost_ledger._parse_cost_ledger_file_text(ledger_path.read_text())
        assert len(rows) == 1
        assert sorted(exit_codes) == [0, 1]


class TestCostLedgerDefaultPathCliWiring:
    def test_cmd_cost_ledger_record_lands_at_config_dir_default_path(self, monkeypatch, tmp_path):
        """cmd_cost_ledger's own dispatch wiring -- not just a direct
        _cost_ledger_report() call -- resolves the ledger's default
        location through config_dir() when COST_LEDGER_PATH is unset.
        cmd_cost_ledger reads datetime.now(UTC) itself with no override
        parameter, so "today" is pinned via a real datetime subclass (not a
        bare stub, so the datetime(...) constructor calls inside
        _cost_ledger_report keep working) instead of depending on the real
        wall clock."""
        monkeypatch.delenv("COST_LEDGER_PATH", raising=False)
        cfg_dir = tmp_path / "fresh-claude-config"
        cfg_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_dir))
        (cfg_dir / ".cost-ledger-enabled").touch()
        expected_path = cfg_dir / "cost-ledger.md"
        expected_path.write_text(
            _mod.cost_ledger._COST_LEDGER_HEADER_LINE + "\n" + _mod.cost_ledger._COST_LEDGER_SEPARATOR_LINE + "\n"
        )

        projects = tmp_path / "projects"
        proj = projects / "-home-user-testrepo"
        proj.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(proj / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 6, 3, 12, 0, tzinfo=tz)

        monkeypatch.setattr(_mod.cost_ledger, "datetime", _FixedDatetime)

        _mod.cost_ledger.cmd_cost_ledger(_cost_ledger_args(record=True))

        assert _mod.cost_ledger._cost_ledger_path() == expected_path
        _preamble, rows = _mod.cost_ledger._parse_cost_ledger_file_text(expected_path.read_text())
        assert len(rows) == 1
        assert rows[0]["week"] == "2026-W23"
