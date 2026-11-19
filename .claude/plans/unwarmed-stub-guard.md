# Unwarmed stub guard

## Context

Goal: a standing guard fails a test when a sh-family PATH stub that a scaled-`timeout`-shim test can reach was not written with `write_prewarmed_stub`.

Ask: "#2 might be worth doing." (engineer, in the prior session, about this guard). On hook point the engineer chose "Let the architect decide". On migration scope the engineer chose "Same PR, split if over 400".

Why now: `write_prewarmed_stub` (`claude/.claude/tests/helpers.py`) exists and sh-family stubs that scaled-shim tests reach were migrated to it. Nothing stops a new or rebased stub from using a plain `write_text` plus `chmod`. The failure shows up as an assertion about something else, in a different test each run, on macOS only, with Linux CI green. The previous work missed one unwarmed `write_shim` in `claude/.claude/scripts/tests/_branch_records_helpers.py` until a manual review caught it.

Intended outcome: a launch whose PATH could reach the scaled `timeout` shim fails at that launch when it could also reach an sh-family stub that `write_prewarmed_stub` did not write. Approach holds the mechanics.

Evidence gathered in this session (exploration, not conclusions):

- The existing guard installs by autouse fixture in `claude/.claude/hooks/tests/conftest.py`, `claude/.claude/scripts/tests/conftest.py` and `plugins/lovable-cloud/tests/conftest.py`. Each replaces `subprocess.run` with a function (`install_shell_exec_guard`, `helpers.py` ~L566).
- 54 `write_scaled_timeout_shim(` calls in 26 files. `pyproject.toml` runs pytest with `-n auto`, so any patch is per worker process.
- `pyproject.toml` sets `tmp_path_retention_policy = "failed"`, so a post-session scan of basetemp sees nothing for passing tests. The check has to run at launch time.
- Of 14 `subprocess.Popen(` calls in scaled-shim test files, 0 run in a test that installs the scaled shim.
- Patching `subprocess.Popen` as a class collides with `_routing_guard_helpers.py:122` and `test_cursor_adapter_shim.py:2177`. `_routing_guard_helpers.py:138` already patches `Popen.__init__` in place.
- A partial dynamic scan (22 tests) found 0 unwarmed stubs. The large files were not reached, so the first-run flag count is unknown.
- No production-script copy was found on a scaled PATH.

## Approach

Each test in the hooks and scripts trees gets `subprocess.Popen.__init__` patched in place, so a launch whose PATH reaches the scaled `timeout` shim fails with `UnwarmedStubError` before its child starts, whenever that PATH could also reach an sh-family stub that lacks `write_prewarmed_stub`'s guard line. After the plan's own commit, the PR holds two commits. The first adds the guard and its contract tests, unwired. A spike then measures the guard's flags with the wiring applied but uncommitted. The second commit holds the wiring and the migration of every flagged stub together, so no tracked edit is reverted and re-applied.

**Hook point: (a), `Popen.__init__` patched in place** (M2). The engineer left this choice to the architect (row 25).
- (a) sees every launch that builds a `Popen` (row 3):
  - `run`, `call`, and `check_call` through `call`;
  - `check_output` through `run`;
  - a direct `Popen`;
  - asyncio's Unix subprocess transport.

  It changes no class identity, so code that bound `Popen` by name before the patch is still checked. The one other in-place `__init__` patch chains through it (row 5).
- (b), extending `guarded_run`, sees only `subprocess.run` and `check_output`. A direct `Popen`, `call`, `check_call` or asyncio launch escapes it. So does every launch in a test that swaps `subprocess.run` for its own function. Today's scaled tests may not launch those ways (row 14), but a standing guard must hold for the next test too.
- (c), a `Popen` subclass, adds no coverage over (a) and is easier to displace:
  - `test_cursor_adapter_shim.py`'s `stand_in` replaces `subprocess.Popen` for its tests (row 6).
  - A module that ran `from subprocess import Popen` before the patch keeps the original class.
  - `_routing_guard_helpers.py:138` would patch the subclass rather than the class the launch uses.
- The check runs before the real `__init__`, so a flagged launch starts no child. Popen's class-level `_child_created = False` makes the half-built object's `__del__` a no-op (row 4). A contract case pins the order with a call recorder installed beneath the guard (Critical files, Dispatch A1).
- xdist (open question 1, verified): `-n auto` runs each worker as its own process (row 7), and `monkeypatch` restores `__init__` at each test's teardown. The patch therefore never crosses a worker or a test. A contract case pins the restore, since a leaked wrapper would stack one layer per test in a worker.

**The check** (M1). `check_scaled_path_stubs_warm(env, cwd, basetemp: Path) -> None` goes in `claude/.claude/tests/helpers.py`'s scaled-timeout section, directly after `write_prewarmed_stub`. That puts it beside the three constants it reads: `_SCALED_TIMEOUT_MARKER_DIRNAME`, `_PREWARM_SHEBANGS` and `_PREWARM_GUARD_LINE`.
- PATH comes from `env` when `env` is not None, and from `os.environ` otherwise. Keys and values go through `os.fsdecode`, as in `_env_passed_without_path` (:490-493). When that source has no PATH key, the check returns at once.
- A relative or empty entry resolves against `cwd`, or against the process's cwd when `cwd` is None, because the child resolves it that way (row 37). The resolution copies `_shell_exec_target`'s handling of `cwd` (:435-437, row 36).
- Trigger: a resolved entry holds a directory named `_SCALED_TIMEOUT_MARKER_DIRNAME`, tested with `os.path.isdir`. Only `write_scaled_timeout_shim` creates one (row 1). With no trigger, the check returns after one `stat` per entry.
- Scan: every entry that holds the marker, plus every entry whose `os.path.realpath` is `basetemp` or lies under it (row 8). Containment compares path components, so a sibling such as `<basetemp>-other` lies outside.
  - The basetemp half reaches a stub directory separate from the shim's, such as `fake_gh_pr_exists`'s `fake-bin-pr` (row 9).
  - Basetemp bounds the scan to directories the run created, so system and repository directories on PATH are never read.
- In each scanned entry, the check reads each direct child that is a regular file, is not a symlink and has an execute bit.
  - It reads at most `_SHEBANG_READ_BYTE_LIMIT` bytes in binary mode, as `_has_env_bash_shebang` does (:441-447, row 36), so a non-UTF-8 file raises no decode error.
  - A file is flagged when its line 1 is in `_PREWARM_SHEBANGS` and its line 2 is absent or is not exactly `_PREWARM_GUARD_LINE`. Both lines fit in that limit (row 2).
  - An `OSError` from a child's `lstat` or read skips that child.
- An `OSError` from listing an entry skips that entry. With `os.path.isdir` returning False rather than raising, a missing, non-directory or unlistable PATH entry therefore never raises (row 53).
- After the scan, one `UnwarmedStubError` names every flagged path, as its PATH entry joined with its file name, and the entry holding the marker. It then gives both remedies in these words: "Write each with helpers.write_prewarmed_stub. A file that cannot carry the guard line, because it copies a tracked file or a test reads its text back, belongs in a directory that is not on this launch's PATH."
  - The second remedy is M5's move rule. Without it, a contributor whose file cannot take the guard line is pointed only at a fix that breaks their test.
- `class UnwarmedStubError(AssertionError)` sits beside it, with the docstring "A launch under the scaled timeout shim could exec an sh-family stub that write_prewarmed_stub did not write."
  - Its base matches `ShellExecGuardError` (`helpers.py:392`). Out of scope names the false negative that base allows.
- The check's docstring states two facts, one sentence each:
  - It raises when the launch's PATH reaches the scaled timeout shim and an sh-family executable in a scanned entry lacks the prewarm guard line.
  - Symlinks, other shebangs and entries outside `basetemp` that hold no marker are not read, because `write_prewarmed_stub` cannot write them or the run does not own them.

**The install** (M2). `install_unwarmed_stub_guard(monkeypatch: pytest.MonkeyPatch, basetemp: Path) -> None` comes right after the check.
- It saves the current `subprocess.Popen.__init__`. Then it installs a `functools.wraps` wrapper with `monkeypatch.setattr(subprocess.Popen, "__init__", ...)`, the shape of `install_shell_exec_guard` (:566-589).
- The wrapper reads `env` and `cwd` from its keyword arguments and runs the check before it calls the saved `__init__`.
- It checks `shell=True` launches too, because the shell resolves commands through the same PATH. `install_shell_exec_guard` skips them (:578, row 39), so that part of its shape is not copied.
- Docstring facts:
  - It wraps `Popen.__init__`, so every launch through `subprocess` or asyncio runs the check before the child starts.
  - `os.system`, `os.posix_spawn`, `os.exec*` and `pty` launches, and an `env` or `cwd` passed positionally, are not checked.

**Wiring** (M3). These two trees hold all 39 test files that can install the scaled shim (row 11).
- The `_guard_shell_exec` autouse fixture in `claude/.claude/hooks/tests/conftest.py` (:455) and the one in `claude/.claude/scripts/tests/conftest.py` (:1622) each gain a `tmp_path_factory` parameter.
- Each also gains the call `install_unwarmed_stub_guard(monkeypatch, tmp_path_factory.getbasetemp())`, and its conftest's `from helpers import` adds the name.
- Each docstring gains one sentence: "It also fails a launch under the scaled timeout shim whose PATH holds a stub write_prewarmed_stub did not write."
- `install_shell_exec_guard` does not change.
- Each tree gets a hand-built wired test, set up as Critical files gives it (Dispatch A2).
- The hooks tree also gets a real-writer wired test through `git_timeout_shim`. It ties the real writer and the fixture's PATH to the trigger (row 40).
- Together the wired tests are the spike's proof that the guard fires (Verification 2).
- `plugins/lovable-cloud/tests/` and the guards in `claude/.claude/tests/` stay unwired, since no test there can install the scaled shim (row 12, Out of scope). The structural test (M7) fails once one can.
- The off-switch for a false-positive storm is a revert of the two conftests' M3 edits, the two wired tests and the structural test. The migration stays, since a migrated stub's guard line acts only while its variable is set (row 16). The PR body names the off-switch's edits.

