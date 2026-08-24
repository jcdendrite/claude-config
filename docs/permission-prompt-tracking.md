# Permission-Prompt Tracking Hook

## Activation

```bash
touch "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/track-permission-prompts"
```

Opt-in, per machine, off by default. Shipped to every stow user of this repo but inert until this sentinel exists — see [`claude/.claude/hooks/_lib.sh`](../claude/.claude/hooks/_lib.sh)'s `_lib_permission_prompt_tracking_active`.

## What the hook does

`track-permission-prompts.sh` is a `Notification` hook (matcher `permission_prompt`) that fires at the moment Claude Code has already decided to show an interactive permission-approval dialog — the minimal signal available for "which commands still prompt in `auto` mode, and how often" without reimplementing Claude Code's own permission-resolution logic. It only logs: it gates nothing, blocks nothing, and has no deny primitive.

On each fire, the hook:

1. Self-checks `hook_event_name == "Notification"` (defense-in-depth — does not rely solely on the `settings.json` matcher).
2. Exits silently unless the sentinel above is present.
3. Runs the raw payload through `_lib_redact_credential_shaped_strings` (`_lib.sh`) — the same credential-value redaction walk `redact-credential-values.sh` uses, extracted to a shared function precisely so both callers stay in sync.
4. Merges in a `logged_at` field (ISO 8601, UTC).
5. Appends the result to the log (see below) and `chmod 600`s it on every append.

## How to disable

Remove the sentinel:

```bash
rm "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/track-permission-prompts"
```

There is no per-repo opt-out. This mechanism only appends to a local log and changes no git/PR/tool behavior, and its whole purpose is a cross-repo frequency view — a per-repo opt-out would fragment the aggregate the feature exists to produce, for no privacy benefit the sentinel above doesn't already provide.

## Log location and format

`<config-dir>/.permission-prompt-log.jsonl` (`<config-dir>` means `$CLAUDE_CONFIG_DIR` when set, else `~/.claude`) — one JSON object per line: the entire `Notification` payload Claude Code sent, credential-redacted, plus `logged_at`. The file is `chmod 600`d after every append.

The log is append-only and not rotated automatically — see [Known limitations](#known-limitations) for the retention tradeoff. Trim it manually if disk space or data age is a concern:

```bash
# Delete and let it regrow:
rm "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.permission-prompt-log.jsonl"

# Or keep only lines logged in the last 30 days:
jq -c --arg cutoff "$(date -u -v-30d +%Y-%m-%dT%H:%M:%SZ)" \
  'select(.logged_at >= $cutoff)' \
  "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.permission-prompt-log.jsonl" > /tmp/trimmed.jsonl \
  && mv /tmp/trimmed.jsonl "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.permission-prompt-log.jsonl"
```

(`date -v-30d` is the BSD/macOS form; GNU `date` uses `date -u -d '30 days ago' +%Y-%m-%dT%H:%M:%SZ` instead.)

## Known limitations

- **The exact field-level shape of a `permission_prompt` `Notification` payload is undocumented** (Anthropic's [hooks reference](https://code.claude.com/docs/en/hooks) has no schema for it), so the hook logs the entire payload verbatim rather than guessing a field list. *This section should be updated with 1-2 real example log lines once a live session has triggered the hook and its output has been inspected.*
- **Redaction is regex-shape-based and can miss a credential with no fixed shape.** `_lib_redact_credential_shaped_strings` recognizes GitHub token prefixes, an AWS access key ID, and PEM private-key blocks (plus any user additions in `credential-value-patterns.md`) — the same coverage, and the same gap, `redact-credential-values.sh`'s own header documents — so a shapeless credential embedded in a logged command (e.g. a `--password` flag or DB connection URL) passes through unredacted.
- **"Never leaves the machine" means "no committed-git path," not "immune to local backup/sync tooling."** Local backup/sync tools (Time Machine, iCloud Drive, chezmoi) snapshotting `$HOME` directly are unaffected.
- **A `chmod 600` failure on the log file (read-only mount, an immutable file flag, an ACL denial) is silent** — the hook still appends, so content can land in a file left at a wider mode.
- **No automatic log rotation** — retention trades off the cross-repo view the log exists to produce, so it's left to the operator via the manual-trim commands above.
- **Whether `Notification` hook execution can delay the dialog it fires for is unverified** upstream, and the hook's `jq`/log-append calls have no timeout guard, so a stalled filesystem under `$CONFIG_DIR` could block the dialog. This should be confirmed empirically alongside the payload-shape verification above.
- **Operational footprint.** No daemon, network call, or persistent-process dependency; every external call is `jq` (timeout-backstopped where available) or a local filesystem op, and log growth is O(1) per append. The hook's blocking stdin read (`INPUT=$(cat)`) is shared by every hook in this family. Whether `Notification` execution blocks dialog rendering (see previous bullet) is the one unresolved item in this footprint.
