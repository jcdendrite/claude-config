#!/bin/bash
# hook-class: gate
# tier-threat-model: cooperative
# Gate: require /skill-review before git commit when SKILL.md files are staged,
# verified via marker file.
#
# WARNING: Do NOT remove the internal git commit check below.
# The "if" field in settings.json is unreliable — it has been observed
# to fire this hook on ALL Bash commands (e.g., git reset, date).
# The internal grep is the actual gate. The "if" field is a hint only.
#
# How it works:
# - The /skill-review skill writes
#   ~/.claude/skill-review-markers/<repo-hash>.<session_id> with the sha256 hash
#   of the staged diff scoped to the same pathspec set this hook defines below
#   (MARKER_PATHSPECS) when the review is clean. The
#   marker lives under $HOME (not inside the repo) so it never pollutes
#   `git status` or risks being accidentally committed.
# - This hook recomputes the same path-scoped diff hash at commit time and
#   looks for any marker under this repo-hash holding that value. Match =
#   the staged skill changes were reviewed, allow the commit. Mismatch/missing
#   = deny and redirect Claude to run /skill-review.
# - The <session_id> in the filename is a WRITE-side key only: it prevents
#   two parallel sessions in the same worktree from overwriting each other's
#   markers when they stage different diffs. The read globs across it,
#   because the stored hash — not the filename — is what proves the review
#   covered this diff. Reading the session key as an authorization predicate
#   would deny a resumed session (new session_id) a review it already
#   completed against the identical staged state.
# - The marker is scoped to SKILL.md and plan-review/ROUTING.md diffs only
#   (not the full staged diff), so re-staging other files after a clean
#   skill-review does not invalidate the marker.
# - The trigger, the structural validator's path list, and the marker hash
#   below are all threaded through one novel-content base
#   (_lib_skill_review_diff_base): mid-merge/cherry-pick/rebase, gated
#   content an already-reviewed upstream commit brought in unchanged reads
#   as already-reviewed rather than newly staged. See
#   docs/design-decisions/skill-review-gate-disarms-on-empty-base-relative-diff.md
#   for the full mechanism, including why revert is excluded.
# - The base resolves once, immediately after REPO_ROOT, with no
#   HEAD-relative prefilter ahead of it: a keep-ours resolution of a
#   conflicted SKILL.md leaves the index equal to HEAD, so a HEAD-relative
#   prefilter would disarm on exactly the commit that discards upstream's
#   reviewed content.
# - Known gap: a conflict-free merge/cherry-pick/revert still reaches a commit
#   with no gate firing; see "Known gap: the ungated clean merge" in
#   docs/design-decisions/skill-review-gate-disarms-on-empty-base-relative-diff.md.
# - The structural validator's path list excludes deletions by the diff's own status
#   (`--diff-filter=d`), and a staged deletion reaches the marker check, which covers
#   it through the base-relative hash.
# - With a non-empty base, a HEAD-relative scan of the gated pathspecs
#   (`git diff -G`, then `git grep` on the staged blobs) hard-denies a staged
#   file that carries column-0 conflict-marker lines and whose staged change
#   touches such a line.
#   Mid-merge the base tree holds conflict-marker blobs, so an unresolved file
#   can read as already reviewed; see "Conflict-marker hard deny" in
#   docs/design-decisions/skill-review-gate-disarms-on-empty-base-relative-diff.md.
# - Fail-closed denies, each rather than disarming or skipping the structural
#   validator:
#   - the conflict-marker scan fails.
#   - any of the three staged-path listings fails.
#   - a listed non-deleted path's blob cannot be read: it is unmerged, the
#     index is corrupt, git still lists its name C-quoted, or its name does
#     not survive argv normalization.
# - Known gap: with neither timeout nor gtimeout on PATH, every _lib_capped
#   site runs uncapped and a stalled git releases the commit at the harness
#   timeout; see "Latency" in
#   docs/design-decisions/skill-review-gate-disarms-on-empty-base-relative-diff.md.
# - Known gap: the validator reads each listed path's blob by re-resolving its
#   name, so a hand-built index with aliased entry names leaves an invalid entry
#   unvalidated; see "Known residual: aliased index entries" in
#   docs/design-decisions/skill-review-gate-disarms-on-empty-base-relative-diff.md.
# - Known gap: an auto-merge that combines two valid gated files into one
#   invalid tree escapes the structural validator, which sees only the
#   base-relative path set; see "Known residual: the structural validator's
#   auto-merge give-up" in
#   docs/design-decisions/skill-review-gate-disarms-on-empty-base-relative-diff.md.
set -uo pipefail

