# Auto mode

Auto mode replaces per-action permission prompts with a background classifier
that evaluates each tool call before it runs, blocking anything irreversible,
destructive, or targeted outside your environment. See the
[engineering deep dive](https://www.anthropic.com/engineering/claude-code-auto-mode)
and the [permission modes reference](https://code.claude.com/docs/en/permission-modes)
for how the two-layer pipeline works. For the high-level overview of what this
repo adds on top, see the [README](../README.md#auto-mode).

## Requirements

- **Plan:** Available on every plan — this is feature eligibility, not
  which mode a session starts in (see "Activating" below for that). On Team
  and Enterprise, an Owner can turn availability off org-wide by setting
  `permissions.disableAutoMode` to `"disable"` in managed settings.
- **Model:** Auto mode requires a supported *session* model — the eligible set
  is provider-dependent, not plan-dependent (see the
  [permission modes reference](https://code.claude.com/docs/en/permission-modes)
  for the authoritative list). On the Anthropic API and Claude Platform on AWS,
  Opus 4.6+, Sonnet 4.6+, or Fable 5 qualify. On Amazon Bedrock, Google Cloud's
  Agent Platform, Microsoft Foundry, and the Claude apps gateway, only Sonnet
  5, Opus 4.7+, or Fable 5 qualify. Auto mode also anchors the session to one
  model for its entire lifetime — there's no plan-mode-to-execution switch the
  way `opusplan` provides, so `opusplan` itself isn't a valid session model for
  it. This repo ships `sonnet` as the default, which already satisfies that
  requirement; the `claude-auto` wrapper described below is still useful for
  starting auto mode on a different model in one step, or if you've set
  `opusplan` as your own default.
- **Claude Code:** a recent release — check `claude --version` against the
  [permission modes reference](https://code.claude.com/docs/en/permission-modes).

## Activating

On Pro, Max, and Team plans, in a terminal or the VS Code extension, auto
mode is Claude Code's built-in starting mode already — no activation step
needed — as of Claude Code v2.1.228 (macOS, Linux, WSL) or v2.1.233 (native
Windows). The first time the built-in default starts a session in auto mode,
Claude Code shows a one-time notice, not a prompt requiring acceptance. On an
older Claude Code version, an Enterprise plan, a Claude Console API key
account, `claude -p` / the Agent SDK, or another provider (Amazon Bedrock,
Google Cloud's Agent Platform, Microsoft Foundry, Claude Platform on AWS, the
Claude apps gateway), the built-in starting mode is still Manual; see the
"Which mode a session starts in" section of the
[permission modes reference](https://code.claude.com/docs/en/permission-modes)
for the full starting-mode precedence table.

To pick a specific model in one step, or to start auto mode explicitly where
it isn't the built-in default, use the `claude-auto` wrapper shipped by this
repo:

```bash
claude-auto                           # start auto mode on Sonnet
claude-auto --model opus              # start auto mode on Opus
claude-auto "summarize the open PRs"  # positional prompt passes through
```

The wrapper resolves the mismatch between `opusplan` (a plan-mode/execution
model pair) and auto mode's requirement for one concrete session model —
relevant if you've set `opusplan` as your own default; this repo's shipped
default, `sonnet`, already satisfies auto mode's requirement directly. It
takes the same `--model` flag as `claude` and passes it through untouched. With
no `--model`, it uses `ANTHROPIC_MODEL` if that is set, and `sonnet` otherwise
— the alias resolves to the latest Sonnet, which auto mode accepts on every
provider. That last step is a flat fallback, not a compatibility check: naming
no model gets you Sonnet even when your configured default was already
eligible. The tradeoff is deliberate — the wrapper can't read which model your
settings resolve to, and always landing on an eligible one beats failing to
start against a default like `opusplan`. Name the model explicitly whenever you
care which one you get. `claude --permission-mode auto` also works directly
once your default is already a single eligible model.

Where auto mode isn't the built-in default — Enterprise, a Claude Console API
key account, an older Claude Code version, or another provider — make it the
default for your own sessions by adding to `~/.claude/settings.json`:

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
| `Read(**/credentials.json)`, `Read(**/.credentials.json)` | Cloud provider credential files (AWS CLI, GCP service accounts, etc.) |
| `Bash(brew install *)`, `Bash(brew tap *)`, `Bash(brew reinstall *)`, `Bash(gem install *)`, `Bash(cargo install *)`, `Bash(go install *)`, `Bash(gh extension install *)`, `Bash(mas install *)`, `Bash(pipx install *)`, `Bash(apt-get install *)`, `Bash(apt install *)`, `Bash(yum install *)`, `Bash(dnf install *)`, `Bash(apk add *)`, `Bash(zypper install *)` | Package installs with no restore-command collision — a bare literal is always an install, never a routine dependency restore. The `curl \| bash` classifier rule this complements is a *soft* block that user intent can clear; these rules cannot be cleared regardless of what the conversation says |
| `EnterPlanMode` | Agent-initiated plan mode entry — plan mode escalates all downstream subagent dispatches to Opus regardless of `model:` pins (see "Subagent delegation under plan mode" below). A human's `Shift+Tab`, `/plan` prefix, and `defaultMode` entry paths are untouched — this is the repo's first bare tool-name deny entry, which removes the tool from the session rather than blocking a call pattern |

The `deny-env-reads.sh` PreToolUse hook covers `.env.*` variants not listed
above. It allows the three conventional non-secret template suffixes
(`.env.example`, `.env.template`, `.env.sample`) while denying everything else,
including symlinks whose resolved target's basename matches a denied pattern.
`deny-network-installs.sh` covers the manager/verb shapes a flat literal
can't express — see the "network-install guard" section in
[`docs/security-hardening.md`](security-hardening.md).

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
no plan-mode-to-execution model switch the way `opusplan` provides. Every
subagent that *inherits* the parent model runs on whatever that anchor model
is.

Subagent model resolution follows this **requested** order — not a
guarantee outside auto mode's own resolution path either; see "Subagent
delegation under plan mode" below for the measured mechanism and its
falsification test.

1. `CLAUDE_CODE_SUBAGENT_MODEL` environment variable (global override)
2. The `model` parameter on the `Agent` dispatch
3. The `model:` frontmatter in the agent's definition file
4. The parent / main-conversation model (inherited)

This repo ships a same-named override, `claude/.claude/agents/Explore.md`,
which replaces the built-in `Explore` before resolution applies at all —
see "Subagent delegation under plan mode" below for how its pin holds up
under measurement.

The routinely-dispatched built-in and repo-shipped subagents' `model:`
pins are requests competing with resolution step 4 (parent inheritance) —
treat the table below as what each agent *asks for*:

| Agent | Requested model | Why |
|---|---|---|
| `Explore` | Sonnet | `claude/.claude/agents/Explore.md` override |
| `staff-*`, `ciso-reviewer` | Sonnet | `model: sonnet` frontmatter in `~/.claude/agents/` |
| `code-writer` | Sonnet | `model: sonnet` frontmatter |
| `general-purpose` | **Inherited from parent** | No model of its own — falls through to the parent |

If the session is anchored to Opus (`claude-auto --model opus`, or a
manual Shift+Tab into auto mode from an Opus session), dispatching
`general-purpose` without an explicit model runs that work on Opus — roughly
5x the per-token cost of Sonnet. Pass an explicit `model: sonnet` on the
`Agent` dispatch to request Sonnet instead; resolution step 2 is a request
that competes with step 4, not a guaranteed override.

Since a session cannot reliably tell whether it is in auto mode, or which
model that session is anchored to, treat this as unconditional: always
dispatch `general-purpose` with an explicit `model`. See the Model Routing
section of the global `CLAUDE.md`.

Do **not** reach for `CLAUDE_CODE_SUBAGENT_MODEL` to solve this. It sits at
resolution step 1 and overrides *every* subagent's model — including the
`staff-*` reviewers' Sonnet pin. The per-dispatch `model` parameter is the
targeted instrument; the env var is a blunt global hammer.

## Subagent delegation under plan mode

Plan mode is a separate axis from auto mode — a session can be in plan mode
whether or not it is anchored via `--model auto`, and the two combine
independently. Plan mode forces subagent dispatches to Opus regardless of a
`model:` frontmatter pin or an explicit `model` param on the `Agent`
dispatch, and this is confirmed independent of the parent's own model, not
just correlated with it. A corrected re-scan of `staff-*`/`ciso-reviewer`
dispatches with a Sonnet-declared pin, a Sonnet-family parent turn, and
`permissionMode == "plan"` at dispatch time found 131 matching dispatches,
129 of which resolved to Opus; 12 of those 131 already carried an explicit
`model: sonnet` param, and all 12 still resolved to Opus. A falsification
test ruled out the obvious confound — that, at measurement time, this repo's
`opusplan` default made plan mode and an Opus-anchored parent nearly
synonymous — by isolating 178 non-plan-mode dispatches from Opus-anchored
parents: 178/178 still resolved to Sonnet, matching the pin. See
[`case-studies/plan-mode-model-resolution.md`](case-studies/plan-mode-model-resolution.md)
(lines 54 and 56 for the corrected re-scan) for the full investigation,
primary-source citations, and rejected mitigations (`ExitPlanMode` timing,
`CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS`).

No instruction-layer mitigation is known. Pass an explicit `model` on every
dispatch anyway (see the global `CLAUDE.md`'s Model Routing section) — it
costs nothing, even though it won't change the outcome in plan mode. See
the case study's "Rejected mitigations" section for the two real
(non-instruction-layer) levers.

## Inspection and tuning

```bash
claude auto-mode defaults   # print built-in environment, allow, and soft_deny rules
claude auto-mode config     # print effective config with your settings applied
claude auto-mode critique   # get AI feedback on your custom rules
```
