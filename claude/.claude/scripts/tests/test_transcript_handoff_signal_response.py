"""Tests for transcript_analysis/handoff_signal_response.py's reporting side: cmd_handoff_signal_response end to
end, spend ranking, curation cards, the startup-burn benchmark, and the aggregate and markdown renderers."""
import importlib.util
import json
import random
import sys
from pathlib import Path

import pytest

from ._handoff_nudge_helpers import _check_call_turn, _check_result_json, _handoff_advisory_attachment
from .conftest import _priced, _thinking_block, _tool_result, _two_declared_roots, _user_msg, _write_jsonl

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


def _handoff_signal_response_args(
    *, projects: str = "*", this_repo: bool = False, config_dir: str | None = None,
    no_redact: bool = False, sample: int = 0, seed: int | None = None,
    output_format: str = "json", context_turns: int = 0,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "config_dir": config_dir,
        "no_redact": no_redact,
        "sample": sample,
        "seed": seed,
        "output_format": output_format,
        "context_turns": context_turns,
    })()


class TestCmdHandoffSignalResponseScopeAndRedaction:
    """cmd_handoff_signal_response's own CLI-boundary contract: the
    resolved-scope banner and the context-distribution-style multi-root
    --no-redact refusal (.claude/plans/handoff-nudge-rationalization-gap.md)."""

    def test_resolved_scope_banner_reports_a_single_root(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000, output=100)])
        _mod.handoff_signal_response.cmd_handoff_signal_response(_handoff_signal_response_args())
        out = capsys.readouterr().out
        assert "HANDOFF SIGNAL RESPONSE SOURCES" in out
        assert "1 root" in out

    @pytest.mark.parametrize("sample", [0, 5])
    def test_no_redact_refused_with_multi_root(self, tmp_path, monkeypatch, capsys, sample):
        """sample=5 pins the higher-risk curation-card path (raw excerpts and
        text), which sample=0's aggregate-only fixture never reached."""
        _two_declared_roots(tmp_path, monkeypatch)
        with pytest.raises(SystemExit) as exc_info:
            _mod.handoff_signal_response.cmd_handoff_signal_response(_handoff_signal_response_args(no_redact=True, sample=sample))
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "--no-redact" in err
        assert "more than one root" in err

    def test_no_redact_allowed_and_stamps_banner_at_single_root(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000, output=100)])
        _mod.handoff_signal_response.cmd_handoff_signal_response(_handoff_signal_response_args(no_redact=True))
        captured = capsys.readouterr()
        assert _mod._DO_NOT_PUBLISH_BANNER in captured.out
        assert _mod._DO_NOT_PUBLISH_BANNER in captured.err

    def test_default_redact_omits_do_not_publish_banner(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000, output=100)])
        _mod.handoff_signal_response.cmd_handoff_signal_response(_handoff_signal_response_args())
        captured = capsys.readouterr()
        assert _mod._DO_NOT_PUBLISH_BANNER not in captured.out
        assert _mod._DO_NOT_PUBLISH_BANNER not in captured.err


