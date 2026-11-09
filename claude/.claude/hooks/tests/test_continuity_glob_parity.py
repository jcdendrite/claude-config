"""Agreement of the continuity-file set across the announce hook, the consume hook and the pending helper.

announce-resume-command.sh and consume-durable-continuity-file-on-read.sh each spell the set as a
`case` pattern. _lib_collect_pending_continuity_files defines it as a filesystem glob, and
_lib_is_pending_continuity_file tests membership in that set. The two hooks' patterns must agree with
each other. The helper's pending set must be a subset of both, because the helper is intentionally
narrower (it skips nested and dot-led names). The tests run the real hooks and
the real helper over one name matrix, so a change to one definition alone fails here. The last test
covers the leading-`@` slash-form precaution on its own rows.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import (
    code_span_command,
    read_input,
    resume_context_stub_invocations,
    write_continuity_named_file,
    write_input,
)

from .test_announce_resume_command import _REFUSED_NAME_REASON, ANNOUNCE_HOOK, _run_hook_raw
from .test_consume_durable_continuity_file_on_read import CONSUME_HOOK
from .test_consume_durable_continuity_file_on_read import _run_hook_raw as _run_consume_hook_raw
from .test_lib_resume_context import _is_pending_continuity_file

_SYMLINK_LEAF = "handoffs/linked-handoff.md"

# Relative path under the config dir for every kind of name the three definitions could disagree on.
_NAME_MATRIX = [
    "handoffs/example-handoff.md",
    "briefs/example-task.md",
    "handoffs/-handoff.md",
    "briefs/-task.md",
    "handoffs/@mention-handoff.md",
    "handoffs/a@b-handoff.md",
    "briefs/a@b-task.md",
    "handoffs/v1.2-step-3.4-handoff.md",
    "handoffs/nested/example-handoff.md",
    "briefs/nested/example-task.md",
    "handoffs/.hidden-handoff.md",
    "briefs/.hidden-task.md",
    "handoffs/example-task.md",
    "briefs/example-handoff.md",
    "handoffs/example-handoff.txt",
    "handoffs/example-handoff",
    "handoffs/examplehandoff.md",
    "briefs/exampletask.md",
    "handoffs/example-handoff.md.bak",
    "briefs/example-task.md.bak",
    _SYMLINK_LEAF,
]

# The helper's verdict on every _NAME_MATRIX row, so a row cannot silently change sides.
_EXPECTED_PENDING = {
    "handoffs/example-handoff.md",
    "briefs/example-task.md",
    "handoffs/-handoff.md",
    "briefs/-task.md",
    "handoffs/@mention-handoff.md",
    "handoffs/a@b-handoff.md",
    "briefs/a@b-task.md",
    "handoffs/v1.2-step-3.4-handoff.md",
}
_EXPECTED_NOT_PENDING = {
    "handoffs/nested/example-handoff.md",
    "briefs/nested/example-task.md",
    "handoffs/.hidden-handoff.md",
    "briefs/.hidden-task.md",
    "handoffs/example-task.md",
    "briefs/example-handoff.md",
    "handoffs/example-handoff.txt",
    "handoffs/example-handoff",
    "handoffs/examplehandoff.md",
    "briefs/exampletask.md",
    "handoffs/example-handoff.md.bak",
    "briefs/example-task.md.bak",
    _SYMLINK_LEAF,
}


def _create(config_dir: Path, relative_path: str) -> Path:
    return write_continuity_named_file(config_dir, relative_path, symlink_leaf=relative_path == _SYMLINK_LEAF)


def _install_recording_consume_script(config_dir: Path) -> Path:
    """A `resume-context.sh` that records its argv and prints a destination, so a run that reaches
    the consume step is observable without depending on what the real script accepts."""
    log = config_dir / "consume-invocations.log"
    script = config_dir / "scripts" / "resume-context.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> "{log}"\nprintf \'%s\\n\' "/dev/null/consumed"\n')
    script.chmod(0o755)
    return log


def _announce_fires(home: Path, path: Path) -> bool:
    result = _run_hook_raw(ANNOUNCE_HOOK, write_input(str(path)), home=home)
    assert result.returncode == 0, result.stderr
    assert resume_context_stub_invocations(home) == ""
    return bool(result.stdout.strip())


def _consume_fires(home: Path, consume_log: Path, path: Path) -> bool:
    before = consume_log.read_text() if consume_log.exists() else ""
    result = _run_consume_hook_raw(CONSUME_HOOK, read_input(str(path)), home=home)
    assert result.returncode == 0, result.stderr
    after = consume_log.read_text() if consume_log.exists() else ""
    return after != before


@pytest.mark.parametrize("relative_path", _NAME_MATRIX)
def test_announce_fires_exactly_when_the_consume_hook_fires(isolated_home: Path, relative_path: str) -> None:
    config_dir = isolated_home / ".claude"
    consume_log = _install_recording_consume_script(config_dir)
    path = _create(config_dir, relative_path)

    assert _announce_fires(isolated_home, path) == _consume_fires(isolated_home, consume_log, path), (
        f"the announce and consume case patterns disagree on {relative_path}"
    )


@pytest.mark.parametrize("relative_path", sorted(_EXPECTED_PENDING))
def test_every_name_the_pending_helper_accepts_is_accepted_by_both_hooks(
    isolated_home: Path, relative_path: str
) -> None:
    config_dir = isolated_home / ".claude"
    consume_log = _install_recording_consume_script(config_dir)
    path = _create(config_dir, relative_path)

    assert _announce_fires(isolated_home, path), f"the announce case pattern refuses pending {relative_path}"
    assert _consume_fires(isolated_home, consume_log, path), f"the consume case pattern refuses pending {relative_path}"


def test_the_matrix_holds_both_pending_and_non_pending_names(isolated_home: Path) -> None:
    """Guards the parity tests against a matrix that stops exercising one side of the helper."""
    config_dir = isolated_home / ".claude"
    paths = {
        relative_path: _create(config_dir, relative_path)
        for relative_path in _NAME_MATRIX
    }
    verdicts = {relative_path: _is_pending_continuity_file(config_dir, str(path)) for relative_path, path in paths.items()}
    assert {name for name, pending in verdicts.items() if pending} == _EXPECTED_PENDING
    assert {name for name, pending in verdicts.items() if not pending} == _EXPECTED_NOT_PENDING


@pytest.mark.parametrize(
    ("relative_path", "slash_form_refused"),
    [
        ("handoffs/a@b-handoff.md", False),
        ("briefs/a@b-task.md", False),
        ("handoffs/@mention-handoff.md", True),
        ("briefs/@mention-task.md", True),
    ],
    ids=["mid-name-at-sign", "mid-name-at-sign-brief", "leading-at-sign", "leading-at-sign-brief"],
)
def test_the_announce_hook_withholds_the_slash_form_from_a_leading_at_sign_only(
    isolated_home: Path, git_repo: Path, relative_path: str, slash_form_refused: bool
) -> None:
    path = write_continuity_named_file(isolated_home / ".claude", relative_path)

    result = _run_hook_raw(ANNOUNCE_HOOK, write_input(str(path), cwd=str(git_repo)), home=isolated_home)

    assert result.returncode == 0, result.stderr
    message = json.loads(result.stdout)["systemMessage"]
    assert (_REFUSED_NAME_REASON in message) == slash_form_refused
    expected_command = f"resume-context {path}" if slash_form_refused else f"/resume-context {path.name}"
    assert code_span_command(message) == expected_command
