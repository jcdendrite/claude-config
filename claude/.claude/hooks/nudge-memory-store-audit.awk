# Sidecar awk program for nudge-memory-store-audit.sh's wc-total-row
# exclusion and project-store count. Invoked via `awk -f` from the hook and
# from test_nudge_memory_store_audit.py directly -- kept out of the hook's
# inline shell string so an apostrophe in a comment here can never break the
# hook's single-quoted awk block (see docs/memory-audit-nudge.md for why a
# batched `find -exec ... {} +` can emit more than one "total" row).
#
# Input: `wc -c` lines (count then path), one call batched from
# `find -exec wc -c {} +` and possibly split across several `wc`
# invocations, each of which can emit its own trailing "total" row.
# A per-file line's path always contains "/"; a "total" row's remaining
# text (the bare word "total") never does, which `index(path, "/") == 0`
# discriminates on.
# Output: two lines -- the byte total (excluding total rows), then the
# project-store count bucketed by the text up to the rightmost "/memory/"
# in each path.
{
  count = $1
  path = $0
  sub(/^[ \t]*[0-9]+[ \t]+/, "", path)
  if (index(path, "/") == 0) next
  sum += count
  if (match(path, /^.*\/memory\//)) seen[substr(path, 1, RLENGTH - 1)] = 1
}
END {
  print sum + 0
  print length(seen)
}
