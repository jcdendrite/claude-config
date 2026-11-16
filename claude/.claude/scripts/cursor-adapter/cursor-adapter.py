#!/usr/bin/env python3
"""Generates, installs, checks and removes the Cursor adapter's user-level hook entries.

Usage: cursor-adapter.py generate [--print-entry REGISTRATION --shim PATH] [--python PATH]
       cursor-adapter.py install --config-dir DIR --acceptance-only
       cursor-adapter.py uninstall
       cursor-adapter.py check [--hook-env]
       cursor-adapter.py status
       cursor-adapter.py self-check --wrapper PATH

`install` and `uninstall` change ~/.cursor/hooks.json and the files beside it. docs/cursor-adapter.md describes the
installer, the Recovery steps every shim deny points to, and the limits. The module imports only the standard library, so
the recorded python3 can run it under `-I -S`. Loading it runs no step.
"""

import argparse
import contextlib
import errno
import fcntl
import functools
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import traceback
import types

# --- constants ---

# The toolkit's Python floor. hooks/_config.sh holds the same value, and a test ties the two.
PYTHON_FLOOR = (3, 14)
# The bash floor of hooks/_config.sh, which the hooks' `#!/usr/bin/env bash` needs.
BASH_FLOOR = (5, 2)
# A project choice: how long one version or floor probe may run. The same bound ends every probe in every entry point.
# It equals the 2-second probe cap in claude/.claude/hooks/check-python-floor.sh.
PROBE_BOUND_SECONDS = 2
# A project choice, equal to the timeout of the SessionStart check-python-floor.sh registration in claude/.claude/settings.json.
CHECK_ENTRY_TIMEOUT_SECONDS = 10
# A project choice: a log past this size is renamed to the one older file kept.
LOG_ROTATE_BYTES = 16 * 1024 * 1024
# A project choice: the age past which the sessionStart check removes a conversation's first-call mark.
FIRST_CALL_MARK_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
# A project choice: the most entries the install and uninstall print set lists, and the most lines `status` prints for one kind
# of log-derived finding.
PRINT_SET_MAX_ENTRIES = 20
# A project choice: `status` reads the last this many bytes and lines of each log file, so a planted file cannot make it
# buffer or print without bound.
LOG_STATUS_READ_BYTES = 4 * 1024 * 1024
LOG_STATUS_MAX_LINES = 50_000
# A project choice: the longest escaped value printed for an event key or a field.
PRINT_VALUE_MAX_CHARS = 200
# The most digits one version field of a probe's first output line may hold, as hooks/_config.sh reads it.
PROBE_VERSION_FIELD_MAX_DIGITS = 9

ADAPTER_FILE_NAME = "cursor-adapter.py"
SHIM_FILE_NAME = "hook-shim.py"
TOOL_MAP_FILE_NAME = "tool-map.json"
PARITY_EXCEPTIONS_FILE_NAME = "parity-exceptions.json"
STOW_ADAPTER_RELATIVE_DIR = ".claude/scripts/cursor-adapter"
STOW_HOOKS_RELATIVE_DIR = ".claude/hooks"
RECORD_RELATIVE_DIR = ".cursor/cursor-adapter"
INSTALL_RECORD_NAME = "install.json"
PRE_IMAGE_NAME = "hooks.json.pre-install"
PRE_IMAGE_NONE_NAME = "hooks.json.pre-install.none"
MANIFEST_NAME = "manifest.json"
HOOKS_JSON_NAME = "hooks.json"
STATE_DIR_NAME = "cursor-adapter-state"
LOG_FILE_NAME = "shim.log"
ROTATED_LOG_FILE_NAME = "shim.log.1"
ROTATE_LOCK_NAME = "rotate.lock"
TEMP_FILE_PREFIX = ".adapter-tmp-"
FIRST_CALL_DIR_NAME = "first-call"
GENERATED_FILE_DIRECTORIES = ("agents", "rules")
# What a log-derived or hooks.json-derived value prints as when it is not a known label.
OTHER_LABEL = "other"

# The routed config dir's allowed characters, matched whole. A project choice that keeps the dir a safe shell word.
CONFIG_DIR_PATTERN = re.compile(r"/[A-Za-z0-9._/+@=-]+", re.ASCII)
# The hooks.json vocabulary: Cursor's event names and the keys of one entry.
HOOKS_JSON_EVENTS = frozenset(
    {
        "preToolUse",
        "postToolUse",
        "beforeShellExecution",
        "beforeReadFile",
        "beforeSubmitPrompt",
        "sessionStart",
        "sessionEnd",
        "subagentStart",
        "subagentStop",
        "stop",
        "afterAgentResponse",
        "preCompact",
    }
)
HOOKS_JSON_ENTRY_KEYS = frozenset({"command", "matcher", "timeout", "failClosed", "loop_limit"})
HOOKS_JSON_VERSION = 1

# The harness's tool names, and the Cursor names a matcher over them must select.
CURSOR_TOOL_NAMES = {
    "Bash": ("Shell", "Delete"),
    "Edit": ("Write",),
    "Write": ("Write",),
    "MultiEdit": ("Write",),
    "Read": ("Read",),
    "Agent": ("Task",),
    "Task": ("Task",),
    "Grep": ("Grep",),
    "WebFetch": ("WebFetch",),
    "WebSearch": ("WebSearch",),
}
# The Cursor event each harness event with a Cursor counterpart maps to. Tool events add their tool matcher.
CURSOR_EVENT_FOR_CLAUDE_EVENT = {
    "SessionStart": "sessionStart",
    "SubagentStart": "subagentStart",
    "UserPromptSubmit": "beforeSubmitPrompt",
}
TOOL_EVENTS = {"PreToolUse": "preToolUse", "PostToolUse": "postToolUse"}
# A fail-closed entry's deny document has a shape only on these Cursor events.
FAIL_CLOSED_EVENTS = frozenset({"preToolUse", "beforeShellExecution", "beforeReadFile"})

# PreToolUse hooks on the write tools that answer with an `ask`, which Cursor does not enforce there. The shim turns the ask into
# a deny, so their entries run fail-closed: a fail-open entry prints `{}` when a payload's author makes the payload malformed.
ASK_ON_WRITE_SCRIPTS = frozenset({"ask-review-permissions.sh", "ask-new-dependency-disclosure.sh"})

# The shim's command-line vocabulary. The command grammar below reads it without loading the shim, and a test ties it to the
# shim's own.
SHIM_VALUE_FLAGS = ("--contract-version", "--claude-event", "--mode", "--matcher", "--script", "--label", "--deadline")
SHIM_SWITCH_FLAGS = ("--fail-closed", "--fail-open")
SHIM_MODES = ("hook", "pin", "pin-check", "classify")
CHECK_ARGUMENTS = ("check", "--hook-env")

# The text a fault tail writes to stderr and puts in a fail-closed entry's deny document.
# docs/cursor-adapter.md opens its Recovery section with the last sentence, and a test ties it to the shim's.
FAULT_TEXT = (
    "The Cursor adapter's hook shim ended without deciding this call. "
    "See the Recovery section of docs/cursor-adapter.md in the claude-config repository. "
    "The engineer, not the agent, follows the Recovery section. An agent runs no command from that section, "
    "retries only where this deny's own text says to, and otherwise stops and reports this deny in chat."
)

# Exit status 1 is a refusal that wrote nothing, and argparse owns 2, so a failure after the first write has its own.
PARTIAL_WRITE_EXIT_STATUS = 3

# What `uninstall` looks for to tell an installed adapter from none.
INSTALLED_THINGS_TEXT = "adapter entry, install record, saved pre-install file or manifest"

RUN_MODE_LABEL = "unreadable"
RUN_MODE_ACCEPTANCE_LABEL = "unreadable+acceptance-only"
RUN_MODE_LINE = "run mode not readable; the run-mode precondition in docs/cursor-adapter.md is unchecked"
# docs/hooks.md describes each gate's marker release in that gate's own entry; only the Cursor-specific fact is stated here.
MARKER_GATES_CLAUSE = (
    "The marker-gated gates (see docs/hooks.md) release on a marker file, "
    "and no path inside Cursor that writes one is verified, so under Cursor they can deny operations."
)
MARKER_GATES_WARNING = f"{MARKER_GATES_CLAUSE} Engineer: run `uninstall` from a terminal to back out."
ACCEPTANCE_ONLY_LINE = f"acceptance-only install: {MARKER_GATES_WARNING}"

# The names of `self-check`'s checks: the shim's own four and the subcommand's load check.
SELF_CHECK_NAMES = ("field-table", "parity-exceptions", "translation", "hook-run", "shim-load")
SELF_CHECK_SUBCOMMAND = "self-check"
SELF_CHECK_INTERPRETER_KEYS = ("executable", "version", "isolated", "no_site")

PROBE_PYTHON_PROGRAM = 'import sys; print("%d %d" % sys.version_info[:2])'
QUERY_PYTHON_PROGRAM = "import json, sys; print(json.dumps([sys.executable, sys.prefix, sys.base_prefix]))"
PROBE_BASH_PROGRAM = 'printf "%d %d\\n" "${BASH_VERSINFO[0]}" "${BASH_VERSINFO[1]}"'

# The tools `install` records after python3, in order. `timeout` or `gtimeout` joins at its place, and `gh` only when present.
RECORDED_TOOLS = ("bash", "jq", "git", "ps", "sha256sum")
WRAPPER_CANDIDATES = ("timeout", "gtimeout")
OPTIONAL_TOOLS = ("gh",)

TOOL_PATH_CONDITION_TEXT = (
    "is not absolute or has a `..` component",
    "holds a `:`, newline or NUL",
    "does not name a regular, executable file",
)
TAMPER_FIX_SENTENCE = (
    "To bring the entries back to what the checkout generates, quit Cursor, re-run `install` and relaunch Cursor."
)
SELF_CHECK_FIX_SENTENCE = (
    "The checkout is damaged or half-pulled. Restore it or re-run `./install.sh`, "
    "then quit Cursor, re-run `install` and relaunch."
)
# The adapter's generated paths are all under the bare `$HOME/.claude`, whatever `--config-dir` routes.
STOW_FIX_SENTENCE = (
    "Run `./install.sh` from the claude-config checkout, which stows it under the bare ~/.claude, or restore the file."
)
PATH_FIX_SENTENCE = (
    "Remove that entry from PATH, or put the tool's directory, absolute and without `..`, first on PATH, then re-run `install`."
)
FILE_FIX_SENTENCE = "Restore the file at that path, or re-point the link, then re-run `install`."
PYTHON_EXECUTABLE_FIX_SENTENCE = (
    "Put first on PATH a `python3` whose `sys.executable` meets both conditions, then re-run `install`."
)
REPLACE_WRAPPER_SENTENCE = "Replacing that file or putting another GNU build first on PATH, then re-running `install`, fixes it."
VERSION_FIX_SENTENCE = (
    f"Quit Cursor, set the top-level `version` of hooks.json back to {HOOKS_JSON_VERSION} by hand, then relaunch Cursor."
)
FOREIGN_FIX_SENTENCE = "Quit Cursor, delete each such entry from hooks.json by hand, then relaunch Cursor."
TOP_LEVEL_FIX_SENTENCE = (
    "To save the current top-level members as the new baseline, quit Cursor, run `uninstall`, then re-run `install` "
    "and relaunch Cursor."
)
RESTORE_CHECKOUT_SENTENCE = "Restore the checkout, then re-run `check`."
GENERATE_RESTORE_SENTENCE = "Restore the checkout, then re-run `generate`."
AGENT_RESTORE_SENTENCE = (
    "The engineer, not the agent, restores the checkout, as the Recovery section of docs/cursor-adapter.md describes."
)
# Leads the sessionStart report's findings, which the agent reads, so no finding's fix reads as the agent's to run.
AGENT_RESTRAINT_SENTENCE = (
    "The engineer, not the agent, acts on the findings below. An agent runs no command a finding names "
    "and reports the findings in chat."
)
# The single retry is a project choice.
RERUN_WRAPPER_SENTENCE = (
    "Re-run the command once. If the same outcome repeats, replace that file or put another GNU build first on PATH, "
    "then re-run `install`."
)


class AdapterError(Exception):
    """A refusal or a failure the command reports in one message and ends with exit status 1."""


class PartialWriteError(AdapterError):
    """A failure after the command's first write, so some of its files changed. `main` ends it with its own exit status."""


class DurabilityUnconfirmed(Exception):
    """A change landed, and the fsync of its directory failed, so a power loss could still undo it."""

    def __init__(self, path, cause):
        super().__init__(path)
        self.path = path
        self.cause = cause


class ProbeStalled(Exception):
    """A probe ran past PROBE_BOUND_SECONDS."""

    def __init__(self, path):
        super().__init__(path)
        self.path = path


# --- paths and loading ---


def is_valid_tool_path(value):
    """The shim's tool-path rule: absolute, no `..` component, and no `:`, newline or NUL. A test ties this to the shim's,
    which is not loaded here because `uninstall` must find its files when the shim does not load."""
    return (
        isinstance(value, str)
        and value.startswith("/")
        and ".." not in value.split("/")
        and not any(character in value for character in (":", "\n", "\0"))
    )


def home_directory():
    home = os.environ.get("HOME", "")
    if not is_valid_tool_path(home):
        raise AdapterError(
            "HOME is unset, not absolute, or holds a `..` component, `:`, newline or NUL, so the adapter cannot place its "
            "generated paths. Run it with a plain absolute HOME."
        )
    return home


def cursor_directory():
    return os.path.join(home_directory(), ".cursor")


def record_directory():
    return os.path.join(home_directory(), RECORD_RELATIVE_DIR)


def install_record_path():
    return os.path.join(record_directory(), INSTALL_RECORD_NAME)


def probe_directory():
    """`~/.cursor/`, where Cursor runs user hooks from. A host that never ran Cursor has none, so HOME stands in."""
    directory = cursor_directory()
    return directory if os.path.isdir(directory) else home_directory()


def adapter_directory():
    return os.path.dirname(os.path.realpath(__file__))


def checkout_claude_directory():
    return os.path.dirname(os.path.dirname(adapter_directory()))


def settings_json_path():
    return os.path.join(checkout_claude_directory(), "settings.json")


def stow_shim_path():
    return os.path.join(home_directory(), STOW_ADAPTER_RELATIVE_DIR, SHIM_FILE_NAME)


def stow_adapter_path():
    return os.path.join(home_directory(), STOW_ADAPTER_RELATIVE_DIR, ADAPTER_FILE_NAME)


