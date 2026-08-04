#!/bin/bash
# hook-class: gate
# Gate: deny Claude's Read tool on data-shaped files before their content
# enters the conversation context. A data dump (CSV export, database
# backup, statistical dataset) read into context is PII/PHI exposure.
#
# Opt-in. Dormant unless ~/.claude/data-file-read-guard.md exists as a
# readable regular file. Every stow user gets this hook disabled by
# default; arming it is a deliberate per-machine action. See
# docs/security-hardening.md.
#
# Built-in rules (active once armed) — deny the Read when the target:
#  - has a data-file extension (.csv .tsv .parquet .avro .xlsx .ndjson
#    .jsonl .dump .bak .sqlite .db .dta .sav .pkl), OR
#  - sits under a Downloads/ directory (where data dumps tend to land;
#    matched case-sensitively, the OS-conventional capitalization), OR
#  - exceeds 5 MB (a large file of any extension is likely a data dump;
#    Read truncates at 2000 lines but those lines are still PII).
# Repo-relative data directories (data/, exports/, dumps/) are deliberately
# NOT built-in: a path component named `data/` is common in ordinary code
# repos and a blanket block would flood false positives. The adopter names
# the specific data directories in data-file-read-guard.md.
#
# Config-file grammar (~/.claude/data-file-read-guard.md): line-based,
# `#` comments and blank lines ignored. Each non-comment line is a path
# glob — an extension as `*.xlsx`, a directory as `**/patient-exports/**`.
# An empty file means built-in rules only.
#
# Deny message names the path and the rule that matched. The path is a
# command argument, already present verbatim in the Read tool call the
# agent issued — naming it discloses nothing new, and the hook denies
# before the file's content is ever read. There is no bypass valve in the
# hook (matches deny-env-reads.sh's posture): patient data should not be on
# a developer machine, and if a file genuinely must be inspected that is a
# deliberate human action outside Claude.
#
# Known gaps (documented, not closed): Bash-based reads (cat/head/grep),
# subagent reads, and prompt-pasted content do not cross the Read tool
# boundary; a data file just under the 5 MB threshold still carries many
# records. This hook is a tripwire against the *accidental* Read of a data
# dump — defense-in-depth, not an airtight gate. The airtight control is
# machine segmentation (no PHI on the box), which is policy — see
# docs/security-hardening.md.
#
# Fail-closed on unparseable hook input.

set -uo pipefail

# Minimal bootstrap so a failed `source` of _lib.sh below can still deny.
# Re-pointed at _lib.sh's _lib_emit_deny immediately after a successful
# source — see _lib_parse_tool_input_or_deny's contract comment in _lib.sh
# for why the full jq-encode-or-hard-block body lives there, not here.
emit_deny() {
  printf '%s\n' "$1" >&2
  exit 2
}

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  # False positive: shellcheck's static pass doesn't model this stub-then-
  # override redefinition, which resolves correctly at call time (see
  # _lib.sh's _lib_emit_deny comment). Considered moving the definition
  # after the call instead, but that defeats the bootstrap's job of
  # covering the case where sourcing _lib.sh itself fails.
  # shellcheck disable=SC2218
  emit_deny "Blocked by data-file read gate: could not source _lib.sh."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "Blocked by data-file read gate: could not parse tool-input JSON. Refusing to evaluate the Read under malformed input."

# Defense-in-depth: only act on Read calls. Edit/Write/MultiEdit also carry
# a file_path field; settings.json matches Read, this re-checks it.
if [ "$TOOL_NAME" != "Read" ]; then
  exit 0
fi

FILE_PATH=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
[ -z "$FILE_PATH" ] && exit 0

