#!/usr/bin/env bash
# ci-watch.sh — launch a background CI-status watch for one PR and report a
# single machine-parseable terminal result line.
#
# Invoked by ready-for-review/SKILL.md's "CI watch (out-of-band)" section via
# `Bash` `run_in_background`, once the PR number is known. Never run in the
# foreground for a real PR: `gh pr checks --watch` blocks until every check
# reaches a terminal state, which can take hours.
#
# Output contract (stdout):
#   LAUNCH_SHA: <oid>          -- printed once, at start, before the watch
#   CI_RESULT: none            -- zero checks were ever configured on this PR
#   CI_RESULT: error <reason>  -- couldn't determine CI status
#   CI_RESULT: checks <json>   -- structured snapshot; `bucket` per check is
#                                  the pass/fail source of truth
#
# Two gh pr checks calls are required: --watch and --json cannot combine, and
# --watch's exit code doesn't distinguish failure/zero-checks/transient-error
# (cli/cli's pkg/cmd/pr/checks/checks.go).
#
# Input (environment):
#   CI_CHECKS_GH_TOKEN  -- optional; used only for the two `gh pr checks`
#                           calls, since fine-grained PATs 403 on the Checks
#                           API. Resolved via direnv and withheld on a
#                           cross-host mismatch — see docs/scripts.md's
#                           CI_CHECKS_GH_TOKEN entry for the full mechanism.
#   GH_HOST             -- optional; tells `gh` which host to target. Needed
#                           when a GHE host is supplied only via direnv and
#                           was never registered via `gh auth login`, so
#                           `hosts.yml` has no fallback entry. Resolved by
#                           resolve_gh_host after the script's blanket
#                           `unset GH_REPO GH_HOST` near the top clears any
#                           stale ambient value.
#
# Usage: ci-watch.sh <pr-number>

set -euo pipefail

# None of this script's gh calls pass --repo, so gh resolves its target repo
# from $PWD's git remote. GH_REPO/GH_HOST override that cwd-based resolution.
# Unset them so a stale value from a differently-scoped invoking shell
# doesn't leak in. Otherwise the repo gh targets could silently diverge from
# the repo the token was resolved for. GH_HOST is resynced below
# (resolve_gh_host) with this directory's own direnv-scoped value. GH_REPO
# gets no equivalent resync, and stays unset for the rest of the run.
#
# AMBIENT_GH_HOST captures GH_HOST before the unset below, since reading it
# post-unset would always see empty and collapse resolve_gh_host's
# changed-vs-confirmed distinction.
AMBIENT_GH_HOST="${GH_HOST:-}"
unset GH_REPO GH_HOST

# direnv_export_bash is shared with cleanup-merged-branches.sh.
# shellcheck source=_direnv-lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/_direnv-lib.sh"

# fd 3 preserves the script's real stderr, independent of the 2>&1 /
# 2>"$STDERR_FILE" redirects applied around each `gh pr checks` call below —
# without it, gh_with_checks_token's escalation notice would be captured
# into WATCH_OUTPUT or STDERR_FILE instead of reaching the terminal.
exec 3>&2

if [[ "$#" -ne 1 ]] || [[ -z "$1" ]]; then
  echo "Usage: $(basename "$0") <pr-number>" >&2
  echo "CI_RESULT: error missing or invalid pr-number argument"
  exit 2
fi
PR_NUMBER="$1"
if [[ ! "$PR_NUMBER" =~ ^[0-9]+$ ]]; then
  echo "Usage: $(basename "$0") <pr-number>" >&2
  echo "CI_RESULT: error pr-number must be numeric, got: ${PR_NUMBER}"
  exit 2
fi

if ! command -v gh &>/dev/null; then
  echo "CI_RESULT: error gh not installed"
  exit 1
fi

# Set by resolve_ci_checks_gh_token below when direnv names this variable
# for this directory, independent of whether the value changed. Consumed
# by the cross-host mismatch gate below.
CI_CHECKS_GH_TOKEN_RESOLVED_VIA_DIRENV=0

