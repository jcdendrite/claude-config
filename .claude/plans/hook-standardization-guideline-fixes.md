# Hook-standardization guideline fixes (GH-815)

## Context

PR #814 ("Hook-family standardization Phase 1: GH-783 Phase 2 command-matcher
helpers", merged as `0ce2cfe`, +1491/-159 across 26 files) landed with CLAUDE.md
guideline violations spread across its shell hooks, its test files, and its
durable comments. GitHub issue GH-815 names one instance: the test class
`TestGh483Invariants`, named after the tracker ID it closed rather than the domain
it tests. The engineer calls that instance "one symptom of a much broader
problem." The engineer deferred the audit deliberately, rather than interrupt the
multi-step plan the authoring agent was executing at the time. Why now: the
violations are merged to `main`
and every stow consumer pulls them; the same authoring pattern is about to run
again for Phase 2 of `.claude/plans/hook-family-standardization.md`, so a fix that
does not also close the detection gap just re-accrues. Intended outcome: the
merged violations are remediated, and the review pipeline is changed so this class
of miss is caught next time rather than found by the engineer's eye.

### Audit evidence

Five parallel read-only audit lanes ran against the merged diff before this plan
was written. Their findings are referenced below by lane tag:

- **Lane A** (`comment-discipline-reviewer`): A1 — 15 sites where the plan-file
  label `Phase 2` leaked into permanent comments and test docstrings; A2 — 3
  durable docstrings using prior-version framing; A3 — 2 low-severity residues.
- **Lane B** (naming and function extraction): B1 — 2 ticket-ID-named identifiers
  repo-wide, later corrected to 3 when `/plan-review` ran the proposed detector
  against the merge-base (row 21a); B2 — 2 generic names; B3 — 4 extraction
  candidates.
- **Lane C** (single source of truth, grounding): C1 — byte-identical 12-line
  block in the two length hooks; C2 — the same dispatch skeleton at 4 sites; C3 —
  12 raw tri-state literals; C4 — a sentence in three places.
- **Lane D** (`staff-sdet`): D1 — triplicated fixture; D2 — `gh`-version-dependent
  test in the unit tier; D3 — silently-lenient fence regex; D4/D5 — coverage gaps.
- **Lane E** (pipeline root cause): E1 — the comment rule exists but its worked
  examples do not match the realized shape, and the agent has no exhaustiveness
  mechanism; E2 — no rule anywhere governs identifier naming; E3 — diff truncation
  ruled out; E4 — a per-commit dispatch-suppression candidate that is
  unverifiable; E5 — no deterministic enforcement exists.

## Approach

Remediate the violations #814 merged, in three sequenced waves of `code-writer`
dispatches, then close the detection gap with **one deterministic primitive plus
one targeted repair**: a repo-wide static pytest scan that mechanically rejects
tracker-ID-named identifiers and plan-phase-qualified labels in source comments,
and two bounded edits to `comment-discipline-reviewer.md` that fix the worked
example that made `GH-783 Phase 2` look like an ordinary citation and turn
"exhaustive enumeration is the point" from an assertion into a grep operation. No
new hook, and no new CLAUDE.md or `.claude/rules/` prose — the scan test's
assertion message is the rule's only home, per this repo's own routing table.

The two pipeline layers overlap on purpose, and the overlap is not the
compounding-defensive-layers failure. They are separated by *decidability and
timing*, not by file type: the scan test deterministically rejects the two
mechanically-decidable shapes (a tracker ID glued to a phase qualifier; a tracker
ID inside an identifier) wherever they appear in `.py`/`.sh`, and it fires on
every test run. The reviewer keeps its existing full scope — including those same
shapes in those same files — because it also owns everything a regex cannot
decide: invented codenames, prior-version framing, wrong altitude, and `.md`
durable docs the scan deliberately never reads. Neither layer closes a gap the
other created, and neither is told the other exists, so no coupling is introduced:
`comment-discipline-reviewer`'s body gets no carve-out and its Scope section
(`:15-24`) is left unchanged. Redundant coverage on the decidable subset is the
intended outcome, because the deterministic layer catches what an LLM reviewer
demonstrably missed once already.

### Assumption ledger

**Root problem.** PR #814 merged 20 CLAUDE.md guideline violations (15 plan-phase
labels, 3 prior-version docstrings, 2 low-severity residues) plus 1
ticket-ID-named identifier into `main`, where every stow consumer pulls them —
alongside 2 more ticket-ID-named identifiers that predate it and fall inside the
same repo-wide sweep; and
no deterministic check exists that would have caught either class, so the same
authoring pattern re-accrues them on the next phase of
`.claude/plans/hook-family-standardization.md`.

