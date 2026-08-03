# Workflow efficiency audit — measurement method, findings, and issue filing

## Context

**Goal:** establish a repeatable way to identify workflow inefficiency, apply it to the
last 30 days of real session data, and file the top contributors as GitHub issues.

The repo already has substantial efficiency tooling — `transcript-analysis.py` (3,306
lines, 17 subcommands), `token-analyzer.py`, `analyze-context.py`, a CLAUDE.md "Model
Routing" section, and `model: sonnet` frontmatter across every agent. What it lacks is a
way to rank levers against each other. Every existing metric is denominated in **raw token
counts**, and the headline efficiency metric (`audit-routing`'s "Sonnet-tier estimate") is
denominated in **output tokens specifically**. Measured in dollars, output tokens are
12.8% of spend. The optimization program is calibrated on the smallest component of the
bill; the largest is uninstrumented.

Intended outcome: a `cost` subcommand that ranks levers by price-weighted dollars, plus
one tracking issue and four child issues capturing what the method surfaced.

## Approach

### The method: a three-axis waste screen

Waste is `cost × (1 − value)`. Neither term is directly observable, so the screen makes
each measurable by proxy:

1. **Price-weight the cost.** Multiply every usage record by its model's actual per-MTok
   rate for that token class. Rank by dollars. This alone reorders the priority list:
   cache-read is 0.1× base input while output is 5× — a 50× spread that raw token counts
   erase.
2. **Pair every cost center with a yield signal.** For each expensive activity, ask what
   artifact it changed. A cost center with no yield signal is not efficient — it is
   *unmeasured*, which is itself the finding.
3. **Check calibration staleness.** Any control parameter derived from a platform constant
   (context window, price, tokenizer) is a dated assumption. Re-derive it against today's
   platform.

**Alternative considered and set aside:** extending `audit-routing`'s turn classification
rather than adding a subcommand. Rejected — `cmd_audit_routing` filters to Opus turns by
construction, and Opus is 16.3% of spend. Bolting a whole-corpus cost view onto an
Opus-scoped command would make its output mean two things.

### Corpus and measurements

30 days (2026-07-03 → 2026-08-02), all projects: **268 sessions, 56,358 priced turns,
$5,906 at list price.**

| Component | $ | Share |
|---|---|---|
| cache read | $3,037 | 51.4% |
| cache write (5m, 1.25×) | $1,533 | 26.0% |
| cache write (1h, 2×) | $572 | 9.7% |
| **output** | **$755** | **12.8%** |
| input | $8 | 0.1% |
| **context (read + writes)** | **$5,143** | **87.1%** |

Mean context per turn: 263,835 tokens. **68.4% of spend lands on turns carrying ≥200k
tokens of context.** By model: Sonnet 5 $4,850 (82.1%), Opus 5 $674 (11.4%), Opus 4.8
$289 (4.9%), Sonnet 4.6 $93 (1.6%) — Opus totals $963, **16.3% of spend**.

**Four hypotheses tested and killed** (recorded so a later revision does not re-raise them):
- *Prefix-cache invalidation.* Cache-writes average 11.7k tokens/turn — a normal
  incremental delta, not repeated whole-prefix rebuilds.
- *Long-session tail.* Spend by session-progress decile is nearly flat (8.1% → 12.6%).
  The driver is turn volume at large context, not session length.
- *Instruction-preamble bloat.* CLAUDE.md at ~4k tokens over ~56k turns is ~$0.04/month.
  The fixed preamble is not the problem; accumulated tool output is.
- *A >200k long-context premium.* The pricing page's "Long context pricing" section states
  Claude 4.6+ include the full 1M window **at standard pricing**. Every model in the
  corpus is 4.6+, so no premium applies and the 68.4% figure needs no rate adjustment.

### Findings, ranked

**F1 — The efficiency toolkit is denominated in the wrong unit.** `token-analyzer.py`
prints unpriced token counts. `audit-routing` headlines a Sonnet-tier estimate computed
from output tokens: 2,369,227 tokens, which at Opus 5 $25/MTok → Sonnet 5 $10/MTok is
**~$36/month, 0.6% of spend**. The 87.1% context share has no instrumentation. (Axis 1)

**F2 — `nudge-handoff-near-context-cap.sh` is calibrated to a retired constant.**
`THRESHOLD=120000`, self-documented at lines 115-117 as "≈ 60% of a 200k context window,
source: claude-sonnet-4-x and claude-opus-4-x at 200k context." Claude 4.6+ ship a 1M
window, so it fires at **12%** of the real window. 71% of sessions cross it (median peak
context 229,388; p90 534,766; max 929,946), and the injected string asserts "Context is
near 60% of the model window" — false by ~5×, injected as fact into the model's context.
`handoff-ratio` shows 50 handoffs vs 7 compactions. (Axis 3)

**F3 — The review gate's own bookkeeping is the top permission-denial source.** 547
permission-rule denials in 30 days; of 443 denied Bash commands, **101 (23%) are
`marker.sh activate/write/deactivate/status`** — the script whose only job is to record
that a review happened. Also denied: 83 `Agent` dispatches, 38 `git commit`, 22
`git checkout`, 19 `git push`. Distinct from #421, which concerns the worktree-enforcement
*hook* regexing command strings; this is `permissions.allow` exact-match rules not matching
the shapes the skills themselves prescribe. Three live instances occurred while producing
this plan: a `marker.sh` call denied for a trailing `; echo`, three specialist dispatches
blocked because `ROUTING.md` was read via `sed` rather than the `Read` tool, and a
reviewer's `/tmp` setup command denied as `git mkdir` when the hook read a file path
containing `bin/git` as a git invocation. (Axis 2)

**F4 — Reviewer fan-out is the largest discretionary workload and its yield is
unmeasured.** 856 of 1,144 subagent dispatches (75%) are reviewer agents — staff-sdet 237,
ciso-reviewer 180, staff-platform-engineer 172, staff-backend 89, skill-fidelity 67,
staff-product 64, staff-frontend 36. Plus 190 `/code-review`, 87 `/plan-review`, 75
`/ready-for-review` invocations. `review-trace` counts spawns but nothing links a dispatch
to a finding, or a finding to a diff change. (Axis 2)

### Issue structure: one tracking issue, four children

**Recommendation: a tracking issue plus four children, not one issue.** They share a real
mechanism — *control loops and efficiency conclusions calibrated against proxies nobody
re-derives* — which earns a family issue. But each fix touches different files, has
different verification, and closes independently. This follows house precedent: #472
("triage separately") and #425, which names a shared shape and links #415/#417/#422 rather
than absorbing them.

The tracking issue is the single authoritative home for corpus, method, and evidence;
children reference it rather than restating numbers (CLAUDE.md DRY rule). F3
cross-references #421 as its hook-side sibling, and the `git mkdir` misparse above is
posted as a comment on #421 rather than duplicated into a new issue.

### Disclosure control (revised — this replaced the original design)

The first draft routed issue bodies through `gh api` so `deny-private-project-refs.sh`
would scan them. Empirical testing during review disproved that as a control: the hook has
exactly two detectors — an `[A-Z]{2,}-\d+` tracker-ID regex and a user-local
`private-projects.md` literal blocklist that **fails open when absent**. A test body
containing an internal IPv4, an `~/.ssh/id_*` path, a hex session ID, an internal hostname
and a home-rooted project path was **allowed, rc=0**. The hook's own header says so at
lines 27-29.

The foundational fix is not a stronger scan — it is **not publishing the risky shape**.
Issue bodies carry **corpus-level aggregates only**: the composition table, context-bucket
distribution, model split, dispatch counts, denial counts. Explicitly excluded from every
published artifact, no exceptions: per-session rows, `cost`'s top-N-sessions section,
verbatim denied-command strings (F3's misparse examples are described in prose, never
quoted literally), and any output from a `--no-redact` run. None of the aggregate content
needs a session ID, a project label, or a path — this dissolves the redaction burden
rather than hardening it.

Three layers remain, in order:
1. **Aggregate-only bodies, scope stated above** (primary — removes the identifying shape
   at the source).
2. **Pre-POST mechanical scan** — `claude/.claude/scripts/scan-issue-body.sh <file>`, a new
   committed script, not an ad-hoc one-off (the plan's own Mechanism justification rejects
   scratch scripts for the same reason F2 documents: nothing re-derives them next time).
   Detects, from the round-1 test body that got a false rc=0: RFC1918/IPv4 literals,
   `.ssh/`/`id_*` key paths, `/Users/<name>/`/`/home/<name>/` home-rooted paths, long hex
   identifiers, **internal hostnames** (any label ending in a non-public-suffix internal
   TLD/domain pattern), and **Slack-channel shape** (`#[a-z0-9_-]+`) — the hostname and
   Slack classes were present in the disproven test body and must not be dropped from its
   replacement. **Exits non-zero on any match; exits non-zero (fail-closed) on its own
   error or an unreadable body file** — a match-only exit code (e.g. bare `grep`, which
   returns 1 on *no* match) is the wrong polarity and must not be wired in directly. Ships
   with allow/deny fixtures, one pair per detection class, under
   `claude/.claude/scripts/tests/`. The POST is chained on this script's exit status, not
   on operator judgment. Confirmation happens **before** publication — a GitHub issue is
   public the instant it POSTs and its edit history is public too, so post-hoc review is
   detection, not control.
3. **`gh api -X POST … -F body=@<file>`** as the tracker-ID/blocklist backstop. Verified to
   reach the hook's scan path (`-F` and `-f` behave identically to the hook; `-F` is
   required because `gh` resolves `@file` only for magic fields). `gh issue create` and
   `gh issue comment` are never scanned by the hook — every published artifact, including
   the `git mkdir` misparse note filed as a comment on #421 (Step 5), goes through the same
   on-disk-body-file → layer-2-scan → `gh api -F body=@file` path. No artifact skips layers
   2–3 because it is "just a comment."

