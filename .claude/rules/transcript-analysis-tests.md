---
paths:
  - "claude/.claude/scripts/transcript-analysis.py"
  - "claude/.claude/scripts/transcript_analysis/**"
  - "claude/.claude/scripts/tests/test_transcript_*.py"
  - "claude/.claude/scripts/tests/test_author_outcome.py"
  - "claude/.claude/scripts/tests/_*_helpers.py"
  - "claude/.claude/scripts/tests/conftest.py"
---

## Where a transcript-analysis test goes

Paths below are under `claude/.claude/scripts/`. Put each test in the file named for its subject: the
code whose defect the test would catch. A module the test runs only to build fixtures, or reads only as an
oracle to agree with, is not its subject.

- **One package module:** `tests/test_transcript_<module>.py`, or `tests/test_transcript_<module>_<seam>.py`
  once that module's tests are split. A new module's test file ships in the change that creates the module.
- **A shared helper reached through one command:** the command's file when the test pins that command's call
  or the values it passes, and the helper's own module's file when the command is only a convenient driver.
- **One command's CLI wiring** (its flags, defaults, and `set_defaults(func=...)`): the file of the module
  that registers its subparser, or `tests/test_transcript_cli.py` when `build_parser()` registers it inline.
- **`build_parser()` or `main()` across commands**, such as a walk of every subparser or `main()`'s
  top-level `--config-dir` handling: `tests/test_transcript_cli.py`.
- **An invariant several modules share:** `tests/test_transcript_analysis_<invariant>.py`. A test that
  reads a `SKILL.md` by path needs that prefix, or its own `select-tests.py` row, so a skill edit selects it.
- **A structural test over package source:** a file named for the property it checks.
- **Helpers:** one family's shared helpers go in `tests/_<family>_helpers.py`. `tests/conftest.py` holds
  only fixtures and helpers several families share, and their own tests go in
  `tests/test_transcript_shared_fixtures.py`.

No test file is a catch-all named for the tool as a whole.
`tests/test_transcript_analysis_module_test_files.py` fails when a package module has neither a test file
named for it nor an entry in that file's `MODULES_TESTED_ELSEWHERE`.

**Size.** `docs/design-decisions/code-file-line-limit.md` sets the line limit, for test files as for code.
A test file that would cross it splits at a named seam, and the new file's suffix names that seam. Never
split by position, as in `_part2`.
