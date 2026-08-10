# Targeted reads: measured, adopted narrowly, and why

*Part of the [claude-config case studies](../case-studies.md).*

**The problem.** An evaluation held outside this repo concluded that the one uncontested token saving on the read path is replacing whole-file `Read` calls with `offset`/`limit` reads around a located region, and recommended adding an instruction to global guidance. Its background assumed whole-file reads dominate, and it flagged its own headline percentage as computed against the wrong denominator — assistant *output* tokens, when a `Read` result is a *prompt* token.

**Question.** Does this repo's own corpus support adding a "read narrow" instruction, and if so, what should it say?

**Short answer.** Yes, but a much smaller one than proposed, because two of the premises do not survive measurement:

1. **Whole-file reads do not dominate by call count.** 45.6% of `Read` calls already carry `offset` or `limit` — roughly 3.5x the 12.8% adoption rate that the source evaluation cited as the bar an instruction-only intervention has to clear.
2. **The instruction already exists one layer down.** The built-in `Read` tool description reads, verbatim: *"When you already know which part of the file you need, only read that part. This can be important for larger files."* The measured 45.6% is adherence to that, not to nothing.

What survives is token mass, not call count: whole-file reads are 53.8% of calls but **76.8%** of `Read`-result tokens. So the added instruction is scoped strictly to the complement of the tool description's own rule — the case where you *don't* already know which part you need — and everything else proposed was dropped on the evidence.

## How this was measured

The `read-scope` subcommand of `claude/.claude/scripts/transcript-analysis.py` does one pass over the transcript corpus, producing every figure below from one scan.

```
transcript-analysis.py read-scope --config-dir <profile-1> --config-dir <profile-2> --config-dir <profile-3>
```

Snapshot taken 2026-08-10 across four config dirs — the default plus three additional account profiles. Once `~/.claude/transcript-config-dirs` is populated, the flags are unnecessary; `_resolve_cost_roots` consults `declared_transcript_roots()` and picks up every declared account. Honest limits:

- **Point-in-time, and the corpus moves faster than the effects being measured.** Two runs of the same command minutes apart differed by **481,294 tokens** of prompt-token growth (0.16%) — because the session doing the measuring writes large transcripts into the corpus it is scanning. That sets the floor on precision here: it is larger than the entire six-part denominator correction described below. Only classification *behavior* is reproducible, via `TestReadScope` and `TestScanReadScopeSession`.
- **The numerator is estimated; the denominator is measured.** `Read`-result tokens are `chars // 4`. Prompt-token growth comes from real `usage` fields. Every ratio mixing them inherits that. The self-consistent cross-check below (both sides `chars // 4`) is the control.
- **Undercounts nested workflow-agent transcripts.** `read-scope` reuses this file's shared session reader, which merges a session's direct `subagents/*.jsonl` children but not a further-nested `subagents/workflows/wf_*/agent-*.jsonl` shape. Every other subcommand has the same gap; widening it would silently change already-published figures elsewhere.
- **No control group, so no causal claim.** A staggered rollout across account profiles would give one and is technically reachable, but was declined: it would fragment the stowed config `install.sh` keeps uniform, and withhold guidance from real work to serve a measurement.
- **One engineer, one machine.** See "What this cannot tell you."

## The numbers

**22,687 `Read` calls**, all four config dirs combined.

| cohort | calls | share of calls | result tokens | share of Read tokens |
|---|---|---|---|---|
| targeted (`offset` and/or `limit`) | 10,342 | 45.6% | ~11,302,200 | 23.2% |
| whole-file (neither) | 12,201 | 53.8% | ~37,471,104 | **76.8%** |

The two shares do not sum to 100%: 129 calls carried no `file_path` at all (an `__unparsedToolInput` payload), so their scope is unknowable and they are excluded from both cohorts rather than silently filed under whole-file. Percentages divide by the full call census, so those 129 stay visible in the arithmetic. Also excluded from the size distribution and reported on their own lines: 535 error results, 28 non-text (image) results, 15 `pages` PDF reads, 0 unpaired calls.

**Where the mass sits.** Whole-file reads of 2,000+ estimated tokens are 6,237 calls holding **32,690,199 of 37,471,104 whole-file tokens (87.2%)**.

**Denominator.** `Read` results are **16.0%** of total prompt-token growth (305,503,283), where growth is the per-source-file, per-`sessionId`, compaction-reset, first-turn-excluded sum of positive per-turn context deltas. Assistant output tokens are the wrong denominator for this numerator — a `Read` result is something the model *reads*, not something it writes — which is the error the source evaluation flagged in itself; `read-scope` does not compute that ratio, so no figure for it is quoted here. Self-consistent cross-check, both sides `chars // 4`: `Read` results are **58.9%** of all tool-result tokens across every tool.

