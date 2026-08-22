# Close three hook coverage gaps and make the Python floor a hard requirement

## Context

Three hooks carry self-documented coverage gaps that let the exact behavior
they exist to prevent through. A rule whose hook is incomplete cannot be
trimmed from `CLAUDE.md`, because the prose is then the only control — so
these gaps are also what blocks a separate, already-planned trim of the two
always-loaded instruction files.

Why now: three hooks' matching gaps currently keep four `CLAUDE.md` prose
rules from being trimmed (see branch `trim-global-claude-md`, whose plan
depends on this one). Three of the four gaps are hook-fixable. This PR
closes them so the trim PR
can delete the prose; it is the prerequisite half of a deliberate two-PR
split, and it ships first because at no point does a rule lose its
guarantee — the prose stays intact here while the hooks get stronger.

Intended outcome: `enforce-marker-script-shape.sh` catches bare redirects to
marker paths; `deny-network-installs.sh` catches path-prefixed manager
invocations; `ask-new-dependency-disclosure.sh` covers every manifest format
this repo can parse; and the Python version the hooks need becomes a stated,
enforced requirement rather than a silent assumption.

## Approach

Three matching-logic fixes plus one contract change. Each fix reuses a
pattern already established elsewhere in this hook suite rather than
inventing one, and each lands with the test that proves it.

The contract change is the load-bearing one and is stated first because the
dependency-disclosure fix depends on it.

### The Python floor becomes a hard requirement

`parse-manifest-dependencies.py` currently targets Python 3.9 because stock
macOS `/usr/bin/python3` is 3.9.6. That constraint is what has kept
`Cargo.toml` and `pyproject.toml` unparseable — `tomllib` is 3.11+ stdlib.

This PR makes Python **>= 3.11** a stated requirement for using
`claude-config`, enforced at install time, and removes the 3.9 constraint
from the parser. Rationale: the alternative postures are both worse. Silent
degradation on an old interpreter is the failure mode the current design
already warns about — a 3.10+ construct "would lint clean and only fail on a
stow user's real interpreter, silently, since the calling hook fails open on
any helper error" — and CI runs 3.12, so CI would never catch it. A stated
requirement with a loud install-time check converts a silent runtime failure
into a visible setup failure.

**Over-powered-primitive check.** Raising a language floor for every stow
user is a wider-scope mechanism than "parse two more manifest formats"
strictly requires, so two lighter primitives were surfaced to the engineer
and explicitly declined:

1. **Guarded import** — `try: import tomllib / except ImportError: tomllib
   = None`, giving TOML coverage on 3.11+ and falling through to uncovered
   on stock macOS, with prose covering the degraded case. Declined: it
   leaves two ecosystems on prose-only enforcement indefinitely and makes
   coverage silently environment-dependent, so a contributor cannot tell
   from the repo whether their manifest is guarded.
2. **Narrow the prose instead** — keep the 3.9 floor and scope the
   naming-duty imperative to name `Cargo.toml` and `pyproject.toml` as the
   manifests no hook covers. Declined: it makes the permanent gap a
   documentation problem rather than fixing it, and the prose is what this
   work exists to reduce.

The engineer chose the floor raise on the grounds that a stated hard
requirement is a cleaner contract than either silent degradation or a
permanent documented hole (row 1). This block records what was weighed, not
a re-litigation of the choice.

**Two facts bear on this decision and are recorded here so it need not be
re-derived:**

- **This repo already ships a 3.11-only feature that degrades silently by
  design.** `nudge-error-mode-analysis.sh:121-131` gates on
  `sys.version_info >= (3, 11)` for `transcript-analysis.py`'s
  `from datetime import UTC`, and handles a below-floor interpreter with
  fail-open plus opt-in gating — never an install-time hard fail. So the
  repo holds two different postures toward the same interpreter floor. This
  plan's install-time hard fail is the stricter one; the inconsistency is
  deliberate but should be named rather than discovered later.
