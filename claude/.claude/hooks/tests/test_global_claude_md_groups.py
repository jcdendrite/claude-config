"""Pin the two-group layout of the global `claude/.claude/CLAUDE.md`.

The file is split into `# Agent Core` (rules every agent follows) and
`# Main session` (rules only the main session and forks follow). The core
group must stay one contiguous block that opens the file, so it can be
extracted as-is. Nothing else in the suite checks which group a rule sits
in, so a bullet drifting across the boundary would go unnoticed.

Tracked under GH-1085. Only the group contract, the placements pinned
individually, and the core-placed prose rules are pinned here; per-bullet
placement for every other rule stays a manual check.
"""
from __future__ import annotations

import re

import pytest
from helpers import CLAUDE_DIR
from validate_skill_structure import parse_frontmatter

_GLOBAL_CLAUDE_MD = CLAUDE_DIR / "CLAUDE.md"
_SETTINGS_RULE = CLAUDE_DIR / "rules" / "settings-json-conventions.md"

_CORE_HEADING = "# Agent Core"
_MAIN_HEADING = "# Main session"

_EXPECTED_CORE_SECTIONS = [
    "## Safety",
    "## Engineering Judgment",
    "## Working Style",
    "## Prose and Output Format",
]
_EXPECTED_MAIN_SECTIONS = [
    "## Safety",
    "## Working Style",
    "## Code Review",
    "## Plan Review",
    "## Pre-Handoff Review",
    "## Agent Briefing",
    "## Model & Effort Routing",
    "## Shipping",
]

_GROUPS = ("core", "main")
# Compared whitespace-normalised and casefolded, so a wrapped, spaced, or re-capitalized copy in core fails.
_PROCEED_CLAUSE_FRAGMENT = "ask permission to proceed with work that is already done"
_PROCEED_CLAUSE = f"Do not {_PROCEED_CLAUSE_FRAGMENT}"

# (id, distinctive substring, expected group). Each substring is a phrase
# distinctive to its bullet, and must occur on exactly one line.
# "core" bullets must precede the Main session heading; "main" bullets must follow it.
_PLACEMENTS = [
    # Prose-backed rules with no hook backstop.
    ("no-autonomous-installs", "Installing new software autonomously is strictly prohibited", "core"),
    ("package-naming", "**Name every new package before it is fetched.**", "core"),
    ("secret-commits", "Never commit secrets, credentials, API keys", "core"),
    ("user-email", "The `userEmail` context identifies the user to you", "core"),
    ("credential-gate", "Never Read or `!`-cat files likely to hold secrets", "core"),
    ("least-privilege", "Apply the **principle of least privilege**", "core"),
    ("discover-the-target", "In destructive paths, discover the target", "core"),
    ("marker-hand-writes", "Never write `<config-dir>/*-markers/*` by hand", "core"),
    ("memory-md-guard", "**A `MEMORY.md` index line routes; it does not authorize.**", "core"),
    (
        "prove-the-failing-check-is-yours",
        "**Prove your change caused a failing check before treating it as in-scope.**",
        "core",
    ),
    ("attribution", "**Attribute to the engineer only what they said.**", "core"),
    ("destructive-action-confirm", "flag the risk and confirm the approach", "core"),
    ("stopping", "Stop when the work is genuinely blocked", "core"),
    ("durable-text-rules", "**No PR-defined terminology**", "core"),
    # Rules addressed to dispatched agents, or relocated into core from Agent Briefing.
    ("dispatch-denial", "**Dispatching cannot clear a denial your child inherits.**", "core"),
    (
        "fork-or-subagent-returns-rather-than-ships",
        "Merge stays human-only; any fork or subagent returns its work to its dispatcher rather than shipping on its own.",
        "core",
    ),
    ("worktree-edit-write-targeting", "Edit and Write must also target the worktree path", "core"),
    ("script-first-bash-recipes", "**Script-first for multi-step Bash recipes;", "core"),
    # Main-session-only bullets: autonomy grants, the output-preferences read, and clear-stale.
    (
        "autonomous-shipping",
        "**Where autonomous shipping is active, a request to do work is the ask.**",
        "main",
    ),
    (
        "no-permission-asking-on-finished-work",
        _PROCEED_CLAUSE,
        "main",
    ),
    ("prescribed-dispatch", "**A prescribed dispatch is an authorized dispatch.**", "main"),
    ("output-preferences", "output-preferences.md", "main"),
    ("clear-stale", "marker.sh clear-stale", "main"),
]

