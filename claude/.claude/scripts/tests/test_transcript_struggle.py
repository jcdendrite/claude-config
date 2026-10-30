"""Tests for transcript_analysis/struggle.py's cmd_struggle: STRUGGLE_PHRASES matching and task-notification
exclusion.
"""
import pytest
from transcript_analysis import struggle

from .conftest import _asst, _table_cols, _user_msg, _write_jsonl


class TestCmdStruggle:
    def test_hallucinat_phrase_registers_as_struggle(self, fake_projects, capsys):
        """"I think you hallucinated about this" should register; "stale cache" should not."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # Assistant turn so branch has a model family to attribute to.
            _asst("claude-sonnet-4-6", branch="feat"),
            # Human turn containing the new phrase — should register as struggle.
            _user_msg(
                [{"type": "text", "text": "I think you hallucinated about this"}],
                branch="feat",
            ),
            # Control: "stale" is excluded from STRUGGLE_PHRASES (legitimate technical term).
            _user_msg(
                [{"type": "text", "text": "the stale cache needs clearing"}],
                branch="feat",
            ),
        ])
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": "feat"})()
        struggle.cmd_struggle(args)
        out = capsys.readouterr().out

        # The output table should include "feat" with at least one struggle signal.
        assert "feat" in out, "branch not found in output"
        # Parse the data row for "feat": columns are Branch Opus Sonnet Haiku Other Unknown
        cols = _table_cols(out, header_contains="Opus", row_contains="feat")
        total_signals = sum(int(cols[k]) for k in ["Opus", "Sonnet", "Haiku", "Other", "Unknown"])
        assert total_signals == 1, (
            f"expected exactly 1 struggle signal (from 'hallucinated', not from 'stale cache'); got cols={cols}"
        )

    def test_stale_control_phrase_does_not_inflate_count(self, fake_projects, capsys):
        """Bare "stale" must not match — excluded from STRUGGLE_PHRASES due to false-positive risk."""
        # Confirm "stale" alone is absent from STRUGGLE_PHRASES.
        assert not any(p == "stale" for p in struggle.STRUGGLE_PHRASES), (
            "bare 'stale' was added to STRUGGLE_PHRASES; it is intentionally excluded — "
            "remove it to avoid false positives on technical uses like 'stale cache'"
        )
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat"),
            _user_msg(
                [{"type": "text", "text": "the stale cache needs clearing"}],
                branch="feat",
            ),
        ])
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": "feat"})()
        struggle.cmd_struggle(args)
        out = capsys.readouterr().out
        # "stale cache" alone should produce no struggle signals — branch absent from output.
        assert "feat" not in out, (
            f"'stale cache' should not register as a struggle signal; branch appeared in output: {out!r}"
        )

    def test_task_notification_envelope_produces_no_struggle_signal(self, fake_projects, capsys):
        """A forwarded <task-notification> record whose <summary> contains a struggle
        phrase is the subagent's own prose, not human input — it must not register."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat"),
            _user_msg(
                "<task-notification><status>completed</status>"
                "<summary>Background task still failing, incorrect output</summary>"
                "</task-notification>",
                branch="feat",
            ),
        ])
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": "feat"})()
        struggle.cmd_struggle(args)
        out = capsys.readouterr().out
        assert "feat" not in out, (
            f"a task-notification's forwarded <summary> should not register as a struggle signal; got {out!r}"
        )

    def test_task_notification_mixed_turn_struggle_phrase_outside_envelope_still_counts(self, fake_projects, capsys):
        """A struggle phrase sitting outside the envelope in the same turn still counts,
        even though the envelope in that same turn is excluded from matching."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat"),
            _user_msg(
                "that's incorrect. also: "
                "<task-notification><summary>still failing</summary></task-notification>",
                branch="feat",
            ),
        ])
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": "feat"})()
        struggle.cmd_struggle(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Opus", row_contains="feat")
        total_signals = sum(int(cols[k]) for k in ["Opus", "Sonnet", "Haiku", "Other", "Unknown"])
        assert total_signals == 1, (
            f"expected exactly 1 struggle signal (from 'incorrect' outside the envelope); got cols={cols}"
        )

    @pytest.mark.parametrize(
        "phrase,text",
        [
            ("are you saying", "Are you saying this is correct?"),
            ("you should be able to", "You should be able to run it directly."),
            ("that doesn't exist", "that doesn't exist in the repo"),
            ("that doesn't match", "that doesn't match what I see"),
            ("not what i wanted", "that's not what i wanted at all"),
            ("not what i asked", "that's not what i asked you to do"),
            ("hallucinat", "I think you hallucinated that function"),
        ],
    )
    def test_new_phrases_register_as_struggle(self, phrase, text, fake_projects, capsys):
        """Each newly-added STRUGGLE_PHRASES entry must register when present in a user turn."""
        assert any(phrase in p for p in struggle.STRUGGLE_PHRASES), (
            f"phrase {phrase!r} not found in STRUGGLE_PHRASES — was it removed or mistyped?"
        )
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat"),
            _user_msg([{"type": "text", "text": text}], branch="feat"),
        ])
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": "feat"})()
        struggle.cmd_struggle(args)
        out = capsys.readouterr().out
        assert "feat" in out, (
            f"phrase {phrase!r} (text={text!r}) should register as a struggle signal; branch absent from output: {out!r}"
        )
