# Close out GH-554's remaining children: denial-friction census (F3) and reviewer-yield diff correlation (F4)

## Context

**Goal:** close GH-557 and GH-558, the two still-open children of the GH-554
workflow-efficiency audit, and record on GH-554 which merged PRs already closed
the other two.

GH-554 filed four findings. Two are done and need only bookkeeping: F1 (GH-555)
was closed by PR #569 (`Closes #555`), which price-weighted `audit-routing`'s
headline and added `cost-trend` on top of the `cost` subcommand from PR #552;
F2 (GH-556) was closed by PR #561 (`closes GH-556`), which replaced the flat
120,000-token handoff threshold with 60% of a per-model resolved context window,
with PR #566 following up to fire the same hook on `Stop`.

The other two are partly done, and PR #569's own body says so — it shipped
"tooling only" for F3 and "verdict-level join only" for F4. Measuring what
landed shows the F3 half cannot produce the census it was built to enable:

- `review-trace --deny-summary` sorts **545 of 777 denials (70%) into `other`**
  on the command-shape axis; `_DENIAL_COMMAND_SHAPES` is three hardcoded git
  strings, so none of GH-557's own categories can be counted.
- **163 (21%)** land in `unmatched` on the hook axis, because the marker-shape
  hook's wording (`marker.sh invocation denied …`) names no hook and
  `_DENIAL_HOOK_NAME_RE` reads only the `blocked by <name> hook|gate` idiom.
- The two tables are **independent marginals**. GH-557's categories are a *cross*
  of the two axes — "worktree-enforcement, other git/bash shapes" cannot be
  separated from permission-layer `git commit` denials by two marginal rows.
- `hook_denial_key`'s docstring states current transcripts carry "no structured
  marker" for a denial. That premise is **stale**: records carry `toolDenialKind`.

**Intended outcome:** `review-trace --deny-summary` produces GH-557's cross-cut
census directly; whatever avoidable denial shapes it surfaces that are safely
fixable by an exact-match `permissions.allow` rule are fixed, with anything
needing a hook or skill-prescription change instead filed and named rather than
silently left; and `reviewer-yield` reports a *discriminating* signal for
whether a reviewer dispatch changed anything, not just whether it returned a
verdict.

Both findings now touch `transcript-analysis.py`, but they close independently,
so they ship as **two PRs off two branches** — Part B branched from Part A's
merge commit, not from the same base (they collide in the test file, the doc, and
adjacent `SKILL.md` routing rows, not in the script itself).

## Approach

### Part 0 — bookkeeping (no code)

Comment on GH-554 recording F1→#569/#552 and F2→#561/#566, and that F3/F4 remain
open with #569 covering only their tooling halves. Comment on GH-557 and GH-558
naming what #569 did and did not close. File five follow-up issues, since five
Out-of-scope items below defer to them: `_lib_emit_deny` self-identification;
structured reviewer findings output; the `marker.sh clear-stale` gap (folding in
the env-prefixed form A4 also excludes); unchaining `code-review/SKILL.md:390`'s
prescribed `write && git commit` form; and a catch-all for any other
hook-accepted-shape widening the A4 census surfaces (A5 is bounded to
`permissions.allow` only — see A5). No issue is closed here.

**Part A's PR body is the closing artifact for GH-557 and must show its work.**
A5 may ship zero new `permissions.allow` rules — a spec-legitimate outcome,
since GH-557 asks for "a census, not a verdict" — but a merged PR with no
visible classification would recreate the exact "tooling only, no visible
result" gap GH-554 filed against #569. The PR body carries the hook×shape
cross-tab, the explicit classification of both named categories, and — for
every shape classified avoidable but not fixed — the follow-up issue number
carrying it. If A5 ships zero rules, the PR body says so explicitly with each
candidate's specific reason, not merely `Closes #557` on a tooling-only diff.

### Part A — GH-557: denial census and the avoidable shapes it surfaces

**A1. Ground detection in `toolDenialKind`, and state exactly what it does and
does not discriminate.** `hook_denial_key` itself is **untouched** — no new
parameter, no changed signature. Its two callers in `cmd_review_trace`
(`:1047`, `:1087`) and both calls inside `friction-count` (`:3630`, `:3643`)
keep classifying gate denials exactly as today, which is what makes the
no-drift claim on `friction-count` verifiable by inspection rather than by
argument. The non-gate `toolDenialKind` classification is a **new, separate**
check — `_is_nongate_friction_kind(tool_denial_kind, already_gate_denied)` —
called only inside `cmd_review_trace`'s `user`-record arm, never inside
`hook_denial_key` and never inside `friction-count`. A falsy `toolDenialKind`
means absent (measured: the field is never empty or null, and never appears on
the `tool_result` block, only on the parent `user` record).

**`toolDenialKind` is the friction-class axis, not the gate-vs-allowlist axis.**
Measured over `review-trace`'s own scope (top-level session files, main thread):
860 `permission-rule` records split 601 signature-matching *with* an extractable
hook name, 155 signature-matching *without* one, and 104 not signature-matching
at all. That last group is the permission-layer denial class — plain
`permissions.allow` misses and third-party-hook wordings. So `permission-rule`
covers **both** PreToolUse-hook denials and allowlist misses, and GH-557's two
census categories live *inside* it, recoverable only from message text. The plan
records this explicitly because the earlier draft asserted the opposite: the
census's "avoidable allowlist gap" figure is an **upper bound**, bounded by A2's
label coverage. The four non-gate kinds (`user-rejected` 65,
`automode-blocked` 23, `automode-unavailable` 10, `interrupted` 8) are counted
and printed on their own line rather than silently dropped.

