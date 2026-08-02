# Plan: per-record branch/model attribution in transcript-analysis (GH-482)

## Context

**Goal:** make `review-trace` and `judgment-pair` resolve branch (and, for
`review-trace`, model) per record instead of attributing a whole session to its
first main-thread record, and close the two adjacent scoping gaps issue #482
files alongside it — `buckets`' invisible cross-project pooling, and
`--projects`' inability to scope by repo identity.

`review-trace` is the subcommand `/error-mode-analysis` Step 2 leans on most
heavily. It derives one `session_branch` and one `session_model` from the first
main-thread record and applies both to every event in the session, and it uses
that single branch as the `--branches` filter key — so a session that starts on
`main` and moves to a feature branch has its feature-branch events labelled
`main` in the header and dropped entirely by `--branches <feature-branch>`.
`judgment-pair` shares the branch half of the mechanism. That shape is not an
edge case: it is what the ordinary "open a session, then create a branch"
workflow produces every time.

Intended outcome is a merged PR where every subcommand resolves branch
per-record, a `review-trace` line is attributable on its own, `buckets` shows
when a row pools several projects, and any `--projects` subcommand can be scoped
to this repo by identity rather than by a path-prefix glob.

**Evidence base.** All figures below were re-derived this session against this
machine's transcript corpus (31 project directories under `~/.claude/projects/`),
driving the module's own predicates — `REVIEW_TRACE_SKILLS`, `hook_denial_key`,
`_REVIEWER_PREFIX`/`_REVIEWER_EXACT`, `_fam` — imported from
`transcript-analysis.py` rather than reimplemented, so the event set counted is
the one the subcommand actually emits.

## Approach

Resolve branch and model from the record that produced each event, then let
`--branches` filter events rather than whole sessions. This is not a new
mechanism — it is what the other subcommands already do (`buckets`, `fail-seq`,
`struggle`, `duration`, `subagents`, `skill-invocation`, … all read
`rec.get("gitBranch")` inside their per-record loop). The two outliers are being
brought onto the existing pattern, not given a new one.

Output shape for `review-trace`: keep one block per session so chronology stays
intact, widen the header to the distinct sets observed across emitted events,
and suffix each event line with its own `(branch=… model=…)`. The suffix goes
*after* the payload so the existing line prefix (`  [`) and kind token stay in
place. An unresolvable branch or model renders as `?`, reusing the sentinel this
same function already uses for a missing timestamp.

For repo scoping, `--this-repo` reuses `_repo_scoped_project_slugs()` — which
matches project directories by **exact identity** derived from
`git worktree list`. That exactness is the whole point of the control and is
kept, which means it does **not** cover a session started in a repo
*subdirectory* (row 13a). The existing prefix glob stays documented as the
fallback for that case rather than being deleted.

### Assumption ledger

**Root problem:** two subcommands attribute an entire session to one record's
branch, so `review-trace`'s header lies about which branch its events belong to
and `--branches` silently drops the majority of them — degrading the analysis
most likely to surface the defect.

