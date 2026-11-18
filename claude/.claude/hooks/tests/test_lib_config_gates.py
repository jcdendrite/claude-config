"""Unit tests for _lib.sh's config-resolved gates (_lib_config_dir, _lib_worktree_enforcement_active,
_lib_permission_prompt_tracking_active and _lib_round_consult_gate_disabled).
The test_sourcing_lib_sh_fails_when_config_sh_is_missing and test_standard_hook_guard_trips_when_config_sh_is_missing
family also pins that sourcing _lib.sh fails when its sibling _config.sh is missing, so the standard hook source guard trips.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from helpers import (
    AUTONOMOUS_SHIPPING_STATEMENT_LEAD,
    init_git_repo_with_commit,
    run_autonomous_shipping_root_for_cwd,
    run_autonomous_shipping_statement,
)

from ._lib_sh_helpers import _LIB_SH


def _lib_sh_with_unreadable_schema(tmp_path: Path) -> Path:
    """Symlink _lib.sh and the _config.sh it sources into an isolated
    directory with no config-keys.psv sibling, simulating an unreadable
    schema file the way test_config_lib.py's _run_with_schema does for
    _config.sh alone -- _lib.sh's own BASH_SOURCE-relative source of
    _config.sh resolves against the symlink's own directory, so the
    isolated dir's absent config-keys.psv is what _config_schema_field sees."""
    isolated_hooks_dir = tmp_path / "isolated-hooks"
    isolated_hooks_dir.mkdir()
    (isolated_hooks_dir / "_lib.sh").symlink_to(_LIB_SH)
    (isolated_hooks_dir / "_config.sh").symlink_to(_LIB_SH.parent / "_config.sh")
    return isolated_hooks_dir / "_lib.sh"


def _lib_sh_with_missing_config_sh(tmp_path: Path) -> Path:
    """Symlink only _lib.sh into an isolated directory with no _config.sh
    sibling at all, simulating a partial stow-relink or interrupted git pull
    that drops _config.sh out from under an already-present _lib.sh."""
    isolated_hooks_dir = tmp_path / "isolated-hooks-no-config"
    isolated_hooks_dir.mkdir()
    (isolated_hooks_dir / "_lib.sh").symlink_to(_LIB_SH)
    return isolated_hooks_dir / "_lib.sh"


