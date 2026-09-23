# Fix path-length test fixtures failing with OSError on macOS

## Context

Fix five test fixtures in claude-config's Python test suite that fail locally on macOS with filesystem-related `OSError`s, even though they pass in CI (Linux) — GitHub issue #1084. Four fixtures in `test_resume_context.py` build synthetic long paths to test `resume-context.sh`'s 2048-byte row-length cap and hit macOS APFS's real `PC_PATH_MAX` (1024 bytes) ceiling, which no amount of directory nesting can work around since it's a flat limit on any syscall's full path string, not a per-component limit — the issue's own suggested fix (shorter nesting components) cannot close this gap. A fifth fixture in `test_findings_path_suffix.py` builds an invalid-UTF-8 branch name and hits a related but distinct macOS/APFS constraint: the filesystem refuses to create filenames that aren't valid UTF-8, which breaks git's ref-lock file creation — a different mechanism than the issue describes for this test. Both are fixture-construction bugs, not production-code bugs; the shell scripts under test behave correctly. The fix adds capability-probe skips so these tests degrade gracefully wherever the local filesystem can't support the fixture's construction, while continuing to exercise the real assertions unchanged on Linux CI and anywhere else with sufficient headroom.

## Approach

Add two capability probes, each local to its own test file, that call `pytest.skip()` inside the test just before a fixture first touches the filesystem, plus filesystem-free unit tests of each probe's own boundary logic and a CI-config change so a future skip is auditable. Every assertion, fixture shape and production script stays the same. In `test_resume_context.py`, `_skip_if_bytes_exceed_path_max(fixture_root, byte_len)` skips when a fixture path's encoded byte length won't fit the `PC_PATH_MAX` that `os.pathconf` reports for `fixture_root`'s filesystem — called for both `src` and the script's own `dest`. In `test_findings_path_suffix.py`, `_skip_unless_filesystem_accepts_filename(directory, filename)` tries to create a file named with the branch name's exact bytes and skips on `OSError`. Linux CI keeps running all five tests. APFS skips them, and each skip reason names the measured limit or the OS error — now visible in CI's own log too, via `pyproject.toml`'s `addopts`.

**Path-length probe (`test_resume_context.py`).**

- Add `import pytest` as its own third-party import block after `from pathlib import Path` (row 12).
- Add this directly above `_src_path_of_exact_length`:

```python
def _skip_if_bytes_exceed_path_max(fixture_root: Path, byte_len: int) -> None:
    """Skip when byte_len (a path's encoded length) meets or exceeds PC_PATH_MAX
    for fixture_root's filesystem. Takes a byte count rather than a Path so a
    call site can probe a not-yet-built path length (e.g. the script's own
    mktemp destination) as easily as a real one.

    fixture_root must already exist, because probing an uncreated long path
    would itself risk hitting the limit this helper is meant to detect.
    """
    try:
        path_max = os.pathconf(fixture_root, "PC_PATH_MAX")
    except OSError as exc:
        pytest.skip(f"could not query PC_PATH_MAX for {fixture_root}: {exc}")
        return
    if 0 < path_max <= byte_len:
        pytest.skip(f"fixture path needs {byte_len} bytes; PC_PATH_MAX here is {path_max}")
```

- Call it at four sites. At each one, the call goes after the relevant length is known and before the first `mkdir`:
  - **`_src_path_of_exact_length`:** build `src_path = dir_path / ("a" * leaf_len)` first. Then call `_skip_if_bytes_exceed_path_max(tmpdir_root, len(os.fsencode(src_path)))`, then `dir_path.mkdir(parents=True, exist_ok=True)`, then `return src_path`. This single call covers both `test_row_at_exact_2048_byte_cap_appends` and `test_row_at_2049_bytes_skips_append_but_consume_still_succeeds`.
  - **The same two exact-boundary tests, before calling `_src_path_of_exact_length`:** immediately after `dest_len` is computed, call `_skip_if_bytes_exceed_path_max(tmp_path, dest_len)`. This closes the gap in row 3's reasoning: `dest_len` (the script's own `mktemp`-produced path) is a real path the shell script must resolve too, and it was never probed by the original design — only `src` was. `dest_len` is already an int (an ASCII-only formula: `len(str(tmp_path)) + 1 + _DEST_BASENAME_LEN`), so no `os.fsencode` conversion is needed for this call.
  - **The two inline-loop tests** (`test_row_exceeding_length_cap_skips_append_but_consume_still_succeeds` and `test_row_under_character_cap_but_over_byte_cap_skips_append`): move `src = deep / "task.md"` above `deep.mkdir(parents=True)`, and insert `_skip_if_bytes_exceed_path_max(tmp_path, len(os.fsencode(src)))` between them. The CJK test's `assert len(str(src)) < 2048` stays where it is.
