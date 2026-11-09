#!/usr/bin/env bash
# hook-class: gate
# tier-threat-model: cooperative, untrusted-input, irreversible
# Gate: deny an agent push whose destination is a protected branch, or whose
# destination the gate cannot resolve, plus a mutating `gh api` call to a
# ref-moving endpoint. Autonomous shipping tells the agent to push without
# asking, so no permission prompt backstops a push to the default branch.
# Text from a PR or a tool result can steer an agent into this push, so steered input is in scope.
#
# Scope: every push-family command (`git push`, `git subtree push`,
# `git send-pack`, and the dashed `git-push` forms) and every mutating
# `gh api` call whose endpoint is `repos/<o>/<r>/contents`, `git/refs` or
# `merges`, or the same paths under `repositories/<id>/`. Wrapped, scripted and
# HTTP pushes pass, and server-side branch protection is the barrier against them.
#
# Protected set: the branch `refs/remotes/<remote>/HEAD` names, plus `main` and
# `master`, plus `develop` when that HEAD does not resolve (_lib_push_protected_branches).
# A default branch other than `main`, `master` or `develop` is protected only
# where its remote's HEAD is set locally.
# A push to a URL or path remote protects `main`, `master` and `develop`.
#
# Judging: arguments are read from quote-masked text only, so a quoted mention
# of a push stays inert. A `$(` or backtick ends its fragment in a cut marker,
# and a push fragment that ends in it is denied as unresolvable. `gh api`
# endpoints are read from quote-stripped text, because a quoted endpoint
# holding `{owner}` or `$` masks to `""`.
# A refspec-less push is denied as unresolvable when a `-c` or `--config-env` setting names a `push.*`, `remote.*` or `branch.*` key.
# The environment arm denies a refspec-less push carrying any `GIT_CONFIG_COUNT=` or `GIT_CONFIG_PARAMETERS=` prefix, whatever the key.
#
# Fail posture, fail-closed on:
#   - A JSON parse error.
#   - A failed quote-strip, fragment-split or quote-mask step.
#   - A git call that fails with a status its caller does not document.
#     A cap kill counts only where `timeout` or `gtimeout` is on PATH; without one, git and jq run uncapped.
# Fail posture, denying every Bash call (the gate has no `if` matcher) on:
#   - A missing jq.
#   - A failed `sed` or `tr` fork.
#   - A `timeout` that rejects `-k`.
# Fail-open on a missing or empty command field, on a non-Bash tool, and on every shape listed under Known gaps.
#
# Dispatch: no `if` in settings.json; the gate self-filters, since an `if`
# pattern would miss aliased and dashed push forms.
# Per-fire cost exceeds the <100ms budget (plugins/claude-hook-review/skills/claude-hook-review/SKILL.md § "7. Performance budget") and grows with the command's line count, an exceedance accepted here.
#
# Server-side branch protection (docs/hooks.md § "Required server-side protection") is the only barrier for every gap below.
#
# Known gaps accepted on GH-1407:
#   - A push from a script file the agent writes.
#   - An alias defined in the same command.
#   - `gh api graphql`.
#   - `gh repo sync` of a fork. A POST to `repos/<owner>/<repo>/merge-upstream` moves a fork's default branch, and the endpoint set does not match it.
#   - `gh api`'s bundled `-iXPUT` and attached `-fmessage=m` spellings, which `_lib_gh_api_call_is_mutating` does not read as mutating.
#   - The bare `&`, which `_lib_split_fragments` does not split on.
#
# Known gaps accepted as permanent debt under the "Existing debt" rule in docs/hooks.md § "Threat-model tiers":
#   - A push inside a double-quoted `$(…)` or backtick span, which bash executes but quote masking treats as a mention.
#   - A push inside a shell wrapper's string argument (`bash -c "…"`, `sh -c '…'`, `eval "…"`, `xargs … sh -c '…'`), which quote masking treats as a mention.
#   - A push that also pushes submodules, through `--recurse-submodules`, `push.recurseSubmodules` or `submodule.recurse`, because only the superproject's refspecs are judged.
#   - A fragment earlier in the same command that changes the branch, config or remote HEAD the gate reads, such as `git config remote.origin.push HEAD:main && git push`, `git branch -m` or `git remote set-head`.
#   - A push over HTTP, such as `curl` with a token against the contents API, because no `git` or `gh` command is present.
#   - `gh api repos/<owner>/<repo>/pulls/<n>/merge`, tracked as GH-1077.
#   - Every mutating verb (`PUT`, `PATCH`, `POST` and `DELETE`) of a `gh api` call to `repos/<owner>/<repo>/branches/<branch>/protection` or `repos/<owner>/<repo>/rulesets`, which weakens or removes the server-side barrier above.
#     The barrier needs an agent credential without repository-administration rights.
#   - On BSD/macOS awk, a blank line inside a quoted span, such as a multi-paragraph commit message, can hide a push chained later in the same command. This is tracked in `_lib_mask_shell_quotes`' Limits.
#   - A `gh api` endpoint written after a boolean flag such as `--paginate`, `-i` or `--silent`. The endpoint read takes another word as the endpoint, so a computed endpoint after it passes, such as `gh api -X PUT -f message="add readme" $URL`.
#   - A `gh api` endpoint written after a quoted multi-word flag value. The endpoint read takes another word as the endpoint, so a computed endpoint after it passes.
#   - A `gh api` endpoint written after a flag value holding a command substitution. The `gh api` fragment is cut at the substitution, so a ref-moving endpoint after it passes, such as `gh api -X PUT -f content="$(base64 …)" repos/o/r/contents/f`.
#   - A `gh api` endpoint written after a flag value holding a quoted or escaped `;`, `|` or `&&`. The `gh api` fragment is cut at the separator, so a ref-moving endpoint after it passes, such as `gh api --jq '.a|.b' -X PUT repos/o/r/contents/f`.
#   - A `gh api` call after a shell keyword, such as `do gh api -X PUT repos/o/r/contents/f -f message=m`, `then`, `else`, `if`, `{` or `!`, because the `gh api` arm keys on the command word.
#   - A `gh api` call after an exec-style wrapper spelled `exec`, `nohup`, `timeout 30`, `gtimeout 30`, `/usr/bin/timeout` or `stdbuf -o L`, because the `gh api` arm keys on the command word.
#   - A `gh api` call after an assignment holding a substitution, such as `GH_TOKEN=$(cat token) gh api -X PUT repos/o/r/contents/f -f message=m`, because the `gh api` arm keys on the command word.
#   - A `gh api` endpoint hidden by a brace sequence, such as `gh api -X PUT repos/o/r/c{o..o}ntents/f -f message=m`, because the endpoint check reads the word before the shell expands it.
#   - A `gh api` endpoint hidden by an ANSI-C escape, such as `gh api -X PUT repos/o/r/$'\x63ontents'/f -f message=m`, because the endpoint check reads the word before the shell decodes it.
#   - A `gh api` call through a persistent `gh alias`, such as `gh NAME -f message=m` after `gh alias set NAME 'api -X PUT repos/{owner}/{repo}/contents/f'` in an earlier call, because the gate never expands a `gh` alias.
#   - A `gh api` call written as a double-parenthesis word, such as `((gh api -X PUT repos/o/r/contents/f -f message=m))`, because the command word reads `(gh`.
#   - A `gh api` call after a wrapper whose flag takes a separate value, such as `sudo -u root`, `env -u X` or `nice -n 5`, because the command word reads the flag's value.
#   - An unquoted command substitution in a `-C` path, such as `git -C $(…) push origin main`. The substitution cuts the fragment before the subcommand, so the push is never parsed.
#     The cut-marker deny is unreachable for it because the parser returns first.
#   - A push hidden by a backslash-quoted quote or by an apostrophe in a `#` comment, which the quote scanner reads as opening or closing a span, such as `echo \"; git push origin main; echo \"`.
#   - An odd apostrophe in an unquoted heredoc body, such as `don't`, which the quote scanner reads as opening a span, so a protected push later in the command is masked when another apostrophe follows it.
#   - A push whose command word carries an attached parenthesis or brace, such as `then (git push origin main)`, `{ (git push origin main); }` or `cat <(git push origin main)`.
#   - A push whose destination carries an attached parenthesis, such as `(cd w && git push origin HEAD) 2>&1`, which reads the destination as `HEAD)`.
#   - An attached `&` on a destination, remote or subcommand word, such as `git push origin main&`, which stays in the word, so the push is judged as an unprotected name.
#   - A git option negation or abbreviation the parser does not read, such as `--no-dry-run`, `--no-tags` or an abbreviated `--mirr`, where git accepts the spelling.
#   - A spelling that relocates the config a refspec-less push reads, such as `GIT_CONFIG_GLOBAL=<file>`, `HOME=<dir>` or `-c include.path=<file>`.
#   - `env -u git git push origin main`, because the parser binds to the first `git`-named word, here the word after `-u`.
#   - A redirect between `git` and its subcommand, such as `git 2>&1 push origin main` or `git >/dev/null push origin main`, which the parser reads as the subcommand.
#   - A leading redirect whose target ends in `/git`, such as `2>/tmp/git git push origin main`, which the parser reads as the command word.
#   - A `git push` option value that the shell rewrites into extra words, such as `git push -o {x,origin} HEAD:main`, because the parser skips the option's value and reads `HEAD:main` as the remote.
#   - A backslash or brace inside any word the parser compares to a literal, such as the command word (`\git push origin main`), the subcommand (`git pu\sh origin main`) or an option (`git push --mir\ror origin`), because the word no longer matches.
#   - An unbraced `$name`, a field-split variable or a glob character in the command or subcommand word, such as `git$IFS push origin main` or `/usr/bin/gi[t] push origin main`, because the word no longer matches and the push goes unrecognised.
#   - `GIT push origin main` on a case-insensitive volume, where bash resolves `GIT` to the git binary.
#   - `GH api -X PUT repos/o/r/contents/f …`, `/usr/bin/GH api …` or `sudo GH api …` on a case-insensitive volume, because the command-word match folds case and the argv walk does not.
#   - A backslash-newline join that ignores backslash parity, such as `echo a\\` followed by a newline and `git push origin main`, which bash reads as two commands.
#   - `remote.<name>.mirror`, which makes a refspec-less `git push <name>` mirror every ref and is never read.
#   - An explicit-refspec push after a `cd`, such as `cd <dir> && git push origin feature`, which is judged against the working directory from before the `cd`.
#   - A refspec-less push whose `@{push}` fails under `push.default=upstream`, where the fallback judges the local branch name instead of the upstream branch's.
#   - A refspec-less push whose `@{push}` fails under `remote.pushDefault`, `branch.<name>.pushRemote` or `branch.<name>.remote` naming a remote other than `origin`, where the fallback reads the protected set from `origin` for a bare `git push` and from the remote word otherwise.
#   - A `gh api` endpoint path written with a double slash, a dot segment (`/./`) or a percent-encoded letter, such as `repos/o/r/%63ontents/f`, because the endpoint regex matches the path as written.
#   - A refspec-less push to a URL or path remote, which judges only `main`, `master` and `develop`, so a differently named default branch of the branch's `@{push}` remote is not protected.
#   - A quoted or computed `-c` key, such as `git -c 'remote.origin.push=HEAD:main' push`, which masks to an empty quoted pair and slips the config-key filter.
#   - A refspec-less push, bare or with a remote word and flags, followed by a same-line comment, such as `git push # publish` or `git push -u origin # publish`, because the parser reads the comment words as the remote or refspecs, so the config, `@{push}` and HEAD judgment never runs.
#   - A configured remote group (`remotes.<group>`), because it passes the plain-name test and is judged against `main`, `master` and `develop` only.
#   - A name-shaped word that git reads as a path, such as a symlink `o` in the working directory, and a configured remote named with `/`, `+` or `@`, because the gate classifies a remote word by its spelling, not by git's own resolution.
#
# See `_lib_mask_shell_quotes`' Limits for the other residuals the masked pass inherits.
#
# Accepted over-denials (the irreversible tier names over-deny as the correct trade):
#   - An unquoted heredoc body line that reads as a protected push.
#   - Prose in an unquoted heredoc body or `echo` argument that names a user's `!`-alias after a `git` word, such as `Run git sync after merging`, which is denied as an unresolvable push.
#   - A backtick-wrapped push.
#   - A computed `-C` path.
#   - A refspec-less push, or a push whose destination is `HEAD` or `@`, chained after a `cd`, `pushd`, `popd`, `git checkout`, `git switch` or `gh pr checkout` fragment, such as `cd <worktree> && git push -u origin HEAD`.
#   - A quoted refspec, `-C` path or `--git-dir` path holding a character outside `[A-Za-z0-9._/-]`, such as `git push origin "HEAD:feature"` or `git -C "<dir with space>" push`, which masks to `""`.
#   - An alias whose value starts with another alias, a git extension, or a builtin `_LIB_GIT_BUILTIN_SUBCOMMANDS` omits.
#   - A mutating `gh api` call to a ref-moving endpoint, quoted as inline code or at the start of a quoted line.
#   - A mutating `gh api` call with any word, a flag value or field text included, that names a ref-moving endpoint path.
#   - A mutating `gh api` call with a boolean flag such as `--paginate`, or a quoted multi-word flag value, before its endpoint, when the first later word gh's flag grammar leaves positional holds `$`, `$(` or a backtick, such as `gh api -X POST -f body="hello $USER" repos/o/r/issues/1/comments`.
#   - On BSD/macOS awk, a blank line inside a quoted commit body inverts quote parity for the rest of the command, so a later quoted mention of a push reads as a live fragment and is denied.
#   - A remote, refspec, `-C` path, `--git-dir` path or `GIT_DIR=` value holding `\`, `{`, `}`, `?`, `[` or `]`, such as `HEAD:ma{i,i}n`, which the shell rewrites before git sees it.
#     The gate denies `HEAD^{commit}` too: the shell leaves it alone, but the gate cannot tell a rewritten word from a literal one.
#   - A `gh api` read of a ref-moving endpoint beside a body flag or write method anywhere in the same command, because `_lib_gh_api_call_is_mutating` reads the whole stripped command.
#     Examples are `gh api -X GET … -f key=value`, `gh api … && rm -f scratch.txt` and `gh api … | grep -F text`.
#
# Where `~/.claude/hooks` is a real directory, a `git pull` leaves this file
# unlinked while settings.json already registers it, and every Bash call then
# blocks. See docs/hooks.md § "Gate deadlock recovery".

