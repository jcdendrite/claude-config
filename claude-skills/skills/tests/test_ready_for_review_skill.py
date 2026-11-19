"""ready-for-review's steps 1-4, 6 and 7, its fix loop, CI watch, completion marker and session-id redaction."""
from __future__ import annotations

import re

import pytest
from _skill_md_helpers import (
    _agent_body,
    _assert_pinned_clause_right_bounded,
    _heading_section_text,
    _raw_heading_section_text,
    _section_between,
    _skill_file,
)

# pyproject.toml's pythonpath also puts claude/.claude/tests on the import
# path, where these shared test helpers live.
from helpers import SCRIPTS_DIR

_READY_FOR_REVIEW_STEP3_HEADING = "## 3. Code review (halt on findings)"


def test_ready_for_review_step3_never_produces_a_staged_diff() -> None:
    """Structural regression for SCOPE_RULE:ready-for-review-cumulative-unnarrowed's
    premise. Step 3 must always dispatch via `pr-diff-against-base.sh`. It must
    never contain a `git diff --cached` invocation, so the diff it hands
    `/code-review` can never satisfy the narrowing precondition's staged-diff
    half. A fixture-based structural check, not a live model call.
    """
    skill_md_path = _skill_file("ready-for-review")
    lines = skill_md_path.read_text().splitlines(keepends=True)
    start_idx, end_idx = _section_between(lines, _READY_FOR_REVIEW_STEP3_HEADING, skill_md_path)
    section_text = "".join(lines[start_idx:end_idx])
    assert "pr-diff-against-base.sh --record" in section_text, (
        f"{skill_md_path}: Step 3 no longer dispatches via pr-diff-against-base.sh --record"
    )
    assert "git diff --cached" not in section_text, (
        f"{skill_md_path}: Step 3 must never produce a staged-diff (git diff "
        "--cached) review basis — that's the commit-gate pass's job, not the "
        "cumulative sweep's"
    )


def test_ready_for_review_step3_discards_the_diff_stdout() -> None:
    """GH-1296: step 3 hands the diff on by its `DIFF_FILE:` path, so printing
    it to stdout only adds its bytes to the session's context. The command
    must end at `> /dev/null` with no further redirect, because an appended
    `2>&1` would discard the `DIFF_EMPTY:` and `DIFF_FILE:` stderr signals
    step 3 acts on. Step 3 must contain no other invocation line, since a
    bare second one would print the diff again.
    """
    skill_md_path = _skill_file("ready-for-review")
    lines = skill_md_path.read_text().splitlines(keepends=True)
    start_idx, end_idx = _section_between(lines, _READY_FOR_REVIEW_STEP3_HEADING, skill_md_path)
    section_text = "".join(lines[start_idx:end_idx])

    discarding_command = "~/.claude/scripts/pr-diff-against-base.sh --record --diff-file > /dev/null"
    invocation_lines = [
        line
        for line in section_text.splitlines()
        if line.startswith("~/.claude/scripts/pr-diff-against-base.sh")
    ]
    assert invocation_lines == [discarding_command], (
        f"{skill_md_path}: step 3's only pr-diff-against-base.sh invocation line must be "
        f"exactly {discarding_command!r}, ending at '> /dev/null' with no further "
        "redirect, so the diff stays out of the session and stderr still reaches it; "
        f"found {invocation_lines!r}"
    )
    assert "Read the diff only from the file at that path" in section_text, (
        f"{skill_md_path}: step 3 no longer tells the session to read the diff "
        "from the DIFF_FILE: path now that stdout is discarded"
    )


_READY_FOR_REVIEW_STEP4_HEADING = "## 4. Skill-procedural-fidelity review (halt on findings)"


