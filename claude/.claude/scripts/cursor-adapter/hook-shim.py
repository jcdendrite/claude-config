#!/usr/bin/env python3
"""Runs one unchanged harness hook on a Cursor payload and prints Cursor's hook output.

Usage: hook-shim.py --contract-version V --claude-event CLAUDE_EVENT [--mode hook|pin|pin-check|classify]
  [--matcher REGEX] [--script PATH] [--label NAME] [--deadline SECONDS] [--fail-closed|--fail-open]

Cursor's payload arrives on stdin and Cursor's output leaves on stdout as one JSON document.
The launch guard that the installer (not yet written) generates runs this file with the recorded python3 under
`-I -S` and turns a missing document into the entry's deny or `{}`. A test can load the module in-process. The install record at
$HOME/.cursor/cursor-adapter/install.json fixes the routed config dir, the tool paths and the tool map.
"""

import contextlib
import errno
import json
import os
import re
import select
import selectors
import signal
import stat
import subprocess
import sys
import time
from types import MappingProxyType

# --- contract ---

# The version of the contract between the generated commands and this code. A command carrying another version is stale.
CONTRACT_VERSION = "1"
STALE_INSTALL_DETAIL = "stale install: the engineer quits Cursor, re-runs install and relaunches."

# The command-line vocabulary. The command grammar of the installer (cursor-adapter.py, not yet written) must stay equal to it.
VALUE_FLAGS = ("--contract-version", "--claude-event", "--mode", "--matcher", "--script", "--label", "--deadline")
SWITCH_FLAGS = ("--fail-closed", "--fail-open")
MODES = ("hook", "pin", "pin-check", "classify")

# The deadline is the entry's `--deadline`, in whole seconds.
# The default is a project choice with no vendor value.
DEFAULT_DEADLINE_SECONDS = 10
MIN_DEADLINE_SECONDS = 2
# A project choice with no documented source or Cursor ceiling.
MAX_DEADLINE_SECONDS = 60
# The wrapper's `-k` value, a project choice: how long a hook that ignores the TERM gets before the KILL.
GRACE_PERIOD_SECONDS = 2
# A project choice: how long the shim keeps reading after the wrapper's kill, for the pipes to reach EOF.
DRAIN_BOUND_SECONDS = 1
# A project choice, not measured. A generated entry's `timeout` is the worst case plus this.
MARGIN_SECONDS = 2
# A project choice. GNU timeout reads a duration of 0 as no timeout, so a time left below this takes the timeout outcome.
TIME_LEFT_FLOOR_SECONDS = 0.1

# Exit statuses, from the GNU coreutils timeout manual unless noted.
TIMEOUT_STATUS_TIMED_OUT = 124
TIMEOUT_STATUS_OWN_FAILURE = 125
TIMEOUT_STATUS_KILLED = 137
SIGNAL_STATUS_BASE = 128
# A hook's exit 2 blocks the action, per the exit-code contract in Cursor's hooks documentation.
HOOK_STATUS_BLOCK = 2
# `start-failed` causes whose deny text invites one retry.
TRANSIENT_ERRNO_SYMBOLS = frozenset({"EAGAIN", "EMFILE", "ENFILE", "ENOMEM"})

# A bound on the bytes the shim reads into memory.
# A project choice, set above the largest Cursor payload observed (about 1.4 MB on `postToolUse`):
# `.claude/plans/cursor-adapter.md` § "Payload (O1, O3, O8, O15)".
PAYLOAD_CAP_BYTES = 8 * 1024 * 1024
# Each of a hook's stdout and stderr is kept up to this size.
HOOK_OUTPUT_CAP_BYTES = 1024 * 1024
# A project choice: the characters of a hook's stderr or JSON reason the shim keeps, so one deny stays a readable message.
# A reason past it is cut without a marker.
HOOK_REASON_CAP_CHARS = 16384
# A project choice: the size of one read or write on a hook's pipes, small enough that the deadline is checked often.
IO_CHUNK_BYTES = 32 * 1024

# Sentence the Recovery section of docs/cursor-adapter.md (not yet written) opens with.
RECOVERY_SENTENCE = (
    "The engineer, not the agent, follows the Recovery section. An agent runs no command from that section, "
    "retries only where this deny's own text says to, and otherwise stops and reports this deny in chat."
)

# Files beside this one that the shim reads on every call.
FIELD_TABLE_FILE_NAME = "field-table.json"
PARITY_EXCEPTIONS_FILE_NAME = "parity-exceptions.json"
INSTALL_RECORD_RELATIVE_PATH = ".cursor/cursor-adapter/install.json"
STATE_DIR_NAME = "cursor-adapter-state"
LOG_FILE_NAME = "shim.log"

# The translations a tool map entry may name. Anything else is a record the shim does not understand, so it is unusable.
TRANSLATION_VOCABULARY = ("shell", "read", "write", "delete", "grep", "task", "passthrough")
# The tools every usable tool map names.
TRANSLATED_TOOLS = ("Shell", "Read", "Write", "Delete", "Grep", "Task")
# PATH after the recorded tool directories.
SYSTEM_PATH_DIRECTORIES = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")
CLAUDE_PROCESS_ENV_NAMES = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_PID")

EXIT_OK = 0
EXIT_FAULT = 1

# --- log-field table ---
# A log line is `name=value` fields separated by one space, ending in a newline. A value holds only characters of
# LOG_VALUE_PATTERN. A value past its field's maximum, or outside the set, is replaced by the overflow token.

LOG_OVERFLOW_TOKEN = "overflow"
LOG_VALUE_PATTERN = r"[0-9A-Za-z._:+-]+"
# One line is at most this many bytes with its newline.
LOG_LINE_MAX_BYTES = 1024
# Field order and maxima. `runmode` is first written by the installer (not yet written).
LOG_FIELDS = (
    ("ts", 12),
    ("reg", 64),
    ("ev", 24),
    ("tool", 24),
    ("out", 32),
    ("exit", 8),
    ("ms", 9),
    ("drop", 8),
    ("ver", 8),
    ("size", 10),
    ("fault", 64),
    ("py", 8),
    ("runmode", 32),
)

# The grammar of a payload id used as a path component, the same as _lib_valid_session_id_component in hooks/_lib.sh.
SESSION_ID_PATTERN = r"[-A-Za-z0-9_]+"

_LOG_VALUE_RE = re.compile(LOG_VALUE_PATTERN)
_SESSION_ID_RE = re.compile(SESSION_ID_PATTERN)
_DEADLINE_RE = re.compile(r"[0-9]{1,3}", re.ASCII)

# --- the directory pin ---

# The pin prefix is the line `builtin cd -- '<dir>' || exit 1` and a newline.
# `builtin` stops a `cd` function or alias from turning a failed cd into a success.
# `exit` stays plain because `builtin exit` fails on a shell with no `builtin`.
PIN_LINE_HEAD = "builtin cd -- '"
PIN_LINE_TAIL = "' || exit 1"
# A project choice: the most newlines the recognition tries as the end of a pin line, which bounds the work a long
# command can cost while allowing a directory name that holds a few newlines.
PIN_MAX_NEWLINES = 64
_WHITESPACE = " \t\n\v\f\r"
# A shell reads a carriage return, vertical tab or form feed as part of the path word, so only space, tab and newline are blanks.
_LEADING_CD_RE = re.compile(r"""cd[ \t\n]+(?:"([^"]*)"|'([^']*)'|([^\[\] \t\n;&|<>()$`\\"']+))[ \t\n]*&&""", re.ASCII)

NO_ABSOLUTE_DIR_DETAIL = (
    "No absolute directory is known for this call: the first workspace root is not absolute, or there is no workspace "
    "root and HOME is unset or not absolute. The engineer opens the folder as a workspace root."
)
DIRECTORY_INPUT_DETAIL = (
    "The Shell tool's directory input must be empty. To work in a directory inside a workspace root, start the command "
    "with `cd <absolute path> &&`, as its only leading `cd`, naming a directory whose path holds no `$`, `~` or "
    "backtick, holds no backslash unless single-quoted, and does not resolve outside the roots. If the call is "
    "refused again with the input empty, or the "
    "directory is outside every workspace root, stop and tell the engineer in chat."
)
PIN_CHECK_DETAIL = (
    "The command does not start with the directory pin prefix. Cursor may have stopped applying the pin's rewrite, or "
    "another entry replaced the command, so the gates cannot know which directory the command runs in."
)

