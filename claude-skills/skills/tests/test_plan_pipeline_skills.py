"""plan-it's Steps 5 and 7, plan-review's tripwire provenance, and the plan-architect agent."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
from _skill_md_helpers import (
    _GLOBAL_CLAUDE_MD,
    _PLAN_ARCHITECT_CONSULT_HEADING,
    _PLAN_ARCHITECT_PATH,
    _STEP4_HEADING,
    _assert_citation_resolves_to_heading,
    _assert_pinned_clause_right_bounded,
    _extract_citations,
    _heading_section_text,
    _raw_heading_section_text,
    _section_between,
    _skill_file,
)

# pyproject.toml's pythonpath also puts claude/.claude/tests on the import
# path, where these shared test helpers live.
from helpers import (
    REPO_ROOT,
    heading_texts,
    normalize_heading,
)

_PLAN_REVIEW_REFERENCES_MD = _skill_file("plan-review").parent / "REFERENCES.md"

_MAPPING_TABLE_HEADING = "## Tripwire → CLAUDE.md principle mapping"
_FOUNDATION_RULES_HEADING = "## Foundation-tripwire rules — surfacing incident"

_TRIPWIRE_BULLET_RE = re.compile(r"^\s{2,}-\s+\*\*(?P<name>[^*]+)\.\*\*")
_MAPPING_ROW_RE = re.compile(r"^\|\s*(?P<name>[^|]+?)\s*\|")

# Bumping this is the point of the ratchet: an eighth tripwire must also
# gain a mapping-table row and a provenance paragraph, or this constant
# stays stale and the count assertion below catches the omission.
_EXPECTED_TRIPWIRE_COUNT = 7


_PLAN_IT_STEP7_HEADING = (
    "## Step 7 — Commit or unwind the plan, then choose where implementation runs"
)

# plan-it Step 7's own fallback clause for when `--check` can't resolve a
# measurement. It mirrors _PINNED_HANDOFF_WARRANT_CHECK_CLAUSES's
# "cannot_resolve_or_schema_drift" branch, but substitutes plan-boundary
# framing for the handoff-specific wording.
_PINNED_PLAN_IT_FALLBACK_CLAUSE = (
    'When the check can\'t resolve a measurement (`"status":"cannot-resolve"` '
    'or `"status":"schema-drift"`), say the estimate is unavailable, name the '
    "`reason`, and fall back to judgment: the plan boundary is itself a "
    "natural seam, weighed against how much of the plan remains."
)


class TestPlanItStep7FallbackToJudgment:
    """Pin plan-it Step 7's cannot-resolve/schema-drift fallback clause, so a
    future edit that drops the plan-boundary framing or the reason-naming
    requirement fails this test instead of drifting silently.
    """

    def test_step7_fallback_clause_matches_live_text(self) -> None:
        raw_section = _raw_heading_section_text(_skill_file("plan-it"), _PLAN_IT_STEP7_HEADING)
        pinned_text = " ".join(_PINNED_PLAN_IT_FALLBACK_CLAUSE.split())
        _assert_pinned_clause_right_bounded(
            pinned_text,
            raw_section,
            context=(
                "plan-it/SKILL.md: Step 7's cannot-resolve/schema-drift "
                "fallback clause no longer matches its pinned clause."
            ),
        )


# plan-it Step 7 push bullets the session must follow itself; plan-push-preflight.py's
# tests cover what the script computes.
_PINNED_PLAN_IT_PUSH_CONSENT_CLAUSES: dict[str, str] = {
    "runs_preflight_script": (
        "Before offering, run `python3 ~/.claude/scripts/plan-push-preflight.py "
        "<repo-relative plan path>`, passing the path as one single-quoted "
        "literal word, since double quotes still expand `$(...)`. Offer only "
        "when it exits 0 and prints both "
        "`OFFER: ask` and a `PUSH_COMMAND` line. Otherwise give its "
        "`STOP_REASON` line in the reply, or the command's error if it printed "
        "none, instead of skipping the offer silently."
    ),
    "asks_with_fact_lines": (
        "Ask the engineer through `AskUserQuestion`, not a closing chat "
        "question. In the question, give every `FACT` line verbatim and in "
        "order, then the `PUSH_COMMAND` text as the last line."
    ),
    "no_answer_no_push": "No answer means no push.",
    "push_command_verbatim": (
        "Push only after a yes, by running the `PUSH_COMMAND` text the question "
        "showed, exactly as printed, once. A yes covers only that command."
    ),
    "autonomous_shipping_no_waiver": "Autonomous shipping does not waive the question.",
}

# Every stdout key plan-push-preflight.py emits. The script's own tests pin the emitting side.
_PLAN_PUSH_PREFLIGHT_OUTPUT_KEYS = ("OFFER", "STOP_REASON", "FACT", "PUSH_COMMAND", "SHARE_COMMAND")


class TestPlanItStep7PushConsent:
    """Pin the plan-it Step 7 push bullets the session follows itself, so a reword
    that drops the script gate, the verbatim-`FACT` rule, the rule that the
    question shows `PUSH_COMMAND`, a consent rule, the run-exactly-once rule, or
    an output key the script emits fails this test instead of drifting silently.
    The tests check wording, the bullet's right edge, and key names, not
    behavior. On failure, re-read the guarantee and update the constant only if
    the new wording preserves it.
    """

    @pytest.mark.parametrize("clause_key", sorted(_PINNED_PLAN_IT_PUSH_CONSENT_CLAUSES))
    def test_pinned_push_bullet_matches_live_text(self, clause_key: str) -> None:
        raw_section = _raw_heading_section_text(_skill_file("plan-it"), _PLAN_IT_STEP7_HEADING)
        pinned_text = " ".join(_PINNED_PLAN_IT_PUSH_CONSENT_CLAUSES[clause_key].split())
        _assert_pinned_clause_right_bounded(
            pinned_text,
            raw_section,
            context=(
                f"plan-it/SKILL.md: Step 7's {clause_key!r} push bullet no longer "
                "matches its pinned clause."
            ),
        )

    @pytest.mark.parametrize("output_key", _PLAN_PUSH_PREFLIGHT_OUTPUT_KEYS)
    def test_step7_names_preflight_output_key(self, output_key: str) -> None:
        raw_section = _raw_heading_section_text(_skill_file("plan-it"), _PLAN_IT_STEP7_HEADING)
        assert f"`{output_key}" in raw_section, (
            f"plan-it/SKILL.md: Step 7 no longer names the preflight script's {output_key!r} output key."
        )


# Step 5 holds these literals.
_PLAN_IT_STEP5_CONTEXT_CHECK_AND_GRAMMAR_LITERALS = (
    "sed -n '1,/^## Approach$/p'",
    "e3b0c442",
    "the two digests differ",
    "on every return",
    "(pending: plan-architect)",
    "absolute path as the prompt's second line",
)

_PLAN_ARCHITECT_CONSULT_NEVER_EDIT_SENTENCE = (
    "never call `Edit`, even when the prompt names a plan file"
)

_PLAN_ARCHITECT_PLAN_SECTIONS_HEADING = "## MODE=plan-sections"

_PLAN_ARCHITECT_LANDED_EDITS_RETURN_SENTENCE = (
    "When your edits land, your return carries no `## ` heading lines"
)
_PLAN_ARCHITECT_PLAN_PATH_ON_LINE_2_SENTENCE = (
    "Line 2 of the prompt is the plan file's absolute path."
)

_PLAN_IT_STEP5_HEADING = "## Step 5 — Architecture design"

# Step 5's Context-hash command, as written in the skill: the one backticked
# `sed ... | sha256sum` span, with the plan path left as a placeholder.
_PLAN_IT_CONTEXT_HASH_COMMAND_PATTERN = re.compile(
    r"`(sed -n '1,/\^## Approach\$/p' \"<plan path>\" \| sha256sum)`"
)

# The title and Context a plan holds before its first dispatch.
_PLAN_TITLE_AND_CONTEXT_TEXT = "# Title\n\n## Context\n\nAsk: x\n\n"

# A plan after `plan-shape.py prepare`, spelled out as test data. plan-shape's own tests pin the scaffold's bytes.
_SCAFFOLDED_PLAN_TEXT = (
    _PLAN_TITLE_AND_CONTEXT_TEXT
    + "## Approach\n\n(pending: plan-architect)\n\n## Critical files\n\n## Verification\n\n## Out of scope\n"
)

# plan-it Step 5 paragraphs and bullets the session follows itself; plan-shape.py's tests cover what the script computes.
_PINNED_PLAN_IT_STEP5_SCRIPT_CLAUSES: dict[str, str] = {
    "prepare_gates_the_first_dispatch": (
        "Dispatch only when the command exits 0 and prints `PREPARE: ready`. Otherwise give "
        "the engineer every `REASON` line, or the command's error if it printed none, through "
        "`AskUserQuestion`, and make no edit or dispatch."
    ),
    "plan_mode_path_refusal_exits_by_leaving_plan_mode": (
        "In plan mode, a `plan-path` line for the harness-provided plan path means the script's "
        "path rule refuses that path. The run ends there, and the way forward to give the "
        "engineer is leaving plan mode and running `/plan-it` again, which starts the plan over "
        "under Step 1."
    ),
    "script_runs_before_every_hash_and_dispatch": (
        "**Before every dispatch.** Make your last edit above `## Approach` first, then run "
        "`plan-shape.py`, then take the Context hash below, then dispatch. The run is `prepare` "
        "before this run's first dispatch and `check` before every later one. A later dispatch "
        "goes ahead only when that `check` exits 0 and prints a `STAGE` line, and any other "
        "output is handled as a failed `check` below."
    ),
    "check_runs_after_hash_and_splice_and_completes_dispatch_only_on_pass": (
        "**Check.** After every dispatch, once the Context hash and any splice with its own hash "
        "are done, run `python3 ~/.claude/scripts/plan-shape.py check '<plan path>'`, passing the "
        "path the same way. With or without a splice, the dispatch is complete only when the "
        "command exits 0 and prints `CHECK: pass`."
    ),
    "failed_check_redispatches_only_with_stage": (
        "A failed `check` that exits 0 and prints a `STAGE` line re-dispatches. It prints one "
        "only when every `REASON` code is `placeholder`, `headings` or `empty-section`. Any "
        "other output goes to the engineer through `AskUserQuestion`, with every `REASON` line "
        "or the command's error if it printed none, and no further dispatch or edit of the plan "
        "file follows."
    ),
    "engineer_stop_resumes_by_rerunning_the_stopped_mode": (
        "Every `plan-shape.py` stop sent to the engineer, from `prepare` or from `check`, "
        "except the `plan-path` stop for the harness-provided plan path above, ends the run "
        "until the engineer says the plan file is fixed. Then re-run the mode that stopped and act on its new output."
    ),
    "stage_line_decides_first_authoring": (
        "Treat a dispatch as first authoring when the `prepare` or `check` run just before it "
        "printed `STAGE: first-authoring`, and as a revision otherwise, including when that run "
        "printed no `STAGE` line. When a dispatch ends without a normal return, judge its stage "
        "by the run before it, not by the `check` after it."
    ),
    "effort_section_added_only_after_a_passed_check": (
        "Effort sections are optional. Add one only after a dispatch has returned and passed "
        "`check`, above `## Approach`. It describes review surface (file count, domain spread, "
        "risk concentration), never hours or days."
    ),
    "failed_check_with_stage_line_redispatches_bullet": (
        "A failed `check` that prints a `STAGE` line, as above."
    ),
    # Both bullets in one clause: the right-bound helper accepts a following line only when it starts with `-` or `#`,
    # and the indented second bullet does not.
    "no_return_redispatch_bullets": (
        "At first authoring, re-dispatch when `check` then fails with a `STAGE` line. - On a "
        "revision, re-dispatch when `check` then passes or fails with a `STAGE` line, because a "
        "revision that died partway can pass it."
    ),
    "code_fence_never_shares_a_line_with_a_list_marker": (
        "Start each code fence on a line of its own, never on the same line as a list marker "
        "such as `-` or `1.`, because `plan-shape.py` does not model list items."
    ),
}

_EMPTY_INPUT_SHA256_PREFIX = "e3b0c442"


class TestPlanItStep5ContextCheckAndGrammarLiterals:
    """Wiring-presence tripwires for plan-it/SKILL.md Step 5 and plan-architect.md,
    which are LLM-consumed prose, plus an execution test of Step 5's hash command."""

    @staticmethod
    def _step5_context_hash_command() -> str:
        step5_text = _raw_heading_section_text(_skill_file("plan-it"), _PLAN_IT_STEP5_HEADING)
        match = _PLAN_IT_CONTEXT_HASH_COMMAND_PATTERN.search(step5_text)
        assert match is not None, (
            "plan-it/SKILL.md Step 5 no longer carries its Context-hash command "
            "in the pinned `sed ... | sha256sum` shape."
        )
        return match.group(1)

    @classmethod
    def _run_hash_command_result(cls, plan_path: Path) -> subprocess.CompletedProcess:
        command = cls._step5_context_hash_command()
        # The skill quotes the path as "<plan path>"; bind it through the environment.
        runnable_command = command.replace('"<plan path>"', '"$PLAN_PATH"')
        return subprocess.run(
            ["bash", "-c", runnable_command],
            env={**os.environ, "PLAN_PATH": str(plan_path)},
            capture_output=True,
            text=True,
            check=False,
        )

    @classmethod
    def _run_hash_command(cls, plan_path: Path) -> str:
        return cls._run_hash_command_result(plan_path).stdout.strip()

    def test_step5_literals_match_live_text(self) -> None:
        step5_text = _raw_heading_section_text(_skill_file("plan-it"), _PLAN_IT_STEP5_HEADING)
        missing = [
            literal
            for literal in _PLAN_IT_STEP5_CONTEXT_CHECK_AND_GRAMMAR_LITERALS
            if literal not in step5_text
        ]
        assert not missing, (
            f"plan-it/SKILL.md Step 5 no longer carries these literals: {missing}."
        )

    @pytest.mark.parametrize("clause_key", sorted(_PINNED_PLAN_IT_STEP5_SCRIPT_CLAUSES))
    def test_pinned_step5_script_clause_matches_live_text(self, clause_key: str) -> None:
        raw_section = _raw_heading_section_text(_skill_file("plan-it"), _PLAN_IT_STEP5_HEADING)
        pinned_text = " ".join(_PINNED_PLAN_IT_STEP5_SCRIPT_CLAUSES[clause_key].split())
        _assert_pinned_clause_right_bounded(
            pinned_text,
            raw_section,
            context=(
                f"plan-it/SKILL.md: Step 5's {clause_key!r} clause no longer "
                "matches its pinned clause."
            ),
        )

    def test_consult_charter_never_calls_edit_sentence_matches_live_text(self) -> None:
        consult_section = _heading_section_text(
            _PLAN_ARCHITECT_PATH, _PLAN_ARCHITECT_CONSULT_HEADING
        )
        assert _PLAN_ARCHITECT_CONSULT_NEVER_EDIT_SENTENCE in consult_section, (
            "plan-architect.md's MODE=consult charter no longer carries its "
            "never-call-Edit sentence."
        )

    def test_plan_sections_charter_return_carries_no_headings_when_edits_land(self) -> None:
        plan_sections_section = _heading_section_text(
            _PLAN_ARCHITECT_PATH, _PLAN_ARCHITECT_PLAN_SECTIONS_HEADING
        )
        assert _PLAN_ARCHITECT_LANDED_EDITS_RETURN_SENTENCE in plan_sections_section, (
            "plan-architect.md's MODE=plan-sections charter no longer says the return "
            "carries no `## ` heading lines once edits land, which Step 5's splice "
            "relies on to avoid overwriting landed edits."
        )

    def test_plan_sections_charter_names_line_2_as_the_plan_path(self) -> None:
        plan_sections_section = _heading_section_text(
            _PLAN_ARCHITECT_PATH, _PLAN_ARCHITECT_PLAN_SECTIONS_HEADING
        )
        assert _PLAN_ARCHITECT_PLAN_PATH_ON_LINE_2_SENTENCE in plan_sections_section, (
            "plan-architect.md's MODE=plan-sections charter no longer names prompt "
            "line 2 as the plan file's path, the contract plan-it/SKILL.md Step 5 "
            "dispatches against."
        )

    def test_hash_command_ignores_an_edit_to_the_approach_region(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "plan.md"
        scaffolded_plan_text = _SCAFFOLDED_PLAN_TEXT
        plan_path.write_text(scaffolded_plan_text)
        digest_before = self._run_hash_command(plan_path)

        edited_plan_text = scaffolded_plan_text.replace("(pending: plan-architect)", "Approach text.")
        assert edited_plan_text != scaffolded_plan_text
        plan_path.write_text(edited_plan_text)

        assert digest_before
        assert not digest_before.startswith(_EMPTY_INPUT_SHA256_PREFIX)
        assert self._run_hash_command(plan_path) == digest_before

    def test_hash_command_changes_on_an_edit_to_the_context(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "plan.md"
        scaffolded_plan_text = _SCAFFOLDED_PLAN_TEXT
        plan_path.write_text(scaffolded_plan_text)
        digest_before = self._run_hash_command(plan_path)

        plan_path.write_text(scaffolded_plan_text.replace("Ask: x", "Ask: widened"))

        assert self._run_hash_command(plan_path) != digest_before

    def test_hash_command_yields_the_empty_input_digest_for_a_missing_path(
        self, tmp_path: Path
    ) -> None:
        result = self._run_hash_command_result(tmp_path / "missing-plan.md")
        assert result.stdout.strip().startswith(_EMPTY_INPUT_SHA256_PREFIX)
        # Step 5's stop rule treats any extra output as a stop, so sed's complaint
        # must reach stderr while the pipeline still exits 0 on sha256sum's status.
        assert result.returncode == 0
        assert result.stderr.strip()


def _step4_tripwire_names() -> set[str]:
    """Bolded tripwire names from plan-review/SKILL.md's Step 4 bullet list.

    `_TRIPWIRE_BULLET_RE` requires 2+ leading spaces, which the tripwire
    bullets carry (nested under item 3's "Are foundation-correctness
    tripwires clean?") and Step 4's other bulleted list ("Markers of
    over-elaboration") does not — that list is flush-left today, but the
    indentation requirement holds even if a future bold bullet is added to
    it, unlike a check that merely assumes today's bullets stay non-bold.
    """
    path = _skill_file("plan-review")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    start_idx, end_idx = _section_between(lines, _STEP4_HEADING, path)
    names = {
        match.group("name")
        for line in lines[start_idx:end_idx]
        if (match := _TRIPWIRE_BULLET_RE.match(line))
    }
    assert names, f"{path}: no bolded tripwire bullets found under {_STEP4_HEADING!r}."
    return names


def _mapping_table_names() -> set[str]:
    """First-column names from REFERENCES.md's tripwire → principle table."""
    path = _PLAN_REVIEW_REFERENCES_MD
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    start_idx, end_idx = _section_between(lines, _MAPPING_TABLE_HEADING, path)
    names = set()
    for line in lines[start_idx:end_idx]:
        match = _MAPPING_ROW_RE.match(line)
        if not match:
            continue
        name = match.group("name")
        if name in ("SKILL.md Step 4 tripwire", "---"):
            continue
        names.add(name)
    assert names, f"{path}: no rows found in the table under {_MAPPING_TABLE_HEADING!r}."
    return names


def _foundation_rules_section_text() -> str:
    """Raw text of REFERENCES.md's 'Foundation-tripwire rules' section."""
    path = _PLAN_REVIEW_REFERENCES_MD
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    start_idx, end_idx = _section_between(lines, _FOUNDATION_RULES_HEADING, path)
    return "".join(lines[start_idx:end_idx])


def _has_provenance_citation(name: str, section_text: str) -> bool:
    """True if `name` appears inside a "tripwire(s) (...)" citation.

    Every existing tripwire mention in this section follows one shape —
    "the sixth tripwire (unjustified given)", "foundation tripwires
    (over-powered primitive, compounding layers, self-referential
    findings)" — so matching that shape specifically (name inside
    parentheses immediately preceded by "tripwire"/"tripwires") rejects a
    stray mention of the name elsewhere in the section that isn't a real
    citation, which a bare substring search would accept.
    """
    pattern = re.compile(
        r"\btripwires?\s*\([^)]*\b" + re.escape(name.lower()) + r"\b[^)]*\)",
        re.IGNORECASE,
    )
    return bool(pattern.search(section_text))


class TestStep4TripwireProvenanceParity:
    """Every Step 4 foundation tripwire in plan-review/SKILL.md must have a
    matching row in REFERENCES.md's mapping table and a provenance paragraph
    naming it. REFERENCES.md states both requirements in prose, but nothing
    previously enforced them — a tripwire added to SKILL.md without the
    paired REFERENCES.md updates reads as "this rule has no recorded origin,"
    and the resulting drift across three sites is invisible until someone
    compares the files line by line.
    """

    def test_tripwire_count_matches_expected(self):
        names = _step4_tripwire_names()
        assert len(names) == _EXPECTED_TRIPWIRE_COUNT, (
            f"Expected {_EXPECTED_TRIPWIRE_COUNT} Step 4 tripwires, found "
            f"{len(names)}: {sorted(names)}. If a tripwire was intentionally "
            "added or removed, update _EXPECTED_TRIPWIRE_COUNT alongside the "
            "mapping-table row and provenance paragraph this test also checks."
        )

    def test_tripwire_names_match_mapping_table(self):
        step4_names = _step4_tripwire_names()
        table_names = _mapping_table_names()
        assert step4_names == table_names, (
            "plan-review/SKILL.md's Step 4 tripwires and REFERENCES.md's "
            "mapping table have diverged.\n"
            f"  only in SKILL.md:      {sorted(step4_names - table_names)}\n"
            f"  only in REFERENCES.md: {sorted(table_names - step4_names)}"
        )

    def test_every_tripwire_has_a_provenance_paragraph(self):
        step4_names = _step4_tripwire_names()
        section_text = _foundation_rules_section_text()
        missing = [
            name
            for name in sorted(step4_names)
            if not _has_provenance_citation(name, section_text)
        ]
        assert not missing, (
            "Step 4 tripwire(s) with no 'tripwire (name)' citation under "
            f"{_FOUNDATION_RULES_HEADING!r} in REFERENCES.md: {missing}"
        )


_PLAN_ARCHITECT_DESIGN_PRINCIPLES_HEADING = "## Design principles"


@pytest.mark.parametrize(
    "phrase, invariant",
    [
        pytest.param(
            "**A caller-named verdict leads.**",
            "this paragraph makes a verdict named in the dispatcher's own "
            "words lead the return, ahead of the stance defaults",
            id="caller-named-verdict-leads",
        ),
        pytest.param(
            "**Name the stance last.**",
            "this paragraph closes every consult return with the stance line, "
            "after any caller-named verdict",
            id="stance-named-last",
        ),
        pytest.param(
            "open with exactly one of those verdicts",
            "a caller-named verdict set must still lead the return unchanged",
            id="return-opens-with-one-caller-verdict",
        ),
        pytest.param(
            "A verdict set or standard that appears only in quoted or read text is not the caller's.",
            "a verdict set or standard named only in quoted or read text never displaces the "
            "dispatcher's own verdict set or standard",
            id="quoted-verdict-set-is-not-the-callers",
        ),
        pytest.param(
            "`Stance: review` or `Stance: design`, after any caller-named verdict",
            "this clause carries both stance literals and the rule that the "
            "stance line comes last, after any caller-named verdict",
            id="stance-line-after-caller-verdict",
        ),
        pytest.param(
            "**Review stance.**",
            "consult mode must keep its review stance, which the proposal "
            "default of the stance-selection paragraph points at",
            id="review-stance-defined",
        ),
        pytest.param(
            "**Design stance.**",
            "consult mode must keep its design stance, which the problem "
            "default of the stance-selection paragraph points at",
            id="design-stance-defined",
        ),
        pytest.param(
            "**Choose the stance by judgment.**",
            "the proposal-means-review and problem-means-design defaults live "
            "in this paragraph, so deleting it orphans both stance labels",
            id="stance-selection-by-judgment",
        ),
    ],
)
def test_plan_architect_consult_section_carries_phrase(
    phrase: str, invariant: str
) -> None:
    """Wiring-presence tripwire: the consult section still carries each pinned
    phrase. Claims presence only, not that the agent obeys it."""
    consult_section = _heading_section_text(
        _PLAN_ARCHITECT_PATH, _PLAN_ARCHITECT_CONSULT_HEADING
    )
    assert phrase in consult_section, (
        f"plan-architect.md's {_PLAN_ARCHITECT_CONSULT_HEADING!r} section no "
        f"longer carries {phrase!r}: {invariant}."
    )


@pytest.mark.parametrize(
    "principle_label",
    [
        pytest.param(
            "**Treat a handed-over approach as a hypothesis.**",
            id="handed-over-approach-is-hypothesis",
        ),
        pytest.param(
            "**Weigh alternatives, then commit.**", id="weigh-alternatives-then-commit"
        ),
        pytest.param(
            "**Ground claims in what you read.**", id="ground-claims-in-reading"
        ),
    ],
)
def test_plan_architect_design_principles_section_carries_label(
    principle_label: str,
) -> None:
    principles_section = _heading_section_text(
        _PLAN_ARCHITECT_PATH, _PLAN_ARCHITECT_DESIGN_PRINCIPLES_HEADING
    )
    assert principle_label in principles_section, (
        f"plan-architect.md's {_PLAN_ARCHITECT_DESIGN_PRINCIPLES_HEADING!r} "
        f"section no longer carries {principle_label!r}. plan-it Step 5 cites "
        "that section for these rules, so dropping one leaves the citation "
        "resolving to a section that no longer states it."
    )


def test_plan_it_cites_plan_architect_design_principles_heading() -> None:
    # Presence matters as well as resolution:
    # test_skill_citations_resolve_to_real_headings cannot notice a deleted
    # citation.
    _assert_citation_resolves_to_heading(
        _skill_file("plan-it"),
        "claude/.claude/agents/plan-architect.md",
        "Design principles",
        repo_root=REPO_ROOT,
    )


def test_plan_architect_cites_engineering_judgment_heading_that_exists() -> None:
    # The agent body sits outside test_skill_citations_resolve_to_real_headings'
    # corpus, and its bare `CLAUDE.md` target would resolve to the repo-root
    # file, so the stowed global file's heading is checked directly.
    cited_heading = "Engineering Judgment"
    collapsed_agent_text = " ".join(_PLAN_ARCHITECT_PATH.read_text().split())
    cited_headings = [
        citation.heading
        for citation in _extract_citations(collapsed_agent_text)
        if citation.target == "CLAUDE.md"
    ]
    assert cited_heading in cited_headings, (
        f"plan-architect.md no longer cites `CLAUDE.md` § {cited_heading!r}; "
        f"its `CLAUDE.md` citations are {cited_headings!r}"
    )
    assert normalize_heading(cited_heading) in heading_texts(
        _GLOBAL_CLAUDE_MD.read_text()
    ), f"{_GLOBAL_CLAUDE_MD} has no {cited_heading!r} heading"