set -uo pipefail

DENY_GATE_LABEL="default-branch-push"

# Minimal bootstrap so a failed `source` of _lib.sh below can still deny.
# Re-pointed at _lib.sh's _lib_emit_deny immediately after a successful source.
emit_deny() {
  printf 'Blocked by %s gate: %s\n' "$DENY_GATE_LABEL" "$1" >&2
  exit 2
}

if ! . "${0%/*}/_lib.sh" 2>/dev/null; then
  # False positive: shellcheck's static pass doesn't model this stub-then-
  # override redefinition, which resolves correctly at call time (see
  # _lib.sh's _lib_emit_deny comment).
  # shellcheck disable=SC2218
  emit_deny "could not source _lib.sh. For the recovery, see docs/hooks.md § \"Gate deadlock recovery\" in the claude-config repository; the user, not the agent, runs ./install.sh from the claude-config checkout in a separate terminal only if that runbook says so."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "could not parse tool-input JSON."

# Defense-in-depth: only act on Bash calls (settings.json registers no matcher narrower than Bash).
if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi

[ -z "$COMMAND" ] && exit 0
[ -z "$CWD" ] && CWD="$PWD"

COMMAND_PREVIEW="${COMMAND:0:200}"

# Stands in for each `$(` and backtick. It holds none of the characters
# _lib_git_push_word_is_computed flags, so it survives as part of a word.
CUT_MARKER="__DEFAULT_BRANCH_PUSH_CUT__"

