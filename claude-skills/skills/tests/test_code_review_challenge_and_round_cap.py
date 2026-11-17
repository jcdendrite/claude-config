"""Pins on code-review's disposition-challenge and round-cap regions, ripple carry-forward and Step 0.1 ledger show."""
from __future__ import annotations

import re

import pytest
from _skill_md_helpers import (
    _CODE_REVIEW_CONTRADICTION_ROUTE_ANCHOR,
    _CODE_REVIEW_DEFER_CRITERIA_HEADING,
    _CODE_REVIEW_DEFER_INVARIANT_ANCHOR,
    _CODE_REVIEW_HEAVIER_MECHANISM_RULE_OPENING,
    _CODE_REVIEW_INVALID_DEFER_HEADING,
    _CODE_REVIEW_NEW_PRIMITIVE_ROUTE_ANCHOR,
    _CODE_REVIEW_RIPPLE_HEADING,
    _CODE_REVIEW_STEP_0_1_HEADING,
    _PLAN_ARCHITECT_CONSULT_HEADING,
    _PLAN_ARCHITECT_PATH,
    _assert_pinned_clause_right_bounded,
    _heading_section_text,
    _normalized_anchor_text,
    _raw_heading_section_text,
    _skill_file,
)

# pyproject.toml's pythonpath also puts claude/.claude/tests on the import
# path, where these shared test helpers live.
from helpers import CONSULT_CLASSIFICATION_TABLE, REPO_ROOT

_CODE_REVIEW_DISPOSITION_CHALLENGE_ANCHOR = "DISPOSITION_RULE:code-review-disposition-challenge"
_CODE_REVIEW_REVIEW_NARRATIVE_LEDGER_HEADING = "## Review-narrative ledger"
_CODE_REVIEW_THREE_PLUS_DEFER_LEAD = "If you find yourself tagging 3+ findings DEFER"
_CODE_REVIEW_PROJECT_LAYER_HEADING = "## Step 0.5 — Load project-specific layer"
_CODE_REVIEW_DIFF_ARTIFACT_LEAD = "**Resolving `comment-discipline-reviewer`'s diff artifact.**"
_DISPOSITION_CHALLENGE_RECORD_PATH = (
    REPO_ROOT / "docs" / "design-decisions" / "architect-disposition-challenge-pilot.md"
)
_PLAN_ARCHITECT_MODE_SELECTION_HEADING = "## Mode selection"