def _run_config_dir(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_config_dir'],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


class TestLibConfigDir:
    def test_returns_claude_config_dir_when_set_absolute(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "custom-config"
        result = _run_config_dir(
            {"HOME": str(tmp_path / "home"), "CLAUDE_CONFIG_DIR": str(config_dir), "PATH": os.environ["PATH"]}
        )
        assert result.returncode == 0
        assert result.stdout.strip() == str(config_dir)

    def test_falls_back_to_home_claude_when_config_dir_unset(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        result = _run_config_dir({"HOME": str(home), "PATH": os.environ["PATH"]})
        assert result.returncode == 0
        assert result.stdout.strip() == str(home / ".claude")

    def test_relative_claude_config_dir_fails_closed(self, tmp_path: Path) -> None:
        result = _run_config_dir(
            {"HOME": str(tmp_path / "home"), "CLAUDE_CONFIG_DIR": "relative/path", "PATH": os.environ["PATH"]}
        )
        assert result.returncode != 0
        assert result.stdout == ""

    def test_empty_string_claude_config_dir_falls_back_to_home(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        result = _run_config_dir(
            {"HOME": str(home), "CLAUDE_CONFIG_DIR": "", "PATH": os.environ["PATH"]}
        )
        assert result.returncode == 0
        assert result.stdout.strip() == str(home / ".claude")

    def test_fails_closed_when_config_dir_and_home_both_unset(self) -> None:
        result = _run_config_dir({"HOME": "", "PATH": os.environ["PATH"]})
        assert result.returncode != 0
        assert result.stdout == ""


# _lib_worktree_enforcement_active — direct unit coverage for the
# machine-sentinel delegation arm, and for the committed-repo-sentinel
# arm's precedence over an unresolvable config dir and over the per-repo
# opt-out. The opt-out's effect on the machine sentinel is left to this
# function's callers' integration tests. worktree_required is the
# highest-blast-radius of the
# four enforcement-critical keys and the only one whose schema row carries
# legacy-probe-on-resolution-failure: true, so a config-dir resolution
# failure still probes the legacy $HOME/.claude location rather than
# falling through to "not enforced" the way every other key's row does.


def _worktree_enforcement_active(repo_root: Path, env: dict) -> bool:
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_worktree_enforcement_active "$1"', "bash", str(repo_root)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return result.returncode == 0


class TestWorktreeEnforcementActive:
    def test_inactive_when_neither_location_has_sentinel(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        config_dir = tmp_path / "profile"
        config_dir.mkdir(parents=True)
        repo = tmp_path / "repo"
        repo.mkdir()
        assert not _worktree_enforcement_active(
            repo, {"HOME": str(home), "CLAUDE_CONFIG_DIR": str(config_dir), "PATH": os.environ["PATH"]}
        )

    def test_active_when_config_dir_absent_sentinel_but_home_claude_has_it(
        self, tmp_path: Path
    ) -> None:
        """Union, not swap: a resolved config dir differentiated from
        $HOME/.claude, holding no sentinel of its own, must not mask a
        sentinel armed at the legacy $HOME/.claude location."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "worktree-required").touch()
        config_dir = tmp_path / "profile"
        config_dir.mkdir(parents=True)
        repo = tmp_path / "repo"
        repo.mkdir()
        assert _worktree_enforcement_active(
            repo, {"HOME": str(home), "CLAUDE_CONFIG_DIR": str(config_dir), "PATH": os.environ["PATH"]}
        )

    def test_active_when_config_dir_unresolvable_but_home_claude_has_sentinel(
        self, tmp_path: Path
    ) -> None:
        """worktree_required's schema row alone carries
        legacy-probe-on-resolution-failure: true, so a relative
        CLAUDE_CONFIG_DIR (resolution failure) still probes the legacy
        $HOME/.claude location instead of falling through to "not
        enforced" -- the opposite of every other config-dir-or-home key's
        row."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "worktree-required").touch()
        repo = tmp_path / "repo"
        repo.mkdir()
        assert _worktree_enforcement_active(
            repo, {"HOME": str(home), "CLAUDE_CONFIG_DIR": "relative/path", "PATH": os.environ["PATH"]}
        )

    def test_inactive_when_config_dir_unresolvable_and_home_empty(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        assert not _worktree_enforcement_active(
            repo, {"HOME": "", "CLAUDE_CONFIG_DIR": "relative/path", "PATH": os.environ["PATH"]}
        )

    def test_active_when_config_dir_unresolvable_and_home_empty_but_repo_has_committed_sentinel(
        self, tmp_path: Path
    ) -> None:
        """With no machine-level location left to read, a committed
        .claude/worktree-required still enforces."""
        repo = tmp_path / "repo"
        (repo / ".claude").mkdir(parents=True)
        (repo / ".claude" / "worktree-required").touch()
        assert _worktree_enforcement_active(
            repo, {"HOME": "", "CLAUDE_CONFIG_DIR": "relative/path", "PATH": os.environ["PATH"]}
        )

    def test_committed_sentinel_not_defeated_by_optout_when_config_dir_unresolvable_and_home_empty(
        self, tmp_path: Path
    ) -> None:
        """The per-repo opt-out has no effect on a committed sentinel, in
        the unresolvable-location case too."""
        repo = tmp_path / "repo"
        (repo / ".claude").mkdir(parents=True)
        (repo / ".claude" / "worktree-required").touch()
        (repo / ".claude" / "worktree-optout").touch()
        assert _worktree_enforcement_active(
            repo, {"HOME": "", "CLAUDE_CONFIG_DIR": "relative/path", "PATH": os.environ["PATH"]}
        )

    def test_active_when_config_keys_psv_unreadable(self, tmp_path: Path) -> None:
        """Cumulative-review finding: an unreadable config-keys.psv must not
        silently disarm worktree enforcement -- worktree_required's own safe
        direction is enforced, the same as the accepted exit-2
        (config-dir-unresolvable) tradeoff above, not "not enforced"."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        repo = tmp_path / "repo"
        repo.mkdir()
        isolated_lib_sh = _lib_sh_with_unreadable_schema(tmp_path)
        result = subprocess.run(
            ["bash", "-c", f'. "{isolated_lib_sh}"; _lib_worktree_enforcement_active "$1"', "bash", str(repo)],
            capture_output=True,
            text=True,
            env={"HOME": str(home), "PATH": os.environ["PATH"]},
            check=False,
        )
        assert result.returncode == 0, f"stderr={result.stderr!r}"

    def test_active_when_config_keys_psv_readable_but_missing_worktree_required_row(
        self, tmp_path: Path
    ) -> None:
        """A config-keys.psv that is readable and non-empty but missing
        worktree_required's own row (the interrupted stow-relink/git-pull
        shape, distinct from the wholly-unreadable case above) must not
        silently disarm worktree enforcement either -- _config_enabled's
        exit 4 for this shape routes to the same stays-armed arm as exit 3.
        Copies the real config-keys.psv with only worktree_required's own
        row removed."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        repo = tmp_path / "repo"
        repo.mkdir()
        isolated_hooks_dir = tmp_path / "isolated-hooks"
        isolated_hooks_dir.mkdir()
        (isolated_hooks_dir / "_lib.sh").symlink_to(_LIB_SH)
        (isolated_hooks_dir / "_config.sh").symlink_to(_LIB_SH.parent / "_config.sh")
        real_schema = (_LIB_SH.parent / "config-keys.psv").read_text().splitlines()
        pruned_schema = [line for line in real_schema if not line.startswith("worktree_required|")]
        (isolated_hooks_dir / "config-keys.psv").write_text("\n".join(pruned_schema) + "\n")
        result = subprocess.run(
            [
                "bash",
                "-c",
                f'. "{isolated_hooks_dir / "_lib.sh"}"; _lib_worktree_enforcement_active "$1"',
                "bash",
                str(repo),
            ],
            capture_output=True,
            text=True,
            env={"HOME": str(home), "PATH": os.environ["PATH"]},
            check=False,
        )
        assert result.returncode == 0, f"stderr={result.stderr!r}"


def test_sourcing_lib_sh_fails_when_config_sh_is_missing(tmp_path: Path) -> None:
    """A missing _config.sh must make _lib.sh's own sourcing fail (non-zero),
    the same as a syntax-broken _lib.sh already does, so the standard
    `if ! . ".../_lib.sh"; then exit 0/deny; fi` guard every hook uses trips
    instead of _lib.sh silently defining every function against a
    _config_*-namespace that no longer exists."""
    isolated_lib_sh = _lib_sh_with_missing_config_sh(tmp_path)
    result = subprocess.run(
        ["bash", "-c", f'. "{isolated_lib_sh}"'],
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"]},
        check=False,
    )
    assert result.returncode != 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"


def test_standard_hook_guard_trips_when_config_sh_is_missing(tmp_path: Path) -> None:
    """The idiom every hook uses (`if ! . ".../_lib.sh"; then exit 0; fi`)
    must actually take its failure branch when _config.sh is missing,
    matching how it already behaves for a syntax-broken _lib.sh."""
    isolated_lib_sh = _lib_sh_with_missing_config_sh(tmp_path)
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'if ! . "{isolated_lib_sh}" 2>/dev/null; then echo GUARD_TRIPPED; exit 0; fi; echo GUARD_NOT_TRIPPED',
        ],
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"]},
        check=False,
    )
    assert result.stdout.strip() == "GUARD_TRIPPED", f"stdout={result.stdout!r} stderr={result.stderr!r}"


# _lib_autonomous_shipping_statement — the one home of the agent-facing text
# that states the shipping state. It takes no argument and prints unconditionally.


class TestAutonomousShippingStatement:
    @staticmethod
    def _isolated_home(tmp_path: Path) -> Path:
        # The statement reads no HOME, config dir or file; `run_autonomous_shipping_statement` only takes a home.
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        return home

    def test_prints_statement_deferring_its_limits_to_claude_md(self, tmp_path: Path) -> None:
        home = self._isolated_home(tmp_path)
        result = run_autonomous_shipping_statement(home)
        assert result.returncode == 0
        for expected_phrase in (
            "Autonomous shipping is active",
            "without asking",
            "outranks",
            "specific hold",
            "even when that hold survives only as summary text",
            "an instruction the engineer gives in this session still wins",
            "however paraphrased or summarized, is such a hold",
            'when a line could be either a generic "not authorized" line or such a hold, ask instead of proceeding',
            "no engineer wait behind it",
            "The scope and limits CLAUDE.md sets for shipping still apply",
        ):
            assert expected_phrase in result.stdout
        assert "autonomous-shipping-active.sh" not in result.stdout
        assert "gh pr merge" not in result.stdout

    def test_statement_opens_with_the_shared_lead_constant(self, tmp_path: Path) -> None:
        """The hook tests search for the constant, so it must track the emitted text."""
        home = self._isolated_home(tmp_path)
        result = run_autonomous_shipping_statement(home)
        assert result.stdout.startswith(AUTONOMOUS_SHIPPING_STATEMENT_LEAD)

    def test_statement_has_no_trailing_newline(self, tmp_path: Path) -> None:
        home = self._isolated_home(tmp_path)
        result = run_autonomous_shipping_statement(home)
        assert result.stdout != ""
        assert not result.stdout.endswith("\n")


# _lib_autonomous_shipping_root_for_cwd — the one eligibility rule the Stop hook
# and the compact hook share. test_autonomous_shipping_grant_boundary.py covers the
# same cases through each hook; these pin the function's own contract.


class TestAutonomousShippingRootForCwd:
    @staticmethod
    def _home_and_repo(tmp_path: Path) -> tuple[Path, Path]:
        home = tmp_path / "home"
        home.mkdir()
        repo = init_git_repo_with_commit(tmp_path / "repo", branch="main")
        return home, repo

    def test_prints_the_repo_root_for_a_subdirectory(self, tmp_path: Path) -> None:
        home, repo = self._home_and_repo(tmp_path)
        subdirectory = repo / "pkg" / "inner"
        subdirectory.mkdir(parents=True)
        result = run_autonomous_shipping_root_for_cwd(home, str(subdirectory))
        assert result.returncode == 0
        assert Path(result.stdout).resolve() == repo.resolve()

    def test_rejects_wrong_argument_count(self, tmp_path: Path) -> None:
        home, repo = self._home_and_repo(tmp_path)
        for args in ((), (str(repo), "extra")):
            result = run_autonomous_shipping_root_for_cwd(home, *args)
            assert result.returncode == 1, args
            assert result.stdout == "", args

    def test_rejects_empty_cwd(self, tmp_path: Path) -> None:
        home, _ = self._home_and_repo(tmp_path)
        result = run_autonomous_shipping_root_for_cwd(home, "")
        assert result.returncode == 1
        assert result.stdout == ""

    def test_rejects_relative_cwd_that_resolves_to_a_repo(self, tmp_path: Path) -> None:
        home, repo = self._home_and_repo(tmp_path)
        result = run_autonomous_shipping_root_for_cwd(home, ".", process_cwd=repo)
        assert result.returncode == 1
        assert result.stdout == ""

    def test_rejects_cwd_outside_a_repo(self, tmp_path: Path) -> None:
        home, _ = self._home_and_repo(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        result = run_autonomous_shipping_root_for_cwd(home, str(outside))
        assert result.returncode == 1
        assert result.stdout == ""

    def test_rejects_unset_and_empty_home(self, tmp_path: Path) -> None:
        _, repo = self._home_and_repo(tmp_path)
        unset_home = run_autonomous_shipping_root_for_cwd(None, str(repo))
        assert unset_home.returncode == 1
        assert unset_home.stdout == ""
        empty_home = run_autonomous_shipping_root_for_cwd("", str(repo))
        assert empty_home.returncode == 1
        assert empty_home.stdout == ""

    def test_rejects_home_naming_a_nonexistent_directory(self, tmp_path: Path) -> None:
        """An unresolvable HOME must not degrade to a walk that ends at / and finds no ancestor to refuse."""
        _, repo = self._home_and_repo(tmp_path)
        result = run_autonomous_shipping_root_for_cwd(tmp_path / "no-such-home", str(repo))
        assert result.returncode == 1
        assert result.stdout == ""

    def test_accepts_any_repo_when_home_is_bare_root(self, tmp_path: Path) -> None:
        """HOME=/ resolves, and the ancestor walk then refuses only a root of / itself.

        The guard therefore accepts every repo for such an account.
        The Stop hook still exits silently there unless CLAUDE_CONFIG_DIR is absolute, because _lib_config_dir fails when HOME is /.
        """
        _, repo = self._home_and_repo(tmp_path)
        result = run_autonomous_shipping_root_for_cwd("/", str(repo))
        assert result.returncode == 0
        assert Path(result.stdout).resolve() == repo.resolve()

    def test_rejects_root_equal_to_home(self, tmp_path: Path) -> None:
        home, _ = self._home_and_repo(tmp_path)
        init_git_repo_with_commit(home, branch="main")
        project = home / "project"
        project.mkdir()
        for cwd in (home, project):
            result = run_autonomous_shipping_root_for_cwd(home, str(cwd))
            assert result.returncode == 1, cwd
            assert result.stdout == "", cwd

    def test_rejects_root_that_is_an_ancestor_of_home(self, tmp_path: Path) -> None:
        home, _ = self._home_and_repo(tmp_path)
        init_git_repo_with_commit(tmp_path, branch="main")
        result = run_autonomous_shipping_root_for_cwd(home, str(home))
        assert result.returncode == 1
        assert result.stdout == ""

    def test_rejects_removed_cwd_instead_of_resolving_the_process_cwd_repo(
        self, tmp_path: Path
    ) -> None:
        home, repo = self._home_and_repo(tmp_path)
        removed_worktree = tmp_path / "removed-worktree"
        removed_worktree.mkdir()
        removed_worktree.rmdir()
        result = run_autonomous_shipping_root_for_cwd(home, str(removed_worktree), process_cwd=repo)
        assert result.returncode == 1
        assert result.stdout == ""

    def test_rejects_root_above_a_symlinked_home_by_resolving_home_physically(
        self, tmp_path: Path
    ) -> None:
        work_tree = init_git_repo_with_commit(tmp_path / "work-tree", branch="main")
        physical_home = work_tree / "home"
        physical_home.mkdir()
        home_link = tmp_path / "home-link"
        home_link.symlink_to(physical_home, target_is_directory=True)
        result = run_autonomous_shipping_root_for_cwd(home_link, str(work_tree))
        assert result.returncode == 1
        assert result.stdout == ""

    def test_accepts_a_repo_strictly_beneath_home(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        repo = init_git_repo_with_commit(home / "projects" / "repo", branch="main")
        result = run_autonomous_shipping_root_for_cwd(home, str(repo))
        assert result.returncode == 0
        assert Path(result.stdout).resolve() == repo.resolve()

    def test_accepts_a_nested_own_repo_beneath_a_home_rooted_work_tree(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        init_git_repo_with_commit(home, branch="main")
        nested_repo = init_git_repo_with_commit(home / "projects" / "repo", branch="main")
        result = run_autonomous_shipping_root_for_cwd(home, str(nested_repo))
        assert result.returncode == 0
        assert Path(result.stdout).resolve() == nested_repo.resolve()

    def test_accepts_a_cwd_that_is_a_symlink_to_a_repo_beneath_home(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        repo = init_git_repo_with_commit(home / "projects" / "repo", branch="main")
        cwd_link = tmp_path / "repo-link"
        cwd_link.symlink_to(repo, target_is_directory=True)
        result = run_autonomous_shipping_root_for_cwd(home, str(cwd_link))
        assert result.returncode == 0
        assert Path(result.stdout).resolve() == repo.resolve()

    def test_accepts_a_root_that_merely_shares_a_name_prefix_with_home(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        sibling_repo = init_git_repo_with_commit(tmp_path / "home-projects", branch="main")
        result = run_autonomous_shipping_root_for_cwd(home, str(sibling_repo))
        assert result.returncode == 0
        assert Path(result.stdout).resolve() == sibling_repo.resolve()


# _lib_permission_prompt_tracking_active — direct unit coverage. The function is
# zero-arity by design (a machine-global sentinel with no repo-scoped axis to
# check against), so there is no repo_root, no optout, and no wrong-arity case
# to pin.


def _permission_prompt_tracking_active(env: dict) -> bool:
    result = subprocess.run(
        ["bash", "-c", f". {_LIB_SH}; _lib_permission_prompt_tracking_active"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return result.returncode == 0


class TestPermissionPromptTrackingActive:
    def test_inactive_when_sentinel_absent(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        assert not _permission_prompt_tracking_active({"HOME": str(home), "PATH": os.environ["PATH"]})

    def test_active_when_sentinel_present(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "track-permission-prompts").touch()
        assert _permission_prompt_tracking_active({"HOME": str(home), "PATH": os.environ["PATH"]})

    def test_uses_config_dir_when_set(self, tmp_path: Path) -> None:
        """CLAUDE_CONFIG_DIR relocates the sentinel lookup away from
        $HOME/.claude — the sentinel at the resolved config dir governs,
        not a hardcoded ~/.claude."""
        home = tmp_path / "home"
        config_dir = tmp_path / "profile"
        config_dir.mkdir(parents=True)
        (config_dir / "track-permission-prompts").touch()
        assert _permission_prompt_tracking_active(
            {"HOME": str(home), "CLAUDE_CONFIG_DIR": str(config_dir), "PATH": os.environ["PATH"]}
        )

    def test_sentinel_at_home_claude_ignored_when_config_dir_points_elsewhere(
        self, tmp_path: Path
    ) -> None:
        """Inverse of the case above: a sentinel sitting at $HOME/.claude
        must not activate tracking once CLAUDE_CONFIG_DIR points elsewhere
        with no sentinel of its own — the resolved dir governs, not a
        union of both locations."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "track-permission-prompts").touch()
        config_dir = tmp_path / "profile"
        config_dir.mkdir(parents=True)
        assert not _permission_prompt_tracking_active(
            {"HOME": str(home), "CLAUDE_CONFIG_DIR": str(config_dir), "PATH": os.environ["PATH"]}
        )

    def test_inactive_when_home_empty(self) -> None:
        assert not _permission_prompt_tracking_active({"HOME": "", "PATH": os.environ["PATH"]})

    def test_inactive_when_home_is_bare_root(self) -> None:
        """Pins the ${HOME%/} normalization specifically: HOME=/ strips to an
        empty home_norm inside _lib_config_dir, so resolution fails
        deterministically rather than depending on ambient root-filesystem
        state."""
        assert not _permission_prompt_tracking_active({"HOME": "/", "PATH": os.environ["PATH"]})


# _lib_round_consult_gate_disabled — direct unit coverage. Delegates to
# _config_enabled's round_consult_gate schema row (presence-disables,
# default true, i.e. armed) but cannot collapse exit codes 1 (disabled) and
# 2 (unresolvable config dir) the way a bare `! _config_enabled ...` would:
# this gate must stay armed (not disabled) on a resolution failure, the
# opposite verdict a naive negation would produce.


def _round_consult_gate_disabled(env: dict) -> bool:
    result = subprocess.run(
        ["bash", "-c", f". {_LIB_SH}; _lib_round_consult_gate_disabled"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return result.returncode == 0


class TestRoundConsultGateDisabled:
    def test_not_disabled_when_sentinel_absent(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        assert not _round_consult_gate_disabled({"HOME": str(home), "PATH": os.environ["PATH"]})

    def test_disabled_when_sentinel_present(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / ".round-consult-gate-disabled").touch()
        assert _round_consult_gate_disabled({"HOME": str(home), "PATH": os.environ["PATH"]})

    def test_not_disabled_when_config_dir_unresolvable(self, tmp_path: Path) -> None:
        """The critical case this function's own case-statement exists for:
        an unresolvable config dir (relative CLAUDE_CONFIG_DIR) must leave
        the gate armed, even with a disable sentinel sitting right at
        $HOME/.claude -- a bare `! _config_enabled round_consult_gate`
        would instead treat _config_enabled's exit code 2 the same as its
        exit code 1 and wrongly report disabled."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / ".round-consult-gate-disabled").touch()
        assert not _round_consult_gate_disabled(
            {"HOME": str(home), "CLAUDE_CONFIG_DIR": "relative/path", "PATH": os.environ["PATH"]}
        )

    def test_not_disabled_when_config_keys_psv_unreadable(self, tmp_path: Path) -> None:
        """Cumulative-review finding: an unreadable config-keys.psv must
        leave the gate armed, the same as the accepted exit-2
        (config-dir-unresolvable) tradeoff above -- round_consult_gate's own
        safe direction is armed, so this must not collapse into (or be
        mistaken for) a legitimately disabled gate."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / ".round-consult-gate-disabled").touch()
        isolated_lib_sh = _lib_sh_with_unreadable_schema(tmp_path)
        result = subprocess.run(
            ["bash", "-c", f'. "{isolated_lib_sh}"; _lib_round_consult_gate_disabled'],
            capture_output=True,
            text=True,
            env={"HOME": str(home), "PATH": os.environ["PATH"]},
            check=False,
        )
        assert result.returncode != 0, "the gate must stay armed (not disabled) when the schema is unreadable"

    def test_not_disabled_when_config_keys_psv_readable_but_missing_round_consult_gate_row(
        self, tmp_path: Path
    ) -> None:
        """A config-keys.psv that is readable and non-empty but missing
        round_consult_gate's own row (the interrupted stow-relink/git-pull
        shape, distinct from the wholly-unreadable case above) must leave
        the gate armed too -- _config_enabled's exit 4 for this shape falls
        into this function's own `*)` catch-all, the same as exit 3. Copies
        the real config-keys.psv with only round_consult_gate's own row
        removed."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / ".round-consult-gate-disabled").touch()
        isolated_hooks_dir = tmp_path / "isolated-hooks"
        isolated_hooks_dir.mkdir()
        (isolated_hooks_dir / "_lib.sh").symlink_to(_LIB_SH)
        (isolated_hooks_dir / "_config.sh").symlink_to(_LIB_SH.parent / "_config.sh")
        real_schema = (_LIB_SH.parent / "config-keys.psv").read_text().splitlines()
        pruned_schema = [line for line in real_schema if not line.startswith("round_consult_gate|")]
        (isolated_hooks_dir / "config-keys.psv").write_text("\n".join(pruned_schema) + "\n")
        result = subprocess.run(
            ["bash", "-c", f'. "{isolated_hooks_dir / "_lib.sh"}"; _lib_round_consult_gate_disabled'],
            capture_output=True,
            text=True,
            env={"HOME": str(home), "PATH": os.environ["PATH"]},
            check=False,
        )
        assert result.returncode != 0, "the gate must stay armed (not disabled) when its schema row is missing"
