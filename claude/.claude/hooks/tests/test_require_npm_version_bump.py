"""Tests for require-npm-version-bump.sh."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, bash_input, edit_input, run_hook, run_hook_advisory, run_hook_reason

_PLUGINS_DIR = HOOKS_DIR.parent.parent.parent / "plugins"
VERSION_BUMP_HOOK = _PLUGINS_DIR / "npm-semver" / "hooks" / "require-npm-version-bump.sh"
_NPM_SEMVER_LIB = _PLUGINS_DIR / "npm-semver" / "hooks" / "_lib.sh"
_STOWED_LIB = HOOKS_DIR / "_lib.sh"


def _git_q(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _package_json_rel(package_rel_dir: str) -> str:
    if package_rel_dir == ".":
        return "package.json"
    return f"{package_rel_dir}/package.json"


def _write_package_json(
    repo: Path,
    package_rel_dir: str,
    version: str | None,
    private: bool = False,
    extra: dict | None = None,
) -> Path:
    """Write a well-formed package.json (or one missing `version` when version=None)."""
    package_json_path = repo / _package_json_rel(package_rel_dir)
    package_json_path.parent.mkdir(parents=True, exist_ok=True)
    name = "root" if package_rel_dir == "." else Path(package_rel_dir).name
    payload: dict = {"name": name}
    if version is not None:
        payload["version"] = version
    if private:
        payload["private"] = True
    if extra:
        payload.update(extra)
    package_json_path.write_text(json.dumps(payload) + "\n")
    return package_json_path


def _write_source_file(repo: Path, rel_path: str, content: str = "export const x = 1;\n") -> Path:
    source_path = repo / rel_path
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(content)
    return source_path


def _commit_package(
    repo: Path,
    package_rel_dir: str,
    version: str,
    private: bool = False,
    message: str = "add package",
) -> None:
    """Create and commit a package at `package_rel_dir` with the given version.

    Becomes the new HEAD, so subsequent uncommitted staged changes are what's
    being gated against this baseline.
    """
    _write_package_json(repo, package_rel_dir, version, private=private)
    _git_q(repo, "add", _package_json_rel(package_rel_dir))
    _git_q(repo, "commit", "-qm", message)


# ---------- merge-base fixtures ------------------------------------------
# Local copies of test_check_branch_divergence.py's bare_remote/feature_clone
# model (not shared via conftest.py — only that file and
# test_require_plugin_version_bump.py need it too; duplicating a small
# git-repo builder keeps each test file independently readable per DAMP).
# The seed repo here also commits a package at packages/demo (1.0.0), which
# test_check_branch_divergence.py's origin has no need for.


@pytest.fixture
def bare_remote(tmp_path):
    """Bare repo to act as `origin`, with a single `main` commit containing
    packages/demo at version 1.0.0."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git_q(seed, "init", "-q", "-b", "main")
    _git_q(seed, "config", "user.email", "t@t.com")
    _git_q(seed, "config", "user.name", "t")
    _commit_package(seed, "packages/demo", "1.0.0", message="init")
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