emit_deny() {
  local reason="$1"
  local reason_json
  # Defined before _lib.sh is sourced so a failed source can still deny,
  # which means _lib_jq may not exist yet. Prefer it when it does, for its
  # timeout backstop.
  if declare -F _lib_jq >/dev/null 2>&1; then
    reason_json=$(printf '%s' "$reason" | _lib_jq -Rs . 2>/dev/null)
  else
    reason_json=$(printf '%s' "$reason" | jq -Rs . 2>/dev/null)
  fi
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

if ! . "${0%/*}/_lib.sh" 2>/dev/null; then
  emit_deny "Blocked by skill-review gate: could not source _lib.sh."
  exit 0
fi

_lib_parse_tool_input_or_deny "Blocked by skill-review gate: could not parse tool-input JSON."

# Filter by tool name in the hook itself rather than relying on the
# settings.json matcher — the "if" field is a hint only (see the warning
# above), and a non-Bash payload yields an empty COMMAND that would otherwise
# reach the git-commit grep and pass by accident rather than by intent.
if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi

# Only gate git commit commands — exit 0 (no opinion) for everything else.
# Match `git commit` at the start of the command OR after a shell separator
# (`&&`, `||`, `;`, `|`, `&`), so chained forms like `git add . && git commit`
# are also caught. The trailing `([[:space:]]|$)` ensures we don't match
# `git commit-tree` or other `git commit`-prefixed subcommands.
if ! printf '%s\n' "$COMMAND" | grep -qE '(^|&&?|;|\|\|?)[[:space:]]*git[[:space:]]+commit([[:space:]]|$)'; then
  exit 0
fi

# Resolve the repo from the payload's cwd rather than this hook process's
# ambient cwd, and thread that one root through every git call below. The
# marker's path (repo hash) and its value (staged SKILL.md diff hash) must
# describe the same tree: resolving the root one way and hashing the diff
# another lets a session whose shell drifted to a different working tree of
# the same repo satisfy the gate with a review of a tree nobody reviewed.
CWD=$(printf '%s\n' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
[ -z "$CWD" ] && CWD="$PWD"

REPO_ROOT=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null)
if [ -z "$REPO_ROOT" ]; then
  # Not in a git repo — let git surface the error itself
  exit 0
fi

# Resolved once per invocation and threaded through the trigger, the
# structural validator's path list, and the marker hash below.
# BASE empty means every consumer below diffs HEAD-relative: status 1 is no
# trusted in-progress state or a revert, status 2 is a failed base computation.
# Worst-case sizing: docs/design-decisions/skill-review-gate-disarms-on-empty-base-relative-diff.md, "Latency".
BASE=$(_lib_skill_review_diff_base "$REPO_ROOT")
BASE_STATUS=$?

# gated_paths_at_base REPO_ROOT BASE DIFF_FILTER PATHSPEC...
# `git diff --cached --name-only`, restricted to PATHSPEC and, when BASE is
# non-empty, diffed against it instead of HEAD.
# A non-empty DIFF_FILTER is passed as `--diff-filter=<DIFF_FILTER>`; `d` excludes deletions.
# Runs under _lib_capped and returns git's own exit status, or the cap-kill
# status (124/137/143, see _lib_capped_for's header). A non-zero status denies below.
# core.quotepath=false keeps non-ASCII paths unquoted so `git show :<path>` resolves them.
# Paths git still quotes (embedded quote, tab, newline) fail that `git show` and deny below.
gated_paths_at_base() {
  local repo_root="$1" base="$2" diff_filter="$3"
  shift 3
  local -a diff_args=(-C "$repo_root" -c core.quotepath=false diff --cached --name-only)
  [ -n "$diff_filter" ] && diff_args+=("--diff-filter=$diff_filter")
  [ -n "$base" ] && diff_args+=("$base")
  diff_args+=(-- "$@")
  _lib_capped git "${diff_args[@]}"
}

# Scoped pathspec sets, shared by every site below that enumerates SKILL.md
# layouts. No bare `*SKILL.md` glob, which would sweep in vendored or fixture
# files.
SKILL_CONTENT_PATHSPECS=('claude-skills/skills/**/SKILL.md' 'plugins/*/skills/**/SKILL.md' 'skills/**/SKILL.md' '.claude/skills/**/SKILL.md')
ROUTING_PATHSPEC='claude-skills/skills/plan-review/ROUTING.md'
MARKER_PATHSPECS=("${SKILL_CONTENT_PATHSPECS[@]}" "$ROUTING_PATHSPEC")

# Conflict-marker scan. The base tree holds conflict-marker blobs, so the scan is
# HEAD-relative: the base-relative diff of an unresolved file is empty.
# `-a --no-textconv` and path-only output stop attributes and diff config from
# hiding a marker line. See "Conflict-marker hard deny" in
# docs/design-decisions/skill-review-gate-disarms-on-empty-base-relative-diff.md.
CONFLICT_MARKER_REGEX='^(<<<<<<<|=======|>>>>>>>)( |$)'
if [ -n "$BASE" ]; then
  CONFLICT_MARKER_CANDIDATES=$(_lib_capped git -C "$REPO_ROOT" -c core.quotepath=false diff --cached --no-color --name-only --diff-filter=d -a --no-textconv -G "$CONFLICT_MARKER_REGEX" -- "${MARKER_PATHSPECS[@]}")
  CONFLICT_MARKER_SCAN_STATUS=$?
  CONFLICT_MARKER_PATHS=""
  if [ "$CONFLICT_MARKER_SCAN_STATUS" -eq 0 ] && [ -n "$CONFLICT_MARKER_CANDIDATES" ]; then
    # `git grep -L` exits 0 when it lists a file and 1 when it lists none.
    MARKER_FREE_PATHS=$(_lib_capped git -C "$REPO_ROOT" -c core.quotepath=false grep --cached --no-color --no-recurse-submodules -L -a --no-textconv -E "$CONFLICT_MARKER_REGEX" -- "${MARKER_PATHSPECS[@]}")
    CONFLICT_MARKER_SCAN_STATUS=$?
    [ "$CONFLICT_MARKER_SCAN_STATUS" -eq 1 ] && CONFLICT_MARKER_SCAN_STATUS=0
    if [ "$CONFLICT_MARKER_SCAN_STATUS" -eq 0 ]; then
      CONFLICT_MARKER_PATHS=$(_lib_conflict_marker_deny_paths "$CONFLICT_MARKER_CANDIDATES" "$MARKER_FREE_PATHS")
    fi
  fi
  if [ "$CONFLICT_MARKER_SCAN_STATUS" -ne 0 ]; then
    emit_deny "Commit blocked by skill-review gate: could not scan the staged skill files for conflict markers (a git diff or git grep call failed or timed out). Retry the commit; if it keeps failing, check that git is responsive in this repository."
    exit 0
  fi
  if [ -n "$CONFLICT_MARKER_PATHS" ]; then
    emit_deny "Commit blocked by skill-review gate: the staged gated file(s) still contain unresolved conflict-marker lines (${CONFLICT_MARKER_PATHS//$'\n'/, }): column-0 lines starting <<<<<<<, =======, or >>>>>>> in a file whose staged change touches such a line. If a conflict is unresolved, resolve it and stage the resolution. If the lines are legitimate content, such as a documented example, indent every such line and restage; the gate then reviews the edited file."
    exit 0
  fi
fi

# Disarm: if no SKILL.md files and no plan-review/ROUTING.md are novel
# relative to BASE, this hook is a no-op. Commits that don't touch any
# gated skill file are not gated by skill-review. ROUTING_DIFF is checked
# separately from SKILL_DIFF because the structural validator's path list
# below covers SKILL.md only -- ROUTING.md has no frontmatter and must not
# reach that validator. Both listings include deletions, and both must succeed
# AND report empty before this disarms.
SKILL_DIFF=$(gated_paths_at_base "$REPO_ROOT" "$BASE" "" "${SKILL_CONTENT_PATHSPECS[@]}")
SKILL_DIFF_STATUS=$?
ROUTING_DIFF=$(gated_paths_at_base "$REPO_ROOT" "$BASE" "" "$ROUTING_PATHSPEC")
ROUTING_DIFF_STATUS=$?
if [ "$SKILL_DIFF_STATUS" -eq 0 ] && [ "$ROUTING_DIFF_STATUS" -eq 0 ] \
  && [ -z "$SKILL_DIFF" ] && [ -z "$ROUTING_DIFF" ]; then
  # Stderr from a hook that exits 0 reaches only the Claude Code debug log
  # (`claude --debug` / `--debug-file`), so this line is a debugging aid, not a
  # durable record. Only when BASE is non-empty -- the ordinary "nothing
  # staged" exit stays silent.
  if [ -n "$BASE" ]; then
    printf 'skill-management: skill-review gate disarmed -- staged skill content is identical to the novel-content base (tree %s). See docs/design-decisions/skill-review-gate-disarms-on-empty-base-relative-diff.md in the claude-config repo.\n' "$BASE" >&2
  fi
  exit 0
fi

# A failed listing leaves STAGED_SKILL_PATHS empty, which would skip the
# structural validator and let a matching marker release the commit, so it
# denies here. It does not retry HEAD-relative: a HEAD-relative listing
# would re-include content the base exists to exclude.
NON_DELETED_SKILL_DIFF=""
NON_DELETED_SKILL_DIFF_STATUS=0
if [ "$SKILL_DIFF_STATUS" -eq 0 ] && [ "$ROUTING_DIFF_STATUS" -eq 0 ]; then
  NON_DELETED_SKILL_DIFF=$(gated_paths_at_base "$REPO_ROOT" "$BASE" "d" "${SKILL_CONTENT_PATHSPECS[@]}")
  NON_DELETED_SKILL_DIFF_STATUS=$?
fi
if [ "$SKILL_DIFF_STATUS" -ne 0 ] || [ "$ROUTING_DIFF_STATUS" -ne 0 ] || [ "$NON_DELETED_SKILL_DIFF_STATUS" -ne 0 ]; then
  emit_deny "Commit blocked by skill-review gate: could not list the staged skill files (git diff --cached --name-only failed or timed out), so the structural validator and marker check cannot run against them. Retry the commit; if it keeps failing, check that git is responsive in this repository."
  exit 0
fi

# Structural validation phase: run validate_skill_structure.py on every staged
# SKILL.md before the behavioral-review marker check. Structural violations
# (invalid YAML, over-length descriptions) are cheap and deterministic; catching
# them here prevents malformed files from reaching the behavioral-equivalence
# audit and gives a precise error message pointing at the offending file.
#
# The validator must see the staged blob content, not the working-tree file —
# otherwise a stage-broken-then-fix-locally-without-restage flow would let
# malformed YAML slip past the gate. Materialize each staged blob via
# `git show :<path>` into a tmp tree that mirrors the original repo path, so
# the validator's error messages reference recognizable paths after the tmp
# prefix is stripped below.
STAGED_SKILL_PATHS=()
while IFS= read -r STAGED_PATH; do
  [ -n "$STAGED_PATH" ] && STAGED_SKILL_PATHS+=("$STAGED_PATH")
done <<< "$NON_DELETED_SKILL_DIFF"

VALIDATOR_SCRIPT="$(dirname "$0")/../scripts/validate_skill_structure.py"
# Fallback chain so the validator finds pyyaml: the plugin's persistent venv, then claude-config's own contributor .venv, then bare system python3.
VALIDATOR_PYTHON="python3"
# Located via this hook's own on-disk path ($0), not $CWD -- $CWD is the repo the gated commit targets, which isn't necessarily claude-config itself.
# --git-common-dir (not --show-toplevel) resolves correctly for a linked worktree too, where .venv lives only at the main worktree root.
HOOK_OWN_GIT_COMMON_DIR=$(git -C "$(dirname "$0")" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
if [ -n "${CLAUDE_PLUGIN_DATA:-}" ] && [ -x "${CLAUDE_PLUGIN_DATA}/venv/bin/python" ]; then
  VALIDATOR_PYTHON="${CLAUDE_PLUGIN_DATA}/venv/bin/python"
elif [ -n "$HOOK_OWN_GIT_COMMON_DIR" ] \
  && [ -x "$(dirname "$HOOK_OWN_GIT_COMMON_DIR")/.venv/bin/python3" ]; then
  VALIDATOR_PYTHON="$(dirname "$HOOK_OWN_GIT_COMMON_DIR")/.venv/bin/python3"
fi

if [ "${#STAGED_SKILL_PATHS[@]}" -gt 0 ]; then
  STAGED_BLOB_DIR=$(mktemp -d)
  trap 'rm -rf "$STAGED_BLOB_DIR"' EXIT

  STAGED_BLOB_PATHS=()
  for STAGED_PATH in "${STAGED_SKILL_PATHS[@]}"; do
    BLOB_PATH="$STAGED_BLOB_DIR/$STAGED_PATH"
    # STAGED_SKILL_PATHS already excludes deletions by the diff's own status,
    # so every path here must be readable: any nonzero status from the bounded
    # `git show` (including a cap kill) denies rather than skipping the validator.
    UNREADABLE_DENY="Commit blocked by skill-review gate: could not read the staged content of ${STAGED_PATH}, so the structural validator cannot run against it. Causes: the path is unmerged (resolve the conflict and stage it), its name still lists quoted because it contains a quote, tab, newline, or backslash (rename it), its name does not survive argv normalization (an NFD name with core.precomposeunicode), or the index could not be read. Fix the cause, then retry the commit."
    mkdir -p "$(dirname "$BLOB_PATH")"
    if ! _lib_capped git -C "$REPO_ROOT" show ":$STAGED_PATH" > "$BLOB_PATH" 2>/dev/null; then
      emit_deny "$UNREADABLE_DENY"
      exit 0
    fi
    STAGED_BLOB_PATHS+=("$BLOB_PATH")
  done

  if [ "${#STAGED_BLOB_PATHS[@]}" -gt 0 ]; then
    # 10s caps validator latency (12s including the -k grace). This call and
    # the corpus scan below cost ~24s combined, but are only two of the
    # hook's many capped sites. Worst-case sizing for the whole hook:
    # docs/design-decisions/skill-review-gate-disarms-on-empty-base-relative-diff.md, "Latency".
    VALIDATOR_STDERR=$(_lib_capped_for 10 "$VALIDATOR_PYTHON" "$VALIDATOR_SCRIPT" "${STAGED_BLOB_PATHS[@]}" 2>&1)
    VALIDATOR_EXIT=$?
    if [ "$VALIDATOR_EXIT" -ne 0 ]; then
      # Strip the tmp-dir prefix so the user sees the original repo-relative
      # SKILL.md path in the deny reason rather than /tmp/tmp.XXXX/...
      VALIDATOR_STDERR=${VALIDATOR_STDERR//"$STAGED_BLOB_DIR/"/}
      # 124 (GNU SIGTERM), 143 (BusyBox SIGTERM), 137 (the -k grace escalated
      # to SIGKILL), and 127 get their own messages since VALIDATOR_STDERR is
      # unreliable for all of them, rather than the generic wrapped-stderr
      # deny used for real structural violations. 137 and 143 are also a
      # validator's own signal-death statuses, so that arm's text names both
      # causes. _lib_capped_for's fallback removes the missing-timeout cause of
      # 127. A validator interpreter (python3) missing from PATH still yields
      # 127, so that branch stays reachable.
      case "$VALIDATOR_EXIT" in
        124|137|143)
          emit_deny "Commit blocked by skill-management structural validator: validator killed (exit ${VALIDATOR_EXIT}) by the 10s cap or a signal."
          ;;
        127)
          emit_deny "Commit blocked by skill-management structural validator: validator command not found."
          ;;
        *)
          emit_deny "Commit blocked by skill-management structural validator: ${VALIDATOR_STDERR}"
          ;;
      esac
      exit 0
    fi
  fi
