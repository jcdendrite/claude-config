# LSP as a token-reduction lever: feasibility and go/no-go

## Context

**Goal: decide whether routing code navigation through the Language Server
Protocol would significantly reduce billed tokens, and record the verdict so a
future session does not re-measure it.**

The question arrived as "could implementing LSP in this repo and others
significantly reduce tokens read?" — asked with particular interest in the
one Claude Code account on this machine that is billed by API key, where
tokens are direct dollars rather than consumption against a subscription
allowance. Investigating it surfaced two findings that change the question
rather than answer it, so this plan records the re-scope and its evidence.

The intended outcome is a decision, not a feature: either a measured "yes,
and here is the integration work," or a measured "no, and here is the row in
the cost-levers register that stops the seventh plan from re-deriving it."
The answer below is closer to the second, with one narrow adoption that is
justified on grounds other than cost.

**The bar.** "Significantly reduce" is read here as a lever plausibly worth
double-digit percent of billed input tokens — the scale at which the register's
existing adopted levers operate. A lever bounded below ~5% is recorded and
closed, not pursued.

## Approach

**Concluded design: do not build an LSP integration. Claude Code already
ships a built-in LSP tool that is dormant until a code-intelligence plugin is
installed, so there is nothing to implement — and the measured upper bound on
what LSP could ever reach is ~3.8% of billed input tokens on the most
code-dense account, below the bar above. Enable the native tool per-account
at user scope for its post-edit diagnostics, reject the MCP-bridge route
outright, and record both in the cost-levers register.**

Two findings drove this.

### Finding 1 — the premise "implementing LSP" is already obsolete

Claude Code has had a first-party LSP tool since v2.0.74. It is not something
to build, wrap, or bridge. From the features documentation, verbatim:

> **Code intelligence** | After file edits and on demand | Diagnostics after
> edits; symbol locations on lookup | Low; reduces file reads elsewhere

> **Context cost:** Low. Symbol lookups often replace broad file reads, so
> net context use can go down.

> The LSP tool is inactive until you install a code intelligence plugin for
> your language.

**How the saving actually works.** The LSP specification defines the
navigation responses as metadata, not content: `Location` is `{ uri, range }`
with no content field, and `definition`, `typeDefinition`, `implementation`,
`references`, `documentSymbol`, `workspace/symbol`, the call-hierarchy
methods, and `publishDiagnostics` all return only URIs, line/character ranges,
names, kinds, and short messages. `hover` alone returns text, and that text is
server-rendered type and doc information rather than a copy of the file. So a
symbol lookup costs bytes measured in tens-to-hundreds where a whole-file
`Read` costs thousands. The mechanism is real; the question is only how much
of the workload it reaches.

That framing also bounds the saving honestly: because the responses are
location-only, an agent that needs to *understand* code still reads it. LSP
converts "read the file to find the symbol" into "look up the symbol, then
read a narrow range." It does not convert "read the file to understand the
module" into anything cheaper.

### Finding 2 — the addressable share is small, and it was measured here

Successive filters, each expressed as a share of **all** `Read`-result tokens
so the rows are not compounded with one another:

| Filter | Share of all `Read` tokens (API-billed account) |
|---|---|
| All `Read` result volume | 100% |
| Of that, files a language server can index (not Markdown/shell/JSON) | 40.8% |
| Of that same total, code **and** read whole-file rather than already targeted | 32.6% |
| Restated as share of **total billed input tokens** (11.7% × 32.6%) | **≈3.8%** |

The last two rows multiply as `11.7% × 32.6%`; the 40.8% row is context for
where 32.6% comes from (79.7% of the code bucket was whole-file), not a third
multiplicand.

**That ~3.8% is an upper bound carrying a two-directional uncertainty**, not a
measured saving. It inherits ledger row 6, which is `[unverified]`: the
conversion from read volume to billed volume assumes `Read`-added content is
re-billed at the same average rate as other growth content, and the true
figure could sit above or below the stated band. It also inherits row 7's
one-directional caveat — no discount has been applied for whole-file code
reads that were genuine comprehension reads, which Finding 1 establishes LSP
cannot replace. Any restatement of "3.8%" must carry both caveats.

Markdown is the single largest read bucket at 42.6% — larger than code — and
LSP does nothing for it. The equivalent upper bound on the machine's default
account is ≈1.7%.

The counter-intuition worth stating: the API-billed account's code directory
is the most code-dense in the portfolio by a wide margin — dozens of
repositories totalling millions of lines across several statically-typed
languages. It is the strongest case available. Despite that, its *observed
reading behavior* was still 42.6% Markdown against 40.8% code. Code-dense
repositories do not imply code-dense reading, and the bound above already
prices that in.

