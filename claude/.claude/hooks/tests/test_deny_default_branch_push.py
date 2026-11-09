"""Tests for deny-default-branch-push.sh.

Most groups pair a deny with an allow control. The parser's per-key and per-arm record pins live
in test_lib_push_destinations.py, so a group here keeps one gate-level deny per distinct judge
predicate.

A test that only reads repository state uses `shared_scenario`, built once per module. A test that
changes config, HEAD or the remotes builds its own `scenario`, so no change crosses tests. Both run
under an isolated git config.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    SYSTEM_AWK_NAME,
    _make_git_recording_argv,
    _run_git,
    assert_cap_engaged,
    awk_first_on_path_env,
    bare_remote_with_default_branch,
    bash_input,
    isolated_git_config_env,
    path_with_failing_tool_shim,
    read_input,
    run_hook,
    run_hook_reason,
)

from ._lib_sh_helpers import _assert_hook_allows

DENY_DEFAULT_BRANCH_PUSH_HOOK = HOOKS_DIR / "deny-default-branch-push.sh"

# Offered by every unresolvable-destination deny, so the agent has one literal push to fall back to.
LITERAL_PUSH_ADVICE = "git push -u origin HEAD"

# Defined once in the shared scenario. The alias tests use every name; TestNoGitCallWithoutPushFragment also uses `st`.
SHARED_ALIASES = {
    "p": "push",
    "q": "-c push.default=current push",
    "co": "checkout",
    "sync": "!git push origin HEAD:main",
    "pp": "p",
    "st": "status",
}


@dataclass
class Scenario:
    """A bare origin, a clone on the default branch, and a linked worktree on `feature`."""

    origin: Path
    main_checkout: Path
    feature_worktree: Path
    default_branch: str

    def expand(self, command: str) -> str:
        return command.replace("<MAIN>", str(self.main_checkout)).replace(
            "<FEATURE>", str(self.feature_worktree)
        )


def _build_scenario(tmp_path: Path, default_branch: str = "main") -> Scenario:
    origin, clone = bare_remote_with_default_branch(tmp_path, branch=default_branch)
    _run_git(clone, "push", "-q", "origin", "HEAD:refs/heads/feature")
    _run_git(clone, "fetch", "-q", "origin")
    _run_git(clone, "branch", "-q", "feature", "origin/feature")
    feature_worktree = tmp_path / "feature-wt"
    _run_git(clone, "worktree", "add", "-q", str(feature_worktree), "feature")
    return Scenario(origin, clone, feature_worktree, default_branch)


@pytest.fixture(autouse=True)
def isolated_git_environment(tmp_path, monkeypatch):
    """Keeps the developer's global git config out of the hook's push-config reads."""
    for name, value in isolated_git_config_env(tmp_path / "home").items():
        monkeypatch.setenv(name, value)


@pytest.fixture(scope="module")
def shared_scenario(tmp_path_factory) -> Scenario:
    root = tmp_path_factory.mktemp("shared-scenario")
    with pytest.MonkeyPatch.context() as isolated:
        for name, value in isolated_git_config_env(root / "home").items():
            isolated.setenv(name, value)
        built = _build_scenario(root)
        for alias_name, alias_value in SHARED_ALIASES.items():
            _run_git(built.feature_worktree, "config", f"alias.{alias_name}", alias_value)
    return built


@pytest.fixture
def scenario(tmp_path) -> Scenario:
    return _build_scenario(tmp_path)


@pytest.fixture
def trunk_scenario(tmp_path) -> Scenario:
    return _build_scenario(tmp_path, default_branch="trunk")


def _assert_allowed(
    command: str, cwd: Path, extra_env: dict | None = None, process_cwd: Path | None = None
) -> None:
    _assert_hook_allows(
        DENY_DEFAULT_BRANCH_PUSH_HOOK, bash_input(command, cwd=str(cwd)), process_cwd or cwd, extra_env
    )


def _assert_denied(
    command: str,
    cwd: Path,
    *mentions: str,
    extra_env: dict | None = None,
    process_cwd: Path | None = None,
) -> None:
    reason = run_hook_reason(
        DENY_DEFAULT_BRANCH_PUSH_HOOK,
        bash_input(command, cwd=str(cwd)),
        cwd=process_cwd or cwd,
        extra_env=extra_env,
    )
    assert reason is not None, f"expected {command!r} to be denied"
    for mention in mentions:
        assert mention in reason, f"deny for {command!r} does not mention {mention!r}: {reason}"


def _git_call_log_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """Returns the PATH override for a `git` shim that logs each call's argv, and the log file it writes."""
    real_git = shutil.which("git")
    assert real_git is not None
    call_log = tmp_path / "git-calls.log"
    shim_directory = tmp_path / "logging-shim-bin"
    _make_git_recording_argv(shim_directory, call_log)
    extra_env = {
        "PATH": f"{shim_directory}{os.pathsep}{os.environ['PATH']}",
        "REAL_GIT": real_git,
    }
    return extra_env, call_log


def _protected(branch: str, remote: str = "origin") -> str:
    return f"protected branch '{branch}' of remote '{remote}'"


def _unresolved(cause: str) -> str:
    return f"cannot resolve: {cause}"


class TestExplicitDestination:
    @pytest.mark.parametrize(
        ("command", "protected_branch"),
        [
            ("git push origin main", "main"),
            ("git push origin feature main", "main"),
            ("git push origin cd main", "main"),
            ("git push origin master", "master"),
        ],
        ids=["main", "feature-and-main", "branch-named-like-a-directory-change", "master-beside-a-resolved-head"],
    )
    def test_denies_a_push_that_names_a_protected_branch(self, shared_scenario, command, protected_branch):
        _assert_denied(command, shared_scenario.feature_worktree, _protected(protected_branch))

    def test_protects_the_default_branch_named_by_origin_head_only_on_a_named_remote(self, trunk_scenario):
        """A path remote gets no origin/HEAD lookup, so `trunk` is unprotected there while `main` stays protected (header).

        The named-remote `main` deny pins a non-first member of the protected set on a remote whose HEAD names another branch.
        """
        feature_worktree = trunk_scenario.feature_worktree
        _assert_denied("git push origin trunk", feature_worktree, _protected("trunk"))
        _assert_denied("git push origin main", feature_worktree, _protected("main"))
        _assert_allowed("git push ../origin.git trunk", feature_worktree)
        _assert_denied("git push ../origin.git main", feature_worktree, _protected("main", "../origin.git"))

    def test_protects_develop_only_while_origin_head_is_unresolved(self, trunk_scenario):
        feature_worktree = trunk_scenario.feature_worktree
        _assert_allowed("git push origin develop", feature_worktree)

        _run_git(feature_worktree, "remote", "set-head", "origin", "--delete")

        _assert_denied("git push origin develop", feature_worktree, _protected("develop"))
        _assert_allowed("git push origin trunk", feature_worktree)

    @pytest.mark.parametrize(
        "command",
        [
            "git push origin feature",
            "git push origin HEAD:main-fix",
            "git push origin feature/main",
            "git push origin HEAD:refs/for/main",
        ],
    )
    def test_allows_a_push_to_a_branch_that_is_not_protected(self, shared_scenario, command):
        _assert_allowed(command, shared_scenario.feature_worktree)

    def test_denies_a_url_remote_push_to_main_and_allows_a_feature_push(self, shared_scenario):
        _assert_denied(
            "git push git@host:o/r.git feature:main",
            shared_scenario.feature_worktree,
            _protected("main", "git@host:o/r.git"),
        )
        _assert_allowed("git push git@host:o/r.git feature", shared_scenario.feature_worktree)