# Cursor events whose deny takes the `permission: deny` shape.
# `beforeSubmitPrompt` blocks with `continue: false` and is handled apart.
DENY_SHAPE_EVENTS = frozenset({"preToolUse", "beforeShellExecution", "beforeReadFile", "subagentStart"})
PRE_CALL_EVENTS = frozenset({"preToolUse", "beforeShellExecution", "beforeReadFile"})
# Claude events whose matcher selects on a name the payload or the entry fixes.
MATCHER_EVENTS = frozenset({"PreToolUse", "PostToolUse", "SessionStart", "SubagentStart", "SubagentStop"})


class Refusal(Exception):
    """A call the shim decides by an outcome of its own: a deny on a fail-closed entry, `{}` on an informational one."""

    def __init__(self, outcome, detail):
        super().__init__(outcome)
        self.outcome = outcome
        self.detail = detail


class CallState:
    """What the log line and the fault handler know about the call so far."""

    def __init__(self):
        self.started = None
        self.label = "unlabeled"
        self.mode = "hook"
        self.claude_event = ""
        self.fail_closed = True
        self.contract_version = ""
        self.cursor_event = ""
        self.tool = ""
        self.outcome = ""
        self.hook_exit = ""
        self.dropped = 0
        self.payload_bytes = 0
        self.state_dir = ""
        self.session_id = ""


class Arguments:
    def __init__(self, values):
        self.contract_version = values.get("--contract-version", "")
        self.claude_event = values["--claude-event"]
        self.mode = values.get("--mode", "hook")
        self.matcher = values.get("--matcher")
        self.script = values.get("--script", "")
        self.label = values.get("--label", "")
        self.deadline = int(values.get("--deadline", DEFAULT_DEADLINE_SECONDS))
        self.fail_closed = values.get("fail_closed", True)
        self.matcher_pattern = None


# --- pure helpers ---


def worst_case_wall_seconds(deadline):
    return deadline + GRACE_PERIOD_SECONDS + DRAIN_BOUND_SECONDS


def entry_timeout_seconds(deadline):
    """The `timeout` a generated fail-closed entry carries for `--deadline`."""
    return worst_case_wall_seconds(deadline) + MARGIN_SECONDS


def is_valid_session_id(value):
    return isinstance(value, str) and _SESSION_ID_RE.fullmatch(value) is not None


def is_valid_tool_path(value):
    return (
        isinstance(value, str)
        and value.startswith("/")
        and not any(character in value for character in (":", "\n", "\0"))
        and ".." not in value.split("/")
    )


def log_value(field, value):
    """`value`, or the overflow token when it is past `field`'s maximum or outside the log grammar."""
    maximum = dict(LOG_FIELDS)[field]
    text = str(value)
    if len(text) > maximum or _LOG_VALUE_RE.fullmatch(text) is None:
        return LOG_OVERFLOW_TOKEN
    return text


def format_log_line(fields):
    """One log line, with its newline, holding the named fields in table order. A field passed empty is omitted."""
    parts = []
    for name, _ in LOG_FIELDS:
        value = fields.get(name, "")
        if value != "":
            parts.append(f"{name}={log_value(name, value)}")
    return (" ".join(parts) + "\n").encode("ascii")


def write_log_line(state_dir, fields):
    """Appends one line to the state dir's log with a single write to an append-mode descriptor, and no lock."""
    line = format_log_line(fields)
    descriptor = os.open(
        os.path.join(state_dir, LOG_FILE_NAME), os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
    )
    try:
        # A short write (ENOSPC, EDQUOT, RLIMIT_FSIZE) is not retried, so the line may be torn.
        os.write(descriptor, line)
    finally:
        # After a complete write, a failed close loses nothing, so it must not read as a failed log line.
        with contextlib.suppress(OSError):
            os.close(descriptor)


def interpreter_version():
    return f"{sys.version_info.major}.{sys.version_info.minor}"


FIRST_CALL_DIR_NAME = "first-call"


def claim_first_call(state_dir, session_id):
    """True when this call is the first the state dir has seen for the conversation, by an exclusive create of its mark.

    The mark is `<state dir>/first-call/<session id>`. The id has passed the session-id grammar, so it is one path
    component. Any failure to make the mark reads as not first. No exception escapes."""
    directory = os.path.join(state_dir, FIRST_CALL_DIR_NAME)
    try:
        with contextlib.suppress(FileExistsError):
            os.mkdir(directory, 0o700)
        if not usable_state_dir(directory):
            return False
        descriptor = os.open(
            os.path.join(directory, session_id), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
        )
    except (OSError, ValueError):
        return False
    # The mark exists once the create returns, so a failed close does not make the call any less the first.
    with contextlib.suppress(OSError):
        os.close(descriptor)
    return True


def release_first_call(state_dir, session_id):
    """Removes a mark whose log line was not written, so the conversation's next call is the first."""
    with contextlib.suppress(OSError, ValueError):
        os.unlink(os.path.join(state_dir, FIRST_CALL_DIR_NAME, session_id))


def usable_state_dir(path):
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except (OSError, ValueError):
        return False


def error_name(error):
    """An error's closed-vocabulary name: its errno symbol, else `errno:<number>`, else its class name."""
    number = getattr(error, "errno", None)
    if isinstance(number, int):
        return errno.errorcode.get(number, f"errno:{number}")
    return type(error).__name__


def signal_name(number):
    try:
        return signal.Signals(number).name
    except ValueError:
        return f"signal:{number}"


def shell_quote(text):
    return "'" + text.replace("'", "'\\''") + "'"


def pin_line(directory):
    return f"builtin cd -- {shell_quote(directory)} || exit 1"


def path_within(root, target):
    """Both must be canonical absolute paths."""
    return root == "/" or target == root or target.startswith(root + "/")


def jq_alternative(value, fallback):
    """jq's `//`: `fallback` when `value` is null or false."""
    return fallback if value is None or value is False else value


def matcher_selects(pattern, name):
    """True when the compiled Claude matcher selects `name`: a whole-name, ASCII, case-sensitive match."""
    return pattern is None or pattern.fullmatch(name) is not None


def compile_matcher(text):
    """The compiled matcher, or None for an entry with no Claude matcher. Raises re.error for an invalid one."""
    if text is None or text in ("", "*"):
        return None
    return re.compile("(?:" + text + ")", re.ASCII)


def deny_text(label, outcome, detail):
    """The one text a shim-authored deny carries in both message fields."""
    return (
        f"Blocked by the Cursor adapter shim (hook {label}, outcome {outcome}): {detail} "
        f"See the Recovery section of docs/cursor-adapter.md in the claude-config repository. {RECOVERY_SENTENCE}"
    )


def deny_document(event, text):
    if event in DENY_SHAPE_EVENTS:
        return {"permission": "deny", "user_message": text, "agent_message": text}
    if event == "beforeSubmitPrompt":
        return {"continue": False, "user_message": text}
    return {}


def deny_event_for(call):
    """The Cursor event whose output shape a shim-authored deny takes, before the payload names its own."""
    if call.cursor_event:
        return call.cursor_event
    if call.mode == "pin-check":
        return "beforeShellExecution"
    if call.mode in ("pin", "classify") or call.claude_event == "PreToolUse":
        return "preToolUse"
    return {
        "UserPromptSubmit": "beforeSubmitPrompt",
        "SubagentStart": "subagentStart",
        "PostToolUse": "other",
        "SessionStart": "other",
        "SessionEnd": "other",
        "SubagentStop": "other",
        "Stop": "other",
        "PreCompact": "other",
    }.get(call.claude_event, "preToolUse")


