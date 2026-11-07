"""Fixture builders and spies used only by the tests of the routing-guard subcommand.

`routing_guard` is imported inside the functions that need it, so importing this module for `_note_line` or
`_digest_name` alone does not import the subcommand.
"""
from __future__ import annotations

import builtins
import contextlib
import errno
import functools
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import _config
import pytest
from transcript_analysis import denials

from ._review_round_helpers import _SONNET
from .conftest import _hook_deny_current

if TYPE_CHECKING:
    from transcript_analysis import routing_guard

_ROUTING_DENY_LABEL_TEXT = "review-orchestrator routing"
_ROUTING_GUARD_HEADING = "## routing-guard"


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
    denial_label = denials._denial_hook_label("", message)
    assert denial_label == _ROUTING_DENY_LABEL_TEXT, denial_label
    if shape == "current":
        return _hook_deny_current(message, tool_id=tool_use_id, ts=ts, branch=branch)
    assert shape == "legacy", shape
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

        assert recorder.executables() == {"gh"}, recorder.executables()

    def test_the_subprocess_recorder_records_a_popen_argv(self, monkeypatch):
        recorder = _install_subprocess_recorder(monkeypatch)

        subprocess.Popen([sys.executable, "-c", "pass"]).wait()

        assert recorder.executables() == {sys.executable}, recorder.executables()


@contextlib.contextmanager
def _environment_unchanged() -> Iterator[None]:
    """Fail when the body adds, removes or changes an environment variable."""
    before = dict(os.environ)
    yield
    after = dict(os.environ)
    assert after == before, {
        "added": sorted(after.keys() - before.keys()),
        "removed": sorted(before.keys() - after.keys()),
        "changed": sorted(name for name in before.keys() & after.keys() if before[name] != after[name]),
    }


class _Outcome(NamedTuple):
    code: int
    out: str
    err: str


def _digest_name(branch: str, revision: int = 1) -> str:
    """The expected capture file name, computed with hashlib directly so a wrong digest function cannot pass."""
    return f"{hashlib.sha256(branch.encode('utf-8')).hexdigest()}.r{revision}.json"


def _note_line(config_dir: Path | str, branch: str, status: str = "merged") -> str:
    """One tab-separated note row as the note file holds it."""
    return "\t".join([str(config_dir), branch, status])


def _note_row(line_no: int, account: Path, branch: str, status: str = "merged") -> routing_guard._NoteRow:
    from transcript_analysis import routing_guard

    return routing_guard._NoteRow(line_no, account, branch, status)


# --- the opt-in read ---------------------------------------------------------

_NOISY_CONFIG_MALFORMED_TOKEN = "malformed-line-sentinel"
_NOISY_CONFIG_MALFORMED_LINE = f"{_NOISY_CONFIG_MALFORMED_TOKEN} without an equals sign"
_NOISY_CONFIG_UNKNOWN_KEY = "unknown_key_sentinel"


def _opt_in_faults() -> list[tuple[str, object, routing_guard._OptInOutcome, str]]:
    """(case id, what the config reader returns or raises, the classified outcome, a fragment of the refusal) for each
    outcome of the `pr_cost_recording` read that is not opted in."""
    from transcript_analysis import routing_guard

    return [
        ("not-opted-in", False, routing_guard._OptInOutcome.NOT_OPTED_IN, "not opted in"),
        ("config-dir-unresolved", None, routing_guard._OptInOutcome.CONFIG_DIR_UNRESOLVED, "could not resolve"),
        (
            "schema-empty", _config.ConfigSchemaEmptyError("pr_cost_recording"),
            routing_guard._OptInOutcome.SCHEMA_EMPTY, "empty or malformed",
        ),
        (
            "row-truncated", _config.ConfigSchemaRowTruncatedError("pr_cost_recording"),
            routing_guard._OptInOutcome.ROW_TRUNCATED, "truncated",
        ),
        ("unknown-key", KeyError("pr_cost_recording"), routing_guard._OptInOutcome.UNKNOWN_KEY, "unknown config key"),
    ]


def _config_enabled_stub(reader_result: object) -> Callable[..., object]:
    """A stand-in for `_config.config_enabled` that raises `reader_result` when it is an exception, else returns it."""

    def config_enabled(_key, config_dir_override=None):
        if isinstance(reader_result, Exception):
            raise reader_result
        return reader_result

    return config_enabled


def _write_noisy_config_state_file(config_dir: Path) -> None:
    """A config state file with one malformed line and one unknown key, which the config reader reports on stderr."""
    (config_dir / "claude-config.toml").write_text(f"{_NOISY_CONFIG_MALFORMED_LINE}\n{_NOISY_CONFIG_UNKNOWN_KEY} = true\n")


# --- provenance statement -------------------------------------------------------

