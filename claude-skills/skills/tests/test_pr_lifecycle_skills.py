"""pr-description, branch-management, respond-pr, tighten-prose and git-feature-branch-sync."""
from __future__ import annotations

import re
from collections.abc import Iterable

import pytest
from _skill_md_helpers import (
    _assert_pinned_clause_right_bounded,
    _blank_code_regions,
    _blank_frontmatter,
    _raw_heading_section_text,
    _section_between,
    _skill_body,
    _skill_file,
)

# pyproject.toml's pythonpath also puts claude/.claude/tests on the import
# path, where these shared test helpers live.
from helpers import SCRIPTS_DIR


class TestPrDescriptionTwoModeDispatch:
    """Pin pr-description's author/sync branching, in both directions.

    The wiring tests in test_convention_skills.py only prove that /ready-for-review and /handoff name
    the skill; they stay green if its body collapses back to sync-only. That
    collapse is the regression that matters: with no author mode, step 5 has
    nothing to do on a branch without a PR, and the first PR body — the one
    reviewers read first — goes out unchecked again.

    The negative assertion is not redundant with the positive one. A body that
    describes author mode while still carrying the old "no open PR, stop"
    precondition is self-contradicting, and a presence-only check passes it.
    """

    def test_pr_description_declares_author_mode(self):
        """pr-description must dispatch to an authoring mode when no PR is open."""
        assert "No open PR → author mode." in _skill_file("pr-description").read_text()

    def test_pr_description_declares_sync_mode(self):
        """pr-description must still dispatch to the sync mode when a PR is open."""
        assert "Open PR → sync mode." in _skill_file("pr-description").read_text()

    def test_pr_description_has_no_stop_on_missing_pr_precondition(self):
        """pr-description must not retain a precondition that halts when no PR exists."""
        assert "no PR to sync" not in _skill_file("pr-description").read_text()

    def test_pr_description_takes_its_base_from_the_divergence_line(self):
        """pr-description takes the base from the divergence script's printed
        `Default branch:` or `Base branch:` line, never from the open PR's
        `baseRefName`.

        Only the `Base branch:` name has passed the script's name-shape check
        (see the branch-divergence-status.sh header). The `Default branch:` name
        comes from origin/HEAD and is not shape-checked.
        """
        body = " ".join(_skill_file("pr-description").read_text().split())
        assert (
            "`<base>` is `origin/<name>`. `<name>` is the branch on the "
            "`Default branch:` or `Base branch:` "
            "line that `~/.claude/scripts/branch-divergence-status.sh` printed "
            "for this branch's current state. Take it from no other source: "
            "not a caller-passed name, not a fallback. Run the script first. "
            "A run earlier in this session on this branch may be reused if no "
            "stack key, retarget or sync has happened since. If the script exits "
            "non-zero or prints neither line, report its stderr and stop. "
            "Run none of the commands below:"
        ) in body
        assert "open PR's base" not in body
        assert "baseRefName" not in body


class TestPrDescriptionExternalStateCheck:
    """Pin the Flag-and-fix bullet that catches claims about state outside
    the repo (GH-476) -- a follow-up ticket said to be pending, a promise
    made as `will create`.

    Content-claim verification (the neighboring bullet) only re-reads files
    in this repo at HEAD; the marker-word bullet below only pattern-matches
    literal `TBD`/`pending` text. Neither catches "will be filed" or "not
    yet confirmed" going stale without a marker word, which is the gap this
    bullet closes.
    """

    def _body(self):
        return _skill_file("pr-description").read_text()

    def test_declares_source_verification_mechanism(self):
        """Mutation-tested: dropping this sentence collapses the bullet to a
        heading with no instruction on *how* to re-check a claim, which is
        indistinguishable from the bullet never having been added."""
        assert "at its own source" in self._body()

    def test_bounds_identifier_carry_to_named_trackers(self):
        """Mutation-tested independently of the mechanism assertion above --
        dropping only this clause leaves the re-check instruction intact but
        removes the guard against writing an internal tracker identifier
        into a body that never named that tracker (ciso-reviewer finding,
        GH-476 plan review)."""
        assert "already names that tracker" in self._body()

    def test_bullet_falls_between_content_claim_and_marker_checks(self):
        """Order-sensitive: a plain `in body` substring check would stay
        green even if the bullet were moved elsewhere in the list, silently
        breaking its own cross-references to the neighboring bullets it
        names by heading."""
        body = self._body()
        start = body.index("**Content-claim verification.**")
        end = body.index('`TBD` / `pending` / "to be updated" markers')
        assert end > start, "marker-bullet anchor must follow the content-claim bullet"
        segment = body[start:end]
        assert "External-state claims" in segment
        assert "at its own source" in segment

    def test_disambiguates_ci_wiring_from_ci_status(self):
        """Mutation-tested independently of the other three assertions in
        this class: dropping only this sentence leaves the re-check-and-
        rewrite instruction intact but removes the boundary against the
        neighboring Reviewer-action-items bullet, which strips CI-status
        placeholders outright -- without it, an implementer has no signal
        that this bullet and that one disagree on what to do with a CI
        claim, and can misclassify a CI-passing claim as re-verify-and-
        rewrite instead of strip (staff-sdet finding, GH-476 code review)."""
        assert "whether CI is *passing* is not" in self._body()


