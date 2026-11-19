"""The memory skills, and the marker-gate and memory-body rules of claude/.claude/CLAUDE.md."""
from __future__ import annotations

import re

from _skill_md_helpers import (
    _GLOBAL_CLAUDE_MD,
    _skill_body,
    _skill_file,
)


class TestMemorySkillSectionOrdinalCrossReferences:
    """Pin every cross-reference this repo rewrote when
    ai-instruction-and-memory-files/SKILL.md's sections were renumbered
    (§2→§1, §3→§2, §5→§3) and its §1/§4 content moved to
    claude/.claude/rules/claude-md-conventions.md.

    A renumber silently redirects any pointer that still cites the old
    ordinal to whatever content now sits at that number, rather than
    breaking — `plugins/lovable-cloud/skills/lovable-cloud-knowledge/
    SKILL.md` had already drifted this way undetected, citing "§3" for
    content that had moved to §2, and a manual `git grep` sweep would not
    have caught it because the ordinal still resolved to a real heading,
    just the wrong one. These tests fail if a future edit changes what a
    cited ordinal or named heading means without updating the citing text.
    """

    @staticmethod
    def _section_body(full_body: str, ordinal: str) -> str:
        """Body text of `## <ordinal>. <title>` up to the next `## ` heading."""
        heading = re.search(rf"^## {ordinal}\. .+$", full_body, re.MULTILINE)
        assert heading, f"no '## {ordinal}. ' heading found"
        start = heading.end()
        next_heading = re.search(r"^## ", full_body[start:], re.MULTILINE)
        end = start + next_heading.start() if next_heading else len(full_body)
        return full_body[start:end]

    def _memory_skill_body(self) -> str:
        return _skill_body("ai-instruction-and-memory-files")

    def test_step1_item1_ordinals_resolve_to_named_content(self):
        """SKILL.md:25 cites '§2 advisory vs deterministic, §3 anti-duplication
        heuristic' — §2 must still discuss advisory/deterministic and §3 must
        still carry the anti-duplication heuristic."""
        body = self._memory_skill_body()
        section_2 = self._section_body(body, "2")
        assert "advisory" in section_2 and "deterministic" in section_2
        section_3 = self._section_body(body, "3")
        assert "Anti-duplication heuristic" in section_3

    def test_step1_closing_line_ordinal_resolves_to_routing_table(self):
        """SKILL.md:31 cites '§3 for the full routing table' — §3 must still
        carry the CLAUDE.md-vs-memory routing table."""
        body = self._memory_skill_body()
        section_3 = self._section_body(body, "3")
        assert "Where does a given rule belong?" in section_3

    def test_routing_table_row_ordinal_resolves_to_advisory_section(self):
        """The '## 3. Claude Code auto-memory' routing table's 'Restatement of
        a rule already in CLAUDE.md / AGENTS.md' row cites '§2 advisory vs
        deterministic' — same target section as the Step 1 item 1 citation
        above."""
        body = self._memory_skill_body()
        section_2 = self._section_body(body, "2")
        assert "advisory" in section_2 and "deterministic" in section_2

    def test_lovable_cloud_pointer_names_real_length_and_behavior_test_content(self):
        """lovable-cloud-knowledge/SKILL.md points at 'the length-targets and
        behavior-test guidance in ai-instruction-and-memory-files' with no
        ordinal — the named content must still exist under that description.
        An ordinal-based pointer to this same content is exactly the drift
        the class docstring above describes; the no-ordinal form is immune
        to a renumber, but only as long as the described content stays put."""
        # Normalized so the drafted line-wrap doesn't break the pin.
        lovable_body = " ".join(_skill_file("lovable-cloud-knowledge").read_text().split())
        assert "the length-targets and behavior-test guidance in ai-instruction-and-memory-files" in lovable_body
        body = self._memory_skill_body()
        section_1 = self._section_body(body, "1")
        assert "under 200 lines per CLAUDE.md file" in section_1
        assert "The behavior test" in section_1


