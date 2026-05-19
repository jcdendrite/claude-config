# Security hardening

This repo ships two **opt-in** PII/PHI guard hooks plus the audit guidance
below. It is aimed at adopters with strict personally-identifying /
protected-health-information requirements — a claude-config install that
must pass a security review. Everything here is dormant by default: a stow
user gets the hooks installed but inert until they deliberately arm them.

The hooks are mechanical tripwires against *accidental* exposure. They are
defense-in-depth, not a substitute for the airtight control — keeping
patient data off developer machines entirely (machine segmentation). See
[Limitations](#limitations).

## The two PII guard hooks

| Hook | Gates | Armed by |
|---|---|---|
| `deny-pii-in-commits.sh` | `git commit` — scans the staged diff and commit message for PII | `~/.claude/pii-patterns.md` |
| `deny-data-file-reads.sh` | `Read` — refuses data-shaped files before their content enters context | `~/.claude/data-file-read-guard.md` |

Each hook is a no-op until its config file exists as a readable regular
file. Both config files are **user-local** — they live at `~/.claude/`
directly, never inside the stowed `claude/.claude/` package, and are never
committed. See [Config file security](#config-file-security).

## Arming the commit-scan hook

`deny-pii-in-commits.sh` denies `git commit` when the **added lines** of the
staged diff, the commit message, or a referenced `-F`/`--file` message file
match a PII pattern. It fires in **every** git repo on the machine, not just
claude-config — a PII commit gate is only useful where PII actually is.

It is robust against `git commit --no-verify`: a Claude Code PreToolUse hook
intercepts the Bash tool call itself, and `--no-verify` disables only git's
native hook chain.

```bash
# Create the config file. The hook ignores `#` comment lines and blanks.
cat > ~/.claude/pii-patterns.md <<'EOF'
# PII patterns scanned in git commits. See docs/security-hardening.md in
# the claude-config repo for the grammar.
EOF
chmod 600 ~/.claude/pii-patterns.md
```

**Pattern tiers.** Once armed, the scan set is:

- **Built-in generic patterns** shipped in the hook: US Social Security
  numbers (`NNN-NN-NNNN`) and credit-card-shaped 13–19 digit runs that pass
  a Luhn checksum. These need no config.
- **Your patterns** — every `<label>: <regex>` line in `pii-patterns.md`.
  Environment-specific identifier shapes (medical record numbers, internal
  UUID formats, and similar) live only in this user-local file. They are
  never committed to the public repo.

**Config grammar.** Line-based; `#` comments and blank lines ignored. Two
line types:

- `<label>: <regex>` — a labelled PII pattern. The regex is POSIX ERE
  (`grep -E`). The label is a human-readable name; the deny message names
  the label, never the regex and never the matched value.
- `exclude: <glob>` — a repo-relative path glob dropped from the diff scan.
  Use this for legitimate synthetic-PII test fixtures rather than disarming
  the hook.

```
# Example pii-patterns.md body — adapt the shapes to your environment.
Medical record number: \bMRN[0-9]{8}\b
Internal patient UUID: \bPT-[0-9a-f]{12}\b
exclude: tests/fixtures/**
exclude: spec/support/synthetic-data/**
```

A non-comment line the hook cannot parse — no `:`, an empty label or value,
or an uncompilable regex — fails the commit closed and names the line
number. A silently-skipped pattern would be an unscanned leak vector.

**Known gaps.** The editor-flow commit (`git commit` with no `-m`/`-F`)
populates the message after the hook fires. A chained `git add … &&
git commit` stages content after the hook fires; the commit message is
still scanned. Credit-card detection matches contiguous digit runs only.

## Arming the data-file read hook

`deny-data-file-reads.sh` refuses Claude's `Read` tool on data-shaped files
*before* their content enters the conversation context.

```bash
# An empty file arms the hook with built-in rules only.
cat > ~/.claude/data-file-read-guard.md <<'EOF'
# Path globs for data files Claude must not Read. See
# docs/security-hardening.md in the claude-config repo.
EOF
chmod 600 ~/.claude/data-file-read-guard.md
```

**Built-in rules** (active once armed) — `Read` is denied when the target:

- has a data-file extension: `.csv`, `.tsv`, `.parquet`, `.avro`, `.xlsx`,
  `.ndjson`, `.jsonl`, `.dump`, `.bak`, `.sqlite`, `.db`, `.dta`, `.sav`,
  `.pkl`; **or**
- sits under a `Downloads/` directory; **or**
- exceeds 5 MB (a large file of any extension is likely a data dump).

Repo-relative data directories (`data/`, `exports/`, `dumps/`) are
deliberately **not** built-in — a path component named `data/` is common in
ordinary code repos and a blanket block would flood false positives. Name
the specific directories that hold real data in the config file:

```
# Example data-file-read-guard.md body — one path glob per line.
**/patient-exports/**
**/phi/**
*.mdb
```

There is no bypass valve in the hook. If a specific file genuinely must be
inspected, that is a deliberate human action outside Claude.

## Config file security

Both config files encode the **structural fingerprints** of your
environment's identifiers — a medical-record-number regex is itself
sensitive. Treat them as sensitive artifacts:

- **`chmod 600`** both files (shown in the arming steps above). A
  world-readable config leaks your identifier shapes to any other local
  process or user.
- **Keep them out of cloud sync.** `~/.claude/pii-patterns.md` and
  `~/.claude/data-file-read-guard.md` must not sit under a cloud-synced or
  backed-up home directory (iCloud Drive, Dropbox, OneDrive, Google Drive).
  Syncing them ships your identifier fingerprints to a third-party service.
  Verify with `ls -la ~/.claude/` and check whether `~` itself is synced.
- They live at `~/.claude/` directly — never inside the stowed
  `claude/.claude/` package. The repo-root `.gitignore` has
  belt-and-suspenders entries for `claude/.claude/pii-patterns.md` and
  `claude/.claude/data-file-read-guard.md` in case one is created in the
  wrong place by mistake.

## Audit checklist

The hooks guard the commit and `Read` surfaces. A full review of a
claude-config install should also cover the surfaces below. This is a
checklist to run — the results are not a repo artifact.

**MCP connectors.** Each connected MCP server can read data into context
and call out to external services. List what is enabled and confirm each is
intended:

```bash
claude mcp list
```

Remove any connector that reaches a system holding PII/PHI unless its access
is reviewed and intended.

**Telemetry and external traffic.** Confirm what leaves the machine. The
canonical list is the [Claude Code settings
docs](https://code.claude.com/docs/en/settings); the relevant environment
variables include:

- `DISABLE_TELEMETRY` — disable usage telemetry.
- `CLAUDE_CODE_ENABLE_TELEMETRY` — OpenTelemetry export (off unless set).
- `DISABLE_ERROR_REPORTING` — disable error reporting to Anthropic.
- `DISABLE_BUG_COMMAND` — disable the `/bug` command.
- `DISABLE_NON_ESSENTIAL_TRAFFIC` — block non-essential external calls.

Verify exact names against the docs before relying on them — the set
evolves. Set the chosen values in the `env` block of `settings.json` (or
enforce them via managed settings, below).

**`permissions.allow` review.** Audit the allow rules in every
`settings.json` / `settings.local.json` in scope. Each rule widens what
Claude can do without a prompt. The `review-permissions` skill in this repo
covers the checklist — over-broad globs, command chaining, and shell-
expansion surface.

## Private-fork guidance

An adopter who does not want their hardening posture visible upstream can
maintain a **private fork** of claude-config. The two config files
(`pii-patterns.md`, `data-file-read-guard.md`) stay user-local in either
case and are never part of the repo. A fork lets the adopter also pin
`settings.json` `env` values and `permissions` without contributing them
back. Pull upstream changes into the fork on the adopter's own cadence.

## Enterprise rollout: managed settings

For a multi-developer rollout, the per-machine env vars and permission rules
above can be **enforced centrally** rather than left to each developer.
Claude Code reads a system-level `managed-settings.json` that takes
precedence over user and project settings and cannot be overridden by them:

| Platform | Path |
|---|---|
| macOS | `/Library/Application Support/ClaudeCode/managed-settings.json` |
| Linux / WSL | `/etc/claude-code/managed-settings.json` |
| Windows | `C:\Program Files\ClaudeCode\managed-settings.json` |

Deploy this file via the organization's existing device-management tooling.
It is the right place to enforce telemetry env vars, `permissions.deny`
rules, and MCP restrictions org-wide. The hooks themselves still arm
per-machine via the user-local config files — managed settings govern the
Claude Code surfaces, not these two hooks. Verify the current paths against
the [settings docs](https://code.claude.com/docs/en/settings) before
deploying; the legacy Windows path under `C:\ProgramData\` is no longer
supported.

## Limitations

These hooks reduce *accidental* exposure. They do not make a machine safe
to hold PII/PHI:

- The data-file read hook only intercepts the `Read` tool. `Bash`-based
  reads (`cat`, `head`, `grep`), subagent reads, and content pasted into a
  prompt do not cross that boundary.
- The commit hook scans `git commit` issued through Claude Code. A commit
  made in a plain terminal, an IDE git GUI, or any non-Claude session is
  not gated. Git-native `pre-commit` and server-side `pre-receive` hooks
  are the enforcement layer for those paths — out of scope for
  claude-config, but worth deploying alongside.
- Pattern matching is shape-based. A novel identifier format not covered by
  a built-in or configured pattern passes.

The airtight control is machine segmentation: developer machines do not
hold patient data. That is policy, not configuration, and these hooks do
not replace it.