| # | Assumption | Tag |
|---|---|---|
| 1 | `review-trace` and `judgment-pair` are the only subcommands that attribute a session to its **first** record's branch. Other subcommands either resolve per-record or gate at session level on the full record set (`cmd_fail_seq` line 481), but none propagate one record's branch across a session. | `[verified: grep of every `gitBranch` read in transcript-analysis.py; corroborates row 1 of the #472 triage ledger, whose "the other 15 resolve per-record" wording overstated — `audit-routing`, `handoff-ratio`, `audit-routing-shape`, and `audit-routing-samples` read no `gitBranch` at all]` |
| 2 | `review-trace` also derives session *model* from the first main-thread assistant record; no other subcommand does. | `[verified: the `session_model` loop at transcript-analysis.py:840-846 is the sole first-record model derivation in the file]` |
| 3 | Mid-session branch transitions dominate the review-event population. | `[verified: of 173 sessions carrying review-trace events, 70 (40.5%) span more than one branch across those events; 770 of 1445 events (53.3%) sit on a branch other than the session's first-record branch]` |
| 4 | The `--branches` filter, not just the header label, is what loses data. | `[verified: 53 sessions have *every* review-trace event on a non-first branch — those sessions return zero rows for `--branches <their-actual-branch>` today; worst single session hides all 36 of its events]` |
| 5 | Model misattribution is real but an order of magnitude rarer than branch. | `[verified: 25 of 173 sessions (14.5%) span more than one model family across their events]` |
| 6 | Every record type that can produce a `review-trace` event carries a non-empty `gitBranch`, so per-record resolution needs no new data source. | `[verified: across a 400-file sample, `assistant` (48374), `attachment` (10809), `user` (26574) records were non-empty-`gitBranch` in 100% of cases; the field is absent only on record types that produce no events — `mode`, `ai-title`, `worktree-state`, …]` |
| 7 | Carry-forward (last non-empty value wins) is the right fallback, not a defensive layer. | Row 6 makes the fallback all but unreachable for branch; it is *load-bearing* for model, because denial events arrive on `attachment`/`user` records carrying no `message.model` at all — the model in effect is by definition the last main-thread assistant model before the denied call. One mechanism serves both fields. `anchors: root` |
| 7a | Carry-forward must be visible when it produces nothing: an event whose branch or model resolves empty renders `?`, not an empty string. | An empty string produces a header reading `branches=,feat` and a suffix reading `(branch= …)`, which is both unparseable and a `--branches ''` matching hazard. `?` is the sentinel `cmd_review_trace` already uses for a missing timestamp (line 981). `anchors: row7` |
| 8 | `buckets` cannot show that a row pools several projects. | `[verified: cmd_buckets keys `branch_data` on the bare `gitBranch` string and prints no project field; the resolved `--projects` glob is never echoed]` |
| 9 | A project **count** column is the publish-safe choice; project **names** are not, and redacted labels do not help. | Project directory names embed private-project identifiers — the reason `audit-routing` ships `--redact`. Three options were weighed: raw names (leaks identifiers), redacted labels via the existing `_redact_proj_label` (emits `private-project-N`, which tells the reader a row is pooled but is no more actionable than a count, while requiring `--redact` to be plumbed into `buckets`), and a bare count. The count answers "is this row pooled?" at the lowest cost; `--this-repo` is the documented next action, named in the scope line. `anchors: row8` |
| 10 | `_repo_scoped_project_slugs()` already solves repo-identity scoping correctly and is reusable. | `[verified: it derives exact directory slugs from `git worktree list --porcelain`, matches them by string equality in `_iter_scoped_sessions`, and fails closed via `sys.exit(1)` at **three** sites — transcript-analysis.py:1203, :1214, :1228 — each hardcoding the `skill-invocation:` label]` |
| 11 | The shipped prefix-glob workaround is genuinely defeated on this machine's layout, not just in principle. | `[verified: this repo has 6 project dirs — the main-tree slug plus 5 worktree slugs — so an exact `--projects` under-covers by 5; and the prefix glob that fixes that would also match any sibling clone whose path extends the same prefix]` |
| 12 | `--this-repo` as an opt-in flag beats making repo scope the default when `--projects` is unset. | Two lighter primitives were checked against the source first: (a) the documented prefix glob — fails per row 11; (b) a hand-assembled comma list passed to `--projects` — works, but the caller must re-derive slugs that change as worktrees come and go. Making it the *default* is the heavier option: it silently reverses 15 subcommands' scope, and machine-wide survey is a documented use of `buckets` and `audit-routing`. `anchors: row11` |
| 12a | The resulting two-default asymmetry (`skill-invocation` defaults to repo scope; the other 15 default to machine-wide) is a real user-facing wart and is mitigated, not ignored. | Mitigations: every subcommand that gains `--this-repo` prints a one-line resolved-scope header, so no output is scope-ambiguous; and `skill-invocation` accepts `--this-repo` as an explicit no-op so the flag is uniform across all 16 `--projects` subcommands. `anchors: row12` |
| 13 | `--this-repo` scopes to *this checkout's* worktrees, not to every clone of the repo on the machine. | `[verified: `git worktree list` enumerates linked worktrees only; a second independent clone of this repo exists on this machine under a different parent and is correctly outside that set]` — documented caveat |
| 13a | `--this-repo` also does **not** cover a session started in a repo *subdirectory*, whose project dir is slugged from that subdirectory path and is string-unequal to every worktree-root slug. | `[verified: `error-mode-analysis/SKILL.md:17` states the trailing `*` exists partly to "pick up … sessions started in a repo subdirectory", so this is coverage the glob has and exact identity does not. Zero instances exist on this machine — all 31 project dirs are repo roots, parent dirs, or `--claude-worktrees-<branch>` paths — so the loss is latent, not observed]`. Prefix-with-separator matching would close it and is **rejected**: `<slug>-` matches a sibling clone at `<repo>-fork`, which is exactly the collision `_repo_scoped_project_slugs`' docstring exists to prevent. `anchors: row12` |
| 14 | A project directory left behind by a removed worktree is silently excluded from `--this-repo`. | `[verified: no orphan exists today — all 5 worktree project dirs match live entries in `git worktree list`]` — pre-existing behavior of the `skill-invocation` default, carried forward and documented, not newly introduced |
| 14a | Rows 13/13a/14 are all *silent under-coverage*, which is safe for disclosure but unsafe for analysis — an under-covered run reads identically to "no evidence exists". | The resolved-scope header (row 12a) is what converts them from silent to visible: under `--this-repo` it echoes the resolved slug count, so a reader can see the corpus was narrower than expected. `anchors: row13a` |
| 15 | `cmd_audit_routing` consumes its session iterator twice (redact-label pass at line 1934, then the counting pass at line 1956); no other call site double-consumes. | `[verified: each of the other 14 commands has exactly one loop over the session iterator; `cmd_fail_seq:481`'s set comprehension is over `records`, not the iterator]` — so the scope helper must be a plain function returning a **fresh** iterator per call, resolving slugs once and caching them |
| 16 | The `iter_sessions` docstring's claim that its flat-sort ordering is "load-bearing for cmd_audit_routing's redact-label first pass" is **stale**. | `[verified: `all_proj_labels.sort()` at transcript-analysis.py:1938 sorts the labels before the redact map is built, so first-seen order does not affect the mapping]` — routing `audit-routing` through the per-directory `_iter_scoped_sessions` under `--this-repo` is therefore safe, and the stale docstring is corrected in this PR |
| 17 | Scope covers all three items in issue #482. | `[engineer-verified]` |
| 18 | `review-trace` output renders as header sets plus a per-line `(branch=… model=…)` suffix. | `[engineer-verified]` — chosen over split-per-branch blocks (breaks chronology, and cannot express model variation within one branch) and header-sets-only (leaves an individual grepped line unattributable) |
| 19 | `review-trace` output is **not** publish-safe under the default machine-wide scope, and this change widens it. | It already prints the full project-dir path at line 975 and a denial `msg!r` at line 990; adding per-line branch strings (which carry ticket IDs and project names) increases what a pasted block discloses. Extending `--redact` to `review-trace` is explicitly **out of scope**; the mitigation is the SKILL.md statement that `--this-repo` is required before quoting the output. `anchors: row12a` |
| 20 | Changing the cwd guard from slug equality to path containment is not, on its own, posture-neutral. Containment alone accepts a cwd inside a *different* repo whose `git worktree list` resolves to this one's worktree paths — an exported `GIT_DIR` pointing at an outer/parent repo, or a nested checkout/submodule. Slug equality caught this; bare containment does not. | `[engineer-verified — surfaced by ciso-reviewer round 2]` — pairing containment with a `git rev-parse --show-toplevel` identity check (cwd's own repo root must be among the enumerated worktree paths) closes the gap: containment then governs *where under the root* the caller stands, not *which root* was resolved. `anchors: row10` |

