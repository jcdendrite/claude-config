"""Tests for mark-terminal.py.

No real controlling terminal is available in this sandboxed test
environment or in CI, so every test exercises the device write against a
real pseudo-terminal from Python's stdlib `pty.openpty()` instead. Every
test that needs `ps` prepends a stub to PATH that reproduces real BSD ps's
`-o tty=`/`-o lstart=` output shapes (including the space-padding), matching
test_claude_auto.py's PATH-stub pattern -- not a Python-level dependency
injection, since resolve_tty/_ps_lstart's default-bound seam parameters
would not be reachable by monkeypatching the module attribute after import
(the default is bound at function-definition time).

CI runs on ubuntu-24.04 (.github/workflows/tests.yml), not Darwin, so any
test that calls main() end-to-end beyond the platform guard itself
monkeypatches platform.system() to "Darwin" -- every other function here is
called directly and never touches that guard.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pty
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent / "mark-terminal.py"
_spec = importlib.util.spec_from_file_location("mark_terminal", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_PS_STUB_TEMPLATE = """#!/usr/bin/env bash
# Fake ps for tests: dispatches on -o tty=/-o lstart= and -p's pid, using
# canned responses baked in by the test that wrote this stub.
pid=""
mode=""
while [ $# -gt 0 ]; do
  case "$1" in
    -p) pid="$2"; shift 2 ;;
    -o) mode="$2"; shift 2 ;;
    *) shift ;;
  esac
done
case "$mode:$pid" in
{cases}
  *) exit 1 ;;
esac
"""


def _write_ps_stub(bin_dir: Path, responses: dict) -> None:
    """Write a fake `ps` to bin_dir. `responses` keys are "{mode}:{pid}"
    (e.g. "tty=:100", "lstart=:100"); a value of None means ps exits nonzero
    (dead/nonexistent pid); a string value is echoed verbatim to stdout at
    exit 0, unstripped, so a test can reproduce BSD ps's space-padded
    columns.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for key, value in responses.items():
        if value is None:
            lines.append(f'  "{key}") exit 1 ;;')
        else:
            lines.append(f"  \"{key}\") printf %s {shlex.quote(value)}; exit 0 ;;")
    stub = bin_dir / "ps"
    stub.write_text(_PS_STUB_TEMPLATE.format(cases="\n".join(lines)))
    stub.chmod(0o755)


@pytest.fixture
def stub_ps(tmp_path, monkeypatch):
    """Returns a function that (re)writes the `ps` stub with the given
    responses and prepends it to PATH. Callable multiple times per test."""
    bin_dir = tmp_path / "fake-bin"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    def _configure(responses: dict) -> None:
        _write_ps_stub(bin_dir, responses)

    return _configure


def _write_registry_entry(sessions_dir: Path, pid: int, **overrides) -> Path:
    data = {
        "sessionId": "sess-aaa",
        "pid": pid,
        "procStart": "Mon Jan  1 00:00:00 2024",
        "cwd": "/tmp/example-project",
    }
    data.update(overrides)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    path = sessions_dir / f"{pid}.json"
    path.write_text(json.dumps(data))
    return path


# ---------------------------------------------------------------------------
# _sanitize_for_terminal
# ---------------------------------------------------------------------------

class TestSanitizeForTerminal:
    def test_strips_esc_bel_del_and_other_c0_controls(self):
        hostile = "safe\x1b]0;evil\x07text\x00\x01\x7f"
        assert _mod._sanitize_for_terminal(hostile) == "safe]0;eviltext"

    def test_strips_c1_control_range(self):
        """U+0080-U+009F is the 8-bit encoding of the same escape
        introducers (CSI, OSC, ST) that C0/ESC-stripping targets."""
        hostile = "safe" + chr(0x9D) + "0;evil" + chr(0x9C) + "text"
        assert _mod._sanitize_for_terminal(hostile) == "safe0;eviltext"

    def test_strips_bidi_override_and_zero_width_characters(self):
        """Unicode Format-category (Cf) characters -- bidi overrides and
        zero-width characters -- can otherwise still reach rendered output
        and produce a visually misleading title."""
        hostile = "safe" + chr(0x202E) + "evil" + chr(0x200B) + "text" + chr(0xFEFF)
        assert _mod._sanitize_for_terminal(hostile) == "safeeviltext"

    def test_variation_selector_passes_through_unstripped(self):
        """Pins the sanitizer's actual charter: escape/control-injection
        prevention, not full invisible-character moderation. A variation
        selector is zero-width but category Mn (not Cf) -- it carries no
        escape-sequence risk, so it is deliberately not stripped."""
        value = "safe" + chr(0xFE0F) + "text"
        assert _mod._sanitize_for_terminal(value) == value

    def test_passes_through_ordinary_text(self):
        assert _mod._sanitize_for_terminal("my-project") == "my-project"

    def test_non_string_degrades_to_none(self):
        assert _mod._sanitize_for_terminal(None) is None
        assert _mod._sanitize_for_terminal(42) is None
        assert _mod._sanitize_for_terminal(["a"]) is None