class TestHeadRelativeDestination:
    @pytest.mark.parametrize("destination", ["HEAD", "@"])
    def test_denies_head_while_on_the_default_branch(self, shared_scenario, destination):
        _assert_denied(
            f"git push origin {destination}", shared_scenario.main_checkout, _protected("main")
        )

    def test_allows_head_while_on_a_feature_branch(self, shared_scenario):
        _assert_allowed("git push -u origin HEAD", shared_scenario.feature_worktree)

    def test_denies_head_on_a_detached_head_and_allows_a_named_destination(self, scenario):
        _run_git(scenario.main_checkout, "checkout", "-q", "--detach")
        _assert_denied("git push origin HEAD", scenario.main_checkout, _unresolved("HEAD is detached"))
        _assert_allowed("git push origin HEAD:feature", scenario.main_checkout)

    def test_denies_a_matching_colon_refspec_and_allows_a_named_branch(self, shared_scenario):
        _assert_denied("git push origin :", shared_scenario.feature_worktree, _unresolved("matching"))
        _assert_allowed("git push origin feature", shared_scenario.feature_worktree)


class TestTagSharingABranchName:
    """`git symbolic-ref --short HEAD` prints `heads/<name>` when a tag shares the branch name."""

    def test_denies_head_on_the_default_branch_when_a_tag_shares_its_name(self, scenario):
        _run_git(scenario.main_checkout, "tag", scenario.default_branch)
        _assert_denied("git push origin HEAD", scenario.main_checkout, _protected("main"))

    def test_denies_a_refspec_less_push_without_an_upstream_when_a_tag_shares_the_default_branch_name(
        self, scenario
    ):
        _run_git(scenario.main_checkout, "tag", scenario.default_branch)
        _run_git(scenario.main_checkout, "branch", "--unset-upstream")
        _assert_denied("git push", scenario.main_checkout, _protected("main"))

    def test_allows_head_on_a_feature_branch_when_a_tag_shares_its_name(self, scenario):
        _run_git(scenario.feature_worktree, "tag", "feature")
        _assert_allowed("git push -u origin HEAD", scenario.feature_worktree)


class TestGlobsAndTags:
    @pytest.mark.parametrize(
        ("command", "cause"),
        [
            ("git push origin refs/heads/*:refs/heads/*", "glob"),
            ("git push --all origin", "all"),
        ],
    )
    def test_denies_a_push_the_gate_cannot_enumerate(self, shared_scenario, command, cause):
        _assert_denied(command, shared_scenario.feature_worktree, _unresolved(cause))

    def test_denies_tags_pushed_beside_the_default_branch(self, shared_scenario):
        _assert_denied(
            "git push --tags origin main", shared_scenario.feature_worktree, _protected("main")
        )

    def test_denies_follow_tags_without_a_refspec_on_the_default_branch(self, shared_scenario):
        _assert_denied("git push --follow-tags", shared_scenario.main_checkout, _protected("main"))

    def test_allows_follow_tags_without_a_refspec_on_a_feature_branch(self, shared_scenario):
        _assert_allowed("git push --follow-tags", shared_scenario.feature_worktree)

    def test_allows_a_tag_name_and_a_tags_only_push(self, shared_scenario):
        _assert_allowed("git push origin v1.0.0", shared_scenario.feature_worktree)
        _assert_allowed("git push --tags", shared_scenario.main_checkout)


class TestDeletion:
    @pytest.mark.parametrize("command", ["git push origin :main", "git push origin --delete main"])
    def test_denies_deleting_the_default_branch(self, shared_scenario, command):
        _assert_denied(command, shared_scenario.feature_worktree, _protected("main"))

    def test_allows_deleting_another_branch(self, shared_scenario):
        _assert_allowed("git push origin --delete old-feature", shared_scenario.feature_worktree)


class TestRefspecLessPush:
    def test_denies_a_bare_push_on_the_default_branch(self, shared_scenario):
        _assert_denied("git push", shared_scenario.main_checkout, _protected("main"))

    def test_denies_a_bare_push_that_resolves_to_the_default_branch_by_upstream_or_by_name(self, scenario):
        # @{push} errors without an upstream, so the gate judges the current branch name.
        _run_git(scenario.main_checkout, "branch", "--unset-upstream")
        _assert_denied("git push", scenario.main_checkout, _protected("main"))
        _run_git(scenario.feature_worktree, "branch", "--set-upstream-to=origin/main", "feature")
        _run_git(scenario.feature_worktree, "config", "push.default", "upstream")
        _assert_denied("git push", scenario.feature_worktree, _protected("main"))
        _run_git(scenario.feature_worktree, "config", "--unset", "push.default")
        _run_git(scenario.feature_worktree, "branch", "--set-upstream-to=origin/feature", "feature")
        _assert_allowed("git push", scenario.feature_worktree)

    def test_allows_a_bare_push_on_a_feature_branch(self, shared_scenario):
        _assert_allowed("git push", shared_scenario.feature_worktree)

    def test_judges_a_slash_bearing_branch_by_its_whole_name(self, trunk_scenario):
        """Guards the `HEAD` and `@{push}` reads keeping the whole branch name, not its last path segment.

        A last-segment read would allow a push from the slash-named default branch `release/2` and deny a push from
        the feature branch `team/main`.
        """
        main_checkout = trunk_scenario.main_checkout
        feature_worktree = trunk_scenario.feature_worktree
        _run_git(main_checkout, "push", "-q", "origin", "HEAD:refs/heads/release/2")
        _run_git(main_checkout, "fetch", "-q", "origin")
        _run_git(main_checkout, "remote", "set-head", "origin", "release/2")
        _run_git(main_checkout, "checkout", "-q", "-B", "release/2", "origin/release/2")
        _run_git(feature_worktree, "checkout", "-q", "-b", "team/main")
        _run_git(feature_worktree, "push", "-q", "-u", "origin", "team/main")

        _assert_denied("git push origin HEAD", main_checkout, _protected("release/2"))
        _assert_denied("git push", main_checkout, _protected("release/2"))
        _assert_allowed("git push origin HEAD", feature_worktree)
        _assert_allowed("git push", feature_worktree)


