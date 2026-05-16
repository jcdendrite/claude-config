# Auto mode: README docs + cross-plan launcher

## Context

A prior session configured `autoMode.environment` in three project roots; that
work is verified and complete. While activating auto mode the repo owner hit
`auto mode unavailable for this model`. Root cause, verified against the
official [permission modes reference](https://code.claude.com/docs/en/permission-modes):

- This repo ships `"model": "opusplan"` in `claude/.claude/settings.json`.
  `opusplan` runs Opus in plan mode and **Sonnet during execution**.
- Auto mode is an *execution* mode. Its supported session model is
  plan-dependent: **Opus on every eligible plan; Sonnet additionally on Team /
  Enterprise / API, but not on Max.**
- So on a **Max** plan, the shipped `opusplan` default routes auto mode to
  Sonnet 4.6 → unsupported → "unavailable for this model." On Team / Enterprise
  / API, `opusplan`'s Sonnet execution *is* eligible and auto mode works
  directly.

`opusplan` stays the global default (owner's decision — it is the right default
for non-auto-mode work). This change makes auto mode reachable without changing
that default, and documents the interaction. The repo is stow-distributed to
engineers on **both Max and enterprise plans**, so the launcher must work
cleanly on every plan, not just Max.

## Decisions

- **No `settings.json` change.** `opusplan` remains the committed default.
- **Ship a launcher** at `claude/.local/bin/claude-auto` — a pure passthrough
  wrapper, following the repo's existing `~/.local/bin` PATH-wrapper pattern.
- **Cross-plan compatibility** via Claude Code's built-in `ANTHROPIC_MODEL` env
  var, defaulting to `opus` (the only model eligible for auto mode on *both* Max
  and enterprise plans). Enterprise users who prefer Sonnet's lighter execution
  override with `ANTHROPIC_MODEL=sonnet`.
- **Update** the existing README `### Auto mode` section — it currently gives
  activation instructions that fail under the repo's own shipped default on Max.

## Implementation

### 1. New file — `claude/.local/bin/claude-auto`

Single-file `exec` launcher (no backing script in `claude/.claude/scripts/` —
there is no logic to host there; a two-file split would be cargo-culting the
pattern used by the logic-bearing wrappers). Must be created with the
executable bit set (`chmod +x`); git tracks the mode, and the existing
`claude/.local/bin/*` shims are executable.

```bash
#!/usr/bin/env bash
# claude-auto — launch Claude Code in auto mode on a model auto mode accepts.
#
# Repo default model is opusplan (Opus in plan mode, Sonnet during execution).
# Auto mode is an execution mode; its supported session model is plan-dependent:
# Opus on every eligible plan; Sonnet additionally on Team/Enterprise/API, not
# on Max. Opus is the safe cross-plan default.
#
# Team/Enterprise/API users who prefer Sonnet's lighter execution can override:
#   ANTHROPIC_MODEL=sonnet claude-auto
exec claude --model "${ANTHROPIC_MODEL:-opus}" --permission-mode auto "$@"
```

`stow` symlinks `claude/.local/bin/` → `~/.local/bin/` (already done by
`install.sh`, which also `mkdir -p`s the directory). **No `install.sh` change
needed** — a re-run of `./install.sh` picks up the new wrapper automatically.

### 2. README — `### Auto mode` section (`README.md`, ~lines 358–440)

- **`#### Requirements` → Model bullet** (line 365): replace the generic "verify
  your alias resolves to a supported model" text with the concrete situation —
  auto mode needs a supported *session* model; the supported set is
  plan-dependent (Opus everywhere eligible; Sonnet also on Team/Enterprise/API,
  not Max); link the permission-modes reference for the authoritative list;
  state the repo consequence — the shipped `opusplan` default runs Sonnet during
  execution, so on Max the bare `claude --permission-mode auto` reports
  "unavailable for this model." Point to the `claude-auto` wrapper.
- **`#### Activating`** (lines 368–384): keep the Shift+Tab description. Replace
  the bare `claude --permission-mode auto` example as the primary instruction
  with the `claude-auto` wrapper, showing argument passthrough and the
  `CLAUDE_AUTO_MODE_MODEL=sonnet` override. Note that on Team/Enterprise/API the
  bare `claude --permission-mode auto` works directly under `opusplan` (Sonnet
  is eligible there). Add a one-line caveat to the `defaultMode` block: it sets
  the *mode*, not the *model*, so the model requirement above still applies.

Documentation lives in the `### Auto mode` section only. The `### Scripts`
section is scoped to `claude/.claude/scripts/` utilities and is left unchanged;
`claude-auto` is a `.local/bin` launcher, not a `scripts/` utility.

## Files modified

| File | Change |
|---|---|
| `claude/.local/bin/claude-auto` | **new** — executable launcher |
| `README.md` | `### Auto mode` — Model requirement + Activating subsections |
| `claude/.claude/plans/read-tmp-auto-mode-environment-config-ha-magical-mccarthy.md` | **new** — this plan; ships in the same PR |

Not changed: `claude/.claude/settings.json` (opusplan stays), `install.sh`
(wrapper auto-stows), no test file (pure `exec` passthrough has no logic to
assert; the repo's `scripts/tests/` covers logic-bearing scripts only).

`~/.claude/plans/` is stow-linked to `claude/.claude/plans/`, so the plan file
is repo-tracked. `git add` it alongside the implementation so the plan and what
it produced land in one PR — do not leave it as an orphaned untracked file.

## Verification

1. `./install.sh` — confirm `~/.local/bin/claude-auto` is a symlink and
   executable (`ls -l ~/.local/bin/claude-auto`).
2. From a repo directory, run `claude-auto`. Expect: the one-time auto-mode
   consent dialog (first run only), then a session whose statusline shows
   **Opus 4.7** with auto mode active.
3. `claude-auto "list the open PRs"` — confirm the argument passes through to
   the prompt.
4. `ANTHROPIC_MODEL=sonnet claude-auto` — confirm the statusline shows
   **Sonnet 4.6** (on Max, auto mode is then expectedly unavailable — this
   override is for Team/Enterprise/API users).
5. Render `README.md`; confirm the `### Auto mode` section reads correctly and
   the permission-modes reference link resolves.
6. `/code-review` on the diff before handoff (repo workflow).
