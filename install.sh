#!/bin/bash
set -e

echo "=== claude-config Setup ==="

missing=()
for cmd in stow git gh jq sha256sum claude python3; do
  command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "Missing dependencies: ${missing[*]}"
  echo "Install them via your system package manager, then re-run."
  exit 1
fi

# pwd -P (not pwd) canonicalizes away any symlink in the invocation path, so
# REPO_DIR matches the canonicalized form the marketplace-registration check
# below compares against — otherwise a symlink-adjacent invocation of this
# script could make REPO_DIR byte-differ from the marketplace's recorded
# .path even though they name the same directory, thrashing (remove+re-add)
# the registration on every run.
REPO_DIR="$(cd "$(dirname "$0")" && pwd -P)"
cd "$REPO_DIR"
mkdir -p "$HOME/.local/bin"
# Both target directories are created before stow runs so stow links their
# contents entry by entry. Without this, stow tree-folds a target that does
# not yet exist into a single symlink pointing back into this checkout — which
# would put every file Claude Code writes at runtime inside the git clone.
mkdir -p "$HOME/.claude"

# The hook test suite extracts the lines between the two INSTALL_TEST_FIXTURE
# markers below and runs them under an isolated $HOME. Keep both markers on
# their own line, wrapping the whole block.
# INSTALL_TEST_FIXTURE: stow-adoption-migration — start
# plans/, handoffs/, briefs/ are the only stow-adopted paths the Write/Edit
# tools target from an arbitrary skill invocation (handoff/brief writes,
# plan-mode's default plan path) — migrate them off stow management first so
# a symlink resolving into this checkout can no longer collide with worktree
# enforcement, then tell stow to leave them alone going forward.
#
# Sourced from its own known repo-relative path, not ~/.claude/scripts/...,
# which doesn't exist yet at this point in a fresh install (migration runs
# before stow does). Not relocate-claude-config.sh itself — sourcing that
# would turn on set -u/pipefail for the rest of install.sh, fatal on an
# empty array under macOS system bash 3.2.
# shellcheck source=claude/.claude/scripts/_stow_migration_lib.sh
. "$REPO_DIR/claude/.claude/scripts/_stow_migration_lib.sh"

STOW_MIGRATION_FAILURES=()
for name in plans handoffs briefs; do
  if ! stow_migrate_adopted_dir "$REPO_DIR" "$name"; then
    STOW_MIGRATION_FAILURES+=("$name")
  fi
  # Repairs a prior run's per-entry adoption regardless of the migration
  # outcome above -- see stow_repair_nested_adoption's own comment.
  stow_repair_nested_adoption "$REPO_DIR" "$name"
