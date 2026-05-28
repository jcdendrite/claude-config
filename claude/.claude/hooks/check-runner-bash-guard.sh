#!/bin/bash
# PreToolUse(Bash) guard for the check-runner subagent.
# Scope: wired via settings.json PreToolUse(Bash). The `.agent_type`
# field in the hook payload scopes enforcement to calls originating
# inside a check-runner subagent — parent and non-check-runner
# subagent calls pass through unchanged.
# Posture: fail-closed on its own dependencies — _lib.sh absent or
# malformed JSON input → deny. Open-fail on the optional project-layer
# extension file — file unreadable or a malformed regex on one line
# logs to stderr and continues with global-only enforcement (the
# global hook does not own a stack-specific extension and must not
# fail-closed on its absence or shape).
# Purpose: deny three categories of Bash command when invoked from
# check-runner:
#   1. git mutations — any `git` subcommand not on the read-only
#      allowlist in _lib.sh (_lib_readonly_git_subcmds).
#   2. state-mutating shapes — database-CLI verb pairs
#      (`<word> db (reset|push|migrate|seed)`), categorical
#      destructive shapes (`git push --force`, `git reset --hard`,
#      `rm -rf /`, `rm -rf $HOME`, `rm -rf ~`), plus any pattern in
#      `<cwd>/.claude/check-runner-deny-patterns.txt` if the file
#      exists. The global patterns are vendor-name-free; vendor-named
#      categories (package installs, container lifecycle, cloud CLIs)
#      live in the per-project extension file.
#   3. file-read commands — `cat`, `head`, `tail` (except the
#      per-command-template failure-excerpt shape), `less`, `more`,
#      `view`, `grep`/`egrep`/`fgrep`, `awk`, `sed`, `rg`, `fd`,
#      `find -print|-exec`. The agent's only legitimate file read is
#      `tail -<N> "$SPOOL"` inside the same call that defined $SPOOL.
# The agent's only legitimate write is its spool file under
# ${TMPDIR:-/tmp}/; everything else is reportable as a verdict, not
# resolvable by mutation.
set -uo pipefail

emit_deny() {
  local reason="$1"
  local reason_json
  reason_json=$(printf '%s' "$reason" | jq -Rs .)
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}' \
    "$reason_json"
}

INPUT=$(cat)

# Defense in depth: only act on Bash tool calls regardless of how the
# hook is wired in settings.json. A misconfigured matcher must not
# cause the hook to read fields off the wrong payload shape.
TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
JQ_TOOL_EXIT=$?
if [ "$JQ_TOOL_EXIT" -ne 0 ]; then
  emit_deny "check-runner-bash-guard: could not parse hook payload JSON — refusing to evaluate under malformed input."
  exit 0
fi
if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi

# Discriminator: only enforce inside a check-runner subagent. The
# PreToolUse(Bash) payload carries `agent_type: "check-runner"` when
# the call originates inside the subagent; the field is absent (jq
# returns empty) in the parent and reports other agent names for
# other subagents.
AGENT_TYPE=$(printf '%s' "$INPUT" | jq -r '.agent_type // empty' 2>/dev/null)
if [ "$AGENT_TYPE" != "check-runner" ]; then
  exit 0
fi

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  emit_deny "check-runner-bash-guard: could not source _lib.sh — refusing to evaluate under degraded state."
  exit 0
fi

COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
JQ_CMD_EXIT=$?
if [ "$JQ_CMD_EXIT" -ne 0 ]; then
  emit_deny "check-runner-bash-guard: could not parse tool-input JSON — refusing to evaluate under malformed input."
  exit 0
fi

if [ -z "$COMMAND" ]; then
  exit 0
fi

deny_with_advice() {
  local layer="$1"
  local fragment="$2"
  emit_deny "check-runner-bash-guard: command fragment '$fragment' matches $layer — check-runner is checks-only and must not run state-mutating commands. Do NOT retry with a modified shape, do NOT propose an allow-rule, do NOT propose any fix; return BLOCKED (block_type: HOOK_BLOCK) for this command with this stderr message verbatim and proceed to the next enumerated command."
}

deny_file_read() {
  local layer="$1"
  local fragment="$2"
  emit_deny "check-runner-bash-guard: command fragment '$fragment' matches $layer (file-read) — check-runner's only chartered Bash calls are \`cd <path>\` and the per-command spool template. Free-standing file reads are out-of-charter. Do NOT retry with a modified shape, do NOT propose an allow-rule, do NOT propose any fix; return BLOCKED (block_type: HOOK_BLOCK) for this command with this stderr message verbatim and proceed to the next enumerated command."
}

# Global generic-shape deny patterns. Vendor-name-free by design — the
# stow target is public and must not name specific vendor binaries
# (see CLAUDE.md "Global skill bodies stay platform-agnostic" — the
# same principle applies to global hooks whose deny set ships to
# every stow user).
GLOBAL_DENY_PATTERNS=(
  # Database-CLI verb-pair convention (Supabase/Prisma/Drizzle/Atlas/sqlx/...).
  '\b[a-z][a-z0-9_-]* db (reset|push|migrate|seed)\b'
  # Universally destructive git shapes.
  '\bgit push --force\b'
  '\bgit push -f\b'
  '\bgit reset --hard\b'
  # Categorical file-tree destruction targeting root or the user's
  # home tree. The agent's only legitimate write is its spool file
  # under ${TMPDIR:-/tmp}/, so blowing away root or HOME has no
  # checks-only justification. `rm -rf /tmp/foo` and other subpaths
  # remain allowed.
  'rm -rf /([[:space:]]|$)'
  'rm -rf \$HOME([[:space:]]|/|$)'
  'rm -rf ~([[:space:]]|/|$)'
)