### Assumption ledger

**Root problem:** workflow control parameters and efficiency conclusions are set from
proxies (raw token counts, a stale window constant, dispatch counts) that nobody
re-derives, so effort concentrates on small levers and the large ones stay unmeasured.

| # | Assumption | Tag |
|---|---|---|
| root | Statement above | — |
| row1 | Per-model-ID base input rates: Opus 5 / 4.8 $5, Sonnet 5 $2 (introductory, vendor end 2026-08-31), Sonnet 4.6 $3, Haiku 4.5 $1. Derived multipliers vs base: output 5×, 5m cache write 1.25×, **1h cache write 2×**, cache read 0.1× | `[verified: platform.claude.com/docs/en/about-claude/pricing, fetched 2026-08-02]` |
| row1b | Every rate needs a re-verify-by date, not only the one with a vendor-stated promo end — Sonnet 5's is 2026-08-31, the other four get `fetch date + 90d` as a re-verify checkpoint, since a rate with no expiry check is the exact F2 shape for 4 of 5 models | `[engineer-verified: reviewer-driven correction, round 2]` |
| row2 | Cache writes split 1h vs 5m and must be priced separately — 131,025,825 of 663,585,218 write tokens (19.7%) are 1h; nested `cache_creation.{ephemeral_1h,ephemeral_5m}_input_tokens` sums exactly to flat `cache_creation_input_tokens` in 56,372/56,372 records | `[verified: computed this session]` |
| row3 | Cost composition (87.1% context / 12.8% output), 268 sessions | `[verified: computed this session; regenerated by the shipped tool before filing — see Verification]` |
| row4 | Claude 4.6+ carry a 1M window **at standard pricing**; no >200k premium applies to any model in the corpus | `[verified: same pricing page, "Long context pricing"]` |
| row5 | `THRESHOLD=120000` cites a 200k window in its own source comment | `[verified: nudge-handoff-near-context-cap.sh:115-117]` |
| row6 | Denial counts and tool attribution | `[verified: toolDenialKind records joined to their tool_use blocks]` |
| row7 | **`deny-private-project-refs.sh` does NOT detect IP literals, key paths, home-rooted paths, or session IDs** | `[verified: FALSE — 6 empirical hook executions during review; hook header lines 27-29 confirm]` — this is why the control was redesigned |
| row8 | Unknown/unpriced model IDs occur in real data: 25 records carry `<synthetic>` | `[verified: computed this session]` |
| row9 | List price approximates real billing (subscription terms may differ) | `[unverified]` — used only to *rank* levers; the ranking is price-ratio-invariant |
| row10 | The 101 marker.sh denials are avoidable rather than correct refusals | `[unverified]` — counts are solid, per-denial root cause is not; the issue asks for a census, it does not assert the verdict |
| row11 | Reviewer fan-out has low yield | **not claimed** — F4 asserts only that yield is *unmeasured* |
| row12 | Scope is issues + the cost tool; all four findings filed | `[engineer-verified]` |

