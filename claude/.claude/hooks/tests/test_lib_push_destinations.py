"""Unit tests for _lib.sh's push-destination helpers: _lib_git_push_targets (the parser),
_lib_push_protected_branches, _lib_gh_api_call_is_mutating, _LIB_GIT_BUILTIN_SUBCOMMANDS and
_LIB_GIT_GLOBAL_VALUE_FLAGS.
"""
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import pytest
from helpers import (
    bare_remote_with_default_branch,
    isolated_git_config_env,
    path_with_failing_tool_shim,
    set_origin_head,
)

from ._lib_sh_helpers import _run_lib_call

# --- _lib_git_push_targets ------------------------------------------------
#
# The parser's records are the contract the default-branch push gate judges. Most cases assert the
# whole record list, so a stray record fails them; the cases parametrized over a cause, a config
# key or a not-a-push shape assert only the one record or the exit status they name.

CWD_REPO = ("repo", "cwd", "")
ORIGIN = ("remote", "origin")


def _push_targets(fragment: str, env: dict | None = None) -> tuple[int, list[tuple[str, ...]]]:
    result = _run_lib_call(
        f"_lib_git_push_targets {shlex.quote(fragment)}", env if env is not None else dict(os.environ)
    )
    records = [tuple(line.split("\t")) for line in result.stdout.splitlines()]
    return result.returncode, records


def _ref(source: str, destination: str) -> tuple[str, str, str]:
    return ("ref", source, destination)


def _unresolvable(reason: str) -> tuple[str, str]:
    return ("unresolvable", reason)


class TestPushTargetsRecords:
    @pytest.mark.parametrize(
        ("fragment", "expected_records"),
        [
            ("git push origin main", [CWD_REPO, ORIGIN, _ref("main", "main")]),
            ("git push", [CWD_REPO, ("no-refspec",)]),
            ("git push origin", [CWD_REPO, ORIGIN, ("no-refspec",)]),
            ("git push -u origin feature", [CWD_REPO, ORIGIN, _ref("feature", "feature")]),
            ("git push origin feature main", [CWD_REPO, ORIGIN, _ref("feature", "feature"), _ref("main", "main")]),
            ("sudo git push origin main", [CWD_REPO, ORIGIN, _ref("main", "main")]),
            ("/usr/bin/git push origin main", [CWD_REPO, ORIGIN, _ref("main", "main")]),
            ("git-push origin main", [CWD_REPO, ORIGIN, _ref("main", "main")]),
            ("git push --force-with-lease=main:abc origin main", [CWD_REPO, ORIGIN, _ref("main", "main")]),
        ],
    )
    def test_command_forms_parse_to_repo_remote_and_refs(self, fragment, expected_records):
        assert _push_targets(fragment) == (0, expected_records)

    @pytest.mark.parametrize(
        ("refspec", "expected_ref"),
        [
            ("+main", _ref("main", "main")),
            ("refs/heads/main", _ref("refs/heads/main", "main")),
            ("HEAD:main", _ref("HEAD", "main")),
            ("feature:refs/heads/main", _ref("feature", "main")),
            ("+feature:main", _ref("feature", "main")),
            ("HEAD:main-fix", _ref("HEAD", "main-fix")),
            ("HEAD:refs/for/main", _ref("HEAD", "refs/for/main")),
            ("HEAD:refs/tags/v1", _ref("HEAD", "refs/tags/v1")),
            (":main", _ref("", "main")),
            ("HEAD:heads/main", _ref("HEAD", "main")),
            ("HEAD:heads/feature", _ref("HEAD", "feature")),
            ("HEAD:refs/heads/heads/main", _ref("HEAD", "heads/main")),
            ("HEAD:feature/main", _ref("HEAD", "feature/main")),
            ("feature/main", _ref("feature/main", "feature/main")),
            ("@:main", _ref("@", "main")),
        ],
    )
    def test_refspec_destination_normalization(self, refspec, expected_ref):
        assert _push_targets(f"git push origin {refspec}") == (0, [CWD_REPO, ORIGIN, expected_ref])

    @pytest.mark.parametrize(
        "fragment",
        ["git push --delete origin main", "git push origin -d main", "git push origin main --delete"],
    )
    def test_delete_flag_gives_the_ref_an_empty_source(self, fragment):
        assert _push_targets(fragment) == (0, [CWD_REPO, ORIGIN, _ref("", "main")])

    def test_tag_keyword_pair_gives_a_tag_ref_for_both_sides(self):
        assert _push_targets("git push origin tag v1.0") == (
            0, [CWD_REPO, ORIGIN, _ref("refs/tags/v1.0", "refs/tags/v1.0")],
        )

    @pytest.mark.parametrize("flag", ["--dry-run", "-n"])
    def test_dry_run_flag_adds_a_dry_run_record(self, flag):
        assert _push_targets(f"git push {flag} origin main") == (
            0, [CWD_REPO, ORIGIN, _ref("main", "main"), ("dry-run",)],
        )

    def test_tags_flag_adds_a_tags_record_and_follow_tags_does_not(self):
        assert _push_targets("git push --tags origin") == (0, [CWD_REPO, ORIGIN, ("no-refspec",), ("tags",)])
        assert _push_targets("git push --follow-tags") == (0, [CWD_REPO, ("no-refspec",)])

    def test_recurse_submodules_separate_value_is_not_the_remote(self):
        assert _push_targets("git push --recurse-submodules check origin") == (
            0, [CWD_REPO, ORIGIN, ("no-refspec",)],
        )

    @pytest.mark.parametrize("flag", ["--recurse-submodules=on-demand", "--no-recurse-submodules"])
    def test_recurse_submodules_attached_forms_add_no_record(self, flag):
        assert _push_targets(f"git push {flag} origin main") == (0, [CWD_REPO, ORIGIN, _ref("main", "main")])

    @pytest.mark.parametrize(
        "fragment",
        [
            "xargs git push origin --delete",
            "/usr/bin/xargs git push origin --delete",
            "find . -exec git push origin {} ;",
            "find . -execdir git push origin {} ;",
            "find . -ok git push origin {} ;",
            "find . -okdir git push origin {} ;",
            "parallel git push origin ::: x",
        ],
    )
    def test_argument_appending_wrapper_before_the_command_is_unresolvable(self, fragment):
        _, records = _push_targets(fragment)
        assert _unresolvable("appended-args") in records

    def test_branch_named_xargs_after_the_command_is_not_an_appending_wrapper(self):
        assert _push_targets("git push origin xargs") == (0, [CWD_REPO, ORIGIN, _ref("xargs", "xargs")])


