"""Account preflight: which Claude Code account a source repo launches under, and whether that account can run
a replay. Every refusal is a `HarnessInvalidatedError` that names the repo and one failure shape, and never
carries the child's stdout, stderr, argv or an environment value.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import measure_subagent_model_resolution as msmr
from review_bench import runner as review_runner
from review_bench.runner import HarnessInvalidatedError

from replay_bench import arms, launch

# The floor `omitClaudeMd` needs.
MIN_CLI_VERSION = (2, 1, 271)
DIRENV_VARIABLE_PREFIX = "DIRENV_"
SESSION_START_HOOK_SUFFIX = "capture-session-id.sh"
PROBE_DIR_PREFIX = "replay-probe-"

_PROBE_SNIPPET = (
    "import json, os; "
    "print(json.dumps({'cwd': os.getcwd(), 'config_dir': os.environ.get('CLAUDE_CONFIG_DIR'), 'names': sorted(os.environ)}))"
)
_VERSION_RE = re.compile(r"^\s*v?(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True)
class Account:
    """`config_dir_value` is the `CLAUDE_CONFIG_DIR` value the probe found, or None when it was unset."""

    key: str
    config_dir: Path
    config_dir_value: str | None
    home: Path
    code_writer_tools: frozenset[str]
    bare_writer_markdown: str
    dropped_variables: tuple[str, ...]
    probe_seconds: float

    def launch_prefix(self, environ: Mapping[str, str]) -> list[str]:
        return launch.launch_prefix(environ, config_dir_value=self.config_dir_value)


def account_key(config_dir: Path) -> str:
    return os.path.realpath(config_dir)


def _shown(path: Path | str) -> str:
    return repr(str(path))


def _refuse(repo: Path | str, problem: str) -> HarnessInvalidatedError:
    return HarnessInvalidatedError(f"preflight: {_shown(repo)}: {problem}")


def process_failure_text(returncode: int, *, repo: Path | str, direnv: bool) -> str:
    hint = f"; run `direnv exec {_shown(repo)} true` in a terminal to see direnv's own message" if direnv else ""
    return f"exit {returncode}{hint}"


def refuse_unsafe_path(environ: Mapping[str, str]) -> None:
    """An empty or relative `PATH` entry would let `env` resolve `claude`, or the agent resolve a command,
    from the fixture."""
    path_value = environ.get("PATH")
    if not path_value:
        raise HarnessInvalidatedError("preflight: PATH is unset or empty")
    if any(not entry or not os.path.isabs(entry) for entry in path_value.split(os.pathsep)):
        raise HarnessInvalidatedError("preflight: PATH holds an empty or relative entry")


@dataclass(frozen=True)
class _Probe:
    config_dir_value: str | None
    names: tuple[str, ...]
    seconds: float


def _probe(repo: Path, probe_dir: Path, *, timeout_s: float) -> _Probe:
    command = ["direnv", "exec", str(repo), sys.executable, "-I", "-c", _PROBE_SNIPPET]
    started = time.monotonic()
    try:
        completed = subprocess.run(command, cwd=probe_dir, capture_output=True, text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired:
        raise _refuse(repo, f"timed out after {timeout_s:g} s; raise --direnv-timeout-s") from None
    except (OSError, UnicodeDecodeError) as exc:
        detail = review_runner._os_error_detail(exc) if isinstance(exc, OSError) else type(exc).__name__
        raise _refuse(repo, detail) from None
    seconds = time.monotonic() - started
    if completed.returncode != 0:
        raise _refuse(repo, process_failure_text(completed.returncode, repo=repo, direnv=True))
    try:
        payload = json.loads(completed.stdout)
        cwd, config_dir_value, names = payload["cwd"], payload["config_dir"], payload["names"]
        well_formed = (
            isinstance(cwd, str) and (config_dir_value is None or isinstance(config_dir_value, str))
            and isinstance(names, list) and all(isinstance(name, str) for name in names)
        )
    except (ValueError, KeyError, TypeError):
        well_formed = False
    if not well_formed:
        raise _refuse(repo, "the probe's output did not parse as the expected JSON")
    if os.path.realpath(cwd) != os.path.realpath(probe_dir):
        raise _refuse(repo, "direnv exec did not run the probe in the caller's working directory")
    if config_dir_value is not None and not os.path.isabs(config_dir_value):
        raise _refuse(repo, "CLAUDE_CONFIG_DIR is not an absolute path")
    return _Probe(config_dir_value or None, tuple(names), seconds)


def dropped_variable_names(names: tuple[str, ...]) -> tuple[str, ...]:
    """Every name in the probe's environment that the launch drops, `CLAUDE_CONFIG_DIR` and direnv's own
    variables aside. The runner's own shell variables are listed too."""
    kept = {*launch.LAUNCH_ENV_ALLOWLIST, launch.CONFIG_DIR_VARIABLE}
    return tuple(name for name in names if name not in kept and not name.startswith(DIRENV_VARIABLE_PREFIX))


