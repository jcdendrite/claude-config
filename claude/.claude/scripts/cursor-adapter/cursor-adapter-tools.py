# cursor-adapter.py executes this file into its own namespace, so names its other parts define need no import here.

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
    """The path `shutil.which` finds, which the adapter calls the tool's `command -v` path. `directory` is the working
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
