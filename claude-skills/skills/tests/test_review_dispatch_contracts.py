"""Reviewer dispatch: the prompt-contract sites, findings_path wiring, the declaration block and the routing reviewer table."""
from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import pytest
from _skill_md_helpers import (
    _AGENTS_DIR,
    _CODE_REVIEW_FINDING_DISPOSITION_HEADING,
    _CODE_REVIEW_RIPPLE_HEADING,
    _STEP4_HEADING,
    _agent_body,
    _all_skill_md_paths,
    _heading_section_text,
    _scope_exempt_change_type_row_text,
    _skill_body,
    _skill_file,
)

# pyproject.toml's pythonpath also puts claude/.claude/tests on the import
# path, where these shared test helpers live.
from helpers import (
    backticked_field_tokens,
    input_contract_field_tokens,
    input_contract_section,
    prompt_contract_agent_names,
)

# Single source of truth for SKILL.md structural rules — the commit-gate hook
# shells out to the same module. pyproject.toml's [tool.pytest.ini_options]
# pythonpath puts plugins/skill-management/scripts and evals/ on the import path.
from validate_skill_structure import parse_frontmatter


def test_plan_review_hands_comment_discipline_reviewer_a_plan_path() -> None:
    """The dispatcher and the agent name the same input token, and the spawn keeps its `/review-pr` skip.

    Its `plan_path:` checks overlap `test_prompt_contract_site_matches_agent_contract`.
    It stays for the spawn-condition literals: the Step 5 condition and the `/review-pr` skip.
    """
    routing_roles_text = _heading_section_text(_ROUTING_MD_PATH, "## Reviewer roles")
    agent_input_contract_text = _heading_section_text(
        _AGENTS_DIR / "comment-discipline-reviewer.md", "## Input contract"
    )
    for literal in (
        "`plan_path:`",
        "on every round that reaches Step 5 with a plan file",
        "Skip it under `/review-pr`'s standing override",
    ):
        assert literal in routing_roles_text, (
            f"{_ROUTING_MD_PATH}: Reviewer roles no longer carries {literal!r}"
        )
    assert "`plan_path:`" in agent_input_contract_text, (
        "comment-discipline-reviewer.md: Input contract no longer names `plan_path:`"
    )


class _PromptContractDispatchSite(NamedTuple):
    label: str
    agent_file: str
    site_text: Callable[[], str]
    sent_tokens: frozenset[str]
    exclusion_sentence: str
    forbidden_phrases: tuple[str, ...]


_PLAN_REVIEW_COMMENT_DISCIPLINE_SPAWN_ANCHOR = "- Always spawn `comment-discipline-reviewer`"


def _plan_review_comment_discipline_spawn_text(routing_md_path: Path) -> str:
    """The comment-discipline-reviewer spawn bullet and its indented continuation paragraph in ROUTING.md.

    An unindented continuation line ends the window, so a sentence added there
    falls outside it.
    """
    lines = routing_md_path.read_text().splitlines()
    start_index = next(
        (
            index
            for index, line in enumerate(lines)
            if line.startswith(_PLAN_REVIEW_COMMENT_DISCIPLINE_SPAWN_ANCHOR)
        ),
        None,
    )
    assert start_index is not None, (
        f"{routing_md_path}: no line starts with "
        f"{_PLAN_REVIEW_COMMENT_DISCIPLINE_SPAWN_ANCHOR!r}"
    )
    end_index = start_index + 1
    while end_index < len(lines) and (
        not lines[end_index].strip() or lines[end_index].startswith(" ")
    ):
        end_index += 1
    return "\n".join(lines[start_index:end_index]).rstrip()