fi

# Corpus budget warning: check aggregate description+when_to_use chars across
# all model-invokable skills in this repo against the Claude Code listing budget.
# Non-blocking — exits 0 regardless; hard enforcement is in pytest/CI.
# Uses the same scoped pathspecs as SKILL_DIFF above (SKILL_CONTENT_PATHSPECS).
CORPUS_PATHS=()
while IFS= read -r CORPUS_PATH; do
  [ -n "$CORPUS_PATH" ] && CORPUS_PATHS+=("$CORPUS_PATH")
done < <(git -C "$REPO_ROOT" ls-files -- "${SKILL_CONTENT_PATHSPECS[@]}" 2>/dev/null)

# Overlay staged blobs: replace corpus path with staged blob path for any
# staged SKILL.md (so the warning reflects the post-commit state of staged files).
if [ "${#CORPUS_PATHS[@]}" -gt 0 ]; then
  OVERLAY_PATHS=()
  for CORPUS_PATH in "${CORPUS_PATHS[@]}"; do
    # Check if this path has a staged version (already materialized in STAGED_BLOB_DIR).
    BLOB_CANDIDATE="${STAGED_BLOB_DIR:-}/$CORPUS_PATH"
    if [ -n "${STAGED_BLOB_DIR:-}" ] && [ -f "$BLOB_CANDIDATE" ]; then
      OVERLAY_PATHS+=("$BLOB_CANDIDATE")
    else
      OVERLAY_PATHS+=("$CORPUS_PATH")
    fi
  done

  # No exit-status branch here (contrast the structural validator call
  # above): this scan is advisory only, so a swallowed timeout or missing
  # binary is a missed warning, not a missed gate.
  CORPUS_STDERR=$(_lib_capped_for 10 "$VALIDATOR_PYTHON" "$VALIDATOR_SCRIPT" --corpus "${OVERLAY_PATHS[@]}" 2>&1 || true)
  if [ -n "$CORPUS_STDERR" ]; then
    printf 'skill-management: corpus budget warning: %s\n' "$CORPUS_STDERR" >&2
  fi
