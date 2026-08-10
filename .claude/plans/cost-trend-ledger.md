# Cost-trend ledger: a durable, annotated series for workflow-efficiency metrics

## Context

**Goal:** give this repo a durable, append-only record of its own
volume-invariant cost metrics, so that a workflow change made today can be
scored against a baseline months from now — after the transcripts that
produced the baseline are gone.

**Problem.** GH-554's efficiency audit shipped four fixes (F1–F4, closed by
#552/#569, #561/#566/#579/#593, #587, #601) and no way to tell whether they
worked. Its own method demands one: axis 2 of its three-axis waste screen is
"pair every cost center with a yield signal — a cost center with no yield
signal is not efficient, it is *unmeasured*, which is itself the finding." The
audit applied that test to reviewer fan-out and never to itself.

The instrument for the corpus-level half already exists: `cost-trend`
(#569) prints per-ISO-week dollars, context share, and Opus share, and runs in
25.9s over the current corpus. What is missing is not measurement. It is
**retention, comparability, and annotation**:

- **Retention.** `cost-trend` re-derives every week from transcripts still on
  disk, and Claude Code deletes those transcripts. Per
  code.claude.com/docs/en/claude-directory: "Files in the paths below are
  deleted on startup once they're older than `cleanupPeriodDays`. The default
  is 30 days and the minimum is 1; setting `0` fails with a validation error."
  The listed paths include `projects/<project>/<session>.jsonl`, the full
  transcripts this tooling reads, and there is no value that disables deletion.
  Every week not recorded while it is observable is a week that cannot be
  recovered — not as a matter of disk hygiene, but by documented default
  behavior on a roughly 30-day fuse.
- **Comparability.** `cost-trend`'s dollars are computed from a list-price
  table with an explicit fetch date (`_PRICING_FETCH_DATE = date(2026, 8, 2)`,
  `transcript-analysis.py:3975`) and per-model re-verify-by dates
  (`_MODEL_RATE_EXPIRES`, 4006-4009). When that table is next updated, every
  historical dollar figure silently changes meaning. A series that does not
  record which rate table produced each row splices two incomparable curves
  and reads as a step change in spend.
- **Annotation.** A number with no note beside it saying what changed that
  week cannot answer "did that change help." This is the whole point of the
  exercise and no existing output carries it.

**Why now.** The four interventions merged between 2026-08-03 and 2026-08-09.
The weeks that would score them have not happened yet, and the weeks that
would serve as their baseline are inside the current 46-day window and
counting down. Recording starts paying immediately and cannot be
retro-applied.

**User surface.** Everything under `claude/` is stowed into `$HOME` for every
contributor who clones and runs `./install.sh` — this is not personal
tooling. A recorder that writes files, and a hook that fires on every prompt,
reach every stow consumer on `git pull`. The design is opt-in by default for
exactly that reason.

## Approach

Three pieces, phased. Phase 1 is self-contained and useful alone; Phase 2 adds
the one genuinely normalized metric and carries a network dependency Phase 1
deliberately avoids.

### Phase 1 — `cost-ledger` subcommand + committed ledger file

A new `cost-ledger` subcommand in `transcript-analysis.py` with two modes:

- **Read (default):** print the ledger file's rows, followed by any weeks
  present in the live corpus that the ledger has not yet captured — so the
  gap between "recorded" and "still recoverable" is visible at a glance
  rather than discovered when it is too late.
- **`--record`:** compute the current week's row from the same code path
  `cost-trend` uses and append it to the ledger file. Refuses to overwrite an
  existing row for the same (week, machine) pair unless `--force` is passed;
  a week's numbers change as the week fills, and silently rewriting history is
  how a ledger stops being one.

**Row schema** (markdown table, one row per week per machine):

| Column | Source | Why it is in the ledger |
|---|---|---|
| `week` | ISO week label | Join key |
| `machine` | operator-supplied `--machine-label` | Multi-machine merge; see below |
| `rates` | `_PRICING_FETCH_DATE` | Rows computed under different price tables are not comparable |
| `usd` | `cost-trend` | Volume, not efficiency — present for context, not for scoring |
| `context_pct` | `cost-trend` | F1's thesis: context is ~88% of the bill |
| `opus_pct` | `cost-trend` | Model-routing discipline |
| `ge200k_pct` | `_context_bucket` | F2: did the handoff nudge move sessions below the line |
| `denials` | `review-trace --deny-summary` | F3: is gate friction falling |
| `reviewer_gap_pp` | `reviewer-yield` | F4: percentage-point gap between the findings-found and zero-finding cited-path edit rates — one number for "is reviewing earning its dispatch" |
| `note` | operator-supplied `--note` | What changed in the workflow that week |

Carrying the F3 and F4 signals as core columns rather than opt-in extras is a
measured call: `cost-trend` 25.9s, `review-trace --deny-summary` 19.0s, and
`reviewer-yield` 27.6s on the current corpus — ~72s for all three. That is
cheap enough that hiding them behind flags would buy nothing and would let
rows exist with the two most interesting columns empty.

`denials` is a raw count and therefore volume-sensitive in the same way `usd`
is; it is recorded for composition against the percentage columns, not as a
standalone score. Normalizing it needs a per-turn denominator that
`review-trace --deny-summary` does not emit — see Out of scope.

**No `fingerprint` column.** An earlier draft published
`_corpus_fingerprint` — a sha256 over the sorted set of raw project labels,
truncated to 12 hex chars — so that two rows could be checked for
same-corpus-ness. Dropped on review, for two independent reasons:

- **It is deanonymizable.** The input set is small and human-guessable
  (project names). An observer with a candidate roster computes the same
  digest locally and confirms it; watching the value change across weekly rows
  reveals when an engagement started or ended. `_corpus_fingerprint`'s own
  docstring says it is "not a security boundary," and the repo's
  redact-structural-fingerprints rule covers exactly this.
- **It is expensive and off-purpose.** `_corpus_fingerprint` takes a redact
  map, and `_build_redact_map` (3683-3729) reads every project's transcript
  bytes machine-wide *regardless of the caller's scope flags* — a full scan to
  produce one column, in a design whose stated posture is minimization.

The `machine` column already distinguishes rows by origin, which is what the
fingerprint was actually for. Corpus identity remains available locally
through `cost`, which is where it belongs; it is never written to the file.

**The ledger file is `docs/cost-ledger.md`**, alongside the existing
`docs/reports/` audits. It is a distinct artifact type from those: they are
dated, one-shot, immutable findings; this is a single file appended to
indefinitely.

The file is **committed with its header and schema preamble as part of this
change**, and doubles as the `docs-anchor` for the sentinel. `--record`
therefore requires it to exist and never lazily creates it — which removes the
question of what a lazily-created file does to a hand-authored prose anchor,
and means a second machine finds the file already present after cloning.

**`machine` values are operator-chosen opaque tokens** (`--machine-label`, no
default — the recorder refuses without it). Convention is not the control: the
recorder **rejects** a label that case-insensitively matches this machine's
hostname (`scutil --computername` / `hostname`), and restricts the value to
`^[a-z0-9]{1,8}$` — short, lowercase-alphanumeric, no spaces or unicode, which
is wide enough for `m1`/`laptop2` and narrow enough that a hostname, a
username, or anything that would break the row parser cannot be expressed in
it. The repo's redaction hook would not catch a plain machine name — its
hostname detector matches only internal TLDs — so this check has to live in
the recorder.

The rejection message names the rule, never the compared hostname value.
`deny-private-project-refs.sh` (~602-607) deliberately withholds matched
hostname and IPv4 values from its own deny text on the grounds that echoing
them persists recon-value data into the session transcript; the same
discipline applies here.

**`note` is free text and therefore the highest-risk column.** It may be
drafted by an agent mid-session, with the whole session's context in reach,
including private project names.

The control for it already exists and is not rebuilt here.
`deny-private-project-refs.sh` scans `git diff --cached` (line 454) and denies
on blocklist hits in "staged/committed content" (line 710) — and a recorded row
is staged content. Writing the row to the working tree is not the publish
event; `git commit` is, and that boundary is already gated. An earlier draft
had the recorder re-run the blocklist scan itself; that was a second
implementation of an existing control at the wrong boundary. The hook's
matching logic lives inline in that bash file and is not importable, so
duplicating it in Python would also have been a DRY violation needing a named
exception — for no gain, since nothing reaches the public repo without passing
through the gate that already exists.

What the plan does state: the blocklist only knows engagements already listed
in `~/.claude/private-projects.md`, so for a newly started engagement, human
review of the diff is the backstop. A recorded row is pre-commit-review
material like any other agent-drafted content.

**Disclosure.** Committing this file publishes the repo owner's weekly
Claude Code spend and its composition, in perpetuity, to a public repo. That
is consistent with what GH-554 and its children already publish in issue
comments, and every remaining column is a corpus-level aggregate carrying no
session, project, path, or per-repo figure. It is still a deliberate choice,
and the sentinel is what makes it opt-in rather than automatic.

The dollar column is deliberately *not* the headline. Weekly spend is
dominated by how much work was done — W31→W32 moved +18% with composition
flat to within a point — so the total cannot separate "more efficient" from
"worked more." The four percentage columns can. Recording the dollars anyway
costs nothing and makes the normalizer in Phase 2 checkable.

**Shared row computation.** `_cost_trend_report` (5383-5453) prints and
returns `None`, and its ISO-week bucketing line is duplicated verbatim at five
call sites (3301, 3450, 5424, 5439, 5517) — so "reuse the path `cost-trend`
already uses" is not achievable as written. This change **extracts
`_compute_cost_trend_data(session_iter) -> dict[str, dict[str, float]]`** and
has both `_cost_trend_report`'s printer and the new row builder call it. The
recorder must never parse `cost-trend`'s formatted stdout: scraping `$14,.2f`
strings would duplicate production formatting logic in a parser that breaks on
a column-width change and passes while broken.

**Error paths**, each with defined behavior rather than left to the
implementer:

- *Empty corpus, or a current week with zero priced turns* (every turn on an
  unpriced model): `--record` refuses with a non-zero exit and writes nothing.
  A blank or zero row is worse than no row — it reads as a measured quiet week.
- *Malformed ledger* (hand-edited row, merge-conflict markers, a `|` inside
  `note`): the parser fails loud and `--record` refuses, rather than
  mis-parsing and appending after a row it misread.
- *Concurrent recorders*: the check-then-append sequence takes an exclusive
  lock on the ledger file for the read-check-write window, so two processes
  cannot both pass the duplicate check.
- *Clock skew*: the recorder compares the week it is about to label against the
  maximum timestamp in the corpus and refuses when they disagree, rather than
  permanently occupying the wrong (week, machine) slot under the no-overwrite
  rule.
- *Crash mid-append*: the row is written to a temp file, parsed back, then
  moved into place, so a killed process cannot leave a partial row that the
  next run's duplicate check misreads.

**Multi-machine.** Rows carry a machine label; each machine appends its own
rows and git merges them. This mirrors a pattern already proven in a separate
private repo's session-analysis tooling, where the scripts that need data from
a second machine are run there and emit portable, aggregate-only JSON the
primary machine consumes — no raw session data crosses the boundary. Adopting
that shape here rather than inventing a second answer. An append-only table
conflicts trivially and resolves by keeping both rows.

**Opt-in.** Recording writes a file, so it is gated on a machine-scope
sentinel `~/.claude/.cost-ledger-enabled`, registered in `install.sh`'s
`SENTINEL_INVENTORY` (215-229). Empty-marker shape, which is the convention
for every sentinel in that array except `.claude/pr-cost-disclosure` —
content-addressing exists there to catch a copied `.claude/` tree carrying
another repo's identity, and nothing here depends on repo identity.

### Phase 2 — `$` per merged PR

The one genuinely normalized cost metric, and the one the ledger's percentage
columns cannot provide: dollars divided by units of shipped work.

`pr-link` already maps branches to PR numbers and `cost --branches` already
prices a branch (including `worktree-agent-*` subagent spend, carried forward
onto the dispatching branch by `_attributed_branch`, 4405-4435). The missing
piece is small and specific: `pr-link`'s `gh pr list` call requests
`--json number` only (3360-3364), so nothing in the toolkit knows whether a
PR reached MERGED. Phase 2 adds `state` to that fetch, surfaces it as a
column, and adds a `--merged-pr-cost` flag to `cost-ledger --record` that
fills a `usd_per_merged_pr` column.

Phased separately because it adds a `gh` + network dependency and a required
`--repo` argument to a recorder that is otherwise offline and corpus-wide,
and because the metric has a real interpretive caveat (a large PR legitimately
costs more, so the mean is sensitive to PR-size mix — the median is the
honest statistic and both should be emitted).

**Two consequences of touching `pr-link` that this phase must handle:**

- None of `pr-link`'s three `subprocess.run` calls (3361-3364, 3378-3381,
  3385-3388) carry a `timeout=`. Today a hang stalls a manually-run command
  and the operator interrupts it. Embedding the same call in `--record`
  changes the blast radius to an unattended flow that hangs indefinitely, so
  this phase adds bounded timeouts to those calls and defines the behavior on
  auth failure, rate limiting, and network stall: the `usd_per_merged_pr`
  column is written empty and the rest of the row still records. A network
  failure must not cost the week's corpus-derived numbers.