**Which surfaces broaden, and the actual mechanism.** `review-trace` has four
denial surfaces — timeline `denial` events, the per-session `denials=N` header,
the `--deny-only` session filter, and the `--deny-summary` tables — but they all
read one shared `events` list built from one call site (the `user`-record arm at
`:1087`). "Opt-in at the call site" cannot separate them: turning the kind
parameter on there broadens all four at once, off broadens none. The real
mechanism is a **new, separate event kind**. The four non-gate `toolDenialKind`
values never produce a `{"kind": "denial", ...}` event — that classification,
and therefore `has_denial`, `denials=N`, and the timeline's rendered `denial`
lines, are **completely unchanged** from today, verifiable by inspection since
`hook_denial_key` itself is untouched. Instead, the same `user`-record arm
additionally emits `{"kind": "friction", "friction_kind": tool_denial_kind, ...}`
for a non-gate value with no signature match. `friction_kind` is printed from
the closed four-value enumeration (`user-rejected`, `automode-blocked`,
`automode-unavailable`, `interrupted`) with an `other-kind` fallback bucket for
any future value, never the raw field echoed verbatim — the one new printed
field this design adds inherits the same bounded-vocabulary discipline A2/A3
apply everywhere else, even though `toolDenialKind` is harness-controlled and
not attacker-influenced today. `--deny-summary` tallies `friction` events into
their own four-line breakout; the other three surfaces never look at
`kind == "friction"` at all.

**The `--deny-only` interaction this creates.** `--deny-only`'s per-session skip
(`if deny_only and not has_denial: continue`) tests only `kind == "denial"`
presence — unchanged. Run together with `--deny-summary` (as the census
invocation does), a session whose only events are `friction` would be dropped
before the summary ever tallies them, undercounting the four non-gate lines.
Fix: the friction tally accumulates from the full per-session `events` list
**before** the `deny_only` skip is applied, not after — `--deny-summary`'s
friction counts are therefore independent of whether `--deny-only` is also
passed, while `--deny-only` alone (no `--deny-summary`) keeps its documented,
unchanged meaning for the two published case studies that already depend on it.
`deny_only` is the **only** filter friction bypasses — `friction` events go
through every other per-session filter (`--since`/`--until`, `--branches`,
project scope) exactly like `denial` events; nothing else in the loop is
special-cased for them.

**A friction-only session is not silently invisible in default output.**
`review-trace`'s default (no `--deny-summary`) per-session view renders a
timeline; today an event kind with no matching render arm is dropped from that
timeline with no trace. A `friction` event gets its own rendered timeline line,
distinct in wording from a `denial` line, so a friction-only session still shows
something rather than a bare `denials=0` header with nothing under it.
`has_denial`, `denials=N`, and `--deny-only`'s session-selection semantics stay
`denial`-kind-only, per the paragraph above — only the *rendering* gains a
`friction` arm. Separately, `--deny-summary`'s own "No denials found in scope."
suppression (today gated on `sum(hook_counts.values())`) is gated on **either**
table having a nonzero total, so a friction-only scope still prints its
breakout instead of being told nothing was found. Friction events use their own
dedup set, never `seen_denial_ids` — sharing it would let a friction event
suppress a later legitimate `attachment`-shape denial that happens to share a
`tool_use_id`, moving `denials=N`, which this design's whole point is to leave
alone.

`interrupted` (`[Request interrupted by user for tool use]`) is not a denial in
any sense and, under this design, never becomes a `denial` event — only ever a
`friction` one.

**Blast radius.** `hook_denial_key` has three direct callers, but a **fourth
consumer**: `TestFrictionCountCrossPathEquality::test_denial_count_matches_review_trace`
(`tests/test_transcript_analysis.py:6014`) asserts `friction-count`'s denial
count *equals* `review-trace`'s `denial`-kind count, with a docstring saying they
"must never silently drift." The new `friction` event kind intentionally makes
`review-trace` count more than `friction-count` does — `review-trace` now reports
both `denial` and `friction` events; `friction-count` only ever sees `denial`
events, unchanged. Today's fixture predates `toolDenialKind` and carries no
`friction`-eligible record, so it would pass by accident while its stated
invariant became false. **Resolution, not accident:** narrow that test's
docstring to state explicitly that the fixture carries no `toolDenialKind` and
the equality holds only on the text-signature (`denial`-only) path, and add a
sibling divergence test — a record with a non-gate `toolDenialKind` **and**
non-signature-matching text — asserting `review-trace`'s non-gate line counts it
while `friction-count` does not. `friction-count` feeds
`nudge-error-mode-analysis.sh`, but that hook is **dormant unless the consumer
opts in** with `.error-mode-nudge-enabled` — so the exposure is "armed consumers
see one extra advisory, non-blocking nudge," not "every stow consumer," which is
how the earlier draft overstated it.

**A2. Cover the deny wordings hooks actually emit — by enumerated label, not free
capture.** Two gaps are measured: `<thing> invocation denied (…)` and
`<name> gate: …` both fail today's single regex. The enumeration source is **not**
`claude/.claude/hooks/*.sh` basenames — every hook emits a hand-written prose
label (`credential-file read gate`, `routing-read gate`,
`marker-script-shape gate`) that matches no script filename, so matching against
basenames would recognize nothing and an implementer's path of least resistance
would be a free capture, the exact risk this item exists to close. The real
enumeration is that **hand-maintained set of emitted prose labels**, pinned by a
test that drives each hook's actual deny paths and asserts the emitted label is
in the set — so the set is caught drifting, not silently stale, when a hook's
wording changes or a hook is added. This applies to **both** denial shapes: the
legacy `attachment` branch (`_denial_hook_label`'s first branch, `:877`, which
today returns `att["hookName"]` verbatim with no bound) gets the same
enumeration and length cap as the regex-extracted branch — otherwise it stays the
one path this item doesn't actually close. A1 admits 104 previously-invisible
messages that embed the raw denied command, and several gate hooks emit
`Read of '<interpolated path>' denied by the … gate:`, so an unbounded capture
would print a credential-file or PII-file path. Every pattern also inherits
`_DENIAL_HOOK_NAME_RE`'s existing discipline verbatim — name-shaped character
class plus `_DENIAL_HOOK_NAME_MAX_CHARS` — and every capture must *precede* a
static anchor. Unrecognized wordings still fall to `unmatched`, and that count
stays printed.