_OPENING_LINE_FRAGMENTS = [
    "Every agent follows Agent Core",
    "only the main session and forks follow Main session",
    "When dispatched,",
    "(ask, confirm, point, name, raise, defer)",
    "report it in your return",
    "take no action it gates",
    "stop means: return",
]
# Closing clause of the opening line: a qualifier inserted before "stop means: return"
# or appended after it must fail.
_ESCALATION_TAIL = "take no action it gates; stop means: return"

_WILDCARDS_STUB_LINE = "- No wildcards in `permissions.allow`."
_DURABLE_TEXT_HEADING = "### Durable text"
_PROSE_SECTION_HEADING = "## Prose and Output Format"


def _lines() -> list[str]:
    return _GLOBAL_CLAUDE_MD.read_text().splitlines()


def _outside_fences(lines: list[str]) -> list[str]:
    """Return `lines` with every fenced-code-block line (fences included) blanked, indices preserved."""
    visible: list[str] = []
    in_fence = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            visible.append("")
        else:
            visible.append("" if in_fence else line)
    return visible


def test_outside_fences_blanks_fenced_lines_and_preserves_indices():
    """A fenced `# Main session` example is invisible to heading lookups; line indices stay aligned."""
    lines = ["# Agent Core", "```", "# Main session", "```", "# Main session", "tail"]
    assert _outside_fences(lines) == ["# Agent Core", "", "", "", "# Main session", "tail"]
    assert _main_heading_index(lines) == 4


def _main_heading_index(lines: list[str]) -> int:
    """Return the index of the `# Main session` line, asserting it is unique.

    Matches the whole line, because the opening line also contains the words
    "Main session". Lines inside fenced code blocks are ignored.
    """
    matches = [
        index for index, line in enumerate(_outside_fences(lines)) if line == _MAIN_HEADING
    ]
    assert len(matches) == 1, (
        f"{_GLOBAL_CLAUDE_MD}: expected exactly one line equal to "
        f"{_MAIN_HEADING!r}, found {len(matches)}."
    )
    return matches[0]


def _index_of_only_line_containing(lines: list[str], fragment: str) -> int:
    matches = [
        index for index, line in enumerate(_outside_fences(lines)) if fragment in line
    ]
    assert matches, (
        f"{_GLOBAL_CLAUDE_MD}: fragment {fragment!r} not found on any line; "
        "the bullet was deleted, reworded, or rewrapped."
    )
    assert len(matches) == 1, (
        f"{_GLOBAL_CLAUDE_MD}: expected {fragment!r} on exactly one line, "
        f"found {len(matches)}. A short or common fragment can survive a "
        "bullet move; pin a phrase distinctive to the bullet."
    )
    return matches[0]


