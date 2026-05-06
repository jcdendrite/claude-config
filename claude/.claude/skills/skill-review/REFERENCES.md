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

None of those conditions apply here. This repo is a single-user skill
catalog — each stow clone is independent, mis-fires are observed in
the same session, and the description can be tuned by reading and
testing directly. The skill set is small and curated. `skill-review`'s
deterministic checklist (`check-skill-length.sh` at 200 lines,
`require-skill-review.sh` at commit) already provides stronger
pre-commit signal than probabilistic evals for this workflow.

`skill-creator`'s body also conflicts with this repo's conventions in
two concrete places: it advocates 500-line skill bodies (its L92,
L96), while `check-skill-length.sh` caps at 200; and it uses a
conversational, first-person voice ("Cool? Cool.") that `skill-review`
explicitly rejects in favor of imperative second-person. Keeping it
enabled would present contradictory authoring guidance every time a
skill was edited.

**Mechanism:** the `skill-creator@claude-plugins-official` entry is
removed from `enabledPlugins` in `settings.json` entirely — not set to
`false`. In this repo, `false` entries are quick-flip handles for
plugins the user might want to enable for an occasional session;
removing the entry means there's no foreseeable re-enable use case.
If a future contribution to a broadly-shipped skill requires quantified
trigger-accuracy work, add `"skill-creator@claude-plugins-official": true`
back to `enabledPlugins` for that session.
