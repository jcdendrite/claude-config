---
name: agent-review
description: >
  Audit of agent files: frontmatter, triggers, voice, length,
  duplication. TRIGGER when: authoring/reviewing a
  .claude/agents/*.md or plugins/*/agents/*.md change, or auditing
  trigger accuracy across the roster. DO NOT TRIGGER when: editing
  SKILL.md (use skill-review), editing CLAUDE.md/AGENTS.md/memory
  files (use ai-instruction-and-memory-files), or reviewing
  non-agent files.
user-invocable: false
---

# Agent Review — Architecture & Checklist

## 1. What makes an agent load

An agent file is `claude/.claude/agents/<name>.md` (stowed user-scope) or `plugins/<plugin>/agents/<name>.md` (plugin-scoped). The harness scans `.claude/agents/` and `~/.claude/agents/` recursively, and each plugin's `agents/` directory. Identity comes from the `name` frontmatter field — the filename does not have to match.

**Required frontmatter:**

- **`name`** — lowercase letters and hyphens; unique across the whole tree. Duplicates within one scope: the harness keeps one and discards the other without warning.
- **`description`** — what the agent does and when to dispatch it. The dispatcher reads this to decide delegation.

**Optional frontmatter:**

- **`tools`** — comma-separated allowlist (restrictive). When omitted, the agent inherits every tool from the main conversation. **Beware confusion with skill `allowed-tools`:** skill `allowed-tools` is additive; agent `tools` is restrictive. They have opposite defaults — mixing them silently breaks the cap.
- **`disallowedTools`** — comma-separated denylist. Applied before `tools` when both are present.
- **`model`** — `sonnet`, `opus`, `haiku`, a full model ID, or `inherit`. Defaults to `inherit`.
- **`maxTurns`** — integer cap on agentic turns. **Must be a YAML integer (`maxTurns: 20`), not a string (`maxTurns: "20"`)** — strings are silently ignored and the cap does not apply.
- **`permissionMode`** — `default`, `acceptEdits`, `auto`, `dontAsk`, `bypassPermissions`, or `plan`. Ignored for plugin subagents.
- **`mcpServers`** — list of MCP servers scoped to the agent. Ignored for plugin subagents.
- **`hooks`** — frontmatter-scoped lifecycle hooks. Ignored for plugin subagents.
- **`skills`** — skills to preload into the agent's context at startup (full body content is injected, not just the description).
- **`memory`** — `user`, `project`, or `local`. Enables a persistent memory directory.
- **`isolation`** — set to `worktree` for a temporary git worktree.
- **`color`** — one of `red`, `blue`, `green`, `yellow`, `purple`, `orange`, `pink`, `cyan`.
- **`background`** — `true` to always run in the background.
- **`effort`** — `low`, `medium`, `high`, `xhigh`, `max`; available levels depend on the model.
- **`initialPrompt`** — auto-submitted as the first user turn when the agent runs as the main session agent.

Description text is always-loaded context budget; agent bodies are lazy — paid only when the harness dispatches the agent. Overspend in the description costs more than overspend in the body.

## 2. Trigger design

Format the description with two parallel lists:

```
TRIGGER when: <specific situation>; <another>; <another>.
DO NOT TRIGGER when: <obvious misfire>; <adjacent agent or skill's surface>; <out of scope>.
```

**Specificity sources, in priority order:**

1. **File globs** (`.claude/agents/*.md`, `*.tsx`) — most reliable; the harness can match them exactly.
2. **Action verbs** (editing, auditing, restructuring, reviewing) — second-most reliable; pin the trigger to a *what-the-user-is-doing* shape.
3. **Context cues** (deciding which file a rule belongs in, debating length) — weakest; only fire when the verbs aren't enough.

**DO NOT TRIGGER carries equal weight.** Over-broad triggers dispatch the agent into turns its system prompt can't help with; over-narrow leaves it dormant. Name the adjacent agents and skills whose surfaces overlap.

**Executor-style carve-out.** Invocation-context agents whose description names a single dispatch shape — e.g., `code-writer` ("dispatch for delegated code changes") — may use narrower trigger language than routed reviewers. Do not relax trigger discipline for routed reviewer agents (`staff-*`, `ciso-reviewer`); their dispatcher matches on description shape and they fire across many surfaces.

## 3. Voice and structure

- **Imperative second person.** "Review the change," "Apply the checklist," not "The reviewer reviews the change."
- **Declarative numbered items** for checklist content.
- **Section headers are domain or step labels** ("Scope," "Review checklist") — never "Introduction," "Background," "Conclusion."
- **Tables for matrices, prose for narratives, bullets for parallel options.**

## 4. Length and the behavior test

Target under **200 lines per file** (diminishing returns past 300).

**The behavior test.** For every line: does removing this line change the agent's behavior on at least one realistic input? If no, cut.

**Lines that almost always pass the test:**

- Anti-pattern descriptions the agent can pattern-match against a diff.
- Rationale that arms the agent to judge edge cases not enumerated in the rule (the *why* lets the rule generalize).
- Trade-off framing that resolves an otherwise-ambiguous call.

**Lines that almost always fail the test:**

- "This agent was created after a production incident where..."
- "This is a contentious choice; reasonable engineers disagree..."
- Narrative case studies that retell a past PR.
- Audience-persuasion language ("It's important to remember that...").

## 5. Operational vs narrative content

Replace narrative with imperative. Agent bodies are operational instructions to the agent, not design documents.

| Tone marker | Operational | Narrative (cut) |
|---|---|---|
| Subject | "Check whether..." | "We've found that..." |
| Tense | imperative present | past, retrospective |
| Audience | the agent, mid-task | a human reading cold |
| Specificity | concrete pattern | generalized lesson |

## 6. Cross-references vs duplication

**Cross-reference (default)** when another skill's or agent's triggers already cover the file path or situation being edited. Example: `code-review` points at `agent-review` for `.claude/agents/*.md` rather than restating this checklist inline.

**Duplicate (defense-in-depth)** only when **all three** hold:

1. The content is critical — getting it wrong has real consequences.
2. The two locations reach the agent through different load paths (e.g. always-loaded CLAUDE.md vs on-demand agent body).
3. One path could silently fail (agent not dispatched, file not loaded for a particular file type, etc.).

If not all three hold, point at the canonical source.

## 7. Review checklist

1. **Frontmatter contract** — `name` matches the agent's identity slug; `description` present and contains both `TRIGGER when:` and `DO NOT TRIGGER when:` blocks. **`tools` vs `allowed-tools`:** agent files use `tools` (restrictive allowlist); skill files use `allowed-tools` (additive). Mixing them silently breaks the cap. Verify the field name matches the file type.

2. **Description scope** — the description's TRIGGER list matches what the system prompt actually covers. An overpromising description dispatches the agent into work it can't do.

3. **Trigger specificity** — TRIGGER conditions use file globs or action verbs, not vague context cues alone. **Executor-style exemption:** invocation-context agents (`code-writer`) may use narrower trigger language; do not relax for routed reviewer agents (`staff-*`, `ciso-reviewer`).

4. **DO NOT TRIGGER coverage** — adjacent-agent and adjacent-skill surfaces are named explicitly; verify every domain-adjacent agent and skill appears.

5. **`disallowedTools` recognition** — any `disallowedTools` entry is a recognized field name (not a misspelling of `tools`), and its value lists actual tool names.

6. **`maxTurns` integer validation** — `maxTurns: 20`, not `maxTurns: "20"`. Strings are silently ignored; the cap will not apply.

7. **`model` field discipline** — check against `~/.claude/CLAUDE.md` "Model Routing" for current guidance, not this line. `Explore`'s pin (`claude/.claude/agents/Explore.md`) is a repo-owned override rather than a per-dispatch request. An agent dispatched from many call sites, or dispatched at high frequency from even one, does not pin `opus` — the pin would leak into work that didn't ask for it. `plan-architect` is the deliberate exception — its `opus` pin is bounded per call site. Re-verify that bound still holds before extending the exception to another agent (`docs/design-decisions.md` §30, §37).

8. **Length** — under the 200-line target. Flag anything that drifts past 200 (and especially past 300) without a load-bearing reason.

9. **Behavior test per line** — every line should change the agent's behavior on at least one realistic input. Cut narrative, editorial meta-commentary, and incident retellings.

10. **Voice** — imperative second person; numbered items for checklists; section headers that label domains or steps.

11. **Cross-reference correctness** — referenced agent and skill names exist; referenced section anchors (`§2`, `§3`) match the target file's structure. Renames in the target break these silently.

12. **Duplication justification** — content duplicated across agents passes the three-condition test in §6. Otherwise, replace the duplicate with a pointer.

13. **Redaction** — no real project, organization, codename, or private-tracker-ID references in examples or rationale. Use `PROJ-<digits>` or `TICKET-<digits>` for tracker-shaped placeholders. (Repo-specific; see repo-root `CLAUDE.md`.)

14. **Behavioral-equivalence audit (compression diffs)** — for any diff that removes or shortens lines, before declaring the review complete:

    | Removed/shortened text | Surviving line | Behavior-preserving? |
    |---|---|---|
    | (quote) | (cite) | Y — (quote surviving line) / N — (what is lost) |

    Y requires citing the specific surviving line that carries the same instruction. "The meaning is the same" is not sufficient. Any N is a finding; restore the instruction.

    Compression that drops a forbidden-action callout ("do not X"), an imperative directive, an escape-hatch exception, or a verification command fails this audit even when the summary reads as equivalent.

15. **Reviewer-agent output contract** (`staff-*` and `ciso-reviewer` only) — verify the agent carries `Write` in its frontmatter `tools` list, and has a `### File-based output` section in its `## Output format` body. Both are required for `findings_path` dispatch from the code-review dispatcher. An agent missing either falls back to full inline output, which risks a heredoc-abort failure on large findings.

16. **Platform-genericness** — the reviewed agent's own body prescribes no tool-invocation verb as a mandatory instruction step, and cites no source-material bias anchor (a named team's or org's practice given as the reason a rule holds, instead of the rule's own rationale); extract every hit mechanically — do not rely on noticing during read-through. Vendor or product names naming a `staff-*` persona's recognized domain landscape (Terraform, Snowflake, Fivetran, Sentry, and similar) are not violations: `staff-*` bodies are domain-expert review personas whose job is enumerating that landscape, so it is load-bearing domain knowledge in the reviewed agent's own body, not platform lock-in. Flag any hit not justified as deliberate or illustrative; agent bodies have no `<skill>-<project>` layer to relocate content to, so an unjustified hit is generalized or removed from the reviewed agent's body directly. (Repo-specific; the tool-verb and bias-anchor rationale applies to agent bodies for the same reason it applies to skill bodies — see repo-root `CLAUDE.md` "Global skill bodies stay platform-agnostic.")

17. **`effort` field discipline** — check against `~/.claude/CLAUDE.md` "Model & Effort Routing" for the current per-agent effort-tier policy, not this line — that policy can change independently of this checklist. `Explore`'s `low` pin is the canonical fast-lookup case; an agent with no `effort:` pin at all silently inherits whatever effort the invoking session runs at, so every agent in the roster should carry an explicit pin unless a documented reason exists to leave it unset.
