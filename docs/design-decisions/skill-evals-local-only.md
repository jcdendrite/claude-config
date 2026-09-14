# Skill evals run locally only; a CI eval harness stays declined

*2026-08-29. Formerly `docs/design-decisions.md` §35.*

`evals/run_skill_evals.py` measures a skill's declared behavior by launching `claude -p` under the operator's own Claude Code subscription auth and reports a per-case pass rate a human reads. Wiring the same harness into GitHub Actions was evaluated and declined. A CI runner *can* authenticate — `claude -p` accepts an `ANTHROPIC_API_KEY` — so the deciding ground is cost, not reachability:

- Every sample is a full headless session.
- Cost scales as K samples × cases per skill.
- `disposition-fidelity` adds about four `claude -p` calls per sample on top of that.
- All of it bills per token, off-subscription.

Two secondary grounds stand independently:

- Triggering is probabilistic, so a single-sample binary pass/fail is flaky.
- A public-repo workflow would need `--dangerously-skip-permissions` over PR-authored content.

`evals/README.md`'s "Why local only — never CI" section holds the full statement and is the site to update when one ground changes. The same posture covers `evals/measure_subagent_model_resolution.py`. The substitute for CI coverage is a manual pre-merge run against the skill the change touches, with the pass rate recorded in the PR description.