def test_ready_for_review_step4_hands_the_reviewer_a_diff_file_path() -> None:
    """The step-4 diff-file handoff contract: anchored instruction-phrase
    presence, the mechanism `_MARKER_TRIPLE_SITES` already implements, not a
    full-clause anchor pin. Four literals catch deletion, rename, command
    drop, and halt-clause removal independently. Step 4's reuse-vs-recompute
    split is pinned by its own text in Step 4 directly. The two
    reuse-vs-recompute literals below are anchored-phrase-presence checks.
    An ordering check further asserts the reuse literal precedes the
    cache-hit literal, catching a branch-assignment swap between the two
    clauses. This is not a full-clause anchor pin. The trailing
    `reviewer_body` assertions guard `skill-fidelity-reviewer`'s own
    Input contract wording, which Step 4's prose points a reader at.
    """
    skill_md_path = _skill_file("ready-for-review")
    lines = skill_md_path.read_text().splitlines(keepends=True)
    step3_start, step3_end = _section_between(lines, _READY_FOR_REVIEW_STEP3_HEADING, skill_md_path)
    step3_text = "".join(lines[step3_start:step3_end])
    step4_start, step4_end = _section_between(lines, _READY_FOR_REVIEW_STEP4_HEADING, skill_md_path)
    step4_text = "".join(lines[step4_start:step4_end])

    for literal in (
        "~/.claude/scripts/pr-diff-against-base.sh --diff-file > /dev/null",
        "DIFF_FILE:",
        "as its own Bash call",
        "skill/script version mismatch",
    ):
        assert literal in step4_text, (
            f"{skill_md_path}: step 4 no longer carries the exact literal {literal!r}"
        )

    assert "pr-diff-against-base.sh --record --diff-file" in step3_text, (
        f"{skill_md_path}: step 3 must record its own --diff-file artifact "
        "alongside --record"
    )
    assert "If step 3 ran this turn" in step4_text, (
        f"{skill_md_path}: step 4 no longer carries the exact literal "
        "'If step 3 ran this turn'"
    )
    for literal in (
        "reuse the path its `DIFF_FILE:` line already named",
        "If step 3 was skipped via the cache-hit branch",
    ):
        assert literal in step4_text, (
            f"{skill_md_path}: step 4 no longer carries the exact literal {literal!r}"
        )
    assert step4_text.index("reuse the path its `DIFF_FILE:` line already named") < step4_text.index(
        "If step 3 was skipped via the cache-hit branch"
    ), (
        f"{skill_md_path}: the reuse clause must precede the cache-hit clause "
        "it governs, or the two branches' assigned actions may have been swapped"
    )

    pr_diff_script_source = (SCRIPTS_DIR / "pr-diff-against-base.sh").read_text()
    assert "DIFF_FILE:" in pr_diff_script_source

    reviewer_body = _agent_body("skill-fidelity-reviewer")
    assert "a path to a diff file" in reviewer_body
    # Also pinned independently in claude/.claude/hooks/tests/test_agent_roster.py's
    # DIFF_INPUT_CONTRACT_SHARED_SENTENCES — update both on a wording change.
    assert "page onward with `offset` until one doesn't" in reviewer_body


# Step 1's stack-base bullet runs the key command only when both operands hold
# the character set _lib_configured_stack_base accepts and neither starts with
# `-`, because the agent composes the quoted command before the helper can
# validate anything. It then halts unless the recipe confirms the base.
_PINNED_STEP1_STACK_BASE_CHARSET_CLAUSE = (
    "Run the command only when both `<branch>` and `<stack-base>` hold just "
    "A-Z, a-z, 0-9, `.`, `_`, `/` and `-`, and neither starts with `-`. "
    "Otherwise, ask the engineer."
)
_PINNED_STEP1_STACK_BASE_HALT_CLAUSE = (
    "Halt unless the next bullet's recipe prints `Base branch: <stack-base>`."
)
# The provenance clause keeps the key set only from the engineer's own words.
_PINNED_STEP1_STACK_BASE_TRIGGER_CLAUSE = (
    "When the engineer has said this branch is stacked on another branch, run "
    "`git config 'branch.<branch>.gh-merge-base' '<stack-base>'` before the next bullet."
)
_PINNED_STEP1_STACK_BASE_PROVENANCE_CLAUSE = (
    "Only `<stack-base>` comes from the engineer's words, with no `origin/` prefix "
    "and never inferred."
)


def test_ready_for_review_names_the_stack_base_key_and_the_base_branch_line() -> None:
    """Step 1 sets the `gh-merge-base` key from the engineer's words, and step
    8 passes `--base` from the `Base branch:` line that
    branch-divergence-status.sh prints for a stack base. Step 1 also runs the
    key command only when both operands hold the helper's character set and
    neither starts with `-`, and halts unless the recipe prints
    `Base branch: <stack-base>`. It also pins the provenance clause: the key
    is set only when the engineer has said the branch is stacked, and only
    `<stack-base>` comes from their words, with no `origin/` prefix and never
    inferred. The script-source assertion is a
    wiring-presence tripwire tying the skill's literal to the script's;
    test_branch_divergence_status.py runs the script and asserts the printed
    line."""
    skill_md_path = _skill_file("ready-for-review")
    lines = skill_md_path.read_text().splitlines(keepends=True)
    step1_start, step1_end = _section_between(lines, _READY_FOR_REVIEW_STEP1_HEADING, skill_md_path)
    step1_text = "".join(lines[step1_start:step1_end])
    step8_start, step8_end = _section_between(
        lines, "## 8. Create PR if missing (skip if PR already exists)", skill_md_path
    )
    step8_text = "".join(lines[step8_start:step8_end])

    assert "gh-merge-base" in step1_text, f"{skill_md_path}: step 1 no longer names the gh-merge-base key"
    step1_collapsed = " ".join(step1_text.split())
    assert " ".join(_PINNED_STEP1_STACK_BASE_TRIGGER_CLAUSE.split()) in step1_collapsed, (
        f"{skill_md_path}: step 1 no longer sets the key only when the engineer has said the branch is stacked"
    )
    assert " ".join(_PINNED_STEP1_STACK_BASE_PROVENANCE_CLAUSE.split()) in step1_collapsed, (
        f"{skill_md_path}: step 1 no longer takes only `<stack-base>` from the engineer's words, "
        "with no `origin/` prefix and never inferred"
    )
    assert " ".join(_PINNED_STEP1_STACK_BASE_CHARSET_CLAUSE.split()) in step1_collapsed, (
        f"{skill_md_path}: step 1 no longer applies the character set and the leading-`-` reject to both "
        "`<branch>` and `<stack-base>` before running the key command"
    )
    assert " ".join(_PINNED_STEP1_STACK_BASE_HALT_CLAUSE.split()) in step1_collapsed, (
        f"{skill_md_path}: step 1 no longer halts unless the recipe prints `Base branch: <stack-base>`"
    )
    assert '--base "<name>"' in step8_text, f"{skill_md_path}: step 8 no longer passes --base \"<name>\" for a stack base"
    assert "Base branch:" in step8_text, f"{skill_md_path}: step 8 no longer keys --base on the Base branch: line"
    divergence_script_source = (SCRIPTS_DIR / "branch-divergence-status.sh").read_text()
    assert 'echo "Base branch: $BASE_BRANCH"' in divergence_script_source


