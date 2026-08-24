# Discovery-audit remediation plan

## Context

Remediate the 85 findings in `docs/reports/2026-08-22-discovery-audit/findings.md`
(11 High, 23 Medium, 33 Low, 11 Very Low, 7 N/A — [verified: `grep`-based
count against the report]) as a sequence of 10 PR-sized phases, so each
phase lands as one reviewable commit/PR rather than one 85-finding mega-diff.

The report's own citations are pinned to baseline commit `6291b343`, which
is 13 commits behind current `HEAD` (`eb8317ad`). Every finding below was
re-verified against `eb8317ad` before being assigned a phase; three findings
drifted (see the ledger), and one (SC5) was undercounted by the original
report. No finding was found resolved already.

## Approach

Group findings into 10 root-cause clusters rather than a strict
severity-tier or file-tier split, so each phase's diff addresses one
mechanism (e.g. "hooks don't cap external commands") across every site that
mechanism touches, per CLAUDE.md's "audit structural siblings" rule. Phase 1
is the hook guard-unification cluster, sequenced ahead of other same-severity
work because it closes the report's two "worse than the prior 2026-08-10
audit" recurrence findings (S2 regressed in count; SC1/S14 idiom-sprawl is a
repeat finding). All 10 phases are planned now rather than parking the
lower-severity tail as a backlog, per engineer instruction.

**Alternatives considered:** a strict severity-tier split (all High findings
in phase 1, regardless of file) was set aside — it would force phase 1 to
touch `_lib.sh`, `transcript-analysis.py`, and doc files in one diff with no
shared review lens, which is harder to review than a root-cause split. A
strict per-file split was also set aside — several files (e.g.
`transcript-analysis.py`) have findings spanning genuinely distinct root
causes (redaction-default gaps vs. ledger-permission gaps), and bundling
them by file rather than by cause would force one PR to justify two
unrelated fixes.

### Assumption ledger

**Root problem:** `findings.md` documents 85 audit findings against a stale
baseline; remediation must land as reviewable, PR-sized chunks without
re-litigating the report's own severity or scope calls.

**Givens:**
- G1 — The finding IDs (`S1`–`S25`, `C1`–`C26`, `D1`–`D16`, `I1`–`I5`,
  `SC1`–`SC7`) are fixed identifiers this plan reuses for traceability;
  this is a naming convention, not a constraint on the design. [reason:
  the IDs are how this plan cross-references `findings.md`; changing them
  would just break that cross-reference, not affect remediation]
- G2 — The 10-bucket grouping and phase-1-first sequencing are fixed.
  [engineer-verified] [reason: inside this plan's reach — Approach's
  "Alternatives considered" paragraph weighs the severity-tier and per-file
  splits against it. Without this given the plan re-derives a bucketing,
  which changes review ergonomics but not which findings get fixed.]
