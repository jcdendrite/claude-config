#!/bin/bash
# hook-class: gate
# tier-threat-model: cooperative
# PreToolUse hook: block git commit when claude/.claude/settings.base.json
# has machine-local or session-scoped keys staged relative to the repo's
# default branch.
# GUARDED_KEYS_JSON below holds the guarded set.
#
# Fail posture: an unsourceable _lib.sh or a tool-input parse failure denies.
# A missing or cap-killed jq fails that parse, so it denies every Bash call.
# The only allow that warns on stderr is a failure of the later changed-keys
# jq call. The other allows on a failure are silent, and Known gaps lists them.
#
# Defense-in-depth: the hook is dispatched on every Bash tool call, and the
# internal commit-shape check and staged-file check below are the sole
# dispatch gate.
# Unlike require-code-review.sh and the two length gates, this hook diffs
# against a named branch (origin/<default>) rather than a novel-content base,
# so it does not consume _lib_gate_diff_base. For why that base's two anchors
# are admitted elsewhere despite being locally forgeable, and their
# residuals, see:
# `docs/design-decisions/rebase-continue-marker-gate-carveout.md` § "The anchor-admissibility test"
# For this hook's own origin/<default> resolution via _lib_default_branch_or_guess, see
# `docs/design-decisions/guard-settings-session-keysshs-default-branch.md`.
#
# Coverage boundary: this gate reads the index as it stands before the Bash
# call runs. Known gaps:
# - An all-flag commit, a commit pathspec, and a chained `git add && git commit`
#   are invisible to it. deny-invisible-commit-content.sh, registered in the
#   same chain, is the backstop.
# - A `cd` embedded in the command is not followed.
# - A commit concluded by `git merge` or `git pull` is never seen. Only CI
#   catches that shape, through test_base_sets_none_of_the_guarded_key_paths
#   in test_guard_settings_session_keys.py.
# - A failed or cap-killed `git rev-parse --is-inside-work-tree` reads as "not
#   in a repo" and allows silently.
# - A failed or cap-killed `git diff --cached --name-only` reads as "not
#   staged" and allows silently.
# - A failed or cap-killed `git show :<path>` leaves the staged content empty,
#   which parses as {}. The commit then allows silently unless the default
#   branch's file carries a guarded key.
# - _lib_command_concludes_commit returning status 2 (could not determine the
#   command's shape) allows silently, the same as status 1 (no match).
#
# Exit codes:
#   0      — allow (no opinion)
#   0+JSON — deny (a guarded key changed in staged settings.base.json)
#   2      — deny with the reason on stderr (_lib.sh unsourceable, or a deny
#            reason jq cannot encode)

set -uo pipefail

# The keys holding one machine's own state, which must never ship as the
# config every stow user receives. A dotted key (e.g. "env.FOO") is guarded
# via path traversal, not a literal top-level match — see guarded_value below.
# The env.* entries below are exact paths:
# - --print-guarded-keys uses them for the dotted-subset cross-check against
#   render-settings.sh's RULE4_DOTTED_PATHS_JSON.
# - A type-mismatch deny uses them to name the changed leaf (e.g.
#   "env env.CLAUDE_CODE_EFFORT_LEVEL") instead of the blunter "env" alone.
# - The CHANGED_KEYS jq body below guards `env` itself as a namespace, not
#   just these paths.
# Defined here, ahead of the direct-invocation mode below, so that mode
# never depends on code that runs later in the script.
GUARDED_KEYS_JSON='[
  "model",
  "effortLevel",
  "skipAutoPermissionPrompt",
  "skipWorkflowUsageWarning",
  "modelSettings",
  "fastMode",
  "theme",
  "tui",
  "agentPushNotifEnabled",
  "env.CLAUDE_CODE_EFFORT_LEVEL",
  "env.ANTHROPIC_MODEL"
]'

# Direct-invocation mode for tests, including the drift check against
# render-settings.sh's dotted-path carry-forward rule: prints GUARDED_KEYS_JSON
# as JSON and exits.
# It runs before _lib.sh is sourced because a CLI call has no stdin JSON and
# _lib_parse_tool_input_or_deny is fail-closed.
if [ "${1:-}" = "--print-guarded-keys" ]; then
  printf '%s\n' "$GUARDED_KEYS_JSON"
  exit 0
