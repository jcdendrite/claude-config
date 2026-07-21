#!/bin/bash
# hook-class: gate
# Gate: require a published npm package's package.json `version` to be
# strictly increased before git commit when staged changes touch a non-test
# source file under that package.
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
# Trigger surface: a *source* file change, not a package.json edit. An
# author changing business logic rarely opens package.json, so gating on
# "package.json changed" would miss the moment the bump was actually owed.
# See "Source file definition" below for exactly which files count.
#
# Package detection: for each gated source file changed since BASE (see
# below), walk its ancestor directories upward; the nearest ancestor `r`
# where `r/package.json` exists (in the BASE tree OR the index, so
# added/removed packages resolve) is that file's package root. Layout
# agnostic, so a monorepo workspace resolves each file to its own nearest
# package.json rather than a repo-root one.
#
# Publish gate: a package.json with "private": true is never required to
# bump — this is the standard npm publish guard (npm refuses to publish a
# private package), and it prevents false positives on an app package that
# happens to live inside an installed repo.
#
# Source file definition (safe over-approximation — this hook cannot see
# npm's public/internal API split, so it will occasionally demand a bump for
# an internal-only change; that over-bump is low-cost since consumers adopt
# on their own cadence):
#   - Tracked .ts/.tsx/.js/.jsx/.mjs/.cjs files.
#   - EXCLUDING: *.test.*/*.spec.* by filename, any path segment named
#     __tests__/test/tests/dist/build/node_modules, anything under .github/,
#     and dotfiles (e.g. .eslintrc.js) — none of these are the package's
#     shipped source.
#   - *.md is excluded implicitly: it never matches the extension whitelist.
#
# BASE resolution (mirrors plugin-semver's own require-plugin-version-bump.sh,
# itself mirroring check-branch-divergence.sh's origin/HEAD pattern):
#   1. origin/HEAD resolvable -> BASE = merge-base(HEAD, origin/<default>).
#   2. Else a local main/master ref exists and differs from HEAD -> merge-base
#      against it.
#   3. Else -> no merge-base is determinable; fail open (see below). This is
#      the one deliberate divergence from plugin-semver's hook, which
#      degrades to a per-commit BASE=HEAD comparison in this case — that
#      degraded mode can still deny. A published-package repo commit that
#      lacks any resolvable baseline (shallow CI clone with no origin/HEAD,
#      a brand-new repo) should not risk blocking unrelated work over an
#      unverifiable state; the skill still nudges the bump advisory.
# Comparing against merge-base (not HEAD) means ONE bump anywhere on the
# branch satisfies the gate, so iterative commits don't each need their own
# bump.
#
# Fail-open on indeterminate state, fail-closed only on a determinable miss:
# no merge-base, a detached HEAD (no feature branch to diverge from), or a
# non-git tree all exit 0 immediately. A package.json that can't be read at
# all (removed on this branch, or some other git-level failure) is treated
# per-package as "nothing to bump" (continue), not a whole-commit allow.
# Only a malformed or missing `version` *within* an otherwise-readable,
# determinable comparison is fail-closed (denied), mirroring plugin-semver.
#
# Every `git` invocation below (rev-parse, symbolic-ref, merge-base, show-ref,
# diff --cached, cat-file, show) has no timeout backstop. This is an accepted,
# undefended risk, not a defended one — all of them are local, read-only
# plumbing commands with no daemon/network/lock-contention exposure in normal
# operation.
#
# On a passing commit where a bump is present, emits a one-line propagate
# reminder via `systemMessage` (advisory only — cross-repo propagation is
# unenforceable from within this repo).

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
  emit_deny "Blocked by npm-version-bump gate: could not source _lib.sh."
  exit 0
fi

_lib_parse_tool_input_or_deny "Blocked by npm-version-bump gate: could not parse tool-input JSON."

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

# Detached HEAD — no feature branch to compute "since this branch diverged
# from the default branch" against; fail open rather than guess at intent.
if ! git symbolic-ref -q HEAD >/dev/null 2>&1; then
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
  # No merge-base determinable (shallow clone with no origin/HEAD, no local
  # main/master distinct from HEAD, or no HEAD yet) — fail open (see header).
  exit 0
fi

# --- Changed files ---------------------------------------------------------

