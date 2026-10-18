# `Explore` stays pinned to Sonnet: a Haiku locate-recall gate failed on precision

*2026-10-15.*

`claude/.claude/agents/Explore.md` keeps `model: sonnet`. A pre-registered, stratified recall-and-precision gate (`evals/measure_explore_recall.py`, K=5 samples per case per model, 130 scored samples) tested whether Haiku's locate-and-report output is trustworthy enough for `Explore`'s charter — this harness's only task shape structurally verifiable enough to even consider for Haiku. Haiku tied Sonnet's recall on grep-derivable search (0.995 vs. 1.000) and trailed by about four points on semantic search (0.917 vs. 0.953), both within the gate's 5-point term. It failed on precision: 0.931 pooled against the required 0.98 floor (roughly a 5-point gap), versus Sonnet's 0.986. It also failed the per-case term: one semantic-search sample returned every path with its leading directory segment dropped, producing a single case's 20-point recall cliff that Sonnet had no counterpart for in the same 25 samples. A locate agent's value depends on the parent trusting its output enough to skip re-verifying every path it names. A precision gap below the required floor plus a one-in-25 unusable-sample rate does not clear that bar. The pin and its paired routing fix (steering `general-purpose`'s locate-shaped dispatches to `Explore` instead) both stay unshipped. Full measurement in [`cost-levers-considered.md`](../cost-levers-considered.md)'s "From `haiku-routing-surfaces.md`" section.

`check-runner`, this repo's only prior production Haiku agent, was retired for inventing structured data from free-form text (`docs/case-studies/check-runner.md`). That failure shape (confabulation under summarization pressure) differs from this gate's (precision loss on open-ended search). Even so, both point to the same lesson: a locate-and-report-shaped bar is not evidence that Haiku's output is trustworthy for a different task shape, so each claim needs its own measurement.

This reopens only on a measured change: a Haiku release (the `haiku` alias resolves to a provider- and version-dependent model) that clears the same gate on a re-run. `evals/measure_explore_recall.py` and its fixture are kept runnable for exactly that re-run; nothing re-runs them automatically.

## Sources

- `docs/case-studies/check-runner.md` — the prior Haiku production agent's retirement.
- `.claude/plans/haiku-routing-surfaces.md` — full plan, gate mechanics, and the per-term measurement.
