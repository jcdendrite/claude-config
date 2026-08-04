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

### Disclosure control (first revision — superseded below)

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
rather than hardening it. This part stands unchanged by the second revision below.

The first revision then shipped `claude/.claude/scripts/scan-issue-body.sh`, a new
committed script with six detectors, chained manually before the POST:
`scan-issue-body.sh <file> && gh api -X POST … -F body=@<file>`. PR #552's own code review
flagged the problem that forces the second revision below: the chain is a manual step an
operator (human or agent) can simply forget, and the six detectors lived in a file
`deny-private-project-refs.sh` — the hook that already fires automatically on every
`gh api -F body=@file` POST in this repo — knew nothing about. Two definitions of "what
counts as identifying content" in two files is exactly the drift CLAUDE.md's single-source-
of-truth rule exists to prevent.

### Disclosure control (second revision — folds into `deny-private-project-refs.sh`)

**The fix:** delete `scan-issue-body.sh` and its test file entirely. Move the six detector
regexes into `claude/.claude/hooks/_lib.sh` as named `_LIB_*_REGEX` constants — the same
pattern `_LIB_CREDENTIAL_VALUE_REGEX` already establishes, shared today between
`deny-pii-in-commits.sh` and `redact-credential-values.sh`. Extend
`deny-private-project-refs.sh`'s existing scan pass — the one that already builds one
`SCAN_TARGET` by resolving every gated surface (staged diff, commit message,
commit-message-source file, PR title/body, PR body-source file, `gh api` inline fields,
`--input` file, and `-f`/`-F key=@<path>` field-value files — the exact shape
`gh api -F body=@<file>` uses) — with a second scan against the six shared regexes, run at
the same point as the existing tracker-ID scan so a single command that fails either check
gets one deny, not two round trips.

Applied uniformly across all three surfaces the hook already gates — `git commit`,
`gh pr create`/`edit`, and `gh api` — not scoped to `gh api` alone. `SCAN_TARGET` is one
combined buffer across a chained command today (a `git commit && gh pr create` chain
concatenates both surfaces' content before the one tracker-ID scan runs), so splitting it
to scope the new detectors to a subset of surfaces would be a structural change to the
hook's data flow for no real gain: root CLAUDE.md's "Redact private-project-identifying
content" section already claims hostnames, Slack channels, and filesystem paths embedding
project names are "caught by hook when `~/.claude/private-projects.md` is populated" — a
promise the hook has never actually kept, since its only detectors are a tracker-ID regex
and a literal blocklist substring match. This closes that gap for real, for every gated
surface, not just issue bodies. Blast radius stays bounded to the `claude-config` repo
specifically: the hook's existing `REMOTE_URL` scoping check short-circuits on any other
origin, so this does not touch commits in unrelated repos.

Enforcement is now automatic for every gated surface — no manual step to chain, no way to
POST through `git commit`, `gh pr create`/`edit`, or `gh api` without going through the
hook first, since each is a Bash tool call and the hook is wired with no `if`-condition
narrowing. **This does not cover every way to publish GitHub content**, corrected from an
earlier, overstated draft of this section: `gh issue create` and `gh issue comment` are a
real, separate bypass — the hook's dispatch logic (`IS_GIT_COMMIT`/`IS_GH_PR`/`IS_GH_API`)
has no branch for `gh issue`, confirmed by reading the code, so content posted that way is
never scanned at all. This gap is **not currently documented anywhere** in the hook header,
`docs/private-project-redaction.md`, or `README.md` — an earlier draft of this plan
incorrectly asserted it already was; a grep of all three during review found zero
mentions. Per the engineer's explicit direction, this revision (a) corrects that false
claim, (b) documents the gap honestly in the hook's own "Known gaps" list and in
`docs/private-project-redaction.md` (Step 3b), matching the six other bypass classes that
hook already documents-but-accepts rather than closes (`--fill`, `$(cat file)`, `eval`,
backslash-`\git`, cross-repo `-C`, persisted `graphql` queries), and (c) files a dedicated
GitHub issue tracking the gap for its own follow-up triage (Step 6) rather than leaving it
as a buried header bullet, since closing it — recognizing and scanning `gh issue`'s
different flag surface (`--body` inline text, not `-f`/`-F` field flags) — is a real,
separate piece of work this plan does not undertake. Every artifact *this plan itself*
publishes still goes through `gh api -F body=@<file>`, never `gh issue create`/`comment`,
so the gap doesn't weaken this plan's own issue-filing safety — only a future session's
choice of command does.

Blast radius: bounded to repos whose `origin` URL contains the substring `claude-config`,
by design — matching `docs/private-project-redaction.md`'s existing "For fork
contributors" section, which documents this as deliberate (a personal fork named e.g.
`my-claude-config-mirror` is also covered). Not "the `claude-config` repo specifically," a
narrower claim than what the existing scoping check actually does.

