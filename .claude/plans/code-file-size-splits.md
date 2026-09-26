# Review quality: reviewer read methodology and oversized code files

## Context

Goal: improve the quality and thoroughness of PR review in this repo. The work has three parts: ground the reviewer read methodology in review best practice, measure how that methodology changes the defects reviewers find, and shrink the oversized, high-churn code files that no read rule can review well. Token cost is a secondary outcome. It is reported, but it is never an acceptance gate.

The engineer set that priority: "first and foremost optimizing diffs agents look at to maximize quality and thoroughness of findings according to best practices, not to minimize tokens". They added: "the blanket rule of reading the entire file with the change was a signal that we should reevaluate the methodology, not a sign to put token optimization at the top of the list. Quality of code an repo over all."

The session started from the engineer's hypothesis that PR token use tracks touched-file size because reviewers read whole files. The instruction side is confirmed. Nine reviewer agent bodies and the `code-review` base checklist (`claude-skills/skills/code-review/SKILL.md:82`) say "Read every changed file fully", and no reviewer has a read cap.

The problem that instruction raises is quality, not cost:
- The Read tool returns at most 25,000 tokens per call. In the one file measured, that was 956 lines (G3). A "full" read of a larger file is therefore partial unless the reviewer pages, and nothing records whether reviewers page.
- Nothing in this repo measures the defects a review misses. `reviewer-yield` counts which dispatches reported findings and whether the cited paths were later edited. It cannot see a defect no reviewer reported.

It matters now because the largest files are also the most-churned:
- `test_transcript_analysis.py` has 27,782 lines and 84 commits in 90 days.
- `transcript-analysis.py` has 13,945 lines and 75 commits.
- `_lib.sh` has 3,554 lines and 64 commits.

A governing decomposition plan for `transcript-analysis.py` (`.claude/plans/transcript-analysis-decomposition.md`) stalled after its cost-family and reviewer-yield phases. Its named next target is review-trace. The 2026-08-10 repo quality audit (`docs/reports/2026-08-10-repo-quality-audit/findings.md`) already put two related items in its backlog:
- the transcript-analysis split (Phases 4a and 4b);
- an in-file reorganization of `_lib.sh` (Phase 3). Phase 3 has not landed.

The intended outcome is an epic with child issues covering four areas:
- the reviewer read methodology;
- quality measurement;
- decomposition and reorganization of the largest files;
- a size policy grounded in an offender assessment.

This branch implements the first decomposition slice.

## Approach

This branch files an epic with six new child issues (A–F) and links the overlapping existing issues and the audit's backlog phases. It then ships the first decomposition slice: review-trace moves into `transcript_analysis/denials.py` (a leaf) and `transcript_analysis/review_trace.py`, and its tests move into two matching test files.

For reviewers, the recommendation has three parts:
- Keep whole-file reads when one Read call returns the whole file.
- For a larger file, read its outline plus the diff with whole-function context.
- In every case, add mandatory reads that follow the change beyond its file.

Child B implements this. B merges only if child A's replay benchmark shows the new rule finds at least as many known defects as today's rule.

### Reviewer read methodology: recommendation (implemented by child B)

**The rule.** Replace "Read every changed file fully" with the following.

- **Entry read.**
  - If one default Read call returns the whole changed file, read the whole file. "Returns the whole file" means the result carries no `PARTIAL view` notice (G3). The Read limit is token-based, so a line count is only a hint.
  - Otherwise, read the file's outline (its top-level definitions, located with Grep) and the diff with function context.
  - Git may return a whole class or file because it could not identify the enclosing function. Without a diff driver, an indented method is not a function header (row 12). In that case, locate the enclosing function with Grep and read that range.
- **Mandatory expansions, in both cases.** Each is triggered by what the change does:
  - A contract change or a change callers can observe (signature, return or output shape, raised error, side effect, exit code, module constant): read every consumer's call site. Go further up only while the change keeps propagating.
  - An error or fallback change: read callers up to the point where the error is handled.
  - Reliance on a callee's behavior that is not visible in what was read: read that callee.
  - A change to module-level state, imports, or class invariants: read those declarations and their users.
  - A fix to one arm of a multi-arm structure, or a new helper or constant: search the file and its package for sibling arms and for an existing equivalent.
- **Whole file at any size:** only where a lens rule below says so.

**Why this rule, with finding quality as the objective:**

- **It keeps whole-file reads where they work.** For a file that one Read call returns whole, a whole read gives within-file context:
  - sibling arms of a fixed structure;
  - duplicated helpers;
  - local conventions;
  - whether the change leaves the file doing two things.

  Function context alone would give that up on exactly the files where a whole read is feasible. The quality priority (rows 41, 42) does not justify that loss.
- **It changes behavior only where the current rule already fails.** Roughly the 43 files over 1,000 lines exceed one Read call, including the two most-churned (row 22, G3). For those files a "full" read is partial unless the reviewer pages. A-field measures how often reviewers page.
- **It reaches further beyond the file.** The fixed "Trace calls at least one hop in each direction" (`staff-backend-engineer.md:63`) is hand-rolled (row 11). The expansions follow every consumer of a changed contract and the whole error path, where one hop can stop short.
- **The size cut is a feasibility boundary.** It is the Read tool's own window, not a claim about reviewer attention. No source sets such a cut (G4). A-bench tests the rule against the status quo and against function context for every file.

**Primary sources** (verified, row 10). They split by lens, and each split points the same way:

- **Whole file is the conditional case.** Google's reviewer guide: "Usually the code review tool will only show you a few lines of code around the parts that are being changed. Sometimes you have to look at the whole file to be sure that the change actually makes sense."
- **Understanding needs context.** Bacchelli & Bird: "When reviewing a small, unfamiliar change, it is often necessary to read through much more code than that being reviewed." The whole-file entry read supplies that context for files that fit one Read call. The outline and the expansions supply it for larger files.
- **Diff-only trades recall for precision.** Anthropic's code-review plugin tells its bug agents: "Focus only on the diff itself without reading extra context." and "Do not flag issues that you cannot validate without looking at context outside of the git diff." This repo's reviewers carry the opposite duty. `code-review/SKILL.md:36`: "A defect outside the boundary that the change causes, activates, or newly reaches stays in scope". Diff-only would silently drop that duty, so it is rejected.
- **Security needs reach beyond the diff.** Anthropic's security review: "Trace data flow from user inputs to sensitive operations" and "Use the repository exploration tools to understand the codebase context".
- **Call-graph reach should follow a question.** Pascarella et al. say a reviewer "may reconstruct the invocation path of a given function to understand the impact", and rank this below other information needs. No source sets a hop count (G4).
- **For large files, the entry unit is built into git.** `git diff -W`/`--function-context`: "Show whole function as context lines for each change".

**Leads B verifies before citing** (row 55; none were fetched this session). B's verify-sources pass quotes each one or drops it.
- Long-context research reporting that models use information in the middle of long inputs less reliably (Liu et al., "Lost in the Middle").
- Anthropic's context-engineering guidance on loading context just in time.
- The rest of Google's "Context" section, which continues past the quoted sentences to whole-system code health.

**Per lens.** Agent bodies are stowed to every consumer, so the rule depends only on harness behavior (the Read window), git, and Grep. None of these differs between consumers.

- **Default:** every code-reviewing lens, plus the `/code-review` base checklist (`SKILL.md:82`), follows the rule above.
- **`ciso-reviewer`:** the default, plus data-flow tracing from each input the change touches to its sinks. Read CI, auth, permission, and policy config files whole at any size. An allow or deny entry can only be judged against the whole list it sits in.
- **`staff-data-engineer`:** read migrations whole and in order (unchanged).
- **`staff-sdet`:** the default, applied to test files.
  - For a test file larger than one Read call: read the changed tests with function context, the fixtures they use, and a Grep of sibling test names for duplicates.
  - Always read the production code each changed test exercises.
- **`staff-backend-engineer` and `staff-analytics-engineer`:** the fixed one-hop rule gives way to the expansions.
- **`comment-discipline-reviewer`:** the default. For a code file larger than one Read call, also read the module docstring, to judge altitude. Markdown docs are still read whole (unchanged).
- **Out of B's scope:** `skill-fidelity-reviewer` and markdown instruction files. They are already length-gated, and fidelity is a judgment about the whole document.

