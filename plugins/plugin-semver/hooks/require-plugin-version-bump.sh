#!/bin/bash
# hook-class: gate
# Gate: require a plugin's .claude-plugin/plugin.json `version` to be
# strictly increased before git commit when staged changes touch any file
# inside that plugin's directory tree.
#
# Structural check, not a marker gate: whether a version bumped is
# deterministically computable from the diff, so this hook belongs to the
# guard-settings-session-keys.sh class (inspect staged content, deny
# structurally) rather than the marker-gate class (require-skill-review.sh) —
# no marker.sh write, no permissions.allow entry, no active-bypass.
#
# WARNING: Do NOT remove the internal git commit check below. The "if"
# field in settings.json is unreliable — it has been observed to fire this
# hook on ALL Bash commands. The internal grep is the actual gate.
#
# Plugin detection: for each file changed since BASE (see below), walk its
# ancestor directories upward; the nearest ancestor `r` where
# `r/.claude-plugin/plugin.json` exists (in the BASE tree OR the index, so
# added/removed plugins resolve) is that file's plugin root. This mirrors
# plugin-semver's own definition of a plugin ("tree contains
# .claude-plugin/plugin.json") and is layout-agnostic — this hook ships
# inside the plugin-semver plugin to consumer repos whose plugin layout is
# unknown, so a hardcoded `plugins/*/` glob would silently miss un-bumped
# changes in any repo laid out differently.
#
# BASE resolution (mirrors check-branch-divergence.sh's origin/HEAD pattern):
#   1. origin/HEAD resolvable -> BASE = merge-base(HEAD, origin/<default>).
#   2. Else a local main/master ref exists and differs from HEAD -> merge-base
#      against it.
#   3. Else -> BASE = HEAD (degraded per-commit mode).
# Comparing against merge-base (not HEAD) means ONE bump anywhere on the
# branch satisfies the gate, so iterative commits don't each need their own
# bump.
#
# Known limitation: in degraded mode (step 3), a branch that already bumped
# a plugin's version in an earlier commit is falsely denied on a later
# commit that touches the same plugin again, because the index version and
# the (HEAD-as-BASE) version compare equal. This only affects repos with no
# origin/HEAD and no local main/master distinct from HEAD; step 1 and step 2
# cover the common cases.

set -uo pipefail

emit_deny() {
  local reason="$1"
  local reason_json
  reason_json=$(printf '%s' "$reason" | jq -Rs .)
  local payload
  payload=$(printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}' "$reason_json")
  printf '%s\n' "$payload"
}

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  emit_deny "Blocked by plugin-version-bump gate: could not source _lib.sh."
  exit 0
fi

_lib_parse_tool_input_or_deny "Blocked by plugin-version-bump gate: could not parse tool-input JSON."

# Only gate Bash tool calls.
if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi

# Only gate commands that contain a git commit invocation. Match `git commit`
# at the start of the command OR after a shell separator (`&&`, `||`, `;`,
# `|`), so chained forms like `git add . && git commit` are also caught. The
# trailing `(\s|$)` ensures we don't match `git commit-tree`.
if ! printf '%s\n' "$COMMAND" | grep -qE '(^|&&?|;|\|\|?)\s*git\s+commit(\s|$)'; then
  exit 0
fi

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$REPO_ROOT" ]; then
  # Not in a git repo — let git surface the error itself.
  exit 0
fi

# --- BASE resolution -----------------------------------------------------