def serialize(document):
    """The document's bytes, ASCII-only JSON that a hook's parser reads as the shim decided, with a newline."""
    return json.dumps(document, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii") + b"\n"


def write_all(descriptor, data):
    view = memoryview(data)
    while len(view):
        written = os.write(descriptor, view)
        view = view[written:]


# --- strict payload parsing ---


def _reject_constant(name):
    raise ValueError(f"constant {name}")


def _parse_finite_float(text):
    value = float(text)
    if value in (float("inf"), float("-inf")):
        raise ValueError("float out of range")
    return value


def _holds_lone_surrogate(value):
    """True when any string or key in the parsed document cannot be encoded as UTF-8. Walks with a stack, not recursion."""
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            try:
                node.encode("utf-8")
            except UnicodeEncodeError:
                return True
        elif isinstance(node, dict):
            stack.extend(node.keys())
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return False


def parse_strict_json(data):
    """Parses UTF-8 JSON, refusing NaN, Infinity, a float past the range, a lone surrogate and nesting past the limit.

    Raises ValueError for any of them, so the caller maps one exception class to the payload-malformed outcome."""
    try:
        text = data.decode("utf-8") if isinstance(data, bytes | bytearray) else data
        value = json.loads(text, parse_constant=_reject_constant, parse_float=_parse_finite_float)
    except (ValueError, RecursionError) as error:
        raise ValueError("not strict JSON") from error
    if _holds_lone_surrogate(value):
        raise ValueError("lone surrogate")
    return value


# --- install record, field table, parity exceptions ---


def shim_directory():
    return os.path.dirname(os.path.abspath(__file__))


def read_json_file(path):
    with open(path, "rb") as handle:
        return parse_strict_json(handle.read())


def load_field_table():
    return read_json_file(os.path.join(shim_directory(), FIELD_TABLE_FILE_NAME))


def load_parity_exceptions():
    return read_json_file(os.path.join(shim_directory(), PARITY_EXCEPTIONS_FILE_NAME))


def install_record_path():
    home = os.environ.get("HOME", "")
    return os.path.join(home, INSTALL_RECORD_RELATIVE_PATH)


def _tool_map_is_usable(tool_map):
    if not isinstance(tool_map, dict):
        return False
    tools = tool_map.get("tools")
    if not isinstance(tools, dict):
        return False
    for name in TRANSLATED_TOOLS:
        entry = tools.get(name)
        if not isinstance(entry, dict) or not isinstance(entry.get("claude"), str):
            return False
    if not all(isinstance(entry, dict) and entry.get("translation") in TRANSLATION_VOCABULARY for entry in tools.values()):
        return False
    prefix_classes = tool_map.get("prefix_classes", [])
    return isinstance(prefix_classes, list) and all(
        isinstance(item, dict)
        and isinstance(item.get("prefix"), str)
        and item["prefix"] != ""
        and item.get("translation") in TRANSLATION_VOCABULARY
        for item in prefix_classes
    )


def read_install_record(call, require_wrapper=False):
    """Reads and validates the install record. Raises Refusal with the install-record or stale-install outcome.

    Sets `call.state_dir` once the record names a usable one. The contract version is judged before the rest of the
    record, so a record of an older contract that lacks a newer key is stale, not malformed. A record that names neither
    `timeout` nor `gtimeout` is unusable for a hook run, which needs the wrapper."""
    unusable = Refusal("install-record", "The adapter's install record is missing, unreadable or malformed.")
    try:
        record = read_json_file(install_record_path())
    except (OSError, ValueError) as error:
        raise unusable from error
    if not isinstance(record, dict):
        raise unusable
    routed = record.get("routed_config_dir")
    if not isinstance(routed, str) or not routed.startswith("/") or "\n" in routed or "\0" in routed:
        raise unusable
    version = record.get("contract_version")
    if isinstance(version, bool) or not isinstance(version, str | int) or str(version) in ("", "\n"):
        raise unusable
    state_dir = os.path.join(routed, STATE_DIR_NAME)
    if str(version) != CONTRACT_VERSION:
        call.state_dir = state_dir if usable_state_dir(state_dir) else ""
        raise Refusal("stale-install", STALE_INSTALL_DETAIL)
    tool_paths = record.get("tool_paths")
    if tool_paths is None:
        tool_paths = {}
    if (
        not _tool_map_is_usable(record.get("tool_map"))
        or not isinstance(tool_paths, dict)
        or not all(is_valid_tool_path(path) for path in tool_paths.values())
        or (require_wrapper and recorded_wrapper({"tool_paths": tool_paths}) is None)
    ):
        raise unusable
    call.state_dir = state_dir if usable_state_dir(state_dir) else ""
    return record


def hook_environment(record):
    """The hook's environment: the shim's own, minus the Claude process markers, with the recorded PATH and config dir."""
    environment = {name: value for name, value in os.environ.items() if name not in CLAUDE_PROCESS_ENV_NAMES}
    directories = []
    for path in (record.get("tool_paths") or {}).values():
        directory = os.path.dirname(path)
        if directory not in directories:
            directories.append(directory)
    environment["PATH"] = ":".join([*directories, *SYSTEM_PATH_DIRECTORIES])
    environment["CLAUDE_CONFIG_DIR"] = record["routed_config_dir"]
    return environment


def recorded_wrapper(record):
    tool_paths = record.get("tool_paths") or {}
    return tool_paths.get("timeout") or tool_paths.get("gtimeout")


# --- arguments ---


def _is_valid_deadline(text):
    if _DEADLINE_RE.fullmatch(text) is None or text.startswith("0"):
        return False
    return MIN_DEADLINE_SECONDS <= int(text) <= MAX_DEADLINE_SECONDS


def parse_arguments(argv):
    """The parsed command line, or None when it holds anything this shim does not accept, abbreviations included."""
    values = {}
    index = 0
    while index < len(argv):
        flag = argv[index]
        if flag in SWITCH_FLAGS:
            values["fail_closed"] = flag == "--fail-closed"
            index += 1
        elif flag in VALUE_FLAGS:
            if index + 1 >= len(argv):
                return None
            values[flag] = argv[index + 1]
            index += 2
        else:
            return None
    if values.get("--claude-event", "") == "" or values.get("--mode", "hook") not in MODES:
        return None
    if "--deadline" in values and not _is_valid_deadline(values["--deadline"]):
        return None
    if values.get("--mode", "hook") == "hook" and values.get("--script", "") == "":
        return None
    arguments = Arguments(values)
    try:
        arguments.matcher_pattern = compile_matcher(arguments.matcher)
    except re.error:
        return None
    if arguments.label == "":
        arguments.label = os.path.basename(arguments.script).removesuffix(".sh")
    if _LOG_VALUE_RE.fullmatch(arguments.label) is None:
        arguments.label = "unlabeled"
    return arguments


# --- reading the payload ---


def read_payload(deadline_at):
    """Reads stdin into memory under the payload cap and the deadline.

    Raises Refusal on a failed read, a stream held open past the deadline, or an overflow."""
    chunks = []
    total = 0
    while True:
        remaining = deadline_at - time.monotonic()
        if remaining <= 0:
            raise Refusal("timeout", "Cursor held its payload stream open past the shim's deadline.")
        try:
            ready, _, _ = select.select([0], [], [], remaining)
            if not ready:
                continue
            chunk = os.read(0, IO_CHUNK_BYTES)
        except OSError as error:
            raise Refusal("stdin-read", "Reading Cursor's payload from stdin failed.") from error
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > PAYLOAD_CAP_BYTES:
            raise Refusal(
                "payload-over-cap",
                f"The Cursor payload is larger than the shim's {PAYLOAD_CAP_BYTES} byte cap. The shim never truncates a payload.",
            )
        chunks.append(chunk)


# --- repo resolution ---


class Roots:
    """The workspace roots of one call, and the directory rules built on them. Canonical forms are computed once."""

    def __init__(self, raw_roots, home):
        self.raw = list(raw_roots)
        self.home = home
        self._memo = {}
        self.canonical = []
        for root in self.raw:
            if root.startswith("/"):
                self.canonical.append(self.canonicalize(root))

    def canonicalize(self, path):
        """The path through os.path.realpath's non-strict mode, which resolves the links in the part that exists."""
        if path not in self._memo:
            self._memo[path] = os.path.realpath(path)
        return self._memo[path]

    def root_containing(self, canonical):
        best = None
        for root in self.canonical:
            if path_within(root, canonical) and (best is None or len(root) > len(best)):
                best = root
        return best

    def is_workspace_root(self, directory):
        return directory in self.raw or directory in self.canonical

    def shell_pin_directory(self, original):
        """The directory the pin gives a Shell command, or None when no absolute directory is known.

        The default is the first workspace root, or $HOME with none.
        A command whose leading `cd <path> &&` leading_cd_candidate recognizes gets the path instead when all of these hold:
        - the path is absolute and holds no NUL, `$`, `~` or backtick;
        - the path is an existing directory;
        - the path is a workspace root or lies inside one, by the containment test on its realpath form.

        The test runs no git and no other program."""
        first = self.raw[0] if self.raw else self.home
        if not first.startswith("/") or "\0" in first:
            return None
        candidate = leading_cd_candidate(original)
        if candidate is not None and self._exception_directory_ok(candidate):
            return self.canonicalize(candidate)
        return self.canonicalize(first)

    def _exception_directory_ok(self, candidate):
        if not candidate.startswith("/") or any(mark in candidate for mark in ("$", "~", "`", "\0")):
            return False
        return os.path.isdir(candidate) and self.root_containing(self.canonicalize(candidate)) is not None

    def file_tool_directory(self, target):
        """The directory a file tool's hook starts in: the longest root containing the target, else the first root,
        else the target's nearest existing ancestor. A file tool never fails for resolution."""
        if "\0" in target:
            return self.canonical[0] if self.canonical else (self.home if self.home.startswith("/") else "/")
        # A relative target resolves against the first canonical root here, while the hook is handed the path as written.
        # Cursor sends absolute targets.
        if not target.startswith("/"):
            base = self.canonical[0] if self.canonical else (self.home or "/")
            target = base.rstrip("/") + "/" + target
        try:
            canonical = self.canonicalize(target)
        except (OSError, ValueError):
            canonical = target
        root = self.root_containing(canonical)
        if root is not None:
            return root
        if self.canonical:
            return self.canonical[0]
        ancestor = canonical
        while not os.path.isdir(ancestor) and ancestor not in ("/", ".", ""):
            ancestor = os.path.dirname(ancestor)
        return ancestor or "/"


def leading_cd_candidate(command):
    """The path of a leading `cd <path> &&`, quotes removed, or None. The syntax is ASCII; the path may be non-ASCII.

    A double-quoted path holding any backslash gives None.
    A shell reads a backslash before a newline, a double quote or a backslash as an escape, not as those two characters,
    so the path read literally can differ from the shell's path."""
    match = _LEADING_CD_RE.match(command)
    if match is None:
        return None
    if match.group(1) is not None and "\\" in match.group(1):
        return None
    for group in (match.group(3), match.group(2), match.group(1)):
        if group:
            return group
    return None


def recognize_pin(command, roots):
    """Returns (the agent's command, whether `command` started with exactly the pin prefix its own remainder would get).

    Tries each newline as the end of the pin line, earliest first.
    A second fire over a prefixed command leaves it alone.
    A lookalike pin line naming another directory is agent text."""
    if not command.startswith(PIN_LINE_HEAD):
        return command, False
    position = -1
    for _ in range(PIN_MAX_NEWLINES):
        position = command.find("\n", position + 1)
        if position < 0:
            break
        head = command[:position]
        if not head.endswith(PIN_LINE_TAIL):
            continue
        rest = command[position + 1 :]
        directory = roots.shell_pin_directory(rest)
        if directory is not None and head == pin_line(directory):
            return rest, True
    return command, False


def unenterable_refusal(directory, roots):
    """The unenterable-directory refusal. Only a workspace root is named, because a Shell call's `cd` target is
    agent-chosen text and the deny carries the shim's authority."""
    subject = f"The directory {directory}" if roots is not None and roots.is_workspace_root(directory) else "The hook's directory"
    return Refusal(
        "unenterable-dir",
        f"{subject} does not exist or cannot be searched. "
        "The engineer restores the folder or its permissions, or starts a new conversation on a folder that exists.",
    )


def require_enterable(directory, roots):
    try:
        enterable = os.path.isdir(directory) and os.access(directory, os.X_OK)
    except ValueError:
        enterable = False
    if not enterable:
        raise unenterable_refusal(directory, roots)


# --- translation ---


class Facts:
    """What the shim learned about one payload before any filesystem question."""

    def __init__(self):
        self.event = ""
        self.event_spec = None
        self.claude_event_name = ""
        self.cursor_tool = ""
        self.tool_class = "none"
        self.tool = None
        self.session_id = ""
        self.command = ""
        self.cwd_state = "none"
        self.cwd_value = ""
        self.roots = []
        self.paths = []
        self.event_status = ""
        self.loop_count = 0
        self.skip = False


def dotted(path):
    return ".".join(str(part) for part in path)


def safe_get(value, path):
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


_JSON_TYPES = MappingProxyType(
    {
        "string": lambda value: isinstance(value, str),
        "object": lambda value: isinstance(value, dict),
        "array": lambda value: isinstance(value, list),
        "boolean": lambda value: isinstance(value, bool),
        "number": lambda value: isinstance(value, int | float) and not isinstance(value, bool),
    }
)


def type_ok(value, wanted):
    return wanted is None or _JSON_TYPES[wanted](value)


def missing_required(paths, base, label_prefix):
    for required in paths:
        value = safe_get(base, required["path"])
        if value is None or not type_ok(value, required.get("type")):
            return label_prefix + dotted(required["path"])
    return None


def resolve_tool(name, record, parity):
    """{kind: mapped | mcp | listed | unknown | absent, claude, aliases, translation}.

    `parity` is a callable returning the parity exceptions, read only when the name is in neither map."""
    if name is None:
        return {"kind": "absent"}
    if not isinstance(name, str):
        return {"kind": "unknown"}
    tool_map = record["tool_map"]
    entry = tool_map["tools"].get(name)
    if entry is not None:
        return {
            "kind": "mapped",
            "claude": entry["claude"],
            "aliases": entry.get("claude_aliases", []),
            "translation": entry["translation"],
        }
    for prefix_class in tool_map.get("prefix_classes", []):
        if name.startswith(prefix_class["prefix"]):
            return {"kind": "mcp", "claude": name, "aliases": [], "translation": prefix_class["translation"]}
    if parity is not None and any(
        item.get("kind") == "cursor-tool" and item.get("key") == name for item in parity().get("entries", [])
    ):
        return {"kind": "listed"}
    return {"kind": "unknown"}


def event_tool_name(event_spec, payload):
    tool = event_spec.get("tool")
    if tool is None:
        return None
    if tool.get("synthetic") is not None:
        return tool["synthetic"]
    if tool.get("from") is not None:
        return safe_get(payload, tool["from"])
    return None


def match_targets(claude_event, tool, payload, overwrite_names):
    """The Claude names the entry's matcher is tested against, per Claude event."""
    if claude_event in ("PreToolUse", "PostToolUse"):
        if tool["kind"] == "mapped":
            return [tool["claude"], *tool["aliases"], *overwrite_names]
        if tool["kind"] == "mcp":
            return [tool["claude"]]
        return []
    if claude_event == "SessionStart":
        return ["startup"]
    if claude_event in ("SubagentStart", "SubagentStop"):
        subagent_type = jq_alternative(payload.get("subagent_type"), "")
        return [subagent_type if isinstance(subagent_type, str) else ""]
    return [""]


def session_id_of(payload):
    """The call's session id: parent_agent_id when it is carried, else conversation_id. Raises Refusal for a bad one."""
    parent = payload.get("parent_agent_id")
    if parent is None or (isinstance(parent, str) and parent == ""):
        if is_valid_session_id(payload.get("conversation_id")):
            return payload["conversation_id"]
        field = "conversation_id"
    elif is_valid_session_id(parent):
        return parent
    else:
        field = "parent_agent_id"
    raise Refusal("session-id-grammar", f"The payload field {field} is missing or is not a valid session id.")


def roots_of(payload):
    roots = payload.get("workspace_roots")
    if "workspace_roots" not in payload:
        return []
    if not isinstance(roots, list) or not all(isinstance(root, str) and root != "" and "\0" not in root for root in roots):
        raise Refusal("payload-malformed", "The payload field workspace_roots must be absent or a list of non-empty strings.")
    return roots


def loop_count_of(payload):
    value = payload.get("loop_count")
    if value is None:
        return 0
    if type(value) is int and value >= 0:
        return value
    raise Refusal("payload-malformed", "The payload field loop_count must be absent, null or a non-negative integer.")


def tool_input_of(payload, event):
    if event == "beforeShellExecution":
        return {"command": payload.get("command")}
    if event == "beforeReadFile":
        return {"file_path": payload.get("file_path")}
    tool_input = payload.get("tool_input")
    return tool_input if isinstance(tool_input, dict) else {}


def examine_payload(payload, arguments, record, spec, parity, call=None):
    """The facts phase: validates the payload, resolves the tool and the ids, and decides whether the call is the entry's.

    Returns Facts, with `skip` set when the entry does not handle the call. Raises Refusal for a payload it cannot translate."""
    facts = Facts()
    event = payload.get("hook_event_name")
    if not isinstance(event, str) or event not in spec["events"]:
        raise Refusal("unknown-event", "The Cursor payload names an event the adapter does not translate.")
    event_spec = spec["events"][event]
    facts.event = event
    facts.event_spec = event_spec
    facts.claude_event_name = event_spec["claude_event"]
    facts.cursor_tool = event_tool_name(event_spec, payload)
    if call is not None:
        call.cursor_event = event
        call.tool = facts.cursor_tool if isinstance(facts.cursor_tool, str) else ""
    tool = (
        {"kind": "none"}
        if event_spec.get("tool") is None
        else resolve_tool(facts.cursor_tool, record, parity if arguments.mode == "classify" else None)
    )
    facts.tool = tool
    kind = tool["kind"]
    has_tool = event_spec.get("tool") is not None
    if arguments.mode == "hook" and facts.claude_event_name != arguments.claude_event:
        facts.skip = True
        return facts
    if arguments.mode == "classify" and has_tool and kind not in ("absent", "mapped", "mcp", "listed"):
        raise Refusal(
            "unknown-tool",
            "Cursor sent a tool name the adapter has not classified. The tool is denied until the engineer classifies it.",
        )
    if arguments.mode == "classify" and kind != "absent":
        facts.tool_class = "none"
        return facts
    if arguments.mode == "hook" and has_tool and kind not in ("absent", "mapped", "mcp"):
        facts.skip = True
        return facts
    if (
        arguments.mode == "hook"
        and kind != "absent"
        and arguments.claude_event in MATCHER_EVENTS
        and not any(
            matcher_selects(arguments.matcher_pattern, name)
            for name in match_targets(arguments.claude_event, tool, payload, _overwrite_names(tool))
        )
    ):
        facts.skip = True
        return facts
    missing = (
        missing_required(spec["common_required"], payload, "")
        or missing_required(event_spec["required"], payload, "")
        or (
            missing_required(spec["tool_input_required"][facts.cursor_tool], payload["tool_input"], "tool_input.")
            if kind == "mapped" and event in ("preToolUse", "postToolUse") and facts.cursor_tool in spec["tool_input_required"]
            else None
        )
    )
    if missing is not None:
        raise Refusal("missing-key", f"The Cursor payload lacks the required key path {missing}, so it cannot be translated.")
    if kind == "mapped" and event in ("preToolUse", "postToolUse") and facts.cursor_tool not in spec["tool_input_required"]:
        raise Refusal(
            "tool-table-gap",
            "The field table lists no required key paths for a tool the install record maps, so the call cannot be checked.",
        )
    facts.session_id = session_id_of(payload)
    if call is not None:
        call.session_id = facts.session_id
    facts.roots = roots_of(payload)
    facts.loop_count = loop_count_of(payload)
    tool_input = tool_input_of(payload, event)
    translation = tool.get("translation", "none")
    facts.tool_class = translation
    if translation == "shell":
        facts.command = jq_alternative(tool_input.get("command"), "")
        if not isinstance(facts.command, str):
            facts.command = ""
    if translation in ("read", "write", "delete"):
        target = jq_alternative(tool_input.get("file_path"), None)
        facts.paths = [target] if isinstance(target, str) else []
    elif translation == "grep":
        target = jq_alternative(tool_input.get("file_path"), tool_input.get("path"))
        facts.paths = [target] if isinstance(target, str) else []
    if translation == "shell" and event == "preToolUse" and "cwd" in tool_input:
        cwd = tool_input["cwd"]
        if cwd is None or cwd == "":
            facts.cwd_state = "none"
        elif isinstance(cwd, str):
            facts.cwd_state, facts.cwd_value = "string", cwd
        else:
            facts.cwd_state = "other"
    status = payload.get("status")
    facts.event_status = status if isinstance(status, str) else ""
    return facts


def _overwrite_names(tool):
    """The extra names a preToolUse Write may carry, so the early selection answers for both before the file is seen."""
    return ["Edit"] if tool.get("translation") == "write" else []


def write_overwrites(facts, arguments):
    """True when a preToolUse Write is an overwrite that goes to the hooks as an `Edit`.

    The target must be a regular file by os.stat, following links, and the entry's Claude matcher must select `Edit`.
    A failed stat, a target that is not absolute and an entry with no matcher keep the name `Write`. The shim opens no file."""
    if facts.tool_class != "write" or facts.event != "preToolUse" or arguments.matcher_pattern is None:
        return False
    if not facts.paths or not facts.paths[0].startswith("/"):
        return False
    if not matcher_selects(arguments.matcher_pattern, "Edit"):
        return False
    try:
        return stat.S_ISREG(os.stat(facts.paths[0]).st_mode)
    except (OSError, ValueError):
        return False


def translated_tool_input(payload, facts, claude_name, original_command):
    tool_input = tool_input_of(payload, facts.event)
    translation = facts.tool_class
    if translation == "shell":
        result = {"command": original_command}
        if tool_input.get("description") is not None:
            result["description"] = tool_input["description"]
        return result
    if translation == "read":
        result = {"file_path": tool_input.get("file_path")}
        result.update({key: value for key, value in tool_input.items() if key in ("offset", "limit")})
        return result
    if translation == "grep":
        result = {"pattern": tool_input.get("pattern")}
        path = jq_alternative(tool_input.get("file_path"), tool_input.get("path"))
        if path is not None:
            result["path"] = path
        result.update({key: value for key, value in tool_input.items() if key in ("glob", "output_mode")})
        return result
    if translation == "write":
        return {"file_path": tool_input.get("file_path"), "content": tool_input.get("content")}
    if translation == "delete":
        return {"command": "rm -- " + shell_quote(tool_input.get("file_path"))}
    if translation == "task":
        result = {"prompt": tool_input.get("prompt"), "subagent_type": tool_input.get("subagent_type")}
        result.update({key: value for key, value in tool_input.items() if key in ("description", "model", "run_in_background")})
        return result
    return tool_input


def tool_response_from_output(output):
    """Only JSON text that parses to an object or array replaces the string. Plain text and scalar JSON keep the text."""
    if not isinstance(output, str):
        return output
    try:
        parsed = parse_strict_json(output)
    except ValueError:
        return output
    return parsed if isinstance(parsed, dict | list) else output


def build_claude_payload(payload, facts, spec, claude_name, directory, original_command):
    """The Claude Code payload a harness hook reads, from the field table."""
    result = {}
    for field in [*spec["common_fields"], *facts.event_spec["fields"]]:
        rule = field.get("rule")
        claude = field["claude"]
        if rule == "absent":
            continue
        if rule == "claude_event_name":
            result[claude] = facts.claude_event_name
        elif rule == "call_session_id":
            result[claude] = facts.session_id
        elif rule == "resolved_directory":
            result[claude] = directory
        elif rule == "claude_tool_name":
            result[claude] = claude_name
        elif rule == "translated_tool_input":
            result[claude] = translated_tool_input(payload, facts, claude_name, original_command)
        elif rule == "tool_response_from_output":
            result[claude] = tool_response_from_output(payload.get("tool_output"))
        elif field.get("const") is not None:
            result[claude] = field["const"]
        elif field.get("from") is not None:
            value = safe_get(payload, field["from"])
            if value is not None and type_ok(value, field.get("type")):
                result[claude] = value
    for dropped in spec["dropped_keys"]:
        result.pop(dropped, None)
    return result


# --- the output table ---


def _is_known(value, kind):
    if kind == "any":
        return True
    return type_ok(value, kind)


KNOWN_TOP_KEYS = MappingProxyType(
    {
        "hookSpecificOutput": "object",
        "decision": "string",
        "reason": "string",
        "systemMessage": "string",
        "continue": "boolean",
        "stopReason": "string",
        "suppressOutput": "boolean",
    }
)
KNOWN_SPECIFIC_KEYS = MappingProxyType(
    {
        "hookEventName": "string",
        "permissionDecision": "string",
        "permissionDecisionReason": "string",
        "additionalContext": "string",
        "updatedToolOutput": "any",
        "sessionTitle": "string",
    }
)


def _off_table(mapping, known):
    """True when `mapping` holds a key outside `known`, or a known key of another type. A null value counts as absent."""
    return any(key not in known or (value is not None and not _is_known(value, known[key])) for key, value in mapping.items())


def carries_unknown_field(parsed):
    if _off_table(parsed, KNOWN_TOP_KEYS):
        return True
    specific = parsed.get("hookSpecificOutput") or {}
    if _off_table(specific, KNOWN_SPECIFIC_KEYS):
        return True
    if (specific.get("permissionDecision") or "allow") not in ("allow", "deny", "ask"):
        return True
    return (parsed.get("decision") or "block") not in ("block", "approve")


def _ask_in_chat(event, reason):
    subjects = {"beforeSubmitPrompt": "This prompt", "subagentStart": "This subagent start"}
    subject = subjects.get(event, "This call")
    unenforced = "" if event in ("beforeSubmitPrompt", "subagentStart") else ", which Cursor does not enforce for this tool"
    return (
        f"{subject} is refused on every retry: the hook asks for the engineer's approval{unenforced}. "
        "Do not retry it or route around it, even after the engineer replies. The engineer makes the change themselves. "
        f"The hook's message: {reason}"
    )


def _followup(meta, reason):
    # Cursor submits a follow-up as the engineer's next turn, so the text says a hook sent it.
    # The reason is quoted as an ASCII-only JSON string, so text a hook echoes cannot add a line, a quote, a Unicode line
    # separator or a bidi control that reads as the engineer's.
    # ASCII quoting expands each non-ASCII character to 6 characters (12 outside the BMP), and HOOK_REASON_CAP_CHARS
    # bounds the reason before quoting, so the quoted text can reach 12 times the cap.
    quoted = json.dumps(reason, ensure_ascii=True)
    return {
        "followup_message": f"Automated message from the {meta['hook']} hook, not from the engineer. "
        f"The hook's text, quoted as a JSON string: {quoted}"
    }


def _followup_allowed(meta):
    return meta["status"] == "completed" and (meta["event"] != "stop" or meta["loop_count"] == 0)


def abnormal_output(meta, outcome, detail):
    """An outcome that is neither a parsed decision nor a no-opinion: a deny on a fail-closed entry, `{}` otherwise."""
    output = deny_document(meta["event"], deny_text(meta["hook"], outcome, detail)) if meta["fail_closed"] else {}
    return {"out": output, "outcome": outcome, "dropped": 0}


def _map_exit_two(meta):
    event = meta["event"]
    stderr = meta["stderr"].strip(_WHITESPACE)
    text = stderr or f"The {meta['hook']} hook blocked this call with exit 2 and no message."
    if event in DENY_SHAPE_EVENTS or event == "beforeSubmitPrompt":
        return {"out": deny_document(event, text), "outcome": "exit-2", "dropped": 0}
    if event == "postToolUse":
        return {"out": {"additional_context": text}, "outcome": "exit-2", "dropped": 0}
    if event == "stop":
        if _followup_allowed(meta):
            return {"out": _followup(meta, text), "outcome": "exit-2", "dropped": 0}
        return {"out": {}, "outcome": "exit-2-skipped", "dropped": 0}
    return {"out": {}, "outcome": "exit-2-ignored", "dropped": 0}


def _map_parsed_decision(meta, parsed):
    event = meta["event"]
    specific = parsed.get("hookSpecificOutput") or {}
    permission = specific.get("permissionDecision")
    legacy = parsed.get("decision")
    reason_raw = specific.get("permissionDecisionReason")
    if reason_raw is None:
        reason_raw = parsed.get("reason")
    reason_raw = (reason_raw if reason_raw is not None else "")[: meta.get("reason_cap", HOOK_REASON_CAP_CHARS)]
    hook = meta["hook"]
    reason = reason_raw or f"The {hook} hook denied this call without giving a reason."
    ask_reason = reason_raw or f"The {hook} hook asked for approval without giving a reason."
    context = specific.get("additionalContext")
    updated_output = specific.get("updatedToolOutput")
    stopped = parsed.get("continue") is False
    blocked = permission == "deny" or legacy == "block"
    denied = blocked or stopped
    asked = permission == "ask"
    # A deny, block or ask on an event with no channel for it is counted, so the log can tell a lost block from an allow.
    lost_block = int(blocked or asked)
    lost_permission = int(permission == "deny" or asked)
    # Fields with no Cursor channel on any event are counted, never delivered.
    always_dropped = sum(
        value is not None
        for value in (
            parsed.get("systemMessage"),
            parsed.get("stopReason"),
            parsed.get("suppressOutput"),
            specific.get("sessionTitle"),
        )
    )
    # `continue: false` halts under Claude Code, so events without a halt channel count it as dropped.
    stopped_dropped = int(stopped)
    context_count = int(context is not None)
    if event in PRE_CALL_EVENTS:
        if denied:
            result = {"out": deny_document(event, reason), "outcome": "deny"}
        elif asked and event == "beforeShellExecution":
            result = {
                "out": {"permission": "ask", "user_message": ask_reason, "agent_message": ask_reason},
                "outcome": "ask",
            }
        elif asked:
            result = {"out": deny_document(event, _ask_in_chat(event, ask_reason)), "outcome": "ask-in-chat"}
        else:
            result = {"out": {}, "outcome": "ok"}
        result["dropped"] = always_dropped + context_count + int(updated_output is not None)
    elif event == "beforeSubmitPrompt" or event == "subagentStart":
        if denied:
            result = {"out": deny_document(event, reason), "outcome": "deny"}
        elif asked:
            result = {"out": deny_document(event, _ask_in_chat(event, ask_reason)), "outcome": "ask-in-chat"}
        else:
            result = {"out": {}, "outcome": "ok"}
        result["dropped"] = always_dropped + context_count
    elif event == "postToolUse":
        pieces = [context] if context is not None else []
        if legacy == "block":
            pieces.append(reason)
        joined = "\n\n".join(pieces)
        replace_output = meta["tool_cursor"].startswith("MCP:") and isinstance(updated_output, dict)
        out = {"additional_context": joined} if joined != "" else {}
        if replace_output:
            out["updated_mcp_tool_output"] = updated_output
        result = {
            "out": out,
            "outcome": "context" if joined != "" or replace_output else "ok",
            "dropped": always_dropped
            + stopped_dropped
            + lost_permission
            + int(updated_output is not None and not replace_output),
        }
    elif event in ("stop", "subagentStop"):
        follows_up = legacy == "block" if event == "subagentStop" else blocked
        if stopped:
            result = {"out": {}, "outcome": "ok"}
        elif follows_up and _followup_allowed(meta):
            result = {"out": _followup(meta, reason), "outcome": "followup"}
        elif follows_up:
            result = {"out": {}, "outcome": "followup-skipped"}
        else:
            result = {"out": {}, "outcome": "ok"}
        if event == "stop":
            result["dropped"] = always_dropped + stopped_dropped + int(asked) + context_count
        else:
            result["dropped"] = always_dropped + stopped_dropped + lost_permission + context_count
    elif event == "sessionStart":
        delivered = context is not None and context != ""
        result = {
            "out": {"additional_context": context} if delivered else {},
            "outcome": "context" if delivered else "ok",
            "dropped": always_dropped + stopped_dropped + lost_block,
        }
    else:
        result = {"out": {}, "outcome": "ok", "dropped": always_dropped + stopped_dropped + lost_block + context_count}
    return result


def map_hook_output(meta, stdout):
    """Maps one harness hook's result to Cursor's hook output, total over event, exit class and stdout class.

    `meta` holds hook, event (Cursor's name), fail_closed, exit, stderr (already cut), tool_cursor, status and loop_count,
    and optionally reason_cap. Returns {out, outcome, dropped}. No cell yields a Cursor allow: a hook's allow is `{}`,
    so Cursor's own approval flow decides."""
    exit_status = meta["exit"]
    if exit_status == HOOK_STATUS_BLOCK:
        return _map_exit_two(meta)
    if exit_status != 0:
        return abnormal_output(meta, "exit-nonzero", f"The hook exited with status {exit_status}.")
    text = stdout.strip(_WHITESPACE)
    if text == "":
        return {"out": {}, "outcome": "ok", "dropped": 0}
    try:
        parsed = parse_strict_json(text)
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict):
        return abnormal_output(meta, "non-json", "The hook printed output that is not a JSON object.")
    if carries_unknown_field(parsed):
        return abnormal_output(meta, "unknown-field", "The hook printed an output field the adapter does not translate.")
    return _map_parsed_decision(meta, parsed)


