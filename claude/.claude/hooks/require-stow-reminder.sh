#!/bin/bash
# Gate: when a PR being opened/edited against claude-config introduces a
# new top-level entry under `claude/.claude/`, require the PR body (or a
# referenced body-source file, or a referenced commit message via
# `--fill`) to mention `install.sh` or `stow`. Reason: GNU Stow links
# each child of `claude/.claude/` individually into `~/.claude/`, but a
# *new* child only appears after re-running stow — `git pull` alone
# doesn't create the symlink. Without a reminder in the PR body,
# whoever merges and pulls won't know to re-run install.sh, and the new
# folder/file silently fails to load (Claude Code reads from
# ~/.claude/<X>, which is empty until stow links it).
#
# NOTE — `if`-dispatch is advisory; the real gate is the internal regex
# below. settings.json wires two `if` entries (`Bash(gh pr create *)`,
# `Bash(gh pr edit *)`) for early dispatch, but any drift between those
# patterns and the IS_GH_PR regex here creates silent coverage gaps.
# Update both surfaces when extending coverage.
#
# Scope:
# - Fires only when origin URL contains `claude-config` (parallel to
#   deny-private-project-refs.sh's scoping).
# - Detects new top-level entries by diffing `main...HEAD` for added
#   files under `claude/.claude/<X>/...` or `claude/.claude/<X>`, then
#   checking whether `<X>` exists on `main`. New file directly at the
#   top level (e.g. `claude/.claude/foo.md`) and new directory (e.g.
#   `claude/.claude/agents/`) are both flagged — both need a fresh
#   stow run to materialize their symlink in `~/.claude/`.
# - For `gh pr edit`: only enforces when the edit is changing the body
#   (`--body`, `--body-file`, `-F`, `--template`, `-T`). Title-only or
#   label-only edits don't need the reminder.
# - Marker is satisfied by case-insensitive substring match of
#   `install.sh` or `stow` in: the inline command (covers `--body
#   "..."`, `--title "..."`), any `--body-file`/`--template` file
#   contents, and — if `--fill`/`-f`/`--fill-first`/`--fill-verbose` is
#   used — the commit messages on the branch since `main`.
#
# Known gaps (documented, not closed):
# - `gh pr create --body "$(cat file)"` or backtick command substitution
#   inside `--body`/`--title` hides content behind shell expansion the
#   hook doesn't execute. Same gap as deny-private-project-refs.sh.
# - `gh pr edit` without any body-modifying flag is allowed through —
#   the gate cannot see the existing PR body without a `gh api` call,
#   and adding a network round-trip to a PreToolUse hook is too costly.
#   Acceptable: the create-time check guarantees the marker landed in
#   the body initially; an edit that doesn't touch the body can't
#   remove it.
# - If `main` doesn't exist locally (fresh clone, weird state), the
#   gate exits 0 (fail-open). The cost of a missed reminder is one
#   confused user; the cost of fail-closed in this rare case is
#   blocking every PR until they fetch main.

set -uo pipefail