def _bullet_lead_in(line: str) -> str | None:
    """The bold lead-in of a column-0 `- **...**` bullet line, minus one terminal period.

    Returns None for any line that does not open a bold-led bullet.
    """
    match = re.match(r"- \*\*(.+?)\*\*", line)
    return match.group(1).removesuffix(".") if match else None


def _bullet_text(section_text: str, lead_in: str) -> str:
    """Whitespace-collapsed text of the bullet whose bold lead-in is `lead_in`, up to the next
    blank line, column-0 `- ` bullet, or markdown heading.

    An indented nested bullet is continuation text.
    """
    lines = section_text.splitlines()
    start = next((i for i, line in enumerate(lines) if _bullet_lead_in(line) == lead_in), None)
    assert start is not None, f"no bullet opens with bold lead-in {lead_in!r}"
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if not lines[i].strip() or lines[i].startswith("- ") or re.match(r"#{1,6} ", lines[i])
        ),
        len(lines),
    )
    return " ".join(" ".join(lines[start:end]).split())


class TestBulletHelpers:
    """Fixture tests for the `_bullet_lead_in` and `_bullet_text` helpers."""

    def test_bullet_lead_in_strips_one_terminal_period_and_accepts_none(self):
        """Allow fixture: the bold span is returned with a single terminal
        period removed, or unchanged when it has none."""
        assert _bullet_lead_in("- **Name.** body") == "Name"
        assert _bullet_lead_in("- **Name** body") == "Name"

    def test_bullet_lead_in_removes_only_one_terminal_period(self):
        """Allow fixture: a span ending in two periods keeps one."""
        assert _bullet_lead_in("- **Name..** body") == "Name."

    @pytest.mark.parametrize(
        "line",
        [
            "  - **Name.** body",
            "- Name. body",
            "- text **Name.** body",
        ],
        ids=["indented-bullet", "non-bold-bullet", "bold-not-at-bullet-opening"],
    )
    def test_bullet_lead_in_returns_none_for_a_line_that_does_not_open_a_bold_bullet(self, line):
        """Deny fixture: only a column-0 bullet whose bold span opens it
        yields a lead-in."""
        assert _bullet_lead_in(line) is None

    def test_bullet_lead_in_stops_at_the_first_bold_span(self):
        """Allow fixture: the bold span is non-greedy, so a second bold span
        later in the line is not swallowed into the lead-in."""
        assert _bullet_lead_in("- **A.** **B** rest") == "A"

    def test_bullet_text_excludes_the_following_bullet(self):
        """Deny fixture: text present only in the next bullet must not appear
        in the first bullet's result."""
        section_text = "- **First.** Alpha text.\n- **Second.** Beta text.\n"
        assert "Alpha text." in _bullet_text(section_text, "First")
        assert "Beta text." not in _bullet_text(section_text, "First")

    def test_bullet_text_stops_at_a_blank_line_and_at_a_heading(self):
        """Deny fixture: prose after a blank line, or after a markdown heading
        with no blank line before it, is not part of the bullet."""
        after_blank = "- **First.** Alpha text.\n\nStray paragraph.\n"
        assert _bullet_text(after_blank, "First") == "- **First.** Alpha text."
        after_heading = "- **First.** Alpha text.\n## Next section\nHeading body.\n"
        assert _bullet_text(after_heading, "First") == "- **First.** Alpha text."

    def test_bullet_text_treats_a_hash_that_is_not_a_heading_as_continuation(self):
        """Allow fixture: a column-0 `#` not followed by a space is wrapped
        prose, so it stays in the bullet."""
        section_text = "- **First.** Alpha text\n#123 continues here.\n"
        assert _bullet_text(section_text, "First") == "- **First.** Alpha text #123 continues here."

    def test_bullet_text_includes_an_indented_nested_bullet(self):
        """Allow fixture: an indented nested bullet is continuation text."""
        section_text = "- **First.** Alpha text.\n  - Nested detail.\n- **Second.** Beta text.\n"
        assert _bullet_text(section_text, "First") == "- **First.** Alpha text. - Nested detail."

    def test_bullet_text_is_independent_of_hard_wrap_position(self):
        """Allow fixture: a hard-wrapped bullet equals its unwrapped form."""
        wrapped = "- **First.** Alpha\n  text that\nwraps.\n"
        unwrapped = "- **First.** Alpha text that wraps.\n"
        assert _bullet_text(wrapped, "First") == _bullet_text(unwrapped, "First")

    def test_bullet_text_raises_when_the_lead_in_is_missing(self):
        """Deny fixture: a renamed lead-in fails loudly instead of returning an
        empty string that every negative assertion would pass against."""
        with pytest.raises(AssertionError, match="no bullet opens with bold lead-in"):
            _bullet_text("- **Other.** Body.\n", "First")