# Set by resolve_ci_checks_gh_token below to 1 only when its own value_probe
# command substitution succeeds. Read by the cross-host mismatch gate below,
# since a failed value_probe can leave CI_CHECKS_GH_TOKEN_RESOLVED_VIA_DIRENV
# latched to 1 (from a separately-succeeding named_probe) while
# CI_CHECKS_GH_TOKEN itself never got direnv's actual answer.
CI_CHECKS_GH_TOKEN_VALUE_PROBE_OK=0

# direnv_probe VAR_NAME MODE
#   MODE=ambient      -- eval direnv's payload against the ambient environment
#   MODE=unset-first  -- unset VAR_NAME first, then eval
# Prints "1<value>" to stdout when the payload leaves VAR_NAME set, "0" when
# not. Runs in a subshell so direnv's arbitrary export payload (other
# variables, secret values) never reaches the caller's environment — only
# VAR_NAME's own presence and value escape, via stdout.
# Callers must assign the result separately (`x=$(direnv_probe ...)`), never
# via `local x=$(direnv_probe ...)` — `local`'s own always-zero exit status
# would mask the command substitution's.
direnv_probe() {
  local name="$1" mode="$2"
  (
    if [[ "$mode" == unset-first ]]; then
      unset "$name"
    fi
    eval "$(direnv_export_bash)"
    status=$?
    [[ $status -eq 0 ]] || exit "$status"
    if [[ -n "${!name+x}" ]]; then
      printf '1%s' "${!name}"
    else
      printf '0'
    fi
  )
}

# resolve_ci_checks_gh_token — resyncs CI_CHECKS_GH_TOKEN via direnv for
# this directory before gh_with_checks_token's first call. See
# docs/scripts.md's CI_CHECKS_GH_TOKEN entry for why and how.
#
# Runs two separate direnv probes, not one. "What value should
# CI_CHECKS_GH_TOKEN have here" and "did direnv name this variable for
# this directory at all" need different observed environments to answer
# correctly — see each probe's own comment below for which question it
# answers and why a shared probe can't answer both.
resolve_ci_checks_gh_token() {
  command -v direnv >/dev/null 2>&1 || return 0
  local ambient="${CI_CHECKS_GH_TOKEN:-}" value_probe named_probe

  # value_probe answers "what value should CI_CHECKS_GH_TOKEN have here".
  # ambient mode evaluates the payload against the ambient environment, not
  # a pre-unset one, so direnv's own `unset CI_CHECKS_GH_TOKEN` takes
  # effect. direnv emits that unset when a prior directory's .envrc
  # exported the variable and this directory's .envrc does not.
  if value_probe=$(direnv_probe CI_CHECKS_GH_TOKEN ambient); then
    CI_CHECKS_GH_TOKEN_VALUE_PROBE_OK=1
    if [[ "${value_probe:0:1}" == 1 ]]; then
      CI_CHECKS_GH_TOKEN="${value_probe:1}"
      if [[ "$CI_CHECKS_GH_TOKEN" != "$ambient" ]]; then
        echo "ci-watch: CI_CHECKS_GH_TOKEN resolved via direnv for $(pwd)" >&3
      fi
    else
      unset CI_CHECKS_GH_TOKEN
      if [[ -n "$ambient" ]]; then
        echo "ci-watch: CI_CHECKS_GH_TOKEN cleared by direnv for $(pwd)" >&3
      fi
    fi
  fi

  # named_probe answers a different question: "did direnv name this
  # variable for this directory at all". unset-first mode forces direnv to
  # re-state a value it would otherwise silently reconfirm, so a
  # value-equality check can't substitute for it.
  if named_probe=$(direnv_probe CI_CHECKS_GH_TOKEN unset-first) \
      && [[ "${named_probe:0:1}" == 1 ]]; then
    CI_CHECKS_GH_TOKEN_RESOLVED_VIA_DIRENV=1
  fi
  return 0
}
resolve_ci_checks_gh_token