# File-read deny patterns. Kept separate from GLOBAL_DENY_PATTERNS so the
# deny message can identify them as file-read violations (deny_file_read)
# rather than state-mutating violations (deny_with_advice). check-runner's
# chartered Bash calls are `cd <path>` and the per-command template
# (check-runner.md). The template's only legitimate file read is
# `tail -<N> "$SPOOL"` on failure, inside the same call that defined $SPOOL.
# Free-standing reads of project files pull material into the agent's context
# that fuels prose-drift verdicts (see docs/case-studies/check-runner.md
# Incident 6). A positive-match carve-out for the template shape is checked
# before this array is tested.
GLOBAL_FILE_READ_PATTERNS=(
  '\b(cat|head|less|more|view)\b'
  '\b(grep|egrep|fgrep|awk|sed)\b'
  '\b(rg|fd)\b'
  '\btail\b'
  '\bfind\b.+(-print|-exec)'
)

# Project-layer extension. Read at decision time from the worktree's
# cwd. Open-fail per file-level event: file missing → no-op; file
# unreadable → log to stderr + continue with global-only; malformed
# regex on one line → log + skip that line, other lines still apply.
PROJECT_PATTERNS_FILE=".claude/check-runner-deny-patterns.txt"
PROJECT_DENY_PATTERNS=()
PROJECT_DENY_LINENOS=()
if [ -e "$PROJECT_PATTERNS_FILE" ]; then
  if [ -r "$PROJECT_PATTERNS_FILE" ]; then
    lineno=0
    while IFS= read -r raw_line || [ -n "$raw_line" ]; do
      lineno=$((lineno + 1))
      # Strip leading/trailing whitespace.
      line="${raw_line#"${raw_line%%[![:space:]]*}"}"
      line="${line%"${line##*[![:space:]]}"}"
      [ -z "$line" ] && continue
      case "$line" in '#'*) continue ;; esac
      # Validate the regex compiles before adding it. grep exits 0 on
      # match, 1 on no-match (valid regex), 2 on regex-parse error —
      # use exit==2 to mean malformed, not exit!=0.
      printf '' | grep -E -- "$line" >/dev/null 2>/dev/null
      grep_status=$?
      if [ "$grep_status" -ge 2 ]; then
        printf 'check-runner-bash-guard: skipping malformed regex on %s line %d: %s\n' \
          "$PROJECT_PATTERNS_FILE" "$lineno" "$line" >&2
      else
        PROJECT_DENY_PATTERNS+=("$line")
        PROJECT_DENY_LINENOS+=("$lineno")
      fi
    done < "$PROJECT_PATTERNS_FILE"
  else
    printf 'check-runner-bash-guard: %s is not readable — continuing with global-only enforcement.\n' \
      "$PROJECT_PATTERNS_FILE" >&2
  fi
fi

ALLOWED_SUBCMDS=($(_lib_readonly_git_subcmds))
ALLOWED_RE=$(IFS='|'; echo "${ALLOWED_SUBCMDS[*]}")
FRAGMENTS=$(_lib_split_fragments "$COMMAND")

while IFS= read -r fragment; do
  [ -z "$fragment" ] && continue

  # Carve-out: per-command-template failure-excerpt shape.
  # Authorized by check-runner.md: tail -<N> "$SPOOL" or $SPOOL (unquoted).
  # The if-then-fi form in the template splits on `;` to yield a fragment
  # like `then tail -50 "$SPOOL"` — the optional `then` keyword is accepted.
  # This must run before the file-read deny patterns below.
  if [[ "$fragment" =~ ^[[:space:]]*(then[[:space:]]+)?tail[[:space:]]+-[0-9]+[[:space:]]+(\"?\$SPOOL\"?)[[:space:]]*$ ]]; then
    continue
  fi

  # Global generic-shape match.
  for pattern in "${GLOBAL_DENY_PATTERNS[@]}"; do
    if [[ "$fragment" =~ $pattern ]]; then
      deny_with_advice "global generic-shape pattern (/$pattern/)" "$fragment"
      exit 0
    fi
  done

  # Global file-read match (separate from mutation patterns so the deny
  # message identifies the violation as file-read, not state-mutating).
  for pattern in "${GLOBAL_FILE_READ_PATTERNS[@]}"; do
    if [[ "$fragment" =~ $pattern ]]; then
      deny_file_read "global file-read pattern (/$pattern/)" "$fragment"
      exit 0
    fi
  done

  # Project-layer match.
  i=0
  while [ "$i" -lt "${#PROJECT_DENY_PATTERNS[@]}" ]; do
    pattern="${PROJECT_DENY_PATTERNS[$i]}"
    if printf '%s' "$fragment" | grep -qE -- "$pattern" 2>/dev/null; then
      deny_with_advice "$PROJECT_PATTERNS_FILE line ${PROJECT_DENY_LINENOS[$i]}" "$fragment"
      exit 0
    fi
    i=$((i + 1))
  done

  # Pre-existing git-allowlist enforcement.
  if _lib_fragment_invokes_git "$fragment"; then
    subcmd=$(_lib_extract_git_subcmd "$fragment")
    if [ -z "$subcmd" ]; then
      emit_deny "check-runner-bash-guard: could not determine the git subcommand in '$fragment'. check-runner must not invoke git mutations — return the verdict now."
      exit 0
    fi
    if ! [[ "$subcmd" =~ ^($ALLOWED_RE)$ ]]; then
      emit_deny "check-runner-bash-guard: 'git $subcmd' is not on the read-only allowlist. check-runner must not invoke git mutations — return the verdict now with whatever results you have so far."
      exit 0
    fi
  fi
done <<< "$FRAGMENTS"

exit 0
