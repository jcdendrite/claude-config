"""Deterministic scan for two ticket-reference shapes CLAUDE.md's naming and
comment-discipline rules forbid, over every tracked `.py`/`.sh` file under
`claude/` and `plugins/`:

- An identifier (Python `def`/`class`, shell function) carrying a
  ticket-prefixed token, e.g. `TestGh483Invariants`.
- A tracker ID immediately followed by a plan-phase or step qualifier in a
  comment or docstring, e.g. `GH-783 Phase 2`.

An LLM reviewer (`comment-discipline-reviewer`) also covers these same
shapes, plus everything a regex cannot decide (invented codenames,
prior-version framing, `.md` durable docs). The two layers deliberately
overlap on the mechanically-decidable subset; this test is the
deterministic backstop that fires on every run.

Limitations: both checks scan file text, not an AST -- a `def`/`class`
line or a `#`/docstring block inside a string literal or heredoc would be
misread. This is a named-shape creep guard, not a full parser.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
_THIS_FILE = Path(__file__).resolve()

# --- Corpus discovery ----------------------------------------------------


def _tracked_corpus_files() -> list[Path]:
    output = subprocess.run(
        ["git", "ls-files", "-z", "--", "claude", "plugins"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    files = []
    for rel in output.split("\0"):
        if not rel or not (rel.endswith(".py") or rel.endswith(".sh")):
            continue
        path = REPO_ROOT / rel
        if path == _THIS_FILE:
            # Excluded from its own corpus: this file necessarily contains
            # ticket-prefixed identifiers and phase-qualified labels as
            # synthetic positive-control fixtures, matching
            # test_deny_private_project_refs.py's precedent for a test file
            # that must contain the string it forbids.
            continue
        files.append(path)
    return sorted(files)


_CORPUS: list[Path] = _tracked_corpus_files()
_CORPUS_IDS = [str(p.relative_to(REPO_ROOT)) for p in _CORPUS]


def test_corpus_is_non_empty() -> None:
    """Guard against a broken git-ls-files invocation silently collecting
    zero files, which would make every check below vacuously pass."""
    assert _CORPUS, f"expected at least one tracked .py/.sh file under claude/ or plugins/, found none under {REPO_ROOT}"


# --- Identifier tokenizer --------------------------------------------------
# Splits each captured identifier into snake_case-shaped tokens and matches
# each token in isolation against the ticket-prefix pattern, rather than a
# generic [A-Za-z]+\d+ shape over the whole identifier -- the latter would
# fragment "sha256" into a letters-piece next to a digits-piece and falsely
# treat it as prefix-shaped.
_CAMEL_LOWER_TO_UPPER_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_CAMEL_ACRONYM_TO_WORD_BOUNDARY = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_TICKET_PREFIXES = ("gh", "cve", "rfc", "issue", "ticket", "jira", "pr")
_TICKET_PREFIX_TOKEN_RE = re.compile(rf"^(?:{'|'.join(_TICKET_PREFIXES)})\d{{2,}}$")


def _tokenize_identifier(identifier: str) -> list[str]:
    snake = _CAMEL_LOWER_TO_UPPER_BOUNDARY.sub("_", identifier)
    snake = _CAMEL_ACRONYM_TO_WORD_BOUNDARY.sub("_", snake)
    return [token.lower() for token in snake.split("_") if token]


def _ticket_prefixed_tokens(identifier: str) -> list[str]:
    return [token for token in _tokenize_identifier(identifier) if _TICKET_PREFIX_TOKEN_RE.match(token)]


# --- Identifier discovery ---------------------------------------------------

_PY_DEF_OR_CLASS_RE = re.compile(r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")
_SH_FUNC_PAREN_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{")
_SH_FUNC_KEYWORD_RE = re.compile(r"^\s*function\s+([A-Za-z_][A-Za-z0-9_]*)")


def _python_identifiers(content: str) -> list[str]:
    return [m.group(1) for line in content.splitlines() if (m := _PY_DEF_OR_CLASS_RE.match(line))]


def _shell_identifiers(content: str) -> list[str]:
    identifiers = []
    for line in content.splitlines():
        match = _SH_FUNC_PAREN_RE.match(line) or _SH_FUNC_KEYWORD_RE.match(line)
        if match:
            identifiers.append(match.group(1))
    return identifiers


def test_python_identifiers_extracts_def_and_class() -> None:
    """Pins the extraction step itself, independent of the tokenizer: if
    this regex silently stopped matching, every corpus check below would
    find zero identifiers and pass vacuously."""
    content = "class Foo:\n    async def bar(self):\n        pass\n"
    assert _python_identifiers(content) == ["Foo", "bar"]


def test_shell_identifiers_extracts_paren_and_keyword_forms() -> None:
    content = "foo() {\n  echo hi\n}\nfunction bar {\n  echo hi\n}\n"
    assert _shell_identifiers(content) == ["foo", "bar"]


@pytest.mark.parametrize("source_file", _CORPUS, ids=_CORPUS_IDS)
def test_no_ticket_prefixed_identifier(source_file: Path) -> None:
    content = source_file.read_text(encoding="utf-8", errors="ignore")
    identifiers = (
        _python_identifiers(content) if source_file.suffix == ".py" else _shell_identifiers(content)
    )
    violations = [
        f"{identifier} (ticket-shaped token(s): {', '.join(hits)})"
        for identifier in identifiers
        if (hits := _ticket_prefixed_tokens(identifier))
    ]
    assert not violations, (
        "An identifier names the domain it covers, not the ticket that "
        f"produced it. {source_file.relative_to(REPO_ROOT)}:\n" + "\n".join(violations)
    )


# --- Plan-phase-label detection ---------------------------------------------
# Consecutive comment lines are joined into a single logical block, with
# whitespace collapsed, only when the prior line ends mid-qualifier (a bare
# "Phase" or "Step" with no number yet). A line-at-a-time regex would miss a
# label split across a line break (e.g. "GH-783 Phase" / "# 2's"). Joining
# unconditionally would instead coincidentally match two unrelated adjacent
# comments (a tracker-ID citation followed by an unrelated "Step N" mention).
# Known gap, accepted rather than closed: a wrap point at the tracker-ID
# boundary (e.g. "GH-905" / "# Phase 2 replaces the old flow") evades this
# heuristic. That shape is syntactically identical to the false-positive
# shape above (a bare tracker ID ending one line, a "Phase"/"Step" word
# starting the next). Closing it would reopen the false positive this
# heuristic exists to avoid. comment-discipline-reviewer remains the
# backstop for this shape.
# Docstring lines are joined unconditionally by a separate function below,
# since a whole docstring is already one logical unit regardless of its
# internal line breaks.
_TRACKER_ID = r"[A-Z]{2,}-\d+"
_PHASE_STEP_QUALIFIER_RE = re.compile(rf"\b{_TRACKER_ID}\s+(?:Phase|Step)\s+\d+\b", re.IGNORECASE)
_DOCSTRING_RE = re.compile(r'"""(.*?)"""|\'\'\'(.*?)\'\'\'', re.DOTALL)
_DANGLING_PHASE_STEP_QUALIFIER_RE = re.compile(r"\b(?:Phase|Step)$", re.IGNORECASE)


def _joined_comment_blocks(content: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            if current and not _DANGLING_PHASE_STEP_QUALIFIER_RE.search(current[-1]):
                blocks.append(" ".join(current))
                current = []
            current.append(stripped.lstrip("#").strip())
        elif current:
            blocks.append(" ".join(current))
            current = []
    if current:
        blocks.append(" ".join(current))
    return blocks


def _joined_docstring_blocks(content: str) -> list[str]:
    blocks = []
    for match in _DOCSTRING_RE.finditer(content):
        text = match.group(1) if match.group(1) is not None else match.group(2)
        blocks.append(" ".join(text.split()))
    return blocks


@pytest.mark.parametrize("source_file", _CORPUS, ids=_CORPUS_IDS)
def test_no_tracker_id_phase_or_step_qualifier(source_file: Path) -> None:
    content = source_file.read_text(encoding="utf-8", errors="ignore")
    blocks = _joined_comment_blocks(content)
    if source_file.suffix == ".py":
        blocks += _joined_docstring_blocks(content)
    violations = [hit for block in blocks for hit in _PHASE_STEP_QUALIFIER_RE.findall(block)]
    assert not violations, (
        "A bare tracker-ID citation (e.g. `GH-783`) is a legitimate, "
        "self-resolving reference. The phase/step qualifier glued to it "
        f"is what makes the label PR-defined. {source_file.relative_to(REPO_ROOT)}:\n"
        + "\n".join(violations)
    )


# --- Anti-vacuity controls ---------------------------------------------------


def test_positive_control_identifier_detector_matches_synthetic_violation() -> None:
    assert _ticket_prefixed_tokens("TestGh999SyntheticFixture") == ["gh999"]


def test_positive_control_phase_label_detector_matches_synthetic_violation() -> None:
    assert _PHASE_STEP_QUALIFIER_RE.search("See GH-999 Phase 3 for context.")


def test_negative_control_identifier_detector_does_not_flag_hash_or_encoding_names() -> None:
    """Stops a future simplification of the tokenizer to a generic
    [A-Za-z]+\\d+ shape from regressing silently until it starts flagging
    real source names like these."""
    assert _ticket_prefixed_tokens("test_sha256_hash") == []
    assert _ticket_prefixed_tokens("test_utf8_edge_case") == []


def test_negative_control_identifier_detector_flags_public_standard_number_collision() -> None:
    """rfc/cve are both ticket-prefix tokens and public-standard numbering
    schemes this repo's own CLAUDE.md encourages citing (RFC/vendor docs
    for protocol values) -- a future parse_rfc2119-style identifier would
    collide and get flagged. Documents the accepted behavior as a known
    boundary, not a design flaw to silently regress on."""
    assert _ticket_prefixed_tokens("parse_rfc2119") == ["rfc2119"]


def test_line_wrapped_comment_label_is_detected_after_joining() -> None:
    """A tracker ID at the end of one comment line and its phase number
    continuing on the next must not evade a line-at-a-time regex."""
    content = "# some prose about GH-901 Phase\n# 4's behavior here\n"
    blocks = _joined_comment_blocks(content)
    hits = [hit for block in blocks for hit in _PHASE_STEP_QUALIFIER_RE.findall(block)]
    assert hits, "expected the line-wrapped label to be caught after joining"


def test_line_wrapped_docstring_label_is_detected_after_joining() -> None:
    content = '"""Prose about GH-902 Phase\n3\'s behavior here."""\n'
    blocks = _joined_docstring_blocks(content)
    hits = [hit for block in blocks for hit in _PHASE_STEP_QUALIFIER_RE.findall(block)]
    assert hits, "expected the line-wrapped docstring label to be caught after joining"


