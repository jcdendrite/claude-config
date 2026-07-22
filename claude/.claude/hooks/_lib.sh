#!/bin/bash
# Shared helper library sourced by require-*.sh hooks and scripts/marker.sh.
# Keep this file the single source of truth for any recipe that must produce
# byte-identical output on both the read side (hooks) and the write side
# (marker.sh). Source it; do not invoke it directly.

# Backstop against a hung jq (~5s, not a per-fire latency budget).
# Cites guard-settings-session-keys.sh's git_capped 5s precedent.
# Fallback to bare jq when timeout(1) is absent (BSD/macOS default).
# install.sh warns about missing timeout at onboarding time.
# Security implication: on BSD/macOS without coreutils, a stalled or
# replaced jq binary can hold a gate hook open indefinitely. The harness's
# own hook timeout (if any) then governs — not this wrapper.
_lib_jq() {
  if command -v timeout >/dev/null 2>&1; then
    timeout 5 jq "$@"
  else
    jq "$@"
  fi
}

# Same 5s backstop and same BSD/macOS fallback as _lib_jq, for any other
# command that reads the filesystem and can stall on it (git against a
# locked .git/index, sha256sum against a dead NFS mount). Callers MUST check
# the exit status: a bare `timeout 5 git ...` is not just uncapped when
# timeout(1) is missing, it is "command not found" (127), which silently
# yields empty output on stock macOS.
# Usage: out=$(_lib_capped git -C "$root" ls-files ...) || <fail closed>
_lib_capped() {
  if command -v timeout >/dev/null 2>&1; then
    timeout 5 "$@"
  else
    "$@"
  fi
}

# Reads stdin into INPUT (global), extracts TOOL_NAME and COMMAND (globals)
# via a single _lib_jq call using ASCII Unit Separator (0x1f) as delimiter.
# The single call surfaces a structural-type error when .tool_input is non-object
# (jq non-zero exit).
#
# Three deny paths protect against silent-allow:
#   (a) jq non-zero exit (parse failure, timeout exit=124, missing jq binary)
#   (b) empty INPUT (stdin EOF, closed pipe, harness misbehavior)
#   (c) empty TOOL_NAME (valid JSON but PreToolUse contract not honored, e.g. "{}")
# Per Anthropic PreToolUse contract, every legitimate event has a non-empty
# .tool_name; absence indicates the call did not originate from a real tool
# invocation. Without (b)/(c), downstream gates that early-exit on
# `[ "$TOOL_NAME" = "Bash" ]` silently allow on zero-byte or "{}" inputs.
#
# Contract: if this function returns, parse succeeded AND TOOL_NAME is set.
# On failure, calls emit_deny (caller-defined) with the supplied message and
# exits 0 — caller never checks $?. Never call this with `|| true` or in
# a pipeline. CALLER MUST define emit_deny before sourcing _lib.sh so this
# helper can resolve it; the canonical pattern (define-emit_deny-then-source)
# is enforced by test_hook_alignment.py.
_lib_parse_tool_input_or_deny() {
  local deny_msg="${1:-Blocked: could not parse tool-input JSON.}"
  INPUT=$(cat)  # command substitution strips trailing newlines — safe for JSON payloads
  if [ -z "$INPUT" ]; then
    emit_deny "$deny_msg"
    exit 0
  fi
  # Single jq call extracts both fields delimited by ASCII Unit Separator (0x1f)
  # rather than newlines, preventing a tool_name value containing an embedded
  # newline from corrupting COMMAND via head/tail line splitting. Unit Separator
  # cannot appear in a valid Claude Code tool name or shell command.
  # The .tool_input.command extraction additionally surfaces a structural-type
  # error when .tool_input is non-object (e.g. "Cannot index string with string
  # 'command'"), returning non-zero.
  local jq_out
  # WARNING: the format string below contains a literal 0x1f (ASCII Unit Separator)
  # byte between the two interpolated fields - invisible in editors and diff views.
  # Do not remove it. test_lib.py::test_valid_bash_payload_returns_ok will fail
  # immediately if the delimiter is absent, catching accidental deletion.
  jq_out=$(printf '%s\n' "$INPUT" | _lib_jq -r '"\(.tool_name // "")\(.tool_input.command // "")"' 2>/dev/null)
  local jq_exit=$?
  if [ "$jq_exit" -ne 0 ]; then
    emit_deny "$deny_msg"
    exit 0
  fi
  TOOL_NAME="${jq_out%%$'\x1f'*}"
  # shellcheck disable=SC2034 # set for hook scripts that source this file and reference $COMMAND
  COMMAND="${jq_out#*$'\x1f'}"
  # Embedded newline in TOOL_NAME means the payload violated the PreToolUse
  # contract; deny rather than allow with a corrupted TOOL_NAME value.
  if [ -z "$TOOL_NAME" ]; then
    emit_deny "$deny_msg"
    exit 0
  fi
  case "$TOOL_NAME" in
    *$'\n'*) emit_deny "$deny_msg"; exit 0 ;;
  esac
}