class TestCmdHandoffSignalResponseSampleCards:
    """--sample curation-card output: session-id redaction and the excerpt
    join against the real (re-read) session file."""

    def test_sample_redacts_session_id_by_default(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _check_call_turn(input=200_000, output=1_000, request_id="r1"),
            _user_msg([_tool_result("chk1", _check_result_json(over_threshold=True))]),
        ])
        _mod.handoff_signal_response.cmd_handoff_signal_response(
            _handoff_signal_response_args(sample=5, seed=1, output_format="json")
        )
        out = capsys.readouterr().out
        cards = json.loads(out.split("\n", 1)[1])  # drop the resolved-scope header line
        assert len(cards) == 1
        assert cards[0]["session_id"] == "session-1"
        assert cards[0]["session_id"] != "sess"  # "sess" is the real jsonl.stem this must not leak

    def test_sample_no_redact_emits_raw_session_id(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _check_call_turn(input=200_000, output=1_000, request_id="r1"),
            _user_msg([_tool_result("chk1", _check_result_json(over_threshold=True))]),
        ])
        _mod.handoff_signal_response.cmd_handoff_signal_response(
            _handoff_signal_response_args(sample=5, seed=1, output_format="json", no_redact=True)
        )
        out = capsys.readouterr().out
        # DO NOT PUBLISH banner (stdout) + resolved-scope header precede the JSON array.
        json_start = out.index("[")
        cards = json.loads(out[json_start:])
        assert len(cards) == 1
        assert cards[0]["session_id"] == "sess"

    def test_sample_card_excerpt_matches_next_eligible_assistant_text_turn(self, fake_projects, capsys):
        """Drives the excerpt seam through the real CLI path (file write ->
        cmd_handoff_signal_response -> card), not just the in-memory unit
        test. _handoff_signal_response_cards re-reads the session file
        independently of the initial scan's own deduped list."""
        excerpt_text = "Continuing because remaining steps are few, not because of cost."
        _write_jsonl(fake_projects / "sess.jsonl", [
            _check_call_turn(input=200_000, output=1_000, request_id="r1"),
            _user_msg([_tool_result("chk1", _check_result_json(over_threshold=True))]),
            _priced(
                "claude-sonnet-5", input=210_000, output=500, request_id="r2",
                content=[{"type": "text", "text": excerpt_text}],
            ),
        ])
        _mod.handoff_signal_response.cmd_handoff_signal_response(
            _handoff_signal_response_args(sample=5, seed=1, output_format="json")
        )
        out = capsys.readouterr().out
        cards = json.loads(out.split("\n", 1)[1])  # drop the resolved-scope header line
        assert cards[0]["excerpt"] == excerpt_text

    def test_multiple_signals_from_the_same_session_get_the_same_redacted_label(self, fake_projects, capsys):
        """Two signals from one real session must redact to the same
        session-N label, not session-1/session-2 for the same underlying
        session -- pins _assign_session_redact_label's per-session (not
        per-row) keying."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _check_call_turn("chk1", input=200_000, output=1_000, request_id="r1"),
            _user_msg([_tool_result("chk1", _check_result_json(over_threshold=True))]),
            _check_call_turn("chk2", input=210_000, output=1_000, request_id="r2"),
            _user_msg([_tool_result("chk2", _check_result_json(over_threshold=True))]),
        ])
        _mod.handoff_signal_response.cmd_handoff_signal_response(
            _handoff_signal_response_args(sample=5, seed=1, output_format="json")
        )
        out = capsys.readouterr().out
        cards = json.loads(out.split("\n", 1)[1])  # drop the resolved-scope header line
        assert len(cards) == 2
        assert cards[0]["session_id"] == cards[1]["session_id"] == "session-1"


class TestCmdHandoffSignalResponseContextTurns:
    """--context-turns wiring through the CLI: the --sample precondition,
    the omitted-flag no-op contract, and the forward_context shape --
    including the thinking-block regression the flag exists to close."""

    def test_context_turns_without_sample_exits_2_naming_both_flags(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            _mod.handoff_signal_response.cmd_handoff_signal_response(_handoff_signal_response_args(sample=0, context_turns=2))
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "--context-turns" in err
        assert "--sample" in err

    def test_negative_context_turns_exits_2_with_stderr_message(self, fake_projects, capsys):
        """--sample is set here so this isolates the negative-value check
        from the --context-turns-requires---sample check above."""
        with pytest.raises(SystemExit) as exc_info:
            _mod.handoff_signal_response.cmd_handoff_signal_response(_handoff_signal_response_args(sample=1, context_turns=-1))
        assert exc_info.value.code == 2
        assert "--context-turns must not be negative" in capsys.readouterr().err

    def test_context_turns_omitted_forward_context_key_absent_from_every_card(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _check_call_turn(input=200_000, output=1_000, request_id="r1"),
            _user_msg([_tool_result("chk1", _check_result_json(over_threshold=True))]),
        ])
        _mod.handoff_signal_response.cmd_handoff_signal_response(
            _handoff_signal_response_args(sample=5, seed=1, output_format="json")
        )
        out = capsys.readouterr().out
        cards = json.loads(out.split("\n", 1)[1])  # drop the resolved-scope header line
        assert len(cards) == 1
        assert "forward_context" not in cards[0]

    def test_context_turns_surfaces_up_to_n_turns_with_turn_offset(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _check_call_turn(input=200_000, output=1_000, request_id="r1"),
            _user_msg([_tool_result("chk1", _check_result_json(over_threshold=True))]),
            _priced("claude-sonnet-5", input=200_000, output=100, request_id="r2",
                    content=[{"type": "text", "text": "turn one text"}]),
            _priced("claude-sonnet-5", input=200_000, output=100, request_id="r3",
                    content=[{"type": "text", "text": "turn two text"}]),
        ])
        _mod.handoff_signal_response.cmd_handoff_signal_response(
            _handoff_signal_response_args(sample=5, seed=1, output_format="json", context_turns=2)
        )
        out = capsys.readouterr().out
        cards = json.loads(out.split("\n", 1)[1])  # drop the resolved-scope header line
        forward_context = cards[0]["forward_context"]
        assert [(t["turn_offset"], t["text"]) for t in forward_context] == [
            (1, "turn one text"), (2, "turn two text"),
        ]

    def test_context_turns_returns_fewer_than_n_when_the_transcript_runs_out(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _check_call_turn(input=200_000, output=1_000, request_id="r1"),
            _user_msg([_tool_result("chk1", _check_result_json(over_threshold=True))]),
            _priced("claude-sonnet-5", input=200_000, output=100, request_id="r2",
                    content=[{"type": "text", "text": "only turn"}]),
        ])
        _mod.handoff_signal_response.cmd_handoff_signal_response(
            _handoff_signal_response_args(sample=5, seed=1, output_format="json", context_turns=5)
        )
        out = capsys.readouterr().out
        cards = json.loads(out.split("\n", 1)[1])  # drop the resolved-scope header line
        assert len(cards[0]["forward_context"]) == 1

    def test_thinking_only_turn_surfaces_in_forward_context_but_not_in_the_excerpt(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _check_call_turn(input=200_000, output=1_000, request_id="r1"),
            _user_msg([_tool_result("chk1", _check_result_json(over_threshold=True))]),
            _priced("claude-sonnet-5", input=200_000, output=100, request_id="r2", content=[_thinking_block()]),
        ])
        _mod.handoff_signal_response.cmd_handoff_signal_response(
            _handoff_signal_response_args(sample=5, seed=1, output_format="json", context_turns=1)
        )
        out = capsys.readouterr().out
        cards = json.loads(out.split("\n", 1)[1])  # drop the resolved-scope header line
        assert cards[0]["excerpt"] == ""
        assert cards[0]["forward_context"] == [{"turn_offset": 1, "text": "", "thinking": "some thought"}]

    @pytest.mark.parametrize("no_redact", [False, True])
    def test_forward_context_never_contains_session_id_or_path_fields(self, fake_projects, capsys, no_redact):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _check_call_turn(input=200_000, output=1_000, request_id="r1"),
            _user_msg([_tool_result("chk1", _check_result_json(over_threshold=True))]),
            _priced("claude-sonnet-5", input=200_000, output=100, request_id="r2",
                    content=[{"type": "text", "text": "turn text"}]),
        ])
        _mod.handoff_signal_response.cmd_handoff_signal_response(
            _handoff_signal_response_args(
                sample=5, seed=1, output_format="json", context_turns=1, no_redact=no_redact,
            )
        )
        out = capsys.readouterr().out
        cards = json.loads(out[out.index("["):])
        for turn in cards[0]["forward_context"]:
            assert set(turn) == {"turn_offset", "text", "thinking"}


class TestCmdHandoffSignalResponseContextTurnsViaRealArgparse:
    """Exercises --context-turns through the real argparse CLI
    (build_parser()), not the _handoff_signal_response_args() test-helper
    shortcut every other handoff-signal-response test uses -- cmd_handoff_signal_response
    reads the flag via getattr(args, "context_turns", 0), a silent
    fallback-to-0 read that a dest/flag-string wiring bug would pass
    unnoticed by every helper-driven test above."""

    def test_context_turns_flag_drives_cmd_handoff_signal_response_through_the_real_parser(
        self, fake_projects, capsys,
    ):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _check_call_turn(input=200_000, output=1_000, request_id="r1"),
            _user_msg([_tool_result("chk1", _check_result_json(over_threshold=True))]),
            _priced("claude-sonnet-5", input=200_000, output=100, request_id="r2",
                    content=[{"type": "text", "text": "turn one text"}]),
        ])
        parser = _mod.build_parser()
        args = parser.parse_args(["handoff-signal-response", "--sample", "5", "--context-turns", "2"])
        assert args.context_turns == 2
        assert args.func == _mod.handoff_signal_response.cmd_handoff_signal_response

        _mod.handoff_signal_response.cmd_handoff_signal_response(args)

        out = capsys.readouterr().out
        cards = json.loads(out.split("\n", 1)[1])  # drop the resolved-scope header line
        assert cards[0]["forward_context"] == [{"turn_offset": 1, "text": "turn one text", "thinking": ""}]

    def test_negative_context_turns_through_the_real_parser_exits_2(self, fake_projects, capsys):
        parser = _mod.build_parser()
        args = parser.parse_args(["handoff-signal-response", "--sample", "5", "--context-turns", "-1"])
        assert args.context_turns == -1

        with pytest.raises(SystemExit) as exc_info:
            _mod.handoff_signal_response.cmd_handoff_signal_response(args)
        assert exc_info.value.code == 2


class TestCmdHandoffSignalResponseSampleTruncation:
    """--sample N ranks by post-signal spend and truncates to exactly N
    cards. TestCmdHandoffSignalResponseSampleCards' own fixtures build exactly
    one signal-bearing session each, so truncation itself is untested there."""

    def test_sample_n_truncates_more_than_n_signal_rows_to_exactly_n_cards(self, fake_projects, capsys):
        for i in range(8):
            _write_jsonl(fake_projects / f"sess{i}.jsonl", [
                _check_call_turn(input=200_000, output=1_000, request_id="r1"),
                _user_msg([_tool_result("chk1", _check_result_json(over_threshold=True))]),
            ])
        _mod.handoff_signal_response.cmd_handoff_signal_response(
            _handoff_signal_response_args(sample=3, seed=1, output_format="json")
        )
        out = capsys.readouterr().out
        cards = json.loads(out.split("\n", 1)[1])  # drop the resolved-scope header line
        assert len(cards) == 3


class TestCmdHandoffSignalResponseSampleRanking:
    """--sample ranks by post-signal spend (.claude/plans/handoff-nudge-rationalization-gap.md
    row 14): the highest-spend rows are where a wrong continue-decision
    actually cost something."""

    @staticmethod
    def _signal_session(post_signal_output: int) -> list[dict]:
        return [
            _check_call_turn(input=200_000, output=1_000, request_id="r1"),
            _user_msg([_tool_result("chk1", _check_result_json(over_threshold=True))]),
            _priced("claude-sonnet-5", input=200_000, output=post_signal_output, request_id="r2"),
        ]

    def test_sample_sorted_descending_by_dollars_after_signal(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "low.jsonl", self._signal_session(1_000))
        _write_jsonl(fake_projects / "mid.jsonl", self._signal_session(20_000))
        _write_jsonl(fake_projects / "high.jsonl", self._signal_session(50_000))
        _mod.handoff_signal_response.cmd_handoff_signal_response(
            _handoff_signal_response_args(sample=3, seed=1, output_format="json", no_redact=True)
        )
        out = capsys.readouterr().out
        cards = json.loads(out[out.index("["):])
        assert [c["session_id"] for c in cards] == ["high", "mid", "low"]

    def test_seed_produces_reproducible_tie_break_order_across_invocations(self, fake_projects, capsys):
        """Multiple $0.00 rows (no turns after the signal) tie on
        dollars_after_signal; a given --seed must break the tie the same way
        every run, via a pre-shuffle before the stable sort."""
        names = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]
        for name in names:
            _write_jsonl(fake_projects / f"{name}.jsonl", [
                _check_call_turn(input=200_000, output=1_000, request_id="r1"),
                _user_msg([_tool_result("chk1", _check_result_json(over_threshold=True))]),
            ])
        expected_order = list(names)
        random.Random(99).shuffle(expected_order)

        _mod.handoff_signal_response.cmd_handoff_signal_response(
            _handoff_signal_response_args(sample=6, seed=99, output_format="json", no_redact=True)
        )
        first_out = capsys.readouterr().out
        first_order = [c["session_id"] for c in json.loads(first_out[first_out.index("["):])]

        _mod.handoff_signal_response.cmd_handoff_signal_response(
            _handoff_signal_response_args(sample=6, seed=99, output_format="json", no_redact=True)
        )
        second_out = capsys.readouterr().out
        second_order = [c["session_id"] for c in json.loads(second_out[second_out.index("["):])]

        assert first_order == second_order == expected_order

    def test_omitting_seed_keeps_ties_in_scan_order_and_is_repeatable(self, fake_projects, capsys):
        """No --seed means no shuffle at all, not an unseeded-but-fixed RNG:
        ties keep the rows' scan order (alphabetical by session filename),
        deterministically across repeated runs."""
        names = ["alpha", "bravo", "charlie", "delta"]
        for name in names:
            _write_jsonl(fake_projects / f"{name}.jsonl", [
                _check_call_turn(input=200_000, output=1_000, request_id="r1"),
                _user_msg([_tool_result("chk1", _check_result_json(over_threshold=True))]),
            ])

        _mod.handoff_signal_response.cmd_handoff_signal_response(
            _handoff_signal_response_args(sample=4, output_format="json", no_redact=True)
        )
        first_out = capsys.readouterr().out
        first_order = [c["session_id"] for c in json.loads(first_out[first_out.index("["):])]

        _mod.handoff_signal_response.cmd_handoff_signal_response(
            _handoff_signal_response_args(sample=4, output_format="json", no_redact=True)
        )
        second_out = capsys.readouterr().out
        second_order = [c["session_id"] for c in json.loads(second_out[second_out.index("["):])]

        assert first_order == second_order == names

    def test_sample_with_no_signal_rows_in_scope_produces_empty_cards(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000, output=100)])
        _mod.handoff_signal_response.cmd_handoff_signal_response(
            _handoff_signal_response_args(sample=5, seed=1, output_format="json", no_redact=True)
        )
        out = capsys.readouterr().out
        cards = json.loads(out[out.index("["):])
        assert cards == []


class TestRankSignalRowsBySpend:
    """_rank_signal_rows_by_spend's own ranking/tie-break contract, exercised
    directly against fabricated rows rather than through the CLI."""

    def test_ranks_descending_by_dollars_after_signal(self):
        rows = [
            {"dollars_after_signal": 1.0, "session_id": "low"},
            {"dollars_after_signal": 5.0, "session_id": "high"},
            {"dollars_after_signal": 3.0, "session_id": "mid"},
        ]
        ranked = _mod.handoff_signal_response._rank_signal_rows_by_spend(rows, sample_n=3, seed=None)
        assert [r["session_id"] for r in ranked] == ["high", "mid", "low"]

    def test_seeded_tie_break_is_reproducible_across_calls(self):
        rows = [{"dollars_after_signal": 0.0, "session_id": name} for name in "abcdef"]
        first = _mod.handoff_signal_response._rank_signal_rows_by_spend(rows, sample_n=6, seed=99)
        second = _mod.handoff_signal_response._rank_signal_rows_by_spend(rows, sample_n=6, seed=99)
        assert [r["session_id"] for r in first] == [r["session_id"] for r in second]

    def test_omitting_seed_keeps_ties_in_input_order(self):
        rows = [{"dollars_after_signal": 0.0, "session_id": name} for name in "abcdef"]
        ranked = _mod.handoff_signal_response._rank_signal_rows_by_spend(rows, sample_n=6, seed=None)
        assert [r["session_id"] for r in ranked] == list("abcdef")


class TestHandoffSignalResponseAggregateReport:
    """_handoff_signal_response_aggregate_report's own conversion-rate and
    per-group breakdown math, pinned against hand-computed values for a
    mixed kind/marker-context row set -- the census run's headline numbers."""

    def test_conversion_rate_and_breakdown_match_hand_computed_values(self, capsys):
        rows = [
            {"session_id": "s1", "kind": _mod.handoff_signal_response._HANDOFF_SIGNAL_CHECK, "marker_active": False,
             "handoff_followed": True, "dollars_after_signal": 1.0},
            {"session_id": "s1", "kind": _mod.handoff_signal_response._HANDOFF_SIGNAL_ADVISORY, "marker_active": True,
             "handoff_followed": False, "dollars_after_signal": 3.0},
            {"session_id": "s2", "kind": _mod.handoff_signal_response._HANDOFF_SIGNAL_CHECK, "marker_active": True,
             "handoff_followed": False, "dollars_after_signal": 2.0},
            {"session_id": "s3", "kind": _mod.handoff_signal_response._HANDOFF_SIGNAL_HARD_BLOCK, "marker_active": False,
             "handoff_followed": True, "dollars_after_signal": 5.0},
        ]
        _mod.handoff_signal_response._handoff_signal_response_aggregate_report(
            rows, log_diagnostic=None, benchmark_dollars=None,
        )
        out = capsys.readouterr().out

        assert "Sessions with at least one signal: 3" in out
        assert "Conversion rate (a same-session /handoff followed the signal): 50.0% (2/4)" in out

        def _breakdown_row(label: str) -> list[str]:
            for line in out.splitlines():
                if line.startswith(label):
                    return line[len(label):].split()
            raise AssertionError(f"breakdown row not found for {label!r}")

        # By signal kind: check={rows 0,2} advisory={row 1} hard-block={row 3}.
        assert _breakdown_row("check") == ["2", "50.0%", "1.50"]
        assert _breakdown_row("advisory") == ["1", "0.0%", "3.00"]
        assert _breakdown_row("hard-block") == ["1", "100.0%", "5.00"]

        # By ready-for-review active-marker context: active={rows 1,2} inactive={rows 0,3}.
        assert _breakdown_row("active") == ["2", "0.0%", "2.50"]
        assert _breakdown_row("inactive") == ["2", "100.0%", "3.00"]

    def test_benchmark_line_and_exceeded_summary_printed_when_available(self, capsys):
        rows = [
            {"session_id": "s1", "kind": _mod.handoff_signal_response._HANDOFF_SIGNAL_CHECK, "marker_active": False,
             "handoff_followed": True, "dollars_after_signal": 1.0, "exceeds_startup_burn_benchmark": False},
            {"session_id": "s2", "kind": _mod.handoff_signal_response._HANDOFF_SIGNAL_CHECK, "marker_active": False,
             "handoff_followed": False, "dollars_after_signal": 5.0, "exceeds_startup_burn_benchmark": True},
        ]
        _mod.handoff_signal_response._handoff_signal_response_aggregate_report(
            rows, log_diagnostic=None, benchmark_dollars=2.00,
        )
        out = capsys.readouterr().out
        assert "Startup-burn benchmark (this scope): $2.00 per continuation session." in out
        assert "Signals whose post-signal spend exceeded the benchmark: 1 (50.0%)" in out

    def test_benchmark_reported_unavailable_when_none(self, capsys):
        _mod.handoff_signal_response._handoff_signal_response_aggregate_report(
            [], log_diagnostic=None, benchmark_dollars=None,
        )
        out = capsys.readouterr().out
        assert "Startup-burn benchmark (this scope): unavailable (no continuation sessions found in scope)." in out

    def test_breakdown_and_benchmark_output_never_pairs_a_count_with_a_summable_dollar_figure(self, capsys):
        """Pins the composition-reconstruction invariant itself, not just
        today's output string: no printed breakdown row may carry both a
        Signals/count column and a mean or per-unit-rate dollar column for
        the same group, and the startup-burn benchmark line must never
        surface a session/branch count alongside its dollar figure --
        pairing either would let a reader recombine the two halves into a
        raw pooled dollar total (docs/private-project-redaction.md
        "Composition is publication")."""
        rows = [
            {"session_id": "s1", "kind": _mod.handoff_signal_response._HANDOFF_SIGNAL_CHECK, "marker_active": False,
             "handoff_followed": True, "dollars_after_signal": 1.0, "exceeds_startup_burn_benchmark": False},
            {"session_id": "s2", "kind": _mod.handoff_signal_response._HANDOFF_SIGNAL_ADVISORY, "marker_active": True,
             "handoff_followed": False, "dollars_after_signal": 3.0, "exceeds_startup_burn_benchmark": True},
            {"session_id": "s3", "kind": _mod.handoff_signal_response._HANDOFF_SIGNAL_HARD_BLOCK, "marker_active": True,
             "handoff_followed": True, "dollars_after_signal": 5.0, "exceeds_startup_burn_benchmark": True},
        ]
        _mod.handoff_signal_response._handoff_signal_response_aggregate_report(
            rows, log_diagnostic=None, benchmark_dollars=2.00,
        )
        out = capsys.readouterr().out

        expected_header = f"{'Group':<14} {'Signals':>8} {'Handoff%':>9} {'Median $ after':>15}"
        header_lines = [line for line in out.splitlines() if line.startswith("Group")]
        assert header_lines, "no breakdown header printed"
        for header_line in header_lines:
            assert header_line == expected_header
        assert "Mean" not in out

        assert "Startup-burn benchmark (this scope): $2.00 per continuation session." in out
        assert "per continuation session." in out
        assert "non-first sessions" not in out
        assert "branches with corpus activity" not in out


