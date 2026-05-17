---
name: transcript-analysis
description: Run the transcript-analysis.py toolkit to analyze Claude Code transcripts — model comparison by branch, test-failure convergence sequences, correction-signal frequency, active-vs-idle duration decomposition, subagent-vs-main turn split, or PR-to-branch mapping. TRIGGER when: the user asks which model was used on a branch, wants to check whether a debugging loop converged or thrashed, wants to scan for frustration signals, wants to see how active vs idle time breaks down on a branch, wants to know how much work ran in subagents vs the main thread, or wants to link branches to PRs with comment counts. DO NOT TRIGGER when: the user wants token-cost or cache-efficiency analysis (use token-analyzer.py directly), or when the question is about a specific file's content rather than transcript-wide patterns.
disable-model-invocation: true
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

## Reading fail-seq output

```
Total runs: 12  Failing: 3 (25.0%)  Longest consecutive-failing streak: 2
Sequence: 0 0 5 0 0 0 3 0 0 0 0 0
```

- **Convergent** (expected): a spike followed by zeros — the signature of a root-cause fix. The sequence above is convergent.
- **Thrashing** (flag for review): oscillation like `8 6 9 7 8` with no sustained run of zeros — a model not closing on a fix.
- The `longest consecutive-failing streak` is the load-bearing metric. A streak of 1–2 is normal (fix lands next run). A streak of 5+ warrants a closer look at the sequence and what was happening between those runs.

## Caveats

- The `N failed` count is a coarse proxy: it matches any `N failed` in tool output, including pre-existing failures and intentional baseline runs. Treat the sequence view as the primary read; the aggregate rate is corroborating.
- Subagent (`isSidechain`) turns are excluded from `fail-seq` and `struggle`. Reviewer/Explore agents run on Sonnet but are not the debugging surface these subcommands measure.
- Durations from `duration` are wall-clock dominated by idle gaps. Look at `Active(min)`, not `Span(min)`.
- `pr-link` requires `gh` and network access. All other subcommands are local-only and make no writes.
- A model-vs-model comparison is only meaningful when there are multiple all-Opus and all-Sonnet execution branches. One or two branches per model is directional, not a controlled A/B.

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
```