Set against it: ~95% of billed input-side volume on both accounts is
cache-read traffic, and genuinely fresh input tokens are ~0.014%. A published
empirical study — arXiv 2607.12161, "Token Reduction Is Not Cost Reduction"
(Weinberger and Hozez) — reports in its abstract that token reduction was
"weakly correlated with cost reduction (Pearson r = 0.15)," and that its
largest compression setup "reduced delivered tool-output tokens by 38.4% but
increased billed cost by 6.8%," attributing the gap to prompt-cache
operations dominating input-side expense. That study does not measure LSP, so
it is a caution about the inference "fewer tokens read ⇒ lower bill," not
evidence about this lever specifically.

### Pros and cons of enabling the native tool

**Pros**

- Post-edit diagnostics: the language server reports type errors, missing
  imports, and syntax issues after each edit without a compiler or linter run,
  so an error introduced mid-turn can be caught in the same turn.
- Precise navigation on typed languages, where grep is imprecise for
  overloaded or re-exported symbols.
- No new third-party dependency in the agent's trust path — the tool is
  first-party and the plugin only configures it.
- Activation is per-account and reversible with `/plugin disable`.

**Cons**

- The token case is weak: bounded by ~3.8% before discounts, on the best
  account; ~1.7% on the other measured one.
- Requires installing a language-server binary per language, which the plugin
  does not do for you.
- Documented memory cost: the vendor's own troubleshooting notes that servers
  "like `rust-analyzer` and `pyright` can consume significant memory on large
  projects," with the remedy being to disable the plugin and fall back to
  built-in search.
- Documented monorepo failure mode: language servers "may report unresolved
  import errors for internal packages if the workspace isn't configured
  correctly" — relevant because the code-dense account is monorepo-heavy.
- Toggling a plugin invalidates the prompt cache, so enabling or disabling
  mid-session carries a re-read cost on the next request.

### Assumption ledger

**Root problem:** billed input tokens on an API-key-billed account are a real
cost, and file-reading is the largest single tool-result contributor to them
(54.8% of tool-result bytes) — the question is whether symbol-level
navigation can convert a meaningful share of that into savings.