- **The affected population is wider than stock macOS.** Ubuntu 22.04 LTS
  ships Python 3.10 and Debian 11 ships 3.9, while `README.md:90` states
  supported platforms as "Linux, macOS, or WSL2" with no minimum distro
  version. Step 2's remediation message must not imply this is a macOS-only
  concern, and Homebrew/pyenv is not the easy remedy on a locked-down Linux
  host that it is on macOS.

**Placement follows `install.sh`'s existing convention, which is not
uniform:** its `:6-14` block hard-fails on *presence* (`stow`, `git`, `gh`,
`jq`, `python3`); its `:799-808` block warns on *quality/config*. A version
floor is a quality check being placed, deliberately, in the fail block.

**Two enforcement points are required, not one.** Install-time enforcement
alone is insufficient: the hooks resolve `python3` from `PATH` at hook
execution time, so a user can pass `./install.sh` and still have stock 3.9
first on `PATH` afterward. `ask-new-dependency-disclosure.sh` already has an
interpreter sanity probe (its step 6, which detects `python3` absent or
present-but-failing); that probe gains a version check.

**Fail-open is reconsidered, not preserved by default.** The hook's
inverted, never-deny disposition exists because a parse failure on a
half-edited manifest must not block work. An interpreter *below the stated
floor* is a different condition: it is a broken install, not an expected
runtime state. This PR surfaces that case rather than swallowing it — see
Step 4 for the exact disposition, which stays non-denying but stops being
silent.

### What is NOT closeable, and why it stays prose

`~/.claude/private-projects.md` (the redaction blocklist) is opt-in and
absent by default; `install.sh`'s `check_private_projects_file` only prints
a TIP. The blocklist *mechanism* is complete and correct — what is missing
is data only the user can supply. Four options were considered and rejected:

1. **Widen the always-on structural detectors.** The six existing detectors
   exhaust what is structurally identifiable without a name list. Further
   candidates (email domains, generic title-case phrases) either reduce to
   needing a name list — Tier 3's own problem — or deny ordinary PR prose
   constantly.
2. **Auto-derive a starter blocklist** from SSH config hosts, sibling repo
   directory names, or shell history. Produces false confidence (always
   incomplete), accumulates the common-word noise
   `docs/private-project-redaction.md` already warns against, and makes a
   security-relevant decision without user review.
3. **Force population at install time.** `install.sh` cannot know what a
   user's private projects are; a forced placeholder reproduces the empty-
   file state `check_private_projects_file`'s second branch already detects.
4. **Escalate the nudge.** A real UX improvement, but it raises the odds the
   user arms the tier themselves — it does not arm anything by default.

**Conclusion: this tier cannot be made safe-by-default without user-specific
data.** No hook change closes it, so the CLAUDE.md prose covering it is not
eligible for deletion in the follow-on trim PR. Recorded here so that PR
does not re-derive it. Option 4 is deliberately **out of scope** — it is an
enhancement, not a gap closure, and conflating the two would let a UX tweak
masquerade as an enforcement fix.

### Assumption ledger

**Root problem:** three hooks self-document matching gaps that admit the
behavior they exist to block, and a fourth control is inert by default.

**Givens:**

- Stock macOS ships `/usr/bin/python3` at 3.9.6 with no `tomllib`; this repo
  cannot change what Apple ships. `[verified: parse-manifest-dependencies.py:5-12]`
- Hooks resolve `python3` through `PATH` at execution time, which the
  harness controls, not this repo. `[verified: ask-new-dependency-disclosure.sh:46-47]`

**Out of scope, not givens:** the redaction-nudge UX improvement (option 4
above); any CLAUDE.md prose edit (that is the follow-on PR).

**Assumption rows:**