# The whole disposition-challenge region, pinned by exact equality so any edit to it is a
# visible change to this constant. The mid-sentence "- " tokens are SKILL.md's list markers
# for the region's verdict and outcome bullets after whitespace collapse, not typos.
_PINNED_DISPOSITION_CHALLENGE_CLAUSE = (
    "**Once dispositions are tagged, outside a `/review-pr` pass, if any ADDRESS "
    "or DEFER is the orchestrator's own decision, run "
    "`~/.claude/scripts/config-get.sh architect_disposition_challenge_pilot`; "
    "only exit 0 applies this rule.** A `/review-pr` pass is one run under that "
    "skill's standing override to report findings and change nothing. This rule "
    "runs after the contradiction route's carries and settled-site stops and any "
    "valid fix-plan table, and the round's heavier-mechanism consults and any "
    "`code-writer` dispatch wait for its result. A row is the orchestrator's own "
    "unless a valid fix-plan table or the human decided it, it is a carry, or it "
    "is on the contradiction route. An ADDRESS row the heavier-mechanism rule "
    "routes to a consult is the orchestrator's own. Mark as barred each of those "
    "rows whose finding the enforcement-invariant rule above or a loaded project "
    "layer bars from DEFER, counting a finding as barred when unsure. A barred "
    "row is not challenged: a barred ADDRESS stays ADDRESS, and a barred DEFER "
    "becomes ADDRESS or the stop-and-ask the rule that bars it prescribes. Number "
    "the unbarred rows from 1, and with none dispatch nothing. Dispatch "
    '`plan-architect` once, with an explicit `model: "opus"`, and with a prompt '
    "whose first line is exactly `MODE=challenge` and nothing else. The prompt "
    "carries the numbered rows, each with its finding, disposition and rationale; "
    "this file's path, naming this section as the rules a row must meet; this "
    "round's findings-file paths; the diff artifact resolved as for "
    "`comment-discipline-reviewer`; the plan file's path when one exists; and the "
    "project layer's path when Step 0.5 loaded one. Take a verdict line only from "
    "the opening run: the consecutive verdict-shaped lines from the return's "
    "first line, none when that line is not one. A row with a verdict-shaped line "
    "outside the opening run has no verdict. Then, per row of its return: - "
    "**`<n>. uphold`** keeps the row. - **`<n>. flip`.** Re-evaluate the row once "
    "under the rules above and a loaded project layer's bar from DEFER, with the "
    "architect's argument in front of you, and concede, applying the flip, only "
    "when you then reach its result. Otherwise the row is a blocking stop-and-ask "
    "to the human, relaying the row, your reasoning, and the flip line and its "
    "argument verbatim. - **A row without exactly one such line**, and every row "
    "when the dispatch failed or its return ends in a `Stance:` line, which only "
    "a consult writes, is one blocking stop-and-ask for all such rows, relaying "
    "the return verbatim. Nothing re-dispatches, and a row the human does not "
    "decide keeps its disposition. Before the human answers a stop here, give the "
    "contradiction route's notice once. Log a row the human decides at such a "
    "stop ADDRESS when it is to be fixed, and otherwise as the contradiction "
    "route logs a human keep. After the last concession and stop, re-state the "
    "disposition and the `Fix route:` line of each row whose disposition changed, "
    "applying the heavier-mechanism rule to each one now ADDRESS, and apply the "
    "3+ DEFER re-read above to the new count. Start the `--rationale` of each "
    "barred and each numbered row with `challenge:<outcome>:<scope>`. `<outcome>` "
    "is `barred` on a barred row, and on a numbered row is: - `upheld` on a row "
    "the architect upheld. - `conceded` on a flip you conceded. - "
    "`escalated-architect` when the human, at a flip's stop, chose the flip. - "
    "`escalated-orchestrator` when the human, at a flip's stop, chose the row's "
    "original disposition. - `ran-as-consult` on every numbered row when the "
    "return ends in a `Stance:` line. - `no-verdict` otherwise, on a row without "
    "a verdict. `<scope>` is `narrowed` or `unnarrowed`, as the `Spawn "
    "decisions:` line's scope tag says."
)

# The agent's whole `## MODE=challenge` section, heading line included, whitespace-collapsed.
_PINNED_PLAN_ARCHITECT_CHALLENGE_SECTION = (
    "## MODE=challenge The prompt carries numbered dispositions another session "
    "decided, with the paths of the rules each must meet and of the evidence "
    "behind it. Never call `Edit`, even when the prompt names a plan file. "
    "Judge only those rows. Whether the branch's foundation is wrong is a "
    '`MODE=consult` question, so ignore any "Review-ledger history" line in '
    "your context. Work out each row's disposition yourself before you read its "
    "rationale. Challenge in both directions: a DEFER the rules do not allow, "
    "and an ADDRESS that a closed-list DEFER criterion covers. Flip a row only "
    "for a defect that meets the Review stance's bar for a rejection, under "
    "`MODE=consult`. Open the return with one line per row, in row order, and "
    "start no other line the same way: - `<n>. uphold` - `<n>. flip: <the rule "
    "the row breaks>`. A flip of an ADDRESS also names the closed-list DEFER "
    "criterion the finding meets. Then give each flip's argument, concrete "
    "enough for the dispatching session to re-evaluate the row against, and "
    "name what you did not verify. End with no `Stance:` line, because only a "
    "consult return ends with one."
)

_DISPOSITION_CHALLENGE_PIN_RECOVERY_NOTE = (
    "If the key is on, this edit needs the key set false and the fixture set re-run: see "
    'docs/design-decisions/architect-disposition-challenge-pilot.md, in its "Changes after enabling" bullet.'
)


