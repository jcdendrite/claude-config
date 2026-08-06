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

# Portable `realpath -m TARGET`: normalizes a path without requiring TARGET (a Write's not-yet-existing destination) or any ancestor to exist. BSD/macOS realpath has no -m; falls back to grealpath, then to resolving the nearest existing ancestor and reattaching the unresolved suffix.
# Each external realpath/grealpath call below is wrapped individually in _lib_capped -- `timeout` can't wrap a shell function directly.
_lib_realpath_m() {
  local target="$1"
  local resolved
  if resolved=$(_lib_capped realpath -m -- "$target" 2>/dev/null) && [ -n "$resolved" ]; then
    printf '%s\n' "$resolved"
    return 0
  fi
  if command -v grealpath >/dev/null 2>&1 \
    && resolved=$(_lib_capped grealpath -m -- "$target" 2>/dev/null) && [ -n "$resolved" ]; then
    printf '%s\n' "$resolved"
    return 0
  fi
  local suffix="" current="$target" suffix_component
  while true; do
    if [ -e "$current" ]; then
      resolved=$(_lib_capped realpath -- "$current" 2>/dev/null) || return 1
      if [ -z "$suffix" ]; then
        printf '%s\n' "$resolved"
      elif [ "$resolved" = "/" ]; then
        printf '/%s\n' "$suffix"
      else
        printf '%s/%s\n' "$resolved" "$suffix"
      fi
      return 0
    fi
    if [ "$current" = "/" ] || [ "$current" = "." ]; then
      return 1
    fi
    suffix_component=$(basename -- "$current")
    case "$suffix_component" in
      ..)
        return 1  # a `..` here could defeat a caller's same-prefix boundary check, so fail closed instead of normalizing it.
        ;;
    esac
    if [ -z "$suffix" ]; then
      suffix="$suffix_component"
    else
      suffix="$suffix_component/$suffix"
    fi
    current=$(dirname -- "$current")
  done
}