- `TestPrLink` (test_transcript_analysis.py:2450-2516, three tests) hand-stubs
  `gh` stdout as `json.dumps([{"number": 77}])` with no `state` key. Adding
  `state` to the fetch breaks all three on landing unless the fixtures are
  updated in the same change — named here because the Critical files list
  otherwise mentions only the new test class.

### Phase 3 — staleness nudge

A fourth `UserPromptSubmit` nudge hook, `nudge-cost-ledger-stale.sh`, modeled
directly on `nudge-error-mode-analysis.sh`: opt-in sentinel, calls
`transcript-analysis.py`, emits `additionalContext`, one-shot per session via
`$CONFIG_DIR/.cost-ledger-nudge-fired.d/$SESSION_ID` with the same inline
30-day `find -mtime +30 -delete` self-sweep all three existing nudges adopted
in #571. Exits 0 on every path and omits `set -e`, inheriting its model's
documented rationale — the `|| true` fail-open guards are load-bearing, and
this hook has more failure surfaces than its model.

**The hook is cwd-relative and only fires inside the claude-config
checkout.** Its first act is `git -C "$CWD" rev-parse --show-toplevel`,
exactly as `nudge-worktree-anchor.sh` does, followed by a cheap identity check
that this is the repo carrying `docs/cost-ledger.md`; anything else exits
silently before any further work.