def _add_fork_remote_with_default_branch_dev(scenario: Scenario, tmp_path: Path) -> Path:
    """Adds remote `fork`, whose HEAD names `dev`, so its protected set differs from origin's. Pushes a `topic` branch too."""
    fork = tmp_path / "fork.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "dev", str(fork)], check=True)
    clone = scenario.main_checkout
    _run_git(clone, "remote", "add", "fork", str(fork))
    _run_git(clone, "push", "-q", "fork", "HEAD:refs/heads/dev", "HEAD:refs/heads/topic")
    _run_git(clone, "fetch", "-q", "fork")
    _run_git(clone, "remote", "set-head", "fork", "dev")
    return fork


class TestSeveralPushFragments:
    def test_denies_a_protected_push_that_follows_an_allowed_push(self, shared_scenario):
        _assert_denied(
            "git push origin feature && git push origin main",
            shared_scenario.feature_worktree,
            _protected("main"),
        )
        _assert_allowed(
            "git push origin feature && git push origin feature:feature-copy",
            shared_scenario.feature_worktree,
        )

    def test_judges_each_push_against_its_own_remote_protected_set(self, scenario, tmp_path):
        """The first push loads origin's protected set; the second must reload fork's, where `dev` is protected."""
        _add_fork_remote_with_default_branch_dev(scenario, tmp_path)
        _assert_denied(
            "git push origin feature && git push fork dev",
            scenario.feature_worktree,
            _protected("dev", "fork"),
        )
        _assert_allowed(
            "git push origin feature && git push fork topic", scenario.feature_worktree
        )


class TestSeveralRepositories:
    def test_judges_each_push_against_its_own_repository_protected_set(self, shared_scenario, trunk_scenario):
        """The first push loads repository A's set (`main`); the second must reload repository B's, where `trunk` is protected."""
        first, second = shared_scenario, trunk_scenario
        _assert_denied(
            f"git -C {first.main_checkout} push origin feature && git -C {second.main_checkout} push origin trunk",
            first.feature_worktree,
            _protected("trunk"),
        )
        # `trunk` is protected only in the second repository, so a set kept from it or unioned with it denies this.
        _assert_allowed(
            f"git -C {second.main_checkout} push origin feature && git -C {first.main_checkout} push origin trunk",
            first.feature_worktree,
        )


class TestSecondPushRemote:
    def test_judges_a_bare_push_against_the_push_default_remote(self, scenario, tmp_path):
        """Under remote.pushDefault=fork, @{push} names fork/<branch>, whose protected set follows fork's HEAD."""
        _add_fork_remote_with_default_branch_dev(scenario, tmp_path)
        clone = scenario.main_checkout
        _run_git(clone, "config", "remote.pushDefault", "fork")
        dev_worktree = tmp_path / "dev-wt"
        topic_worktree = tmp_path / "topic-wt"
        _run_git(clone, "worktree", "add", "-q", "--track", "-b", "dev", str(dev_worktree), "fork/dev")
        _run_git(clone, "worktree", "add", "-q", "--track", "-b", "topic", str(topic_worktree), "fork/topic")

        _assert_denied("git push", dev_worktree, _protected("dev", "fork"))
        _assert_allowed("git push", topic_worktree)

    def test_judges_a_bare_push_against_the_remote_the_command_names_not_the_upstream_remote(
        self, scenario, tmp_path
    ):
        """Branch `dev` tracks origin/dev, where `dev` is unprotected, but `git push fork` lands on fork's default branch."""
        _add_fork_remote_with_default_branch_dev(scenario, tmp_path)
        clone = scenario.main_checkout
        _run_git(clone, "push", "-q", "origin", "HEAD:refs/heads/dev")
        _run_git(clone, "fetch", "-q", "origin")
        dev_worktree = tmp_path / "dev-wt"
        _run_git(clone, "worktree", "add", "-q", "--track", "-b", "dev", str(dev_worktree), "origin/dev")

        _assert_denied("git push fork", dev_worktree, _protected("dev", "fork"))
        _assert_allowed("git push origin", dev_worktree)

    def test_judges_the_current_branch_name_on_the_remote_the_command_names_when_no_push_target_resolves(
        self, scenario, tmp_path
    ):
        """With no upstream `@{push}` fails, so the gate judges branch `dev` on the named remote, not on `origin`."""
        _add_fork_remote_with_default_branch_dev(scenario, tmp_path)
        dev_worktree = tmp_path / "dev-wt"
        _run_git(scenario.main_checkout, "worktree", "add", "-q", "--no-track", "-b", "dev", str(dev_worktree), "HEAD")

        _assert_denied("git push fork", dev_worktree, _protected("dev", "fork"))
        _assert_allowed("git push origin", dev_worktree)