# Prints the active Claude Code config directory: $CLAUDE_CONFIG_DIR if set
# (must be absolute — a relative value resolves differently per invocation
# cwd, the same path-mismatch bug this function exists to fix), else
# $HOME/.claude. Returns 1 with no stdout when CLAUDE_CONFIG_DIR is relative,
# or when CLAUDE_CONFIG_DIR is unset/empty and $HOME is also unset/empty.
# Call-site contract (load-bearing): bare interpolation,
# "$(_lib_config_dir)/whatever", is unsafe — under `set -e`, a failing
# *nested* command substitution does not abort the script, so a resolver
# failure silently collapses to "/whatever" (root-anchored) instead of being
# caught. Every call site must capture and check the exit status first:
#   config_dir=$(_lib_config_dir) || { <fail-open-or-deny per this caller>; }
_lib_config_dir() {
  if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then
    case "$CLAUDE_CONFIG_DIR" in
      /*) ;;
      *) return 1 ;;  # relative values resolve differently per invocation
                      # cwd — the exact read/write path-mismatch bug this
                      # function fixes, just triggered a different way.
    esac
    printf '%s\n' "${CLAUDE_CONFIG_DIR%/}"
    return 0
  fi
  local home_norm="${HOME%/}"
  [ -n "$home_norm" ] || return 1
  printf '%s\n' "$home_norm/.claude"
}

# Canonical jq-encode-or-hard-block body for a gate hook's deny path.
# Deliberately NOT named `emit_deny`: sourcing this file must not silently
# satisfy the "CALLER MUST define emit_deny" contract below on its own, or a
# hook that forgets its own bootstrap `emit_deny` would inherit this one
# silently instead of bash's loud "command not found" —
# test_missing_emit_deny_loud_fail in test_lib.py pins that. Each gate hook
# defines a minimal bootstrap `emit_deny` before sourcing this file (so a
# failed `source` can still deny), then re-points `emit_deny` at this
# function immediately after a successful source:
#   emit_deny() { printf '%s\n' "$1" >&2; exit 2; }
#   if ! . ".../_lib.sh" 2>/dev/null; then emit_deny "..."; fi
#   emit_deny() { _lib_emit_deny "$1"; }
# An accidentally-dropped re-point line degrades UX only (every deny in that
# hook falls back to the bootstrap stub's plain exit-2 stderr instead of this
# function's JSON envelope) — it does not weaken the fail-closed guarantee,
# since the bootstrap stub still blocks. _lib_jq's timeout backstop is safe
# to rely on unconditionally here (unlike in the bootstrap stub) because this
# function only ever runs after _lib.sh
# has fully sourced.
_lib_emit_deny() {
  local reason="$1"
  local reason_json
  reason_json=$(printf '%s' "$reason" | _lib_jq -Rs . 2>/dev/null)
  if [ -z "$reason_json" ]; then
    # jq is absent, failed, or was killed by the timeout backstop. Exit 2 is
    # the harness's blocking path for PreToolUse and carries the reason on
    # stderr, so it needs no JSON encoding. Emitting a half-built payload on
    # exit 0 instead would parse as no-decision and let the tool run.
    #
    # The fixed prefix is load-bearing: every gate parses its input with jq
    # before any command filtering, so a missing jq denies every tool call
    # with the parse-failure reason below — which names the wrong cause.
    # Without this line the session has no in-agent route to a fix.
    printf 'Hook gate could not encode its deny reason: jq is missing from PATH, failed, or timed out. Every gate hook blocks until this is fixed — this is deliberate, not a bug. In an interactive session, install jq (and GNU coreutils timeout) using the ! shell escape, which runs outside the tool-call path these hooks gate; in a headless or non-interactive run, ensure jq is installed in the execution environment beforehand. Underlying gate reason follows.\n%s\n' \
      "$reason" >&2
    exit 2
  fi
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' \
    "$reason_json"
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
# is enforced by test_hook_alignment.py. See _lib_emit_deny above for the
# post-source re-pointing pattern hooks use to pick up the shared body.
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
# This binds PATH identity, not REPOSITORY identity: it digests the toplevel
# path string, not the initial commit, remote URL, or .git inode. A worktree
# removed and later replaced by an unrelated repository at the same absolute
# path inherits the old path's markers. Harmless today (a stale marker still
# has to match the content hash to authorize anything), but do not read this
# hash as proof that two markers came from the same repository.
# Usage: hash=$(_marker_lib_repo_hash "$REPO_ROOT")
_marker_lib_repo_hash() {
  printf '%s' "$1" | sha256sum | awk '{print $1}'
}

# _lib_marker_value_present MARKERS_DIR EXPECTED_VALUE GLOB_PREFIX...
# Returns 0 (true) iff some file in MARKERS_DIR whose name begins with one of
# the supplied prefixes holds EXPECTED_VALUE as a whole line.
#
# Completion markers are content-addressed: the stored value is a hash of
# exactly the state that was reviewed. That content is the authorization, so
# the read asks "has this state been reviewed?" rather than "did this filename
# review it?" — the filename keys only namespace concurrent writers apart.
#
# One `grep` process regardless of marker count. Marker directories are
# unbounded and already hold thousands of entries, so a per-file `cat` loop
# would put a fork count proportional to review history on a hook that fires
# on every Write/Edit.
#
# `-x` (whole-line) is load-bearing, not stylistic: with a bare `-F` a stored
# value that merely CONTAINS the expected hash — a truncated write, or a
# longer digest sharing this one as a prefix — would falsely release the gate.
#
# `nullglob` is equally load-bearing. Bash's default expands a zero-match
# pattern to the literal, unexpanded pattern string, which grep then tries to
# open as a real path and fails on (exit 2) — and "no marker exists yet for
# this prefix" is the single most common call, so the default behavior would
# misreport the common case as an error rather than as a clean not-found.
# The shopt state is saved and restored so callers that rely on the default
# glob behavior elsewhere are unaffected.
_lib_marker_value_present() {
  local markers_dir="$1" expected_value="$2"
  shift 2
  [ -n "$markers_dir" ] || return 1
  [ -n "$expected_value" ] || return 1
  [ -d "$markers_dir" ] || return 1
  [ "$#" -gt 0 ] || return 1

  local nullglob_was_set=0
  if shopt -q nullglob; then nullglob_was_set=1; fi
  shopt -s nullglob
  local -a marker_files=()
  local prefix
  for prefix in "$@"; do
    [ -n "$prefix" ] || continue
    marker_files+=("$markers_dir/$prefix"*)
  done
  if [ "$nullglob_was_set" -eq 0 ]; then shopt -u nullglob; fi

  [ "${#marker_files[@]}" -gt 0 ] || return 1
  # -e pins EXPECTED_VALUE as the pattern even if it begins with a dash; the
  # stderr redirect swallows the "Is a directory" noise a stray subdirectory
  # under MARKERS_DIR would otherwise produce.
  #
  # SCALE BOUNDARY: the matched files become one argv, so a single prefix
  # holding roughly 13k-30k markers (host-dependent; ARG_MAX bounded by the
  # stack rlimit) makes grep fail to exec with E2BIG. That is a nonzero exit,
  # which every call site reads as "no matching marker" — so it fails CLOSED,
  # denying rather than releasing. Completion markers are never pruned today
  # (marker.sh clear-stale only evicts active-bypass markers), so the ceiling
  # is reachable by unattended growth. Whoever adds marker retention should
  # remove this note; until then a wedged gate at that scale is a deny with no
  # diagnostic, and manual pruning of ~/.claude/*-markers/ is the workaround.
  # A flat glob (not `grep -r`) is deliberate: recursion would let a file
  # nested in a stray subdirectory authorize a gate.
  #
  # Callers must test truthiness, never `[ $? -eq 1 ]`: grep reports a plain
  # no-match as 1 but returns 2 when any argv entry errors — which a stray
  # subdirectory under MARKERS_DIR ("Is a directory") triggers even under -q.
  # Both are correctly false here; an exact-code check would not be.
  grep -qFx -e "$expected_value" -- "${marker_files[@]}" 2>/dev/null
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

# Resolve a fragment's effective command word: skip leading env-var
# assignments and a closed set of runner/wrapper words (plus each runner's
# connector sub-token), then return the first remaining word. `npx prettier`,
# `python -m black`, `sudo env X=1 isort` resolve to prettier/black/isort.
#
# WHY command-word, not any-word (unlike the shared _lib_fragment_invokes_git
# used for git): a tool name like black/sed/mv is a common word that can
# legitimately appear as an argument in a read-only command (`grep black
# file`), so an any-word scan would false-deny it; "git" doesn't have that
# problem.
#
# Shared by deny-reviewer-tree-mutation.sh and deny-repo-relocation.sh.
_lib_fragment_command_word() {
  local fragment="$1"
  local saved_opts=$-
  set -f
  local word cmd="" expect_after_runner=false
  for word in $fragment; do
    # Leading env-var assignment (VAR=val); precedes the command. A flag like
    # --write=false starts with '-' and does not match, so it is never eaten.
    if [[ "$word" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
      continue
    fi
    if $expect_after_runner; then
      # A runner's own connector sub-token or flag (e.g. `poetry run`,
      # `python -m`, `npx --yes`) — skip it; the command is still ahead.
      case "$word" in
        run|exec|dlx|tool|-*) continue ;;
      esac
      expect_after_runner=false
    fi
    # Matched against the basename, not the full path, so an absolute or
    # relative runner path (e.g. /usr/local/bin/pnpm) still resolves like
    # the bare name.
    case "${word##*/}" in
      sudo|doas|env|command|time|nice|xargs|npx|pnpm|yarn|bunx|bun|pipx|uvx|uv|poetry|pipenv|rye|hatch|pdm|python|python2|python3|node|deno)
        expect_after_runner=true
        continue ;;
    esac
    cmd="$word"
    break
  done
  if [[ "$saved_opts" != *f* ]]; then set +f; fi
  printf '%s' "$cmd"
}

