# Record per-PR token and cost figures, and surface opt-in sentinels at install

## Context

**Goal:** let `pr-description` record the total tokens and list-price cost attributable to
a PR, and make every opt-in sentinel in this repo discoverable from `install.sh`.

The originating question was whether a handoff file could carry its session's cost forward,
so a PR spanning several sessions could report a running total. It cannot, usefully:
`resume-context.sh` execs a brand-new `claude` with only `--append-system-prompt-file` — no
`--resume`, no `--session-id`, no linkage of any kind
(`claude/.claude/scripts/resume-context.sh:211-224`). A cost figure travelling through a
handoff file would be a hand-maintained running total that nothing can reconcile, that
breaks whenever a session ends without writing a handoff, and that silently drops the spend
of every session that never wrote one.

The transcripts already hold the answer directly. Every assistant record carries
`gitBranch`, and `transcript-analysis.py` already prices turns per model ID. So "cost of
this PR" is a query, not carried state — recoverable at any time, including for sessions
that ended long before the PR was opened.

Intended outcome: `transcript-analysis.py cost --branches <branch> --summary` emits a
compact, aggregate-only block that cannot run unscoped; `pr-description` embeds that block
verbatim under a `## Cost` heading only in a repo whose own opt-in sentinel names it by
identity (not a copy-pasteable flag); and `install.sh` ends by listing every opt-in sentinel
with its current state, so the whole scheme stops being undiscoverable.

**Revision note.** This plan was revised after a `/plan-review` round in which
`ciso-reviewer`, `staff-platform-engineer`, and `staff-sdet` each returned Request Changes.
Every blocking finding from that round is folded into the design below; none is deferred.

## Approach

### Part 1 — branch-scoped cost in `transcript-analysis.py`

`cmd_cost` (`claude/.claude/scripts/transcript-analysis.py:3918`, report body `_cost_report`
at :3935) already iterates records and prices each assistant turn via `_price_turn` (:3728).
It takes `--projects` / `--this-repo` / `--since` / `--top` / `--by-project` /
`--config-dir`, but **no `--branches`** — while `buckets`, `fail-seq`, `struggle`,
`user-input`, `pr-link`, `review-trace`, and `judgment-pair` all filter on per-record
`gitBranch`. Four changes close the gap.

**(a) `--branches B1,B2,...` on `cost`.** Mirror `cmd_buckets`' flag shape (parser at :5402)
and filter inside the existing per-record loop (:4044) on `rec.get("gitBranch")`. Per-record,
not per-session: a single real session routinely spans branches — one measured session in
this repo's own corpus carries 863 assistant records on a feature branch and 212 on `main`,
so session-level attribution would misprice it in both directions. This is the same defect
GH-482 already fixed for `review-trace` and `judgment-pair`; `cost` is being brought onto the
pattern the other subcommands already use, not given a new one.

**(b) Token counts alongside dollars.** `_price_turn` returns dollars only. Add a
`_token_counts(usage)` helper that reuses `_cache_write_split` (:3709) to produce per-class
token counts, accumulate it in the same loop, and add a `Tokens` column to the existing
"Cost by token class" table (:4170). **`_price_turn`'s three-tuple return signature is not
changed** — five other call sites (`audit-routing`, `context-distribution`, `cost-trend`,
`audit-routing-shape`, `audit-routing-samples`) consume it as-is; a test pins the arity so a
future edit can't silently widen it for one caller and break the rest. `_token_counts`
accumulates from the same loop position as `_price_turn`, **after** the
`if dollars_by_class is None: continue` guard (:4059) — so, like dollars, an unpriced
model's tokens are excluded from the `Tokens` column and surfaced only through the separate
unpriced-tokens counter, never silently folded into a total that looks complete.

**(c) `--summary`: a structurally scoped, aggregate-only output mode.** The full report ends
with a top-N-sessions section keyed on session IDs and project labels, which
`docs/transcript-analysis.md` already states must never be published. `--summary` is a
distinct rendering branch, not a flag that trims the existing one — it never enters the
per-session labeling path at all:

