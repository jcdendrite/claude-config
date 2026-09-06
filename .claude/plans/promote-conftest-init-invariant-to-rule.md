# Promote the conftest.py `__init__.py` invariant into a rule

## Context

Promote the conftest.py/`__init__.py` packaging invariant currently stated
only as a single sentence in README.md's Tests section into a proper
contributor-facing rule, since it prescribes required action for anyone
adding a new `claude/.claude/<domain>/tests/` tree rather than merely
describing the test suite's current shape. This follows PR #784 (merged),
which added `TestConftestModuleNamesAreUnique` to enforce the invariant at
CI time but left contributor guidance as a README aside that a contributor
only encounters if they happen to read that section before writing a new
conftest.py. The intended outcome: a contributor authoring a new
`claude/.claude/<domain>/tests/conftest.py` sees the `__init__.py`
requirement surfaced automatically — via this repo's existing
`.claude/rules/` path-scoped auto-load mechanism, or another home Step 5
concludes is the better fit — rather than first discovering it as a CI
failure or a README paragraph unrelated to the file they're editing.

## Approach

Add one path-scoped rule file, `.claude/rules/test-tree-packaging.md`, carrying the required action a contributor must take when adding a `claude/.claude/<domain>/tests/` tree, and reduce README.md's Tests-section sentence to a descriptive pointer at that rule. The rule states *what to add* and defers the *why* to `TestConftestModuleNamesAreUnique`'s class docstring, which already owns the mechanism.

The invariant is specific to this repo's own `claude/.claude/<domain>/tests/` layout, so it belongs in the repo-scoped `.claude/rules/` directory — not in the stowed `test-conventions` skill and not in `claude/.claude/rules/`, both of which install into every repo the user opens. README.md:64 already designates `.claude/rules/` as the home for exactly this class of content: "Contributor-workflow instructions that only apply to specific file types live in `.claude/rules/` instead."

### Root problem

The `__init__.py` requirement is prescriptive — it tells a contributor what to do — but its only contributor-facing statement sits inside README.md's Tests section, which is otherwise descriptive "how to run the suite" prose. A contributor authoring a new `claude/.claude/<domain>/tests/conftest.py` has no reason to be reading that paragraph, so the requirement reaches them only as a CI failure. Relocation must put the prescription where the file being edited pulls it in automatically, without leaving a second copy behind to drift.

### Givens