# True iff the fragment's command word equals $2, or ends in "/$2" (an
# absolute/relative path invocation, e.g. /usr/bin/terraform).
_lib_fragment_invokes_tool() {
  local fragment="$1" tool="$2"
  local cmd
  cmd=$(_lib_fragment_command_word "$fragment")
  [[ -n "$cmd" && ( "$cmd" == "$tool" || "$cmd" == */"$tool" ) ]]
}

# True iff $2 appears in $1 as a standalone whitespace-delimited token — for
# exact-flag checks (e.g. --fix, --remove-source-files) where a real value
# never appends more non-space characters.
_lib_fragment_has_token() {
  local fragment="$1" token="$2"
  [[ "$fragment" =~ (^|[[:space:]])${token}([[:space:]]|$) ]]
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
  # An unresolvable config dir (empty/unset $HOME, no CLAUDE_CONFIG_DIR) skips
  # the resolved-config-dir arm rather than probing a root-anchored path —
  # same load-bearing guard as before, now expressed via _lib_config_dir.
  # Union, not swap: checks the resolved config dir first, then falls back
  # to the literal $HOME/.claude sentinel — a machine-wide `worktree-required`
  # armed before CLAUDE_CONFIG_DIR adoption must not silently go dark under a
  # differentiated profile, the same enforcement-invariant-regression shape
  # the guard-config hooks (deny-credential-file-reads.sh et al.) fix for
  # their own opt-in configs. Mirrors require-worktree-for-file-writes.sh's
  # exemption union.
  local config_dir
  if config_dir=$(_lib_config_dir) && [ -f "$config_dir/worktree-required" ]; then
    [ ! -f "$repo_root/.claude/worktree-optout" ] && return 0
    return 1
  fi
  [ -f "$HOME/.claude/worktree-required" ] \
    && [ ! -f "$repo_root/.claude/worktree-optout" ] \
    && return 0
  return 1
}

# _lib_autonomous_shipping_active REPO_ROOT
# Returns 0 (true) when this machine has opted into autonomous shipping
# (commit/push/PR without asking) for the given repo.
#
# NOT a generalization of _lib_worktree_enforcement_active above: that
# function's committed-sentinel arm is safe because worktree enforcement
# only restricts a hostile repo, while autonomous shipping removes a human
# checkpoint — so a repo's own committed content must never grant it. There
# is no repo-level "required" file in this code path; committing one has no
# effect. Two tiers only: (1) machine sentinel
# (~/.claude/autonomous-shipping-required), required; (2) per-repo opt-out
# (.claude/autonomous-shipping-optout), narrows the machine default off for
# this repo only. Every error path (filesystem error, empty $HOME, empty
# REPO_ROOT, wrong argument count) fails toward NOT shipping — the safe
# direction for a granting mechanism.
_lib_autonomous_shipping_active() {
  [ "$#" -eq 1 ] || return 1
  local repo_root="$1"
  [ -n "$repo_root" ] || return 1
  # Load-bearing, same reasoning as _lib_worktree_enforcement_active above:
  # an unresolvable config dir would otherwise probe a root-anchored path,
  # and a stray root-owned file there would force-activate autonomous
  # shipping — a materially worse consequence here than for worktree
  # enforcement, since this mechanism removes a human checkpoint rather
  # than adding one.
  local config_dir
  config_dir=$(_lib_config_dir) || return 1
  [ -f "$config_dir/autonomous-shipping-required" ] || return 1
  [ -f "$repo_root/.claude/autonomous-shipping-optout" ] && return 1
  return 0
}

# _lib_permission_prompt_tracking_active
# Returns 0 (true) when this machine has opted into permission-prompt
# tracking (~/.claude/track-permission-prompts exists) — gates
# track-permission-prompts.sh. Same sentinel-file shape as
# _lib_autonomous_shipping_active above, minus the per-repo optout: this
# mechanism only appends to a local log and changes no git/PR/tool
# behavior, so there is no per-repo axis to narrow (see
# docs/permission-prompt-tracking.md). Zero-arity by design — unlike
# _lib_autonomous_shipping_active, which takes a repo root to check a
# per-repo optout against, this sentinel is machine-global with nothing
# repo-scoped to look up. Fails toward NOT tracking on an unresolvable
# config dir, the same error direction every other opt-in gate in this
# file takes.
_lib_permission_prompt_tracking_active() {
  local config_dir
  config_dir=$(_lib_config_dir) || return 1
  [ -f "$config_dir/track-permission-prompts" ] || return 1
  return 0
}

