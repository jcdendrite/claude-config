"""The handoff, brief and resume-context skills, and the executable bit of every stowed script."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from _skill_md_helpers import (
    _assert_pinned_clause_right_bounded,
    _extract_citations,
    _heading_section_text,
    _raw_heading_section_text,
    _skill_file,
)

# pyproject.toml's pythonpath also puts claude/.claude/tests on the import
# path, where these shared test helpers live.
from helpers import (
    SCRIPTS_DIR,
    extract_skill_command,
    normalize_heading,
    run_skill_command,
)

# Single source of truth for SKILL.md structural rules — the commit-gate hook
# shells out to the same module. pyproject.toml's [tool.pytest.ini_options]
# pythonpath puts plugins/skill-management/scripts and evals/ on the import path.
from validate_skill_structure import parse_frontmatter


class TestContinuityFileBucketCrosscheck:
    """Pin the autonomous-vs-authorization bucket cross-check in continuity skills.

    handoff (§3/§3.5) and brief (§6/§6.5) both split next steps into an
    autonomously-safe section and a pending-engineer-authorization section,
    each with a categorization rule. The rule only helps if the writer
    re-scans the safe section against it before writing the file; these
    pre-write checklist bullets are that trigger. Without them an
    irreversible step (a bulk delete, an external communication) can ship
    labeled autonomously-safe and be executed unprompted by a resuming
    session.
    """

    def test_handoff_prewrite_checklist_crosschecks_section3(self):
        """handoff's pre-write checklist must direct re-checking a §3 step
        against the §3.5 rule's underlying principle, not just the
        mechanically-scanned anchor shapes check-handoff.py's soft warning
        covers -- the script's anchor-shape scan is necessarily narrower than
        the full categorization rule, so this residual instruction is what
        keeps that gap from being a silent regression."""
        body = _skill_file("handoff").read_text()
        assert "can still belong in §3.5" in body
        assert "not the underlying principle" in body

    def test_brief_prewrite_checklist_crosschecks_section6(self):
        """brief's pre-write checklist must re-scan §6 against the §6.5 rule."""
        assert "re-checked against the §6.5 categorization rule" in _skill_file("brief").read_text()

    def test_handoff_section3_5_excepts_plain_ship_steps_when_shipping_is_active(self):
        """handoff §3.5 must carve out commit, push and PR creation when the
        resolver exits 0 and names the resumer's own resolver exit as the
        filed step's precondition, so a handoff does not file them as pending
        authorization on a machine where autonomous shipping is active."""
        match = re.search(r"## §3\.5.*?(?=\n## §4)", _skill_file("handoff").read_text(), re.DOTALL)
        assert match, "handoff/SKILL.md §3.5 section not found — has it been renamed?"
        assert "Exception:" in match.group(0), "handoff/SKILL.md §3.5 'Exception:' clause not found — has it been renamed?"
        exception = match.group(0).split("Exception:", 1)[1]
        assert "autonomous-shipping-active.sh" in exception
        assert "exit 0" in exception
        assert "only when" in exception, "the exception must be conditional, not unconditional"
        assert "precondition" in exception, "the filed step must carry the exit as a precondition"
        assert "resumer's own" in exception, "the resumer, not the writer, must evaluate the exit"
        assert "no hold" in exception
        assert "never to the default branch" in exception
        assert "§3.5" in exception, "a stated hold must be filed under §3.5"

    def test_brief_section6_5_excepts_plain_ship_steps_when_shipping_is_active(self):
        """brief §6.5 carries the same exception as handoff §3.5."""
        match = re.search(r"## §6\.5.*?(?=\n## §7 )", _skill_file("brief").read_text(), re.DOTALL)
        assert match, "brief/SKILL.md §6.5 section not found — has it been renamed?"
        assert "Exception:" in match.group(0), "brief/SKILL.md §6.5 'Exception:' clause not found — has it been renamed?"
        exception = match.group(0).split("Exception:", 1)[1]
        assert "autonomous-shipping-active.sh" in exception
        assert "exit 0" in exception
        assert "only when" in exception, "the exception must be conditional, not unconditional"
        assert "precondition" in exception, "the filed step must carry the exit as a precondition"
        assert "resumer's own" in exception, "the resumer, not the writer, must evaluate the exit"
        assert "no hold" in exception
        assert "never to the default branch" in exception
        assert "§6.5" in exception, "a stated hold must be filed under §6.5"

    def test_handoff_prewrite_checklist_points_at_section3_5_for_the_principle(self):
        """The checklist must send the reader to §3.5 for the principle rather than restate it, so it
        cannot contradict §3.5's exception."""
        anchor = "A §3 step the script did *not* warn on"
        checklist_lines = [line for line in _skill_file("handoff").read_text().splitlines() if anchor in line]
        assert checklist_lines, f"handoff/SKILL.md checklist line starting {anchor!r} not found — has it been renamed?"
        assert "including its exception" in checklist_lines[0]

    def test_handoff_prewrite_checklist_crosschecks_section2_6(self):
        """handoff's pre-write checklist must verify §2.6 was populated before writing."""
        assert (
            "§2.6 is populated — a faithful task-list serialization with per-item ordinal, "
            'status, and blocking edges, or "None." — and carries the resume directive'
            in _skill_file("handoff").read_text()
        )


