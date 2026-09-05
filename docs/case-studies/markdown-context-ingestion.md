# Markdown context-ingestion cost: measured, and why no retrieval mechanism ships

*Part of the [claude-config case studies](../case-studies.md).*

**The problem.** This repo and its stow consumers lean heavily on markdown — always-loaded `CLAUDE.md` files, dozens of skill bodies, and a large `docs/` tree — and its own prose cross-references documentation by section hundreds of times without any way to retrieve one section instead of a whole file. Nobody had measured what markdown actually costs before proposing a markdown-focused Language Server Protocol (LSP) integration as the fix.

**Question.** Does this repo's own evidence support building a markdown retrieval mechanism (an LSP integration, a section-extract script, or a `skills:`/`context: fork` preload change)? Separately, have the pre-registered [targeted-read-discipline.md](targeted-read-discipline.md) revisit triggers moved since their 2026-08-10 snapshot?

**Short answer.** No retrieval mechanism ships. No LSP request returns document text (`documentSymbol` and siblings return ranges and labels only — a Language Server Protocol constraint, not a Claude Code one), so a markdown language server would still need a `Read` to retrieve the section it located. Two lighter primitives already do the job with zero new infrastructure:

1. `Grep '^#{1,6} '` on the target file, then `Read` with `offset`/`limit` around the returned line range.
2. `Read` with `offset`/`limit` directly against a cited `§N`, when the citation already names the target.

Both are already covered by the global `CLAUDE.md` rule "Locate before a whole-file read," adopted in `targeted-read-discipline.md`. The largest measured item — the always-loaded `CLAUDE.md` baseline — is a real cost, but it is not a retrieval problem; it is capped by an existing commit-time line-length hook and owned by a separate trimming effort. The revisit triggers have not crossed their thresholds in a directly comparable run, with one exception flagged below for a wider rerun rather than treated as fired.

## How this was measured

Three independent measurement threads, of different reproducibility:

**1. Static markdown inventory — freshly re-derived today, against this worktree's `HEAD` (`80df8ce`).**

```
wc -c CLAUDE.md; wc -l CLAUDE.md
wc -c claude/.claude/CLAUDE.md; wc -l claude/.claude/CLAUDE.md
find claude/.claude/skills -name "SKILL.md" -print0 | xargs -0 wc -c
find docs -type f -name "*.md" -print0 | xargs -0 wc -c
```

**2. The revisit-trigger evaluation — freshly re-derived today.**

```
transcript-analysis.py read-scope --this-repo
```

`--this-repo` is `read-scope`'s documented minimization control (`transcript_analysis/scope.py:47-65`), scoping the scan to this repo's own project directories rather than the default scope of every declared account root. This is a genuinely different population than the all-repos scope `targeted-read-discipline.md`'s 2026-08-10 baseline used (see that case study for its account scope) — see "What this cannot tell you" for what that means for comparability.

**3. The cost re-weighting and waste-heuristic findings — one-off scans from this plan's own discovery phase, not re-executed in this dispatch.** No standing `transcript-analysis.py` subcommand implements a byte-turns (result-bytes × remaining-turns) weighting or a reference-proximity waste heuristic, and building one is a `claude/` code change outside this docs-only phase. These figures are carried forward from `.claude/plans/markdown-context-ingestion-cost.md`'s assumption ledger, cited by row number, and are not independently re-verified here — the same caveat this register already applies to other one-off scans (e.g. the "Git-diff output in main-session context" entry in [`cost-levers-considered.md`](../cost-levers-considered.md)).

## The numbers

### Static inventory (today)

| Surface | Bytes | Lines |
|---|---|---|
| Repo-root `CLAUDE.md` | 9,687 | 163 |
| Global `claude/.claude/CLAUDE.md` | 32,454 | 184 |
| Skill bodies (`SKILL.md`, 29 files) | 353,240 | — |
| `docs/*.md` | 1,000,259 | — |

These differ from the figures quoted in the plan's own Context section (42,141 / 418,140 / 993,261 combined) because the plan's own inventory was taken against a base that `main` has since moved past. The plan's row 26 names this drift directly, which is why this case study re-derives each number rather than copying it forward.