**Mechanism justification.** One new read-only subcommand in an existing script, plus one
small committed scan script for the disclosure control — the lightest primitives that make
both the method and the publication safeguard repeatable. Two lighter options rejected for
the subcommand: (a) a one-off scratch script, which leaves the next audit re-deriving
prices by hand and is exactly how the current toolkit drifted (`anchors: root`); (b)
documenting the method in prose without code, which cannot produce the numbers and
restates prices where nothing re-derives them — the failure F2 documents (`anchors:
row5`). The same reasoning applies to the scan script: an ad-hoc grep typed at filing time
is the one-off-script failure repeated for the disclosure control (`anchors: root`). No new
dependency, no new hook, no new permission scope.

## Critical files

Work happens on a new branch in a linked worktree (worktree enforcement is active). All
file changes below land in **one commit** — `claude/**` goes live on `git pull` with no
re-install, so a doc naming a subcommand that isn't there yet, or an issue-filing step
that depends on a scan script not yet committed, is a broken state for stow users.

### Step 1 — `claude/.claude/scripts/transcript-analysis.py`: add the `cost` subcommand

- Reuse `iter_sessions` (note: no leading underscore; `_iter_scoped_sessions` is a
  different function) and the `--projects` / `--since` flag plumbing shared by
  `audit-routing` (parser at line 3191).
