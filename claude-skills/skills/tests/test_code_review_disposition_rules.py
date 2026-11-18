"""Pins on code-review's clean definition and its contradiction-route, defer-invariant, new-primitive, fix-plan-pilot rules."""
from __future__ import annotations

import re

# pyproject.toml's pythonpath also puts claude/.claude/scripts on the import
# path, where the production config-keys.psv parser lives.
from _config import schema as config_schema
from _skill_md_helpers import (
    _CODE_REVIEW_CONTRADICTION_ROUTE_ANCHOR,
    _CODE_REVIEW_DEFER_CRITERIA_HEADING,
    _CODE_REVIEW_DEFER_INVARIANT_ANCHOR,
    _CODE_REVIEW_FINDING_DISPOSITION_HEADING,
    _CODE_REVIEW_HEAVIER_MECHANISM_RULE_OPENING,
    _CODE_REVIEW_INVALID_DEFER_HEADING,
    _CODE_REVIEW_NEW_PRIMITIVE_ROUTE_ANCHOR,
    _assert_pinned_clause_right_bounded,
    _heading_section_text,
    _normalized_anchor_text,
    _raw_heading_section_text,
    _skill_file,
)

_CODE_REVIEW_RECORD_COMPLETION_HEADING = "## Step — Record review completion"

# The clean-definition sentence: a finding logged DEFER or SETTLED, by a
# consult, the human, or a carry, counts as resolved, so none of them blocks
# the review-completion marker written by `marker.sh write code-review`.
_PINNED_CLEAN_DEFINITION_CLAUSE = (
    "A finding logged DEFER or SETTLED (by a consult, the human, or a carry) "
    "counts as resolved. If the review is **clean** (no blockers, no "
    "unresolved critical findings, and you reviewed the currently staged "
    "changes), record it by running this command exactly once:"
)


class TestCodeReviewCleanDefinitionCountsSettledAndDefer:
    """Pin code-review/SKILL.md's clean-definition sentence, so a future edit
    can't silently drop the DEFER or SETTLED branch (a consult, human, or
    carry decision) and make a settled-keep finding block the
    review-completion marker.
    """

    def test_clean_definition_clause_matches_live_text(self) -> None:
        raw_section = _raw_heading_section_text(
            _skill_file("code-review"), _CODE_REVIEW_RECORD_COMPLETION_HEADING
        )
        pinned_text = " ".join(_PINNED_CLEAN_DEFINITION_CLAUSE.split())
        _assert_pinned_clause_right_bounded(
            pinned_text,
            raw_section,
            context="code-review/SKILL.md: clean-definition clause no longer matches.",
        )


