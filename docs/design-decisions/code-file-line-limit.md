# This repo caps code-file length at 1,000 lines, via a repo-local pytest check

*2026-09-25.*

`.claude/plans/code-file-size-splits.md`'s child issue F ("Code-file size
policy") records this decision. It applies only to `claude-config` itself,
not to any repo that installs its stowed packages.

**Mechanism: a repo-local pytest check, not a hook.** Anything under
`claude/.claude/hooks/` installs into every stow consumer's own repository
(`CLAUDE.md`'s "Plans in this repo affect all stow users" section), so a
hook enforcing this repo's own file-length convention would impose it on
every downstream repo regardless of that repo's own conventions. A
repo-local pytest check has an existing precedent doing exactly this job
for a different rule: `test_ticket_reference_discipline.py` already scans
every tracked `.py`/`.sh` file in this repo and carries its own
`select-tests.py` exception so a scoped local run still selects it.

**Limit: 1,000 lines, for production and test files alike.** This is
where the surveyed linters' own defaults converge, not a defect
threshold — no primary study links file length to defect rates or to
LLM review effectiveness (an absence checked for, not merely unclaimed).
pylint's `too-many-lines` check, SonarQube's `S104`, and SwiftLint's
`file_length` *error* level (its *warning* level fires at 400) all default
to 1,000. Checkstyle's `FileLength` check defaults to 2,000, and ESLint's
`max-lines` rule — off by default, so its own docs' example config is the
convention being cited — defaults to 300 when enabled. ruff and ShellCheck
carry no file-length rule of any kind. No surveyed tool sets a separate
default for test files, which is why this repo's test-file limit matches
its production-file limit instead of following some other convention.
The Read tool's own per-call token cap independently lands near 1,000
lines for at least one measured file (`claude-skills/skills/code-review/REFERENCES.md`
§ "Read-tool per-call token cap") — a feasibility coincidence, not further
evidence that 1,000 lines is where defects start.

**Exception ceilings are exact, in both directions.** An exception row
passes only when its file's physical line count equals the row's
recorded ceiling — a shrink must lower its own ceiling, not merely stay
under a stale one. This rejects two lighter alternatives on the same
failure mode: an unbounded grandfather list (no ceiling at all) lets
exceptions grow without limit, which is what happened to `_lib.sh` absent
any check — 1,232 lines when a prior audit first proposed splitting it,
1,497 at a later discovery pass, and 3,554 today. An upward-only ceiling
— the semantics this repo's existing length-gates already use (deny only
when `NEW > LIMIT and NEW > OLD`) — has the same failure shape one step
removed: a file that shrinks keeps its old, larger ceiling, so the slack
between its new size and that ceiling is spent by later growth with no
reviewed edit forcing a conscious decision.

**Timing: the check ships only after an offender assessment exists.** The
limit and the exception mechanism are decided, but nothing enforces them
until F1 (the offender assessment) records, for every file already over
1,000 lines, its size, its 90-day commit count, and a verdict — split
pending or structural exception with a reason. That ordering exists so
the number and shape of today's offenders inform the exception table
before the check can fail a PR over them, rather than the check landing
first and forcing an unreviewed guess at every exception row.

**Revisit** if any of:

- F1's assessment finds the 1,000-line limit produces an unreasonable
  number or shape of exceptions once every offender is actually
  inventoried — the engineer's own stated condition for imposing it.
- A primary study linking file length to review-defect rates or to LLM
  review effectiveness is found, which would ground the limit in
  something other than linter-default convergence.

## Sources

- pylint, `too-many-lines` (`C0302`) — <https://github.com/pylint-dev/pylint/blob/main/pylint/checkers/format.py> — `"default": 1000,` on the `max-module-lines` option. Fetched 2026-09-25.
- SonarSource, `sonar-python` `TooManyLinesInFileCheck` (S104) — <https://raw.githubusercontent.com/SonarSource/sonar-python/master/python-checks/src/main/java/org/sonar/python/checks/TooManyLinesInFileCheck.java> — `private static final int DEFAULT = 1000;`. `rules.sonarsource.com` itself was unreachable when fetched; verified against the check's own source instead. Fetched 2026-09-25.
- Checkstyle, *FileLength* — <https://checkstyle.sourceforge.io/checks/sizes/filelength.html> — `max ... int ... 2000`. Fetched 2026-09-25.
- SwiftLint, *file_length* — <https://realm.github.io/SwiftLint/file_length.html> — `warning | 400` / `error | 1000`. Fetched 2026-09-25.
- ESLint, *max-lines* — <https://eslint.org/docs/latest/rules/max-lines> — `"max" (default 300) enforces a maximum number of lines in a file`; the rule itself is not enabled by default. Fetched 2026-09-25.
- ruff, full rules list — <https://docs.astral.sh/ruff/rules/> — no rule addresses file length, file size, or line-count limits. Fetched 2026-09-25.
- ShellCheck, wiki check index — <https://www.shellcheck.net/wiki/> — no rule addresses file length. Fetched 2026-09-25.
- `CLAUDE.md` — "Plans in this repo affect all stow users" — why a hook is rejected in favor of a repo-local check.
- `claude/.claude/scripts/tests/test_ticket_reference_discipline.py` and its `select-tests.py` exception — the existing precedent this check's mechanism follows.
- `claude/.claude/hooks/_lib.sh` — its own line count (3,554 as of this writing) as the unbounded-grandfather-list failure mode being avoided.
- `.claude/plans/code-file-size-splits.md` — child issue F's full design, the engineer's decisions (rows 43–46, 60–62), and G4's absence-of-evidence finding.
- `claude-skills/skills/code-review/REFERENCES.md` § "Read-tool per-call token cap" — the feasibility-coincidence measurement cited above.