**Ceiling on benefit — gross 8.5%, net 8.1%.** Replacing every whole-file read above 2,000 tokens with a targeted read at the observed targeted-cohort mean (1,097 tok) saves 25,848,210 tokens, or **8.5% of prompt-token growth**. Netting the locate step that a targeted read requires — measured at **191 tokens per `Grep`/`Glob` call** — costs 1,191,267 and brings it to **8.1%**. Both are ceilings, not estimates: they assume every one of those reads could have been narrowed with no correctness loss, which is false.

**What was dropped on the evidence.** The source evaluation proposed a "re-read narrowly after an edit" clause. Repeat whole-file reads of the same path within one context window are 858 calls / ~1,486,323 tokens = **0.49% of prompt-token growth**, and 339 of them target `.output`/`.log`-shaped paths — polling a growing file, which is correct behavior. Not worth a line.

### The denominator correction that did not matter

The growth denominator is wrong in six ways if computed naively over a session's merged record list. All six were implemented and are fixture-pinned. On this corpus their aggregate effect is **−0.17%**, essentially all of it from partitioning by source file:

| definition | total |
|---|---|
| flat merged, no corrections | 301,774,970 |
| + per-source-file partition | 301,274,877 |
| + per-`sessionId` | 301,274,877 |
| + compaction reset | 301,274,877 |
| + skip absent `usage` | 301,274,877 |

Two corrections are exactly inert here, and the compaction reset is inert *by construction*: summing only positive deltas already discards the drop a compaction causes, so resetting the chain cannot change the sum. It stays in for corpora that contain the pattern, but it fixed no number here and is not claimed to have.

One corpus fact worth recording for anyone reimplementing this: **a subagent transcript is named for its agent, but its records carry the parent session's id** — 0 of 545 sampled subagent files had a stem matching their own records' `sessionId`, against 115 of 115 for main transcripts. An intermediate version of this subcommand used each file's stem as its session reference, which silently attributed zero growth to every subagent group and cut the measured total by 54% with the whole test suite still green. Chaining deltas per `sessionId`, rather than selecting one reference id per file, avoids the trap entirely.

## What this cannot tell you

The corpus is one engineer's, on one machine. The instruction ships to every stow consumer, on every repo they open. The measurement carries direct evidence against assuming it generalizes: across this engineer's own four account profiles, targeted-read share spans **33.2% to 48.2%** — a 15-point spread driven by nothing but which work each account does. A consumer whose work is weighted toward unfamiliar-codebase exploration, where whole-file reads are more often the right call, is not described by any number above.

A further asymmetry: **77.1% of whole-file-read tokens are inside subagents**, whose context is discarded on return rather than re-billed for the rest of the session. A token saved there is worth less than one saved on the main thread, so the realized benefit sits below the growth-denominated share.

## Decision

**Adopt the read discipline as one line of guidance. Decline the hook.**

The added line in `claude/.claude/CLAUDE.md` carries only what the built-in `Read` description does not — that when you *don't* know which part you need, finding out is cheap: `Grep` returns line numbers, `wc -l` bounds the file. It is scoped to a file already chosen, which is what keeps it from colliding with `subagent-delegation`'s mandate to dispatch codebase *discovery* rather than run it inline.

A `PostToolUse` `Read` hook was considered as an adherence counter and declined on two independent grounds. It is redundant — the transcript already records `offset`/`limit` on every call, which is how every figure here was derived, so `read-scope` *is* the counter. And it is self-defeating: advisory context injected on all 22,687 `Read` calls spends context to save context.

Placing the rule in `subagent-delegation/SKILL.md` was also declined. That skill's frontmatter excludes *"comprehension read feeding your own writing/review/design"* — the exact moment this rule needs to fire. A rule behind a trigger that excludes its own use case does not fire.

**Numeric revisit trigger.** Reconsider if any of the following becomes true, all re-derivable via `transcript-analysis.py read-scope`:

- Targeted-read share falls below **40%** of `Read` calls sustained over two weeks, against today's measured 45.6%. This is the source evaluation's own threshold, now measurable against a baseline rather than assumed.
- `Read` results exceed **20%** of prompt-token growth, against today's 16.0%.
- Whole-file reads of 2,000+ tokens exceed **90%** of whole-file-read tokens, against today's 87.2% — the mass concentrating further into the addressable tail.
- The locate step's mean cost exceeds **500 tokens/call**, against today's 191, which would erode the gross-to-net gap the ceiling depends on.

## Sources

- `claude/.claude/scripts/transcript-analysis.py` — `read-scope`, the subcommand producing every figure above.
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — `TestReadScope`, `TestScanReadScopeSession`.
- `.claude/plans/targeted-read-discipline.md` — the plan, including the assumption ledger and the four review rounds that corrected it.