def stow_hook_path(script):
    return os.path.join(home_directory(), STOW_HOOKS_RELATIVE_DIR, script)


def open_regular_file(path, extra_flags=0):
    """A read-only descriptor for the regular file at `path`. Opens without blocking, so a planted FIFO cannot hang the
    caller, and raises OSError for any file that is not regular."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | extra_flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError(errno.EINVAL, "not a regular file", str(path))
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def read_regular_bytes(path):
    """The bytes of the regular file at `path`. Raises OSError, FileNotFoundError among them, for any other kind of file."""
    with os.fdopen(open_regular_file(path), "rb") as handle:
        return handle.read()


def compile_module(name, path):
    """Compiles the file's source into a fresh module, so no bytecode file is written."""
    module = types.ModuleType(name)
    module.__file__ = str(path)
    source = read_regular_bytes(path)
    exec(compile(source, str(path), "exec"), module.__dict__)  # loading the shim is this loader's purpose
    return module


# What a guarded shim load or call can raise: a module body may call `sys.exit`.
LOAD_FAILURES = (Exception, SystemExit)

_shim_modules = {}


def load_shim(path=None):
    """The shim module, loaded once per path. The default is the file beside this one."""
    shim_path = str(path) if path is not None else os.path.join(adapter_directory(), SHIM_FILE_NAME)
    if shim_path not in _shim_modules:
        _shim_modules[shim_path] = compile_module("cursor_hook_shim", shim_path)
    return _shim_modules[shim_path]


# --- files ---


def parse_json_bytes(data):
    """The shim's strict parse of `data`, so this file and the shim agree on what a usable JSON file is. Raises ValueError.

    A shim that does not load leaves the lenient parse, because `uninstall` must still read `hooks.json` then."""
    try:
        parse = load_shim().parse_strict_json
    except LOAD_FAILURES:
        try:
            return json.loads(data.decode("utf-8"))
        except RecursionError as error:
            raise ValueError("nesting past the limit") from error
    return parse(data)


def read_json_object(path):
    """The JSON object at `path`. Raises AdapterError naming the file when it is missing, unreadable or not an object."""
    try:
        value = parse_json_bytes(read_regular_bytes(path))
    except FileNotFoundError as error:
        raise AdapterError(f"{path} does not exist.") from error
    except (OSError, ValueError) as error:
        raise AdapterError(f"{path} cannot be read as JSON ({type(error).__name__}).") from error
    if not isinstance(value, dict):
        raise AdapterError(f"{path} is not a JSON object.")
    return value


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def flush_to_disk(descriptor):
    """Asks the drive to flush its cache where the platform has `fcntl.F_FULLFSYNC`, which macOS fsync(2) and fcntl(2)
    describe as a flush past the drive's cache. A refused or failed full flush falls back to `os.fsync`."""
    full_fsync = getattr(fcntl, "F_FULLFSYNC", None)
    if full_fsync is not None:
        with contextlib.suppress(OSError):
            fcntl.fcntl(descriptor, full_fsync)
            return
    os.fsync(descriptor)


def fsync_directory(directory):
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        flush_to_disk(descriptor)
    finally:
        os.close(descriptor)


# A test sets this to a function that raises at a named point, to stop a command between two of its writes.
crash_hook = None


def checkpoint(name):
    if crash_hook is not None:
        crash_hook(name)


def durable_write(path, data, mode=0o600):
    """Writes `data` to `path` by temp file, fsync and rename, so a crash leaves the old bytes or the new ones."""
    directory = os.path.dirname(path)
    descriptor, temp_path = tempfile.mkstemp(dir=directory, prefix=TEMP_FILE_PREFIX)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            flush_to_disk(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_path)
        raise
    sync_directory_noting(directory)


def sync_directory_noting(directory):
    """Fsyncs `directory` after a change that landed. Raises DurabilityUnconfirmed when the fsync fails."""
    try:
        fsync_directory(directory)
    except OSError as error:
        raise DurabilityUnconfirmed(directory, load_error_name(error)) from error


def load_error_name(error):
    """An error's closed-vocabulary name, from the shim when it loads and the class name otherwise."""
    try:
        return load_shim().error_name(error)
    except LOAD_FAILURES:
        return type(error).__name__


class Unconfirmed:
    """The paths whose change landed but whose directory fsync failed, kept so a command can say so once at its end."""

    def __init__(self):
        self.entries = []

    def run(self, action, *arguments):
        try:
            action(*arguments)
        except DurabilityUnconfirmed as error:
            self.entries.append(error)

    def lines(self):
        return [f"written, durability unconfirmed: {item.path} (cause {item.cause})" for item in self.entries]


def make_private_directory(path):
    os.makedirs(path, mode=0o700, exist_ok=True)


def read_bytes_or_none(path):
    """The file's bytes, None when it does not exist. Raises OSError for any other failure, a file that is not regular
    included."""
    try:
        return read_regular_bytes(path)
    except FileNotFoundError:
        return None


def read_bytes_or_refuse(path):
    """`read_bytes_or_none` for a read before the command's first write: an OSError becomes a refusal naming the path."""
    try:
        return read_bytes_or_none(path)
    except OSError as error:
        raise AdapterError(
            f"{path} cannot be read ({load_error_name(error)}). Make it a readable regular file, or remove it, then re-run."
        ) from error


# --- registrations and generation ---


class Registration:
    def __init__(self, event, matcher, script, timeout, fail_closed):
        self.event = event
        self.matcher = matcher
        self.script = script
        self.timeout = timeout
        self.fail_closed = fail_closed

    @property
    def key(self):
        return f"{self.event}|{self.matcher or '*'}|{self.script}"


class EmittedEntry:
    """One entry `generate` emits: its Cursor event, its hooks.json object and the label the reports use."""

    def __init__(self, event, label, entry, fail_closed, registration_key, deadline=None):
        self.deadline = deadline
        self.event = event
        self.label = label
        self.entry = entry
        self.fail_closed = fail_closed
        self.registration_key = registration_key


def is_settings_hooks_object(hooks):
    return isinstance(hooks, dict) and all(
        isinstance(groups, list)
        and all(
            isinstance(group, dict)
            and isinstance(group.get("hooks", []), list)
            and all(isinstance(hook, dict) for hook in group.get("hooks", []))
            for group in groups
        )
        for groups in hooks.values()
    )


def read_registrations(settings_path):
    """Every settings.json hook registration, deduplicated by (event, matcher, script) once `if:` is dropped.

    Raises AdapterError, naming it, for a hook that is not a command hook and for two registrations of one key that differ in
    `timeout` or `onFailure`, because either would silently drop or weaken a gate. It raises too for a `hooks` member that is
    not an object of arrays of groups holding arrays of hook objects."""
    settings = read_json_object(settings_path)
    hooks = settings.get("hooks", {})
    seen = {}
    if not is_settings_hooks_object(hooks):
        raise AdapterError(
            f"The hooks member of {settings_path} is not an object of event arrays of groups that hold arrays of hook objects, "
            "so the adapter cannot read the registrations. Restore the file."
        )
    for event, groups in hooks.items():
        for group in groups:
            for hook in group.get("hooks", []):
                command = hook.get("command")
                if hook.get("type", "command") != "command" or not isinstance(command, str):
                    raise AdapterError(
                        f"A hook under {event}|{group.get('matcher') or '*'} in settings.json is not a command hook, "
                        "which the adapter does not translate."
                    )
                registration = Registration(
                    event, group.get("matcher"), command.rsplit("/", 1)[-1], hook.get("timeout"), hook.get("onFailure") == "block"
                )
                first = seen.setdefault(registration.key, registration)
                if (first.timeout, first.fail_closed) != (registration.timeout, registration.fail_closed):
                    raise AdapterError(
                        f"The registration {registration.key} appears twice in settings.json with a different `timeout` or "
                        "`onFailure`, so the adapter cannot tell which one applies."
                    )
    return list(seen.values())


def exception_keys(parity_path):
    parity = read_json_object(parity_path)
    return {item["key"] for item in parity.get("entries", []) if item.get("kind") == "registration"}


def cursor_matcher_for(registration):
    """The regex Cursor selects tool names with, or None when the registration has no matcher token.

    Raises AdapterError naming a token with no Cursor tool name, because a matcher that dropped it would never fire the gate
    for that tool. A registration `parity-exceptions.json` lists never reaches this function."""
    names = []
    tokens = [token for token in (registration.matcher or "").split("|") if token and token != "*"]
    for token in tokens:
        if token not in CURSOR_TOOL_NAMES:
            raise AdapterError(
                f"The matcher token {token!r} of the registration {registration.key} has no Cursor tool name, "
                "and parity-exceptions.json does not list the registration."
            )
        for name in CURSOR_TOOL_NAMES[token]:
            if name not in names:
                names.append(name)
    return "|".join(names) or None


def cursor_entries_for(registration):
    """The (Cursor event, Cursor matcher) pairs one registration emits. An empty list means Cursor has no counterpart."""
    if registration.event in TOOL_EVENTS:
        matcher = cursor_matcher_for(registration)
        if matcher is None:
            return []
        pairs = [(TOOL_EVENTS[registration.event], matcher)]
        if registration.event == "PreToolUse" and "Read" in (registration.matcher or "").split("|"):
            pairs.append(("beforeReadFile", None))
        return pairs
    if registration.event in CURSOR_EVENT_FOR_CLAUDE_EVENT:
        return [(CURSOR_EVENT_FOR_CLAUDE_EVENT[registration.event], None)]
    return []


def render_command(python_path, script_path, arguments, fail_closed_event=None):
    """The launch guard: POSIX sh text around one call of the recorded python3, with the entry's fault tail.

    `fail_closed_event` is the Cursor event of a fail-closed entry, whose tail prints that event's deny document. None
    renders the informational tail."""
    call = f"{shlex.quote(python_path)} -I -S {shlex.quote(script_path)}"
    for argument in arguments:
        call += " " + shlex.quote(argument)
    head = f"{GUARD_HEAD}{call}{GUARD_MIDDLE}"
    if fail_closed_event is None:
        return head + INFORMATIONAL_TAIL
    shim = load_shim()
    document = shim.serialize(shim.deny_document(fail_closed_event, FAULT_TEXT)).decode("ascii").rstrip("\n")
    return head + f"printf '%s' {shlex.quote(document)}; printf '%s' {shlex.quote(FAULT_TEXT)} >&2; exit 2"


GUARD_HEAD = "{ out=$("
GUARD_MIDDLE = '); } 2>/dev/null; rc=$?; if [ "$rc" -eq 0 ] && [ -n "$out" ]; then printf \'%s\' "$out"; exit 0; fi; '
INFORMATIONAL_TAIL = "printf '%s' '{}'; exit 0"
_FAULT_TAIL_BETWEEN = "; printf '%s' "
_FAULT_TAIL_END = " >&2; exit 2"
_TOKEN_RE = re.compile(r"""(?:'[^']*'|"'")+|[A-Za-z0-9_@%+=:,./-]+""")


class ParsedCommand:
    def __init__(self, python_path, script_path, arguments, fail_closed, deny_document, form):
        self.python_path = python_path
        self.script_path = script_path
        self.arguments = arguments
        self.fail_closed = fail_closed
        self.deny_document = deny_document
        self.form = form

    def argument_value(self, flag):
        if flag in self.arguments:
            index = self.arguments.index(flag)
            if index + 1 < len(self.arguments):
                return self.arguments[index + 1]
        return None


def _read_token(text, position):
    match = _TOKEN_RE.match(text, position)
    if match is None:
        return None, position
    words = shlex.split(match.group(0))
    if len(words) != 1:
        return None, position
    return words[0], match.end()


def _shim_arguments_parse(arguments):
    """True when the arguments use only the shim's vocabulary, each flag once, and carry a Claude event and a known mode."""
    values = {}
    index = 0
    while index < len(arguments):
        flag = arguments[index]
        if flag in SHIM_SWITCH_FLAGS:
            if "switch" in values:
                return False
            values["switch"] = flag
            index += 1
        elif flag in SHIM_VALUE_FLAGS:
            if flag in values or index + 1 >= len(arguments):
                return False
            values[flag] = arguments[index + 1]
            index += 2
        else:
            return False
    return values.get("--claude-event", "") != "" and values.get("--mode", "hook") in SHIM_MODES


def parse_command(command):
    return _parse_command(command, stow_shim_path(), stow_adapter_path())


@functools.lru_cache(maxsize=2048)
def _parse_command(command, shim_path, adapter_path):
    """The command as a ParsedCommand when it is in the generated command grammar, else None.

    The grammar is the launch guard around the recorded python3 with `-I -S`, then either the shim's stow path with the
    shim's arguments, or this file's stow path with exactly `check --hook-env` under the informational tail. A command
    parses whatever contract version or python3 path it carries."""
    if not isinstance(command, str) or not command.startswith(GUARD_HEAD):
        return None
    position = len(GUARD_HEAD)
    python_path, position = _read_token(command, position)
    if python_path is None or not command.startswith(" -I -S ", position):
        return None
    script_path, position = _read_token(command, position + len(" -I -S "))
    if script_path is None:
        return None
    arguments = []
    while not command.startswith(GUARD_MIDDLE, position):
        if not command.startswith(" ", position):
            return None
        argument, position = _read_token(command, position + 1)
        if argument is None:
            return None
        arguments.append(argument)
    position += len(GUARD_MIDDLE)
    rest = command[position:]
    if rest == INFORMATIONAL_TAIL:
        fail_closed, deny_document = False, None
    elif rest.startswith("printf '%s' "):
        document, after = _read_token(rest, len("printf '%s' "))
        if document is None or not rest.startswith(_FAULT_TAIL_BETWEEN, after):
            return None
        fault_text, after = _read_token(rest, after + len(_FAULT_TAIL_BETWEEN))
        if fault_text != FAULT_TEXT or rest[after:] != _FAULT_TAIL_END:
            return None
        fail_closed, deny_document = True, document
    else:
        return None
    if script_path == shim_path:
        form = "shim" if _shim_arguments_parse(arguments) else None
    elif script_path == adapter_path:
        form = "check" if list(arguments) == list(CHECK_ARGUMENTS) and not fail_closed else None
    else:
        form = None
    if form is None:
        return None
    return ParsedCommand(python_path, script_path, arguments, fail_closed, deny_document, form)


def holds_adapter_path(command):
    return isinstance(command, str) and (stow_shim_path() in command or stow_adapter_path() in command)


