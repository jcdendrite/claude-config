# Fix the embedded-newline leak in announce-resume-command.sh

## Context

Make `announce-resume-command.sh`'s allowlist gate actually reject a continuity-file path containing an embedded newline, closing a security-relevant gap. The gate relies on GNU grep's `-z` anchoring semantics, which BSD grep (macOS's default `/usr/bin/grep`) does not provide. On macOS the gate therefore fails open silently. A test already in this repo pins the expected rejecting behavior.

This was discovered incidentally while investigating unrelated macOS test
flakiness on PR #1082, and confirmed to reproduce identically on `main` at
`ab518788` — a pre-existing defect, not something introduced by any in-flight
branch. It's a correctness bug in a security-relevant control: a
`PostToolUse` hook whose job includes resisting adversarial file-path
content, tested and pinned, but not actually enforcing what the test expects
on this platform.

The fix should make `test_file_path_with_embedded_newline_emits_nothing`
(and the rest of `test_announce_resume_command.py`) pass, without
introducing a platform-specific dependency (GNU grep) that isn't guaranteed
present on a contributor's machine.

The allowlist helper lives in the shared `claude/.claude/hooks/_lib.sh`, and a direct unit-test file covers its byte-class matrix by sourcing `_lib.sh`. The hook's subprocess tests keep one case per branch. A hook-local helper forces that matrix onto full-subprocess hook tests. On that point the engineer said: "Wait, you're adding new tests that have tech debt? That's unacceptable. If the WORKTREE_ROOT stub tests should have a unit tested helper, that needs to be fixed."

Separately, the engineer asked to fix `docs/hooks.md`'s stale allowlist-mechanism description in this same PR ("update the docs/hooks.md please - that's opportunistic tech debt fixing"), rather than deferring it as a follow-up.

Disclosure posture: the engineer selected "Direct fix PR, no exploit detail
(Recommended)" and said, "let's leave off the github private vulnerability
reporting" — ship as a normal PR, with no GitHub private vulnerability
report and no public issue describing the leak mechanism. Commit and PR
language should describe the fix mechanically (e.g., running the allowlist
checks in bash so they behave identically on BSD and GNU platforms) rather
than narrating the injection/exploit shape — that mechanical framing is this
plan's own proposal, not the engineer's wording.

## Approach

Replace both `grep -Eqz` allowlist checks with one shared bash helper in `claude/.claude/hooks/_lib.sh`, `_lib_passes_path_char_allowlist`. It rejects an empty value and any value containing a byte outside `[A-Za-z0-9._/@+-]`, using a `case` arm of the form `'' | *[!A-Za-z0-9._/@+-]*)`. Its body runs in a subshell that assigns `LC_ALL=C`, so the bracket ranges are byte ranges whatever the caller's locale. A direct unit-test file sweeps the helper's byte class, and the hook's subprocess tests keep one case per branch.

The work lands in three phases on this branch:
- **Phase 1, landed in `a167194f`:** a hook-local `_passes_allowlist` helper under a script-wide `LC_ALL=C` replaced both `grep -Eqz` checks. A new CANDIDATE_ROOT-arm embedded-newline test was sequenced ahead of the hook edit, red on macOS first, then green.
- **Phase 2:** the helper moves into `_lib.sh` as `_lib_passes_path_char_allowlist` and sets its own locale. The hook's script-wide `LC_ALL=C` goes. A new unit-test file takes the input matrix, and the hook test file keeps one subprocess case per branch.
- **Phase 3:** `docs/hooks.md`'s `announce-resume-command.sh` entry is corrected to match the hook's own header (case-glob mechanism, not regex; a failing worktree root drops only `--cwd`, not the whole announcement).

Why this form:
- **Newline handling.** A newline is simply a byte outside the set, so the check needs no line-anchoring semantics at all. It runs in bash's own pattern matcher rather than an external tool whose behavior differs by platform.
- **Repo precedent.** 26 `case` arms across 5 files under `claude/` already use the `''|*[!<set>]*)` shape as their pure-bash character-set check. Examples: `nudge-handoff-near-context-cap.sh:635` `""|*[!A-Za-z0-9_-]*) continue ;;` and `_lib.sh:210`.
- **Scope of the defect.** The only `grep -z` uses in the repo are the two lines being replaced. No `--null-data` or `grep -…z` appears in any non-`.sh` file either, so no other site shares this dependency.
- **Placement in `_lib.sh` implements rules that already apply; it overrides no convention.**
  - `claude/.claude/rules/shell-script-conventions.md` (stowed; loads for `**/*.sh`), final bullet: "Extract pure bash into a sourceable `_<topic>-lib.sh`, then unit-test it by sourcing that file directly… Keep the subprocess test to one case per branch; move the input matrix to the direct tests instead." (row 14)
  - `.claude/rules/bash-unit-test-seams.md` gives a placement order, not a gate on extracting, and "stay in the hook" is not one of its options. For a helper only one hook uses, option 3 (a new sidecar) applies. The same file blocks a sidecar under `hooks/` and says: "Land a hook-side extraction inside the existing `_lib.sh` instead, until that guard is widened." (row 15)
  - The engineer's instruction applies if a unit-tested helper is warranted (row 19), and rows 14-15 show it is (row 20).
- **The helper is generic, not hook-specific.** `set-session-title-from-branch.sh:153` already hard-codes the same byte set. The announce hook's own header cites that file as its source, so two hooks already share this knowledge (row 18).
- **The helper sets its own locale.** In a shared lib, relying on the caller's `LC_ALL=C` becomes a hidden requirement on every caller. A second caller that forgot it would fail open on macOS only, and Linux CI (bash 5) would never notice. Why the subshell body:
  - It keeps the mechanism already tested: assigning `LC_ALL=C` makes bash switch locale, so the ranges are byte ranges (row 6).
  - It costs one fork per call, two calls per hook fire, next to three `jq` runs and up to four `timeout`-wrapped `git` runs.
  - `${1-}` keeps the function safe under `set -u` for future callers.
  - `docs/hooks.md:155` ("under `LC_ALL=C`") stays accurate, so it needs no edit on that point (Phase 3 still corrects the other two stale claims on that entry).
- **The hook's script-wide `LC_ALL=C` goes.** Its only reader was the matcher, so keeping it would be a redundant second pin (row 25). The side effect on `jq`/`git` children is row 26.
- **Name: `_lib_` prefix**, matching `_lib_jq`, `_lib_capped` and `_lib_realpath_m`. A bare `_passes_allowlist` is ambiguous in the shared namespace, because "allowlist" already means other things in hooks (`OSS_ALLOWLIST`, the `deny-env-reads.sh` basename allowlist). No `_lib_passes*` exists today.
- **CI safety.** The alignment test's hook-shape assertions exclude `_lib.sh` (row 16). The liveness guard is satisfied by the hook's two call sites (row 17).
- **`_lib.sh` growth is negligible** (row 32).

Alternatives set aside:
- **Extglob `[[ "$v" == $ALLOWLIST_PATTERN ]]` with `ALLOWLIST_PATTERN='+([A-Za-z0-9._/@+-])'`.** The dispatching session verified this end to end (24/24). It needs `shopt -s extglob`, a parser change for the rest of the script with 0 uses anywhere in the repo, plus two `# shellcheck disable=SC2053` lines for the variable-held pattern. The negated form gets the same result with neither. It stays the fallback if the chosen form fails Verification step 2.
- **`[[ "$v" =~ $ALLOWLIST_RE ]]`.** It keeps the existing constant and needs no suppression. But bash hands `=~` to the platform C library's `regcomp`/`regexec`, so whether `^`/`$` anchor at an embedded newline again depends on each platform's regex implementation. That is the same kind of dependency that caused this bug.
- **Requiring GNU grep (`ggrep`, Homebrew coreutils).** Excluded by the Context: no platform-specific dependency.
- **Keeping grep and adding a separate `*$'\n'*` rejection.** This adds a second layer to compensate for the first layer's platform divergence, and leaves grep's per-platform semantics in place. That signals the foundation is wrong, so fix the foundation instead.
- **Keeping the helper hook-local.** The byte-class matrix then has to ride on full-subprocess hook tests, which `shell-script-conventions.md` bars (row 14). `bash-unit-test-seams.md` offers no stay-in-the-hook option (row 15).
- **Widening `_HELPER_LIBRARY_NAMES` and adding a `_<topic>-lib.sh` sidecar.** It touches the alignment test's invariants for a six-line function. The `_all_hook_files` docstring at `test_hook_alignment.py:94-97` describes a one-`_lib.sh`-per-directory count invariant. The rule already names `_lib.sh` as the interim home.
- **Regex-extracting the function from the hook at test time.** `shell-script-conventions.md` forbids it.
- **Sourcing the hook itself.** Impossible, because it reads stdin and exits at top level.
- **`local LC_ALL=C`.** Whether bash 3.2 restores the locale when the function returns is unknown (row 23). `_lib_sanitize_for_terminal` (`_lib.sh:3529-3531`) already uses this form, but its header states no caller-locale guarantee, while this helper's unit file pins one.
- **A spelled-out 68-byte class with no ranges.** It avoids the fork. But invalid-UTF-8 bytes under a UTF-8 locale would then go through bash's multibyte matcher and its fallback path, and nobody has verified that path never skips a byte (row 24).

**Assumption ledger**

- **Root:** Both allowlist checks in `announce-resume-command.sh` (:84, :93) depend on GNU grep's `-z` anchoring `^`/`$` to the whole value. Under BSD grep, a value with an embedded newline passes whenever any one of its lines is allowlist-clean, so on macOS the gate does not enforce its documented contract.
- **Givens:**
  - macOS resolves `grep` to BSD grep. Apple ships it, and the hook cannot choose a contributor's `grep`. `[verified: session's grep --version → "BSD grep (GNU compatible) 2.6.0-FreeBSD"]`
  - Hooks run under their `#!/bin/bash` shebang, so under whatever `/bin/bash` the platform ships. Stock macOS ships bash 3.2, so the fix must not rely on bash ≥4.1 behavior such as `[[ == ]]` matching as if extglob were on. The platform vendor owns that binary. `[unverified — 3.2 not re-checked this session; Verification step 2 runs under the shebang regardless]`
  - `tool_input.file_path` is whatever path the acting agent chose, and POSIX filenames may contain `\n`. The harness owns the payload, and the hook can only filter it.