class TestRedirectingConfig:
    def test_denies_a_bare_push_under_push_default_matching_or_a_remote_push_refspec(self, scenario):
        feature_worktree = scenario.feature_worktree
        _run_git(feature_worktree, "config", "push.default", "matching")
        _assert_denied("git push", feature_worktree, "push.default=matching")
        _run_git(feature_worktree, "config", "--unset", "push.default")
        _run_git(feature_worktree, "config", "remote.origin.push", "HEAD:main")
        _assert_denied("git push", feature_worktree, "remote.origin.push")

    def test_denies_a_bare_push_with_a_command_line_config_override(self, shared_scenario):
        _assert_denied(
            "git -c push.default=matching push", shared_scenario.feature_worktree, "'push.default'"
        )

    def test_denies_a_bare_push_with_a_command_line_remote_push_refspec(self, shared_scenario):
        _assert_denied(
            "git -c remote.origin.push=HEAD:main push",
            shared_scenario.feature_worktree,
            "'remote.origin.push'",
        )

    def test_denies_a_bare_push_with_a_command_line_branch_upstream_override(self, shared_scenario):
        _assert_denied(
            "git -c branch.feature.remote=origin -c branch.feature.merge=refs/heads/main push",
            shared_scenario.feature_worktree,
            "'branch.feature.remote'",
        )

    def test_allows_a_command_line_branch_override_beside_an_explicit_refspec(self, shared_scenario):
        _assert_allowed(
            "git -c branch.feature.merge=refs/heads/main push origin feature",
            shared_scenario.feature_worktree,
        )

    def test_allows_a_bare_push_with_a_command_line_config_override_of_an_unrelated_key(self, shared_scenario):
        _assert_allowed("git -c core.editor=true push", shared_scenario.feature_worktree)

    def test_allows_a_config_override_beside_an_explicit_refspec(self, shared_scenario):
        _assert_allowed(
            "git -c push.default=matching push origin feature", shared_scenario.feature_worktree
        )

    @pytest.mark.parametrize(
        "prefix",
        [
            "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=push.default GIT_CONFIG_VALUE_0=matching",
            "GIT_CONFIG_PARAMETERS=\"'push.default=matching'\"",
        ],
        ids=["config-count", "config-parameters"],
    )
    def test_environment_config_prefix_denies_a_bare_push_and_allows_an_explicit_refspec(
        self, shared_scenario, prefix
    ):
        _assert_denied(f"{prefix} git push", shared_scenario.feature_worktree)
        _assert_allowed(f"{prefix} git push origin feature", shared_scenario.feature_worktree)

    def test_allows_the_plan_push_preflight_command_shape(self, shared_scenario):
        """Copies the command plan-push-preflight.py builds with shlex.join: its -c push.pushOption= push names a refspec."""
        commit = _run_git(shared_scenario.feature_worktree, "rev-parse", "HEAD").strip()
        command = (
            "git -c push.pushOption= push --no-follow-tags --no-recurse-submodules "
            f"origin {commit}:refs/heads/feature"
        )
        _assert_allowed(command, shared_scenario.feature_worktree)


class TestGlobalGitConfig:
    """The developer's own global config reaches the hook, so these tests point GIT_CONFIG_GLOBAL at a file they write."""

    @staticmethod
    def _global_config_env(tmp_path: Path, config_text: str) -> dict[str, str]:
        global_config = tmp_path / "global-gitconfig"
        global_config.write_text(config_text)
        return {"GIT_CONFIG_GLOBAL": str(global_config)}

    def test_denies_a_bare_push_under_a_global_push_default_matching(self, shared_scenario, tmp_path):
        extra_env = self._global_config_env(tmp_path, "[push]\n\tdefault = matching\n")
        _assert_denied(
            "git push", shared_scenario.feature_worktree, "push.default=matching", extra_env=extra_env
        )

    def test_expands_a_global_alias_of_push_before_judging(self, shared_scenario, tmp_path):
        extra_env = self._global_config_env(tmp_path, "[alias]\n\tgp = push\n")
        _assert_denied(
            "git gp origin main", shared_scenario.feature_worktree, _protected("main"), extra_env=extra_env
        )
        _assert_allowed("git gp origin feature", shared_scenario.feature_worktree, extra_env=extra_env)

    def test_allows_a_bare_feature_branch_push_under_a_global_push_default_current(
        self, shared_scenario, tmp_path
    ):
        extra_env = self._global_config_env(tmp_path, "[push]\n\tdefault = current\n")
        _assert_allowed("git push", shared_scenario.feature_worktree, extra_env=extra_env)
        _assert_denied("git push", shared_scenario.main_checkout, _protected("main"), extra_env=extra_env)


class TestComputedDestination:
    @pytest.mark.parametrize(
        "command",
        [
            'git push origin "$(git branch --show-current)"',
            "git push origin $(git branch --show-current)",
            "git push origin `git branch --show-current`",
        ],
    )
    def test_denies_a_destination_computed_by_a_substitution(self, shared_scenario, command):
        _assert_denied(command, shared_scenario.feature_worktree, LITERAL_PUSH_ADVICE)

    @pytest.mark.parametrize(
        "refspec",
        ["HEAD:m\\ain", "HEAD:ma{i,i}n"],
        ids=["backslash", "brace-list"],
    )
    def test_denies_a_refspec_the_shell_rewrites_before_git_sees_it(self, shared_scenario, refspec):
        """Bash turns each of these into `HEAD:main`, which the gate would otherwise read as literal text."""
        _assert_denied(
            f"git push origin {refspec}", shared_scenario.feature_worktree, _unresolved("refspec")
        )

    def test_denies_a_remote_the_shell_rewrites_before_git_sees_it(self, shared_scenario):
        _assert_denied("git push or{i,i}gin main", shared_scenario.feature_worktree, _unresolved("remote"))

    @pytest.mark.parametrize(
        "command",
        [
            "git push --force-with-lease --force-if-includes",
            'git push --force-with-lease="feature:$(git rev-parse origin/feature)" \\\n'
            "         --force-if-includes origin feature",
            "git push --force-with-lease origin " + "a" * 40 + ":feature",
        ],
        ids=["lease-and-includes", "explicit-lease-backslash-newline", "recovered-sha-restore"],
    )
    def test_allows_the_sync_recipes_on_a_feature_branch(self, shared_scenario, command):
        """Copies the pushes git-feature-branch-sync/SKILL.md prescribes, with its placeholders filled in."""
        _assert_allowed(command, shared_scenario.feature_worktree)


class TestBackslashNewlineJoin:
    """A backslash-newline pair joins lines in the shell, so a push split across lines reads as one command."""

    @pytest.mark.parametrize(
        ("deny_command", "allow_command"),
        [
            ("git push origin \\\nmain", "git push origin \\\nfeature"),
            ("git -C <MAIN> \\\npush origin HEAD", "git -C <FEATURE> \\\npush origin HEAD"),
        ],
        ids=["refspec-on-the-next-line", "subcommand-on-the-next-line"],
    )
    def test_judges_a_push_split_across_lines_like_the_joined_push(
        self, shared_scenario, deny_command, allow_command
    ):
        _assert_denied(
            shared_scenario.expand(deny_command), shared_scenario.feature_worktree, _protected("main")
        )
        _assert_allowed(shared_scenario.expand(allow_command), shared_scenario.feature_worktree)