| # | Assumption | Tag |
|---|---|---|
| 1 | Python floor becomes a hard, install-enforced requirement | `[engineer-verified]` |
| 2 | Two PRs, hooks first | `[engineer-verified]` |
| 3 | `enforce-marker-script-shape.sh`'s Bash arm only evaluates authority when the command text contains `marker.sh`, so a bare redirect bypasses it entirely | `[verified: enforce-marker-script-shape.sh:227,261-272; xfail test at test_enforce_marker_script_shape.py:815-841]` |
| 4 | Its Write/Edit arm already matches on the resolved target path via tilde-expand → `_lib_realpath_m` → case pattern | `[verified: enforce-marker-script-shape.sh:200-215]` |
| 5 | `deny-network-installs.sh` matches manager names by exact whole-word token, so path-prefixed invocations evade every check | `[verified: _install_check_* at :150-162,171-186,212-217,222-228; leftover-token equality at :107; residual pinned at test_deny_network_installs.py:93-102]` |
| 6 | `_lib_fragment_invokes_git` (`_lib.sh:440`) and `deny-private-project-refs.sh:222-223` already establish the `NAME` or `*/NAME` basename-match convention in this suite | `[verified: read directly]` |
| 7 | `ask-new-dependency-disclosure.sh` fires only on `basename == "package.json"`; its post-text reconstruction is already format-agnostic while its diffing is JSON-specific | `[verified: :109-114; parse-manifest-dependencies.py:174-192 vs :86-91,152-165,195-216]` |
| 8 | That hook never denies — every failure mode is an ask or a silent allow | `[verified: ask-new-dependency-disclosure.sh:10-11]` |
| 9 | CI runs Python 3.12, so a floor regression would not surface there | `[verified: .github/workflows/tests.yml:138]` |
| 10 | No `settings.json` wiring change is needed — all three hooks already fire on the correct tool surface with internal filtering | `[verified: settings.json:199-203,280-282,298-302,335-337,339-342]` |
| 11 | `install-dev.sh:78` runs `.venv/bin/pip install --quiet -r requirements-dev.txt`, currently allowed *because of* the path-prefix residual this PR removes | `[verified: test_deny_network_installs.py:267-282 docstring]` |
| 12 | The redaction blocklist tier cannot be armed by default | `[verified: install.sh:610-623; .gitignore:49; docs/private-project-redaction.md:10-26]` |

### What this PR licenses the trim PR to delete — per duty, not per rule

This plan's framing ("the trim PR can delete the prose") is broader than
what these fixes actually close, and the follow-on PR will read this section
as its authority. State it per sentence, not per rule:

| Duty | What this PR closes | What prose may be deleted |
|---|---|---|
| "Never write `~/.claude/*-markers/*` by hand" | `>`/`>>`/`tee`/`cp`/`mv`/`install`/`dd of=`/`sed -i` targets resolving to marker paths | **Nothing yet.** `python3 -c` writes, here-docs, `$(...)`-computed paths, and shell-function indirection stay open and are the reason this imperative exists. The flat imperative stays; only the content-hash *mechanism* explanation is deletable. |
| "Name every new package before it is fetched" | The **informational** layer's manifest coverage (`ask-new-dependency-disclosure.sh` gains `requirements*.txt`, `go.mod`, `Gemfile`, `Cargo.toml`, `pyproject.toml`) | The clause restricting the duty to `package.json`-adjacent cases. **Not** the duty itself — the *gate* layer (`deny-network-installs.sh`) retains five documented residuals plus whatever the npx-family fix does not reach. |
| "Installing new software autonomously is prohibited" | Path-prefixed manager invocation, including the npx family | The path-prefix-specific carve-out only. The five other documented residuals in the hook header are untouched. |
| Redaction, "caught by hook when populated" | Nothing — not closeable (see above) | **Nothing.** |

Each hook's own "Known gaps" header must be updated in this PR to name the
newly-*narrower* residual rather than silently dropping the closed bullet —
those headers are the evidence base the trim PR reads.