**The structural test** (M7). `claude/.claude/tests/test_scaled_shim_callers_are_guarded.py` (new) fails when a tracked file can install the scaled shim and no guard covers it. It copies the `git ls-files -z` and `ast.walk` shape of `test_pytest_collection_config.py:453-491` (row 41). Unlike that precedent, whose pathspec names three test trees, it lists every tracked file with no pathspec and keeps the names that end in `.py`.
- It reads each file as bytes and parses only one whose bytes hold one of the four names below. At the plan's base that is 41 files (row 11), so the parse cost grows with the files that name a shim, not with every tracked `.py`. The AST still decides what counts as a reference.
- A reference is an AST node that names `write_scaled_timeout_shim`, `_write_conditional_sleep_shim`, `git_timeout_shim` or `gh_timeout_shim`. The node is a `Name`, an `Attribute`, a function argument, an import alias, or a string constant equal to one of them, which covers `usefixtures`.
  - A definition or a docstring mention is not a reference, so `helpers.py` needs no allow-list entry.
  - The scan skips its own file, whose name tuple would otherwise count.
  - It skips a tracked `.py` that it cannot read, such as one deleted from the working tree but not from the index, or that does not parse. Python cannot import such a file to install the shim.
- A file is covered when its own code calls `install_unwarmed_stub_guard`, or when a `conftest.py` in its directory or an ancestor up to a given root does. The real scan's root is the repository root.
  - A call is an `ast.Call` whose function names `install_unwarmed_stub_guard`. An import alone is not a call.
  - The reference scan and the cover rule sit in one function that takes the file list and the root, so the seeded controls can call it on files under `tmp_path`.
- Its failure names each uncovered `path:line` and the fix: "Call install_unwarmed_stub_guard(monkeypatch, tmp_path_factory.getbasetemp()) from that tree's autouse fixture. In a tree with no conftest.py, such as claude/.claude/tests/, call it from an autouse fixture in the file itself."
  - The second sentence names the own-file arm of the cover rule. `claude/.claude/tests/` cannot take a conftest, because the packaging rule bars the `__init__.py` one would need (row 29).
- Positive control: the referencing set holds `claude/.claude/hooks/tests/test_require_ready_for_review.py`, which requests `git_timeout_shim` (:1069), and `claude/.claude/scripts/tests/test_ci_watch.py`, which calls `write_scaled_timeout_shim` (:1221). The two anchors show the scan reaches both trees and both reference kinds. Listing all 39 files would restate row 11's snapshot and fail on every rename, without catching more than the anchors do.
- Seeded controls give each reference shape, the docstring non-reference, each skip and each cover-rule arm its own case (Critical files, Dispatch A2).
- The cover rule sees that the call exists, not that it sits in an autouse fixture (Out of scope).

**Migration rule** (M5). Every path the guard flags is a site. The guard sets scope by location, not by which call a cap wraps, so the prior plan's four-condition rule is not re-applied.
- Trace each flagged file to the code that writes it: the test, a fixture or a shared writer (row 31).
- Replace that code's `write_text` and `chmod` with one `write_prewarmed_stub(path, script)` call. The edit shape and its first two rules are the ones at `.claude/plans/prewarm-test-stubs.md:59-61`: a later rewrite of the same stub also goes through the helper, and a shared writer migrates whole.
- Two kinds of flagged file cannot take the guard line: a copy of a tracked file, and a file whose text a test reads back by hash, diff or line position (row 16). Move such a file to a directory that is not on the launch's PATH, but only when the test's assertions still hold. Otherwise report it as unsettled and leave it unedited.
- Never edit an assertion, add a skip, or move a stub outside basetemp to clear a flag.
- An `UnwarmedStubError` raised inside one of the seven bare `pytest.raises(AssertionError)` blocks in `test_scaled_timeout_shim.py` would satisfy it. Each block's test also launches outside any such block with the same env, cwd and stubs, and a flagged stub fails that launch (row 43). A migration leaves that true: it moves no launch the guard could flag into such a block, and it adds no stub that only a block's launch reaches.

**Order and packaging** (M6). Every dispatch is a `code-writer`, which leaves its edits unstaged in the working tree and never stages or commits (row 47). The session stages, runs `/code-review` and makes every commit. Every commit message, the plan commit's included, stays true once later commits land, because the squash body carries each one to `main` (row 35).
0. **Plan commit.** `plan-it` Step 7 commits this reviewed plan alone, before A1 (row 55). `/code-review` gates that commit too, because the code-review gate exempts only an empty staged diff (`require-code-review.sh:129-137`). The plan then ships in the PR. Once committed and unmodified, it leaves the plan-review gate disarmed through A1, A2 and B, unless step 4 or 5 edits it (row 56).
1. **A1.** Dispatch A1. It writes M1, M2 and the contract tests, and runs Verification 1.
2. **Commit 1.** The session stages A1's two files, runs Verification 1's last bullet, runs `/code-review`, and commits. The branch then holds the plan commit and commit 1, with nothing uncommitted, which is the boundary step 6's split proposal uses.
   - Commit 1's message describes what the commit adds, and never says the guard is unused.
3. **Spike.** First the session checks the spike's tools with `scripts/venv-run.sh python3 -c "import shutil; print({name: shutil.which(name) for name in ('git', 'gh', 'timeout', 'gtimeout')})"`, the lookup the shim fixtures use (row 32).
   - When `git` or `gh` is missing, or `timeout` and `gtimeout` both are, the session asks the engineer through `AskUserQuestion`: "The spike needs git, gh, and timeout or gtimeout on the PATH its tests see, and this session lacks <names>. Agents may not install software. How should the spike run?" The options:
     - `Add to PATH`: the engineer names directories that already hold the tools, such as an installed coreutils `gnubin`, and A2 prepends them to PATH for its pytest runs.
       - Installing a missing tool stays the engineer's own step, in a separate terminal.
       - If the harness refuses the prefixed command, A2 reports the refusal verbatim. The engineer then runs Verification 2's commands in their own shell, or chooses `Lower bound`.
     - `Lower bound`: the spike runs as is, and its totals count as a lower bound. Without `timeout` or `gtimeout`, that bound is zero by construction, as the coverage bullet below says.
   - Then dispatch A2. It applies M3, the two wired-test files and M7, runs Verification 2, and returns the spike's flag table. It migrates nothing. The table answers open question 2, the first-run flag count. It also answers open question 3 by classifying every site the guard over-approximates (row 16).
   - Coverage is full when the tools resolved, the hooks real-writer wired test ran rather than skipped, and no skip reason among the 39 files names a missing `git`, `gh`, `timeout` or `gtimeout`, or a non-GNU `timeout` (row 32). Otherwise the totals are a lower bound.
   - With neither `timeout` nor `gtimeout`, no route test can write a marker (rows 1, 32). The spike's count is then zero by construction, not a measurement, and CI's run is the guard's first measurement (Verification 6).
   - A2's files stay uncommitted until commit 2. Until then the session runs no `/ready-for-review` and opens no PR, because any test run in this worktree runs the uncommitted wiring (row 49).
4. **Stops before B**, run by the session on A2's return.
   - When a wired test fails, the guard does not fire through its tree's fixture. Stop and report that to the engineer before any migration.
   - Stop and ask when the spike reports an unsettled site: "The spike flagged <K> files that can neither take the guard line nor leave the launch's PATH: <each path with its reason>. How should they be handled?" The options:
     - `Edit the test`: B may change each named test so its file can take the guard line, for example by skipping line 2 in its read-back. The answer gets its own engineer-verified row.
     - `Add an exemption`: the plan returns to `/plan-it` to design a named exemption, which reopens that Out of scope bullet.
   - Also stop and ask when a write site lies outside B's file limit (Critical files): "The spike found write sites outside the planned file limit: <file:line list>. Add these files to the change?" The options are `Add these files` and `Stop and replan`. On `Add these files`, the session adds them to Critical files with their own engineer-verified row.
   - After an `Edit the test` or `Add these files` answer, the session records it in this plan and then runs `/plan-review` on the plan before it dispatches B.
     - The plan edit re-arms the plan-review gate for every non-plan Write and Edit, B's included, until a clean review records the plan's new hash (row 56).
     - A `/plan-review` run records no consult-gate state (row 57).
     - The edited plan is staged with commit 2.
     - The cost is one plan-review round, paid only on these two paths.
   - With zero flags, skip B. On step 3's zero-by-construction path, that skip rests on no measurement.
5. **B.** Dispatch B, or B1 then B2 (Critical files). B migrates every flagged site on top of A2's uncommitted files and runs Verification 4.
   - The guard stops each test at its first flagged launch, so B's re-runs can flag sites the spike never reached, even at full coverage. B migrates such a site like any other.
   - When B returns a site as unsettled or outside its file limit, the session asks step 4's matching question, naming B's re-run in place of the spike. It handles the answer as step 4 does, recording and `/plan-review` included, before B continues.