# _lib_valid_session_id_component SESSION_ID
# Returns 0 (true) iff SESSION_ID is safe to use as a single filesystem path
# component (e.g. "$STATE_DIR/$SESSION_ID"). Every call site that builds such
# a path takes SESSION_ID straight from the hook payload's `.session_id` with
# no further sanitization, so a value containing ".." or "/" escapes the
# intended directory once concatenated in — turning an `rm -f`, `>`, or
# `touch` against that path into an operation against a caller-chosen path
# instead. Harness session ids are UUIDs, so this conservative allow-list
# (letters, digits, underscore, hyphen) has ample room without ever needing
# '.' or '/'. Empty input is rejected — callers must not fall through to an
# unvalidated empty SESSION_ID.
_lib_valid_session_id_component() {
  local session_id="$1"
  [[ "$session_id" =~ ^[A-Za-z0-9_-]+$ ]]
}

# _lib_active_bypass_marker_live MARKER_DIR_NAME SESSION_ID
# Returns 0 (true) iff $HOME/.claude/MARKER_DIR_NAME/SESSION_ID holds the PID
# of a live process — that is, the skill which writes this marker is running
# right now, in this session. Returns 1 in every other case, and evicts the
# marker as an orphan when it exists but its stored PID is dead or unreadable,
# so a session that died before its cleanup step cannot wedge a gate open.
#
# Session-id validation lives here rather than at each call site, which makes
# "never build a filesystem path out of an unvalidated session id" a property
# of this function instead of something every caller has to remember. An empty
# or path-escaping id returns 1 having touched the filesystem not at all.
#
# This function reports only whether the marker is live; it takes no position
# on the tool call itself. What a 1 means is the caller's to decide, and it
# differs by gate shape: where the marker grants an exception to a standing
# deny, a 1 withholds the exception and the deny stands; where the gate has
# further checks below, a 1 just means those checks decide instead.
#
# Deliberately tree-agnostic, and narrower than it looks. The marker path holds
# a session id and no repo hash, so a live marker releases its gate for every
# repo and worktree the session touches while the owning skill runs — unlike
# the completion markers, which _marker_lib_repo_hash binds to one tree. That
# is the intended reading of "a review is running in THIS process right now".
#
# The weak part is the liveness test rather than the keying. The stored PID is
# the session's, and a session outlives any one skill invocation, so the bypass
# outlasts what it was scoped to in two ways: a skill that halts between its
# activate and deactivate steps leaves the gate released until the process
# exits, and a tree switch inside the window carries the release across. Both
# predate this function's extraction. Bounding the marker's age would cover
# both — require-routing-read.sh already gates a sibling marker that way, and
# session-marker-dashboard.sh already reports these as stale past an hour with
# no gate acting on it. Repo-keying the path would cover only the second.
#
# Usage: if _lib_active_bypass_marker_live ".respond-pr-active.d" "$SESSION_ID"; then exit 0; fi
_lib_active_bypass_marker_live() {
  # Arity guard. Under `set -u` a call that omits an argument aborts the whole
  # hook process at expansion time rather than returning here, and a gate hook
  # that dies before emitting a decision has no defined disposition. Degrade a
  # malformed call to the same withheld-bypass outcome as every other rejection
  # path instead. `[ ]` rather than `(( ))`: a standalone arithmetic command
  # evaluating to zero is itself a non-zero exit status, which `set -e` callers
  # would abort on.
  [ "$#" -eq 2 ] || return 1
  local marker_dir_name="$1" session_id="$2"
  _lib_valid_session_id_component "$session_id" || return 1
  # No empty-$HOME guard here, unlike _lib_worktree_enforcement_active above.
  # That one probes a path whose mere presence force-enables enforcement for
  # every repo on the machine, so an empty $HOME there fails toward more
  # enforcement from a root-writable path. This function's sinks fail the other
  # way: an unresolvable config dir just makes the marker read miss and the
  # bypass is withheld, leaving the gate enforcing.
  local config_dir
  config_dir=$(_lib_config_dir) || return 1
  local marker="$config_dir/$marker_dir_name/$session_id"
  [ -f "$marker" ] || return 1
  local stored_pid
  # `|| true` keeps this line's status irrelevant to a caller's shell options.
  # The pipeline reports failure under `pipefail` when `cat` loses a race with
  # the marker's removal even though `tr` succeeds on empty stdin; a `set -e`
  # caller would then abort mid-gate-check rather than fall through to the
  # eviction below. No caller sets `-e` today — this keeps that from mattering.
  stored_pid=$(cat "$marker" 2>/dev/null | tr -d '[:space:]') || true
  if [[ "$stored_pid" =~ ^[0-9]+$ ]] && kill -0 "$stored_pid" 2>/dev/null; then
    return 0
  fi
  rm -f "$marker" 2>/dev/null
  return 1
}