# Opt-in: dormant unless the user-local config file exists as a readable
# regular file. `[ -f ]` rejects a FIFO or device file that would block
# the line-by-line config read below.
# Union, not swap: $(_lib_config_dir)'s copy wins if present, else the legacy $HOME/.claude location -- keeps an already-armed CLAUDE_CONFIG_DIR user's guard live.
# An unresolvable config dir leaves GUARD_FILE at the legacy path; this is an opt-in guard, not a gate, so resolver failure must not disable it.
GUARD_FILE="${HOME}/.claude/data-file-read-guard.md"
if config_dir=$(_lib_config_dir) && [ -f "$config_dir/data-file-read-guard.md" ]; then
  GUARD_FILE="$config_dir/data-file-read-guard.md"
fi
if [ ! -f "$GUARD_FILE" ] || [ ! -r "$GUARD_FILE" ]; then
  exit 0
fi

BASENAME=$(basename -- "$FILE_PATH")

# --- Built-in rule: data-file extension ----------------------------------
case "$BASENAME" in
  *.csv|*.tsv|*.parquet|*.avro|*.xlsx|*.ndjson|*.jsonl|*.dump|*.bak|*.sqlite|*.db|*.dta|*.sav|*.pkl)
    DATA_EXT=${BASENAME##*.}
    emit_deny "Read of '${FILE_PATH}' denied by the data-file read gate: the '.${DATA_EXT}' extension is a data-file shape that commonly holds PII/PHI. Reading it pulls the records into Claude's conversation context. If a specific file genuinely must be inspected, that is a deliberate human action outside Claude. (Armed by ~/.claude/data-file-read-guard.md — see docs/security-hardening.md.)"
    exit 0
    ;;
esac

# --- Built-in rule: under a Downloads/ directory -------------------------
case "$FILE_PATH" in
  */Downloads/*)
    emit_deny "Read of '${FILE_PATH}' denied by the data-file read gate: the path is under a Downloads/ directory, where data dumps and exports tend to land. Reading it risks pulling PII/PHI into Claude's conversation context. (Armed by ~/.claude/data-file-read-guard.md — see docs/security-hardening.md.)"
    exit 0
    ;;
esac

# --- Built-in rule: file size over the threshold -------------------------
# Portable size: GNU stat (-c%s), then BSD/macOS stat (-f%z), then wc -c.
file_size() {
  local target="$1" size
  size=$(stat -c%s -- "$target" 2>/dev/null) \
    || size=$(stat -f%z -- "$target" 2>/dev/null) \
    || size=$(wc -c < "$target" 2>/dev/null | tr -d '[:space:]')
  printf '%s' "$size"
}

if [ -f "$FILE_PATH" ]; then
  FILE_SIZE=$(file_size "$FILE_PATH")
  if [ -n "$FILE_SIZE" ] && [ "$FILE_SIZE" -gt "$_LIB_SIZE_THRESHOLD_BYTES" ] 2>/dev/null; then
    emit_deny "Read of '${FILE_PATH}' denied by the data-file read gate: the file is ${FILE_SIZE} bytes, over the 5 MB threshold. A large file of any extension is likely a data dump; Read truncates at 2000 lines but those lines are still PII/PHI. (Armed by ~/.claude/data-file-read-guard.md — see docs/security-hardening.md.)"
    exit 0
  fi
fi

# --- Configured path globs from data-file-read-guard.md ------------------
while IFS=$'\t' read -r _lineno line; do
  # Each line is a path glob. `case` glob matching treats `*` as matching
  # any character including `/`, so `**` collapses to `*` and a glob like
  # `**/patient-exports/**` matches at any depth.
  # shellcheck disable=SC2254 # $line is an intentional user-authored glob, per
  # the comment directly above. Quoting it forces literal matching and would
  # silently break every wildcard rule in every user's guard file — a false
  # negative on this deny gate. Do not apply ShellCheck's quoting suggestion.
  case "$FILE_PATH" in
    $line)
      emit_deny "Read of '${FILE_PATH}' denied by the data-file read gate: the path matches the glob '${line}' in ~/.claude/data-file-read-guard.md, a directory or file shape you flagged as holding PII/PHI. (See docs/security-hardening.md.)"
      exit 0
      ;;
  esac
done < <(_lib_config_lines "$GUARD_FILE")

exit 0