This is a repo-wide, security-critical, always-on hook — the `claude-hook-review` skill
governs its design, not `/code-review` alone (root CLAUDE.md's "Should this be a hook?"
section). Route the hook-editing portion of this change through it before `/code-review`.

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
| row13 | The first revision's `scan-issue-body.sh` duplicates detection logic outside `deny-private-project-refs.sh`, and an unwired, manually-chained script is bypassable — both raised as a PR #552 code-review finding (deferred, "ticket to be filed," never filed) and independently by the engineer directly, who judged PR #552 unshippable on this basis | `[engineer-verified]` |
| row14 | `gh issue create`/`gh issue comment` are never scanned by `deny-private-project-refs.sh` (no dispatch branch for `gh issue`), and this was NOT already documented anywhere in the tree despite an earlier draft of this plan claiming it was | `[verified: hook dispatch logic read directly; grep of hook header, docs/private-project-redaction.md, README.md for "gh issue" — zero hits]` — disposition (file a tracking issue, Step 6, rather than close in-PR or leave as a silent header bullet) is `[engineer-verified]` |
| row15 | The parent hook's two existing branches (tracker-ID, private-projects blocklist) echo matched content into their deny messages; porting that convention to the six new detectors would leak session-ID-shaped hex values, hostnames, and IPs into the model's context and the local transcript JSONL | `[verified: deny-private-project-refs.sh:569,595-608 read directly; scan-issue-body.sh and deny-pii-in-commits.sh's own label-only conventions confirmed by reading both]` |
| row16 | Porting `scan-issue-body.sh`'s file-based `grep -Eq` to scan the `SCAN_TARGET` bash variable under this hook's active `set -o pipefail` risks a SIGPIPE-induced exit 141 being misclassified as a genuine grep error by the stated `rc>=2` fail-closed contract, if implemented as a `printf \| grep` pipe rather than a here-string | `[verified: deny-private-project-refs.sh:144 set -uo pipefail confirmed; deny-private-project-refs.sh:592-594's own comment documents the same SIGPIPE-under-pipefail interaction for an adjacent loop]` |

**Mechanism justification.** One new read-only subcommand in an existing script for the
`cost` tool, plus committed, tested detection logic for the disclosure control — the
lightest primitives that make both the method and the publication safeguard repeatable.
Two lighter options rejected for the subcommand: (a) a one-off scratch script, which
leaves the next audit re-deriving prices by hand and is exactly how the current toolkit
drifted (`anchors: root`); (b) documenting the method in prose without code, which cannot
produce the numbers and restates prices where nothing re-derives them — the failure F2
documents (`anchors: row5`). The same two options are rejected for the disclosure-control
detectors for the identical reason (`anchors: root`). The first revision's choice of
*where* to commit that detection logic — a new standalone script rather than the existing
`deny-private-project-refs.sh` hook — is itself superseded by the second revision above
(`anchors: row13`): a second, unwired location for "what counts as identifying content" is
a heavier mechanism than extending the hook that already runs automatically on every gated
surface, and it reintroduces exactly the drift this paragraph's own reasoning warns
against. No new dependency, no new hook, no new permission scope — the second revision
uses *fewer* mechanisms than the first, not more.

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

### Step 3b (revised) — fold the six detectors into `deny-private-project-refs.sh`, delete `scan-issue-body.sh`

- **Delete** `claude/.claude/scripts/scan-issue-body.sh` and
  `claude/.claude/scripts/tests/test_scan_issue_body.py` entirely — superseded by the
  hook-integrated design in Disclosure control above.
- **`claude/.claude/hooks/_lib.sh`**: add six named regex constants, one per detector,
  following `_LIB_CREDENTIAL_VALUE_REGEX`'s existing precedent (a single quoted POSIX-ERE
  string per constant, documented with a one-line comment). Port the exact patterns from
  the deleted script's `DETECTORS` array (`scan-issue-body.sh` lines 46-51 as shipped in
  PR #552) unchanged — do not redesign the regexes themselves in this pass; the boundary-
  safety fixes on the originals (SSH-key-path substring/hyphen-boundary, home-rooted-path
  trailing-slash, Slack-shape vs. bare issue numbers) already went through three rounds of
  review and have dedicated regression tests to port alongside them.
