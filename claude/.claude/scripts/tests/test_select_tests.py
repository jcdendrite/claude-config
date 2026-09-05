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

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import _commit, _init_repo, _make_repo_with_remote

_SCRIPT = Path(__file__).parent.parent / "select-tests.py"
_spec = importlib.util.spec_from_file_location("select_tests", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)

# Anchors the path-fidelity and glob-expansion tests to this actual
# checkout's root, via the module's own repo-root resolution (dogfooding
# the production code path rather than hand-deriving a parents[N] guess).
_REPO_ROOT = _mod.resolve_repo_root(cwd=Path(__file__).parent)

# The five path constants claude/.claude/tests/helpers.py exports, seeding
# the resolver's environment for names imported rather than assigned in the
# scanned file itself. No AGENTS_DIR here: test_agent_roster.py defines its
# own from CLAUDE_DIR, which the in-file environment already resolves.
_SEED_REPO_PATHS: dict[str, str] = {
    "REPO_ROOT": "",
    "CLAUDE_DIR": "claude/.claude",
    "HOOKS_DIR": "claude/.claude/hooks",
    "SKILLS_DIR": "claude/.claude/skills",
    "SCRIPTS_DIR": "claude/.claude/scripts",
}


def _pop_segments(prefix: str | None, count: int) -> str | None:
    """Drops `count` trailing segments from a repo-relative prefix string.

    Mirrors pathlib.Path's own arithmetic: `.parent` pops one segment, and
    `.parents[N]` pops N+1 (`Path("a/b/c").parents[0] == Path("a/b/c").parent`).
    Popping past the repo root -- the prefix has fewer segments than `count`
    -- is unresolvable, not a negative-length result.
    """
    if prefix is None:
        return None
    segments = prefix.split("/") if prefix else []
    if count > len(segments):
        return None
    return "/".join(segments[: len(segments) - count])


def _resolve_repo_path_expr(
    node: ast.expr, *, env: dict[str, str | None], test_file_relpath: str,
) -> str | None:
    """Resolves one AST expression to a repo-relative prefix string, per the
    grammar table in .claude/plans/select-tests-cross-domain-read-completeness.md.
    Any shape not explicitly handled below is unresolvable by design:
    - a bare string `Constant`
    - a `BoolOp`
    - a call that is neither `Path(__file__)` nor `.resolve()`
    - a `ListComp`
    - a `List` of glob results
    All fall through to the final `return None`.
    """
    if isinstance(node, ast.Call):
        if (
            isinstance(node.func, ast.Name) and node.func.id == "Path"
            and len(node.args) == 1 and not node.keywords
            and isinstance(node.args[0], ast.Name) and node.args[0].id == "__file__"
        ):
            return test_file_relpath
        if (
            isinstance(node.func, ast.Attribute) and node.func.attr == "resolve"
            and not node.args and not node.keywords
        ):
            return _resolve_repo_path_expr(node.func.value, env=env, test_file_relpath=test_file_relpath)
        return None
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        base = _resolve_repo_path_expr(node.value, env=env, test_file_relpath=test_file_relpath)
        return _pop_segments(base, 1)
    if isinstance(node, ast.Subscript):
        subscripted = node.value
        if isinstance(subscripted, ast.Attribute) and subscripted.attr == "parents":
            index_node = node.slice
            if isinstance(index_node, ast.Constant) and isinstance(index_node.value, int):
                base = _resolve_repo_path_expr(
                    subscripted.value, env=env, test_file_relpath=test_file_relpath,
                )
                return _pop_segments(base, index_node.value + 1)
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        base = _resolve_repo_path_expr(node.left, env=env, test_file_relpath=test_file_relpath)
        if base is None or not (isinstance(node.right, ast.Constant) and isinstance(node.right.value, str)):
            return None
        return "/".join(part for part in (base, node.right.value) if part)
    if isinstance(node, ast.Name):
        return env.get(node.id)
    return None


def _resolve_module_level_repo_paths(source: str, *, test_file_relpath: str) -> dict[str, str]:
    """Maps each module-level, single-`Name`-target assignment in `source`
    to the repo-relative prefix string it resolves to, walking `tree.body`
    in source order and never descending into a FunctionDef, ClassDef, or
    If. A name rebound to an unresolvable expression shadows any seed of
    the same name in the returned environment, rather than falling back to
    the seed.
    """
    tree = ast.parse(source)
    env: dict[str, str | None] = dict(_SEED_REPO_PATHS)
    resolved: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = _resolve_repo_path_expr(node.value, env=env, test_file_relpath=test_file_relpath)
        env[target.id] = value
        if value is None:
            resolved.pop(target.id, None)
        else:
            resolved[target.id] = value
    return resolved


def _test_corpus(repo_root: Path) -> list[str]:
    """Repo-relative paths of every test_*.py file the completeness scanner
    parses: claude/.claude/hooks/tests/, claude/.claude/scripts/tests/,
    claude/.claude/skills/tests/, and plugins/*/tests/."""
    patterns = (
        "claude/.claude/hooks/tests/test_*.py",
        "claude/.claude/scripts/tests/test_*.py",
        "claude/.claude/skills/tests/test_*.py",
        "plugins/*/tests/test_*.py",
    )
    corpus = [
        str(path.relative_to(repo_root))
        for pattern in patterns
        for path in repo_root.glob(pattern)
    ]
    return sorted(corpus)


