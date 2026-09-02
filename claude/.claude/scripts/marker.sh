#!/bin/bash
# Write or remove review markers for Claude Code workflow skills.
# Called from SKILL.md HOOK_TEST_FIXTURE fenced blocks.
# Usage: marker.sh <write|activate|deactivate|clear-stale> [<skill>|--dry-run]
# _marker_lib_repo_hash is defined in the sourced library so the hash recipe
# stays in sync with the read side (require-*.sh hooks) automatically.
# shellcheck source=../hooks/_lib.sh
. "$(dirname "$0")/../hooks/_lib.sh"

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
  resolve-session-id
             Print this session's canonically-resolved session id. Takes no
             skill argument.
  status     Report every completion marker (code-review, skill-review,
             plan-review, ready-for-review) for this repo and every
             active-bypass marker (plan-review, ready-for-review,
             respond-pr, memory-skill, handoff, issue-triage) for this
             session, each as live, historical, or absent. Takes no skill
             argument. Evicts a stale (dead-PID) active-bypass marker for
             this session as a side effect of classifying it.

Valid (subcommand, skill) combinations:
  write       code-review | skill-review | plan-review | ready-for-review
  activate    plan-review | ready-for-review | respond-pr | memory-skill | handoff
              issue-triage <owner>/<repo>
  deactivate  plan-review | ready-for-review | respond-pr | memory-skill | handoff
              issue-triage

issue-triage inverts the other five markers' polarity: a live marker here
ACTIVATES a deny (see deny-gh-mutation-during-triage.sh), it doesn't
release one. `activate` also takes a second argument (`<owner>/<repo>`)
that the other five don't.
EOF
}

_walk_session() {
  # Delegates to _lib.sh's _lib_resolve_claude_pid, which is this same
  # ancestor walk (moved there so hooks can call it directly without
  # sourcing this file). Kept as a thin wrapper, not inlined at call sites,
  # so this file's own error message stays specific to marker.sh's context.
  local out
  out=$(_lib_resolve_claude_pid) || {
    printf 'marker.sh: SESSION_ID empty — capture-session-id.sh SessionStart hook did not run. Abort without writing a marker.\n' >&2
    return 2
  }
  printf '%s' "$out"
}