class TestStartupBurnBenchmark:
    """_startup_burn_benchmark's own weighted-average formula, exercised
    directly against fabricated workstream dicts (_compute_workstream_dollars'
    own per-branch shape) rather than through a full corpus scan --
    complements TestCmdHandoffSignalResponseStartupBurnBenchmark's own
    end-to-end wiring test below."""

    def test_weighted_average_across_a_solo_branch_and_a_continuation_branch(self):
        workstream = {
            "solo": {"session_count": 1, "total_dollars": 2.41, "startup_burn_dollars": 0.0, "last_activity_ts": 0.0},
            "cont": {"session_count": 2, "total_dollars": 1.61, "startup_burn_dollars": 1.41, "last_activity_ts": 0.0},
        }
        benchmark, total_continuations, branch_count = _mod.handoff_signal_response._startup_burn_benchmark(workstream)
        assert benchmark == pytest.approx(1.41)
        assert total_continuations == 1
        assert branch_count == 2

    def test_benchmark_is_weighted_by_continuation_count_not_averaged_per_branch(self):
        """Two branches with very different continuation counts: the
        benchmark must be sum-of-burn / sum-of-continuations (weighted), not
        the mean of each branch's own per-branch average -- an unweighted
        average would let the low-volume branch skew the result as much as
        the high-volume one."""
        workstream = {
            # 10 continuations at $10.00/continuation.
            "high-volume": {
                "session_count": 11, "total_dollars": 0.0, "startup_burn_dollars": 100.0, "last_activity_ts": 0.0,
            },
            # 1 continuation at $2.00/continuation.
            "low-volume": {
                "session_count": 2, "total_dollars": 0.0, "startup_burn_dollars": 2.0, "last_activity_ts": 0.0,
            },
        }
        benchmark, total_continuations, _branch_count = _mod.handoff_signal_response._startup_burn_benchmark(workstream)
        # An unweighted mean of (10.00, 2.00) would be 6.00; the correct
        # weighted figure is (100 + 2) / (10 + 1).
        assert benchmark == pytest.approx(102.0 / 11)
        assert total_continuations == 11

    def test_benchmark_is_none_when_no_branch_has_a_continuation_session(self):
        workstream = {
            "solo-a": {"session_count": 1, "total_dollars": 1.0, "startup_burn_dollars": 0.0, "last_activity_ts": 0.0},
            "solo-b": {"session_count": 1, "total_dollars": 2.0, "startup_burn_dollars": 0.0, "last_activity_ts": 0.0},
        }
        benchmark, total_continuations, branch_count = _mod.handoff_signal_response._startup_burn_benchmark(workstream)
        assert benchmark is None
        assert total_continuations == 0
        assert branch_count == 2

    def test_empty_workstream_reports_unavailable_benchmark_and_zero_branches(self):
        benchmark, total_continuations, branch_count = _mod.handoff_signal_response._startup_burn_benchmark({})
        assert benchmark is None
        assert total_continuations == 0
        assert branch_count == 0


