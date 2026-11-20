"""The hook log, the `check` and `status` commands and the sessionStart check."""

import contextlib
import fcntl
import json
import os
import re
import stat
import subprocess
import sys
import time

from .core import (
    AGENT_RESTORE_SENTENCE,
    AGENT_RESTRAINT_SENTENCE,
    BASH_FLOOR,
    CURSOR_VERSION_CHANGED_LINE,
    CURSOR_VERSION_OTHER,
    CURSOR_VERSION_PATTERN,
    CURSOR_VERSIONS_DIR_NAME,
    FIRST_CALL_DIR_NAME,
    FIRST_CALL_MARK_MAX_AGE_SECONDS,
    HOOKS_JSON_NAME,
    HOST_WALK_BOUND_SECONDS,
    HOST_WALK_CLAUDE_FIRST,
    HOST_WALK_CLAUDE_FIRST_LINE,
    HOST_WALK_FOUND,
    HOST_WALK_NOT_FOUND,
    HOST_WALK_NOT_FOUND_ENTRY,
    HOST_WALK_NOT_FOUND_LINE,
    HOST_WALK_STATUS_CLAUDE_FIRST,
    LOAD_FAILURES,
    LOG_FILE_NAME,
    LOG_ROTATE_BYTES,
    LOG_STATUS_MAX_LINES,
    LOG_STATUS_READ_BYTES,
    OTHER_LABEL,
    PRINT_SET_MAX_ENTRIES,
    PYTHON_FLOOR,
    RESTORE_CHECKOUT_SENTENCE,
    ROTATE_LOCK_NAME,
    ROTATED_LOG_FILE_NAME,
    SANDBOX_GRANT_PENDING_LINE,
    STOW_FIX_SENTENCE,
    TAMPER_FIX_SENTENCE,
    TOOL_MAP_FILE_NAME,
    WRAPPER_CANDIDATES,
    AdapterError,
    ProbeStalled,
    adapter_directory,
    cursor_directory,
    home_directory,
    load_shim,
    open_regular_file,
    probe_directory,
    read_json_object,
    record_directory,
    settings_json_path,
    stow_hook_path,
)
from .hooks_json import (
    bounded_text,
    classify_hooks,
    drift_lines,
    generate_entries,
    non_owned_lines,
    parse_command,
    pre_image_hooks_of,
    read_live_hooks,
    safe_label,
    top_level_lines,
)
from .install import has_owned_entry, missing_prerequisites, pre_image_state, run_mode_label, run_mode_lines
from .sandbox import grant_pending, promoted_sandbox_record, recorded_while_pending_text, sandbox_comparison_lines
from .tools import (
    bash_floor_status,
    find_tool,
    floor_text,
    format_version,
    python_floor_status,
    record_environment,
    record_or_none,
    record_or_reason,
    run_self_check,
    self_check_failure_lines,
    shim_load_failure_text,
    stall_text,
    state_directory,
    wrapper_candidate_test,
)
from .uninstall import failure_cause, fallback_report

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
        add_sandbox_comparison(comparison)
        return comparison
    if document is None:
        comparison.findings.append(f"{hooks_path} does not exist. {TAMPER_FIX_SENTENCE}")
        add_sandbox_comparison(comparison)
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
    add_sandbox_comparison(comparison)
    return comparison


def add_sandbox_comparison(comparison):
    """The sandbox grant's comparison, which no state of hooks.json skips."""
    sandbox_findings, sandbox_notes = sandbox_comparison_lines(record_directory())
    comparison.findings += sandbox_findings
    comparison.notes += sandbox_notes


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


def hook_env_payload(shim):
    """The sessionStart payload object on stdin, or None when it is absent, not an object or not read in time."""
    try:
        payload = shim.parse_strict_json(shim.read_payload(time.monotonic() + shim.MIN_DEADLINE_SECONDS))
    except shim.Refusal, ValueError, OSError:
        return None
    return payload if isinstance(payload, dict) else None


def hook_env_conversation_id(shim, payload):
    """The session id of the sessionStart payload, or None when there is none or it is invalid."""
    if payload is None:
        return None
    try:
        return shim.session_id_of(payload)
    except shim.Refusal:
        return None