- Correct two docstrings that claim the nesting avoids ENAMETOOLONG (row 20):
  - In `_src_path_of_exact_length`, replace "without tripping ENAMETOOLONG" with "without any single component exceeding NAME_MAX; skips where the filesystem's PC_PATH_MAX cannot hold the total".
  - In `test_row_exceeding_length_cap_...`, replace the same phrase with "without any single component exceeding NAME_MAX".

The byte length comes from `os.fsencode(path)`, the same conversion Python applies before the syscall. So the CJK fixture measures at 3 bytes per `中` (T + 2123 bytes, where T = `len(str(tmp_path))`) rather than its T + 719 characters. No caller needs a separate byte-or-character switch.

**Filename probe (`test_findings_path_suffix.py`).**

- Add this next to `_run_script` / `_exclude_lines`:

```python
def _skip_unless_filesystem_accepts_filename(directory: Path, filename: bytes) -> None:
    """Skip when the filesystem under `directory` refuses to create a file named with exactly these bytes."""
    probe_path = os.path.join(os.fsencode(directory), filename)
    try:
        with open(probe_path, "wb"):
            pass
    except OSError as exc:
        pytest.skip(f"filesystem rejects filename {filename!r}: {exc.strerror}")
        return
    try:
        os.unlink(probe_path)
    except OSError:
        pass  # tmp_path's own pytest teardown still removes it; a race/permission
              # quirk here must not fail a test whose creation already succeeded
```

- In `test_suffix_is_valid_utf8_when_branch_name_is_entirely_invalid_utf8`, insert `_skip_unless_filesystem_accepts_filename(tmp_path, branch_name)` between the `branch_name = ...` line and the `git checkout` call.
- The probe runs in `tmp_path`, which sits next to `repo/` on the same filesystem, so the repo under test is never touched.
- `os` and `pytest` are already imported.

**Boundary unit tests for both new helpers.** Neither helper's own comparison logic (the `<=` boundary, the `-1`/no-limit sentinel, the `os.pathconf` raise path) is exercised by any of today's five fixtures — they all sit at least 864 bytes clear of the boundary (row 2), so an off-by-one regression in either helper would pass silently on both environments in play. Add direct, filesystem-free unit tests using `monkeypatch`:

- In `test_resume_context.py`, near `_skip_if_bytes_exceed_path_max`:

```python
class TestSkipIfBytesExceedPathMax:
    def test_skips_when_byte_len_meets_path_max(self, monkeypatch, tmp_path):
        monkeypatch.setattr(os, "pathconf", lambda *_a: 100)
        with pytest.raises(pytest.skip.Exception):
            _skip_if_bytes_exceed_path_max(tmp_path, 100)

    def test_does_not_skip_one_byte_under_path_max(self, monkeypatch, tmp_path):
        monkeypatch.setattr(os, "pathconf", lambda *_a: 100)
        try:
            _skip_if_bytes_exceed_path_max(tmp_path, 99)
        except pytest.skip.Exception as exc:
            pytest.fail(f"unexpected skip: {exc}")

    def test_does_not_skip_when_pathconf_reports_no_limit(self, monkeypatch, tmp_path):
        monkeypatch.setattr(os, "pathconf", lambda *_a: -1)
        try:
            _skip_if_bytes_exceed_path_max(tmp_path, 10_000_000)
        except pytest.skip.Exception as exc:
            pytest.fail(f"unexpected skip: {exc}")

    def test_skips_when_pathconf_raises(self, monkeypatch, tmp_path):
        def _raise(*_a):
            raise OSError("no such pathconf name")

        monkeypatch.setattr(os, "pathconf", _raise)
        with pytest.raises(pytest.skip.Exception):
            _skip_if_bytes_exceed_path_max(tmp_path, 10)
```