class TestPushTargetsRepoForms:
    @pytest.mark.parametrize(
        ("fragment", "expected_repo"),
        [
            ("git -C a push", ("repo", "C", "a")),
            ("git -C a -C b push", ("repo", "C", "a/b")),
            ("git -C /x -C b push", ("repo", "C", "/x/b")),
            ("git -C a -C /y push", ("repo", "C", "/y")),
            ("git -C /abs push", ("repo", "C", "/abs")),
            ("git --git-dir=/g push", ("repo", "git-dir", "/g")),
            ("git --git-dir /g push", ("repo", "git-dir", "/g")),
            ("GIT_DIR=/g git push", ("repo", "git-dir", "/g")),
            ("GIT_DIR=/x/git git push", ("repo", "git-dir", "/x/git")),
            ("git -C a --git-dir g push", ("repo", "git-dir", "a/g")),
            ("git -C a --git-dir /g push", ("repo", "git-dir", "/g")),
        ],
    )
    def test_repo_record_composes_c_and_git_dir_values(self, fragment, expected_repo):
        assert _push_targets(fragment) == (0, [expected_repo, ("no-refspec",)])

    @pytest.mark.parametrize("git_dir_value", ["/x/git", "/x/repo"])
    def test_git_dir_prefix_is_recorded_whatever_the_value_ends_in(self, git_dir_value):
        # A GIT_DIR value ending in `/git` must not read as the command word.
        assert _push_targets(f"GIT_DIR={git_dir_value} git push origin HEAD:main") == (
            0, [("repo", "git-dir", git_dir_value), ORIGIN, _ref("HEAD", "main")],
        )

    def test_other_assignment_prefix_ending_in_git_is_not_the_command_word(self):
        assert _push_targets("FOO=/x/git git push origin main") == (0, [CWD_REPO, ORIGIN, _ref("main", "main")])

    def test_git_dir_prefix_before_a_non_push_command_exits_one(self):
        result = _run_lib_call(f"_lib_git_push_targets {shlex.quote('GIT_DIR=/x/git git status')}", dict(os.environ))
        assert (result.returncode, result.stdout) == (1, "")

    def test_leading_tilde_slash_reads_as_home(self):
        env = {**os.environ, "HOME": "/home/tester"}
        assert _push_targets("git -C ~/work push", env) == (0, [("repo", "C", "/home/tester/work"), ("no-refspec",)])
        assert _push_targets("git --git-dir=~/work/.git push", env) == (
            0, [("repo", "git-dir", "/home/tester/work/.git"), ("no-refspec",)],
        )

    @pytest.mark.parametrize(
        "fragment",
        [
            'git -C "$DIR" push',
            'git -C "" push',
            "git -C $DIR push",
            "git -C `pwd` push",
            "git -C ~other/x push",
            "git -C ~ push",
            'git --git-dir="$G" push',
            "GIT_DIR=$G git push",
            "git -C a\\b push",
            "git -C a{b,c} push",
            "git -C a?b push",
            "git --git-dir=a[b] push",
        ],
    )
    def test_computed_or_user_tilde_repo_path_is_unresolvable(self, fragment):
        _, records = _push_targets(fragment)
        assert _unresolvable("repo-path") in records


