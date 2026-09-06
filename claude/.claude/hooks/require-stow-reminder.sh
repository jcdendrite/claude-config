#!/bin/bash
# hook-class: gate
# Gate: when a PR being opened/edited against claude-config introduces a
# new top-level entry under `claude/.claude/`, OR changes `install.sh`
# (GH-465), require the PR body (or a referenced body-source file, or a
# referenced commit message via `--fill`) to mention `install.sh` or
# `stow`. Reason: GNU Stow links each child of `claude/.claude/`
# individually into `~/.claude/`, but a *new* child only appears after
# re-running stow — `git pull` alone doesn't create the symlink.
# `install.sh` itself is not stowed and only takes effect when invoked, so
# a change to it ships on `git pull` only if it removes stowed behavior,
# with the replacement landing only after a manual re-run. Without a
# reminder in the PR body, whoever merges and pulls won't know to re-run
# install.sh, and the new folder/file (or the changed installer behavior)
# silently fails to take effect (Claude Code reads from ~/.claude/<X>,
# which is empty until stow links it).
#
# NOTE — `if`-dispatch is advisory; the real gate is the internal
# _lib_command_invokes_tool_subcmd check below. settings.json wires two
# `if` entries (`Bash(gh pr create *)`, `Bash(gh pr edit *)`) for early
# dispatch, but any drift between those patterns and this hook's own gh
# pr create/edit matching creates silent coverage gaps. Update both
# surfaces when extending coverage.
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
# - GH-465: also fires when `install.sh` itself differs between `main`
#   and `HEAD` (any change, not only new content), independent of whether
#   any new top-level entry exists.
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
# - `ADDED_PATHS` below is hardcoded to diff against `claude/.claude/`
#   and structurally cannot see anything added under `claude-skills/`.
#   A future PR that adds a second immediate child under `claude-skills/`
#   without also touching `install.sh` gets no reminder — that is the
#   standing, unclosed gap.

set -uo pipefail

DENY_GATE_LABEL="stow-reminder"

# Minimal bootstrap so a failed `source` of _lib.sh below can still deny.
# Re-pointed at _lib.sh's _lib_emit_deny immediately after a successful
# source — see _lib_parse_tool_input_or_deny's contract comment in _lib.sh
# for why the full jq-encode-or-hard-block body lives there, not here.
emit_deny() {
  printf 'Blocked by %s gate: %s\n' "$DENY_GATE_LABEL" "$1" >&2
  exit 2
}

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  # False positive: shellcheck's static pass doesn't model this stub-then-
  # override redefinition, which resolves correctly at call time (see
  # _lib.sh's _lib_emit_deny comment). Considered moving the definition
  # after the call instead, but that defeats the bootstrap's job of
  # covering the case where sourcing _lib.sh itself fails.
  # shellcheck disable=SC2218
  emit_deny "could not source _lib.sh."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "could not parse tool-input JSON. Refusing to evaluate under malformed input."

[ -z "$CWD" ] && CWD="$PWD"

# Internal filter (defense-in-depth against settings.json `if` drift).
# Only fire on `gh pr create` or `gh pr edit` invocations. Deliberately
# unchecked, matching this hook's own fail-open posture (see header): status
# 2 (could not determine) falls through the same "not gated, allow" path as
# status 1 (no match), rather than gaining a dedicated deny fork.
if ! _lib_command_invokes_tool_subcmd "$COMMAND" gh pr create && ! _lib_command_invokes_tool_subcmd "$COMMAND" gh pr edit; then
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

# GH-465: install.sh itself is not stowed and only takes effect when
# invoked, so a change to it -- not only a new stowed entry under
# claude/.claude/ -- also needs the re-run reminder. Any change (not
# diff-filter=A alone): moving behavior OUT of a stowed file and INTO
# install.sh ships the removal on `git pull` and the replacement only
# after a manual re-run.
INSTALL_SH_CHANGED=0
if [ -n "$(cd "$REPO_ROOT" && _lib_capped git diff --name-only main...HEAD -- install.sh 2>/dev/null)" ]; then
  INSTALL_SH_CHANGED=1
fi

if [ -z "$ADDED_PATHS" ] && [ "$INSTALL_SH_CHANGED" -eq 0 ]; then
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
if [ -z "$NEW_TOPLEVEL_ENTRIES" ] && [ "$INSTALL_SH_CHANGED" -eq 0 ]; then
  exit 0
fi

# For `gh pr edit`, only enforce when the edit modifies the body. The
# create-time gate guarantees the marker landed initially; a non-body
# edit can't remove it. Deliberately unchecked, same fail-open posture as
# the fast-reject filter above.
IS_GH_PR_EDIT=0
if _lib_command_invokes_tool_subcmd "$COMMAND" gh pr edit; then
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
# shellcheck disable=SC2016 # the `$` in the sed script is a sed end-of-line
# anchor (s/$/.../), not a shell variable — nothing here needs shell expansion.
ENTRIES_HUMAN=$(printf '%s' "$NEW_TOPLEVEL_ENTRIES" | tr ' ' '\n' | sed 's/^/`claude\/.claude\//; s/$/`/' | tr '\n' ' ' | sed 's/ $//')

# GH-465: state which trigger(s) fired so the deny message names the actual
# reason rather than always describing the new-top-level-entry case.
if [ -n "$NEW_TOPLEVEL_ENTRIES" ] && [ "$INSTALL_SH_CHANGED" -eq 1 ]; then
  REASON_DETAIL="adds new top-level entries under claude/.claude/ ($ENTRIES_HUMAN) and changes install.sh"
elif [ -n "$NEW_TOPLEVEL_ENTRIES" ]; then
  REASON_DETAIL="adds new top-level entries under claude/.claude/ ($ENTRIES_HUMAN)"
else
  REASON_DETAIL="changes install.sh"
fi

emit_deny "this PR $REASON_DETAIL. Stow links each top-level child individually, and a brand-new child only appears in ~/.claude/ after re-running install.sh — git pull alone does not create the symlink. install.sh itself is not stowed and only takes effect when invoked, so a change to it ships on git pull only if it removes stowed behavior, with the replacement landing only after a manual re-run. Without a reminder in the PR body, whoever merges won't know to re-run install.sh, and the change will silently fail to take effect. Add a line to the PR body (or a commit message if using --fill) mentioning install.sh or stow — for example: 'Post-merge: run \`./install.sh\` to pick up this change.' The gate is satisfied by a case-insensitive substring match for 'install.sh' or 'stow' in the PR body, any --body-file/--template file, or commit messages reachable from --fill."

exit 0
