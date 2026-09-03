#!/bin/bash
# hook-class: gate
# Gate: require git write operations to happen inside a linked worktree,
# not the main working tree. Three activation markers:
#   - <repo>/.claude/worktree-required  (committed repo sentinel — opt-out has no effect)
#   - ~/.claude/worktree-required       (machine-level personal default)
#   - <repo>/.claude/worktree-optout    (per-repo opt-out of machine default only)
#
# Motivation: concurrent Claude Code sessions on the same working tree can
# race — e.g. one session's `git reset --hard` silently wipes another's
# uncommitted edits. Working in linked worktrees (`git worktree add`)
# isolates each session's state — but only if each session gets its own
# worktree. A git command that resolves into a linked worktree passes
# through `_lib_worktree_collision_guard` (see _lib.sh) whenever that
# worktree's lock is already held, denying when a *different* live session
# holds it. Only a write can cause the guard to freshly acquire the lock,
# closing the gap where two sessions independently anchor into the
# identical worktree with no isolation between them at all.
#
# Threat model: this is a developer-machine guardrail against *accidental*
# main-tree writes, not an adversarial boundary — the agent is cooperative,
# not attacking the gate. When a command's effective working directory for a
# git write is genuinely ambiguous, this hook denies rather than guesses;
# the fallback (anchor cwd with a separate `cd` call, or work in a worktree)
# is cheap for a cooperative agent.
#
# Mechanism: `parse-git-command.py` (co-located) tokenizes the raw command
# with the stdlib shlex module — quote- and heredoc-aware, unlike a
# regex/sed split on the raw string — into an ordered stream of CD and GIT
# records plus any literal global `-C`. This hook threads cwd across the CD
# records and judges each GIT record:
#   - a read-only subcommand (allowlist) is always allowed — cwd is
#     irrelevant, since a read cannot clobber the working tree or index.
#   - a write subcommand is allowed only when its effective cwd resolves,
#     through a plain literal `cd`/`-C` chain, to a linked worktree of this
#     repo. Anything that keeps that resolution from being clean — a cd/-C
#     target needing shell expansion (~, $VAR, $(...), glob), a write inside
#     a subshell/command-substitution/backtick group, a write reached via
#     `||`, or more than one global `-C` — denies the write outright rather
#     than guessing which cwd it would actually run in.
# See parse-git-command.py's module docstring for the exact record grammar.
#
# python3 is a hard precondition for this hook (present or absent is
# checked explicitly below); any parser invocation failure (missing
# binary, missing script, non-zero exit, timeout) denies — a gate must
# fail closed on its own tooling. A command that mentions "git" only
# inside a heredoc body, a quoted string, or backticked/substituted text
# with no real invocation produces empty parser output, which is a
# legitimate, safe "nothing to judge" result and is allowed.
#
# Rollback: a bad parser could deny every main-tree git write, including
# `git pull` (not on the read-only allowlist) — the fix-forward path is
# itself gated. Escape hatch: write `.claude/worktree-optout` (a file
# write, not a git op) or remove the machine-level sentinel, then pull.
#
# Known gaps (what this model does NOT close):
#   - A `git` reached only through an alias, a wrapper script, or another
#     level of indirection this parser cannot see is undecidable — a
#     command containing no literal `git`/`*/git` token produces no GIT
#     record and is allowed, same as any non-git command.
#   - The `cd`/`git rev-parse` resolution calls below have no timeout of
#     their own (unlike the `python3` parser spawn) — a path backed by a
#     stalled network filesystem could hang the call. Accepted for a
#     developer-machine guardrail; not guarded against network-mount
#     hangs the way the parser spawn is guarded against a runaway parse.
#   - No cap on the size of the command string handed to the parser —
#     bounded in practice by the 5s parser timeout, not by an explicit
#     size check.
#   - The four _lib_capped-wrapped git/python3 calls in this file run
#     uncapped on a machine lacking both timeout(1) and gtimeout(1), so a
#     stalled call (locked index, network mount) hangs this gate rather than
#     degrading gracefully.
#   - The collision guard's liveness check (`kill -0` on a stored PID) can
#     rarely false-deny if that PID is reused by an unrelated process after
#     the original session exited — applies only to a foreign
#     (different-session_id) or old-format lock, since a same-session resume
#     recognizes its own lock by session_id before this check is ever
#     reached; bounded and self-clearing once whatever now holds that PID
#     number exits, not closed outright.
#   - A `locked` file write killed mid-write (the 5s `_lib_capped` timeout)
#     can leave a truncated `pid`/`session` field; a truncated session token
#     fails the session-id character-class regex and is treated as
#     unparseable, matching the guard's fail-closed default.
#   - A dead-PID lock the collision guard detects gets one claim-gated
#     reclaim attempt (`_lib_worktree_reclaim_dead_lock` in `_lib.sh`) before
#     falling back to a deny. A claim burnt by an interrupted eviction
#     permanently disables auto-eviction for that one lock identity, and the
#     deny message then names the manual `git worktree unlock <path>`
#     remedy instead. See `_lib.sh`'s own Known-gaps list for the full
#     bound.
#   - The collision guard's liveness check can't distinguish a dead PID from
#     one owned by a different user on a shared machine — out of scope for
#     this guardrail's single-developer-machine threat model.
#   - A non-contention write failure (a permission error, or `bash` missing
#     from PATH) is diagnosed the same as a transient race and told to
#     "retry", which is permanently wrong advice in that case.
#   - The collision guard makes a write into a linked worktree a
#     conditional allow: it depends on _lib_resolve_claude_pid resolving
#     this session's own live pid, which in turn depends on
#     capture-session-id.sh's SessionStart hook having already written a
#     session file. SessionStart necessarily precedes any PreToolUse in the
#     same session, so this holds by construction. Reads stay an
#     unconditional allow regardless of collision state.
#   - A fast-path collision denial falls through to full parsing rather
#     than denying directly, so a denied write now costs roughly double
#     the collision guard's git-subprocess calls plus a python3 spawn
#     versus a single call. Only the already-rare, already-blocked deny
#     path pays this, not the common allow path.
#   - An absent-lock command falls through to full parsing (python3 spawn)
#     for as long as the lock stays absent. For a write, this cost adds to
#     the guard's own acquisition cost rather than replacing it.
#   - The fresh-lock-acquisition `additionalContext` note's own pre-check
#     has a narrow TOCTOU window against the guard's own O_EXCL write. A
#     foreign session winning that race is still denied by the guard's own
#     session_id/pid re-check, so the cost is a wasted pre-check, not a
#     wrong message. On the fast path, this pre-check gates whether the
#     guard runs at all.
#   - The opposite-direction race also exists: a concurrent
#     `git worktree unlock` between the pre-check and the guard's own read
#     causes a real fresh acquisition that gets no message. On the fast
#     path this race is the only route to an unmessaged fresh acquisition,
#     since the guard is reached only when the lock is already present.
#   - Two near-simultaneous calls from the SAME session (e.g. two parallel
#     subagents sharing this worktree) can both see "unlocked" before
#     either write lands, firing the note twice for one real acquisition.
#     This applies to the slow path's own pre-check and to
#     require-worktree-for-file-writes.sh's identical-shaped pre-check; the
#     fast path above runs no pre-check and emits no note. Low stakes: both
#     calls belong to the same session, and the note's substance stays true
#     either way, just possibly reported twice instead of once.
#   - That pre-check is a bash builtin `[ -e ... ]` (a single `stat(2)`),
#     not wrapped in `_lib_capped`, because capping it would add a
#     `timeout`+`test` subprocess spawn to the fast path's steady-state
#     case (an already-self-locked worktree), which must stay
#     subprocess-free. A stalled network-mounted worktree path hangs this
#     stat the same way it hangs `cd`.
#   - The slow path defers its note to a single emit after the record loop
#     (see FRESH_LOCK_CONTEXT below), folded into a later deny's reason
#     instead of dropped when one occurs. So a single fresh acquisition
#     isn't dropped by an unrelated later deny in the same command. A
#     command chaining fresh-lock writes into two DIFFERENT worktrees still
#     reports only the later one on an eventual allow, though —
#     FRESH_LOCK_CONTEXT is overwritten per record, not accumulated across
#     worktrees.
#   - A `||`/`&`-chained git write from a worktree with an absent lock
#     denies until this session's first lock acquisition — see
#     docs/design-decisions.md §32.
#   - A python3-less machine cannot complete a first git operation, read or
#     write, against a never-locked worktree — see
#     docs/design-decisions.md §32.
#   - A python3-less write against an already foreign-locked worktree
#     denies citing python3 rather than the true foreign-lock holder — see
#     docs/design-decisions.md §32.
#
# Scope boundary: `_lib.sh`'s `_lib_split_fragments`/`_lib_extract_git_subcmd`/
# `_lib_fragment_invokes_git` (used by deny-pii-in-commits.sh,
# deny-private-project-refs.sh, require-ready-for-review.sh,
# deny-reviewer-tree-mutation.sh) are NOT reused here.
# - Those hooks judge commit-message/PR-readiness content, or
#   (deny-reviewer-tree-mutation.sh) whether a git subcommand is read-only.
# - None need a resolved cwd target.
# - The heredoc/quote misparse class COMMAND_UNQUOTED's quote-stripping
#   closes rarely bites any of them.
# - Their boolean-fragment-test shape doesn't fit the cwd-threading records
#   this hook needs.
# Two parsers coexist deliberately — a named exception to
# single-source-of-truth, not an oversight.

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
  emit_deny "Blocked by worktree-enforcement hook: could not source _lib.sh — hook cannot evaluate git discipline safely."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "Blocked by worktree-enforcement hook: could not parse tool-input JSON. Refusing to evaluate git discipline under malformed input."