- In `test_findings_path_suffix.py`, near `_skip_unless_filesystem_accepts_filename`, a parallel `TestSkipUnlessFilesystemAcceptsFilename` covering: a real, always-valid filename under `tmp_path` (must not skip) and a monkeypatched `open()` that raises `OSError` (must skip) — this file's probe has no `-1`/no-limit-style sentinel to cover, since it works by direct attempt rather than a queried ceiling.

Both classes use `pytest.raises(pytest.skip.Exception)` to assert a skip fires without the asserting test itself reporting as skipped, and the inverse pattern (call, catch, `pytest.fail` on an unexpected `Skipped`) to assert one does not — a bare call with no assertion would let a regressed helper silently skip the assertion test itself rather than fail it.

**CI skip-reason visibility.** The plan's design intent — "each skip reason names the measured limit or the OS error" — doesn't reach the CI log as CI is configured today: `.github/workflows/tests.yml`'s two "Run tests" steps invoke `pytest ... -v` with no `-r` flag, and `pyproject.toml`'s `addopts` doesn't add one either, so a future skip on CI would show a bare `SKIPPED` line with no reason text. Add `"-ra"` to `pyproject.toml`'s `addopts` (line 24: `addopts = ["-n", "auto", "--strict-markers"]` becomes `addopts = ["-n", "auto", "--strict-markers", "-ra"]`) — the single source of truth both CI steps and `select-tests.py` runs already inherit, rather than editing each CI step individually. `-ra` surfaces skip/xfail/error reasons in the terminal summary without the noise of listing every passed test. This plan's own Verification step 2 already overrides `addopts` entirely via `-o addopts=""` and adds its own `-v -rs`, so that command is unaffected by this change either way.

**Alternatives set aside.**