fi

# Every git call below is capped via _lib_capped — see _lib.sh for the cap and its fallback behavior.
# On a machine lacking both timeout(1) and gtimeout(1), _lib_capped runs
# these git calls uncapped, so a stalled git (locked index, network mount)
# hangs this gate rather than degrading gracefully.

DENY_GATE_LABEL="settings session-keys"

# Minimal bootstrap so a failed `source` of _lib.sh below can still deny.
# Re-pointed at _lib.sh's _lib_emit_deny immediately after a successful
# source — see _lib_parse_tool_input_or_deny's contract comment in _lib.sh
# for why the full jq-encode-or-hard-block body lives there, not here.
emit_deny() {
  printf 'Blocked by %s gate: %s\n' "$DENY_GATE_LABEL" "$1" >&2
  exit 2
}

if ! . "${0%/*}/_lib.sh" 2>/dev/null; then
  # False positive: shellcheck's static pass doesn't model this stub-then-
  # override redefinition, which resolves correctly at call time (see
  # _lib.sh's _lib_emit_deny comment). Considered moving the definition
  # after the call instead, but that defeats the bootstrap's job of
  # covering the case where sourcing _lib.sh itself fails.
  # shellcheck disable=SC2218
  emit_deny "could not source _lib.sh; run ./install.sh to pick up hook files this update added (stow does not relink a new file into an existing directory until it is re-run)."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "could not parse tool-input JSON."

# Only gate Bash tool calls.
if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi

# Only gate commands that conclude a commit, using the broad predicate
# (armed on `git rebase --continue`); see:
# `docs/design-decisions/rebase-continue-marker-gate-carveout.md` § "Why `git rebase --continue` is not gated by the marker gates"
# Deliberately unchecked, matching this hook's own fail-open posture on the
# changed-keys jq failure path below: status 2 (could not determine) falls through the same
# "not gated, allow" path as status 1 (no match), rather than gaining a
# dedicated deny fork.
_lib_command_concludes_commit "$COMMAND" || exit 0

# Resolve the repo from the payload's cwd rather than this hook process's
# ambient cwd, matching require-plan-review.sh/require-code-review.sh.
# CWD is already populated by _lib_parse_tool_input_or_deny above.
[ -z "$CWD" ] && CWD="$PWD"

# Only proceed if inside a git repo.
if [ "$(_lib_capped git -C "$CWD" rev-parse --is-inside-work-tree 2>/dev/null)" != "true" ]; then
  exit 0
fi

SETTINGS_REPO_PATH="claude/.claude/settings.base.json"

# Check whether settings.base.json is staged at all.
# -c diff.relative=false keeps the names repo-root-relative whatever the
# payload's cwd and the user's diff.relative say.
# The names go through a variable, not a pipe: under pipefail a grep that exits
# on its first match can SIGPIPE git and fail the pipeline, which `!` would read
# as "not staged".
STAGED_NAMES=$(_lib_capped git -C "$CWD" -c diff.relative=false diff --cached --name-only 2>/dev/null)
if ! grep -qF "$SETTINGS_REPO_PATH" <<<"$STAGED_NAMES"; then
  exit 0
fi

# Diffs the staged version against origin/<default branch>; an unresolvable
# branch or missing file diffs against an empty baseline instead. Against
# that empty baseline, a staged guarded key denies and a staged file with none
# of the guarded keys allows.
# Latency tradeoff of the default-branch resolution: see
# `docs/design-decisions/guard-settings-session-keysshs-default-branch.md`.
if ! DEFAULT_BRANCH=$(_lib_default_branch_or_guess "$CWD"); then DEFAULT_BRANCH=""; fi
STAGED_CONTENT=$(_lib_capped git -C "$CWD" show :"$SETTINGS_REPO_PATH" 2>/dev/null)
if [ -z "$DEFAULT_BRANCH" ] || ! MAIN_CONTENT=$(_lib_capped git -C "$CWD" show "origin/$DEFAULT_BRANCH:$SETTINGS_REPO_PATH" 2>/dev/null); then
  MAIN_CONTENT=""
fi

