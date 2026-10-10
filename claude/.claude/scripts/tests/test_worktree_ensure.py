"""Tests for worktree-ensure.sh.

Runs the script as a subprocess against real git repositories built from
conftest.py's helpers, with one case per I/O branch. The decision matrix for
the pure-bash functions it calls lives in test_worktree_lib.py.

Fixture defaults, applied by `_make_fixture`:
- The main checkout sits under a directory whose name contains a space.
- Branch names have the `<ticket>/<slug>` shape.
- The repo sets `core.hooksPath` and `branch.autoSetupMerge=always` explicitly,
  so neither the contributor's global config nor git's default decides a case.
- The main checkout's `.git/info/exclude` lists `.claude/worktrees/`, which
  `_make_repo_with_remote` does not ignore.

Isolation: `_clean_env` removes the four repository-selection variables
(`GIT_DIR`, `GIT_WORK_TREE`, `GIT_COMMON_DIR`, `GIT_INDEX_FILE`) and the
DIRENV and gh-credential variables, for the calls made in this file. It does not
cover the conftest builders, which call git with the inherited environment.
Nothing isolates the contributor's global or system git config beyond the two
keys above, so keys such as `commit.gpgsign` still apply to the second-clone
commits.

Git behaviors this file observes, each seen on git 2.43.0. A failure after a
git upgrade in one of them points at git's behavior before the script:
- `worktree add -b` creates the branch before it checks the target path, and
  leaves the branch behind when the add then fails.
- A failing `post-checkout` hook makes the add exit non-zero after the checkout
  is complete, and git relays a hook's stdout to its own stderr.
- `worktree list --porcelain` prints `locked <reason>` for a locked record.
- `worktree add` accepts an existing empty target directory.
- `worktree add` follows a symlinked ancestor of the target.
- A fetch of `+refs/heads/<branch>:refs/remotes/origin/<branch>` updates that
  ref when the remote branch was rewritten, and ignores a narrowed
  `remote.origin.fetch`.

The `initializing` lock reason is hand-seeded in the `add-unfinished` case.
No test observes the literal real git writes during an in-flight add.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import NamedTuple

import pytest

from .conftest import (
    _base_test_env,
    _make_feature_branch,
    _make_repo_with_remote,
    _make_worktree,
)

_SCRIPT = Path(__file__).parent.parent / "worktree-ensure.sh"

_INHERITED_GIT_VARIABLES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE")

_CURRENT_NEXT_LINE = (
    "next: none; the working directory is this worktree, "
    "and EnterWorktree refuses a target equal to the working directory"
)

_BRANCH = "GH-1/new-thing"

_REAL_GIT = shutil.which("git")


class _Fixture(NamedTuple):
    base: Path
    main: Path
    bare: Path
    default_branch: str


def _clean_env(**overrides: str | None) -> dict[str, str]:
    """The base test env without inherited repository-selection variables; a None override removes the key."""
    env = {key: value for key, value in _base_test_env().items() if key not in _INHERITED_GIT_VARIABLES}
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, env=_clean_env(), capture_output=True, text=True, check=True
    ).stdout


def _make_fixture(tmp_path: Path, default_branch: str = "main", base_name: str = "dir with space") -> _Fixture:
    base = tmp_path / base_name
    base.mkdir()
    main, bare = _make_repo_with_remote(base, default_branch)
    hooks_dir = base / "hooks"
    hooks_dir.mkdir()
    _git(main, "config", "core.hooksPath", str(hooks_dir))
    _git(main, "config", "branch.autoSetupMerge", "always")
    with (main / ".git" / "info" / "exclude").open("a") as exclude_file:
        exclude_file.write(".claude/worktrees/\n")
    return _Fixture(base, main, bare, default_branch)


def _push_from_second_clone(tmp_path: Path, fixture: _Fixture) -> str:
    """Push one new commit to the default branch from a second clone; return its sha."""
    second = tmp_path / "second-clone"
    subprocess.run(
        ["git", "clone", "-q", str(fixture.bare), str(second)], env=_clean_env(), check=True, capture_output=True
    )
    _git(second, "config", "user.email", "t@t.com")
    _git(second, "config", "user.name", "t")
    (second / "pushed.txt").write_text("pushed\n")
    _git(second, "add", "pushed.txt")
    _git(second, "commit", "-q", "-m", "pushed from a second clone")
    _git(second, "push", "-q", "origin", f"HEAD:{fixture.default_branch}")
    return _git(second, "rev-parse", "HEAD").strip()


def _force_push_rewritten_tip(tmp_path: Path, fixture: _Fixture) -> str:
    """Rewrite the commit `_push_from_second_clone` pushed and force-push it; return the new sha.

    The new tip shares a parent with the old one and is not its descendant.
    """
    second = tmp_path / "second-clone"
    _git(second, "commit", "-q", "--amend", "-m", "rewritten in a second clone")
    _git(second, "push", "-q", "--force", "origin", f"HEAD:{fixture.default_branch}")
    return _git(second, "rev-parse", "HEAD").strip()


def _install_hook(main: Path, hook_name: str, body: str) -> None:
    hooks_dir = Path(_git(main, "config", "core.hooksPath").strip())
    hook = hooks_dir / hook_name
    hook.write_text(f"#!/usr/bin/env bash\n{body}\n")
    hook.chmod(0o755)


def _expected_target(main: Path, branch: str) -> Path:
    return Path(os.path.realpath(main)) / ".claude" / "worktrees" / branch


def _make_decoy_worktree(main: Path) -> Path:
    decoy = main / ".claude" / "worktrees" / "GH-9" / "decoy"
    _git(main, "worktree", "add", "-q", "-b", "GH-9/decoy", str(decoy))
    return decoy


def _run_script(cwd: Path, args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(_SCRIPT), *args],
        cwd=str(cwd),
        env=env if env is not None else _clean_env(),
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# PATH shims standing in for git
# ---------------------------------------------------------------------------

# Resolves the subcommand after any leading `-C <path>` pairs, and a second
# word for `worktree <verb>`, so a shim matches the call the script makes
# whether or not it passes -C.
_SHIM_SUBCOMMAND_PRELUDE = """\
#!/usr/bin/env bash
args=("$@")
index=0
while [ "${args[$index]-}" = "-C" ]; do index=$((index + 2)); done
subcommand="${args[$index]-}"
if [ "$subcommand" = "worktree" ]; then subcommand="worktree ${args[$((index + 1))]-}"; fi
"""


def _write_shim(tmp_path: Path, body: str) -> Path:
    shim_dir = tmp_path / f"git_shim_{uuid.uuid4().hex}"
    shim_dir.mkdir()
    shim = shim_dir / "git"
    shim.write_text(body)
    shim.chmod(0o755)
    return shim_dir


def _git_shim_failing_nth(tmp_path: Path, subcommand: str, nth: int) -> Path:
    """A `git` shim that delegates to the real git except on the n-th call of `subcommand`, which exits 1."""
    calls_file = tmp_path / f"shim_calls_{uuid.uuid4().hex}"
    return _write_shim(
        tmp_path,
        _SHIM_SUBCOMMAND_PRELUDE
        + f"""\
