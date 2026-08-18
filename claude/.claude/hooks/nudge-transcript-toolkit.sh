#!/bin/bash
# hook-class: informational
# PostToolUse Write|Edit|MultiEdit hook: when the content just written to a
# .py/.sh file looks like a hand-rolled transcript-corpus glob
# (`projects/*/*.jsonl`-shaped), name `transcript-analysis.py` via
# `additionalContext` — it already unions across every config root declared
# in `~/.claude/transcript-config-dirs` by default, which a one-off glob
# almost never does. See `.claude/plans/transcript-parsing-guardrails.md`
# for the incident this backstops.
#
# The harness does not support `additionalContext` on `PreToolUse` at all,
# so this fires one step later than ideal: after the write, before the
# script runs. Never denies; a miss costs nothing and a false positive
# costs one line of context.
#
# Deliberately no `Bash` arm: it would fire on commands that only discuss
# the pattern (`grep -rn 'projects/\*/\*\.jsonl' docs/`) and on single-file
# reads unrelated to the multi-root globbing that caused the incident.
#
# Content extraction, by tool (post-write state only, no pre-state needed
# since this never reconstructs a diff, only inspects what was just
# written):
#   Write     tool_input.content verbatim.
#   Edit      tool_input.new_string alone.
#   MultiEdit tool_input.edits[0].new_string, but ONLY when there is exactly
#             one entry — a glob split across multiple edits[] entries is a
#             documented residual (see below), not reconstructed.
#
# Suppression is path-anchored to this toolkit's own tree, in both shapes a
# contributor can be editing it from:
#   - repo-source shape: the path contains `claude/.claude/scripts/`,
#     `claude/.claude/hooks/`, or `claude/.claude/tests/` as a path segment
#     (worktree-nested or a plain clone) — the two-segment `claude/.claude`
#     marker is this repo's own stow-package layout, not a generic
#     `scripts/` name, so it does not suppress an unrelated project's own
#     `scripts/` directory.
#   - stowed shape: the path sits under the resolved config dir's own
#     `scripts/`, `hooks/`, or `tests/` — tied to `_lib_config_dir`'s
#     resolution (not a bare `.claude/scripts/` substring), so a different
#     project's own `.claude/scripts/` is not suppressed either.
#
# Documented residuals, each pinned by a regression test rather than solved:
# - Path components assembled via `os.path.join(...)` (or equivalent):
#   "projects", "*", "*.jsonl" never appear as one contiguous literal
#   substring, so no single regex over the written text can catch it.
# - The glob shape built from variables/f-string interpolation rather than
#   written as a literal path segment in this edit's own text.
# - A MultiEdit that splits the glob shape across two or more `edits[]`
#   entries — only a single-entry MultiEdit is inspected.
# - Any extension other than `.py`/`.sh` — deliberate: markdown discussing
#   the pattern (13 files in this repo do) must never fire, and gating on
#   extension is simpler and safer than trying to detect discussion-vs-code.
#
# Defense-in-depth: filters tool_name and file_path itself; does not rely
# solely on the settings.json matcher condition.
set -uo pipefail

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  exit 0
fi

INPUT=$(cat) || exit 0
[ -n "$INPUT" ] || exit 0

TOOL_NAME=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_name // empty' 2>/dev/null) || exit 0
case "$TOOL_NAME" in
  Write | Edit | MultiEdit) ;;
  *) exit 0 ;;
esac

FILE_PATH=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_input.file_path // empty' 2>/dev/null) || exit 0
[ -n "$FILE_PATH" ] || exit 0

case "$FILE_PATH" in
  *.py | *.sh) ;;
  *) exit 0 ;;
esac

# Repo-source shape: this toolkit's own tree, worktree-nested or a plain clone.
case "$FILE_PATH" in
  claude/.claude/scripts/* | */claude/.claude/scripts/* | \
    claude/.claude/hooks/* | */claude/.claude/hooks/* | \
    claude/.claude/tests/* | */claude/.claude/tests/*)
    exit 0
    ;;
esac

# Stowed shape: under the resolved config dir's own scripts/hooks/tests —
# tied to _lib_config_dir, not a bare ".claude/scripts/" substring, so an
# unrelated project's own .claude/scripts/ isn't suppressed too.
if CONFIG_DIR=$(_lib_config_dir); then
  case "$FILE_PATH" in
    "$CONFIG_DIR"/scripts/* | "$CONFIG_DIR"/hooks/* | "$CONFIG_DIR"/tests/*)
      exit 0
      ;;
  esac
fi

case "$TOOL_NAME" in
  Write)
    CONTENT=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_input.content // empty' 2>/dev/null)
    ;;
  Edit)
    CONTENT=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_input.new_string // empty' 2>/dev/null)
    ;;
  MultiEdit)
    # Two calls, not consolidated into _lib_parse_tool_input_or_deny's
    # single-delimited-call shape: edits[] length gates which field to read
    # next, so the fields aren't independent the way tool_name/command are.
    # Each call is still individually timeout-bounded via _lib_jq.
    EDITS_COUNT=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_input.edits | length' 2>/dev/null)
    if [ "$EDITS_COUNT" = "1" ]; then
      CONTENT=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_input.edits[0].new_string // empty' 2>/dev/null)
    else
      CONTENT=""
    fi
    ;;
esac
[ -n "${CONTENT:-}" ] || exit 0

# Same shape used elsewhere in this repo to scan for this pattern — kept
# identical so match counts stay comparable across the two call sites.
TRANSCRIPT_GLOB_PATTERN='projects/\*.*\.jsonl'
printf '%s\n' "$CONTENT" | grep -qE "$TRANSCRIPT_GLOB_PATTERN" || exit 0

ADDITIONAL_CONTEXT="This file's content looks like a hand-rolled transcript-corpus glob (a projects/*/*.jsonl shape). transcript-analysis.py already unions across every config root declared in ~/.claude/transcript-config-dirs by default — invoke the transcript-analysis skill instead of a one-off script, which silently covers only one root."

# shellcheck disable=SC2016 # single-quoted on purpose: $ctx is a jq --arg binding, not a shell variable; double-quoting would expand it in the shell before jq sees it. Bare `jq` suppresses this itself, but the _lib_jq wrapper that carries the timeout backstop is opaque to shellcheck's jq awareness.
_lib_jq -n --arg ctx "$ADDITIONAL_CONTEXT" \
  '{hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: $ctx}}' \
  2>/dev/null || true

exit 0