# The whole contradiction-route region: the consult route, the site definition,
# the settled-site and two-rewrites human stop, the three verdicts, the
# enforcement-invariant carve-out on *keep current text*, the
# no-explicit-verdict blocking stop, and how a keep is logged and carried. It
# is pinned whole so removing or weakening any sentence fails a test. The whole
# region is compared by exact equality, so any added, removed, or reworded text
# inside the anchors fails. The mid-sentence "- " tokens are SKILL.md's
# verdict bullets after whitespace collapse, not typos.
_PINNED_CONTRADICTION_ROUTE_CLAUSE = (
    "**A finding whose fix would undo a fix an earlier round applied "
    "is also a design question, in every round, staged commit-gate "
    "rounds included.** Write `plan-architect — consult` for it on the "
    "`Fix route:` line. Dispatch, verbatim relay, and the disagreement "
    "stop-and-ask follow the heavier-mechanism rule directly above, "
    "and the consult prompt also carries the earlier finding and its "
    "fix (the consult never invokes `review-ledger.sh` or `marker.sh`). A "
    "site is the file plus the contiguous block — paragraph, list "
    "item, table row, or function — that an earlier round's fix "
    "edited, that a live DEFER or SETTLED decision's `--source` names, "
    "or, when the earlier round's outcome was *keep current text* with "
    "nothing edited, the block the settled finding's own cited "
    "location named. A finding's location is matched against that site "
    "via the ledger's `--source` field or the "
    "fix commit's own diff hunk, read generously enough to include an "
    "adjacent or wrapped continuation of the same clause and any "
    "duplicate expression of the same defect elsewhere in the block — "
    "a finding is not a different site merely because its cited "
    "location sits just outside the literal edited or cited range. A "
    "finding against a site an earlier verdict already settled, or "
    "that two earlier rounds' fixes already rewrote, goes straight to "
    "the human as a blocking stop-and-ask, with no consult, unless it "
    "carries as below. Count those rewrites from the ledger, never from "
    "recall: write the branch's rows with "
    "`~/.claude/scripts/review-ledger.sh show --out "
    "agent-reviews/review-history-<suffix>.md`, Grep that file for the "
    "finding's path, and count the earlier rounds holding an ADDRESS row "
    "on that path whose `finding` and `rationale` concern this block. The "
    "path is the key, because a row's range gives its own round's lines "
    "and later fixes shift them, so the range is a hint, never a test. A "
    "row the recorded text cannot place in or out of this block makes the "
    "match uncertain, so that error is over-firing, which stops "
    "for the engineer. The count sees only where findings pointed, so a "
    "fix that landed at another path is not counted, an accepted miss. "
    "`unchanged` writes no file. "
    "It counts zero only when no earlier round of this session logged a "
    "row, and is uncertain otherwise. A non-zero exit makes the match "
    "uncertain. That stop names its trigger — a script "
    "rejection (relay its reason), a different failure mode, an "
    "uncertain match, or an invariant-class finding — and quotes the "
    "earlier decision's words, date and round. The consult's judgment "
    "standard is that the current text wins unless the finding names a "
    "defect, under a stated rule, that the current text actually has. "
    "One consult carries every such finding in the round and returns "
    "exactly one of the three verdicts per finding: - *Keep current "
    "text* resolves it with nothing dispatched, logged as "
    "`--disposition SETTLED --decided-by plan-architect` with the "
    "verdict in `--rationale` and a range-form `--source` naming the "
    "whole block, and is never available to a finding the "
    "enforcement-invariant rule below covers. - *Apply this round's fix* "
    "is an ordinary ADDRESS row on the `code-writer` route. - *Cannot "
    "choose* is a blocking stop-and-ask to the human. A finding with "
    "no explicit per-finding verdict from the consult (failed "
    "dispatch, empty, hedged, or partial coverage) is likewise a "
    "blocking stop-and-ask, never *keep current text*. A human keep is "
    "logged `--disposition SETTLED --decided-by engineer "
    "--engineer-quote '<their words>'` with a range naming the whole "
    "block (widen when unsure). It also carries `--ref <id>` to the "
    "live earlier decision on the same failure mode, if any (take ids "
    "from the digest's Id column; `append` prints none). It "
    "adds `--carry-forward` only when the engineer's answer states no "
    "scope or time limit, neither declines carries nor asks to be asked "
    "again, and the enforcement-invariant rule below does not apply. "
    "A keep \"for now\" or \"just this file\", a decline, or an ask to "
    "be asked again is logged without `--carry-forward`. "
    "Relay the `stored engineer quote:` line. Every "
    "stop-and-ask in this section whose keep is logged this way first "
    "tells the engineer that the answer is quoted in the public "
    "PR body, where tracker IDs, UUIDs, long hex runs, home paths and "
    "blocklisted names block the update, and, except at the "
    "enforcement-invariant stop, that the keep also applies without asking to "
    "same-failure-mode repeats on this unchanged block unless they "
    "decline or limit it. A same-failure-mode repeat at a live DEFER decision, or "
    "at a live engineer SETTLED logged `--carry-forward`, carries "
    "without a stop. Apply the enforcement-invariant rule to the new "
    "finding first, since an invariant-class finding never carries. Then "
    "log it with the decision's disposition, `--decided-by carry --ref "
    "<id>`, `--rationale` naming the shared defect, the reviewer's "
    "`--cited-line`, and a `--source` range reproducing the "
    "decided text at its current lines (rules: `--help`). A DEFER "
    "carry also restates `--defer-criterion` after re-running the "
    "closed list. A "
    "repeat that does not carry takes the stop at a SETTLED site and a "
    "fresh disposition against the closed list at a DEFER site. An "
    "ADDRESS or fresh DEFER of a repeat carries `--ref <id>` so the old "
    "row retires; another finding at the site leaves it live. The "
    "round report relays each `carry of decision` line, and a reopen "
    "is logged `ADDRESS --ref <id> --relog` with the decision's `--source`, "
    "which retires the decision and its carries from the PR block."
)