class TestCodeReviewDispositionChallengeRegionPin:
    """Pin code-review/SKILL.md's disposition-challenge region whole, and the
    referents it reads from other files, so dropping or weakening any of its
    rules fails a test instead of drifting silently. The presence tests claim
    that wiring exists, not that the agent obeys the text.
    """

    @staticmethod
    def _region_text() -> str:
        return _normalized_anchor_text(
            _skill_file("code-review"), _CODE_REVIEW_DISPOSITION_CHALLENGE_ANCHOR
        )

    @classmethod
    def _region_mode_literal(cls) -> str:
        mode_match = re.search(r"first line is exactly `(MODE=[^`]+)`", cls._region_text())
        assert mode_match, "the region no longer states its prompt's first line as exactly `MODE=...`"
        return mode_match.group(1)

    def test_region_matches_pin(self) -> None:
        """Change detector: any edit to the region must be mirrored in the pin,
        as test_pinned_scope_clause_matches_live_text's docstring says of scope clauses."""
        live_text = self._region_text()
        pinned_text = " ".join(_PINNED_DISPOSITION_CHALLENGE_CLAUSE.split())
        assert live_text == pinned_text, (
            f"{_CODE_REVIEW_DISPOSITION_CHALLENGE_ANCHOR} no longer matches its pinned text.\n"
            f"  live:   {live_text!r}\n"
            f"  pinned: {pinned_text!r}\n"
            f"{_DISPOSITION_CHALLENGE_PIN_RECOVERY_NOTE}"
        )

    def test_agent_mode_section_matches_pin(self) -> None:
        """Change detector: any edit to plan-architect.md's challenge section must be
        mirrored in the pin. The phrase tests below catch a dropped rule, not a reworded one."""
        live_text = _heading_section_text(_PLAN_ARCHITECT_PATH, f"## {self._region_mode_literal()}")
        assert live_text == _PINNED_PLAN_ARCHITECT_CHALLENGE_SECTION, (
            f"{_PLAN_ARCHITECT_PATH}'s challenge section no longer matches its pinned text.\n"
            f"  live:   {live_text!r}\n"
            f"  pinned: {_PINNED_PLAN_ARCHITECT_CHALLENGE_SECTION!r}\n"
            f"{_DISPOSITION_CHALLENGE_PIN_RECOVERY_NOTE}"
        )

    def test_region_follows_three_plus_defer_paragraph_and_precedes_ledger_section(
        self,
    ) -> None:
        """The region's "the rules above" and "the 3+ DEFER re-read above"
        depend on this position."""
        skill_md_path = _skill_file("code-review")
        collapsed_text = " ".join(skill_md_path.read_text().split())
        ordered_markers = [
            " ".join(marker.split())
            for marker in (
                f"<!-- {_CODE_REVIEW_DEFER_INVARIANT_ANCHOR} end -->",
                _CODE_REVIEW_THREE_PLUS_DEFER_LEAD,
                f"<!-- {_CODE_REVIEW_DISPOSITION_CHALLENGE_ANCHOR} start -->",
                _CODE_REVIEW_REVIEW_NARRATIVE_LEDGER_HEADING,
            )
        ]
        offsets = [collapsed_text.find(marker) for marker in ordered_markers]
        assert -1 not in offsets, (
            f"{skill_md_path}: placement needs every marker in {ordered_markers}; offsets found: {offsets}"
        )
        assert offsets == sorted(offsets), (
            f"{skill_md_path}: {_CODE_REVIEW_DISPOSITION_CHALLENGE_ANCHOR} must sit after the "
            "defer-invariant region and the 3+ DEFER paragraph, and before the "
            f"{_CODE_REVIEW_REVIEW_NARRATIVE_LEDGER_HEADING} section; offsets found: {offsets}"
        )

    def test_cited_referents_still_exist(self) -> None:
        skill_md_path = _skill_file("code-review")
        skill_text = " ".join(skill_md_path.read_text().split())
        for skill_phrase in (
            "mandatory **Fix route:** line",
            _CODE_REVIEW_DIFF_ARTIFACT_LEAD,
            _CODE_REVIEW_PROJECT_LAYER_HEADING,
            _CODE_REVIEW_DEFER_CRITERIA_HEADING,
            _CODE_REVIEW_INVALID_DEFER_HEADING,
        ):
            assert skill_phrase in skill_text, (
                f"{skill_md_path}: the disposition-challenge region cites {skill_phrase!r}, "
                "which is no longer in the file"
            )
        spawn_decisions_paragraphs = [
            paragraph
            for paragraph in skill_md_path.read_text().split("\n\n")
            if paragraph.startswith("The **Spawn decisions:** line also states")
        ]
        assert spawn_decisions_paragraphs, f"{skill_md_path}: the Spawn decisions scope paragraph is gone"
        for scope_tag in ("(narrowed)", "(unnarrowed"):
            assert scope_tag in spawn_decisions_paragraphs[0], (
                f"{skill_md_path}: the Spawn decisions scope paragraph no longer carries {scope_tag!r}, "
                "which the region's <scope> tag copies"
            )
        contradiction_text = _normalized_anchor_text(skill_md_path, _CODE_REVIEW_CONTRADICTION_ROUTE_ANCHOR)
        for contradiction_phrase in (
            "A human keep is logged",
            "Every stop-and-ask in this section whose keep is logged this way first tells the engineer",
        ):
            assert contradiction_phrase in contradiction_text, (
                f"{skill_md_path}: the contradiction-route region must still contain "
                f"{contradiction_phrase!r}, which the disposition-challenge region reuses by reference"
            )
        heavier_mechanism_text = _normalized_anchor_text(skill_md_path, _CODE_REVIEW_NEW_PRIMITIVE_ROUTE_ANCHOR)
        assert heavier_mechanism_text.startswith(_CODE_REVIEW_HEAVIER_MECHANISM_RULE_OPENING), (
            f"{skill_md_path}: the heavier-mechanism region must open with "
            f"{_CODE_REVIEW_HEAVIER_MECHANISM_RULE_OPENING!r}"
        )
        review_pr_path = _skill_file("review-pr")
        review_pr_text = " ".join(review_pr_path.read_text().split())
        for override_phrase in ("**Standing override.**", "change nothing"):
            assert override_phrase in review_pr_text, (
                f"{review_pr_path}: the standing override the disposition-challenge region names "
                f"must still contain {override_phrase!r}"
            )

    def test_region_verdict_heads_presence_in_agent_mode_section(self) -> None:
        """Claims presence only: each verdict head the region reads opens a
        backticked token in the agent's challenge section."""
        verdict_heads = re.findall(r"`(<n>\. [a-z]+)`", self._region_text())
        assert verdict_heads, "the region no longer names any backticked `<n>. <word>` verdict head"
        mode_section = _heading_section_text(_PLAN_ARCHITECT_PATH, f"## {self._region_mode_literal()}")
        for verdict_head in verdict_heads:
            assert f"`{verdict_head}" in mode_section, (
                f"plan-architect.md's challenge section no longer opens a backticked token with {verdict_head!r}"
            )

    def test_region_mode_literal_is_agent_mode_and_non_consult_row(self) -> None:
        mode_literal = self._region_mode_literal()
        agent_lines = _PLAN_ARCHITECT_PATH.read_text().splitlines()
        assert f"## {mode_literal}" in agent_lines, (
            f"plan-architect.md has no `## {mode_literal}` heading for the region's mode literal"
        )
        matching_rows = [row for row in CONSULT_CLASSIFICATION_TABLE if row[0] == mode_literal]
        assert matching_rows, f"CONSULT_CLASSIFICATION_TABLE has no row whose prompt is exactly {mode_literal!r}"
        for _prompt, expect_consult, row_id in matching_rows:
            assert not expect_consult, f"table row {row_id!r} must classify {mode_literal!r} as no consult"

    def test_mode_selection_names_every_mode_presence(self) -> None:
        """Claims presence only: the agent's mode-selection section names every mode and the exact-first-line rule."""
        mode_selection = _heading_section_text(_PLAN_ARCHITECT_PATH, _PLAN_ARCHITECT_MODE_SELECTION_HEADING)
        for required_text in (
            "`MODE=plan-sections`",
            "`MODE=consult`",
            f"`{self._region_mode_literal()}`",
            "Any other first line, an absent one included, is a consult.",
        ):
            assert required_text in mode_selection, (
                f"plan-architect.md's {_PLAN_ARCHITECT_MODE_SELECTION_HEADING!r} section no longer "
                f"carries {required_text!r}"
            )

    def test_region_outcome_tokens_presence_in_record(self) -> None:
        """Claims presence only, in one direction: the record holds no delimited
        token list to parse, so a region rename the record misses is what fails."""
        region_text = self._region_text()
        outcome_span_start = region_text.find("`<outcome>` is")
        outcome_span_end = region_text.find("`<scope>` is")
        assert -1 not in (outcome_span_start, outcome_span_end), (
            "the region no longer defines `<outcome>` and `<scope>`"
        )
        outcome_span = region_text[outcome_span_start:outcome_span_end]
        outcome_tokens = [
            token for token in re.findall(r"`([^`]+)`", outcome_span) if re.fullmatch(r"[a-z]+(-[a-z]+)*", token)
        ]
        assert outcome_tokens, "no outcome tokens parsed from the region"
        tag_match = re.search(r"`([^`<]+)<outcome>:<scope>`", region_text)
        assert tag_match, "the region no longer states its `challenge:<outcome>:<scope>` tag"
        tag_prefix = tag_match.group(1)
        record_text = _DISPOSITION_CHALLENGE_RECORD_PATH.read_text()
        for record_token in (*outcome_tokens, tag_prefix):
            assert f"`{record_token}`" in record_text, (
                f"{_DISPOSITION_CHALLENGE_RECORD_PATH} no longer carries `{record_token}`, "
                "which the region's tag uses"
            )

    @pytest.mark.parametrize(
        "phrase, invariant",
        [
            pytest.param(
                "Never call `Edit`, even when the prompt names a plan file.",
                "the mode keeps the charter-only no-Edit rule, including the plan-file case",
                id="never-calls-edit",
            ),
            pytest.param(
                "start no other line the same way",
                "no line other than a row's verdict may start like one, so the verdict count is exact",
                id="one-line-per-row",
            ),
            pytest.param(
                "End with no `Stance:` line",
                "only a consult return ends in a Stance line, which the region reads as a consult",
                id="no-stance-line",
            ),
        ],
    )
    def test_agent_mode_section_carries_phrase(self, phrase: str, invariant: str) -> None:
        """Wiring-presence tripwire: claims presence only, not that the agent obeys the phrase."""
        mode_section = _heading_section_text(_PLAN_ARCHITECT_PATH, f"## {self._region_mode_literal()}")
        assert phrase in mode_section, (
            f"plan-architect.md's challenge section no longer carries {phrase!r}: {invariant}."
        )

    def test_region_stance_token_in_consult_stance_line(self) -> None:
        """Claims presence only: the line the region detects as a consult return is
        the line the consult section says it ends with."""
        region_text = self._region_text()
        bullet_start = region_text.find("**A row without exactly one such line**")
        bullet_end = region_text.find("Before the human answers a stop here")
        assert -1 not in (bullet_start, bullet_end), "the region no longer has its no-verdict bullet"
        stance_tokens = re.findall(r"`([^`]*:)`", region_text[bullet_start:bullet_end])
        assert stance_tokens, "the no-verdict bullet no longer detects a backticked `...:` line"
        consult_section = _heading_section_text(_PLAN_ARCHITECT_PATH, _PLAN_ARCHITECT_CONSULT_HEADING)
        for stance_token in stance_tokens:
            for stance_word in ("review", "design"):
                assert f"`{stance_token} {stance_word}`" in consult_section, (
                    f"plan-architect.md's consult section no longer carries `{stance_token} {stance_word}`"
                )