class TestPushTargetsConfigRecords:
    @pytest.mark.parametrize(
        ("fragment", "expected_key"),
        [
            ("git -c Push.Default=matching push", "push.default"),
            ("git -c Remote.Origin.Push=HEAD:main push", "remote.Origin.push"),
            ("git -c branch.Feat.PushRemote=fork push", "branch.Feat.pushremote"),
            ("git -c push.pushOption=ci.skip push origin feature", "push.pushoption"),
            ("git --config-env=push.default=SOME_VAR push", "push.default"),
            ("git --config-env push.default=SOME_VAR push", "push.default"),
            ("GIT_CONFIG_PARAMETERS=x git push", "*"),
            ("GIT_CONFIG_COUNT=1 git push", "*"),
        ],
    )
    def test_config_flag_and_env_prefix_give_a_canonical_config_record(self, fragment, expected_key):
        _, records = _push_targets(fragment)
        assert ("config", expected_key) in records

    @pytest.mark.parametrize(
        ("fragment", "expected_key"),
        [
            ("git -c push.default=matching push", "push.default"),
            ("git -c remote.origin.push=HEAD:main push", "remote.origin.push"),
        ],
    )
    def test_push_routing_config_with_no_refspec_gives_a_config_record_and_no_refspec(self, fragment, expected_key):
        assert _push_targets(fragment) == (0, [CWD_REPO, ("config", expected_key), ("no-refspec",)])

    def test_repo_flag_value_is_the_remote_when_no_positional_is_given(self):
        assert _push_targets("git push --repo=upstream") == (0, [CWD_REPO, ("remote", "upstream"), ("no-refspec",)])
        assert _push_targets("git push --repo upstream") == (0, [CWD_REPO, ("remote", "upstream"), ("no-refspec",)])

    def test_first_positional_wins_over_the_repo_flag(self):
        assert _push_targets("git push --repo=upstream origin main") == (
            0, [CWD_REPO, ORIGIN, _ref("main", "main")],
        )