An earlier draft made the hook's subject the claude-config clone *wherever it
lives*, reachable from any session in any repo. That forced four compounding
defenses — resolving the physical clone by reading back through the stow
symlink, capping the cross-repo git call, an early-exit identity check, and
fail-open handling for a ledger that might not be there — each closing a gap
the previous one opened. The mismatch generating them was scope: a hook whose
subject is one repo's file has no business running work on every prompt in
every unrelated repo. Making it cwd-relative dissolves all four at once, and
costs nothing real, because workflow changes are authored in claude-config
sessions, which is exactly where the reminder is actionable.

**One parser, not three.** The ledger's format is read by `cost-ledger`'s read
mode, by the round-trip test, and by this hook. A bash re-implementation of
markdown-table parsing would drift from the Python one on the first schema
change. The hook therefore shells out to
`transcript-analysis.py cost-ledger --last-row-date`, a mode that prints one
ISO date or nothing — so the file has exactly one parser and the hook's
dependency on the schema is a single documented line of output.

**Bounded, like every sibling.** Each existing nudge caps its work
(`timeout 2` on `tail|jq`; `timeout 10` on `friction-count`; `_lib_capped`
around git calls). This hook's two calls — the `--last-row-date` invocation and
the `git log` commit count — are wrapped the same way, and the plan's own
Verification requires the per-prompt cost to be measured before merge rather
than assumed from `--record`'s ~72s, which is a different code path entirely.

