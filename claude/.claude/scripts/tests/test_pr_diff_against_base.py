"""Tests for pr-diff-against-base.sh.

gh is replaced in every test by a PATH shim answering `gh pr view --json
baseRefName --jq .baseRefName` -- a different, narrower shape than
test_cleanup_merged_branches.py's `_gh_shim_source` (which simulates
`gh pr list --head`), so this file writes its own. Real git operations run
against temporary repos built via conftest.py's shared scaffolding.

The `--context-files` mode's cases start at the "--context-files mode" banner below. Its pure helpers are
unit-tested in test_pr_diff_lib.py.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import textwrap
import time
from pathlib import Path

import pytest
from helpers import (
    CONTEXT_FILES_EXIT_ZERO_TOKEN_NAMES,
    CONTEXT_FILES_HALT_HEADING,
    GITLINK_AFTER,
    GITLINK_BEFORE,
    HOOKS_DIR,
    LANE_INDEX_ROW_FIELDS,
    ambient_umask,
    assert_cap_engaged,
    cumulative_review_value,
    file_mode,
    git_config_isolation_env,
    installed_locale_where,
    isolated_git_diff,
    isolated_git_env,
    require_cumulative_review_value,
    scaled_shim_sleep,
    set_gitlink,
    write_scaled_timeout_shim,
)

from ._pr_diff_helpers import (
    _DIFF_PIN,
    _DSA_KEY,
    _ED25519_KEY,
    _RSA_KEY,
    _SSH_DIR,
    _credential_read_gates_verdict,
    _git_out,
)
from .conftest import _commit, _init_repo, _make_feature_branch, _make_repo_with_remote

# Path to the script under test (resolved relative to this file)
_SCRIPT = Path(__file__).parent.parent / "pr-diff-against-base.sh"
_LIB_SH = HOOKS_DIR / "_lib.sh"


def _gh_shim_source(base_ref: str | None) -> str:
    """Return source for a gh shim answering `gh pr view --json baseRefName
    --jq .baseRefName`.

    base_ref=None models `gh pr view` failing (no PR open for this branch,
    or gh not authenticated) -- the shim exits 1 with no stdout, exercising
    pr-diff-against-base.sh's default-branch fallback path.
    """
    body = "sys.exit(1)" if base_ref is None else f"print({base_ref!r})"
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys
        args = sys.argv[1:]
        if args[:2] == ["pr", "view"] and "--json" in args and "baseRefName" in args:
            {body}
        else:
            sys.exit(1)
    """)


def _env_with_gh_shim(tmp_path: Path, base_ref: str | None) -> dict:
    """Build an env with a gh shim reporting base_ref prepended to PATH."""
    shim_dir = tmp_path / "gh_shim"
    shim_dir.mkdir()
    gh_shim = shim_dir / "gh"
    gh_shim.write_text(_gh_shim_source(base_ref))
    gh_shim.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(shim_dir), env.get("PATH", "")])
    return env


def _mv_shim_source(fail_when_path_contains: str) -> str:
    """Return source for an `mv` shim that fails only when one of its
    arguments contains fail_when_path_contains, delegating to the real mv
    otherwise -- lets a test force one mv call to fail (mktemp having
    already succeeded) while a different mv call in the same run still
    succeeds normally."""
    real_mv = shutil.which("mv")
    assert real_mv is not None
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import subprocess
        import sys
        args = sys.argv[1:]
        if any({fail_when_path_contains!r} in a for a in args):
            sys.exit(1)
        sys.exit(subprocess.run([{real_mv!r}] + args).returncode)
    """)


def _env_with_gh_shim_and_failing_mv(tmp_path: Path, base_ref: str | None, fail_when_path_contains: str) -> dict:
    """_env_with_gh_shim plus a PATH-prepended `mv` shim that fails only for
    the given path fragment -- forces a post-mktemp mv failure without
    touching mktemp itself, distinct from the chmod-based approach the
    prior-subject-intact tests use to fail mktemp."""
    env = _env_with_gh_shim(tmp_path, base_ref)
    mv_shim_dir = tmp_path / "mv_shim"
    mv_shim_dir.mkdir()
    mv_shim = mv_shim_dir / "mv"
    mv_shim.write_text(_mv_shim_source(fail_when_path_contains))
    mv_shim.chmod(0o755)
    env["PATH"] = os.pathsep.join([str(mv_shim_dir), env["PATH"]])
    return env


def _git_diff_shim_source(fail_when_args_contain: str) -> str:
    """Return source for a `git` shim that fails only a `git diff` or
    `git diff-tree` call whose arguments contain fail_when_args_contain,
    delegating to the real git otherwise. Merge-base resolution, checkouts,
    and the fixture's own setup calls all still hit the real git, so only
    the targeted diff call is forced to fail."""
    real_git = shutil.which("git")
    assert real_git is not None
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import subprocess
        import sys
        args = sys.argv[1:]
        if args and args[0] in ("diff", "diff-tree") and any({fail_when_args_contain!r} in a for a in args):
            sys.exit(1)
        sys.exit(subprocess.run([{real_git!r}] + args).returncode)
    """)


def _env_with_gh_shim_and_failing_git_diff(tmp_path: Path, base_ref: str | None, fail_when_args_contain: str) -> dict:
    """_env_with_gh_shim plus a PATH-prepended `git` shim that fails only
    the `git diff` or `git diff-tree` call whose arguments contain
    fail_when_args_contain."""
    env = _env_with_gh_shim(tmp_path, base_ref)
    git_shim_dir = tmp_path / "git_shim"
    git_shim_dir.mkdir()
    git_shim = git_shim_dir / "git"
    git_shim.write_text(_git_diff_shim_source(fail_when_args_contain))
    git_shim.chmod(0o755)
    env["PATH"] = os.pathsep.join([str(git_shim_dir), env["PATH"]])
    return env


def _run_script(
    repo: Path,
    env: dict,
    *,
    staged: bool = False,
    record: bool = False,
    diff_file: bool = False,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess:
    args = [str(_SCRIPT)]
    if staged:
        args.append("--staged")
    if record:
        args.append("--record")
    if diff_file:
        args.append("--diff-file")
    if extra_args:
        args.extend(extra_args)
    return subprocess.run(
        args, cwd=str(repo), env=env, capture_output=True, text=True, check=False,
    )


def _repo_hash(repo: Path) -> str:
    repo_root = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return hashlib.sha256(repo_root.encode()).hexdigest()


def _subject_path(env: dict, repo: Path, session_id: str) -> Path:
    """The recorded-subject path --record writes and `write cumulative-review`
    reads -- same repo-hash recipe as _lib.sh's _marker_lib_repo_hash, applied
    to _lib_repo_root's resolution of `repo`. Suffixed with the session id the
    same <repo-hash>.<session-id> way every completion marker is keyed."""
    config_dir = Path(env["CLAUDE_CONFIG_DIR"])
    return config_dir / "cumulative-review-subject-markers" / f"{_repo_hash(repo)}.{session_id}"


def _diff_artifact_path(env: dict, repo: Path, session_id: str) -> Path:
    """The diff-file artifact path --diff-file writes -- same repo-hash
    recipe as _subject_path above, keyed into its own directory since the two
    artifacts have independent lifecycles."""
    config_dir = Path(env["CLAUDE_CONFIG_DIR"])
    return config_dir / "cumulative-review-diff-markers" / f"{_repo_hash(repo)}.{session_id}"


def _staged_diff_artifact_path(env: dict, repo: Path, session_id: str) -> Path:
    """The diff-file artifact path --staged --diff-file writes. Same
    repo-hash recipe as _diff_artifact_path above, keyed into its own
    directory so a staged write never collides with the cumulative
    --diff-file artifact at the same repo-hash-and-session-id."""
    config_dir = Path(env["CLAUDE_CONFIG_DIR"])
    return config_dir / "code-review-diff-markers" / f"{_repo_hash(repo)}.{session_id}"


def _diff_file_announced_path(stderr: str) -> str:
    """Extract the path from stderr's `DIFF_FILE: <path>` line.

    Raises rather than returning None on absence -- an unconditional lookup,
    not a generator expression that would let a caller's assertion pass
    vacuously against a script that silently dropped the flag."""
    for line in stderr.splitlines():
        if line.startswith("DIFF_FILE: "):
            return line[len("DIFF_FILE: ") :]
    raise AssertionError(f"no DIFF_FILE: line in stderr: {stderr!r}")


def _seed_session(config_dir: Path, session_id: str, pid: int | None = None) -> None:
    """Write CONFIG_DIR/sessions/<pid> in the two-line format
    capture-session-id.sh writes, so pr-diff-against-base.sh's
    --record can resolve a session id via the same ancestor-walk
    _lib_resolve_session_id uses. Targets CONFIG_DIR directly rather than
    $HOME/.claude, since this file pins CLAUDE_CONFIG_DIR to an isolated tmp
    dir rather than isolating $HOME. Duplicated from
    hooks/tests/conftest.py's helper of the same shape rather than
    cross-imported, per that file's own neither-test-tree-imports-the-other
    convention.

    pid defaults to this test process's own pid: the walk reaches the
    pytest process itself, since pr-diff-against-base.sh runs as its direct
    subprocess with no intermediate shell.
    """
    target_pid = os.getpid() if pid is None else pid
    sessions_dir = config_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    start_time = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(target_pid)],
        env={**os.environ, "TZ": "UTC", "LC_ALL": "C"},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.rstrip("\n")
    (sessions_dir / str(target_pid)).write_text(f"{session_id}\n{start_time}\n")


def _make_branch_for_porcelain_config_probe(tmp_path: Path, *, gitmodules_ignore_all: bool) -> Path:
    """A repo on a pushed-base feature branch whose merge-base diff touches a file under `pkg/`, a file at
    the root, and a gitlink at `sub`, so config that rewrites a plain `git diff` has something to rewrite.

    `gitmodules_ignore_all` commits a `.gitmodules` entry for `sub` with `ignore = all`.
    """
    local, _bare = _make_repo_with_remote(tmp_path)
    numbered_lines = "".join(f"line {number}\n" for number in range(1, 12))
    (local / "pkg").mkdir()
    (local / "pkg" / "numbers.txt").write_text(numbered_lines)
    if gitmodules_ignore_all:
        (local / ".gitmodules").write_text('[submodule "sub"]\n\tpath = sub\n\turl = ./sub\n\tignore = all\n')
    subprocess.run(["git", "add", "-A"], cwd=local, check=True)
    set_gitlink(local, "sub", GITLINK_BEFORE)
    subprocess.run(["git", "commit", "-q", "-m", "base: numbered lines and a gitlink"], cwd=local, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=local, check=True)
    subprocess.run(["git", "checkout", "-q", "-b", "feat/porcelain-config-probe"], cwd=local, check=True)
    (local / "pkg" / "numbers.txt").write_text(numbered_lines.replace("line 6\n", "line six\n"))
    (local / "file.txt").write_text("edited at the root\n")
    subprocess.run(["git", "add", "-A"], cwd=local, check=True)
    set_gitlink(local, "sub", GITLINK_AFTER)
    subprocess.run(["git", "commit", "-q", "-m", "edit both files and move the gitlink"], cwd=local, check=True)
    return local


def _isolated_porcelain_diff(directory: Path, isolated_env: dict, *args: str) -> str:
    """Stdout of a plain `git diff --no-ext-diff *args, origin/main...HEAD` run from `directory` under `isolated_env`."""
    return subprocess.run(
        ["git", "diff", "--no-ext-diff", *args, "origin/main...HEAD"],
        cwd=directory, env=isolated_env, capture_output=True, text=True, check=True,
    ).stdout


class TestNormalPathAgainstMain:
    def test_diverged_feature_branch_diff_mentions_changed_file(self, tmp_path):
        """The simplest regression guard against an EXIT trap whose last
        command is a failing test clobbering $?: with neither --record nor
        --diff-file, TMP_FILE and DIFF_TMP stay empty for the whole run, so
        the cleanup trap's final `[ -n "$DIFF_TMP" ]` check evaluates false
        and would turn this returncode-0 assertion into a 1 without the
        trap's own explicit $? capture-and-restore."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/add-thing")
        subprocess.run(["git", "checkout", "-q", "feat/add-thing"], cwd=local, check=True)

        env = _env_with_gh_shim(tmp_path, "main")
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "diff --git a/file.txt b/file.txt" in result.stdout
        assert "+work on feat/add-thing" in result.stdout

    def test_merge_base_diff_ignores_color_and_external_diff_config(self, tmp_path):
        """Repo-local `color.ui=always` and `diff.external` must not reach the cumulative diff:
        colored output would defeat the cumulative-review normalizer's header rule,
        and an external diff driver prints text that is not a patch."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/user-diff-config")
        subprocess.run(["git", "checkout", "-q", "feat/user-diff-config"], cwd=local, check=True)
        subprocess.run(["git", "config", "color.ui", "always"], cwd=local, check=True)
        subprocess.run(["git", "config", "diff.external", "false"], cwd=local, check=True)

        env = _env_with_gh_shim(tmp_path, "main")
        result = _run_script(local, env)

        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("diff --git")
        assert "\x1b[" not in result.stdout

    @pytest.mark.parametrize(
        ("repo_config", "diff_opts_env", "run_from_subdirectory"),
        [
            pytest.param({"diff.relative": "true"}, None, True, id="diff-relative-from-a-subdirectory"),
            pytest.param({"diff.submodule": "log"}, None, False, id="diff-submodule-log"),
            pytest.param({"diff.context": "0"}, None, False, id="diff-context-zero"),
            pytest.param({"diff.noprefix": "true"}, None, False, id="diff-noprefix"),
            pytest.param({}, "-u0", False, id="git-diff-opts-zero-context"),
        ],
    )
    def test_merge_base_diff_is_unchanged_by_porcelain_diff_config(
        self, tmp_path, repo_config, diff_opts_env, run_from_subdirectory
    ):
        """The cumulative-review key hashes this text, so user-level diff config that rewrites a
        plain `git diff` (paths relative to the cwd, a submodule log summary, a narrowed context
        window) must not reach it."""
        local = _make_branch_for_porcelain_config_probe(tmp_path, gitmodules_ignore_all=False)
        run_directory = local / "pkg" if run_from_subdirectory else local
        isolated_env = isolated_git_env(_env_with_gh_shim(tmp_path, "main"), tmp_path / "empty-xdg-config-home")
        unconfigured = _run_script(run_directory, isolated_env)
        assert unconfigured.returncode == 0, unconfigured.stderr
        assert "Subproject commit" in unconfigured.stdout, "baseline carries the gitlink the config rewrites"
        plain_porcelain_diff_before_config = _isolated_porcelain_diff(run_directory, isolated_env)

        for key, value in repo_config.items():
            subprocess.run(["git", "config", key, value], cwd=local, check=True)
        if diff_opts_env is not None:
            merge_base = subprocess.run(
                ["git", "merge-base", "origin/main", "HEAD"],
                cwd=local, env=isolated_env, capture_output=True, text=True, check=True,
            ).stdout.strip()
            plumbing_command = ["git", "diff-tree", "-r", "-p", "-M", merge_base, "HEAD", "--"]
            plumbing_diff_without_variable = subprocess.run(
                plumbing_command, cwd=run_directory, env=isolated_env,
                capture_output=True, text=True, check=True,
            ).stdout
            plumbing_diff_with_variable = subprocess.run(
                plumbing_command, cwd=run_directory, env={**isolated_env, "GIT_DIFF_OPTS": diff_opts_env},
                capture_output=True, text=True, check=True,
            ).stdout
            assert plumbing_diff_with_variable != plumbing_diff_without_variable, (
                "control: the variable changes plumbing output, so the script's -u3 pin is load-bearing"
            )
            isolated_env["GIT_DIFF_OPTS"] = diff_opts_env
        plain_porcelain_diff_after_config = _isolated_porcelain_diff(run_directory, isolated_env)
        assert plain_porcelain_diff_after_config != plain_porcelain_diff_before_config, (
            "control: the config changes a plain git diff"
        )

        configured = _run_script(run_directory, isolated_env)

        assert configured.returncode == 0, configured.stderr
        assert configured.stdout == unconfigured.stdout
        if run_from_subdirectory:
            assert _run_script(local, isolated_env).stdout == unconfigured.stdout, (
                "the repo-root run and the subdirectory run print the same text"
            )

    @pytest.mark.parametrize(
        ("repo_config", "gitmodules_ignore_all"),
        [
            pytest.param({"diff.ignoreSubmodules": "all"}, False, id="diff-ignore-submodules-config"),
            pytest.param({}, True, id="gitmodules-ignore-all"),
        ],
    )
    def test_merge_base_diff_keeps_a_gitlink_change_under_ignore_submodules_setting(
        self, tmp_path, repo_config, gitmodules_ignore_all
    ):
        """An ignore-all submodule setting must not drop a gitlink change from the hashed diff text,
        or a PR that only moves a submodule pointer would hash like an empty diff."""
        local = _make_branch_for_porcelain_config_probe(tmp_path, gitmodules_ignore_all=gitmodules_ignore_all)
        for key, value in repo_config.items():
            subprocess.run(["git", "config", key, value], cwd=local, check=True)
        isolated_env = isolated_git_env(_env_with_gh_shim(tmp_path, "main"), tmp_path / "empty-xdg-config-home")
        porcelain_diff_with_ignore_overridden = _isolated_porcelain_diff(local, isolated_env, "--ignore-submodules=none")
        assert "Subproject commit" in porcelain_diff_with_ignore_overridden, "control: the gitlink is in the range"
        plain_porcelain_diff = _isolated_porcelain_diff(local, isolated_env)
        assert "Subproject commit" not in plain_porcelain_diff, "control: the setting hides the gitlink from a plain git diff"
        if gitmodules_ignore_all:
            # Under a .gitmodules `ignore`, plumbing drops the gitlink too, so --ignore-submodules=none must keep it.
            merge_base = subprocess.run(
                ["git", "merge-base", "origin/main", "HEAD"],
                cwd=local, env=isolated_env, capture_output=True, text=True, check=True,
            ).stdout.strip()
            plumbing_diff_without_override = subprocess.run(
                ["git", "diff-tree", "-r", "-p", "-M", merge_base, "HEAD", "--"],
                cwd=local, env=isolated_env, capture_output=True, text=True, check=True,
            ).stdout
            assert "diff --git a/pkg/numbers.txt b/pkg/numbers.txt" in plumbing_diff_without_override, (
                "control: the plumbing diff is real and only the gitlink is missing from it"
            )
            assert "Subproject commit" not in plumbing_diff_without_override, (
                "control: the setting hides the gitlink from plumbing too, so --ignore-submodules=none is load-bearing"
            )

        result = _run_script(local, isolated_env)

        assert result.returncode == 0, result.stderr
        assert f"-Subproject commit {GITLINK_BEFORE}" in result.stdout
        assert f"+Subproject commit {GITLINK_AFTER}" in result.stdout

    def test_default_config_output_equals_the_porcelain_diff_of_the_merge_base_range(self, tmp_path):
        """Pins that rename detection is on and that plumbing output is byte-equal to the porcelain's for these eight
        shapes, in a repo with no textconv driver: a rename with a content edit, a mode change, a binary file,
        a non-ASCII path, a moved gitlink, a deleted file, a newly added empty file and a file edited to lose its
        trailing newline."""
        local, _bare = _make_repo_with_remote(tmp_path)
        (local / "rename-source.txt").write_text("".join(f"stable line {number}\n" for number in range(20)))
        (local / "run.sh").write_text("#!/bin/sh\n")
        (local / "image.bin").write_bytes(b"\x00base binary\x01")
        (local / "caf\u00e9.txt").write_text("base\n")
        (local / "deleted.txt").write_text("removed on the feature branch\n")
        (local / "loses-newline.txt").write_text("first line\nlast line\n")
        subprocess.run(["git", "add", "-A"], cwd=local, check=True)
        set_gitlink(local, "sub", GITLINK_BEFORE)
        subprocess.run(["git", "commit", "-q", "-m", "base: eight shapes"], cwd=local, check=True)
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=local, check=True)
        subprocess.run(["git", "checkout", "-q", "-b", "feat/eight-shapes"], cwd=local, check=True)
        subprocess.run(["git", "mv", "rename-source.txt", "rename-target.txt"], cwd=local, check=True)
        (local / "rename-target.txt").write_text(
            "".join("edited line 10\n" if number == 10 else f"stable line {number}\n" for number in range(20))
        )
        (local / "image.bin").write_bytes(b"\x00feature binary\x02")
        (local / "caf\u00e9.txt").write_text("feature\n")
        (local / "deleted.txt").unlink()
        (local / "empty.txt").write_text("")
        (local / "loses-newline.txt").write_text("first line\nlast line")
        subprocess.run(["git", "add", "-A"], cwd=local, check=True)
        set_gitlink(local, "sub", GITLINK_AFTER)
        subprocess.run(["git", "update-index", "--chmod=+x", "run.sh"], cwd=local, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "feature: edit all eight shapes"], cwd=local, check=True)

        env = isolated_git_env(_env_with_gh_shim(tmp_path, "main"), tmp_path / "empty-xdg-config-home")
        result = _run_script(local, env)

        assert result.returncode == 0, result.stderr
        assert "rename from rename-source.txt" in result.stdout
        assert "old mode 100644" in result.stdout
        assert "Binary files a/image.bin and b/image.bin differ" in result.stdout
        assert "caf\\303\\251.txt" in result.stdout, "the non-ASCII path prints in git's quoted form"
        assert f"+Subproject commit {GITLINK_AFTER}" in result.stdout
        assert "deleted file mode 100644" in result.stdout
        assert "diff --git a/empty.txt b/empty.txt\nnew file mode 100644" in result.stdout
        assert "\\ No newline at end of file" in result.stdout
        assert result.stdout.encode().rstrip(b"\n") == isolated_git_diff(local, "origin/main...HEAD")

    def test_stale_branch_diff_excludes_a_change_made_only_on_the_advanced_base(self, tmp_path):
        """The diff runs from the merge-base, so a base commit the un-rebased branch lacks must not
        print as a reversion of that commit."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/stale")
        (local / "base-only.txt").write_text("added on the base after the branch forked\n")
        subprocess.run(["git", "add", "base-only.txt"], cwd=local, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "base: advance past the fork point"], cwd=local, check=True)
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=local, check=True)
        subprocess.run(["git", "checkout", "-q", "feat/stale"], cwd=local, check=True)

        result = _run_script(local, _env_with_gh_shim(tmp_path, "main"))

        assert result.returncode == 0, result.stderr
        assert "diff --git a/file.txt b/file.txt" in result.stdout
        assert "base-only.txt" not in result.stdout

    def test_a_tracked_file_named_like_a_revision_does_not_break_the_diff(self, tmp_path):
        """A file named `HEAD` in the cwd must not make `git diff-tree` exit 128 (ambiguous argument):
        the trailing `--` ends revision parsing, so git skips the revision-versus-filename ambiguity check."""
        local, _bare = _make_repo_with_remote(tmp_path)
        subprocess.run(["git", "checkout", "-q", "-b", "feat/file-named-head"], cwd=local, check=True)
        (local / "HEAD").write_text("a file, not a revision\n")
        subprocess.run(["git", "add", "HEAD"], cwd=local, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add a root file named HEAD"], cwd=local, check=True)
        env = _env_with_gh_shim(tmp_path, "main")
        isolated_env = isolated_git_env(env, tmp_path / "empty-xdg-config-home")
        merge_base = subprocess.run(
            ["git", "merge-base", "origin/main", "HEAD"],
            cwd=local, env=isolated_env, capture_output=True, text=True, check=True,
        ).stdout.strip()
        # LC_ALL=C keeps git's error message in English for the stderr phrase below.
        diff_tree_without_terminator = subprocess.run(
            ["git", "diff-tree", "-r", "-p", "-M", merge_base, "HEAD"],
            cwd=local, env={**isolated_env, "LC_ALL": "C"}, capture_output=True, text=True,
        )
        assert diff_tree_without_terminator.returncode == 128, "control: without the terminator git rejects the ambiguous HEAD"
        assert "ambiguous argument" in diff_tree_without_terminator.stderr

        result = _run_script(local, env)

        assert result.returncode == 0, result.stderr
        assert "diff --git a/HEAD b/HEAD" in result.stdout

    def test_textconv_driver_does_not_convert_the_text(self, tmp_path):
        """The cumulative diff shows the raw bytes of a path with a `textconv` driver, never the driver's output."""
        local, _bare = _make_repo_with_remote(tmp_path)
        (local / ".gitattributes").write_text("notes.txt diff=shout\n")
        (local / "notes.txt").write_text("quiet base\n")
        subprocess.run(["git", "config", "diff.shout.textconv", "sed s/quiet/CONVERTED/"], cwd=local, check=True)
        subprocess.run(["git", "add", "-A"], cwd=local, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "base: a path with a textconv driver"], cwd=local, check=True)
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=local, check=True)
        subprocess.run(["git", "checkout", "-q", "-b", "feat/textconv"], cwd=local, check=True)
        (local / "notes.txt").write_text("quiet feature\n")
        subprocess.run(["git", "commit", "-q", "-a", "-m", "feature: edit the textconv path"], cwd=local, check=True)
        isolated_env = isolated_git_env(_env_with_gh_shim(tmp_path, "main"), tmp_path / "empty-xdg-config-home")
        porcelain_diff = _isolated_porcelain_diff(local, isolated_env)
        assert "CONVERTED" in porcelain_diff, "control: the driver converts a plain git diff"

        result = _run_script(local, isolated_env)

        assert result.returncode == 0, result.stderr
        assert "+quiet feature" in result.stdout
        assert "CONVERTED" not in result.stdout

    def test_binary_file_with_a_textconv_driver_prints_binary_files_differ_and_keeps_its_index_line(self, tmp_path):
        """A binary path with a `textconv` driver prints `Binary files ... differ` with its `index` line,
        never the driver's output."""
        local, _bare = _make_repo_with_remote(tmp_path)
        (local / ".gitattributes").write_text("blob.bin diff=shout\n")
        (local / "blob.bin").write_bytes(b"\x00quiet base")
        subprocess.run(["git", "config", "diff.shout.textconv", "sed s/quiet/CONVERTED/"], cwd=local, check=True)
        subprocess.run(["git", "add", "-A"], cwd=local, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "base: a binary path with a textconv driver"], cwd=local, check=True)
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=local, check=True)
        subprocess.run(["git", "checkout", "-q", "-b", "feat/binary-textconv"], cwd=local, check=True)
        (local / "blob.bin").write_bytes(b"\x00quiet feature")
        subprocess.run(["git", "commit", "-q", "-a", "-m", "feature: edit the binary textconv path"], cwd=local, check=True)
        isolated_env = isolated_git_env(_env_with_gh_shim(tmp_path, "main"), tmp_path / "empty-xdg-config-home")
        textconv_porcelain_diff = _isolated_porcelain_diff(local, isolated_env, "--textconv")
        assert "CONVERTED" in textconv_porcelain_diff, "control: the driver converts a plain git diff --textconv"

        result = _run_script(local, isolated_env)

        assert result.returncode == 0, result.stderr
        assert "Binary files a/blob.bin and b/blob.bin differ" in result.stdout
        assert "\nindex " in result.stdout
        assert "CONVERTED" not in result.stdout

    def test_cumulative_review_value_ignores_a_nul_byte_in_an_edited_line_past_the_binary_sniff_window(self, tmp_path):
        """Named residual: two cumulative diffs that differ only by a NUL byte in an edited line past git's
        8000-byte binary-sniff window share one cumulative-review value.

        Git still prints the file as text, and the script's command substitution drops the NUL.
        The key therefore makes no claim about NUL bytes.
        """
        git_binary_sniff_window_bytes = 8000
        base_lines = [f"line {number:04d}\n" for number in range(3000)]
        edited_line_index = 2900
        assert len("".join(base_lines[:edited_line_index]).encode()) > git_binary_sniff_window_bytes
        local, _bare = _make_repo_with_remote(tmp_path)
        (local / "long.txt").write_text("".join(base_lines))
        subprocess.run(["git", "add", "long.txt"], cwd=local, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "base: a text file longer than the sniff window"], cwd=local, check=True)
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=local, check=True)
        env = isolated_git_env(_env_with_gh_shim(tmp_path, "main"), tmp_path / "empty-xdg-config-home")

        def diff_script_and_git_for_edit(branch: str, edited_line: bytes) -> tuple[subprocess.CompletedProcess, bytes]:
            subprocess.run(["git", "checkout", "-q", "-b", branch, "main"], cwd=local, check=True)
            edited_lines = [line.encode() for line in base_lines]
            edited_lines[edited_line_index] = edited_line
            (local / "long.txt").write_bytes(b"".join(edited_lines))
            subprocess.run(["git", "commit", "-q", "-a", "-m", f"feature: edit line {edited_line_index}"], cwd=local, check=True)
            return _run_script(local, env), isolated_git_diff(local, "origin/main...HEAD")

        plain_result, _ = diff_script_and_git_for_edit("feat/plain-edit", b"edited\n")
        nul_result, nul_git_diff = diff_script_and_git_for_edit("feat/nul-edit", b"edit\x00ed\n")

        assert plain_result.returncode == 0, plain_result.stderr
        assert nul_result.returncode == 0, nul_result.stderr
        assert b"edit\x00ed" in nul_git_diff, "control: git prints the NUL-bearing edit as text"
        assert "Binary files" not in nul_result.stdout
        assert "\x00" not in nul_result.stdout, "command substitution drops the NUL"
        assert plain_result.stdout != nul_result.stdout, "control: the post-image blob ids in the index lines differ"
        plain_value = require_cumulative_review_value(plain_result.stdout.encode().rstrip(b"\n"), tmp_path)
        nul_value = require_cumulative_review_value(nul_result.stdout.encode().rstrip(b"\n"), tmp_path)
        assert plain_value == nul_value


class TestGhPrViewFailureFallback:
    def test_gh_pr_view_failure_still_diffs_against_main(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/fallback")
        subprocess.run(["git", "checkout", "-q", "feat/fallback"], cwd=local, check=True)

        env = _env_with_gh_shim(tmp_path, None)
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "diff --git a/file.txt b/file.txt" in result.stdout
        assert "+work on feat/fallback" in result.stdout

    def test_gh_pr_view_failure_resolves_non_main_default_branch(self, tmp_path):
        # "trunk" is outside main/master/develop so this only passes via
        # symbolic-ref, not the candidate loop.
        local, _bare = _make_repo_with_remote(tmp_path, default_branch="trunk")
        _make_feature_branch(local, "feat/on-trunk", return_to="trunk")
        subprocess.run(["git", "checkout", "-q", "feat/on-trunk"], cwd=local, check=True)

        env = _env_with_gh_shim(tmp_path, None)
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "+work on feat/on-trunk" in result.stdout
        assert "defaulting base to trunk" in result.stderr

    def test_gh_pr_view_failure_resolves_slash_containing_default_branch(self, tmp_path):
        # _lib_default_branch_from_origin_head strips the literal
        # refs/remotes/origin/ prefix, so a multi-segment default branch
        # name must survive intact.
        local, _bare = _make_repo_with_remote(tmp_path, default_branch="release/1.0")
        _make_feature_branch(local, "feat/on-release", return_to="release/1.0")
        subprocess.run(["git", "checkout", "-q", "feat/on-release"], cwd=local, check=True)

        env = _env_with_gh_shim(tmp_path, None)
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "+work on feat/on-release" in result.stdout
        assert "defaulting base to release/1.0" in result.stderr


class TestMergeBaseFailure:
    def test_unresolvable_base_ref_aborts_naming_the_ref_on_stderr(self, tmp_path):
        # gh reports a base ref that was never fetched as origin/<name> locally --
        # distinct from the gh-failure fallback above, which always resolves against
        # the local origin/main that _make_repo_with_remote already sets up.
        local, _bare = _make_repo_with_remote(tmp_path)

        env = _env_with_gh_shim(tmp_path, "nonexistent-base")
        result = _run_script(local, env)

        assert result.returncode == 1
        assert result.stdout == ""
        assert "origin/nonexistent-base" in result.stderr


class TestCumulativeDiffFailure:
    def test_diff_against_merge_base_failure_produces_curated_message(self, tmp_path):
        """A `git diff-tree` failure after merge-base resolution already
        succeeded must produce this script's own curated message and exit
        1, not a bare set -e abort surfacing git's own stderr and exit
        status."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/diff-failure")
        subprocess.run(["git", "checkout", "-q", "feat/diff-failure"], cwd=local, check=True)
        merge_base = subprocess.run(
            ["git", "merge-base", "origin/main", "HEAD"], cwd=local,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        env = _env_with_gh_shim_and_failing_git_diff(tmp_path, "main", merge_base)

        result = _run_script(local, env)

        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr == f"pr-diff-against-base.sh: could not compute the diff against {merge_base}\n"


class TestCandidateLoopFallback:
    def test_missing_origin_head_falls_back_to_candidate_loop(self, tmp_path):
        # When origin/HEAD's symref is absent, the main/master/develop
        # candidate loop must still recover origin/main.
        local, _bare = _make_repo_with_remote(tmp_path)
        subprocess.run(["git", "remote", "set-head", "origin", "--delete"], cwd=local, check=True)
        _make_feature_branch(local, "feat/no-symref", return_to="main")
        subprocess.run(["git", "checkout", "-q", "feat/no-symref"], cwd=local, check=True)

        env = _env_with_gh_shim(tmp_path, None)
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "+work on feat/no-symref" in result.stdout
        assert "defaulting base to main" in result.stderr

    def test_candidate_loop_prefers_main_when_multiple_candidates_exist(self, tmp_path):
        # main/master/develop are probed in that order, so main must win
        # when both exist as origin branches.
        local, _bare = _make_repo_with_remote(tmp_path)
        subprocess.run(["git", "checkout", "-q", "-b", "master"], cwd=local, check=True)
        subprocess.run(["git", "push", "-q", "origin", "master"], cwd=local, check=True)
        subprocess.run(["git", "checkout", "-q", "main"], cwd=local, check=True)
        subprocess.run(["git", "remote", "set-head", "origin", "--delete"], cwd=local, check=True)
        _make_feature_branch(local, "feat/multi-candidate", return_to="main")
        subprocess.run(["git", "checkout", "-q", "feat/multi-candidate"], cwd=local, check=True)

        env = _env_with_gh_shim(tmp_path, None)
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "+work on feat/multi-candidate" in result.stdout
        assert "defaulting base to main" in result.stderr

    def test_dangling_origin_head_target_falls_back_to_live_candidate(self, tmp_path):
        # origin/HEAD's symref still resolves to refs/remotes/origin/main, but
        # that target ref is gone locally while origin/develop stays live --
        # _lib_default_branch_from_origin_head's verify step rejects the
        # dangling target, and the candidate loop must recover "develop".
        local, _bare = _make_repo_with_remote(tmp_path)
        subprocess.run(["git", "checkout", "-q", "-b", "develop"], cwd=local, check=True)
        subprocess.run(["git", "push", "-q", "origin", "develop"], cwd=local, check=True)
        subprocess.run(["git", "checkout", "-q", "main"], cwd=local, check=True)
        subprocess.run(
            ["git", "update-ref", "-d", "refs/remotes/origin/main"], cwd=local, check=True
        )
        _make_feature_branch(local, "feat/dangling-target", return_to="main")
        subprocess.run(["git", "checkout", "-q", "feat/dangling-target"], cwd=local, check=True)

        env = _env_with_gh_shim(tmp_path, None)
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "+work on feat/dangling-target" in result.stdout
        assert "defaulting base to develop" in result.stderr


class TestReportedBaseOverridesDefaultBranch:
    def test_stacked_pr_diffs_against_reported_base_not_repo_default(self, tmp_path):
        # gh's reported base must win over the repo's own default branch.
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "staging")
        subprocess.run(["git", "checkout", "-q", "staging"], cwd=local, check=True)
        _make_feature_branch(local, "feat/stacked", return_to="staging")
        subprocess.run(["git", "checkout", "-q", "feat/stacked"], cwd=local, check=True)

        env = _env_with_gh_shim(tmp_path, "staging")
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "+work on feat/stacked" in result.stdout
        assert "-work on staging" in result.stdout
        assert result.stderr == ""


class TestDefaultBranchUnresolvable:
    def test_repo_without_origin_aborts_naming_the_resolution_failure(self, tmp_path):
        # Regression test: no origin remote at all must produce a message
        # naming the resolution failure, not a stale "origin/main" guess.
        local = tmp_path / "no-remote"
        _init_repo(local)
        _commit(local, "init")

        env = _env_with_gh_shim(tmp_path, None)
        result = _run_script(local, env)

        assert result.returncode == 1
        assert result.stdout == ""
        assert "no default branch resolved" in result.stderr


def _lib_cumulative_diff_hash(repo_root: str, pr_diff_script: str, env: dict) -> str:
    """Shell out to the real _lib_cumulative_diff_hash against `repo_root`,
    driving PR_DIFF_SCRIPT through `env`'s own gh shim so it resolves the
    same base ref pr-diff-against-base.sh's --record invocation did."""
    result = subprocess.run(
        [
            "bash", "-c",
            f'. "{_LIB_SH}"; _lib_cumulative_diff_hash "$1" "$2"',
            "_cumulative_diff_hash", repo_root, pr_diff_script,
        ],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return result.stdout.strip()


class TestRecordFlag:
    """--record's own behavior. Three invariants, each with its own test
    method below:

    - Must never change the bare invocation's stdout.
    - Must always print the diff before its own resolution can fail.
    - Must produce a subject whose hashed value agrees byte-for-byte with
      _lib_cumulative_diff_hash's -- required because a second hashing
      recipe that strips text differently would make marker.sh's stored
      value and status's recomputed value permanently and silently
      disagree.
    """

    SID = "test-session-record-flag"

    @pytest.fixture(autouse=True)
    def _seeded_session(self, tmp_path):
        # Matches _isolate_transcript_corpus_lookups' own CLAUDE_CONFIG_DIR
        # value (same tmp_path fixture instance) so --record's session-id
        # resolution finds this entry.
        _seed_session(tmp_path / "isolated-claude-config", self.SID)

    def test_bare_invocation_stdout_unchanged_by_record_flag(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/parity")
        subprocess.run(["git", "checkout", "-q", "feat/parity"], cwd=local, check=True)
        env = _env_with_gh_shim(tmp_path, "main")

        bare_result = _run_script(local, env)
        record_result = _run_script(local, env, record=True)

        assert bare_result.returncode == 0
        assert record_result.returncode == 0
        assert bare_result.stdout == record_result.stdout

    def test_record_writes_subject_matching_stdout_minus_trailing_newline(self, tmp_path):
        """The subject file omits the mechanically appended trailing newline
        stdout carries (see the empty-diff test below for why), so it
        matches stdout with exactly that one newline stripped."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/record")
        subprocess.run(["git", "checkout", "-q", "feat/record"], cwd=local, check=True)
        env = _env_with_gh_shim(tmp_path, "main")

        result = _run_script(local, env, record=True)
        assert result.returncode == 0, result.stderr

        subject = _subject_path(env, local, self.SID)
        assert subject.read_text() == result.stdout[:-1]

    def test_record_writes_zero_byte_subject_for_a_genuinely_empty_diff(self, tmp_path):
        """A repo already at parity with its merge-base (no feature branch,
        nothing to diff) must record a 0-byte subject -- a format pin on the
        recorded artifact itself. This 0-byte pin is independent of how
        marker.sh judges emptiness, which reads the file through its own
        command-substitution lens, not this raw byte count. Still a
        ground-truth assertion, not a comparison against this script's own
        stdout, which would share any padding bug."""
        local, _bare = _make_repo_with_remote(tmp_path)
        env = _env_with_gh_shim(tmp_path, "main")

        result = _run_script(local, env, record=True)
        assert result.returncode == 0, result.stderr
        # Bare stdout still carries the mechanically appended newline (see
        # the comment above DIFF_TEXT's declaration) -- only the subject
        # file itself is byte-exact for this 0-byte format pin.
        assert result.stdout == "\n"

        subject = _subject_path(env, local, self.SID)
        assert subject.stat().st_size == 0

    def test_record_overwrites_a_prior_subject_with_fresh_content(self, tmp_path):
        """A second --record must replace the subject with the current diff,
        not append to or leave stale content alongside it -- mktemp+mv
        overwrites the target atomically, but nothing else in the pipeline
        guarantees that without this test."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/re-record")
        subprocess.run(["git", "checkout", "-q", "feat/re-record"], cwd=local, check=True)
        env = _env_with_gh_shim(tmp_path, "main")

        first = _run_script(local, env, record=True)
        assert first.returncode == 0, first.stderr
        subject = _subject_path(env, local, self.SID)
        first_content = subject.read_text()

        (local / "second.txt").write_text("second file\n")
        subprocess.run(["git", "add", "second.txt"], cwd=local, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "second commit"], cwd=local, check=True)

        second = _run_script(local, env, record=True)
        assert second.returncode == 0, second.stderr
        second_content = subject.read_text()

        assert second_content == second.stdout[:-1]
        assert second_content != first_content
        assert "second.txt" in second_content

    def test_record_prints_before_any_record_path_failure(self, tmp_path):
        """A CONFIG_DIR resolution failure in the record path must never cost
        the caller the diff it asked for -- the diff already reaches stdout
        before --record's own resolution runs. A relative CLAUDE_CONFIG_DIR
        makes _lib_config_dir fail, per its own documented contract."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/unresolvable-config-dir")
        subprocess.run(
            ["git", "checkout", "-q", "feat/unresolvable-config-dir"], cwd=local, check=True
        )
        env = _env_with_gh_shim(tmp_path, "main")
        env["CLAUDE_CONFIG_DIR"] = "relative-and-unresolvable"

        result = _run_script(local, env, record=True)
        assert result.returncode == 0, result.stderr
        assert "diff --git a/file.txt b/file.txt" in result.stdout
        assert "subject not recorded" in result.stderr

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_record_path_failure_leaves_prior_subject_intact(self, tmp_path):
        """A failed record pipeline must not touch a subject already on disk
        from a prior successful record -- the mktemp+mv sequence only
        replaces the subject on a fully successful write."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/protect-subject")
        subprocess.run(["git", "checkout", "-q", "feat/protect-subject"], cwd=local, check=True)
        env = _env_with_gh_shim(tmp_path, "main")

        first = _run_script(local, env, record=True)
        assert first.returncode == 0, first.stderr
        subject = _subject_path(env, local, self.SID)
        prior_content = subject.read_text()

        # Read+execute only: mktemp can no longer create a new temp file in
        # this directory, so the pipeline fails before ever reaching mv.
        subject.parent.chmod(0o555)
        try:
            second = _run_script(local, env, record=True)
        finally:
            subject.parent.chmod(0o755)

        assert second.returncode == 0, (
            "a record-path failure must not change the script's exit code"
        )
        assert "diff --git a/file.txt b/file.txt" in second.stdout
        assert subject.read_text() == prior_content

    def test_recorded_content_hashes_identically_to_lib_cumulative_diff_hash(self, tmp_path):
        """The byte-equality property this comparison exists for: hashing
        the recorded subject through _lib_cumulative_review_value must equal
        _lib_cumulative_diff_hash's own value for the same tree, so a
        second, independently-drifting hashing recipe can never make the
        write-side and read-side values silently disagree."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/byte-equality")
        subprocess.run(["git", "checkout", "-q", "feat/byte-equality"], cwd=local, check=True)
        env = _env_with_gh_shim(tmp_path, "main")

        result = _run_script(local, env, record=True)
        assert result.returncode == 0, result.stderr
        subject = _subject_path(env, local, self.SID)
        # Trailing newlines stripped, mirroring bash command substitution's
        # own stripping inside _lib_cumulative_diff_hash -- without it this
        # comparison would fail on the newline boundary alone, not on a
        # real recipe mismatch.
        recorded_text = subject.read_bytes().rstrip(b"\n")

        subject_exit_status, from_subject = cumulative_review_value(recorded_text, tmp_path)
        assert subject_exit_status == 0

        repo_root = subprocess.run(
            ["git", "-C", str(local), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        from_cumulative_hash = _lib_cumulative_diff_hash(repo_root, str(_SCRIPT), env)

        assert from_subject == from_cumulative_hash


class TestRecordFlagWithoutASession:
    """No test in this class seeds a session file, unlike TestRecordFlag's
    autouse fixture. This exercises --record's session-id-resolution failure
    branch specifically, distinct from TestRecordFlag's own CONFIG_DIR
    failure test above."""

    def test_record_prints_before_session_resolution_failure(self, tmp_path):
        """No live session file exists, so _lib_resolve_session_id fails.
        --record must still print the diff and fail the subject recording
        open, the same posture every other --record failure mode uses."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/no-session")
        subprocess.run(["git", "checkout", "-q", "feat/no-session"], cwd=local, check=True)
        env = _env_with_gh_shim(tmp_path, "main")

        result = _run_script(local, env, record=True)
        assert result.returncode == 0, result.stderr
        assert "diff --git a/file.txt b/file.txt" in result.stdout
        # The resolution-specific fragment, not "subject not recorded" alone:
        # all four --record failure branches share that tail, so a bare-tail
        # assertion can't distinguish this branch from the other three.
        assert "could not resolve the repo root, config directory, or session id" in result.stderr
        subject_dir = Path(env["CLAUDE_CONFIG_DIR"]) / "cumulative-review-subject-markers"
        assert not subject_dir.exists() or list(subject_dir.iterdir()) == []


