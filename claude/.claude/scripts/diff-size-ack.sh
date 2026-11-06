#!/usr/bin/env bash
# Record the engineer's `Proceed anyway` answer to the diff-size gate for the current branch.
# Usage: diff-size-ack.sh   (no arguments)
# Exit statuses: 0 recorded or already recorded, 1 nothing recorded because the state is unreadable or the count is
# below the threshold, 2 usage, 3 the latch could not be written.
# The gate (hooks/require-diff-size-prompt.sh) honors the latch this writes. docs/hooks.md owns the full behavior.
# shellcheck source=../hooks/_lib.sh
if ! . "$(dirname "${BASH_SOURCE[0]}")/../hooks/_lib.sh"; then
  printf 'diff-size-ack.sh: could not source _lib.sh\n' >&2
  exit 1
fi

# No `set -e`: _lib_diff_size_gate_state returns 1, 2 and 3 as answers.
set -uo pipefail

if [ "$#" -ne 0 ]; then
  printf 'usage: diff-size-ack.sh (takes no arguments)\n' >&2
  exit 2
fi

# An unresolvable config directory also makes _lib_diff_size_gate_state return 1, which the case below reports.
config_dir=$(_lib_config_dir) || config_dir=""

gate_state_status=0
gate_state_output=$(_lib_diff_size_gate_state "$PWD") || gate_state_status=$?

case "$gate_state_status" in
  0 | 3) ;;
  2)
    printf 'diff-size-ack.sh: nothing was recorded because the diff is below the %s-line threshold, so no acknowledgment is needed.\n' \
      "$_LIB_DIFF_SIZE_PROMPT_THRESHOLD_LINES" >&2
    exit 1
    ;;
  *)
    printf 'diff-size-ack.sh: nothing was recorded because the state could not be read here. If the gate denied this commit, the ack and the gate read different state, so report this to the engineer.\n' >&2
    exit 1
    ;;
esac

if [ "$gate_state_status" -eq 3 ]; then
  printf 'The diff-size acknowledgment for this branch is already recorded under %s. Re-run the denied command unchanged.\n' "$config_dir"
  exit 0
fi

{ IFS= read -r latch_file; IFS= read -r measurement; } <<<"$gate_state_output"
changed_line_count="${measurement%% *}"
base_branch="${measurement#* }"

# The default-branch arm takes the base from origin/HEAD's target with no shape check, and the name is printed and stored.
base_name_shape_status=0
_lib_stack_base_name_shape "$base_branch" || base_name_shape_status=$?
if [ "$base_name_shape_status" -eq 0 ]; then
  base_ref="origin/$base_branch"
  latch_base_field="$base_ref"
else
  base_ref="the base branch"
  latch_base_field="-"
fi

latch_dir="${latch_file%/*}"
latch_temp_file=""
trap '[ -z "$latch_temp_file" ] || rm -f -- "$latch_temp_file"' EXIT

report_write_failure() {
  printf 'diff-size-ack.sh: the acknowledgment could not be recorded (%s). Report this to the engineer.\n' "$1" >&2
  exit 3
}

mkdir -p -- "$latch_dir" 2>/dev/null || report_write_failure "the latch directory under $config_dir could not be created"
# BSD mktemp needs a template.
latch_temp_file=$(mktemp "$latch_dir/ack.XXXXXX" 2>/dev/null) || report_write_failure "a temporary file could not be created under $config_dir"
printf '%s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$changed_line_count" "$latch_base_field" >"$latch_temp_file" ||
  report_write_failure "the temporary file could not be written"
mv -f -- "$latch_temp_file" "$latch_file" 2>/dev/null || report_write_failure "the move into place failed"
moved_temp_name="${latch_temp_file##*/}"
latch_temp_file=""
trap - EXIT

# With a directory at the latch path, `mv -f` exits 0 and puts the temp file inside it.
if [[ ! -f "$latch_file" ]]; then
  rm -f -- "$latch_file/$moved_temp_name" 2>/dev/null
  printf 'diff-size-ack.sh: the acknowledgment could not be recorded because something that is not a regular file sits at %s. The engineer removes it, then re-runs this script.\n' "$latch_file" >&2
  exit 3
fi

# Sweeps latches written more than 30 days ago; a failed sweep does not undo the write.
find "$latch_dir" -maxdepth 1 -type f -mtime +30 -delete 2>/dev/null || true

printf 'Recorded the diff-size acknowledgment for this branch under %s (%s changed lines against %s). Re-run the denied command unchanged.\n' \
  "$config_dir" "$changed_line_count" "$base_ref"
exit 0
