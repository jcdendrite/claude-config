"""Producer-contract tests: how `_lib_continuity_file_resume_cwd` parses the resume-command
shapes that handoff/SKILL.md §7 and brief/SKILL.md §7.5 prescribe.

The templates are read at test time, so a reword of either section that changes what the
parser answers fails here. test_lib_resume_context.py's TestLibContinuityFileResumeCwd copies hand-written
shapes and cannot see that drift.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from helpers import write_continuity_body

from .test_lib_resume_context import _UNUSABLE, _continuity_file_resume_cwd

_REPO_ROOT = Path(__file__).resolve().parents[4]
_HANDOFF_SKILL = _REPO_ROOT / "claude-skills" / "skills" / "handoff" / "SKILL.md"
_BRIEF_SKILL = _REPO_ROOT / "claude-skills" / "skills" / "brief" / "SKILL.md"

_WORKTREE_PATH = "/work/tree-a"
_WORKTREE_PLACEHOLDER = "<worktree path>"
_OWN_FILE_PLACEHOLDER = re.compile(r"<config-dir>/[a-z]+/<slug>-[a-z]+\.md")
_BACKTICKED_SPAN = re.compile(r"`([^`]+)`")
_END_OF_BOTH_ALTERNATIVES = "alone."

_TEMPLATES = [
    pytest.param(_HANDOFF_SKILL, r"^## §7 Resume command$", id="handoff-section-7"),
    pytest.param(_BRIEF_SKILL, r"^## §7\.5 Resume command$", id="brief-section-7.5"),
]


def _section_body(skill_path: Path, heading_pattern: str) -> str:
    """The text under the heading matching `heading_pattern`, up to the next `## ` heading."""
    skill_text = skill_path.read_text()
    heading = re.search(heading_pattern, skill_text, re.MULTILINE)
    assert heading, f"{skill_path.name}: heading /{heading_pattern}/ not found -- was the section renamed?"
    body_and_rest = skill_text[heading.end():]
    next_heading = re.search(r"^## ", body_and_rest, re.MULTILINE)
    return body_and_rest[: next_heading.start()] if next_heading else body_and_rest


class _PrescribedShapes:
    """The resume-command shapes one template prescribes, with placeholders resolved so the
    file's own path is `{file}` (substituted by write_continuity_body) and the worktree is
    `_WORKTREE_PATH`."""

    def __init__(self, section_body: str) -> None:
        resolved = _OWN_FILE_PLACEHOLDER.sub("{file}", section_body.replace(_WORKTREE_PLACEHOLDER, _WORKTREE_PATH))
        assert _END_OF_BOTH_ALTERNATIVES in resolved, "template no longer ends its both-alternatives sentence with 'alone.'"
        sentence_end = resolved.index(_END_OF_BOTH_ALTERNATIVES) + len(_END_OF_BOTH_ALTERNATIVES)
        self.both_alternatives_sentence = resolved[:sentence_end].strip()
        assert "<" not in self.both_alternatives_sentence, f"unresolved placeholder: {self.both_alternatives_sentence!r}"
        self.explanatory_paragraph = resolved[sentence_end:].strip()
        commands = _BACKTICKED_SPAN.findall(self.both_alternatives_sentence)
        assert len(commands) == 2, f"expected a `--cwd` command and a bare command, found {commands!r}"
        (self.command_with_cwd, self.bare_command) = commands
        assert "--cwd" in self.command_with_cwd
        assert "--cwd" not in self.bare_command


@pytest.mark.parametrize(("skill_path", "heading_pattern"), _TEMPLATES)
class TestResumeCommandTemplateContract:
    def test_both_alternatives_sentence_parses_to_the_cwd_path(
        self, skill_path: Path, heading_pattern: str, tmp_path: Path
    ) -> None:
        shapes = _PrescribedShapes(_section_body(skill_path, heading_pattern))
        continuity_file = write_continuity_body(tmp_path / "handoffs" / "own-handoff.md", shapes.both_alternatives_sentence)
        assert _continuity_file_resume_cwd(continuity_file) == (0, _WORKTREE_PATH)

    def test_fenced_command_with_cwd_parses_to_the_cwd_path(
        self, skill_path: Path, heading_pattern: str, tmp_path: Path
    ) -> None:
        shapes = _PrescribedShapes(_section_body(skill_path, heading_pattern))
        continuity_file = write_continuity_body(
            tmp_path / "handoffs" / "own-handoff.md", f"```\n{shapes.command_with_cwd}\n```\n"
        )
        assert _continuity_file_resume_cwd(continuity_file) == (0, _WORKTREE_PATH)

    def test_bare_command_alone_reads_empty(self, skill_path: Path, heading_pattern: str, tmp_path: Path) -> None:
        shapes = _PrescribedShapes(_section_body(skill_path, heading_pattern))
        continuity_file = write_continuity_body(tmp_path / "handoffs" / "own-handoff.md", f"`{shapes.bare_command}`\n")
        assert _continuity_file_resume_cwd(continuity_file) == (0, "")

    def test_bare_command_followed_by_the_explanatory_cwd_paragraph_is_unknown(
        self, skill_path: Path, heading_pattern: str, tmp_path: Path
    ) -> None:
        """The paragraph holds `--cwd` text but no own command carries a `--cwd`, so the parser
        cannot prove the file names no worktree. It fails closed to `unknown`, which gets the
        shell route, and this test pins that intended result."""
        shapes = _PrescribedShapes(_section_body(skill_path, heading_pattern))
        assert "--cwd" in shapes.explanatory_paragraph, "template no longer explains `--cwd`; this pin is stale"
        continuity_file = write_continuity_body(
            tmp_path / "handoffs" / "own-handoff.md", f"`{shapes.bare_command}`\n{shapes.explanatory_paragraph}\n"
        )
        assert _continuity_file_resume_cwd(continuity_file) == (0, _UNUSABLE)
