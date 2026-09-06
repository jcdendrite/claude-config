#!/usr/bin/env bash
# Verifies that a nested context: fork resolves to the same session and
# process ancestry as its outer fork — the property marker.sh's ancestor
# walk (claude/.claude/hooks/_lib.sh's _lib_resolve_claude_pid) depends on
# for every forked skill in this repo. Re-run after any Claude Code version
# bump that could change fork process topology.
#
# Materializes two throwaway project-scope skills under .claude/skills/:
# outer-fork-topology-probe (context: fork, background: false) invokes
# inner-fork-topology-probe (same frontmatter) by name. Both print their
# process-ancestor chain and which ancestor, if any, owns a
# ${CLAUDE_CONFIG_DIR:-$HOME/.claude}/sessions/<pid> file.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

SKILLS_DIR=".claude/skills"
OUTER_DIR="$SKILLS_DIR/outer-fork-topology-probe"
INNER_DIR="$SKILLS_DIR/inner-fork-topology-probe"

cleanup() {
  rm -rf "$OUTER_DIR" "$INNER_DIR"
}
trap cleanup EXIT

mkdir -p "$OUTER_DIR" "$INNER_DIR"

cat > "$OUTER_DIR/SKILL.md" <<'SKILL_EOF'
---
name: outer-fork-topology-probe
description: Throwaway probe — do not trigger. Verifies nested context: fork process topology ahead of a frontmatter change; materialized and removed by scripts/dev/fork-topology-probe.sh.
context: fork
background: false
allowed-tools: Bash, Skill
---

Run the report below verbatim, then invoke the `inner-fork-topology-probe` skill by name, then report both outputs back to the user verbatim — do not summarize or interpret either one.

```bash
echo "=== outer fork ==="
config_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
pid=$$
while [[ -n "$pid" && "$pid" != "0" && "$pid" != "1" ]]; do
  comm=$(ps -o comm= -p "$pid" 2>/dev/null || echo "?")
  owns_session_file="no"
  [[ -r "$config_dir/sessions/$pid" ]] && owns_session_file="yes ($config_dir/sessions/$pid)"
  echo "  pid=$pid comm=$comm owns_session_file=$owns_session_file"
  pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
done
```
SKILL_EOF

cat > "$INNER_DIR/SKILL.md" <<'SKILL_EOF'
---
name: inner-fork-topology-probe
description: Throwaway probe — do not trigger. Nested half of outer-fork-topology-probe; materialized and removed by scripts/dev/fork-topology-probe.sh.
context: fork
background: false
allowed-tools: Bash
---

Run the report below verbatim and return its output — do not summarize or interpret it.

```bash
echo "=== inner fork ==="
config_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
pid=$$
while [[ -n "$pid" && "$pid" != "0" && "$pid" != "1" ]]; do
  comm=$(ps -o comm= -p "$pid" 2>/dev/null || echo "?")
  owns_session_file="no"
  [[ -r "$config_dir/sessions/$pid" ]] && owns_session_file="yes ($config_dir/sessions/$pid)"
  echo "  pid=$pid comm=$comm owns_session_file=$owns_session_file"
  pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
done
```
SKILL_EOF

echo "Probe skills materialized at $OUTER_DIR and $INNER_DIR."
echo "In an interactive Claude Code session anchored in this repo, invoke:"
echo "  /outer-fork-topology-probe"
echo "Confirm the inner fork's report returns to the outer fork, and both"
echo "chains resolve to the same live Claude main-process PID."
read -r -p "Press Enter once both reports have been read, to clean up: " _