# ---------------------------------------------------------------------------
# _positive_pid
# ---------------------------------------------------------------------------

class TestPositivePid:
    def test_accepts_positive_integer_string(self):
        assert _mod._positive_pid("123") == 123

    @pytest.mark.parametrize("raw", ["0", "-5", "abc", "12.5", ""])
    def test_rejects_non_positive_or_non_numeric(self, raw):
        with pytest.raises(argparse.ArgumentTypeError):
            _mod._positive_pid(raw)

    def test_bad_pid_is_rejected_before_ps_is_ever_invoked(self, tmp_path, monkeypatch):
        """A non-positive/non-numeric pid errors at argparse -- ps is never
        shelled out to reach it."""
        bin_dir = tmp_path / "fake-bin"
        bin_dir.mkdir()
        poison = bin_dir / "ps"
        poison.write_text("#!/usr/bin/env bash\necho unexpected-ps-invocation >&2\nexit 1\n")
        poison.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setattr(_mod.platform, "system", lambda: "Darwin")

        with pytest.raises(SystemExit):
            _mod.main(["-5"])


# ---------------------------------------------------------------------------
# resolve_tty
# ---------------------------------------------------------------------------

class TestResolveTty:
    def test_strips_bsd_space_padded_tty_column(self, stub_ps):
        stub_ps({"tty=:100": "ttys015  \n"})
        assert _mod.resolve_tty(100) == "ttys015"

    def test_no_controlling_terminal_raises(self, stub_ps):
        stub_ps({"tty=:100": "??      \n"})
        with pytest.raises(ValueError, match="no controlling terminal"):
            _mod.resolve_tty(100)

    def test_nonexistent_pid_raises(self, stub_ps):
        stub_ps({"tty=:100": None})
        with pytest.raises(ValueError, match="no such process"):
            _mod.resolve_tty(100)

    def test_rejects_tty_name_containing_path_separators(self, stub_ps):
        """A compromised ps on PATH returning a traversal-shaped value must
        not reach Path("/dev") / tty in main() -- reject anything but a
        bare alphanumeric device name."""
        stub_ps({"tty=:100": "../../../../tmp/pwned\n"})
        with pytest.raises(ValueError, match="unexpected tty name"):
            _mod.resolve_tty(100)

    def test_missing_ps_binary_raises_clear_error(self, tmp_path, monkeypatch):
        empty_bin = tmp_path / "empty-bin"
        empty_bin.mkdir()
        monkeypatch.setenv("PATH", str(empty_bin))
        with pytest.raises(ValueError, match="ps not found"):
            _mod.resolve_tty(100)

    def test_hanging_ps_raises_clear_error_instead_of_blocking(self):
        """A pure fake run= raising TimeoutExpired directly -- no real
        subprocess spawned, no wall-clock wait -- mirrors
        test_post_crash_sessions.py's test_find_scheduled_task_locks_reports_timeout_without_raising.
        A real sleep-based subprocess double was tried and rejected: killing
        the direct child on timeout doesn't reap its own un-exec'd children,
        leaking an orphaned process that outlives the test."""
        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))
        with pytest.raises(ValueError, match="timed out"):
            _mod.resolve_tty(100, run=fake_run)


# ---------------------------------------------------------------------------
# _same_process (pure -- no ps needed)
# ---------------------------------------------------------------------------

