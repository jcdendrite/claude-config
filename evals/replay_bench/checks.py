"""Per-run and per-block checks for a replay run: the instruction-load log the runner's
`InstructionsLoaded` hook writes, the subagent transcript's tool calls and first-turn prompt
size, and the block calibration over a `toolkit` run and a `bare` run.

The log is untrusted input: its `file_path` values are only classified, and none reaches
`os.path.realpath` before every line of the log has passed its field checks.

Steered agents are out of scope, so these checks detect an honest agent's leak after the fact and do
not contain a hostile one.

Command-text scan. A Bash command is scanned for guarded paths as text, not executed or parsed as shell:
- A backslash-newline is removed first, as bash does. Then each line is tokenized on its own: its words are
  `shlex`'s, or whitespace-separated when `shlex` cannot split the line.
- Each word is a candidate whole, and again as the pieces left by splitting it on shell operators and quotes.
  A quoted word is never split on whitespace, but a quoted string that spans lines is split at each line break.
- A candidate longer than `MAX_PATH_CHARS` is skipped and recorded, so a guarded path inside a long word is
  still found through its shorter pieces.
- `~`, `$HOME` and `${HOME}` at the start of a candidate expand to the home directory, and `$PWD` and
  `${PWD}` to the fixture. No other variable expands.
- A candidate hits a guarded location when it names the location, is below it, or is a directory holding it
  that is still inside the location's own tree. The trees are the config dir (the toolkit and the projects
  root), each live checkout root (its changed paths and its `.git`), the local dir, and each
  `FIXTURE_DIR_PREFIX` directory other than the run's own. A directory above a tree is never a hit, so `/`,
  `~`, `$HOME` and a division operator in a heredoc fail nothing, and `ls ~/.claude` fails.

The scan cannot see these shapes, which are illustrative, not exhaustive:
- a command run on a directory above a guarded tree, as in `grep -r x ~`;
- a path relative to a directory the command changed into;
- a path in a nested command string, as in `bash -c`, `eval` or `xargs`, which is scanned only when `shlex`
  cannot split its line;
- a path built from another shell variable, a glob, brace expansion or a colon list;
- a path glued to a short option, as in `tar -tf<path>`;
- a case variant of a guarded path on a case-insensitive volume;
- a Bash command that writes outside the fixture, which is recorded but never an `out-of-fixture-write`;
- a symlink created in the fixture, written through and removed before the check;
- a read or write of the run's own session files, which the own-session exemption allows.

A guarded path that appears only as text, in a heredoc body or a quoted search pattern, is a false hit.
"""
from __future__ import annotations

import json
import os
import posixpath
import re
import shlex
import stat
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePath, PurePosixPath
from typing import TypeGuard

from review_bench import runner

REASON_ARM_LEAK = "arm-leak"
REASON_INSTRUMENT_MISSING = "instrument-missing"
REASON_INSTRUMENT_LOG_INVALID = "instrument-log-invalid"
REASON_FIXTURE_INSTRUCTION_LOAD = "fixture-instruction-load"
REASON_ACCOUNT_HOOKS_MISSING = "account-hooks-missing"
# Every reason the instruction-load check or the account-hook check returns. The runner never retries one.
INSTRUMENT_REASONS = frozenset({
    REASON_ARM_LEAK, REASON_INSTRUMENT_MISSING, REASON_INSTRUMENT_LOG_INVALID,
    REASON_FIXTURE_INSTRUCTION_LOAD, REASON_ACCOUNT_HOOKS_MISSING,
})
REASON_ARM_CONFIG_MISSING = "arm-config-missing"
REASON_OUT_OF_FIXTURE_WRITE = "out-of-fixture-write"
REASON_OUT_OF_FIXTURE_PATH = "out-of-fixture-path"

LOAD_REASON_SESSION_START = "session_start"
LOAD_REASON_PATH_GLOB_MATCH = "path_glob_match"
# The key a subagent's `path_glob_match` line carries the triggering file under.
TRIGGER_PATH_KEY = "trigger_file_path"
# Linux's PATH_MAX. macOS's is 1,024, so no real path exceeds it, and `realpath` time grows faster than linearly in length.
MAX_PATH_CHARS = 4096

SESSIONS_DIR_NAME = "sessions"
TOOLKIT_SUBDIRECTORY_NAMES = ("rules", "agents", "skills")
GIT_DIR_NAME = ".git"

NON_EXECUTION_PERMISSION_RULE = "permission-rule"
PERMISSION_DECISION_REJECT = "reject"
# The `toolDenialKind` values observed on a call that never ran. `interrupted` and any unknown value count as
# executed, so a new harness value fails closed.
NEVER_EXECUTED_DENIAL_KINDS = frozenset({
    NON_EXECUTION_PERMISSION_RULE, "user-rejected", "automode-blocked", "automode-unavailable",
})
_TOOL_NAME_BASH = "Bash"
_TOOL_NAME_READ = "Read"
_TOOL_NAME_NOTEBOOK_EDIT = "NotebookEdit"
_WRITE_TOOL_NAMES = frozenset({"Edit", "MultiEdit", "Write", _TOOL_NAME_NOTEBOOK_EDIT})

