# Fix the round-2 pilot's unread config key, then pool both machines' data to evaluate G2

## Context

Goal: fix a verified defect where `_lib_reviewer_round_state_cap()` never
reads the `round_consult_round2_pilot` config key, so the engineer can
control the round-2 `plan-architect`-consult pilot's arming per machine by
editing `claude-config.toml` directly, instead of relying on a legacy
sentinel file the TOML migration (#991) silently stopped keeping in sync.
Then use the corrected per-firing history (now recoverable with certainty
from each firing's own deny-message text) to evaluate the pilot's
pre-registered G2 outcome measure, pooling this machine's data with a
second machine's.

**The bug.** `_lib_reviewer_round_state_cap()` in
`claude/.claude/hooks/_lib.sh` (~line 3082-3090) resolves the round-2
pilot's state-cap override solely via a presence check on
`"$config_dir/.round-consult-round2-pilot"`. `claude/.claude/scripts/migrate-legacy-config.sh`
imported this sentinel's value into `claude-config.toml`'s
`round_consult_round2_pilot` key and then deleted the now-redundant legacy
file — correct behavior for the migration itself — but no code path was
ever updated to read the TOML key, so the cap silently reverted to the
default (2, uncapped) the moment the legacy file was gone.

**Confirmed impact, not a hypothetical.** `require-architect-consult.sh`'s
deny message interpolates the resolved `$CAP` value directly
(`"this branch has already recorded $CAP distinct reviewed state(s)..."`),
so each historical firing's own message text is a direct, load-bearing
record of which cap was in effect at that moment — not something that
needs reconstructing from timestamps. On this machine, every
architect-consult firing from 2026-09-09 through 2026-09-13T08:34 (21
firings) fired with CAP=1 — this machine ran as the pilot's treatment arm
for its entire active life. It reverted to CAP=2 only in the ~17 hours
before this defect was found, exactly when the TOML migration
(`8a303840`, 2026-09-13T21:33:23-07:00) deleted the legacy sentinel this
machine's `_lib_reviewer_round_state_cap()` still exclusively depends on.
There was never an intentional two-machine control/treatment split for
this pilot — its own pre-registration (GitHub issue #936, filed
2026-09-08) records the sentinel as "created on two machines" starting
that day, both as treatment; the asymmetry investigated this session was
this defect, not a design choice.

**Sibling-key audit (still valid, independent of the above).** All 15
keys in `claude/.claude/hooks/config-keys.psv` were cross-referenced this
session against every `_config_enabled`/`_config_value` (bash) and
`_config.config_enabled` (Python) call site, plus `config-get.sh`
shell-outs from skill files. `round_consult_round2_pilot` is the only key
with no real consumer; every other key is properly read somewhere. Closed,
one-item finding — no further sibling-key work is needed. [verified:
recomputed this session — grep for `_config_enabled`, `_config_value`,
`_config.config_enabled`, and `config-get.sh` across
`claude/.claude/hooks/`, `claude/.claude/scripts/`, and
`claude-skills/skills/`]

**What the engineer wants next.** Fix `_lib_reviewer_round_state_cap()` to
read the config key (so `claude-config.toml` becomes the real, direct
arming lever per machine/account going forward — the engineer's own stated
plan is to set it by hand per account rather than touch a legacy sentinel
file). Separately, evaluate the pilot's own pre-registered G2 outcome
measure — share of fired branches (branches where the gate denied at
CAP=1) that still reach a 3rd review round, against the historical 50%
(29/58) baseline, adopt-candidate bar ≤25% (5/20) at n=20 — using the
now-precise per-firing CAP evidence, pooled with a second machine's
corpus via a peer session (`macbook-round2-ab-test`) already asked to
extract and return its own CAP=1-firing branches and their round-3
outcomes (aggregate counts only, not branch names, per
`docs/transcript-analysis.md`'s DO NOT PUBLISH constraint on raw branch
names under a multi-root corpus).

Intended outcome: `_lib_reviewer_round_state_cap()` reads
`round_consult_round2_pilot` via `_config_enabled` (whose own resolution
chain already covers the legacy sentinel as a fallback, so no separate OR
is needed — see Approach), and an in-session, uncommitted G2 read reports
the pooled adopt/revert signal using real cap-tagged branch outcomes.

## Approach

Two independent workstreams ship under one plan. **Phase A** replaces
`_lib_reviewer_round_state_cap()`'s hand-rolled sentinel-file probe with a
single `_config_enabled round_consult_round2_pilot` call, so the config key
that `docs/hooks.md` already documents as the arming lever actually becomes
one. **Phase B** is a read-only, in-session evaluation of the pilot's
pre-registered G2 outcome measure, using each historical denial's own
interpolated `$CAP` text as ground truth for which arm that firing ran
under, pooled with a second machine's aggregate. Phase B changes no
repository file and commits nothing.

**Root problem:** the pilot's arming state is written in one place
(`claude-config.toml`) and read from another (a legacy sentinel file the
migration deleted), so the pilot silently disarmed — and the same
per-firing evidence that proves it disarmed is what makes the pre-registered
outcome measure computable with certainty rather than reconstruction.

**Givens:**

- The pre-registration's G2 definition, its 50% baseline, its ≤25% bar, and
  its one-sided exact-binomial α=0.05 are fixed and not renegotiable in this
  plan. [verified: `docs/design-decisions/round2-consult-trigger-pilot.md`:78-83]
  Reason: the pilot's own pre-registration owns them, and it invokes the
  "gates fixed before any scan ran" precedent — revising a gate after the
  data exists voids the result it was written to protect.
- The 29/58 = 50% baseline is single-machine, single-repo, computed
  2026-09-01 from `review-trace --this-repo --skill code-review` over
  `jcdendrite/claude-config` only. [verified:
  `docs/case-studies/opus-frontload-review-rounds.md`:35, 79-82] Reason: the
  case study owns that figure; no second-machine baseline exists and
  computing one is a separate study.
- The second machine's CAP=1 extraction is owned by a peer session
  (`macbook-round2-ab-test`) and arrives as an aggregate, asynchronously.
  [engineer-verified] Reason: another party owns it, and this machine cannot
  read that machine's corpus.
- Local session transcripts age out on a rolling 30 days. [verified:
  `docs/design-decisions/round2-consult-trigger-pilot.md`:62-64] Reason:
  Claude Code imposes it. It has not bitten yet — the pilot started
  2026-09-08 and today is 2026-09-14 — but it fixes the deadline.

### Phase A — read the config key

**Mechanism A1 — replace the body with `_config_enabled`, guarded by `||`.**

```bash
_lib_reviewer_round_state_cap() {
  local pilot_status=0
  _config_enabled round_consult_round2_pilot || pilot_status=$?
  if [ "$pilot_status" -eq 0 ]; then
    printf '1\n'
  else
    printf '%s\n' "$_LIB_REVIEWER_ROUND_STATE_CAP"
  fi
}
```

`anchors: root` — one line: `_config_enabled`'s own resolution chain already
walks TOML-key → legacy-file → schema-default internally
(`_config.sh:466-559`, driven by `config-keys.psv:104`'s
`.round-consult-round2-pilot` / `presence-enables` columns), so reading the
key is strictly *less* machinery than the current hand-rolled probe, not
more. [rows 1, 3]

Two lighter primitives were weighed and rejected:

1. **OR-composition — `_config_enabled ... || [ -f "$config_dir/.round-consult-round2-pilot" ]`.**
   Rejected: it re-implements a legacy check `_config_location_value:497-506`
   already performs for this exact key, and the two copies could diverge the
   moment `config-keys.psv`'s `legacy-polarity` column changes. A second
   defensive layer closing a gap the first layer does not have is the
   wrong-foundation tell CLAUDE.md names.
2. **Reading `claude-config.toml` directly (a `grep` for the key).**
   Rejected: it bypasses the value-subset grammar, the malformed-row
   warnings, and the schema-default fallback that `_config_read_key_from_file`
   implements, and it would be the only such reader in the repo.

`anchors: row2` — `|| pilot_status=$?` rather than a bare call followed by
`status=$?`: the bare form aborts a `set -e` caller's `$(...)` subshell
before the `printf`, producing the empty stdout this function's own contract
comment exists to forbid. No current consumer of this function sets `-e`, but
`claude/.claude/scripts/autonomous-shipping-active.sh` (`set -euo pipefail`
at line 2) sources `_lib.sh` at line 9, so a `-e` consumer of this library
demonstrably exists today. The `||` form makes the always-prints-an-integer
contract independent of the caller's shell options — the same reasoning
`_lib_reviewer_round_state_value`'s own header already states for its
capture-then-test shape. The sibling `_lib_round_consult_gate_disabled`
(`_lib.sh:3203-3216`) can use the bare-call-then-`case "$?"` shape because
its sole call site is `_lib_round_consult_gate_disabled && exit 0`, where
`-e` is suppressed by the `&&`.

**Mechanism A2 — fail direction: every nonzero exit yields the default cap.**

`anchors: row6` — the `else` arm deliberately collapses exit 1 (key false),
2 (config dir unresolvable), 3 (`config-keys.psv` unreadable), and 4 (row
missing) into the same result, unlike `_lib_round_consult_gate_disabled`'s
explicit `case`. That is correct *here* and wrong *there* because the two
keys' safe directions are opposite: a disarmed pilot allows more review
rounds before the gate fires, never fewer, so every failure mode already
lands on the safe side with no case distinction needed.

**Mechanism A3 — comment and audit-doc corrections.** The function header,
both consumer hooks' headers, and
`claude/.claude/hooks/tests/config-schema-audit.md`'s
`round_consult_round2_pilot` section all currently assert the file-only
behavior as the design. `docs/hooks.md:54` and `:81` do not — both already
describe "the `round_consult_round2_pilot` config key, falling back to its
legacy sentinel ... when absent," i.e. the post-fix behavior.

`anchors: row7` — `docs/hooks.md` needs no edit, and its wording is the
canonical phrasing the corrected comments defer to rather than restate: the
fallback chain is documented once, at
`_lib_reviewer_round_state_cap`'s own header and in `docs/hooks.md`, and the
two consumer hooks' headers just name the key.

### Phase B — evaluate G2, pooled, on per-firing CAP ground truth

**Mechanism B0 — compose existing subcommands; ship no durable tooling.**

`anchors: root` — the heavier primitive available here is a new
`transcript-analysis.py` subcommand (or a script under `claude/.claude/scripts/`)
that joins denials to round counts. Two lighter primitives in the existing
system cover the whole job: `review-trace --this-repo --deny-only` already
prints each denial's full message text with per-event branch attribution,
and `review-trace --this-repo --skill code-review` already yields the
per-branch round proxy the 29/58 baseline was itself computed from. A
one-off retrospective that runs twice and then never again does not earn a
durable surface every stow consumer carries. [rows 10, 11, 12]

**Mechanism B1 — classify each firing by its own deny-message text.**

Run `transcript-analysis.py review-trace --this-repo --deny-only --since 2026-09-08`.
Keep only rows whose `msg=` opens with `Blocked by architect-consult gate:` —
match on the message, never on the row's `hook=` field, which is empty for
every current-shape denial row. Then bucket by substring:

| Substring in `msg=` | Bucket |
|---|---|
| `already recorded 1 distinct reviewed state ` | CAP=1 (treatment) |
| `already recorded 2 distinct reviewed states` | CAP=2 (default arm) |
| `entering its third distinct reviewed state` | pre-mechanism phrasing — **excluded**, counted |
| anything else | **excluded**, counted, and reported as unclassified |

`anchors: row10` — the deny message interpolates the resolved `$CAP` and
switches `$STATE_NOUN` on it (`require-architect-consult.sh:121-123`), and
`_lib_emit_deny` prefixes it verbatim (`_lib.sh:186-193`), so the captured
text is a direct record of the cap in force at that firing rather than
something reconstructed from timestamps. Match the CAP=1 string with its
trailing space, so `state ` cannot also match `states`.

`anchors: row11` — no truncation risk: `review-trace` renders the denial as
`msg={msg!r}` with no slice (`transcript-analysis.py:2088-2093`).

Reduce CAP=1 rows to **distinct branches**. Rows rendering `branch=?`
(unresolvable per-event branch) go to their own excluded bucket with a
count — they cannot be joined to a round count.

**Mechanism B2 — round counts from the baseline's own instrument.**

Run `transcript-analysis.py review-trace --this-repo --skill code-review`
with **no** `--since`, and count `skill code-review` events per branch. A
branch "reached a 3rd review round" iff it has ≥3 such events.

`anchors: row12` — instrument-matching is load-bearing: the 29/58 baseline
defines a round as one `/code-review` Skill invocation counted from
`review-trace`, so computing the pilot-side numerator from a different
instrument would compare unlike things. `review-round-cost` is main-thread-only
and uses round-*window* detection, so its counts can legitimately differ
from `review-trace`'s raw invocation counts. Use
`review-round-cost --this-repo --branches <fired-branch-list>` as a
**cross-check** on the fired subset only, reading its per-branch
`code-review=N` sub-count; report any branch where the two instruments
disagree on the ≥3 classification rather than silently picking one.

The round query is deliberately unwindowed: a branch that fired on
2026-09-10 can reach round 3 after any `--since`/`--until` bound, and
undercounting rounds biases toward adoption — the wrong direction for a
decision gate.

**Mechanism B3 — determinacy rule, fixed before the numbers are read.**

Reaching round 3 is monotone and absorbing; not reaching it is not. So the
classification is asymmetric:

- **Reaching (determinate):** ≥3 code-review events. Nothing later can undo
  it, regardless of whether the branch is still open.
- **Non-reaching (determinate):** <3 events **and** the branch's PR is
  `MERGED` or `CLOSED` — no further rounds can occur.
- **Indeterminate:** <3 events and the PR is `OPEN`, or no PR row exists.

Resolve PR state with one `gh pr list --repo jcdendrite/claude-config --state all --json number,headRefName,state,mergedAt --limit 300`
call joined locally by branch name, not one `gh pr view` per branch.

`anchors: row18` — the pre-registration is silent on indeterminate branches,
so this plan fixes the rule now rather than after the data is visible.
**Primary:** exclude indeterminate branches from both numerator and
denominator, and report the exclusion count beside the result. **Mandatory
sensitivity:** recompute with every excluded branch counted as *reaching*
round 3 — the direction adverse to adoption. The adopt-candidate bar counts
as cleared only if it clears on both. Treating indeterminate branches as
non-reaching was rejected outright: it is the one handling that moves the
result toward adoption for free.

**Mechanism B4 — the two pre-registered checks, both required.**

G2's text requires the bar to clear "both proxy-on-proxy and
gate-truth-denominator/proxy-numerator."

- **Gate-truth denominator / proxy numerator.** Denominator: the distinct
  CAP=1-fired branches from B1 (actual attested treatment exposure).
  Numerator: of those, how many reach ≥3 rounds per B2.
- **Proxy-on-proxy.** Denominator: branches whose **2nd** code-review event
  falls inside this machine's attested CAP=1 window (the proxy for "entered
  round 2," exactly the denominator that produced the historical 58).
  Numerator: of those, how many reach ≥3 rounds. This is the instrument-
  identical comparison against 29/58.

`anchors: row13, row16` — the pair exists because each check has an artifact
the other doesn't. Gate-truth measures real exposure but uses a denominator
the 2026-08 baseline never had, so a difference could be definitional.
Proxy-on-proxy is baseline-comparable by construction but its denominator
includes branches the gate never actually denied (a live
`/plan-review`/`/ready-for-review` bypass marker, an existing consult latch,
or no reviewer-persona dispatch at the new state — `require-architect-consult.sh:71-83,
114-119`).

The CAP=1 window's end is **bracketed, not pinpointed**: the last CAP=1-tagged
denial and the first CAP=2-tagged denial from B1 bound the moment this
machine's TOML migration took effect. Derive both bounds from the B1 output
at implementation time. A branch whose 2nd round falls inside that bracket is
excluded from proxy-on-proxy and counted in the exclusion report — it cannot
be assigned to an arm. Window start is 2026-09-08 (sentinel creation, per the
pre-registration).

**Mechanism B5 — exact binomial at the actual n, not the pre-registered 20.**

Compute the one-sided tail `P(X ≤ k | n, p₀ = 0.5)` as an integer sum of
`math.comb(n, i)` for `i` in `0..k`, divided by `2**n` once at the end —
stdlib only, exact integer arithmetic, no float accumulation, and no new
dependency.

`anchors: row14` — the bar is "≤5/20 (25%) at n=20" only because 5 is the
largest count clearing α=0.05 at exactly n=20; the pooled n will not be
exactly 20, so hardcoding 5/20 would either over- or under-state the bar.
The implementation must reproduce the pre-registration's own anchor as a
self-check before being trusted on the real n: `P(X ≤ 5 | 20, 0.5) = 0.02069…`
(≤0.05, clears) and `P(X ≤ 6 | 20, 0.5) = 0.05768…` (>0.05, does not).
Report the observed share against the ≤25% bar **and** the exact p-value —
both legs, since at a small n the two can disagree.

**Mechanism B6 — pooling as an asynchronous input.**

Pool by summing `k` and `n` per check across machines, then apply B5 at the
pooled n. Both machines ran the identical CAP=1 treatment through the
identical instrument, so counts pool directly.

`anchors: row17` — the peer's current ask covers only the gate-truth check
(CAP=1-firing branch count and how many reached round 3). Proxy-on-proxy
pooling needs two further aggregates from that machine: its own CAP=1 window
bounds, and its branches-entering-round-2 count with its ≥3 sub-count. Send
that follow-up ask once, phrased with the exact commands and the same
determinacy rule. Do not block on it: if it has not arrived when this
machine's own analysis is ready, report proxy-on-proxy as **this-machine-only**,
say so explicitly, and run the adverse sensitivity with every peer-side
unknown counted as reaching. The same applies if the peer's return omits its
indeterminate-exclusion count.

Compute the per-machine split as an in-session heterogeneity check — a peer
rate wildly unlike this machine's would otherwise hide inside the pool — but
mark it non-publishable on sight: `docs/private-project-redaction.md`:126-139
bars a Count split along the machine dimension, whatever the pooled figure's
own status.

**Open decision — the engineer's, not this plan's.** The pre-registration
fixes a stop point (20 firings or 4 calendar weeks, i.e. 2026-10-06) that the
config bug involuntarily truncated on 2026-09-13, and the same artifact
admits two correct readings of what a read taken now *is*: (A) *the*
pre-registered G2 read, closing the pilot at a truncated n — defensible,
since the treatment window ended involuntarily and the accrued data is all
the pilot will ever produce under those conditions; or (B) an interim
descriptive look, after which Phase A re-arms accrual to the pre-registered
stop point, with the α thereafter nominal rather than exact. The two differ
in whether the α=0.05 bar means what it says. Recommended default: (A) if
the pooled **distinct-branch** n is ≥12 (G1's own under-accrual floor),
otherwise (B). This call must be recorded **before** the numbers are read.
Ask it before Phase B runs.

### Assumption ledger

1. `_config_enabled round_consult_round2_pilot` resolves TOML row → legacy
   file → schema default internally, with no caller-side composition needed.
   [verified: `_config.sh`:466-559 (`_config_location_value`'s documented
   precedence) and `config-keys.psv`:104 (`.round-consult-round2-pilot` /
   `presence-enables`)]
2. A `set -e` consumer of `_lib.sh` exists today, so the `||`-guarded status
   capture is grounded rather than speculative. [verified:
   `claude/.claude/scripts/autonomous-shipping-active.sh`:2 (`set -euo pipefail`)
   and :9 (sources `_lib.sh`)]
3. The probe narrows from `_lib_capped find … -maxdepth 0` (any file type,
   5s-capped) to `[ -f ]` (regular files only, uncapped). A sentinel created
   as a directory would stop arming the pilot; a hung filesystem would block
   rather than time out. Accepted: `touch` produces a regular file, and
   `_config.sh`:28-30 already documents the uncapped-read tradeoff as
   applying to every config-key read on every gate. [verified: `_config.sh`:28-32,
   497-506]
4. `_config_enabled` adds no meaningful per-firing cost at this call site:
   `require-architect-consult.sh:71` already resolves a config key through
   the same schema file earlier in the same invocation. [verified:
   `require-architect-consult.sh`:71, `_lib.sh`:3203-3216]
5. The six existing pilot-sentinel test cases in
   `test_require_architect_consult.py`:344-460 and
   `test_log_reviewer_round.py`:341-470 keep passing unchanged, since each
   `touch`es the legacy file in an isolated `$HOME/.claude` with no
   `claude-config.toml` present — exactly the legacy-fallback arm. [verified:
   both files' pilot classes; `_config_location_value`:493-506]
6. The fix introduces two failure modes the function did not previously
   have — exit 3 (`config-keys.psv` unreadable) and exit 4 (row missing) —
   both of which must yield cap 2 and a non-empty stdout. [verified:
   `_config.sh`:691-713's exit-code contract] Untested today; new tests
   required (see Critical files).
7. `docs/hooks.md`:54 and :81 already describe the config-key-with-legacy-
   fallback behavior, so the doc needs no edit and the code is what drifted
   from it. [verified: both lines read in full]
8. This machine's `claude-config.toml` already carries
   `round_consult_round2_pilot = true`, so the machine re-arms from existing
   state once the fix reaches the stowed checkout via `git pull` — no
   `touch` of the legacy sentinel anywhere in this plan. [engineer-verified]
9. Going forward the engineer sets arming per account by editing that
   account's `claude-config.toml` directly, never by touching the legacy
   sentinel. [engineer-verified]
10. Each denial's message text records the cap in force at that firing,
    because `$CAP` and the `$STATE_NOUN` singular/plural switch are
    interpolated into it. [verified: `require-architect-consult.sh`:98,
    121-123; `_lib.sh`:186-193]
11. `review-trace` prints the denial message in full (`msg={msg!r}`, no
    slice) and its `hook=` field is empty for current-shape denial rows, so
    classification must match on message text. [verified:
    `transcript-analysis.py`:2085-2093;
    `claude/.claude/agents/skill-fidelity-reviewer.md`:148-155]
12. The 29/58 baseline is proxy-on-proxy: denominator = branches with ≥2
    `/code-review` invocations (29 stopping at 2 plus 29 going to 3+),
    numerator = those reaching 3+, both from
    `review-trace --this-repo --skill code-review`. [verified:
    `docs/case-studies/opus-frontload-review-rounds.md`:35 (method) and
    :79-82 (bucket table)]
13. G2's n is **distinct fired branches**, not denial events — its own text
    says "share of fired branches," and one branch can deny repeatedly
    before any consult writes the latch. G1's separate "20 firings" stop
    rule more naturally reads as denial events; report both counts so the
    stop-rule reading stays checkable, and decide neither here. [verified:
    `docs/design-decisions/round2-consult-trigger-pilot.md`:75-83;
    `require-architect-consult.sh`:116-119]
14. `P(X ≤ 5 | n=20, p=0.5) = 21700/1048576 = 0.02069` and
    `P(X ≤ 6 | n=20, p=0.5) = 60460/1048576 = 0.05768`, confirming 5 is the
    largest count clearing a one-sided α=0.05 at n=20. [verified: recomputed
    this session from integer binomial coefficients; matches the
    pre-registration's stated bar]
15. This machine's CAP-taggable firings ran 2026-09-09 through
    2026-09-13T08:34 (21 firings, all CAP=1), with 3 earlier denials using
    the pre-mechanism phrasing. [verified: prior session's grep against
    `review-trace --deny-only --since 2026-09-08` output] Treated as an
    expectation to confirm, not an input: B1 re-derives every count and
    timestamp from a fresh run.
16. The CAP=1 → CAP=2 transition on this machine is bracketed by the last
    CAP=1 and first CAP=2 denials, not pinpointed — the migration commit
    (`8a303840`, 2026-09-13T21:33) is a merge time, not this machine's pull
    time. Branches whose round-2 entry falls inside the bracket are excluded
    and counted. [verified: reasoning from row 15 plus the Context section's
    commit timestamp]
17. The peer machine's aggregate is asynchronous and may arrive before or
    after this machine's analysis; its current ask covers the gate-truth
    check only. [engineer-verified]
18. The pre-registration is silent on branches whose round-3 outcome is not
    yet knowable, so the exclude-plus-adverse-sensitivity rule in B3 is new
    and must be recorded before any number is read. [unverified — asserted
    as the least-gameable handling; anything downstream of the G2 verdict
    inherits this flag]
19. Nothing from Phase B is committed, and whether any pooled figure is ever
    publishable is a separate, gated decision. [verified: root `CLAUDE.md`
    § "Also redact structural fingerprints and provenance";
    `docs/private-project-redaction.md`:91-165]
20. Whether a read taken now is *the* pre-registered G2 or an interim look
    is the engineer's call. **Resolved: (A).** The read already taken is
    the pre-registered G2, closing the pilot at its truncated n. No
    accrual restart follows. Nothing further blocks review-loop changes on
    this machine's account. [engineer-verified]

## Critical files

**Phase A — one `code-writer` dispatch** (`model: sonnet`). The four files
are a single coupled change: the function, its two consumers' headers, the
audit doc asserting today's behavior, and the tests. They cannot be
partitioned without restating the same background in each prompt.

- `claude/.claude/hooks/_lib.sh` — replace
  `_lib_reviewer_round_state_cap()`'s body (lines 3082-3090) with Mechanism
  A1's five lines. Rewrite the header comment (lines 3071-3081) so the
  resolution line names the key, the fail-direction line covers every
  nonzero exit, and the always-echoes-an-integer contract line is kept
  verbatim. Add one line stating why the call is `||`-guarded. Do not
  restate the TOML→legacy→default chain — `_config_location_value`'s own
  header is its canonical home. Leave `_LIB_REVIEWER_ROUND_STATE_CAP` and
  its adjacent comment (lines 3058-3069) untouched. **Reuse:**
  `_config_enabled` is already in scope — `_lib.sh`:21-23 sources
  `_config.sh` at load, and `_lib_round_consult_gate_disabled` at :3203 is
  the in-file precedent for calling it.
- `claude/.claude/hooks/require-architect-consult.sh` — line 6, replace
  "1 under the round-2 pilot sentinel" with "1 when the
  `round_consult_round2_pilot` config key is enabled." Header only; no
  logic change.
- `claude/.claude/hooks/log-reviewer-round.sh` — line 18, the identical
  substitution in "The cap is 2 by default, 1 under the round-2 pilot
  sentinel."
- `claude/.claude/hooks/tests/config-schema-audit.md` — the
  `round_consult_round2_pilot` section, lines 343-364. Its Call-site bullet
  currently states the file-only probe as the design and asserts "The schema
  row exists only for `install.sh`'s schema-driven reporter, not for this
  function's own enforcement" — now false, and the sentence to delete.
  Rewrite the Call-site bullet to name `_config_enabled round_consult_round2_pilot`;
  rewrite the Fail-direction bullet to cover exits 2, 3, and 4 identically,
  stating why collapsing them is correct here and not in
  `_lib_round_consult_gate_disabled`; correct "A user hand-toggles it via
  `touch`" to name editing `claude-config.toml`, with the legacy file as a
  readable fallback. Preserve the "Not part of pre-migration
  `SENTINEL_INVENTORY`" sentence — it is a historical record.
- `claude/.claude/hooks/tests/test_lib_reviewer_round_state.py` — extend
  `TestLibReviewerRoundStateCap` (lines 243-269). Keep all three existing
  cases; the pilot-sentinel one at :255 now exercises the legacy-fallback
  arm and its docstring should say so. Add six cases:
  1. `claude-config.toml` with `round_consult_round2_pilot = true`, no
     legacy file → `1`.
  2. `claude-config.toml` with `round_consult_round2_pilot = false`, legacy
     file present → `2` (the precedence pin: a TOML row is authoritative
     over the legacy file).
  3. `claude-config.toml` present and populated but carrying no
     `round_consult_round2_pilot` row, legacy file present → `1` (the
     fallback survives a populated TOML — this is the exact shape the
     migration produced).
  4. `claude-config.toml` `= true` plus legacy file present → `1`
     (agreement; no double-count, no error).
  5. `config-keys.psv` unreadable (`_config_enabled` exit 3) → `2` on
     stdout, exit 0.
  6. `config-keys.psv` readable and non-empty but with
     `round_consult_round2_pilot`'s row removed (exit 4) → `2` on stdout,
     exit 0.

  **Reuse:** `_state_cap()` at :63-79 already isolates `CLAUDE_CONFIG_DIR`
  and is the harness for cases 1-4 unchanged. Cases 5-6 need an isolated
  hooks dir; use `helpers.symlink_hooks_lib_chain` (already the single
  source of truth for the three-symlink `_lib.sh`/`_config.sh`/`config-keys.psv`
  chain, `helpers.py`:1349-1364) rather than hand-rolling symlinks, and note
  that writing the pruned `config-keys.psv` *before* calling it is
  load-bearing — the helper is `_symlink_if_absent`-based and will leave a
  real file in place. `test_lib.py`:4056-4083
  (`test_not_disabled_when_config_keys_psv_readable_but_missing_round_consult_gate_row`)
  is the worked precedent for the row-pruning recipe; filter on
  `startswith("round_consult_round2_pilot|")`, which cannot collide with the
  `round_consult_gate|` prefix.

No change to `docs/hooks.md` (row 7), `config-keys.psv` (the schema row is
already correct), `docs/config-file.md`, or
`docs/design-decisions/round2-consult-trigger-pilot.md` — the last is a
preserved record of the pre-registration and stays untouched.

**Phase B — no repository files.** The deliverable is an in-session report to
the engineer: the two checks' k/n, their exact p-values, the full exclusion
accounting, and the pooled verdict against the ≤25% / α=0.05 bar. Dispatch
one `general-purpose` extraction agent (`model: sonnet`) to run the
`review-trace` / `review-round-cost` / `gh` calls and perform the
branch-level join, returning **counts and bucket tallies only, never branch
names** — the verbose transcript output is exactly what should not be paid
for in the parent's context, and keeping the join inside the dispatch puts
the aggregation boundary there as hygiene. Run the binomial arithmetic and
the two-check interpretation inline in the parent: it is a handful of stdlib
lines, and the verdict is the judgment the engineer acts on. Ask the
Approach's open decision **before** dispatching.

## Verification

Run from this worktree.

**Phase A:**

- `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py` — the
  scoped suite. A change under `claude/.claude/hooks/` selects the hooks
  domain, which covers `test_lib_reviewer_round_state.py`,
  `test_require_architect_consult.py`, `test_log_reviewer_round.py`, and
  `test_lib.py`.
- `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_lib_reviewer_round_state.py -v`
  as the fast inner loop while writing the six new cases; confirm all nine
  cases in `TestLibReviewerRoundStateCap` pass and that each prints a bare
  integer with exit 0.
- `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_require_architect_consult.py::TestRound2PilotCap claude/.claude/hooks/tests/test_log_reviewer_round.py -v`
  — the legacy-fallback regression check. These six existing cases arm the
  pilot by `touch`ing the sentinel in an isolated `$HOME/.claude`; all must
  still pass unchanged, proving the legacy arm survives the rewrite.
- `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` —
  shell lint for the `_lib.sh` edit.
- `../../../.venv/bin/ruff check claude/.claude/` — lint for the new tests.
- **End-to-end on this machine, before merge:**
  `bash -c '. claude/.claude/hooks/_lib.sh; _lib_reviewer_round_state_cap'`
  run from this worktree with the session's real `CLAUDE_CONFIG_DIR` —
  expect `1`, since this machine's `claude-config.toml` already carries
  `round_consult_round2_pilot = true` (row 8). This is the check that the
  arming lever actually works: it sources the worktree's `_lib.sh` (whose
  `BASH_SOURCE`-relative chain picks up the worktree's `config-keys.psv`)
  against the real config dir. Cross-check with
  `claude/.claude/scripts/config-get.sh round_consult_round2_pilot`, which
  must report the same key enabled. Before the fix the same command prints
  `2` — run it first, so the change is demonstrated rather than assumed.

**Phase B** — these are correctness checks on the analysis itself, not tests:

- **No denial silently unclassified.** The four bucket counts from B1
  (CAP=1, CAP=2, pre-mechanism phrasing, unclassified) must sum to the total
  count of `Blocked by architect-consult gate:` rows in the window. Report
  the unclassified bucket even when it is zero.
- **The pre-mechanism exclusions are visible.** Report their count and
  timestamps explicitly rather than folding them into CAP=2.
- **No fired branch silently dropped.** Every distinct CAP=1 branch must
  land in exactly one of: reaching, non-reaching, indeterminate, or
  branch-unresolvable. The four counts must sum to the fired-branch total.
- **Binomial self-check before use.** The implementation must reproduce
  `P(X ≤ 5 | 20, 0.5) = 0.0207` and `P(X ≤ 6 | 20, 0.5) = 0.0577`, i.e.
  re-derive the pre-registration's own "≤5/20" anchor, before being applied
  to the real n.
- **Instrument cross-check.** For the fired-branch subset, compare
  `review-trace --skill code-review` event counts against
  `review-round-cost`'s per-branch `code-review=N`; report every branch
  where the two disagree on the ≥3 classification instead of resolving it
  silently.
- **Both G2 legs reported, both sensitivities reported.** Gate-truth and
  proxy-on-proxy, each with its primary (indeterminate excluded) and adverse
  (indeterminate counted as reaching) figure, each with observed share and
  exact p-value. A bar cleared on only one leg or only the primary is
  reported as not cleared.

## Out of scope

- **Publishing any G2 figure.** Nothing from Phase B is committed to this
  repo, quoted into a PR body, or added to the design-decision file. Even
  the pooled figure carries a calendar-time window and would have to clear
  `docs/private-project-redaction.md` § "Publishing a pooled tooling
  measurement" on its own terms, including its approval requirement. The
  per-machine split computed as a heterogeneity check is barred from
  publication outright by that doc's machine-dimension rule (:126-139).
  Whether and how the pilot's result is eventually published is the
  engineer's separate call.
- **The adopt/revert decision and its follow-up PR.** The pre-registration
  specifies that adopting makes cap=1 the default in a follow-up PR
  superseding the round-3 decision file, and that reverting removes the
  sentinel. This plan produces the G2 input to that decision; it neither
  makes the decision nor ships either branch of it.
- **G3 and G4.** The pre-registration's qualitative consult-quality measure
  and its commits-to-merge descriptive measure are separate reads with
  separate instruments. G3 is stated as non-contradiction-only for the
  adopt rule, so it is needed before adopting — but not by this plan.
- **A durable analysis subcommand or script.** Rejected in Mechanism B0. If
  this retrospective is ever run a third time, that is the moment to weigh a
  durable surface, with two prior runs' worth of evidence about what it
  should compute.
- **Recomputing a baseline for the second machine.** The 29/58 rate is
  single-machine (Givens). Pooling treatment counts against a single-machine
  baseline is a stated limit of the result, not something this plan closes.
- **Sidechain branch-attribution hardening.** A denial recorded on a
  sidechain thread carries the subagent's own `gitBranch`, which
  `docs/transcript-analysis.md`:182 notes can name a different repo than its
  parent session's. Include such denials and note the caveat in the report;
  building parent-session fallback attribution is disproportionate to a
  one-off read.
- **The 30-day retention deadline's mechanics.** Retention has not bitten
  (Givens), so no recovery or archival work is in this plan. If Phase B
  slips past 2026-10-08, the earliest firings become unrecoverable and the
  window shrinks — a scheduling constraint, not a work item.