_resolve_session_id() {
  local out sid
  out=$(_walk_session) || return 2
  sid="${out%% *}"
  # Chokepoint for every marker.sh path built from a session id (~15
  # constructions below): reject here once rather than at each call site. An
  # id containing '..' or '/' would escape the marker/active directories once
  # concatenated into a path, turning `rm -f`/`>` against it into an
  # operation against a caller-chosen path.
  if ! _lib_valid_session_id_component "$sid"; then
    printf 'marker.sh: SESSION_ID %s is not a valid path component. Abort without writing a marker.\n' "$sid" >&2
    return 2
  fi
  printf '%s' "$sid"
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

_refuse_main_tree_under_enforcement() {
  # A marker's path (repo hash) and its contents (staged diff, HEAD, plan set)
  # are all keyed to the tree this script resolves. When the reviewed work
  # lives in a linked worktree but this invocation resolves to the main tree,
  # the marker records a review of a tree nobody reviewed — and the reading
  # hook, resolving the same way, accepts it. There is no payload to import a
  # trusted directory from, so refuse instead of guessing.
  local root="$1" session_git_dir common_git_dir worktree
  _lib_worktree_enforcement_active "$root" || return 0
  # One rev-parse call, one line per query flag, in the order given. For the
  # main working tree both paths are identical; for a linked worktree
  # --absolute-git-dir points at <common>/worktrees/<name>.
  {
    read -r session_git_dir
    read -r common_git_dir
  } < <(_lib_capped git -C "$root" rev-parse --absolute-git-dir --path-format=absolute --git-common-dir 2>/dev/null)
  if [ -z "${session_git_dir:-}" ] || [ -z "${common_git_dir:-}" ]; then
    printf 'marker.sh: could not determine git state for %s. Refusing to write a marker under worktree enforcement.\n' "$root" >&2
    return 2
  fi
  [ "$session_git_dir" != "$common_git_dir" ] && return 0
  # Main tree, but no linked worktree exists: there is no second tree for this
  # marker to be confused with, so it correctly describes the only tree there
  # is. Refusing here would wedge a repo whose staged state was produced
  # outside Claude Code's gated tool calls — a hand-staged edit in a terminal
  # or editor, a CI checkout, or work staged before the repo opted in. The
  # worktree hooks gate tool calls, not ambient git state, so that condition is
  # reachable and legitimate.
  worktree=$(_lib_first_live_linked_worktree "$root") || return 0
  printf 'marker.sh: refusing to write a marker from the main working tree (%s) while worktree enforcement is active and a linked worktree exists (%s).\n' "$root" "$worktree" >&2
  printf 'Markers are keyed to the tree they are written from, so this would record the review against the wrong tree.\n' >&2
  printf 'Re-enter the branch worktree — EnterWorktree{path: "%s"} — and re-run the review skill there.\n' "$worktree" >&2
  return 2
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
  _refuse_main_tree_under_enforcement "$root" || return 2
  printf '%s' "$root"
}

_guard_staged_vs_unstaged() {
  local repo_root="$1"; shift
  local skill="$1"; shift
  if git -C "$repo_root" diff --cached --quiet -- "$@" && ! git -C "$repo_root" diff --quiet -- "$@"; then
    # shellcheck disable=SC2016 # single-quoted for literal display text (the
    # backtick-quoted `git add` is markdown-style formatting, not command
    # substitution); %s below is the only intended expansion.
    printf 'marker.sh: staged diff is empty but unstaged tracked changes exist — run `git add` before /%s.\n' "$skill" >&2
    exit 2
  fi
}

# _status_glob_has_match DIR PREFIX
# True iff some file in DIR whose name begins with PREFIX exists, regardless
# of content -- used by `status` to tell "historical" (a marker exists for
# this repo but its hash is stale) apart from "absent" (no marker at all).
_status_glob_has_match() {
  local dir="$1" prefix="$2"
  local nullglob_was_set=0
  if shopt -q nullglob; then nullglob_was_set=1; fi
  shopt -s nullglob
  local -a matched=("$dir/$prefix"*)
  if [ "$nullglob_was_set" -eq 0 ]; then shopt -u nullglob; fi
  # nullglob leaves the array empty on no match, so the first slot is unset
  # (empty string) precisely when nothing matched.
  [ -n "${matched[0]:-}" ]
}

# _status_report_completion_marker LABEL MARKERS_DIR REPO_HASH_PREFIX CURRENT_VALUE
# Prints "  LABEL: live|historical|absent (...)" for `status`. Returns 0 when
# live so a caller needing an extra live-only check (the reconciliation flag)
# doesn't have to re-derive the state.
_status_report_completion_marker() {
  local label="$1" markers_dir="$2" prefix="$3" current_value="$4"
  if [ -n "$current_value" ] && _lib_marker_value_present "$markers_dir" "$current_value" "$prefix"; then
    printf '  %s: live (hash matches the current state)\n' "$label"
    return 0
  fi
  if _status_glob_has_match "$markers_dir" "$prefix"; then
    printf '  %s: historical (marker present, hash does not match the current state)\n' "$label"
  else
    printf '  %s: absent (no marker for this repo)\n' "$label"
  fi
  return 1
}

# _status_reconciliation_flag LABEL REPO_ROOT [PATHSPEC...]
# Prints a flag line when the working tree holds unstaged changes overlapping
# PATHSPEC (the whole repo when no pathspec is given) -- called only when the
# corresponding marker is live, since "uncommitted changes overlap a
# not-live marker" has nothing to reconcile.
_status_reconciliation_flag() {
  local label="$1" repo_root="$2"; shift 2
  local diff_status
  # Exit code checked exactly, not just nonzero: a capped call that times out
  # also exits nonzero, and must not be misread as "differences found".
  _lib_capped git -C "$repo_root" diff --quiet -- "$@"
  diff_status=$?
  if [ "$diff_status" -eq 1 ]; then
    printf '  %s reconciliation flag: uncommitted changes overlap the diff this marker covers\n' "$label"
  fi
}

# _status_report_active_bypass LABEL DIR_NAME SESSION_ID
# Prints "  LABEL: live|stale|absent (...)" for `status`. Existence is
# captured BEFORE calling _lib_active_bypass_marker_live, which evicts a
# stale (dead-PID) marker as a side effect -- otherwise "stale" and "absent"
# would be indistinguishable after the call evicts the file out from under us.
_status_report_active_bypass() {
  local label="$1" dir_name="$2" session_id="$3"
  local marker_path="$CONFIG_DIR/$dir_name/$session_id"
  local existed_before=0
  [ -f "$marker_path" ] && existed_before=1
  if _lib_active_bypass_marker_live "$dir_name" "$session_id"; then
    printf '  %s: live (bypass marker present for this session)\n' "$label"
  elif [ "$existed_before" -eq 1 ]; then
    printf '  %s: stale (dead-PID marker evicted)\n' "$label"
  else
    printf '  %s: absent (no bypass marker for this session)\n' "$label"
  fi
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  usage
  exit 0
fi

if [ $# -lt 1 ] || [ $# -gt 3 ]; then
  usage
  exit 2
fi

# Fail closed: every marker path below is built from CONFIG_DIR, so an
# unresolvable CLAUDE_CONFIG_DIR (relative value, or empty $HOME with no
# override) must abort the write rather than fall through to a
# root-anchored path.
CONFIG_DIR=$(_lib_config_dir) || {
  # shellcheck disable=SC2016 # single-quoted for literal display text: $HOME
  # and $CLAUDE_CONFIG_DIR name the env vars in the message, not shell expansions.
  printf 'marker.sh: could not resolve the Claude Code config directory (CLAUDE_CONFIG_DIR is set to a relative path, or $HOME is unset/empty). Abort without writing a marker.\n' >&2
  exit 2
}

SUBCOMMAND="$1"
ARG2="${2:-}"
ARG3="${3:-}"

# Validate arg count per subcommand. write/deactivate stay 2-arg only.
# activate takes an optional third argument, used only by issue-triage's
# restriction-polarity marker to carry the confinement target (see usage()
# above).
case "$SUBCOMMAND" in
  write|deactivate)
    if [ -z "$ARG2" ] || [ -n "$ARG3" ]; then
      usage
      exit 2
    fi
    SKILL="$ARG2"
    ;;
  activate)
    if [ -z "$ARG2" ]; then
      usage
      exit 2
    fi
    SKILL="$ARG2"
    if [ "$SKILL" = "issue-triage" ]; then
      if [ -z "$ARG3" ]; then
        usage
        exit 2
      fi
      REPO_TARGET="$ARG3"
    elif [ -n "$ARG3" ]; then
      usage
      exit 2
    fi
    ;;
  clear-stale)
    if { [ -n "$ARG2" ] && [ "$ARG2" != "--dry-run" ]; } || [ -n "$ARG3" ]; then
      usage
      exit 2
    fi
    ;;
  resolve-session-id|status)
    if [ -n "$ARG2" ] || [ -n "$ARG3" ]; then
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
        REPO_ROOT=$(_resolve_repo_root) || exit 2
        REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
        _guard_staged_vs_unstaged "$REPO_ROOT" code-review
        # Compute before redirecting: `>` truncates the marker before the
        # pipeline runs, so a failed hash would destroy a valid marker and
        # silently force a re-review. Same shape in every arm below.
        MARKER_VALUE=$(git -C "$REPO_ROOT" diff --cached | sha256sum | awk '{print $1}')
        [ -n "$MARKER_VALUE" ] || { printf 'marker.sh: could not hash the staged diff. Abort without writing a marker.\n' >&2; exit 2; }
        mkdir -p "$CONFIG_DIR/code-review-markers"
        printf '%s\n' "$MARKER_VALUE" \
          > "$CONFIG_DIR/code-review-markers/$REPO_HASH.$SESSION_ID"
        ;;
      skill-review)
        SESSION_ID=$(_resolve_session_id) || exit 2
        REPO_ROOT=$(_resolve_repo_root) || exit 2
        REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
        _guard_staged_vs_unstaged "$REPO_ROOT" skill-review 'claude/.claude/skills/**/SKILL.md' 'plugins/*/skills/**/SKILL.md' 'claude/.claude/skills/plan-review/ROUTING.md'
        # The pathspecs are load-bearing: scope the hash to SKILL.md diffs (both stowed
        # and plugin locations) plus plan-review/ROUTING.md, matching what
        # require-skill-review.sh checks at commit time.
        MARKER_VALUE=$(git -C "$REPO_ROOT" diff --cached -- 'claude/.claude/skills/**/SKILL.md' 'plugins/*/skills/**/SKILL.md' 'claude/.claude/skills/plan-review/ROUTING.md' | sha256sum | awk '{print $1}')
        [ -n "$MARKER_VALUE" ] || { printf 'marker.sh: could not hash the staged SKILL.md diff. Abort without writing a marker.\n' >&2; exit 2; }
        mkdir -p "$CONFIG_DIR/skill-review-markers"
        printf '%s\n' "$MARKER_VALUE" \
          > "$CONFIG_DIR/skill-review-markers/$REPO_HASH.$SESSION_ID"
        ;;
      plan-review)
        SESSION_ID=$(_resolve_session_id) || exit 2
        REPO_ROOT=$(_resolve_repo_root) || exit 2
        REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
        # A plan-mode declaration takes priority over the repo-relative plan
        # set: the /plan-review skill's Step 0 writes the harness-designated
        # plan-mode file's path into this sibling file when the session is in
        # plan mode. Content-addressed like the repo-relative case below —
        # hash the DECLARED TARGET's current content, read fresh here, not
        # the sibling file's own bytes and not any hash computed earlier — so
        # a plan revision mid-review is still caught.
        PLANMODE_SIBLING="$CONFIG_DIR/.plan-review-active.d/$SESSION_ID.planmode-path"
        if PLANMODE_TARGET=$(_lib_capped cat "$PLANMODE_SIBLING" 2>/dev/null); then
          PLAN_HASH=$(_lib_capped sha256sum -- "$PLANMODE_TARGET" 2>/dev/null | awk '{print $1}')
          if [ -z "$PLAN_HASH" ]; then
            # Matches _lib_active_plan_hash's own abort contract below:
            # falling back to the repo-relative hash here would silently
            # write a completion marker that doesn't cover what was reviewed.
            printf 'marker.sh: cannot read plan-mode file %s — cannot compute the plan-review hash. Abort without writing a marker.\n' "$PLANMODE_TARGET" >&2
            exit 2
          fi
        else
          # Content-addressed: the marker holds a hash of the active plan
          # file set (paths + contents), so editing a reviewed plan re-arms
          # require-plan-review.sh on the next gate hit. _lib_active_plan_hash
          # is the single source of truth shared with the read side.
          #
          # Capture into a variable before redirecting. Writing the
          # function's output straight into the marker path would let `>`
          # truncate an existing valid marker before the function even runs,
          # so a failed attempt would destroy a good marker as a side effect.
          if ! PLAN_HASH=$(_lib_active_plan_hash "$REPO_ROOT"); then
            printf 'marker.sh: cannot read active plan file %s — cannot compute the plan-review hash. Abort without writing a marker.\n' "$PLAN_HASH" >&2
            exit 2
          fi
        fi
        mkdir -p "$CONFIG_DIR/plan-review-markers"
        printf '%s\n' "$PLAN_HASH" \
          > "$CONFIG_DIR/plan-review-markers/$REPO_HASH.$SESSION_ID"
        ;;
      ready-for-review)
        SESSION_ID=$(_resolve_session_id) || exit 2
        REPO_ROOT=$(_resolve_repo_root) || exit 2
        REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
        MARKER_VALUE=$(git -C "$REPO_ROOT" rev-parse HEAD)
        [ -n "$MARKER_VALUE" ] || { printf 'marker.sh: could not resolve HEAD. Abort without writing a marker.\n' >&2; exit 2; }
        mkdir -p "$CONFIG_DIR/ready-for-review-markers"
        printf '%s\n' "$MARKER_VALUE" \
          > "$CONFIG_DIR/ready-for-review-markers/$REPO_HASH.$SESSION_ID"
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
        mkdir -p "$CONFIG_DIR/.plan-review-active.d"
        printf '%s\n' "$CLAUDE_PID" > "$CONFIG_DIR/.plan-review-active.d/$SESSION_ID"
        # Backfill: a ROUTING.md Read landing just before this activate still
        # counts, via log-routing-read.sh's pending-read record. 5 minutes
        # covers a same-turn re-read while staying well inside
        # require-routing-read.sh's 60-minute freshness window, so a stale
        # Read from earlier in the session can't falsely backfill.
        PENDING_READ="$CONFIG_DIR/.plan-review-pending-read.d/$SESSION_ID"
        if [ -f "$PENDING_READ" ] && [ -n "$(find "$PENDING_READ" -mmin -5 2>/dev/null)" ]; then
          mkdir -p "$CONFIG_DIR/.plan-review-routing-read.d"
          touch "$CONFIG_DIR/.plan-review-routing-read.d/$SESSION_ID"
        fi
        ;;
      ready-for-review)
        SESSION_ID=$(_resolve_session_id) || exit 2
        CLAUDE_PID=$(_resolve_claude_pid) || exit 2
        mkdir -p "$CONFIG_DIR/.ready-for-review-active.d"
        printf '%s\n' "$CLAUDE_PID" > "$CONFIG_DIR/.ready-for-review-active.d/$SESSION_ID"
        ;;
      respond-pr)
        SESSION_ID=$(_resolve_session_id) || exit 2
        CLAUDE_PID=$(_resolve_claude_pid) || exit 2
        mkdir -p "$CONFIG_DIR/.respond-pr-active.d"
        printf '%s\n' "$CLAUDE_PID" > "$CONFIG_DIR/.respond-pr-active.d/$SESSION_ID"
        ;;
      memory-skill)
        SESSION_ID=$(_resolve_session_id) || exit 2
        CLAUDE_PID=$(_resolve_claude_pid) || exit 2
        mkdir -p "$CONFIG_DIR/.memory-skill-active.d"
        printf '%s\n' "$CLAUDE_PID" > "$CONFIG_DIR/.memory-skill-active.d/$SESSION_ID"
        ;;
      handoff)
        SESSION_ID=$(_resolve_session_id) || exit 2
        CLAUDE_PID=$(_resolve_claude_pid) || exit 2
        mkdir -p "$CONFIG_DIR/.handoff-active.d"
        printf '%s\n' "$CLAUDE_PID" > "$CONFIG_DIR/.handoff-active.d/$SESSION_ID"
        ;;
      issue-triage)
        # REPO_TARGET is stored in a sibling file, not a second line in the
        # PID marker: the shared liveness check strips all whitespace
        # before matching `^[0-9]+$`, so a second line would corrupt that
        # check for every marker kind. Restriction-polarity marker (see
        # usage() above): a live marker ACTIVATES
        # deny-gh-mutation-during-triage.sh's deny, rather than releasing a
        # standing one.
        case "$REPO_TARGET" in
          *[[:space:]]*)
            printf "marker.sh: 'activate issue-triage' target %s must not contain whitespace.\n" "$REPO_TARGET" >&2
            exit 2
            ;;
        esac
        SESSION_ID=$(_resolve_session_id) || exit 2
        CLAUDE_PID=$(_resolve_claude_pid) || exit 2
        mkdir -p "$CONFIG_DIR/.issue-triage-active.d"
        printf '%s\n' "$CLAUDE_PID" > "$CONFIG_DIR/.issue-triage-active.d/$SESSION_ID"
        printf '%s\n' "$REPO_TARGET" > "$CONFIG_DIR/.issue-triage-active.d/$SESSION_ID.repo-target"
        ;;
      *)
        printf "marker.sh: 'activate %s' is not valid. 'activate' supports: plan-review, ready-for-review, respond-pr, memory-skill, handoff, issue-triage\n" "$SKILL" >&2
        exit 2
        ;;
    esac
    ;;
  deactivate)
    case "$SKILL" in
      plan-review)
        SESSION_ID=$(_resolve_session_id) || exit 2
        rm -f "$CONFIG_DIR/.plan-review-active.d/$SESSION_ID"
        rm -f "$CONFIG_DIR/.plan-review-active.d/$SESSION_ID.planmode-path"
        rm -f "$CONFIG_DIR/.plan-review-routing-read.d/$SESSION_ID"
        rm -f "$CONFIG_DIR/.plan-review-pending-read.d/$SESSION_ID"
        ;;
      ready-for-review)
        SESSION_ID=$(_resolve_session_id) || exit 2
        rm -f "$CONFIG_DIR/.ready-for-review-active.d/$SESSION_ID"
        ;;
      respond-pr)
        SESSION_ID=$(_resolve_session_id) || exit 2
        rm -f "$CONFIG_DIR/.respond-pr-active.d/$SESSION_ID"
        ;;
      memory-skill)
        SESSION_ID=$(_resolve_session_id) || exit 2
        rm -f "$CONFIG_DIR/.memory-skill-active.d/$SESSION_ID"
        ;;
      handoff)
        SESSION_ID=$(_resolve_session_id) || exit 2
        rm -f "$CONFIG_DIR/.handoff-active.d/$SESSION_ID"
        ;;
      issue-triage)
        SESSION_ID=$(_resolve_session_id) || exit 2
        rm -f "$CONFIG_DIR/.issue-triage-active.d/$SESSION_ID"
        rm -f "$CONFIG_DIR/.issue-triage-active.d/$SESSION_ID.repo-target"
        ;;
      *)
        printf "marker.sh: 'deactivate %s' is not valid. 'deactivate' supports: plan-review, ready-for-review, respond-pr, memory-skill, handoff, issue-triage\n" "$SKILL" >&2
        exit 2
        ;;
    esac
    ;;
  clear-stale)
    DRY_RUN=0
    [ "$ARG2" = "--dry-run" ] && DRY_RUN=1
    EVICTED=0
    KEPT=0
    for active_dir in "$CONFIG_DIR"/.*-active.d; do
      [ -d "$active_dir" ] || continue
      dir_name=$(basename "$active_dir")
      for entry in "$active_dir"/*; do
        [ -f "$entry" ] || continue
        # Name-based exemption, not a PID-liveness question: this sibling
        # holds a declared plan-mode path, never a PID, so the ^[0-9]+$ test
        # below would always misread it as a dead marker and evict it.
        case "$entry" in
          *.planmode-path) continue ;;
        esac
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
  resolve-session-id)
    SESSION_ID=$(_resolve_session_id) || exit 2
    printf '%s' "$SESSION_ID"
    ;;
  status)
    SESSION_ID=$(_resolve_session_id) || exit 2
    REPO_ROOT=$(_resolve_repo_root) || exit 2
    REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
    REPO_HASH_PREFIX="$REPO_HASH."

    printf 'Completion markers (this repo):\n'

    # code-review: hash of the whole-repo staged diff -- same recipe as the
    # `write code-review` arm above. Capped so a stalled git diff can't hang
    # the whole status report; a killed process yields an empty value, which
    # _status_report_completion_marker already treats as absent/historical.
    CODE_REVIEW_VALUE=$(_lib_capped git -C "$REPO_ROOT" diff --cached | sha256sum | awk '{print $1}')
    if _status_report_completion_marker code-review "$CONFIG_DIR/code-review-markers" "$REPO_HASH_PREFIX" "$CODE_REVIEW_VALUE"; then
      _status_reconciliation_flag code-review "$REPO_ROOT"
    fi

    # skill-review: same recipe as the `write skill-review` arm above,
    # scoped to the SKILL.md/ROUTING.md pathspecs.
    SKILL_REVIEW_PATHSPECS=('claude/.claude/skills/**/SKILL.md' 'plugins/*/skills/**/SKILL.md' 'claude/.claude/skills/plan-review/ROUTING.md')
    SKILL_REVIEW_VALUE=$(_lib_capped git -C "$REPO_ROOT" diff --cached -- "${SKILL_REVIEW_PATHSPECS[@]}" | sha256sum | awk '{print $1}')
    if _status_report_completion_marker skill-review "$CONFIG_DIR/skill-review-markers" "$REPO_HASH_PREFIX" "$SKILL_REVIEW_VALUE"; then
      _status_reconciliation_flag skill-review "$REPO_ROOT" "${SKILL_REVIEW_PATHSPECS[@]}"
    fi

    # plan-review: same recipe as the `write plan-review` arm above (the
    # plan-mode sibling takes priority over _lib_active_plan_hash). A hash
    # that can't be computed (unreadable plan-mode target) is treated as
    # empty here rather than aborting -- `status` is a report, not a write,
    # and the other three markers still deserve their own report.
    PLANMODE_SIBLING="$CONFIG_DIR/.plan-review-active.d/$SESSION_ID.planmode-path"
    if PLANMODE_TARGET=$(_lib_capped cat "$PLANMODE_SIBLING" 2>/dev/null); then
      PLAN_REVIEW_VALUE=$(_lib_capped sha256sum -- "$PLANMODE_TARGET" 2>/dev/null | awk '{print $1}')
    else
      PLAN_REVIEW_VALUE=$(_lib_active_plan_hash "$REPO_ROOT") || PLAN_REVIEW_VALUE=""
    fi
    _status_report_completion_marker plan-review "$CONFIG_DIR/plan-review-markers" "$REPO_HASH_PREFIX" "$PLAN_REVIEW_VALUE"

    # ready-for-review: same recipe as the `write ready-for-review` arm
    # above. Unlike that arm, stderr is suppressed and an empty result is not
    # fatal -- a zero-commit repo has no HEAD to hash, which `status` must
    # report as absent rather than error on.
    READY_FOR_REVIEW_VALUE=$(_lib_capped git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null)
    _status_report_completion_marker ready-for-review "$CONFIG_DIR/ready-for-review-markers" "$REPO_HASH_PREFIX" "$READY_FOR_REVIEW_VALUE"

    printf '\nActive-bypass markers (this session):\n'
    _status_report_active_bypass plan-review ".plan-review-active.d" "$SESSION_ID"
    _status_report_active_bypass ready-for-review ".ready-for-review-active.d" "$SESSION_ID"
    _status_report_active_bypass respond-pr ".respond-pr-active.d" "$SESSION_ID"
    _status_report_active_bypass memory-skill ".memory-skill-active.d" "$SESSION_ID"
    _status_report_active_bypass handoff ".handoff-active.d" "$SESSION_ID"
    # issue-triage is restriction-polarity (see usage() above): "live" here
    # means the gh-mutation deny is currently ACTIVE for this session, not
    # that a standing deny is released.
    _status_report_active_bypass issue-triage ".issue-triage-active.d" "$SESSION_ID"
    ;;
esac