class TestDiffFileFlag:
    """--diff-file's own behavior -- the mirror of TestRecordFlag above for
    the step-4 diff-handoff artifact. Same invariants (never changes bare
    stdout; always prints the diff before its own resolution can fail; a
    write failure never withholds the diff or changes the exit code), plus
    two properties specific to this artifact: supersession at a fixed path
    (the property a random-suffix mktemp under ${TMPDIR:-/tmp} cannot
    deliver) and a pinned 0600 mode (since a direct `>` redirect would
    instead be umask-dependent)."""

    SID = "test-session-diff-file-flag"

    @pytest.fixture(autouse=True)
    def _seeded_session(self, tmp_path):
        # Same isolated CLAUDE_CONFIG_DIR _isolate_transcript_corpus_lookups
        # pins for this whole test tree, matching TestRecordFlag's own fixture.
        _seed_session(tmp_path / "isolated-claude-config", self.SID)

    def test_bare_invocation_stdout_unchanged_by_diff_file_flag(self, tmp_path):
        """The central property's first conjunct: --diff-file changes stdout
        by zero bytes, alone and combined with --record. This conjunct alone
        passes against the *unmodified* script (the no-unknown-argument
        parser silently drops the flag), so it never stands on its own --
        see test_diff_file_writes_artifact_matching_full_stdout below for the
        teeth."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/diff-file-parity")
        subprocess.run(["git", "checkout", "-q", "feat/diff-file-parity"], cwd=local, check=True)
        env = _env_with_gh_shim(tmp_path, "main")

        bare_result = _run_script(local, env)
        diff_file_result = _run_script(local, env, diff_file=True)
        record_result = _run_script(local, env, record=True)
        combined_result = _run_script(local, env, record=True, diff_file=True)

        assert bare_result.returncode == 0
        assert diff_file_result.returncode == 0
        assert record_result.returncode == 0
        assert combined_result.returncode == 0
        assert bare_result.stdout == diff_file_result.stdout
        assert record_result.stdout == combined_result.stdout

    def test_diff_file_writes_artifact_matching_full_stdout(self, tmp_path):
        """The teeth: an unconditional assertion that stderr carries a
        DIFF_FILE: line -- never a conditional lookup that treats absence as
        nothing to check -- that the path it names equals the path this test
        computes independently, that the file exists, and that its bytes
        equal the full stdout exactly, trailing newline included.
        Also covers item 12: the file is complete and readable once the
        process exits, which is all subprocess.run(capture_output=True)
        can observe -- no stream-interleaving order is asserted."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/diff-file-teeth")
        subprocess.run(["git", "checkout", "-q", "feat/diff-file-teeth"], cwd=local, check=True)
        env = _env_with_gh_shim(tmp_path, "main")

        result = _run_script(local, env, diff_file=True)
        assert result.returncode == 0, result.stderr

        announced_path = _diff_file_announced_path(result.stderr)
        expected_path = _diff_artifact_path(env, local, self.SID)
        assert announced_path == str(expected_path)
        assert expected_path.exists()
        assert expected_path.read_bytes() == result.stdout.encode()

    def test_diff_file_writes_one_byte_artifact_for_a_genuinely_empty_diff(self, tmp_path):
        """Row 24's newline divergence from --record is sharpest here: the
        artifact for a genuinely empty diff is exactly b"\\n" (one byte), not
        zero -- the one fixture a --record copy-paste would get wrong, per
        test_record_writes_zero_byte_subject_for_a_genuinely_empty_diff's own
        0-byte pin on the opposite side of this divergence."""
        local, _bare = _make_repo_with_remote(tmp_path)
        env = _env_with_gh_shim(tmp_path, "main")

        result = _run_script(local, env, diff_file=True)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "\n"

        artifact = _diff_artifact_path(env, local, self.SID)
        assert artifact.read_bytes() == b"\n"

    def test_diff_file_rerun_supersedes_prior_artifact_at_same_path(self, tmp_path):
        """Two runs with different diff content leave one file at one path
        holding the second run's bytes, and the directory holds exactly one
        entry."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/diff-file-supersede")
        subprocess.run(["git", "checkout", "-q", "feat/diff-file-supersede"], cwd=local, check=True)
        env = _env_with_gh_shim(tmp_path, "main")

        first = _run_script(local, env, diff_file=True)
        assert first.returncode == 0, first.stderr
        artifact = _diff_artifact_path(env, local, self.SID)
        first_content = artifact.read_bytes()

        (local / "second.txt").write_text("second file\n")
        subprocess.run(["git", "add", "second.txt"], cwd=local, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "second commit"], cwd=local, check=True)

        second = _run_script(local, env, diff_file=True)
        assert second.returncode == 0, second.stderr

        assert artifact.read_bytes() == second.stdout.encode()
        assert artifact.read_bytes() != first_content
        assert list(artifact.parent.iterdir()) == [artifact]

    def test_diff_file_reversed_flag_order_matches_forward_order(self, tmp_path):
        """Reversed order --diff-file --record produces identical stdout,
        artifact, and subject to --record --diff-file -- the two blocks
        resolve independently (a no-hoist design), so flag order must
        not matter."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/diff-file-order")
        subprocess.run(["git", "checkout", "-q", "feat/diff-file-order"], cwd=local, check=True)
        env = _env_with_gh_shim(tmp_path, "main")

        forward = subprocess.run(
            [str(_SCRIPT), "--record", "--diff-file"], cwd=str(local), env=env,
            capture_output=True, text=True, check=False,
        )
        assert forward.returncode == 0, forward.stderr
        forward_diff_artifact = _diff_artifact_path(env, local, self.SID).read_bytes()
        forward_subject = _subject_path(env, local, self.SID).read_bytes()

        reversed_result = subprocess.run(
            [str(_SCRIPT), "--diff-file", "--record"], cwd=str(local), env=env,
            capture_output=True, text=True, check=False,
        )
        assert reversed_result.returncode == 0, reversed_result.stderr
        reversed_diff_artifact = _diff_artifact_path(env, local, self.SID).read_bytes()
        reversed_subject = _subject_path(env, local, self.SID).read_bytes()

        assert forward.stdout == reversed_result.stdout
        assert forward_diff_artifact == reversed_diff_artifact
        assert forward_subject == reversed_subject

    def test_diff_file_only_run_leaves_subject_markers_untouched(self, tmp_path):
        """Flag isolation: a bare --diff-file-only run writes the artifact
        and leaves the subject-markers directory absent or empty, and its
        stderr contains no subject-related text."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/diff-file-isolation")
        subprocess.run(["git", "checkout", "-q", "feat/diff-file-isolation"], cwd=local, check=True)
        env = _env_with_gh_shim(tmp_path, "main")

        result = _run_script(local, env, diff_file=True)
        assert result.returncode == 0, result.stderr
        assert _diff_artifact_path(env, local, self.SID).exists()
        assert "subject" not in result.stderr

        subject_dir = Path(env["CLAUDE_CONFIG_DIR"]) / "cumulative-review-subject-markers"
        assert not subject_dir.exists() or list(subject_dir.iterdir()) == []

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_diff_file_artifact_mode_is_0600(self, tmp_path):
        """The artifact is 0o600 after the mv: a same-filesystem mv gives
        the destination the source's mode, and the source is itself
        mktemp-created -- unlike a direct `>` redirect, which would be
        umask-dependent."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/diff-file-mode")
        subprocess.run(["git", "checkout", "-q", "feat/diff-file-mode"], cwd=local, check=True)
        env = _env_with_gh_shim(tmp_path, "main")

        result = _run_script(local, env, diff_file=True)
        assert result.returncode == 0, result.stderr

        artifact = _diff_artifact_path(env, local, self.SID)
        assert stat.S_IMODE(artifact.stat().st_mode) == 0o600

    def test_diff_file_prints_before_resolution_failure(self, tmp_path):
        """A CONFIG_DIR resolution failure must never cost the caller the
        diff it asked for, the --diff-file mirror of
        test_record_prints_before_any_record_path_failure: a relative
        CLAUDE_CONFIG_DIR makes _lib_config_dir fail deterministically and
        privilege-independently, per its own documented contract."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/diff-file-unresolvable-config-dir")
        subprocess.run(
            ["git", "checkout", "-q", "feat/diff-file-unresolvable-config-dir"], cwd=local, check=True
        )
        env = _env_with_gh_shim(tmp_path, "main")
        env["CLAUDE_CONFIG_DIR"] = "relative-and-unresolvable"

        result = _run_script(local, env, diff_file=True)
        assert result.returncode == 0, result.stderr
        assert "diff --git a/file.txt b/file.txt" in result.stdout
        assert "--diff-file could not resolve the repo root, config directory, or session id" in result.stderr
        assert "DIFF_FILE:" not in result.stderr

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_diff_file_path_failure_leaves_prior_artifact_intact(self, tmp_path):
        """A failed diff-file write must not touch an artifact already on
        disk from a prior successful run -- the --diff-file mirror of
        test_record_path_failure_leaves_prior_subject_intact: read+execute
        only means mktemp can no longer create a new temp file in this
        directory, so the pipeline fails before ever reaching mv."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/diff-file-protect-artifact")
        subprocess.run(["git", "checkout", "-q", "feat/diff-file-protect-artifact"], cwd=local, check=True)
        env = _env_with_gh_shim(tmp_path, "main")

        first = _run_script(local, env, diff_file=True)
        assert first.returncode == 0, first.stderr
        artifact = _diff_artifact_path(env, local, self.SID)
        prior_content = artifact.read_bytes()

        artifact.parent.chmod(0o555)
        try:
            second = _run_script(local, env, diff_file=True)
        finally:
            artifact.parent.chmod(0o755)

        assert second.returncode == 0, (
            "a diff-file-path failure must not change the script's exit code"
        )
        assert "diff --git a/file.txt b/file.txt" in second.stdout
        assert "DIFF_FILE:" not in second.stderr
        assert artifact.read_bytes() == prior_content

    def test_combined_flags_resolution_failure_prints_two_distinct_messages(self, tmp_path):
        """Combined-shape resolution failure: --record --diff-file under the
        same relative-CLAUDE_CONFIG_DIR trigger prints two messages, one
        naming each flag -- pinning the choice rather than leaving it to
        the implementation. A shared hoisted resolution block would
        instead produce only one."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/combined-unresolvable-config-dir")
        subprocess.run(
            ["git", "checkout", "-q", "feat/combined-unresolvable-config-dir"], cwd=local, check=True
        )
        env = _env_with_gh_shim(tmp_path, "main")
        env["CLAUDE_CONFIG_DIR"] = "relative-and-unresolvable"

        result = _run_script(local, env, record=True, diff_file=True)
        assert result.returncode == 0, result.stderr
        assert "--record could not resolve the repo root, config directory, or session id" in result.stderr
        assert "--diff-file could not resolve the repo root, config directory, or session id" in result.stderr

    def test_combined_flags_write_both_artifacts_with_own_newline_conventions(self, tmp_path):
        """--record --diff-file writes both artifacts, each with its own
        newline convention: the subject strips stdout's trailing newline for
        hash-recipe agreement, the diff-file artifact keeps it."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/combined-artifacts")
        subprocess.run(["git", "checkout", "-q", "feat/combined-artifacts"], cwd=local, check=True)
        env = _env_with_gh_shim(tmp_path, "main")

        result = _run_script(local, env, record=True, diff_file=True)
        assert result.returncode == 0, result.stderr

        subject = _subject_path(env, local, self.SID)
        artifact = _diff_artifact_path(env, local, self.SID)
        assert subject.read_text() == result.stdout[:-1]
        assert artifact.read_bytes() == result.stdout.encode()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_combined_flags_record_failure_leaves_no_leaked_temp_when_diff_file_succeeds(self, tmp_path):
        """The trap-collision property: with --record's write forced to
        fail (mktemp can no longer create a file in a read+execute-only
        subject directory) while --diff-file's independent resolution
        against its own, unaffected directory still succeeds, no
        `.`-prefixed temp survives in either artifact directory after exit.
        A shared TMP_FILE name between the two blocks would instead let
        --diff-file's mktemp'd path overwrite the variable --record's own
        (armed-then-abandoned) trap relies on, leaking --record's temp on a
        run where its own mktemp succeeds but its mv fails -- distinct
        variable names make that not possible."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/combined-trap-collision")
        subprocess.run(["git", "checkout", "-q", "feat/combined-trap-collision"], cwd=local, check=True)
        env = _env_with_gh_shim(tmp_path, "main")

        first = _run_script(local, env, record=True)
        assert first.returncode == 0, first.stderr
        subject = _subject_path(env, local, self.SID)
        subject.parent.chmod(0o555)
        try:
            result = _run_script(local, env, record=True, diff_file=True)
        finally:
            subject.parent.chmod(0o755)

        assert result.returncode == 0, result.stderr
        assert "DIFF_FILE:" in result.stderr
        artifact = _diff_artifact_path(env, local, self.SID)
        assert artifact.exists()

        leftover_dotfiles = [
            p for p in list(subject.parent.iterdir()) + list(artifact.parent.iterdir())
            if p.name.startswith(".")
        ]
        assert leftover_dotfiles == []

    def test_combined_flags_record_mv_failure_leaves_no_leaked_temp_when_diff_file_succeeds(self, tmp_path):
        """The trap-collision property, mv-specific: unlike the test above
        (which fails --record's mktemp itself via a read+execute-only
        directory, never reaching mv), this forces --record's mktemp to
        succeed and only its mv to fail, while --diff-file's own mktemp+mv
        (a different directory) still succeeds -- the exact shape the bug
        produced, since a shared TMP_FILE name would let --diff-file's
        trap registration silently overwrite --record's still-armed one
        for this armed-then-never-cleared temp."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/combined-mv-failure")
        subprocess.run(["git", "checkout", "-q", "feat/combined-mv-failure"], cwd=local, check=True)
        env = _env_with_gh_shim_and_failing_mv(tmp_path, "main", "cumulative-review-subject-markers")

        result = _run_script(local, env, record=True, diff_file=True)

        assert result.returncode == 0, result.stderr
        assert "subject not recorded" in result.stderr
        assert "DIFF_FILE:" in result.stderr
        artifact = _diff_artifact_path(env, local, self.SID)
        assert artifact.exists()

        subject_dir = Path(env["CLAUDE_CONFIG_DIR"]) / "cumulative-review-subject-markers"
        leftover_dotfiles = [p for p in subject_dir.iterdir() if p.name.startswith(".")]
        assert leftover_dotfiles == []

    def test_combined_flags_diff_file_mv_failure_leaves_no_leaked_temp_when_record_succeeds(self, tmp_path):
        """The trap-collision property, mirrored: unlike the test above
        (--record's own mv fails), this forces --diff-file's mktemp to
        succeed and only its mv to fail, while --record's own mktemp+mv (a
        different directory) still succeeds -- the same shared-TMP_FILE-name
        bug shape, from the opposite direction."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/combined-diff-file-mv-failure")
        subprocess.run(["git", "checkout", "-q", "feat/combined-diff-file-mv-failure"], cwd=local, check=True)
        env = _env_with_gh_shim_and_failing_mv(tmp_path, "main", "cumulative-review-diff-markers")

        result = _run_script(local, env, record=True, diff_file=True)

        assert result.returncode == 0, result.stderr
        assert "diff file not written" in result.stderr
        assert "DIFF_FILE:" not in result.stderr
        subject = _subject_path(env, local, self.SID)
        assert subject.exists()

        diff_dir = Path(env["CLAUDE_CONFIG_DIR"]) / "cumulative-review-diff-markers"
        leftover_dotfiles = [
            p for p in list(subject.parent.iterdir()) + list(diff_dir.iterdir())
            if p.name.startswith(".")
        ]
        assert leftover_dotfiles == []


class TestDiffFileFlagWithoutASession:
    """Mirrors TestRecordFlagWithoutASession: no test in this class seeds a
    session file, exercising --diff-file's session-id-resolution failure
    branch specifically."""

    def test_diff_file_prints_before_session_resolution_failure(self, tmp_path):
        """No live session file exists, so _lib_resolve_session_id fails.
        --diff-file must still print the diff and skip the artifact write,
        the same posture every other --diff-file failure mode uses."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/diff-file-no-session")
        subprocess.run(["git", "checkout", "-q", "feat/diff-file-no-session"], cwd=local, check=True)
        env = _env_with_gh_shim(tmp_path, "main")

        result = _run_script(local, env, diff_file=True)
        assert result.returncode == 0, result.stderr
        assert "diff --git a/file.txt b/file.txt" in result.stdout
        assert "--diff-file could not resolve the repo root, config directory, or session id" in result.stderr
        assert "DIFF_FILE:" not in result.stderr
        diff_dir = Path(env["CLAUDE_CONFIG_DIR"]) / "cumulative-review-diff-markers"
        assert not diff_dir.exists() or list(diff_dir.iterdir()) == []


def _lib_staged_diff_hash_of_repo(repo_root: str, env: dict) -> str:
    """Shell out to the real _lib_staged_diff_hash, sourced directly from
    _lib.sh, which has no unconditional subcommand dispatch. Passes an empty
    BASE, the HEAD-relative recipe every write site uses outside a trusted
    in-progress state, matching `write code-review`'s own call in
    marker.sh."""
    script = f'. "{_LIB_SH}"\n_lib_staged_diff_hash "$@"\n'
    result = subprocess.run(
        ["bash", "-c", script, "bash", repo_root, ""],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return result.stdout.strip()


class TestStagedMode:
    """--staged's own behavior: DIFF_TEXT comes from `git diff --cached`
    instead of the merge-base diff, and the `gh pr view`/merge-base block is
    skipped entirely. Cases below cover:
    - the success path on a fixture where the cumulative path provably
      cannot resolve
    - artifact supersession at a fixed path
    - the empty-stage early return
    - directory isolation from the cumulative --diff-file artifact, in
      both write orders
    - dirty-index isolation of the non-staged modes
    - the --record mutual exclusion, in both flag orders
    - the `git diff --cached` failure branch
    - byte-equality between the --diff-file artifact and
      _lib_staged_diff_hash's own value"""

    SID = "test-session-staged-mode"

    @pytest.fixture(autouse=True)
    def _seeded_session(self, tmp_path):
        # Same isolated CLAUDE_CONFIG_DIR _isolate_transcript_corpus_lookups
        # pins for this whole test tree, matching TestDiffFileFlag's own fixture.
        _seed_session(tmp_path / "isolated-claude-config", self.SID)

    def test_staged_bare_invocation_stdout_matches_git_diff_cached(self, tmp_path):
        """The --staged analogue of test_bare_invocation_stdout_unchanged_by_
        diff_file_flag's cumulative-mode check: for --staged alone with no
        --diff-file, a non-empty staged diff produces stdout identical to an
        independently-run `git diff --cached`, not just flag-invariant."""
        local, _bare = _make_repo_with_remote(tmp_path)
        env = _env_with_gh_shim(tmp_path, "main")

        (local / "staged.txt").write_text("staged content\n")
        subprocess.run(["git", "add", "staged.txt"], cwd=local, check=True)

        result = _run_script(local, env, staged=True)
        assert result.returncode == 0, result.stderr

        raw_diff = subprocess.run(
            ["git", "diff", "--cached"], cwd=local, capture_output=True, text=True, check=True,
        ).stdout
        assert result.stdout == raw_diff.rstrip("\n") + "\n"

    def test_staged_diff_file_succeeds_where_merge_base_resolution_cannot(self, tmp_path):
        """A fixture with no origin remote at all guarantees the cumulative
        path cannot resolve a base, which the bare run's assertion pins.
        That guarantee makes the --staged run's absence of any
        merge-base-failure text a non-vacuous proof that the gh pr
        view/merge-base block was never reached."""
        local = tmp_path / "no-remote"
        _init_repo(local)
        _commit(local, "init")
        env = _env_with_gh_shim(tmp_path, None)

        (local / "staged.txt").write_text("staged content\n")
        subprocess.run(["git", "add", "staged.txt"], cwd=local, check=True)

        bare_result = _run_script(local, env)
        assert bare_result.returncode == 1
        assert "no default branch resolved from origin" in bare_result.stderr

        result = _run_script(local, env, staged=True, diff_file=True)
        assert result.returncode == 0, result.stderr

        raw_diff = subprocess.run(
            ["git", "diff", "--cached"], cwd=local, capture_output=True, text=True, check=True,
        ).stdout
        assert result.stdout == raw_diff.rstrip("\n") + "\n"

        announced_path = _diff_file_announced_path(result.stderr)
        expected_path = _staged_diff_artifact_path(env, local, self.SID)
        assert announced_path == str(expected_path)
        assert expected_path.exists()
        assert "gh pr view failed; defaulting base to" not in result.stderr
        assert "no default branch resolved" not in result.stderr

    def test_staged_diff_file_artifact_hashes_identically_to_lib_staged_diff_hash(self, tmp_path):
        """The byte-equality property docs/scripts.md's --staged bullet
        claims: hashing the --diff-file artifact's own bytes must equal
        `_lib_staged_diff_hash`'s value for the same staged tree (the
        function every marker write site, including `write code-review`,
        calls), so the artifact a reviewer reads and the subject
        `write code-review` hashes can never silently diverge. This claim
        holds outside any in-progress state only -- an empty BASE is passed
        explicitly, matching every write site's own HEAD-relative recipe
        there."""
        local, _bare = _make_repo_with_remote(tmp_path)
        env = _env_with_gh_shim(tmp_path, "main")

        (local / "staged.txt").write_text("staged content\n")
        subprocess.run(["git", "add", "staged.txt"], cwd=local, check=True)

        result = _run_script(local, env, staged=True, diff_file=True)
        assert result.returncode == 0, result.stderr

        artifact = _staged_diff_artifact_path(env, local, self.SID)
        from_artifact = hashlib.sha256(artifact.read_bytes()).hexdigest()

        from_lib = _lib_staged_diff_hash_of_repo(str(local), env)

        assert from_artifact == from_lib

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_staged_diff_file_artifact_mode_is_0600(self, tmp_path):
        """Mirrors test_diff_file_artifact_mode_is_0600 for the --staged
        artifact: the same mktemp-then-mv path gives it mode 0o600."""
        local, _bare = _make_repo_with_remote(tmp_path)
        env = _env_with_gh_shim(tmp_path, "main")

        (local / "staged.txt").write_text("staged content\n")
        subprocess.run(["git", "add", "staged.txt"], cwd=local, check=True)

        result = _run_script(local, env, staged=True, diff_file=True)
        assert result.returncode == 0, result.stderr

        artifact = _staged_diff_artifact_path(env, local, self.SID)
        assert stat.S_IMODE(artifact.stat().st_mode) == 0o600

    def test_staged_diff_file_rerun_supersedes_prior_artifact_at_same_path(self, tmp_path):
        """Mirrors test_diff_file_rerun_supersedes_prior_artifact_at_same_path
        for the --staged artifact: two runs against different staged
        content leave one file at one path holding the second run's
        bytes."""
        local, _bare = _make_repo_with_remote(tmp_path)
        env = _env_with_gh_shim(tmp_path, "main")

        (local / "first.txt").write_text("first staged content\n")
        subprocess.run(["git", "add", "first.txt"], cwd=local, check=True)

        first = _run_script(local, env, staged=True, diff_file=True)
        assert first.returncode == 0, first.stderr
        artifact = _staged_diff_artifact_path(env, local, self.SID)
        first_content = artifact.read_bytes()

        subprocess.run(["git", "reset", "-q"], cwd=local, check=True)
        (local / "second.txt").write_text("second staged content\n")
        subprocess.run(["git", "add", "second.txt"], cwd=local, check=True)

        second = _run_script(local, env, staged=True, diff_file=True)
        assert second.returncode == 0, second.stderr

        assert artifact.read_bytes() == second.stdout.encode()
        assert artifact.read_bytes() != first_content
        assert list(artifact.parent.iterdir()) == [artifact]

    def test_staged_nothing_staged_on_diverged_branch_reports_diff_empty(self, tmp_path):
        """The diverged-branch fixture is what gives result.stdout == "" its
        teeth: had merge-base resolution run instead of --staged's early
        return, stdout would carry the feature branch's own committed
        diff."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/staged-empty")
        subprocess.run(["git", "checkout", "-q", "feat/staged-empty"], cwd=local, check=True)
        env = _env_with_gh_shim(tmp_path, "main")

        result = _run_script(local, env, staged=True, diff_file=True)
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""
        assert "DIFF_EMPTY: no staged changes" in result.stderr
        diff_dir = Path(env["CLAUDE_CONFIG_DIR"]) / "code-review-diff-markers"
        assert not diff_dir.exists() or list(diff_dir.iterdir()) == []

    def test_staged_and_cumulative_diff_file_artifacts_do_not_clobber_each_other(self, tmp_path):
        """The likelier collision scenario is cumulative-then-staged
        (/ready-for-review then a fix-commit /code-review). A commit-gate
        /code-review pass followed later in the same session by
        /ready-for-review is the reverse and equally plausible, so both
        orders are exercised rather than assuming order-independence."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/dual-artifact")
        subprocess.run(["git", "checkout", "-q", "feat/dual-artifact"], cwd=local, check=True)
        env = _env_with_gh_shim(tmp_path, "main")

        cumulative_artifact = _diff_artifact_path(env, local, self.SID)
        staged_artifact = _staged_diff_artifact_path(env, local, self.SID)

        (local / "staged-one.txt").write_text("staged one\n")
        subprocess.run(["git", "add", "staged-one.txt"], cwd=local, check=True)

        cumulative_first = _run_script(local, env, diff_file=True)
        assert cumulative_first.returncode == 0, cumulative_first.stderr
        staged_first = _run_script(local, env, staged=True, diff_file=True)
        assert staged_first.returncode == 0, staged_first.stderr

        assert cumulative_artifact.exists()
        assert staged_artifact.exists()
        assert cumulative_artifact.read_bytes() != staged_artifact.read_bytes()
        assert list(cumulative_artifact.parent.iterdir()) == [cumulative_artifact]
        assert list(staged_artifact.parent.iterdir()) == [staged_artifact]

        subprocess.run(["git", "reset", "-q"], cwd=local, check=True)
        (local / "staged-two.txt").write_text("staged two\n")
        subprocess.run(["git", "add", "staged-two.txt"], cwd=local, check=True)

        staged_second = _run_script(local, env, staged=True, diff_file=True)
        assert staged_second.returncode == 0, staged_second.stderr
        cumulative_second = _run_script(local, env, diff_file=True)
        assert cumulative_second.returncode == 0, cumulative_second.stderr

        assert cumulative_artifact.exists()
        assert staged_artifact.exists()
        assert cumulative_artifact.read_bytes() != staged_artifact.read_bytes()
        assert list(cumulative_artifact.parent.iterdir()) == [cumulative_artifact]
        assert list(staged_artifact.parent.iterdir()) == [staged_artifact]

    def test_staged_only_content_never_affects_non_staged_invocation(self, tmp_path):
        """The cumulative mode reads merge-base...HEAD regardless of what is
        staged -- a dirty index must not leak into either the bare or
        --diff-file cumulative output."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/dirty-index")
        subprocess.run(["git", "checkout", "-q", "feat/dirty-index"], cwd=local, check=True)
        env = _env_with_gh_shim(tmp_path, "main")

        (local / "staged-only.txt").write_text("staged-only marker text\n")
        subprocess.run(["git", "add", "staged-only.txt"], cwd=local, check=True)

        bare_result = _run_script(local, env)
        assert bare_result.returncode == 0, bare_result.stderr
        assert "+work on feat/dirty-index" in bare_result.stdout
        assert "staged-only marker text" not in bare_result.stdout

        diff_file_result = _run_script(local, env, diff_file=True)
        assert diff_file_result.returncode == 0, diff_file_result.stderr
        assert "+work on feat/dirty-index" in diff_file_result.stdout
        assert "staged-only marker text" not in diff_file_result.stdout

    def test_staged_diff_cached_failure_produces_curated_message(self, tmp_path):
        """A `git diff --cached` failure must produce this script's own
        curated message and exit 1, not a bare set -e abort surfacing
        git's own stderr and exit status."""
        local, _bare = _make_repo_with_remote(tmp_path)
        env = _env_with_gh_shim_and_failing_git_diff(tmp_path, "main", "--cached")

        (local / "staged.txt").write_text("staged content\n")
        subprocess.run(["git", "add", "staged.txt"], cwd=local, check=True)

        result = _run_script(local, env, staged=True)

        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr == "pr-diff-against-base.sh: --staged could not compute the staged diff\n"

    def test_staged_record_combination_rejected_before_any_git_work(self, tmp_path):
        """Mirrors test_unknown_argument_exits_before_any_git_work: run from
        a non-repo tmp_path so the absence of git-originated stderr text
        pins that the exclusion check fires before any git call, in both
        flag orders."""
        forward = _run_script(tmp_path, dict(os.environ), staged=True, record=True)
        assert forward.returncode == 2
        assert forward.stdout == ""
        assert "--staged cannot be combined with --record" in forward.stderr
        assert "git" not in forward.stderr.lower()

        reversed_result = subprocess.run(
            [str(_SCRIPT), "--record", "--staged"], cwd=str(tmp_path), env=dict(os.environ),
            capture_output=True, text=True, check=False,
        )
        assert reversed_result.returncode == 2
        assert reversed_result.stdout == ""
        assert "--staged cannot be combined with --record" in reversed_result.stderr
        assert "git" not in reversed_result.stderr.lower()


class TestStagedModeWithoutASession:
    """Mirrors TestDiffFileFlagWithoutASession: no test in this class seeds
    a session file, exercising --staged --diff-file's session-id-resolution
    failure branch specifically."""

    def test_staged_diff_file_prints_before_session_resolution_failure(self, tmp_path):
        """Stages a file first so DIFF_TEXT is non-empty and the run
        actually reaches the writer block. Staging nothing would hit the
        DIFF_EMPTY: early return instead, before the writer block's session
        resolution ever runs, so the test would pass vacuously for the
        wrong reason."""
        local, _bare = _make_repo_with_remote(tmp_path)
        env = _env_with_gh_shim(tmp_path, "main")

        (local / "staged.txt").write_text("staged content\n")
        subprocess.run(["git", "add", "staged.txt"], cwd=local, check=True)

        result = _run_script(local, env, staged=True, diff_file=True)
        assert result.returncode == 0, result.stderr
        assert "diff --git a/staged.txt b/staged.txt" in result.stdout
        assert "--diff-file could not resolve the repo root, config directory, or session id" in result.stderr
        assert "DIFF_FILE:" not in result.stderr
        diff_dir = Path(env["CLAUDE_CONFIG_DIR"]) / "code-review-diff-markers"
        assert not diff_dir.exists() or list(diff_dir.iterdir()) == []


def test_unknown_argument_exits_before_any_git_work(tmp_path):
    """An exit-code-only assertion would not pin "before any git work":
    empty stdout and a stderr containing no git-originated text pin that the
    parser's exit 2 fires ahead of resolve_default_branch/gh/git merge-base,
    run from a non-repo tmp_path with no _init_repo at all."""
    result = _run_script(tmp_path, dict(os.environ), extra_args=["--bogus"])
    assert result.returncode == 2
    assert result.stdout == ""
    assert "unknown argument" in result.stderr
    assert "git" not in result.stderr.lower()