# --- the hook run ---


def retry_sentence(cause):
    if cause in TRANSIENT_ERRNO_SYMBOLS:
        return "The cause can be transient: retry this call once, and if it is denied again, stop and report this deny in chat."
    return "Do not retry this call."


def start_failed(program, cause):
    return Refusal("start-failed", f"The {program} could not be started (cause {cause}). {retry_sentence(cause)}")


def interpret_status(returncode, deadline_reached):
    """The hook's own exit status, or a Refusal for the wrapper's and the signal outcomes.

    124 and 137 (or death by SIGKILL) at the deadline are GNU timeout's kill, so they take the timeout outcome even when
    the hook exited 0 on the TERM. Before the deadline, 124 is the hook's own status."""
    if returncode == TIMEOUT_STATUS_OWN_FAILURE:
        raise start_failed("wrapper", f"status:{TIMEOUT_STATUS_OWN_FAILURE}")
    killed = returncode in (TIMEOUT_STATUS_TIMED_OUT, TIMEOUT_STATUS_KILLED) or returncode == -signal.SIGKILL
    if killed and deadline_reached:
        raise Refusal("timeout", "The hook did not finish within its deadline.")
    if returncode == TIMEOUT_STATUS_TIMED_OUT:
        return TIMEOUT_STATUS_TIMED_OUT
    if returncode < 0:
        raise Refusal("signal", f"The hook was killed by a signal ({signal_name(-returncode)}).")
    if returncode > SIGNAL_STATUS_BASE:
        raise Refusal("signal", f"The hook was killed by a signal ({signal_name(returncode - SIGNAL_STATUS_BASE)}).")
    return returncode


