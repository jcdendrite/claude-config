"""Shared scaffolding for every test that exercises the branch-records archive.

`build_archive_env` is the one function through which archive cases build their
per-command environment, so no case can resolve the engineer's real archive root.
`run_lib` sources `_branch-records-lib.sh` in `bash -c`, and `archive_successfully` is the
only way a case turns an archive result into a path it may modify.
"""
from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, SCRIPTS_DIR

from .conftest import _INHERITED_GIT_VARIABLES, _clean_env, _commit, _git, _init_repo, _make_worktree

ARCHIVE_ROOT_RELATIVE_PATH = Path(".local") / "state" / "agent-branch-records"
SENTINEL_NAME = ".agent-branch-records-root"
ENABLING_LINE = "agent_branch_records = true"
STRICT_SHELL_OPTIONS = "set -euo pipefail"
STATUS_SHELL_OPTIONS = ("set -u", STRICT_SHELL_OPTIONS)
STATUS_SHELL_OPTION_IDS = ("set-u", "strict")
ARCHIVE_ARGUMENT_NAMES = ["worktree", "branch", "main_worktree", "trigger"]

LIB = SCRIPTS_DIR / "_branch-records-lib.sh"
HOOKS_LIB = HOOKS_DIR / "_lib.sh"
CALLER_UMASK = "0022"
_REPORT_DELIMITER = "@@CALL@@"

# Each call runs with its stdout sent to a file, so the report can assert the call printed nothing.
_DRIVER = f"""
report_call() {{
  printf '%s\\n' '{_REPORT_DELIMITER}'
  printf 'rc=%s\\n' "$1"
  printf 'call_stdout_bytes=%s\\n' "$(wc -c <"$CALL_STDOUT_FILE" | tr -d ' ')"
  printf 'cwd=%s\\n' "$(pwd -P)"
  printf 'umask=%s\\n' "$(umask)"
  printf 'cdpath=%s\\n' "$CDPATH"
  printf 'state=%s\\n' "$BRANCH_RECORDS_STATE"
  printf 'reason=%s\\n' "$BRANCH_RECORDS_REASON"
  printf 'snapshot=%s\\n' "$BRANCH_RECORDS_SNAPSHOT"
  printf 'account_dir=%s\\n' "$BRANCH_RECORDS_ACCOUNT_DIR"
}}
call_status() {{
  branch_records_status >"$CALL_STDOUT_FILE"
  report_call 0
}}
call_archive() {{
  local rc=0
  if branch_records_archive_worktree "$@" >"$CALL_STDOUT_FILE"; then rc=0; else rc=$?; fi
  report_call "$rc"
}}
"""

requires_non_root = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the read-only modes this case relies on")


def archive_root(env: dict[str, str]) -> Path:
    """The archive root the scripts resolve from `env`'s HOME."""
    return Path(env["HOME"]) / ARCHIVE_ROOT_RELATIVE_PATH


def account_component(config_dir: Path) -> str:
    """The account directory name the archive derives from a config directory."""
    return str(config_dir.resolve()).replace("%", "%25").replace("/", "%2F")


def build_archive_env(
    tmp_path: Path,
    base_env: dict[str, str] | None = None,
    *,
    config_dir: Path | None = None,
    key_line: str | None = ENABLING_LINE,
    create_root: bool = True,
    create_sentinel: bool = True,
    root_symlink_target: Path | None = None,
    create_symlink_target: bool = True,
) -> dict[str, str]:
    """An environment whose HOME and CLAUDE_CONFIG_DIR sit under `tmp_path`.

    `base_env` is layered under the two overrides, so a script test keeps the `gh` and
    `direnv` shims of the environment it passes. `key_line` is written to
    `claude-config.toml` verbatim; None writes an empty file. The root exists on request,
    and the sentinel inside it exists on request. `root_symlink_target` makes the root a symlink to
    that directory, which holds the sentinel instead of the root. `create_symlink_target=False`
    leaves that directory uncreated, so the root dangles and no sentinel can exist.
    """
    home = tmp_path / "archive_home"
    resolved_config_dir = config_dir if config_dir is not None else tmp_path / "archive_config"
    directories = [home, resolved_config_dir]
    if root_symlink_target is not None:
        directories.append(root_symlink_target)
    for directory in directories:
        assert tmp_path in directory.parents, f"{directory} is outside {tmp_path}"
    home.mkdir(exist_ok=True)
    resolved_config_dir.mkdir(parents=True, exist_ok=True)
    (resolved_config_dir / "claude-config.toml").write_text("" if key_line is None else key_line + "\n")

    env = dict(_clean_env() if base_env is None else base_env)
    env["HOME"] = str(home)
    env["CLAUDE_CONFIG_DIR"] = str(resolved_config_dir)
    root = archive_root(env)
    if create_root:
        root_resolves = root_symlink_target is None or create_symlink_target
        if root_symlink_target is None:
            root.mkdir(parents=True, exist_ok=True)
        else:
            if create_symlink_target:
                root_symlink_target.mkdir(parents=True, exist_ok=True)
            root.parent.mkdir(parents=True, exist_ok=True)
            root.symlink_to(root_symlink_target)
        if create_sentinel and root_resolves:
            (root / SENTINEL_NAME).write_text("")
    return env