## Critical files

### 1. `claude/.claude/scripts/transcript-analysis.py`

**`cmd_review_trace`** — replace the two first-record derivation loops with
carry-forward trackers inside the existing per-record walk. The ordering is
load-bearing and is specified exactly:

1. Per record, update `last_branch` from `rec.get("gitBranch")` and `last_model`
   from `(rec.get("message") or {}).get("model")` on main-thread assistant
   records — **before** the `--since`/`--until` filter, since the model in effect
   at time T is set by the last assistant turn before T regardless of the
   reporting window. This means branch carry-forward can also cross a date
   boundary; that is intended and gets a test.
2. Apply the date filter.
3. Detect events and dedup denials by `tool_use_id` exactly as today —
   `seen_denial_ids` must be populated over **all** events, before any branch
   filtering, or a duplicate-id denial on a differently-branched record gets
   emitted instead of suppressed.
4. Stamp `branch` and `model` (via `_fam`) onto each event at append time,
   falling back to `?` per row 7a.
5. **After** the loop, filter the event list by `branch_filter`.
6. `if not events: continue` — evaluated on the *filtered* list.
7. `has_denial` computed over the *filtered* list; `deny_only` stays a
   **session-level** gate over that list, not a per-event filter. Its current
   behavior (a qualifying session prints all its events, skills included) is
   unchanged.