# A `gh api` endpoint that moves a ref or merges: contents, git/refs, merges.
GH_API_REF_ENDPOINT_REGEX='(^|/)(repos/[^/]+/[^/]+|repositories/[0-9]+)/(contents|git/refs|merges)([/?]|$)'

# Judging state: set by the fragment judge, read by the push judge.
MOVED=false
TARGET_ARGS=()
TARGET_FORM=C
TARGET_PATH=""
PROTECTED_KEY=""
PROTECTED_BRANCHES=""
HEAD_BRANCH=""
GIT_CALL_OUTPUT=""
EXPANDED_FRAGMENT=""
FRAGMENTS=()

deny_and_exit() {
  emit_deny "$1"
  exit 0
}

deny_protected_destination() {
  deny_and_exit "'${COMMAND_PREVIEW}' would push to the protected branch '$1' of remote '$2'. Push a feature branch and open a PR, or ask the engineer to run the push with the ! prefix."
}

deny_unresolved() {
  deny_and_exit "'${COMMAND_PREVIEW}' pushes to a destination this gate cannot resolve: $1. Run one literal push as its own Bash call, such as 'git push -u origin HEAD' from a feature branch."
}

deny_moved() {
  deny_and_exit "'${COMMAND_PREVIEW}' pushes after an earlier fragment (cd, pushd, popd, git checkout, git switch or gh pr checkout) changed the directory or branch this gate reads, so the push destination cannot be resolved. Run the push as its own Bash call, with 'git -C <dir> push …' in place of 'cd <dir> && git push …'."
}