class TestRequireNpmVersionBump:
    # ---------------- Bump semantics (merge-base-resolved BASE) ------------

    def test_bump_present_allows_with_propagate_reminder(self, feature_clone):
        _write_source_file(feature_clone, "packages/demo/src/index.ts")
        _write_package_json(feature_clone, "packages/demo", "1.1.0")
        _git_q(feature_clone, "add", "packages/demo/src/index.ts", _package_json_rel("packages/demo"))
        assert (
            run_hook_advisory(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=feature_clone)
            == "allow"
        )

    def test_bump_present_emits_systemMessage_naming_package_root(self, feature_clone):
        """Strengthens the coarse check above: parses the hook's raw stdout as
        JSON and asserts the systemMessage field is present and names the
        bumped package's root — deleting the hook's systemMessage emit block
        would make this test fail, unlike run_hook_advisory above, which
        collapses any non-deny payload to "allow" without inspecting content."""
        _write_source_file(feature_clone, "packages/demo/src/index.ts")
        _write_package_json(feature_clone, "packages/demo", "1.1.0")
        _git_q(feature_clone, "add", "packages/demo/src/index.ts", _package_json_rel("packages/demo"))
        result = subprocess.run(
            [str(VERSION_BUMP_HOOK)],
            input=json.dumps(bash_input("git commit -m foo")),
            capture_output=True,
            text=True,
            cwd=feature_clone,
            check=False,
        )
        payload = json.loads(result.stdout)
        assert "systemMessage" in payload
        assert "packages/demo" in payload["systemMessage"]

    def test_bump_missing_denies(self, feature_clone):
        _write_source_file(feature_clone, "packages/demo/src/index.ts")
        _git_q(feature_clone, "add", "packages/demo/src/index.ts")
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=feature_clone) == "deny"

    def test_version_lowered_denies(self, feature_clone):
        _write_source_file(feature_clone, "packages/demo/src/index.ts")
        _write_package_json(feature_clone, "packages/demo", "0.9.0")
        _git_q(feature_clone, "add", "packages/demo/src/index.ts", _package_json_rel("packages/demo"))
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=feature_clone) == "deny"

    def test_version_exactly_equal_denies(self, feature_clone):
        _write_source_file(feature_clone, "packages/demo/src/index.ts")
        # Re-stage package.json with the identical version (a no-op edit).
        _write_package_json(feature_clone, "packages/demo", "1.0.0")
        _git_q(feature_clone, "add", "packages/demo/src/index.ts", _package_json_rel("packages/demo"))
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=feature_clone) == "deny"

    # ---------------- Publish gate ------------------------------------------

    def test_private_true_skips_bump_requirement(self, tmp_path):
        # Branch off and diverge from main so BASE resolves to a real
        # merge-base — otherwise this would vacuously pass via the
        # no-merge-base fail-open path rather than the private-package skip.
        repo = tmp_path / "private-repo"
        repo.mkdir()
        _git_q(repo, "init", "-q", "-b", "main")
        _git_q(repo, "config", "user.email", "t@t.com")
        _git_q(repo, "config", "user.name", "t")
        _commit_package(repo, ".", "1.0.0", private=True, message="init")
        _git_q(repo, "checkout", "-q", "-b", "feature")
        _git_q(repo, "commit", "-q", "--allow-empty", "-m", "diverge from main")
        _write_source_file(repo, "src/index.ts")
        _git_q(repo, "add", "src/index.ts")
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=repo) == "allow"

    # ---------------- Source-file definition --------------------------------

    def test_test_only_change_skips(self, feature_clone):
        _write_source_file(feature_clone, "packages/demo/src/index.test.ts")
        _git_q(feature_clone, "add", "packages/demo/src/index.test.ts")
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=feature_clone) == "allow"

    def test_docs_only_change_skips(self, feature_clone):
        _write_source_file(feature_clone, "packages/demo/README.md", "docs\n")
        _git_q(feature_clone, "add", "packages/demo/README.md")
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=feature_clone) == "allow"

    @pytest.mark.parametrize(
        "rel_path",
        [
            "packages/demo/src/__tests__/index.js",
            "packages/demo/test/index.js",
            "packages/demo/tests/index.js",
            "packages/demo/dist/index.js",
            "packages/demo/build/index.js",
            "packages/demo/node_modules/dep/index.js",
            "packages/demo/.github/workflows/ci.js",
            "packages/demo/.eslintrc.js",
        ],
    )
    def test_excluded_directories_and_dotfiles_skip(self, feature_clone, rel_path):
        _write_source_file(feature_clone, rel_path)
        _git_q(feature_clone, "add", rel_path)
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=feature_clone) == "allow"

    @pytest.mark.parametrize("extension", [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"])
    def test_gated_extension_denies_without_bump(self, feature_clone, extension):
        _write_source_file(feature_clone, f"packages/demo/src/index{extension}")
        _git_q(feature_clone, "add", f"packages/demo/src/index{extension}")
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=feature_clone) == "deny"

    # ---------------- Monorepo package detection ----------------------------

    def test_monorepo_nearest_package_resolution(self, tmp_path):
        # A local main/master distinct from HEAD is required for BASE
        # resolution to find anything determinable (see the fail-open path
        # tested below) — commit the packages on main, then branch and
        # diverge with an empty commit before the change under test.
        repo = tmp_path / "monorepo"
        repo.mkdir()
        _git_q(repo, "init", "-q", "-b", "main")
        _git_q(repo, "config", "user.email", "t@t.com")
        _git_q(repo, "config", "user.name", "t")
        _commit_package(repo, "packages/foo", "1.0.0", message="init foo")
        _commit_package(repo, "packages/bar", "1.0.0", message="init bar")
        _git_q(repo, "checkout", "-q", "-b", "feature")
        _git_q(repo, "commit", "-q", "--allow-empty", "-m", "diverge from main")
        _write_source_file(repo, "packages/foo/src/deep/nested/index.ts")
        _git_q(repo, "add", "packages/foo/src/deep/nested/index.ts")
        reason = run_hook_reason(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=repo)
        assert reason is not None, "hook allowed silently; expected deny"
        assert "packages/foo" in reason
        assert "packages/bar" not in reason

    def test_monorepo_partial_bump_denies_only_unbumped_package(self, tmp_path):
        repo = tmp_path / "monorepo-partial"
        repo.mkdir()
        _git_q(repo, "init", "-q", "-b", "main")
        _git_q(repo, "config", "user.email", "t@t.com")
        _git_q(repo, "config", "user.name", "t")
        _commit_package(repo, "packages/foo", "1.0.0", message="init foo")
        _commit_package(repo, "packages/bar", "1.0.0", message="init bar")
        _git_q(repo, "checkout", "-q", "-b", "feature")
        _git_q(repo, "commit", "-q", "--allow-empty", "-m", "diverge from main")
        _write_source_file(repo, "packages/foo/src/index.ts")
        _git_q(repo, "add", "packages/foo/src/index.ts")
        _write_source_file(repo, "packages/bar/src/index.ts")
        _git_q(repo, "add", "packages/bar/src/index.ts")
        # Bump only packages/bar.
        _write_package_json(repo, "packages/bar", "1.1.0")
        _git_q(repo, "add", _package_json_rel("packages/bar"))
        reason = run_hook_reason(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=repo)
        assert reason is not None, "hook allowed silently; expected deny"
        assert "packages/foo" in reason
        assert "packages/bar" not in reason

    def test_new_package_added_with_source_allows(self, tmp_path):
        # Branch off and diverge from main so BASE resolves to a real
        # merge-base — otherwise this would vacuously pass via the
        # no-merge-base fail-open path rather than the new-package skip.
        repo = tmp_path / "new-package-repo"
        repo.mkdir()
        _git_q(repo, "init", "-q", "-b", "main")
        _git_q(repo, "config", "user.email", "t@t.com")
        _git_q(repo, "config", "user.name", "t")
        (repo / "file.txt").write_text("first\n")
        _git_q(repo, "add", "file.txt")
        _git_q(repo, "commit", "-qm", "init")
        _git_q(repo, "checkout", "-q", "-b", "feature")
        _git_q(repo, "commit", "-q", "--allow-empty", "-m", "diverge from main")
        _write_package_json(repo, "packages/brand-new", "1.0.0")
        _write_source_file(repo, "packages/brand-new/src/index.ts")
        _git_q(repo, "add", _package_json_rel("packages/brand-new"), "packages/brand-new/src/index.ts")
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=repo) == "allow"

    def test_package_removed_on_branch_allows(self, feature_clone):
        _write_source_file(feature_clone, "packages/demo/src/index.ts")
        _git_q(feature_clone, "add", "packages/demo/src/index.ts")
        _git_q(feature_clone, "commit", "-qm", "add source")
        _git_q(feature_clone, "rm", "-rq", "packages/demo")
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=feature_clone) == "allow"

    # ---------------- Fail-closed inputs -----------------------------------

    def test_malformed_staged_package_json_denies(self, feature_clone):
        package_json = feature_clone / _package_json_rel("packages/demo")
        package_json.write_text("not json")
        _write_source_file(feature_clone, "packages/demo/src/index.ts")
        _git_q(feature_clone, "add", _package_json_rel("packages/demo"), "packages/demo/src/index.ts")
        reason = run_hook_reason(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=feature_clone)
        assert reason is not None, "hook allowed silently; expected deny"

    def test_missing_version_key_denies(self, feature_clone):
        _write_package_json(feature_clone, "packages/demo", None)
        _write_source_file(feature_clone, "packages/demo/src/index.ts")
        _git_q(feature_clone, "add", _package_json_rel("packages/demo"), "packages/demo/src/index.ts")
        reason = run_hook_reason(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=feature_clone)
        assert reason is not None, "hook allowed silently; expected deny"
        assert "missing or has no 'version' key" in reason

    @pytest.mark.parametrize("bad_version", ["v1.1.0", "1.1", "1.1.0-beta"])
    def test_non_numeric_dotted_version_denies(self, feature_clone, bad_version):
        _write_package_json(feature_clone, "packages/demo", bad_version)
        _write_source_file(feature_clone, "packages/demo/src/index.ts")
        _git_q(feature_clone, "add", _package_json_rel("packages/demo"), "packages/demo/src/index.ts")
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=feature_clone) == "deny"

    def test_multi_digit_minor_version_allows_numeric_bump(self, tmp_path):
        """base 1.9.0 -> index 1.10.0 is a numeric bump despite comparing as
        lexicographically smaller than 1.9.0 — guards against a naive string
        comparison mistaking '1.10.0' for a decrease."""
        repo = tmp_path / "multi-digit-repo"
        repo.mkdir()
        _git_q(repo, "init", "-q", "-b", "main")
        _git_q(repo, "config", "user.email", "t@t.com")
        _git_q(repo, "config", "user.name", "t")
        _commit_package(repo, "packages/demo", "1.9.0", message="init")
        _git_q(repo, "checkout", "-q", "-b", "feature")
        _git_q(repo, "commit", "-q", "--allow-empty", "-m", "diverge from main")
        _write_source_file(repo, "packages/demo/src/index.ts")
        _write_package_json(repo, "packages/demo", "1.10.0")
        _git_q(repo, "add", "packages/demo/src/index.ts", _package_json_rel("packages/demo"))
        assert run_hook_advisory(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=repo) == "allow"

    def test_multi_digit_minor_version_denies_numeric_decrease(self, tmp_path):
        """base 1.10.0 -> index 1.9.0 is a numeric decrease despite comparing
        as lexicographically greater — the reverse-direction guard for the
        case above."""
        repo = tmp_path / "multi-digit-repo-reverse"
        repo.mkdir()
        _git_q(repo, "init", "-q", "-b", "main")
        _git_q(repo, "config", "user.email", "t@t.com")
        _git_q(repo, "config", "user.name", "t")
        _commit_package(repo, "packages/demo", "1.10.0", message="init")
        _git_q(repo, "checkout", "-q", "-b", "feature")
        _git_q(repo, "commit", "-q", "--allow-empty", "-m", "diverge from main")
        _write_source_file(repo, "packages/demo/src/index.ts")
        _write_package_json(repo, "packages/demo", "1.9.0")
        _git_q(repo, "add", "packages/demo/src/index.ts", _package_json_rel("packages/demo"))
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=repo) == "deny"

    # ---------------- Command/tool/repo gates -------------------------------

    def test_non_package_file_changed_allows(self, isolated_home, git_repo):
        # git_repo already has file.txt staged; no package touched.
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

    def test_chained_add_commit_denies_without_bump(self, feature_clone):
        _write_source_file(feature_clone, "packages/demo/src/index.ts")
        _git_q(feature_clone, "add", "packages/demo/src/index.ts")
        assert (
            run_hook(VERSION_BUMP_HOOK, bash_input("git add file.txt && git commit -m foo"), cwd=feature_clone)
            == "deny"
        )

    def test_chained_add_commit_allows_with_bump(self, feature_clone):
        _write_source_file(feature_clone, "packages/demo/src/index.ts")
        _write_package_json(feature_clone, "packages/demo", "1.1.0")
        _git_q(feature_clone, "add", "packages/demo/src/index.ts", _package_json_rel("packages/demo"))
        assert (
            run_hook_advisory(
                VERSION_BUMP_HOOK, bash_input("git add file.txt && git commit -m foo"), cwd=feature_clone
            )
            == "allow"
        )

    # ---------------- Fail-open on indeterminate git state ------------------

    def test_outside_git_repo_allows(self, isolated_home, tmp_path):
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=non_repo) == "allow"

    def test_no_merge_base_determinable_allows(self, tmp_path):
        """No origin remote and no local main/master distinct from HEAD — the
        BASE resolution ladder bottoms out with nothing determinable. Unlike
        plugin-semver's degraded BASE=HEAD fallback, this hook fails open
        rather than risk a false deny on an unverifiable baseline."""
        repo = tmp_path / "no-merge-base-repo"
        repo.mkdir()
        _git_q(repo, "init", "-q", "-b", "main")
        _git_q(repo, "config", "user.email", "t@t.com")
        _git_q(repo, "config", "user.name", "t")
        _commit_package(repo, "packages/demo", "1.0.0", message="init")
        _write_source_file(repo, "packages/demo/src/index.ts")
        _git_q(repo, "add", "packages/demo/src/index.ts")
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=repo) == "allow"

    def test_detached_head_allows(self, feature_clone):
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=feature_clone, capture_output=True, text=True, check=True
        ).stdout.strip()
        _git_q(feature_clone, "checkout", "-q", head_sha)
        _write_source_file(feature_clone, "packages/demo/src/index.ts")
        _git_q(feature_clone, "add", "packages/demo/src/index.ts")
        assert run_hook(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=feature_clone) == "allow"

    # ---------------- Merge-base resolution ---------------------------------

    def test_merge_base_one_bump_per_branch_allows_later_untouched_commit(self, feature_clone):
        """Load-bearing case: a bump landed in an earlier commit on the
        feature branch; a later commit touches the same package again with
        no further bump. The merge-base-vs-index comparison correctly
        allows, since BASE (merge-base with origin/main) predates the bump."""
        _write_package_json(feature_clone, "packages/demo", "1.1.0")
        source = feature_clone / "packages" / "demo" / "src" / "index.ts"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("v1\n")
        _git_q(feature_clone, "add", _package_json_rel("packages/demo"), "packages/demo/src/index.ts")
        _git_q(feature_clone, "commit", "-qm", "bump and add source")

        # Later, uncommitted change touching the same package again — no further bump.
        source.write_text("v1\nv2\n")
        _git_q(feature_clone, "add", "packages/demo/src/index.ts")

        # The staged version (1.1.0) is still strictly greater than BASE's
        # 1.0.0, so this passes — with the propagate reminder, hence
        # run_hook_advisory rather than run_hook.
        assert (
            run_hook_advisory(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=feature_clone)
            == "allow"
        )

    def test_merge_base_resolved_via_local_main_fallback(self, tmp_path):
        """No origin remote; a local `main` distinct from HEAD (`feature`)
        is the fallback BASE. Advancing feature past main first forces the
        resolution through the merge-base(HEAD, refs/heads/main) fallback
        branch rather than the no-merge-base fail-open path."""
        repo = tmp_path / "local-main-repo"
        repo.mkdir()
        _git_q(repo, "init", "-q", "-b", "main")
        _git_q(repo, "config", "user.email", "t@t.com")
        _git_q(repo, "config", "user.name", "t")
        _commit_package(repo, "packages/demo", "1.0.0", message="init")
        _git_q(repo, "checkout", "-q", "-b", "feature")
        _write_source_file(repo, "packages/demo/src/index.ts")
        _git_q(repo, "add", "packages/demo/src/index.ts")
        _git_q(repo, "commit", "-qm", "add source on feature")

        _write_package_json(repo, "packages/demo", "1.1.0")
        _git_q(repo, "add", _package_json_rel("packages/demo"))
        assert (
            run_hook_advisory(VERSION_BUMP_HOOK, bash_input("git commit -m foo"), cwd=repo) == "allow"
        )

    # ---------------- Drift guard -------------------------------------------

    def test_npm_semver_lib_sh_parses_tool_input_same_as_stowed_lib_sh(self):
        """The plugin's trimmed _lib.sh must behave identically to the stowed
        copy for the one function this hook actually sources:
        _lib_parse_tool_input_or_deny (which itself calls _lib_jq).

        A project-scope install of npm-semver has no stowed
        ~/.claude/hooks/_lib.sh available, so the plugin ships its own trimmed
        copy (require-npm-version-bump.sh needs only these two helpers — no
        git helpers, no marker helpers, no worktree-enforcement helpers; see
        plugins/npm-semver/hooks/_lib.sh's header). A behavioral check here —
        not a whole-file byte comparison — is the right invariant: whole-file
        identity would force this plugin to carry, and re-sync on every
        change to, worktree/git-enforcement code it never calls.
        """
        harness = (
            'emit_deny() {{ printf "DENY:%s\\n" "$1"; exit 0; }}; '
            '. "{lib}"; '
            '_lib_parse_tool_input_or_deny "test-msg"; '
            'printf "OK:%s:%s\\n" "$TOOL_NAME" "$COMMAND"'
        )
        payload = '{"tool_name":"Bash","tool_input":{"command":"git commit -m foo"}}'
        plugin_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_NPM_SEMVER_LIB)],
            input=payload, capture_output=True, text=True, check=False,
        )
        stowed_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_STOWED_LIB)],
            input=payload, capture_output=True, text=True, check=False,
        )
        assert plugin_result.stdout == stowed_result.stdout, (
            "plugins/npm-semver/hooks/_lib.sh's _lib_parse_tool_input_or_deny "
            "behaves differently than the stowed claude/.claude/hooks/_lib.sh copy — "
            f"plugin: {plugin_result.stdout!r}, stowed: {stowed_result.stdout!r}"
        )

    def test_npm_semver_lib_sh_jq_fallback_matches_stowed_lib_sh(self, tmp_path):
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
            ["bash", "-c", harness.format(lib=_NPM_SEMVER_LIB)],
            input=payload, capture_output=True, text=True, check=False, env=env,
        )
        stowed_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_STOWED_LIB)],
            input=payload, capture_output=True, text=True, check=False, env=env,
        )
        assert plugin_result.stdout == stowed_result.stdout, (
            "plugins/npm-semver/hooks/_lib.sh's _lib_jq timeout-absent fallback "
            "behaves differently than the stowed claude/.claude/hooks/_lib.sh copy — "
            f"plugin: {plugin_result.stdout!r}, stowed: {stowed_result.stdout!r}"
        )