8. Header becomes `branches=<sorted,distinct>  models=<sorted,distinct>` over the
   emitted events, keeping the existing `skills=/denials=/reviewer-spawns=`
   counts. Each event line gains a trailing `  (branch=… model=…)`.

**`cmd_judgment_pair`** — delete the first-record `session_branch` loop; test
`branch_filter` against the invocation record's own `gitBranch` at the point the
invocation is detected. Add `branch=` to the existing block header line
(`Skill: …  (line N)`), which today carries no branch at all.

**`cmd_buckets`** — track a `set` of `jsonl.parent.name` per branch (the loop
already receives the path; it is currently discarded as `_jsonl`). A **set**, not
a counter — the same project contributing two session files on one branch must
report `Proj == 1`, `Sess == 2`. Add a `Proj` column between `Branch` and `Sess`
holding `len(projects)`, and adjust the `"-" * 108` rule width.

**New scope-dispatch helper** — a plain function (**not** a generator; a
`yield`-based helper would defer `sys.exit(1)` to first `next()` instead of
firing at scope-resolution time) that resolves slugs **once** per invocation,
caches them, and returns a fresh iterator per call. It reads `args.this_repo`
**unguarded** — not `getattr(args, "this_repo", False)` — so a subparser that was
never wired through the argparse helper raises `AttributeError` at first use
rather than silently falling through to machine-wide scope. Every other scope
decision in this file fails closed; this one must too. It replaces the
`iter_sessions(PROJECTS_DIR, projects_glob)` call at each of the 15
non-`skill-invocation` sites, and `cmd_audit_routing` calls it twice per row 15.

It also returns a **resolved-scope label** (`this repo (N project dirs)` or the
literal glob) which each of the 15 subcommands prints as a one-line header, in
the shape `cmd_skill_invocation` already uses (`SKILL INVOCATION SOURCES (…)`).
This is row 12a's and row 14a's mitigation.

**`_repo_scoped_project_slugs`** — parameterize the subcommand label hardcoded as
`skill-invocation:` in its **three** fail-closed messages (lines 1203, 1214,
1228). Its cwd guard at line 1222 currently tests slug *equality* against
worktree roots, so invoking `--this-repo` from any repo subdirectory exits 1 with
a misleading message; change it to path *containment*
(`Path(os.getcwd()).resolve()` relative to a resolved worktree path), **paired
with** a repo-identity check — `git rev-parse --show-toplevel` from cwd must
itself be among the enumerated worktree paths (row 20). Containment alone is not
posture-neutral: it would accept a cwd resolving to a *different* repo whose
`git worktree list` output happens to contain this one's paths (an exported
`GIT_DIR`, a nested checkout, a submodule) — slug equality caught that case,
bare containment does not. The pairing keeps it fail-closed: containment governs
*where under the root* the caller stands, identity governs *which root* was
resolved.

Its docstring generalizes only the clause naming *which caller* asks for repo
scope. **Keep verbatim** the sentences stating that this is a minimization
control and that a silent fallback to `"*"` would reintroduce the cross-project
read it exists to prevent — restating that rationale as a preference is the
concrete path by which a future edit makes "warn and fall back to `*`" look
locally reasonable. Add row 13a's subdirectory exclusion to the docstring.