**The 200-line cap is not close to breaching.** `check-claude-md-length.sh:66` hard-codes the limit at 200 lines for both `CLAUDE.md` files. The global file is at 184 today, up from 146 immediately after `trim-global-claude-md.md`'s relocation landed (that 146 figure is [already published](../cost-levers-considered.md) in this register, not re-derived here since it is a historical snapshot). That is +38 lines while staying inside budget — evidence the cap sits above the desired steady state, not evidence of enforcement failure, and not something this plan changes: lowering `limit_for` is owned by the in-flight `claude-md-audience-restructure` effort. Flagged here as a risk to watch, nothing proposed.

### Revisit-trigger evaluation, this-repo scope

| Trigger (from `targeted-read-discipline.md`) | 2026-08-10 baseline (all repos — see linked source for account scope) | Today, this-repo scope | Threshold | Crossed? |
|---|---|---|---|---|
| Targeted-read share of `Read` calls | 45.6% | 55.6% (2,988 / 5,371 calls) | falls below 40% | No — direction reassuring |
| `Read`-result tokens as share of prompt-token growth | 16.0% | 17.7% | exceeds 20% | No |
| Whole-file reads ≥2,000 tokens as share of whole-file-read tokens | 87.2% | 92.6% | exceeds 90% | **Nominally yes — see caveat below** |
| Locate-step (`Grep`/`Glob`) mean cost per call | 191 tok | ~482 tok (1,103 calls, ~532,009 tok) | exceeds 500 tok | No, but closer to the threshold than the baseline |

**The third row is not a confirmed trigger fire.** The 2026-08-10 baseline was measured across this engineer's full multi-account, all-repos corpus; today's run is scoped to this repo's own sessions only, which include plan-authoring and documentation-heavy work unrepresentative of the wider population the trigger was defined against. Comparing a repo-scoped population against an all-repos threshold is the same denominator-mismatch risk this plan's Approach section names for the markdown-only figures — it is not the same error, since this run does cover all `Read` calls in all scopes as the trigger requires, but the *population* still differs. Recorded as worth a wider, all-accounts rerun before treating it as fired; not run here, since the hard minimization control (`--this-repo`) is what this dispatch used and widening was not judged necessary for the other three rows.

### Cost re-weighting and waste heuristic (one-off, not re-executed here)