## Implementation steps

Each fix lands as its own commit with its tests, so a regression bisects to
one hook.

### Phase A — the Python floor

1. `claude/.claude/hooks/parse-manifest-dependencies.py` — rewrite the
   module docstring's version section: state Python >= 3.11 as the
   requirement and why (`tomllib` for TOML manifests), replacing the 3.9
   rationale. Do not leave the old constraint described as current.
2. `install.sh` — add a preflight check that resolves `python3` and fails
   loudly with a remediation message naming the required version if it is
   below 3.11. **Check, do not install** — the installer must not fetch
   software on the user's behalf. Place it with the other preflight checks,
   and make it fail the install rather than warn, per the hard-requirement
   decision (row 1).
3. `claude/.claude/hooks/ask-new-dependency-disclosure.sh` — extend the
   existing interpreter sanity probe (step 6) to also reject an interpreter
   below the floor. Disposition: **degraded-ask, not silent-allow** — the
   hook still never denies (row 8), but a below-floor interpreter is a
   broken install rather than an expected state, so it must surface. Reuse
   the existing degraded-ask path rather than adding a new emit shape.
4. `README.md` — state the Python requirement in the setup section, next to
   the existing install instructions. Name the Linux LTS surface, not only
   macOS (see the rationale block above).
4a. **New test** `claude/.claude/hooks/tests/test_install_sh_python_floor.py`
   — no `test_install_sh_*` file covers the preflight loop today. Follow the
   marker-delimited block extraction + fake-PATH `subprocess.run` pattern in
   `test_install_sh_session_concurrency_check.py:21-65`
   (`_extract_concurrency_check_block`, `_make_fake_bin_dir`): stub a
   `python3` reporting `3.9.x`, assert nonzero exit and that the remediation
   text appears.
4b. **New test** in `test_ask_new_dependency_disclosure.py` — Tier-3
   subprocess case with a stubbed below-floor `python3` on `PATH`, asserting
   **degraded-ask, not silent-allow**. Mirror the existing
   `TestHookDegradedAskDistinguishedFromOrdinaryAsk` class (`:815`).
4c. `test_ask_new_dependency_disclosure.py:334-341` —
   `test_source_parses_under_python_3_9_syntax` pins the *old* floor via
   `ast.parse(source, feature_version=(3,9))`. It will keep passing after
   the raise (import statements parse under any `feature_version`;
   `ast.parse` does not check module availability), silently asserting an
   invariant the module docstring now contradicts. Retarget it to
   `feature_version=(3,11)` or delete it in favour of 4b — do not leave a
   decorative pass.

### Phase B — marker redirect bypass

