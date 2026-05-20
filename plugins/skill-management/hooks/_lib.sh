#!/bin/bash
# Shared helper library sourced by require-*.sh hooks and scripts/marker.sh.
# Keep this file the single source of truth for any recipe that must produce
# byte-identical output on both the read side (hooks) and the write side
# (marker.sh). Source it; do not invoke it directly.

# Compute the marker repo-hash for an absolute repo-toplevel path.
# Input must have no trailing newline -- printf '%s' omits one, so the SHA
# covers exactly the bytes of $1.
# Usage: hash=$(_marker_lib_repo_hash "$REPO_ROOT")
_marker_lib_repo_hash() {
  printf '%s' "$1" | sha256sum | awk '{print $1}'
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
# `git commit` fragment. PreToolUse hooks fire once per Bash tool call before
# the chain runs, so an on-disk marker check denies naturally-typed forms like
# `marker.sh write code-review && git commit`. When the same Bash call will
# write the marker before invoking commit, the in-chain marker.sh invocation
# is the same evidence the on-disk marker would later provide -- marker.sh is
# the only sanctioned writer in either case.
#
# Usage: _lib_chains_marker_write_before_commit "$COMMAND" code-review
# Returns 0 (true) if a marker-write fragment precedes a git-commit fragment.
_lib_chains_marker_write_before_commit() {
  local command="$1" skill="$2"
  local fragment seen_marker=1
  # The marker-write detection regex pins the path to canonical sanctioned
  # forms (tilde-anchored ~/.claude/scripts/marker.sh or absolute path ending
  # in /.claude/scripts/marker.sh). Without that pin, an attacker who chained
  # `git add . && /home/evil/marker.sh write code-review && git commit` would
  # bypass the gate: enforce-marker-script-shape's Stage 2 anchor only fires
  # when the command STARTS with marker.sh, so a non-leading bogus path slips
  # past it. Pinning here closes that gap defense-in-depth.
  #
  # Here-string (<<<) appends a trailing newline so `read` consumes the final
  # fragment too — process substitution (< <(...)) would drop it. Matches the
  # pattern in deny-pii-in-commits.sh and deny-private-project-refs.sh.
  while IFS= read -r fragment; do
    if _lib_fragment_invokes_git "$fragment" \
        && [ "$(_lib_extract_git_subcmd "$fragment")" = "commit" ]; then
      return "$seen_marker"
    fi
    if printf '%s' "$fragment" \
        | grep -qE "(^|[[:space:]])(~|/[A-Za-z0-9_./-]+)/\.claude/scripts/marker\.sh[[:space:]]+write[[:space:]]+${skill}([[:space:]]|$)"; then
      seen_marker=0
    fi
  done <<< "$(_lib_split_fragments "$command")"
  return 1
}

# Single source of truth for read-only git subcommands. Sourced by
# require-worktree-for-git-writes.sh and check-runner-bash-guard.sh.
# Edit this list; both consumers transitively pick up the change.
_LIB_READONLY_GIT_SUBCMDS=(
  blame
  branch           # "git branch" lists; creating/deleting takes flags
  cat-file
  check-attr       # read-only attribute lookup
  check-ignore     # read-only gitignore query
  check-mailmap    # read-only mailmap lookup
  check-ref-format # read-only ref name validation
  count-objects
  describe
  diff
  fetch            # updates remote-tracking refs only, not working tree
  for-each-ref
  fsck
  help
  log
  ls-files
  ls-remote
  ls-tree
  name-rev
  reflog
  remote
  rev-list
  rev-parse
  shortlog
  show
  status
  tag              # "git tag" lists; creating takes flags — acceptable risk
  var              # read-only git variable lookup
  verify-commit
  verify-tag
  version
  worktree         # bootstrap for the whole mechanism — don't block it
)
_lib_readonly_git_subcmds() {
  printf '%s\n' "${_LIB_READONLY_GIT_SUBCMDS[@]}"
}