**`main()`** — extract `build_parser()` as a testable seam (today `main()` builds
the parser inline and ends with `parse_args()`/`parsed.func(parsed)`, so the
argparse layer cannot be tested without executing a subcommand against the real
`~/.claude/projects`). Add `_add_project_scope_args(parser)` creating a mutually
exclusive group of `--projects GLOB` and `--this-repo`, and call it from the 15
subparsers that currently repeat `add_argument("--projects", default="*", metavar="GLOB")`
verbatim. `skill-invocation` keeps its own `--projects` (`default=None`, custom
help) and its existing default-to-repo-scope behavior, but **also** accepts
`--this-repo` as an explicit no-op so the flag is uniform across all 16
`--projects` subcommands (row 12a) — added to its own mutually exclusive group,
not `_add_project_scope_args`'s, so `skill-invocation --this-repo --projects '*'`
exits 2 exactly like the other 15 rather than silently accepting both. Note the
dispatch helper must branch on `this_repo` **first**: `--projects` retains its
`"*"` default even when unset, so a truthiness check on the glob would silently
win.

**Reuse, not reimplementation:** `_repo_scoped_project_slugs`,
`_iter_scoped_sessions`, `_path_to_project_slug`, `_read_session_file`,
`iter_sessions`, `_fam`, `_projects_glob`, `_branch_filter`, `_derive_proj_label`
all exist and are called as-is. Genuinely new code: the scope-dispatch helper,
`_add_project_scope_args`, `build_parser()`, and the per-event stamping.

### 2. `claude/.claude/scripts/tests/test_transcript_analysis.py`

**The plan's earlier break-list was wrong in both directions.** A prototype of
these changes was run against the suite: **306/306 tests pass unchanged**. No
existing test asserts on the `branch=`/`model=` header;
`TestFrictionCountCrossPathEquality` matches `denials=(\d+)`, which survives;
`TestJudgmentPair.test_branches_filter` puts the invocation on the session's
first-record branch and so is structurally blind to the bug; `TestBuckets`'
`assert " 2 " in out` survives an inserted column by luck. **The entire
`review-trace` output contract is unasserted** — this change can ship inverted
and stay green. That is the gap the new tests must close, not a set of broken
assertions to repair.

**Fixture work first:**
- Extend `_hook_deny(hook_name, *, stringified=False)` with `branch` and `ts`
  parameters, matching its sibling `_hook_deny_current`, which already has both.
  Without this, every synthetic attachment-denial test exercises only the
  carry-forward path, so an implementation that ignores an attachment record's
  own `gitBranch` passes the whole suite while mislabelling real denials.
- Add `this_repo` to the ~23 namespace-building sites for subcommands gaining the
  flag: the factories `_review_trace_args`, `_judgment_pair_args`,
  `_skill_pair_args`, `_gate_args`, `_audit_routing_args`, `_handoff_args`,
  `_audit_routing_shape_args`, `_audit_routing_samples_args`, `_subagents_args`,
  plus the inline `type("A", (), {...})` namespaces for `buckets`,
  `subagent-mix`, `pr-link`, `struggle`, and `fail-seq`. Because the helper reads
  `args.this_repo` unguarded, a missed site fails loudly with `AttributeError`
  rather than silently passing.

**New tests.** Parse the values out of the output (split on the token) rather
than string-matching whole rendered lines:

- **The GH-482 regression test:** a session where *every* review event sits on a
  branch other than the first-record branch (the 53-session class from row 4).
  Assert (a) unfiltered output emits all events with correct per-event branches,
  and (b) `--branches <second-branch>` emits exactly those events and none from
  the first branch.
- `review-trace` header parses to the correct distinct `branches=`/`models=` sets
  on a multi-branch, multi-model session.
- A denial whose attachment record carries its **own** `gitBranch`, differing
  from the carried-forward branch, is stamped with the record's own value.
- A denial with no model-bearing record of its own inherits the last main-thread
  assistant model rather than rendering `other`.