# Compute the marker repo-hash for an absolute repo-toplevel path.
# Input must have no trailing newline -- printf '%s' omits one, so the SHA
# covers exactly the bytes of $1.
# Usage: hash=$(_marker_lib_repo_hash "$REPO_ROOT")
_marker_lib_repo_hash() {
  printf '%s' "$1" | sha256sum | awk '{print $1}'
}

# Compute a content-addressed hash of the "active" plan file set in a
# repo's .claude/plans/ directory, for the plan-review completion marker.
# "Active" means untracked, or tracked-and-modified-vs-HEAD; a plan that is
# tracked and byte-identical to HEAD is historical (its PR shipped) and is
# excluded. Hashes repo-relative paths AND contents, so editing an active
# plan (including a ledger row) changes the hash and re-arms the gate.
# Paths are hashed repo-relative rather than absolute because the write side
# and read side resolve the repo root independently; an absolute path would
# fold any difference between those two resolutions into the digest.
#
# Three-outcome contract -- exit status disambiguates stdout, because
# "nothing to gate" and "could not compute" must never collapse onto the
# same caller-visible signal:
#   - exit 0, non-empty stdout: that hash is the active plan set.
#   - exit 0, empty stdout:     no plan is active; the gate is disarmed.
#   - exit 1, stdout = the path of the plan file that could not be hashed
#     (unreadable, vanished mid-enumeration, sha256sum failed). Callers
#     MUST fail closed. Treating this as the disarmed case is fail-*open*:
#     it lets an unreviewed plan edit through on a transient disk or
#     permission blip, silently and with nothing logged. Reusing stdout for
#     the offending path keeps this to one call site with no subshell
#     visibility problem -- the exit status already says which meaning
#     applies.
#
# Determinism contract (write side [marker.sh] and read side
# [require-plan-review.sh] must agree byte-for-byte, or the gate wedges):
#   - `LC_ALL=C sort` on the file list -- a bare `sort` honors
#     $LC_COLLATE, and the write-side (Bash-tool locale) and read-side
#     (harness hook environment) callers can differ, flipping order on
#     >=2 active plans and producing a false-deny. `-u` also collapses the
#     overlap between the two git queries, which are unioned, not disjoint.
#   - Path and per-file content-hash are newline-delimited per entry. With
#     today's fixed-width 64-hex digests the concatenation would already be
#     unambiguous without them, so this is defensive rather than
#     load-bearing: it keeps the serialization injective if the digest ever
#     becomes variable-width, and keeps the hashed input readable when
#     debugging a mismatch. Do not cite it as a live collision defense.
#   - Every digest is captured into a variable and tested for emptiness,
#     rather than being returned as a pipeline's exit status. This is what
#     makes the contract independent of the caller's shell options, and it
#     is load-bearing: marker.sh sources this file under `set -u` with no
#     `pipefail`, where a pipeline reports only its LAST command's status
#     -- a missing or failed `sha256sum` still leaves `awk` exiting 0
#     having printed nothing, which would silently misclassify a hash
#     failure as "no active plan". The emptiness check, not `pipefail`, is
#     the guard.
# Usage: hash=$(_lib_active_plan_hash "$REPO_ROOT")
_lib_active_plan_hash() {
  local repo_root="$1"
  local plans_dir="$repo_root/.claude/plans"
  [ -d "$plans_dir" ] || return 0

  # "Active" is exactly `untracked` UNION `tracked and modified vs HEAD`, so
  # ask git for those two sets directly rather than listing the directory and
  # probing each file's status. Enumerate-then-probe costs two git spawns per
  # plan file (~1.2s on a 61-plan directory) and this runs on every
  # Write/Edit/MultiEdit/ExitPlanMode.
  #
  # Every git call is capped AND status-checked. An unchecked call is the
  # dangerous shape here: a failed or timed-out enumeration yields fewer
  # files, and fewer files still hashes cleanly -- so both sides would agree
  # on a hash computed over an active plan that neither of them saw, and the
  # gate would open with nothing logged. A partial enumeration is therefore
  # treated exactly like an unhashable file: fail closed.
  # :(glob) confines the `*` to one path segment, preserving the maxdepth-1
  # scope; --others without --exclude-standard keeps gitignored plans in the
  # set, since an ignored plan is still an unreviewed plan.
  # --diff-filter=d drops deletions: a tracked plan deleted from the worktree
  # is reported as modified but has no bytes left to hash, and treating it as
  # active would fail the hash and deny forever instead of disarming.
  # core.quotePath=false keeps non-ASCII filenames raw rather than C-escaped.
  # Newline-delimited (not -z): a plan filename containing a newline is
  # already unsupported, and -z would force the output through a command
  # substitution, which strips NUL bytes.
  local -a plan_pathspecs=(":(glob).claude/plans/*.md" ":(glob).claude/plans/*.txt")
  local untracked_plans modified_plans
  untracked_plans=$(_lib_capped git -C "$repo_root" -c core.quotePath=false \
    ls-files --others -- "${plan_pathspecs[@]}" 2>/dev/null) || {
    printf '%s' "$plans_dir"
    return 1
  }
  if _lib_capped git -C "$repo_root" rev-parse --verify -q HEAD >/dev/null 2>&1; then
    modified_plans=$(_lib_capped git -C "$repo_root" -c core.quotePath=false \
      diff --name-only --diff-filter=d HEAD -- "${plan_pathspecs[@]}" 2>/dev/null) || {
      printf '%s' "$plans_dir"
      return 1
    }
  else
    # No HEAD to diff against means nothing has shipped yet, so every
    # tracked plan still counts as active.
    modified_plans=$(_lib_capped git -C "$repo_root" -c core.quotePath=false \
      ls-files -- "${plan_pathspecs[@]}" 2>/dev/null) || {
      printf '%s' "$plans_dir"
      return 1
    }
  fi

  local plan_file
  local -a active_files=()
  while IFS= read -r plan_file; do
    [ -n "$plan_file" ] || continue
    active_files+=("$plan_file")
  done < <(printf '%s\n%s\n' "$untracked_plans" "$modified_plans" | LC_ALL=C sort -u)

  [ "${#active_files[@]}" -gt 0 ] || return 0

  local file file_hash combined=""
  for file in "${active_files[@]}"; do
    file_hash=$(_lib_capped sha256sum -- "$repo_root/$file" 2>/dev/null | awk '{print $1}')
    if [ -z "$file_hash" ]; then
      # Unreadable or vanished mid-enumeration. Name the offending file on
      # stdout so the caller's deny message can point the user at it.
      printf '%s' "$repo_root/$file"
      return 1
    fi
    combined+="$file"$'\n'"$file_hash"$'\n'
  done

  local digest
  digest=$(printf '%s' "$combined" | sha256sum | awk '{print $1}')
  if [ -z "$digest" ]; then
    printf '%s' "$plans_dir"
    return 1
  fi
  printf '%s' "$digest"
}