# Literal text, so a reworded production constant fails the tests that pin it.
_BANNER_PROVENANCE_SENTENCE = (
    "Note that the key state behind PASSED and FAILED comes from log rows any process running as the account can write,"
    " and compute reads capture files the account can write too, as it can the transcripts they were built from,"
    " so those statuses rest on unauthenticated inputs."
)
_COMPUTE_PROVENANCE_LINE = (
    "routing-guard: the key state behind PASSED and FAILED comes from log rows any process running as the account can write,"
    " and compute reads capture files the account can write too, as it can the transcripts they were built from,"
    " so those statuses rest on unauthenticated inputs"
)


# --- capture files -------------------------------------------------------------

_VALID_STATES = {"routing": "off", "round_consult_round2_pilot": "off", "architect_fix_plan_pilot": "off"}


def _capture_loop(**overrides) -> dict:
    """A valid stored loop. A test overrides the one field it means."""
    return {
        "overhead_tokens": {_SONNET: {"standard": {"input": 5}}}, "growth_tokens": 1, "later_turns": {_SONNET: 1},
        "degraded": False, **overrides,
    }


def _capture_file_text(loops: object, **state_overrides) -> str:
    """A capture file's text holding `loops` and the three key states, `state_overrides` replacing a state.

    `loops` is not checked, so a test can pass an invalid shape.
    """
    return json.dumps({"loops": loops, **_VALID_STATES, **state_overrides})


# --- the session config-keys log ---------------------------------------------

_LOG_FILE_NAME = ".session-config-keys-log.jsonl"
# Before every fixture transcript timestamp, which sit on 2026-08-01 from 10:00.
_DEFAULT_LOG_AT = "2026-08-01T09:00:00Z"


def _log_row(
    session_id: str, logged_at: str = _DEFAULT_LOG_AT, *, routing: str = "false", round2: str = "false",
    architect: str = "false", source: str = "startup", keys: dict | None = None,
) -> dict:
    """One session config-keys log row. A value is the log's own token: `false` is off, `true` or any enum token is on.

    `keys`, when given, replaces the three keys' entries entirely, so a test can omit one or add an unknown token.
    """
    return {
        "session_id": session_id,
        "source": source,
        "logged_at": logged_at,
        "keys": keys if keys is not None else {
            "review_orchestrator_routing": routing,
            "round_consult_round2_pilot": round2,
            "architect_fix_plan_pilot": architect,
        },
    }


def _write_log(config_dir: Path, *rows: dict | str | bytes, mode: int = 0o600, final_newline: bool = True) -> Path:
    """Write the log the way the recorder does: every row after a newline and followed by one.

    A `str` or `bytes` row is written as given, so a test can plant a torn, over-long or non-JSON line.
    `final_newline=False` leaves the last row unterminated, as a write cut short does.
    """
    path = config_dir / _LOG_FILE_NAME
    body = b""
    for row in rows:
        encoded = row if isinstance(row, bytes) else row.encode() if isinstance(row, str) else json.dumps(row).encode()
        body += b"\n" + encoded + b"\n"
    if not final_newline:
        body = body.removesuffix(b"\n")
    path.write_bytes(body)
    path.chmod(mode)
    return path


def _append_log_rows(config_dir: Path, *rows: dict) -> Path:
    """Append rows to a log that may already exist, creating it `0600`."""
    path = config_dir / _LOG_FILE_NAME
    existing = path.read_bytes() if path.exists() else b""
    path.write_bytes(existing + b"".join(b"\n" + json.dumps(row).encode() + b"\n" for row in rows))
    path.chmod(0o600)
    return path


_RECORDER_HOOK_PATH = Path(__file__).resolve().parents[2] / "hooks" / "record-session-config-keys.sh"
# Far above the hook's normal run, so only a hung child trips it and the test fails instead of the suite stalling.
_RECORDER_HOOK_TIMEOUT_SECONDS = 30
# The recorder hook parses its payload with jq, so a test that runs it skips without jq.
_JQ_ABSENT = shutil.which("jq") is None
_JQ_ABSENT_SKIP_REASON = "the recorder hook needs jq"