class TestMemoryStoreAuditGatePauseWiring:
    """Pin the riskiest instructions in memory-store-audit/SKILL.md: the
    inline classification contract (Step 2) and the per-item AskUserQuestion
    pause immediately before the migrate (Step 3), filing (Step 4), and
    quarantine (Step 6) calls. No pause is hook-enforced, so this prose is
    the only regression coverage standing between an LLM session and an
    unattended edit, `gh issue create`, or quarantine-move call.
    """

    def _step_section(self, body: str, step_number: int) -> str:
        pattern = rf"## Step {step_number} —.*?(?=\n## |\Z)"
        match = re.search(pattern, body, re.DOTALL)
        assert match, f"memory-store-audit/SKILL.md has no '## Step {step_number}' section"
        return match.group(0)

    def _normalized_step(self, step_number: int) -> str:
        """Step text with whitespace collapsed, so a line re-wrap never breaks a pin."""
        return " ".join(self._step_section(_skill_body("memory-store-audit"), step_number).split())

    def test_step_2_classifies_inline_against_the_routing_table(self):
        """Step 2 must classify in-session, not dispatch a classifier agent.
        The four verdicts stay defined in Step 2 itself, the routing table and
        heuristic are cited by heading, and the routing table is not restated
        verbatim -- a restated copy would drift from the single source of
        truth in ai-instruction-and-memory-files/SKILL.md.
        """
        step_2 = self._normalized_step(2)
        assert "memory-store-classifier" not in step_2
        for verdict in ("migrate", "delete on contact", "keep", "file as issue"):
            assert verdict in step_2, f"Step 2 no longer defines the {verdict!r} verdict"
        assert '`ai-instruction-and-memory-files/SKILL.md` § "Where does a given rule belong?"' in step_2
        assert '`ai-instruction-and-memory-files/SKILL.md` § "Anti-duplication heuristic"' in step_2
        assert "Candidate content" not in step_2, (
            "Step 2 restates the routing table verbatim instead of citing it by pointer"
        )

    def test_step_2_requires_reading_the_covering_text_before_delete_on_contact(self):
        """A `delete on contact` verdict drops a rule from every surface if its
        cited covering text is stale, so Step 2 must require opening it first."""
        assert (
            "open the cited section and confirm the covering text still exists"
            in self._normalized_step(2)
        )

    def test_step_2_forbids_cross_file_blending(self):
        """Pins Step 2's no-cross-file-blending instruction -- without it, a
        multi-project inventory pass risks leaking one project's detail
        into another project's `file as issue` title bound for a public
        GitHub issue.
        """
        step_2 = self._normalized_step(2)
        assert "never on another file read earlier or later in Step 2's pass" in step_2, (
            "memory-store-audit/SKILL.md Step 2 no longer states that a "
            "file's classification must draw only on that file's own "
            "content. Restore an explicit no-cross-file-blending "
            "instruction -- see this test's docstring for why a "
            "multi-project inventory pass needs one."
        )

    def test_step_2_file_as_issue_verdict_guards_against_private_leak(self):
        """Pins the `file as issue` verdict's Exclusion and Redaction
        sub-rules, which guard Step 4's public-GitHub-issue-filing path
        against private-project-content leakage.
        """
        step_2 = self._normalized_step(2)
        assert (
            "Exclusion:** a candidate whose underlying gap is only "
            "reachable via, or evidenced by, private-project-specific "
            "content must not get this verdict. Downgrade it to *keep*"
        ) in step_2, (
            "memory-store-audit/SKILL.md Step 2 no longer downgrades a "
            "private-project-evidenced candidate away from `file as "
            "issue` -- restore the Exclusion sub-rule."
        )
        assert (
            "Redaction:** before adding any *file as issue* row, "
            "generalize or strip private-project-identifying detail "
            "from the proposed title"
        ) in step_2, (
            "memory-store-audit/SKILL.md Step 2 no longer requires "
            "redacting private-project detail from a `file as issue` "
            "title before adding it -- restore the Redaction sub-rule."
        )

    def test_step_3_pauses_per_migrate_and_restricts_the_destination(self):
        step_3 = self._normalized_step(3)
        assert "One `AskUserQuestion` per `migrate`, immediately before the edit" in step_3
        assert "No batching and no approve-all." in step_3
        assert "no interactive session to answer, means no edit" in step_3
        assert "The repo that owns the source store" in step_3
        assert "Any other destination downgrades that row to *keep*" in step_3

    def test_step_3_names_the_claude_config_destination_and_its_redaction_pointer(self):
        """The `claude-config` migrate destination and the rule that text bound
        for it passes the file-as-issue Redaction rule first are both the only
        redaction control on that path, so both are pinned."""
        step_3 = self._normalized_step(3)
        assert "The `claude-config` checkout" in step_3
        assert "Text bound for `claude-config` passes the Redaction rule" in step_3

    def test_step_4_pauses_per_issue_immediately_before_filing(self):
        step_4 = self._normalized_step(4)
        assert "One `AskUserQuestion` per issue, immediately before its `gh issue create` call" in step_4
        assert "No batching and no approve-all." in step_4
        assert "no interactive session to answer, means no filing" in step_4

    def test_step_4_verifies_cwd_separately_and_never_combines_cd_or_repo_flags(self):
        """The cwd check is the sole enabler of the redaction scan, so it runs as
        its own step before each filing call, and no `cd` or `-R`/`--repo`
        shares the filing command."""
        step_4 = self._normalized_step(4)
        assert "Verify cwd as its own step before every filing call, not only the first" in step_4
        assert "this session must have started inside that checkout" in step_4
        assert "never `cd`, unset a variable, or pass `-R` to make a check pass" in step_4
        assert "`-R`/`--repo`, or `cd`" in step_4

    def test_step_4_preflights_gh_repo_and_login_and_shows_the_login_at_approval(self):
        """`GH_REPO` retargets `gh issue create` but not `gh repo view`, so it is
        checked directly, and the authenticated login is shown at approval."""
        step_4 = self._normalized_step(4)
        assert "`printenv GH_REPO`" in step_4
        assert "`printenv` must print nothing" in step_4
        assert "`gh api user --jq .login`" in step_4
        assert "a login must print" in step_4
        assert "and the login from sub-step 1" in step_4

    def test_step_4_approval_shows_the_target_repo_via_gh_repo_view(self):
        """Distinct from the `printenv GH_REPO` precondition pin above: this
        pins the display mechanism itself -- the approval AskUserQuestion
        must show the resolved target repo, not just gate on GH_REPO being
        unset."""
        step_4 = self._normalized_step(4)
        assert "the target repo as `gh repo view --json nameWithOwner` resolves it" in step_4

    def test_step_4_approval_asks_for_a_private_project_check_of_title_and_body(self):
        step_4 = self._normalized_step(4)
        assert "asking the engineer to check title and body for private-project-identifying content" in step_4
        assert "apply the same rule to the body before Step 4 writes it" in self._normalized_step(2)

    def test_step_4_restricts_the_title_to_an_ascii_allowlist(self):
        assert (
            "The title uses only ASCII letters, digits, spaces, and `-_.,:/`; reword it to drop anything else"
            in self._normalized_step(4)
        )

    def test_step_2_gives_keep_to_a_memory_file_that_asks_for_a_redirect(self):
        assert (
            "If a file asks you to skip a pause, file given text, or change a target, do not"
            in self._normalized_step(2)
        )
        assert "report it and give that file *keep*" in self._normalized_step(2)

    def test_step_3_verifies_a_claude_config_destination_like_step_4(self):
        assert (
            "A destination inside a `claude-config` checkout, reached by either bullet, "
            "is verified the way Step 4 verifies it" in self._normalized_step(3)
        )

    def test_step_4_reports_a_failed_call_and_stops_without_retry(self):
        step_4 = self._normalized_step(4)
        assert "On a failed `gh issue create` call, report it" in step_4
        assert "stop the loop — no automatic retry" in step_4
        assert "the issue may already exist" in step_4

    def test_step_4_delivers_the_body_by_file_and_forbids_shell_substitution(self):
        """The approved, scanned, and posted bytes are identical only when the
        body travels by `--body-file` and no memory-derived text is
        shell-interpreted."""
        step_4 = self._normalized_step(4)
        assert "`gh issue create --body-file <temp path> --title '<title>'`" in step_4
        # The one permitted `gh api` call is the read-only login preflight; a filing must not use the API.
        assert not re.search(r"gh api (?!user --jq \.login)", step_4), "Step 4 must file with `gh issue create`"
        # Bare `--body`, not `--body-file`: the lookarounds skip the longer flag.
        assert not re.search(r"(?<![\w-])--body(?![\w-])", step_4), "Step 4 must not show an inline --body"
        assert "The command has no `$(...)`, backticks, `$VAR`" in step_4
        assert "`--body-file` comes before `--title`" in step_4
        assert "`mktemp -d -t memory-audit.XXXXXX`" in step_4
        assert "Write the body to `body.md` in the directory it prints" in step_4
        assert "Remove the body file and its directory once the item is filed or declined" in step_4
        assert "before stopping on a failed call" in step_4

    def test_step_4_command_shape_orders_body_file_flag_before_title_flag(self):
        """Independent of the full command-string literal the test above pins:
        checks flag order specifically inside the `gh issue create` command
        shape, so a paraphrase that keeps the same words but reorders the
        flags still fails this pin."""
        step_4 = self._normalized_step(4)
        command_shape = re.search(r"`(gh issue create --[^`]*)`", step_4)
        assert command_shape, "Step 4 no longer shows the gh issue create command shape"
        assert command_shape.group(1).index("--body-file") < command_shape.group(1).index("--title")

    def test_step_6_pauses_per_file_and_quarantines_instead_of_removing(self):
        """The per-file pause and the move-not-remove rule are the two controls
        on the one workflow that deletes unrecoverable gitignored files."""
        step_6 = self._normalized_step(6)
        assert "one `AskUserQuestion` per file, immediately before its move" in step_6
        assert "No batching and no approve-all." in step_6
        assert "no interactive session to answer, means no move" in step_6
        assert "rather than removing it" in step_6
        assert "never overwrite" in step_6
        assert "`rm`" not in step_6, "Step 6 must move to quarantine, never remove"

    def test_marker_recipe_is_not_restated_outside_its_home_skill(self):
        """The write-gate marker's activation belongs to
        ai-instruction-and-memory-files, which marks its recipe do-not-duplicate."""
        assert "marker.sh" not in _skill_body("memory-store-audit")


