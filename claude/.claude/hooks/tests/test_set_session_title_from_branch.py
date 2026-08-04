"""Tests for set-session-title-from-branch.sh.

The hook is a SessionStart hook (matcher: startup) that sets the terminal
tab title via hookSpecificOutput.sessionTitle to `<repo>/<branch>`, derived
from the payload's `.cwd` (not process cwd). Emits nothing (today's
auto-titler runs unchanged) on the default branch, when the default branch
is undeterminable, on a bare main worktree, or when either title component
fails the `^[A-Za-z0-9._/@+-]+$` allowlist matched under LC_ALL=C.

Every test passes `home=` so kill-switch cases read an isolated $HOME
rather than the real one — without it, a machine with the real sentinel
present would turn the whole file vacuously green.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, build_path_without, run_hook_session_start

SET_SESSION_TITLE_HOOK = HOOKS_DIR / "set-session-title-from-branch.sh"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def _git_q(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _run_raw(input_bytes: bytes, home: Path, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Invoke the hook with raw (not JSON-encoded) stdin — for the empty-
    stdin and non-JSON-stdin cases run_hook_session_start's json.dumps
    can't express."""
    env = {**os.environ, "HOME": str(home)}
    return subprocess.run(
        [str(SET_SESSION_TITLE_HOOK)],
        input=input_bytes,
        capture_output=True,
        cwd=cwd,
        env=env,
        check=False,
    )


# ---------- fixtures ----------------------------------------------------


@pytest.fixture
def isolated_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return home