- **`claude/.claude/hooks/deny-private-project-refs.sh`**:
  - Add a second scan pass against `SCAN_TARGET`, immediately after the existing
    tracker-ID `HITS` check and before the private-projects blocklist check, so a single
    deny message covers whichever check fires first. Keep each of the six checks
    independent (not collapsed into one alternation) so the deny message can name which
    detector fired.
  - **Deny message names the detector label only — never the matched substring.** This is
    a deliberate, required departure from the two *existing* branches in this same
    function, which both echo raw matched content (the tracker-ID `HIT_LIST`, the
    blocklist's quoted `matched_lines`) into their deny messages. Reviewed and confirmed
    unsafe to extend to the six new classes: a long hex identifier could *be* a live
    session ID or token fragment, and an internal hostname or IPv4 literal is
    network-recon-value data — echoing either into the deny message writes it into the
    model's context for the rest of the session and persists it verbatim into the local
    session transcript JSONL, the same corpus this plan's own `cost`/`audit-routing`
    tooling parses. `scan-issue-body.sh`'s original design already got this right (its
    deny output names only the label, e.g. `matched detector 'Slack-channel shape'`,
    never the match) and `deny-pii-in-commits.sh` states the identical rule explicitly for
    the same reason (its header: "never the matched value... echoing it re-exposes it into
    the Claude transcript"). Port the label-only behavior, not the two existing branches'
    echo convention — an implementer copying the adjacent `HIT_LIST` pattern for the new
    checks is the failure mode to avoid here.
  - **Use a here-string (`grep -Eq -- "$pattern" <<< "$SCAN_TARGET"`) for each of the six
    checks, not a `printf '%s' "$SCAN_TARGET" | grep -Eq ...` pipe.** `scan-issue-body.sh`'s
    original `rc=$?` capture is safe because it greps a *file* directly, no pipe involved.
    Porting to scan the `SCAN_TARGET` *bash variable* under this hook's `set -o pipefail`
    (already active, line 144) reintroduces a real risk if piped: for a `SCAN_TARGET` larger
    than the pipe buffer, an early grep match can SIGPIPE the `printf` side, and pipefail
    reports the pipeline's exit as the rightmost non-zero status (141), which the stated
    `rc>=2` fail-closed branch cannot distinguish from a genuine grep engine error — a
    spurious "detector failed to scan" deny on what was actually a clean match. A
    here-string has no such pipe. (The codebase already works around this exact interaction
    elsewhere — the private-projects blocklist loop's own comment at lines 592-594 notes
    the same SIGPIPE risk under `pipefail` and sidesteps it by never inspecting `$?`; the
    six new checks *do* need to inspect `$?` to distinguish match/no-match/error, so the
    here-string form is required, not optional, here.)
  - Fail closed on a `grep` error (`rc>=2`) for the new checks. Note this is *stricter*
    than the adjacent tracker-ID scan, not "matching" its posture as an earlier draft of
    this plan claimed: that scan's `HITS=$(... | grep -vE "$OSS_ALLOWLIST" || true)` (line
    561-565) swallows any nonzero grep exit via `|| true`, making it fail-*open* on a grep
    engine error — a real, pre-existing inconsistency between two checks on the same
    buffer in the same function, out of scope to fix here but worth a one-line code comment
    at the new checks noting the asymmetry so a future reader doesn't assume the two scans
    share a failure posture.
  - Update the header comment's "Scope and limits" list: it currently states the hook
    "does NOT catch... absolute filesystem paths with private-project names, or
    structural fingerprints" — false after this change for home-rooted paths and long hex
    identifiers specifically; name the six new classes and keep the remaining true gap
    (custom SSH key names, unlisted internal TLDs, short git SHAs, per PR #552's own
    deferred-findings row) explicit rather than silently dropping the caveat.
  - Add `gh issue create`/`gh issue comment` to the header's "Known gaps" list (see
    Disclosure control above) — a documented, accepted gap like the six already there, not
    a defect introduced by this change.
- **`claude/.claude/hooks/tests/test_deny_private_project_refs.py`**: port the deleted
  test file's ~20 allow/deny fixture pairs (all six detector classes plus the fail-closed
  grep-error and unreadable-file cases) as new test methods, using this file's existing
  `run_hook`/`bash_input`/`claude_config_repo` fixtures rather than the direct-subprocess-
  on-a-bare-file pattern the deleted file used — these are now full hook-invocation tests
  (a `gh api -F body=@<file>` or `git commit` Bash call through the PreToolUse hook), not
  standalone-script tests. Also add at least one chained-command case
  (`git commit -m "..." && gh pr create --body "..."`) proving a single detector match
  anywhere in the combined `SCAN_TARGET` denies the whole chain — the behavior the
  Disclosure control section's "one combined buffer" note depends on. **Add one
  content-suppression regression test per detector class**: assert the deny message
  contains the detector's label and does NOT contain the matched fixture value — this is
  the test that would catch a future edit silently reintroducing the echo-the-match
  pattern from the two adjacent existing branches (check whether `deny-pii-in-commits.sh`'s
  own test file already has an equivalent assertion for its label-only guarantee and
  mirror that shape if so). File is already 2,112 lines; growing it further is consistent
  with this repo's one-test-file-per-hook convention (`test_deny_pii_in_commits.py`,
  `test_redact_credential_values.py` are the sibling examples) — do not split into a
  second file for this hook.
- **`docs/private-project-redaction.md`**: add the six new detector classes to the
  documented match semantics, plus the `gh issue create`/`gh issue comment` gap (Known
  gaps, above). **Illustrative examples in this doc must not themselves match the pattern
  being documented** — the doc will need to explain what each detector catches, and a
  realistic example (e.g. `/Users/alice/...` to illustrate the home-rooted-path detector)
  would trip that exact detector when the doc-update commit itself is scanned. This is the
  same self-reference problem CLAUDE.md's tracker-ID rule already solved with the
  `PROJ-`/`TICKET-` allowlisted-placeholder convention; the six new detectors have no
  equivalent placeholder scheme, so use non-matching illustrative shapes instead (verified:
  an angle-bracket placeholder like `/Users/<username>/` does not match the home-rooted-
  path character class, since `<` falls outside `[A-Za-z0-9_.-]`) or describe the pattern
  in prose without embedding a literal matching string.
- **Root CLAUDE.md**, "Redact private-project-identifying content" section: currently
  states hostnames, Slack channels, and filesystem paths embedding project names are
  "caught by hook when `~/.claude/private-projects.md` is populated" — after this change,
  three of the six new detectors (home-rooted path, internal hostname, Slack-channel
  shape) are always-on structural checks, not gated by that file's population at all. This
  makes the section more true (the hook now actually does most of what it already
  claimed), but the *conditional* framing ("when populated") becomes inaccurate for those
  three classes specifically — correct it in the same commit rather than leaving the repo's
  own canonical redaction doc describing a weaker mechanism than what will exist.

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
`gh api -X POST … -F body=@<file>` — the redesigned hook (Step 3b) scans and gates the
POST automatically; there is no separate script to chain first. Labels: `methodology` on
the tracking issue, `enhancement`/`bug` per child. The `git mkdir` misparse note goes
through the identical `gh api -F body=@file` path as a comment on #421, described in prose
(never quoting the denied command string verbatim, since that string is the one piece of
content in this plan that is not aggregate).

### Step 6 — file a follow-up issue for the `gh issue create`/`gh issue comment` gap