**Fails open on a missing or empty ledger.** If `docs/cost-ledger.md` is absent
or has no data rows — Phase 3 merged before any machine has recorded, or Phase
1 reverted while Phase 3 stayed — the hook exits silently. It never emits a
traceback into prompt submission.

**Testable clock.** Both fire conditions are date arithmetic, and no existing
nudge does date arithmetic, so there is no convention to inherit. The hook
reads its reference date through an override the tests set, mirroring the
`cmd_* → _*_report(args, today)` injection the Python side already uses, so
the 7-day and 10-commit boundaries are both reachable in a test.

**The hook nudges; it never records.** A hook that writes to a git-tracked
file on prompt submission would dirty the working tree of every stow consumer
who enabled it, mid-session, with no review. The human decides whether a week
is worth recording and what the note says — which is also the only way the
`note` column gets a meaningful value, since no automated trigger can judge
"major workflow change."

**Fire condition** — both must hold:

1. The newest ledger row is more than 7 days old.
2. At least 10 commits touching `claude/.claude/**` have landed on the
   default branch since that row's date.

Grounded in measured rates: 84 commits touched `claude/.claude/**` on `main`
in the 30 days to 2026-08-09, a mean of 2.8/day. Condition 2 at 10 commits is
therefore ~3.5 days of normal activity — it does not gate during active
periods, and it suppresses the nudge entirely during idle ones, which is the
failure mode a bare 7-day timer has. Condition 1 sets the cadence at roughly
weekly, matching the ISO-week bucketing the row schema already uses.

