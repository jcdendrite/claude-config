"""Fixture builders and spies used only by the tests of the routing-guard subcommand.

`routing_guard` is imported inside the functions that need it, so importing this module for `_note_line` or
`_digest_name` alone does not import the subcommand.
"""
from __future__ import annotations

import builtins
import contextlib
import errno
import hashlib
import io
import os
import subprocess
import sys
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import pytest
from transcript_analysis import denials

from .conftest import _hook_deny_current

if TYPE_CHECKING:
    from transcript_analysis import routing_guard

_ROUTING_DENY_LABEL_TEXT = "review-orchestrator routing"


def _routing_deny_record(
    tool_use_id: str, *, shape: str = "current", branch: str = "feat", ts: str | None = None,
    body: str = "routing sentinel body",
) -> dict:
    """A review-orchestrator routing gate denial in either transcript shape: "current" (an is_error tool_result)
    or "legacy" (a hook_blocking_error attachment).

    The message starts with the gate's own "Blocked by ... gate:" wording. The assertion fails a fixture whose
    wording denials._denial_hook_label no longer labels as the routing gate, so a reworded gate cannot leave
    every deny test passing for the wrong reason.
    """
    message = f"Blocked by {_ROUTING_DENY_LABEL_TEXT} gate: {body}"
    assert denials._denial_hook_label("", message) == _ROUTING_DENY_LABEL_TEXT
    if shape == "current":
        return _hook_deny_current(message, tool_id=tool_use_id, ts=ts, branch=branch)
    assert shape == "legacy"
    record: dict = {
        "type": "attachment",
        "gitBranch": branch,
        "attachment": {
            "type": "hook_blocking_error",
            "hookName": _ROUTING_DENY_LABEL_TEXT,
            "toolUseID": tool_use_id,
            "blockingError": {"blockingError": message, "command": "x"},
        },
    }
    if ts:
        record["timestamp"] = ts
    return record


class _PathSpies:
    """Every path the spied `open` and directory-listing entry points received while installed."""

    def __init__(self) -> None:
        self.opened: list[str] = []
        self.listed: list[str] = []

    @staticmethod
    def _without_python_modules(paths: list[str]) -> list[str]:
        return [p for p in paths if not p.endswith((".py", ".pyc", ".pth")) and "__pycache__" not in p]

    def opened_paths(self) -> list[str]:
        return self._without_python_modules(self.opened)

    def listed_paths(self) -> list[str]:
        return self._without_python_modules(self.listed)

    def all_paths(self) -> list[str]:
        return self.opened_paths() + self.listed_paths()


def _install_path_spies(monkeypatch) -> _PathSpies:
    """Record the path argument of builtins.open, io.open, os.open, os.scandir and os.listdir.

    A file descriptor argument is not a path and is skipped. The spies delegate to the real functions.
    """
    spies = _PathSpies()

    def recorded(target: list[str], real: Callable) -> Callable:
        def spy(path=".", *args, **kwargs):
            if isinstance(path, str | bytes | os.PathLike):
                target.append(os.fsdecode(path))
            return real(path, *args, **kwargs)
        return spy

    monkeypatch.setattr(builtins, "open", recorded(spies.opened, builtins.open))
    monkeypatch.setattr(io, "open", recorded(spies.opened, io.open))
    monkeypatch.setattr(os, "open", recorded(spies.opened, os.open))
    monkeypatch.setattr(os, "scandir", recorded(spies.listed, os.scandir))
    monkeypatch.setattr(os, "listdir", recorded(spies.listed, os.listdir))
    return spies


class _SubprocessRecorder:
    def __init__(self) -> None:
        self.argvs: list[list[str]] = []

    def executables(self) -> set[str]:
        return {argv[0] for argv in self.argvs}


