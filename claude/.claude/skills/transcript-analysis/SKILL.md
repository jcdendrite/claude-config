---
name: transcript-analysis
description: Analyze Claude Code transcripts — model comparison by branch, test-failure convergence sequences, correction-signal frequency, active-vs-idle duration, subagent-vs-main turn split, PR-to-branch mapping, per-session review-activity timelines (skill invocations, hook denials, reviewer spawns), a per-session narrative of typed prompts classified as initial/followup/explicit correction, or a corpus-wide census of denial/friction shapes. For token-cost, cache-efficiency, or branch/repo-scoped dollar cost use the `cost` subcommand.
---

The toolkit lives at `scripts/transcript-analysis.py` under the active Claude Code config dir (`$CLAUDE_CONFIG_DIR`, or `~/.claude`). Run it directly from the shell.

## Scope confirmation

Before quoting a corpus-wide statistic from this toolkit's output, include the resolved-scope header line verbatim in what you report, and if that line reads "1 root (no ~/.claude/transcript-config-dirs declared)", ask the user whether other Claude accounts exist before treating the number as complete.

`cost --summary` prints no resolved-scope header — it is always scoped to the active account only, and states so on its own `Scope: this account only (...)` line instead; quote that line rather than asking about other accounts.

## Which subcommand to use

| Question | Subcommand |
|----------|-----------|
| Which branches exist? What models were used on each? | `buckets` |
| Did a branch converge or thrash during test debugging? | `fail-seq --branches <branch>` |
| Did the user express frustration more with one model? | `struggle --branches <branch>` |
| How much logged time was active vs idle gaps? | `duration --branches <branch>` |
| How much work went through subagents vs the main thread? | `subagents --branches <branch>` |
| Map branches to PRs; count per-author review comments | `pr-link --repo owner/repo --branches <branch>` |
| Which sessions ran review skills, hit a hook denial, or spawned reviewer agents? | `review-trace` |
| Which denial/friction shapes recur across sessions — a corpus-wide census, not per-session? | `review-trace --deny-summary` |
| Which skills did a branch invoke, by source (auto-trigger / routing / `/slash`)? | `skill-invocation --branches <branch>` |
| Where did a human push back on an AI review's output? | `judgment-pair` |
| What prompts did I write, and where did I redirect Claude? | `user-input` |
| Is Opus spend doing Sonnet-tier code-read/write in parent sessions? | `audit-routing --since 35d --redact` |
| Which lever costs the most in actual dollars — cache read/write, output, or input? | `cost --since 30d` |
| What did this branch cost, as a publish-ready aggregate for a PR body? | `cost --this-repo --branches <branch> --summary` |
| Are reviewer dispatches producing real findings, and do sessions then edit what was cited? | `reviewer-yield --since 30d --redact` |
| Is spend climbing week over week? | `cost-trend` |
| What's this repo's durable week-over-week cost/efficiency history, beyond what live transcripts still retain? | `cost-ledger` |
| Which client or profile does spend belong to? | `cost --by-project --config-dir <dir>` |
| What fraction of Opus code-read turns are clearly dispatchable vs. read-then-edit loops? | `audit-routing-shape --since 35d` |
| Pull a random sample of Opus code-read turns for delegation judgment curation | `audit-routing-samples --since 35d --sample 50 --seed 1` |
| What did a specific subagent type actually cost over a date range, and what would it have cost at another model? | `subagent-mix --since-date <date> --until-date <date> --reprice-as <model-id>` |

## Reading fail-seq output

```
Total runs: 12  Failing: 3 (25.0%)  Longest consecutive-failing streak: 2
Sequence: 0 0 5 0 0 0 3 0 0 0 0 0
```

- **Convergent** (expected): a spike followed by zeros — the signature of a root-cause fix. The sequence above is convergent.
- **Thrashing** (flag for review): oscillation like `8 6 9 7 8` with no sustained run of zeros — a model not closing on a fix.
- The `longest consecutive-failing streak` is the load-bearing metric. A streak of 1–2 is normal (fix lands next run). A streak of 5+ warrants a closer look at the sequence and what was happening between those runs.

## Reading user-input output

```
Total sessions: 12   Fresh prompts: 47   Explicit corrections: 6 (12.8%)
```

Three classifications appear in the output:

- **INITIAL** — the first typed prompt in a session. Sets the direction of the conversation.
- **FOLLOWUP** — a subsequent prompt with no frustration-phrase match. Quiet redirects: refining scope, asking follow-up questions, or continuing the thread without explicit frustration.
- **EXPLICIT_CORRECTION** — a subsequent prompt containing a phrase from the `struggle` phrase list (e.g., "try again", "not that"). The matched phrase is noted in parentheses.

Every non-INITIAL prompt counts as a course correction under the "frustration phrases + all follow-up prompts" definition. The FOLLOWUP vs EXPLICIT_CORRECTION split lets you distinguish polite redirects from explicitly frustrated ones.

Use `--corrections-only` to strip initial prompts when you only want the steering moments. Use `--since`/`--until` to focus on a date window (e.g., a sprint or a specific project phase).