class TestModuleLevelRepoPathResolver:
    """Pins _resolve_module_level_repo_paths' grammar directly against small
    ast.parse-able fixture strings, one case per row of the grammar table
    plus its negatives -- no real files involved."""

    _TEST_FILE = "claude/.claude/scripts/tests/test_example.py"

    def test_seeded_name_resolves_to_its_seed_value(self):
        resolved = _resolve_module_level_repo_paths("X = SKILLS_DIR\n", test_file_relpath=self._TEST_FILE)
        assert resolved["X"] == "claude/.claude/skills"

    def test_path_dunder_file_resolves_to_the_scanned_files_own_relpath(self):
        resolved = _resolve_module_level_repo_paths(
            "X = Path(__file__)\n", test_file_relpath=self._TEST_FILE,
        )
        assert resolved["X"] == self._TEST_FILE

    def test_resolve_call_is_a_no_op_on_an_already_normalized_prefix(self):
        resolved = _resolve_module_level_repo_paths(
            "X = Path(__file__).resolve()\n", test_file_relpath=self._TEST_FILE,
        )
        assert resolved["X"] == self._TEST_FILE

    def test_parent_attribute_pops_the_last_segment(self):
        resolved = _resolve_module_level_repo_paths("X = SCRIPTS_DIR.parent\n", test_file_relpath=self._TEST_FILE)
        assert resolved["X"] == "claude/.claude"

    def test_two_hop_parent_chain_from_seeded_name_then_slash_append(self):
        source = 'R = CLAUDE_DIR.parent.parent\nY = R / "claude" / ".claude" / "x.md"\n'
        resolved = _resolve_module_level_repo_paths(source, test_file_relpath=self._TEST_FILE)
        assert resolved["R"] == ""
        assert resolved["Y"] == "claude/.claude/x.md"

    def test_three_hop_parent_chain_from_seeded_name_then_slash_append(self):
        """Mirrors test_require_skill_review.py's real
        `_PLUGINS_DIR = HOOKS_DIR.parent.parent.parent / "plugins"`."""
        resolved = _resolve_module_level_repo_paths(
            'Z = HOOKS_DIR.parent.parent.parent / "plugins"\n', test_file_relpath=self._TEST_FILE,
        )
        assert resolved["Z"] == "plugins"

    def test_parents_subscript_with_int_constant_pops_n_plus_one_segments(self):
        """Mirrors test_install_dev.py's real `Path(__file__).parents[4]`
        climbing scripts/tests/ -> scripts/ -> claude/.claude/ -> claude/ ->
        repo root -- four `.parent` hops, so parents[4] pops five segments."""
        resolved = _resolve_module_level_repo_paths(
            "X = Path(__file__).resolve().parents[4]\n", test_file_relpath=self._TEST_FILE,
        )
        assert resolved["X"] == ""

    def test_path_dunder_file_parent_chain_without_a_resolve_call(self):
        resolved = _resolve_module_level_repo_paths(
            'X = Path(__file__).parent.parent / "x.py"\n', test_file_relpath=self._TEST_FILE,
        )
        assert resolved["X"] == "claude/.claude/scripts/x.py"

    def test_embedded_slash_literal_appended_in_one_truediv(self):
        """Mirrors test_check_claude_md_length.py's real
        `Path(__file__).resolve().parents[4] / "claude/.claude/settings.json"`
        -- the appended literal may itself contain '/'."""
        source = 'X = Path(__file__).resolve().parents[4] / "claude/.claude/settings.json"\n'
        resolved = _resolve_module_level_repo_paths(
            source, test_file_relpath="claude/.claude/hooks/tests/test_example.py",
        )
        assert resolved["X"] == "claude/.claude/settings.json"

    def test_bare_repo_root_binding_resolves_to_the_empty_prefix(self):
        """The resolver itself does not drop the empty prefix --
        TestCrossDomainReadCompleteness's own candidate filter does."""
        resolved = _resolve_module_level_repo_paths("X = REPO_ROOT\n", test_file_relpath=self._TEST_FILE)
        assert resolved["X"] == ""

    def test_bare_string_constant_is_unresolvable(self):
        resolved = _resolve_module_level_repo_paths(
            'X = "/tmp/synthetic-hook-payload.json"\n', test_file_relpath=self._TEST_FILE,
        )
        assert "X" not in resolved

    def test_function_local_assignment_is_invisible_to_the_module_level_walk(self):
        resolved = _resolve_module_level_repo_paths(
            "def f():\n    X = SKILLS_DIR\n", test_file_relpath=self._TEST_FILE,
        )
        assert "X" not in resolved

    def test_call_rooted_value_is_unresolvable(self):
        resolved = _resolve_module_level_repo_paths(
            'X = _skill_file("plan-review").parent / "REFERENCES.md"\n', test_file_relpath=self._TEST_FILE,
        )
        assert "X" not in resolved

    def test_boolop_is_unresolvable(self):
        resolved = _resolve_module_level_repo_paths(
            'X = shutil.which("bash") or "/bin/bash"\n', test_file_relpath=self._TEST_FILE,
        )
        assert "X" not in resolved

    def test_seeded_name_rebound_to_an_unresolvable_value_shadows_the_seed(self):
        """A name that shares a seed's name but is reassigned to an
        unresolvable expression must not fall back to the seed value on a
        later reference in the same file."""
        source = 'SKILLS_DIR = compute_dynamically()\nY = SKILLS_DIR / "SKILL.md"\n'
        resolved = _resolve_module_level_repo_paths(source, test_file_relpath=self._TEST_FILE)
        assert "SKILLS_DIR" not in resolved
        assert "Y" not in resolved

    def test_pop_above_the_repo_root_is_unresolvable(self):
        resolved = _resolve_module_level_repo_paths("X = REPO_ROOT.parent\n", test_file_relpath=self._TEST_FILE)
        assert "X" not in resolved

    def test_parents_subscript_with_a_name_index_is_unresolvable(self):
        """Pins the grammar table's own restriction to an int Constant --
        a Name index (even one bound to an int elsewhere) doesn't qualify."""
        source = "N = 4\nX = Path(__file__).resolve().parents[N]\n"
        resolved = _resolve_module_level_repo_paths(source, test_file_relpath=self._TEST_FILE)
        assert "X" not in resolved

    def test_negative_parents_index_is_unresolvable(self):
        """A negative literal parses as a UnaryOp wrapping a Constant, not a
        Constant itself, so it doesn't satisfy the isinstance(index_node,
        ast.Constant) guard."""
        resolved = _resolve_module_level_repo_paths(
            "X = Path(__file__).resolve().parents[-1]\n", test_file_relpath=self._TEST_FILE,
        )
        assert "X" not in resolved

    def test_subscript_with_a_non_parents_base_is_unresolvable(self):
        resolved = _resolve_module_level_repo_paths("X = SOME_LIST[0]\n", test_file_relpath=self._TEST_FILE)
        assert "X" not in resolved

    def test_binop_with_a_non_div_operator_is_unresolvable(self):
        resolved = _resolve_module_level_repo_paths("X = A + B\n", test_file_relpath=self._TEST_FILE)
        assert "X" not in resolved

    def test_div_binop_with_a_non_constant_right_operand_is_unresolvable(self):
        resolved = _resolve_module_level_repo_paths(
            "X = SCRIPTS_DIR / some_name\n", test_file_relpath=self._TEST_FILE,
        )
        assert "X" not in resolved

    def test_tuple_unpack_target_assignment_is_unresolvable(self):
        """A single ast.Assign with a Tuple target (not a Name), so it trips
        the isinstance(target, ast.Name) guard -- see
        test_chained_target_assignment_is_unresolvable for the sibling
        len(node.targets) != 1 guard, which this shape does not reach."""
        resolved = _resolve_module_level_repo_paths(
            "X, Y = SKILLS_DIR, HOOKS_DIR\n", test_file_relpath=self._TEST_FILE,
        )
        assert "X" not in resolved
        assert "Y" not in resolved

    def test_chained_target_assignment_is_unresolvable(self):
        """True chained assignment (X = Y = ...) parses as one ast.Assign
        with two Name targets, tripping the len(node.targets) != 1 guard --
        distinct from the tuple-unpack shape above."""
        resolved = _resolve_module_level_repo_paths(
            "X = Y = SKILLS_DIR\n", test_file_relpath=self._TEST_FILE,
        )
        assert "X" not in resolved
        assert "Y" not in resolved

    def test_annotated_assignment_is_unresolvable(self):
        resolved = _resolve_module_level_repo_paths(
            'X: str = SKILLS_DIR / "SKILL.md"\n', test_file_relpath=self._TEST_FILE,
        )
        assert "X" not in resolved