# ---------------------------------------------------------------------------------------------------------------
# --context-files mode
#
# Every case below goes through _run_context, which asserts the exit-hygiene contract (stdout empty; stderr only
# token lines on exit 0; one line ending with the docs pointer on exit 1), so a new case cannot skip it.
# ---------------------------------------------------------------------------------------------------------------

_LIB_PR_DIFF_SH = _SCRIPT.parent / "_pr-diff-lib.sh"
_SCRIPTS_DOC = _SCRIPT.parents[3] / "docs" / "scripts.md"
_SUFFIX = "1700000000-gh1208"
_HALT_POINTER = f'see docs/scripts.md § "{CONTEXT_FILES_HALT_HEADING}"'

_INDEX_HEADER_KEYS = (
    "lane-index", "mode", "root", "base", "head", "generated", "lane", "diff-file", "base-file", "history-file",
)

_ZERO_SHA = "0" * 40
_HARNESS_OUTPUT_LIMIT_BYTES = 30000

def _context_env(tmp_path: Path, *, base_ref: str | None = "main", extra_path: tuple[Path, ...] = (), **extra_env: str) -> dict:
    """An env for a --context-files run: a gh shim, per-test TMPDIR/HOME/CLAUDE_CONFIG_DIR/XDG_CONFIG_HOME, no git config.

    A fresh TMPDIR lets a test assert the private temp directory is gone; HOME and CLAUDE_CONFIG_DIR keep the opt-in
    credential-file-guard.md lookup off the contributor's real files.
    `git_config_isolation_env` keeps git off the contributor's global config and ignore file, which an inherited
    XDG_CONFIG_HOME would otherwise select despite the isolated HOME.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith(("GIT_", "DIRENV_"))}
    gh_dir = tmp_path / "gh_shim"
    gh_dir.mkdir(exist_ok=True)
    (gh_dir / "gh").write_text(_gh_shim_source(base_ref))
    (gh_dir / "gh").chmod(0o755)
    for name in ("tmp", "home", "isolated-claude-config", "xdg-config"):
        (tmp_path / name).mkdir(exist_ok=True)
    env.update(
        TMPDIR=str(tmp_path / "tmp"),
        HOME=str(tmp_path / "home"),
        CLAUDE_CONFIG_DIR=str(tmp_path / "isolated-claude-config"),
        **git_config_isolation_env(tmp_path / "xdg-config"),
        PATH=os.pathsep.join([*(str(path) for path in extra_path), str(gh_dir), os.environ["PATH"]]),
    )
    env.update(extra_env)
    return env


def _is_token_line(line: str) -> bool:
    return line.startswith(tuple(f"{token_name} " for token_name in CONTEXT_FILES_EXIT_ZERO_TOKEN_NAMES))


def _assert_exit_hygiene(result: subprocess.CompletedProcess, *, lane_agents: tuple[str, ...] | None) -> None:
    """Stdout is empty; exit 0 prints only token lines in a shape the mode allows; exit 1 ends its line with the docs pointer."""
    assert result.stdout == "", result.stdout
    lines = result.stderr.split("\n")
    assert lines[-1] == "", f"stderr must end with a newline: {result.stderr!r}"
    lines = lines[:-1]
    if result.returncode == 0:
        assert lines, "an exit-0 run prints at least one token line"
        assert all(_is_token_line(line) for line in lines), result.stderr
        if len(lines) == 1 and lines[0].startswith("DIFF_EMPTY: "):
            return
        if lines[0].startswith("LISTING: "):
            assert all(line.startswith(("LISTING: ", "CHANGED_PATH: ")) for line in lines)
            assert sum(line.startswith("LISTING: ") for line in lines) == 1
            return
        assert sum(line.startswith("LANE_ROW_COUNT: ") for line in lines) == 1
        assert lines.index(next(line for line in lines if line.startswith("LANE_ROW_COUNT: "))) <= 2
        if lane_agents is not None:
            for agent in lane_agents:
                assert sum(line.startswith(f"LANE_INDEX: {agent} ") for line in lines) == 1
                assert sum(line.startswith(f"LANE_DIFF: {agent} ") for line in lines) == 1
        first_row = next((position for position, line in enumerate(lines) if line.startswith("LANE_ROW: ")), len(lines))
        assert all(
            not line.startswith(("CONTEXT_DEGRADED: ", "BASE_GUESSED: ", "LANE_ROW_COUNT: ", "LANE_INDEX: ", "LANE_DIFF: "))
            for line in lines[first_row:]
        ), "every must-act line precedes the first LANE_ROW: line"
    elif result.returncode == 1:
        assert len(lines) == 1, result.stderr
        assert lines[0].endswith(_HALT_POINTER), result.stderr
        assert not any(_is_token_line(line) for line in lines)
    else:
        assert not any(_is_token_line(line) for line in lines), result.stderr


def _private_dirs(env: dict) -> list[str]:
    return sorted(path.name for path in Path(env["TMPDIR"]).iterdir() if path.name.startswith("pr-diff-context."))


def _dot_entries(repo: Path) -> list[str]:
    directory = repo / "agent-reviews"
    return sorted(path.name for path in directory.iterdir() if path.name.startswith(".")) if directory.is_dir() else []


def _assert_no_leaked_temps(repo: Path, env: dict, private_before: list[str], dots_before: list[str]) -> None:
    """A run, whatever its exit, leaves neither a private temp directory nor a dot-named lane temp it did not find."""
    assert _private_dirs(env) == private_before, "a private temp directory leaked"
    assert _dot_entries(repo) == dots_before, "a lane temp leaked"


def _run_context(
    repo: Path,
    env: dict,
    *source: str,
    suffix: str = _SUFFIX,
    list_only: bool = False,
    listing: str | None = None,
    lanes: tuple[str, ...] = (),
    flags: tuple[str, ...] = (),
    hygiene: bool = True,
    script: Path = _SCRIPT,
) -> subprocess.CompletedProcess:
    """Run one --context-files call; `source` is --staged, --working-tree, --expect-diff <path> or empty."""
    args = [str(script), *source, "--context-files", suffix, *flags]
    if list_only:
        args.append("--list")
    else:
        if listing is not None:
            args += ["--listing", listing]
        args += ["--", *lanes]
    private_before, dots_before = _private_dirs(env), _dot_entries(repo)
    result = subprocess.run(args, cwd=repo, env=env, capture_output=True, text=True, check=False, errors="surrogateescape")
    if hygiene:
        agents = None if list_only else tuple(lane.split("=")[0].split(":")[0] for lane in lanes)
        _assert_exit_hygiene(result, lane_agents=agents)
        _assert_no_leaked_temps(repo, env, private_before, dots_before)
    return result


def _parse_listing(result: subprocess.CompletedProcess) -> tuple[str, int, list[dict]]:
    """The id, the count and the CHANGED_PATH rows of a --list call."""
    assert result.returncode == 0, result.stderr
    lines = result.stderr.split("\n")[:-1]
    header = lines[0].split(" ")
    assert header[0] == "LISTING:" and len(header) == 3, lines[0]
    rows = []
    for line in lines[1:]:
        assert line.startswith("CHANGED_PATH: ")
        number, _, rest = line[len("CHANGED_PATH: "):].partition("\t")
        fields = rest.split("\t")
        rows.append({
            "n": int(number), "status": fields[0], "withheld": fields[1], "path": fields[2],
            "old_path": fields[3] if len(fields) > 3 else None,
        })
    assert int(header[2]) == len(rows)
    return header[1], len(rows), rows


def _list_then_lanes(
    repo: Path, env: dict, *source: str, lanes: tuple[str, ...], suffix: str = _SUFFIX
) -> tuple[str, list[dict], subprocess.CompletedProcess]:
    """The two-call recipe: --list, then the lane call with the printed id."""
    listing_id, _count, rows = _parse_listing(_run_context(repo, env, *source, suffix=suffix, list_only=True))
    result = _run_context(repo, env, *source, suffix=suffix, listing=listing_id, lanes=lanes)
    return listing_id, rows, result


def _token_values(result: subprocess.CompletedProcess, token: str) -> list[str]:
    return [line[len(token) + 2:] for line in result.stderr.split("\n") if line.startswith(f"{token}: ")]


def _lane_files(result: subprocess.CompletedProcess, agent: str) -> dict[str, Path]:
    """The index and diff paths a call printed for `agent`, plus the base and history files the index header names."""
    index_path = Path(next(v for v in _token_values(result, "LANE_INDEX") if v.startswith(f"{agent} "))[len(agent) + 1:])
    diff_path = Path(next(v for v in _token_values(result, "LANE_DIFF") if v.startswith(f"{agent} "))[len(agent) + 1:])
    header, _rows = _parse_index(index_path)
    files = {"index": index_path, "diff": diff_path}
    for key in ("base", "history"):
        value = header[f"{key}-file"]
        if value != "-":
            files[key] = Path(value)
    return files


def _parse_index(index_path: Path) -> tuple[dict[str, str], list[dict[str, str | None]]]:
    """Parse a lane index under a grammar this test defines independently of the script."""
    text = index_path.read_bytes().decode("utf-8", "surrogateescape")
    head, blank, body = text.partition("\n\n")
    assert blank, "an index holds a blank line after its header"
    header: dict[str, str] = {}
    keys = []
    for line in head.split("\n"):
        key, _, value = line.partition(": ")
        header[key] = value
        keys.append(key)
    assert tuple(keys) == _INDEX_HEADER_KEYS
    assert header["lane-index"] == "1"
    rows = []
    for line in body.split("\n")[:-1]:
        fields = line.split("\t")
        assert len(fields) in (len(LANE_INDEX_ROW_FIELDS), len(LANE_INDEX_ROW_FIELDS) + 1), line
        row: dict[str, str | None] = dict(zip(LANE_INDEX_ROW_FIELDS, fields, strict=False))
        row["old_path"] = fields[6] if len(fields) == 7 else None
        rows.append(row)
    return header, rows


def _cquote_index(path: str) -> str:
    """The index rule: quoted only when the path holds a control byte or begins with a double quote."""
    if not path.startswith('"') and not any(ord(char) < 32 or ord(char) == 127 for char in path):
        return path
    escapes = {
        '"': '\\"', "\\": "\\\\", "\a": "\\a", "\b": "\\b", "\t": "\\t", "\n": "\\n", "\v": "\\v", "\f": "\\f", "\r": "\\r",
    }
    out = []
    for char in path:
        if char in escapes:
            out.append(escapes[char])
        elif ord(char) < 32 or ord(char) == 127:
            out.append(f"\\{ord(char):03o}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def _cunquote_index(text: str) -> str:
    """Decode an index-quoted path (the inverse of _cquote_index); an unquoted path comes back unchanged."""
    if not text.startswith('"'):
        return text
    named = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", "\\": "\\", '"': '"'}
    inner, out, position = text[1:-1], [], 0
    while position < len(inner):
        char = inner[position]
        if char != "\\":
            out.append(char)
            position += 1
        elif inner[position + 1] in named:
            out.append(named[inner[position + 1]])
            position += 2
        else:
            out.append(chr(int(inner[position + 1:position + 4], 8)))
            position += 4
    return "".join(out)


def _tree_snapshot(root: Path) -> dict[str, bytes | str]:
    """Every file and symlink under root (the .git directory excluded) with its bytes or link target."""
    snapshot: dict[str, bytes | str] = {}
    for current, directories, files in os.walk(root):
        if ".git" in directories:
            directories.remove(".git")
        for name in files + [d for d in directories if (Path(current) / d).is_symlink()]:
            path = Path(current) / name
            snapshot[str(path.relative_to(root))] = os.readlink(path) if path.is_symlink() else path.read_bytes()
    return snapshot


# --- fixture repositories -------------------------------------------------------------------------------------


def _numbered_lines(tag: str, count: int) -> str:
    return "".join(f"{tag} line {number:04d}\n" for number in range(1, count + 1))


def _init_nested_repo(path: Path, text: str) -> None:
    """A nested repository with one commit, which `git add` records as a gitlink (mode 160000)."""
    path.mkdir(parents=True, exist_ok=True)
    for command in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t.com"], ["config", "user.name", "t"]):
        subprocess.run(["git", *command], cwd=path, check=True, capture_output=True)
    (path / "inner.txt").write_text(text)
    subprocess.run(["git", "add", "inner.txt"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "nested"], cwd=path, check=True, capture_output=True)


# Names: a non-ASCII byte, a leading dash, a space, a colon, and one per git quoting class.
_NAME_NONASCII = "ünï-name.txt"
_NAME_BELL = "bell\a.txt"
_NAME_CTRL = "ctrl\x01.txt"
_NAME_DEL = "del\x7f.txt"
_NAME_QUOTE = 'qu"ote.txt'
_NAME_BACKSLASH = "back\\slash.txt"
_NAME_TAB = "tab\tname.txt"
_NAME_NEWLINE = "new\nline.txt"
_TRICKY_NAMES = (
    _NAME_NONASCII, "-dash-lead.txt", "sp ace.txt", "co:lon.txt", _NAME_BELL, _NAME_CTRL, _NAME_DEL, _NAME_QUOTE,
    _NAME_BACKSLASH, _NAME_TAB, _NAME_NEWLINE,
)
_CREDENTIAL_NAME = ".env"
_LONG_LINE_BYTES = 1_000_000


def _write_realistic_base(repo: Path) -> None:
    """The base commit of the realistic change set (see _apply_realistic_changes)."""
    def put(name: str, data: str | bytes) -> None:
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data if isinstance(data, bytes) else data.encode())

    put("a_mod.txt", _numbered_lines("mod", 12))
    put("a_del.txt", _numbered_lines("del", 5))
    put("b_ren_plain.txt", _numbered_lines("ren-plain", 20))
    put("b_ren_edit.txt", _numbered_lines("ren-edit", 20))
    put("bin.dat", b"bin\x00base\x01\x02\n")
    put("mode.sh", _numbered_lines("mode", 3))
    put("link_target.txt", _numbered_lines("target", 3))
    os.symlink("link_target.txt", repo / "sym_to_file")
    put("file_to_sym.txt", _numbered_lines("file-to-sym", 4))
    os.symlink("link_target.txt", repo / "link_retarget")
    put("fd", _numbered_lines("fd-file", 3))
    put("f2gl", _numbered_lines("file-to-gitlink", 2))
    put("empty_del.txt", b"")
    put("no_eol.txt", "first\nsecond-without-newline")
    put("crlf.txt", "one\r\ntwo\r\nthree\r\n")
    put("formfeed.txt", "page one\n\x0c\npage two\n")
    put("lone_cr.txt", "left\rright\nnext\n")
    put("m_long_line.txt", "x" * _LONG_LINE_BYTES + "\n")
    put("nul_late.txt", _numbered_lines("nul-late", 1700))
    put("notes.patch", "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\ndiff --git a/x b/x\ndiff --git a/z b/z\n")
    put("plain_to_quoted.txt", _numbered_lines("plain-to-quoted", 20))
    put('qu"oted_old.txt', _numbered_lines("quoted-to-plain", 20))
    put("fd_stays/readme.txt", _numbered_lines("readme", 2))
    for index, name in enumerate(_TRICKY_NAMES):
        put(name, _numbered_lines(f"tricky-{index}", 6))
    _init_nested_repo(repo / "gl", "gl one\n")
    _init_nested_repo(repo / "gl2", "gl2 one\n")


def _apply_realistic_changes(repo: Path) -> None:
    """Edit the working tree into the realistic change set: A, M and D; R with an edit and R100; binary, mode-only,
    symlink, gitlink and type changes; empty, no-newline, CRLF, form-feed and lone-CR files; one 1 MB line; a text
    diff with a NUL after byte 8,000; every git quoting class; and a file replaced by a directory holding a
    credential-named file. Nothing is staged."""
    def put(name: str, data: str | bytes, *, append: bool = False) -> None:
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("ab" if append else "wb") as handle:
            handle.write(data if isinstance(data, bytes) else data.encode())

    (repo / "a_mod.txt").write_text(_numbered_lines("mod", 12).replace("mod line 0003", "mod line three, edited"))
    put("a_added.txt", _numbered_lines("added", 3))
    (repo / "a_del.txt").unlink()
    (repo / "b_ren_plain.txt").rename(repo / "b_ren_plain_moved.txt")
    (repo / "b_ren_edit.txt").rename(repo / "b_ren_edit_moved.txt")
    (repo / "b_ren_edit_moved.txt").write_text(_numbered_lines("ren-edit", 20).replace("line 0009", "line nine, edited"))
    put("bin.dat", b"bin\x00changed\x03\x04\n")
    (repo / "mode.sh").chmod(0o755)
    (repo / "sym_to_file").unlink()
    put("sym_to_file", _numbered_lines("was-a-symlink", 2))
    (repo / "file_to_sym.txt").unlink()
    os.symlink("link_target.txt", repo / "file_to_sym.txt")
    (repo / "link_retarget").unlink()
    os.symlink("a_mod.txt", repo / "link_retarget")
    os.symlink("a_mod.txt", repo / "new_symlink")
    (repo / "fd").unlink()
    put(f"fd/{_CREDENTIAL_NAME}", "SECRET=canary-in-the-credential-file\n")
    put("fd/inner.txt", _numbered_lines("fd-inner", 3))
    (repo / "f2gl").unlink()
    _init_nested_repo(repo / "f2gl", "now a gitlink\n")
    put("gl/inner.txt", "gl two\n")
    subprocess.run(["git", "commit", "-q", "-a", "-m", "gl two"], cwd=repo / "gl", check=True, capture_output=True)
    shutil.rmtree(repo / "gl2" / ".git")
    put("gl2/inner.txt", "gl2 is now plain files\n")
    put("gl2/extra.txt", "extra\n")
    put("empty_add.txt", b"")
    (repo / "empty_del.txt").unlink()
    put("no_eol.txt", "first\nsecond-without-newline-edited")
    put("crlf.txt", "one\r\ntwo-edited\r\nthree\r\n")
    put("formfeed.txt", "page one\n\x0c\npage two edited\n")
    put("lone_cr.txt", "left\rright-edited\nnext\n")
    put("m_long_line.txt", "y" * _LONG_LINE_BYTES + "\n")
    put("nul_late.txt", b"tail\x00more\n", append=True)
    put(
        "notes.patch",
        "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+newer\ndiff --git a/x b/x\ndiff --git a/z b/z\ndiff --git a/y b/y\n",
    )
    (repo / "plain_to_quoted.txt").rename(repo / 'qu"oted_new.txt')
    (repo / 'qu"oted_new.txt').write_text(_numbered_lines("plain-to-quoted", 20).replace("line 0004", "line four, edited"))
    (repo / 'qu"oted_old.txt').rename(repo / "quoted_to_plain_new.txt")
    quoted_to_plain_text = _numbered_lines("quoted-to-plain", 20).replace("line 0004", "line four, edited")
    (repo / "quoted_to_plain_new.txt").write_text(quoted_to_plain_text)
    for index, name in enumerate(_TRICKY_NAMES):
        (repo / name).write_text(_numbered_lines(f"tricky-{index}", 6).replace("line 0002", "line two, edited"))


def _commit_all(repo: Path, message: str) -> None:
    _git_out(repo, "add", "-A")
    _git_out(repo, "commit", "-q", "-m", message)


def _build_realistic_repo(root: Path, source: str) -> Path:
    """A repository in `source`'s state: staged (changes in the index), working-tree (staged, plus an unstaged edit
    and untracked paths) or cumulative (committed on a feature branch off origin/main)."""
    local, _bare = _make_repo_with_remote(root)
    _write_realistic_base(local)
    _commit_all(local, "realistic base")
    _git_out(local, "push", "-q", "origin", "main")
    (local / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
    if source == "cumulative":
        _git_out(local, "checkout", "-q", "-b", "feature")
    _apply_realistic_changes(local)
    if source == "cumulative":
        _commit_all(local, "realistic change")
        return local
    _git_out(local, "add", "-A")
    if source == "working-tree":
        with (local / "a_mod.txt").open("a") as handle:
            handle.write("an unstaged addition\n")
        (local / "loose.txt").write_text("untracked and plain\n")
        (local / f"{_CREDENTIAL_NAME}.local").write_text("TOKEN=untracked-canary\n")
        _init_nested_repo(local / "nested_repo", "nested\n")
        (local / "new\nuntracked.txt").write_text("a newline in an untracked name\n")
    return local


@pytest.fixture(scope="module")
def realistic_templates(tmp_path_factory) -> dict[str, Path]:
    """One built repository per source, copied per test (the build costs a few seconds)."""
    return {
        source: _build_realistic_repo(tmp_path_factory.mktemp(f"realistic-{source}"), source)
        for source in ("staged", "working-tree", "cumulative")
    }


@pytest.fixture(scope="module")
def small_template(tmp_path_factory) -> Path:
    """A staged small repository, copied per test where two runs must see identical commit ids."""
    return _small_repo(tmp_path_factory.mktemp("small-template"), "staged")


def _copy_template(template: Path, tmp_path: Path, *, refresh_index: bool = True) -> Path:
    """Copy a template repository for one test.

    The copy's index entries carry stale stat data. The first `git diff HEAD` against such an index prints an all-zero
    new-side blob id for a path whose working-tree content is unchanged from the index, then rewrites the index, so the
    next call prints the real id. `refresh_index` settles that first, as a long-lived repository would be.
    """
    destination = tmp_path / "repo"
    shutil.copytree(template, destination, symlinks=True)
    if refresh_index:
        _git_out(destination, "update-index", "-q", "--refresh")
    return destination


_SOURCE_ARGS = {"staged": ("--staged",), "working-tree": ("--working-tree",), "cumulative": ()}
_SOURCE_MODE_NAMES = {"staged": "staged", "working-tree": "working-tree", "cumulative": "cumulative"}
_ALL_SOURCES = ("staged", "working-tree", "cumulative")


def _small_repo(tmp_path: Path, source: str = "staged", *, credential: bool = False, extra_changes=None) -> Path:
    """A small repository with a base commit pushed to origin and four changed paths (M, A, R100 and, when
    `credential` is set, a credential-named file), in `source`'s state."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    local, _bare = _make_repo_with_remote(tmp_path)
    (local / "one.txt").write_text(_numbered_lines("one", 8))
    (local / "two.txt").write_text(_numbered_lines("two", 8))
    (local / "keep.txt").write_text(_numbered_lines("keep", 4))
    if credential:
        (local / _CREDENTIAL_NAME).write_text("SECRET=base-canary\n")
    _commit_all(local, "small base")
    _git_out(local, "push", "-q", "origin", "main")
    (local / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
    if source == "cumulative":
        _git_out(local, "checkout", "-q", "-b", "feature")
    (local / "one.txt").write_text(_numbered_lines("one", 8).replace("line 0003", "line three, edited"))
    (local / "two.txt").rename(local / "three.txt")
    (local / "added.txt").write_text(_numbered_lines("added", 3))
    if credential:
        (local / _CREDENTIAL_NAME).write_text("SECRET=changed-canary\n")
    if extra_changes is not None:
        extra_changes(local)
    if source == "cumulative":
        _commit_all(local, "small change")
    else:
        _git_out(local, "add", "-A")
    return local


# --- independent oracle ---------------------------------------------------------------------------------------


def _oracle_revision(repo: Path, source: str) -> list[str]:
    if source == "staged":
        return ["--cached"]
    if source == "working-tree":
        return ["HEAD"]
    merge_base = _git_out(repo, "merge-base", "origin/main", "HEAD").stdout.decode().strip()
    return [f"{merge_base}...HEAD"]


def _oracle_rows(repo: Path, source: str) -> list[tuple[str, str, str]]:
    """(status, old path, new path) per changed row, parsed in Python from `git diff --raw -z` plus the untracked list."""
    raw = _git_out(repo, *_DIFF_PIN, "--raw", "-z", "--no-abbrev", *_oracle_revision(repo, source), "--").stdout
    tokens = raw.split(b"\0")[:-1]
    rows: list[tuple[str, str, str]] = []
    position = 0
    while position < len(tokens):
        status = tokens[position].split(b" ")[-1].decode()
        position += 1
        if status[0] in "RC":
            rows.append((status, tokens[position].decode(), tokens[position + 1].decode()))
            position += 2
        else:
            rows.append((status, tokens[position].decode(), tokens[position].decode()))
            position += 1
    if source == "working-tree":
        untracked = _git_out(repo, "ls-files", "--others", "--exclude-standard", "-z").stdout.split(b"\0")[:-1]
        rows += [("?", path.decode(), path.decode()) for path in untracked]
    return rows


def _oracle_block(repo: Path, source: str, row: tuple[str, str, str]) -> bytes:
    """One row's diff block from an independent per-path git diff: literal pathspec, the pinned flags, -U3."""
    status, old, new = row
    paths = [old] if old == new else [old, new]
    return _git_out(
        repo, "--literal-pathspecs", *_DIFF_PIN, "-p", "--full-index", "-U3", *_oracle_revision(repo, source), "--", *paths
    ).stdout


def _ambiguous_rows(rows: list[tuple[str, str, str]]) -> set[int]:
    """Rows whose literal pathspec also matches another row's path (a directory beside its descendants): the per-path
    reference cannot isolate their blocks, so the test checks them by slice instead."""
    ambiguous: set[int] = set()
    for index, (status, old, new) in enumerate(rows):
        if status == "?":
            continue
        for other_index, (other_status, other_old, other_new) in enumerate(rows):
            if other_index == index or other_status == "?":
                continue
            if any(name.startswith(f"{mine}/") for mine in (old, new) for name in (other_old, other_new)):
                ambiguous.add(index)
    return ambiguous


def _withheld_rows(rows: list[tuple[str, str, str]]) -> set[int]:
    def credential_named(path: str) -> bool:
        basename = path.rsplit("/", 1)[-1]
        return basename == _CREDENTIAL_NAME or basename.startswith(f"{_CREDENTIAL_NAME}.")

    return {index for index, (_status, old, new) in enumerate(rows) if credential_named(old) or credential_named(new)}


def _expected_preamble(rows: list[tuple[str, str, str]], lane_rows: list[int], withheld: set[int]) -> bytes:
    lines = []
    for index in lane_rows:
        status, _old, new = rows[index]
        if index in withheld:
            lines.append(f"# omitted withheld: {_cquote_index(new)}\n")
        elif status == "?":
            lines.append(f"# omitted untracked: {_cquote_index(new)}\n")
    return ("".join(lines) + "\n").encode("utf-8", "surrogateescape") if lines else b""


def _assert_lane_diff_matches_oracle(repo: Path, source: str, lane_diff: bytes, lane_rows: list[int]) -> None:
    """Byte-for-byte equality with the concatenated per-path reference blocks, in whole-diff order.

    The ambiguous rows are checked by their position between the reference blocks around them.
    """
    rows = _oracle_rows(repo, source)
    withheld = _withheld_rows(rows)
    ambiguous = _ambiguous_rows(rows)
    preamble = _expected_preamble(rows, lane_rows, withheld)
    assert lane_diff.startswith(preamble)
    position = len(preamble)
    kept = [index for index in lane_rows if index not in withheld and rows[index][0] != "?"]
    blocks = {index: _oracle_block(repo, source, rows[index]) for index in kept if index not in ambiguous}
    for order, index in enumerate(kept):
        if index in ambiguous:
            following = next((blocks[later] for later in kept[order + 1:] if later in blocks), None)
            end = lane_diff.find(following, position) if following else len(lane_diff)
            assert end > position, f"no bytes for the ambiguous row {rows[index]!r}"
            assert lane_diff[position:end].startswith(b"diff --git "), rows[index]
            position = end
        else:
            block = blocks[index]
            assert block.startswith(b"diff --git ") or block == b""
            assert lane_diff[position:position + len(block)] == block, f"block of {rows[index]!r} differs from the reference"
            position += len(block)
    assert position == len(lane_diff)


def _numbers_for(rows: list[dict], *paths: str) -> str:
    """The comma-joined listing numbers of the named paths."""
    by_path = {_cunquote_index(row["path"]): row["n"] for row in rows}
    return ",".join(str(by_path[path]) for path in paths)


def _listed_lane_paths(_source: str) -> tuple[str, ...]:
    """A listed lane that reaches a rename with an edit, a type change, a gitlink, a gitlink-only text path, the 1 MB line,
    a no-eol file and a quoting-class path, so slices sit before and after the long-line block."""
    return (
        "a_mod.txt", "b_ren_edit_moved.txt", "file_to_sym.txt", "gl", "no_eol.txt", "m_long_line.txt", "crlf.txt",
        _NAME_TAB, "-dash-lead.txt",
    )


# --- the realistic fixture, every source ---------------------------------------------------------------------------


@pytest.fixture(scope="module", params=_ALL_SOURCES)
def realistic_run(request, realistic_templates, tmp_path_factory):
    """One two-call run per source over the realistic change set: a whole-diff lane, a listed lane, a diff-only lane and the
    withheld-lane reviewer. Read-only afterwards, so every test below shares it."""
    source = request.param
    root = tmp_path_factory.mktemp(f"run-{source}")
    repo = _copy_template(realistic_templates[source], root)
    env = _context_env(root)
    listing_id, _count, rows = _parse_listing(_run_context(repo, env, *_SOURCE_ARGS[source], list_only=True))
    numbers = _numbers_for(rows, *_listed_lane_paths(source))
    lane_args = ("staff-sdet", f"staff-backend-engineer={numbers}", "comment-discipline-reviewer:diff", "ciso-reviewer")
    result = _run_context(repo, env, *_SOURCE_ARGS[source], listing=listing_id, lanes=lane_args)
    assert result.returncode == 0, result.stderr
    return source, repo, env, listing_id, rows, result


# The classification notes each realistic-fixture path's construction implies; every other path carries none of these six.
# A symlink pre-image is written as base text without a final newline, so it also carries no-eol. git reads the empty file
# deleted and the empty file added as one R100 rename, which is why neither carries empty here.
_REALISTIC_CLASSIFICATION_NOTES = {
    "bin.dat": {"binary"},
    "mode.sh": {"mode"},
    "sym_to_file": {"symlink", "no-eol"},
    "file_to_sym.txt": {"symlink"},
    "link_retarget": {"symlink", "no-eol"},
    "new_symlink": {"symlink"},
    "f2gl": {"gitlink"},
    "gl": {"gitlink"},
    "no_eol.txt": {"no-eol"},
}
_CLASSIFICATION_NOTE_NAMES = {"binary", "gitlink", "symlink", "mode", "empty", "no-eol"}


def _assert_ranges_tile_lane_bodies(files: dict[str, Path], repo: Path) -> None:
    """Every index row's base and history range starts where the previous one ended, and the ranges end at the file's end.

    A base body equals the pre-image `git show` returns at the index header's base.
    A history body opens with a `commit ` line.
    """
    header, index_rows = _parse_index(files["index"])
    base_lines = files["base"].read_bytes().split(b"\n")[:-1]
    history_lines = files["history"].read_bytes().split(b"\n")[:-1]
    base_sha = header["base"]
    cursor = {"base": 1, "history": 1}
    for row in index_rows:
        for key, lines in (("base", base_lines), ("history", history_lines)):
            value = row[key]
            if value == "-":
                continue
            start, count = map(int, value.split("+"))
            assert start == cursor[key], f"{key} range of {row['path']} leaves a gap or overlaps"
            cursor[key] += count
            body = b"\n".join(lines[start - 1:start - 1 + count]) + b"\n"
            if key == "base":
                old = _cunquote_index(row["old_path"] or row["path"])
                pre_image = _git_out(repo, "show", f"{base_sha}:{old}").stdout
                assert body == (pre_image if pre_image.endswith(b"\n") else pre_image + b"\n"), row
            else:
                assert lines[start - 1].startswith(b"commit "), row
    assert cursor["base"] - 1 == len(base_lines)
    assert cursor["history"] - 1 == len(history_lines)


class TestRealisticChangeSet:
    def test_lane_diff_equals_per_path_git_reference(self, realistic_run):
        """Whole-diff lanes equal the reference with only the withheld block missing; a listed lane equals its rows' reference
        blocks; a long-line block follows a sliced block, so a pipeline-shaped slice would exit 141 and fail the exit-0 check."""
        source, repo, _env, _id, rows, result = realistic_run
        oracle_rows = _oracle_rows(repo, source)
        by_path = {_cunquote_index(row["path"]): row["n"] - 1 for row in rows}

        whole = (_lane_files(result, "staff-sdet")["diff"]).read_bytes()
        _assert_lane_diff_matches_oracle(repo, source, whole, list(range(len(oracle_rows))))
        listed_rows = sorted(by_path[path] for path in _listed_lane_paths(source))
        listed = (_lane_files(result, "staff-backend-engineer")["diff"]).read_bytes()
        _assert_lane_diff_matches_oracle(repo, source, listed, listed_rows)
        assert listed.startswith(b"diff --git "), "no preamble for a listed lane of tracked, non-withheld rows"
        assert listed.endswith(b"\n")
        assert (_lane_files(result, "comment-discipline-reviewer")["diff"]).read_bytes() == whole
        assert (_lane_files(result, "ciso-reviewer")["diff"]).read_bytes() == whole

    def test_withheld_rows_leave_no_block_in_a_whole_diff_lane_and_are_named_in_its_preamble(self, realistic_run):
        source, repo, _env, _id, _rows, result = realistic_run
        whole = (_lane_files(result, "staff-sdet")["diff"]).read_bytes()
        rows = _oracle_rows(repo, source)
        withheld = _withheld_rows(rows)
        assert len(withheld) == (2 if source == "working-tree" else 1)
        assert whole.startswith(_expected_preamble(rows, list(range(len(rows))), withheld))
        assert b"canary-in-the-credential-file" not in whole
        assert b"untracked-canary" not in whole

    def test_listing_ordinals_equal_index_row_positions(self, realistic_run):
        source, _repo, _env, _id, rows, result = realistic_run
        header, index_rows = _parse_index(_lane_files(result, "staff-sdet")["index"])

        assert [row["n"] for row in rows] == list(range(1, len(index_rows) + 1))
        for listing_row, index_row in zip(rows, index_rows, strict=True):
            assert index_row["status"] == listing_row["status"]
            assert index_row["path"] == listing_row["path"]
            assert index_row["old_path"] == listing_row["old_path"]
            assert ("withheld" in (index_row["notes"] or "").split(",")) == (listing_row["withheld"] == "withheld")
        assert header["mode"] == _SOURCE_MODE_NAMES[source]
        assert header["lane"] == "whole-diff"

    def test_index_notes_hold_the_classification_each_fixture_rows_construction_implies(self, realistic_run):
        """Only the six classification notes are compared: `no-eol` marks a row whose written base lacks a final newline, and a
        binary or gitlink pre-image is never written."""
        _source, _repo, _env, _id, _rows, result = realistic_run
        _header, index_rows = _parse_index(_lane_files(result, "staff-sdet")["index"])

        actual = {
            _cunquote_index(row["path"]): set((row["notes"] or "").split(",")) & _CLASSIFICATION_NOTE_NAMES for row in index_rows
        }

        assert set(_REALISTIC_CLASSIFICATION_NOTES) <= set(actual)
        assert actual == {path: _REALISTIC_CLASSIFICATION_NOTES.get(path, set()) for path in actual}

    def test_index_header_names_the_lane_files_and_the_repository_root(self, realistic_run):
        source, repo, _env, _id, _rows, result = realistic_run
        files = _lane_files(result, "staff-backend-engineer")
        header, index_rows = _parse_index(files["index"])
        base_sha = _git_out(repo, "rev-parse", "HEAD" if source != "cumulative" else "origin/main").stdout.decode().strip()
        head_sha = _git_out(repo, "rev-parse", "HEAD").stdout.decode().strip()

        assert header["root"] == str(repo)
        assert header["lane"] == "listed"
        assert Path(header["diff-file"]) == files["diff"] and files["diff"].parent == repo / "agent-reviews"
        assert Path(header["base-file"]).name == "staff-backend-engineer-" + _SUFFIX + ".base"
        assert Path(header["history-file"]).name == "staff-backend-engineer-" + _SUFFIX + ".history"
        if source == "cumulative":
            assert header["base"] == base_sha and header["head"] == head_sha
        else:
            assert header["base"] == head_sha and header["head"] == "-"
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", header["generated"])
        lane_marks = {_cunquote_index(row["path"]) for row in index_rows if row["lane"] == "lane"}
        assert lane_marks == set(_listed_lane_paths(source))
        assert all(row["lane"] in ("lane", "-") for row in index_rows)

    def test_base_and_history_ranges_tile_each_body_and_land_on_their_rows(self, realistic_run):
        _source, repo, _env, _id, _rows, result = realistic_run
        _assert_ranges_tile_lane_bodies(_lane_files(result, "staff-sdet"), repo)

    def test_lane_row_echo_names_each_listed_row_in_index_order(self, realistic_run):
        source, _repo, _env, _id, rows, result = realistic_run
        listed_paths = _listed_lane_paths(source)
        wanted = sorted((row["n"], _cunquote_index(row["path"])) for row in rows if _cunquote_index(row["path"]) in listed_paths)

        echoed = [line[len("LANE_ROW: "):] for line in result.stderr.split("\n") if line.startswith("LANE_ROW: ")]

        assert echoed == [f"staff-backend-engineer {n} {_cquote_index(path)}" for n, path in wanted]
        assert _token_values(result, "LANE_ROW_COUNT") == [str(len(wanted))]
        assert not any("sdet" in line or "ciso" in line for line in echoed)

    def test_diff_only_lane_writes_an_index_and_a_diff_but_no_base_or_history(self, realistic_run):
        _source, repo, _env, _id, _rows, result = realistic_run
        files = _lane_files(result, "comment-discipline-reviewer")
        header, index_rows = _parse_index(files["index"])

        assert header["lane"] == "diff-only"
        assert header["base-file"] == "-" and header["history-file"] == "-"
        assert set(files) == {"index", "diff"}
        assert all(row["base"] == "-" and row["history"] == "-" for row in index_rows)
        agent_files = sorted(
            path.name for path in (repo / "agent-reviews").iterdir() if path.name.startswith("comment-discipline-reviewer")
        )
        assert agent_files == [f"comment-discipline-reviewer-{_SUFFIX}.diff", f"comment-discipline-reviewer-{_SUFFIX}.index"]

    def test_success_leaves_only_final_files_and_no_private_temp_directory(self, realistic_run):
        _source, repo, env, _id, _rows, _result = realistic_run

        names = sorted(path.name for path in (repo / "agent-reviews").iterdir())

        assert all(not name.startswith(".") for name in names)
        assert len(names) == 14, names  # three whole or listed lanes of four files each, and a diff-only lane of two
        assert list(Path(env["TMPDIR"]).iterdir()) == []


def _fail_nth_call(counter: Path, nth: int, status: int) -> str:
    """Shell for a tool shim that counts its calls in `counter` and exits `status` on call number `nth`."""
    quoted = shlex.quote(str(counter))
    return f'printf x >> {quoted}; if [ "$(wc -c < {quoted})" -eq {nth} ]; then exit {status}; fi'


# --- PATH shims -------------------------------------------------------------------------------------------------------

_GIT_SHIM_SOURCE = '''#!/usr/bin/env python3
"""Test-only git shim. Rules come from the JSON file named by PR_DIFF_GIT_SHIM_CONFIG.

Every call is appended to the config's "log" file as one JSON line (the argv). The first rule whose "subcommand", every
"match" fragment (a substring of some argument) and no "absent" fragment fit the call acts:
  fail    exit "status", print nothing.
  print   write "stdout" / "stderr" (latin-1 text) and exit "status", as a capped call killed mid-write would.
  edit    run the real git, then rewrite its stdout: "append" (bytes), "drop_block" (n), "swap_blocks" (i, j) or
          "append_block" (the "path" of a header-bearing block for a path in no row).
  warn    run the real git and add "stderr" text, keeping its status.
  after   run the real git and, when it succeeded, run "command" (argv[0] "git" means the real git) in the same cwd.
  sleep   sleep "seconds", then run the real git (a capped call outlasting a scaled cap).
  block   create "started_file", wait up to 30 s for "release_file" to exist, then run the real git.
Calls no rule fits pass straight through.
"""
import json
import os
import re
import subprocess
import sys

config = json.load(open(os.environ["PR_DIFF_GIT_SHIM_CONFIG"]))
real_git = config["real_git"]
args = sys.argv[1:]
position = 0
while position < len(args):
    if args[position] in ("-c", "-C"):
        position += 2
    elif args[position].startswith("-"):
        position += 1
    else:
        break
subcommand = args[position] if position < len(args) else ""
if config.get("log"):
    with open(config["log"], "a") as log:
        log.write(json.dumps({"argv": args, "subcommand": subcommand}) + "\\n")


def matches(rule):
    if rule.get("subcommand") not in (None, subcommand):
        return False
    if any(any(fragment in arg for arg in args) for fragment in rule.get("absent", [])):
        return False
    return all(any(fragment in arg for arg in args) for fragment in rule.get("match", []))


def run_real():
    return subprocess.run([real_git, *args], capture_output=True)


def split_blocks(data):
    return [piece for piece in re.split(rb"(?m)^(?=diff --git )", data) if piece]


for rule in config["rules"]:
    if not matches(rule):
        continue
    mode = rule["mode"]
    if mode == "fail":
        sys.exit(rule["status"])
    if mode == "print":
        sys.stdout.buffer.write(rule.get("stdout", "").encode("latin-1"))
        sys.stderr.buffer.write(rule.get("stderr", "").encode("latin-1"))
        sys.exit(rule["status"])
    if mode == "sleep":
        import time
        time.sleep(rule["seconds"])
        os.execv(real_git, [real_git, *args])
    if mode == "block":
        import time
        open(rule["started_file"], "w").close()
        for _ in range(300):
            if os.path.exists(rule["release_file"]):
                break
            time.sleep(0.1)
        os.execv(real_git, [real_git, *args])
    if mode == "warn":
        real = run_real()
        sys.stdout.buffer.write(real.stdout)
        sys.stderr.buffer.write(real.stderr + rule["stderr"].encode())
        sys.exit(real.returncode)
    if mode == "after":
        real = run_real()
        sys.stdout.buffer.write(real.stdout)
        sys.stderr.buffer.write(real.stderr)
        if real.returncode == 0:
            command = [real_git if token == "git" else token for token in rule["command"]]
            subprocess.run(command, cwd=os.getcwd(), check=True, capture_output=True)
        sys.exit(real.returncode)
    if mode == "edit":
        real = run_real()
        data = real.stdout
        edit = rule["edit"]
        if edit == "append":
            data += rule["bytes"].encode("latin-1")
        elif edit == "append_block":
            path = rule["path"]
            template = (
                "diff --git a/%s b/%s\\nnew file mode 100644\\nindex 0000000..1111111\\n"
                "--- /dev/null\\n+++ b/%s\\n@@ -0,0 +1 @@\\n+%s\\n"
            )
            data += (template % (path, path, path, rule["content"])).encode()
        elif edit == "drop_block":
            blocks = split_blocks(data)
            del blocks[rule["n"]]
            data = b"".join(blocks)
        elif edit == "swap_blocks":
            blocks = split_blocks(data)
            first, second = rule["i"], rule["j"]
            blocks[first], blocks[second] = blocks[second], blocks[first]
            data = b"".join(blocks)
        sys.stdout.buffer.write(data)
        sys.stderr.buffer.write(real.stderr)
        sys.exit(real.returncode)
    raise SystemExit("unknown shim mode " + mode)
os.execv(real_git, [real_git, *args])
'''


class GitShim:
    """A PATH `git` shim with a rule list a test extends before the run, and an argv log it reads back after."""

    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        real_git = shutil.which("git")
        assert real_git is not None
        self.config_path = directory / "config.json"
        self.log_path = directory / "argv.log"
        self.log_path.write_text("")
        self.config: dict = {"real_git": real_git, "log": str(self.log_path), "rules": []}
        shim = directory / "git"
        shim.write_text(_GIT_SHIM_SOURCE)
        shim.chmod(0o755)
        self.write()

    def write(self) -> None:
        self.config_path.write_text(json.dumps(self.config))

    def add_rule(self, **rule) -> GitShim:
        self.config["rules"].append(rule)
        self.write()
        return self

    def env(self, env: dict) -> dict:
        return {**env, "PATH": f"{self.directory}{os.pathsep}{env['PATH']}", "PR_DIFF_GIT_SHIM_CONFIG": str(self.config_path)}

    def calls(self) -> list[dict]:
        return [json.loads(line) for line in self.log_path.read_text().splitlines()]

    def clear_log(self) -> None:
        self.log_path.write_text("")


def _tool_shim(directory: Path, tool: str, *, fragments: tuple[str, ...] = (), status: int | None = None,
               stdout: str = "", log: Path | None = None, extra: str = "") -> Path:
    """A PATH shim for `tool`: when every fragment is a substring of the space-padded argument string, print `stdout` and
    exit `status` (when `status` is set); otherwise run the real tool. `log` records each call and `extra` is shell run first."""
    directory.mkdir(parents=True, exist_ok=True)
    real = shutil.which(tool)
    assert real is not None, tool
    checks = " && ".join(f'[[ " $* " == *{shlex.quote(fragment)}* ]]' for fragment in fragments) or "true"
    lines = ["#!/bin/bash"]
    if log is not None:
        lines.append(f'printf "%s\\n" {shlex.quote(tool)} >> {shlex.quote(str(log))}')
    if status is not None:
        lines.append(f"if {checks}; then printf %s {shlex.quote(stdout)}; exit {status}; fi")
    if extra:
        lines.append(extra)
    lines.append(f'exec {shlex.quote(real)} "$@"')
    shim = directory / tool
    shim.write_text("\n".join(lines) + "\n")
    shim.chmod(0o755)
    return shim


# --- usage errors -----------------------------------------------------------------------------------------------------


_LISTING_ID = "a" * 64


class TestContextUsageErrors:
    """Every usage error exits 2 before any git work, so these run from a directory that is not a repository."""

    def _run(self, tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [str(_SCRIPT), *args], cwd=tmp_path, env=_context_env(tmp_path), capture_output=True, text=True, check=False
        )
        assert result.returncode == 2, (result.returncode, result.stderr)
        assert result.stdout == ""
        assert "fatal" not in result.stderr and "LISTING_STALE" not in result.stderr
        assert result.stderr.startswith("pr-diff-against-base.sh: ")
        return result

    @pytest.mark.parametrize(
        ("args", "fragment"),
        [
            # One row per cause the script wires: test_pr_diff_lib.py's validators and lane-grammar cases hold the rest of the
            # suffix, listing id, agent name and ordinal inputs, and the per-flag rows are separate branches of the argument loop.
            (["--context-files"], "--context-files needs a value"),
            (["--context-files", "-x", "--list"], "--context-files needs a value"),
            (["--context-files", _SUFFIX, "--listing"], "--listing needs a value"),
            (["--context-files", _SUFFIX, "--listing", "-x", "--", "a"], "--listing needs a value"),
            (["--context-files", _SUFFIX, "--expect-diff"], "--expect-diff needs a value"),
            (["--context-files", _SUFFIX, "--expect-diff", "-x", "--list"], "--expect-diff needs a value"),
            (["--context-files", "abc", "--list"], "--context-files needs a suffix matching"),
            (["--context-files", _SUFFIX, "--context-files", _SUFFIX, "--list"], "--context-files was given more than once"),
            (["--staged", "--staged", "--context-files", _SUFFIX, "--list"], "--staged was given more than once"),
            (["--context-files", _SUFFIX, "--list", "--list"], "--list was given more than once"),
            (
                ["--working-tree", "--working-tree", "--context-files", _SUFFIX, "--list"],
                "--working-tree was given more than once",
            ),
            (
                ["--expect-diff", "x", "--expect-diff", "y", "--context-files", _SUFFIX, "--list"],
                "--expect-diff was given more than once",
            ),
            (["--staged", "--working-tree", "--context-files", _SUFFIX, "--list"], "alternatives"),
            (["--staged", "--expect-diff", "x", "--context-files", _SUFFIX, "--list"], "alternatives"),
            (["--working-tree", "--expect-diff", "x", "--context-files", _SUFFIX, "--list"], "alternatives"),
            (["--working-tree"], "--working-tree requires --context-files"),
            (["--expect-diff", "x"], "--expect-diff requires --context-files"),
            (["--listing", _LISTING_ID], "--listing requires --context-files"),
            (["--list"], "--list requires --context-files"),
            (["--", "staff-sdet"], "-- requires --context-files"),
            (["--context-files", _SUFFIX, "--list", "--", "staff-sdet"], "--list takes no --listing and no lane"),
            (["--context-files", _SUFFIX, "--list", "--listing", _LISTING_ID], "--list takes no --listing and no lane"),
            (["--context-files", _SUFFIX], "needs at least one lane"),
            (["--context-files", _SUFFIX, "--"], "needs at least one lane"),
            (["--context-files", _SUFFIX, "--record", "--", "a"], "cannot combine with --record or --diff-file"),
            (["--context-files", _SUFFIX, "--diff-file", "--", "a"], "cannot combine with --record or --diff-file"),
            (["--context-files", _SUFFIX, "--", "Staff"], "valid agent name"),
            (["--context-files", _SUFFIX, "--listing", "abc", "--", "a"], "64 lower-case hexadecimal digits"),
            (["--context-files", _SUFFIX, "--", "a=1"], "needs --listing"),
            (["--context-files", _SUFFIX, "--listing", _LISTING_ID, "--", "a", "a:diff"], "more than one lane argument"),
        ],
    )
    def test_usage_error_exits_2_naming_the_cause(self, tmp_path, args, fragment):
        assert fragment in self._run(tmp_path, *args).stderr

    def test_unknown_argument_message_names_the_context_flags(self, tmp_path):
        stderr = self._run(tmp_path, "--bogus").stderr

        named = (
            "--staged", "--record", "--diff-file", "--context-files", "--working-tree", "--expect-diff", "--list", "--listing",
        )
        for flag in named:
            assert flag in stderr

    def test_missing_lib_exits_2_with_the_install_hint_while_staged_mode_runs_without_it(self, tmp_path):
        """A pull without a re-run installer leaves a non-folded install without _pr-diff-lib.sh."""
        layout = tmp_path / "layout"
        (layout / "scripts").mkdir(parents=True)
        (layout / "scripts" / "pr-diff-against-base.sh").symlink_to(_SCRIPT)
        (layout / "hooks").symlink_to(HOOKS_DIR)
        repo = _small_repo(tmp_path / "work")
        env = _context_env(tmp_path)

        context = subprocess.run(
            [str(layout / "scripts" / "pr-diff-against-base.sh"), "--staged", "--context-files", _SUFFIX, "--list"],
            cwd=repo, env=env, capture_output=True, text=True, check=False,
        )
        staged_without_context_files = subprocess.run(
            [str(layout / "scripts" / "pr-diff-against-base.sh"), "--staged"],
            cwd=repo, env=env, capture_output=True, text=True, check=False,
        )

        assert context.returncode == 2
        assert context.stdout == ""
        assert context.stderr == (
            "pr-diff-against-base.sh: could not source _pr-diff-lib.sh "
            "(run ./install.sh to pick up script files this update added)\n"
        )
        assert staged_without_context_files.returncode == 0
        assert "diff --git a/one.txt b/one.txt" in staged_without_context_files.stdout

    def test_usage_error_wins_over_a_failing_shared_diff_call(self, tmp_path):
        """An out-of-range number exits 2 even when the later shared `git diff` call is shimmed to fail."""
        repo = _small_repo(tmp_path)
        shim = GitShim(tmp_path / "git_shim").add_rule(subcommand="diff", match=["--full-index"], mode="fail", status=128)
        env = shim.env(_context_env(tmp_path))
        listing_id, count, _rows = _parse_listing(_run_context(repo, env, "--staged", list_only=True))

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=(f"staff-sdet={count + 1}",))

        assert result.returncode == 2
        assert f"outside 1..{count}" in result.stderr
        assert not any("--full-index" in " ".join(call["argv"]) for call in shim.calls())

    def test_a_malformed_number_exits_2_against_a_real_listing(self, tmp_path):
        """A leading zero is the input a numeric comparison would read as octal; test_pr_diff_lib.py's lane grammar holds the
        rest."""
        repo = _small_repo(tmp_path)
        env = _context_env(tmp_path)
        listing_id, _count, _rows = _parse_listing(_run_context(repo, env, "--staged", list_only=True))

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet=01",), hygiene=False)

        assert result.returncode == 2
        assert result.stdout == ""
        assert not (repo / "agent-reviews").exists()

    def test_list_with_a_lane_or_a_listing_exits_2(self, tmp_path):
        repo = _small_repo(tmp_path)
        env = _context_env(tmp_path)
        with_lane = subprocess.run(
            [str(_SCRIPT), "--staged", "--context-files", _SUFFIX, "--list", "--", "staff-sdet"], cwd=repo, env=env,
            capture_output=True, text=True, check=False,
        )
        with_listing = subprocess.run(
            [str(_SCRIPT), "--staged", "--context-files", _SUFFIX, "--list", "--listing", _LISTING_ID], cwd=repo, env=env,
            capture_output=True, text=True, check=False,
        )

        assert with_lane.returncode == with_listing.returncode == 2
        assert not (repo / "agent-reviews").exists()


