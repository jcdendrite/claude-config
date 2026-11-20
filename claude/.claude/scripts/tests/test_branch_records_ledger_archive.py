"""Direct tests for branch_records_archive_ledger_file in _branch-records-lib.sh, sourced standalone in `bash -c`.

The sweep in review-ledger.sh calls this function once per stale ledger file, and the subprocess cases in
hooks/tests/test_review_ledger_sweep_archive.py hold one case per sweep branch. The input matrix lives here.
"""
from __future__ import annotations

import calendar
import os
import shlex
import stat
from pathlib import Path

import pytest
from helpers import (
    CP_OPERANDS_NOTE,
    assert_cap_engaged,
    real_binary_shim_env,
    scaled_shim_sleep,
    skip_without_timeout_binary,
    write_scaled_timeout_shim,
)

from . import _branch_records_helpers as records_helpers
from ._branch_records_helpers import (
    account_directory,
    build_archive_env,
    requires_non_root,
    run_archive_ledger,
    run_lib,
)

# The fixtures live once in the helper module. An import would trip F811 on every test parameter that names one.
reason = records_helpers.reason
status_shell_options = records_helpers.status_shell_options
process_isolation = records_helpers.process_isolation

_LEDGER_STEM = "repohash.branchhash"
_FIRST_MTIME = calendar.timegm((2024, 3, 1, 23, 30, 0))
_FIRST_STAMP = "20240301T233000Z"
_SECOND_MTIME = _FIRST_MTIME + 3600
_SECOND_STAMP = "20240302T003000Z"
_ROWS = '{"round":1}\n{"round":2}\n'
# A POSIX zone nine hours ahead of UTC, so 23:30 UTC on 1 March reads as 2 March locally.
# The angle-bracket name keeps the value clear of the tracker-ID shape the redaction hook denies.
_ZONE_AHEAD_OF_UTC = "<+09>-9"


def _write_ledger_file(tmp_path: Path, text: str = _ROWS, mtime: int = _FIRST_MTIME, stem: str = _LEDGER_STEM) -> Path:
    ledger_dir = tmp_path / "ledger_dir"
    ledger_dir.mkdir(exist_ok=True)
    ledger_file = ledger_dir / f"{stem}.jsonl"
    ledger_file.write_text(text)
    ledger_file.chmod(0o644)
    os.utime(ledger_file, (mtime, mtime))
    return ledger_file


def _copy_path(env: dict[str, str], stamp: str = _FIRST_STAMP) -> Path:
    return account_directory(env) / "review-narrative-ledger" / f"{_LEDGER_STEM}.{stamp}.jsonl"


def _assert_no_group_or_other_permission_bits(account_dir: Path) -> None:
    for path in [account_dir, *account_dir.rglob("*")]:
        mode = stat.S_IMODE(path.lstat().st_mode)
        assert mode & 0o077 == 0, f"{path} has mode {oct(mode)}"