5. `claude/.claude/hooks/enforce-marker-script-shape.sh` — add a Bash arm
   that runs **before** the existing Stage 1 substring gate (Stage 1's
   `marker.sh` requirement is exactly what must be bypassed, row 3):
   - Fast-reject on `grep -qF '.claude'` against the command — every alias
     of a marker path contains that literal, mirroring Stage 1's own
     cheap-prefilter discipline.
   - On match, split via `_lib_split_fragments` and per fragment extract
     redirect targets: a `>`/`>>` operator (optionally fd-prefixed) followed
     by a path-shaped word — mirror `deny-network-installs.sh:84-91`'s
     `redirect_op_re`/`redirect_glued_re` construction — and a
     `tee [-a] <path...>` word scan, since `tee`'s targets are ordinary
     arguments and it accepts several.
   - **Redirect and `tee` alone do not close the bypass.**
     `cp <src> <marker-path>` and `dd of=<marker-path>` both bypass the
     current hook (exit 0) against a restricted `code-writer` agent, which
     carries unrestricted `Bash` (`agents/code-writer.md:4`) with no
     `permissions.allow` backstop for general-purpose write utilities. The
     scan must therefore also cover:
     `cp`/`mv`/`install` **last-argument** targets, `dd of=` glued
     arguments, and `sed -i` targets. `python3 -c "open(...).write(...)"`
     and here-doc bodies are **accepted residuals** — matching this file's
     own documented indirection carve-out (`:236-244`) — as are
     `$(...)`-computed paths and shell-function indirection.
   - **Enumerate those residuals in the hook's own header** in this PR, in
     the same "Known gaps (accepted, not chased further)" style the file
     already uses. Do not simply delete the closed bullet: the follow-on
     trim PR reads these headers to decide what prose is redundant, and a
     header that understates the remaining gap causes it to over-delete.
   - Resolve each candidate through the **same** tilde-expand →
     `_lib_realpath_m` → marker-shape case pattern the Write arm already
     uses (row 4). **Factor that into one function shared by both arms
     within this file** — not `_lib.sh`, since only this hook needs it — so
     the two cannot drift. Signature: `_marker_shape_match <path>` returning
     a boolean; it does tilde-expansion, `_lib_realpath_m` normalization,
     and the case-pattern test **only** — no agent-type read, no deny. The
     two arms differ in loop shape and the helper must serve both: the Write
     arm resolves **one** target through **two** candidate forms
     (`EXPANDED_TARGET`, `NORMALIZED_TARGET`, `:200-215`); the Bash arm
     resolves **N** extracted targets through the same two forms — a nested
     loop. Preserve the raw-tilde-expand candidate for redirect targets;
     dropping it and resolving only via `realpath` silently loses the
     no-realpath-available fallback for the new arm.
   - Note the ordering difference is intentional and not a defect: the Write
     arm reads `AGENT_TYPE` and `TARGET_PATH` in one `jq` call up front
     (`:153-161`) because that call is unavoidable there; the Bash arm defers
     the `jq` read until after a shape match, because its common case is
     zero candidates.
   - On a match, read `.agent_type` with the existing fail-closed `jq`
     pattern and deny via the existing `GATE_RELEASE_DENIAL_GUIDANCE` when
     the agent is in `_LIB_NO_GATE_RELEASE_AGENTS`. No match falls through
     to Stage 1 unchanged.
6. `claude/.claude/hooks/tests/test_enforce_marker_script_shape.py` — remove
   the `xfail` from `test_bash_redirect_write_to_planmode_sibling_bypasses_write_authority`
   (`:815-841`) and invert it to a deny assertion; update its docstring to
   remove the now-obsolete xfail rationale, since the bypass it documented
   is closed. Add: `tee` to a marker
   path (restricted agent) → deny; stow-fold physical path and
   `..`-traversal redirect targets → deny. The `:978-1024` citation spans
   two distinct tests — mirror **both** explicitly:
   `test_stow_directory_fold_physical_path_denied` (`:978-995`) **and**
   `test_traversal_path_denied_without_realpath` (`:997-1024`), the second
   because the nested-loop risk above is exactly the no-realpath fallback.
   Also: main session with no `agent_type` → allow (mirrors `:884`);
   `general-purpose`/`claude` → allow (mirrors `:931`).
   **Over-firing coverage must exercise the fast-reject's real blast
   radius.** `grep -qF '.claude'` matches any command mentioning that
   substring — far more than marker paths. A redirect to a path *outside*
   `.claude/` never reaches the shape check at all, so it proves nothing.
   Add at least one allow case where the target is `.claude/`-rooted but not
   marker-shaped (e.g. `> .claude/plans/foo.md`, `> .claude/scratch.log`)
   for a restricted agent — that is the branch that would over-fire.

### Phase C — path-prefixed manager invocation