class TestBlankLineInTheCommand:
    """A blank line, quoted or not, must not split the command into separately-masked records."""

    @pytest.mark.parametrize(
        "command",
        [
            "# publish\n\ngit push origin main",
            "git status\n\ngit push origin main",
            'git commit -m "title\n\nbody" && git push origin HEAD:main && echo "done"',
        ],
        ids=["push-after-a-comment", "push-after-a-command", "push-chained-after-a-multi-paragraph-message"],
    )
    def test_denies_a_push_after_a_blank_line_on_the_awk_on_path(self, shared_scenario, command):
        """Guards `_lib_mask_shell_quotes` keeping a blank line out of record splitting, on the first awk on PATH.

        An awk that read `RS = "\\0"` as paragraph mode (BSD/macOS awk) fused the paragraph-initial `git` word onto
        the previous paragraph's last word, or inverted quote parity after a quoted blank line, so the push was
        never judged. The masker's unit tests run the same shapes under every installed awk.
        """
        _assert_denied(command, shared_scenario.feature_worktree, _protected("main"))

    @pytest.mark.skipif(
        sys.platform != "darwin" or not Path("/usr/bin/awk").exists(),
        reason="forces BSD/macOS awk's paragraph-mode reading of `RS = \"\\0\"`, which only Darwin's /usr/bin/awk has",
    )
    def test_denies_a_push_after_a_blank_line_on_the_bsd_awk(self, shared_scenario, tmp_path):
        """Guards the masker's record separator at the gate, with `/usr/bin/awk` forced ahead of any other awk on PATH.

        Regression pin for `RS = "\\0"`: CI runs a non-BSD awk, which reads it as an ordinary separator and would
        pass without the fix.
        """
        extra_env = awk_first_on_path_env(tmp_path / "bsd-awk-bin", SYSTEM_AWK_NAME)
        _assert_denied(
            "# publish\n\ngit push origin main",
            shared_scenario.feature_worktree,
            _protected("main"),
            extra_env=extra_env,
        )


class TestRepoPath:
    def test_allows_a_push_into_a_feature_worktree_named_relative_to_the_cwd(self, shared_scenario):
        _assert_allowed("git -C ../feature-wt push -u origin HEAD", shared_scenario.main_checkout)

    @pytest.mark.parametrize(
        "command",
        ["git -C <MAIN> push origin HEAD", "git --git-dir=<MAIN>/.git push origin HEAD"],
    )
    def test_denies_a_push_that_targets_the_default_branch_checkout(self, shared_scenario, command):
        _assert_denied(
            shared_scenario.expand(command), shared_scenario.feature_worktree, _protected("main")
        )

    def test_allows_a_push_through_the_git_dir_of_the_feature_worktree(self, shared_scenario):
        """Pairs with the `--git-dir=<MAIN>/.git` deny above, which differs only in the repository it names."""
        feature_git_dir = shared_scenario.main_checkout / ".git" / "worktrees" / shared_scenario.feature_worktree.name
        _assert_allowed(
            f"git --git-dir={feature_git_dir} push origin HEAD", shared_scenario.main_checkout
        )

    def test_resolves_a_relative_dash_c_against_the_payload_cwd_not_the_process_cwd(
        self, shared_scenario, tmp_path
    ):
        payload_cwd = shared_scenario.main_checkout.parent
        unrelated_process_cwd = tmp_path / "unrelated-process-cwd"
        unrelated_process_cwd.mkdir()
        _assert_denied(
            f"git -C {shared_scenario.main_checkout.name} push origin HEAD",
            payload_cwd,
            _protected("main"),
            process_cwd=unrelated_process_cwd,
        )
        _assert_allowed(
            f"git -C {shared_scenario.feature_worktree.name} push origin HEAD",
            payload_cwd,
            process_cwd=unrelated_process_cwd,
        )

    def test_resolves_the_repository_from_the_payload_cwd_not_the_process_cwd(
        self, shared_scenario, tmp_path
    ):
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        reason = run_hook_reason(
            DENY_DEFAULT_BRANCH_PUSH_HOOK,
            bash_input("git push origin HEAD", cwd=str(shared_scenario.main_checkout)),
            cwd=elsewhere,
        )
        assert reason is not None and _protected("main") in reason
        assert (
            run_hook(
                DENY_DEFAULT_BRANCH_PUSH_HOOK,
                bash_input("git push origin HEAD", cwd=str(shared_scenario.feature_worktree)),
                cwd=elsewhere,
            )
            == "allow"
        )


class TestMovedFlag:
    @pytest.mark.parametrize(
        "command",
        [
            "cd <MAIN> && git push",
            "git checkout main && git push origin HEAD",
            "gh pr checkout 7 && git push -u origin HEAD",
            "pushd <FEATURE> && git push -u origin HEAD",
            "popd && git push -u origin HEAD",
            "git switch main && git push origin HEAD",
            "git checkout main && git push origin @",
        ],
    )
    def test_denies_a_head_relative_push_after_a_fragment_that_moves_state(
        self, shared_scenario, command
    ):
        _assert_denied(
            shared_scenario.expand(command),
            shared_scenario.feature_worktree,
            "git -C <dir> push",
        )

    @pytest.mark.parametrize(
        "command",
        ["cd <FEATURE> && git push origin feature", "git checkout main && git push origin feature"],
        ids=["directory-change", "checkout"],
    )
    def test_allows_an_explicit_push_after_a_fragment_that_moves_state(self, shared_scenario, command):
        _assert_allowed(shared_scenario.expand(command), shared_scenario.main_checkout)

    @pytest.mark.parametrize(
        "command",
        [
            "git add -A && git commit -m x && git push -u origin HEAD",
            "git add -A && git commit -m x && git push",
        ],
        ids=["head-relative", "refspec-less"],
    )
    def test_allows_a_head_relative_push_after_fragments_that_move_nothing(self, shared_scenario, command):
        _assert_allowed(command, shared_scenario.feature_worktree)