# Shell operators and quote characters. A path glued to one is a path the shell would see on its own.
_SHELL_SEPARATOR_RE = re.compile(r"""\$\(|[;|&<>()=`'"]""")
_HOME_VARIABLE_RE = re.compile(r"^(?:\$HOME|\$\{HOME\})(?=/|$)")
_PWD_VARIABLE_RE = re.compile(r"^(?:\$PWD|\$\{PWD\})(?=/|$)")
REASON_TRANSCRIPT_UNREADABLE = runner.VALIDITY_FAIL_TRANSCRIPT_UNREADABLE

# A rough token estimate. Halving the `CLAUDE.md` term leaves room for tokenizer variance.
CHARS_PER_TOKEN_ESTIMATE = 4
CLAUDE_MD_MARGIN_FRACTION = 0.5

EXCLUDE_NOTE_EXERCISED = (
    "exclude note: the `bare` run read a file that triggered a rule load in the `toolkit` run, "
    "so its loads test the exclude."
)
EXCLUDE_NOTE_NOT_EXERCISED = (
    "exclude note: the `bare` run read none of the files that triggered a rule load in the `toolkit` run, "
    "so it did not test the exclude."
)
EXCLUDE_NOTE_RESIDUAL = (
    "A `bare` run that read no rule-matching file passes without testing the exclude, "
    "and a later `bare` run that reads one fails `arm-leak`."
)


class LoadClass(StrEnum):
    TOOLKIT = "toolkit"
    FIXTURE = "fixture"
    OTHER = "other"


def _real(path: str | os.PathLike) -> Path:
    return Path(os.path.realpath(path))


def _normalized(path: str | os.PathLike) -> Path:
    return Path(os.path.normpath(path))


def _relative_to_any(path: Path, bases: Collection[Path]) -> Path | None:
    for base in bases:
        if path.is_relative_to(base):
            return path.relative_to(base)
    return None


def _expand_word(word: str, base_dir: Path) -> str:
    """Expands a leading `~`, `$HOME` or `${HOME}` to the home directory and a leading `$PWD` or `${PWD}` to
    `base_dir`, the fixture. The agent's home is the runner's, since the launch environment passes `HOME`."""
    home = os.path.expanduser("~")
    if not home.startswith("~"):
        if word == "~" or word.startswith("~/"):
            word = home + word[1:]
        word = _HOME_VARIABLE_RE.sub(lambda _match: home, word)
    return _PWD_VARIABLE_RE.sub(lambda _match: str(base_dir), word)


def _path_forms(raw_path: str, base_dir: Path) -> tuple[Path, Path] | None:
    """The path as a shell would name it (expanded, joined to `base_dir`, `..` collapsed) and the path it
    resolves to. None for a path that cannot be resolved, such as one holding a NUL byte or longer than a path can be."""
    if "\x00" in raw_path or len(raw_path) > MAX_PATH_CHARS:
        return None
    try:
        expanded = _expand_word(raw_path, base_dir)
        lexical = _normalized(expanded if os.path.isabs(expanded) else base_dir / expanded)
        return lexical, runner._resolve(expanded, base_dir=base_dir, expand_home=False)
    except (ValueError, OSError, RuntimeError):
        return None


def _chain(path: Path) -> tuple[Path, ...]:
    return (path, *path.parents)


# --- Toolkit classification ----------------------------------------------------