# Name the guarded keys whose staged value differs from the default branch. Notes:
# - One jq call, not one per key: hooks fire on every matching tool call, so a
#   spawn-per-key loop would not hold the per-fire latency budget.
# - _lib_jq, not bare jq: a wedged jq would otherwise hang the gated commit
#   indefinitely, the same risk _lib_capped covers for git above.
# - guarded_value walks a dot-split path, so a nested key (e.g.
#   "env.CLAUDE_CODE_EFFORT_LEVEL") is guarded exactly like a top-level one —
#   including the null/false-vs-absent distinction — and a non-object
#   mid-path segment degrades to "not present" instead of erroring.
# - Content that does not parse degrades to {}, so keys the other side does
#   have still register as changed. Only a jq that cannot run at all yields no
#   names, and that path warns below rather than passing silently.
# - jq's `//` collapses a literal false env value to null, so a false-vs-absent
#   pair counts as no change.
# - `env` is guarded as a whole namespace, not only the dotted paths in
#   GUARDED_KEYS_JSON: any added, removed, or changed key under it denies,
#   including a credential-shaped one such as env.ANTHROPIC_AUTH_TOKEN.
# shellcheck disable=SC2016 # single-quoted on purpose: $guarded/$staged/$main are jq --arg bindings, not shell variables; double-quoting would expand them in the shell before jq sees them. Bare `jq` suppresses this itself, but the _lib_jq wrapper that carries the timeout backstop is opaque to shellcheck's jq awareness.
if ! CHANGED_KEYS=$(_lib_jq -rn \
  --argjson guarded "$GUARDED_KEYS_JSON" \
  --arg staged "$STAGED_CONTENT" \
  --arg main "$MAIN_CONTENT" \
  'def guarded_value($settings; $key):
     ($key | split(".")) as $path
     | reduce $path[] as $seg
         ({present: true, value: $settings};
          if .present and (.value | type) == "object" and (.value | has($seg))
          then {present: true, value: .value[$seg]}
          else {present: false, value: null}
          end)
     | if .present then [.value] else [] end;
   (($staged | fromjson?) // {}) as $staged_settings
   | (($main | fromjson?) // {}) as $main_settings
   | [ $guarded[]
       | . as $key
       | select(guarded_value($staged_settings; $key)
                != guarded_value($main_settings; $key)) ] as $exact_matches
   | (if ($staged_settings | type) == "object" then ($staged_settings.env // null) else null end) as $staged_env_raw
   | (if ($main_settings | type) == "object" then ($main_settings.env // null) else null end) as $main_env_raw
   | (($staged_env_raw | type) as $t | $t == "object" or $t == "null") as $staged_env_objlike
   | (($main_env_raw | type) as $t | $t == "object" or $t == "null") as $main_env_objlike
   | (if $staged_env_raw == $main_env_raw then []
      elif $staged_env_objlike and $main_env_objlike then
        (($staged_env_raw // {}) as $staged_env
         | ($main_env_raw // {}) as $main_env
         | (($staged_env | keys) + ($main_env | keys) | unique) as $env_keys
         | [ $env_keys[]
             | . as $k
             | [$staged_env | has($k), $staged_env[$k]] as $s
             | [$main_env | has($k), $main_env[$k]] as $m
             | select($s != $m)
             | ("env." + $k) ])
      else ["env"]
      end) as $env_matches
   | ($exact_matches + $env_matches | unique)
   | join(" ")' 2>/dev/null); then
  # Allow, matching this gate's fail-open posture, but say so — a silent
  # allow here is indistinguishable from a clean one, and leaves the engineer
  # believing a guard ran that did not.
  printf '%s\n' "guard-settings-session-keys: jq failed or timed out while comparing the staged settings keys — the settings-key guard did not evaluate this commit." >&2
  exit 0
fi

if [ -z "$CHANGED_KEYS" ]; then
  exit 0
fi

emit_deny "settings.base.json has machine-local or session-scoped keys changed — commit these only if intentional. The staged settings.base.json differs from ${DEFAULT_BRANCH:-the default branch} on: ${CHANGED_KEYS}. These keys hold one machine's own state, and several are written by Claude Code rather than by hand (model and effortLevel from /config, skipAutoPermissionPrompt when it records the permission-prompt preference), so committing them ships your local state as the shipped config for every user. Unstage the file (git restore --staged claude/.claude/settings.base.json) to allow the commit, or proceed only if this is a deliberate update."