if [ "$subcommand" = {shlex.quote(subcommand)} ]; then
  calls=$(cat {shlex.quote(str(calls_file))} 2>/dev/null || echo 0)
  calls=$((calls + 1))
  echo "$calls" > {shlex.quote(str(calls_file))}
  if [ "$calls" -eq {nth} ]; then
    echo "synthetic {subcommand} failure" >&2
    exit 1
  fi
fi
exec {shlex.quote(_REAL_GIT)} "$@"
""",
    )


def _git_shim_on(tmp_path: Path, subcommand: str, snippet: str) -> Path:
    """A `git` shim that runs `snippet` on each call of `subcommand`, then execs the real git.

    The snippet may `exit` to replace the call.
    """
    return _write_shim(
        tmp_path,
        _SHIM_SUBCOMMAND_PRELUDE
        + f"""\
if [ "$subcommand" = {shlex.quote(subcommand)} ]; then
{snippet}
fi
exec {shlex.quote(_REAL_GIT)} "$@"
""",
    )


def _git_shim_exiting(tmp_path: Path, status: int) -> Path:
    return _write_shim(tmp_path, f"#!/usr/bin/env bash\nexit {status}\n")


def _env_with_shim(shim_dir: Path, **overrides: str | None) -> dict[str, str]:
    return _clean_env(PATH=f"{shim_dir}:{os.environ['PATH']}", **overrides)


# ---------------------------------------------------------------------------
# State snapshots and assertions
# ---------------------------------------------------------------------------


def _worktrees_listing(root: Path) -> list[str]:
    """Sorted recursive listing naming each entry's type without following a symlink, or `root`'s own type."""
    if root.is_symlink():
        return ["symlink"]
    if not root.exists():
        return ["absent"]
    if not root.is_dir():
        return ["file"]
    entries = []
    for directory, directory_names, file_names in os.walk(root):
        for name in directory_names + file_names:
            entry = Path(directory) / name
            kind = "symlink" if entry.is_symlink() else "dir" if entry.is_dir() else "file"
            entries.append(f"{kind} {entry.relative_to(root)}")
    return sorted(entries)


def _repo_state(main: Path) -> tuple[str, str, list[str]]:
    return (
        _git(main, "for-each-ref", "refs/heads"),
        _git(main, "worktree", "list", "--porcelain"),
        _worktrees_listing(main / ".claude" / "worktrees"),
    )


def _main_checkout_state(main: Path) -> tuple[str, str, str]:
    return (
        _git(main, "symbolic-ref", "-q", "HEAD"),
        _git(main, "rev-parse", "HEAD"),
        _git(main, "status", "--porcelain"),
    )


def _last_stderr_line(result: subprocess.CompletedProcess) -> str:
    lines = result.stderr.rstrip("\n").splitlines()
    assert lines, "stderr is empty"
    return lines[-1]


def _assert_refused_output(result: subprocess.CompletedProcess, exit_code: int, token: str) -> None:
    assert result.returncode == exit_code, result.stderr
    assert result.stdout == ""
    assert _last_stderr_line(result).startswith(f"worktree-ensure.sh: {token}:"), result.stderr


def _assert_refused(result: subprocess.CompletedProcess, exit_code: int, token: str, before, after) -> None:
    _assert_refused_output(result, exit_code, token)
    assert before == after


def _assert_ready(result: subprocess.CompletedProcess, state: str, path: Path) -> None:
    assert result.returncode == 0, result.stderr
    if state == "current":
        expected = f"current {path}\n{_CURRENT_NEXT_LINE}\n"
    else:
        expected = f'{state} {path}\nnext: EnterWorktree{{path: "{path}"}}\n'
    assert result.stdout == expected, result.stderr


def _message_text(result: subprocess.CompletedProcess, tmp_path: Path, *paths: Path) -> str:
    """The last stderr line after its `worktree-ensure.sh: <token>: ` prefix, with `tmp_path` and each
    further path removed in raw and realpath forms, longest first.

    `tmp_path` names come from test names, so a word asserted on the raw line could come from a path.
    """
    message = re.sub(r"^worktree-ensure\.sh: [a-z-]+: ", "", _last_stderr_line(result))
    forms = {str(path) for path in (tmp_path, *paths)} | {os.path.realpath(path) for path in (tmp_path, *paths)}
    for form in sorted(forms, key=len, reverse=True):
        message = message.replace(form, "")
    return message


def _run_refusal(fixture: _Fixture, branch: str, cwd: Path | None = None, env: dict[str, str] | None = None):
    """Move the remote away, so a refusal that ran after the fetch fails on its token, then run the script.

    Returns (result, state before the run, state after).
    """
    fixture.bare.rename(fixture.base / "remote-moved-away.git")
    before = _repo_state(fixture.main)
    result = _run_script(cwd if cwd is not None else fixture.main, [branch], env=env)
    return result, before, _repo_state(fixture.main)


# ---------------------------------------------------------------------------
# Argument, name and repository exit paths
# ---------------------------------------------------------------------------


class TestArgumentsAndName:
    @pytest.mark.parametrize("args", [[], ["GH-1/a", "GH-1/b"]], ids=["no-argument", "two-arguments"])
    def test_wrong_argument_count_is_usage(self, tmp_path, args):
        fixture = _make_fixture(tmp_path)

        _assert_refused_output(_run_script(fixture.main, args), 2, "usage")

    @pytest.mark.parametrize("name", ["a;b", "a..b"], ids=["shell-metacharacter", "git-invalid-name"])
    def test_bad_name_is_refused_before_repository_detection(self, tmp_path, name):
        """Run from a directory that is not a repository, which shows the name check comes first."""
        not_a_repo = tmp_path / "plain-directory"
        not_a_repo.mkdir()

        _assert_refused_output(_run_script(not_a_repo, [name]), 2, "bad-name")

    def test_bad_name_holding_a_forged_status_line_leaves_exactly_one_status_line(self, tmp_path):
        """The skill reads the last stderr line starting `worktree-ensure.sh: `, so the name must not reach it."""
        not_a_repo = tmp_path / "plain-directory"
        not_a_repo.mkdir()

        result = _run_script(not_a_repo, ["a\nworktree-ensure.sh: in-main: x"])

        _assert_refused_output(result, 2, "bad-name")
        status_lines = [line for line in result.stderr.splitlines() if line.startswith("worktree-ensure.sh: ")]
        assert len(status_lines) == 1, result.stderr
        assert "in-main" not in result.stderr

    def test_git_that_cannot_run_is_no_repo_from_the_name_check(self, tmp_path):
        fixture = _make_fixture(tmp_path)
        shim_dir = _git_shim_exiting(tmp_path, 127)

        result = _run_script(fixture.main, [_BRANCH], env=_env_with_shim(shim_dir))

        _assert_refused_output(result, 1, "no-repo")
        assert "127" in _message_text(result, tmp_path)

    def test_branch_run_outside_any_repository_is_no_repo(self, tmp_path):
        not_a_repo = tmp_path / "plain-directory"
        not_a_repo.mkdir()

        _assert_refused_output(_run_script(not_a_repo, [_BRANCH]), 1, "no-repo")


# ---------------------------------------------------------------------------
# Successful states
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.parametrize(
        ("cwd_kind", "default_branch"),
        [("main-root", "main"), ("linked-worktree", "trunk")],
    )
    def test_created_then_existing_then_current(self, tmp_path, cwd_kind, default_branch):
        """GH-1234: a create run from inside a linked worktree must land under the main
        checkout's worktrees directory, not nest inside the worktree the session sits in."""
        fixture = _make_fixture(tmp_path, default_branch)
        main = fixture.main
        if cwd_kind == "main-root":
            assert not (main / ".claude").exists(), "git must create .claude itself"
            start_cwd = main
        else:
            start_cwd = main / ".claude" / "worktrees" / "GH-1234" / "nest-host"
            _git(main, "worktree", "add", "-q", "-b", "GH-1234/nest-host", str(start_cwd))
        pushed_sha = _push_from_second_clone(tmp_path, fixture)
        main_state_before = _main_checkout_state(main)
        target = _expected_target(main, _BRANCH)

        created = _run_script(start_cwd, [_BRANCH])

        _assert_ready(created, "created", target)
        if cwd_kind == "linked-worktree":
            assert not (start_cwd / ".claude").exists(), "a nested create would leave .claude inside the session's worktree"
        assert _git(main, "rev-parse", _BRANCH).strip() == pushed_sha
        assert _main_checkout_state(main) == main_state_before
        upstream = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", f"{_BRANCH}@{{upstream}}"],
            cwd=main, env=_clean_env(), capture_output=True, text=True, check=False,
        )
        assert upstream.returncode != 0, "a future remote-tracking start point would set an upstream"

        _assert_ready(_run_script(start_cwd, [_BRANCH]), "existing", target)
        _assert_ready(_run_script(target, [_BRANCH]), "current", target)
        subdirectory = target / "subdirectory"
        subdirectory.mkdir()
        _assert_ready(_run_script(subdirectory, [_BRANCH]), "existing", target)

    def test_non_ascii_main_checkout_path_is_created_then_existing_then_current(self, tmp_path):
        """The script runs under LC_ALL=C while it compares, enters and prints the path's raw bytes."""
        fixture = _make_fixture(tmp_path, base_name="dïr with ñ")
        target = _expected_target(fixture.main, _BRANCH)

        _assert_ready(_run_script(fixture.main, [_BRANCH]), "created", target)
        _assert_ready(_run_script(fixture.main, [_BRANCH]), "existing", target)
        _assert_ready(_run_script(target, [_BRANCH]), "current", target)

    def test_cwd_that_is_an_existing_empty_target_is_current_after_the_add(self, tmp_path):
        fixture = _make_fixture(tmp_path)
        target = _expected_target(fixture.main, _BRANCH)
        target.mkdir(parents=True)

        _assert_ready(_run_script(target, [_BRANCH]), "current", target)

    def test_cwd_reached_through_a_symlink_is_current(self, tmp_path):
        """The child's PWD names the symlink, so a plain `pwd` would differ from `pwd -P`."""
        fixture = _make_fixture(tmp_path)
        worktree = fixture.main / ".claude" / "worktrees" / "GH-1" / "linked"
        _git(fixture.main, "worktree", "add", "-q", "-b", _BRANCH, str(worktree))
        link = tmp_path / "link-to-worktree"
        link.symlink_to(worktree)

        result = _run_script(link, [_BRANCH], env=_clean_env(PWD=str(link)))

        _assert_ready(result, "current", Path(os.path.realpath(worktree)))

    def test_existing_needs_no_network(self, tmp_path):
        fixture = _make_fixture(tmp_path)
        worktree = _expected_target(fixture.main, _BRANCH)
        _git(fixture.main, "worktree", "add", "-q", "-b", _BRANCH, str(worktree))
        fixture.bare.rename(tmp_path / "remote-moved-away.git")

        _assert_ready(_run_script(fixture.main, [_BRANCH]), "existing", worktree)