7. `claude/.claude/hooks/deny-network-installs.sh` — change manager-**name**
   token matching (only the name; not verbs, not flags) from exact equality
   to `NAME` or `*/NAME`, per the convention already live at `_lib.sh:440`
   and `deny-private-project-refs.sh:222-223` (row 6).

   **Introduce a new matcher function local to this hook — do NOT edit
   `_lib_fragment_has_token` (`_lib.sh:554-557`).** That helper is shared by
   three hooks and is used for verbs and flags as well as names:
   `deny-reviewer-tree-mutation.sh:344-371` matches `--fix`/`--fix-only`,
   `deny-repo-relocation.sh:165` matches `--remove-source-files`. Widening
   it would give path-prefix tolerance to flag matching in two untouched,
   untested hooks. Mirror `_lib_fragment_invokes_git`'s shape instead.
   Carry the same `set -f`/`set +f` guard pair `_install_has_leftover_token`
   already uses (`:75-92`) — the word loop is unquoted, so an unguarded
   helper picks up glob expansion on a crafted `*`/`?` argument.

   Apply at every site in row 5, **including
   `_install_has_leftover_token`'s `pending_managers` equality at `:107`** —
   that one is load-bearing: without it a path-prefixed manager token is
   misread as a leftover package name and every path-prefixed *restore*
   starts denying. Also apply to the curl/wget/interpreter loop at
   `:261-270`, **and to `_install_check_npx_family` (`:190-207`)** — a
   structural sibling row 5 omitted, so `/opt/homebrew/bin/npx -y
   create-react-app` and path-prefixed `bunx`/`uvx`/`pipx run` would
   otherwise still bypass after this PR.

   Include the matched token in the deny reason (`:237,242,…`). The messages
   are currently generic, so a field over-fire from the widened matching is
   not self-diagnosable without reading the hook source — and this PR is
   deliberately widening what those messages must explain.
8. `claude/.claude/hooks/tests/test_deny_network_installs.py` — invert
   `test_path_prefixed_manager_allowed_is_a_named_residual` (`:93-102`) to
   assert deny and update its docstring. Add deny cases across every family
   (`/opt/homebrew/bin/npm install x`, `/usr/local/bin/pnpm add x`,
   `./node_modules/.bin/npm install x`, `/usr/bin/pip3 install x`,
   `/opt/homebrew/bin/uv add x`, path-prefixed curl piped to a
   path-prefixed interpreter). **Critically**, add allow cases proving
   restores still pass: `/opt/homebrew/bin/npm ci`, and
   `.venv/bin/pip install -r requirements-dev.txt`.
9. Update `test_install_dev_sh_own_invocation_allowed` (`:267-282`) — its
   docstring at `:271-274` states outright that it passes "for the
   path-prefix residual reason... not the restore-marker reason," so this
   PR changes its rationale. Update the docstring in the same commit; a
   stale rationale here is how the next contributor mis-diagnoses a break in
   `install-dev.sh`. **Mitigating context:** a companion test already exists
   at `:284-296` exercising the same command *without* the path prefix
   through the restore-marker logic, so that path is independently pinned
   today — the risk is lower than "unpinned," though not eliminated.
   Additionally, promote Verification step 5's smoke command into an
   automated Tier-3 subprocess test; as written it is manual and nothing
   forces a future contributor to re-run it.

### Phase D — manifest coverage