- **G1.** *When* Claude Code loads a `paths:`-scoped rule is the harness's own semantics; this repo chooses the globs but not the trigger event. *(Vendor-imposed.)*
- **G2.** The packaging invariant itself — two `__init__.py` markers per domain tree, and the relative-import form inside a packaged tree — is settled by the merged predecessor change and pinned by `claude/.claude/tests/test_pytest_collection_config.py`. This plan relocates guidance; it does not revisit the invariant. *(Owned by a prior merged decision.)*
- **G3.** `claude/.claude/tests/` stays unpackaged: creating `claude/.claude/tests/__init__.py` would split `helpers` into two module objects with independent `REPO_ROOT` state. *(Decided and recorded as Out of scope by `.claude/plans/fix-conftest-module-collision.md:220`, outside this plan's reach.)*

### Mechanisms

- **M1 — New `.claude/rules/test-tree-packaging.md`, two `paths:` globs** (`claude/.claude/**/tests/**` and `claude/.claude/**/conftest.py`). *anchors: root, row 1, row 2* — the tests-tree glob is what makes the rule land under the pessimistic reading of row 2, because a contributor building a new tree reads an existing sibling test file before writing their own. The conftest glob covers a conftest that lands outside a `tests/` directory.
- **M2 — Rule body states the required action only, citing the test for the mechanism.** *anchors: root, row 8* — the test docstring is already the canonical home for *why*; restating it in the rule creates the second copy this plan exists to remove.
- **M3 — One line in the rule on the relative sibling-import form.** *anchors: row 4* — the same packaging decision produces this second required action, `TestNoBareSameDirectorySiblingImports` fails CI on it, and no contributor-facing doc states it anywhere today. The rule fires on exactly the population that needs it. **This is a deliberate one-line extension past the ticket's literal wording; it is strikeable without affecting the rest of the plan.**
- **M4 — One line in the rule prohibiting `claude/.claude/tests/__init__.py`.** *anchors: G3, row 5* — M1's globs put the rule in front of someone editing that directory, and the rule's own imperative would otherwise read as instructing them to package it, which the predecessor plan calls actively harmful.
- **M5 — README.md:515 becomes a descriptive sentence plus a pointer to the rule.** *anchors: root, row 8* — README keeps the layout fact a human browsing GitHub needs ("why do these `__init__.py` files exist"); the imperative moves out, so neither site restates the other.
- **M6 — README.md:243's `.claude/rules/` enumeration gains the fourth topic.** *anchors: row 6* — that line lists the current three rules by topic and goes stale on the next line's own terms otherwise.

### Alternatives weighed and set aside

The over-powered-primitive check runs toward *lighter* here, since a rule file is already near the floor. Three lighter options:

**Leave the sentence in README, change nothing** — the status quo, zero new files. It fails the one requirement: nothing surfaces it at authoring time.

**A bullet in the repo-root `CLAUDE.md`** — also no new file, but `CLAUDE.md` loads in full on every session in this repo, and this guidance is relevant to a handful of files. README.md:64 draws precisely this line, and the path-scoped mechanism exists to hold the file-type-specific half.

**A comment in each existing `conftest.py` and `__init__.py`** — the cheapest surface of all, but it reaches only someone who opens an existing marker file. The target contributor is creating a tree that has no markers yet.

Two heavier options, rejected:

**A PreToolUse hook denying a `conftest.py` write when its markers are absent.** `TestConftestModuleNamesAreUnique` already fails CI on this exact state with an actionable message naming the two files to add. A hook would be a second enforcement layer over an invariant that already has one — CLAUDE.md's compounding-defensive-layers tell.

**`test-conventions`'s SKILL.md or REFERENCES.md, or a stowed `claude/.claude/rules/` file.** Both install to every repo the user opens. `.claude/rules/skill-and-agent-self-review.md` states the governing constraint directly: "Global skill bodies stay platform-agnostic. Skills under `claude/.claude/skills/` install to every stack — don't hardcode engine/platform tokens." A claude-config directory layout is a stronger version of the token this bans. `test-conventions` also carries no pytest-specific content at all today, so this would be the first stack-specific material in it.

**On the README sentence: delete outright, considered and set aside.** Deleting leaves no human-readable surface at all — `.claude/rules/` files load for an agent session, not for someone reading the repo on GitHub, and README's Tests section is where a contributor looks for how the suite is laid out. The retained sentence is descriptive (what the tree currently is) rather than prescriptive (what you must add), so it references the rule rather than restating it.

### Assumption ledger

1. **`.claude/rules/*.md` load only when a file matching a `paths:` glob is opened**, and every existing rule in both rules directories is gated this way. `[verified: README.md:64; frontmatter of all three files under .claude/rules/]`
2. **Whether a rule loads on a Write/Edit that *creates* a matching path, as opposed to a Read of an existing one, is documented nowhere in this repo.** The two-glob design is a hedge that holds under either reading: a contributor building a new domain tree reads an existing test file or conftest first, and both match. `[unverified]` — resolved by Verification step 3, not by more reading.
3. **A `paths:` glob is never checked against a real path by any test.** `test_rules_frontmatter.py`'s own module docstring: a "syntactically-valid but wrong/typo'd glob (e.g. `"cluade/.claude/rules/**"`) passes this check while still silently matching nothing at runtime." `[verified: claude/.claude/skills/tests/test_rules_frontmatter.py:8-16]`
4. **The bare-sibling-import prohibition applies only to trees carrying an `__init__.py`.** `TestNoBareSameDirectorySiblingImports` skips a directory with no marker (`if not (path.parent / "__init__.py").exists(): continue`), so the rule's wording must be conditioned on the tree being packaged. `[verified: claude/.claude/tests/test_pytest_collection_config.py:406-433]`
5. **Nothing asserts `claude/.claude/tests/__init__.py` is absent.** G3's prohibition is documentation-only, which is why M4 has to state it rather than rely on a check firing. `[verified: no such assertion in test_pytest_collection_config.py; .claude/plans/fix-conftest-module-collision.md:220 records it as Out of scope with the `helpers` module-identity reason]`
6. **README.md:243 enumerates the current three rules by topic**, so a fourth rule leaves that line stale unless M6 lands with M1. `[verified: README.md:243]`
7. **The tree today has exactly two `conftest.py` files and five `__init__.py` files** — `hooks/tests/conftest.py`, `scripts/tests/conftest.py`; markers at `hooks/`, `hooks/tests/`, `scripts/`, `scripts/tests/`, `scripts/transcript_analysis/`. `claude/.claude/skills/tests/` and `claude/.claude/tests/` carry neither. `[verified: repo globs claude/.claude/**/conftest.py and claude/.claude/**/__init__.py]`
8. **The mechanism has one canonical home already.** `TestConftestModuleNamesAreUnique`'s class docstring states the eviction behavior, the last-writer-wins slot, and why the package root is asserted alongside the name. `[verified: claude/.claude/tests/test_pytest_collection_config.py:239-251]`
9. **`select-tests.py` maps this diff to two domains and does not fall open to the full suite.** `.claude/rules/**` → `claude/.claude/skills/tests`; `README.md` → `claude/.claude/hooks/tests` + `claude/.claude/skills/tests`; `.claude/plans/**` → empty target tuple. A `CROSS_DOMAIN_EXCEPTIONS` match sets `matched = True`, so the rule file is not an unmatched path. `[verified: claude/.claude/scripts/select-tests.py:250, 324-331, 363-379]`
10. **`test_rules_frontmatter.py` discovers rule files by `rglob("*.md")` and parametrizes over them**, so the new file gains coverage with no test edit. It is also the only test that reads root `.claude/rules/` by path. `[verified: claude/.claude/skills/tests/test_rules_frontmatter.py:33-38, 84-100; select-tests.py:70, 123-127, 297-299]`
11. **`docs/rules-references.md` needs no new section.** It carries one section per rule file *that cites external documentation* — currently only the two stowed rules. None of the three project-scoped rules has one, and this rule's sole citation is an in-repo test. `[verified: docs/rules-references.md, whole file]`
12. **`test-conventions` is stack-agnostic and mentions neither `conftest.py` nor `__init__.py`.** `[verified: Step 3 exploration read of claude/.claude/skills/test-conventions/SKILL.md]` The prohibition on putting repo-specific content there is independently stated. `[verified: .claude/rules/skill-and-agent-self-review.md, "Global skill bodies stay platform-agnostic"]`
13. **The marker-file docstring form the rule describes matches what is on disk.** `claude/.claude/hooks/__init__.py` is exactly one docstring line pointing at the test file. `[verified: claude/.claude/hooks/__init__.py]`

**Single `code-writer` dispatch.** Three small doc edits driven by one decision about where the invariant lives; splitting would force the same background into two prompts for no disjoint-file-set benefit.

## Critical files

**Create — `.claude/rules/test-tree-packaging.md`:**

```markdown
---
paths:
  - "claude/.claude/**/tests/**"
  - "claude/.claude/**/conftest.py"
---

## Test-tree packaging under `claude/.claude/`

A test directory that carries its own `conftest.py` needs an `__init__.py` in
both itself and its parent domain directory — `claude/.claude/<domain>/__init__.py`
and `claude/.claude/<domain>/tests/__init__.py`. Both are required: with only the
leaf marker, the tree resolves to a module name one level too shallow and
collides with a sibling tree's conftest. Each marker holds one docstring
line naming the test below, and nothing else.

In a test tree that has an `__init__.py`, import a same-directory sibling module
as `from .sibling import X`, never `from sibling import X` — the bare form stops
resolving once the directory is a package.

Do not add `claude/.claude/tests/__init__.py`. That directory stays unpackaged
deliberately. `helpers.py` is already importable as a top-level module through
`pyproject.toml`'s `pythonpath`. Packaging the directory would create a second
`helpers` module object with its own `REPO_ROOT`.

`claude/.claude/tests/test_pytest_collection_config.py` carries the mechanism:

- `TestConftestModuleNamesAreUnique` fails CI on a missing marker.
- `TestNoBareSameDirectorySiblingImports` fails CI on a bare sibling import.
```

Do not restate the eviction/`sys.modules` mechanism here — row 8's docstring owns it, and duplicating it is the defect this change removes.

**Modify — `README.md:515`,** replacing the current prescriptive sentence with a descriptive one plus the pointer:

> Test trees under `claude/.claude/` that carry their own `conftest.py` are Python packages, so each tree's conftest resolves to a distinct module name. [`.claude/rules/test-tree-packaging.md`](./.claude/rules/test-tree-packaging.md) states what a new tree must add; [`claude/.claude/tests/test_pytest_collection_config.py`](./claude/.claude/tests/test_pytest_collection_config.py) enforces it.

Keep its current position — after the `pytest-xdist` sentence at line 512, before the `-n auto` sizing bullets at line 516 — so the layout notes stay grouped.

**Modify — `README.md:243`,** extending the `.claude/rules/` topic list from three to four: `…per-file-type review-pipeline dispatch, settings.json conventions, and test-tree packaging.` Change nothing else on that line.

**Reuse opportunities (do not reimplement):**

- The frontmatter and `##`-heading shape of the three existing files under `.claude/rules/` — `paths:` as a quoted-string list, one `##` heading, no `name:`/`description:` keys.
- `test_rules_frontmatter.py` discovers rule files by `rglob`, so the new file needs no test registration (row 10). Do not add a parametrize entry or a fixture for it.
- The marker-docstring wording already on disk at `claude/.claude/hooks/__init__.py` (row 13) — the rule describes that form rather than inventing a new one.

**Do not touch:** `claude/.claude/skills/test-conventions/**`, `claude/.claude/rules/**`, `claude/.claude/tests/test_pytest_collection_config.py`, `docs/rules-references.md` (row 11), `claude/.claude/scripts/select-tests.py` (row 9 — the diff is already mapped), and every `__init__.py`/`conftest.py` currently in the tree.

## Verification

1. The repo's documented scoped command, run from this worktree (three levels deep, per README.md:510):

   ```bash
   ../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py
   ```

   Per row 9 this selects `claude/.claude/hooks/tests` and `claude/.claude/skills/tests` and reports `domain-selected`, not a full-suite fallback. `test_rules_frontmatter.py` runs inside the skills domain and parametrizes over the new file, so a malformed `paths:` block fails here. A `running the full suite (unmatched-path: …)` line instead means a path in the diff is unmapped — investigate that before reading the result as a pass.

   No `ruff` or ShellCheck run is needed: the diff adds no Python and no shell.

2. Confirm README's two edited lines render their links: `.claude/rules/test-tree-packaging.md` and `claude/.claude/tests/test_pytest_collection_config.py` both exist at the paths given, relative to the repo root.

3. **The check no test performs (row 3): confirm the globs actually match.** In a *fresh* session anchored in this repo, open `claude/.claude/hooks/tests/conftest.py` and confirm the rule's text arrives as an injected system-reminder. Repeat with `claude/.claude/scripts/tests/test_token_analyzer.py` to exercise the `**/tests/**` glob independently of the conftest glob. `test_rules_frontmatter.py` passes on a typo'd glob that matches nothing, so step 1 cannot substitute for this.

4. `/code-review` before commit, per CLAUDE.md. The whole diff is durable in-repo doc prose, which is the change type README.md:236 documents as dispatching `comment-discipline-reviewer` — expect that dispatch rather than treating it as noise.

## Out of scope

- **Adding a test that pins G3's prohibition** (asserting `claude/.claude/tests/__init__.py` stays absent). It would be a new enforcement layer for a hazard with no observed occurrence, over an invariant whose real failure mode — the split `helpers` identity — surfaces immediately and loudly in the suite. Raise separately if it is ever tripped.
- **Packaging `claude/.claude/skills/tests/`.** It carries no `conftest.py` (row 7), so it is not the same bug shape; the predecessor plan already audited and excluded it.
- **Adding root `.claude/rules/**` to `pr-cost.md`'s `--risk-surface-glob` defaults.** Only the stowed `claude/.claude/rules/**` is listed there today. Whether repo-scoped rule files are a risk surface is a separate call with its own ledger effects.
- **Moving any other prescriptive sentence out of README's Tests section.** Only line 515's is in scope; a broader descriptive-versus-prescriptive audit of that section is a different change.
- **Widening `test-conventions` with any repo-specific pytest guidance.** Set aside on the merits above, not deferred — the stowed skill stays stack-agnostic.
