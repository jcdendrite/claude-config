# cursor-adapter.py executes this file into its own namespace, so names its other parts define need no import here.

# --- the sandbox grant ---
#
# The user-level ~/.cursor/sandbox.json lets an agent Shell call under a sandboxed mode write the state the gates read. The
# grant is the listed directories of the routed config dir, added to the engineer's own `additionalReadwritePaths`.


def grant_exceptions_path():
    return os.path.join(adapter_directory(), PARITY_EXCEPTIONS_FILE_NAME)


def grant_pending():
    """True unless the parity file affirmatively shows the permissions gate, the grant's prerequisite, is delivered.

    Delivered means the file is an object whose `entries` is a list of objects and none of them is an exception for the
    gate, whatever its reason. A file that is unreadable or has any other shape counts as pending, so no grant is written
    on a build that cannot say."""
    try:
        entries = read_json_object(grant_exceptions_path()).get("entries")
    except AdapterError:
        return True
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        return True
    return any(
        entry.get("kind") == SANDBOX_GRANT_PREREQUISITE[0] and entry.get("key") == SANDBOX_GRANT_PREREQUISITE[1]
        for entry in entries
    )


def sandbox_directories(routed_config_dir):
    return [os.path.join(routed_config_dir, name) for name in SANDBOX_WRITABLE_DIRECTORIES]


def refuse_unsafe_sandbox_directories(routed_config_dir):
    """Refuses, naming the path, a listed directory that is a symlink or exists as something other than a directory."""
    for path in sandbox_directories(routed_config_dir):
        if os.path.islink(path):
            raise AdapterError(f"{path} is a symlink, and the sandbox grant lists real directories only. Remove it, then re-run.")
        if os.path.lexists(path) and not os.path.isdir(path):
            raise AdapterError(f"{path} exists and is not a directory, so the sandbox grant cannot list it. Remove it.")


def sandbox_json_path():
    return os.path.join(cursor_directory(), SANDBOX_JSON_NAME)


def sandbox_paths_of(document):
    paths = document.get(SANDBOX_PATHS_KEY, []) if isinstance(document, dict) else None
    return paths if isinstance(paths, list) and all(isinstance(item, str) for item in paths) else None


def read_live_sandbox(path, refused_outcome="nothing was installed"):
    """(the file's bytes, its document), or (None, None) when it is absent. Raises AdapterError, naming the file and ending
    its reason in `refused_outcome`, for one that does not parse, is not an object, or whose `additionalReadwritePaths` is
    not a list of strings."""
    raw = read_bytes_or_refuse(path)
    if raw is None:
        return None, None
    try:
        document = parse_json_bytes(raw)
    except ValueError as error:
        raise AdapterError(
            f"{path} does not parse as JSON, so {refused_outcome}. Fix or remove it, then re-run."
        ) from error
    if not isinstance(document, dict) or sandbox_paths_of(document) is None:
        raise AdapterError(
            f"{path} is not an object whose {SANDBOX_PATHS_KEY} is a list of strings, so {refused_outcome}. "
            "Fix or remove it, then re-run."
        )
    return raw, document


def ordered_union(first, second):
    return list(dict.fromkeys([*first, *second]))


def pending_sandbox_record(directory):
    """The paths an interrupted install recorded as its additions, or [] when there is no usable pending record."""
    try:
        paths = read_json_object(os.path.join(directory, SANDBOX_PENDING_NAME)).get("added")
    except AdapterError:
        return []
    return paths if isinstance(paths, list) and all(isinstance(item, str) for item in paths) else []


def promoted_sandbox_record(directory):
    manifest = read_manifest(directory)
    paths = manifest.get("sandbox_paths") if manifest is not None else None
    return paths if isinstance(paths, list) and all(isinstance(item, str) for item in paths) else []


def recorded_sandbox_paths(directory):
    """Every path an earlier install recorded as its own addition, promoted or still pending."""
    return ordered_union(promoted_sandbox_record(directory), pending_sandbox_record(directory))


class SandboxPlan:
    """What an install does to sandbox.json: the live file, the paths it adds, and every path it then owns."""

    def __init__(self, path, raw, document, to_add, owned, first_grant):
        self.path = path
        self.raw = raw
        self.document = document
        self.to_add = to_add
        self.owned = owned
        self.first_grant = first_grant