def _registers_session_start_hook(settings_path: Path) -> bool:
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        groups = settings["hooks"]["SessionStart"]
        return any(
            isinstance(hook.get("command"), str) and hook["command"].endswith(SESSION_START_HOOK_SUFFIX)
            for group in groups for hook in group["hooks"]
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def _check_config_dir(repo: Path, config_dir: Path) -> tuple[frozenset[str], str]:
    """The account's code-writer tools and the `bare-writer.md` text built from its file."""
    for required in (config_dir / "CLAUDE.md", config_dir / arms.CODE_WRITER_AGENT_RELPATH):
        if not required.is_file():
            raise _refuse(repo, f"the config dir lacks {_shown(required.relative_to(config_dir))}")
    try:
        code_writer = arms.code_writer_text(config_dir)
        markdown = arms.bare_writer_markdown(code_writer)
    except (OSError, UnicodeDecodeError) as exc:
        raise _refuse(repo, f"code-writer.md is unreadable: {type(exc).__name__}") from None
    except arms.AgentConfigError as exc:
        raise _refuse(repo, str(exc)) from None
    if not _registers_session_start_hook(config_dir / "settings.json"):
        raise _refuse(
            repo, f"the account's settings.json registers no SessionStart command ending in {SESSION_START_HOOK_SUFFIX}",
        )
    return msmr.agent_frontmatter_tools(code_writer), markdown


def check_generators(repo: Path, config_dir: Path, home: Path) -> None:
    """Calls the generators an attempt calls, so preflight's check and an attempt's cannot drift apart."""
    try:
        arms.claude_md_excludes(config_dir)
        launch.read_allow_paths(config_dir, home)
    except launch.UnsafeConfigPathError as exc:
        raise _refuse(repo, str(exc)) from None


def parse_cli_version(version_output: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.match(version_output)
    return None if match is None else (int(match[1]), int(match[2]), int(match[3]))


def check_readiness(prefix: list[str], probe_dir: Path, *, repo: Path, timeout_s: float) -> None:
    """Runs `claude --version` behind the exact launch prefix. It shows that `claude` resolves on the
    allowlisted `PATH` and starts, not that the account is logged in. The child's output is never echoed."""
    try:
        completed = subprocess.run(
            launch.readiness_argv(prefix), cwd=probe_dir, capture_output=True, text=True, timeout=timeout_s, check=False,
        )
    except subprocess.TimeoutExpired:
        raise _refuse(repo, f"claude --version timed out after {timeout_s:g} s") from None
    except (OSError, UnicodeDecodeError) as exc:
        detail = review_runner._os_error_detail(exc) if isinstance(exc, OSError) else type(exc).__name__
        raise _refuse(repo, f"claude --version: {detail}") from None
    if completed.returncode != 0:
        raise _refuse(repo, f"claude --version: {process_failure_text(completed.returncode, repo=repo, direnv=False)}")
    version = parse_cli_version(completed.stdout)
    if version is None:
        raise _refuse(repo, "claude --version printed a version this runner cannot parse")
    if version < MIN_CLI_VERSION:
        floor = ".".join(map(str, MIN_CLI_VERSION))
        raise _refuse(repo, f"claude {'.'.join(map(str, version))} is older than {floor}, which omitClaudeMd needs")


def refuse_fixture_dir_under_home(fixture_dir: Path, home: Path) -> None:
    """Ancestor instruction files under `$HOME` would load into the fixture's session."""
    if fixture_dir.resolve().is_relative_to(home.resolve()):
        raise HarnessInvalidatedError(
            f"fixture directory {_shown(fixture_dir)} lies under $HOME, where ancestor instruction files would load"
        )


def preflight_repo(
    repo: Path, *, direnv_timeout_s: float | None = None, readiness_timeout_s: float | None = None,
    environ: Mapping[str, str] | None = None,
) -> Account:
    """Probes `repo`'s account and refuses it, with HarnessInvalidatedError, on any failure the plan lists.
    Both timeouts default to `review_bench.runner._ENVIRONMENT_READ_TIMEOUT_S`, read at call time."""
    environ = os.environ if environ is None else environ
    if not repo.is_absolute():
        raise _refuse(repo, "the source repo path is not absolute")
    if direnv_timeout_s is None:
        direnv_timeout_s = review_runner._ENVIRONMENT_READ_TIMEOUT_S
    if readiness_timeout_s is None:
        readiness_timeout_s = review_runner._ENVIRONMENT_READ_TIMEOUT_S
    refuse_unsafe_path(environ)
    if shutil.which("direnv", path=environ["PATH"]) is None:
        raise _refuse(repo, "direnv is not on PATH")
    probe_dir = Path(tempfile.mkdtemp(prefix=PROBE_DIR_PREFIX)).resolve()
    try:
        probe = _probe(repo, probe_dir, timeout_s=direnv_timeout_s)
        home = Path(environ["HOME"]) if environ.get("HOME") else Path.home()
        config_dir = Path(probe.config_dir_value) if probe.config_dir_value else home / ".claude"
        code_writer_tools, markdown = _check_config_dir(repo, config_dir)
        check_generators(repo, config_dir, home)
        prefix = launch.launch_prefix(environ, config_dir_value=probe.config_dir_value)
        check_readiness(prefix, probe_dir, repo=repo, timeout_s=readiness_timeout_s)
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)
    return Account(
        key=account_key(config_dir), config_dir=config_dir, config_dir_value=probe.config_dir_value, home=home,
        code_writer_tools=code_writer_tools, bare_writer_markdown=markdown,
        dropped_variables=dropped_variable_names(probe.names), probe_seconds=probe.seconds,
    )