class TestPushTargetsWordRules:
    @pytest.mark.parametrize(
        "fragment",
        [
            "git push -un origin main",
            "git push -nu origin main",
        ],
    )
    def test_short_bundle_is_read_per_letter(self, fragment):
        assert _push_targets(fragment) == (0, [CWD_REPO, ORIGIN, _ref("main", "main"), ("dry-run",)])

    def test_bundled_d_letter_sets_delete_mode(self):
        assert _push_targets("git push -fd origin main") == (0, [CWD_REPO, ORIGIN, _ref("", "main")])

    @pytest.mark.parametrize(
        "fragment",
        [
            "git push -oX origin main",
            "git push -o X origin main",
            "git push -uo X origin main",
            "git push --push-option X origin main",
            "git push --push-option=X origin main",
        ],
    )
    def test_push_option_value_is_not_a_positional(self, fragment):
        assert _push_targets(fragment) == (0, [CWD_REPO, ORIGIN, _ref("main", "main")])

    def test_value_flag_ending_the_fragment_takes_no_value(self):
        assert _push_targets("git push origin main -o") == (0, [CWD_REPO, ORIGIN, _ref("main", "main")])

    def test_words_after_double_dash_are_positional(self):
        assert _push_targets("git push -- origin -main") == (0, [CWD_REPO, ORIGIN, _ref("-main", "-main")])
        assert _push_targets("git push origin -- --delete") == (
            0, [CWD_REPO, ORIGIN, _ref("--delete", "--delete")],
        )

    @pytest.mark.parametrize(
        "fragment",
        [
            "git push origin 2>&1",
            "git push origin >/dev/null 2>&1",
        ],
    )
    def test_trailing_redirection_leaves_a_refspec_less_push_with_no_ref(self, fragment):
        assert _push_targets(fragment) == (0, [CWD_REPO, ORIGIN, ("no-refspec",)])

    def test_bare_redirection_operator_also_skips_its_target_word(self):
        assert _push_targets("git push origin feature > log") == (0, [CWD_REPO, ORIGIN, _ref("feature", "feature")])

    def test_redirection_between_arguments_is_not_a_refspec(self):
        assert _push_targets("git push -u origin HEAD 2>&1") == (0, [CWD_REPO, ORIGIN, _ref("HEAD", "HEAD")])

    def test_unspaced_redirection_inside_a_refspec_is_unresolvable(self):
        assert _push_targets("git push origin main>log") == (0, [CWD_REPO, ORIGIN, _unresolvable("refspec")])

    def test_push_parser_consumes_the_value_of_a_flag_listed_in_the_global_value_flags(self):
        result = _run_lib_call(
            "_LIB_GIT_GLOBAL_VALUE_FLAGS+=(--sentinel-flag); "
            f"_lib_git_push_targets {shlex.quote('git --sentinel-flag value push origin main')}",
            dict(os.environ),
        )
        records = [tuple(line.split("\t")) for line in result.stdout.splitlines()]
        assert (result.returncode, records) == (0, [CWD_REPO, ORIGIN, _ref("main", "main")])


class TestPushTargetsUnresolvable:
    @pytest.mark.parametrize(
        ("fragment", "reason"),
        [
            ("git push --all origin", "all"),
            ("git push --branches origin", "all"),
            ("git push --mirror origin", "mirror"),
            ("git push --prune origin", "prune"),
            ("git push origin refs/heads/*:refs/heads/*", "glob"),
            ("git push origin :", "matching"),
            ("git push origin +:", "matching"),
            ('git push "" main', "remote"),
            ("git push $REMOTE main", "remote"),
            ("git push `echo origin` main", "remote"),
            ('git push origin ""', "refspec"),
            ("git push origin HEAD:$BRANCH", "refspec"),
            ("git push origin `git branch --show-current`", "refspec"),
            ("git push origin tag $TAG", "refspec"),
            ("git push origin HEAD:m\\ain", "refspec"),
            ("git push origin HEAD:ma{i,i}n", "refspec"),
            ("git push origin HE\\AD", "refspec"),
            ("git push origin HEAD:{main,wip}", "refspec"),
            ("git push origin HEAD:ma?n", "refspec"),
            ("git push origin HEAD:ma[i]n", "refspec"),
            ("git push or\\igin main", "remote"),
            ("git push or{i,i}gin main", "remote"),
        ],
    )
    def test_each_cause_gives_its_own_reason(self, fragment, reason):
        _, records = _push_targets(fragment)
        assert _unresolvable(reason) in records

    def test_force_mark_revision_suffix_and_at_sign_in_a_refspec_are_literal(self):
        """Guards the computed-word test against flagging characters git refspecs legitimately hold."""
        assert _push_targets("git push origin +HEAD~1:feature/x@y") == (
            0, [CWD_REPO, ORIGIN, _ref("HEAD~1", "feature/x@y")],
        )

    def test_unresolvable_word_adds_no_remote_or_ref_record(self):
        assert _push_targets("git push origin $BRANCH") == (
            0, [CWD_REPO, ORIGIN, _unresolvable("refspec")],
        )

    def test_one_record_per_cause(self):
        assert _push_targets("git push --all --mirror --all origin") == (
            0, [CWD_REPO, ORIGIN, ("no-refspec",), _unresolvable("all"), _unresolvable("mirror")],
        )