class TestSameProcess:
    def test_matching_timestamps_are_same(self):
        assert _mod._same_process("Mon Jan  1 00:00:00 2024", "Mon Jan  1 00:00:00 2024") is True

    def test_at_tolerance_boundary_is_same(self):
        assert _mod._same_process("Mon Jan  1 00:00:00 2024", "Mon Jan  1 00:00:02 2024") is True

    def test_beyond_tolerance_boundary_is_different(self):
        assert _mod._same_process("Mon Jan  1 00:00:00 2024", "Mon Jan  1 00:00:03 2024") is False

    def test_missing_stored_side_is_indeterminate(self):
        assert _mod._same_process(None, "Mon Jan  1 00:00:00 2024") is None

    def test_missing_live_side_is_indeterminate(self):
        assert _mod._same_process("Mon Jan  1 00:00:00 2024", None) is None

    def test_non_string_stored_side_is_indeterminate(self):
        assert _mod._same_process(1704067200000, "Mon Jan  1 00:00:00 2024") is None

    def test_unparseable_stored_side_is_indeterminate(self):
        assert _mod._same_process("not a timestamp", "Mon Jan  1 00:00:00 2024") is None

    def test_unparseable_live_side_is_indeterminate(self):
        assert _mod._same_process("Mon Jan  1 00:00:00 2024", "not a timestamp") is None


class TestPsLstart:
    """_ps_lstart degrades to None on failure -- it never raises, matching
    its "empty stdout means dead" contract (post-crash-sessions.py's own
    _ps_lstart follows the same shape)."""

    def test_missing_ps_binary_degrades_to_none(self, tmp_path, monkeypatch):
        empty_bin = tmp_path / "empty-bin"
        empty_bin.mkdir()
        monkeypatch.setenv("PATH", str(empty_bin))
        assert _mod._ps_lstart(100) is None

    def test_hanging_ps_degrades_to_none_instead_of_blocking(self):
        """Pure fake run=, no real subprocess -- see the rationale on
        TestResolveTty's equivalent test."""
        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))
        assert _mod._ps_lstart(100, run=fake_run) is None

    def test_forces_utc_c_locale_env_on_the_ps_call(self):
        """_ps_lstart's TZ=UTC/LC_ALL=C forcing is what makes ps's weekday/
        month output match _LSTART_FORMAT regardless of the invoking user's
        locale -- a dropped or typo'd key here would silently make every
        registry entry look stale on a non-C-locale machine, and no
        PATH-stub test can catch that since the stub never reads env."""
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["env"] = kwargs.get("env")
            return subprocess.CompletedProcess(cmd, 0, "Mon Jan  1 00:00:00 2024\n", "")

        _mod._ps_lstart(100, run=fake_run)
        assert captured["env"]["TZ"] == "UTC"
        assert captured["env"]["LC_ALL"] == "C"


# ---------------------------------------------------------------------------
# build_title / _read_registry_entry (staleness, schema drift)
# ---------------------------------------------------------------------------