# Decide whether a shell fragment actually invokes `git`, not just mentions it
# as a substring of a path or URL. Walks whitespace-separated words; returns
# success iff any word equals `git` or ends in `/git`. Env-var prefixes
# (GIT_DIR=... git ...), wrapper commands (eval, sudo, xargs), and `git` as a
# non-first word are all handled by scanning every word, not just the first.
#
# Rejects: `ls .github/`, `cat .gitignore`, `grep github.com`, `./git-foo`.
# Accepts: `git log`, `sudo git commit`, `GIT_DIR=x git push`, `/usr/bin/git status`.
_lib_fragment_invokes_git() {
  local fragment="$1"
  local saved_opts=$-
  set -f
  local found=false word
  for word in $fragment; do
    if [[ "$word" == "git" || "$word" == */git ]]; then
      found=true
      break
    fi
  done
  if [[ "$saved_opts" != *f* ]]; then set +f; fi
  $found
}

# Extract the git subcommand from a fragment like "git -C path push -u origin"
# or "GIT_DIR=x git push". Walks words to find the `git` command word (same
# logic as _lib_fragment_invokes_git), then continues from there — skipping
# global flags that consume the next word and other flags — to return the first
# bare word (the subcommand). Strips trailing non-alnum characters so that
# `push)` from paren-group splitting yields `push`. Globbing disabled to
# prevent expansion of wildcards in the command text.
_lib_extract_git_subcmd() {
  local fragment="$1"
  local saved_opts=$-
  set -f
  local past_git=false skip_next=false subcmd="" word
  for word in $fragment; do
    if ! $past_git; then
      if [[ "$word" == "git" || "$word" == */git ]]; then
        past_git=true
      fi
      continue
    fi
    if $skip_next; then skip_next=false; continue; fi
    case "$word" in
      -C|-c|--git-dir|--work-tree|--namespace|--super-prefix|--config-env)
        skip_next=true ;;
      -*) ;;
      *) subcmd="${word%%[^a-zA-Z0-9_-]*}"; break ;;
    esac
  done
  if [[ "$saved_opts" != *f* ]]; then set +f; fi
  printf '%s' "$subcmd"
}