class TestCmdHandoffSignalResponseStartupBurnBenchmark:
    """cmd_handoff_signal_response's own end-to-end startup-burn benchmark
    wiring: a second, independent _resolve_project_scope pass feeds
    _compute_workstream_dollars, and _startup_burn_benchmark's weighted-average
    formula (.claude/plans/handoff-nudge-rationalization-gap.md) sets
    exceeds_startup_burn_benchmark on every signal row."""

    def test_benchmark_and_exceeds_flag_match_hand_computed_values(self, fake_projects, capsys):
        # Branch "solo": exactly one session -- contributes zero to both the
        # benchmark's numerator (startup_burn_dollars) and denominator
        # (session_count - 1 == 0). Its own signal's post-signal spend
        # ($2.00) ends up well above the benchmark computed below.
        _write_jsonl(fake_projects / "solo.jsonl", [
            _check_call_turn(branch="solo", input=200_000, output=1_000, request_id="r1"),
            _user_msg([_tool_result("chk1", _check_result_json(over_threshold=True))], branch="solo"),
            _priced("claude-sonnet-5", branch="solo", input=1_000_000, output=0, request_id="r2"),  # $2.00
        ])

        # Branch "cont": two sessions -- cont-1 (chronologically first)
        # contributes nothing; cont-2 (non-first) contributes both of its own
        # main-thread turns' dollars as startup burn (until_first_n_turns=5
        # is never reached with only 2 turns). Its own signal's post-signal
        # spend ($1.00) ends up below the benchmark.
        _write_jsonl(fake_projects / "cont-1.jsonl", [
            _priced("claude-sonnet-5", branch="cont", input=100_000, output=0, ts="2026-08-01T10:00:00.000Z"),
        ])  # $0.20, first session by time
        _write_jsonl(fake_projects / "cont-2.jsonl", [
            _check_call_turn(
                "chk2", branch="cont", input=200_000, output=1_000, request_id="r3", ts="2026-08-02T10:00:00.000Z",
            ),  # $0.41
            _user_msg(
                [_tool_result("chk2", _check_result_json(over_threshold=True))],
                branch="cont", ts="2026-08-02T10:00:01.000Z",
            ),
            _priced(
                "claude-sonnet-5", branch="cont", input=500_000, output=0, ts="2026-08-02T10:01:00.000Z",
            ),  # $1.00
        ])

        # Hand-computed benchmark: total_burn = cont-2's own two main-thread
        # turns ($0.41 + $1.00 = $1.41); total_continuations = 1 (only
        # branch "cont" has a non-first session); benchmark = $1.41 / 1.
        _mod.handoff_signal_response.cmd_handoff_signal_response(_handoff_signal_response_args(no_redact=True))
        out = capsys.readouterr().out
        assert "Startup-burn benchmark (this scope): $1.41 per continuation session." in out
        # solo's $2.00 exceeds $1.41; cont's $1.00 does not -- 1 of 2 signals.
        assert "Signals whose post-signal spend exceeded the benchmark: 1 (50.0%)" in out

    def test_exceeds_flag_set_per_card_above_and_below_the_benchmark(self, fake_projects, capsys):
        """Same fixture as above, read through the --sample curation-card
        path instead of the aggregate report, to pin exceeds_startup_burn_benchmark
        on the per-row/per-card data itself."""
        _write_jsonl(fake_projects / "solo.jsonl", [
            _check_call_turn(branch="solo", input=200_000, output=1_000, request_id="r1"),
            _user_msg([_tool_result("chk1", _check_result_json(over_threshold=True))], branch="solo"),
            _priced("claude-sonnet-5", branch="solo", input=1_000_000, output=0, request_id="r2"),  # $2.00
        ])
        _write_jsonl(fake_projects / "cont-1.jsonl", [
            _priced("claude-sonnet-5", branch="cont", input=100_000, output=0, ts="2026-08-01T10:00:00.000Z"),
        ])
        _write_jsonl(fake_projects / "cont-2.jsonl", [
            _check_call_turn(
                "chk2", branch="cont", input=200_000, output=1_000, request_id="r3", ts="2026-08-02T10:00:00.000Z",
            ),
            _user_msg(
                [_tool_result("chk2", _check_result_json(over_threshold=True))],
                branch="cont", ts="2026-08-02T10:00:01.000Z",
            ),
            _priced(
                "claude-sonnet-5", branch="cont", input=500_000, output=0, ts="2026-08-02T10:01:00.000Z",
            ),  # $1.00
        ])

        _mod.handoff_signal_response.cmd_handoff_signal_response(
            _handoff_signal_response_args(sample=2, seed=1, output_format="json", no_redact=True)
        )
        out = capsys.readouterr().out
        cards = json.loads(out[out.index("["):])
        by_session = {c["session_id"]: c for c in cards}
        assert by_session["solo"]["exceeds_startup_burn_benchmark"] is True
        assert by_session["cont-2"]["exceeds_startup_burn_benchmark"] is False

    def test_exceeds_flag_is_false_on_an_exact_tie_with_the_benchmark(self, fake_projects, capsys):
        """dollars_after_signal == benchmark_dollars exactly must resolve to
        False -- pins the "> benchmark_dollars" (not ">=") comparison."""
        _write_jsonl(fake_projects / "cont-1.jsonl", [
            _priced("claude-sonnet-5", branch="cont", input=100_000, output=0, ts="2026-08-01T10:00:00.000Z"),
        ])
        _write_jsonl(fake_projects / "cont-2.jsonl", [
            # $0 pre-signal turn, then a $1.00 post-signal turn -- cont-2's
            # own startup burn (both of its main-thread turns, since
            # until_first_n_turns=5 is never reached) is therefore exactly
            # $1.00, the same $1.00 dollars_after_signal below.
            _priced(
                "claude-sonnet-5", branch="cont", input=0, output=0, request_id="r1",
                ts="2026-08-02T10:00:00.000Z",
            ),
            _handoff_advisory_attachment(),
            _priced(
                "claude-sonnet-5", branch="cont", input=500_000, output=0, request_id="r2",
                ts="2026-08-02T10:01:00.000Z",
            ),  # $1.00
        ])

        _mod.handoff_signal_response.cmd_handoff_signal_response(
            _handoff_signal_response_args(sample=1, seed=1, output_format="json", no_redact=True)
        )
        out = capsys.readouterr().out
        cards = json.loads(out[out.index("["):])
        assert cards[0]["dollars_after_signal"] == pytest.approx(1.0)
        assert cards[0]["exceeds_startup_burn_benchmark"] is False

    def test_benchmark_unavailable_and_exceeds_flag_none_with_no_continuation_sessions(
        self, fake_projects, capsys
    ):
        """A degenerate corpus with zero continuation sessions anywhere in
        scope -- every branch has exactly one session -- reports the
        benchmark as unavailable rather than raising, and every row's
        exceeds_startup_burn_benchmark is None rather than a bool."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _check_call_turn(input=200_000, output=1_000, request_id="r1"),
            _user_msg([_tool_result("chk1", _check_result_json(over_threshold=True))]),
        ])

        _mod.handoff_signal_response.cmd_handoff_signal_response(_handoff_signal_response_args(no_redact=True))
        out = capsys.readouterr().out
        assert "Startup-burn benchmark (this scope): unavailable (no continuation sessions found in scope)." in out
        assert "Signals whose post-signal spend exceeded the benchmark" not in out

        _mod.handoff_signal_response.cmd_handoff_signal_response(
            _handoff_signal_response_args(sample=1, seed=1, output_format="json", no_redact=True)
        )
        out = capsys.readouterr().out
        cards = json.loads(out[out.index("["):])
        assert len(cards) == 1
        assert cards[0]["exceeds_startup_burn_benchmark"] is None


class TestCmdHandoffSignalResponseOperatorLagDiagnostic:
    """.handoff-nudge.log's operator-response-lag cross-check, wired
    end-to-end through cmd_handoff_signal_response -- mirrors
    TestRearmBacktestReport's own .handoff-nudge.log fixture/config_dir
    pattern (fake_projects already monkeypatches config_dir() to tmp_path,
    the log's own real location)."""

    def test_log_diagnostic_reports_joined_count_and_median_lag(self, fake_projects, tmp_path, capsys):
        (tmp_path / ".handoff-nudge.log").write_text(
            "nudged session=sess est=150000 model=claude-sonnet-5 window=1000000 event=Stop\n"
        )
        _write_jsonl(fake_projects / "sess.jsonl", [
            _check_call_turn(input=200_000, output=1_000, request_id="r1"),
            _user_msg([_tool_result("chk1", _check_result_json(over_threshold=True))]),
        ])
        _mod.handoff_signal_response.cmd_handoff_signal_response(_handoff_signal_response_args())
        out = capsys.readouterr().out
        assert (
            "Operator-response-lag cross-check (.handoff-nudge.log 'nudged' lines): 1 joined"
            " (0 excluded -- no matching session in scope), median lag 51,000 tokens past the fire point" in out
        )


class TestFormatHandoffSignalCardsAsMarkdown:
    """_format_handoff_signal_cards_as_markdown's own document shape --
    mirrors _format_samples_as_markdown's own curation-card format."""

    _CARD = {
        "session_id": "session-1",
        "kind": _mod.handoff_signal_response._HANDOFF_SIGNAL_CHECK,
        "position": 10,
        "context_at_turn": 200_000,
        "threshold": 150_000,
        "marker_active": False,
        "handoff_followed": True,
        "turns_after_signal": 3,
        "dollars_after_signal": 1.23,
        "session_total_dollars": 4.00,
        "pct_spend_after_signal": 0.3075,
        "exceeds_startup_burn_benchmark": True,
        "excerpt": "Confirmed over threshold, running /handoff now.",
    }

    def test_header_names_signal_count_sample_n_and_seed(self):
        out = _mod.handoff_signal_response._format_handoff_signal_cards_as_markdown(
            [self._CARD], sample_n=5, seed=7, benchmark_dollars=0.50,
        )
        assert out.startswith("# handoff-signal-response curation — 1 signal(s)")
        assert "--sample 5" in out
        assert "--seed 7" in out

    def test_header_reports_unset_seed_as_none_placeholder(self):
        out = _mod.handoff_signal_response._format_handoff_signal_cards_as_markdown(
            [self._CARD], sample_n=5, seed=None, benchmark_dollars=0.50,
        )
        assert "--seed (none)" in out

    def test_header_names_the_startup_burn_benchmark_dollar_figure(self):
        out = _mod.handoff_signal_response._format_handoff_signal_cards_as_markdown(
            [self._CARD], sample_n=1, seed=1, benchmark_dollars=0.50,
        )
        assert "Startup-burn benchmark (this scope): $0.50 per continuation session." in out

    def test_header_reports_benchmark_unavailable_when_none(self):
        out = _mod.handoff_signal_response._format_handoff_signal_cards_as_markdown(
            [self._CARD], sample_n=1, seed=1, benchmark_dollars=None,
        )
        assert "Startup-burn benchmark (this scope): unavailable" in out

    def test_one_section_header_per_card(self):
        cards = [dict(self._CARD, session_id=f"session-{i}") for i in range(3)]
        out = _mod.handoff_signal_response._format_handoff_signal_cards_as_markdown(
            cards, sample_n=3, seed=1, benchmark_dollars=0.50
        )
        section_headers = [line for line in out.splitlines() if line.startswith("## ")]
        assert len(section_headers) == 3

    def test_section_names_session_kind_and_position(self):
        out = _mod.handoff_signal_response._format_handoff_signal_cards_as_markdown(
            [self._CARD], sample_n=1, seed=1, benchmark_dollars=0.50
        )
        assert "session `session-1` — check signal at turn 10" in out

    def test_excerpt_is_interpolated_into_a_blockquote(self):
        out = _mod.handoff_signal_response._format_handoff_signal_cards_as_markdown(
            [self._CARD], sample_n=1, seed=1, benchmark_dollars=0.50
        )
        assert "> Confirmed over threshold, running /handoff now." in out

    def test_empty_excerpt_renders_the_no_eligible_text_placeholder(self):
        card = dict(self._CARD, excerpt="")
        out = _mod.handoff_signal_response._format_handoff_signal_cards_as_markdown(
            [card], sample_n=1, seed=1, benchmark_dollars=0.50
        )
        assert "> (no eligible assistant text turn followed the signal)" in out

    def test_verdict_checklist_present_in_each_section(self):
        out = _mod.handoff_signal_response._format_handoff_signal_cards_as_markdown(
            [self._CARD], sample_n=1, seed=1, benchmark_dollars=0.50
        )
        assert "Verdict: [ ] cost-grounded" in out

    def test_card_line_shows_pct_spend_and_exceeds_benchmark(self):
        out = _mod.handoff_signal_response._format_handoff_signal_cards_as_markdown(
            [self._CARD], sample_n=1, seed=1, benchmark_dollars=0.50
        )
        assert "% of session spend after signal: 30.8%  ·  exceeds startup-burn benchmark: yes" in out

    def test_card_line_reports_pct_and_exceeds_as_not_applicable_when_none(self):
        card = dict(self._CARD, pct_spend_after_signal=None, exceeds_startup_burn_benchmark=None)
        out = _mod.handoff_signal_response._format_handoff_signal_cards_as_markdown(
            [card], sample_n=1, seed=1, benchmark_dollars=None
        )
        assert "% of session spend after signal: n/a  ·  exceeds startup-burn benchmark: n/a" in out

    def test_forward_context_key_absent_renders_no_forward_context_block(self):
        out = _mod.handoff_signal_response._format_handoff_signal_cards_as_markdown(
            [self._CARD], sample_n=1, seed=1, benchmark_dollars=0.50
        )
        assert "Forward context" not in out

    def test_forward_context_present_renders_a_labeled_turn_line(self):
        card = dict(self._CARD, forward_context=[{"turn_offset": 1, "text": "wrapping up now", "thinking": ""}])
        out = _mod.handoff_signal_response._format_handoff_signal_cards_as_markdown(
            [card], sample_n=1, seed=1, benchmark_dollars=0.50
        )
        assert "**Forward context (next 1 turn(s)):**" in out
        assert "- turn +1: text: wrapping up now" in out

    def test_forward_context_turn_with_both_fields_empty_is_skipped(self):
        card = dict(self._CARD, forward_context=[{"turn_offset": 1, "text": "", "thinking": ""}])
        out = _mod.handoff_signal_response._format_handoff_signal_cards_as_markdown(
            [card], sample_n=1, seed=1, benchmark_dollars=0.50
        )
        assert "turn +1" not in out


class TestCmdHandoffSignalResponseMarkdownFormat:
    """cmd_handoff_signal_response(..., output_format='md') integration:
    --format md produces the curation document, not the JSON array. Models
    the sibling audit-routing-samples subcommand's own md-format test shape."""

    def test_format_md_emits_curation_document_with_a_verdict_checklist(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _check_call_turn(input=200_000, output=1_000, request_id="r1"),
            _user_msg([_tool_result("chk1", _check_result_json(over_threshold=True))]),
        ])
        _mod.handoff_signal_response.cmd_handoff_signal_response(
            _handoff_signal_response_args(sample=5, seed=1, output_format="md")
        )
        out = capsys.readouterr().out
        assert "# handoff-signal-response curation" in out
        assert "Verdict: [ ] cost-grounded" in out

    def test_context_turns_renders_a_forward_context_subsection(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _check_call_turn(input=200_000, output=1_000, request_id="r1"),
            _user_msg([_tool_result("chk1", _check_result_json(over_threshold=True))]),
            _priced("claude-sonnet-5", input=200_000, output=100, request_id="r2",
                    content=[{"type": "text", "text": "wrapping up now"}]),
        ])
        _mod.handoff_signal_response.cmd_handoff_signal_response(
            _handoff_signal_response_args(sample=5, seed=1, output_format="md", context_turns=1)
        )
        out = capsys.readouterr().out
        assert "**Forward context (next 1 turn(s)):**" in out
        assert "- turn +1: text: wrapping up now" in out