class TestBuildTitle:
    def test_explicit_title_wins_outright(self, tmp_path, stub_ps):
        stub_ps({})
        assert _mod.build_title(100, [tmp_path], emoji="📍", explicit_title="my title") == "my title"

    def test_registry_hit_formats_title(self, tmp_path, stub_ps):
        stub_ps({"lstart=:100": "Mon Jan  1 00:00:00 2024"})
        _write_registry_entry(tmp_path / "sessions", 100, cwd="/tmp/my-project")
        assert _mod.build_title(100, [tmp_path], emoji="📍", explicit_title=None) == "📍 my-project (100)"

    def test_registry_miss_falls_back_to_bare_pid(self, tmp_path, stub_ps):
        stub_ps({"lstart=:100": None})  # no sessions dir at all -> no entry
        assert _mod.build_title(100, [tmp_path], emoji="📍", explicit_title=None) == "PID 100"

    def test_malformed_json_degrades_to_miss(self, tmp_path):
        # No ps stub configured: JSON parsing fails before _read_registry_entry
        # ever calls ps_lstart.
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        (sessions_dir / "100.json").write_text("not json{{{")
        assert _mod.build_title(100, [tmp_path], emoji="📍", explicit_title=None) == "PID 100"

    def test_non_dict_payload_degrades_to_miss(self, tmp_path):
        # No ps stub configured: the non-dict check fails before
        # _read_registry_entry ever calls ps_lstart.
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        (sessions_dir / "100.json").write_text(json.dumps(["unexpected", "shape"]))
        assert _mod.build_title(100, [tmp_path], emoji="📍", explicit_title=None) == "PID 100"

    @pytest.mark.parametrize("bad_cwd", [42, ["a", "b"], None])
    def test_non_string_cwd_degrades_to_miss(self, tmp_path, stub_ps, bad_cwd):
        stub_ps({"lstart=:100": "Mon Jan  1 00:00:00 2024"})
        _write_registry_entry(tmp_path / "sessions", 100, cwd=bad_cwd)
        assert _mod.build_title(100, [tmp_path], emoji="📍", explicit_title=None) == "PID 100"

    def test_stale_entry_pid_recycled_is_treated_as_miss(self, tmp_path, stub_ps):
        """procStart doesn't match the live process's actual start time --
        this pid was recycled since the registry entry was written."""
        stub_ps({"lstart=:100": "Mon Jan  1 00:10:00 2024"})
        _write_registry_entry(tmp_path / "sessions", 100, procStart="Mon Jan  1 00:00:00 2024")
        assert _mod.build_title(100, [tmp_path], emoji="📍", explicit_title=None) == "PID 100"

    def test_same_pid_under_two_config_dirs_resolves_by_matching_procstart(self, tmp_path, stub_ps):
        """Not first-match-wins: only the config dir whose procStart matches
        the live process's actual start time is used, even if it's second."""
        stub_ps({"lstart=:100": "Mon Jan  1 00:00:00 2024"})
        stale_dir = tmp_path / "stale-account"
        live_dir = tmp_path / "live-account"
        _write_registry_entry(stale_dir / "sessions", 100, cwd="/tmp/stale-project", procStart="Mon Jan  1 09:00:00 2024")
        _write_registry_entry(live_dir / "sessions", 100, cwd="/tmp/live-project", procStart="Mon Jan  1 00:00:00 2024")
        title = _mod.build_title(100, [stale_dir, live_dir], emoji="📍", explicit_title=None)
        assert title == "📍 live-project (100)"

    def test_emoji_is_sanitized(self, tmp_path, stub_ps):
        stub_ps({"lstart=:100": "Mon Jan  1 00:00:00 2024"})
        _write_registry_entry(tmp_path / "sessions", 100, cwd="/tmp/my-project")
        title = _mod.build_title(100, [tmp_path], emoji="\x1b]0;evil\x07📍", explicit_title=None)
        assert "\x1b" not in title and "\x07" not in title
        assert title == "]0;evil📍 my-project (100)"


# ---------------------------------------------------------------------------
# write_title -- real pty, no controlling terminal needed
# ---------------------------------------------------------------------------

