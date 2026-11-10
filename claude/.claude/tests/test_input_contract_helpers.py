"""Contract tests for the Input contract helpers in helpers.py."""
from __future__ import annotations

from pathlib import Path

import pytest
from helpers import (
    backticked_field_tokens,
    heading_section_bounds,
    input_contract_field_tokens,
    input_contract_section,
    prompt_contract_agent_names,
)


def test_field_token_is_read_from_a_backticked_bullet_lead_with_an_underscore() -> None:
    section_text = "- `plan_path:` — derived: the plan file's absolute path.\n"

    assert input_contract_field_tokens(section_text) == {"plan_path:"}


def test_indented_sub_bullet_is_not_a_field_token() -> None:
    section_text = (
        "- `prior_decisions:` — on a re-review only:\n"
        "  - `derived:` the paths of earlier findings files.\n"
    )

    assert input_contract_field_tokens(section_text) == {"prior_decisions:"}


def test_bold_lead_bullet_is_not_a_field_token() -> None:
    section_text = "- **`findings_path:`** — where to write findings.\n"

    assert input_contract_field_tokens(section_text) == set()


def test_bold_lead_bullet_with_a_colon_bearing_token_is_not_a_field_token() -> None:
    section_text = "- **`plan_path:`** — derived: the plan file's absolute path.\n"

    assert input_contract_field_tokens(section_text) == set()


@pytest.mark.parametrize(
    "near_miss_bullet",
    [
        "- `diff`: — derived: the diff.\n",
        "* `diff:` — derived: the diff.\n",
        "- `plan-path:` — derived: the plan file's absolute path.\n",
        "-  `diff:` — derived: the diff.\n",
    ],
    ids=["colon-outside-backticks", "asterisk-bullet", "hyphenated-token", "two-spaces-after-dash"],
)
def test_near_miss_bullet_shape_is_not_a_field_token(near_miss_bullet: str) -> None:
    """The helper accepts only the exact bullet shape "- `token:`", narrower than `agent-review` item 19's wording.

    A near-miss bullet enrolls its agent in neither completeness check.
    Loosening the bullet regex to accept one is a deliberate decision, not a drive-by.
    """
    assert input_contract_field_tokens(near_miss_bullet) == set()


def test_backticked_field_tokens_extracts_a_token_from_mid_sentence_text() -> None:
    sentence = "Its prompt carries `diff:` and, on a re-review, `prior_decision:` as well."

    assert backticked_field_tokens(sentence) == {"diff:", "prior_decision:"}


def test_backticked_field_tokens_ignores_backticked_paths_and_unterminated_names() -> None:
    sentence = "See `claude/.claude/agents/example.md` and `findings_path` without a colon."

    assert backticked_field_tokens(sentence) == set()


def test_heading_section_bounds_ends_before_the_next_second_level_heading() -> None:
    lines = ["# Title\n", "## Wanted\n", "text\n", "### Sub\n", "## Next\n", "more\n"]

    assert heading_section_bounds(lines, "## Wanted") == (1, 4)


def test_heading_section_bounds_ends_at_the_line_count_when_the_section_is_last() -> None:
    lines = ["## Earlier\n", "text\n", "## Wanted\n", "last line\n"]

    assert heading_section_bounds(lines, "## Wanted") == (2, 4)


def test_heading_section_bounds_is_one_line_when_the_next_line_is_another_second_level_heading() -> None:
    lines = ["## Wanted\n", "## Next\n", "more\n"]

    assert heading_section_bounds(lines, "## Wanted") == (0, 1)


def test_heading_section_bounds_is_none_when_the_heading_is_absent() -> None:
    lines = ["## Wanted extra\n", "text\n", "### Wanted\n"]

    assert heading_section_bounds(lines, "## Wanted") is None


def test_input_contract_section_is_empty_for_a_file_without_the_heading(tmp_path: Path) -> None:
    agent_path = tmp_path / "no-heading.md"
    agent_path.write_text("# Agent\n\n## Scope\n\nBody text.\n")

    assert input_contract_section(agent_path) == ""


def test_input_contract_section_stops_before_the_next_second_level_heading(tmp_path: Path) -> None:
    agent_path = tmp_path / "with-heading.md"
    agent_path.write_text(
        "# Agent\n\n"
        "## Input contract\n\n"
        "- `diff:` — derived: the diff.\n\n"
        "### Sub heading\n\n"
        "Sub text.\n\n"
        "## Scope\n\n"
        "Scope text.\n"
    )

    section = input_contract_section(agent_path)

    assert section.startswith("## Input contract\n")
    assert "Sub text." in section
    assert "Scope" not in section


def test_input_contract_section_runs_to_end_of_file_when_it_is_the_last_section(
    tmp_path: Path,
) -> None:
    agent_path = tmp_path / "last-section.md"
    agent_path.write_text(
        "# Agent\n\n## Scope\n\nScope text.\n\n## Input contract\n\n- `diff:` — derived: the diff.\n"
    )

    assert input_contract_section(agent_path) == (
        "## Input contract\n\n- `diff:` — derived: the diff.\n"
    )


@pytest.mark.parametrize(
    "near_miss_heading",
    ["## Input contracts", "## Input contract (v2)", "## Input Contract", "### Input contract"],
)
def test_input_contract_section_is_empty_for_a_near_miss_heading(
    tmp_path: Path, near_miss_heading: str
) -> None:
    agent_path = tmp_path / "near-miss-heading.md"
    agent_path.write_text(f"{near_miss_heading}\n\n- `diff:` — derived: the diff.\n")

    assert input_contract_section(agent_path) == ""


def test_input_contract_section_is_empty_for_a_heading_with_trailing_whitespace(
    tmp_path: Path,
) -> None:
    """The heading match is line-exact: it strips only the newline, so a trailing space reads as no section."""
    agent_path = tmp_path / "padded-heading.md"
    agent_path.write_text("## Input contract \n\n- `diff:` — derived: the diff.\n")

    assert input_contract_section(agent_path) == ""


def test_input_contract_section_is_found_under_a_crlf_heading_because_read_text_translates_newlines(
    tmp_path: Path,
) -> None:
    agent_path = tmp_path / "crlf-heading.md"
    agent_path.write_bytes(b"## Input contract\r\n\r\n- `diff:` \xe2\x80\x94 derived: the diff.\r\n")

    assert input_contract_section(agent_path).startswith("## Input contract\n")


def test_prompt_contract_agent_names_returns_the_field_bullet_agent_and_not_the_bold_lead_one(
    tmp_path: Path,
) -> None:
    (tmp_path / "field-bullets.md").write_text(
        "## Input contract\n\n- `diff:` — derived: the diff.\n"
    )
    (tmp_path / "bold-lead.md").write_text(
        "## Input contract\n\n- **`diff:`** — derived: the diff.\n"
    )
    (tmp_path / "no-contract.md").write_text("## Scope\n\nBody.\n")

    assert prompt_contract_agent_names(tmp_path) == {"field-bullets.md"}


def test_prompt_contract_agent_names_ignores_a_field_bullet_under_a_different_heading(
    tmp_path: Path,
) -> None:
    (tmp_path / "bullet-in-output-format.md").write_text(
        "## Input contract\n\nProse only.\n\n## Output format\n\n- `diff:` — derived: the diff.\n"
    )

    assert prompt_contract_agent_names(tmp_path) == set()
