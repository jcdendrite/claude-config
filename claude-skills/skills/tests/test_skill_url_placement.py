"""Citation URLs stay out of SKILL.md bodies and runtime-read auxiliary files."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# pyproject.toml's pythonpath also puts claude/.claude/scripts on the import
# path, where the auxiliary-filename constant shared with select-tests.py lives.
from _skill_auxiliary_files import SKILL_AUXILIARY_MD_NAMES
from _skill_md_helpers import (
    _all_skill_md_files,
    _blank_code_regions,
    _blank_frontmatter,
)

# pyproject.toml's pythonpath also puts claude/.claude/tests on the import
# path, where these shared test helpers live.
from helpers import REPO_ROOT, SKILLS_DIR

# --- Citation placement: URLs live in REFERENCES.md, not in a SKILL.md body ---
#
# A SKILL.md body loads into the session on every skill fire; REFERENCES.md never
# does. A citation URL in a body is therefore re-read on every fire by a reader
# that cannot click it. URLs that are *functional* (a namespace URI a parser
# needs, an attack payload, a placeholder) are a different thing entirely, and in
# this corpus they are always inside a fenced code block or an inline code span —
# so stripping code regions first separates the two classes with no allowlist.

_URL_RE = re.compile(r"https?://")


def test_skill_bodies_carry_no_citation_urls() -> None:
    """No SKILL.md body may contain a URL outside a code fence or code span.

    Reports every violation at once rather than failing on the first, so a
    contributor fixes the whole set in one pass.

    Enforcement point: this is a pytest/CI check only, NOT a commit gate. The
    sibling SKILL.md rules in validate_skill_structure.py are enforced at commit
    time by require-skill-review.sh, and this one deliberately is not: that
    validator ships to downstream repos inside the skill-management plugin, so
    putting a claude-config-internal placement convention there would impose it
    on every consumer that installed the plugin for frontmatter validation. The
    tradeoff is that a leaked URL is caught by CI rather than at `git commit`.

    Two CommonMark forms are deliberately not handled: 4-space-indented code
    blocks, and inline code spans that cross a newline. No SKILL.md in this repo
    uses either form to hold a URL, before or after this convention was applied,
    so the machinery would be untested coverage for a shape that does not occur.
    Both gaps err toward over-flagging — the block reads as prose and the test
    fails with a line number pointing straight at it, whose fix is to fence the
    block rather than to widen the parser. Every deliberate gap here is biased
    that direction; see _closed_fence_line_indices for why under-flagging is
    treated as the unacceptable failure mode.
    """
    repo_root = REPO_ROOT
    skill_files = _all_skill_md_files()

    violations: list[str] = []
    for path in skill_files:
        for lineno in _prose_url_line_numbers(path.read_text()):
            violations.append(f"  {path.relative_to(repo_root)}:{lineno}")

    assert not violations, (
        "SKILL.md bodies must not carry citation URLs — a body is re-read on "
        "every skill fire by a reader that cannot follow a link. Move the URL "
        "(and any verbatim source quote) to a REFERENCES.md beside the SKILL.md, "
        "keeping any rule-generalizing rationale in the body. A bare authority "
        "name may stay where the claim is contestable. If the URL is functional "
        "rather than a citation, put it in a code fence or code span.\n"
        + "\n".join(violations)
    )


@pytest.mark.parametrize(
    ("markdown", "url_survives"),
    [
        pytest.param("```\nhttps://example.test\n```\n", False, id="fence-backtick"),
        pytest.param("~~~\nhttps://example.test\n~~~\n", False, id="fence-tilde"),
        # The only fence shape actually present in this corpus. A helper matching
        # on "the opening line is exactly the delimiter" passes every other case
        # here and fails this one.
        pytest.param(
            '```python\nurl = "https://example.test"\n```\n', False, id="fence-info-string"
        ),
        pytest.param("Payload: `https://evil.test/x`\n", False, id="span-single-backtick"),
        pytest.param("Payload: ``https://evil.test/`x``\n", False, id="span-multi-backtick"),
        pytest.param("See https://example.test for details.\n", True, id="bare-url-in-prose"),
        pytest.param("See [the docs](https://example.test).\n", True, id="markdown-link"),
        # The three cases below pin under-flagging bugs — the failure direction
        # that silently exempts a real citation from the scan. Each one returned
        # False (URL swallowed) before the delimiter-run lookarounds and the
        # closed-fence requirement were added.
        pytest.param(
            "````see https://leaked.test``` end\n", True, id="span-mismatched-run-lengths"
        ),
        pytest.param(
            "````\nhttps://in-fence.test\n```\nstill fenced\n````\n",
            False,
            id="fence-longer-opener-ignores-shorter-closer",
        ),
        pytest.param(
            "```python\nx = 1\n\nUnclosed, so this is prose: https://leaked.test\n",
            True,
            id="fence-unterminated-falls-back-to-prose",
        ),
    ],
)
def test_blank_code_regions_distinguishes_functional_urls_from_citations(
    markdown: str, url_survives: bool
) -> None:
    """The helper must bite on both strip paths, not just the fenced one.

    A corpus that happens to pass proves nothing about the algorithm; these cases
    are what prove it. The span cases specifically guard `review-permissions`,
    whose attack payload is a URL inside an inline code span.
    """
    assert bool(_URL_RE.search(_blank_code_regions(markdown))) is url_survives


def test_blank_code_regions_handles_all_three_forms_in_one_document() -> None:
    """A fence, a span, and a real leaked citation in one file.

    This is the actual shape of ai-instruction-and-memory-files/SKILL.md. A
    two-pass bug — fence-stripping running past its own closing delimiter and
    swallowing a later line — would pass every single-form case above and only
    surface here. Also pins line-number preservation, which the corpus scan's
    file:line reporting depends on.
    """
    document = (
        "# Heading\n"  # 1
        "```python\n"  # 2
        'u = "https://in-fence.test"\n'  # 3
        "```\n"  # 4
        "Span: `https://in-span.test`\n"  # 5
        "Leak: [docs](https://leaked.test)\n"  # 6
    )
    prose_lines = _blank_code_regions(document).split("\n")

    flagged = [i for i, line in enumerate(prose_lines, start=1) if _URL_RE.search(line)]
    assert flagged == [6], f"expected only the markdown link on line 6 to survive, got {flagged}"


def test_blank_frontmatter_excludes_metadata_from_the_scan() -> None:
    """Frontmatter is metadata, not body prose — excluded so the rule is exact."""
    document = "---\nname: demo\nhomepage: https://example.test\n---\n\nBody prose.\n"
    assert not _URL_RE.search(_blank_frontmatter(document))
    assert "Body prose." in _blank_frontmatter(document)


# REFERENCES.md is the edit-time reference and is never loaded at runtime, so it
# is the one auxiliary name allowed to carry citation URLs.
# The glob covers claude-skills/skills/ only. Extend it if a runtime auxiliary
# file ever lands under plugins/ or .claude/skills/.
_RUNTIME_READ_AUXILIARY_NAMES = tuple(name for name in SKILL_AUXILIARY_MD_NAMES if name != "REFERENCES.md")
_RUNTIME_READ_AUXILIARY_FILES = sorted(
    path for name in _RUNTIME_READ_AUXILIARY_NAMES for path in SKILLS_DIR.glob(f"*/{name}")
)


def _prose_url_line_numbers(markdown_text: str) -> list[int]:
    """1-based line numbers holding a URL outside frontmatter and code regions."""
    prose = _blank_code_regions(_blank_frontmatter(markdown_text))
    return [lineno for lineno, line in enumerate(prose.split("\n"), start=1) if _URL_RE.search(line)]


@pytest.mark.parametrize("auxiliary_name", _RUNTIME_READ_AUXILIARY_NAMES)
def test_every_runtime_read_auxiliary_name_matches_a_skill_file(auxiliary_name: str) -> None:
    """A registered name with no file on disk would make the URL scan below
    pass vacuously for that name."""
    assert any(path.name == auxiliary_name for path in _RUNTIME_READ_AUXILIARY_FILES), (
        f"no skill directory holds a {auxiliary_name}; drop it from SKILL_AUXILIARY_MD_NAMES or add the file."
    )


@pytest.mark.parametrize(
    "auxiliary_path",
    _RUNTIME_READ_AUXILIARY_FILES,
    ids=lambda path: f"{path.parent.name}/{path.name}",
)
def test_runtime_read_auxiliary_files_carry_no_citation_urls(auxiliary_path: Path) -> None:
    """A runtime-read auxiliary file is re-read on every run that reaches it, by
    a reader that cannot follow a link, so it obeys the same URL rule as a
    SKILL.md body. Citation URLs belong in the skill's REFERENCES.md."""
    assert _prose_url_line_numbers(auxiliary_path.read_text()) == [], (
        f"{auxiliary_path.relative_to(REPO_ROOT)} carries a URL outside a code region; "
        "move it to the skill's REFERENCES.md."
    )


def test_prose_url_detector_flags_a_leaked_citation_and_spares_code() -> None:
    """Deny fixture: the runtime-auxiliary URL check must fire on prose URLs."""
    document = (
        "# Heading\n"  # 1
        "Cited at https://leaked.test in prose.\n"  # 2
        "Payload `https://in-span.test` only.\n"  # 3
        "```\nhttps://in-fence.test\n```\n"  # 4-6
        "[docs](https://leaked-link.test)\n"  # 7
    )
    assert _prose_url_line_numbers(document) == [2, 7]