**Givens** (fixed beyond this plan's reach):

- **G1.** `agent-reviews/` is gitignored and not retained post-merge, so the
  per-commit `comment-discipline-reviewer` reasoning from #814 is unrecoverable. The
  artifact is gone — no read recovers it. Consequence: Lane E's E1 diagnosis and
  E4 candidate stay inferences and are treated as such below.
- **G2.** The installed `gh` CLI version is a user-installed external dependency
  this repo does not pin (`_lib.sh:799-804` records the 2026-09-02 fetch by hand).
  Finding D2's non-hermeticity therefore cannot be fixed by pinning; it needs a
  test-tier decision, which is why it defers.

**Rows:**

1. Scope is remediation **and** the review-pipeline fix in one plan.
   `[engineer-verified]` `anchors: root`
2. Blast radius is the 26 files #814 touched, for every violation class, plus a repo-wide
   sweep for ticket-ID-named identifiers specifically. `[engineer-verified]`
   `anchors: root`
3. Each in-scope-but-deferred finding becomes its own GitHub issue carrying
   file:line and the cited rule. `[engineer-verified]` `anchors: root`
4. The orchestrating session never edits code; every implementation dispatch goes
   to `code-writer`. `[engineer-verified]` `anchors: root`
5. The pipeline mechanism is a static pytest scan in the existing suite, not a new
   hook. **Over-powered-primitive check** — a new hook is the heavy option
   (`deny-private-project-refs.sh`'s precedent carries a 3261-line test file, per
   Lane E). Four lighter or alternative primitives were enumerated: (a) **a static
   pytest scan — chosen**; the repo already hosts repo-wide static scans in
   `claude/.claude/hooks/tests/` (`test_nudge_transcript_toolkit.py:116` rglobs
   every repo `*.md` and reads content; `test_shellcheck.py` lints every tracked
   shell file; `test_agent_roster.py` globs `plugins/*/agents/*.md` — all three
   cited in `select-tests.py:99-135, 270-300`), and CI runs `pytest
   claude/.claude/ plugins/` on every PR (`.github/workflows/tests.yml:158-166`);
   (b) **agent-body edit alone** — fails the engineer's "caught next time" bar,
   since an LLM reviewer instruction is not deterministic and E1 shows this exact
   instruction already existed and did not fire; (c) **extend
   `deny-private-project-refs.sh` with a naming detector** — fails because it
   welds naming discipline onto a hook whose charter is privacy redaction, and its
   allowlist deliberately passes `GH-` (repo-root `CLAUDE.md`, Redact section), so
   the detector would have to fight its own host's design; (d) **a new CLAUDE.md
   or `.claude/rules/` line** — fails per row 6.
   `[verified: .github/workflows/tests.yml:158-166; select-tests.py:99-135, 270-300 — read this session]`
   `anchors: root`
6. No new prose rule is written for the naming convention.
   `ai-instruction-and-memory-files/SKILL.md:166`'s routing table answers this
   directly: "Rule already enforced by a hook or structural test → **Nowhere — the
   hook enforces it; prose is load**." The rule text lives in the scan test's
   assertion message, which is what a violator actually reads.
   `[verified: ai-instruction-and-memory-files/SKILL.md:157-166 — read this session]`
   `anchors: row5`
7. The `comment-discipline-reviewer.md` repair is bounded to two body edits, both
   **additive**. Its Scope section (`:15-24`) is left unchanged, so it keeps
   covering `.py`/`.sh` comments — the redundancy with row 5's scan is intended
   (see Approach) and no carve-out naming the scan is added, since telling the
   agent what another mechanism covers is the coupling this design avoids.
   Frontmatter needs no change — `Grep` is already in `tools:` at `:6`, and
   `description` at `:5` is unchanged.
   `[verified: comment-discipline-reviewer.md:1-6, 15-24, 58-61, 74-90 — read this session]`
   `anchors: row5`
7a. Edit (b) to "How to work" item 4 **adds** the grep step rather than replacing
   the existing text. The current item protects two things: do not stop at the
   first violation *type*, and sweep the whole diff across all angles. Replacing
   it with a grep-count operation would drop the first guarantee — an agent could
   faithfully count every repeat of the first label it found and never look for a
   structurally different violation elsewhere. Keep the existing sweep sentence
   verbatim; append the repeat-enumeration step for confirmed candidates.
   `[verified: comment-discipline-reviewer.md:74-90 — read this session]`
   `anchors: row7`
7b. Edit (a)'s worked example uses an abstract shape (`<TICKET> Phase <N>`), not
   the literal `GH-783 Phase 2`, per `.claude/rules/skill-and-agent-self-review.md`'s
   abstract-first convention ("keep the failure mode and the fix; drop the
   trigger's identity"). That rule's heading says "skill"; applying it to an agent
   body is a deliberate read, and it also keeps the example from tripping the very
   scan row 5 adds. `anchors: row7`
7c. The scan fires at test-run time, not at keystroke time, so a hand-authored
   identifier is caught at the next test run rather than at authorship. This is
   accepted rather than closed with a write-time rule, because the authoring path
   this plan actually targets is a delegated `code-writer` dispatch, and
   `code-writer` runs verification before returning (`code-writer.md:9`, `:24`),
   backed by this repo's own "Agents: run `select-tests.py`" instruction
   (`CLAUDE.md`, Commands section). A `.claude/rules/` entry would fire at
   write time, but it buys write-time coverage only for hand edits while adding
   per-session prose load to every session touching a `.py` or `.sh` file — row 6's
   routing verdict stands.
   `[verified: ~/.claude/agents/code-writer.md:9, :24 — read this session]`
   `anchors: row6`
8. Finding C1 (the byte-identical 12-line block in the two length hooks) defers to
   the standing plan's Phase 2 and gets **no issue** — filing one would duplicate a
   plan row already on `main`.
   `[verified: hook-family-standardization.md:50 (row 9), :83-91 (Phase 2 file list) — read this session]`
   `anchors: root`
9. Finding C3 is **declined**, and its premise is wrong. The tri-state contract is
   already documented as a named call-site contract at `_lib.sh:757-773`. The
   polarity variation is not drift: each site is a complete three-way partition
   whose branch order follows that hook's match consequence.
   `require-code-review.sh:66-72` and `check-skill-length.sh:69-75` continue on
   match, so they test `-eq 1` (no match → exit) then `-ne 0` (indeterminate →
   deny). `block-gh-pr-merge.sh:70-77` denies on match, so it tests `-eq 0` (match
   → deny) then `-ne 1` (indeterminate → deny). No status falls through unhandled
   at any of the sites read. CLAUDE.md's discriminator-literal rule fires only
   "where a canonical symbol exists"; none does, and minting `readonly` sentinels
   in `_lib.sh` would add a sourced-global surface that Phase 4 of the standing
   plan (all 43 hooks' bootstrap) then has to absorb.
   `[verified: _lib.sh:757-788; require-code-review.sh:60-72; block-gh-pr-merge.sh:61-79; check-skill-length.sh:63-75 — all read this session]`
   `anchors: root`
10. Finding C4 is **declined**. The runtime `emit_deny` copy of the `install.sh`
    re-run sentence is instructional prose that must stand alone — CLAUDE.md's
    SSOT named exception 2 — and header-plus-`docs/hooks.md` restatement is the
    established convention across all 43 hooks, so changing one hook would create
    the inconsistency rather than remove it.
    `[verified: claude/.claude/CLAUDE.md:7 (exception 2) — read this session; the three sites are Lane C's enumeration, not re-opened]`
    `anchors: root`
11. The A1 fix is **not** a mechanical two-word deletion. At `_lib.sh:762-765` the
    label is possessive and scopes a count ("GH-783 Phase 2's eight gate hooks");
    stripping only " Phase 2" leaves "GH-783's eight gate hooks", which
    over-claims, since GH-783 as a whole spans Phase 1's nine hooks plus these
    eight. Each site needs the label replaced by the plain noun phrase it
    qualified ("the eight gate hooks", "six checked-fail-closed hooks"), retaining
    a bare `GH-783` only where it reads as a standalone citation rather than a
    possessive. `[verified: _lib.sh:757-773 — read this session]` `anchors: row2`
12. The three A2 sites survive CLAUDE.md's Axis-3 preserved-content exception, so
    a `code-writer` must not decline to edit them. The decision test is "does this
    text record something that happened, or describe how the code currently
    behaves?" Each site carries a current-behavior description wrapped in a
    record: keep the description, drop the record.
    `test_check_claude_md_length.py:606-617` keeps "the `if: \"Bash(git commit *)\"`
    predicate matches chained and prefixed commands" and drops the "correcting an
    earlier, disproven claim" narrative. `test_require_respond_pr.py:~1006-1011`
    keeps "a `PATTERN_*` constant can be defined and never wired into a gate arm,
    which is how one command reached production gated in one arm only" and drops
    the "the prior version of this test used a naming heuristic" narrative.
    `test_hook_command_normalization.py:115-117` keeps the current expected
    call-site count and drops the 9→11 delta.
    `[verified: claude/.claude/CLAUDE.md:53-60 — read this session]` `anchors: row2`
13. The `_fragment_raw_write_targets` three-pass extraction
    (`deny-reviewer-tree-mutation.sh:209-224` and `:228-244`) is **deferred to an
    issue**, not done here. `/plan-review` returned three independent, distinct
    failure modes on this one surface, which is the escalation discriminator
    rather than a correlated-reviewer artifact: (a) `ciso-reviewer` — the three
    passes read a `words` array built once under `set -f` bracketing (`:200-206`),
    and that glob-protection is implicit in code structure with no comment
    stating it as a contract, so an extraction that re-derives `words` silently
    changes which write targets a reviewer agent gets caught writing to; (b)
    `staff-sdet` — `TestRawWriteTargetGap` has zero coverage for the
    glued-redirect branches (`:216-220`), the tee flag-skip branch (`:235`),
    path-qualified `tee` (`:240`), and multi-target `tee`, so "the existing suite
    passes unedited" is not an equivalence proof over the branches the extraction
    would move; (c) `staff-platform-engineer` — comparing two arrays across a
    function boundary invites `local -n`, which is bash 4.3+, and this repo
    targets macOS system bash 3.2 while `test_no_bash4_constructs.py`'s guarded
    token list does not include nameref, so that regression would ship silently.
    Doing it properly needs six new characterization tests and an array-passing
    design decision — real work, and the payoff is readability on a gate whose
    behavior this plan otherwise promises not to touch. Extraction is also the
    softest finding class in the audit (a "should extract" judgment, not a
    violation), so it is the right thing to cut when a PR's no-behavior-change
    thesis is at stake. The issue must carry all three failure modes and the
    tests-first sequence so nothing is lost. `_lib.sh:833-847`'s glued-flag `case`
    stays **declined** — one nameable operation inside a function whose own
    docstring already names it, so extraction adds indirection without removing
    comprehension effort.
    `anchors: root`
13a. `_lib_words_start_with` (`_lib.sh:895-903`) **stays in scope**:
    `ciso-reviewer` confirmed the target loop sits strictly inside the
    `return 0`/`return 1` success path and does not touch the earlier `return 2`
    fail-closed branches that give `_lib_command_invokes_tool_subcmd` its
    tri-state contract. Two constraints bind the dispatch: the new helper's return
    convention must be stated, and the "no match falls through to `return 1`" path
    must survive unchanged. The same bash-3.2 constraint applies — no `local -n`
    or `declare -n`; pass both arrays via a flattened `"$@"` with an explicit
    sentinel, matching this file's existing by-value idiom at `:883`.
    `[verified: _lib.sh:883, :895-903 — read by ciso-reviewer and staff-platform-engineer this session]`
    `anchors: row2`
14. Findings C2, D1, D2, and D3 become issues rather than edits, each with a
    distinct reason. **C2** (the 4-site call/capture/allow/deny skeleton): two of
    its four sites are the length pair that Phase 2 folds into the driver, so
    today's count is not the count the fix would act on — the issue must state
    that it needs re-derivation after Phase 2 lands. **D1**
    (`stub_bin_without_timeout` triplication): pre-existing, so `code-review` item
    14 makes it informational; consolidating it into `conftest.py` also changes
    the fixture surface of three files this PR is simultaneously renaming and
    re-docstringing, which destroys the behavioral-equivalence signal. **D2**:
    needs a marker-taxonomy decision (new marker versus relocation) and touches
    `pyproject.toml`, a `GLOBAL_TRIGGER_PATHS` entry (`select-tests.py:182-186`),
    widening this PR's verification surface for an unrelated reason. **D3**
    (`_FENCED_BLOCK_RE`): currently correct, so it is a latent-robustness
    improvement, not a guideline violation, and therefore outside this plan's
    charter.
    `[verified: select-tests.py:177-186; code-review/SKILL.md:142 — read this session]`
    `anchors: row3`
15. Wave-1's three parallel dispatches share this one feature worktree, so each is
    instructed to run **only** `ruff` and `shellcheck` and to leave pytest to the
    orchestrator's post-join run. Concurrent pytest invocations in one tree race
    the `timing`-marked tests, which CI already isolates to a serial `-n0` pass
    precisely because they "would flake under sibling-worker load."
    `[verified: .github/workflows/tests.yml:26-38, 158-166 — read this session]`
    `anchors: row4`
16. The scan test needs a `select-tests.py` cross-domain exception to fire on the
    local loop, not only in CI. `.py` files under `claude/.claude/scripts/`,
    `claude/.claude/skills/tests/`, and `plugins/` currently select their own
    domain and never `HOOKS_TESTS_DIR` — which is exactly the path shape of
    GH-815's second instance (`test_transcript_analysis.py:2766`). A new predicate
    closes it, and `select-tests.py`'s own table comment already prescribes this
    maintenance ("When a test starts reading a file outside its own domain-rule
    tree by path or subprocess, audit this table by hand and add the matching
    entry"). **The predicate must exclude `DELIBERATELY_UNMAPPED_TOP_LEVEL_DIRS`**
    — a bare "any `.py` under `claude/` or `plugins/`" also matches
    `claude/.claude/tests/*.py`, which `select_pytest_targets` currently routes to
    the deliberate full-suite fallback via `unmatched-path` (comment at `:156-157`).
    Two real files sit there besides `helpers.py` — `test_statusline_command.py`
    and `test_pytest_collection_config.py` — and setting `matched=True` for them
    would narrow their selection from the full suite down to `HOOKS_TESTS_DIR`, a
    directory that does not contain them, silently dropping their own coverage
    from the local loop. `test_select_tests.py`'s completeness test (~`:656`) only
    checks that top-level directory names are registered, so nothing would catch
    this.
    `[verified: select-tests.py:156-157, 248-337; ls claude/.claude/tests/*.py — read by staff-platform-engineer this session]`
    `anchors: row5`
17. The scan test lands **last**, after the remediation waves — the same ordering
    discipline the standing plan states for its own conformance tests
    (`hook-family-standardization.md:23`: a conformance test placed ahead of the
    conversion "forces an allowlist written under merge pressure").
    `[verified: hook-family-standardization.md:23 — read this session]`
    `anchors: row5`
18. The detector must normalize wrapped comment blocks before matching. At
    `_lib.sh:762-763` the offending label is split across a line break ("GH-783
    Phase" / "# 2's"), so a line-at-a-time regex silently misses it and reports 15
    findings where 16 exist. Consecutive comment lines must be joined (stripping
    the leading `#`/`"""` and collapsing whitespace) before the pattern is
    applied. `[verified: _lib.sh:762-765 — read this session]` `anchors: row5`
19. The scan test excludes its own file from its corpus, with a one-line comment
    naming why, following `deny-private-project-refs.sh`'s test-file precedent for
    a test that must contain the string it forbids. It also carries three
    anti-vacuity assertions: a non-empty corpus assertion; a positive control
    asserting the detector matches a synthetic violating string; and a **negative
    control** asserting it does not flag `test_sha256_hash` or
    `test_utf8_edge_case`. The negative control is what stops a future
    simplification of the tokenizer to a generic `[A-Za-z]+\d+` shape from
    regressing silently until it starts flagging real source names.
    `[verified: test_nudge_transcript_toolkit.py:118's assert-matches anti-vacuity precedent — read this session; the redaction-test precedent is Lane E's report]`
    `anchors: row5`
20. The identifier detector tokenizes each captured identifier on `_` and
    camelCase boundaries and matches each token against a named prefix set (`gh`,
    `cve`, `rfc`, `issue`, `ticket`, `jira`, `pr`) followed by two or more digits.
    Prefix-set matching rather than a generic `[A-Za-z]+\d+` shape is what keeps
    `test_sha256_hash` and `test_utf8_*` out of the findings; both known instances
    (`TestGh483Invariants` → `Gh483`, `test_gh482_events_...` → `gh482`) match.
    `[unverified]` — the tokenizer was designed here, not run; the false-positive
    claim is reasoned from the two names, not measured against the repo corpus.
    Implementation must run it against the full corpus at `0ce2cfe` and reconcile
    against row 21's expected file set before the test is accepted. `anchors: row5`
21. At the pre-remediation merge-base `0ce2cfe`, the expected finding set is the
    11 files Lane A enumerated for the label check plus the 3 identifiers in row
    21a. **A hit outside that set is a new discovery until demonstrated to be a
    tokenizer bug — not a detector defect by default.** The inverted framing
    matters concretely: `staff-sdet` ran the row-20 rule against the merge-base
    during `/plan-review` and found a third identifier the naming audit missed
    (row 21a), which a "deviation means detector defect" rule would have
    reconciled away as noise. A file on the expected list that the detector misses
    is a genuine gap, and row 18's line-wrap case at `_lib.sh:762-763` is the
    first one to check. No expected total is pinned, because row 18's
    normalization changes the per-site count and no detector has been run to
    derive it. `anchors: row17`
21a. The identifier check's expected set at `0ce2cfe` is three, not two:
    `test_require_respond_pr.py:973` (`TestGh483Invariants`),
    `test_transcript_analysis.py:2766`
    (`test_gh482_events_attributed_to_own_branch_not_session_first_branch`), and
    `test_transcript_analysis.py:14806`
    (`test_turn_index_bucket_edges_match_pr605_bands_including_the_gap`, whose own
    docstring cites PR #605). The third predates #814
    and is in scope under the same repo-wide sweep authorization as the second.
    `[verified: run by staff-sdet against the tracked tree during /plan-review]`
    `anchors: row2`
22. `docs/hooks.md` needs no change: `:30` already cites bare `GH-783` with no
    phase suffix, per Lane A. The 16 `.claude/plans/GH-<num>-<slug>.md` filenames
    and `claude/.claude/hooks/tests/fixtures/gh564-incident.diff` are the
    documented `plan-it` branch-slug convention, are not identifiers, and are
    outside the detector's corpus (`.py`/`.sh` only) by construction.
    `anchors: row2`

## Critical files

Five `code-writer` dispatches in three waves. The orchestrator edits nothing; it
dispatches, runs verification between waves, runs the review skills `code-writer`
cannot run, and files the issues.

**Reuse, do not reimplement:**

- `claude/.claude/hooks/tests/conftest.py` for any new fixture.
- `claude/.claude/tests/helpers.py` for test plumbing.
- `test_nudge_transcript_toolkit.py`'s repo-wide `rglob` plus anti-vacuity-assertion
  shape (`:113-119`) as the model for the new scan.
- `select-tests.py`'s existing `_is_under` / `_is_plugin_subpath` predicate helpers
  (`:189-227`) rather than a new path-matching idiom.

### Wave 1 — three parallel dispatches, disjoint file sets

**Dispatch 1A — durable-prose remediation and the in-file class rename.** Comments,
docstrings, and one identifier only; no logic changes anywhere.

- `claude/.claude/hooks/_lib.sh` — `:762`, `:770` (per row 11: replace the
  possessive label with the plain noun phrase; note the `:762-763` line wrap from
  row 18).
- `claude/.claude/hooks/deny-invisible-commit-content.sh` — `:81`, `:140`.
- `claude/.claude/hooks/tests/test_lib.py` — `:1343`, `:1387`.
- `claude/.claude/hooks/tests/test_hook_command_normalization.py` — `:115-117`
  (both the label strip and the A2 rewrite per row 12).
- `claude/.claude/hooks/tests/test_block_gh_pr_merge.py` — `:201` (label),
  `:216-217` and `:224-225` (A3 residues: keep the behavior each pins, drop "the
  prior regex missed" / "the NEW mechanism" framing).
- `claude/.claude/hooks/tests/test_deny_escaped_backticks_in_pr_body.py` — `:85`.
- `claude/.claude/hooks/tests/test_deny_invisible_commit_content.py` — `:408`.
- `claude/.claude/hooks/tests/test_require_code_review.py` — `:474`.
- `claude/.claude/hooks/tests/test_check_claude_md_length.py` — `:34`, `:142`
  (labels), `:606-617` (A2 per row 12).
- `claude/.claude/hooks/tests/test_check_skill_length.py` — `:26`, `:149`.
- `claude/.claude/hooks/tests/test_guard_settings_session_keys.py` — `:459`.
- `claude/.claude/hooks/tests/test_require_respond_pr.py` — `:973` rename
  `TestGh483Invariants` to a domain name covering both its methods (they group the
  `respond-pr/SKILL.md` reply-command attribution scan and the `PATTERN_*`
  gate-wiring scan; both method names are already correctly domain-named and stay
  unchanged), and `:~1006-1011` (A2 per row 12).

Verification for this dispatch: `ruff` and `shellcheck` **scoped to this
dispatch's own file list above**, not the whole tree — the three Wave-1 dispatches
run concurrently in one shared worktree, and a tree-wide lint samples a sibling
dispatch's file mid-edit, leaving the agent with a finding it has no basis to
classify as pre-existing, transient, or its own. Do not run pytest (row 15).

**Dispatch 1B — the two pre-existing ticket-ID-named tests.**

- `claude/.claude/scripts/tests/test_transcript_analysis.py` — two renames:
  - `:2766`, `test_gh482_events_attributed_to_own_branch_not_session_first_branch`
    — drop the `gh482` token; the remainder is already a domain description.
  - `:14806`, `test_turn_index_bucket_edges_match_pr605_bands_including_the_gap`
    — drop the `pr605` token; keep the ticket reference in the docstring, which
    already carries it. Found by `staff-sdet` during `/plan-review`, not by the
    naming audit (row 21a).

  Both predate #814 and are in scope only under the engineer's repo-wide sweep for
  this class (row 2).

Verification: `ruff` scoped to this file. No pytest (row 15).

**Dispatch 1C — the reviewer-agent repair.**

- `claude/.claude/agents/comment-discipline-reviewer.md` — two edits, no
  frontmatter change (row 7):
  1. `:58-61`, PR-defined terminology angle: lead the examples with the realized
     shape — a tracker ID carrying a phase or step qualifier — before the invented
     codenames, and state the durable discriminator in one line: a bare tracker ID
     is a self-resolving citation, the phase/step qualifier is what makes the
     label PR-defined.
  2. `:87-90`, "How to work" item 4: convert the assertion into an operation —
     once a candidate PR-defined label or prior-version phrase is found, `Grep`
     the diff's file set for that literal token, enumerate every hit, and report
     the count.

Verification: none runnable by the agent. The orchestrator runs `/agent-review` on
this diff afterward (`.claude/rules/review-pipeline-dispatch.md`), with the fixture
pair `.claude/rules/skill-and-agent-self-review.md` requires: must-flag = a comment
containing `GH-783 Phase 2`; must-not-flag = a comment containing a bare `GH-783`
citation. `code-writer` and reviewer agents cannot run review skills
(`claude/.claude/CLAUDE.md:129`), so this stays with the orchestrator.

### Wave 2 — one dispatch, sequenced (overlaps 1A on `_lib.sh` and `test_lib.py`)

**Dispatch 2 — descriptive names, function extraction, and the coverage the
extraction needs.** Write the new tests first and confirm green against the
pre-extraction tree, then extract and re-run — that ordering is the
behavioral-equivalence proof.

- `claude/.claude/hooks/tests/test_lib.py` — add to `TestCommandInvokesToolSubcmd`
  (`:1430`): D4's shorter-subcommand case,
  `_command_invokes_tool_subcmd("gh pr", "gh", "pr", "merge") == 1`, covering the
  untested `[ "${#got_subcmd[@]}" -lt "${#want_subcmd[@]}" ] && continue` guard;
  and D5's `-R o/r` short-flag-with-space case.
- `claude/.claude/hooks/_lib.sh` — `:895-903`, extract the manual word-by-word
  array-prefix comparison inside `_lib_command_invokes_tool_subcmd` into
  `_lib_words_start_with`; `:1609-1616`, rename `result`/`result_exit` in
  `_lib_strip_shell_quotes` to `unquoted`/`unquoted_exit`, matching the callers'
  `COMMAND_UNQUOTED`. Two constraints on the extraction, per row 13a: state the
  new helper's return convention in its own one-line docstring, and preserve the
  "no match falls through to `return 1`" path unchanged. **No `local -n` or
  `declare -n`** — this repo targets macOS system bash 3.2, and
  `test_no_bash4_constructs.py`'s guarded-token list does not cover nameref, so
  that regression would ship silently. Pass both arrays as a flattened `"$@"`
  with an explicit sentinel, matching the by-value idiom at `:883`.
- `claude/.claude/hooks/deny-reviewer-tree-mutation.sh` — `:207` only, rename `n`
  to `word_count`. The `_fragment_raw_write_targets` three-pass extraction is
  **not** in this dispatch (row 13); it is deferred to an issue.

Verification: `.venv/bin/python3 claude/.claude/scripts/select-tests.py`,
`.venv/bin/ruff check claude/.claude/`,
`scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`, plus
`grep -n 'declare -n\|local -n' claude/.claude/hooks/_lib.sh` expecting no match.

### Wave 3 — one dispatch, sequenced last (row 17)

**Dispatch 3 — the deterministic scan and its selection wiring.**

- `claude/.claude/hooks/tests/test_ticket_reference_discipline.py` (new) — two
  checks over every tracked `.py` and `.sh` under `claude/` and `plugins/`:
  - **Identifier check:** capture Python `def`/`class` names and shell function
    names; tokenize per row 20; fail on any token matching the named prefix set
    plus two or more digits. Assertion message states the rule — an identifier
    names the domain it covers, not the ticket that produced it — since this
    message is the rule's only home (row 6).
  - **Plan-phase-label check:** join consecutive comment/docstring lines per row
    18, then fail on a tracker ID immediately followed by a phase qualifier.
    Assertion message states that a bare tracker ID is a legitimate citation and
    the phase qualifier is what makes it PR-defined.
  - Self-exclusion plus the two anti-vacuity assertions per row 19.
- `claude/.claude/scripts/select-tests.py` — add the cross-domain exception from
  row 16 (`.py` under `claude/` or `plugins/` → `HOOKS_TESTS_DIR`), **excluding
  `DELIBERATELY_UNMAPPED_TOP_LEVEL_DIRS`**, with the constant's own citing comment
  in the established style of the neighbouring constants (`:99-135`).
- `claude/.claude/scripts/tests/test_select_tests.py` — extend the rule-table
  path-fidelity and selection tests to cover the new predicate, and add the
  regression row 16 requires: `select_pytest_targets(["claude/.claude/tests/test_statusline_command.py"])`
  must still return `is_full_suite=True, reason="unmatched-path"` after the
  change.

Verification: `select-tests.py` selects the **full suite** here, because
`select-tests.py` is itself a `GLOBAL_TRIGGER_PATHS` entry (`:182-186`) — this is
`CLAUDE.md`'s documented full-suite case 1, not a hand-widened run.

### Orchestrator-only work (no code edits)

- Between waves: run the verification commands above; do not start Wave 2 until
  all three Wave-1 dispatches have returned.
- After Wave 1: `/agent-review` on Dispatch 1C's diff.
- After Wave 3 passes: file five GitHub issues — C2, D1, D2, D3, and the
  `_fragment_raw_write_targets` extraction (row 13) — each with file:line and the
  cited rule, per row 14's and row 13's per-finding reasons. File before
  `/pr-description` so the PR body can reference them.
- `/code-review` before the commit and `/ready-for-review` before the push, per
  `CLAUDE.md`.

## Verification

Repo-documented commands, scoped to the diff:

```bash
.venv/bin/python3 claude/.claude/scripts/select-tests.py
.venv/bin/ruff check claude/.claude/
scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck
```

Per-wave additions:

- **Wave 1.** After the three dispatches join, run all three commands once from
  the orchestrator — not per dispatch (row 15). Two dispatch-specific checks
  beyond them:
  - *Prose-only proof for the two shell files.*
    `git diff -- claude/.claude/hooks/_lib.sh claude/.claude/hooks/deny-invisible-commit-content.sh`
    must contain no changed line outside a `#` comment block. Shellcheck passing
    is necessary but not sufficient — it would also pass a real logic edit.
  - *Rename equivalence.* Capture
    `.venv/bin/pytest claude/.claude/hooks/tests/test_require_respond_pr.py claude/.claude/scripts/tests/test_transcript_analysis.py --collect-only -q`
    before and after. The node count must be identical, and the only differing
    node ids must be the three renamed ones. A changed count means a rename
    collided with an existing name and Python's last-definition-wins shadowing
    dropped a node.
  - *No external dependents.* The collect-only diff cannot see a consumer outside
    those two files keyed on the literal old identifier — a `select-tests.py`
    rule-table entry, a doc, a `-k` filter. `git grep -n 'Gh483\|gh482\|pr605'`
    must return zero hits outside the three renamed sites. `staff-sdet` confirmed
    zero external references exist today; this check makes that verified evidence
    rather than an assumption carried into execution.
- **Wave 2.** The D4 and D5 tests must be shown passing on the pre-extraction
  `_lib.sh` before `_lib_words_start_with` is extracted, then passing again after
  — run in that order inside the one dispatch. A test written only after the
  extraction proves nothing about equivalence. `test_deny_reviewer_tree_mutation.py`
  must pass with no edits, which is a meaningful check for this dispatch only
  because the dispatch now touches that hook with a single local-variable rename
  (row 13 moved the extraction out); it was **not** a sufficient equivalence proof
  for the extraction, which is part of why the extraction defers.
- **Wave 3.** The scan test must be shown failing against the pre-remediation
  merge-base before it is accepted as a real check:
  `git worktree add <tmp> 0ce2cfe`, copy in only the new test file, run it there.
  Reconcile the reported set against rows 21 and 21a — the 11 label-carrying files
  from Lane A plus the 3 identifiers. A hit outside that set is a **new discovery
  to remediate** unless demonstrated to be a tokenizer bug; a listed item the
  detector misses is a detector gap, and row 18's line-wrap case at
  `_lib.sh:762-763` is the specific one to check first. Do not accept a pass on
  the current tree as evidence the detector works; a detector matching nothing
  also passes.

  Separately, confirm the `select-tests.py` change actually fires: with only
  `claude/.claude/scripts/tests/test_transcript_analysis.py` dirty,
  `select-tests.py` must now name `claude/.claude/hooks/tests` among its targets.
  That single check is what distinguishes the local loop catching this class from
  CI alone catching it.

## Out of scope

**Deferred to an already-owned plan (no issue filed):**

- **C1 — the byte-identical 12-line block in `check-skill-length.sh:63-73` and
  `check-claude-md-length.sh:58-68`.** Owned by
  `.claude/plans/hook-family-standardization.md` row 9 and Phase 2 (`:83-91`),
  whose file list covers exactly these lines. Editing that plan is within this
  plan's reach — it is this repo's own committed artifact — so this is a
  deliberate decline, not an external constraint. Absorbing Phase 2's collapse
  here would produce a materially larger PR that preempts an approved,
  `/plan-review`-passed, unlanded phase; re-scoping it needs its own decision
  session. Filing an issue would duplicate a plan row already on `main`.

**Deferred to a filed issue.** Each carries file:line and the cited rule, per row
3, with the per-finding reason in row 14.

- **C2** — the 4-site call/capture/allow/deny skeleton. Re-derivable only after
  Phase 2 lands, since two of its four sites are the length pair Phase 2 folds
  into the driver.
- **D1** — `stub_bin_without_timeout` triplicated at
  `test_check_claude_md_length.py:30`, `test_check_skill_length.py:22`, and
  `test_guard_settings_session_keys.py:455`. Name `conftest.py:141-203` as the
  precedent to follow.
- **D2** — `test_lib.py:1540`'s `gh`-version non-hermeticity.
- **D3** — `test_require_respond_pr.py:31`'s `_FENCED_BLOCK_RE` silently excluding
  `sh`- and `shell`-tagged fences.
- **The `_fragment_raw_write_targets` three-pass extraction** (row 13). The issue
  must carry all three failure modes `/plan-review` found — the `set -f`
  glob-protection contract at `deny-reviewer-tree-mutation.sh:200-206`, the
  untested branches, and the bash-3.2 no-nameref constraint — plus the
  tests-first sequence. Six characterization tests come first:
  - the glued-redirect branches at `:216-220`
  - the tee flag-skip branch at `:235`
  - path-qualified `tee` at `:240`
  - multi-target `tee`
  - a glob-character write target, such as `cp *.bak src/x`
  - a fragment tripping two passes at once, such as `echo x > src/a | tee src/b`

**Declined, with reason:**

- **C3 — named tri-state sentinels for the 12 comparison sites.** The contract is
  already documented at `_lib.sh:757-773`, and the polarity variation is a
  complete per-hook three-way partition rather than drift (row 9).
  `ciso-reviewer` independently checked all nine call sites across the six
  fail-closed and two fail-open hooks and confirmed every site fully partitions
  0/1/2 with no unhandled fallthrough. No issue.
- **C4 — the `install.sh` re-run sentence appearing in three places.** SSOT named
  exception 2 plus the established 43-hook convention (row 10). No issue.
- **`_lib.sh:833-847`'s glued-flag-value `case` extraction.** One nameable
  operation already named by its enclosing function's docstring (row 13). No
  issue.
- **A new hook for either detector class.** Rejected under row 5's
  over-powered-primitive check; the static test is the lighter deterministic
  primitive.
- **A new CLAUDE.md or `.claude/rules/` line for the naming convention.** Rejected
  under row 6; the test enforces it and prose would be pure per-session load.
- **Extending the deterministic detector to `.md` durable docs.** A doc
  legitimately describing a phased rollout is indistinguishable by regex from a
  leaked plan label; `.md` prose stays `comment-discipline-reviewer`'s lane, which
  is the division of labor the two-layer design depends on.
- **Changing any behavior #814 shipped.** This plan touches comments, identifiers,
  variable names, and one function boundary inside `_lib.sh` only; every hook's
  verdict on every input is unchanged, which is what the unedited-existing-tests
  checks in Verification assert. Row 13's deferral is what keeps this claim true —
  the one proposed change that could have falsified it is now out.

**Revert granularity.** This repo squash-merges (`0ce2cfe` is one commit covering all
26 files in #814), so reverting Wave 3 alone after merge is not a single
`git revert`. Its file set is small and fully enumerated — the new
`test_ticket_reference_discipline.py`, the one `select-tests.py` predicate, and
the `test_select_tests.py` additions — so manual reversion is mechanical.

**Inherited boundaries (G1, G2):** the per-commit reviewer reasoning from #814 is
unrecoverable, so Lane E's E1 worked-example diagnosis and E4 dispatch-decision
candidate are inferences this plan acts on without confirming — the 1C repair is
justified by the agent body's current text, not by a reconstructed transcript.
`gh`-CLI version drift stays outside this repo's control and is why D2 defers
rather than being pinned.
