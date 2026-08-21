# Root-cause analysis: brand-new-session context bloat

## Context

A brand-new Claude Code session in this repo (representative of every repo on this machine, since `claude-config` is stowed to `~/.claude`) starts with a much larger baseline context than expected, before the user types anything. The user had already isolated a lead from a prior debugging session: the built-in `Artifact` and `Workflow` tools are always loaded even though the user never uses either. This plan runs the root-cause-analysis playbook to (a) measure every contributor to that baseline, not just the one lead, and (b) fix whatever is actually configurable, documenting the rest.

## Approach

The two largest contributors are the `Artifact` and `Workflow` built-in tools' full schemas, loaded eagerly into every session instead of deferred like ~78 other optional tools — and both have a real, documented settings toggle with an environment-variable equivalent. The fix is exporting two environment variables from a personal, machine-wide shell profile (outside this repo) plus a design-decisions.md entry and README note recording the finding for future reference and other stow users.

### Assumption ledger

```
Root: a brand-new session's baseline context is far larger than the two
tools (Artifact, Workflow) the user already suspected can fully explain,
so the fix must be grounded in a full measured breakdown, not the single
lead.
Givens: Claude Code's set of built-in tools and their eager-vs-deferred
loading mechanism is fixed platform behavior — beyond reach: it is
Anthropic's harness code, not something this repo's config can rewrite.

Row 1 [mechanism]: measure every eagerly-loaded context contributor by
direct wc -c/-w on the exact text shown in this session's own system
prompt, rather than estimating — anchors: root — matches root-cause-
analysis Stage B (verify tool ingestion / work from a complete read, not
a guess).
Row 2 [assumption]: `Artifact` tool description is 9,432 chars / 1,543
words [verified: wc -c/-w on the literal tool-definition text from this
session's own system prompt] — anchors: row1
Row 3 [assumption]: `Workflow` tool description is 19,371 chars / 2,861
words [verified: wc -c/-w on the literal tool-definition text from this
session's own system prompt] — anchors: row1
Row 4 [assumption]: the other 12 eagerly-loaded built-in tools combined
are 15,157 chars / 2,514 words [verified: wc -c/-w on the literal
tool-definition text from this session's own system prompt] — anchors:
row1
Row 5 [assumption]: ~78 deferred tool names (all MCP tools plus several
built-ins: CronCreate, WebFetch, WebSearch, SendMessage, etc.) plus the
harness's own (truncated) MCP server instructions text together cost only
4,497 chars [verified: wc -c on the literal deferred-tool-listing text
from this session's own system prompt] — anchors: row1 — confirms
deferral is the cheap path already used for the large majority of tools.
Row 6 [assumption]: global `~/.claude/CLAUDE.md` is 27,039 chars / 4,170
words and repo-root `CLAUDE.md` is 12,709 chars / 1,747 words [verified:
wc -c/-w on the literal file contents shown in this session's own system
prompt] — anchors: row1
Row 7 [assumption]: the available-agent-types listing is 11,207 chars and
the available-skills listing is 10,614 chars [verified: wc -c on the
literal listing text from this session's own system prompt] — anchors:
row1
Row 8 [mechanism]: `disableArtifact` (settings.json boolean, equivalently
`CLAUDE_CODE_DISABLE_ARTIFACT=1`) disables the Artifact tool — anchors:
row2 — [verified: code.claude.com/docs/en/settings, cross-checked against
the published schemastore.org/claude-code-settings.json, both fetched
independently this session and returning matching key name, type, and
description]. No lighter primitive exists for this: `permissions.deny`
gates *calling* a tool, and its documentation nowhere claims it also
removes the tool's schema from the system prompt, so it doesn't
address token cost the way a "disable the tool" setting is documented to.
Row 9 [mechanism]: `disableWorkflows` (settings.json boolean, equivalently
`CLAUDE_CODE_DISABLE_WORKFLOWS=1`) disables workflow execution — anchors:
row3 — [verified: raw JSON text of schemastore.org/claude-code-settings.json,
fetched this session, showing the literal `"disableWorkflows": {"type":
"boolean", "description": "Disable workflow execution...", "default":
false}` schema entry]. Same reasoning as Row 8 for rejecting
`permissions.deny` as the lighter alternative.
Row 10 [assumption]: setting `disableArtifact`/`disableWorkflows` removes
the tool's schema from the system prompt (not just blocks calling it)
[unverified] — anchors: row8, row9 — the settings docs say "disable the
tool" but do not explicitly state the token-budget effect; verify via
`/context` before/after per this plan's Verification section before
treating the ~7,200-token reclaim as confirmed.
Row 11 [assumption]: no settings.json key equivalent to `skillOverrides`
exists for the available-agent-types listing (no way to reduce its
~2,800-token cost) [verified: schemastore.org/claude-code-settings.json
searched this session for "agentOverride", "agentListingBudget",
"agentDescriptionBudget" — zero occurrences of all three] — anchors: row7
Row 12 [assumption]: an initial research pass on these settings (a
`claude-code-guide` subagent) returned an output the harness itself
flagged as containing instruction-shaped content, and separately claimed
a `--tools` CLI flag that a direct fetch of code.claude.com/docs/en/commands
found no evidence of [verified: direct WebFetch of code.claude.com/docs/en/commands
this session, explicitly returning "no mention of a --tools CLI flag"] —
anchors: root — every specific settings claim in this plan (Rows 8-11) was
independently re-verified against primary sources after that subagent
returned, per this repo's verify-sources practice; the `--tools` claim is
not carried into this plan.
```

