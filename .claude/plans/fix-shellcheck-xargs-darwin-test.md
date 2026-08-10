# Fix darwin-only failure in `test_xargs_zero_composition_exits_nonzero_on_empty_input`

## Context

Goal: make `claude/.claude/hooks/tests/test_shellcheck.py::TestGateActuallyBites::test_xargs_zero_composition_exits_nonzero_on_empty_input`
pass on both macOS (Darwin) dev machines and the Linux CI runner, without
losing the guarantee it exists to protect.

The test pins the actual CI shellcheck step
(`.github/workflows/tests.yml:159`, `./scripts/list-shell-files.sh | xargs -0 shellcheck`)
by asserting that `xargs -0 <shellcheck-binary>` on empty stdin still exits
nonzero. That assertion is true only under GNU xargs' default semantics
(invoke the command once even on empty input). BSD/macOS xargs defaults the
other way — it does not invoke the command at all on empty input, so `xargs`
itself exits 0 and the assertion fails. This was reproduced directly on this
machine: `printf '' | xargs -0 false` exits 0 here, confirmed against the
BSD xargs manpage's `-r`/`--no-run-if-empty` entry ("The FreeBSD version of
xargs does not run the utility argument on empty input... GNU... runs the
utility argument at least once, even if xargs input is empty"). It is a
platform default, not a bug in this repo's scripts.

The empty-list-fails-loudly behavior is itself intentional and documented in
two places — `scripts/list-shell-files.sh`'s header comment and the
"Exit 3 on an empty file list is a feature... deliberately not `|| true`"
comment at `.github/workflows/tests.yml:156-158` — so it is treated here as
a given, not something this plan revisits.

## Approach

**Root problem:** a test asserts GNU-xargs-specific default behavior, but
runs unconditionally on any dev machine, including macOS ones that ship BSD
xargs.

**Givens:**
- CI always runs this step on `ubuntu-24.04` (pinned in
  `.github/workflows/tests.yml:25`). [verified: .github/workflows/tests.yml:25]
  That line only pins the runner OS, not its toolchain — ubuntu-24.04
  ships GNU findutils `xargs` by default, but this plan does not treat that
  as a hard guarantee (see Approach: the skip is CI-aware and fails loud,
  rather than silently skipping, if the probe ever disagrees). Reason the
  runner-OS pin itself is fixed: changing the CI runner OS is out of this
  plan's scope and unrelated to the reported failure.

**Chosen approach:** make the test self-scoping — skip it (with a clear,
specific reason) on any host where `xargs -0 <cmd>` does not invoke `<cmd>`
on empty input, and keep it enforcing on hosts where it does (which always
includes CI today). Detect this by *probing actual xargs behavior* at
test-collection time, not by checking `platform.system() == "Darwin"`.

The probe-negative case is CI-aware, mirroring this file's existing
`_require_shellcheck()` precedent (`claude/.claude/hooks/tests/test_shellcheck.py:106-123`,
which skips locally but `pytest.fail()`s in CI when shellcheck isn't on
PATH, "since a skipped lint step reports green while checking nothing"):
locally (`CI` env var unset) the test skips with a stated reason; in CI
(`CI` env var set — GitHub Actions sets this by default) the same
probe-negative result fails the test instead of skipping it. This closes a
gap flagged in review: an unconditional skip would go silently inert if
CI's own `xargs` ever stopped defaulting to invoke-on-empty (image swap,
runner change, self-hosted substitution), defeating the exact guarantee
this test exists to pin with no failure signal. [staff-platform-engineer,
staff-sdet]

anchors: root
- Probing behavior instead of hardcoding an OS name is the one-line
  justification: an OS-name check would keep breaking (or keep silently
  under-testing) if a Linux distro ever shipped busybox xargs, or if CI ever
  moved to a macOS runner — the actual dependency is the xargs binary's
  semantics, not the kernel name. [verified: reproduced `printf '' | xargs
  -0 false` exits 0 on this machine, matching the BSD xargs manpage's `-r`
  entry cited in Context] — see Alternatives below for the two lighter/
  heavier options weighed.

**Alternatives weighed:**
1. **Hardcode `sys.platform != "darwin"` as the skip condition.** Rejected:
   couples the skip to *why* it currently fails on this one platform, not
   *what* the test depends on (xargs semantics) — a Linux box with a
   non-GNU xargs would still fail, and the hardcode would need updating for
   every future platform surprise. The probe is one subprocess call, not
   meaningfully heavier, so there's no cost trade-off favoring the hardcode.