- An event with an unresolvable branch renders `?` (row 7a).
- Carry-forward across a `--since` boundary: an in-window event inheriting a
  branch from an out-of-window record — assert the intended value explicitly.
- `--deny-only` combined with `--branches` where the sole denial is on the
  filtered-out branch: no block is emitted (filter-then-deny, not deny-then-filter).
- `judgment-pair --branches` selects by invocation-record branch, including a
  session whose branch changes between the invocation and the user response.
- `buckets`: same project, two session files, one branch → `Proj == 1`,
  `Sess == 2` (this is the case that discriminates set semantics from a counter);
  two project dirs sharing a branch → `Proj == 2`.
- Scope helper called twice from one `args` yields the same non-empty session
  list both times (row 15 — a shared generator would make `audit-routing`'s
  second pass silently empty).
- Scope helper raises rather than defaulting to `"*"` when `args` lacks
  `this_repo`.
- Fail-closed coverage on the newly-generic path for all three `sys.exit` sites,
  including the `if not worktree_paths` branch at line 1209, which
  `TestSkillInvocationRepoScope` does not currently cover.
- `_repo_scoped_project_slugs`' new containment-plus-identity guard (row 20):
  cwd inside a worktree subdirectory resolves successfully; cwd inside a sibling
  path sharing the same string prefix (`<repo>-fork`) exits 1 — the case bare
  `str.startswith` containment would wrongly accept.
- Two records on different branches sharing one denial `tool_use_id` in
  `cmd_review_trace`: exactly one denial event is emitted, under both an
  unfiltered run and a `--branches`-filtered run — pins step 3's dedup-before-filter
  ordering, which has a named failure mode (a differently-branched duplicate
  emitted instead of suppressed) and no other coverage.
- The resolved-scope header renders for at least one non-`skill-invocation`
  subcommand under both `--this-repo` and a glob — the sole mitigation for rows
  12a/13/13a/14/14a is otherwise asserted only by the manual corpus run in
  Verification, which is explicitly not the verification of record.
- Via `build_parser()`: `--this-repo --projects x` exits 2 on a 15-subcommand
  parser and on `skill-invocation`'s own group; `--this-repo` alone parses to
  `Namespace(projects='*', this_repo=True)` without error.

### 3. Docs and skill prose

