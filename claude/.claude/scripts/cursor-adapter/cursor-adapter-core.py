# cursor-adapter.py executes this file into its own namespace, so names it or its other parts define need no import here.

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
# A project choice: how long the sessionStart entry's host walk may run.
# With the stdin read and the three probes at their bound, it keeps the entry inside CHECK_ENTRY_TIMEOUT_SECONDS.
HOST_WALK_BOUND_SECONDS = 1
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
CURSOR_VERSIONS_DIR_NAME = "cursor-versions"
GENERATED_FILE_DIRECTORIES = ("agents", "rules")
SANDBOX_JSON_NAME = "sandbox.json"
SANDBOX_PATHS_KEY = "additionalReadwritePaths"
SANDBOX_PRE_IMAGE_NAME = "sandbox.json.pre-install"
SANDBOX_PRE_IMAGE_NONE_NAME = "sandbox.json.pre-install.none"
SANDBOX_PENDING_NAME = "sandbox.pending.json"
# The grant waits while this parity entry is listed in the parity exceptions, whatever its reason, because that gate is what
# denies a `marker.sh` shape the engineer's permissions do not allow in a mode that gives no prompt.
SANDBOX_GRANT_PREREQUISITE = ("adapter-gate", "gate-settings-permissions.sh")
SANDBOX_KEPT_SENTENCE = "Its saved pre-install file and the record stay, so a later `uninstall` can finish."
SANDBOX_GRANT_PENDING_LINE = (
    "sandbox grant: pending. `install` writes no sandbox.json until the permissions gate (gate-settings-permissions.sh) is "
    "delivered, so an agent Shell call under a sandboxed mode cannot write a marker."
)
SANDBOX_GRANT_PREREQUISITE_PENDING_TEXT = "while its prerequisite (gate-settings-permissions.sh) is pending"
SANDBOX_GRANT_MARKER_WRITE_CLAUSE = "so an agent Shell call under a sandboxed mode can write a marker that no gate denies"
# The routed config dir's directories that the agent-run identity consumers write: marker.sh, pr-diff-against-base.sh, the
# review-pr scripts and review-ledger.sh. The routed config dir itself is never listed.
# `.handoff-nudge.log`, which handoff-record-conversion.sh appends to directly in that dir, is left out by name.
SANDBOX_WRITABLE_DIRECTORIES = (
    "code-review-markers",
    "code-review-diff-markers",
    "skill-review-markers",
    "plan-review-markers",
    "ready-for-review-markers",
    "cumulative-review-markers",
    "cumulative-review-subject-markers",
    "cumulative-review-diff-markers",
    "review-pr-markers",
    "verification-markers",
    ".plan-review-active.d",
    ".ready-for-review-active.d",
    ".respond-pr-active.d",
    ".memory-skill-active.d",
    ".handoff-active.d",
    ".plan-review-routing-read.d",
    ".plan-review-pending-read.d",
    ".review-pr-active.d",
    "review-narrative-ledger",
)
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

# The shim's command-line vocabulary. `parse_command` reads it without loading the shim, and a test ties it to the
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

# The host walk's results, as the sessionStart entry's `hostwalk` log field and its report hold them.
HOST_WALK_FOUND = "found"
HOST_WALK_NOT_FOUND = "not-found"
HOST_WALK_CLAUDE_FIRST = "claude-first"
# The exit status `_lib_cursor_host_pid` gives when its walk stops at `claude` with no CLI process remembered.
HOST_WALK_STATUS_CLAUDE_FIRST = 3
# The token a `cursor_version` is recorded and reported by when it is empty, is dots only, is too long, or holds a character
# outside the grammar. Every such value shares this one record, so after the first, a change to another of them reports nothing.
CURSOR_VERSION_OTHER = "other"
CURSOR_VERSION_PATTERN = re.compile(r"[0-9A-Za-z.+-]{1,64}", re.ASCII)
CURSOR_VERSION_CHANGED_LINE = (
    "Cursor version changed to {version}. The Cursor-update regression checklist in docs/cursor-adapter.md applies. "
    "The engineer, not the agent, runs it."
)
# The heading of the Recovery entry in docs/cursor-adapter.md that both not-found messages name.
HOST_WALK_NOT_FOUND_ENTRY = "A host walk that found no host"
HOST_WALK_NOT_FOUND_LINE = (
    "The host walk found no Cursor or CLI process above this hook, so the adapter's identity has no anchor in this conversation. "
    f"The engineer follows the Recovery section's \"{HOST_WALK_NOT_FOUND_ENTRY}\" entry in docs/cursor-adapter.md."
)
HOST_WALK_CLAUDE_FIRST_LINE = (
    "The host walk reached a Claude Code process before any Cursor or CLI process, so identity resolves through Claude Code."
)

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
    """The shim module, loaded once per path. The default is the file beside `cursor-adapter.py`."""
    shim_path = str(path) if path is not None else os.path.join(adapter_directory(), SHIM_FILE_NAME)
    if shim_path not in _shim_modules:
        _shim_modules[shim_path] = compile_module("cursor_hook_shim", shim_path)
    return _shim_modules[shim_path]


# --- files ---


def parse_json_bytes(data):
    """The shim's strict parse of `data`, so the adapter and the shim agree on what a usable JSON file is. Raises ValueError.

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