# _lib_first_live_linked_worktree REPO_ROOT
# Prints the path of the first linked worktree of REPO_ROOT whose directory is
# present on disk and returns 0; prints nothing and returns 1 when there is
# none. `git worktree list` still reports entries whose directory was deleted
# but not pruned, so each candidate is tested rather than trusting the entry
# count. Callers use this to distinguish "session is in the main tree while the
# work belongs in a worktree" from "this repo has no worktree at all" — the
# latter is the ordinary state just before `git worktree add`, and carries no
# wrong-tree risk because there is no second tree to confuse the first with.
# Parses newline-delimited output, so a worktree path containing a literal
# newline (pathological, and unhandled here) would be split across two lines
# and matched incorrectly; git's own docs recommend `-z` for that case.
_lib_first_live_linked_worktree() {
  local repo_root="$1" worktree_path
  [ -n "$repo_root" ] || return 1
  while IFS= read -r worktree_path; do
    [ -n "$worktree_path" ] || continue
    [ "$worktree_path" = "$repo_root" ] && continue
    if [ -d "$worktree_path" ]; then
      printf '%s' "$worktree_path"
      return 0
    fi
  done < <(_lib_capped git -C "$repo_root" worktree list --porcelain 2>/dev/null \
    | awk '/^worktree /{print substr($0, 10)}')
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

# Byte-size threshold above which content is too large to scan cheaply. 5 MB, shared between deny-data-file-reads.sh's Read-target cap and redact-credential-values.sh's tool_response cap.
_LIB_SIZE_THRESHOLD_BYTES=5242880

# _lib_config_lines FILE
# Prints each non-blank, non-comment line of a per-user config file
# (credential-file-guard.md, data-file-read-guard.md, pii-patterns.md,
# credential-value-patterns.md) as "<1-based raw line number>\t<CR-stripped,
# trimmed line>". The line number counts every raw line, including ones this
# function skips, so a caller reporting a parse error can point the user at
# the actual line in their file. Prints nothing (returns 0) when FILE is
# absent or unreadable. Callers apply their own per-line grammar and match
# semantics (substring glob, exact glob, or "<label>: <regex>") to the line
# field -- those differ by design and are not this function's concern.
_lib_config_lines() {
  local file="$1"
  [ -f "$file" ] && [ -r "$file" ] || return 0
  local raw_line line lineno=0
  while IFS= read -r raw_line || [ -n "$raw_line" ]; do
    lineno=$((lineno + 1))
    line=${raw_line%$'\r'}
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [ -z "$line" ] && continue
    case "$line" in '#'*) continue ;; esac
    printf '%s\t%s\n' "$lineno" "$line"
  done < "$file"
}

# Collapses bash's character-removal-based literal-reassembly mechanisms for
# credential/PII pattern matching against raw Bash command text: bash
# executes `cat ~/.ssh/config"_backup"`, `cat ~/id_r\sa`, and
# `cat ~/id_r$''sa` all identically to their unquoted/unescaped form (an
# adjacent quote split, a backslash-escaped character, and an ANSI-C/
# locale-translated quoted segment respectively all have the delimiting
# characters removed, then the remaining literals are joined into one
# word), but a `grep -E` scan of the unexpanded command sees each
# delimiter as a hard break and can miss a credential-path or
# credential-value pattern that only completes once they're removed.
# Deny-credential-bash-reads.sh and deny-pii-in-commits.sh's
# credential-value sub-check must run this over $COMMAND before matching
# against _LIB_CREDENTIAL_PATH_REGEX or _LIB_CREDENTIAL_VALUE_REGEX. Order:
# first drop the leading `$` of a $'...'/$"..." opener so its content
# reassembles the same way a plain "..."/'...' segment does, then remove
# backslash-escapes (bash drops an unquoted backslash and treats the
# following character literally, everywhere -- including inside a
# single-quoted region, where bash itself would NOT remove it; this
# over-strips relative to bash's real single-quote semantics, but only in
# the safe over-matching direction for a deny gate), then remove the
# remaining bare quote characters. Each step only ever joins
# previously-separated literal characters -- never breaks an existing
# contiguous match -- so the whole pipeline stays a safe superset
# transformation for these substring regexes. Does not attempt full
# shell-word tokenization: variable expansion and command substitution
# remain an accepted residual, same as the documented indirection gap (see
# docs/security-hardening.md).
_lib_strip_shell_quotes() {
  printf '%s' "$1" \
    | sed -E -e "s/\\\$'/'/g" -e 's/\$"/"/g' -e 's/\\(.)/\1/g' \
    | tr -d "\"'"
}

# Credential-shaped PATH tokens, sourced by deny-credential-bash-reads.sh and deny-credential-file-reads.sh. POSIX ERE, basename-token match (not path-qualified): matches a bare filename wherever it appears, closing a `cd ~/.ssh && cat id_rsa` bypass.
# Three alternations with different trailing boundaries. Group 1 excludes a following `.` so `id_rsa` doesn't match inside the safe-to-read `id_rsa.pub`, and `.env` doesn't match inside `.env.foo`/`package.env`; `.env`'s own dotted variants beyond the ones enumerated here are deliberately left to deny-env-reads.sh's broader `.env.*` gate. Group 2 (`.netrc`, `.git-credentials`, `credentials.json`, and the three directory-qualified stores) has no known safe dotted-suffix variant, so it allows a following `.` too — closing a `credentials.json.bak`/`.netrc.bak`-style backup-copy bypass group 1's exclusion would otherwise leave open. Group 3 matches `.ssh` (optionally backup/rename-suffixed, e.g. `.ssh.bak`, `.ssh_backup`, `.ssh.old` — the same `.bak`-style continuation group 2 allows) only as a directory/glob reference (`~/.ssh`, `~/.ssh/`, `~/.ssh//`, `~/.ssh/*`, `~/.ssh/.*`), not `.ssh/<filename>`; a named-file reference under `.ssh` (or its backup-suffixed siblings) is instead deny-by-default via `_lib_has_unsafe_ssh_dir_reference` below, since enumerating every unsafe key basename doesn't scale the way enumerating the few safe ones does.
_LIB_CREDENTIAL_PATH_REGEX='(^|[^A-Za-z0-9_.])(id_rsa|id_dsa|id_ecdsa|id_ed25519|\.env|\.env\.local|\.env\.production|\.env\.development|\.env\.staging|\.env\.test)([^A-Za-z0-9_.]|$)|(^|[^A-Za-z0-9_.])(\.netrc|_netrc|\.git-credentials|credentials\.json|\.credentials\.json|\.aws/credentials|\.docker/config\.json|\.kube/config|\.config/gh/hosts\.yml)([^A-Za-z0-9_]|$)|(^|[^A-Za-z0-9_.])\.ssh([._-][A-Za-z0-9_.-]*)?(/+(\*|\.|[^A-Za-z0-9_./]|$)|[^A-Za-z0-9_./]|$)'