- G3 — All 10 phases are planned now; none is deferred to a backlog.
  [engineer-verified] [reason: inside reach — the plan could park the
  Low/Very-Low tail. Without this given, Phases 7-10 become an unplanned
  backlog and A8's 85-finding coverage claim no longer holds.]

**Per-mechanism justification** (anchors: root): each phase reuses an
existing in-repo helper or pattern rather than introducing a new one — see
each phase's Critical Files reuse column. No phase introduces a new
coordination pattern, privilege level, or abstraction the codebase doesn't
already have an established site for; the over-powered-primitive check does
not apply to any phase.

**Material assumptions:**

| # | Assumption | Tag |
|---|---|---|
| A1 | All 85 finding citations below hold against `HEAD` `eb8317ad` | [verified: 6 parallel re-exploration passes against `eb8317ad` this task, spot-checked directly this session for `review-permissions/SKILL.md` line count (`wc -l` = 200), `check-skill-length.sh`'s `limit_for()` exception list (code-review/plan-review/plan-review-ROUTING.md → 500 lines), `require-ready-for-review.sh:191`'s bare `timeout 5 gh pr view`, and `_lib.sh`'s `_lib_capped`/`_lib_capped_for` signature] |
| A2 | Three findings drifted from the report's baseline: D4 (case-studies index gap widened — a second orphaned file appeared), SC6 (CLAUDE.md grew 141→150 lines), and check-skill-length.sh line numbers (+3/+5 from unrelated growth) | [verified: re-exploration this task] |
| A3 | SC5 was undercounted by the original report — 8 duplicate sites of the no-redact/multi-root-refusal pattern exist, not 4 | [verified: re-exploration this task, all 8 sites cited in Phase 3] |
| A4 | Phase 6 (review-permissions dispatcher fix) needs to add ~2 sentences to `review-permissions/SKILL.md`, which sits at exactly its 200-line hard cap (`wc -l` = 200) with zero headroom | [verified: this session, direct `wc -l`] |
| A5 | Resolving A4's conflict by extending `check-skill-length.sh`'s `limit_for()` 500-line exception (already granted to `code-review/SKILL.md`, `plan-review/SKILL.md`, `plan-review/ROUTING.md`) to `review-permissions/SKILL.md`, rather than trimming existing content first | [unverified — this is this plan's own scope decision, not previously confirmed with the engineer; flagged explicitly below] |
| A6 | SC5's 8 duplicate sites are grouped into Phase 3 (redaction-default cluster) rather than Phase 10 (grab-bag), because they're the same file and the same conceptual defect (missing shared refusal helper) as Phase 3's other findings | [unverified — a placement call, not previously confirmed; low-stakes, stated here rather than re-asked] |
| A7 | Three shared-helper extraction opportunities noticed during exploration beyond the report's own findings (C7's default-branch resolver in Phase 1, C10's PID-liveness helper in Phase 10, SC2's eviction-sweep helper in Phase 10) fold into their respective phases as sub-steps rather than becoming new phases or new findings | [unverified — a scope decision, stated here rather than re-asked, per engineer's "plan all 10 buckets, don't backlog" instruction extending naturally to in-scope sub-steps discovered while planning them] |
| A8 | All 85 report finding IDs map onto either an actionable phase or the true N/A set (7 IDs); none is silently dropped | [verified: this session, `awk`-based section-header extraction cross-checked against every phase's bulleted findings; `S24`, `D5`, `D6`, and `D7` are Low/Medium findings that are *not* N/A-tagged and are placed in Phases 6-9] |
| A9 | `plugins/skill-management/skills/skill-review/SKILL.md` has the same zero-headroom conflict as A4 (also exactly 200 lines, and Phase 6's C15 also adds content to it); resolved via the same `limit_for()` 500-line exception mechanism as A5, extended to this second file | [verified: this session's specialist review, `wc -l` = 200; the extension choice itself is unverified — same A5 scope-decision status] |

**Flag to the engineer when presenting this plan:** A5 (the review-permissions
line-cap exception, now also extended to `skill-review/SKILL.md` per A9)
and A6 (SC5's placement) are scope decisions this plan made rather than
ones already confirmed — call them out explicitly rather than letting them
pass as settled.

**Resolved with the engineer at presentation** — the following four items
were flagged as open and are now settled, [engineer-confirmed] this
session:

- **Phase 1 stays 1a/1b, no third split.** G2 fixes the 10-bucket grouping
  [engineer-verified], so this plan does not re-cut it. Confirmed this is
  not a deferral: all four failure modes the review findings concentrated
  in Phase 1 (the inline-alias bypass, the bare-`&` gap, the fail-closed
  exit-124 handling across all 4 content-bearing sites, and the
  alias-bypass/wrapper-command test pairs for the 7 converted hooks) are
  already fully specified with concrete fixes and required tests inside
  Dispatch 1b above — the two Critical prerequisites additionally land as
  their own standalone PR first per the "Exception" carve-out, which is a
  sequencing choice, not a scope reduction. A round-4 review found the
  Exception carve-out had no stated dispatch mechanism for producing that
  standalone PR — fixed below at the Exception paragraph itself (a
  two-commit sequence inside Dispatch 1b's own worktree, not an
  undeclared third dispatch).
- **Phase 9's `S103` scoping: keep it out of the repo-wide ignore.** A
  round-4 security review found the originally-proposed mechanism
  (per-file-ignores excluding `transcript-analysis.py`) doesn't work —
  `ruff`'s `per-file-ignores` is additive-only and cannot un-suppress a
  rule already in the top-level `ignore =` list, verified empirically
  against `ruff 0.6.9`. Corrected mechanism: leave `S103` out of the
  top-level `ignore =` entirely, and instead add `per-file-ignores` rows
  for the (non-`transcript-analysis.py`) files among the 5 current `S103`
  findings, identified at implementation time. See Phase 9's S25 bullet
  for the full mechanism and its verification step.
- **Phase 9 gets no `CHANGELOG.md` entry.** It is a contributor-facing
  lint-config change with no stow-user-visible effect (see "CHANGELOG
  entries").
- **D1 confirmed: wire in `evals/` CI collection.** Overrides the prior
  "no CI wiring" decision recorded in
  `.claude/plans/plan-mode-model-resolution-experiment.md:450-457` and
  `evals/README.md:23-25`, per D1's own bullet in Phase 9. Verified this
  session and independently re-verified by round-4 review that
  `test_measure_subagent_model_resolution.py` makes no live Claude API
  call — no cost concern. A round-4 review found the original mechanism
  description understated what's required (collecting the file explicitly
  and guarding the `evals/fixtures` collection error are both needed, not
  alternatives) and surfaced a `staff-backend-engineer.md`/`Explore.md`
  content-coupling risk and `evals/README.md` staleness — all corrected in
  D1's own bullet in Phase 9.

### Branch and PR shape

Each phase gets its own branch and worktree, cut fresh from `main` after
the previous phase has merged: `git worktree add .claude/worktrees/<slug>
-b <slug>` per the `branch-management` skill, with the phase's
`code-writer` dispatched into it **without** `isolation: worktree` (these
are PR-bound implementation dispatches, per CLAUDE.md's Agent Briefing).
Ten phases means ten branches and ten PRs — the plan's own "PR-sized
phases" premise requires it; running every phase's dispatch inside this
plan's worktree would accumulate all 10 into one branch and produce
exactly the mega-diff the phasing exists to avoid. Cutting each branch
after the prior merge is also what makes Phases 3-5's citation-drift
re-resolution work: each dispatch reads a `transcript-analysis.py` that
already carries the previous phase's edits.

This plan file itself ships in its own standalone PR ahead of
implementation, so no phase branch carries it.

**Exception — land Phase 1's two `_lib.sh` prerequisites first, standalone.**
The inline git-config-alias sentinel in `_lib_extract_git_subcmd` and the
bare-`&` addition to `_lib_split_fragments` are specified in Dispatch 1b
below, but they should merge as their own small PR immediately after this
plan PR, not bundled with 1b's 7-hook conversion and S2 wraps.
**Mechanism [engineer-confirmed]:** dispatch Dispatch 1b's `code-writer`
into its own worktree as normal, but instruct it to make the two
prerequisite fixes (plus their own tests) as a first commit, open and
merge that as its own PR before continuing, then make the remaining
1b-scope changes (7-hook conversion, `GIT_DIR` stripping, S2 wraps) as a
second commit/PR from the same worktree — not a separate undeclared
dispatch. This plan publishes working invocations for both bypasses, and
`deny-pii-in-commits.sh` — whose credential-value tier is unconditional for
every stow install (`settings.json:276` registers the hook with no `if`
matcher) — is defeatable by them until the fix ships [verified: this
session's specialist review]. The underlying bug is already public in
`_lib.sh`, so this shrinks a disclosure window rather than closing a new
hole, but the fix is small and fully specified, so there is no reason to
make it wait on the rest of the dispatch.

### CHANGELOG entries

`CHANGELOG.md`'s `[Unreleased]` section is this repo's user-facing record
for stow consumers, and its established convention attaches an explicit
**Migration:** note to breaking or behavior-changing entries [verified:
this session, 4 such notes present in the file]. Three phases change
behavior a stow user can observe on `git pull` and each needs an entry
with a Migration note in its own PR:

- **Phase 1** — git-command detection changes on 7 authorization-gating
  hooks, plus the new fail-closed-on-timeout denial at the 4
  content-bearing sites.
- **Phase 3** — S15's `--redact` → `--no-redact` flag-shape break on
  `audit-routing`.
- **Phase 6** — the `check-skill-length.sh` cap relaxation, which changes
  what the length gate permits for every stow user.

Phase 9's ruff-`"S"` addition is contributor-facing rather than
stow-user-facing (it gates this repo's own CI, not an installed surface) —
flag to the engineer whether it warrants an entry rather than assuming
either way. No other phase changes observable behavior.

### Dispatch split

Each phase is implemented by one `code-writer` dispatch, except Phase 1
(see its own section) — every other phase's files partition into a single
coherent root-cause fix with no independent sub-slices worth parallelizing,
so splitting further would just restate the same shared context across
dispatches. Phase 1 splits into two sequenced dispatches (1a: prerequisite
+ new helper; 1b: shared-trio rollout + wrap + tests) because its file
count (~29) and mixed review lenses (a new mechanism vs. mechanical
wrap-throughs) make it the one phase too large for a single reviewable
diff — see Phase 1's own section for the split rationale.

Phases run sequentially in the order below (1 first, per G2); a later
phase's dispatch prompt does not depend on an earlier phase's diff except
where explicitly noted (Phase 6 depends on Phase 1 only in the sense that
both touch hook-adjacent docs, not code — independent in practice).

## Critical files

### Phase 1 — Hook guard-unification cluster (S2, S3, S13, S14, SC1, C6, C7, C9, C19, D2, D3, D8, D9, D10, D12)

Unifies every hook's use of `_lib.sh`'s shared trio
(`_lib_capped`/`_lib_capped_for`, `_lib_jq`, `_lib_fragment_invokes_git` +
`_lib_extract_git_subcmd` + `_lib_split_fragments`) instead of bespoke
`grep -qE` regexes or unwrapped external calls. Sequenced first: closes the
report's two regression findings (S2's growing unguarded-call count; SC1/S14's
repeat idiom-sprawl finding).

Reuse: the 4 hooks already using the shared trio correctly —
`require-ready-for-review.sh:101-114`, `deny-pii-in-commits.sh:181-191`,
`deny-private-project-refs.sh:250-251,281`,
`deny-reviewer-tree-mutation.sh:309-310,389` — are the pattern every other
site below replicates.

By file count this phase is the largest of the 10 (~29 files across hooks,
`_lib.sh`, plugin `_lib.sh` copies, and tests) and mixes two distinct review
lenses — a new/behavior-changing mechanism (C7's helper extraction, the
SC1/S14 regex→shared-trio swap) versus mechanical wrap-throughs (S2). Split
into two sequenced `code-writer` dispatches on that basis rather than one:

**Dispatch 1a — prerequisite + new helper (S3, C7, D2 rescoped to S3 only):**
- **D2, rescoped**: `claude/.claude/tests/helpers.py:383-393` — add a `cwd`
  param to `bash_input()`, matching `edit_input`/`write_input`'s existing
  `cwd` params. This is a prerequisite for **S3's own test** only, not for
  D3/D8/D9/D10/D12 below — verified those five sibling tests all exercise
  `run_hook()`'s own `cwd=` param (the *ambient* subprocess cwd) rather than
  a payload `.cwd` field, and their target hooks (`check-skill-length.sh`,
  `check-claude-md-length.sh`, `deny-private-project-refs.sh`,
  `deny-pii-in-commits.sh`) never read payload `.cwd` at all. Only S3's fix
  below reads payload `.cwd` distinct from ambient cwd, so only S3's test
  needs `bash_input(cwd=...)`.
- **S3**: `claude/.claude/hooks/guard-settings-session-keys.sh` — never
  reads payload `.cwd` (5 git calls at lines 62,70,76,77,80 rely on ambient
  cwd). Add `CWD=$(printf '%s\n' "$INPUT" | jq -r '.cwd // empty'); [ -z
  "$CWD" ] && CWD="$PWD"` and thread `-C "$CWD"` into all 5 git calls,
  matching `require-plan-review.sh:82-90` / `require-code-review.sh:74,77`.
  Precedent: commit `fe249da5` (#704) applied the identical fix to
  `session-marker-dashboard.sh`. Add a test using the new `bash_input(cwd=...)`
  param asserting payload cwd and ambient cwd can diverge.
- **C7 + shared-helper extraction**: `guard-settings-session-keys.sh:77,80`
  hardcodes literal `"main"`. Extract the portable default-branch-resolution
  pattern from `require-ready-for-review.sh:158-169` (`git symbolic-ref
  --quiet refs/remotes/origin/HEAD`, fallback probing main/master/develop)
  into a new `_lib.sh` helper — 2+ files now want it — and call it from
  `guard-settings-session-keys.sh`. Add a regression test for the new
  helper directly (a repo with a non-`main` default branch resolves
  correctly), independent of any caller.

**Dispatch 1b — shared-trio rollout, S2 wrap, and tests (S13, S14, SC1, C6, C9, C19, D3, D8, D9, D10, D12):**
- **Bare-`&` fragment-splitting gap in the shared trio** (Critical; not in
  the source report): `_lib_split_fragments`
  (`_lib.sh:490-494`) splits command fragments on `;`, `&&`, `||`, `|`,
  `$(`, and backtick, but not a bare single `&` — valid bash background-op
  syntax needs no surrounding whitespace (`foo&git commit -m x` really
  executes `git commit`). `_lib_fragment_invokes_git` (`_lib.sh:440-453`)
  then never sees the fragment as starting with `git`, so any hook using
  the shared trio misses this input shape entirely — confirmed the bespoke
  regex this dispatch is about to retire (`grep -qE
  '(^|&&?|;|\|\|?)\s*git\s+commit(\s|$)'`) DOES catch it, but the shared
  trio does not, and `deny-pii-in-commits.sh` (already on the shared trio)
  is exploitable by this input today. Add bare `&` to
  `_lib_split_fragments`'s split set (or an equivalent boundary check in
  `_lib_fragment_invokes_git`) before or alongside this dispatch's own
  regex→shared-trio swap below — this is a prerequisite, not a follow-on,
  since the swap would otherwise trade a working detector for a broken
  one. Add a `foo&git commit`-shaped regression test to every hook already
  or newly using the shared trio, including `deny-pii-in-commits.sh` (not
  just the 7 hooks converted in this dispatch). This new split boundary
  must not regress the existing `&&` case or misfire on unrelated `&`
  syntax: add two more cases to the same regression pass — (a) an
  `&&`-separated git-commit command (e.g. `foo && git commit -m x`) is
  still detected post-change, and (b) a redirect/fd-duplication use of
  `&` that is not a command separator (`cmd 2>&1; git commit -m x`, `cmd
  &> log; git commit -m x`) does not get mis-split into a spurious
  fragment boundary and does not produce a false allow or false deny.
- **Inline git-config alias bypass in `_lib_extract_git_subcmd`**
  (Critical; not in the source report — prerequisite, same class as the
  bare-`&` gap above): git resolves an alias defined inline in the same
  command string, so `git -c alias.ci=commit ci -m x` runs `git commit`
  while `_lib_extract_git_subcmd` (`_lib.sh:462-485`) returns `ci` and the
  gated-verb comparison never matches [verified: this session's specialist
  review, reproduced empirically against a copy of `_lib.sh`].

  Fix at the helper, not per-hook: make `_lib_extract_git_subcmd` treat an
  inline alias definition as unresolvable, returning a sentinel the callers
  treat as the gated verb (fail closed), since the hook cannot know what the
  alias expands to. Three details:

  - Two syntactic forms reach this: `-c alias.x=` / `--config-env`, and a
    `GIT_CONFIG_KEY_*` / `GIT_CONFIG_VALUE_*` / `GIT_CONFIG_COUNT`
    assignment prefix. `-c` is in the helper's skip-next flag list; the
    env-assignment prefix is skipped by its `past_git` walk.
  - Close it as a prerequisite rather than propagate it. Only
    `deny-pii-in-commits.sh` is on the shared trio today; this dispatch
    widens the gap to all 8 converted hooks, the credential/PII scanner
    among them.
  - Distinct from S13's residual below, which is a *persistent* git alias
    and a `\git` shell-alias escape. D10's planned test mirrors the
    persistent-alias pattern and exercises neither inline form.

  Tests: one per form — `-c alias.x=`, `--config-env`, and
  `GIT_CONFIG_KEY_*` each get their own case; a round-4 review found
  `--config-env` silently absent from an earlier "one per form" phrasing
  that only named two of the three — asserting the deny path fires for
  each, plus a negative case confirming a non-alias `-c` use
  (`git -c core.pager=cat commit`) still resolves to `commit` rather than
  tripping the sentinel.
- **GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE stripping** (new, closes a gap
  this phase's own "audit structural siblings" rationale otherwise leaves
  open): three sibling hooks already establish this idiom —
  `require-worktree-for-file-writes.sh:35`,
  `require-worktree-for-git-writes.sh:142`,
  `deny-reviewer-tree-mutation.sh:253` (each commented "Defensive: prevent
  GIT_DIR / GIT_WORK_TREE env overrides"). None of the ~12 hooks this phase
  touches unset these vars, so an ambient `GIT_DIR` (a poisoned shell rc or
  CI env var) could redirect their `git rev-parse`/`diff --cached`/`show`
  calls at a different repo/index, defeating the guard itself. Fix at the
  single choke point every wrapped call passes through: add `unset GIT_DIR
  GIT_WORK_TREE GIT_INDEX_FILE` inside `_lib_capped_for` (`_lib.sh:38-48`,
  before the `command -v`/exec dispatch) — every hook using
  `_lib_capped`/`_lib_jq`, present or future, gets this defense automatically
  instead of needing it re-added per file. `require-npm-version-bump.sh`
  and `require-plugin-version-bump.sh` (both touched by the
  regex→shared-trio swap below) make their git calls directly, bare, with
  no `_lib_capped`/`_lib_jq` wrapper at all — their own plugin `_lib.sh`
  copies explicitly state "No git helpers," so this choke-point fix never
  reaches their ~20 `git rev-parse`/`merge-base`/`diff --cached`/`show`/
  `cat-file` call sites. Add the same `unset GIT_DIR GIT_WORK_TREE
  GIT_INDEX_FILE` to each plugin's own `_lib.sh`
  (`plugins/npm-semver/hooks/_lib.sh`,
  `plugins/plugin-semver/hooks/_lib.sh`), not duplicated inline in either
  hook's body — each `_lib.sh` is already sourced before any hook-body git
  call runs (both hooks already reuse it for `_lib_jq`), so this is a
  strictly better placement than hook-body logic: it can't be skipped by
  a call landing before the unset by accident, and it automatically covers
  any future hook added to either plugin, matching this phase's own
  choke-point rationale for `_lib_capped_for` above instead of
  reintroducing the idiom-sprawl failure mode (SC1/S14) this phase exists
  to close. Add a regression test for both the `_lib_capped_for` choke
  point and each plugin's `_lib.sh` addition, asserting an ambient
  `GIT_DIR` pointing at a different repo does not redirect the wrapped
  call.
- **Content-bearing sites need exit-status-aware handling, not a bare wrap**
  (new — distinguishes these from the mechanical S2 wraps below): 4 of
  S2's sites read full diff *content*, not cheap metadata (named
  individually below), and all four have a **fail-open bug** if wrapped
  naively (three share one gate shape, the fourth reaches the same bug
  through an inverted one — see below). `require-code-review.sh`'s empty-diff
  gate check (currently ~line 85: `if [ -z "$(git -C "$REPO_ROOT" diff
  --cached 2>/dev/null)" ]; then exit 0; fi` — [verified: this session,
  read directly]) decides whether the code-review gate applies at all. If
  this call is wrapped in `_lib_capped` and a legitimately large staged
  diff exceeds the 5s cap, `timeout` SIGTERMs the process, stdout comes
  back empty, and the `-z` check reads that as "nothing staged" — silently
  skipping the gate for exactly the large-diff case most needing review,
  the opposite of this same file's own "fail closed" comment two blocks
  later. `require-skill-review.sh` has the identical pattern at its own
  empty-diff check (~line 116, confirmed byte-identical this session).
  `deny-private-project-refs.sh:454`'s unrestricted `git diff --cached --
  ':(top,exclude)...'` is a fourth content-bearing read, but its gate is
  shaped differently: the result is captured into `$STAGED_DIFF`, then
  gated by `if [ -n "$STAGED_DIFF" ]; then ... fi` (lines 455-494) — a
  presence check guarding the scan, not the `-z ... exit 0` early-return
  the other three share [verified: this session, read directly]. Line
  489's own comment documents that an empty `$STAGED_DIFF` skips adding
  *both* the diff and the raw command string to the scan target, "to
  preserve historical behavior" for the true-empty case — but a
  `_lib_capped` timeout produces the same empty string, so it silently
  skips scanning inline command content too, not just the diff. For the
  first three sites: do not leave the current one-line
  `if [ -z "$(git ... 2>/dev/null)" ]; then exit 0; fi` shape intact with
  `_lib_capped` merely substituted inside it — that shape discards the
  wrapped command's own exit status entirely (`$?` after the `if` reflects
  `[`'s result, never `124`), silently reproducing the exact fail-open bug
  this fix exists to close. Use the explicit two-statement form instead:
  `OUT=$(_lib_capped git ...); RC=$?; [ "$RC" = 124 ] && { echo "..." >&2;
  exit 1; }; [ -z "$OUT" ] && exit 0` (deny/error on `124` before the
  empty-string check ever runs). For the fourth site
  (`deny-private-project-refs.sh:454`), apply the same RC-before-emptiness
  discipline adapted to its own shape: immediately after
  `STAGED_DIFF=$(_lib_capped git diff --cached -- ...)`, check `RC=$?; [
  "$RC" = 124 ] && { echo "..." >&2; exit 1; }` before the existing `if [
  -n "$STAGED_DIFF" ]` runs, so a timeout denies instead of silently
  taking the same branch as "nothing staged". Add a regression test per
  site (all four) using the `fake_git sleep`-shim pattern already
  established in `test_deny_pii_in_commits.py:218-242`
  (`test_staged_diff_git_timeout_denied` — a `git` wrapper on `$PATH` that
  sleeps past the cap, deterministic and fast), not an actually oversized
  diff — and assert the deny path itself fires, not just that a test
  exists. This trade is fail-open for fail-closed, which introduces a
  denial mode stow users do not have today: a legitimately large staged
  diff that exceeds `_lib_capped`'s 5s cap now blocks the commit outright
  instead of silently passing. The cap is hardcoded with no per-invocation
  override (`_lib.sh:38-48`). Confirm 5s is realistic headroom for a large
  staged diff on a cold cache before shipping, and add the new behavior to
  `docs/security-hardening.md`'s Known-gaps paragraph for these hooks so
  a user who hits it can recognize it — the deny message alone does not
  distinguish "timed out" from "review actually missing".
- **7 bespoke-regex hooks (SC1/S14)**: `require-code-review.sh:64`,
  `guard-settings-session-keys.sh:57`, `check-skill-length.sh:60`,
  `check-claude-md-length.sh:58`,
  `plugins/npm-semver/hooks/require-npm-version-bump.sh:126`,
  `plugins/plugin-semver/hooks/require-plugin-version-bump.sh:90`,
  `plugins/skill-management/hooks/require-skill-review.sh:83` — replace each
  bespoke `grep -qE` git-commit detector with the shared-trio loop
  (`_lib_split_fragments` → loop → `_lib_fragment_invokes_git` →
  `_lib_extract_git_subcmd`, compare subcommand to target verb). This
  changes detection behavior on an authorization-gating boundary (each hook
  blocks a commit/version-bump/leaking-content path); add at minimum one
  alias-bypass test and one wrapper-command (`bash -c`/`eval`) allow/deny
  test pair per converted hook. Each of these 7 hooks already has a
  dedicated test suite (`test_require_code_review.py` 701 lines,
  `test_guard_settings_session_keys.py` 766 lines,
  `test_check_skill_length.py` 456 lines,
  `test_check_claude_md_length.py` 652 lines,
  `test_require_npm_version_bump.py` 550 lines,
  `test_require_plugin_version_bump.py` 438 lines,
  `test_require_skill_review.py` 1233 lines, all under
  `claude/.claude/hooks/tests/`, not under `plugins/` — [verified: this
  session's specialist review]); the swap's regression risk is uniform
  across all 7, not concentrated in a zero-coverage subset. Those 7 suites
  are a weaker baseline than their line counts suggest, though: every
  git-commit case in them invokes the single literal shape
  `git commit -m foo`, with no flag, hoisted-flag, or alias variety —
  contrast `deny-pii-in-commits.sh`, already on the shared trio, whose
  suite exercises ~14 distinct shapes (`--amend`, `-am`, `-c user.name=`,
  `-C .`, `add . && commit`) [verified: this session's specialist review].
  So "the full existing suite stays green" proves only that the canonical
  shape still detects; a broken flag-walk in the new
  `_lib_extract_git_subcmd` usage would ship undetected. Add one
  hoisted-flag-form case per converted hook (e.g. `git -C . commit -m x`)
  alongside the alias-bypass and wrapper-command pairs above, rather than
  treating suite-stays-green as the primary gate.
- **S2 unguarded external-call sites** (metadata/cheap calls only — the 3
  content-bearing sites above are handled separately) — wrap each in
  `_lib_capped`/`_lib_jq`: `deny-private-project-refs.sh:287,298`,
  `check-claude-md-length.sh:62,86`, `check-skill-length.sh:64,93`,
  `require-code-review.sh:77,100`, `require-plan-review.sh:90`,
  `require-ready-for-review.sh:150,162,163,166,204`,
  `require-stow-reminder.sh:86,92,99,104,130,189`,
  `require-worktree-for-file-writes.sh:123,132,133`,
  `plugins/skill-management/hooks/require-skill-review.sh:96,108,109,142,158,186,221`,
  `deny-credential-file-reads.sh:38`, `deny-env-reads.sh:58`,
  `deny-data-file-reads.sh:77`, `require-routing-read.sh:64` (this last one
  wraps `find`, not git/jq). Also add a `gtimeout` probe (mirroring
  `_lib_capped_for`'s probe-then-run) to all 4 plugin copies of
  `_lib_jq()`, which currently only probe `timeout` — [verified: this
  session, all 4 copies are byte-identical 7-line functions] —
  `plugins/lovable-cloud/hooks/_lib.sh:14-20`,
  `plugins/npm-semver/hooks/_lib.sh:16-22`,
  `plugins/plugin-semver/hooks/_lib.sh:16-22`,
  `plugins/skill-management/hooks/_lib.sh:52-58`.
  Add one timeout-path test per newly-`_lib_capped`-wrapped **git** call
  site, reusing the `fake_git sleep`-shim pattern cited above. Wrapping
  creates a timeout branch that did not exist before, so the pre-existing
  suite cannot cover it, and several of these hooks are deny-hooks where
  the branch decides allow-vs-deny — the individually-named S2-adjacent
  findings below (C6/C9/C19/D9) each already carry this instruction, and
  the bulk sites need it for the same reason. The three `_lib_jq`-only
  sites (`deny-credential-file-reads.sh:38`, `deny-env-reads.sh:58`,
  `deny-data-file-reads.sh:77`) are exempt: each parses one small
  already-in-memory JSON blob, so a 5s timeout is not a reachable state
  [verified: this session's specialist review].
- **C6**: `check-claude-md-length.sh:62-63`, `check-skill-length.sh:64-65` —
  `REPO_ROOT` is computed and tested for emptiness but never threaded into
  `git diff --cached`/`git show` (implicit cwd). Thread `-C "$REPO_ROOT"`.
  Add a regression test per file asserting `-C "$REPO_ROOT"` is actually
  threaded (a divergent-cwd test, mirroring S3's `bash_input(cwd=...)`
  pattern above).
- **C9**: `session-marker-dashboard.sh:94` — `REPO_ROOT` git call unguarded
  (contrast the already-`_lib_jq`-wrapped call at `:102`). Wrap with
  `_lib_capped`. Add a timeout-path test for this newly-wrapped call,
  matching D9's sequencing note (write it after this dispatch's own wrap
  lands).
- **C19**: `consume-durable-continuity-file-on-read.sh:118-122` — inline
  timeout check has no `gtimeout` probe; replace with `_lib_capped`. Add a
  `gtimeout`-probe regression test mirroring the pattern this dispatch
  already establishes elsewhere in this phase.
- **D3/D12**: `test_check_skill_length.py:419-436`,
  `test_check_claude_md_length.py:588+` — both have a timeout-absent test
  whose docstring admits it only exercises the already-capped `git show`
  sites, never the bare rev-parse/`diff --cached` sites. Extend to cover
  those sites once S2's fix lands.
- **D8**: `test_require_code_review.py:375-390` — wraps `marker.sh` in
  `bash -c` but leaves `git commit` itself unwrapped; add a test wrapping
  the `git commit` invocation in `bash -c`/`sh -c`/`eval`.
- **D9**: `test_deny_private_project_refs.py` (2,966 lines, 176 tests) has
  zero sleep/fake_git/timeout tests; `deny-pii-in-commits.sh`'s test file has
  ≥3. Add analogous timeout-path tests targeting
  `deny-private-project-refs.sh:287,298` — sequence after this dispatch's
  own S2 wrap of those same two sites lands (a timeout-path test is
  meaningless before the wrap exists), matching D3/D12's existing
  "once S2's fix lands" sequencing note.
- **S13** (documentation, not a code fix — [verified: this session, read
  directly]): `deny-pii-in-commits.sh:185-187`'s git-commit detection is
  fully bypassed by a configured git alias (`git ci`, `git cm`) — the
  hook's own `_lib_extract_git_subcmd` does no alias resolution, so an
  alias makes the resolved subcommand `"ci"` and the hook's entire scan
  (including the always-on credential-value tier) never runs. This same
  residual gap is already documented and accepted in 3 sibling hooks
  (`deny-repo-relocation.sh:35-39`, `deny-reviewer-tree-mutation.sh:103-114`,
  `deny-private-project-refs.sh:67,83-87`, e.g. "A backslash-escaped `\git`
  invocation (used to bypass a shell alias)") — `deny-pii-in-commits.sh` is
  the one hook with the highest-consequence content whose own Known-gaps
  list doesn't say so. Add the alias-bypass gap to
  `deny-pii-in-commits.sh:88-100`'s "Known gaps (documented, not closed):"
  comment block, matching the siblings' phrasing, and to
  `docs/security-hardening.md:451-458`'s "**Known gaps.**" paragraph for
  this hook. Given the bare-`&` fragment-splitting gap above also applies
  to `deny-pii-in-commits.sh` today, name both residuals — persistent-alias
  resolution and bare-`&` fragment-splitting — together in this same
  Known-gaps addition and pinning test, not just the alias one. Do not
  list the inline git-config alias form (`-c alias.x=`,
  `GIT_CONFIG_KEY_*`) among the accepted residuals: this dispatch closes
  that one at the helper, so naming it here would disclose a gap that no
  longer exists. D10
  (below) adds the test that pins this same accepted gap — land S13's
  documentation and D10's test together so the gap is both named and
  pinned, not one without the other.
- **D10**: `test_deny_pii_in_commits.py` has zero alias/`git-ci`/`git-cm`
  tests; sibling pattern `test_reviewer_quoted_command_name_bypass_allowed`
  exists in `test_deny_reviewer_tree_mutation.py:648`. Add the analogous
  test.
- **Plugin version bumps required** (blocker, new — `require-plugin-version-bump.sh`
  hook-denies any commit touching a file inside a plugin's tree with no
  matching `.claude-plugin/plugin.json` version bump): this phase's 1a+1b
  dispatches touch `plugins/npm-semver/hooks/_lib.sh`,
  `plugins/npm-semver/hooks/require-npm-version-bump.sh`,
  `plugins/plugin-semver/hooks/_lib.sh`,
  `plugins/plugin-semver/hooks/require-plugin-version-bump.sh`,
  `plugins/skill-management/hooks/_lib.sh`,
  `plugins/skill-management/hooks/require-skill-review.sh`, and
  `plugins/lovable-cloud/hooks/_lib.sh`. Bump each of the 4 plugins'
  `.claude-plugin/plugin.json` version per `plugin-semver`'s bump-magnitude
  table before this phase's commit — the exit-124/GIT_DIR/bare-`&`
  behavior changes above are minor-or-greater, not patch-only. Current
  versions: npm-semver 1.0.3, plugin-semver 1.1.4, skill-management 3.2.2,
  lovable-cloud 3.2.4.

**Rollback**: each dispatch is one commit; revert that commit to undo. 1a's
new `_lib.sh` helper (C7) has no consumer outside `guard-settings-session-keys.sh`
at merge time, so 1a is revert-safe in isolation even after 1b lands.

### Phase 2 — `require-ready-for-review.sh` bare `gh` call (S1)

- `claude/.claude/hooks/require-ready-for-review.sh:191` — bare `timeout 5
  gh pr view --json number --jq '.number'`. Reuse: `_lib_capped` (`_lib.sh:27-48`,
  command-agnostic — this is the first `gh`+`_lib_capped` call site in the
  repo, not a copy of an existing `gh`-specific pattern). New line:
  `PR_NUMBER=$(cd "$CWD" 2>/dev/null && _lib_capped gh pr view --json number
  --jq '.number' 2>/dev/null)`.

### Phase 3 — transcript-analysis.py redaction-default gaps (S4, S5, S6, S7, S15, I1, SC5)

All in `claude/.claude/scripts/transcript-analysis.py` (11,184 lines).
Reuse pattern for the label/session-ID redaction shape:
`cmd_user_input:508-732` (`--redact` flag `:516`, `redact_map:527`,
`_assign_session_redact_label`/`_redact_session_id:633-637`,
`_redact_proj_label:641`) — note this pattern redacts labels/session-IDs
only, not message text (documented limitation at `p_user_input:10253-10259`).

**Citations in this phase and Phases 4-5 are line numbers against the file
as of this plan's writing; each of those three phases edits the same
11,184-line file and runs after this one, so its own citations will have
drifted by the time it's dispatched. Re-resolve every citation below by
function/symbol name (a fresh `grep -n`) at dispatch time — do not trust
a phase's recorded line numbers verbatim once an earlier phase against the
same file has landed.**

S4, S6, and S7 below only *add* a previously-absent `--redact` flag — no
existing caller passes it, so these are additive, not breaking. S15 is
different: it *removes* an existing `--redact` flag and replaces it with
`--no-redact` (matching `p_cost`'s shape), which breaks any caller
currently invoking `audit-routing --redact` — that invocation exits with
an argparse "unrecognized arguments" error on the next `git pull`, with no
deprecation window. S15's bullet updates the docs, help text, and test
helper; it deliberately ships **no** compatibility shim. Accepted because
the flag was opt-in redaction and every in-repo caller is updated in the
same commit, so the only breakage is a stow user's own ad-hoc alias, and
the failure is loud and immediate rather than silent — a shim that
accepted `--redact` as a no-op would instead leave that user believing
they had opted into something. Record the break as a `CHANGELOG.md`
Migration note (see the "CHANGELOG entries" section) so the user
learns of it from the changelog, not from the traceback.

- **S4** (`judgment-pair`): `cmd_judgment_pair:1834-1996`. No `--redact` in
  its argparse block (`p_jp:10463-10493`). Add the flag using the
  `cmd_user_input` model.
- **S5** (`review-trace`): `cmd_review_trace:1705-1832`, raw path `:1805`,
  raw message `:1821`/`:1826`. No `--redact` (`p_review_trace:10433-10461`).
  Add the flag; message-text redaction inherits `cmd_user_input`'s
  documented label-only scope limit unless extended (see Out of scope).
- **S6** (`audit-routing-samples`): `cmd_audit_routing_samples:8263-8449`,
  raw fields `:8424-8433`. No `--redact` (`p_audit_samples:11019-11043`).
- **S7** (`buckets`/`fail-seq`/`struggle`/`duration`):
  `:232-289`, `:292-371`, `:374-411`, `:734-770` — all print raw branch
  names (`:287-288`, `:768-769`), none has `--redact`
  (`p_...:10221-10224,10226-10229,10231-10234,10262-10266`). Reuse
  `cmd_subagents`' `_branch_label` closure (`:901-909`) using
  `_assign_root_scoped_redact_label` (`transcript_analysis/redaction.py:197`).
- **S15** (`audit-routing`): `cmd_audit_routing:3039-`, redact defaults to
  `False` (`:3048`); `--redact` is opt-in (`p_audit:10495-10519`,
  `:10512-10517`). Flip to default-on, replacing `--redact` with
  `--no-redact` to opt out, and refuse under multi-root — matching
  `p_cost`'s existing help text (`:10521-10527`, "Redacted by default").
  Implement the multi-root refusal via SC5's own
  `_apply_no_redact_multi_root_refusal(args, scan_roots, subcommand_name)`
  helper below, making `audit-routing` its 9th call site —
  `cmd_audit_routing` currently has no multi-root refusal of any kind, so
  this is a new call site, not a conversion of an existing bespoke one.
  This is a breaking flag-shape change: two SKILL.md files and one
  canonical reference doc document the current opt-in contract by name and
  must be updated in this same phase — `transcript-analysis/SKILL.md:41`
  (`audit-routing --since 35d --redact` example) and `:92-93` (explicitly
  contrasts `audit-routing`'s opt-in `--redact` against `cost`'s
  default-on behavior — that contrast disappears once this fix lands),
  `transcript-narrative/SKILL.md:77,80`, and `docs/transcript-analysis.md`'s
  own `## audit-routing` section (~lines 507-556, the canonical
  flag-reference doc for this subcommand) plus its cross-references at
  lines 45, 233, 570. Two more stale-parity citations need the same
  treatment: `transcript_analysis/reviewer_yield.py:567`'s docstring
  ("`--redact` is accepted for CLI parity with cost/audit-routing") and
  `transcript-analysis.py:10355-10360`'s `reviewer-yield --redact`
  argparse `help=` text (also framed as "parity with cost/audit-routing")
  both go stale once `audit-routing` no longer has a bare opt-in
  `--redact` flag — update both in the same commit
  [verified: this session, both read directly]. Update the shared
  `_audit_routing_args(redact: bool = False, ...)` test helper
  (`claude/.claude/scripts/tests/conftest.py:456`) to a `no_redact: bool =
  False` param, mirroring the sibling `_cost_args` helper's existing
  `no_redact` shape (`conftest.py:412,444`) — this helper is consumed at
  19 call sites (18 in `test_transcript_analysis.py`, 1 in
  `test_transcript_cost.py:536`; grep
  `_audit_routing_args(` in both files at dispatch time for the current
  list, since line numbers drift), 3 of which pass `redact=True`
  explicitly (`test_transcript_analysis.py:3661,13970`,
  `test_transcript_cost.py:536` — confirmed this session) and will hard-break
  (`TypeError`) if the param is renamed without updating them; update
  every call site to the new signature in the same commit, matching this
  phase's own citation-drift discipline already applied to
  `transcript-analysis.py` itself. Add two regression tests, not one:
  `audit-routing` with no flags now redacts by default (guarding the flip),
  and `audit-routing --no-redact` on a single root actually disables
  redaction (guarding the opt-out path, which nothing else in this phase
  exercises).
- **I1** (`read-scope`): `_read_scope_report:4226-4287` uses
  `_root_index_for_path` (`scope.py:615`, scan-order) instead of
  `_redaction_ordinals` (`scope.py:180`, resolved-path-sorted) at `:4284`;
  label print `:4407-4408`. Switch to `_redaction_ordinals`, matching
  `cmd_edit_format:3717-3762` and `cmd_subagents:812-817` (both already use
  the correct helper with an explanatory in-code comment).
- **SC5** (8 duplicate no-redact/multi-root-refusal sites, undercounted by
  the report as 4): `read-scope:4244-4262`, `context-composition:5093-5111`,
  `cache-efficiency:5425-5438`, `cache-rebuild:5616-5629`,
  `context-distribution:3305-3319`, `edit-format:3717-3727`,
  `rearm-backtest:9731-9738`, `plan-boundary:9981-9991`. Extract shared
  `_apply_no_redact_multi_root_refusal(args, scan_roots, subcommand_name)`
  helper; call from all 8 sites — collapses ~96 duplicated lines. The
  helper must **return the resolved `redact: bool`**, not be void:
  `context-composition:5128,5132`, `cache-efficiency:5450,5454`, and
  `cache-rebuild:5648-5658` all read a local `redact` after the block this
  extraction removes [verified: this session's specialist review], so a
  void helper either forces those 3 sites to recompute
  `redact = not bool(getattr(args, "no_redact", False))` themselves —
  contradicting the ~96-line collapse — or leaves a `NameError` if the
  implementer deletes the assignment assuming full replacement.
  Centralizing 8 (soon 9) independent refusal sites into one function also
  makes that function a single point of failure for a leak-refusal
  control, so test it directly rather than only through call sites: add a
  dedicated allow/deny unit-test pair against
  `_apply_no_redact_multi_root_refusal` itself (single-root allows;
  multi-root with `--no-redact` refuses). Two of the 8 sites have **no
  multi-root-refusal test today** — `context-composition` and
  `rearm-backtest` [verified: this session's specialist review; the other
  6 have `test_no_redact_refused_with_multi_root` or an equivalently-named
  class] — so add per-site refusal tests for those two as well, or the
  extraction can silently regress their refusal with nothing catching it.
- Test coverage: add a `--redact`-default fixture local to each changed
  subcommand's existing test class in
  `claude/.claude/scripts/tests/test_transcript_analysis.py`.

**Rollback**: this phase's commit reverts cleanly in isolation — none of
its fixes are consumed by Phase 4 or Phase 5's own changes to the same
file (different functions, no shared new symbol).

### Phase 4 — transcript-analysis.py ledger hygiene (S16, S17, SC4)

Also in `transcript-analysis.py`. Re-resolve every citation below by
symbol name at dispatch time — see Phase 3's citation-drift note.

- **S16** (PR-cost ledger permissions): `_write_pr_cost_ledger_file:6885-6911`
  — chmod-preserve branch `:6903-6904` only applies 0600 on create.
  `docs/pr-cost.md:56` requires restrictive perms on every write, not just
  creation. Contrast `_write_cost_ledger_file` (deliberate preserve, for the
  *public* ledger) `:6152-6189`, chmod-preserve `:6183-6184` — that one is
  correct as-is. Fix: unconditional `os.chmod(tmp_name, 0o600)` in
  `_write_pr_cost_ledger_file`; drop the exists()-preserve branch there
  only. This closes the write path — [verified: this session's specialist
  review confirmed `tempfile.mkstemp`'s temp file is always ≤0600 and the
  chmod runs before `os.replace`'s atomic rename, so there is no window
  where the final path is visible at looser permissions on any write after
  this fix ships. The fix is forward-only, though: a ledger file already
  on disk with loose permissions (inherited from the pre-fix
  exists()-preserve branch, or a manual `chmod`) keeps them until its
  *next* `--record`.] Add a migration note to `docs/pr-cost.md` instructing
  existing users to `chmod 600` their ledger file once, since this fix
  provides no automatic remediation path for a ledger already on disk.
  Add a regression test pinning both the fix and its forward-only
  semantic: a pre-existing ledger file created with loose permissions
  keeps them across a write that doesn't hit `--record`, then gets 0600
  applied on its next `--record` write.
- **S17** (GIT_DIR stripping): `_git_remote_origin_host_and_owner_repo:6941-6963`
  (subprocess call `:6951-6955`, no `env=`) and
  `_local_git_object_exists_batch:7215-7241` (subprocess call `:7229-7233`,
  no `env=`) — both can pick up an ambient `GIT_DIR`/`GIT_WORK_TREE`. Reuse
  pattern: `_ledger_path_is_git_tracked:5897-` env-prep block `:5911-5919`
  (copies `os.environ`, strips `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE`,
  sets `LC_ALL=C`). Extract into a shared `_local_git_env()` helper (now
  needed in 3 places) and apply ahead of both subprocess calls. Add a
  regression test per new call site asserting an ambient `GIT_DIR`
  pointing at a different repo does not redirect the call, mirroring the
  GIT_DIR regression test this plan already requires for Phase 1's
  `_lib_capped_for` choke point.
- **SC4** (lock/write-file duplication): lock pair
  `_acquire_cost_ledger_lock:6264-6288` vs.
  `_acquire_pr_cost_ledger_lock:6914-6938` — near-identical; collapse into
  `_acquire_ledger_lock(lock_f, ledger_label)`. All 4 existing call sites
  must switch to the new signature — [verified: this session's specialist
  review] production sites `transcript-analysis.py:6490` and `:7758`, plus
  two direct test invocations at `test_transcript_analysis.py:16292` and
  `:16300` (`_mod._acquire_pr_cost_ledger_lock(lock_f)`, the old
  single-arg signature — these break immediately if not updated in the
  same dispatch). Write-file pair `_write_cost_ledger_file:6152-6189` vs.
  `_write_pr_cost_ledger_file:6885-6911` — share a
  temp-file/read-back/parse-verify/atomic-replace skeleton but differ in
  format/parse-fn/exception-class/tempfile-prefix, and (after S16)
  permission policy diverges intentionally; the plan keeps both functions'
  external names/signatures and only factors internal boilerplate, so this
  half has no additional call sites to update. Extract only the genuinely
  identical ~20-line write/verify/replace/cleanup boilerplate into a helper
  taking `(new_text, tmp_prefix, parse_verify_fn, parse_error_cls,
  permission_policy)`; if the extraction doesn't come out clean, leave the
  duplication per CLAUDE.md's "a small duplicated value can beat a bad
  abstraction" exception rather than force it.

**Rollback**: revert this phase's commit. S16 and SC4's lock-pair collapse
touch adjacent but non-overlapping line ranges in the same functions SC4's
write-file half leaves alone, so this phase reverts cleanly as one unit;
it is not independently revert-safe *per finding* (S16 alone) because SC4's
collapsed `_acquire_ledger_lock` is dispatched in the same commit and a
partial revert would leave callers referencing a function that no longer
exists in its pre-collapse two-function form.

### Phase 5 — `transcript-analysis.py` dispatcher-usage dedup (C1)

Re-resolve every citation below by symbol name at dispatch time — see
Phase 3's citation-drift note.

- `_dispatch_usage_summary:2556-2664`, `cmd_subagent_mix:2220`, call site
  `:2390-2392`. `_dedup_turns_by_request_id` is imported from
  `transcript_analysis/pricing.py:162` (`dedup_turns_by_request_id`) at
  `transcript-analysis.py:82`, with 13 existing call sites (lines 841, 3086,
  3382, 3625, 5075, 5460, 5472, 5697, 7372, 8055, 8292, 8581, 9364) — none in
  `_dispatch_usage_summary`. Reuse the pattern at `:7372`
  (`_compute_pr_cost_branch_totals`, the structurally closest analog):
  materialize records, `records = _dedup_turns_by_request_id(records)  #
  dedup before pricing (must run first, see pricing.py)`, then iterate.
  Change `_dispatch_usage_summary` from streaming-parse-per-line to
  materialize-then-dedup-then-iterate. This trades the current O(1)-memory
  streaming pass (one record parsed, aggregated into scalars/sets, then
  discarded) for O(file size) peak memory — holding every record's full
  body (thinking blocks, tool_use payloads, embedded diff/image content in
  tool_result blocks), not just the `model`/`usage`/`timestamp` fields this
  function consumes. Accepted as a transient, per-dispatch-invocation cost
  (this subcommand processes one dispatch transcript at a time, not the
  full corpus) rather than switching to a lighter tuple-extraction dedup —
  matches every other `_dedup_turns_by_request_id` call site's existing
  shape, at the cost of this one tradeoff. The stronger driver for this
  fix is correctness, not just consistency: `_merge_assistant_run`'s own
  docstring states `output_tokens` "ascends within the run, only reaching
  its billed value on the last record", and that `input_tokens` and the
  `cache_*` classes are *identical* — not delta — across every record in a
  run [verified: this session's specialist review]. The pre-fix streaming
  code prices every raw assistant line's own `usage` independently, so a
  multi-block turn is overcounted twice over: the ascending output
  component, and — the larger driver, since cache/context tokens dominate
  spend per this repo's own `docs/cost-levers-considered.md` — the
  identical input/cache-read/cache-write tokens re-billed once per
  physical record. This is a real dollar-overcounting bug, not a
  shape-consistency nit; materialize-then-dedup fixes both components.
- Test: `TestSubagentMixDollars`,
  `claude/.claude/scripts/tests/test_transcript_analysis.py:987-1264` (7
  tests, zero requestId-sharing fixtures) — add one, mirroring the existing
  multi-record fixture patterns at `:4028` or `:6824`.

**Rollback**: revert this phase's commit; no other phase's changes to
`transcript-analysis.py` consume `_dispatch_usage_summary`.

### Phase 6 — Review-pipeline dispatcher lag (S12, C4, C15, C16, D5) + SC7 cap resolution

- **SC7 / A4-A5 resolution** (do this first in this phase, since S12/C4 add
  content to the same file): `review-permissions/SKILL.md` is exactly at its
  200-line hard cap (`wc -l` = 200 — [verified: this session]) with zero
  headroom. Add `claude/.claude/skills/review-permissions/SKILL.md` to
  `check-skill-length.sh`'s `limit_for()` case statement (`:68-77`) with the
  same 500-line allowance already granted to `code-review/SKILL.md`,
  `plan-review/SKILL.md`, and `plan-review/ROUTING.md` — this file is a
  dispatcher-adjacent target of repeated cross-references (S12, C4, C15 all
  touch it), so trimming existing content risks losing it again under
  future edits. This is the plan's own scope decision (A5) — flagged to the
  engineer at presentation, not previously confirmed. Add a positive/negative
  test pair for this new exception, mirroring the existing
  `test_code_review_over_default_under_override_allows`/`_denies` pattern
  in `test_check_skill_length.py`.
- **C15's own target file also has zero headroom** (not in the source
  report; the same conflict SC7/A4-A5 resolves for
  `review-permissions/SKILL.md` — see A9): C15 below adds a sentence to
  `plugins/skill-management/skills/skill-review/SKILL.md`, which is also
  at exactly 200 lines (`wc -l` = 200) with zero headroom. Extend the same
  `limit_for()` 500-line exception added above to this file too, in the
  same edit — same rationale as A5 (dispatcher-adjacent, repeatedly
  cross-referenced, trimming risks losing content again). This is an
  extension of A5's scope decision, not a new one — flagged to the
  engineer alongside A5 and A6 at presentation. Add the same test-pair
  parity for this file's exception as SC7's above.
- **S12**: `code-review/SKILL.md:176` and `plan-review/SKILL.md:233` — add
  "a bare `permissions.deny` entry was added, or `permissions.defaultMode`
  changed" to each skill's `/review-permissions` dispatch sentence.
  `review-permissions/SKILL.md`'s own TRIGGER (`:3-8`) and checklist item 23
  (`:185-189`) already cover this scope — no edit needed there for S12.
- **C4**: `review-permissions/SKILL.md:16` and `:27` — reword "Read the
  `permissions.allow` array..." to also name bare `permissions.deny`
  entries and `permissions.defaultMode`.
- **D5** (same TRIGGER text S12 edits — [verified: this session, read
  directly]): `review-permissions/SKILL.md:9`'s `DO NOT TRIGGER` clause
  names only "other settings.json fields (env, model, theme)" as
  explicitly out of scope; `settings.json`'s `skillOverrides` map (controls
  whether a skill's description is auto-trigger-eligible at all — flipping
  a skill to `"name-only"` or `"off"` silently removes it from the
  always-loaded budget) is named by neither the TRIGGER nor DO NOT TRIGGER
  list, leaving it unrouted rather than deliberately excluded. Add
  `skillOverrides` changes to the TRIGGER list (it is security-adjacent —
  a silent auto-trigger removal can defeat a security-relevant skill the
  same way a bad `permissions.allow` rule can) in the same edit that
  covers S12's `permissions.deny`/`defaultMode` addition.
- **C15**: `plugins/skill-management/skills/skill-review/SKILL.md:79-81` and
  checklist item `:153-154` — add a sentence noting `check-skill-length.sh`'s
  `limit_for()` grants a cap above this skill's own 200/300 target to a
  named set of files. Enumerate that set completely, against
  `check-skill-length.sh:68-77` at dispatch time rather than from this
  plan: it is `code-review/SKILL.md`, `plan-review/SKILL.md`,
  `plan-review/ROUTING.md` at 500; `pr-description/SKILL.md` at **210** — a
  fourth entry, easily missed because it is not part of the 500 group; and,
  after this phase, both `review-permissions/SKILL.md` and this file itself
  (`skill-review/SKILL.md`, per A9) at 500. A sentence that lists only the
  500 group would ship a fresh docs-accuracy defect inside the phase whose
  own job is closing them.
- **C16**: `agent-review/SKILL.md` item 16 (`:152`, checks 2 classes:
  tool-verb + bias-anchor) is missing 2 classes that
  `skill-review/SKILL.md` item 12 (`:187-188`) already checks:
  vendor/product-name-anchoring-a-category, and borrowed-interface-shape-
  no-vendor-token. Port the 2 missing classes into `agent-review/SKILL.md`
  item 16, preserving its existing staff-* vendor-name carve-out.
- Run `/skill-review` on every `SKILL.md` touched in this phase per
  `.claude/rules/skill-and-agent-self-review.md` (hook-enforced via
  `require-skill-review.sh`).
- **Plugin version bump required**: this phase's C15 edit touches
  `plugins/skill-management/skills/skill-review/SKILL.md` —
  `require-plugin-version-bump.sh` hook-denies the commit unless
  `plugins/skill-management/.claude-plugin/plugin.json`'s version is
  bumped (patch, for a documentation-only addition) in the same commit.

**Rollback**: revert this phase's commit, including the `limit_for()`
exception addition — `check-skill-length.sh` reverts to enforcing 200 lines
on `review-permissions/SKILL.md` again, which is safe precisely because the
revert also removes this phase's own added content.

### Phase 7 — Docs accuracy (S8, S9, S10, S18, S24, C2, C3, C11, C12, C13, C17, C22, C23, D4, D16, I2, I4)

- **S8**: `SECURITY.md:5-9` — Scope section omits credential/PII/
  network-install/repo-relocation/reviewer-mutation/plan-mode guards
  (documented elsewhere in the repo). Enumerate fully or defer to
  `docs/security-hardening.md` as authoritative.
- **S24** (co-located with S8, same file/section — [verified: this
  session, read directly]): `deny-reviewer-tree-mutation.sh:58-70`'s own
  "Known gaps" comment already documents that it does not resolve
  arbitrary Bash write-target redirection (`cp scratch src/x`, `sed ... >
  src/x`, `tee src/x`) for the 8 canary reviewer agents carrying `Bash` —
  a known, reasoned, narrow residual (requires a cooperating or
  successfully-injected agent to exploit), already disclosed at the hook
  level but never surfaced at the instruction-surface level the way S8's
  fix enumerates the guard's existence. When S8 enumerates the
  reviewer-mutation guard in `SECURITY.md`'s Scope section, add one clause
  naming this specific residual (write-target redirection is not fully
  closed) so a reader of `SECURITY.md` alone — not just the hook's own
  comments — knows the guard's boundary.
- **S9**: `SECURITY.md:7` — conflates the always-on tracker-ID regex with
  the opt-in blocklist. Reword to distinguish them.
- **S10**: `docs/security-hardening.md:354-355` — claims 14 managers
  including cargo/bundle/poetry/deno add; actual is 15 entries
  (`settings.json:50-64`), none of those four present. Replace with the
  real 15-entry list.
- **S18**: `SECURITY.md:9` — no opt-in/off-by-default framing for
  `require-worktree-for-git-writes` (per `README.md:254-256`). Append an
  opt-in clause.
- **C2**: `docs/transcript-analysis.md:702,709,842-863` — documents a
  removed `handoff-ratio` subcommand (renamed `spend-over-threshold`,
  already correctly documented in `docs/handoff-nudge.md:92-112`). Fix
  refs; delete/replace the stale section.
- **C3**: `docs/design-decisions.md:97` — claims
  `check-runner-bash-guard.sh` was "kept as reference," self-contradicted 2
  lines later (`:99`, "Retired 2026-06-23") and by
  `docs/case-studies/check-runner.md:86` ("now deleted"). Fix the wording.
- **C11**: `docs/skills.md:112-116` — "two-method model" vs. actual four
  (`evals/run_skill_evals.py:78-80` `VALID_METHODS`). Match
  `CONTRIBUTING.md:48-50`'s correct wording.
- **C12**: `docs/design-decisions.md:23` — stale citation rows; real rows
  are now 347,367,371,376,377,378,380,381,385 (double-item row is 367, not
  347). Update citations; the 10/9 count itself is still correct.
- **C13**: `docs/scripts.md:37` — claims `install.sh` doesn't check
  `python3` version; actually `install.sh:25-35` checks `>=3.11`.
- **C17 / I4** (same sentence, one fix resolves both, plus a sibling site
  the original report didn't catch): `evals/README.md:400` — "CI lints
  `claude/.claude/` only"; actual is `ruff check claude/.claude/ plugins/`
  (`tests.yml:170`). The identical staleness exists a second place: root
  `CLAUDE.md`'s Commands block documents
  `.venv/bin/pytest claude/.claude/` and
  `.venv/bin/ruff check claude/.claude/`, both omitting `plugins/`, while
  CI actually runs both across `claude/.claude/ plugins/` — fix this
  structural sibling in the same commit (append " plugins/" to both
  existing command lines; this is a same-line edit with ~zero net line
  growth, so it doesn't materially affect Phase 8's headroom check on this
  same file).
- **C22**: `docs/scripts.md:54` — "12 valid invocation shapes" for
  `marker.sh`; actual is 13 allow entries (`settings.json:4-16`) + 2
  `clear-stale` variants validated by regex but prompting rather than
  auto-approving = 15 total validated shapes.
- **C23**: `docs/cost-levers-considered.md:210` — cites
  `transcript-analysis.py:7782-7783`; actual current defs are at
  `:5495-5496` (file now 11,184 lines). Citation-only fix.
- **D4**: `docs/case-studies.md:5-14` — index lists 8; actual is now 10
  files. Missing both `cold-cache-attribution.md` (cross-linked elsewhere)
  and a newly-appeared, fully orphaned `pr-cost-context-bucket.md` (zero
  inbound links anywhere). Add both.
- **D16**: `docs/precompact-hook-behavior.md` is unreachable from any
  shipped doc — the only reference is `.claude/plans/precompact-review-snapshot.md`.
  Add a link from a shipped doc (e.g. `docs/hooks.md`).
- **I2**: `README.md:497-498` — commands list omits `plugins/` and the
  timing-split second pass. Actual: `tests.yml:160,166,170`.

### Phase 8 — Instruction-surface (S11, S19, S20, D6, C14, C24, SC6)

- **Length-cap headroom check** (do this first in this phase; not in the
  source report): root `CLAUDE.md` is 182
  lines against `check-claude-md-length.sh`'s hook-enforced 200-line cap
  ([verified: this session, `wc -l`] — re-verify at dispatch time, since
  Phase 7's C17/I4 fix also touches this file's Commands block, though as
  a same-line edit with ~zero net line growth). This phase stacks three
  separate one-sentence additions onto it (S11, S20, D6 below) against
  only 18 lines of headroom. If the three additions together would exceed
  200 lines, tighten existing prose elsewhere in the file to make room
  rather than requesting a `limit_for()` exception for this file — unlike
  `review-permissions/SKILL.md` (Phase 6), root `CLAUDE.md` is a
  contributor-facing overview document, not a dispatcher-adjacent
  reference the exception pattern was designed for.
- **S11**: root `CLAUDE.md:108-114` (text `:111-112`) — "don't merge your
  own PRs" is scoped to literal `gh pr merge`;
  `block-gh-pr-merge.sh:21-23` documents a `gh api .../pulls/N/merge`
  bypass as an intentionally excluded case, never surfaced in `CLAUDE.md`.
  Add a sentence noting the gap.
- **S19**: `claude/.claude/rules/github-actions-workflows.md:2-4`
  (frontmatter `paths`), risk text `:35-37` — glob matches
  `workflows/*.yml` only, not composite actions (`action.yml`). No
  `action.yml` exists yet in the repo (confirmed via `find`). Add
  `**/.github/actions/*/action.yml` and `.yaml` to `paths`. Add a
  case-file-style match assertion (e.g. `fnmatch`/`pathlib.match` against
  a synthetic `.github/actions/foo/action.yml` path) confirming the new
  glob actually matches — `test_rules_frontmatter.py` explicitly disclaims
  verifying individual glob correctness on its own.
- **S20**: root `CLAUDE.md` tiers `:121-143`, default statement
  `:137-138`, Provenance `:145-159` — "if in doubt, strip it" is attached
  to tier 2 only, never restated for tier 3 (Provenance, the
  weakest-enforced tier). Add one sentence at the end of the Provenance
  paragraph.
- **D6**: root `CLAUDE.md:30-37` (this repo's own contributor-workflow
  file, not `claude/.claude/CLAUDE.md`) — [verified: this session, read
  directly] describes the four `.claude/rules/*.md` path-scoped files as
  loading "automatically via `paths` frontmatter matching" for the main
  session, but never states whether that mechanism also fires inside a
  dispatched subagent (`code-writer`, this repo's prescribed path for
  delegated Dockerfile/SQL/shell/GH-Actions authorship). If it doesn't
  propagate, the security- and correctness-relevant content in those four
  files may never reach the context that most needs it. This needs an
  empirical check before drafting the fix text — dispatch a throwaway
  `code-writer` agent against a file matching one rule's `paths` glob and
  confirm from its own transcript whether the rule's content was present
  in its context. Document whatever the check finds (propagates / does
  not propagate / propagates only under condition X) as a new sentence in
  this paragraph; if the check is inconclusive, state that uncertainty
  explicitly rather than asserting an unconfirmed behavior either way.
- **C14**: `rules/sql-ddl-conventions.md:18-19,76-77,80-84,103-106`,
  `staff-data-engineer.md:48`, `staff-analytics-engineer.md:43-46,57` —
  duplication is still one-directional-acknowledged. SSOT-exception
  citation is at `ai-instruction-and-memory-files/SKILL.md:117`. Add an
  explicit cross-reference from the duplicated sites back to that
  exception.
- **C24**: `docs/rules-references.md:1` — title "References — rules" reads
  generic/plural but the 130-line file is GH-Actions-only;
  `dockerfile-conventions.md:11`, `sql-ddl-conventions.md:13-15` cite
  sources inline instead of pointing here. **Rename the title** to name its
  actual scope (GitHub Actions workflow references), and leave the two
  inline citations where they are. Expanding the file and redirecting them
  is the larger option, but it converts a title-accuracy fix into a
  docs-restructure spanning three files for no reader benefit this finding
  identifies — and the inline citations are correct where they sit, per
  CLAUDE.md's "place prose where its reader and altitude match."
- **SC6** (informational, no code fix): `claude/.claude/CLAUDE.md` is now
  150 lines (cap 200 per `check-claude-md-length.sh:69,89`) — headroom is
  50 lines, not the report's 59. Note the current state in this phase's PR
  description; no file change required.

### Phase 9 — CI/dependency hygiene (S21, S25, I5, D1, D7)

- **S21**: `.github/dependabot.yml:1-11` covers `github-actions` only, no
  `pip` ecosystem. `requirements-dev.txt:1-5` uses wildcard pins with no
  `--require-hashes` (`tests.yml:142`);
  `plugins/skill-management/requirements.txt:1` has its own, separate
  `pyyaml==6.*` dependency. Dependabot's `pip` package-ecosystem entries
  are directory-scoped, not recursive, so add **two** entries — one with
  `directory: "/"` (covers `requirements-dev.txt`) and one with
  `directory: "/plugins/skill-management"` (covers its
  `requirements.txt`) — not the single block a one-line reading of this
  finding would produce. [Unverified: whether Dependabot's pip parser
  correctly proposes update PRs against wildcard specifiers like
  `pytest==8.*` versus exact pins — confirm against GitHub's Dependabot
  pip-ecosystem documentation before treating "block added" as "gap
  closed"; add this check to this phase's Verification.]
- **S25, rescoped** — [verified: this session's specialist review, `ruff
  check --select S` against `claude/.claude/ plugins/` on current `HEAD`
  returns **10,547 findings**: 8,506 `S101` (100% confined to test-path
  files — zero non-test-path `S101` findings), 1,034 `S603`, 848 `S607`,
  127 `S108`, 24 `S105`, 5 `S103`, 3 `S311`, spanning ~100 distinct
  non-test files for the `S603`/`S607`/etc. classes alone]. This is not
  the "triage a few findings" scope the original report finding implies,
  and `tests.yml`'s Lint step (`ruff check claude/.claude/ plugins/`, no
  `|| true`) is a hard blocking gate on every PR after this one lands —
  landing the full `"S"` ruleset as written would either force ~2,000
  non-test findings into one non-PR-sized diff or break CI for every
  subsequent PR, including this phase's own commit. Split: **(a)** add
  `"S"` to `pyproject.toml:6`'s select list, an `S101` ignore on test-glob
  paths (closes 100% of that class with no per-line judgment needed), and a
  repo-wide `ignore =` entry for the deferred non-test codes
  (`S603,S607,S108,S105,S311` — `S103` is deliberately excluded from this
  entry, see below). Three constraints on how those two ignore entries are
  written:

  - Add the `S101` glob as a new **row inside the existing**
    `[tool.ruff.lint.per-file-ignores]` table (`pyproject.toml:12`, already
    populated), not as a second table header. TOML forbids a repeated table
    header, and `[tool.pytest.ini_options]` lives in the same file, so a
    duplicate breaks pytest collection as well as ruff [verified: this
    session's specialist review].
  - Give the `ignore =` entry a one-line comment naming what was deferred
    and pointing at this plan's Out-of-Scope entry. CLAUDE.md's
    suppression-rationale rule is not satisfied by a rationale that lives
    only in a plan file.
  - `S103` is excluded from this repo-wide `ignore =` entry entirely — see
    the closing note on this bullet for why and for the mechanism that
    keeps it enforced.

  Both ignore entries land in this phase: without the repo-wide one, this
  phase's own commit leaves the 2,041 residual findings unsuppressed and
  breaks the CI Lint gate it is meant to fix, contradicting this plan's
  own Verification claim of no unexpected CI failure. **(b)** the
  remaining ~100-file `S603`/`S607`/
  `S108`/`S105`/`S311` triage (`S103` is fully handled within this phase,
  not deferred to (b) — see below) (each needing a judgment call between
  a real fix and replacing the blanket ignore with a per-line
  `# noqa: S... — <rationale>` per CLAUDE.md's suppression-rationale rule,
  plus a named owner to adjudicate them) is **out of scope for this
  phase** — see Out of Scope. This means Phase 9 lands the ruleset fully
  enabled but with the non-test codes globally suppressed rather than
  individually triaged; (b) is what narrows that suppression down to real
  fixes and named exceptions over time. One consequence to state rather
  than discover later: `S103` is bad-file-permissions — the exact construct
  class Phase 4's S16 hardens (`os.chmod(tmp_name, 0o600)` on the PR-cost
  ledger). Suppressing it repo-wide gives that fix zero forward regression
  protection: a later edit reintroducing loose ledger permissions passes
  lint.

  **`S103` mechanism [engineer-confirmed]:** `ruff`'s `per-file-ignores`
  table is additive-only — it adds suppressions on top of the top-level
  `ignore =` list; it cannot un-suppress a rule the top-level list already
  silences for one file [verified: this session's specialist review,
  reproduced empirically against `ruff 0.6.9` — an `os.chmod(tmp, 0o777)`
  call in a file named in a `per-file-ignores` "exclusion" row still
  produced zero findings when `S103` was also in the top-level `ignore =`
  list]. A `per-file-ignores` row naming `transcript-analysis.py` does
  **not** preserve S16's CI protection if `S103` stays in the top-level
  `ignore =` alongside `S603`/`S607`/etc. — do not implement it that way.
  Instead: leave `S103` **out of** the top-level `ignore =` entry (so it
  stays selected and enforced by default), and add `S103` to
  `per-file-ignores` scoped only to whichever of the 5 current `S103`
  findings are *not* in `transcript-analysis.py` — identify those file(s)
  at implementation time via `.venv/bin/ruff check --select S103
  claude/.claude/ plugins/` (this plan does not cite their locations, only
  the aggregate count). This leaves `S103` enforced everywhere except the
  named files, with `transcript-analysis.py` uncovered by any suppression
  and therefore still protected. Add a verification step to this phase: a
  scratch `os.chmod(tmp, 0o777)`-shaped line added temporarily to
  `transcript-analysis.py`, confirming `ruff check --select S103` still
  flags it post-config, then remove the scratch line — CI passing alone
  does not prove `S103` is still enforced there, since a misconfigured
  suppression also produces a green run.
- **I5** (Very Low, informational, no fix needed): `dependabot.yml:7` limit
  is 3; exactly 2 actions are pinned repo-wide. Note only.
- **D1**: `evals/test_measure_subagent_model_resolution.py` (876 lines) has
  zero CI collection (`tests.yml:160,166` roots are `claude/.claude/
  plugins/` only; `pyproject.toml:18` `pythonpath` is import-only, not a
  collection root). This finding reopens a decision the source plan
  already made and recorded:
  `.claude/plans/plan-mode-model-resolution-experiment.md:450-457` states
  "No `tests.yml` change... Recorded as a deliberate gap, not an
  oversight" — a different, more directly on-point rationale (wiring cost
  vs. benefit for a rarely-changed parser) than item M7 (which covers the
  separate, intentionally-uncollected live script and does not apply to
  this test file); `evals/README.md:23-25` separately states the never-CI
  posture applies equally to this test. **[engineer-confirmed] at
  presentation**: implementing D1 as a CI-wiring change overrides that
  prior, reasoned "no" — confirmed explicitly rather than treated as
  uncontested. Also confirmed this session: the test makes no live Claude
  API call. Its external interactions are `subprocess.run(["claude",
  "--version"])` (stubbed via `monkeypatch` at
  `test_measure_subagent_model_resolution.py:701-708`) and a
  `subprocess.Popen`-based live-dispatch launcher (stubbed at the function
  level via `monkeypatch.setattr(msmr, "_run_claude_to_completion",
  fake_run_claude)`, line 399) — both fully intercepted, so collecting it
  in CI carries no API cost [verified: this session and independently
  re-verified by round-4 review]. A round-4 review also found this test
  hard-asserts on the live, committed frontmatter content of two other
  files (`claude/.claude/agents/staff-backend-engineer.md` and
  `Explore.md`, via `TestAgentFrontmatterParsing` and
  `TestGatherEnvironmentReport::test_reports_expected_keys`) — collecting
  it in required CI means a routine frontmatter edit to either agent file
  (e.g. re-pinning `model:`/`tools:` per the Model & Effort Routing rule)
  now fails this test with no obvious signal that the failure is content
  drift rather than a resolution-logic regression. Accepted as-is per the
  test file's own docstring intent (config-loading checks against the
  repo's real agent files); no action required, but an implementer hitting
  this failure mode later should know it's expected coupling, not a bug in
  either file.

  **CI-wiring mechanism [engineer-confirmed]:** do not add `evals/` as a
  bare pytest collection root — confirmed empirically that doing so also
  sweeps in `evals/fixtures/temp-project/tests/test_calculator.py`, which
  fails to import (`ModuleNotFoundError: No module named 'calculator'`,
  since its sibling `calculator.py` isn't on any configured `pythonpath`)
  and aborts collection entirely (`pytest ... --collect-only` against the
  real `pyproject.toml` returns `Interrupted: 1 error during collection`,
  zero tests run) — breaking CI for every subsequent PR, since
  `tests.yml`'s test step has no `|| true`. A round-4 review found the
  plan's original two mitigations were presented as alternatives when they
  are not: collecting the file explicitly and guarding against the
  `evals/fixtures` collection error are two separate, both-required steps.
  Do both: **(1)** add `evals/test_measure_subagent_model_resolution.py`
  as an explicit positional path in `tests.yml`'s parallel-pass `pytest`
  invocation (line 160, the `-m "not timing"` pass — this file has no
  `timing`-marked tests, so adding it to the serial timing-pass line 166
  too is harmless but not required) — without this, no `pyproject.toml`
  change alone wires anything into CI, since neither `norecursedirs` nor
  `--ignore` adds a collection root by itself. **(2)** guard the
  `evals/fixtures` collection-error path either via `evals/fixtures` in
  `pyproject.toml`'s `norecursedirs` or via `--ignore=evals/fixtures` on
  the same `tests.yml` line — required only if step (1)'s explicit path
  doesn't already exclude `fixtures/` implicitly (confirm at
  implementation time; both forms were verified this session to fix the
  bare-root case, but (1) uses an explicit file path, not the bare root,
  so re-verify whether (2) is still needed once (1) is in place). Also
  update `evals/README.md:23-25`, which currently states the harness's
  never-CI posture "applies equally to `measure_subagent_model_resolution.py`"
  — add a one-line disambiguation once D1 ships: the live harness (real
  `claude -p` subprocess, real subscription auth) still never runs in CI,
  but this test file's own unit tests now do, fully mocked. Add this D1
  wiring to this phase's own Verification checklist explicitly (not just
  implied by "run every pytest command above").
- **D7**: zero eval coverage exists for any plugin-scoped skill despite
  explicit harness support — `evals/run_skill_evals.py:107-110,252-254`
  globs `plugins/*/skills/*/evals/*-cases.json`, but no plugin skill has
  a case file, versus 4 covered skills under `claude/.claude/skills/`. Add
  at least one `*-cases.json` file for the highest-risk uncovered plugin
  skill — `lovable-cloud-migration-sync`, which performs `git rm`
  deletions of migration files (per the report's own risk callout on this
  finding) — mirroring an existing covered skill's case-file shape. Full
  coverage of all uncovered plugin skills is not required by this phase;
  closing the zero-coverage state for the one skill with real write/delete
  blast radius is. This edit adds a file under
  `plugins/lovable-cloud/skills/lovable-cloud-migration-sync/evals/` —
  bump `plugins/lovable-cloud/.claude-plugin/plugin.json`'s version
  (patch, for a test-only addition) in the same commit or
  `require-plugin-version-bump.sh` denies it.
- **Plugin version bumps required**: this phase's D7 edit touches
  `plugins/lovable-cloud/.claude-plugin/`-scoped files (see D7's own
  bullet above); no other phase-9 change touches a plugin directory.

**Rollback**: revert this phase's commit. The `"S"`-ruleset addition (S25a)
is the highest-risk revert candidate in this plan — if it produces
unexpected CI breakage after merge despite the `S101` per-file-ignore,
revert this commit rather than patching forward with ad hoc per-file
noqas.

### Phase 10 — Low-severity grab-bag (S22, S23, C5, C8, C10, C18, C20, C21, C25, C26, D11, D13, D14, D15, SC2, SC3)

- **S22**: `parse-git-command.py:335` — no stdin cap. Reuse:
  `parse-manifest-dependencies.py:76` (`_MAX_STDIN_BYTES` = 2MB) +
  enforcement at `:466-468` (read capped+1 bytes, check length,
  error+exit). Port the identical shape. Add a unit test asserting
  `parse-git-command.py` errors past the 2MB cap, mirroring
  `parse-manifest-dependencies.py`'s own cap-enforcement test.
- **S23**: `require-plugin-version-bump.sh:245,251` — bare `jq -r`; `_lib_jq`
  is already available in the same file (`:52`). Sibling
  `require-npm-version-bump.sh:355,367,373` already uses `_lib_jq`. Swap to
  `_lib_jq -r`.
- **C5**: `deny-escaped-backticks-in-pr-body.sh:54` — fixed-adjacency
  `gh pr` regex. Reuse: `deny-private-project-refs.sh:215-236`
  `fragment_gh_gated_surface` (correct word-walking pattern, handles
  hoisted flags). Extract as a shared helper and use it here.
- **C8**: `test_hook_alignment.py:316-333` — docstring says informational
  hooks "never deny," doesn't mention the `PreToolUse`+`ask` shape used by
  `ask-review-permissions.sh:2,27` and `ask-new-dependency-disclosure.sh:2,164`.
  Doc-precision fix to the docstring only.
- **C10 + shared-helper extraction**: `marker.sh:361` — bare `kill -0`.
  `_lib.sh:850-876` `_lib_resolve_claude_pid` takes no PID argument (walks
  its own ancestor chain) — not directly reusable. Extract the
  start-time-comparison block (`_lib.sh:857-868`) into a new
  `_lib_pid_alive_with_recorded_start PID` helper, callable from both
  `_lib_resolve_claude_pid` and `marker.sh`'s `clear-stale:361`. No format
  change needed to markers themselves — the marker write path
  (`marker.sh:63-72`) already only ever writes a bare PID, and every stored
  PID has a `$CONFIG_DIR/sessions/<pid>` entry to check against. Add a
  boundary-condition regression test for the new helper directly (live PID,
  dead PID, and a recycled PID whose recorded start time does not match),
  independent of any caller — matching the dedicated-test instruction
  Phase 1's C7 and Phase 4's S17 both carry for structurally identical
  extractions. A PID-liveness bug that misjudges a live session as dead
  silently clears a live gate marker, and no caller's existing suite
  asserts that boundary. Third
  migration target: `nudge-handoff-near-context-cap.sh:348-388`
  independently implements the identical idiom (same
  `$CONFIG_DIR/sessions/<pid>` 2-line format, same `TZ=UTC LC_ALL=C ps -o
  lstart=` comparison) — [verified: this session's specialist review].
  Migrate it to the new helper too, so this plan's SC1/S14
  idiom-unification thesis (Phase 1) doesn't leave a third copy of the
  exact pattern it exists to close standing in a different phase. The two
  sites' invocation shape differs, though, and the difference is
  documented as load-bearing at one of them: `_lib.sh:857` runs
  `TZ=UTC LC_ALL=C _lib_capped ps ...` (bare prefix) while
  `nudge-handoff-near-context-cap.sh:382` runs
  `_lib_capped env TZ=UTC LC_ALL=C ps ...`, with a comment stating the
  `env` indirection exists because `timeout` execs a leading `VAR=val` as
  the program name and exits 127 [verified: this session's specialist
  review]. Before standardizing all 3 callers on one form, confirm
  empirically — run both, compare output and exit status — rather than by
  inspection; adopt the `env` form if they diverge.
- **C18**: `plugins/lovable-cloud/skills/lovable-cloud-migration-sync/SKILL.md:3-8`
  still has TRIGGER prose plus `disable-model-invocation: true`. Only 2
  skills repo-wide carry that flag — [verified: this session's specialist
  review] — `pr-description-claude-config/SKILL.md` and this one. The fix
  stands on its own logic regardless of the count:
  `pr-description-claude-config/SKILL.md`
  correctly omits TRIGGER prose given `disable-model-invocation: true`
  makes auto-dispatch routing moot, and this skill should match. Strip the
  TRIGGER/DO NOT TRIGGER lines from its description.
- **C20**: `_config_dir.py:126` — one generic "unreadable" message covers 3
  distinct failure branches (`:116-119` not-absolute, `:121-124`
  not-a-directory/`is_valid`). Thread a reason string through. Add one
  test per failure branch asserting its distinguishing reason string
  appears in the error.
- **C21**: `update-claude-config-plugins.sh:190-195` — the Python snippet
  silently drops non-numeric version segments, and runs via bare `python3`
  (not `.venv/bin/python3`) as a standalone ops script any contributor
  invokes with their own interpreter. `packaging` is not importable under
  bare `python3` and is not declared in any requirements file — [verified:
  this session's specialist review, reproduced `ModuleNotFoundError`] — so
  for most real invocations the fallback path is the only path actually
  taken, not an edge case. Guard the `packaging` use as optional-import
  only (`try: import packaging.version except ImportError:` — do not add
  `packaging` to any requirements file; that would trigger CLAUDE.md's
  new-third-party-dependency disclosure rule for a dependency this fix
  doesn't need to declare). On `ImportError`, warn and skip the
  non-numeric segment rather than silently truncating it — this yields a
  visible warning, not necessarily a *correct* version comparison, on the
  path most invocations will actually take; state that limitation in the
  warning text itself. Add a test with a non-numeric version segment
  asserting a warning (not silent truncation) under both the
  `packaging`-available and `ImportError` paths — force each branch
  deterministically rather than relying on whichever `python3` the test
  machine's ambient `PATH` happens to resolve: the existing harness
  (`test_update_claude_config_plugins.py:141-143`'s `_run_script` only
  prepends a fake `claude` shim dir to `PATH`, leaving the rest of
  `PATH` — and hence `packaging`'s importability — at the ambient system
  value [verified: this session, read directly]). Force the `ImportError`
  branch with a `sys.path`-excluding shim or a `sys.modules['packaging']
  = None` mock, and force the `packaging`-available branch by skipping
  the test (with a clear reason) when `packaging` isn't actually
  importable in the test environment, rather than asserting on an
  unforced ambient state either way.
- **C25** (optional — candidate to drop from this phase): `code-writer.md`
  (order: name, description, tools, model, effort) and `Explore.md` (order:
  name, description, model, effort, tools) both differ from all 10
  `CANARY_AGENTS`' frontmatter order (model, effort, name, description,
  tools). Confirmed — [verified: this session's specialist review,
  `test_agent_roster.py` read in full] — no test asserts frontmatter key
  order, and YAML frontmatter parsing (`yaml.safe_load`) is inherently
  order-blind, so this is pure cosmetics with zero behavioral effect. Per
  CLAUDE.md's Axis 4 (minimal, targeted changes) and Axis 1 bucket 1
  (revert cosmetic-only edits by default), the engineer may prefer to drop
  this finding from the phase entirely, or land it as a one-line
  PR-description mention rather than a diff, rather than reorder two
  files' frontmatter for no functional gain. If landed, reorder both to
  match.
- **C26**: `tests.yml:57-58` step "Detect hook-relevant changes" /
  `id: detect` now gates the full pytest+ruff pass, not just hooks. Rename
  the step/id and update the `SKIP_REGEX` comment (`:89`) to match its
  actual scope. Every `steps.detect.outputs.*` reference in the same file
  must be updated to the new id in the same commit — 9 lines as of this
  review (`tests.yml:129,136,141,149,155,159,165,169,173`; re-grep
  `steps.detect` at dispatch time since this drifts) — an incomplete
  rename silently breaks every downstream job's `if:` condition rather
  than failing loudly.
- **D11**: no test exists for C5's flag-hoisted-form gap; companion pattern
  `test_gh_pr_flag_before_subcommand_denied` exists at
  `test_deny_private_project_refs.py:2915`. Add the analogous test once
  C5's fix lands (sequence after C5 within this phase).
- **D13**: `test_enforce_marker_script_shape.py:1570-1618`
  (`TestGateReleaseAuthorityBashArmConfigDirShapeSurvivesBudgetExhaustion`)
  — both tests set a `CLAUDE_CONFIG_DIR` override; none tests the plain
  `$HOME`-relative shape. Add a sibling test with the default `~/.claude`
  layout.
- **D14**: `consume-migration-token.sh:4-5` — an uncited
  `PostToolUse`-success assumption. Plan
  `.claude/plans/lovable-cloud-utc-migration-enforcement.md:25-27,79`
  required `verify-sources` confirmation before shipping, never recorded.
  An equivalent citation already exists, unconnected, at
  `.claude/plans/warn-read-consumes-handoff.md:58` ([verified:
  code.claude.com/docs/en/hooks — "PostToolUse | After a tool call
  succeeds"]). Add a citation comment to the hook header pointing at the
  same source (re-running `verify-sources` is unnecessary — the citation
  already exists in-repo and just needs connecting).
- **D15**: `test_require_stow_reminder.py` — every `--title` usage supplies
  a fixed literal; none places the marker string inside `--title`. Add one
  test with the marker in `--title`, no `--body` marker, asserting allow.
- **SC2 + shared-helper extraction**: 4 near-duplicate 30-day eviction
  sweeps: `nudge-error-mode-analysis.sh:151,176`,
  `nudge-handoff-near-context-cap.sh:549`, `nudge-worktree-anchor.sh:167`,
  `advance-past-commit-stall.sh:204`. These are **not** behaviorally
  identical, and the extraction must not flatten the difference
  [verified: this session's specialist review]: the first two run
  `find "$DIR" -maxdepth 1 -mtime +30 -delete` with no `-type f` and no
  directory guard, while the last two add `-type f` *and* a preceding
  `[ -d "$DIR" ] && [ ! -L "$DIR" ]` symlink guard. A single
  `_lib_evict_stale_state_files DIR [-type f]` signature would either drop
  the symlink guard from the two sites that have it — reopening a
  symlink-follow foot-gun in a `-delete` sweep — or silently narrow the
  other two sites to files-only.

  Give the helper the type restriction and the symlink/directory guard as
  two independent explicit parameters, and state per call site which
  behavior it keeps. Where a site gains a protection it lacked (the symlink
  guard for the first two), call that out in the PR description as
  deliberate hardening rather than letting it ride as a refactor.
  `review-ledger.sh:_sweep_stale_ledger_files:73-92` implements a fifth,
  related sweep (same `-mtime +30 -delete` shape, per its own comment) but
  is deliberately excluded from this extraction — [verified: this
  session's specialist review] it additionally supports dry-run mode and
  per-file reporting, and matches multiple `-name` extensions rather than
  deleting unconditionally, so forcing it into
  `_lib_evict_stale_state_files DIR [-type f]`'s narrower signature would
  either lose that behavior or bloat the shared helper for one caller —
  see Out of Scope. Add a boundary-condition regression test for
  `_lib_evict_stale_state_files` directly (a 29-day-old file survives, a
  31-day-old file is deleted, a symlinked `DIR` is refused under the
  guard), independent of any caller — same rationale as C10's above: an
  off-by-one at the 30-day boundary propagates to all 4 callers with
  nothing but incidental per-caller assertions behind it.
- **SC3**: `cleanup-merged-branches.sh:767` — `git fetch --prune` runs
  inside the per-branch loop (`:705-793`). Hoist a single fetch before the
  loop; adjust auto-pruned-detection to use the one batch fetch's output.
- **Plugin version bumps required**: this phase's S23 edit touches
  `plugins/plugin-semver/hooks/require-plugin-version-bump.sh`, and its
  C18 and D14 edits both touch files under `plugins/lovable-cloud/`
  (`.../skills/lovable-cloud-migration-sync/SKILL.md` and
  `hooks/consume-migration-token.sh` respectively). Bump
  `plugins/plugin-semver/.claude-plugin/plugin.json` (patch, for S23's
  `_lib_jq` swap) and `plugins/lovable-cloud/.claude-plugin/plugin.json`
  (patch, for C18+D14 together) in the same commits —
  `require-plugin-version-bump.sh` hook-denies otherwise.

**Rollback**: revert this phase's commit — self-contained; no other
phase consumes this phase's new `_lib_pid_alive_with_recorded_start`
helper or `_lib_evict_stale_state_files` helper.

## Verification

Each phase is independently testable — no phase's tests depend on a later
phase's diff landing first.

- **Phase 1**: `../../../.venv/bin/pytest claude/.claude/hooks/tests/` (all
  hook tests, plus the new/extended D2/D3/D8/D9/D10/D12 tests) and
  `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`
  for every touched hook. Confirm each of the 4 touched plugins'
  `.claude-plugin/plugin.json` version was bumped (see this phase's
  "Plugin version bumps required" bullet) before committing —
  `require-plugin-version-bump.sh` hook-denies the commit otherwise.
- **Phase 2**: targeted test for `require-ready-for-review.sh`'s
  `_lib_capped`-wrapped `gh pr view` call (timeout-path fixture, mirroring
  Phase 1's D9-style pattern) + `../../../.venv/bin/shellcheck`.
- **Phases 3–5**: `../../../.venv/bin/pytest
  claude/.claude/scripts/tests/test_transcript_analysis.py` plus each new
  `--redact`-default fixture and the new `TestSubagentMixDollars`
  requestId-sharing fixture; `../../../.venv/bin/ruff check
  claude/.claude/`.
- **Phase 6**: `/skill-review` on every touched `SKILL.md` (hook-enforced
  via `require-skill-review.sh`), plus a manual re-read confirming both
  `review-permissions/SKILL.md` and
  `plugins/skill-management/skills/skill-review/SKILL.md` still pass
  `check-skill-length.sh` under their new 500-line exception, and that
  `plugins/skill-management/.claude-plugin/plugin.json`'s version was
  bumped. If `review-permissions` later gains a `*-cases.json` eval file,
  extend it with a case for this phase's new trigger conditions
  (`permissions.deny`/`defaultMode`/`skillOverrides`) — not required for
  this phase to land, since no case file exists yet.
- **Phases 7–8**: no test suite covers prose accuracy — verify each fixed
  claim against the cited source file/line directly (re-run the same `wc
  -l`/`grep`/`sed` commands this plan's citations came from) before
  committing.
- **Phase 9**: `.github/workflows/tests.yml` itself (push the branch and
  confirm the two-directory pip-ecosystem Dependabot config and the new
  `ruff --select S` pass — S101 excepted via `per-file-ignores`,
  `S603/S607/S108/S105/S311` excepted via the repo-wide `ignore =` entry,
  and `S103` left selected repo-wide with only its non-`transcript-analysis.py`
  site(s) excepted via `per-file-ignores` (see S25's `S103` mechanism
  note) — produce no unexpected CI failure); `../../../.venv/bin/ruff check
  claude/.claude/ plugins/`; the scratch `os.chmod(tmp, 0o777)` check
  confirming `S103` still fires in `transcript-analysis.py` post-config
  (see S25); confirm `evals/test_measure_subagent_model_resolution.py` is
  now collected by both of `tests.yml`'s pytest passes per D1's mechanism
  above, and that `evals/README.md`'s never-CI disambiguation was added;
  confirm `plugins/lovable-cloud/.claude-plugin/plugin.json`'s version was
  bumped for D7's new case file.
- **Phase 10**: `../../../.venv/bin/pytest claude/.claude/ plugins/` (full
  suite across both collection roots — this phase edits files under
  `plugins/`: S23 → `plugins/plugin-semver/hooks/require-plugin-version-bump.sh`,
  D14 → `plugins/lovable-cloud/hooks/consume-migration-token.sh`, C18 → a
  `plugins/lovable-cloud/skills/...SKILL.md`; CI's own two pytest passes
  always run both roots together, and `plugins/lovable-cloud/tests/` is a
  separate, real collection root the narrower `claude/.claude/`-only
  command would miss) plus `../../../.venv/bin/shellcheck` for the
  shell-file changes (C5, S23, SC2, SC3); confirm
  `plugins/plugin-semver/.claude-plugin/plugin.json` and
  `plugins/lovable-cloud/.claude-plugin/plugin.json` versions were
  bumped; push the branch and confirm CI's own two pytest passes and the
  workflow-level changes actually run clean, matching Phase 9's own
  explicit CI-exercise pattern above.
- **Closing step, after all 10 phases have merged**: one full
  `../../../.venv/bin/pytest claude/.claude/ plugins/` and `../../../.venv/bin/ruff
  check claude/.claude/ plugins/` run against `HEAD`, in addition to each
  phase's own per-phase verification above. Phases 3, 4, and 5 land as three
  sequential independent commits against the same `transcript-analysis.py`,
  each dispatched from a prompt whose citations are re-resolved fresh (see
  each phase's citation-drift note) rather than trusted from this plan
  verbatim — the closing full-suite run is what catches any residual
  cross-phase interaction (e.g., Phase 4's `_local_git_env()` extraction
  not actually being reused by code Phase 5 adds nearby) that per-phase
  verification, scoped to each phase's own diff, cannot see.

**Run every `pytest` command above as the two passes CI uses**, not as one
invocation: `-m "not timing"` under the default `-n auto`, then
`-m timing -n0` serially. `pyproject.toml`'s own marker documentation
states timing-marked tests must run serially to avoid parallel-load
flakiness, and 21 such tests span 10 files including
`claude/.claude/hooks/tests/test_deny_pii_in_commits.py`, `test_lib.py`
(Phase 1's own targets) and `test_transcript_analysis.py` (Phases 3-5)
[verified: this session's specialist review]. A single unsplit local run
can pass where CI fails, or fail on a wall-clock assertion CI would not
reproduce.

Every phase also runs `/code-review` before its commit (repo-wide gate,
hook-enforced via `require-code-review.sh`), which is also what dispatches
`/skill-review` and `/agent-review` per
`.claude/rules/review-pipeline-dispatch.md`. Two phases stage files that
make `/skill-review` a hard commit gate rather than a dispatcher courtesy —
`require-skill-review.sh` denies any commit staging
`claude/.claude/skills/**/SKILL.md` or `plugins/*/skills/**/SKILL.md`:
Phase 3 (S15 edits `transcript-analysis/SKILL.md` and
`transcript-narrative/SKILL.md`) and Phase 10 (C18 edits
`plugins/lovable-cloud/skills/lovable-cloud-migration-sync/SKILL.md`).
Treat it as a blocker in those two phases the way plugin version bumps are
treated elsewhere in this plan, not as something `/code-review` will
happen to cover. Phase 7's C14 and Phase 10's C25 edit agent files
(`staff-data-engineer.md`, `staff-analytics-engineer.md`, `code-writer.md`,
`Explore.md`), where `/agent-review` is dispatcher-invoked and not
hook-enforced.

## Out of scope

- Re-triaging any finding's severity (High/Medium/Low/Very Low/N/A). This
  is reachable — `findings.md` is a repo file this plan's implementer
  could edit — but it is declined: severity triage is `root-cause-analysis`
  discipline. Re-running it here would mean re-litigating a completed
  audit's judgment calls inside a remediation plan whose own job is
  sequencing fixes, not re-scoring them; a genuine severity dispute
  belongs in a fresh audit pass, not a silent edit inside this plan.
- The 7 N/A findings from the source report — `S26`, `S27`, `S28`, `S29`,
  `S30`, `I3`, `SC8`, each tagged "### N/A — reviewed and confirmed sound"
  in `findings.md` — are not remediated (by definition: each was reviewed
  and found to need no code change). [verified: this session, `awk`-based
  section-header mapping against `findings.md`. `S24` (Low) and
  `D5`/`D6`/`D7` (Medium) are *not* N/A-tagged despite reading that way at
  a glance — they are actionable and are placed in Phases 6-9 above.]
- `C13`'s aside about a `python3` version-floor mismatch between
  `install.sh` (3.11) and another script's stated 3.10+ — the finding
  itself only requires fixing `docs/scripts.md`'s wrong claim about
  `install.sh`; reconciling the floor mismatch elsewhere is a separate,
  unscoped question the exploration surfaced but did not confirm needs a
  fix.
- Extending Phase 3's `S5` message-text redaction beyond `cmd_user_input`'s
  existing label/session-ID-only scope — flagged as a documented limitation
  in the reused pattern, not something this plan's S5 fix newly resolves.
  Given the declared surface (contributor's own machine/CI, not a hosted
  service), the narrower scope is acceptable, but Phase 3's `--redact`
  help text and `docs/transcript-analysis.md` must state the limitation
  explicitly (not leave it only in the `p_user_input` docstring), with a
  test asserting raw message text still appears under `--redact` so the
  limitation is pinned as intended rather than free to drift either way.
- Phase 9's S25 ruff-`S` triage for the ~100 non-test files carrying
  `S603`/`S607`/`S108`/`S105`/`S311` findings (10,547 total findings minus
  the `S101` test-file class this plan's Phase 9 does silence; `S103` is
  fully handled within Phase 9, not deferred here) — see Phase 9's
  rescoped S25 bullet. This needs its own scoping pass (a named triage
  owner, a per-finding fix-vs-suppress decision) before it can land as a
  PR-sized phase; it is not planned here.
- `review-ledger.sh`'s `_sweep_stale_ledger_files` (Phase 10's SC2) —
  deliberately left out of the shared `_lib_evict_stale_state_files`
  extraction; see Phase 10's SC2 bullet for why forcing it in isn't a
  clean fit.