class TestCodeReviewContradictionRouteRegionPin:
    """Pin code-review/SKILL.md's contradiction-route region whole, so dropping
    any of these rules fails a test instead of drifting silently:

    - the enforcement-invariant carve-out on *keep current text*;
    - the settled-site / two-rewrites human stop;
    - the no-explicit-verdict blocking stop-and-ask;
    - the consent notice before an engineer keep is logged;
    - `--carry-forward` only for an answer with no scope or time limit;
    - the carry rules: the invariant check before a carry, and a `--source`
      range reproducing the decided text at its current lines (the rest of the
      carry rules live in `--help`, pinned by test_review_ledger_script.py);
    - the `--ref` that retires a decision, and the `ADDRESS --ref` reopen;
    - the consult prompt's bar on invoking `review-ledger.sh` or `marker.sh`.
    """

    def test_contradiction_route_region_matches_live_text(self) -> None:
        skill_md_path = _skill_file("code-review")
        live_text = _normalized_anchor_text(
            skill_md_path, _CODE_REVIEW_CONTRADICTION_ROUTE_ANCHOR
        )
        pinned_text = " ".join(_PINNED_CONTRADICTION_ROUTE_CLAUSE.split())
        assert live_text == pinned_text, (
            f"{skill_md_path}: {_CODE_REVIEW_CONTRADICTION_ROUTE_ANCHOR} no longer "
            f"matches its pinned text.\n"
            f"  live:   {live_text!r}\n"
            f"  pinned: {pinned_text!r}"
        )


# The whole defer-invariant region: the enforcement-invariant class definition,
# its never-DEFER-eligible ruling, the ADDRESS-or-blocking-stop disposition,
# the scope of the bar (an invariant break a round notices anywhere in the PR),
# and its precedence over checklist item 14's informational-only route.
# The contradiction-route region's *keep current text* carve-out takes its scope
# from this class, so it is pinned whole by exact equality, like that region.
_PINNED_DEFER_INVARIANT_CLAUSE = (
    "- **\"Enforcement invariant weakened, but disclosed\"** — a "
    "finding that the diff opens a path around an enforcement "
    "invariant (a gate, hook, permission check, required-approval, or "
    "marker guarantee some mechanism currently makes unbypassable) is "
    "never DEFER-eligible, regardless of which criterion above seems "
    "to match. Disposition is ADDRESS (fix the hole) or a blocking "
    "stop-and-ask to the human, whose keep is logged with "
    "`--enforcement-invariant` and without `--carry-forward`: it is "
    "asked again on every re-raise, never carried, and never DEFERred. "
    "Approval of a diff or PR does not function as informed consent "
    "for an invariant-break buried in the body. The bar applies to an "
    "invariant break a round notices anywhere in the PR, not only inside "
    "the diff that round enumerates (the staged diff in a staged round, "
    "the PR against its base in the cumulative pass). Checklist item 14's "
    "informational-only route does not apply to such a break."
)


_ENFORCEMENT_INVARIANT_CONSULT_STOP_SENTENCE = (
    "For a finding the enforcement-invariant rule below covers, Endorsed with "
    "no plan file and Rejected each end in that rule's blocking stop-and-ask, "
    "since neither fixes the hole."
)