6. **Commit 2.** The session stages A2's and B's files, and this plan when step 4 or 5 edited it, runs Verification 5, runs `/code-review`, and commits the wiring, the wired tests, M7 and the migration together.
   - The diff-size gate (`require-diff-size-prompt.sh`) denies the first commit that brings the branch to 400 measured changed lines (row 48). That deny is the post-spike size question row 45 refers to. The plan asks no other size question. By the projection, the gate denies commit 2 (Critical files, Projected diff).
   - On that deny, the session asks the engineer through `AskUserQuestion`, with exactly the options `Split now` and `Proceed anyway`: "This commit brings the branch to <N> changed lines against origin/main, as the diff-size gate measured them: commit 1's <C1>, the wiring, wired tests and structural test, and <S> migrated write sites in <F> files<, a lower bound because <K> tests skipped for a missing tool>. At plan review you chose Proceed anyway twice, at a projected 430 lines in round 2 and at a projected 580 in round 4. Neither projection was measured, and neither included the migration. Proceed anyway also covers any later migration commit that CI forces. Split now or proceed anyway?"
   - On `Proceed anyway`, the session follows the deny text: it states the count and base, runs `~/.claude/scripts/diff-size-ack.sh` as its own Bash call, and re-runs the denied commit unchanged. The branch's latch then covers every later commit (row 48).
   - If commit 1 already reaches 400, the gate asks at step 2 instead.
     - That question gives commit 1's measured count and cites both plan-time answers, as above.
     - It says that `Split now` there has no part to move: commit 1 is the whole of the work so far, and its guard and contract tests do not split apart. On `Split now` there, the session does not retry the commit (row 58) and returns the plan to `/plan-it`.
     - It says that `Proceed anyway` there also waives the size question for commit 2 and the migration, because the latch covers every later commit (row 48).
     - After `Proceed anyway` there, state the migration's measured size in the PR body, since no gate measures it.
   - On `Split now` at step 6, the session follows the deny text (row 58). It does not retry, and it agrees with the engineer which part moves and how, a stacked branch included.
     - This worktree's branch and index stay untouched as the one copy of the work, and the session does not remove this worktree.
     - The plan's proposal is that the plan commit and commit 1 ship alone as the first PR. Its branch starts at main through `branch-management` (row 50), so its `/ready-for-review` runs in a worktree that holds no wiring.
     - M7 moves with the wiring, because it fails until the conftests call the install. The split conversation names that against row 46.
     - The wiring and the migration do not split apart, because the wiring fails every site not yet migrated. A split between them goes back to `/plan-it`.
7. **Ship.** The session runs `/ready-for-review`, opens the PR, and runs Verifications 6-8.

**The architect-consult gate** (row 57) can deny a reviewer fan-out before the PR ships. On that deny, the session follows the deny text and `code-review/SKILL.md` § "Round-cap architect consult", passing this plan's path, and routes by the consult's verdict. This plan does not pre-answer that verdict.

**What each commit has run before it lands:**
- Commit 1 runs A1's scoped checks and the three tracked-file scanners that read a new test file (Verification 1). Nothing else tests commit 1 at its own SHA unless it ships alone: CI tests the PR head (row 34), and squash merging keeps only the final tree (row 35). When it ships alone on `Split now`, its own PR's `/ready-for-review` runs the full suite on it (row 24).
- Commit 2 runs the full suite (Verification 5). When the spike's coverage is full, that run measures the guard over every route test.
- On a lower bound, the tests that skipped could not trigger the guard (rows 1, 32), so CI is their first measurement. A flag CI shows first adds a migration commit (Verification 6), which leaves commit 2 failing at its own SHA. Squash merging keeps that state off `main` (row 35).

**Alternatives set aside:**
- **(b) and (c)**, as above. anchors: row3, row5, row6.
- **A `sys.addaudithook` hook on the `subprocess.Popen` audit event** (`subprocess.py:1858`). It mutates no stdlib object. But a hook cannot be removed (row 19), so it would stay live for the worker's whole life and run on every audited event in every tree. Scoping it per test would also take module-level state, where `monkeypatch` scopes (a) for free. anchors: row3, row19.
- **A docstring plus a failure signature only.** Nothing fails, so it cannot reach a stub already written on another branch. Exec-bit `chmod` calls also far outnumber `write_prewarmed_stub` calls (row 18), so the unguarded pattern is the one an author copies. anchors: root, row18.
- **A path-scoped rule line for test files.** This is guidance with the same gap: nothing fails when an author misses it or never loads it. anchors: root.
- **A per-test autouse teardown that scans the test's whole `tmp_path`.** It needs no registry, no stdlib patch and no per-launch cost, and it runs before the `failed` retention policy deletes the directory (row 10). But it has no PATH to scope by, so it flags every sh-family executable under `tmp_path`, such as a script copied into a test repository that no capped launch can reach. It also misses a stub in a session- or module-scoped `tmp_path_factory` directory, which lies outside the test's `tmp_path`. Only the launch knows its own PATH. anchors: row9, row10.
- **An assertion inside the bash shim.** It sees only its own `$1`, not the stubs the wrapped command execs. It also gives the shim a second job beside its logs, which are the suite's evidence that a cap fired (`helpers.py:3058-3072`). anchors: row1.
- **Auto-warming flagged stubs from the wrapper.** It would silently rewrite test-written files, putting a line-2 guard into text a test may read back. Its warm execs would also add `subprocess.run` calls that break exact-call spies (row 28). anchors: row2, row28.
- **Scanning only the entry that holds the marker.** It misses `fake-bin-pr`-style stub directories. anchors: row9.
- **Scoping the scan by PATH entries absent from the import-time PATH**, in place of basetemp. It would read repository directories a test puts on PATH, whose production scripts cannot take the guard line. anchors: row8.
- **Folding the launch check into `install_shell_exec_guard`.** All nine of its call sites would then need `basetemp` (row 12). One of them is `plugins/lovable-cloud/tests/conftest.py`, whose edit needs a plugin version bump (row 17), for trees with no scaled test. anchors: row12, row17.
- **Skipping relative and empty PATH entries**, as a listed false negative. The wrapper already receives `cwd`, and `_shell_exec_target` shows how to resolve against it, so closing the gap costs a few lines. anchors: row36, row37.
- **Wiring every test tree**, in place of the structural test. `claude/.claude/tests/` has no conftest, so each file there installs its own guards (row 29). The plugin tree needs a version bump for each edit (row 17). Neither holds a scaled test today. anchors: row17, row29.
- **A substring source-scan with a name allow-list**, like `test_scaled_timeout_shim.py:332`. A substring also matches a docstring mention such as `helpers.py:3245`, so the scan would need an allow-list that each new mention breaks. The AST form counts only code references (row 41). anchors: row41.
- **Commit 1 wired, then the migration, with a push embargo.** Commit 1 would fail the full suite, so every push and `/ready-for-review` run at it would rest on a rule to hold off. On `Split now` it could not ship alone. anchors: row34, row35.
- **A migration commit apart from a final wiring commit.** In a conftest that holds both a migrated site and the wiring, the wiring has to come out by hand before the migration commit and go back in after it, with a check that the re-applied text matches. Its only gain is a migration commit that passes at its own SHA, and nothing tests that SHA: CI tests the PR head (row 34), and squash merging keeps only the final tree (row 35). anchors: row34, row35, row47.
- **One commit for everything.** B may migrate a writer in `helpers.py` (Critical files), so a `Split now` would have to separate that file's hunks by hand. Committing the guard before the spike leaves a ready split boundary. That boundary costs one more reviewed state against the consult gate's cap (row 57). The plan accepts that cost. anchors: row26, row47, row57.
- **Recording step 4's answers outside the plan until commit 2**, such as in the PR body. It keeps the plan's hash fixed through B. But commit 2's `/code-review` stops and asks about any changed file that Critical files does not name (row 59). A plan edit after B would also deny every fix-round edit until commit 2 lands (row 56). anchors: row56, row59.
- **A plan-level size question after the spike, apart from the diff-size gate.** The gate asks at the first commit that reaches 400 whatever the plan does, with a measured count (row 48). A plan question before it would put the same decision to the engineer twice. The ack also cannot be recorded early, because at the spike the index holds only commit 1. anchors: row45, row48.
- **Installing the guard for the spike from a scratch pytest plugin loaded with `-p`**, so no tracked file holds the wiring before commit 2. Whether `-p` reaches xdist workers through the `pythonpath` ini needs a probe first. Commit 2 already holds the wiring and the migration together, so no tracked edit has to come back out. A Split-now first PR also runs its gates in its own worktree (Order step 6). anchors: row49, row50.
- **A `select-tests.py` row that selects M7 for every `.py` change** (Out of scope). anchors: row52.

Over-powered-primitive check: (a) mutates a stdlib class attribute, where (b) wraps one function. Both lighter primitives fail. (b) misses launch routes (row 3). The audit hook mutates nothing but cannot be scoped to a test (row 19). `monkeypatch` confines (a) to one test and restores it at teardown.

### Assumption ledger

**Root:** a sh-family PATH stub not written with `write_prewarmed_stub` can bring back the macOS-only cap-kill flake while Linux CI stays green, if a test that installs the scaled `timeout` shim can reach it. Nothing fails when such a stub is added (Context, "Why now"). This change fails the test at the launch whose PATH could reach the stub. [unverified for the flake's mechanism, which `.claude/plans/prewarm-test-stubs.md` row 1 records; verified for the guard's inputs: rows 1, 2]