- **Price table keyed by model ID, not family.** Sonnet 5 and Sonnet 4.6 differ; Opus 4.1
  is 3× Opus 5. Store one base input rate per model ID plus an `expires` field, derived as
  `min(vendor-stated promo end, fetch-date + 90d)` — Sonnet 5 gets 2026-08-31; the other
  four models have no vendor promo end, so they get fetch-date + 90d as a re-verify-by
  checkpoint. Every rate rots-checks; only Sonnet 5 rots on a vendor calendar. Derive
  output/write/read rates from the documented 5× / 1.25× / 2× / 0.1× multipliers so one
  constant per model is the source of truth. Cite the pricing URL and fetch date above the
  table.
- **Staleness banner takes an injected `today` parameter — never reads wall-clock
  internally.** `cost`'s CLI entry point passes `datetime.date.today()` in; the pricing
  function itself takes `today` as an argument. Without this seam, every `cost` test's
  stdout starts carrying the banner on 2026-09-01 (Sonnet 5's expiry, four weeks after plan
  approval) and any test that string-matches output silently breaks. When any priced rate
  has `today > expires`, the banner text is emitted **inside the same output block as the
  dollar tables** (not a separate log line) — a stale run must not be able to produce
  clean-looking copyable output for a public issue. This is the control F2 shows a source
  comment alone does not provide — the same "cite the source and date" pattern already
  rotted into a 5× false assertion in the handoff hook.
- **Price 1h and 5m cache writes separately** from
  `cache_creation.{ephemeral_1h,ephemeral_5m}_input_tokens`, falling back to flat
  `cache_creation_input_tokens` as 5m only when the nested block is absent. Never count
  both.
- **Unknown model IDs are surfaced, never silently $0** — emit a named row and an
  unpriced-token counter. `<synthetic>` appears 25 times in real data.
- **Define context-at-turn explicitly** as
  `input_tokens + cache_read_input_tokens + ephemeral_1h + ephemeral_5m`. This formula
  produces the 68.4% figure; write it in the code and the docs.