class TestCodeReviewDeferInvariantRegionPin:
    """Pin the text that keeps an enforcement-invariant break from being
    deferred or silently accepted, so drifting from it fails a test.

    Whole-region equality against _PINNED_DEFER_INVARIANT_CLAUSE guards:
    - the never-DEFER-eligible ruling;
    - the enforcement-invariant class definition;
    - the ADDRESS-or-blocking-stop-and-ask disposition;
    - the bar covering an invariant break noticed anywhere in the PR, not only
      the diff a round enumerates;
    - the sentence keeping checklist item 14's informational-only route from
      absorbing such a break.

    Two narrower checks guard the rest:
    - The Finding disposition section must contain the walk clause that adds an
      orchestrator-noticed invariant break to the disposition walk. This pins
      the clause's presence in that section, not the walk sentence's structure.
    - The new-primitive-route region must contain
      _ENFORCEMENT_INVARIANT_CONSULT_STOP_SENTENCE, which ends the
      Endorsed-with-no-plan-file and Rejected returns of an enforcement-invariant
      finding's heavier-mechanism consult in the blocking stop-and-ask.

    On failure, update the pinned text only if the new wording preserves what
    every sentence of the current pinned text guarantees.
    """

    def test_defer_invariant_region_matches_live_text(self) -> None:
        skill_md_path = _skill_file("code-review")
        live_text = _normalized_anchor_text(
            skill_md_path, _CODE_REVIEW_DEFER_INVARIANT_ANCHOR
        )
        pinned_text = " ".join(_PINNED_DEFER_INVARIANT_CLAUSE.split())
        assert live_text == pinned_text, (
            f"{skill_md_path}: {_CODE_REVIEW_DEFER_INVARIANT_ANCHOR} no longer "
            f"matches its pinned text.\n"
            f"  live:   {live_text!r}\n"
            f"  pinned: {pinned_text!r}"
        )

    def test_disposition_walk_covers_orchestrator_noticed_invariant_break(self) -> None:
        """The Finding disposition section carries the clause that routes an
        orchestrator-noticed invariant break into the disposition walk."""
        skill_md_path = _skill_file("code-review")
        walk_clause = (
            "plus any break the orchestrator's own pass noticed that the "
            "enforcement-invariant rule below covers"
        )
        section_text = _heading_section_text(
            skill_md_path, _CODE_REVIEW_FINDING_DISPOSITION_HEADING
        )
        assert " ".join(walk_clause.split()) in section_text, (
            f"{skill_md_path}: the {_CODE_REVIEW_FINDING_DISPOSITION_HEADING} "
            f"section must still contain {walk_clause!r}"
        )

    def test_enforcement_invariant_finding_ends_unfixing_consult_returns_in_human_stop(
        self,
    ) -> None:
        """An enforcement-invariant finding's Endorsed-with-no-plan-file and
        Rejected consult returns still end in the human stop."""
        skill_md_path = _skill_file("code-review")
        heavier_mechanism_text = _normalized_anchor_text(
            skill_md_path, _CODE_REVIEW_NEW_PRIMITIVE_ROUTE_ANCHOR
        )
        stop_sentence = " ".join(_ENFORCEMENT_INVARIANT_CONSULT_STOP_SENTENCE.split())
        assert stop_sentence in heavier_mechanism_text, (
            f"{skill_md_path}: the {_CODE_REVIEW_NEW_PRIMITIVE_ROUTE_ANCHOR} region "
            f"must still contain {stop_sentence!r}"
        )


_CODE_REVIEW_FIX_PLAN_PILOT_ANCHOR = "DISPOSITION_RULE:code-review-fix-plan-pilot"


