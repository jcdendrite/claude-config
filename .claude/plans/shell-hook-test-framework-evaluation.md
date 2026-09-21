# Shell hook test framework evaluation: bats-core vs pure pytest

## Context

The engineer asked whether this repo should adopt bats-core alongside its
pure-pytest suite, motivated by the repo's heavy reliance on complex Bash hook
logic and a hypothesis that bats-core might yield better coverage and/or test
performance. Why now: the shell surface has grown to 110 tracked files,
including two large shared libraries (`claude/.claude/hooks/_lib.sh`
at 3,385 lines, `claude/.claude/hooks/_config.sh` at 1,258), and the engineer
wants to know if the testing approach has outgrown its foundation before more
shell logic accretes. Intended outcome: a grounded verdict on bats-core
adoption, plus whatever concrete remediation the investigation justifies. The
engineer supplied a secondary-source analysis arguing bats-core wins on
white-box mocking and loses on assertions/performance; that analysis has been
verified against primary sources.

## Approach

**Do not adopt bats-core.** Record the decision as a design-decision file,
complete the one test-coverage gap the investigation found, and add the
mechanical guard that would have surfaced that gap on its own. The two larger
findings (`install.sh` decomposition, shared white-box helper) ship as filed
issues, not as PR work: neither is caused by the framework choice, and both
would be the same refactor under any runner.

**`_lib_reviewer_persona_agents` is kept, not deleted.** It is the unconsumed
half of a three-member test-seam family whose other two members are both
consumed (rows 4–5), so M2 adds the missing consumer.

**Scope call (delegated to plan-architect):** neither "full remediation" nor
"cheap ones only" as framed — one step past cheap-ones, and short of full. It
adds a roster-sync test and a liveness guard, neither of which was on the
engineer's list, and drops the two large refactors, which were. Reasoning is in
rows 10–15 and in M2's scope paragraph.

### Assumption ledger

**Root:** The repo's shell surface (110 files, two shared libraries) is tested entirely through a Python harness, and no one has checked
whether that harness still reaches the shell surface adequately — so a framework
change is being weighed without a grounded picture of what it would fix.