def isolate_test_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keeps a stray relative path and every setup git call inside the case's own `tmp_path`.

    The conftest's `_init_repo`, `_commit` and `_make_worktree` start git with the process
    environment, so an exported repository-selection variable would send them into the caller's repository.
    """
    monkeypatch.chdir(tmp_path)
    for variable in _INHERITED_GIT_VARIABLES:
        monkeypatch.delenv(variable, raising=False)


@dataclass(frozen=True)
class CallReport:
    rc: int
    state: str
    reason: str
    # None when the call produced no snapshot, so the report cannot be mistaken for a path (Path("") is the cwd).
    snapshot: Path | None
    account_dir: str


@dataclass(frozen=True)
class LibRun:
    calls: list[CallReport]
    stderr: str

    @property
    def only(self) -> CallReport:
        assert len(self.calls) == 1, f"expected one call report, got {len(self.calls)}"
        return self.calls[0]


@dataclass(frozen=True)
class ArchiveWorktree:
    main: Path
    worktree: Path
    branch: str


def run_lib(
    tmp_path: Path, env: dict[str, str], body: str, shell_options: str = STRICT_SHELL_OPTIONS
) -> LibRun:
    """Source both libs under `shell_options`, run `body`, and parse each call's report.

    The caller's cwd, umask and CDPATH are asserted unchanged and the call's own stdout empty,
    for every call, on success and failure returns alike.
    """
    caller_cwd = tmp_path / "caller_cwd"
    cdpath_dir = tmp_path / "cdpath_dir"
    caller_cwd.mkdir(exist_ok=True)
    cdpath_dir.mkdir(exist_ok=True)
    script = "\n".join([
        f"CDPATH={shlex.quote(str(cdpath_dir))}",
        f"umask {CALLER_UMASK}",
        f"cd {shlex.quote(str(caller_cwd))}",
        shell_options,
        f". {shlex.quote(str(HOOKS_LIB))}",
        f". {shlex.quote(str(LIB))}",
        _DRIVER,
        body,
    ])
    result = subprocess.run(
        ["bash", "-c", script],
        env={**env, "CALL_STDOUT_FILE": str(tmp_path / "call_stdout")},
        capture_output=True,
        text=True,
        check=False,
    )
    calls = []
    for block in result.stdout.split(_REPORT_DELIMITER + "\n")[1:]:
        fields = dict(line.split("=", 1) for line in block.splitlines())
        assert fields["call_stdout_bytes"] == "0", f"the call wrote to stdout: {result.stderr}"
        assert fields["cwd"] == str(caller_cwd.resolve()), "the call changed the caller's cwd"
        assert fields["umask"] == CALLER_UMASK, "the call changed the caller's umask"
        assert fields["cdpath"] == str(cdpath_dir), "the call changed the caller's CDPATH"
        calls.append(CallReport(
            rc=int(fields["rc"]),
            state=fields["state"],
            reason=fields["reason"],
            snapshot=Path(fields["snapshot"]) if fields["snapshot"] else None,
            account_dir=fields["account_dir"],
        ))
    assert calls, f"no call reported (returncode {result.returncode}): {result.stderr}"
    return LibRun(calls=calls, stderr=result.stderr)


def run_status(tmp_path: Path, env: dict[str, str], shell_options: str = STRICT_SHELL_OPTIONS) -> CallReport:
    return run_lib(tmp_path, env, "call_status", shell_options).only


def run_archive(
    tmp_path: Path,
    env: dict[str, str],
    target: ArchiveWorktree,
    trigger: str = "test-trigger.sh",
    shell_options: str = STRICT_SHELL_OPTIONS,
) -> CallReport:
    arguments = " ".join(shlex.quote(str(value)) for value in (target.worktree, target.branch, target.main, trigger))
    return run_lib(tmp_path, env, f"call_archive {arguments}", shell_options).only


def checked_snapshot(tmp_path: Path, env: dict[str, str], report: CallReport) -> Path:
    """The snapshot of a call that must have produced one, shown to lie inside the archive root under `tmp_path`.

    A case modifies a snapshot only through this check, because an unproduced snapshot is None and a
    path outside the root is a sign the lib wrote somewhere unintended.
    """
    assert report.rc == 0, f"setup archive refused: {report.reason}"
    assert report.snapshot is not None, "setup archive returned no snapshot"
    root = archive_root(env)
    assert tmp_path in root.parents, f"the archive root {root} is outside {tmp_path}"
    assert report.snapshot.is_relative_to(root), f"{report.snapshot} is outside the archive root {root}"
    return report.snapshot


def archive_successfully(tmp_path: Path, env: dict[str, str], target: ArchiveWorktree) -> Path:
    """Archives `target` for a case's setup and returns the checked snapshot."""
    return checked_snapshot(tmp_path, env, run_archive(tmp_path, env, target))


