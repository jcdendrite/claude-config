# Extend semver + consumer-propagation discipline to npm packages via a sibling `npm-semver` plugin

## Context

**Goal:** ship an opt-in `npm-semver` plugin in claude-config that any published-npm-package
repo installs to (1) enforce a `package.json` version bump when the package's source changes,
and (2) remind the author to propagate the new version into consuming repos — extending the
bump-by-backward-compatibility discipline that `plugin-semver` currently applies only to Claude
Code plugin manifests.

The trigger that matters fires when *business logic* changes (`src/**`), not when `package.json`
is edited — an author updating logic never opens `package.json`, so a `package.json`-scoped
reminder would miss the moment; and a generic reminder that fired on any `package.json` would
false-positive in unpublished repos. "Is this a published package" is repo-specific knowledge a
generic stowed glob cannot express. An **opt-in plugin** resolves both: only published-package
repos install it (no false positives), and its **commit-time hook** keys on the source diff (not
on which file is open), so a bump can't be silently forgotten. This mirrors `plugin-semver`
exactly — the established house pattern for this class of problem.

**Decisions (confirmed with user):**
- Sibling plugin `npm-semver`, **not** an expansion of `plugin-semver` — disjoint install
  audiences (plugin-authors vs package-publishers), avoids a bimodal hook, avoids a breaking
  rename of an installed plugin, independent version streams.