class TestHandoffCommitMarkerCoveredWork:
    """Pin handoff's instruction to land marker-covered work before the boundary.

    §5 now delegates the live/historical/absent computation to
    `marker.sh status` (see claude/.claude/scripts/marker.sh and
    hooks/tests/test_marker_script.py's TestMarkerScriptStatus* classes for
    the mechanism itself) rather than explaining the marker-content-hash
    mechanics in prose — these tests pin that the delegation instruction and
    the commit-first directive survive, not the mechanics explanation that
    the deterministic script now owns.
    """

    def test_handoff_section5_directs_running_marker_status_and_committing_flagged_work(self):
        """§5 must direct running marker.sh status, describe its live/
        historical/absent labeling, and direct the writer to commit finished
        marker-covered work before writing the file when the reconciliation
        flag fired."""
        body = _skill_file("handoff").read_text()
        assert "Run `~/.claude/scripts/marker.sh status` and paste its output verbatim" in body
        assert "each labeled live, historical, or absent" in body
        assert "code-review or skill-review marker whose covered state has uncommitted" in body
        assert "commit it *before* writing this file" in body

    def test_handoff_section5_does_not_claim_markers_die_with_their_session(self):
        """Guard against reintroducing the session-keyed expiry claim.

        Gates authorize on a marker's stored hash, not on the session id in
        its filename, so text asserting that a marker is spent once its
        session ends is false — and costly, because it sends the writer down a
        re-review path that is not required. The claim gets its own assertion
        because it is plausible on its face and was true of an earlier gate
        implementation, which makes it the likely thing for a future edit to
        reintroduce.
        """
        body = _skill_file("handoff").read_text()
        assert "keyed to the session that wrote them" not in body
        assert "nothing listed here will satisfy a pre-commit gate" not in body

    def test_handoff_prewrite_checklist_verifies_marker_covered_work_committed(self):
        """The pre-write checklist must carry the commit-first verification, with the
        §3 fallback for work that is not commit-ready."""
        assert (
            "§5's script output shows no unresolved reconciliation flag; where one "
            "fired, §3 names the review skill the resuming session must re-run to "
            "commit the covered work first" in _skill_file("handoff").read_text()
        )


class TestHandoffTaskListPersistence:
    """Pin the §2.6 task-list serialization and resume directive in handoff.

    Without a faithful task-list serialization and an explicit resume
    directive, a session resumed from a handoff file starts with an empty
    task list and re-derives remaining work from prose instead of tracking
    it from the live state the prior session captured. Resumed sessions are
    typically non-TTY and expose no task-list tool (Task tools are gated on
    an interactive TTY upstream), so tracking must fall back to inline
    conversation state rather than hard-depending on the tool.
    """

    def test_handoff_task_list_reads_live_state_not_memory(self):
        """§2.6 must instruct reading the current task list from the tool if present,
        else from inline-tracked items — never reconstructed from memory."""
        assert (
            "Read the current task list — from your session's task-list tool if it "
            "exposes one, otherwise from the inline items you have been tracking (not "
            "reconstructed from memory) — and list each item with a stable ordinal, its "
            "status — `completed` / `in_progress` / `pending` — and, for pending/in_progress "
            "items, which ordinals block it and (for the in_progress item) its "
            "`activeForm`. Preserve order. Example: `3. [pending] Phase B: … (blocked by 2)`."
            in _skill_file("handoff").read_text()
        )

    def test_handoff_task_list_has_resume_directive(self):
        """§2.6's resume directive must cover authoritativeness (no plan-file/memory
        fallback), tracking order and dependency edges, the no-re-add-completed dedup
        rule, opportunistic (not required) tool use, and the inline fallback."""
        assert (
            "§2.6 is the authoritative source of remaining task state on resume — do not "
            "reconstruct the task list from the plan file or from memory. As you resume, "
            "track the `pending` and `in_progress` items below, preserving order and their "
            "`blockedBy`/`blocks` relationships (map the serialized ordinals to the items "
            "in that position); completed items are listed for context only — do not "
            "re-add them. If your session exposes a task-list tool (e.g. "
            "`TaskCreate`/`TaskUpdate`), mirror the items into it"
            in _skill_file("handoff").read_text()
        )
        assert (
            "If it does not — common for resumed sessions — track them inline. Tracking "
            "these items is a safe, reversible action, not gated by the artifact preamble's "
            "re-confirm-before-executing rule (which is scoped to irreversible/shared-state "
            "actions); a missing task-list tool is not a blocker."
            in _skill_file("handoff").read_text()
        )


