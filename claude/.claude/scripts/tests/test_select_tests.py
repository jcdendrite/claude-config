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
        result = _mod.select_pytest_targets(["claude/.claude/hooks/deny-example.py"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.HOOKS_TESTS_DIR, _mod.TRANSCRIPT_ANALYSIS_TEST_GLOB}

    def test_hooks_dir_shell_script_change_also_selects_scripts_tests(self):
        """test_no_bash4_constructs.py (SCRIPTS_TESTS_DIR) recursively globs
        claude/.claude/ for *.sh files, picking up claude/.claude/hooks/.
        Without this cross-domain exception, that check goes unrun on a
        hooks-dir shell script change."""
        result = _mod.select_pytest_targets(["claude/.claude/hooks/deny-example.sh"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {
            _mod.HOOKS_TESTS_DIR, _mod.TRANSCRIPT_ANALYSIS_TEST_GLOB, _mod.SCRIPTS_TESTS_DIR,
        }

    def test_scripts_change_selects_scripts_tests_only(self):
        result = _mod.select_pytest_targets(["claude/.claude/scripts/mark-terminal.py"])
        assert result.is_full_suite is False
        assert result.target_paths == (_mod.SCRIPTS_TESTS_DIR,)

    def test_scripts_dir_shell_script_change_also_selects_hooks_and_skills_tests(self):
        """test_shellcheck.py (HOOKS_TESTS_DIR) lints every tracked shell
        script in the repo, including claude/.claude/scripts/*.sh.
        test_skills.py's test_scripts_are_executable (SKILLS_TESTS_DIR) also
        globs SCRIPTS_DIR/*.sh for an executable-bit check. Without these
        cross-domain exceptions, SCRIPTS_DIR's domain rule claims the path
        first and both checks go unrun."""
        result = _mod.select_pytest_targets(["claude/.claude/scripts/new-migration.sh"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.SCRIPTS_TESTS_DIR, _mod.HOOKS_TESTS_DIR, _mod.SKILLS_TESTS_DIR}

    def test_scripts_dir_nested_shell_script_change_also_selects_hooks_tests(self):
        """_is_under matches any depth under SCRIPTS_DIR, not just top-level --
        a shell script in a scripts subdirectory (e.g. lib/) must still pull
        in HOOKS_TESTS_DIR the same as a top-level one."""
        result = _mod.select_pytest_targets(["claude/.claude/scripts/lib/helper.sh"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.SCRIPTS_TESTS_DIR, _mod.HOOKS_TESTS_DIR, _mod.SKILLS_TESTS_DIR}

    def test_scripts_dir_extensionless_change_also_selects_hooks_tests(self):
        """test_shellcheck.py's own discovery (KNOWN_EXTENSIONLESS_SHELL_FILES)
        finds shell scripts by shebang, not just by .sh suffix -- an
        extensionless script under claude/.claude/scripts/ must select
        HOOKS_TESTS_DIR the same as a .sh-suffixed one."""
        result = _mod.select_pytest_targets(["claude/.claude/scripts/new-helper"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.SCRIPTS_TESTS_DIR, _mod.HOOKS_TESTS_DIR, _mod.SKILLS_TESTS_DIR}

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

    def test_lovable_cloud_agents_change_also_selects_skills_and_hooks_tests(self):
        """test_skills.py globs plugins/*/agents/*.md into SKILLS_TESTS_DIR's
        checks. test_agent_roster.py (HOOKS_TESTS_DIR) globs the same path
        for a cross-scope agent-name-collision check. plugins/lovable-cloud/agents/
        doesn't exist on disk today, but both rules must hold the moment it's added."""
        result = _mod.select_pytest_targets(["plugins/lovable-cloud/agents/reviewer.md"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {
            _mod.LOVABLE_CLOUD_TESTS_DIR, _mod.SKILLS_TESTS_DIR, _mod.HOOKS_TESTS_DIR,
        }

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

    def test_skill_management_scripts_shell_script_change_falls_open(self):
        """Scoped to .py files only, so a .sh file here (none exists today)
        must fall open to the full suite rather than be claimed by this
        narrow exception ahead of test_shellcheck.py's repo-wide sweep. This
        mirrors the fall-open safety net every other plugin's scripts/
        directory already gets."""
        result = _mod.select_pytest_targets(["plugins/skill-management/scripts/new-hook.sh"])
        assert result.is_full_suite is True
        assert result.reason == "unmatched-path"

    def test_skill_evals_runner_change_selects_skills_tests(self):
        result = _mod.select_pytest_targets([_mod.SKILL_EVALS_RUNNER])
        assert result.is_full_suite is False
        assert result.target_paths == (_mod.SKILLS_TESTS_DIR,)

    def test_handoff_skill_md_change_also_selects_scripts_and_hooks_tests(self):
        """test_check_handoff.py (SCRIPTS_TESTS_DIR) reads HANDOFF_SKILL_MD's
        exact file by path, not by import.
        test_restore_authorization_boundary_on_compact.py (HOOKS_TESTS_DIR)
        also reads it by path, asserting hook-named tokens are a subset of
        its §3.5 list. Without these cross-domain exceptions, the skills
        domain rule claims the path first and both checks go unrun."""
        result = _mod.select_pytest_targets([_mod.HANDOFF_SKILL_MD])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {
            _mod.SKILLS_TESTS_DIR, _mod.TRANSCRIPT_ANALYSIS_TEST_GLOB,
            _mod.SCRIPTS_TESTS_DIR, _mod.HOOKS_TESTS_DIR,
        }

    def test_code_review_skill_md_change_also_selects_hooks_tests(self):
        """test_reconciliation_block_consistency.py (HOOKS_TESTS_DIR) reads
        CODE_REVIEW_SKILL_MD's exact file by path to diff its Reconciliation
        block against plan-review/ROUTING.md. Without this cross-domain
        exception, the skills domain rule claims the path first and that
        check goes unrun."""
        result = _mod.select_pytest_targets([_mod.CODE_REVIEW_SKILL_MD])
        assert result.is_full_suite is False
        assert _mod.HOOKS_TESTS_DIR in result.target_paths

    def test_skill_management_hooks_and_skills_change_is_unmatched_and_falls_open(self):
        """Only LOVABLE_CLOUD_DIR is broadly scoped under plugins/, so a
        hooks/*.sh or SKILL.md change under any other plugin (here:
        skill-management) falls open to the full suite instead of
        silently under-selecting."""
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

    def test_agents_dir_change_also_selects_hooks_and_skills_tests(self):
        """test_agent_roster.py (HOOKS_TESTS_DIR) and test_skills.py
        (SKILLS_TESTS_DIR) both read claude/.claude/agents/*.md by path, not
        by import. Without this cross-domain exception, a change under
        claude/.claude/agents/ falls open to the full suite instead of
        selecting the two domains that actually depend on it."""
        result = _mod.select_pytest_targets(["claude/.claude/agents/code-writer.md"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.HOOKS_TESTS_DIR, _mod.SKILLS_TESTS_DIR}

    def test_rules_dir_change_also_selects_skills_tests(self):
        """test_rules_frontmatter.py (SKILLS_TESTS_DIR) rglobs
        claude/.claude/rules/*.md by path, not by import."""
        result = _mod.select_pytest_targets(["claude/.claude/rules/shell-script-conventions.md"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.SKILLS_TESTS_DIR}

    def test_github_actions_workflows_rule_md_change_also_selects_hooks_tests(self):
        """test_ci_path_filter.py (HOOKS_TESTS_DIR) reads this exact file by
        path, not by import -- a sibling rule file under the same directory
        does not need HOOKS_TESTS_DIR, only this one does."""
        result = _mod.select_pytest_targets([_mod.GITHUB_ACTIONS_WORKFLOWS_RULE_MD])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.SKILLS_TESTS_DIR, _mod.HOOKS_TESTS_DIR}

    def test_plans_dir_change_selects_no_tests(self):
        """No test reads any file under .claude/plans/ by path or
        subprocess, so a plan change is covered without pulling in the
        full-suite fallback."""
        result = _mod.select_pytest_targets([".claude/plans/some-plan.md"])
        assert result.is_full_suite is False
        assert result.target_paths == ()

    def test_plans_dir_sibling_directory_sharing_prefix_does_not_match(self):
        """_is_under's directory-boundary check requires an exact match or a
        `directory + "/"` prefix. .claude/plans-archive/ shares PLANS_DIR's
        string prefix but is a distinct sibling directory, so it must not
        match."""
        result = _mod.select_pytest_targets([".claude/plans-archive/x.md"])
        assert result.is_full_suite is True
        assert result.reason == "unmatched-path"

    def test_changelog_md_change_selects_no_tests(self):
        """test_ci_path_filter.py is the only test that references
        CHANGELOG.md. It matches it as a static CI ignore-paths allowlist
        entry and never reads its content."""
        result = _mod.select_pytest_targets([_mod.CHANGELOG_MD])
        assert result.is_full_suite is False
        assert result.target_paths == ()

    def test_transcript_analysis_doc_md_change_selects_no_tests(self):
        """No test reads docs/transcript-analysis.md's content by path.
        Every existing reference is a source-code comment citing the doc for
        human readers."""
        result = _mod.select_pytest_targets([_mod.TRANSCRIPT_ANALYSIS_DOC_MD])
        assert result.is_full_suite is False
        assert result.target_paths == ()

    def test_transcript_analysis_architecture_doc_md_change_selects_scripts_tests(self):
        """test_transcript_analysis_architecture_doc.py (SCRIPTS_TESTS_DIR)
        reads this exact file's content by path to pin its module list."""
        result = _mod.select_pytest_targets([_mod.TRANSCRIPT_ANALYSIS_ARCHITECTURE_DOC_MD])
        assert result.is_full_suite is False
        assert result.target_paths == (_mod.SCRIPTS_TESTS_DIR,)


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

    def test_every_real_top_level_claude_dir_is_mapped_or_allowlisted(self):
        """Existing tests above validate declared table entries -- that a
        target exists, that a glob matches something. None of them validate
        completeness against the real tree: a new top-level directory under
        claude/.claude/ could sit unmapped indefinitely with no test catching
        it. Catches only an unnamed directory, not a misregistered one --
        MAPPED_TOP_LEVEL_DIRS membership is not cross-checked against any
        real DOMAIN_RULES/CROSS_DOMAIN_EXCEPTIONS predicate."""
        claude_claude_dir = _REPO_ROOT / "claude" / ".claude"
        real_dirs = {
            d.name for d in claude_claude_dir.iterdir()
            if d.is_dir() and d.name != "worktrees"  # gitignored, not a tracked domain
        }
        known = _mod.MAPPED_TOP_LEVEL_DIRS | _mod.DELIBERATELY_UNMAPPED_TOP_LEVEL_DIRS
        unmapped = real_dirs - known
        assert not unmapped, (
            f"claude/.claude/{sorted(unmapped)} exist on disk but are named in "
            "neither MAPPED_TOP_LEVEL_DIRS nor DELIBERATELY_UNMAPPED_TOP_LEVEL_DIRS "
            "-- audit whether any test reads into this directory by path or "
            "subprocess and add the corresponding table entry"
        )


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
        file must appear in the changed-set. This exercises `git diff
        --name-only HEAD`'s tracked-modification path, the sibling case to
        `test_working_tree_dirty_file_is_included`'s untracked-file path."""
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
        """git's default core.quotePath=true escapes non-ASCII bytes in
        --name-only output, so a non-ASCII changed path never string-matches
        a domain predicate and falls open to the full suite."""
        local, _bare = _make_repo_with_remote(tmp_path)
        # Pinned explicitly, matching _init_repo's own --initial-branch=main
        # precedent, so this depends on core.quotePath's default rather than
        # the executing machine's ambient git config.
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
    and untracked-list calls, exercised via the run= DI seam —
    TestComputeChangedPathsGitSmoke's real-git fixtures cover only the
    merge-base-lookup failure, not these two."""

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
    """main()'s selection-to-invocation wiring, with resolve_repo_root,
    compute_changed_paths, and run_pytest all monkeypatched so no real git
    diff, git rev-parse, or pytest subprocess runs."""

    def test_git_diff_unavailable_falls_back_to_full_suite(self, monkeypatch):
        fake_repo_root = Path("/fake/repo/root")

        def fake_compute_changed_paths(repo_root):
            raise _mod.GitDiffUnavailable("stub failure")

        recorded = {}

        def fake_run_pytest(pytest_argv, *, cwd):
            recorded["pytest_argv"] = pytest_argv
            recorded["cwd"] = cwd
            return 0

        monkeypatch.setattr(_mod, "resolve_repo_root", lambda *, cwd: fake_repo_root)
        monkeypatch.setattr(_mod, "compute_changed_paths", fake_compute_changed_paths)
        monkeypatch.setattr(_mod, "run_pytest", fake_run_pytest)

        exit_code = _mod.main([])

        assert exit_code == 0
        assert recorded["pytest_argv"] == list(_mod.FULL_SUITE_TARGETS)
        assert recorded["cwd"] == fake_repo_root

    def test_domain_selected_paths_are_passed_through_to_run_pytest(self, monkeypatch):
        fake_repo_root = Path("/fake/repo/root")
        recorded = {}

        def fake_compute_changed_paths(repo_root):
            recorded["repo_root_passed_to_compute"] = repo_root
            return ["claude/.claude/scripts/mark-terminal.py"]

        def fake_run_pytest(pytest_argv, *, cwd):
            recorded["pytest_argv"] = pytest_argv
            recorded["cwd"] = cwd
            return 0

        monkeypatch.setattr(_mod, "resolve_repo_root", lambda *, cwd: fake_repo_root)
        monkeypatch.setattr(_mod, "compute_changed_paths", fake_compute_changed_paths)
        monkeypatch.setattr(_mod, "run_pytest", fake_run_pytest)

        exit_code = _mod.main(["-k", "foo"])

        assert exit_code == 0
        assert recorded["pytest_argv"] == [_mod.SCRIPTS_TESTS_DIR, "-k", "foo"]
        assert recorded["repo_root_passed_to_compute"] == fake_repo_root
        assert recorded["cwd"] == fake_repo_root

    def test_empty_target_selection_skips_run_pytest_and_returns_zero(self, monkeypatch):
        """A domain-selected-but-empty target set (e.g. a .claude/plans/
        change) must short-circuit before run_pytest, not fall through to
        a bare `pytest` invocation that recursively collects the whole repo."""
        fake_repo_root = Path("/fake/repo/root")

        def fake_run_pytest(pytest_argv, *, cwd):
            raise AssertionError("run_pytest must not be called for an empty target selection")

        monkeypatch.setattr(_mod, "resolve_repo_root", lambda *, cwd: fake_repo_root)
        monkeypatch.setattr(
            _mod, "compute_changed_paths", lambda repo_root: [".claude/plans/some-plan.md"],
        )
        monkeypatch.setattr(_mod, "run_pytest", fake_run_pytest)

        exit_code = _mod.main([])

        assert exit_code == 0

    def test_empty_target_selection_with_passthrough_args_still_skips_run_pytest(
        self, monkeypatch,
    ):
        """Pins the empty-target short circuit against a future edit that
        relocates it below build_pytest_argv, which would reintroduce
        whole-repo collection specifically when passthrough args are
        non-empty."""
        fake_repo_root = Path("/fake/repo/root")

        def fake_run_pytest(pytest_argv, *, cwd):
            raise AssertionError("run_pytest must not be called for an empty target selection")

        monkeypatch.setattr(_mod, "resolve_repo_root", lambda *, cwd: fake_repo_root)
        monkeypatch.setattr(
            _mod, "compute_changed_paths", lambda repo_root: [".claude/plans/some-plan.md"],
        )
        monkeypatch.setattr(_mod, "run_pytest", fake_run_pytest)

        exit_code = _mod.main(["-k", "foo"])

        assert exit_code == 0

    def test_argv_none_falls_back_to_sys_argv(self, monkeypatch):
        """main()'s real entry point (`sys.exit(main())` under
        `__main__`) always calls it with argv=None; this pins that branch
        since both tests above supply an explicit argv."""
        fake_repo_root = Path("/fake/repo/root")
        recorded = {}

        monkeypatch.setattr(_mod, "resolve_repo_root", lambda *, cwd: fake_repo_root)
        monkeypatch.setattr(
            _mod, "compute_changed_paths", lambda repo_root: ["claude/.claude/scripts/mark-terminal.py"],
        )

        def fake_run_pytest(pytest_argv, *, cwd):
            recorded["pytest_argv"] = pytest_argv
            return 0

        monkeypatch.setattr(_mod, "run_pytest", fake_run_pytest)
        monkeypatch.setattr(sys, "argv", ["select-tests.py", "-k", "bar"])

        exit_code = _mod.main(None)

        assert exit_code == 0
        assert recorded["pytest_argv"] == [_mod.SCRIPTS_TESTS_DIR, "-k", "bar"]