class TestPushTargetsOtherSurfaces:
    @pytest.mark.parametrize(
        ("fragment", "expected_records"),
        [
            ("git subtree push -P dir origin main", [CWD_REPO, ORIGIN, _ref("main", "main")]),
            ("git subtree push --prefix=dir -m msg origin main", [CWD_REPO, ORIGIN, _ref("main", "main")]),
            ("git subtree push -P dir --onto abc -b b origin main", [CWD_REPO, ORIGIN, _ref("main", "main")]),
            ("git subtree push -P dir origin", [CWD_REPO, ORIGIN, ("no-refspec",)]),
            ("git-subtree push -P dir origin main", [CWD_REPO, ORIGIN, _ref("main", "main")]),
            ("git send-pack ../origin.git main", [CWD_REPO, ("remote", "../origin.git"), _ref("main", "main")]),
            ("git-send-pack ../origin.git feature", [CWD_REPO, ("remote", "../origin.git"), _ref("feature", "feature")]),
        ],
    )
    def test_subtree_and_send_pack_parse_like_push(self, fragment, expected_records):
        assert _push_targets(fragment) == (0, expected_records)

    def test_send_pack_with_no_ref_is_unresolvable_instead_of_no_refspec(self):
        assert _push_targets("git send-pack ../origin.git") == (
            0, [CWD_REPO, ("remote", "../origin.git"), _unresolvable("no-refs")],
        )

    def test_send_pack_stdin_is_unresolvable_without_no_refs(self):
        assert _push_targets("git send-pack --stdin ../origin.git") == (
            0, [CWD_REPO, ("remote", "../origin.git"), _unresolvable("stdin")],
        )

    @pytest.mark.parametrize(
        ("fragment", "reason"),
        [("git send-pack --all ../origin.git", "all"), ("git send-pack --mirror ../origin.git", "mirror")],
    )
    def test_send_pack_all_or_mirror_gives_its_own_reason_without_no_refs(self, fragment, reason):
        assert _push_targets(fragment) == (
            0, [CWD_REPO, ("remote", "../origin.git"), _unresolvable(reason)],
        )

    @pytest.mark.parametrize(
        "fragment",
        [
            "git status",
            "git commit -m push",
            "git -C dir log",
            "git subtree split -P dir",
            "git subtree",
            "git",
            "ls -la",
            "echo hello",
            "gh pr view",
            "",
        ],
    )
    def test_fragment_that_is_not_a_push_exits_one_with_no_output(self, fragment):
        result = _run_lib_call(f"_lib_git_push_targets {shlex.quote(fragment)}", dict(os.environ))
        assert (result.returncode, result.stdout) == (1, "")


# --- _lib_git_argv_from_subcmd reads _LIB_GIT_GLOBAL_VALUE_FLAGS -----------


def test_argv_walk_consumes_the_value_of_a_flag_listed_in_the_global_value_flags():
    result = _run_lib_call(
        "_LIB_GIT_GLOBAL_VALUE_FLAGS+=(--sentinel-flag); "
        f"_lib_git_argv_from_subcmd {shlex.quote('git --sentinel-flag value push origin')}",
        dict(os.environ),
    )
    assert result.stdout.split() == ["push", "origin"]


# --- _LIB_GIT_BUILTIN_SUBCOMMANDS -----------------------------------------