**Givens** (conditions beyond this plan's reach):

- **G1 — bats-core ships no PyPI wheel; its installation reference lists distro
  package, Homebrew, npm, source clone, or Docker.** Upstream bats-core owns its
  distribution; this repo cannot make a wheel exist. Its tutorial also presents a
  git submodule as the quick installation, and an action named
  `bats-core/bats-action` exists in the bats-core organization. As of 2026-09-19; see the Sources line of
  `docs/design-decisions/bats-core-adoption-declined.md` (the decision doc).
- **G2 — ShellCheck's `.bats` support is undocumented in `--shell`'s help output
  and carries at least seven open false-positive issues** (#2041, #3222, #2873,
  #3263, #3229, #3247, #3509). Upstream ShellCheck owns both the help text and
  the issue backlog.
- **G3 — bats parallelism requires GNU parallel or a compatible replacement, and
  does not guarantee test ordering.** That is bats-core's documented design, not a
  configuration this repo can change.
- **G4 — `scripts/dev/fork-topology-probe.sh` requires a human reading live
  Claude Code fork output.** The harness's fork behavior is observable only at
  runtime by a person; no test framework changes that.
- **G5 — fork+exec is the likely dominant cost of the suite, from two separable
  causes, neither reachable from here.** The causes are in the decision doc's
  performance section (`docs/design-decisions/bats-core-adoption-declined.md`).
  Neither cause shrinks under bats, which execs the same Bash artifacts.

**Rows** (each describes the tree as it stood when the plan was written, before
M2 lands):

1. The white-box capability bats is proposed to add already exists in this repo,
   as `subprocess.run(["bash","-c", f". {_LIB_SH}; {call}"])`. `[verified: 151
   single-line matches of "bash", "-c" across 19 test files under
   claude/.claude/hooks/tests/ (a lower bound on sites, since some sites put the
   two strings on separate lines), not all of which source the lib;
   claude/.claude/hooks/tests/test_lib.py:141-150 wraps it as
   _run_lib_call(call, env), and :95-113 as _HARNESS_TEMPLATE/_run_harness for
   the emit_deny-predefining variant]`
2. Every function in the two shared libraries but one has a reference (row 3).
   That establishes reference completeness only: a reference is not execution,
   so it is not a coverage claim. `[verified: row 3's evidence]`
3. Of 103 functions in `_lib.sh` + `_config.sh`, exactly one has no reference
   anywhere in the repo: `_lib_reviewer_persona_agents`
   (`claude/.claude/hooks/_lib.sh:3044-3046`). The six other functions the
   discovery flagged as test-unreferenced all have production callers and are
   outcome-tested through them. `[verified: Grep over the whole worktree this
   session; independently reproduced by staff-sdet across three corpus
   definitions, and by ciso-reviewer and staff-platform-engineer by direct
   grep]`
4. The three roster accessors in `_lib.sh` are a single test-seam family, and
   every one of their callers is a test file. `_lib_review_only_agents`
   (`:2946`) is called from `test_deny_reviewer_tree_mutation.py:939` and
   `test_lib.py:1216`; `_lib_no_gate_release_agents` (`:3016`) from
   `test_agent_roster.py:656` and `test_lib.py:1205`;
   `_lib_reviewer_persona_agents` (`:3044`) from nothing. None has a production
   caller — printing a derived array to stdout is useful to nothing but a Python
   test reading the shipping source of truth. `[verified: repo-wide Grep for all
   three names this session]`
5. Nothing regression-tests that `_LIB_REVIEWER_PERSONA_AGENTS`'s derivation
   stays correct, where both siblings have exactly such a test.
   `test_no_gate_release_set_covers_every_review_only_agent`
   (`test_lib.py:1213-1222`) asserts the no-gate-release set is a superset of
   the review-only roster "by derivation not by copy"; `_review_only_roster()`
   (`test_deny_reviewer_tree_mutation.py:934-944`) pins roster length and
   per-member behavior. The reviewer-persona array — `_LIB_REVIEW_ONLY_AGENTS`
   minus exactly the `Explore | Plan) continue` line at `_lib.sh:3039` — has no
   equivalent. `.claude/plans/round3-review-consult-trigger.md:110` proposed the
   accessor/predicate pair by name mirroring the sibling pattern; only the
   predicate half got wired. `[verified: all four files read this session;
   provenance corroborated by ciso-reviewer]`
6. That derivation feeds two live enforcement gates, so the drift it is not
   protected against is gate drift. `_lib_is_reviewer_persona`
   (`_lib.sh:3052-3056`) reads the array and is called from
   `require-architect-consult.sh:68` and `log-reviewer-round.sh:142`. Both
   existing hook tests exercise it only against hardcoded literal agent names,
   never against the derived roster as a whole. `[verified: _lib.sh:3030-3056
   and both hook call sites read this session; ciso-reviewer independently
   confirmed the two production callers]`
7. Adopting bats through the system-package route means a new non-pip system
   dependency. `requirements-dev.txt`
   holds five wheels and nothing else; ShellCheck itself arrives as the
   `shellcheck-py` wheel; the only non-pip CI install is
   `apt-get install -y stow direnv`. bats would be a third apt package, plus GNU
   parallel (G3) for parallelism. `[verified: evidence pack section E;
   .github/workflows/tests.yml:150-156; staff-platform-engineer reconfirmed the
   five-wheel count and the single apt line]`
8. The bar against the system-package route is repo precedent, not a `CLAUDE.md`
   prohibition. The decision doc's Dependency section
   (`docs/design-decisions/bats-core-adoption-declined.md`) covers the
   git-submodule and `bats-core/bats-action` routes (G1) as facts, and the
   `tests.yml` stow/direnv precedent. `CLAUDE.md` § "Working in this repo" does
   not bar a mandatory non-pip system package, and the repo already installs two.
   `[verified: root CLAUDE.md "Working in this repo" section read in full;
   .github/workflows/tests.yml:144-156 read this session. The submodule and
   bats-action routes are stated in G1 and the decision doc as facts only, with
   no rule analysis]`
9. The performance hypothesis points the wrong way. A large share of CPU time is
   `sys` in the local observation, which points to a fork+exec-bound suite (G5),
   and bats' subshell-per-`run` (`lib/bats-core/test_functions.bash`) adds forks
   rather than removing them. The repo already runs `-n auto` with a CI
   `timing`/`-n0` serial split. `[verified: pyproject.toml:24
   addopts; the `sys` share is a local measurement from the evidence pack
   (sections D and F), which is not in the tree (row 19)]`
10. `install.sh`'s 18 `INSTALL_TEST_FIXTURE` marker pairs exist because the
    file's top-level logic mutates `$HOME` and tracked repo settings, so it can
    never be sourced — not because pytest lacks a seam. bats would hit the
    identical wall: `load` cannot safely pull in a script whose top level
    mutates the invoking environment either. The finding is therefore orthogonal
    to the framework question. `[verified: install.sh:1-36 header and
    presence-check block read this session; 18 marker pairs counted by grep]`
11. Decomposing `install.sh` is the largest and highest-blast-radius item in the
    finding set: ~995 lines rewritten into functions behind a
    `[[ "${BASH_SOURCE[0]}" == "$0" ]]` guard, plus 15 `test_install_sh_*.py`
    files rewritten from text-slicing to direct function calls. A sourcing-guard
    bug means a test run mutates the developer's real `$HOME`. Every stow
    consumer runs this file. `[verified: install.sh header, marker grep,
    test_install_sh_python_floor.py read in full]`
12. Rows 10 and 11 together put `install.sh` outside this PR under
    `~/.claude/CLAUDE.md` Scope discipline Axis 1 and Axis 4. Repo `CLAUDE.md`'s
    "plans in this repo affect all stow users" rule raises the review bar
    further rather than lowering it. `[verified: both CLAUDE.md files]`
13. The shared-white-box-helper duplication is real, and its correct signature
    is underdetermined. `test_lib.py` holds 84 of the 151 single-line
    `"bash", "-c"` matches. Nearly all are raw inline sites: only `_run_harness`
    and `_run_lib_call` (18 callers each) route through a helper. The other 18
    files carry 1–21 matches each. The same shape matches on 30 more lines in 9
    files outside `claude/.claude/hooks/tests/`, including
    `claude/.claude/tests/helpers.py`. At least four distinct invocation shapes
    exist: bare source-then-call with env
    (`test_lib.py:141`), prelude-before-source (`test_lib.py:95-101`),
    positional-args-after-`bash -c` (`test_require_skill_review.py:1289`), and
    symlinked-lib-directory isolation (`test_lib.py:64-85`). A helper covering
    all four needs roughly seven optional parameters. `[verified: greps and
    reads this session]`
14. A helper landed without migrating its call sites makes matters worse — it
    becomes one more pattern alongside the existing per-file copies. So the
    helper and the migration of every site row 13 counts are one unit of work, and that unit exceeds Axis 4 for this PR.
    `[verified: ~/.claude/CLAUDE.md Scope discipline Axis 4; repo CLAUDE.md
    single-source-of-truth carve-out "a small duplicated value that beats a bad
    abstraction"]`
15. `select-tests.py` has no representation of a second test runner:
    `select_pytest_targets` and `build_pytest_argv` only ever
    construct a pytest argv, and an unmatched path falls open to
    `FULL_SUITE_TARGETS` with reason `"unmatched-path"`. A `.bats` file would
    trigger a full pytest run that never executes it. This describes no live
    defect — no `.bats` file exists and this plan decides none will — so it is
    the first implementation task of a decision being declined, tracked against
    the reconsideration trigger rather than as standing backlog. `[verified:
    `select_pytest_targets` in claude/.claude/scripts/select-tests.py read this
    session; ciso-reviewer reconfirmed the unmatched-path fall-open]`
16. Full remediation was the engineer's leaning, with cheap-ones-only explicitly
    left open and the call delegated to plan-architect. `[engineer-verified]`
17. All four findings are to be captured as tracked follow-ups regardless of
    what this PR implements. The engineer confirmed a direct in-PR fix
    discharges this for the finding it covers: M2's roster-sync test satisfies
    row 17 for that finding without also requiring a filed issue. The other
    three findings still get Issues A, B, and C per the sequencing below.
    `[engineer-verified]`
18. No dead-function or liveness guard exists for the shell libraries today.
    `test_shellcheck.py` lints every tracked script, but ShellCheck cannot see a
    cross-file unused function. `[verified: grep for
    dead/unused/uncalled/liveness across claude/.claude/hooks/tests/;
    staff-platform-engineer reconfirmed test_shellcheck.py is the only
    shell-lint test there]`
19. Local wall-clock timings are not cited: they come from a contended
    machine with other worktrees' pytest running concurrently.
    `docs/design-decisions/fixture-setup-caching-declined.md:5-7` records the
    repo's own precedent that local `-n auto` timings diverge sharply from the
    4-vCPU CI runner and produced contradictory readings there. `[verified: that
    file read this session and reconfirmed by two reviewers. The evidence pack
    (section D) is a session artifact absent from the tree, so timings are
    unverified and the decision doc omits them]`
20. `TestCrossDomainReadCompleteness` cannot see M3's corpus enumeration, so the
    cross-domain question is settled by direct analysis of `DOMAIN_RULES` and
    `CROSS_DOMAIN_EXCEPTIONS`. **Forward** (the guard reads production shell
    files): any tracked shell file edit selects `HOOKS_TESTS_DIR` or falls open to
    the full suite as an unmatched path. **Reverse** (the guard reads `.py` files
    outside `claude/.claude/hooks/`): some such edits do not select the guard. The
    rule table is canonical for which ones, and issue #1045 tracks the gap. CI's
    full suite (`tests.yml:165,172`) is the backstop.
    `[verified: test_select_tests.py:67-113 and :341-363 read this session;
    select-tests.py `DOMAIN_RULES` read this session; tests.yml:165,172 read by
    staff-sdet (both full-suite runs are gated by the detect step, so "CI's full
    suite" means "not scope-selected"); non-selection of the guard for some `.py`
    edits outside hooks/ probed by staff-sdet via select_pytest_targets, on the
    paths it probed only; reverse-direction gap first identified by staff-sdet and
    reconfirmed here]`
21. `scripts/list-shell-files.sh` is the repo's single definition of the
    tracked-shell-file set, `git ls-files -z`-based and already consumed by both
    the CI shellcheck step and `test_shellcheck.py`, which re-derives the set
    independently and asserts agreement. A `Path.rglob` alternative would walk a
    gitignored in-tree `.venv/` and diverge between a dirty local tree and CI's
    clean checkout. `[verified: scripts/list-shell-files.sh:1-25 read this
    session; staff-platform-engineer raised the divergence risk independently]`
22. Plugins are an independent namespace, not a shared one. Every plugin hook
    sources its own vendored lib — `${CLAUDE_PLUGIN_ROOT}/hooks/_lib.sh` or
    `${0%/*}/_lib.sh` — never this repo's `claude/.claude/hooks/_lib.sh`, and
    seven function names are defined independently in both trees (`_lib_jq`,
    `_lib_parse_tool_input_or_deny`, `_lib_config_dir`, `_lib_capped_for`,
    `_marker_lib_repo_hash`, `_lib_marker_value_present`,
    `_lib_chains_marker_write_before_commit`). A name match inside `plugins/` is
    therefore evidence about a different function. Excluding `plugins/` costs
    nothing today: staff-sdet's corpus definition (b) excluded it and returned
    the same single zero-reference function. `[verified: Grep for lib-sourcing
    across plugins/*.sh this session; collision list and definition-(b) result
    from staff-sdet]`
23. Comment-only mentions of these function names occur in practice, not just in
    theory. `plugins/lovable-cloud/lib/token-path.sh:14` names
    `_lib_config_dir()` in a comment with no call. A predicate that counts a
    comment as a reference would mark a function live forever on the strength of
    one `#` line — the same insufficiency this plan's own rejected alternative
    (b) concedes. `[verified: that line read this session; the general weakness
    raised by ciso-reviewer]`
24. A scanner expressed as a pure function is unit-testable against fixtures,
    and this repo already does exactly that for a comparable scanner.
    `TestModuleLevelRepoPathResolver` (`test_select_tests.py:161-164`) pins
    `_resolve_module_level_repo_paths`' grammar "directly against small
    ast.parse-able fixture strings… no real files involved," one case per
    grammar row plus negatives. `[verified: test_select_tests.py:161-184 read
    this session]`

### Mechanism justifications

**M1 — Decline bats-core; record it as
`docs/design-decisions/bats-core-adoption-declined.md`.** `anchors: root`. Rows
1, 2, and 9 remove the three motivations (white-box access, coverage,
performance); rows 7 and 8 supply the dependency facts: repo precedent for the
system-package route, and the submodule and action routes as facts only. The
decision does not rest on the dependency ground. The repo already has the
`*-declined.md` genre for exactly this shape.

**M2 — Add the missing roster-sync test for `_LIB_REVIEWER_PERSONA_AGENTS`, in
`test_lib.py`.** `anchors: row3, row4, row5, row6`.

`_lib_reviewer_persona_agents` is not dead code — it is an unconsumed test seam.
Every member of its three-accessor family exists to be called from a test and
from nothing else (row 4); two of the three have their test, this one does not
(row 5). Deleting it would remove the seam and leave a derivation feeding two
live gate hooks (row 6) with no drift regression, so a future contributor
closing that gap would have to re-add the exact function the deletion removed.
Adding the consumer instead closes the gap, gives the accessor a real caller,
and leaves the shipping shell surface untouched.

The test mirrors `test_no_gate_release_set_covers_every_review_only_agent`
(`test_lib.py:1213-1222`): read both rosters through their accessors and assert
the derivation rather than a copy — `set(reviewer_persona) == set(review_only) -
{"Explore", "Plan"}`. Pinning the excluded pair as a literal is the same
closed-set discipline `HARNESS_BUILTIN_NO_GATE_RELEASE_AGENTS`
(`test_agent_roster.py:649`) uses. It catches the shell-side `case` exclusion
(`_lib.sh:3039`) drifting from the Python-side literal. A new harness built-in
added to `_LIB_REVIEW_ONLY_AGENTS` with the exclusion left untouched passes,
since the name lands on both sides.

Row 6's gap (the predicate is never exercised against the derived roster) is
closed by a second pair of tests on `_lib_is_reviewer_persona`: one accepts every
member of the derived roster, and one rejects non-members and near-miss, prefix
and case variants. The roster is derived inside the test body, so a broken
accessor fails one named test rather than interrupting collection.

Over-powered-primitive check, two lighter primitives from this repo's own
toolkit: (a) *the prose comment already at `_lib.sh:3030-3035`*, which documents
the derivation — rejected because it is already present and did not prevent the
gap; (b) *a literal spot check that `_lib_is_reviewer_persona Explore` returns
false* — rejected because that is what `test_require_architect_consult.py` and
`test_log_reviewer_round.py` already do against hardcoded names, and it cannot
catch a rename inside `_LIB_REVIEW_ONLY_AGENTS`, which is the drift that
matters.

Scope: Axis 1 bucket 2 at worst — same footprint as the deletion it replaces,
non-cosmetic, PR stays coherent. Arguably squarely in scope rather than
incidental: the ticket asks whether the shell test surface is adequate, and "one
of three sibling roster derivations has no drift regression" is a coverage
finding, which is that question's own subject matter. Row 16 records the
engineer leaning toward more remediation rather than less; this call does not
rest on that, and nothing here contradicts it.

**M3 — Add an unconsumed-seam guard over `_lib.sh` and `_config.sh`.** `anchors:
row2, row3, row18, row20, row21, row22, row23, row24`.

Name it for what it is. This is a **dead-code / unconsumed-seam tripwire**, not
a coverage guard: a function referenced only from production code with no test
at all passes it. Row 2 establishes reference completeness, not coverage; row 3
establishes the separate fact that a seam sat unconsumed and nothing flagged it.
M3 addresses the second only, and M1's design-decision file must say so rather
than citing M3 as coverage evidence.

It is earned by a demonstrated instance, not a hypothesis: the repo did accrete
an orphaned accessor, it survived at least one full PR cycle, and it surfaced
only because a human asked a framework question and an agent ran a bespoke
103-function scan (rows 3, 18). Under M2 the guard's real meaning sharpens — an
unreferenced function in a shared library is the visible symptom of a seam whose
consumer was never written, which is exactly what happened here.

**Predicate.** A function fails when its name appears nowhere in the reference
corpus outside its own definition line. Six parameters, all fixed here so none
is left to implementation-time discretion:

1. **Definition set** — every function defined at column 0 in
   `claude/.claude/hooks/_lib.sh` and `claude/.claude/hooks/_config.sh`, matched
   by `^([a-z_][a-z0-9_]*)\(\)`. Column-0 anchoring skips nested or indented
   helpers. The regex matches all 103 definitions with no `function`-keyword,
   space-before-paren, or brace-on-next-line variants anywhere in either file
   (row 3).
2. **Reference corpus** — `scripts/list-shell-files.sh`'s output for shell
   files, plus every tracked `.py` file from `git ls-files`. Both sets are
   git-tracked, so a dirty local tree and CI's clean checkout agree and neither
   walks a gitignored in-tree `.venv/`. Shelling into the existing script rather
   than reimplementing its scope keeps the linted set and the scanned set one
   definition (row 21). Every tracked `.py` file, not only files under `tests/`:
   a reference from a non-test script is still a reference, and over-inclusion
   errs toward never failing CI on a live function.
3. **Namespace scope** — `plugins/**` is excluded from the corpus, keyed on a
   relative path segment (`"plugins" in path.parts` or equivalent) rather than
   an absolute `REPO_ROOT / "plugins"` prefix check — only the relative form is
   testable from a `tmp_path` fixture rooted outside `REPO_ROOT`. Plugin hooks
   source their own vendored `_lib.sh`, never this repo's, so a same-named
   function there is a different function (row 22). If this exclusion ever flags
   a function whose only reference is under `plugins/`, the response is to
   examine whether that reference is real, not to widen the corpus reflexively.
4. **Line filter** — lines matching `^[[:space:]]*#` are stripped from every
   corpus file before matching, in both shell and Python. A comment naming a
   function is not a caller (row 23).
5. **Self-exclusion** — the guard's own file is excluded from the corpus, so a
   future allowlist or diagnostic example naming a function cannot satisfy the
   predicate it is exempting.
6. **Match anchoring** — the match anchors on the character immediately
   following the name, so a longer sibling function name cannot satisfy a
   shorter name's predicate merely by containing it as a prefix —
   `re.search(rf"{re.escape(name)}(?![A-Za-z0-9_])", corpus_text)` or
   equivalent. `_` is a regex word character, so an unanchored or
   `\b`-guarded-only match does not separate `_lib_capped` from its occurrence
   inside `_lib_capped_for`; five such prefix pairs exist today in `_lib.sh` +
   `_config.sh` (`_lib_capped`/`_lib_capped_for`,
   `_config_resolve`/`_config_resolve_fail_safe`,
   `_lib_active_bypass_marker_live`/`_lib_active_bypass_marker_live_and_touch`,
   `_lib_command_concludes_commit`/`_lib_command_concludes_commit_shape`,
   `_lib_extract_git_subcmd`/`_lib_extract_git_subcmd_args`), none a live
   false-negative today but each a latent hole in exactly the drift this guard
   exists to catch.

**Residual, named rather than closed.** A name appearing only inside a Python
docstring or a shell heredoc still satisfies the predicate. Closing that needs
AST parsing on one side and shell parsing on the other, buying precision a
tripwire does not need. The guard reports a suspicious absence; it does not
prove reachability. This sentence belongs in the module docstring, not only
here.

**Is this a stack of defensive layers or one specification?** The distinguishing
test in `~/.claude/CLAUDE.md` is whether each layer closes a gap the previous
layer created. None of these does. A textual scan has exactly these free
parameters — which definitions, which files, which of those count, which lines
within them, and how a name is matched — and all are fixed here. Fixing them
completes a specification rather than stacking defenses:
remove any one and the predicate is not weaker, it is undefined. The
compounding-layers tell would fire if the comment filter forced a heredoc filter
which forced a string-literal filter; this stops at the first and names the
residual instead of adding the second. Two
further properties hold: the guard needs **no allowlist at landing**
(zero violations once M2 lands, so the exception list stays empty and stays a
real signal), and it is a **name-reference scan, not a call-graph analysis** —
conservative in the safe direction, since indirect invocation via `"$fn_name"`,
`trap`, or `eval` still puts the literal name somewhere.

It deliberately does **not** require a *test* name-reference per function — that
would force six fake unit tests where outcome-through-caller coverage is correct
(row 3).

**Proving the predicate — two proofs, because they prove different things.**

*Implementation ordering, one-shot against the real tree:* write the guard first
and run it before adding M2's roster-sync test. It must fail and name exactly
`_lib_reviewer_persona_agents`. Then add M2 and confirm it passes. This is the
only point in the repository's history where a genuine zero-reference function
exists to demonstrate against, and M2-as-addition makes the demonstration the
natural implementation order rather than a revert-and-restore dance.

*Fixture unit tests, durable:* extract the scan as a pure function —
`unreferenced_functions(lib_paths, corpus_paths) -> list[str]` — and pin each
in-scope parameter against small `tmp_path` fixtures:

- a referenced and an unreferenced function in a synthetic lib (general
  true-positive/true-negative discrimination);
- a function whose only mention is a `#` comment, which must still be reported
  (parameter 4);
- a function whose only mention sits in a path the namespace rule excludes,
  which must still be reported (parameter 3);
- three negative cases for the definition regex — `function foo() {`, a space
  before the paren, and a non-column-0 indent — none of which may be picked up
  as a spurious definition (parameter 1);
- a function whose only mention in the fixture corpus sits at the guard's own
  module path, which must still be reported as unreferenced (parameter 5);
- a function whose name is a literal prefix of a second, referenced function's
  name (e.g. `foo` and `foo_bar`, mirroring the real `_lib_capped`/
  `_lib_capped_for` pair) where only `foo_bar` is called — `foo` must still be
  reported as unreferenced, proving the anchored match (parameter 6) does not
  let the longer sibling's occurrence stand in for the shorter name.

Parameter 2 (corpus enumeration) has no fixture case of its own: shelling into
`scripts/list-shell-files.sh` plus `git ls-files` is the caller's job, resolved
before `unreferenced_functions` ever runs, so it is not a property of the pure
function to pin against a fixture. It rests on the one-shot real-tree assertion
below rather than on a durable per-CI-run fixture, and a future refactor of the
corpus-building step is unguarded by anything in this file.

`TestModuleLevelRepoPathResolver` (`test_select_tests.py:161-164`) is this
repo's own precedent for pinning a scanner's grammar against fixture strings
with no real files involved (row 24); that precedent now holds for parameters
1, 3, 4, 5, and 6, not for parameter 2. The one-shot proof expires at this
commit; the fixtures keep proving each covered parameter on every CI run.

Over-powered-primitive check, three lighter primitives: (a) *no guard at all,
relying on M2's roster-sync test* — rejected because that test protects one
derivation and says nothing about the other 102 functions, so the next
unconsumed seam accretes with the same silence this one did; (b) *a comment
convention marking a function intentionally unused* — rejected twice over, since
this repo's habit is that a convention ships with its enforcing test
(`test_hook_alignment.py`, `test_design_decision_files.py`,
`test_pytest_collection_config.py` are the precedents), and a comment is
precisely what parameter 4 now strips; (c) *a guard scoped to the three roster
accessors only* — rejected because that family is already self-guarding, each
accessor's sole consumer being a named test whose deletion is visible in the
same diff.

**M4 — Two findings to filed issues, one to a trigger-gated issue.** `anchors:
row10, row11, row12, row13, row14, row15`. Covered in the sequencing call below.

### Sequencing and grouping (delegated to plan-architect)

**This PR — one phase, one `code-writer` dispatch:** M1 + M2 + M3, in that
written order but with M3's file authored and run before M2's test (see M3's
one-shot proof). Grouped, not merely co-located: M3's real-tree assertion fails
without M2, since `_lib_reviewer_persona_agents` would still have zero
references; and M3 is the mechanism that keeps M1's dead-seam claim durable
rather than a prose assurance dated 2026-09-19. One dispatch, because the three
share the same `_lib.sh` reading and splitting would restate it (plan-it's
do-not-split rule).

**Issue A — shared white-box helper** (`anchors: row13, row14`). Ready to start
immediately. Hand it a concrete starting point rather than a blank ticket: a
`run_shell_function` in `claude/.claude/tests/helpers.py` alongside the
`run_hook*` family, signature `run_shell_function(lib: Path, call: str, *,
prelude: str | None = None, args: Sequence[str] = (), stdin: str | None = None,
cwd: Path | None = None, home: Path | None = None, extra_env: dict | None =
None) -> subprocess.CompletedProcess`, reusing `_build_subprocess_env`
(`helpers.py:71-93`) for the `home`/`extra_env` pair. `prelude` absorbs the
`emit_deny`-predefining variant — prepended before the `. {lib};`, matching
`_HARNESS_TEMPLATE`'s ordering, which is load-bearing (`test_lib.py:802-839`
asserts the failure mode when `emit_deny` is absent). The issue's **first** task
is to confirm one signature genuinely covers the four shapes in row 13 before
any migration; if it does not, the right answer is two helpers or none.
Migration lands in the same PR as the helper (row 14), which is why it is not
in this one. The first task also fixes the site set and a count predicate that survives
argument-per-line formatting, and it counts the helper-routed callers
separately. A single-line grep is only a lower bound: it matches 151 lines in 19
files under `claude/.claude/hooks/tests/`, and 181 lines in 28 files tree-wide.
The tree-wide set adds the 30 lines in 9 files that row 13 names, including
`claude/.claude/tests/helpers.py` (the proposed home, with three inline
sourced-lib calls of its own).

**Issue B — evaluate decomposing `install.sh` behind a sourcing guard**
(`anchors: row10, row11, row12`). Frame it as evaluate-then-do, not a foregone
refactor: the 18 marker pairs work today, are tested, and are documented in the
file header. Acceptance criterion is that the markers collapse into direct
function calls, with the `$HOME`-mutation hazard closed by the guard.

**Order A before B, if both are done.** This is the one genuine output→input
coupling among the issues: B rewrites 15 test files' extraction helpers into
source-then-call invocations, which is exactly Issue A's helper shape. Doing B
first invents another local `_run_block`; doing A first means B consumes the
shared one.

**Issue C — `select-tests.py` second-runner representation** (`anchors: row15`).
File it explicitly gated on the reconsideration trigger below, and cross-link it
from the decision record. It closes unread if the trigger never fires; that is
the correct outcome, not a failure.

### Reconsideration trigger for bats-core

Falsifiable, and split across two independent axes. **Capability axis — all
three must hold:**

1. A specific named function in `_lib.sh` or `_config.sh` cannot be exercised
   from Python via `bash -c '. lib; fn'`, with the reason the sourcing pattern
   cannot reach it stated concretely. Today the count is zero.
2. bats-core becomes installable from `requirements-dev.txt` alone — the
   decision doc's trigger condition 2 is canonical, including its provenance
   requirement — **or** a
   maintainer opens a new design-decision file proposing to widen the repo's
   apt-get precedent to cover a dependency that is a test *vehicle* rather than
   a binary the tests exercise directly, and that file is reviewed and merged
   (G1, rows 7 and 8). The first disjunct is a fact about the world; the second
   names a concrete, checkable artifact rather than restating that the
   decision has already been made. No `CLAUDE.md` rule forbids widening the
   system-package precedent — see row 8 — it is a choice the repo would be making
   deliberately, through that artifact.
3. ShellCheck documents `.bats` in `--shell`'s help output **and** the open
   `.bats` false-positive issues close (G2). The decision doc's trigger section
   is canonical.

**Performance axis — independent, one condition:** a profile of the CI
`-m "not timing"` pass attributes the majority of its time to pytest's own
per-test overhead rather than to subprocess fork+exec. A local, unreproduced observation finds a large share
of CPU time in `sys` (row 9), which says the opposite. A wall-clock threshold is deliberately *not*
the trigger: absolute wall time grows with test count and would fire for a cause
bats cannot address (row 9, G5).

## Critical files

Single phase, single `code-writer` dispatch — the three deliverables are
interdependent (M3's real-tree assertion fails without M2; M1 cites both).

**Create:**

- `docs/design-decisions/bats-core-adoption-declined.md` — the verdict, its
  grounds, and the reconsideration trigger. Slug satisfies
  `^[a-z][a-z0-9-]*\.md$` per `.claude/rules/design-decisions.md`. Format: H1,
  blank line, then `*2026-09-19.*` — a date-only provenance line with **no**
  `Formerly §N` clause (that phrase is reserved for pre-split content). No index
  to update; `docs/design-decisions.md` is a pointer file, not a list. Three
  content constraints: state that a large share of CPU time is `sys`,
  not local wall-clock absolutes, since concurrent-worktree contention makes them
  unreliable, following `docs/design-decisions/fixture-setup-caching-declined.md:5-7`
  (row 19); state the dependency facts per row 8 — repo
  precedent for the system-package route (tests exercise `stow`/`direnv` as real
  binaries, a test runner would be a vehicle), and the submodule and action routes
  as facts only; and do not cite M3's
  unconsumed-seam guard as evidence for the function-level-coverage claim — M3
  is a dead-code tripwire, not a coverage guard, and row 2 establishes only
  reference completeness (restated from M3's mechanism justification, so an
  implementer working from this list alone still sees it).
- `claude/.claude/hooks/tests/test_shell_lib_function_liveness.py` — M3's guard,
  plus its fixture unit tests. Structure it as a pure
  `unreferenced_functions(lib_paths, corpus_paths)` scanner, the fixture cases
  listed under "Proving the predicate," and the real-tree tests.
  Declare the two lib paths as **module-level** constants imported by bare
  name — `from helpers import HOOKS_DIR` (matching `test_lib.py:30,61`'s own
  convention), never `import helpers` followed by dotted `helpers.HOOKS_DIR`
  access. Only the bare-name form is resolvable by
  `TestCrossDomainReadCompleteness`'s `ast.Name` resolver, so a dotted read falls through
  unresolved with no failure signal. Under the bare-name form the constants are
  already covered by `DOMAIN_RULES`'s `HOOKS_DIR` row, so they introduce no new
  `CROSS_DOMAIN_EXCEPTIONS` requirement (row 20) — that coverage claim holds
  only under this reading. The corpus enumeration is invisible to that resolver
  either way. Module docstring states the durable facts and nothing from this
  plan's reasoning: what the predicate is, that a production caller alone
  satisfies it, that comment lines and `plugins/` are excluded and why, that
  the file excludes itself, the match-anchoring rule, and the docstring/heredoc
  residual.

**Modify:**

- `claude/.claude/hooks/tests/test_lib.py` — M2's roster-sync test, placed next
  to `test_no_gate_release_set_covers_every_review_only_agent` (`:1213-1222`)
  with a `_reviewer_persona_agents()` helper matching the
  `_no_gate_release_agents()` shape at `:1203-1210`. Same file, same idiom, same
  derive-not-copy claim.
- `.claude/plans/shell-hook-test-framework-evaluation.md` — this plan, committed
  per plan-it Step 7 since Critical files is non-empty.

**Do not modify:**

- `claude/.claude/hooks/_lib.sh` — no shell file changes in this PR.
  `_lib_reviewer_persona_agents` (`:3044-3046`), `_LIB_REVIEWER_PERSONA_AGENTS`,
  its building loop (`:3036-3043`), the comment block (`:3030-3035`), and
  `_lib_is_reviewer_persona` (`:3048-3056`) all stay exactly as they are. An
  implementer who reads M2 as "make the guard pass" and reaches for the deletion
  has inverted the mechanism.
- `.claude/plans/round3-review-consult-trigger.md` — a preserved historical
  record under Scope discipline Axis 3. It is cited as provenance in row 5 only.

**Reuse:**

- `helpers.REPO_ROOT` and `helpers.HOOKS_DIR`
  (`claude/.claude/tests/helpers.py:23-26`) for the guard's roots, imported by
  bare name (`from helpers import REPO_ROOT, HOOKS_DIR`) rather than via
  `import helpers` dotted access or new `parents[N]` chains — see the
  bare-name requirement under Critical files' "Create" entry for this guard.
- `scripts/list-shell-files.sh` for the shell half of the corpus, invoked rather
  than reimplemented, so the linted set and the scanned set remain one
  definition (row 21).
- `test_lib.py:1203-1222`'s accessor-helper-plus-derivation-assertion shape for
  M2.
- `TestModuleLevelRepoPathResolver` (`test_select_tests.py:161-164`) as the
  precedent for fixture-pinning a scanner's grammar.

**Non-file deliverables:** Issues A, B, and C per the sequencing above, filed
regardless of PR content (row 17). Issue A is #1041 (shared white-box helper),
Issue B is #1042 (`install.sh` sourcing-guard evaluation), and Issue C is #1043
(gated `select-tests.py` second runner). Issue #1045, which tracks the
scoped-selection gap in row 20, is also filed; the decision doc names all four.

## Verification

Run the repo's documented scoped command from the worktree root — not the full
suite, per repo `CLAUDE.md`:

```bash
.venv/bin/python3 claude/.claude/scripts/select-tests.py
```

The selection is the tool's call — do not widen by hand. Read the tool's own
output for what it selected; `SCRIPTS_TESTS_DIR` is not expected, because no
shell file changes in this PR.

Confirm these four by name rather than reading only the aggregate count:

- `claude/.claude/hooks/tests/test_lib.py` — M2's new roster-sync test and the
  `_lib_is_reviewer_persona` accept and reject tests.
- `claude/.claude/hooks/tests/test_shell_lib_function_liveness.py` — M3's
  fixtures and its real-tree tests.
- `claude/.claude/hooks/tests/test_design_decision_files.py` — the new decision
  file's slug grammar and provenance-line format.
- `claude/.claude/scripts/tests/test_select_tests.py::TestCrossDomainReadCompleteness`
  — a green run here proves the guard's two module-level lib-path constants are
  covered by an existing rule. It proves nothing about the corpus-enumeration
  reads, which its resolver cannot see (row 20); do not read a pass as clearing
  that question.

**Red-then-green, once, during implementation.** Author
`test_shell_lib_function_liveness.py` and run it *before* adding M2's test to
`test_lib.py`. It must fail and name exactly `_lib_reviewer_persona_agents`.
Then add M2's test and re-run: it must pass with an empty violation list. This
is the only commit in the repo's history with a genuine zero-reference function
to demonstrate against, and the fixture tests inside the guard file carry the
proof forward afterward.

Lint:

```bash
.venv/bin/ruff check claude/.claude/ claude-skills/
```

ShellCheck is not run standalone for this diff — no shell file changes, and
`test_shellcheck.py` sits inside `HOOKS_TESTS_DIR`, which the selection above
already includes, so the whole tracked-script set is linted as part of the suite
run regardless.

No manual grep step: nothing is deleted.

## Out of scope

- **Deleting `_lib_reviewer_persona_agents`.** It is an
  unconsumed test seam, not dead code (rows 4–5); deleting it would foreclose
  the roster-sync test M2 adds and leave a gate-feeding derivation with no drift
  regression (row 6).
- **Decomposing `install.sh`** — Issue B. Orthogonal to the framework choice
  (row 10), largest blast radius in the finding set (row 11), excluded by Axis 1
  and Axis 4 (row 12).
- **The shared white-box helper and its multi-site migration** — Issue A.
  Signature underdetermined (row 13); helper-without-migration is a net
  regression (row 14).
- **`select-tests.py` second-runner support** — Issue C. No live defect; gated
  on the reconsideration trigger (row 15).
- **Closing the reverse-direction gap in `CROSS_DOMAIN_EXCEPTIONS`** — see row 20
  and issue #1045. Closing it is a `select-tests.py` rule-table edit outside this
  diff.
- **Adopting bats as an optional, degrades-gracefully integration.** Declined on
  cost-versus-benefit rather than on rule; see the decision doc's "Optional,
  degrades-gracefully adoption is declined too" section.
- **Requiring a test name-reference for every shell function.** M3's predicate
  deliberately accepts a production caller alone; the stricter rule would force
  six fake unit tests where outcome-through-caller coverage is already correct
  (row 3).
- **Call-graph-accurate reachability analysis.** M3's residual (row 23's line
  filter leaves Python docstrings and shell heredocs counting as references) is
  named in the guard's module docstring, not closed. Closing it needs AST
  parsing on the Python side and shell parsing on the other, for precision a
  tripwire does not need.
- **Automating `scripts/dev/fork-topology-probe.sh`** — G4; a person must read
  live fork output, which no framework changes.
- **Publishing local wall-clock timings as a headline measurement** — row 19.
  State that a large share of CPU time is `sys` and omit the timings, since
  concurrent-worktree contention makes them unreliable.
- **Removing or rewriting the 20 fixture-marker pairs** (18 in `install.sh`, one
  each in `marker.sh` and `register-marketplace.sh`). They work, they are
  tested, and their fate belongs to Issue B.
- **A liveness guard over `claude/.claude/scripts/*.sh` or
  `plugins/*/hooks/*.sh`.** The investigation's evidence covers `_lib.sh` and
  `_config.sh` only, and widening the scan without first auditing those trees
  risks landing a test that fails on day one for reasons nobody has looked at.
  Plugins additionally need their own within-plugin corpus rule, since row 22's
  namespace exclusion runs both ways. Widen in a follow-up once M3's shape has
  proven itself.