**Rejected: recording on every workflow-affecting merge.** The user's initial
framing — record after workflow changes rather than on a clock — is right
about *what* should anchor a row, but at 2.8 workflow commits per day it
produces ~3 rows/day, each costing a 25.9s corpus scan, and a ledger too noisy
to read. The staleness-plus-activity pair keeps the event character (it only
fires when work actually happened) without the volume.

**Lighter primitives considered, and why each fails.** A new hook is the
heaviest mechanism in this plan — a new file, a `settings.json` entry, a new
sentinel, and per-session state, all shipped to every stow consumer. Three
lighter options exist and none of them works:

1. **No automation — a documented command plus a routing-table row.** The
   lightest possible answer: the engineer records a row when they change the
   workflow. Fails on the evidence of this very issue: GH-554 carried an
   explicit written re-run caveat in its own Assumptions section from
   2026-08-03, and the re-run happened only when the engineer asked about it
   on 2026-08-09 — a written reminder inside the artifact itself did not
   produce the action.
2. **A line in `/ready-for-review`.** Attractive because that skill already
   runs at the moment a workflow change ships, making it event-triggered by
   construction with no new file at all. Fails against this repo's own rule
   (root `CLAUDE.md`, "Should this be a hook?"): "The harness executes hooks;
   nothing in memory or a CLAUDE.md prose rule can fulfill an automatic-trigger
   request." A skill-body line is advisory text a session can skip; the ask
   here is for something that fires whether or not anyone remembers.
3. **Extend `check-branch-divergence.sh`.** A `SessionStart` hook that already
   compares the branch to `origin/<default>` and counts commits behind, so
   condition 2's machinery is nearly there. Fails because its subject is *this
   branch's* staleness — a per-branch concern — and folding a repo-wide ledger
   check into it makes one hook answer two questions with different opt-in
   states and different fire conditions.

**Rejected: cron.** Heavier, not lighter, and worse on the merits. It requires
a daemon and machine state this repo installs nothing else through; it fires
only if the laptop is awake, so the series develops holes exactly during the
away periods where a quiet week would be informative; and it cannot see the
git state that condition 2 reads.

### Assumption ledger

**Root problem:** transcript-derived cost history is destroyed by transcript
turnover, is silently invalidated by price-table updates, and carries no record
of what changed — so no workflow change made now can be scored later.

**Givens** (conditions the design treats as fixed, each beyond its reach):

