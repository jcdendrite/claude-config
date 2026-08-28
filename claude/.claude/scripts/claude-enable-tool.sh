#!/usr/bin/env bash
# claude-enable-tool.sh <artifact|workflow> — launch Claude Code with one
# tool re-enabled for this session, overriding the repo-wide disable default.
#
#   claude-enable-tool.sh artifact                # Artifact enabled, Workflow still off
#   claude-enable-tool.sh workflow "draft a plan"  # Workflow enabled, prompt passes through
#
# CLI scope is the only scope that outranks the User-scope default a stow
# consumer inherits from claude/.claude/settings.json — docs/design-decisions.md.
# A caller-supplied --settings would have to be merged with this script's own
# JSON by hand, so it is refused rather than silently overridden or combined.
set -euo pipefail

tool="${1:?usage: claude-enable-tool.sh <artifact|workflow> [claude args...]}"
case "$tool" in
  artifact) settings_json='{"disableArtifact": false}' ;;
  workflow) settings_json='{"disableWorkflows": false}' ;;
  *)
    echo "claude-enable-tool.sh: unknown tool '$tool' (expected artifact or workflow)" >&2
    exit 1
    ;;
esac
shift

# Scanning stops at a literal `--`, since everything after it is positional
# text rather than a flag this script should inspect.
for arg in "$@"; do
  case "$arg" in
    --) break ;;
    --settings | --settings=*)
      echo "claude-enable-tool.sh: refusing to launch — a caller-supplied --settings can't be merged automatically. Run the merged form by hand instead: claude --settings '$settings_json' ..." >&2
      exit 1
      ;;
  esac
done

exec claude --settings "$settings_json" "$@"
