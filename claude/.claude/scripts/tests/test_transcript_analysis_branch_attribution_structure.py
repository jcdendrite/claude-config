"""Pins every literal "gitBranch" read in the transcript-analysis shim and its
package to a classified group, so a new consumer that reads a record's tag
directly fails here instead of silently bypassing scope's branch attribution.

The literal scan sees every `ast.Constant` equal to "gitBranch" outside a
docstring. A second scan pins the callers of scope._branch_tag outside scope.py,
which read the tag without a literal, and requires each to also call
_attribute_carried_branch. That check is name presence only: it cannot show the
call's result feeds the filter. A consumer that reaches the tag through another
consumer's output is invisible to both scans. Those derived consumers are pinned
by behavioral tests instead (the agreement class in
test_transcript_analysis_branch_attribution.py).
"""
import ast
from collections import Counter

import pytest
from helpers import REPO_ROOT

SCRIPTS_DIR = REPO_ROOT / "claude" / ".claude" / "scripts"
SHIM_RELATIVE_PATH = "transcript-analysis.py"
PACKAGE_RELATIVE_DIR = "transcript_analysis"

GITBRANCH_KEY = "gitBranch"
BRANCH_TAG_HELPER = "_branch_tag"
SCOPE_RELATIVE_PATH = "transcript_analysis/scope.py"
MODULE_QUALNAME = "<module>"