@functools.cache
def reason_variables() -> dict[str, str]:
    """Maps each cause to its fixed reason string, read from the sourced lib through bash's prefix expansion."""
    script = (
        f". {shlex.quote(str(HOOKS_LIB))}\n. {shlex.quote(str(LIB))}\n"
        'for name in "${!_BRANCH_RECORDS_REASON_@}"; do printf \'%s\\0%s\\0\' "$name" "${!name}"; done'
    )
    output = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout
    parts = output.split("\0")[:-1]
    prefix = "_BRANCH_RECORDS_REASON_"
    return {name.removeprefix(prefix): value for name, value in zip(parts[0::2], parts[1::2], strict=True)}


def make_archive_worktree(
    tmp_path: Path, branch: str = "feat/archive-me", worktree_name: str = "archived_worktree"
) -> ArchiveWorktree:
    main = tmp_path / "main_checkout"
    if not main.exists():
        _init_repo(main)
        _commit(main, "init")
    _git(main, "branch", branch)
    worktree = tmp_path / worktree_name
    _make_worktree(main, branch, worktree)
    return ArchiveWorktree(main=main, worktree=worktree, branch=branch)


def write_tree(directory: Path, files: dict[str, str]) -> None:
    for relative_name, text in files.items():
        file_path = directory / relative_name
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(text)


def read_tree(directory: Path) -> tuple[dict[str, str], set[str]]:
    """Maps every file under `directory` to its text, plus the set of its subdirectories."""
    files, directories = {}, set()
    for path in directory.rglob("*"):
        relative_name = str(path.relative_to(directory))
        if path.is_dir():
            directories.add(relative_name)
        else:
            files[relative_name] = path.read_text()
    return files, directories


def read_manifest(snapshot: str | Path) -> dict:
    return json.loads((Path(snapshot) / "manifest.json").read_text())


def expected_ledger_key(repo_root: str, branch: str) -> str:
    return f"{hashlib.sha256(repo_root.encode()).hexdigest()}.{hashlib.sha256(branch.encode()).hexdigest()}"


def key_directory(env: dict[str, str], ledger_key: str) -> Path:
    return archive_root(env) / account_component(Path(env["CLAUDE_CONFIG_DIR"])) / "branches" / ledger_key


def list_tree(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


@contextlib.contextmanager
def read_only(directory: Path) -> Iterator[None]:
    original_mode = stat.S_IMODE(directory.stat().st_mode)
    directory.chmod(0o500)
    try:
        yield
    finally:
        directory.chmod(original_mode)


def shim_path_env(env: dict[str, str], shim_dir: Path) -> dict[str, str]:
    return {**env, "PATH": f"{shim_dir}{os.pathsep}{env['PATH']}"}


def write_shim(shim_dir: Path, tool: str, body: str) -> None:
    shim_dir.mkdir(exist_ok=True)
    shim = shim_dir / tool
    shim.write_text(f"#!/bin/bash\n{body}\n")
    shim.chmod(0o755)


def write_cp_shim(shim_dir: Path, body: str) -> None:
    """Writes a `cp` shim whose `body` runs with `$REAL_CP` bound to the real binary."""
    write_shim(shim_dir, "cp", f'REAL_CP={shlex.quote(shutil.which("cp"))}\n{body}')


def archive_then_change_worktree(tmp_path: Path, env: dict[str, str], target: ArchiveWorktree) -> Path:
    """Archives `target` once, then adds a file so the earlier snapshot no longer matches. Returns that snapshot."""
    source_dir = target.worktree / "agent-reviews"
    write_tree(source_dir, {"finding.md": "text\n"})
    first_snapshot = archive_successfully(tmp_path, env, target)
    write_tree(source_dir, {"second.md": "more text\n"})
    return first_snapshot


def quoted_archive_arguments(target: ArchiveWorktree, empty_argument: str | None = None) -> str:
    """The four shell-quoted arguments `branch_records_archive_worktree` takes, with `empty_argument` blanked."""
    arguments = {
        "worktree": str(target.worktree),
        "branch": target.branch,
        "main_worktree": str(target.main),
        "trigger": "test.sh",
    }
    if empty_argument is not None:
        arguments[empty_argument] = ""
    return " ".join(shlex.quote(value) for value in arguments.values())