# Basenames under a `.ssh`-shaped directory that are safe to read (never
# private-key material): the three conventional non-secret files, and
# anything ending `.pub` (a public key). Consumed only by
# _lib_has_unsafe_ssh_dir_reference below.
_LIB_SSH_SAFE_BASENAME_REGEX='^(authorized_keys|known_hosts|known_hosts\.old|config)$|\.pub$'

# Deny-by-default counterpart to _LIB_CREDENTIAL_PATH_REGEX's .ssh group: extracts every apparent named-file reference under a `.ssh` or `.ssh`-backup-suffixed directory (`~/.ssh/deploy_key`, `~/.ssh.bak/id_rsa`, `~/.ssh/subdir/deploy_key`, ...) from $1, and returns success (0) if ANY extracted leaf basename is not on the safe allowlist above. Mirrors deny-env-reads.sh's allowlist design (deny by default under the directory, allow only documented-safe names) rather than enumerating every unsafe key basename, which doesn't scale — a custom-named key (`deploy_key`, `github_actions_key`) has no fixed shape to enumerate.
# Each candidate is the whole shell-word remainder after `.ssh/` (may itself
# contain further `/`-nesting). A trailing `/` is NOT treated as proof this
# is a directory reference rather than a named file: `tar czf x
# ~/.ssh/deploy_key/` (BSD tar) still archives the file's full content
# despite the trailing slash, so special-casing it as always-safe would
# reopen the exact bypass this function exists to close. The basename is
# checked the same way with or without a trailing slash -- a legitimate
# directory reference (`~/.ssh/sockets/`, a ControlMaster socket dir) is an
# accepted false positive here, same as this hook family's other documented
# over-denial residuals (e.g. the `grep id_rsa` search-pattern residual).
_lib_has_unsafe_ssh_dir_reference() {
  local text="$1" candidate base
  while IFS= read -r candidate; do
    [ -z "$candidate" ] && continue
    # A `..` path segment anywhere in the candidate is unsafe outright,
    # without attempting to resolve it -- this function only ever inspects
    # the trailing string segment as a basename, so `.ssh/deploy_key/../
    # deploy_key.pub` would otherwise read as the safe basename
    # `deploy_key.pub` while the string still names `deploy_key`. Mirrors
    # _lib_realpath_m's own `..`-rejection precedent elsewhere in this file.
    case "/${candidate}/" in
      */../*) return 0 ;;
    esac
    base="${candidate%/}"
    base="${base##*/}"
    if ! printf '%s' "$base" | grep -qEi "$_LIB_SSH_SAFE_BASENAME_REGEX"; then
      return 0
    fi
  done < <(printf '%s' "$text" | grep -oEi '\.ssh([._-][A-Za-z0-9_.-]*)?/[^[:space:]"'"'"']+')
  return 1
}

# Credential-shaped VALUE patterns, sourced by redact-credential-values.sh (jq gsub) and deny-pii-in-commits.sh's credential-value sub-check (grep -E). Must compile under both POSIX ERE and jq's Oniguruma engine, so only dialect-neutral syntax is used.
# Token prefixes (ghp_/gho_/ghu_/ghs_/ghr_ classic and github_pat_ fine-grained) per GitHub's "About authentication to GitHub" docs. The {20,} length floor is NOT vendor-grounded — chosen low enough that a genuine token is never missed, not a verified minimum.
# AKIA (long-term access key) and ASIA (temporary/STS access key) prefixes per AWS's "IAM identifiers" doc (Understanding unique ID prefixes table). The 16-character suffix length is the widely-observed convention for these IDs, not independently vendor-confirmed for this exact length — same non-verified-minimum caveat as the GitHub {20,} floor above.
# The PEM alternative matches only the BEGIN header line: grep -E is line-oriented and can't match across a newline, so a header-only form is what lets deny-pii-in-commits.sh detect a PEM key at commit time at all. See _LIB_PEM_PRIVATE_KEY_BLOCK_REGEX below for the full-block counterpart.
_LIB_CREDENTIAL_VALUE_REGEX='(gh[opsur]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|(AKIA|ASIA)[A-Z0-9]{16}|-----BEGIN[A-Z ]*PRIVATE KEY-----)'