- **Rows:**
  1. BSD grep's `-z` still anchors `^`/`$` at each embedded newline. `[verified: session run of printf 'abc\ndef' | LC_ALL=C grep -Eo -z '^[A-Za-z]+$' on /usr/bin/grep → two separate matches]`
  2. The existing suite reproduces the defect: at `ab518788` on macOS, the file gives 23 passed and 1 failed, and the failure is `test_file_path_with_embedded_newline_emits_nothing`. `[verified: session pytest run]`
  3. CI stays green because `.github/workflows/tests.yml:25` runs `ubuntu-24.04`, whose GNU grep anchors `-z` to the whole record. `[verified: runs-on line]` The GNU `-z` semantics on that runner are `[unverified — not run on Linux this session]`. This part is not load-bearing, because the fix removes grep from both checks.
  4. Bash evaluates `case` patterns with its own matcher, not libc regex or `fnmatch`, so the helper behaves the same wherever the same bash runs. `[unverified — from bash's implementation, not checked this session; Verification steps 2 and 5 close it on both platforms]`
  5. In a bash bracket expression, a newline is an ordinary byte outside the set, so `*[!A-Za-z0-9._/@+-]*` matches any value that contains one. `[unverified for this exact negated form; the positive form +([A-Za-z0-9._/@+-]), same set and same matcher, rejected the test fixture this session; Verification step 2 closes it]`
  6. Assigning `LC_ALL=C`, exported or not, switches the locale bash itself uses for bracket ranges, whether the assignment sits at script level or inside the helper's subshell body. Bash re-runs `setlocale` on assignment to `LC_ALL`. The pin is needed because outside C, bracket ranges can follow collation order and admit non-ASCII bytes, which is the hazard documented at `set-session-title-from-branch.sh:18-23`. `[unverified for bash's own matcher; the hazard itself is verified: set-session-title-from-branch.sh:18-23; Phase 2 Verification step 2 closes it on macOS]`
  7. Before this branch, no test exercised the locale pin: every allowlist fixture in the test file was ASCII, space, or newline. `[verified: test_announce_resume_command.py read in full at plan time]` Phase 1 added `test_file_path_with_non_allowlisted_byte_emits_nothing_under_utf8_locale` (:331-353). Its docstring says the non-ASCII case guards the `LC_ALL=C` line only under bash < 5. `[verified: test file :339-344 read this pass]` That the case actually goes red without the pin is `[unverified — Phase 2 Verification step 2 closes it for the helper's own pin]`.
  8. The CANDIDATE_ROOT arm's call site never validates that `CANDIDATE_ROOT` is a real directory. It consumes whatever `git -C <cwd> rev-parse --show-toplevel` prints, verbatim, so a PATH-stubbed `git` drives that arm deterministically. Whether a platform's `git worktree add` accepts a newline-containing path doesn't matter to it. `[verified: announce-resume-command.sh:103-104 read this pass]`
  9. A real `Edit`/`Write`/`MultiEdit` payload can carry a `file_path` with an embedded newline on a macOS session. `[unverified — the dispatching session's own analysis; the harness source is outside this repo]`
  10. `set-session-title-from-branch.sh`'s identical `ALLOWLIST_RE` check (plain `grep -Eq` at :153-155) never sees an embedded newline. `REPO_COMPONENT` is the basename of a single `awk` output line (:137, :149). `BRANCH_COMPONENT` is either a `symbolic-ref --short` name or `@<short-sha>` (:112-122). `[verified: file read]` That git ref names exclude control characters, per `git-check-ref-format`, is `[unverified — not re-read this session]`.
  11. `consume-durable-continuity-file-on-read.sh` shares the continuity-path glob (:119-122) but has no allowlist gate. It passes `FILE_PATH` to `resume-context.sh --consume-only` and emits no path text into model-visible output. `[verified: dispatching session's read of that file]`
  12. Ship as a normal PR with no exploit detail. `[engineer-verified: "Direct fix PR, no exploit detail (Recommended)"]` `[engineer-verified: "let's leave off the github private vulnerability reporting"]`
  13. The option description behind row 12's label, "keep the commit message and PR body focused on 'hardens the allowlist gate for cross-platform grep portability'", is the dispatching session's own proposal, not the engineer's words. `[unverified]`
  14. `claude/.claude/rules/shell-script-conventions.md`'s final bullet requires extracting pure bash into a sourceable lib and unit-testing it by sourcing that file directly. It keeps the subprocess test to one case per branch and moves the input matrix to the direct tests. `[verified: claude/.claude/rules/shell-script-conventions.md read this pass]`
  15. `.claude/rules/bash-unit-test-seams.md` lists placement in preference order, with no stay-in-the-hook option. It blocks a new `_*-lib.sh` sidecar under `claude/.claude/hooks/` and routes a hook-side extraction into the existing `_lib.sh`. `[verified: .claude/rules/bash-unit-test-seams.md read this pass]`
  16. `test_hook_alignment.py:85` defines `_HELPER_LIBRARY_NAMES = frozenset({"_lib.sh", "_config.sh"})`, used as the default exclusion at :102, so the hook-shape assertions skip `_lib.sh`. `include_lib=True` (`ALL_HOOKS_AND_LIBS`, :153) adds `_lib.sh` back only for the `\s` check (:92), and the new function contains no `\s`. `[verified: test_hook_alignment.py:85, :89-92, :102, :153 read this pass]`
  17. `claude/.claude/hooks/tests/test_shell_lib_function_liveness.py` requires every column-0 function in `_lib.sh` to be named outside its own definition line, in non-comment tracked text. Its definition regex `^([a-z_][a-z0-9_]*)\(\)` matches both the `name() {` and `name() (` forms. The hook's two call sites satisfy it. `[verified: dispatching session's read of test_shell_lib_function_liveness.py; not reopened in this revision]`
  18. `set-session-title-from-branch.sh:153` hard-codes the same byte set (`ALLOWLIST_RE='^[A-Za-z0-9._/@+-]+$'`). The announce hook's header cites that file as its source (:32-34). `[verified: hook header read this pass; set-session-title-from-branch.sh:153 per the dispatching session's read]`
  19. New tests on this branch must not carry tech debt. If the WORKTREE_ROOT stub tests should have a unit-tested helper, that gets fixed. `[engineer-verified: "Wait, you're adding new tests that have tech debt? That's unacceptable. If the WORKTREE_ROOT stub tests should have a unit tested helper, that needs to be fixed."]`
  20. Row 19's condition holds: rows 14-15 already require a unit-tested helper in `_lib.sh`. So Phase 2 implements a rule that already applies, per the engineer's confirmation. It is not a convention override. `[verified: rows 14-15]`
  21. With the helper hook-local, the WORKTREE_ROOT-arm stub tests are the slowest in the file, at 10-16s each. `[unverified — timing from this session's review run, not re-measured; Phase 2 Verification step 8 measures it]`
  22. In a function whose body is a `( ... )` subshell, assigning `LC_ALL=C` switches bash's matcher locale for that subshell only, and the caller's locale stays as it was. `[unverified — Phase 2 Verification step 1's caller-locale case closes it]`
  23. Whether bash 3.2 restores the locale when a function holding `local LC_ALL=C` returns is `[unverified]`. `_lib_sanitize_for_terminal` already uses `local LC_ALL=C`, and its header states no caller-locale guarantee. `[verified: _lib.sh:3516-3541 read this pass]`
  24. Under a UTF-8 locale, whether bash's multibyte matcher can skip a byte when it falls back on an invalid-UTF-8 sequence is `[unverified]`.
  25. The hook's script-wide `LC_ALL=C` (:85) has no reader besides the helper's `case`. The continuity-path glob (:79-82) runs before it, and nothing after it uses a bracket range. It is unexported, so child processes see it only if the caller had already exported `LC_ALL`. `[verified: announce-resume-command.sh read in full this pass]`
  26. Removing the script-wide `LC_ALL=C` affects the `jq`/`git` children only when the caller had exported `LC_ALL`: they then see the caller's value instead of C. That makes no output difference, because `rev-parse` path output and `jq --arg` are not locale-formatted. `[unverified]`
  27. If `_lib_passes_path_char_allowlist` were missing, both call sites fail closed. `… "$FILE_PATH" || exit 0` exits with no output on status 127, and `if …; then` leaves `WORKTREE_ROOT` empty, so `--cwd` is dropped. A `_lib.sh` that fails to source already exits 0 at :63-65. `[verified: announce-resume-command.sh:63-65, :95, :104 read this pass]`
  28. The existing lib test harnesses resolve a bare `bash` from PATH: `test_lib.py:143`'s `_run_lib_call` and `claude/.claude/scripts/tests/test_worktree_lib.py`'s `_run_bash`. `[verified: dispatching session's read of both harnesses]` On macOS that is often Homebrew bash 5, which hides a 3.2-only failure. `[unverified]`
  29. `en_US.UTF-8` is installed on the `ubuntu-24.04` CI runner. `[unverified]` If it is absent, that locale param quietly runs as C, so only a macOS run proves the locale scoping.
  30. The two surviving stub tests are slow because macOS assesses a freshly written stub `git` binary the first time it executes (Gatekeeper/codesign). `[unverified hypothesis]` This fits the fact that `test_non_matching_path_never_invokes_git` (:544) and `test_allowlist_failing_path_never_invokes_git` (:565) write similar stubs but never execute them.
  31. `select-tests.py:374` maps the new `claude/.claude/hooks/tests/test_lib_path_char_allowlist.py` to `HOOKS_TESTS_DIR`, the same domain the hook edit already selects. `[verified: dispatching session's read of select-tests.py:374; not reopened in this revision]`
  32. Hooks source `_lib.sh` on every fire, and a six-line function adds negligible parse cost. `[unverified]` Topic-specific helpers already live there: `_lib_advance_offset_past_complete_lines` serves two nudge hooks. `[verified: _lib.sh:177-182 read this pass]`
  33. `test_lib.py` is 6,633 lines, and five per-helper `test_lib_*.py` files already sit beside it in `claude/.claude/hooks/tests/`. `[verified: line count and glob this pass]`
  34. `docs/hooks.md`'s `announce-resume-command.sh` entry (:155, :157) still describes the pre-fix regex mechanism and wrongly claims a failing worktree root suppresses the whole announcement. `[verified: staff-platform-engineer's cumulative code-review this session, and dispatching session's read of docs/hooks.md]` The engineer asked for this fixed in this PR rather than deferred. `[engineer-verified: "update the docs/hooks.md please - that's opportunistic tech debt fixing"]`
- **Mechanisms:**
  - M1: The `case` allowlist helper replaces both `grep -Eqz` checks. It is lighter than what it replaces (a bash builtin construct instead of an external process), so no heavier-primitive justification applies. `anchors: row5`
  - M2: The helper's body runs in a subshell that assigns `LC_ALL=C`, and the hook's script-wide `LC_ALL=C` is deleted. The subshell costs one fork per call, which is heavier than an in-shell assignment. Lighter primitives, each set aside: a caller-set `LC_ALL=C` is a hidden requirement on every `_lib.sh` caller that fails open on macOS only when forgotten. `local LC_ALL=C` would rest the caller-locale guarantee on unknown bash 3.2 restore behavior (row 23). A spelled-out 68-byte class would send invalid-UTF-8 bytes under a UTF-8 locale to the multibyte matcher's unverified fallback (row 24). `anchors: row22`
  - M3: The CANDIDATE_ROOT-arm newline test is written and run red before the hook edit (Phase 1). `anchors: row8`
  - M4: Commit message and PR body describe the change mechanically (bash-native allowlist checks that behave the same on BSD and GNU platforms), not as an exploit. `anchors: row12`
  - M5: The helper moves into `_lib.sh`, and a new `test_lib_path_char_allowlist.py` unit-tests it by sourcing `_lib.sh`. The hook's subprocess tests drop to one case per branch. `_lib.sh` is a wider scope than a hook-local function, and both lighter primitives fail. A hook-local helper can't be sourced for direct tests, because the hook reads stdin and exits at top level, and it violates row 14. A new sidecar lib fails CI (rows 15-16). `anchors: row14`
  - M6: The unit harness pins `/bin/bash` and passes values as bytes argv. `anchors: row28`
  - M7: `docs/hooks.md`'s stale entry is corrected in this PR rather than deferred. It is a doc-text edit, not a new mechanism. `anchors: row34`

## Critical files

**Phase 1, landed in `a167194f`.**

1. `claude/.claude/hooks/tests/test_announce_resume_command.py`: add `test_worktree_root_with_embedded_newline_falls_back_to_bare_command` directly after `test_worktree_root_with_embedded_space_falls_back_to_bare_command`.

   Drive this with a PATH-stubbed fake `git` as the only vehicle, not a real `git worktree add`. The call site under test never validates that `CANDIDATE_ROOT` is a real directory: it consumes whatever `git -C <cwd> rev-parse --show-toplevel` prints, verbatim. A stub proves the identical invariant deterministically on every platform, with no dependency on whether the local `git` binary accepts a newline-containing path (row 8).

   ```python
   def test_worktree_root_with_embedded_newline_falls_back_to_bare_command(
       self, isolated_home, tmp_path
   ):
       """The WORKTREE_ROOT gate's counterpart to
       test_file_path_with_embedded_newline_emits_nothing: a worktree root
       containing an embedded newline fails the allowlist, so only --cwd is
       dropped and none of the root's text -- including any injected
       sentinel -- reaches the output. Drives the CANDIDATE_ROOT arm with a
       PATH-stubbed git rather than a real `git worktree add`, since the
       call site never validates that CANDIDATE_ROOT is a real directory --
       the stub proves the identical invariant deterministically on every
       platform."""
       malicious_root = "linked\n\nSENTINEL-INJECT\n\nwt"
       stub_bin = tmp_path / "stub-bin"
       stub_bin.mkdir()
       fake_git = stub_bin / "git"
       fake_git.write_text(
           "#!/bin/bash\n"
           'case "$4" in\n'
           "  --git-dir) exit 0 ;;\n"
           "  --absolute-git-dir) printf '%s\\n' /fake/git-dir/A ;;\n"
           "  --path-format=absolute)\n"
           '    case "$5" in\n'
           "      --git-common-dir) printf '%s\\n' /fake/git-dir/B ;;\n"
           "      *) exit 1 ;;\n"
           "    esac\n"
           "    ;;\n"
           f"  --show-toplevel) printf '%s' {shlex.quote(malicious_root)} ;;\n"
           "  *) exit 1 ;;\n"
           "esac\n"
       )
       fake_git.chmod(0o755)
       fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
       result = _run_hook_raw(
           ANNOUNCE_HOOK,
           write_input(str(fixture), cwd=str(isolated_home)),
           home=isolated_home,
           extra_env={"PATH": f"{stub_bin}:{os.environ['PATH']}"},
       )
       assert result.returncode == 0
       payload = json.loads(result.stdout)
       assert "--cwd" not in payload["systemMessage"]
       assert f"resume-context {fixture}" in payload["systemMessage"]
       assert "--cwd" not in payload["hookSpecificOutput"]["additionalContext"]
       assert f"resume-context {fixture}" in payload["hookSpecificOutput"]["additionalContext"]
       assert "SENTINEL-INJECT" not in result.stdout
   ```

   As landed, the stub is factored into `_stub_git_linked_worktree` (:69-99), which also records a reached toplevel call and any unexpected argv in marker files.

   Argv discrimination: the four `_lib_capped`-wrapped calls all take the shape `git -C <cwd> rev-parse ...`, and `timeout` passes the same argv through unchanged. So `$4` (and `$5` for the two-flag `--git-common-dir` form) alone discriminates all four cases, with no substring or `case "$*"` matching that could accidentally collapse two branches. `PAYLOAD_CWD` is `isolated_home` (via `cwd=str(isolated_home)` in `write_input`). The stub ignores the `-C` target entirely, so it only needs to be *some* existing directory, matching the existing precedent in `test_non_matching_path_never_invokes_git`.

   Also add `test_file_path_with_embedded_carriage_return_emits_nothing`, mirroring `test_file_path_with_embedded_space_emits_nothing` exactly. It uses the same shape and the same assertions (`result.returncode == 0` and `result.stdout == ""`), with `malicious_name = "notes\rhandoff.md"` in place of the space fixture. It is a cheap adjacent-boundary-byte regression, guarding against a future typo widening `[A-Za-z0-9._/@+-]` to admit `\r`. Phase 2 deletes it, because the unit sweep covers class membership.

   **Reuse.** `_write_fixture`, `_run_hook_raw`, `write_input`, `isolated_home`. Needs a `shlex` import if not already present (it is, at :6).
2. `claude/.claude/hooks/announce-resume-command.sh`: the `ALLOWLIST_RE` constant, its `-z` comment block and both `grep -Eqz` checks are replaced by a hook-local `_passes_allowlist` `case` helper under a script-wide `LC_ALL=C` (:84-95, called again at :104).

**Phase 2: one `code-writer` dispatch covering all four files below.** Do not split it. The `_lib.sh` insertion and the hook's rename must land together, or the hook calls a function that doesn't exist: it fails closed (row 27), and its subprocess tests go red. The hook-test deletions are safe only once the unit file covers their cases. Order within the dispatch:
1. Write the unit file and observe it fail with the function absent.
2. Add the function.
3. Edit the hook and prune the hook tests.
4. Run Verification Phase 2, including the conditional latency fix in step 8.

1. `claude/.claude/hooks/_lib.sh`: insert after `_lib_realpath_m` (which ends at :175), before the `_lib_advance_offset_past_complete_lines` header at :177:
   ```bash
   # Succeeds only when $1 is non-empty and every byte is in [A-Za-z0-9._/@+-].
   # Matched in bash rather than grep, because BSD grep's -z still anchors ^/$ at each embedded newline.
   # The subshell body scopes LC_ALL=C to this match, since bracket ranges are byte ranges only in the C locale.
   _lib_passes_path_char_allowlist() (
     LC_ALL=C
     case "${1-}" in
       '' | *[!A-Za-z0-9._/@+-]*) exit 1 ;;
     esac
   )
   ```
2. `claude/.claude/hooks/announce-resume-command.sh`:
   - `:30`: name the `_lib.sh` function, e.g. "in [A-Za-z0-9._/@+-], checked by _lib_passes_path_char_allowlist in _lib.sh."
   - `:36-37`: delete the script-wide `LC_ALL=C` sentence.
   - `:84-93`: delete the `LC_ALL=C` line, its comment and the local function definition.
   - `:95` and `:104`: rename the calls to `_lib_passes_path_char_allowlist`.
   - Keep `:38-40`, the `$(...)` NUL/trailing-newline residual. That is a hook-level fact.
3. New `claude/.claude/hooks/tests/test_lib_path_char_allowlist.py`. Precedent for a separate file: `test_lib.py` is 6,633 lines, and five per-helper `test_lib_*.py` files already exist (row 33). `select-tests.py` maps it to `HOOKS_TESTS_DIR`, the same domain the hook edit already selects (row 31).
   - **Harness:**
     - Pin `/bin/bash`, the hooks' shebang. Don't use the bare `bash` that `test_lib.py:143` `_run_lib_call` and `claude/.claude/scripts/tests/test_worktree_lib.py`'s `_run_bash` resolve from PATH. On macOS that is often Homebrew bash 5, which hides the 3.2-only failure (row 28). Otherwise mirror `_run_lib_call`'s environment setup.
     - Pass values as bytes argv, not interpolated into the script: `[b"/bin/bash", b"-c", b'. "$1" || exit 90; shift; for v in "$@"; do if _lib_passes_path_char_allowlist "$v"; then printf 1; else printf 0; fi; done', b"_", lib, *values]`.
     - One bash spawn covers a whole matrix in milliseconds.
   - **Exhaustive single-byte sweep, 0x01–0xFF (NUL can't go in argv):** assert that exactly the 68 expected bytes are accepted. Build that 68-byte reference set independently in Python (e.g. `string.ascii_letters + string.digits + "._/@+-"`), not by copying the bash `case` pattern's literal text — a copy would make the assertion a restatement of the code under test rather than an independent check. Run it with `LC_ALL` set to `C` and to `en_US.UTF-8`. This covers what the carriage-return test covered and catches a typo in the class, including the boundary bytes `:`, `[`, `` ` `` and `{`.
   - **Whole-value cases, both locales:**
     - Deny: `''`, `"linked\n\nSENTINEL-INJECT\n\nwt"`, a trailing `\n`, a space, `café`, `esc\x1b[31m`, and raw `b"abc\x9bdef"`.
     - Allow: `team@x+y-handoff.md` and `/fake/wt@team/a+b`.
     - The raw `\x9b` case is the invalid-UTF-8 test. At the argv level it has no filename-encoding obstacle.
   - **Locale-precondition self-check:** before trusting any `en_US.UTF-8`-param result as a regression guard, assert that the `café` case's behavior under `en_US.UTF-8` is actually being exercised — e.g. also run a direct probe (`LC_ALL=en_US.UTF-8 bash -c '[[ é == [a-z] ]]'`-shaped check, or equivalent) and skip with a visible logged reason if the runner's `en_US.UTF-8` collapses to `C` behavior. Without this, a CI runner missing the `en_US.UTF-8` locale (row 29) makes the `en_US.UTF-8`-param run silently pass regardless of whether the helper's `LC_ALL=C` pin is later deleted by mistake — a real regression in the property this PR fixes would produce no failure signal at all, on either platform.
   - **Caller locale untouched:** `LC_ALL=en_US.UTF-8; _lib_passes_path_char_allowlist x; printf %s "$LC_ALL"` prints `en_US.UTF-8`. This pins the subshell scoping (row 22).
   - **No-arg call under `set -u`:** returns 1 without aborting.
4. `claude/.claude/hooks/tests/test_announce_resume_command.py`, keeping one subprocess case per branch.
   - Keep:
     - `:202` `test_linked_worktree_cwd_includes_cwd_flag`, the real-git allow case (`--cwd` included).
     - `:283` `test_clean_file_path_passes_allowlist`, the FILE_PATH allow case.
     - `:306` `test_file_path_with_embedded_newline_emits_nothing`, FILE_PATH deny-emits-nothing (the original red regression).
     - `:387` `test_worktree_root_with_embedded_newline_falls_back_to_bare_command`, the stub WORKTREE_ROOT deny case that drops only `--cwd`.
     - `:421` `test_clean_worktree_root_from_stub_git_emits_cwd_flag` with the `plain` param only, as the stub's positive control. Drop the `@pytest.mark.parametrize` decorator (:418-420) and set `clean_root = "/fake/worktrees/linked-root"` in the body.
     - `:360` `test_file_path_bytes_dropped_by_shell_announce_only_allowlisted_bytes`, the NUL/trailing-newline case. This is a `$(...)` property, not a helper property, so it can't move.
     - `:565` `test_allowlist_failing_path_never_invokes_git`, the allowlist-before-git ordering.
   - Delete (the new unit tests cover these):
     - `:296` `test_file_path_with_embedded_space_emits_nothing`.
     - `:321` `test_file_path_with_embedded_carriage_return_emits_nothing`.
     - `:331-353` `test_file_path_with_non_allowlisted_byte_emits_nothing_under_utf8_locale` (×2). Its docstring guards the `LC_ALL=C` line being removed.
     - `:378` `test_file_path_with_at_and_plus_passes_allowlist`.
     - The `at-and-plus` param at `:419`.
     - `:440` `test_empty_worktree_root_from_stub_git_emits_bare_command`. The `''` arm becomes a unit case, and `:387`, `:214` and `:228` already exercise the `[ -n "$WORKTREE_ROOT" ]` fallthrough.
     - `:459` `test_worktree_root_with_embedded_space_falls_back_to_bare_command`. It covers the same branch as `:387`, and `:202` already pins real git's argv shape. It is the one deleted test whose coverage predates this PR, so the PR body names it.
   - Fix the docstrings that point at deleted tests:
     - `:284-289` becomes `"""FILE_PATH allowlist gate, allow branch: a path built only from allowlisted bytes is announced."""`.
     - `:424-426` drops its last sentence, becoming `"""Positive control for the stub-git newline test above: the same stub with an allowlist-clean toplevel emits --cwd, proving the toplevel call is reached."""`.

**Reuse.** `_run_hook_raw`, `_write_fixture` and `_stub_git_linked_worktree` stay unchanged for the kept tests, unless Verification Phase 2 step 8 triggers the module-scoped stub.

**Phase 3: fold into the Phase 2 `code-writer` dispatch (same file set is already open).** `docs/hooks.md`'s `announce-resume-command.sh` entry (:155, :157):
- Replace the regex-mechanism description with the case-glob mechanism, naming `_lib_passes_path_char_allowlist` in `_lib.sh`.
- Replace "or nothing is emitted at all" (stated for both values) with the accurate split: a failing `FILE_PATH` emits nothing; a failing worktree root drops only `--cwd`.
- Fix the "Known gaps" bullet that repeats the "suppresses the announcement entirely" claim for a worktree path, to state the same `--cwd`-only-drop fact.

## Verification

Run from the worktree root (`.claude/worktrees/fix-announce-resume-newline-leak`).

**Phase 1, landed in `a167194f`.** Phase 2's steps re-cover every Phase 1 check that still applies. Step 1's red run cannot be repeated on this branch.

1. **Red (macOS), after the test edit only:** `../../../.venv/bin/python3 -m pytest -o addopts="" claude/.claude/hooks/tests/test_announce_resume_command.py -v` should show exactly **two** failures: `test_file_path_with_embedded_newline_emits_nothing` and the new `test_worktree_root_with_embedded_newline_falls_back_to_bare_command`. The new `test_file_path_with_embedded_carriage_return_emits_nothing` passes on the pre-fix hook, because a carriage return is not a newline and both grep implementations reject it (its docstring, :322-325).

   The new worktree-root test's first failing `assert` in source order is the `--cwd`-absence check, so the observed failure message names `--cwd`. The underlying leak is broader. Under the unfixed hook, `CANDIDATE_ROOT`'s `grep -Eqz` check shares `FILE_PATH`'s fail-open defect. `WORKTREE_ROOT` therefore holds the entire malicious value, and the full `SENTINEL-INJECT`-bearing string reaches `systemMessage`/`additionalContext`. The PR description should state the leak accurately (full string, not just `--cwd`) rather than repeat the narrower framing.

   If the new worktree-root test passes here (before the hook fix lands), its fixture is not reaching the gate. Fix the fixture before touching the hook.
2. **Green, after the hook edit:** the same command collects 26 tests with 0 failures. If the pattern-matching approach fails unexpectedly, record the failure and re-derive the fix rather than reverting to an unreviewed alternative.
3. `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py` is green.
4. `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` is clean, and the hook gains no new `# shellcheck disable` line.
5. `../../../.venv/bin/ruff check claude/.claude/hooks/tests/test_announce_resume_command.py` is clean.
6. CI on the PR (`ubuntu-24.04`: GNU tools, bash 5.x) is green — the second platform for rows 4 and 5.

**Phase 2.**

1. **Unit file, macOS:** `../../../.venv/bin/python3 -m pytest -o addopts="" claude/.claude/hooks/tests/test_lib_path_char_allowlist.py -v` passes every case under both locale params, including the caller-locale and `set -u` cases. Run it once before adding the function and confirm it fails.
2. **Locale mutation check, macOS only:** temporarily delete the helper's `LC_ALL=C` line and re-run step 1's command. At least one `en_US.UTF-8` case must fail; the expected failure is the `café` whole-value deny case. Restore the line. If nothing fails, the unit file does not guard the locale assignment, so fix the test before continuing. Linux bash 5 can't run this check, because it treats bracket ranges as ASCII ranges by default.
3. **Hook test file:** record the collected count of `../../../.venv/bin/python3 -m pytest -o addopts="" claude/.claude/hooks/tests/test_announce_resume_command.py -v` before the Phase 2 test edit. After the edit, the same command collects exactly 8 fewer tests, with 0 failures.
4. `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py` is green. Its run must include `test_hook_alignment.py` and `test_shell_lib_function_liveness.py` (rows 16-17). If its output doesn't list them, also run `../../../.venv/bin/python3 -m pytest -o addopts="" claude/.claude/hooks/tests/test_hook_alignment.py claude/.claude/hooks/tests/test_shell_lib_function_liveness.py`.
5. `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` is clean. Neither `_lib.sh` nor the hook gains a `# shellcheck disable` line.
6. `../../../.venv/bin/ruff check claude/.claude/hooks/tests/test_lib_path_char_allowlist.py claude/.claude/hooks/tests/test_announce_resume_command.py` is clean.
7. CI on the PR (`ubuntu-24.04`, bash 5) is green. The unit file's `en_US.UTF-8` param may run as C there (row 29), so steps 1-2 on macOS are the locale proof.
8. **Stub latency (row 30).** First run `:387` alone: `../../../.venv/bin/python3 -m pytest -o addopts="" -p no:xdist --durations=0 "claude/.claude/hooks/tests/test_announce_resume_command.py::TestAnnounceResumeCommand::test_worktree_root_with_embedded_newline_falls_back_to_bare_command"`. Then, in a scratch script, build one `_stub_git_linked_worktree` directory and pipe the same payload into the hook twice with that directory on PATH, timing each run.
   - **Second run fast:** move the stub *script* into a module-scoped fixture (via `tmp_path_factory`), so its script is written and first executed once. `_stub_git_linked_worktree` bakes the toplevel value and marker paths into the script text (:69-99). The module-scoped stub must read the toplevel value from a per-test env var instead. The marker-file *directory* stays function-scoped — pass a fresh per-test directory path in via its own env var, rather than writing markers under the shared module-scoped `stub_bin`. A marker directory shared across tests would let one test's leftover `unexpected-argv`/`show-toplevel-called` marker produce a false pass or a test-order-dependent failure in a later test that shares the module-scoped stub; only the script file itself should be amortized across tests.
   - **Second run also slow:** the hypothesis is wrong. Record the measured durations in the PR body and raise the residual to the engineer. Do not claim the latency is fixed.
9. **PR body** (run `/pr-description`, mechanical framing per M4) states:
   - that deleting `test_worktree_root_with_embedded_space_falls_back_to_bare_command` removes real-git coverage that predates this PR;
   - that deleting `test_file_path_with_at_and_plus_passes_allowlist` and the `at-and-plus` param removes hook-level coverage of the `@`/`+` bytes through the hook's own `jq --arg`/interpolation path — the new unit file covers only the helper's own byte-class judgment for those bytes, not that path;
   - the exported-`LC_ALL` side effect of removing the script-wide pin (row 26), backed by an actual check rather than assertion alone: run the hook once with `LC_ALL` exported to `en_US.UTF-8` against an allowlist-clean input, diff its stdout against the same run with `LC_ALL` unset, and confirm the two are byte-identical. State that result, not just the claim.
   - step 8's measured result.

**Phase 3.**

1. `git diff docs/hooks.md` shows only the three corrected claims (mechanism, FILE_PATH-failure text unchanged, worktree-root-failure text, Known-gaps bullet) — no unrelated rewording.
2. No test pins `docs/hooks.md`'s prose today, so there is no automated check beyond a read-through against the hook's own corrected header comment.

## Out of scope

- **`claude/.claude/hooks/set-session-title-from-branch.sh`:** audited, not changed. Its `grep -Eq` allowlist (:153-155) never receives an embedded newline (row 10), so there is no live bug there today. Follow-up: move its identical byte-set check onto `_lib_passes_path_char_allowlist`, for one home and two fewer `grep` forks. Its line-oriented `grep` has the same bug shape in principle.
- **`claude/.claude/hooks/consume-durable-continuity-file-on-read.sh`:** audited, not changed. It shares the continuity-path glob but has no allowlist gate and emits no path text into model-visible output (row 11).
- **Plugin hooks:** they ship their own `_lib.sh` copy, so the new helper isn't available to them. This is not a regression, only a scope note. `[unverified — not reopened in this revision]`
- **Residual stub-test latency** if Verification Phase 2 step 8 disproves the first-exec hypothesis: record the numbers and raise them to the engineer. It is not fixed in this PR on that branch of the outcome.
- **Installing `en_US.UTF-8` on the CI runner (row 29):** not done here. The macOS run in Verification Phase 2 steps 1-2 is the locale proof.
- **No macOS CI runner:** this fix's own bash-3.2-specific regression coverage runs only on a contributor's local macOS machine, never in CI. Adding a macOS runner or CI matrix is infra work beyond this PR's scope; raise as a tracked follow-up in the PR description.