class TestPrDescriptionBranchHistoryCheck:
    """Pin the rules that keep review-round and reviewer-attribution narration
    out of a PR body, a shape the per-commit-narrative check does not catch.

    No eval covers pr-description, so these are presence tripwires, not proof
    that an agent follows the rules.
    """

    _AUTHORING_LEAD_IN = "Current state, not branch history"

    def _authoring_section(self):
        return _raw_heading_section_text(_skill_file("pr-description"), "## What the body must carry")

    def _authoring_bullet(self):
        return _bullet_text(self._authoring_section(), self._AUTHORING_LEAD_IN)

    def _check_bullet(self):
        section = _raw_heading_section_text(_skill_file("pr-description"), "## Checks")
        return _bullet_text(section, "Branch-history narration")

    def _test_plan_bullet(self):
        return _bullet_text(self._authoring_section(), "A `## Test plan` of results, not a checklist")

    def test_authoring_bullet_excludes_review_history_and_attribution(self):
        """Dropping this sentence lets review rounds, superseded designs, and
        reviewer attribution into the body."""
        assert "Review rounds, superseded designs, and who found what stay out" in self._authoring_bullet()

    def test_authoring_bullet_keeps_still_true_findings_as_present_tense_facts(self):
        """Dropping this sentence lets the history rule strip a finding,
        limitation, or accepted risk that is still true at HEAD."""
        assert (
            "A finding, limitation, or accepted risk that is still true at HEAD stays as a present-tense fact"
            in self._authoring_bullet()
        )

    def test_authoring_bullet_rejects_branch_only_mechanism_as_context(self):
        """Dropping this sentence lets a mechanism visible only in the branch's
        own history pass as reviewer context."""
        assert "A mechanism that exists only in the branch's own history is not context" in self._authoring_bullet()

    def test_authoring_bullet_routes_rejected_designs_to_alternatives_not_context(self):
        """Dropping either half lets a rejected approach a reviewer would
        propose land in Context, or leaves it with no home."""
        bullet = self._authoring_bullet()
        assert "belongs in `## Alternatives considered`" in bullet
        assert "not in Context" in bullet

    def test_authoring_bullet_exempts_machine_managed_blocks(self):
        """Dropping this sentence applies the history rule to the deferred
        review findings block, which the coherence pass reinserts verbatim."""
        assert "The machine-managed blocks under Checks below are exempt" in self._authoring_bullet()

    def test_check_names_both_narration_tells(self):
        """One tell per narration category (round-based and reviewer-based), so
        a category dropped entirely fails here."""
        bullet = self._check_bullet()
        assert "earlier rounds" in bullet
        assert "reviewer or agent name" in bullet

    def test_check_defers_to_authoring_bullet_by_name(self):
        """The Check bullet's bold pointer name must equal the authoring bullet's
        lead-in, so a rename or repoint of the pointer fails here."""
        pointer_names = re.findall(r"per \*\*(.+?)\*\* above", self._check_bullet())
        assert len(pointer_names) == 1, (
            f"expected one `per **<name>** above` pointer in the Check bullet, extracted {pointer_names!r}"
        )
        assert pointer_names == [self._AUTHORING_LEAD_IN]

    def test_test_plan_bullet_scopes_review_result_to_the_final_pass(self):
        """The Test plan legitimately reports review outcomes; its authoring
        bullet distinguishes the final result from round-by-round history."""
        assert (
            "state what the final review pass returned, not how many rounds ran or what earlier ones found"
            in self._test_plan_bullet()
        )


def _bullet_pointer_names(template_text: str) -> list[str]:
    """Bold names a template points at with `follow its **<name>**`."""
    return re.findall(r"follow its \*\*(.+?)\*\*", template_text)


def _missing_bullet_lead_ins(names: Iterable[str], section_text: str) -> list[str]:
    """Names in `names` that no `- **<name>.**` bullet in `section_text` opens.

    Each bullet's bold lead-in span, minus one terminal period, must equal the
    name exactly, so a truncated name does not match.
    """
    lead_in_names = {name for line in section_text.splitlines() if (name := _bullet_lead_in(line)) is not None}
    return [name for name in names if name not in lead_in_names]


