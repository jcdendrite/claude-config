"""The code-writer and skill-fidelity-reviewer agent bodies."""
from __future__ import annotations

from _skill_md_helpers import (
    _agent_body,
    _skill_body,
)


class TestCodeWriterSelfReviewScope:
    """Pin code-writer's self-review diffing instructions.

    Deliberately does not pin the surrounding explanatory prose -- a
    copy-edit there must not fail this test; the three pinned fragments are
    the mechanical instructions whose loss changes what the agent diffs.
    """

    def _body(self) -> str:
        return " ".join(_agent_body("code-writer").split())

    def test_pins_git_diff_paths_for_pre_existing_files(self):
        """A pre-existing modified file must be reviewed with `git diff -- <paths>`."""
        assert "`git diff -- <paths>`" in self._body()

    def test_pins_git_status_porcelain_cross_check(self):
        """The self-tracked path list must be cross-checked against `git status --porcelain`."""
        assert "`git status --porcelain`" in self._body()

    def test_prohibits_git_diff_head(self):
        """`git diff HEAD` must not be used -- it pulls a prior round's staged work into scope."""
        assert "Never use `git diff HEAD`" in self._body()


_TEST_TO_FIT_RULE = "fix the code, not the test"


class TestCodeWriterTestToFitRule:
    """Pin code-writer's Charter bullet on fixing a red check.

    The second literal is verbatim-shared with ready-for-review/SKILL.md's
    "Test-to-fit is forbidden" line, making the accepted duplication across
    the two files mechanically detectable rather than a silent drift risk.
    """

    def _body(self) -> str:
        return _agent_body("code-writer")

    def test_names_test_to_fit(self):
        """The Charter must name the failure mode as test-to-fit."""
        assert "test-to-fit" in self._body().lower()

    def test_states_fix_the_code_not_the_test(self):
        """The Charter must carry the canonical rule statement verbatim."""
        assert _TEST_TO_FIT_RULE in self._body()

    def test_ready_for_review_shares_the_same_literal(self):
        """ready-for-review/SKILL.md must carry the identical literal, pinning the cross-file duplication."""
        assert _TEST_TO_FIT_RULE in _skill_body("ready-for-review")


class TestSkillFidelityReviewerUndecidableDismissal:
    """Pin the decidability-keyed dismissal rule and its visible output slot.

    skill-fidelity-reviewer's only evidence is the diff text and the plan
    path; it has no Bash. A rule keyed on artifact *location* rather than
    *decidability* mis-fires in both directions: it would evaluate
    respond-pr's diff-visible commit (whose correctness depends on review
    comments the reviewer never sees), and it would sweep plan-it's plan
    file into a bare ~/.claude/-prefix dismissal (plan-it writes there in
    plan mode before staging the file onto the branch). The dismissal must
    also be visible: without an Output-format slot, a dismissed skill
    produced no signal at all -- silent non-coverage in place of the false
    positive the rule exists to fix.
    """

    # Shared with test_undecidable_examples_exclude_a_known_decidable_skill,
    # which uses this same literal as its enumeration-clause slice-end anchor
    # -- rewording it fails both tests; the other test's ValueError on a
    # missing anchor is expected collateral, not a separate defect.
    _DISK_HUNT_PROHIBITION = "do not go looking for it on disk"

    def _body(self):
        return _agent_body("skill-fidelity-reviewer")

    def test_declares_decidability_test(self):
        """The dismissal rule must be keyed on decidability, not artifact presence."""
        assert "decidable from your evidence" in self._body()

    def test_prohibits_disk_hunt_for_dismissed_artifacts(self):
        """The reviewer must not search disk for a dismissed skill's artifact."""
        assert self._DISK_HUNT_PROHIBITION in self._body()

    def test_undecidable_examples_exclude_a_known_decidable_skill(self):
        """plan-it's plan file is staged onto the branch and is decidable --
        a location-keyed rule (bare ~/.claude/ prefix) would wrongly sweep it
        into the undecidable-example list alongside handoff/brief. Scope the
        check to the enumeration clause itself (elsewhere in the body,
        `plan-it` legitimately appears as a skill-invocation-label example
        and in the anti-adoption guard) and pin its absence there, so a
        future edit widening the illustrative examples can't reintroduce
        that false dismissal silently."""
        body = self._body()
        start = body.index("Undecidable when")
        end = body.index(self._DISK_HUNT_PROHIBITION)
        assert end > start, (
            "anchor order inverted -- a duplicate/earlier occurrence of one "
            "anchor would otherwise silently degrade this to a vacuous "
            "always-pass empty-slice check"
        )
        enumeration_clause = body[start:end]
        assert "plan-it" not in enumeration_clause

    def test_pointer_line_reports_dismissed_count(self):
        """The pointer line must report dismissals, not just carry the
        step-2 instruction to record them. A single `in body` check on the
        bare phrase is satisfied by the step-2 occurrence alone, so this
        pins the fuller pointer-line fragment specifically."""
        assert "issues, <M> dismissed as undecidable" in self._body()

    def test_file_based_output_relaxes_heading_to_suggestion(self):
        """The file-based output structure must still tell the model where
        to place dismissals, but stop mandating the exact heading string --
        this pins the placement guidance and the relaxation language
        together, so an edit that keeps placement but re-imposes an exact
        string (or vice versa) fails here rather than passing silently."""
        body = self._body()
        assert "after the per-finding H2s" in body
        assert (
            "the exact wording is not required: nothing downstream parses "
            "it mechanically" in body
        )

    def test_dismissed_section_drops_literal_heading_mandate(self):
        """The retracted verbatim-heading mandate must be gone, and replaced
        by explicit acceptance of a self-chosen heading -- pinning only the
        absence would pass on an edit that dropped the mandate without ever
        saying self-composed headings are fine, silently reintroducing the
        original failure mode under different wording."""
        body = self._body()
        assert "does not satisfy this" not in body
        assert (
            "prose explaining the same conclusion under a self-chosen "
            "heading" in body
        )

    def test_pointer_line_claim_is_corrected(self):
        """The false claim that the pointer line is the only surface
        /ready-for-review reads must be retracted and replaced with the
        true one -- /ready-for-review reads the whole findings file."""
        body = self._body()
        assert "the only surface `/ready-for-review` reads" not in body
        assert "reads the whole findings file after every dispatch" in body

    def test_step_two_records_dismissal_without_exact_heading_mandate(self):
        """Step 2's own recording instruction must drop its
        "(exact structure below)" pointer to the now-relaxed Output-format
        mandate and instead state the semantic requirement inline -- this
        covers step 2 independently of the Output-format section edit, so
        a revert of step 2 alone can't silently reintroduce a pointer to a
        section that no longer requires exact structure."""
        body = self._body()
        assert (
            "name the skill and the one-line reason, grouped with any "
            "other dismissals" in body
        )
        assert "(exact structure below)" not in body

    def test_inline_output_lists_dismissals_before_verdict(self):
        """Inline mode must list each dismissal with a reason, not just carry
        it in the opening count -- confirmed by mutation testing that the
        other four assertions stay green even if this sentence is dropped."""
        assert "list each with a one-line reason" in self._body()

    def test_inline_output_reports_dismissed_separately(self):
        """Inline mode's opening count line must separate dismissed skills
        from in-scope ones -- the only feasible verification for that
        branch, since findings_path dispatch never reaches inline mode."""
        assert "how many were dismissed as undecidable" in self._body()