def test_ready_for_review_step2_takes_the_fallback_git_diff_base_from_the_recipe_line() -> None:
    """Step 2's pre-existing-failure check runs `git diff origin/<base>` with a base
    taken from the `Default branch:` or `Base branch:` line step 1's recipe printed,
    never from the `baseRefName` that `gh pr view` returned. Only the
    `Base branch:` name has passed the script's name-shape check (see the
    branch-divergence-status.sh header). The `Default branch:` name comes from
    origin/HEAD and is not shape-checked. The rebase sentence after it names the
    `<base>` step 1's recipe measured."""
    step2_text = " ".join(
        _raw_heading_section_text(_skill_file("ready-for-review"), "## 2. Verification (halt on fail)").split()
    )
    assert (
        "`<base>` is the branch on the `Default branch:` or `Base branch:` line step 1's recipe printed. "
        "Rebase once `origin/<base>` is green."
    ) in step2_text
    assert "baseRefName" not in step2_text


def test_ready_for_review_step_4_has_the_rerun_that_recovers_omitted_plan_review_rows() -> None:
    """Step 4 must name the rerun that recovers the plan-review rows the omit flag drops."""
    step_4_text = _heading_section_text(
        _skill_file("ready-for-review"), "## 4. Skill-procedural-fidelity review (halt on findings)"
    )
    for literal in ("without `--omit-plan-review-spawns`", "`round=plan-review`"):
        assert literal in step_4_text, (
            f"ready-for-review step 4 no longer carries {literal!r}"
        )


_READY_FOR_REVIEW_STEP1_HEADING = "## 1. Preconditions (halt on fail)"

# The condition→action bullets from ready-for-review/SKILL.md's step-1
# context-budget cluster, mirroring _PINNED_HANDOFF_WARRANT_CHECK_CLAUSES:
# the cluster is one fact per sibling bullet rather than one run-on bullet,
# so each is pinned and right-bounded independently — pinning the bold
# label alone would still pass if a future edit added a real halt
# condition nearby while leaving the label untouched.
_PINNED_CONTEXT_BUDGET_CLAUSES: dict[str, str] = {
    "defers_via_handoff": (
        "**Context budget (defers, not a warning).** Run "
        "`~/.claude/hooks/nudge-handoff-near-context-cap.sh --check`. On "
        '`"status":"ok"` with `over_threshold` or `already_fired` true, report '
        "`estimate` and `threshold`, then invoke `/handoff` instead of "
        "running steps 2–9 in this session. Also name `nudge_disabled` "
        "when true — the measurement still holds even though no nudge "
        "fires on its own."
    ),
    "deferring_is_cheap": (
        "Deferring here is cheap: steps 3 and 4 each dispatch a full "
        "reviewer pass and step 5 runs `pr-description`'s own checks, so "
        "what remains costs what the diff costs, not what the step "
        "counter says. Steps 2–9 take their inputs from the repository — "
        "the diff, `gh pr view`, `skill-fidelity-report.sh` — so a fresh "
        "session rebuilds almost nothing this one holds."
    ),
    "marker_deactivation_ordering": (
        "Deactivate only once `/handoff`'s own \"Verify the handoff file "
        "with Bash\" step confirms the write succeeded, via "
        "`~/.claude/scripts/marker.sh deactivate ready-for-review`. "
        "Deactivating earlier risks a session with no active marker, no "
        "completion marker, and no handoff file if it fails "
        "mid-`/handoff`; a fresh session then restarts this gate from "
        "step 0. If `/handoff` itself declines to write (e.g. its own "
        "warrant check reports `cannot-resolve`, or states another "
        "reason it won't write), that is itself a halt — report and "
        "stop, do not deactivate the `ready-for-review` marker."
    ),
    "engineer_override": (
        "The one exception is an engineer decision, not the agent's "
        "judgment: an explicit, unambiguous instruction to finish in this "
        'session overrides the deferral, but a vague "let\'s wrap up soon" '
        "does not."
    ),
    "continue_silently_fallback": (
        'Continue silently in every other case — `"status":"ok"` under '
        "threshold, or any other status including "
        "`cannot-resolve`/`schema-drift`. This gate's outcome never "
        "depends on the tool's own success."
    ),
    "session_id_redaction": (
        "Do not quote the raw `session_id` into prose that may reach the PR body."
    ),
    "handoff_cross_reference": (
        'See `handoff/SKILL.md` § "Before writing: is a handoff warranted?" '
        "for the remaining fields."
    ),
}