- **Name the `include_subagents` setting `cost` uses** (pin one of `iter_sessions`'s two
  modes) so "priced exactly once" in Step 3's sidechain test has a decidable expected
  value.
- **Pin the rounding policy**: sum raw sub-cent values across all matching records first,
  round to cents only at render time — never round-then-sum. State this once here; Step 3's
  price-math test relies on it.
- Output sections: cost by token class; cost by model ID; cost by context-at-turn bucket;
  top-N sessions by dollars (this section is real-project-identifying by construction —
  never appears in a published artifact; see Disclosure control).

### Step 2 — same file: lift the redact-map builder into a shared helper, failing closed

- Extract the map builder currently inline in `cmd_audit_routing` (lines 1934-1938) so
  `cost --redact` and `audit-routing --redact` cannot drift.
- **Build the map over `iter_sessions(PROJECTS_DIR, "*")` explicitly** — the same
  enumeration `iter_sessions` itself uses, not a raw `PROJECTS_DIR.glob("*/*.jsonl")`.
  The two differ on zero-record transcripts (files whose parse yields no records, which
  `iter_sessions` excludes but a raw glob would not), and that difference shifts every
  subsequent `private-project-N` index — so the enumeration must be named precisely, not
  described as "the full glob." Ignore the caller's `--projects` filter when building the
  map (today both passes share a glob and agree; a `cost` pass that narrowed the filter
  would produce different label→project bindings, so `private-project-2` could mean two
  different projects across two outputs — de-anonymizing by elimination). `--since` never
  reaches map construction in the existing code and stays that way.
- **Fail closed, preserving the `claude-config` carve-out.** `_redact_proj_label`
  (line 1904) is `redact_map.get(label, label)` — a map miss returns the **raw project
  name**. Change the miss branch to a fixed opaque token, but keep line 1902's early
  return of `"claude-config"` unredacted — the fail-closed rewrite must not opaque-token
  the one label that's supposed to pass through, and Step 3's map-miss test must not
  assert against that label.
- **Redact session identifiers too.** At line 2071 only `proj` passes through the map;
  `sid` (a 12-char real-UUID prefix, set at line 1958) prints verbatim under `--redact`.
  `cost`'s top-N section inherits that shape.