def test_h1_and_h2_headings_match_the_two_group_layout():
    """H1s are exactly Agent Core then Main session, with the decided H2s under each."""
    lines = _lines()
    visible_lines = _outside_fences(lines)
    h1_lines = [line for line in visible_lines if re.match(r"^# ", line)]
    assert h1_lines == [_CORE_HEADING, _MAIN_HEADING], (
        f"{_GLOBAL_CLAUDE_MD}: H1 lines are {h1_lines!r}; expected "
        f"{[_CORE_HEADING, _MAIN_HEADING]!r} so core stays one contiguous block."
    )

    main_index = _main_heading_index(lines)
    h2_before_main = [line for line in visible_lines[:main_index] if re.match(r"^## ", line)]
    h2_after_main = [line for line in visible_lines[main_index:] if re.match(r"^## ", line)]
    assert h2_before_main == _EXPECTED_CORE_SECTIONS, (
        f"{_GLOBAL_CLAUDE_MD}: `##` headings under {_CORE_HEADING} are "
        f"{h2_before_main!r}; expected {_EXPECTED_CORE_SECTIONS!r}."
    )
    assert h2_after_main == _EXPECTED_MAIN_SECTIONS, (
        f"{_GLOBAL_CLAUDE_MD}: `##` headings under {_MAIN_HEADING} are "
        f"{h2_after_main!r}; expected {_EXPECTED_MAIN_SECTIONS!r}."
    )


@pytest.mark.parametrize(
    ("fragment", "group"),
    [(fragment, group) for _, fragment, group in _PLACEMENTS],
    ids=[placement_id for placement_id, _, _ in _PLACEMENTS],
)
def test_pinned_bullet_sits_in_its_group(fragment, group):
    """Each pinned bullet sits on its group's side of the Main session heading."""
    assert group in _GROUPS, f"placement table group {group!r} must be one of {_GROUPS!r}."
    lines = _lines()
    index = _index_of_only_line_containing(lines, fragment)
    main_index = _main_heading_index(lines)
    if group == "core":
        assert index < main_index, (
            f"{_GLOBAL_CLAUDE_MD}: {fragment!r} moved below {_MAIN_HEADING!r}; "
            "an agent that skips Main session would lose this rule."
        )
    else:
        assert index > main_index, (
            f"{_GLOBAL_CLAUDE_MD}: {fragment!r} moved above {_MAIN_HEADING!r}; "
            "a dispatched agent would inherit a rule that is the main session's alone."
        )


def test_opening_line_states_audiences_and_escalation_translation_once():
    """One opening line sits between the core H1 and the first H2, with every contract fragment."""
    lines = _lines()
    assert lines[0] == _CORE_HEADING, (
        f"{_GLOBAL_CLAUDE_MD}: first line is {lines[0]!r}; expected {_CORE_HEADING!r}."
    )
    first_h2_index = next(
        index for index, line in enumerate(lines) if line.startswith("## ")
    )
    opening_lines = [line for line in lines[1:first_h2_index] if line.strip()]
    assert len(opening_lines) == 1, (
        f"{_GLOBAL_CLAUDE_MD}: expected exactly one opening line between "
        f"{_CORE_HEADING!r} and the first `## ` heading, found {len(opening_lines)}."
    )
    opening_line = opening_lines[0]
    for fragment in _OPENING_LINE_FRAGMENTS:
        assert fragment in opening_line, (
            f"{_GLOBAL_CLAUDE_MD}: opening line lacks {fragment!r}; the "
            "audience statement and escalation translation are its contract."
        )
    assert opening_line.rstrip().endswith(_ESCALATION_TAIL + "."), (
        f"{_GLOBAL_CLAUDE_MD}: opening line must end with the contiguous {_ESCALATION_TAIL!r}; "
        "a qualifier before or after that clause weakens the escalation translation."
    )
    occurrences = _GLOBAL_CLAUDE_MD.read_text().count(_OPENING_LINE_FRAGMENTS[0])
    assert occurrences == 1, (
        f"{_GLOBAL_CLAUDE_MD}: opening line text appears {occurrences} times; expected once."
    )


