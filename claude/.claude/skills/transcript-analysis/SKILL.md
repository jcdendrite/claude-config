---
name: transcript-analysis
description: Analyze Claude Code transcripts — model comparison by branch, test-failure convergence sequences, correction-signal frequency, active-vs-idle duration, subagent-vs-main turn split, PR-to-branch mapping, per-session review-activity timelines (skill invocations, hook denials, reviewer spawns), a per-session narrative of typed prompts classified as initial/followup/explicit correction, or a corpus-wide census of denial/friction shapes. For token-cost, cache-efficiency, or branch/repo-scoped dollar cost use the `cost` subcommand.
---

The toolkit lives at `~/.claude/scripts/transcript-analysis.py`. Run it directly from the shell.

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
| Are reviewer dispatches producing real findings or mostly zero-finding passes? | `reviewer-yield --since 30d --redact` |
| Is spend climbing week over week? | `cost-trend` |
| Which client or profile does spend belong to? | `cost --by-project --config-dir <dir>` |
| What fraction of Opus code-read turns are clearly dispatchable vs. read-then-edit loops? | `audit-routing-shape --since 35d` |
| Pull a random sample of Opus code-read turns for delegation judgment curation | `audit-routing-samples --since 35d --sample 50 --seed 1` |

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

- The `N failed` count is a coarse proxy: it matches any `N failed` in tool output, including pre-existing failures and intentional baseline runs. Treat the sequence view as the primary read; the aggregate rate is corroborating.
- Subagent (`isSidechain`) turns are excluded from `fail-seq` and `struggle` — reviewer, `Explore`, and `code-writer` agents are not the debugging surface these subcommands measure.
- Durations from `duration` are wall-clock dominated by idle gaps. Look at `Active(min)`, not `Span(min)`.
- `pr-link` requires `gh` and network access. All other subcommands are local-only and make no writes.
- A model-vs-model comparison is only meaningful when there are multiple all-Opus and all-Sonnet execution branches. One or two branches per model is directional, not a controlled A/B.
- `review-trace` locates candidate sessions; it does not judge whether a review caught a *material* issue — that read is qualitative. Use `--since`/`--until` (inclusive day bounds) for before/after-a-date analysis, `--deny-only` to isolate sessions that hit an enforcement hook, and `--deny-summary` for a corpus-wide census by hook, by command shape, and their cross-tab, plus a friction-kind breakout — a distinct axis from denials — see `docs/transcript-analysis.md`'s `review-trace` section for the full output shape.
- `judgment-pair` captures what the human said immediately after a review output. Tool-result turns, `isMeta` injections, and `isCompactSummary` records between the review and the user reply are automatically skipped. Use `--out` to save output to a file for offline curation.
- `user-input` prints raw prompt text verbatim regardless of `--redact` — that flag anonymizes project labels and session IDs only, matching every other `--redact` implementation in this file (none scrub message content). Review output before pasting it anywhere public.
- `audit-routing --redact` remaps project names to anonymized labels for public reporting — use this flag when posting output to GitHub issues.
- `cost` redacts project names and session IDs by default (the opposite of `audit-routing`'s opt-in `--redact`) — pass `--no-redact` only for local use, never for output headed to a public issue.
- `cost --config-dir` unions extra account profiles into one report; `--this-repo` and `--no-redact` are refused in that mode, and redacted labels are not comparable between reports (each run prints a corpus fingerprint).
- `--projects` defaults to `*` — every project on the machine; scope it with `--this-repo` or an explicit glob (see `docs/transcript-analysis.md`'s "Scoping to this repo" section for the derivation and its gaps). `buckets`' Date range column describes whatever the glob matched rather than a bounded window — `buckets` takes no `--since`/`--until`; use `review-trace --since/--until` for a bounded window.
- `review-trace` output is not publish-safe under the default machine-wide scope — each event line's branch string can carry a ticket ID or project name. Run it with `--this-repo` before quoting output anywhere public.

## Example usage

```bash
# Survey all branches
python3 ~/.claude/scripts/transcript-analysis.py buckets

# Check if a branch's debugging loop converged
python3 ~/.claude/scripts/transcript-analysis.py fail-seq --branches feat-TICKET-101

# Compare two branches side by side
python3 ~/.claude/scripts/transcript-analysis.py fail-seq --branches feat-TICKET-101,feat-TICKET-202

# Link branches to PRs and count one author's review comments
python3 ~/.claude/scripts/transcript-analysis.py pr-link \
  --repo owner/repo --branches feat-TICKET-101,feat-TICKET-202 --author alice

# Find sessions that hit an enforcement-hook denial
python3 ~/.claude/scripts/transcript-analysis.py review-trace --deny-only

# Review activity in a date window (e.g. before vs after a skill landed)
python3 ~/.claude/scripts/transcript-analysis.py review-trace --since 2026-01-01 --until 2026-03-31
```

For narrative case studies and annotated timelines built on top of these metrics, use `transcript-narrative`.
