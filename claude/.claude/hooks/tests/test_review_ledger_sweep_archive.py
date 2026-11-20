"""One subprocess case per branch of review-ledger.sh's retention sweep with the `agent_branch_records` key on.

The input matrix for the copy itself is in scripts/tests/test_branch_records_ledger_archive.py.
The `disabled` sweep is covered by TestReviewLedgerMtimeSweep in test_review_ledger_script.py.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from pathlib import Path

import pytest
from helpers import (
    CP_OPERANDS_NOTE,
    config_schema_text_without_key,
    install_review_ledger_scripts,
    real_binary_shim_env,
    skip_without_timeout_binary,
)
from scripts.tests._branch_records_helpers import account_component, expected_ledger_key

from .conftest import _seed_session
from .test_review_ledger_script import REVIEW_LEDGER_SCRIPT, SID, _append_args, _git, _run

# The lib's reason test and the doc's reason table pin these values, so a drift fails the whole-line comparisons below.
_REASON_CONFIG = "the config directory or the archive setting could not be resolved"
_REASON_SENTINEL = "the archive root is missing its readiness marker"
_REASON_DIRECTORY = "an archive directory could not be created"
_REASON_STALLED = "a filesystem call under the archive root did not answer in time"
_REASON_LEDGER_COPY_FAILED = "copying a review ledger file failed"

_STALE_WINDOW_DAYS = 30
_STALE_AGE_DAYS = _STALE_WINDOW_DAYS + 1
_ROWS = '{"finding":"stale"}\n'
_SENTINEL_NAME = ".agent-branch-records-root"
_TRIGGERS = ["append", "clear-stale"]


def _ledger_dir(home: Path) -> Path:
    return home / ".claude" / "review-narrative-ledger"


def _plant_stale(home: Path, name: str, text: str = _ROWS, age_days: int = _STALE_AGE_DAYS) -> Path:
    path = _ledger_dir(home) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    age = int(time.time()) - age_days * 24 * 60 * 60
    os.utime(path, (age, age))
    return path


def _enable_archive(tmp_path: Path, home: Path, *, key_line: str = "agent_branch_records = true", sentinel: bool = True) -> Path:
    """Turns the key on under `home`, creates the archive root, and returns the account directory the sweep resolves."""
    config_dir = home / ".claude"
    (config_dir / "claude-config.toml").write_text(key_line + "\n")
    # An absent window setting makes clear-stale warn on stderr, which the stderr assertions below exclude.
    (config_dir / "settings.json").write_text(json.dumps({"cleanupPeriodDays": _STALE_WINDOW_DAYS}))
    root = home / ".local" / "state" / "agent-branch-records"
    assert tmp_path.resolve() in root.resolve().parents, f"the archive root {root} is outside {tmp_path}"
    root.mkdir(parents=True)
    if sentinel:
        (root / _SENTINEL_NAME).write_text("")
    return root / account_component(config_dir)


def _copies(account_dir: Path) -> list[Path]:
    return sorted((account_dir / "review-narrative-ledger").glob("*.jsonl"))


def _call_log_lines(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


def _utc_stamp(mtime: float) -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(mtime))


def _cp_shim_failing_first_call(log: Path, later_calls: str) -> str:
    quoted_log = shlex.quote(str(log))
    log_call = f'printf \'%s\\n\' "$*" >> {quoted_log}\n'
    return CP_OPERANDS_NOTE + log_call + f'[ "$(wc -l < {quoted_log})" -eq 1 ] && exit 1\n{later_calls}'


def _sweep_line(kept: int, reason: str) -> str:
    return (
        f"review-ledger.sh: retention sweep kept {kept} ledger file(s) past retention "
        f"because they could not be archived: {reason} (see docs/agent-branch-records.md)"
    )


def _run_trigger(trigger: str, cwd: Path, home: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    args = _append_args() if trigger == "append" else ["clear-stale"]
    return _run(args, cwd=cwd, home=home, extra_env=extra_env, timeout=60)


def _run_installed(script: Path, args: list[str], cwd: Path, home: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    return subprocess.run(["bash", str(script), *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=60)


@pytest.fixture
def feature_repo(isolated_home, git_repo):
    """On a feature branch with a session, so `append` prints nothing about session scope."""
    _git(git_repo, "checkout", "-q", "-b", "feature")
    _seed_session(isolated_home, SID)
    return git_repo


class TestReadyArchive:
    def test_append_copies_two_stale_files_then_removes_them_and_probes_the_sentinel_and_chmods_the_account_directory_once(
        self, tmp_path, isolated_home, feature_repo
    ):
        skip_without_timeout_binary()
        account_dir = _enable_archive(tmp_path, isolated_home)
        first = _plant_stale(isolated_home, "0" * 64 + ".first-session.jsonl", '{"finding":"first"}\n')
        second = _plant_stale(isolated_home, "1" * 64 + ".second-session.jsonl", '{"finding":"second"}\n')
        log = tmp_path / "test_calls.log"
        chmod_log = tmp_path / "chmod_calls.log"
        env = real_binary_shim_env(
            tmp_path / "stub-bin",
            {
                "test": f'printf \'%s\\n\' "$*" >> {shlex.quote(str(log))}\nexec "$REAL" "$@"',
                "chmod": f'printf \'%s\\n\' "$*" >> {shlex.quote(str(chmod_log))}\nexec "$REAL" "$@"',
            },
        )

        result = _run(_append_args(), cwd=feature_repo, home=isolated_home, extra_env=env, timeout=60)

        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        assert first.name not in result.stdout and second.name not in result.stdout
        assert not first.exists() and not second.exists()
        assert sorted(copy.read_text() for copy in _copies(account_dir)) == ['{"finding":"first"}\n', '{"finding":"second"}\n']
        assert len([line for line in _call_log_lines(log) if _SENTINEL_NAME in line]) == 1
        assert [line for line in _call_log_lines(chmod_log) if str(account_dir) in line] == [f"-- 700 {account_dir}"]

    def test_append_archives_only_the_regular_file_among_stale_non_regular_entries(
        self, tmp_path, isolated_home, feature_repo
    ):
        account_dir = _enable_archive(tmp_path, isolated_home)
        regular = _plant_stale(isolated_home, "0" * 64 + ".regular.jsonl")
        stale_lock = _plant_stale(isolated_home, "0" * 64 + ".regular.jsonl.lock", "")
        outside_target = tmp_path / "outside_target.txt"
        outside_target.write_text("outside\n")
        old = int(time.time()) - _STALE_AGE_DAYS * 24 * 60 * 60
        planted_entries = {
            "dangling": _ledger_dir(isolated_home) / ("2" * 64 + ".dangling.jsonl"),
            "link_to_regular": _ledger_dir(isolated_home) / ("3" * 64 + ".link.jsonl"),
            "fifo": _ledger_dir(isolated_home) / ("4" * 64 + ".fifo.jsonl"),
            "directory": _ledger_dir(isolated_home) / ("5" * 64 + ".directory.jsonl"),
        }
        planted_entries["dangling"].symlink_to(tmp_path / "missing_target")
        planted_entries["link_to_regular"].symlink_to(outside_target)
        os.mkfifo(planted_entries["fifo"])
        planted_entries["directory"].mkdir()
        for entry in planted_entries.values():
            os.utime(entry, (old, old), follow_symlinks=False)

        result = _run(_append_args(), cwd=feature_repo, home=isolated_home, timeout=60)

        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        assert not regular.exists() and not stale_lock.exists()
        assert [copy.read_text() for copy in _copies(account_dir)] == [_ROWS]
        for removed in ("dangling", "link_to_regular", "fifo"):
            assert not planted_entries[removed].is_symlink() and not planted_entries[removed].exists()
        assert outside_target.read_text() == "outside\n"

    def test_clear_stale_reports_archive_and_evict_and_counts_the_file(self, tmp_path, isolated_home, feature_repo):
        account_dir = _enable_archive(tmp_path, isolated_home)
        stale = _plant_stale(isolated_home, "0" * 64 + ".stale.jsonl")

        result = _run(["clear-stale"], cwd=feature_repo, home=isolated_home, timeout=60)

        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [
            f"Archive: ready ({account_dir})",
            f"  archive and evict: {stale.name}",
            "clear-stale: evicted 1 file(s)",
        ]
        assert result.stderr == ""
        assert not stale.exists() and len(_copies(account_dir)) == 1

    def test_dry_run_reports_what_it_would_archive_and_creates_and_removes_nothing(
        self, tmp_path, isolated_home, feature_repo
    ):
        account_dir = _enable_archive(tmp_path, isolated_home)
        stale = _plant_stale(isolated_home, "0" * 64 + ".stale.jsonl")

        result = _run(["clear-stale", "--dry-run"], cwd=feature_repo, home=isolated_home, timeout=60)

        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [
            f"Archive: ready ({account_dir})",
            f"  archive and evict (dry-run): {stale.name}",
            "clear-stale: would evict 1 file(s)",
        ]
        assert not account_dir.exists()
        assert stale.exists()

    def test_dry_run_with_no_ledger_directory_prints_only_the_archive_line(self, tmp_path, isolated_home, git_repo):
        account_dir = _enable_archive(tmp_path, isolated_home)
        assert not _ledger_dir(isolated_home).exists()

        result = _run(["clear-stale", "--dry-run"], cwd=git_repo, home=isolated_home, timeout=60)

        assert (result.returncode, result.stdout) == (0, f"Archive: ready ({account_dir})\n")

    def test_clear_stale_names_the_copy_of_a_real_branch_ledger_file_by_the_snapshot_ledger_key(
        self, tmp_path, isolated_home, feature_repo
    ):
        account_dir = _enable_archive(tmp_path, isolated_home)
        ledger_key = expected_ledger_key(str(feature_repo.resolve()), "feature")
        appended = _run(_append_args(), cwd=feature_repo, home=isolated_home, timeout=60)
        assert appended.returncode == 0, appended.stderr
        branch_ledger = _ledger_dir(isolated_home) / f"{ledger_key}.jsonl"
        assert list(_ledger_dir(isolated_home).glob("*.jsonl")) == [branch_ledger]
        aged_mtime = int(time.time()) - _STALE_AGE_DAYS * 24 * 60 * 60
        os.utime(branch_ledger, (aged_mtime, aged_mtime))
        stamp = _utc_stamp(aged_mtime)

        result = _run(["clear-stale"], cwd=feature_repo, home=isolated_home, timeout=60)

        assert result.returncode == 0, result.stderr
        assert not branch_ledger.exists()
        assert [copy.name for copy in _copies(account_dir)] == [f"{ledger_key}.{stamp}.jsonl"]


class TestKeyOffOrUnreadable:
    @pytest.mark.parametrize("trigger", _TRIGGERS)
    def test_key_false_removes_the_stale_file_with_no_archive_directory_and_no_archive_line(
        self, tmp_path, isolated_home, feature_repo, trigger
    ):
        account_dir = _enable_archive(tmp_path, isolated_home, key_line="agent_branch_records = false")
        stale = _plant_stale(isolated_home, "0" * 64 + ".stale.jsonl")

        result = _run_trigger(trigger, feature_repo, isolated_home)

        assert result.returncode == 0, result.stderr
        assert not stale.exists() and not account_dir.exists()
        assert "Archive:" not in result.stdout
        assert result.stderr == ""
        if trigger == "clear-stale":
            assert result.stdout.splitlines() == [f"  evict: {stale.name}", "clear-stale: evicted 1 file(s)"]

    def test_key_false_dry_run_lists_the_stale_file_and_removes_nothing(self, tmp_path, isolated_home, feature_repo):
        account_dir = _enable_archive(tmp_path, isolated_home, key_line="agent_branch_records = false")
        stale = _plant_stale(isolated_home, "0" * 64 + ".stale.jsonl")

        result = _run(["clear-stale", "--dry-run"], cwd=feature_repo, home=isolated_home, timeout=60)

        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [f"  evict (dry-run): {stale.name}", "clear-stale: would evict 1 file(s)"]
        assert result.stderr == ""
        assert stale.exists() and not account_dir.exists()

    def test_schema_without_the_key_row_keeps_every_due_file_and_warns_with_the_config_reason(
        self, tmp_path, isolated_home, feature_repo
    ):
        script = install_review_ledger_scripts(isolated_home)
        degraded_schema = isolated_home / ".claude" / "hooks" / "config-keys.psv"
        degraded_schema.unlink()
        degraded_schema.write_text(config_schema_text_without_key("agent_branch_records"))
        stale_files = [_plant_stale(isolated_home, f"{digit * 64}.stale.jsonl") for digit in "01"]

        result = _run_installed(script, _append_args(), feature_repo, isolated_home)

        assert result.returncode == 0, result.stderr
        assert all(stale.exists() for stale in stale_files)
        assert any(path.suffix == ".jsonl" and path not in stale_files for path in _ledger_dir(isolated_home).iterdir())
        *warning_lines, sweep_line = result.stderr.splitlines()
        assert sweep_line == _sweep_line(2, _REASON_CONFIG)
        assert warning_lines
        assert all("agent_branch_records" in line for line in warning_lines)


class TestUnavailableArchive:
    def test_key_on_with_only_a_stale_lock_and_no_sentinel_removes_the_lock_without_probing(
        self, tmp_path, isolated_home, feature_repo
    ):
        skip_without_timeout_binary()
        _enable_archive(tmp_path, isolated_home, sentinel=False)
        stale_lock = _plant_stale(isolated_home, "0" * 64 + ".stale.jsonl.lock", "")
        log = tmp_path / "test_calls.log"
        env = real_binary_shim_env(
            tmp_path / "stub-bin", {"test": f'printf \'%s\\n\' "$*" >> {shlex.quote(str(log))}\nexec "$REAL" "$@"'}
        )

        result = _run(_append_args(), cwd=feature_repo, home=isolated_home, extra_env=env, timeout=60)

        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        assert not stale_lock.exists()
        assert not [line for line in _call_log_lines(log) if _SENTINEL_NAME in line]

    def test_clear_stale_keeps_the_file_when_the_sentinel_is_missing(self, tmp_path, isolated_home, feature_repo):
        _enable_archive(tmp_path, isolated_home, sentinel=False)
        stale = _plant_stale(isolated_home, "0" * 64 + ".stale.jsonl")

        result = _run(["clear-stale"], cwd=feature_repo, home=isolated_home, timeout=60)

        assert result.returncode == 0
        assert stale.exists()
        assert result.stdout.splitlines() == [
            f"Archive: unavailable ({_REASON_SENTINEL})",
            f"  keep: {stale.name} ({_REASON_SENTINEL})",
            "clear-stale: evicted 0 file(s)",
        ]
        assert result.stderr == _sweep_line(1, _REASON_SENTINEL) + "\n"

    def test_dry_run_with_a_missing_sentinel_reports_keep_and_prints_no_stderr(
        self, tmp_path, isolated_home, feature_repo
    ):
        _enable_archive(tmp_path, isolated_home, sentinel=False)
        stale = _plant_stale(isolated_home, "0" * 64 + ".stale.jsonl")

        result = _run(["clear-stale", "--dry-run"], cwd=feature_repo, home=isolated_home, timeout=60)

        assert result.returncode == 0
        assert result.stdout.splitlines() == [
            f"Archive: unavailable ({_REASON_SENTINEL})",
            f"  keep (dry-run): {stale.name} ({_REASON_SENTINEL})",
            "clear-stale: would evict 0 file(s)",
        ]
        assert result.stderr == ""
        assert stale.exists()


class TestCopyFailures:
    def test_clear_stale_keeps_the_file_when_a_regular_file_sits_at_the_subdirectory_path(
        self, tmp_path, isolated_home, feature_repo
    ):
        account_dir = _enable_archive(tmp_path, isolated_home)
        account_dir.mkdir(parents=True)
        (account_dir / "review-narrative-ledger").write_text("not a directory")
        stale = _plant_stale(isolated_home, "0" * 64 + ".stale.jsonl")

        result = _run(["clear-stale"], cwd=feature_repo, home=isolated_home, timeout=60)

        assert result.returncode == 0
        assert stale.exists()
        assert result.stdout.splitlines() == [
            f"Archive: ready ({account_dir})",
            f"  keep: {stale.name} ({_REASON_DIRECTORY})",
            "clear-stale: evicted 0 file(s)",
        ]
        assert result.stderr == _sweep_line(1, _REASON_DIRECTORY) + "\n"

    def test_a_stalled_copy_ends_archiving_after_one_cp_call_and_keeps_all_three_files(
        self, tmp_path, isolated_home, feature_repo
    ):
        _enable_archive(tmp_path, isolated_home)
        stale_files = [_plant_stale(isolated_home, f"{digit * 64}.stale.jsonl") for digit in "012"]
        log = tmp_path / "cp_calls.log"
        env = real_binary_shim_env(
            tmp_path / "stub-bin", {"cp": CP_OPERANDS_NOTE + f'printf \'%s\\n\' "$*" >> {shlex.quote(str(log))}\nexit 124'}
        )

        result = _run(_append_args(), cwd=feature_repo, home=isolated_home, extra_env=env, timeout=60)

        assert result.returncode == 0
        assert len(_call_log_lines(log)) == 1
        assert all(stale.exists() for stale in stale_files)
        assert result.stderr == _sweep_line(3, _REASON_STALLED) + "\n"
        assert result.stdout == ""

    @pytest.mark.parametrize("trigger", _TRIGGERS)
    def test_a_failed_copy_keeps_only_its_own_file_and_the_sweep_goes_on(
        self, tmp_path, isolated_home, feature_repo, trigger
    ):
        account_dir = _enable_archive(tmp_path, isolated_home)
        rows_by_file = {}
        for digit in "012":
            rows = f'{{"finding":"stale-{digit}"}}\n'
            rows_by_file[_plant_stale(isolated_home, f"{digit * 64}.stale.jsonl", rows)] = rows
        stamp_by_file = {stale: _utc_stamp(stale.stat().st_mtime) for stale in rows_by_file}
        log = tmp_path / "cp_calls.log"
        env = real_binary_shim_env(
            tmp_path / "stub-bin", {"cp": _cp_shim_failing_first_call(log, 'exec "$REAL" "$@"')}
        )

        result = _run_trigger(trigger, feature_repo, isolated_home, env)

        assert result.returncode == 0
        cp_calls = _call_log_lines(log)
        assert len(cp_calls) == 3
        # Each cp call logs `-- <source> <copy>`, and find's order is not fixed, so the first call names the failing file.
        failing_file = next(stale for stale in rows_by_file if cp_calls[0].split()[-2] == str(stale))
        assert [stale for stale in rows_by_file if stale.exists()] == [failing_file]
        copy_dir = account_dir / "review-narrative-ledger"
        for stale, rows in rows_by_file.items():
            if stale != failing_file:
                assert (copy_dir / f"{stale.stem}.{stamp_by_file[stale]}.jsonl").read_text() == rows
        assert len(_copies(account_dir)) == 2
        assert result.stderr == _sweep_line(1, _REASON_LEDGER_COPY_FAILED) + "\n"
        if trigger == "clear-stale":
            keep_lines = [line for line in result.stdout.splitlines() if line.startswith("  keep:")]
            assert keep_lines == [f"  keep: {failing_file.name} ({_REASON_LEDGER_COPY_FAILED})"]
            assert "clear-stale: evicted 2 file(s)" in result.stdout.splitlines()
        else:
            assert result.stdout == ""

    @pytest.mark.parametrize("trigger", _TRIGGERS)
    def test_the_sweep_line_names_the_first_failing_copys_reason_when_a_later_copy_stalls(
        self, tmp_path, isolated_home, feature_repo, trigger
    ):
        account_dir = _enable_archive(tmp_path, isolated_home)
        stale_files = [_plant_stale(isolated_home, f"{digit * 64}.stale.jsonl") for digit in "012"]
        log = tmp_path / "cp_calls.log"
        env = real_binary_shim_env(tmp_path / "stub-bin", {"cp": _cp_shim_failing_first_call(log, "exit 124")})

        result = _run_trigger(trigger, feature_repo, isolated_home, env)

        assert result.returncode == 0
        # The stall ends archiving, so the third file is kept with the stall reason without a cp call of its own.
        assert len(_call_log_lines(log)) == 2
        assert all(stale.exists() for stale in stale_files)
        assert _copies(account_dir) == []
        assert result.stderr == _sweep_line(3, _REASON_LEDGER_COPY_FAILED) + "\n"
        if trigger == "clear-stale":
            stdout_lines = result.stdout.splitlines()
            keep_lines = [line for line in stdout_lines if line.startswith("  keep:")]
            # Keep lines follow cp call order, and each cp call logs `-- <source> <copy>`.
            first_copied_name = Path(_call_log_lines(log)[0].split()[-2]).name
            assert keep_lines[0] == f"  keep: {first_copied_name} ({_REASON_LEDGER_COPY_FAILED})"
            assert [line.rpartition(" (")[2] for line in keep_lines[1:]] == [f"{_REASON_STALLED})"] * 2
            assert sorted(line.removeprefix("  keep: ").partition(" (")[0] for line in keep_lines) == sorted(
                stale.name for stale in stale_files
            )
            assert stdout_lines[-1] == "clear-stale: evicted 0 file(s)"
        else:
            assert result.stdout == ""


class TestFileChangedDuringTheSweep:
    @pytest.mark.parametrize("trigger", _TRIGGERS)
    @pytest.mark.parametrize(
        ("cp_body", "final_source_text"),
        [
            pytest.param(CP_OPERANDS_NOTE + 'touch -- "$2"\nexec "$REAL" "$@"', _ROWS, id="mtime-moved"),
            pytest.param(
                CP_OPERANDS_NOTE + '"$REAL" "$@"\nprintf \'{"late":1}\\n\' >> "$2"',
                _ROWS + '{"late":1}\n',
                id="row-appended",
            ),
        ],
    )
    def test_file_written_after_find_listed_it_stays_with_its_latest_bytes_and_prints_nothing(
        self, tmp_path, isolated_home, feature_repo, trigger, cp_body, final_source_text
    ):
        account_dir = _enable_archive(tmp_path, isolated_home)
        stale = _plant_stale(isolated_home, "0" * 64 + ".stale.jsonl")
        env = real_binary_shim_env(tmp_path / "stub-bin", {"cp": cp_body})

        result = _run_trigger(trigger, feature_repo, isolated_home, env)

        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        assert stale.read_text() == final_source_text
        assert [copy.read_text() for copy in _copies(account_dir)] == [_ROWS]
        if trigger == "clear-stale":
            assert result.stdout.splitlines() == [f"Archive: ready ({account_dir})", "clear-stale: evicted 0 file(s)"]

    @pytest.mark.parametrize("trigger", _TRIGGERS)
    def test_a_stalled_copy_of_a_file_written_since_find_still_ends_archiving_for_the_later_files(
        self, tmp_path, isolated_home, feature_repo, trigger
    ):
        account_dir = _enable_archive(tmp_path, isolated_home)
        stale_files = [_plant_stale(isolated_home, f"{digit * 64}.stale.jsonl") for digit in "012"]
        log = tmp_path / "cp_calls.log"
        quoted_log = shlex.quote(str(log))
        env = real_binary_shim_env(
            tmp_path / "stub-bin",
            {
                "cp": CP_OPERANDS_NOTE
                + f'printf \'%s\\n\' "$*" >> {quoted_log}\n[ "$(wc -l < {quoted_log})" -eq 1 ] && touch -- "$2"\nexit 124'
            },
        )

        result = _run_trigger(trigger, feature_repo, isolated_home, env)

        assert result.returncode == 0, result.stderr
        cp_calls = _call_log_lines(log)
        assert len(cp_calls) == 1
        # Each cp call logs `-- <source> <copy>`, and find's order is not fixed, so the one call names the touched file.
        touched_file = next(stale for stale in stale_files if cp_calls[0].split()[-2] == str(stale))
        assert all(stale.exists() for stale in stale_files)
        assert _copies(account_dir) == []
        assert result.stderr == _sweep_line(2, _REASON_STALLED) + "\n"
        if trigger == "clear-stale":
            untouched_names = sorted(stale.name for stale in stale_files if stale != touched_file)
            stdout_lines = result.stdout.splitlines()
            assert stdout_lines[0] == f"Archive: ready ({account_dir})"
            assert sorted(stdout_lines[1:-1]) == [f"  keep: {name} ({_REASON_STALLED})" for name in untouched_names]
            assert stdout_lines[-1] == "clear-stale: evicted 0 file(s)"
            assert touched_file.name not in result.stdout
        else:
            assert result.stdout == ""

    @pytest.mark.parametrize("trigger", _TRIGGERS)
    def test_source_removed_by_a_concurrent_sweep_keeps_the_verified_copy_and_prints_nothing(
        self, tmp_path, isolated_home, feature_repo, trigger
    ):
        account_dir = _enable_archive(tmp_path, isolated_home)
        stale = _plant_stale(isolated_home, "0" * 64 + ".stale.jsonl")
        stamp = _utc_stamp(stale.stat().st_mtime)
        verified_copy = account_dir / "review-narrative-ledger" / f"{stale.stem}.{stamp}.jsonl"
        verified_copy.parent.mkdir(parents=True)
        verified_copy.write_text(_ROWS)
        log = tmp_path / "cp_calls.log"
        env = real_binary_shim_env(
            tmp_path / "stub-bin",
            {"cp": CP_OPERANDS_NOTE + f'printf \'%s\\n\' "$*" >> {shlex.quote(str(log))}\nrm -f -- "$2"\nexit 1'},
        )

        result = _run_trigger(trigger, feature_repo, isolated_home, env)

        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        cp_calls = _call_log_lines(log)
        assert len(cp_calls) == 1
        assert cp_calls[0].endswith(f" {verified_copy}")
        assert not stale.exists()
        assert verified_copy.read_text() == _ROWS
        if trigger == "clear-stale":
            assert result.stdout.splitlines() == [f"Archive: ready ({account_dir})", "clear-stale: evicted 0 file(s)"]


class TestInstall:
    def test_a_scripts_directory_without_the_branch_records_lib_exits_2_and_writes_no_ledger(
        self, isolated_home, feature_repo
    ):
        script = install_review_ledger_scripts(isolated_home)
        (script.parent / "_branch-records-lib.sh").unlink()

        result = _run_installed(script, _append_args(), feature_repo, isolated_home)

        assert result.returncode == 2
        assert "_branch-records-lib.sh" in result.stderr and "./install.sh" in result.stderr
        assert not _ledger_dir(isolated_home).exists()
        assert REVIEW_LEDGER_SCRIPT.parent.joinpath("_branch-records-lib.sh").is_file()