class TestHandoffMidFlightNoteUnconditional:
    """Pin that §2.5's mid-flight note and its pre-write-checklist crosscheck fire
    unconditionally, not only on context-limit handoffs, so a phase-complete
    handoff can't silently strand a still-running background dispatch with no
    record of it.

    Mutation-tested: re-adding the "If the handoff reason is context-limit,"
    gate, or dropping the collected/stranded terms, flips these assertions red.
    """

    def test_handoff_section2_5_mid_flight_note_has_no_context_limit_gate(self):
        """§2.5's mid-flight sentence must not gate on the handoff reason, and
        must record each named dispatch as collected or stranded — pinned as one
        contiguous span so the two claims are proven adjacent, not merely
        present somewhere in the file."""
        body = _skill_file("handoff").read_text()
        assert "If the handoff reason is context-limit," not in body
        assert (
            "whatever the handoff reason: open tool calls — including any "
            "background subagent dispatch this session spawned, named by its "
            "`agent-<agentId>` (never its full transcript path, which embeds "
            "this session's own id) — and pending verifications (see §2.6 for "
            "task-list state). Record each named dispatch as **collected** "
            "(its output was folded into this handoff before writing — see "
            "\"Before writing: collect in-flight background dispatches\" above) "
            "or **stranded**"
            in body
        )

    def test_handoff_prewrite_checklist_mid_flight_line_has_no_context_limit_gate(self):
        """The pre-write checklist's mid-flight line must not gate on the
        handoff reason either, and must reference the collected/stranded label."""
        assert (
            "§2.5 names what was mid-flight at the time of the handoff, "
            "regardless of handoff reason — including any background subagent "
            "dispatch this session spawned, by its `agent-<agentId>`, marked "
            "collected or stranded"
            in _skill_file("handoff").read_text()
        )


class TestHandoffCollectStepPinsLoadBearingClauses:
    """Pin the "Before writing: collect in-flight background dispatches" section's
    load-bearing clauses, so a future edit can't silently drop the
    ListAgents-unavailable fallback, reintroduce the deprecated blocking-wait
    path, or lose the subagent/re-fire handling without a test failing.

    Mutation-tested: dropping the ListAgents-unavailable fallback clause,
    reintroducing polling/TaskOutput, or dropping the subagent-gate or
    repeat-block-is-expected clauses each flips a distinct assertion below red.
    """

    def test_collect_step_falls_through_to_stranded_when_listagents_unavailable(self):
        body = _skill_file("handoff").read_text()
        assert "## Before writing: collect in-flight background dispatches" in body
        assert (
            "If `ListAgents` errors or is unavailable, skip straight to "
            "recording every not-yet-returned dispatch as stranded"
            in body
        )

    def test_collect_step_forbids_polling_and_taskoutput(self):
        body = _skill_file("handoff").read_text()
        assert "do not poll and do not call `TaskOutput`" in body

    def test_collect_step_states_subagents_never_reach_it_via_their_own_block(self):
        body = _skill_file("handoff").read_text()
        assert (
            "a subagent never reaches it as remediation for its own hard "
            "block, since the nudge hook's subagent gate exits before any "
            "escalation logic runs"
            in body
        )

    def test_collect_step_treats_a_repeat_hard_block_as_expected(self):
        body = _skill_file("handoff").read_text()
        assert (
            "that's expected, not a new problem — it just means the "
            "session is still past its threshold while following the "
            "block's own remediation"
            in body
        )


# Durable-handoff-location: /handoff and /brief write to a durable
# ~/.claude/ directory, not /tmp (lost on reboot). Both skills legitimately
# mention /tmp elsewhere for the consumed-tier destination (the temp path
# resume-context.sh moves the file to), so these tests anchor to the
# write-target construct — frontmatter description + the body "Write ... at"
# line — rather than a blanket "/tmp" presence/absence scan, which would
# false-positive on that correct content.
_DURABLE_WRITE_TARGETS = {
    "handoff": ("~/.claude/handoffs/", "-handoff.md"),
    "brief": ("~/.claude/briefs/", "-task.md"),
}


@pytest.mark.parametrize("skill_name", sorted(_DURABLE_WRITE_TARGETS))
def test_handoff_and_brief_write_target_matches_durable_path(skill_name: str) -> None:
    """Anchored to the frontmatter description only — the one place this
    path is documented but never executed (the skill picker never runs the
    write recipe). The execution test below covers the literal recipe
    behavior; a text-match against the body's opening paragraph would be
    redundant with it and brittle to copy-only rewording (an SDET review
    round flagged this)."""
    skill_path = _skill_file(skill_name)
    directory, suffix = _DURABLE_WRITE_TARGETS[skill_name]
    frontmatter = parse_frontmatter(skill_path)
    description = frontmatter.get("description", "") or ""
    assert directory in description and suffix in description, (
        f"{skill_name}/SKILL.md frontmatter description does not name the durable "
        f"write target {directory}<slug>{suffix}"
    )