deny_failed_step() {
  deny_and_exit "could not $1 (status $2) while judging '${COMMAND_PREVIEW}'. Failing closed per this gate's documented posture rather than letting an unjudged push through."
}

deny_gh_api() {
  deny_and_exit "'${COMMAND_PREVIEW}' is a mutating gh api call whose $1, so it can move a ref or merge a branch. Push a feature branch and open a PR, or ask the engineer to run the call with the ! prefix. A read this check catches can run as its own Bash call with no body flag (-f, -F, --input) or write method (-X PUT) elsewhere in the command."
}

# Sets GIT_CALL_OUTPUT to git's stdout and returns its status. Runs in the
# current shell so a caller's deny can exit the script.
git_call() {
  GIT_CALL_OUTPUT=$(_lib_capped git "$@" 2>/dev/null </dev/null)
}

load_fragments() {
  local line
  FRAGMENTS=()
  while IFS= read -r line; do
    [[ "$line" =~ [^[:space:]] ]] && FRAGMENTS+=("$line")
  done <<< "$1"
}

# Prints _lib_split_fragments' fragments of TEXT with each `$(` and backtick
# turned into the cut marker plus a newline, so a substitution ends its
# fragment visibly instead of silently shortening it.
split_with_cut_marker() {
  # shellcheck disable=SC2016 # `$(` is the literal text to match, not an expansion; double quotes would run it.
  local text="$1" opener='$(' backtick='`'
  local cut_newline="${CUT_MARKER}"$'\n'
  text="${text//"$opener"/$cut_newline}"
  text="${text//"$backtick"/$cut_newline}"
  _lib_split_fragments "$text"
}