# ---------------------------------------------------------------------------
# Fetch and start point
# ---------------------------------------------------------------------------


class TestFetch:
    def test_narrowed_fetch_configuration_still_starts_from_the_fresh_tip(self, tmp_path):
        fixture = _make_fixture(tmp_path)
        _git(fixture.main, "config", "remote.origin.fetch", "+refs/heads/other:refs/remotes/origin/other")
        pushed_sha = _push_from_second_clone(tmp_path, fixture)

        result = _run_script(fixture.main, [_BRANCH])

        _assert_ready(result, "created", _expected_target(fixture.main, _BRANCH))
        assert _git(fixture.main, "rev-parse", _BRANCH).strip() == pushed_sha

    def test_force_pushed_default_branch_starts_from_the_rewritten_tip(self, tmp_path):
        """Only the `+` in the fetch refspec lets a non-fast-forward update reach refs/remotes/origin/<default>."""
        fixture = _make_fixture(tmp_path)
        replaced_sha = _push_from_second_clone(tmp_path, fixture)
        _git(fixture.main, "fetch", "-q", "origin")
        assert _git(fixture.main, "rev-parse", f"refs/remotes/origin/{fixture.default_branch}").strip() == replaced_sha
        rewritten_sha = _force_push_rewritten_tip(tmp_path, fixture)
        assert rewritten_sha != replaced_sha

        result = _run_script(fixture.main, [_BRANCH])

        _assert_ready(result, "created", _expected_target(fixture.main, _BRANCH))
        assert _git(fixture.main, "rev-parse", _BRANCH).strip() == rewritten_sha

    def test_missing_origin_head_is_refused_before_the_fetch(self, tmp_path):
        fixture = _make_fixture(tmp_path)
        _git(fixture.main, "remote", "set-head", "origin", "-d")

        result, before, after = _run_refusal(fixture, _BRANCH)

        _assert_refused(result, 1, "no-origin-head", before, after)

    def test_unreachable_origin_is_fetch_failed(self, tmp_path):
        fixture = _make_fixture(tmp_path)
        _git(fixture.main, "remote", "set-url", "origin", str(tmp_path / "does-not-exist.git"))
        before = _repo_state(fixture.main)

        result = _run_script(fixture.main, [_BRANCH])

        _assert_refused(result, 1, "fetch-failed", before, _repo_state(fixture.main))
        assert fixture.default_branch in _message_text(result, tmp_path)

    def test_absent_remote_tracking_ref_after_a_successful_fetch_is_fetch_failed(self, tmp_path):
        fixture = _make_fixture(tmp_path)
        shim_dir = _git_shim_on(
            tmp_path,
            "rev-parse",
            "  case \"$*\" in *'^{commit}'*) echo 'synthetic rev-parse failure' >&2; exit 1 ;; esac",
        )
        before = _repo_state(fixture.main)

        result = _run_script(fixture.main, [_BRANCH], env=_env_with_shim(shim_dir))

        _assert_refused(result, 1, "fetch-failed", before, _repo_state(fixture.main))
        message = _message_text(result, tmp_path)
        assert "is absent" in message
        assert "did not run" in message