@dataclass(frozen=True)
class ToolkitPaths:
    """The account's toolkit files. A path belongs when its normalized form or its `realpath` does, so a
    stowed file reported by its link target still classifies, including where `rules/`, `agents/` or
    `skills/` is a real directory of per-file links.

    Membership is a set lookup on the path and its parents, so its cost does not grow with the number of
    toolkit files. A directory above the toolkit belongs only up to the config dir, so `ancestors` stops there.
    """

    claude_md_forms: frozenset[Path]
    roots: frozenset[Path]
    rules_roots: frozenset[Path]
    # Resolved path of each file found below a toolkit subdirectory -> its config-relative name.
    listed_files: Mapping[Path, str]
    rule_files: frozenset[Path]
    # Longest root first, so a linked directory names its files before the subdirectory's root does.
    display_roots: tuple[tuple[Path, str], ...]
    read_allow_files: frozenset[Path]
    anchors: frozenset[Path]
    ancestors: frozenset[Path]

    @classmethod
    def from_config_dir(cls, config_dir: Path, read_allow_list: Collection[str | os.PathLike] = ()) -> ToolkitPaths:
        roots: set[Path] = set()
        rules_roots: set[Path] = set()
        rule_files: set[Path] = set()
        listed_files: dict[Path, str] = {}
        display_roots: list[tuple[Path, str]] = []
        for name in TOOLKIT_SUBDIRECTORY_NAMES:
            top = config_dir / name
            top_forms = {_normalized(top), _real(top)}
            link_roots: set[Path] = set()
            files_below: list[Path] = []
            for current, directory_names, file_names in os.walk(top):
                for entry_name in directory_names:
                    entry = Path(current) / entry_name
                    if entry.is_symlink():
                        link_roots.add(_real(entry))
                        display_roots.append((_real(entry), _display_name(name, entry.relative_to(top))))
                for entry_name in file_names:
                    entry = Path(current) / entry_name
                    listed_files[_real(entry)] = _display_name(name, entry.relative_to(top))
                    files_below.append(_real(entry))
            roots |= top_forms | link_roots
            display_roots.extend((root, name) for root in top_forms)
            if name == "rules":
                rules_roots |= top_forms | link_roots
                rule_files.update(files_below)
        claude_md_forms = frozenset({_normalized(config_dir / "CLAUDE.md"), _real(config_dir / "CLAUDE.md")})
        read_allow_files = frozenset(form for entry in read_allow_list for form in (_normalized(entry), _real(entry)))
        anchors = frozenset(roots | claude_md_forms | read_allow_files | frozenset(listed_files))
        config_roots = {_normalized(config_dir), _real(config_dir)}
        return cls(
            claude_md_forms=claude_md_forms, roots=frozenset(roots), rules_roots=frozenset(rules_roots),
            listed_files=listed_files, rule_files=frozenset(rule_files),
            display_roots=tuple(sorted(display_roots, key=lambda item: len(item[0].parts), reverse=True)),
            read_allow_files=read_allow_files, anchors=anchors,
            ancestors=frozenset(parent for anchor in anchors for parent in _parents_within(anchor, config_roots)),
        )

    def contains(self, *forms: Path) -> bool:
        """True for a toolkit file, a path below a toolkit root, or a directory holding one up to the config dir."""
        return any(
            form in self.anchors or form in self.ancestors or any(parent in self.roots for parent in form.parents)
            for form in forms
        )

    def is_read_allow_listed(self, *forms: Path) -> bool:
        return any(form in self.read_allow_files for form in forms)

    def is_rule_file(self, *forms: Path) -> bool:
        return any(
            form in self.rule_files
            or (form not in self.rules_roots and any(parent in self.rules_roots for parent in form.parents))
            for form in forms
        )

    def display_path(self, *forms: Path) -> str:
        """The config-relative name where a form falls under a toolkit root or is a listed file, else the last form."""
        for form in forms:
            if form in self.claude_md_forms:
                return "CLAUDE.md"
            if form in self.listed_files:
                return self.listed_files[form]
            for root, name in self.display_roots:
                if form.is_relative_to(root):
                    return _display_name(name, form.relative_to(root))
        return str(forms[-1])


def _parents_within(path: Path, tree_roots: Collection[Path]) -> tuple[Path, ...]:
    """The directories above `path` up to and including the first of `tree_roots`; empty when `path` is not below one."""
    chain = []
    for parent in path.parents:
        chain.append(parent)
        if parent in tree_roots:
            return tuple(chain)
    return ()


def _display_name(subdirectory_name: str, relative: Path) -> str:
    return PurePosixPath(subdirectory_name, relative.as_posix()).as_posix()


# --- Instruction-load log ------------------------------------------------------


@dataclass(frozen=True)
class InstructionLoad:
    file_path: str
    load_reason: str
    agent_id: str | None
    trigger_file_path: str | None

    @property
    def is_subagent(self) -> bool:
        return self.agent_id is not None


@dataclass(frozen=True)
class InstructionLog:
    """`loads` is empty whenever `reason` is set: a log that fails a parse check is not read further."""

    loads: tuple[InstructionLoad, ...]
    reason: str | None = None
    detail: str | None = None


class _InvalidLogLine(Exception):
    pass


def _check_file_path(file_path: str) -> None:
    if "\x00" in file_path:
        raise _InvalidLogLine("has a file_path holding a NUL byte")
    if not os.path.isabs(file_path):
        raise _InvalidLogLine("has a file_path that is not absolute")
    if len(file_path) > MAX_PATH_CHARS:
        raise _InvalidLogLine("has a file_path longer than a path can be")
    try:
        file_path.encode("utf-8")  # a lone surrogate passes the checks above and makes `realpath` raise
    except UnicodeEncodeError as exc:
        raise _InvalidLogLine("has a file_path that cannot be resolved") from exc


def _parse_log_line(line: str, session_id: str) -> InstructionLoad:
    try:
        payload = json.loads(line)
    except (ValueError, RecursionError) as exc:  # ValueError covers a malformed line and an over-long integer
        raise _InvalidLogLine("does not parse as JSON") from exc
    if not isinstance(payload, dict):
        raise _InvalidLogLine("is not a JSON object")
    agent_id = None
    if "agent_id" in payload:
        agent_id = payload["agent_id"]
        if not isinstance(agent_id, str) or not agent_id:
            raise _InvalidLogLine("has an agent_id that is null, empty or not a string")
    for key in ("session_id", "file_path", "load_reason"):
        if not isinstance(payload.get(key), str):
            raise _InvalidLogLine(f"has a {key} that is absent or not a string")
    _check_file_path(payload["file_path"])
    if payload["session_id"] != session_id:
        raise _InvalidLogLine("belongs to another session")
    trigger = payload.get(TRIGGER_PATH_KEY) if payload["load_reason"] == LOAD_REASON_PATH_GLOB_MATCH else None
    if not isinstance(trigger, str) or "\x00" in trigger:
        trigger = None
    return InstructionLoad(payload["file_path"], payload["load_reason"], agent_id, trigger)