# Split a shell command string into fragments on shell operators (;, &&, ||, |,
# $(...), backticks). Each fragment may invoke a distinct command. Leading/
# trailing parentheses are stripped from each fragment so that `(cd /x; git push)`
# yields `git push` as a clean fragment rather than `git push)`.
_lib_split_fragments() {
  printf '%s' "$1" \
    | sed -E 's/;/\n/g; s/&&/\n/g; s/\|\|/\n/g; s/\|/\n/g; s/\$\(/\n/g; s/`/\n/g' \
    | sed -E 's/^[[:space:]]*\(//; s/\)[[:space:]]*$//'
}

# Decide whether a command chains `marker.sh write <skill>` before its first
# `git commit`. PreToolUse hooks fire once per Bash tool call before the chain
# runs, so an on-disk marker check denies naturally-typed forms like
# `marker.sh write code-review && git commit`. When the same Bash call will
# write the marker before invoking commit, the in-chain marker.sh invocation
# is the same evidence the on-disk marker would later provide — marker.sh is
# the only sanctioned writer in either case.
#
# **Anchored at command start, not fragment start.** A fragment-walking
# approach (split on `&&` / `;` / `|`, then scan each fragment) would treat
# heredoc-body lines and wrapper-command arguments as "fragments" — so
# `echo ~/.claude/scripts/marker.sh write code-review && git commit` or
# `cat <<EOF | bash\n...marker.sh write code-review\nEOF && git commit` would
# wedge the gate open without ever actually invoking marker.sh. The strict
# command-start anchor here mirrors enforce-marker-script-shape.sh's
# VALID_CHAINED_COMMIT_PATTERN: only the literal shape
# `marker.sh write <skill>{,&& marker.sh write <skill2>...} && git commit`
# is honored. Wrapper commands, env-var prefixes, `bash -c`, heredoc bodies,
# and pipes all fail the anchor.
#
# Usage: _lib_chains_marker_write_before_commit "$COMMAND" code-review
# Returns 0 (true) if the command matches the sanctioned chained shape AND
# the target skill appears among the chained writes.
_lib_chains_marker_write_before_commit() {
  local command="$1" skill="$2"
  # Step 1: command matches the sanctioned chained shape (mirrors
  # enforce-marker-script-shape.sh's VALID_CHAINED_COMMIT_PATTERN). One or
  # more marker.sh write fragments joined by `&&`, then git commit. Anchored
  # so wrapper commands cannot trick the gate.
  if ! printf '%s' "$command" | grep -qE \
    "^[[:space:]]*((~|/[A-Za-z0-9_./-]+)/\.claude/scripts/marker\.sh[[:space:]]+write[[:space:]]+(code-review|skill-review|plan-review|ready-for-review)[[:space:]]*&&[[:space:]]*)+git[[:space:]]+commit([[:space:]].*)?$"; then
    return 1
  fi
  # Step 2: target skill is among the chained writes. A chain like
  # `marker.sh write skill-review && git commit` must not authorize a
  # code-review-gated commit.
  printf '%s' "$command" | grep -qE \
    "(~|/[A-Za-z0-9_./-]+)/\.claude/scripts/marker\.sh[[:space:]]+write[[:space:]]+${skill}([[:space:]]|$)"
}