| # | Given | Why it is a given |
|---|---|---|
| G1 | Transcript files are the only source of historical cost data; there is no billing API this tooling can read | Vendor surface; the platform exposes usage in transcripts, not through a queryable local history |
| G2 | Prices are list prices, not the operator's actual billed rate | Subscription terms are not visible to this tooling; GH-554 already accepted this, on the grounds that ranking is price-ratio-invariant |
| G3 | `claude/` ships to every stow consumer on `git pull` | Stow's distribution model; the recorder must live in `transcript-analysis.py` to reuse its pricing and scan code, and that file is stowed |

Per-machine corpus isolation is deliberately **not** listed as a given.
Syncing transcripts between machines is within this plan's reach — it declines
to, and that decision belongs in Out of scope with its reason, not in a table
of conditions the design cannot touch.

**Mechanisms** (each anchored to what it addresses):

| Mechanism | Anchors | Justification |
|---|---|---|
| Append-only committed markdown ledger | root | The minimum durable store; git supplies versioning, review, and multi-machine merge with no new format or sync mechanism |
| `machine` column | root | Makes cross-machine rows distinguishable without centralizing any transcript, and without publishing a corpus digest |
| `rates` column | root | The only defense against a price-table update silently re-basing the series |
| Opt-in sentinel | G3 | Nothing writes on any stow consumer's machine until they opt in |
| `UserPromptSubmit` nudge | root | Lighter than cron (see rejections above); three existing instances to model on |
| Nudge emits, never writes | G3 | A hook that dirties a tracked file mid-session is the invasive version of the same idea |

**Over-powered-primitive check** — the two heavier candidates considered and
why each fails, both anchored to the nudge row: **cron** (needs a daemon,
misses sleeping machines, blind to git state) and **a CI job on merge to main**
(CI has no access to the local transcripts the metrics are computed from, so
it cannot produce a row at all).

**Assumptions:**

| # | Assumption | Tag |
|---|---|---|
| A1 | The three source subcommands run in 25.9s (`cost-trend`), 19.0s (`review-trace --deny-summary`), and 27.6s (`reviewer-yield`) over the current corpus — ~72s for a full row, cheap enough that no column needs to be optional | `[verified: timed runs, this session]` |
| A2 | 84 commits touched `claude/.claude/**` on main in the 30d to 2026-08-09 (2.8/day), grounding both fire-condition thresholds | `[verified: git log --since=2026-07-10, this session]` |
| A3 | The oldest transcript on this machine is dated 2026-06-24 (46-day span, 2,431 files), so the corpus is finite and older history is already unrecoverable | `[verified: filesystem probe, this session]` |
| A4 | Claude Code deletes transcripts at startup once older than `cleanupPeriodDays`, default 30 days, minimum 1, with no "keep forever" value — so unrecorded weeks become permanently unrecoverable | `[verified: code.claude.com/docs/en/claude-directory, and the Available settings section of code.claude.com/docs/en/settings]` |
| A5 | `_corpus_fingerprint` requires a redact map, and `_build_redact_map` scans every project's transcripts machine-wide regardless of the caller's scope flags — one of the two reasons the fingerprint column was dropped rather than wired in | `[verified: transcript-analysis.py:3743-3751 and 3683-3729; cost-trend builds no redact map]` |
| A6 | A markdown table is parseable enough to serve as the nudge's staleness input, given a strict column contract and a tested parser | `[unverified]` |
| A7 | The engineer wants the claude-config half only; the related cross-project time analysis living in a separate private repo is planned separately | `[engineer-verified]` |
| A8 | `pr-link` cannot currently distinguish MERGED from OPEN/CLOSED, so Phase 2 requires adding `state` to its `gh` fetch | `[verified: transcript-analysis.py:3360-3364]` |
| A9 | The ledger is `docs/cost-ledger.md`, committed with its header in this change and never lazily created; `--machine-label` is required, matches `^[a-z0-9]{1,8}$`, and is rejected when it equals the machine's hostname | `[engineer-verified: disclosure rules for this public repo]` |
| A10 | Recording the same (week, machine) twice is refused without `--force`, because a week's figures change as the week fills | `[verified: cost-trend labels the current ISO week "(partial)"]` |
| A11 | A staged ledger row is already covered by `deny-private-project-refs.sh`'s existing blocklist scan, so the recorder needs no scan of its own | `[verified: deny-private-project-refs.sh:454 scans git diff --cached; :710 denies on blocklist hits in staged content]` |

