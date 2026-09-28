# Fix case-colliding tmp paths in the YAML boolean-spelling test

## Context

Goal: make `TestValidateContextForkRequiresExplicitBackground::test_yaml_coerced_boolean_spellings_pass` pass on Linux, macOS, and CI, and record the portability rule where later sessions will find it instead of re-deriving it.

The test loops over `("yes", "on", "True", "TRUE")` and calls `_make_skill(tmp_path, f"a_{spelling}", ...)`, which runs `mkdir()` on `tmp_path / "a_True"` and then `tmp_path / "a_TRUE"`. On macOS's case-insensitive (case-preserving) filesystem the second `mkdir` raises `FileExistsError: [Errno 17]`. Linux and CI (`runs-on: ubuntu-24.04`, the only OS in `.github/workflows/tests.yml`) never see it.

Reproduced at `origin/main` (worktree HEAD equals `origin/main`), so the failure is pre-existing and independent of any other session's diff. The test dates to the skills stow-package split; nothing recent introduced it.

Why now: another session hit it and did not want to relitigate it. Decision from the user: fix the test and add a project-layer `test-conventions-claude-config` skill (the glob `.claude/skills/test-conventions-*/SKILL.md` that `test-conventions` Step 0 already reads).

## Approach

Convert the looping test into a `@pytest.mark.parametrize` over the four spellings, so each spelling gets its own `tmp_path`, and stop encoding the spelling in the fixture directory name at all — the skill dir becomes `"a"`, the same name the other eight tests in the class already use. Separately, record the portability rule in a new project layer, `.claude/skills/test-conventions-claude-config/SKILL.md`, which `test-conventions` Step 0 already globs for.

**The test edit** (`claude-skills/skills/tests/test_skills.py`, replacing the loop at `:1910-1920`; docstring unchanged):

```python
    @pytest.mark.parametrize("spelling", ["yes", "on", "True", "TRUE"])
    def test_yaml_coerced_boolean_spellings_pass(self, tmp_path, spelling):
        """<existing docstring, unchanged>"""
        f = self._make_skill(tmp_path, "a", ["context: fork", f"background: {spelling}"])
        assert not self._background_violations(validate(f)), (
            f"background: {spelling} should currently pass (PyYAML coerces it to bool)"
        )
```

All four spellings stay: `yes`/`on` are YAML 1.1 boolean spellings that are not the token `true`, `True`/`TRUE` are its case variants, and the test exists to pin that `isinstance(background, bool)` accepts all four (`plugins/skill-management/scripts/validate_skill_structure.py:116-129`). The default ids (`yes`, `on`, `True`, `TRUE`) stay: pytest gives every case its own numbered `tmp_path` directory, and for this test name the id is truncated out of the directory name entirely (ledger row 4).

Parametrize also isolates failures: the loop aborts at the first failing spelling, so a parser change that rejects `yes` and `on` would never report on `True`/`TRUE`, while one case per test reports each spelling separately (a mutation run of the test with a YAML 1.2 style loader confirmed `[yes]`/`[on]` fail and `[True]`/`[TRUE]` pass). The base `test-conventions` skill says nothing about case-insensitive volumes, which is why the rule goes in a project layer. Alternatives set aside: index-suffixed dir names (`a_0`…`a_3`) inside the shared `tmp_path` fix the collision but keep first-failure-aborts-the-rest reporting; lowercasing the dir name (`spelling.lower()`) collides `True` and `TRUE` outright; `pytest.mark.skipif(sys.platform == "darwin")` hides the test on the only platform where it currently fails, and the behavior under test (PyYAML coercion) is platform-independent.

**The project layer** (`.claude/skills/test-conventions-claude-config/SKILL.md`, new, ~20 lines), following the two sibling layers' frontmatter and voice exactly:

```markdown
---
name: test-conventions-claude-config
description: Project-specific layer for /test-conventions, loaded only when writing tests in the claude-config repo itself.
disable-model-invocation: true
---

## Filesystem fixtures must not collide under case-insensitive filesystems

CI runs Linux only (`.github/workflows/tests.yml`), and contributors run the same
suite on macOS, whose default volume is case-insensitive and case-preserving. Two
paths in one directory that differ only by case are one path there. A second
`mkdir()` raises `FileExistsError`, a second `write_text()` silently overwrites the
first, and `exists()` returns True for the wrong case — on a contributor's machine,
never in CI.

No two entries in one fixture directory may be equal under `str.casefold()`, and a
test must not rely on `exists()` telling case-only-different names apart. When a
test builds a path from a value set — a loop, a `parametrize` list, a factory
helper's `name` argument — check the set for a casefold pair. If it has one, give
each value its own `tmp_path` via `@pytest.mark.parametrize` and keep the directory
name itself constant.
```

It stays on the one proven rule. No other portability hazard has been observed in this repo, and a layer that speculates about symlinks, path separators, or locale collation would be untested prose in an always-globbed file. The body carries no URL, since `.claude/skills/*/SKILL.md` is inside the repo-wide SKILL.md contracts (row 9).

**No mechanical backstop.** The hazard has exactly one occurrence repo-wide, and any detector (a source scanner or a runtime check of the realized `tmp_path` tree) is untested machinery for a shape that occurs once, against `CLAUDE.md`'s default-suspect-over-powered-primitives bar. The honest mechanical enforcement is a macOS CI job, which is a cost decision the user has not asked for; it is recorded in **Out of scope** rather than planned here. The layer is not pure reviewer discipline either: `claude/.claude/agents/staff-sdet.md:19` reads `test-conventions` before citing a section, which runs its Step 0 glob, so the rule loads automatically on the review path as well as the authoring path.

### Assumption ledger

**Root:** `test_yaml_coerced_boolean_spellings_pass` creates `a_True` and then `a_TRUE` inside one `tmp_path`; those are one directory on macOS's default volume, so the second `mkdir()` raises `FileExistsError` on every contributor Mac while Linux-only CI stays green. The plan makes the test case-agnostic and records the rule where the next test author meets it.

**Givens:**
- macOS's default APFS volume is case-insensitive and case-preserving. Apple owns that default; no change in this repo reaches it.
- `pytest` owns how `tmp_path` derives its directory name; the repo only pins `pytest==8.*` (`requirements-dev.txt:1`).
- CI is `ubuntu-24.04` and nothing else, so this failure class structurally cannot reach CI. Widening the matrix is a cost decision outside this fix (Out of scope).
- The user chose "fix the test **and** add `test-conventions-claude-config`" rather than either alone. `[engineer-verified]`