`~/.claude/settings.local.json` is not a real Claude Code scope — the documented settings-file precedence lists exactly `Managed`, `CLI args`, `Local` (`.claude/settings.local.json`, resolved to the *current repository's root*, not the user's home directory), `Project` (`.claude/settings.json`, also repository-scoped), and `User` (`~/.claude/settings.json`, global — the same file this repo's stow install symlinks to `claude/.claude/settings.json`) [verified: two independent fetches of code.claude.com/docs/en/settings, one an explicit search for the literal string `~/.claude/settings.local.json` returning zero occurrences].

**Why environment variables, not any settings.json:** the preference ("I never use Artifacts or Workflows") should hold across every repo on the machine, not one repo at a time — engineer-confirmed, rejecting a per-repo `settings.local.json` outright ("that means artifacts have to be disabled for each of the dozens of repos on this machine. Absolutely not."). The only genuinely global scope, `User` (`~/.claude/settings.json`), is for a stow install this repo's own shared, tracked `claude/.claude/settings.json` — writing a personal preference there makes it every other stow consumer's default too, which the user separately confirmed is not intended (engineer-confirmed: not a repo-wide default). Both settings have documented environment-variable equivalents — `CLAUDE_CODE_DISABLE_ARTIFACT=1` / `CLAUDE_CODE_DISABLE_WORKFLOWS=1` — and Claude Code reads shell-exported environment variables at every launch regardless of which repository the session starts in [verified: code.claude.com/docs/en/env-vars, fetched this session]. The fix is exporting both from a personal shell profile outside this repo (this engineer's private `workstation-setup` repo, mirroring the existing `CLAUDE.local.md` personal-preference pattern), not any Claude Code settings file.

## Critical files

- **This engineer's private `workstation-setup` shell profile** (untracked, not part of this repo/branch) — add `export CLAUDE_CODE_DISABLE_ARTIFACT=1` and `export CLAUDE_CODE_DISABLE_WORKFLOWS=1`.
- **`docs/design-decisions.md`** — §28 records this investigation's measured breakdown, the two confirmed settings, and the environment-variable placement.
- **`docs/skills.md`** — one-line cross-reference from "Tuning the skill-listing budget for your project" to the design-decisions.md entry; three other sites in this file also reference `.claude/settings.local.json` correctly (repo-scoped, not `~/.claude/settings.local.json`).
- **`README.md`** — a "Context budget: disabling Artifact/Workflow" note for other stow users, since the ~7,200-token reclaim is real for anyone who doesn't use either tool. The adjacent "Machine-specific overrides" section documents that personal overrides live in a repo's own `.claude/settings.local.json`, not a global scope.

No code changes; no PR-required change touches `claude/.claude/settings.json` itself, since the fix is a personal preference, not a repo-wide default.

## Verification

1. In a real (non-subagent) Claude Code session, before touching any settings, run `/context all` and note the total baseline and the `Artifact`/`Workflow` line items (Row 10 is currently unverified — this step confirms or refutes it).
2. Export both environment variables from the shell profile.
3. Start a brand-new session in any repo, run `/context all` again, and confirm `Artifact`/`Workflow` no longer appear as separate line items and the total baseline dropped by roughly the measured ~7,200 tokens.
4. If a future task genuinely needs Workflow (multi-agent orchestration) or Artifact (publishing), temporarily unset `CLAUDE_CODE_DISABLE_WORKFLOWS`/`CLAUDE_CODE_DISABLE_ARTIFACT` for that one shell session rather than editing the profile back and forth.
5. `/code-review` and `/plan-review` per this repo's standard pipeline for the `docs/design-decisions.md`, `docs/skills.md`, and `README.md` diffs before PR handoff.

## Out of scope

- **Trimming CLAUDE.md content** (global ~6,760 est. tokens + project ~3,177 est. tokens). Real, measured contributor, but deliberately authored engineering-judgment prose — an editorial call the user hasn't asked for here. Recorded as a finding only.
- **Reducing the available-agent-types listing** (~2,800 tokens). No platform lever currently exists (Row 11) — a candidate for an upstream Anthropic feature request, not something this repo can fix today.
- **Further skill-listing reduction** (~2,650 tokens). Already actively managed via `skillOverrides` (12+ skills already `name-only`/`off`); any further reduction needs a per-skill judgment call this task wasn't asked to make.
- **Exhaustive per-MCP-server token accounting.** The visible slice (deferred tool names + the harness's own truncated instructions text) is already the cheap path (~1,100 tokens measured); a full accounting would require reading `~/.claude.json`, which CLAUDE.md's Safety section explicitly forbids (secrets-shaped file).