- **npm-focused**, not a multi-ecosystem frame — the shared surface is only the ~5-line semver
  rubric (which the repo's convention says to duplicate, not abstract); the enforcement mechanics
  (version location, publish-detection, version grammar) are irreducibly per-ecosystem, and some
  ecosystems aren't even semver (Python PEP 440) or have no manifest version field (Go tags). No
  near-term Cargo/Python/Go publishers. The skill's rubric prose is written ecosystem-portable so
  a future `cargo-semver` is a cheap copy-and-swap.
- **Skill + commit-blocking hook** (not skill-only) — mechanical enforcement is the point.

## Approach

Create `plugins/npm-semver/`, a structural sibling of `plugins/plugin-semver/`, and register it in
the marketplace. Published-package repos install it at project scope; adoption in any specific
consuming repo is a separate, out-of-band action (see Adoption).

### Plugin components

- **`plugins/npm-semver/.claude-plugin/plugin.json`** — `name: npm-semver`, `version: 1.0.0`
  (initial release; future bumps governed by `plugin-semver`'s own hook), `author` matching the
  other plugins, `"skills": "./skills/"`. **No `version` in the marketplace entry** (Claude Code
  resolves `plugin.json` first and masks a marketplace value — the same rule `plugin-semver`
  documents). **Self-block check:** creating `plugins/npm-semver/**` is itself editing a plugin
  dir, which triggers `plugin-semver`'s "version raised since merge-base" requirement — but the
  plugin is absent at merge-base. Before the first commit, confirm `plugin-semver`'s hook treats a
  brand-new plugin (initial `1.0.0`, not in the base tree) as satisfied; if it instead requires a
  prior version to raise *from*, the commit that creates the plugin would be self-blocked and needs
  handling.

- **`plugins/npm-semver/skills/npm-semver/SKILL.md`** — advisory skill, `user-invocable: false`,
  modeled on `plugin-semver`'s SKILL.md. Sections:
  - **Version field: one location** — the canonical version is `package.json` `version`.
  - **Bump magnitude** — semver.org 2.0.0, keyed on backward compatibility / the package's
    *declared public API*, not diff size. Verbatim source (semver.org): *"increment the: MAJOR
    version when you make incompatible API changes, MINOR version when you add functionality in a
    backward compatible manner, PATCH version when you make backward compatible bug fixes."* plus
    semver.org's precondition that semver requires a declared public API. Written
    ecosystem-portable (says "the package's version", not npm-specific tokens) so it copies
    cleanly to a future ecosystem plugin. This ~5-line rubric is a deliberate, named DRY exception
    (duplicated from the concept `plugin-semver` also expresses) — standalone plugins cannot share
    a partial, and both derive from semver.org.
  - **Propagate to consumers** — generic: a bump alone does not update consumers; each consuming
    repo must re-pin the new version, reinstall, and re-run its own validation on its own cadence.
    **No specific consumer names or pinning policy** (exact-pin/no-caret is a project's own policy)
    — those live in the package's own `CLAUDE.md` (single source of truth + public-repo redaction).
  - **Checklist** — emit-on-trigger, mirroring `plugin-semver`'s.
  - Frontmatter TRIGGER: editing the source of a published npm package, or preparing to publish
    one. DO NOT TRIGGER: a `private: true` / unpublished `package.json`, or a consuming repo that
    only pins the dependency.

- **`plugins/npm-semver/hooks/`** — `hooks.json` + `require-npm-version-bump.sh` + `_lib.sh`
  (version-compare helper copied from `plugin-semver`'s `_lib.sh` — accepted standalone-plugin
  duplication). A `PreToolUse`/`Bash` gate on `git commit`:
  - For each **non-test source file** changed since the branch's merge-base with the default
    branch, walk ancestors to the nearest `package.json` (layout-agnostic, mirroring
    `plugin-semver`'s ancestor walk for `plugin.json`; handles monorepo workspaces).
  - **Publish gate = `private: true`.** If that `package.json` has `private: true`, skip it — this
    is the standard npm publish guard (npm refuses to publish private packages) and it prevents
    false positives on an app package even inside an installed repo. Otherwise, require the
    package's `version` to be **strictly raised since merge-base** (one bump per branch satisfies
    it — not per-commit). Block the commit with an actionable message if not.
  - **"Source" definition (the main design decision):** a safe over-approximation — tracked code
    files (`.ts/.tsx/.js/.jsx/.mjs/.cjs`) under the package, **excluding** test files
    (`*.test.*`, `*.spec.*`, `__tests__/`, `test/`, `tests/`), build output (`dist/`, `build/`),
    `node_modules/`, docs (`*.md`), CI (`.github/`), and dotfile config. The hook cannot see npm's
    public/internal API split, so it will occasionally demand a bump for an internal-only change;
    that over-bump is low-cost because consumers adopt on their own cadence (a bump doesn't force
    downstream work). Version parsing mirrors `plugin-semver`: 3-part numeric core, fail-closed;
    prerelease/`v`-prefix handling is a documented limitation to revisit (the concrete adopter
    uses plain `x.y.z`).
  - **Fail-open on indeterminate state, fail-closed only on a determinable miss.** If the hook
    cannot compute the answer — no merge-base (shallow CI clone), detached HEAD, non-git tree,
    unreadable `package.json` — it must **allow** the commit (blocking unrelated work on an
    unverifiable state is worse than a missed bump, which the skill still nudges). Block only when
    a non-private package with a changed source file has a `version` provably not raised since
    merge-base. (Fail-*closed* still applies to the narrow version-*parse* case within a
    determinable comparison, mirroring `plugin-semver`.)
  - On a passing commit where a bump is present, echo a one-line **propagate reminder** (the
    cross-repo propagation itself is unenforceable from within the package repo).

- **`claude/.claude/hooks/tests/test_require_npm_version_bump.py`** — verified location:
  `plugin-semver`'s own hook test lives centrally at
  `claude/.claude/hooks/tests/test_require_plugin_version_bump.py`, **not** inside
  `plugins/plugin-semver/`; mirror that placement exactly (import the hook script from
  `plugins/npm-semver/hooks/` by relative path, the same way the existing test imports
  `plugins/plugin-semver/hooks/require-plugin-version-bump.sh` — see its `VERSION_BUMP_HOOK`
  constant). Covers: bump-present-passes, bump-missing-blocks, version-*lowered*-blocks,
  `private:true`-skips, test-only-change-skips, docs-only-change-skips, monorepo nearest-package
  resolution, monorepo partial-bump (two packages changed, only one bumped → block the unbumped
  one), initial-version of a newly-added package (absent at merge-base → verified via direct read
  of `require-plugin-version-bump.sh` lines 212–216: a missing baseline `continue`s, i.e. treated
  as satisfied, not blocked — mirror this exactly, no new handling needed), fail-closed version
  parsing, and **fail-open on indeterminate git state** (no merge-base / detached HEAD).

### Registration + docs (claude-config)

- **`.claude-plugin/marketplace.json`** — add an `npm-semver` entry (`name`, `description`,
  `author`, `source: ./plugins/npm-semver`, `category`), no `version` field.
- **`docs/skills.md`** — add `npm-semver` to the "Project-scoped plugins" table and the
  `claude plugin install` command list.
- **`README.md`** — confirm whether it separately enumerates the marketplace plugins; if so, add
  `npm-semver` there too so the two docs don't drift.

### Adoption (a separate, consuming repo — not part of the claude-config PR)

- In a published-npm-package repo, after the plugin is merged and the marketplace is refreshed:
  `claude plugin install npm-semver@claude-config --scope project`.
- A package's own `CLAUDE.md` (or equivalent) — wherever it already documents its own
  consumer-pinning policy and consumer list — stays as the single source of truth for its
  package-specific propagation; the plugin references the concept generically and the list stays
  local to that repo, not duplicated here.

## Critical files

**Create (claude-config):** `plugins/npm-semver/.claude-plugin/plugin.json`,
`plugins/npm-semver/skills/npm-semver/SKILL.md`, `plugins/npm-semver/hooks/hooks.json`,
`plugins/npm-semver/hooks/require-npm-version-bump.sh`, `plugins/npm-semver/hooks/_lib.sh`, and
`claude/.claude/hooks/tests/test_require_npm_version_bump.py`.
**Modify (claude-config):** `.claude-plugin/marketplace.json`, `docs/skills.md`, and `README.md`
if it separately enumerates plugins.
**Separate repo action:** project-scope install of the plugin in whichever published-package
repo(s) adopt it (no file authored here).

**Reuse (no reimplementation):**
- `plugins/plugin-semver/` is the structural template for every file above — copy its plugin.json
  shape, SKILL.md section structure + checklist, `hooks.json` registration, the ancestor-walk +
  merge-base + dotted-numeric-compare logic in `require-plugin-version-bump.sh` / `_lib.sh`, and
  its hook test.
- semver.org 2.0.0 is the grounding source for the rubric (quoted verbatim, verified this session).

## Verification

1. **Hook unit test:** `.venv/bin/pytest` against the new hook test — covers the block/pass matrix
   above, including `private:true`-skip and test/docs-only-skip.
2. **Full suite + lint:** `.venv/bin/pytest claude/.claude/` and `.venv/bin/ruff check claude/.claude/`.
3. **Live hook smoke test (in a scratch repo with a `package.json` that has no `private: true`):** change a
   `src/*.ts` non-test file without bumping `version` → commit is blocked; bump `version` → commit
   passes with the propagate reminder; change only a `*.test.ts` or `README.md` → commit passes
   with no bump demanded; a `private:true` package with a source change → not blocked.
4. **Skill trigger check:** editing a published package's source surfaces the `npm-semver` skill's
   rubric + propagate reminder.
5. **Plugin-review discipline:** run `/code-review` (dispatches `skill-review` for the SKILL.md,
   `claude-hook-review` for the hook, and `plugin-semver` for the version field) before handoff.

## Out of scope

- Any multi-ecosystem (Cargo/Python/Go) support — npm only; portable rubric prose keeps a future
  sibling cheap (no near-term need).
- Expanding or renaming `plugin-semver`.
- Enforcing cross-repo consumer propagation (unenforceable from the package repo — stays advisory).
- Editing the adopter's package-specific `CLAUDE.md` consumer list (lives in that repo; already present).
- Renovate/Dependabot dependency automation for internally-scoped packages.
- Actually bumping any package (nothing is owed now — recent changes have been docs-only).