fi

# Honor in-chain marker writes. When the same Bash call chains
# `marker.sh write skill-review` before `git commit`, the on-disk marker
# does not exist yet at PreToolUse time (the chain has not run), so the
# usual marker check below would deny. The in-chain marker.sh invocation
# is the same evidence the on-disk marker would later provide -- marker.sh
# is the only sanctioned writer in either case. The structural validator
# above still fires, so malformed SKILL.md files cannot slip through this
# bypass.
if _lib_chains_marker_write_before_commit "$COMMAND" skill-review; then
  exit 0
fi

REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
# Same argv as marker.sh's `write skill-review`/`status` arms: both call
# _lib_staged_diff_hash with the same REPO_ROOT and BASE.
# MARKER_PATHSPECS here and SKILL_REVIEW_PATHSPECS in marker.sh are separate
# literals kept in step by hand, and a test pins their equality.
# An empty CURRENT_HASH reaches the terminal deny through the "no match" path.
CURRENT_HASH=$(_lib_staged_diff_hash "$REPO_ROOT" "$BASE" "${MARKER_PATHSPECS[@]}")

# Fail closed: an unresolvable config dir must deny the gate, not silently
# skip the marker check and let the commit through.
if ! CONFIG_DIR=$(_lib_config_dir); then
  emit_deny "Blocked by skill-review gate: could not resolve the Claude Code config directory (CLAUDE_CONFIG_DIR is set to a relative path, or \$HOME is unset/empty)."
  exit 0
