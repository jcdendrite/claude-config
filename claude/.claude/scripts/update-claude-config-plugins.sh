#!/usr/bin/env bash
# update-claude-config-plugins.sh — diff and interactively update @claude-config plugins.
#
# Refreshes the claude-config marketplace, identifies installed plugins whose
# version lags the marketplace's latest, and offers to update them one by one.
# Scope is limited to @claude-config plugins: they carry real semver in plugin.json
# (enforced by the plugin-semver plugin), making the version comparison clean.
# Other marketplaces use sha-pinned refs with no comparable semver — out of scope.
#
# Run this script from the root of the consumer repo whose plugins you want to
# update. Project-scope plugin entries are filtered to those installed in the
# current project root; user-scope entries are shown regardless of cwd. Pass
# --all-projects to sweep project-scope entries across every repo on the
# machine instead — updates are applied by cd-ing into each entry's own
# project root, since the claude CLI has no cwd-override flag.
#
# Usage:
#   update-claude-config-plugins.sh
#   update-claude-config-plugins.sh --dry-run
#   update-claude-config-plugins.sh --yes
#   update-claude-config-plugins.sh --dry-run --yes
#   update-claude-config-plugins.sh --all-projects --yes
#
# Exit codes:
#   0  success (including no-op)
#   1  prerequisite error (marketplace not configured, CLI unavailable);
#      also set post-hoc if any --all-projects update fails or is skipped
#   2  bad arguments

set -euo pipefail

# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------

usage() {
  echo "Usage: $(basename "$0") [--dry-run] [--yes] [--all-projects]" >&2
}

DRY_RUN=0
ASSUME_YES=0
ALL_PROJECTS=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)      DRY_RUN=1 ;;
    --yes)          ASSUME_YES=1 ;;
    --all-projects) ALL_PROJECTS=1 ;;
    *)              usage; exit 2 ;;
  esac
  shift
done

# ---------------------------------------------------------------------------
# Project root — used to filter project-scope entries to the current repo
# ---------------------------------------------------------------------------

PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)

# ---------------------------------------------------------------------------
# Step 1: Verify claude-config marketplace is configured; capture installLocation
# ---------------------------------------------------------------------------
# Done before the refresh so a missing marketplace reports cleanly rather than
# as a failed marketplace-update call.

MARKETPLACE_LIST_JSON=$(claude plugin marketplace list --json 2>/dev/null) || {
  echo "ERROR: 'claude plugin marketplace list --json' failed. Is the claude CLI available?" >&2
  exit 1
}

