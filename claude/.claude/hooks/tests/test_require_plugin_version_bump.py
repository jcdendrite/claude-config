"""Tests for require-plugin-version-bump.sh."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, bash_input, edit_input, run_hook, run_hook_reason

_PLUGINS_DIR = HOOKS_DIR.parent.parent.parent / "plugins"
VERSION_BUMP_HOOK = _PLUGINS_DIR / "plugin-semver" / "hooks" / "require-plugin-version-bump.sh"
_PLUGIN_LIB = _PLUGINS_DIR / "plugin-semver" / "hooks" / "_lib.sh"
_STOWED_LIB = HOOKS_DIR / "_lib.sh"


def _git_q(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _plugin_json_rel(plugin_rel_dir: str) -> str:
    return f"{plugin_rel_dir}/.claude-plugin/plugin.json"


def _write_plugin_json(repo: Path, plugin_rel_dir: str, version: str | None, extra: dict | None = None) -> Path:
    """Write a well-formed plugin.json (or one missing `version` when version=None)."""
    plugin_json_path = repo / _plugin_json_rel(plugin_rel_dir)
    plugin_json_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict = {"name": Path(plugin_rel_dir).name}
    if version is not None:
        payload["version"] = version
    if extra:
        payload.update(extra)
    plugin_json_path.write_text(json.dumps(payload) + "\n")
    return plugin_json_path


def _write_skill_file(repo: Path, rel_path: str, content: str = "## test\n") -> Path:
    skill_path = repo / rel_path
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(content)
    return skill_path


def _commit_plugin(repo: Path, plugin_rel_dir: str, version: str, message: str = "add plugin") -> None:
    """Create and commit a plugin at `plugin_rel_dir` with the given version.

    Becomes the new HEAD, so subsequent uncommitted staged changes are what's
    being gated against this baseline.
    """
    _write_plugin_json(repo, plugin_rel_dir, version)
    _git_q(repo, "add", _plugin_json_rel(plugin_rel_dir))
    _git_q(repo, "commit", "-qm", message)


# ---------- merge-base fixtures ------------------------------------------
# Local copies of test_check_branch_divergence.py's bare_remote/feature_clone
# model (not shared via conftest.py — only this file and
# test_check_branch_divergence.py need it, and duplicating a small git-repo
# builder keeps each test file independently readable per DAMP). The seed
# repo here also commits a plugin at plugins/demo (1.0.0), which
# test_check_branch_divergence.py's origin has no need for.


@pytest.fixture
def bare_remote(tmp_path):
    """Bare repo to act as `origin`, with a single `main` commit containing
    plugins/demo at version 1.0.0."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git_q(seed, "init", "-q", "-b", "main")
    _git_q(seed, "config", "user.email", "t@t.com")
    _git_q(seed, "config", "user.name", "t")
    _commit_plugin(seed, "plugins/demo", "1.0.0", message="init")
    _git_q(seed, "remote", "add", "origin", str(bare))
    _git_q(seed, "push", "-q", "origin", "main")
    return bare


@pytest.fixture
def feature_clone(tmp_path, bare_remote):
    """Clone of `bare_remote` checked out on a feature branch with
    origin/HEAD properly set."""
    repo = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", str(bare_remote), str(repo)],
        check=True,
        capture_output=True,
    )
    _git_q(repo, "config", "user.email", "t@t.com")
    _git_q(repo, "config", "user.name", "t")
    _git_q(repo, "remote", "set-head", "origin", "main")
    _git_q(repo, "checkout", "-q", "-b", "feature")
    return repo