class TestAliases:
    @pytest.mark.parametrize(
        ("deny_command", "allow_command"),
        [
            ("git p origin main", "git p origin feature"),
            ("git q origin main", "git q origin feature"),
        ],
        ids=["alias-of-push", "alias-with-global-option"],
    )
    def test_expands_an_alias_to_a_push_before_judging(
        self, shared_scenario, deny_command, allow_command
    ):
        _assert_denied(deny_command, shared_scenario.feature_worktree, _protected("main"))
        _assert_allowed(allow_command, shared_scenario.feature_worktree)

    def test_looks_an_alias_up_in_the_repository_a_dash_c_names_and_judges_the_push_there(self, shared_scenario):
        """The re-judged push follows `-C`, not the payload cwd. The lookup half is pinned by the next test."""
        _assert_denied(
            shared_scenario.expand("git -C <MAIN> p origin HEAD"),
            shared_scenario.feature_worktree,
            _protected("main"),
        )
        _assert_allowed(
            shared_scenario.expand("git -C <FEATURE> p origin HEAD"), shared_scenario.main_checkout
        )

    def test_finds_an_alias_defined_only_in_the_repository_the_command_names(self, shared_scenario, trunk_scenario):
        """The payload cwd's repository lacks `only-here`, so a lookup there would miss and allow the push."""
        _run_git(trunk_scenario.main_checkout, "config", "alias.only-here", "push")
        for command in (
            f"git -C {trunk_scenario.main_checkout} only-here origin HEAD",
            f"git --git-dir={trunk_scenario.main_checkout}/.git only-here origin HEAD",
        ):
            _assert_denied(command, shared_scenario.feature_worktree, _protected("trunk"))

    def test_denies_an_alias_of_checkout_as_a_moved_flag(self, shared_scenario):
        _assert_denied("git co main && git push", shared_scenario.feature_worktree, "git -C <dir> push")

    def test_denies_a_shell_alias_the_gate_cannot_expand(self, shared_scenario):
        _assert_denied(
            "git sync", shared_scenario.feature_worktree, "runs a shell command", LITERAL_PUSH_ADVICE
        )

    def test_denies_an_alias_of_an_alias_and_allows_an_alias_of_a_builtin(self, shared_scenario):
        _assert_denied(
            "git pp origin feature", shared_scenario.feature_worktree, "expands to 'p'"
        )
        _assert_allowed("git st", shared_scenario.feature_worktree)


class TestOtherPushSurfaces:
    @pytest.mark.parametrize(
        "command",
        ["git subtree push -P dir origin main", "git send-pack ../origin.git main"],
    )
    def test_denies_a_subtree_or_send_pack_push_to_the_default_branch(self, shared_scenario, command):
        _assert_denied(command, shared_scenario.feature_worktree, "protected branch 'main'")

    def test_allows_a_send_pack_push_to_a_feature_branch(self, shared_scenario):
        _assert_allowed("git send-pack ../origin.git feature", shared_scenario.feature_worktree)


class TestGhApi:
    @pytest.mark.parametrize(
        "command",
        [
            "gh api -X PUT repos/o/r/contents/f -f message=m -f content=x",
            "gh api -X PATCH repos/o/r/git/refs/heads/main -f sha=abc",
            "gh api -X POST repos/o/r/merges -f base=main -f head=x",
            "gh api -X PUT $URL -f message=m -f content=x",
            "gh api --paginate repos/o/r/contents/f -X PUT -f message=m -f content=x",
            "gh api repos/$(gh repo view --json nameWithOwner -q .nameWithOwner)/contents/f "
            "-X PUT -f message=m -f content=x",
            "gh api repos/`gh repo view --json nameWithOwner -q .nameWithOwner`/contents/f "
            "-X PUT -f message=m -f content=x",
            'gh api -X PUT "repos/{owner}/{repo}/contents/f" -f message=m -f content=x',
            "gh api -X PUT /repos/o/r/contents/f -f message=m -f content=x",
            "gh api -X POST repos/o/r/merges?base=main -f head=x",
            "gh api -X PUT repositories/123/contents/f -f message=m -f content=x",
            "gh api repos/o/r/contents/f --jq '.a|.b' -X PUT -f message=m -f content=x",
        ],
        ids=[
            "contents",
            "git-refs",
            "merges",
            "computed-endpoint",
            "boolean-flag-first",
            "substitution",
            "backtick",
            "quoted-placeholder-endpoint",
            "leading-slash-endpoint",
            "query-string-only-endpoint",
            "repositories-id-endpoint",
            "pipe-in-flag-value-before-method",
        ],
    )
    def test_denies_a_mutating_call_to_a_ref_moving_endpoint(self, tmp_path, command):
        """The last case puts a separator inside the call's own words, so its method and body flags land in a later fragment."""
        _assert_denied(command, tmp_path, "mutating gh api call")

    @pytest.mark.parametrize(
        "command",
        [
            "gh api repos/o/r/contents/f",
            "gh api --paginate repos/o/r/contents/f",
            "gh api -X POST repos/o/r/issues -f title=t",
            'gh api repos/o/r/issues -f body="$(cat f)"',
            'git commit -m "deny gh api -X PUT repos/o/r/contents/f"',
            "gh api repos/o/r/contents/f --jq '.a|.b'",
        ],
        ids=[
            "get",
            "get-boolean-flag",
            "mutating-issues",
            "substitution-field-value",
            "mention-not-command-word",
            "read-with-pipe-in-flag-value",
        ],
    )
    def test_allows_a_read_or_a_mutating_call_that_moves_no_ref(self, tmp_path, command):
        _assert_allowed(command, tmp_path)

    @pytest.mark.parametrize(
        "command",
        [
            "gh api repos/o/r/contents/f -X GET -f key=value",
            "gh api repos/o/r/git/refs/heads/main --jq .object.sha && rm -f scratch.txt",
            "gh api repos/o/r/contents/README.md --jq .content | base64 -d | grep -F pattern",
        ],
        ids=["get-with-field", "read-then-force-remove", "read-piped-to-fixed-string-grep"],
    )
    def test_denies_a_read_beside_a_body_flag_or_write_method_elsewhere_in_the_command(self, tmp_path, command):
        """Pins the header's accepted over-denial: the mutating test reads the whole command, not the `gh api` fragment.

        The deny message must also name the way forward: running the read as its own Bash call.
        """
        _assert_denied(command, tmp_path, "mutating gh api call", "its own Bash call")

    @pytest.mark.parametrize(
        "command",
        [
            'gh api repos/o/r/issues/1/comments -f body="Status: $STATUS"',
            "gh api graphql -f query='mutation { resolveReviewThread(input: {threadId: $id}) "
            "{ clientMutationId } }' -f id=X",
            "gh api repos/owner/repo/pulls/4/comments/12345678/replies \\\n"
            "  -F body='Moved `helper` to the shared module.\n"
            "\n"
            "Generated by an agent'",
        ],
        ids=["status-field", "graphql-variable", "respond-pr-reply-with-inline-code"],
    )
    def test_reads_only_the_endpoint_word_for_a_computed_endpoint(self, tmp_path, command):
        _assert_allowed(command, tmp_path)


