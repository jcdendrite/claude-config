# Case studies

Longer-form writeups of specific design decisions in this repo, with primary-source citations. Each study is its own page under [`case-studies/`](case-studies/). Sibling to [`design-decisions.md`](design-decisions.md), which carries the shorter-form decision records — a case study is the empirical record behind one of them.

## Index

- [**Worktree enforcement: hook vs. CLAUDE.md prose**](case-studies/worktree-enforcement.md) — is the worktree-enforcement hook over-engineered relative to carefully-pruned CLAUDE.md prose? Works through each failure mode from primary sources, and when prose-only is sufficient.
- [**Review gates and the agent-babysitting problem**](case-studies/review-vs-babysitting.md) — does a workflow of review gates and reviewer agents cut the agent-babysitting cost or just relocate it? Grounded in 946 hook denials and review catches across this repo's transcript corpus.
- [**check-runner: charter scoping over command-pattern hardening**](case-studies/check-runner.md) — the six operational findings behind `design-decisions.md` §10: five incidents that each tempted a command-string regex and were instead fixed by narrowing the agent's charter, plus a measurement that re-grounded why check-runner exists.
- [**Estimating effort by review surface, not implementation time**](case-studies/effort-estimation-review-surface.md) — does an LLM anchor effort estimates on human coding speed, and does encoding a "review surface, not hours" rule in the planning skills prevent it? Grounded in a corpus audit of 50,471 assistant text blocks.