class TestWriteTitle:
    def test_writes_osc_sequence_for_a_legitimate_title(self):
        master_fd, slave_fd = pty.openpty()
        try:
            _mod.write_title(Path(os.ttyname(slave_fd)), "my-project (100)")
            written = os.read(master_fd, 4096)
            assert written == b"\033]0;my-project (100)\007"
        finally:
            os.close(master_fd)
            os.close(slave_fd)

    @pytest.mark.parametrize("source", ["registry_cwd", "explicit_title", "emoji"])
    def test_embedded_osc_sequence_is_neutralized_to_exactly_one(self, tmp_path, stub_ps, source):
        hostile = "\x1b]0;evil\x07"
        if source == "registry_cwd":
            stub_ps({"lstart=:100": "Mon Jan  1 00:00:00 2024"})
            _write_registry_entry(tmp_path / "sessions", 100, cwd=f"/tmp/{hostile}project")
            title = _mod.build_title(100, [tmp_path], emoji="📍", explicit_title=None)
        elif source == "explicit_title":
            title = _mod.build_title(100, [tmp_path], emoji="📍", explicit_title=hostile)
        else:
            stub_ps({"lstart=:100": "Mon Jan  1 00:00:00 2024"})
            _write_registry_entry(tmp_path / "sessions", 100, cwd="/tmp/my-project")
            title = _mod.build_title(100, [tmp_path], emoji=hostile, explicit_title=None)

        master_fd, slave_fd = pty.openpty()
        try:
            _mod.write_title(Path(os.ttyname(slave_fd)), title)
            written = os.read(master_fd, 4096)
        finally:
            os.close(master_fd)
            os.close(slave_fd)

        assert written.count(b"\033]0;") == 1
        assert written.count(b"\007") == 1
        assert written == f"\033]0;{title}\007".encode()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_permission_denied_write_raises_clear_error(self):
        master_fd, slave_fd = pty.openpty()
        device_path = Path(os.ttyname(slave_fd))
        try:
            os.chmod(device_path, 0o000)
            with pytest.raises(ValueError, match="(?i)permission denied"):
                _mod.write_title(device_path, "some title")
        finally:
            os.chmod(device_path, 0o620)
            os.close(master_fd)
            os.close(slave_fd)

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_toctou_backstop_triggers_when_os_access_reports_writable_but_open_fails(self, monkeypatch):
        """Exercises the try/except PermissionError backstop independently
        of the leading os.access() check, by forcing os.access() to report
        writable while the underlying open() still raises."""
        monkeypatch.setattr(_mod.os, "access", lambda *args, **kwargs: True)
        master_fd, slave_fd = pty.openpty()
        device_path = Path(os.ttyname(slave_fd))
        try:
            os.chmod(device_path, 0o000)
            with pytest.raises(ValueError, match="(?i)permission denied"):
                _mod.write_title(device_path, "some title")
        finally:
            os.chmod(device_path, 0o620)
            os.close(master_fd)
            os.close(slave_fd)


# ---------------------------------------------------------------------------
# --list
# ---------------------------------------------------------------------------

class TestRunList:
    def test_dead_pid_excluded(self, tmp_path, stub_ps, capsys):
        stub_ps({"lstart=:100": None})
        _write_registry_entry(tmp_path / "sessions", 100)
        exit_code = _mod._run_list([tmp_path])
        assert exit_code == 0
        assert "100" not in capsys.readouterr().out

    def test_stale_pid_recycled_excluded_even_though_alive(self, tmp_path, stub_ps, capsys):
        stub_ps({"lstart=:100": "Mon Jan  1 09:00:00 2024", "tty=:100": "ttys015  \n"})
        _write_registry_entry(tmp_path / "sessions", 100, procStart="Mon Jan  1 00:00:00 2024")
        exit_code = _mod._run_list([tmp_path])
        assert exit_code == 0
        assert "100" not in capsys.readouterr().out

    def test_genuinely_live_entry_resolves_tty(self, tmp_path, stub_ps, capsys):
        stub_ps({"lstart=:100": "Mon Jan  1 00:00:00 2024", "tty=:100": "ttys015  \n"})
        _write_registry_entry(tmp_path / "sessions", 100, cwd="/tmp/my-project")
        exit_code = _mod._run_list([tmp_path])
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "100" in out
        assert "ttys015" in out
        assert "/tmp/my-project" in out

    def test_cwd_with_control_characters_prints_sanitized(self, tmp_path, stub_ps, capsys):
        stub_ps({"lstart=:100": "Mon Jan  1 00:00:00 2024", "tty=:100": "ttys015  \n"})
        _write_registry_entry(tmp_path / "sessions", 100, cwd="/tmp/\x1b]0;evil\x07project")
        exit_code = _mod._run_list([tmp_path])
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "\x1b" not in out and "\x07" not in out
        assert "/tmp/]0;evilproject" in out

    def test_no_live_sessions_prints_friendly_message(self, tmp_path, capsys):
        exit_code = _mod._run_list([tmp_path])
        assert exit_code == 0
        assert "no live sessions" in capsys.readouterr().out

    def test_column_widths_align_across_mixed_digit_pids(self, tmp_path, stub_ps, capsys):
        stub_ps({
            "lstart=:9": "Mon Jan  1 00:00:00 2024", "tty=:9": "ttys001  \n",
            "lstart=:100000": "Mon Jan  1 00:00:00 2024", "tty=:100000": "ttys015  \n",
        })
        _write_registry_entry(tmp_path / "sessions", 9, cwd="/tmp/short-pid")
        _write_registry_entry(tmp_path / "sessions", 100000, cwd="/tmp/long-pid")
        exit_code = _mod._run_list([tmp_path])
        lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
        assert exit_code == 0
        header = lines[0]
        # Rows sort lexically by filename ("100000.json" < "9.json"), not
        # numerically by pid -- pinned here as current behavior, not a
        # promised ordering contract.
        assert "100000" in lines[1] and "short-pid" in lines[2]
        row_9 = next(line for line in lines if "short-pid" in line)
        row_100000 = next(line for line in lines if "long-pid" in line)
        # Every row's TTY/CWD columns start at the same offset once the PID
        # column widens to fit the longer pid.
        assert row_9.index("ttys001") == row_100000.index("ttys015")
        assert header.index("TTY") == row_9.index("ttys001")

    def test_same_live_pid_under_two_config_dirs_renders_as_two_rows(self, tmp_path, stub_ps, capsys):
        """Pins current (undeduped) behavior: unlike build_title's
        first-match-wins, _run_list has no cross-dir dedup."""
        stub_ps({"lstart=:100": "Mon Jan  1 00:00:00 2024", "tty=:100": "ttys015  \n"})
        account_a = tmp_path / "account-a"
        account_b = tmp_path / "account-b"
        _write_registry_entry(account_a / "sessions", 100, cwd="/tmp/project-a")
        _write_registry_entry(account_b / "sessions", 100, cwd="/tmp/project-b")
        exit_code = _mod._run_list([account_a, account_b])
        out = capsys.readouterr().out
        assert exit_code == 0
        assert out.count("100") == 2
        assert "/tmp/project-a" in out
        assert "/tmp/project-b" in out

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_sessions_dir_is_excluded_not_raised(self, tmp_path, capsys):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        try:
            sessions_dir.chmod(0o000)
            exit_code = _mod._run_list([tmp_path])
        finally:
            sessions_dir.chmod(0o755)
        assert exit_code == 0
        assert "no live sessions" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# resolve_config_dirs