def test_every_listed_builtin_subcommand_is_a_builtin_of_the_running_git():
    # `--list-cmds` is an internal option of git(1); a git without it skips rather than fails.
    listing = subprocess.run(
        ["git", "--list-cmds=builtins"], capture_output=True, text=True, check=False
    )
    if listing.returncode != 0 or not listing.stdout.strip():
        pytest.skip("this git does not support --list-cmds=builtins")
    listed = _run_lib_call('printf "%s\\n" "${_LIB_GIT_BUILTIN_SUBCOMMANDS[@]}"', dict(os.environ))
    assert listed.returncode == 0
    names = listed.stdout.split()
    assert "push" in names
    assert set(names) - set(listing.stdout.split()) == set()


# --- _lib_push_protected_branches -----------------------------------------


def _protected_branches(call_args: str, env: dict, cwd_prefix: str = "") -> tuple[int, list[str]]:
    result = _run_lib_call(f"{cwd_prefix}_lib_push_protected_branches {call_args}", env)
    return result.returncode, result.stdout.split()


def _add_remote_with_head(clone: Path, remote_name: str, head_branch: str, env: dict) -> None:
    """Add a remote and point its remote-tracking HEAD at `head_branch` without a network fetch.

    `env` covers the `remote add` call only; `set_origin_head` runs its git calls under the process environment.
    """
    subprocess.run(
        ["git", "remote", "add", remote_name, str(clone)], cwd=clone, check=True, env={**os.environ, **env}
    )
    set_origin_head(clone, head_branch, remote_name)


class TestPushProtectedBranches:
    def test_resolved_origin_head_adds_nothing_beyond_main_and_master(self, tmp_path):
        env = {**os.environ, **isolated_git_config_env(tmp_path / "home")}
        _, clone = bare_remote_with_default_branch(tmp_path)

        assert _protected_branches(f"C {shlex.quote(str(clone))} origin", env) == (0, ["main", "master"])

    def test_resolved_origin_head_naming_another_branch_is_protected(self, tmp_path):
        env = {**os.environ, **isolated_git_config_env(tmp_path / "home")}
        _, clone = bare_remote_with_default_branch(tmp_path, branch="trunk")

        assert _protected_branches(f"C {shlex.quote(str(clone))} origin", env) == (0, ["trunk", "main", "master"])

    def test_unset_origin_head_adds_develop(self, tmp_path):
        env = {**os.environ, **isolated_git_config_env(tmp_path / "home")}
        _, clone = bare_remote_with_default_branch(tmp_path)
        subprocess.run(
            ["git", "remote", "set-head", "origin", "--delete"], cwd=clone, check=True, env=env
        )

        assert _protected_branches(f"C {shlex.quote(str(clone))} origin", env) == (0, ["main", "master", "develop"])

    def test_cwd_form_reads_the_current_directory(self, tmp_path):
        env = {**os.environ, **isolated_git_config_env(tmp_path / "home")}
        _, clone = bare_remote_with_default_branch(tmp_path, branch="trunk")

        assert _protected_branches(
            "cwd '' origin", env, cwd_prefix=f"cd {shlex.quote(str(clone))} && "
        ) == (0, ["trunk", "main", "master"])

    def test_git_dir_form_reads_the_named_git_directory(self, tmp_path):
        env = {**os.environ, **isolated_git_config_env(tmp_path / "home")}
        _, clone = bare_remote_with_default_branch(tmp_path, branch="trunk")

        assert _protected_branches(f"git-dir {shlex.quote(str(clone / '.git'))} origin", env) == (
            0, ["trunk", "main", "master"],
        )

    def test_url_path_or_empty_remote_adds_develop_and_protects_no_trunk_default(self, tmp_path):
        env = {**os.environ, **isolated_git_config_env(tmp_path / "home")}
        _, clone = bare_remote_with_default_branch(tmp_path, branch="trunk")
        remotes = ["git@host:o/r.git", "https://host/o/r.git", "../origin.git", "", "-origin"]

        protected_by_remote = {
            remote: _protected_branches(f"C {shlex.quote(str(clone))} {shlex.quote(remote)}", env)
            for remote in remotes
        }

        assert protected_by_remote == {remote: (0, ["main", "master", "develop"]) for remote in remotes}

    def test_each_remote_is_judged_by_its_own_head(self, tmp_path):
        env = {**os.environ, **isolated_git_config_env(tmp_path / "home")}
        _, clone = bare_remote_with_default_branch(tmp_path)
        _add_remote_with_head(clone, "fork", "dev", env)

        assert _protected_branches(f"C {shlex.quote(str(clone))} fork", env) == (0, ["dev", "main", "master"])
        assert _protected_branches(f"C {shlex.quote(str(clone))} origin", env) == (0, ["main", "master"])

    def test_git_failure_exits_two_with_no_output(self, tmp_path):
        env = {**os.environ, **isolated_git_config_env(tmp_path / "home")}
        not_a_repo = tmp_path / "not-a-repo"
        not_a_repo.mkdir()

        assert _protected_branches(f"C {shlex.quote(str(not_a_repo))} origin", env) == (2, [])

    def test_unknown_form_exits_two_with_no_output(self, tmp_path):
        env = {**os.environ, **isolated_git_config_env(tmp_path / "home")}

        assert _protected_branches("worktree /x origin", env) == (2, [])