INPUT=$(cat)
COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
JQ_EXIT=$?
CWD=$(printf '%s\n' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
[ -z "$CWD" ] && CWD="$PWD"

emit_deny() {
  local reason="$1"
  local reason_json
  reason_json=$(printf '%s' "$reason" | jq -Rs .)
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' \
    "$reason_json"
}

# Fail-closed on malformed input. Same posture as the other gates.
if [ "$JQ_EXIT" -ne 0 ]; then
  emit_deny "Blocked by stow-reminder gate: could not parse tool-input JSON. Refusing to evaluate under malformed input."
  exit 0
fi

# Internal filter (defense-in-depth against settings.json `if` drift).
# Only fire on `gh pr create` or `gh pr edit` invocations.
if ! printf '%s\n' "$COMMAND" | grep -qE '(^|&&?|;|\|\|?)\s*gh\s+pr\s+(create|edit)(\s|$)'; then
  exit 0
fi

REPO_ROOT=$(cd "$CWD" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$REPO_ROOT" ]; then
  exit 0
fi

# Scope: claude-config only. Other repos don't use stow this way.
REMOTE_URL=$(cd "$REPO_ROOT" && git config --get remote.origin.url 2>/dev/null)
if [[ "$REMOTE_URL" != *claude-config* ]]; then
  exit 0
fi

# Need a `main` ref to compute the new-top-level diff. If absent, fail
# open — see "Known gaps" in the header.
if ! (cd "$REPO_ROOT" && git rev-parse --verify main >/dev/null 2>&1); then
  exit 0
fi

# Added paths on the branch since main, scoped to the stow source dir.
ADDED_PATHS=$(cd "$REPO_ROOT" && git diff --name-only --diff-filter=A main...HEAD -- 'claude/.claude/' 2>/dev/null || true)
if [ -z "$ADDED_PATHS" ]; then
  exit 0
fi

# Extract immediate-child names under claude/.claude/ and keep only the
# ones that don't exist on `main`. An added path can be either:
#   claude/.claude/<X>           (new top-level file)
#   claude/.claude/<X>/...       (new file inside a directory; <X> is
#                                 either a brand-new directory OR an
#                                 existing one — we filter to brand-new
#                                 below).
NEW_TOPLEVEL_ENTRIES=""
SEEN_CHILDREN=""
while IFS= read -r added_path; do
  [ -z "$added_path" ] && continue
  rest="${added_path#claude/.claude/}"
  # If stripping the prefix produced no change, the path didn't have it.
  [ "$rest" = "$added_path" ] && continue
  child="${rest%%/*}"
  [ -z "$child" ] && continue
  # Dedup: only check each child once.
  case " $SEEN_CHILDREN " in *" $child "*) continue ;; esac
  SEEN_CHILDREN="$SEEN_CHILDREN $child"
  # Does `<child>` exist on main? `git cat-file -e` succeeds for both
  # files and directories at that ref.
  if ! (cd "$REPO_ROOT" && git cat-file -e "main:claude/.claude/$child" 2>/dev/null); then
    NEW_TOPLEVEL_ENTRIES="$NEW_TOPLEVEL_ENTRIES $child"
  fi
done <<< "$ADDED_PATHS"

# Trim leading space.
NEW_TOPLEVEL_ENTRIES="${NEW_TOPLEVEL_ENTRIES# }"
if [ -z "$NEW_TOPLEVEL_ENTRIES" ]; then
  exit 0
fi

# For `gh pr edit`, only enforce when the edit modifies the body. The
# create-time gate guarantees the marker landed initially; a non-body
# edit can't remove it.
IS_GH_PR_EDIT=0
if printf '%s\n' "$COMMAND" | grep -qE '(^|&&?|;|\|\|?)\s*gh\s+pr\s+edit(\s|$)'; then
  IS_GH_PR_EDIT=1
fi
if [ "$IS_GH_PR_EDIT" -eq 1 ]; then
  if ! printf '%s\n' "$COMMAND" | grep -qE '(--body([[:space:]=]|$)|--body-file|-F([[:space:]=]|$)|--template|-T([[:space:]=]|$))'; then
    exit 0
  fi
fi

# Extract paths passed to body-source flags. Same parser shape as
# deny-private-project-refs.sh.
extract_body_source_paths() {
  local cmd="$1"
  printf '%s\n' "$cmd" \
    | grep -oE '(--body-file|--template|-F|-T)(=|[[:space:]]+)[^[:space:];&|]+' \
    | sed -E 's/^(--body-file|--template|-F|-T)(=|[[:space:]]+)//' \
    | sed -E "s/^['\"](.*)['\"]$/\\1/"
}

is_pseudo_file_path() {
  case "$1" in
    -|/dev/stdin|/dev/fd/*|/proc/*/fd/*) return 0 ;;
    *) return 1 ;;
  esac
}

# Build scan target: command (covers inline --body / --title), body-
# source file contents, and — if --fill is in play — commit messages on
# the branch since main.
SCAN_TARGET="$COMMAND"

BODY_SOURCES=$(extract_body_source_paths "$COMMAND")
if [ -n "$BODY_SOURCES" ]; then
  while IFS= read -r body_source_path; do
    [ -z "$body_source_path" ] && continue
    is_pseudo_file_path "$body_source_path" && continue
    [ ! -r "$body_source_path" ] && continue
    SCAN_TARGET+=$'\n'"$(cat "$body_source_path" 2>/dev/null || true)"
  done <<< "$BODY_SOURCES"
fi

# `--fill` family: body comes from commit messages. Add those to the
# scan target so a properly-worded commit message satisfies the gate.
if printf '%s\n' "$COMMAND" | grep -qE '(--fill(-first|-verbose)?|[[:space:]]-f([[:space:]]|$))'; then
  COMMIT_MESSAGES=$(cd "$REPO_ROOT" && git log --format='%B' main..HEAD 2>/dev/null || true)
  SCAN_TARGET+=$'\n'"$COMMIT_MESSAGES"
fi

# Marker check: case-insensitive substring match for install.sh or stow.
if printf '%s' "$SCAN_TARGET" | grep -qiE '(install\.sh|stow)'; then
  exit 0
fi

# Format the entry list compactly for the deny message.
ENTRIES_HUMAN=$(printf '%s' "$NEW_TOPLEVEL_ENTRIES" | tr ' ' '\n' | sed 's/^/`claude\/.claude\//; s/$/`/' | tr '\n' ' ' | sed 's/ $//')

emit_deny "Blocked by stow-reminder gate: this PR adds new top-level entries under claude/.claude/ ($ENTRIES_HUMAN). Stow links each top-level child individually, and a brand-new child only appears in ~/.claude/ after re-running install.sh — git pull alone does not create the symlink. Without a reminder in the PR body, whoever merges won't know to re-stow, and the new content will silently fail to load. Add a line to the PR body (or a commit message if using --fill) mentioning install.sh or stow — for example: 'Post-merge: run \`./install.sh\` to link the new top-level entry.' The gate is satisfied by a case-insensitive substring match for 'install.sh' or 'stow' in the PR body, any --body-file/--template file, or commit messages reachable from --fill."

exit 0