class TestSkillFidelityReviewerNameResolutionOrdering:
    """Pin Name resolution's Glob-then-filter-then-Read ordering.

    Anchor order, not mere presence, is the contract -- a regression
    that reintroduces a body Read ahead of the filters would still
    contain all three anchor phrases, just in the wrong order.
    """

    def _body(self):
        return _agent_body("skill-fidelity-reviewer")

    def test_globs_for_existence_before_filtering_before_reading(self):
        """Existence must resolve via Glob, then the out-of-scope and
        decidability filters apply, and only a skill surviving both gets
        a body Read."""
        body = self._body()
        glob_anchor = body.index("`Glob` for `~/.claude/skills/<name>/SKILL.md`")
        filter_anchor = body.index("check it against the out-of-scope list above")
        read_anchor = body.index(
            "`Read` the body only for a skill that reaches this step"
        )
        assert glob_anchor < filter_anchor < read_anchor, (
            "anchor order inverted -- Name resolution must Glob for "
            "existence, then filter, then Read, in that order"
        )

    def test_unfamiliar_skill_defaults_to_reading_not_guessed_undecidable(self):
        """A skill name matching neither the out-of-scope list nor the
        enumerated undecidable exemplars -- an unfamiliar skill with a
        diff-invisible artifact, say -- must default to a body read
        rather than a guessed dismissal. Without this default, an
        unfamiliar skill's dismissal is indistinguishable in output
        shape from a correctly-reasoned one, silently regressing the
        exact property this agent exists to protect."""
        body = self._body()
        assert (
            'A name matching neither list defaults to a body read, not '
            'a guessed "undecidable"' in body
        )