def parse_instruction_log(log_path: Path, *, session_id: str) -> InstructionLog:
    """Never raises on log content or a missing file. A torn last line, which a read at the flush timeout
    can see, fails as invalid."""
    try:
        text = log_path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        return InstructionLog((), REASON_INSTRUMENT_MISSING, "the instruction log is absent")
    except UnicodeDecodeError:
        return InstructionLog((), REASON_INSTRUMENT_LOG_INVALID, "the instruction log is not UTF-8")
    except OSError as exc:
        detail = f"the instruction log is unreadable: {runner._os_error_detail(exc)}"
        return InstructionLog((), REASON_INSTRUMENT_LOG_INVALID, detail)
    if not text.strip():
        return InstructionLog((), REASON_INSTRUMENT_MISSING, "the instruction log is empty")
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    loads: list[InstructionLoad] = []
    for number, line in enumerate(lines, start=1):
        try:
            loads.append(_parse_log_line(line, session_id))
        except _InvalidLogLine as exc:
            return InstructionLog((), REASON_INSTRUMENT_LOG_INVALID, f"line {number} {exc}")
    if len({load.agent_id for load in loads if load.is_subagent}) > 1:
        return InstructionLog((), REASON_INSTRUMENT_LOG_INVALID, "the log carries more than one agent_id")
    return InstructionLog(tuple(loads))


@dataclass(frozen=True)
class ClassifiedLoad:
    load: InstructionLoad
    load_class: LoadClass
    lexical: Path
    resolved: Path
    display_path: str


def classify_loads(log: InstructionLog, *, toolkit: ToolkitPaths, fixture_dir: Path) -> tuple[ClassifiedLoad, ...]:
    """A path lexically inside the fixture is a fixture load, even when its `realpath` is a toolkit file."""
    fixture_bases = (_normalized(fixture_dir), _real(fixture_dir))
    classified = []
    for load in log.loads:
        lexical = _normalized(load.file_path)
        resolved = _real(load.file_path)
        lexical_relative = _relative_to_any(lexical, fixture_bases)
        resolved_relative = _relative_to_any(resolved, fixture_bases)
        if lexical_relative is not None:
            load_class, display_path = LoadClass.FIXTURE, lexical_relative.as_posix()
        elif toolkit.contains(lexical, resolved):
            load_class, display_path = LoadClass.TOOLKIT, toolkit.display_path(lexical, resolved)
        elif resolved_relative is not None:
            load_class, display_path = LoadClass.FIXTURE, resolved_relative.as_posix()
        else:
            load_class, display_path = LoadClass.OTHER, str(resolved)
        classified.append(ClassifiedLoad(load, load_class, lexical, resolved, display_path))
    return tuple(classified)


@dataclass(frozen=True)
class LoadExpectation:
    """What an arm's instruction loads must look like.

    `excludes_toolkit` arms fail on any subagent load outside the fixture and on any dispatcher load
    other than the expected one, since the hook logs nothing for what a subagent inherits from its
    dispatcher. `allowed_fixture_loads` are fixture-relative paths.
    """

    expected_dispatcher_claude_md: Path
    excludes_toolkit: bool
    allowed_fixture_loads: frozenset[str] = frozenset()


@dataclass(frozen=True)
class InstructionLoadCheck:
    reasons: tuple[str, ...]
    # Class name -> path -> load count, for the subagent's loads. A duplicate load is counted, never failed.
    subagent_loads_by_class: Mapping[str, Mapping[str, int]]


def check_instruction_loads(
    log: InstructionLog, expectation: LoadExpectation, *, toolkit: ToolkitPaths, fixture_dir: Path,
) -> InstructionLoadCheck:
    if log.reason is not None:
        return InstructionLoadCheck((log.reason,), {})
    expected = _real(expectation.expected_dispatcher_claude_md)
    classified = classify_loads(log, toolkit=toolkit, fixture_dir=fixture_dir)
    reasons: list[str] = []

    positive_control_fired = any(
        not item.load.is_subagent and item.load.load_reason == LOAD_REASON_SESSION_START and item.resolved == expected
        for item in classified
    )
    if not positive_control_fired:
        reasons.append(REASON_INSTRUMENT_MISSING)

    if expectation.excludes_toolkit and any(
        (item.load.is_subagent and item.load_class is not LoadClass.FIXTURE)
        or (not item.load.is_subagent and item.resolved != expected)
        for item in classified
    ):
        reasons.append(REASON_ARM_LEAK)

    if any(
        item.load_class is LoadClass.FIXTURE and item.display_path not in expectation.allowed_fixture_loads
        for item in classified
    ):
        reasons.append(REASON_FIXTURE_INSTRUCTION_LOAD)

    loads_by_class: dict[str, dict[str, int]] = {}
    for item in classified:
        if item.load.is_subagent:
            counts = loads_by_class.setdefault(item.load_class.value, {})
            counts[item.display_path] = counts.get(item.display_path, 0) + 1
    return InstructionLoadCheck(tuple(reasons), loads_by_class)