- **The always-loaded baseline is the single largest measured item.** Weighted in byte-turns (a read's byte count × the turns remaining in its context window, since a loaded file is re-sent on every subsequent turn), the always-loaded `CLAUDE.md`/skill/doc baseline was measured at 4,417,798,518 byte-turns against 3,142,092,419 for all main-thread markdown `Read` calls combined — 1.41×. (Plan row 8.)
- **All 812 sampled multi-read subagent dispatches favored observed spread-out reads over a turn-1 preload of every skill body; zero favored preload.** This refutes preloading important skills at dispatch time as a cost win under byte-turns weighting — turn-1 loading maximizes the remaining-turns multiplier for the whole payload. One axis was left unmeasured: preload avoiding a *second* full read of the same file. (Plan row 14.)
- **The waste heuristic found no evidence of waste, which the plan is explicit is not the same as evidence of no waste.** 69% of 155 large whole-file markdown reads sampled were inconclusive — no near-term reference either way — and the heuristic cannot separate a wasted read from a legitimate comprehension read. This is corroborating support only; it does not carry the decision to decline a retrieval mechanism on its own. (Plan row 3.)
- **Compaction rate is not measurable from the transcript schema.** No `compact_boundary` record was observed in the scanned corpus, and the available detector (used by the `handoff-ratio` subcommand) cannot be distinguished from ordinary cache invalidation. Recorded as a null result, not investigated further here — a background fact from the plan's own investigation (plan Given `G3`), not a number this dispatch re-derived.
- **The session-start attachment baseline is capped at n=10.** Only 10 sessions in the corpus carry the full startup attachment bundle needed to measure it, confirmed independently twice during the plan's discovery phase. The sessions that would widen it have already aged out of retention. (Plan Given `G4`.)

### Dropped: the code-review/SKILL.md read-count discrepancy

The plan's own assumption ledger (row 16) flagged an unresolved conflict between two one-off scans: one reporting 63 corpus-wide reads of `code-review/SKILL.md`, another reporting 44 whole-file plus 30 partial (74) reads within `skill-fidelity-reviewer`'s own dispatches specifically. Neither scan has a committed, rerunnable implementation, so this dispatch cannot adjudicate the discrepancy by rerunning either one, and reconstructing an equivalent scan from raw transcript data is outside a docs-only phase. Per the plan's own instruction, both figures — and anything derived from them, including the ballpark "44 × 63,714 ≈ 2.80 MB" figure the plan's row 16 also flags — are **dropped from this case study, not published**. The qualitative finding this evidence was gathered to support (a read-then-discard ordering defect in `skill-fidelity-reviewer`'s name resolution) is unaffected and is addressed in this plan's Phase B, a separate dispatch.

## What this cannot tell you

- **Two of the three measurement threads above are one-off scans with no committed implementation.** Their numbers cannot be reproduced by a later reader the way `read-scope`'s figures can; treat their precision the way this register already treats other one-off entries (e.g. the "Git-diff output in main-session context" section of [`cost-levers-considered.md`](../cost-levers-considered.md)).
- **The revisit-trigger evaluation is scoped to this repo's own sessions, not the original multi-account, all-repos population the 2026-08-10 baseline used.** A repo-scoped read is a different — narrower, and in this case more doc-heavy — population than "everything this engineer worked on." Three of the four comparisons above are directionally consistent with the baseline; the fourth (whole-file concentration) is not, and needs a wider rerun to know whether that is real drift or a population artifact of this repo's own read mix.
- **Corpus provenance.** The revisit-trigger figures above, like the original `targeted-read-discipline.md` baseline they are compared against, are drawn from a transcript corpus that spans multiple account profiles on one engineer's machine, some of which hold private engagement work. Consistent with that case study and with this repo's own redaction rules for content derived from that corpus, this case study does not publish an account count, which profiles are dormant versus active, any per-account breakdown, a timing pattern, or any figure that would be mappable to a single engagement's existence or activity level. Where a figure could only be produced by disclosing one of those, it was omitted rather than published.
- **Compaction and the n=10 session-start baseline remain unmeasured**, for the reasons stated above (plan Givens `G3` and `G4`) — this case study records that gap rather than closing it.
- **One engineer, one machine, one point in time**, for the same reasons `targeted-read-discipline.md` names: no control group, and the corpus itself is smaller and shaped differently than the population any given stow consumer would bring to the same measurement.

## Decision

**No retrieval mechanism ships. Publish the measurement; make no code change in this phase.**

A markdown LSP or MCP section server is declined outright, not merely left unjustified: no LSP request returns document text, so even a built markdown language server would locate a section and still require a `Read` to retrieve it — it is a locate step wearing a plugin, a third-party server, and a `.lsp.json` dependency inert in cloud sessions. The two lighter primitives named above already do the locate-then-read job in two calls with zero new infrastructure, and are already mandatory via the global `CLAUDE.md` "Locate before a whole-file read" rule.

A section-extract script or new markdown-parsing dependency is declined for the same reason one level down: the lighter primitives succeed, the waste heuristic found no evidence of waste for such a tool to recover, and this repo declares no npm toolchain and no markdown parser today — adding either is its own new-dependency decision this plan does not make.

`context: fork`, a `skills:` preload, and switching agents from `Read` to `Skill` invocation are all declined: an invoked skill body and a read file both persist in-conversation identically until compaction, so switching between them saves no in-turn bytes; omitting `Skill` from a subagent's `tools:` disables invocation entirely with no selective-restriction surface; and the 812-dispatch preload measurement above found no case favoring a turn-1 preload over observed, as-needed reads.

The always-loaded baseline is the largest measured lever and is explicitly not addressed here. It sits inside its 200-line cap today (184/200 for the global file) and trimming it is scoped to a separate, already-in-flight effort. This case study's role is to name it plainly as the largest item rather than imply the line cap already contains it on every axis — line count and byte weight move independently, and only the former is capped.

## Sources

- `.claude/plans/markdown-context-ingestion-cost.md` — the plan, including the assumption ledger this case study cites by row number for every figure it did not re-derive itself.
- `claude/.claude/scripts/transcript-analysis.py` — `read-scope`, the subcommand producing the revisit-trigger figures above.
- `claude/.claude/hooks/check-claude-md-length.sh` — the commit-time 200-line cap.
- [`docs/case-studies/targeted-read-discipline.md`](targeted-read-discipline.md) — the read-discipline measurement and its four pre-registered revisit triggers.
- [`docs/cost-levers-considered.md`](../cost-levers-considered.md) — the register entry for this plan, and the `trim-global-claude-md.md` entry cited for the 146-line historical baseline.