# The whole fix-plan pilot region, pinned by exact equality so any edit to it is a visible
# change to this constant. The mid-sentence "- " tokens are SKILL.md's verdict bullets after
# whitespace collapse, not typos.
_PINNED_FIX_PLAN_PILOT_CLAUSE = (
    "**Once a round has a reviewer-spawned finding, run "
    "`~/.claude/scripts/config-get.sh architect_fix_plan_pilot`; "
    "only exit 0 applies this rule.** The contradiction route's "
    "settled-site stop and carries run first, and the "
    "reviewer-spawned findings left after them are the payload; "
    "with none left, dispatch nothing and write no tag. Number the "
    "payload findings from 1 and mark each one at an earlier "
    "round's site. Dispatch `plan-architect` once, with "
    "`MODE=consult` as the prompt's first line. The prompt carries "
    "the numbered findings; each marked one's earlier finding and "
    "fix; the contradiction route's judgment standard; the DEFER "
    "criteria and invalid DEFER rationales below; this round's "
    "findings-file paths; the diff artifact resolved as for "
    "`comment-discipline-reviewer`; the plan file and ledger digest "
    "paths when each exists; the bar on invoking "
    "`review-ledger.sh` or `marker.sh` stated above; "
    "and the project layer's path when Step 0.5 loaded one. Ask for "
    "one row per payload number, each a single line of `<n>.` "
    "followed by ADDRESS with the fix shape and the files it "
    "touches, DEFER naming a closed-list criterion, or KEEP (keep "
    "current text) for a marked number. Ask for exactly one line "
    "reading `foundation: sound` or `foundation: wrong`. Say that "
    "reasoning and not-verified notes go outside the rows, that no "
    "other line starts like a row, and that a finding it cannot "
    "commit to gets no row. For payload findings a valid table "
    "decides, this consult replaces the contradiction consult. "
    "Relay the return verbatim and validate "
    "it by the list below; never re-litigate a row. Each stop names "
    "every condition the return failed. Nothing re-dispatches "
    "automatically. Only the human may re-dispatch or send the "
    "payload findings through the rest of this section instead. A "
    "re-dispatch's return is read under the same list. Write one tag on the "
    "round's `Fix route:` line: `fix-plan: dispatched` when a valid "
    "table became the disposition, `fix-plan: fell-back` when the "
    "human chose the rest of this section, and `fix-plan: stopped` "
    "for any other end. - **Valid table.** Every line starting "
    "`<n>. ADDRESS`, `<n>. DEFER` or `<n>. KEEP` is a row in the "
    "shape asked for, the rows' numbers are the payload numbers "
    "with none missing or repeated, no row is barred as below, and "
    "exactly one line starts with `foundation:` and reads exactly "
    "`foundation: sound`. Text outside the rows and `foundation:` "
    "lines decides nothing. The table is then the disposition. One "
    "`code-writer` carries its ADDRESS rows, if any, each fix shape "
    "replacing the Suggested fix. A fix "
    "that trips the heavier-mechanism limbs takes that rule instead. "
    "Log each row as the ledger section below "
    "and a loaded project layer prescribe. Log a KEEP as the "
    "contradiction route logs a consult's keep. Log an ADDRESS or "
    "fresh DEFER of a repeat with the `--ref <id>` that route "
    "requires. Every table-decided row's `--rationale` starts with "
    "`fix-plan:`. - **`foundation: wrong`**, with or without rows, "
    "is a blocking stop-and-ask on whether to re-plan; a yes "
    "re-plans as the round-cap consult's first verdict above does. "
    "- **Barred row.** A DEFER or KEEP row on a finding the "
    "enforcement-invariant rule below or a loaded project layer "
    "bars from DEFER is a blocking stop-and-ask. - **Any other "
    "return**, a failed dispatch included, is a blocking "
    "stop-and-ask before any fix is dispatched."
)