def plan_sandbox_grant(routed_config_dir, directory):
    """What the install would do to sandbox.json, or None while the grant is pending. Reads and refuses; writes nothing."""
    if grant_pending():
        return None
    refuse_unsafe_sandbox_directories(routed_config_dir)
    path = sandbox_json_path()
    refuse_unsafe_targets(path)
    raw, document = read_live_sandbox(path)
    live_paths = sandbox_paths_of(document) if document is not None else []
    previous = recorded_sandbox_paths(directory)
    to_add = [item for item in sandbox_directories(routed_config_dir) if item not in live_paths]
    pre_image_names = (SANDBOX_PRE_IMAGE_NAME, SANDBOX_PRE_IMAGE_NONE_NAME)
    pre_image_kept = any(os.path.lexists(os.path.join(directory, name)) for name in pre_image_names)
    first_grant = not previous and not pre_image_kept
    return SandboxPlan(path, raw, document, to_add, ordered_union(previous, to_add), first_grant)


def merge_sandbox(document, to_add):
    merged = dict(document) if document is not None else {}
    merged[SANDBOX_PATHS_KEY] = [*merged.get(SANDBOX_PATHS_KEY, []), *to_add]
    return merged


def engineer_listed_sandbox_directories(plan, routed_config_dir):
    """The grant's directories the live file lists that the install does not own, so `uninstall` keeps them."""
    live_paths = sandbox_paths_of(plan.document) if plan.document is not None else []
    return [item for item in sandbox_directories(routed_config_dir) if item in live_paths and item not in plan.owned]


def write_sandbox_records(plan, directory, unconfirmed):
    """The sandbox part of `install`'s writes between the hooks.json pre-image and the install record: the sandbox.json
    pre-image, then the pending record of the paths about to be added, each durable.

    A re-run that finds either keeps it, because the live file may already be widened."""
    if not plan.owned:
        return
    if plan.first_grant:
        if plan.raw is None:
            unconfirmed.run(durable_write, os.path.join(directory, SANDBOX_PRE_IMAGE_NONE_NAME), b"")
        else:
            unconfirmed.run(durable_write, os.path.join(directory, SANDBOX_PRE_IMAGE_NAME), plan.raw)
        checkpoint("after-sandbox-pre-image")
    if plan.owned != promoted_sandbox_record(directory):
        unconfirmed.run(durable_write, os.path.join(directory, SANDBOX_PENDING_NAME), json_bytes({"added": plan.owned}))


def write_sandbox_file(plan, routed_config_dir, unconfirmed):
    """Install's sandbox part after hooks.json: creates the listed directories, then writes the merged file by the same
    re-read and atomic rename as hooks.json."""
    for path in sandbox_directories(routed_config_dir):
        make_private_directory(path)
    checkpoint("after-sandbox-dirs")
    if not plan.to_add:
        return
    if read_bytes_or_none(plan.path) != plan.raw:
        raise PartialWriteError(
            f"{plan.path} changed while `install` ran, so it was left as it is. The adapter's files may be partly written. "
            "Run `uninstall`, then re-run `install`."
        )
    mode = stat.S_IMODE(os.stat(plan.path).st_mode) if plan.raw is not None else 0o600
    unconfirmed.run(durable_write, plan.path, json_bytes(merge_sandbox(plan.document, plan.to_add)), mode)


def sandbox_restore_plan(directory):
    """(the action, bytes or None, a line to print, the live bytes the action was planned from) for `uninstall`'s sandbox
    step.

    The action is `nothing`, `blocked`, `restore`, `delete` or `rewrite`. Any failure to read or plan the file is `blocked`,
    which keeps the record and leaves every other step to run."""
    recorded = recorded_sandbox_paths(directory)
    if not recorded:
        return "nothing", None, "", None
    path = sandbox_json_path()
    try:
        refuse_unsafe_targets(path)
        raw, document = read_live_sandbox(path, refused_outcome="`uninstall` did not remove the sandbox grant")
        if document is None:
            return "nothing", None, "", None
        rest = dict(document)
        rest[SANDBOX_PATHS_KEY] = [item for item in sandbox_paths_of(document) if item not in recorded]
        pre_bytes = read_bytes_or_refuse(os.path.join(directory, SANDBOX_PRE_IMAGE_NAME))
        if pre_bytes is not None:
            with contextlib.suppress(ValueError, RecursionError):
                pre_document = parse_json_bytes(pre_bytes)
                comparable = dict(rest)
                # The merge adds the paths key to a file that lacked it, so an empty list there is the file as it was.
                if isinstance(pre_document, dict) and SANDBOX_PATHS_KEY not in pre_document and rest[SANDBOX_PATHS_KEY] == []:
                    del comparable[SANDBOX_PATHS_KEY]
                if pre_document == comparable:
                    return "restore", pre_bytes, "", raw
        elif os.path.lexists(os.path.join(directory, SANDBOX_PRE_IMAGE_NONE_NAME)) and rest == {SANDBOX_PATHS_KEY: []}:
            return "delete", None, "", raw
        if rest == document:
            return "nothing", None, "", None
        return "rewrite", json_bytes_or_refusal(rest, path), "", raw
    except AdapterError as error:
        return "blocked", None, f"{error} {SANDBOX_KEPT_SENTENCE}", None