def test_round_cap_consult_prompt_excludes_ledger_output_files_from_the_findings_files() -> None:
    """The previous round's findings files are picked by the `agent-reviews/` name filter. Without
    the exclusion list, a digest or history file would pass as a reviewer's findings file."""
    round_cap_text = _normalized_anchor_text(
        _skill_file("code-review"), "DISPOSITION_RULE:code-review-round-cap-consult-verdict"
    )
    assert (
        "never a `review-ledger-`, `review-history-`, `code-review-dispositions-` or `pr-body-` file"
        in round_cap_text
    )


class TestRoundCapSecondNoiseVerdictStop:
    """Wiring-presence tripwires on the round-cap region's second-noise-verdict stop and its verdict record.
    The region has no whole-text pin, so each load-bearing clause is named here."""

    def _region_text(self) -> str:
        return _normalized_anchor_text(
            _skill_file("code-review"), "DISPOSITION_RULE:code-review-round-cap-consult-verdict"
        )

    @pytest.mark.parametrize(
        "clause",
        [
            pytest.param(
                "When this round has a finding and the ledger holds two or more `noise` `CONSULT` rows in any rounds, "
                "this verdict's own included, the verdict is also a blocking stop-and-ask",
                id="fires-with-a-finding-and-two-noise-rows-in-any-round-its-own-included",
            ),
            pytest.param(
                '`"disposition":"CONSULT","rationale":"noise"`', id="greps-the-shown-disposition-and-rationale-run",
            ),
            pytest.param("Treat an uncertain count as two", id="treats-an-uncertain-count-as-two"),
            pytest.param(
                "Ask once this round's findings are tagged, before any is logged or fixed",
                id="asks-once-before-any-finding-is-logged-or-fixed",
            ),
            pytest.param(
                "before retrying the denied spawn, log it with "
                "`~/.claude/scripts/review-ledger.sh append code-review --disposition CONSULT "
                "--rationale '<verdict>' --round <N>`",
                id="logs-the-verdict-before-retrying-the-denied-spawn",
            ),
            pytest.param(
                "An append that still fails after one retry is a blocking stop", id="a-failed-verdict-append-stops",
            ),
            pytest.param(
                "deliberately for every verdict alike", id="the-failed-append-stop-is-uniform-across-verdicts",
            ),
            pytest.param("A re-plan does not reset the count", id="a-re-plan-does-not-reset-the-noise-count"),
            pytest.param(
                "An `unchanged` after the append is uncertain, not zero", id="unchanged-after-the-append-is-uncertain",
            ),
            pytest.param(
                "naming the trigger (the two rows' rounds, or \"ledger read failed, count unknown\")",
                id="the-stop-names-its-trigger",
            ),
            pytest.param(
                "dedups to no new row, so count a repeated noise verdict as one more yourself",
                id="a-deduped-repeat-noise-verdict-is-counted-by-the-session",
            ),
            pytest.param(
                "At a `/ready-for-review` step-4 denial, relay the verdict to the engineer and log no CONSULT row, "
                "so a step-4 verdict is not counted",
                id="a-step-4-denial-verdict-is-relayed-and-not-counted",
            ),
            pytest.param(
                "where `<verdict>` is `re-plan`, `noise` or `one-defect`", id="names-the-three-verdict-words",
            ),
        ],
    )
    def test_region_carries_clause(self, clause: str) -> None:
        assert clause in self._region_text(), f"the round-cap region no longer carries {clause!r}"

    def test_the_three_options_appear_in_order(self) -> None:
        region_text = self._region_text()
        positions = [
            region_text.find(label)
            for label in ("*Ship with the live DEFER and SETTLED rows.*", "*Re-plan*", "*Continue review*")
        ]
        assert -1 not in positions and positions == sorted(positions), (
            "the second-noise-verdict stop must offer ship, re-plan, then continue, in that order"
        )

    def test_ship_leaves_an_invariant_finding_to_the_invariant_rules_own_stop(self) -> None:
        region_text = self._region_text()
        ship_label = "*Ship with the live DEFER and SETTLED rows.*"
        carve_out_sentence = (
            "A finding the enforcement-invariant rule covers is outside this answer "
            "and takes that rule's own stop, which names it."
        )
        notice_then_log_sentence = (
            "Give the contradiction route's notice, then log each other finding as that route logs a human keep"
        )
        ship_label_position = region_text.find(ship_label)
        assert ship_label_position != -1, f"the round-cap region no longer carries the Ship label {ship_label!r}"
        ship_label_end = ship_label_position + len(ship_label)
        replan_label_start = region_text.find("*Re-plan*", ship_label_end)
        assert replan_label_start != -1, "the round-cap region carries no *Re-plan* label after the Ship label"
        ship_segment = region_text[ship_label_end:replan_label_start]
        carve_out_position = ship_segment.find(carve_out_sentence)
        assert carve_out_position != -1, (
            f"the Ship segment no longer carries the carve-out sentence {carve_out_sentence!r}"
        )
        notice_then_log_position = ship_segment.find(notice_then_log_sentence)
        assert notice_then_log_position != -1, (
            f"the Ship segment no longer carries the notice-then-log sentence {notice_then_log_sentence!r}"
        )
        assert carve_out_position < notice_then_log_position, (
            "the Ship segment must state the carve-out sentence before the notice-then-log sentence"
        )