def _first_line_matches(path: str, expected_first_line: bytes) -> bool:
    """Opens without following a link and without blocking on a FIFO, then reads only a bounded first line."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return False
        head = os.read(descriptor, len(expected_first_line) + 3)
    except OSError:
        return False
    finally:
        os.close(descriptor)
    return head.split(b"\n", 1)[0].rstrip(b"\r") == expected_first_line


def check_account_hooks(config_dir: Path, session_id: str) -> str | None:
    """`REASON_ACCOUNT_HOOKS_MISSING` unless the account's `sessions/` holds a non-symlink file whose
    first line is the run's session ID. An empty session ID matches nothing."""
    if not session_id:
        return REASON_ACCOUNT_HOOKS_MISSING
    try:
        with os.scandir(config_dir / SESSIONS_DIR_NAME) as entries:
            paths = [entry.path for entry in entries]
    except OSError:
        return REASON_ACCOUNT_HOOKS_MISSING
    expected_first_line = session_id.encode()
    if any(_first_line_matches(path, expected_first_line) for path in paths):
        return None
    return REASON_ACCOUNT_HOOKS_MISSING


# --- Subagent transcript -------------------------------------------------------


@dataclass(frozen=True)
class ToolCall:
    tool_use_id: str
    name: str
    tool_input: dict
    answered: bool
    denied: bool  # the result carries a permission-rule `non_execution_kind`, so the call never ran


@dataclass(frozen=True)
class SubagentTranscript:
    """`skipped_lines` counts the non-blank lines that were not a JSON object. A transcript with any skipped
    line, a torn last line or an `unreadable_detail` may be missing calls, so the caller should not read it as clean."""

    tool_calls: tuple[ToolCall, ...]
    permission_denials: int
    first_turn_prompt_size: int | None
    skipped_lines: int = 0
    last_line_torn: bool = False
    unreadable_detail: str | None = None