class TestRequirePluginVersionBump:
    # ---------------- Bump semantics (degraded BASE=HEAD is fine here) ----

    def test_plugin_file_changed_no_bump_denies(self, isolated_home, git_repo):
        _commit_plugin(git_repo, "plugins/foo", "1.0.0")
        _write_skill_file(git_repo, "plugins/foo/skills/bar/SKILL.md")
        _git_q(git_repo, "add", "plugins/foo/skills/bar/SKILL.md")
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=git_repo) == "deny"

    def test_version_raised_allows(self, isolated_home, git_repo):
        _commit_plugin(git_repo, "plugins/foo", "1.0.0")
        _write_skill_file(git_repo, "plugins/foo/skills/bar/SKILL.md")
        _git_q(git_repo, "add", "plugins/foo/skills/bar/SKILL.md")
        _write_plugin_json(git_repo, "plugins/foo", "1.1.0")
        _git_q(git_repo, "add", _plugin_json_rel("plugins/foo"))
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=git_repo) == "allow"

    def test_version_lowered_denies(self, isolated_home, git_repo):
        _commit_plugin(git_repo, "plugins/foo", "1.5.0")
        _write_plugin_json(git_repo, "plugins/foo", "1.4.0")
        _git_q(git_repo, "add", _plugin_json_rel("plugins/foo"))
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=git_repo) == "deny"

    def test_version_exactly_equal_denies(self, isolated_home, git_repo):
        _commit_plugin(git_repo, "plugins/foo", "1.0.0")
        _write_skill_file(git_repo, "plugins/foo/skills/bar/SKILL.md")
        _git_q(git_repo, "add", "plugins/foo/skills/bar/SKILL.md")
        # Re-stage plugin.json with the identical version (a no-op edit).
        _write_plugin_json(git_repo, "plugins/foo", "1.0.0")
        _git_q(git_repo, "add", _plugin_json_rel("plugins/foo"))
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=git_repo) == "deny"

    def test_two_plugins_changed_only_one_bumped_denies(self, isolated_home, git_repo):
        _commit_plugin(git_repo, "plugins/foo", "1.0.0")
        _commit_plugin(git_repo, "plugins/bar", "1.0.0")
        _write_skill_file(git_repo, "plugins/foo/skills/x/SKILL.md")
        _git_q(git_repo, "add", "plugins/foo/skills/x/SKILL.md")
        _write_skill_file(git_repo, "plugins/bar/skills/y/SKILL.md")
        _git_q(git_repo, "add", "plugins/bar/skills/y/SKILL.md")
        # Bump only plugins/bar.
        _write_plugin_json(git_repo, "plugins/bar", "1.1.0")
        _git_q(git_repo, "add", _plugin_json_rel("plugins/bar"))
        reason = run_hook_reason(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=git_repo)
        assert reason is not None, "hook allowed silently; expected deny"
        # Attributes the violation to the unbumped plugin only, not the bumped one.
        assert "plugins/foo" in reason
        assert "plugins/bar" not in reason

    def test_references_md_only_change_no_bump_denies(self, isolated_home, git_repo):
        """Any file counts toward the gate, not just SKILL.md/hooks — confirms
        no exclusion list exists for REFERENCES.md or similar co-located files."""
        _commit_plugin(git_repo, "plugins/foo", "1.0.0")
        _write_skill_file(git_repo, "plugins/foo/skills/bar/REFERENCES.md", "notes\n")
        _git_q(git_repo, "add", "plugins/foo/skills/bar/REFERENCES.md")
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=git_repo) == "deny"

    def test_pure_version_only_bump_commit_allows(self, isolated_home, git_repo):
        _commit_plugin(git_repo, "plugins/foo", "1.0.0")
        _write_plugin_json(git_repo, "plugins/foo", "1.0.1")
        _git_q(git_repo, "add", _plugin_json_rel("plugins/foo"))
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=git_repo) == "allow"

    # ---------------- Fail-closed inputs -----------------------------------

    def test_malformed_staged_plugin_json_denies(self, isolated_home, git_repo):
        _commit_plugin(git_repo, "plugins/foo", "1.0.0")
        plugin_json = git_repo / _plugin_json_rel("plugins/foo")
        plugin_json.write_text("not json")
        _git_q(git_repo, "add", _plugin_json_rel("plugins/foo"))
        reason = run_hook_reason(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=git_repo)
        assert reason is not None, "hook allowed silently; expected deny"

    def test_missing_version_key_denies(self, isolated_home, git_repo):
        _commit_plugin(git_repo, "plugins/foo", "1.0.0")
        _write_plugin_json(git_repo, "plugins/foo", None)
        _git_q(git_repo, "add", _plugin_json_rel("plugins/foo"))
        reason = run_hook_reason(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=git_repo)
        assert reason is not None, "hook allowed silently; expected deny"
        assert "missing or has no 'version' key" in reason

    @pytest.mark.parametrize("bad_version", ["v1.0.0", "1.0", "1.0.0-beta"])
    def test_non_numeric_dotted_version_denies(self, isolated_home, git_repo, bad_version):
        _commit_plugin(git_repo, "plugins/foo", "1.0.0")
        _write_plugin_json(git_repo, "plugins/foo", bad_version)
        _git_q(git_repo, "add", _plugin_json_rel("plugins/foo"))
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=git_repo) == "deny"

    # ---------------- Plugin detection --------------------------------------

    def test_new_plugin_added_allows(self, isolated_home, git_repo):
        _write_plugin_json(git_repo, "plugins/brand-new", "1.0.0")
        _git_q(git_repo, "add", _plugin_json_rel("plugins/brand-new"))
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=git_repo) == "allow"

    def test_plugin_directory_removed_allows(self, isolated_home, git_repo):
        _commit_plugin(git_repo, "plugins/foo", "1.0.0")
        _git_q(git_repo, "rm", "-r", "plugins/foo")
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=git_repo) == "allow"

    def test_stray_file_under_plugins_with_no_plugin_root_ancestor_allows(self, isolated_home, git_repo):
        stray = git_repo / "plugins" / "README.md"
        stray.parent.mkdir(parents=True, exist_ok=True)
        stray.write_text("not a plugin root\n")
        _git_q(git_repo, "add", "plugins/README.md")
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=git_repo) == "allow"

    def test_nested_file_attributes_to_plugin_root(self, isolated_home, git_repo):
        """A deeply nested file (plugins/foo/skills/bar/SKILL.md) must be
        attributed to plugin root plugins/foo, not skipped or misattributed —
        proven here by the deny firing on the *unbumped* plugins/foo, exactly
        as it would for a change to plugins/foo/.claude-plugin/plugin.json
        directly."""
        _commit_plugin(git_repo, "plugins/foo", "1.0.0")
        _write_skill_file(git_repo, "plugins/foo/skills/bar/SKILL.md")
        _git_q(git_repo, "add", "plugins/foo/skills/bar/SKILL.md")
        reason = run_hook_reason(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=git_repo)
        assert reason is not None, "hook allowed silently; expected deny"
        assert "plugins/foo" in reason

    # ---------------- Command/tool/repo gates -------------------------------

    def test_non_plugin_file_changed_allows(self, isolated_home, git_repo):
        # git_repo already has file.txt staged; no plugin touched.
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=git_repo) == "allow"

    @pytest.mark.parametrize(
        "command",
        [
            "git status",
            "git log --oneline",
            "git commit-tree abc123",
        ],
    )
    def test_non_commit_git_commands_allowed(self, isolated_home, git_repo, command):
        assert run_hook(VERSION_BUMP_HOOK, bash_input(command), cwd=git_repo) == "allow"

    def test_non_bash_tool_allowed(self, isolated_home, git_repo):
        assert run_hook(VERSION_BUMP_HOOK, edit_input("/tmp/foo.txt"), cwd=git_repo) == "allow"

    def test_outside_git_repo_allowed(self, isolated_home, tmp_path):
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=non_repo) == "allow"

    def test_empty_staged_diff_allows(self, isolated_home, git_repo):
        """Amend-message-only, --allow-empty, or nothing-to-commit has no new content."""
        subprocess.run(["git", "commit", "-q", "-m", "tmp"], cwd=git_repo, check=True)
        assert (
            run_hook(VERSION_BUMP_HOOK, bash_input("git commit --amend -m new-message"), cwd=git_repo)
            == "allow"
        )

    def test_amend_with_unbumped_plugin_change_denies(self, isolated_home, git_repo):
        """BASE (degraded mode) is HEAD, the commit that created plugins/foo
        at 1.0.0. An amend that stages a further unbumped change to that
        plugin still compares BASE's 1.0.0 against the staged 1.0.0 —
        equal, so this is a correct deny given BASE-vs-index comparison,
        not a false positive specific to --amend."""
        _commit_plugin(git_repo, "plugins/foo", "1.0.0")
        _write_skill_file(git_repo, "plugins/foo/skills/bar/SKILL.md")
        _git_q(git_repo, "add", "plugins/foo/skills/bar/SKILL.md")
        assert (
            run_hook(VERSION_BUMP_HOOK, bash_input("git commit --amend -m new-message"), cwd=git_repo)
            == "deny"
        )

    def test_chained_add_commit_denies_without_bump(self, isolated_home, git_repo):
        _commit_plugin(git_repo, "plugins/foo", "1.0.0")
        _write_skill_file(git_repo, "plugins/foo/skills/bar/SKILL.md")
        _git_q(git_repo, "add", "plugins/foo/skills/bar/SKILL.md")
        assert (
            run_hook(VERSION_BUMP_HOOK, bash_input("git add file.txt && git commit -m foo"), cwd=git_repo)
            == "deny"
        )

    def test_chained_add_commit_allows_with_bump(self, isolated_home, git_repo):
        _commit_plugin(git_repo, "plugins/foo", "1.0.0")
        _write_skill_file(git_repo, "plugins/foo/skills/bar/SKILL.md")
        _git_q(git_repo, "add", "plugins/foo/skills/bar/SKILL.md")
        _write_plugin_json(git_repo, "plugins/foo", "1.1.0")
        _git_q(git_repo, "add", _plugin_json_rel("plugins/foo"))
        assert (
            run_hook(VERSION_BUMP_HOOK, bash_input("git add file.txt && git commit -m foo"), cwd=git_repo)
            == "allow"
        )

    # ---------------- Merge-base resolution ---------------------------------

    def test_merge_base_one_bump_per_branch_allows_later_untouched_commit(self, feature_clone):
        """Load-bearing case: a bump landed in an earlier commit on the
        feature branch; a later commit touches the same plugin again with
        no further bump. Under degraded BASE=HEAD comparison this would
        deny (HEAD already contains the bump, so HEAD-vs-index compares
        equal) — the merge-base-vs-index comparison correctly allows,
        since BASE (merge-base with origin/main) predates the bump. If
        BASE resolution silently fell back to degraded mode, this
        assertion would flip to deny and fail."""
        _write_plugin_json(feature_clone, "plugins/demo", "1.1.0")
        skill = feature_clone / "plugins" / "demo" / "skills" / "bar" / "SKILL.md"
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text("v1\n")
        _git_q(feature_clone, "add", _plugin_json_rel("plugins/demo"), "plugins/demo/skills/bar/SKILL.md")
        _git_q(feature_clone, "commit", "-qm", "bump and add skill")

        # Later, uncommitted change touching the same plugin again — no further bump.
        skill.write_text("v1\nv2\n")
        _git_q(feature_clone, "add", "plugins/demo/skills/bar/SKILL.md")

        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=feature_clone) == "allow"

    def test_merge_base_resolved_via_origin_head(self, feature_clone):
        """Simple confirmation that BASE resolves via merge-base(HEAD,
        origin/main) when origin/HEAD is set: comparing against the
        pre-branch baseline (1.0.0) allows a straightforward bump."""
        _write_plugin_json(feature_clone, "plugins/demo", "1.1.0")
        _git_q(feature_clone, "add", _plugin_json_rel("plugins/demo"))
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=feature_clone) == "allow"

    def test_merge_base_resolved_via_local_main_fallback(self, tmp_path):
        """No origin remote; a local `main` distinct from HEAD (`feature`)
        is the fallback BASE. Advancing feature past main first forces the
        resolution through the merge-base(HEAD, refs/heads/main) fallback
        branch rather than degraded BASE=HEAD."""
        repo = tmp_path / "local-main-repo"
        repo.mkdir()
        _git_q(repo, "init", "-q", "-b", "main")
        _git_q(repo, "config", "user.email", "t@t.com")
        _git_q(repo, "config", "user.name", "t")
        _commit_plugin(repo, "plugins/demo", "1.0.0", message="init")
        _git_q(repo, "checkout", "-q", "-b", "feature")
        _write_skill_file(repo, "plugins/demo/skills/bar/SKILL.md")
        _git_q(repo, "add", "plugins/demo/skills/bar/SKILL.md")
        _git_q(repo, "commit", "-qm", "add skill on feature")

        _write_plugin_json(repo, "plugins/demo", "1.1.0")
        _git_q(repo, "add", _plugin_json_rel("plugins/demo"))
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=repo) == "allow"

    def test_degraded_mode_denies_earlier_bumped_commit_known_limitation(self, isolated_home, git_repo):
        """Known limitation, pinned by test: with no origin/HEAD and no
        local main/master distinct from HEAD, BASE degrades to HEAD. A
        branch that already bumped a plugin's version in an earlier commit
        is falsely denied on a later commit touching the same plugin
        again, because BASE (=HEAD, which already contains the bump)
        compares equal to the staged version."""
        _commit_plugin(git_repo, "plugins/demo", "1.0.0")
        _write_plugin_json(git_repo, "plugins/demo", "1.1.0")
        skill = git_repo / "plugins" / "demo" / "skills" / "bar" / "SKILL.md"
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text("v1\n")
        _git_q(git_repo, "add", _plugin_json_rel("plugins/demo"), "plugins/demo/skills/bar/SKILL.md")
        _git_q(git_repo, "commit", "-qm", "bump and add skill")

        skill.write_text("v1\nv2\n")
        _git_q(git_repo, "add", "plugins/demo/skills/bar/SKILL.md")

        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=git_repo) == "deny"

    # ---------------- Drift guard -------------------------------------------

    def test_plugin_lib_sh_parses_tool_input_same_as_stowed_lib_sh(self):
        """The plugin's trimmed _lib.sh must behave identically to the stowed
        copy for the one function this hook actually sources:
        _lib_parse_tool_input_or_deny (which itself calls _lib_jq).

        A project-scope install of plugin-semver has no stowed
        ~/.claude/hooks/_lib.sh available, so the plugin ships its own trimmed
        copy (require-plugin-version-bump.sh needs only these two helpers —
        no git helpers, no marker helpers, no worktree-enforcement helpers;
        see plugins/plugin-semver/hooks/_lib.sh's header). A behavioral check
        here — not a whole-file byte comparison — is the right invariant:
        whole-file identity would force this plugin to carry, and re-sync on
        every change to, worktree/git-enforcement code it never calls.
        """
        harness = (
            'emit_deny() {{ printf "DENY:%s\\n" "$1"; exit 0; }}; '
            '. "{lib}"; '
            '_lib_parse_tool_input_or_deny "test-msg"; '
            'printf "OK:%s:%s\\n" "$TOOL_NAME" "$COMMAND"'
        )
        payload = '{"tool_name":"Bash","tool_input":{"command":"git commit -m foo"}}'
        plugin_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_PLUGIN_LIB)],
            input=payload, capture_output=True, text=True, check=False,
        )
        stowed_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_STOWED_LIB)],
            input=payload, capture_output=True, text=True, check=False,
        )
        assert plugin_result.stdout == stowed_result.stdout, (
            "plugins/plugin-semver/hooks/_lib.sh's _lib_parse_tool_input_or_deny "
            "behaves differently than the stowed claude/.claude/hooks/_lib.sh copy — "
            f"plugin: {plugin_result.stdout!r}, stowed: {stowed_result.stdout!r}"
        )

    def test_plugin_lib_sh_jq_fallback_matches_stowed_lib_sh(self, tmp_path):
        """Without timeout(1) in PATH, _lib_jq's bare-jq fallback branch must
        behave identically between the plugin's copy and the stowed copy.

        The default-PATH parity test above never exercises this branch — jq
        and bash's own timeout(1) is present on the test runner's PATH, so
        _lib_jq's `if command -v timeout` always takes the wrapped branch.
        Mirrors test_lib.py::test_timeout_absent_fallback_valid_payload_returns_ok's
        technique: build a PATH with jq/bash/coreutils symlinked in but
        timeout deliberately omitted.
        """
        import shutil

        jq_path = shutil.which("jq")
        bash_path = shutil.which("bash")
        if not jq_path or not bash_path:
            pytest.skip("jq or bash not found in PATH")
        (tmp_path / "jq").symlink_to(jq_path)
        (tmp_path / "bash").symlink_to(bash_path)
        for cmd in ["head", "tail", "cat", "cut", "printf"]:
            cmd_path = shutil.which(cmd)
            if cmd_path:
                (tmp_path / cmd).symlink_to(cmd_path)
        env = {"PATH": str(tmp_path), "HOME": str(tmp_path)}

        harness = (
            'emit_deny() {{ printf "DENY:%s\\n" "$1"; exit 0; }}; '
            '. "{lib}"; '
            '_lib_parse_tool_input_or_deny "test-msg"; '
            'printf "OK:%s:%s\\n" "$TOOL_NAME" "$COMMAND"'
        )
        payload = '{"tool_name":"Bash","tool_input":{"command":"git commit -m foo"}}'
        plugin_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_PLUGIN_LIB)],
            input=payload, capture_output=True, text=True, check=False, env=env,
        )
        stowed_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_STOWED_LIB)],
            input=payload, capture_output=True, text=True, check=False, env=env,
        )
        assert plugin_result.stdout == stowed_result.stdout, (
            "plugins/plugin-semver/hooks/_lib.sh's _lib_jq timeout-absent fallback "
            "behaves differently than the stowed claude/.claude/hooks/_lib.sh copy — "
            f"plugin: {plugin_result.stdout!r}, stowed: {stowed_result.stdout!r}"
        )