# The re-review carry-forward paragraph: the disposition records reach each
# spawn prompt, a record never narrows a review or suppresses a finding the
# current text supports, and a missing or partial earlier fix is a new finding
# for any reviewer. It is pinned whole because the right-bound check needs a
# structural boundary at the pinned text's end.
_PINNED_RIPPLE_CARRY_FORWARD_CLAUSE = (
    "On a re-review, or whenever a digest exists, prior decisions are "
    "this session's context, the "
    "ledger digest at `agent-reviews/review-ledger-<suffix>.md` when "
    "it exists, and every disposition record "
    "`ready-for-review/SKILL.md` § \"3. Code review (halt on "
    "findings)\" wrote for this branch; put those paths and these "
    "rules in each spawn prompt (a reviewer sees only its "
    "prompt). A spawn never invokes "
    "`review-ledger.sh` or `marker.sh`. The spawn finishes its own review "
    "before opening the digest or the records — a record or the digest "
    "never narrows what it reviews or suppresses a finding the current "
    "text supports, and it names in its findings any record it cannot "
    "parse, or whose claim the current text contradicts. For each "
    "earlier ADDRESS row the same agent raised whose Outcome names a "
    "fix, it confirms the fix landed as the finding required; a "
    "missing or partial fix is a new finding for any reviewer that "
    "sees it, whichever agent raised the row. It re-flags a site an "
    "earlier fix or verdict rewrote only under a different rule than "
    "the one behind the rewrite, or for a fact the rewrite dropped, "
    "and when its fix would move a site back toward its earlier "
    "wording it names the finding it contradicts."
)


