# Statusline: show account plan + email

## Context

Goal: extend the Claude Code statusline to display the logged-in account's
plan type and email, next to the existing `5h`/`7d` rate-limit usage. The
user wants a quick visual confirmation of which account/plan is active
without running an interactive command. This lives in `claude-config`
(`claude/.claude/statusline-command.sh`), not `linux-setup` — the statusline
script is out of that repo's scope per its README's "Not included" section.

## Approach

**Data source.** The statusline script currently derives all its fields from
the JSON object Claude Code pipes to its stdin (`.model`, `.cost`,
`.context_window`, `.rate_limits`, etc.). That schema is documented at
https://code.claude.com/docs/en/statusline.md and does **not** include an
email or plan/subscription-type field — confirmed against the "Available
data" and full JSON schema sections of that doc.

The only local source that has this data is `~/.claude.json`, which Claude
Code itself writes and maintains. Its top-level `oauthAccount` object
includes `emailAddress` and `organizationType` (e.g. `"claude_max"`) among
other fields — verified by inspecting key names on this machine (values were
not dumped into any transcript beyond what was needed to confirm the field
shape). This is Claude Code's own internal state file, not a documented
public API, so field names could change across Claude Code versions without
notice `[unverified: no doc reference, internal file]`. Read access is
local-file-only (no network round trip), so it's cheap enough to read on
every statusline render, matching the script's existing pattern of shelling
out to `jq` per invocation.

**Alternatives considered:**
- *Parse statusline stdin JSON* — rejected: field doesn't exist there per
  the documented schema `[verified: code.claude.com/docs/en/statusline.md]`.
- *Shell out to a `claude` CLI subcommand per render* — rejected: no
  documented subcommand prints email/plan non-interactively
  (`/status`, `/usage` are interactive slash commands, not CLI subcommands),
  and even if one existed, spawning the Node CLI on every statusline render
  (which fires on essentially every turn) would be far slower than a local
  `jq` read of a file already resident on disk.
- *Cache the value to a file periodically instead of reading `~/.claude.json`
  directly on every render* — rejected as unnecessary complexity: a `jq`
  parse of a single small local JSON file per render is the same cost model
  the script already accepts for the stdin JSON itself.

**Display format.** Per user selection: show both, plan first, e.g.
`claude_max · alice@example.com`, styled dim (matching the existing
`DIM` treatment used for placeholder/secondary text) and appended after the
cost segment. When the account segment would be empty (file missing, or
neither field present — e.g. a Claude Code install with a different auth
state) the whole segment is omitted, mirroring how `git_branch` is omitted
entirely (not shown as a placeholder) when there's no git repo — this is an
identity field that either exists or doesn't, unlike the rate-limit bars
which show a `--` placeholder while genuinely waiting for data.

**Width budget.** The script pre-computes a fixed-width baseline (`54`,
comment: "model + separators + bar + rates + cost") to decide how much
terminal width is left for the cwd/branch split. The account segment is
optional and variable-length (email lengths vary), so instead of folding it
into that static baseline (which would either overcount when absent or
undercount for long emails), reserve a capped max width for it (40 chars,
truncated with an ellipsis beyond that) and subtract that reservation from
`_available` *before* the existing cwd/branch 55/45 split — but only when
the account segment is actually going to render, so terminals with no
`~/.claude.json` data aren't penalized. This mirrors how `_max_branch_name`
already caps the branch segment rather than letting it grow unbounded.

**Assumption ledger**

- Root problem: user wants a fast visual check of the active account's plan
  and email in the statusline, alongside existing usage stats.
  `anchors: root`
- `~/.claude.json.oauthAccount.{emailAddress,organizationType}` are the
  fields to read. `[verified: key inspection on this machine, this
  session]` `anchors: root`
- These field names are Claude Code internal/undocumented and may rename or
  disappear across versions; script degrades to omitting the segment (not
  erroring) if so. `[unverified: no doc reference]` `anchors: root`
- Reading `~/.claude.json` from a local shell script on every statusline
  render is acceptable cost (same pattern as the existing stdin-JSON parse).
  `[verified: statusline.md docs — script already re-invoked per render;
  local file read is not a network call]` `anchors: root`
- No project-specific plan-it layer exists for this repo.
  `[verified: glob for .claude/skills/plan-it-*/SKILL.md under
  claude-config found no matches]` `anchors: root`

## Critical files

- `claude/.claude/statusline-command.sh` — add an account-info extraction
  block (reads `~/.claude.json` via `jq`, builds `account_display`), reserve
  its width in the truncation budget, and append `account_display` to the
  final `echo -e` assembly line. Reuse the existing `DIM`/`RESET` color vars
  and the same `jq -r '... // empty'` null-coalescing idiom already used for
  `used_pct`/`rate_5h`/`rate_7d`.

## Verification

- Run the script by hand with a representative stdin payload:
  `echo '{"model":{"display_name":"Sonnet"},"cwd":"'"$HOME"'"}' | ./claude/.claude/statusline-command.sh`
  and confirm the account segment renders when `~/.claude.json` has
  `oauthAccount` data.
- Temporarily point the script at a nonexistent file path (or a copy of
  `~/.claude.json` with `oauthAccount` stripped via `jq`) to confirm the
  segment is cleanly omitted with no errors, extra whitespace, or `jq`
  stderr noise.
- Shellcheck the modified script if the repo has a shellcheck check
  configured (check CI config / existing lint invocations for this file).
- Visually confirm in an actual terminal, including a narrow one, that the
  new segment doesn't break the existing truncation behavior for `cwd` and
  branch.