**A4 note — an unexplained observation, recorded rather than resolved.**
`cleanupPeriodDays` is set in neither the stowed settings file nor its
gitignored machine-scope sibling, so the documented 30-day default applies; yet the oldest
transcript on this machine has a 46-day-old mtime. The documentation does not
state what the retention clock measures (mtime, session start, or last
access), and it documents one way the sweep can stall: a settings file that
fails to parse pauses the cleanup sweep until fixed. Neither explanation is
confirmed here. This does not change the plan — the documented behavior is
deletion with no opt-out, so the ledger's premise holds either way — but it
does mean the current 46-day window is *not* a retention guarantee to design
against, and it is worth checking `/status` for a paused-sweep warning
independently of this work.

## Critical files

**Create:**

- `claude/.claude/hooks/nudge-cost-ledger-stale.sh` — Phase 3. Model on
  `nudge-error-mode-analysis.sh` (opt-in gate, `transcript-analysis.py` call,
  `additionalContext` envelope, one-shot marker + self-sweep). Reuse
  `_lib_config_dir` and `_lib_valid_session_id_component` (`_lib.sh:694-697`)
  rather than re-deriving either.
- `claude/.claude/hooks/tests/test_nudge_cost_ledger_stale.sh` → `.py` — the
  `_run`/`_context` subprocess pattern from `test_nudge_worktree_anchor.py:28-113`,
  with the `isolated_home` fixture from `hooks/tests/conftest.py:58-68`.
- `docs/cost-ledger.md` — one file serving both roles: the ledger data table
  and the sentinel's `docs-anchor` (`install.sh:198-214`). Committed in this
  change with its schema preamble and header row already present, so
  `--record` appends to an existing file and never creates one.

**Modify:**

- `claude/.claude/scripts/transcript-analysis.py` — `cmd_cost_ledger` +
  `_cost_ledger_report`, following the `cmd_*` → `_*_report(args, today, ...)`
  split both `cmd_cost` (4438) and `cmd_cost_trend` (5372) use so the wall
  clock is read once at the CLI boundary and tests stay deterministic.
  Register via `sub.add_parser` + `_add_project_scope_args` + `set_defaults`
  in `build_parser()` (6390). **Reuse, do not reimplement:**
  `_resolve_project_scope(..., include_subagents=True)` (2323), `_price_turn`
  (4107), `_context_bucket`, `_fam`. Also **extract**
  `_compute_cost_trend_data(session_iter) -> dict[str, dict[str, float]]` from
  `_cost_trend_report` (5383-5453, currently returns `None`) and call it from
  both the printer and the row builder — without the extraction, "recorder
  matches `cost-trend`" is two implementations kept in sync by discipline.
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — a
  `TestCostLedger` class using `_priced` (4347) and the `fake_projects`
  fixture (221-227); `_table_cols` (53) for output assertions. Phase 2 also
  updates `TestPrLink`'s three `gh`-stdout fixtures (2450-2516) to carry the
  new `state` key.
- `claude/.claude/settings.json` — one `UserPromptSubmit` hook entry appended
  to the existing three-command group.
- `install.sh` — one `SENTINEL_INVENTORY` row, `machine` scope.
- `docs/transcript-analysis.md` — a `##` section in parser-registration order,
  following the fixed Purpose / Flags / Sample output / When to reach for it
  template.
- `claude/.claude/skills/transcript-analysis/SKILL.md` — one routing-table row
  (10-30) and a Caveats line (59-73) for the write side effect.
- `docs/hooks.md`, `README.md` — the new hook and the new sentinel.

## Verification

- `../../../.venv/bin/pytest claude/.claude/` — full suite. Two failures are
  known-pre-existing on this machine:
  `test_shellcheck.py::TestGateActuallyBites::test_xargs_zero_composition_exits_nonzero_on_empty_input`
  and
  `test_enforce_marker_script_shape.py::TestGateReleaseAuthorityFileWrites::test_large_write_cost_stays_near_the_parse_floor`.
  Reproduce both against a throwaway worktree at the merge base. **Decision
  rule:** reproduces at baseline → pre-existing, proceed; does not reproduce →
  this branch caused it and it is in scope, fix before merge, even though
  neither file is one this change touches.