# Redaction-only counterpart to the PEM alternative above: matches the full PEM block (BEGIN through END), not just the header line, since redact-credential-values.sh must strip the actual base64 key body via jq's whole-string gsub, which (unlike grep -E) can match across embedded newlines.
# Body class excludes `-` so a greedy match stops at the first END footer rather than consuming past it; [:space:] (not `.`) lets the match span embedded newlines under Oniguruma without a dot-matches-newline flag.
_LIB_PEM_PRIVATE_KEY_BLOCK_REGEX='-----BEGIN[A-Z ]*PRIVATE KEY-----[A-Za-z0-9+/=[:space:]]*-----END[A-Z ]*PRIVATE KEY-----'

# _lib_redact_credential_shaped_strings JSON
# Replaces every credential-shaped string anywhere in JSON's value tree
# (the PEM block/header, GitHub token prefixes, an AWS access key ID, plus
# any optional additions from credential-value-patterns.md) with
# [REDACTED-CREDENTIAL], regardless of field name -- extracted from
# redact-credential-values.sh's original inline pattern-assembly-and-walk
# so a second caller doesn't duplicate this security-sensitive logic.
# Echoes redacted JSON on success; on any jq resolution/parse failure emits
# nothing and returns non-zero, so a caller's `[ -n "$result" ]` guard
# treats "redaction failed" the same as "nothing to act on" rather than
# silently passing the unredacted input through.
_lib_redact_credential_shaped_strings() {
  local json="$1"

  # Full PEM block first: Oniguruma takes the first alternative that matches at each position, not the longest, so ordering it before the header-only PEM alternative is what makes gsub prefer redacting the whole key body when a complete block is present.
  local credential_value_pattern="${_LIB_PEM_PRIVATE_KEY_BLOCK_REGEX}|${_LIB_CREDENTIAL_VALUE_REGEX}"
  # Optional user additions: ~/.claude/credential-value-patterns.md, one `<label>: <regex>` line per pattern (same grammar as deny-pii-in-commits.sh's pii-patterns.md, minus `exclude:`).
  # Union, not swap: $(_lib_config_dir)'s copy wins if present, else the legacy $HOME/.claude location -- keeps an already-armed CLAUDE_CONFIG_DIR user's guard live.
  # An unresolvable config dir leaves this at the legacy path; this is an opt-in guard, not a gate, so resolver failure must not disable it.
  local credential_value_patterns_file="${HOME}/.claude/credential-value-patterns.md"
  local config_dir
  if config_dir=$(_lib_config_dir) && [ -f "$config_dir/credential-value-patterns.md" ]; then
    credential_value_patterns_file="$config_dir/credential-value-patterns.md"
  fi
  if [ -f "$credential_value_patterns_file" ] && [ -r "$credential_value_patterns_file" ]; then
    local addition_lineno line addition_value
    while IFS=$'\t' read -r addition_lineno line; do
      case "$line" in
        *:*) ;;
        *) continue ;;
      esac

      addition_value="${line#*:}"
      addition_value="${addition_value#"${addition_value%%[![:space:]]*}"}"
      [ -n "$addition_value" ] || continue

      # Skip (don't apply) a pattern that fails to compile under jq's regex engine -- one bad addition would otherwise break the single combined gsub call below for the whole invocation, including the built-in redaction.
      # shellcheck disable=SC2016 # single-quoted on purpose: $pattern is a jq --arg binding, not a shell variable; double-quoting would expand it in the shell before jq sees it.
      if ! _lib_jq -n --arg pattern "$addition_value" '"" | test($pattern)' >/dev/null 2>&1; then
        printf '_lib_redact_credential_shaped_strings: skipping unparseable pattern at %s line %d (jq could not compile it as a regex) — built-in credential redaction is unaffected, but this addition is not being applied.\n' \
          "$credential_value_patterns_file" "$addition_lineno" >&2
        continue
      fi
      credential_value_pattern="${credential_value_pattern}|${addition_value}"
    done < <(_lib_config_lines "$credential_value_patterns_file")
  fi

  local redacted
  # shellcheck disable=SC2016 # single-quoted on purpose: $pattern is a jq --arg binding, not a shell variable; double-quoting would expand it in the shell before jq sees it.
  redacted=$(printf '%s' "$json" | _lib_jq -c --arg pattern "$credential_value_pattern" \
    'walk(if type == "string" then gsub($pattern; "[REDACTED-CREDENTIAL]") else . end)' 2>/dev/null)
  [ -n "$redacted" ] || return 1
  printf '%s' "$redacted"
}

# Six structural-shape detectors for content that can identify a specific
# machine, person, or private project without naming it directly. Sourced by
# deny-private-project-refs.sh's always-on structural scan. POSIX ERE, one
# constant per detector so a match can be reported by label rather than
# collapsed into one alternation.

# An RFC 1918 private-range (10/8, 172.16/12, 192.168/16) or RFC 1122
# §3.2.1.3 loopback (127/8) IPv4 literal; a public IPv4 no longer matches.
# Every octet position is prefixed `0*` to also match zero-padded forms
# (e.g. `010.000.000.001`).
_LIB_IPV4_LITERAL_REGEX='(0*10\.0*(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.0*(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.0*(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])|0*172\.0*(1[6-9]|2[0-9]|3[01])\.0*(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.0*(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])|0*192\.0*168\.0*(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.0*(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])|0*127\.0*(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.0*(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.0*(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9]))'