# ---------------------------------------------------------------------------
# Inherited environment
# ---------------------------------------------------------------------------


def _environment_variants(main: Path, unrelated_repo: Path) -> dict[str, dict[str, str]]:
    return {
        "git-dir": {"GIT_DIR": str(main / ".git")},
        "git-work-tree": {"GIT_WORK_TREE": str(main)},
        "git-common-dir": {"GIT_COMMON_DIR": str(main / ".git")},
        "git-index-file": {"GIT_INDEX_FILE": str(main / ".git" / "index")},
        "git-dir-of-an-unrelated-repo": {"GIT_DIR": str(unrelated_repo / ".git")},
    }


class TestEnvironment:
    @pytest.mark.parametrize(
        "variant",
        ["git-dir", "git-work-tree", "git-common-dir", "git-index-file", "git-dir-of-an-unrelated-repo"],
    )
    def test_inherited_git_variables_do_not_redirect_the_script(self, tmp_path, variant):
        fixture = _make_fixture(tmp_path)
        unrelated_base = tmp_path / "unrelated"
        unrelated_base.mkdir()
        unrelated_repo, _ = _make_repo_with_remote(unrelated_base)
        env = _clean_env(**_environment_variants(fixture.main, unrelated_repo)[variant])
        target = _expected_target(fixture.main, _BRANCH)
        main_state_before = _main_checkout_state(fixture.main)

        created = _run_script(fixture.main, [_BRANCH], env=env)

        _assert_ready(created, "created", target)
        assert _main_checkout_state(fixture.main) == main_state_before
        assert f"worktree {target}\n" in _git(fixture.main, "worktree", "list", "--porcelain")
        if variant == "git-dir":
            _assert_ready(_run_script(fixture.main, [_BRANCH], env=env), "existing", target)
            _assert_ready(_run_script(target, [_BRANCH], env=env), "current", target)

    def test_fetch_runs_with_terminal_prompts_disabled(self, tmp_path):
        fixture = _make_fixture(tmp_path)
        seen_value = tmp_path / "terminal-prompt-seen"
        shim_dir = _git_shim_on(
            tmp_path, "fetch", f'  echo "${{GIT_TERMINAL_PROMPT-unset}}" > {shlex.quote(str(seen_value))}'
        )

        result = _run_script(fixture.main, [_BRANCH], env=_env_with_shim(shim_dir, GIT_TERMINAL_PROMPT="1"))

        _assert_ready(result, "created", _expected_target(fixture.main, _BRANCH))
        assert seen_value.read_text().strip() == "0"

    def test_worktree_add_runs_under_the_c_locale(self, tmp_path):
        """With LC_ALL unset and a UTF-8 LANG, only an exported LC_ALL=C reaches git's own process."""
        fixture = _make_fixture(tmp_path)
        seen_value = tmp_path / "lc-all-seen"
        shim_dir = _git_shim_on(
            tmp_path, "worktree add", f'  echo "${{LC_ALL-unset}}" > {shlex.quote(str(seen_value))}'
        )

        result = _run_script(
            fixture.main, [_BRANCH], env=_env_with_shim(shim_dir, LC_ALL=None, LANG="C.UTF-8")
        )

        _assert_ready(result, "created", _expected_target(fixture.main, _BRANCH))
        assert seen_value.read_text().strip() == "C"