- **The ticket's fix (shorter nesting components):** it can't work. No fixture layout fits the exact-2048 append test under a 1024-byte `PC_PATH_MAX` (row 5).
- **`@pytest.mark.skipif(sys.platform == "darwin", ...)`:** lighter, but it names a platform instead of the capability that fails. It would skip a macOS machine whose tmp dir has headroom and still run on a Linux filesystem that lacks it. The engineer picked the probe (row 6).
- **A `skipif(os.pathconf(tempfile.gettempdir(), ...) < N)` evaluated when pytest collects tests:** the byte count depends on the length of `tmp_path` (row 2), which isn't known at that point, and `--basetemp` can move it. N would be a guess.
- **`try: mkdir ... except OSError: pytest.skip(...)`:** it would also swallow a fixture bug where one component exceeds NAME_MAX, which raises the same errno. It would also have to wrap the mkdir, the write and the script run.
- **`pytest.mark.xfail(raises=OSError)`:** it reports XFAIL instead of a skip and hides the same fixture bugs.
- **Finding the probe directory by walking `path.parents`:** stat-ing uncreated ancestors that are over the limit hits the same ceiling the helper probes (row 18).
- **Putting either probe in `claude/.claude/scripts/tests/conftest.py`:** each probe has a single consumer file (row 14). Move a probe there once a second file needs it.
- **Merging the filename probe into the path-length helper:** they check different constraints with different mechanisms. `pathconf` has no name for filename encoding (row 21), so the two share no logic.
- **Catching only `errno.EILSEQ` in the filename probe:** the errno is only known for APFS. Other filesystems that require UTF-8 names may return a different one. Putting `exc.strerror` in the skip reason keeps any unexpected cause visible instead.
- **Probing inside `repo/.git/refs/heads/` to mirror git exactly:** it would leave a ref-shaped file in the repo under test. `tmp_path` is already on the same filesystem.
- **Leaving `os.pathconf`'s raise path unhandled:** the first drafted helper only handled the documented `-1`/no-limit return value, not a raised `OSError`/`ValueError` on a filesystem that can't answer the query at all (a network mount, some FUSE/overlay filesystems). Left unhandled, the probe would itself crash uncaught on such a filesystem — reproducing the exact failure class this plan exists to eliminate, inside the fix. The `try`/`except OSError` above closes this.
- **Probing only `src`, never `dest`:** the first drafted design compared only the `src` path against `PC_PATH_MAX`, on the reasoning that `dest`/the index directory are always shorter "whenever T < 977" (row 3) — an unenforced precondition. On an unusually long `tmp_path` root (this repo's own nested `.claude/worktrees/<branch>/` convention, or a CI `--basetemp` under a long runner path), `src` could clear the probe while the script's own `mktemp`-derived `dest` doesn't, and the test would then hit the shell script's own failure (a nonzero `mv`/`mktemp` exit) rather than skip cleanly — the exact "crashes instead of degrading" outcome the plan exists to avoid, just relocated to a variable the probe never checked. Probing `dest_len` too, at the two exact-boundary call sites, closes this.

### Assumption ledger

**Root:** Five fixtures under `claude/.claude/scripts/tests/` build filesystem entries that APFS cannot hold: paths of 1024 bytes or more, and one filename that is invalid UTF-8. On macOS they error instead of skipping, even though the shell logic they exercise is correct and passes on Linux CI.

**Givens:**
- G1: APFS refuses to resolve a path string of 1024 bytes or more in any syscall. Apple's kernel imposes this (row 1).
- G2: APFS refuses to create a filename that is not valid UTF-8. Apple's filesystem imposes this (row 10).
- G3: git's default files ref backend creates `.git/refs/heads/<name>.lock` when it creates a branch. git owns that design, so a branch with an invalid-UTF-8 name can't be created wherever G2 holds (row 10).

**Mechanisms:**
- M1: `_skip_if_bytes_exceed_path_max` compares the probed `PC_PATH_MAX` with a caller-supplied byte length. It tests the capability that actually fails, not a platform name. anchors: row6, row1, row2, row15, row16
- M2: The byte length comes from `os.fsencode` at the `src`/CJK call sites, so one helper serves both the ASCII and CJK fixtures; `dest_len` (M10) is an already-ASCII int and needs no conversion. anchors: row2
- M3: `fixture_root` is an explicit parameter, not discovered from `path`. anchors: row18
- M4: The skip runs after the relevant length is known and before the first `mkdir`. anchors: row3
- M5: Each helper lives in the one test file that uses it. anchors: row14
- M6: `_skip_unless_filesystem_accepts_filename` actually tries to create the file. anchors: row10, row11, row21
- M7: The probe catches any `OSError` and puts `strerror` in the skip reason. anchors: row10
- M8: Tests skip by calling `pytest.skip` from a helper inside the test, not through a decorator. Lighter options were rejected: a `sys.platform` `skipif` (anchors: row6), a collection-time `pathconf` `skipif` (anchors: row2), and `xfail` or `try`/`except` around mkdir (anchors: row1). The in-body form is the repo's dominant idiom, not a heavier one. anchors: row13
- M9: The docstring corrections. anchors: row20, row1
- M10: The two exact-boundary tests also probe `dest_len`, not just `src`, closing the gap in row 3's original reasoning (staff-sdet round-1 finding). anchors: row3
- M11: `_skip_if_bytes_exceed_path_max` wraps its `os.pathconf` call in `try`/`except OSError`, skipping rather than crashing when a filesystem can't answer the query at all (staff-sdet round-1 finding). anchors: row16
- M12: `TestSkipIfBytesExceedPathMax` and `TestSkipUnlessFilesystemAcceptsFilename` unit-test each helper's own comparison logic (the boundary, the `-1` sentinel, the raise path) with `monkeypatch`, independent of which real filesystem the suite runs on (staff-sdet round-1 finding, since none of today's five fixtures land near enough to the boundary to exercise it). anchors: row15, row16
- M13: `pyproject.toml`'s `addopts` gains `-ra` so a future CI skip on any of these five tests shows its reason string in the log, not a bare `SKIPPED` (staff-sdet round-1 finding: CI's "Run tests" steps run `-v` with no `-r` flag today). anchors: root
- The test-only scope rests on row 19; M13 is a one-line CI-config addition, not a production-behavior change, so it doesn't reopen row 19. anchors: root