# --- empty sources, unborn HEAD, stale ids ----------------------------------------------------------------------------------


def _empty_repo(tmp_path: Path, source: str) -> Path:
    """A repository whose `source` holds no change: nothing staged, a clean tree, or a feature branch level with origin/main."""
    cumulative = source in ("cumulative", "expect-diff")
    repo = _small_repo(tmp_path, "cumulative" if cumulative else "staged")
    _git_out(repo, "reset", "-q", "--hard", "origin/main" if cumulative else "HEAD")
    _git_out(repo, "clean", "-q", "-fd")
    return repo


def _conflicted_repo(tmp_path: Path) -> tuple[Path, str]:
    """A repository mid-merge with one conflicted path whose name holds a quote, a newline and DIFF_EMPTY: text."""
    conflict_name = 'c"onflict\nDIFF_EMPTY: forged.txt'
    repo = _small_repo(tmp_path, "cumulative")
    _git_out(repo, "reset", "-q", "--hard", "origin/main")
    (repo / conflict_name).write_text("base\n")
    _commit_all(repo, "conflict base")
    _git_out(repo, "checkout", "-q", "-b", "other")
    (repo / conflict_name).write_text("other\n")
    _commit_all(repo, "other side")
    _git_out(repo, "checkout", "-q", "main")
    (repo / conflict_name).write_text("main side\n")
    _commit_all(repo, "main side")
    assert _git_out(repo, "merge", "-q", "other", check=False).returncode != 0
    return repo, conflict_name


class TestEmptyAndUnbornSources:
    @pytest.mark.parametrize("source", ["staged", "working-tree", "cumulative", "expect-diff"])
    @pytest.mark.parametrize("call", ["list", "lanes"])
    def test_diff_empty_per_source_writes_no_file_and_no_directory(self, tmp_path, source, call):
        repo = _empty_repo(tmp_path, source)
        env = _context_env(tmp_path)
        before = _tree_snapshot(repo)
        handed = tmp_path / "handed.diff"
        handed.write_text("")
        source_args = _SOURCE_ARGS.get(source, ("--expect-diff", str(handed)))

        if call == "list":
            result = _run_context(repo, env, *source_args, list_only=True)
        else:
            result = _run_context(repo, env, *source_args, lanes=("staff-sdet",))

        assert result.returncode == 0, result.stderr
        assert re.fullmatch(r"DIFF_EMPTY: [^\n]+\n", result.stderr)
        assert not (repo / "agent-reviews").exists()
        assert _tree_snapshot(repo) == before
        assert list(Path(env["TMPDIR"]).iterdir()) == []

    @pytest.mark.parametrize("handed_text", ["", "\n"])
    def test_expect_diff_prints_diff_empty_for_a_zero_byte_or_one_newline_handed_file_in_both_calls(self, tmp_path, handed_text):
        repo = _empty_repo(tmp_path, "expect-diff")
        env = _context_env(tmp_path)
        handed = tmp_path / "handed.diff"
        handed.write_text(handed_text)

        listed = _run_context(repo, env, "--expect-diff", str(handed), list_only=True)
        laned = _run_context(repo, env, "--expect-diff", str(handed), lanes=("staff-sdet",))

        assert listed.stderr.startswith("DIFF_EMPTY: ") and laned.stderr.startswith("DIFF_EMPTY: ")

    def test_an_empty_staged_diff_prints_diff_empty_even_where_agent_reviews_is_not_ignored(self, tmp_path):
        repo = _empty_repo(tmp_path, "staged")
        (repo / ".git" / "info" / "exclude").write_text("")
        env = _context_env(tmp_path)

        result = _run_context(repo, env, "--staged", lanes=("staff-sdet",))

        assert result.returncode == 0 and result.stderr.startswith("DIFF_EMPTY: ")

    def test_a_stale_id_exits_2_before_the_ignore_check_would_exit_3(self, tmp_path):
        repo = _small_repo(tmp_path)
        (repo / ".git" / "info" / "exclude").write_text("")
        env = _context_env(tmp_path)

        result = _run_context(repo, env, "--staged", listing="b" * 64, lanes=("staff-sdet",))

        assert result.returncode == 2
        assert result.stderr.startswith("LISTING_STALE: ")

    @pytest.mark.parametrize("source", ["staged", "working-tree", "cumulative", "expect-diff"])
    def test_unborn_head_exits_1_with_a_no_commit_line_in_both_calls(self, tmp_path, source):
        repo = tmp_path / "unborn"
        repo.mkdir()
        _git_out(repo, "init", "-q", "-b", "main")
        (repo / "new.txt").write_text("x\n")
        _git_out(repo, "add", "new.txt")
        env = _context_env(tmp_path)
        handed = tmp_path / "handed.diff"
        handed.write_text("x\n")
        source_args = _SOURCE_ARGS.get(source, ("--expect-diff", str(handed)))

        listed = _run_context(repo, env, *source_args, list_only=True)
        laned = _run_context(repo, env, *source_args, listing="c" * 64, lanes=("staff-sdet",))

        for result in (listed, laned):
            assert result.returncode == 1
            assert result.stderr == (
                f"pr-diff-against-base.sh: --context-files failed at rev-parse (exit 1): no commit yet; {_HALT_POINTER}\n"
            )
        assert not (repo / "agent-reviews").exists()
        assert list(Path(env["TMPDIR"]).iterdir()) == []


class TestListingIdentity:
    def test_two_list_runs_on_an_unchanged_tree_print_the_same_id_and_the_sources_differ(self, tmp_path):
        repo = _small_repo(tmp_path)
        (repo / "unstaged.txt").write_text("untracked\n")
        env = _context_env(tmp_path)

        staged_one = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]
        staged_two = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]
        working = _parse_listing(_run_context(repo, env, "--working-tree", list_only=True))[0]

        assert staged_one == staged_two
        assert staged_one != working

    def test_listing_stale_id_exits_2_and_a_relist_recovers(self, tmp_path):
        """A path staged between the two calls changes the id; the lane call writes nothing and exits 2."""
        repo = _small_repo(tmp_path)
        env = _context_env(tmp_path)
        listing_id, _count, _rows = _parse_listing(_run_context(repo, env, "--staged", list_only=True))
        (repo / "late.txt").write_text("late\n")
        _git_out(repo, "add", "late.txt")

        stale = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))
        wrote_for_the_stale_call = (repo / "agent-reviews").exists()
        new_id, new_count, _rows = _parse_listing(_run_context(repo, env, "--staged", list_only=True))
        good = _run_context(repo, env, "--staged", listing=new_id, lanes=("staff-sdet",))

        assert stale.returncode == 2 and stale.stderr.startswith("LISTING_STALE: ")
        assert "did not match the id recomputed" in stale.stderr
        assert not wrote_for_the_stale_call
        assert new_id != listing_id and new_count == 4
        assert good.returncode == 0

    def test_a_whole_diff_only_call_with_a_stale_id_exits_2_too(self, tmp_path):
        repo = _small_repo(tmp_path)
        env = _context_env(tmp_path)

        result = _run_context(repo, env, "--staged", listing="d" * 64, lanes=("staff-sdet",))

        assert result.returncode == 2 and result.stderr.startswith("LISTING_STALE: ")

    def test_a_one_character_off_id_is_stale_not_a_usage_error(self, tmp_path):
        repo = _small_repo(tmp_path)
        env = _context_env(tmp_path)
        listing_id, _count, _rows = _parse_listing(_run_context(repo, env, "--staged", list_only=True))
        off_by_one = listing_id[:-1] + ("0" if listing_id[-1] != "0" else "1")

        result = _run_context(repo, env, "--staged", listing=off_by_one, lanes=("staff-sdet",))

        assert result.returncode == 2 and result.stderr.startswith("LISTING_STALE: ")

    def test_a_source_emptied_after_the_listing_prints_diff_empty_before_the_stale_check(self, tmp_path):
        repo = _small_repo(tmp_path)
        env = _context_env(tmp_path)
        listing_id, _count, _rows = _parse_listing(_run_context(repo, env, "--staged", list_only=True))
        _git_out(repo, "reset", "-q")
        _git_out(repo, "checkout", "-q", "--", ".")
        _git_out(repo, "clean", "-q", "-fd")

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 0 and result.stderr.startswith("DIFF_EMPTY: ")

    def test_working_tree_content_drift_under_one_listing_is_not_caught(self, tmp_path):
        """Characterization: the working-tree id covers paths, modes and blob ids, so an edit to an already-changed, unstaged
        file leaves it unchanged. Call 2 is self-consistent, which is accepted: the docs/scripts.md Call 1 bullet states it."""
        repo = _small_repo(tmp_path, "working-tree")
        _git_out(repo, "reset", "-q")
        _git_out(repo, "update-index", "-q", "--refresh")
        env = _context_env(tmp_path)
        listing_id, _count, _rows = _parse_listing(_run_context(repo, env, "--working-tree", list_only=True))
        with (repo / "one.txt").open("a") as handle:
            handle.write("drift after the listing\n")

        result = _run_context(repo, env, "--working-tree", listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 0, result.stderr

    def test_working_tree_listing_id_stable_across_first_diff(self, tmp_path):
        """The first git diff on a stat-dirty index rewrites the index and turns the raw output's all-zero new-side blob id of
        an unchanged-since-staged path into the real one. The working-tree id leaves that field out, so the first id still
        matches the second and a lane call on the first id is not stale."""
        template = _small_repo(tmp_path / "template", "staged")
        repo = _copy_template(template, tmp_path, refresh_index=False)
        env = _context_env(tmp_path)
        index_before = (repo / ".git" / "index").read_bytes()

        first = _parse_listing(_run_context(repo, env, "--working-tree", list_only=True))[0]
        index_after_first = (repo / ".git" / "index").read_bytes()
        second = _parse_listing(_run_context(repo, env, "--working-tree", list_only=True))[0]
        lane_call = _run_context(repo, env, "--working-tree", listing=first, lanes=("staff-sdet",))

        assert index_after_first != index_before, "the premise: git's own stat refresh rewrote the stat-dirty index"
        assert first == second
        assert lane_call.returncode == 0, lane_call.stderr

    def test_list_writes_nothing_under_the_repository(self, tmp_path):
        """Nothing in the working tree, agent-reviews/ or the ignored set changes. The snapshot skips .git, where git's own
        stat refresh may rewrite the index of a stat-dirty checkout."""
        repo = _small_repo(tmp_path, "working-tree")
        (repo / "loose.txt").write_text("untracked\n")
        env = _context_env(tmp_path)
        before_tree = _tree_snapshot(repo)
        before_status = _git_out(repo, "status", "--porcelain", "--ignored").stdout

        result = _run_context(repo, env, "--working-tree", list_only=True)

        assert result.returncode == 0
        assert _tree_snapshot(repo) == before_tree
        assert _git_out(repo, "status", "--porcelain", "--ignored").stdout == before_status
        assert not (repo / "agent-reviews").exists()

    def test_a_leftover_private_directory_from_a_killed_run_breaks_neither_call(self, tmp_path):
        repo = _small_repo(tmp_path)
        env = _context_env(tmp_path)
        leftover = Path(env["TMPDIR"]) / "pr-diff-context.AbC123"
        leftover.mkdir()
        (leftover / "shared.XXXX").write_text("stale withheld bytes\n")

        listing_id, _count, _rows = _parse_listing(_run_context(repo, env, "--staged", list_only=True))
        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 0
        assert (leftover / "shared.XXXX").exists()
        assert [path.name for path in Path(env["TMPDIR"]).iterdir()] == ["pr-diff-context.AbC123"]

    def test_a_trailing_slash_tmpdir_is_trimmed_and_still_cleaned(self, tmp_path):
        repo = _small_repo(tmp_path)
        env = _context_env(tmp_path)
        env["TMPDIR"] = env["TMPDIR"] + "/"

        listing_id, _count, _rows = _parse_listing(_run_context(repo, env, "--staged", list_only=True))
        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 0
        assert list(Path(env["TMPDIR"]).iterdir()) == []

    def test_a_conflicted_index_halts_both_calls_at_status_check_naming_the_status_and_path(self, tmp_path):
        """Only --staged lists a conflicted path as U; against HEAD it is an ordinary M row (git 2.43.0)."""
        repo, conflict_name = _conflicted_repo(tmp_path)
        env = _context_env(tmp_path)

        listed = _run_context(repo, env, "--staged", list_only=True)
        laned = _run_context(repo, env, "--staged", listing="e" * 64, lanes=("staff-sdet",))

        for result in (listed, laned):
            assert result.returncode == 1
            assert result.stderr == (
                "pr-diff-against-base.sh: --context-files failed at status-check (exit 1): unsupported status U for "
                f"{_cquote_index(conflict_name)}; {_HALT_POINTER}\n"
            )
        working = _run_context(repo, env, "--working-tree", list_only=True)
        assert [row["status"] for row in _parse_listing(working)[2]] == ["M"]
        assert _git_out(repo, "diff", "HEAD").stdout.count(b"diff --git ") == 1

    def test_a_hostile_path_name_cannot_forge_a_token_line(self, tmp_path):
        """A changed path holding a newline and DIFF_EMPTY: text comes back C-quoted on one line."""
        hostile = "evil\nDIFF_EMPTY: nothing\nLANE_INDEX: staff-sdet etc-passwd.txt"

        def add_hostile(repo: Path) -> None:
            (repo / hostile).write_text("x\n")

        repo = _small_repo(tmp_path, extra_changes=add_hostile)
        env = _context_env(tmp_path)

        result = _run_context(repo, env, "--staged", list_only=True)

        _id, _count, rows = _parse_listing(result)
        assert _cquote_index(hostile) in [row["path"] for row in rows]
        assert result.stderr.count("\n") == len(rows) + 1
        assert not any(line.startswith(("DIFF_EMPTY", "LANE_INDEX")) for line in result.stderr.split("\n"))

    def test_output_counts_and_token_order_above_harness_limit(self, tmp_path):
        """600 paths of 60 bytes print a listing past 30,000 bytes whose LISTING: count equals the row count and the highest
        CHANGED_PATH: number, and a lane call with that many LANE_ROW: lines puts every must-act line first. U+2028 and
        U+0085 stay raw in a path (C-quoting leaves bytes of 0x80 and above), and the output splits on \\n only."""
        names = [f"dir/{'p' * 53}{number:04d}.txt" for number in range(600)] + ["split here\u0085now.txt"]

        def add_many(repo: Path) -> None:
            (repo / "dir").mkdir()
            for name in names:
                (repo / name).write_text("x\n")

        repo = _small_repo(tmp_path, extra_changes=add_many)
        env = _context_env(tmp_path)
        listed = _run_context(repo, env, "--staged", list_only=True)

        listing_id, count, rows = _parse_listing(listed)

        assert len(listed.stderr.encode()) > _HARNESS_OUTPUT_LIMIT_BYTES
        assert count == len(rows) == max(row["n"] for row in rows) == 3 + len(names)
        assert any(" " in row["path"] and "\u0085" in row["path"] for row in rows)
        assert len(listed.stderr.split("\n")) == count + 2
        every_number = ",".join(str(row["n"]) for row in rows)
        lanes = (f"staff-sdet={every_number}", "staff-backend-engineer")
        laned = _run_context(repo, env, "--staged", listing=listing_id, lanes=lanes)
        lines = laned.stderr.split("\n")[:-1]
        first_row = next(position for position, line in enumerate(lines) if line.startswith("LANE_ROW: "))
        assert lines[:first_row].count(f"LANE_ROW_COUNT: {count}") == 1
        assert sum(line.startswith("LANE_ROW: ") for line in lines) == count
        assert [line for line in lines[:first_row] if line.startswith("LANE_INDEX: ")] != []


# --- halts, atomicity and core failures ---------------------------------------------------------------------------------


def _agent_reviews_listing(repo: Path) -> list[str]:
    """Every entry of agent-reviews/, dotfiles included; empty when the directory is absent."""
    directory = repo / "agent-reviews"
    return sorted(path.name for path in directory.iterdir()) if directory.exists() else []


def _assert_halted_at(result: subprocess.CompletedProcess, step: str, status: int, detail: str | None = None) -> None:
    """Exit 1, one stderr line naming the step and the failing status, ending with the docs pointer, and no token line."""
    assert result.returncode == 1, (result.returncode, result.stderr)
    expected_detail = f": {detail}" if detail else ""
    expected = f"pr-diff-against-base.sh: --context-files failed at {step} (exit {status}){expected_detail}; {_HALT_POINTER}\n"
    assert result.stderr == expected


def _assert_nothing_left(repo: Path, env: dict) -> None:
    assert _agent_reviews_listing(repo) == []
    assert list(Path(env["TMPDIR"]).iterdir()) == []


# Each row: id, shim kind, shim arguments, the source the call runs under, and the step the trap names.
_CORE_FAILURES = [
    ("repo-root", ("git", {"subcommand": "rev-parse", "match": ["--show-toplevel"]}), "staged", "repo-root"),
    ("rev-parse", ("git", {"subcommand": "rev-parse", "match": ["--verify"]}), "staged", "rev-parse"),
    ("merge-base", ("git", {"subcommand": "merge-base"}), "cumulative", "merge-base"),
    ("untracked-list", ("git", {"subcommand": "ls-files", "match": ["--others"]}), "working-tree", "untracked-list"),
    ("core-raw", ("git", {"subcommand": "diff", "match": ["--raw"]}), "staged", "core-raw"),
    ("core-numstat", ("git", {"subcommand": "diff", "match": ["--numstat"]}), "staged", "core-numstat"),
    (
        "expect-diff-recompute",
        ("git", {"subcommand": "diff-tree"}),
        "expect-diff",
        "expect-diff",
    ),
    ("lane-diff", ("git", {"subcommand": "diff", "match": ["--full-index"]}), "staged", "lane-diff"),
    ("header-check", ("tool", {"tool": "grep", "fragments": (" -a ", " -b ")}), "staged", "header-check"),
    ("lane-slice", ("tool", {"tool": "head", "fragments": (" -c ",)}), "staged", "lane-slice"),
    ("ignore-check", ("git", {"subcommand": "check-ignore"}), "staged", "ignore-check"),
    ("temp-dir", ("tool", {"tool": "mktemp", "fragments": (" -d ",)}), "staged", "temp-dir"),
    ("lane-diff-mktemp", ("tool", {"tool": "mktemp", "fragments": ("shared.",)}), "staged", "lane-diff"),
    ("header-check-mktemp", ("tool", {"tool": "mktemp", "fragments": ("headers.",)}), "staged", "header-check"),
    ("lane-slice-mktemp", ("tool", {"tool": "mktemp", "fragments": (".diff.",)}), "staged", "lane-slice"),
    ("compose-lane-temp-mktemp", ("tool", {"tool": "mktemp", "fragments": (".index.",)}), "staged", "lane-slice"),
    ("enrichment-mktemp", ("tool", {"tool": "mktemp", "fragments": ("base.",)}), "staged", "enrichment"),
    ("listing-id", ("tool", {"tool": "sha256sum"}), "staged", "listing-id"),
    ("write", ("tool", {"tool": "mv"}), "staged", "write"),
]


# The --staged rows inject every reserved status, so a pass-through of 2, 3 or a cap's 124 fails; the other rows prove their
# own wiring with one hostile status (128).
_CORE_FAILURE_CASES = [
    (*row, status) for row in _CORE_FAILURES for status in ((2, 3, 124, 128) if row[2] == "staged" else (128,))
]


def _install_failure(tmp_path: Path, env: dict, kind: tuple, status: int) -> dict:
    """Install the failing shim for one _CORE_FAILURES row and return the env that reaches it."""
    shim_kind, arguments = kind
    if shim_kind == "git":
        return GitShim(tmp_path / "git_shim").add_rule(mode="fail", status=status, **arguments).env(env)
    shim_dir = tmp_path / "tool_shim"
    _tool_shim(shim_dir, arguments["tool"], fragments=arguments.get("fragments", ()), status=status)
    return {**env, "PATH": f"{shim_dir}{os.pathsep}{env['PATH']}"}


def _repo_for_failure(tmp_path: Path, source: str) -> tuple[Path, tuple[str, ...]]:
    if source == "expect-diff":
        repo = _small_repo(tmp_path, "cumulative")
        handed = tmp_path / "handed.diff"
        handed.write_bytes(_default_mode_stdout(repo, _context_env(tmp_path)))
        return repo, ("--expect-diff", str(handed))
    return _small_repo(tmp_path, "staged" if source == "working-tree" else source), _SOURCE_ARGS[source]


class TestHaltsAndAtomicity:
    @pytest.mark.parametrize(
        ("case", "kind", "source", "step", "status"),
        _CORE_FAILURE_CASES,
        ids=[f"{row[0]}-{row[4]}" for row in _CORE_FAILURE_CASES],
    )
    def test_a_failing_call_halts_at_its_step_with_its_own_status_and_leaves_nothing(
        self, tmp_path, case, kind, source, step, status
    ):
        """Statuses 2, 3, 124 and 128 are injected on the --staged rows so a pass-through of a reserved status, git's or the
        cap's fails the case; the other rows inject 128.

        `_lib_repo_root` reports every failure as status 1 whatever git returned, so that one step shows (exit 1).
        """
        shown_status = 1 if case == "repo-root" else status
        repo, source_args = _repo_for_failure(tmp_path, source)
        base_env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(repo, base_env, *source_args, list_only=True))[0]
        env = _install_failure(tmp_path, base_env, kind, status)
        shared_with_the_list_call = step in (
            "repo-root", "rev-parse", "merge-base", "untracked-list", "core-raw", "expect-diff", "listing-id", "temp-dir",
        )

        lane_result = _run_context(repo, env, *source_args, listing=listing_id, lanes=("staff-sdet", "staff-backend-engineer=1"))

        _assert_halted_at(lane_result, step, shown_status)
        _assert_nothing_left(repo, env)
        if shared_with_the_list_call:
            list_result = _run_context(repo, env, *source_args, list_only=True)
            _assert_halted_at(list_result, step, shown_status)

    def test_a_failing_gh_falls_to_the_guess_and_the_call_exits_0(self, tmp_path):
        repo = _small_repo(tmp_path, "cumulative")
        env = _context_env(tmp_path, base_ref=None)

        listing_id, _count, _rows = _parse_listing(_run_context(repo, env, list_only=True))
        result = _run_context(repo, env, listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 0, result.stderr
        assert "BASE_GUESSED: main\n" in result.stderr
        assert "defaulting base to" not in result.stderr

    def test_no_resolvable_default_branch_halts_at_base_resolution(self, tmp_path):
        repo = tmp_path / "no-remote"
        _init_repo(repo)
        _commit(repo, "init")
        env = _context_env(tmp_path, base_ref=None)

        for call in ({"list_only": True}, {"listing": "f" * 64, "lanes": ("staff-sdet",)}):
            result = _run_context(repo, env, **call)
            _assert_halted_at(result, "base-resolution", 1, "gh pr view failed and no default branch resolved from origin")

    @pytest.mark.parametrize(
        ("variant", "detail"),
        [
            ("relative", "TMPDIR must be an absolute path"),
            ("inside-the-repository", "TMPDIR must not be inside the repository"),
            ("the-repository-itself", "TMPDIR must not be inside the repository"),
            ("symlink-into-the-repository", "TMPDIR must not be inside the repository"),
        ],
    )
    def test_a_relative_or_in_repository_tmpdir_halts_at_temp_dir_in_both_calls_and_leaves_nothing(
        self, tmp_path, variant, detail
    ):
        """The private directory holds withheld bytes, so it is never made under the repository, however TMPDIR reaches it."""
        repo = _small_repo(tmp_path)
        env = _context_env(tmp_path)
        scratch = repo / ".scratch"
        scratch.mkdir()
        (tmp_path / "link-into-repository").symlink_to(scratch)
        env["TMPDIR"] = {
            "relative": ".scratch",
            "inside-the-repository": str(scratch),
            "the-repository-itself": str(repo),
            "symlink-into-the-repository": str(tmp_path / "link-into-repository"),
        }[variant]
        before_tree = _tree_snapshot(repo)

        for call in (["--list"], ["--listing", "a" * 64, "--", "staff-sdet"]):
            result = subprocess.run(
                [str(_SCRIPT), "--staged", "--context-files", _SUFFIX, *call],
                cwd=repo, env=env, capture_output=True, text=True, check=False,
            )
            _assert_halted_at(result, "temp-dir", 1, detail)

        assert _tree_snapshot(repo) == before_tree
        assert _agent_reviews_listing(repo) == []
        assert not [path for path in repo.rglob("pr-diff-context.*")]

    def test_check_ignore_status_1_exits_3(self, tmp_path):
        repo = _small_repo(tmp_path)
        shim = GitShim(tmp_path / "git_shim").add_rule(subcommand="check-ignore", mode="fail", status=1)
        env = shim.env(_context_env(tmp_path))
        listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 3
        assert result.stderr.startswith(f"CONTEXT_NOT_IGNORED: {repo}/agent-reviews/staff-sdet-{_SUFFIX}.index;")

    @pytest.mark.parametrize("failing_move", [1, 4, 8], ids=["first", "middle", "last"])
    def test_a_failing_final_move_removes_every_file_already_moved(self, tmp_path, failing_move):
        """Two whole-diff lanes move eight files (base, history, diff, index per lane); failing the nth removes the rest."""
        repo = _small_repo(tmp_path)
        env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]
        counter = tmp_path / "mv-count"
        counter.write_text("")
        shim_dir = tmp_path / "mv_shim"
        _tool_shim(
            shim_dir, "mv",
            extra=_fail_nth_call(counter, failing_move, 5),
        )
        env["PATH"] = f"{shim_dir}{os.pathsep}{env['PATH']}"

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet", "staff-backend-engineer"))

        _assert_halted_at(result, "write", 5)
        _assert_nothing_left(repo, env)

    def test_a_lost_mkdir_race_re_validates_and_exits_0_while_a_planted_symlink_or_file_exits_1(self, tmp_path):
        repo = _small_repo(tmp_path)
        env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]
        shim_dir = tmp_path / "mkdir_shim"
        real_mkdir = shutil.which("mkdir")
        shim_dir.mkdir()
        (shim_dir / "mkdir").write_text(
            f'#!/bin/bash\nlast=${{@: -1}}\n'
            f'if [ "${{last##*/}}" = agent-reviews ]; then {real_mkdir} "$@"; exit 1; fi\nexec {real_mkdir} "$@"\n'
        )
        (shim_dir / "mkdir").chmod(0o755)
        race_env = {**env, "PATH": f"{shim_dir}{os.pathsep}{env['PATH']}"}

        raced = _run_context(repo, race_env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert raced.returncode == 0, raced.stderr
        for planted in ("symlink", "file"):
            other = _small_repo(tmp_path / planted)
            elsewhere = tmp_path / f"{planted}-target"
            elsewhere.mkdir()
            plant_command = f'ln -s {elsewhere} "$last"' if planted == "symlink" else ': > "$last"'
            (shim_dir / "mkdir").write_text(
                f'#!/bin/bash\nlast=${{@: -1}}\n'
                f'if [ "${{last##*/}}" = agent-reviews ]; then {plant_command}; exit 1; fi\nexec {real_mkdir} "$@"\n'
            )
            id_for_other = _parse_listing(_run_context(other, env, "--staged", list_only=True))[0]
            result = _run_context(other, race_env, "--staged", listing=id_for_other, lanes=("staff-sdet",))
            assert result.returncode == 1
            assert " failed at output-dir (exit 1): agent-reviews/ is not a real directory: " in result.stderr
            assert list(elsewhere.iterdir()) == []

    @pytest.mark.parametrize(
        ("variant", "detail"),
        [
            ("numstat-missing-a-record", "no numstat entry for added.txt"),
            ("numstat-with-an-extra-record", "the raw output holds 3 rows and the numstat output 4"),
        ],
    )
    def test_a_raw_numstat_disagreement_halts_at_lane_diff_naming_the_disagreement(self, tmp_path, variant, detail):
        """A path in --raw -z and not in --numstat -z, or the reverse, after a race; the docs entry says to re-run once."""
        repo = _small_repo(tmp_path)
        shim = GitShim(tmp_path / "git_shim")
        if variant == "numstat-missing-a-record":
            shim.add_rule(subcommand="diff", match=["--numstat"], mode="print", stdout="1\t1\tone.txt\0", status=0)
        else:
            shim.add_rule(subcommand="diff", match=["--numstat"], mode="edit", edit="append", bytes="1\t1\tghost.txt\0")
        env = shim.env(_context_env(tmp_path))

        listing_id = _parse_listing(_run_context(repo, _context_env(tmp_path), "--staged", list_only=True))[0]

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        _assert_halted_at(result, "lane-diff", 1, detail)
        _assert_nothing_left(repo, env)


class TestSharedDiffHeaderCheck:
    """The shared diff is checked header by header against the index rows before any lane file is written."""

    def _run(self, tmp_path: Path, rule: dict, *, credential: bool = False, lanes=("staff-sdet", "staff-backend-engineer=1")):
        repo = _small_repo(tmp_path, credential=credential)
        base_env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(repo, base_env, "--staged", list_only=True))[0]
        shim = GitShim(tmp_path / "git_shim").add_rule(subcommand="diff", match=["--full-index"], mode="edit", **rule)
        env = shim.env(base_env)
        return repo, env, listing_id, _run_context(repo, env, "--staged", listing=listing_id, lanes=lanes)

    def test_an_appended_header_bearing_block_for_a_path_in_no_row_halts_and_writes_nothing(self, tmp_path):
        repo, env, _id, result = self._run(tmp_path, {"edit": "append_block", "path": "ghost.txt", "content": "ghost canary"})

        _assert_halted_at(result, "header-check", 1, "unexpected header diff --git a/ghost.txt b/ghost.txt")
        _assert_nothing_left(repo, env)

    def test_a_dropped_block_halts_naming_the_missing_header(self, tmp_path):
        repo, env, _id, result = self._run(tmp_path, {"edit": "drop_block", "n": 0})

        _assert_halted_at(result, "header-check", 1, "missing header for added.txt")
        _assert_nothing_left(repo, env)

    def test_an_appended_withheld_row_block_halts_and_no_byte_of_it_reaches_a_lane_file(self, tmp_path):
        repo, env, _id, result = self._run(
            tmp_path, {"edit": "append_block", "path": _CREDENTIAL_NAME, "content": "appended-canary"}, credential=True,
            lanes=("staff-sdet", "ciso-reviewer"),
        )

        assert result.returncode == 1
        assert " failed at header-check (exit 1): expected 4 headers, found 5; " in result.stderr
        _assert_nothing_left(repo, env)

    def test_two_swapped_blocks_pass_and_a_listed_lane_holds_its_own_paths_bytes(self, tmp_path):
        """Headers compare as a multiset, so a swap passes; attribution follows the header, not the position."""
        lanes = ("staff-sdet", "staff-backend-engineer=1")
        repo, env, _id, result = self._run(tmp_path, {"edit": "swap_blocks", "i": 0, "j": 1}, lanes=lanes)

        assert result.returncode == 0, result.stderr
        header_listing = _parse_listing(_run_context(repo, env, "--staged", list_only=True))
        first_path, second_path = header_listing[2][0]["path"], header_listing[2][1]["path"]

        def reference_block(path: str) -> bytes:
            return _git_out(
                repo, "--literal-pathspecs", *_DIFF_PIN, "-p", "--full-index", "-U3", "--cached", "--", path
            ).stdout

        whole = _lane_files(result, "staff-sdet")["diff"].read_bytes()
        assert whole.startswith(reference_block(second_path)), "the shim engaged: the whole-diff lane leads with the second block"
        assert reference_block(first_path) in whole and whole.index(reference_block(first_path)) > 0
        listed = _lane_files(result, "staff-backend-engineer")["diff"].read_bytes()
        assert listed == reference_block(first_path)

    def test_a_path_staged_after_the_core_calls_halts_with_an_unexpected_header_and_writes_nothing(self, tmp_path):
        """The shim stages late.txt once the numstat call returns, so the shared diff holds a block no row accounts for."""
        repo = _small_repo(tmp_path)
        (repo / "late.txt").write_text("late canary\n")
        base_env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(repo, base_env, "--staged", list_only=True))[0]
        shim = GitShim(tmp_path / "git_shim")
        shim.add_rule(subcommand="diff", match=["--numstat"], mode="after", command=["git", "add", "late.txt"])
        env = shim.env(base_env)

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        _assert_halted_at(result, "header-check", 1, "unexpected header diff --git a/late.txt b/late.txt")
        _assert_nothing_left(repo, env)

    def test_the_untracked_only_working_tree_round_passes_with_an_empty_expected_set(self, tmp_path):
        """No tracked row: the expected set is empty and the shared diff has zero bytes, so the lane's diff is the preamble."""
        repo = _small_repo(tmp_path, "staged")
        _git_out(repo, "reset", "-q", "--hard", "HEAD")
        _git_out(repo, "clean", "-q", "-fd")
        (repo / "loose.txt").write_text("untracked only\n")
        env = _context_env(tmp_path)

        listing_id, _count, rows = _parse_listing(_run_context(repo, env, "--working-tree", list_only=True))
        result = _run_context(repo, env, "--working-tree", listing=listing_id, lanes=("staff-sdet",))

        assert [row["status"] for row in rows] == ["?"]
        assert result.returncode == 0, result.stderr
        assert _lane_files(result, "staff-sdet")["diff"].read_bytes() == b"# omitted untracked: loose.txt\n\n"

    def test_a_staged_round_with_every_row_withheld_leaves_a_preamble_only_diff(self, tmp_path):
        repo = _small_repo(tmp_path)
        _git_out(repo, "reset", "-q", "--hard", "HEAD")
        (repo / _CREDENTIAL_NAME).write_text("SECRET=only-row\n")
        _git_out(repo, "add", "-f", _CREDENTIAL_NAME)
        env = _context_env(tmp_path)
        listing_id, _count, rows = _parse_listing(_run_context(repo, env, "--staged", list_only=True))

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet", f"ciso-reviewer={rows[0]['n']}"))

        assert result.returncode == 0, result.stderr
        for agent in ("staff-sdet", "ciso-reviewer"):
            assert _lane_files(result, agent)["diff"].read_bytes() == f"# omitted withheld: {_CREDENTIAL_NAME}\n\n".encode()

    def test_header_like_content_in_a_patch_file_yields_exit_0_and_the_exact_block_set(self, tmp_path):
        def add_patch_file(repo: Path) -> None:
            (repo / "notes.patch").write_text(
                "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\ndiff --git a/x b/x\n diff --git a/z b/z\n"
            )

        repo = _small_repo(tmp_path, extra_changes=add_patch_file)
        env = _context_env(tmp_path)

        listing_id, _count, rows = _parse_listing(_run_context(repo, env, "--staged", list_only=True))
        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 0, result.stderr
        diff = _lane_files(result, "staff-sdet")["diff"].read_bytes()
        assert diff.count(b"\ndiff --git ") + diff.startswith(b"diff --git ") == len(rows)
        assert b"+diff --git a/x b/x" in diff