class TestPrDescriptionDefaultTemplateWiring:
    """Wiring tripwire: SKILL.md points at DEFAULT_TEMPLATE.md through the
    harness-substituted skill-directory variable, and the file it points at
    exists with the five section headings in their fixed order. The
    citation-resolution, runtime-read URL-hygiene, and per-name auxiliary-file
    tests also read the file, but none checks the pointer or the heading
    order, so a reordered template would otherwise ship silently."""

    _EXPECTED_HEADINGS = [
        "Summary",
        "Screenshots",
        "Context for the reviewer",
        "Alternatives considered",
        "Test plan",
    ]

    def test_skill_md_points_at_default_template(self):
        assert "${CLAUDE_SKILL_DIR}/DEFAULT_TEMPLATE.md" in _skill_file("pr-description").read_text()

    def test_default_template_file_exists(self):
        assert (_skill_file("pr-description").parent / "DEFAULT_TEMPLATE.md").is_file()

    def test_default_template_has_the_five_headings_in_order(self):
        template = (_skill_file("pr-description").parent / "DEFAULT_TEMPLATE.md").read_text()
        prose = _blank_code_regions(_blank_frontmatter(template))
        headings = re.findall(r"^## (.+)$", prose, flags=re.MULTILINE)
        assert headings == self._EXPECTED_HEADINGS

    def test_template_bullet_pointers_resolve_to_skill_md_bullets(self):
        """The template's Summary and Test plan sections are only pointers to
        bold-named SKILL.md bullets. The `§ "Heading"` half of each pointer is
        checked by the citation test; this pins the bold-bullet half, so
        renaming either bullet in SKILL.md fails here."""
        skill_md_path = _skill_file("pr-description")
        template = (skill_md_path.parent / "DEFAULT_TEMPLATE.md").read_text()
        pointed_at_names = _bullet_pointer_names(template)
        assert len(pointed_at_names) == 2, (
            f"expected the Summary and Test plan pointers, extracted {pointed_at_names!r}; "
            "update this test's extractor if a pointer was reworded"
        )
        section_text = _raw_heading_section_text(skill_md_path, "## What the body must carry")
        assert _missing_bullet_lead_ins(pointed_at_names, section_text) == []

    def test_bullet_lead_in_check_flags_a_renamed_bullet(self):
        """Deny fixture: a bullet renamed in SKILL.md must be reported missing."""
        section_text = "- **What and why.** Body.\n- **Verification results.** Body.\n"
        assert _missing_bullet_lead_ins(["What and why"], section_text) == []
        assert _missing_bullet_lead_ins(["What and why", "A test plan"], section_text) == ["A test plan"]
        renamed_section = "- **Why and what.** Body.\n"
        assert _missing_bullet_lead_ins(["What and why"], renamed_section) == ["What and why"]
        # A truncated name that is only a prefix of the bold span does not match.
        assert _missing_bullet_lead_ins(["What"], section_text) == ["What"]
        # A bold phrase that is not a bullet lead-in does not satisfy the check.
        assert _missing_bullet_lead_ins(["What and why"], "Prose mentioning **What and why** inline.\n") == [
            "What and why"
        ]

    def test_bullet_pointer_extractor_drops_a_reworded_pointer(self):
        """Deny fixture: a pointer reworded out of the `follow its **X**` form
        yields fewer names than the two the wiring test requires."""
        both_pointers = "follow its **What and why** bullet.\n...\nfollow its **A test plan** bullet.\n"
        assert _bullet_pointer_names(both_pointers) == ["What and why", "A test plan"]
        one_reworded = "use its **What and why** bullet.\n...\nfollow its **A test plan** bullet.\n"
        assert _bullet_pointer_names(one_reworded) == ["A test plan"]


class TestBranchManagementWorktreeEnsureWiring:
    """Wiring tripwire, not a behavioral test: the script's checks, states and
    exit codes are validated behaviorally by
    claude/.claude/scripts/tests/test_worktree_ensure.py (real subprocess
    execution against worktree-ensure.sh). This class only proves the skill
    body still names the script, its call form, its output protocol and the
    failure rule the script's header leaves to the skill."""

    @staticmethod
    def _collapsed(text: str) -> str:
        """SKILL.md is hard-wrapped, so collapse each whitespace run before a substring test."""
        return " ".join(text.split())

    def _assert_body_contains(self, phrase: str) -> None:
        body = self._collapsed(_skill_body("branch-management"))
        assert self._collapsed(phrase) in body, f"branch-management/SKILL.md lost: {phrase!r}"

    def test_script_exists(self):
        assert (SCRIPTS_DIR / "worktree-ensure.sh").is_file()

    @pytest.mark.parametrize(
        "phrase",
        [
            pytest.param("~/.claude/scripts/worktree-ensure.sh '<branch>'", id="call-form"),
            pytest.param("./install.sh", id="missing-script-remedy"),
            pytest.param("ASCII letters, digits, `.`, `_`, `/` and `-`", id="branch-name-character-set"),
            pytest.param("`next:`", id="next-line-marker"),
            pytest.param("`created `", id="created-state"),
            pytest.param("`existing `", id="existing-state"),
            pytest.param("`current `", id="current-state"),
            pytest.param(
                "Commands that write are the engineer's to run, wherever they are suggested",
                id="writing-command-bar",
            ),
            pytest.param("Never fall back to a hand-typed `git worktree add`", id="no-hand-typed-add"),
            pytest.param("correct the call and rerun once", id="exit-2-single-rerun"),
            pytest.param("A second exit 2", id="exit-2-second-occurrence"),
            pytest.param("goes to the engineer", id="exit-2-escalation"),
            pytest.param("On `current`, skip `EnterWorktree`.", id="skip-enter-worktree-on-current"),
            pytest.param("`usage`: call the script with exactly one argument.", id="usage-bullet"),
            pytest.param("`bad-name`: rewrite the name within the set in step 1.", id="bad-name-bullet"),
            pytest.param(
                "`in-main`: ask the engineer to switch the main checkout to another branch, then rerun.",
                id="in-main-bullet",
            ),
            # test_require_worktree_for_git_writes.py accepts a gate denial for these names
            # as a known limitation because this rule tells the agent to rename the branch.
            pytest.param(
                "when the name is `git` or ends in `/git`: rename the branch",
                id="rename-a-branch-the-gate-reads-as-a-git-command",
            ),
        ],
    )
    def test_skill_body_keeps_script_protocol_phrase(self, phrase):
        self._assert_body_contains(phrase)

    @pytest.mark.parametrize("state", ["created", "existing", "current"])
    def test_state_word_the_skill_instructs_on_is_in_the_script_stdout_grammar(self, state):
        script_text = (SCRIPTS_DIR / "worktree-ensure.sh").read_text()
        stdout_grammar = re.search(r"^# Stdout on exit 0.*?^# On exit 1 or 2", script_text, re.S | re.M)
        assert stdout_grammar, "worktree-ensure.sh header lost its stdout grammar paragraph"
        assert re.search(rf"\b{state}\b", stdout_grammar.group(0))
        assert f"`{state} `" in self._collapsed(_skill_body("branch-management"))

    @pytest.mark.parametrize("token", ["usage", "bad-name", "in-main"])
    def test_failure_token_the_skill_instructs_on_is_in_the_script_token_list(self, token):
        script_text = (SCRIPTS_DIR / "worktree-ensure.sh").read_text()
        token_list = re.search(r"^# Tokens:(.*?)\.$", script_text, re.S | re.M)
        assert token_list, "worktree-ensure.sh header lost its token list"
        assert token in re.findall(r"[a-z]+(?:-[a-z]+)*", token_list.group(1))
        assert f"`{token}`:" in self._collapsed(_skill_body("branch-management"))