def apply_sandbox_action(action, data, planned_from, unconfirmed):
    """`uninstall`'s sandbox write. None when it was written, else a line saying why it was left as it is.

    The live file is re-read first, as hooks.json is, so an edit made since the plan is never overwritten."""
    path = sandbox_json_path()
    try:
        if read_bytes_or_none(path) != planned_from:
            return f"{path} changed while `uninstall` ran, so it was left as it is. {SANDBOX_KEPT_SENTENCE}"
        if action == "delete":
            os.unlink(path)
            unconfirmed.run(sync_directory_noting, cursor_directory())
        else:
            unconfirmed.run(durable_write, path, data, stat.S_IMODE(os.stat(path).st_mode))
    except OSError as error:
        return f"{path} could not be changed ({load_error_name(error)}), so it was left as it is. {SANDBOX_KEPT_SENTENCE}"
    return None


def live_recorded_sandbox_count(recorded):
    """How many of the recorded paths the live sandbox.json lists: 0 when it is absent, None when it is unusable."""
    try:
        _, document = read_live_sandbox(sandbox_json_path())
    except AdapterError:
        return None
    live_paths = sandbox_paths_of(document) if document is not None else []
    return sum(1 for item in recorded if item in live_paths)


def recorded_while_pending_text(recorded):
    """(text, live): what a recorded grant whose prerequisite is pending again says. `live` is the count of recorded paths
    the file lists now, None when the file is unusable. Only a count above zero says a Shell call can write a marker."""
    live = live_recorded_sandbox_count(recorded)
    lead = f"{len(recorded)} path(s) are recorded as the sandbox grant, and "
    if live is None:
        lead += f"{SANDBOX_JSON_NAME} is unusable, so how many are listed now is unknown"
    else:
        lead += f"{live} of them are listed in {SANDBOX_JSON_NAME} now"
    text = f"{lead}, {SANDBOX_GRANT_PREREQUISITE_PENDING_TEXT}"
    return (f"{text}, {SANDBOX_GRANT_MARKER_WRITE_CLAUSE}." if live else f"{text}."), live


def sandbox_comparison_lines(directory):
    """(findings, notes) comparing the live sandbox.json with what the recorded install wrote.

    Counts and fixed labels only, so no key or path text of the file reaches a report."""
    recorded = promoted_sandbox_record(directory)
    if not recorded:
        return [], []
    findings = []
    if grant_pending():
        pending_text, live = recorded_while_pending_text(recorded)
        findings.append(f"{pending_text} `uninstall` removes the grant." if live else pending_text)
    try:
        _, document = read_live_sandbox(sandbox_json_path())
    except AdapterError:
        unusable = f"{SANDBOX_JSON_NAME} does not parse or has the wrong shape, so the sandbox grant cannot be compared."
        return [*findings, unusable], []
    if document is None:
        return [*findings, f"{SANDBOX_JSON_NAME} is missing, so the sandbox grant is gone."], []
    live_paths = sandbox_paths_of(document)
    try:
        pre_bytes = read_bytes_or_none(os.path.join(directory, SANDBOX_PRE_IMAGE_NAME))
    except OSError as error:
        unreadable = (
            f"{SANDBOX_PRE_IMAGE_NAME} cannot be read ({load_error_name(error)}), so the sandbox grant cannot be compared."
        )
        return [*findings, unreadable], []
    pre_document = {}
    if pre_bytes is not None:
        with contextlib.suppress(ValueError):
            parsed = parse_json_bytes(pre_bytes)
            pre_document = parsed if isinstance(parsed, dict) else {}
    expected_paths = ordered_union(sandbox_paths_of(pre_document) or [], recorded)
    removed = [item for item in expected_paths if item not in live_paths]
    added = [item for item in live_paths if item not in expected_paths]
    other_keys = {*document, *pre_document} - {SANDBOX_PATHS_KEY}
    changed_keys = [key for key in other_keys if document.get(key) != pre_document.get(key)]
    if removed:
        findings.append(f"{SANDBOX_JSON_NAME} no longer lists {len(removed)} path(s) the install added or the engineer had.")
    if changed_keys:
        findings.append(f"{SANDBOX_JSON_NAME} has {len(changed_keys)} key(s) that differ from the saved pre-install file.")
    notes = [f"{len(added)} path(s) in {SANDBOX_JSON_NAME} were added since install"] if added else []
    return findings, notes
