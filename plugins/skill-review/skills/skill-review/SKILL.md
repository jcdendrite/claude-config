---
name: skill-review
description: >
  Review and audit of SKILL.md files: frontmatter conventions, trigger
  design, voice, length targets, and cross-reference vs duplication
  policy. TRIGGER when: reviewing a .claude/skills/**/SKILL.md change or
  auditing trigger accuracy across a skill set. DO NOT TRIGGER when:
  authoring or iterating on a skill (use skill-creator), editing
  CLAUDE.md/AGENTS.md (use ai-instruction-and-memory-files), or
  reviewing non-skill files.
user-invocable: false
---

# Skill Review — Architecture & Checklist

Fires in **review and audit** contexts (applying a checklist to a
skill-file change). `skill-creator` covers the authoring loop —
scaffolding, evals, benchmarking, description tuning. The lane split
is by context, not by output type: skill-review stays out of authoring
turns to avoid dual-firing with `skill-creator`'s broad "edit existing
skill" claim.

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
on demand. Description text is always-loaded context budget; body
text is conditional. Frontmatter overspend hurts more than body overspend.

Target description shape: one summary sentence (≤80 chars), then `TRIGGER when:` and
`DO NOT TRIGGER when:` clauses. Do not enumerate body-topic headings in the summary —
body sections already enumerate; the summary routes.

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

**DO NOT TRIGGER carries equal weight.** Over-broad triggers load
skill body into unrelated turns; over-narrow leave it dormant. Name
the adjacent skills whose surfaces overlap — a `skill-review`
description that doesn't list `skill-creator` under DO NOT TRIGGER
will dual-fire on every authoring turn.

## 3. Voice and structure

- **Imperative second person.** "Review the change," "Apply the
  checklist," not "The reviewer reviews the change."
- **Declarative numbered items** for checklist content; the dispatcher
  routes by item number in some skills (see `code-review` Item
  ownership table).
- **Section headers are domain or step labels** ("Trigger design,"
  "Review checklist") — never "Introduction," "Background,"
  "Conclusion."
- **Tables for matrices, prose for narratives, bullets for parallel
  options.** A four-column comparison wants a table; a two-step
  decision wants prose.

## 4. Length and the behavior test

Target under **200 lines per file** (diminishing returns past 300).
Prose-rule compliance tops out around ~70%; structural tests and
hooks hit 100%. When a rule can be encoded mechanically, prefer
mechanical enforcement.

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

## 5. Operational vs narrative content

Replace narrative with imperative. Skill bodies are operational instructions for Claude, not design documents.

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

If not all three hold, point at the canonical source.

## 7. Review checklist

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
   explicitly; verify every domain-adjacent skill appears.
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
11. **Behavioral-equivalence audit (compression diffs)** — for any
    diff that removes or shortens lines, before declaring the review
    complete:

    | Removed/shortened text | Surviving line | Behavior-preserving? |
    |---|---|---|
    | (quote) | (cite) | Y — (quote surviving line) / N — (what is lost) |

    Y requires citing the specific surviving line that carries the
    same instruction. "The meaning is the same" is not sufficient.
    Any N is a finding; restore the instruction.

    Compression that drops a forbidden-action callout ("do not X"),
    an imperative directive, an escape-hatch exception, or a
    verification command fails this audit even when the summary reads
    as equivalent.

## Step — Record review completion

If the review is clean (table above has no N rows, no other
blockers), record completion by running this command exactly once:

<!-- HOOK_TEST_FIXTURE: skill-review-marker-write — the hook-alignment test suite reads this exact fenced block from this file (plugins/skill-review/skills/skill-review/SKILL.md) to verify it matches require-skill-review.sh's marker layout. Do not duplicate the recipe elsewhere; the test re-reads it from here. -->
```
~/.claude/scripts/marker.sh write skill-review
```

The pathspecs `claude/.claude/skills/**/SKILL.md` and `plugins/*/skills/**/SKILL.md` are encoded inside `marker.sh write skill-review` so the marker matches what `require-skill-review.sh` checks — covering both stowed skills and project-scoped plugin skills.
