---
name: skill-review
description: >
  How to review and audit Claude Code skill files
  (`.claude/skills/**/SKILL.md`): frontmatter conventions, TRIGGER /
  DO NOT TRIGGER design, voice, length targets, the
  operational-vs-narrative content test, and the cross-reference vs
  duplication framework.
  TRIGGER when: editing or reviewing `.claude/skills/**/SKILL.md`;
  auditing trigger accuracy across a skill set; restructuring or
  pruning skills.
  DO NOT TRIGGER when: creating a brand-new skill from scratch (use
  `skill-creator`); editing CLAUDE.md or AGENTS.md (use
  `ai-instruction-and-memory-files`); editing `.lovable/*.md` (use
  `lovable-knowledge`); reviewing code that isn't a skill file.
user-invocable: false
---

# Skill Review — Architecture & Checklist

Complements `skill-creator` (scaffolds new skills, runs evals, tunes
description text). This skill applies a review checklist to skill-file
diffs.

## 1. What makes a skill load

A skill file is `claude/.claude/skills/<name>/SKILL.md`. Required
frontmatter:

- **`name`** — must match the directory name; the harness keys on it.
- **`description`** — loaded into every session for trigger matching.
  This is the only body content the harness sees until the skill fires.

Optional frontmatter:

- **`allowed-tools`** — comma-separated allowlist (e.g.
  `Read, Grep, Glob, Bash`). Omit to inherit the session's tools.
- **`user-invocable`** — `false` hides the skill from the slash-command
  menu but keeps it auto-triggerable from the description.

The harness loads only the description at startup; the body is fetched
on demand when triggers match. This means description text is
always-loaded context budget, body text is conditional. Frontmatter
overspend hurts more than body overspend.

## 2. Trigger design

Format the description with two parallel lists:

```
TRIGGER when: <specific situation>; <another>; <another>.
DO NOT TRIGGER when: <obvious misfire>; <adjacent skill's surface>; <out of scope>.
```

**Specificity sources, in priority order:**

1. **File globs** (`.claude/skills/**/SKILL.md`, `*.tsx`) — most
   reliable; the harness can match them exactly.
2. **Action verbs** (editing, auditing, restructuring, reviewing) —
   second-most reliable; pin the trigger to a *what-the-user-is-doing*
   shape.
3. **Context cues** (deciding which file a rule belongs in, debating
   length) — weakest; only fire when the verbs aren't enough.

**Tradeoff.** Over-broad triggers load the skill's body into
unrelated turns and steal context from the active task. Over-narrow
triggers leave the skill dormant when it should fire and the user
ends up restating what the skill already knows. Both fail; tune by
sampling real recent transcripts where the skill should have fired.

**DO NOT TRIGGER carries equal weight.** Name the adjacent skills
whose surfaces overlap. A `skill-review` description that says only
"editing skill files" without naming `skill-creator` will fire on
brand-new-skill scaffolding turns where `skill-creator` should own.

## 3. Voice and structure

- **Imperative second person.** "Review the change," "Apply the
  checklist," not "The reviewer reviews the change."
- **Declarative numbered items** for checklist content; the dispatcher
  routes by item number in some skills (see `code-review` Item
  ownership table).
- **Section headers are domain or step labels** ("Trigger design,"
  "Review checklist") — never "Introduction," "Background,"
  "Conclusion." The headers are skim navigation; padding headers add
  load without a routing payoff.
- **Tables for matrices, prose for narratives, bullets for parallel
  options.** A four-column comparison wants a table; a two-step
  decision wants prose.

## 4. Length and the behavior test

**Length targets:**

- Target under **200 lines per file**. Diminishing returns past 300.
- Attention decay hits the **middle** of long files ("lost in the
  middle"). Burying critical rules past line ~150 reduces their
  effective load.
- Prose-rule compliance tops out around ~70%. Structural tests and
  hooks hit 100%. When a rule can be encoded mechanically, prefer
  the mechanical enforcement.

**The behavior test.** For every line: does removing this line
change Claude's behavior on at least one realistic input? If no, cut.

**Lines that almost always pass the test:**

- Anti-pattern descriptions Claude can pattern-match against a diff.
- Rationale that arms Claude to judge edge cases not enumerated in
  the rule (the *why* lets the rule generalize).
- Trade-off framing that resolves an otherwise-ambiguous call.

**Lines that almost always fail the test:**

- "This skill was created after a production incident where..."
- "This is a contentious choice; reasonable engineers disagree..."
- Narrative case studies that retell a past PR.
- Audience-persuasion language ("It's important to remember that...").

When a skill is surfaced by a real incident, keep the failure mode
and the fix in the skill body; drop the incident's identity. (Repo
`CLAUDE.md` "When a skill is surfaced by real-world work, abstract
first" governs this for the public claude-config repo specifically.)

## 5. Operational vs narrative content

A skill body is operational instructions for Claude, not a design
document for a human reader. Replace narrative with imperative.

| Tone marker | Operational | Narrative (cut) |
|---|---|---|
| Subject | "Check whether..." | "We've found that..." |
| Tense | imperative present | past, retrospective |
| Audience | Claude, mid-task | a human reading cold |
| Specificity | concrete pattern | generalized lesson |

## 6. Cross-references vs duplication

**Cross-reference (default)** when another skill's triggers already
cover the file path or situation being edited. Example: `code-review`
points at `skill-review` for `.claude/skills/**/SKILL.md` review
rather than restating the skill-review checklist inline.

**Duplicate (defense-in-depth)** only when **all three** hold:

1. The content is critical — getting it wrong has real consequences.
2. The two locations reach Claude through different load paths
   (e.g. always-loaded CLAUDE.md vs on-demand skill body).
3. One path could silently fail (skill not triggered, file not loaded
   for a particular file type, etc.).

Otherwise, point at the canonical source. Two copies of the same rule
drift; one will go stale and the contradiction surfaces during review.

## 7. Review checklist

When reviewing a PR that touches `claude/.claude/skills/**/SKILL.md`:

1. **Frontmatter** — `name` matches directory; `description` present
   and contains both `TRIGGER when:` and `DO NOT TRIGGER when:`
   blocks. `allowed-tools` and `user-invocable` only if needed.
2. **Description scope** — the description's TRIGGER list matches
   what the body actually covers. An overpromising description fires
   on turns the body can't help with.
3. **Trigger specificity** — TRIGGER conditions use file globs or
   action verbs, not vague context cues alone. A skill that triggers
   on "thinking about X" is too soft to fire reliably.
4. **DO NOT TRIGGER coverage** — adjacent-skill surfaces are named
   explicitly. Audit by listing every nearby skill in the same
   domain and asking whether DO NOT TRIGGER says "use that one."
5. **Length** — under the 200-line target. Flag anything that drifts
   past 200 (and especially past 300) without a load-bearing reason.
6. **Behavior test per line** — every line should change Claude's
   behavior on at least one realistic input. Cut narrative,
   editorial meta-commentary, and incident retellings.
7. **Voice** — imperative second person; numbered items for
   checklists; section headers that label domains or steps.
8. **Cross-reference correctness** — referenced skill names exist;
   referenced section anchors (`§2`, `§3`) match the target file's
   structure. Renames in the target break these silently.
9. **Duplication justification** — content duplicated across skills
   passes the three-condition test in §6. Otherwise, replace the
   duplicate with a pointer.
10. **Redaction** — no real project, organization, codename, or
    private-tracker-ID references in examples or rationale. Use
    `PROJ-<digits>` or `TICKET-<digits>` for tracker-shaped
    placeholders. (Repo-specific; see repo-root `CLAUDE.md`.)