done
if [ ${#STOW_MIGRATION_FAILURES[@]} -gt 0 ]; then
  echo "[install] warning: could not migrate the following off stow management: ${STOW_MIGRATION_FAILURES[*]} — re-run install.sh to retry; stow keeps managing them as symlinks in the meantime" >&2
fi
# INSTALL_TEST_FIXTURE: stow-adoption-migration — end

# The hook test suite extracts the lines between the two INSTALL_TEST_FIXTURE
# markers below and runs them against a real `stow` binary. Keep both markers
# on their own line, wrapping the whole block.
# INSTALL_TEST_FIXTURE: stow-adopt-ignore — start
# --ignore values are anchored Perl regexes matched against each item's path
# relative to the package root (claude/), not its basename — '^plans$' never
# matches '.claude/plans' and silently fails to protect it once '.claude'
# itself is unfolded (forced real by the mkdir -p above), so stow adopts
# every file inside individually instead of leaving the directory alone.
stow -v --adopt --ignore='^\.claude/plans$' --ignore='^\.claude/handoffs$' --ignore='^\.claude/briefs$' -t "$HOME" claude
# INSTALL_TEST_FIXTURE: stow-adopt-ignore — end

# The hook test suite extracts the lines between the two INSTALL_TEST_FIXTURE
# markers below and runs them under an isolated $HOME. Keep both markers on
# their own line, wrapping the whole block.
# INSTALL_TEST_FIXTURE: repo-relocation-manifest — start
# Record this checkout's location so relocate-claude-config can find it
# later without depending on a live ~/.claude symlink (which its own repair
# mode may need to work around). Single-line, idempotent overwrite.
printf '%s\n' "$REPO_DIR" > "$HOME/.claude-config-source"

# Real file copy (not stow) — relocate-claude-config's whole purpose is to
# keep working when the exact symlink chain it repairs has already failed,
# so it cannot itself be a stow-managed symlink into this checkout.
install -m 755 -- "$REPO_DIR/claude/.claude/scripts/relocate-claude-config.sh" "$HOME/.local/bin/relocate-claude-config"
# INSTALL_TEST_FIXTURE: repo-relocation-manifest — end

# Harden ~/.claude and ~/.claude.json against other local accounts. $HOME is
# commonly 755 (set once at account creation, not by umask), and under the
# widespread umask 0002 anything created beneath it lands group-writable —
# ~/.claude at 775. Claude Code narrows only a few paths of its own
# (.credentials.json, projects/, sessions/, ide/, daemon/), leaving
# file-history/, plans/, shell-snapshots/ and the rest at that default.
# chmod 700 on ~/.claude is a single choke point: clearing the search bit
# blocks path resolution into every subdirectory at once, so no per-directory
# recipe is needed and directories added by future releases are covered too.
#
# ~/.claude.json needs its own chmod because it sits at $HOME level, outside
# ~/.claude/ — it indexes every project directory ever opened. Current Claude
# Code releases create it 0600 and preserve its mode across their
# temp-file-plus-rename rewrites, so this is a one-time repair of files left
# at 664 by older releases, and the repair holds.
#
# chmod is skipped when ~/.claude is a symlink: chmod dereferences, so a
# tree-folded ~/.claude left by an earlier install would narrow this
# checkout's own directory rather than a private one. Failures only warn, so
# hardening never blocks the plugin registration below.
#
# The hook test suite extracts the lines between the two INSTALL_TEST_FIXTURE
# markers below and runs them under an isolated $HOME. Keep both markers on
# their own line, wrapping the whole block.
# INSTALL_TEST_FIXTURE: continuity-hardening — start
if [ -L "$HOME/.claude" ]; then
  echo "[install] warning: ~/.claude is a symlink to $(readlink "$HOME/.claude") — skipping chmod 700 so the link target is not narrowed. Claude Code is storing its state inside that path; replace the symlink with a real directory to get owner-only permissions." >&2
elif [ -d "$HOME/.claude" ]; then
  chmod 700 "$HOME/.claude" || echo "[install] warning: could not chmod 700 ~/.claude" >&2
fi
if [ -f "$HOME/.claude.json" ]; then
  chmod 600 "$HOME/.claude.json" || echo "[install] warning: could not chmod 600 ~/.claude.json" >&2
fi
# INSTALL_TEST_FIXTURE: continuity-hardening — end

# The hook test suite extracts the lines between the two INSTALL_TEST_FIXTURE
# markers below and runs them under an isolated $HOME. Keep both markers on
# their own line, wrapping the whole block.
# INSTALL_TEST_FIXTURE: machine-level-opt-ins — start
# Caller must check `[ -t 0 ]` before invoking this — it has no TTY guard of
# its own and will block on `read` forever against an open, never-closed
# stdin. configure_machine_level_opt_ins below is the only sanctioned caller.
_prompt_sentinel_opt_in() {
  local sentinel_path="$1" human_name="$2" description="$3"
  # Defense-in-depth: both current call sites hardcode a safe path, but this
  # function performs unconditional touch/rm/mkdir on its first argument —
  # confine it to $HOME/.claude so a future call site with a
  # non-hardcoded/repo-influenced path fails loudly instead of silently
  # writing wherever it's pointed.
  case "$sentinel_path" in
    "$HOME/.claude/"*) ;;
    *)
      echo "[install] internal error: _prompt_sentinel_opt_in refuses a path outside \$HOME/.claude: $sentinel_path" >&2
      return 1
      ;;
  esac
  if [ -f "$sentinel_path" ]; then
    printf '%s is currently ENABLED on this machine (%s).\n' "$human_name" "$sentinel_path"
    printf '%s\n' "$description"
    # `|| answer=""` treats read's EOF (e.g. Ctrl-D) the same as a bare
    # Enter (no-op) instead of letting set -e abort the rest of install.sh
    # — including marketplace/plugin registration below — with no diagnostic.
    read -r -p "Keep it enabled? [Y/n] " answer || answer=""
    case "$answer" in
      [Nn]*) rm -f "$sentinel_path"; echo "  → disabled: removed $sentinel_path" ;;
      *) echo "  ✓ keeping $human_name enabled" ;;
    esac
  else
    printf '%s is currently disabled on this machine.\n' "$human_name"
    printf '%s\n' "$description"
    read -r -p "Enable it now? [y/N] " answer || answer=""
    case "$answer" in
      [Yy]*) mkdir -p "$(dirname "$sentinel_path")"; touch "$sentinel_path"; echo "  → enabled: created $sentinel_path" ;;
      *) echo "  ✓ leaving $human_name disabled" ;;
    esac
  fi
}

