"""Tests for select-tests.py.

Tests here follow test-conventions' "narrowest double that covers the
test's intent." select_pytest_targets is tested against synthetic
changed-path lists (no real git calls) since the rule-table mapping doesn't
need git. compute_changed_paths is tested against throwaway git fixture
repos (via conftest's _init_repo/_commit/_make_repo_with_remote) since the
real merge-base/dirty-tree plumbing can't be faked without losing the thing
under test.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import _commit, _init_repo, _make_repo_with_remote

_SCRIPT = Path(__file__).parent.parent / "select-tests.py"
_spec = importlib.util.spec_from_file_location("select_tests", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)

# Anchors the path-fidelity and glob-expansion tests to this actual
# checkout's root, via the module's own repo-root resolution (dogfooding
# the production code path rather than hand-deriving a parents[N] guess).
_REPO_ROOT = _mod.resolve_repo_root(cwd=Path(__file__).parent)


class TestSelectPytestTargets:
    def test_hooks_change_selects_hooks_tests_and_transcript_analysis(self):
        result = _mod.select_pytest_targets(["claude/.claude/hooks/deny-example.sh"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.HOOKS_TESTS_DIR, _mod.TRANSCRIPT_ANALYSIS_TEST_GLOB}

    def test_scripts_change_selects_scripts_tests_only(self):
        result = _mod.select_pytest_targets(["claude/.claude/scripts/mark-terminal.py"])
        assert result.is_full_suite is False
        assert result.target_paths == (_mod.SCRIPTS_TESTS_DIR,)

    def test_sibling_directory_sharing_scripts_dir_prefix_does_not_match(self):
        """claude/.claude/scripts-other/ shares SCRIPTS_DIR's string prefix
        but is a distinct sibling directory -- _is_under's directory-boundary
        check (path == directory or startswith directory + "/") must not
        treat it as under claude/.claude/scripts."""
        result = _mod.select_pytest_targets(["claude/.claude/scripts-other/x.py"])
        assert result.is_full_suite is True
        assert result.reason == "unmatched-path"

    def test_skill_md_change_selects_skills_tests_and_transcript_analysis(self):
        result = _mod.select_pytest_targets(["claude/.claude/skills/test-conventions/SKILL.md"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.SKILLS_TESTS_DIR, _mod.TRANSCRIPT_ANALYSIS_TEST_GLOB}

    def test_non_skill_md_file_under_skills_is_unmatched_and_falls_open(self):
        """Only SKILL.md changes trigger the skills domain rule -- a sibling
        file (REFERENCES.md, ROUTING.md) under the same skill directory does
        not, and falls open instead."""
        result = _mod.select_pytest_targets(["claude/.claude/skills/test-conventions/REFERENCES.md"])
        assert result.is_full_suite is True
        assert result.reason == "unmatched-path"

    def test_lovable_cloud_change_selects_lovable_cloud_tests(self):
        result = _mod.select_pytest_targets(["plugins/lovable-cloud/README.md"])
        assert result.is_full_suite is False
        assert result.target_paths == (_mod.LOVABLE_CLOUD_TESTS_DIR,)

    def test_lovable_cloud_plugin_manifest_change_also_selects_skills_tests(self):
        """test_plugin_manifests.py (in SKILLS_TESTS_DIR, not the
        lovable-cloud domain) globs every plugin's .claude-plugin/plugin.json
        by path. Without this cross-domain exception, LOVABLE_CLOUD_DIR's
        broad domain rule claims the path first and the dependent test goes
        unrun."""
        result = _mod.select_pytest_targets([_mod.LOVABLE_CLOUD_PLUGIN_MANIFEST])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.LOVABLE_CLOUD_TESTS_DIR, _mod.SKILLS_TESTS_DIR}

    def test_lovable_cloud_hooks_change_also_selects_hooks_tests(self):
        """test_hook_alignment.py and test_lib.py both glob
        plugins/*/hooks/*.sh into HOOKS_TESTS_DIR's checks. Without this
        cross-domain exception, LOVABLE_CLOUD_DIR's broad domain rule claims
        the path first and those checks go unrun."""
        result = _mod.select_pytest_targets(["plugins/lovable-cloud/hooks/deny-example.sh"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.LOVABLE_CLOUD_TESTS_DIR, _mod.HOOKS_TESTS_DIR}

    def test_lovable_cloud_skills_change_also_selects_skills_tests(self):
        """test_skills.py globs plugins/*/skills/*/SKILL.md and
        plugins/*/skills/**/REFERENCES.md into SKILLS_TESTS_DIR's checks.
        Same cross-domain shape as the hooks and plugin-manifest cases
        above."""
        result = _mod.select_pytest_targets(["plugins/lovable-cloud/skills/lovable-cloud-knowledge/SKILL.md"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.LOVABLE_CLOUD_TESTS_DIR, _mod.SKILLS_TESTS_DIR}

    def test_lovable_cloud_agents_change_also_selects_skills_tests(self):
        """test_skills.py globs plugins/*/agents/*.md into SKILLS_TESTS_DIR's
        checks. plugins/lovable-cloud/agents/ doesn't exist on disk today,
        but the rule must hold the moment it's added."""
        result = _mod.select_pytest_targets(["plugins/lovable-cloud/agents/reviewer.md"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.LOVABLE_CLOUD_TESTS_DIR, _mod.SKILLS_TESTS_DIR}

    def test_lovable_cloud_scripts_shell_file_change_also_selects_hooks_tests(self):
        """test_shellcheck.py lints every tracked shell script in the repo,
        not just claude/.claude/hooks/. plugins/lovable-cloud/scripts/ and
        plugins/lovable-cloud/lib/ both hold real shell scripts it covers,
        so a change there must also select HOOKS_TESTS_DIR."""
        result = _mod.select_pytest_targets(["plugins/lovable-cloud/scripts/new-migration"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.LOVABLE_CLOUD_TESTS_DIR, _mod.HOOKS_TESTS_DIR}

        result = _mod.select_pytest_targets(["plugins/lovable-cloud/lib/token-path.sh"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.LOVABLE_CLOUD_TESTS_DIR, _mod.HOOKS_TESTS_DIR}

    def test_skill_management_scripts_change_selects_skills_tests(self):
        result = _mod.select_pytest_targets(["plugins/skill-management/scripts/validate_skill_structure.py"])
        assert result.is_full_suite is False
        assert result.target_paths == (_mod.SKILLS_TESTS_DIR,)

    def test_skill_evals_runner_change_selects_skills_tests(self):
        result = _mod.select_pytest_targets([_mod.SKILL_EVALS_RUNNER])
        assert result.is_full_suite is False
        assert result.target_paths == (_mod.SKILLS_TESTS_DIR,)

    def test_handoff_skill_md_change_also_selects_scripts_tests(self):
        """test_check_handoff.py (SCRIPTS_TESTS_DIR) reads
        HANDOFF_SKILL_MD's exact file by path, not by import. Without this
        cross-domain exception, the skills domain rule claims the path first
        and test_check_handoff.py goes unrun."""
        result = _mod.select_pytest_targets([_mod.HANDOFF_SKILL_MD])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.SKILLS_TESTS_DIR, _mod.TRANSCRIPT_ANALYSIS_TEST_GLOB, _mod.SCRIPTS_TESTS_DIR}

    def test_skill_management_hooks_and_skills_change_is_unmatched_and_falls_open(self):
        """No DOMAIN_RULES entry matches plugins/ broadly, only
        LOVABLE_CLOUD_DIR is scoped that way.

        So a hooks/*.sh or SKILL.md change under a non-lovable-cloud plugin
        falls open to the full suite rather than silently under-selecting.
        This locks in that safety net for skill-management specifically.
        """
        result = _mod.select_pytest_targets(["plugins/skill-management/hooks/require-skill-review.sh"])
        assert result.is_full_suite is True
        assert result.reason == "unmatched-path"

        result = _mod.select_pytest_targets(["plugins/skill-management/skills/skill-review/SKILL.md"])
        assert result.is_full_suite is True
        assert result.reason == "unmatched-path"

    def test_npm_semver_hooks_and_skills_change_is_unmatched_and_falls_open(self):
        """Same safety net as skill-management, for npm-semver."""
        result = _mod.select_pytest_targets(["plugins/npm-semver/hooks/require-npm-version-bump.sh"])
        assert result.is_full_suite is True
        assert result.reason == "unmatched-path"

        result = _mod.select_pytest_targets(["plugins/npm-semver/skills/npm-semver/SKILL.md"])
        assert result.is_full_suite is True
        assert result.reason == "unmatched-path"

    def test_plugin_semver_hooks_and_skills_change_is_unmatched_and_falls_open(self):
        """Same safety net as skill-management, for plugin-semver."""
        result = _mod.select_pytest_targets(["plugins/plugin-semver/hooks/require-plugin-version-bump.sh"])
        assert result.is_full_suite is True
        assert result.reason == "unmatched-path"

        result = _mod.select_pytest_targets(["plugins/plugin-semver/skills/plugin-semver/SKILL.md"])
        assert result.is_full_suite is True
        assert result.reason == "unmatched-path"

    def test_claude_hook_review_skills_change_is_unmatched_and_falls_open(self):
        """Same safety net as skill-management, for claude-hook-review.

        This plugin has no hooks/ directory of its own, only skills/, so
        only the SKILL.md case applies.
        """
        result = _mod.select_pytest_targets(["plugins/claude-hook-review/skills/claude-hook-review/SKILL.md"])
        assert result.is_full_suite is True
        assert result.reason == "unmatched-path"

    def test_multi_domain_change_unions_both_target_sets(self):
        result = _mod.select_pytest_targets([
            "claude/.claude/scripts/mark-terminal.py",
            "plugins/lovable-cloud/README.md",
        ])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.SCRIPTS_TESTS_DIR, _mod.LOVABLE_CLOUD_TESTS_DIR}

    def test_helpers_py_global_trigger_forces_full_suite(self):
        result = _mod.select_pytest_targets(["claude/.claude/tests/helpers.py"])
        assert result.is_full_suite is True
        assert result.reason == "global-trigger"
        assert result.target_paths == _mod.FULL_SUITE_TARGETS

    def test_pyproject_toml_global_trigger_forces_full_suite(self):
        result = _mod.select_pytest_targets(["pyproject.toml"])
        assert result.is_full_suite is True
        assert result.reason == "global-trigger"

    def test_select_tests_self_change_global_trigger_forces_full_suite(self):
        result = _mod.select_pytest_targets([_mod.SELECT_TESTS_SCRIPT])
        assert result.is_full_suite is True
        assert result.reason == "global-trigger"

    def test_global_trigger_precedence_over_a_matched_domain_change(self):
        """A global-trigger path mixed into a changed-set that also has a
        matched domain file still falls back to the full suite -- the
        domain match must not suppress the global trigger."""
        result = _mod.select_pytest_targets([
            "pyproject.toml",
            "claude/.claude/scripts/mark-terminal.py",
        ])
        assert result.is_full_suite is True
        assert result.reason == "global-trigger"

    def test_unmatched_path_falls_open_to_full_suite(self):
        result = _mod.select_pytest_targets(["README.md"])
        assert result.is_full_suite is True
        assert result.reason == "unmatched-path"
        assert result.target_paths == _mod.FULL_SUITE_TARGETS

    def test_empty_diff_falls_open_to_full_suite(self):
        result = _mod.select_pytest_targets([])
        assert result.is_full_suite is True
        assert result.reason == "empty-diff"
        assert result.target_paths == _mod.FULL_SUITE_TARGETS


class TestBuildPytestArgv:
    def test_plain_directory_targets_pass_through_unchanged(self):
        argv = _mod.build_pytest_argv(
            [_mod.HOOKS_TESTS_DIR, _mod.SCRIPTS_TESTS_DIR], [], repo_root=_REPO_ROOT,
        )
        assert argv == [_mod.HOOKS_TESTS_DIR, _mod.SCRIPTS_TESTS_DIR]

    def test_glob_target_expands_to_concrete_sorted_files_on_disk(self):
        argv = _mod.build_pytest_argv([_mod.TRANSCRIPT_ANALYSIS_TEST_GLOB], [], repo_root=_REPO_ROOT)
        assert argv, "glob target must expand to at least one file"
        assert argv == sorted(argv)
        for path in argv:
            assert path.startswith("claude/.claude/scripts/tests/test_transcript_analysis")
            assert path.endswith(".py")

    def test_passthrough_args_appended_after_target_paths(self):
        argv = _mod.build_pytest_argv(
            [_mod.SCRIPTS_TESTS_DIR], ["-k", "select_tests", "-v"], repo_root=_REPO_ROOT,
        )
        assert argv == [_mod.SCRIPTS_TESTS_DIR, "-k", "select_tests", "-v"]


class TestRunPytest:
    def test_invokes_resolved_pytest_executable_with_constructed_argv(self, tmp_path, monkeypatch):
        """No .venv-shaped sibling next to sys.executable in this fixture,
        so the fallback bare "pytest" name is what should reach the stubbed
        run() -- the real pytest subprocess is never shelled out to."""
        recorded = {}

        class _FakeCompletedProcess:
            returncode = 7

        def fake_run(cmd, *, cwd, check):
            recorded["cmd"] = cmd
            recorded["cwd"] = cwd
            recorded["check"] = check
            return _FakeCompletedProcess()

        monkeypatch.setattr(sys, "executable", str(tmp_path / "python3"))
        exit_code = _mod.run_pytest(["claude/.claude/hooks/tests"], cwd=tmp_path, run=fake_run)

        assert exit_code == 7
        assert recorded["cmd"] == ["pytest", "claude/.claude/hooks/tests"]
        assert recorded["cwd"] == tmp_path
        assert recorded["check"] is False

    def test_resolves_pytest_from_the_sys_executable_sibling_when_present(self, tmp_path, monkeypatch):
        venv_bin = tmp_path / "venv_bin"
        venv_bin.mkdir()
        fake_python = venv_bin / "python3"
        fake_python.write_text("")
        fake_pytest = venv_bin / "pytest"
        fake_pytest.write_text("")
        monkeypatch.setattr(sys, "executable", str(fake_python))

        recorded = {}

        class _FakeCompletedProcess:
            returncode = 0

        def fake_run(cmd, *, cwd, check):
            recorded["cmd"] = cmd
            return _FakeCompletedProcess()

        _mod.run_pytest([], cwd=tmp_path, run=fake_run)

        assert recorded["cmd"][0] == str(fake_pytest)


class TestRuleTablePathFidelity:
    """Catches silent drift if a rule-table target directory is later
    renamed or a glob pattern stops matching anything."""

    def _all_targets(self) -> set[str]:
        targets = set(_mod.FULL_SUITE_TARGETS)
        for _, domain_targets in _mod.DOMAIN_RULES:
            targets.update(domain_targets)
        for _, exception_targets in _mod.CROSS_DOMAIN_EXCEPTIONS:
            targets.update(exception_targets)
        return targets

    def test_every_directory_target_exists_on_disk(self):
        directory_targets = [t for t in self._all_targets() if "*" not in t]
        assert directory_targets, "expected at least one plain directory target"
        for target in directory_targets:
            assert (_REPO_ROOT / target).is_dir(), f"{target} does not exist as a directory"

    def test_every_glob_target_matches_at_least_one_file_on_disk(self):
        glob_targets = [t for t in self._all_targets() if "*" in t]
        assert glob_targets, "expected at least one glob-pattern target"
        for target in glob_targets:
            assert list(_REPO_ROOT.glob(target)), f"{target} matched no files on disk"

    def test_every_global_trigger_path_exists_on_disk(self):
        for path in _mod.GLOBAL_TRIGGER_PATHS:
            assert (_REPO_ROOT / path).is_file(), f"{path} does not exist as a file"

    def test_lovable_cloud_plugin_manifest_path_exists_on_disk(self):
        assert (_REPO_ROOT / _mod.LOVABLE_CLOUD_PLUGIN_MANIFEST).is_file()


class TestComputeChangedPathsGitSmoke:
    """Exercises the real git codepath against throwaway fixture repos
    (not a local-only stand-in for merge-base(HEAD, origin/main))."""

    def test_committed_diff_against_merge_base_is_included(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path)
        subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=local, check=True)
        hooks_dir = local / "claude" / ".claude" / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "new-hook.sh").write_text("#!/usr/bin/env bash\n")
        subprocess.run(["git", "add", "claude/.claude/hooks/new-hook.sh"], cwd=local, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add hook"], cwd=local, check=True)

        changed = _mod.compute_changed_paths(local)

        assert "claude/.claude/hooks/new-hook.sh" in changed

    def test_modified_tracked_file_is_included(self, tmp_path):
        """An uncommitted modification to an already-tracked, already-pushed
        file must show up in the computed changed-set.

        Exercises `git diff --name-only HEAD`'s tracked-modification path,
        distinct from `test_working_tree_dirty_file_is_included` below,
        which exercises `git ls-files --others`'s untracked-file path.
        """
        local, _bare = _make_repo_with_remote(tmp_path)
        hooks_dir = local / "claude" / ".claude" / "hooks"
        hooks_dir.mkdir(parents=True)
        tracked_file = hooks_dir / "existing-hook.sh"
        tracked_file.write_text("#!/usr/bin/env bash\necho original\n")
        subprocess.run(["git", "add", "claude/.claude/hooks/existing-hook.sh"], cwd=local, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add hook"], cwd=local, check=True)
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=local, check=True)

        tracked_file.write_text("#!/usr/bin/env bash\necho modified\n")

        changed = _mod.compute_changed_paths(local)

        assert "claude/.claude/hooks/existing-hook.sh" in changed

    def test_working_tree_dirty_file_is_included(self, tmp_path):
        """An uncommitted (here: untracked) file must show up in the
        computed changed-set even with no committed divergence from
        origin/main at all -- this is the half that makes the tool useful
        pre-commit."""
        local, _bare = _make_repo_with_remote(tmp_path)
        scripts_dir = local / "claude" / ".claude" / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "new-script.py").write_text("")

        changed = _mod.compute_changed_paths(local)

        assert "claude/.claude/scripts/new-script.py" in changed

    def test_non_ascii_path_falls_open_to_full_suite(self, tmp_path):
        """git's default core.quotePath=true escapes non-ASCII path bytes in
        --name-only output, and _run_git doesn't override it. A non-ASCII
        changed path therefore doesn't string-match any domain predicate.
        That's the safe direction, pinned here as intentional rather than
        left as an unverified comment claim."""
        local, _bare = _make_repo_with_remote(tmp_path)
        # Pinned explicitly rather than inherited, matching _init_repo's own
        # --initial-branch=main precedent.
        # This test's assertion depends on core.quotePath's *default* value,
        # not whatever the executing machine's ambient git config sets.
        subprocess.run(["git", "config", "core.quotePath", "true"], cwd=local, check=True)
        scripts_dir = local / "claude" / ".claude" / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "café.py").write_text("")

        changed = _mod.compute_changed_paths(local)
        result = _mod.select_pytest_targets(changed)

        assert result.is_full_suite is True
        assert result.reason == "unmatched-path"

    def test_deleted_tracked_file_is_included(self, tmp_path):
        """A deleted-but-committed-then-deleted-again path must still show
        up in the computed changed-set. `git diff --name-only` reports
        deletions the same way it reports modifications."""
        local, _bare = _make_repo_with_remote(tmp_path)
        hooks_dir = local / "claude" / ".claude" / "hooks"
        hooks_dir.mkdir(parents=True)
        tracked_file = hooks_dir / "removed-hook.sh"
        tracked_file.write_text("#!/usr/bin/env bash\n")
        subprocess.run(["git", "add", "claude/.claude/hooks/removed-hook.sh"], cwd=local, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add hook"], cwd=local, check=True)
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=local, check=True)

        tracked_file.unlink()

        changed = _mod.compute_changed_paths(local)

        assert "claude/.claude/hooks/removed-hook.sh" in changed

    def test_merge_base_lookup_failure_raises_for_caller_to_fall_open(self, tmp_path):
        """No origin remote configured at all (so origin/main can't
        resolve) plus a detached HEAD -- compute_changed_paths must raise
        rather than silently produce a bad selection or crash."""
        local = tmp_path / "detached-no-origin"
        _init_repo(local)
        _commit(local, "init")
        subprocess.run(["git", "checkout", "-q", "--detach", "HEAD"], cwd=local, check=True)

        with pytest.raises(_mod.GitDiffUnavailable):
            _mod.compute_changed_paths(local)


class _FakeCompletedProcess:
    def __init__(self, *, returncode: int = 0, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout


class TestResolveRepoRoot:
    def test_falls_back_to_cwd_when_git_rev_parse_fails(self, tmp_path):
        def fake_run(cmd, **kwargs):
            return _FakeCompletedProcess(returncode=1, stdout="")

        result = _mod.resolve_repo_root(cwd=tmp_path, run=fake_run)

        assert result == tmp_path

    def test_returns_git_rev_parse_stdout_when_it_succeeds(self, tmp_path):
        """Direct unit test of the success branch, distinct from the
        module-level _REPO_ROOT dogfood call above, which exercises the
        same branch only indirectly against a real git checkout."""
        toplevel = tmp_path / "some-repo-root"

        def fake_run(cmd, **kwargs):
            return _FakeCompletedProcess(stdout=f"{toplevel}\n")

        result = _mod.resolve_repo_root(cwd=tmp_path, run=fake_run)

        assert result == toplevel


class TestRunGitFailureModes:
    """_run_git's own exception-to-None mapping, exercised via the run= DI
    seam -- no real git subprocess involved."""

    def test_oserror_from_run_returns_none(self, tmp_path):
        def fake_run(cmd, **kwargs):
            raise OSError("git executable not found")

        result = _mod._run_git(["status"], cwd=tmp_path, run=fake_run)

        assert result is None

    def test_timeout_expired_from_run_returns_none(self, tmp_path):
        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=_mod._GIT_TIMEOUT_SECONDS)

        result = _mod._run_git(["status"], cwd=tmp_path, run=fake_run)

        assert result is None


class TestComputeChangedPathsFailureModes:
    """compute_changed_paths' GitDiffUnavailable branches for the dirty-diff
    and untracked-list calls, exercised via the run= DI seam.

    TestComputeChangedPathsGitSmoke's real-git fixtures cover the
    merge-base lookup failure but not these two calls.
    """

    def test_dirty_diff_failure_raises_git_diff_unavailable(self, tmp_path):
        def fake_run(cmd, **kwargs):
            if "merge-base" in cmd:
                return _FakeCompletedProcess(stdout="deadbeef\n")
            if "diff" in cmd and "HEAD" in cmd:
                return _FakeCompletedProcess(returncode=1)
            return _FakeCompletedProcess(stdout="")

        with pytest.raises(_mod.GitDiffUnavailable):
            _mod.compute_changed_paths(tmp_path, run=fake_run)

    def test_untracked_list_failure_raises_git_diff_unavailable(self, tmp_path):
        def fake_run(cmd, **kwargs):
            if "merge-base" in cmd:
                return _FakeCompletedProcess(stdout="deadbeef\n")
            if "ls-files" in cmd:
                return _FakeCompletedProcess(returncode=1)
            return _FakeCompletedProcess(stdout="")

        with pytest.raises(_mod.GitDiffUnavailable):
            _mod.compute_changed_paths(tmp_path, run=fake_run)


class TestMainComposition:
    """main()'s selection-to-invocation wiring, with compute_changed_paths
    and run_pytest both monkeypatched so neither a real git diff nor a real
    pytest subprocess runs."""

    def test_git_diff_unavailable_falls_back_to_full_suite(self, monkeypatch):
        def fake_compute_changed_paths(repo_root):
            raise _mod.GitDiffUnavailable("stub failure")

        recorded = {}

        def fake_run_pytest(pytest_argv, *, cwd):
            recorded["pytest_argv"] = pytest_argv
            return 0

        monkeypatch.setattr(_mod, "compute_changed_paths", fake_compute_changed_paths)
        monkeypatch.setattr(_mod, "run_pytest", fake_run_pytest)

        exit_code = _mod.main([])

        assert exit_code == 0
        assert recorded["pytest_argv"] == list(_mod.FULL_SUITE_TARGETS)

    def test_domain_selected_paths_are_passed_through_to_run_pytest(self, monkeypatch):
        def fake_compute_changed_paths(repo_root):
            return ["claude/.claude/scripts/mark-terminal.py"]

        recorded = {}

        def fake_run_pytest(pytest_argv, *, cwd):
            recorded["pytest_argv"] = pytest_argv
            return 0

        monkeypatch.setattr(_mod, "compute_changed_paths", fake_compute_changed_paths)
        monkeypatch.setattr(_mod, "run_pytest", fake_run_pytest)

        exit_code = _mod.main(["-k", "foo"])

        assert exit_code == 0
        assert recorded["pytest_argv"] == [_mod.SCRIPTS_TESTS_DIR, "-k", "foo"]