def test_bare_tracker_id_citation_is_not_flagged() -> None:
    content = "# See GH-783 for background.\n"
    blocks = _joined_comment_blocks(content)
    hits = [hit for block in blocks for hit in _PHASE_STEP_QUALIFIER_RE.findall(block)]
    assert not hits, "a bare tracker-ID citation with no phase/step qualifier must not be flagged"


def test_adjacent_unrelated_comment_lines_are_not_joined_into_false_match() -> None:
    """A bare tracker-ID citation immediately followed by an unrelated 'Step N'
    mention on the next comment line must not be joined into a coincidental
    phase/step qualifier match -- unlike a genuine wrapped label, neither line
    ends mid-qualifier."""
    content = "# Fixed in GH-901\n# Step 4 configures the retry timeout\n"
    blocks = _joined_comment_blocks(content)
    hits = [hit for block in blocks for hit in _PHASE_STEP_QUALIFIER_RE.findall(block)]
    assert not hits, "two unrelated adjacent comments must not coincidentally form a phase/step qualifier"


def test_id_boundary_wrap_is_an_accepted_gap_not_a_regression() -> None:
    """A genuine wrapped label split right after the tracker ID (rather than
    mid-qualifier) currently evades this detector. This is an accepted gap:
    closing it would reintroduce the false positive the test above guards
    against. comment-discipline-reviewer remains the backstop for this
    shape."""
    content = "# Recorded in GH-905\n# Phase 2 replaces the old flow\n"
    blocks = _joined_comment_blocks(content)
    hits = [hit for block in blocks for hit in _PHASE_STEP_QUALIFIER_RE.findall(block)]
    assert not hits, "documents the accepted ID-boundary-wrap gap; a pass here means the gap still exists"