- **Default `cost` to redacted output** with an explicit `--no-redact` opt-out. A command
  whose documented purpose includes producing public-issue text should not default to
  emitting real project names and session IDs. `--no-redact` output is never published
  (see Disclosure control's excluded-content list).
- Fix the live docstring drift this touches: `iter_sessions` (lines 393-398) says labels
  are assigned "by first-seen order," which line 1936's `.sort()` already makes false —
  replace the stated reason with the actual one (or state that flat-sort ordering matters
  to other callers independent of the label-assignment claim), don't just delete the
  justification.

### Step 3 — `claude/.claude/scripts/tests/test_transcript_analysis.py`: tests

Follow the existing `capsys` + `monkeypatch(PROJECTS_DIR)` seam and the house DAMP-not-DRY
rule. Add a `_priced(model, *, input=0, cache_read=0, ephemeral_1h=0, ephemeral_5m=0,
output=0, flat_cache_creation=None)` fixture builder sibling to `_opus` (line 1527) —
existing builders emit `usage: {}` or flat keys only, so a naive builder that derives flat
from the nested sum would make the double-count test unable to express its own scenario.
`flat_cache_creation=None` omits the nested block entirely (for the fallback case below);
a numeric value emits flat-only with no nested block; `ephemeral_1h`/`ephemeral_5m` emit
the nested block. Extend `_priced()` for a real `cache_creation`-absent case (`usage: {}`
shape) if the price-math test needs it — anywhere a test needs a specific usage shape, it
constructs a corpus with `_priced()`'s parameters rather than inline dicts.

- Hand-computed corpus with known token counts and a **hand-written expected dollar
  total**, read back through a named extractor (not a string-match on formatted output —
  the rounding policy above makes an ad-hoc parse of `$`-prefixed, comma-separated cells
  brittle) — this, not tool-vs-tool comparison, is the gate on the price math.
- Nested `cache_creation` with non-zero `ephemeral_1h_input_tokens` priced at 2×; flat +
  nested both present are not double-counted; **nested block absent, flat-only** priced at
  1.25× (the fallback path — untested, this under-prices every legacy record by half).
- Per-model-ID selection: Sonnet 5 vs Sonnet 4.6 in the same corpus.
- Unknown model ID surfaced in output, excluded from the priced total, and the
  unpriced-token counter itself asserted (not just the row's presence).
- Mixed model IDs within one session: per-record pricing, session total equals the sum of
  its turns (`token-analyzer.py:116` picks a dominant family per session — an implementer
  copying that shape would misprice every mixed session).
- Context-bucket boundaries at 199,999, exactly 200,000 (the inclusive edge of the "≥200k"
  claim), and 200,001 — a ≥200k turn priced at the same per-token rate as a <200k turn,
  pinning row4 against a future premium being assumed.
- Empty/zero-priced-turn corpus renders a clean zero state (all shares 0%), not a
  divide-by-zero traceback.
- Staleness banner: fires when an injected `today` is past a rate's `expires` and is
  embedded in the same output block as the dollar tables; **stays absent when `today` is
  before `expires`** — a one-direction test passes against a banner that always prints.
- Sidechain turns priced exactly once under the `include_subagents` setting Step 1 names.
- **Redaction deny path (no flag, i.e. the redacted default):** raw project label absent
  from *entire* stdout across every `cost` section; a label absent from the map renders as
  the opaque token, never the raw name (constructed with a ≥2-project fixture so the map
  actually has an unmapped label to test — a single-project fixture can't express a miss);
  session IDs absent. The existing `test_redact_flag_anonymizes_project_names` (line 1658)
  asserts only that `"private-project-"` appears — it catches a total no-op, not a partial
  leak.
- **Redaction allow path:** `--no-redact` emits the real label and real session ID —
  proving the default didn't silently become the only mode.
- **Shared-helper contract:** a fixture with **≥2 non-`claude-config` projects** whose
  sort order changes under a narrowed `--projects` filter (a single-project fixture always
  yields `private-project-1` regardless of the bug, so it can't fail). Assert
  `cost --projects <subset> --redact` and `audit-routing --projects '*' --redact` bind the
  *same* project to the *same* placeholder. (`--since` was dropped from this test — it
  never reaches map construction in the existing code, so a test varying it can't
  distinguish a correct implementation from a broken one.)
- **Regression gate on the lift:** the existing `cmd_audit_routing` tests
  (lines 1601-1792, `--redact` at 1658) must pass **unedited**. If the lift forces a test
  change, it is not behavior-preserving.

### Step 3b — `claude/.claude/scripts/scan-issue-body.sh` + fixtures

New committed script per the Disclosure control section: takes a file path, exits non-zero
on a match for any of the six detection classes (RFC1918/IPv4, `.ssh/`/`id_*` paths,
home-rooted paths, long hex identifiers, internal hostnames, Slack-channel shape) or on its
own error/unreadable input, exits zero on a clean file. Allow/deny fixture pair per class
under `claude/.claude/scripts/tests/`, following the same `test_*.py` convention as the
other scripts in that directory (subprocess invocation + return-code assertion, matching
`test_claude_auto.py`'s shape for a shell-script-under-test).

### Step 4 — docs

- `docs/transcript-analysis.md` — a `cost` section in the existing per-subcommand shape
  (Purpose / Flags / Sample output / When to reach for it), including the context-at-turn
  formula and observed wall-clock on the reference corpus so users can extrapolate.
- `claude/.claude/skills/transcript-analysis/SKILL.md` — one row in the
  question→subcommand table. (The file is 71 lines, comfortably under cap; keep it to a
  row anyway — the table is the skill's routing contract, not a place for prose.)
- `claude/.claude/scripts/token-analyzer.py` — extend the closing pointer at line 185 to
  name `cost`.

### Step 5 — file the issues

1 tracking issue + 4 children in the repo, aggregate-only bodies (per Disclosure control's
excluded-content list — no per-session rows, no top-N-sessions output, no verbatim denied
commands, no `--no-redact` output), each filed via
`scan-issue-body.sh <file> && gh api -X POST … -F body=@<file>`. Labels: `methodology` on
the tracking issue, `enhancement`/`bug` per child. The `git mkdir` misparse note goes
through the identical path — on-disk body file → `scan-issue-body.sh` → `gh api -F
body=@file` — as a comment on #421, described in prose (never quoting the denied command
string verbatim, since that string is the one piece of content in this plan that is not
aggregate).

## Verification

1. From the worktree: `../../../.venv/bin/pytest claude/.claude/` — the **full** suite, not
   just the one test file. CI (`.github/workflows/tests.yml:141`) runs the whole tree with
   `stow` installed (lines 132-137), and this change lifts shared code out of
   `cmd_audit_routing`; if `stow` is not on local `PATH`,
   `test_relocate_claude_config.py`'s real-`stow` invocations error rather than skip
   (no `skipif` guard exists) — install `stow` locally first, or the local run is not
   equivalent to CI's.
2. `../../../.venv/bin/ruff check claude/.claude/`.
3. `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` — this PR adds
   `scan-issue-body.sh` (Step 3b), so shell **did** change; this is a required check here,
   not the no-op it would be for a Python-only change.
4. Confirm the existing `cmd_audit_routing` tests passed **unedited** (git-diff the test
   file: only additions).
5. Run `transcript-analysis.py cost --since 30d` and confirm it **reproduces the ledger
   row3 table**. If it disagrees, the tool's numbers win and the issue bodies are rewritten
   from them — the evidence must be regenerable by a reader running one command, which is
   named in the tracking issue.
   *Not* a cross-check against `token-analyzer.py`: that tool buckets by family
   (`_fam`, line 16-23) so it cannot express per-model-ID rates, drops sessions with
   `total_out == 0` (line 109-110) which discards cache-only sessions, and applies an
   mtime prefilter (line 52) the `--since` path does not. Three unrelated reasons it could
   never reconcile.
6. Run `cost --since 30d` (redacted by default) and confirm no project name, no session
   ID, and no path appears in stdout. Time the run and record it in
   `docs/transcript-analysis.md` (Step 4) — the redact-map first pass now runs on every
   default invocation and full-parses the whole corpus solely to read a directory name; if
   this is beyond interactive tolerance on the reference corpus, revisit the
   Out-of-scope deferral on `iter_sessions`' redact-pass performance rather than shipping
   a default that pushes users toward `--no-redact` for speed.
7. Run `scan-issue-body.sh` over each issue and comment body file; POST only on a clean
   (zero) exit — the script fails closed on its own error, so a POST never proceeds on an
   unreadable file either. Re-read each published artifact afterward as a post-condition
   check, not the detection point: **if it fails, edit the body immediately to redact,
   note the correction inline, and if the leaked content is a credential rather than an
   identifier, rotate it** — GitHub retains edit history, so this catches exposure, not
   publication.
8. `/code-review`, then commit; `/ready-for-review`, then open the PR. Autonomous shipping
   is active (`~/.claude/autonomous-shipping-required` present, no repo optout), so this
   proceeds without further prompting. Merge stays human-only.

## Out of scope

- **Fixing** F2, F3, or F4 — each is filed for separate triage. Only F1's tooling ships here.
- Re-litigating CLAUDE.md's Model Routing section. The 0.6% figure is a cost observation;
  routing also serves quality and latency, which this audit did not measure.
- A new issue for the worktree-enforcement hook — #421 carries a stronger census (1,120
  denials / 335 sessions, 92% FP). It gets a corroborating comment, not a duplicate.
- Performance work on `iter_sessions`' redact first pass, which fully JSON-parses every
  record solely to read `jsonl.parent.name` (a wasted full parse of ~785 MB). Deriving
  labels from `PROJECTS_DIR.glob()` paths is the right fix; it touches a shared function
  used by other subcommands and belongs in its own change.
- The `deny-private-project-refs.sh` detection gap (row7). Worked around here by design,
  not bundled — worth its own issue.