class _QualnameScopedVisitor(ast.NodeVisitor):
    """Tracks the qualname of the innermost enclosing def or class. A lambda or
    comprehension counts toward its enclosing def; a class-body node counts
    toward the class."""

    def __init__(self):
        self._scope_stack: list[str] = []

    def _current_qualname(self) -> str:
        return ".".join(self._scope_stack) or MODULE_QUALNAME

    def _visit_scope(self, node: ast.AST, name: str) -> None:
        self._scope_stack.append(name)
        self.generic_visit(node)
        self._scope_stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_scope(node, node.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scope(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scope(node, node.name)


class _GitBranchReadCollector(_QualnameScopedVisitor):
    """Records each non-docstring "gitBranch" constant against its enclosing qualname."""

    def __init__(self, docstring_constants: set[int]):
        super().__init__()
        self._docstring_constants = docstring_constants
        self.reads: Counter[str] = Counter()

    def visit_Constant(self, node: ast.Constant) -> None:
        if node.value == GITBRANCH_KEY and id(node) not in self._docstring_constants:
            self.reads[self._current_qualname()] += 1


class _BranchTagCallCollector(_QualnameScopedVisitor):
    """Records the qualname of each def that calls the tag helper, bare or as `module._branch_tag(...)`."""

    def __init__(self):
        super().__init__()
        self.callers: set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:
        called = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
        if called == BRANCH_TAG_HELPER:
            self.callers.add(self._current_qualname())
        self.generic_visit(node)


def _docstring_constant_ids(tree: ast.AST) -> set[int]:
    """ids of the Constant nodes that are a module, class or function docstring."""
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first_statement = node.body[0] if node.body else None
        if (
            isinstance(first_statement, ast.Expr)
            and isinstance(first_statement.value, ast.Constant)
            and isinstance(first_statement.value.value, str)
        ):
            docstrings.add(id(first_statement.value))
    return docstrings


def _collect_gitbranch_reads(source: str, module_key: str) -> Counter[str]:
    """Count the literal "gitBranch" reads in `source`, keyed `<module_key>:<qualname>`."""
    tree = ast.parse(source)
    collector = _GitBranchReadCollector(_docstring_constant_ids(tree))
    collector.visit(tree)
    return Counter({f"{module_key}:{qualname}": count for qualname, count in collector.reads.items()})


def _collect_branch_tag_callers(source: str, module_key: str) -> set[str]:
    """The `<module_key>:<qualname>` of every def in `source` that calls the tag helper."""
    collector = _BranchTagCallCollector()
    collector.visit(ast.parse(source))
    return {f"{module_key}:{qualname}" for qualname in collector.callers}


def _function_nodes_by_qualname(source: str) -> dict[str, ast.AST]:
    nodes: dict[str, ast.AST] = {}

    def walk(parent: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(parent):
            if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                qualname = f"{prefix}{child.name}"
                nodes[qualname] = child
                walk(child, f"{qualname}.")
            else:
                walk(child, prefix)

    walk(ast.parse(source), "")
    return nodes


def _called_names(function_node: ast.AST) -> set[str]:
    """Names called inside `function_node`, whether bare (`f()`) or attribute (`module.f()`)."""
    names: set[str] = set()
    for node in ast.walk(function_node):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return names


def _scanned_source_files() -> dict[str, str]:
    """`<path relative to claude/.claude/scripts>` -> source, for the shim and every package module."""
    relative_paths = [SHIM_RELATIVE_PATH] + sorted(
        path.relative_to(SCRIPTS_DIR).as_posix() for path in (SCRIPTS_DIR / PACKAGE_RELATIVE_DIR).glob("*.py")
    )
    return {relative: (SCRIPTS_DIR / relative).read_text(encoding="utf-8") for relative in relative_paths}


# Positional consumers carry the last main-thread tag forward, read it through
# scope._branch_tag, and apply the pre-branch step to the carried value. They
# are also the exact set of scope._branch_tag callers allowed outside scope.py.
_POSITIONAL_CARRY_FORWARD = {
    "transcript_analysis/review_rounds.py:_session_record_branches":
        "round windows carry the main thread's branch forward by position",
    "transcript_analysis/review_trace.py:_review_trace_session_events":
        "events carry the main thread's branch forward in timestamp order",
}

# Literal reads that sit beside an attributed read in the same function.
_RAW_READ_BESIDE_ATTRIBUTION = {
    "transcript_analysis/fail_seq.py:cmd_fail_seq": (
        2, "a whole-session prefilter and the pending-pair reset mark a physical checkout change; "
           "only the filter and the runs key take the attributed branch",
    ),
    "transcript_analysis/subagents.py:cmd_subagents": (
        2, "the attestation set records the branch a main-thread assistant or user record carried; "
           "the printed row takes the attributed branch",
    ),
}

# Literal reads whose question is the checkout a record was written in, not the branch it counts toward.
_RAW_BY_DESIGN = {
    "transcript_analysis/commit_gate.py:cmd_commit_gate": (1, "a whole-session filter on any main-thread assistant record's tag"),
    "transcript_analysis/handoff_nudge.py:_session_matches_rearm_scope":
        (1, "a whole-session filter on the main-thread assistant records' tags"),
    "transcript_analysis/turn_shape.py:_turn_shape_session_turns":
        (1, "a streak breaks where the checkout physically changed"),
}

# The one place a record's tag is read for attribution; every step in scope goes through it.
_HELPER = {
    "transcript_analysis/scope.py:_branch_tag": (1, "the non-empty-string tag read shared by every attribution step"),
}

_EXPECTED_LITERAL_READS: dict[str, int] = {
    key: count
    for group in (_HELPER, _RAW_READ_BESIDE_ATTRIBUTION, _RAW_BY_DESIGN)
    for key, (count, _reason) in group.items()
}


class TestEveryGitBranchReadIsClassified:
    def test_literal_reads_equal_the_classified_table_exactly(self):
        collected: Counter[str] = Counter()
        for module_key, source in _scanned_source_files().items():
            collected += _collect_gitbranch_reads(source, module_key)

        assert collected == Counter(_EXPECTED_LITERAL_READS), (
            "an unclassified (or no longer present) literal gitBranch read: attribute it with "
            "scope._attributed_branch / _attribute_carried_branch, or classify it in this file "
            f"with a reason.\nunexpected: {dict(collected - Counter(_EXPECTED_LITERAL_READS))}\n"
            f"missing: {dict(Counter(_EXPECTED_LITERAL_READS) - collected)}"
        )

    def test_branch_tag_callers_outside_scope_equal_the_positional_table_exactly(self):
        collected: set[str] = set()
        for module_key, source in _scanned_source_files().items():
            if module_key != SCOPE_RELATIVE_PATH:
                collected |= _collect_branch_tag_callers(source, module_key)

        expected = set(_POSITIONAL_CARRY_FORWARD)
        assert collected == expected, (
            "an unclassified (or no longer present) scope._branch_tag caller: it reads a record's own "
            "tag without attribution. Use scope._attributed_branch, or carry the branch forward and apply "
            "scope._attribute_carried_branch, then classify it in _POSITIONAL_CARRY_FORWARD.\n"
            f"unexpected: {sorted(collected - expected)}\n"
            f"missing: {sorted(expected - collected)}"
        )

    @pytest.mark.parametrize("key", sorted(_POSITIONAL_CARRY_FORWARD))
    def test_positional_consumer_applies_the_pre_branch_step_to_its_carried_branch(self, key):
        function_node = self._function_for(key)
        called = _called_names(function_node)
        assert "_attribute_carried_branch" in called
        assert "_branch_tag" in called

    @pytest.mark.parametrize("key", sorted(_RAW_READ_BESIDE_ATTRIBUTION))
    def test_raw_read_beside_attribution_still_attributes_its_branch(self, key):
        assert "_attributed_branch" in _called_names(self._function_for(key))

    @staticmethod
    def _function_for(key: str) -> ast.AST:
        module_key, qualname = key.split(":")
        functions = _function_nodes_by_qualname(_scanned_source_files()[module_key])
        assert qualname in functions, f"{key} not found; update the classification table"
        return functions[qualname]


class TestCollectorOnSyntheticSnippets:
    def test_nested_def_class_method_and_lambda_attribute_to_the_innermost_scope(self):
        source = (
            "def outer(rec):\n"
            "    def inner():\n"
            "        return rec['gitBranch']\n"
            "    return lambda: rec.get('gitBranch')\n"
            "class Holder:\n"
            "    def method(self, rec):\n"
            "        return rec['gitBranch']\n"
        )
        assert _collect_gitbranch_reads(source, "m.py") == Counter({
            "m.py:outer.inner": 1, "m.py:outer": 1, "m.py:Holder.method": 1,
        })

    def test_comprehension_counts_toward_the_enclosing_def(self):
        source = "def tags(records):\n    return {r.get('gitBranch') for r in records}\n"
        assert _collect_gitbranch_reads(source, "m.py") == Counter({"m.py:tags": 1})

    def test_subscript_get_and_membership_forms_are_all_counted(self):
        source = (
            "def reads(rec):\n"
            "    a = rec['gitBranch']\n"
            "    b = rec.get('gitBranch')\n"
            "    return 'gitBranch' in rec\n"
        )
        assert _collect_gitbranch_reads(source, "m.py") == Counter({"m.py:reads": 3})

    def test_module_level_constant_is_reported_as_the_module(self):
        assert _collect_gitbranch_reads("KEY = 'gitBranch'\n", "m.py") == Counter({"m.py:<module>": 1})

    def test_class_body_read_counts_toward_the_class(self):
        assert _collect_gitbranch_reads("class Fields:\n    KEY = 'gitBranch'\n", "m.py") == Counter({
            "m.py:Fields": 1,
        })

    def test_docstring_and_help_text_mentions_are_not_counted(self):
        source = (
            '"""Module docs naming gitBranch."""\n'
            "def documented(parser):\n"
            '    """Filters on gitBranch."""\n'
            "    parser.add_argument('--branches', help='comma-separated gitBranch values')\n"
        )
        assert _collect_gitbranch_reads(source, "m.py") == Counter()

    def test_a_docstring_that_is_exactly_the_key_is_not_counted(self):
        assert _collect_gitbranch_reads("def f():\n    'gitBranch'\n", "m.py") == Counter()

    def test_a_string_statement_after_the_docstring_position_is_a_read(self):
        source = "def f():\n    x = 1\n    'gitBranch'\n"
        assert _collect_gitbranch_reads(source, "m.py") == Counter({"m.py:f": 1})

    def test_branch_tag_callers_are_found_bare_and_as_attributes_in_the_innermost_def(self):
        source = (
            "def outer(rec):\n"
            "    def inner():\n"
            "        return scope._branch_tag(rec)\n"
            "    return _branch_tag(rec)\n"
            "def unrelated(rec):\n"
            "    return rec.get('other')\n"
        )
        assert _collect_branch_tag_callers(source, "m.py") == {"m.py:outer.inner", "m.py:outer"}
