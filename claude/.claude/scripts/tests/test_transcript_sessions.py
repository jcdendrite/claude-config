"""Tests for transcript_analysis/sessions.py's cmd_sessions, reached through the real parser."""
import pytest
from transcript_analysis import cli

from .conftest import _asst, _write_jsonl


def test_sessions_without_paths_exits_2_and_emits_no_path(fake_projects, capsys):
    """The seeded transcript would reach stdout if the refusal ran after the emission loop."""
    _write_jsonl(fake_projects / "sess.jsonl", [_asst("claude-sonnet-5")])
    args = cli.build_parser().parse_args(["sessions"])

    with pytest.raises(SystemExit) as exit_info:
        args.func(args)

    captured = capsys.readouterr()
    assert exit_info.value.code == 2
    assert "--paths is required" in captured.err
    assert captured.out == ""