def _pump(process, payload_bytes, bound_at):
    """The communicate loop with a retention cap: writes the payload, reads stdout and stderr to EOF, within the bound."""
    collected = {"out": bytearray(), "err": bytearray()}
    selector = selectors.DefaultSelector()
    offset = 0
    try:
        if payload_bytes:
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, "in")
        elif process.stdin is not None:
            process.stdin.close()
        selector.register(process.stdout, selectors.EVENT_READ, "out")
        selector.register(process.stderr, selectors.EVENT_READ, "err")
        while selector.get_map():
            remaining = bound_at - time.monotonic()
            if remaining <= 0:
                raise Refusal("timeout", "The hook did not finish within its deadline.")
            for key, _ in selector.select(remaining):
                stream = key.fileobj
                if key.data == "in":
                    try:
                        offset += os.write(stream.fileno(), payload_bytes[offset : offset + IO_CHUNK_BYTES])
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        offset = len(payload_bytes)
                    if offset >= len(payload_bytes):
                        selector.unregister(stream)
                        stream.close()
                    continue
                chunk = os.read(stream.fileno(), IO_CHUNK_BYTES)
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                buffer = collected[key.data]
                if len(buffer) + len(chunk) > HOOK_OUTPUT_CAP_BYTES:
                    name = "stdout" if key.data == "out" else "stderr"
                    raise Refusal(
                        "hook-output-cap", f"The hook's {name} held more than the shim's {HOOK_OUTPUT_CAP_BYTES} byte cap."
                    )
                buffer.extend(chunk)
        remaining = bound_at - time.monotonic()
        try:
            process.wait(timeout=max(remaining, 0.0))
        except subprocess.TimeoutExpired as error:
            raise Refusal("timeout", "The hook did not finish within its deadline.") from error
    finally:
        selector.close()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
    return bytes(collected["out"]), bytes(collected["err"])


