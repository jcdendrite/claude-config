#!/bin/bash
# hook-class: informational
# Notification hook (matcher permission_prompt): appends the raw hook
# payload -- redacted for credential-shaped strings, with one added
# `logged_at` field -- to a local, gitignored JSONL log, when the
# per-developer sentinel file ~/.claude/track-permission-prompts exists.
# This hook only logs: it gates nothing, blocks nothing, and has no deny
# primitive.
#
# Dispatch: `Notification`, matcher `permission_prompt`
# (https://code.claude.com/docs/en/hooks) -- fires at the moment Claude
# Code has already decided to show an interactive permission dialog, the
# minimal signal for "which commands still prompt in auto mode" without
# reimplementing Claude Code's own permission-resolution logic.
#
# Known gaps (see docs/permission-prompt-tracking.md for the full design
# record):
# - The defense-in-depth self-check below only reaches
#   `hook_event_name == "Notification"`, not a `permission_prompt`
#   sub-type field -- the real Notification payload schema is
#   undocumented, so a future Claude Code version that fires
#   `Notification` more broadly than today would pollute the log with
#   unrelated notification types until this comment is revisited.
# - Redaction (_lib_redact_credential_shaped_strings) is regex-shape-based
#   and can miss a credential with no fixed shape, the same caveat
#   redact-credential-values.sh's own header carries.
#
# Kill switch: absence of ~/.claude/track-permission-prompts. Opt-in, not
# on-by-default -- `touch ~/.claude/track-permission-prompts` to enable.
set -uo pipefail

# Fail-open on malformed stdin: exiting non-zero would break the
# triggering Notification event.
INPUT=$(cat) || exit 0
[ -n "$INPUT" ] || exit 0

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  exit 0
fi

# Defense-in-depth: self-check hook_event_name rather than trust the
# settings.json matcher alone (repo CLAUDE.md "Hook defense-in-depth").
HOOK_EVENT_NAME=$(printf '%s\n' "$INPUT" | _lib_jq -r '.hook_event_name // ""' 2>/dev/null) || exit 0
[ "$HOOK_EVENT_NAME" = "Notification" ] || exit 0

CONFIG_DIR=$(_lib_config_dir) || exit 0

_lib_permission_prompt_tracking_active || exit 0

# Size cap before the redaction walk, mirroring redact-credential-values.sh's
# own _LIB_SIZE_THRESHOLD_BYTES check -- an oversized payload pays unbounded
# regex-alternation cost in the gsub below with no other size gate upstream.
INPUT_SIZE=$(printf '%s' "$INPUT" | wc -c | tr -d '[:space:]')
if [ -n "$INPUT_SIZE" ] && [ "$INPUT_SIZE" -gt "$_LIB_SIZE_THRESHOLD_BYTES" ] 2>/dev/null; then
  exit 0
fi

REDACTED_INPUT=$(_lib_redact_credential_shaped_strings "$INPUT")
[ -n "$REDACTED_INPUT" ] || exit 0

LOGGED_AT=$(_lib_jq -n -r 'now | todateiso8601' 2>/dev/null)
[ -n "$LOGGED_AT" ] || exit 0

# shellcheck disable=SC2016 # single-quoted on purpose: $logged_at is a jq --arg binding, not a shell variable; double-quoting would expand it in the shell before jq sees it.
LOG_LINE=$(printf '%s' "$REDACTED_INPUT" | _lib_jq -c --arg logged_at "$LOGGED_AT" \
  '. + {logged_at: $logged_at}' 2>/dev/null)
[ -n "$LOG_LINE" ] || exit 0

LOG_FILE="$CONFIG_DIR/.permission-prompt-log.jsonl"
# umask closes the creation-time window (a default-mode file briefly
# readable before the append even runs); the pre-append chmod closes the
# separate case of a pre-existing, looser-mode file being appended to
# before it's tightened; the post-append chmod is the final backstop.
umask 077
[ -e "$LOG_FILE" ] && chmod 600 "$LOG_FILE" 2>/dev/null
printf '%s\n' "$LOG_LINE" >> "$LOG_FILE" 2>/dev/null || true
chmod 600 "$LOG_FILE" 2>/dev/null || true

exit 0