# --- _lib_gh_api_call_is_mutating -----------------------------------------


def _gh_api_call_is_mutating(text: str, env: dict | None = None) -> int:
    return _run_lib_call(
        f"_lib_gh_api_call_is_mutating {shlex.quote(text)}", env if env is not None else dict(os.environ)
    ).returncode


class TestGhApiCallIsMutating:
    @pytest.mark.parametrize(
        "text",
        [
            "gh api -X POST repos/o/r/issues",
            "gh api -X PUT repos/o/r/contents/f",
            "gh api -X PATCH repos/o/r/git/refs/heads/main",
            "gh api -X DELETE repos/o/r/git/refs/heads/x",
            "gh api -XPOST repos/o/r/issues",
            "gh api -X=PUT repos/o/r/contents/f",
            "gh api --method POST repos/o/r/issues",
            "gh api --method=PUT repos/o/r/contents/f",
            "gh api -x post repos/o/r/issues",
            "gh api repos/o/r/issues -f title=t",
            "gh api repos/o/r/issues -F draft=true",
            "gh api repos/o/r/issues --field title=t",
            "gh api repos/o/r/issues --raw-field title=t",
            "gh api repos/o/r/issues --input body.json",
            "gh api repos/o/r/issues --field=title=t",
            "cd /tmp && gh api repos/o/r/issues -f title=t",
        ],
    )
    def test_request_body_spelling_is_mutating(self, text):
        assert _gh_api_call_is_mutating(text) == 0

    @pytest.mark.parametrize(
        "text",
        [
            "gh api repos/o/r/contents/f",
            "gh api -X GET repos/o/r/contents/f",
            "gh api -XGET repos/o/r",
            "gh api --method GET repos/o/r",
            "gh api repos/o/r --jq .name",
            "gh api -X POSTED",
            "gh pr view 7",
        ],
    )
    def test_read_only_spelling_is_not_mutating(self, text):
        assert _gh_api_call_is_mutating(text) == 1

    def test_grep_failure_counts_as_mutating(self, tmp_path):
        grep_engine_error_status = 2
        env = {
            **os.environ,
            "PATH": path_with_failing_tool_shim(
                tmp_path / "grep-shim", "grep", exit_status=grep_engine_error_status
            ),
        }

        assert _gh_api_call_is_mutating("gh api repos/o/r/contents/f", env) == 0

    def test_body_flag_grep_failure_counts_as_mutating(self, tmp_path):
        """The method grep (`-qiE`) runs real and finds no match, so only the body-flag grep (`-qE`) fails."""
        grep_engine_error_status = 2
        env = {
            **os.environ,
            "PATH": path_with_failing_tool_shim(
                tmp_path / "grep-shim", "grep", arg_pattern="-qE", exit_status=grep_engine_error_status
            ),
        }

        assert _gh_api_call_is_mutating("gh api repos/o/r/contents/f", env) == 0