# ---------------------------------------------------------------------------

class TestResolveConfigDirs:
    def test_default_scans_active_profile_and_declared_roots(self, tmp_path, monkeypatch):
        default_dir = tmp_path / "default-config"
        default_dir.mkdir()
        declared_dir = tmp_path / "declared-config"
        (declared_dir / "sessions").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{declared_dir}\n")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(default_dir))
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        config_dirs = _mod.resolve_config_dirs(None, tool_name="mark-terminal")
        assert config_dirs == [default_dir, declared_dir]

    def test_explicit_config_dir_overrides_declared_roots_default(self, tmp_path, monkeypatch):
        default_dir = tmp_path / "default-config"
        default_dir.mkdir()
        declared_dir = tmp_path / "declared-config"
        (declared_dir / "sessions").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{declared_dir}\n")
        explicit_dir = tmp_path / "explicit-config"
        (explicit_dir / "sessions").mkdir(parents=True)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(default_dir))
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        config_dirs = _mod.resolve_config_dirs([str(explicit_dir)], tool_name="mark-terminal")
        assert config_dirs == [default_dir, explicit_dir]

    def test_dedupes_default_config_dir_supplied_again_explicitly(self, tmp_path, monkeypatch):
        default_dir = tmp_path / "default-config"
        (default_dir / "sessions").mkdir(parents=True)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(default_dir))

        config_dirs = _mod.resolve_config_dirs([str(default_dir)], tool_name="mark-terminal")
        assert config_dirs == [default_dir]

    def test_rejects_nonexistent_config_dir(self, tmp_path, monkeypatch):
        default_dir = tmp_path / "default-config"
        default_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(default_dir))
        missing = tmp_path / "does-not-exist"

        with pytest.raises(ValueError, match="is not a directory"):
            _mod.resolve_config_dirs([str(missing)], tool_name="mark-terminal")

    def test_rejects_config_dir_with_no_sessions_or_projects_subdir(self, tmp_path, monkeypatch):
        default_dir = tmp_path / "default-config"
        default_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(default_dir))
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        with pytest.raises(ValueError, match="no sessions/ or projects/ subdirectory"):
            _mod.resolve_config_dirs([str(empty_dir)], tool_name="mark-terminal")


