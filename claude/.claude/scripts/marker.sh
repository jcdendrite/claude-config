#!/bin/bash
# Write or remove review markers for Claude Code workflow skills.
# Called from SKILL.md HOOK_TEST_FIXTURE fenced blocks.
# Usage: marker.sh <write|activate|deactivate|clear-stale> [<skill>|--dry-run]
# _marker_lib_repo_hash is defined in the sourced library so the hash recipe
# stays in sync with the read side (require-*.sh hooks) automatically.
# shellcheck source=../hooks/_lib.sh
. "$HOME/.claude/hooks/_lib.sh"

set -u

usage() {
  cat >&2 <<'EOF'
Usage: ~/.claude/scripts/marker.sh <subcommand> [<skill>|--dry-run]

Subcommands:
  write      Write a completion marker for the given skill
  activate   Write an active-bypass marker for the given skill
  deactivate Remove the active-bypass marker for the given skill
  clear-stale [--dry-run]
             Evict active-bypass markers whose originating session is no
             longer alive. --dry-run reports without removing.

Valid (subcommand, skill) combinations:
  write       code-review | skill-review | plan-review | ready-for-review
  activate    plan-review | ready-for-review | respond-pr | memory-skill
  deactivate  plan-review | ready-for-review | respond-pr | memory-skill
EOF
}

_walk_session() {
  local sid pid
  # Walk up the process ancestor chain looking for a session file. Direct
  # invocation resolves in one step ($PPID = Claude Code PID). Script
  # invocation from the Bash tool resolves in two steps ($PPID = Bash tool
  # shell, grandparent = Claude Code PID). The loop handles any depth.
  # On the first ancestor with a readable session file, print
  # "<session_id> <pid>": that $pid is the live Claude main-process PID,
  # since the walk reached it as a process ancestor of this script.
  pid=$PPID
  while [ -n "$pid" ] && [ "$pid" != "0" ] && [ "$pid" != "1" ]; do
    sid=$(cat "$HOME/.claude/sessions/$pid" 2>/dev/null)
    if [ -n "$sid" ]; then
      printf '%s %s' "$sid" "$pid"
      return 0
    fi
    pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' \t')
  done
  printf 'marker.sh: SESSION_ID empty — capture-session-id.sh SessionStart hook did not run. Abort without writing a marker.\n' >&2
  return 2
}

_resolve_session_id() {
  local out
  out=$(_walk_session) || return 2
  printf '%s' "${out%% *}"
}

_resolve_claude_pid() {
  # The live Claude main-process PID for this session — the ancestor whose
  # session file the walk matched. Written into the active-bypass marker so
  # require-*.sh hooks can liveness-check it with kill -0. Resolving it from
  # the ancestor walk (not by content-scanning ~/.claude/sessions/) keeps it
  # immune to stale per-session files left behind after a crash.
  local out
  out=$(_walk_session) || return 2
  printf '%s' "${out##* }"
}

_resolve_repo_root() {
  # tr -d '\n' is load-bearing: git rev-parse appends a trailing newline.
  # require-* hooks compute the hash via printf '%s' "$REPO_ROOT" (no newline),
  # so both sides must strip it to produce the same sha256 and matching paths.
  local root
  root=$(git rev-parse --show-toplevel 2>/dev/null | tr -d '\n')
  if [ -z "$root" ]; then
    printf 'marker.sh: not inside a git repository\n' >&2
    return 2
  fi
  printf '%s' "$root"
}

_resolve_repo_hash() {
  local root
  root=$(_resolve_repo_root) || return 2
  _marker_lib_repo_hash "$root"
}

_guard_staged_vs_unstaged() {
  local skill="$1"; shift
  if git diff --cached --quiet -- "$@" && ! git diff --quiet -- "$@"; then
    # shellcheck disable=SC2016 # single-quoted for literal display text (the
    # backtick-quoted `git add` is markdown-style formatting, not command
    # substitution); %s below is the only intended expansion.
    printf 'marker.sh: staged diff is empty but unstaged tracked changes exist — run `git add` before /%s.\n' "$skill" >&2
    exit 2
  fi
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  usage
  exit 0
fi

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
  usage
  exit 2
fi

SUBCOMMAND="$1"
ARG2="${2:-}"

# Validate arg count per subcommand.
case "$SUBCOMMAND" in
  write|activate|deactivate)
    if [ -z "$ARG2" ]; then
      usage
      exit 2
    fi
    SKILL="$ARG2"
    ;;
  clear-stale)
    if [ -n "$ARG2" ] && [ "$ARG2" != "--dry-run" ]; then
      usage
      exit 2
    fi
    ;;
  *)
    printf "marker.sh: unknown subcommand '%s'\n" "$SUBCOMMAND" >&2
    usage
    exit 2
    ;;