10. `claude/.claude/hooks/parse-manifest-dependencies.py` — add a
    per-format dependency parser dispatched by basename, each returning the
    declared-name set the existing `compute_new_dependency_names` diff
    consumes (its reconstruction half already generalizes, row 7):
    - `requirements*.txt` (glob — this repo has `requirements.txt` and
      `requirements-dev.txt`): per non-blank, non-`#` line take the name up
      to the first `==`/`>=`/`<=`/`~=`/`!=`/`[`/`;`/whitespace; skip
      `-r`/`--requirement`/`-c`/`--constraint`/`-e`/`--editable`/`--hash`
      control lines. A `-r other.txt` include pulling in a new file is a
      residual — only the edited manifest's own text is diffed. Document it.
    - `go.mod`: single-line `require module version` and the
      `require ( ... )` block form; strip trailing `// indirect`.
    - `Gemfile`: anchored `^\s*gem\s+['"]name['"]`.
    - `Cargo.toml` and `pyproject.toml`: parse with `tomllib` (available
      per Phase A). Read `[dependencies]`/`[dev-dependencies]`/
      `[build-dependencies]` for Cargo; `[project] dependencies` and
      `[project.optional-dependencies]` for pyproject.
      **`import tomllib` must be deferred inside the TOML parser function,
      not placed at module top.** This file's existing convention is
      unconditional top-of-file stdlib imports (`:60-67`); following it here
      makes the *entire* parser import-time-broken below 3.11 — killing
      `package.json` and `go.mod` coverage too, not just TOML — and makes
      Phases A and D mutually unrevertible.
11. `claude/.claude/hooks/ask-new-dependency-disclosure.sh` — replace the
    `basename == "package.json"` test (`:109-114`) with the recognized-
    manifest set above.
12. `claude/.claude/hooks/tests/test_ask_new_dependency_disclosure.py` —
    generalize the `_package_json` helper to
    `_manifest_file(tmp_path, content, name)`. Per format, mirroring the
    existing `TestComputeNewDependencyNames*` classes: dependency added →
    ask; version-only bump → no ask; comment and include lines ignored; and
    format-specific edges (go.mod block vs single line, `// indirect`,
    Gemfile options after the name). Extend
    `test_non_lowercase_basename_silent_allow` (`:589`) for the new
    recognized-basename boundary. Bound every regex to its declaration
    syntax rather than loose substring search — a spurious match costs a
    confirmation prompt, not a block (row 8), but noise erodes the signal.

### Phase E — documentation

13. `docs/hooks.md` — update the three hooks' entries to describe their new
    coverage, and record the `-r`-include residual from Step 10.
14. `docs/security-hardening.md` — it carries the duty-to-hook mapping for
    the package-naming rule; update the row now that non-`package.json`
    manifests are covered.
15. `docs/private-project-redaction.md` — record the "cannot be armed by
    default" conclusion and the four rejected options, so the follow-on
    trim PR and any future reader do not re-derive it.

## Verification

1. `../../../.venv/bin/pytest claude/.claude/` — must pass.
2. `../../../.venv/bin/ruff check claude/.claude/`.
3. `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` —
   three shell hooks change; this is the primary lint gate for them.
4. **`claude-hook-review` on each of the three modified hooks.** Required by
   `.claude/rules/review-pipeline-dispatch.md` for hook changes, and the
   fixes are matching-logic changes where a subtle over-match is the
   expected defect class.
5. **Contributor-setup smoke test, run explicitly**: confirm
   `.venv/bin/pip install --quiet -r requirements-dev.txt` is still allowed
   by the modified `deny-network-installs.sh`. This is the highest-risk
   regression in the PR (row 11) — verify it directly, not only through the
   unit test that asserts it.
6. **Interpreter-floor check**: confirm `install.sh` fails with a clear
   message on a below-floor `python3`, and that the hook's sanity probe
   surfaces rather than silently allows in the same condition.
7. `/code-review` on the full diff before commit.

## Out of scope

- Any edit to either `CLAUDE.md` — that is the follow-on trim PR
  (`trim-global-claude-md`), which this PR unblocks.
- Arming the redaction blocklist, and the install-time nudge escalation
  (option 4) — see "What is NOT closeable."
- The other five accepted gaps in `deny-network-installs.sh`'s header
  (`pip install -e <VCS-URL>`, bare `npx`/`pipx`, unrecognized
  value-taking flags, prose-mention false denies, curl/interpreter
  co-occurrence). Only the path-prefix gap is in scope; the rest stay
  documented residuals.
- Changing the never-deny disposition of `ask-new-dependency-disclosure.sh`
  for any condition other than a below-floor interpreter.