## Caveats

- Every subcommand's default scope is a union across every config dir listed in `~/.claude/transcript-config-dirs` (see `docs/transcript-analysis.md`'s "Corpus scope: the declared-roots file" section), not just the active profile — except `cost --summary`, which resolves to the active config dir only. The resolved-scope header states the root count unconditionally on every funnel site that prints it, even at one root with nothing declared — see "Scope confirmation" above — except `review-trace` and `skill-invocation`, which defer their header print until a match is found, and `cost --summary`, which prints no header at all. Redaction covers `cost`, `cost-trend`, `context-distribution`, and `audit-routing` only; every other subcommand prints raw project labels, branch names, or paths under this same union.
- `--branches` filters records by `gitBranch` string only, never by project dir or root — under a multi-root scope it pools same-named branches across every declared account into one tally, with no per-account signal.
- The `N failed` count is a coarse proxy: it matches any `N failed` in tool output, including pre-existing failures and intentional baseline runs. Treat the sequence view as the primary read; the aggregate rate is corroborating.
- Subagent (`isSidechain`) turns are excluded from `fail-seq` and `struggle` — reviewer, `Explore`, and `code-writer` agents are not the debugging surface these subcommands measure.
- Durations from `duration` are wall-clock dominated by idle gaps. Look at `Active(min)`, not `Span(min)`.
- `pr-link` requires `gh` and network access. `cost-ledger --record` writes to `docs/cost-ledger.md` and requires the opt-in sentinel `~/.claude/.cost-ledger-enabled`; every other subcommand, and `cost-ledger`'s own default read mode, is local-only and makes no writes.
- A model-vs-model comparison is only meaningful when there are multiple all-Opus and all-Sonnet execution branches. One or two branches per model is directional, not a controlled A/B.
- `review-trace` locates candidate sessions; it does not judge whether a review caught a *material* issue — that read is qualitative. Use `--since`/`--until` (inclusive day bounds) for before/after-a-date analysis, `--deny-only` to isolate sessions that hit an enforcement hook, and `--deny-summary` for a corpus-wide census by hook, by command shape, and their cross-tab, plus a friction-kind breakout — a distinct axis from denials — see `docs/transcript-analysis.md`'s `review-trace` section for the full output shape.
- `judgment-pair` captures what the human said immediately after a review output. Tool-result turns, `isMeta` injections, and `isCompactSummary` records between the review and the user reply are automatically skipped. Use `--out` to save output to a file for offline curation.
- `user-input` prints raw prompt text verbatim regardless of `--redact` — that flag anonymizes project labels and session IDs only, matching every other `--redact` implementation in this file (none scrub message content). Review output before pasting it anywhere public.
- `audit-routing --redact` remaps project names to anonymized labels for public reporting — use this flag when posting output to GitHub issues.
- `cost` redacts project names and session IDs by default (the opposite of `audit-routing`'s opt-in `--redact`) — pass `--no-redact` only for local use, never for output headed to a public issue.
- `cost --config-dir` unions extra account profiles into one report on top of the declared-roots default; `--this-repo` and `--no-redact` are refused in that mode. Redacted labels (`private-project-N`, `account-N`) are comparable between two reports only when the same declared-roots file produced both — a changed root set renumbers every ordinal (each run still prints a corpus fingerprint).
- `--projects` defaults to `*` — every project across every declared root; scope it with `--this-repo` or an explicit glob (see `docs/transcript-analysis.md`'s "Scoping to this repo" section for the derivation and its gaps). `buckets`' Date range column describes whatever the glob matched rather than a bounded window — `buckets` takes no `--since`/`--until`; use `review-trace --since/--until` for a bounded window.
- `review-trace` output is not publish-safe under the default multi-root scope — each event line's branch string can carry a ticket ID or project name. No flag currently guarantees single-account scope on `buckets`, `review-trace`, `fail-seq`, `struggle`, `duration`, `subagents`, or `pr-link` short of an explicit single `--config-dir` — `--this-repo` no longer implies one account, since it unions across every declared root by default. Name `--config-dir` as the one narrowing control before quoting any of these seven anywhere public.

## Example usage

```bash
# Survey all branches
python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/transcript-analysis.py" buckets

# Check if a branch's debugging loop converged
python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/transcript-analysis.py" fail-seq --branches feat-TICKET-101

# Compare two branches side by side
python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/transcript-analysis.py" fail-seq --branches feat-TICKET-101,feat-TICKET-202

# Link branches to PRs and count one author's review comments
python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/transcript-analysis.py" pr-link \
  --repo owner/repo --branches feat-TICKET-101,feat-TICKET-202 --author alice

# Find sessions that hit an enforcement-hook denial
python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/transcript-analysis.py" review-trace --deny-only

# Review activity in a date window (e.g. before vs after a skill landed)
python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/transcript-analysis.py" review-trace --since 2026-01-01 --until 2026-03-31
```

For narrative case studies and annotated timelines built on top of these metrics, use `transcript-narrative`.