**Rows:**
1. [verified: this session's os.pathconf experiment] `os.pathconf(<tmp dir>, "PC_PATH_MAX")` returns 1024 on this macOS host whichever directory is queried. It is a flat limit, not the room left below that directory, so the helper compares the full absolute byte length against it. A full-path `os.stat` of a 1052-char path, built with `chdir` plus relative `mkdir` calls, still raised `[Errno 63] File name too long`. The ceiling binds every full-path syscall, however the tree was built.
2. [verified: test_resume_context.py:1004-1007, :1066-1069, :1090-1091, :1111-1112] With T = the byte length of `tmp_path`, the `src` each test passes to the script is:
   - T + 2273 bytes (15 × `"a"*150`, plus `/task.md`);
   - T + 2123 bytes (9 × `"中"*78` at 3 bytes per char, plus `/task.md`);
   - 2003 − T and 2004 − T bytes for the two exact-boundary tests.

   T was about 115 in this session's macOS reproduction, so every target is at least 1888 bytes, well over 1024. On a filesystem with a 4096-byte limit, a skip would need T ≥ 1823.
3. [verified: test_resume_context.py:817-824, :1090; resume-context.sh:321; hooks/_lib.sh:3473] The other paths these tests create are shorter than `src` whenever T < 977. The mktemp dest is T + 22 bytes, the index dir T + 25 (3-digit uid) and the day-file T + 49. This T < 977 precondition was originally unenforced — "checking `src` alone is sufficient" held only for today's typical `tmp_path` lengths, not as a general guarantee. M10 closes this by probing `dest_len` directly at the two exact-boundary call sites (the two inline-loop tests' targets are so far over the limit — T + 2273 / T + 2123 — that `dest`/index-dir/day-file can never be the longer path for any realistic T, so they don't need the same treatment).
4. [verified: resume-context.sh:161, :188; hooks/_lib.sh:3492-3493] `record_consumed_destination` creates the index dir before it checks the byte cap, and a failed creation silently skips the append. A "skips append" test whose index dir can't be created would therefore pass without testing anything.
5. [verified: arithmetic from rows 2-4] Under a 1024-byte `PC_PATH_MAX`, no fixture layout can make the exact-2048 append test fit. The day-file caps T at 975, so dest is at most 997 bytes and `src` at most 1024, which caps the row at 2044 bytes. The three skip-path tests could fit only by splitting the budget between a ~990-byte `RESUME_CONTEXT_TMPDIR` and a ~1010-byte `src`, with under 20 bytes spare.
6. [engineer-verified: "Portable skip via os.pathconf (Recommended)"] The four resume-context tests get a skip that probes `PC_PATH_MAX`.
7. [engineer-verified: "Portable skip via os.pathconf (Recommended)"] The question presented exactly two mutually exclusive options for handling the four resume-context tests (probe-and-skip vs. restructure the harness); selecting one under `AskUserQuestion`'s single-select semantics is a direct decision against the other, not an inference from an unstated preference — unlike a free-text answer, where inferring a rejection of an unmentioned alternative would need its own row. The selected option's description ("Runs unchanged on Linux CI … skips cleanly on macOS dev machines. Minimal, no production-code change.") is still the session's text, not the engineer's, and carries no independent weight here — only the option label does.
8. [engineer-verified: "Include it (Recommended)"] `test_suffix_is_valid_utf8_when_branch_name_is_entirely_invalid_utf8` is in scope for this plan.
9. [unverified] The phrase "Apply an analogous capability-probe skip" is the session's option description, not the engineer's words. M6 is justified by rows 10, 11 and 21, not by this row.
10. [verified: this session's reproduction] `git checkout -b` with `b"\xff\xfe\xfd\xfc\xfb\xfa\xf9\xf8"` fails with `Unable to create '.../refs/heads/<name>.lock': Illegal byte sequence`, and fails identically under `LC_ALL=C`. The valid-UTF-8 Cyrillic sibling test passes. The constraint is filename UTF-8 validity at the filesystem level, not locale and not non-ASCII content.
11. [unverified] Python's `open(<bytes path>, "wb")` with the same bytes under `tmp_path` raises `OSError` on APFS, just as git's lock-file `open(2)` does on the same filesystem. This wasn't run this session. Verification step 2 settles it.
12. [verified: test_resume_context.py:10-15; pyproject.toml:6; test_findings_path_suffix.py:12, :17] `test_resume_context.py` has no `import pytest`. Ruff's `I` rule is enabled, so the import needs its own third-party block. `test_findings_path_suffix.py` already imports `os` and `pytest`.
13. [verified: grep over *.py] Calling `pytest.skip(` inside the test body is the dominant idiom: 123 calls across 32 files, against 66 `@pytest.mark.skipif` decorators across 30 files. A helper that calls `pytest.skip` itself has a precedent: `require_direnv()` at claude/.claude/scripts/tests/conftest.py:91-104.
14. [verified: session's failing-test reproduction] Only `test_resume_context.py` needs the path-length probe, and only `test_findings_path_suffix.py` needs the filename probe.
15. [unverified: recalled POSIX `<limits.h>` definition, not fetched] PATH_MAX counts the terminating NUL, so the helper skips when the byte length is at least the limit. The exact boundary doesn't matter today: row 2's targets are at least 864 bytes above 1024 and far below 4096. M12's unit tests pin the `<=` comparison directly (not filesystem-dependently), so a future off-by-one in the operator fails a fast unit test instead of silently passing on both of today's environments.
16. [unverified: recalled CPython posixmodule.c, not read] `os.pathconf` returns −1 instead of raising when the filesystem reports no limit — but per staff-sdet's round-1 finding, it can also raise `OSError`/`ValueError` on a filesystem that can't answer the query at all, which this row's original text didn't address. M11's `try`/`except OSError` skips rather than crashes on that path; M12's unit tests pin both the `-1` sentinel and the raise path directly.
17. [unverified: recalled Linux/ext4 behavior] Linux reports a `PC_PATH_MAX` of 4096, and ext4 accepts any byte except `/` and NUL in a name. If so, neither probe fires on CI's `ubuntu-24.04` runner [verified: .github/workflows/tests.yml:25]. Verification step 4 settles it.
18. [verified: row 1's stat experiment] Stat-ing an uncreated path over the limit hits the same ceiling. Discovering a probe directory by walking `path.parents` would make exactly the failing syscalls, so the directory is passed in explicitly.
19. [verified: session reproduction; resume-context.sh:188] This is a test-only change. Every failure raises in the fixture's own `mkdir` or `git checkout`, before the script under test runs. The script logic the tests assert, including `resume-context.sh`'s `[ "$row_bytes" -gt 2048 ]` cap and `findings-path-suffix.sh`'s filtering, stays unchanged.
20. [verified: test_resume_context.py:844-848, :999-1001] Two docstrings say the nesting avoids ENAMETOOLONG. On APFS it only keeps each component under NAME_MAX.
21. [unverified: recalled POSIX pathconf name list] No `pathconf` name reports filename-encoding rules, so actually creating a file is the only portable probe for G2.

## Critical files

- `claude/.claude/scripts/tests/test_resume_context.py` (modify):
  - add `import pytest`;
  - add `_skip_if_bytes_exceed_path_max` (wraps `os.pathconf` in `try`/`except OSError`);
  - call it at four sites (inside `_src_path_of_exact_length`, once more for `dest_len` at the two exact-boundary tests, and in the two inline-loop tests);
  - add `TestSkipIfBytesExceedPathMax`, a filesystem-free unit test of the helper's own boundary/`-1`/raise-path logic;
  - correct two docstrings.
- `claude/.claude/scripts/tests/test_findings_path_suffix.py` (modify): add `_skip_unless_filesystem_accepts_filename` (with a guarded trailing `os.unlink`) and call it once, in `TestNonUtf8BranchNameEntirelyInvalid.test_suffix_is_valid_utf8_when_branch_name_is_entirely_invalid_utf8`; add `TestSkipUnlessFilesystemAcceptsFilename`, a small unit test covering the always-valid-filename case and a monkeypatched `open()`-raises case.
- `pyproject.toml` (modify): add `"-ra"` to `addopts` (line 24) so a future CI skip on any of these five tests shows its reason string in the log instead of a bare `SKIPPED`.
- Reuse:
  - `_src_path_of_exact_length`: one skip inside it covers both exact-boundary tests' `src`.
  - stdlib `os.pathconf` and `os.fsencode`.
  - `require_direnv()` at `claude/.claude/scripts/tests/conftest.py:91-104` as the model for a helper that calls `pytest.skip` itself. It is not imported, since it covers a different concern.
- Not modified: `claude/.claude/scripts/resume-context.sh`, `claude/.claude/scripts/findings-path-suffix.sh`, `claude/.claude/hooks/_lib.sh`, `claude/.claude/scripts/tests/conftest.py`.
- Dispatch split: a single `code-writer` dispatch covers all three files, checked by Verification steps 1-3. It isn't split because the two test-file edits need the same background on the skip convention and APFS limits, together come to well under 100 lines, and the `pyproject.toml` line is a one-line, low-risk addition alongside them.

## Verification

The commands use `.venv/bin/...` as run from the main checkout. README.md's Tests section gives the `../../../` prefix for a `.claude/worktrees/<branch>/` worktree. A branch name containing `/`, like this one, nests one level deeper.

1. Lint: `.venv/bin/ruff check claude/.claude/scripts/tests/test_resume_context.py claude/.claude/scripts/tests/test_findings_path_suffix.py` should report no errors, including under the `I` rule for the new `import pytest` block.
2. Targeted run on macOS: `.venv/bin/python3 -m pytest -o addopts="" -v -rs claude/.claude/scripts/tests/test_findings_path_suffix.py::TestNonUtf8BranchNameEntirelyInvalid claude/.claude/scripts/tests/test_resume_context.py::TestConsumedIndex claude/.claude/scripts/tests/test_resume_context.py::TestSkipIfBytesExceedPathMax claude/.claude/scripts/tests/test_findings_path_suffix.py::TestSkipUnlessFilesystemAcceptsFilename`. Expected result:
   - no failures and no errors;
   - exactly five SKIPPED (the `TestConsumedIndex`/`TestNonUtf8...` fixtures);
   - the four named resume-context tests skip with the reason `fixture path needs <N> bytes; PC_PATH_MAX here is 1024`, or, for the two exact-boundary tests, possibly at the `dest_len` probe instead of the `src` probe depending on `tmp_path`'s length on this machine;
   - the invalid-UTF-8 test skips with a reason ending in `Illegal byte sequence`, which settles row 11;
   - the Cyrillic sibling test and every other `TestConsumedIndex` test PASSED;
   - every test in the two new `TestSkipIfBytesExceedPathMax`/`TestSkipUnlessFilesystemAcceptsFilename` classes PASSED (these are filesystem-independent unit tests and must never skip).

   If the invalid-UTF-8 test reports FAILED or ERROR, the probe didn't reproduce git's failure. Stop and re-examine row 11 rather than widening what the probe catches.
3. Scoped suite: `.venv/bin/python3 claude/.claude/scripts/select-tests.py -rs` (the agent command documented in CLAUDE.md). Expect no failures in the three edited files.
4. CI: in the PR's `ubuntu-24.04` run, the "Run tests" step's log must list all five fixture node IDs as PASSED, not SKIPPED, and the four new unit-test node IDs as PASSED. This confirms the probes don't skip on a filesystem that has headroom, and settles rows 16 and 17. With `-ra` now in `addopts`, a future skip on any of these tests would additionally show its reason string in this same log — confirm the summary section lists reasons for any SKIPPED entry that does appear, if any does.

## Out of scope

- Production scripts (`resume-context.sh`, `findings-path-suffix.sh`, `hooks/_lib.sh`). The 2048-byte cap and the branch-name filtering are correct, and every failure is in fixture construction (row 19).
- Restructuring `resume-context.sh`'s test harness to unit-test the byte cap without real long paths. The engineer's selection declined it (row 7).
- Rewriting the fixtures to split the byte budget between a deep `RESUME_CONTEXT_TMPDIR` and `src`, which would let three of the four resume-context tests run on APFS (row 5). It leaves under 20 bytes spare and depends on `_lib.sh`'s index-dir name and the uid's digit count. It still can't fit the exact-2048 append test. It also changes fixture shapes that this plan keeps as they are.
- Failing hard when `GITHUB_ACTIONS` is set, as `require_direnv()` does. On today's Linux runner the skip can only fire if `tmp_path` exceeds 1822 bytes (row 2). With `-ra` now in `addopts` (M13), the CI log names any SKIPPED test's reason, which is the auditability this plan wants — a hard failure would additionally break a future macOS CI leg for a real filesystem limit.
- Adding a macOS leg to CI.
- `test_nudge_long_turn_subagent.py::TestNudgeLongTurnSubagent::test_scan_lock_release_point_matches_the_read_scan_write_scope`. The issue itself flags it as an unreproduced flake and leaves it off its failing-test list.