class TestPlanReviewCommentDisciplineSpawnText:
    _SPAWN_BULLET = (
        "- Always spawn `comment-discipline-reviewer` on every round.\n"
        "  - `plan_path:` with the plan file's path\n"
        "\n"
        "  It gets no Item ownership routing. It keys its findings by angle name.\n"
    )

    def test_text_appended_after_the_paragraphs_last_sentence_is_inside_the_window(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "ROUTING.md"
        path.write_text(
            self._SPAWN_BULLET.rstrip("\n") + " Also pass it `declared:`.\n"
            "\n"
            "- Next unindented bullet.\n"
        )

        window = _plan_review_comment_discipline_spawn_text(path)

        assert window.endswith("Also pass it `declared:`.")
        assert "It gets no Item ownership routing." in window

    def test_next_unindented_bullet_is_outside_the_window(self, tmp_path: Path) -> None:
        path = tmp_path / "ROUTING.md"
        path.write_text(self._SPAWN_BULLET + "\n- Always spawn `other-reviewer` for `declared:`.\n")

        window = _plan_review_comment_discipline_spawn_text(path)

        assert "other-reviewer" not in window
        assert "declared:" not in window

    @pytest.mark.parametrize("trailing_blank_lines", ["", "\n", "\n\n\n"])
    def test_spawn_bullet_that_ends_the_file_returns_its_whole_paragraph(
        self, tmp_path: Path, trailing_blank_lines: str
    ) -> None:
        path = tmp_path / "ROUTING.md"
        path.write_text("- Earlier bullet.\n\n" + self._SPAWN_BULLET + trailing_blank_lines)

        window = _plan_review_comment_discipline_spawn_text(path)

        assert window == self._SPAWN_BULLET.rstrip("\n")

    def test_reworded_start_anchor_raises_an_assertion_error_naming_the_anchor(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "ROUTING.md"
        path.write_text(
            self._SPAWN_BULLET.replace("Always spawn", "Spawn")
        )

        with pytest.raises(AssertionError) as excinfo:
            _plan_review_comment_discipline_spawn_text(path)
        assert str(path) in str(excinfo.value)
        assert _PLAN_REVIEW_COMMENT_DISCIPLINE_SPAWN_ANCHOR in str(excinfo.value)


# Every dispatch site that hands an agent with an Input contract field list its prompt.
# A new site for a listed agent goes here, or it escapes test_prompt_contract_site_matches_agent_contract.
_PROMPT_CONTRACT_DISPATCH_SITES = (
    _PromptContractDispatchSite(
        label="code-review",
        agent_file="comment-discipline-reviewer.md",
        site_text=lambda: _scope_exempt_change_type_row_text(_skill_file("code-review")),
        sent_tokens=frozenset({"diff:", "findings_path:", "prior_decisions:"}),
        exclusion_sentence=(
            "Send it no diff scope, specific question, declared value or declaration "
            'block from the "Spawn per question" paragraph, and no return budget from '
            'the "Other spawned specialists" paragraph below this table.'
        ),
        forbidden_phrases=(
            "declaration block",
            "specific question",
            "return budget",
            "diff scope",
            "declared value",
            "declared:",
        ),
    ),
    _PromptContractDispatchSite(
        label="plan-review",
        agent_file="comment-discipline-reviewer.md",
        site_text=lambda: _plan_review_comment_discipline_spawn_text(_ROUTING_MD_PATH),
        sent_tokens=frozenset({"plan_path:", "findings_path:", "prior_decisions:"}),
        exclusion_sentence=(
            "It gets no Item ownership routing and no Ledger cross-check instruction."
        ),
        forbidden_phrases=(
            "declared:",
            "Declaration block",
            "Item ownership",
            "Ledger cross-check",
        ),
    ),
)


@pytest.mark.parametrize(
    "site", _PROMPT_CONTRACT_DISPATCH_SITES, ids=lambda site: site.label
)
def test_prompt_contract_site_matches_agent_contract(site: _PromptContractDispatchSite) -> None:
    """A dispatching site cites the agent's Input contract and names exactly the fields it sends, each one the contract lists.

    Each item the site's exclusion sentence names appears nowhere else in the site,
    and the site carries the phrase "its prompt carries only". Other added prose goes
    unchecked, and the agent's shared sentences set it aside at runtime.

    The agent's Input contract is the one home for what its dispatch prompt may carry.
    docs/design-decisions/agent-input-contract-owns-dispatch-fields.md records why the contract owns the fields.
    """
    site_text = site.site_text()
    agent_contract_tokens = input_contract_field_tokens(
        input_contract_section(_AGENTS_DIR / site.agent_file)
    )
    assert agent_contract_tokens, (
        f"{site.agent_file}: '## Input contract' section not found or lists no field bullet."
    )
    citation = f'`claude/.claude/agents/{site.agent_file}` § "Input contract"'
    assert citation in site_text, f"{site.label}: site no longer cites {citation}"
    named_tokens = backticked_field_tokens(site_text)
    assert named_tokens == site.sent_tokens, (
        f"{site.label}: site names {sorted(named_tokens)}, but the fields it sends are "
        f"{sorted(site.sent_tokens)}. A backticked mention of a field token counts as sending it, "
        "even in a negation, so the exclusion sentence names the declared value without backticks."
    )
    assert site.sent_tokens <= agent_contract_tokens, (
        f"{site.label}: {sorted(site.sent_tokens - agent_contract_tokens)} not in "
        f"{site.agent_file}'s Input contract fields {sorted(agent_contract_tokens)}"
    )
    assert site_text.count(site.exclusion_sentence) == 1, (
        f"{site.label}: site must carry its exclusion sentence exactly once: "
        f"{site.exclusion_sentence!r}"
    )
    lowered_site_text = site_text.lower()
    assert "its prompt carries only" in lowered_site_text, (
        f"{site.label}: site no longer says its prompt carries only the fields it sends."
    )
    lowered_exclusion_sentence = site.exclusion_sentence.lower()
    for phrase in site.forbidden_phrases:
        lowered_phrase = phrase.lower()
        allowed_count = lowered_exclusion_sentence.count(lowered_phrase)
        assert lowered_site_text.count(lowered_phrase) == allowed_count, (
            f"{site.label}: {phrase!r} appears {lowered_site_text.count(lowered_phrase)} time(s) "
            f"in the site, but only {allowed_count} inside its exclusion sentence. The agent's "
            "Input contract does not list that part of a prompt."
        )


def test_code_review_exclusion_sentence_cites_paragraphs_that_exist() -> None:
    """The /code-review row's exclusion sentence names two paragraphs, so each must still open a line in the skill.

    Both names also occur inside the exclusion sentence, so only a line-start match proves the paragraph exists.
    """
    skill_md_path = _skill_file("code-review")
    lines = skill_md_path.read_text().splitlines()
    for paragraph_name in ("Spawn per question", "Other spawned specialists"):
        assert any(line.startswith(paragraph_name) for line in lines), (
            f"{skill_md_path}: no line starts with {paragraph_name!r}, the paragraph the "
            "comment-discipline row's exclusion sentence cites."
        )


def test_prompt_contract_sites_cover_every_contract_agent() -> None:
    """An agent under claude/.claude/agents with Input contract field bullets and no listed dispatch site fails here.

    Scans claude/.claude/agents only, not plugin agent directories.
    """
    agents_with_sites = {site.agent_file for site in _PROMPT_CONTRACT_DISPATCH_SITES}
    agents_with_fields = prompt_contract_agent_names(_AGENTS_DIR)
    assert agents_with_sites == agents_with_fields, (
        f"Agents with Input contract field bullets are {sorted(agents_with_fields)}, but "
        f"_PROMPT_CONTRACT_DISPATCH_SITES names {sorted(agents_with_sites)}. Add each dispatching "
        "site of the missing agent(s) to _PROMPT_CONTRACT_DISPATCH_SITES."
    )


def test_plan_prose_findings_never_block_and_plan_diffs_stay_out_of_lane() -> None:
    """These sentences keep plan prose non-blocking and reviewed once, before approval.

    The plan rule is pinned because a plan may cite its own review rounds without being flagged.
    """
    plan_review_skill_path = _skill_file("plan-review")
    output_format_text = _heading_section_text(plan_review_skill_path, "## Output format")
    agent_scope_text = _heading_section_text(
        _AGENTS_DIR / "comment-discipline-reviewer.md", "## Scope"
    )
    rerun_note = (
        "Re-run the cells in docs/design-decisions/plan-prose-reviewed-inside-plan-review.md "
        "by hand after this edit."
    )
    non_blocking_literal = (
        "A plan prose finding never makes the verdict **Request changes**, "
        "whatever severity the reviewer gave it, so plan prose alone never adds a review round."
    )
    approve_with_changes_literal = (
        "A round whose only findings are plan prose ends **Approve with changes** "
        "when you accept any of them. It ends **Approve** otherwise."
    )
    diff_exclusion_literal = (
        "A plan file under `.claude/plans/` that appears in a diff is out of scope"
    )
    review_rounds_literal = "references to its own review rounds and findings"
    assert non_blocking_literal in output_format_text, (
        f"{plan_review_skill_path}: Output format no longer carries "
        f"{non_blocking_literal!r}. {rerun_note}"
    )
    assert approve_with_changes_literal in output_format_text, (
        f"{plan_review_skill_path}: Output format no longer carries "
        f"{approve_with_changes_literal!r}. {rerun_note}"
    )
    assert diff_exclusion_literal in agent_scope_text, (
        f"comment-discipline-reviewer.md: Scope no longer carries {diff_exclusion_literal!r}. "
        f"{rerun_note}"
    )
    assert review_rounds_literal in agent_scope_text, (
        f"comment-discipline-reviewer.md: Scope no longer lists {review_rounds_literal!r}. "
        f"{rerun_note}"
    )


# --- findings_path wiring in plan-review/ROUTING.md ---
# The mechanical recipe (info/exclude append, <epoch>-<slug> derivation) lives
# in claude/.claude/scripts/findings-path-suffix.sh, so each dispatcher body
# carries only the contract statement (path template, spawn-synchronously
# rule, read-back protocol).
#
# Four tests:
# 1. Recipe-sync tripwire: code-review/SKILL.md and ROUTING.md must match
#    verbatim on the surviving contract tokens (path template, background-spawn
#    rule, whole-file read-back).
# 2. Reviewer-contract coverage backstop: only contract-carrying reviewers
#    may receive a findings_path.
# 3. Script-invocation presence check across the three dispatchers.
# 4. Retired-recipe absence scan across every skill body, so a fourth
#    dispatcher copy-pasting the old recipe fails rather than passing unseen.

_ROUTING_MD_PATH = _skill_file("plan-review").parent / "ROUTING.md"

# Contract tokens pinned verbatim, not just paraphrased — code-review/SKILL.md
# and plan-review/ROUTING.md must reuse the same findings_path template and
# background-spawn rule, so a future edit to either would drop the exact
# substring pinned here and fail, rather than passing a looser template-only
# check. The background-spawn rule is pinned as the negated phrase actually
# used ("not `run_in_background`"), not the bare `run_in_background` token —
# a future edit permitting background spawn would still contain the bare
# substring and pass while re-introducing the same-turn read-back race the
# rule exists to prevent. The read-back token guards removal of the
# whole-file-read sentence, not a scope qualifier appended to it later.
# ready-for-review/SKILL.md is deliberately excluded here, because its contract
# statement is not a verbatim match for these tokens.
# It spawns one named reviewer (`agent-reviews/skill-fidelity-reviewer-<suffix>.md`,
# not the `<agent-name>` placeholder).
# It says "synchronously" rather than carrying the `not run_in_background` phrase.
# Its read-back sentence carries no scope qualifier.
_FINDINGS_PATH_RECIPE_TOKENS = (
    "agent-reviews/<agent-name>-<suffix>.md",
    "not `run_in_background`",
    "`Read` the whole findings file, whatever its finding count",
)


def test_findings_path_recipe_tokens_present_in_code_review_and_plan_review() -> None:
    """code-review/SKILL.md and plan-review/ROUTING.md must carry the same
    findings_path contract tokens, verbatim.

    Modeled on test_invalid_skip_rationale_labels_match_across_review_skills
    in test_review_scope_rules.py — same two-dispatcher-sync shape, applied to the findings_path
    contract instead of the skip-rationale label set. Asserts presence per
    token per file rather than block byte-equality: the surrounding prose
    legitimately differs between a dispatcher-generic paragraph
    (code-review/SKILL.md) and one scoped to reviewers carrying the
    file-based-output contract (ROUTING.md), so
    TestFileBasedOutputBlockConsistency's byte-identical-template approach
    (test_agent_roster.py) does not transfer here.
    """
    code_review_text = _skill_body("code-review")
    routing_text = _ROUTING_MD_PATH.read_text()

    for token in _FINDINGS_PATH_RECIPE_TOKENS:
        assert token in code_review_text, (
            f"code-review/SKILL.md: findings_path contract token {token!r} missing."
        )
        assert token in routing_text, (
            f"plan-review/ROUTING.md: findings_path contract token {token!r} "
            "missing — ROUTING.md's findings_path wiring paragraph must reuse "
            "code-review/SKILL.md's contract statement verbatim."
        )


_LEDGER_INLINE_OBLIGATION_TOKEN = "**Ledger cross-check** instruction's own wording copied into the spawn prompt as literal text"


def test_routing_spawn_step_states_ledger_instruction_passed_as_literal_text() -> None:
    """The spawn-step paragraph in plan-review/ROUTING.md must state the
    Ledger cross-check instruction is copied into the spawn prompt as
    literal text, not left as a pointer to the "## Ledger cross-check"
    section below.

    Sampled dispatches carried a pointer instead of the inlined instruction,
    so the obligation to inline it is pinned at the spawn step rather than
    left to a one-time skill-review pass to keep catching a reversion.
    The token is the Ledger cross-check clause's own wording, so the
    declaration block's copy-as-literal-text sentence cannot satisfy it.
    The search is scoped to the text preceding the "## Ledger cross-check"
    heading, so wording inside that section cannot satisfy it either.
    """
    routing_text = _ROUTING_MD_PATH.read_text()
    ledger_heading = "## Ledger cross-check"
    heading_index = routing_text.index(f"\n{ledger_heading}")
    spawn_step_text = routing_text[:heading_index]

    assert _LEDGER_INLINE_OBLIGATION_TOKEN in spawn_step_text, (
        "plan-review/ROUTING.md: the spawn-step section (before "
        f"{ledger_heading!r}) no longer states the ledger cross-check "
        f"instruction is {_LEDGER_INLINE_OBLIGATION_TOKEN!r} — a pointer to "
        "the Ledger cross-check section is not sufficient, since the "
        "spawned reviewer's prompt must carry the instruction's literal "
        "wording."
    )


_CODE_REVIEW_SKILL_PATH = _skill_file("code-review")
_PLAN_REVIEW_SKILL_PATH = _skill_file("plan-review")
_CODE_REVIEW_STEP_1_HEADING = "## Step 1 — Implementation-fitness gate"
_PLAN_REVIEW_OUTPUT_HEADING = "## Output format"
_ROUTING_ROLES_HEADING = "## Reviewer roles"

# Each skip pin runs from its first word to its sentence's closing period, so an arm added in that span breaks the match.
# A pin does not detect text inserted ahead of its first word.
_CODE_REVIEW_HIGH_STAKES_SKIP_CLAUSE = (
    "are near-mandatory: skip only when Step 1 reports `declared: dev-only, cooperative`, "
    "and name the surface in the output."
)
_CODE_REVIEW_OTHER_ROW_SKIP_CLAUSE = "Any other row skip that rests on Step 1's declaration needs the same value."
_CODE_REVIEW_CISO_SKIP_CLAUSE = (
    "high-stakes-boundary case is non-optional, **unless** Step 1 reports `declared: dev-only, cooperative` "
    "and the change crosses no privilege boundary."
)
_ROUTING_REVIEWER_SKIP_CLAUSE = (
    "Skip only when Step 4 reports `declared: dev-only, cooperative` and that declaration puts the change "
    "outside that reviewer's threat model — and name the skip rationale in the output, not silently."
)
_ROUTING_CISO_SKIP_CLAUSE = (
    "high-stakes-boundary case is non-optional, **unless** Step 4 reports `declared: dev-only, cooperative` "
    "and that declaration puts the change outside `ciso-reviewer`'s threat model."
)

# (file, section heading, clause). Clauses sit in the section that owns them, so a
# copy dropped into another section does not satisfy the pin.
_DECLARATION_WIRING_CLAUSES = (
    (
        _CODE_REVIEW_SKILL_PATH,
        _CODE_REVIEW_STEP_1_HEADING,
        "Also declare the user surface and threat tier the change serves as a `declared: <surface>, <tier>` tag "
        "on the **Spawn decisions:** line",
    ),
    (
        _CODE_REVIEW_SKILL_PATH,
        _CODE_REVIEW_STEP_1_HEADING,
        "ticket or an instruction-file line added or edited anywhere since the merge base",
    ),
    (_CODE_REVIEW_SKILL_PATH, _CODE_REVIEW_STEP_1_HEADING, "can raise a field but never settles one"),
    (
        _CODE_REVIEW_SKILL_PATH,
        _CODE_REVIEW_STEP_1_HEADING,
        "A plan for a branch other than the one under review is no source",
    ),
    (
        _CODE_REVIEW_SKILL_PATH,
        _CODE_REVIEW_STEP_1_HEADING,
        "When those sources cannot settle both fields, report `declared: undeclared`",
    ),
    (
        _CODE_REVIEW_SKILL_PATH,
        _CODE_REVIEW_STEP_1_HEADING,
        "Defensive paths against an actor or input the declared tier excludes",
    ),
    (_CODE_REVIEW_SKILL_PATH, _CODE_REVIEW_RIPPLE_HEADING, _CODE_REVIEW_HIGH_STAKES_SKIP_CLAUSE),
    (_CODE_REVIEW_SKILL_PATH, _CODE_REVIEW_RIPPLE_HEADING, _CODE_REVIEW_OTHER_ROW_SKIP_CLAUSE),
    (_CODE_REVIEW_SKILL_PATH, _CODE_REVIEW_RIPPLE_HEADING, _CODE_REVIEW_CISO_SKIP_CLAUSE),
    (
        _CODE_REVIEW_SKILL_PATH,
        _CODE_REVIEW_RIPPLE_HEADING,
        "When Step 1 declared a value other than `undeclared`, also pass that `declared:` value "
        "and the declaration block",
    ),
    (
        _CODE_REVIEW_SKILL_PATH,
        _CODE_REVIEW_RIPPLE_HEADING,
        "The declaration block is the text that follows this sentence through the last item of the list below, "
        "copied into the spawn prompt as literal text",
    ),
    (
        _CODE_REVIEW_SKILL_PATH,
        _CODE_REVIEW_RIPPLE_HEADING,
        "review as you would without this declaration, and drop no finding because of it",
    ),
    (
        _CODE_REVIEW_SKILL_PATH,
        _CODE_REVIEW_RIPPLE_HEADING,
        "Put `beyond declaration` in the heading or first line of a finding whose failure needs a user, actor or input "
        "that the declaration excludes and the change does not reach",
    ),
    (
        _CODE_REVIEW_SKILL_PATH,
        _CODE_REVIEW_RIPPLE_HEADING,
        "The surface is `dev-only` when nothing the change does or adds reaches a production system, production data, "
        "end users, a copy installed for anyone beyond those who build and test it",
    ),
    (
        _CODE_REVIEW_SKILL_PATH,
        _CODE_REVIEW_RIPPLE_HEADING,
        "or readers beyond them through a public repository's commits or PRs or a released artifact. "
        "Otherwise the surface is `production`",
    ),
    (
        _CODE_REVIEW_SKILL_PATH,
        _CODE_REVIEW_FINDING_DISPOSITION_HEADING,
        "The DEFER's rationale quotes the `declared:` value the **Spawn decisions:** line reports",
    ),
    (
        _CODE_REVIEW_SKILL_PATH,
        _CODE_REVIEW_FINDING_DISPOSITION_HEADING,
        "Under `declared: undeclared`, the rationale rests on such a route or on Step 1's implementation-fitness answer",
    ),
    (
        _PLAN_REVIEW_SKILL_PATH,
        _STEP4_HEADING,
        "Read `${CLAUDE_SKILL_DIR}/ROUTING.md` with the Read tool first",
    ),
    (
        _PLAN_REVIEW_SKILL_PATH,
        _STEP4_HEADING,
        "Then state the answer as a `declared:` value by the declaration rule `ROUTING.md` § \"Reviewer roles\" states.",
    ),
    (
        _PLAN_REVIEW_SKILL_PATH,
        _PLAN_REVIEW_OUTPUT_HEADING,
        "Step 4's `declared:` value, and which plan sections/phases were reviewed",
    ),
    (_ROUTING_MD_PATH, _ROUTING_ROLES_HEADING, _ROUTING_REVIEWER_SKIP_CLAUSE),
    (_ROUTING_MD_PATH, _ROUTING_ROLES_HEADING, _ROUTING_CISO_SKIP_CLAUSE),
    (
        _ROUTING_MD_PATH,
        _ROUTING_ROLES_HEADING,
        "Step 4 declares the user surface and threat tier the plan serves as a `declared: <surface>, <tier>` tag",
    ),
    (
        _ROUTING_MD_PATH,
        _ROUTING_ROLES_HEADING,
        "The plan under review is author text",
    ),
    (
        _ROUTING_MD_PATH,
        _ROUTING_ROLES_HEADING,
        "ticket or an instruction-file line added or edited anywhere since the merge base",
    ),
    (_ROUTING_MD_PATH, _ROUTING_ROLES_HEADING, "can raise a field but never settles one"),
    (
        _ROUTING_MD_PATH,
        _ROUTING_ROLES_HEADING,
        "A plan for a branch other than the one under review is no source",
    ),
    (
        _ROUTING_MD_PATH,
        _ROUTING_ROLES_HEADING,
        "When those sources cannot settle both fields, report `declared: undeclared`",
    ),
    (
        _ROUTING_MD_PATH,
        _ROUTING_ROLES_HEADING,
        "When Step 4 declared a value other than `undeclared`, also pass that `declared:` value "
        "and the declaration block above",
    ),
    (
        _ROUTING_MD_PATH,
        _ROUTING_ROLES_HEADING,
        "Copy the block into the spawn prompt as literal text, from `Declaration block:` through its "
        "`adversarial` item",
    ),
    (
        _ROUTING_MD_PATH,
        _ROUTING_ROLES_HEADING,
        "instruction's own wording copied into the spawn prompt as literal text",
    ),
)


def _declaration_wiring_case_id(case: tuple[Path, str, str]) -> str:
    path, _heading, clause = case
    return f"{path.parent.name}/{path.name}::{clause[:60]}"


@pytest.mark.parametrize(
    ("skill_path", "heading", "clause"),
    _DECLARATION_WIRING_CLAUSES,
    ids=[_declaration_wiring_case_id(case) for case in _DECLARATION_WIRING_CLAUSES],
)
def test_declaration_wiring_present_in_review_skills(skill_path: Path, heading: str, clause: str) -> None:
    """Pins each declared-surface wiring clause exactly once in its section.

    The tuple includes ROUTING.md's Ledger cross-check literal-text clause to count it exactly once in the Reviewer roles section.
    A wording-only edit can otherwise remove or duplicate a clause while every other test stays green.
    """
    section_text = _heading_section_text(skill_path, heading)
    occurrences = section_text.count(clause)

    assert occurrences == 1, (
        f"{skill_path.parent.name}/{skill_path.name} § {heading!r}: expected declaration clause {clause!r} "
        f"exactly once, found {occurrences}."
    )


_DECLARATION_BLOCK_START = "Declaration block: review"
_DECLARATION_TIER_ITEM_PREFIXES = (
    "- `cooperative` — ",
    "- `untrusted-input` — ",
    "- `adversarial` — ",
)


def _declaration_block_span(skill_path: Path) -> str:
    """Text from the declaration block's opening words through the end of its
    `adversarial` item, asserting the block's shape on the way.
    """
    label = f"{skill_path.parent.name}/{skill_path.name}"
    lines = skill_path.read_text().splitlines()
    start_indexes = [index for index, line in enumerate(lines) if _DECLARATION_BLOCK_START in line]
    assert len(start_indexes) == 1, (
        f"{label}: expected exactly one line containing {_DECLARATION_BLOCK_START!r}, found {len(start_indexes)}."
    )
    start_index = start_indexes[0]
    for offset, prefix in enumerate(_DECLARATION_TIER_ITEM_PREFIXES, start=1):
        item_index = start_index + offset
        item_line = lines[item_index] if item_index < len(lines) else ""
        assert item_line.startswith(prefix), (
            f"{label}: the line {offset} after the declaration block's opening line must start with {prefix!r}. "
            "The opening paragraph must be a single physical line, followed immediately by the tier items."
        )
    end_index = start_index + len(_DECLARATION_TIER_ITEM_PREFIXES) + 1
    line_after_block = lines[end_index] if end_index < len(lines) else ""
    assert end_index >= len(lines) or not line_after_block.strip() or line_after_block.startswith("#"), (
        f"{label}: the line after the declaration block's `adversarial` item must be blank, a heading or end of file, "
        f"found {line_after_block!r} — text added there sits outside the compared span."
    )
    opening_line = lines[start_index]
    opening_text = opening_line[opening_line.index(_DECLARATION_BLOCK_START) :]
    return "\n".join([opening_text, *lines[start_index + 1 : end_index]])


def test_declaration_block_identical_in_code_review_and_routing() -> None:
    """plan-review/ROUTING.md keeps its own copy of code-review/SKILL.md's
    declaration block, and the two must match.
    """
    code_review_block = _declaration_block_span(_CODE_REVIEW_SKILL_PATH)
    routing_block = _declaration_block_span(_ROUTING_MD_PATH)

    assert code_review_block == routing_block, (
        "code-review/SKILL.md and plan-review/ROUTING.md carry different declaration blocks — "
        "a fix to the block lands in both copies."
    )


def _routing_reviewer_agent_names() -> set[str]:
    """Agent names in plan-review/ROUTING.md's Reviewer roles table (the
    `| Domain | Agent | Focus |` table), read from the backtick-quoted
    second column of each row between the header and the table's end.
    """
    header = "| Domain | Agent | Focus |"
    lines = _ROUTING_MD_PATH.read_text().splitlines()
    header_idx = next((i for i, line in enumerate(lines) if line.strip() == header), None)
    assert header_idx is not None, (
        f"{_ROUTING_MD_PATH}: {header!r} not found — the Reviewer roles table may have moved."
    )
    names: set[str] = set()
    for line in lines[header_idx + 2 :]:  # skip the header row and its `|---|` separator
        if not line.startswith("|"):
            break
        match = re.match(r"^\|[^|]*\|\s*`([a-z0-9-]+)`\s*\|", line)
        if match:
            names.add(match.group(1))
    assert names, f"{_ROUTING_MD_PATH}: extracted no agent names from the Reviewer roles table."
    return names


def test_routing_reviewer_table_agents_carry_file_based_output_contract() -> None:
    """Every agent named in plan-review/ROUTING.md's Reviewer roles table
    must carry `Write` and a `### File-based output` section.

    This is a same-repo sync-drift backstop, not coverage of a downstream
    project-layer `plan-review-*` skill's own table extension:
    `_routing_reviewer_agent_names()` only ever reads this repo's own
    ROUTING.md, so what it actually catches is ROUTING.md's table naming
    an agent that `hooks/tests/test_agent_roster.py`'s `CANARY_AGENTS`
    list, or the agent's own frontmatter, has drifted out of sync with.
    The `findings_path` grant is conditioned on this contract, but as
    prose it is backstopped only by `agent-review` checklist item 15 — a
    point-in-time authoring check, not a dispatch-time one. A reviewer
    lacking the contract that is nonetheless passed a `findings_path`
    re-arms the heredoc-abort-on-large-findings failure, which for a
    security reviewer means silently lost findings. `tools:` is read via
    parse_frontmatter rather than a `^tools:` regex, mirroring
    test_agent_roster.py's `_declared_tool_names` — a regex silently reports the
    tool absent on YAML list form (`tools:\n  - Write`), which would turn
    this assertion into a false pass on exactly the frontmatter shape it
    exists to catch.
    """
    for name in sorted(_routing_reviewer_agent_names()):
        agent_path = _AGENTS_DIR / f"{name}.md"
        fm = parse_frontmatter(agent_path)
        declared_tools = fm.get("tools")
        assert declared_tools, f"{name}.md: no 'tools:' field found in frontmatter."
        tools = (
            {t.strip() for t in declared_tools.split(",")}
            if isinstance(declared_tools, str)
            else {str(t).strip() for t in declared_tools}
        )
        assert "Write" in tools, (
            f"{name}.md: 'Write' not declared in tools: {tools!r} — plan-review/ROUTING.md's "
            "Reviewer roles table names this agent, so it must carry the file-based-output "
            "contract before it can safely receive a findings_path."
        )
        assert "### File-based output" in _agent_body(name), (
            f"{name}.md: '### File-based output' section missing — plan-review/ROUTING.md's "
            "Reviewer roles table names this agent, so it must carry the file-based-output "
            "contract before it can safely receive a findings_path."
        )


# The three findings_path dispatchers, keyed by name for parametrize test-id
# readability. plan-review is keyed on its ROUTING.md co-located file, not
# SKILL.md, since that's where its findings_path wiring paragraph lives.
_FINDINGS_PATH_DISPATCHER_BODIES: dict[str, Callable[[], str]] = {
    "code-review": lambda: _skill_body("code-review"),
    "plan-review/ROUTING.md": lambda: _ROUTING_MD_PATH.read_text(),
    "ready-for-review": lambda: _skill_body("ready-for-review"),
}


@pytest.mark.parametrize("dispatcher_name", sorted(_FINDINGS_PATH_DISPATCHER_BODIES))
def test_findings_path_dispatcher_calls_suffix_script(dispatcher_name: str) -> None:
    """Each of the three findings_path dispatchers must call
    findings-path-suffix.sh rather than restating the derivation inline.

    Mirrors TestPrDescriptionCostSectionWiring.test_declares_account_scoped_mode_gate's
    shape (asserting a body invokes a named script by its literal path), applied
    to all three dispatchers instead of one.
    """
    body = _FINDINGS_PATH_DISPATCHER_BODIES[dispatcher_name]()
    assert "~/.claude/scripts/findings-path-suffix.sh" in body, (
        f"{dispatcher_name}: no longer calls findings-path-suffix.sh"
    )


# These three shell expressions must not appear in any skill body now that
# findings-path-suffix.sh computes them. Absence is checked across the whole
# skill corpus (below), not just the three known dispatchers — a copy-paste
# of the retired recipe into a fourth skill would otherwise pass unseen. A
# literal-substring check like this one can't catch an equivalent alternate
# spelling (e.g. `date '+%s'`) — it only closes the copy-paste regression
# shape, not every possible reimplementation.
_RETIRED_FINDINGS_PATH_RECIPE_EXPRESSIONS = (
    "$(date +%s)",
    "$(git rev-parse --abbrev-ref HEAD | tr '/' '-' | cut -c1-20)",
    "git rev-parse --git-path info/exclude",
)


def test_findings_path_retired_recipe_expressions_absent_from_every_skill_body() -> None:
    """None of the three retired findings_path derivation expressions may
    appear in any SKILL.md (stowed or plugin) or in plan-review/ROUTING.md.

    Modeled on test_ready_for_review_step3_never_produces_a_staged_diff's
    present-AND-absent shape, but scoped to the whole corpus rather than one
    file — this is the reachable half of the fourth-file regression: a new
    dispatcher copy-pasting the retired recipe fails here rather than
    silently forking the mechanism a second time.
    """
    for path in [*_all_skill_md_paths(), _ROUTING_MD_PATH]:
        text = path.read_text()
        for expr in _RETIRED_FINDINGS_PATH_RECIPE_EXPRESSIONS:
            assert expr not in text, (
                f"{path}: retired findings_path recipe expression {expr!r} still "
                "present — call findings-path-suffix.sh instead of restating the "
                "derivation inline"
            )


@pytest.mark.parametrize(
    "dispatcher_name, dispatcher_path",
    [
        pytest.param("code-review/SKILL.md", _skill_file("code-review"), id="code-review"),
        pytest.param("plan-review/ROUTING.md", _ROUTING_MD_PATH, id="plan-review"),
    ],
)
def test_review_dispatchers_still_hand_reviewers_a_relative_agent_reviews_findings_path(
    dispatcher_name: str, dispatcher_path: Path
) -> None:
    """review-pr/SKILL.md's standing override rewrites the leading
    `agent-reviews` of the `findings_path` each dispatcher hands a reviewer. A
    dispatcher reword away from `findings_path: agent-reviews/` would make that
    rewrite a silent no-op, leaving a main-tree session's reviewers with a path
    the worktree gate denies."""
    dispatcher_text = " ".join(dispatcher_path.read_text().split())
    assert "findings_path: agent-reviews/" in dispatcher_text, (
        f"{dispatcher_name} no longer hands reviewers `findings_path: agent-reviews/`, "
        "so review-pr/SKILL.md's override that replaces the leading `agent-reviews` "
        "no longer applies to it."
    )
