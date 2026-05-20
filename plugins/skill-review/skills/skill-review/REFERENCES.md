# skill-review — References

## Anthropic subagent frontmatter contract

Primary source: <https://code.claude.com/docs/en/sub-agents.md>. Verbatim
quotes for the rules encoded in §1 (agent frontmatter) and §7 item 1b:

- **Required fields:** "name (required), description (required)" — only
  these two are required; everything else is optional.
- **Name vs. filename:** "The name is used to invoke the subagent and
  doesn't need to match the filename." (Inverse of the SKILL.md rule.)
- **`tools` (not `allowed-tools`):** "tools (optional, defaults to all
  inherited tools): Comma-separated list of allowed tools."
- **`disallowedTools` precedence:** "disallowedTools (optional): Tools
  the subagent cannot use. This is enforced even if listed in tools."
- **`model` values:** "model (optional, defaults to inherit): Specify
  which model to use. Options: sonnet, haiku, opus, or a specific
  model name like claude-sonnet-4-5. Use inherit to use the same model
  as the parent."
- **`maxTurns`:** "maxTurns (optional): Maximum number of turns the
  subagent can take before stopping."

The TRIGGER / DO NOT TRIGGER framing for agent descriptions is a
**repo convention**, not a docs requirement — the docs describe the
description as plain English "when Claude should delegate." Item 1b
of §7 in SKILL.md applies the convention to routed-reviewer agents
only; executor-style agents (e.g., `check-runner`) are exempt.

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

**Update:** skill evals have been adopted as a standalone local harness
(`evals/run_skill_evals.py`) that adapts `run_eval.py`'s stream-json detection
mechanism. The `skill-creator` *plugin* remains disabled — the mechanism is
adopted but the plugin's voice and length conventions still conflict with this
repo. The key distinction: this repo's harness measures the trigger fidelity
of existing skills at local/manual cadence, not optimizing descriptions via
the `run_loop.py` search loop.

The original conditions have changed: the skill set has grown to ~23 skills
used across many independent projects, meaning mis-fires now happen in
sessions the owner doesn't observe. `evals/run_skill_evals.py` covers this
without CI security risks (local auth, no `--dangerously-skip-permissions`),
without per-token budget (Max-plan OAuth), and without flaky CI signals (the
output is a human-read pass-rate report, not a binary gate).

`skill-creator`'s body still conflicts in two concrete places: it advocates
500-line skill bodies (its L92, L96), while `check-skill-length.sh` caps at
200; and it uses a conversational, first-person voice ("Cool? Cool.") that
`skill-review` explicitly rejects. Keeping it enabled would present
contradictory authoring guidance every time a skill was edited.

**Mechanism:** the `skill-creator@claude-plugins-official` entry is
removed from `enabledPlugins` in `settings.json` entirely — not set to
`false`. In this repo, `false` entries are quick-flip handles for
plugins the user might want to enable for an occasional session;
removing the entry means there's no foreseeable re-enable use case.