# Diffs the index against BASE (not just the last commit), so "one bump
# anywhere on the branch" semantics hold across multiple commits.
CHANGED_FILES=$(git diff --cached --name-only "$BASE" -- . 2>/dev/null)
if [ -z "$CHANGED_FILES" ]; then
  exit 0
fi

# --- Source file definition -------------------------------------------------

# Returns 0 (true) iff $1 (a repo-relative path) is gated source: see the
# "Source file definition" header comment for the full rule.
is_gated_source_file() {
  local path="$1"
  local base
  base=$(basename -- "$path")

  # Dotfile config (e.g. .eslintrc.js) is never gated, regardless of extension.
  case "$base" in
    .*) return 1 ;;
  esac

  # Test files, by filename convention, are never gated.
  case "$base" in
    *.test.*|*.spec.*) return 1 ;;
  esac

  # Any path segment matching a test/build/dependency directory is never gated.
  case "/$path/" in
    */__tests__/*|*/test/*|*/tests/*|*/dist/*|*/build/*|*/node_modules/*|*/.github/*) return 1 ;;
  esac

  # Only these extensions count as gated source. *.md is excluded implicitly.
  case "$path" in
    *.ts|*.tsx|*.js|*.jsx|*.mjs|*.cjs) return 0 ;;
    *) return 1 ;;
  esac
}

# --- Package root detection --------------------------------------------------

# Memoization cache for find_package_root, keyed by starting directory.
# Two parallel arrays (not `declare -A`) — associative arrays require bash
# 4+, which is not the default on stock macOS bash 3.2. CACHED_ROOTS holds
# either the resolved root directory or the sentinel "__NONE__" for a
# confirmed miss, so a miss (which still costs a full ancestor walk to
# confirm) is cached too, not just a hit.
CACHED_DIRS=()
CACHED_ROOTS=()

# Return the nearest ancestor directory of $1 (a repo-relative file path)
# that contains package.json in the BASE tree or the index. Prints the
# directory (or "." if the repo root itself is the package root) and returns
# 0 on a match; returns 1 with no output when no ancestor qualifies.
#
# Memoized via CACHED_DIRS/CACHED_ROOTS: the cache is consulted at EVERY
# directory level as the walk ascends, not just at the file's own starting
# directory — so when a second file's walk reaches a directory a prior
# file's walk already resolved (even a different starting directory), it
# stops immediately instead of continuing to re-run `git cat-file` up to
# the root. A cache check only at the starting directory would miss this:
# two files in different subdirectories of the same already-resolved
# package tree would each still walk fully to the root on their own.
find_package_root() {
  local file_path="$1"
  local dir
  dir=$(dirname -- "$file_path")

  local candidate result i cache_hit
  local -a visited_dirs=()
  result="__NONE__"
  while :; do
    cache_hit=0
    for ((i = 0; i < ${#CACHED_DIRS[@]}; i++)); do
      if [ "${CACHED_DIRS[i]}" = "$dir" ]; then
        result="${CACHED_ROOTS[i]}"
        cache_hit=1
        break
      fi
    done
    [ "$cache_hit" -eq 1 ] && break

    visited_dirs+=("$dir")
    if [ "$dir" = "." ]; then
      candidate="package.json"
    else
      candidate="$dir/package.json"
    fi
    if git cat-file -e "$BASE:$candidate" 2>/dev/null || git cat-file -e ":$candidate" 2>/dev/null; then
      result="$dir"
      break
    fi
    [ "$dir" = "." ] && break
    dir=$(dirname -- "$dir")
  done

  # Guard before iterating: on bash <4.4 (including stock macOS bash 3.2),
  # `for x in "${arr[@]}"` on an EMPTY array triggers "unbound variable"
  # under `set -u` — this hits on the common case of a cache hit at the
  # walk's very first iteration, where visited_dirs is still empty.
  local visited
  if [ "${#visited_dirs[@]}" -gt 0 ]; then
    for visited in "${visited_dirs[@]}"; do
      CACHED_DIRS+=("$visited")
      CACHED_ROOTS+=("$result")
    done
  fi

  [ "$result" = "__NONE__" ] && return 1
  printf '%s\n' "$result"
  return 0
}

PACKAGE_ROOTS=()
while IFS= read -r CHANGED_FILE; do
  [ -z "$CHANGED_FILE" ] && continue
  is_gated_source_file "$CHANGED_FILE" || continue
  ROOT=$(find_package_root "$CHANGED_FILE") || continue
  [ -z "$ROOT" ] && continue
  ALREADY_SEEN=0
  # Same bash <4.4 empty-array guard as above — PACKAGE_ROOTS is empty for
  # the first gated changed file of nearly every invocation.
  if [ "${#PACKAGE_ROOTS[@]}" -gt 0 ]; then
    for EXISTING_ROOT in "${PACKAGE_ROOTS[@]}"; do
      if [ "$EXISTING_ROOT" = "$ROOT" ]; then
        ALREADY_SEEN=1
        break
      fi
    done
  fi
  [ "$ALREADY_SEEN" -eq 0 ] && PACKAGE_ROOTS+=("$ROOT")
done <<< "$CHANGED_FILES"

if [ "${#PACKAGE_ROOTS[@]}" -eq 0 ]; then
  exit 0
fi

# --- Version comparison ------------------------------------------------

# Requires exactly 3 dot-separated numeric components (major.minor.patch).
# Rejects prerelease suffixes, `v` prefixes, and non-3-part versions — a
# version that doesn't parse this way can't be compared, so it's treated the
# same as a missing version (fail-closed).
version_is_well_formed() {
  [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

# Portable dotted-numeric compare. Returns 0 (true) iff $2 is strictly
# greater than $1. Deliberately not `sort -V` — a GNU coreutils extension
# absent on stock BSD/macOS `sort`.
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
BUMPED_ROOTS=()
for ROOT in "${PACKAGE_ROOTS[@]}"; do
  if [ "$ROOT" = "." ]; then
    PACKAGE_JSON_REL="package.json"
  else
    PACKAGE_JSON_REL="$ROOT/package.json"
  fi

  # Branch on `git show`'s own exit code, not a pipe's — piping into jq
  # would mask a missing-blob failure behind jq's exit status.
  if ! IDX_CONTENT=$(git show ":$PACKAGE_JSON_REL" 2>/dev/null); then
    # package.json unreadable or removed on this branch — nothing to bump.
    continue
  fi

  PRIVATE=$(printf '%s' "$IDX_CONTENT" | _lib_jq -r '.private // false' 2>/dev/null)
  if [ "$PRIVATE" = "true" ]; then
    # Publish gate: npm refuses to publish a private package, so no bump
    # is owed here regardless of source changes.
    continue
  fi

  if ! BASE_CONTENT=$(git show "$BASE:$PACKAGE_JSON_REL" 2>/dev/null); then
    # New package on this branch — no baseline to compare against.
    continue
  fi

  IDX_VER=$(printf '%s' "$IDX_CONTENT" | _lib_jq -r '.version // empty' 2>/dev/null)
  if [ -z "$IDX_VER" ]; then
    VIOLATIONS+=("$ROOT: staged package.json ($PACKAGE_JSON_REL) is missing or has no 'version' key — can't verify a bump was made")
    continue
  fi

  BASE_VER=$(printf '%s' "$BASE_CONTENT" | _lib_jq -r '.version // empty' 2>/dev/null)

  if version_strictly_greater "$BASE_VER" "$IDX_VER"; then
    BUMPED_ROOTS+=("$ROOT")
  else
    VIOLATIONS+=("$ROOT: version not increased ($BASE_VER -> $IDX_VER) in $PACKAGE_JSON_REL")
  fi
done

if [ "${#VIOLATIONS[@]}" -gt 0 ]; then
  REASON="Commit blocked by npm-version-bump gate: staged changes touch a non-private package's source but its version was not bumped since $BASE:"
  for VIOLATION in "${VIOLATIONS[@]}"; do
    REASON="$REASON"$'\n'"  - $VIOLATION"
  done
  REASON="$REASON"$'\n'"Bump the affected package's package.json 'version' — see the npm-semver skill for choosing patch/minor/major."
  emit_deny "$REASON"
  exit 0
fi

if [ "${#BUMPED_ROOTS[@]}" -gt 0 ]; then
  ROOTS_LIST=$(IFS=,; echo "${BUMPED_ROOTS[*]}")
  MESSAGE="npm-semver: version bumped for ${ROOTS_LIST} — remember to propagate: each consuming repo re-pins, reinstalls, and re-runs its own validation on its own cadence."
  MESSAGE_JSON=$(printf '%s' "$MESSAGE" | jq -Rs .)
  printf '{"systemMessage":%s}\n' "$MESSAGE_JSON"
fi

exit 0
