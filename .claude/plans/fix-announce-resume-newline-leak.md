# Fix the embedded-newline leak in announce-resume-command.sh

## Context

Make `announce-resume-command.sh`'s allowlist gate actually reject a
continuity-file path containing an embedded newline, closing a
security-relevant gap where the gate relies on GNU-grep-specific `-z`
anchoring semantics that BSD grep (macOS's default `/usr/bin/grep`) does not
provide — the gate silently fails open on macOS while a test already in this
repo pins the expected (rejecting) behavior.

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

Disclosure posture: the engineer selected "Direct fix PR, no exploit detail
(Recommended)" and said, "let's leave off the github private vulnerability
reporting" — ship as a normal PR, with no GitHub private vulnerability
report and no public issue describing the leak mechanism. Commit and PR
language should describe the fix mechanically (e.g., running the allowlist
checks in bash so they behave identically on BSD and GNU platforms) rather
than narrating the injection/exploit shape — that mechanical framing is this
plan's own proposal, not the engineer's wording.

## Approach

Replace both `grep -Eqz` allowlist checks with one hook-local bash helper, `_passes_allowlist`. It rejects an empty value and any value containing a byte outside `[A-Za-z0-9._/@+-]`, using a `case` arm of the form `'' | *[!A-Za-z0-9._/@+-]*)`. `LC_ALL=C` is assigned once in-script, so the bracket ranges are byte ranges. A new CANDIDATE_ROOT-arm embedded-newline test is written first, run red on macOS, and then turned green by the hook edit.

Why this form:
- **Newline handling.** A newline is simply a byte outside the set, so the check needs no line-anchoring semantics at all. It runs in bash's own pattern matcher rather than an external tool whose behavior differs by platform.
- **Repo precedent.** 26 `case` arms across 5 files under `claude/` already use the `''|*[!<set>]*)` shape as their pure-bash character-set check. Examples: `nudge-handoff-near-context-cap.sh:635` `""|*[!A-Za-z0-9_-]*) continue ;;` and `_lib.sh:210`.
- **Scope of the defect.** The only `grep -z` uses in the repo are the two lines being replaced. No `--null-data` or `grep -…z` appears in any non-`.sh` file either, so no other site shares this dependency.

Alternatives set aside:
- **Extglob `[[ "$v" == $ALLOWLIST_PATTERN ]]` with `ALLOWLIST_PATTERN='+([A-Za-z0-9._/@+-])'`.** The dispatching session verified this end to end (24/24). It needs `shopt -s extglob`, a parser change for the rest of the script with 0 uses anywhere in the repo, plus two `# shellcheck disable=SC2053` lines for the variable-held pattern. The negated form gets the same result with neither. It stays the fallback if the chosen form fails Verification step 2.
- **`[[ "$v" =~ $ALLOWLIST_RE ]]`.** It keeps the existing constant and needs no suppression. But bash hands `=~` to the platform C library's `regcomp`/`regexec`, so whether `^`/`$` anchor at an embedded newline again depends on each platform's regex implementation. That is the same kind of dependency that caused this bug.
- **Requiring GNU grep (`ggrep`, Homebrew coreutils).** Excluded by the Context: no platform-specific dependency.
- **Keeping grep and adding a separate `*$'\n'*` rejection.** This adds a second layer to compensate for the first layer's platform divergence, and leaves grep's per-platform semantics in place. That signals the foundation is wrong, so fix the foundation instead.

The helper stays in the hook rather than `_lib.sh`. `.claude/rules/bash-unit-test-seams.md` moves a hook helper into `_lib.sh` only once two or more hooks need it, and a new `_*-lib.sh` sidecar under `hooks/` fails CI today. The new subprocess test is a per-call-site pin, not an input-matrix entry. It is the only test that proves the call site at :93 rejects a newline.

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
  6. Assigning `LC_ALL=C` in the script, exported or not, switches the locale bash itself uses for bracket ranges. Bash re-runs `setlocale` on assignment to `LC_ALL`. The pin is needed because outside C, bracket ranges can follow collation order and admit non-ASCII bytes, which is the hazard documented at `set-session-title-from-branch.sh:18-23`. `[unverified for bash's own matcher; the hazard itself is verified: set-session-title-from-branch.sh:18-23]`
  7. No test exercises the locale pin today. Every allowlist fixture in the test file is ASCII, space, or newline. The fix carries the pin over; it does not start relying on it. `[verified: test_announce_resume_command.py read in full]`
  8. **Superseded by plan-review round 1 (`staff-sdet` finding).** Originally: "`git worktree add` accepts a path containing embedded newlines... that lets a real worktree drive the :93 arm. `[unverified]`." Dropped as the test vehicle — the call site never validates `CANDIDATE_ROOT` is a real directory, so a PATH-stubbed fake `git` proves the identical invariant deterministically without depending on this unverified, platform-variable git behavior. See Critical files §1.
  9. A real `Edit`/`Write`/`MultiEdit` payload can carry a `file_path` with an embedded newline on a macOS session. `[unverified — the dispatching session's own analysis; the harness source is outside this repo]`
  10. `set-session-title-from-branch.sh`'s identical `ALLOWLIST_RE` check (plain `grep -Eq` at :153-155) never sees an embedded newline. `REPO_COMPONENT` is the basename of a single `awk` output line (:137, :149). `BRANCH_COMPONENT` is either a `symbolic-ref --short` name or `@<short-sha>` (:112-122). `[verified: file read]` That git ref names exclude control characters, per `git-check-ref-format`, is `[unverified — not re-read this session]`.
  11. `consume-durable-continuity-file-on-read.sh` shares the continuity-path glob (:119-122) but has no allowlist gate. It passes `FILE_PATH` to `resume-context.sh --consume-only` and emits no path text into model-visible output. `[verified: dispatching session's read of that file]`
  12. Ship as a normal PR with no exploit detail. `[engineer-verified: "Direct fix PR, no exploit detail (Recommended)"]` `[engineer-verified: "let's leave off the github private vulnerability reporting"]`
  13. The option description behind row 12's label, "keep the commit message and PR body focused on 'hardens the allowlist gate for cross-platform grep portability'", is the dispatching session's own proposal, not the engineer's words. `[unverified]`
- **Mechanisms:**
  - M1: `_passes_allowlist` (the `case` helper) replaces both `grep -Eqz` checks. It is lighter than what it replaces (a bash builtin construct instead of an external process), so no heavier-primitive justification applies. `anchors: row5`
  - M2: `LC_ALL=C` is assigned once in-script, not exported, immediately above the helper. Only bash's own matcher reads it, so child `git`/`jq` calls keep the ambient environment unless `LC_ALL` was already exported. `anchors: row6`
  - M3: The CANDIDATE_ROOT-arm newline test is written and run red before the hook edit. `anchors: row8`
  - M4: Commit message and PR body describe the change mechanically (bash-native allowlist checks that behave the same on BSD and GNU platforms), not as an exploit. `anchors: row12`

## Critical files

One `code-writer` dispatch covering both files, in this order. Do not split it: the test must be observed red against the unfixed hook before the hook edit lands.

1. `claude/.claude/hooks/tests/test_announce_resume_command.py`: add `test_worktree_root_with_embedded_newline_falls_back_to_bare_command` directly after `test_worktree_root_with_embedded_space_falls_back_to_bare_command` (:287-311).

   **Revised per `staff-sdet`'s plan-review finding:** drive this with a PATH-stubbed fake `git` as the primary (only) vehicle, not a real `git worktree add`. The call site under test (`announce-resume-command.sh:92-95`) never validates that `CANDIDATE_ROOT` is a real directory — it consumes whatever `git -C <cwd> rev-parse --show-toplevel` prints, verbatim. A stub proves the identical invariant deterministically on every platform, with no dependency on whether the local `git` binary happens to accept a newline-containing path (real-`git`-worktree was the plan's original approach; dropped because that dependency is itself `[unverified]` and platform-variable — see the superseded row 8 note below).

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

   Argv discrimination: the four `_lib_capped`-wrapped calls all take the shape `git -C <cwd> rev-parse ...` (`timeout` execs the same argv through unchanged, per `staff-platform-engineer`'s plan-review confirmation), so `$4` (and `$5` for the two-flag `--git-common-dir` form) alone discriminates all four cases — no substring/`case "$*"` matching that could accidentally collapse two branches. `PAYLOAD_CWD` is `isolated_home` (via `cwd=str(isolated_home)` in `write_input`) — the stub ignores the `-C` target entirely, so it only needs to be *some* existing directory, matching the existing precedent in `test_non_matching_path_never_invokes_git`.

   Also add `test_file_path_with_embedded_carriage_return_emits_nothing`, mirroring `test_file_path_with_embedded_space_emits_nothing` exactly (same shape, same assertions — `result.returncode == 0` and `result.stdout == ""`), with `malicious_name = "notes\rhandoff.md"` in place of the space fixture — a cheap adjacent-boundary-byte regression per `ciso-reviewer`'s plan-review finding, guarding against a future typo widening `[A-Za-z0-9._/@+-]` to admit `\r`. (A raw invalid-UTF-8-byte test, also suggested by `ciso-reviewer`, is deferred — see Out of scope.)

   **Reuse.** `_write_fixture`, `_run_hook_raw`, `write_input`, `isolated_home`. Needs a `shlex` import if not already present (it is — used at :380, :403 for the existing stub tests).
2. `claude/.claude/hooks/announce-resume-command.sh`:
   - **Replace :78-84**, the `ALLOWLIST_RE` constant, its inline `-z` comment block, and the `grep -Eqz` line, with:
     ```bash
     # Bracket ranges are byte ranges only in the C locale.
     LC_ALL=C

     # Succeeds only when $1 is non-empty and every byte is in [A-Za-z0-9._/@+-].
     # Matched in bash rather than grep, because BSD grep's -z still anchors ^/$ at each embedded newline.
     _passes_allowlist() {
       case "$1" in
         '' | *[!A-Za-z0-9._/@+-]*) return 1 ;;
       esac
     }

     _passes_allowlist "$FILE_PATH" || exit 0
     ```
   - **Replace :93's condition** with `if _passes_allowlist "$CANDIDATE_ROOT"; then`. The helper's `''` arm covers the old `[ -n "$CANDIDATE_ROOT" ]` check.
   - **If shellcheck reports SC2034 on `LC_ALL=C`**, switch to `export LC_ALL=C`, the dispatching session's shellcheck-clean form. Do not add a suppression.
   - **Leave unchanged:** the header block (:1-54), which states the set and the `LC_ALL=C` contract but not the grep/`-z` mechanism, and the SC2016 disable at :105.
   - **Precedent** for hook-local underscore helper names: `_marker_shape_match` (`enforce-marker-script-shape.sh:200`), `_sanitize_ask_field` (`ask-new-dependency-disclosure.sh:155`).

## Verification

Run from the worktree root (`.claude/worktrees/fix-announce-resume-newline-leak`):

1. **Red (macOS), after the test edit only:** `../../../.venv/bin/python3 -m pytest -o addopts="" claude/.claude/hooks/tests/test_announce_resume_command.py -v` should show exactly **three** failures: `test_file_path_with_embedded_newline_emits_nothing`, the new `test_worktree_root_with_embedded_newline_falls_back_to_bare_command`, and the new `test_file_path_with_embedded_carriage_return_emits_nothing`.

   **Correction from plan-review round 1 (`staff-sdet` finding):** the new worktree-root test's failure reason is not merely "`--cwd` appears" — under the old (unfixed) hook, `CANDIDATE_ROOT`'s `grep -Eqz` check shares the identical fail-open defect as `FILE_PATH`'s, so `WORKTREE_ROOT` is set to the *entire* malicious value and the old hook leaks the full `SENTINEL-INJECT`-bearing string into `systemMessage`/`additionalContext`, not just an extra `--cwd` flag. The test fails on its first `assert` in source order (the `--cwd`-absence check), so the observed failure message names `--cwd`, but the underlying leak is broader — the PR description should state the leak accurately (full string, not just `--cwd`) rather than repeat the narrower framing.

   If either new test passes here (before the hook fix lands), its fixture is not reaching the gate — fix the fixture before touching the hook.
2. **Green, after the hook edit:** the same command collects 26 tests with 0 failures. If the pattern-matching approach fails unexpectedly, record the failure and re-derive the fix rather than reverting to an unreviewed alternative.
3. `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py` is green.
4. `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` is clean, and the hook gains no new `# shellcheck disable` line. If the `LC_ALL=C` → `export LC_ALL=C` SC2034 fallback (Critical files §2) is actually triggered, note in the PR description that `LC_ALL=C` becomes ambient to the downstream `git`/`jq` calls — `staff-platform-engineer`'s plan-review pass confirmed this is very likely benign (those calls emit path data, not locale-formatted text, and already discard stderr) but flagged it as worth a one-line confirmation in the diff that actually ships.
5. `../../../.venv/bin/ruff check claude/.claude/hooks/tests/test_announce_resume_command.py` is clean.
6. CI on the PR (`ubuntu-24.04`: GNU tools, bash 5.x) is green — the second platform for rows 4 and 5.

## Out of scope

- **`claude/.claude/hooks/set-session-title-from-branch.sh`:** audited, not changed. Its `grep -Eq` allowlist (:153-155) never receives an embedded newline (row 10), so the bug does not occur there. Moving it onto `_passes_allowlist` would require moving the helper into `_lib.sh`, a wider change with no bug behind it.
- **`claude/.claude/hooks/consume-durable-continuity-file-on-read.sh`:** audited, not changed. It shares the continuity-path glob but has no allowlist gate and emits no path text into model-visible output (row 11).
- **Moving `_passes_allowlist` into `_lib.sh` with direct unit tests:** deferred until a second hook needs it, per `.claude/rules/bash-unit-test-seams.md`.
- **Pre-existing doc drift, unrelated to this bug:**
  - The hook header (:28-30) and `docs/hooks.md:73` say a failing allowlist emits "nothing at all" for both values.
  - `docs/hooks.md:75` says a worktree path containing a space suppresses the announcement entirely.
  - In fact, a failing worktree root drops only `--cwd`: the hook's own Known-gaps paragraph (:42-44) and `test_worktree_root_with_embedded_space_falls_back_to_bare_command` both show this.
  - Raise it to the reviewer as one follow-up that fixes both sites together.
- **A regression test for the `LC_ALL=C` pin itself:** none exists for the grep form either. A meaningful one depends on which non-C locales each runner has installed (row 7). Per `staff-sdet`'s plan-review finding, this is a defensible deferral (forcing a specific non-C locale in CI risks flakiness on stripped-down images) but should be tracked as a follow-up rather than left permanently implicit — file a non-security tracked issue for a `LC_ALL=C`-specific regression test once this PR lands.
- **A raw invalid-UTF-8-byte filename test:** per `ciso-reviewer`'s plan-review finding, deferred in favor of the cheaper `test_file_path_with_embedded_carriage_return_emits_nothing` addition (Critical files §1) — a genuinely invalid byte sequence in a filename runs into cross-platform filename-encoding constraints (Python's `pathlib`/`str` path handling and macOS's filesystem both expect valid UTF-8 names), which is disproportionate machinery for a boundary-byte regression test in this PR.