class TestReadyForReviewContextBudgetDefers:
    """Pin that ready-for-review's step-1 context-budget bullet defers the
    whole gate to a fresh session via `/handoff` on `over_threshold` or
    `already_fired`, keyed on the same `--check` fields `plan-it` Step 7
    already uses.
    """

    @pytest.mark.parametrize("branch", sorted(_PINNED_CONTEXT_BUDGET_CLAUSES))
    def test_step1_context_budget_bullet_matches_live_text(self, branch: str) -> None:
        raw_section = _raw_heading_section_text(
            _skill_file("ready-for-review"), _READY_FOR_REVIEW_STEP1_HEADING
        )
        pinned_text = " ".join(_PINNED_CONTEXT_BUDGET_CLAUSES[branch].split())
        _assert_pinned_clause_right_bounded(
            pinned_text,
            raw_section,
            context=f"ready-for-review/SKILL.md: step-1 context-budget {branch!r} bullet no longer matches its pinned clause.",
        )

    def test_context_budget_clauses_appear_in_pinned_order(self) -> None:
        """Each clause is pinned independently via right-bounded search,
        which does not pin their relative ordering within the step-1
        section -- this test catches a reorder the per-clause test alone
        would miss. Indices must be strictly increasing in
        _PINNED_CONTEXT_BUDGET_CLAUSES's own dict-insertion order."""
        raw_section = _heading_section_text(_skill_file("ready-for-review"), _READY_FOR_REVIEW_STEP1_HEADING)
        indices = []
        for branch, clause in _PINNED_CONTEXT_BUDGET_CLAUSES.items():
            pattern = r"\s+".join(re.escape(word) for word in clause.split())
            match = re.search(pattern, raw_section)
            assert match is not None, (
                f"ready-for-review/SKILL.md: step-1 context-budget {branch!r} clause not found in "
                f"the step-1 section at all.\n  pinned:  {clause!r}\n  section: {raw_section!r}"
            )
            indices.append(match.start())
        assert all(earlier < later for earlier, later in zip(indices, indices[1:], strict=False)), (
            "ready-for-review/SKILL.md: step-1 context-budget clauses no longer appear in "
            f"_PINNED_CONTEXT_BUDGET_CLAUSES's own order (found at indices {indices})."
        )


_READY_FOR_REVIEW_STEP8_HEADING = "## 8. Create PR if missing (skip if PR already exists)"

# ready-for-review clauses the session follows itself; pr-create-preflight.py's tests cover what the script computes.
_PINNED_READY_FOR_REVIEW_PREFLIGHT_CLAUSES: dict[str, tuple[str, str]] = {
    "step1_runs_ticket_mode_and_reads_its_output": (
        _READY_FOR_REVIEW_STEP1_HEADING,
        "Current branch is not the default branch (`main` / `master` / `develop`). Also run "
        "`python3 ~/.claude/scripts/pr-create-preflight.py ticket` here for step 5 to consume, and again "
        "before step 5 if its value is no longer in context. When it exits 0 and prints a `TICKET_ID` line, "
        "that line's value is the `<TICKET-ID>`, and `none` means there is none. Otherwise halt with every "
        "`REASON` line, or the command's error if it printed none. When that error says the script file does "
        "not exist, also tell the engineer to re-run `./install.sh` in their claude-config checkout from a "
        "separate terminal, which links it.",
    ),
    "step8_runs_create_mode_then_chains_it_ahead_of_pr_creation": (
        _READY_FOR_REVIEW_STEP8_HEADING,
        "The body is step 5's file; this step composes none of its own. Run "
        "`python3 ~/.claude/scripts/pr-create-preflight.py create '<path>'`, writing step 5's reported path "
        "as one single-quoted literal word. It derives the title from the branch and checks the path and the "
        "file it names. Unless it exits 0 and prints a `TITLE` line, halt with every `REASON` line, or the "
        "command's error if it printed none, and open no PR. Otherwise open the PR with "
        "`python3 ~/.claude/scripts/pr-create-preflight.py create '<path>' && gh pr create --title \"<title>\" "
        "--body-file <path>`, as one Bash call. When step 6 printed `Base branch: <name>`, add "
        "`--base \"<name>\"` to the `gh pr create` half. Write the `TITLE` line's value, the `--base` name "
        "when one is added, and the same path as **literal text** — not a `$VAR` or a command substitution "
        "holding any of them. `gh pr create --body-file` is scanned by a redaction "
        "gate that resolves the flag's argument statically; a shell variable is opaque to that scan, so it "
        "fails closed and refuses the call. Every attempt runs that whole call, never its `gh` half alone, "
        "including a retry after a denial, a failure or a rewrite of the body file. A stop it prints halts "
        "as above.",
    ),
}