def run_wrapped_hook(wrapper, script, payload_bytes, directory, environment, deadline_at, roots=None):
    """Starts `<wrapper> -k <grace> <time left> <script>` in `directory`, feeds it the payload and reads its output.

    Returns (exit status, stdout bytes, stderr bytes). Raises Refusal for every other outcome. The wrapper is the hook's
    parent and, with no `--foreground`, signals the hook's whole process group at the deadline."""
    time_left = deadline_at - time.monotonic()
    if time_left < TIME_LEFT_FLOOR_SECONDS:
        raise Refusal("timeout", "The shim's deadline passed before the hook could start.")
    argv = [wrapper, "-k", str(GRACE_PERIOD_SECONDS), f"{time_left:.3f}", script]
    try:
        process = subprocess.Popen(
            argv,
            cwd=directory,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
    except OSError as error:
        if error.filename == directory:
            raise unenterable_refusal(directory, roots) from error
        raise start_failed("wrapper", error_name(error)) from error
    bound_at = deadline_at + GRACE_PERIOD_SECONDS + DRAIN_BOUND_SECONDS
    stdout, stderr = _pump(process, payload_bytes, bound_at)
    # The clock is read after the drain, so a child outside the hook's group that holds the pipes can relabel an early hook
    # exit as the timeout outcome.
    return interpret_status(process.returncode, time.monotonic() >= deadline_at), stdout, stderr


def check_script(script):
    """Tests the hook script before the call, because the wrapper folds a script's start error into its own 126 or 127."""
    try:
        mode = os.stat(script).st_mode
    except FileNotFoundError as error:
        raise Refusal("script-missing", "The hook script was not found.") from error
    except (OSError, ValueError) as error:
        raise start_failed("hook script", error_name(error)) from error
    if not stat.S_ISREG(mode) or not os.access(script, os.X_OK):
        raise Refusal("script-not-executable", "The hook script is not an executable file.")


# --- modes ---


def decode_text(data):
    return data.decode("utf-8", errors="replace")


def resolve_directory(facts, roots):
    if facts.tool_class == "shell":
        original, _ = recognize_pin(facts.command, roots)
        directory = roots.shell_pin_directory(original)
    elif facts.tool_class in ("read", "write", "delete", "grep") and facts.paths:
        return roots.file_tool_directory(facts.paths[0])
    else:
        directory = roots.shell_pin_directory("")
    if directory is None:
        raise Refusal("no-absolute-dir", NO_ABSOLUTE_DIR_DETAIL)
    return directory


def run_classify(call):
    call.outcome = "classified"
    return {}


def run_pin(call, facts, roots, payload):
    if facts.tool_class != "shell" or facts.event != "preToolUse":
        call.outcome = "not-shell"
        return {}
    original, recognized = recognize_pin(facts.command, roots)
    directory = roots.shell_pin_directory(original)
    if directory is None:
        raise Refusal("no-absolute-dir", NO_ABSOLUTE_DIR_DETAIL)
    if facts.cwd_state == "string":
        accepted = False
        if facts.cwd_value.startswith("/") and "\0" not in facts.cwd_value:
            accepted = roots.canonicalize(facts.cwd_value) == directory
        if not accepted:
            raise Refusal("dir-input-refused", DIRECTORY_INPUT_DETAIL)
    elif facts.cwd_state == "other":
        raise Refusal("dir-input-refused", DIRECTORY_INPUT_DETAIL)
    require_enterable(directory, roots)
    if recognized:
        call.outcome = "pin-unchanged"
        return {}
    call.outcome = "pin"
    updated = dict(payload["tool_input"])
    updated["command"] = pin_line(directory) + "\n" + original
    return {"updated_input": updated}


def run_pin_check(call, facts, roots):
    if facts.tool_class != "shell":
        call.outcome = "not-shell"
        return {}
    _, recognized = recognize_pin(facts.command, roots)
    if recognized:
        call.outcome = "pin-check-ok"
        return {}
    raise Refusal("pin-check", PIN_CHECK_DETAIL)


def run_hook(call, arguments, record, facts, roots, payload, spec, deadline_at):
    directory = resolve_directory(facts, roots)
    require_enterable(directory, roots)
    tool = facts.tool
    original = facts.command
    if facts.tool_class == "shell":
        original, _ = recognize_pin(facts.command, roots)
    overwrites = write_overwrites(facts, arguments)
    claude_name = ("Edit" if overwrites else tool["claude"]) if tool["kind"] in ("mapped", "mcp") else None
    if (
        claude_name is not None
        and arguments.claude_event in ("PreToolUse", "PostToolUse")
        and not any(matcher_selects(arguments.matcher_pattern, name) for name in [claude_name, *tool.get("aliases", [])])
    ):
        call.outcome = "skipped"
        return {}
    claude_payload = build_claude_payload(payload, facts, spec, claude_name, directory, original)
    document = serialize(claude_payload)
    check_script(arguments.script)
    status, stdout, stderr = run_wrapped_hook(
        recorded_wrapper(record), arguments.script, document, directory, hook_environment(record), deadline_at, roots
    )
    call.hook_exit = str(status)
    meta = {
        "hook": arguments.label,
        "event": facts.event,
        "fail_closed": arguments.fail_closed,
        "exit": status,
        "stderr": decode_text(stderr)[:HOOK_REASON_CAP_CHARS],
        "tool_cursor": facts.cursor_tool if isinstance(facts.cursor_tool, str) else "",
        "status": facts.event_status,
        "loop_count": facts.loop_count,
    }
    mapped = map_hook_output(meta, decode_text(stdout))
    call.outcome = mapped["outcome"]
    call.dropped = mapped["dropped"]
    return mapped["out"]


# --- finishing ---


def log_call(call):
    """Logs the call when the state dir is usable. A failure to log never changes the call's decision."""
    if not call.state_dir:
        return
    fields = {
        "ts": int(time.time()),
        "reg": call.label,
        "ev": call.cursor_event,
        "tool": call.tool,
        "out": call.outcome,
        "exit": call.hook_exit,
        "ms": int((time.monotonic() - call.started) * 1000) if call.started is not None else "",
        "drop": call.dropped or "",
        "ver": call.contract_version,
        "size": call.payload_bytes or "",
    }
    # The line that wins the conversation's first-call mark also records the interpreter version. A mark whose line was
    # not written is released, so the pair stays whole.
    claimed = bool(call.session_id) and claim_first_call(call.state_dir, call.session_id)
    if claimed:
        fields["py"] = interpreter_version()
    try:
        write_log_line(call.state_dir, fields)
    except (OSError, ValueError):
        if claimed:
            release_first_call(call.state_dir, call.session_id)


def emit(call, document):
    # The outcome is logged before the document is written, so a failed write adds a second, shim-fault line.
    log_call(call)
    write_all(1, serialize(document))
    return EXIT_OK


def refuse(call, refusal):
    call.outcome = refusal.outcome
    if call.fail_closed:
        document = deny_document(deny_event_for(call), deny_text(call.label, refusal.outcome, refusal.detail))
    else:
        document = {}
    return emit(call, document)


def note_usage(call):
    """Logs a usage line when the record names a usable state dir. The launch guard's tail decides the call."""
    call.outcome = "usage"
    with contextlib.suppress(Refusal, OSError, ValueError):
        read_install_record(call)
    log_call(call)


def serve(call, arguments):
    started = call.started
    record = read_install_record(call, arguments.mode == "hook")
    if arguments.contract_version != CONTRACT_VERSION:
        raise Refusal("stale-install", STALE_INSTALL_DETAIL)
    deadline_at = started + arguments.deadline
    data = read_payload(deadline_at)
    call.payload_bytes = len(data)
    try:
        payload = parse_strict_json(data)
    except ValueError as error:
        raise Refusal("payload-malformed", "The Cursor payload is not valid strict UTF-8 JSON.") from error
    if not isinstance(payload, dict):
        raise Refusal("payload-malformed", "The Cursor payload is not a JSON object.")
    spec = load_field_table()
    facts = examine_payload(payload, arguments, record, spec, load_parity_exceptions, call)
    if facts.skip:
        call.outcome = "skipped"
        return emit(call, {})
    if arguments.mode == "classify":
        return emit(call, run_classify(call))
    roots = Roots(facts.roots, os.environ.get("HOME", ""))
    if arguments.mode == "pin":
        return emit(call, run_pin(call, facts, roots, payload))
    if arguments.mode == "pin-check":
        return emit(call, run_pin_check(call, facts, roots))
    return emit(call, run_hook(call, arguments, record, facts, roots, payload, spec, deadline_at))


def main(argv, call):
    """Runs one call and returns its exit status: 0 once the one output document is written, 1 for the usage outcome."""
    call.started = time.monotonic()
    call.contract_version = CONTRACT_VERSION
    arguments = parse_arguments(argv)
    if arguments is None:
        note_usage(call)
        return EXIT_FAULT
    call.contract_version = arguments.contract_version
    call.label = arguments.label
    call.mode = arguments.mode
    call.claude_event = arguments.claude_event
    call.fail_closed = arguments.fail_closed
    try:
        return serve(call, arguments)
    except Refusal as refusal:
        return refuse(call, refusal)


def run(argv):
    """The top-level handler: a fault of any kind ends the run with no output document, so the launch guard decides."""
    call = CallState()
    try:
        return main(argv, call)
    except BaseException as fault:  # the launch guard's tail needs every fault to end as exit 1
        try:
            call.outcome = "shim-fault"
            if call.state_dir:
                write_log_line(
                    call.state_dir,
                    {
                        "ts": int(time.time()),
                        "reg": call.label,
                        "ev": call.cursor_event,
                        "tool": call.tool,
                        "out": "shim-fault",
                        "ver": call.contract_version,
                        "fault": type(fault).__name__,
                        "py": interpreter_version(),
                    },
                )
        except BaseException:  # nothing may escape the handler
            pass
        return EXIT_FAULT


# --- self-check ---


class SelfCheckMismatch(Exception):
    pass


SELF_CHECK_SHELL = "/bin/sh"
SELF_CHECK_PROGRAM = b"printf out-token; printf err-token >&2; exit 3\n"
SELF_CHECK_TOKENS = (b"out-token", b"err-token")
SELF_CHECK_EXIT_STATUS = 3


def _self_check_payload():
    return {
        "conversation_id": "self-check",
        "hook_event_name": "preToolUse",
        "tool_name": "Shell",
        "tool_input": {"command": "true"},
        "workspace_roots": ["/"],
    }


def _self_check_record():
    return {
        "routed_config_dir": "/",
        "tool_map": {"tools": {name: {"claude": name, "translation": "passthrough"} for name in TRANSLATED_TOOLS}},
    }


def self_check(wrapper):
    """Runs the checks that the installer's `install` and `check` commands (not yet written) run under the recorded python3.
    Returns the failures, one `name: class` each."""
    failures = []

    def attempt(name, step):
        try:
            step()
        except Exception as error:  # the report names the class, never the message
            failures.append(f"{name}: {type(error).__name__}")

    state = {}

    def check_field_table():
        state["spec"] = load_field_table()

    def check_parity_exceptions():
        state["parity"] = load_parity_exceptions()

    def check_translation():
        record = _self_check_record()
        shell_entry = {"claude": "Bash", "translation": "shell"}
        record["tool_map"]["tools"]["Shell"] = shell_entry
        arguments = parse_arguments(["--claude-event", "PreToolUse", "--script", "self-check"])
        payload = _self_check_payload()
        facts = examine_payload(payload, arguments, record, state["spec"], None)
        roots = Roots(facts.roots, "/")
        directory = resolve_directory(facts, roots)
        built = build_claude_payload(payload, facts, state["spec"], facts.tool["claude"], directory, facts.command)
        if built.get("tool_name") != shell_entry["claude"]:
            raise SelfCheckMismatch("translation")

    def check_hook_run():
        deadline_at = time.monotonic() + MIN_DEADLINE_SECONDS
        status, stdout, stderr = run_wrapped_hook(
            wrapper, SELF_CHECK_SHELL, SELF_CHECK_PROGRAM, "/", dict(os.environ), deadline_at
        )
        if status != SELF_CHECK_EXIT_STATUS or (stdout, stderr) != SELF_CHECK_TOKENS:
            raise SelfCheckMismatch("hook-run")

    attempt("field-table", check_field_table)
    attempt("parity-exceptions", check_parity_exceptions)
    if "spec" in state:
        attempt("translation", check_translation)
    attempt("hook-run", check_hook_run)
    return failures


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