fi

# Allow when any marker under this repo-hash holds the currently staged skill
# diff's hash. The stored hash is the authorization — it proves a review
# covered exactly this diff — so the question is "has this diff been
# reviewed?", not "did this session review it?". An empty CURRENT_HASH
# (sha256sum unavailable) never matches, so a hashing failure denies.
if _lib_marker_value_present "$CONFIG_DIR/skill-review-markers" "$CURRENT_HASH" "$REPO_HASH."; then
  exit 0
fi

# No marker, or marker hash does not match the current staged skill state.
# Build the reason as a bash variable so the conditional notes below can be
# interpolated; jq -Rs handles JSON-encoding safely regardless of what
# characters appear in the appended note.
DENY_REASON="Commit blocked by skill-review gate: Staged skill changes have not been audited by /skill-review. Run /skill-review on the staged SKILL.md diff; the skill must produce an explicit behavioral-equivalence table for any removed or shortened lines before committing."
if [ -n "$BASE" ]; then
  DENY_REASON="${DENY_REASON} Note: this commit was gated against a novel-content base (mid-merge/cherry-pick/rebase), not the full staged diff, so a marker written by an out-of-date marker.sh will not match here. If a marker written moments ago still denies, ask the user to run 'git pull' in the claude-config checkout, then re-run /skill-review. See docs/hooks.md § \"Gate deadlock recovery\" in the claude-config repo."
elif [ "$BASE_STATUS" -eq 2 ]; then
  DENY_REASON="${DENY_REASON} Note: this commit was gated against HEAD, not a novel-content base (the base could not be computed), so a mismatch mid-merge/cherry-pick/rebase is expected here rather than a review gap."
fi
emit_deny "$DENY_REASON"