class TestQuotedMentionsAreInert:
    @pytest.mark.parametrize(
        "command",
        [
            'gh pr create --body "never git push origin main"',
            'git commit -m "docs: git push origin main"',
            "git commit -m \"$(cat <<'EOF'\n"
            "docs: describe the push gate\n"
            "Never run `git push origin main`, and never use eval.\n"
            "EOF\n"
            ')"',
        ],
        ids=["pr-body", "commit-message", "heredoc-commit-with-inline-code"],
    )
    def test_allows_a_quoted_mention_of_a_push_on_the_default_branch(self, shared_scenario, command):
        _assert_allowed(command, shared_scenario.main_checkout)

    @pytest.mark.parametrize(
        ("command", "mention"),
        [
            ('git push origin "main"', _protected("main")),
            ("git push origin 'main'", _protected("main")),
            ('"git" push origin main', _protected("main")),
            ('git push "origin" "main"', _protected("main")),
            # `:` is outside [A-Za-z0-9._/-], so the masker blanks the span to "" (header, accepted over-denials).
            # A masker that unquotes it would flip this case to _protected("main").
            ('git push origin "HEAD:main"', _unresolved("refspec")),
        ],
        ids=["quoted-branch", "single-quoted-branch", "quoted-git-word", "quoted-remote-and-branch", "quoted-refspec"],
    )
    def test_denies_a_push_whose_words_are_quoted(self, shared_scenario, command, mention):
        """Quote-splitting a push into words must not hide it from the masked pass that judges it."""
        _assert_denied(command, shared_scenario.feature_worktree, mention)


class TestRedirectionWordsAreNotPushArguments:
    def test_redirected_push_is_judged_like_the_same_push_without_a_redirect(self, shared_scenario):
        """Guards the parser skipping redirection words such as `2>&1`.

        Read as a push argument, `2>&1` would be a refspec, so `git push origin 2>&1` would be judged as a push of a
        branch named `2>&1` instead of a refspec-less push. The same push is denied on the default branch and
        allowed on a feature branch.
        """
        _assert_denied("git push origin 2>&1", shared_scenario.main_checkout, _protected("main"))
        _assert_allowed("git push origin 2>&1", shared_scenario.feature_worktree)


class TestPinnedGaps:
    def test_executed_double_quoted_substitution_is_a_pinned_gap(self, shared_scenario):
        """Quote masking cannot tell a double-quoted `$(...)` that bash runs from a mention (header Known gap)."""
        _assert_allowed('out="$(git push -u origin HEAD 2>&1)"', shared_scenario.main_checkout)
        _assert_denied("git push -u origin HEAD", shared_scenario.main_checkout, _protected("main"))

    def test_dash_c_command_substitution_target_is_a_pinned_gap(self, shared_scenario):
        """Pins the header's unquoted `-C $(...)` Known gap: the substitution cuts the fragment before the subcommand.

        If the first assertion fails, a fix made the allow stop reproducing. Delete the header entry and flip
        this test to a deny.
        """
        feature_worktree = shared_scenario.feature_worktree
        _assert_allowed("git -C $(pwd) push origin main", feature_worktree)
        _assert_denied("git -C . push origin main", feature_worktree, _protected("main"))

    @pytest.mark.parametrize(
        ("hidden_endpoint", "visible_endpoint"),
        [
            (
                'gh api -X PUT -f message="add readme" $URL',
                'gh api $URL -X PUT -f message="add readme"',
            ),
            (
                'gh api -X PUT -f content="$(base64 f)" repos/o/r/contents/f',
                'gh api repos/o/r/contents/f -X PUT -f content="$(base64 f)"',
            ),
        ],
        ids=["quoted-multi-word-value", "substitution-value"],
    )
    def test_gh_api_endpoint_after_a_flag_value_is_a_pinned_gap(self, tmp_path, hidden_endpoint, visible_endpoint):
        _assert_allowed(hidden_endpoint, tmp_path)
        _assert_denied(visible_endpoint, tmp_path, "mutating gh api call")


class TestPinnedGhApiCommandWordGaps:
    """Pins the header's `gh api` command-word Known gaps: the arm keys on the command word, so a prefix hides the call."""

    MUTATING_CALL = "gh api -X PUT repos/o/r/contents/f -f message=m -f content=x"

    @pytest.mark.parametrize(
        "prefix",
        ["do ", "timeout 30 ", "gtimeout 30 ", "GH_TOKEN=$(cat token) "],
        ids=["shell-keyword", "timeout", "gtimeout", "assignment-with-substitution"],
    )
    def test_gh_api_call_after_a_prefix_is_a_pinned_gap(self, tmp_path, prefix):
        """If the allow fails, a fix closed the gap: delete the header entry and flip this case to a deny."""
        _assert_allowed(prefix + self.MUTATING_CALL, tmp_path)

    def test_gh_api_call_after_an_assignment_without_a_substitution_is_read(self, tmp_path):
        """The control for the assignment-with-substitution gap: only the substitution's cut hides the call."""
        _assert_denied("GH_TOKEN=token " + self.MUTATING_CALL, tmp_path, "mutating gh api call")


class TestAppendedArguments:
    def test_denies_a_push_whose_arguments_xargs_appends(self, shared_scenario):
        _assert_denied(
            "git branch --merged | xargs git push origin --delete",
            shared_scenario.feature_worktree,
            _unresolved("appended-args"),
        )

    def test_allows_an_xargs_command_that_is_not_a_push(self, shared_scenario):
        _assert_allowed("git branch --merged | xargs git branch -d", shared_scenario.feature_worktree)


class TestDryRun:
    def test_allows_a_dry_run_push_to_the_default_branch(self, shared_scenario):
        _assert_allowed("git push --dry-run origin main", shared_scenario.feature_worktree)

    def test_denies_a_real_push_to_the_default_branch_chained_after_a_dry_run(self, shared_scenario):
        """The dry-run exemption belongs to its own push, not to the rest of the command."""
        _assert_denied(
            "git push --dry-run origin main && git push origin main",
            shared_scenario.feature_worktree,
            _protected("main"),
        )

    def test_allows_a_real_push_to_a_feature_branch_chained_after_a_dry_run(self, shared_scenario):
        _assert_allowed(
            "git push --dry-run origin main && git push origin feature",
            shared_scenario.feature_worktree,
        )