class TestPrDescriptionCostSectionWiring:
    """Wiring tripwire, not a behavioral test: the `## Cost (list-price
    estimate)` section's actual runtime behavior -- sentinel absent or mode
    not "dollars" -> block deleted if present; mode "dollars" -> sync
    regenerates; detached HEAD -> section omitted -- is validated
    behaviorally by claude/.claude/scripts/tests/test_pr_cost_section.py
    (real subprocess execution against pr-cost-section.sh), not here. The
    account-scoped mode-grammar gate and the config-dir resolution live in
    that script rather than in this skill body (docs/worktree-bash-guard.md),
    so pinning their exact source shape a second time here would be
    redundant with that behavioral suite -- same reasoning this class
    already applies to install.sh's own _report_account_sentinel. The
    delimiters, the heading, and the reproducibility trailer are emitted by
    pr-cost-section.sh and pinned byte-exactly by test_pr_cost_section.py.
    This class only proves the skill body still names the script, both
    delimiters, the heading, and the raw-markdown rule.
    """

    def _body(self):
        return _skill_file("pr-description").read_text()

    def test_declares_pr_cost_delimiters(self):
        body = self._body()
        assert "<!-- pr-cost:start -->" in body
        assert "<!-- pr-cost:end -->" in body

    def test_declares_account_scoped_mode_gate(self):
        body = self._body()
        assert "pr-cost-disclosure" in body
        assert "~/.claude/scripts/pr-cost-section.sh" in body

    def test_declares_cost_heading_literal(self):
        body = self._body()
        assert "## Cost (list-price estimate)" in body

    def test_declares_raw_markdown_not_code_fence(self):
        body = self._body()
        assert "raw markdown" in body
        assert "code fence" in body


class TestPrDescriptionProseTighteningPassWiring:
    """Wiring tripwire for the `## Prose tightening pass` section, mirroring
    TestPrDescriptionCostSectionWiring's treatment of the neighboring `##
    Cost section` above it -- pr-description has no behavioral test suite
    in this repo, so these assertions only prove the section is present,
    correctly placed, and dispatches by name; not that it executes
    correctly (execution requires manual verification)."""

    def _body(self):
        return _skill_file("pr-description").read_text()

    def _prose_section(self):
        """The `## Prose tightening pass` section's own text, isolated from
        the rest of the body -- an assertion against the whole body can pass
        on unrelated text from a neighboring section (e.g. `## Cost
        section`'s own CLAUDE_CONFIG_DIR reference) even when the new
        section itself dropped what's being checked for."""
        body = self._body()
        start = body.index("## Prose tightening pass")
        end = body.index("## Checks", start)
        return body[start:end]

    def test_declares_config_get_invocation(self):
        section = self._prose_section()
        assert "config-get.sh pr_description_tighten_prose" in section

    def test_section_placed_before_checks(self):
        """Order-sensitive: the pass must run before ## Checks so the
        reader-coherence pass and content-claim verification validate the
        final, already-tightened bytes rather than pre-rewrite text."""
        body = self._body()
        prose_pass_index = body.index("## Prose tightening pass")
        checks_index = body.index("## Checks")
        assert prose_pass_index < checks_index, (
            "## Prose tightening pass must precede ## Checks"
        )
        cost_section_index = body.index("## Cost section")
        assert cost_section_index < prose_pass_index, (
            "## Prose tightening pass must follow ## Cost section"
        )

    def test_dispatches_tighten_prose_by_name(self):
        assert "dispatch `tighten-prose` by name" in self._body()