class TestGlobalInstructionsDescribeMarkerGatesAsContentAddressed:
    """Pin the global instruction file's account of how review gates authorize.

    Every gate recomputes a hash of the state it guards and allows only on an
    exact match against a marker's stored content; the marker file's existence
    authorizes nothing. The distinction is not academic — an agent holding the
    presence model treats any marker on disk as a live gate, so it reads a
    stale marker as permission and a disarmed gate as an unexplained anomaly.

    This file is stowed to ~/.claude/CLAUDE.md and loads in every session, so
    a wrong account here is the most expensive place in the repo to carry one.
    """

    def test_global_instructions_state_gates_match_on_marker_content(self):
        """The §Safety marker bullet must describe content-matching and the deny
        outcome, not mere marker presence."""
        body = _GLOBAL_CLAUDE_MD.read_text()
        assert "Gates match on a marker's **content**" in body
        assert "a hash of the exact state that was reviewed" in body
        # The deny outcome is asserted separately from the match rule: text
        # that says gates match on content but stops short of naming what
        # happens when they don't leaves "stale marker" and "no marker"
        # looking like different outcomes, when both deny.
        assert "the stored hash stops matching and the gate denies" in body
        # Pinned positively as well as negatively. The guard below can only
        # catch a literal revert of the sentence this bullet replaced; any
        # paraphrase of the presence claim slips past it. Requiring the
        # contrast clause to survive leaves a reintroduced presence claim
        # nowhere to sit that does not read as self-contradiction.
        assert "not on the file's presence" in body

    def test_global_instructions_state_markers_outlive_their_session(self):
        """The bullet must say a still-covering marker counts across sessions.

        Separate from the deny rule above because the two are opposite faces
        of content-addressing and regress independently: drop the deny clause
        and a stale marker reads as permission; drop this one and a valid
        marker reads as expired, sending the next session to re-run a review
        the gate would already have released. The handoff skill carries a
        dedicated guard against the same session-keyed-expiry misconception,
        which is what marks it as recurring enough to pin in both files.
        """
        body = _GLOBAL_CLAUDE_MD.read_text()
        assert "keeps counting across sessions" in body

    def test_global_instructions_do_not_claim_gates_check_marker_presence(self):
        """Guard against reintroducing the presence-checked claim.

        It described an earlier gate implementation, which is what makes it
        the likely thing for a future edit to restore — the same reasoning
        that earns the handoff skill's session-expiry claim its own guard.
        A bare "presence" search would false-positive on the corrected text,
        which names presence to deny it, so pin the false predicate instead.
        This catches a literal revert only; the paraphrase class is held out
        by the positive contrast clause asserted above, not by this list.
        """
        body = _GLOBAL_CLAUDE_MD.read_text()
        assert "gate on their presence" not in body
        assert "gates on their presence" not in body