def test_durable_text_heading_closes_agent_core_after_prose_section():
    """`### Durable text` appears once, under Prose and Output Format and before Main session."""
    lines = _outside_fences(_lines())
    durable_indexes = [i for i, line in enumerate(lines) if line == _DURABLE_TEXT_HEADING]
    assert len(durable_indexes) == 1, (
        f"{_GLOBAL_CLAUDE_MD}: expected exactly one {_DURABLE_TEXT_HEADING!r} line, "
        f"found {len(durable_indexes)}."
    )
    prose_index = lines.index(_PROSE_SECTION_HEADING)
    assert prose_index < durable_indexes[0] < _main_heading_index(lines), (
        f"{_GLOBAL_CLAUDE_MD}: {_DURABLE_TEXT_HEADING!r} must sit after "
        f"{_PROSE_SECTION_HEADING!r} and before {_MAIN_HEADING!r}; every agent reads it."
    )


def test_options_rule_splits_across_the_group_boundary():
    """The evaluate-options half sits in Agent Core; the walk-through half in Main session."""
    lines = _lines()
    main_index = _main_heading_index(lines)
    core_half = "When presenting options, evaluate them"
    main_half = "Walk through your proposed approach"
    assert _index_of_only_line_containing(lines, core_half) < main_index, (
        f"{_GLOBAL_CLAUDE_MD}: {core_half!r} moved below {_MAIN_HEADING!r}; "
        "dispatched agents present options too."
    )
    assert _index_of_only_line_containing(lines, main_half) > main_index, (
        f"{_GLOBAL_CLAUDE_MD}: {main_half!r} moved above {_MAIN_HEADING!r}; "
        "a dispatched agent has no user to walk through an approach with."
    )


def test_wildcards_stub_is_exact_line_in_agent_core():
    """The `permissions.allow` wildcards stub is one exact line before Main session."""
    lines = _lines()
    matches = [index for index, line in enumerate(lines) if line == _WILDCARDS_STUB_LINE]
    assert len(matches) == 1, (
        f"{_GLOBAL_CLAUDE_MD}: expected exactly one line equal to {_WILDCARDS_STUB_LINE!r}, "
        f"found {len(matches)}; the detail lives in {_SETTINGS_RULE.name}."
    )
    assert matches[0] < _main_heading_index(lines), (
        f"{_GLOBAL_CLAUDE_MD}: the wildcards stub moved below {_MAIN_HEADING!r}."
    )


def test_proceed_clause_is_absent_from_agent_core():
    """The shipping antecedent lives in Main session, so Agent Core must not carry the clause."""
    lines = _lines()
    core_text = " ".join("\n".join(lines[: _main_heading_index(lines)]).split()).casefold()
    fragment = " ".join(_PROCEED_CLAUSE_FRAGMENT.split()).casefold()
    assert fragment not in core_text, (
        f"{_GLOBAL_CLAUDE_MD}: {_PROCEED_CLAUSE_FRAGMENT!r} appears in {_CORE_HEADING}; "
        "its antecedent (autonomous shipping) is Main session's alone."
    )


def _rule_body(rule_path) -> str:
    """Return the rule file text after its closing frontmatter fence."""
    parts = rule_path.read_text().split("---", 2)
    assert len(parts) == 3, f"{rule_path}: missing `---` frontmatter fences."
    return parts[2]


def test_settings_rule_file_keeps_globs_guidance_and_both_settings_paths():
    """The relocated globs rule must remain in the body, and `paths:` must cover both settings filenames."""
    body = _rule_body(_SETTINGS_RULE)
    for required in ("Don't add globs", "Use exact-match rules", "review-permissions/SKILL.md"):
        assert required in body, (
            f"{_SETTINGS_RULE}: rule body lacks {required!r}; the relocated "
            "`permissions.allow` globs guidance must stay complete, since "
            "CLAUDE.md keeps only a one-line stub."
        )
    paths = parse_frontmatter(_SETTINGS_RULE).get("paths", [])
    for expected_glob in ("**/settings.json", "**/settings.local.json"):
        assert expected_glob in paths, (
            f"{_SETTINGS_RULE}: frontmatter `paths:` is {paths!r}; it must "
            f"list {expected_glob!r}."
        )