# SENTINEL_INVENTORY (defined in the INSTALL_TEST_FIXTURE: sentinel-inventory
# block below) must already be populated by the time this runs — every
# top-level call site in this script satisfies that by ordering, since both
# blocks are defined, in file order, before this function is ever called.
# configure_machine_level_opt_ins iterates its scope=machine-promptable rows.
configure_machine_level_opt_ins() {
  if [ ! -t 0 ]; then
    echo ""
    echo "=== Machine-level opt-ins ==="
    echo "  (skipped — not an interactive terminal; existing settings are unchanged)"
    return 0
  fi
  echo ""
  echo "=== Machine-level opt-ins ==="
  SENTINEL_INVENTORY_PROMPTED_INDICES=""
  local sentinel_index=0 entry path_template scope human_name prompt_description default_state docs_anchor
  for entry in "${SENTINEL_INVENTORY[@]}"; do
    IFS='|' read -r path_template scope human_name prompt_description default_state docs_anchor <<< "$entry"
    if [ "$scope" = "machine-promptable" ]; then
      _prompt_sentinel_opt_in "$HOME/.claude/$path_template" "$human_name" "$prompt_description"
      SENTINEL_INVENTORY_PROMPTED_INDICES="$SENTINEL_INVENTORY_PROMPTED_INDICES $sentinel_index"
    fi
    sentinel_index=$((sentinel_index + 1))
  done
}
# INSTALL_TEST_FIXTURE: machine-level-opt-ins — end

# The hook test suite extracts the lines between the two INSTALL_TEST_FIXTURE
# markers below and runs them under an isolated $HOME. Keep both markers on
# their own line, wrapping the whole block.
# INSTALL_TEST_FIXTURE: sentinel-inventory — start
# Flat, pipe-delimited rows rather than an associative array: the system bash
# on macOS (and this machine's default) is 3.2, which has no `declare -A`.
# Schema (no surrounding whitespace around any `|` — IFS='|' read -r would
# otherwise bake leading/trailing spaces into every field):
#   path-template|scope|human-name|prompt-description|default-state|docs-anchor
# scope is one of: machine-promptable (offered by configure_machine_level_opt_ins
# above, path-template resolved against $HOME/.claude/), machine (report-only,
# same resolution), repo (report-only, path-template resolved against this
# repo's own root), or account (report-only, path-template resolved against
# $CLAUDE_CONFIG_DIR when set and absolute, else $HOME/.claude — never both;
# see _report_account_sentinel below). prompt-description is carried only for
# machine-promptable rows. default-state is the sentinel's own state —
# "disabled" here for every row, but what makes a row match its default-state
# differs: machine-promptable/machine/repo rows key off the file's own
# presence (a kill-switch row's human-name names the suppression itself, not
# the feature it suppresses, so presence still reads as that suppression
# being "enabled"), while the account row's state comes from the file's
# content instead — see _report_account_sentinel.
SENTINEL_INVENTORY=(
  "worktree-required|machine-promptable|Worktree enforcement|Denies git commit/push/etc. outside a linked worktree on every repo without a per-repo .claude/worktree-optout. See README 'Worktree enforcement'.|disabled|README.md § Worktree enforcement"
  "autonomous-shipping-required|machine-promptable|Autonomous shipping|Lets Claude Code commit, push, and open PRs without asking first, on every repo without a per-repo .claude/autonomous-shipping-optout. A repo cannot enable this by committing anything — only this machine-level file can. See README 'Autonomous shipping'.|disabled|README.md § Autonomous shipping"
  "track-permission-prompts|machine-promptable|Permission-prompt tracking|Logs each interactive permission-prompt Notification (credential-shaped values redacted) to ~/.claude/.permission-prompt-log.jsonl, so you can see which commands still trigger a prompt under auto permission mode. No per-repo opt-out.|disabled|docs/permission-prompt-tracking.md"
  ".error-mode-nudge-enabled|machine|Error-mode analysis nudge||disabled|docs/error-mode-nudge.md"
  ".handoff-nudge-disabled|machine|Handoff-near-cap nudge suppression||disabled|docs/handoff-nudge.md"
  ".consume-durable-continuity-disabled|machine|Durable-continuity auto-consume suppression||disabled|docs/hooks.md § Utility hooks"
  ".commit-stall-block-disabled|machine|Commit-stall auto-advance suppression||disabled|docs/commit-stall-block.md"
  ".session-title-disabled|machine|Branch-based session-title suppression (machine-wide)||disabled|docs/hooks.md § Utility hooks"
  ".claude/worktree-required|repo|Worktree enforcement (committed, this repo)||disabled|README.md § Worktree enforcement"
  ".claude/worktree-optout|repo|Worktree enforcement opt-out (this repo)||disabled|README.md § Worktree enforcement"
  ".claude/autonomous-shipping-optout|repo|Autonomous-shipping opt-out (this repo)||disabled|README.md § Autonomous shipping"
  ".claude/session-title-disabled|repo|Branch-based session-title suppression (this repo)||disabled|docs/hooks.md § Utility hooks"
  "pr-cost-disclosure|account|PR cost disclosure (this account)||disabled|README.md § PR cost disclosure"
)