class TestRespondPrPromiseRedemption:
    """Pin the Guidelines bullet requiring a filed `will create` ticket to
    update every place that promise was already published (GH-476).

    Placed beside the existing stale-SHA rule (:111), which governs the
    identical shape -- a claim in an already-posted reply going stale
    during the session -- and prescribes the same correction-reply remedy,
    not respond-pr's separate in-place-PATCH path (:101), which is scoped
    to typos and factual errors in Claude's own comments.
    """

    def _body(self):
        return _skill_file("respond-pr").read_text()

    def test_requires_sweeping_already_published_artifacts(self):
        """Mutation-tested: dropping this clause leaves the heading with no
        obligation to act on it -- indistinguishable from no rule at all."""
        assert "correct every place that promise was already published" in self._body()

    def test_requires_correction_reply_for_prior_replies(self):
        """Mutation-tested independently of the other two assertions in this
        class: dropping only this clause leaves 'correct every place' with
        no named remedy for the already-posted-reply half of the sweep --
        regressing to only the PR-body half via the /pr-description pointer,
        with no test signal (staff-sdet finding, GH-476 code review)."""
        assert "post a correction reply for earlier replies" in self._body()

    def test_points_at_pr_description_for_the_body_surface(self):
        """Mutation-tested independently of the sweep assertion above --
        dropping only the pointer leaves 'correct every place' with no
        named mechanism for the PR-body half of that sweep, which
        respond-pr itself has no tool to perform."""
        assert "re-running `/pr-description`" in self._body()


class TestRespondPrEditBoundary:
    """Pins respond-pr step 6's sweep-boundary sentence.

    The sweep's edits stay inside files the PR already changes, and the step-8 commit carrying them needs a clean
    /code-review of that staged diff (require-code-review.sh). The sweep therefore needs no confirmation stop for
    hook, gate or permission files.
    """

    def test_step6_bounds_the_sweep_to_the_changed_files_the_pr_lists(self):
        assert (
            "The sweep's edits stay inside the PR's changed files, which `gh pr diff --name-only` lists."
            in _skill_file("respond-pr").read_text()
        )


class TestTightenProseScopeCarveOutPhrases:
    """Regression guard on tighten-prose's scope rule, mirroring
    TestContinuityFileBucketCrosscheck's guard on brief's critical-rule
    text. REFERENCES.md is never loaded at runtime, so the durable-doc/
    plan-file scope boundary must live in the SKILL.md body itself -- this
    pins those carve-out phrases so a future edit can't silently drop them.
    """

    def test_declares_references_md_carve_out(self):
        assert "REFERENCES.md" in _skill_file("tighten-prose").read_text()

    def test_declares_plan_file_carve_out(self):
        assert ".claude/plans/" in _skill_file("tighten-prose").read_text()


# respond-pr step 3's failed-recipe sentence: re-run once, then abort with the
# PR number, the already-re-run fact, the recipe's stderr and the next action,
# and never route to /git-feature-branch-sync, whose detection runs the same
# script. Mirrors ready-for-review step 6's pinned recipe-failure clause.
_PINNED_RESPOND_PR_STEP3_FAILED_RECIPE_SENTENCE = (
    "When the recipe exits non-zero, re-run it once. If it fails again, abort "
    "the skill. The abort message names the PR number (captured in step 1), "
    "says the recipe was already re-run once, gives the recipe's stderr, and "
    "ends with the engineer's next action (fix the cause the stderr names, "
    "then re-invoke `/respond-pr`). Do not route to "
    "`/git-feature-branch-sync`, because its detection runs the same script."
)

# respond-pr step 3's CONFLICT bullet: its bold label, then abort naming the PR
# number, route to /git-feature-branch-sync, and apply no FIXED changes onto a
# stale tree, then the next bullet's `- **Trial merge is CLEAN.**` marker and
# label. The leading label binds the clauses to CONFLICT and the trailing label
# bounds the pin, so a clause appended inside the bullet fails it.
_PINNED_RESPOND_PR_STEP3_CONFLICT_BULLET = (
    "- **Trial merge reports CONFLICT.** Abort the skill — print a message naming "
    "the PR number (captured in step 1) and route the user to "
    "`/git-feature-branch-sync`. Do not apply `FIXED` changes onto a stale tree; "
    "replies acknowledging fixes would refer to a commit the reviewer cannot "
    "cleanly resolve. - **Trial merge is CLEAN.**"
)

