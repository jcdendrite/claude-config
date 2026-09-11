# Bash word-expansion coverage

`_lib_command_has_write_construct` and its callers scan Bash command text
before a real shell ever executes it, so any of bash's word-expansion
types (Bash manual §3.5) that the executing shell resolves but this
mechanism doesn't could hide a real write target from the scan. This
table names, for each type, the function that currently handles or
discloses it — a future gap in an already-audited type surfaces as a
documented-missing row instead of another independently-discovered
bypass, though a still-unaudited type below still needs its own discovery
work first.

| Word-expansion type | Status |
|---|---|
| Brace expansion | Handled: `_lib_brace_flatten` flattens a live construct to its first alternative before the raw-text scans re-run against it; `_lib_command_has_brace_expansion_bypass` separately detects the construct's shape and denies outright inside `_lib_redirect_candidates`. |
| Tilde expansion | Currently unaudited against this mechanism. |
| Parameter expansion and command substitution | Currently unaudited against this mechanism. |
| Arithmetic expansion | Currently unaudited against this mechanism. |
| Word splitting | Currently unaudited against this mechanism. |
| Quote removal | Handled: `_lib_strip_shell_quotes` produces `COMMAND_UNQUOTED`, the text every raw-text scan in this family reads instead of raw `$COMMAND`. |
| Filename (glob) expansion | Open, not closed: `_lib_shape_match`'s `case`-based matching and quoted `-ef` comparisons treat glob metacharacters (`?`, `*`) in a candidate as literal data, but real bash pathname expansion would glob-expand an unescaped `?`/`*` against an already-existing filesystem entry before the write utility ever sees the argument. Not yet built out or tracked in a dedicated issue. |

"Currently unaudited" means no one has yet traced whether a construct of
that type can hide a write target from `_lib_command_has_write_construct`
or its callers — not that it is known-safe. Auditing one of these rows and
finding a real gap follows the same pattern the brace-expansion row above
sets: a detection predicate (or a flattening pass, if the construct's real
value can't be determined from text alone) added to `_lib.sh`, wired into
both consumer hooks via the same `raw || flattened`-style pairing
`test_hook_alignment.py`'s `test_raw_brace_scan_has_flattened_companion`
enforces for the brace-expansion case.