def _install_subprocess_recorder(monkeypatch, delegate: Callable | None = None) -> _SubprocessRecorder:
    """Record the argv of every subprocess.run call and every subprocess.Popen construction.

    `delegate` replaces subprocess.run, else the real function runs. subprocess.run builds a Popen, so a real run
    records its argv twice. A shell string is recorded as one element.
    """
    recorder = _SubprocessRecorder()
    real_run = delegate or subprocess.run
    real_popen_init = subprocess.Popen.__init__

    def argv_of(args) -> list[str]:
        if isinstance(args, str | bytes | os.PathLike):
            return [os.fsdecode(args)]
        return [os.fsdecode(part) for part in args]

    def recording_run(*args, **kwargs):
        recorder.argvs.append(argv_of(args[0] if args else kwargs["args"]))
        return real_run(*args, **kwargs)

    def recording_popen_init(self, args, *init_args, **init_kwargs):
        recorder.argvs.append(argv_of(args))
        return real_popen_init(self, args, *init_args, **init_kwargs)

    monkeypatch.setattr(subprocess, "run", recording_run)
    monkeypatch.setattr(subprocess.Popen, "__init__", recording_popen_init)
    return recorder


class _RecorderPositiveControls:
    """Tests that the subprocess recorder sees a `gh` run and a Popen, so a test asserting it saw no other
    executable cannot pass on a recorder that records nothing.

    Not collected here. One test module subclasses it as a `Test...` class.
    """

    def test_the_subprocess_recorder_records_a_gh_argv(self, monkeypatch):
        recorder = _install_subprocess_recorder(monkeypatch, delegate=lambda *a, **k: subprocess.CompletedProcess(a, 0))

        subprocess.run(["gh", "pr", "list"], check=False)

        assert recorder.executables() == {"gh"}

    def test_the_subprocess_recorder_records_a_popen_argv(self, monkeypatch):
        recorder = _install_subprocess_recorder(monkeypatch)

        subprocess.Popen([sys.executable, "-c", "pass"]).wait()

        assert recorder.executables() == {sys.executable}


@contextlib.contextmanager
def _environment_unchanged() -> Iterator[None]:
    """Fail when the body adds, removes or changes an environment variable."""
    before = dict(os.environ)
    yield
    assert dict(os.environ) == before


class _Outcome(NamedTuple):
    code: int
    out: str
    err: str


def _digest_name(branch: str, revision: int = 1) -> str:
    """The expected capture file name, computed with hashlib directly so a wrong digest function cannot pass."""
    return f"{hashlib.sha256(branch.encode('utf-8')).hexdigest()}.r{revision}.json"


def _pilot_cells(arm: str, pilots: tuple[str, str] | None) -> tuple[str, str]:
    """The two pilot cells of a note row: `-` for an unmerged row, else `pilots` (default both off)."""
    if arm == "unmerged":
        return ("-", "-")
    return pilots or ("off", "off")


def _note_line(config_dir: Path | str, branch: str, arm: str = "on", pilots: tuple[str, str] | None = None) -> str:
    """One tab-separated note row as the note file holds it."""
    return "\t".join([str(config_dir), branch, arm, *_pilot_cells(arm, pilots)])


def _note_row(
    line_no: int, account: Path, branch: str, arm: str = "on", pilots: tuple[str, str] = ("off", "off"),
) -> routing_guard._NoteRow:
    from transcript_analysis import routing_guard

    round2_pilot, architect_pilot = _pilot_cells(arm, pilots)
    return routing_guard._NoteRow(line_no, account, branch, arm, round2_pilot, architect_pilot)


def _is_nameable_text(text: str) -> bool:
    """False for text that would be "contained" in nearly any output: empty, whitespace-only, or a dot segment."""
    return bool(text.strip()) and text not in (".", "..")


def _texts_a_config_dir_cell_can_print(cell: str) -> list[str]:
    """The cell, plus the final segment of its `~/`-expanded path and of its resolved path, and both paths.

    Production's own expansion and resolution produce the paths. A cell production refuses to expand (a relative
    one, or `~/` with no absolute HOME) contributes only its whole text, because its last segment is arbitrary
    text. A cell that expands but does not resolve contributes the expanded path only.

    No returned text is empty, whitespace-only, or a dot segment (`.` or `..`).
    """
    from transcript_analysis import routing_guard

    try:
        expanded = routing_guard._expand_note_config_dir(cell, 0)
    except routing_guard._NoteRefusal:
        return [cell] if _is_nameable_text(cell) else []
    paths = [expanded]
    with contextlib.suppress(routing_guard._NoteRefusal):
        paths.append(routing_guard._resolve_note_config_dir(expanded, 0))
    texts = [cell]
    for path in paths:
        texts.append(str(path))
        texts.append(path.name)
    return list(dict.fromkeys(text for text in texts if _is_nameable_text(text)))