**Where the line count comes from.**
- The reviewer inputs that #1103's orchestrator writes carry each changed file's line count. For a file over the predictor's cut, they also carry the function-context diff and the outline.
- A reviewer without those inputs counts lines with Grep (pattern `^`, count mode).
- The line count only predicts which branch applies. At G3's one measured density the cut falls near 1,000 lines, and a denser file crosses it sooner. The deciding signal is the Read result itself: a `PARTIAL view` notice means the file did not fit one call (G3, row 56).

### Epic and children

The epic is titled "Review quality: reviewer read methodology and oversized code files". Its body carries a task list of child issue references, and each child links back to it. Existing issues and the audit's backlog phases are linked, not re-filed.

Ordering between children is recorded on the issues themselves, not only in this plan:
- The epic's task list carries two checkboxes, placed before B's entry:
  - "A-bench result recorded (link)";
  - "A-field baseline captured (date, where the measurement lives)".
- B's issue body opens with "Blocked by: both A checkboxes on the epic". B may be drafted before then. It does not merge before then.
  - A-bench needs B's draft rule as one of its arms.
  - Transcript retention (G1) makes a B merge before the A-field baseline an unrecoverable measurement loss.
- F2's issue body opens with "Blocked by: F1's assessment". The limits are decided (rows 60–62), but row 45 still has the engineer see F1's offender table before F2 imposes them.
- D carries no prerequisite. The prior gate on widening `_HELPER_LIBRARY_NAMES` is obsolete, because D creates no file.

- **A — Measure review quality (new).** A has two instruments. Its primary purpose is to measure finding quality; token figures are secondary columns.
  - **A-bench (primary; the acceptance gate for B).** A local-only replay harness under `evals/`, following that directory's never-CI posture (row 53).
    - Inputs: a hand-curated set of this repo's merged PRs that carry a known defect. Two sources feed it:
      - a later fix commit whose changed lines blame to the PR (SZZ-style, row 54);
      - while transcripts are retained, an issue a later review round raised on code an earlier round already had in scope.
    - Arms:
      - the current rule;
      - B's draft rule;
      - function context for every file, as the low-context comparator.

      Every arm uses the same model and K samples.
    - Outputs per arm:
      - recall on the known-defect set;
      - adjudicated precision (an adjudicator labels each finding valid or not, and a human spot-checks the labels). The adjudicator and the spot-checker are blind to which arm produced each finding;
      - Read tokens, as a secondary column.
    - A's plan fixes the non-inferiority margin before any run. It also fixes the minimum defect-set size that margin needs to have useful power. If the curated set falls short, A reports the result as inconclusive rather than as a pass.
    - The defect set comes from this public repo's own git history. Any transcript-derived figure follows `docs/private-project-redaction.md` § "Publishing a tooling measurement".
  - **A-field (secondary).** Two PRs.
    - A1 extracts `read-scope` into `transcript_analysis/read_scope.py`. This is a behavior-preserving decomposition phase, so the monolith doesn't grow.
    - A2 adds:
      - a reviewer-subagent split, reusing `reviewer_yield._is_reviewer_subagent_type`;
      - a touched-file-size bucket;
      - main-thread Reads inside review-round windows, using `review_rounds`' window detection;
      - a count of reviewer Reads that returned a `PARTIAL view` notice, and how many of those the reviewer followed with further paged Reads. That shows how often a "full" read was partial (G3, row 16).

      A2 reports these alongside the existing field signals:
      - reviewer-yield's verdict mix and cited-path edit overlap (row 17);
      - review-ledger ADDRESS/DEFER rates, if A's plan finds ledgers aggregable across sessions (row 52).
    - A-field outputs size buckets only, never file paths, matching read-scope's redaction design.

  A starts now, in parallel with everything else.

  Acceptance: A answers these questions:
  - A-bench: per arm, what are recall and adjudicated precision on the known-defect set, against the pre-set margin?
  - A-field: how often were reviewers' "full" reads of files larger than one Read call actually partial?
  - A-field: what share of reviewer tokens went to Read, and what share of that went to files over 1,000 lines (secondary)?
- **B — Reviewer read methodology (new).** Implements the rule above. It edits:
  - the agent bodies in row 8;
  - `code-review/SKILL.md:82`, and the `:36` sentence that names "the full-file read required elsewhere in this skill";
  - `claude/.claude/CLAUDE.md:65`, whose "reviewing it" currently reads as covering reviewing a change. Coordinate this edit with #885.
  - optionally, a repo-root `.gitattributes` with diff drivers, once row 12 is verified. This improves only this repo.
  - `pr-diff-against-base.sh`, which gets a function-context mode as a new flag. Its default must not change, because `:91` documents its output as byte-identical to `git diff`.

  B coordinates with #1103. The line count, the function-context diff, and the outline belong in #1103's orchestrator-written reviewer inputs, not in a parallel mechanism. B also evaluates whether those inputs can mark moved blocks, so that a move reads as a move. That is unverified; B checks git's move detection. B's own review will fire code-review's "Reshapes reviewer ownership" row.

  Acceptance:
  - A-bench: the draft rule's recall is non-inferior to the current rule's within A's pre-set margin, and its adjudicated precision is not lower. Tokens are reported, never gated.
  - After merge: A-field watches late findings (issues raised in a later review round on code an earlier round had in scope) over a window. A rise reopens B.
- **C — Complete the transcript-analysis decomposition (new; tracking issue).** One issue with a task list. `.claude/plans/transcript-analysis-decomposition.md` governs it, and each phase keeps its own `/plan-it`. Phases:
  - review-trace (this branch);
  - read-scope (A1);
  - the remaining groups, largest first, re-measured at each phase;
  - the final `cli.py` phase. It also relocates `_UNCONDITIONAL_HEADER_CASES` and converts line-number citations into the shim to symbol references (`review_rounds.py:42,62,71,91,124`). The citations in `config-schema-audit.md:205,213,225` are converted at the cost-ledger phase instead.

  C also closes the audit's Phases 4a and 4b (row 51), and links the audit report:
  - 4b is the governing plan's shim-plus-package design.
  - 4a's test-split-first ordering is superseded: each group's tests move with its source, under the audit's node-ID invariant.

  Each phase targets the engineer's 1,000-line limit, for modules and test files alike (rows 60, 61). The finished cost phase and this branch's two new test files already exceed it (row 57), and F1 records them. Once F2 lands, a phase that would create a file over the limit splits it along a named seam, or records an exception with a reason.

  The quality case for C is the governing plan's own case: coupling and importability. In addition, a module that fits one Read call is read whole under B's rule.