# respond-pr step 3's CLEAN bullet: its bold label, then proceed even when behind,
# since only an actual conflict blocks, then the next bullet's
# `- **Recipe exits non-zero.**` marker and label as the right bound.
_PINNED_RESPOND_PR_STEP3_CLEAN_BULLET = (
    "- **Trial merge is CLEAN.** Proceed even when behind > 0. Small drift is "
    "normal during review, and the precheck only blocks on actual conflicts. "
    "- **Recipe exits non-zero.**"
)


# respond-pr step 3's lead sentence: the ordering rule that puts the recipe ahead
# of any FIXED change or reply. The rest of the sentence (the pointer and the
# base-line clause) is not pinned.
_PINNED_RESPOND_PR_STEP3_ORDERING_CLAUSE = (
    "Before applying any `FIXED` change or posting any reply, run the canonical detection recipe"
)


def _respond_pr_step3_text() -> str:
    """Raw text of respond-pr's step 3 list item, from its `3.` marker to the
    line before step 4's `4.` marker. Both markers are asserted found, so a
    renumbered step fails instead of extracting empty text."""
    lines = _skill_file("respond-pr").read_text().splitlines(keepends=True)
    start_idx = next((i for i, line in enumerate(lines) if line.startswith("3. **Divergence precheck.**")), None)
    assert start_idx is not None, "respond-pr/SKILL.md: step 3 (`3. **Divergence precheck.**`) not found"
    end_idx = next((i for i in range(start_idx + 1, len(lines)) if lines[i].startswith("4. ")), None)
    assert end_idx is not None, "respond-pr/SKILL.md: step 4 marker not found after step 3"
    return "".join(lines[start_idx:end_idx])


class TestRespondPrStep3FailedRecipeSentence:
    """Pin respond-pr step 3's failed-recipe sentence, its CONFLICT bullet and
    its CLEAN bullet. A non-zero recipe exit parses as neither CONFLICT nor
    CLEAN, so without the failed-recipe arm the skill would proceed with no
    divergence check. Deleting the do-not-route clause, the PR number, the
    already-re-run fact, the stderr or the next action fails the failed-recipe
    pin, as does appending a contradicting clause. That pin passes
    `_assert_pinned_clause_right_bounded` only because its bullet is the last
    in step 3. An indented sibling added directly after that bullet fails with
    an "unbounded trailing content" message. A sibling or paragraph added after a blank line,
    or a line starting `-` or `#` at column 0, passes."""

    def test_step3_runs_the_recipe_before_any_fixed_change_or_reply(self) -> None:
        """Deleting the lead sentence's ordering clause leaves the three outcome
        bullets passing their pins while nothing runs the recipe first."""
        step3_collapsed = " ".join(_respond_pr_step3_text().split())

        assert _PINNED_RESPOND_PR_STEP3_ORDERING_CLAUSE in step3_collapsed, (
            "respond-pr/SKILL.md: step 3 no longer says to run the canonical detection recipe "
            "before applying any `FIXED` change or posting any reply"
        )

    def test_step3_reruns_a_failed_recipe_once_then_aborts_without_routing_to_sync(self) -> None:
        _assert_pinned_clause_right_bounded(
            " ".join(_PINNED_RESPOND_PR_STEP3_FAILED_RECIPE_SENTENCE.split()),
            _respond_pr_step3_text(),
            context=(
                "respond-pr/SKILL.md: step 3's failed-recipe sentence has drifted from its pinned clause "
                "(re-run once, abort naming the PR number with the recipe's stderr, no routing to "
                "/git-feature-branch-sync)"
            ),
        )

    def test_step3_aborts_on_a_trial_merge_conflict_and_routes_to_sync_without_applying_fixes(self) -> None:
        """Matched as a whitespace-collapsed substring that starts at the
        CONFLICT label and ends at the next bullet's CLEAN label:
        `_assert_pinned_clause_right_bounded` finds no structural boundary after
        a nested bullet, so the next bullet's label is the right bound. Swapping
        the label, deleting the abort, the PR number, the route to
        `/git-feature-branch-sync` or the no-`FIXED`-onto-a-stale-tree clause,
        or appending a clause inside the bullet fails it."""
        step3_collapsed = " ".join(_respond_pr_step3_text().split())

        assert " ".join(_PINNED_RESPOND_PR_STEP3_CONFLICT_BULLET.split()) in step3_collapsed, (
            "respond-pr/SKILL.md: step 3's CONFLICT bullet has drifted from its pinned text "
            "(CONFLICT label, abort naming the PR number, route to /git-feature-branch-sync, "
            "no FIXED changes onto a stale tree, then the CLEAN label)"
        )

    def test_step3_proceeds_on_a_clean_trial_merge_even_when_behind(self) -> None:
        """Matched as a whitespace-collapsed substring that starts at the CLEAN
        label and ends at the next bullet's recipe-failure label, for the same
        reason as the CONFLICT pin. Swapping the label, deleting the proceed
        clause, or appending a clause inside the bullet fails it."""
        step3_collapsed = " ".join(_respond_pr_step3_text().split())

        assert " ".join(_PINNED_RESPOND_PR_STEP3_CLEAN_BULLET.split()) in step3_collapsed, (
            "respond-pr/SKILL.md: step 3's CLEAN bullet has drifted from its pinned text "
            "(CLEAN label, proceed even when behind, then the recipe-failure label)"
        )


