# Auto mode

Auto mode replaces per-action permission prompts with a background classifier
that evaluates each tool call before it runs, blocking anything irreversible,
destructive, or targeted outside your environment. See the
[engineering deep dive](https://www.anthropic.com/engineering/claude-code-auto-mode)
and the [permission modes reference](https://code.claude.com/docs/en/permission-modes)
for how the two-layer pipeline works. For the high-level overview of what this
repo adds on top, see the [README](../README.md#auto-mode).

## Requirements

- **Plan:** Max, Team, Enterprise, or Anthropic API. Not available on Pro, or
  on Bedrock, Vertex, or Foundry.
- **Model:** Auto mode requires a supported *session* model — the eligible set
  is plan-dependent (see the
  [permission modes reference](https://code.claude.com/docs/en/permission-modes)
  for the authoritative list). Opus is eligible on all qualifying plans; Sonnet
  is additionally eligible on Team, Enterprise, and API, but **not on Max**.
  This repo ships `opusplan` as the default, which routes auto mode to Sonnet
  during execution — on Max that produces "unavailable for this model." The
  `claude-auto` wrapper described below handles this automatically.
- **Claude Code:** a recent release — check `claude --version` against the
  [permission modes reference](https://code.claude.com/docs/en/permission-modes).

## Activating

Press **Shift+Tab** in the CLI to cycle through modes until `auto` appears,
then accept the one-time opt-in prompt. To start directly in auto mode, use the
`claude-auto` wrapper shipped by this repo:

```bash
claude-auto                          # defaults to Opus (eligible on all plans)
claude-auto "summarize the open PRs"  # positional prompt passes through
ANTHROPIC_MODEL=sonnet claude-auto   # Sonnet override (Team/Enterprise/API only)
```

The wrapper resolves the model mismatch between `opusplan`'s Sonnet execution
and auto mode's session-model requirement. On Team, Enterprise, and API plans,
Sonnet is eligible for auto mode, so `claude --permission-mode auto` also works
directly — the wrapper is most useful on Max, where only Opus qualifies.
`ANTHROPIC_MODEL` is Claude Code's built-in model env var and applies to all
invocation forms.

To make auto mode the default, add to `~/.claude/settings.json`:

```json
{
  "permissions": {
    "defaultMode": "auto"
  }
}
```

`defaultMode` sets the *mode*, not the *model* — the session-model requirement
above still applies regardless of how auto mode is activated.

## Hard-floor deny rules

`settings.json` in this repo ships a `permissions.deny` list that runs *before*
the classifier and cannot be overridden by any `autoMode.allow` entry. These
close gaps the classifier's default block list doesn't cover:

| Rule | What it closes |
|---|---|
| `Bash(sudo *)`, `Bash(sudo)` | Privilege escalation — turns the `sudo` prohibition in `CLAUDE.md` into a hard block |
| `Read(**/.env)`, `Read(**/.env.local)`, `Read(**/.env.local.*)`, `Read(**/.env.production)`, `Read(**/.env.production.*)`, `Read(**/.env.development)`, `Read(**/.env.development.*)`, `Read(**/.env.staging)`, `Read(**/.env.staging.*)`, `Read(**/.env.test)`, `Read(**/.env.test.*)` | Local secret reads — hard floors on the well-known secret-bearing variants; the classifier won't flag in-working-directory reads as exfiltration |
| `Read(**/credentials.json)` | Cloud provider credential files (AWS CLI, GCP service accounts, etc.) |

The `deny-env-reads.sh` PreToolUse hook covers `.env.*` variants not listed
above. It allows the three conventional non-secret template suffixes
(`.env.example`, `.env.template`, `.env.sample`) while denying everything else,
including symlinks whose resolved target's basename matches a denied pattern.

These rules apply in all permission modes, not only auto mode.

## What to put in `settings.local.json`

The classifier trusts only the working repo and its configured remotes by
default. Add `autoMode.environment` to `~/.claude/settings.local.json`
(gitignored) to declare which infrastructure is yours, reducing false positives
on routine operations:

```json
{
  "autoMode": {
    "environment": [
      "$defaults",
      "Organization: <org name>. Primary use: <use case, e.g. software development, security consulting>.",
      "Source control: github.com — only repos this developer is a collaborator on. Do not push to other organizations.",
      "Trusted domains: <domains your work regularly reaches, e.g. supabase.com, vercel.com, api.example.com>",
      "Additional context: <regulated industry, multi-tenant infrastructure, compliance constraints if any>"
    ]
  }
}
```

`"$defaults"` splices in the built-in trust list at that position. Omit it only
if you intend to replace the defaults entirely — doing so silently drops all
built-in block rules including force-push and `curl | bash` protection. See the
[danger note in the config reference](https://code.claude.com/docs/en/auto-mode-config#override-the-block-and-allow-rules).

Keep project names, internal hostnames, and private domain names in
`settings.local.json`. Do not put them in the committed `settings.json`.

Start minimal and expand reactively: run `claude auto-mode config` to see your
effective config, and check `/permissions → Recently denied` after the first
few sessions to find legitimate operations the classifier is blocking.

## Broad allow rules drop in auto mode

When auto mode activates, Claude Code silently drops `permissions.allow` rules
that grant arbitrary code execution:

- Blanket wildcards: `Bash(*)`, `PowerShell(*)`
- Wildcarded interpreters: `Bash(python3:*)`, `Bash(node:*)`, and similar
- Package-manager run commands

Check your `settings.local.json` for entries matching these patterns — those
operations will route to the classifier instead of auto-approving. Narrow rules
like `Bash(npm test)` carry over unchanged. Dropped rules are restored when you
leave auto mode.

## Subagent delegation under auto mode

Auto mode anchors the session to one model for its entire lifetime — there is
no plan-mode-to-execution model switch the way `opusplan` provides. On Max that
anchor is Opus (the only auto-eligible session model), so every subagent that
*inherits* the parent model runs on Opus too.

Subagent model is resolved in this order — the first that applies wins:

1. `CLAUDE_CODE_SUBAGENT_MODEL` environment variable (global override)
2. The `model` parameter on the `Agent` dispatch
3. The `model:` frontmatter in the agent's definition file
4. The parent / main-conversation model (inherited)

The routinely-dispatched built-in subagents resolve as follows:

| Agent | Model under an Opus auto-mode parent | Why |
|---|---|---|
| `Explore` | Haiku | Pinned by Claude Code; read-only search |
| `staff-*`, `ciso-reviewer` | Sonnet | `model: sonnet` frontmatter in `~/.claude/agents/` |
| `check-runner` | Haiku | `model: haiku` frontmatter |
| `code-writer` | Sonnet | `model: sonnet` frontmatter |
| `general-purpose` | **Opus (inherited)** | No model of its own — falls through to the parent |

So dispatching `general-purpose` for whole-file discovery or research from an
Opus auto-mode parent runs that work on Opus — roughly 5x the per-token cost of
Sonnet. To keep it off Opus, pass an explicit `model: sonnet`
on the `Agent` dispatch; resolution step 2 overrides the inherited parent at
step 4.

Since a session cannot reliably tell whether it is in auto mode, treat this as
unconditional: always dispatch `general-purpose` with an explicit `model`. See
the Model Routing section of the global `CLAUDE.md`.

Do **not** reach for `CLAUDE_CODE_SUBAGENT_MODEL` to solve this. It sits at
resolution step 1 and overrides *every* subagent's model — including
`check-runner`'s deliberate Haiku pin and the `staff-*` reviewers' Sonnet pin.
The per-dispatch `model` parameter is the targeted instrument; the env var is a
blunt global hammer.

## Inspection and tuning

```bash
claude auto-mode defaults   # print built-in environment, allow, and soft_deny rules
claude auto-mode config     # print effective config with your settings applied
claude auto-mode critique   # get AI feedback on your custom rules
```