**Givens:**
- **G1.** CI runs on Linux, where a cold stub's first exec has not been seen to outlast a scaled cap, so CI timing alone never flags one. Reason: GitHub owns the runner image, and the first-exec cost is a host property outside the repository (`.claude/plans/prewarm-test-stubs.md` G1, G2). [unverified: Context]
- **G2.** pytest runs with `-n auto` and `tmp_path_retention_policy = "failed"`. Reason: `pyproject.toml` governs collection for every contributor and for CI, and changing it is a repo-wide decision outside this plan. [verified: `pyproject.toml:24`, `:28`]
- **G3.** Agents run `select-tests.py` rather than the full suite, unless it selects the full suite itself. Reason: repo `CLAUDE.md` § "Commands" sets this. [verified: `CLAUDE.md` "Commands"]

**Rows:**
1. [verified: `helpers.py:3076`, `:3182-3186`; Grep for `_SCALED_TIMEOUT_MARKER_DIRNAME` and `scaled-timeout-markers` under `claude/`] Only `write_scaled_timeout_shim` creates the marker directory, inside the bin_dir that holds its `timeout` shim. When neither `timeout` nor `gtimeout` is on PATH it writes nothing and returns False (:3182-3184), so on such a host no test can trigger the guard.
2. [verified: `helpers.py:381`, `:3081`, `:3084-3086`, `:3114-3121`] `write_prewarmed_stub` accepts only `_PREWARM_SHEBANGS` and writes `_PREWARM_GUARD_LINE` as line 2, so that pair identifies its output. By hand count, the longest shebang, the guard line and both newlines total 121 bytes, within `_SHEBANG_READ_BYTE_LIMIT` (256).
3. [verified: local CPython 3.14.10 `subprocess.py:396`, `:415`, `:473`, `:555`; `asyncio/unix_events.py:858`] `call` builds a `Popen`, and `check_call` calls `call`. `check_output` calls `run`, and `run` builds a `Popen`. asyncio's Unix subprocess transport builds a `subprocess.Popen` too. Each looks the class up at call time, so a patch of `Popen.__init__` sees all of them.
4. [verified: `subprocess.py:815`, `:1132-1135`, local 3.14.10] Popen sets `_child_created = False` at class level "since __del__ checks it", and `__del__` returns at once while it is False. Other interpreter versions were not read; CI's run covers its own.
5. [verified: `_routing_guard_helpers.py:122`, `:133-138`; `_pytest/monkeypatch.py:392`; Grep for `Popen.__init__` and `Popen, "__init__"` across `*.py`] The only other `Popen.__init__` patch captures the current `__init__` when installed and calls through it. `monkeypatch.undo` restores in reverse order, so either install order chains and unwinds correctly.
6. [verified: `test_cursor_adapter_shim.py:2164-2177`] `stand_in` replaces `subprocess.Popen` with a factory that starts no process. Under (a), its tests launch nothing to check. Under (c), it would displace the subclass.
7. [verified: `pyproject.toml:24`; `xdist/plugin.py:324` and `xdist/workermanage.py:106` in the shared `.venv`] `-n auto` sets `tx` to `["popen"] * numprocesses`, so each worker is a separate Python process that an execnet popen gateway starts. A monkeypatch in one worker's test changes nothing in another worker.
8. [verified: `_pytest/tmpdir.py:111`, `:139-156`] `getbasetemp()` returns a resolved path, given or default, and `mktemp` directories, `tmp_path` among them, sit under it. Comparing each entry's `os.path.realpath` therefore still matches when the temp root is reached through a symlink.
9. [verified: `test_require_ready_for_review.py:41`; `hooks/tests/conftest.py:324-327`] `fake_gh_pr_exists` writes its `gh` into `tmp_path / "fake-bin-pr"`, while `git_timeout_shim` installs the scaled shim into `tmp_path` itself. A test that uses both therefore keeps its stub in a PATH entry that holds no marker.
10. [verified: `pyproject.toml:28-29`] `tmp_path_retention_policy = "failed"` deletes a passing test's directories, so only a check at launch time sees its stubs.
11. [verified: Grep for `write_scaled_timeout_shim|_write_conditional_sleep_shim|git_timeout_shim|gh_timeout_shim` across `*.py`; `hooks/tests/conftest.py:198-452`] 41 files match:
    - `helpers.py`, for the definition and one docstring;
    - the hooks conftest;
    - 39 test files, 32 under `claude/.claude/hooks/tests/` and 7 under `claude/.claude/scripts/tests/`.

    In the hooks conftest, only `git_timeout_shim` and `gh_timeout_shim` reach `_write_conditional_sleep_shim`, and a test requests each by name. So the 39 files are every test file that can install the scaled shim. No other file under `plugins/` or `claude/.claude/tests/` matches.
12. [verified: Grep for `install_shell_exec_guard(` across `*.py`] `install_shell_exec_guard` has nine call sites in seven files, besides its definition at `helpers.py:566`.
    - Three are tree conftests' autouse fixtures: `hooks/tests/conftest.py:460`, `scripts/tests/conftest.py:1627` and `plugins/lovable-cloud/tests/conftest.py:23`.
    - Three are module fixtures, at `claude/.claude/tests/test_shell_exec_guard.py:47`, `test_ci_path_filter.py:39` and `test_ci_workflow_gates.py:50`.
    - Three are in-test calls, at `claude/.claude/tests/test_prewarmed_stub.py:295`, `:302` and `:311`.

    None of the seven call sites outside the hooks and scripts trees can install the scaled shim (row 11).
13. [verified: `test_reconcile_project_plugins.py:1454-1500`] The residual `Popen` at :1471 runs in a test that never installs the scaled shim, and whose `bin_dir` holds only `make_reconciler_claude_shim`'s shim. No launch in it can trigger the guard, which settles open question 4.
14. [unverified] Exploration's AST pass found that none of the 14 `subprocess.Popen(` calls in scaled-shim test files runs in a test that installs the scaled shim. This dispatch relayed that result and did not re-run it. Under (a), the answer no longer limits coverage.
15. [unverified] The first-run flag count. A partial launch-time scan of 22 tests found 0 unwarmed stubs and never reached the largest files. Verification 2 measures it, as a lower bound when a test skips for a missing tool (Order step 3).
16. [unverified] The guard flags by location, so it also flags an sh-family executable in a scanned entry that no capped call reaches. Migrating such a file is harmless, since the guard line acts only while its variable is set. The exceptions are a tracked-file copy and text a test reads back. Exploration found no production-script copy on a scaled PATH. But it scanned the marker entry only, and this design also scans every basetemp entry. Verification 2 classifies every flag.
17. [verified: `.claude/rules/review-pipeline-dispatch.md`] Any edit under a plugin directory needs a plugin version bump, and a hook enforces it.
18. [unverified] Exploration counted about 386 exec-bit `chmod` calls in 130 test files, against about 67 `write_prewarmed_stub(` matches in 25 files. Relayed, not re-counted.
19. [unverified] A `sys.addaudithook` hook cannot be removed once added. This comes from the Python `sys` docs as recalled, not fetched this dispatch. Only an alternative that was set aside rests on it.
20. [unverified] The check's cost:
    - With no trigger, one `stat` per entry per launch.
    - With a trigger, it adds a `realpath` per entry, an `lstat` per child of each scanned entry and a read of up to 256 bytes per sh-family candidate.

    This is estimated at under a millisecond. Verification 1 measures it on the host for the PR body. A green `-m timing -n0` pass shows only that it disturbs no timing test, not that it stays under a millisecond. No contract case pins that a launch with no trigger lists or reads nothing. A regression there would slow launches without missing a flag. Verification 1's figure and CI's wall times (Verification 6) would show it, so the plan adds no case for it.
21. [verified: `helpers.py:3089-3097`, `:3122-3132`] `write_prewarmed_stub`'s warm exec runs with PATH set to `_PREWARM_EXEC_PATH`, the session bash's directory and `os.defpath`. That PATH holds no marker directory, so the guard never fires on a warm exec.
22. [unverified: `.claude/plans/prewarm-test-stubs.md` row 36] No test stub uses an sh-family shebang with trailing text, so matching exactly against `_PREWARM_SHEBANGS` misses none today. That plan verified this at its own base.
23. [verified: Grep of `docs/`, `README.md`, `claude-skills/`, both rules directories and `claude/.claude/agents/` for `prewarm`, `scaled.timeout`, `shell.exec guard` and `install_shell_exec_guard`, with no match] No doc describes these helpers, so the diff changes no doc.
24. [verified: `select-tests.py:514-525`, `:1039-1041`] `helpers.py` is a global trigger, so `select-tests.py` selects the full suite for any diff that holds it (`CLAUDE.md` "Commands", case 1). That covers this PR, and commit 1's own PR on `Split now`. A Split-now second PR holds `helpers.py` only when B migrated a writer there, and otherwise row 51 applies.
25. [engineer-verified: "Let the architect decide"] On the hook point, the engineer left the choice among (a), (b) and (c) to the architect.
26. [engineer-verified: "Same PR, split if over 400"] On migration scope, the migration ships in this PR, and a diff over 400 lines is the trigger to split.
27. [unverified] The session relayed an operational reading of row 26: a spike before the diff size is committed, a `Projected diff:` line, and a `Split now` / `Proceed anyway` question at 400 lines or more. That reading is the session's, not the engineer's words. It turns the label's split into a question at the gate. It also puts the boundary at exactly 400, where the label says "over 400".
28. [verified: `helpers.py:3146-3165`] `record_subprocess_runs` and `assert_each_stub_warm_exec_ed_once` assert the exact list of `subprocess.run` calls, so a wrapper that added calls would break them.
29. [verified: `claude/.claude/tests/test_shell_exec_guard.py:1`, `:44-47`; `.claude/rules/test-tree-packaging.md`] Contract tests for helpers' guard functions live in `claude/.claude/tests/`, which has no conftest. Each test file there installs what it tests itself. The rule bars `claude/.claude/tests/__init__.py`, which a conftest there would need.
30. [unverified] The session relayed that issue #1015 keeps helpers used across test trees in `helpers.py`; this dispatch did not read the issue. Both trees use the new functions, so they go there.
31. [verified: Grep of `^from \.(_\w+|test_\w+) import` across the route files] The route files import from these sibling modules:
    - `_lib_sh_helpers.py`, `_branch_records_helpers.py` and `_review_pr_stack_helpers.py`;
    - `test_git_floor.py`, for `_write_executable`, and `test_python_floor.py`;
    - `test_deny_pii_in_commits.py`, itself a route file;
    - `test_bash_floor.py` and `test_config_lib.py`, whose imported names, judged by name only, are not stub writers.