# git-feature-branch-sync keeps its recipe examples on the default branch's
# literal names, so the preamble names the base the recipe reports as what they
# stand for. The detection section tells an agent that arrives from the
# SessionStart advisory, which never reads the stack-base key, to sync to the
# branch the recipe names, and to stop on a failed recipe. It also says why: the
# hook never reads the stack-base key, so its advisory counts against the default
# branch. The callers sentence and its worktree-bash-guard pointer stay adjacent,
# and the advisory instruction opens its own paragraph after them. All four clauses
# (placeholder, callers pointer, advisory redirect, advisory reason) are
# whitespace-collapsed substrings: the detection block's paragraph continues past
# its last pinned word.
_PINNED_SYNC_PREAMBLE_PLACEHOLDER_CLAUSE = (
    "use `main` / `origin/main` as the placeholder for the base the recipe reports"
)
_PINNED_SYNC_CALLERS_POINTER_CLAUSE = (
    "at the `/ready-for-review` pre-push gate and the `/respond-pr` precheck. "
    "See `docs/worktree-bash-guard.md` for why the two skill-driven callers go "
    "through a dedicated script instead of restating the recipe inline."
)
_PINNED_SYNC_ADVISORY_REDIRECT_CLAUSE = (
    "After the SessionStart advisory, run the recipe and sync to the branch "
    "its `Default branch:` or `Base branch:` line names, not the advisory's. "
    "If the recipe exits non-zero, report its stderr and stop. "
    "Sync nothing against the advisory's branch."
)
_PINNED_SYNC_ADVISORY_REASON_CLAUSE = (
    "The hook never reads `git config branch.<branch>.gh-merge-base`, so on a "
    "stacked branch its advisory counts against the default branch."
)
_REMOVED_SYNC_UNIFORM_PRIMITIVE_SENTENCE = "Same detection primitive everywhere"


def test_git_feature_branch_sync_sends_a_stacked_branch_to_the_recipes_base() -> None:
    """The sync skill's `main` / `origin/main` examples stand for the base the
    recipe reports, and after the SessionStart advisory (default-branch only) the
    agent runs the recipe and syncs to the branch its `Default branch:` or
    `Base branch:` line names, stopping when the recipe exits non-zero. Deleting
    the placeholder clause or the advisory-redirect clause lets an agent primed
    by the advisory run a literal `git rebase origin/main` on a stacked branch.
    Deleting the advisory-reason clause drops the sentence that the hook never
    reads the stack-base key and so counts against the default branch, which is
    why the redirect is needed. The callers-pointer assert guards the callers
    sentence staying adjacent to its `docs/worktree-bash-guard.md` pointer. The
    paragraph assert guards the post-advisory instruction staying in its own
    paragraph."""
    skill_md_path = _skill_file("git-feature-branch-sync")
    lines = skill_md_path.read_text().splitlines(keepends=True)
    detecting_start, detecting_end = _section_between(lines, "## Detecting divergence", skill_md_path)
    preamble_collapsed = " ".join("".join(lines[:detecting_start]).split())
    detecting_collapsed = " ".join("".join(lines[detecting_start:detecting_end]).split())

    assert _PINNED_SYNC_PREAMBLE_PLACEHOLDER_CLAUSE in preamble_collapsed, (
        f"{skill_md_path}: the preamble no longer says `main` / `origin/main` stand for the base the recipe reports"
    )
    assert " ".join(_PINNED_SYNC_CALLERS_POINTER_CLAUSE.split()) in detecting_collapsed, (
        f"{skill_md_path}: '## Detecting divergence' no longer keeps the callers sentence and its "
        "`docs/worktree-bash-guard.md` pointer adjacent"
    )
    assert "\n\nAfter the SessionStart advisory" in "".join(lines[detecting_start:detecting_end]), (
        f"{skill_md_path}: '## Detecting divergence' no longer puts the post-advisory instruction in its own paragraph"
    )
    assert " ".join(_PINNED_SYNC_ADVISORY_REDIRECT_CLAUSE.split()) in detecting_collapsed, (
        f"{skill_md_path}: '## Detecting divergence' no longer tells the agent to sync to the branch the recipe "
        "names rather than the advisory's, or no longer stops on a failed recipe"
    )
    assert " ".join(_PINNED_SYNC_ADVISORY_REASON_CLAUSE.split()) in detecting_collapsed, (
        f"{skill_md_path}: '## Detecting divergence' no longer says the hook never reads the stack-base key"
    )
    assert _REMOVED_SYNC_UNIFORM_PRIMITIVE_SENTENCE not in skill_md_path.read_text(), (
        f"{skill_md_path}: the sentence calling the hook and the recipe one uniform detection primitive is back"
    )
