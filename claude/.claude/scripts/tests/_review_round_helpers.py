"""Fixture builders shared by the tests of transcript_analysis subcommands that read review rounds."""
import builtins
import contextlib
import hashlib
import io
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from transcript_analysis import denials, routing_guard

from .conftest import (
    _agent_use,
    _bash_use,
    _hook_deny_current,
    _priced,
    _tool_result,
    _user_msg,
    _write_subagent_dispatch,
)

_ORCHESTRATOR_AGENT_TYPE = "review-orchestrator"
_SONNET = "claude-sonnet-5"

# Plain-ASCII prefix on every free-text value a review-orchestrator dispatch
# carries that no round-consuming subcommand extracts. A prefix that appears in
# any output means a subcommand surfaced a field it must not.
_LEAK_SENTINEL_PREFIX = "LEAKSENTINEL-"


def _leak_sentinel(field_name: str) -> str:
    return f"{_LEAK_SENTINEL_PREFIX}{field_name}"


def _dispatch_block_with_free_text(tool_use_id: str, subagent_type: str, *, tool_name: str = "Agent") -> dict:
    """An Agent/Task tool_use block whose every input key except
    subagent_type carries a sentinel."""
    block = _agent_use(tool_use_id, subagent_type, tool_name=tool_name)
    block["input"].update({
        "description": _leak_sentinel(f"{tool_use_id}-description"),
        "prompt": _leak_sentinel(f"{tool_use_id}-prompt"),
        "name": _leak_sentinel(f"{tool_use_id}-name"),
    })
    return block


def _orchestrator_spawn_record(
    tool_use_id: str, *, ts: str, branch: str = "feat", tool_name: str = "Agent", extra_blocks: tuple = (),
) -> dict:
    """A main-thread priced assistant record ($0.20) that dispatches a
    review-orchestrator."""
    return _priced(
        "claude-sonnet-5", input=100_000, branch=branch, ts=ts,
        content=[
            *extra_blocks,
            {"type": "text", "text": _leak_sentinel(f"{tool_use_id}-assistant-text")},
            _dispatch_block_with_free_text(tool_use_id, _ORCHESTRATOR_AGENT_TYPE, tool_name=tool_name),
        ],
    )


def _orchestrator_return_record(tool_use_id: str, *, ts: str, branch: str = "feat") -> dict:
    return _user_msg(
        [_tool_result(tool_use_id, _leak_sentinel(f"{tool_use_id}-result"))], branch=branch, ts=ts,
    )


def _as_sidechain(record: dict) -> dict:
    record["isSidechain"] = True
    return record


def _write_orchestrator_subagent_tree(
    proj: Path, session_id: str, *, orchestrator_tool_use_id: str = "o1", branch: str = "feat",
) -> None:
    """The orchestrator's own transcript ($0.20: its Skill call plus a nested
    reviewer dispatch) and that reviewer's depth-2 transcript ($1.00), both in
    the session's flat subagents/ directory with isSidechain on every record."""
    orchestrator_records = [
        _as_sidechain(_priced(
            "claude-sonnet-5", input=100_000, branch=branch, ts="2026-08-01T10:00:10.000Z",
            content=[
                {"type": "text", "text": _leak_sentinel("orchestrator-text")},
                {
                    "type": "tool_use", "id": "inner-skill", "name": "Skill",
                    "input": {"skill": "code-review", "args": _leak_sentinel("skill-args")},
                },
                _dispatch_block_with_free_text("n1", "staff-sdet"),
            ],
        )),
    ]
    reviewer_records = [
        _as_sidechain(_priced(
            "claude-sonnet-5", input=500_000, branch=branch, ts="2026-08-01T10:00:20.000Z",
            content=[{"type": "text", "text": _leak_sentinel("reviewer-text")}],
        )),
    ]
    _write_subagent_dispatch(
        proj, session_id, "agent-o", orchestrator_tool_use_id, orchestrator_records,
        agent_type=_ORCHESTRATOR_AGENT_TYPE, description=_leak_sentinel("orchestrator-meta-description"),
    )
    _write_subagent_dispatch(
        proj, session_id, "agent-n", "n1", reviewer_records,
        agent_type="staff-sdet", description=_leak_sentinel("reviewer-meta-description"), spawn_depth=2,
    )


_MARKER_WRITE_COMMAND = "marker.sh write code-review"


def _marker_write_use(tool_id: str) -> dict:
    return _bash_use(tool_id, _MARKER_WRITE_COMMAND)


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
    round2_pilot, architect_pilot = _pilot_cells(arm, pilots)
    return routing_guard._NoteRow(line_no, account, branch, arm, round2_pilot, architect_pilot)


def _strings_the_output_must_not_name(note_path: str, config_dir_argument: str | None) -> list[str]:
    """The branch and config dir of every note line, the account directory names, and the --config-dir argument.

    A cell is read leniently, since a refused note still must not be echoed. A relative config dir cell
    contributes only its whole text, because its last segment is arbitrary text.
    """
    strings: list[str] = [config_dir_argument] if config_dir_argument else []
    try:
        note_text = Path(note_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return strings
    for raw_line in note_text.split("\n"):
        line = raw_line.removesuffix("\r")
        if not line or line.startswith("#"):
            continue
        cells = line.split("\t")
        strings.append(cells[0])
        if os.path.isabs(cells[0]) and Path(cells[0]).name:
            strings.append(Path(cells[0]).name)
        if len(cells) > 1 and cells[1]:
            strings.append(cells[1])
    return strings


def _invoke_routing_guard(capsys, args) -> _Outcome:
    """Run cmd_routing_guard to its exit and return the exit code and both streams.

    On every exit code, no branch name or config dir read from the note, no account directory name and no
    `--config-dir` argument may appear in either stream.
    """
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
