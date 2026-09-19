# A `comment-discipline-reviewer` angle catches design-decision docs restating a canonical rule

*2026-09-18.*

`docs/design-decisions/round3-consult-verdict-routing.md` restated
`code-review/SKILL.md`'s verdict-routing and spawn-obligation rules
near-verbatim, across two separate commits months apart on the same file,
and the duplication itself was caught only by an Opus-level
`plan-architect` consult — never by the ordinary review loop.
`comment-discipline-reviewer.md` gains a seventh review angle,
`Restated canonical rule`, to catch this pattern at ordinary review cost
instead.
`claude/.claude/agents/comment-discipline-reviewer.md` § "Core review angles" states the angle's predicate, its canonical-home location
procedure, and its remedy directly; this entry doesn't restate them.

This operationalizes rather than supersedes
[single-source-of-truth-rule.md](single-source-of-truth-rule.md), which
rejected a `/code-review` checklist item that would have been "a second
copy of an always-loaded rule on a surface that can drift from it," while
accepting item 9 (repeated logic) as an operationalization of the same
principle for code. The new angle takes item 9's route — a named surface,
a named predicate, and a named remedy, none of which appear in CLAUDE.md's
own single-source-of-truth bullet — so the prior rejection doesn't apply
to it.

Two supporting routes make the angle reachable without a dispatch of its
own. Route 1: `code-writer.md`'s self-review domain table now routes
durable in-repo docs (any markdown file) to
`comment-discipline-reviewer`. Route 2: `code-review/SKILL.md`'s item
12a gains a matching one-line index bullet, pinned to the agent's ordered
angle headers by `claude/.claude/hooks/tests/test_design_decision_files.py`'s
`test_item_12a_index_matches_agent_angle_headers`, which compares each
bullet's leading name against the corresponding header in sequence, so
the index can't silently drift stale.
`.claude/rules/design-decisions.md` § "Design-decision files" gains a matching authoring-time bullet; this entry doesn't restate its content
either.

Routing design-decision docs to `ai-instruction-and-memory-files` was
rejected: that skill audits instruction and memory files, and these docs
are records, so the genre does not match.

A mechanical lexical-duplication test (shingle or n-gram overlap against
the skill corpus) was rejected:

- The defect is semantic, so a paraphrase evades a lexical threshold
  while remaining the same defect.
- Legitimate short quotation is already the corpus norm and would trip
  the same threshold.
- No threshold could be grounded without a corpus measurement this
  decision had no tooling to run.

## Sources

- [single-source-of-truth-rule.md](single-source-of-truth-rule.md) — the
  rule this angle operationalizes; not superseded.
- `claude/.claude/agents/comment-discipline-reviewer.md` § "Core review angles" — the angle's predicate, location procedure, and remedy.
- `claude-skills/skills/code-review/SKILL.md`'s item 12a — the index
  bullet that dispatches the angle.
- `.claude/rules/design-decisions.md` § "Design-decision files" — the authoring-time cite-don't-restate convention.
- `.claude/plans/design-decision-ssot-duplication-check.md` — full
  assumption ledger, mechanism-by-mechanism reasoning, and verification
  steps.