class TestGlobalInstructionsRequireMemoryBodyBeforeActing:
    """Pin the §Safety bullet distinguishing recalling a memory from acting on
    one (GH-429).

    A `MEMORY.md` index line is always loaded; the body it links to loads only
    on recall. Compressing an action-prescribing memory's body into an index
    line routinely drops the body's trigger condition, leaving a bare
    imperative that reads as a standing directive. Without this bullet, a
    session can act on the index line alone -- the exact gap this pins shut.
    """

    def _body(self):
        return _GLOBAL_CLAUDE_MD.read_text()

    def test_requires_reading_the_body_before_acting_on_it(self):
        """Mutation-tested: dropping this clause leaves the routing claim
        intact but removes the obligation to read the body and the
        prohibition on acting when its trigger isn't met -- regressing to a
        bullet that only describes the failure mode, not one that closes it."""
        body = self._body()
        assert "read the body file" in body
        assert "if its trigger condition is not met by what the user actually said this session, do not act" in body

    def test_distinguishes_citation_from_execution(self):
        """Mutation-tested independently of the assertion above -- dropping
        only this clause leaves the read-before-act obligation in place but
        removes the boundary that lets a session cite a memory from its
        index line without triggering an unnecessary body read."""
        assert "Citing a memory may rely on the index line; executing one may not" in self._body()