esac

case "$SUBCOMMAND" in
  write)
    case "$SKILL" in
      code-review)
        SESSION_ID=$(_resolve_session_id) || exit 2
        REPO_HASH=$(_resolve_repo_hash) || exit 2
        _guard_staged_vs_unstaged code-review
        # Compute before redirecting: `>` truncates the marker before the
        # pipeline runs, so a failed hash would destroy a valid marker and
        # silently force a re-review. Same shape in every arm below.
        MARKER_VALUE=$(git diff --cached | sha256sum | awk '{print $1}')
        [ -n "$MARKER_VALUE" ] || { printf 'marker.sh: could not hash the staged diff. Abort without writing a marker.\n' >&2; exit 2; }
        mkdir -p "$HOME/.claude/code-review-markers"
        printf '%s\n' "$MARKER_VALUE" \
          > "$HOME/.claude/code-review-markers/$REPO_HASH.$SESSION_ID"
        ;;
      skill-review)
        SESSION_ID=$(_resolve_session_id) || exit 2
        REPO_HASH=$(_resolve_repo_hash) || exit 2
        _guard_staged_vs_unstaged skill-review 'claude/.claude/skills/**/SKILL.md' 'plugins/*/skills/**/SKILL.md'
        # The pathspecs are load-bearing: scope the hash to SKILL.md diffs only (both stowed
        # and plugin locations), matching what require-skill-review.sh checks at commit time.
        MARKER_VALUE=$(git diff --cached -- 'claude/.claude/skills/**/SKILL.md' 'plugins/*/skills/**/SKILL.md' | sha256sum | awk '{print $1}')
        [ -n "$MARKER_VALUE" ] || { printf 'marker.sh: could not hash the staged SKILL.md diff. Abort without writing a marker.\n' >&2; exit 2; }
        mkdir -p "$HOME/.claude/skill-review-markers"
        printf '%s\n' "$MARKER_VALUE" \
          > "$HOME/.claude/skill-review-markers/$REPO_HASH.$SESSION_ID"
        ;;
      plan-review)
        SESSION_ID=$(_resolve_session_id) || exit 2
        REPO_HASH=$(_resolve_repo_hash) || exit 2
        REPO_ROOT=$(_resolve_repo_root) || exit 2
        # Content-addressed: the marker holds a hash of the active plan file
        # set (paths + contents), so editing a reviewed plan re-arms
        # require-plan-review.sh on the next gate hit. _lib_active_plan_hash
        # is the single source of truth shared with the read side.
        #
        # Capture into a variable before redirecting. Writing the function's
        # output straight into the marker path would let `>` truncate an
        # existing valid marker before the function even runs, so a failed
        # attempt would destroy a good marker as a side effect.
        if ! PLAN_HASH=$(_lib_active_plan_hash "$REPO_ROOT"); then
          printf 'marker.sh: cannot read active plan file %s — cannot compute the plan-review hash. Abort without writing a marker.\n' "$PLAN_HASH" >&2
          exit 2
        fi
        mkdir -p "$HOME/.claude/plan-review-markers"
        printf '%s\n' "$PLAN_HASH" \
          > "$HOME/.claude/plan-review-markers/$REPO_HASH.$SESSION_ID"
        ;;
      ready-for-review)
        SESSION_ID=$(_resolve_session_id) || exit 2
        REPO_HASH=$(_resolve_repo_hash) || exit 2
        MARKER_VALUE=$(git rev-parse HEAD)
        [ -n "$MARKER_VALUE" ] || { printf 'marker.sh: could not resolve HEAD. Abort without writing a marker.\n' >&2; exit 2; }
        mkdir -p "$HOME/.claude/ready-for-review-markers"
        printf '%s\n' "$MARKER_VALUE" \
          > "$HOME/.claude/ready-for-review-markers/$REPO_HASH.$SESSION_ID"
        ;;
      *)
        printf "marker.sh: 'write %s' is not valid. 'write' supports: code-review, skill-review, plan-review, ready-for-review\n" "$SKILL" >&2
        exit 2
        ;;
    esac
    ;;
  activate)
    case "$SKILL" in
      plan-review)
        SESSION_ID=$(_resolve_session_id) || exit 2
        CLAUDE_PID=$(_resolve_claude_pid) || exit 2
        mkdir -p "$HOME/.claude/.plan-review-active.d"
        printf '%s\n' "$CLAUDE_PID" > "$HOME/.claude/.plan-review-active.d/$SESSION_ID"
        ;;
      ready-for-review)
        SESSION_ID=$(_resolve_session_id) || exit 2
        CLAUDE_PID=$(_resolve_claude_pid) || exit 2
        mkdir -p "$HOME/.claude/.ready-for-review-active.d"
        printf '%s\n' "$CLAUDE_PID" > "$HOME/.claude/.ready-for-review-active.d/$SESSION_ID"
        ;;
      respond-pr)
        SESSION_ID=$(_resolve_session_id) || exit 2
        CLAUDE_PID=$(_resolve_claude_pid) || exit 2
        mkdir -p "$HOME/.claude/.respond-pr-active.d"
        printf '%s\n' "$CLAUDE_PID" > "$HOME/.claude/.respond-pr-active.d/$SESSION_ID"
        ;;
      memory-skill)
        SESSION_ID=$(_resolve_session_id) || exit 2
        CLAUDE_PID=$(_resolve_claude_pid) || exit 2
        mkdir -p "$HOME/.claude/.memory-skill-active.d"
        printf '%s\n' "$CLAUDE_PID" > "$HOME/.claude/.memory-skill-active.d/$SESSION_ID"
        ;;
      *)
        printf "marker.sh: 'activate %s' is not valid. 'activate' supports: plan-review, ready-for-review, respond-pr, memory-skill\n" "$SKILL" >&2
        exit 2
        ;;
    esac
    ;;
  deactivate)
    case "$SKILL" in
      plan-review)
        SESSION_ID=$(_resolve_session_id) || exit 2
        rm -f "$HOME/.claude/.plan-review-active.d/$SESSION_ID"
        rm -f "$HOME/.claude/.plan-review-routing-read.d/$SESSION_ID"
        ;;
      ready-for-review)
        SESSION_ID=$(_resolve_session_id) || exit 2
        rm -f "$HOME/.claude/.ready-for-review-active.d/$SESSION_ID"
        ;;
      respond-pr)
        SESSION_ID=$(_resolve_session_id) || exit 2
        rm -f "$HOME/.claude/.respond-pr-active.d/$SESSION_ID"
        ;;
      memory-skill)
        SESSION_ID=$(_resolve_session_id) || exit 2
        rm -f "$HOME/.claude/.memory-skill-active.d/$SESSION_ID"
        ;;
      *)
        printf "marker.sh: 'deactivate %s' is not valid. 'deactivate' supports: plan-review, ready-for-review, respond-pr, memory-skill\n" "$SKILL" >&2
        exit 2
        ;;
    esac
    ;;
  clear-stale)
    DRY_RUN=0
    [ "$ARG2" = "--dry-run" ] && DRY_RUN=1
    EVICTED=0
    KEPT=0
    for active_dir in "$HOME/.claude"/.*-active.d; do
      [ -d "$active_dir" ] || continue
      dir_name=$(basename "$active_dir")
      for entry in "$active_dir"/*; do
        [ -f "$entry" ] || continue
        stored_pid=$(cat "$entry" 2>/dev/null | tr -d '[:space:]')
        entry_name=$(basename "$entry")
        if [[ "$stored_pid" =~ ^[0-9]+$ ]] && kill -0 "$stored_pid" 2>/dev/null; then
          KEPT=$((KEPT + 1))
          [ "$DRY_RUN" -eq 1 ] && printf '  keep: %s/%s (PID %s alive)\n' "$dir_name" "$entry_name" "$stored_pid"
        else
          EVICTED=$((EVICTED + 1))
          if [ "$DRY_RUN" -eq 0 ]; then
            rm -f "$entry"
            printf '  evict: %s/%s (PID %s dead)\n' "$dir_name" "$entry_name" "${stored_pid:-empty}"
          else
            printf '  evict (dry-run): %s/%s (PID %s dead)\n' "$dir_name" "$entry_name" "${stored_pid:-empty}"
          fi
        fi
      done
    done
    if [ "$DRY_RUN" -eq 1 ]; then
      printf 'clear-stale: would evict %d orphan(s), keep %d active\n' "$EVICTED" "$KEPT"
    else
      printf 'clear-stale: evicted %d orphan(s), kept %d active\n' "$EVICTED" "$KEPT"
    fi
    ;;
esac