| # | Assumption | Tag |
|---|---|---|
| 1 | The failure is pre-existing at `origin/main`, not a product of another session's diff. | `[verified: repro at origin/main — 1 failed / 8 passed, FileExistsError on .../test_yaml_coerced_boolean_spel0/a_TRUE]` |
| 2 | All four spellings must survive the fix; dropping `TRUE` would silently narrow what the test pins. | `[verified: claude-skills/skills/tests/test_skills.py:1910-1920 docstring; validate_skill_structure.py:116-129]` |
| 3 | A per-case `tmp_path` removes the collision outright: `mktemp(..., numbered=True)` hands each parametrized case its own numbered directory under the session basetemp. | `[verified: _pytest/tmpdir.py:115-137, 246-251 in the pinned venv]` |
| 4 | Each parametrized case gets its own numbered `tmp_path` directory, so `[True]` and `[TRUE]` cannot collide. For this test name `_mk_tmp` truncates the node name to 30 characters, dropping the id (observed dirs `test_yaml_coerced_boolean_spel0`…`3`). For a shorter test name where ids survive, `find_prefixed` lowercases both sides before numbering, so case-only-different ids still get distinct numbers, and `make_numbered_dir` retries rather than raising `FileExistsError`. | `[verified: _pytest/tmpdir.py:246-251; _pytest/pathlib.py:173-177, 225-245; pytest 8.4.2; observed dirs on macOS]` |
| 5 | This is the only site in the repo that loops over case-varied literals to build a path. | `[verified: sibling grep across claude/.claude, claude-skills, plugins]` |
| 6 | No other test in the repo builds a path from a list/tuple/set of string literals containing a case-only-different pair. | `[verified: AST sweep of every test_*.py under claude/.claude, claude-skills, plugins, .claude — 23 case-pair literals total, 22 outside this test; the only path-building ones (`test_ask_new_dependency_disclosure.py:1002` basenames) are parametrized, so each gets its own tmp_path]` |
| 7 | `.claude/skills/test-conventions-claude-config/SKILL.md` is the exact path Step 0 globs, and adding it leaves exactly one match, so the "multiple match → stop" branch stays untriggered. | `[verified: claude-skills/skills/test-conventions/SKILL.md:15; .claude/skills/ holds only code-review-claude-config and plan-review-claude-config]` |
| 8 | The new layer needs no registration: `_model_invokable_skills()` and `COMMAND_SKILLS` scan `claude-skills/skills` and `plugins/*/skills` only, and `docs/skills.md` documents the prefix set (`test-conventions-` already listed), never the instances. | `[verified: test_skills.py:171-222, :230; docs/skills.md:201-203]` |
| 9 | The new file does enter the repo-wide SKILL.md contracts, because `_all_skill_md_files()` globs `.claude/skills/*/SKILL.md`: no bare URL in the body, any `` `target` § "Heading" `` citation must resolve, no per-account `~/.claude/<state>` path. | `[verified: test_skills.py:2593-2615, 2765-2783, 3293-3329, 4891-4899]` |
| 10 | Committing the new SKILL.md is hook-blocked until `/skill-review` runs, and the same hook runs the structural validator on the staged file. | `[verified: plugins/skill-management/hooks/require-skill-review.sh:105, 115, 157-180]` |
| 11 | `select-tests.py` maps both changed paths with no full-suite fallback: `.claude/skills/**` → skills tests; the test file → skills tests + ticket-reference test + claude tests + the select-tests test. | `[verified: claude/.claude/scripts/select-tests.py:370-380, 497, 501-502]` |
| 12 | This worktree has no `.venv`; the documented cross-worktree form is the three-level-up path into the main checkout's venv. | `[verified: README.md:519, 543]` |
| 13 | `line-length = 130`, so the one-line `_make_skill` call above needs no wrapping. | `[verified: pyproject.toml:1-2]` |

**Mechanisms:**
- **M1 — `@pytest.mark.parametrize` over the four spellings.** `anchors: root` — per-case `tmp_path` is the base skill's own §4 unique-temp-directory rule, and it is the only option that also restores per-spelling pass/fail reporting.
- **M2 — constant `"a"` fixture directory name.** `anchors: root` — removes the case-varying path component itself instead of relying on isolation to keep two case-variants apart, and matches the eight sibling tests in the class.
- **M3 — a project-layer skill rather than a comment, a CLAUDE.md line, or a base-skill edit.** `anchors: root` (the "record it where later sessions find it" half of the goal). Lighter primitives considered: (a) a comment in the test file — reaches only a reader already inside that file, which the next author writing a different fixture is not; (b) a line in root `CLAUDE.md` — always-loaded budget for every session in the repo, for a rule that binds only when writing tests; (c) a line in base `claude-skills/skills/test-conventions/SKILL.md` — that body installs to every stack and `.claude/rules/skill-and-agent-self-review.md` requires it to stay platform-agnostic, while the load-bearing half of this rule ("CI won't catch it") is a fact about this repo's single-OS matrix.
- **M4 — a `casefold()` sweep of the repo's other test value sets, run during plan review.** `anchors: row6` — `CLAUDE.md`'s audit-structural-siblings rule; it found no second site, so no other file is in scope.

## Critical files

Paths below are relative to the worktree root.

