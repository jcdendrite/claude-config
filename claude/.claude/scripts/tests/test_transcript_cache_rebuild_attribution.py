"""Tests for transcript_analysis/cache_rebuild.py (cmd_cache_rebuild): subagent idle-gap cause attribution, both unit
and report tests."""
import importlib.util
import sys
from pathlib import Path

import pytest

from ._cache_rebuild_helpers import (
    _cache_rebuild_args,
    _extract_cache_rebuild_attribution_row,
    _extract_cache_rebuild_row,
)
from .conftest import (
    _asst,
    _bash_use,
    _edit_use,
    _priced,
    _tool_result,
    _user_msg,
    _write_jsonl,
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


def _tool_result_record(tool_id: str, *, ts: str, text: str = "ok") -> dict:
    """A user record carrying one tool_result block, for
    _attribute_idle_gap_cause's direct unit tests below."""
    return _user_msg([_tool_result(tool_id, text)], ts=ts)


def _meta_marker_record(text: str, *, ts: str, is_meta: bool = True, is_sidechain: bool = True) -> dict:
    """A harness-injected meta-marker user record (background-task or
    coordinator-message), for _attribute_idle_gap_cause's direct unit tests
    below -- conftest builds no meta-record helper of its own, so this
    mirrors TestCacheRebuildOriginSplit's own rec["isSidechain"] = True
    idiom of setting the flag post-hoc on a plain _user_msg record."""
    rec = _user_msg(text, ts=ts)
    rec["isMeta"] = is_meta
    rec["isSidechain"] = is_sidechain
    return rec


class TestAttributeIdleGapCause:
    """Direct unit tests for _attribute_idle_gap_cause -- hand-built
    prior_turn/window dicts, no fixture transcript and no
    _cache_rebuild_report run, so a marker-shape bug is pinned without
    needing a full transcript fixture. Covers
    .claude/plans/subagent-idle-gap-cause-attribution.md's Verification
    section's precedence, self-scoping, and clock-skew cases, plus the
    bash_shape/no-command/other-command split. Sleep-poll wait follows the
    winning Bash marker, not window order."""

    def test_bash_tool_result_at_gap_end_attributes_to_own_bash_call_with_high_covered_share(self):
        prior_turn = _asst("claude-sonnet-5", content=[_bash_use("tool-1", "pytest -k foo")])
        window = [_tool_result_record("tool-1", ts="2026-08-01T10:05:50.000Z")]
        gap_start_ts = _mod._parse_ts("2026-08-01T10:00:00.000Z")
        cause, covered_share, _bash_shape = _mod.cache_rebuild_rules._attribute_idle_gap_cause(
            prior_turn, window, gap_start_ts=gap_start_ts, gap_seconds=360.0
        )
        assert cause == _mod.cache_rebuild_rules._ATTR_OWN_BASH
        assert covered_share == pytest.approx(350 / 360)

    def test_background_task_marker_attributes_to_waiting_on_background_task(self):
        prior_turn = _asst("claude-sonnet-5", content=[])
        window = [_meta_marker_record(
            f"{_mod.cache_rebuild_rules._BACKGROUND_TASK_MARKER_PREFIX} a backgrounded task finished",
            ts="2026-08-01T10:05:55.000Z",
        )]
        gap_start_ts = _mod._parse_ts("2026-08-01T10:00:00.000Z")
        cause, covered_share, _bash_shape = _mod.cache_rebuild_rules._attribute_idle_gap_cause(
            prior_turn, window, gap_start_ts=gap_start_ts, gap_seconds=360.0
        )
        assert cause == _mod.cache_rebuild_rules._ATTR_BACKGROUND_TASK
        assert covered_share == pytest.approx(355 / 360)

    def test_coordinator_message_marker_attributes_to_waiting_on_coordinator_message(self):
        prior_turn = _asst("claude-sonnet-5", content=[])
        window = [_meta_marker_record(
            f"{_mod.cache_rebuild_rules._COORDINATOR_MESSAGE_MARKER_PREFIX}: hello",
            ts="2026-08-01T10:05:55.000Z",
        )]
        gap_start_ts = _mod._parse_ts("2026-08-01T10:00:00.000Z")
        cause, covered_share, _bash_shape = _mod.cache_rebuild_rules._attribute_idle_gap_cause(
            prior_turn, window, gap_start_ts=gap_start_ts, gap_seconds=360.0
        )
        assert cause == _mod.cache_rebuild_rules._ATTR_COORDINATOR
        assert covered_share == pytest.approx(355 / 360)

    def test_empty_window_is_unattributed_with_no_covered_share(self):
        prior_turn = _asst("claude-sonnet-5", content=[_bash_use("tool-1", "pytest")])
        cause, covered_share, _bash_shape = _mod.cache_rebuild_rules._attribute_idle_gap_cause(
            prior_turn, [], gap_start_ts=_mod._parse_ts("2026-08-01T10:00:00.000Z"), gap_seconds=360.0
        )
        assert cause == _mod.cache_rebuild_rules._ATTR_UNATTRIBUTED
        assert covered_share is None

    def test_bash_then_coordinator_marker_in_window_last_marker_wins_coordinator(self):
        prior_turn = _asst("claude-sonnet-5", content=[_bash_use("tool-1", "pytest")])
        window = [
            _tool_result_record("tool-1", ts="2026-08-01T10:01:00.000Z"),
            _meta_marker_record(
                f"{_mod.cache_rebuild_rules._COORDINATOR_MESSAGE_MARKER_PREFIX}: hello", ts="2026-08-01T10:05:00.000Z",
            ),
        ]
        cause, _covered_share, _bash_shape = _mod.cache_rebuild_rules._attribute_idle_gap_cause(
            prior_turn, window, gap_start_ts=_mod._parse_ts("2026-08-01T10:00:00.000Z"), gap_seconds=360.0
        )
        assert cause == _mod.cache_rebuild_rules._ATTR_COORDINATOR

    def test_coordinator_then_bash_marker_in_window_last_marker_wins_bash(self):
        """Same two markers as the previous test, reversed window order --
        last-marker-wins must flip with them, proving precedence is
        window-order-based, not a fixed cause priority."""
        prior_turn = _asst("claude-sonnet-5", content=[_bash_use("tool-1", "pytest")])
        window = [
            _meta_marker_record(
                f"{_mod.cache_rebuild_rules._COORDINATOR_MESSAGE_MARKER_PREFIX}: hello", ts="2026-08-01T10:01:00.000Z",
            ),
            _tool_result_record("tool-1", ts="2026-08-01T10:05:00.000Z"),
        ]
        cause, _covered_share, _bash_shape = _mod.cache_rebuild_rules._attribute_idle_gap_cause(
            prior_turn, window, gap_start_ts=_mod._parse_ts("2026-08-01T10:00:00.000Z"), gap_seconds=360.0
        )
        assert cause == _mod.cache_rebuild_rules._ATTR_OWN_BASH

    def test_tool_result_id_prior_turn_never_emitted_is_unattributed(self):
        """Self-scoping: a tool_result whose tool_use_id isn't among the ids
        prior_turn's own Bash tool_use blocks emitted must not match, even
        though it's the only marker-shaped record in the window."""
        prior_turn = _asst("claude-sonnet-5", content=[_bash_use("tool-1", "pytest")])
        window = [_tool_result_record("tool-999", ts="2026-08-01T10:05:00.000Z")]
        cause, covered_share, _bash_shape = _mod.cache_rebuild_rules._attribute_idle_gap_cause(
            prior_turn, window, gap_start_ts=_mod._parse_ts("2026-08-01T10:00:00.000Z"), gap_seconds=360.0
        )
        assert cause == _mod.cache_rebuild_rules._ATTR_UNATTRIBUTED
        assert covered_share is None

    def test_non_bash_tool_use_result_in_window_is_unattributed(self):
        """A tool_result for an Edit call (not Bash) must not attribute to
        'waiting on own Bash call' even though its id matches a tool_use
        prior_turn itself emitted -- only a Bash-named tool_use seeds the
        matching id set."""
        prior_turn = _asst("claude-sonnet-5", content=[_edit_use("tool-1")])
        window = [_tool_result_record("tool-1", ts="2026-08-01T10:05:00.000Z")]
        cause, covered_share, _bash_shape = _mod.cache_rebuild_rules._attribute_idle_gap_cause(
            prior_turn, window, gap_start_ts=_mod._parse_ts("2026-08-01T10:00:00.000Z"), gap_seconds=360.0
        )
        assert cause == _mod.cache_rebuild_rules._ATTR_UNATTRIBUTED
        assert covered_share is None

    def test_marker_prefix_text_present_but_isMeta_absent_is_unattributed(self):
        prior_turn = _asst("claude-sonnet-5", content=[])
        window = [_user_msg(
            f"{_mod.cache_rebuild_rules._BACKGROUND_TASK_MARKER_PREFIX} a backgrounded task finished",
            ts="2026-08-01T10:05:00.000Z",
        )]
        cause, covered_share, _bash_shape = _mod.cache_rebuild_rules._attribute_idle_gap_cause(
            prior_turn, window, gap_start_ts=_mod._parse_ts("2026-08-01T10:00:00.000Z"), gap_seconds=360.0
        )
        assert cause == _mod.cache_rebuild_rules._ATTR_UNATTRIBUTED
        assert covered_share is None

    def test_isMeta_true_isSidechain_false_is_unattributed(self):
        """A main-thread system notification (isMeta True, isSidechain
        False) must not attribute -- both flags are required."""
        prior_turn = _asst("claude-sonnet-5", content=[])
        window = [_meta_marker_record(
            f"{_mod.cache_rebuild_rules._BACKGROUND_TASK_MARKER_PREFIX} x", ts="2026-08-01T10:05:00.000Z", is_sidechain=False,
        )]
        cause, covered_share, _bash_shape = _mod.cache_rebuild_rules._attribute_idle_gap_cause(
            prior_turn, window, gap_start_ts=_mod._parse_ts("2026-08-01T10:00:00.000Z"), gap_seconds=360.0
        )
        assert cause == _mod.cache_rebuild_rules._ATTR_UNATTRIBUTED
        assert covered_share is None

    def test_isSidechain_true_isMeta_false_is_unattributed(self):
        """The reverse combination (isSidechain True, isMeta False) must
        also not attribute -- both flags are required, held individually
        false in this test and the previous one."""
        prior_turn = _asst("claude-sonnet-5", content=[])
        window = [_meta_marker_record(
            f"{_mod.cache_rebuild_rules._COORDINATOR_MESSAGE_MARKER_PREFIX}: x", ts="2026-08-01T10:05:00.000Z", is_meta=False,
        )]
        cause, covered_share, _bash_shape = _mod.cache_rebuild_rules._attribute_idle_gap_cause(
            prior_turn, window, gap_start_ts=_mod._parse_ts("2026-08-01T10:00:00.000Z"), gap_seconds=360.0
        )
        assert cause == _mod.cache_rebuild_rules._ATTR_UNATTRIBUTED
        assert covered_share is None

    def test_two_bash_pairs_poll_loop_last_marker_wins_the_later_tool_result(self):
        """Two Bash tool_use/tool_result pairs in one window (the poll-loop
        shape .claude/plans/subagent-idle-gap-cause-attribution.md's
        Context section hand-sampled) -- last-marker-wins must pick the
        LATER tool_result's timestamp, not the first."""
        prior_turn = _asst(
            "claude-sonnet-5", content=[_bash_use("tool-1", "sleep 30"), _bash_use("tool-2", "sleep 30")]
        )
        window = [
            _tool_result_record("tool-1", ts="2026-08-01T10:00:30.000Z"),
            _tool_result_record("tool-2", ts="2026-08-01T10:05:50.000Z"),
        ]
        cause, covered_share, _bash_shape = _mod.cache_rebuild_rules._attribute_idle_gap_cause(
            prior_turn, window, gap_start_ts=_mod._parse_ts("2026-08-01T10:00:00.000Z"), gap_seconds=360.0
        )
        assert cause == _mod.cache_rebuild_rules._ATTR_OWN_BASH
        assert covered_share == pytest.approx(350 / 360)

    def test_two_bash_pairs_differing_shape_the_later_sleep_poll_wins_the_shape(self):
        """Two Bash tool_use/tool_result pairs where only the later result's
        own command is a sleep-poll -- the shape must follow the winning
        (later) marker, not the other pair's non-matching command."""
        prior_turn = _asst(
            "claude-sonnet-5", content=[_bash_use("tool-1", "pytest"), _bash_use("tool-2", "sleep 30")]
        )
        window = [
            _tool_result_record("tool-1", ts="2026-08-01T10:00:30.000Z"),
            _tool_result_record("tool-2", ts="2026-08-01T10:05:50.000Z"),
        ]
        _cause, _covered_share, bash_shape = _mod.cache_rebuild_rules._attribute_idle_gap_cause(
            prior_turn, window, gap_start_ts=_mod._parse_ts("2026-08-01T10:00:00.000Z"), gap_seconds=360.0
        )
        assert bash_shape == _mod.cache_rebuild_rules._BASH_WAIT_SLEEP_POLL

    def test_two_bash_pairs_differing_shape_the_later_other_command_wins_the_shape(self):
        """Same two pairs as the previous test, reversed window order -- the
        shape must flip with them, proving it follows window-order
        precedence, not a fixed per-command priority."""
        prior_turn = _asst(
            "claude-sonnet-5", content=[_bash_use("tool-1", "pytest"), _bash_use("tool-2", "sleep 30")]
        )
        window = [
            _tool_result_record("tool-2", ts="2026-08-01T10:00:30.000Z"),
            _tool_result_record("tool-1", ts="2026-08-01T10:05:50.000Z"),
        ]
        _cause, _covered_share, bash_shape = _mod.cache_rebuild_rules._attribute_idle_gap_cause(
            prior_turn, window, gap_start_ts=_mod._parse_ts("2026-08-01T10:00:00.000Z"), gap_seconds=360.0
        )
        assert bash_shape == _mod.cache_rebuild_rules._BASH_WAIT_OTHER

    def test_coordinator_marker_win_carries_no_bash_shape(self):
        """A coordinator-marker win (last-marker-wins over an earlier Bash
        result) must carry no shape at all -- shape is only ever set when
        the winning cause is _ATTR_OWN_BASH."""
        prior_turn = _asst("claude-sonnet-5", content=[_bash_use("tool-1", "sleep 30")])
        window = [
            _tool_result_record("tool-1", ts="2026-08-01T10:01:00.000Z"),
            _meta_marker_record(
                f"{_mod.cache_rebuild_rules._COORDINATOR_MESSAGE_MARKER_PREFIX}: hello", ts="2026-08-01T10:05:00.000Z",
            ),
        ]
        _cause, _covered_share, bash_shape = _mod.cache_rebuild_rules._attribute_idle_gap_cause(
            prior_turn, window, gap_start_ts=_mod._parse_ts("2026-08-01T10:00:00.000Z"), gap_seconds=360.0
        )
        assert bash_shape is None

    def test_bash_tool_use_with_no_input_yields_no_command_recorded_shape(self):
        """A Bash tool_use block with no `input` key at all must still win
        the cause via its matching tool_use_id. The shape degrades to 'no
        command recorded' rather than raising on the missing key. This is
        distinct from TestClassifyBashWaitShape's present-but-non-string
        case."""
        prior_turn = _asst("claude-sonnet-5", content=[{"type": "tool_use", "id": "tool-1", "name": "Bash"}])
        window = [_tool_result_record("tool-1", ts="2026-08-01T10:05:50.000Z")]
        _cause, _covered_share, bash_shape = _mod.cache_rebuild_rules._attribute_idle_gap_cause(
            prior_turn, window, gap_start_ts=_mod._parse_ts("2026-08-01T10:00:00.000Z"), gap_seconds=360.0
        )
        assert bash_shape == _mod.cache_rebuild_rules._BASH_WAIT_NO_COMMAND

    def test_clock_skew_marker_outside_gap_window_yields_finite_unclamped_covered_share(self):
        """A marker timestamp before gap_start_ts (clock skew) must still
        render a finite covered_share, negative and unclamped, rather than
        crashing or silently clamping to 0."""
        prior_turn = _asst("claude-sonnet-5", content=[_bash_use("tool-1", "pytest")])
        window = [_tool_result_record("tool-1", ts="2026-08-01T09:59:00.000Z")]
        cause, covered_share, _bash_shape = _mod.cache_rebuild_rules._attribute_idle_gap_cause(
            prior_turn, window, gap_start_ts=_mod._parse_ts("2026-08-01T10:00:00.000Z"), gap_seconds=360.0
        )
        assert cause == _mod.cache_rebuild_rules._ATTR_OWN_BASH
        assert covered_share == pytest.approx(-60 / 360)

    def test_marker_with_no_timestamp_field_still_attributes_with_none_covered_share(self):
        """A structurally-matching marker with no `timestamp` field at all
        must still win the cause -- only its covered_share degrades to
        None, never a silent fall-through to unattributed or to an earlier
        marker."""
        prior_turn = _asst("claude-sonnet-5", content=[_bash_use("tool-1", "pytest")])
        window = [{"type": "user", "message": {"content": [_tool_result("tool-1", "ok")]}}]
        cause, covered_share, _bash_shape = _mod.cache_rebuild_rules._attribute_idle_gap_cause(
            prior_turn, window, gap_start_ts=_mod._parse_ts("2026-08-01T10:00:00.000Z"), gap_seconds=360.0
        )
        assert cause == _mod.cache_rebuild_rules._ATTR_OWN_BASH
        assert covered_share is None

    def test_later_marker_with_bad_timestamp_still_wins_over_earlier_timestamped_marker(self):
        """Last-marker-wins must not silently revert to an earlier,
        timestamped marker just because the true last marker's own
        timestamp is missing -- that would misattribute to a different
        real cause instead of disclosing the gap via covered_share."""
        prior_turn = _asst("claude-sonnet-5", content=[_bash_use("tool-1", "pytest")])
        window = [
            _tool_result_record("tool-1", ts="2026-08-01T10:01:00.000Z"),
            {
                "type": "user",
                "message": {"content": f"{_mod.cache_rebuild_rules._COORDINATOR_MESSAGE_MARKER_PREFIX}: hello"},
                "isMeta": True,
                "isSidechain": True,
            },
        ]
        cause, covered_share, _bash_shape = _mod.cache_rebuild_rules._attribute_idle_gap_cause(
            prior_turn, window, gap_start_ts=_mod._parse_ts("2026-08-01T10:00:00.000Z"), gap_seconds=360.0
        )
        assert cause == _mod.cache_rebuild_rules._ATTR_COORDINATOR
        assert covered_share is None


class TestClassifyBashWaitShape:
    """Direct unit tests for _classify_bash_wait_shape -- a pure string
    classifier, so its whole test surface is literal command strings. Covers
    the sleep-poll match shape, including the quoted/heredoc and `$VAR`
    edge cases."""

    def test_do_sleep_shape_classifies_as_sleep_poll(self):
        """The `until ... kill -0 $PID ...; do sleep N; done` shape -- one
        of the two Bash sleep-poll idioms this classifier exists to catch,
        a `kill -0 $PID`-style wait loop."""
        command = "until ! kill -0 $PID 2>/dev/null; do sleep 5; done"
        assert _mod.cache_rebuild_rules._classify_bash_wait_shape(command) == _mod.cache_rebuild_rules._BASH_WAIT_SLEEP_POLL

    def test_leading_sleep_shape_classifies_as_sleep_poll(self):
        """The `sleep N; <check>` shape -- the other of the two Bash
        sleep-poll idioms this classifier exists to catch, a leading sleep
        before a check command."""
        command = "sleep 30; kill -0 $PID || break"
        assert _mod.cache_rebuild_rules._classify_bash_wait_shape(command) == _mod.cache_rebuild_rules._BASH_WAIT_SLEEP_POLL

    def test_ampersand_separated_sleep_classifies_as_sleep_poll(self):
        command = "make build && sleep 10 && make test"
        assert _mod.cache_rebuild_rules._classify_bash_wait_shape(command) == _mod.cache_rebuild_rules._BASH_WAIT_SLEEP_POLL

    def test_sleep_with_variable_argument_classifies_as_other(self):
        """No literal leading digit -- the documented under-count bias."""
        command = "sleep $INTERVAL"
        assert _mod.cache_rebuild_rules._classify_bash_wait_shape(command) == _mod.cache_rebuild_rules._BASH_WAIT_OTHER

    def test_sleep_flag_name_not_in_command_position_classifies_as_other(self):
        """`sleep` appearing inside a longer flag name, not preceded by a
        separator or reserved word -- no per-tool poll idiom matching."""
        command = "gh run watch --sleep-interval 5"
        assert _mod.cache_rebuild_rules._classify_bash_wait_shape(command) == _mod.cache_rebuild_rules._BASH_WAIT_OTHER

    def test_quoted_sleep_inside_echo_classifies_as_sleep_poll(self):
        """Textual match with no shell parsing: a `sleep 5` inside a quoted
        echo argument counts, documenting the accepted false positive so a
        later widening to shell-aware parsing is a deliberate change."""
        command = 'echo "done; sleep 5"'
        assert _mod.cache_rebuild_rules._classify_bash_wait_shape(command) == _mod.cache_rebuild_rules._BASH_WAIT_SLEEP_POLL

    def test_newline_separated_sleep_classifies_as_sleep_poll(self):
        """A `sleep N` on its own line inside a multi-line Bash script --
        the newline-separator branch of the sleep-poll regex."""
        command = "set -e\nsleep 5\necho done"
        assert _mod.cache_rebuild_rules._classify_bash_wait_shape(command) == _mod.cache_rebuild_rules._BASH_WAIT_SLEEP_POLL

    def test_then_sleep_classifies_as_sleep_poll(self):
        """A `sleep N` guarded by an `if`/`then` conditional -- the
        `then` reserved-word branch of the sleep-poll regex."""
        command = "if x; then sleep 5; fi"
        assert _mod.cache_rebuild_rules._classify_bash_wait_shape(command) == _mod.cache_rebuild_rules._BASH_WAIT_SLEEP_POLL

    def test_else_sleep_classifies_as_sleep_poll(self):
        """A `sleep N` guarded by an `if`/`else` conditional -- the
        `else` reserved-word branch of the sleep-poll regex."""
        command = "if x; then true; else sleep 5; fi"
        assert _mod.cache_rebuild_rules._classify_bash_wait_shape(command) == _mod.cache_rebuild_rules._BASH_WAIT_SLEEP_POLL

    def test_heredoc_body_sleep_classifies_as_sleep_poll(self):
        """Heredoc bodies match via the same newline-separator branch as a
        multi-line script."""
        command = "cat <<'EOF'\nsleep 5\nEOF"
        assert _mod.cache_rebuild_rules._classify_bash_wait_shape(command) == _mod.cache_rebuild_rules._BASH_WAIT_SLEEP_POLL

    def test_pipe_separated_sleep_classifies_as_sleep_poll(self):
        command = "some-command | sleep 5"
        assert _mod.cache_rebuild_rules._classify_bash_wait_shape(command) == _mod.cache_rebuild_rules._BASH_WAIT_SLEEP_POLL

    def test_none_command_classifies_as_no_command_recorded(self):
        assert _mod.cache_rebuild_rules._classify_bash_wait_shape(None) == _mod.cache_rebuild_rules._BASH_WAIT_NO_COMMAND

    def test_non_string_command_classifies_as_no_command_recorded(self):
        assert _mod.cache_rebuild_rules._classify_bash_wait_shape(12345) == _mod.cache_rebuild_rules._BASH_WAIT_NO_COMMAND


class TestCacheRebuildIdleGapAttribution:
    """.claude/plans/subagent-idle-gap-cause-attribution.md's Verification
    section's report-level cases for the subagent idle-gap cause-attribution
    table, modeled on TestCacheRebuildOriginSplit's own fixture-per-case
    style. Precedence/self-scoping/clock-skew are covered directly against
    _attribute_idle_gap_cause in TestAttributeIdleGapCause above; these
    tests exercise the report's own wiring of that function into the scan
    loop and the printed table. Also covers the report-level own-Bash
    wait-shape rows: the sleep-poll/other/no-command split sums exactly to
    the 'waiting on own Bash call' row it sub-splits."""

    def test_bash_tool_result_at_gap_end_populates_own_bash_row_leaving_others_zero_seeded(
        self, fake_projects, capsys
    ):
        """Also covers the populated/empty-bucket case: the three
        unmatched causes must render their zero/'n/a' sentinel in the same
        run without statistics.median raising on an empty list."""
        session_id = "sess-bash-attr"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [])
        subagent_records = [
            _priced(
                "claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z",
                request_id="sub-1", content=[_bash_use("tool-1", "pytest -k foo")],
            ),
            _user_msg([_tool_result("tool-1", "ok")], ts="2026-08-01T10:05:50.000Z"),
            _priced(
                "claude-sonnet-5", ephemeral_5m=200_000, ts="2026-08-01T10:06:00.000Z", request_id="sub-2",
            ),
        ]
        subagent_records[0]["isSidechain"] = True
        subagent_records[2]["isSidechain"] = True
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", subagent_records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        assert _extract_cache_rebuild_row(out, "subagent") == (1, "0.46")
        assert _extract_cache_rebuild_attribution_row(out, _mod.cache_rebuild_rules._ATTR_OWN_BASH) == (
            1, "0.46", "0.46", "97.2%",
        )
        assert _extract_cache_rebuild_attribution_row(out, _mod.cache_rebuild_rules._ATTR_BACKGROUND_TASK) == (
            0, "0.00", "0.00", "n/a",
        )
        assert _extract_cache_rebuild_attribution_row(out, _mod.cache_rebuild_rules._ATTR_COORDINATOR) == (
            0, "0.00", "0.00", "n/a",
        )
        assert _extract_cache_rebuild_attribution_row(out, _mod.cache_rebuild_rules._ATTR_UNATTRIBUTED) == (
            0, "0.00", "0.00", "n/a",
        )

    def test_background_task_marker_populates_background_task_row(self, fake_projects, capsys):
        session_id = "sess-bg-attr"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [])
        subagent_records = [
            _priced("claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z", request_id="sub-1"),
            _user_msg(
                f"{_mod.cache_rebuild_rules._BACKGROUND_TASK_MARKER_PREFIX} a backgrounded task finished",
                ts="2026-08-01T10:05:55.000Z",
            ),
            _priced(
                "claude-sonnet-5", ephemeral_5m=200_000, ts="2026-08-01T10:06:00.000Z", request_id="sub-2",
            ),
        ]
        subagent_records[0]["isSidechain"] = True
        subagent_records[1]["isMeta"] = True
        subagent_records[1]["isSidechain"] = True
        subagent_records[2]["isSidechain"] = True
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", subagent_records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        assert _extract_cache_rebuild_attribution_row(out, _mod.cache_rebuild_rules._ATTR_BACKGROUND_TASK) == (
            1, "0.46", "0.46", "98.6%",
        )

    def test_coordinator_message_marker_populates_coordinator_row(self, fake_projects, capsys):
        session_id = "sess-coord-attr"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [])
        subagent_records = [
            _priced("claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z", request_id="sub-1"),
            _user_msg(
                f"{_mod.cache_rebuild_rules._COORDINATOR_MESSAGE_MARKER_PREFIX}: here's an update", ts="2026-08-01T10:05:55.000Z",
            ),
            _priced(
                "claude-sonnet-5", ephemeral_5m=200_000, ts="2026-08-01T10:06:00.000Z", request_id="sub-2",
            ),
        ]
        subagent_records[0]["isSidechain"] = True
        subagent_records[1]["isMeta"] = True
        subagent_records[1]["isSidechain"] = True
        subagent_records[2]["isSidechain"] = True
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", subagent_records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        assert _extract_cache_rebuild_attribution_row(out, _mod.cache_rebuild_rules._ATTR_COORDINATOR) == (
            1, "0.46", "0.46", "98.6%",
        )

    def test_main_origin_bash_tool_result_in_window_does_not_leak_into_subagent_attribution(
        self, fake_projects, capsys
    ):
        """Mirrors TestCacheRebuildOriginSplit's own
        test_inline_sidechain_record_in_main_file_counts_as_subagent_origin_and_own_gap_chain
        main/inline-sidechain/main shape, adding a second origin's own Bash
        tool_use/tool_result interleaved into the same group_records list.
        The main-origin tool_result lands LATER in the subagent's own
        window than the subagent's own tool_result -- if self-scoping were
        "any Bash tool_result in the window" rather than true tool_use_id
        membership, last-marker-wins would pick up main's late-arriving
        result instead and report a high covered share, not the correct
        low one."""
        main_first = _priced(
            "claude-sonnet-5", ephemeral_5m=100_000, ts="2026-08-01T10:00:00.000Z",
            request_id="main-1", content=[_bash_use("main-tool-1", "ls")],
        )
        sidechain_first = _priced(
            "claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:01.000Z",
            request_id="sub-inline-1", content=[_bash_use("sub-tool-1", "pytest")],
        )
        sidechain_first["isSidechain"] = True
        sub_tool_result = _user_msg([_tool_result("sub-tool-1", "ok")], ts="2026-08-01T10:00:10.000Z")
        main_tool_result = _user_msg([_tool_result("main-tool-1", "ok")], ts="2026-08-01T10:05:55.000Z")
        sidechain_second = _priced(
            "claude-sonnet-5", ephemeral_5m=200_000, ts="2026-08-01T10:06:00.000Z", request_id="sub-inline-2",
        )
        sidechain_second["isSidechain"] = True
        _write_jsonl(fake_projects / "sess.jsonl", [
            main_first, sidechain_first, sub_tool_result, main_tool_result, sidechain_second,
        ])

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        assert _extract_cache_rebuild_row(out, "idle 5m-1h")[0] == 1
        assert _extract_cache_rebuild_row(out, "subagent") == (1, "0.46")
        rebuilds, excess, band_excess, median_cov = _extract_cache_rebuild_attribution_row(
            out, _mod.cache_rebuild_rules._ATTR_OWN_BASH
        )
        assert (rebuilds, excess, band_excess) == (1, "0.46", "0.46")
        assert median_cov == "2.5%"

    def test_main_origin_rebuild_enters_no_attribution_row_and_totals_reconcile_with_subagent_row(
        self, fake_projects, capsys
    ):
        """A main-origin idle-gap rebuild in the same corpus as two
        differently-attributed subagent rebuilds: the main rebuild must
        enter no attribution row, and the attribution table's own
        Rebuilds/Excess $ must sum exactly to the subagent origin row --
        neither more (main leaking in) nor less (a subagent rebuild
        dropped)."""
        main_records = [
            _priced("claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z", request_id="main-1"),
            _priced(
                "claude-sonnet-5", ephemeral_5m=100_000, ts="2026-08-01T10:06:00.000Z", request_id="main-2",
            ),
        ]
        _write_jsonl(fake_projects / "sess-main.jsonl", main_records)

        subagent_records = [
            _priced(
                "claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z",
                request_id="sub-1", content=[_bash_use("tool-1", "pytest")],
            ),
            _user_msg([_tool_result("tool-1", "ok")], ts="2026-08-01T10:05:50.000Z"),
            _priced(
                "claude-sonnet-5", ephemeral_5m=200_000, ts="2026-08-01T10:06:00.000Z", request_id="sub-2",
            ),
            _user_msg(
                f"{_mod.cache_rebuild_rules._BACKGROUND_TASK_MARKER_PREFIX} x", ts="2026-08-01T10:11:55.000Z",
            ),
            _priced(
                "claude-sonnet-5", ephemeral_5m=200_000, ts="2026-08-01T10:12:00.000Z", request_id="sub-3",
            ),
        ]
        for rec in (subagent_records[0], subagent_records[2], subagent_records[4]):
            rec["isSidechain"] = True
        subagent_records[3]["isMeta"] = True
        subagent_records[3]["isSidechain"] = True
        _write_jsonl(fake_projects / "sess-sub.jsonl", [])
        _write_subagent_jsonl(fake_projects, "sess-sub", "agent-1", subagent_records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        main_rebuilds, main_excess = _extract_cache_rebuild_row(out, "main")
        subagent_rebuilds, subagent_excess = _extract_cache_rebuild_row(out, "subagent")
        assert (main_rebuilds, main_excess) == (1, "0.23")
        assert (subagent_rebuilds, subagent_excess) == (2, "0.92")

        rows = [
            _extract_cache_rebuild_attribution_row(out, attr)
            for attr in _mod.cache_rebuild_rules._CACHE_REBUILD_ATTRIBUTIONS
        ]
        total_attributed_rebuilds = sum(row[0] for row in rows)
        total_attributed_excess = sum(float(row[1]) for row in rows)
        assert total_attributed_rebuilds == subagent_rebuilds
        assert total_attributed_excess == pytest.approx(float(subagent_excess))
        # main's own 0.23 must not have leaked into any attribution row.
        assert total_attributed_excess == pytest.approx(0.92)

    def test_idle_over_1h_subagent_rebuild_counts_in_excess_but_not_5m_1h_band_excess(
        self, fake_projects, capsys
    ):
        session_id = "sess-over-1h"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [])
        subagent_records = [
            _priced(
                "claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z",
                request_id="sub-1", content=[_bash_use("tool-1", "pytest")],
            ),
            _user_msg([_tool_result("tool-1", "ok")], ts="2026-08-01T10:30:00.000Z"),
            # Exactly 3600s after sub-1 -- idle >1h, not idle 5m-1h.
            _priced(
                "claude-sonnet-5", ephemeral_5m=200_000, ts="2026-08-01T11:00:00.000Z", request_id="sub-2",
            ),
        ]
        subagent_records[0]["isSidechain"] = True
        subagent_records[2]["isSidechain"] = True
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", subagent_records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        assert _extract_cache_rebuild_row(out, "idle >1h")[0] == 1
        assert _extract_cache_rebuild_attribution_row(out, _mod.cache_rebuild_rules._ATTR_OWN_BASH) == (
            1, "0.46", "0.00", "50.0%",
        )

    def test_no_marker_in_window_populates_unattributed_row_with_nonzero_rebuilds(self, fake_projects, capsys):
        """A genuine idle-gap candidate whose prior turn emits no Bash
        tool_use and whose window carries no meta marker at all -- distinct
        from the all-zero corpus case below, which never reaches a
        populated attribution branch."""
        session_id = "sess-unattr"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [])
        subagent_records = [
            _priced("claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z", request_id="sub-1"),
            _priced(
                "claude-sonnet-5", ephemeral_5m=200_000, ts="2026-08-01T10:05:50.000Z", request_id="sub-2",
            ),
        ]
        subagent_records[0]["isSidechain"] = True
        subagent_records[1]["isSidechain"] = True
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", subagent_records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        assert _extract_cache_rebuild_row(out, "subagent") == (1, "0.46")
        assert _extract_cache_rebuild_attribution_row(out, _mod.cache_rebuild_rules._ATTR_UNATTRIBUTED) == (
            1, "0.46", "0.46", "n/a",
        )

    def test_multiple_candidates_in_same_bucket_uses_true_median_not_mean(self, fake_projects, capsys):
        """Three Bash-attributed candidates, each in its own subagent group,
        with covered shares 10%/90%/95% -- the median (90%) diverges from
        the mean (65%), so this pins statistics.median against a
        mean/first/last-value regression that every other test in this
        class (at most one candidate per bucket) can't catch."""
        marker_offsets = [
            ("sess-median-a", "2026-08-01T10:00:36.000Z"),  # 36/360 = 10.0%
            ("sess-median-b", "2026-08-01T10:05:24.000Z"),  # 324/360 = 90.0%
            ("sess-median-c", "2026-08-01T10:05:42.000Z"),  # 342/360 = 95.0%
        ]
        for session_id, marker_ts in marker_offsets:
            _write_jsonl(fake_projects / f"{session_id}.jsonl", [])
            subagent_records = [
                _priced(
                    "claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z",
                    request_id="sub-1", content=[_bash_use("tool-1", "pytest")],
                ),
                _user_msg([_tool_result("tool-1", "ok")], ts=marker_ts),
                _priced(
                    "claude-sonnet-5", ephemeral_5m=200_000, ts="2026-08-01T10:06:00.000Z", request_id="sub-2",
                ),
            ]
            subagent_records[0]["isSidechain"] = True
            subagent_records[2]["isSidechain"] = True
            _write_subagent_jsonl(fake_projects, session_id, "agent-1", subagent_records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        assert _extract_cache_rebuild_attribution_row(out, _mod.cache_rebuild_rules._ATTR_OWN_BASH) == (
            3, "1.38", "1.38", "90.0%",
        )

    def test_timestampless_winning_marker_still_attributes_cause_with_na_median_cov(self, fake_projects, capsys):
        """Report-level regression test for the timestamp-fallback bug this
        round fixed: a winning Bash tool_result with no timestamp field at
        all must still land in waiting-on-own-Bash-call, not unattributed,
        rendering Median cov. as n/a rather than crashing or misattributing."""
        session_id = "sess-no-ts-marker"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [])
        subagent_records = [
            _priced(
                "claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z",
                request_id="sub-1", content=[_bash_use("tool-1", "pytest")],
            ),
            _user_msg([_tool_result("tool-1", "ok")]),  # no ts -- the fixed fallback shape
            _priced(
                "claude-sonnet-5", ephemeral_5m=200_000, ts="2026-08-01T10:06:00.000Z", request_id="sub-2",
            ),
        ]
        subagent_records[0]["isSidechain"] = True
        subagent_records[2]["isSidechain"] = True
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", subagent_records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        assert _extract_cache_rebuild_attribution_row(out, _mod.cache_rebuild_rules._ATTR_OWN_BASH) == (
            1, "0.46", "0.46", "n/a",
        )

    def test_no_subagent_idle_gap_rebuilds_renders_all_four_attribution_rows_zero_seeded(
        self, fake_projects, capsys
    ):
        main_records = [
            _priced("claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z", request_id="main-1"),
            _priced(
                "claude-sonnet-5", ephemeral_5m=100_000, ts="2026-08-01T10:06:00.000Z", request_id="main-2",
            ),
        ]
        _write_jsonl(fake_projects / "sess-mainonly.jsonl", main_records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        assert _extract_cache_rebuild_row(out, "subagent") == (0, "0.00")
        for attribution in _mod.cache_rebuild_rules._CACHE_REBUILD_ATTRIBUTIONS:
            assert _extract_cache_rebuild_attribution_row(out, attribution) == (0, "0.00", "0.00", "n/a")

    def test_sleep_poll_bash_command_populates_sleep_poll_wait_row(self, fake_projects, capsys):
        """Mirrors
        test_bash_tool_result_at_gap_end_populates_own_bash_row_leaving_others_zero_seeded
        with a sleep-poll command instead of a plain one -- pins that the
        sleep-poll wait row, not just the parent own-Bash row, gets
        populated, leaving the other two shape rows zero-seeded."""
        session_id = "sess-sleep-poll"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [])
        subagent_records = [
            _priced(
                "claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z",
                request_id="sub-1", content=[_bash_use("tool-1", "sleep 30")],
            ),
            _user_msg([_tool_result("tool-1", "ok")], ts="2026-08-01T10:05:50.000Z"),
            _priced(
                "claude-sonnet-5", ephemeral_5m=200_000, ts="2026-08-01T10:06:00.000Z", request_id="sub-2",
            ),
        ]
        subagent_records[0]["isSidechain"] = True
        subagent_records[2]["isSidechain"] = True
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", subagent_records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        assert _extract_cache_rebuild_attribution_row(out, _mod.cache_rebuild_rules._BASH_WAIT_SLEEP_POLL) == (
            1, "0.46", "0.46", "97.2%",
        )
        assert _extract_cache_rebuild_attribution_row(out, _mod.cache_rebuild_rules._BASH_WAIT_OTHER) == (
            0, "0.00", "0.00", "n/a",
        )
        assert _extract_cache_rebuild_attribution_row(out, _mod.cache_rebuild_rules._BASH_WAIT_NO_COMMAND) == (
            0, "0.00", "0.00", "n/a",
        )

    def test_own_bash_wait_shape_rows_sum_exactly_to_the_own_bash_attribution_row(
        self, fake_projects, capsys
    ):
        """Three own-Bash-attributed subagent rebuilds, one per shape
        (sleep-poll, other, no command recorded), plus one background-task-
        attributed rebuild that must not leak into the shape sum -- the
        three shape rows' Rebuilds/Excess $/5m-1h $ must sum exactly to the
        'waiting on own Bash call' row above them, modeled on
        test_main_origin_rebuild_enters_no_attribution_row_and_totals_reconcile_with_subagent_row."""
        shape_fixtures = [
            ("sess-shape-sleep", [_bash_use("tool-1", "sleep 30")]),
            ("sess-shape-other", [_bash_use("tool-1", "pytest")]),
            ("sess-shape-nocommand", [{"type": "tool_use", "id": "tool-1", "name": "Bash"}]),
        ]
        for session_id, first_content in shape_fixtures:
            _write_jsonl(fake_projects / f"{session_id}.jsonl", [])
            subagent_records = [
                _priced(
                    "claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z",
                    request_id="sub-1", content=first_content,
                ),
                _user_msg([_tool_result("tool-1", "ok")], ts="2026-08-01T10:05:50.000Z"),
                _priced(
                    "claude-sonnet-5", ephemeral_5m=200_000, ts="2026-08-01T10:06:00.000Z", request_id="sub-2",
                ),
            ]
            subagent_records[0]["isSidechain"] = True
            subagent_records[2]["isSidechain"] = True
            _write_subagent_jsonl(fake_projects, session_id, "agent-1", subagent_records)

        session_id = "sess-shape-bgtask"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [])
        bgtask_records = [
            _priced("claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z", request_id="sub-1"),
            _user_msg(f"{_mod.cache_rebuild_rules._BACKGROUND_TASK_MARKER_PREFIX} x", ts="2026-08-01T10:05:55.000Z"),
            _priced(
                "claude-sonnet-5", ephemeral_5m=200_000, ts="2026-08-01T10:06:00.000Z", request_id="sub-2",
            ),
        ]
        bgtask_records[0]["isSidechain"] = True
        bgtask_records[1]["isMeta"] = True
        bgtask_records[1]["isSidechain"] = True
        bgtask_records[2]["isSidechain"] = True
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", bgtask_records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        own_bash_rebuilds, own_bash_excess, own_bash_band_excess, _median = (
            _extract_cache_rebuild_attribution_row(out, _mod.cache_rebuild_rules._ATTR_OWN_BASH)
        )
        assert (own_bash_rebuilds, own_bash_excess, own_bash_band_excess) == (3, "1.38", "1.38")
        assert _extract_cache_rebuild_attribution_row(out, _mod.cache_rebuild_rules._ATTR_BACKGROUND_TASK)[0] == 1

        shape_rows = [
            _extract_cache_rebuild_attribution_row(out, shape)
            for shape in _mod.cache_rebuild_rules._OWN_BASH_WAIT_SHAPES
        ]
        assert sum(row[0] for row in shape_rows) == own_bash_rebuilds
        assert sum(float(row[1]) for row in shape_rows) == pytest.approx(float(own_bash_excess))
        assert sum(float(row[2]) for row in shape_rows) == pytest.approx(float(own_bash_band_excess))

    def test_no_subagent_idle_gap_rebuilds_renders_all_three_shape_rows_zero_seeded(
        self, fake_projects, capsys
    ):
        """Same all-main-origin corpus shape as the four-row zero-seeding
        test above, pinning that statistics.median doesn't raise on the new
        block's own empty lists either."""
        main_records = [
            _priced("claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z", request_id="main-1"),
            _priced(
                "claude-sonnet-5", ephemeral_5m=100_000, ts="2026-08-01T10:06:00.000Z", request_id="main-2",
            ),
        ]
        _write_jsonl(fake_projects / "sess-mainonly-shape.jsonl", main_records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        for shape in _mod.cache_rebuild_rules._OWN_BASH_WAIT_SHAPES:
            assert _extract_cache_rebuild_attribution_row(out, shape) == (0, "0.00", "0.00", "n/a")

    def test_bash_shape_row_5m_1h_excess_excludes_idle_over_1h_candidate_but_excess_includes_it(
        self, fake_projects, capsys
    ):
        """Mirrors test_idle_over_1h_subagent_rebuild_counts_in_excess_but_not_5m_1h_band_excess
        at the shape-row level: bash_shape_band_excess's own idle-5m-1h-band
        guard sits unexercised whenever every own-Bash fixture lands in the
        same band, as every other shape-row test above does. Pairing an
        in-band sleep-poll candidate against an idle->1h sleep-poll
        candidate makes 5m-1h $ and Excess $ diverge on the shape row."""
        session_band = "sess-shape-band"
        _write_jsonl(fake_projects / f"{session_band}.jsonl", [])
        band_records = [
            _priced(
                "claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z",
                request_id="sub-1", content=[_bash_use("tool-1", "sleep 30")],
            ),
            _user_msg([_tool_result("tool-1", "ok")], ts="2026-08-01T10:05:50.000Z"),
            _priced(
                "claude-sonnet-5", ephemeral_5m=200_000, ts="2026-08-01T10:06:00.000Z", request_id="sub-2",
            ),
        ]
        band_records[0]["isSidechain"] = True
        band_records[2]["isSidechain"] = True
        _write_subagent_jsonl(fake_projects, session_band, "agent-1", band_records)

        session_over1h = "sess-shape-over1h"
        _write_jsonl(fake_projects / f"{session_over1h}.jsonl", [])
        over1h_records = [
            _priced(
                "claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z",
                request_id="sub-1", content=[_bash_use("tool-1", "sleep 30")],
            ),
            _user_msg([_tool_result("tool-1", "ok")], ts="2026-08-01T10:30:00.000Z"),
            # Exactly 3600s after sub-1 -- idle >1h, not idle 5m-1h.
            _priced(
                "claude-sonnet-5", ephemeral_5m=200_000, ts="2026-08-01T11:00:00.000Z", request_id="sub-2",
            ),
        ]
        over1h_records[0]["isSidechain"] = True
        over1h_records[2]["isSidechain"] = True
        _write_subagent_jsonl(fake_projects, session_over1h, "agent-1", over1h_records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        sleep_poll_rebuilds, sleep_poll_excess, sleep_poll_band_excess, _median = (
            _extract_cache_rebuild_attribution_row(out, _mod.cache_rebuild_rules._BASH_WAIT_SLEEP_POLL)
        )
        assert sleep_poll_rebuilds == 2
        assert sleep_poll_excess == "0.92"
        assert sleep_poll_band_excess == "0.46"


