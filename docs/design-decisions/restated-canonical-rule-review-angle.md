# A `comment-discipline-reviewer` angle catches record-style docs restating a canonical rule

*2026-09-18.*

A record-style doc (a decision record, ADR, RFC, or postmortem) that
restates a rule stated canonically in another file, instead of citing it,
is invisible to the ordinary review loop. The motivating case:
`docs/design-decisions/round3-consult-verdict-routing.md` restated
`code-review/SKILL.md`'s verdict-routing and spawn-obligation rules
near-verbatim, across two separate commits months apart on the same file,
and only an Opus-level `plan-architect` consult caught the duplication.
`comment-discipline-reviewer.md` gains a new angle, `Restated canonical
rule`, to catch this pattern at ordinary review cost instead.
`claude/.claude/agents/comment-discipline-reviewer.md` § "Core review angles"
states the angle's predicate, its canonical-home location procedure, and its
remedy directly; this entry doesn't restate them.

This operationalizes rather than supersedes
[single-source-of-truth-rule.md](single-source-of-truth-rule.md) § "Single source of truth elevated to a canonical CLAUDE.md rule".

Three supporting routes make the angle reachable without a dispatch of its
own:

- `code-writer.md`'s self-review domain table gains a row routing durable
  in-repo docs to `comment-discipline-reviewer`.
- `code-review/SKILL.md`'s item 12a gains a matching one-line index
  bullet, pinned to the agent's angle headers by
  `test_item_12a_index_matches_agent_angle_headers` in
  `claude/.claude/hooks/tests/test_design_decision_files.py`.
- `.claude/rules/design-decisions.md` § "Design-decision files" gains an authoring-time bullet. This entry doesn't restate its content.

Routing design-decision docs to `ai-instruction-and-memory-files` was
rejected: that skill audits instruction and memory files, and these docs
are records, so the genre does not match.

A mechanical lexical-duplication test (shingle or n-gram overlap against
the skill corpus) was rejected. The reasons are in
`.claude/plans/design-decision-ssot-duplication-check.md` § "Assumption ledger".

## Sources

- [single-source-of-truth-rule.md](single-source-of-truth-rule.md) — the
  rule this angle operationalizes; not superseded.
- `claude/.claude/agents/comment-discipline-reviewer.md` § "Core review angles" — the angle's predicate, location procedure, and remedy.
- `claude-skills/skills/code-review/SKILL.md`'s item 12a — the index
  bullet listing the angle.
- `.claude/rules/design-decisions.md` § "Design-decision files" — the authoring-time cite-don't-restate convention.
- `.claude/plans/design-decision-ssot-duplication-check.md` — full
  assumption ledger, mechanism-by-mechanism reasoning, and verification
  steps.