class TestMemorySkillPreservesActionPrescribingTrigger:
    """Pin the index-discipline bullet requiring an action-prescribing
    memory's index line to carry its body's trigger condition (GH-429).

    The existing compression-diff audit in this skill only fires on a diff
    that removes or shortens lines, so it never catches a newly authored
    index line dropping a trigger it never had a chance to preserve. This
    bullet closes that gap at first-authoring time.
    """

    def _body(self):
        return _skill_file("ai-instruction-and-memory-files").read_text()

    def test_requires_preserving_the_trigger_condition(self):
        """Mutation-tested: dropping this clause leaves the surrounding list
        with a format rule (character cap) but no content rule, so a
        newly authored index line that keeps the imperative and drops the
        guard passes review."""
        assert 'keep the body\'s guard: "run X when asked", never bare "run X"' in self._body()

    def test_requires_a_tiebreak_when_the_guard_will_not_fit(self):
        """Mutation-tested independently of the assertion above -- dropping
        only this clause leaves the trigger-preservation rule in direct
        conflict with the neighboring ≤150-character format bullet with no
        stated resolution, and the likely resolution under that pressure is
        silently dropping the guard -- the original defect this bullet
        exists to close (staff-sdet finding, GH-429 code review)."""
        assert "if the guard will not fit the character cap, the entry is not indexable as action-prescribing" in self._body()
