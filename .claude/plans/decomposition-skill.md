# Decomposition phase: the skill group

## Context

Goal: move the `skill-invocation`, `skill-pair`, and `judgment-pair` subcommands, with their `add_parser` blocks and their test slice, out of the `transcript-analysis.py` shim into the `transcript_analysis` package, and verify the move with the move-fidelity harness.

Ask: "let's do plan-it for the skill group" (engineer, this session). Tracker GH-1116 lists the group as: "skill — skill-invocation, skill-pair, judgment-pair (~1,225 test lines) Goes first: it deletes the shim duplicates `review_trace.py` and `review_rounds.py` carry (`_normalize_skill_name`, `_SLASH_COMMAND_RE`) and converts `review_rounds.py`'s four drifted `transcript-analysis.py:<N>` citations." Engineer's answers to Step 4: "Move the blocks (Recommended)" (move the three `add_parser` blocks into the package) and "In this PR, first dispatch (Recommended)" (the parser-introspection check lands as this PR's first dispatch).

Why now: the prep PR is merged. It provides the move-fidelity harness, the loader-free `fake_projects`/`cost_ledger_file`/`cost_ledger_enabled` fixtures, and `scope._add_project_scope_args`. This phase is the first to move parser blocks, and the harness's `capture --all-help` cannot see an argparse default, type, `dest`, or `set_defaults` handler binding, so a parser-introspection check has to exist before any block moves.

Intended outcome: the three commands, their helper, and their parser blocks live in the package. The shim keeps no copy of `_normalize_skill_name` or the `command-name` regex. `review_rounds.py` cites symbols, not shim line numbers. Every later phase can reuse the same parser-move mechanism and the introspection check.


## Approach

The skill group moves out of the shim into three new package modules, split at command seams: `skill_invocation.py`, `skill_pair.py`, and `judgment_pair.py`. Each module owns its handler plus an `_add_<command>_subparser(subparsers)` function that holds the command's former `add_parser` block. `build_parser()` calls each function at its block's old position, so registration order and every `--help` byte stay the same.

The first dispatch extends the move-fidelity harness so it can see what help bytes cannot:
- `capture --all-help` also dumps each parser's argparse configuration.
- `compare-captures --moved-callable` accepts a declared handler relocation.
- `nodes --rename` reverses names only where they occur as code.

No stow consumer sees a behavior change. Help, stdout, stderr, and exit codes stay byte-identical, under both a single-root and a multi-root corpus (Verification 6). The parser dumps differ only by four declared callable relocations. An uncaught exception's traceback names the new module files instead of the shim.

**Part 1: harness extensions (dispatch 1).** All changes are in `claude/.claude/scripts/tests/_move_fidelity.py`. The module stays stdlib-only. Every subcommand keeps its exit contract: 0 when clean, 1 on any finding, 2 on bad input. `capture` still exits 0 after recording runs, and exits 2 on bad input, including a `build_parser()` that fails to load.

- **Parser dump.** `capture --all-help` loads the script once and uses that one `build_parser()` result for both the subcommand names and the dumps. The load runs in the harness process under the ambient environment, not capture's pinned one (row 48). For `help-top` and for each subcommand in registration order, it writes `<label>.parser` next to the label's `.stdout`. Each file holds `\n`-terminated lines in this order:
  - `parser allow_abbrev=<v> argument_default=<v> prefix_chars=<v> exit_on_error=<v>`
  - one `action <class> option_strings=<v> dest=<v> nargs=<v> const=<v> default=<v> type=<v> choices=<v> required=<v>` line per entry of the parser's `_actions`, in order;
  - one `mutex required=<v> dests=<v>` line per entry of `_mutually_exclusive_groups`, in order, with `dests` listing its `_group_actions`' dests in order;
  - one `default <key>=<v>` line per `_defaults` key, sorted by key.
- **Value rendering.** The first rule that matches wins:
  1. `None`, `bool`, `int`, `float`, `str`: `repr()`.
  2. A subparsers action's `choices`: the list of its subcommand names, in registration order.
  3. `list` renders as `[a, b]`, `tuple` as `(a, b)` (`(a,)` for one item), and `dict` as `{k: v}` in insertion order, each item rendered by these rules.
  4. `set`/`frozenset`: the type name, then `{`, the rendered items sorted as strings, then `}`.
  5. A callable with string `__module__` and `__qualname__`: `<callable MODULE:QUALNAME>`, with MODULE rendered as `<script>` when it equals `CAPTURE_TARGET_MODULE_NAME`.
  6. Anything else: `repr()`, unless the repr contains ` at 0x`, in which case `<instance MODULE:QUALNAME>` of its type.

  Action classes render by rule 5, for example `<callable argparse:_StoreAction>`. No line embeds a memory address.
- **`compare-captures DIR_A DIR_B [--mask REGEX...] [--moved-callable QUALNAME=MODULE...]`.**
  - Before masking, every `<callable <script>:QUALNAME>` token in DIR_A's `.parser` files, matched through its closing `>`, becomes `<callable MODULE:QUALNAME>`. A declared QUALNAME that prefixes another callable's QUALNAME leaves that other token as written.
  - Only DIR_A, and only its `.parser` files, are rewritten. DIR_A must therefore be the before side, and a stale `<script>` binding left on the after side still differs.
  - Each declaration that rewrote nothing gives its own `moved-callable-unused` finding, naming its QUALNAME.
  - A malformed spec, an empty MODULE, or a QUALNAME declared twice exits 2.
- **Code-only rename reversal.**
  - `nodes` finds the character spans of every `STRING`, `FSTRING_MIDDLE`, and `COMMENT` token in a node's text with `tokenize`, the same pass `_comment_tokens` already makes. Each span runs from its token's start position to its end position, converted to character offsets in the node's text, so a multi-line string's span covers every line it occupies.
  - A NEW-name match that starts inside one of those spans is left as written. The rule applies to the reversal, to the `rename-ambiguous` test against the before node, and to the `rename-unused` count. Names inside an f-string's replacement field are code tokens, so they are still reversed.
  - **Python floor.** `nodes` given any `--rename` exits 2 when `tokenize` has no `FSTRING_MIDDLE` (Python below 3.12), with a `bad-input` message naming the 3.12 requirement. The attribute is read at call time, so the module still imports, and every other subcommand still runs, on 3.11. This guard, not a docstring-only floor, is the choice: without it a 3.11 run either raises, exiting 1 with a traceback that the exit contract reads as findings, or misreads every f-string as one literal (rows 46–47).
- **Module docstring and help text.**
  - Usage line: `compare-captures DIR_A DIR_B [--mask REGEX...] [--moved-callable QUALNAME=MODULE...]`.
  - The paragraph after the findings/exit-status paragraph becomes exactly these sentences:
    - "`nodes --rename` reverses a NEW name only where it occurs as code, including inside an f-string replacement field, so a NEW name in a string literal, docstring, or comment is compared as written."
    - "`nodes --rename` needs the f-string tokens Python 3.12's `tokenize` reports, and exits 2 on an interpreter without them."
    - "`capture --all-help` also writes `<label>.parser` for the top level and each subcommand: one line per argparse action, mutually exclusive group, and `set_defaults` entry, with each callable rendered as `<callable MODULE:QUALNAME>` and the captured script's own module as `<script>`."
    - "The dump covers one level of subcommands and no callable's body."
    - "The dump calls `build_parser()` in the harness process under the ambient environment, not capture's pinned one."
    - "`compare-captures --moved-callable QUALNAME=MODULE` rewrites `<script>:QUALNAME` to `MODULE:QUALNAME` in DIR_A's `.parser` files only, so DIR_A must be the before side."
    - "`remainder` drops every top-level import statement, with any comment on its lines, before comparing, so it checks neither import order nor import-line comments."
  - `--all-help` help: "capture --help, and a parser dump, for the top level and every subcommand".
  - `--moved-callable` help: "rewrite DIR_A's <script>:QUALNAME to MODULE:QUALNAME in .parser files; one that rewrites nothing is moved-callable-unused".
