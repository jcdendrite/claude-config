#!/bin/bash
# hook-class: gate
# Gate: reject `gh pr create` and `gh pr edit` commands whose body
# content (inline --body "..." or body-source file) contains literal
# backslash-backtick sequences (\`). Those sequences appear when a
# session uses a <<'EOF' single-quoted heredoc but writes \` to
# "escape" the backtick — the backslash survives into the body GitHub
# renders, breaking code-span formatting.
#
# NOTE — `if`-dispatch in settings.json is advisory; the authoritative
# gate is the IS_GH_PR_CREATE/IS_GH_PR_EDIT check inside this script. Both
# surfaces must be updated together when extending coverage.
#
# Scope and limits:
# - Covers: `gh pr create` and `gh pr edit`, including chained commands
#   (&&, ||, ;) and inline --body / --body-file / --template / -F / -T.
# - Does NOT scan `git commit` or `gh api` — those surfaces are out of
#   scope for the escaped-backtick failure mode this hook addresses.
# - Does NOT catch \` injected via shell command substitution
#   (--body "$(cat file)") — static scan limitation; the hook cannot
#   execute the substitution.
# - Does NOT auto-permit \` that appear inside fenced code blocks.
#   The fix is always to drop the backslash, not to add a carve-out.
#   The deny message explains exactly what to do.
# - Fails closed (blocks) when a body-source file is a pseudo-file or
#   is not readable, matching the posture of deny-private-project-refs.sh.

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
  emit_deny "Blocked by backtick-escape gate: could not source _lib.sh."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "Blocked by backtick-escape gate: could not parse tool-input JSON. Refusing to evaluate PR body under malformed input."

# Authoritative gate: only scan gh pr create / edit commands. Checked and
# fail-closed: an undetermined match (sed/tr missing, killed, or erroring
# inside the helper) must not silently skip the scan.
_lib_command_invokes_tool_subcmd "$COMMAND" gh pr create
IS_GH_PR_CREATE_STATUS=$?
_lib_command_invokes_tool_subcmd "$COMMAND" gh pr edit
IS_GH_PR_EDIT_STATUS=$?
if [ "$IS_GH_PR_CREATE_STATUS" -eq 2 ] || [ "$IS_GH_PR_EDIT_STATUS" -eq 2 ]; then
  emit_deny "Blocked by backtick-escape gate: could not determine whether this command invokes gh pr create/edit — sed/tr may be missing, killed, or errored. Failing closed rather than letting an unscanned PR body bypass the backtick-escape scan."
  exit 0
fi

if [ "$IS_GH_PR_CREATE_STATUS" -ne 0 ] && [ "$IS_GH_PR_EDIT_STATUS" -ne 0 ]; then
  exit 0
fi

# Extract paths passed to any gh-pr body-source flag. Covers:
#   --body-file <path>    --body-file=<path>
#   -F <path>             -F=<path>
#   --template <path>     --template=<path>
#   -T <path>             -T=<path>
# One path per output line. Uses xargs tokenization to avoid matching
# flag-shaped text inside a quoted argument value.
extract_body_source_paths() {
  local cmd="$1"
  printf '%s\n' "$cmd" | xargs -n1 2>/dev/null | awk '
    BEGIN { cap = 0 }
    cap { print; cap = 0; next }
    /^(--body-file|--template|-F|-T)$/ { cap = 1; next }
    /^(--body-file=|--template=|-F=|-T=)/ { sub(/^[^=]*=/, ""); print }
  '
}

# Pseudo-file paths whose contents the hook cannot meaningfully scan
# at hook-fire time. Reject all of them fail-closed.
is_pseudo_file_path() {
  case "$1" in
    -|/dev/stdin|/dev/fd/*|/proc/*/fd/*) return 0 ;;
    *) return 1 ;;
  esac
}

# Build the scan target: start with the full command string (which
# contains any inline --body "..." value), then append the contents
# of any referenced body-source files. Deliberately NOT unioned with a
# quote-stripped copy: the sole detector below is a literal backslash-
# backtick match, and quote-stripping deletes backslashes -- a stripped
# copy could only ever remove a match the raw copy still has, never add one.
SCAN_TARGET=""
SCAN_TARGET+=$'\n'"$COMMAND"

BODY_SOURCES=$(extract_body_source_paths "$COMMAND")
if [ -n "$BODY_SOURCES" ]; then
  while IFS= read -r body_source_path; do
    [ -z "$body_source_path" ] && continue
    if is_pseudo_file_path "$body_source_path"; then
      emit_deny "gh pr command passes a body-source flag pointing at a pseudo-file path ('${body_source_path}'). The backtick-escape gate cannot statically verify what gh will read from there — '-' / '/dev/stdin' / '/dev/fd/*' resolve to the hook's own stdin or a process-specific fd, not gh's future stdin. Inline the content with --body or prepare a real on-disk file. See ~/.claude/skills/ready-for-review/SKILL.md 'Backtick hygiene' for the full rationale."
      exit 0
    fi
    if [ ! -r "$body_source_path" ]; then
      emit_deny "gh pr command references a body-source file at '${body_source_path}', but that path does not exist or is not readable from the hook. The backtick-escape gate refuses to scan an unreadable body file (fail-closed). Create the file before running the gh pr command, inline the content with --body, or simplify the path. See ~/.claude/skills/ready-for-review/SKILL.md 'Backtick hygiene' for the full rationale."
      exit 0
    fi
    BODY_CONTENT=$(cat "$body_source_path" 2>/dev/null || true)
    SCAN_TARGET+=$'\n'"$BODY_CONTENT"
  done <<< "$BODY_SOURCES"
fi

if [ -z "$SCAN_TARGET" ]; then
  exit 0
fi

# The literal two-character sequence backslash + backtick is the only
# thing scanned. -F means fixed-string (no regex interpretation), so
# the backslash is treated literally and not as a regex escape.
if printf '%s' "$SCAN_TARGET" | grep -qF -- '\`'; then
  emit_deny "PR body blocked: it contains literal backslash-backtick sequences (\\\`) that break GitHub markdown code-span rendering. Fix: if using a <<'EOF' heredoc (single-quoted delimiter), write backticks literally — do NOT write \\\`; the single-quote suppresses all expansion so the backslash is unnecessary and harmful. If using an unquoted <<EOF heredoc or double-quoted --body \"...\", switch to <<'EOF' so backticks need no escaping. See ~/.claude/skills/ready-for-review/SKILL.md 'Backtick hygiene' subsection for the full rationale."
  exit 0
fi

exit 0