- **D — Reorganize `_lib.sh` in-file (new; the audit's Phase 3).**
  - Scope: the audit's Phase 3 as written: delimited sections by concern, and a header index naming each section and its entry points (`findings.md:325`). No new file (`findings.md:332`).
  - Why not a split:
    - Every hook sources the whole library, so sibling files would narrow no consumer's dependency.
    - In-file sections give reviewers the same map.
    - The header index is also the outline B's rule reads for a file larger than one Read call.
  - The audit's four objections to a split are partly stale (rows 49, 50). Since the audit, `_config.sh` shipped as a hook-side sibling:
    - `_lib.sh` sources it and returns 1 on failure, so each consumer's existing source guard fires.
    - `_HELPER_LIBRARY_NAMES` names it.

    D's issue records that correction, so a later split proposal starts from current facts. The audit report is a dated record and is not edited.
  - F gives `_lib.sh` a named exception.
  - Related: #1041, #1042. Before filing D, search for an existing Phase 3 issue, and link it instead of re-filing if one exists.
- **E — Split the large test files (new).**
  - Seams:
    - `test_lib.py` by `_lib` domain. Follow D's sections once D lands, and the existing `test_lib_*.py` siblings meanwhile.
    - `test_skills.py` by skill.
    - `test_deny_private_project_refs.py` by detector.
    - `test_marker_script.py` by subcommand.
    - Further seams come from F1's "split" verdicts.
  - Each split holds the audit's invariant (`findings.md:347-352`): collected node IDs are equal before and after, and no existing assertion is edited.
  - Each split also checks select-tests' exact-path exceptions and `.claude/rules/test-tree-packaging.md`.
  - The work is test-only and can run in parallel with everything else.
- **F — Code-file size policy (new).** Two PRs, in order.

  The engineer's decisions:
  - a repo-local pytest check, for this repo (rows 43, 44);
  - no limit until an offender assessment exists (row 45);
  - exceptions with their own limits must be considered (row 46);
  - exact ceilings, and a 1,000-line limit for production code (row 60);
  - a 1,000-line limit for test files (row 61);
  - ceilings exact in both directions, so a file that shrinks must also lower its ceiling (row 62).

  Row 45 still governs timing: the limits are decided, but F2 imposes them only after F1's assessment exists. At G3's one measured density, 1,000 lines is also close to one default Read call, so a file within the limit is usually read whole under B's rule. That is a feasibility tie, not evidence that 1,000 lines is a quality threshold (G4, row 19).

  - **F1 — offender assessment.** For each file over 1,000 lines, F1 records:
    - physical and non-comment line counts;
    - 90-day commits;
    - its subject and candidate seams;
    - a verdict: either split pending, with the issue that tracks it, or structural exception, with its reason.

    Both verdicts get an exception row while the file is over the limit (M15). F1 proposes each row's ceiling at the file's size then. F2 sets each ceiling to the file's size when F2 lands, which the check's own failure message prints. The assessment and the proposed exception table live in F1's plan file. No check lands in F1.

    First pass, from this revision's count (row 22):
    - Non-test files over 1,000 lines: 8.
      - `transcript-analysis.py` (13,945): split tracked by C.
      - `_lib.sh` (3,554): exception candidate. The audit decided on in-file reorganization (D).
      - `post-crash-sessions.py` (1,861): unassessed.
      - `claude/.claude/tests/helpers.py` (1,784): unassessed.
      - `evals/run_skill_evals.py` (1,422): unassessed.
      - `_config.sh` (1,258): exception candidate. `install.sh` sources it standalone before stow (row 59).
      - `transcript_analysis/cost.py` (1,230): a finished decomposition phase that is already over the limit (row 57).
      - `cleanup-merged-branches.sh` (1,025): unassessed.
    - Test files over 1,000 lines: 35. Five already have tracked splits: `test_transcript_analysis.py` (C), and E's four. The other 30 need verdicts.
    - This branch adds two test files over 1,000 lines, `test_transcript_review_trace.py` and `test_transcript_denials.py` (row 57). F1's re-derived list includes them once this branch merges. Their verdict is split pending, which feeds E.
    - Next non-test files to cross 1,000: `install.sh` (995) and `marker.sh` (914) (row 58).

  - **Consequences F1 records for the engineer** (row 45):
    - A new-offender failure lands on whichever PR crosses the line. `install.sh` is five lines under.
    - The 1,000-line test limit puts 35 test files over it today, and 37 after this branch. The decomposition keeps creating test files over it (row 57), so after F2 lands each C phase splits the tests it moves along a named seam, or records an exception.
    - Both-direction ceilings put a ceiling edit into every PR that changes an exception file's length, whether it grows or shrinks. That includes every C phase, since each one shrinks the shim and the legacy test file.
    - The check must run on any `.py`/`.sh` change. It therefore needs a select-tests exception like `test_ticket_reference_discipline.py`'s (row 21). Without one, a scoped run skips the check and only CI's full suite catches a crossing.
    - A ceiling raise is one line in a table, and an agent can make it mechanically. The required reason field and the reviewed diff are the only friction.

  - **The check's semantics** (M15):
    - The general limit is 1,000 physical lines, for production and test files alike (rows 60, 61). A file without an exception row fails above it.
    - A file with an exception row passes only when its physical line count equals the row's ceiling (row 62).
    - Every row needs a non-empty reason, which the check enforces. That a raise was reviewed is process, carried by PR review, not by the check.
    - A row fails when its file no longer exists, or when its ceiling is at or under the general limit.
    - Concurrent PRs that change the same exception file's length both edit its row, so the second to merge gets a git conflict on that row. F2's plan documents the resolution: after rebasing, rerun the check and write the value its failure message prints. A wrong merge cannot pass, because the check compares against the real file size.
    - Physical lines are the unit. This is the plan's default, not an engineer decision (row 63). F1's table carries both counts, so the engineer can change the unit when F1 presents the assessment.
    - The failure message prints the file, its physical line count, and the exact value to write in its row, or says to remove the row. For a file over its limit or ceiling, it lists the remedies in this order:
      1. move the new code into a new module or test file;
      2. split the file along a named seam, such as a subcommand, a domain, or a detector;
      3. add or raise an exception row, with a reason.

      It forbids positional splits, such as a `_part2` file, and fitting a file by deleting comments or joining lines. The check cannot detect either, so the message and PR review carry that rule (row 63).
  - **F2 — the check.** It lives beside `test_ticket_reference_discipline.py`, with its own select-tests exception. That precedent's predicate matches `.py` files only. F2's predicate must also match `.sh` files anywhere in the repo. `select-tests.py` already routes shell scripts under `claude/.claude/scripts/` to that test directory (`_is_scripts_dir_shell_script_change`). It does not route shell files elsewhere, such as `install-dev.sh` or the top-level `scripts/` directory. Without the wider predicate, those are missed in every scoped local run.
  - **Rejected: a hook under `claude/.claude/hooks/`.** It would fire in every stow consumer's repositories, not just this one (row 21).
  - **Rejected: commit-time ratchet semantics through `_lib_staged_length_gate`** (row 20). That gate denies only growth, so a shrink leaves slack that later growth spends without a reviewed edit. The engineer chose both directions (row 62).
- **Existing issues:**
  - #1015 joins the epic as a child. This branch adds about 90 lines of builders to `scripts/tests/conftest.py`. Note that on #1015 if that is the conftest it targets.
  - #1103 is linked from B.
  - #1041 and #1042 are linked from D.
  - #885 is linked from B.
  - #955 needs re-triage before it is linked. Its subject, `split_command_segments`, exists nowhere in this tree (row 24).

### First slice (this branch): review-trace

**Why review-trace first:**

- The governing plan names it next.
- Phase 3 deferred its ownership decision for `_is_reviewer_subagent_type` to this phase (`.claude/plans/transcript-analysis-phase3-reviewer-yield.md` row 4c).
- Every one of its cross-group couplings is mapped (row 28).
- Both new modules come in under the 1,000-line limit, at about 385 and 700 lines, and at G3's density each fits one default Read call. Under B's rule, future changes to review-trace code are therefore read whole (row 57). The two new test files exceed the limit and one Read call; F1 records them (Out of scope).

**Alternatives considered:**

- Cache-rebuild is larger, but its couplings are unmapped (row 38).
- B cannot go first (M3).
- A1 is the first PR on A-field's parallel track.

**How the code splits.** It splits along a real seam. Pure denial classification has no `cmd_*` dependency, so it becomes a leaf module (M6).

- **`denials.py`** takes shim lines 1036–1420 (≈385 lines by line-number arithmetic):
  - Hook-denial detection: `hook_denial_key`, `_normalize_blocking_error`, `_HOOK_DENIAL_SIGNATURE`.
  - Label, cause, and command-shape classification: `_DENIAL_HOOK_*`, `_DENIAL_COMMAND_*`, `_drop_denial_command_flag_values`, `_denial_hook_label`, `_denial_cause_kind`, `_denial_command_shape`, `_DENY_SUMMARY_*`, `_DENIAL_CAUSE_*`.
  - toolDenialKind friction classification: `_GATE_TOOL_DENIAL_KIND`, `_TOOL_DENIAL_KIND_REGIME_START`/`_TS`, `_is_nongate_friction_kind`, `_FRICTION_KINDS`, `_FRICTION_KIND_OTHER`, `_friction_kind_label`.
  - It imports `corpus` by module, for the module-level `_parse_ts` call at :1370.
- **`review_trace.py`** takes shim lines 1011–1025 and 1422–2107 (≈700 lines):
  - `REVIEW_TRACE_SKILLS`, `_ARCHITECT_CONSULT_*`, `_REVIEW_TRACE_NO_SESSIONS_MSG`;
  - `_print_deny_summary`, `_is_architect_consult_dispatch`, `_group_start_indices`, `_review_trace_session_events`, `_fresh_records_and_group_boundaries`;
  - `compute_deny_summary_data`, promoted from `_compute_deny_summary_data`;
  - `cmd_review_trace`.

  It imports `corpus`, `denials`, `render`, `reviewer_yield`, and `scope` by module, following the pattern in `reviewer_yield.py:5-6,19`. It calls `reviewer_yield._is_reviewer_subagent_type`. That is the package's first import from one command-group module into another, and the architecture doc records it (M7).
- **Stays in the shim:**
  - `_MCP_TOOL_BUCKET_LABEL` and `_UNREQUESTED_MODEL_LABEL` (1005–1009);
  - `AUDIT_JUDGMENT_SKILLS` (1027–1034).

**Shim imports.** Each serves a still-monolithic caller (M8):

- From `denials`:
  - `hook_denial_key`, for `_friction_denial_events` at :10615 and :10628;
  - `_drop_denial_command_flag_values`, for `_command_segment_is_mutating_git` at :10253.
- From `review_trace`:
  - `cmd_review_trace` and `REVIEW_TRACE_SKILLS`, for `build_parser` at :13087;
  - `compute_deny_summary_data as _compute_deny_summary_data`, for `_cost_ledger_report` at :8061.
- `denials` and `review_trace` are added as whole modules to the existing module-import line at :40.

Drop the `_is_reviewer_subagent_type -> _review_trace_session_events` note at :139. Keep that name's import only if a remaining test still reads it via `_mod`.

**Where the tests go:**

- **`test_transcript_denials.py`** holds every test that reads hooks by path:
  - 3513–3600: the flag-value, nongate-friction, and friction-label unit tests;
  - 18983–20093: its own section header (18983–18986), then the label enumeration, the bootstrap-fallback lists and their two module-level tests, the real-hook drivers, deny-gate conformance, and cause kind.
- **`test_transcript_review_trace.py`** gets:
  - `_since_until_epochs` (3506);
  - 3625–5235: `TestReviewTrace` and `TestComputeDenySummaryDataGroupBoundaryFreshRead`. The audit-routing section header at 5237–5239 stays in the legacy file, and so does the multi-account header at 20095–20098.
- **`conftest.py`** gets `_hook_deny`, `_hook_deny_current`, and `_review_trace_args` (3416–3505). The friction-count tests and cross-subcommand tables that stay in the legacy file still use them (row 30).
- **What stays in the legacy file:**
  - `TestSanitizeTableCell` (3601), which tests `render`;
  - the friction-count classes;
  - `_UNCONDITIONAL_HEADER_CASES` and its classes (row 36).
- **Imports each new file needs.** The loader template below lacks several of these, and a missing name in a `parametrize` decorator fails at collection:
  - `test_transcript_review_trace.py`: `argparse` (for `_since_until_epochs`), and `CONSULT_CLASSIFICATION_TABLE` from `helpers` (the parametrize at legacy :3777).
  - `test_transcript_denials.py`: `json`, `re`, `shutil`, `subprocess`, `Path`, and `HOOKS_DIR`, `bash_input`, `build_path_without`, `run_hook_reason` from `helpers` (legacy imports at :21–28).
  - Both: whatever else ruff's F821 reports after extraction.
- **How the new files reach the code.**
  - They copy the loader from `test_transcript_reviewer_yield.py:11-31`.
  - They reach private helpers as `_mod.denials.<name>` and `_mod.review_trace.<name>`. That is the channel the shim's own comment at :36–39 sanctions.
  - They do not grow the shim's `noqa: F401` re-export block, which the final `cli.py` phase would have to unwind.
  - Any remaining `_mod.<moved name>` read in the legacy file is re-pointed the same way.
- **`test_transcript_cli_bootstrap.py`** gains three real-subprocess tests, mirroring :236–287:
  - `review-trace --help`;
  - a seeded run of the default timeline;
  - a seeded run of `--deny-summary`.
- **`select-tests.py`:**
  - It gains a `TRANSCRIPT_DENIALS_TEST_PATH` constant in the `_is_hooks_or_skills_change` exception at :478 (M10).
  - Its header comments at :65–68 and :402–403 are updated to match.
  - `test_select_tests.py`'s expected sets at :432–435 and :444–446 are updated too.
  - Grep `test_transcript_review_trace.py` for hook or SKILL.md path reads. If it has any, it gets the same treatment.

### Assumption ledger

**Root:** Two things limit review quality in this repo. The first is the reviewer read rule:
- "Read every changed file fully" is silently partial on files larger than one Read call.
- It reaches beyond the file by a fixed one hop.
- Nothing measures the defects reviews miss.

The second is the repo's oversized, high-churn files.

This plan does three things about that:
- grounds a replacement read methodology in external review practice, and gates it on a defect-recall benchmark (children A and B);
- files the measurement, decomposition, reorganization, test-split, and size-policy work as tracked issues;
- shrinks the largest file by one decomposition phase.

**Givens:**
- G1. Transcripts age out on a rolling window (`cleanupPeriodDays`, default 30 days). A before/after comparison must therefore capture its baseline while pre-change transcripts still exist. Reason: the harness owns retention. `[verified: docs/pr-cost.md:3]`
- G2. pytest's `prepend` import mode makes test-file basenames a global namespace, so new test files take the `test_transcript_*` prefix. Reason: pytest owns this behavior. `[verified: .claude/plans/transcript-analysis-decomposition.md:59-63]`
- G3. The Read tool caps a single call by tokens, not lines. Claude Code's tools reference: "When a whole-file read exceeds the token limit, Read returns the first page with a `PARTIAL view` notice". A live default Read in this session's plan-architect consult measured the cap once. On `claude/.claude/scripts/tests/test_transcript_reviewer_yield.py` (1,935 lines) it returned lines 1–956, with the notice `[Truncated: PARTIAL view — <path>: showing lines 1-956 of 1935 total (43009 tokens, cap 25000). ...]`.
  - The cap is 25,000 tokens.
  - The tool schema's default of up to 2,000 lines binds first only for a file averaging under 12.5 tokens per line.
  - That file averages about 22 tokens per line, which predicts about 1,100–1,125 lines per page. The observed page held 956, about 15% fewer. The gap is unexplained (per-page overhead or uneven density are candidates), and it is one sample. Token density varies by file, so about 1,000 lines is a rough predictor, not a boundary.
  - A "full" read of a larger file therefore takes several paged calls.

  Reason: the harness owns tool behavior. `[verified: code.claude.com/docs/en/tools-reference "Read tool behavior", fetched by the verify-sources subagent this session; live default Read in this session's consult, notice quoted above; Read tool description in this session's tool schema]`
- G4. The literature quantifies no call-graph hop count, no file size above which reviewers should stop reading whole files, and no link between file length and defects or LLM review effectiveness. Reason: this is the state of the external evidence base. `[verified: E5/E6 verify-sources searches — absence of evidence, not evidence of absence]`
- G5. GNU Stow links a package directory whose target does not yet exist as one directory symlink (tree folding). Reason: the stow tool owns this behavior. `[verified: test_stow_packages.py:167-171,188 asserts it against a real stow binary for a subdirectory under a pre-created ~/.claude; install.sh:47-50]`

**Mechanisms:**
- M1 — The read methodology (child B): the whole file when one default Read call returns it; otherwise the outline plus function context; mandatory reach beyond the file in both cases. `anchors: row2, row9, row10, row22, row41, row42, G3, G4`
  - It is heavier than the status quo's one-line rule. The lighter primitives are each rejected:
    - Keeping "Read every changed file fully": it is partial on roughly the 43 files over 1,000 lines, and its one-hop reach is hand-rolled. `anchors: row8, row11, row22, G3`
    - Function context for every file: it gives up within-file context on files that one Read returns whole, which the quality priority does not justify. `anchors: row41, row42`
    - Diff-only: it drops the causal-reach duty. `anchors: row9`
    - Locate-then-ranged-read with no default entry unit: it gives lenses no shared starting unit, and it leaves reviewers without Bash stuck with whatever artifact they are handed. `anchors: row26`
  - The size cut is the Read tool's own window, not a quality claim. A-bench validates it. `anchors: G3, G4, row54`
- M2 — Optional repo-root `.gitattributes` diff drivers inside B. `anchors: row12`
  - This is a config addition that affects the whole repo.
  - Rejected: no drivers. Function context for a method would become its whole class, and `TestReviewTrace` alone spans 3625–5209.
  - Rejected: a global `core.attributesFile` set at install. It would touch every consumer's git config.
  - B's agent rule must still work without drivers. That is why the rule includes a locate-then-ranged-read fallback.
- M3 — B merges only after two things happen. `anchors: G1, row4, row17, row54` This is this plan's default, not the engineer's decision.
  - A-bench shows B's rule non-inferior to the current rule on recall and precision.
  - A-field captures its transcript baseline.
- M4 — An epic, six new children, and one tracking issue for the remaining decomposition phases. The audit's backlog phases are linked, not re-filed. `anchors: row5, row22, row48, row51`
  - Rejected: no issues. That loses the split the engineer selected.
  - Rejected: one issue per phase. That would be about ten issues for work the governing plan already sequences.
- M5 — The first slice is review-trace. `anchors: row6, row27, row28, row38, row39, row57`
- M6 — `denials.py` as a leaf plus `review_trace.py` as a command module. `anchors: row28, row29, row32, row33`
  - Precedent is a single module per move, the lighter primitive. It is rejected for three reasons:
    - friction-count and turn-shape would each import a command-group module when they move;
    - it produces a ≈1,090-line module inside an initiative targeting 1,000;
    - it puts the hook-reading tests in the same file as the timeline tests, which blurs M10's exception.
  - Also rejected: promoting only `hook_denial_key` into `corpus.py`. `corpus.py` is transcript parsing, and the other classification functions share the same consumers.
- M7 — `review_trace` imports `reviewer_yield` by module for the reviewer predicate. `anchors: row34, row35`
  - Rejected: relocating the predicate to `corpus.py`. It touches `reviewer_yield.py` and its tests for no behavior gain.
  - Rejected: a new roster module for three names.
  - A2 revisits this if it becomes a third consumer inside the package.
- M8 — Shim re-exports for still-monolithic callers, with `compute_deny_summary_data` promoted and aliased. `anchors: row28, row33`
- M9 — Shared builders are promoted to `conftest.py`. `anchors: row30, row33` Duplicating them into two files under the DAMP exception is rejected: they are record builders, and conftest already hosts that family.
- M10 — An explicit select-tests constant for `test_transcript_denials.py`. `anchors: row32`
  - Rejected: naming the file so the existing glob matches it. Coverage by filename coincidence breaks the `test_transcript_<module>.py` convention.
  - Rejected: relying on `.sh` hook edits already selecting `scripts/tests/`. That narrows selection for non-`.sh` hook paths and SKILL.md edits, in a diff that should preserve behavior.
  - Rejected: widening the glob to `test_transcript_*.py`. That selects every transcript test file on every hook edit.
- M11 — F's mechanism is a repo-local pytest check, which the engineer approved for this repo. `anchors: row21, row43, row44` It is lighter than a stowed hook, which would reach every consumer's repos.
- M12 — One `code-writer` dispatch that moves code by line-range extraction, never by retyping. `anchors: row29, row31` Separate production and test dispatches are rejected: both halves need the same coupling map, and they must land green together.
- M13 — A-bench, a local-only replay benchmark, is A's primary instrument. `anchors: row17, row41, row52, row53, row54, G1`
  - It is heavier than reusing existing reports. Each lighter primitive is rejected as the primary quality measure, though A-field keeps the first two as field signals:
    - reviewer-yield's findings-found rate: it counts dispatches that reported something, cannot see a missed defect, and rises with noise. `anchors: row17`
    - cited-path edit overlap and review-ledger dispositions: these are precision proxies with no recall. `anchors: row17, row52`
    - an observational before/after window: model, prompt, and PR-mix changes confound it, and transcript retention bounds it. `anchors: G1`
- M14 — D reorganizes `_lib.sh` in-file, as the audit's Phase 3 specifies, instead of splitting it. `anchors: row47, row48, row49, row50`
  - In-file sections are the lighter primitive.
  - The heavier split is rejected on quality gain, not feasibility:
    - every hook sources the whole library, so siblings narrow nothing;
    - a split re-exposes the per-entry-link hazard (row 50);
    - the audit already decided.
  - The `_config.sh` precedent (row 49) shows a split is mechanically possible.
- M15 — F's exceptions carry per-file ceilings, each with a reason, plus the tracking issue where a split is pending. A ceiling is exact in both directions: an exception file passes only when its physical line count equals its ceiling. `anchors: row22, row45, row46, row48, row57, row58, row59, row60, row61, row62, row63`
  - Exactness and both directions are the engineer's decisions (rows 60, 62). Physical-line counting and the failure message are this plan's defaults (row 63).
  - Rejected: a grandfather list with no ceiling. Exceptions would grow unbounded, as `_lib.sh` did. `anchors: row48`
  - Rejected: one limit with no exceptions. That means 43 immediate failures, including files with structural reasons not to split. `anchors: row22, row59`
  - Rejected: upward-only ceilings, the semantics of the existing length gates. A shrink would leave slack that later growth spends without a reviewed edit. `anchors: row20, row62`
  - Rejected: non-comment counting. It needs a comment classifier for each language, and shell's heredocs and quoted `#` defeat a line-based one. It also departs from what a Read call pays, since comments cost Read tokens too. Physical counting penalizes comment-heavy `_lib.sh`, and its exception row absorbs that. `anchors: G3, row48, row63`

**Assumptions:**
1. `[engineer-verified: "I think those should be split into core and files for subcommands"]`
2. `[engineer-verified: "There is clearly a best practice for reviewing diffs of code, and we shouldn't hand-roll it."]`
3. `[engineer-verified: "Measure in parallel (Recommended)"]`
4. `[unverified]` The selected option's description ("don't block the transcript-analysis split on it") was the session's proposal, not the engineer's words. This plan adopts it and adds M3's gate on B, which that description did not address.
5. `[engineer-verified: "Epic + child issues, then first slice (Recommended)"]`
6. `[unverified]` Naming review-trace as the first slice was the session's option description. M5 re-derives it.
7. `[engineer-verified: "pylint is a great signal, but I don't deem it sufficient without more research."]` The tag covers one point: pylint's default alone did not settle the threshold. The mechanism is approved (rows 43, 44), and rows 60 and 61 record the limits the engineer later chose.
8. "Read every changed file fully", or equivalent wording, appears at these sites. `[verified: E1; re-read :63, :106, :82]`
   - `staff-backend-engineer.md:63`
   - `ciso-reviewer.md:51`
   - `staff-analytics-engineer.md:80`
   - `staff-frontend-engineer.md:57`
   - `staff-platform-engineer.md:63`
   - `staff-data-engineer.md:56`
   - `staff-product-engineer.md:63`
   - `staff-sdet.md:55`
   - `comment-discipline-reviewer.md:106`
   - `code-review/SKILL.md:82`
9. `code-review/SKILL.md:36` holds spawns responsible for defects the change "causes, activates, or newly reaches". It also names "the full-file read required elsewhere in this skill". `[verified: code-review/SKILL.md:36]`
10. The quoted lines from Google, the Anthropic code-review plugin, Anthropic security-review, git, Bacchelli & Bird, and Pascarella are accurate. `[verified: primary sources fetched by the verify-sources subagent (E6); not re-fetched by plan-architect]`
11. No source sets a hop count. `[verified: E6 — absence]`
12. Git's function detection for `-W` follows the hunk-header funcname rules. Without a `diff=<driver>` attribute, the default treats only lines starting with a letter, `_`, or `$` as function headers, so an indented method is not one. Git ships built-in `python`, `bash`, and `markdown` drivers. `[unverified — B verifies against git help gitattributes § "Defining a custom hunk-header"]` The repo has no `.gitattributes`. `[verified: Glob]`
13. Reviewer-dispatch spend is roughly 10–15% of total, from a hand-sum rather than a computed corpus total. `[verified: docs/cost-levers-considered.md:611-616]` This is now a secondary A-field figure.
14. A main-thread base-checklist read costs more per token than the same read in a subagent, because it stays in context for the rest of the session. `[unverified — A-field measures]` This is secondary.
15. `read-scope` computes `file_path` internally but prints none, and its only split is main versus subagent. Its group spans shim :4418 to about :4870. `[verified: E2; grep]`
16. A partial Read's notice carries the file's total line count and token count, which would make A-field's size join exact for partial reads. `[verified: G3's live Read]` Whether transcripts store that notice, and what a whole-file Read result carries, is `[unverified — A's plan checks a real transcript]`.
17. `reviewer-yield` has two tables. `[verified: docs/transcript-analysis.md:257, :285-287]`
    - It classifies each reviewer dispatch's verdict as findings-found, zero-finding, or unclassified.
    - It reports whether the paths a reviewer cited were later edited.

    That makes it a volume signal plus a precision proxy, not a quality check: it cannot count a defect no reviewer reported.
18. File-length defaults across the surveyed tools. No tool sets a separate default for test files. `[verified: E5]`
    - pylint: 1000
    - SonarQube: 1000
    - SwiftLint: warning 400, error 1000
    - Checkstyle: 2000
    - ESLint: 300
    - ruff: no rule
    - ShellCheck: no rule
19. No primary study links file length to defects or to LLM review effectiveness. `[verified: E5 — absence]`
20. The existing length gates deny only when NEW > LIMIT and NEW > OLD. `[verified: E4, _lib.sh:1627, :1711 — not reopened]`
21. Anything under `claude/.claude/hooks/` installs to every stow consumer. `test_ticket_reference_discipline.py` is an existing repo-local scanner of every tracked `.py`/`.sh` file, with its own select-tests exception. `[verified: repo CLAUDE.md "Plans in this repo affect all stow users"; select-tests.py:71-76]`
22. Files over 1,000 lines. `[verified: this revision's count for sizes; E4 for churn]`
    - 43 `.py`/`.sh` files are over 1,000 lines by this revision's count (ripgrep line counts over the worktree, which honors `.gitignore`). E4 reported 41. F1 re-derives the list from `git ls-files` and records the command.
    - Of the 43, 8 are non-test files and 35 are test files.
    - 17 are over 2,000 lines: 2 non-test (`transcript-analysis.py`, `_lib.sh`) and 15 test files.
    - The churn figures are as E4 lists them.
    - At G3's one measured density, a default Read returns about 1,000 lines, so these 43 files approximate the set that one Read call cannot return whole. Each file's token density sets its actual boundary. `[unverified — inferred from G3's one sample]`
23. Exposure, measured as size × 90-day commits. The top two files dominate. `[verified: arithmetic over row 22]`
    - `test_transcript_analysis.py`: ≈2.33M
    - `transcript-analysis.py`: ≈1.05M
    - `test_lib.py`: ≈265k
    - `_lib.sh`: ≈227k
24. `split_command_segments` and `shell_commands` appear nowhere in the tree. `[verified: grep]`
25. A new hook-side `_*-lib.sh` fails CI today, because `_HELPER_LIBRARY_NAMES` lists exactly `_lib.sh` and `_config.sh`. `[verified: test_hook_alignment.py:85; .claude/rules/bash-unit-test-seams.md]` D no longer needs this widened. #978 resolved `_lib.sh` parse cost through consolidation. `[verified: E4; issue not reopened]`
26. `claude/.claude/CLAUDE.md:65` lists "reviewing it" as a case for reading a whole file. `[verified: grep]`
27. Review-trace's production code sits at shim lines 1011–1025 and 1036–2107. Lines 1005–1009 and 1027–1034 belong to other groups. The region has no `__file__`, `global`, or `lru_cache` use. `[verified: Read/grep]`
28. Cross-group couplings `[verified: grep]`:
    - `_compute_deny_summary_data` is called from :8061.
    - `hook_denial_key` is called from :10615 and :10628.
    - `_drop_denial_command_flag_values` is called from :10253.
    - `REVIEW_TRACE_SKILLS` is read at :13087.
    - `_is_reviewer_subagent_type` comes in from `reviewer_yield` (:139, :150).
    - The module-level `_parse_ts` call is at :1370.
    - `render._sanitize_table_cell` is used at :1489.
29. The test slice ranges are as listed in the First slice section. `[verified: class/def listing]`
30. Friction-count tests also use `_hook_deny` and `_hook_deny_current` (:18411–18965). `_review_trace_args` is also used at :18625, :20193, :20220, :20234, :20286, :20319, and :20419. `[verified: grep]`
31. No test uses `setattr` on a review-trace or denial name on `_mod`, so no patch needs retargeting. `[verified: grep]`
32. Test-selection facts `[verified: select-tests.py:69, :285-286, :478, :491; test_select_tests.py:116-139, :422-446]`:
    - `TRANSCRIPT_ANALYSIS_TEST_GLOB` (:69) is used only by `_is_hooks_or_skills_change` (:478).
    - A `.sh` hook edit also selects `scripts/tests/` (:491).
    - The completeness scanner resolves only module-level `Assign` nodes. An imported `HOOKS_DIR` is invisible to it.
33. Precedent for a command-group move `[verified: transcript-analysis.py:40-155; reviewer_yield.py:1-19; test_transcript_cli_bootstrap.py:236-287; conftest.py:586]`:
    - dependencies are imported by module;
    - the shim re-exports names for monolithic callers;
    - a promoted public name is imported with an `as _name` alias;
    - each moved command gets subprocess bootstrap tests;
    - args builders are promoted to conftest.
34. Reading a private name across modules by attribute has precedent: `redaction.py` reads `scope._redaction_ordinals`. `[verified: docs/transcript-analysis-architecture.md:47-48]`
35. The package never imports the shim. `[verified: docs/transcript-analysis-architecture.md:14-16]`
36. `_UNCONDITIONAL_HEADER_CASES` stays in the legacy file until the final phase. `[verified: transcript-analysis-decomposition.md:157-161]`
37. New modules under `scripts/transcript_analysis/` need no stow change, and they add no exposure beyond what the package's creation already carried.
    - On a standard install, `~/.claude/scripts` is one folded symlink: `install.sh` pre-creates only `~/.claude` and `~/.local/bin` (G5). Repo `CLAUDE.md:57` states that changes under `claude/.claude/**` go live on `git pull`. `[verified: install.sh:46-51; CLAUDE.md:57; G5]`
    - Where a consumer's `~/.claude/scripts` was a real directory, the same rule folds `transcript_analysis/` itself into one symlink, so new modules inside it are still visible. `[unverified — inferred from G5, not exercised]`
38. Cache-rebuild is the largest remaining group: production ≈1,283 lines per E3, tests ≈8547–12676. `[verified: class listing]` Its couplings are unmapped. `[unverified]`
39. The denial-label registry changes every time a gate hook is added, which ties this group's churn to hook churn. `[unverified — inferred from test_transcript_analysis.py:19202's completeness test; git log -L over _DENIAL_HOOK_LABELS would confirm]`
40. `scripts/tests/` is a package. New test files import conftest with `from .conftest import ...`. `[verified: test_transcript_reviewer_yield.py:11; .claude/rules/test-tree-packaging.md]`
41. `[engineer-verified: "first and foremost optimizing diffs agents look at to maximize quality and thoroughness of findings according to best practices, not to minimize tokens"]` The tag covers two points: the reviewer methodology is chosen for finding quality and thoroughness according to best practice, and token cost is not the objective.
42. `[engineer-verified: "the blanket rule of reading the entire file with the change was a signal that we should reevaluate the methodology, not a sign to put token optimization at the top of the list. Quality of code an repo over all."]` The tag covers two points: the read methodology is re-evaluated, and code and repo quality outrank token cost.
43. `[engineer-verified: "yes this sounds good, for this particular repo, yes."]` This answered the session's recommendation of a repo-local pytest check with a baseline of current offenders. The tag covers that recommendation, scoped to this repo.
44. `[engineer-verified: "ok pytest it is"]`
45. `[engineer-verified: "we need to see how many offenders there are and how reasonable it is to keep lines under 1000 before actually imposing the limit."]` The tag covers two points: no limit is imposed before the assessment, and 1,000 is the candidate under assessment, not an approved limit.
46. `[engineer-verified: "we might need exceptions and different limits for those exceptions."]` The tag covers one point: F's design must consider per-file exceptions with their own limits. It does not decide that any exception exists.
47. `[engineer-verified: "for example, _lib.sh - I though there was a reason that couldn't be decomposed."]` This records the engineer's recollection. Row 48 supports it: the audit ruled out a file split.
48. The audit's Phase 3 chose in-file reorganization, and "Phase 3 must not create a new file", for four stated reasons. `[verified: findings.md:325, :332-345]`
    - Phase 3 has not landed: `_lib.sh` has no section delimiters and no header index. `[verified: grep and Read of _lib.sh:1-23; 2026-08-22 discovery audit findings.md:70, "Status unchanged (worse)"]`
    - `_lib.sh` grew from 1,232 lines at the audit's baseline, to 1,497 at the discovery audit, to 3,554 now. `[verified: findings.md:269; discovery findings.md:70; _lib.sh:3554]`
49. Since the audit, `_config.sh` shipped as a hook-side sibling. `_lib.sh` sources it with `if ! . "$(dirname "${BASH_SOURCE[0]}")/_config.sh"; then return 1; fi`, and `_HELPER_LIBRARY_NAMES` names it. `[verified: _lib.sh:21-23; test_hook_alignment.py:85]`

    That pattern answers three of the audit's four objections `[unverified — inferred from those lines, not exercised]`:
    - The test exclusion is a one-name edit.
    - `test_lib.py` sources `_lib.sh`, which sources the sibling.
    - Each consumer's existing source guard fires, so no per-hook bootstrap is needed.
50. The fourth objection, stow, is narrower than the audit states.
    - On a standard install, a new file under `claude/.claude/hooks/` goes live on pull (G5; `install.sh:46-51`; `CLAUDE.md:57`). `[verified: those lines]`
    - A consumer whose `~/.claude/hooks` was a real directory before install gets per-entry links, and would miss a new sibling until re-running `install.sh`. `[unverified — inferred from G5]`
    - Whether `install.sh` at the audit's baseline behaved differently is also unverified. `[unverified — git history not read]`
51. How the audit's Phases 4a and 4b relate to the governing plan. `[verified: findings.md:327-328, :347-352; transcript-analysis-decomposition.md:40-49; grep of that plan finds no citation of the audit]`
    - Phase 4b (a thin single-file entry point over a package) is the governing plan's shim design.
    - Phase 4a (split the test file first) is superseded by the governing plan's per-phase co-move of source and tests.
    - The audit's verification invariant still applies: node IDs set-equal, and no edits to existing test assertions.
52. `review-ledger.sh` records a per-finding ADDRESS or DEFER disposition. `[verified: review-ledger.sh:24-26, :152-166]` Its storage location and retention are `[unverified]`.
53. `evals/` is the repo's home for local-only, never-CI model measurements that run `claude -p`. `[verified: evals/README.md:10-34]`
54. A controlled replay isolates the read rule as the only varying input. Its defect set can be mined from git history, which does not age out the way transcripts do. `[unverified — the SZZ method (Śliwerski, Zimmermann & Zeller, 2005) is named from memory, not fetched; A's plan verifies it]`
55. Long-context research and Anthropic's context-engineering guidance support targeted context over whole large files for LLM reviewers. `[unverified — not fetched; B's verify-sources pass quotes these or drops them]`
56. A default Read of a file over the token limit tells the reader it stopped early, with a `PARTIAL view` notice. `[verified: G3's docs quote and G3's live Read]` M1 keys its entry branch on that notice. The line count from #1103's inputs or a Grep count only predicts the branch. The notice embeds per-read numbers, so B's agent bodies key on the `PARTIAL view` token rather than quoting the whole notice.
57. Decomposition files against the size limits. `[verified: line counts; arithmetic over rows 27 and 29's ranges]`
    - `transcript_analysis/cost.py` (1,230 lines) and `test_transcript_cost.py` (3,972) already exceed the 1,000-line limit (rows 60, 61).
    - This branch's new modules come to about 385 lines (`denials.py`) and about 700 (`review_trace.py`). At G3's density, both fit one default Read call. `[unverified — inferred from G3's one sample]`
    - This branch's new test files come to about 1,895 lines (`test_transcript_review_trace.py`, after the rebase conflict resolution ported in 15 more tests) and about 1,220 (`test_transcript_denials.py`). Both exceed the 1,000-line test limit. At G3's density, neither fits one default Read call. `[unverified for the Read fit — inferred from G3's one sample]`
58. `install.sh` has 995 lines and `marker.sh` has 914. `[verified: line counts]`
59. `install.sh` sources `_config.sh` standalone, by its repo-relative path (after stow in the current line order, `install.sh:348,352` then `:420`, so stow timing is irrelevant). `_config.sh` is kept dependency-free of the rest of `_lib.sh` for its standalone consumers. Its bash–Python parity is pinned by `test_config_parser_parity.py`. `[verified: _config.sh:2-12]`
60. `[engineer-verified: "I think exact ceilings are important. And I think 1000 lines for prod code and 2000 lines for test code is reasonable."]` This answered the plan's open questions on F's limits and on exact versus headroom ceilings. The tag covers two points: exception ceilings are exact rather than carrying headroom, and the production-code limit is 1,000 lines. The message's "2000 lines for test code" was replaced by the engineer's later selection in row 61. Row 45's timing point still governs: F2 imposes the limits only after F1's assessment. Row 45's reading of 1,000 as a candidate covers only its own quote; this row records the later approval.
61. `[engineer-verified: "1,000 for tests too (Recommended)"]` This label answered "With that measurement, what limit should test files get?", asked after the session reported G3's Read measurement. The tag covers one point: the test-file limit is 1,000 lines. The question and the option descriptions were the session's. Asked to confirm against row 60's typed "2000 lines for test code", the engineer selected `[engineer-verified: "Yes, 1,000 for tests"]`.
62. `[engineer-verified: "Both directions (Recommended)"]` This label answered "Should exact ceilings be exact in both directions, so a file that shrinks must also lower its ceiling?" The tag covers one point: ceilings are exact in both directions, so a file that shrinks must also lower its ceiling. The check's equality test is M15's implementation of it.
63. `[unverified]` These parts of F's design are this plan's defaults, proposed by the session, not decided by the engineer:
    - counting physical lines rather than non-comment lines (the engineer was not asked);
    - the failure message's content, remedy order, and prohibitions;
    - failing a row whose file no longer exists or whose ceiling is at or under the general limit.

    F1's table records both line counts, so the engineer can change the unit when F1 presents the assessment.

## Critical files

**GitHub issues.** The session files these with `gh issue create` before the code dispatch. They are not repository files.
- Before filing C and D, search open issues for the audit's Phases 3, 4a, and 4b. Link any issue found instead of re-filing it.
- The epic, and children A–F:
  - A carries A-bench and A-field.
  - D is the audit's Phase 3 in-file reorganization.
  - F carries F1 and F2.
  - The ordering checkboxes and "Blocked by" lines are as the Epic and children section specifies.
- Link comments on #1015, #1103, #1041, #1042, and #885.
- A re-triage comment on #955 that cites row 24.
- The 2026-08-10 audit report is linked from C and D.

The repo is public, so the issue bodies are too. They carry no per-account figures, and they cite doc sections for spend shares instead of restating pooled numbers.

**Create:**
- `claude/.claude/scripts/transcript_analysis/denials.py`
- `claude/.claude/scripts/transcript_analysis/review_trace.py`
- `claude/.claude/scripts/tests/test_transcript_denials.py`
- `claude/.claude/scripts/tests/test_transcript_review_trace.py`

**Modify:**
- `claude/.claude/scripts/transcript-analysis.py`
  - Remove the moved lines.
  - Update the import block as the First slice section specifies.
  - Update the comment at :132–140.
- `claude/.claude/scripts/tests/test_transcript_analysis.py`
  - Remove the moved ranges.
  - Re-point the remaining `_mod.<moved name>` reads.
- `claude/.claude/scripts/tests/conftest.py`
  - Add three builders.
  - Add `test_transcript_denials.py` and `test_transcript_review_trace.py` to the module docstring's consumer list (:1–7).
- `claude/.claude/scripts/tests/test_transcript_cli_bootstrap.py`: add three review-trace subprocess tests.
- `claude/.claude/scripts/select-tests.py`
  - Add the new constant and its exception-tuple entry.
  - Update the two header comments.
- `claude/.claude/scripts/tests/test_select_tests.py`: update two expected sets.
- `docs/transcript-analysis-architecture.md`
  - Add `### \`denials.py\`` and `### \`review_trace.py\`` sections. The drift test requires both headings.
  - Update the exception paragraph at :14–18. Both `denials` (via `hook_denial_key` and `_drop_denial_command_flag_values`) and `review_trace` (via `compute_deny_summary_data`, `cmd_review_trace`, `REVIEW_TRACE_SKILLS`) join the modules the shim imports back into. Record the `review_trace → reviewer_yield` import there as well.
  - Update :92–93, since review-trace no longer reaches the predicate through the shim.
  - Update the Tests section at :124–135. It is stale and names only `test_transcript_cost.py` as a per-group file.

**Reuse:**
- `reviewer_yield.py:1-19`, for the module docstring and the by-module import shape.
- `test_transcript_reviewer_yield.py:11-31`, for the relative conftest import and the shim loader.
- `conftest.py`'s record builders (`_asst`, `_user_msg`, `_tool_result`, `_write_jsonl`, `_write_subagent_dispatch`), and `_reviewer_yield_args` at :586 as the promotion precedent.
- `test_transcript_cli_bootstrap.py`'s `_run` helper and its seeded-dispatch pattern (:236–287).

**Dispatch.** One `code-writer` dispatch covers every file above. Its instructions:
- Move code by line-range extraction: slice the First slice ranges out of the unmodified files with a scratch script. Never retype moved code.
- Let ruff's undefined-name check (F821) drive each `module.` prefix.
- Rewrite moved test references from `_mod.X` to `_mod.denials.X` or `_mod.review_trace.X` mechanically. Change nothing else on an assertion line.
- Leave these alone, because they resolve through the shim's re-exports unchanged:
  - `_mod._compute_deny_summary_data` (legacy test :13083);
  - the `_mod.cmd_review_trace` call sites in the legacy file (:18609–18694, :20165–20347).
- Do not add a `denials.` prefix inside `_cost_ledger_report` (:8062) or `_friction_signals` (:10706). Both have a local variable named `denials`, which shadows the new module name. The shim imports the names it needs by name.
- Verify with Verification steps 1 through 6, plus step 8.

## Verification

Run everything from the worktree root. `<venv>` means the worktree-relative `.venv` path documented in README.md's Tests section.

0. **Baseline, before the dispatch.** Write outputs to a scratch directory and print only counts:
   - copies of the unmodified shim and legacy test file (step 8 needs them);
   - the collected test IDs from `<venv>/bin/pytest --collect-only -q claude/.claude/scripts/tests/test_transcript_analysis.py`, and the trailing count;
   - `python3 claude/.claude/scripts/transcript-analysis.py <sub> --help` for the top level and every subcommand;
   - `wc -l` for the shim and the legacy test file.
1. **Scoped suite.** Run `<venv>/bin/python3 claude/.claude/scripts/select-tests.py`. For this diff it selects all of `scripts/tests/` (select-tests.py:375). Because the diff touches `docs/`, it also selects `hooks/tests/` and `skills/tests/` (:496).
2. **Test parity.** Compare collected test IDs with the file prefix stripped (`Class::test[param]`), as sorted lists. Step 0's list from `test_transcript_analysis.py` must equal the combined list from `test_transcript_analysis.py`, `test_transcript_denials.py`, and `test_transcript_review_trace.py`, duplicates included.
   - A set match proves no test is lost.
   - The duplicate-preserving match proves none is doubled.
   - The three new bootstrap tests are counted separately.
3. **CLI parity.** Every `--help` output must be byte-identical to step 0's capture.
4. **Hook runtime path.** `nudge-error-mode-analysis.sh:151` runs `friction-count`, which now gets `hook_denial_key` from `denials.py`. Confirm step 1 ran `claude/.claude/hooks/tests/test_nudge_error_mode_analysis.py`. If it did not, run that one file and record the gap as a rule-table observation in the PR body.
5. **Lint.** Run `<venv>/bin/ruff check claude/.claude/scripts/`.
6. **Leftovers.** No moved name may still be defined in the shim.
   - Before the move, list every top-level `def` and `NAME =` binding in shim lines 1011–1025 and 1036–2107 into a scratch file.
   - After the move, `git grep -nE '^(def )?(<those names, |-joined>)\b' claude/.claude/scripts/transcript-analysis.py` must return nothing.
   - Constants count, not only functions: a duplicated classification table left behind would drift on its next edit.
7. **Move fidelity, after the commit.** Run `git blame -C -C -s` on each of the four new files. Lines attributed to the new commit should be limited to:
   - module headers;
   - imports;
   - `module.` prefixes;
   - the `_mod.<module>.` test re-points;
   - the one public-name promotion.

   Put the per-file counts in the PR body. Name this command in the `/code-review` spawn prompts, together with a statement that the diff is a move meant to preserve behavior. That lets reviewers verify the move mechanically and spend their judgment on the lines that were not moved.
8. **Assertion preservation** (the audit's invariant, `findings.md:347-352`).
   - In scratch copies of the two new test files, rewrite `_mod.denials.` and `_mod.review_trace.` back to `_mod.`.
   - Diff each moved range against the same range in step 0's legacy copy. Only the file header, imports, and loader may differ.
   - The legacy file's remaining lines may differ from step 0's copy only by the removed ranges and the same re-points.
   - Any other difference on an `assert` line fails the step.
8a. **Section headers.** Run `git grep -n -A2 '^# ------' --` on the legacy test file and the two new test files. Each header block must sit directly above the section it names. No new file may start or end with another section's header, and the legacy file must keep the audit-routing and multi-account headers.
9. **Sizes for the PR body.** Report measured `wc -l` for the four new files and the two shrunk files. Do not estimate. State that both new test files exceed the 1,000-line test limit (row 61), that this branch adds no check, and that F1 records them (Out of scope).
10. **Issues.** Run `gh issue view <epic>` and check:
    - The task list references A–F and #1015.
    - The two A checkboxes come before B's entry.
    - B's body opens with its blocker line.
    - D's body cites the audit's Phase 3, records row 49's correction, and carries no new-file prerequisite.
    - F's body separates F1 and F2, with F2 blocked on F1's assessment. It records the engineer's decisions (rows 60–62) and marks row 63's items as plan defaults.
    - Each child links back to the epic.

## Out of scope

- **Implementing A, B, D, E, or F.** Each gets its own issue and `/plan-it`.
- **Splitting `_lib.sh` into sibling files.** D reorganizes it in-file instead (M14).
- **Editing the 2026-08-10 audit report.** It is a dated record. Its partly stale stow reasoning is corrected in D's issue.
- **Relocating `_is_reviewer_subagent_type`** (M7). A2 revisits it if it becomes a third consumer.
- **Consolidating the three overlapping skill sets:** `REVIEW_TRACE_SKILLS`, `review_rounds.REVIEW_SKILLS`, and `AUDIT_JUDGMENT_SKILLS`. That would change behavior, and this diff must preserve it.
- **Splitting the two new test files to bring each under 1,000 lines.** `test_transcript_review_trace.py` (≈1,895) and `test_transcript_denials.py` (≈1,220) exceed the test limit (row 61) and, at G3's density, one default Read call (row 57). This branch adds no check, so nothing fails. Splitting `TestReviewTrace` needs a seam map this plan lacks, and a further split of the denials tests could need a second select-tests constant (M10). Keeping this PR a pure move keeps Verification steps 2, 7, and 8 mechanical. F1 gives both files a split-pending verdict, which feeds E.
- **An import-direction guard test for `review_trace.py`'s back-import,** like `test_transcript_analysis_cost_import_direction.py`. Reviewer-yield's phase added none either. It is a candidate for C's final phase.
- **Converting the line-number citations in `review_rounds.py`.** This move shifts them by about 1,090 lines, but they had already drifted (E3). Touching the file now would add a whole-file review read for a comment-only change that C's final phase redoes anyway.
- **Moving friction-count or turn-shape.** They are their own phases. This slice serves them only through shim imports.
- **Any change to the CLI's subcommands, flags, or output.**
- **Fixing #955.** It gets re-triaged instead (row 24).