def host_walk_result(record, shim):
    """One of the HOST_WALK_* tokens, from the same walk the shim's `pin` mode runs. Any failure to run it is `not-found`."""
    bash = record["tool_paths"].get("bash")
    if bash is None:
        return HOST_WALK_NOT_FOUND
    try:
        completed = subprocess.run(
            [bash, "-c", shim.HOST_WALK_PROGRAM, "walk", stow_hook_path("_lib.sh")],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=probe_directory(),
            env=record_environment(record),
            timeout=HOST_WALK_BOUND_SECONDS,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return HOST_WALK_NOT_FOUND
    if completed.returncode == 0:
        return HOST_WALK_FOUND
    return HOST_WALK_CLAUDE_FIRST if completed.returncode == HOST_WALK_STATUS_CLAUDE_FIRST else HOST_WALK_NOT_FOUND


def cursor_version_token(version):
    """The version as the record file name and the log field hold it: itself when it fits the grammar, else `other`."""
    if CURSOR_VERSION_PATTERN.fullmatch(version) and version.strip(".") != "":
        return version
    return CURSOR_VERSION_OTHER


def cursor_version_changed(shim, state_dir, token):
    """True when `token` is a version to report: one the state dir's record did not hold, after it held another.

    The record is one file per seen version, made by an exclusive create, so of two conversations that see the change
    only the one whose create wins reports it. A first sight records and stays quiet. A record that cannot be read or
    written reports a change, which sends the engineer to the checklist."""
    directory = os.path.join(state_dir, CURSOR_VERSIONS_DIR_NAME)
    try:
        with contextlib.suppress(FileExistsError):
            os.mkdir(directory, 0o700)
        if not shim.usable_state_dir(directory):
            return True
        held_a_version = bool(os.listdir(directory))
        descriptor = os.open(
            os.path.join(directory, token), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
        )
    except FileExistsError:
        return False
    except (OSError, ValueError):
        return True
    with contextlib.suppress(OSError):
        os.close(descriptor)
    return held_a_version


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
        payload = hook_env_payload(shim) if shim is not None else None
        host_walk = host_walk_result(record, shim) if shim is not None else ""
        host_walk_lines = {
            HOST_WALK_NOT_FOUND: HOST_WALK_NOT_FOUND_LINE,
            HOST_WALK_CLAUDE_FIRST: HOST_WALK_CLAUDE_FIRST_LINE,
        }
        if host_walk:
            lines.append(f"Host walk: {host_walk}.")
            if host_walk in host_walk_lines:
                lines.append(host_walk_lines[host_walk])
        reported_version = ""
        payload_version = payload.get("cursor_version") if payload is not None else None
        if state_usable and isinstance(payload_version, str):
            token = cursor_version_token(payload_version)
            if cursor_version_changed(shim, state_dir, token):
                reported_version = token
                lines.append(CURSOR_VERSION_CHANGED_LINE.format(version=token))
        comparison = compare_install(record, os.path.join(cursor_directory(), HOOKS_JSON_NAME), AGENT_RESTORE_SENTENCE)
        findings += comparison.findings
        notes += comparison.notes
        conversation = hook_env_conversation_id(shim, payload) if shim is not None else None
        fields = {
            "ts": int(time.time()),
            "reg": "check-hook-env",
            "ev": "sessionStart",
            "out": "tamper" if comparison.findings else "ok",
            "ms": int((time.monotonic() - started) * 1000),
            "ver": record.get("contract_version", ""),
            "runmode": run_mode_label(record),
            "hostwalk": host_walk,
            "cursorver": reported_version,
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
    if grant_pending():
        recorded_paths = promoted_sandbox_record(record_directory())
        if recorded_paths:
            pending_text, live = recorded_while_pending_text(recorded_paths)
            print(f"sandbox grant: pending{', and still in force' if live else ''}. {pending_text}")
        else:
            print(SANDBOX_GRANT_PENDING_LINE)
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
    not_found_walks = sum(1 for fields in lines if fields.get("hostwalk") == HOST_WALK_NOT_FOUND)
    if not_found_walks:
        print(
            f"flag: the sessionStart entry's host walk found no Cursor or CLI host in {not_found_walks} conversation(s). "
            f"The engineer follows the Recovery section's \"{HOST_WALK_NOT_FOUND_ENTRY}\" entry in docs/cursor-adapter.md."
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
