"""Helpers shared by two or more test modules that exercise _lib.sh, among them the test_lib*.py files."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    SCRIPTS_DIR,
    _make_git_recording_argv,
    _run_git,
    bare_remote_with_default_branch,
    isolated_git_config_env,
    reviewer_round_state_key,
)

# Path to _lib.sh: this module lives in hooks/tests/, _lib.sh is in hooks/.
_LIB_SH = Path(__file__).resolve().parents[1] / "_lib.sh"
# The skill-management plugin ships its own copy of _lib_capped_for, since plugin hooks cannot source the stowed _lib.sh.
_SKILL_MANAGEMENT_PLUGIN_LIB = HOOKS_DIR.parent.parent.parent / "plugins" / "skill-management" / "hooks" / "_lib.sh"


def _run_lib_call(call: str, env: dict) -> subprocess.CompletedProcess:
    """Source _lib.sh, then run one statement that calls a helper directly."""
    harness = f". {_LIB_SH}; {call}"
    return subprocess.run(
        ["bash", "-c", harness],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _gh_shim_dir(
    tmp_path: Path, call_log: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int = 0
) -> Path:
    """PATH dir holding a fake `gh` that records argv and
    GH_HOST/GH_ENTERPRISE_TOKEN to call_log (one JSON object) and exits
    exit_code. Deletes every ambient GH_*/GITHUB_* variable through
    monkeypatch first, so no real credential reaches the recorded file. The
    deletion mutates os.environ, so a caller must build its subprocess env
    from os.environ after calling this, not before."""
    for ambient_name in [key for key in os.environ if key.startswith(("GH_", "GITHUB_"))]:
        monkeypatch.delenv(ambient_name)
    shim_dir = tmp_path / "gh-shim"
    shim_dir.mkdir()
    gh_shim = shim_dir / "gh"
    gh_shim.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json
        import os
        import sys
        record = {{
            "args": sys.argv[1:],
            "GH_HOST": os.environ.get("GH_HOST"),
            "GH_ENTERPRISE_TOKEN": os.environ.get("GH_ENTERPRISE_TOKEN"),
        }}
        with open({str(call_log)!r}, "a") as f:
            f.write(json.dumps(record) + chr(10))
        sys.exit({exit_code})
    """))
    gh_shim.chmod(0o755)
    return shim_dir


def _gate_diff_base(
    repo: Path, env: dict | None = None, timeout: float | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_gate_diff_base "$1"', "bash", str(repo)],
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else dict(os.environ),
        timeout=timeout,
    )


def _staged_diff_hash(
    repo: Path, base: str, *pathspecs: str, env: dict | None = None, timeout: float | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "bash", "-c",
            f'. {_LIB_SH}; _lib_staged_diff_hash "$1" "$2" "${{@:3}}"',
            "bash", str(repo), base, *pathspecs,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else dict(os.environ),
        timeout=timeout,
    )


def _forge_state_marker(gitdir: Path, state: str, oid: str) -> None:
    """Write the on-disk shape _lib_git_inprogress_state/_lib_gate_diff_base
    read for `state`, without running the real git operation -- for
    precedence and forged-ref tests that only need the marker shape, not a
    genuine conflict."""
    if state == "rebase":
        (gitdir / "rebase-merge").mkdir(exist_ok=True)
        (gitdir / "rebase-merge" / "head-name").write_text("refs/heads/feature\n")
        (gitdir / "REBASE_HEAD").write_text(oid + "\n")
    elif state == "merge":
        (gitdir / "MERGE_HEAD").write_text(oid + "\n")
    elif state == "cherry-pick":
        (gitdir / "CHERRY_PICK_HEAD").write_text(oid + "\n")
    elif state == "revert":
        (gitdir / "REVERT_HEAD").write_text(oid + "\n")
    else:
        raise ValueError(state)


def _assert_valid_tree_oid(repo: Path, oid: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{oid}^{{tree}}"],
        capture_output=True,
    )
    assert result.returncode == 0, f"{oid!r} is not a valid tree oid"