- `../../../.venv/bin/ruff check claude/.claude/` and
  `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`.
- **Parity test** (recorder vs. instrument): at a frozen `today`, assert the
  recorder's computed row dict equals `_compute_cost_trend_data`'s entry for
  that week. No markdown involved. This is the test that catches drift.
- **Serialization test** (I/O fidelity): serialize an in-memory row, parse it
  back, assert equality. No dependence on `cost-trend` at all. Kept separate
  from the parity test so a failure says which of the two broke — the existing
  `TestCostTrend` convention of pinning a fixed date (e.g. `date(2099, 1, 1)`)
  applies to both.
- **Parser hostility:** feed the canonical parser a wrong column count, a
  non-ISO week label, a non-numeric percentage, a `|` inside `note`, and git
  merge-conflict markers; assert a defined loud failure in each case, never a
  silent misparse.
- **Idempotence and correction:** a second `--record` for the same (week,
  machine) is refused without `--force` and leaves the file byte-identical;
  *with* `--force` it replaces that row in place — exactly one row for the key
  afterward, adjacent rows untouched.
- **Degenerate corpora:** empty corpus, and a week whose every turn is on an
  unpriced model — both refuse and write nothing.
- **Concurrency:** two `--record` processes racing produce one row, not two
  and not a corrupt table.
- **Nudge branch coverage:** silent outside the claude-config checkout; silent
  when the sentinel is absent; silent when the ledger file is missing or has no
  data rows; silent when the newest row is fresh; silent when the row is stale
  but fewer than 10 workflow commits landed; emits when both conditions hold;
  emits at most once per session. Both boundaries (exactly 7 days, exactly 10
  commits) exercised through the injectable reference date.
- **Nudge latency:** measure the hook's actual per-prompt cost inside a
  claude-config session and state it, rather than inheriting `--record`'s ~72s
  figure, which is a different code path.
- **Publish safety:** a SENTINEL-path fixture asserting no project label,
  session id, or path reaches the ledger file or stdout — the same construction
  #601 used for the cited-path join. Extended to the two new adversarial
  inputs: a `--machine-label` equal to the real hostname, and a `--note`
  containing a blocklisted term. Both must be refused.
- **Sentinel negative test:** `--record` refuses to write when the sentinel is
  absent — not merely that read mode stays quiet.
- **Live run:** record one real row, inspect the diff, confirm it carries the
  rate-table date and no fingerprint.

## Out of scope

- **Cross-project time analysis in a separate private repo.**
  Engineer-selected for a separate plan. That tooling reads engagement data
  under different disclosure rules, and it is engagement-scoped by
  construction — each run takes a project scope, a date window, and a
  per-engagement allowlist — so "regular across projects" is a new entry point
  there, not a scheduling change here.
- **Backfilling weeks older than the current corpus.** Not recoverable; the
  ledger starts where the transcripts do.
- **`cost-trend --config-dir`.** `cost-trend` has no multi-account flag, so
  ledger rows are single-account on a multi-account machine. Real limitation,
  separately ticketable, and orthogonal to whether a ledger exists.
- **Syncing transcripts between machines.** Within reach — rsync or any shared
  store would give one machine the whole corpus and make the `machine` column
  unnecessary. Declined: it moves raw transcripts, which carry project names,
  paths, and session content, off the machine that produced them, for a
  benefit (one row instead of two per week) that the `machine` column already
  delivers at no disclosure cost.
- **Normalizing `denials` by a turn denominator.** `review-trace
  --deny-summary` emits counts and a window, not a per-turn base, so
  denials-per-1k-turns needs a new denominator in that subcommand. Worth doing
  and separately ticketable; the raw count is honest in the meantime as long as
  it is read alongside the percentage columns rather than alone.
- **Closing GH-500.** Its subject — inconsistent per-session cleanup across the
  three nudge hooks — was resolved by #571's self-sweep conversion; the issue
  is merely still open. Worth closing, but not by this branch.