fragment_ends_in_cut_marker() {
  local trimmed="${1%"${1##*[![:space:]]}"}"
  [[ "$trimmed" == *"$CUT_MARKER" ]]
}

fragment_has_directory_change_word() {
  local saved_opts=$- word found=1
  set -f
  for word in $1; do
    case "$word" in
      cd|pushd|popd) found=0; break ;;
    esac
  done
  if [[ "$saved_opts" != *f* ]]; then set +f; fi
  return "$found"
}

# Exit 0 when FRAGMENT changes the branch or directory a later push reads.
fragment_moves_state() {
  local fragment="$1" subcmd gh_argv
  fragment_has_directory_change_word "$fragment" && return 0
  if _lib_fragment_invokes_git "$fragment"; then
    subcmd=$(_lib_extract_git_subcmd "$fragment")
    case "$subcmd" in
      checkout|switch) return 0 ;;
    esac
  fi
  if _lib_fragment_invokes_tool "$fragment" gh; then
    gh_argv=$(_lib_tool_argv_from_subcmd "$fragment" gh)
    case "$gh_argv" in
      pr$'\n'checkout|pr$'\n'checkout$'\n'*) return 0 ;;
    esac
  fi
  return 1
}

# Prints the subcommand of a git FRAGMENT that is neither push-family nor a
# builtin, so git may expand it as an alias. Exit 1 otherwise.
git_alias_candidate() {
  local fragment="$1" subcmd builtin
  _lib_fragment_invokes_git "$fragment" || return 1
  subcmd=$(_lib_extract_git_subcmd "$fragment")
  [ -n "$subcmd" ] || return 1
  _lib_git_push_targets "$fragment" >/dev/null && return 1
  for builtin in "${_LIB_GIT_BUILTIN_SUBCOMMANDS[@]}"; do
    [ "$subcmd" = "$builtin" ] && return 1
  done
  printf '%s' "$subcmd"
}

fragment_is_gh_api() {
  local first_word
  _lib_fragment_invokes_tool "$1" gh || return 1
  first_word=$(_lib_tool_argv_from_subcmd "$1" gh)
  [ "${first_word%%$'\n'*}" = "api" ]
}

# Exit 0 when a stripped FRAGMENT needs judging: a push, an alias candidate or a `gh api` call.
fragment_needs_judging() {
  _lib_git_push_targets "$1" >/dev/null && return 0
  git_alias_candidate "$1" >/dev/null && return 0
  fragment_is_gh_api "$1"
}