@pytest.mark.parametrize("skill_name", sorted(_DURABLE_WRITE_TARGETS))
def test_handoff_and_brief_write_recipe_executes_to_durable_path(
    skill_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Execution test, not just a text-match: runs the skill's own
    HOOK_TEST_FIXTURE recipe (mkdir -p) in an isolated $HOME and asserts the
    directory actually lands at the literal expected path — this is the
    real, literal instruction the skill always issues (not synthetic
    placeholder scaffolding). Owner-only protection for the directory comes
    from `~/.claude` being `700`, set once by `install.sh` — not from
    anything this recipe does. This test's isolated $HOME never runs
    install.sh, so the created directory's mode is whatever the ambient
    umask leaves it at; asserting a mode here would test the umask, not the
    recipe.
    """
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    monkeypatch.setenv("HOME", str(isolated_home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    skill_path = _skill_file(skill_name)
    command = extract_skill_command(skill_path, "write-target")
    run_skill_command(command, cwd=tmp_path, isolated_home=isolated_home)

    directory, _suffix = _DURABLE_WRITE_TARGETS[skill_name]
    expected_dir = isolated_home / directory.replace("~/", "")
    assert expected_dir.is_dir(), (
        f"{skill_name}/SKILL.md's write-target fixture did not create {expected_dir}"
    )


@pytest.mark.parametrize("skill_name", sorted(_DURABLE_WRITE_TARGETS))
def test_handoff_and_brief_write_recipe_honors_config_dir_when_set(
    skill_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write half of the round trip that
    test_consumes_handoff_under_config_dir_when_set covers on the read side:
    with CLAUDE_CONFIG_DIR set, the write-target recipe must create the
    durable directory under it, not under $HOME/.claude."""
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    config_dir = tmp_path / "profile"
    config_dir.mkdir()
    monkeypatch.setenv("HOME", str(isolated_home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))

    skill_path = _skill_file(skill_name)
    command = extract_skill_command(skill_path, "write-target")
    run_skill_command(command, cwd=tmp_path, isolated_home=isolated_home)

    directory, _suffix = _DURABLE_WRITE_TARGETS[skill_name]
    expected_dir = config_dir / directory.replace("~/.claude/", "")
    assert expected_dir.is_dir(), (
        f"{skill_name}/SKILL.md's write-target fixture did not honor "
        f"CLAUDE_CONFIG_DIR — expected {expected_dir}"
    )


@pytest.mark.parametrize(
    "script",
    sorted(p for p in SCRIPTS_DIR.glob("*.sh") if not p.name.startswith("_")),
    ids=lambda p: p.name,
)
def test_scripts_are_executable(script: Path) -> None:
    """Every script under claude/.claude/scripts/ is invoked by a hardcoded
    literal path (e.g. ~/.claude/scripts/foo.sh), never `bash <path>` — a
    script committed without the executable bit fails outright on first use
    for every stow consumer simultaneously. Generalized from a single-script
    check (resume-context.sh) so a newly-added script gets this coverage for
    free instead of needing its own copy of the same assertion. A leading
    underscore names a sourced library (e.g. _worktree-lib.sh), never invoked
    directly, so it carries no executable-bit expectation."""
    assert os.access(script, os.X_OK), (
        f"{script.name} must be committed with the executable bit set "
        "(git add --chmod=+x) so the stow symlink is runnable"
    )


@pytest.mark.parametrize("skill_name", sorted(_DURABLE_WRITE_TARGETS))
def test_handoff_and_brief_reference_resume_context_literally(skill_name: str) -> None:
    """Doc-consistency check only, explicitly scoped as such (parallel to
    test_handoff_and_brief_write_target_matches_durable_path above) — proves
    the skill body names a literal resume-context invocation, not that the
    invocation actually works end-to-end (test_resume_context.py and the
    hook tests cover that). Kept separate from the executable-bit check
    above so a benign copy-edit to this prose can't fail that invariant, and
    vice versa (an SDET review round flagged the two being combined)."""
    body = _skill_file(skill_name).read_text()
    assert "resume-context <config-dir>/" in body, (
        f"{skill_name}/SKILL.md must give a literal resume-context invocation, "
        "not just mention the name in passing"
    )


# GH-474: consume-durable-continuity-file-on-read.sh moves a handoff/brief
# file the moment it's Read, even from the authoring session mid-draft. Both
# skills warn against re-Read-to-verify with the same fragment, deliberately
# duplicated (this repo forbids shared partials across skills) — this test is
# the only thing that can catch the two copies drifting apart, since two
# independently-worded per-file assertions would each keep passing right up
# to the point the wording diverges. Subject-first so a rewrite to "does not
# consume" breaks the match; stops short of the tool list so cat/grep/sed can
# still change; whitespace-normalized on both sides as forward-looking
# tolerance for a reflow that moves the fragment across a line break, not
# because it currently straddles one.
_READ_CONSUMES_FILE_PIN = "consumes the file — verify with a Bash"


@pytest.mark.parametrize("skill_name", sorted(_DURABLE_WRITE_TARGETS))
def test_handoff_and_brief_warn_against_read_to_verify(skill_name: str) -> None:
    body = " ".join(_skill_file(skill_name).read_text().split())
    pin = " ".join(_READ_CONSUMES_FILE_PIN.split())
    assert pin in body, (
        f"{skill_name}/SKILL.md must warn that a Read of its continuity file "
        f"consumes it — expected the shared fragment {_READ_CONSUMES_FILE_PIN!r}"
    )


@pytest.mark.parametrize("skill_name", sorted(_DURABLE_WRITE_TARGETS))
def test_handoff_and_brief_reference_find_consumed_continuity_file(skill_name: str) -> None:
    """Content-consistency check only, not a behavioral test (test_resume_context.py
    and test_find_consumed_continuity_file.py cover the script's actual
    behavior) — the only guard previously named for the "does not name where
    the file went" clause was the line-count ceiling, which says nothing
    about whether the replacement prose itself references the new lookup
    script."""
    body = _skill_file(skill_name).read_text()
    assert "find-consumed-continuity-file" in body, (
        f"{skill_name}/SKILL.md must reference find-consumed-continuity-file "
        "as the recovery path for an already-consumed continuity file"
    )


_HANDOFF_WARRANT_CHECK_HEADING = "## Before writing: is a handoff warranted?"


# Each entry is one condition→action-bound bullet from handoff/SKILL.md's
# canonical "Before writing: is a handoff warranted?" section. Matched as a
# whole clause with a right-edge boundary check (_assert_pinned_clause_right_bounded)
# rather than by six `assert token in body` checks, because a token-presence
# check would still pass on a gutted section that lists the field names as a
# bare enumeration while dropping the binding between a status/field value
# and what it means.
_PINNED_HANDOFF_WARRANT_CHECK_CLAUSES: dict[str, str] = {
    "status_ok": (
        '`"status":"ok"` — the session is past its threshold when `over_threshold` '
        "is `true` or `already_fired` is `true`; report `estimate` and `threshold` "
        "either way."
    ),
    "nudge_disabled": (
        "`nudge_disabled` is `true` — say so; the measurement still holds but no "
        "nudge will arrive on its own."
    ),
    "model_recognized_false": (
        '`"model_recognized":false` — the window fell back to the 1M default, '
        "so also report `model`/`context_window` and treat the result as a "
        "soft number; those two fields are what let the engineer judge how "
        "far off it is."
    ),
    "cannot_resolve_or_schema_drift": (
        '`"status":"cannot-resolve"` or `"status":"schema-drift"` — name the '
        "`reason` and fall back to judgment."
    ),
    # The fallback-to-judgment sentence is followed on the same line by more
    # prose, so this entry pins through the paragraph's own end (a real
    # structural boundary) instead of stopping mid-sentence.
    "fallback_to_judgment_and_closing": (
        "When `--check` can't resolve a measurement, weigh session length, "
        "how much of the task remains, and whether this is a natural seam. "
        "Any of these warrant a handoff without a cost argument at all: a "
        "§2 reason that applies on its own terms; an explicit engineer "
        "request; or the session ending anyway. Do not quote the raw "
        "`session_id` into prose that may reach a commit, PR body, or "
        "handoff file. If none of the above warrant writing, run "
        "`~/.claude/scripts/marker.sh "
        "deactivate handoff` before stopping — the marker activated above "
        "has no further purpose once the write itself doesn't happen."
    ),
}


class TestHandoffWarrantCheckCanonicalSection:
    """Pin handoff/SKILL.md's "Before writing: is a handoff warranted?"
    section as the single canonical statement of the `--check` reporting
    contract that `plan-it` Step 7 and `ready-for-review` step 1 cite rather
    than restate, per CLAUDE.md's single-source-of-truth rule.
    """

    @pytest.mark.parametrize("branch", sorted(_PINNED_HANDOFF_WARRANT_CHECK_CLAUSES))
    def test_pinned_branch_clause_matches_live_text(self, branch: str) -> None:
        """Exact clause match per branch, mirroring
        test_pinned_scope_clause_matches_live_text. On failure, re-read the
        guarantee and update the constant only if the new wording preserves it.
        """
        raw_section = _raw_heading_section_text(
            _skill_file("handoff"), _HANDOFF_WARRANT_CHECK_HEADING
        )
        pinned_text = " ".join(_PINNED_HANDOFF_WARRANT_CHECK_CLAUSES[branch].split())
        _assert_pinned_clause_right_bounded(
            pinned_text,
            raw_section,
            context=f"handoff/SKILL.md: {branch!r} branch no longer matches its pinned clause.",
        )

    def test_plan_it_cites_the_canonical_section(self) -> None:
        """Uses the repo's own citation parser rather than a raw substring
        match, so a benign hard-wrap the parser tolerates doesn't false-fail
        this test. Citing-side presence only — whether the citation actually
        resolves against the target file's real headings is
        `test_skill_citations_resolve_to_real_headings`'s job.
        """
        citations = _extract_citations(_skill_file("plan-it").read_text())
        expected_heading = normalize_heading("Before writing: is a handoff warranted?")
        assert any(
            citation.target == "handoff/SKILL.md"
            and normalize_heading(citation.heading) == expected_heading
            for citation in citations
        ), "plan-it/SKILL.md no longer cites handoff/SKILL.md's warrant-check section"

    def test_ready_for_review_cites_the_canonical_section(self) -> None:
        """Uses the repo's own citation parser rather than a raw substring
        match, so a benign hard-wrap the parser tolerates doesn't false-fail
        this test. Citing-side presence only — whether the citation actually
        resolves against the target file's real headings is
        `test_skill_citations_resolve_to_real_headings`'s job.
        """
        citations = _extract_citations(_skill_file("ready-for-review").read_text())
        expected_heading = normalize_heading("Before writing: is a handoff warranted?")
        assert any(
            citation.target == "handoff/SKILL.md"
            and normalize_heading(citation.heading) == expected_heading
            for citation in citations
        ), "ready-for-review/SKILL.md no longer cites handoff/SKILL.md's warrant-check section"


# Clauses of resume-context/SKILL.md that keep the skill from consuming a file it
# cannot match to this session, or from running on its own.
# One clause has the skill set each relayed command off as a fenced code block.
_PINNED_RESUME_CONTEXT_CLAUSES: dict[str, str] = {
    "run_only_when_asked": (
        "Run only when the engineer asked to resume in this session. Never run the "
        "skill on your own, including right after writing a handoff or brief."
    ),
    "no_argument_rerun_guard": (
        "If this conversation already loaded a file through this skill, run the "
        "script again only with an explicit argument. With no argument, tell the "
        "engineer and stop."
    ),
    "refuse_and_tell_on_unsafe_arguments": (
        "If `$ARGUMENTS` starts with `-` or contains any character outside "
        "`[A-Za-z0-9._/@+-]`, do not run the script; tell the engineer the accepted "
        "forms: a slug, a file name, or an absolute path."
    ),
    "never_read_pending_path": (
        "Never `Read` a path under `<config-dir>/handoffs/` or `<config-dir>/briefs/`, "
        "because that `Read` consumes the file before any check runs."
    ),
    "session_target_comparison": (
        "First compare `SESSION_TARGET:` with the worktree this session is anchored in: "
        "the working directory the environment context shows, or the `EnterWorktree` "
        "target. The script keys on the Bash `$PWD`, so an earlier `cd` into another "
        "worktree yields a verdict for that worktree. When `SESSION_TARGET:` is `none`, "
        "they match only if the anchored directory is not inside a linked worktree of any "
        "repository. If they differ, say so and stop before any `Read`, without acting on "
        "the file. Give the recovery text below."
    ),
    "file_target_none_check_then_stop": (
        "Then check the target. When the script printed `FILE_TARGET: none` and the "
        "file's §4 header (§3 in a brief) names a directory other than this session's, "
        "say so and stop without acting on the file. Give the recovery text below."
    ),
    "consumed_file_recovery_text": (
        "Recovery text: the file is already consumed, so tell the engineer to `cd` to "
        "the directory named here and run `claude --append-system-prompt-file <LOAD path>`. "
        "Also tell them that `~/.claude/scripts/find-consumed-continuity-file.sh <slug>` "
        "finds the copy later. The directory is the `FILE_TARGET:` path when it is a path "
        "(on `LOAD:` it equals `SESSION_TARGET:`). When `FILE_TARGET:` is `none`, the "
        "script printed no directory for the file: say the directory is unknown and must "
        "come from the session that wrote the file; name none."
    ),
    "unreadable_file_target_relay": (
        "When `FILE_TARGET: unknown` appears, add that the script could not resolve one "
        "directory from the file, so the printed command has none. The engineer "
        "should check the resume command in the file itself, outside this session because "
        "a `Read` here consumes it, and run that command in place of the printed one."
    ),
    "slash_form_only_for_matching_candidates": (
        "Suggest `/resume-context <file name>` only for a candidate whose `target` is the "
        "same path as `SESSION_TARGET:`, or is `none` when `SESSION_TARGET: none`, and never "
        "when either is `unknown`. A candidate whose file name starts with `-` or `@` is "
        "never suggested as `/resume-context <name>`. Every candidate not suggested that way "
        "needs the shell command on its `CANDIDATE:` line, which you relay as a command for "
        "a new terminal."
    ),
    "unreadable_or_absent_candidate_target_caveat": (
        "Add step 3's caveats: its `SESSION_TARGET: unknown` caveat once for the list when "
        "that line appears, and, for each candidate with `target none` or `target unknown`, "
        "the caveat for that `FILE_TARGET:` value beside its shell command."
    ),
    "read_only_after_session_target_matches": (
        "Otherwise `Read` the `LOAD:` path in full. Tell the engineer which `SOURCE:` "
        "file was consumed and its `AGE:`."
    ),
    "skipped_unprintable_relay": (
        "Relay `SKIPPED_UNPRINTABLE:` whenever it appears, with or without candidates. "
        "It means n pending files under `<config-dir>/handoffs/` or `<config-dir>/briefs/` "
        "have names with a character outside `[A-Za-z0-9._/@+-]`, so the script does not "
        "print them. The engineer can resume one with the shell `resume-context` and its "
        "full path; do not list or name them yourself."
    ),
    "no_enter_worktree_no_section_seven": (
        "Do not call `EnterWorktree`, and do not run the file's §7/§7.5 command. The "
        "script already matched the file to this session."
    ),
    "absent_file_target_relay": (
        "When `FILE_TARGET: none` appears, add that the file names no worktree, so the "
        "command must be run from the directory the file was written in."
    ),
    "unknown_session_target_relay": (
        "When `SESSION_TARGET: unknown` appears, add that this session's worktree "
        "could not be determined (a path with a space or a failed git call), not that "
        "the file names another worktree."
    ),
    "shell_command_relay_and_stop": (
        "Relay the line verbatim as a command for a new terminal, not as a slash "
        "command. The file is still pending. Stop."
    ),
    "fence_each_relayed_command": (
        "In chat replies, put each command this skill has you relay or suggest alone "
        "in its own fenced code block."
    ),
}


class TestResumeContextSkill:
    """Pin the resume-context body's guards, so a reword that drops one fails here
    instead of drifting silently. The tests check wording and the clause's right edge,
    not behavior. On failure, re-read the guarantee and update the
    constant only if the new wording preserves it.
    """

    @pytest.mark.parametrize("clause_key", sorted(_PINNED_RESUME_CONTEXT_CLAUSES))
    def test_pinned_clause_matches_live_text(self, clause_key: str) -> None:
        body = _skill_file("resume-context").read_text()
        pinned_text = " ".join(_PINNED_RESUME_CONTEXT_CLAUSES[clause_key].split())
        _assert_pinned_clause_right_bounded(
            pinned_text,
            body,
            context=f"resume-context/SKILL.md: the {clause_key!r} clause no longer matches its pinned text.",
        )

    def test_session_target_comparison_precedes_the_read(self) -> None:
        """The comparison must come before the `Read` clause, or a mismatched file is consumed first."""
        body = " ".join(_skill_file("resume-context").read_text().split())
        comparison = " ".join(_PINNED_RESUME_CONTEXT_CLAUSES["session_target_comparison"].split())
        read = " ".join(_PINNED_RESUME_CONTEXT_CLAUSES["read_only_after_session_target_matches"].split())
        assert 0 <= body.find(comparison) < body.find(read), (
            "resume-context/SKILL.md: the SESSION_TARGET comparison clause no longer starts before the `Read` clause."
        )


# Clauses of the handoff §7 and brief §7.5 resume-command sections that make the
# written `--cwd` the directory the resumed session starts in, which the announce
# message compares with the session's worktree to pick the resume route. Each
# section states the directory rule and the command forms (three bullets), then a
# second paragraph. The second paragraph holds the `cd <path> &&` prohibition and
# the instruction to relay the announce hook's resume route, so a trim at the
# line cap must fail here.
_PINNED_RESUME_DIRECTORY_CLAUSES: dict[str, tuple[str, str, str]] = {
    "handoff_directory_bullet": (
        "handoff",
        "## §7 Resume command",
        "The directory is the root of the worktree or checkout where §3's first step "
        "runs, or that directory itself outside any repository. For a step that creates "
        "a worktree it is the existing directory the step runs from. For a first step "
        "that names no directory or is only a question it is this session's own "
        "directory, as §4's header names it.",
    ),
    "handoff_cwd_form_bullet": (
        "handoff",
        "## §7 Resume command",
        "Write `resume-context --cwd <worktree path> <config-dir>/handoffs/<slug>-handoff.md`.",
    ),
    "handoff_bare_form_bullet": (
        "handoff",
        "## §7 Resume command",
        "Write the bare form `resume-context <config-dir>/handoffs/<slug>-handoff.md` "
        "instead only when that directory is the one this session runs in outside any "
        "linked worktree and §4's header names it too. In every other case write the "
        "first form alone.",
    ),
    "handoff_second_paragraph": (
        "handoff",
        "## §7 Resume command",
        "Relay the resume route the announce hook names after the write to the "
        "engineer. `--cwd` makes the launched session start "
        "in that directory regardless of where the resume command is actually run from "
        "— do not use a `cd <path> &&` prefix instead, which only works if the invoker "
        "happens to already be positioned to run it. The command moves the file to a fresh "
        "temp path and launches a new session with it loaded, so nothing depends on the resuming "
        "session remembering to read or delete the file.",
    ),
    "brief_directory_bullet": (
        "brief",
        "## §7.5 Resume command",
        "The directory is the root of the worktree or checkout where §6's first step "
        "runs, or that directory itself outside any repository. For a step that creates "
        "a worktree it is the existing directory the step runs from. For a first step "
        "that names no directory or is only a decision it is the worktree §3 names, "
        "else this session's own directory.",
    ),
    "brief_cwd_form_bullet": (
        "brief",
        "## §7.5 Resume command",
        "Write `resume-context --cwd <worktree path> <config-dir>/briefs/<slug>-task.md`.",
    ),
    "brief_bare_form_bullet": (
        "brief",
        "## §7.5 Resume command",
        "Write the bare form `resume-context <config-dir>/briefs/<slug>-task.md` "
        "instead only when that directory is the one this session runs in outside any "
        "linked worktree and §3 names no other directory. In every other case write the "
        "first form alone.",
    ),
    "brief_second_paragraph": (
        "brief",
        "## §7.5 Resume command",
        "Relay the resume route the announce hook names after the write to the "
        "engineer. `--cwd` makes the launched session start "
        "in that directory regardless of where the resume command is actually run from "
        "— do not use a `cd <path> &&` prefix instead, which only works if the invoker "
        "happens to already be positioned to run it. The command moves the file to a fresh "
        "temp path and launches a new session with it loaded, so picking up the brief doesn't depend "
        "on the fresh session remembering to read or delete the file.",
    ),
}

_HANDOFF_SECTION4_HEADING = "## §4 Files modified this session"
_HANDOFF_SECTION4_REENTER_PHRASE = (
    "Direct the resuming session to re-enter §7's directory (its `--cwd`, or this "
    "header's directory when §7 is bare) before"
)


class TestResumeCommandDirectoryClauses:
    """Pin the §7/§7.5 text that makes `--cwd` the directory the resumed session
    starts in. The tests check wording and the clause's right edge, not behavior.
    On failure, re-read the guarantee and update the constant only if the new
    wording preserves it.
    """

    @pytest.mark.parametrize("clause_key", sorted(_PINNED_RESUME_DIRECTORY_CLAUSES))
    def test_pinned_clause_matches_live_text(self, clause_key: str) -> None:
        skill_name, heading, pinned_text = _PINNED_RESUME_DIRECTORY_CLAUSES[clause_key]
        raw_section = _raw_heading_section_text(_skill_file(skill_name), heading)
        _assert_pinned_clause_right_bounded(
            " ".join(pinned_text.split()),
            raw_section,
            context=f"{skill_name}/SKILL.md: the {clause_key!r} clause no longer matches its pinned text.",
        )

    @pytest.mark.parametrize(
        "clause_key",
        [
            "handoff_directory_bullet",
            "handoff_cwd_form_bullet",
            "handoff_bare_form_bullet",
            "brief_directory_bullet",
            "brief_cwd_form_bullet",
            "brief_bare_form_bullet",
        ],
    )
    def test_directory_rule_clause_is_one_unwrapped_line(self, clause_key: str) -> None:
        """A hard wrap inside a clause that holds a command splits the command, and the
        template contract's scan joins lines only on a trailing backslash.
        """
        skill_name, heading, clause = _PINNED_RESUME_DIRECTORY_CLAUSES[clause_key]
        section_lines = [
            line.removeprefix("- ") for line in _raw_heading_section_text(_skill_file(skill_name), heading).splitlines()
        ]
        assert clause in section_lines, (
            f"{skill_name}/SKILL.md: the {clause_key!r} clause is not one unwrapped line of its section."
        )

    def test_handoff_section4_directs_reentry_to_the_resume_directory(self) -> None:
        section_text = _heading_section_text(_skill_file("handoff"), _HANDOFF_SECTION4_HEADING)
        assert _HANDOFF_SECTION4_REENTER_PHRASE in section_text, (
            "handoff/SKILL.md: §4 no longer directs the resuming session to re-enter §7's directory."
        )
