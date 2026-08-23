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
  [engineer-verified]
- G3 — All 10 phases are planned now; none is deferred to a backlog.
  [engineer-verified]

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
| A8 | All 85 report finding IDs map onto either an actionable phase or the true N/A set (7 IDs); none is silently dropped | [verified: this session, `awk`-based section-header extraction cross-checked against every phase's bulleted findings — caught and corrected a gap where `S24`/`D5`/`D6`/`D7` had been omitted from an earlier draft] |

**Flag to the engineer when presenting this plan:** A5 (the review-permissions
line-cap exception) and A6 (SC5's placement) are scope decisions this plan
made rather than ones already confirmed — call them out explicitly rather
than letting them pass as settled.

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
  `guard-settings-session-keys.sh`.

**Dispatch 1b — shared-trio rollout, S2 wrap, and tests (S13, S14, SC1, C6, C9, C19, D3, D8, D9, D10, D12):**
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
  instead of needing it re-added per file.
- **Content-bearing sites need exit-status-aware handling, not a bare wrap**
  (new — distinguishes these from the mechanical S2 wraps below): 3 of
  S2's sites read full diff *content*, not cheap metadata, and one has a
  **fail-open bug** if wrapped naively. `require-code-review.sh`'s empty-diff
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
  ':(top,exclude)...'` is a fourth content-bearing read with the same
  general shape (excluded from S2's mechanical list below for the same
  reason). For these three sites: after wrapping, check `$?` for `124`
  (the standard `timeout`(1)/`gtimeout`(1) exit code for "killed by cap")
  before treating an empty result as "nothing to review" — on a `124`,
  deny/error instead of silently passing. Add a regression test per site
  using a staged diff sized to exceed the cap.
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
  test pair per converted hook, prioritizing the 3 with zero existing test
  coverage — `require-npm-version-bump.sh`, `require-plugin-version-bump.sh`,
  `require-skill-review.sh` (no test file exists for any of the three under
  `plugins/` — [verified: this session's specialist review]).
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
- **C6**: `check-claude-md-length.sh:62-63`, `check-skill-length.sh:64-65` —
  `REPO_ROOT` is computed and tested for emptiness but never threaded into
  `git diff --cached`/`git show` (implicit cwd). Thread `-C "$REPO_ROOT"`.
- **C9**: `session-marker-dashboard.sh:94` — `REPO_ROOT` git call unguarded
  (contrast the already-`_lib_jq`-wrapped call at `:102`). Wrap with
  `_lib_capped`.
- **C19**: `consume-durable-continuity-file-on-read.sh:118-122` — inline
  timeout check has no `gtimeout` probe; replace with `_lib_capped`.
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
  this hook. D10 (below) adds the test that pins this same accepted gap —
  land S13's documentation and D10's test together so the gap is both
  named and pinned, not one without the other.
- **D10**: `test_deny_pii_in_commits.py` has zero alias/`git-ci`/`git-cm`
  tests; sibling pattern `test_reviewer_quoted_command_name_bypass_allowed`
  exists in `test_deny_reviewer_tree_mutation.py:648`. Add the analogous
  test.

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
currently invoking `audit-routing --redact` — see S15's own bullet for the
back-compat fix this requires.

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
  This is a breaking flag-shape change: two SKILL.md files document the
  current opt-in contract by name and must be updated in this same phase —
  `transcript-analysis/SKILL.md:41` (`audit-routing --since 35d --redact`
  example) and `:92-93` (explicitly contrasts `audit-routing`'s opt-in
  `--redact` against `cost`'s default-on behavior — that contrast disappears
  once this fix lands), and `transcript-narrative/SKILL.md:77,80`. Add a
  regression test asserting `audit-routing` with no flags now redacts by
  default, guarding the flip itself (not just the new opt-out path).
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
  helper; call from all 8 sites — collapses ~96 duplicated lines.
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
- **S17** (GIT_DIR stripping): `_git_remote_origin_host_and_owner_repo:6941-6963`
  (subprocess call `:6951-6955`, no `env=`) and
  `_local_git_object_exists_batch:7215-7241` (subprocess call `:7229-7233`,
  no `env=`) — both can pick up an ambient `GIT_DIR`/`GIT_WORK_TREE`. Reuse
  pattern: `_ledger_path_is_git_tracked:5897-` env-prep block `:5911-5919`
  (copies `os.environ`, strips `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE`,
  sets `LC_ALL=C`). Extract into a shared `_local_git_env()` helper (now
  needed in 3 places) and apply ahead of both subprocess calls.
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
  shape, at the cost of this one tradeoff.
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
  engineer at presentation, not previously confirmed.
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
  `limit_for()` grants `code-review/SKILL.md`, `plan-review/SKILL.md`,
  `plan-review/ROUTING.md` (and, after this phase, `review-permissions/SKILL.md`)
  a 500-line cap instead of 200/300.
- **C16**: `agent-review/SKILL.md` item 16 (`:152`, checks 2 classes:
  tool-verb + bias-anchor) is missing 2 classes that
  `skill-review/SKILL.md` item 12 (`:187-188`) already checks:
  vendor/product-name-anchoring-a-category, and borrowed-interface-shape-
  no-vendor-token. Port the 2 missing classes into `agent-review/SKILL.md`
  item 16, preserving its existing staff-* vendor-name carve-out.
- Run `/skill-review` on every `SKILL.md` touched in this phase per
  `.claude/rules/skill-and-agent-self-review.md` (hook-enforced via
  `require-skill-review.sh`).

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
- **C17 / I4** (same sentence, one fix resolves both): `evals/README.md:400`
  — "CI lints `claude/.claude/` only"; actual is `ruff check claude/.claude/
  plugins/` (`tests.yml:170`).
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

- **S11**: root `CLAUDE.md:108-114` (text `:111-112`) — "don't merge your
  own PRs" is scoped to literal `gh pr merge`;
  `block-gh-pr-merge.sh:21-23` documents a `gh api .../pulls/N/merge`
  bypass as an intentionally excluded case, never surfaced in `CLAUDE.md`.
  Add a sentence noting the gap.
- **S19**: `claude/.claude/rules/github-actions-workflows.md:2-4`
  (frontmatter `paths`), risk text `:35-37` — glob matches
  `workflows/*.yml` only, not composite actions (`action.yml`). No
  `action.yml` exists yet in the repo (confirmed via `find`). Add
  `**/.github/actions/*/action.yml` and `.yaml` to `paths`.
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
  sources inline instead of pointing here. Either rename the title or
  expand the file and redirect those inline citations to it.
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
  returns **10,547 findings**: 8,506 `S101` (99.8% confined to test files),
  1,034 `S603`, 848 `S607`, 127 `S108`, 24 `S105`, 5 `S103`, 3 `S311`,
  spanning ~100 distinct non-test files for the `S603`/`S607`/etc. classes
  alone]. This is not the "triage a few findings" scope the original
  report finding implies, and `tests.yml`'s Lint step (`ruff check
  claude/.claude/ plugins/`, no `|| true`) is a hard blocking gate on
  every PR after this one lands — landing the full `"S"` ruleset as
  written would either force ~2,000 non-test findings into one
  non-PR-sized diff or break CI for every subsequent PR. Split: **(a)**
  add `"S"` to `pyproject.toml:6`'s select list plus one
  `[tool.ruff.lint.per-file-ignores]` entry silencing `S101` on test-glob
  paths (closes 99.8% of the volume with no per-line judgment needed) —
  this lands in this phase. **(b)** the remaining ~100-file `S603`/`S607`/
  `S108`/`S105`/`S103`/`S311` triage (each needing a judgment call between
  a real fix and a `# noqa: S... — <rationale>` per CLAUDE.md's
  suppression-rationale rule, plus a named owner to adjudicate them) is
  **out of scope for this phase** — see Out of Scope.
- **I5** (Very Low, informational, no fix needed): `dependabot.yml:7` limit
  is 3; exactly 2 actions are pinned repo-wide. Note only.
- **D1**: `evals/test_measure_subagent_model_resolution.py` (876 lines) has
  zero CI collection (`tests.yml:160,166` roots are `claude/.claude/
  plugins/` only; `pyproject.toml:18` `pythonpath` is import-only, not a
  collection root). Add this file (or its deterministic subset) to a
  CI-collected path/invocation, without adopting the live-script's separate
  no-CI rationale from `.claude/plans/plan-mode-model-resolution-experiment.md`
  item M7 (that rationale covers a different, intentionally-uncollected
  script).
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
  blast radius is.

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
  PID has a `$CONFIG_DIR/sessions/<pid>` entry to check against. Third
  migration target: `nudge-handoff-near-context-cap.sh:348-388`
  independently implements the identical idiom (same
  `$CONFIG_DIR/sessions/<pid>` 2-line format, same `TZ=UTC LC_ALL=C ps -o
  lstart=` comparison) — [verified: this session's specialist review].
  Migrate it to the new helper too, so this plan's SC1/S14
  idiom-unification thesis (Phase 1) doesn't leave a third copy of the
  exact pattern it exists to close standing in a different phase.
- **C18**: `plugins/lovable-cloud/skills/lovable-cloud-migration-sync/SKILL.md:3-8`
  still has TRIGGER prose plus `disable-model-invocation: true`. Confirmed
  exactly 4 skills repo-wide carry that flag; the other 3
  (`pr-description-claude-config`, `plan-review-claude-config`,
  `code-review-claude-config`) correctly omit TRIGGER prose — this one is
  the outlier. Strip the TRIGGER/DO NOT TRIGGER lines from its description.
- **C20**: `_config_dir.py:126` — one generic "unreadable" message covers 3
  distinct failure branches (`:116-119` not-absolute, `:121-124`
  not-a-directory/`is_valid`). Thread a reason string through. Add one
  test per failure branch asserting its distinguishing reason string
  appears in the error.
- **C21**: `update-claude-config-plugins.sh:190-195` — the Python snippet
  silently drops non-numeric version segments. Use
  `packaging.version.Version` if available, or explicitly warn+skip on a
  non-numeric segment instead of silently truncating. Add a test with a
  non-numeric version segment asserting a warning (not silent truncation).
- **C25**: `code-writer.md` (order: name, description, tools, model,
  effort) and `Explore.md` (order: name, description, model, effort,
  tools) both differ from all 10 `CANARY_AGENTS`' frontmatter order (model,
  effort, name, description, tools). Reorder both to match.
- **C26**: `tests.yml:57-58` step "Detect hook-relevant changes" /
  `id: detect` now gates the full pytest+ruff pass, not just hooks. Rename
  the step/id and update the `SKIP_REGEX` comment (`:89`) to match its
  actual scope.
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
- **SC2 + shared-helper extraction**: 4 confirmed exact-duplicate 30-day
  eviction sweeps: `nudge-error-mode-analysis.sh:151,176`,
  `nudge-handoff-near-context-cap.sh:549`, `nudge-worktree-anchor.sh:167`,
  `advance-past-commit-stall.sh:204`. Extract a shared
  `_lib_evict_stale_state_files DIR [-type f]` helper in `_lib.sh`.
  `review-ledger.sh:_sweep_stale_ledger_files:73-92` implements a fifth,
  related sweep (same `-mtime +30 -delete` shape, per its own comment) but
  is deliberately excluded from this extraction — [verified: this
  session's specialist review] it additionally supports dry-run mode and
  per-file reporting, and matches multiple `-name` extensions rather than
  deleting unconditionally, so forcing it into
  `_lib_evict_stale_state_files DIR [-type f]`'s narrower signature would
  either lose that behavior or bloat the shared helper for one caller —
  see Out of Scope.
- **SC3**: `cleanup-merged-branches.sh:767` — `git fetch --prune` runs
  inside the per-branch loop (`:705-793`). Hoist a single fetch before the
  loop; adjust auto-pruned-detection to use the one batch fetch's output.

**Rollback**: revert this phase's commit — self-contained; no other
phase consumes this phase's new `_lib_pid_alive_with_recorded_start`
helper or `_lib_evict_stale_state_files` helper.

## Verification

Each phase is independently testable — no phase's tests depend on a later
phase's diff landing first.

- **Phase 1**: `../../../.venv/bin/pytest claude/.claude/hooks/tests/` (all
  hook tests, plus the new/extended D2/D3/D8/D9/D10/D12 tests) and
  `../../../.venv/bin/shellcheck` via `scripts/list-shell-files.sh | xargs
  -0 .venv/bin/shellcheck` for every touched hook.
- **Phase 2**: targeted test for `require-ready-for-review.sh`'s
  `_lib_capped`-wrapped `gh pr view` call (timeout-path fixture, mirroring
  Phase 1's D9-style pattern) + `shellcheck`.
- **Phases 3–5**: `../../../.venv/bin/pytest
  claude/.claude/scripts/tests/test_transcript_analysis.py` plus each new
  `--redact`-default fixture and the new `TestSubagentMixDollars`
  requestId-sharing fixture; `../../../.venv/bin/ruff check
  claude/.claude/`.
- **Phase 6**: `/skill-review` on every touched `SKILL.md` (hook-enforced
  via `require-skill-review.sh`), plus a manual re-read confirming
  `review-permissions/SKILL.md` still passes `check-skill-length.sh` under
  its new 500-line exception.
- **Phases 7–8**: no test suite covers prose accuracy — verify each fixed
  claim against the cited source file/line directly (re-run the same `wc
  -l`/`grep`/`sed` commands this plan's citations came from) before
  committing.
- **Phase 9**: `.github/workflows/tests.yml` itself (push the branch and
  confirm the two-directory pip-ecosystem Dependabot config and the new
  `ruff --select S` pass — S101 excepted via `per-file-ignores` — produce
  no unexpected CI failure); `../../../.venv/bin/ruff check
  claude/.claude/ plugins/`.
- **Phase 10**: `../../../.venv/bin/pytest claude/.claude/ plugins/` (full
  suite across both collection roots — this phase edits files under
  `plugins/`: S23 → `plugins/plugin-semver/hooks/require-plugin-version-bump.sh`,
  D14 → `plugins/lovable-cloud/hooks/consume-migration-token.sh`, C18 → a
  `plugins/lovable-cloud/skills/...SKILL.md`; CI's own two pytest passes
  always run both roots together, and `plugins/lovable-cloud/tests/` is a
  separate, real collection root the narrower `claude/.claude/`-only
  command would miss) plus `shellcheck` for the shell-file changes (C5,
  S23, SC2, SC3).
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

Every phase also runs `/code-review` before its commit (repo-wide gate,
hook-enforced via `require-code-review.sh`) and, since this repo carries
`.claude/worktree-required`, each phase's `code-writer` dispatch operates
inside this plan's own worktree (`.claude/worktrees/discovery-audit-remediation-plan`)
— no `isolation: worktree` per-phase, since these are PR-bound implementation
dispatches per `CLAUDE.md`'s Agent Briefing.

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
  section-header mapping against `findings.md`; this same check caught
  that `S24` (Low) and `D5`/`D6`/`D7` (Medium) were *not* N/A-tagged and
  had been dropped from an earlier draft of this plan's bucketing — they
  are folded into Phases 6-9 above.]
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
  `S603`/`S607`/`S108`/`S105`/`S103`/`S311` findings (10,547 total findings
  minus the `S101` test-file class this plan's Phase 9 does silence) — see
  Phase 9's rescoped S25 bullet. This needs its own scoping pass (a named
  triage owner, a per-finding fix-vs-suppress decision) before it can land
  as a PR-sized phase; it is not planned here.
- `review-ledger.sh`'s `_sweep_stale_ledger_files` (Phase 10's SC2) —
  deliberately left out of the shared `_lib_evict_stale_state_files`
  extraction; see Phase 10's SC2 bullet for why forcing it in isn't a
  clean fit.