32. [verified: `hooks/tests/conftest.py:309-311`, `:320-323`, `:436-440`; `helpers.py:3182-3184`] `git_timeout_shim` skips without `git` or a `timeout` binary, and `gh_timeout_shim` skips without `gh` or a `timeout` binary. `git_timeout_shim` also skips a `sigterm_immune` call on a non-GNU `timeout`. A test that skips cannot flag a stub on that host. A test that calls `write_scaled_timeout_shim` directly gets False without `timeout` and runs with no marker, so it cannot flag either.
33. [engineer-verified: "Yes, quote and scope are right"] The engineer confirmed that the `Ask:` line's quote is their wording and that the plan's scope matches: the guard, its wiring in the hooks and scripts test trees, its contract tests, and migrating every stub it flags in the same PR. The scope list is the session's wording of the question; the label confirms it.
34. [verified: `.github/workflows/tests.yml:12-15`, `:51-54`] CI runs on a push to `main` and on `pull_request`, and on nothing else, so no manual trigger exists for a CI run of an intermediate state. The checkout step passes no `ref`. For a `pull_request` event, `actions/checkout` then checks out the PR head merged with the base as of the event; that is GitHub's documented default as recalled, not fetched this dispatch. Either way, no commit inside the PR is tested on its own.
35. [unverified: relayed from `.claude/plans/suppress-session-url-attribution.md:186`, its row 20, which recorded `allow_squash:true, allow_merge:false, allow_rebase:false` from `gh api`; not re-run] Squash is the only merge method this repository allows, so only a PR's final tree reaches `main`. The squash body concatenates the branch's commit messages, so each commit message here ships to `main`. To re-confirm, `gh api repos/{owner}/{repo} --jq '{allow_squash_merge, allow_merge_commit, allow_rebase_merge}'` returns true, false and false. It needs network and credentials, which this dispatch lacks.
36. [verified: `helpers.py:435-437`, `:441-447`] `_shell_exec_target` resolves a relative path against `os.fsdecode(os.fspath(cwd))`, or against `Path.cwd()` when `cwd` is None. `_has_env_bash_shebang` reads in binary mode, capped at `_SHEBANG_READ_BYTE_LIMIT`.
37. [unverified: relayed from the staff-sdet plan review's scratch probe on CPython 3.14.10] A relative PATH entry resolves against the child's `cwd`. An `env` passed positionally does not appear in the keyword arguments `Popen.__init__` receives. That an empty entry means the current directory is POSIX's PATH rule as recalled, not fetched.
38. [verified: local CPython 3.14.10 `asyncio/unix_events.py:858-860`, `asyncio/base_subprocess.py:38-43`] asyncio passes `shell`, `env` and `cwd` to `subprocess.Popen` as keywords. When that call raises, asyncio closes its transport and re-raises, so `UnwarmedStubError` reaches the awaiting test.
39. [verified: `helpers.py:569-571`, `:578`] `install_shell_exec_guard` skips `shell=True` calls by design, so copying its wrapper shape whole would skip them here too.
40. [verified: `hooks/tests/conftest.py:244`, `:254-262`, `:320-327`] `git_timeout_shim`'s `install` writes through `_write_conditional_sleep_shim`, which calls `write_scaled_timeout_shim(tmp_path)` whenever `sleep_seconds` is above 0. It returns a PATH that starts with `tmp_path`, and it skips when neither `timeout` nor `gtimeout` resolves.
41. [verified: `test_pytest_collection_config.py:453-491`] An existing structural test lists tracked `.py` files with `git ls-files -z` and walks each file's AST.
42. [verified: `_pytest/main.py:347-348`, `:816-826` and `_pytest/mark/__init__.py:282-284` in the shared `.venv`] `-m` deselects inside `pytest_collection_modifyitems`, and pytest counts collected tests after that hook. It returns `ExitCode.NO_TESTS_COLLECTED` when none remain, so a `-m timing -n0` batch with no timing test exits 5 with no failure.
43. [verified: `test_scaled_timeout_shim.py:255-259`, `:400-404`, `:420-427`, `:449-453`, `:470-488`] Seven blocks, at :258, :400, :420, :426, :449, :474 and :484, wrap shim launches in a bare `pytest.raises(AssertionError)`, which an `UnwarmedStubError` raised inside would satisfy. Each block's test also launches outside any such block, before or after it, with the same env, cwd and stubs (:255-256, :403-404, :423-424, :452-453, :470-472, :480-482). The guard's check reads only those inputs, so a flagged stub still fails that test.
44. [verified: `.github/workflows/tests.yml:28`, `:43`, `:131-132`, `:157`] Both CI legs run on `ubuntu-24.04` under a 30-minute `timeout-minutes`. The workflow installs `stow`, `direnv` and a pinned `git`, but neither `gh` nor coreutils, so CI's `gh` and `timeout` come from the runner image.
45. [engineer-verified: "Proceed anyway"] At plan review round 2 the plan's `Projected diff:` line stated about 430 changed lines, and the engineer chose `Proceed anyway` on the question "Split into smaller PRs now, or proceed?". The pre-spike estimate excludes the migration, so the plan's post-spike gate still asks again.
46. [engineer-verified: "Keep it (Recommended)"] At plan review round 2 the engineer chose to keep the structural test `claude/.claude/tests/test_scaled_shim_callers_are_guarded.py` (M7) in this PR, after being asked whether to keep it as a deliverable the original ask did not clearly call for. The description of what the test does is the session's wording; the label confirms keeping it.
47. [verified: `claude/.claude/agents/code-writer.md:4`, `:23-24`] A `code-writer` does not commit, push, stage or open PRs, and leaves its changes unstaged in the working tree. Its tools exclude `Skill`, so it cannot run `/code-review`. Every stage and commit here is therefore the session's.
48. [verified: `claude/.claude/hooks/require-diff-size-prompt.sh:4-16`, `:47-52`, `:196`; `claude/.claude/hooks/_lib.sh:5113`, `:5162`, `:5188-5205`; `claude/.claude/scripts/diff-size-ack.sh:27-33`] The diff-size gate denies a `git commit` when the index's diff against its merge-base with `origin/<base>` reaches 400 changed lines, additions plus deletions with `-M`, not counting `.claude/plans/`.
    - Once `diff-size-ack.sh` writes the branch's latch, the gate allows every later commit on the branch without measuring.
    - The ack exits 1 and writes nothing below 400, so no ack can be recorded before a commit reaches it.
    - A `code-writer` running the ack is denied.
    - The deny tells the session to ask with exactly `Split now` and `Proceed anyway`, and says the question may cite an answer given at plan time.
49. [verified: `claude/.claude/scripts/select-tests.py:1093-1106`] `select-tests.py`'s changed set joins committed, uncommitted and untracked paths, and pytest collects from the working tree. Any test run in this worktree while A2's files are uncommitted therefore runs them, the wiring included.
50. [verified: `claude/.claude/scripts/worktree-ensure.sh:207`, `:214`; `claude-skills/skills/branch-management/SKILL.md:116`] A worktree that `branch-management` creates starts at `origin/<default>`, and the skill never falls back to a hand-typed `git worktree add`. A Split-now branch therefore starts at main, not at this branch.
51. [verified: `select-tests.py:33-36`, `:609-616`, `:632-638`, `:692-693`, `:701`, `:997`] With no global trigger in a diff, a change under `claude/.claude/hooks/` selects the hooks tests, and one under `claude/.claude/scripts/` selects the scripts tests. A `.py` under either test tree or under `claude/.claude/tests/` selects `claude/.claude/tests/`. So does any `conftest.py` or `__init__.py` under `claude/`, `claude-skills/` or `plugins/`. A Split-now second PR always changes both conftests and adds M7, so its selection holds every tree whose tests the guard can fail.
52. [verified: `select-tests.py:1024-1045`; `test_select_tests.py:1593-1614`] A selection is the union of every matching row's targets, and existing cases assert exact target sets, as at `:1600` and `:1612-1614`. A row that selected M7 for each `.py` change under `plugins/`, `claude-skills/` or `evals/` would add M7 to every such case's expected set.
53. [unverified: Python `os` and `os.path` docs as recalled, not fetched] `os.path.isdir` returns False when its `stat` raises. `os.scandir` raises `FileNotFoundError`, `NotADirectoryError` or `PermissionError` for a missing, non-directory or unlistable entry, and each is an `OSError`. Verification 1's PATH-entry cases pin the check's behavior whichever way these hold.
54. [verified: `test_select_tests.py:143-161`; `claude/.claude/hooks/tests/test_ticket_reference_discipline.py:36`; `claude/.claude/tests/test_pytest_collection_config.py:455-466`] `TestCrossDomainReadCompleteness` globs the filesystem, so it reads an untracked new test file. `test_ticket_reference_discipline.py` and `test_pytest_collection_config.py`'s sibling-import check list files with `git ls-files`, so they read a new file only once it is staged.
55. [verified: `claude-skills/skills/plan-it/SKILL.md:151-154`; `claude-skills/skills/ready-for-review/SKILL.md:43`, `:123`] When Critical files names a file, `plan-it` Step 7 commits the reviewed plan to the implementation branch before implementation begins. `/ready-for-review` halts unless the working tree holds no unstaged, uncommitted or untracked change.
56. [verified: `claude/.claude/hooks/require-plan-review.sh:4-9`, `:29-32`, `:78-82`, `:201-209`, `:224-245`, `:263-286`] The plan-review gate arms while a plan under `.claude/plans/` is untracked or differs from HEAD. A committed, unmodified plan is historical and leaves it disarmed.
    - While armed, it denies every Write, Edit and MultiEdit aimed inside the repository, except one aimed at the plan itself, unless a plan-review marker holds the plan's current hash.
    - It filters on tool name, not agent type, so a `code-writer`'s edits are denied too.
    - Any edit to the plan, a ledger row included, changes the hash.
57. [verified: `claude/.claude/hooks/require-architect-consult.sh:76`, `:88-90`, `:115-142`; `claude/.claude/hooks/_lib.sh:5040`, `:5053-5060`, `:5064`, `:5102-5110`, `:5220-5226`, `:5242-5252`; `claude-skills/skills/code-review/SKILL.md:321-329`; `claude/.claude/hooks/log-reviewer-round.sh:80-82`, `:103-109`, `:148-149`] The architect-consult gate counts distinct reviewed states per branch, each a `<HEAD sha> <staged-diff sha256>` pair.
    - Once a branch has recorded 2 states, or 1 when `round_consult_round2_pilot` is enabled, a reviewer-persona spawn at a new state is denied until a `plan-architect MODE=consult` runs. After a consult, the gate allows 3 more.
    - A spawn during a live `/plan-review` is never gated or counted.
    - `code-review/SKILL.md` § "Round-cap architect consult" sets what the consult receives and how the session routes its verdict.
    - Each reviewer-persona spawn outside a live `/plan-review` records its state, `/ready-for-review`'s included.
58. [verified: `claude/.claude/hooks/require-diff-size-prompt.sh:196`] On `Split now`, the diff-size gate's deny tells the session not to retry, to agree with the engineer which part of the work moves, and to start that branch through the `branch-management` skill. When the engineer says the new branch stacks on this one, the deny names the `branch.<new-branch>.gh-merge-base` key to set.
59. [verified: `claude-skills/skills/code-review/SKILL.md:76`] When this branch's plan exists, `/code-review` stops and asks about any changed file that the plan's Critical files does not name, and a yes is recorded per `plan-it` Step 5.
60. [engineer-verified: "Proceed anyway (Recommended)"] At plan review round 4 the plan's `Projected diff:` line stated about 580 changed lines, and the engineer chose `Proceed anyway` on the question "Split into smaller PRs now, or proceed?". The "(Recommended)" suffix and the option's description were the session's proposal. The diff-size gate still asks with a measured count (row 48).

**Mechanisms:**
- **M1. `UnwarmedStubError` and `check_scaled_path_stubs_warm` in helpers.py's scaled-timeout section, which decide at launch whether the launch's PATH reaches an sh-family stub that `write_prewarmed_stub` did not write.** anchors: root, row1, row2, row8, row9, row16, row21, row22, row30, row36, row37, row53.
- **M2. `install_unwarmed_stub_guard`, which patches `Popen.__init__` in place through `monkeypatch` and checks before the real `__init__` runs.** anchors: row3, row4, row5, row6, row7, row14, row19, row20, row25, row28, row38, row39.
- **M3. Wiring from the hooks and scripts `_guard_shell_exec` fixtures through `tmp_path_factory`, with a hand-built wired test per tree whose cold stub sits apart from the marker's entry, and a real-writer wired test in the hooks tree.** anchors: row9, row11, row12, row17, row32, row40.
- **M4. Contract tests in a new `claude/.claude/tests/test_unwarmed_stub_guard.py`.** anchors: row2, row3, row4, row5, row21, row29, row36, row37, row38, row39, row53, row54.
- **M5. The migration rule: every flagged path is a site, which migrates through `write_prewarmed_stub`, moves off PATH, or is reported unsettled.** anchors: row16, row28, row31, row43.
- **M6. Commit 1 unwired from A1, a spike on A2's uncommitted wiring, and commit 2 holding the wiring and the migration together, with the diff-size gate's deny as the one size question and its `Split now` answer settled with the engineer.** anchors: row10, row15, row24, row26, row27, row32, row34, row35, row44, row45, row47, row48, row49, row50, row51, row55, row56, row57, row58, row59.
- **M7. A structural test that fails when a tracked file can install the scaled shim outside the guard's reach.** anchors: row11, row12, row17, row29, row41, row46, row52.

## Critical files

Paths are repo-relative. Line numbers are at the plan's base. Each dispatch below is a `code-writer` that leaves its edits unstaged in the working tree (row 47).

**Dispatch A1** (first). Its two files become commit 1 (Approach, Order step 2).
- `claude/.claude/tests/helpers.py`: M1 and M2, placed after `write_prewarmed_stub` (:3100-3143).
- `claude/.claude/tests/test_unwarmed_stub_guard.py` (new), with the module docstring "Contract tests for helpers.check_scaled_path_stubs_warm and helpers.install_unwarmed_stub_guard."
  - Conventions for every case below:
    - It builds its directories under `tmp_path`.
    - It passes `tmp_path` as `basetemp` and `None` as `cwd` unless it says otherwise.
    - A marker directory means an entry holding a directory named `helpers._SCALED_TIMEOUT_MARKER_DIRNAME`.
    - A raising case asserts that the message names each flagged path.
  - `check_scaled_path_stubs_warm`, one file in a marker directory (one parametrized test may carry these):
    - Raises: a cold stub for each of the four `_PREWARM_SHEBANGS`; a shebang-only file with no newline; a file whose guard line sits on line 3.
    - Passes: the stub written by `write_prewarmed_stub`; a symlink to a cold stub; an executable whose line 1 is `#!/usr/bin/env python3`; a cold sh file with no execute bit; an executable of non-UTF-8 bytes; an empty executable; a subdirectory.
  - `check_scaled_path_stubs_warm`, PATH layout and inputs:
    - A cold stub beside a shim that the real `write_scaled_timeout_shim(tmp_path / "bin")` wrote raises. The case skips when the writer returns False, and CI runs it.
    - A cold stub's message contains both remedies: `helpers.write_prewarmed_stub` and `not on this launch's PATH`.
    - A cold stub passes when no PATH entry holds a marker.
    - A cold stub in a separate PATH entry under basetemp raises and is named, while the marker sits in another entry.
    - Two cold stubs raise one error that names both.
    - With `basetemp` set to `tmp_path / "owned"` and the marker in an entry under it, a cold stub in a non-marker entry:
      - passes when the entry lies outside `basetemp`, including the prefix-sharing sibling `tmp_path / "owned-other"`;
      - raises when the entry equals `basetemp`;
      - raises when the entry is a symlink outside `basetemp` that points under it.
    - A cold stub in a marker entry outside `basetemp` raises.
    - With the ambient PATH set by `monkeypatch.setenv` to a marker directory holding a cold stub, `env=None` raises and an `env` with no PATH key passes.
    - With PATH removed from `os.environ` by `monkeypatch.delenv`, `env=None` passes.
    - An `env` with bytes keys and values raises.
    - Each of these three ways of naming the marker directory, with a cold stub in it, raises (one parametrized test):
      - its bare name, with `cwd` set to its parent;
      - an empty entry, with `cwd` set to the directory itself;
      - its bare name, with `cwd=None` after `monkeypatch.chdir` to its parent.
  - `check_scaled_path_stubs_warm`, PATH entries and files that cannot be read. One parametrized test may carry these. Each case returns None or raises `UnwarmedStubError`, and never raises another exception:
    - A marker directory holding one cold stub, followed by a nonexistent entry outside `basetemp`, raises naming only that stub.
    - A marker directory of warm stubs, plus a nonexistent entry under `basetemp` and a regular file used as an entry under `basetemp`, passes.
    - A PATH of nonexistent entries only, with no marker, passes.
    - A marker directory of warm stubs, plus a directory under `basetemp` with mode 000, passes. The case skips when `os.geteuid()` is 0, since root can list it, and it restores the mode before it returns.
    - A marker directory holding a cold stub with mode 0o111, which can be executed but not read, passes, with the same root skip and mode restore.
  - `install_unwarmed_stub_guard`:
    - The route cases and the pass-through case each write their stubs first, then install a recorder beneath the guard: a `monkeypatch.setattr` of `subprocess.Popen.__init__` with a wrapper that counts its calls and then calls the original. The guard installs after the recorder, so the guard calls the recorder. Warm execs at write time are not counted, because the stubs are written first.
    - Each stub's body creates a sentinel file at an absolute path with a shell redirection, since PATH holds no `touch`.
    - Route cases, one parametrized test over these launches of the cold stub by bare name:
      - `subprocess.run`;
      - `subprocess.call`;
      - `subprocess.check_call`;
      - `subprocess.check_output`;
      - `subprocess.Popen`;
      - `subprocess.run` of a command string with `shell=True`;
      - `asyncio.create_subprocess_exec` under `asyncio.run`;
      - `subprocess.run` given `cwd=` the marker directory's parent.

      Each launch's PATH has one entry, the marker directory. It names that directory by its absolute path, except in the `cwd=` case, which names it by its bare name. Each case raises `UnwarmedStubError`, and the recorder counts 0 calls. The sentinel's absence is a second check.
    - Pass-through case: with warm stubs only, `subprocess.run` of a warm stub under that PATH returns 0, the recorder counts 1 call, and the warm stub's sentinel exists. The sentinel shows that a stub body can write it under this PATH.
    - With the guard installed and the ambient PATH set to a marker directory holding a cold stub, `write_prewarmed_stub` of a second stub in that directory returns, because its warm exec runs under `_PREWARM_EXEC_PATH` (row 21).
    - A second in-place `__init__` wrapper installed after the guard, the way `_routing_guard_helpers.py:133-138` installs one, still raises for the cold stub.
    - Inside `pytest.MonkeyPatch.context()`, the guard and then a second in-place wrapper are installed. After the context exits, `subprocess.Popen.__init__` is the original object again.
- A1 runs Verification 1 and returns the cost figures.

**Dispatch A2** (after commit 1). Its five files stay uncommitted until commit 2 (Approach, Order steps 3 and 6).
- `claude/.claude/hooks/tests/conftest.py` and `claude/.claude/scripts/tests/conftest.py`: M3.
- Both wired files hold the hand-built test. Its launch is `subprocess.run([sys.executable, "-c", ""], env=<the case's env>, capture_output=True, check=False)`, and the real-writer test uses the same launch.
  - It hand-builds a marker directory in `tmp_path / "bin"` and puts a cold `#!/bin/sh` stub in `cold_dir = tmp_path_factory.mktemp("cold-bin")`, a basetemp entry outside `tmp_path`.
  - Under `env={"PATH": f"{cold_dir}{os.pathsep}{tmp_path / 'bin'}"}`, the launch raises `UnwarmedStubError` naming the cold stub.
  - It needs no `timeout` binary, so it proves its tree's wiring on every host. A conftest that passes `tmp_path` as `basetemp` fails it.
- `claude/.claude/hooks/tests/test_shell_exec_guard_wired.py`: the hand-built test, plus a real-writer test that requests `git_timeout_shim` and `tmp_path_factory`.
  - `env = git_timeout_shim("false")` writes the real shim, its marker and a warm `git` into `tmp_path` (row 40).
  - Its cold stub goes into a `cold_dir` made as in the hand-built test, and the launch passes `env={"PATH": f"{cold_dir}{os.pathsep}{env['PATH']}"}`. It raises `UnwarmedStubError` naming the cold stub.
  - It skips with the fixture when `git` or a `timeout` binary is absent (row 32), and CI runs it.
- `claude/.claude/scripts/tests/test_shell_exec_guard_wired.py`: the hand-built test only.
- Both wired files' module docstrings name both installs.
- `claude/.claude/tests/test_scaled_shim_callers_are_guarded.py` (new): M7, with the module docstring "Structural test: every file that can install the scaled timeout shim runs under helpers.install_unwarmed_stub_guard."
  - Positive control: the two anchors in Approach, M7.
  - Seeded controls, each on files under `tmp_path`, with `tmp_path / "root"` as the given root:
    - One case per reference shape, each asserting that the seeded file lands in the referencing set: a fixture request as a test function's argument; a bare-name call; an attribute call, `helpers.write_scaled_timeout_shim(tmp_path)`; an import alias with no call; and `@pytest.mark.usefixtures("git_timeout_shim")`.
    - A docstring-only mention is not a reference.
    - A seeded `.py` with a syntax error, and a listed path with no file behind it, are each skipped, and the scan raises nothing.
    - A referencing file is uncovered with no conftest, and covered once a `conftest.py` beside it calls the install.
    - A `conftest.py` that calls the install in an ancestor directory within the root covers a nested file.
    - A calling `conftest.py` in a sibling directory, in a descendant directory, or in `tmp_path` above the root leaves the file uncovered.
    - A `conftest.py` that imports `install_unwarmed_stub_guard` but never calls it leaves the file uncovered.
- A2 runs Verification 2 and returns the spike's table. It fixes nothing the spike flags.

**Stops before B** (the session, on A2's return): Approach, Order step 4.

**Dispatch B** (after the stops) applies M5 to every write site the spike names, on top of A2's uncommitted files (Approach, Order step 5).
- B's file limit is the 39 route files plus eight shared-writer files (rows 11, 31). B edits only the files that hold a flagged write site. A site in any other file needs the engineer's answer first (Order steps 4 and 5).
  - Shared writers: `claude/.claude/tests/helpers.py`, `claude/.claude/hooks/tests/conftest.py`, `claude/.claude/scripts/tests/conftest.py`, `claude/.claude/hooks/tests/_lib_sh_helpers.py`, `claude/.claude/hooks/tests/test_git_floor.py`, `claude/.claude/hooks/tests/test_python_floor.py`, `claude/.claude/scripts/tests/_branch_records_helpers.py` and `claude/.claude/scripts/tests/_review_pr_stack_helpers.py`.
  - Route files under `claude/.claude/hooks/tests/`: `test_advance_past_commit_stall.py`, `test_announce_resume_command.py`, `test_check_claude_md_length.py`, `test_check_python_floor.py`, `test_check_skill_length.py`, `test_consume_durable_continuity_file_on_read.py`, `test_deny_default_branch_push.py`, `test_deny_escaped_backticks_in_pr_body.py`, `test_deny_pii_in_commits.py`, `test_deny_private_project_refs.py`, `test_hook_alignment.py`, `test_lib.py`, `test_lib_capped_for.py`, `test_lib_command_words.py`, `test_lib_credentials.py`, `test_lib_git_refs.py`, `test_lib_markers.py`, `test_lib_realpath.py`, `test_lib_resume_context.py`, `test_marker_lib.py`, `test_marker_script.py`, `test_nudge_error_mode_analysis.py`, `test_nudge_handoff_near_context_cap.py`, `test_nudge_long_turn_subagent.py`, `test_nudge_memory_store_audit.py`, `test_record_session_end.py`, `test_require_architect_consult.py`, `test_require_plan_review.py`, `test_require_ready_for_review.py`, `test_require_skill_review.py`, `test_require_stow_reminder.py`, `test_scaled_timeout_shim.py`.
  - Route files under `claude/.claude/scripts/tests/`: `test_branch_divergence_status.py`, `test_branch_records_archive.py`, `test_branch_records_lib.py`, `test_ci_watch.py`, `test_cleanup_merged_branches.py`, `test_reconcile_project_plugins.py`, `test_review_pr_lib.py`.
- B runs Verification 4. For each flagged path, it returns the site it migrated or moved, the reason the path is unsettled, or its write site outside the file limit, unedited.
- B is one dispatch when the spike's sites lie in 10 files or fewer. Otherwise B1 takes the shared writers and the hooks files, and then B2 takes the scripts files.

**Why this split.**
- A1 and A2 are separate dispatches because commit 1 lands between them, and a `code-writer` cannot commit (row 47). Commit 1 has to come from a tree that holds only A1's files, because B may edit `helpers.py` too.
- A2 and B are separate because B's file list is the spike's output, and Order step 4's stops need the engineer.
- A2's five files share one design (M3, M7), which a further split would restate.
- B1 and B2 run one at a time rather than in parallel, for two reasons. B2's tests can reach a shared writer B1 changes. And each runs `-m timing -n0`, which a concurrent run's load would disturb.

**Reuse:**
- `_SCALED_TIMEOUT_MARKER_DIRNAME`, `_PREWARM_SHEBANGS` and `_PREWARM_GUARD_LINE` (`helpers.py:3076-3086`), and `_SHEBANG_READ_BYTE_LIMIT` (:381). The check reads them rather than restating any.
- The `os.fsdecode` handling of env keys in `_env_passed_without_path` (:490-493).
- `_shell_exec_target`'s resolution against `cwd` (:435-437) and `_has_env_bash_shebang`'s binary read (:441-447).
- `install_shell_exec_guard`'s wrapper shape (:566-589), without its `shell=True` skip.
- `write_prewarmed_stub` (:3100), which is both the fix the error names and B's edit.
- `git_timeout_shim` (`hooks/tests/conftest.py:265`), for the hooks tree's wired test.
- The tracked-file listing and AST walk of `test_pytest_collection_config.py:453-491`, for M7.

**Not touched:**
- `plugins/lovable-cloud/tests/conftest.py` and its wired test, and the module-level and in-test guards in `claude/.claude/tests/` (row 12).
- `install_shell_exec_guard`, `check_shell_exec`, `write_prewarmed_stub` and `write_scaled_timeout_shim`.
- `claude/.claude/scripts/tests/_routing_guard_helpers.py` and `claude/.claude/scripts/tests/test_cursor_adapter_shim.py`. Verification 2 runs them, but nothing edits them.
- `claude/.claude/scripts/select-tests.py` and `claude/.claude/scripts/tests/test_select_tests.py` (Out of scope).

Projected diff: about 580 changed lines before any migration, across A1's 2 files and A2's 5. Commit 1 holds about 380 of them: about 90 in `helpers.py` and 290 in the contract-test file. A2 adds about 200: 8 across the two conftests, 62 across the two wired-test files and 130 in the structural test. Migration adds about 4 lines per flagged write site and 1 import line per file, counted by the spike (Verification 2). These figures are estimates. Commit 1 sits about 20 lines under the gate's 400, so the gate may ask at Order step 2 rather than step 6. The engineer chose `Proceed anyway` at about 430 (row 45) and at about 580 (row 60). Neither answer covered the measured count or the migration's size, which Order step 6's question gives.

## Verification

Run from the worktree root.

1. **Commit 1's checks.**
   - A1 runs `scripts/venv-run.sh pytest claude/.claude/tests/test_unwarmed_stub_guard.py claude/.claude/tests/test_shell_exec_guard.py claude/.claude/tests/test_prewarmed_stub.py claude/.claude/scripts/tests/test_select_tests.py::TestCrossDomainReadCompleteness -m "not timing"`, then the same targets with `-m timing -n0`. `TestCrossDomainReadCompleteness` globs the filesystem, so it reads the new file unstaged (row 54).
   - A1 then runs `scripts/venv-run.sh ruff check` on `helpers.py` and the new contract-test file.
   - A `-m timing -n0` run whose files hold no timing test exits 5, which is not a failure (row 42).
   - Cost: A1 times the check with `timeit` from a scratch script it deletes afterwards, 5,000 calls each. One PATH has 30 entries and no marker. The other has 30 entries, one of them a basetemp entry holding the marker and 30 warm stubs. A1 returns both per-call figures for the PR body, beside row 20. Linux is not measured, because no step here runs on Linux and CI's timing tests assert second-scale bounds.
   - A1 fixes a failure in its own new tests, or one `TestCrossDomainReadCompleteness` reports against its new file. It returns any other failing node ID with its output, unfixed.
   - After staging A1's two files and before `/code-review`, the session runs `claude/.claude/hooks/tests/test_ticket_reference_discipline.py` and `claude/.claude/tests/test_pytest_collection_config.py` in both passes. Both list files with `git ls-files`, so they read the new file only once it is staged (row 54).
2. **A2's checks and the spike**, with A2's files applied.
   - A2 runs both passes over the two wired-test files, `claude/.claude/tests/test_scaled_shim_callers_are_guarded.py`, `claude/.claude/scripts/tests/test_transcript_routing_guard_compute.py`, `claude/.claude/scripts/tests/test_transcript_routing_guard_capture.py` and `claude/.claude/scripts/tests/test_cursor_adapter_shim.py`. Then it runs `ruff check` on its five files.
     - The last three test files exercise the other in-place `__init__` patch and the `Popen` stand-in under the wired guard (rows 5, 6).
   - Proof that the guard fires, before the batches: every wired test that runs passes, and both hand-built tests run on every host. Without it, a guard that never fires on a real route test would read as zero flags.
     - When a wired test fails, A2 returns before the batches.
     - When the hooks real-writer test skips, A2 still runs the batches and marks coverage a lower bound. When the skip comes from a missing `timeout` binary, the count is zero by construction (rows 1, 32; Order step 3).
   - Precondition for the batches: Order step 3's tool check passed, or the engineer chose `Lower bound`.
   - Run the 39 route files with `scripts/venv-run.sh pytest <batch> -m "not timing"`, then with `-m timing -n0`. Use batches small enough to finish in one tool call. Batching changes nothing the guard sees, since it checks each launch on its own. A timing batch that exits 5 is a no-op (row 42).
   - Return a table with one row per test that fails with `UnwarmedStubError`: its node ID and every path the error names.
     - Group the paths by write site, the `file:line` of the code that writes that name into that directory.
     - Mark each group migrate, move (with the reason), or unsettled (with what could not be settled).
     - Give each write site's count of flagged node IDs, which is how many warm execs per suite run its migration adds. For a fixture or shared writer that count is a lower bound, since an unflagged test that calls it pays the warm exec too.
     - Give totals for flagged tests, flagged paths, write sites and files.
   - Return the skipped count among the 39 files, with pytest's skip reasons, and whether the hooks real-writer test ran. Mark coverage full or a lower bound by Order step 3's rule.
   - Return any other failing node ID with its output, unfixed. The session reruns each one once with `-n0` in a merge-base worktree (`CLAUDE.md` "Prove your change caused a failing check"). It blocks only on one that passes there and fails again on the branch.
3. **Stops** (the session): Approach, Order step 4, on A2's return.
4. **B's checks**, with A2's files applied.
   - Re-run, in both passes as in 2, every file that holds a flagged test.
   - Also re-run, in both passes, every consumer of a shared writer B changed:
     - for a writer in a tree's `conftest.py`, that conftest's whole test tree, because a fixture reaches every test that requests it;
     - for a writer in `helpers.py` or a sibling module, every file that imports it (row 31).
   - Pass criterion for a single B, and for B2: no `UnwarmedStubError` remains. For B1: every remaining `UnwarmedStubError` names only paths whose write site the spike assigned to B2.
   - Then run `scripts/venv-run.sh ruff check <B's files>`.
   - B returns any other failure unfixed, and the session judges it as in 2.
5. **Full suite, in CI's two passes**, run by the session after staging commit 2's files and before `/code-review`. Staging comes first because two scanners read only the index (row 54).
   - Run `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py -m "not timing"`, then `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py -m timing -n0`. Run each pass in the background and read its completed summary, since a full-suite pass can outlast one foreground tool call.
   - With `helpers.py` in the branch's diff, both select the full suite through its global trigger (row 24, G3). On a Split-now second PR without it, they domain-select. The session then confirms the selection names the hooks, scripts and `claude/.claude/tests/` trees, which hold every test the guard can fail (row 51).
   - No `UnwarmedStubError` may appear. Any other failure is judged as in 2.
   - Then run `scripts/venv-run.sh ruff check claude/.claude/`. No tracked shell file changes, so ShellCheck has nothing new to check.
6. **CI** passes both pytest steps on Linux.
   - The session reads both legs' `-ra` skip summaries. It records in the PR body that no test skipped for a missing tool in any of these:
     - the 39 route files;
     - the real-writer case in `test_unwarmed_stub_guard.py`;
     - the hooks real-writer wired test.

     The last two tie the real writer to the trigger. CI's `gh` and `timeout` come from the runner image (row 44), so a skip in any of the three would shrink the guard's reach silently.
   - It also records both legs' wall times beside those of main's latest push run, against the 30-minute `timeout-minutes` (row 44).
   - A flag CI shows first gets one more migration commit under M5, on top of commit 2, not an exemption. The session runs Verification 5 and `/code-review` before that commit. The commit leaves commit 2 failing at its own SHA, which squash-only merging keeps off `main` (row 35).
7. `git diff --name-only origin/main...HEAD` lists only Critical files paths and this plan.
8. **Before the merge hand-off**, the session syncs the branch with `origin/main` and re-runs Verification 5 on the synced head. A cold stub merged by another PR after this PR's last CI run would otherwise fail `main`'s push run. Another merge to `main` before this PR merges calls for another sync and run.

## Out of scope

- **Known false negatives, accepted.** The guard misses each of these, and each would surface only as the flake itself, diagnosed by hand:
  - a stub exec'd by absolute path from a directory not on the launch's PATH;
  - a stub in a PATH entry that lies outside basetemp and holds no marker;
  - a python-shebang stub, or an sh-family line 1 outside `_PREWARM_SHEBANGS`, such as one with trailing text or a CRLF ending;
  - a copied file that already carries the guard line;
  - a launch through `os.system`, `os.posix_spawn`, `os.exec*`, `pty` or `multiprocessing`;
  - a `Popen` given `env` or `cwd` positionally;
  - a stub written after the launch of a long-running child that later execs it;
  - a PATH entry a hook adds during its own run;
  - a scaled-shim test outside the wired trees in a file that calls `install_unwarmed_stub_guard` only in some other test, which the structural test counts as covered;
  - a new tree whose `conftest.py` calls `install_unwarmed_stub_guard` from a fixture that is not autouse, which the structural test also counts as covered. The two wired trees are pinned by their wired tests, and a new tree is not;
  - a flagged launch inside a bare `pytest.raises(AssertionError)` or an `except AssertionError`, which absorbs `UnwarmedStubError`. M5's shared-launch bullet covers only the seven blocks in `test_scaled_timeout_shim.py` (row 43), so the next author who wraps a launch that way, with no unwrapped launch beside it, silences the guard. The plan keeps the `AssertionError` base to match `ShellExecGuardError` (`helpers.py:392`), and accepts this residual.
- **`plugins/lovable-cloud/tests/` and the guards in `claude/.claude/tests/` stay unwired.** No test there can install the scaled shim (rows 11, 12), and wiring the plugin tree needs a plugin version bump (row 17). The structural test (M7) fails when a file there first can, and its message names the fix for a tree with or without a conftest.
- **Local selection of the structural test for a `.py` change outside the trees that already select it.** `select-tests.py` selects M7 for a `.py` change under the hooks, scripts or `claude/.claude/tests/` trees (row 51). It also selects M7 for any `conftest.py` or `__init__.py` under `claude/`, `claude-skills/` or `plugins/` (row 51). It does not select M7 for another `.py` under `plugins/`, `claude-skills/` or `evals/`, so CI's full run is M7's only trigger there. A selection row would add M7 to the exact target sets that existing `test_select_tests.py` cases assert (row 52), for trees that hold no scaled test today (row 11).
- **An exemption mechanism.** None is added up front. A flagged file that can neither migrate nor move stops for the engineer, before B or on B's return (Approach, Order steps 4 and 5).
- **A CI step that fails when the runner image lacks `gh` or `timeout`**, the way `require_direnv` fails without `direnv` (`tests.yml:128`). Verification 6 reads this PR's skip summaries instead. A standing check changes the workflow every PR runs, which is a decision of its own.
- **Changes to `write_prewarmed_stub`, `write_scaled_timeout_shim`, `TIMEOUT_SCALE_DIVISOR` or any production cap.**
- **New per-writer spy cases.** The guard checks every writer's output at launch, which covers the per-writer spy-case obligation that review-ledger rows `37fc0c4d72f2` and `e7fac74f95fd` deferred. Updating those rows is a ledger action outside this diff.
- **One combined entry point for `install_shell_exec_guard` and the new install** (Alternatives).