INSTALL_LOCATION=$(echo "$MARKETPLACE_LIST_JSON" | python3 -c "
import json, sys
entries = json.load(sys.stdin)
for e in entries:
    if e.get('name') == 'claude-config':
        loc = e.get('installLocation', '')
        if loc:
            print(loc)
        sys.exit(0)
" 2>/dev/null || true)

if [ -z "$INSTALL_LOCATION" ]; then
  echo "ERROR: The claude-config marketplace is not configured." >&2
  echo "Add it first: claude plugin marketplace add <source> --name claude-config" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Step 2: Refresh the marketplace so installLocation reflects the latest
# ---------------------------------------------------------------------------

echo "Refreshing claude-config marketplace..." >&2
claude plugin marketplace update claude-config >/dev/null 2>&1 || {
  echo "WARNING: marketplace refresh failed; version data may be stale." >&2
}

# ---------------------------------------------------------------------------
# Step 3: Build latest-version map from the marketplace's plugin.json files
# ---------------------------------------------------------------------------

MARKETPLACE_MANIFEST="${INSTALL_LOCATION}/.claude-plugin/marketplace.json"

if [ ! -f "$MARKETPLACE_MANIFEST" ]; then
  echo "ERROR: marketplace manifest not found at ${MARKETPLACE_MANIFEST}" >&2
  exit 1
fi

declare -a PLUGIN_NAMES=()
declare -a PLUGIN_LATEST=()
declare -a PLUGIN_DESC=()

while read -r plugin_name plugin_source_rel; do
  [ -z "$plugin_name" ] && continue
  plugin_json="${INSTALL_LOCATION}/${plugin_source_rel}/.claude-plugin/plugin.json"
  if [ ! -f "$plugin_json" ]; then
    continue
  fi
  parsed=$(python3 -c "
import json, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
version = d.get('version', '')
desc = d.get('description', '').replace('\n', ' ')
print(version)
print(desc)
" "$plugin_json" 2>/dev/null || true)
  plugin_version=$(printf '%s' "$parsed" | sed -n '1p')
  plugin_desc=$(printf '%s' "$parsed" | sed -n '2p')
  if [ -n "$plugin_version" ]; then
    PLUGIN_NAMES+=("$plugin_name")
    PLUGIN_LATEST+=("$plugin_version")
    PLUGIN_DESC+=("$plugin_desc")
  fi
done < <(python3 -c "
import json, re, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
for p in d.get('plugins', []):
    name = p.get('name', '')
    source = p.get('source', '')
    # Normalize ./plugins/foo or /plugins/foo to plugins/foo
    source = re.sub(r'^\.\/', '', source).lstrip('/')
    if name and source:
        print(name, source)
" "$MARKETPLACE_MANIFEST" 2>/dev/null || true)

if [ "${#PLUGIN_NAMES[@]}" -eq 0 ]; then
  echo "No plugins found in the claude-config marketplace manifest." >&2
  exit 0
fi

# ---------------------------------------------------------------------------
# Step 4: Read installed @claude-config plugins, filtering by project root
# ---------------------------------------------------------------------------

INSTALLED_LIST_JSON=$(claude plugin list --json 2>/dev/null) || {
  echo "ERROR: 'claude plugin list --json' failed." >&2
  exit 1
}

declare -a OUTDATED_NAMES=()
declare -a OUTDATED_INSTALLED_VERSIONS=()
declare -a OUTDATED_LATEST_VERSIONS=()
declare -a OUTDATED_SCOPES=()
declare -a OUTDATED_PROJECT_PATHS=()

while IFS=$'\t' read -r entry_name installed_version entry_scope entry_project_path; do
  [ -z "$entry_name" ] && continue

  if [ "$ALL_PROJECTS" -eq 0 ] && [ "$entry_scope" = "project" ] && [ "$entry_project_path" != "$PROJECT_ROOT" ]; then
    continue
  fi

  latest_version=""
  for _pi in "${!PLUGIN_NAMES[@]}"; do
    if [ "${PLUGIN_NAMES[$_pi]}" = "$entry_name" ]; then
      latest_version="${PLUGIN_LATEST[$_pi]}"
      break
    fi
  done
  if [ -z "$latest_version" ]; then
    continue
  fi

  if [ "$installed_version" = "$latest_version" ]; then
    continue
  fi

  # Flag as outdated only when marketplace version sorts strictly higher.
  # A locally-ahead dev copy (installed > latest) is silently skipped.
  lower=$(python3 -c "
import sys
a = tuple(int(x) for x in sys.argv[1].split('.') if x.isdigit())
b = tuple(int(x) for x in sys.argv[2].split('.') if x.isdigit())
print(sys.argv[1] if a <= b else sys.argv[2])
" "$installed_version" "$latest_version")
  if [ "$lower" = "$installed_version" ]; then
    OUTDATED_NAMES+=("$entry_name")
    OUTDATED_INSTALLED_VERSIONS+=("$installed_version")
    OUTDATED_LATEST_VERSIONS+=("$latest_version")
    OUTDATED_SCOPES+=("$entry_scope")
    OUTDATED_PROJECT_PATHS+=("$entry_project_path")
  fi

done < <(echo "$INSTALLED_LIST_JSON" | python3 -c "
import json, sys
entries = json.load(sys.stdin)
for e in entries:
    plugin_id = e.get('id', '')
    if not plugin_id.endswith('@claude-config'):
        continue
    name = plugin_id.rsplit('@', 1)[0]
    version = e.get('version', '')
    scope = e.get('scope', '')
    project_path = e.get('projectPath', '')
    print(f'{name}\t{version}\t{scope}\t{project_path}')
" 2>/dev/null || true)

# ---------------------------------------------------------------------------
# Early exit when nothing is outdated
# ---------------------------------------------------------------------------

if [ "${#OUTDATED_NAMES[@]}" -eq 0 ]; then
  any_installed=$(echo "$INSTALLED_LIST_JSON" | python3 -c "
import json, sys
entries = json.load(sys.stdin)
print(sum(1 for e in entries if e.get('id','').endswith('@claude-config')))
" 2>/dev/null || echo 0)
  if [ "$any_installed" -eq 0 ]; then
    echo "No @claude-config plugins are installed in this project."
  else
    echo "All @claude-config plugins are up to date."
  fi
  exit 0
fi

# ---------------------------------------------------------------------------
# Report outdated plugins
# ---------------------------------------------------------------------------

echo "Outdated @claude-config plugins:"
for i in "${!OUTDATED_NAMES[@]}"; do
  plugin_name="${OUTDATED_NAMES[$i]}"
  entry_scope="${OUTDATED_SCOPES[$i]}"
  entry_project_path="${OUTDATED_PROJECT_PATHS[$i]}"
  installed_ver="${OUTDATED_INSTALLED_VERSIONS[$i]}"
  latest_ver="${OUTDATED_LATEST_VERSIONS[$i]}"
  description=""
  for _pi in "${!PLUGIN_NAMES[@]}"; do
    if [ "${PLUGIN_NAMES[$_pi]}" = "$plugin_name" ]; then
      description="${PLUGIN_DESC[$_pi]}"
      break
    fi
  done

  if [ "$entry_scope" = "project" ]; then
    echo "  ${plugin_name}  ${installed_ver} → ${latest_ver}  (scope: project, path: ${entry_project_path})"
  else
    echo "  ${plugin_name}  ${installed_ver} → ${latest_ver}  (scope: ${entry_scope})"
  fi
  if [ -n "$description" ]; then
    echo "    ${description}"
  fi
done

# ---------------------------------------------------------------------------
# Dry-run stops here
# ---------------------------------------------------------------------------

if [ "$DRY_RUN" -eq 1 ]; then
  exit 0
fi

# ---------------------------------------------------------------------------
# Interactive update loop
# ---------------------------------------------------------------------------

if [ ! -t 0 ] && [ "$ASSUME_YES" -eq 0 ]; then
  printf 'Skipped %d update(s) (no TTY for prompt; rerun with --yes or from a terminal)\n' \
    "${#OUTDATED_NAMES[@]}"
  exit 0
fi

UPDATED_COUNT=0
FAILED_COUNT=0

for i in "${!OUTDATED_NAMES[@]}"; do
  plugin_name="${OUTDATED_NAMES[$i]}"
  entry_scope="${OUTDATED_SCOPES[$i]}"
  entry_project_path="${OUTDATED_PROJECT_PATHS[$i]}"
  installed_ver="${OUTDATED_INSTALLED_VERSIONS[$i]}"
  latest_ver="${OUTDATED_LATEST_VERSIONS[$i]}"

  if [ "$entry_scope" = "project" ]; then
    scope_label="project, path: ${entry_project_path}"
  else
    scope_label="$entry_scope"
  fi

  if [ "$ASSUME_YES" -eq 1 ]; then
    DO_UPDATE=1
  else
    printf "Update %s (%s → %s, scope: %s)? [y/N]: " \
      "$plugin_name" "$installed_ver" "$latest_ver" "$scope_label"
    read -r REPLY
    if [[ "$REPLY" == "y" || "$REPLY" == "Y" ]]; then
      DO_UPDATE=1
    else
      DO_UPDATE=0
    fi
  fi

  if [ "$DO_UPDATE" -eq 1 ]; then
    if [ "$ALL_PROJECTS" -eq 1 ] && [ "$entry_scope" = "project" ]; then
      if [ ! -d "$entry_project_path" ]; then
        echo "WARNING: project path no longer exists, skipping ${plugin_name}: ${entry_project_path}" >&2
        FAILED_COUNT=$(( FAILED_COUNT + 1 ))
        continue
      fi
      if ! (cd "$entry_project_path" && claude plugin update "${plugin_name}@claude-config" --scope project); then
        echo "WARNING: update failed for ${plugin_name} in ${entry_project_path}" >&2
        FAILED_COUNT=$(( FAILED_COUNT + 1 ))
        continue
      fi
    else
      claude plugin update "${plugin_name}@claude-config" --scope "$entry_scope"
    fi
    UPDATED_COUNT=$(( UPDATED_COUNT + 1 ))
  fi
done

# ---------------------------------------------------------------------------
# Closing note
# ---------------------------------------------------------------------------

if [ "$UPDATED_COUNT" -gt 0 ]; then
  echo "Restart Claude Code for the updated plugins to take effect."
fi

if [ "$FAILED_COUNT" -gt 0 ]; then
  exit 1
fi