class TestCrossDomainReadCompleteness:
    """Derives CROSS_DOMAIN_EXCEPTIONS' required entries by scanning every
    test file in _test_corpus() for module-level repo-path constants, so a
    new undeclared cross-domain read fails here instead of depending on
    manual audit of the header comment."""

    def test_test_corpus_has_a_minimum_size(self):
        """Floors _test_corpus()'s size well below its real count (~128
        files today) to catch a corpus-emptying regression, not to pin an exact count."""
        assert len(_test_corpus(_REPO_ROOT)) >= 50

    def test_every_resolved_path_selects_a_target_covering_its_reading_test(self):
        violations: list[tuple[str, str, tuple[str, ...]]] = []
        for test_relpath in _test_corpus(_REPO_ROOT):
            source = (_REPO_ROOT / test_relpath).read_text()
            resolved = _resolve_module_level_repo_paths(source, test_file_relpath=test_relpath)
            for candidate in sorted(set(resolved.values())):
                if not candidate or not (_REPO_ROOT / candidate).exists():
                    continue
                result = _mod.select_pytest_targets([candidate])
                if result.is_full_suite:
                    continue
                covered = any(
                    _mod._is_under(test_relpath, target)
                    or test_relpath in _mod._expand_target(target, repo_root=_REPO_ROOT)
                    for target in result.target_paths
                )
                if not covered:
                    violations.append((test_relpath, candidate, result.target_paths))
        assert not violations, "\n".join(
            f"{test} reads {path!r} by path, but select_pytest_targets([{path!r}]) "
            f"selects {targets!r}, which does not cover {test}"
            for test, path, targets in sorted(violations)
        )

    def test_every_file_test_corpus_returns_matches_is_test_source_change(self):
        """Pins the row-to-corpus coupling from two independently derived
        sides: a filesystem walk (_test_corpus) and the production predicate
        (_is_test_source_change) that selects SELECT_TESTS_TEST_PATH for a
        change to any file in that corpus."""
        for test_relpath in _test_corpus(_REPO_ROOT):
            assert _mod._is_test_source_change(test_relpath), test_relpath

    def test_every_is_test_source_change_match_is_in_test_corpus(self):
        """Reverse of the check above: _is_test_source_change matches any
        test_*.py file anywhere under a tests/ directory in claude/ or
        plugins/, while _test_corpus() only globs four hardcoded one-level-
        deep roots. A nested test tree (e.g. plugins/<name>/scripts/tests/)
        would satisfy the predicate but stay invisible to the corpus, and
        this walk catches that gap."""
        corpus = set(_test_corpus(_REPO_ROOT))
        for path in _REPO_ROOT.rglob("test_*.py"):
            test_relpath = str(path.relative_to(_REPO_ROOT))
            if _mod._is_test_source_change(test_relpath):
                assert test_relpath in corpus, test_relpath


