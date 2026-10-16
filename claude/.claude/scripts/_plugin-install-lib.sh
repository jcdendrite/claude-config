# shellcheck shell=bash
# Sourced by install.sh and install-dev.sh.

# Whether $1 (a "name@marketplace" plugin id) is already installed at $3,
# given $2 as a "$id\t$projectPath" TSV of existing entries at that scope.
# Canonicalizes each entry's path with `readlink -f`.
# Checks the assignment's own exit status rather than `cmd1 || cmd2` inside
# `$()`, because BSD readlink can emit partial stdout on a dangling target
# and the fallback would concatenate onto it.
_plugin_installed_at_path() {
  local plugin_id="$1" existing_tsv="$2" dir="$3" entry_id entry_path entry_path_real
  while IFS=$'\t' read -r entry_id entry_path; do
    [ -z "$entry_id" ] && continue
    [ "$entry_id" != "$plugin_id" ] && continue
    if ! entry_path_real="$(readlink -f -- "$entry_path" 2>/dev/null)"; then
      entry_path_real="$entry_path"
    fi
    [ "$entry_path_real" = "$dir" ] && return 0
  done <<< "$existing_tsv"
  return 1
}