**Givens accepted (beyond this plan's reach):**

- Prompt-cache pricing structure and TTL behavior are set by the vendor's
  API; no repository artifact changes how cache reads are billed.
- The built-in LSP tool's activation model — plugin plus a separately
  installed server binary — is the vendor's design; no repository artifact
  makes it install-free.

**Mechanisms and their justification:**

- *Do nothing about tokens* — `anchors: root`. The bound (~3.8%, itself an
  upper bound with two unverified inputs) sits below the stated bar.
- *Enable native code-intelligence plugins per-account at user scope* —
  `anchors: root`. Adopted for post-edit diagnostics; the token effect is
  secondary and unguaranteed. This is the **lightest** primitive available,
  which the over-powered-primitive check requires naming alternatives against:
  - *Rejected — MCP LSP bridge (Serena, `mcp-language-server`)*: strictly
    heavier than a first-party tool that already exists. Adds a third-party
    dependency, a runtime, and a schema surface to duplicate a built-in.
    Note that the schema surface is not itself the objection — MCP tool
    schemas are deferred by default under tool search (ledger row 3) — the
    objection is duplicating a shipped capability with third-party code.
  - *Rejected — richer subagent delegation*: already absorbing ~70% of
    tool-result volume (ledger row 10); this is the incumbent, not an
    unexplored lever.
  - *Rejected — tighter targeted-read discipline in `CLAUDE.md`*: the
    "Locate before a whole-file read" rule already exists and is the prose
    equivalent of what LSP automates; restating it changes nothing.
- *Record the verdict in `docs/cost-levers-considered.md`* — `anchors: root`.
  The register exists precisely so a later plan does not re-measure closed
  ground.

**Assumption rows:**

| # | Assumption | Tag |
|---|---|---|
| 1 | Claude Code ships a built-in LSP tool, dormant until a code-intelligence plugin is installed | `[verified: code.claude.com/docs/en/features-overview and /docs/en/discover-plugins, fetched this session]` |
| 2 | LSP navigation responses carry URIs, ranges, names and kinds — never source text; `hover` alone returns server-rendered doc/type text | `[verified: LSP 3.17 specification, Location/definition/references/documentSymbol/callHierarchy response types]` |
| 3 | MCP tool schemas are deferred by default (tool search on), so idle MCP tools do not sit in the per-turn baseline | `[verified: code.claude.com/docs/en/features-overview — "Full JSON schemas stay deferred until Claude needs a specific tool"]` |
| 4 | `Read` output is ≈11.7% of total billed input tokens on the API-billed account, ≈12.9% on the default account | `[verified: transcript-analysis read-scope + cost, this machine's corpus]` |
| 5 | LSP-indexable code is 40.8% of read volume; the whole-file-code slice is 32.6% of all read volume; upper bound ≈3.8% of billed tokens | `[verified: transcript-analysis, bucketed by file extension, reconciled to read-scope's own total]` |
| 6 | Whole-file→billed conversion assumes `Read`-added content is re-billed at the same average rate as other growth content | `[unverified]` — positional weighting not built; true share could run **above or below** the stated band |
| 7 | The realism discount — what fraction of whole-file code reads were comprehension reads LSP cannot replace — is unquantified | `[unverified]` — transcripts carry no intent signal; 3.8% is an upper bound only |
| 8 | No independently reproduced benchmark of LSP-vs-grep token savings exists | `[verified: search returned only vendor self-published or self-acknowledged-broken benchmarks]` |
| 9 | Tokens on the API-key-billed account are direct dollars, making it the priority lens | `[engineer-verified]` — stated this session |
| 10 | Subagent sidechains already absorb ~70% of tool-result volume (69.6% / 70.5% on the two accounts measured) | `[verified: transcript-analysis subagents, this session]` — distinct from the register's 71.2% delegation figure, which measures a different window |
| 11 | Plugin install offers user / project / local scope; only **project** scope writes to `.claude/settings.json` | `[verified: code.claude.com/docs/en/discover-plugins — "Project scope: install for all collaborators on this repository, which adds the plugin to .claude/settings.json"]` |
| 12 | Enabling code intelligence yields post-edit diagnostics that catch type errors without a compiler run | `[unverified]` — vendor-documented claim, not measured here; adopted on that basis, and no verification step in this plan tests it |

## Critical files

| Path | Change | Reuse |
|---|---|---|
| `docs/cost-levers-considered.md` | Add a section recording this lever, its verdict, and the measured reason | Follow the existing per-plan section + table shape; keep the verdict and measured reason, not the investigation. Restate "≈3.8%" only with its upper-bound-and-unverified-conversion caveat |
| `.claude/plans/lsp-token-reduction-feasibility.md` | This file, committed alongside | — |

**No change to `claude/.claude/settings.json`.** Ledger row 11 establishes why
this holds rather than assuming it: plugin installation offers three scopes,
and only *project* scope writes to `settings.json`. Installing at **user
scope** activates the plugin for one account's config directory without
touching the stow package — the correct boundary, since the stow package
installs to every contributor while the accounts that would benefit are a
subset of one machine's.

Adoption is therefore **deliberately unrecorded in this repo** and outside the
register's audit scope: a future reader can learn from the register that the
token verdict was "no" but cannot learn from it which plugins any given
account has enabled. That is the intended trade — recording per-account
machine state in a public repo is the thing the redaction rules exist to
prevent. `settings.local.json` is not an alternative here; it is gitignored
but still per-repository, whereas plugin scope is per-account.

## Verification

1. Re-derive the two multiplicands that produce the bound, not just the
   context figure: `Read` share of billed input tokens (11.7%) and whole-file
   LSP-indexable-code share of all read volume (32.6%), via
   `transcript-analysis.py read-scope` and `cost`. The verdict holds while
   their product stays below the ~5% bar stated in Context. Re-check the
   40.8% code share alongside them for context, but do not treat it alone as
   the drift signal — it can stay flat while either operative input moves.
2. The corpus is a rolling 30-day window, so these figures move; re-measure
   before citing them in any later plan rather than copying them forward.
3. Re-open the verdict on either of two vendor-side changes, independent of
   corpus drift: tool-search defaults changing such that MCP schemas load
   upfront (invalidating ledger row 3), or the LSP tool's response shape
   changing to return source text rather than locations (invalidating ledger
   row 2 and raising the ceiling).
4. Confirm the register entry renders correctly and that each claim carries
   the source that produced it.
5. If plugins are enabled, the honest test is a before/after on the same
   account's `read-scope` whole-file-code token volume across comparable
   windows — not a synthetic benchmark. Treat the result as directional; the
   delegation ratio and workload mix move independently and will confound a
   short window. Note that this tests the token claim only; ledger row 12's
   diagnostics claim has no verification step here.

## Out of scope

- **Installing language servers.** None are present beyond Xcode's `clangd`.
  This is a policy choice, not an external constraint: installing new software
  autonomously is prohibited, so the binaries named in the vendor's plugin
  table are the engineer's to install.
- **Adopting an MCP LSP bridge.** Rejected above; not planned, not trialed.
- **Verifying the diagnostics benefit.** Ledger row 12 is adopted on the
  vendor's documented claim; measuring it is a separate exercise.
- **Reducing the Markdown read bucket**, which is larger than the code bucket
  on both accounts. It is the bigger target by volume, but it is a different
  lever (prose/skill-body sizing, delegation) and belongs in its own plan.
- **Acting on the cache-rebuild finding.** The register already records
  idle-gap rebuilds from concurrent-session switching as the dominant cost
  driver; it dwarfs this lever and is tracked separately.