# Whether $1 (a zero-based SENTINEL_INVENTORY index) was prompted by
# configure_machine_level_opt_ins during this run — report_sentinel_inventory
# uses this to suppress a redundant enable-hint for a sentinel the user was
# just asked about.
_sentinel_index_prompted_this_run() {
  case " ${SENTINEL_INVENTORY_PROMPTED_INDICES:-} " in
    *" $1 "*) return 0 ;;
    *) return 1 ;;
  esac
}

# Prints "ENABLED" when $1 exists, else $2 (the row's own default-state,
# always "disabled" per the schema comment above) — the same two labels
# _prompt_sentinel_opt_in's own prompts already use.
_sentinel_state_label() {
  if [ -f "$1" ]; then
    printf 'ENABLED'
  else
    printf '%s' "$2"
  fi
}

_report_machine_sentinel() {
  local sentinel_index="$1" path_template="$2" human_name="$3" default_state="$4" docs_anchor="$5" diverged_config_dir="$6"
  local home_path="$HOME/.claude/$path_template"
  if [ -n "$diverged_config_dir" ]; then
    local config_dir_path="$diverged_config_dir/$path_template"
    # shellcheck disable=SC2016 # single-quoted deliberately — $HOME must stay
    # unexpanded here, naming the literal env var in the diagnostic message,
    # not this run's own resolved value (already printed on the next line).
    printf '  %s: DIVERGED — CLAUDE_CONFIG_DIR and $HOME/.claude disagree\n' "$human_name"
    printf '    %s: %s\n' "$home_path" "$(_sentinel_state_label "$home_path" "$default_state")"
    printf '    %s: %s\n' "$config_dir_path" "$(_sentinel_state_label "$config_dir_path" "$default_state")"
    printf '    docs: %s\n' "$docs_anchor"
    return 0
  fi
  local state
  state="$(_sentinel_state_label "$home_path" "$default_state")"
  printf '  %s: %s (%s)\n' "$human_name" "$state" "$home_path"
  printf '    docs: %s\n' "$docs_anchor"
  if [ "$state" = "$default_state" ] && ! _sentinel_index_prompted_this_run "$sentinel_index"; then
    printf '    → to enable: touch %s\n' "$home_path"
  fi
}