# A path segment naming the SSH config directory, or a bare/boundary-
# delimited `id_<algorithm>` SSH key filename (the four OpenSSH default key
# algorithms). The config-directory alternative is written as a bracket
# expression (`[.]`), not `\.`, purely to keep this constant's own source
# line from containing the literal substring this same detector matches —
# `\.` and `[.]` are equivalent POSIX ERE for a literal dot, so this changes
# no matching behavior; without it, committing this line would trip the
# detector it defines.
_LIB_SSH_KEY_PATH_REFERENCE_REGEX='([.]ssh/|(^|[^A-Za-z0-9_])id_(rsa|dsa|ecdsa|ed25519)([^A-Za-z0-9_]|$))'

# A /Users/<name> or /home/<name> home-rooted filesystem path.
_LIB_HOME_ROOTED_PATH_REGEX='(/Users/[A-Za-z0-9_.-]+|/home/[A-Za-z0-9_.-]+)'

# A 32+ contiguous hex-char run, or a UUID-shaped (8-4-4-4-12) hex sequence.
_LIB_LONG_HEX_IDENTIFIER_REGEX='([0-9a-fA-F]{32,}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})'

# A hostname ending in a non-public-suffix TLD.
_LIB_INTERNAL_HOSTNAME_REGEX='[A-Za-z0-9.-]+\.(internal|corp|local|lan|intranet|private)([^A-Za-z0-9_-]|$)'

# A `#`-prefixed lowercase-hyphenated Slack-channel shape. Excludes
# all-digit runs so a plain GitHub issue reference (e.g. issue #421) doesn't
# false-positive; a markdown anchor link fragment shares the same shape as a
# real channel name and is deliberately still matched.
_LIB_SLACK_CHANNEL_SHAPE_REGEX='#[a-z0-9_-]*[a-z_-][a-z0-9_-]*'

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

# Single source of truth for review-only agent identities: the eight
# staff-*/ciso-reviewer personas dispatched by /plan-review and /code-review,
# the skill-fidelity-reviewer dispatched by /ready-for-review, plus the harness
# built-ins Explore and Plan. Sourced by deny-reviewer-tree-mutation.sh. Closed
# enumeration, same discipline as _LIB_READONLY_GIT_SUBCMDS above — new entries
# are added deliberately (a persona proven review-only, never dispatched to
# write project files), not accreted via "etc./like".
_LIB_REVIEW_ONLY_AGENTS=(
  ciso-reviewer
  skill-fidelity-reviewer
  staff-analytics-engineer
  staff-backend-engineer
  staff-data-engineer
  staff-frontend-engineer
  staff-platform-engineer
  staff-product-engineer
  staff-sdet
  Explore
  Plan
)
_lib_review_only_agents() {
  printf '%s\n' "${_LIB_REVIEW_ONLY_AGENTS[@]}"
}

# _lib_is_review_only_agent AGENT_TYPE
# Returns 0 (true) iff AGENT_TYPE exactly matches an entry in
# _LIB_REVIEW_ONLY_AGENTS. Empty input (agent_type absent from the
# PreToolUse payload, e.g. the main session) never matches.
_lib_is_review_only_agent() {
  local agent_type="$1"
  [ -n "$agent_type" ] || return 1
  local candidate
  for candidate in "${_LIB_REVIEW_ONLY_AGENTS[@]}"; do
    [ "$agent_type" = "$candidate" ] && return 0
  done
  return 1
}

# Agent identities that may never release a review gate — every review-only
# persona above, plus the code-writer implementer. Sourced by
# enforce-marker-script-shape.sh to deny `marker.sh write` and
# `marker.sh activate` from these callers.
#
# The boundary rests on two different grounds, and conflating them misleads
# whoever adds the next entry:
#   - Every file-backed identity here (code-writer, the staff-* reviewers,
#     ciso-reviewer, skill-fidelity-reviewer) declares no `Skill` tool in its
#     agents/*.md frontmatter, so it cannot invoke a review skill at all.
#     test_agent_roster.py asserts that mechanically, so granting `Skill` to
#     one of them fails a test rather than silently widening what a subagent
#     can release.
#   - The two harness built-ins (Explore, Plan) are understood to carry
#     `Skill` — they ship with the harness, so this repo holds no frontmatter
#     and no registry that could confirm or falsify it. They are listed on
#     mandate (they are dispatched read-only), which holds either way; do not
#     rewrite this as a tool-absence claim on the strength of the harness
#     behaving one way today.
# Either way a marker write from one of these identities is unearned rather
# than merely unusual, which is what the deny turns on.
#
# Derived from _LIB_REVIEW_ONLY_AGENTS rather than re-enumerated, so a persona
# added there is covered here automatically. `general-purpose` and `claude`
# carry the full tool set and can genuinely run a review skill, so they are
# deliberately absent — that is the documented delegation escape hatch.
_LIB_NO_GATE_RELEASE_AGENTS=(
  "${_LIB_REVIEW_ONLY_AGENTS[@]}"
  code-writer
)
_lib_no_gate_release_agents() {
  printf '%s\n' "${_LIB_NO_GATE_RELEASE_AGENTS[@]}"
}

# _lib_is_no_gate_release_agent AGENT_TYPE
# Returns 0 (true) iff AGENT_TYPE exactly matches an entry in
# _LIB_NO_GATE_RELEASE_AGENTS. Empty input (agent_type absent from the
# PreToolUse payload, e.g. the main session) never matches.
_lib_is_no_gate_release_agent() {
  local agent_type="$1"
  [ -n "$agent_type" ] || return 1
  local candidate
  for candidate in "${_LIB_NO_GATE_RELEASE_AGENTS[@]}"; do
    [ "$agent_type" = "$candidate" ] && return 0
  done
  return 1
}