# --- Duplicate top-level test-name detection ---------------------------------
# A second top-level `def test_x` or `class TestX` in the same file silently
# shadows the first under Python's last-definition-wins name-binding rule --
# pytest then collects only the surviving definition, with no collection
# error to signal the loss.
_TOP_LEVEL_DEF_OR_CLASS_RE = re.compile(r"^(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")


def _tracked_test_py_files_under_claude_or_plugins() -> list[Path]:
    output = subprocess.run(
        ["git", "ls-files", "-z", "--", "claude", "plugins"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    files = []
    for rel in output.split("\0"):
        if not rel:
            continue
        name = Path(rel).name
        if name.startswith("test_") and name.endswith(".py"):
            files.append(REPO_ROOT / rel)
    return sorted(files)


def _top_level_test_declaration_names(content: str) -> list[str]:
    names = []
    for line in content.splitlines():
        if not (m := _TOP_LEVEL_DEF_OR_CLASS_RE.match(line)):
            continue
        name = m.group(1)
        if name.startswith("test_") or name.startswith("Test"):
            names.append(name)
    return names


_TEST_PY_FILES_UNDER_CLAUDE_OR_PLUGINS = _tracked_test_py_files_under_claude_or_plugins()
_TEST_PY_FILE_IDS = [str(p.relative_to(REPO_ROOT)) for p in _TEST_PY_FILES_UNDER_CLAUDE_OR_PLUGINS]


def test_test_py_corpus_under_claude_or_plugins_is_non_empty() -> None:
    """Guard against a broken git-ls-files invocation silently collecting
    zero files, which would make the check below vacuously pass."""
    assert _TEST_PY_FILES_UNDER_CLAUDE_OR_PLUGINS, (
        f"expected at least one tracked test_*.py file under claude/ or plugins/, found none under {REPO_ROOT}"
    )


@pytest.mark.parametrize("source_file", _TEST_PY_FILES_UNDER_CLAUDE_OR_PLUGINS, ids=_TEST_PY_FILE_IDS)
def test_no_duplicate_top_level_test_name_within_a_file(source_file: Path) -> None:
    content = source_file.read_text(encoding="utf-8", errors="ignore")
    names = _top_level_test_declaration_names(content)
    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert not duplicates, (
        f"{source_file.relative_to(REPO_ROOT)} declares duplicate top-level test name(s) "
        f"{duplicates} -- Python's last-definition-wins silently drops every earlier "
        "definition and pytest collects only the survivor"
    )


def test_positive_control_duplicate_test_name_detector_matches_synthetic_violation() -> None:
    content = "def test_x():\n    pass\n\n\ndef test_x():\n    pass\n"
    names = _top_level_test_declaration_names(content)
    assert sorted({name for name in names if names.count(name) > 1}) == ["test_x"]


def test_positive_control_duplicate_class_name_detector_matches_synthetic_violation() -> None:
    """Direct coverage of the class alternation branch, mirroring the def
    control above. The negative control only proves class names extract
    correctly, not that the duplicate-counting logic is exercised on them."""
    content = "class TestFoo:\n    pass\n\n\nclass TestFoo:\n    pass\n"
    names = _top_level_test_declaration_names(content)
    assert sorted({name for name in names if names.count(name) > 1}) == ["TestFoo"]


def test_negative_control_duplicate_test_name_detector_ignores_same_named_methods_in_different_classes() -> None:
    """A method named test_x inside two different classes is not a top-level
    collision -- each lives in its own class namespace, not the module's."""
    content = (
        "class TestFoo:\n    def test_x(self):\n        pass\n\n"
        "class TestBar:\n    def test_x(self):\n        pass\n"
    )
    names = _top_level_test_declaration_names(content)
    assert names == ["TestFoo", "TestBar"]