- **Requires `--this-repo`, and accepts no other scope flag.** `--summary` refuses to run
  unless `args.this_repo` is `True` — it does not accept a `--projects` glob at all, default
  or otherwise (they remain mutually exclusive per `_add_project_scope_args`, and `--summary`
  additionally refuses outright if `--projects` was supplied). A first-draft version of this
  rule accepted "any `--projects` value other than the literal default `*`" as an alternative
  to `--this-repo`; that is a bypassable string-inequality check, not a scope bound — every
  project directory slug is derived from an absolute path via `_path_to_project_slug`
  (:2100-2108) and therefore begins with `-`, so a glob like `--projects '-*'` is
  machine-wide, is not the literal string `*`, and would pass that check while reproducing
  the exact cross-repo aggregation this refusal exists to prevent. `--this-repo` alone is the
  gate. Enforced inside `_cost_report` itself (mirroring the existing `--no-redact` +
  multi-root refusal at :3960, which is already justified there as "every direct caller of
  `_cost_report`, including this module's own tests, bypasses the CLI-level boundary").
  Without this, `cost --branches main --summary` filters the *machine-wide* corpus on branch
  name alone — `main`, `develop`, `staging` collide across every repo on the machine, and a
  dropped or mistyped flag in the calling skill would aggregate another repo's session data
  into this repo's PR body.
- **Refuses `--by-project`, `--no-redact`, and `--config-dir` in combination** — each of
  those existing flags drives code paths (`## Cost by project`, raw labels, multi-root
  scan-summary lines) that `--summary` structurally never reaches; accepting them silently
  would either no-op confusingly or (worse) resurrect one of the identifying code paths this
  mode exists to avoid.
- **Never calls `_redact_proj_label`, never accumulates `session_rows`, never builds or
  reads the redact map, never prints the `_DO_NOT_PUBLISH_BANNER`, and never prints a raw
  per-root path label.** These are four separate consumers of the existing `redact` variable
  (:4009 map build, :3968 banner, :3992 per-root raw-path label, :4085 the redact-map-miss
  assertion) — `--summary` does not touch `redact` at all; it takes an earlier branch that
  skips every one of those call sites, because it accumulates only class/model/thread totals
  plus session and priced-turn *counts*, never a per-session or per-project row. This is why
  `--summary` needs no map: nothing it prints is keyed by project or session identity in the
  first place. It **does** keep the diagnostic scan-count step (:3985-4007) — "scanned N
  transcripts, M skipped" plus the zero-scope `WARNING` — since that step is identity-free
  under single-root `--this-repo` scope (its `root_label` is `account-1`, never a raw path,
  whenever only one root is in scope) and is exactly what makes an empty or under-scanned
  corpus visible instead of silently printing `$0.00` as if it were a real measurement.
- **The printed scope line reports directories actually scanned, not candidate slugs.**
  `_repo_scoped_project_slugs` returns one slug per `git worktree list` entry with no
  existence check, so a naive "N project directories in scope" line sourced from that count
  would overstate coverage. The line instead reports the scan step's own `scanned` count
  (post-existence-check) alongside the priced-session count, and the zero-scope `WARNING`
  from the retained scan step above fires inside the same `--summary` block when nothing was
  found — never a silent `$0.00`.
- **Always prints an "Unpriced tokens: N tokens across M model IDs" line**, even when zero,
  so an unrecognized model ID (`<synthetic>` appears 25 times in this repo's own corpus) can
  never silently understate a published figure with no marker.
- **Carries the `STALE PRICING` banner** in the same output block as the dollar tables,
  identically to the full report — a stale run must not be able to produce clean-looking
  copyable output for a PR body.
- **A detached-`HEAD` or unresolvable branch is refused by the caller, not silently priced.**
  `pr-description` resolves the branch via `git rev-parse --abbrev-ref HEAD`; on detached
  `HEAD` this returns the literal string `"HEAD"`, which would filter to zero real records and
  print a `$0.00` block that looks like a genuine measurement. The skill checks for that
  literal value before invoking `cost` and omits the `## Cost` section with a one-line note
  instead of publishing a misleading zero.

**Worktree-isolated subagent spend is attributed by carry-forward, not excluded.** Subagents
dispatched with `isolation: "worktree"` run on a harness-generated branch, and their records
carry it literally: a corpus grep found 424 records across four such agents with `gitBranch`
values of the form `worktree-agent-<hash>`. A plain `--branches <feature-branch>` filter,
taking each record's own `gitBranch` at face value, drops every one of them — silently
undercounting real spend that was genuinely incurred in service of that branch's work. An
earlier draft of this plan responded by segregating that spend onto a separate,
excluded-from-total line, reasoning that carrying a branch forward onto it would be
"inference dressed as measurement." That reasoning doesn't hold: a `worktree-agent-*`
record's presence in `<dispatching-session-uuid>/subagents/*.jsonl` is a structural fact —
the harness nests it there — not an inference, and `worktree-agent-<hash>` itself is the
harness's *ephemeral isolation* branch, not a claim about which branch the work belongs to.

**This is new machinery, not an extension of GH-482, and the plan states that plainly rather
than overclaiming precedent.** GH-482's carry-forward (`cmd_review_trace`/`cmd_judgment_pair`)
is position-based, not timestamp-based — it works only because those functions default to
`include_subagents=False` and never see a record ordering where position and time diverge.
The one function that does cross the main-file/subagents-subdirectory boundary today,
`cmd_subagents`, takes each sidechain record's literal `gitBranch` with no carry-forward at
all. Neither existing mechanism resolves what this needs. The only genuine reuse from GH-482
is the `?` sentinel convention for "no signal to carry forward" — the resolution mechanism
itself is new:

1. **Build a per-session sorted index of `(timestamp, gitBranch)` from that session's
   main-thread records only**, once per session, before evaluating any `worktree-agent-*`
   record in it. This is necessary, not optional: `_read_session_file` (:370-409) appends
   *all* subagent records after *all* main-thread records, so main-thread and subagent
   records never interleave positionally — "nearest-preceding main-thread record by
   timestamp" cannot be answered by extending the existing single forward-pass loop
   in-place; it requires this index (or an equivalent per-session sort) plus a lookup per
   `worktree-agent-*` record.
2. **For each `worktree-agent-*` record, resolve its attributed branch as the entry in that
   index with the largest timestamp ≤ the record's own** — the branch active in the
   dispatching session at that exact moment, correctly resolving through a mid-session
   branch switch rather than collapsing to "the session's last branch." If no index entry
   precedes it (dispatched before any main-thread activity in that session), fall forward to
   the index's earliest entry instead — a **look-ahead this design introduces**, not one
   GH-482's forward-only stream provided; the per-session index already holds every
   main-thread entry, so this fallback is a lookup against it, not a second pass over the
   record stream.
3. The resolved branch, not the literal one, is what `--branches` filters on — so a
   worktree-isolated subagent dispatched mid-session from a feature branch folds directly
   into that branch's headline total, exactly like any other record.
4. Only the genuinely degenerate case — a session whose index is empty (no main-thread
   branch-bearing record at all) — stays unresolvable; it renders `?`, reusing GH-482's
   sentinel for this state, and is excluded from any branch-filtered total.

**Command-injection guard.** `pr-description` interpolates a branch name into the `cost`
invocation. Git ref names can contain shell metacharacters (`git check-ref-format` permits
`;`, `$`, `&`, `|`, backticks, and quotes). The skill resolves the branch via
`git rev-parse --abbrev-ref HEAD` immediately before the call and passes it as a quoted,
opaque literal — never string-interpolated unquoted into a shell command.

**Two lighter primitives checked before adding flags to `cost`:**

- **The built-in `/cost` slash command** (an alias of `/usage`, confirmed in the 2.1.223
  binary) already reports session cost, and the statusline payload carries
  `session.total_cost_usd`. Both are **session-scoped**: they cannot aggregate the several
  sessions a PR spans, cannot exclude the pre-branch portion of a session that started on
  `main`, and cannot be read non-interactively into a PR body.
- **`token-analyzer.py`**, which already walks the same corpus. Rejected for the reasons
  `docs/transcript-analysis.md` already records against reconciling the two: it buckets by
  model *family* so it cannot express per-model-ID rates, drops sessions with zero output
  tokens, and applies an mtime prefilter. It is also unpriced, which is the whole point here.

### Part 2 — the `## Cost` section in `pr-description`

`pr-description` gains one step: when the repo opts in (see below), run
`cost --this-repo --branches "$(git rev-parse --abbrev-ref HEAD)" --summary` and embed its
stdout **verbatim** in a fenced block under a `## Cost` heading, followed by the exact
command that produced it. The skill does not recompose, round, or re-narrate the figures —
per root CLAUDE.md's rule on quantitative claims in PR prose, the number names the source
that produced it, and a reviewer can re-run one command to check it.

**The block is machine-managed, delimited by `<!-- pr-cost:start -->` /
`<!-- pr-cost:end -->`.** `pr-description` already has this pattern for
`## Deferred review findings` (SKILL.md:76-82): the delimited span is lifted out before the
reader-coherence pass and reinserted afterward, so the pass does not flag it as a "what is
this?" span. The cost block reuses that mechanism, with one difference the skill body states
explicitly, at the point the new rule is introduced, not left implicit by proximity to the
Deferred-findings rule a few lines above it: **on sync the block is regenerated fresh, never
reinserted verbatim** — the Deferred-findings rule's "must survive byte-identical" is the
opposite instruction, and the two rules sitting next to each other in the body is exactly
where a careless read would conflate them.

**Three states on sync, not two — the gate check must distinguish "opted out" from "could
not tell."** A naive read of "regenerate when enabled, delete when not" collapses onto a
single boolean, and a transient `gh` failure during a sync pass would then read as
"disabled" and silently strip a previously-published disclosure from an existing PR body.
The skill body states three explicit outcomes:
1. **Gate check succeeds and matches** (see below) → regenerate the block fresh.
2. **Gate check succeeds and the sentinel is absent, empty, or does not match this repo** →
   delete the block if one exists — a disclosure decision that changed must not leave the
   old disclosure sitting in place.
3. **The gate check itself could not complete** (`gh repo view` exits non-zero, or produces
   empty output) → leave any existing `## Cost` block untouched and note in the run's report
   that the cost check was skipped this pass, pending a working `gh` call. Indeterminate is
   never treated as "disabled."

**Gating: a content-addressed sentinel, not a content-free flag.**
`.claude/pr-cost-disclosure` is not an empty marker file — its content is the exact
`owner/repo` string for the repo it was enabled in (e.g. `jaredcordova/claude-config`). The
gate is three explicit conjuncts, not a single equality that can be satisfied by two empty
strings: (a) the sentinel file exists, (b) its trimmed content is non-empty, and (c)
`gh repo view --json nameWithOwner --jq .nameWithOwner` for the current repo exits zero with
non-empty stdout equal to (b). Any `gh` failure — unauthenticated, no remote, offline, a
non-GitHub remote — falls into outcome 3 above, never outcome 2: a `gh` failure must never be
read as "the sentinel doesn't match," which is indistinguishable from "opted out" under a
naive content-equality check and would fail the gate *open* toward silently disabling a
previously-working disclosure rather than closed toward leaving prior state alone. `gh` is
already a hard dependency of `pr-description` (it calls `gh pr view`), so this adds no new
tool dependency.

**Scope note for the README:** content-addressing defends against a copy-pasted `.claude/`
directory landing the sentinel in an unrelated repo — it does not, and is not meant to,
distinguish between two different clones of *this same* public repo's own origin. A
non-fork contributor working directly against this repo's origin has a sentinel that
correctly matches, because they are, in fact, working in the repo it names.

This closes the disclosure risk a presence-only sentinel has: `claude/.claude/skills/` is
this repo's own stow package, and copying `.claude/` wholesale into a new repo — the
documented, encouraged way people adopt config from a repo like this one — is exactly how an
inert flag file travels silently into a client repo with no state change and no prompt. A
content-addressed sentinel cannot do that: a copied file's content still names the *origin*
repo, so it fails the match in any repo it wasn't authored for, and enabling it in a new repo
is an explicit, repo-specific act (writing that repo's own `owner/repo` string), not an
inherited default. This dissolves the travel risk structurally rather than adding a second
independent check on top of a control that still fails open by default.

**Note for `install.sh`'s inventory (Part 3):** because this sentinel is content-addressed,
its report line is one of three states, not two — *absent*, *present and matching this
repo*, or *present but content does not match this repo* (a stale copy) — surfaced
distinctly so a copied-in file reads as a warning, not as silently-enabled.

**The disclosed fields are not neutral — document them as such.** Re-derived from `--summary`'s
own output: session count, priced-turn count, and per-class token volume on a branch are an
engagement-scale and duration signal; per-model-ID dollars discloses which models are in use.
In this repo, publishing that is the point. In a repo where the sentinel is legitimately
enabled for a different reason, it may not be. The skill body and `docs/hooks.md` state this
plainly rather than describing the fields as "aggregate, therefore safe."

**Two lighter primitives checked before adding a sentinel:**

- **A `pr-description-*` project layer.** `pr-description` already globs
  `.claude/skills/pr-description-*/SKILL.md` and merges its checks (SKILL.md:30-37), and this
  repo already uses the equivalent mechanism for `plan-review`
  (`.claude/skills/plan-review-claude-config/`). It needs no sentinel and no change to the
  stowed skill. Rejected because the base skill permits **exactly one** matching layer — a
  repo later needing any other `pr-description` project rule would have to merge the cost
  step into that same file — and because enabling it in a second repo means duplicating a
  skill body rather than touching one file. The ask was a capability available across repos
  from a single opt-in; a per-repo skill body is the opposite shape.
- **No gate at all — always emit.** Rejected: `claude/.claude/skills/` is stowed to every user
  of this repo, so an unconditional step publishes spend figures into every PR body every stow
  user opens anywhere, including client repos, with no action on their part.

This repo commits `.claude/pr-cost-disclosure` containing its own `nameWithOwner` — public,
single-owner, and the case the feature was asked for.

### Part 3 — surface every opt-in sentinel in `install.sh`

Twelve behavior-toggling sentinels exist across hooks and scripts; `install.sh` offers exactly
two of them (`worktree-required` at :177, `autonomous-shipping-required` at :179 via
`_prompt_sentinel_opt_in` at :132). The other ten are discoverable only by reading
`docs/hooks.md`, and three of those (`.commit-stall-block-disabled`,
`.handoff-nudge-disabled`, `.consume-durable-continuity-disabled`) appear in no README at all.

**A resolution/mutation split, not a widened guard.** `_prompt_sentinel_opt_in` confines its
`touch`/`rm` target to `"$HOME/.claude/"*` (:139-145) because it mutates the path
unconditionally — that confinement is kept exactly as-is. The two existing calls already pass
a literal `"$HOME/.claude/<name>"` string, never a `$CLAUDE_CONFIG_DIR`-resolved path, so the
guard never fires for them regardless of `$CLAUDE_CONFIG_DIR`. Every new promptable entry
(machine-scope only) follows the identical convention: the **prompt** call always targets the
literal `$HOME/.claude/<name>` path, unchanged behavior, so adding a third prompt
(`track-permission-prompts`) cannot trip the guard's `return 1` and cannot abort the script
under `set -e` — this is true by construction, not by adding a runtime fallback.

The **report**, which only reads and never mutates, is a separate function operating under a
separate resolution rule: for each machine-scope sentinel it checks the value
`_lib_config_dir()`'s own logic would produce (`$CLAUDE_CONFIG_DIR` when set, else
`$HOME/.claude`). When `$CLAUDE_CONFIG_DIR` is set to something other than `$HOME/.claude`,
the report prints **both** paths' state and flags them as diverged, rather than picking one —
because the prompt only ever mutates the `$HOME/.claude` copy while some sentinel readers
(below) honor only `$CLAUDE_CONFIG_DIR` with no fallback, so which copy is "the real one"
genuinely depends on the specific sentinel.

**Incidental fix, named explicitly: `_lib_autonomous_shipping_active` gets the same
`$HOME/.claude` fallback `_lib_worktree_required` already has.** Investigating the report's
resolution logic surfaced a real pre-existing bug: `_lib_autonomous_shipping_active`
(`claude/.claude/hooks/_lib.sh:652`) reads only `$CLAUDE_CONFIG_DIR/autonomous-shipping-required`
with no `$HOME/.claude` fallback, unlike `_lib_worktree_required` (:615, :619), which checks
both and unions them. Concretely, this means `install.sh`'s existing autonomous-shipping
prompt — which writes to a literal `$HOME/.claude/autonomous-shipping-required` — is already
silently ineffective for any user running under a non-default `$CLAUDE_CONFIG_DIR`. This
report is what makes that divergence visible for the first time, and a report whose "current
state" column is wrong for the one row it's about to newly surface undermines the whole
feature. Fix it here as a small, non-cosmetic fix with direct value to a PR this size stays
coherent with (root CLAUDE.md Axis 1, bucket 2) — one line added to
`_lib_autonomous_shipping_active`, mirroring `_lib_worktree_required`'s existing union check
exactly.

**Array schema.** `SENTINEL_INVENTORY` is a flat, pipe-delimited string array — not an
associative array, since the system bash on this machine (and macOS's shipped default
generally) is 3.2, which has no `declare -A`. Fields, with **no surrounding whitespace around
any `|`** (a schema with spaces, `path | scope | name`, feeds `IFS='|' read -r` fields with
leading/trailing spaces baked in — a real, silent drift in the two currently-hardcoded
prompt strings that no existing test would catch):

```
path-template|scope|human-name|prompt-description|default-state|docs-anchor
```

`prompt-description` is carried only for `scope=machine-promptable` rows — it is not new
prose; it is the same longer explanatory sentence `_prompt_sentinel_opt_in`'s existing calls
already hardcode as their third positional argument (install.sh:178, :180), moved into the
array so the loop can pass it through unchanged. `human-name` is the same short label already
passed as the function's second argument. The array is a data table for enumeration, not an
expansion of what's said — `docs/hooks.md` remains the authoritative behavior description;
the array's `docs-anchor` field points there rather than restating it.

`configure_machine_level_opt_ins` iterates the `scope=machine-promptable` rows and calls
`_prompt_sentinel_opt_in "$HOME/.claude/<name>" "<human-name>" "<prompt-description>"` for
each — the existing TTY guard, path confinement, asymmetric `[Y/n]`/`[y/N]` defaults, and
`|| answer=""` EOF handling are all unchanged. It records which array indices it prompted
this run (a simple space-separated index list) for the report to consume.

`report_sentinel_inventory` is new, non-interactive, and read-only — it creates and removes
nothing. It is called **after** `configure_machine_level_opt_ins`, iterates every array row
(not only the promptable ones), and prints each sentinel's resolved current state plus its
docs anchor. For any index `configure_machine_level_opt_ins` recorded as prompted this run, it
suppresses the "here's the command to enable it" hint line (the state is still shown — just
not paired with an enable-hint for a sentinel the user was asked about seconds earlier).

**Both functions and the array live inside one `INSTALL_TEST_FIXTURE: sentinel-inventory`
block**, not split across two — `install.sh` deliberately runs without `set -u` (the header
comment at :44-45 explains why: fatal on an empty array under macOS system bash 3.2), so a
test block that captured the array declaration without both consumers, or vice versa, would
have the missing half silently iterate zero times rather than erroring. A new test asserts
the array's entry count is nonzero, catching exactly that failure mode.

**`.gitignore` gets the six missing entries, in this PR.** Two machine-scope sentinels
(`worktree-required`, `.error-mode-nudge-enabled`) already have `.gitignore` protection
against landing physically inside the repo under stow directory-fold; six do not
(`autonomous-shipping-required`, `track-permission-prompts`, `.commit-stall-block-disabled`,
`.handoff-nudge-disabled`, `.consume-durable-continuity-disabled`,
`.session-title-disabled`). `report_sentinel_inventory`'s entire purpose is printing the
enable-command for sentinels a user did not previously know existed — it manufactures exactly
the traffic that trips this gap, on a public repo, rather than merely sitting adjacent to it.
Closing it here is in scope, not a follow-up.

### Assumption ledger

**Root problem:** the spend attributable to a PR is measurable from data already on disk, but
no tool scopes it to a branch, so the figure is never recorded and no reviewer can see what a
change cost to produce.

**Givens** — conditions this design treats as fixed and does not attempt to change:

| Given | Why it is beyond this plan's reach |
|---|---|
| No session-identity linkage survives a handoff resume | `resume-context.sh` is a claude-config-owned file and technically in reach, but `docs/hooks.md:52` documents that the review-gate markers are deliberately content-keyed, not identity-keyed, specifically so they survive this discontinuity — adding session linkage would need to unwind that separate, already-considered architecture decision, not just this plan's own scope. |
| Local figures are list price, not billed price | Transcripts persist token counts only — no `costUSD` field exists on any record. Vendor billing terms for a subscription plan are exposed to no local artifact; only list rates are published. |
| `isolation: "worktree"` agents get a harness-generated branch name | The harness assigns it; nothing in this repo controls the branch a dispatched agent's worktree is created on. |

| # | Assumption | Tag |
|---|---|---|
| root | Statement above | — |
| row1 | `cost` has no `--branches` flag; 7 other subcommands filter on per-record `gitBranch` | `[verified: read p_cost parser at transcript-analysis.py:5646-5685 and _add_project_scope_args at :5366 this session]` |
| row2 | Assistant records carry per-record `gitBranch`, and a single session commonly spans branches | `[verified: one session in this repo's corpus splits 863 records on a feature branch / 212 on main]` |
| row3 | Subagent records carry `gitBranch` and `isSidechain: true`, and live in `<session-stem>/subagents/*.jsonl` — never inline in the parent file; appended after the parent's own records | `[verified: 619 subagent assistant records in the sampled session, all isSidechain=true, all carrying gitBranch; zero isSidechain=true records in any top-level transcript machine-wide; _read_session_file:370-409 appends them]` |
| row4 | `cost` already prices subagent turns — `_resolve_project_scope(..., include_subagents=True)` | `[verified: _cost_report:3978 and its docstring]` |
| row5 | `isolation: "worktree"` subagent records carry `gitBranch: worktree-agent-<hash>` and are dropped by a plain branch filter unless resolved by a per-session timestamp index built from that session's own main-thread records | `[verified: corpus grep found 424 such records across 4 distinct worktree-agent branches]` |
| row5a | GH-482's carry-forward is position-based and confined to `include_subagents=False` callers; it never crosses the main-file/subagents-subdirectory boundary and has no fall-forward (look-ahead) case. `cmd_subagents`, the one function that does cross that boundary, takes each sidechain record's literal `gitBranch` with no carry-forward at all. The only genuine reuse from GH-482 is the `?` sentinel convention | `[verified: staff-sdet's read of cmd_review_trace/cmd_judgment_pair, cmd_subagents (:1030-1100), and their include_subagents defaults this round — corrects an earlier overclaim in this same plan that GH-482 already established this pattern]` |
| row5b | Subagent records carry a `timestamp` field in the same ISO 8601 format as main-thread records, making cross-record timestamp comparison within one session valid | `[verified: sampled main-thread and subagent timestamps from this repo's own corpus this session — e.g. 2026-08-03T19:13:13.608Z (main) vs. 2026-08-03T19:46:38.622Z (subagent), same format]` |
| row6 | `cost` prints dollars only; no token counts anywhere in its output | `[verified: rendering block at transcript-analysis.py:4170-4180 and the sample output in docs/transcript-analysis.md]` |
| row7 | `_price_turn` is called from 5 subcommands besides `cost` | `[verified: usage-block read sites at :3495, :4047, :4316, :4402, :4696, :4932]` |
| row8 | `usage.iterations[]` is a sub-breakdown of the same turn's top-level counts, not an addend | `[verified: sampled usage object — iterations[0] repeats the record's own input/output/cache figures]` |
| row9 | The `cost` top-N-sessions section is project-identifying and must not be published | `[verified: docs/transcript-analysis.md "When to reach for it" states this explicitly]` |
| row10 | `redact` gates four separate consumers in `_cost_report`: map build (:4009 area), `_DO_NOT_PUBLISH_BANNER` (:3968), per-root raw-path label (:3992), and the redact-map-miss assertion (:4085) | `[verified: staff-sdet's and ciso-reviewer's independent reads of _cost_report both converged on this — the reconciliation-worthy distinct-failure-mode case: one flagged the crash, the other flagged the privilege/exposure angle]` |
| row11 | `pr-description` supports exactly one `pr-description-*` project layer and halts on multiple | `[verified: pr-description/SKILL.md:30-37]` |
| row12 | `pr-description` already has a lift-and-reinsert mechanism for a delimited machine-managed block | `[verified: pr-description/SKILL.md:76-82, the `code-review:deferred` delimiters]` |
| row13 | Twelve behavior-toggling sentinels exist; `install.sh` offers two | `[verified: full grep sweep of hooks/scripts/skills this session; install.sh:177,179]` |
| row14 | `_prompt_sentinel_opt_in` refuses any path outside `$HOME/.claude/`, and both existing call sites already pass a literal `$HOME/.claude/...` string rather than a `$CLAUDE_CONFIG_DIR`-resolved one | `[verified: install.sh:139-145, :177, :179]` |
| row15 | `_lib_autonomous_shipping_active` has no `$HOME/.claude` fallback, unlike `_lib_worktree_required`'s union check | `[verified: staff-platform-engineer's read of _lib.sh:615,619,652 this round]` |
| row16 | `install.sh` runs with `set -e` but deliberately without `set -u`, specifically because an empty array under macOS system bash 3.2 would otherwise be fatal | `[verified: install.sh:2, :44-45 comment]` |
| row17 | `_cost_args()`'s existing ~60 call sites in `test_transcript_analysis.py` construct a fixed attribute set; a new flag read via bare `args.summary` would `AttributeError` on all of them, while `_branch_filter` already reads via `getattr` | `[verified: staff-sdet's read of _cost_args at :3256 and _branch_filter at :71 this round]` |
| row18 | A session under a non-personal account resolves that account's own transcripts automatically, so `--config-dir` is not needed in the normal PR flow | `[verified: config_dir() in scripts/_config_dir.py reads $CLAUDE_CONFIG_DIR; cost additionally refuses the top-level --config-dir]` — see [[multi-account-claude-config-dirs]] |
| row19 | Cost belongs in the PR body, reported as tokens **and** a list-price dollar figure | `[engineer-verified]` |
| row20 | Handoff files should not carry cost forward | `[engineer-verified]` |
| row21 | The section is gated on an opt-in sentinel, and all sentinels should be surfaced by `install.sh` | `[engineer-verified]` |
| row22 | A content-free sentinel travels silently via the documented `.claude/` copy-paste adoption path; a content-addressed sentinel (storing the origin repo's `owner/repo`) does not, since a copied file's content still names the wrong repo | `[verified: ciso-reviewer's finding this round, re-derived: gh repo view --json nameWithOwner is inherent to the git remote and is not part of a directory copy-paste]` |
| row23 | Every `_path_to_project_slug`-derived project directory slug begins with `-` (absolute-path-derived), so a `--projects` glob like `-*` is machine-wide despite not being the literal default `*` — a "non-default `--projects`" escape hatch on `--summary`'s scope gate is bypassable, not merely permissive | `[verified: ciso-reviewer's confirmatory-round finding, re-derived against _path_to_project_slug at :2100-2108]` — closed by removing the `--projects` arm entirely; `--summary` accepts `--this-repo` only |
| row24 | An unspecified `gh repo view` failure mode in a content-equality gate check risks comparing two empty strings and opening the gate; the correct posture is a three-outcome gate (match / no-match / indeterminate), never collapsing "could not check" into "disabled" | `[verified: ciso-reviewer's confirmatory-round finding]` — closed by stating the three explicit conjuncts and the three sync-time outcomes |

**Mechanism justification.** Every change extends a mechanism already carrying this weight: a
flag on an existing subcommand rather than a new subcommand (`anchors: root`); a helper reusing
`_cache_write_split` rather than a second pricing path (`anchors: row7`); a delimited block
reusing the deferred-findings lift-and-reinsert mechanism rather than a new body convention
(`anchors: row12`); a content-addressed sentinel that dissolves the travel risk structurally
rather than a bolted-on second check (`anchors: row22`); an array-driven inventory that keeps
`_prompt_sentinel_opt_in`'s confinement and its literal-path convention exactly as-is rather
than widening the guard (`anchors: row14`). No new dependency, no new hook, no new permission
scope. Lighter primitives rejected for Part 1 (`/cost`, `token-analyzer.py`) and Part 2
(project layer, no gate) are enumerated in those sections with the reason each fails.

## Critical files

**Reuse, do not reimplement:** `_price_turn` (:3728), `_cache_write_split` (:3709),
`_resolve_project_scope` (:2323), `_parse_since_nd_arg` (:337), `_context_bucket` (:3724),
`_pct_of` (:3762), `_add_project_scope_args` (:5366), `_branch_filter` (:71, already
`getattr`-based — the model for how `--summary` must be read), and `_prompt_sentinel_opt_in`
(`install.sh:132`) all already do the work; every change below calls into them.

| File | Change |
|---|---|
| `claude/.claude/scripts/transcript-analysis.py` | `--branches` (parser + per-record filter) and `--summary` (its own refusal/rendering branch, never touching `redact`) on `p_cost`; a new per-session `(timestamp, gitBranch)` index built from main-thread records, used to resolve each `worktree-agent-*` record's attributed branch by nearest-preceding-or-earliest-following timestamp (this is new logic — no existing helper does this; it is not an extension of GH-482's position-based carry-forward); both new flags read via `getattr(args, "...", default)`, never a bare attribute access, so `_cost_args()` and its ~60 existing call sites are untouched; `_token_counts` helper; `Tokens` column excluding unpriced turns; the resolved-scope-count line; the always-present unpriced-tokens line |
| `claude/.claude/scripts/tests/test_transcript_analysis.py` | Tests per the existing `capsys` + `monkeypatch(PROJECTS_DIR)` seam; `_priced()` gains a `branch=` kwarg defaulting to `"main"` so every existing call site is unaffected; new fixtures use ≥2 non-`claude-config` project labels (`_redact_proj_label` passes `claude-config` through unmapped, so a claude-config-only fixture can't test the miss path) |
| `claude/.claude/skills/pr-description/SKILL.md` | Content-addressed sentinel check (`gh repo view --json nameWithOwner` match); sentinel-gated `## Cost` step; the `pr-cost` delimiters added to the machine-managed-blocks rule with the regenerate-vs-verbatim distinction stated as a flat rule, not left implicit; absent-sentinel-on-sync deletes the block; branch resolved via `git rev-parse --abbrev-ref HEAD` and passed as a quoted literal |
| `install.sh` | `SENTINEL_INVENTORY` array (pipe-delimited, no surrounding whitespace, bash-3.2-safe); `configure_machine_level_opt_ins` iterates promptable rows and records prompted indices; new `report_sentinel_inventory` (resolution-only, `_lib_config_dir`-aware, called after the prompt step, suppresses enable-hints for just-prompted entries); one-line `$HOME/.claude` fallback added to `_lib_autonomous_shipping_active`; both functions and the array inside one new `INSTALL_TEST_FIXTURE: sentinel-inventory` block |
| `.gitignore` | Six new entries: `autonomous-shipping-required`, `track-permission-prompts`, `.commit-stall-block-disabled`, `.handoff-nudge-disabled`, `.consume-durable-continuity-disabled`, `.session-title-disabled`, matching the existing two entries' comment style |
| `claude/.claude/hooks/tests/test_install_sh_sentinel_inventory.py` | New file, matching the sibling `test_install_sh_*.py` block-extraction pattern; includes the nonzero-entry-count test and the full-`$HOME`-snapshot non-TTY no-op test |
| `docs/transcript-analysis.md` | `cost` flag entries for `--branches`/`--summary`, a `--summary` sample block, the worktree-agent-* carry-forward-attribution note (and its `?`-sentinel degenerate case), and the "these fields are disclosive" language |
| `claude/.claude/skills/transcript-analysis/SKILL.md` | One routing-table row. Also fix the stale frontmatter `description` line pointing token-cost questions at `token-analyzer.py` — an always-loaded trigger-matching field, not body text — and re-run `evals/run_skill_evals.py --skill transcript-analysis` after the edit |
| `README.md` | `pr-cost-disclosure` in the opt-in sections alongside worktree/autonomous-shipping, stating explicitly that the sentinel is content-addressed and what that closes; pointer to the install-time inventory |
| `docs/hooks.md` | Add the disclosive-fields note and the sentinel-travel rationale for `pr-cost-disclosure` |
| `.claude/pr-cost-disclosure` | New file, content = this repo's own `owner/repo` string (not empty) — this repo opts itself in |

## Verification

Run from the branch's worktree (`.venv` lives at the main worktree root only, three levels up):

1. `../../../.venv/bin/pytest claude/.claude/` — full suite, not just the touched files. CI runs
   the whole tree with `stow` installed, so install `stow` locally first or the run is not
   CI-equivalent.
2. `../../../.venv/bin/ruff check claude/.claude/`
3. `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` — required here, since
   `install.sh` and `_lib.sh` changed.
4. **Branch filter is per-record, not per-session:** hand-built corpus with one session split
   across two branches; assert `--branches A` returns only A's dollars/tokens and that
   `--branches A` + `--branches B` sum to the unfiltered total. Uses `_priced(branch=...)`.
5. **Token math:** hand-computed corpus with known counts and a hand-written expected total,
   read back through a named extractor rather than string-matching formatted output.
   Nested-`cache_creation` and flat-only fallback both asserted; `iterations[]` present but not
   double-counted; an assertion pins `_price_turn`'s three-tuple arity.
6. **`--summary` refuses any scope other than `--this-repo`:** `cost --summary` with no
   `--this-repo` exits non-zero, including when a `--projects` glob is supplied instead —
   `--projects '*'`, `--projects '-*'`, and any other glob are all refused, not just the
   literal default. `--summary --by-project`, `--summary --no-redact`, and
   `--summary --config-dir <dir>` are each refused too.
7. **`--summary` emits nothing identifying, under any accepted flag combination:** a ≥2-project
   fixture (neither labelled `claude-config`); assert no raw project name, no
   `private-project-N` label, and no session ID appears anywhere in stdout, and that
   `_redact_proj_label` / `_build_redact_map` are never called on that path.
7a. **Cross-repo aggregation is excluded from the total, not just from the label set.** The
    ≥2-project fixture from item 7, run as `--summary --this-repo` scoped to one of the two
    projects: assert the dollar/token totals equal a single-project-only computation over that
    project's records alone — proving the second project's spend is absent from the *numbers*,
    not only that its name is absent from the text. A test that checks only for missing labels
    (item 7 alone) passes even when both projects' dollars are silently summed together.
8. **Carry-forward attribution for `worktree-agent-*` records:** (a) a session with a
   main-thread record on the requested branch followed (later timestamp) by a `worktree-agent-*`
   subagent record — the subagent's dollars/tokens fold into the requested branch's headline
   total, not a separate line; (b) the same case but with the `worktree-agent-*` record's
   timestamp *earlier* than any main-thread record in the session — resolves by falling forward
   to the index's earliest entry, same result as (a); (c) **the discriminating case: a session
   whose main-thread records switch branches mid-session (feature-branch, then later `main`),
   with a `worktree-agent-*` record whose real timestamp falls *before* the switch** — must
   resolve to the pre-switch (feature) branch. Given `_read_session_file`'s append order, this
   subagent record is positioned *after* both main-thread records in the merged list despite
   its earlier timestamp — a broken implementation that resolves from record-list position (or
   "last branch seen in the session") rather than the timestamp index would resolve it to
   `main` instead, and this is the one fixture shape that catches that; a case built the other
   way (asserting the subagent record precedes a main-thread record in list order) is not
   constructible under the real append order and is not a valid test; (d) a session with **no**
   main-thread branch-bearing record at all — the `worktree-agent-*` record renders `?`
   (GH-482's sentinel convention, reused; the resolution mechanism itself is not GH-482's) and
   is excluded from every `--branches` filter's total, the one case that stays genuinely
   unattributable.
9. **Unpriced-model handling under `--summary`:** a fixture mixing a priced and an unpriced model
   ID; assert the unpriced-tokens line is present and non-silent, and that the `Tokens` column
   excludes the unpriced turn.
10. **STALE PRICING × `--summary`:** `today` past a rate's `expires` → banner present in
    `--summary` output; `today` before `expires` → absent. Mirrors the existing full-report pair.
11. **Null-`gitBranch` record:** counted in an unfiltered run, excluded (deliberately) under any
    `--branches` filter — pins that the branch-filter sum invariant in item 4 isn't silently
    passing only because the fixture happens to have no null-branch record.
12. **Regression gates, both sides:** `git diff --exit-code` on `test_transcript_analysis.py`'s
    pre-existing `cmd_audit_routing`/cost tests, and on `test_install_sh_machine_level_opt_ins.py`,
    at the end of the branch — if either needed an edit to accommodate the new code, that's a
    behavior change, not a preserved regression gate.
13. **`install.sh` non-interactive run** prints the full inventory (asserted non-empty — the
    nonzero-entry-count test) and, per the strengthened non-TTY bar, leaves a full recursive
    `$HOME` fixture snapshot byte-identical before and after.
14. **`pr-cost` wiring tripwire** (source-scan, explicitly labelled non-behavioral): asserts the
    `<!-- pr-cost:start -->`/`<!-- pr-cost:end -->` delimiter strings and the content-addressed
    sentinel check appear in `pr-description/SKILL.md`, alongside the existing `code-review:deferred`
    assertion pattern in `claude/.claude/skills/tests/test_skills.py`. `pr-description` has no
    behavioral test suite in this repo (its `tests/` doesn't exist) — Part 2's actual behavior
    (sentinel absent → no section; present + matching → sync regenerates; present + no PR →
    author mode includes it) is validated by runtime observation only, stated here rather than
    implied by this tripwire's presence.
14a. **Gate fail-closed behavior (runtime observation, per item 14's stated limit — `pr-description`
    has no behavioral test suite):** manually verify, once, that a `gh repo view` failure (e.g. run
    from a directory with no `git remote`) leaves an existing `## Cost` block untouched rather than
    deleting it, and that a detached-`HEAD` checkout omits the section rather than publishing a
    `$0.00` block.
15. End-to-end: on this branch, run
    `python3 ~/.claude/scripts/transcript-analysis.py cost --this-repo --branches <branch> --summary`
    and confirm the block is paste-ready and its grand total does not exceed an unfiltered
    `cost --this-repo` run over the same corpus.
16. `/skill-review` (hook-enforced on the two SKILL.md edits — already run at plan-review time
    against this design; re-run against the literal drafted text once written), then
    `/code-review`, then commit; `/ready-for-review`, then open the PR. Autonomous shipping is
    active on this machine, so this proceeds without further prompting. Merge stays human-only.

## Out of scope

- **Backfilling cost onto already-merged PRs.** The query works retroactively, but rewriting
  merged PR bodies is a separate decision.
- **Reconciling list price against actual billing.** No local artifact carries billed amounts;
  the figure is labelled a list-price estimate and that is the ceiling of what is knowable here.
- **Prompting for repo-scoped sentinels in `install.sh`.** `_prompt_sentinel_opt_in`'s
  `$HOME/.claude/` confinement is kept exactly as-is (not widened); the inventory reports
  repo-scoped entries' state (including the three-state content-match case for
  `pr-cost-disclosure`) without offering to create or remove them.
- **Cross-session carry-forward** (resolving a `worktree-agent-*` record's branch from a
  *different* session's main-thread records). The dispatching session is always the
  attribution boundary; a record with no main-thread anchor in its own session renders `?`
  rather than searching other sessions for one.
