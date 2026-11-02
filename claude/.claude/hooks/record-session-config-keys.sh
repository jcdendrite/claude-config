#!/usr/bin/env bash
# hook-class: informational
# Lets a later analysis tell which config keys were on for a session.
# The reader rules live in docs/session-config-keys-log.md.
# Discarding stderr keeps _config.sh warnings out of the transcript, because they can quote a path.
# The no-row notice prints from the EXIT trap rather than at each exit, so an exit added later prints it too.
# A second EXIT trap, such as the append lock's in _lib.sh, would replace this one and drop the notice.
# The notice is fixed text, so no key value, path or payload field reaches the screen or the model through it.
# The notice also goes to the model, so the agent reports a lost row even when the engineer misses the screen line.
# The notice limits the agent to reporting, because a fix to this shared hook, or an issue about it, is the engineer's call.
# A payload naming another event, or none, prints the notice, because only a harness change to that field can deliver one to a hook registered on SessionStart alone.
# A read the library reports as failed records "unreadable" rather than a default, because callers fold a failed read toward on for some keys and toward off for others.
# The token check keeps a path or free text out of the log, because a schema default can reach this hook as raw text.
# The token class spells out each character, so no locale's collation widens it.
# Ownership, not the file mode, stops another local account's pre-created file from receiving rows.
# A process that can write the config dir can still swap the log path between the checks and the append.
# Such a swap can redirect or stall the write.
# A link swapped in before the chmod also has its target, file or directory, set to mode 600.
# The append takes no lock, because a row under the 2048 bytes the tests pin goes out in one write.
# The lock-free append assumes a local filesystem on one host.
# The test pinning that one write runs only where `strace` is installed and skips elsewhere.
# Each append starts with a newline, so a row never fuses with a line an earlier failed write left unfinished.
# No config key turns this hook off, because the record has to exist where every key is off too.
# A hung config-dir filesystem stalls the hook until the harness hook timeout, because the registration sets no timeout.
# The hook knowingly exceeds the 100 ms per-fire budget in plugins/claude-hook-review/skills/claude-hook-review/SKILL.md § "7. Performance budget", because it fires once per session event.
set -uo pipefail
exec 2>/dev/null

no_row_notice_due=1
NO_ROW_SCREEN_TEXT='record-session-config-keys.sh wrote no config-key row for this session event. See docs/session-config-keys-log.md in the claude-config repository.'
NO_ROW_AGENT_TEXT='record-session-config-keys.sh wrote no config-key row for this session event. In your next reply, tell the engineer so and point them to docs/session-config-keys-log.md in the claude-config repository. Do nothing else about it unless the engineer asks: read no files for it, file no issue, write no row and leave the hook unchanged.'

print_no_row_notice() {
  printf '{"systemMessage":"%s","hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"%s"}}\n' "$NO_ROW_SCREEN_TEXT" "$NO_ROW_AGENT_TEXT"
}

trap '[ "$no_row_notice_due" = 0 ] || print_no_row_notice; exit 0' EXIT

INPUT=$(cat) || exit 0
[ -n "$INPUT" ] || exit 0

if ! . "${0%/*}/_lib.sh" 2>/dev/null; then
  exit 0
fi

# Repo CLAUDE.md "Hook defense-in-depth".
event_name=$(printf '%s\n' "$INPUT" | _lib_jq -r '.hook_event_name // empty') || exit 0
[ "$event_name" = SessionStart ] || exit 0

session_id=$(printf '%s\n' "$INPUT" | _lib_jq -r '.session_id // empty')
_lib_valid_session_id_component "$session_id" || exit 0
payload_source=$(printf '%s\n' "$INPUT" | _lib_jq -r '.source // empty')

config_dir=$(_lib_config_dir) || exit 0
# A CLAUDE_CONFIG_DIR made only of slashes resolves to an empty string or to slashes, which would put the log at the filesystem root.
[[ "$config_dir" == *[!/]* ]] || exit 0
log_file="$config_dir/.session-config-keys-log.jsonl"

key_list=$(_config_schema_known_keys) || exit 0

token_regex='^[-abcdefghijklmnopqrstuvwxyz0123456789_]+$'
if ! [[ "$payload_source" =~ $token_regex ]]; then
  payload_source=unreadable
fi

IFS=' ' read -r -a key_array <<< "$key_list"
key_names=""
key_values=""
for key in "${key_array[@]}"; do
  value=$(_config_value "$key")
  value_exit_status=$?
  if [ "$value_exit_status" -ne 0 ] || ! [[ "$value" =~ $token_regex ]]; then
    value=unreadable
  fi
  key_names="$key_names${key_names:+$'\n'}$key"
  key_values="$key_values${key_values:+$'\n'}$value"
done

# shellcheck disable=SC2016 # single-quoted on purpose: $session_id, $source, $key_names and $key_values are jq --arg bindings, not shell variables.
row=$(_lib_jq -c -n --arg session_id "$session_id" --arg source "$payload_source" --arg key_names "$key_names" --arg key_values "$key_values" \
  '{session_id: $session_id, source: $source, logged_at: (now | todateiso8601), keys: ([($key_names | split("\n")), ($key_values | split("\n"))] | transpose | map({key: .[0], value: .[1]}) | from_entries)}') || exit 0
[ -n "$row" ] || exit 0

umask 077
if [ ! -e "$log_file" ] && [ ! -L "$log_file" ]; then
  ( set -o noclobber; : > "$log_file" ) || true
fi
[ ! -L "$log_file" ] && [ -f "$log_file" ] && [ -O "$log_file" ] || exit 0
chmod -- 600 "$log_file" || exit 0
printf '\n%s\n' "$row" >> "$log_file" || printf '\n%s\n' "$row" >> "$log_file" || exit 0
no_row_notice_due=0