# The seed commit's file on `trunk`, from bare_remote_with_default_branch's default.
SEED_FILE = "f"


@pytest.fixture(autouse=True)
def isolated_git_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the host's git config out of fixture building and the measured calls."""
    for name, value in isolated_git_config_env(tmp_path / "home").items():
        monkeypatch.setenv(name, value)


def test_isolated_git_config_fixture_reaches_every_git_child(tmp_path: Path) -> None:
    """Fixture test: the autouse fixture's values reach a git child, so a host's git config cannot change a count.
    Pytest collects it through the import in test_require_diff_size_prompt.py, the only module that imports it."""
    git_env_variables = ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "HOME", "XDG_CONFIG_HOME")
    shim_env, log_file = _git_env_recording_shim(tmp_path, git_env_variables)

    subprocess.run(["git", "--version"], env={**os.environ, **shim_env}, capture_output=True, check=True)

    isolated_home = str(tmp_path / "home")
    assert _recorded_values(log_file, "GIT_CONFIG_GLOBAL") == {os.devnull}
    assert _recorded_values(log_file, "GIT_CONFIG_NOSYSTEM") == {"1"}
    assert _recorded_values(log_file, "HOME") == {isolated_home}
    assert _recorded_values(log_file, "XDG_CONFIG_HOME") == {isolated_home}


def _lines(prefix: str, count: int) -> str:
    """`count` distinct lines, so a deleted line never equals an added one."""
    return "".join(f"{prefix}-{i}\n" for i in range(count))


def _stage_file(repo: Path, relative_path: str, content: str | bytes) -> None:
    target = repo / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        target.write_bytes(content)
    else:
        target.write_text(content)
    _run_git(repo, "add", "--", relative_path)


def _commit_staged(repo: Path) -> None:
    _run_git(repo, "commit", "-q", "-m", "work")


def _clone_with_trunk_files(tmp_path: Path, trunk_files: dict[str, str] | None = None) -> tuple[Path, Path]:
    """Bare origin whose default branch is `trunk` and a clone on a new `feature` branch. `trunk_files` are
    committed and pushed to `trunk` first, so they exist at the branch point."""
    bare, clone = bare_remote_with_default_branch(tmp_path, branch="trunk", file_name=SEED_FILE)
    if trunk_files:
        for relative_path, content in trunk_files.items():
            _stage_file(clone, relative_path, content)
        _commit_staged(clone)
        _run_git(clone, "push", "-q", "origin", "trunk")
    _run_git(clone, "checkout", "-q", "-b", "feature")
    return bare, clone


def _clone_with_staged_lines(tmp_path: Path, line_count: int) -> Path:
    _bare, clone = _clone_with_trunk_files(tmp_path)
    _stage_file(clone, "work.txt", _lines("work", line_count))
    return clone


def _linked_worktree_with_staged_lines(tmp_path: Path, line_count: int) -> tuple[Path, Path]:
    """(main clone, linked worktree on `linked-branch`), with `line_count` lines staged in the worktree."""
    _bare, clone = _clone_with_trunk_files(tmp_path)
    linked = tmp_path / "linked"
    _run_git(clone, "worktree", "add", "-q", "-b", "linked-branch", str(linked), "trunk")
    _stage_file(linked, "work.txt", _lines("work", line_count))
    return clone, linked


def _config_dir(tmp_path: Path) -> Path:
    """The one CLAUDE_CONFIG_DIR a hook or script test hands its subprocess, so every latch-path assertion derives from it."""
    return tmp_path / "cfg"


def _latch_path(repo: Path, tmp_path: Path) -> Path:
    return _config_dir(tmp_path) / ".diff-size-prompt-latch.d" / reviewer_round_state_key(repo)


def _default_branch_arm_base(clone: Path, base_name: str) -> None:
    """Make the diff-size count's no-stack-key base resolve to `base_name`: a new `refs/remotes/origin/<base_name>`
    at HEAD, with `refs/remotes/origin/HEAD` as a symref to it. Only that arm can deliver an unchecked name."""
    _run_git(clone, "update-ref", f"refs/remotes/origin/{base_name}", "HEAD")
    _run_git(clone, "symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{base_name}")