# ---------------------------------------------------------------------------
# Refusals that settle without a write
# ---------------------------------------------------------------------------


class TestRefusals:
    def test_missing_when_a_locked_worktree_directory_was_removed(self, tmp_path):
        fixture = _make_fixture(tmp_path)
        decoy = _make_decoy_worktree(fixture.main)
        blocker = _expected_target(fixture.main, _BRANCH)
        _git(fixture.main, "worktree", "add", "-q", "-b", _BRANCH, str(blocker))
        _git(fixture.main, "worktree", "lock", str(blocker))
        shutil.rmtree(blocker)

        result, before, after = _run_refusal(fixture, _BRANCH)

        _assert_refused(result, 1, "missing", before, after)
        assert str(blocker) in _last_stderr_line(result)
        assert str(decoy) not in _last_stderr_line(result)

    def test_add_unfinished_when_the_lock_reason_is_initializing(self, tmp_path):
        fixture = _make_fixture(tmp_path)
        decoy = _make_decoy_worktree(fixture.main)
        blocker = _expected_target(fixture.main, _BRANCH)
        _git(fixture.main, "worktree", "add", "-q", "-b", _BRANCH, str(blocker))
        admin_dir = Path(_git(blocker, "rev-parse", "--absolute-git-dir").strip())
        (admin_dir / "locked").write_text("initializing")
        assert "locked initializing\n" in _git(fixture.main, "worktree", "list", "--porcelain")

        result, before, after = _run_refusal(fixture, _BRANCH)

        _assert_refused(result, 1, "add-unfinished", before, after)
        assert str(blocker) in _last_stderr_line(result)
        assert str(decoy) not in _last_stderr_line(result)

    def test_in_main_when_the_main_checkout_holds_the_branch(self, tmp_path):
        fixture = _make_fixture(tmp_path)
        _git(fixture.main, "checkout", "-q", "-b", _BRANCH)

        result, before, after = _run_refusal(fixture, _BRANCH)

        _assert_refused(result, 1, "in-main", before, after)

    def test_outside_when_the_worktree_sits_beside_the_worktrees_directory(self, tmp_path):
        fixture = _make_fixture(tmp_path)
        decoy = _make_decoy_worktree(fixture.main)
        _make_feature_branch(fixture.main, _BRANCH)
        outside_worktree = fixture.base / "outside-worktree"
        _make_worktree(fixture.main, _BRANCH, outside_worktree)

        result, before, after = _run_refusal(fixture, _BRANCH)

        _assert_refused(result, 1, "outside", before, after)
        assert str(outside_worktree) in _last_stderr_line(result)
        assert str(decoy) not in _last_stderr_line(result)

    def test_branch_without_worktree_names_the_tip_and_offers_no_add_command(self, tmp_path):
        fixture = _make_fixture(tmp_path)
        _git(fixture.main, "branch", _BRANCH)
        tip = _git(fixture.main, "rev-parse", _BRANCH).strip()

        result, before, after = _run_refusal(fixture, _BRANCH)

        _assert_refused(result, 1, "branch-without-worktree", before, after)
        message = _message_text(result, tmp_path)
        assert tip in message
        assert "git worktree list" in message
        assert "docs/scripts.md" in message
        assert "worktree add" not in message

    def test_ref_conflict_names_the_existing_branch(self, tmp_path):
        fixture = _make_fixture(tmp_path)
        _git(fixture.main, "branch", "GH-1")

        result, before, after = _run_refusal(fixture, "GH-1/new")

        _assert_refused(result, 1, "ref-conflict", before, after)
        assert "GH-1" in _message_text(result, tmp_path)

    def test_symlinked_worktrees_directory_is_refused_and_nothing_appears_behind_it(self, tmp_path):
        fixture = _make_fixture(tmp_path)
        outside = tmp_path / "outside-directory"
        outside.mkdir()
        (fixture.main / ".claude").mkdir()
        (fixture.main / ".claude" / "worktrees").symlink_to(outside)

        result, before, after = _run_refusal(fixture, _BRANCH)

        _assert_refused(result, 1, "symlinked-path", before, after)
        assert list(outside.iterdir()) == []

    def test_path_in_use_by_a_detached_worktree_at_the_target(self, tmp_path):
        fixture = _make_fixture(tmp_path)
        decoy = _make_decoy_worktree(fixture.main)
        blocker = _expected_target(fixture.main, _BRANCH)
        _git(fixture.main, "worktree", "add", "-q", "--detach", str(blocker))

        result, before, after = _run_refusal(fixture, _BRANCH)

        _assert_refused(result, 1, "path-in-use", before, after)
        assert str(blocker) in _last_stderr_line(result)
        assert str(decoy) not in _last_stderr_line(result)
        message = _message_text(result, tmp_path)
        assert "detached" in message
        assert tmp_path.name not in message

    def test_path_in_use_by_a_worktree_at_an_ancestor_of_the_target(self, tmp_path):
        fixture = _make_fixture(tmp_path)
        decoy = _make_decoy_worktree(fixture.main)
        blocker = fixture.main / ".claude" / "worktrees" / "foo"
        _git(fixture.main, "worktree", "add", "-q", "-b", "bar", str(blocker))

        result, before, after = _run_refusal(fixture, "foo/baz")

        _assert_refused(result, 1, "path-in-use", before, after)
        assert str(blocker) in _last_stderr_line(result)
        assert str(decoy) not in _last_stderr_line(result)
        assert "branch bar" in _message_text(result, tmp_path)

    def test_path_in_use_by_a_stray_git_entry_no_worktree_claims(self, tmp_path):
        """A worktree moved and then pruned leaves a directory whose `.git` points at a deleted record."""
        fixture = _make_fixture(tmp_path)
        original = fixture.main / ".claude" / "worktrees" / "old"
        _git(fixture.main, "worktree", "add", "-q", "-b", "other", str(original))
        original.rename(fixture.main / ".claude" / "worktrees" / "stale")
        _git(fixture.main, "worktree", "prune")

        result, before, after = _run_refusal(fixture, "stale/inner")

        _assert_refused(result, 1, "path-in-use", before, after)
        message = _message_text(result, tmp_path)
        assert "no registered worktree" in message
        assert "mov" not in message.casefold()

    def test_target_directory_with_an_entry_is_not_empty(self, tmp_path):
        fixture = _make_fixture(tmp_path)
        target = _expected_target(fixture.main, _BRANCH)
        target.mkdir(parents=True)
        (target / "file.txt").write_text("x\n")

        result, before, after = _run_refusal(fixture, _BRANCH)

        _assert_refused(result, 1, "target-not-empty", before, after)