# --- signals and kills -----------------------------------------------------------------------------------------------------


def _reset_terminating_signals() -> None:
    """Bash cannot trap a signal that was ignored when it started, so a child starts with SIGINT and SIGTERM at their defaults."""
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)


def _spawn_blocked_run(
    repo: Path,
    env: dict,
    listing_id: str,
    tmp_path: Path,
    *,
    suffix: str = _SUFFIX,
    hold: dict | None = None,
    label: str = "block",
) -> tuple[subprocess.Popen, Path, Path]:
    """Start a lane call whose git call matching the `hold` rule (the shared diff by default) blocks until the test releases it.

    The shared diff is the first call to write a temp; a held `cat-file` or `log` call runs after the lane `.diff` temps exist.
    """
    started, release = tmp_path / f"{label}-started", tmp_path / f"{label}-release"
    rule = hold or {"subcommand": "diff", "match": ["--full-index"]}
    shim = GitShim(tmp_path / f"git_{label}").add_rule(mode="block", started_file=str(started), release_file=str(release), **rule)
    blocked_env = shim.env(env)
    process = subprocess.Popen(
        [str(_SCRIPT), "--staged", "--context-files", suffix, "--listing", listing_id, "--", "staff-sdet"],
        cwd=repo, env=blocked_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        preexec_fn=_reset_terminating_signals,
    )
    try:
        deadline = time.monotonic() + 30
        while not started.exists():
            assert time.monotonic() < deadline, "the held git call never started"
            assert process.poll() is None
            time.sleep(0.05)
    except BaseException:
        _release_and_reap(process, release)
        raise
    return process, started, release


def _release_and_reap(process: subprocess.Popen, release: Path) -> None:
    """Let a held git call finish, then kill and reap the script if it is still running."""
    release.write_text("go")
    if process.poll() is None:
        process.kill()
        process.wait()
    for stream in (process.stdout, process.stderr):
        stream.close()


@pytest.fixture
def spawn_blocked_run():
    """`_spawn_blocked_run`, with every run it spawned released, killed if still running and reaped when the test ends."""
    spawned: list[tuple[subprocess.Popen, Path]] = []

    def spawn(*args, **kwargs) -> tuple[subprocess.Popen, Path, Path]:
        process, started, release = _spawn_blocked_run(*args, **kwargs)
        spawned.append((process, release))
        return process, started, release

    yield spawn
    for process, release in spawned:
        _release_and_reap(process, release)


class TestSignalsAndKills:
    @pytest.mark.parametrize(("signal_number", "expected_exit"), [(signal.SIGTERM, 143), (signal.SIGINT, 130)])
    def test_a_trapped_signal_leaves_no_temp_and_exits_with_the_signals_status(
        self, tmp_path, spawn_blocked_run, signal_number, expected_exit
    ):
        repo = _small_repo(tmp_path)
        env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]
        process, _started, release = spawn_blocked_run(repo, env, listing_id, tmp_path)

        process.send_signal(signal_number)
        release.write_text("go")
        stdout, stderr = process.communicate(timeout=60)

        assert process.returncode == expected_exit, (stdout, stderr)
        assert _agent_reviews_listing(repo) == []
        assert list(Path(env["TMPDIR"]).iterdir()) == []

    def test_an_untrapped_sighup_removes_every_lane_file_and_the_private_directory(self, tmp_path):
        """SIGHUP has no trap of its own and the EXIT trap sees status 0, so whether the call completed, not its status, decides
        that files already moved into place go. The second move is held, so one lane file is in place when the signal lands."""
        repo = _small_repo(tmp_path)
        env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]
        counter, started = tmp_path / "mv-count", tmp_path / "mv-started"
        release, finished = tmp_path / "mv-release", tmp_path / "mv-finished"
        counter.write_text("")
        counter_q, started_q, release_q, finished_q = (shlex.quote(str(path)) for path in (counter, started, release, finished))
        real_mv = shutil.which("mv")
        shim_dir = tmp_path / "mv_hold_shim"
        _tool_shim(
            shim_dir, "mv",
            extra=(
                f'printf x >> {counter_q}; '
                f'if [ "$(wc -c < {counter_q})" -eq 2 ]; then : > {started_q}; '
                f'held=0; while [ ! -e {release_q} ] && [ "$held" -lt 300 ]; do sleep 0.1; held=$((held+1)); done; '
                f'{real_mv} "$@"; : > {finished_q}; exit 0; fi'
            ),
        )
        held_env = {**env, "PATH": f"{shim_dir}{os.pathsep}{env['PATH']}"}
        process = subprocess.Popen(
            [str(_SCRIPT), "--staged", "--context-files", _SUFFIX, "--listing", listing_id, "--", "staff-sdet"],
            cwd=repo, env=held_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            preexec_fn=_reset_terminating_signals,
        )
        try:
            deadline = time.monotonic() + 30
            while not started.exists():
                assert time.monotonic() < deadline, "the second move never started"
                time.sleep(0.05)
            in_place = [name for name in _agent_reviews_listing(repo) if not name.startswith(".")]
            assert len(in_place) == 1, in_place

            process.send_signal(signal.SIGHUP)
            process.wait(timeout=60)
            release.write_text("go")
            while not finished.exists():
                assert time.monotonic() < deadline + 30, "the held move never finished"
                time.sleep(0.05)
            process.communicate(timeout=60)

            assert process.returncode in (129, -signal.SIGHUP)
            assert _agent_reviews_listing(repo) == []
            assert list(Path(env["TMPDIR"]).iterdir()) == []
        finally:
            _release_and_reap(process, release)

    def test_a_second_run_finishing_while_the_first_is_held_leaves_the_first_runs_temps_alone(self, tmp_path, spawn_blocked_run):
        """Lane temps are removed by recorded path, never by a glob: a glob would delete a live second call's temps. The first
        run is held at its first enrichment call, after its lane .diff temps exist."""
        repo = _small_repo(tmp_path)
        env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]
        first_suffix, second_suffix = "1700000001-first", "1700000002-second"
        process, _started, release = spawn_blocked_run(
            repo, env, listing_id, tmp_path, suffix=first_suffix, hold={"subcommand": "cat-file", "match": ["-s"]}, label="first"
        )
        held_temps = _dot_entries(repo)
        assert any(f".staff-sdet-{first_suffix}.diff." in name for name in held_temps), held_temps

        second = _run_context(repo, env, "--staged", suffix=second_suffix, listing=listing_id, lanes=("staff-sdet",))

        assert second.returncode == 0, second.stderr
        assert _dot_entries(repo) == held_temps, "the second run left the first run's live temps in place"
        release.write_text("go")
        stdout, stderr = process.communicate(timeout=60)
        first = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
        assert first.returncode == 0, first.stderr
        for result, suffix in ((first, first_suffix), (second, second_suffix)):
            lane = _lane_files(result, "staff-sdet")
            assert all(path.is_file() for path in lane.values())
            assert lane["index"].name == f"staff-sdet-{suffix}.index"
        assert _dot_entries(repo) == []
        kinds = ("base", "diff", "history", "index")
        assert sorted(_agent_reviews_listing(repo)) == sorted(
            f"staff-sdet-{suffix}.{kind}" for suffix in (first_suffix, second_suffix) for kind in kinds
        )

    def test_sigkill_leaves_one_private_directory_outside_the_repository_and_the_next_call_ignores_it(self, tmp_path):
        """No EXIT trap runs after SIGKILL, so the directory (which may hold withheld bytes) stays; nothing detects it.
        The docs/scripts.md Temps bullet states this accepted residual."""
        repo = _small_repo(tmp_path, credential=True)
        env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]
        process, _started, release = _spawn_blocked_run(repo, env, listing_id, tmp_path)

        process.kill()
        process.communicate(timeout=60)
        release.write_text("go")
        leftovers = [path for path in Path(env["TMPDIR"]).iterdir()]
        deadline = time.monotonic() + 30
        while not any(path.stat().st_size > 0 for path in leftovers[0].glob("shared.*")):
            assert time.monotonic() < deadline, "the orphaned shared diff call never finished writing"
            time.sleep(0.05)
        assert [path.name.startswith("pr-diff-context.") for path in leftovers] == [True]
        assert _agent_reviews_listing(repo) == []
        for path in repo.rglob("*"):
            if ".git" not in path.parts and path.is_file():
                assert b"changed-canary" not in path.read_bytes() or path.name == _CREDENTIAL_NAME
        again = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))
        assert again.returncode == 0, again.stderr
        assert [path.name for path in Path(env["TMPDIR"]).iterdir()] == [leftovers[0].name]


# --- the ignore check and the output targets ----------------------------------------------------------------------------------


def _ignore_calls(shim: GitShim) -> list[list[str]]:
    return [call["argv"] for call in shim.calls() if call["subcommand"] == "check-ignore"]


