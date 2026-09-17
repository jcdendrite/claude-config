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
#                           API. Resolved automatically via direnv for the
#                           current directory before first use (see
#                           resolve_ci_checks_gh_token below). An ambient
#                           value survives untouched when direnv is absent
#                           or its resolution fails. See docs/scripts.md for
#                           provisioning guidance.
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

# Set by resolve_ci_checks_gh_token/resolve_gh_host below when direnv's
# export payload actually names that variable for this directory. This is
# independent of whether the resolved value differs from that variable's
# own pre-unset ambient value. Read after both resolvers run to warn when
# GH_HOST resolved via direnv but CI_CHECKS_GH_TOKEN did not — see
# docs/scripts.md's GH_HOST entry.
CI_CHECKS_GH_TOKEN_RESOLVED_VIA_DIRENV=0
GH_HOST_RESOLVED_VIA_DIRENV=0

# resolve_ci_checks_gh_token — resyncs CI_CHECKS_GH_TOKEN via direnv for
# this directory before gh_with_checks_token's first call. See
# docs/scripts.md's CI_CHECKS_GH_TOKEN entry for why and how.
#
# The notice fires only when direnv's answer actually differs from the
# ambient value (set, cleared, or changed). It does not fire on every run
# where direnv happens to be installed but this directory's .envrc has
# nothing to say about CI_CHECKS_GH_TOKEN, which would otherwise spam this
# line on every invocation on any contributor machine with direnv present.
# CI_CHECKS_GH_TOKEN_RESOLVED_VIA_DIRENV is set independently of that
# comparison, from whether direnv's export payload names
# CI_CHECKS_GH_TOKEN at all. A value-equality comparison would miss an
# ambient value direnv merely reconfirms. It would also wrongly report a
# direnv-resolved value that happens to already match ambient as unresolved.
resolve_ci_checks_gh_token() {
  command -v direnv >/dev/null 2>&1 || return 0
  # probe is assigned separately below (not `local probe=$(...)`), so its
  # exit status isn't masked by `local`'s own always-zero status. The
  # subshell unsets CI_CHECKS_GH_TOKEN first so direnv's diff-against-
  # observed-environment semantics are forced to re-state it whenever this
  # directory's .envrc sets it. direnv emits nothing for a variable that's
  # already ambient-correct, so a plain re-eval without this unset would
  # silently miss that case. Whether direnv named the variable at all is
  # then read via `${VAR+x}`, not a text/regex parse of the export payload,
  # since a payload line can't be parsed without picking a delimiter that
  # might collide with a value containing `;` or `=`.
  local ambient="${CI_CHECKS_GH_TOKEN:-}" probe
  if probe=$(
        unset CI_CHECKS_GH_TOKEN
        eval "$(direnv_export_bash)"
        status=$?
        [[ $status -eq 0 ]] || exit "$status"
        if [[ -n "${CI_CHECKS_GH_TOKEN+x}" ]]; then
          printf '1%s' "$CI_CHECKS_GH_TOKEN"
        else
          printf '0'
        fi
      ); then
    if [[ "${probe:0:1}" == 1 ]]; then
      CI_CHECKS_GH_TOKEN_RESOLVED_VIA_DIRENV=1
      CI_CHECKS_GH_TOKEN="${probe:1}"
      if [[ "$CI_CHECKS_GH_TOKEN" != "$ambient" ]]; then
        echo "ci-watch: CI_CHECKS_GH_TOKEN resolved via direnv for $(pwd)" >&3
      fi
    fi
  fi
  return 0
}
resolve_ci_checks_gh_token

# resolve_gh_host — resyncs GH_HOST via direnv, undoing the script's blanket
# `unset GH_REPO GH_HOST` near the top with this directory's own value
# before any gh call. Exported when non-empty (gh reads GH_HOST from the
# environment, gh v2.97.0) — see docs/scripts.md's GH_HOST entry for why.
# GH_HOST_RESOLVED_VIA_DIRENV is set independently of the notice's
# value-equality comparison below, from whether direnv's export payload
# names GH_HOST at all. See resolve_ci_checks_gh_token's own comment for
# why value-equality is the wrong signal for that flag.
resolve_gh_host() {
  command -v direnv >/dev/null 2>&1 || return 0
  # probe is assigned separately below (not `local probe=$(...)`), so its
  # exit status isn't masked by `local`'s own always-zero status. The
  # subshell's own `unset GH_HOST` is defensive redundancy, not load-bearing
  # today. The script's top-level `unset GH_REPO GH_HOST` already guarantees
  # GH_HOST is absent here, since nothing repopulates it in between. See
  # resolve_ci_checks_gh_token's own comment for the unset-then-re-eval shape
  # and its `${VAR+x}` presence test, which this function shares.
  local ambient="$AMBIENT_GH_HOST" probe
  if probe=$(
        unset GH_HOST
        eval "$(direnv_export_bash)"
        status=$?
        [[ $status -eq 0 ]] || exit "$status"
        if [[ -n "${GH_HOST+x}" ]]; then
          printf '1%s' "$GH_HOST"
        else
          printf '0'
        fi
      ); then
    if [[ "${probe:0:1}" == 1 ]]; then
      GH_HOST_RESOLVED_VIA_DIRENV=1
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

# Warns when GH_HOST resolved via direnv but CI_CHECKS_GH_TOKEN stayed at
# its ambient value, which may now be sent as GH_TOKEN to a different,
# newly-reachable host. Scoped to *.ghe.com hosts — see docs/scripts.md's
# GH_HOST entry for why.
if [[ "$GH_HOST_RESOLVED_VIA_DIRENV" -eq 1 ]] \
    && [[ "$CI_CHECKS_GH_TOKEN_RESOLVED_VIA_DIRENV" -ne 1 ]] \
    && [[ -n "${CI_CHECKS_GH_TOKEN:-}" ]] \
    && [[ "${GH_HOST:-}" == *.ghe.com ]]; then
  echo "ci-watch: GH_HOST resolved via direnv for $(pwd) but CI_CHECKS_GH_TOKEN did not — a stale token may reach the newly-resolved host" >&3
fi

# GH_TOKEN alone covers both github.com and *.ghe.com subdomains (gh help
# environment, gh v2.97.0).
gh_with_checks_token() {
  if [[ -n "${CI_CHECKS_GH_TOKEN:-}" ]]; then
    echo "ci-watch: using CI_CHECKS_GH_TOKEN override for Checks API" >&3
    GH_TOKEN="$CI_CHECKS_GH_TOKEN" gh "$@" 3>&-
  else
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
  if [[ -z "${CI_CHECKS_GH_TOKEN:-}" ]]; then
    HINT=" (if this is a 403: fine-grained PATs cannot reach the Checks API at all, and no usable CI_CHECKS_GH_TOKEN was found in this process's ambient environment or via direnv's resolution for the current directory; see docs/scripts.md for how to provision one)"
  fi
  echo "CI_RESULT: error gh pr checks --json failed after --watch resolved: ${REASON:-unknown gh failure}${HINT}"
  exit 1
fi

echo "CI_RESULT: checks ${SNAPSHOT_JSON}"
exit 0