class TestSkillFidelityReviewerArchitectConsultCheck:
    """Pin the architect-consult check's narrowed two-case emission contract
    and the Input-contract correction it depends on.

    The check emits [SILENT-SKIP] in exactly one case -- a gate-deny row with
    no later consult row on the timeline -- and forecloses it everywhere
    else: a reviewer that reverted to treating zero `architect-consult` rows
    alone as an unmet obligation would silently reintroduce the false-claim
    risk the no-deny case forecloses. And the Input contract's two stale
    clauses must stay corrected: a future edit that reintroduced either --
    "every Agent/Task dispatch" or "present only for code-review's
    obligation" -- would make the agent believe absence of a row proves
    absence of a dispatch, which the no-deny case's own reasoning depends on
    being false.
    """

    def _body(self):
        """Whitespace-normalized so a substring spanning a markdown line
        wrap (the body's line width isn't a contract this test pins) still
        matches -- same convention as this file's other agent/skill-body
        substring pins."""
        return " ".join(_agent_body("skill-fidelity-reviewer").split())

    def _section(self, body: str) -> str:
        start = body.index("## The architect-consult check")
        end = body.index("## The standard")
        return body[start:end]

    def test_declares_own_heading_independent_of_code_review_scope(self):
        """The new check must fire on timeline-presence alone, not only
        when a completed code-review pass is in scope."""
        body = self._body()
        assert "## The architect-consult check" in body
        assert "independent of whether any `code-review` pass is in scope" in self._section(body)

    def test_silent_skip_named_inapplicable_in_no_deny_case(self):
        """[SILENT-SKIP] must be explicitly ruled out for the no-deny case,
        with the absence-is-not-evidence reason -- not merely omitted.
        Scoped to the no-deny sub-heading alone, not the whole section span,
        so this can't pass on text that now lives in the gate-deny
        sub-heading instead."""
        section = self._section(self._body())
        no_deny_start = section.index("### No gate deny on the branch's timeline")
        gate_deny_start = section.index("### A gate deny with no later consult")
        assert no_deny_start < gate_deny_start, (
            "the no-deny sub-heading is expected to precede the gate-deny sub-heading"
        )
        no_deny_section = section[no_deny_start:gate_deny_start]
        assert "never `[SILENT-SKIP]`" in no_deny_section
        assert "absence of a row is not evidence of absence" in no_deny_section

    def test_states_three_outcome_verdict_set(self):
        """The check's three honest outcomes -- consult-observed [DISCLOSED],
        the dismissed-as-undecidable fallback, and the narrowed
        [SILENT-SKIP] exception -- must all appear. This method is the
        class's single enumeration of the verdict set."""
        section = self._section(self._body())
        assert "[DISCLOSED]" in section
        assert "Dismissed as undecidable" in section
        gate_deny_start = section.index("### A gate deny with no later consult")
        assert "[SILENT-SKIP]" in section[gate_deny_start:]

    def test_gate_deny_exception_named_in_gate_deny_case(self):
        """The one case that earns [SILENT-SKIP] -- a denial row naming the
        architect-consult gate with no later consult row -- must be pinned
        in its own sub-heading, keyed on the message text rather than the
        hook field."""
        section = self._section(self._body())
        no_deny_start = section.index("### No gate deny on the branch's timeline")
        gate_deny_start = section.index("### A gate deny with no later consult")
        assert no_deny_start < gate_deny_start, (
            "the no-deny sub-heading is expected to precede the gate-deny sub-heading"
        )
        gate_deny_section = section[gate_deny_start:]
        assert "is the one exception that earns `[SILENT-SKIP]`:" in gate_deny_section
        assert "Blocked by architect-consult gate:" in gate_deny_section
        assert "no later `architect-consult` row on the same branch's timeline" in gate_deny_section
        assert "never on the row's `hook=` field" in gate_deny_section

    def test_initiation_not_completion_wording(self):
        """The check must state a consult dispatch was initiated, never that
        it completed."""
        assert "*initiated*, never that it completed" in self._section(self._body())

    def test_input_contract_no_longer_claims_every_agent_task_dispatch(self):
        """The stale 'every Agent/Task dispatch' claim must be gone --
        _is_reviewer_subagent_type gates reviewer-spawn rows."""
        assert "every `Agent`/`Task` dispatch on this branch's main thread" not in self._body()

    def test_input_contract_no_longer_claims_timeline_is_code_review_only(self):
        """The stale 'present only when checking code-review's
        spawn-dispatch obligation' claim must be gone -- the timeline now
        also serves the architect-consult check."""
        assert "present only when checking" not in self._body()

    def test_input_contract_names_architect_consult_rows(self):
        """The Input contract must say what the timeline does contain --
        reviewer-spawn rows and architect-consult rows, both sourced from
        the main thread and every dispatched subagent, marked by a `thread`
        field, with the widening's safety rationale pinned at its single
        source rather than restated here. Each assertion pins a distinct
        clause of the split sentence, not overlapping substring slices of
        one unbroken sentence -- an innocuous rewording of one clause can
        trip at most one of these."""
        body = self._body()
        assert "`architect-consult` rows" in body
        assert "Both row types are sourced from the main thread and every dispatched subagent's own transcript" in body
        assert "marked with a `thread` field" in body
        assert "design-decisions.md` §58" in body

    def test_description_names_architect_consult_check(self):
        """The frontmatter description must name the third check for a
        human skimming the roster."""
        assert "plan-architect consult dispatch" in self._body()

    def test_output_format_has_architect_consult_record_section(self):
        """The consult-observed record needs its own labeled Output-format
        slot, excluded from both <N> and <M>."""
        body = self._body()
        assert "## Architect consults observed" in body
        assert "Never counted in `<N>` or `<M>`" in body