# resolve_gh_host — resyncs GH_HOST via direnv, undoing the script's blanket
# `unset GH_REPO GH_HOST` near the top with this directory's own value
# before any gh call. Exported when non-empty (gh reads GH_HOST from the
# environment, gh v2.97.0) — see docs/scripts.md's GH_HOST entry for why.
resolve_gh_host() {
  command -v direnv >/dev/null 2>&1 || return 0
  # probe is assigned separately below (not `local probe=$(...)`), so its
  # exit status isn't masked by `local`'s own always-zero status. This
  # resolver needs no separate ambient-mode probe like
  # resolve_ci_checks_gh_token's: the script's top-level `unset GH_REPO
  # GH_HOST` already puts GH_HOST in the state the unset-first probe would
  # otherwise have to construct itself.
  local ambient="$AMBIENT_GH_HOST" probe
  if probe=$(direnv_probe GH_HOST unset-first); then
    if [[ "${probe:0:1}" == 1 ]]; then
      local resolved="${probe:1}"
      if [[ -n "$resolved" ]]; then
        export GH_HOST="$resolved"
      fi
      if [[ "$resolved" != "$ambient" ]]; then
        echo "ci-watch: GH_HOST resolved via direnv for $(pwd)" >&3
      fi
    fi
  fi
  return 0
}
resolve_gh_host