# Defensive: prevent GIT_DIR / GIT_WORK_TREE env overrides from making the
# main tree impersonate a linked worktree via rev-parse output.
# HOME is not unset: it comes from the OS user session (trusted), unlike git env vars.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE

CWD=$(printf '%s\n' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
[ -z "$CWD" ] && CWD="$PWD"

# Fast-path: commands that don't mention `git` as a word are not our
# concern. A plain `*git*` substring check false-positives on `.github`,
# `.gitignore`, `github.com`, and similar, blocking harmless reads like
# `ls .github/workflows/`. Require a non-alnum boundary (or string edge) on
# both sides so `git` fires only as a command word.
if ! [[ "$COMMAND" =~ (^|[^[:alnum:]])git([^[:alnum:]]|$) ]]; then
  exit 0
fi

# Find the repo. Outside a git repo, nothing to enforce.
REPO_ROOT=$(cd "$CWD" 2>/dev/null && _lib_capped git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$REPO_ROOT" ]; then
  exit 0
fi

# Three-marker gate: repo sentinel, machine sentinel, per-repo opt-out.
_lib_worktree_enforcement_active "$REPO_ROOT" || exit 0

# "Am I in a linked worktree?" check, and the enforced repo's identity
# anchor for later effective-cwd comparisons. For the main working tree,
# --git-dir and --git-common-dir return the same absolute path. For a
# linked worktree, --git-dir points at <common>/worktrees/<name> while
# --git-common-dir still points at <common> — the same value regardless of
# which worktree of the repo you query it from, which is what makes it a
# reliable identity anchor for "does this other cwd belong to this repo?"
# Both paths come from one rev-parse call (git prints one line per query
# flag, in the order given) rather than two separate subprocess spawns.
{
  read -r SESSION_GIT_DIR_ABS
  read -r REPO_GIT_COMMON_DIR
} < <(cd "$CWD" 2>/dev/null && _lib_capped git rev-parse --absolute-git-dir --path-format=absolute --git-common-dir 2>/dev/null)
if [ -z "${SESSION_GIT_DIR_ABS:-}" ] || [ -z "${REPO_GIT_COMMON_DIR:-}" ]; then
  emit_deny "Blocked by worktree-enforcement hook: could not determine git state for the session working directory. Refusing to evaluate git discipline under unresolvable git state."
  exit 0
fi
SESSION_IS_WORKTREE=false
if [ "$SESSION_GIT_DIR_ABS" != "$REPO_GIT_COMMON_DIR" ]; then
  SESSION_IS_WORKTREE=true
fi

# Relocation-aware fast path: already in a linked worktree, and nothing in
# the command could move a write elsewhere, so it is safe without the
# parser. This guard is load-bearing — a worktree session running
# `cd <main-repo> && git reset --hard` must still be caught, so any hint of
# relocation (a `cd` word, a `-C` flag, or a subshell/substitution/backtick
# group) falls through to full parsing regardless of session cwd. The
# check is a deliberately conservative over-approximation — it may route a
# command with no real relocation risk (e.g. `-C` mentioned only in a
# comment string) to the parser too, which just costs a python3 spawn, not
# a false allow. The collision guard below is called only when this
# worktree's lock is already present. When the lock is absent, the guard
# call is skipped and the command falls through to full parsing, where a
# read is allowed with no guard call at all and only a write record
# re-runs the guard.
if $SESSION_IS_WORKTREE; then
  if ! [[ "$COMMAND" =~ (^|[^[:alnum:]_])cd([^[:alnum:]_]|$) ]] \
     && [[ "$COMMAND" != *'-C'* ]] \
     && [[ "$COMMAND" != *'('* ]] \
     && [[ "$COMMAND" != *'`'* ]]; then
    # See _lib_worktree_lock_absent (_lib.sh) for the pre-check contract.
    if ! _lib_worktree_lock_absent "$SESSION_GIT_DIR_ABS"; then
      _lib_worktree_collision_guard "$CWD" "$REPO_GIT_COMMON_DIR" >/dev/null && exit 0
    fi
    # Collision guard denied, or the lock is absent and acquisition is
    # deferred to the slow path. Neither branch can tell read from write
    # without the git subcommand, so this falls through to full parsing.
    # ALLOWED_RE there re-runs the guard only for an actual write record,
    # exempting reads.
  fi
fi

# From here on: the session is in the main tree, the command could
# plausibly relocate a write, or the fast path's own collision-guard call
# denied above (it cannot tell read from write on its own). Parse it
# properly.
PARSER="$(dirname "$0")/parse-git-command.py"
if ! command -v python3 >/dev/null 2>&1; then
  emit_deny "Blocked by worktree-enforcement hook: python3 is required to parse this command safely and was not found on PATH. Install python3 (see claude-config README) or run this git operation from inside a linked worktree whose lock this session already holds, where the fast path above does not require python3."
  exit 0
fi

# 5s ceiling matches _lib_jq's and require-plan-review.sh's established
# precedent for local, non-network subprocess calls. Parsing a Bash
# command string is a pure in-memory operation with no I/O, so this leaves
# ample headroom; a timeout here (exit 124) is treated as a parser failure
# and denies, same as any other non-zero exit.
RECORDS=$(printf '%s' "$COMMAND" | _lib_capped python3 "$PARSER" 2>/dev/null)
PARSER_EXIT=$?
if [ "$PARSER_EXIT" -ne 0 ]; then
  emit_deny "Blocked by worktree-enforcement hook: the command parser exited abnormally (exit $PARSER_EXIT) or timed out. Refusing to evaluate git discipline under an unparseable command. If this persists, check that claude/.claude/hooks/parse-git-command.py is present and executable with python3."
  exit 0
fi

# Pulled from _lib.sh. Built via a read loop, not `mapfile` (bash-4+, breaks
# macOS's shipped bash 3.2.57 — see test_no_bash4_constructs.py).
ALLOWED_SUBCMDS=()
while IFS= read -r subcmd; do
  ALLOWED_SUBCMDS+=("$subcmd")
done < <(_lib_readonly_git_subcmds)
ALLOWED_RE=$(IFS='|'; echo "${ALLOWED_SUBCMDS[*]}")

# Threaded across CD records in order. `resolvable` becomes permanently
# false for the rest of this command once any cd cannot be trusted (an
# in-group cd, or a target needing shell expansion) — a write judged after
# that point is denied regardless of any later cd, since the parser cannot
# tell which cwd it would really run in.
running_cwd="$CWD"
resolvable=true

# Set by the write-allow branch below when its collision-guard call is the
# one that freshly acquired the worktree lock. Deferred to a single emit
# after the loop, not printed inline: a later record in this same command
# can still deny. Interleaving an allow-context payload with a deny
# payload on stdout would produce two concatenated JSON documents instead
# of one parseable envelope.
FRESH_LOCK_CONTEXT=""

# Wraps emit_deny for every call site inside the loop below: a deny here
# exits before the post-loop FRESH_LOCK_CONTEXT emit ever runs, so an
# earlier record's real lock acquisition would otherwise be dropped
# silently. Folds it into the deny reason instead, leaving the reason text
# byte-for-byte unchanged when FRESH_LOCK_CONTEXT is still empty.
emit_deny_folding_fresh_lock_context() {
  local reason="$1"
  if [ -n "$FRESH_LOCK_CONTEXT" ]; then
    reason="$reason $FRESH_LOCK_CONTEXT"
  fi
  emit_deny "$reason"
}

while IFS=$'\x1f' read -r rec_type field1 field2 field3 field4 field5; do
  [ -z "$rec_type" ] && continue
  case "$rec_type" in
    SENTINEL)
      emit_deny_folding_fresh_lock_context "Blocked by worktree-enforcement hook: $field1. This is a repo where worktree discipline is active (repo-level .claude/worktree-required committed, or your machine-level ~/.claude/worktree-required). To exempt this repo from machine-level enforcement, add .claude/worktree-optout. Run git write operations from inside a linked worktree — either change the session cwd into an existing worktree under .claude/worktrees/, use the EnterWorktree tool, or spawn an agent with isolation: worktree."
      exit 0
      ;;
    CD)
      target="$field1"
      in_group="$field3"
      if [ "$in_group" = "1" ] || [ -z "$target" ]; then
        resolvable=false
        continue
      fi
      if $resolvable; then
        # No timeout wrapper here (see header "Known gaps") — cd is a shell
        # builtin, so guarding it would require an extra bash -c layer for a
        # hang scenario (a network-mounted worktree path) this repo's other
        # hooks don't guard against either.
        new_cwd=$(cd "$running_cwd" 2>/dev/null && cd "$target" 2>/dev/null && pwd -P 2>/dev/null)
        if [ -n "$new_cwd" ]; then
          running_cwd="$new_cwd"
        else
          resolvable=false
        fi
      fi
      ;;
    GIT)
      subcmd="$field1"
      c_path="$field2"
      c_status="$field3"
      op="$field4"
      in_group="$field5"

      # Reads are always allowed: they cannot clobber the working tree or
      # index, so cwd, -C, group nesting, and the preceding operator are
      # all irrelevant to the invariant this hook protects.
      if [[ "$subcmd" =~ ^($ALLOWED_RE)$ ]]; then
        continue
      fi

      # Write, and its effective cwd cannot be trusted: a group-scoped
      # write (subshell cd does not affect the parent shell), a write
      # reached only if a preceding command failed (`||`) or backgrounded
      # (`&` — a backgrounded `cd` forks a subshell and never changes the
      # parent shell's cwd either, so a write after `&` cannot trust
      # whatever `running_cwd` currently holds), an unresolved cd earlier
      # in this command, or an unresolved/ambiguous `-C`.
      if [ "$in_group" = "1" ] || [ "$op" = "||" ] || [ "$op" = "&" ] || [ "$c_status" = "UNRESOLVED" ] || ! $resolvable; then
        emit_deny_folding_fresh_lock_context "Blocked by worktree-enforcement hook: 'git $subcmd' is a write whose effective working directory cannot be safely determined (a cd/-C target needing shell expansion, a write inside a subshell/command-substitution/backtick group, a write reached via '||' or backgrounded with '&', or more than one global -C flag), and this session is running in a repo where worktree discipline is active (repo-level .claude/worktree-required committed, or your machine-level ~/.claude/worktree-required). To exempt this repo from machine-level enforcement, add .claude/worktree-optout. Run this as a literal 'cd <worktree-path> && git ...' or 'git -C <worktree-path> ...' with a plain path — not a variable, glob, subshell, or backgrounded cd — or spawn an agent with isolation: worktree."
        exit 0
      fi

      effective_cwd="$running_cwd"
      case "$c_status" in
        NONE)
          ;;
        LITERAL)
          resolved_c=$(cd "$running_cwd" 2>/dev/null && cd "$c_path" 2>/dev/null && pwd -P 2>/dev/null)
          if [ -z "$resolved_c" ]; then
            emit_deny_folding_fresh_lock_context "Blocked by worktree-enforcement hook: 'git $subcmd -C $c_path' targets a working directory that does not exist or is unreachable from '$running_cwd'. This is a repo where worktree discipline is active (repo-level .claude/worktree-required committed, or your machine-level ~/.claude/worktree-required). To exempt this repo from machine-level enforcement, add .claude/worktree-optout."
            exit 0
          fi
          effective_cwd="$resolved_c"
          ;;
        *)
          # UNRESOLVED is already denied above; anything else is a
          # parser/shell contract mismatch this hook has never seen —
          # deny rather than silently treat it as NONE (which would
          # discard a real -C target and judge the write against the
          # wrong cwd).
          emit_deny_folding_fresh_lock_context "Blocked by worktree-enforcement hook: 'git $subcmd' carries an unrecognized -C status ('$c_status') that this hook does not know how to judge safely. This likely indicates a parser/hook version mismatch. Refusing to evaluate git discipline under an unrecognized record shape."
          exit 0
          ;;
      esac

      # Both queries in one rev-parse call (git prints one line per query
      # flag, in the order given) rather than two separate subprocess spawns.
      eff_git_dir=""
      eff_common_dir=""
      {
        read -r eff_git_dir
        read -r eff_common_dir
      } < <(cd "$effective_cwd" 2>/dev/null && _lib_capped git rev-parse --absolute-git-dir --path-format=absolute --git-common-dir 2>/dev/null)
      if [ -z "$eff_git_dir" ] || [ -z "$eff_common_dir" ] || [ "$eff_common_dir" != "$REPO_GIT_COMMON_DIR" ]; then
        emit_deny_folding_fresh_lock_context "Blocked by worktree-enforcement hook: 'git $subcmd' targets a working directory outside this repository (or its git state could not be determined), so it cannot be confirmed safe. This is a repo where worktree discipline is active (repo-level .claude/worktree-required committed, or your machine-level ~/.claude/worktree-required). To exempt this repo from machine-level enforcement, add .claude/worktree-optout."
        exit 0
      fi

      if [ "$eff_git_dir" != "$eff_common_dir" ]; then
        # Linked worktree of this repo — check for a same-worktree
        # collision with another live session before allowing.
        # See _lib_worktree_lock_absent (_lib.sh) for the pre-check contract.
        WAS_UNLOCKED=false
        _lib_worktree_lock_absent "$eff_git_dir" && WAS_UNLOCKED=true
        COLLISION_REASON=$(_lib_worktree_collision_guard "$effective_cwd" "$REPO_GIT_COMMON_DIR") || {
          emit_deny_folding_fresh_lock_context "Blocked by worktree-enforcement hook: 'git $subcmd' — $COLLISION_REASON. This is a repo where worktree discipline is active (repo-level .claude/worktree-required committed, or your machine-level ~/.claude/worktree-required)."
          exit 0
        }
        if $WAS_UNLOCKED; then
          FRESH_LOCK_CONTEXT="Running 'git $subcmd' re-acquired the worktree lock for $effective_cwd, since worktree discipline allows only one live session per linked worktree at a time. If this was unintended, run 'git worktree unlock $effective_cwd' again."
        fi
        continue
      fi

      emit_deny_folding_fresh_lock_context "Blocked by worktree-enforcement hook: 'git $subcmd' is not on the read-only allowlist, and this write targets the MAIN working tree of a repo where worktree discipline is active (repo-level .claude/worktree-required committed, or your machine-level ~/.claude/worktree-required). To exempt this repo from machine-level enforcement, add .claude/worktree-optout. Run git write operations from inside a linked worktree — cd into an existing worktree under .claude/worktrees/, create one with 'git worktree add .claude/worktrees/<branch> -b <branch>' (that specific command is allowed on the main tree), or spawn an agent with isolation: worktree. See claude-config README 'Worktree enforcement' for details.$(_lib_stray_marker_hint "$REPO_ROOT")"
      exit 0
      ;;
    *)
      # Unrecognized record type — a future parser change added a record
      # kind this hook doesn't know how to judge, or the wire format got
      # corrupted in transit. Deny rather than silently skip the record:
      # an unjudged record could represent a real git write.
      emit_deny_folding_fresh_lock_context "Blocked by worktree-enforcement hook: received an unrecognized record type ('$rec_type') from the command parser. This likely indicates a parser/hook version mismatch. Refusing to evaluate git discipline under an unrecognized record shape."
      exit 0
      ;;
  esac
done <<< "$RECORDS"

if [ -n "$FRESH_LOCK_CONTEXT" ]; then
  _lib_emit_allow_with_context "$FRESH_LOCK_CONTEXT"
fi
exit 0
