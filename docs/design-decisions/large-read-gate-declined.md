# No gate on large file reads: the reachable cost is too small and reading is the reviewers' job

*2026-11-01.*

This repo does not deny large whole-file reads, and does not redirect them to a cheaper worker model. The design considered was a `PreToolUse` hook that denies a whole-file `Read` or Bash read above a line threshold and sends the agent to a script that asks a worker model about the file. It was declined on measurement, for this repo's own workload.

## What was measured

Figures come from `transcript-analysis.py read-cost --this-repo`, which refuses any corpus wider than this repository on one account. Absolute corpus totals are omitted on purpose. Shares are of total spend in cache-read-token equivalents. A read's weight is one cache write plus one cache read per later turn in its context window, so main-thread tokens are charged for every turn they are re-sent.

**The main thread has almost nothing to gate.**

- Whole-file reads over 350 lines: 41 calls.
- Of those, 17 target a file the main thread itself edited later, where the agent needed the exact content.
- The other 24 are the only reads a gate could help, as far as the main thread's own edits show. Those 24 reads' share of total spend rounds to 0.0%, before subtracting the cost of the worker's answer and the denied turn. Edits made through Bash are invisible, which overstates never-edited. Failed edits and edits after a compaction count as edits, which understates it (`docs/transcript-analysis.md` `read-cost` Known limits).
- A threshold of 800 lines catches 2 calls.
- Long-line files mean a token threshold catches more: reads of 2,000+ tokens are 43% of main-thread whole-file read calls. That population includes files later edited.

**Reads are not what subagents cost.**

- Subagents' whole-file reads are 6.0% of spend, and all their reads are 12.5%, so most subagent spend is something other than reads. Subagents are 75.7% of total spend.
- Repeat reads inside one agent are 0.1% of that 6.0%.
- 68.3% of it is paths the main thread read whole-file at any point in the session, or a subagent earlier in file-name order read whole-file.
- Most of that is independent reviewers reading independently, which is the point of dispatching them, and no mechanism shares a `Read` result between agents.
- 88.0% of it is large files the agent never edited. For a read-only reviewer that describes the job, not waste.

The specialist reviewer agents' whole-file reads are about 3.9% of spend. That set is the seven `staff-*` agents plus `ciso-reviewer`, `comment-discipline-reviewer` and `skill-fidelity-reviewer`. It excludes every other agent type. Spend there follows how many reviewers run and what each carries. Capping that fan-out is a separate lever, already rejected in `docs/cost-levers-considered.md`.

## Why the gate would not work anyway

- **It clears on any `offset` or `limit`.** Exempting targeted reads is necessary because edits need them. So the natural response to a deny is `limit: 2000`, which returns the same content and wastes a turn.
- **It cannot see intent.** A whole-file read is right when the task is the whole file. The read-sweep plan shelved in #1330 stopped for the same reason: its false positives depend on why a read happened, which transcripts do not record.
- **The tool already says to narrow.** The `Read` tool description tells the agent to read only the part of a file it needs when it already knows which part (`docs/case-studies/targeted-read-discipline.md`).

## Why the cheaper worker does not fit

- **Parents re-checked a cheap worker's output.** `check-runner`, a cheap worker absorbing verbose command output, was retired because its misbehavior survived repeated hardening and parents kept re-running its delegated commands inline and voicing distrust (`docs/case-studies/check-runner.md` § "Retirement (2026-06-23)").
- **A cheaper worker is not a free one.** Worker spend moves rather than disappears, and a saved token says nothing about answer accuracy.
- **A script that reads files itself bypasses the read gates.** `deny-env-reads.sh` and `deny-credential-file-reads.sh` see tool calls, not what a subprocess opens, and the content then leaves the machine.
- **The lighter primitive exists.** Locate-and-report reads already route to `Explore` under `subagent-delegation`, inside the hooks.

## When to reopen

Reopen when the never-edited main-thread whole-file reads in either the 350-line or the 800-line cohort the report prints reach 1% of total spend.

Run `read-cost` again if the workload changes. The result binds this repo's mix, which is review-heavy with short files. A codebase of very large source files is not described by these numbers, and that is the case where this design could pay.

## Limits

- One price ratio is applied across models, so Opus-pinned `plan-architect` is understated relative to Sonnet agents. Per-model cache-read exceptions, the fast-mode multiplier and the inference-geo multiplier are not applied either, so every share is a token-equivalent share, not a dollar share.
- Each read is priced as one 5-minute cache write plus cache reads, which understates some reads and overstates others (`docs/transcript-analysis.md` `read-cost` Known limits).
- `read-cost` measures the `Read` tool only. The considered design also covered Bash reads, which are not measured, so the reachable set is understated.
- The main/subagent shares in `docs/case-studies/targeted-read-discipline.md` come from `read-scope` runs that counted `api-request-blob` records, and are not re-derived here.

## Sources

- `claude/.claude/scripts/transcript_analysis/read_cost.py` — the report behind every figure above.
- `docs/case-studies/targeted-read-discipline.md` — the prior decision to keep read narrowing as one line of guidance.
- `docs/case-studies/check-runner.md` — the prior cheap-worker retirement.
- `docs/private-project-redaction.md` § "Publishing a tooling measurement" — the scope bar these figures meet.
