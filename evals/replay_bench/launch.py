"""What a replay attempt launches: the allowlisted environment behind `/usr/bin/env -i`, the `--settings`
JSON that carries the instruction-load hook, and the exact-path Read allow entries on the command line.

The child's environment is built from a named allowlist instead of inherited, so the subagent's Bash
sees none of the runner's other session variables. Each allowlisted value sits in `env`'s argv until `env`
execs `claude`, so the runner assumes a single-user machine.
"""
from __future__ import annotations

import json
import os
import shlex
from collections.abc import Collection, Mapping
from pathlib import Path

ENV_EXECUTABLE = "/usr/bin/env"
CONFIG_DIR_VARIABLE = "CLAUDE_CONFIG_DIR"
INSTRUCTIONS_LOG_NAME = "instructions-loaded.jsonl"

# Which proxy forms the CLI reads is unchecked, so each form the runner's environment sets passes.
LAUNCH_ENV_ALLOWLIST = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE",
    "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "no_proxy", "all_proxy",
    "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE",
)

# A path holding one of these could parse as a pattern or split into two entries when written exactly.
_PATTERN_CHARACTERS = frozenset("*?[]{}(),\\")
PERMISSION_MODE_ARGV = ("--permission-mode", "acceptEdits")


class UnsafeConfigPathError(ValueError):
    """A generated config-tree path that cannot be written as an exact exclude or Read allow entry."""


def check_path_characters(path: str) -> None:
    """Raises UnsafeConfigPathError, naming the path in `!r` form, for whitespace, a control character or a
    pattern character. Both generators call this, and preflight reaches it through them."""
    if any(character.isspace() or not character.isprintable() or character in _PATTERN_CHARACTERS for character in path):
        raise UnsafeConfigPathError(
            f"config path {path!r} holds whitespace, a control character or a pattern character, "
            "so it cannot be written as an exact entry"
        )


def launch_prefix(environ: Mapping[str, str], *, config_dir_value: str | None) -> list[str]:
    """`env -i` plus `NAME=value` for each allowlisted variable `environ` sets. A variable `environ` does not
    set is left out, never passed empty. `config_dir_value` is the value the probe found for
    `CLAUDE_CONFIG_DIR`, or None when it found none."""
    argv = [ENV_EXECUTABLE, "-i"]
    argv.extend(f"{name}={environ[name]}" for name in LAUNCH_ENV_ALLOWLIST if name in environ)
    if config_dir_value is not None:
        argv.append(f"{CONFIG_DIR_VARIABLE}={config_dir_value}")
    return argv


def readiness_argv(prefix: list[str]) -> list[str]:
    return [*prefix, "claude", "--version"]


def instrument_hook_command(log_path: Path) -> str:
    """One `printf` of the captured payload reaches the log in one write, so two hooks firing at once cannot
    split one payload from its newline."""
    return f"payload=$(cat); printf '%s\\n' \"$payload\" >> {shlex.quote(str(log_path))}"


def settings_json(log_path: Path) -> str:
    hook = {"type": "command", "command": instrument_hook_command(log_path)}
    return json.dumps({"hooks": {"InstructionsLoaded": [{"hooks": [hook]}]}})


def _existing_roots(config_dir: Path, home: Path) -> list[Path]:
    roots: dict[Path, Path] = {}
    for candidate in (config_dir, home / ".claude"):
        if candidate.is_dir():
            roots.setdefault(Path(os.path.realpath(candidate)), candidate)
    return list(roots.values())


def _listed_files(root: Path) -> list[Path]:
    """Each `*.md` file directly under `agents/` and each `skills/<name>/SKILL.md`. `is_file` follows a link,
    so a dangling link is skipped."""
    files: list[Path] = []
    agents_dir = root / "agents"
    if agents_dir.is_dir():
        files.extend(entry for entry in sorted(agents_dir.iterdir()) if entry.suffix == ".md" and entry.is_file())
    skills_dir = root / "skills"
    if skills_dir.is_dir():
        files.extend(
            skill_file for skill_dir in sorted(skills_dir.iterdir())
            if (skill_file := skill_dir / "SKILL.md").is_file()
        )
    return files


def read_allow_paths(config_dir: Path, home: Path) -> list[Path]:
    """Every agent and skill file under the config dir and `$HOME/.claude`, each by the path as listed and by
    its realpath, deduplicated. Raises UnsafeConfigPathError before returning when a path holds a character
    that `check_path_characters` refuses."""
    paths: dict[str, Path] = {}
    for root in _existing_roots(config_dir, home):
        for listed in _listed_files(root):
            for form in (listed, Path(os.path.realpath(listed))):
                check_path_characters(str(form))
                paths.setdefault(str(form), form)
    return list(paths.values())


def read_allow_entries(paths: Collection[Path]) -> list[str]:
    """`Read(//<absolute path>)`: the extra slash is the permission syntax's absolute-path prefix."""
    return [f"Read(/{path})" for path in paths]


def allowed_tools_tail(paths: Collection[Path]) -> list[str]:
    """`--allowedTools` and each entry as its own argv element. It goes last on the command line."""
    return ["--allowedTools", *read_allow_entries(paths)]