class TestCodeReviewFixPlanPilotRegionPin:
    """Pin code-review/SKILL.md's fix-plan pilot region whole, so dropping or
    weakening any of its rules fails a test instead of drifting silently.
    """

    def test_fix_plan_pilot_region_matches_live_text(self) -> None:
        skill_md_path = _skill_file("code-review")
        live_text = _normalized_anchor_text(
            skill_md_path, _CODE_REVIEW_FIX_PLAN_PILOT_ANCHOR
        )
        pinned_text = " ".join(_PINNED_FIX_PLAN_PILOT_CLAUSE.split())
        assert live_text == pinned_text, (
            f"{skill_md_path}: {_CODE_REVIEW_FIX_PLAN_PILOT_ANCHOR} no longer "
            f"matches its pinned text.\n"
            f"  live:   {live_text!r}\n"
            f"  pinned: {pinned_text!r}"
        )

    def test_region_placed_after_contradiction_route_and_before_defer_criteria(
        self,
    ) -> None:
        """The region's "above", "below" and "the rest of this section"
        references depend on its position."""
        skill_md_path = _skill_file("code-review")
        raw_text = skill_md_path.read_text()
        contradiction_end = raw_text.find(
            f"<!-- {_CODE_REVIEW_CONTRADICTION_ROUTE_ANCHOR} end -->"
        )
        pilot_start = raw_text.find(
            f"<!-- {_CODE_REVIEW_FIX_PLAN_PILOT_ANCHOR} start -->"
        )
        defer_criteria = raw_text.find(_CODE_REVIEW_DEFER_CRITERIA_HEADING)
        assert -1 not in (contradiction_end, pilot_start, defer_criteria), (
            f"{skill_md_path}: placement needs the contradiction-route end anchor, "
            f"the fix-plan pilot start anchor and {_CODE_REVIEW_DEFER_CRITERIA_HEADING}; "
            f"offsets found: {contradiction_end}, {pilot_start}, {defer_criteria}"
        )
        assert contradiction_end < pilot_start < defer_criteria, (
            f"{skill_md_path}: {_CODE_REVIEW_FIX_PLAN_PILOT_ANCHOR} start anchor must "
            "fall after the contradiction-route end anchor and before "
            f"{_CODE_REVIEW_DEFER_CRITERIA_HEADING}"
        )

    def test_cited_referents_still_exist(self) -> None:
        """The region cites "the round-cap consult's first verdict", "that
        rule" (the heavier-mechanism rule, consult through Rejected outcome and
        disagreement stop) and "invalid DEFER rationales below" by position and
        name, and none of those is otherwise pinned."""
        skill_md_path = _skill_file("code-review")
        round_cap_text = _normalized_anchor_text(
            skill_md_path, "DISPOSITION_RULE:code-review-round-cap-consult-verdict"
        )
        first_bullet_start = round_cap_text.find("- **")
        assert first_bullet_start != -1 and round_cap_text[
            first_bullet_start + len("- **") :
        ].startswith("Foundation is wrong"), (
            f"{skill_md_path}: the round-cap consult region's first verdict bullet "
            "must be the re-plan verdict (`Foundation is wrong`)"
        )
        heavier_mechanism_text = _normalized_anchor_text(
            skill_md_path, _CODE_REVIEW_NEW_PRIMITIVE_ROUTE_ANCHOR
        )
        assert heavier_mechanism_text.startswith(
            _CODE_REVIEW_HEAVIER_MECHANISM_RULE_OPENING
        ), (
            f"{skill_md_path}: the heavier-mechanism region must open with "
            f"{_CODE_REVIEW_HEAVIER_MECHANISM_RULE_OPENING!r}"
        )
        # A limb-tripping ADDRESS row takes the whole rule, so its Rejected outcome and its
        # orchestrator-disagreement stop must survive too.
        for heavier_mechanism_clause in (
            "**Rejected",
            "A disagreement between the orchestrator and the returned recommendation "
            "is a blocking stop-and-ask to the human",
        ):
            assert heavier_mechanism_clause in heavier_mechanism_text, (
                f"{skill_md_path}: the heavier-mechanism region must still contain "
                f"{heavier_mechanism_clause!r}"
            )
        raw_text = skill_md_path.read_text()
        pilot_end = raw_text.find(
            f"<!-- {_CODE_REVIEW_FIX_PLAN_PILOT_ANCHOR} end -->"
        )
        invalid_defer_heading = raw_text.find(_CODE_REVIEW_INVALID_DEFER_HEADING)
        assert -1 not in (pilot_end, invalid_defer_heading), (
            f"{skill_md_path}: the region cites {_CODE_REVIEW_INVALID_DEFER_HEADING} "
            "by name and needs it below the region; offsets found: "
            f"{pilot_end}, {invalid_defer_heading}"
        )
        assert pilot_end < invalid_defer_heading, (
            f"{skill_md_path}: {_CODE_REVIEW_INVALID_DEFER_HEADING} must fall "
            "after the fix-plan pilot region"
        )

    def test_region_names_a_key_in_the_config_schema(self) -> None:
        """Guards the region-to-schema wiring, not behavior."""
        region_text = _normalized_anchor_text(
            _skill_file("code-review"), _CODE_REVIEW_FIX_PLAN_PILOT_ANCHOR
        )
        key_match = re.search(r"config-get\.sh ([a-z0-9_]+)", region_text)
        assert key_match, "fix-plan pilot region no longer names a config-get.sh key"
        assert key_match.group(1) in config_schema(), (
            f"{key_match.group(1)!r} is not a key in config-keys.psv"
        )