# ---------------------------------------------------------------------------
# Failures after the fetch or the add
# ---------------------------------------------------------------------------


class TestLateFailures:
    def test_target_filled_after_the_checks_leaves_the_branch_and_reruns_as_branch_without_worktree(self, tmp_path):
        fixture = _make_fixture(tmp_path)
        pushed_sha = _push_from_second_clone(tmp_path, fixture)
        target = _expected_target(fixture.main, _BRANCH)
        shim_dir = _git_shim_on(
            tmp_path,
            "worktree add",
            f"  mkdir -p {shlex.quote(str(target))} && echo x > {shlex.quote(str(target / 'file'))}",
        )

        result = _run_script(fixture.main, [_BRANCH], env=_env_with_shim(shim_dir))

        _assert_refused_output(result, 1, "add-failed")
        assert "may remain" in _message_text(result, tmp_path)
        assert _git(fixture.main, "rev-parse", f"refs/heads/{_BRANCH}").strip() == pushed_sha
        assert f"branch refs/heads/{_BRANCH}\n" not in _git(fixture.main, "worktree", "list", "--porcelain")

        before = _repo_state(fixture.main)
        rerun = _run_script(fixture.main, [_BRANCH])
        _assert_refused(rerun, 1, "branch-without-worktree", before, _repo_state(fixture.main))
        assert pushed_sha in _message_text(rerun, tmp_path)

    def test_branch_that_appears_after_the_checks_is_not_reset(self, tmp_path):
        fixture = _make_fixture(tmp_path)
        older_sha = _git(fixture.main, "rev-parse", "HEAD").strip()
        _push_from_second_clone(tmp_path, fixture)
        assert not (fixture.main / ".claude").exists()
        main_state_before = _main_checkout_state(fixture.main)
        shim_dir = _git_shim_on(
            tmp_path,
            "worktree add",
            f"  {shlex.quote(_REAL_GIT)} -C {shlex.quote(str(fixture.main))} branch {shlex.quote(_BRANCH)} {older_sha}",
        )

        result = _run_script(fixture.main, [_BRANCH], env=_env_with_shim(shim_dir))

        _assert_refused_output(result, 1, "add-failed")
        assert _git(fixture.main, "rev-parse", _BRANCH).strip() == older_sha
        assert _main_checkout_state(fixture.main) == main_state_before
        assert not (fixture.main / ".claude").exists()

    def test_add_redirected_through_a_swapped_ancestor_is_outside(self, tmp_path):
        fixture = _make_fixture(tmp_path)
        outside = tmp_path / "outside-directory"
        outside.mkdir()
        claude_dir = fixture.main / ".claude"
        shim_dir = _git_shim_on(
            tmp_path,
            "worktree add",
            f"  mkdir -p {shlex.quote(str(claude_dir))} && "
            f"ln -s {shlex.quote(str(outside))} {shlex.quote(str(claude_dir / 'worktrees'))}",
        )

        result = _run_script(fixture.main, [_BRANCH], env=_env_with_shim(shim_dir))

        _assert_refused_output(result, 1, "outside")
        assert os.path.realpath(outside) in _last_stderr_line(result)

    def test_local_branch_listing_failure_is_list_failed_before_any_write(self, tmp_path):
        fixture = _make_fixture(tmp_path)
        shim_dir = _git_shim_failing_nth(tmp_path, "for-each-ref", 1)

        result, before, after = _run_refusal(fixture, _BRANCH, env=_env_with_shim(shim_dir))

        _assert_refused(result, 1, "list-failed", before, after)

    def test_post_add_listing_failure_says_the_add_may_have_succeeded(self, tmp_path):
        fixture = _make_fixture(tmp_path)
        shim_dir = _git_shim_failing_nth(tmp_path, "worktree list", 2)

        result = _run_script(fixture.main, [_BRANCH], env=_env_with_shim(shim_dir))

        _assert_refused_output(result, 1, "list-failed")
        assert "may have succeeded" in _message_text(result, tmp_path)