@pytest.fixture
def bare_remote(tmp_path):
    """Bare repo to act as `origin` with a single commit on `main`."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git_q(seed, "init", "-q", "-b", "main")
    _git_q(seed, "config", "user.email", "t@t.com")
    _git_q(seed, "config", "user.name", "t")
    (seed / "f").write_text("a\n")
    _git_q(seed, "add", "f")
    _git_q(seed, "commit", "-qm", "init")
    _git_q(seed, "remote", "add", "origin", str(bare))
    _git_q(seed, "push", "-q", "origin", "main")
    return bare


@pytest.fixture
def feature_clone(tmp_path, bare_remote):
    """Clone of `bare_remote` checked out on a feature branch with
    origin/HEAD properly set."""
    repo = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", str(bare_remote), str(repo)], check=True, capture_output=True
    )
    _git_q(repo, "config", "user.email", "t@t.com")
    _git_q(repo, "config", "user.name", "t")
    _git_q(repo, "remote", "set-head", "origin", "main")
    _git_q(repo, "checkout", "-q", "-b", "feature")
    return repo


def _title(
    repo: Path,
    isolated_home: Path,
    payload_cwd: Path | None = None,
    source: str = "startup",
    extra_env: dict | None = None,
) -> str | None:
    """Run the hook with process cwd `repo` and payload `.cwd` `payload_cwd`
    (defaulting to `repo` when the two don't need to diverge)."""
    return run_hook_session_start(
        SET_SESSION_TITLE_HOOK,
        {"source": source, "cwd": str(payload_cwd if payload_cwd is not None else repo)},
        cwd=repo,
        home=isolated_home,
        extra_env=extra_env,
    )


def _set_head_to_raw_branch(repo: Path, branch_bytes: bytes) -> None:
    """Point HEAD at a branch name carrying raw bytes, without creating a
    ref object with that name as a filename — `git symbolic-ref HEAD
    refs/heads/<bytes>` only rewrites HEAD's own (normally-named) file
    content, so it works even for bytes a filesystem would reject as a
    literal filename (e.g. macOS/APFS rejects invalid-UTF-8 filenames but
    accepts them inside a text file's content)."""
    branch_ref = b"refs/heads/" + branch_bytes
    result = subprocess.run(
        ["git", "symbolic-ref", b"HEAD", branch_ref], cwd=repo, capture_output=True
    )
    assert result.returncode == 0, result.stderr


class TestSetSessionTitleFromBranch:
    # ---------- title composition -----------------------------------

    def test_feature_branch_emits_repo_slash_branch(self, feature_clone, isolated_home):
        assert _title(feature_clone, isolated_home) == "clone/feature"

    def test_default_branch_no_title(self, feature_clone, isolated_home):
        _git_q(feature_clone, "checkout", "-q", "main")
        assert _title(feature_clone, isolated_home) is None

    def test_no_origin_remote_no_title(self, tmp_path, isolated_home):
        repo = tmp_path / "no-origin"
        repo.mkdir()
        _git_q(repo, "init", "-q", "-b", "main")
        _git_q(repo, "config", "user.email", "t@t.com")
        _git_q(repo, "config", "user.name", "t")
        (repo / "f").write_text("a\n")
        _git_q(repo, "add", "f")
        _git_q(repo, "commit", "-qm", "init")
        _git_q(repo, "checkout", "-q", "-b", "feature")
        assert _title(repo, isolated_home) is None

    def test_origin_head_unset_no_title(self, feature_clone, isolated_home):
        _git_q(feature_clone, "remote", "set-head", "--delete", "origin")
        assert _title(feature_clone, isolated_home) is None

    def test_stale_origin_head_emits_known_wrong_title(self, tmp_path, bare_remote, isolated_home):
        """origin/HEAD renamed away from the actual default (a
        master->main rename with no `set-head -a`) still resolves to a
        real branch, so the hook can't tell it's stale — it emits the
        current branch anyway. Accepted cost, not a defect: asserted as
        the specific known-wrong string, not just "some title"."""
        repo = tmp_path / "clone"
        subprocess.run(["git", "clone", "-q", str(bare_remote), str(repo)], check=True, capture_output=True)
        _git_q(repo, "config", "user.email", "t@t.com")
        _git_q(repo, "config", "user.name", "t")
        _git_q(repo, "checkout", "-q", "-b", "old-main")
        _git_q(repo, "push", "-q", "origin", "old-main")
        _git_q(repo, "remote", "set-head", "origin", "old-main")
        _git_q(repo, "checkout", "-q", "-b", "feature", "main")
        assert _title(repo, isolated_home) == "clone/feature"

    def test_dangling_origin_head_no_title(self, feature_clone, isolated_home):
        """origin/HEAD pointing at a ref that does not exist still
        resolves via `symbolic-ref -q` (it just reads the pointer text) —
        must not be conflated with a live default branch."""
        _git_q(
            feature_clone,
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            "refs/remotes/origin/deleted-branch",
        )
        assert _title(feature_clone, isolated_home) is None

    def test_not_a_git_repo_no_title(self, tmp_path, isolated_home):
        plain = tmp_path / "plain"
        plain.mkdir()
        assert _title(plain, isolated_home) is None

    def test_bare_repo_no_title(self, tmp_path, bare_remote, isolated_home):
        """A bare repo's own HEAD is a real symbolic ref (even with no
        commits), so it must resolve a divergent origin/HEAD to reach the
        worktree-list bare-attribute check that actually skips it — a bare
        repo with no origin remote would exit at the default-branch gate
        instead, for the wrong reason."""
        bare_session = tmp_path / "bare-session.git"
        subprocess.run(
            ["git", "init", "-q", "--bare", "-b", "session-branch", str(bare_session)], check=True
        )
        _git_q(bare_session, "remote", "add", "origin", str(bare_remote))
        _git_q(bare_session, "fetch", "-q", "origin")
        _git_q(bare_session, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        assert _title(bare_session, isolated_home) is None

    def test_separate_git_dir_not_skipped(self, tmp_path, bare_remote, isolated_home):
        """`--separate-git-dir` is not skipped by the bare-attribute guard —
        its first porcelain record carries no `bare` attribute. On git
        2.55.0, that record's `worktree` path is the relocated
        git-dir, not the working directory: `git worktree list --porcelain`
        has no administrative record of the main worktree's own path when
        it's not colocated with the git-dir, so it falls back to the
        git-dir's own location. The emitted repo component reflects that."""
        workdir = tmp_path / "sep-workdir"
        gitdir = tmp_path / "sep-gitdir"
        subprocess.run(
            ["git", "init", "-q", "-b", "main", f"--separate-git-dir={gitdir}", str(workdir)],
            check=True,
        )
        _git_q(workdir, "config", "user.email", "t@t.com")
        _git_q(workdir, "config", "user.name", "t")
        (workdir / "f").write_text("a\n")
        _git_q(workdir, "add", "f")
        _git_q(workdir, "commit", "-qm", "init")
        _git_q(workdir, "remote", "add", "origin", str(bare_remote))
        _git_q(workdir, "fetch", "-q", "origin")
        _git_q(workdir, "remote", "set-head", "origin", "main")
        _git_q(workdir, "checkout", "-q", "-b", "feature")
        assert _title(workdir, isolated_home) == "sep-gitdir/feature"

    def test_detached_head_emits_at_short_sha(self, feature_clone, isolated_home):
        head_sha = _git(feature_clone, "rev-parse", "HEAD").strip()
        _git_q(feature_clone, "checkout", "-q", "--detach", head_sha)
        short_sha = _git(feature_clone, "rev-parse", "--short", "HEAD").strip()
        assert _title(feature_clone, isolated_home) == f"clone/@{short_sha}"

    def test_linked_worktree_emits_main_repo_and_worktree_branch(
        self, feature_clone, tmp_path, isolated_home
    ):
        worktree = tmp_path / "clone-wt"
        _git_q(feature_clone, "worktree", "add", "-q", "-b", "wt-feature", str(worktree), "main")
        assert _title(worktree, isolated_home) == "clone/wt-feature"

    def test_submodule_emits_submodules_own_dirname(self, tmp_path, isolated_home):
        """Inside a submodule, `worktree list`'s main worktree is the
        submodule's own checkout — not the superproject's."""
        outer = tmp_path / "outer"
        outer.mkdir()
        _git_q(outer, "init", "-q", "-b", "main")
        _git_q(outer, "config", "user.email", "t@t.com")
        _git_q(outer, "config", "user.name", "t")
        (outer / "f").write_text("a\n")
        _git_q(outer, "add", "f")
        _git_q(outer, "commit", "-qm", "init")

        sub_src = tmp_path / "subsrc"
        sub_src.mkdir()
        _git_q(sub_src, "init", "-q", "-b", "main")
        _git_q(sub_src, "config", "user.email", "t@t.com")
        _git_q(sub_src, "config", "user.name", "t")
        (sub_src / "g").write_text("b\n")
        _git_q(sub_src, "add", "g")
        _git_q(sub_src, "commit", "-qm", "sub init")

        _git_q(
            outer, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub_src), "libs/thing"
        )
        _git_q(outer, "commit", "-qm", "add submodule")
        sub_path = outer / "libs" / "thing"
        _git_q(sub_path, "checkout", "-q", "-b", "feature")
        assert _title(sub_path, isolated_home) == "thing/feature"

    def test_branch_over_32_chars_truncates(self, tmp_path, bare_remote, isolated_home):
        long_branch = "github-actions-comment-durability-standards"
        assert len(long_branch) == 43
        repo = tmp_path / "clone"
        subprocess.run(["git", "clone", "-q", str(bare_remote), str(repo)], check=True, capture_output=True)
        _git_q(repo, "config", "user.email", "t@t.com")
        _git_q(repo, "config", "user.name", "t")
        _git_q(repo, "remote", "set-head", "origin", "main")
        _git_q(repo, "checkout", "-q", "-b", long_branch)
        title = _title(repo, isolated_home)
        assert title == f"clone/{long_branch[:32]}"
        assert len(title.split("/", 1)[1]) == 32

    @pytest.mark.parametrize("length,expect_truncated", [(32, False), (33, True)])
    def test_branch_truncation_boundary_is_exactly_32(
        self, tmp_path, bare_remote, isolated_home, length, expect_truncated
    ):
        """Pins the off-by-one boundary directly: a 32-char branch must pass
        through whole, a 33-char branch must lose exactly its last char."""
        branch = "a" * length
        repo = tmp_path / "clone"
        subprocess.run(["git", "clone", "-q", str(bare_remote), str(repo)], check=True, capture_output=True)
        _git_q(repo, "config", "user.email", "t@t.com")
        _git_q(repo, "config", "user.name", "t")
        _git_q(repo, "remote", "set-head", "origin", "main")
        _git_q(repo, "checkout", "-q", "-b", branch)
        title = _title(repo, isolated_home)
        expected_branch = branch[:32] if expect_truncated else branch
        assert title == f"clone/{expected_branch}"

    def test_payload_cwd_overrides_process_cwd(self, feature_clone, tmp_path, isolated_home):
        """Process cwd is the main checkout; payload `.cwd` names a linked
        worktree on a different branch — the hook must read `.cwd`, not
        process cwd. A hook that read process cwd instead would pass every
        other case in this suite and only fail here."""
        worktree = tmp_path / "clone-wt"
        _git_q(feature_clone, "worktree", "add", "-q", "-b", "wt-feature", str(worktree), "main")
        title = _title(feature_clone, isolated_home, payload_cwd=worktree)
        assert title == "clone/wt-feature"

    def test_allowlist_positive_boundary_every_punctuation_char_accepted(
        self, tmp_path, bare_remote, isolated_home
    ):
        """A branch using every character the allowlist accepts besides
        alnum (including `@`, exercised nowhere else as an allowlist-boundary
        case — only incidentally via the detached-HEAD `@<sha>` title) passes
        through unmodified — pins the class so a future tightening of the
        pattern fails a test instead of silently killing real branches."""
        boundary_branch = "release/v1.2.3+build_x.y-z@tag"
        repo = tmp_path / "clone"
        subprocess.run(["git", "clone", "-q", str(bare_remote), str(repo)], check=True, capture_output=True)
        _git_q(repo, "config", "user.email", "t@t.com")
        _git_q(repo, "config", "user.name", "t")
        _git_q(repo, "remote", "set-head", "origin", "main")
        _git_q(repo, "checkout", "-q", "-b", boundary_branch)
        assert _title(repo, isolated_home) == f"clone/{boundary_branch}"

    # ---------- allowlist rejections: branch component ----------------
    # Each matched under both LC_ALL=C and LC_ALL=en_US.UTF-8 to prove the
    # hook's internal pin, not the caller's locale, decides the outcome.

    @pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
    def test_branch_rejects_raw_c1_0x9b(self, feature_clone, isolated_home, locale):
        _set_head_to_raw_branch(feature_clone, b"\x9bbranch")
        assert _title(feature_clone, isolated_home, extra_env={"LC_ALL": locale}) is None

    @pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
    def test_branch_rejects_raw_c1_0x9c(self, feature_clone, isolated_home, locale):
        _set_head_to_raw_branch(feature_clone, b"\x9cbranch")
        assert _title(feature_clone, isolated_home, extra_env={"LC_ALL": locale}) is None

    @pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
    def test_branch_rejects_utf8_encoded_u009c(self, feature_clone, isolated_home, locale):
        _set_head_to_raw_branch(feature_clone, b"\xc2\x9cbranch")
        assert _title(feature_clone, isolated_home, extra_env={"LC_ALL": locale}) is None

    @pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
    def test_branch_rejects_accented_name(self, tmp_path, bare_remote, isolated_home, locale):
        repo = tmp_path / "clone"
        subprocess.run(["git", "clone", "-q", str(bare_remote), str(repo)], check=True, capture_output=True)
        _git_q(repo, "config", "user.email", "t@t.com")
        _git_q(repo, "config", "user.name", "t")
        _git_q(repo, "remote", "set-head", "origin", "main")
        _git_q(repo, "checkout", "-q", "-b", "café")
        assert _title(repo, isolated_home, extra_env={"LC_ALL": locale}) is None

    # ---------- allowlist rejections: directory component --------------

    @pytest.mark.skipif(
        sys.platform == "darwin",
        reason="APFS refuses to create a directory whose name is not valid "
        "UTF-8 (EILSEQ) — the fixture is unconstructible on macOS; CI runs "
        "Linux, where the byte sequence is a legal filename.",
    )
    def test_directory_rejects_invalid_utf8_basename(self, tmp_path, bare_remote, isolated_home):
        repo = Path(os.fsdecode(os.fsencode(str(tmp_path)) + b"/invalid-\xffdir"))
        subprocess.run(["git", "clone", "-q", str(bare_remote), str(repo)], check=True, capture_output=True)
        _git_q(repo, "config", "user.email", "t@t.com")
        _git_q(repo, "config", "user.name", "t")
        _git_q(repo, "remote", "set-head", "origin", "main")
        _git_q(repo, "checkout", "-q", "-b", "feature")
        assert _title(repo, isolated_home) is None

    @pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
    def test_directory_rejects_non_ascii_basename(self, tmp_path, bare_remote, isolated_home, locale):
        """Dual-locale, matching the branch-side rejections above: proves the
        repo-component grep's LC_ALL=C pin (hook line 141) decides the
        outcome rather than ambient locale — a future edit dropping the pin
        from only this grep (leaving the branch-side one at line 142 intact)
        would otherwise pass every other test in this file."""
        repo = tmp_path / "café-repo"
        subprocess.run(["git", "clone", "-q", str(bare_remote), str(repo)], check=True, capture_output=True)
        _git_q(repo, "config", "user.email", "t@t.com")
        _git_q(repo, "config", "user.name", "t")
        _git_q(repo, "remote", "set-head", "origin", "main")
        _git_q(repo, "checkout", "-q", "-b", "feature")
        assert _title(repo, isolated_home, extra_env={"LC_ALL": locale}) is None

    def test_directory_rejects_basename_with_space(self, tmp_path, bare_remote, isolated_home):
        repo = tmp_path / "My Repo"
        subprocess.run(["git", "clone", "-q", str(bare_remote), str(repo)], check=True, capture_output=True)
        _git_q(repo, "config", "user.email", "t@t.com")
        _git_q(repo, "config", "user.name", "t")
        _git_q(repo, "remote", "set-head", "origin", "main")
        _git_q(repo, "checkout", "-q", "-b", "feature")
        assert _title(repo, isolated_home) is None

    # ---------- .source filter -----------------------------------------

    def test_source_absent_no_title(self, feature_clone, isolated_home):
        title = run_hook_session_start(
            SET_SESSION_TITLE_HOOK,
            {"cwd": str(feature_clone)},
            cwd=feature_clone,
            home=isolated_home,
        )
        assert title is None

    def test_empty_stdin_no_title(self, feature_clone, isolated_home):
        result = _run_raw(b"", isolated_home, cwd=feature_clone)
        assert result.returncode == 0
        assert result.stdout == b""

    def test_non_json_stdin_no_title(self, feature_clone, isolated_home):
        result = _run_raw(b"not json{{{", isolated_home, cwd=feature_clone)
        assert result.returncode == 0
        assert result.stdout == b""

    def test_unknown_future_source_value_no_title(self, feature_clone, isolated_home):
        assert _title(feature_clone, isolated_home, source="some_future_value") is None

    @pytest.mark.parametrize("source", ["resume", "fork", "clear", "compact"])
    def test_non_startup_sources_no_title(self, feature_clone, isolated_home, source):
        assert _title(feature_clone, isolated_home, source=source) is None

    def test_jq_absent_targets_source_filter(self, feature_clone, isolated_home, tmp_path):
        """With jq entirely absent, the .source filter's own jq call fails,
        SOURCE stays empty, and the [[ == "startup" ]] gate exits — with
        only one jq binary on the machine, the emit-path failure (a later
        jq -n call) is not separately constructible, which is itself worth
        recording."""
        farm_dir = tmp_path / "no-jq-path"
        farm_dir.mkdir()
        path_without_jq = build_path_without("jq", farm_dir)
        title = _title(feature_clone, isolated_home, extra_env={"PATH": path_without_jq})
        assert title is None

    # ---------- kill switches -------------------------------------------

    def test_machine_global_kill_switch(self, feature_clone, isolated_home):
        (isolated_home / ".claude").mkdir()
        (isolated_home / ".claude" / ".session-title-disabled").touch()
        assert _title(feature_clone, isolated_home) is None

    def test_per_repo_kill_switch_from_main_tree(self, feature_clone, isolated_home):
        claude_dir = feature_clone / ".claude"
        claude_dir.mkdir()
        (claude_dir / "session-title-disabled").touch()
        assert _title(feature_clone, isolated_home) is None

    def test_per_repo_kill_switch_sentinel_in_main_tree_while_running_from_linked_worktree(
        self, feature_clone, tmp_path, isolated_home
    ):
        """Resolved against the main worktree root, never cwd: the
        sentinel lives in the main checkout, invisible from a linked
        worktree's own directory listing. Asserted on emitted output, not
        a stat count, so a regression here is caught behaviorally."""
        worktree = tmp_path / "clone-wt"
        _git_q(feature_clone, "worktree", "add", "-q", "-b", "wt-feature", str(worktree), "main")
        claude_dir = feature_clone / ".claude"
        claude_dir.mkdir()
        (claude_dir / "session-title-disabled").touch()
        assert _title(worktree, isolated_home) is None

    def test_per_repo_kill_switch_session_launched_from_subdirectory(
        self, feature_clone, isolated_home
    ):
        subdir = feature_clone / "sub" / "dir"
        subdir.mkdir(parents=True)
        claude_dir = feature_clone / ".claude"
        claude_dir.mkdir()
        (claude_dir / "session-title-disabled").touch()
        assert _title(subdir, isolated_home) is None

    # ---------- CLAUDE_CONFIG_DIR resolution -----------------------------

    def test_machine_global_kill_switch_at_config_dir(self, feature_clone, isolated_home, tmp_path):
        """The machine-global kill switch is read from CLAUDE_CONFIG_DIR
        when set, not from $HOME/.claude."""
        config_dir = tmp_path / "profile"
        config_dir.mkdir()
        (config_dir / ".session-title-disabled").touch()
        assert _title(
            feature_clone, isolated_home, extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)}
        ) is None

    def test_machine_global_kill_switch_at_home_does_not_suppress_when_config_dir_set(
        self, feature_clone, isolated_home, tmp_path
    ):
        """The legacy $HOME/.claude kill-switch location is not consulted
        once CLAUDE_CONFIG_DIR is set — the title still fires."""
        (isolated_home / ".claude").mkdir()
        (isolated_home / ".claude" / ".session-title-disabled").touch()
        config_dir = tmp_path / "profile"
        config_dir.mkdir()
        assert _title(
            feature_clone, isolated_home, extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)}
        ) == "clone/feature"