# candidate_gh_hosts — print, one per line, the host(s) `gh` is actually
# about to target: GH_HOST when non-empty (gh help environment, gh
# v2.97.0), else every host parsed from this repo's git remote URLs (gh's
# own cwd-based fallback resolution). Never fails: a non-repo cwd or a
# repo with no remotes yields no output.
candidate_gh_hosts() {
  if [[ -n "${GH_HOST:-}" ]]; then
    printf '%s\n' "$GH_HOST"
    return 0
  fi
  local line url host
  git config --get-regexp '^remote\..*\.url$' 2>/dev/null | while IFS= read -r line; do
    url="${line#* }"
    # URL form: scheme://[user@]host[:port]/path (e.g. https://, ssh://).
    if [[ "$url" =~ ^[A-Za-z][A-Za-z0-9+.-]*://([^/@]*@)?([^/:]+) ]]; then
      host="${BASH_REMATCH[2]}"
    # scp-like form: user@host:path (e.g. git@host.ghe.com:owner/repo.git).
    elif [[ "$url" =~ ^[^/@[:space:]]+@([^:/[:space:]]+): ]]; then
      host="${BASH_REMATCH[1]}"
    else
      continue
    fi
    printf '%s\n' "$host"
  done || true
  return 0
}

# Withholds CI_CHECKS_GH_TOKEN when it did not resolve via direnv but a
# *.ghe.com candidate host (candidate_gh_hosts above) is among the hosts
# gh is actually about to target, since the stale token may otherwise
# reach that host as GH_TOKEN. See docs/scripts.md's GH_HOST entry for the
# full rationale.
# A failed value_probe counts the same as "did not resolve via direnv" —
# see CI_CHECKS_GH_TOKEN_VALUE_PROBE_OK's own declaration comment above.
# nocasematch scopes the glob match case-insensitively, since [[ ]] glob
# matching is case-sensitive by default.
CI_CHECKS_GH_TOKEN_WITHHELD=0
if { [[ "$CI_CHECKS_GH_TOKEN_RESOLVED_VIA_DIRENV" -ne 1 ]] || [[ "$CI_CHECKS_GH_TOKEN_VALUE_PROBE_OK" -ne 1 ]]; } \
    && [[ -n "${CI_CHECKS_GH_TOKEN:-}" ]]; then
  shopt -s nocasematch
  while IFS= read -r candidate_host; do
    if [[ "$candidate_host" == *.ghe.com ]]; then
      echo "ci-watch: withholding CI_CHECKS_GH_TOKEN for $(pwd) — the destination host (${candidate_host}) is a *.ghe.com candidate but CI_CHECKS_GH_TOKEN's resolution did not follow it, so a stale token is not being sent to it; see docs/scripts.md" >&3
      unset CI_CHECKS_GH_TOKEN
      CI_CHECKS_GH_TOKEN_WITHHELD=1
      break
    fi
  done < <(candidate_gh_hosts)
  shopt -u nocasematch
fi

# GH_TOKEN alone covers both github.com and *.ghe.com subdomains (gh help
# environment, gh v2.97.0).
gh_with_checks_token() {
  if [[ -n "${CI_CHECKS_GH_TOKEN:-}" ]]; then
    echo "ci-watch: using CI_CHECKS_GH_TOKEN override for Checks API" >&3
    GH_TOKEN="$CI_CHECKS_GH_TOKEN" gh "$@" 3>&-
  else
    # Unresynced ambient GH_TOKEN reaches gh here — docs/scripts.md's
    # GH_HOST entry, "Known residual gap" bullet.
    gh "$@" 3>&-
  fi
}

STDERR_FILE=$(mktemp -t ci-watch-stderr.XXXXXX) || {  # GNU mktemp requires the XXXXXX suffix; a bare prefix is BSD-only.
  echo "CI_RESULT: error could not create temp file for stderr capture"
  exit 1
}
trap 'rm -f "$STDERR_FILE"' EXIT

# Recorded before the watch starts so a mid-watch push is detectable as
# "superseded," not silently diagnosed against.
# Unresynced ambient GH_TOKEN reaches gh here — docs/scripts.md's GH_HOST
# entry, "Known residual gap" bullet.
if ! LAUNCH_SHA=$(gh pr view "$PR_NUMBER" --json headRefOid --jq .headRefOid 2>"$STDERR_FILE" 3>&-); then
  REASON=$(tr '\n' ' ' < "$STDERR_FILE")
  echo "CI_RESULT: error could not resolve PR #${PR_NUMBER}'s head SHA: ${REASON:-unknown gh failure}"
  exit 1
fi
echo "LAUNCH_SHA: ${LAUNCH_SHA}"

# Parses only the literal zero-checks text — --watch's exit code/output
# (2>&1, since gh writes it to stderr) can't otherwise distinguish a
# transient failure from a late-registering "pending" check.
WATCH_OUTPUT=$(gh_with_checks_token pr checks "$PR_NUMBER" --watch 2>&1) || true

if printf '%s' "$WATCH_OUTPUT" | grep -q 'no checks reported'; then
  echo "CI_RESULT: none"
  exit 0
fi

if ! SNAPSHOT_JSON=$(gh_with_checks_token pr checks "$PR_NUMBER" \
      --json name,bucket,description,link,workflow 2>"$STDERR_FILE"); then
  REASON=$(tr '\n' ' ' < "$STDERR_FILE")
  HINT=""
  if [[ "$CI_CHECKS_GH_TOKEN_WITHHELD" -eq 1 ]]; then
    HINT=" (if this is a 403: CI_CHECKS_GH_TOKEN was withheld due to a cross-host mismatch — see docs/scripts.md)"
  elif [[ -z "${CI_CHECKS_GH_TOKEN:-}" ]]; then
    HINT=" (if this is a 403: fine-grained PATs cannot reach the Checks API at all, and no usable CI_CHECKS_GH_TOKEN was found in this process's ambient environment or via direnv's resolution for the current directory; see docs/scripts.md for how to provision one)"
  fi
  echo "CI_RESULT: error gh pr checks --json failed after --watch resolved: ${REASON:-unknown gh failure}${HINT}"
  exit 1
fi

echo "CI_RESULT: checks ${SNAPSHOT_JSON}"
exit 0
