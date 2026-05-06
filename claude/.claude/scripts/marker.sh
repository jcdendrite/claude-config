#!/bin/bash
# Write or remove review markers for Claude Code workflow skills.
# Called from SKILL.md HOOK_TEST_FIXTURE fenced blocks.
# Usage: marker.sh <write|activate|deactivate> <skill>
# _marker_lib_repo_hash is defined in the sourced library so the hash recipe
# stays in sync with the read side (require-*.sh hooks) automatically.
. "$HOME/.claude/hooks/_lib.sh"

set -u

usage() {
  cat >&2 <<'EOF'
Usage: ~/.claude/scripts/marker.sh <subcommand> <skill>

Subcommands:
  write      Write a completion marker for the given skill
  activate   Write an active-bypass marker for the given skill
  deactivate Remove the active-bypass marker for the given skill

Valid (subcommand, skill) combinations:
  write       code-review | skill-review | plan-review | ready-for-review
  activate    plan-review | ready-for-review | respond-pr
  deactivate  plan-review | ready-for-review | respond-pr
EOF
}

_resolve_session_id() {
  local sid pid
  # Walk up the process ancestor chain looking for a session file. Direct
  # invocation resolves in one step ($PPID = Claude Code PID). Script
  # invocation from the Bash tool resolves in two steps ($PPID = Bash tool
  # shell, grandparent = Claude Code PID). The loop handles any depth.
  pid=$PPID
  while [ -n "$pid" ] && [ "$pid" != "0" ] && [ "$pid" != "1" ]; do
    sid=$(cat "$HOME/.claude/sessions/$pid" 2>/dev/null)
    if [ -n "$sid" ]; then
      printf '%s' "$sid"
      return 0
    fi
    pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' \t')
  done
  printf 'marker.sh: SESSION_ID empty — capture-session-id.sh SessionStart hook did not run. Abort without writing a marker.\n' >&2
  exit 2
}

_resolve_repo_hash() {
  if ! git rev-parse --show-toplevel >/dev/null 2>&1; then
    printf 'marker.sh: not inside a git repository\n' >&2
    exit 2
  fi
  # tr -d '\n' is load-bearing: git rev-parse appends a trailing newline.
  # require-* hooks compute the hash via printf '%s' "$REPO_ROOT" (no newline),
  # so both sides must strip it to produce the same sha256 and matching paths.
  _marker_lib_repo_hash "$(git rev-parse --show-toplevel | tr -d '\n')"
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  usage
  exit 0
fi

if [ $# -ne 2 ]; then
  usage
  exit 2
fi

SUBCOMMAND="$1"
SKILL="$2"

case "$SUBCOMMAND" in
  write)
    case "$SKILL" in
      code-review)
        SESSION_ID=$(_resolve_session_id) || exit 2
        REPO_HASH=$(_resolve_repo_hash) || exit 2
        mkdir -p "$HOME/.claude/review-markers"
        git diff --cached | sha256sum | awk '{print $1}' \
          > "$HOME/.claude/review-markers/$REPO_HASH.$SESSION_ID"
        ;;
      skill-review)
        SESSION_ID=$(_resolve_session_id) || exit 2
        REPO_HASH=$(_resolve_repo_hash) || exit 2
        mkdir -p "$HOME/.claude/skill-review-markers"
        # The pathspec is load-bearing: scopes the hash to SKILL.md diffs only,
        # matching what require-skill-review.sh checks at commit time.
        git diff --cached -- 'claude/.claude/skills/**/SKILL.md' | sha256sum | awk '{print $1}' \
          > "$HOME/.claude/skill-review-markers/$REPO_HASH.$SESSION_ID"
        ;;
      plan-review)
        SESSION_ID=$(_resolve_session_id) || exit 2
        REPO_HASH=$(_resolve_repo_hash) || exit 2
        mkdir -p "$HOME/.claude/plan-review-markers"
        printf 'reviewed\n' > "$HOME/.claude/plan-review-markers/$REPO_HASH.$SESSION_ID"
        ;;
      ready-for-review)
        SESSION_ID=$(_resolve_session_id) || exit 2
        REPO_HASH=$(_resolve_repo_hash) || exit 2
        mkdir -p "$HOME/.claude/ready-for-review-markers"
        git rev-parse HEAD > "$HOME/.claude/ready-for-review-markers/$REPO_HASH.$SESSION_ID"
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
        mkdir -p "$HOME/.claude/.plan-review-active.d"
        touch "$HOME/.claude/.plan-review-active.d/$SESSION_ID"
        ;;
      ready-for-review)
        SESSION_ID=$(_resolve_session_id) || exit 2
        mkdir -p "$HOME/.claude/.ready-for-review-active.d"
        touch "$HOME/.claude/.ready-for-review-active.d/$SESSION_ID"
        ;;
      respond-pr)
        SESSION_ID=$(_resolve_session_id) || exit 2
        mkdir -p "$HOME/.claude/.respond-pr-active.d"
        touch "$HOME/.claude/.respond-pr-active.d/$SESSION_ID"
        ;;
      *)
        printf "marker.sh: 'activate %s' is not valid. 'activate' supports: plan-review, ready-for-review, respond-pr\n" "$SKILL" >&2
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
      *)
        printf "marker.sh: 'deactivate %s' is not valid. 'deactivate' supports: plan-review, ready-for-review, respond-pr\n" "$SKILL" >&2
        exit 2
        ;;
    esac
    ;;
  *)
    printf "marker.sh: unknown subcommand '%s'\n" "$SUBCOMMAND" >&2
    usage
    exit 2
    ;;
esac