class TestNonBashTools:
    def test_passes_a_non_bash_tool_call(self, shared_scenario):
        assert (
            run_hook(
                DENY_DEFAULT_BRANCH_PUSH_HOOK,
                read_input("/etc/hosts"),
                cwd=shared_scenario.main_checkout,
            )
            == "allow"
        )

    def test_judges_the_same_command_only_when_the_tool_is_bash(self, shared_scenario):
        """Only the tool-name guard separates the two payloads: the command and the working directory are identical."""
        command = "git push origin main"
        working_directory = shared_scenario.feature_worktree
        _assert_denied(command, working_directory, _protected("main"))
        non_bash_payload = {
            "tool_name": "Read",
            "tool_input": {"command": command},
            "cwd": str(working_directory),
        }
        _assert_hook_allows(DENY_DEFAULT_BRANCH_PUSH_HOOK, non_bash_payload, working_directory)

    def test_allows_a_bash_call_with_no_command(self, shared_scenario):
        """A Bash payload whose tool_input holds no command has nothing to judge (header: fail-open)."""
        payload = {"tool_name": "Bash", "tool_input": {}, "cwd": str(shared_scenario.main_checkout)}
        _assert_hook_allows(DENY_DEFAULT_BRANCH_PUSH_HOOK, payload, shared_scenario.main_checkout)


class TestPayloadWorkingDirectory:
    def test_judges_the_process_cwd_when_the_payload_names_none(self, shared_scenario):
        payload_without_cwd = {"tool_name": "Bash", "tool_input": {"command": "git push origin HEAD"}}
        reason = run_hook_reason(
            DENY_DEFAULT_BRANCH_PUSH_HOOK, payload_without_cwd, cwd=shared_scenario.main_checkout
        )
        assert reason is not None and _protected("main") in reason
        _assert_hook_allows(
            DENY_DEFAULT_BRANCH_PUSH_HOOK, payload_without_cwd, shared_scenario.feature_worktree
        )

    def test_judges_a_repository_whose_path_holds_a_space_and_a_non_ascii_character(self, tmp_path):
        spaced_root = tmp_path / "wörk space"
        spaced_root.mkdir()
        spaced_scenario = _build_scenario(spaced_root)
        _assert_denied("git push origin HEAD", spaced_scenario.main_checkout, _protected("main"))
        _assert_allowed("git push origin HEAD", spaced_scenario.feature_worktree)


class TestNoGitCallWithoutPushFragment:
    def test_forks_git_only_for_an_alias_lookup(self, shared_scenario, tmp_path):
        extra_env, call_log = _git_call_log_env(tmp_path)

        for command in ("ls", "git status", "git log --oneline", "gh pr view"):
            _assert_allowed(command, shared_scenario.feature_worktree, extra_env=extra_env)
        assert not call_log.exists(), f"git was called for a command with no push fragment: {call_log.read_text()}"

        _assert_allowed("git st", shared_scenario.feature_worktree, extra_env=extra_env)
        calls = call_log.read_text().splitlines()
        assert len(calls) == 1 and calls[0].endswith("config --get alias.st"), calls

    def test_allows_a_git_extension_after_exactly_one_alias_lookup(self, shared_scenario, tmp_path):
        """A subcommand outside the builtin list and defined by no alias is looked up once, then allowed."""
        extra_env, call_log = _git_call_log_env(tmp_path)
        _assert_allowed("git submodule status", shared_scenario.feature_worktree, extra_env=extra_env)
        calls = call_log.read_text().splitlines()
        assert len(calls) == 1 and calls[0].endswith("config --get alias.submodule"), calls


class TestFailClosed:
    """Each fail-closed site denies and names the step that failed."""

    def test_denies_when_stripping_quotes_fails(self, shared_scenario, tmp_path):
        extra_env = {"PATH": path_with_failing_tool_shim(tmp_path / "failing-tr", "tr", exit_status=3)}
        _assert_denied(
            "git push origin feature", shared_scenario.feature_worktree, "strip shell quotes", extra_env=extra_env
        )

    def test_denies_when_the_stripped_split_fails(self, shared_scenario, sed_split_stage_shim):
        _assert_denied(
            "git push origin feature",
            shared_scenario.feature_worktree,
            "split the stripped command",
            extra_env=sed_split_stage_shim(0),
        )

    def test_denies_when_the_masked_split_fails(self, shared_scenario, sed_split_stage_shim):
        _assert_denied(
            "git push origin feature",
            shared_scenario.feature_worktree,
            "split the masked command",
            extra_env=sed_split_stage_shim(1),
        )

    def test_denies_when_masking_fails(self, shared_scenario, tmp_path):
        extra_env = {"PATH": path_with_failing_tool_shim(tmp_path / "failing-awk", "awk", exit_status=3)}
        _assert_denied(
            "git push origin feature", shared_scenario.feature_worktree, "mask quoted command text", extra_env=extra_env
        )

    @pytest.mark.parametrize(
        ("shim_match", "command", "step", "status"),
        [
            ('[[ "$*" == *"config --get alias."* ]]', "git p origin feature", "look up alias 'p'", 3),
            ('[[ "$*" == *"symbolic-ref -q --short HEAD"* ]]', "git push origin HEAD", "read HEAD", 3),
            ('[[ "$*" == *"--get-regexp"* ]]', "git push", "read push config", 3),
            ('[[ "$*" == *" remote" ]]', "git push", "list remotes", 3),
            (
                '[[ "$*" == *"symbolic-ref -q refs/remotes/"* ]]',
                "git push origin feature",
                "read the protected branches",
                2,
            ),
        ],
        ids=["alias-lookup", "symbolic-ref-head", "config-get-regexp", "remote-list", "protected-branches"],
    )
    def test_denies_when_a_git_call_fails(
        self, shared_scenario, git_timeout_shim, shim_match, command, step, status
    ):
        extra_env = git_timeout_shim(shim_match, exit_status=3)
        _assert_denied(
            command, shared_scenario.feature_worktree, step, f"status {status}", extra_env=extra_env
        )

    @pytest.mark.timing
    def test_denies_when_resolving_the_push_destination_hits_the_cap(
        self, scenario, git_timeout_shim, tmp_path
    ):
        """The 128 fallback for a missing @{push} would judge `feature` and pass; a cap kill must not take it."""
        _run_git(scenario.feature_worktree, "branch", "--set-upstream-to=origin/main", "feature")
        _run_git(scenario.feature_worktree, "config", "push.default", "upstream")
        extra_env = git_timeout_shim('[[ "$*" == *"rev-parse --symbolic-full-name"* ]]')
        with assert_cap_engaged(tmp_path, production_cap=5):
            _assert_denied("git push", scenario.feature_worktree, "resolve @{push}", extra_env=extra_env)