# set_target FORM PATH: sets TARGET_ARGS (git's repository selector), TARGET_FORM
# and TARGET_PATH from a parser `repo` record. A relative path resolves against
# the payload CWD, and the cwd form becomes `-C <CWD>`.
set_target() {
  local form="$1" target_path="$2"
  if [ -n "$target_path" ] && [[ "$target_path" != /* ]]; then
    target_path="$CWD/$target_path"
  fi
  if [ "$form" = git-dir ]; then
    TARGET_FORM=git-dir
    TARGET_PATH="$target_path"
    TARGET_ARGS=("--git-dir=$target_path")
  else
    [ "$form" = C ] || target_path="$CWD"
    TARGET_FORM=C
    TARGET_PATH="$target_path"
    TARGET_ARGS=(-C "$target_path")
  fi
}

set_target_from_records() {
  local line rest
  while IFS= read -r line; do
    case "$line" in
      repo$'\t'*)
        rest="${line#repo$'\t'}"
        set_target "${rest%%$'\t'*}" "${rest#*$'\t'}"
        return 0
        ;;
    esac
  done <<< "$1"
  set_target cwd ""
}

# Sets HEAD_BRANCH to the target's current branch, or denies when HEAD is detached or unreadable.
resolve_head_branch() {
  local call_status=0
  git_call "${TARGET_ARGS[@]}" symbolic-ref -q --short HEAD || call_status=$?
  case "$call_status" in
    # `--short` prints `heads/<name>` when a tag shares the branch name.
    0) HEAD_BRANCH="${GIT_CALL_OUTPUT#heads/}" ;;
    1) deny_unresolved "HEAD is detached" ;;
    *) deny_failed_step "read HEAD with git symbolic-ref" "$call_status" ;;
  esac
}

# Loads the protected set for REMOTE on the current target, once per target and remote.
load_protected_branches() {
  local remote="$1" key call_status=0
  key="$TARGET_FORM|$TARGET_PATH|$remote"
  [ "$key" = "$PROTECTED_KEY" ] && return 0
  PROTECTED_BRANCHES=$(_lib_push_protected_branches "$TARGET_FORM" "$TARGET_PATH" "$remote") || call_status=$?
  [ "$call_status" -eq 0 ] || deny_failed_step "read the protected branches of remote '$remote' with _lib_push_protected_branches" "$call_status"
  PROTECTED_KEY="$key"
}

branch_is_protected() {
  case $'\n'"$PROTECTED_BRANCHES"$'\n' in
    *$'\n'"$1"$'\n'*) return 0 ;;
  esac
  return 1
}

# judge_destination DESTINATION REMOTE: denies a destination that is, or
# resolves to, a protected branch of REMOTE.
judge_destination() {
  local destination="$1" remote="$2" branch
  # A tag, `refs/for/<x>` or any other non-branch namespace moves no branch.
  [[ "$destination" == refs/* ]] && return 0
  [ -n "$destination" ] || deny_unresolved "an empty refspec destination"
  branch="$destination"
  if [ "$destination" = HEAD ] || [ "$destination" = "@" ]; then
    resolve_head_branch
    branch="$HEAD_BRANCH"
  fi
  load_protected_branches "$remote"
  if branch_is_protected "$branch"; then
    deny_protected_destination "$branch" "$remote"
  fi
}

judge_refspecless_config() {
  local key value call_status=0 push_default=""
  git_call "${TARGET_ARGS[@]}" config --get-regexp '^(push\.default|remote\..*\.push)$' || call_status=$?
  case "$call_status" in
    0) ;;
    1) return 0 ;;
    *) deny_failed_step "read push config with git config --get-regexp" "$call_status" ;;
  esac
  while read -r key value; do
    case "$key" in
      push.default) push_default="$value" ;;
      remote.*.push) deny_unresolved "'$key' redirects a refspec-less push" ;;
    esac
  done <<< "$GIT_CALL_OUTPUT"
  if [ "$push_default" = matching ]; then
    deny_unresolved "push.default=matching pushes every matching branch"
  fi
}

# Prints the remote name that prefixes tracking ref FULL_REF (refs/remotes/<remote>/<branch>), longest first match.
# Exit 1 when no configured remote prefixes it.
split_tracking_ref() {
  local full_ref="$1" remote_names="$2" name best=""
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    if [[ "$full_ref" == "refs/remotes/$name/"* ]] && [ "${#name}" -gt "${#best}" ]; then
      best="$name"
    fi
  done <<< "$remote_names"
  [ -n "$best" ] || return 1
  printf '%s' "$best"
}

# judge_refspecless REMOTE_WORD CONFIG_KEY...: a push with no refspec word lands where git resolves @{push}.
judge_refspecless() {
  local remote_word="$1" key call_status=0 full_ref push_remote push_branch
  shift
  for key in "$@"; do
    case "$key" in
      push.*|remote.*|branch.*|"*") deny_unresolved "a -c, --config-env or environment setting ('$key') can redirect a refspec-less push" ;;
    esac
  done
  judge_refspecless_config
  git_call "${TARGET_ARGS[@]}" rev-parse --symbolic-full-name '@{push}' || call_status=$?
  case "$call_status" in
    0)
      full_ref="$GIT_CALL_OUTPUT"
      git_call "${TARGET_ARGS[@]}" remote || call_status=$?
      [ "$call_status" -eq 0 ] || deny_failed_step "list remotes with git remote" "$call_status"
      if push_remote=$(split_tracking_ref "$full_ref" "$GIT_CALL_OUTPUT"); then
        push_branch="${full_ref#"refs/remotes/$push_remote/"}"
        # The command's remote word, not the @{push} remote, decides where the push lands.
        push_remote="${remote_word:-$push_remote}"
        load_protected_branches "$push_remote"
        if branch_is_protected "$push_branch"; then
          deny_protected_destination "$push_branch" "$push_remote"
        fi
        return 0
      fi
      ;;
    128) ;;
    *) deny_failed_step "resolve @{push} with git rev-parse" "$call_status" ;;
  esac
  # @{push} errors when no push destination is configured, so judge the current branch name on the command's remote.
  resolve_head_branch
  push_remote="${remote_word:-origin}"
  load_protected_branches "$push_remote"
  if branch_is_protected "$HEAD_BRANCH"; then
    deny_protected_destination "$HEAD_BRANCH" "$push_remote"
  fi
}

# judge_push_records RECORDS: judges one push fragment from the parser's records.
judge_push_records() {
  local records="$1" line rest destination reason
  local repo_form=cwd repo_path="" remote="" no_refspec=false tags=false dry_run=false
  local -a config_keys=() destinations=() reasons=()
  while IFS= read -r line; do
    case "$line" in
      repo$'\t'*)
        rest="${line#repo$'\t'}"
        repo_form="${rest%%$'\t'*}"
        repo_path="${rest#*$'\t'}"
        ;;
      config$'\t'*) config_keys+=("${line#config$'\t'}") ;;
      remote$'\t'*) remote="${line#remote$'\t'}" ;;
      ref$'\t'*) destinations+=("${line##*$'\t'}") ;;
      unresolvable$'\t'*) reasons+=("${line#unresolvable$'\t'}") ;;
      no-refspec) no_refspec=true ;;
      tags) tags=true ;;
      dry-run) dry_run=true ;;
    esac
  done <<< "$records"

  $dry_run && return 0
  if [ "${#reasons[@]}" -gt 0 ]; then
    reason="${reasons[*]}"
    deny_unresolved "${reason// /, }"
  fi
  # Only tags move: git consults neither remote.*.push nor push.default under --tags.
  if $tags && [ "${#destinations[@]}" -eq 0 ]; then
    return 0
  fi
  if $MOVED; then
    $no_refspec && deny_moved
    for destination in "${destinations[@]}"; do
      if [ "$destination" = HEAD ] || [ "$destination" = "@" ]; then
        deny_moved
      fi
    done
  fi

  set_target "$repo_form" "$repo_path"
  for destination in "${destinations[@]}"; do
    judge_destination "$destination" "${remote:-origin}"
  done
  if $no_refspec; then
    judge_refspecless "$remote" "${config_keys[@]}"
  fi
}

# Looks up `alias.<name>` on the target of FRAGMENT. Sets EXPANDED_FRAGMENT to FRAGMENT with
# the alias word replaced by its value and returns 0. Returns 1 when git defines no such alias.
# Denies on a value starting with `!` and on a failed lookup.
expand_git_alias() {
  local fragment="$1" alias_name="$2" call_status=0 alias_value
  local -a words=() argv_words=() head_words=()
  local argv word_count argv_count
  read -r -a words <<< "$fragment"
  argv=$(_lib_git_argv_from_subcmd "$fragment")
  mapfile -t argv_words <<< "$argv"
  word_count=${#words[@]}
  argv_count=${#argv_words[@]}
  head_words=("${words[@]:0:word_count-argv_count}")
  # The head holds the git command word and its global options; a synthetic `push` gives the parser the repo selector.
  set_target_from_records "$(_lib_git_push_targets "${head_words[*]} push")"
  git_call "${TARGET_ARGS[@]}" config --get "alias.$alias_name" || call_status=$?
  case "$call_status" in
    0) alias_value="$GIT_CALL_OUTPUT" ;;
    1) return 1 ;;
    *) deny_failed_step "look up alias '$alias_name' with git config --get" "$call_status" ;;
  esac
  if [[ "$alias_value" == "!"* ]]; then
    deny_unresolved "alias '$alias_name' runs a shell command this gate does not expand"
  fi
  EXPANDED_FRAGMENT="${head_words[*]} $alias_value ${argv_words[*]:1}"
}

# judge_fragment FRAGMENT: judges one masked fragment in command order.
judge_fragment() {
  local fragment="$1" alias_expanded=false alias_name records parse_status=0
  while true; do
    # A push whose refspec happens to read `cd` is judged as a push, not skipped as a directory change.
    if ! _lib_git_push_targets "$fragment" >/dev/null && fragment_moves_state "$fragment"; then
      MOVED=true
      return 0
    fi
    if alias_name=$(git_alias_candidate "$fragment"); then
      $alias_expanded && deny_unresolved "an alias value expands to '$alias_name', which is another alias, a git extension or a command this gate does not recognize"
      alias_expanded=true
      expand_git_alias "$fragment" "$alias_name" || return 0
      fragment="$EXPANDED_FRAGMENT"
      continue
    fi
    break
  done
  records=$(_lib_git_push_targets "$fragment") || parse_status=$?
  [ "$parse_status" -eq 0 ] || return 0
  if fragment_ends_in_cut_marker "$fragment"; then
    deny_unresolved "a command substitution ends the push command"
  fi
  judge_push_records "$records"
}

# check_gh_api_fragment FRAGMENT: denies a mutating `gh api` call to a ref-moving or unreadable endpoint.
check_gh_api_fragment() {
  local fragment="$1" saved_opts=$- word past_api=false gh_argv endpoint_word
  fragment_is_gh_api "$fragment" || return 0
  # Scans the whole command: a substitution, or a quoted or escaped separator in the call's own words, moves its method and body flags into a later fragment.
  _lib_gh_api_call_is_mutating "$STRIPPED_COMMAND" || return 0
  set -f
  for word in $fragment; do
    if ! $past_api; then
      [ "$word" = api ] && past_api=true
      continue
    fi
    if [[ "$word" =~ $GH_API_REF_ENDPOINT_REGEX ]]; then
      if [[ "$saved_opts" != *f* ]]; then set +f; fi
      deny_gh_api "words name a ref-moving endpoint ('$word')"
    fi
  done
  if [[ "$saved_opts" != *f* ]]; then set +f; fi
  gh_argv=$(_lib_tool_argv_from_subcmd "$fragment" gh)
  [[ "$gh_argv" == *$'\n'* ]] || return 0
  endpoint_word="${gh_argv#*$'\n'}"
  endpoint_word="${endpoint_word%%$'\n'*}"
  case "$endpoint_word" in
    *'$'*|*"$CUT_MARKER"*) deny_gh_api "endpoint word ('$endpoint_word') is computed, so the gate cannot read it" ;;
  esac
}

# Step 1: a backslash-newline pair joins lines, as the shell reads it.
BACKSLASH_NEWLINE=$'\\\n'
JOINED_COMMAND="${COMMAND//"$BACKSLASH_NEWLINE"/}"

STRIPPED_COMMAND=$(_lib_strip_shell_quotes "$JOINED_COMMAND")
STEP_STATUS=$?
[ "$STEP_STATUS" -eq 0 ] || deny_failed_step "strip shell quotes" "$STEP_STATUS"

# Step 2: split the stripped text with substitution openers cut visibly.
STRIPPED_SPLIT=$(split_with_cut_marker "$STRIPPED_COMMAND")
STEP_STATUS=$?
[ "$STEP_STATUS" -eq 0 ] || deny_failed_step "split the stripped command into fragments" "$STEP_STATUS"
load_fragments "$STRIPPED_SPLIT"
STRIPPED_FRAGMENTS=("${FRAGMENTS[@]}")

# Step 3: exit before any git call unless some fragment is a push, an alias candidate or a `gh api` call.
NEEDS_JUDGING=false
for FRAGMENT in "${STRIPPED_FRAGMENTS[@]}"; do
  if fragment_needs_judging "$FRAGMENT"; then
    NEEDS_JUDGING=true
    break
  fi
done
$NEEDS_JUDGING || exit 0

# Step 4: the masked pass, the only pass that judges a push.
MASKED_COMMAND=$(_lib_mask_shell_quotes "$JOINED_COMMAND")
STEP_STATUS=$?
[ "$STEP_STATUS" -eq 0 ] || deny_failed_step "mask quoted command text" "$STEP_STATUS"
MASKED_SPLIT=$(split_with_cut_marker "$MASKED_COMMAND")
STEP_STATUS=$?
[ "$STEP_STATUS" -eq 0 ] || deny_failed_step "split the masked command into fragments" "$STEP_STATUS"
load_fragments "$MASKED_SPLIT"
for FRAGMENT in "${FRAGMENTS[@]}"; do
  judge_fragment "$FRAGMENT"
done

# Step 5: the `gh api` check reads endpoints from stripped text.
for FRAGMENT in "${STRIPPED_FRAGMENTS[@]}"; do
  check_gh_api_fragment "$FRAGMENT"
done

exit 0