**A3. Replace the three hardcoded shapes with an allowlist, not a sanitizer.**
Normalize then match against an enumerated shape set; anything unrecognized
becomes `other`. Normalization must, in order: strip `NAME=VALUE` environment
assignments from the front (one such prefix in the corpus carries a live token),
basename the first token (an absolute script path is a home-rooted path, one of
the repo's six always-on structural detectors), and **drop the values of a named
set of value-taking flags** (`-C`, `--git-dir`, `--work-tree` as separate-token
forms; `-c`, `--git-dir=`, `--work-tree=` as `=`-attached forms, which carry no
following token to drop) rather than merely "skipping" flag tokens — skipping
without dropping the value still promotes it into the subcommand slot
(`git -C <path> commit` → `commit` is at index 2, not 1, so a naive skip reads
`<path>` as the subcommand). The two forms are handled differently by
construction: a separate-token flag consumes the next token whole; an
`=`-attached flag consumes nothing further, since the value is already inside
the flag's own token. Since `-C` is `require-worktree-for-git-writes.sh`'s own
resolution mechanism for compliant worktree writes, this is the dominant form
for the worktree-enforcement category GH-557 asks about — mishandling it would
silently reproduce the 70% `other` problem A3 exists to fix, for exactly the
category the census most needs. Only after dropping flag values does the
classifier take command plus one subcommand
level for the multiplexers that dominate the corpus (`git`, `gh`, the marker
script). The earlier draft's "character-class-restricted and length-capped"
bound was aimed at *arguments*; the leak is in the token slots themselves.
`review-trace` has no `--redact` flag, so this is the only control. An empty
command (no paired tool_use) keeps its existing `other` bucket —
`test_deny_summary_unmatched_hook_name_bucketed_not_dropped` pins that, and a new
fixture pins `git -C <path> commit` bucketing as `git commit`.

**A4. Cross-tab the axes, then run the census.** Add a hook×command-shape
cross-tab to `--deny-summary`; two marginals cannot express GH-557's categories.
Then classify **GH-557's two named categories** — `marker.sh` ops and
worktree-enforcement/other-git — as "correctly caught an unsafe shape" or
"avoidable." Not "the two largest categories": after A3's regrouping the top two
by raw count may well be `git commit` and `git checkout`, both correctly-caught
by design, which would walk past the ticket. The census must classify the
marker.sh category **by invocation form**, because its 123 denials are *not*
missing-op denials — `write`, `activate`, and `deactivate` already have twelve
exact-match rules. `status` is not a subcommand at all, so those denials are
correctly caught. The census reports its window and excludes pre-`toolDenialKind`
records from the kind breakdown — the field first appears 2026-07-20 and the
corpus starts 2026-06-24, so two detection regimes exist.

**A4 must state each candidate form's outcome against A5's admission test
before A5 runs**, because two of the three forms this plan originally proposed
as A5 candidates are themselves disqualified by A5's own criteria:

| Form | Outcome | Reason |
|---|---|---|
| Chained `marker.sh write code-review && git commit …` (`code-review/SKILL.md:390`) | Not an A5 candidate | No exact-match rule can express a chain without a glob over the commit message, which the repo's no-globs rule forbids and which would pre-approve arbitrary trailing shell. The actual fix is unchaining the *prescription* in `code-review/SKILL.md` — a change to a high-traffic skill's documented workflow, deliberately not made inside this efficiency-audit plan. Filed as its own follow-up issue. |
| Env-prefixed `CLAUDE_CONFIG_DIR=… marker.sh write …` | Not an A5 candidate | `enforce-marker-script-shape.sh`'s Stage 2 intentionally fast-exits wrapped/env-prefixed forms with the comment "`permissions.allow` is their gate" — so this form has **no hook-level shape validation at all**, and granting it removes the only remaining control on top of an already-ungated form. It is also machine-specific (the env value varies per consumer), so it could never be a portable stowed rule regardless. Folded into the `clear-stale` follow-up issue, which already carries this exact "unscoped, bypasses the shape hook" reasoning. |
| Absolute-path invocation (the twelve existing rules are tilde-only) | Not an A5 candidate | A machine-specific home-rooted path in a *stowed* `settings.json` is non-portable across consumers and is one of the repo's own six always-on redaction-hook triggers — shipping it would flag the repo's own config file. |

**A5's honest scope: this plan may ship zero new `permissions.allow` rules for
the marker.sh category.** All three candidates identified so far are excluded —
only the env-prefixed form fails A5's test (iii) directly; the chained form is
excluded by the repo's own no-globs rule (inexpressible as an exact match) and
the absolute-path form by portability and the repo's own redaction-hook
triggers, independent of test (iii). A5 ships a rule only if A4's live run
surfaces some other shape that passes all three, not for any candidate named
above.

**A5. Fix the avoidable shapes, bounded by a stated test.** A rule ships only for
a shape that (i) a skill body or `CLAUDE.md` prescribes, (ii)
`enforce-marker-script-shape.sh` already accepts, and (iii) carries no
gate-release power **and** is not already gated solely by `permissions.allow`
(i.e., is not one of the fast-exited wrapped/env-prefixed forms). Exact-match
only, no globs, mirroring the twelve existing rules. The `settings.json` change
is **its own commit**, so the permission diff is reviewable and revertible apart
from several hundred lines of Python, and `/review-permissions` runs against it
in isolation. Anything needing a *hook* accepted-shape change is deferred and
routed through `claude-hook-review` — A5 touches `permissions.allow` only; any
census-surfaced shape needing a hook change is filed to the catch-all follow-up
issue named in Part 0.

**`marker.sh clear-stale` is deliberately NOT granted here**, reversing the
earlier draft. It is a genuine prescription/allowlist gap — `CLAUDE.md`
prescribes it, the hook accepts it, no rule covers it — but it fails test (iii)
and is not in GH-557's data. `clear-stale` sweeps **every** `.*-active.d` entry
with a non-live PID across **all** sessions machine-wide, while the wedge it
fixes is session-local. The marker-shape hook denies `write|activate` for
no-gate-release agents but has no arm for `clear-stale`, so the permission prompt
is currently the only control keeping a reviewer or `code-writer` subagent from
mutating machine-wide bypass state. And the "eviction only re-arms gates" premise
is false for at least one consumer: `require-routing-read.sh` reads
`.plan-review-active.d` with a bare `[ -f ]` and no liveness check, where
presence turns enforcement **on** — so eviction *disables* that gate. Granting it
unprompted is an over-powered primitive when `marker.sh deactivate <skill>`
(already allowlisted, session-scoped) and `_lib_active_bypass_marker_live`'s
existing dead-PID `rm -f` on read both already cover the session-local case.
Filed as its own issue with the two prerequisites: session-scope the sweep, or
add a `clear-stale` arm to the hook's no-gate-release regex.

**Alternatives weighed.** (i) `_lib_emit_deny` self-identifying by deriving its
hook name from `$0`, making the label axis exact. Better foundation and it
dissolves the pattern table, but it changes the deny message every stow consumer
sees and does nothing for the existing transcripts the census must read — filed,
not built. (ii) Skip the tooling and hand-classify from raw transcripts: it does
not rank the remaining categories and leaves the blind detector for the next
census. (iii) Rely on PR #577's forward-going permission-prompt log:
complementary, not a substitute — it records prompts that are *approved*, which
never appear in transcripts at all.

### Part B — GH-558: cited-path edit overlap, with a null control

Extend `reviewer-yield` with a column group recording whether a file the reviewer
**cited** was subsequently edited by the parent session.

**The metric needs a null control, or it measures nothing.** Reviewers cite files
from the diff under review — files the parent is already editing. Raw overlap
will approach ceiling for nearly every dispatch regardless of finding quality,
and a `/plan-review` dispatch cites the plan file the parent then edits, a
guaranteed self-match. So rows are **split by the existing `_REVIEWER_VERDICT_*`
bucket**: `zero-finding` dispatches are the null control, and the reportable
signal is the *spread* between findings-found and zero-finding overlap within an
agent type. Plan-file self-matches and the findings file's own path are excluded
from the cited set. If the buckets do not separate, the metric does not answer
GH-558 and the issue stays open — that is a stated exit condition, not a
disclosure. The earlier draft guarded only the false negative (low extraction
misread as low yield) and missed this false positive entirely.

**The control needs three more specified properties to actually discriminate:**

1. **`unclassified` dispatches are excluded from both arms and reported
   separately**, with their own share of the agent type's total. `_classify_reviewer_verdict`
   returns a third bucket beyond `findings-found`/`zero-finding`; silently folding
   it into either arm would contaminate the comparison, and if `unclassified`
   correlates with output contract (a `findings_path` dispatch that never wrote
   its file, for instance), one agent type could be systematically mis-ranked.
2. **A minimum per-bucket N of 10 gates whether a cell reports a rate at all**,
   fixed here rather than chosen after seeing which cells separate — the same
   tautology the `Cited` floor above was rewritten to avoid applies identically
   to a threshold picked post hoc. 10 is a conventional floor for a two-proportion
   comparison to carry any signal at all; it is not tuned to this corpus. GH-558's
   own dispatch counts show single-digit 30-day volume for some agent types
   (`staff-data-engineer` 5, `staff-analytics-engineer` 6), so at this floor those
   types report "insufficient data (N=k)" rather than a rate, and the printed N
   accompanies every cell so the reader can judge it directly. If **no** agent
   type clears the floor in both buckets, that is reported plainly as "dispatch
   volume, not yield, is the limiting factor" and GH-558 stays open on that
   basis — the same stated-exit-condition treatment as the separation clause
   below, not a silent empty table.
3. **Dispatch position is a named, unresolved confound.** `zero-finding` dispatches
   cluster at end-of-work (the final approving pass), where the parent has often
   stopped editing regardless of what was cited — a spread could reflect dispatch
   position rather than reviewer yield. This plan does not control for it (that
   would need session-position bucketing this metric doesn't otherwise carry); it
   is stated here as a competing explanation the reported spread cannot rule out,
   so a reader doesn't over-read a small spread as a clean yield signal.

**Extraction.** Reviewer agents have two documented contracts: with
`findings_path`, findings go to a file via the Write tool (one H2 per finding,
`file:line` anchor) and only a pointer line returns; without it, findings are
inline with a "File and line" element. Both live in the subagent transcript
`_index_subagent_dispatches` already resolves. Measured: 372 of 1,268 dispatches
(29.3%) pass `findings_path`, but **136 of a 257-dispatch sample (52.9%)** carry
a `Write` tool_use with string content in the subagent transcript — the
structured surface is materially larger than the prompt-substring figure
suggests, and the inline path carries less than the earlier draft assumed.

**Normalization must preserve repo identity, not just strip to a bare relative
path.** 772 of 1,236 parent `Edit`/`Write` `file_path` values (62%) are
worktree-rooted (`…/<repo>/.claude/worktrees/<branch>/claude/…`). A prior
version of this plan specified stripping both the repo root and the worktree
segment, which would normalize `claude/.claude/scripts/x.py` in *any two
different repos* to the same key — making the plan's own "two repos sharing a
relative suffix must **not** match" test case unsatisfiable by construction. The
correct normalization key is `<repo-identity>/<repo-relative-path>`: strip only
the `.claude/worktrees/<branch>/` segment from the middle of a worktree path
(leaving the repo root in place), so a plain repo-root-relative path and a
worktree path from the *same* repo collapse to the same key while two different
repos' identically-named relative paths do not. Strip order is worktree-segment
first, since stripping repo root first would leave `.claude/worktrees/<branch>/…`
dangling with nothing left to remove it. `~` is expanded before matching; macOS's
`/tmp` vs `/private/tmp` aliasing is normalized to one canonical form. A cited
path's repo identity comes from the dispatching subagent's own `cwd` field
(recorded on its transcript records, the same field the parent side's identity
is derived from), so both sides of the join resolve identity the same way; a
citation with no resolvable repo (a path under `~/.claude/plans/`, `/tmp`, or
similar) buckets to a stated "no-repo" identity, which only ever self-collides
and so never spuriously matches an in-repo edit. Ordering
uses the dispatch's paired `tool_result` timestamp; 333 of 338 reviewer
dispatches have one, and the ~1.5% without are excluded from the `Cited`
denominator rather than silently contributing zero.

**The coverage floor is a decision threshold, not a restatement of its own
measurement.** A throwaway spike scores candidate extraction against both output
contracts over the corpus; its result is compared against a floor **fixed before
the spike runs**, with a named abandon action ("below the floor, ship the
verdict-bucket comparison without the cited-path overlap and leave GH-558 open
rather than shipping a low-coverage metric that reads as authoritative"). A
floor set to whatever the spike measures can never fail and is not a real gate —
the earlier draft put this in post-implementation verification with no number at
all, which is exactly the shape of the miss it cites as precedent: #569 assumed
~20% unclassified and measured ~45% only after shipping. The spike's code is
discarded once the floor decision is made; it does not become the shipped
extractor, so a shortcut taken to get a fast measurement doesn't leak into
production parsing.

**Performance framing, corrected.** The earlier draft called this "a materially
bigger scan." It is not: `_last_assistant_text` (`:1864`) already opens and
`json.loads`-es every line of each paired subagent transcript. Part B adds no new
file I/O and no new JSON parsing — only content-block inspection on
already-decoded records. Measured baseline: `reviewer-yield` all-time,
machine-wide is **13.5s** over 354 parent sessions / 1,603 subagent transcripts /
474 MB total scanned, and no hook invokes it. The extraction pattern itself runs
over a narrower ~375 MB subset — reviewer-transcript text only, excluding
non-reviewer subagent transcripts the same scan also reads — of arbitrary
reviewer prose: it must be linear-time with a bounded,
length-capped character class, the idiom `_DENIAL_HOOK_NAME_RE` already
establishes. Post-change wall clock is recorded per PR #552's precedent.

**Redaction.** Output stays aggregate per-agent-type rows; `--redact` remains the
documented no-op it already is. No cited path, session id, or project label
prints. The debugging affordance the coverage work will want — printing
unmatched paths — is deliberately not shipped, because `--redact` would silently
stop being a no-op.

**Alternatives weighed.** (i) Finding-level join, matching each finding to a diff
hunk — what GH-558 literally asks for, but free-text finding-splitting is the
fragile parse that already misfired in #569, and the verdict-bucket split gets a
discriminating signal from a far more stable token. (ii) Dispatch-level with no
path matching: measures session shape, not reviewer yield.

### Assumption ledger

**Root problem:** GH-554's F3 and F4 both stop one step short of an actionable
result — F3's grouping cannot express its own census categories, and F4 measures
whether a reviewer spoke, not whether anything changed.

**Givens** (fixed, outside this plan's reach):

| Given | Reason |
|---|---|
| Transcript record schema, including `toolDenialKind` and `toolUseResult` field shapes | Written by Claude Code; no change in this repo alters what the harness records. |
| `permissions.allow` matches exact command strings, so each accepted shape needs its own rule | Harness matching behavior; no repo artifact changes it. |

Reviewers emitting findings as prose is **not** a given: `claude/.claude/agents/*.md`
are in this repo and Part B could dissolve its dependence on prose parsing by
changing that contract. The plan declines to — see **Out of scope**.

**Mechanisms:**

| Mechanism | Justification | Anchors |
|---|---|---|
| Read `toolDenialKind` rather than broadening the message regex | Structured field already on disk; the regex is the heavier, less exact primitive, and its own docstring wrongly claims the field does not exist | root |
| New `friction` event kind, `hook_denial_key` untouched | Only shape that leaves `friction-count`'s call sites byte-identical and keeps three of four `review-trace` surfaces unmoved — a parameter on a predicate shared by four surfaces can't selectively broaden one | row 4 |
| Enumerated hand-maintained hook-label set, not `hooks/*.sh` basenames | Emitted labels are hand-written prose that matches no script filename; a free capture would print interpolated credential/PII paths from gate messages | row 8 |
| Allowlist-normalize the command shape, dropping (not skipping) value-taking flags | The leak is in the token slots, not the arguments; skipping without dropping promotes a flag's value into the subcommand slot | row 8 |
| Verdict-bucket split as null control, with a minimum-N gate per cell | Cheapest way to make the overlap number discriminating without finding-level parsing; ungated cells at single-digit GH-558 dispatch volumes would print noise as a ranking | row 6 |
| Exact-match `permissions.allow` rules, no globs, own commit, shipped only where A5's three-part test clears | Repo rule; globs widen the flag-injection surface; two of the three original candidates fail test (iii) on inspection | root |

**Assumptions:**

| # | Assumption | Tag |
|---|---|---|
| 1 | F1/GH-555 closed by #569, F2/GH-556 by #561 (+#566) | `[verified: PR bodies state "Closes #555" / "closes GH-556"]` |
| 2 | 777 denials, 545 `other` (70%), 163 `unmatched` (21%) | `[verified: ran review-trace --deny-only --deny-summary, 2026-08-07]` |
| 3 | `permission-rule` splits 601 / 155 / 104 and conflates hook denials with allowlist misses; basis is top-level session files, main thread, matching `review-trace`'s own `include_subagents=False` scope | `[verified: staff-backend-engineer cross-tab of the corpus, 2026-08-07]` |
| 4 | Four consumers of `hook_denial_key`, not three — the fourth is `TestFrictionCountCrossPathEquality` | `[verified: staff-backend-engineer grep + test docstring]` |
| 5 | `toolDenialKind` first appears 2026-07-20; corpus starts 2026-06-24; never empty/null, never on the block | `[verified: corpus scan]` |
| 6 | Reviewers cite files from the diff under review, so raw overlap approaches ceiling without a null control | `[verified: staff-product-engineer, from the reviewer output contracts]` |
| 7 | 62% of parent `Edit`/`Write` paths are worktree-rooted; 333/338 dispatches have a timestamped paired `tool_result` | `[verified: staff-backend-engineer corpus measurement]` |
| 8 | `clear-stale` is unscoped machine-wide, ungated for no-gate-release agents, and `require-routing-read.sh` has inverted marker polarity | `[verified: ciso-reviewer read of marker.sh, enforce-marker-script-shape.sh, require-routing-read.sh]` |
| 9 | `reviewer-yield` baseline 13.5s; `_last_assistant_text` already reads every subagent line, so Part B adds no new I/O | `[verified: staff-platform-engineer measurement]` |
| 10 | `nudge-error-mode-analysis.sh` is dormant absent `.error-mode-nudge-enabled` | `[verified: hook source]` |
| 11 | Corpus figures are **all-time local history**, not GH-554's trailing-30-day window; they must not be presented as restating the issue's numbers, and must be re-derived at PR-write time | `[verified: no --since applied]` |
| 12 | The `Cited` extraction floor is not yet known | `[unverified]` — a numeric floor is fixed *before* the pre-implementation spike runs, with a stated abandon action, so the spike's result is judged against it rather than becoming it |
| 13 | Scope is both findings, two PRs; F3 goes tooling→census→fixes; F4 uses cited-path overlap | `[engineer-verified]` |
| 14 | The env-prefixed `marker.sh write` form is a gate-release op that fails A5's own admission test (iii) directly; the chained and absolute-path forms are excluded on independent exact-match/portability grounds, not test (iii) — the census's actual A5-eligible candidate set may still be empty, but not all three exclusions share one reason | `[verified: ciso-reviewer read of enforce-marker-script-shape.sh Stage 2 fast-exit; staff-product-engineer caught the round-3 draft overgeneralizing this to all three candidates]` |
| 15 | Path normalization must preserve repo identity (`<repo-identity>/<repo-relative-path>`), not collapse to a bare relative path, or the plan's own cross-repo-suffix-collision test case becomes unsatisfiable | `[verified: staff-backend-engineer, from the plan's own stated test case]` |
| 16 | `_classify_reviewer_verdict` returns a third bucket, `unclassified`, beyond findings-found/zero-finding | `[verified: staff-product-engineer, transcript-analysis.py:1828, :1910]` |
| 17 | GH-558's own 30-day dispatch table shows single-digit volume for some agent types (`staff-data-engineer` 5, `staff-analytics-engineer` 6) | `[verified: staff-product-engineer, quoting GH-558's issue body]` |
| 18 | Minimum per-bucket N is fixed at 10 in this plan, before the corpus run, as a conventional two-proportion-comparison floor — not tuned to observed cell counts | `[engineer-verified]` — three reviewers (staff-sdet, staff-platform-engineer, staff-product-engineer) independently flagged an unfixed N as the same tautology already fixed for the `Cited` floor |

## Critical files

**Part A** (branch 1):
- `claude/.claude/scripts/transcript-analysis.py` — `_HOOK_DENIAL_SIGNATURE`,
  `hook_denial_key`, `_denial_hook_label`, `_denial_command_shape`,
  `_print_deny_summary` (add the cross-tab), the four `review-trace` denial
  surfaces, and the `friction-count` call sites that must stay byte-identical.
- `claude/.claude/settings.json` — `permissions.allow`, **separate commit**.
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — new cases, plus
  re-scoping `TestFrictionCountCrossPathEquality`'s docstring.
- `docs/transcript-analysis.md` and
  `claude/.claude/skills/transcript-analysis/SKILL.md` — note `--deny-summary`
  shipped in #569 **undocumented**, so this *adds* the flag rather than amending
  existing text.

**Part B** (branch 2, from Part A's merge commit):
- `claude/.claude/scripts/transcript-analysis.py` — `cmd_reviewer_yield`,
  `_index_subagent_dispatches`; a cited-path extractor, a path normalizer that
  strips only the `.claude/worktrees/<branch>/` segment while preserving repo
  identity (`<repo-identity>/<repo-relative-path>`, worktree-segment stripped
  before repo-root handling, `~` expanded, `/tmp` vs `/private/tmp` reconciled),
  and a parent-side post-dispatch edit index.
- Same test file, doc section (`## reviewer-yield`), and `SKILL.md` routing row.

**Reuse rather than reimplement:** `_index_subagent_dispatches` for the dispatch
join; `_parse_ts` for ordering; `_content_text` for block decoding;
`_parse_since_nd_arg` (`reviewer-yield` duplicates it inline at `:1938-1948` —
behaviorally identical including the error text, so folding it in is safe in-file
scope for Part B); the `_REVIEWER_VERDICT_*` bucket constants for the null
control; `_DENIAL_HOOK_NAME_RE`'s bounded-capture idiom for every new pattern.
Existing fixture helpers need **parameter additions, not parallel builders**:
`_edit_use` hardcodes its path, `_user_msg`/`_tool_result` carry no timestamp,
and no helper builds a `Write` tool_use with `input.content` in a subagent
transcript.

## Verification

Run from a linked worktree; every command carries the `../../../.venv/bin/`
prefix, **shellcheck included** — `requirements-dev.txt` pins `shellcheck-py`
and a bare `shellcheck` resolves to a different system binary that CI does not
use.

**Both parts:** `../../../.venv/bin/pytest claude/.claude/`,
`../../../.venv/bin/ruff check claude/.claude/`, and
`scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`. Any
failure is reproduced against a merge-base worktree before being treated as in
scope. If pytest wall clock moves materially, update the CI budget comment in
`.github/workflows/tests.yml`.

**Every corpus-derived acceptance threshold below is additionally pinned by a
deterministic fixture test, except where explicitly named as an exit condition
on reality that no fixture can pin** (Part B item 2's separation clause) —
because CI has no `~/.claude/projects` and cannot re-derive any of these
numbers. Corpus runs are PR-body evidence only, labelled per assumption 11.

**Part A:**
1. A `TestFrictionCount` characterization test over one fixture containing: a
   legacy `attachment` denial; a `tool_result` with signature text and no
   `toolDenialKind`; one per each of the five kind values *with* signature text;
   and one `permission-rule` with **non**-signature text. Assert an explicit
   integer with a docstring naming which records contribute. This pins movement
   in **both** directions — the existing nudge test cannot, because every one of
   its fixtures lacks `toolDenialKind` entirely and so exercises only the
   fallback path.
2. A divergence test with a record carrying a **non-gate `toolDenialKind` and
   non-signature-matching text** (not merely "a non-gate-kind record," which
   could still be signature-matching and so not exercise the actual divergence),
   asserting `review-trace`'s `friction` line counts it while `friction-count`
   does not. Plus the re-scoped `TestFrictionCountCrossPathEquality` docstring,
   stating explicitly that its fixture carries no `toolDenialKind` and that the
   equality holds only on the `denial`-only path — so a future reader adding a
   kinded record to that fixture doesn't silently resurrect the false invariant.
3. `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_nudge_error_mode_analysis.py`
   — passes unchanged, as an end-to-end wiring check only, **not** as the
   semantics guard.
4. Cases for: absent `toolDenialKind`; legacy `attachment` interacting with the
   new kind axis; dedup across the legacy+current pair sharing one `tool_use_id`
   where one carries a non-gate kind, asserting friction's separate dedup set
   does not suppress it; empty-string `tool_use_id`; and each A3 normalization
   step — `NAME=VALUE` prefix, absolute-path token 0, and one case per named
   value-taking flag (`-C <path> commit`, `-c key=value commit`,
   `--git-dir=<path> status`, `--work-tree <path> commit`, covering both the
   separate-token and `=`-attached forms), each asserting it buckets by its true
   subcommand and never as `other`, plus an ESC byte asserting it never survives
   to stdout.
5. A friction-only session fixture (no `denial`-kind events at all), run with
   `--deny-only --deny-summary` together: asserts the session is not dropped,
   its friction counts appear in the breakout, and — run without `--deny-summary`
   — the default timeline renders a `friction` line for it rather than an empty
   `denials=0` header. A companion negative-assertion case on the same fixture:
   `has_denial` is false and no `denial`-kind timeline line is rendered,
   directly pinning "three of four surfaces stay unchanged" rather than relying
   on the divergence test's `friction-count` comparison alone.
6. A prescription↔allowlist alignment test comparing (subcommand, argument)
   tuples under the **tilde-prefixed form only** — the hook's `MARKER_SHAPE`
   regex accepts both `~/…` and any absolute `/…/.claude/scripts/marker.sh`
   prefix, but every existing and proposed `permissions.allow` rule is
   tilde-only by convention (A5's absolute-path exclusion is deliberate, not an
   oversight the test should flag). Every marker operation the shape hook
   accepts, under the tilde form, has a matching `permissions.allow` entry,
   **except a fixed, literal exception set written into the test at authoring
   time** — `{"clear-stale", "clear-stale --dry-run"}` today, each entry
   commented with its follow-up issue number. The set is a literal, not "any
   other form A4 declines" — an open-ended exception clause degrades to
   whatever the implementer types, the exact failure blocker 1 closed one level
   up. A shape A4 later excludes requires editing this literal (and so passing
   review), not matching a wildcard. The test asserts the exception set equals
   that literal exactly, not that parity holds unconditionally — an unqualified
   parity assertion would fail on day one and the path of least resistance to
   green CI would be re-granting the very shapes A4/A5 just excluded. Assert by
   parsing `settings.json` and driving the hook, not by substring-matching
   either file.
7. `review-trace --deny-only --deny-summary` against the real corpus, evaluated
   against a fixture with a known, enumerated set of denial shapes asserting
   `other == 0` and `unmatched == 0` for that fixture — a numeric, CI-checkable
   criterion, not "drops materially." The fixture's shape set is drawn from
   shapes currently observed as `other` in the live corpus, not authored from
   A3's own allowlist — a fixture built from the allowlist it's meant to test
   can never fail. The live-corpus run against the 70%/21% baseline is recorded
   in the PR body as directional evidence only. State whether the four non-gate
   kinds are inside or outside the `other`/`unmatched` denominator in both the
   fixture and the corpus run.
8. `/review-permissions` against the isolated `settings.json` commit.
9. **Rollback:** reverting either commit is a clean `git revert`; no data
   migration exists. Two residues survive a revert and are named rather than
   swept: `.error-mode-nudge-checkpoint.d/<session_id>` persists a running
   per-signal total and byte offset for any live session that scanned under the
   changed friction semantics, so a revert leaves that session's counters
   permanently blended — bounded impact, since the nudge is a non-blocking
   advisory string; and `.error-mode-nudge-fired.d/<session_id>` is one-shot and
   swept after 30 days, so a spuriously-fired nudge cannot be unfired but expires
   on its own. No sweep is added for either; both are named so a revert isn't
   mistaken for a clean-slate reset. If the `settings.json` commit is ever
   reverted on its own while the Python commit stays (e.g. to walk back a rule
   A5 shipped), the corresponding shape must move into item 6's exception-set
   literal in the same revert, or that test goes red — the isolated-commit split
   is for reviewability, not for independent rollback of a rule the alignment
   test still expects.

**Part B:**
1. The pre-implementation spike, run **before** the extractor is designed,
   scored against the numeric `Cited` floor fixed in advance (assumption 12) —
   the floor is written back into this plan once decided, before the extractor's
   design starts.
2. `reviewer-yield --since 30d` against the real corpus: record the `Cited`
   denominator against that floor. Separately, as a **named exit condition on the
   ticket, not a CI-checkable criterion** (no fixture can pin an empirical
   property of the actual corpus): confirm findings-found and zero-finding
   overlap rates actually **separate** per agent type, printing each cell's N and
   suppressing any cell below the fixed minimum N of 10 as "insufficient data."
   If the rates do not separate at cells with adequate N, or if no agent type
   clears N=10 in both buckets, GH-558 stays open, in the latter case with the
   stated reason "dispatch volume, not yield, is the limiting factor."
3. A unit fixture pinning the N=10 boundary itself: a cell with exactly 10
   dispatches reports a rate, a cell with 9 reports "insufficient data" — since
   an off-one error here silently flips a cell between publishing and
   suppressing. A separate `unclassified`-exclusion fixture: one `unclassified`
   dispatch contributes to **neither** bucket and appears in its own reported
   row with its share of the agent type's total — asserted by checking it lands
   in the named `unclassified` row specifically, not merely that some bucket's
   count stayed low, which would pass regardless of where the dispatch actually
   went. The `findings_path`-set-but-no-`Write`-emitted case (below) reuses this
   same named-bucket assertion rather than a bucket-agnostic count check, since
   a weak "lands in exactly one bucket" assertion can't detect that dispatch
   silently landing in the wrong one.

   Path-matching cases: cited path later edited; never edited; inline citations
   with no findings file; an edit preceding the dispatch's return (must not
   count); parent edits the findings file (must not count); a plan-file
   self-match (must not count); a **relative** citation matching an **absolute**
   parent edit path (must match); a worktree-rooted parent path matching a
   repo-relative citation (must match); an **absolute** citation matching a
   worktree-rooted parent edit from a *different* branch of the same repo (must
   match — the dominant production shape, per assumption 7's 62%); two repos
   sharing a relative suffix (must **not** match); a citation with no
   resolvable repo identity (must not spuriously match an in-repo edit); a
   citation carrying a `file:line` suffix (must match after the suffix is
   stripped); a dispatch with `findings_path` set but no `Write` tool_use ever
   emitted (must land in the named `unclassified` row, per above); a dispatch
   with no paired `tool_result`; and the null-control fixture itself — one
   `zero-finding` and one `findings-found` dispatch citing the same
   later-edited path, asserting they land in two distinct reported rows.
4. Record post-change wall clock against the 13.5s baseline.
5. Confirm no path, session id, or project label appears in output.

**Pipeline:** `/code-review` before each commit; `/ready-for-review` before each
PR; closing-keyword syntax (`Closes #557`, `Closes #558`) in both bodies.

## Out of scope

- **Closing GH-555 / GH-556.** Already closed; Part 0 records the association.
- **Granting `marker.sh clear-stale` in `permissions.allow`.** A real gap, but it
  needs the sweep session-scoped or a `clear-stale` arm added to the marker-shape
  hook's no-gate-release regex first — a hook change routing through
  `claude-hook-review`. Filed as its own issue with both prerequisites; the
  env-prefixed marker.sh form A4 also excludes is folded into the same issue.
- **Unchaining `code-review/SKILL.md:390`'s prescribed `marker.sh write &&
  git commit` form.** The real fix for the chained-invocation denial category,
  but it changes a high-traffic skill's documented workflow — deliberately not
  a drive-by inside an efficiency-audit plan. Filed as its own issue.
- **Widening any hook's accepted-shape list.** A5 touches `permissions.allow`
  only; any other census-surfaced shape needing a hook change is filed to a
  catch-all follow-up issue rather than left unrouted.
- **`_lib_emit_deny` self-identification.** The better long-term fix for the label
  axis; filed, since it changes a user-visible deny message for every stow
  consumer and helps only future transcripts.
- **Changing the reviewer agents' output contract** to emit structured findings.
  Inside this repo's reach (`claude/.claude/agents/*.md`), and it would make Part
  B an exact join on a declared field, dissolving assumption 12 entirely.
  Declined because it rewrites nine agent definitions and changes what every stow
  consumer's reviewers return on every dispatch. Filed.
- **GH-430's traversal-guard ordering bug** and **GH-421's worktree-enforcement
  precision work.** Both independently ticketed; GH-557 scopes itself as distinct
  from GH-421.
- **Re-running GH-554's corpus tables.** The tracking issue carries its own re-run
  and staleness caveat; assumption 11 governs how these numbers are described.
