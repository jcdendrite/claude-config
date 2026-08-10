#!/bin/bash
# hook-class: informational
# Gate: ask (never deny) when an Edit/Write/MultiEdit call adds a
# dependency name to a package.json that wasn't already declared —
# reminder layer for claude/.claude/CLAUDE.md's §Safety "Name every new
# package before it is fetched" duty. See docs/security-hardening.md for
# the three-layer design and the full named-residuals list.
#
# Invocation shape (only) mirrors require-worktree-for-git-writes.sh's
# python3 handoff; the disposition is INVERTED. That hook fails CLOSED on
# a parser problem. This one never denies: every failure mode below is
# either a silent allow or a degraded ask, never a block — a missed
# reminder is not a security boundary.
#
# Reconstruction table (what parse-manifest-dependencies.py's
# compute_new_dependency_names applies to tool_input, for each tool):
#   Edit      pre-state=on-disk content; post-state=old_string->new_string
#             on it, honoring replace_all.
#   MultiEdit pre-state=on-disk content; post-state=each edits[] item
#             applied in order against a running buffer, each honoring
#             its own replace_all.
#   Write     pre-state=on-disk content, or empty if the file doesn't
#             exist; post-state=content verbatim.
# A pre-state that fails to parse as JSON (an agent mid-repair of a
# broken manifest) is agent-authored content exactly like the post-state
# is, so it is never silently treated as an empty dependency set (would
# false-fire on every existing dependency) or silently allowed (would
# suppress a disclosure) -- it degrades to the same loud, generic ask as
# any other helper failure.
#
# Filter order is load-bearing -- this runs on every Edit/Write/MultiEdit
# for every stow user, and nothing else on that matcher spawns a python3
# subprocess today:
#   1. tool_name in Edit|Write|MultiEdit, else silent allow.
#   2. tool_input.file_path non-empty, else silent allow.
#   3. basename == "package.json", case-sensitive, else silent allow --
#      deliberate: this repo targets stock macOS, where APFS is
#      case-insensitive-preserving by default, but the manifest name
#      itself is a fixed, always-lowercase npm convention.
#   4. path excludes node_modules/, fixtures/, __fixtures__/, test-data/
#      as whole path segments (not a substring match -- "my-fixtures-app"
#      must not exclude), else silent allow. A vendored or test-fixture
#      manifest is not a dependency the human is choosing.
#   5. on-disk pre-state size over _LIB_SIZE_THRESHOLD_BYTES -> degraded
#      ask. Independent of step 6/7: the size check needs no python3.
#   6. interpreter sanity probe: python3 absent from PATH, or present but
#      `python3 -c ''` exits nonzero (the Xcode Command Line Tools shim,
#      which sits on PATH but fails until CLT is installed) -> silent
#      allow. Both mean "the tool to evaluate is unavailable", not "the
#      content is unevaluable" -- kept distinct from step 5/7's degraded
#      ask, and checked before step 7 so a broken interpreter can never
#      be misread as a helper content-parse failure.
#   7. spawn the helper under _lib_capped. Nonzero exit, or an empty
#      output file, -> degraded ask. Zero new dependencies -> silent
#      allow. Otherwise -> ask, naming every new dependency.
#
# No repo-scoped opt-out sentinel. A `.claude/*-optout` file this hook
# would be the sole reader of is agent-writable with one Write call and
# produces total, silent, permanent suppression -- the one-step
# suppression failure mode the degraded-ask disposition above exists to
# prevent. See docs/security-hardening.md.
#
# Emission: every interpolated value (the file path on the degraded-ask
# path; the dependency names/constraints on the ordinary path) is folded
# into the reason string in plain bash, then the WHOLE reason is encoded
# in one shot via `_lib_jq -Rs .` -- never an interpolated payload built
# with echo. This is the repo's first hook to interpolate untrusted
# content into a decision envelope. Disposition is inverted from
# _lib_emit_deny: where that hard-blocks on encode failure, this one
# `exit 0`s silently -- a half-built payload is worse than none.
#
# Known gaps (accepted, not chased further -- rationale: docs/security-hardening.md):
#   - `overrides`/`resolutions` repointing an existing name at a different
#     tarball, and `scripts.preinstall`/`postinstall`, aren't detected --
#     key-set diffing only sees dependency-name additions.
#   - Bash heredoc/`tee`/`sed`/`node -e`/`npx create-*` manifest writes
#     never fire this hook -- it only sees Edit/Write/MultiEdit tool calls.
#   - `npm pkg set dependencies.<name>=<ver>` writes a manifest entry with
#     no ask -- the highest-priority follow-up of this hook family, since
#     it reproduces the originating incident end-to-end.
#   - Non-`package.json` ecosystems (including this repo's own
#     `requirements.txt`) and lockfiles aren't covered.

