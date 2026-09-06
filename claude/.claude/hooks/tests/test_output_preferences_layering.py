"""Pin the boundary between the personal output-preferences layer and CLAUDE.md.

`README.md`'s Output preferences template is what a stow consumer copies
into their own `<config-dir>/output-preferences.md`, and the promoted rules
live in `claude/.claude/CLAUDE.md`'s prose section, not in that template. If
either end drifts — a promoted rule creeps back into the template, or the
section name changes without updating README's pointer — a consumer either
re-duplicates rules that already apply globally, or follows a dangling
reference. Nothing else in the suite guards this boundary.

Both facts are read self-referentially rather than hardcoded on both sides:
the CLAUDE.md section name comes from README's own pointer sentence, and the
duplication check derives "what counts as a promoted rule" from CLAUDE.md's
actual bullet lead-ins rather than a copy of their text. A coordinated
rename or rewording that updates both sides consistently does not fail
either test; only an actual drift between them does.
"""
from __future__ import annotations

import re

from helpers import CLAUDE_DIR

# CLAUDE_DIR is defined in helpers.py as Path(__file__).resolve().parent.parent,
# anchored to the stow-source path, not the symlink target (~/.claude/).
# Chain: CLAUDE_DIR (claude/.claude/) → .parent (claude/) → .parent (repo root).
REPO_ROOT = CLAUDE_DIR.parent.parent

_README_SECTION_HEADING = "### Output preferences\n"


def _extract_section(text: str, heading: str) -> str:
    """Return the body between `heading` and the next heading of any level.

    Mirrors test_doc_counts.py's anchor-count discipline: `heading` must
    appear exactly once. Zero means it was reworded and this scan has no
    start; more than one means the scan boundary is ambiguous.

    Skips heading-shaped lines inside fenced code blocks — this section's
    own template example is itself a markdown snippet starting with a `#`
    heading, which would otherwise end the scan before reaching the real
    next section.
    """
    anchors = text.count(heading)
    assert anchors == 1, (
        f"Expected exactly one {heading!r} anchor, found {anchors}. Zero "
        "means it was reworded; more than one means the scan boundary is "
        "ambiguous."
    )
    body = text.split(heading, 1)[1]
    in_fence = False
    for line_match in re.finditer(r"^(.*)$", body, re.MULTILINE):
        line = line_match.group(1)
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and re.match(r"^#{1,6} ", line):
            return body[: line_match.start()]
    return body


def _prose_section_title(readme_section_body: str) -> str:
    """Return the CLAUDE.md section name README's own pointer sentence names."""
    match = re.search(r'"([^"]+)" section', readme_section_body)
    assert match, (
        "README's Output preferences section no longer names the CLAUDE.md "
        "section it points at"
    )
    return match.group(1)


def test_template_holds_only_the_personal_bullets() -> None:
    readme_text = (REPO_ROOT / "README.md").read_text()
    readme_section_body = _extract_section(readme_text, _README_SECTION_HEADING)

    fenced_block_match = re.search(
        r"```markdown\n(.*?)\n```", readme_section_body, re.DOTALL
    )
    assert fenced_block_match, "Output preferences section has no fenced template block"
    bullet_lines = [
        line
        for line in fenced_block_match.group(1).splitlines()
        if line.startswith("- ")
    ]
    assert len(bullet_lines) == 2, (
        "Expected exactly the two personal-taste bullets (tone, emoji) in "
        f"README's template; found {len(bullet_lines)}: {bullet_lines}"
    )

    title = _prose_section_title(readme_section_body)
    claude_md_text = (REPO_ROOT / "claude/.claude/CLAUDE.md").read_text()
    prose_section_body = _extract_section(claude_md_text, f"## {title}\n")
    promoted_lead_ins = re.findall(r"^- \*\*(.+?)\*\*", prose_section_body, re.MULTILINE)
    assert promoted_lead_ins, (
        f"Could not find any promoted-rule bullet lead-ins in CLAUDE.md's "
        f"{title!r} section"
    )
    template_lower = "\n".join(bullet_lines).lower()
    for lead_in in promoted_lead_ins:
        assert lead_in.lower() not in template_lower, (
            f"README's personal-preferences template re-duplicates the "
            f"promoted CLAUDE.md rule {lead_in!r}; that rule already applies "
            "globally and does not belong in the personal template."
        )


def test_readme_names_the_global_prose_section() -> None:
    readme_text = (REPO_ROOT / "README.md").read_text()
    readme_section_body = _extract_section(readme_text, _README_SECTION_HEADING)
    title = _prose_section_title(readme_section_body)

    claude_md_text = (REPO_ROOT / "claude/.claude/CLAUDE.md").read_text()
    assert f"## {title}" in claude_md_text, (
        f"README points at a {title!r} section in CLAUDE.md that does not exist there"
    )