# _lib_worktree_enforcement_active REPO_ROOT
# Returns 0 (true) when worktree discipline is active for the given repo root.
# Three-marker logic:
#   1. Committed repo sentinel (.claude/worktree-required at repo root): hard requirement,
#      travels via git pull. Opt-out cannot defeat it.
#   2. Machine sentinel (~/.claude/worktree-required): personal default-on for all repos
#      on this machine. Defeated by the per-repo opt-out.
#   3. Per-repo opt-out (.claude/worktree-optout at repo root): exempts this repo from
#      the machine sentinel only — has no effect when the repo sentinel is present.
# On filesystem error (EACCES, ESTALE, NFS timeout), [ -f ] returns false — the machine
# sentinel silently deactivates for that invocation, the same outcome as an absent sentinel.
# Empty REPO_ROOT returns 1 (no-enforce): the machine sentinel must never fire when
# the session is outside a git repo.
_lib_worktree_enforcement_active() {
  local repo_root="$1"
  [ -n "$repo_root" ] || return 1                                     # degenerate: no repo, never enforce
  [ -f "$repo_root/.claude/worktree-required" ] && return 0           # committed requirement (opt-out has no effect)
  # Machine default, minus per-repo opt-out.
  # The [ -n "$home_norm" ] guard is load-bearing: an empty/unset $HOME would make
  # the test below probe /.claude/worktree-required and a stray root file could
  # force-enforce every repo. Mirrors require-worktree-for-file-writes.sh lines 73-74.
  local home_norm="${HOME%/}"
  [ -n "$home_norm" ] \
    && [ -f "$home_norm/.claude/worktree-required" ] \
    && [ ! -f "$repo_root/.claude/worktree-optout" ] \
    && return 0
  return 1
}

# _lib_stray_marker_hint REPO_ROOT
# Returns a hint string when the repo-root sentinel exists in the working
# tree but is not tracked in the git index — a stray copy still enforces
# per _lib_worktree_enforcement_active's `[ -f ]` check, and this surfaces
# why: the deny messages that consume this hint already state the
# tracked/committed case, so an untracked marker producing the same deny
# reads as unexplained. Returns empty when the marker is absent, or present
# and tracked (the normal opted-in case) — no noise on the common path.
# Deny-path only: callers interpolate this into an already-decided deny
# message, so the `git ls-files` call here never runs on the allow path.
# 5s timeout backstop mirrors _lib_jq (line 14): a stalled `git ls-files`
# (e.g. NFS-mounted repo root) would otherwise hold the deny message open
# indefinitely. Falls back to bare git when timeout(1) is absent (BSD/macOS).
_lib_stray_marker_hint() {
  local repo_root="$1"
  [ -f "$repo_root/.claude/worktree-required" ] || return 0
  if command -v timeout >/dev/null 2>&1; then
    timeout 5 git -C "$repo_root" ls-files --error-unmatch .claude/worktree-required \
      >/dev/null 2>&1 && return 0
  else
    git -C "$repo_root" ls-files --error-unmatch .claude/worktree-required \
      >/dev/null 2>&1 && return 0
  fi
  printf '%s' " Note: .claude/worktree-required is present but untracked — an accidental stray copy activates enforcement exactly like a committed one. Commit it if intentional, or remove it if it was created by accident."
}

# Single source of truth for read-only git subcommands. Sourced by
# require-worktree-for-git-writes.sh. Closed enumeration — this is a
# security surface, so new entries are added deliberately (a subcommand
# proven read-only against git's own docs), not accreted via "etc./like".
_LIB_READONLY_GIT_SUBCMDS=(
  blame
  branch           # "git branch" lists; creating/deleting takes flags
  cat-file
  check-attr       # read-only attribute lookup
  check-ignore     # read-only gitignore query
  check-mailmap    # read-only mailmap lookup
  check-ref-format # read-only ref name validation
  cherry           # comparison of commits; distinct from the still-denied cherry-pick
  count-objects
  describe
  diff
  diff-files       # read-only plumbing diff
  diff-index       # read-only plumbing diff
  diff-tree        # read-only plumbing diff
  fetch            # updates remote-tracking refs only, not working tree
  for-each-ref
  fsck
  grep             # read-only content search
  help
  log
  ls-files
  ls-remote
  ls-tree
  merge-base
  name-rev
  range-diff
  reflog
  remote
  rev-list
  rev-parse
  shortlog
  show
  show-branch
  show-ref
  status
  symbolic-ref     # "symbolic-ref HEAD <ref>" repoints HEAD but touches neither the
                   # working tree nor the index — acceptable risk under this hook's
                   # working-tree-race threat model, same tier as branch/tag below
  tag              # "git tag" lists; creating takes flags — acceptable risk
  var              # read-only git variable lookup
  verify-commit
  verify-tag
  version
  whatchanged
  worktree         # bootstrap for the whole mechanism — don't block it
)
_lib_readonly_git_subcmds() {
  printf '%s\n' "${_LIB_READONLY_GIT_SUBCMDS[@]}"
}