def ownership(item):
    """`owned` for an entry in the generated command grammar, `foreign` for one that holds an adapter path outside it, and
    `non-owned` for anything else, including an element with no string `command`."""
    if not isinstance(item, dict) or not isinstance(item.get("command"), str):
        return "non-owned"
    if parse_command(item["command"]) is not None:
        return "owned"
    return "foreign" if holds_adapter_path(item["command"]) else "non-owned"


def shim_form_arguments(shim, label, claude_event, deadline, *, mode="hook", matcher=None, script=None, fail_closed=True):
    arguments = ["--contract-version", shim.CONTRACT_VERSION, "--claude-event", claude_event, "--mode", mode]
    if matcher is not None:
        arguments += ["--matcher", matcher]
    if script is not None:
        arguments += ["--script", script]
    arguments += ["--label", label, "--deadline", str(deadline), "--fail-closed" if fail_closed else "--fail-open"]
    return arguments


def hook_entry(command, timeout, *, matcher=None, fail_closed=False):
    entry = {"command": command}
    if matcher is not None:
        entry["matcher"] = matcher
    entry["timeout"] = timeout
    if fail_closed:
        entry["failClosed"] = True
    return entry


def adapter_only_entries(shim, python_path, shim_path):
    """The fail-closed `classify`, `pin` and `pin-check` entries and the informational sessionStart check entry."""
    deadline = shim.DEFAULT_DEADLINE_SECONDS
    timeout = shim.entry_timeout_seconds(deadline)
    specs = (
        ("preToolUse", "classify", "classify", None),
        ("preToolUse", "pin", "pin", "Shell"),
        ("beforeShellExecution", "pin-check", "pin-check", None),
    )
    emitted = []
    for event, label, mode, cursor_matcher in specs:
        arguments = shim_form_arguments(shim, label, "PreToolUse", deadline, mode=mode)
        command = render_command(python_path, shim_path, arguments, fail_closed_event=event)
        emitted.append(
            EmittedEntry(
                event, label, hook_entry(command, timeout, matcher=cursor_matcher, fail_closed=True), True, None, deadline
            )
        )
    command = render_command(python_path, stow_adapter_path(), CHECK_ARGUMENTS)
    emitted.append(EmittedEntry("sessionStart", "check", hook_entry(command, CHECK_ENTRY_TIMEOUT_SECONDS), False, None))
    return emitted


def generate_entries(*, python_path, registrations=None, listed_keys=None, shim_path=None, only_key=None):
    """Every entry the install emits, in a fixed order. Raises AdapterError for a registration that drops without a listing.

    `only_key` renders one registration's entries and nothing else, ignoring the listed exceptions."""
    shim = load_shim()
    registrations = registrations if registrations is not None else read_registrations(settings_json_path())
    listed = (
        listed_keys if listed_keys is not None else exception_keys(os.path.join(adapter_directory(), PARITY_EXCEPTIONS_FILE_NAME))
    )
    shim_script = shim_path if shim_path is not None else stow_shim_path()
    emitted = []
    for registration in registrations:
        if only_key is not None and registration.key != only_key:
            continue
        if only_key is None and registration.key in listed:
            continue
        pairs = cursor_entries_for(registration)
        if not pairs:
            raise AdapterError(
                f"The registration {registration.key} has no Cursor counterpart and parity-exceptions.json does not list it."
            )
        deadline = registration.timeout if registration.timeout is not None else shim.DEFAULT_DEADLINE_SECONDS
        if type(deadline) is not int or not shim.MIN_DEADLINE_SECONDS <= deadline <= shim.MAX_DEADLINE_SECONDS:
            raise AdapterError(f"The registration {registration.key} has a timeout the shim's deadline range does not allow.")
        label = registration.script.removesuffix(".sh")
        fail_closed = registration.fail_closed or (
            registration.event == "PreToolUse" and registration.script in ASK_ON_WRITE_SCRIPTS
        )
        for cursor_event, cursor_matcher in pairs:
            arguments = shim_form_arguments(
                shim,
                label,
                registration.event,
                deadline,
                matcher=registration.matcher,
                script=stow_hook_path(registration.script),
                fail_closed=fail_closed,
            )
            command = render_command(python_path, shim_script, arguments, fail_closed_event=cursor_event if fail_closed else None)
            entry = hook_entry(command, shim.entry_timeout_seconds(deadline), matcher=cursor_matcher, fail_closed=fail_closed)
            emitted.append(EmittedEntry(cursor_event, label, entry, fail_closed, registration.key, deadline))
    if only_key is not None:
        if not emitted:
            raise AdapterError(f"No settings.json registration has the key {only_key}.")
        return emitted
    emitted += adapter_only_entries(shim, python_path, shim_script)
    assert_generation_invariants(shim, emitted)
    return emitted


def assert_generation_invariants(shim, emitted):
    """Fails on a label the log grammar rejects, a fail-closed entry on an event with no deny shape, or a timeout that does
    not exceed the shim's worst-case wall time."""
    for item in emitted:
        if shim.log_value("reg", item.label) != item.label:
            raise AdapterError(f"The label {item.label} does not fit the log's registration field.")
        if item.fail_closed:
            if item.event not in FAIL_CLOSED_EVENTS:
                raise AdapterError(f"The fail-closed entry {item.label} is on {item.event}, which has no deny shape.")
            if item.entry["timeout"] <= shim.worst_case_wall_seconds(item.deadline):
                raise AdapterError(f"The fail-closed entry {item.label} has a timeout below the shim's worst-case wall time.")


def hooks_object(emitted):
    hooks = {}
    for item in emitted:
        hooks.setdefault(item.event, []).append(item.entry)
    return hooks


def hooks_vocabulary_problems(hooks):
    """Event names and entry keys outside the pinned vocabulary, one string each.

    Only the generator's tests call it: `install` writes the generator's own output, and `check` reads the live file as data."""
    problems = []
    for event, entries in hooks.items():
        if event not in HOOKS_JSON_EVENTS:
            problems.append(f"unknown event {event!r}")
        for position, entry in enumerate(entries):
            if not isinstance(entry, dict) or not isinstance(entry.get("command"), str):
                continue
            for key in entry:
                if key not in HOOKS_JSON_ENTRY_KEYS:
                    problems.append(f"{event}[{position}] holds the unknown key {key!r}")
    return problems


# --- comparing and printing hooks.json entries ---


def entry_identity(item):
    """What stays the same when an owned entry's arguments change: its form, script, label, mode, event and matcher."""
    parsed = parse_command(item["command"])
    if parsed.form == "check":
        return ("check",)
    return (
        "shim",
        parsed.script_path,
        parsed.argument_value("--label"),
        parsed.argument_value("--mode"),
        parsed.argument_value("--claude-event"),
        parsed.argument_value("--matcher"),
    )


def entry_signature(item, tolerant):
    """A comparable form of an owned entry. `tolerant` leaves out the contract-version token and the python3 path."""
    parsed = parse_command(item["command"])
    arguments = list(parsed.arguments)
    python_path = parsed.python_path
    if tolerant:
        python_path = ""
        if "--contract-version" in arguments:
            arguments[arguments.index("--contract-version") + 1] = ""
    command_part = [python_path, parsed.script_path, arguments, parsed.fail_closed, parsed.deny_document]
    rest = {key: value for key, value in item.items() if key != "command"}
    return json.dumps([command_part, rest], sort_keys=True)


def label_of(item):
    parsed = parse_command(item["command"])
    if parsed.form == "check":
        return "check"
    return parsed.argument_value("--label") or "other"


class HooksReport:
    """How a live hooks.json compares with the entries `generate` emits. Positions are (event, index) in the live file."""

    def __init__(self):
        self.missing = []  # (event, label) of an emitted entry with no live counterpart
        self.changed = []  # (event, index, label) of an owned entry that differs from its emitted counterpart
        self.extra_owned = []  # (event, index, label) of an owned entry with no emitted counterpart
        self.duplicated = []  # (event, index, label)
        self.foreign = []  # (event, index)
        self.non_owned = []  # (event, index)

    @property
    def clean(self):
        return not (self.missing or self.changed or self.extra_owned or self.duplicated or self.foreign)


def classify_hooks(emitted, live_hooks, *, tolerant):
    report = HooksReport()
    unmatched = [(item, entry_signature(item.entry, tolerant), entry_identity(item.entry)) for item in emitted]
    matched_signatures = set()
    pending = []
    for event, items in live_hooks.items():
        for index, item in enumerate(items):
            kind = ownership(item)
            if kind == "non-owned":
                report.non_owned.append((event, index))
            elif kind == "foreign":
                report.foreign.append((event, index))
            else:
                signature = entry_signature(item, tolerant)
                for candidate in unmatched:
                    if candidate[0].event == event and candidate[1] == signature:
                        unmatched.remove(candidate)
                        matched_signatures.add((event, signature))
                        break
                else:
                    if (event, signature) in matched_signatures:
                        report.duplicated.append((event, index, label_of(item)))
                    else:
                        pending.append((event, index, item))
    for event, index, item in pending:
        identity = entry_identity(item)
        for candidate in unmatched:
            if candidate[0].event == event and candidate[2] == identity:
                unmatched.remove(candidate)
                report.changed.append((event, index, label_of(item)))
                break
        else:
            report.extra_owned.append((event, index, label_of(item)))
    report.missing = [(candidate[0].event, candidate[0].label) for candidate in unmatched]
    return report


def refuse_unsafe_targets(*paths):
    """Refuses, naming the path, when an existing file or directory the command writes is a symlink, is owned by someone
    else, or is writable by group or others."""
    for path in paths:
        try:
            status = os.lstat(path)
        except FileNotFoundError:
            continue
        except OSError as error:
            raise AdapterError(
                f"{path} cannot be inspected ({load_error_name(error)}). Make each directory leading to it a readable "
                "directory, then re-run."
            ) from error
        if stat.S_ISLNK(status.st_mode):
            raise AdapterError(
                f"{path} is a symlink, and the adapter does not write through links. Replace it with the real file or directory."
            )
        if status.st_uid != os.getuid() or status.st_mode & 0o022:
            raise AdapterError(
                f"{path} must be owned by you and not writable by group or others. Fix its owner or mode, then re-run."
            )


def is_event_arrays_object(hooks):
    """The shape of a hooks.json `hooks` member: an object whose every value is an array."""
    return isinstance(hooks, dict) and all(isinstance(items, list) for items in hooks.values())


def pre_image_document_of(pre_bytes):
    """The saved pre-install document, or None for bytes that are not a JSON object whose `hooks` member, when present, is
    an object of event arrays."""
    if pre_bytes is None:
        return None
    try:
        document = parse_json_bytes(pre_bytes)
    except ValueError:
        return None
    if not isinstance(document, dict) or not is_event_arrays_object(document.get("hooks", {})):
        return None
    return document


def pre_image_hooks_of(pre_bytes):
    """The `hooks` object of the saved pre-install bytes, or {} for bytes that `pre_image_document_of` does not accept."""
    document = pre_image_document_of(pre_bytes)
    return document.get("hooks", {}) if document is not None else {}


def read_live_hooks(path):
    """(raw bytes or None, parsed document or None, the hooks object). Raises AdapterError for a file the merge cannot use."""
    raw = read_bytes_or_refuse(path)
    if raw is None:
        return None, None, {}
    try:
        document = parse_json_bytes(raw)
    except ValueError as error:
        raise AdapterError(f"{path} does not parse as JSON. Fix or remove it, then re-run.") from error
    if not isinstance(document, dict):
        raise AdapterError(f"{path} is not a JSON object. Fix or remove it, then re-run.")
    hooks = document.get("hooks", {})
    if not is_event_arrays_object(hooks):
        raise AdapterError(f"The hooks member of {path} is not an object of event arrays. Fix it, then re-run.")
    return raw, document, hooks


def escape_text(text):
    pieces = []
    for character in text:
        code = ord(character)
        if 0x20 <= code <= 0x7E:
            pieces.append(character)
        elif code <= 0xFF:
            pieces.append(f"\\x{code:02x}")
        elif code <= 0xFFFF:
            pieces.append(f"\\u{code:04x}")
        else:
            pieces.append(f"\\U{code:08x}")
    return "".join(pieces)


def bounded_text(text):
    escaped = escape_text(text)
    if len(escaped) <= PRINT_VALUE_MAX_CHARS:
        return escaped
    return f"{escaped[:PRINT_VALUE_MAX_CHARS]}... [cut; {len(escaped)} characters in all]"


def describe_entry(event, index, item):
    """One printed entry: its event key, its position and each field by name, every value escaped and bounded."""
    head = f"{bounded_text(event)} [{index}]"
    if not isinstance(item, dict):
        return f"{head}: value={bounded_text(json.dumps(item, ensure_ascii=False))}"
    fields = "; ".join(
        f"{bounded_text(str(key))}={bounded_text(json.dumps(value, ensure_ascii=False))}" for key, value in item.items()
    )
    return f"{head}: {fields}"


def print_set(report, live_hooks, pre_image_hooks, *, replacing_pre_image, comparable, removing=False):
    """The entries the step would otherwise erase, in class order with foreign entries first, as printable lines.

    Each line starts with what the step does to the entry. `removing` is True for `uninstall`, which drops the owned ones."""
    owned_action = "removed" if removing else "replaced"
    classes = [
        ("kept foreign entry", [(e, i) for e, i in report.foreign]),
        (f"{owned_action} duplicated owned entry", [(e, i) for e, i, _ in report.duplicated]),
    ]
    if comparable:
        classes.append(
            (
                f"{owned_action} owned entry that differs from what generate emits now",
                [(e, i) for e, i, _ in report.changed + report.extra_owned],
            )
        )
    if replacing_pre_image:
        not_in_pre_image = [
            (event, index) for event, index in report.non_owned if live_hooks[event][index] not in pre_image_hooks.get(event, [])
        ]
        classes.append(("kept non-owned entry the pre-install file does not hold", not_in_pre_image))
    lines = []
    budget = PRINT_SET_MAX_ENTRIES
    for name, positions in classes:
        shown = positions[:budget]
        budget -= len(shown)
        lines += [f"  {name}: {describe_entry(event, index, live_hooks[event][index])}" for event, index in shown]
        if len(positions) > len(shown):
            lines.append(f"  {len(positions) - len(shown)} more {name}(s) not shown")
    return lines


def safe_event_name(name):
    return name if name in HOOKS_JSON_EVENTS else "other"