_report_repo_sentinel() {
  local sentinel_index="$1" path_template="$2" human_name="$3" default_state="$4" docs_anchor="$5"
  local repo_path="$REPO_DIR/$path_template"
  local state
  state="$(_sentinel_state_label "$repo_path" "$default_state")"
  printf '  %s: %s (%s)\n' "$human_name" "$state" "$path_template"
  printf '    docs: %s\n' "$docs_anchor"
  if [ "$state" = "$default_state" ] && ! _sentinel_index_prompted_this_run "$sentinel_index"; then
    printf '    → to enable: touch %s\n' "$path_template"
  fi
}

# Content, not presence, is this row's state — see the mode grammar in
# claude/.claude/skills/pr-description/SKILL.md, whose gate this reporter
# mirrors byte-for-byte (same trim/lowercase/anchored-compare snippet, pinned
# in both places so they cannot silently diverge). Resolution, not union:
# $CLAUDE_CONFIG_DIR only when set and absolute, else $HOME/.claude — never
# both, so one account's opt-in cannot activate disclosure under another
# account's config dir.
_report_account_sentinel() {
  local sentinel_index="$1" path_template="$2" human_name="$3" docs_anchor="$4"
  local config_dir
  case "${CLAUDE_CONFIG_DIR:-}" in
    /*) config_dir="${CLAUDE_CONFIG_DIR%/}" ;;
    *) config_dir="$HOME/.claude" ;;
  esac
  local sentinel_path="$config_dir/$path_template"
  local state
  if [ ! -f "$sentinel_path" ]; then
    state="disabled"
  else
    local mode
    mode=$(cat "$sentinel_path" 2>/dev/null) || mode=""
    mode="${mode#"${mode%%[![:space:]]*}"}"
    mode="${mode%"${mode##*[![:space:]]}"}"
    mode=$(printf '%s' "$mode" | tr '[:upper:]' '[:lower:]')
    if [ "$mode" = "dollars" ]; then
      state="ENABLED (mode=dollars)"
    elif [ -z "$mode" ]; then
      state="disabled"
    else
      state="present but mode not recognized: \"$mode\" — treated as disabled"
    fi
  fi
  printf '  %s: %s (%s)\n' "$human_name" "$state" "$sentinel_path"
  # shellcheck disable=SC2016 # single-quoted deliberately — $HOME must stay
  # unexpanded here, naming the literal env var in the diagnostic message.
  printf '    the only path this scope checks — never falls back to $HOME/.claude when CLAUDE_CONFIG_DIR is set\n'
  printf '    docs: %s\n' "$docs_anchor"
  if [ ! -f "$sentinel_path" ] && ! _sentinel_index_prompted_this_run "$sentinel_index"; then
    printf '    → to enable: echo dollars > "%s"\n' "$sentinel_path"
  fi
}

# Read-only: creates and removes nothing. Called after
# configure_machine_level_opt_ins so a just-prompted row's hint can be
# suppressed. Resolves machine-scope state the way _lib_config_dir()
# (claude/.claude/hooks/_lib.sh) would: CLAUDE_CONFIG_DIR when it names a
# directory other than $HOME/.claude, else $HOME/.claude alone. When the two
# differ, both paths' state are printed and flagged as diverged rather than
# picking one — the prompt above only ever mutates $HOME/.claude, but some
# sentinel readers honor only CLAUDE_CONFIG_DIR with no fallback, so which
# copy is "the real one" genuinely depends on the specific sentinel.
report_sentinel_inventory() {
  echo ""
  echo "=== Opt-in sentinel inventory ==="
  local diverged_config_dir=""
  if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then
    local normalized_config_dir="${CLAUDE_CONFIG_DIR%/}"
    if [ "$normalized_config_dir" != "${HOME%/}/.claude" ]; then
      diverged_config_dir="$normalized_config_dir"
    fi
  fi

  local sentinel_index=0 entry path_template scope human_name prompt_description default_state docs_anchor
  for entry in "${SENTINEL_INVENTORY[@]}"; do
    IFS='|' read -r path_template scope human_name prompt_description default_state docs_anchor <<< "$entry"
    case "$scope" in
      machine-promptable | machine)
        _report_machine_sentinel "$sentinel_index" "$path_template" "$human_name" "$default_state" "$docs_anchor" "$diverged_config_dir"
        ;;
      repo)
        _report_repo_sentinel "$sentinel_index" "$path_template" "$human_name" "$default_state" "$docs_anchor"
        ;;
      account)
        _report_account_sentinel "$sentinel_index" "$path_template" "$human_name" "$docs_anchor"
        ;;
    esac
    sentinel_index=$((sentinel_index + 1))
  done
}
# INSTALL_TEST_FIXTURE: sentinel-inventory — end

configure_machine_level_opt_ins
report_sentinel_inventory

SETTINGS_FILE="$HOME/.claude/settings.json"
if [ -f "$SETTINGS_FILE" ]; then
  "$REPO_DIR/claude/.claude/scripts/register-marketplace.sh"

  echo ""
  echo "=== Installing this repo's own project-scope plugins ==="
  PROJECT_SETTINGS_FILE="$REPO_DIR/.claude/settings.json"
  if [ -f "$PROJECT_SETTINGS_FILE" ]; then
    # INSTALL_TEST_FIXTURE: project-plugin-match — start
    # Whether $1 (a "name@marketplace" plugin id) is already installed at
    # project scope for $REPO_DIR, given $2 as a "$id\t$projectPath" TSV of
    # existing scope=="project" entries. Canonicalizes each entry's path
    # the same way ensure_local_bin_on_path's readlink -f fallback does, not
    # the `cmd1 || cmd2`-inside-`$()` form used above for marketplace
    # registration — that form can concatenate BSD readlink's partial
    # stdout with the fallback on a dangling target.
    _project_plugin_already_installed() {
      local plugin_id="$1" existing_tsv="$2" entry_id entry_path entry_path_real
      while IFS=$'\t' read -r entry_id entry_path; do
        [ -z "$entry_id" ] && continue
        [ "$entry_id" != "$plugin_id" ] && continue
        if ! entry_path_real="$(readlink -f -- "$entry_path" 2>/dev/null)"; then
          entry_path_real="$entry_path"
        fi
        [ "$entry_path_real" = "$REPO_DIR" ] && return 0
      done <<< "$existing_tsv"
      return 1
    }
    # INSTALL_TEST_FIXTURE: project-plugin-match — end

    # INSTALL_TEST_FIXTURE: project-scope-plugin-install — start
    if ! existing_project_plugins="$(claude plugin list --json 2>/dev/null | jq -r '.[] | select(.scope == "project") | "\(.id)\t\(.projectPath)"')"; then
      echo "[install] warning: could not read installed project-scope plugins via 'claude plugin list --json' — proceeding as if none are installed" >&2
      existing_project_plugins=""
    fi
    if ! enabled_project_plugins="$(jq -r '.enabledPlugins // {} | to_entries[] | select(.value == true) | .key' "$PROJECT_SETTINGS_FILE")"; then
      echo "[install] warning: could not parse enabledPlugins from $PROJECT_SETTINGS_FILE — skipping project-scope plugin install" >&2
      enabled_project_plugins=""
    fi
    while read -r plugin; do
      [ -z "$plugin" ] && continue
      if _project_plugin_already_installed "$plugin" "$existing_project_plugins"; then
        echo "  ✓ $plugin (already installed)"
      else
        echo "  → installing $plugin"
        claude plugin install "$plugin" -s project || \
          echo "[install] warning: failed to install $plugin at project scope" >&2
      fi
    done <<< "$enabled_project_plugins"
    # INSTALL_TEST_FIXTURE: project-scope-plugin-install — end
  fi
fi

check_private_projects_file() {
  local file="$HOME/.claude/private-projects.md"
  if [ ! -e "$file" ]; then
    echo ""
    echo "TIP: Create ~/.claude/private-projects.md and add \"@private-projects.md\""
    echo "     to ~/.claude/CLAUDE.md to enable redaction of project names you don't"
    echo "     want leaking in commits/PRs. See README section 'Private-project redaction'."
  elif ! grep -Evq '^[[:space:]]*(#|$)' "$file" 2>/dev/null; then
    echo ""
    echo "WARNING: ~/.claude/private-projects.md exists but contains no usable entries"
    echo "         (only comments or blank lines). Either populate it or delete it —"
    echo "         an empty file is the confusing state."
  fi
}

# The hook test suite extracts the lines between the two INSTALL_TEST_FIXTURE
# markers below and runs them under an isolated $HOME. Keep both markers on
# their own line, wrapping the whole block.
# INSTALL_TEST_FIXTURE: transcript-config-dirs — start
# declared_transcript_roots() (_config_dir.py) always reads
# ~/.claude/transcript-config-dirs, never a CLAUDE_CONFIG_DIR-relative path —
# a profile whose CLAUDE_CONFIG_DIR diverges from ~/.claude has no other way
# to declare itself there.
check_transcript_config_dirs() {
  local _resolved_config_dir _resolved_default_dir
  _resolved_config_dir="$(cd "${CLAUDE_CONFIG_DIR:-$HOME/.claude}" 2>/dev/null && pwd -P)" || _resolved_config_dir=""
  _resolved_default_dir="$(cd "$HOME/.claude" 2>/dev/null && pwd -P)" || _resolved_default_dir=""
  if [ -n "$_resolved_config_dir" ] && [ "$_resolved_config_dir" != "$_resolved_default_dir" ]; then
    echo ""
    echo "TIP: this profile's CLAUDE_CONFIG_DIR ($_resolved_config_dir) differs from"
    echo "     the default ~/.claude ($_resolved_default_dir). transcript-analysis.py's"
    echo "     multi-account corpus union reads declared roots from"
    echo "     ~/.claude/transcript-config-dirs only, never from this profile's own"
    echo "     config dir — add this profile there, from the default profile, to"
    echo "     include it in the union. See docs/transcript-analysis.md's 'Corpus"
    echo "     scope: the declared-roots file' section."
  fi

  if [ -L "$HOME/.claude" ]; then
    echo ""
    echo "TIP: ~/.claude is a symlink to $(readlink "$HOME/.claude") — creating"
    echo "     ~/.claude/transcript-config-dirs writes into that resolved location,"
    echo "     not a separate directory."
  fi

  local roots_file="$HOME/.claude/transcript-config-dirs"
  if [ -e "$roots_file" ] && ! grep -Evq '^[[:space:]]*(#|$)' "$roots_file" 2>/dev/null; then
    echo ""
    echo "WARNING: ~/.claude/transcript-config-dirs exists but contains no usable"
    echo "         entries (only comments or blank lines). Either populate it or"
    echo "         delete it — an empty file is the confusing state."
  fi
}
# INSTALL_TEST_FIXTURE: transcript-config-dirs — end

# The hook test suite extracts the lines between the two INSTALL_TEST_FIXTURE
# markers below and runs them under an isolated $HOME. Keep both markers on
# their own line, wrapping the whole block.
# INSTALL_TEST_FIXTURE: local-bin-path — start
# Whether $1 already has a non-comment line mentioning $2 — excludes
# comment-only lines (same reason check_private_projects_file above excludes
# comment/blank lines) so a stale "# TODO: add ~/.local/bin" note doesn't
# false-positive as already-configured.
_file_has_active_reference() {
  local file="$1" needle="$2"
  [ -f "$file" ] && grep -v '^[[:space:]]*#' -- "$file" 2>/dev/null | grep -Fq -- "$needle"
}

# Shared by every failure branch below: undoes the just-written append,
# preferring the pre-append backup over a bare rm so a first-run failure
# (no backup exists yet) doesn't leave a half-written rc file behind either.
_undo_local_bin_append() {
  local rc_file="$1" backup="$2" message="$3"
  if [ -n "$backup" ]; then
    mv -- "$backup" "$rc_file"
    echo "[install] warning: $message; restored from backup." >&2
  else
    rm -f -- "$rc_file"
    echo "[install] warning: $message; removed the file." >&2
  fi
}

ensure_local_bin_on_path() {
  # shellcheck disable=SC2016 # single-quoted deliberately — $HOME must stay
  # unexpanded here so it is evaluated when the rc file is later sourced, not
  # when install.sh runs.
  local export_line='export PATH="$HOME/.local/bin:$PATH"'
  local shell_name rc_file resolved companion backup

  for shell_name in zsh bash; do
    rc_file="$HOME/.${shell_name}rc"

    if [ -L "$rc_file" ]; then
      # BSD readlink -f (macOS) can print a partial path to stdout AND exit
      # non-zero for a dangling symlink, unlike GNU readlink — check the exit
      # status of the assignment itself rather than trusting a non-empty
      # capture, so a dangling link falls back to the symlink's own path
      # instead of keeping BSD's partial garbage.
      if ! resolved="$(readlink -f -- "$rc_file" 2>/dev/null)"; then
        resolved="$rc_file"
      fi
      companion="${rc_file}.local"
      # Direct-match checked before the companion heuristic: a resolved
      # target that already has ~/.local/bin needs nothing further,
      # regardless of whether it also happens to mention the companion's
      # basename (e.g. in a comment).
      if _file_has_active_reference "$resolved" '.local/bin'; then
        echo "  ✓ $HOME/.${shell_name}rc already has ~/.local/bin on PATH (via $resolved)"
        continue
      elif [ ! -L "$companion" ] && _file_has_active_reference "$resolved" "$(basename "$companion")"; then
        rc_file="$companion"
        echo "  → managing ~/.local/bin PATH setup in $companion (sourced by $HOME/.${shell_name}rc)"
      else
        echo "[install] warning: $HOME/.${shell_name}rc is a symlink to $resolved — not writing PATH setup through it. Add '$export_line' to whichever file manages your $shell_name startup, then restart your shell." >&2
        continue
      fi
    fi

    if _file_has_active_reference "$rc_file" '.local/bin'; then
      echo "  ✓ $rc_file already has ~/.local/bin on PATH"
      continue
    fi

    command -v "$shell_name" >/dev/null 2>&1 || continue

    backup=""
    if [ -f "$rc_file" ]; then
      backup="${rc_file}.bak.$(date +%Y%m%d%H%M%S)"
      if ! cp -- "$rc_file" "$backup"; then
        echo "[install] warning: could not back up $rc_file; skipping PATH setup for it" >&2
        continue
      fi
    fi

    # Negating a redirected `{ }` group directly (`if ! { ...; } >> file`)
    # does not propagate the group's own redirect-open failure on bash —
    # verified on both macOS system bash 3.2 and bash 5.3. Using the
    # un-negated group as the if-condition itself detects the failure
    # correctly and is still exempt from `set -e` as an if-condition.
    if {
      printf '\n# BEGIN claude-config: ensure ~/.local/bin on PATH\n'
      printf '%s\n' "$export_line"
      printf '# END claude-config: ensure ~/.local/bin on PATH\n'
    } >> "$rc_file"; then
      :
    else
      _undo_local_bin_append "$rc_file" "$backup" "could not append to $rc_file"
      continue
    fi

    if ! "$shell_name" -n "$rc_file" 2>/dev/null; then
      _undo_local_bin_append "$rc_file" "$backup" "appending to $rc_file produced invalid $shell_name syntax"
      continue
    fi
    if [ -n "$backup" ]; then
      rm -f -- "$backup"
    fi
  done
}
# INSTALL_TEST_FIXTURE: local-bin-path — end

if ! command -v timeout >/dev/null 2>&1; then
  # shellcheck disable=SC2016 # single-quoted for literal display text — the
  # backtick-quoted tokens are markdown-style formatting, not command
  # substitution; there is no shell expansion intended in either message.
  printf '[install] warning: GNU coreutils `timeout` not in PATH; guard hooks will run jq and git checks (e.g. the agent-reviews/ ignore-state check) without timeout protection.\n' >&2
  # shellcheck disable=SC2016 # single-quoted for literal display text — the
  # backtick-quoted tokens are markdown-style formatting, not command
  # substitution; there is no shell expansion intended in this message.
  printf '[install] hint: install via `brew install coreutils` (macOS) or `apt install coreutils` (debian). On macOS, coreutils installs `gtimeout` by default — either re-run with `--with-default-names` (older brew) or symlink `gtimeout` to `timeout` in PATH.\n' >&2
fi

check_private_projects_file
check_transcript_config_dirs
ensure_local_bin_on_path

if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  echo ""
  echo "NOTE: this python3 lacks ensurepip, so 'python3 -m venv' produces a venv with no pip."
  echo "      On Debian/Ubuntu, install it first: sudo apt install python3.12-venv"
fi

echo ""
echo "Done. Optional (contributors): run the hook test suite:"
echo "  ./install-dev.sh   # creates .venv from requirements-dev.txt"
echo "  .venv/bin/pytest claude/.claude/"
