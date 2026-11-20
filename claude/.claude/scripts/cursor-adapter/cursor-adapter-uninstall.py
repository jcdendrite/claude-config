# cursor-adapter.py executes this file into its own namespace, so names it or its other parts define need no import here.

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
    leftovers_present = (
        pre_state is not None
        or os.path.lexists(os.path.join(directory, MANIFEST_NAME))
        or os.path.lexists(os.path.join(directory, SANDBOX_PENDING_NAME))
    )
    sandbox_action, sandbox_bytes, sandbox_line, sandbox_planned_from = sandbox_restore_plan(directory)
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
        sandbox_writes = sandbox_action in ("restore", "rewrite", "delete")
        if sandbox_writes:
            sandbox_line = apply_sandbox_action(sandbox_action, sandbox_bytes, sandbox_planned_from, unconfirmed)
            if sandbox_line is not None:
                sandbox_action = "blocked"
        if sandbox_line:
            print(sandbox_line)
            remaining.append(f"the sandbox grant in {SANDBOX_JSON_NAME}")
        if sandbox_writes and sandbox_action != "blocked":
            reach("after-sandbox")
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
        # A sandbox.json that could not be handled keeps its record, so a later `uninstall` can finish it.
        sandbox_names = (SANDBOX_PRE_IMAGE_NAME, SANDBOX_PRE_IMAGE_NONE_NAME, SANDBOX_PENDING_NAME, MANIFEST_NAME)
        for name in (PRE_IMAGE_NAME, PRE_IMAGE_NONE_NAME, *([] if sandbox_action == "blocked" else sandbox_names)):
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
        raise post_write_failure(error, progress[0], uninstall_failure_instruction(progress[0])) from error
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


def uninstall_failure_instruction(reached):
    """What the engineer does after `uninstall` failed, naming only what the step reached has handled."""
    if reached == "after-sandbox":
        return (
            f"Only the sandbox grant in {SANDBOX_JSON_NAME} is handled so far, and hooks.json is not. Leave the saved "
            "pre-install file in place. Fix the cause: the permissions or ownership of the file named first, or, when no file is "
            "named, a change to hooks.json while `uninstall` ran. Re-run `uninstall`. "
            "If `cursor-adapter.py` cannot run, restore hooks.json by hand as docs/cursor-adapter.md's Recovery section says."
        )
    return (
        "hooks.json is already handled, so do not restore it by hand, and leave the saved pre-install file in place. "
        "Fix the cause (permissions or ownership of the file named first). Re-run `uninstall`."
    )


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