2. **Make the production composition portable** (e.g., have the CI step /
   `scripts/list-shell-files.sh` explicitly check for zero discovered files
   and exit nonzero itself, instead of relying on implementation-defined
   xargs behavior at all) — this would let the test assert identical,
   portable behavior everywhere. Rejected for this change: the reported
   problem is test-only friction on a machine that never runs this
   production step for real (CI only runs on ubuntu-24.04); rewriting a
   documented, working, intentional CI behavior (two comments call it "a
   feature") to fix a local-test-ergonomics issue is a larger and riskier
   diff than the problem calls for (CLAUDE.md Axis 4: prefer minimal,
   targeted changes). Worth raising to the user as a follow-up if the
   guarantee should ever be platform-independent on principle, but not
   bundled into this fix.
3. **Delete or `xfail` the test on macOS.** Rejected: loses real coverage
   permanently rather than scoping it to where it's meaningful, and an
   `xfail` that always fails the same way is a weaker signal than a `skip`
   with a stated, checkable reason.

## Critical files

- `claude/.claude/hooks/tests/test_shellcheck.py`:
  1. Add a module-level probe helper, placed near `_require_shellcheck()`
     (around line 106) so the two CI-aware-skip helpers stay adjacent:

     ```python
     def _xargs_invokes_command_on_empty_input() -> bool:
         """True when `xargs -0 <cmd>` on empty stdin still invokes <cmd> once.

         GNU xargs does this by default; BSD/macOS xargs does not (see
         `xargs -r`/`--no-run-if-empty` in the FreeBSD/macOS man page). Probes
         with `false`, not the real shellcheck binary, so this decision never
         depends on shellcheck being installed — that's `_require_shellcheck()`'s
         job, and it runs independently.
         """
         try:
             result = subprocess.run(
                 ["xargs", "-0", "false"], input="", capture_output=True, text=True
             )
         except (OSError, subprocess.SubprocessError):
             return False
         return result.returncode != 0
     ```

     The `try/except` guard is required: an unguarded call raises
     `FileNotFoundError` out of `skipif`'s condition evaluation if `xargs`
     isn't resolvable on PATH, which aborts collection for the whole module
     (a `CollectError`) rather than affecting one test — a confusing failure
     mode for what should be a narrow, single-test concern.
     [staff-platform-engineer, staff-sdet]

  2. Replace the bare `pytest.mark.skipif` originally planned with a CI-aware
     check inside the test body itself (matching `_require_shellcheck()`'s
     shape, not `pytest.mark.skipif`, since the CI/local branch needs runtime
     logic a static marker can't express):

     ```python
     def test_xargs_zero_composition_exits_nonzero_on_empty_input(self):
         ...  # existing docstring unchanged
         if not _xargs_invokes_command_on_empty_input():
             reason = (
                 "local xargs does not invoke the command on empty input "
                 "(BSD/macOS default); CI's ubuntu-24.04 runner uses GNU "
                 "xargs, where this guarantee holds"
             )
             if os.environ.get("CI"):
                 pytest.fail(reason)
             pytest.skip(reason)
         binary = _require_shellcheck()
         ...  # existing body unchanged
     ```

     `os` is already imported in this file (used by `_require_shellcheck()`).
     No other test in the file depends on this semantic (confirmed via
     `grep -rn '\["xargs"' claude/.claude/hooks/tests/*.py`: this is the
     only direct `subprocess.run(["xargs", ...])` call in the test suite;
     other `xargs` hits are `xargs -n1` tokenization in hook scripts and
     tests, an unrelated concern). Reuse: no existing helper in `helpers.py`
     does this probe or anything close to it — a new one-off local function
     is appropriately scoped (single call site, no `helpers.py` addition
     needed).

No other file changes are needed: `scripts/list-shell-files.sh`,
`.github/workflows/tests.yml`, and `install-dev.sh` describe real CI
behavior and stay as-is per the given above.

## Verification

- `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_shellcheck.py -v`
  run from this worktree: the target test should now show `SKIPPED` locally
  (macOS/BSD xargs) with the stated reason visible in `-v` output, and the
  rest of the file's tests still pass.
- Confirm the probe's own correctness by running it standalone once:
  `printf '' | xargs -0 false; echo $?` — expect `0` on this machine
  (already reproduced during planning) and, for contrast, note that CI's
  `ubuntu-24.04` runner would print `1` there, which is what keeps the test
  active in CI.
- `../../../.venv/bin/ruff check claude/.claude/` to confirm the new helper
  passes lint.
- Full local suite: `../../../.venv/bin/pytest claude/.claude/` to confirm
  no unrelated regressions.
- CI itself is the final confirmation that the skip condition evaluates to
  "run" (not skip) on the Linux runner: check the `pytest -v` output in the
  PR's `tests` job log and confirm
  `test_xargs_zero_composition_exits_nonzero_on_empty_input` shows `PASSED`
  specifically — a green job alone doesn't distinguish "ran and passed"
  from "silently skipped," which is the exact failure mode this test
  exists to prevent one layer up. [staff-sdet]

## Out of scope

- **Making the empty-discovery-fails-loudly guarantee itself
  platform-independent** (e.g., an explicit zero-files check in
  `scripts/list-shell-files.sh` or the CI step, instead of relying on
  GNU xargs' invoke-on-empty default). This is in reach — both files live in
  this repo — but is deliberately not done here: see Approach, Alternative 2,
  for the rejection rationale. Flagging for the user in case the guarantee
  should be made portable on principle; not bundled into this fix.