**One `code-writer` dispatch (`model: sonnet`), both files.** Splitting would require restating the same shared background — the case-collision mechanic, the macOS/Linux-CI asymmetry, the four-spelling rationale — in both prompts, and the SKILL.md's content is the rule the test edit demonstrates. That is `plan-it` Step 5's explicit do-not-split condition. Total surface is roughly 15 changed lines across two files.

1. **`claude-skills/skills/tests/test_skills.py`** *(modify, `:1910-1920`)* — replace the loop with the parametrized form above; docstring and assertion message unchanged. **Reuse:** `_make_skill` and `_background_violations` (`:1879-1888`) unchanged; `@pytest.mark.parametrize` is already used in this file.
2. **`.claude/skills/test-conventions-claude-config/SKILL.md`** *(create)* — body as drafted above. **Reuse:** frontmatter shape and one-line `description` voice from `.claude/skills/plan-review-claude-config/SKILL.md` (17 lines); no registration file, doc, or settings entry to touch (row 8).
No other file is in scope: the sibling sweep (ledger row 6) found no second case-colliding fixture.

## Verification

Run from the worktree root. The contributor `.venv` lives only in the main checkout, so use the documented three-level-up form (`README.md:519`).

1. **The fix discriminates on this platform** — `../../../.venv/bin/pytest claude-skills/skills/tests/test_skills.py::TestValidateContextForkRequiresExplicitBackground -n0 -v`. Expect 12 passed (8 unchanged tests plus 4 parametrized cases), with `[yes]`, `[on]`, `[True]`, `[TRUE]` all listed. The pre-fix failure is already reproduced at `origin/main` (1 failed / 8 passed, `FileExistsError` on `.../a_TRUE`), so the post-fix run on macOS is the discriminating one — no second pre-fix run is needed.
2. **Scoped suite** — `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py` (`README.md:543`; required local command for agents). It computes the changed set itself, so both files are picked up; expect a domain selection, not a full-suite fallback (row 11). This is what proves the new SKILL.md clears the repo-wide contracts in row 9.
3. **Lint** — `../../../.venv/bin/ruff check claude-skills/`.
4. **Layer loads** — the layer is `disable-model-invocation: true` and fires only through the base skill's Step 0 glob, so confirm by `Glob`ing `.claude/skills/test-conventions-*/SKILL.md` from the repo root and checking it returns exactly one path (row 7).
5. **Commit gates** — `/code-review`, then `/skill-review` on the staged diff; the latter is hook-enforced by `require-skill-review.sh` for `.claude/skills/**/SKILL.md` and will block `git commit` until its marker matches (row 10).

## Out of scope

- **A macOS job in the CI matrix.** The only honest mechanical backstop for this bug class, and a real option — but it roughly doubles CI runtime on every push and would likely surface unrelated macOS-only failures that this branch would then own. The user asked for a test fix plus a convention layer; this is a separate decision with its own cost.
- **Mechanical enforcement of "no casefold-duplicate entries in a test's fixture tree".** One occurrence repo-wide. A source scanner would have to reason about f-string path construction, and a runtime check of the realized `tmp_path` tree (an autouse fixture) would be heavier than the bug. Left unenforced; the layer rule and reviewer loading carry it.
- **Follow-ups the review surfaced, not part of this fix.** `_background_violations` filters on a bare `"background"` substring that also matches the file path, and the negative YAML 1.1 spellings (`no`, `off`, `False`, `FALSE`) have no test.
- **Promoting the rule into base `claude-skills/skills/test-conventions/SKILL.md` §4.** The mechanics (case-insensitive volumes) are not claude-config-specific and would help every stow consumer; only the "CI won't catch it" half is repo-specific. The user chose the project layer, and moving rule text into a globally-installed skill body changes what every consumer loads — a separate decision, not a silent widening here.
- **Editing other test files.** The sweep found no second case-colliding fixture, so a non-colliding site is left alone.
- **Any change to `validate()` or the `context: fork` check itself.** The production code is correct; only the test's fixture layout is at fault.