class TestCodeReviewRippleCarryForwardPin:
    """Pin code-review/SKILL.md's re-review carry-forward paragraph, so
    inverting the record-never-suppresses trust rule, dropping the hand-off
    of record paths to every spawn prompt, or dropping the any-reviewer
    partial-fix rule fails a test instead of drifting silently.
    """

    def test_carry_forward_paragraph_matches_live_text(self) -> None:
        raw_section = _raw_heading_section_text(
            _skill_file("code-review"), _CODE_REVIEW_RIPPLE_HEADING
        )
        pinned_text = " ".join(_PINNED_RIPPLE_CARRY_FORWARD_CLAUSE.split())
        _assert_pinned_clause_right_bounded(
            pinned_text,
            raw_section,
            context="code-review/SKILL.md: Ripple effect triage's carry-forward paragraph no longer matches.",
        )


class TestCodeReviewFirstRoundLedgerShow:
    """Pin Step 0.1's first-round `show` call, so a session
    that starts on a branch with earlier rounds continues the branch's round
    sequence instead of restarting at round 1. The call sends rows to
    /dev/null because only the stderr header's max round is needed. Only the
    load-bearing tokens are pinned, so the surrounding wording can change.
    """

    def test_first_round_reads_show_header_and_continues_from_max_round(self) -> None:
        step_text = _heading_section_text(
            _skill_file("code-review"), _CODE_REVIEW_STEP_0_1_HEADING
        )
        for required in (
            "first round",
            "`~/.claude/scripts/review-ledger.sh show > /dev/null`",
            "stderr header",
            "max round",
            "prints no header",
            "instead of restarting at round 1",
        ):
            assert required in step_text, (
                f"code-review/SKILL.md: Step 0.1 no longer contains {required!r}."
            )