# ---------------------------------------------------------------------------
# Hooks that run inside the add
# ---------------------------------------------------------------------------


class TestHooksInsideTheAdd:
    def test_hook_that_empties_the_new_worktree_is_missing_and_stays_missing_on_rerun(self, tmp_path):
        fixture = _make_fixture(tmp_path)
        target = _expected_target(fixture.main, _BRANCH)
        _install_hook(
            fixture.main,
            "post-checkout",
            f'[ "$(pwd -P)" = {shlex.quote(str(target))} ] || exit 0\nfind . -mindepth 1 -delete',
        )

        result = _run_script(fixture.main, [_BRANCH])

        _assert_refused_output(result, 1, "missing")
        porcelain = _git(fixture.main, "worktree", "list", "--porcelain")
        assert f"worktree {target}\n" in porcelain
        assert f"branch refs/heads/{_BRANCH}\n" in porcelain
        assert not (target / ".git").exists()
        before = _repo_state(fixture.main)
        _assert_refused(_run_script(fixture.main, [_BRANCH]), 1, "missing", before, _repo_state(fixture.main))

    def test_failing_hook_that_prints_leaves_a_created_worktree_with_clean_stdout(self, tmp_path):
        """State decides, not exit status: git exits non-zero after a failing hook but keeps the checkout.

        Git relays a hook's stdout to its own stderr, so this case cannot show the script's own redirect.
        """
        fixture = _make_fixture(tmp_path)
        _install_hook(fixture.main, "post-checkout", "echo 'hook noise'\nexit 1")

        result = _run_script(fixture.main, [_BRANCH])

        _assert_ready(result, "created", _expected_target(fixture.main, _BRANCH))
        assert "hook noise" in result.stderr