class TestCopy:
    @pytest.mark.parametrize(
        "text", [_ROWS, "", '{"round":1}'], ids=["one-line-rows", "empty-file", "last-line-without-newline"]
    )
    def test_copy_lands_under_stem_and_utc_mtime_stamp_at_owner_only_mode(self, tmp_path, status_shell_options, text):
        env = {**build_archive_env(tmp_path, home_name="archive home"), "TZ": _ZONE_AHEAD_OF_UTC}
        ledger_file = _write_ledger_file(tmp_path, text)

        report = run_archive_ledger(
            tmp_path, env, str(ledger_file), shell_options=status_shell_options, in_ready_account=True
        )

        copy = _copy_path(env)
        assert (report.rc, report.reason, report.snapshot) == (0, "", copy)
        assert copy.read_bytes() == text.encode()
        assert stat.S_IMODE(copy.stat().st_mode) == 0o600
        assert stat.S_IMODE(account_directory(env).stat().st_mode) == 0o700
        assert stat.S_IMODE(ledger_file.stat().st_mode) == 0o644
        assert ledger_file.read_text() == text

    @pytest.mark.parametrize("account_dir_exists_at_0755", [False, True], ids=["no-account-directory", "account-directory-0755"])
    def test_copy_leaves_no_group_or_other_permission_bit_under_the_account_directory(
        self, tmp_path, status_shell_options, account_dir_exists_at_0755
    ):
        env = build_archive_env(tmp_path)
        ledger_file = _write_ledger_file(tmp_path)
        if account_dir_exists_at_0755:
            account_directory(env).mkdir(parents=True)
            account_directory(env).chmod(0o755)

        report = run_archive_ledger(
            tmp_path, env, str(ledger_file), shell_options=status_shell_options, in_ready_account=True
        )

        assert report.rc == 0, report.reason
        _assert_no_group_or_other_permission_bits(account_directory(env))

    def test_second_call_on_an_unchanged_file_returns_the_same_path_and_leaves_one_copy(
        self, tmp_path, status_shell_options
    ):
        env = build_archive_env(tmp_path)
        ledger_file = _write_ledger_file(tmp_path)

        first = run_archive_ledger(tmp_path, env, str(ledger_file), in_ready_account=True)
        second = run_archive_ledger(
            tmp_path, env, str(ledger_file), shell_options=status_shell_options, in_ready_account=True
        )

        assert (first.rc, second.rc) == (0, 0)
        assert second.snapshot == first.snapshot
        assert list(_copy_path(env).parent.iterdir()) == [_copy_path(env)]

    @pytest.mark.parametrize("change", ["append-a-row", "replace-with-disjoint-rows"])
    def test_file_with_a_later_mtime_gets_a_new_copy_and_the_first_copy_keeps_its_bytes(self, tmp_path, change):
        env = build_archive_env(tmp_path)
        ledger_file = _write_ledger_file(tmp_path)
        run_archive_ledger(tmp_path, env, str(ledger_file), in_ready_account=True)
        second_rows = _ROWS + '{"round":3}\n' if change == "append-a-row" else '{"round":9}\n'
        if change == "replace-with-disjoint-rows":
            ledger_file.unlink()
        ledger_file.write_text(second_rows)
        os.utime(ledger_file, (_SECOND_MTIME, _SECOND_MTIME))

        report = run_archive_ledger(tmp_path, env, str(ledger_file), in_ready_account=True)

        assert (report.rc, report.snapshot) == (0, _copy_path(env, _SECOND_STAMP))
        assert _copy_path(env, _SECOND_STAMP).read_text() == second_rows
        assert _copy_path(env).read_text() == _ROWS

    def test_existing_copy_at_the_name_that_differs_is_overwritten(self, tmp_path):
        env = build_archive_env(tmp_path)
        ledger_file = _write_ledger_file(tmp_path)
        stale_copy = _copy_path(env)
        stale_copy.parent.mkdir(parents=True)
        stale_copy.write_text("older partial copy\n")

        report = run_archive_ledger(tmp_path, env, str(ledger_file), in_ready_account=True)

        assert (report.rc, report.snapshot) == (0, stale_copy)
        assert stale_copy.read_text() == _ROWS