Per the engineer's explicit direction (this gap does not get silently folded into a
"known gaps" bullet alone): file one issue tracking that `deny-private-project-refs.sh`
never scans `gh issue create` or `gh issue comment` — a different flag surface (`--body`
inline text, not `-f`/`-F` field-value files) than the three surfaces this hook currently
gates, so closing it is real, separate work, not a one-line fix. Aggregate-only body (no
identifying content is needed to describe this gap), filed via the same `gh api -F
body=@<file>` path as Step 5, `bug` label. Cross-reference this plan's PR and the "Known
gaps" bullet added to the hook's header (Step 3b) so a reader lands on the code location,
not just the tracking issue.

## Verification

1. From the worktree: `../../../.venv/bin/pytest claude/.claude/` — the **full** suite, not
   just the one test file. CI (`.github/workflows/tests.yml:141`) runs the whole tree with
   `stow` installed (lines 132-137), and this change lifts shared code out of
   `cmd_audit_routing`; if `stow` is not on local `PATH`,
   `test_relocate_claude_config.py`'s real-`stow` invocations error rather than skip
   (no `skipif` guard exists) — install `stow` locally first, or the local run is not
   equivalent to CI's.
2. `../../../.venv/bin/ruff check claude/.claude/`.
3. `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` — this PR edits
   `deny-private-project-refs.sh` and `_lib.sh` (Step 3b), so shell **did** change; this is
   a required check here, not the no-op it would be for a Python-only change.
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
7. **`claude-hook-review`** on the modified `deny-private-project-refs.sh` and `_lib.sh` —
   required before `/code-review` per root CLAUDE.md's "Should this be a hook?" section;
   this is a repo-wide, security-critical, always-on hook, not an ordinary code change.
   Include a **wall-clock timing measurement** for the hook's per-fire cost against a
   representative large diff/PR body (this hook fires on every gated Bash call for every
   contributor, a materially higher call frequency than the `cost` subcommand item 6
   already times) — record it in `docs/private-project-redaction.md` alongside the new
   detector documentation.
8. Prove the new detectors fire through the *actual* PreToolUse hook path, not just a
   standalone scan: for each of the six classes, construct a `gh api -X POST … -F
   body=@<file>` (or `git commit`) call whose body/diff contains that class's shape and
   confirm the hook denies it with the correct label **and that the deny message does not
   contain the matched substring itself**; one clean call and confirm it passes; one
   chained `git commit && gh pr create` case where the leak is in the second command's
   content and confirm the whole chain denies. This is the proof `scan-issue-body.sh`'s own
   tests provided standalone (per the deleted PR #552 test suite) — it must now be
   re-proven through the real enforcement point instead.
9. Confirm `scan-issue-body.sh` has zero remaining references repo-wide:
   `grep -rn "scan-issue-body" claude/ docs/ .claude/` after the deletion, and update
   PR #552's own description (its "Why a separate disclosure-control script" section and
   test-plan bullets both name the deleted script) once the implementation is ready — that
   edit is out-of-band via `gh api -X POST repos/{owner}/{repo}/pulls/552` and itself
   flows through the very hook being modified here. Confirm Step 6's follow-up issue was
   actually filed (not just planned) before treating this revision as complete.
10. Re-read each published artifact (the 5 already-filed issues plus the #421 comment)
    against the new detector set as a post-condition sanity check, not the primary
    detection point — they were already scanned and posted under the first-revision
    design and passed; this just confirms the second revision doesn't newly flag content
    that the first revision's identical regexes already cleared.
11. `/code-review`, then commit; `/ready-for-review`, then open the PR update. Autonomous
    shipping is active (`~/.claude/autonomous-shipping-required` present, no repo optout),
    so this proceeds without further prompting. Merge stays human-only.

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
- Widening the six detectors' precision (RFC1918-only IPv4 scoping, additional SSH key
  name patterns, additional internal TLD suffixes) — carried forward unchanged from PR
  #552's accepted-gaps list. This revision changes *where* the detectors live and *how*
  they're enforced, not what they detect.
- Extending detection to `gh issue create` / `gh issue comment` — a pre-existing gap in
  `deny-private-project-refs.sh` unrelated to this revision; every artifact this plan
  publishes already avoids those commands by design.

---

# Follow-up: PR #552 deferred-findings fix-now subset

## Context

**Goal:** fix the four PR #552 deferred-review findings the engineer triaged as worth doing
now, without reopening the other eight, which stay deferred for the reasons already
recorded in the PR body's "Deferred review findings" table.

PR #552 (this branch) shipped the `cost` subcommand and the disclosure-control hook
fold-in above; it is code-reviewed, plan-reviewed, and CI-green, but still open. Its PR
description carries an 11-row deferred-findings table. This session re-fetched that table
fresh, independently verified the three findings a prior handoff had flagged as
load-bearing against current code (not the handoff's snapshot — one of the three turned out
to need a materially different fix than the handoff proposed, see item 3 below), and
triaged with the engineer via `AskUserQuestion`. Four items were selected to fix now:

1. Hook per-fire latency (measured 650ms-2.5s, ~10-25x over the repo's <100ms hook budget)
2. IPv4 detector over-matching (any dotted-quad, not just private ranges)
3. `_build_redact_map` / `--this-repo` docstring note (documentation only — see below for
   why this is not a behavior fix)
4. SSH-key-path detector test coverage gap (rsa/dsa tested, ecdsa/ed25519 not)

**Branch:** continuing on `transcript-cost-subcommand` rather than branching fresh off
`main`. PR #552 is still open and unmerged, this fix set is small (4 bounded items, no new
subsystem), and the same reviewers already hold context on the hook and `_build_redact_map`
from this branch's prior rounds — a second branch/PR for closely-related follow-up on an
already-open PR would split review context for no benefit. Re-verified before planning:
`main` is still at `ee5e2e9` (unchanged since the prior session's snapshot), and this
branch's HEAD is still `b351907e4372bf05f9580462e8cd3ce18c147d7a`.

## Approach

**Root problem:** four independently-scoped defects survived PR #552's review as
accepted-for-now, but the engineer judged them worth closing rather than leaving
indefinitely deferred; each needs the smallest correct fix, not a redesign.

### 1. Hook per-fire latency — collapse the six-detector loop into a fast/slow path

The six structural detectors (`deny-private-project-refs.sh:617-637`) each spawn one
`grep -Eq` subprocess unconditionally, every hook fire, via a `for` loop — 6 spawns even
when nothing matches, which is the overwhelmingly common case (a clean commit/PR). Fix:
derive one combined-alternation regex from the same `STRUCTURAL_DETECTORS` array already
used by the per-detector loop — join each entry's pattern (stripped of its label prefix)
with `|`, each wrapped in its own group — and run it as a single fast-path `grep -Eq`
before the array is ever iterated. On no match (the common case), skip straight to the
tracker-ID-clean/blocklist section below: 6 spawns collapse to 1. On a match, fall through
to the existing per-detector loop unchanged, to identify which label fired for the deny
message — the rare (deny) path pays the same cost as today, never more. Deriving the
combined pattern from `STRUCTURAL_DETECTORS` at hook runtime, rather than hand-maintaining
a second, separately-declared combined constant in `_lib.sh`, keeps the array the single
source of truth: a future 7th detector added there is automatically covered by the fast
path with no second list to remember to update — closing exactly the maintenance gap a
hand-duplicated constant would create.

Two correctness cases the fast path introduces, both requiring fail-closed handling —
surfaced by plan-review's `ciso-reviewer` and `staff-platform-engineer` passes, who
independently converged on the first:

1. **The fast-path grep call itself errors (`rc>=2`)** — a real grep engine failure
   (locale issue, malformed byte sequence in a large diff), not "no match" (`rc==1`). The
   file runs under `set -uo pipefail` with no `-e` (`deny-private-project-refs.sh:160`), so
   a naive `if grep ...; then <matched> else <no match> fi` around the fast-path call
   collapses `rc==1` and `rc>=2` into the same "no match" branch — the hook would then skip
   the entire per-detector loop and fall through to the tracker-ID-clean/blocklist section,
   a silent fail-open on exactly the leak vector this layer exists to catch. The fast-path
   grep call must capture its exit code with the same three-way `rc==0` / `rc==1` / `rc>=2`
   split the per-detector loop already uses, and the `rc>=2` case must fail closed (deny)
   immediately — not fall through to "no match."
2. **The combined regex matches but the subsequent per-detector loop finds no individual
   match** (only possible if the combined pattern were mis-composed — e.g. an unwrapped
   internal alternation bleeding across a `|` join). Falling through the loop with nothing
   to report must not silently allow content that should be denied.

Both cases get their own fail-closed branch, mirroring the file's own existing `rc>=2`
fail-closed convention (`deny-private-project-refs.sh:633-635`) rather than inventing a new
failure discipline, and each needs its own message so a future reader (or test) can tell
them apart: one names a grep-engine failure on the pre-check itself, the other names a
regex-composition mismatch. `staff-platform-engineer` also flagged that the deny path now
costs one grep call more than today (the fast-path check itself, before the loop runs) —
correcting the earlier claim that the deny path "pays the same cost as today, never more";
the actual delta is one extra subprocess, immaterial next to the six-iteration loop's own
cost.

Two lighter alternatives considered and rejected:
- **No pre-check, just reorder the array** — doesn't help; all 6 still spawn every fire
  regardless of order, since the loop must exhaust all detectors when none match.
- **A single hand-merged mega-regex with no fast/slow split** (one grep, but the deny
  message can't say *which* detector fired without re-deriving it some other way) —
  rejected: the file's own header (`deny-private-project-refs.sh:592-594`) documents that
  detectors are checked independently specifically so the deny message can name the fired
  label; collapsing to one opaque check would regress that, and reverse-engineering the
  label from a single match still needs a second pass, so it doesn't even save a subprocess
  on the deny path.
- **Switch to `rg` (ripgrep) or `perl` for combined regex + named-capture-group label
  reporting in one call** — rejected: introduces a new tool dependency not already used
  anywhere in this file (POSIX ERE `grep` is used throughout), a heavier primitive than the
  fast/slow split for a win that only applies to the already-rare deny path.

### 2. IPv4 detector precision — scope to RFC 1918 + loopback

`_LIB_IPV4_LITERAL_REGEX` currently matches any dotted-quad shape
(`([0-9]{1,3}\.){3}[0-9]{1,3}`), including four-part version strings and any public IP.
Narrow to the three RFC 1918 private ranges plus loopback:

> "10.0.0.0 - 10.255.255.255 (10/8 prefix); 172.16.0.0 - 172.31.255.255 (172.16/12 prefix);
> 192.168.0.0 - 192.168.255.255 (192.168/16 prefix)" — RFC 1918 §3, verified via
> rfc-editor.org/rfc/rfc1918 this session.

> 127.0.0.0/8 listed as "Loopback" per RFC 1122 §3.2.1.3 — verified via the IANA IPv4
> Special-Purpose Address Registry this session.

Standard octet-range regex (`(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])`, matches 0-255)
composed into each of the four ranges above, each respecting its own prefix-length boundary
(`172\.(1[6-9]|2[0-9]|3[01])\.` for the /12).

**`ciso-reviewer`'s plan-review pass flagged a real gap in the naive version of this
narrowing:** the old broad regex (`([0-9]{1,3}\.){3}[0-9]{1,3}`) accepts zero-padded octets
(e.g. `010.000.000.001`, `192.168.001.001` — a real shape from some legacy tooling and log
formats), but literal range prefixes like `10\.` or alternations like `1[6-9]` do not match
a zero-padded form of an in-range value (`016` for `16`). A strict range-scoped regex with
no zero-padding tolerance would silently stop catching private/loopback addresses written
this way — a false negative the old, broader detector did not have. Fix: prefix every octet
position (both the four generic 0-255 slots and the 172-range's second-octet 16-31 slot)
with `0*` to tolerate any number of leading zeros ahead of the numeric value — e.g. the
generic octet becomes `0*(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])` and the 172 second
octet becomes `0*(1[6-9]|2[0-9]|3[01])`. `0*` is a single-character-class repeat with no
nested quantifier or alternation overlap, so this introduces no catastrophic-backtracking
risk. With this fix, the claim holds precisely: every string the old regex matched that
falls inside these four ranges (zero-padded or not) still matches; every string outside
them (public IPs, version-number-shaped strings) now correctly does not.

Two lighter alternatives considered and rejected:
- **Leave the broad match, just document the imprecision** — this is the status quo the
  deferred finding flagged as worth fixing now; documenting instead of fixing was exactly
  the "gold-plating, defer" call the original review made, which the engineer is now
  overriding.
- **A dedicated CIDR-matching tool (`grepcidr`, a Python `ipaddress`-based pre-filter)** —
  rejected: new dependency for a single regex-scale fix, over-powered relative to inlining
  four already-well-known ranges in plain ERE, consistent with every other detector in this
  same array.

### 3. `_build_redact_map` / `--this-repo` — docstring only, not a behavior change

`_build_redact_map` (`transcript-analysis.py:2061-2090`) always scans the full corpus via
`iter_sessions(PROJECTS_DIR, "*")`, and its docstring already explains why: a project must
bind to the same opaque label whether found by a narrowed `--this-repo cost` run or a full
`audit-routing` run, or the same label would mean two different projects across two
published outputs. That is a real, load-bearing invariant — changing it to respect
`--this-repo` would break label stability unless the labeling scheme itself were redesigned
(e.g. content-hash-derived labels instead of scan-order-assigned indices), which is a
materially larger change than this deferred-findings sweep and explicitly out of scope (see
below). Fix: add a one-line docstring note stating that `--redact` therefore reads every
project's transcript bytes off disk even under `--this-repo`, in tension with
`--this-repo`'s minimization intent elsewhere in this file — so a future reader sees this
as a named, considered tradeoff rather than mistaking it for an oversight to "fix."

### 4. SSH-key-path detector test coverage — add ecdsa/ed25519 cases

`test_structural_ssh_key_algorithm_name_as_substring_allowed`
(`claude/.claude/hooks/tests/test_deny_private_project_refs.py:1723`) asserts that
`invalid_rsa_token`/`avoid_dsa_warnings`-shaped substrings are allowed (not
boundary-delimited key filenames), but only for `rsa`/`dsa`. The detector regex itself
already covers all four algorithms (`_LIB_SSH_KEY_PATH_REFERENCE_REGEX` lists
`rsa|dsa|ecdsa|ed25519`) — this is a test-coverage gap, not a regex gap. Add two sibling
assertions covering `invalid_ecdsa_token`- and `avoid_ed25519_warnings`-shaped substrings.

### Assumption ledger

**Root problem:** stated above — four independently-scoped PR #552 deferred findings,
triaged by the engineer as worth fixing now rather than leaving indefinitely deferred.

| # | Assumption | Tag |
|---|---|---|
| root | Statement above | — |
| row1 | Six structural detectors each spawn a separate `grep -Eq` subprocess unconditionally, every hook fire | `[verified: deny-private-project-refs.sh:617-637 read directly this session]` |
| row2 | Measured per-fire cost 650ms-2.5s (5/50/500KB body sizes), ~10-25x over the repo's stated <100ms hook budget | `[verified: docs/private-project-redaction.md "## Performance" section, lines 155-182, read directly this session]` |
| row3 | Deriving a combined regex by wrapping and `\|`-joining the six existing pattern bodies (none contain an unbalanced paren or an unwrapped top-level alternation) is safe ERE composition | `[verified: all six constants read directly, _lib.sh:878-903]` |
| row4 | RFC 1918 defines 10/8, 172.16/12, 192.168/16 as the three private-use ranges | `[verified: rfc-editor.org/rfc/rfc1918, fetched this session — quoted above]` |
| row5 | 127.0.0.0/8 is IANA-registered "Loopback" per RFC 1122 §3.2.1.3 | `[verified: iana.org IPv4 Special-Purpose Address Registry, fetched this session]` |
| row6 | `_build_redact_map` always scans the full corpus regardless of `--this-repo`, and its own docstring already states this is deliberate (label-stability across differently-scoped runs) | `[verified: transcript-analysis.py:2061-2090 read directly this session]` |
| row7 | A real behavior fix (making the scan respect `--this-repo`) requires redesigning label assignment away from scan-order-derived indices, which is out of scope for this sweep | `[verified: same read — the docstring's own stated reason is the label-stability invariant, which scan-order-narrowing would break]` |
| row8 | `test_structural_ssh_key_algorithm_name_as_substring_allowed` tests only rsa/dsa substrings; the detector regex itself already lists all four algorithms | `[verified: test_deny_private_project_refs.py:1723-1737 and _lib.sh:888 read directly this session]` |
| row9 | The other 8 rows of PR #552's deferred-findings table stay deferred; engineer confirmed this exact 4-item subset via `AskUserQuestion` this session | `[engineer-verified]` |
| row10 | The fast-path grep call's own `rc>=2` case, left unhandled, collapses into the "no match" branch under this file's `set -uo pipefail` (no `-e`) and silently skips the entire per-detector loop — a fail-open regression on the exact leak vector this layer exists to catch | `[verified: ciso-reviewer and staff-platform-engineer independently converged on this finding in plan-review; deny-private-project-refs.sh:160 (`set -uo pipefail`, no `-e`) read directly this session]` |
| row11 | The old broad IPv4 regex accepts zero-padded octets (`010.000.000.001`); a naive range-scoped narrowing without a `0*` leading-zero allowance would silently stop catching private/loopback addresses written this way | `[verified: ciso-reviewer plan-review finding; confirmed by inspection of the old regex `([0-9]{1,3}\.){3}[0-9]{1,3}` against a zero-padded example]` |
| row12 | The existing `test_structural_grep_engine_error_fails_closed` test (line 1972) globally shadows `grep` on `PATH` for the whole hook invocation and asserts the *first* grep call to run hits the fail-closed branch — today that's the per-detector loop's "IPv4 literal" entry; under the fast-path redesign it becomes the new fast-path pre-check instead, so this existing test's assertion must be updated, not just left passing by coincidence | `[verified: test_deny_private_project_refs.py:1972-2004 read directly this session]` |

**Mechanism justification.** All four fixes are the smallest change that closes each
finding: a fast-path regex derived from the existing detector array ahead of an unchanged
slow path (not a rewrite of the detector mechanism), a narrower regex (not a new
dependency), a one-line docstring (not a labeling redesign), and two test cases (not a
broader test-harness change). `anchors: root` for all four — none introduces a new tool, a
new hook, or a new permission scope.

## Critical files

Work continues on `transcript-cost-subcommand` in the existing worktree
(`.claude/worktrees/transcript-cost-subcommand`); no new branch, no new plan file.

### `claude/.claude/hooks/_lib.sh`
- Redefine `_LIB_IPV4_LITERAL_REGEX` (~line 878) to the RFC-1918-plus-loopback-scoped
  pattern, with every octet position prefixed `0*` to tolerate zero-padded forms (see
  Approach item 2); update its one-line comment to state the new scope and cite RFC 1918 /
  RFC 1122 §3.2.1.3.

### `claude/.claude/hooks/deny-private-project-refs.sh`
- Immediately after the `STRUCTURAL_DETECTORS` array declaration (~line 624), derive
  `structural_combined_pattern` by iterating the array once, stripping each entry's label
  prefix, wrapping the remaining pattern in `(...)`, and joining with `|`.
- Replace the unconditional six-iteration loop (lines 625-637) with: one fast-path grep
  call capturing its own exit code with the same three-way `rc==0` / `rc==1` / `rc>=2` split
  the per-detector loop already uses. `rc==1` (no match): fall through to the
  tracker-ID-clean/blocklist section below (line 639+) unchanged. `rc>=2` (grep engine
  error on the pre-check itself): emit a new fail-closed deny naming the pre-check failure —
  do not fall through to "no match." `rc==0` (match): run the existing per-detector loop
  body unchanged to identify the label; if the loop completes with nothing found (the
  composition-mismatch case), emit a second, distinct fail-closed deny naming that case, so
  a reader (or a test) can tell the two new failure modes apart by message text.
- Add a one-line comment above the new fast-path block per this repo's shell-script
  comment-length convention, stating what it does and why (mirrors the loop's own header
  comment style at lines 590-616).
- Update the existing header comment (line 592: "Checked independently (not one
  alternation) so the deny message can name which detector fired") — this description goes
  stale once the fast path adds exactly one combined-alternation pre-check ahead of the
  per-detector loop. State the two-phase shape: one alternation for the common-case
  pre-check, the existing independent per-detector loop only for identifying the label once
  the pre-check hits.
- No change to `emit_deny` call sites' message text for the six detectors — only the
  dispatch path above them changes.

### `claude/.claude/hooks/tests/test_deny_private_project_refs.py`
- IPv4: add a test proving a public (non-private) dotted-quad, e.g. `8.8.8.8`, is now
  **allowed** — the direct proof of the narrowing fix. Existing
  `test_structural_ipv4_literal_denied` (uses `10.20.30.40`, inside 10/8) and
  `test_structural_ipv4_near_miss_two_dot_version_string_allowed` need no change — both
  still pass under the narrower regex. Add one allow/deny boundary pair per range
  (`staff-sdet` plan-review finding — the original draft covered only 172.16/12):
  10/8 (`10.255.255.255` denied / `11.0.0.0` allowed), 172.16/12 (`172.16.0.1` denied /
  `172.32.0.1` allowed), 192.168/16 (`192.168.255.255` denied / `192.169.0.0` allowed), and
  loopback (`127.255.255.255` denied / `128.0.0.0` allowed). Add one zero-padded-octet test
  (`010.000.000.001` denied) proving the `0*` leading-zero allowance works.
- SSH key path: extend `test_structural_ssh_key_algorithm_name_as_substring_allowed` (or
  add two sibling tests, matching this file's existing one-test-per-case granularity) with
  `ecdsa`/`ed25519`-shaped substring-allow assertions mirroring the existing rsa/dsa ones.
- **Fast-path grep-error case** (`ciso-reviewer` + `staff-platform-engineer` plan-review
  finding): update the existing `test_structural_grep_engine_error_fails_closed`
  (line 1972), which globally shadows `grep` on `PATH` so every grep call in the script
  errors. Today its assertion pins the *first* grep call to hit the fail-closed branch as
  the per-detector loop's "IPv4 literal" entry; under the fast-path redesign the first grep
  call is the new pre-check, so update the assertion to match the pre-check's own
  fail-closed message instead — this single updated test *is* the coverage for the new
  `rc>=2` fast-path branch, no separate test needed.
- **Composition-mismatch case** (`staff-sdet` + `ciso-reviewer` plan-review finding,
  new — this branch has no existing test to extend): add a test with a `grep` stub on
  `PATH` that discriminates by invocation — matches (`exit 0`) when the pattern argument is
  the long combined pattern, does not match (`exit 1`) for every individual detector
  pattern (distinguishable by argument length: the combined pattern is several hundred
  characters, the longest individual detector pattern is under 100) — and asserts the hook
  denies with the composition-mismatch message. This is new, previously-unexercised
  security-critical logic; per the standing rule, an untested security branch is
  indistinguishable from an absent one.

### `claude/.claude/scripts/transcript-analysis.py`
- `_build_redact_map` docstring (~line 2061-2074): append one sentence noting that this
  full-corpus scan runs even when the caller passed `--this-repo`, which is in tension with
  that flag's minimization intent elsewhere in this file — a named tradeoff, not an
  oversight.

### `docs/private-project-redaction.md`
- "The six structural detectors" table (line 38): update the IPv4 row's "Catches" / "Does
  NOT catch" cells to describe the new RFC-1918-plus-loopback scope (a public IPv4 address
  now joins "an IPv6 address" in the does-NOT-catch column).
- "## Performance" section (lines 155-182): re-measure the hook's per-fire cost post-fix
  using the same method already documented there (5 runs at 5/50/500KB body sizes on this
  machine) and replace the existing table and narrative with the new numbers — do not
  hand-adjust the old numbers.

## Verification

1. `../../../.venv/bin/pytest claude/.claude/` — full suite, from the worktree.
2. `../../../.venv/bin/ruff check claude/.claude/` and
   `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` — both files
   touched here are shell + Python.
3. `claude-hook-review` on the modified `deny-private-project-refs.sh` and `_lib.sh` —
   required before `/code-review` per root CLAUDE.md's "Should this be a hook?" section,
   same as the original PR #552 work on this file.
4. Re-run the exact per-fire timing method already recorded in
   `docs/private-project-redaction.md` (5 runs each at 5/50/500KB body sizes) against the
   fixed hook; confirm the combined-regex fast path measurably reduces median latency
   versus the currently-recorded 835ms/908ms/1,802ms baseline, and record the new numbers —
   this is the fix's own success criterion, not just a regression check.
5. Confirm automated test coverage, not a one-time manual pass, is what proves the
   fast-path/slow-path split preserves existing guarantees (`staff-sdet` plan-review
   finding: a manual check duplicates coverage the existing per-detector tests already give
   via the full suite, while missing the actually-new logic). The existing six
   per-detector deny/allow tests already re-verify — unchanged — that the slow path still
   identifies the correct label and the deny message still omits the matched substring,
   since they run through the same modified dispatch path in step 1's full `pytest` run. In
   addition, confirm the three new/updated tests from `Critical files` above all pass: the
   updated `test_structural_grep_engine_error_fails_closed` (fast-path grep-error case),
   the new composition-mismatch stub test, and the new IPv4 boundary/zero-padding tests.
6. `/code-review`, then commit; `/ready-for-review`, then push. Autonomous shipping is
   active (`~/.claude/autonomous-shipping-required` present, no repo optout) — this
   proceeds without further prompting once verification passes. Merge stays human-only.

## Out of scope

- The other 8 rows of PR #552's deferred-findings table — each already has a recorded,
  sound DEFER rationale (edge case below current scale, gold-plating, contract pinned
  elsewhere, already-ticketed, or orthogonal). Not re-litigated here.
- Redesigning `_build_redact_map`'s label-assignment scheme (e.g. content-hash-derived
  labels) to make it safely respect `--this-repo` — the docstring note above documents the
  tradeoff; an actual behavior change is a separate, larger piece of work.
- Widening the six detectors' coverage beyond IPv4 precision (custom SSH key names,
  unlisted internal TLDs, short git SHAs) — carried forward unchanged from PR #552's own
  accepted-gaps list; this fix touches only the IPv4 detector's precision, not the detector
  set's completeness.
- Extending detection to `gh issue create` / `gh issue comment` — tracked separately in
  issue #559, unrelated to this fix set.
- Collapsing the tracker-ID scan's two `grep` calls or the private-projects blocklist
  loop's per-entry `grep` calls into the same fast-path treatment — the deferred finding
  names the six *new* detectors as the fix target; the tracker-ID/blocklist scans predate
  this PR and are unrelated to it.