def safe_label(label, known_labels):
    return label if label in known_labels else "other"


def drift_lines(report, known_labels):
    """Registration labels and event names only, never an entry's text. A drifted adapter entry is followed by the sentence
    that re-installs it, and the foreign entries by the sentence that deletes them, because `install` keeps a foreign entry."""
    lines = []
    for event, label in report.missing:
        lines.append(f"missing or altered entry: {safe_label(label, known_labels)} on {safe_event_name(event)}")
    for event, _, label in report.changed:
        lines.append(
            f"owned entry differs from what generate emits now: {safe_label(label, known_labels)} on {safe_event_name(event)}"
        )
    for event, _, label in report.extra_owned:
        lines.append(f"owned entry with no emitted counterpart: {safe_label(label, known_labels)} on {safe_event_name(event)}")
    for event, _, label in report.duplicated:
        lines.append(f"duplicated owned entry: {safe_label(label, known_labels)} on {safe_event_name(event)}")
    foreign_lines = [
        f"foreign entry that holds an adapter path: {safe_event_name(event)} [{index}]"
        for event, index in report.foreign[:PRINT_SET_MAX_ENTRIES]
    ]
    if len(report.foreign) > PRINT_SET_MAX_ENTRIES:
        foreign_lines.append(f"{len(report.foreign) - PRINT_SET_MAX_ENTRIES} more foreign entry(s) not shown")
    return [
        *lines,
        *([TAMPER_FIX_SENTENCE] if lines else []),
        *foreign_lines,
        *([FOREIGN_FIX_SENTENCE] if foreign_lines else []),
    ]


def non_owned_lines(report, live_hooks, pre_image_hooks, owned_remain):
    """(findings, notes) for the engineer's own entries, by event and count, never an entry's text.

    The count per event is a note, because an engineer's own hooks are expected. With no owned entry left, each non-owned
    entry the pre-install file does not hold is a finding, by event and position."""
    counts = {}
    for event, _ in report.non_owned:
        counts[safe_event_name(event)] = counts.get(safe_event_name(event), 0) + 1
    notes = []
    if counts:
        notes.append("non-owned entries by event: " + ", ".join(f"{event} {count}" for event, count in sorted(counts.items())))
    findings = []
    if not owned_remain:
        unsaved = [
            (event, index) for event, index in report.non_owned if live_hooks[event][index] not in pre_image_hooks.get(event, [])
        ]
        findings = [
            f"non-owned entry the pre-install file does not hold: {safe_event_name(event)} [{index}]"
            for event, index in unsaved[:PRINT_SET_MAX_ENTRIES]
        ]
        if len(unsaved) > PRINT_SET_MAX_ENTRIES:
            findings.append(f"{len(unsaved) - PRINT_SET_MAX_ENTRIES} more non-owned entry(s) not shown")
    return findings, notes


def has_current_version(document):
    version = document.get("version")
    return type(version) is int and version == HOOKS_JSON_VERSION


def top_level_lines(document, pre_bytes_state):
    """Findings for a `version` other than the one Cursor reads and for top-level members that differ from the pre-install file.

    `pre_bytes_state` is `pre_image_state`'s pair. The merge keeps every top-level member but `hooks` as the pre-install file
    held it, so any difference was made after the install. Member names are not printed, only their count."""
    findings = []
    if not has_current_version(document):
        findings.append(f"the top-level version of hooks.json is not {HOOKS_JSON_VERSION}. {VERSION_FIX_SENTENCE}")
    state, pre_bytes = pre_bytes_state
    baseline = None
    if state == "none":
        baseline = {"version": HOOKS_JSON_VERSION}
    elif state == "present":
        try:
            parsed = parse_json_bytes(pre_bytes)
        except ValueError:
            parsed = None
        baseline = {key: value for key, value in parsed.items() if key != "hooks"} if isinstance(parsed, dict) else None
    if baseline is not None:
        live = {key: value for key, value in document.items() if key != "hooks"}
        differing = sum(1 for key in baseline.keys() | live.keys() if baseline.get(key, MISSING) != live.get(key, MISSING))
        if differing:
            findings.append(
                f"top-level members of hooks.json that differ from the pre-install file: {differing}. {TOP_LEVEL_FIX_SENTENCE}"
            )
    return findings


MISSING = object()


# --- probes and tools ---


