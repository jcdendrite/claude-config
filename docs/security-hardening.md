# Security hardening

This repo ships two families of guard hook plus the audit guidance below.
The **credential guards** (SSH keys, cloud credential stores, token/PEM
values) are **always on** for every stow user, no arming required — a
file named exactly `id_rsa` or a string shaped like `github_pat_...` is
essentially always a live secret, so there is no meaningful false-positive
cost to weigh against leaving them off by default. The **PII/PHI guards**
are **opt-in**, aimed at adopters with strict personally-identifying /
protected-health-information requirements — a claude-config install that
must pass a security review. Domain-specific PII/PHI patterns carry real
false-positive risk in an ordinary repo (a `.csv` in a `data/` directory is
common and rarely PHI), which is why that family stays dormant until a
stow user deliberately arms it.

The hooks are mechanical tripwires against *accidental* exposure. They are
defense-in-depth, not a substitute for the airtight control — keeping
secrets and patient data off developer machines entirely (machine
segmentation). See [Limitations](#limitations).

## The always-on credential guards

| Hook | Gates | Optional additions file |
|---|---|---|
| `deny-credential-bash-reads.sh` | `Bash` — denies any command whose raw text contains a credential-shaped path token, matched case-insensitively (SSH private key, `.netrc`/`_netrc`, `.git-credentials`, a cloud credential store, a non-template `.env`/`credentials.json`) | `~/.claude/credential-file-guard.md` |
| `deny-credential-file-reads.sh` | `Read` — same built-in path shapes (case-insensitive), resolves symlinks and fails closed on an unresolvable target | `~/.claude/credential-file-guard.md` |
| `redact-credential-values.sh` | `Bash`/`Read`/`WebFetch`/`Grep`/`Task` (PostToolUse) — redacts a credential-*shaped value* with a vendor-fixed format (a GitHub token prefix, a full PEM private-key block) wherever it surfaces in a tool result, regardless of path | `~/.claude/credential-value-patterns.md` |

None of these three hooks has a config file to arm — they run for every
stow user from install. The optional additions files widen the built-in
sets with personal or org-specific shapes (a per-account token-file naming
convention, an internal secret-value prefix); unlike the built-ins, a
guessed personal convention does carry real false-positive risk, which is
why it stays opt-in even though the built-ins do not.

`~/.claude/credential-file-guard.md` (checked by both path-based hooks, in
addition to their built-in regex): one glob per line, same grammar as
`data-file-read-guard.md` below — `#` comments and blank lines ignored,
each remaining line a path glob. Optional — the built-in path shapes are
already always on; this file only adds personal/org-specific ones.

```bash
cat > ~/.claude/credential-file-guard.md <<'EOF'
# Path globs for personal/org-specific credential files, in addition to
# the built-in set. See docs/security-hardening.md in the claude-config
# repo for the grammar.
EOF
chmod 600 ~/.claude/credential-file-guard.md
```

`~/.claude/credential-value-patterns.md` (checked by
`redact-credential-values.sh`, in addition to its built-in regex): one
`<label>: <regex>` line per pattern, same grammar as `pii-patterns.md`
below, minus its `exclude:` directive — there is no diff-scan concept to
exclude a path from when redacting a tool result. A line this file's
consuming hook cannot parse is skipped, not denied: unlike the gate hooks,
`redact-credential-values.sh` is a `PostToolUse` hook with no deny
primitive, so it fails open on a malformed line the same way it fails open
on any other parse failure. Optional — the built-in value shapes are
already always on; this file only adds personal/org-specific ones.

```bash
cat > ~/.claude/credential-value-patterns.md <<'EOF'
# Personal/org-specific credential-value patterns, in addition to the
# built-in set. See docs/security-hardening.md in the claude-config repo
# for the grammar.
EOF
chmod 600 ~/.claude/credential-value-patterns.md
```

The path-based pair (`deny-credential-bash-reads.sh`,
`deny-credential-file-reads.sh`) has no bypass valve, matching
`deny-env-reads.sh`'s and `deny-data-file-reads.sh`'s own zero-allowlist
stance for a near-certain-secret shape. `deny-credential-bash-reads.sh`
specifically denies on the path token alone, with no carve-out for a
command that references the path without exposing its content (`ssh-add`,
`chmod`, `ssh -i`) — the set of verbs that CAN expose content is
unbounded, so a verb allowlist would trade a bounded false-positive cost
for an unbounded bypass. Inspect or run a specific legitimate command via
the `!` shell escape instead. `redact-credential-values.sh` is the
different-layer backstop for the two gate hooks: a credential can enter
context through a path neither one anticipates (a `WebFetch` response, a
`Grep` match, subagent-returned text), so it scans tool *results* for a
credential's own shape — limited to shapes with a vendor-fixed format —
rather than trying to enumerate every path a secret could live at.

Both path-based hooks match the built-in credential-path set, and the
optional `credential-file-guard.md` additions, case-insensitively: on the
default case-insensitive-but-case-preserving filesystem (macOS APFS/HFS+,
Windows NTFS), `id_RSA` and `id_rsa` open the identical on-disk file, so a
case-sensitive match anywhere in these hooks would be a silent bypass of a
gate whose whole design point is having no bypass valve. The additions
file's glob match is folded via a scoped `shopt -s nocasematch` around the
match itself (bash `case` has no per-pattern case-fold syntax) — a
personal glob line is protected by the same case-insensitive guarantee as
the built-in set, not left as something the user has to get right
themselves.

## The two PII guard hooks

| Hook | Gates | Armed by |
|---|---|---|
| `deny-pii-in-commits.sh` | `git commit` — scans the staged diff and commit message for PII | `~/.claude/pii-patterns.md` |
| `deny-data-file-reads.sh` | `Read` — refuses data-shaped files before their content enters context | `~/.claude/data-file-read-guard.md` |

Each hook is a no-op until its config file exists as a readable regular
file. Both config files are **user-local** — they live at `~/.claude/`
directly, never inside the stowed `claude/.claude/` package, and are never
committed. See [Config file security](#config-file-security).

`deny-pii-in-commits.sh` also carries an always-on credential-value
sub-check (the same pattern `redact-credential-values.sh` uses) that fires
whether or not `~/.claude/pii-patterns.md` exists — see [Arming the
commit-scan hook](#arming-the-commit-scan-hook) below for how the two
tiers split.

## Arming the commit-scan hook

`deny-pii-in-commits.sh` denies `git commit` when the **added lines** of the
staged diff, the commit message, or a referenced `-F`/`--file` message file
match a PII pattern — or, unconditionally, a credential-value pattern. It
fires in **every** git repo on the machine, not just claude-config — a PII
commit gate is only useful where PII actually is, and a leaked-credential
gate is only useful everywhere.

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

**Pattern tiers.**

- **Credential-value patterns** (always on, no config file needed): the
  same GitHub-token-prefix / PEM-private-key-header regex
  `redact-credential-values.sh` uses. Fires whether or not
  `~/.claude/pii-patterns.md` exists.
- **Built-in generic PII patterns** (once armed): US Social Security
  numbers (`NNN-NN-NNNN`) and credit-card-shaped 13–19 digit runs that pass
  a Luhn checksum. These need no config beyond arming.
- **Your patterns** (once armed) — every `<label>: <regex>` line in
  `pii-patterns.md`. Environment-specific identifier shapes (medical record
  numbers, internal UUID formats, and similar) live only in this user-local
  file. They are never committed to the public repo.

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

Every config file mentioned above — `pii-patterns.md`,
`data-file-read-guard.md`, and (for the always-on credential guards)
`credential-file-guard.md`, `credential-value-patterns.md` — encodes the
**structural fingerprints** of your environment's identifiers or naming
conventions; a medical-record-number regex or a per-account token-file
naming pattern is itself sensitive. Treat them as sensitive artifacts:

- **`chmod 600`** each file (shown in the arming steps above). A
  world-readable config leaks your identifier shapes to any other local
  process or user.
- **Keep them out of cloud sync.** None of the four must sit under a
  cloud-synced or backed-up home directory (iCloud Drive, Dropbox,
  OneDrive, Google Drive). Syncing them ships your identifier fingerprints
  to a third-party service. Verify with `ls -la ~/.claude/` and check
  whether `~` itself is synced.
- They live at `~/.claude/` directly — never inside the stowed
  `claude/.claude/` package. The repo-root `.gitignore` has
  belt-and-suspenders entries for all four in case one is created in the
  wrong place by mistake.

## Audit checklist

The hooks guard the commit, `Bash`, and `Read`/tool-result surfaces. A full
review of a claude-config install should also cover the surfaces below.
This is a checklist to run — the results are not a repo artifact.

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
maintain a **private fork** of claude-config. All four config files
(`pii-patterns.md`, `data-file-read-guard.md`, `credential-file-guard.md`,
`credential-value-patterns.md`) stay user-local in either case and are
never part of the repo. A fork lets the adopter also pin `settings.json`
`env` values and `permissions` without contributing them back. Pull
upstream changes into the fork on the adopter's own cadence.

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
to hold PII/PHI or live credentials:

- The data-file read hook only intercepts the `Read` tool. `Bash`-based
  reads (`cat`, `head`, `grep`), subagent reads, and content pasted into a
  prompt do not cross that boundary. `deny-credential-bash-reads.sh`
  closes this specific gap for credential paths, but only for the Bash tool
  — it has no filesystem resolution, so a credential referenced only
  through an earlier symlink or rename under an innocuous name (not by its
  own path token, in the command this hook actually sees) is not caught.
- The commit hook scans `git commit` issued through Claude Code. A commit
  made in a plain terminal, an IDE git GUI, or any non-Claude session is
  not gated. Git-native `pre-commit` and server-side `pre-receive` hooks
  are the enforcement layer for those paths — out of scope for
  claude-config, but worth deploying alongside.
- Pattern matching is shape-based. A novel identifier format not covered by
  a built-in or configured pattern passes — this applies equally to the
  credential-value regex `redact-credential-values.sh` and
  `deny-pii-in-commits.sh`'s credential-value sub-check share: known vendor
  token-prefix shapes and a PEM header, not entropy-based generic-secret
  detection. Concretely, this means `redact-credential-values.sh` does NOT
  redact a `.netrc` plaintext password, a `.git-credentials` URL, an AWS
  `credentials` file's secret key value, a Docker `config.json` auth blob,
  or a Kubernetes `config` bearer token/cert if one of those enters a tool
  result through a path the two credential-path gates don't cover (a
  `WebFetch` response, a `Grep` match) — none of those value shapes have a
  fixed, vendor-documented format to match against. The path gates, not
  the value-redaction backstop, are what stop those credential families
  from entering context via `Bash`/`Read`.
- `redact-credential-values.sh` leaves a `tool_response` over its 5 MB size
  cap completely unscanned rather than partially redacted, to bound its own
  per-fire latency. A credential inside a truncated-past-cap output is not
  redacted.
- Both credential-path gates (`deny-credential-bash-reads.sh`,
  `deny-credential-file-reads.sh`) match a bare basename token anywhere in
  the scanned text, not only when directory-qualified — this closes a
  `cd`-then-bare-reference bypass but also means a command genuinely
  searching FOR the literal string `id_rsa` (e.g. `grep "id_rsa" .`), not
  opening a file by that name, is denied too. Accepted false-positive cost,
  not a gap in the credential-exposure coverage itself.

The airtight control is machine segmentation: developer machines do not
hold patient data or long-lived credentials. That is policy, not
configuration, and these hooks do not replace it.