class TestFailures:
    @pytest.mark.parametrize(
        "arguments",
        [(), ("account-only",), ("", "file"), ("account", "")],
        ids=["no-arguments", "one-argument", "empty-account-directory", "empty-file"],
    )
    def test_missing_or_empty_argument_returns_copy_failed_and_writes_nothing(
        self, tmp_path, reason, status_shell_options, arguments
    ):
        env = build_archive_env(tmp_path)

        report = run_archive_ledger(tmp_path, env, *arguments, shell_options=status_shell_options)

        assert (report.rc, report.reason, report.snapshot) == (1, reason["LEDGER_COPY_FAILED"], None)
        assert not account_directory(env).exists()

    def test_empty_account_directory_with_a_regular_file_returns_copy_failed_before_any_filesystem_call(
        self, tmp_path, reason, status_shell_options
    ):
        env = build_archive_env(tmp_path)
        ledger_file = _write_ledger_file(tmp_path)

        report = run_archive_ledger(tmp_path, env, "", str(ledger_file), shell_options=status_shell_options)

        assert (report.rc, report.reason, report.snapshot) == (1, reason["LEDGER_COPY_FAILED"], None)
        assert not account_directory(env).exists()
        assert list(ledger_file.parent.iterdir()) == [ledger_file]
        assert ledger_file.read_text() == _ROWS

    @pytest.mark.parametrize("kind", ["missing", "symlink", "directory"])
    def test_file_that_is_not_a_regular_file_returns_copy_failed_and_creates_nothing(
        self, tmp_path, reason, status_shell_options, kind
    ):
        env = build_archive_env(tmp_path)
        real_file = _write_ledger_file(tmp_path)
        target = real_file.with_name("other.jsonl")
        if kind == "symlink":
            target.symlink_to(real_file)
        elif kind == "directory":
            target.mkdir()

        report = run_archive_ledger(
            tmp_path, env, str(target), shell_options=status_shell_options, in_ready_account=True
        )

        assert (report.rc, report.reason, report.snapshot) == (1, reason["LEDGER_COPY_FAILED"], None)
        assert not account_directory(env).exists()
        assert real_file.read_text() == _ROWS

    def test_a_failing_call_after_a_successful_one_carries_nothing_over_from_either_neighbour(
        self, tmp_path, reason
    ):
        env = build_archive_env(tmp_path)
        ledger_file = _write_ledger_file(tmp_path)
        missing_file = ledger_file.with_name("missing.jsonl")
        archive_call = 'call_archive_ledger "$BRANCH_RECORDS_ACCOUNT_DIR"'

        run = run_lib(
            tmp_path,
            env,
            "\n".join([
                "branch_records_status",
                f"{archive_call} {shlex.quote(str(missing_file))}",
                f"{archive_call} {shlex.quote(str(ledger_file))}",
                f"{archive_call} {shlex.quote(str(missing_file))}",
            ]),
        )

        outcomes = [(call.rc, call.reason, call.snapshot) for call in run.calls]
        assert outcomes == [
            (1, reason["LEDGER_COPY_FAILED"], None),
            (0, "", _copy_path(env)),
            (1, reason["LEDGER_COPY_FAILED"], None),
        ]

    def test_regular_file_where_the_subdirectory_belongs_returns_the_directory_reason(
        self, tmp_path, reason, status_shell_options
    ):
        env = build_archive_env(tmp_path)
        ledger_file = _write_ledger_file(tmp_path)
        account_directory(env).mkdir(parents=True)
        (account_directory(env) / "review-narrative-ledger").write_text("not a directory")

        report = run_archive_ledger(
            tmp_path, env, str(ledger_file), shell_options=status_shell_options, in_ready_account=True
        )

        assert (report.rc, report.reason, report.snapshot) == (2, reason["DIRECTORY"], None)
        assert ledger_file.read_text() == _ROWS

    def test_subdirectory_probe_killed_at_the_cap_returns_stalled_and_creates_nothing(
        self, tmp_path, reason, status_shell_options
    ):
        skip_without_timeout_binary()
        env = {
            **build_archive_env(tmp_path),
            **real_binary_shim_env(
                tmp_path / "stub-bin", {"test": 'case "$*" in *review-narrative-ledger*) exit 124;; esac\nexec "$REAL" "$@"'}
            ),
        }
        ledger_file = _write_ledger_file(tmp_path)

        report = run_archive_ledger(
            tmp_path, env, str(ledger_file), shell_options=status_shell_options, in_ready_account=True
        )

        assert (report.rc, report.reason, report.snapshot) == (2, reason["STALLED"], None)
        assert not account_directory(env).exists()

    def test_failing_date_returns_copy_failed_and_creates_nothing(self, tmp_path, reason, status_shell_options):
        env = {**build_archive_env(tmp_path), **real_binary_shim_env(tmp_path / "stub-bin", {"date": "exit 1"})}
        ledger_file = _write_ledger_file(tmp_path)

        report = run_archive_ledger(
            tmp_path, env, str(ledger_file), shell_options=status_shell_options, in_ready_account=True
        )

        assert (report.rc, report.reason, report.snapshot) == (1, reason["LEDGER_COPY_FAILED"], None)
        assert not account_directory(env).exists()

    def test_cp_that_writes_part_of_the_file_and_fails_returns_copy_failed_and_leaves_the_partial_copy(
        self, tmp_path, reason, status_shell_options
    ):
        env = {
            **build_archive_env(tmp_path),
            **real_binary_shim_env(tmp_path / "stub-bin", {"cp": CP_OPERANDS_NOTE + 'head -c 4 -- "$2" > "$3"\nexit 1'}),
        }
        ledger_file = _write_ledger_file(tmp_path)

        report = run_archive_ledger(
            tmp_path, env, str(ledger_file), shell_options=status_shell_options, in_ready_account=True
        )

        assert (report.rc, report.reason, report.snapshot) == (1, reason["LEDGER_COPY_FAILED"], None)
        assert _copy_path(env).read_text() == _ROWS[:4]
        assert ledger_file.read_text() == _ROWS

    def test_cp_killed_at_the_cap_after_writing_part_of_the_file_returns_stalled_and_removes_nothing(
        self, tmp_path, reason, status_shell_options
    ):
        env = build_archive_env(tmp_path)
        ledger_file = _write_ledger_file(tmp_path)
        run_archive_ledger(tmp_path, env, str(ledger_file), in_ready_account=True)
        stalling_cp_env = {
            **env,
            **real_binary_shim_env(tmp_path / "stub-bin", {"cp": CP_OPERANDS_NOTE + 'head -c 4 -- "$2" > "$3"\nexit 124'}),
        }

        report = run_archive_ledger(
            tmp_path,
            stalling_cp_env,
            str(ledger_file),
            shell_options=status_shell_options,
            in_ready_account=True,
        )

        assert (report.rc, report.reason, report.snapshot) == (2, reason["STALLED"], None)
        assert _copy_path(env).read_text() == _ROWS[:4]
        assert ledger_file.read_text() == _ROWS

    def test_cp_that_fails_without_writing_keeps_a_complete_copy_already_at_the_name(
        self, tmp_path, reason, status_shell_options
    ):
        env = build_archive_env(tmp_path)
        ledger_file = _write_ledger_file(tmp_path)
        run_archive_ledger(tmp_path, env, str(ledger_file), in_ready_account=True)
        failing_cp_env = {**env, **real_binary_shim_env(tmp_path / "stub-bin", {"cp": "exit 1"})}

        report = run_archive_ledger(
            tmp_path,
            failing_cp_env,
            str(ledger_file),
            shell_options=status_shell_options,
            in_ready_account=True,
        )

        assert (report.rc, report.reason, report.snapshot) == (1, reason["LEDGER_COPY_FAILED"], None)
        assert _copy_path(env).read_text() == _ROWS

    @requires_non_root
    def test_unreadable_file_makes_the_real_cp_fail_with_nothing_on_stderr(self, tmp_path, reason, status_shell_options):
        env = build_archive_env(tmp_path)
        ledger_file = _write_ledger_file(tmp_path)
        ledger_file.chmod(0)

        run = run_lib(
            tmp_path,
            env,
            f"call_archive_ledger_in_ready_account {shlex.quote(str(ledger_file))}",
            status_shell_options,
        )

        assert (run.only.rc, run.only.reason, run.only.snapshot) == (1, reason["LEDGER_COPY_FAILED"], None)
        assert run.only.state == "ready"
        ledger_copy_dir = account_directory(env) / "review-narrative-ledger"
        assert ledger_copy_dir.is_dir()
        assert not list(ledger_copy_dir.glob("*.jsonl"))
        assert run.stderr == ""

    def test_cp_that_exits_0_after_writing_a_truncated_copy_is_caught_by_the_real_cmp(
        self, tmp_path, reason, status_shell_options
    ):
        env = {
            **build_archive_env(tmp_path),
            **real_binary_shim_env(tmp_path / "stub-bin", {"cp": CP_OPERANDS_NOTE + 'head -c 4 -- "$2" > "$3"\nexit 0'}),
        }
        ledger_file = _write_ledger_file(tmp_path)

        report = run_archive_ledger(
            tmp_path, env, str(ledger_file), shell_options=status_shell_options, in_ready_account=True
        )

        assert (report.rc, report.reason, report.snapshot) == (1, reason["LEDGER_COPY_MISMATCH"], None)
        assert _copy_path(env).read_text() == _ROWS[:4]
        assert ledger_file.read_text() == _ROWS

    @pytest.mark.parametrize("chmod_status", [1, 124])
    def test_failed_or_stalled_chmod_of_the_account_directory_does_not_stop_the_copy(
        self, tmp_path, status_shell_options, chmod_status
    ):
        env = {**build_archive_env(tmp_path), **real_binary_shim_env(tmp_path / "stub-bin", {"chmod": f"exit {chmod_status}"})}
        ledger_file = _write_ledger_file(tmp_path)

        report = run_archive_ledger(
            tmp_path, env, str(ledger_file), shell_options=status_shell_options, in_ready_account=True
        )

        assert (report.rc, report.reason, report.snapshot) == (0, "", _copy_path(env))
        assert _copy_path(env).read_text() == _ROWS

    @pytest.mark.parametrize(
        ("cmp_status", "expected_rc", "expected_cause"),
        [(1, 1, "LEDGER_COPY_MISMATCH"), (2, 1, "LEDGER_COPY_FAILED"), (127, 1, "LEDGER_COPY_FAILED"), (124, 2, "STALLED")],
    )
    def test_cmp_status_selects_the_return_and_reason(
        self, tmp_path, reason, status_shell_options, cmp_status, expected_rc, expected_cause
    ):
        env = {**build_archive_env(tmp_path), **real_binary_shim_env(tmp_path / "stub-bin", {"cmp": f"exit {cmp_status}"})}
        ledger_file = _write_ledger_file(tmp_path)

        report = run_archive_ledger(
            tmp_path, env, str(ledger_file), shell_options=status_shell_options, in_ready_account=True
        )

        assert (report.rc, report.reason, report.snapshot) == (expected_rc, reason[expected_cause], None)
        assert ledger_file.read_text() == _ROWS
        assert _copy_path(env).read_text() == _ROWS

    def test_every_archive_volume_call_runs_under_the_cap(self, tmp_path, status_shell_options):
        skip_without_timeout_binary()
        timeout_log = tmp_path / "timeout_calls.log"
        # timeout's own arguments are `-k 2 SECONDS`, so the wrapped command starts at the fourth word.
        env = {
            **build_archive_env(tmp_path),
            **real_binary_shim_env(
                tmp_path / "stub-bin",
                {"timeout": f'printf \'%s\\n\' "${{*:4}}" >> {shlex.quote(str(timeout_log))}\nexec "$REAL" "$@"'},
            ),
        }
        ledger_file = _write_ledger_file(tmp_path)
        account_dir = account_directory(env)
        archive_dir = account_dir / "review-narrative-ledger"
        copy = _copy_path(env)
        archive_volume_calls = [
            f"test -d {archive_dir}",
            f"mkdir -- {account_dir}",
            f"mkdir -- {archive_dir}",
            f"chmod -- 700 {account_dir}",
            f"cp -- {ledger_file} {copy}",
            f"cmp -s -- {ledger_file} {copy}",
        ]

        report = run_archive_ledger(
            tmp_path, env, str(ledger_file), shell_options=status_shell_options, in_ready_account=True
        )

        assert (report.rc, report.reason, report.snapshot) == (0, "", copy)
        capped_calls = timeout_log.read_text().splitlines()
        assert sorted(call for call in capped_calls if call in archive_volume_calls) == sorted(archive_volume_calls)

    @pytest.mark.timing
    def test_cp_outliving_the_cap_returns_stalled(self, tmp_path, reason):
        shim_dir = tmp_path / "stub-bin"
        shim_dir.mkdir()
        if not write_scaled_timeout_shim(shim_dir):
            pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")
        env = {
            **build_archive_env(tmp_path),
            **real_binary_shim_env(shim_dir, {"cp": f"exec sleep {scaled_shim_sleep(10)}"}),
        }
        ledger_file = _write_ledger_file(tmp_path)

        with assert_cap_engaged(shim_dir, production_cap=5):
            report = run_archive_ledger(tmp_path, env, str(ledger_file), in_ready_account=True)

        assert (report.rc, report.reason, report.snapshot) == (2, reason["STALLED"], None)
        assert ledger_file.read_text() == _ROWS