# Each hostile code point is built with chr(), so this source carries no literal bidi or tag character. The ack's locale
# probe and its hostile-text cases read this tuple, so the probe qualifies a locale on every code point the cases use.
# U+E0001 is the language tag, and U+E0041 is a tag letter in U+E0020-E007F.
HOSTILE_BIDI_AND_TAG_CHARACTERS = (chr(0x202E), chr(0xE0001), chr(0xE0041))
_BIDI_CONTROL, _TAG_CHARACTER, _TAG_LETTER = HOSTILE_BIDI_AND_TAG_CHARACTERS

HOSTILE_BASE_NAME_PARAMS = [
    pytest.param("a" + _BIDI_CONTROL + "b", id="bidi control"),
    pytest.param("a" + _TAG_CHARACTER + "b", id="unicode tag character"),
    pytest.param("a" + _TAG_LETTER + "b", id="unicode tag letter"),
    pytest.param("a$b", id="printable metacharacter"),
    pytest.param("-dash", id="leading dash"),
    pytest.param("origin/nested", id="origin prefix"),
]


DIFF_SIZE_GATE_HOOK = HOOKS_DIR / "require-diff-size-prompt.sh"
DIFF_SIZE_ACK_SCRIPT = SCRIPTS_DIR / "diff-size-ack.sh"
# The one spelling the gate's roster bar and the ack's `permissions.ask` entry match.
DIFF_SIZE_ACK_COMMAND = "~/.claude/scripts/diff-size-ack.sh"


def _ack_env(
    tmp_path: Path, env_overrides: dict[str, str] | None = None, *, pin_config_dir: bool = True
) -> dict[str, str]:
    """The ambient environment with `_config_dir(tmp_path)` as CLAUDE_CONFIG_DIR. A test that needs the variable
    absent passes `pin_config_dir=False` after `monkeypatch.delenv`, since this builder never removes a key."""
    env = dict(os.environ)
    if pin_config_dir:
        env["CLAUDE_CONFIG_DIR"] = str(_config_dir(tmp_path))
    env.update(env_overrides or {})
    return env


def _run_diff_size_ack(cwd: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(DIFF_SIZE_ACK_SCRIPT), *args], cwd=cwd, env=env, capture_output=True, text=True, check=False
    )


def _git_env_recording_shim(tmp_path: Path, variable_names: tuple[str, ...]) -> tuple[dict[str, str], Path]:
    """(env overrides, log file). With the overrides in a subprocess env, every `git` call it makes appends one
    `NAME=value` line to the log for each of `variable_names`, read by name from the env that call received."""
    log_file = tmp_path / "git-env.log"
    action = "".join(f'printf "%s=%s\\n" {name} "${{{name}-}}" >> "{log_file}"\n' for name in variable_names)
    shim_dir = tmp_path / "git-env-shim"
    _make_git_recording_argv(shim_dir, tmp_path / "git-argv.log", action)
    real_git = shutil.which("git")
    assert real_git is not None
    return {"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}", "REAL_GIT": real_git}, log_file


def _assert_hook_allows(
    hook: Path, payload: dict, process_cwd: Path, extra_env: dict[str, str] | None = None
) -> None:
    """Asserts a clean silent exit. `run_hook` reads any status but 2 with empty stdout as allow, so a crash would pass."""
    result = subprocess.run(
        [str(hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=process_cwd,
        env={**os.environ, **(extra_env or {})},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "", result.stdout


def _recorded_values(log_file: Path, variable_name: str) -> set[str]:
    """Every value `_git_env_recording_shim` saw for `variable_name`. An empty log fails, so a shim that never ran
    cannot pass a premise check."""
    assert log_file.exists(), "premise: the recording shim never ran"
    prefix = f"{variable_name}="
    return {line[len(prefix):] for line in log_file.read_text().splitlines() if line.startswith(prefix)}