def _run_recorder_hook(config_dir: Path, home: Path, payload: dict, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run the real recorder hook once against `config_dir`, as the harness does: the payload on stdin."""
    return subprocess.run(
        [str(_RECORDER_HOOK_PATH)], input=json.dumps(payload), capture_output=True, text=True, cwd=cwd, check=False,
        env={**os.environ, "HOME": str(home), "CLAUDE_CONFIG_DIR": str(config_dir)},
        timeout=_RECORDER_HOOK_TIMEOUT_SECONDS,
    )


# A payload that names no SessionStart event, which the hook answers with its no-row notice and no row.
_NO_ROW_PAYLOAD = {"hook_event_name": "PreToolUse", "session_id": "no-row-session"}


@functools.cache
def _real_no_row_stdout() -> str:
    """The stdout the real recorder hook prints when it writes no row.

    Cached after the first run. The test module warms it in a module fixture, so no test's subprocess or path spy sees the run.
    """
    if _JQ_ABSENT:
        pytest.skip(_JQ_ABSENT_SKIP_REASON)
    with tempfile.TemporaryDirectory() as scratch:
        scratch_dir = Path(scratch)
        config_dir = scratch_dir / "config"
        config_dir.mkdir()
        stdout = _run_recorder_hook(config_dir, scratch_dir, _NO_ROW_PAYLOAD, scratch_dir).stdout
    assert stdout.strip(), "the recorder hook printed no notice for a payload it writes no row for"
    return stdout


def _recorder_attachment(
    session_id: str, timestamp: str, *, stdout: str | None = None, command: str | None = None,
    attachment_type: str = "hook_success", sidechain: bool = False,
) -> dict:
    """A transcript record for a recorder hook attachment. The default `stdout` is the real hook's no-row notice (a lost event).

    Keys the reader ignores hold synthetic values.
    """
    return {
        "type": "attachment",
        "sessionId": session_id,
        "timestamp": timestamp,
        "isSidechain": sidechain,
        "attachment": {
            "type": attachment_type,
            "hookName": "SessionStart:startup",
            "toolUseID": f"hook-{session_id}",
            "hookEvent": "SessionStart",
            "content": "",
            "stdout": _real_no_row_stdout() if stdout is None else stdout,
            "stderr": "",
            "exitCode": 0,
            "command": command or "~/.claude/hooks/record-session-config-keys.sh",
            "durationMs": 12,
        },
    }


# Bound at import, before any test installs the path spies, so reading an account here never lands in a spy's record.
_UNSPIED_OS_OPEN = os.open
_UNSPIED_SCANDIR = os.scandir


def _read_bytes_unspied(path: Path) -> bytes:
    fd = _UNSPIED_OS_OPEN(path, os.O_RDONLY)
    try:
        chunks = []
        while chunk := os.read(fd, 65536):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _session_ids_in_account(config_dir_argument: str | None) -> list[str]:
    """The session ids an account's log rows and transcript file names carry, read leniently and without a spy seeing it."""
    if not config_dir_argument:
        return []
    account = Path(config_dir_argument)
    session_ids: list[str] = []
    with contextlib.suppress(OSError):
        for line in _read_bytes_unspied(account / _LOG_FILE_NAME).split(b"\n"):
            with contextlib.suppress(ValueError, RecursionError):
                parsed = json.loads(line)
                if isinstance(parsed, dict) and isinstance(parsed.get("session_id"), str):
                    session_ids.append(parsed["session_id"])
    with contextlib.suppress(OSError), _UNSPIED_SCANDIR(account / "projects") as project_dirs:
        for project_dir in list(project_dirs):
            with contextlib.suppress(OSError), _UNSPIED_SCANDIR(project_dir.path) as sessions:
                session_ids.extend(Path(entry.name).stem for entry in sessions if entry.name.endswith(".jsonl"))
    return [session_id for session_id in dict.fromkeys(session_ids) if _is_nameable_text(session_id)]


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
    """The branch and config dir cell of every note line, the account directory names, the --config-dir argument, and
    the session ids in that account's log and transcript file names.

    The note is read the way production reads it: bytes decoded as UTF-8 and split on `\\n` only, so a bare
    carriage return does not start a line. A cell is read leniently, since a refused note still must not be echoed.
    The --config-dir argument is treated as a config dir cell. See `_texts_a_config_dir_cell_can_print` for what a
    config dir cell contributes. A branch cell contributes its text unless it is empty, whitespace-only, or a dot
    segment.
    """
    strings: list[str] = _texts_a_config_dir_cell_can_print(config_dir_argument) if config_dir_argument else []
    strings.extend(_session_ids_in_account(config_dir_argument))
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
    assert outcome.code == 2, outcome.code
    assert len(outcome.err.splitlines()) >= 2, outcome.err
    diagnostic_line, refusal_line = outcome.err.splitlines()[:2]
    assert diagnostic_line.startswith("routing-guard: git-tracked check failed"), diagnostic_line
    assert diagnostic_line.startswith(tuple(exempt_prefixes)), (diagnostic_line, exempt_prefixes)
    assert "git working tree" in refusal_line, refusal_line
    assert refusal_line.startswith("routing-guard: --note"), refusal_line
    assert str(tmp_path) not in outcome.out + outcome.err, (outcome.out, outcome.err)


def _routing_guard_section_lines(doc_text: str) -> list[str]:
    """The lines of the `## routing-guard` section, which ends at the next `## ` heading outside a code fence."""
    section: list[str] = []
    in_section = False
    in_fence = False
    for line in doc_text.split("\n"):
        if line.startswith("```"):
            in_fence = not in_fence
        elif not in_fence and line.startswith("## "):
            if in_section:
                break
            in_section = line == _ROUTING_GUARD_HEADING
        if in_section:
            section.append(line)
    return section
