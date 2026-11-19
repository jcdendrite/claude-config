# cursor-adapter.py executes this file into its own namespace, so names it or its other parts define need no import here.

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
    sandbox_plan = plan_sandbox_grant(routed, directory)
    for line in print_set(report, live_hooks, pre_hooks, replacing_pre_image=replacing_pre_image, comparable=True):
        print(line)
    if sandbox_plan is not None:
        engineers_listed = engineer_listed_sandbox_directories(sandbox_plan, routed)
        if engineers_listed:
            print(
                f"{len(engineers_listed)} of the sandbox grant's directories are already listed in {sandbox_plan.path}, "
                "and are kept as they are."
            )
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
        if sandbox_plan is not None:
            write_sandbox_records(sandbox_plan, directory, unconfirmed)
            reach("after-sandbox-records")
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
        manifest = {"files": manifest_files}
        if sandbox_plan is not None:
            write_sandbox_file(sandbox_plan, routed, unconfirmed)
            reach("after-sandbox-file")
        # A build that writes no grant carries an earlier install's record forward, so `uninstall` still removes those paths.
        owned_paths = sandbox_plan.owned if sandbox_plan is not None else recorded_sandbox_paths(directory)
        if owned_paths:
            manifest["sandbox_paths"] = owned_paths
        unconfirmed.run(durable_write, os.path.join(directory, MANIFEST_NAME), json_bytes(manifest))
        with contextlib.suppress(FileNotFoundError):
            os.unlink(os.path.join(directory, SANDBOX_PENDING_NAME))
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