def _is_count(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _prompt_size(message: Mapping) -> int | None:
    """None when a usage field is absent or not a count, and when all three are zero."""
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    fields = [usage.get(name) for name in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")]
    counts = [field for field in fields if _is_count(field)]
    if len(counts) != len(fields):
        return None
    return sum(counts) or None


def _marks_permission_denial(carrier: object) -> bool:
    """True when the carrier holds any of the three denial shapes named in `read_subagent_transcript`."""
    if not isinstance(carrier, dict):
        return False
    meta = carrier.get("tool_result_meta")
    if isinstance(meta, list) and any(
        isinstance(item, dict) and item.get("non_execution_kind") == NON_EXECUTION_PERMISSION_RULE for item in meta
    ):
        return True
    denial_kind = carrier.get("toolDenialKind")
    if isinstance(denial_kind, str) and denial_kind in NEVER_EXECUTED_DENIAL_KINDS:
        return True
    decision = carrier.get("permissionDecision")
    return isinstance(decision, dict) and decision.get("decision") == PERMISSION_DECISION_REJECT


def _read_transcript_entries(subagent_jsonl: Path) -> tuple[list[dict], int, bool]:
    """The JSON-object lines, the count of other non-blank lines, and whether the last non-blank line failed to parse."""
    text = subagent_jsonl.read_text(encoding="utf-8", errors="replace")
    entries: list[dict] = []
    skipped = 0
    last_line_torn = False
    for line in (line for line in text.split("\n") if line.strip()):
        parsed: object = None
        last_line_torn = False
        try:
            parsed = json.loads(line)
        except (ValueError, RecursionError):
            last_line_torn = True
        if isinstance(parsed, dict):
            entries.append(parsed)
        else:
            skipped += 1
    return entries, skipped, last_line_torn


def read_subagent_transcript(subagent_jsonl: Path) -> SubagentTranscript:
    """Entries that are neither an assistant message nor a user message, such as the attachment a
    `SubagentStop` hook's send-back leaves, are ignored. Never raises on the file's content or absence.

    A call counts as denied when its carrier holds any of three markers: a `tool_result_meta` item whose
    `non_execution_kind` is `permission-rule`, a `toolDenialKind` in `NEVER_EXECUTED_DENIAL_KINDS`, or a
    `permissionDecision` object whose `decision` is `reject`. A `toolDenialKind` of `interrupted`, or any
    value outside that set, leaves the call counted as executed. The carrier is the `tool_result` block, or
    the enclosing user entry when it holds exactly one result, since an entry-level marker cannot name which
    of several results it denied. The last two markers sit on the user entry in a main-session transcript
    recorded on CLI 2.1.323, and a subagent transcript may carry the first on the block instead.
    """
    try:
        entries, skipped_lines, last_line_torn = _read_transcript_entries(subagent_jsonl)
    except OSError as exc:
        return SubagentTranscript((), 0, None, unreadable_detail=runner._os_error_detail(exc))
    pending_calls: list[tuple[str, str, dict]] = []
    answered_ids: set[str] = set()
    denied_ids: set[str] = set()
    first_turn_prompt_size: int | None = None
    seen_assistant_entry = False
    for entry in entries:
        message = entry.get("message")
        message = message if isinstance(message, dict) else {}
        content = message.get("content")
        blocks = [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []
        if entry.get("type") == "assistant":
            if not seen_assistant_entry:
                seen_assistant_entry = True
                first_turn_prompt_size = _prompt_size(message)
            for block in blocks:
                name = block.get("name")
                tool_use_id = block.get("id")
                tool_input = block.get("input")
                if block.get("type") == "tool_use" and isinstance(name, str):
                    pending_calls.append((
                        tool_use_id if isinstance(tool_use_id, str) else "", name,
                        tool_input if isinstance(tool_input, dict) else {},
                    ))
        elif entry.get("type") == "user":
            result_blocks = [block for block in blocks if block.get("type") == "tool_result"]
            entry_marks_denial = len(result_blocks) == 1 and _marks_permission_denial(entry)
            for block in result_blocks:
                tool_use_id = block.get("tool_use_id")
                if isinstance(tool_use_id, str) and tool_use_id:
                    answered_ids.add(tool_use_id)
                    if entry_marks_denial or _marks_permission_denial(block):
                        denied_ids.add(tool_use_id)
    tool_calls = tuple(
        ToolCall(tool_use_id, name, tool_input, tool_use_id in answered_ids, tool_use_id in denied_ids)
        for tool_use_id, name, tool_input in pending_calls
    )
    permission_denials = sum(call.denied for call in tool_calls)
    return SubagentTranscript(tool_calls, permission_denials, first_turn_prompt_size, skipped_lines, last_line_torn)


# --- Path checks over tool calls -----------------------------------------------


def _bash_candidates(call: ToolCall, call_number: int, unparsed: list[str]) -> list[str]:
    """The candidate path strings of a Bash command: each word whole and its pieces split on shell operators.

    Each line is tokenized on its own, so quotes in prose never pair across lines and hide a later command.
    A line `shlex` cannot split, such as one holding an apostrophe, is scanned as whitespace-separated raw
    text instead of skipped.
    """
    command = call.tool_input.get("command")
    if not isinstance(command, str):
        unparsed.append(f"{call.name} call {call_number}: command is missing or not a string")
        return []
    words: list[str] = []
    for line in command.replace("\\\n", "").split("\n"):
        try:
            words.extend(shlex.split(line))
        except ValueError:
            unparsed.append(f"{call.name} call {call_number}: a line cannot be split, scanned as raw text")
            words.extend(line.split())
    candidates: dict[str, None] = {}
    for word in words:
        for candidate in (word, *_SHELL_SEPARATOR_RE.split(word)):
            if len(candidate) > MAX_PATH_CHARS:
                unparsed.append(f"{call.name} call {call_number}: a candidate is longer than a path can be")
            elif candidate:
                candidates[candidate] = None
    return list(candidates)


def _write_target(call: ToolCall) -> str | None:
    key = "notebook_path" if call.name == _TOOL_NAME_NOTEBOOK_EDIT else "file_path"
    target = call.tool_input.get(key)
    return target if isinstance(target, str) else None


@dataclass(frozen=True)
class PathCheck:
    """Holds raw agent commands and absolute machine paths, so it belongs only in gitignored records."""

    failure_reasons: tuple[str, ...]
    out_of_fixture_writes: tuple[str, ...]
    out_of_fixture_tokens: tuple[str, ...]
    # Tokens that resolve outside the fixture and fail no rule.
    other_outside_fixture_tokens: tuple[str, ...]
    bash_commands: tuple[str, ...]
    unparsed: tuple[str, ...]


def check_tool_call_paths(
    transcript: SubagentTranscript, *, fixture_dir: Path, config_dir: Path, projects_root: Path,
    own_session_paths: tuple[Path, ...], local_dir: Path, live_checkout_roots: tuple[Path, ...],
    changed_relpaths: tuple[str, ...],
) -> PathCheck:
    """A transcript that is unreadable, has a skipped or torn line, or may be missing calls fails as
    `REASON_TRANSCRIPT_UNREADABLE`, since a clean verdict over missing calls proves nothing.

    A live checkout's `.git` directory is guarded beside its changed files.

    A call the permission flow denied reached nothing, so the kinds checked here are skipped once denied: a
    denied Bash command is only recorded, and a denied Write, Edit, MultiEdit or NotebookEdit adds no
    `out-of-fixture-write`. Read, Grep and Glob targets are checked by the runner's validity step, not here.
    A denied call still counts as evidence for `toolkit_reach_reasons`.
    """
    fixture_real = _real(fixture_dir)
    local_real = _real(local_dir)
    config_bases = {_normalized(config_dir), _real(config_dir)}
    projects_real = projects_root.resolve()
    live_bases = {root.resolve() for root in live_checkout_roots}
    leak_targets = runner.live_checkout_leak_targets(live_checkout_roots, changed_relpaths)
    git_dirs = tuple(root / GIT_DIR_NAME for root in live_bases)
    writes: list[str] = []
    failing_tokens: list[str] = []
    other_tokens: list[str] = []
    commands: list[str] = []
    unparsed: list[str] = []

    for call_number, call in enumerate(transcript.tool_calls):
        if call.name in _WRITE_TOOL_NAMES:
            if call.denied:
                continue
            target = _write_target(call)
            if target is None:
                unparsed.append(f"{call.name} call {call_number}: target path is missing or not a string")
                continue
            forms = _path_forms(target, fixture_dir)
            if forms is None:
                unparsed.append(f"{call.name} call {call_number}: target path is unresolvable")
            elif not forms[1].is_relative_to(fixture_real):
                writes.append(target)
        elif call.name == _TOOL_NAME_BASH:
            command = call.tool_input.get("command")
            if isinstance(command, str):
                commands.append(command)
            if call.denied:
                continue
            for token in _bash_candidates(call, call_number, unparsed):
                forms = _path_forms(token, fixture_dir)
                if forms is None:
                    unparsed.append(f"{call.name} call {call_number}: a token is unresolvable")
                    continue
                resolved = forms[1]
                if resolved.is_relative_to(fixture_real):
                    continue
                chain = _chain(resolved)
                inside_config = any(parent in config_bases for parent in chain)
                inside_live_checkout = any(parent in live_bases for parent in chain)
                # The tree bound limits only the arm that makes a directory above a target a hit. A word that is,
                # or lies below, a target hits wherever that target resolves, so a relocated target still counts.
                at_or_below_projects_root = resolved.is_relative_to(projects_real)
                at_or_below_changed_path = any(resolved.is_relative_to(target) for target in leak_targets)
                if (
                    ((inside_config or at_or_below_projects_root)
                     and runner.is_config_dir_leak(resolved, projects_root, own_session_paths))
                    or ((inside_live_checkout or at_or_below_changed_path)
                        and runner.is_changed_file_leak(resolved, leak_targets))
                    or any(resolved.is_relative_to(git_dir) for git_dir in git_dirs)
                    or resolved.is_relative_to(local_real)
                    or any(part.startswith(runner.FIXTURE_DIR_PREFIX) for part in resolved.parts)
                ):
                    failing_tokens.append(token)
                else:
                    other_tokens.append(token)

    reasons = []
    if transcript.unreadable_detail is not None or transcript.last_line_torn or transcript.skipped_lines:
        reasons.append(REASON_TRANSCRIPT_UNREADABLE)
    if writes:
        reasons.append(REASON_OUT_OF_FIXTURE_WRITE)
    if failing_tokens:
        reasons.append(REASON_OUT_OF_FIXTURE_PATH)
    return PathCheck(
        tuple(reasons), tuple(dict.fromkeys(writes)), tuple(dict.fromkeys(failing_tokens)),
        tuple(dict.fromkeys(other_tokens)), tuple(commands), tuple(dict.fromkeys(unparsed)),
    )


@dataclass(frozen=True)
class ToolkitReaches:
    paths: tuple[str, ...]
    unparsed: tuple[str, ...]


def find_toolkit_file_reaches(transcript: SubagentTranscript, *, toolkit: ToolkitPaths, fixture_dir: Path) -> ToolkitReaches:
    """The raw Read, Grep, Glob and Bash-candidate paths that resolve to a toolkit file or a directory holding
    one up to the config dir, a read `toolkit` makes by design and `bare` fails `arm-leak` on. A denied call
    still counts, since the attempt is evidence about the arm."""
    reaches: list[str] = []
    unparsed: list[str] = []
    for call_number, call in enumerate(transcript.tool_calls):
        if call.name in runner._READ_LIKE_TOOLS:
            target = runner._read_like_target(call.name, call.tool_input)
            raw_paths = [target] if target else []
        elif call.name == _TOOL_NAME_BASH:
            raw_paths = _bash_candidates(call, call_number, unparsed)
        else:
            continue
        for raw_path in raw_paths:
            forms = _path_forms(raw_path, fixture_dir)
            if forms is None:
                unparsed.append(f"{call.name} call {call_number}: a path is unresolvable")
            elif toolkit.contains(*forms):
                reaches.append(raw_path)
    return ToolkitReaches(tuple(dict.fromkeys(reaches)), tuple(dict.fromkeys(unparsed)))


def toolkit_reach_reasons(
    transcript: SubagentTranscript, expectation: LoadExpectation, *, toolkit: ToolkitPaths, fixture_dir: Path,
) -> tuple[str, ...]:
    reaches = find_toolkit_file_reaches(transcript, toolkit=toolkit, fixture_dir=fixture_dir)
    if expectation.excludes_toolkit and reaches.paths:
        return (REASON_ARM_LEAK,)
    return ()


@dataclass(frozen=True)
class AllowListRead:
    path: str
    denied: bool


@dataclass(frozen=True)
class ReadGrantRecords:
    allow_list_reads: tuple[AllowListRead, ...]
    denied_other_toolkit_reads: tuple[str, ...]


def record_read_grants(transcript: SubagentTranscript, *, toolkit: ToolkitPaths, fixture_dir: Path) -> ReadGrantRecords:
    """Reads of Read-allow-list paths that ran or were denied, and the paths of denied Reads of any other
    toolkit file. A Read with no result in the transcript is in neither."""
    allow_list_reads: list[AllowListRead] = []
    denied_other: list[str] = []
    for call in transcript.tool_calls:
        raw_path = call.tool_input.get("file_path")
        if call.name != _TOOL_NAME_READ or not isinstance(raw_path, str) or not call.answered:
            continue
        forms = _path_forms(raw_path, fixture_dir)
        if forms is None:
            continue
        if toolkit.is_read_allow_listed(*forms):
            allow_list_reads.append(AllowListRead(raw_path, call.denied))
        elif call.denied and toolkit.contains(*forms):
            denied_other.append(raw_path)
    return ReadGrantRecords(tuple(allow_list_reads), tuple(dict.fromkeys(denied_other)))


# --- Block calibration ---------------------------------------------------------


def rule_control_loads(log: InstructionLog, toolkit: ToolkitPaths, *, fixture_dir: Path) -> tuple[InstructionLoad, ...]:
    """The subagent `path_glob_match` loads of a toolkit file below the account's `rules/` directory, which
    show that the hook sees a subagent's path-scoped rule loads."""
    return tuple(
        item.load for item in classify_loads(log, toolkit=toolkit, fixture_dir=fixture_dir)
        if item.load.is_subagent and item.load.load_reason == LOAD_REASON_PATH_GLOB_MATCH
        and item.load_class is LoadClass.TOOLKIT and toolkit.is_rule_file(item.lexical, item.resolved)
    )


def dispatcher_rule_files(log: InstructionLog, toolkit: ToolkitPaths, *, fixture_dir: Path) -> tuple[Path, ...]:
    """The rule files the dispatcher loaded at `session_start`. `bare`'s exclude drops them, so each
    one raises the inheritance margin."""
    return tuple(dict.fromkeys(
        item.resolved for item in classify_loads(log, toolkit=toolkit, fixture_dir=fixture_dir)
        if not item.load.is_subagent and item.load.load_reason == LOAD_REASON_SESSION_START
        and item.load_class is LoadClass.TOOLKIT and toolkit.is_rule_file(item.lexical, item.resolved)
    ))


def block_calibration_verdict(
    toolkit_log: InstructionLog, *, toolkit_first_turn_size: int | None, bare_first_turn_size: int | None,
    toolkit: ToolkitPaths, fixture_dir: Path, claude_md_chars: int, code_writer_body_chars: int,
    bare_writer_body_chars: int, dispatcher_rule_chars: Sequence[int],
) -> tuple[str, ...]:
    """Every failed condition, in order: the rule-load control, then the inheritance gap.

    The gap, the `toolkit` first-turn size minus the `bare` one minus the body term, must be at least the
    margin. The body term is the one other known difference between the two subagents' first turns.
    """
    reasons: list[str] = []
    if not rule_control_loads(toolkit_log, toolkit, fixture_dir=fixture_dir):
        reasons.append(REASON_INSTRUMENT_MISSING)
    if toolkit_first_turn_size is None or bare_first_turn_size is None:
        reasons.append(REASON_ARM_CONFIG_MISSING)
        return tuple(reasons)
    body_term = (code_writer_body_chars - bare_writer_body_chars) / CHARS_PER_TOKEN_ESTIMATE
    margin = (
        CLAUDE_MD_MARGIN_FRACTION * claude_md_chars + sum(dispatcher_rule_chars)
    ) / CHARS_PER_TOKEN_ESTIMATE
    if toolkit_first_turn_size - bare_first_turn_size - body_term < margin:
        reasons.append(REASON_ARM_CONFIG_MISSING)
    return tuple(reasons)


def _fixture_relative(path_text: str, fixture_dir: Path) -> str | None:
    """Lexical, since the trigger path is never resolved. A relative path is taken as fixture-relative."""
    candidate = PurePath(path_text)
    if candidate.is_absolute():
        relative = _relative_to_any(Path(candidate), (_normalized(fixture_dir), _real(fixture_dir)))
        if relative is None:
            return None
        candidate = relative
    normalized = posixpath.normpath(candidate.as_posix())
    return None if normalized == ".." or normalized.startswith("../") else normalized


def exclude_note_lines(
    toolkit_log: InstructionLog, bare_transcript: SubagentTranscript, *, toolkit: ToolkitPaths,
    toolkit_fixture_dir: Path, bare_fixture_dir: Path,
) -> tuple[str, ...]:
    """The lines `smoke` prints about whether `bare` tested its exclude. Never gates a verdict. The
    exercised or not-exercised line appears only when the `toolkit` control fired with a trigger path."""
    trigger_relpaths = {
        relpath for load in rule_control_loads(toolkit_log, toolkit, fixture_dir=toolkit_fixture_dir)
        if load.trigger_file_path is not None
        and (relpath := _fixture_relative(load.trigger_file_path, toolkit_fixture_dir)) is not None
    }
    if not trigger_relpaths:
        return (EXCLUDE_NOTE_RESIDUAL,)
    bare_fixture_bases = (_normalized(bare_fixture_dir), _real(bare_fixture_dir))
    bare_read_relpaths: set[str] = set()
    for call in bare_transcript.tool_calls:
        raw_path = call.tool_input.get("file_path")
        if call.name != _TOOL_NAME_READ or not call.answered or call.denied or not isinstance(raw_path, str):
            continue
        forms = _path_forms(raw_path, bare_fixture_dir)
        for form in forms or ():
            relative = _relative_to_any(form, bare_fixture_bases)
            if relative is not None:
                bare_read_relpaths.add(relative.as_posix())
    exercised = bool(trigger_relpaths & bare_read_relpaths)
    return (EXCLUDE_NOTE_EXERCISED if exercised else EXCLUDE_NOTE_NOT_EXERCISED, EXCLUDE_NOTE_RESIDUAL)