# ---------------------------------------------------------------------------
# Platform guard
# ---------------------------------------------------------------------------

class TestPlatformGuard:
    def test_non_darwin_exits_loudly_without_touching_anything_else(self, monkeypatch, capsys):
        monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
        exit_code = _mod.main(["100"])
        assert exit_code == 2
        assert "macOS/BSD only" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# main() CLI wiring (platform.system() forced to Darwin to get past the guard)
# ---------------------------------------------------------------------------

class TestMainCliWiring:
    def test_pid_required_unless_list(self, tmp_path, monkeypatch, capsys):
        default_dir = tmp_path / "default-config"
        default_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(default_dir))
        monkeypatch.setattr(_mod.platform, "system", lambda: "Darwin")

        with pytest.raises(SystemExit) as exc_info:
            _mod.main([])
        assert exc_info.value.code == 2
        assert "pid is required" in capsys.readouterr().err

    def test_titles_the_resolved_device(self, tmp_path, monkeypatch, stub_ps, capsys):
        """Spies on write_title rather than performing a real device write --
        main()'s Path("/dev") / tty construction assumes BSD's flat
        /dev/ttysNNN naming (this script is Darwin-only in real use), which
        doesn't hold for a real pty's /dev/pts/N path on the Linux CI runner
        this test suite also runs on."""
        default_dir = tmp_path / "default-config"
        default_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(default_dir))
        monkeypatch.setattr(_mod.platform, "system", lambda: "Darwin")
        stub_ps({"tty=:100": "ttys015  \n"})

        captured = {}

        def _fake_write_title(device_path, title):
            captured["device_path"] = device_path
            captured["title"] = title

        monkeypatch.setattr(_mod, "write_title", _fake_write_title)

        exit_code = _mod.main(["100", "--title", "explicit title"])

        assert exit_code == 0
        assert captured == {"device_path": Path("/dev/ttys015"), "title": "explicit title"}
        assert "explicit title" in capsys.readouterr().out

    def test_list_flag_dispatches_to_run_list(self, tmp_path, monkeypatch, stub_ps, capsys):
        default_dir = tmp_path / "default-config"
        default_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(default_dir))
        monkeypatch.setattr(_mod.platform, "system", lambda: "Darwin")
        stub_ps({"lstart=:100": "Mon Jan  1 00:00:00 2024", "tty=:100": "ttys015  \n"})
        _write_registry_entry(default_dir / "sessions", 100, cwd="/tmp/my-project")

        exit_code = _mod.main(["--list"])

        assert exit_code == 0
        assert "/tmp/my-project" in capsys.readouterr().out

    def test_resolve_tty_failure_exits_1(self, tmp_path, monkeypatch, stub_ps, capsys):
        default_dir = tmp_path / "default-config"
        default_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(default_dir))
        monkeypatch.setattr(_mod.platform, "system", lambda: "Darwin")
        stub_ps({"tty=:100": None})

        exit_code = _mod.main(["100"])

        assert exit_code == 1
        assert "no such process" in capsys.readouterr().err

    def test_write_title_failure_exits_1(self, tmp_path, monkeypatch, stub_ps, capsys):
        default_dir = tmp_path / "default-config"
        default_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(default_dir))
        monkeypatch.setattr(_mod.platform, "system", lambda: "Darwin")
        stub_ps({"tty=:100": "ttys015  \n"})

        def _fake_write_title(device_path, title):
            raise ValueError(f"cannot write to {device_path}: permission denied")

        monkeypatch.setattr(_mod, "write_title", _fake_write_title)

        exit_code = _mod.main(["100", "--title", "x"])

        assert exit_code == 1
        assert "permission denied" in capsys.readouterr().err

    def test_invalid_config_dir_exits_2(self, tmp_path, monkeypatch, capsys):
        default_dir = tmp_path / "default-config"
        default_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(default_dir))
        monkeypatch.setattr(_mod.platform, "system", lambda: "Darwin")
        missing = tmp_path / "does-not-exist"

        exit_code = _mod.main(["100", "--config-dir", str(missing)])

        assert exit_code == 2
        assert "is not a directory" in capsys.readouterr().err