def run_probe(path, arguments, *, environment=None):
    """Runs one tool probe from the probe directory with stdin on the null device and stderr discarded.

    Returns (exit status, first stdout line). Raises ProbeStalled past the bound and OSError when the file cannot start."""
    try:
        completed = subprocess.run(
            [path, *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=probe_directory(),
            env=environment if environment is not None else dict(os.environ),
            timeout=PROBE_BOUND_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ProbeStalled(path) from error
    text = completed.stdout.decode("utf-8", errors="replace")
    first_line = text.split("\n", 1)[0]
    return completed.returncode, first_line


def parse_version_fields(line):
    """(major, minor) from exactly two fields of 1 to 9 digits, else None."""
    fields = line.split()
    if len(fields) != 2:
        return None
    if not all(re.fullmatch(f"[0-9]{{1,{PROBE_VERSION_FIELD_MAX_DIGITS}}}", field, re.ASCII) for field in fields):
        return None
    return int(fields[0]), int(fields[1])


def floor_status(path, floor, program_arguments, *, environment=None):
    """('ok'|'below'|'unreadable', the parsed (major, minor) or None, the start failure's cause or "") for one tool, compared
    as integers. The cause is set only when the tool could not start."""
    try:
        status, line = run_probe(path, program_arguments, environment=environment)
    except OSError as error:
        return "unreadable", None, load_error_name(error)
    version = parse_version_fields(line) if status == 0 else None
    if version is None:
        return "unreadable", None, ""
    return ("ok" if version >= floor else "below"), version, ""


def python_floor_status(path, *, environment=None):
    return floor_status(path, PYTHON_FLOOR, ["-I", "-S", "-c", PROBE_PYTHON_PROGRAM], environment=environment)


def bash_floor_status(path, *, environment=None):
    return floor_status(path, BASH_FLOOR, ["--noprofile", "--norc", "-c", PROBE_BASH_PROGRAM], environment=environment)


def stall_text(path, command_name):
    return (
        f"{path} did not answer within {PROBE_BOUND_SECONDS} s. A first run after an install or a boot can be slow, "
        f"and re-running `{command_name}` is the first step. If the stall repeats, run {path} from your own terminal "
        "to see what holds it."
    )


def format_version(version):
    return f"{version[0]}.{version[1]}"


def floor_text(label, path, floor, status, version, start_error=""):
    floor_name = format_version(floor)
    if status == "below":
        return f"{label} at {path} reports {format_version(version)}, below {floor_name} or later."
    if start_error:
        return f"{label} at {path} could not be started (cause {start_error}), so {floor_name} or later cannot be confirmed."
    return f"{label} at {path} printed no version it could read, so {floor_name} or later cannot be confirmed."


def judge_tool_path(path):
    """The index (0, 1 or 2) of the first tool-path condition the path fails, in the rule's listed order, or None."""
    shim = load_shim()
    if not shim.is_absolute_without_parent_component(path):
        return 0
    if not shim.holds_no_path_list_character(path):
        return 1
    try:
        if not (stat.S_ISREG(os.stat(path).st_mode) and os.access(path, os.X_OK)):
            return 2
    except OSError, ValueError:
        return 2
    return None


def tool_path_refusal(tool, path, condition_index):
    sentence = FILE_FIX_SENTENCE if condition_index == 2 else PATH_FIX_SENTENCE
    return AdapterError(f"{tool} at {path!r} {TOOL_PATH_CONDITION_TEXT[condition_index]}. {sentence}")


def find_tool(name, *, search_path=None, directory=None):
    """The path `shutil.which` finds, which this file calls the tool's `command -v` path. `directory` is the working
    directory the lookup runs in, because a relative or empty PATH entry resolves against it."""
    if directory is None:
        return shutil.which(name, path=search_path)
    with contextlib.chdir(directory):
        return shutil.which(name, path=search_path)


def is_gnu_coreutils_line(line):
    return "GNU coreutils" in line


def wrapper_candidate_test(path, command_name):
    """None when `path` runs and its `--version` first line names GNU coreutils, else a one-line cause."""
    try:
        status, line = run_probe(path, ["--version"])
    except ProbeStalled:
        return stall_text(path, command_name)
    except OSError as error:
        return f"{path} could not be started (cause {load_error_name(error)})."
    if status != 0 or not is_gnu_coreutils_line(line):
        printed = bounded_text(line) or "nothing"
        return f"{path} does not name GNU coreutils in the first line of its --version (it printed {printed!r})."
    return None


def gnu_refusal(judged):
    lines = ["Neither `timeout` nor `gtimeout` names GNU coreutils in the first line of its --version."]
    for name, path, line in judged:
        if path is None:
            lines.append(f"  {name}: not found")
        else:
            lines.append(f"  {name} at {path}: {bounded_text(line) if line else 'printed no first line'}")
    lines.append(
        "The adapter needs GNU coreutils' `timeout`, which is stricter than the README's `timeout` requirement, "
        "because BusyBox returns a TERM-trapping hook's own status, as the `_lib_capped_for` header in "
        "claude/.claude/hooks/_lib.sh states, so a timed-out hook could read as an allow."
    )
    lines.append(
        "On macOS, `brew install coreutils` provides `gtimeout`. Otherwise, put GNU's directory first on PATH. "
        "Then re-run `install`."
    )
    return AdapterError("\n".join(lines))


def child_failure_text(wrapper, failure):
    """The text for a wrapper that names GNU coreutils but fails the self-check's child, or None for a failure it does not
    describe. A pure function of the failure string, with the outcome vocabulary read from the shim."""
    shim = load_shim()
    fields = failure.split(" ")
    if len(fields) < 3 or fields[0] != "hook-run:" or fields[2] not in shim.HOOK_RUN_OUTCOMES:
        return None
    outcome = fields[2]
    value = fields[3] if len(fields) > 3 else ""
    replace, rerun = REPLACE_WRAPPER_SENTENCE, RERUN_WRAPPER_SENTENCE
    if outcome == "unenterable-dir":
        return None
    if outcome == "status":
        return (
            f"The wrapper {wrapper} ran with status {value}, needed status {shim.SELF_CHECK_EXIT_STATUS}, "
            f"so it does not run hooks as the shim needs. {replace}"
        )
    if outcome == "streams":
        return (
            f"The wrapper {wrapper} gave status {shim.SELF_CHECK_EXIT_STATUS} but its child's stdout or stderr did not "
            f"come through, so it does not pass a hook's streams as the shim needs. {replace}"
        )
    if outcome == "start-failed" and value.startswith("status:"):
        return (
            f"The wrapper {wrapper} started and failed on its own ({value}), as the Recovery section's start-failed entry "
            f"says. {rerun}"
        )
    if outcome == "start-failed":
        sentence = rerun if value in shim.TRANSIENT_ERRNO_SYMBOLS else replace
        return f"The wrapper {wrapper} could not be started at that path (cause {value}). {sentence}"
    if outcome == "timeout":
        return f"The wrapper {wrapper} did not finish within the self-check's deadline, {shim.MIN_DEADLINE_SECONDS} s. {rerun}"
    if outcome == "signal":
        return f"The wrapper {wrapper} was ended by the signal {value}. {rerun}"
    if outcome == "hook-output-cap":
        return f"The wrapper {wrapper} passed more output than the shim's cap. {replace}"
    return None


# --- the self-check subcommand and its parent-side reading ---


def interpreter_report():
    return {
        "executable": sys.executable,
        "version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "isolated": sys.flags.isolated,
        "no_site": sys.flags.no_site,
    }


def shim_load_failure_text(error, consequence, fix_sentence=SELF_CHECK_FIX_SENTENCE):
    """The one sentence for a hook-shim.py that did not load, naming only the error's class."""
    return f"hook-shim.py did not load ({type(error).__name__}), so {consequence}. {fix_sentence}"


def run_self_check_subcommand(wrapper):
    """Prints one JSON document, whatever the checks find: the interpreter report and the failure strings."""
    report = interpreter_report()
    failures = []
    try:
        shim = compile_module("cursor_hook_shim_stow", stow_shim_path())
    except Exception as error:  # the failure names the class, never the message
        failures.append(f"shim-load: {type(error).__name__}")
    else:
        failures += shim.self_check(wrapper)
    sys.stdout.write(json.dumps({"interpreter": report, "failures": failures}) + "\n")
    return 0


def parse_failure(text):
    """(check name, class, outcome, value) for a failure string in the grammar, else None, which callers print as `other`."""
    shim = load_shim()
    name, separator, rest = text.partition(": ")
    if not separator or name not in SELF_CHECK_NAMES:
        return None
    fields = rest.split(" ")
    if not 1 <= len(fields) <= 3 or not all(re.fullmatch(shim.LOG_VALUE_PATTERN, field) for field in fields):
        return None
    if len(fields) > 1 and fields[1] not in shim.HOOK_RUN_OUTCOMES:
        return None
    return (name, *fields)


def render_failure(failure, wrapper):
    parsed = parse_failure(failure)
    if parsed is None:
        return "self-check: other"
    text = child_failure_text(wrapper, failure)
    if text is not None:
        return text
    detail = f"{parsed[0]}: {' '.join(parsed[1:])}"
    return detail if parsed[0] == "hook-run" else f"{detail}. {SELF_CHECK_FIX_SENTENCE}"


class SelfCheckResult:
    def __init__(self, interpreter, failures):
        self.interpreter = interpreter
        self.failures = failures


def run_self_check(python_path, wrapper):
    """Runs the subcommand from the stow path under the recorded python3. Returns a SelfCheckResult, or raises AdapterError
    when the run gives no result."""
    adapter = stow_adapter_path()
    if not os.access(adapter, os.R_OK):
        raise AdapterError(
            f"{adapter} is missing or unreadable, so the self-check cannot run. {STOW_FIX_SENTENCE}"
        )
    try:
        shim = load_shim()
    except LOAD_FAILURES as error:
        raise AdapterError(shim_load_failure_text(error, "the self-check cannot run")) from error
    environment = {name: value for name, value in os.environ.items() if name not in shim.CLAUDE_PROCESS_ENV_NAMES}
    repeat_arguments = [python_path, "-I", "-S", adapter, SELF_CHECK_SUBCOMMAND, "--wrapper", wrapper]
    repeat_command = shlex.join(repeat_arguments)
    bound_seconds = shim.entry_timeout_seconds(shim.MIN_DEADLINE_SECONDS)
    try:
        completed = subprocess.run(
            repeat_arguments,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            cwd=probe_directory(),
            env=environment,
            timeout=bound_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        # The single retry is a project choice, as for RERUN_WRAPPER_SENTENCE.
        raise AdapterError(
            f"The self-check at {adapter} under {python_path} gave no result within {bound_seconds} s "
            f"({type(error).__name__}). A first run after an install or a boot can be slow. Re-run the command once. "
            f"If it times out again, run `{repeat_command}` from a terminal to see what holds it."
        ) from error
    except OSError as error:
        raise AdapterError(
            f"The self-check at {adapter} under {python_path} gave no result ({type(error).__name__}). "
            f"{SELF_CHECK_FIX_SENTENCE} To see the cause, run `{repeat_command}` from a terminal."
        ) from error
    no_result = AdapterError(
        f"The self-check at {adapter} under {python_path} gave no result (exit status {completed.returncode}). "
        f"{SELF_CHECK_FIX_SENTENCE} To see the cause, run `{repeat_command}` from a terminal."
    )
    if completed.returncode != 0:
        raise no_result
    try:
        document = json.loads(completed.stdout.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError) as error:
        raise no_result from error
    if (
        not isinstance(document, dict)
        or set(document) != {"interpreter", "failures"}
        or not isinstance(document["interpreter"], dict)
        or set(document["interpreter"]) != set(SELF_CHECK_INTERPRETER_KEYS)
        or not isinstance(document["failures"], list)
        or not all(isinstance(item, str) for item in document["failures"])
    ):
        raise no_result
    return SelfCheckResult(document["interpreter"], document["failures"])


def self_check_failure_lines(result, python_path, wrapper):
    interpreter = result.interpreter
    prefix = (
        f"The self-check under {interpreter['executable']} ({interpreter['version']}, "
        f"isolated={interpreter['isolated']}, no_site={interpreter['no_site']})"
    )
    return [f"{prefix}: {render_failure(failure, wrapper)}" for failure in result.failures]


# --- the install record ---


def read_record():
    """The install record's parsed object. Raises AdapterError when it is missing or unusable.

    Every recorded path meets the shim's tool-path rule and python3 is a string, so no command runs a file the record names
    before that has been judged."""
    record = read_json_object(install_record_path())
    tool_paths = record.get("tool_paths")
    if not isinstance(record.get("routed_config_dir"), str) or not isinstance(tool_paths, dict):
        raise AdapterError(f"{install_record_path()} lacks the routed config dir or the tool paths.")
    if not isinstance(tool_paths.get("python3"), str):
        raise AdapterError(f"{install_record_path()} names no python3.")
    if not is_valid_tool_path(record["routed_config_dir"]) or not all(is_valid_tool_path(path) for path in tool_paths.values()):
        raise AdapterError(
            f"{install_record_path()} holds a path that is not absolute, or that has a `..` component, `:`, newline or NUL."
        )
    return record


def record_or_reason():
    """(the record, None), or (None, the reason it is unusable)."""
    try:
        return read_record(), None
    except AdapterError as error:
        return None, str(error)


def record_or_none():
    return record_or_reason()[0]


def record_environment(record):
    return load_shim().hook_environment({"tool_paths": record["tool_paths"], "routed_config_dir": record["routed_config_dir"]})


def state_directory(record):
    return os.path.join(record["routed_config_dir"], STATE_DIR_NAME)


def validate_config_directory(raw):
    """The routed config dir, or an AdapterError naming the check it fails."""
    if CONFIG_DIR_PATTERN.fullmatch(raw) is None:
        raise AdapterError(f"--config-dir {raw!r} must be an absolute path of letters, digits and . _ / + @ = - only.")
    if any(component in ("", "..") for component in raw.split("/")[1:]):
        raise AdapterError(f"--config-dir {raw} must have no `..` and no empty component, such as `//` or a trailing `/`.")
    if os.path.islink(raw):
        raise AdapterError(f"--config-dir {raw} is a symlink. Name the real directory.")
    try:
        status = os.lstat(raw)
    except FileNotFoundError as error:
        raise AdapterError(f"--config-dir {raw} does not exist. Create it, or name an existing directory.") from error
    except OSError as error:
        raise AdapterError(f"--config-dir {raw} cannot be read ({type(error).__name__}).") from error
    if not stat.S_ISDIR(status.st_mode):
        raise AdapterError(f"--config-dir {raw} is not a directory.")
    if status.st_uid != os.getuid() or status.st_mode & 0o022:
        raise AdapterError(f"--config-dir {raw} must be owned by you and not writable by group or others.")
    ancestor = os.path.realpath(raw)
    while True:
        if os.path.exists(os.path.join(ancestor, ".git")):
            raise AdapterError(f"--config-dir {raw} lies inside a git work tree. Name a directory outside any repository.")
        parent = os.path.dirname(ancestor)
        if parent == ancestor:
            return raw
        ancestor = parent


# --- discovering the tools ---


def discover_tools():
    """The ordered tool paths to record, each found and judged, python3 first. Raises AdapterError with zero writes done."""
    shim = load_shim()
    tool_paths = {}
    search_directory = probe_directory()
    found_python = find_tool("python3", directory=search_directory)
    if found_python is None:
        raise AdapterError(
            f"`python3` is not on PATH. Put a python3 of {format_version(PYTHON_FLOOR)} or later first on PATH, "
            "then re-run `install`."
        )
    failed = judge_tool_path(found_python)
    if failed is not None:
        raise tool_path_refusal("python3", found_python, failed)
    try:
        query_status, query_line = run_probe(found_python, ["-I", "-S", "-c", QUERY_PYTHON_PROGRAM])
    except ProbeStalled as error:
        raise AdapterError(stall_text(error.path, "install")) from error
    except OSError as error:
        raise AdapterError(f"python3 at {found_python} could not be started (cause {shim.error_name(error)}).") from error
    try:
        answer = json.loads(query_line)
        is_three_strings = isinstance(answer, list) and len(answer) == 3 and all(isinstance(item, str) for item in answer)
        if query_status != 0 or not is_three_strings:
            raise ValueError
    except (ValueError, RecursionError) as error:
        raise AdapterError(f"python3 at {found_python} did not answer the `sys.executable` query it needs.") from error
    executable, prefix, base_prefix = answer
    if prefix != base_prefix:
        raise AdapterError(
            f"python3 at {found_python} runs in a virtual environment. Run `install` from a shell with no activated environment."
        )
    if not shim.is_absolute_without_parent_component(executable):
        raise AdapterError(
            f"python3's `sys.executable` is {executable!r}, which {TOOL_PATH_CONDITION_TEXT[0]}. {PYTHON_EXECUTABLE_FIX_SENTENCE}"
        )
    if not shim.holds_no_path_list_character(executable):
        raise AdapterError(
            f"python3's `sys.executable` is {executable!r}, which {TOOL_PATH_CONDITION_TEXT[1]}. {PYTHON_EXECUTABLE_FIX_SENTENCE}"
        )
    tool_paths["python3"] = executable
    status, version, start_error = python_floor_status_or_stall(executable, "install")
    if status != "ok":
        raise AdapterError(
            f"{floor_text('python3', executable, PYTHON_FLOOR, status, version, start_error)} "
            f"Put a {format_version(PYTHON_FLOOR)} or later `python3` first on PATH, then re-run `install`."
        )
    for name in RECORDED_TOOLS:
        path = find_tool(name)
        if path is None:
            raise AdapterError(f"`{name}` is not on PATH. Install it and put its directory on PATH, then re-run `install`.")
        failed = judge_tool_path(path)
        if failed is not None:
            raise tool_path_refusal(name, path, failed)
        tool_paths[name] = path
        if name == "bash":
            status, version, start_error = probe_or_stall(bash_floor_status, path, "install")
            if status != "ok":
                raise AdapterError(
                    f"{floor_text('bash', path, BASH_FLOOR, status, version, start_error)} "
                    f"Put a bash {format_version(BASH_FLOOR)} or later first on PATH, then re-run `install`."
                )
    tool_paths = order_wrapper_in(tool_paths, choose_wrapper())
    for name in OPTIONAL_TOOLS:
        path = find_tool(name)
        if path is not None:
            failed = judge_tool_path(path)
            if failed is not None:
                raise tool_path_refusal(name, path, failed)
            tool_paths[name] = path
    return tool_paths


def probe_or_stall(function, path, command_name, **keywords):
    try:
        return function(path, **keywords)
    except ProbeStalled as error:
        raise AdapterError(stall_text(error.path, command_name)) from error


def python_floor_status_or_stall(path, command_name, **keywords):
    return probe_or_stall(python_floor_status, path, command_name, **keywords)


def choose_wrapper():
    """(name, path) of the first of `timeout` and `gtimeout` that names GNU coreutils. Raises the GNU refusal otherwise."""
    judged = []
    for name in WRAPPER_CANDIDATES:
        path = find_tool(name)
        if path is None:
            judged.append((name, None, ""))
            continue
        failed = judge_tool_path(path)
        if failed is not None:
            raise tool_path_refusal(name, path, failed)
        try:
            status, line = run_probe(path, ["--version"])
        except ProbeStalled as error:
            raise AdapterError(stall_text(error.path, "install")) from error
        except OSError:
            judged.append((name, path, ""))
            continue
        if status == 0 and is_gnu_coreutils_line(line):
            return name, path
        judged.append((name, path, line))
    raise gnu_refusal(judged)


def order_wrapper_in(tool_paths, wrapper):
    """The tool paths with the wrapper after `sha256sum`, the position the record lists it in."""
    name, path = wrapper
    ordered = dict(tool_paths)
    ordered[name] = path
    return ordered


def bare_python_check(tool_paths, routed_config_dir):
    """Judges the `python3` the hooks' PATH resolves. Returns None when it meets the floor, else a one-line cause."""
    environment = record_environment({"tool_paths": tool_paths, "routed_config_dir": routed_config_dir})
    recorded_directory = os.path.dirname(tool_paths["python3"])
    found = find_tool("python3", search_path=environment["PATH"])
    if found is None:
        return (
            "No `python3` resolves on the PATH the hooks run under. Put a "
            f"{format_version(PYTHON_FLOOR)} or later `python3` first on PATH whose own directory holds a file named `python3`, "
            "then re-run `install`."
        )
    status, version, start_error = python_floor_status_or_stall(found, "install", environment=environment)
    if status == "ok":
        return None
    if os.path.dirname(found) == recorded_directory:
        fix = f"Make {recorded_directory}'s `python3` {format_version(PYTHON_FLOOR)} or later, then re-run `install`."
    else:
        fix = (
            f"Put a {format_version(PYTHON_FLOOR)} or later `python3` first on PATH whose own directory holds a file named "
            f"`python3`, then re-run `install`. The recorded python3's directory {recorded_directory} holds none, so the hooks "
            f"run {found}."
        )
    return f"{floor_text('The `python3` the hooks run', found, PYTHON_FLOOR, status, version, start_error)} {fix}"


# --- install ---


def desired_record(tool_paths, routed_config_dir, shim, acceptance_only):
    tool_map_path = os.path.join(adapter_directory(), TOOL_MAP_FILE_NAME)
    tool_map = read_json_object(tool_map_path)
    # The shim's own predicate, so install refuses the map the shim would refuse at every call.
    if not shim.tool_map_is_usable(tool_map):
        raise AdapterError(f"{tool_map_path} is not a tool map the shim accepts, so every hook would deny. {STOW_FIX_SENTENCE}")
    record = {
        "routed_config_dir": routed_config_dir,
        "contract_version": shim.CONTRACT_VERSION,
        "tool_paths": tool_paths,
        "tool_map": tool_map,
    }
    if acceptance_only:
        record["acceptance_only"] = True
    return record


def missing_prerequisites(emitted):
    """The runtime files and hook scripts an emitted entry runs that are absent, sorted."""
    needed = {stow_shim_path(), stow_adapter_path()}
    for item in emitted:
        parsed = parse_command(item.entry["command"])
        script = parsed.argument_value("--script")
        if script is not None:
            needed.add(script)
    return [path for path in sorted(needed) if not os.path.isfile(path)]


def check_prerequisites(emitted):
    """Refuses, naming the first absent file, when a runtime file or hook script an emitted entry runs is absent."""
    missing = missing_prerequisites(emitted)
    if missing:
        raise AdapterError(
            f"{missing[0]} is missing, so an entry the install would write could not run. {STOW_FIX_SENTENCE}"
        )


def invocation(subcommand):
    """The full command that runs one adapter subcommand from a terminal."""
    return f"python3 {shlex.quote(stow_adapter_path())} {subcommand}"


def step_reached_text(reached):
    """Where a failed command stopped. `reached` is the last step name it recorded, or "none" before its first."""
    return "before its first step finished" if reached == "none" else f"after the step {reached}"


def failing_file_text(error):
    """The file an OSError names, else a general phrase."""
    filename = getattr(error, "filename", None)
    return filename if isinstance(filename, str) else "a file this command writes"


def post_write_failure(error, reached, instruction):
    """The one message for an error after the command's first write."""
    where = failing_file_text(error)
    return PartialWriteError(
        f"{where} failed ({load_error_name(error)}) {step_reached_text(reached)}. hooks.json and the adapter's files "
        f"may be partly written, and the next `uninstall` completes or removes them. {instruction}"
    )


def merge_hooks(document, live_hooks, emitted):
    """The merged document: every non-owned and foreign entry stays in place, and the emitted entries follow them."""
    merged_hooks = {}
    for event, items in live_hooks.items():
        kept = [item for item in items if ownership(item) != "owned"]
        if kept:
            merged_hooks[event] = kept
    for event, items in hooks_object(emitted).items():
        merged_hooks.setdefault(event, []).extend(items)
    merged = dict(document) if document is not None else {"version": HOOKS_JSON_VERSION}
    merged["hooks"] = merged_hooks
    return merged


def has_owned_entry(live_hooks):
    return any(ownership(item) == "owned" for items in live_hooks.values() for item in items)


def pre_image_state(directory):
    """('present', bytes), ('none', None) or (None, None) for the pre-install file the directory holds."""
    present = read_bytes_or_refuse(os.path.join(directory, PRE_IMAGE_NAME))
    if present is not None:
        return "present", present
    if os.path.exists(os.path.join(directory, PRE_IMAGE_NONE_NAME)):
        return "none", None
    return None, None


def load_shim_or_refuse(command_name, fix_sentence=SELF_CHECK_FIX_SENTENCE):
    """The shim, or an AdapterError naming the failed load, for a command that writes nothing before it has the shim."""
    try:
        return load_shim()
    except LOAD_FAILURES as error:
        raise AdapterError(shim_load_failure_text(error, f"`{command_name}` cannot run", fix_sentence)) from error


def run_install(arguments):
    shim = load_shim_or_refuse("install")
    if not arguments.acceptance_only:
        raise AdapterError(
            "`install` refuses without --acceptance-only: this build supports only the acceptance run, so add "
            f"--acceptance-only to run it. {MARKER_GATES_CLAUSE} Nothing was written."
        )
    routed = validate_config_directory(arguments.config_dir)
    directory = record_directory()
    existing = record_or_none()
    if existing is not None and existing["routed_config_dir"] != routed:
        raise AdapterError(
            f"An install already routes {existing['routed_config_dir']}. `install` with another config dir refuses, "
            "because hooks.json is machine-wide. Run `uninstall` first."
        )
    state_dir = os.path.join(routed, STATE_DIR_NAME)
    if os.path.islink(state_dir) or (os.path.exists(state_dir) and not os.path.isdir(state_dir)):
        raise AdapterError(f"{state_dir} is not a real directory. Remove it, then re-run `install`.")
    refuse_unsafe_targets(state_dir)
    tool_paths = discover_tools()
    cause = bare_python_check(tool_paths, routed)
    if cause is not None:
        raise AdapterError(cause)
    bash_cause = bare_bash_check(tool_paths, routed)
    if bash_cause is not None:
        raise AdapterError(bash_cause)
    record = desired_record(tool_paths, routed, shim, arguments.acceptance_only)
    python_path = tool_paths["python3"]
    wrapper = next(path for name, path in tool_paths.items() if name in WRAPPER_CANDIDATES)
    emitted = generate_entries(python_path=python_path)
    check_prerequisites(emitted)
    result = run_self_check(python_path, wrapper)
    if result.failures:
        raise AdapterError("\n".join(self_check_failure_lines(result, python_path, wrapper)))
    hooks_path = os.path.join(cursor_directory(), HOOKS_JSON_NAME)
    refuse_unsafe_targets(cursor_directory(), directory, hooks_path)
    raw, document, live_hooks = read_live_hooks(hooks_path)
    if document is not None and not has_current_version(document):
        found_version = bounded_text(json.dumps(document["version"])) if "version" in document else "absent"
        raise AdapterError(
            f"{hooks_path} has the top-level version {found_version}, and the adapter writes only version "
            f"{HOOKS_JSON_VERSION} files. {VERSION_FIX_SENTENCE} Nothing was written."
        )
    merged = merge_hooks(document, live_hooks, emitted)
    report = classify_hooks(emitted, live_hooks, tolerant=True)
    owned_present = has_owned_entry(live_hooks)
    pre_state, pre_bytes = pre_image_state(directory)
    pre_hooks = pre_image_hooks_of(pre_bytes)
    replacing_pre_image = not owned_present and pre_state is not None
    for line in print_set(report, live_hooks, pre_hooks, replacing_pre_image=replacing_pre_image, comparable=True):
        print(line)
    # Writes, in order. A crash between two leaves a state the next `uninstall` completes.
    unconfirmed = Unconfirmed()
    progress = ["none"]

    def reach(name):
        progress[0] = name
        checkpoint(name)

    try:
        make_private_directory(cursor_directory())
        make_private_directory(directory)
        sweep_temp_debris(cursor_directory())
        sweep_temp_debris(directory)
        if not owned_present:
            for stale in (PRE_IMAGE_NAME, PRE_IMAGE_NONE_NAME):
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(os.path.join(directory, stale))
            if raw is None:
                unconfirmed.run(durable_write, os.path.join(directory, PRE_IMAGE_NONE_NAME), b"")
            else:
                unconfirmed.run(durable_write, os.path.join(directory, PRE_IMAGE_NAME), raw)
        reach("after-pre-image")
        unconfirmed.run(durable_write, os.path.join(directory, INSTALL_RECORD_NAME), json_bytes(record))
        make_private_directory(state_dir)
        reach("after-install-record")
        manifest_files = write_generated_files()
        reach("after-generated-files")
        if read_bytes_or_none(hooks_path) != raw:
            raise PartialWriteError(
                f"{hooks_path} changed while `install` ran, so it was left as it is, and the install stopped "
                f"{step_reached_text(progress[0])}. The adapter's files may be partly written. "
                "Run `uninstall`, then re-run `install`."
            )
        mode = stat.S_IMODE(os.stat(hooks_path).st_mode) if raw is not None else 0o600
        unconfirmed.run(durable_write, hooks_path, json_bytes(merged), mode)
        reach("after-hooks-json")
        unconfirmed.run(durable_write, os.path.join(directory, MANIFEST_NAME), json_bytes({"files": manifest_files}))
        reach("after-manifest")
    except PartialWriteError:
        raise
    except Exception as error:
        raise post_write_failure(
            error, progress[0], "Run `uninstall`, then re-run `install` once the cause is fixed."
        ) from error
    print(f"Installed {len(emitted)} entries into {hooks_path}, routing {routed}.")
    for line in unconfirmed.lines():
        print(line)
    for line in run_mode_lines(record):
        print(line)
    print(f"Relaunch Cursor, then run `{invocation('check')}`.")
    return 0


def bare_bash_check(tool_paths, routed_config_dir):
    environment = record_environment({"tool_paths": tool_paths, "routed_config_dir": routed_config_dir})
    found = find_tool("bash", search_path=environment["PATH"])
    if found is None:
        return (
            "No `bash` resolves on the PATH the hooks run under. "
            f"Put a bash {format_version(BASH_FLOOR)} or later first on PATH, then re-run `install`."
        )
    status, version, start_error = probe_or_stall(bash_floor_status, found, "install", environment=environment)
    if status == "ok":
        return None
    return (
        f"{floor_text('The `bash` the hooks run', found, BASH_FLOOR, status, version, start_error)} "
        f"Put a bash {format_version(BASH_FLOOR)} or later first on PATH, then re-run `install`."
    )


def write_generated_files():
    """Writes the agents and rules this build generates, and returns the manifest's path-to-hash map. This build
    generates none."""
    return {}


def run_mode_lines(record):
    lines = [RUN_MODE_LINE]
    if record.get("acceptance_only") is True:
        lines.append(ACCEPTANCE_ONLY_LINE)
    return lines


def run_mode_label(record):
    return RUN_MODE_ACCEPTANCE_LABEL if record.get("acceptance_only") is True else RUN_MODE_LABEL


# --- uninstall ---


def manifest_candidates(manifest_files, cursor_dir):
    """The manifest candidates to remove, as (path, hash) pairs, and the paths to keep.

    A candidate is removable when it lies inside the agents or rules directory, which must itself be a real directory, by path
    component on the resolved parent, is a regular file and not a link, and matches the manifest's hash. One inside the
    directories that is already absent counts as removed."""
    accepted, kept = [], []
    roots = []
    for name in GENERATED_FILE_DIRECTORIES:
        root = os.path.join(cursor_dir, name)
        with contextlib.suppress(OSError):
            if stat.S_ISDIR(os.lstat(root).st_mode):
                roots.append(os.path.realpath(root))
    for path, expected_hash in manifest_files.items():
        try:
            lexical = os.path.abspath(path)
            resolved_parent = os.path.realpath(os.path.dirname(lexical))
        except ValueError:  # a key that is not a clean path, such as one holding a NUL
            kept.append(path)
            continue
        inside = any(resolved_parent == root or resolved_parent.startswith(root + os.sep) for root in roots)
        if not inside:
            kept.append(lexical)
            continue
        try:
            status = os.lstat(lexical)
        except FileNotFoundError:
            accepted.append((lexical, None))
            continue
        except OSError, ValueError:
            kept.append(lexical)
            continue
        if stat.S_ISREG(status.st_mode):
            try:
                descriptor = os.open(lexical, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
                with os.fdopen(descriptor, "rb") as handle:
                    matches = sha256_hex(handle.read()) == expected_hash
            except OSError, ValueError:
                matches = False
            if matches:
                accepted.append((lexical, expected_hash))
                continue
        kept.append(lexical)
    return accepted, kept


def prune_empty_arrays(document):
    """The document without its empty event arrays. `document["hooks"]` must be an object, as `pre_image_document_of` ensures."""
    pruned = dict(document)
    hooks = {event: items for event, items in document.get("hooks", {}).items() if items}
    pruned["hooks"] = hooks
    return pruned


def failure_cause(error):
    """The error's class name, with its bounded message for an AdapterError, whose message holds settings.json keys and file
    paths and never hooks.json entry text."""
    if isinstance(error, AdapterError):
        return f"{type(error).__name__}: {bounded_text(str(error))}"
    return type(error).__name__


def entries_for_uninstall():
    """(the emitted entries, None), or (None, the failing step's description and the failure cause).

    The load and the generation are guarded apart so the printed line names the step that failed."""
    try:
        load_shim()
    except LOAD_FAILURES as error:
        return None, ("hook-shim.py did not load", failure_cause(error))
    try:
        return generate_entries(python_path=sys.executable), None
    except LOAD_FAILURES as error:
        return None, ("generating the expected entries failed", failure_cause(error))


def json_bytes_or_refusal(document, path):
    """`json_bytes(document)`, or a refusal naming `path` for a document JSON cannot write back, which only the lenient parse
    of a shim that does not load lets through."""
    try:
        return json_bytes(document)
    except (ValueError, RecursionError) as error:
        raise AdapterError(
            f"{path} holds a value JSON cannot write back (such as NaN or Infinity), so it was left as it is. "
            "Fix or remove that member, then re-run."
        ) from error


def sweep_temp_debris(directory):
    """Removes the `.adapter-tmp-*` regular files a killed write left in `directory`."""
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        if name.startswith(TEMP_FILE_PREFIX):
            path = os.path.join(directory, name)
            with contextlib.suppress(OSError):
                if stat.S_ISREG(os.lstat(path).st_mode):
                    os.unlink(path)


def fallback_report(live_hooks):
    """The comparison without generated entries: foreign, duplicated and non-owned entries, by their own grammar."""
    report = HooksReport()
    seen_signatures = set()
    for event, items in live_hooks.items():
        for index, item in enumerate(items):
            kind = ownership(item)
            if kind == "foreign":
                report.foreign.append((event, index))
            elif kind == "non-owned":
                report.non_owned.append((event, index))
            else:
                signature = (event, entry_signature(item, True))
                if signature in seen_signatures:
                    report.duplicated.append((event, index, label_of(item)))
                seen_signatures.add(signature)
    return report


def run_uninstall(arguments):
    directory = record_directory()
    hooks_path = os.path.join(cursor_directory(), HOOKS_JSON_NAME)
    refuse_unsafe_targets(cursor_directory(), directory, hooks_path)
    raw, document, live_hooks = read_live_hooks(hooks_path)
    record = record_or_none()
    pre_state, pre_bytes = pre_image_state(directory)
    pre_document = pre_image_document_of(pre_bytes) if pre_state == "present" else None
    record_file_present = os.path.lexists(os.path.join(directory, INSTALL_RECORD_NAME))
    leftovers_present = pre_state is not None or os.path.lexists(os.path.join(directory, MANIFEST_NAME))
    emitted, comparison_failure = entries_for_uninstall()
    report = classify_hooks(emitted, live_hooks, tolerant=True) if emitted is not None else None
    owned_count = sum(1 for items in live_hooks.values() for item in items if ownership(item) == "owned")
    rest_hooks = {}
    for event, items in live_hooks.items():
        kept = [item for item in items if ownership(item) != "owned"]
        if kept:
            rest_hooks[event] = kept
    rest_document = dict(document) if document is not None else None
    if rest_document is not None:
        rest_document["hooks"] = rest_hooks
    restore = None
    if document is not None:
        if (
            pre_state == "present"
            and pre_document is not None
            and prune_empty_arrays(rest_document) == prune_empty_arrays(pre_document)
        ):
            restore = pre_bytes
        elif pre_state == "none" and prune_empty_arrays(rest_document) == {"version": HOOKS_JSON_VERSION, "hooks": {}}:
            restore = "delete"
    rewritten = None
    if document is not None and owned_count and restore is None:
        rewritten = json_bytes_or_refusal(rest_document, hooks_path)
    installed = bool(owned_count) or record_file_present or leftovers_present
    if comparison_failure is not None and owned_count:
        step, cause = comparison_failure
        print(f"{step} ({cause}), so {owned_count} owned entries were not compared.")
    pre_hooks = pre_image_hooks_of(pre_bytes)
    # Non-owned entries are listed against a saved pre-install file, which a later step deletes, so none is listed without one.
    replacing_pre_image = restore is None and pre_state is not None
    if report is None:
        report = fallback_report(live_hooks)
    for line in print_set(
        report, live_hooks, pre_hooks, replacing_pre_image=replacing_pre_image, comparable=emitted is not None, removing=True
    ):
        print(line)
    if document is not None and owned_count and restore is None:
        if pre_state is None:
            reason = "no saved pre-install file exists"
        elif pre_state == "present" and pre_document is None:
            reason = "the saved pre-install file is not a usable hooks.json"
        else:
            reason = "the file differs from the saved one beyond the entries"
        print(
            f"{hooks_path} is not restored from the saved pre-install file ({reason}), "
            "so it is rewritten without the adapter's entries."
        )
    if report.foreign:
        print(
            f"{len(report.foreign)} kept foreign entry(s) still hold an adapter path, so Cursor can still run the adapter "
            f"through them. {FOREIGN_FIX_SENTENCE}"
        )
    # What the closing line must not call gone: a kept foreign entry, a kept manifest path or a left state directory.
    remaining = [f"{len(report.foreign)} kept foreign entry(s) in hooks.json"] if report.foreign else []
    unconfirmed = Unconfirmed()
    progress = ["none"]

    def reach(name):
        progress[0] = name
        checkpoint(name)

    try:
        if document is not None and (owned_count or restore is not None):
            if read_bytes_or_none(hooks_path) != raw:
                raise AdapterError(f"{hooks_path} changed while `uninstall` ran, so it was left as it is. Re-run `uninstall`.")
            mode = stat.S_IMODE(os.stat(hooks_path).st_mode)
            if restore == "delete":
                os.unlink(hooks_path)
                unconfirmed.run(sync_directory_noting, cursor_directory())
            elif restore is not None:
                unconfirmed.run(durable_write, hooks_path, restore, mode)
            else:
                unconfirmed.run(durable_write, hooks_path, rewritten, mode)
        elif document is None and installed:
            print(f"{hooks_path} does not exist, so there are no entries to remove.")
        reach("after-entries")
        manifest = read_manifest(directory)
        if manifest is None:
            if installed:
                print(f"{os.path.join(directory, MANIFEST_NAME)} is missing or unreadable, so no generated file was removed.")
        else:
            accepted, kept = manifest_candidates(manifest.get("files", {}), cursor_directory())
            for path, _ in accepted:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(path)
            for path in kept[:PRINT_SET_MAX_ENTRIES]:
                print(f"Kept {bounded_text(path)}: it is not a file this install wrote, or it changed since.")
            if len(kept) > PRINT_SET_MAX_ENTRIES:
                print(f"{len(kept) - PRINT_SET_MAX_ENTRIES} more kept manifest path(s) not shown")
            if kept:
                remaining.append(f"{len(kept)} kept manifest path(s)")
        reach("after-generated-files")
        if record is not None:
            if not remove_runtime_state(record):
                remaining.append("the runtime state directory")
        elif installed and leftovers_present and not record_file_present:
            print("The install record is missing, so the runtime state may already have been removed by an earlier `uninstall`.")
        elif installed:
            print("The install record is missing or unreadable, so the runtime state could not be located and was left in place.")
            remaining.append("the runtime state directory")
        reach("after-state")
        with contextlib.suppress(FileNotFoundError):
            os.unlink(os.path.join(directory, INSTALL_RECORD_NAME))
        reach("after-record")
        for name in (PRE_IMAGE_NAME, PRE_IMAGE_NONE_NAME, MANIFEST_NAME):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(os.path.join(directory, name))
        sweep_temp_debris(cursor_directory())
        sweep_temp_debris(directory)
        if os.path.isdir(directory):
            unconfirmed.run(sync_directory_noting, directory)
        try:
            os.rmdir(directory)
        except FileNotFoundError:
            pass
        except OSError as error:
            print(f"Left {directory} in place: it could not be removed (cause {load_error_name(error)}).")
        else:
            unconfirmed.run(sync_directory_noting, cursor_directory())
    except Exception as error:
        if progress[0] == "none":
            if isinstance(error, AdapterError):
                raise
            raise AdapterError(
                f"{failing_file_text(error)} failed ({load_error_name(error)}) before `uninstall` wrote anything, so nothing "
                "changed. Fix the cause, then re-run `uninstall`."
            ) from error
        raise post_write_failure(
            error,
            progress[0],
            "hooks.json is already handled, so do not restore it by hand, and leave the saved pre-install file in place. "
            "Fix the cause (permissions or ownership of the file named first). Re-run `uninstall`.",
        ) from error
    for line in unconfirmed.lines():
        print(line)
    remaining_text = f"{', '.join(remaining)} {'remains' if len(remaining) == 1 else 'remain'}"
    if remaining and installed:
        print(
            f"Uninstalled the adapter's own entries, but not everything: {remaining_text}. Relaunch Cursor "
            "so it drops the removed entries, and see the lines above for each remainder."
        )
    elif remaining:
        print(f"Found no {INSTALLED_THINGS_TEXT}, but {remaining_text}. See the lines above for each remainder.")
    elif installed:
        print(
            "Uninstalled. Relaunch Cursor so it drops the removed entries. To confirm, read "
            f"{hooks_path}: it holds no entry that runs the adapter, or it does not exist."
        )
    else:
        print(f"Nothing was installed: no {INSTALLED_THINGS_TEXT} was found.")
    return 0


def read_manifest(directory):
    try:
        manifest = read_json_object(os.path.join(directory, MANIFEST_NAME))
    except AdapterError:
        return None
    return manifest if isinstance(manifest.get("files", {}), dict) else None


def report_state_dir_left(state_dir, reason):
    """`uninstall` removes the install record right after the state directory, so a re-run cannot retry the removal."""
    print(f"Left {state_dir} in place: {reason} The engineer deletes it by hand.")


def remove_runtime_state(record):
    """True when the state directory is gone, False when it was left in place, which it prints with the reason."""
    routed = record["routed_config_dir"]
    state_dir = os.path.join(routed, STATE_DIR_NAME)
    if not os.path.lexists(state_dir):
        return True
    try:
        validate_config_directory(routed)
    except AdapterError:
        report_state_dir_left(state_dir, "its config directory no longer passes the install's checks.")
        return False
    if os.path.islink(state_dir) or not os.path.isdir(state_dir):
        report_state_dir_left(state_dir, "it is not a real directory.")
        return False
    try:
        refuse_unsafe_targets(state_dir)
    except AdapterError:
        report_state_dir_left(
            state_dir,
            "it is not owned by you, is writable by group or others, or a directory on its path is not readable.",
        )
        return False
    shutil.rmtree(state_dir)
    return True


# --- logging, rotation and the first-call marks ---


def rotate_log(state_dir):
    """Renames the log past LOG_ROTATE_BYTES to the one older file kept. Returns True when it rotated.

    A rotator takes a non-blocking exclusive lock first and skips when another rotator holds it, and it checks the size only
    once it holds the lock. It skips when `state_dir` is not a real directory, because `O_NOFOLLOW` guards only the last
    path component."""
    if not load_shim().usable_state_dir(state_dir):
        return False
    lock_path = os.path.join(state_dir, ROTATE_LOCK_NAME)
    try:
        descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        log_path = os.path.join(state_dir, LOG_FILE_NAME)
        try:
            if os.lstat(log_path).st_size <= LOG_ROTATE_BYTES:
                return False
            os.replace(log_path, os.path.join(state_dir, ROTATED_LOG_FILE_NAME))
        except OSError:
            return False
        return True
    finally:
        os.close(descriptor)


def sweep_first_call_marks(state_dir, now):
    """Removes regular first-call marks older than the age bound and returns the count removed.

    A mark's name is a session id, so a file whose name fails that grammar is not a mark and stays. A `first-call` that is
    not a real directory is left alone."""
    directory = os.path.join(state_dir, FIRST_CALL_DIR_NAME)
    removed = 0
    shim = load_shim()
    if not shim.usable_state_dir(state_dir) or not shim.usable_state_dir(directory):
        return 0
    try:
        names = os.listdir(directory)
    except OSError:
        return 0
    for name in names:
        if not shim.is_valid_session_id(name):
            continue
        path = os.path.join(directory, name)
        try:
            status = os.lstat(path)
            if stat.S_ISREG(status.st_mode) and now - status.st_mtime > FIRST_CALL_MARK_MAX_AGE_SECONDS:
                os.unlink(path)
                removed += 1
        except OSError:
            continue
    return removed


class LogRead:
    """The lines `status` read from the log and its one rotated file, and whether a size cap cut either."""

    def __init__(self):
        self.lines = []
        self.cut = False


def read_log_tail(path):
    """(the file's last lines as bytes, whether a cap cut it). A file that cannot be opened has no lines."""
    try:
        descriptor = open_regular_file(path, os.O_NOFOLLOW)
    except OSError:
        return [], False
    with os.fdopen(descriptor, "rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        cut = size > LOG_STATUS_READ_BYTES
        if cut:
            handle.seek(size - LOG_STATUS_READ_BYTES)
        data = handle.read(LOG_STATUS_READ_BYTES)
    lines = data.split(b"\n")
    if cut:
        lines = lines[1:]
    if lines and lines[-1] == b"":
        lines.pop()
    if len(lines) > LOG_STATUS_MAX_LINES:
        lines, cut = lines[-LOG_STATUS_MAX_LINES:], True
    return lines, cut


def parse_log_line(raw_line, shim):
    """One log line as a dict of known field names to values. A value outside the log grammar or past its field's maximum
    becomes `other`, and a line past the shim's line cap is one `other` registration."""
    if len(raw_line) >= shim.LOG_LINE_MAX_BYTES:
        return {"reg": OTHER_LABEL}
    maxima = dict(shim.LOG_FIELDS)
    fields = {}
    for part in raw_line.decode("ascii", errors="replace").split(" "):
        name, separator, value = part.partition("=")
        if not separator or name not in maxima:
            continue
        fits = len(value) <= maxima[name] and re.fullmatch(shim.LOG_VALUE_PATTERN, value) is not None
        fields[name] = value if fits else OTHER_LABEL
    return fields


def read_log(state_dir):
    """The parsed lines of the log and its one rotated file, oldest first, read from each file's tail under the size caps."""
    shim = load_shim()
    result = LogRead()
    for name in (ROTATED_LOG_FILE_NAME, LOG_FILE_NAME):
        raw_lines, cut = read_log_tail(os.path.join(state_dir, name))
        result.cut = result.cut or cut
        for raw_line in raw_lines:
            fields = parse_log_line(raw_line, shim)
            if fields:
                result.lines.append(fields)
    return result


# --- check and status ---


def expected_entries_for(record):
    return generate_entries(python_path=record["tool_paths"]["python3"])


def known_labels_of(emitted):
    return {item.label for item in emitted}


class TamperComparison:
    """What the tamper comparison found. `findings` are faults, `notes` are counts that are expected, and `emitted` is the entry
    list the comparison used, or None when it could not be built."""

    def __init__(self):
        self.findings = []
        self.notes = []
        self.emitted = None


def note_comparison_failure(comparison, error, fix_sentence=""):
    """Adds the finding that a step of the comparison failed, once per cause, because a shim that does not load fails several."""
    text = f"The comparison failed ({failure_cause(error)})."
    if not any(finding.startswith(text) for finding in comparison.findings):
        comparison.findings.append(f"{text} {fix_sentence}" if fix_sentence else text)


def compare_install(record, hooks_path, generation_failure_fix=RESTORE_CHECKOUT_SENTENCE):
    """The tamper comparison `status`, `check` and the sessionStart entry share. It names labels, event names, positions and
    counts only, never an entry's text, and a failure to build the expected entries is a finding, not an exception.
    `generation_failure_fix` ends that finding, so a surface whose reader is not the engineer can attribute the fix.

    The checks that need no generated entries run whether or not generation failed. When it failed, foreign and duplicated
    entries are classified without the generated entries, as `uninstall` does."""
    comparison = TamperComparison()
    try:
        _, document, live_hooks = read_live_hooks(hooks_path)
    except AdapterError as error:
        comparison.findings.append(str(error))
        return comparison
    if document is None:
        comparison.findings.append(f"{hooks_path} does not exist. {TAMPER_FIX_SENTENCE}")
        return comparison
    try:
        comparison.emitted = expected_entries_for(record)
    except LOAD_FAILURES as error:
        note_comparison_failure(comparison, error, generation_failure_fix)
    try:
        pre_state, pre_bytes = pre_image_state(record_directory())
        comparison.findings += top_level_lines(document, (pre_state, pre_bytes))
        if comparison.emitted is not None:
            report = classify_hooks(comparison.emitted, live_hooks, tolerant=False)
        else:
            report = fallback_report(live_hooks)
        comparison.findings += drift_lines(report, known_labels_of(comparison.emitted or []))
        non_owned_findings, comparison.notes = non_owned_lines(
            report, live_hooks, pre_image_hooks_of(pre_bytes), has_owned_entry(live_hooks)
        )
        comparison.findings += non_owned_findings
    except LOAD_FAILURES as error:
        note_comparison_failure(comparison, error)
    try:
        live_map = read_json_object(os.path.join(adapter_directory(), TOOL_MAP_FILE_NAME))
        if record.get("tool_map") != live_map:
            comparison.findings.append(
                "tool-map.json differs from the map the install recorded. Quit Cursor, re-run `install` and relaunch."
            )
        if record.get("contract_version") != load_shim().CONTRACT_VERSION:
            comparison.findings.append(
                "The install record's contract version differs from the shim's: a stale install. "
                "Quit Cursor, re-run `install` and relaunch."
            )
        elif shim_refuses_record():
            comparison.findings.append(
                "The shim refuses the install record (a damaged tool map, a missing `timeout` or `gtimeout`, or a malformed "
                "contract version), so every fail-closed hook denies with the install-record outcome. "
                "Quit Cursor, re-run `install` and relaunch."
            )
    except LOAD_FAILURES as error:
        note_comparison_failure(comparison, error)
    return comparison


def shim_refuses_record():
    """True when the shim's own reader rejects the install record on disk as malformed. A stale contract version is a
    different outcome, which the comparison reports on its own."""
    shim = load_shim()
    try:
        shim.read_install_record(shim.CallState(), require_wrapper=True)
    except shim.Refusal as refusal:
        return refusal.outcome == "install-record"
    return False


def settings_link_findings(record):
    tracked = os.path.realpath(settings_json_path())
    findings = []
    bare_path = os.path.join(home_directory(), ".claude", "settings.json")
    routed_path = os.path.join(record["routed_config_dir"], "settings.json")
    routed_fix_sentence = (
        f"Re-link {routed_path} to the checkout's file. `./install.sh` covers only the bare ~/.claude."
    )
    for label, path, fix_sentence in (
        ("bare ~/.claude/settings.json", bare_path, STOW_FIX_SENTENCE),
        (
            "the routed config dir's settings.json",
            routed_path,
            STOW_FIX_SENTENCE if routed_path == bare_path else routed_fix_sentence,
        ),
    ):
        if os.path.realpath(path) != tracked:
            findings.append(f"{label} does not resolve to {tracked}. {fix_sentence}")
    return findings


def tool_findings(record, command_name):
    """Re-tests the wrapper, the hooks'-PATH python3 and bash floors, and that jq and sha256sum resolve there.

    Returns finding lines, wrapper first.
    """
    findings = []
    tool_paths = record["tool_paths"]
    wrapper_name = next((name for name in WRAPPER_CANDIDATES if name in tool_paths), None)
    if wrapper_name is None:
        findings.append("The install record names neither `timeout` nor `gtimeout`.")
    else:
        cause = wrapper_candidate_test(tool_paths[wrapper_name], command_name)
        if cause is not None:
            findings.append(cause)
    try:
        environment = record_environment(record)
    except LOAD_FAILURES as error:  # a record the shim cannot use
        return [*findings, f"The install record cannot build the hooks' environment ({type(error).__name__})."]
    try:
        bare = find_tool("python3", search_path=environment["PATH"])
        if bare is None:
            findings.append("No `python3` resolves on the PATH the hooks run under.")
        else:
            status, version, start_error = python_floor_status(bare, environment=environment)
            if status != "ok":
                findings.append(floor_text("The `python3` the hooks run", bare, PYTHON_FLOOR, status, version, start_error))
        bash = find_tool("bash", search_path=environment["PATH"])
        if bash is None:
            findings.append("No `bash` resolves on the PATH the hooks run under.")
        else:
            status, version, start_error = bash_floor_status(bash, environment=environment)
            if status != "ok":
                findings.append(floor_text("The `bash` the hooks run", bash, BASH_FLOOR, status, version, start_error))
        for name in ("jq", "sha256sum"):
            if find_tool(name, search_path=environment["PATH"]) is None:
                findings.append(f"No `{name}` resolves on the PATH the hooks run under.")
    except ProbeStalled as error:
        findings.append(stall_text(error.path, command_name))
    return findings


def run_check(arguments):
    record, reason = record_or_reason()
    if record is None:
        print(f"No usable install record: {reason} Run `install` to install the adapter.")
        return 1
    findings = []
    python_path = record["tool_paths"]["python3"]
    python_ok = False
    try:
        status, version, start_error = python_floor_status(python_path)
        shown = "no version"
        if version:
            shown = format_version(version)
        elif start_error:
            shown = f"could not be started, cause {start_error}"
        print(f"recorded python3: {python_path} ({shown})")
        if status != "ok":
            findings.append(floor_text("The recorded python3", python_path, PYTHON_FLOOR, status, version, start_error))
        python_ok = status == "ok"
    except ProbeStalled as error:
        findings.append(stall_text(error.path, "check"))
    findings += tool_findings(record, "check")
    wrapper = next((path for name, path in record["tool_paths"].items() if name in WRAPPER_CANDIDATES), None)
    if python_ok and wrapper is not None:
        try:
            result = run_self_check(python_path, wrapper)
            findings += self_check_failure_lines(result, python_path, wrapper)
        except AdapterError as error:
            findings.append(str(error))
    elif not python_ok:
        findings.append("The self-check did not run, because the recorded python3 did not pass its floor probe.")
    findings += settings_link_findings(record)
    comparison = compare_install(record, os.path.join(cursor_directory(), HOOKS_JSON_NAME))
    findings += comparison.findings
    if comparison.emitted is not None:
        findings += [
            f"{path} is missing, so an entry in hooks.json cannot run: a stale install. "
            f"{STOW_FIX_SENTENCE} Then quit Cursor, re-run `install` and relaunch."
            for path in missing_prerequisites(comparison.emitted)
        ]
    for line in run_mode_lines(record):
        print(line)
    for note in comparison.notes:
        print(f"note: {note}")
    for finding in findings:
        print(f"finding: {finding}")
    if not findings:
        print("no findings")
    return 1 if findings else 0


def hook_env_conversation_id(shim):
    """The session id of the sessionStart payload on stdin, or None when it is absent, invalid or not read in time."""
    try:
        payload = shim.parse_strict_json(shim.read_payload(time.monotonic() + shim.MIN_DEADLINE_SECONDS))
        return shim.session_id_of(payload) if isinstance(payload, dict) else None
    except shim.Refusal, ValueError, OSError:
        return None


def run_hook_env_check():
    """The sessionStart check. Exits 0 and prints one non-empty document whatever it finds."""
    try:
        return hook_env_check_body()
    except Exception as error:  # the informational tail would turn a nonzero exit into `{}` and drop every finding
        document = {
            "additional_context": f"finding: Cursor adapter check failed ({type(error).__name__}). Run `check` from a terminal."
        }
        sys.stdout.write(json.dumps(document) + "\n")
        return 0


def hook_env_check_body():
    started = time.monotonic()
    lines = []
    notes = []
    findings = []
    claude_names = [name for name in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT") if name in os.environ]
    lines.append("Claude Code environment names set in this hook: " + (", ".join(claude_names) or "none"))
    if claude_names:
        lines.append(
            "A Cursor started from a Claude Code terminal passes these names on, and the import's copies of the hooks then "
            "refuse the adapter's pair and deny in linked worktrees. The engineer follows the Recovery section's "
            "CLAUDECODE entry of docs/cursor-adapter.md."
        )
    locale_names = sorted(name for name in os.environ if name in ("LANG", "LC_ALL", "LC_CTYPE") or name.startswith("LC_"))
    lines.append("Locale names set in this hook: " + (", ".join(locale_names) or "none"))
    record = record_or_none()
    if record is None:
        findings.append("The adapter's install record is unreadable. Run `check` from a terminal.")
    else:
        lines += run_mode_lines(record)
        state_dir = state_directory(record)
        try:
            shim = load_shim()
        except LOAD_FAILURES as error:
            shim = None
            findings.append(
                shim_load_failure_text(error, "this check wrote no log line, first-call mark or rotation", AGENT_RESTORE_SENTENCE)
            )
        first_call_dir = os.path.join(state_dir, FIRST_CALL_DIR_NAME)
        state_usable = shim is not None and shim.usable_state_dir(state_dir)
        first_call_usable = state_usable and (not os.path.lexists(first_call_dir) or shim.usable_state_dir(first_call_dir))
        if shim is not None and not state_usable:
            findings.append(
                f"The state directory {bounded_text(state_dir)} is missing or not a real directory, "
                "so this check wrote no log line, first-call mark or rotation."
            )
        elif shim is not None and not first_call_usable:
            findings.append(
                f"The first-call directory under {bounded_text(state_dir)} is not a real directory, "
                "so this check wrote no first-call mark and swept none."
            )
        if state_usable:
            rotate_log(state_dir)
        if first_call_usable:
            sweep_first_call_marks(state_dir, time.time())
        comparison = compare_install(record, os.path.join(cursor_directory(), HOOKS_JSON_NAME), AGENT_RESTORE_SENTENCE)
        findings += comparison.findings
        notes += comparison.notes
        conversation = hook_env_conversation_id(shim) if shim is not None else None
        fields = {
            "ts": int(time.time()),
            "reg": "check-hook-env",
            "ev": "sessionStart",
            "out": "tamper" if comparison.findings else "ok",
            "ms": int((time.monotonic() - started) * 1000),
            "ver": record.get("contract_version", ""),
            "runmode": run_mode_label(record),
        }
        log_line_written = False
        if state_usable:
            with contextlib.suppress(OSError, ValueError):
                shim.write_log_line(state_dir, fields)
                log_line_written = True
        if conversation is not None and first_call_usable and log_line_written:
            shim.claim_first_call(state_dir, conversation)
        try:
            findings += tool_findings(record, "check")
        except Exception as error:  # a failed re-test never costs the report
            findings.append(f"The tool re-test failed ({type(error).__name__}).")
    lines += [f"note: {note}" for note in notes]
    if findings:
        lines.append(AGENT_RESTRAINT_SENTENCE)
    lines += [f"finding: {finding}" for finding in findings]
    if not findings:
        lines.append("no findings")
    document = {"additional_context": "Cursor adapter check:\n" + "\n".join(lines)}
    sys.stdout.write(json.dumps(document) + "\n")
    return 0


def shell_selecting_registrations_without_a_fire(emitted, fire_counts):
    """Labels of the preToolUse shim entries whose matcher selects `Shell` and that no log line names."""
    labels = []
    for item in emitted:
        parsed = parse_command(item.entry["command"])
        matcher = item.entry.get("matcher") or ""
        selects_shell = parsed.form == "shim" and item.event == "preToolUse" and re.search(matcher, "Shell")
        if selects_shell and not fire_counts.get(item.label):
            labels.append(item.label)
    return labels


def print_comparison(comparison):
    for note in comparison.notes:
        print(f"note: {note}")
    for line in comparison.findings:
        print(f"finding: {line}")


def run_status(arguments):
    record, reason = record_or_reason()
    if record is None:
        print(f"No usable install record: {reason} Run `install` to install the adapter.")
        return 1
    comparison = compare_install(record, os.path.join(cursor_directory(), HOOKS_JSON_NAME))
    try:
        shim = load_shim()
    except LOAD_FAILURES as error:
        print(f"hook-shim.py did not load ({type(error).__name__}), so `status` cannot read the log. Run `check`.")
        print_comparison(comparison)
        return 1
    state_dir = state_directory(record)
    known_labels = ({item.label for item in comparison.emitted} if comparison.emitted else set()) | {"check-hook-env"}
    if shim.usable_state_dir(state_dir):
        log = read_log(state_dir)
    else:
        log = LogRead()
        print(f"The state directory {bounded_text(state_dir)} is missing or not a real directory, so `status` read no log.")
    lines = log.lines
    print(f"recorded python3: {record['tool_paths']['python3']}")
    try:
        bare = find_tool("python3", search_path=record_environment(record)["PATH"])
        print(f"hooks' python3: {bare or 'none resolves'}")
    except LOAD_FAILURES as error:  # a record the shim cannot use
        print(f"hooks' python3: unavailable ({type(error).__name__})")
    for line in run_mode_lines(record):
        print(line)
    if log.cut:
        print(f"note: the log is larger than `status` reads, so these counts cover its last {LOG_STATUS_MAX_LINES} lines at most")
    fire_counts = {}
    for fields in lines:
        label = fields.get("reg", OTHER_LABEL)
        if label != shim.LOG_OVERFLOW_TOKEN:
            label = safe_label(label, known_labels)
        fire_counts[label] = fire_counts.get(label, 0) + 1
    for label, count in sorted(fire_counts.items()):
        if label == shim.LOG_OVERFLOW_TOKEN:
            print(f"anomaly: {count} log line(s) carry the overflow token as a registration")
        else:
            print(f"registration {label}: {count} call(s)")
    if comparison.emitted and any(fields.get("tool") == "Shell" for fields in lines):
        for label in shell_selecting_registrations_without_a_fire(comparison.emitted, fire_counts):
            print(
                f"flag: the Shell-selecting registration {label} has not fired in the lines read, though other Shell calls "
                "logged. Run `check` to compare the entries with what the checkout generates."
            )
    for outcome, name in (("pin-check", "pin-check denies"), ("stale-install", "stale-install calls"), ("usage", "usage faults")):
        count = sum(1 for fields in lines if fields.get("out") == outcome)
        if count:
            print(f"{name}: {count}")
    fault_counts = {}
    for fields in lines:
        if fields.get("out") == "shim-fault":
            kind = (fields.get("fault", OTHER_LABEL), fields.get("py", "unknown"))
            fault_counts[kind] = fault_counts.get(kind, 0) + 1
    ranked_faults = sorted(fault_counts.items(), key=lambda item: (-item[1], item[0]))
    for (fault, python_version), count in ranked_faults[:PRINT_SET_MAX_ENTRIES]:
        print(f"shim fault: class {fault} on python {python_version}: {count} call(s)")
    if len(ranked_faults) > PRINT_SET_MAX_ENTRIES:
        print(f"{len(ranked_faults) - PRINT_SET_MAX_ENTRIES} more shim fault kind(s) not shown")
    log_path = os.path.join(state_dir, LOG_FILE_NAME)
    with contextlib.suppress(OSError):
        if os.lstat(log_path).st_size > LOG_ROTATE_BYTES:
            print(
                "flag: the log is past the rotation threshold. The next conversation's check rotates it, and one long healthy "
                "conversation can trip this flag, so it needs no action unless it stays after a new conversation."
            )
    check_lines = [index for index, fields in enumerate(lines) if fields.get("reg") == "check-hook-env"]
    after_check = lines[check_lines[-1] + 1 :] if check_lines else lines
    first_lines = [fields for fields in after_check if "py" in fields and fields.get("reg") != "check-hook-env"]
    if len(first_lines) >= 2:
        print(
            "flag: the sessionStart entry looks silent: two or more shim first lines follow its last check line. "
            "Run `check`, and if it reports no finding, quit Cursor, re-run `install` and relaunch."
        )
    print_comparison(comparison)
    return 0


# --- generate ---


def run_generate(arguments):
    load_shim_or_refuse("generate", GENERATE_RESTORE_SENTENCE)
    python_path = arguments.python or (record_or_none() or {"tool_paths": {}})["tool_paths"].get("python3") or sys.executable
    if arguments.print_entry:
        if not arguments.shim:
            raise AdapterError("--print-entry needs --shim PATH.")
        emitted = generate_entries(python_path=python_path, shim_path=arguments.shim, only_key=arguments.print_entry)
    else:
        emitted = generate_entries(python_path=python_path)
    sys.stdout.write(json_bytes({"version": HOOKS_JSON_VERSION, "hooks": hooks_object(emitted)}).decode("ascii"))
    return 0


# --- command line ---


def build_parser():
    parser = argparse.ArgumentParser(prog=ADAPTER_FILE_NAME, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate", allow_abbrev=False)
    generate.add_argument("--print-entry")
    generate.add_argument("--shim")
    generate.add_argument("--python")
    install = commands.add_parser("install", allow_abbrev=False)
    install.add_argument("--config-dir", required=True)
    install.add_argument("--acceptance-only", action="store_true")
    commands.add_parser("uninstall", allow_abbrev=False)
    check = commands.add_parser("check", allow_abbrev=False)
    check.add_argument("--hook-env", action="store_true")
    commands.add_parser("status", allow_abbrev=False)
    self_check = commands.add_parser(SELF_CHECK_SUBCOMMAND, allow_abbrev=False)
    self_check.add_argument("--wrapper", required=True)
    return parser


def main(argv=None):
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.command == "generate":
            return run_generate(arguments)
        if arguments.command == "install":
            return run_install(arguments)
        if arguments.command == "uninstall":
            return run_uninstall(arguments)
        if arguments.command == "check":
            return run_hook_env_check() if arguments.hook_env else run_check(arguments)
        if arguments.command == "status":
            return run_status(arguments)
        return run_self_check_subcommand(arguments.wrapper)
    except PartialWriteError as error:
        print(f"failed after writes began: {error}", file=sys.stderr)
        return PARTIAL_WRITE_EXIT_STATUS
    except AdapterError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        frame = traceback.extract_tb(error.__traceback__)[-1]
        located = f"{type(error).__name__} at {os.path.basename(frame.filename)}:{frame.lineno} in {frame.name}"
        nothing_written = " Nothing was written." if arguments.command in ("install", "uninstall") else ""
        print(f"`{arguments.command}` failed on an unexpected {located}.{nothing_written}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