class TestSelectPytestTargets:
    def test_hooks_change_selects_hooks_tests_and_transcript_analysis(self):
        """TICKET_REFERENCE_DISCIPLINE_TEST_PATH is also selected: this is a
        .py file under claude/, which that test statically scans."""
        result = _mod.select_pytest_targets(["claude/.claude/hooks/deny-example.py"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {
            _mod.HOOKS_TESTS_DIR, _mod.TRANSCRIPT_ANALYSIS_TEST_GLOB,
            _mod.TICKET_REFERENCE_DISCIPLINE_TEST_PATH,
        }

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

    def test_scripts_change_also_selects_ticket_reference_discipline_test(self):
        """test_ticket_reference_discipline.py statically scans every
        tracked .py file under claude/, including this one, for
        ticket-prefixed identifiers and plan-phase-qualified labels."""
        result = _mod.select_pytest_targets(["claude/.claude/scripts/mark-terminal.py"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {
            _mod.SCRIPTS_TESTS_DIR, _mod.TICKET_REFERENCE_DISCIPLINE_TEST_PATH,
        }

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
        treat it as under claude/.claude/scripts. A non-.py extension keeps
        this scoped to that boundary check rather than also exercising
        _is_py_source_under_claude_or_plugins, which would match a .py file
        here regardless of the SCRIPTS_DIR boundary."""
        result = _mod.select_pytest_targets(["claude/.claude/scripts-other/x.md"])
        assert result.is_full_suite is True
        assert result.reason == "unmatched-path"

    def test_skill_md_change_selects_skills_tests_and_transcript_analysis(self):
        result = _mod.select_pytest_targets(["claude/.claude/skills/test-conventions/SKILL.md"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.SKILLS_TESTS_DIR, _mod.TRANSCRIPT_ANALYSIS_TEST_GLOB}

    def test_skill_auxiliary_md_change_selects_skills_tests(self):
        """test_skill_citations_resolve_to_real_headings (SKILLS_TESTS_DIR)
        scans every REFERENCES.md/ROUTING.md sibling of a SKILL.md, not just
        SKILL.md itself -- a REFERENCES.md-only diff must domain-select
        rather than fall open to the full suite."""
        result = _mod.select_pytest_targets(["claude/.claude/skills/test-conventions/REFERENCES.md"])
        assert result.is_full_suite is False
        assert result.target_paths == (_mod.SKILLS_TESTS_DIR,)

    def test_skill_routing_md_change_selects_skills_tests(self):
        """Same _is_skill_auxiliary_md_change rule as the REFERENCES.md case
        above, for the other auxiliary filename it matches -- but this exact
        ROUTING.md is also PLAN_REVIEW_ROUTING_MD (GH-847): see
        test_plan_review_routing_md_change_also_selects_hooks_tests for that
        cross-domain exception's own coverage."""
        result = _mod.select_pytest_targets(["claude/.claude/skills/plan-review/ROUTING.md"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.SKILLS_TESTS_DIR, _mod.HOOKS_TESTS_DIR}

    def test_non_skill_auxiliary_file_under_skills_is_unmatched_and_falls_open(self):
        """A skill-directory file that is neither SKILL.md, REFERENCES.md,
        nor ROUTING.md matches no skills domain rule and falls open."""
        result = _mod.select_pytest_targets(["claude/.claude/skills/test-conventions/scratch.md"])
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

    def test_skill_management_scripts_change_also_selects_hooks_tests(self):
        """test_ticket_reference_discipline.py statically scans every
        tracked .py file under plugins/ too, so this .py change now selects
        TICKET_REFERENCE_DISCIPLINE_TEST_PATH alongside SKILLS_TESTS_DIR."""
        result = _mod.select_pytest_targets(["plugins/skill-management/scripts/validate_skill_structure.py"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {
            _mod.SKILLS_TESTS_DIR, _mod.TICKET_REFERENCE_DISCIPLINE_TEST_PATH,
        }

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

    def test_skill_files_read_by_hook_tests_each_also_select_hooks_tests(self):
        """Every SKILL_FILES_READ_BY_HOOK_TESTS member is read by exact path
        from a test under HOOKS_TESTS_DIR -- e.g.
        test_reconciliation_block_consistency.py reads CODE_REVIEW_SKILL_MD
        to diff its Reconciliation block against PLAN_REVIEW_ROUTING_MD.
        Without this cross-domain exception, the skills domain rule (or a
        plugin rule, for SKILL_REVIEW_SKILL_MD) claims the path first and the
        HOOKS_TESTS_DIR check goes unrun."""
        for skill_md_path in _mod.SKILL_FILES_READ_BY_HOOK_TESTS:
            result = _mod.select_pytest_targets([skill_md_path])
            assert result.is_full_suite is False, skill_md_path
            assert _mod.HOOKS_TESTS_DIR in result.target_paths, skill_md_path

    def test_plan_review_routing_md_change_also_selects_hooks_tests(self):
        """test_reconciliation_block_consistency.py (HOOKS_TESTS_DIR) reads
        PLAN_REVIEW_ROUTING_MD's exact file by path to diff its
        Reconciliation block against CODE_REVIEW_SKILL_MD. Pinned directly
        here (GH-847) rather than only implied by frozenset membership."""
        result = _mod.select_pytest_targets([_mod.PLAN_REVIEW_ROUTING_MD])
        assert result.is_full_suite is False
        assert _mod.HOOKS_TESTS_DIR in result.target_paths

    def test_skill_management_hooks_and_skills_change_selects_matching_domain_tests(self):
        """test_hook_alignment.py and test_lib.py glob plugins/*/hooks/*.sh
        generically, across every plugin, not only lovable-cloud, so a
        skill-management hooks change selects HOOKS_TESTS_DIR.
        test_skills.py globs plugins/*/skills/*/SKILL.md just as generically,
        so a skill-management skills change selects SKILLS_TESTS_DIR too.
        This exact SKILL.md is also SKILL_REVIEW_SKILL_MD, which
        test_require_skill_review.py reads by path from HOOKS_TESTS_DIR, so
        HOOKS_TESTS_DIR is selected as well."""
        result = _mod.select_pytest_targets(["plugins/skill-management/hooks/require-skill-review.sh"])
        assert result.is_full_suite is False
        assert result.target_paths == (_mod.HOOKS_TESTS_DIR,)

        result = _mod.select_pytest_targets(["plugins/skill-management/skills/skill-review/SKILL.md"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.SKILLS_TESTS_DIR, _mod.HOOKS_TESTS_DIR}

    def test_npm_semver_hooks_and_skills_change_selects_matching_domain_tests(self):
        """Same plugin-generic plugins/*/hooks/*.sh and
        plugins/*/skills/*/SKILL.md globs as skill-management, for
        npm-semver."""
        result = _mod.select_pytest_targets(["plugins/npm-semver/hooks/require-npm-version-bump.sh"])
        assert result.is_full_suite is False
        assert result.target_paths == (_mod.HOOKS_TESTS_DIR,)

        result = _mod.select_pytest_targets(["plugins/npm-semver/skills/npm-semver/SKILL.md"])
        assert result.is_full_suite is False
        assert result.target_paths == (_mod.SKILLS_TESTS_DIR,)

    def test_plugin_semver_hooks_and_skills_change_selects_matching_domain_tests(self):
        """Same plugin-generic plugins/*/hooks/*.sh and
        plugins/*/skills/*/SKILL.md globs as skill-management, for
        plugin-semver."""
        result = _mod.select_pytest_targets(["plugins/plugin-semver/hooks/require-plugin-version-bump.sh"])
        assert result.is_full_suite is False
        assert result.target_paths == (_mod.HOOKS_TESTS_DIR,)

        result = _mod.select_pytest_targets(["plugins/plugin-semver/skills/plugin-semver/SKILL.md"])
        assert result.is_full_suite is False
        assert result.target_paths == (_mod.SKILLS_TESTS_DIR,)

    def test_claude_hook_review_skills_change_selects_skills_tests(self):
        """Same plugin-generic plugins/*/skills/*/SKILL.md glob as
        skill-management, for claude-hook-review.

        This plugin has no hooks/ directory of its own, only skills/, so
        only the SKILL.md case applies.
        """
        result = _mod.select_pytest_targets(["plugins/claude-hook-review/skills/claude-hook-review/SKILL.md"])
        assert result.is_full_suite is False
        assert result.target_paths == (_mod.SKILLS_TESTS_DIR,)

    def test_plugin_skills_nested_subdirectory_change_also_selects_skills_tests(self):
        """_is_plugin_skills_change matches any depth under plugins/*/skills/,
        not just the single-level plugins/*/skills/*/SKILL.md shape the
        globs in test_skills.py actually use -- a SKILL.md nested one level
        deeper than any real glob reaches must still select SKILLS_TESTS_DIR."""
        result = _mod.select_pytest_targets(["plugins/npm-semver/skills/npm-semver/subdir/SKILL.md"])
        assert result.is_full_suite is False
        assert result.target_paths == (_mod.SKILLS_TESTS_DIR,)

    def test_multi_domain_change_unions_both_target_sets(self):
        result = _mod.select_pytest_targets([
            "claude/.claude/scripts/mark-terminal.py",
            "plugins/lovable-cloud/README.md",
        ])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {
            _mod.SCRIPTS_TESTS_DIR, _mod.LOVABLE_CLOUD_TESTS_DIR,
            _mod.TICKET_REFERENCE_DISCIPLINE_TEST_PATH,
        }

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
        """.gitignore matches no domain rule and no cross-domain exception --
        CI's own SKIP_REGEX doesn't list it either, so select-tests.py must
        not route it to an empty target (its empty-target set must stay a
        subset of CI's confirmed-unread set)."""
        result = _mod.select_pytest_targets([".gitignore"])
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

    def test_rules_dir_change_also_selects_hooks_and_skills_tests(self):
        """test_rules_frontmatter.py (SKILLS_TESTS_DIR) and
        test_claude_md_excludes.py (HOOKS_TESTS_DIR) both rglob
        claude/.claude/rules/*.md by path, not by import."""
        result = _mod.select_pytest_targets(["claude/.claude/rules/shell-script-conventions.md"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.SKILLS_TESTS_DIR, _mod.HOOKS_TESTS_DIR}

    def test_github_actions_workflows_rule_md_change_also_selects_hooks_tests(self):
        """test_ci_path_filter.py (HOOKS_TESTS_DIR) reads this exact file by path,
        not by import. Both this exact-match row and the RULES_DIR row above
        supply HOOKS_TESTS_DIR for this path. See this row's own comment in
        select-tests.py for why both are kept."""
        result = _mod.select_pytest_targets([_mod.GITHUB_ACTIONS_WORKFLOWS_RULE_MD])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.SKILLS_TESTS_DIR, _mod.HOOKS_TESTS_DIR}

    def test_github_actions_workflows_rule_md_row_declares_hooks_tests_directly(self):
        """This row's declaration is narrower than the RULES_DIR row's and
        outlives changes to it."""
        sibling_rule_path = "claude/.claude/rules/shell-script-conventions.md"
        matching_rows = [
            targets
            for predicate, targets in _mod.CROSS_DOMAIN_EXCEPTIONS
            if predicate(_mod.GITHUB_ACTIONS_WORKFLOWS_RULE_MD) and not predicate(sibling_rule_path)
        ]
        assert len(matching_rows) == 1
        assert matching_rows[0] == (_mod.HOOKS_TESTS_DIR,)

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

    def test_transcript_analysis_doc_md_change_selects_hooks_and_skills_tests(self):
        """No test reads docs/transcript-analysis.md's content by path --
        every existing reference is a source-code comment citing the doc for
        human readers -- but it's still covered by the docs/ blanket like
        every other docs/*.md file."""
        result = _mod.select_pytest_targets(["docs/transcript-analysis.md"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.HOOKS_TESTS_DIR, _mod.SKILLS_TESTS_DIR}

    def test_transcript_analysis_architecture_doc_md_change_selects_scripts_hooks_and_skills_tests(self):
        """test_transcript_analysis_architecture_doc.py (SCRIPTS_TESTS_DIR)
        reads this exact file's content by path to pin its module list, in
        addition to the docs/ blanket's HOOKS_TESTS_DIR and SKILLS_TESTS_DIR,
        since test_skills.py's test_doc_has_no_state_path also parametrizes
        over it."""
        result = _mod.select_pytest_targets([_mod.TRANSCRIPT_ANALYSIS_ARCHITECTURE_DOC_MD])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {
            _mod.SCRIPTS_TESTS_DIR, _mod.HOOKS_TESTS_DIR, _mod.SKILLS_TESTS_DIR,
        }

    def test_docs_dir_change_selects_hooks_and_skills_tests(self):
        """The docs/ blanket covers any file directly under docs/:
        test_hook_alignment.py, test_doc_counts.py (both HOOKS_TESTS_DIR),
        and test_skills.py's test_doc_has_no_state_path (SKILLS_TESTS_DIR)
        all read docs/*.md files by path."""
        result = _mod.select_pytest_targets(["docs/some-guide.md"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.HOOKS_TESTS_DIR, _mod.SKILLS_TESTS_DIR}

    def test_docs_reports_nested_file_change_also_selects_hooks_and_skills_tests(self):
        """_is_under matches any depth under DOCS_DIR, so a nested
        docs/reports/*.md file is covered by the same blanket as a
        top-level docs/*.md file. Over-selecting here is deliberate: only
        test_skills.py's test_doc_has_no_state_path excludes docs/reports/**
        from its own corpus (preserved historical records, CLAUDE.md Axis 3),
        and over-selection is the safe direction."""
        result = _mod.select_pytest_targets(["docs/reports/2026-08-audit.md"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.HOOKS_TESTS_DIR, _mod.SKILLS_TESTS_DIR}

    def test_readme_md_change_selects_hooks_and_skills_tests(self):
        """test_doc_counts.py (HOOKS_TESTS_DIR) pins reviewer-agent and
        token-cap counts in README.md. test_skills.py's
        test_doc_has_no_state_path (SKILLS_TESTS_DIR) also reads it for the
        per-account state-path contract."""
        result = _mod.select_pytest_targets([_mod.README_MD])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.HOOKS_TESTS_DIR, _mod.SKILLS_TESTS_DIR}

    def test_install_sh_change_selects_hooks_tests(self):
        """The eleven test_install_sh_*.py modules and test_shellcheck.py,
        both in HOOKS_TESTS_DIR, read install.sh by path."""
        result = _mod.select_pytest_targets([_mod.INSTALL_SH])
        assert result.is_full_suite is False
        assert result.target_paths == (_mod.HOOKS_TESTS_DIR,)

    def test_claude_settings_json_change_selects_hooks_skills_and_scripts_tests(self):
        """test_hook_alignment.py and test_doc_counts.py (HOOKS_TESTS_DIR)
        read this file's permissions and skillOverrides entries by path.
        test_skills.py (SKILLS_TESTS_DIR) reads it for the
        skillOverrides-to-docs/skills.md cross-check.
        test_claude_enable_tool.py:199 (SCRIPTS_TESTS_DIR) reads it by path
        to assert which settings payload backs a re-enabled session."""
        result = _mod.select_pytest_targets([_mod.CLAUDE_SETTINGS_JSON])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {
            _mod.HOOKS_TESTS_DIR, _mod.SKILLS_TESTS_DIR, _mod.SCRIPTS_TESTS_DIR,
        }

    def test_global_claude_md_change_selects_hooks_and_skills_tests(self):
        """test_skills.py (SKILLS_TESTS_DIR) reads this file by path in six
        places, including test_global_claude_md_has_no_state_path.
        test_doc_counts.py (HOOKS_TESTS_DIR) reads it by path in
        _count_ground_every_choice_categories. test_nudge_transcript_toolkit.py's
        TestNeverFiresOnMarkdown (HOOKS_TESTS_DIR) also picks it up via its
        repo-wide rglob("*.md") content scan."""
        result = _mod.select_pytest_targets([_mod.GLOBAL_CLAUDE_MD])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.HOOKS_TESTS_DIR, _mod.SKILLS_TESTS_DIR}

    def test_root_claude_md_change_selects_hooks_tests(self):
        """No test reads the repo-root CLAUDE.md by path -- unlike
        GLOBAL_CLAUDE_MD, it has no SKILLS_TESTS_DIR reader. It's still
        picked up by test_nudge_transcript_toolkit.py's TestNeverFiresOnMarkdown
        (HOOKS_TESTS_DIR) repo-wide rglob("*.md") content scan."""
        result = _mod.select_pytest_targets([_mod.ROOT_CLAUDE_MD])
        assert result.is_full_suite is False
        assert result.target_paths == (_mod.HOOKS_TESTS_DIR,)

    def test_root_rules_dir_change_selects_hooks_and_skills_tests(self):
        """test_rules_frontmatter.py (SKILLS_TESTS_DIR) rglobs both this
        directory and RULES_DIR (claude/.claude/rules/) for frontmatter
        validation. test_claude_md_excludes.py (HOOKS_TESTS_DIR) rglobs both
        directories too."""
        result = _mod.select_pytest_targets([".claude/rules/settings-json-conventions.md"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.HOOKS_TESTS_DIR, _mod.SKILLS_TESTS_DIR}

    def test_root_skills_dir_change_selects_skills_tests(self):
        """test_skills.py's _all_skill_md_files() (SKILLS_TESTS_DIR) globs
        .claude/skills/*/SKILL.md by path, one of three SKILL.md-glob roots
        alongside SKILLS_DIR and plugins/*/skills/*/SKILL.md."""
        result = _mod.select_pytest_targets([".claude/skills/code-review-claude-config/SKILL.md"])
        assert result.is_full_suite is False
        assert result.target_paths == (_mod.SKILLS_TESTS_DIR,)

    def test_root_settings_json_change_selects_hooks_tests(self):
        """test_claude_md_excludes.py (HOOKS_TESTS_DIR) reads the repo-root
        .claude/settings.json's claudeMdExcludes entry by path."""
        result = _mod.select_pytest_targets([_mod.ROOT_SETTINGS_JSON])
        assert result.is_full_suite is False
        assert result.target_paths == (_mod.HOOKS_TESTS_DIR,)

    def test_skills_test_tree_change_selects_skills_tests(self):
        """`_is_under(p, SKILLS_TESTS_DIR)` mirrors the hooks and scripts
        domains' own blanket `_is_under()` rules, so a file anywhere under
        the skills test tree -- not just a literal `SKILL.md` -- selects
        `SKILLS_TESTS_DIR`. TICKET_REFERENCE_DISCIPLINE_TEST_PATH is also
        selected: this is a .py file under claude/, which that test
        statically scans. SELECT_TESTS_TEST_PATH is selected too:
        test_skills.py is itself in _test_corpus(), so a change to it can
        introduce a module-level constant the completeness scan must see."""
        result = _mod.select_pytest_targets(["claude/.claude/skills/tests/test_skills.py"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {
            _mod.SKILLS_TESTS_DIR, _mod.TICKET_REFERENCE_DISCIPLINE_TEST_PATH, _mod.SELECT_TESTS_TEST_PATH,
        }

    def test_non_lovable_cloud_plugin_agents_change_selects_hooks_and_skills_tests(self):
        """test_agent_roster.py (HOOKS_TESTS_DIR) and test_skills.py
        (SKILLS_TESTS_DIR) both glob plugins/*/agents/*.md generically, not
        just lovable-cloud's. npm-semver doesn't need an agents/ directory
        to exist on disk for this -- select_pytest_targets is a pure
        path-string mapping."""
        result = _mod.select_pytest_targets(["plugins/npm-semver/agents/reviewer.md"])
        assert result.is_full_suite is False
        assert set(result.target_paths) == {_mod.HOOKS_TESTS_DIR, _mod.SKILLS_TESTS_DIR}

    def test_triggering_paths_populated_for_unmatched_path(self):
        result = _mod.select_pytest_targets([".gitignore", "LICENSE"])
        assert result.is_full_suite is True
        assert result.reason == "unmatched-path"
        assert result.triggering_paths == (".gitignore", "LICENSE")

    def test_matched_and_unmatched_path_together_falls_open_to_full_suite(self):
        """A path that matches a domain rule alongside a path that matches
        none still falls open to the full suite -- the accumulate-all-
        unmatched-paths refactor doesn't let a matched domain's targets
        leak through when another path in the same diff is unmatched."""
        result = _mod.select_pytest_targets(
            ["claude/.claude/scripts/mark-terminal.py", ".gitignore"],
        )
        assert result.is_full_suite is True
        assert result.target_paths == _mod.FULL_SUITE_TARGETS
        assert result.triggering_paths == (".gitignore",)

    def test_triggering_paths_populated_for_global_trigger(self):
        result = _mod.select_pytest_targets([
            "pyproject.toml", "claude/.claude/scripts/mark-terminal.py",
        ])
        assert result.reason == "global-trigger"
        assert result.triggering_paths == ("pyproject.toml",)

    def test_triggering_paths_empty_for_empty_diff(self):
        result = _mod.select_pytest_targets([])
        assert result.reason == "empty-diff"
        assert result.triggering_paths == ()

    def test_arbitrary_plugin_py_file_also_selects_hooks_tests(self):
        """_is_py_source_under_claude_or_plugins is plugin-generic, not tied
        to a named plugin's own cross-domain exception -- a .py file under a
        plugin with no dedicated rule of its own (unlike skill-management or
        lovable-cloud) still selects TICKET_REFERENCE_DISCIPLINE_TEST_PATH."""
        result = _mod.select_pytest_targets(["plugins/npm-semver/scripts/check.py"])
        assert result.is_full_suite is False
        assert result.target_paths == (_mod.TICKET_REFERENCE_DISCIPLINE_TEST_PATH,)

    def test_deliberately_unmapped_claude_tests_dir_py_change_falls_open(self):
        """claude/.claude/tests/ has no selectable pytest target of its own
        (DELIBERATELY_UNMAPPED_TOP_LEVEL_DIRS) -- a bare "any .py under
        claude/" predicate would incorrectly narrow
        test_statusline_command.py's and test_pytest_collection_config.py's
        own coverage from the full suite down to HOOKS_TESTS_DIR, a
        directory that does not contain them."""
        result = _mod.select_pytest_targets(["claude/.claude/tests/test_statusline_command.py"])
        assert result.is_full_suite is True
        assert result.reason == "unmatched-path"

        result = _mod.select_pytest_targets(["claude/.claude/tests/test_pytest_collection_config.py"])
        assert result.is_full_suite is True
        assert result.reason == "unmatched-path"

    def test_non_py_file_under_claude_or_plugins_does_not_select_hooks_tests_via_the_new_predicate(self):
        """Scoped to .py only, matching test_ticket_reference_discipline.py's
        own corpus for the identifier/plan-phase-label checks it backs. A
        .sh file gets HOOKS_TESTS_DIR only through an existing shell-script
        rule (e.g. HOOKS_DIR's own domain rule), never through this one --
        this fixture path is under neither, so it must fall open."""
        result = _mod.select_pytest_targets(["plugins/npm-semver/scripts/check.sh"])
        assert result.is_full_suite is True
        assert result.reason == "unmatched-path"

    def test_is_py_source_under_claude_or_plugins_rejects_path_outside_both_dirs(self):
        """Direct assertion on the predicate: a .py file outside both
        claude/ and plugins/ (e.g. SKILL_EVALS_RUNNER) must not match."""
        assert _mod._is_py_source_under_claude_or_plugins(_mod.SKILL_EVALS_RUNNER) is False


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


# Every constant backing a `lambda p: p == CONSTANT` exact-match predicate
# in CROSS_DOMAIN_EXCEPTIONS. A rename that drifts one of these from its
# real on-disk path leaves that predicate silently dead -- it matches
# nothing, and no test fails.
_EXACT_MATCH_LITERAL_PATH_CONSTANTS: tuple[str, ...] = (
    _mod.LOVABLE_CLOUD_PLUGIN_MANIFEST,
    _mod.README_MD,
    _mod.INSTALL_SH,
    _mod.CLAUDE_SETTINGS_JSON,
    _mod.HANDOFF_SKILL_MD,
    *sorted(_mod.SKILL_FILES_READ_BY_HOOK_TESTS),
    _mod.GITHUB_ACTIONS_WORKFLOWS_RULE_MD,
    _mod.TRANSCRIPT_ANALYSIS_ARCHITECTURE_DOC_MD,
    _mod.GLOBAL_CLAUDE_MD,
    _mod.ROOT_CLAUDE_MD,
    _mod.ROOT_SETTINGS_JSON,
)

# The two CROSS_DOMAIN_EXCEPTIONS targets that name a file rather than a
# domain directory. Its only consumer is the fidelity partition below --
# _expand_target (select-tests.py) partitions on "*" in target and has no
# use for this distinction, so it stays a test-only constant.
_FILE_TARGETS: frozenset[str] = frozenset({
    _mod.TICKET_REFERENCE_DISCIPLINE_TEST_PATH,
    _mod.SELECT_TESTS_TEST_PATH,
})

# Hand-derived audit record of every SKILL.md path read from a HOOKS_TESTS_DIR
# test, plus the settings.json read from test_claude_enable_tool.py
# (SCRIPTS_TESTS_DIR). Each pair was hand-checked against its citing test's
# own read call. This list is not derived from SKILL_FILES_READ_BY_HOOK_TESTS
# or the CLAUDE_SETTINGS_JSON row, so it is DAMP test data (CLAUDE.md's named
# exception) rather than a DRY violation. Add a newly-mapped read here only
# when it fits that scope. A HOOKS_TESTS_DIR entry whose read path is not a
# SKILL_FILES_READ_BY_HOOK_TESTS member fails the equality test below.
_KNOWN_CROSS_DOMAIN_READS: tuple[tuple[str, str], ...] = (
    ("claude/.claude/hooks/tests/test_reconciliation_block_consistency.py",
     "claude/.claude/skills/code-review/SKILL.md"),
    ("claude/.claude/hooks/tests/test_reconciliation_block_consistency.py",
     "claude/.claude/skills/plan-review/ROUTING.md"),
    ("claude/.claude/hooks/tests/test_require_memory_skill.py",
     "claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md"),
    ("claude/.claude/hooks/tests/test_require_plan_review.py",
     "claude/.claude/skills/plan-review/SKILL.md"),
    ("claude/.claude/hooks/tests/test_require_ready_for_review.py",
     "claude/.claude/skills/ready-for-review/SKILL.md"),
    ("claude/.claude/hooks/tests/test_require_respond_pr.py",
     "claude/.claude/skills/error-mode-analysis/SKILL.md"),
    ("claude/.claude/hooks/tests/test_require_respond_pr.py",
     "claude/.claude/skills/respond-pr/SKILL.md"),
    ("claude/.claude/hooks/tests/test_require_routing_read.py",
     "claude/.claude/skills/plan-review/SKILL.md"),
    ("claude/.claude/hooks/tests/test_require_skill_review.py",
     "plugins/skill-management/skills/skill-review/SKILL.md"),
    ("claude/.claude/scripts/tests/test_claude_enable_tool.py",
     "claude/.claude/settings.json"),
)


class TestKnownCrossDomainReadsAudit:
    """Cross-checks `_KNOWN_CROSS_DOMAIN_READS` against
    `select_pytest_targets`."""

    def test_each_known_read_selects_a_target_covering_its_reading_test(self):
        for reading_test, read_path in _KNOWN_CROSS_DOMAIN_READS:
            result = _mod.select_pytest_targets([read_path])
            assert result.is_full_suite is False, read_path
            covered = any(
                _mod._is_under(reading_test, target)
                or reading_test in _mod._expand_target(target, repo_root=_REPO_ROOT)
                for target in result.target_paths
            )
            assert covered, f"{read_path!r} selects {result.target_paths!r}, which does not cover {reading_test}"

    def test_skill_files_read_by_hook_tests_equals_known_reads_under_hooks_tests_dir(self):
        """The only check that can catch a wrongly-added or missing
        SKILL_FILES_READ_BY_HOOK_TESTS member: its expectation is built from
        _KNOWN_CROSS_DOMAIN_READS, not from the frozenset itself."""
        expected = {
            read_path for reading_test, read_path in _KNOWN_CROSS_DOMAIN_READS
            if _mod._is_under(reading_test, _mod.HOOKS_TESTS_DIR)
        }
        assert expected == _mod.SKILL_FILES_READ_BY_HOOK_TESTS


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
        directory_targets = [
            t for t in self._all_targets()
            if "*" not in t and t not in _FILE_TARGETS
        ]
        assert directory_targets, "expected at least one plain directory target"
        for target in directory_targets:
            assert (_REPO_ROOT / target).is_dir(), f"{target} does not exist as a directory"

    def test_every_file_target_exists_on_disk(self):
        """The CROSS_DOMAIN_EXCEPTIONS targets that name a file rather than
        a domain directory -- excluded from the directory check above,
        checked as files here instead."""
        for target in _FILE_TARGETS:
            assert (_REPO_ROOT / target).is_file(), f"{target} does not exist as a file"

    def test_every_glob_target_matches_at_least_one_file_on_disk(self):
        glob_targets = [t for t in self._all_targets() if "*" in t]
        assert glob_targets, "expected at least one glob-pattern target"
        for target in glob_targets:
            assert list(_REPO_ROOT.glob(target)), f"{target} matched no files on disk"

    def test_every_global_trigger_path_exists_on_disk(self):
        for path in _mod.GLOBAL_TRIGGER_PATHS:
            assert (_REPO_ROOT / path).is_file(), f"{path} does not exist as a file"

    def test_every_exact_match_literal_path_constant_exists_on_disk(self):
        for constant in _EXACT_MATCH_LITERAL_PATH_CONSTANTS:
            assert (_REPO_ROOT / constant).is_file(), f"{constant} does not exist as a file"

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

    def test_every_real_root_claude_dir_is_mapped(self):
        """Mirrors test_every_real_top_level_claude_dir_is_mapped_or_allowlisted
        for the separate root .claude/ tree, where PLANS_DIR, ROOT_RULES_DIR,
        and ROOT_SKILLS_DIR reference subdirectories by path. No union with a
        DELIBERATELY_UNMAPPED counterpart: unlike claude/.claude/tests/, no
        real subdirectory of root .claude/ lacks a selectable pytest target."""
        root_claude_dir = _REPO_ROOT / ".claude"
        real_dirs = {
            d.name for d in root_claude_dir.iterdir()
            if d.is_dir() and d.name != "worktrees"  # gitignored, not a tracked domain
        }
        unmapped = real_dirs - _mod.MAPPED_ROOT_CLAUDE_DIRS
        assert not unmapped, (
            f".claude/{sorted(unmapped)} exist on disk but are not named in "
            "MAPPED_ROOT_CLAUDE_DIRS -- audit whether any test reads into "
            "this directory by path or subprocess and add the corresponding "
            "table entry"
        )

    def test_lovable_cloud_is_the_only_plugin_with_a_tests_directory(self):
        """_is_plugin_hooks_change/_is_plugin_skills_change/_is_plugin_agents_change
        route every plugin's hooks/skills/agents changes to
        HOOKS_TESTS_DIR/SKILLS_TESTS_DIR, never to a plugin's own tests/
        directory -- safe only because lovable-cloud is the only plugin
        with one today."""
        plugins_with_tests_dir = sorted(
            path.parent.name for path in (_REPO_ROOT / _mod.PLUGINS_DIR).glob("*/tests")
        )
        assert plugins_with_tests_dir == ["lovable-cloud"], (
            f"plugins with their own tests/ directory: {plugins_with_tests_dir}. "
            "A second plugin gaining a tests/ directory means the "
            "plugin-generic hooks/skills/agents rules in select-tests.py now "
            "under-select for it -- audit DOMAIN_RULES/CROSS_DOMAIN_EXCEPTIONS "
            "and give that plugin its own LOVABLE_CLOUD_TESTS_DIR-shaped "
            "domain rule."
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
        assert recorded["pytest_argv"] == [
            _mod.TICKET_REFERENCE_DISCIPLINE_TEST_PATH, _mod.SCRIPTS_TESTS_DIR, "-k", "foo",
        ]
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
        assert recorded["pytest_argv"] == [
            _mod.TICKET_REFERENCE_DISCIPLINE_TEST_PATH, _mod.SCRIPTS_TESTS_DIR, "-k", "bar",
        ]

    def test_unmatched_path_prints_offending_paths_to_stderr(self, monkeypatch, capsys):
        """stderr previously named only the reason code, never the path
        that actually triggered it."""
        fake_repo_root = Path("/fake/repo/root")

        monkeypatch.setattr(_mod, "resolve_repo_root", lambda *, cwd: fake_repo_root)
        monkeypatch.setattr(
            _mod, "compute_changed_paths", lambda repo_root: [".gitignore", "LICENSE"],
        )
        monkeypatch.setattr(_mod, "run_pytest", lambda pytest_argv, *, cwd: 0)

        _mod.main([])

        stderr = capsys.readouterr().err
        assert "running the full suite (unmatched-path: .gitignore, LICENSE)" in stderr

    def test_global_trigger_prints_offending_path_to_stderr(self, monkeypatch, capsys):
        fake_repo_root = Path("/fake/repo/root")

        monkeypatch.setattr(_mod, "resolve_repo_root", lambda *, cwd: fake_repo_root)
        monkeypatch.setattr(_mod, "compute_changed_paths", lambda repo_root: ["pyproject.toml"])
        monkeypatch.setattr(_mod, "run_pytest", lambda pytest_argv, *, cwd: 0)

        _mod.main([])

        stderr = capsys.readouterr().err
        assert "running the full suite (global-trigger: pyproject.toml)" in stderr
