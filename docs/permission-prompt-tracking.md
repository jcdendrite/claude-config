# Permission-Prompt Tracking Hook

## Activation

```bash
touch ~/.claude/track-permission-prompts
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
rm ~/.claude/track-permission-prompts
```

There is no per-repo opt-out. This mechanism only appends to a local log and changes no git/PR/tool behavior, and its whole purpose is a cross-repo frequency view — a per-repo opt-out would fragment the aggregate the feature exists to produce, for no privacy benefit the sentinel above doesn't already provide.

## Log location and format

`~/.claude/.permission-prompt-log.jsonl` — one JSON object per line: the entire `Notification` payload Claude Code sent, credential-redacted, plus `logged_at`. The file is `chmod 600`d after every append.

The log is append-only and not rotated automatically — see [Known limitations](#known-limitations) for the retention tradeoff. Trim it manually if disk space or data age is a concern:

```bash
# Delete and let it regrow:
rm ~/.claude/.permission-prompt-log.jsonl

# Or keep only lines logged in the last 30 days:
jq -c --arg cutoff "$(date -u -v-30d +%Y-%m-%dT%H:%M:%SZ)" \
  'select(.logged_at >= $cutoff)' \
  ~/.claude/.permission-prompt-log.jsonl > /tmp/trimmed.jsonl \
  && mv /tmp/trimmed.jsonl ~/.claude/.permission-prompt-log.jsonl
```

(`date -v-30d` is the BSD/macOS form; GNU `date` uses `date -u -d '30 days ago' +%Y-%m-%dT%H:%M:%SZ` instead.)

## Known limitations

- **The exact field-level shape of a `permission_prompt` `Notification` payload is undocumented.** Anthropic's [hooks reference](https://code.claude.com/docs/en/hooks) gives a schema example for `PreToolUse` but not for `Notification`, and does not state whether a `permission_prompt` notification carries `tool_name`/`tool_input` or only a free-text `message`. Rather than guess a field list that might not exist on the real payload — silently losing data if it's wrong — the hook logs the entire payload verbatim (redacted, plus `logged_at`) so whatever fields Claude Code actually sends get captured. *This section should be updated with 1-2 real example log lines once a live session has triggered the hook and its output has been inspected.*
- **Redaction is regex-shape-based and can miss a credential with no fixed shape.** `_lib_redact_credential_shaped_strings` recognizes GitHub token prefixes, an AWS access key ID, and PEM private-key blocks (plus any user additions in `credential-value-patterns.md`) — the same coverage, and the same gap, `redact-credential-values.sh`'s own header documents. Because the hook logs the entire raw `message` field verbatim, the realistic exposure isn't limited to discrete credential-store files (`.netrc`, `.git-credentials`) — it's anything embedded in a rendered shell command shown in a permission-prompt dialog with no vendor-fixed shape: a `--password` flag, a DB connection URL, a custom API key.
- **"Never leaves the machine" means "no committed-git path," not "immune to local backup/sync tooling."** For a standard `install.sh` install, `$HOME/.claude` is a real directory (not a stow symlink — `install.sh` pre-creates it so stow links entry-by-entry), so the log materializes there directly, outside the git-tracked checkout. The `.gitignore` entry for `claude/.claude/.permission-prompt-log.jsonl` is a safety net for atypical setups (e.g. a contributor pointing `CLAUDE_CONFIG_DIR` at the checkout itself), not evidence the log normally lives inside it. Regardless of location, `deny-pii-in-commits.sh`'s always-on credential-value scan is an independent second layer that denies a `git commit` whose staged diff matches a credential shape, but neither mechanism defends against a local backup or dotfile-sync tool (Time Machine, iCloud Drive, chezmoi) snapshotting `$HOME` directly off disk.
- **A `chmod 600` failure on the log file (read-only mount, an immutable file flag, an ACL denial) is silent.** The hook still appends on a failed pre-append chmod, so new content can land in a file that stayed at a wider mode; the trailing chmod attempt fails the same way with no diagnostic. Narrow enough (all three causes require an unusual local filesystem state) that this is documented here rather than instrumented — revisit if that assumption stops holding.
- **No automatic log rotation.** This is a longitudinal, cross-repo frequency view by design, so picking a retention window trades away exactly the value the feature exists to produce — that's a call for whoever is deciding how long to keep the log, not a default baked in here. Use the manual-trim commands above.
- **Whether `Notification` hook execution can delay the dialog it fires for is unverified.** Anthropic's [hooks reference](https://code.claude.com/docs/en/hooks) does not state whether `Notification` runs synchronously with dialog rendering; on a machine without GNU coreutils `timeout`, the hook's `jq` calls have no backstop against a hang, and the log-append step (`chmod`/`printf >>`) has no timeout guard on any platform, so a stalled filesystem under `$CONFIG_DIR` is a second, independent way the hook could block before returning. This should be confirmed empirically alongside the payload-shape verification above.