set -uo pipefail

# shellcheck disable=SC1091
if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  # Steps 1-3 below need _lib_jq; a failed source happens before any
  # manifest could have been matched, and the degraded-ask path also
  # needs _lib_jq to encode its own reason -- so this is unconditionally
  # silent, not degraded.
  exit 0
fi

INPUT=$(cat)
[ -n "$INPUT" ] || exit 0

# --- Step 1 ----------------------------------------------------------
TOOL_NAME=$(printf '%s' "$INPUT" | _lib_jq -r '.tool_name // empty' 2>/dev/null)
case "$TOOL_NAME" in
  Edit | Write | MultiEdit) ;;
  *) exit 0 ;;
esac

# --- Step 2 ----------------------------------------------------------
FILE_PATH=$(printf '%s' "$INPUT" | _lib_jq -r '.tool_input.file_path // empty' 2>/dev/null)
[ -n "$FILE_PATH" ] || exit 0

# --- Step 3 (case-sensitive; deliberate, see header) ------------------
BASENAME=$(basename -- "$FILE_PATH")
case "$BASENAME" in
  package.json) ;;
  *) exit 0 ;;
esac

# --- Step 4 (whole-path-segment exclusion, see header) -----------------
case "$FILE_PATH" in
  */node_modules/* | node_modules/* | \
    */fixtures/* | fixtures/* | \
    */__fixtures__/* | __fixtures__/* | \
    */test-data/* | test-data/*)
    exit 0
    ;;
esac

# Strips C0/C1 control codepoints (U+0000-U+001F, U+007F-U+009F) and every
# Unicode Cf (Format) codepoint (bidi-override/isolate controls and
# zero-width joiners -- the "Trojan Source" character set), then caps
# length, before an agent-controlled value (the file path on the
# degraded-ask path) enters a human-facing reason string. Both passes run
# inside one jq call rather than shelling out to `tr -d`: `tr` deletes raw
# bytes, and a byte-range delete for 0x7F-0x9F corrupts any multi-byte
# UTF-8 character whose continuation byte falls in that range (verified:
# GNU tr on Linux mangles this hook's own "…and N more" marker this way;
# BSD tr on macOS happens not to). jq decodes to codepoints before its
# regex ever runs, so the same two ranges match by character, not by byte.
# The helper applies the equivalent Cf rule (via Python's unicodedata) to
# every dependency name/constraint it emits; this hook uses jq instead
# since python3 availability isn't guaranteed at this call site (the size
# guard below fires before step 6's interpreter probe, by design).
_MAX_ASK_FIELD_CHARS=200
_sanitize_ask_field() {
  printf '%s' "$1" | _lib_jq -Rr 'gsub("[\\x00-\\x1f\\x7f-\\x9f]"; "") | gsub("\\p{Cf}"; "")' | cut -c "1-${_MAX_ASK_FIELD_CHARS}"
}

_emit_ask() {
  local reason="$1" reason_json
  reason_json=$(printf '%s' "$reason" | _lib_jq -Rs . 2>/dev/null)
  # A half-built payload is worse than none -- see header's Emission note.
  [ -n "$reason_json" ] || exit 0
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":%s}}\n' \
    "$reason_json"
  exit 0
}

_emit_degraded_ask() {
  local sanitized_path
  sanitized_path=$(_sanitize_ask_field "$1")
  _emit_ask "dependency delta could not be determined for '$sanitized_path' -- claude/.claude/CLAUDE.md's \"Name every new package before it is fetched\" duty still applies: if this edit adds a dependency, name it, its exact version constraint, and why, and get explicit confirmation before it proceeds."
}

# --- Step 5: on-disk pre-state size guard -------------------------------
# Portable byte-count probe: GNU stat, then BSD/macOS stat, then wc -c --
# same pattern as deny-data-file-reads.sh's file_size() (not shared via
# _lib.sh; see that hook for the canonical form).
_file_size() {
  local target="$1" size
  size=$(stat -c%s -- "$target" 2>/dev/null) \
    || size=$(stat -f%z -- "$target" 2>/dev/null) \
    || size=$(wc -c < "$target" 2>/dev/null | tr -d '[:space:]')
  printf '%s' "$size"
}

if [ -f "$FILE_PATH" ]; then
  FILE_SIZE=$(_file_size "$FILE_PATH")
  if [ -n "$FILE_SIZE" ] && [ "$FILE_SIZE" -gt "$_LIB_SIZE_THRESHOLD_BYTES" ] 2>/dev/null; then
    _emit_degraded_ask "$FILE_PATH"
  fi
fi

# --- Step 6: interpreter sanity probe -----------------------------------
command -v python3 >/dev/null 2>&1 || exit 0
python3 -c '' >/dev/null 2>&1 || exit 0

# --- Step 7: spawn the helper --------------------------------------------
HELPER_SCRIPT="$(dirname "$0")/parse-manifest-dependencies.py"
[ -f "$HELPER_SCRIPT" ] || _emit_degraded_ask "$FILE_PATH"

HELPER_OUT=$(mktemp "${TMPDIR:-/tmp}/manifest-deps.XXXXXX") || _emit_degraded_ask "$FILE_PATH"
trap 'rm -f "$HELPER_OUT"' EXIT

printf '%s' "$INPUT" | _lib_capped python3 "$HELPER_SCRIPT" >"$HELPER_OUT" 2>/dev/null
HELPER_EXIT=$?

if [ "$HELPER_EXIT" -ne 0 ] || [ ! -s "$HELPER_OUT" ]; then
  _emit_degraded_ask "$FILE_PATH"
fi

# Wire grammar (see parse-manifest-dependencies.py's module docstring):
# <marker-line>\n<record>\0<record>\0...\0<record>. The marker line is
# read first, on its own line, before the NUL-delimited record stream is
# ever touched -- keeping the two structurally distinct even when a
# crafted dependency name equals the marker's own text. The record join
# for display runs entirely inside jq (split on the real NUL delimiter,
# not a `tr`/`sed` substitution against a display character): bash's
# $(...) cannot hold embedded NUL bytes in the first place, and a `tr '\0'
# ','` fix-up would also misrender a dependency name that itself contains
# a literal comma, merging it visually with the join separator.
MARKER_LINE=$(head -n 1 "$HELPER_OUT")
RECORDS_DISPLAY=$(tail -n +2 "$HELPER_OUT" | _lib_jq -Rrs 'split("\u0000") | map(select(length > 0)) | join(", ")')

# Empty diff -- nothing new to disclose.
[ -n "$RECORDS_DISPLAY" ] || exit 0

REASON="This edit to '$(_sanitize_ask_field "$FILE_PATH")' adds a dependency not already declared: ${RECORDS_DISPLAY}"
if [ -n "$MARKER_LINE" ]; then
  REASON="${REASON} ($(_sanitize_ask_field "$MARKER_LINE"))"
fi
REASON="${REASON}. Per claude/.claude/CLAUDE.md's \"Name every new package before it is fetched\": name each package, its exact version constraint, and why, then get explicit confirmation before this edit proceeds."

_emit_ask "$REASON"