BASE=""
DEFAULT_REF=$(git symbolic-ref -q refs/remotes/origin/HEAD 2>/dev/null) || true
if [ -n "$DEFAULT_REF" ]; then
  DEFAULT_BRANCH=${DEFAULT_REF#refs/remotes/origin/}
  BASE=$(git merge-base HEAD "origin/$DEFAULT_BRANCH" 2>/dev/null) || BASE=""
fi

if [ -z "$BASE" ]; then
  HEAD_SHA=$(git rev-parse -q --verify HEAD 2>/dev/null) || HEAD_SHA=""
  for LOCAL_DEFAULT in main master; do
    if git show-ref --verify --quiet "refs/heads/$LOCAL_DEFAULT"; then
      LOCAL_DEFAULT_SHA=$(git rev-parse -q --verify "refs/heads/$LOCAL_DEFAULT" 2>/dev/null) || LOCAL_DEFAULT_SHA=""
      if [ -n "$LOCAL_DEFAULT_SHA" ] && [ "$LOCAL_DEFAULT_SHA" != "$HEAD_SHA" ]; then
        BASE=$(git merge-base HEAD "refs/heads/$LOCAL_DEFAULT" 2>/dev/null) || BASE=""
        [ -n "$BASE" ] && break
      fi
    fi
  done
fi

if [ -z "$BASE" ]; then
  # Degraded per-commit mode (known limitation — see header comment).
  BASE=$(git rev-parse -q --verify HEAD 2>/dev/null) || BASE=""
fi

if [ -z "$BASE" ]; then
  # No HEAD yet (e.g. the very first commit in a brand-new repo) — nothing
  # to compare against.
  exit 0
fi

# --- Changed files ---------------------------------------------------------

# Diffs the index against BASE (not just the last commit), so "one bump
# anywhere on the branch" semantics hold across multiple commits.
CHANGED_FILES=$(git diff --cached --name-only "$BASE" -- . 2>/dev/null)
if [ -z "$CHANGED_FILES" ]; then
  exit 0
fi

# --- Plugin root detection --------------------------------------------------

# Return the nearest ancestor directory of $1 (a repo-relative file path)
# that contains .claude-plugin/plugin.json in the BASE tree or the index.
# Prints the directory (or "." if the repo root itself is the plugin root)
# and returns 0 on a match; returns 1 with no output when no ancestor
# qualifies (e.g. a stray file directly under plugins/ with no plugin.json
# above it, or a file entirely outside any plugin tree).
find_plugin_root() {
  local file_path="$1"
  local dir
  dir=$(dirname -- "$file_path")
  local candidate
  while :; do
    if [ "$dir" = "." ]; then
      candidate=".claude-plugin/plugin.json"
    else
      candidate="$dir/.claude-plugin/plugin.json"
    fi
    if git cat-file -e "$BASE:$candidate" 2>/dev/null || git cat-file -e ":$candidate" 2>/dev/null; then
      printf '%s\n' "$dir"
      return 0
    fi
    [ "$dir" = "." ] && return 1
    dir=$(dirname -- "$dir")
  done
}

PLUGIN_ROOTS=()
while IFS= read -r CHANGED_FILE; do
  [ -z "$CHANGED_FILE" ] && continue
  ROOT=$(find_plugin_root "$CHANGED_FILE") || continue
  [ -z "$ROOT" ] && continue
  ALREADY_SEEN=0
  for EXISTING_ROOT in "${PLUGIN_ROOTS[@]}"; do
    if [ "$EXISTING_ROOT" = "$ROOT" ]; then
      ALREADY_SEEN=1
      break
    fi
  done
  [ "$ALREADY_SEEN" -eq 0 ] && PLUGIN_ROOTS+=("$ROOT")
done <<< "$CHANGED_FILES"

if [ "${#PLUGIN_ROOTS[@]}" -eq 0 ]; then
  exit 0
fi

# --- Version comparison ------------------------------------------------

# Requires exactly 3 dot-separated numeric components (major.minor.patch),
# matching this repo's plugin.json version convention. Rejects prerelease
# suffixes, `v` prefixes, and non-3-part versions — a version that doesn't
# parse this way can't be compared, so it's treated the same as a missing
# version (fail-closed).
version_is_well_formed() {
  [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

# Portable dotted-numeric compare. Returns 0 (true) iff $2 is strictly
# greater than $1. Deliberately not `sort -V` — a GNU coreutils extension
# absent on stock BSD/macOS `sort`, the same portability gap _lib.sh and
# check-branch-divergence.sh already route around.
version_strictly_greater() {
  local base="$1" idx="$2"
  version_is_well_formed "$base" || return 1
  version_is_well_formed "$idx" || return 1
  local -a base_parts idx_parts
  IFS='.' read -r -a base_parts <<< "$base"
  IFS='.' read -r -a idx_parts <<< "$idx"
  local i
  for ((i = 0; i < 3; i++)); do
    if [ "${idx_parts[i]}" -gt "${base_parts[i]}" ]; then
      return 0
    elif [ "${idx_parts[i]}" -lt "${base_parts[i]}" ]; then
      return 1
    fi
  done
  return 1  # equal
}

VIOLATIONS=()
for ROOT in "${PLUGIN_ROOTS[@]}"; do
  if [ "$ROOT" = "." ]; then
    PLUGIN_JSON_REL=".claude-plugin/plugin.json"
  else
    PLUGIN_JSON_REL="$ROOT/.claude-plugin/plugin.json"
  fi

  # Branch on `git show`'s own exit code, not a pipe's — piping into jq
  # would mask a missing-blob failure behind jq's exit status.
  if ! BASE_CONTENT=$(git show "$BASE:$PLUGIN_JSON_REL" 2>/dev/null); then
    # New plugin on this branch — no baseline to compare against.
    continue
  fi

  if ! IDX_CONTENT=$(git show ":$PLUGIN_JSON_REL" 2>/dev/null); then
    # Plugin removed on this branch — nothing to bump.
    continue
  fi

  IDX_VER=$(printf '%s' "$IDX_CONTENT" | jq -r '.version // empty' 2>/dev/null)
  if [ -z "$IDX_VER" ]; then
    VIOLATIONS+=("$ROOT: staged plugin.json ($PLUGIN_JSON_REL) is missing or has no 'version' key — can't verify a bump was made")
    continue
  fi

  BASE_VER=$(printf '%s' "$BASE_CONTENT" | jq -r '.version // empty' 2>/dev/null)

  if ! version_strictly_greater "$BASE_VER" "$IDX_VER"; then
    VIOLATIONS+=("$ROOT: version not increased ($BASE_VER -> $IDX_VER) in $PLUGIN_JSON_REL")
  fi
done

if [ "${#VIOLATIONS[@]}" -eq 0 ]; then
  exit 0
fi

REASON="Commit blocked by plugin-version-bump gate: staged changes touch a plugin whose version was not bumped since $BASE:"
for VIOLATION in "${VIOLATIONS[@]}"; do
  REASON="$REASON"$'\n'"  - $VIOLATION"
done
REASON="$REASON"$'\n'"Bump the affected plugin's .claude-plugin/plugin.json 'version' — see the plugin-semver skill for choosing patch/minor/major."

emit_deny "$REASON"