class TestReadyForReviewPreflightScriptClauses:
    """Pin the ready-for-review clauses that run pr-create-preflight.py and read its output.

    The step-8 pin keeps the preflight command chained ahead of `gh pr create`, so a failed body
    check never reaches the PR-creation command. It also keeps the instruction to re-run the whole
    call on every attempt. Dropping either reopens the empty-body hole.

    The step-8 pin also keeps the `--base "<name>"` attachment to the `gh pr create` half, keyed on
    step 6's `Base branch:` line, so the base appears in the command text the redaction hook scans.
    The step-8 pin also keeps the `TITLE` value, the `--base` name and the path as literal command
    text, not a variable or command substitution.

    The step-1 `ticket` pin keeps `<TICKET-ID>` read from the script's `TICKET_ID` line, with a halt
    on its `REASON` lines.
    """

    @pytest.mark.parametrize("clause_key", sorted(_PINNED_READY_FOR_REVIEW_PREFLIGHT_CLAUSES))
    def test_pinned_preflight_clause_matches_live_text(self, clause_key: str) -> None:
        heading, clause = _PINNED_READY_FOR_REVIEW_PREFLIGHT_CLAUSES[clause_key]
        raw_section = _raw_heading_section_text(_skill_file("ready-for-review"), heading)
        _assert_pinned_clause_right_bounded(
            " ".join(clause.split()),
            raw_section,
            context=f"ready-for-review/SKILL.md: the {clause_key!r} clause no longer matches its pinned clause.",
        )


_READY_FOR_REVIEW_OVERVIEW_HEADING = "# Ready-for-review gate"

# The Overview's cross-reference binding a halt on step 2, 3, or 4 to a
# context-budget re-check that runs only after that round's fix commit has
# landed, from ready-for-review/SKILL.md. Step 6 is deliberately excluded
# (pushing commits is cheap enough to finish before any deferral
# consideration).
_PINNED_HALT_DEFERS_CLAUSE = (
    "A halt on step 2, 3, or 4 triggers the fix loop above first; only "
    "once that round's fix commit has landed does a context-budget re-check "
    "run, and only then does an over-threshold/already-fired result "
    "route to step 1's deferral. A halt on step 6 stays outside this "
    "routing — pushing the commits is cheap enough to finish before any "
    "deferral consideration."
)


class TestReadyForReviewHaltRoutesToContextBudgetDeferral:
    """Pin ready-for-review's Overview sentence routing a halt on step 2,
    3, or 4 to a post-commit context-budget re-check that defers to step 1
    when it reports `over_threshold` or `already_fired`, so narrowing the
    halt-step list, dropping the deferral routing, or reordering it ahead
    of the fix commit fails this test instead of drifting silently.
    """

    def test_overview_names_halt_steps_and_deferral_routing(self) -> None:
        raw_section = _raw_heading_section_text(
            _skill_file("ready-for-review"), _READY_FOR_REVIEW_OVERVIEW_HEADING
        )
        pinned_text = " ".join(_PINNED_HALT_DEFERS_CLAUSE.split())
        _assert_pinned_clause_right_bounded(
            pinned_text,
            raw_section,
            context="ready-for-review/SKILL.md: Overview's halt-deferral sentence no longer matches.",
        )
        # The halt clause says "the fix loop above", so the fix-loop paragraph
        # must precede it.
        # _assert_pinned_clause_right_bounded returns no match position, so the
        # start offsets are rebuilt here with the same whitespace-tolerant pattern.
        fix_loop_pattern = r"\s+".join(re.escape(word) for word in _PINNED_FIX_LOOP_CLAUSE.split())
        halt_pattern = r"\s+".join(re.escape(word) for word in pinned_text.split())
        fix_loop_match = re.search(fix_loop_pattern, raw_section)
        halt_match = re.search(halt_pattern, raw_section)
        assert fix_loop_match is not None and halt_match is not None, (
            "ready-for-review/SKILL.md: Overview's fix-loop or halt-deferral clause not found "
            "for the ordering check; if the fix-loop clause drifted, see "
            "TestReadyForReviewFixLoopRule."
        )
        assert fix_loop_match.start() < halt_match.start(), (
            "ready-for-review/SKILL.md: Overview's fix-loop paragraph must precede the "
            "halt-deferral paragraph that refers to it as 'the fix loop above'."
        )


# The Overview's fix-loop rule: every fix produced by step 2, 3, or 4
# re-enters at step 2, and step 4 never re-runs on its own output.
_PINNED_FIX_LOOP_CLAUSE = (
    "After a fix produced by step 2, 3, or 4, return to step 2 and continue "
    "in order. Step 3 then re-reviews the fixed cumulative diff in full, "
    "because its cache marker misses on the changed bytes. Step 4 does not "
    "re-run on its own output."
)


class TestReadyForReviewFixLoopRule:
    """Pin the Overview's fix-loop rule, so a future edit that lets the fix
    commit's own staged-diff review stand in for a cumulative re-run fails
    this test instead of drifting silently.
    """

    def test_overview_fix_loop_clause_matches_live_text(self) -> None:
        raw_section = _raw_heading_section_text(
            _skill_file("ready-for-review"), _READY_FOR_REVIEW_OVERVIEW_HEADING
        )
        pinned_text = " ".join(_PINNED_FIX_LOOP_CLAUSE.split())
        _assert_pinned_clause_right_bounded(
            pinned_text,
            raw_section,
            context="ready-for-review/SKILL.md: Overview's fix-loop clause no longer matches.",
        )