- **`docs/transcript-analysis.md`** — new sample output for `review-trace`,
  `judgment-pair`, and `buckets` (including the scope header line); a
  `--this-repo` bullet on all 16 `--projects` flag lists. This file becomes the
  **single canonical home** for the repo-scoping caveats: rows 13, 13a, and 14
  are stated once here, in the `--this-repo` description — including the
  subdirectory-fallback recipe (`git rev-parse --path-format=absolute
  --git-common-dir | tr '/.' '-'`, plus the "derive from `--git-common-dir`, not
  `pwd`" warning, both currently only in `error-mode-analysis/SKILL.md`) as the
  documented fallback for the case `--this-repo` doesn't cover. Every other site
  points at this page rather than restating any of it.
- **`claude/.claude/skills/transcript-analysis/SKILL.md`** — rewrite the Caveats
  bullet at line 46 as follows, so the implementer is not left to decide:
  *keep* the "`--projects` defaults to `*` — every project on the machine" clause
  (still true, now answered by `--this-repo`); *keep* the `Date range`-is-not-a-window
  clause unchanged; *drop* the hand-derivation clause and its
  "see `error-mode-analysis`'s Step 1 for the full derivation" cross-reference —
  the derivation now lives at `docs/transcript-analysis.md` per the bullet above,
  not in `error-mode-analysis`; *drop* the "no project column" clause, which
  `Proj` now falsifies. Add row 19's statement that `review-trace` output is not
  publish-safe under default scope and that `--this-repo` is required before
  quoting it.
- **`claude/.claude/skills/error-mode-analysis/SKILL.md`** Step 1 — replace the
  two-line bash block with a `--this-repo` invocation. The ~6-sentence rationale
  paragraph after it — including the derivation recipe and the "do not derive
  from `pwd`" warning — is replaced by a one-line pointer to
  `docs/transcript-analysis.md`'s `--this-repo` caveat (which now carries that
  same recipe as the documented subdirectory-session fallback, per the bullet
  above — nothing is dropped, only relocated to its single canonical home). The
  following paragraph ("`buckets`, `review-trace`, and `fail-seq` all accept
  `--projects GLOB`…") gains `--this-repo`.

  Axis-1 justification for touching a file issue #482 does not name: the issue's
  third item calls out this exact prose — *"The shipped skill guidance works
  around this with a repo-root-derived prefix glob, which is a workaround rather
  than a fix"* — and this file holds the only copy of that derivation
  (`transcript-analysis/SKILL.md:46` defers to it). Shipping the fix while
  leaving the prose would instruct users toward the defeated workaround.
- **`claude/.claude/skills/transcript-narrative/SKILL.md`** — a missed consumer:
  line 11 runs `buckets` unscoped and line 62 runs `review-trace` unscoped, the
  exact pooling this plan makes visible. Add `--this-repo` to both, so the two
  consumer skills do not teach opposite scopes for the same question.

## Verification

From the worktree (the contributor `.venv` lives at the main worktree root only,
exactly three levels up):

```bash
../../../.venv/bin/pytest claude/.claude/scripts/tests/test_transcript_analysis.py
../../../.venv/bin/pytest claude/.claude/          # full suite — hooks + skills
../../../.venv/bin/ruff check claude/.claude/
```

No shell files change, so the ShellCheck lane is untouched.

**Hook-enforced review steps** (both gate `git commit`, so they are work items,
not optional): `/code-review` for the whole diff, and `/skill-review` for the
three `SKILL.md` edits per `.claude/rules/review-pipeline-dispatch.md`.

End-to-end against the real corpus — each run's output should contradict what
the pre-change tool reported:

```bash
TA=claude/.claude/scripts/transcript-analysis.py

# 1. Multi-branch sessions now show both, and each line self-attributes.
python3 $TA review-trace --this-repo | grep -c 'branches=.*,'

# 2. The regression case: a branch that returned zero rows now returns its events.
python3 $TA review-trace --branches <a-branch-that-is-not-a-session-first-branch>

# 3. Denial events carry a model family, not '?' or 'other'.
python3 $TA review-trace --this-repo --deny-only | grep denial

# 4. Pooling is visible: an unscoped run shows Proj > 1 on shared branch names;
#    the same run under --this-repo shows Proj == 1 throughout.
python3 $TA buckets | sort -k2 -rn | head
python3 $TA buckets --this-repo

# 5. Every scoped subcommand echoes its resolved scope.
python3 $TA duration --this-repo | head -2

# 6. Fails closed; refuses to combine scopes; runs from a subdirectory of THIS worktree.
(cd /tmp && python3 "$OLDPWD/$TA" buckets --this-repo)      # expect exit 1 (unrelated repo)
python3 $TA buckets --this-repo --projects '*'              # expect exit 2 (mutex)
(cd claude/.claude/scripts && python3 ./transcript-analysis.py buckets --this-repo)  # expect success
python3 $TA skill-invocation --this-repo --projects '*'     # expect exit 2 (mutex, skill-invocation too)
```

The corpus run is a pre-merge sanity check, not the verification of record — the
row 3/4 figures are machine-local and not reproducible for another contributor.
The durable signal is the GH-482 regression test above, which encodes the same
invariant on synthetic fixtures.

## Out of scope

- Changing the machine-wide `--projects '*'` default. Row 12 — `--this-repo` is
  opt-in; no existing invocation changes behavior.
- Extending `--redact` to `review-trace` (row 19). The mitigation is documentary.
- Closing row 13a's subdirectory gap by prefix matching — rejected in that row;
  the glob stays documented as the fallback.
- Resolving a project directory orphaned by a removed worktree (row 14).
  Documented, not fixed.
- `friction-count`. It takes `--transcript PATH` rather than `--projects`, so
  neither the scoping helper nor the argparse helper applies.
- Whether `review-trace` should filter denial events by `isSidechain` the way it
  filters skill and reviewer-spawn events. It does not today; that is a separate
  question from attribution and would change counts.
