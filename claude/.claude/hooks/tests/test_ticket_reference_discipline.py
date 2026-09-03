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
# Consecutive comment lines and consecutive docstring lines are joined into
# single logical blocks, with whitespace collapsed, before the pattern is
# applied -- a line-at-a-time regex misses a label split across a line
# break (e.g. "GH-783 Phase" / "# 2's").
_TRACKER_ID = r"[A-Z]{2,}-\d+"
_PHASE_STEP_QUALIFIER_RE = re.compile(rf"\b{_TRACKER_ID}\s+(?:Phase|Step)\s+\d+\b", re.IGNORECASE)
_DOCSTRING_RE = re.compile(r'"""(.*?)"""|\'\'\'(.*?)\'\'\'', re.DOTALL)


def _joined_comment_blocks(content: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
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
