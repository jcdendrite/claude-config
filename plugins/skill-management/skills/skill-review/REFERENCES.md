# skill-review — References

## Why skill-creator is disabled in this repo

`skill-creator` (Anthropic's `claude-plugins-official` plugin) and
`skill-review` (this repo's local skill) have a deliberate lane split:
`skill-creator` owns the *authoring* loop — scaffolding a new skill
directory, running eval/benchmark pipelines, and optimizing the
`description` field via a `run_loop.py` search loop. `skill-review`
owns the *audit* lane — the 11-item pre-commit checklist and the
behavioral-equivalence table that `require-skill-review.sh` enforces
at commit. The boundary was hand-tightened in commit `618c9ad`, which
added `skill-creator` to `skill-review`'s DO NOT TRIGGER block to
prevent dual-fire on every skill-edit turn.

`skill-creator`'s eval/benchmark machinery is the right tool when a
skill ships at scale: diverse phrasings from many users mean trigger
accuracy is hard to verify by reading, mis-fires happen in sessions
you don't observe, and a 20-query eval run pays for itself in reduced
regressions. The `run_loop.py` description optimizer makes sense when
you genuinely can't tell whether a wording change improves accuracy
without measuring it.

`skill-creator` stays disabled: its 500-line-body convention (its L92, L96)
and first-person voice conflict with `check-skill-length.sh`'s 200-line cap
and this repo's voice rules. Trigger-fidelity evals instead run via
`evals/run_skill_evals.py`, a local adaptation of `run_eval.py`'s
stream-json detection, without using `skill-creator`'s `run_loop.py`
optimizer or enabling the plugin. The skill set has grown to ~23 skills
used across many independent projects. Mis-fires now happen in sessions
the owner doesn't observe, which is what makes a local eval run worth
its cost. This covers it without:
- CI security risk (local auth, no `--dangerously-skip-permissions`)
- per-token budget (Max-plan OAuth)
- flaky CI signal (a human-read pass-rate report, not a binary gate)

**Mechanism:** the `skill-creator@claude-plugins-official` entry is
removed from `enabledPlugins` in `settings.json` entirely, not set to
`false`. `false` entries are reserved as quick-flip handles for
occasional re-enable; this one has no foreseeable re-enable use case.