class TestIgnoreCheckAndOutputTargets:
    def _prepare(self, tmp_path: Path, *, exclude: str = "agent-reviews/\n"):
        repo = _small_repo(tmp_path)
        (repo / ".git" / "info" / "exclude").write_text(exclude)
        shim = GitShim(tmp_path / "git_shim")
        env = shim.env(_context_env(tmp_path))
        listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]
        shim.clear_log()
        return repo, env, shim, listing_id

    def test_exactly_one_check_ignore_per_lane_file_kind_however_many_lanes(self, tmp_path):
        repo, env, shim, listing_id = self._prepare(tmp_path)
        lanes = ("staff-sdet", "staff-backend-engineer=1", "ciso-reviewer", "comment-discipline-reviewer:diff")

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=lanes)

        assert result.returncode == 0, result.stderr
        calls = _ignore_calls(shim)
        expected = [f"agent-reviews/staff-sdet-{_SUFFIX}.{kind}" for kind in ("index", "diff", "base", "history")]
        assert [call[-1] for call in calls] == expected
        assert len({tuple(call) for call in calls}) == 4

    def test_a_call_with_only_diff_only_lanes_checks_only_the_two_kinds_it_writes(self, tmp_path):
        repo, env, shim, listing_id = self._prepare(tmp_path)

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("comment-discipline-reviewer:diff",))

        assert result.returncode == 0, result.stderr
        assert [call[-1].rsplit(".", 1)[1] for call in _ignore_calls(shim)] == ["index", "diff"]

    def test_a_tracked_file_under_an_ignored_directory_exits_3(self, tmp_path):
        repo, env, _shim, listing_id = self._prepare(tmp_path)
        (repo / "agent-reviews").mkdir()
        tracked = repo / "agent-reviews" / f"staff-sdet-{_SUFFIX}.index"
        tracked.write_text("tracked\n")
        _git_out(repo, "add", "-f", str(tracked.relative_to(repo)))
        # staging it changes the listing, so list again
        listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 3
        assert result.stderr.startswith(f"CONTEXT_NOT_IGNORED: {repo}/agent-reviews/staff-sdet-{_SUFFIX}.index;")

    def test_a_symlinked_agent_reviews_exits_1_at_ignore_check_and_never_3(self, tmp_path):
        """git check-ignore exits 128 beyond a symbolic link; the link's target is never written."""
        repo, env, _shim, listing_id = self._prepare(tmp_path)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (repo / "agent-reviews").symlink_to(elsewhere)

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        _assert_halted_at(result, "ignore-check", 128)
        assert list(elsewhere.iterdir()) == []

    @pytest.mark.timing
    def test_a_slow_check_ignore_exits_1_at_ignore_check_never_3(self, tmp_path):
        """The 5 s cap, scaled down by the repo's timeout shim, kills a stalled check-ignore."""
        repo, env, shim, listing_id = self._prepare(tmp_path)
        bin_dir = tmp_path / "scaled_timeout"
        bin_dir.mkdir()
        if not write_scaled_timeout_shim(bin_dir):
            pytest.skip("no timeout(1) or gtimeout(1) on PATH")
        shim.add_rule(subcommand="check-ignore", mode="sleep", seconds=scaled_shim_sleep(5))
        env = {**env, "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}"}

        with assert_cap_engaged(bin_dir, production_cap=5, command="git"):
            result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 1
        assert " failed at ignore-check (exit " in result.stderr

    def test_a_consumer_rule_ignoring_only_md_files_exits_3_naming_the_index_path_and_its_remedy(self, tmp_path):
        repo, env, _shim, listing_id = self._prepare(tmp_path, exclude="agent-reviews/*.md\n")

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet", "staff-backend-engineer"))

        assert result.returncode == 3
        assert result.stdout == ""
        assert result.stderr.count("CONTEXT_NOT_IGNORED:") == 1
        assert result.stderr.startswith(f"CONTEXT_NOT_IGNORED: {repo}/agent-reviews/staff-sdet-{_SUFFIX}.index;")
        assert "add agent-reviews/ to .gitignore or .git/info/exclude" in result.stderr
        assert "remove any rule that un-ignores files under it" in result.stderr
        assert not (repo / "agent-reviews").exists()

    def test_a_rule_ignoring_only_index_files_exits_3_naming_the_diff_path(self, tmp_path):
        repo, env, _shim, listing_id = self._prepare(tmp_path, exclude="agent-reviews/*.index\n")

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 3
        assert result.stderr.count("CONTEXT_NOT_IGNORED:") == 1
        assert result.stderr.startswith(f"CONTEXT_NOT_IGNORED: {repo}/agent-reviews/staff-sdet-{_SUFFIX}.diff;")

    def test_a_negation_un_ignoring_only_the_second_lanes_stem_still_exits_0(self, tmp_path):
        """Characterization of the accepted residual: the check proves the folder rule ignores each lane-file kind for the
        first lane's stem, not for every stem. The docs/scripts.md Exit codes bullet states
        the once-per-kind check this follows from."""
        exclude = f"agent-reviews/*\n!agent-reviews/staff-backend-engineer-{_SUFFIX}.*\n"
        repo, env, _shim, listing_id = self._prepare(tmp_path, exclude=exclude)

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet", "staff-backend-engineer"))

        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize("target_kind", ["file-symlink", "directory-symlink"])
    def test_a_final_name_planted_as_a_symlink_exits_1_and_the_link_target_is_untouched(self, tmp_path, target_kind):
        repo, env, _shim, listing_id = self._prepare(tmp_path)
        (repo / "agent-reviews").mkdir()
        outside = tmp_path / "outside"
        if target_kind == "file-symlink":
            outside.write_text("precious\n")
        else:
            outside.mkdir()
        (repo / "agent-reviews" / f"staff-sdet-{_SUFFIX}.index").symlink_to(outside)

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 1
        assert " failed at output-dir (exit 1): an output target is a symlink or not a regular file: " in result.stderr
        assert (outside.read_text() == "precious\n") if target_kind == "file-symlink" else (list(outside.iterdir()) == [])
        assert [path.name for path in (repo / "agent-reviews").iterdir()] == [f"staff-sdet-{_SUFFIX}.index"]

    def test_a_final_name_planted_as_a_directory_exits_1(self, tmp_path):
        repo, env, _shim, listing_id = self._prepare(tmp_path)
        (repo / "agent-reviews" / f"staff-sdet-{_SUFFIX}.diff").mkdir(parents=True)

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 1
        assert " failed at output-dir (exit 1): an output target is a symlink or not a regular file: " in result.stderr

    def test_a_planted_hard_link_is_replaced_and_the_other_link_is_unchanged(self, tmp_path):
        repo, env, _shim, listing_id = self._prepare(tmp_path)
        (repo / "agent-reviews").mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("precious\n")
        os.link(outside, repo / "agent-reviews" / f"staff-sdet-{_SUFFIX}.index")

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 0, result.stderr
        assert outside.read_text() == "precious\n"
        assert (repo / "agent-reviews" / f"staff-sdet-{_SUFFIX}.index").read_text().startswith("lane-index: 1\n")

    def test_a_regular_file_named_agent_reviews_exits_1_at_output_dir(self, tmp_path):
        repo, env, _shim, listing_id = self._prepare(tmp_path, exclude="agent-reviews\n")
        (repo / "agent-reviews").write_text("not a directory\n")

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 1
        assert " failed at output-dir (exit 1): agent-reviews/ is not a real directory: " in result.stderr
        assert (repo / "agent-reviews").read_text() == "not a directory\n"

    def test_an_absent_agent_reviews_is_created_with_mode_0700_and_lane_files_0600(self, tmp_path):
        repo, env, _shim, listing_id = self._prepare(tmp_path)
        assert not (repo / "agent-reviews").exists()

        with ambient_umask(0o022):
            result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 0, result.stderr
        assert stat.S_IMODE((repo / "agent-reviews").stat().st_mode) == 0o700
        assert {stat.S_IMODE(path.stat().st_mode) for path in (repo / "agent-reviews").iterdir()} == {0o600}

    def test_a_run_that_makes_git_rewrite_a_stat_dirty_index_leaves_the_index_mode_unchanged(self, tmp_path):
        """Guards the script's umask changing the mode git gives a rewritten `.git/index`. The 0o022 umask pin makes a
        leaked umask show as a mode change, and the `os.utime` call makes the index stat-dirty so git rewrites it."""
        repo = _small_repo(tmp_path, "working-tree")
        env = _context_env(tmp_path)
        index_path = repo / ".git" / "index"
        with ambient_umask(0o022):
            index_path.chmod(0o644)
            index_mode_before = file_mode(index_path)
            index_inode_before = index_path.stat().st_ino
            os.utime(repo / "keep.txt", ns=(1_000_000_000, 1_000_000_000))

            result = _run_context(repo, env, "--working-tree", list_only=True)

        assert result.returncode == 0, result.stderr
        assert index_path.stat().st_ino != index_inode_before, "git did not rewrite the index in the fixture"
        assert file_mode(index_path) == index_mode_before

    def test_a_same_suffix_rerun_with_a_changed_lane_replaces_the_files_and_the_new_header_names_no_stale_body(self, tmp_path):
        """Characterization of the accepted residual: a lane file the rerun no longer writes stays on disk. The docs/scripts.md
        Files bullet states it."""
        repo, env, _shim, listing_id = self._prepare(tmp_path)
        first = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))
        assert first.returncode == 0, first.stderr
        stale_base = (repo / "agent-reviews" / f"staff-sdet-{_SUFFIX}.base")

        second = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet:diff",))

        assert second.returncode == 0, second.stderr
        header, _rows = _parse_index(repo / "agent-reviews" / f"staff-sdet-{_SUFFIX}.index")
        assert header["lane"] == "diff-only" and header["base-file"] == "-" and header["history-file"] == "-"
        assert stale_base.exists(), "the script never deletes old lane files"

    def test_two_calls_with_distinct_suffixes_succeed_independently(self, tmp_path):
        repo, env, _shim, listing_id = self._prepare(tmp_path)

        first = _run_context(repo, env, "--staged", suffix="1700000001-a", listing=listing_id, lanes=("staff-sdet",))
        second = _run_context(repo, env, "--staged", suffix="1700000002-b", listing=listing_id, lanes=("staff-sdet",))

        assert first.returncode == second.returncode == 0
        assert len(_agent_reviews_listing(repo)) == 8

    def test_a_run_from_a_subdirectory_and_from_a_linked_worktree_reports_the_repository_root(self, tmp_path):
        repo = _small_repo(tmp_path, credential=True)
        (repo / "sub").mkdir()
        env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(repo / "sub", env, "--staged", list_only=True))[0]

        from_subdirectory = _run_context(repo / "sub", env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert from_subdirectory.returncode == 0, from_subdirectory.stderr
        header, rows = _parse_index(_lane_files(from_subdirectory, "staff-sdet")["index"])
        assert header["root"] == str(repo)
        assert (repo / "agent-reviews").is_dir() and not (repo / "sub" / "agent-reviews").exists()
        assert b"changed-canary" not in _lane_files(from_subdirectory, "staff-sdet")["diff"].read_bytes()

        worktree = tmp_path / "linked"
        _git_out(repo, "worktree", "add", "-q", str(worktree), "-b", "linked-branch")
        (worktree / "wt.txt").write_text("worktree change\n")
        (worktree / _CREDENTIAL_NAME).write_text("SECRET=worktree-canary\n")
        _git_out(worktree, "add", "-A")
        (repo / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
        worktree_id = _parse_listing(_run_context(worktree, env, "--staged", list_only=True))[0]
        from_worktree = _run_context(worktree, env, "--staged", listing=worktree_id, lanes=("staff-sdet",))
        assert from_worktree.returncode == 0, from_worktree.stderr
        header, rows = _parse_index(_lane_files(from_worktree, "staff-sdet")["index"])
        assert header["root"] == str(worktree)
        assert b"worktree-canary" not in _lane_files(from_worktree, "staff-sdet")["diff"].read_bytes()
        assert {row["path"] for row in rows} >= {"wt.txt", _CREDENTIAL_NAME}


# --- caps tied to calls -----------------------------------------------------------------------------------------------------

# The cap, in seconds, each git subcommand runs under (docs/scripts.md): the core diff calls and ls-files 60 s, enrichment and
# merge-base 20 s, and the quick calls 5 s.
_CAP_BY_SUBCOMMAND = {
    "diff": 60, "diff-tree": 60, "ls-files": 60, "log": 20, "cat-file": 20, "merge-base": 20,
    "rev-parse": 5, "symbolic-ref": 5, "check-ignore": 5,
}
# The subcommands whose capped command is not `git` itself: the shared cumulative-diff command starts with `env`.
_CAPPED_PROGRAM_BY_SUBCOMMAND = {"diff-tree": "env"}


@pytest.mark.timing
class TestCapsTiedToCalls:
    @pytest.mark.parametrize("source", ["staged", "working-tree", "cumulative", "expect-diff"])
    def test_each_call_runs_under_its_cap(self, tmp_path, source):
        repo, source_args = _repo_for_failure(tmp_path, source if source != "working-tree" else "working-tree")
        if source == "working-tree":
            (repo / "loose.txt").write_text("untracked\n")
        bin_dir = tmp_path / "scaled_timeout"
        bin_dir.mkdir()
        if not write_scaled_timeout_shim(bin_dir):
            pytest.skip("no timeout(1) or gtimeout(1) on PATH")
        shim = GitShim(tmp_path / "git_shim")
        env = shim.env(_context_env(tmp_path, extra_path=(bin_dir,)))
        listing_id = _parse_listing(_run_context(repo, env, *source_args, list_only=True))[0]
        shim.clear_log()
        for line in ("started", "completed"):
            (bin_dir / "scaled-timeout-markers" / line).write_text("")

        result = _run_context(repo, env, *source_args, listing=listing_id, lanes=("staff-sdet", "staff-backend-engineer=1"))

        assert result.returncode == 0, result.stderr
        started = (bin_dir / "scaled-timeout-markers" / "started").read_text().split("\n")[:-1]
        expected_git = sorted(
            f"{_CAP_BY_SUBCOMMAND[call['subcommand']]} {_CAPPED_PROGRAM_BY_SUBCOMMAND.get(call['subcommand'], 'git')}"
            for call in shim.calls()
        )
        assert sorted(entry for entry in started if entry.endswith((" git", " env"))) == expected_git
        expected_gh = ["15 gh"] if source in ("cumulative", "expect-diff") else []
        assert sorted(entry for entry in started if entry.endswith(" gh")) == expected_gh
        assert all(entry.endswith((" git", " env", " gh")) for entry in started)


# --- environment and configuration the script must neutralize -------------------------------------------------------------


def _lane_bytes(result: subprocess.CompletedProcess, agent: str) -> dict[str, bytes]:
    return {kind: path.read_bytes() for kind, path in _lane_files(result, agent).items() if kind in ("diff", "base", "history")}


def _hostile_repo(tmp_path: Path) -> Path:
    """A repository whose changed set holds a gitlink, a non-ASCII path, a rename and a textconv-attributed text file,
    with two prior commits on one path so a history body exists. One changed path, file.txt, has a history that reaches the
    root commit."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    local, _bare = _make_repo_with_remote(tmp_path)
    (local / ".gitattributes").write_text("*.txt diff=upper\n")
    (local / "one.txt").write_text(_numbered_lines("one", 12))
    (local / "two.txt").write_text(_numbered_lines("two", 12))
    (local / "ünï.txt").write_text(_numbered_lines("uni", 6))
    _init_nested_repo(local / "gl", "gl one\n")
    _commit_all(local, "hostile base one")
    (local / "one.txt").write_text(_numbered_lines("one", 12).replace("line 0002", "line two, first edit"))
    _commit_all(local, "hostile base two")
    _git_out(local, "push", "-q", "origin", "main")
    root_commits = _git_out(local, "rev-list", "--max-parents=0", "HEAD").stdout
    file_txt_added_in = _git_out(local, "log", "--diff-filter=A", "--format=%H", "--", "file.txt").stdout
    assert file_txt_added_in == root_commits, "file.txt must be added by the root commit, or no history body ends at the root"
    (local / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
    edited_one = _numbered_lines("one", 12).replace("line 0002", "line two, first edit").replace("line 0008", "line eight")
    (local / "one.txt").write_text(edited_one)
    (local / "two.txt").rename(local / "three.txt")
    (local / "three.txt").write_text(_numbered_lines("two", 12).replace("line 0004", "line four"))
    (local / "ünï.txt").write_text(_numbered_lines("uni", 6).replace("line 0002", "line two"))
    (local / "gl" / "inner.txt").write_text("gl two\n")
    subprocess.run(["git", "commit", "-q", "-a", "-m", "gl two"], cwd=local / "gl", check=True, capture_output=True)
    (local / "added.txt").write_text(_numbered_lines("added", 3))
    (local / "file.txt").write_text("edited root file\n")
    _git_out(local, "add", "-A")
    return local


def _two_calls(repo: Path, env: dict, *, lanes=("staff-sdet", "staff-backend-engineer=2")) -> subprocess.CompletedProcess:
    listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]
    result = _run_context(repo, env, "--staged", listing=listing_id, lanes=lanes)
    assert result.returncode == 0, result.stderr
    return result


def _blocks(diff: bytes) -> list[bytes]:
    return sorted(piece for piece in re.split(rb"(?m)^(?=diff --git )", diff) if piece)


@pytest.fixture(scope="module")
def hostile_template(tmp_path_factory) -> Path:
    return _hostile_repo(tmp_path_factory.mktemp("hostile-template"))


class TestHostileGitConfiguration:
    @pytest.fixture(scope="class")
    def baseline(self, hostile_template, tmp_path_factory) -> dict[str, dict[str, bytes]]:
        root = tmp_path_factory.mktemp("hostile-baseline")
        result = _two_calls(_copy_template(hostile_template, root), _context_env(root))
        return {agent: _lane_bytes(result, agent) for agent in ("staff-sdet", "staff-backend-engineer")}

    @pytest.mark.parametrize(
        "setting",
        [
            ("diff.renames", "false"), ("diff.renames", "copies"), ("diff.noprefix", "true"), ("diff.mnemonicPrefix", "true"),
            ("color.ui", "always"), ("diff.external", "{script}"), ("log.showSignature", "true"), ("format.pretty", "oneline"),
            ("diff.submodule", "log"), ("diff.submodule", "diff"), ("diff.ignoreSubmodules", "all"),
            ("diff.orderFile", "{order}"),
            ("diff.context", "0"), ("core.quotePath", "true"), ("diff.upper.textconv", "{script}"),
            ("log.follow", "true"), ("log.decorate", "full"), ("diff.algorithm", "patience"),
            # The script cd's to the repository root, so a relative diff has nothing to relativize.
            ("diff.relative", "true"),
        ],
        ids=lambda setting: f"{setting[0]}={setting[1]}",
    )
    def test_hostile_git_config_leaves_lanes_unchanged(self, tmp_path, hostile_template, baseline, setting):
        """The same change set under each hostile setting yields the clean-config lanes byte for byte, history bodies
        included (the history call needs five flags beyond the patch call's, and the patch call -U3)."""
        repo = _copy_template(hostile_template, tmp_path)
        script = tmp_path / "hostile-script.sh"
        script.write_text("#!/bin/sh\necho HOSTILE-EXTERNAL-RAN \"$1\"\n")
        script.chmod(0o755)
        order = tmp_path / "order.txt"
        order.write_text("three.txt\nadded.txt\n")
        key, value = setting
        _git_out(repo, "config", key, value.format(script=script, order=order))

        result = _two_calls(repo, _context_env(tmp_path))

        for agent in ("staff-sdet", "staff-backend-engineer"):
            observed = _lane_bytes(result, agent)
            expected = baseline[agent]
            if key == "diff.orderFile":
                # The order file reorders the rows: ordinals name other paths and bodies concatenate in another order, so only
                # the whole-diff lane's set of blocks is comparable.
                if agent == "staff-sdet":
                    assert _blocks(observed["diff"]) == _blocks(expected["diff"])
                continue
            assert observed == expected

    @pytest.mark.parametrize("setting", [("core.abbrev", "12"), ("log.showRoot", "false")])
    def test_settings_that_change_the_history_bodies_are_not_pinned(self, tmp_path, hostile_template, baseline, setting):
        """core.abbrev and log.showRoot change a history body (index-line length; the root commit's patch). The script pins
        neither, so each setting yields a history body unlike the clean-config one, and the index-to-body invariant holds
        under it."""
        repo = _copy_template(hostile_template, tmp_path)
        _git_out(repo, "config", *setting)

        result = _two_calls(repo, _context_env(tmp_path))

        assert _lane_bytes(result, "staff-sdet")["history"] != baseline["staff-sdet"]["history"], (
            f"{setting[0]}={setting[1]} left the history body identical to the clean-config one: the script now pins "
            "that setting (this test's premise is gone)"
        )
        for agent in ("staff-sdet", "staff-backend-engineer"):
            _assert_ranges_tile_lane_bodies(_lane_files(result, agent), repo)

    def test_an_edited_credential_rename_git_cannot_pair_lists_as_a_withheld_delete_and_an_add_under_the_new_name(self, tmp_path):
        """diff.renameLimit=1 stops git pairing the edited rename, so the old name is a delete withheld by its name and the
        new name an add whose block ships, the content a direct Read of the new name returns."""
        tmp_path.mkdir(parents=True, exist_ok=True)
        local, _bare = _make_repo_with_remote(tmp_path)
        _write(local, f"keys/{_RSA_KEY}", "preimage-only-canary\n" + _numbered_lines("key", 20))
        _commit_all(local, "base")
        _git_out(local, "push", "-q", "origin", "main")
        (local / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
        _git_out(local, "config", "diff.renameLimit", "1")
        _git_out(local, "mv", f"keys/{_RSA_KEY}", "keys/moved-key.txt")
        _write(local, "keys/moved-key.txt", "renamed-content line\n" + _numbered_lines("key", 20))
        # Three add destinations against one delete exceed diff.renameLimit=1, so git skips inexact pairing.
        _write(local, "extra_a.txt", "extra a\n")
        _write(local, "extra_b.txt", "extra b\n")
        _git_out(local, "add", "-A")
        env = _context_env(tmp_path)
        listing_id, _count, rows = _parse_listing(_run_context(local, env, "--staged", list_only=True))
        numbers = ",".join(str(row["n"]) for row in rows)

        result = _run_context(
            local, env, "--staged", listing=listing_id, lanes=("staff-sdet", f"staff-backend-engineer={numbers}")
        )

        assert result.returncode == 0, result.stderr
        listed = {row["path"]: (row["status"], row["withheld"]) for row in rows}
        assert listed[f"keys/{_RSA_KEY}"] == ("D", "withheld")
        assert listed["keys/moved-key.txt"] == ("A", "-")
        assert b"preimage-only-canary" not in _every_byte_a_call_left(local, (result,))
        diff = _lane_files(result, "staff-sdet")["diff"].read_bytes()
        assert b"diff --git a/keys/moved-key.txt b/keys/moved-key.txt\nnew file mode" in diff
        assert b"+renamed-content line\n" in diff

    def test_history_body_equals_a_reference_git_log_under_a_clean_config(self, tmp_path, hostile_template):
        repo = _copy_template(hostile_template, tmp_path)
        result = _two_calls(repo, _context_env(tmp_path))
        header, rows = _parse_index(_lane_files(result, "staff-sdet")["index"])
        one_row = next(row for row in rows if row["path"] == "one.txt")
        start, count = map(int, one_row["history"].split("+"))
        history_lines = _lane_files(result, "staff-sdet")["history"].read_bytes().split(b"\n")

        reference = _git_out(
            repo, "-c", "core.quotePath=false", "--literal-pathspecs", "log", "-n", "5", "--no-merges", "--no-color",
            "--no-ext-diff", "--no-textconv", "--follow", "-p", "--src-prefix=a/", "--dst-prefix=b/", "--submodule=short",
            "--ignore-submodules=none",
            "-U3", "--format=commit %H%n%ad %s%n%n%b", "--date=short", header["base"], "--", "one.txt",
        ).stdout

        assert b"\n".join(history_lines[start - 1:start - 1 + count]) + b"\n" == reference
        assert b"Author:" not in reference and reference.count(b"commit ") >= 2

    @pytest.mark.parametrize(
        "variable", ["GIT_GLOB_PATHSPECS", "GIT_ICASE_PATHSPECS", "GIT_NOGLOB_PATHSPECS", "GIT_LITERAL_PATHSPECS"]
    )
    def test_exported_pathspec_variables_leave_history_present_and_the_ignore_check_passing(self, tmp_path, variable):
        """Each variable makes a bare `git check-ignore -q` or `git --literal-pathspecs log` exit 128 on the installed git
        (git 2.43.0), so without the mode's unset the round would halt or lose its history."""
        repo = _small_repo(tmp_path)
        env = _context_env(tmp_path, **{variable: "1"})

        result = _two_calls(repo, env, lanes=("staff-sdet",))

        header, rows = _parse_index(_lane_files(result, "staff-sdet")["index"])
        one_row = next(row for row in rows if row["path"] == "one.txt")
        assert one_row["history"] != "-" and "unavailable" not in (one_row["notes"] or "")
        assert "CONTEXT_DEGRADED" not in result.stderr

    def test_exported_diff_opts_leaves_lanes_unchanged(self, tmp_path, small_template):
        """GIT_DIFF_OPTS=--unified=0 overrides the command-line -U3 of the shared diff and the history call."""
        clean = _copy_template(small_template, tmp_path / "clean")
        hostile = _copy_template(small_template, tmp_path / "hostile")
        clean_result = _two_calls(clean, _context_env(tmp_path / "clean"), lanes=("staff-sdet",))
        hostile_env = _context_env(tmp_path / "hostile", GIT_DIFF_OPTS="--unified=0")
        hostile_result = _two_calls(hostile, hostile_env, lanes=("staff-sdet",))

        assert _lane_bytes(hostile_result, "staff-sdet") == _lane_bytes(clean_result, "staff-sdet")


class TestForeignGitEnvironment:
    @pytest.mark.parametrize(
        "names", [("GIT_DIR",), ("GIT_WORK_TREE",), ("GIT_INDEX_FILE",), ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")]
    )
    def test_foreign_git_dir_leaves_context_mode_describing_the_working_directory_repository(
        self, tmp_path, small_template, names
    ):
        """Deleting any of the three names from the mode's unset makes the lanes describe the other repository."""
        mine = _copy_template(small_template, tmp_path / "mine")
        repo = _copy_template(small_template, tmp_path / "also-mine")
        foreign = _small_repo(tmp_path / "theirs")
        (foreign / "foreign-only.txt").write_text("belongs to the other repository\n")
        _git_out(foreign, "add", "-A")
        exported = {
            "GIT_DIR": str(foreign / ".git"), "GIT_WORK_TREE": str(foreign), "GIT_INDEX_FILE": str(foreign / ".git" / "index"),
        }
        clean_result = _two_calls(mine, _context_env(tmp_path / "mine"), lanes=("staff-sdet",))
        polluted_env = {**_context_env(tmp_path / "also-mine"), **{name: exported[name] for name in names}}

        polluted = _two_calls(repo, polluted_env, lanes=("staff-sdet",))

        header, rows = _parse_index(_lane_files(polluted, "staff-sdet")["index"])
        assert header["root"] == str(repo)
        assert _lane_bytes(polluted, "staff-sdet") == _lane_bytes(clean_result, "staff-sdet")
        assert "foreign-only.txt" not in {row["path"] for row in rows}

    def test_default_mode_still_honors_the_ambient_git_environment(self, tmp_path):
        """The mode's unset sits inside the --context-files branch, never at the file top, because default mode's stdout is
        what _lib_cumulative_diff_hash hashes: a foreign GIT_DIR keeps steering default mode as it did before this mode."""
        repo = _small_repo(tmp_path / "mine", "cumulative")
        foreign = _small_repo(tmp_path / "theirs", "cumulative")
        (foreign / "foreign-only.txt").write_text("belongs to the other repository\n")
        _commit_all(foreign, "foreign work")
        env = _context_env(tmp_path / "mine")
        polluted_env = {**env, "GIT_DIR": str(foreign / ".git"), "GIT_WORK_TREE": str(foreign)}

        mine = subprocess.run([str(_SCRIPT)], cwd=repo, env=env, capture_output=True, text=True, check=False)
        theirs = subprocess.run([str(_SCRIPT)], cwd=repo, env=polluted_env, capture_output=True, text=True, check=False)

        assert mine.returncode == 0 and theirs.returncode == 0, (mine.stderr, theirs.stderr)
        assert "foreign-only.txt" not in mine.stdout
        assert "foreign-only.txt" in theirs.stdout


# --- withholding --------------------------------------------------------------------------------------------------------------


def _write(repo: Path, name: str, text: str) -> None:
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def _write_credential_guard(env: dict, *globs: str) -> None:
    """The opt-in credential-file-guard.md in the run's config dir, one glob per line."""
    (Path(env["CLAUDE_CONFIG_DIR"]) / "credential-file-guard.md").write_text("".join(f"{glob}\n" for glob in globs))


def _write_data_guard(env: dict, *globs: str) -> None:
    """The opt-in data-file-read-guard.md in the run's config dir, one glob per line."""
    (Path(env["CLAUDE_CONFIG_DIR"]) / "data-file-read-guard.md").write_text("".join(f"{glob}\n" for glob in globs))


def _canary_repo(tmp_path: Path, source: str) -> Path:
    """A repository whose changed set holds one withheld path of each reachable kind, each carrying canaries in its diff,
    its base pre-image and its history, beside an innocuous control that carries the same three canaries.

    - .env: the dotenv basename rule and the built-in regex.
    - keys/<RSA key basename> renamed to keys/moved-key.txt: a rename out of a credential-named basename.
    - deploy/renamed.txt: innocuous now, but its history reaches the credential-named earlier name deploy/<Ed25519 key basename>.
    - notes.txt: the innocuous control.
    Under --working-tree an untracked dotenv variant with a local-override suffix joins them.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    local, _bare = _make_repo_with_remote(tmp_path)
    for name, label in ((_CREDENTIAL_NAME, "secret"), ("notes.txt", "control")):
        _write(local, name, f"{label}-history-canary\n" + _numbered_lines(label, 6))
    _write(local, f"keys/{_RSA_KEY}", "rename-history-canary\n" + _numbered_lines("key", 20))
    _write(local, f"deploy/{_ED25519_KEY}", "earlier-name-canary\n" + _numbered_lines("deploy", 20))
    _commit_all(local, "canary history")
    for name, label in ((_CREDENTIAL_NAME, "secret"), ("notes.txt", "control")):
        _write(local, name, f"{label}-base-canary\n" + _numbered_lines(label, 6))
    (local / "deploy").mkdir(exist_ok=True)
    _git_out(local, "mv", f"deploy/{_ED25519_KEY}", "deploy/renamed.txt")
    _write(local, "deploy/renamed.txt", "deploy edited at rename\n" + _numbered_lines("deploy", 20))
    _commit_all(local, "canary base")
    _git_out(local, "push", "-q", "origin", "main")
    (local / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
    if source == "cumulative":
        _git_out(local, "checkout", "-q", "-b", "feature")
    for name, label in ((_CREDENTIAL_NAME, "secret"), ("notes.txt", "control")):
        _write(local, name, f"{label}-diff-canary\n" + _numbered_lines(label, 6))
    _git_out(local, "mv", f"keys/{_RSA_KEY}", "keys/moved-key.txt")
    _write(local, "keys/moved-key.txt", "rename-diff-canary\n" + _numbered_lines("key", 20))
    _write(local, "deploy/renamed.txt", "deploy edited again\n" + _numbered_lines("deploy", 20))
    if source == "cumulative":
        _commit_all(local, "canary change")
    else:
        _git_out(local, "add", "-A")
    if source == "working-tree":
        _write(local, f"{_CREDENTIAL_NAME}.local", "untracked-canary\n")
        _write(local, "loose.txt", "untracked and innocuous\n")
    return local


_FORBIDDEN_CANARIES = (
    b"secret-history-canary", b"secret-base-canary", b"secret-diff-canary", b"rename-history-canary", b"rename-diff-canary",
    b"earlier-name-canary", b"untracked-canary",
)
_CONTROL_CANARIES = (b"control-history-canary", b"control-base-canary", b"control-diff-canary")


def _every_byte_a_call_left(repo: Path, results: tuple[subprocess.CompletedProcess, ...]) -> bytes:
    """All of agent-reviews/ (dotfiles included) plus the captured output of the calls."""
    pieces = []
    directory = repo / "agent-reviews"
    for path in sorted(directory.iterdir()) if directory.exists() else []:
        pieces.append(path.read_bytes())
    for result in results:
        pieces += [result.stdout.encode("utf-8", "surrogateescape"), result.stderr.encode("utf-8", "surrogateescape")]
    return b"\n".join(pieces)


class TestWithholding:
    @pytest.mark.parametrize("source", _ALL_SOURCES)
    def test_no_withheld_byte_in_any_lane_file(self, tmp_path, source):
        """Canaries in a withheld path's diff block, base pre-image and history stay out of every file and every output line,
        across every lane form; the innocuous control's canaries are present as the positive control."""
        repo = _canary_repo(tmp_path, source)
        env = _context_env(tmp_path)
        listing_id, _count, rows = _parse_listing(_run_context(repo, env, *_SOURCE_ARGS[source], list_only=True))
        withheld_numbers = ",".join(str(row["n"]) for row in rows if row["withheld"] == "withheld")
        lanes = ("staff-sdet", "comment-discipline-reviewer:diff", f"staff-backend-engineer={withheld_numbers}", "ciso-reviewer")

        result = _run_context(repo, env, *_SOURCE_ARGS[source], listing=listing_id, lanes=lanes)

        assert result.returncode == 0, result.stderr
        everything = _every_byte_a_call_left(repo, (result,))
        for canary in _FORBIDDEN_CANARIES:
            assert canary not in everything, canary
        for canary in _CONTROL_CANARIES:
            lane = _lane_files(result, "staff-sdet")
            assert canary in lane["diff"].read_bytes() + lane["base"].read_bytes() + lane["history"].read_bytes(), canary
        assert list(Path(env["TMPDIR"]).iterdir()) == []

    @pytest.mark.parametrize(("store_directory", "read_verdict"), [(".aws", "deny"), ("project", "allow")])
    def test_a_credential_name_judged_by_its_root_is_withheld_in_both_calls_and_poisons_a_renamed_history(
        self, tmp_path, store_directory, read_verdict
    ):
        """A top-level credentials file is credential-named only under a .aws root. Both calls scan against that root, so
        under .aws the file is withheld and notes.txt, renamed from its earlier name, loses its history; under project
        nothing is withheld and the new file ships."""
        repo = tmp_path / store_directory
        _init_repo(repo)
        _write(repo, "credentials", "history-canary-a\n" + _numbered_lines("line", 20))
        _commit_all(repo, "add credentials")
        _git_out(repo, "mv", "credentials", "notes.txt")
        _write(repo, "notes.txt", "renamed content\n" + _numbered_lines("line", 20))
        _commit_all(repo, "rename credentials to notes")
        (repo / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
        _write(repo, "notes.txt", "edited content\n" + _numbered_lines("line", 20))
        _write(repo, "credentials", "diff-canary-b\n" + _numbered_lines("other", 3))
        _git_out(repo, "add", "-A")
        env = _context_env(tmp_path)
        hook_verdict = _credential_read_gates_verdict("credentials", Path(env["HOME"]), str(repo.resolve()))
        assert hook_verdict == read_verdict
        history_patch = _git_out(repo, "log", "--follow", "-p", "--format=", "--", "notes.txt").stdout
        assert b"rename from credentials" in history_patch

        _listing_id, rows, result = _list_then_lanes(repo, env, "--staged", lanes=("staff-sdet",))

        assert result.returncode == 0, result.stderr
        listed = {row["path"]: row["withheld"] for row in rows}
        _header, index_rows = _parse_index(_lane_files(result, "staff-sdet")["index"])
        notes = {row["path"]: row["notes"] for row in index_rows}
        everything = _every_byte_a_call_left(repo, (result,))
        if hook_verdict == "deny":
            assert listed == {"credentials": "withheld", "notes.txt": "-"}
            assert "withheld" in notes["credentials"].split(",")
            assert "unavailable" in notes["notes.txt"].split(",")
            assert b"history-canary-a" not in everything
            assert b"diff-canary-b" not in everything
        else:
            assert listed == {"credentials": "-", "notes.txt": "-"}
            assert "withheld" not in notes["credentials"].split(",")
            assert "unavailable" not in notes["notes.txt"].split(",")
            assert b"+diff-canary-b\n" in _lane_files(result, "staff-sdet")["diff"].read_bytes()

    def test_a_listed_lane_naming_only_withheld_rows_has_a_preamble_only_diff_and_no_bodies(self, tmp_path):
        repo = _canary_repo(tmp_path, "staged")
        env = _context_env(tmp_path)
        listing_id, _count, rows = _parse_listing(_run_context(repo, env, "--staged", list_only=True))
        numbers = ",".join(str(row["n"]) for row in rows if row["withheld"] == "withheld")
        normal = next(row["n"] for row in rows if row["path"] == "notes.txt")

        lanes = (f"staff-sdet={numbers}", f"staff-backend-engineer={normal}")
        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=lanes)

        assert result.returncode == 0, result.stderr
        header, index_rows = _parse_index(_lane_files(result, "staff-sdet")["index"])
        diff = _lane_files(result, "staff-sdet")["diff"].read_bytes().decode()
        assert diff == f"# omitted withheld: {_CREDENTIAL_NAME}\n# omitted withheld: keys/moved-key.txt\n\n"
        assert all(row["base"] == "-" and row["history"] == "-" for row in index_rows if row["lane"] == "lane")
        assert _lane_files(result, "staff-sdet")["base"].read_bytes() == b""
        other = _lane_files(result, "staff-backend-engineer")["diff"].read_bytes()
        assert b"control-diff-canary" in other and not other.startswith(b"#")

    def test_listing_marks_withheld_for_the_same_rows_the_index_marks(self, tmp_path):
        repo = _canary_repo(tmp_path, "staged")
        env = _context_env(tmp_path)
        listing_id, _count, rows = _parse_listing(_run_context(repo, env, "--staged", list_only=True))
        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        _header, index_rows = _parse_index(_lane_files(result, "staff-sdet")["index"])

        from_listing = {row["path"] for row in rows if row["withheld"] == "withheld"}
        from_index = {row["path"] for row in index_rows if "withheld" in (row["notes"] or "").split(",")}
        assert from_listing == from_index == {_CREDENTIAL_NAME, "keys/moved-key.txt"}

    def test_a_row_whose_history_reaches_a_credential_named_earlier_name_loses_its_history_whole(self, tmp_path):
        repo = _canary_repo(tmp_path, "staged")
        env = _context_env(tmp_path)
        listing_id, _count, _rows = _parse_listing(_run_context(repo, env, "--staged", list_only=True))

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        _header, index_rows = _parse_index(_lane_files(result, "staff-sdet")["index"])
        renamed = next(row for row in index_rows if row["path"] == "deploy/renamed.txt")
        control = next(row for row in index_rows if row["path"] == "notes.txt")
        assert renamed["history"] == "-" and "unavailable" in (renamed["notes"] or "").split(",")
        assert control["history"] != "-"
        assert b"earlier-name-canary" not in _lane_files(result, "staff-sdet")["history"].read_bytes()

    @pytest.mark.parametrize(
        ("earlier_name", "why"),
        [
            (f"ü{_RSA_KEY}", "a non-ASCII byte before the credential basename"),
            (f'q"{_RSA_KEY}', "a double quote"),
            (f"b\\{_RSA_KEY}", "a backslash"),
            (f"t\t{_RSA_KEY}", f"a tab, which only unquoting exposes: the quoted text has `t` before `{_RSA_KEY}`"),
        ],
    )
    def test_history_through_an_earlier_name_is_withheld_per_quoting_class(self, tmp_path, earlier_name, why):
        tmp_path.mkdir(parents=True, exist_ok=True)
        local, _bare = _make_repo_with_remote(tmp_path)
        _write(local, earlier_name, "quoting-class-canary\n" + _numbered_lines("early", 20))
        _commit_all(local, "early name")
        _git_out(local, "mv", earlier_name, "notes.txt")
        _write(local, "notes.txt", "renamed with an edit\n" + _numbered_lines("early", 20))
        _commit_all(local, "rename away")
        _git_out(local, "push", "-q", "origin", "main")
        (local / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
        _write(local, "notes.txt", "changed again\n" + _numbered_lines("early", 20))
        _git_out(local, "add", "-A")
        env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(local, env, "--staged", list_only=True))[0]

        result = _run_context(local, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 0, result.stderr
        _header, rows = _parse_index(_lane_files(result, "staff-sdet")["index"])
        assert rows[0]["history"] == "-", why
        assert b"quoting-class-canary" not in _every_byte_a_call_left(local, (result,))

    @pytest.mark.parametrize(
        ("name", "withheld"),
        [
            (".env", True), (".env.template", False), (f"keys/{_RSA_KEY}", True), (f"{_SSH_DIR}/deploy_key", True),
            ("src/environment.py", False),
        ],
    )
    def test_withholding_by_name(self, tmp_path, name, withheld):
        """One name per predicate (dotenv, credential regex, `.ssh` directory), the template allow, and a basename that only
        contains `env`. test_pr_diff_lib.py's name matrix holds the rest of the input space against the Read hooks' verdicts."""
        tmp_path.mkdir(parents=True, exist_ok=True)
        local, _bare = _make_repo_with_remote(tmp_path)
        _write(local, name, "name-canary base\n")
        _write(local, "control.txt", "control-name-canary base\n")
        _commit_all(local, "base")
        _git_out(local, "push", "-q", "origin", "main")
        (local / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
        _write(local, name, "name-canary changed\n")
        _write(local, "control.txt", "control-name-canary changed\n")
        _git_out(local, "add", "-A", "-f")
        env = _context_env(tmp_path)
        listing_id, _count, rows = _parse_listing(_run_context(local, env, "--staged", list_only=True))

        result = _run_context(local, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 0, result.stderr
        listed = {row["path"]: row["withheld"] for row in rows}
        assert listed[name] == ("withheld" if withheld else "-")
        assert listed["control.txt"] == "-"
        everything = _every_byte_a_call_left(local, (result,))
        assert (b"name-canary" in everything.replace(b"control-name-canary", b"")) == (not withheld)
        assert b"control-name-canary changed" in everything

    @pytest.mark.parametrize("direction", ["from-credential-name", "to-credential-name"])
    def test_a_rename_is_withheld_when_either_name_is_credential_named(self, tmp_path, direction):
        tmp_path.mkdir(parents=True, exist_ok=True)
        local, _bare = _make_repo_with_remote(tmp_path)
        old, new = (f"keys/{_RSA_KEY}", "notes.txt") if direction == "from-credential-name" else ("notes.txt", f"keys/{_RSA_KEY}")
        _write(local, old, "rename-canary\n" + _numbered_lines("r", 20))
        _commit_all(local, "base")
        _git_out(local, "push", "-q", "origin", "main")
        (local / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
        (local / "keys").mkdir(exist_ok=True)
        _git_out(local, "mv", old, new)
        _git_out(local, "add", "-A")
        env = _context_env(tmp_path)
        listing_id, _count, rows = _parse_listing(_run_context(local, env, "--staged", list_only=True))

        result = _run_context(local, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert rows[0]["status"] == "R100" and rows[0]["withheld"] == "withheld"
        assert result.returncode == 0, result.stderr
        assert b"rename-canary" not in _every_byte_a_call_left(local, (result,))

    def test_untracked_credential_and_innocuous_files_get_different_preamble_reasons(self, tmp_path):
        repo = _canary_repo(tmp_path, "working-tree")
        env = _context_env(tmp_path)
        listing_id, _count, rows = _parse_listing(_run_context(repo, env, "--working-tree", list_only=True))

        result = _run_context(repo, env, "--working-tree", listing=listing_id, lanes=("staff-sdet",))

        diff = _lane_files(result, "staff-sdet")["diff"].read_text()
        assert f"# omitted withheld: {_CREDENTIAL_NAME}.local\n" in diff
        assert "# omitted untracked: loose.txt\n" in diff
        _header, index_rows = _parse_index(_lane_files(result, "staff-sdet")["index"])
        untracked_secret = next(row for row in index_rows if row["path"] == f"{_CREDENTIAL_NAME}.local")
        assert untracked_secret["notes"] == "withheld,untracked"
        assert next(row for row in index_rows if row["path"] == "loose.txt")["notes"] == "untracked"
        assert next(row for row in rows if row["path"] == f"{_CREDENTIAL_NAME}.local")["withheld"] == "withheld"

    def test_a_withheld_path_holding_a_newline_forges_no_omitted_or_diff_line(self, tmp_path):
        name = f"{_CREDENTIAL_NAME}\n# omitted untracked: forged\ndiff --git"

        def add_hostile(repo: Path) -> None:
            _write(repo, name, "hostile-canary\n")

        repo = _small_repo(tmp_path, extra_changes=add_hostile)
        env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        diff = _lane_files(result, "staff-sdet")["diff"].read_bytes()
        omitted_lines = [line for line in diff.split(b"\n") if line.startswith(b"# omitted")]
        assert omitted_lines == [f"# omitted withheld: {_cquote_index(name)}".encode()]
        assert diff.count(b"\ndiff --git ") + diff.startswith(b"diff --git ") == 3
        assert b"hostile-canary" not in _every_byte_a_call_left(repo, (result,))

    def test_a_listed_ciso_reviewer_lane_omitting_a_withheld_number_exits_2_with_no_files(self, tmp_path):
        repo = _canary_repo(tmp_path, "staged")
        env = _context_env(tmp_path)
        listing_id, _count, rows = _parse_listing(_run_context(repo, env, "--staged", list_only=True))
        withheld = [row["n"] for row in rows if row["withheld"] == "withheld"]
        innocuous = next(row["n"] for row in rows if row["path"] == "notes.txt")

        omitting = _run_context(repo, env, "--staged", listing=listing_id, lanes=(f"ciso-reviewer={innocuous}",), hygiene=False)
        partial_lane = (f"ciso-reviewer={withheld[0]},{innocuous}",)
        partial = _run_context(repo, env, "--staged", listing=listing_id, lanes=partial_lane, hygiene=False)

        for result in (omitting, partial):
            assert result.returncode == 2
            assert "the listed ciso-reviewer lane omits withheld number" in result.stderr
        assert not (repo / "agent-reviews").exists()
        complete_lane = (f"ciso-reviewer={','.join(map(str, withheld))}",)
        complete = _run_context(repo, env, "--staged", listing=listing_id, lanes=complete_lane)
        whole = _run_context(repo, env, "--staged", listing=listing_id, lanes=("ciso-reviewer",))
        other_agent = _run_context(repo, env, "--staged", listing=listing_id, lanes=(f"staff-sdet={innocuous}",))
        assert complete.returncode == whole.returncode == other_agent.returncode == 0

    def test_withholding_fails_open_when_a_predicate_tool_fails(self, tmp_path):
        """The grep behind the .ssh directory predicate exits 2: a path only that predicate names is not withheld and the
        call still exits 0, as the hook's own predicates fail open. The docs/scripts.md Withholding bullet states this accepted
        residual."""
        def add_keys(repo: Path) -> None:
            _write(repo, f"keys/{_RSA_KEY}", "withheld-key-canary\n")
            _write(repo, f"{_SSH_DIR}/deploy_key", "fail-open-ssh-canary\n")

        repo = _small_repo(tmp_path, extra_changes=add_keys)
        _git_out(repo, "add", "-f", "-A")
        base_env = _context_env(tmp_path)
        listing_id, _count, rows = _parse_listing(_run_context(repo, base_env, "--staged", list_only=True))
        withheld = {row["path"]: row["withheld"] for row in rows}
        assert withheld[f"keys/{_RSA_KEY}"] == withheld[f"{_SSH_DIR}/deploy_key"] == "withheld"
        shim_dir = tmp_path / "shim-ssh-predicate"
        _tool_shim(shim_dir, "grep", fragments=(" -oEi ",), status=2)
        env = {**base_env, "PATH": f"{shim_dir}{os.pathsep}{base_env['PATH']}"}

        failing_listing = _parse_listing(_run_context(repo, env, "--staged", list_only=True))
        result = _run_context(repo, env, "--staged", listing=failing_listing[0], lanes=("staff-sdet",))

        assert result.returncode == 0, result.stderr
        kept_block = _lane_files(result, "staff-sdet")["diff"].read_bytes()
        assert b"fail-open-ssh-canary" in kept_block, "the path reads as no match, so its block is kept"
        assert b"withheld-key-canary" not in kept_block, "the regex pass runs in the shell, so a failing grep cannot reach it"

    def test_withholding_fails_closed_when_the_ssh_per_candidate_basename_check_fails(self, tmp_path):
        """The grep -qEi behind the .ssh directory predicate's per-candidate basename check exits 2: a candidate it could not
        classify reads as unsafe, so even a safe-allowlist basename (`config`) is withheld and its block is dropped from every
        lane. The docs/scripts.md Withholding bullet states this half of the predicate's failure handling."""
        def add_ssh_files(repo: Path) -> None:
            _write(repo, f"{_SSH_DIR}/config", "fail-closed-ssh-config-canary\n")
            _write(repo, f"{_SSH_DIR}/deploy_key", "fail-closed-ssh-key-canary\n")

        repo = _small_repo(tmp_path, extra_changes=add_ssh_files)
        _git_out(repo, "add", "-f", "-A")
        base_env = _context_env(tmp_path)
        _healthy_id, _count, healthy_rows = _parse_listing(_run_context(repo, base_env, "--staged", list_only=True))
        healthy_withheld = {row["path"]: row["withheld"] for row in healthy_rows}
        assert healthy_withheld[f"{_SSH_DIR}/deploy_key"] == "withheld"
        assert healthy_withheld[f"{_SSH_DIR}/config"] != "withheld", "control: allowlisted basename is safe"
        shim_dir = tmp_path / "shim-ssh-per-candidate"
        _tool_shim(shim_dir, "grep", fragments=(" -qEi ",), status=2)
        env = {**base_env, "PATH": f"{shim_dir}{os.pathsep}{base_env['PATH']}"}

        failing_id, _count, failing_rows = _parse_listing(_run_context(repo, env, "--staged", list_only=True))
        result = _run_context(repo, env, "--staged", listing=failing_id, lanes=("staff-sdet",))

        failing_withheld = {row["path"]: row["withheld"] for row in failing_rows}
        assert failing_withheld[f"{_SSH_DIR}/config"] == failing_withheld[f"{_SSH_DIR}/deploy_key"] == "withheld"
        assert failing_withheld["one.txt"] != "withheld", (
            "control: a path outside the .ssh directory is not withheld by the failing per-candidate grep"
        )
        assert result.returncode == 0, result.stderr
        lane_diff = _lane_files(result, "staff-sdet")["diff"].read_bytes()
        assert b"fail-closed-ssh-config-canary" not in lane_diff
        assert b"fail-closed-ssh-key-canary" not in lane_diff

    def test_history_withholding_fails_closed_when_the_header_scan_fails(self, tmp_path):
        """A grep that fails the history-header scan checked no header, so every history body is dropped and its row noted
        `unavailable`. The same run without the failing grep ships the control's history body."""
        repo = _canary_repo(tmp_path, "staged")
        base_env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(repo, base_env, "--staged", list_only=True))[0]
        shim_dir = tmp_path / "shim-history-scan"
        _tool_shim(shim_dir, "grep", fragments=(" -a -H ",), status=2)
        failing_env = {**base_env, "PATH": f"{shim_dir}{os.pathsep}{base_env['PATH']}"}

        healthy = _run_context(repo, base_env, "--staged", listing=listing_id, lanes=("staff-sdet",))
        healthy_history = _lane_files(healthy, "staff-sdet")["history"].read_bytes()
        failing = _run_context(repo, failing_env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert healthy.returncode == failing.returncode == 0, failing.stderr
        failing_files = _lane_files(failing, "staff-sdet")
        assert b"control-history-canary" in healthy_history
        assert failing_files["history"].read_bytes() == b""
        control_row = next(row for row in _parse_index(failing_files["index"])[1] if row["path"] == "notes.txt")
        assert control_row["history"] == "-"
        assert "unavailable" in (control_row["notes"] or "").split(",")

    @pytest.mark.parametrize("source", _ALL_SOURCES)
    def test_both_withholding_collectors_work_under_a_grep_without_gnu_null_options(self, tmp_path, source):
        """A BSD-shaped grep rejects -z, -Z, --null and --null-data (exit 2). The regex-named and history-reached canaries
        stay withheld, so neither collector depends on a GNU-only option."""
        repo = _canary_repo(tmp_path, source)
        base_env = _context_env(tmp_path)
        shim_dir = tmp_path / "bsd-grep"
        shim_dir.mkdir()
        real_grep = shutil.which("grep")
        assert real_grep is not None
        shim = shim_dir / "grep"
        shim.write_text(
            "#!/bin/bash\n"
            'for arg in "$@"; do\n'
            '  case $arg in\n'
            '    --null|--null-data|-z|-Z|-[a-zA-Z]*[zZ]*) echo "grep: unknown option -- $arg" >&2; exit 2 ;;\n'
            "  esac\n"
            "done\n"
            f'exec {shlex.quote(real_grep)} "$@"\n'
        )
        shim.chmod(0o755)
        env = {**base_env, "PATH": f"{shim_dir}{os.pathsep}{base_env['PATH']}"}
        listing_id, _count, rows = _parse_listing(_run_context(repo, env, *_SOURCE_ARGS[source], list_only=True))
        withheld_numbers = ",".join(str(row["n"]) for row in rows if row["withheld"] == "withheld")
        lanes = ("staff-sdet", f"staff-backend-engineer={withheld_numbers}", "ciso-reviewer")

        result = _run_context(repo, env, *_SOURCE_ARGS[source], listing=listing_id, lanes=lanes)

        assert result.returncode == 0, result.stderr
        assert {row["path"] for row in rows if row["withheld"] == "withheld"} >= {_CREDENTIAL_NAME, "keys/moved-key.txt"}
        everything = _every_byte_a_call_left(repo, (result,))
        for canary in _FORBIDDEN_CANARIES:
            assert canary not in everything, canary
        lane = _lane_files(result, "staff-sdet")
        for canary in _CONTROL_CANARIES:
            assert canary in lane["diff"].read_bytes() + lane["base"].read_bytes() + lane["history"].read_bytes(), canary

    @pytest.mark.parametrize(
        ("earlier_name", "guard_glob", "withheld"),
        [
            (f"{_SSH_DIR}/deploy_key", None, True),
            (".env.ci", None, True),
            ("guarded-vault/store.dat", "*/guarded-vault/*", True),
            ("early/plain.dat", "*/guarded-vault/*", False),
        ],
        ids=["ssh-directory-predicate", "dotenv-basename-rule", "guard-glob", "innocuous-control"],
    )
    def test_history_through_an_earlier_name_only_one_predicate_half_names_is_withheld_whole(
        self, tmp_path, earlier_name, guard_glob, withheld
    ):
        """The history walk reaches an earlier name that the built-in regex does not name; each of the other predicate halves
        must still drop the body whole. The control ships its body when no predicate names the earlier name."""
        tmp_path.mkdir(parents=True, exist_ok=True)
        local, _bare = _make_repo_with_remote(tmp_path)
        _write(local, earlier_name, "earlier-half-canary\n" + _numbered_lines("early", 20))
        _commit_all(local, "early name")
        _git_out(local, "mv", earlier_name, "notes.txt")
        _write(local, "notes.txt", "renamed with an edit\n" + _numbered_lines("early", 20))
        _commit_all(local, "rename away")
        _git_out(local, "push", "-q", "origin", "main")
        (local / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
        _write(local, "notes.txt", "changed again\n" + _numbered_lines("early", 20))
        _git_out(local, "add", "-A")
        env = _context_env(tmp_path)
        if guard_glob is not None:
            _write_credential_guard(env, guard_glob)
        listing_id, _count, listed = _parse_listing(_run_context(local, env, "--staged", list_only=True))

        result = _run_context(local, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 0, result.stderr
        assert [row["withheld"] for row in listed] == ["-"], "the current name is innocuous, so only the history walk can drop it"
        _header, rows = _parse_index(_lane_files(result, "staff-sdet")["index"])
        everything = _every_byte_a_call_left(local, (result,))
        if withheld:
            assert rows[0]["history"] == "-" and "unavailable" in (rows[0]["notes"] or "").split(",")
            assert b"earlier-half-canary" not in everything
        else:
            assert rows[0]["history"] != "-" and "unavailable" not in (rows[0]["notes"] or "").split(",")
            assert b"earlier-half-canary" in everything

    def test_a_modified_path_only_a_user_glob_matches_loses_its_diff_base_and_history(self, tmp_path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        local, _bare = _make_repo_with_remote(tmp_path)
        for name in ("vault/ledger.txt", "control.txt"):
            label = "glob" if name.startswith("vault") else "control"
            _write(local, name, f"{label}-history-canary\n" + _numbered_lines(label, 6))
        _commit_all(local, "history")
        for name in ("vault/ledger.txt", "control.txt"):
            label = "glob" if name.startswith("vault") else "control"
            _write(local, name, f"{label}-base-canary\n" + _numbered_lines(label, 6))
        _commit_all(local, "base")
        _git_out(local, "push", "-q", "origin", "main")
        (local / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
        for name in ("vault/ledger.txt", "control.txt"):
            label = "glob" if name.startswith("vault") else "control"
            _write(local, name, f"{label}-diff-canary\n" + _numbered_lines(label, 6))
        _git_out(local, "add", "-A")
        env = _context_env(tmp_path)
        _write_credential_guard(env, "*/vault/*")
        listing_id, _count, rows = _parse_listing(_run_context(local, env, "--staged", list_only=True))

        result = _run_context(local, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert {row["path"]: row["withheld"] for row in rows} == {"control.txt": "-", "vault/ledger.txt": "withheld"}
        assert result.returncode == 0, result.stderr
        everything = _every_byte_a_call_left(local, (result,))
        for canary in (b"glob-history-canary", b"glob-base-canary", b"glob-diff-canary"):
            assert canary not in everything, canary
        for canary in (b"control-history-canary", b"control-base-canary", b"control-diff-canary"):
            assert canary in everything, canary

    def test_a_rename_whose_old_name_alone_matches_a_user_glob_is_withheld(self, tmp_path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        local, _bare = _make_repo_with_remote(tmp_path)
        _write(local, "vault/old-name.txt", "glob-rename-canary\n" + _numbered_lines("moved", 20))
        _commit_all(local, "base")
        _git_out(local, "push", "-q", "origin", "main")
        (local / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
        _git_out(local, "mv", "vault/old-name.txt", "moved-out.txt")
        _write(local, "moved-out.txt", "glob-rename-canary\n" + _numbered_lines("moved", 20).replace("line 0003", "line three"))
        _git_out(local, "add", "-A")
        env = _context_env(tmp_path)
        _write_credential_guard(env, "*/vault/*")
        listing_id, _count, rows = _parse_listing(_run_context(local, env, "--staged", list_only=True))

        result = _run_context(local, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert rows[0]["status"].startswith("R")
        assert (rows[0]["path"], rows[0]["old_path"]) == ("moved-out.txt", "vault/old-name.txt")
        assert rows[0]["withheld"] == "withheld"
        assert result.returncode == 0, result.stderr
        assert b"glob-rename-canary" not in _every_byte_a_call_left(local, (result,))

    @pytest.mark.parametrize("armed", [True, False], ids=["guard-armed", "guard-absent"])
    def test_the_data_file_guard_withholds_by_extension_glob_and_earlier_name_only_when_its_file_exists(self, tmp_path, armed):
        """One wiring case per branch: the library owns the predicate matrix, so this pins that the script loads the guard
        before it scans the changed set and again before it scans the history headers."""
        tmp_path.mkdir(parents=True, exist_ok=True)
        local, _bare = _make_repo_with_remote(tmp_path)
        by_label = {"data/export.csv": "extension", "exports-private/report.txt": "glob", "notes.txt": "control"}
        for name, label in by_label.items():
            _write(local, name, f"{label}-history-canary\n" + _numbered_lines(label, 6))
        _write(local, "early/old-export.csv", "earlier-name-canary\n" + _numbered_lines("deploy", 20))
        _commit_all(local, "history")
        for name, label in by_label.items():
            _write(local, name, f"{label}-base-canary\n" + _numbered_lines(label, 6))
        (local / "deploy").mkdir()
        _git_out(local, "mv", "early/old-export.csv", "deploy/renamed.txt")
        _write(local, "deploy/renamed.txt", "deploy edited at rename\n" + _numbered_lines("deploy", 20))
        _commit_all(local, "base")
        _git_out(local, "push", "-q", "origin", "main")
        (local / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
        for name, label in by_label.items():
            _write(local, name, f"{label}-diff-canary\n" + _numbered_lines(label, 6))
        _write(local, "deploy/renamed.txt", "deploy edited again\n" + _numbered_lines("deploy", 20))
        _git_out(local, "add", "-A")
        env = _context_env(tmp_path)
        if armed:
            _write_data_guard(env, "*/exports-private/*")
        listing_id, _count, listed = _parse_listing(_run_context(local, env, "--staged", list_only=True))

        result = _run_context(local, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 0, result.stderr
        _header, rows = _parse_index(_lane_files(result, "staff-sdet")["index"])
        renamed = next(row for row in rows if row["path"] == "deploy/renamed.txt")
        renamed_notes = (renamed["notes"] or "").split(",")
        everything = _every_byte_a_call_left(local, (result,))
        guarded_canaries = (
            b"extension-history-canary", b"extension-base-canary", b"extension-diff-canary", b"glob-history-canary",
            b"glob-base-canary", b"glob-diff-canary", b"earlier-name-canary",
        )
        for canary in (b"control-history-canary", b"control-base-canary", b"control-diff-canary"):
            assert canary in everything, canary
        if armed:
            assert {row["path"]: row["withheld"] for row in listed} == {
                "data/export.csv": "withheld", "exports-private/report.txt": "withheld",
                "deploy/renamed.txt": "-", "notes.txt": "-",
            }
            assert renamed["history"] == "-" and "unavailable" in renamed_notes
            for canary in guarded_canaries:
                assert canary not in everything, canary
        else:
            assert {row["withheld"] for row in listed} == {"-"}
            assert renamed["history"] != "-" and "unavailable" not in renamed_notes
            for canary in guarded_canaries:
                assert canary in everything, canary

    def test_a_withheld_type_change_leaves_neither_of_its_two_header_blocks_in_any_lane_file(self, tmp_path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        local, _bare = _make_repo_with_remote(tmp_path)
        _write(local, f"keys/{_RSA_KEY}", "type-change-canary\n" + _numbered_lines("key", 6))
        _write(local, "control.txt", "control-diff-canary base\n")
        _commit_all(local, "base")
        _git_out(local, "push", "-q", "origin", "main")
        (local / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
        (local / "keys" / _RSA_KEY).unlink()
        os.symlink("elsewhere", local / "keys" / _RSA_KEY)
        _write(local, "control.txt", "control-diff-canary changed\n")
        _git_out(local, "add", "-A")
        env = _context_env(tmp_path)
        listing_id, _count, rows = _parse_listing(_run_context(local, env, "--staged", list_only=True))

        lanes = ("staff-sdet", "comment-discipline-reviewer:diff")
        result = _run_context(local, env, "--staged", listing=listing_id, lanes=lanes)

        assert {row["path"]: (row["status"], row["withheld"]) for row in rows} == {
            "control.txt": ("M", "-"), f"keys/{_RSA_KEY}": ("T", "withheld"),
        }
        assert result.returncode == 0, result.stderr
        for agent in ("staff-sdet", "comment-discipline-reviewer"):
            diff = _lane_files(result, agent)["diff"].read_bytes()
            assert f"diff --git a/keys/{_RSA_KEY} b/keys/{_RSA_KEY}".encode() not in diff
            assert diff.startswith(f"# omitted withheld: keys/{_RSA_KEY}\n\n".encode())
            assert b"control-diff-canary changed" in diff
        assert b"type-change-canary" not in _every_byte_a_call_left(local, (result,))


# --- history, memoization and caps ------------------------------------------------------------------------------------------


def _history_repo(tmp_path: Path, prior_commits: int, *, name: str = "hist.txt") -> Path:
    """A repository whose `name` has `prior_commits` commits before the staged change."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    local, _bare = _make_repo_with_remote(tmp_path)
    for index in range(prior_commits):
        _write(local, name, _numbered_lines("hist", 10).replace(f"line {index + 1:04d}", f"line edit {index}"))
        _commit_all(local, f"history commit {index}")
    _git_out(local, "push", "-q", "origin", "main")
    (local / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
    _write(local, name, _numbered_lines("hist", 10) + "staged change\n")
    _git_out(local, "add", "-A")
    return local


def _script_with_lib_overrides(tmp_path: Path, overrides: str) -> Path:
    """The script in a layout whose `_pr-diff-lib.sh` sources the real lib and then runs `overrides`, so a test can reset a lib
    variable or redefine a lib function without touching the repository's files."""
    layout = tmp_path / "layout"
    (layout / "scripts").mkdir(parents=True)
    (layout / "scripts" / "pr-diff-against-base.sh").symlink_to(_SCRIPT)
    (layout / "hooks").symlink_to(HOOKS_DIR)
    (layout / "scripts" / "_pr-diff-lib.sh").write_text(f"source {shlex.quote(str(_LIB_PR_DIFF_SH))}\n{overrides}\n")
    return layout / "scripts" / "pr-diff-against-base.sh"


class TestHistoryAndBodies:
    @pytest.mark.parametrize("prior_commits", [1, 2, 5, 6])
    def test_history_holds_the_newest_first_up_to_five_commits_without_author_fields(self, tmp_path, prior_commits):
        repo = _history_repo(tmp_path, prior_commits)
        env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        history = _lane_files(result, "staff-sdet")["history"].read_text()
        subjects = re.findall(r"^commit [0-9a-f]{40}\n\d{4}-\d\d-\d\d (history commit \d+)$", history, re.M)
        assert subjects == [f"history commit {number}" for number in range(prior_commits - 1, max(prior_commits - 6, -1), -1)]
        assert len(subjects) == min(prior_commits, 5)
        assert "Author:" not in history and "Date:" not in history
        assert "staged change" not in history

    def test_a_new_path_has_no_history_range_and_no_note(self, tmp_path):
        repo = _history_repo(tmp_path, 1)
        _write(repo, "brand-new.txt", "new\n")
        _git_out(repo, "add", "-A")
        env = _context_env(tmp_path)
        listing_id, _count, rows = _parse_listing(_run_context(repo, env, "--staged", list_only=True))

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        _header, index_rows = _parse_index(_lane_files(result, "staff-sdet")["index"])
        new_row = next(row for row in index_rows if row["path"] == "brand-new.txt")
        assert (new_row["base"], new_row["history"], new_row["notes"]) == ("-", "-", "-")

    def test_history_follows_a_rename_from_the_old_name(self, tmp_path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        local, _bare = _make_repo_with_remote(tmp_path)
        for index in range(3):
            _write(local, "old.txt", _numbered_lines("old", 20).replace(f"line {index + 1:04d}", f"line edit {index}"))
            _commit_all(local, f"old name commit {index}")
        _git_out(local, "mv", "old.txt", "new.txt")
        _commit_all(local, "rename commit")
        _git_out(local, "push", "-q", "origin", "main")
        (local / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
        _write(local, "new.txt", _numbered_lines("old", 20) + "staged\n")
        _git_out(local, "add", "-A")
        env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(local, env, "--staged", list_only=True))[0]

        result = _run_context(local, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        history = _lane_files(result, "staff-sdet")["history"].read_text()
        subjects = re.findall(r"^\d{4}-\d\d-\d\d (.*)$", history, re.M)
        assert subjects == ["rename commit", "old name commit 2", "old name commit 1", "old name commit 0"]

    def test_history_reads_a_path_with_glob_characters_literally(self, tmp_path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        local, _bare = _make_repo_with_remote(tmp_path)
        _write(local, "g[lo]b*?.txt", _numbered_lines("literal", 5))
        _write(local, "globx.txt", _numbered_lines("sibling", 5))
        _commit_all(local, "both files")
        _write(local, "globx.txt", _numbered_lines("sibling", 5) + "more\n")
        _commit_all(local, "sibling only")
        _git_out(local, "push", "-q", "origin", "main")
        (local / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
        _write(local, "g[lo]b*?.txt", _numbered_lines("literal", 5) + "staged\n")
        _git_out(local, "add", "-A")
        env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(local, env, "--staged", list_only=True))[0]

        result = _run_context(local, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        history = _lane_files(result, "staff-sdet")["history"].read_text()
        assert "both files" in history and "sibling only" not in history

    def test_two_lanes_naming_one_path_make_one_log_and_one_blob_read_for_it(self, tmp_path):
        repo = _history_repo(tmp_path, 2)
        shim = GitShim(tmp_path / "git_shim")
        env = shim.env(_context_env(tmp_path))
        listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]
        shim.clear_log()

        lanes = ("staff-sdet", "staff-backend-engineer", "ciso-reviewer=1")
        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=lanes)

        assert result.returncode == 0, result.stderr
        enrichment = [call["argv"] for call in shim.calls() if call["subcommand"] in ("log", "cat-file")]
        assert sum(1 for argv in enrichment if "log" in argv) == 1
        assert sum(1 for argv in enrichment if "blob" in argv) == 1
        assert len({tuple(argv) for argv in enrichment}) == len(enrichment), "no two enrichment calls share an argv"

    @pytest.mark.parametrize("failing", [("cat-file", "cat-file-s"), ("cat-file-blob", "cat-file-blob"), ("log", "log")])
    def test_a_failing_body_read_degrades_the_row_and_exits_0_with_a_degraded_line(self, tmp_path, failing):
        repo = _history_repo(tmp_path, 2)
        kind = failing[0]
        shim = GitShim(tmp_path / "git_shim")
        if kind == "cat-file":
            shim.add_rule(subcommand="cat-file", match=["-s"], mode="fail", status=128)
        elif kind == "cat-file-blob":
            shim.add_rule(subcommand="cat-file", match=["blob"], mode="fail", status=128)
        else:
            shim.add_rule(subcommand="log", mode="fail", status=128)
        env = shim.env(_context_env(tmp_path))
        listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 0, result.stderr
        assert re.search(r"^CONTEXT_DEGRADED: [1-9]\d*$", result.stderr, re.M)
        _header, rows = _parse_index(_lane_files(result, "staff-sdet")["index"])
        assert "unavailable" in (rows[0]["notes"] or "").split(",")

    def test_a_log_call_killed_mid_write_degrades_and_none_of_its_bytes_reach_a_lane_file(self, tmp_path):
        repo = _history_repo(tmp_path, 2)
        shim = GitShim(tmp_path / "git_shim")
        shim.add_rule(subcommand="log", mode="print", stdout="PARTIAL-HISTORY-BYTES\n", status=124)
        env = shim.env(_context_env(tmp_path))
        listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 0, result.stderr
        assert b"PARTIAL-HISTORY-BYTES" not in _every_byte_a_call_left(repo, (result,))
        _header, rows = _parse_index(_lane_files(result, "staff-sdet")["index"])
        assert rows[0]["history"] == "-" and "unavailable" in (rows[0]["notes"] or "").split(",")

    def test_an_exhausted_enrichment_budget_skips_every_body_and_still_exits_0(self, tmp_path):
        """The budget is a lib variable, so a layout whose `_pr-diff-lib.sh` sets it to 0 exhausts it before the first body."""
        script = _script_with_lib_overrides(tmp_path, "CONTEXT_ENRICHMENT_SECONDS=0")
        repo = _small_repo(tmp_path / "work")
        env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True, script=script))[0]

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",), script=script)

        assert result.returncode == 0, result.stderr
        _header, rows = _parse_index(_lane_files(result, "staff-sdet")["index"])
        enriched = [row for row in rows if row["status"] != "A"]
        assert len(enriched) == 2, "the modified and the renamed row; an added row has no pre-image"
        for row in rows:
            assert row["base"] == row["history"] == "-"
            assert ("skipped" in (row["notes"] or "").split(",")) == (row["status"] != "A"), row
        assert _token_values(result, "CONTEXT_DEGRADED") == [str(2 * len(enriched))]

    def test_a_budget_lapsing_before_the_commit_list_read_skips_the_history_and_keeps_the_base(self, tmp_path):
        """The budget check before the commit-list read is the fourth call for the first enriched row: the base size read, the
        base blob read and the patch-history read each clear one. A history over the cap is what reaches the fourth."""
        counting_has_time = (
            "HISTORY_BYTES_PER_PATH=100\n"
            "enrichment_time_checks=0\n"
            "_pr_diff_enrichment_has_time() {\n"
            "  enrichment_time_checks=$((enrichment_time_checks + 1))\n"
            '  [ "$enrichment_time_checks" -le 3 ]\n'
            "}"
        )
        script = _script_with_lib_overrides(tmp_path, counting_has_time)
        repo = _history_repo(tmp_path / "work", 3)
        env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True, script=script))[0]

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",), script=script)

        assert result.returncode == 0, result.stderr
        _header, rows = _parse_index(_lane_files(result, "staff-sdet")["index"])
        notes = (rows[0]["notes"] or "").split(",")
        assert rows[0]["base"] != "-", "the base was read before the budget lapsed"
        assert rows[0]["history"] == "-" and "skipped" in notes
        assert "commits-only" not in notes
        assert _token_values(result, "CONTEXT_DEGRADED") == ["1"]
        assert list(Path(env["TMPDIR"]).iterdir()) == []

    def test_a_base_over_the_byte_cap_is_omitted_and_noted_over_cap(self, tmp_path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        local, _bare = _make_repo_with_remote(tmp_path)
        _write(local, "huge.txt", "x" * 300_000 + "\n")
        _commit_all(local, "huge base")
        _git_out(local, "push", "-q", "origin", "main")
        (local / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
        _write(local, "huge.txt", "y" * 300_000 + "\n")
        _git_out(local, "add", "-A")
        env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(local, env, "--staged", list_only=True))[0]

        result = _run_context(local, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        _header, rows = _parse_index(_lane_files(result, "staff-sdet")["index"])
        assert rows[0]["base"] == "-" and "over-cap" in (rows[0]["notes"] or "").split(",")
        assert _lane_files(result, "staff-sdet")["base"].read_bytes() == b""
        assert re.search(r"^CONTEXT_DEGRADED: [1-9]\d*$", result.stderr, re.M)

    def test_a_history_over_the_byte_cap_becomes_its_commit_list(self, tmp_path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        local, _bare = _make_repo_with_remote(tmp_path)
        for index in range(3):
            _write(local, "churn.txt", "".join(f"churn {index} line {number:05d}\n" for number in range(5000)))
            _commit_all(local, f"churn commit {index}")
        _git_out(local, "push", "-q", "origin", "main")
        (local / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
        _write(local, "churn.txt", "tiny\n")
        _git_out(local, "add", "-A")
        env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(local, env, "--staged", list_only=True))[0]

        result = _run_context(local, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        _header, rows = _parse_index(_lane_files(result, "staff-sdet")["index"])
        history = _lane_files(result, "staff-sdet")["history"].read_text()
        assert "commits-only" in (rows[0]["notes"] or "").split(",")
        subjects = re.findall(r"^\d{4}-\d\d-\d\d (churn commit \d)$", history, re.M)
        assert subjects == ["churn commit 2", "churn commit 1", "churn commit 0"]
        assert "diff --git" not in history and len(history) < 1000

    def test_bodies_stop_once_a_lanes_bytes_reach_the_lane_cap(self, tmp_path):
        """Six 250,000-byte pre-images: the fifth base takes the lane past 1 MiB, so row five's history and row six drop."""
        tmp_path.mkdir(parents=True, exist_ok=True)
        local, _bare = _make_repo_with_remote(tmp_path)
        for number in range(1, 7):
            _write(local, f"big{number}.txt", "a" * 250_000 + "\n")
        _commit_all(local, "six big files")
        _git_out(local, "push", "-q", "origin", "main")
        (local / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
        for number in range(1, 7):
            _write(local, f"big{number}.txt", "b" * 250_000 + "\n")
        _git_out(local, "add", "-A")
        env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(local, env, "--staged", list_only=True))[0]

        result = _run_context(local, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        _header, rows = _parse_index(_lane_files(result, "staff-sdet")["index"])
        assert [row["base"] != "-" for row in rows] == [True, True, True, True, True, False]
        assert [row["history"] != "-" for row in rows] == [True, True, True, True, False, False]
        assert all("commits-only" in (row["notes"] or "").split(",") for row in rows[:4])
        assert all("over-cap" in (row["notes"] or "").split(",") for row in rows[4:])
        # Rows 1-4 are commits-only (4), row 5 loses its history to the lane cap (1), and row 6 loses its base and history (2).
        assert "CONTEXT_DEGRADED: 7\n" in result.stderr

    def test_a_cumulative_history_omits_the_change_s_own_commits_and_keeps_the_base_side_ones(self, tmp_path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        local, _bare = _make_repo_with_remote(tmp_path)
        for index in range(2):
            _write(local, "hist.txt", _numbered_lines("hist", 10).replace(f"line {index + 1:04d}", f"line edit {index}"))
            _commit_all(local, f"base-side commit {index}")
        _git_out(local, "push", "-q", "origin", "main")
        (local / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
        _git_out(local, "checkout", "-q", "-b", "feature")
        for index in range(2):
            _write(local, "hist.txt", _numbered_lines("hist", 10) + f"feature line {index}\n")
            _commit_all(local, f"feature-side commit {index}")
        env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(local, env, list_only=True))[0]

        result = _run_context(local, env, listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 0, result.stderr
        history = _lane_files(result, "staff-sdet")["history"].read_text()
        subjects = re.findall(r"^\d{4}-\d\d-\d\d (.*)$", history, re.M)
        assert subjects == ["base-side commit 1", "base-side commit 0"]
        assert "feature-side" not in history

    def test_a_merge_commit_touching_the_path_is_not_in_its_history(self, tmp_path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        local, _bare = _make_repo_with_remote(tmp_path)
        _write(local, "hist.txt", _numbered_lines("hist", 10))
        _commit_all(local, "history seed")
        _git_out(local, "checkout", "-q", "-b", "side")
        _write(local, "hist.txt", _numbered_lines("hist", 10).replace("line 0002", "line side edit"))
        _commit_all(local, "side branch edit")
        _git_out(local, "checkout", "-q", "main")
        _write(local, "other.txt", "main moves on\n")
        _commit_all(local, "main moves on")
        _git_out(local, "merge", "--no-ff", "-q", "-m", "merge the side branch", "side")
        _git_out(local, "push", "-q", "origin", "main")
        (local / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
        _write(local, "hist.txt", _numbered_lines("hist", 10) + "staged change\n")
        _git_out(local, "add", "-A")
        assert len(_git_out(local, "log", "-1", "--format=%p").stdout.split()) == 2, "the fixture holds a merge commit"
        env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(local, env, "--staged", list_only=True))[0]

        result = _run_context(local, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 0, result.stderr
        history = _lane_files(result, "staff-sdet")["history"].read_text()
        subjects = re.findall(r"^\d{4}-\d\d-\d\d (.*)$", history, re.M)
        assert "merge the side branch" not in subjects
        assert subjects == ["side branch edit", "history seed"]

    def test_a_history_over_the_byte_cap_never_has_more_than_the_cap_plus_one_byte_on_disk(self, tmp_path, spawn_blocked_run):
        """The held call is the commit-list read, which runs while the patch history's temp still exists: that temp holds the
        cap plus one byte however long the history is."""
        tmp_path.mkdir(parents=True, exist_ok=True)
        local, _bare = _make_repo_with_remote(tmp_path)
        for index in range(3):
            _write(local, "churn.txt", "".join(f"churn {index} line {number:05d}\n" for number in range(20000)))
            _commit_all(local, f"churn commit {index}")
        _git_out(local, "push", "-q", "origin", "main")
        (local / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
        _write(local, "churn.txt", "tiny\n")
        _git_out(local, "add", "-A")
        env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(local, env, "--staged", list_only=True))[0]
        history_cap = _lib_constant("HISTORY_BYTES_PER_PATH")
        hold = {"subcommand": "log", "match": ["--format=commit %H%n%ad %s"], "absent": ["%n%n%b"]}

        process, _started, release = spawn_blocked_run(local, env, listing_id, tmp_path, hold=hold, label="commit-list")
        (private_dir,) = list(Path(env["TMPDIR"]).iterdir())
        sizes = sorted(path.stat().st_size for path in (private_dir / "history").iterdir())
        release.write_text("go")
        stdout, stderr = process.communicate(timeout=60)
        result = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)

        assert sizes == [0, history_cap + 1]
        assert result.returncode == 0, result.stderr
        _header, rows = _parse_index(_lane_files(result, "staff-sdet")["index"])
        assert "commits-only" in (rows[0]["notes"] or "").split(",")
        history = _lane_files(result, "staff-sdet")["history"].read_text()
        assert len(re.findall(r"^\d{4}-\d\d-\d\d churn commit \d$", history, re.M)) == 3

    def test_a_commit_subject_over_the_byte_cap_leaves_the_row_over_cap_with_no_history_body(self, tmp_path):
        """Both reads exceed the cap, so there is no commit list to show either: the row says over-cap and holds no range."""
        tmp_path.mkdir(parents=True, exist_ok=True)
        local, _bare = _make_repo_with_remote(tmp_path)
        _write(local, "long.txt", _numbered_lines("long", 10))
        message_file = tmp_path / "long-subject.txt"
        message_file.write_text("subject-over-cap-canary " + "s" * (_lib_constant("HISTORY_BYTES_PER_PATH") + 100) + "\n")
        _git_out(local, "add", "-A")
        _git_out(local, "commit", "-q", "-F", str(message_file))
        _git_out(local, "push", "-q", "origin", "main")
        (local / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
        _write(local, "long.txt", _numbered_lines("long", 10) + "staged\n")
        _git_out(local, "add", "-A")
        env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(local, env, "--staged", list_only=True))[0]

        result = _run_context(local, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 0, result.stderr
        _header, rows = _parse_index(_lane_files(result, "staff-sdet")["index"])
        assert rows[0]["history"] == "-"
        assert "over-cap" in (rows[0]["notes"] or "").split(",") and "commits-only" not in (rows[0]["notes"] or "").split(",")
        assert _lane_files(result, "staff-sdet")["history"].read_bytes() == b""
        assert re.search(r"^CONTEXT_DEGRADED: [1-9]\d*$", result.stderr, re.M)

    def test_log_show_signature_never_runs_gpg_and_the_body_equals_a_clean_configs(self, tmp_path):
        """A commit object with an armored gpgsig header (written without a keyring) makes `git log` call gpg.program under
        log.showSignature=true; the history call passes --no-show-signature, so the recording shim never runs."""
        repo = _history_repo(tmp_path, 2)
        header, _, message = _git_out(repo, "cat-file", "commit", "HEAD").stdout.partition(b"\n\n")
        signed = (
            header + b"\ngpgsig -----BEGIN PGP SIGNATURE-----\n \n iQEzBAABCAAdFiEEnotarealsignatureatall\n"
            b" -----END PGP SIGNATURE-----\n\n" + message
        )
        signed_id = subprocess.run(
            ["git", "hash-object", "-t", "commit", "-w", "--stdin"], cwd=repo, input=signed, capture_output=True, check=True
        ).stdout.decode().strip()
        _git_out(repo, "update-ref", "refs/heads/main", signed_id)
        env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]
        clean = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))
        clean_history = _lane_files(clean, "staff-sdet")["history"].read_bytes()
        gpg_calls = tmp_path / "gpg-calls.log"
        gpg_calls.write_text("")
        gpg_shim = tmp_path / "gpg-shim.sh"
        gpg_shim.write_text(f"#!/bin/sh\nprintf 'ran\\n' >> {shlex.quote(str(gpg_calls))}\nexit 1\n")
        gpg_shim.chmod(0o755)
        _git_out(repo, "config", "log.showSignature", "true")
        _git_out(repo, "config", "gpg.program", str(gpg_shim))
        _git_out(repo, "log", "-1", "--format=%s", "HEAD")
        assert gpg_calls.read_text() != "", "the fixture engages: a plain log under the setting runs the shim"
        gpg_calls.write_text("")

        hostile = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert hostile.returncode == 0, hostile.stderr
        assert gpg_calls.read_text() == ""
        assert _lane_files(hostile, "staff-sdet")["history"].read_bytes() == clean_history
        assert b"gpgsig" not in clean_history


# --- --expect-diff -----------------------------------------------------------------------------------------------------------


def _default_mode_stdout(repo: Path, env: dict) -> bytes:
    process = subprocess.run([str(_SCRIPT)], cwd=repo, env=env, capture_output=True, check=True)
    return process.stdout


class TestExpectDiff:
    def test_a_matching_handed_file_runs_both_calls(self, tmp_path):
        repo = _small_repo(tmp_path, "cumulative")
        env = _context_env(tmp_path)
        handed = tmp_path / "handed.diff"
        handed.write_bytes(_default_mode_stdout(repo, env))

        listing_id, _count, rows = _parse_listing(_run_context(repo, env, "--expect-diff", str(handed), list_only=True))
        result = _run_context(repo, env, "--expect-diff", str(handed), listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 0, result.stderr
        header, _rows = _parse_index(_lane_files(result, "staff-sdet")["index"])
        assert header["mode"] == "cumulative" and header["head"] != "-"

    @pytest.mark.parametrize(
        ("setting_kind", "key", "value"),
        [
            pytest.param("env", "GIT_DIFF_OPTS", "--unified=5", id="exported-git-diff-opts"),
            pytest.param("config", "diff.noprefix", "true", id="diff-noprefix"),
            pytest.param("config", "diff.context", "0", id="diff-context-zero"),
            pytest.param("config", "diff.renames", "false", id="diff-renames-false"),
            pytest.param("config", "color.ui", "always", id="color-ui-always"),
            pytest.param("config", "diff.external", "{script}", id="diff-external"),
        ],
    )
    def test_a_handed_file_written_under_a_porcelain_diff_setting_does_not_mismatch(self, tmp_path, setting_kind, key, value):
        """A setting that reached only default mode or only the recompute would print a false DIFF_MISMATCH. Both run the command
        `_cumulative_diff_argv` in pr-diff-against-base.sh builds."""
        repo = _small_repo(tmp_path, "cumulative")
        recording_script = tmp_path / "external-diff-recording.sh"
        ran_log = tmp_path / "external-diff-ran.log"
        ran_log.write_text("")
        recording_script.write_text(f"#!/bin/sh\nprintf 'ran\\n' >> {shlex.quote(str(ran_log))}\n")
        recording_script.chmod(0o755)
        env = _context_env(tmp_path)
        plain_diff_args = ("diff", "origin/main...HEAD", "--")
        plain_diff_before = _git_out(repo, *plain_diff_args, env=env).stdout
        handed_before_setting = _default_mode_stdout(repo, env)

        if setting_kind == "env":
            env = _context_env(tmp_path, **{key: value})
        else:
            _git_out(repo, "config", key, value.format(script=recording_script))
        plain_diff_after = _git_out(repo, *plain_diff_args, env=env).stdout
        assert plain_diff_after != plain_diff_before, "control: the setting changes a plain git diff"
        if key == "diff.external":
            assert ran_log.read_text() != "", "control: a plain git diff runs the recording script"
            ran_log.write_text("")

        handed = tmp_path / "handed.diff"
        handed.write_bytes(_default_mode_stdout(repo, env))
        assert handed.read_bytes() == handed_before_setting, "the setting does not reach default mode's diff"
        listing_id, _count, _rows = _parse_listing(_run_context(repo, env, "--expect-diff", str(handed), list_only=True))
        result = _run_context(repo, env, "--expect-diff", str(handed), listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 0, result.stderr
        assert "DIFF_MISMATCH:" not in result.stderr
        if key == "diff.external":
            assert ran_log.read_text() == "", "no step of the round ran the external diff"

    def test_the_recompute_issues_the_same_diff_tree_argv_as_default_mode(self, tmp_path):
        """Default mode wrote the handed file, so a recompute that issues any other diff-tree argv prints a false
        DIFF_MISMATCH."""
        repo = _small_repo(tmp_path, "cumulative")
        shim = GitShim(tmp_path / "git_shim")
        env = shim.env(_context_env(tmp_path))
        handed = tmp_path / "handed.diff"
        handed.write_bytes(_default_mode_stdout(repo, env))

        result = _run_context(repo, env, "--expect-diff", str(handed), list_only=True)

        assert result.returncode == 0, result.stderr
        diff_tree_argvs = [call["argv"] for call in shim.calls() if call["subcommand"] == "diff-tree"]
        assert len(diff_tree_argvs) == 2, diff_tree_argvs
        assert diff_tree_argvs[0] == diff_tree_argvs[1]

    def test_a_handed_file_that_differs_by_one_byte_prints_diff_mismatch_in_both_calls(self, tmp_path):
        repo = _small_repo(tmp_path, "cumulative")
        env = _context_env(tmp_path)
        handed = tmp_path / "handed.diff"
        handed.write_bytes(_default_mode_stdout(repo, env).replace(b"three", b"thre3", 1))

        listed = _run_context(repo, env, "--expect-diff", str(handed), list_only=True)
        laned = _run_context(repo, env, "--expect-diff", str(handed), listing="a" * 64, lanes=("staff-sdet",))

        for result in (listed, laned):
            assert result.returncode == 1
            assert " failed at expect-diff (exit 1): DIFF_MISMATCH: " in result.stderr
            assert "re-run /ready-for-review step 3" in result.stderr
        assert not (repo / "agent-reviews").exists()

    def test_a_non_empty_handed_file_against_an_empty_diff_prints_diff_mismatch(self, tmp_path):
        repo = _empty_repo(tmp_path, "expect-diff")
        env = _context_env(tmp_path)
        handed = tmp_path / "handed.diff"
        handed.write_text("diff --git a/x b/x\n")

        result = _run_context(repo, env, "--expect-diff", str(handed), list_only=True)

        assert result.returncode == 1
        assert " failed at expect-diff (exit 1): DIFF_MISMATCH: " in result.stderr

    def test_an_unreadable_handed_file_halts_at_expect_diff(self, tmp_path):
        repo = _small_repo(tmp_path, "cumulative")
        env = _context_env(tmp_path)

        result = _run_context(repo, env, "--expect-diff", str(tmp_path / "missing.diff"), list_only=True)

        _assert_halted_at(result, "expect-diff", 1, "could not read the handed diff file")

    def test_a_relative_handed_path_is_resolved_before_the_script_changes_directory(self, tmp_path):
        repo = _small_repo(tmp_path, "cumulative")
        (repo / "sub").mkdir()
        env = _context_env(tmp_path)
        (repo / "sub" / "handed.diff").write_bytes(_default_mode_stdout(repo, env))

        result = _run_context(repo / "sub", env, "--expect-diff", "handed.diff", list_only=True)

        assert result.returncode == 0, result.stderr

    def test_expect_diff_agrees_with_default_mode_over_nul_fixture(self, tmp_path):
        """A text diff holding a NUL after byte 8,000 (before and after it a block): command substitution drops the NUL, so a
        handed file written from default mode's capture must still compare equal, and one byte off must not."""
        tmp_path.mkdir(parents=True, exist_ok=True)
        local, _bare = _make_repo_with_remote(tmp_path)
        _write(local, "before.txt", "before\n")
        _write(local, "nul_late.txt", _numbered_lines("nul-late", 1700))
        _write(local, "zafter.txt", "after\n")
        _commit_all(local, "nul base")
        _git_out(local, "push", "-q", "origin", "main")
        (local / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
        _git_out(local, "checkout", "-q", "-b", "feature")
        _write(local, "before.txt", "before changed\n")
        with (local / "nul_late.txt").open("ab") as handle:
            handle.write(b"tail\x00more\n")
        _write(local, "zafter.txt", "after changed\n")
        _commit_all(local, "nul change")
        env = _context_env(tmp_path)
        diff_text = _default_mode_stdout(local, env)
        assert b"\x00" not in diff_text, "default mode's capture dropped the NUL"
        handed = tmp_path / "handed.diff"
        handed.write_bytes(diff_text)

        listing_id, _count, _rows = _parse_listing(_run_context(local, env, "--expect-diff", str(handed), list_only=True))
        result = _run_context(local, env, "--expect-diff", str(handed), listing=listing_id, lanes=("staff-sdet",))
        handed.write_bytes(diff_text.replace(b"after changed", b"after changeD"))
        off_by_one = _run_context(local, env, "--expect-diff", str(handed), list_only=True)

        assert result.returncode == 0, result.stderr
        assert off_by_one.returncode == 1 and "DIFF_MISMATCH" in off_by_one.stderr

    def test_diff_mismatch_wins_over_a_stale_listing_id(self, tmp_path):
        repo = _small_repo(tmp_path, "cumulative")
        env = _context_env(tmp_path)
        handed = tmp_path / "handed.diff"
        handed.write_text("stale handed file\n")

        result = _run_context(repo, env, "--expect-diff", str(handed), listing="b" * 64, lanes=("staff-sdet",))

        assert result.returncode == 1 and "DIFF_MISMATCH" in result.stderr

    def test_a_failing_recompute_exits_1_at_expect_diff_never_as_a_mismatch(self, tmp_path):
        repo = _small_repo(tmp_path, "cumulative")
        base_env = _context_env(tmp_path)
        handed = tmp_path / "handed.diff"
        handed.write_bytes(_default_mode_stdout(repo, base_env))
        shim = GitShim(tmp_path / "git_shim")
        shim.add_rule(subcommand="diff-tree", mode="fail", status=128)

        result = _run_context(repo, shim.env(base_env), "--expect-diff", str(handed), list_only=True)

        _assert_halted_at(result, "expect-diff", 128)


# --- output hygiene and the trap ---------------------------------------------------------------------------------------------


class TestOutputHygieneAndTrap:
    def test_a_warning_on_every_git_diff_call_leaves_only_token_lines_in_an_expect_diff_round(self, tmp_path):
        """The diff call sites (raw, numstat, shared, diff-tree recompute) redirect their own stderr, so a warning never leaks."""
        repo = _small_repo(tmp_path, "cumulative")
        base_env = _context_env(tmp_path)
        handed = tmp_path / "handed.diff"
        handed.write_bytes(_default_mode_stdout(repo, base_env))
        shim = GitShim(tmp_path / "git_shim")
        shim.add_rule(subcommand="diff", mode="warn", stderr="warning: noisy diff\n")
        shim.add_rule(subcommand="diff-tree", mode="warn", stderr="warning: noisy diff\n")
        env = shim.env(base_env)
        listing_id = _parse_listing(_run_context(repo, env, "--expect-diff", str(handed), list_only=True))[0]

        lanes = ("staff-sdet", "staff-backend-engineer=1")
        result = _run_context(repo, env, "--expect-diff", str(handed), listing=listing_id, lanes=lanes)

        assert result.returncode == 0, result.stderr
        assert "noisy" not in result.stderr
        assert sum(1 for call in shim.calls() if call["subcommand"] == "diff") >= 4
        assert any(call["subcommand"] == "diff-tree" for call in shim.calls()), "the recompute's call reached the shim"

    def test_a_warning_on_every_git_log_call_leaves_only_token_lines(self, tmp_path):
        repo = _history_repo(tmp_path, 2)
        shim = GitShim(tmp_path / "git_shim").add_rule(subcommand="log", mode="warn", stderr="warning: noisy log\n")
        env = shim.env(_context_env(tmp_path))
        listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 0, result.stderr
        assert "noisy" not in result.stderr
        assert any(call["subcommand"] == "log" for call in shim.calls()), "the warning shim saw a log call"

    def test_a_failing_rm_in_the_trap_neither_changes_the_status_nor_skips_later_temps(self, tmp_path):
        """The trap's first rm fails (exit 5, the second rm of the run; the first drops the shared diff) while a move fails with
        128: the call still exits 1 showing (exit 128), and the private temp directory and later temps are removed anyway."""
        repo = _small_repo(tmp_path)
        env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]
        mv_counter, rm_counter = tmp_path / "mv-count", tmp_path / "rm-count"
        mv_counter.write_text("")
        rm_counter.write_text("")
        _tool_shim(
            tmp_path / "mv_shim", "mv",
            extra=_fail_nth_call(mv_counter, 5, 128),
        )
        _tool_shim(
            tmp_path / "rm_shim", "rm",
            extra=_fail_nth_call(rm_counter, 2, 5),
        )
        shimmed = {**env, "PATH": f"{tmp_path / 'mv_shim'}{os.pathsep}{tmp_path / 'rm_shim'}{os.pathsep}{env['PATH']}"}

        result = _run_context(repo, shimmed, "--staged", listing=listing_id, lanes=("staff-sdet", "staff-backend-engineer"))

        _assert_halted_at(result, "write", 128)
        assert list(Path(shimmed["TMPDIR"]).iterdir()) == []
        assert len(_agent_reviews_listing(repo)) <= 1, "only the file whose own removal failed may remain"


# --- base resolution, both modes ------------------------------------------------------------------------------------------------


class TestBaseResolutionBothModes:
    def test_default_mode_with_a_failing_gh_prints_the_defaulting_line_byte_for_byte(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/base-line")
        _git_out(local, "checkout", "-q", "feat/base-line")

        result = _run_script(local, _env_with_gh_shim(tmp_path, None))

        assert result.returncode == 0
        assert result.stderr == "pr-diff-against-base.sh: gh pr view failed; defaulting base to main\n"

    def test_default_mode_with_no_resolvable_default_branch_prints_the_resolution_failure_line(self, tmp_path):
        local = tmp_path / "no-remote"
        _init_repo(local)
        _commit(local, "init")

        result = _run_script(local, _env_with_gh_shim(tmp_path, None))

        assert result.returncode == 1
        assert result.stderr == "pr-diff-against-base.sh: gh pr view failed and no default branch resolved from origin\n"

    @pytest.mark.timing
    def test_default_mode_with_a_slow_gh_falls_to_the_guess_and_prints_what_a_failing_gh_prints(self, tmp_path):
        """The gh call runs under a 15 s cap; the repo's scaled timeout shim fires it in a third of that, and the
        test does not wait out 15 s."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/slow-gh")
        _git_out(local, "checkout", "-q", "feat/slow-gh")
        (tmp_path / "failing").mkdir()
        failing = _run_script(local, _env_with_gh_shim(tmp_path / "failing", None))
        bin_dir = tmp_path / "scaled_timeout"
        bin_dir.mkdir()
        if not write_scaled_timeout_shim(bin_dir):
            pytest.skip("no timeout(1) or gtimeout(1) on PATH")
        slow_dir = tmp_path / "slow_gh"
        slow_dir.mkdir()
        (slow_dir / "gh").write_text(f"#!/bin/bash\nsleep {scaled_shim_sleep(15) + 1}\nexit 0\n")
        (slow_dir / "gh").chmod(0o755)
        env = {**os.environ, "PATH": f"{slow_dir}{os.pathsep}{bin_dir}{os.pathsep}{os.environ['PATH']}"}

        with assert_cap_engaged(bin_dir, production_cap=15, command="gh"):
            slow = _run_script(local, env)

        assert slow.returncode == 0, slow.stderr
        assert slow.stdout == failing.stdout
        assert slow.stderr == failing.stderr

    def test_context_mode_with_a_failing_gh_exits_0_and_only_the_lane_call_prints_base_guessed(self, tmp_path):
        repo = _small_repo(tmp_path, "cumulative")
        env = _context_env(tmp_path, base_ref=None)

        listed = _run_context(repo, env, list_only=True)
        listing_id = _parse_listing(listed)[0]
        laned = _run_context(repo, env, listing=listing_id, lanes=("staff-sdet",))

        assert "BASE_GUESSED" not in listed.stderr
        assert "BASE_GUESSED: main\n" in laned.stderr
        assert "defaulting base to" not in listed.stderr + laned.stderr

    def test_a_gh_answering_once_then_failing_stales_the_listing_when_the_pr_base_differs_and_one_relist_recovers(self, tmp_path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "staging")
        _git_out(local, "checkout", "-q", "staging")
        _git_out(local, "checkout", "-q", "-b", "feat/stacked")
        _commit(local, "stacked work")
        (local / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
        counter = tmp_path / "gh-count"
        counter.write_text("")
        gh_dir = tmp_path / "flapping_gh"
        gh_dir.mkdir()
        (gh_dir / "gh").write_text(
            f'#!/bin/bash\nprintf x >> {shlex.quote(str(counter))}\n'
            f'if [ "$(wc -c < {shlex.quote(str(counter))})" -eq 1 ]; then echo staging; exit 0; fi\nexit 1\n'
        )
        (gh_dir / "gh").chmod(0o755)
        env = _context_env(tmp_path)
        env["PATH"] = f"{gh_dir}{os.pathsep}{env['PATH']}"

        first_id = _parse_listing(_run_context(local, env, list_only=True))[0]
        stale = _run_context(local, env, listing=first_id, lanes=("staff-sdet",))
        second_id = _parse_listing(_run_context(local, env, list_only=True))[0]
        recovered = _run_context(local, env, listing=second_id, lanes=("staff-sdet",))

        assert stale.returncode == 2 and stale.stderr.startswith("LISTING_STALE: ")
        assert recovered.returncode == 0, recovered.stderr
        assert "BASE_GUESSED: main\n" in recovered.stderr


# --- the worst-case time ------------------------------------------------------------------------------------------------


def _lib_constant(name: str) -> int:
    process = subprocess.run(
        ["bash", "-c", f'. "$1"; printf %s "${name}"', "bash", str(_LIB_PR_DIFF_SH)], capture_output=True, text=True, check=True
    )
    return int(process.stdout)


def _worst_case_calls(source: str, call: str) -> list[tuple[int, int]]:
    """One entry per capped call a source makes, as (seconds, how many). The guess's five 5 s git calls and gh's 15 s cap are
    the worst case of `gh pr view` failing; enrichment is its budget plus one call that starts just inside it."""
    git_quick, git_call, diff_call = 5, _lib_constant("CONTEXT_GIT_CALL_SECONDS"), _lib_constant("CONTEXT_DIFF_CALL_SECONDS")
    calls = [(git_quick, 2), (diff_call, 1)]  # _lib_repo_root and rev-parse; the raw diff
    if source == "working-tree":
        calls.append((diff_call, 1))  # git ls-files --others
    if source in ("cumulative", "expect-diff"):
        calls += [(15, 1), (git_quick, 5), (git_call, 1)]  # gh pr view; the default-branch guess; git merge-base
    if source == "expect-diff":
        calls.append((diff_call, 1))  # the recompute
    if call == "lanes":
        calls += [(diff_call, 2), (git_quick, 4)]  # numstat and the shared diff; one check-ignore per lane-file kind
        calls += [(_lib_constant("CONTEXT_ENRICHMENT_SECONDS"), 1), (git_call, 1)]
    return calls


def test_worst_case_call_time_under_bash_timeout():
    """The caps are the script's only time bound, and the Bash tool's 600 s maximum bounds only the caller's wait: each
    source's worst-case call list, with every call at its cap and a 2 s kill grace on each capped call, plus 60 s for the
    uncapped local work this test cannot measure, stays under 600 s. `timeout: 600000` is the form the repo already passes."""
    bash_tool_maximum_seconds, margin_seconds, kill_grace_seconds = 600, 60, 2
    for source in ("staged", "working-tree", "cumulative", "expect-diff"):
        for call in ("list", "lanes"):
            calls = _worst_case_calls(source, call)
            worst = sum(seconds * count for seconds, count in calls) + kill_grace_seconds * sum(count for _s, count in calls)
            assert worst < bash_tool_maximum_seconds - margin_seconds, (source, call, worst)


@pytest.mark.timing
def test_the_time_sum_accounts_for_every_capped_call_a_real_run_makes(tmp_path):
    """Ties the table above to the script: a happy-path run's capped calls fit inside the call list, per cap."""
    bin_dir = tmp_path / "scaled_timeout"
    bin_dir.mkdir()
    if not write_scaled_timeout_shim(bin_dir):
        pytest.skip("no timeout(1) or gtimeout(1) on PATH")
    started_log = bin_dir / "scaled-timeout-markers" / "started"
    for source in ("staged", "working-tree", "cumulative", "expect-diff"):
        work = tmp_path / source
        repo, source_args = _repo_for_failure(work, source)
        env = _context_env(work, extra_path=(bin_dir,))
        started_log.write_text("")
        listing_id = _parse_listing(_run_context(repo, env, *source_args, list_only=True))[0]
        list_entries = started_log.read_text().split("\n")[:-1]
        started_log.write_text("")

        lane_result = _run_context(repo, env, *source_args, listing=listing_id, lanes=("staff-sdet",))
        assert lane_result.returncode == 0, (source, lane_result.stderr)
        lane_entries = started_log.read_text().split("\n")[:-1]

        for call, entries in (("list", list_entries), ("lanes", lane_entries)):
            allowed: dict[int, int] = {}
            for seconds, count in _worst_case_calls(source, call):
                allowed[seconds] = allowed.get(seconds, 0) + count
            observed: dict[int, int] = {}
            for entry in entries:
                seconds = int(entry.split(" ")[0])
                observed[seconds] = observed.get(seconds, 0) + 1
            # In the lanes call the 20 s bucket mixes git merge-base with the enrichment calls, which the table bounds by their
            # shared 120 s budget. The list call runs no enrichment, so its 20 s bucket is merge-base alone and is counted.
            for seconds, count in observed.items():
                if call == "list" or seconds != _lib_constant("CONTEXT_GIT_CALL_SECONDS"):
                    assert count <= allowed.get(seconds, 0), (source, call, seconds, count, allowed)


# --- the spawn count ----------------------------------------------------------------------------------------------------

_SPY_TOOLS = (
    "bash", "timeout", "git", "mktemp", "grep", "head", "tail", "wc", "cat", "mv", "rm", "mkdir", "sha256sum", "dirname", "tr",
)


def _spy_path(directory: Path, log: Path, tools=_SPY_TOOLS) -> str:
    """A PATH holding only counting shims written in shell, each exec'ing the real tool by absolute path, so any other external
    (a per-path basename, sort or stat) fails loudly with command-not-found and never goes uncounted."""
    directory.mkdir(parents=True, exist_ok=True)
    for tool in tools:
        real = shutil.which(tool)
        assert real is not None, tool
        shim = directory / tool
        shim.write_text(f'#!/bin/bash\nprintf "%s\\n" {tool} >> {shlex.quote(str(log))}\nexec {shlex.quote(real)} "$@"\n')
        shim.chmod(0o755)
    return str(directory)


def _build_spawn_repo(root: Path, repetitions: int, *, untracked: bool = False, extra_path: str | None = None) -> Path:
    """A staged repository holding `repetitions` copies of every path kind, plus one withheld credential-named path.

    `untracked` adds that many untracked files, and `extra_path` stages one more new path."""
    root.mkdir(parents=True, exist_ok=True)
    local, _bare = _make_repo_with_remote(root)
    for rep in range(repetitions):
        _write(local, f"a_ren{rep}_old.txt", _numbered_lines(f"ren{rep}", 20))
        _write(local, f"b_type{rep}.txt", _numbered_lines(f"type{rep}", 4))
        (local / f"c_bin{rep}.dat").write_bytes(b"bin\x00base\n")
        _write(local, f"d_mode{rep}.sh", _numbered_lines(f"mode{rep}", 3))
        os.symlink("a_target", local / f"e_sym{rep}")
        _write(local, f"f_noeol{rep}.txt", "no newline at the end")
        _write(local, f"g_ctrl{rep}\x01.txt", _numbered_lines(f"ctrl{rep}", 3))
        _write(local, f'"h_quote{rep}.txt', _numbered_lines(f"quote{rep}", 3))
        _write(local, f"ssh{rep}/{_SSH_DIR}/config", _numbered_lines(f"cfg{rep}", 2))
        _write(local, f"ssh{rep}/{_SSH_DIR}/known_hosts", _numbered_lines(f"kh{rep}", 2))
    _write(local, f"keys/{_RSA_KEY}", "withheld base\n")
    _commit_all(local, "spawn base")
    _git_out(local, "push", "-q", "origin", "main")
    (local / ".git" / "info" / "exclude").write_text("agent-reviews/\n")
    for rep in range(repetitions):
        _git_out(local, "mv", f"a_ren{rep}_old.txt", f"a_ren{rep}_new.txt")
        _write(local, f"a_ren{rep}_new.txt", _numbered_lines(f"ren{rep}", 20).replace("line 0003", "line three"))
        (local / f"b_type{rep}.txt").unlink()
        os.symlink("a_target", local / f"b_type{rep}.txt")
        (local / f"c_bin{rep}.dat").write_bytes(b"bin\x00changed\n")
        (local / f"d_mode{rep}.sh").chmod(0o755)
        _write(local, f"f_noeol{rep}.txt", "no newline at the end, edited")
        _write(local, f"g_ctrl{rep}\x01.txt", _numbered_lines(f"ctrl{rep}", 3) + "more\n")
        _write(local, f'"h_quote{rep}.txt', _numbered_lines(f"quote{rep}", 3) + "more\n")
        _write(local, f"ssh{rep}/{_SSH_DIR}/config", _numbered_lines(f"cfg{rep}", 2) + "more\n")
        _write(local, f"ssh{rep}/{_SSH_DIR}/known_hosts", _numbered_lines(f"kh{rep}", 2) + "more\n")
        _write(local, f"new{rep}.txt", "added\n")
    _write(local, f"keys/{_RSA_KEY}", "withheld changed\n")
    if extra_path is not None:
        _write(local, extra_path, "extra changed\n")
    _git_out(local, "add", "-A", "-f")
    if untracked:
        for rep in range(repetitions):
            _write(local, f"untracked{rep}.txt", "untracked\n")
    return local


def _exec_counts(repo: Path, tmp_path: Path, lanes: tuple[str, ...], source_arg: str = "--staged") -> dict[str, int]:
    """Per-tool exec counts of a two-call round under the counting PATH."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "execs.log"
    log.write_text("")
    env = _context_env(tmp_path)
    env["PATH"] = _spy_path(tmp_path / "spy", log)
    listing_id = _parse_listing(_run_context(repo, env, source_arg, list_only=True))[0]
    log.write_text("")
    result = _run_context(repo, env, source_arg, listing=listing_id, lanes=lanes)
    assert result.returncode == 0, result.stderr
    counts: dict[str, int] = {}
    for tool in log.read_text().split():
        counts[tool] = counts.get(tool, 0) + 1
    return counts


class TestSpawnCount:
    _LANES = ("comment-discipline-reviewer:diff", "staff-backend-engineer=1,2,3")

    @pytest.mark.parametrize("source", ["staged", "working-tree"])
    def test_spawn_count_constant_in_changed_path_count(self, tmp_path, source):
        """The number of processes executed does not grow with the changed paths for a fixed lane set: only the SSH predicate's
        per-candidate grep scales, by exactly one per added candidate under the SSH directory. The working-tree source puts the
        untracked loop in the count."""
        untracked = source == "working-tree"
        source_arg = f"--{source}"
        small_repo = _build_spawn_repo(tmp_path / "small", 5, untracked=untracked)
        large_repo = _build_spawn_repo(tmp_path / "large", 50, untracked=untracked)
        small = _exec_counts(small_repo, tmp_path / "small-run", self._LANES, source_arg)
        large = _exec_counts(large_repo, tmp_path / "large-run", self._LANES, source_arg)

        for tool in ("git", "timeout", "mktemp", "mv"):
            assert small.get(tool, 0) >= 1, f"{tool} never ran, so equal counts would prove nothing"
        added_candidates = 2 * (50 - 5)
        assert large.get("grep", 0) - small.get("grep", 0) == added_candidates
        for tool in sorted((set(small) | set(large)) - {"grep"}):
            assert small.get(tool, 0) == large.get(tool, 0), (tool, small, large)

    @pytest.mark.parametrize(
        ("extra_path", "grep_bound"),
        [(f"extra/{_DSA_KEY}", 0), ("extra/.env.ci", 0), (f"extra/{_SSH_DIR}/deploy_key", 3 * (2 * 50 + 1))],
        ids=["credential-regex", "dotenv-only", "ssh-directory-only"],
    )
    def test_a_predicate_hit_adds_only_its_own_resolution(self, tmp_path, extra_path, grep_bound):
        """One more withheld path of each predicate half: the regex and dotenv passes add no process, the .ssh half resolves
        each .ssh path on its own (bounded by their count), and the extra withheld row splits the diff-only lane's run once."""
        without = _exec_counts(_build_spawn_repo(tmp_path / "plain", 50), tmp_path / "plain-run", self._LANES)
        with_hit = _exec_counts(_build_spawn_repo(tmp_path / "hit", 50, extra_path=extra_path), tmp_path / "hit-run", self._LANES)

        assert with_hit.get("grep", 0) - without.get("grep", 0) <= grep_bound
        assert with_hit.get("head", 0) - without.get("head", 0) == 1 == with_hit.get("tail", 0) - without.get("tail", 0)
        for tool in sorted((set(without) | set(with_hit)) - {"grep", "head", "tail"}):
            assert without.get(tool, 0) == with_hit.get(tool, 0), (tool, without, with_hit)

    def test_whole_diff_lane_scales_only_git_timeout_and_the_staging_tools_with_its_paths(self, tmp_path):
        """A whole-diff lane enriches every path: git, timeout and the staging tools (mktemp, cat, mv) legitimately grow with
        it (head joins them: each history read is capped by one), and nothing else but the SSH predicate's grep and the temp
        removal does."""
        small = _exec_counts(_build_spawn_repo(tmp_path / "small", 5), tmp_path / "small-run", ("staff-sdet",))
        large = _exec_counts(_build_spawn_repo(tmp_path / "large", 20), tmp_path / "large-run", ("staff-sdet",))

        grown = {tool for tool in set(small) | set(large) if large.get(tool, 0) != small.get(tool, 0)}
        assert grown <= {"git", "timeout", "mktemp", "cat", "mv", "grep", "rm", "head"}, (small, large)

    def test_each_enrichment_subcommand_runs_at_most_once_per_lane_path(self, tmp_path):
        repo = _build_spawn_repo(tmp_path / "repo", 4)
        shim = GitShim(tmp_path / "git_shim")
        env = shim.env(_context_env(tmp_path))
        listing_id, count, _rows = _parse_listing(_run_context(repo, env, "--staged", list_only=True))
        shim.clear_log()

        _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet", "ciso-reviewer"))

        calls = [call["argv"] for call in shim.calls()]
        assert sum(1 for argv in calls if "log" in argv[:6]) <= count
        assert sum(1 for argv in calls if "cat-file" in argv and "blob" in argv) <= count
        assert sum(1 for argv in calls if "cat-file" in argv and "-s" in argv) <= count

    def test_one_shared_diff_call_however_many_lanes(self, tmp_path):
        repo = _build_spawn_repo(tmp_path / "repo", 3)
        shim = GitShim(tmp_path / "git_shim")
        env = shim.env(_context_env(tmp_path))
        listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]
        shim.clear_log()

        lanes = ("staff-sdet", "ciso-reviewer", "staff-backend-engineer=1,2", "comment-discipline-reviewer:diff")
        _run_context(repo, env, "--staged", listing=listing_id, lanes=lanes)

        assert sum(1 for call in shim.calls() if call["subcommand"] == "diff" and "--full-index" in call["argv"]) == 1

    def test_the_script_forks_no_shell_per_changed_path(self, tmp_path):
        """A PATH spy sees an exec and not a forked builtin, so this counts the distinct shells the script runs: a DEBUG trap,
        installed for the script alone through BASH_ENV, logs the BASHPID of every command. The mode switches xtrace off, so
        `bash -x` cannot do this."""
        pids = []
        for repetitions, label in ((5, "small"), (50, "large")):
            work = tmp_path / label
            repo = _build_spawn_repo(work, repetitions)
            env = _context_env(work)
            listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]
            pid_log = work / "pids.log"
            prelude = work / "pid-prelude.sh"
            prelude.write_text(
                'case $0 in\n  *pr-diff-against-base.sh)\n    set -T\n'
                "    trap 'printf \"%s\\n\" \"$BASHPID\" >> \"$PR_DIFF_PID_LOG\"' DEBUG\n    ;;\nesac\n"
            )
            result = subprocess.run(
                ["bash", str(_SCRIPT), "--staged", "--context-files", _SUFFIX, "--listing", listing_id, "--", *self._LANES],
                cwd=repo, env={**env, "BASH_ENV": str(prelude), "PR_DIFF_PID_LOG": str(pid_log)},
                capture_output=True, text=True, check=False,
            )
            assert result.returncode == 0, result.stderr
            stderr_lines = result.stderr.split("\n")[:-1]
            assert all(_is_token_line(line) for line in stderr_lines)
            pids.append(set(pid_log.read_text().split()))

        assert len(pids[0]) > 1
        assert len(pids[0]) == len(pids[1])


# --- the halts section and the failing-call coverage -------------------------------------------------------------------


def _step_names() -> list[str]:
    process = subprocess.run(
        ["bash", "-c", '. "$1"; printf "%s\\n" "${PR_DIFF_STEP_NAMES[@]}"', "bash", str(_LIB_PR_DIFF_SH)],
        capture_output=True, text=True, check=True,
    )
    return process.stdout.split()


def _halts_section() -> str:
    text = _SCRIPTS_DOC.read_text()
    start = text.index(f"## {CONTEXT_FILES_HALT_HEADING}")
    following = text.find("\n## ", start + 1)
    return text[start:] if following == -1 else text[start:following]


class TestScriptsDocHaltsSection:
    def test_the_section_opens_by_telling_an_agent_to_stop_and_run_no_recovery_step(self):
        section = _halts_section()

        assert section.startswith(
            f"## {CONTEXT_FILES_HALT_HEADING}\n\n"
            "An agent that reaches this section stops and runs no recovery step; the engineer follows it"
        )

    def test_every_halt_pointer_the_script_prints_names_an_existing_heading(self):
        assert f"\n## {CONTEXT_FILES_HALT_HEADING}\n" in _SCRIPTS_DOC.read_text()

    def test_the_section_holds_a_remedy_entry_for_each_step_name(self):
        section = _halts_section()

        steps_without_entry = [step for step in _step_names() if f"\n- **`{step}`** — " not in section]

        assert steps_without_entry == []

    @pytest.mark.parametrize(
        "token",
        [
            "DIFF_MISMATCH:", "CONTEXT_NOT_IGNORED:", "LISTING_STALE:", "A `LANE_ROW:` mismatch", "A hook denial of either call",
            "A Bash-tool timeout", "A usage error",
            "A refused permission prompt", "An unreconciled count",
            "A reviewer whose return says it was handed no readable input",
            "A denied `Read` of a lane file",
        ],
    )
    def test_the_section_holds_an_entry_for_each_non_step_halt(self, token):
        section = _halts_section()

        assert re.search(rf"^- \*\*`?{re.escape(token)}", section, re.M), token

    def test_the_kill_entry_names_the_private_directory_template_and_the_lane_temp_shape(self):
        section = _halts_section()

        assert "`pr-diff-context.*`" in section and "`agent-reviews/.<stem>.<kind>.*`" in section

    def test_the_entry_cross_references_gate_deadlock_recovery_without_restating_it(self):
        assert 'docs/hooks.md` § "Gate deadlock recovery"' in _halts_section()


class TestEveryCallHasAFailingCase:
    def test_every_step_name_has_a_failing_case_in_this_file(self):
        """A step added to the closed list without a case here fails the suite: the matrix rows, plus the three steps whose
        cases are dedicated tests (base resolution, the status check and the output directory)."""
        covered = {row[3] for row in _CORE_FAILURES} | {"base-resolution", "status-check", "output-dir"}

        assert covered == set(_step_names())

    @pytest.mark.parametrize("source", ["staged", "working-tree", "cumulative", "expect-diff"])
    def test_every_git_subcommand_a_run_makes_has_a_failing_case(self, tmp_path, source):
        """One unfailed run per source logs its git subcommands; each must be one a failing case injects (or one the degraded
        body reads and the default-branch guess cover with their own tests)."""
        repo, source_args = _repo_for_failure(tmp_path, source)
        shim = GitShim(tmp_path / "git_shim")
        env = shim.env(_context_env(tmp_path, base_ref=None))
        listing_id = _parse_listing(_run_context(repo, env, *source_args, list_only=True))[0]
        _run_context(repo, env, *source_args, listing=listing_id, lanes=("staff-sdet",))

        observed = {call["subcommand"] for call in shim.calls()}

        injected = {row[1][1]["subcommand"] for row in _CORE_FAILURES if row[1][0] == "git"}
        degrading = {"cat-file", "log"}
        guessing = {"symbolic-ref"}
        assert observed <= injected | degrading | guessing, observed - (injected | degrading | guessing)

    def test_every_step_name_a_failing_case_prints_is_in_the_closed_list(self):
        assert {row[3] for row in _CORE_FAILURES} <= set(_step_names())


# --- remaining wiring cases ---------------------------------------------------------------------------------------------


class TestRemainingWiring:
    def test_duplicate_numbers_in_one_lane_collapse_to_one_echo_and_one_marked_row(self, tmp_path):
        repo = _small_repo(tmp_path)
        env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet=3,3",))

        assert result.returncode == 0, result.stderr
        echoed = [line for line in result.stderr.split("\n") if line.startswith("LANE_ROW: ")]
        assert echoed == ["LANE_ROW: staff-sdet 3 three.txt"]
        _header, rows = _parse_index(_lane_files(result, "staff-sdet")["index"])
        assert [row["lane"] for row in rows] == ["-", "-", "lane"]

    def test_an_in_range_wrong_number_yields_a_different_lane_and_the_echo_names_the_row_actually_used(self, tmp_path):
        """Characterization: the comparison is one-directional, from the echo to the intended path, so only the caller can
        notice a wrong in-range number."""
        repo = _small_repo(tmp_path)
        env = _context_env(tmp_path)
        listing_id, _count, rows = _parse_listing(_run_context(repo, env, "--staged", list_only=True))
        intended = next(row for row in rows if row["path"] == "one.txt")["n"]

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=(f"staff-sdet={intended % len(rows) + 1}",))

        echoed = next(line for line in result.stderr.split("\n") if line.startswith("LANE_ROW: "))
        assert not echoed.endswith(" one.txt")

    def test_the_private_directory_is_mode_0700_named_by_the_template_and_holds_the_core_and_shared_temps(
        self, tmp_path, spawn_blocked_run
    ):
        """Inspected while the shared diff call blocks, the one moment every temp exists."""
        repo = _small_repo(tmp_path)
        env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]
        with ambient_umask(0o022):
            process, _started, release = spawn_blocked_run(repo, env, listing_id, tmp_path)

        (private_dir,) = list(Path(env["TMPDIR"]).iterdir())
        names = sorted(path.name.rsplit(".", 1)[0] for path in private_dir.iterdir())
        modes = {stat.S_IMODE(path.stat().st_mode) for path in private_dir.iterdir()}
        directory_mode = stat.S_IMODE(private_dir.stat().st_mode)
        release.write_text("go")
        process.communicate(timeout=60)

        assert re.fullmatch(r"pr-diff-context\.[A-Za-z0-9]{6}", private_dir.name)
        assert directory_mode == 0o700
        assert names == ["numstat", "raw", "shared", "untracked"]
        assert modes == {0o600}
        assert process.returncode == 0

    def test_a_withheld_row_gets_no_blob_read_and_no_log_walk(self, tmp_path):
        repo = _canary_repo(tmp_path, "staged")
        shim = GitShim(tmp_path / "git_shim")
        env = shim.env(_context_env(tmp_path))
        listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]
        shim.clear_log()
        withheld_blobs = {
            _git_out(repo, "rev-parse", f"HEAD:{name}").stdout.decode().strip() for name in (_CREDENTIAL_NAME, f"keys/{_RSA_KEY}")
        }
        control_blob = _git_out(repo, "rev-parse", "HEAD:notes.txt").stdout.decode().strip()

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet", "ciso-reviewer"))

        assert result.returncode == 0, result.stderr
        calls = [call["argv"] for call in shim.calls()]

        def reads_a_blob_of(blobs: set[str]) -> bool:
            return any("cat-file" in argv and blobs & set(argv) for argv in calls)

        def walks_history_of(paths: tuple[str, ...]) -> bool:
            return any("log" in argv[:8] and "--" in argv and argv[argv.index("--") + 1] in paths for argv in calls)

        assert reads_a_blob_of({control_blob}), "the shim recorded no blob read for the control row"
        assert walks_history_of(("notes.txt",)), "the shim recorded no history walk for the control row"
        assert not reads_a_blob_of(withheld_blobs)
        assert not walks_history_of((_CREDENTIAL_NAME, f"keys/{_RSA_KEY}"))

    def test_no_history_body_is_copied_into_a_lane_temp_before_the_history_withheld_check_finishes(self, tmp_path):
        repo = _history_repo(tmp_path, 2)
        base_env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(repo, base_env, "--staged", list_only=True))[0]
        order = tmp_path / "order.log"
        order.write_text("")
        quoted = shlex.quote(str(order))
        _tool_shim(tmp_path / "grep_shim", "grep", extra=f'case " $* " in *" -H "*) printf "history-check\\n" >> {quoted};; esac')
        _tool_shim(
            tmp_path / "cat_shim", "cat", extra=f'case " $* " in */history/hist.*) printf "history-copy\\n" >> {quoted};; esac'
        )
        env = {**base_env, "PATH": f"{tmp_path / 'grep_shim'}{os.pathsep}{tmp_path / 'cat_shim'}{os.pathsep}{base_env['PATH']}"}

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 0, result.stderr
        assert order.read_text().split() == ["history-check", "history-copy"]

    def test_an_untracked_nested_repository_is_one_trailing_slash_entry_with_an_untracked_preamble_line(self, tmp_path):
        repo = _small_repo(tmp_path)
        _init_nested_repo(repo / "nested_repo", "nested\n")
        env = _context_env(tmp_path)
        listing_id, _count, rows = _parse_listing(_run_context(repo, env, "--working-tree", list_only=True))

        result = _run_context(repo, env, "--working-tree", listing=listing_id, lanes=("staff-sdet",))

        nested = [row for row in rows if row["path"].startswith("nested_repo")]
        assert [(row["status"], row["path"]) for row in nested] == [("?", "nested_repo/")]
        assert "# omitted untracked: nested_repo/\n" in _lane_files(result, "staff-sdet")["diff"].read_text()

    def test_a_content_line_that_is_invalid_utf_8_passes_through_under_a_utf_8_locale(self, tmp_path):
        """Bytes that are not valid UTF-8 reach the lane diff unchanged under a UTF-8 locale."""
        utf8_locale = installed_locale_where(
            '[ "$(locale charmap)" = UTF-8 ]', "", absent_message="no UTF-8 locale installed"
        )

        def add_invalid(repo: Path) -> None:
            (repo / "invalid.txt").write_bytes(b"valid line\nbad \xff\xfe byte line\nlast line\n")

        repo = _small_repo(tmp_path, extra_changes=add_invalid)
        env = _context_env(tmp_path, LC_ALL=utf8_locale)
        listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))

        assert result.returncode == 0, result.stderr
        assert b"+bad \xff\xfe byte line\n" in _lane_files(result, "staff-sdet")["diff"].read_bytes()

    def test_no_tail_c_plus_n_appears_in_the_mode(self):
        """`tail -c +N | head -c M` exits 141 under pipefail once more than a pipe buffer follows the slice."""
        for source in (_SCRIPT, _LIB_PR_DIFF_SH):
            assert not re.search(r"tail\s+-c\s*\+", source.read_text()), source

    def test_the_leak_check_fails_on_a_new_lane_temp_and_on_a_new_private_directory(self, tmp_path):
        """Pins that the check every run goes through can fail, by creating what it looks for after its before-snapshot."""
        repo = _small_repo(tmp_path)
        env = _context_env(tmp_path)
        (repo / "agent-reviews").mkdir()
        private_before, dots_before = _private_dirs(env), _dot_entries(repo)

        (repo / "agent-reviews" / ".lane-temp.abc123").write_text("x")
        with pytest.raises(AssertionError, match="a lane temp leaked"):
            _assert_no_leaked_temps(repo, env, private_before, dots_before)
        (repo / "agent-reviews" / ".lane-temp.abc123").unlink()
        (Path(env["TMPDIR"]) / "pr-diff-context.AbC123").mkdir()
        with pytest.raises(AssertionError, match="a private temp directory leaked"):
            _assert_no_leaked_temps(repo, env, private_before, dots_before)


# --- cases the test matrix names ----------------------------------------------------------------------------------------


class TestNamedCases:
    def test_exit_hygiene_token_lines_only(self, tmp_path):
        """A happy round prints only token lines on stderr and nothing on stdout, and the helper every run goes through
        rejects a polluted result, so a new case cannot skip the check."""
        repo = _small_repo(tmp_path)
        env = _context_env(tmp_path)
        listing_id = _parse_listing(_run_context(repo, env, "--staged", list_only=True))[0]

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet", "staff-backend-engineer=1"))

        assert result.stdout == ""
        assert all(_is_token_line(line) for line in result.stderr.split("\n")[:-1])
        polluted_stdout = subprocess.CompletedProcess([], 0, stdout="stray\n", stderr=result.stderr)
        polluted_stderr = subprocess.CompletedProcess([], 0, stdout="", stderr=result.stderr + "warning: noise\n")
        unpointed_halt = subprocess.CompletedProcess([], 1, stdout="", stderr="pr-diff-against-base.sh: failed\n")
        for bad in (polluted_stdout, polluted_stderr, unpointed_halt):
            with pytest.raises(AssertionError):
                _assert_exit_hygiene(bad, lane_agents=("staff-sdet", "staff-backend-engineer"))

    def test_ordinal_out_of_range_exits_2(self, tmp_path):
        repo = _small_repo(tmp_path)
        env = _context_env(tmp_path)
        listing_id, count, _rows = _parse_listing(_run_context(repo, env, "--staged", list_only=True))

        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=(f"staff-sdet={count + 1}",), hygiene=False)

        assert result.returncode == 2
        assert f"names number {count + 1}, outside 1..{count}" in result.stderr
        assert not (repo / "agent-reviews").exists()

    def test_a_path_only_a_user_glob_matches_is_withheld_in_the_listing_and_absent_from_every_lane_file(self, tmp_path):
        """The opt-in credential-file-guard.md is applied to <root>/<path>, so the lane files hold none of a matched path."""
        def add_vault_file(repo: Path) -> None:
            _write(repo, "vault/token.dat", "vault-glob-canary diff\n")

        repo = _small_repo(tmp_path, extra_changes=add_vault_file)
        env = _context_env(tmp_path)
        (Path(env["CLAUDE_CONFIG_DIR"]) / "credential-file-guard.md").write_text(f"{repo}/vault/*\n")
        listing_id, _count, rows = _parse_listing(_run_context(repo, env, "--staged", list_only=True))

        lanes = ("staff-sdet", "comment-discipline-reviewer:diff", "ciso-reviewer")
        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=lanes)

        assert {row["path"]: row["withheld"] for row in rows}["vault/token.dat"] == "withheld"
        assert b"vault-glob-canary" not in _every_byte_a_call_left(repo, (result,))

    def test_a_guard_glob_does_not_match_the_relative_name(self, tmp_path):
        """A glob written against the relative name never matches: the reviewer's Read presents the absolute path."""
        def add_vault_file(repo: Path) -> None:
            _write(repo, "vault/token.dat", "relative-glob-canary\n")

        repo = _small_repo(tmp_path, extra_changes=add_vault_file)
        env = _context_env(tmp_path)
        (Path(env["CLAUDE_CONFIG_DIR"]) / "credential-file-guard.md").write_text("vault/*\n")

        listing_id, _count, rows = _parse_listing(_run_context(repo, env, "--staged", list_only=True))

        assert {row["path"]: row["withheld"] for row in rows}["vault/token.dat"] == "-"
        result = _run_context(repo, env, "--staged", listing=listing_id, lanes=("staff-sdet",))
        assert b"relative-glob-canary" in _lane_files(result, "staff-sdet")["diff"].read_bytes()