- **Self-test placement.** The parser and `--moved-callable` seeds go in a new `tests/test_transcript_move_fidelity_parser.py`, because the existing 924-line file cannot take them under 1,000 lines (row 19). That file carries verbatim copies of `_invoke`, `_checks`, `_write`, and `_write_capture`, under CLAUDE.md's DAMP exception. Its docstring is: "Seeded-defect self-test for tests/_move_fidelity.py's parser dump (capture --all-help's .parser files) and compare-captures --moved-callable.", a blank line, then "Every check is proved by a synthetic script or capture directory written under tmp_path: a clean case yields no findings, and one seeded defect per check yields exactly the finding that names it. The other subcommands' seeds live in test_transcript_move_fidelity.py and test_transcript_move_fidelity_test_ids.py."
  - **Isolation.** Every seed that imports a module beside a synthetic script uses a module-name literal no other seed uses, and pops it from `sys.modules` in a `finally`, as `test_transcript_move_fidelity.py:752-777` does (row 48).
  - **Size.** Parametrize wherever seeds share a shape, so the file stays at or under 1,000 lines.
  - Positive control. Capturing a synthetic two-subcommand script twice compares clean. `help-top.parser` and one `.parser` per subcommand exist, and no `run-NN` label gets one.
  - Golden text. One synthetic script's `help-top.parser` and one subparser's `.parser` are pinned in full:
    - The subparser carries an action with `default`, `type=int`, list `choices` with a `metavar`, `nargs="?"`, `const`, `required`, and an explicit `dest`. Its `default`, `const`, `dest`, `nargs`, `type`, and `choices` render to pairwise distinct strings, so a transposition changes the text.
    - It also carries a mutex group, a script-defined `set_defaults(func=...)`, and a second `set_defaults` key, inserted after `func`, that sorts before it.
    - `help-top.parser`'s text pins the subparsers action's `choices` as the subcommand names in registration order.
  - A second golden `parser` line pins a subparser built with `allow_abbrev=False`, `argument_default=argparse.SUPPRESS`, `prefix_chars="-+"`, and `exit_on_error=False`.
  - One test is parametrized over help-invisible mutations: a `default`, a `type`, a `dest` hidden behind a `metavar`, a `const`, `choices` hidden behind a `metavar`, `allow_abbrev`, and the `set_defaults` handler. Each mutation gives exactly one `capture-bytes` finding, on `help-<name>.parser`, and that subcommand's `.stdout` compares equal.
  - Swapping two subcommands' registration order gives `capture-bytes` on `help-top.parser`.
  - One rendering seed each:
    - a handler imported from a module beside the script renders as `<callable that_module:handler>`;
    - `type=int` renders as `<callable builtins:int>`;
    - a tuple, a dict, and a one-item tuple render as specified;
    - an `object()` default renders as `<instance builtins:object>` and the file contains no `0x`;
    - a frozenset of small ints (`{10, 9, 2}`, whose `list()` order is the same under any hash seed and differs from the as-string sort) renders sorted as strings. The test asserts that precondition first;
    - rule 6's plain-`repr` arm: a `decimal.Decimal("1.5")` default renders as `Decimal('1.5')`;
    - rule 5's fall-through: a `functools.partial(int, base=16)` `type`, which has no `__qualname__`, renders as exactly its `repr()`. The test asserts first that this repr holds no ` at 0x`.
  - `--moved-callable`:
    - Undeclared, a `<script>`→`pkg.mod` handler change gives `capture-bytes`. Declared correctly it compares clean. Declared with the wrong MODULE it gives `capture-bytes`.
    - A declaration whose after side still shows `<script>` gives `capture-bytes`.
    - A QUALNAME that appears nowhere gives `moved-callable-unused`.
    - One used and one unused declaration give exactly one `moved-callable-unused`, naming the unused QUALNAME.
    - Declaring `cmd_a` leaves `<callable <script>:cmd_ab>` unrewritten, so a capture whose after side keeps that token compares clean.
    - The same token inside a `.stdout` file is not rewritten.
    - One parametrized test: a spec with no `=`, an empty MODULE (`cmd_a=`), and a repeated QUALNAME each exit 2 with `bad-input` on stderr.
  - End to end: a before script defines its handler, and an after script imports the same handler from a sibling module. With the declaration they compare clean. Without it, exactly `help-<name>.parser` differs.
- **Rename seeds**, in `test_transcript_move_fidelity.py`, parametrized to fit its 76 lines of headroom (row 19):
  - `test_nodes_reversal_is_textual_so_a_new_name_only_in_a_string_or_comment_passes_as_a_used_rename` becomes `test_nodes_does_not_reverse_a_new_name_inside_a_string_or_comment`. It keeps its two parametrize ids and gains a third, with the expected checks as a parameter:
    - `inside-a-string-literal` expects `["node-ast", "rename-unused"]`;
    - `inside-a-comment` expects `["node-comments", "rename-unused"]`;
    - `inside-f-string-literal-text`, `f"helper failed: {value}"` → `f"util.helper failed: {value}"`, expects `["node-ast", "rename-unused"]`.
  - New, one test parametrized over three cases, each clean with `compared-nodes: 1`:
    - a one-line before docstring that already names `util.helper`, with the code prefixed;
    - `f"{helper(value)}"` → `f"{util.helper(value)}"`;
    - a three-line before docstring naming `util.helper` on its last line, followed by `helper(value)` → `util.helper(value)` in code.
  - New: with `tokenize.FSTRING_MIDDLE` removed by `monkeypatch.delattr`, `nodes --rename` exits 2 and its stderr names Python 3.12.
  - The existing code-occurrence `rename-ambiguous` seed stays.
  - The last docstring sentence becomes "The test-ids seeds live in test_transcript_move_fidelity_test_ids.py, and the parser-dump and --moved-callable seeds in test_transcript_move_fidelity_parser.py."
  - In `test_transcript_move_fidelity_test_ids.py`, the docstring's last sentence becomes "The other subcommands' seeds live in test_transcript_move_fidelity.py and test_transcript_move_fidelity_parser.py."
- **Stopping rule.** Seeding is complete when all three conditions hold:
  1. Every `findings.append(` site in `_move_fidelity.py` has a seed that produces it. There are 28 at plan time: the 27 in row 19 plus `moved-callable-unused`. The grep governs.
  2. Every `raise BadInput` site that dispatch 1 adds, and every existing site a new flag reaches, has a seed that asserts exit 2 and the site's message. At plan time those are:
     - a `--moved-callable` spec with no `=` or an empty QUALNAME (the existing `_parse_key_value_pairs` site);
     - an empty MODULE;
     - a repeated QUALNAME;
     - `nodes --rename` under a `tokenize` without `FSTRING_MIDDLE`.

     The shared loader's load failure keeps its existing seed. A before/after comparison of `grep -n "raise BadInput"` governs.
  3. Every rendering, reversal, ordering, and pinned-text branch has an exact-text assertion, or a Verification 1 mutation control, that changes under a wrong implementation. The branches are:
     - the six value rules, including rule 5's fall-through to rule 6 and both arms of rule 6; the choices special case; the `<script>` substitution;
     - the four line shapes and their order within a file; `_actions` order; `mutex` order and `dests` order; the `default` key sort; the set-item sort; dict insertion order; the one-item tuple form; the `parser` line's four attributes;
     - the DIR_A-only, `.parser`-only rewrite; the rewrite's trailing `>` boundary; per-declaration `moved-callable-unused`;
     - code-only reversal for a string, a docstring, a comment, an f-string replacement field, f-string literal text, and a code occurrence after a multi-line docstring.

  Dispatch 1's return maps every finding site, `BadInput` site, and branch to its seed or control, and the parent checks that map before Verification 2. Review rounds on dispatch 1 stop when no remaining finding is either a false clean (a defect the check misses) or a contradiction between the module docstring, `--help`, and behavior. Any other reviewer FYI goes to Out of scope, not into a new seed.

**Part 2: production layout (dispatch 2, stage 1).** Moved code is copied verbatim, extracted by line range from Verification 2's scratch snapshots and never retyped. Only these edits are allowed:
- the production rename map below;
- each module's docstring and imports;
- the registrar parameter name;
- wrap-only reflow, with the count reported.

Every module starts with `from __future__ import annotations`. No `.py` text names a ticket or a line number.

- **`transcript_analysis/judgment_pair.py`** holds `cmd_judgment_pair`, then `_add_judgment_pair_subparser`.
  - Imports: `argparse`; `from pathlib import Path`; `from transcript_analysis import corpus, redaction, render, review_rounds, scope`.
  - Docstring: `"""The judgment-pair command: cmd_judgment_pair, which pairs each review skill's last main-thread output with the user's next fresh prompt, and its subparser registration.\n\nImports corpus, redaction, render, review_rounds, and scope by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`
- **`transcript_analysis/skill_invocation.py`** holds `_normalize_skill_name`, then `cmd_skill_invocation` (its OUTPUT INVARIANT comment moves verbatim), then `_add_skill_invocation_subparser`.
  - Stage-1 imports: `argparse`, `re`, `sys`; `from collections import defaultdict`; `from transcript_analysis import corpus, render, scope`.
  - Docstring, final from stage 1: `"""The skill-invocation command: cmd_skill_invocation's per-skill top-level, routed, and /slash invocation tally, the _normalize_skill_name display-label helper, and its subparser registration.\n\nImports corpus, render, review_rounds, and scope by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`
- **`transcript_analysis/skill_pair.py`** holds `cmd_skill_pair`, then `_add_skill_pair_subparser`.
  - Imports: `argparse`, `fnmatch`; `from collections import defaultdict`; `from datetime import UTC, datetime`; `from transcript_analysis import corpus, pricing, scope`.
  - Docstring: `"""The skill-pair command: cmd_skill_pair, the ISO-week rate at which a follower skill fires in sessions where a leader skill fired on the main thread, and its subparser registration.\n\nImports corpus, pricing, and scope by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`
- **Registrars.**
  - Each registrar takes `build_parser()`'s `add_subparsers()` action as `subparsers` and returns `None`.
  - Docstring: `"""Register the <command> subparser on build_parser()'s subparsers action, binding cmd_<name> through set_defaults(func=...)."""`
  - Its body is the shim's block, with `sub` → `subparsers`, `_add_project_scope_args` → `scope._add_project_scope_args`, `_iso_date` → `scope._iso_date`, and `REVIEW_SKILLS` → `review_rounds.REVIEW_SKILLS`.
  - The `_add_project_scope_args` entry applies only to the skill-pair and judgment-pair registrars. The skill-invocation block builds its own `--projects`/`--this-repo` group and never calls it (row 54).
  - `set_defaults(func=cmd_<name>)` binds the module's own handler, so `main()`'s `parsed.func(parsed)` is unchanged.
- **Production rename map**, which applies to code only, never to docstrings or comments:
  - To `scope.`: `_branch_filter`, `_resolve_project_scope`, `_parse_absolute_window_args`, `_session_branch_context`, `_attributed_branch`, `_resolved_scope_header`, `_single_level_projects_glob`, `_iter_glob_scoped_sessions`, `_iter_scoped_sessions`, `_repo_scoped_project_slugs`.
  - `_resolve_scan_roots` → `scope.resolve_scan_roots`, and `_print_resolved_scope` → `scope.print_resolved_scope`.
  - To `corpus.`: `iter_sessions`, `_parse_ts`.
  - To `render.`: `_content_text`, `_fmt_date`, `_is_fresh_user_prompt`.
  - To `redaction.`: `_derive_proj_label`.
  - To `pricing.`: `_count_subagent_spawns`, `_warn_if_subagent_format_drift`.
  - To `review_rounds.`: `REVIEW_SKILLS`.

  That is 21 entries. Let ruff F821 drive the prefixes, and never run a regex over bare names.
- **`scope.py`** gains `_iso_date`, verbatim, immediately above `_parse_absolute_window_args` (its reader, row 12), plus `from datetime import datetime`. Its `_single_level_projects_glob` docstring sentence naming "transcript-analysis.py's cmd_skill_invocation" becomes "skill_invocation.py's cmd_skill_invocation".
- **Shim, stage 1.**
  - Delete `cmd_judgment_pair`, `_normalize_skill_name`, `cmd_skill_invocation`, `cmd_skill_pair`, and `_iso_date`.
  - Replace each of the three `build_parser()` blocks with the one line `_add_<command>_subparser(sub)` at the same position, keeping one blank line on each side.
  - Add `_iso_date` to the existing `from transcript_analysis.scope import (...)` block.
  - Add three by-name blocks in the file's commented style. Each imports the registrar, which `build_parser` reads bare, and the module's `cmd_*` as `# noqa: F401`, which only test files read as `_mod.cmd_*` (row 26).
  - Remove no existing import yet. Stage 1 is lint-dirty by design: F401 on exactly the seven names in row 25. That keeps the four expected failures assertion failures rather than AttributeErrors (row 27).

**Part 3: single homes and citations (dispatch 2, stage 2).**
- `skill_invocation.py`:
  - The `/slash` loop reads `review_rounds._SLASH_COMMAND_RE.finditer(content_str)` (row 20).
  - Drop `import re`.
  - Add `review_rounds` to the package import.
- `review_trace.py`:
  - Delete `_normalize_skill_name`, and add `skill_invocation` to its package import.
  - Both calls become `skill_invocation._normalize_skill_name(raw_skill_name)`.
  - The two-line "Mirrors cmd_skill_invocation's own user-record branch" comment becomes `# Mirrors skill_invocation.cmd_skill_invocation's user-record branch.`
  - In the module docstring, the "Imports …" sentence becomes "Imports corpus, denials, render, review_rounds, reviewer_yield, scope, and skill_invocation by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why." The "Calls …" sentence becomes "Calls reviewer_yield._is_reviewer_subagent_type, review_rounds._round_skill_name, and skill_invocation._normalize_skill_name, each owned by another command-group module."
- `review_rounds.py`, prose only:
  - Module docstring lines 4–5 become "REVIEW_SKILLS is the round-opening skill set. judgment_pair.py reads it for judgment-pair's --skills default."
  - The `REVIEW_SKILLS` comment becomes `# The three review-loop skills a round opens on.`
  - The `_SLASH_COMMAND_RE` comment becomes `# Captures only the skill name from a /slash invocation's <command-name> tag, never its <command-args>.` followed by `# skill_invocation.py and review_trace.py read this pattern too.` (row 51).
  - In `_round_skill_name`, "(as _normalize_skill_name does)" becomes "(as skill_invocation._normalize_skill_name does)", and "_normalize_skill_name (transcript-analysis.py:2253-2277) strips" becomes "skill_invocation._normalize_skill_name strips".
  - In `_round_open_skill`, "mirroring cmd_skill_invocation's own comment (transcript-analysis.py:2311-2323)" becomes "mirroring skill_invocation.cmd_skill_invocation's OUTPUT INVARIANT comment".
  - In `detect_round_windows`, "Re-expresses cmd_judgment_pair's own boundary rule (transcript-analysis.py:2166-2197)." becomes "Re-expresses judgment_pair.cmd_judgment_pair's boundary rule."
- Shim, final:
  - Remove `fnmatch`, `_iter_glob_scoped_sessions`, and `_single_level_projects_glob`.
  - Keep `_is_fresh_user_prompt`, `_iter_scoped_sessions`, `_repo_scoped_project_slugs`, and `_resolved_scope_header`, each with `# noqa: F401 -- read only via _mod.<name> from test files`.
  - The `REVIEW_SKILLS` import comment becomes `# REVIEW_SKILLS is read bare by build_parser's review-round-cost --skill choices.`
  - If F401 flags any other name, stop and report.

**Part 4: tests (dispatch 2, stage 2).** The new test files load no shim. They import package modules directly, which the loader-free fixtures permit (rows 6, 30).
- **`tests/test_transcript_skill_invocation.py`**:
  - Contents: `TestSkillInvocation`, `TestSkillInvocationScoping`, `TestSkillInvocationNameNormalization`, `TestSkillInvocationProvenance`, `TestSkillInvocationRepoScope`, plus a verbatim local copy of `_attr_ts`. The copy follows the `test_transcript_subagents.py:45` precedent; the legacy file keeps its own.
  - New, after the moved classes: module-level `test_slash_invocation_counts_the_command_name_and_never_its_command_args(fake_projects, capsys)`, in the shape of `TestSkillInvocationRepoScope::test_explicit_projects_never_invokes_git`.
    - It writes one `_user_msg("<command-name>/x</command-name><command-args>/abs/secret</command-args>")` record under `fake_projects` and runs `skill_invocation.cmd_skill_invocation` with `projects="*"`.
    - It asserts first that neither `secret` nor `/abs` occurs in stdout or stderr, then that the `x` row shows a /slash count of 1.
    - Docstring: `"""A /slash invocation contributes only its <command-name>; <command-args> text, which can hold absolute paths, never reaches output."""`
  - Docstring: `"""Tests for transcript_analysis/skill_invocation.py's cmd_skill_invocation: invocation buckets, --branches and --include-subagents scoping, name normalization, provenance, the repo-scoped default, and /slash <command-args> exclusion."""`
- **`tests/test_transcript_skill_pair.py`**:
  - Contents: the `_TS_SUNDAY`/`_TS_MONDAY` comment and constants, `TestSkillPair`, and `TestSkillPairSubagentFile`.
  - Docstring: `"""Tests for transcript_analysis/skill_pair.py's cmd_skill_pair: ISO-week pairing rates, --exclude-projects, --branches, and subagent-file followers."""`
- **`tests/test_transcript_judgment_pair.py`**:
  - Contents: `TestJudgmentPair`.
  - Docstring: `"""Tests for transcript_analysis/judgment_pair.py's cmd_judgment_pair: review-output and user-response window extraction, filters, truncation, and --out."""`
- **`tests/_skill_helpers.py`**:
  - Contents: `_TS_FIXED` with its comment, `_skill_pair_args`, `_skill_inv_args`, `_judgment_pair_args`, `_review_asst`, `_user_reply`.
  - Imports: `argparse`; `from transcript_analysis import review_rounds`; `from .conftest import _asst, _user_msg`.
  - Docstring: `"""Test helpers shared by the skill-group test files (test_transcript_skill_invocation.py, test_transcript_skill_pair.py, test_transcript_judgment_pair.py) and by test_transcript_analysis.py's cross-subcommand tables."""`
- **conftest**: `_skill_use_rec` moves byte-identical to sit directly after `_skill_use`. It is a record builder that three command groups' tests use (row 31).
- **Test rename map**:
  - `_mod.cmd_skill_invocation` → `skill_invocation.cmd_skill_invocation`, `_mod.cmd_skill_pair` → `skill_pair.cmd_skill_pair`, `_mod.cmd_judgment_pair` → `judgment_pair.cmd_judgment_pair`.
  - `_mod.scope` → `scope`, `_mod.os` → `os`, `_mod.REVIEW_SKILLS` → `review_rounds.REVIEW_SKILLS`.

  Nothing else on any moved line changes, apart from wrap-only reflow past 130 characters.
- **Legacy `test_transcript_analysis.py`**:
  - Delete the moved classes and helpers.
  - Import the six `_skill_helpers` names via `from ._skill_helpers import …`, and add `_skill_use_rec` to the `.conftest` import.
  - Remove only the imports F401 then flags.
  - Banners:
    - `# skill-pair` (:1107) becomes `# branch-attribution fixture timestamps`;
    - delete `# skill-invocation` (:5637);
    - `# skill-invocation: --branches, --include-subagents, name normalization` (:5928) becomes `# _repo_scoped_project_slugs guard, build_parser, and iter_sessions ordering`;
    - `# judgment-pair` (:6835) becomes `# _is_fresh_user_prompt (render.py)`.
  - Retarget exactly seven lines (row 27):
    - :8160 and :8166 to `_mod.scope` / `monkeypatch.setattr(_mod.scope, "_iter_glob_scoped_sessions", …)`;
    - :8178 and :8184 to the same for `_iter_scoped_sessions`;
    - :8206 and :8212 to `_mod.corpus.iter_sessions` / `monkeypatch.setattr(_mod.corpus, "iter_sessions", …)`;
    - :9163 to `monkeypatch.setattr(_mod.scope, "resolve_scan_roots", …)`.
  - `_UNCONDITIONAL_HEADER_CASES`, `TestAllSubcommandsSingleRootHeader`, `TestFormatDriftCanary`, `TestMultiRootFormatOutliers`, `TestIsFreshUserPrompt`, and the attribution classes stay byte-identical.
- **`test_transcript_cli_bootstrap.py`** gains six tests, each docstring stating the fact it proves (the shape at :399–401):
  - `skill-invocation --help` asserts `--include-subagents`;
  - `skill-pair --help` asserts `LEADER`;
  - `judgment-pair --help` asserts `--truncate-chars`;
  - three seeded runs reuse `_seed_review_trace_skill_account` with `--config-dir <acct>`:
    - `skill-invocation --projects '*'` exits 0 with `code-review` in stdout;
    - `skill-pair code-review plan-review` exits 0 with `2026-W21` in stdout;
    - `judgment-pair` exits 0 with `Skill: code-review` in stdout.
- **Prefix test**: add `judgment_pair.py`, `skill_invocation.py`, and `skill_pair.py` to `PRODUCTION_MODULES`. Add no file to `TEST_FILES`, because the new test files make no `_mod.<module>.<name>` read (row 33).

**Part 5: docs.**
- **`__init__.py`**: add `judgment_pair`, `skill_invocation`, and `skill_pair` to the command-group module list.
- **`docs/transcript-analysis-architecture.md`, stage 1**: add `### \`judgment_pair.py\``, `### \`skill_invocation.py\``, and `### \`skill_pair.py\`` after `handoff_signal_response.py`, because the drift test requires a heading per module (row 36). Each section states:
  - responsibilities;
  - module imports;
  - that its `_add_<command>_subparser` is the one name `build_parser()` reaches bare;
  - that the shim's `cmd_*` binding exists only for test files.

  The `skill_invocation.py` section also says `_normalize_skill_name` has its one definition there and `review_trace.py` reads it, and that the `/slash` pattern is `review_rounds._SLASH_COMMAND_RE`.
- **Stage 2 doc edits**:
  - Add the three modules to the list at :16–19.
  - After :48, add: "`build_parser()` registers judgment-pair, skill-invocation, and skill-pair by calling each owning module's `_add_<command>_subparser` from the shim at that command's place in registration order. Each module's own registration binds `set_defaults(func=...)` to its own handler."
  - After :32, add: "`review_trace.py` also imports `skill_invocation.py`, for its emitted skill label (`skill_invocation._normalize_skill_name`). `judgment_pair.py` imports `review_rounds.py` for `REVIEW_SKILLS`, and `skill_invocation.py` imports it for `_SLASH_COMMAND_RE`."
  - `scope.py` section: add "`scope.py` also owns `_iso_date`, the argparse `type=` validator for every YYYY-MM-DD flag, which `_parse_absolute_window_args` relies on."
  - `render.py` section :127: "the shim's judgment-pair" becomes "`judgment_pair.py`".
  - `review_rounds.py` section: :172–177 becomes "`REVIEW_SKILLS` is read by `judgment_pair.py`, for judgment-pair's `--skills` default, and by the shim's `build_parser()`, for review-round-cost's `--skill` choices. `_round_skill_name` and `_SLASH_COMMAND_RE` are read by `review_trace.py`: `_round_skill_name` for its `REVIEW_TRACE_SKILLS` membership test and `--skill` filter comparison, and `_SLASH_COMMAND_RE` to extract a `/slash`-invoked skill name before `_round_skill_name` normalizes it. `_SLASH_COMMAND_RE` is also `skill_invocation.py`'s `/slash` pattern, and this is its one definition."
  - `review_trace.py` section: add `skill_invocation` to the import list at :231. :236–238 becomes "`_review_trace_session_events` reads `skill_invocation._normalize_skill_name` for the emitted display label's directory-only strip."
  - Tests section: add `_skill_use_rec` to conftest's shared list at :477–480.
  - Tests section: replace the harness paragraph at :492–496 with: "`tests/_move_fidelity.py` checks a code move between files: moved-node AST equality modulo a rename map applied to code only, what remains of each source file, byte-compared CLI captures under a pinned environment, a per-subcommand dump of each parser's argparse configuration, and per-test outcomes across junit XML runs. Its seeded-defect self-test is split by subject: `tests/test_transcript_move_fidelity_test_ids.py` covers `test-ids`, `tests/test_transcript_move_fidelity_parser.py` covers the parser dump and `compare-captures --moved-callable`, and `tests/test_transcript_move_fidelity.py` covers the rest. Run the module with `--help` for usage."
  - Tests section: add a skill-group paragraph naming the three test files and their seams, and stating that they load no shim. It notes that `tests/_skill_helpers.py` is a plain module that the legacy file also imports, as `from ._skill_helpers import ...` (see `.claude/rules/test-tree-packaging.md`). It also explains that `TestIsFreshUserPrompt` stays in the legacy file because it tests `render.py`.

**Alternatives set aside.** Mechanism-level alternatives appear in M1–M22. At the whole-plan level:
- One `skills.py` (about 610 lines) would fit the size target, but the three commands share no helper. Precedent splits at exactly that seam (row 40).
- Keeping shim loaders in the new test files would follow the dispatch-phase precedent. The prep item exists so that moved files drop them (row 6).

**Open decisions: none.** The two engineer answers settle block placement and dispatch order. Every other choice has a precedent- or read-backed default, recorded below.

### Assumption ledger

**Root:** The skill group still lives in the shim: `cmd_judgment_pair`, `_normalize_skill_name`, `cmd_skill_invocation`, `cmd_skill_pair`, and three non-contiguous `add_parser` blocks. Its roughly 1,050-line test slice lives in the legacy file. The package carries two re-expressions of shim code: `review_trace._normalize_skill_name`, and `review_rounds._SLASH_COMMAND_RE` against the shim's inline literal. `review_rounds.py` cites four drifted shim line numbers. The harness cannot see an argparse default, type, dest, or handler binding. This PR moves the group with byte-identical CLI output and an unchanged parse configuration, leaving one definition per name. `[verified: transcript-analysis.py:891-1375, 4226-4349; review_trace.py:36-46; review_rounds.py:48-59; _move_fidelity.py:16-17]`

**Givens:**
- G1. The shim keeps `build_parser()`, `main()`, and every unmigrated `cmd_*` until the `cli.py` phase. Reason: the governing plan's phase order owns this. `[verified: transcript-analysis-decomposition.md:139, 155-156]`
- G2. pytest's `prepend` import mode makes test basenames global, so new files keep unique `test_transcript_*` names. Reason: pytest owns this behavior. `[verified: transcript-analysis-decomposition.md:59-63]`
- G3. A monkeypatch reaches code only through the binding that code reads at call time. Reason: Python name-binding semantics. `[verified: scope.py:5-10]`
- G4. Agents run `select-tests.py`, not the full suite, outside CLAUDE.md's two named cases. Reason: this repo's CLAUDE.md § Commands imposes it. `[verified]`
- G5. Committed `.py` text carries no tracker IDs. Reason: a repo test imposes it. `[unverified — relayed from decomposition-prep.md row 20]`
- G6. The venv and CI run Python 3.12, whose `tokenize` is the C tokenizer. Reason: the venv and CI config own the interpreter. `[verified: /usr/lib/python3.12/tokenize.py:488-494; tests.yml:127; interpreter version relayed from decomposition-prep.md row 28]`
- G7. argparse exposes no public accessor for actions, defaults, or mutex groups, so the dump reads `_actions`, `_defaults`, and `_mutually_exclusive_groups`. Reason: the stdlib owns the API. `[verified: /usr/lib/python3.12/argparse.py:1414-1417, 1697-1710]`

**Rows:**
1. The Ask. `[engineer-verified: "let's do plan-it for the skill group"]`
2. This phase moves its three `add_parser` blocks into the package. `[engineer-verified: "Move the blocks (Recommended)"]` The option's description was the session's own proposal and is not covered by this tag.
3. The parser-introspection check lands as this PR's first dispatch. `[engineer-verified: "In this PR, first dispatch (Recommended)"]` The option's description was the session's own proposal and is not covered by this tag.
4. The tracker defines the group as skill-invocation, skill-pair, and judgment-pair. It names the `_normalize_skill_name`/`_SLASH_COMMAND_RE` duplicates and the four drifted citations as this phase's work. `[unverified — relayed: quoted in this plan's Context; I could not read GH-1116]`
5. "Deletes the shim duplicates" means one definition per name. Which copy survives is a design choice: the surviving copy is the one whose home keeps imports flowing from command module to shared module. `[unverified: interpretation of the tracker wording]`
6. The tracker requires three things. Starting with the skill group, each phase moves its blocks into the owning module in registration order so `--help` stays byte-identical. Each module and test file targets 1,000 lines. The prep item retargeted the fixtures "so each moved test file drops its shim loader as its subject moves". `[verified: decomposition-prep.md:11, quoting the tracker]`
7. Registration order is …, pr-link, skill-pair, commit-gate, skill-invocation, review-trace, judgment-pair, audit-routing, …, so the group is not contiguous. `[verified: transcript-analysis.py:4216-4351]`
8. Counts and dispatch:
   - The shim has 42 `add_parser(` and 42 `set_defaults(func=` calls; the package has none.
   - `main()` dispatches through `parsed.func(parsed)`.
   - `_SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR` names none of the three commands.

   `[verified: grep; transcript-analysis.py:5193-5230; scope.py:699-705]`
9. `add_parser` stores each subparser in `_name_parser_map` in call order, and nothing ties a subparser to the calling module. `set_defaults` stores into `_defaults`, which `parse_args` copies onto the namespace. Composing `add_parser` calls from separate modules is therefore plain method calls on one action object. That settles the governing plan's `[unverified]` at :222. `[verified: argparse.py:1211-1249, 1439-1440, 1934-1936]`
10. `_iso_date` is defined only in the shim, at :318. It is the `type=` of 12 flags across 7 subparsers, judgment-pair's `--since`/`--until` among them. `[verified: grep, 12 occurrences of "type=_iso_date"]`
11. argparse reports an `ArgumentTypeError` as `str(err)`, independent of the type function's module, so moving `_iso_date` leaves error text unchanged. `[verified: argparse.py:2569-2589]`
12. `scope.py` imports `argparse` but not `datetime`. Its `_parse_absolute_window_args` docstring relies on `type=_iso_date`. `[verified: scope.py:12-40, 1106-1126]`
13. `capture --all-help` behaves as follows:
    - it loads the script under `CAPTURE_TARGET_MODULE_NAME` without registering it;
    - it walks `_actions` for subcommand names;
    - it captures rendered help only.

    A script-defined callable's `__module__` is therefore that name. `[verified: _move_fidelity.py:16-17, 51, 470-543]`
14. judgment-pair's `--truncate-chars` help states "(default: 1000)" as literal text, not `%(default)s`, so changing the default leaves help bytes unchanged. `[verified: transcript-analysis.py:4336-4342]`
15. `nodes`' rename reversal is textual, and `rename-ambiguous` fires when a NEW name occurs anywhere in the before node, docstrings included. `[verified: _move_fidelity.py:14-15, 181-191, 268-283; test_transcript_move_fidelity.py:166-171, 231-252]`
16. Both `cmd_judgment_pair`'s and `cmd_skill_invocation`'s docstrings already name `scope._attributed_branch`. Under the committed harness, the needed rename would therefore report `rename-ambiguous` for both. `[verified: transcript-analysis.py:903-906, 1101-1103]`
17. Within the moved test slices, `scope` occurs only in strings, docstrings, and comments, and `os` never occurs as a bare code token. `[verified: grep of test_transcript_analysis.py; slice hits :6105-6107, :6160, :6204, :7149-7150 are all docstring, string, or comment]`
18. Python 3.12's tokenize defines `FSTRING_START`/`FSTRING_MIDDLE`. `[verified: /usr/lib/python3.12/token.py:67-68]` Names inside a replacement field tokenize as `NAME`. `[unverified: PEP 701 behavior; dispatch 1's f-string seed pins it]`
19. Harness sizes: `_move_fidelity.py` has 795 lines and 27 `findings.append(` sites. `test_transcript_move_fidelity.py` has 924 lines, leaving 76 lines of headroom under 1,000. `[verified: reads and grep]` `test_transcript_move_fidelity_test_ids.py` has 278 lines. `[unverified — relayed from Step 3]`
20. `re.finditer(p, s)` is `_compile(p, flags).finditer(s)`. `review_rounds._SLASH_COMMAND_RE`'s pattern is character-identical to the shim's inline literal, with flags 0. `[verified: /usr/lib/python3.12/re/__init__.py:219-224; review_rounds.py:51; transcript-analysis.py:1183]`
21. `review_trace` uses `_normalize_skill_name` only for the emitted "skill" field, at two sites. Its tests cover both sites with worktree-qualified names. `[verified: review_trace.py:36-46, 324, 374; test_transcript_review_trace.py:127-134, 203-207]`
22. `review_rounds.py` has drifted citations at :49, :59, :142, and :183, and back-import prose at :4–5 and :40–42. `[verified]`
23. Command-group to command-group imports have precedent:
    - `review_trace` imports `reviewer_yield` and `review_rounds`, each for a helper that module's command owns;
    - `subagent_mix` imports `review_rounds`;
    - `author_outcome` imports `review_rounds`.

    `[verified: review_trace.py:17; architecture doc :27-32, :207]`
24. `review_rounds` imports no command module. `_REVIEW_BENCH_SCRIPTS_DEPENDENCIES` lists `review_rounds.py`, `scope.py`, and `render.py` but not `review_trace.py`, and a ground-truth test derives that set from imports. Adding no import to `review_rounds` therefore needs no `select-tests.py` change. `[verified: review_rounds.py:37-38; select-tests.py:74-88]`
25. After the move, shim production code no longer reads seven names: `fnmatch`, `_iter_glob_scoped_sessions`, `_single_level_projects_glob`, `_is_fresh_user_prompt`, `_iter_scoped_sessions`, `_repo_scoped_project_slugs`, and `_resolved_scope_header`. Staying tests read the last four as `_mod.<name>`, at :6884–6923, :8244/:8270/:8398, :6282–6365, and :9401/:9412. `REVIEW_SKILLS` is still read, at :4858. `[verified: grep of the shim and tests]`
26. Staying tests read `_mod.cmd_skill_pair`, `_mod.cmd_skill_invocation`, and `_mod.cmd_judgment_pair`:
    - header rows :7871 and :7891 (no skill-invocation row exists, per :7855–7857);
    - :6678;
    - :7951–8047;
    - :8168–8214;
    - :9165;
    - :9257;
    - :9750–9950.

    No `args.func` or `build_parser()`-structure test names these three commands. `[verified: grep]`
27. Four staying tests patch a shim binding that the moved handler stops reading:
    - `TestRootsThreadingSpy::test_skill_invocation_glob_scope_receives_the_multi_root_list_as_is`
    - `TestRootsThreadingSpy::test_skill_invocation_repo_scoped_call_receives_the_multi_root_list`
    - `TestRootsThreadingSpy::test_single_root_skill_invocation_glob_call_passes_a_bare_path_not_a_list`
    - `TestSingleLevelProjectsGlob::test_skill_invocation_multi_root_rejects_a_multi_segment_projects_value_at_runtime`

    With the shim's imports kept, each fails as an assertion failure: the first two on "was never called", the third on `[] == [...]`, and the fourth on DID NOT RAISE, because the autouse fixture pins one root. `[verified: :8152-8216, :9152-9167; conftest.py:1450-1476]`
28. `TestRootsThreadingSpy`'s parametrized case patches both `_mod` and `_mod.scope`, so the skill-pair and judgment-pair rows keep passing. `[verified: :8098-8104]`
29. The moved slices read only these names from the shim: the three `cmd_*`, `_mod.scope.PROJECTS_DIR`, `_mod.os.getcwd`, and `_mod.REVIEW_SKILLS`. None calls `build_parser()` or `main()`. `[verified: grep of _mod. in the slice ranges]`
30. `fake_projects`, `cost_ledger_file`, and `cost_ledger_enabled` work in a file that loads no shim. `[verified: architecture doc :488-490]`
31. Helper usage across seams:
    - `_TS_FIXED`: :6673;
    - `_skill_pair_args`: :6678, :7871, :9950;
    - `_skill_inv_args`: :7951–8214, :9897;
    - `_judgment_pair_args`: :7891, :9257, :9750–9794;
    - `_skill_use_rec`: review-trace and skill-invocation tests at :7936–8123, plus :9251, :9745, :9782;
    - `_review_asst`/`_user_reply`: judgment-pair cases only, at :9252–9786;
    - `_TS_SUNDAY`/`_TS_MONDAY`: TestSkillPair only;
    - `_attr_ts`: staying attribution classes and :6047–6053.

    `[verified: grep]`
32. Helper-home precedents:
    - family helper modules import `.conftest` and `transcript_analysis` (`_cache_rebuild_helpers.py:8`, `_handoff_nudge_helpers.py:6-8`, `_pr_cost_helpers.py:10`);
    - record builders used by more than one command group live in conftest (`decompose-transcript-dispatch.md:135`);
    - `_attr_ts` was copied locally once before (`test_transcript_subagents.py:45-48`).

    `[verified]`
33. `PRODUCTION_MODULES` lists the 9 modules from the latest phases. `TEST_FILES` checks only `_mod.<module>.<name>` reads. `[verified: test_transcript_package_module_prefixes.py:1-45, 264-282]`
34. The bootstrap file has no test for the three commands. `_seed_review_trace_skill_account` seeds one main-thread `code-review` Skill record at 2026-05-19, which falls in ISO week 2026-W21. `[verified: test_transcript_cli_bootstrap.py:377-409; computed: Monday 2026-05-18 opens W21]`
35. None of the three commands takes `--no-redact`, so the governing plan's multi-root `--no-redact` refusal test has no site here. `[verified: transcript-analysis.py:4226-4349; transcript-analysis-decomposition.md:298-303]`
36. The doc-drift test requires a heading per module. `[unverified — relayed from decomposition-prep.md row 19]` select-tests maps the doc and package changes to `claude/.claude/scripts/tests`. `[verified: select-tests.py:36, 195, 306-308]`
37. The governing plan prescribes a revert rehearsal at each phase boundary: "At each phase boundary, `git revert` the phase's merge commit on a scratch branch and run the full suite." `[verified: transcript-analysis-decomposition.md:276-279]` The engineer dropped the rehearsal for this PR. `[engineer-verified: "Drop it"]` (answer to whether to keep the revert rehearsal); the option's description was the session's own proposal and is not covered by this tag.
38. `skill-fidelity-report.sh` runs `skill-invocation --branches … --include-subagents` through the CLI, which stays unchanged. `[verified: skill-fidelity-report.sh:15-18]`
39. Legacy banners at :1107, :5637, :5928, :6516, and :6835 head the sections listed in Part 4. `[verified: grep]`
40. Of the package's 18 command-group modules, 15 hold one `cmd_*`. The three multi-command modules (cost, subagent_mix, audit_routing) hold commands that share helpers. The dispatch phase split at a no-shared-helper seam. `[verified: grep "^def cmd_"; decompose-transcript-dispatch.md:32, 62-66]`
41. ruff selects E/F/B/I/UP/SIM at line length 130. `[verified: pyproject.toml:2, 6]`
42. `compare-captures --moved-callable` stays in this PR. `[engineer-verified: "compare-captures --moved-callable (Recommended)"]` The option's description was the session's own proposal and is not covered by this tag.
43. The `nodes` code-only rename reversal stays in this PR. `[engineer-verified: "nodes: code-only rename reversal (Recommended)"]` The option's description was the session's own proposal and is not covered by this tag.
44. `_iso_date` moves into `scope.py` in this PR. `[engineer-verified: "Move _iso_date into scope.py (Recommended)"]` The option's description was the session's own proposal and is not covered by this tag.
45. This worktree sits at `.claude/worktrees/GH-1116/decomposition-skill`, four levels below the main checkout, because the branch name contains `/`. From its root, `../../../../.venv/bin/pytest` and `../../../../.venv/bin/ruff` exist, and nothing exists under `../../../.venv`. README.md:523's "exactly three levels deep" holds only for a branch name without `/`. `[verified: Glob of both paths; README.md:523]`
46. Python floors differ by surface. README.md:100 sets `python3` >= 3.11, which `install.sh` checks. The dev tooling targets 3.12: ruff's `target-version` is `py312`, and CI runs `python-version: '3.12'`. `tokenize.FSTRING_MIDDLE` exists only from 3.12 (row 18). A PEP 701-dependent test already skips below 3.12. `[verified: README.md:100; pyproject.toml:3; tests.yml:127; test_plan_push_preflight.py:803]`
47. The harness's `main()` maps only `BadInput` and `OSError` to exit 2. Any other exception escapes as a traceback with exit 1, the findings status. The self-tests call `main()` in-process through `_invoke`, so `monkeypatch` reaches the harness's `tokenize`. `[verified: _move_fidelity.py:40-42, 785-791; test_transcript_move_fidelity.py:24-27]`
48. `capture`'s environment and loader:
    - the child's environment pins `CLAUDE_CONFIG_DIR`, `TRANSCRIPT_CONFIG_DIRS_FILE` (to a file under `--account` that capture never creates), `COLUMNS`, and `PYTHONHASHSEED`, then applies `--env`;
    - `--out` refuses an existing directory;
    - the loader runs `build_parser()` in the harness process under the ambient environment, and restores `sys.path` but leaves imported modules in `sys.modules`;
    - the existing sibling-module seed pops its module in a `finally`.

    `[verified: _move_fidelity.py:443-455, 470-486, 515-516, 531-534; test_transcript_move_fidelity.py:752-777]` Today's `build_parser()` derives no default from the environment. `[unverified — relayed from staff-platform-engineer review]`
49. Multi-root scope works as follows:
    - `TRANSCRIPT_CONFIG_DIRS_FILE` lists absolute config-dir paths, one per line, each needing a `projects/` subdirectory, and `resolve_scan_roots` appends each to `PROJECTS_DIR`;
    - `cmd_skill_invocation` runs its one-level `--projects` refusal (exit 2) and its `_iter_glob_scoped_sessions` branch only when `len(roots) > 1`, and its default reads `_iter_scoped_sessions(_repo_scoped_project_slugs(), …, roots=roots)`;
    - skill-pair and judgment-pair resolve scope through `_resolve_project_scope`;
    - every capture in the previous design ran single-root.

    `[verified: _config_dir.py:89-169; scope.py:433-504; transcript-analysis.py:909-910, 1126-1143, 1297-1298; _move_fidelity.py:448]`
50. `cmd_skill_pair` reads the positional `leader` and `follower`, so bare `skill-pair` exits 2 in argparse before scope resolution. The multi-root capture therefore runs `skill-pair code-review plan-review` where the ciso finding wrote `skill-pair`. `[verified: transcript-analysis.py:1293-1294]`
51. On the `/slash` path, only the `[^<]+` group, which stops at `</command-name>`, keeps `<command-args>` text out of skill-invocation's rows. The captured name then passes through `_normalize_skill_name`'s last-`/` strip, so a pattern widened to end at `</command-args>` captures `x</command-name><command-args>/abs/secret`, which normalizes to `secret`. No file under `claude/.claude/scripts` mentions `<command-args>`. `[verified: transcript-analysis.py:1183-1184; review_rounds.py:51; grep]`
52. Two tests pin the scope branches that controls 8(k) and 8(l) mutate:
    - `TestSingleLevelProjectsGlob::test_skill_invocation_multi_root_rejects_a_multi_segment_projects_value_at_runtime` expects `SystemExit` 2 under two roots, and `_iter_glob_scoped_sessions` itself never raises;
    - `TestSkillInvocationRepoScope::test_scoped_default_reads_only_this_repos_dirs` asserts that another project's skill is absent from the default.

    `[verified: test_transcript_analysis.py:6133-6161, 9152-9167; scope.py:433-467]`
53. `main()` calls `parsed.func(parsed)` with no exception handling. Binding skill-pair's `func` to its registrar therefore crashes each skill-pair run that reaches the handler. `[verified: transcript-analysis.py:5193-5230]`
54. The skill-invocation block builds its own `--projects`/`--this-repo` group and never calls `_add_project_scope_args`. `[verified: transcript-analysis.py:4260-4287]`
55. Rollback depends on names, not only on text:
    - six subparsers outside this group keep `type=_iso_date` (row 10), so a later phase that moves any of them binds `scope._iso_date`;
    - later tests are expected to import `_skill_use_rec` and `_skill_helpers`, and later phases to use the parser dump and `--moved-callable`;
    - later phases also edit the prefix test, the bootstrap file, `__init__.py`, `review_trace.py`, and `review_rounds.py`.

    `[verified: grep for the six subparsers; the rest unverified — relayed from staff-platform-engineer review]` The PR body states the window past a later phase by name. `[inference from row 37]`
56. `gh pr list` returns 30 results unless `--limit` is passed. `[unverified — relayed from staff-platform-engineer review]` The plan's `--limit 500` is a ceiling with a rerun rule, not a vendor-specified value.
57. Stow: `stow-packages.sh` lists `claude -> .`, and `install.sh` passes no `--no-folding`, so `~/.claude/scripts` folds on a fresh install. CPython puts a symlinked script's resolved directory at `sys.path[0]`. An ImportError at the shim's package import fails every subcommand. `[unverified — relayed from staff-platform-engineer and staff-backend-engineer reviews; the sys.path rule was not run]` Whether an unfolded `scripts/` with a symlinked shim still finds new modules is unverified, so the PR body advises `./install.sh` for every unfolded consumer.
58. CI runs one job with `timeout-minutes: 20` and pytest roots `claude/.claude/ claude-skills/ plugins/`, so tests under `evals/` run only through `select-tests.py`. `select-tests.py` targets `evals/test_review_bench*.py`, `evals/test_measure_subagent_model_resolution.py`, and `claude/.claude/hooks/tests/test_review_ledger_script.py`. `[verified: tests.yml:33, 159, 166; select-tests.py:61, 69, 207, 222]` That this diff selects them is `[unverified — relayed from staff-platform-engineer review]`.
59. Dispatch 2 edits `docs/transcript-analysis-architecture.md` in both stages, and the previous snapshot list omitted it. A restart that re-adds the stage-1 headings would pass, because the drift test keys on headings as a set. `[verified: this plan's Critical files; drift-test behavior unverified — relayed from staff-backend-engineer review]`

**Mechanisms:**
- M1: The parser dump inside `capture --all-help`. `anchors: row3, row13, row14, row48`. Lighter primitives fail:
  - Help-byte parity alone cannot see defaults, types, hidden dests, or handlers (rows 13–14).
  - A runs file with one argv per flag cannot observe a `type` callable's identity or a handler binding, and every phase would re-author it.
  - An in-process before/after test has no before state once the move lands.
  - A golden snapshot would need regenerating on every parser edit.
- M2: `compare-captures --moved-callable`, rewriting DIR_A only. `anchors: row8, row10, row13`. Alternatives:
  - Lighter, rejected: `--mask`, which is symmetric. A mask that equates `<script>:h` with `mod:h` also hides a stale `<script>:h` left on the after side.
  - Heavier, rejected: a structured `compare-parsers` subcommand.
- M3: Code-only rename reversal. `anchors: row15, row16, row17, row18`. Lighter primitives fail:
  - Hand-editing scratch inputs to neutralize the two docstrings is a per-phase manual step, and the loader-free `_mod.scope=scope` map hits it again in strings.
  - Dropping the ambiguous entries leaves both large handlers unverified.
  - Binding names by name in the new modules breaks the attribute-access rule (G3).
- M4: The new parser self-test file and the three-part stopping rule. `anchors: row6, row19, row47, row48`
- M5: Per-command registrars, called at the original positions. `anchors: row2, row6, row7, row8, row9, row54`. Lighter primitives fail:
  - Leaving the blocks in place contradicts row 2.
  - A module function that only adds arguments to a subparser the shim creates splits one command's definition across two files and leaves the `add_parser` call behind.

  Heavier, set aside: one registration call followed by reordering argparse's private maps, and a registrar registry, which the governing plan rejected at :220.
- M6: Three modules split at command seams. `anchors: row40`
- M7: `_iso_date` moves to `scope.py`. `anchors: row10, row11, row12, row44`. Alternatives:
  - Moving it into `judgment_pair.py` puts a 7-subparser validator in one command's module.
  - A registrar parameter would exist only for the transition.
  - A duplicate violates single source of truth.
- M8: `_normalize_skill_name`'s one home is `skill_invocation.py`; `_SLASH_COMMAND_RE`'s is `review_rounds.py`. `anchors: row5, row20, row21, row23, row24`. Alternatives:
  - The regex in `skill_invocation.py` would make round detection import a command module and add a select-tests dependency (row 24).
  - Both names in `render.py` would edit a leaf beyond a move (dispatch M3 precedent).
  - `_normalize_skill_name` in `review_rounds.py` would be a misplaced helper, since its docstring states skill-invocation's minimization rule.
- M9: Convert the citations and prose. `anchors: row22`
- M10: Shim imports, staged lint-dirty and then final. `anchors: row25, row26, row27`
- M11: Loader-free test files with the test rename map. `anchors: row6, row17, row29, row30`
- M12: The seven staying-test retargets. `anchors: row27, row28`
- M13: Helper homes. `anchors: row31, row32`
- M14: Prefix-test opt-in for production modules only. `anchors: row33`
- M15: Six bootstrap tests. `anchors: G6, row34`
- M16: Doc edits. `anchors: row36`
- M17: Dispatch 1 (harness), the parent's baseline, then one dispatch in two stages with a stage-1 scratch snapshot. `anchors: row3, row27`. Sequenced dispatches for stages 1 and 2 were rejected because both stages edit the shim, `skill_invocation.py`, and the architecture doc.
- M18: No `select-tests.py` change, and a rollback window stated by name dependency. `anchors: row24, row36, row37, row55, row56`. The revert rehearsal the governing plan prescribes is not run in this PR (row 37).
- M19: A second, multi-root capture. `anchors: row49, row50`. Lighter primitives fail:
  - The retargeted legacy tests alone patch bindings, and row 27's fourth test shows such a test reads DID NOT RAISE when it patches the wrong one.
  - Adding the runs to the single-root runs file cannot change `TRANSCRIPT_CONFIG_DIRS_FILE`, because `--env` applies per capture (row 48).
- M20: The `<command-args>` exclusion test, its widening control, and the name-only regex comment. `anchors: row51`. Lighter primitives fail:
  - The comment alone enforces nothing.
  - Control 8(h) breaks the closing tag, which never widens the capture group.
- M21: The Python-floor guard on `nodes --rename`. `anchors: row43, row46, row47`. Alternatives:
  - A docstring-only floor leaves a 3.11 run exiting 1 with a traceback, which reads as findings (row 47).
  - Refusing only nodes that hold an f-string needs its own tokenize pass for an interpreter the dev tooling does not target (row 46).
  - Skip markers on the rename seeds are in Out of scope.
- M22: Byte-exact restore sources for every tracked file dispatch 2 edits, and a fresh `<attempt>` directory per dispatch-2 attempt. `anchors: row48, row59`

## Critical files

**Dispatch 1: `code-writer`, the harness.** It owns only these files:
- Modify `claude/.claude/scripts/tests/_move_fidelity.py` (Part 1).
- Modify `claude/.claude/scripts/tests/test_transcript_move_fidelity.py`: the rename seeds, the Python-floor seed, and the docstring sentence.
- Modify `claude/.claude/scripts/tests/test_transcript_move_fidelity_test_ids.py`: the docstring sentence only.
- Create `claude/.claude/scripts/tests/test_transcript_move_fidelity_parser.py`.

Its brief requires:
- every file at or under 1,000 lines; parametrize shared shapes, and if the rename seeds would still push `test_transcript_move_fidelity.py` past 1,000, stop and return;
- no ticket or line number in `.py` text;
- `../../../../.venv` as the interpreter path, because this worktree is four levels deep (row 45); if `../../../../.venv/bin/pytest` or `…/ruff` is missing, stop and return, and never substitute another interpreter.

Its return lists every added or renamed test ID. It also maps each `findings.append(` site, each `raise BadInput` site in stopping-rule condition 2, and each branch in condition 3 to its seed or Verification 1 control.

Its verification, from the worktree root:
- `../../../../.venv/bin/pytest claude/.claude/scripts/tests/test_transcript_move_fidelity.py claude/.claude/scripts/tests/test_transcript_move_fidelity_test_ids.py claude/.claude/scripts/tests/test_transcript_move_fidelity_parser.py`
- `../../../../.venv/bin/ruff check claude/.claude/scripts/tests/`
- `../../../../.venv/bin/python3 claude/.claude/scripts/tests/_move_fidelity.py --help`
- `wc -l` on the four files

**Parent, between the dispatches:** Verification 1, `/code-review`, and the commit, then Verification 2. A review-round edit to `_move_fidelity.py` or its seeds re-runs Verification 1 before the commit.

**Dispatch 2: `code-writer`, the move.** It must not touch dispatch 1's files. The parent passes it a fresh `<attempt>` directory, and the brief uses `../../../../.venv` with the same stop rule as dispatch 1.

Stage 1, Part 2. It owns these files and must stop after them:
- Create `claude/.claude/scripts/transcript_analysis/judgment_pair.py`, `…/skill_invocation.py`, and `…/skill_pair.py`.
- Modify `claude/.claude/scripts/transcript_analysis/scope.py`: `_iso_date`, the datetime import, and the `_single_level_projects_glob` docstring pointer.
- Modify `claude/.claude/scripts/transcript-analysis.py`: deletions, three registrar calls, and the stage-1 imports.
- Modify `docs/transcript-analysis-architecture.md`: the three module sections only.

At the end of stage 1, the code-writer:
- runs `../../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py -m "not timing" --junitxml=<attempt>/stage1-parallel.xml`; every selected test passes except exactly the four row-27 IDs;
- runs `ruff check`, whose findings must be exactly row 25's seven F401s in the shim;
- copies the shim and the four production files to `<attempt>/stage1/`;
- reports all three results, and stops before stage 2 and returns if any deviates: a fifth failing test, an `<error>` in place of a `<failure>`, or a lint set other than the seven F401s.

Stage 2, Parts 3–5:
- Modify `claude/.claude/scripts/transcript_analysis/skill_invocation.py`, `…/review_trace.py`, `…/review_rounds.py`, `…/__init__.py`, and `claude/.claude/scripts/transcript-analysis.py`.
- Create `claude/.claude/scripts/tests/test_transcript_skill_invocation.py`, `…/test_transcript_skill_pair.py`, `…/test_transcript_judgment_pair.py`, and `…/_skill_helpers.py`.
- Modify `claude/.claude/scripts/tests/test_transcript_analysis.py`, `…/conftest.py`, `…/test_transcript_cli_bootstrap.py`, `…/test_transcript_package_module_prefixes.py`, and `docs/transcript-analysis-architecture.md`.
- Stage 2 does not edit `judgment_pair.py`, `skill_pair.py`, or `scope.py` (Verification 7 compares them byte for byte with stage 1).

Stage 2 verification:
- `../../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`
- `../../../../.venv/bin/ruff check claude/.claude/ claude-skills/`, clean
- `wc -l` on every new file

If ruff F821 flags a legacy-local name that row 31 does not list, stop and report. The brief also forbids staging.

**Abort procedure.** If dispatch 2 is interrupted, the code-writer returns and cleans nothing. The parent then:
1. lists paths with `git status --short`. Every modified path must be one of Verification 2's ten snapshotted files. Every untracked path must be the plan file or one of the seven files dispatch 2 creates: the three modules, the three test files, and `_skill_helpers.py`. If any other path appears, stop and report, touching nothing;
2. copies each Verification 2 snapshot back over its path, the architecture doc included;
3. when Verification 4 has run, unstages each created file with `git restore --staged -- <path>`, one literal call each, then deletes it by its literal path;
4. checks success: `cmp` of each of the ten files against its snapshot exits 0, and `git status --short` lists nothing except, at most, the plan file.

It never uses `git clean`, `git checkout .`, `git reset --hard`, or `git stash`, and it runs each git write as its own literal call. Dispatch 1 has no snapshots: its three modified files are clean at `HEAD`, so its abort restores each from `HEAD` and deletes the created file by literal path, and succeeds when `git status --short` lists nothing except, at most, the plan file. After a dispatch-2 abort, the parent creates a fresh `<attempt>` and restarts from stage 1. Verification 0–2's outputs in `<scratch>` stay valid, because dispatch 2 never commits or stages, so `HEAD` is unchanged.

**Also in the diff:** `.claude/plans/decomposition-skill.md`.

**Explicitly unchanged:**
- `select-tests.py` and `test_select_tests.py` (row 24);
- `docs/transcript-analysis.md` and the transcript-analysis `SKILL.md`, since the CLI is identical;
- `skill-fidelity-report.sh`;
- `render.py`, `corpus.py`, `pricing.py`, `redaction.py`, and every other package module;
- `test_transcript_review_trace.py`, `test_transcript_review_rounds.py`, `test_transcript_subagents.py`, and `test_transcript_shared_fixtures.py`;
- `README.md`, including its three-level venv claim (Out of scope).

**Reuse:**
- `scope._add_project_scope_args`;
- the harness's `_parse_key_value_pairs`, `_emit`, `BadInput`, and the tokenize pass in `_comment_tokens`;
- `subcommand_names`' `_SubParsersAction` walk, refactored into one loader;
- `_invoke`, `_checks`, `_write`, and `_write_capture`, copied into the parser self-test;
- the `sys.modules` cleanup at `test_transcript_move_fidelity.py:774-777`;
- conftest's `_asst`, `_skill_use`, `_user_msg`, `_write_jsonl`, `_table_cols`, and `fake_projects`;
- `TestSkillInvocationRepoScope::test_explicit_projects_never_invokes_git` as the shape for the `<command-args>` test;
- bootstrap's `_run` and `_seed_review_trace_skill_account`;
- `tests/_subagent_helpers.py:1-3` for the helper-module docstring shape.

**PR body carries:**
- each Verification result, including stage 1's four expected failures, the harness's compared-node counts and both open-PR check results;
- measured `wc -l` for every new or shrunk file, flagging files already over 1,000 lines: the shim, the legacy file, conftest, and `scope.py`;
- the wrap-only reflow counts;
- the CI job's measured duration against its 20-minute cap (row 58), added by a body edit after CI completes, with the stow line kept;
- the rollback statement: "A plain `git revert` of the squash commit restores the pre-PR tree until a later phase merges code that references a name this PR adds, including `scope._iso_date`, conftest's `_skill_use_rec`, `tests/_skill_helpers.py`, the harness's parser dump, `--moved-callable`, code-only `nodes --rename` reversal, or Python 3.12 guard, or the three new modules and their `_add_<command>_subparser` registrars. After that, fix forward. Later phases also edit the shim's import blocks, the legacy file, conftest, the architecture doc, `test_transcript_package_module_prefixes.py`, `test_transcript_cli_bootstrap.py`, `transcript_analysis/__init__.py`, `review_trace.py`, and `review_rounds.py`, so a revert after one of them conflicts there too. Cross-phase reverts run last-in-first-out.";
- the dependency statement: "Later decomposition phases depend on this PR for the parser dump, `--moved-callable`, the code-only rename reversal, the registrars, and `scope._iso_date`. Rebase them on it.";
- the stow line: "The three new modules live under `claude/.claude/scripts/transcript_analysis/`, which the shim imports from its own resolved directory, so a shim reached through a folded `~/.claude/scripts` symlink finds them after `git pull`. If your `~/.claude/scripts` is a real directory rather than that folded symlink, re-run `./install.sh`. Until the new modules are reachable, the shim's package import fails, and every subcommand fails with it, not only these three." (row 57);
- the follow-ups listed in Out of scope.

## Verification

Run everything from the worktree root. Notation:
- `<venv>` is `../../../../.venv`. The depth depends on the branch name: this worktree sits at `.claude/worktrees/GH-1116/decomposition-skill`, four levels below the main checkout, because the branch name contains `/`. A worktree for a branch name without `/` is three levels deep (row 45).
- `<mf>` is `<venv>/bin/python3 claude/.claude/scripts/tests/_move_fidelity.py`.
- `<scratch>` is a directory `mktemp -d` creates once, before step 0. Read its printed path and use it literally. It holds the baselines.
- `<attempt>` is a directory `mktemp -d` creates before each dispatch-2 attempt. It holds `stage1/`, `stage1-parallel.xml`, `after*`, `multi-after`, and `ctl-*`, so a restart never meets an earlier attempt's output, and `capture --out` never meets an existing directory (row 48).

Run each git command and each capture as its own literal call. Seed files with the Write tool. Seeded names, `git worktree list` output, and captures stay in scratch. The parser dump runs under the parent's ambient environment, not capture's pinned one (row 48), so run every capture from the same session.

0. **Preconditions and harness baseline, before dispatch 1.**
   - `ls <venv>/bin/pytest <venv>/bin/ruff` exits 0. If it fails, stop and report, and never substitute another interpreter.
   - Run `<venv>/bin/pytest claude/.claude/scripts/tests/test_transcript_move_fidelity.py claude/.claude/scripts/tests/test_transcript_move_fidelity_test_ids.py --junitxml=<scratch>/harness-before.xml`.
   - Record `grep -n "raise BadInput" claude/.claude/scripts/tests/_move_fidelity.py` into `<scratch>/badinput-before.txt` with the Write tool.
1. **Harness gate, after dispatch 1.**
   - Rerun the three self-test files with `--junitxml=<scratch>/harness-after.xml`. Then run `<mf> test-ids --before <scratch>/harness-before.xml --after <scratch>/harness-after.xml --file-prefix claude/.claude/scripts/tests/ --rename 'test_nodes_reversal_is_textual_so_a_new_name_only_in_a_string_or_comment_passes_as_a_used_rename=test_nodes_does_not_reverse_a_new_name_inside_a_string_or_comment'` with one `--expect-new` per added ID from the return. It must be clean.
   - Check the return's map against `grep -n "findings.append(" claude/.claude/scripts/tests/_move_fidelity.py` and against the new lines of `grep -n "raise BadInput"` compared with `<scratch>/badinput-before.txt`. Every site and every stopping-rule branch must map to a seed or a control below.
   - Mutation controls, each a hash-guarded edit to `_move_fidelity.py`, run one at a time with `sha256sum` files under `<scratch>`. Each must fail its matching seed, and `sha256sum -c` must pass after the undo:
     - render callables by `repr()`;
     - iterate sets unsorted;
     - drop the `<script>` substitution;
     - rewrite `--moved-callable` on both sides;
     - rewrite non-`.parser` files;
     - restore textual reversal;
     - treat whole f-strings as literals;
     - drop `mutex` lines;
     - drop `default` lines;
     - emit `default` lines in insertion order;
     - render a subparsers action's `choices` by the generic rules;
     - drop the `dest=` field from `action` lines;
     - drop `FSTRING_MIDDLE` from the reversal's span tokens.
2. **Move baseline, after dispatch 1's commit.**
   - Precondition: no open PR touches any of the ten snapshotted files below or dispatch 1's four harness files. A hit, or a failed `gh` call, stops the run and goes to the engineer. Check with `gh pr list --state open --limit 500 --json number,files`. If it returns 500 entries, rerun with a higher `--limit` (row 56). If `origin/main` moved and touched a Critical file, sync first. A sync after this step that touches a Critical file stops the run and goes to the engineer.
   - `git status --short` must be clean apart from the plan.
   - Snapshot from `HEAD` into `<scratch>`: `before-shim.py`, `before-scope.py`, `before-review_rounds.py`, `before-review_trace.py`, `before-__init__.py`, `before-legacy.py`, `before-conftest.py`, `before-bootstrap.py`, `before-prefix.py`, and `before-architecture.md` (the architecture doc). These ten files are the abort procedure's restore sources. Also create an empty `<scratch>/empty.py`.
   - Seed `<scratch>/account/projects/-home-user-parityrepo/s1.jsonl`. Every record is on 2026-05-19 and on branch `feat`. The records are:
     - (r1) a main-thread `Skill` `code-review`;
     - (r2) a text-only assistant reply;
     - (r3) a fresh user prompt;
     - (r4) a user record containing `<command-name>/plan-review</command-name>`;
     - (r5) a main-thread `Skill` `plan-review` with `attributionSkill: "code-review"`;
     - (r6) a sidechain `Skill` `.claude/worktrees/feat/claude:handoff`;
     - (r7) a main-thread `Skill` `.claude/worktrees/feat/claude:skill-review`.
   - Seed one more project directory, named by the slug `scope._path_to_project_slug` gives this worktree's path as `git worktree list --porcelain` prints it, holding one `code-review` Skill record.
   - Write `<scratch>/runs.txt` with:
     - `skill-invocation --projects '*'`
     - the same with `--include-subagents`
     - the same with `--branches feat`
     - the same with `--this-repo`, which exits 2
     - `skill-pair code-review plan-review`
     - the same with `--exclude-projects '-home-user-*'`
     - `skill-pair`, which exits 2
     - `judgment-pair`
     - `judgment-pair --skills code-review --truncate-chars 5 --since 2026-05-19 --until 2026-05-19`
     - `judgment-pair --since 2026-13-01`, which exits 2
     - `user-input --since 2026-13-01`, which exits 2
     - `review-trace`
     - `skill-invocation`, run from the worktree root
   - Capture twice: `<mf> capture --script claude/.claude/scripts/transcript-analysis.py --account <scratch>/account --runs <scratch>/runs.txt --all-help --out <scratch>/before-a`, then the same into `before-b`. `<mf> compare-captures <scratch>/before-a <scratch>/before-b` must be clean; otherwise add a `--mask` and record it.
   - `before-a` must hold `help-top` plus one `help-<name>` capture and one `.parser` file per subcommand. The count is `grep -c "add_parser(" claude/.claude/scripts/transcript-analysis.py`, which is 42 at plan time; the grep governs.
   - `help-skill-pair.parser`, `help-skill-invocation.parser`, and `help-judgment-pair.parser` each contain `default func=<callable <script>:cmd_<name>>`.
   - Corpus liveness: `before-a` holds a token seeded only by `s1.jsonl` in each corpus-reading run: `plan-review` and `claude:skill-review` in `run-01`, `claude:handoff` also in `run-02`, `2026-W21` in `run-05`, `Skill: code-review` in `run-08`, and `claude:skill-review` in `run-12`. If one is absent, read the output, then fix the seed (or the expected token if the seed is right) and recapture. Without this, a mis-seeded corpus makes every comparison equal on empty output.
   - Default-scope precondition: `before-a/run-13.stdout` contains `code-review` and none of `plan-review`, `skill-review`, or `handoff`. If it fails, the seeded slug does not match `git worktree list`; fix the seed and recapture.
   - **Multi-root corpus.**
     - Seed `<scratch>/account2/projects/-home-user-foreignrepo/f1.jsonl` with one main-thread `Skill` `foreign-only-probe` record on 2026-05-19, branch `feat`.
     - Write `<scratch>/roots.txt` holding one line: the absolute path of `<scratch>/account2`.
     - Write `<scratch>/multi-runs.txt` with:
       - `skill-invocation --projects 'a/b'`, which exits 2
       - `skill-invocation --projects '*'`
       - `skill-invocation`, run from the worktree root
       - `skill-pair code-review plan-review` (row 50)
       - `judgment-pair`
     - Capture twice: `<mf> capture --script claude/.claude/scripts/transcript-analysis.py --account <scratch>/account --runs <scratch>/multi-runs.txt --env TRANSCRIPT_CONFIG_DIRS_FILE=<scratch>/roots.txt --out <scratch>/multi-before-a`, then the same into `multi-before-b`. `<mf> compare-captures <scratch>/multi-before-a <scratch>/multi-before-b`, with step 2's masks, must be clean.
     - Preconditions on `multi-before-a`: `run-01.exit` holds `2` and `run-01.stderr` contains `skill-invocation: --projects:`; `run-02.stdout` contains `foreign-only-probe`, which proves the second root is read; `run-03.stdout` contains `code-review` and not `foreign-only-probe`.
   - Run `<venv>/bin/python3 claude/.claude/scripts/select-tests.py -m "not timing" --junitxml=<scratch>/before-parallel.xml` and `… -m timing -n0 --junitxml=<scratch>/before-timing.xml`. Everything passes.
   - Record `wc -l` for every file in this step's snapshot list.
3. **Stage-1 control.**
   - `<mf> test-ids --before <scratch>/before-parallel.xml --after <attempt>/stage1-parallel.xml --file-prefix claude/.claude/scripts/tests/` reports exactly four `id-outcome` findings (passed→failure), naming row 27's IDs.
   - Each of the four is a `<failure>` element, not an `<error>`, in the XML.
   - Each `<failure message=...>` string matches row 27's predicted reason: the first two name the spy that was never called, the third shows `[] == [...]`, and the fourth says DID NOT RAISE.
   - `<attempt>/stage1/` holds the five copied files.
4. **Scoped suite, final tree.**
   - Stage the created files with `git add -- <paths>` first.
   - Run step 2's two `select-tests.py` commands, writing `<attempt>/after-parallel.xml` and `<attempt>/after-timing.xml`. Everything passes.
   - The selection must include:
     - the three move-fidelity self-tests;
     - `test_transcript_analysis_architecture_doc.py`;
     - `test_transcript_package_module_prefixes.py`;
     - `test_transcript_cli_bootstrap.py`;
     - `test_transcript_review_trace.py`;
     - `test_transcript_review_rounds.py`;
     - `test_skill_fidelity_report.py`;
     - `claude/.claude/tests/test_pytest_collection_config.py`;
     - `claude/.claude/hooks/tests/test_ticket_reference_discipline.py`;
     - `claude/.claude/hooks/tests/test_review_ledger_script.py`;
     - `evals/test_review_bench*.py` (six files at plan time) and `evals/test_measure_subagent_model_resolution.py`, which CI's pytest roots never reach (row 58).

     Run any missing file alone. An unmappable path is a rule-table bug, not a reason to widen the run.
5. **Test-ID parity.** `<mf> test-ids --before <scratch>/before-parallel.xml --before <scratch>/before-timing.xml --after <attempt>/after-parallel.xml --after <attempt>/after-timing.xml --file-prefix claude/.claude/scripts/tests/` must be clean, with seven `--expect-new` declarations: one per bootstrap test name, six in all, plus `test_slash_invocation_counts_the_command_name_and_never_its_command_args`. Moved tests need no declaration, because an ID drops its module.
6. **CLI and parser parity.**
   - Capture into `<attempt>/after`. Then run `<mf> compare-captures <scratch>/before-a <attempt>/after --moved-callable cmd_judgment_pair=transcript_analysis.judgment_pair --moved-callable cmd_skill_invocation=transcript_analysis.skill_invocation --moved-callable cmd_skill_pair=transcript_analysis.skill_pair --moved-callable _iso_date=transcript_analysis.scope`, with step 2's masks only. It must be clean.
   - Without the `--moved-callable` flags, the only findings are `capture-bytes` on the `.parser` files of the three moved commands and of every subcommand with a `type=_iso_date` flag. That is nine files at plan time: user-input, subagent-mix, reviewer-yield, skill-pair, skill-invocation, review-trace, judgment-pair, spend-over-threshold, and review-round-cost. `grep -n "type=_iso_date" <scratch>/before-shim.py` governs.
   - No `.stdout`, `.stderr`, `.exit`, or `.argv` file may differ.
   - Capture the multi-root corpus into `<attempt>/multi-after` with step 2's multi-root flags. `<mf> compare-captures <scratch>/multi-before-a <attempt>/multi-after`, with step 2's masks, must be clean.
   - If a repo-scoped run differs, check whether `git worktree list` changed in between, and re-capture both sides if it did.
7. **Move fidelity.**
   - Production nodes: `<mf> nodes --before <scratch>/before-shim.py --after <attempt>/stage1/judgment_pair.py --after <attempt>/stage1/skill_invocation.py --after <attempt>/stage1/skill_pair.py --after <attempt>/stage1/scope.py --after <attempt>/stage1/transcript-analysis.py --moved cmd_judgment_pair _normalize_skill_name cmd_skill_invocation cmd_skill_pair _iso_date --verbatim _normalize_skill_name _iso_date`, plus one `--rename` per entry of Part 2's map (21). It must be clean with `compared-nodes: 5`.
   - Unchanged since stage 1: `cmp <attempt>/stage1/judgment_pair.py claude/.claude/scripts/transcript_analysis/judgment_pair.py`, and the same for `skill_pair.py` and `scope.py`, each exits 0. The stage-1 `nodes` result therefore holds for those three files' final bodies.
   - Shim remainder: `<mf> remainder --before <scratch>/before-shim.py --after <attempt>/stage1/transcript-analysis.py --removed cmd_judgment_pair _normalize_skill_name cmd_skill_invocation cmd_skill_pair _iso_date --changed build_parser`. The added imports are declared as `--import-added transcript_analysis.<m>:<name>` for each registrar and `cmd_*` and for `transcript_analysis.scope:_iso_date`. It must be clean.
   - `git diff` of `build_parser` must show exactly the three blocks replaced by three calls.
   - Final shim: `remainder --before <attempt>/stage1/transcript-analysis.py --after claude/.claude/scripts/transcript-analysis.py --import-removed fnmatch --import-removed transcript_analysis.scope:_iter_glob_scoped_sessions --import-removed transcript_analysis.scope:_single_level_projects_glob` must be clean. The rewritten `REVIEW_SKILLS` comment sits on an import line, so check it in `git diff` by eye.
   - New modules: `remainder --before <scratch>/empty.py --after <module> --added __doc__ <its top-level names> --import-added <its triples>` must be clean for each new production file and for `_skill_helpers.py`. Each new test file reports only the `remainder-comments` finding Part 4 assigns it: the timestamp comment in the skill-pair file and in `_skill_helpers.py`. For `test_transcript_skill_invocation.py`, the top-level names include `test_slash_invocation_counts_the_command_name_and_never_its_command_args`.
   - Dedup in `skill_invocation.py`: `git diff --no-index <attempt>/stage1/skill_invocation.py claude/.claude/scripts/transcript_analysis/skill_invocation.py` changes only the `import re` line, the package-import line, and the `/slash` loop line.
   - `review_trace.py`:
     - `nodes --before <scratch>/before-review_trace.py --after <pkg>/review_trace.py --moved _review_trace_session_events --rename _normalize_skill_name=skill_invocation._normalize_skill_name` reports exactly one `node-comments` finding (the rewritten comment) and no `node-ast`.
     - `remainder … --removed _normalize_skill_name --changed _review_trace_session_events --changed __doc__ --import-added transcript_analysis:skill_invocation` is clean.
   - `review_rounds.py`:
     - `remainder --before <scratch>/before-review_rounds.py --after <pkg>/review_rounds.py --changed __doc__ --changed _round_skill_name --changed _round_open_skill --changed detect_round_windows` reports exactly two `remainder-comments` findings.
     - `git diff` changes only comment and docstring lines.
   - `scope.py`: `remainder --before <scratch>/before-scope.py --after <pkg>/scope.py --added _iso_date --changed _single_level_projects_glob --import-added datetime:datetime` is clean.
   - Tests:
     - `nodes --before <scratch>/before-legacy.py`, with `--after` set to the three new test files, `_skill_helpers.py`, conftest, and the final legacy file, `--moved` the 8 classes plus `_TS_FIXED _TS_SUNDAY _TS_MONDAY _skill_pair_args _skill_inv_args _judgment_pair_args _skill_use_rec _review_asst _user_reply`, `--verbatim` every helper except `_judgment_pair_args`, and Part 4's six renames, is clean with `compared-nodes: 17`.
     - `nodes … --after <tests>/test_transcript_skill_invocation.py --moved _attr_ts --verbatim _attr_ts` is clean.
   - Legacy remainder: `remainder --before <scratch>/before-legacy.py --after <tests>/test_transcript_analysis.py`, with `--removed` the same 17 names, `--changed TestRootsThreadingSpy --changed TestSingleLevelProjectsGlob`, and the import triples. It reports only the banner and moved-comment `remainder-comments` findings, counted against `git diff`. `git diff` inside the two changed classes touches exactly the seven Part 4 lines.
   - `remainder` is clean for conftest (`--added _skill_use_rec`), bootstrap (`--added` the six names), the prefix test (`--changed PRODUCTION_MODULES`), and `__init__.py` (`--changed __doc__`).
8. **Negative controls.** Before each worktree edit, run `sha256sum <file> > <attempt>/ctl-<x>.sha256`. Edit by hand, run the check, undo by hand, then `sha256sum -c` must pass. Never use `git stash` or `git checkout`. Each control capture goes to a fresh `<attempt>/ctl-<x>` directory.
   - (a) Step 3 controls the four retargets.
   - (b) Move `_add_skill_invocation_subparser(sub)` up to directly after `_add_skill_pair_subparser(sub)`. A fresh capture compared against `<attempt>/after` must report `capture-bytes` on `help-top.stdout` and `help-top.parser`.
   - (c) In `judgment_pair.py`, change `--truncate-chars`' `default=1000` to `999`. Use the full capture. Exactly `help-judgment-pair.parser` and `run-08.stdout` must differ, because `run-08` prints the effective default in its `truncated to N chars` header. `help-judgment-pair.stdout`, `run-09`, and `run-10` compare equal (row 14). The `run-08` difference relies on the corpus-liveness precondition in step 2.
   - (d) Bind skill-pair's `set_defaults(func=…)` to `_add_skill_pair_subparser`. A fresh full capture compared against `<attempt>/after` must report `capture-bytes` on exactly these files: `help-skill-pair.parser`, plus `.stdout`, `.stderr`, and `.exit` of `run-05` and `run-06`, the two skill-pair runs that reach the handler and now crash calling the registrar with the namespace (row 53). `help-skill-pair.stdout` and `run-07` compare equal.
   - (e) Re-run step 6's compare without the `_iso_date` declaration. Exactly the seven `_iso_date`-only `.parser` files must differ.
   - (f) In scratch copies of `<attempt>/stage1/skill_invocation.py`, change `rsplit("/", 1)` to `rsplit("/", 2)`; that gives `verbatim` and `node-ast`. Separately, change one word of `cmd_skill_invocation`'s docstring; that gives `node-ast`.
   - (g) Make `skill_invocation._normalize_skill_name` return `raw`. The review-trace qualified-name tests at :127 and :203 and `TestSkillInvocationNameNormalization` must fail.
   - (h) In `review_rounds._SLASH_COMMAND_RE`, change the closing `</command-name>` to `<command-name>`, so no `/slash` record matches. At least one `/slash` test in each of `test_transcript_skill_invocation.py` and `test_transcript_review_trace.py` must fail. If a file has none that fails, stop and report a test gap.
   - (i) In a copy of `<attempt>/after-parallel.xml`, delete one moved `<testcase>` under `claude..claude.scripts.tests.`. `test-ids` must report `id-missing`.
   - (j) `remainder --before X --after X` is clean for the final shim and the legacy file.
   - (k) In `skill_invocation.py`, delete the `try`/`except` block around `scope._single_level_projects_glob`, so the multi-root branch goes straight to `scope._iter_glob_scoped_sessions`. `TestSingleLevelProjectsGlob::test_skill_invocation_multi_root_rejects_a_multi_segment_projects_value_at_runtime` must fail (row 52).
   - (l) In `skill_invocation.py`, replace the default branch's `scope._iter_scoped_sessions(scope._repo_scoped_project_slugs(), include_subagents, roots=roots)` with `scope._iter_glob_scoped_sessions(roots, "*", include_subagents)`. `test_transcript_skill_invocation.py::TestSkillInvocationRepoScope::test_scoped_default_reads_only_this_repos_dirs` must fail (row 52).
   - (m) Widen `review_rounds._SLASH_COMMAND_RE` to `r"<command-name>/(.+?)</command-args>"`. `test_slash_invocation_counts_the_command_name_and_never_its_command_args` must fail on its `secret` assertion (row 51).
9. **Leftovers and single homes.**
   - `git grep -nE "^def (cmd_judgment_pair|cmd_skill_invocation|cmd_skill_pair|_normalize_skill_name|_iso_date)\b" -- claude/.claude/scripts` returns one hit per name, each in its owning module.
   - `git grep -nF "<command-name>/([^<]+)</command-name>" -- claude/.claude/scripts` returns one hit, in `review_rounds.py`.
   - `git grep -nE "transcript-analysis\.py:[0-9]" -- claude/.claude/scripts/transcript_analysis/review_rounds.py` returns nothing.
   - `git grep -n "may not import back from the shim" -- claude/.claude/scripts/transcript_analysis` returns nothing.
   - `git grep -n "_mod" --` the three new test files and `_skill_helpers.py` return nothing.
   - `git grep -n "from _config_dir import" --` the three new modules return nothing, so `fake_projects`' four bindings stay complete.
   - `git grep -nE "_mod\.(cmd_skill_pair|cmd_skill_invocation|cmd_judgment_pair)\b" -- claude/.claude/scripts/tests` returns only row 26's staying sites.
10. **Lint.** `<venv>/bin/ruff check claude/.claude/ claude-skills/` is clean.
11. **Sizes.** Report `wc -l` for every new and changed file. Every new file must be at most 1,000 lines.
12. **Open-PR re-check, before `/ready-for-review` hands off.**
    - Repeat step 2's open-PR check with the same `--limit` rule. If `origin/main` moved and touched a Critical file, stop and report to the engineer instead of re-verifying in place. A failed `gh` call stops this step too.

## Out of scope

- **A discovery-based `fake_projects` completeness guard.** No new module binds `config_dir` by name (Verification 9), so the four pinned bindings stay complete. It remains a recommended follow-up.
- **Harness limits this phase does not hit:**
  - `remainder` ignores import-line comments, so Verification 7 checks the one rewritten import-line comment by eye.
  - `remainder` matches comment text, not where a comment attaches.
  - `--file-prefix` drops a collection-error testcase whose classname is `""`. A moved file that fails to collect still shows `id-missing` for every test it held.
  - `--expect-new` is blind to outcome. Verification 4 requires every selected test to pass, which covers the seven declared tests.
  - The parser dump loads `build_parser()` under the ambient environment, not capture's pinned one. Today's blocks derive no default from the environment (row 48), and the module docstring states the limit. A later phase whose block computes a default from the environment must revisit it.
- **Skip markers that let the rename seeds pass on Python 3.11.** The dev tooling targets 3.12 (row 46), and the exit-2 guard names the requirement on an older interpreter.
- **README.md:523's "exactly three levels deep" venv claim**, which is wrong for slash-named branches (row 45). README is not a Critical file. Raise it as a follow-up.
- **Consolidating `cmd_skill_invocation`'s inlined multi-root branch with `_resolve_project_scope`'s.** This phase is a behavior-preserving move. Raise it as a follow-up.
- **The parametrized multi-root `--no-redact` refusal test.** None of the three commands carries the guard (row 35).
- **Folding `_round_skill_name`'s `rsplit("/")` step onto `skill_invocation._normalize_skill_name`.** It would make `review_rounds` import `skill_invocation`, which already imports `review_rounds` for `_SLASH_COMMAND_RE`, creating a cycle. Raise it as a follow-up that first moves both names to a leaf.
- **Retrofitting the prefix test to `review_trace.py`, `review_rounds.py`, or earlier modules.** Verification 8(g) covers `review_trace`'s retargeted reads.
- **Leaf tests left in the legacy file:** `TestIsFreshUserPrompt`, `TestIsoDate`, `TestRepoScopedProjectSlugsGuard`, `TestSingleLevelProjectsGlob`, `TestBuildParser`, and `TestIterSessionsOrdering`. The leaf-module-tests phase owns them.
- **`_UNCONDITIONAL_HEADER_CASES` and the cross-command classes.** They stay until the `cli.py` phase (transcript-analysis-decomposition.md:157-161).
- **Consolidating the three `_attr_ts` copies.** It is a three-line helper covered by the DAMP exception.
- **Moving any other command's parser block.** That is later phases' work, which this mechanism serves.
- **Two pre-existing texts that move verbatim:** `TestRootsThreadingSpy`'s stale "(:2146, :2148 in the plan's line numbering)" docstring, and `_TS_FIXED`'s "tests 1–6, 8–10" comment.
- **Splitting the shim, the legacy file, conftest, or `scope.py` under 1,000 lines.** Each was already over before this phase.
- **Public names for the registrars, and any CLI, help-text, or output change.**
- **A committed golden parser snapshot, and dumps of nested subparser levels.** The shim has no nested subcommands. The permanent suite therefore does not pin the moved parsers' defaults or types after this PR; the parser dump is a per-phase gate.
- **Drifted shim citations outside `review_rounds.py`,** for example `_config.py:5`. `[unverified — relayed from decompose-transcript-dispatch.md:547]`
- **Records left untouched:** past phase plans, and the tracker text and checkbox, which the engineer edits.