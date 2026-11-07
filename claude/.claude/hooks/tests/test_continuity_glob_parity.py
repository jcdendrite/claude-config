"""Parity of the three definitions of the continuity-file set.

announce-resume-command.sh and consume-durable-continuity-file-on-read.sh each spell the set as a
`case` pattern, and _lib_is_pending_continuity_file defines it as a filesystem glob. Each test runs
the real hooks and the real helper over one name matrix, so a change to one definition alone fails here.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from helpers import (
    _build_subprocess_env,
    read_input,
    resume_context_stub_invocations,
    write_input,
)

from .test_announce_resume_command import _REFUSED_NAME_REASON, ANNOUNCE_HOOK, _run_hook_raw
from .test_consume_durable_continuity_file_on_read import CONSUME_HOOK
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
    _SYMLINK_LEAF,
}


def _create(config_dir: Path, relative_path: str) -> Path:
    path = config_dir / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if relative_path == _SYMLINK_LEAF:
        real = config_dir.parent / "elsewhere" / "real-handoff.md"
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_text("body\n")
        path.symlink_to(real)
    else:
        path.write_text("body\n")
    return path


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
    result = subprocess.run(
        [str(CONSUME_HOOK)],
        input=json.dumps(read_input(str(path))),
        capture_output=True,
        text=True,
        env=_build_subprocess_env(home, None),
        check=False,
    )
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


@pytest.mark.parametrize("relative_path", _NAME_MATRIX)
def test_every_name_the_pending_helper_accepts_is_accepted_by_both_hooks(
    isolated_home: Path, relative_path: str
) -> None:
    config_dir = isolated_home / ".claude"
    consume_log = _install_recording_consume_script(config_dir)
    path = _create(config_dir, relative_path)

    if _is_pending_continuity_file(config_dir, str(path)):
        assert _announce_fires(isolated_home, path), f"the announce case pattern refuses pending {relative_path}"
        assert _consume_fires(isolated_home, consume_log, path), (
            f"the consume case pattern refuses pending {relative_path}"
        )


def test_the_matrix_holds_both_pending_and_non_pending_names(isolated_home: Path) -> None:
    """Guards the parity tests against a matrix that stops exercising one side of the helper."""
    config_dir = isolated_home / ".claude"
    verdicts = {
        relative_path: _is_pending_continuity_file(config_dir, str(_create(config_dir, relative_path)))
        for relative_path in _NAME_MATRIX
    }
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
    path = _create(isolated_home / ".claude", relative_path)

    result = _run_hook_raw(ANNOUNCE_HOOK, write_input(str(path), cwd=str(git_repo)), home=isolated_home)

    assert result.returncode == 0, result.stderr
    assert (_REFUSED_NAME_REASON in json.loads(result.stdout)["systemMessage"]) == slash_form_refused