# Pinned as one contiguous clause so a sentence inserted between them fails.
_PINNED_STEP4_RERUN_AND_RE_PLAN_ADDRESS_CLAUSE = (
    "A step-4 spawn the gate denied reviewed nothing, so step 4 runs again "
    "after the fix loop. "
    "A re-plan verdict is an ADDRESS row in the disposition record of the "
    "step-3 pass under review or whose fix is under review, or a step-4 "
    "finding, and the fix loop takes it like any other finding."
)

# Step 3's clean/dirty definition.
_PINNED_STEP3_CLEAN_DIRTY_CLAUSE = (
    "The pass is clean when every reviewer its *Spawn decisions:* line lists "
    "as spawned has returned and every row is resolved as "
    '`code-review/SKILL.md` § "Step — Record review completion" counts it (a '
    "`none` row counts as resolved), and dirty otherwise. No record grants a "
    "review skip and the `cumulative-review` marker stays the sole "
    "authorization; later reviews read it only as context."
)

# Step 3's loop-back sentence.
_PINNED_STEP3_LOOP_BACK_CLAUSE = (
    "Its fix commit goes through the standard staged-diff `/code-review` + "
    "marker gate, and the Overview's fix-loop rule then brings the loop back "
    "through step 2 to a full pass of this step over the fixed bytes."
)

# Step 3 must re-review a fix's own output in full. This phrase is the tail of
# the sentence that forbids that re-review, so its presence in step 3 is the
# regression.
_STEP3_SKIP_REREVIEW_PHRASE = "on its own output (loop risk)"


class TestReadyForReviewRunExitAndStep3Pins:
    """Pin the Overview's run-exit sentences and step 3's clean/dirty and
    loop-back sentences verbatim, so dropping one fails a test instead of
    drifting silently.
    """

    def test_overview_denied_step4_rerun_and_re_plan_address_clause_match_live_text(
        self,
    ) -> None:
        raw_section = _raw_heading_section_text(
            _skill_file("ready-for-review"), _READY_FOR_REVIEW_OVERVIEW_HEADING
        )
        _assert_pinned_clause_right_bounded(
            " ".join(_PINNED_STEP4_RERUN_AND_RE_PLAN_ADDRESS_CLAUSE.split()),
            raw_section,
            context=(
                "ready-for-review/SKILL.md: Overview's denied-step-4 re-run and "
                "re-plan ADDRESS-finding sentences no longer match."
            ),
        )

    @pytest.mark.parametrize(
        ("pinned_clause", "site"),
        [
            pytest.param(
                _PINNED_STEP3_CLEAN_DIRTY_CLAUSE,
                "step 3's clean/dirty definition",
                id="clean-dirty-definition",
            ),
            pytest.param(
                _PINNED_STEP3_LOOP_BACK_CLAUSE,
                "step 3's fix loop-back sentence",
                id="loop-back",
            ),
        ],
    )
    def test_step3_clause_matches_live_text(self, pinned_clause: str, site: str) -> None:
        raw_section = _raw_heading_section_text(
            _skill_file("ready-for-review"), _READY_FOR_REVIEW_STEP3_HEADING
        )
        pinned_text = " ".join(pinned_clause.split())
        _assert_pinned_clause_right_bounded(
            pinned_text,
            raw_section,
            context=f"ready-for-review/SKILL.md: {site} no longer matches.",
        )

    def test_step3_does_not_forbid_rereview_of_a_fixs_own_output(self) -> None:
        raw_section = _raw_heading_section_text(
            _skill_file("ready-for-review"), _READY_FOR_REVIEW_STEP3_HEADING
        )
        assert _STEP3_SKIP_REREVIEW_PHRASE not in " ".join(raw_section.split()), (
            "ready-for-review/SKILL.md: step 3 forbids re-reviewing a fix's own "
            f"output ({_STEP3_SKIP_REREVIEW_PHRASE!r}); the fix loop requires "
            "that re-review."
        )


_READY_FOR_REVIEW_CI_WATCH_HEADING = "## CI watch (out-of-band)"

# CI watch's "Land the fix" item routes a CI fix through the Overview's
# fix-loop rule instead of restating a separate push-then-loop mechanic, so
# a CI fix gets the same full cumulative re-review as a local-failure fix.
_PINNED_CI_LAND_THE_FIX_CLAUSE = (
    "**Land the fix.** Step 9 removed this session's active marker and "
    "`require-ready-for-review.sh` denies a push without one, so re-run "
    "step 0's `marker.sh activate` command, then treat the fix as a "
    "step-2 failure's fix under the Overview's fix-loop rule, which "
    "carries it through step 9."
)


class TestReadyForReviewCiWatchLandsFixUnderFixLoopRule:
    """Pin the CI watch's "Land the fix" item, which routes the fix through
    the Overview's fix-loop rule. That rule gives a CI fix the same fresh
    cumulative review as a local-failure fix.
    """

    def test_land_the_fix_clause_matches_live_text(self) -> None:
        raw_section = _raw_heading_section_text(
            _skill_file("ready-for-review"), _READY_FOR_REVIEW_CI_WATCH_HEADING
        )
        pinned_text = " ".join(_PINNED_CI_LAND_THE_FIX_CLAUSE.split())
        _assert_pinned_clause_right_bounded(
            pinned_text,
            raw_section,
            context="ready-for-review/SKILL.md: CI watch's 'Land the fix' clause no longer matches.",
        )