def _strings_the_output_must_not_name(note_path: str, config_dir_argument: str | None) -> list[str]:
    """The branch and config dir cell of every note line, the account directory names, and the --config-dir argument.

    The note is read the way production reads it: bytes decoded as UTF-8 and split on `\\n` only, so a bare
    carriage return does not start a line. A cell is read leniently, since a refused note still must not be echoed.
    The --config-dir argument is treated as a config dir cell. See `_texts_a_config_dir_cell_can_print` for what a
    config dir cell contributes. A branch cell contributes its text unless it is empty, whitespace-only, or a dot
    segment.
    """
    strings: list[str] = _texts_a_config_dir_cell_can_print(config_dir_argument) if config_dir_argument else []
    try:
        note_text = Path(note_path).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return strings
    for raw_line in note_text.split("\n"):
        line = raw_line.removesuffix("\r")
        if not line or line.startswith("#"):
            continue
        cells = line.split("\t")
        strings.extend(_texts_a_config_dir_cell_can_print(cells[0]))
        if len(cells) > 1 and _is_nameable_text(cells[1]):
            strings.append(cells[1])
    return strings


def _symlink_loop_that_resolve_rejects(directory: Path) -> Path:
    """Two symlinks in `directory` pointing at each other, and the path of one.

    Skips the test when `Path.resolve()` tolerates the loop (Python 3.13 and later), so a test using this
    never passes without reaching the arm that handles a resolve failure.
    """
    loop_a = directory / "loop-a"
    loop_b = directory / "loop-b"
    loop_a.symlink_to(loop_b)
    loop_b.symlink_to(loop_a)
    try:
        loop_a.resolve()
    except (OSError, RuntimeError):
        return loop_a
    pytest.skip("Path.resolve() does not raise on a symlink loop on this Python")


def _path_that_resolve_rejects_as_nul(directory: Path) -> str:
    """An absolute path under `directory` with an embedded NUL, skipping the test when `Path.resolve()` accepts it."""
    path_with_nul = f"{directory}/nul\x00path"
    try:
        Path(path_with_nul).resolve()
    except ValueError:
        return path_with_nul
    pytest.skip("Path.resolve() does not raise on an embedded NUL on this Python")


def _invoke_routing_guard(capsys, args) -> _Outcome:
    """Run cmd_routing_guard to its exit and return the exit code and both streams.

    On every exit code, no branch name or config dir read from the note, no account directory name and no
    `--config-dir` argument may appear in either stream.
    """
    from transcript_analysis import routing_guard

    with pytest.raises(SystemExit) as exit_info:
        routing_guard.cmd_routing_guard(args)
    captured = capsys.readouterr()
    outcome = _Outcome(exit_info.value.code, captured.out, captured.err)
    combined = outcome.out + outcome.err
    named = [
        text for text in _strings_the_output_must_not_name(args.note, getattr(args, "config_dir", None)) if text in combined
    ]
    assert named == [], f"routing-guard output names {len(named)} string(s) read from the note or the arguments"
    return outcome


def _install_git_not_found(monkeypatch) -> None:
    """Make every `subprocess.run` raise the error a missing `git` executable raises."""

    def git_not_found(*args, **kwargs):
        raise FileNotFoundError(errno.ENOENT, "No such file or directory", "git")

    monkeypatch.setattr(subprocess, "run", git_not_found)


def _assert_git_failure_diagnostic_precedes_note_refusal(
    outcome: _Outcome, exempt_prefixes: Sequence[str], tmp_path: Path,
) -> None:
    """The first stderr line is the git diagnostic under the routing-guard label, and the second is the note refusal.

    `exempt_prefixes` is the calling module's digit-scan exemption list, which the diagnostic line must match.
    """
    diagnostic_line, refusal_line = outcome.err.splitlines()[:2]
    assert outcome.code == 2
    assert diagnostic_line.startswith("routing-guard: git-tracked check failed")
    assert diagnostic_line.startswith(tuple(exempt_prefixes))
    assert "git working tree" in refusal_line
    assert refusal_line.startswith("routing-guard: --note")
    assert str(tmp_path) not in outcome.out + outcome.err
