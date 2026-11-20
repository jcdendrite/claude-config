"""Generating the hooks.json entries and comparing them with the live ones."""

import functools
import json
import os
import re
import shlex
import stat

from .core import (
    ASK_ON_WRITE_SCRIPTS,
    CHECK_ARGUMENTS,
    CHECK_ENTRY_TIMEOUT_SECONDS,
    CURSOR_EVENT_FOR_CLAUDE_EVENT,
    CURSOR_TOOL_NAMES,
    FAIL_CLOSED_EVENTS,
    FAULT_TEXT,
    FOREIGN_FIX_SENTENCE,
    HOOKS_JSON_ENTRY_KEYS,
    HOOKS_JSON_EVENTS,
    HOOKS_JSON_VERSION,
    PARITY_EXCEPTIONS_FILE_NAME,
    PRINT_SET_MAX_ENTRIES,
    PRINT_VALUE_MAX_CHARS,
    SHIM_MODES,
    SHIM_SWITCH_FLAGS,
    SHIM_VALUE_FLAGS,
    TAMPER_FIX_SENTENCE,
    TOOL_EVENTS,
    TOP_LEVEL_FIX_SENTENCE,
    VERSION_FIX_SENTENCE,
    AdapterError,
    adapter_directory,
    load_error_name,
    load_shim,
    parse_json_bytes,
    read_bytes_or_refuse,
    read_json_object,
    settings_json_path,
    stow_adapter_path,
    stow_hook_path,
    stow_shim_path,
)

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
    shim's arguments, or the adapter's stow path with exactly `check --hook-env` under the informational tail. A command
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