_READY_FOR_REVIEW_STEP6_HEADING = "## 6. Final hygiene recheck (halt on fail)"

# Step 6's behind-base bullet: no sync on a clean trial merge, a conflict returns
# the gate to step 1, and a recipe failure is re-run once in place, then reported.
_PINNED_STEP6_BEHIND_BASE_CLAUSE = (
    "Branch has no merge conflict with the base branch. Re-run the divergence "
    "detection recipe (`git-feature-branch-sync/SKILL.md` § \"Detecting "
    "divergence\"). Behind with `Trial merge: CLEAN` passes: report the "
    "behind-count in the Completion summary. Do not sync here: a sync changes "
    "the tree, so step 2 would re-run. Behind with `Trial merge: CONFLICT` "
    "halts this step: return to step 1, whose in-sync precondition runs the "
    "sync. A recipe failure (non-zero exit) halts this step: re-run the recipe "
    "here once rather than restarting the gate, whose step 1 would sync; if it "
    "fails again, report its stderr cause to the engineer and stop."
)

# Step 1's in-sync precondition: the bullet step 6 hands a conflict back to.
# The pin also holds the sentence requiring step 7's completion marker to record
# the post-resync HEAD SHA that the push-gate hook checks. No other test pins
# that sentence, so trimming it needs a deliberate update here.
_PINNED_STEP1_IN_SYNC_BULLET = (
    "**Branch is in sync with `origin/<base>`.** Run the canonical detection "
    "recipe (see `git-feature-branch-sync/SKILL.md` § \"Detecting "
    "divergence\"). If behind > 0, invoke `/git-feature-branch-sync`, then "
    "re-run step 2 against the synced tree; step 7's completion marker must "
    "record the post-resync HEAD SHA so it matches what the push-gate hook "
    "checks."
)


class TestReadyForReviewStep6BehindBaseRule:
    """Pin step 6's behind-base bullet as one whole clause, deliberately, and
    check that no other step-6 text says "sync". That second check is a
    lexical tripwire on the skill's own vocabulary (the "sync" stem, which
    also matches the sync skill's name), not proof that no step-6 text moves
    the branch. Fails when
    step 6 syncs on a clean trial merge, stops returning a conflict to step 1,
    or stops halting on a recipe failure. Step 1's in-sync precondition, which
    runs the sync on that hand-off, is pinned separately, so both ends of the
    step 6 to step 1 hand-off are checked.

    A sync at step 6 changes the tree and forces step 2 to re-run; see
    docs/design-decisions/ready-for-review-base-drift.md.
    """

    def test_step6_behind_base_bullet_is_the_only_sync_rule(self) -> None:
        skill_md_path = _skill_file("ready-for-review")
        pinned_text = " ".join(_PINNED_STEP6_BEHIND_BASE_CLAUSE.split())

        # The negative check depends on the positive match, so both live here.
        _assert_pinned_clause_right_bounded(
            pinned_text,
            _raw_heading_section_text(skill_md_path, _READY_FOR_REVIEW_STEP6_HEADING),
            context=(
                "ready-for-review/SKILL.md: step 6's behind-base bullet has drifted from its pinned clause "
                "(no sync on a clean trial merge, a conflict returns to step 1, a recipe failure is re-run "
                "once in place then reported)"
            ),
        )

        section_text = _heading_section_text(skill_md_path, _READY_FOR_REVIEW_STEP6_HEADING)
        remainder = section_text.replace(pinned_text, "", 1)
        # Over-matches benign words like "synchronously" and "in sync" (reword step 6 if it trips).
        # Does not match unnamed branch-moving wording such as merge, rebase, or "Resync".
        stray_sync = re.search(r"\bsync", remainder, re.IGNORECASE)
        assert stray_sync is None, (
            "ready-for-review/SKILL.md: step 6 says "
            f"{stray_sync.group(0)!r} outside its behind-base bullet: "
            f"{remainder[max(stray_sync.start() - 30, 0) : stray_sync.end() + 30]!r}"
        )

    def test_step1_in_sync_bullet_is_the_sync_the_step6_conflict_handoff_reaches(self) -> None:
        pinned_text = " ".join(_PINNED_STEP1_IN_SYNC_BULLET.split())

        _assert_pinned_clause_right_bounded(
            pinned_text,
            _raw_heading_section_text(_skill_file("ready-for-review"), _READY_FOR_REVIEW_STEP1_HEADING),
            context=(
                "ready-for-review/SKILL.md: step 1's in-sync precondition has drifted from its pinned clause "
                "(the pin also holds the rule that step 7's completion marker records the post-resync HEAD SHA)"
            ),
        )


_READY_FOR_REVIEW_STEP7_HEADING = "## 7. Record gate completion"

# Step 7's "Do NOT write the completion marker if" bullets, from
# ready-for-review/SKILL.md: the halt-on-fail step list, the closed list of
# outcomes that count as complete without full execution (whose last sentence
# is the missing-body-file case), and the dispatched-subagent rule.
_PINNED_COMPLETION_MARKER_HALT_STEP_LIST_CLAUSE = (
    "Any halt-on-fail step (1, 2, 3, 4, 6) left a finding unresolved this "
    "session (a DEFERred or SETTLED finding counts as resolved)."
)
_PINNED_COMPLETION_MARKER_CLOSED_COMPLETE_OUTCOMES_CLAUSE = (
    "Any of steps 1–6 did not run, or ended in an outcome its own text does "
    "not define as complete. Only these outcomes count as complete without "
    "full execution: step 2's scope-exception skip, step 2's skip of "
    "undefined commands, step 2's reported cache hit, step 3's reported "
    "cache hit, step 4's empty-list no-op, and step 5's already-in-sync "
    "report (see Completion)."
)
_PINNED_COMPLETION_MARKER_NO_BODY_FILE_CLAUSE = (
    "With no PR open, step 5 is also incomplete unless it reported a "
    "`BODY_FILE:` path whose file exists and is non-empty; that file check "
    "applies only to a reported path when no PR is open."
)
_PINNED_COMPLETION_MARKER_SUBAGENT_CLAUSE = (
    "A dispatched subagent never writes this marker; it reports to its caller."
)


class TestReadyForReviewCompletionMarkerHaltStepList:
    """Pin step 7's "Do NOT write the completion marker if" bullets against
    the live SKILL.md text: the halt-on-fail step numbers, the closed list of
    outcomes that count as complete, the no-body-file case, and the
    dispatched-subagent rule.
    """

    @pytest.mark.parametrize(
        "pinned_clause",
        [
            _PINNED_COMPLETION_MARKER_HALT_STEP_LIST_CLAUSE,
            # The closed-list bullet ends with the no-body-file sentence, so the
            # whole bullet is pinned as one string.
            _PINNED_COMPLETION_MARKER_CLOSED_COMPLETE_OUTCOMES_CLAUSE
            + " "
            + _PINNED_COMPLETION_MARKER_NO_BODY_FILE_CLAUSE,
            _PINNED_COMPLETION_MARKER_NO_BODY_FILE_CLAUSE,
            _PINNED_COMPLETION_MARKER_SUBAGENT_CLAUSE,
        ],
        ids=[
            "halt_step_list",
            "closed_complete_outcomes",
            "no_body_file",
            "subagent_never_writes",
        ],
    )
    def test_completion_marker_do_not_write_bullet_matches_live_text(
        self, pinned_clause: str
    ) -> None:
        raw_section = _raw_heading_section_text(
            _skill_file("ready-for-review"), _READY_FOR_REVIEW_STEP7_HEADING
        )
        pinned_text = " ".join(pinned_clause.split())
        _assert_pinned_clause_right_bounded(
            pinned_text,
            raw_section,
            context="ready-for-review/SKILL.md: a step 7 do-not-write bullet does not match its pinned clause.",
        )


_READY_FOR_REVIEW_COMPLETION_HEADING = "## Completion"


def test_session_id_redaction_present_at_every_check_output_site() -> None:
    """Each `--check`-output site carries its own copy of the redaction
    sentence — a safety rule that only fires if the reader follows a
    citation doesn't fire.

    Bound each ready-for-review assertion to its own heading section
    (step 1, Completion) rather than a whole-file count — a whole-file count
    stays unchanged if both copies relocate into the same section.
    """
    assert (
        "Do not quote the raw `session_id` into prose that may reach a commit, "
        "PR body, or handoff file."
        in _skill_file("handoff").read_text()
    )
    assert (
        "Do not quote the raw `session_id` into prose that may reach a commit, "
        "PR body, or plan file."
        in _skill_file("plan-it").read_text()
    )
    session_id_redaction_sentence = (
        "Do not quote the raw `session_id` into prose that may reach the PR body."
    )
    step1_section = _heading_section_text(
        _skill_file("ready-for-review"), _READY_FOR_REVIEW_STEP1_HEADING
    )
    assert session_id_redaction_sentence in step1_section, (
        "ready-for-review/SKILL.md: step 1 no longer forbids quoting the raw "
        "session_id."
    )
    completion_section = _heading_section_text(
        _skill_file("ready-for-review"), _READY_FOR_REVIEW_COMPLETION_HEADING
    )
    assert session_id_redaction_sentence in completion_section, (
        "ready-for-review/SKILL.md: Completion section no longer forbids "
        "quoting the raw session_id."
    )


def test_ready_for_review_step3_halts_on_empty_cumulative_diff() -> None:
    """A legitimately empty cumulative diff (the branch's PR is already
    merged) must halt the skill before it reaches `/code-review`, rather
    than running a vacuous review and then failing at the marker.sh write
    gate with a message naming causes that never fired."""
    skill_md_path = _skill_file("ready-for-review")
    lines = skill_md_path.read_text().splitlines(keepends=True)
    start_idx, end_idx = _section_between(lines, _READY_FOR_REVIEW_STEP3_HEADING, skill_md_path)
    section_text = "".join(lines[start_idx:end_idx])
    assert "the branch's cumulative diff against its base is empty" in section_text, (
        f"{skill_md_path}: Step 3 must name the empty-diff halt condition explicitly"
    )
