"""Tests for nudge-memory-store-audit.sh.

SessionStart hook (matcher startup only) that measures the total byte size
of every auto-memory store under <config-dir>/projects/*/memory and emits a
hookSpecificOutput.additionalContext advisory once the total crosses
MEMORY_AUDIT_NUDGE_PER_PROJECT_BYTES (default 25600) times the number of
project stores holding any memory content -- a count-scaled threshold, not a
fixed one. Re-arms at MEMORY_AUDIT_NUDGE_REARM_BYTES (default 25600) past the
byte total recorded at the last fire. See docs/memory-audit-nudge.md for the
threshold derivation.

All tests sandbox $HOME (and clear CLAUDE_CONFIG_DIR) so state/log files land
under a temp directory rather than the real ~/.claude.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, assert_cap_engaged, build_path_without

from .conftest import _real_timeout_is_gnu_coreutils, _write_conditional_sleep_shim

NUDGE_HOOK = HOOKS_DIR / "nudge-memory-store-audit.sh"

# Mirrors the hook's own shipped defaults.
DEFAULT_PER_PROJECT_BYTES = 25600
DEFAULT_REARM_BYTES = 25600

# Obviously-synthetic project-name prefix, matching
# test_deny_private_project_refs.py's convention: the fixture names in this
# file must never be readable as a real project on this machine.
SYNTHETIC_PROJECT_PREFIX = "fakeproj"

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _config_dir(home: Path) -> Path:
    return home / ".claude"


def _memory_dir(home: Path, project: str) -> Path:
    d = _config_dir(home) / "projects" / project / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_memory_file(home: Path, project: str, filename: str, size_bytes: int) -> Path:
    """Create a memory file of exactly size_bytes under
    <config-dir>/projects/<project>/memory/<filename>."""
    path = _memory_dir(home, project) / filename
    path.write_bytes(b"x" * size_bytes)
    return path


def _run_hook(payload: dict, home: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(NUDGE_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


@pytest.fixture(autouse=True)
def _clear_memory_audit_nudge_override_env(monkeypatch):
    """The hook's two documented override knobs pass through _run_hook's
    `{**os.environ, ...}` env dict unchanged from the ambient environment.
    Without this, a real exported MEMORY_AUDIT_NUDGE_PER_PROJECT_BYTES or
    MEMORY_AUDIT_NUDGE_REARM_BYTES value could silently satisfy a test that
    never sets it explicitly, mirroring conftest.py's _clear_claude_pid_env
    fixture for the same category of bug."""
    monkeypatch.delenv("MEMORY_AUDIT_NUDGE_PER_PROJECT_BYTES", raising=False)
    monkeypatch.delenv("MEMORY_AUDIT_NUDGE_REARM_BYTES", raising=False)


def _base_payload(source: str = "startup") -> dict:
    return {"source": source}


def _isolated_hooks_dir(tmp_path: Path) -> Path:
    """Symlink the nudge hook plus its _lib.sh/_config.sh dependencies into
    a directory with no config-keys.psv sibling, so _config_schema_field sees
    an absent (unreadable) schema and _config_enabled memory_audit_nudge
    returns exit 3. Mirrors
    test_nudge_handoff_near_context_cap.py's identical-purpose helper.
    Returns the isolated hook's own path."""
    isolated = tmp_path / "isolated-hooks"
    isolated.mkdir()
    (isolated / NUDGE_HOOK.name).symlink_to(NUDGE_HOOK)
    (isolated / "_lib.sh").symlink_to(HOOKS_DIR / "_lib.sh")
    (isolated / "_config.sh").symlink_to(HOOKS_DIR / "_config.sh")
    (isolated / "nudge-memory-store-audit.awk").symlink_to(WC_TOTAL_ROW_AWK_PROGRAM)
    return isolated / NUDGE_HOOK.name


def _state_file(home: Path) -> Path:
    return _config_dir(home) / ".memory-audit-nudge-fired"


def _log_path(home: Path) -> Path:
    return _config_dir(home) / ".memory-audit-nudge.log"


def _parse_log_line(text: str) -> dict:
    """Split a `nudged key=value ...` log line into a field dict."""
    return dict(token.split("=", 1) for token in text.strip().split() if "=" in token)


# The wc-total-row-exclusion and project-store-count awk program lives in
# its own sidecar file, invoked directly via `awk -f` below so the tests run
# the exact program the hook runs, rather than a copy extracted from it.
WC_TOTAL_ROW_AWK_PROGRAM = HOOKS_DIR / "nudge-memory-store-audit.awk"


def _write_shim(shim_dir: Path, name: str, body: str) -> None:
    """Write an executable `name` shim into shim_dir (created if absent)."""
    shim_dir.mkdir(exist_ok=True)
    shim = shim_dir / name
    shim.write_text(f"#!/bin/bash\n{body}\n")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)


def _write_state_toml(home: Path, content: str) -> None:
    """Write <config-dir>/claude-config.toml with the given content."""
    _config_dir(home).mkdir(parents=True, exist_ok=True)
    (_config_dir(home) / "claude-config.toml").write_text(content)


def _path_with_shims(shim_dir: Path) -> str:
    return f"{shim_dir}{os.pathsep}{os.environ['PATH']}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNudgeMemoryStoreAudit:
    # -- Threshold and scaling -------------------------------------------

    @pytest.mark.parametrize(
        "size_bytes,expect_fire",
        [
            (DEFAULT_PER_PROJECT_BYTES - 1, False),
            (DEFAULT_PER_PROJECT_BYTES, True),
        ],
    )
    def test_threshold_boundary(self, tmp_path, size_bytes, expect_fire):
        """N-1/N adjacent pair at the single-store threshold (N=1 project,
        threshold=25600): one byte below stays silent, one byte at it fires."""
        _write_memory_file(tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-boundary", "MEMORY.md", size_bytes)
        result = _run_hook(_base_payload(), tmp_path)
        assert result.returncode == 0
        if expect_fire:
            assert result.stdout.strip() != ""
        else:
            assert result.stdout.strip() == ""

    def test_count_scaling_fires_at_n_silent_at_n_plus_one(self, tmp_path):
        """The same total byte count (51200) fires when scaled to N=2 project
        stores (threshold=51200) and stays silent at N=3 (threshold=76800) --
        pins the count-scaled rule rather than a fixed byte constant."""
        home_n2 = tmp_path / "home-n2"
        home_n2.mkdir()
        for i in range(2):
            _write_memory_file(
                home_n2, f"{SYNTHETIC_PROJECT_PREFIX}-scale-{i}", "MEMORY.md", DEFAULT_PER_PROJECT_BYTES
            )
        result_n2 = _run_hook(_base_payload(), home_n2)
        assert result_n2.returncode == 0
        assert result_n2.stdout.strip() != ""

        home_n3 = tmp_path / "home-n3"
        home_n3.mkdir()
        # Same 51200-byte total, spread so the third store still counts
        # toward N (at least one file) without pushing the total higher.
        _write_memory_file(home_n3, f"{SYNTHETIC_PROJECT_PREFIX}-scale-0", "MEMORY.md", DEFAULT_PER_PROJECT_BYTES)
        _write_memory_file(home_n3, f"{SYNTHETIC_PROJECT_PREFIX}-scale-1", "MEMORY.md", DEFAULT_PER_PROJECT_BYTES)
        _write_memory_file(home_n3, f"{SYNTHETIC_PROJECT_PREFIX}-scale-2", "MEMORY.md", 0)
        result_n3 = _run_hook(_base_payload(), home_n3)
        assert result_n3.returncode == 0
        assert result_n3.stdout.strip() == ""

    # -- Kill-switch and source filter -------------------------------------

    def test_kill_switch_suppresses_before_scan(self, tmp_path):
        """The kill-switch check precedes the scan: no stdout, no log line,
        and no state file, even with a store well past threshold."""
        _config_dir(tmp_path).mkdir(parents=True, exist_ok=True)
        (_config_dir(tmp_path) / ".memory-audit-nudge-disabled").touch()
        _write_memory_file(
            tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-killswitch", "MEMORY.md", DEFAULT_PER_PROJECT_BYTES * 5
        )
        result = _run_hook(_base_payload(), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert not _log_path(tmp_path).exists()
        assert not _state_file(tmp_path).exists()

    def test_unreadable_config_keys_psv_keeps_nudge_enabled(self, tmp_path):
        """The kill-switch check's own fail direction (comment above its
        _config_enabled call): exit 3 (config-keys.psv unreadable) falls
        through the same `case` as exit 2, leaving the nudge enabled rather
        than silently suppressed. A schema-unreadable hook that stayed
        silent here would be indistinguishable from a correctly-suppressed
        one without this test."""
        isolated_hook = _isolated_hooks_dir(tmp_path)
        _write_memory_file(
            tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-unreadable-schema", "MEMORY.md", DEFAULT_PER_PROJECT_BYTES * 3
        )
        env = {**os.environ, "HOME": str(tmp_path)}
        env.pop("CLAUDE_CONFIG_DIR", None)
        result = subprocess.run(
            [str(isolated_hook)],
            input=json.dumps(_base_payload()),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0
        assert result.stdout.strip() != "", "schema-unreadable must not suppress the nudge"

    @pytest.mark.parametrize(
        "corrupted_row",
        [None, "memory_audit_nudge|bool"],
        ids=["row-absent", "row-truncated"],
    )
    def test_corrupted_schema_row_keeps_nudge_enabled(self, tmp_path, corrupted_row):
        """A config-keys.psv with the memory_audit_nudge row absent or
        truncated to two fields is a schema-read failure, which leaves the
        nudge enabled -- even beside a legacy kill-switch file."""
        isolated_hook = _isolated_hooks_dir(tmp_path)
        kept_rows = [
            line
            for line in (HOOKS_DIR / "config-keys.psv").read_text().splitlines()
            if not line.startswith("memory_audit_nudge|")
        ]
        if corrupted_row is not None:
            kept_rows.append(corrupted_row)
        (isolated_hook.parent / "config-keys.psv").write_text("\n".join(kept_rows) + "\n")
        _config_dir(tmp_path).mkdir(parents=True, exist_ok=True)
        (_config_dir(tmp_path) / ".memory-audit-nudge-disabled").touch()
        _write_memory_file(
            tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-corrupt-schema", "MEMORY.md", DEFAULT_PER_PROJECT_BYTES * 3
        )
        env = {**os.environ, "HOME": str(tmp_path)}
        env.pop("CLAUDE_CONFIG_DIR", None)
        result = subprocess.run(
            [str(isolated_hook)],
            input=json.dumps(_base_payload()),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0
        assert result.stdout.strip() != "", "a corrupted schema row must not suppress the nudge"

    def test_toml_false_suppresses_before_the_scan_runs(self, tmp_path):
        """memory_audit_nudge = false suppresses before any scan: a recording
        find shim on PATH is never invoked, so this cannot pass for a check
        that ran after the scan."""
        _write_state_toml(tmp_path, "memory_audit_nudge = false\n")
        _write_memory_file(
            tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-toml-false", "MEMORY.md", DEFAULT_PER_PROJECT_BYTES * 5
        )
        invocation_record = tmp_path / "find-invocations"
        shim_dir = tmp_path / "shims"
        _write_shim(shim_dir, "find", f'echo invoked >> "{invocation_record}"\nexit 0')
        result = _run_hook(_base_payload(), tmp_path, extra_env={"PATH": _path_with_shims(shim_dir)})
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert not invocation_record.exists()
        assert not _log_path(tmp_path).exists()
        assert not _state_file(tmp_path).exists()

    def test_find_shim_records_invocation_when_enabled(self, tmp_path):
        """Control for the recording shim above: with the nudge enabled the
        shim is invoked, so the never-invoked assertion is meaningful."""
        _write_memory_file(
            tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-shim-control", "MEMORY.md", DEFAULT_PER_PROJECT_BYTES
        )
        invocation_record = tmp_path / "find-invocations"
        shim_dir = tmp_path / "shims"
        _write_shim(shim_dir, "find", f'echo invoked >> "{invocation_record}"\nexit 0')
        _run_hook(_base_payload(), tmp_path, extra_env={"PATH": _path_with_shims(shim_dir)})
        assert invocation_record.exists()

    def test_explicit_true_row_beats_legacy_disabled_file(self, tmp_path):
        """An explicit memory_audit_nudge = true row wins over the legacy
        sentinel file, so the documented re-enable recipe also switches the
        legacy file off."""
        _write_state_toml(tmp_path, "memory_audit_nudge = true\n")
        (_config_dir(tmp_path) / ".memory-audit-nudge-disabled").touch()
        _write_memory_file(
            tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-true-over-legacy", "MEMORY.md", DEFAULT_PER_PROJECT_BYTES * 2
        )
        result = _run_hook(_base_payload(), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() != ""

    def test_legacy_disabled_file_alone_suppresses(self, tmp_path):
        """With no memory_audit_nudge row in the config file, the legacy
        sentinel file alone disables the nudge."""
        _write_state_toml(tmp_path, "# no memory_audit_nudge row\n")
        (_config_dir(tmp_path) / ".memory-audit-nudge-disabled").touch()
        _write_memory_file(
            tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-legacy-only", "MEMORY.md", DEFAULT_PER_PROJECT_BYTES * 2
        )
        result = _run_hook(_base_payload(), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_non_bare_false_value_is_malformed_and_nudge_fires(self, tmp_path):
        """A quoted "false" is a malformed line for a bool key, so the nudge
        keeps firing."""
        _write_state_toml(tmp_path, 'memory_audit_nudge = "false"\n')
        _write_memory_file(
            tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-quoted-false", "MEMORY.md", DEFAULT_PER_PROJECT_BYTES * 2
        )
        result = _run_hook(_base_payload(), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() != ""

    @pytest.mark.parametrize(
        "payload",
        [
            _base_payload(source="clear"),
            _base_payload(source="compact"),
            _base_payload(source="resume"),
            {"source": 123},
            {},
        ],
        ids=["clear", "compact", "resume", "non-string-source", "absent-source"],
    )
    def test_non_startup_source_is_silent(self, tmp_path, payload):
        """Non-'startup' .source values are silent, including a non-string
        .source (e.g. a JSON number) and an entirely absent .source --
        additional input-shape coverage for the outer `[ "$SOURCE" =
        "startup" ]` bash gate, which rejects both regardless of the jq
        type-check filter that precedes it."""
        _write_memory_file(
            tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-source", "MEMORY.md", DEFAULT_PER_PROJECT_BYTES * 3
        )
        result = _run_hook(payload, tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_startup_source_fires(self, tmp_path):
        _write_memory_file(
            tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-source-startup", "MEMORY.md", DEFAULT_PER_PROJECT_BYTES
        )
        result = _run_hook(_base_payload(source="startup"), tmp_path)
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        assert "/memory-store-audit" in ctx
        # N=1 project store at exactly DEFAULT_PER_PROJECT_BYTES: total and
        # threshold are both the shipped default.
        assert str(DEFAULT_PER_PROJECT_BYTES) in ctx

    def test_fire_reports_distinct_total_projects_and_threshold(self, tmp_path):
        """Two stores holding 70000 bytes give total=70000, projects=2, and
        threshold=51200 -- all distinct, so swapping any pair in the log line
        or the message fails."""
        _write_memory_file(tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-pin-a", "MEMORY.md", 40000)
        _write_memory_file(tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-pin-b", "MEMORY.md", 30000)
        result = _run_hook(_base_payload(), tmp_path)
        assert result.returncode == 0
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "now hold 70000 bytes across 2 project store(s), past the 51200-byte" in context
        fields = _parse_log_line(_log_path(tmp_path).read_text())
        assert fields == {
            "total": "70000",
            "projects": "2",
            "threshold": "51200",
            "source": "startup",
        }

    # -- Re-arm band and shrink rewrite -------------------------------------

    def test_rearm_band(self, tmp_path):
        """A second startup at the same total stays silent; growing to one
        byte short of the recorded total plus the re-arm band still stays
        silent; growing to exactly that band boundary fires again."""
        project = f"{SYNTHETIC_PROJECT_PREFIX}-rearm"
        _write_memory_file(tmp_path, project, "MEMORY.md", DEFAULT_PER_PROJECT_BYTES)
        first = _run_hook(_base_payload(), tmp_path)
        assert first.stdout.strip() != ""

        second = _run_hook(_base_payload(), tmp_path)
        assert second.returncode == 0
        assert second.stdout.strip() == ""

        topic_file = _write_memory_file(tmp_path, project, "topic.md", DEFAULT_REARM_BYTES - 1)
        just_under_band = _run_hook(_base_payload(), tmp_path)
        assert just_under_band.returncode == 0
        assert just_under_band.stdout.strip() == ""

        topic_file.write_bytes(b"x" * DEFAULT_REARM_BYTES)
        third = _run_hook(_base_payload(), tmp_path)
        assert third.returncode == 0
        assert third.stdout.strip() != ""

    def test_shrink_rewrite_then_later_crossing_fires(self, tmp_path):
        """A total below the recorded high-water mark rewrites the state file
        without firing; a later crossing past the (now-lower) recorded total
        plus the re-arm band fires again."""
        project = f"{SYNTHETIC_PROJECT_PREFIX}-shrink"
        big_file = _write_memory_file(tmp_path, project, "topic.md", DEFAULT_PER_PROJECT_BYTES * 2)
        first = _run_hook(_base_payload(), tmp_path)
        assert first.stdout.strip() != ""
        assert int(_state_file(tmp_path).read_text().strip()) == DEFAULT_PER_PROJECT_BYTES * 2

        shrunk_size = DEFAULT_PER_PROJECT_BYTES + 100
        big_file.write_bytes(b"x" * shrunk_size)
        second = _run_hook(_base_payload(), tmp_path)
        assert second.returncode == 0
        assert second.stdout.strip() == ""
        assert int(_state_file(tmp_path).read_text().strip()) == shrunk_size

        grown_size = shrunk_size + DEFAULT_REARM_BYTES
        big_file.write_bytes(b"x" * grown_size)
        third = _run_hook(_base_payload(), tmp_path)
        assert third.returncode == 0
        assert third.stdout.strip() != ""

    # -- Malformed override handling -----------------------------------------

    @pytest.mark.parametrize(
        "override_value",
        ["", "0", "abc", "0100"],
        ids=["empty", "zero", "non-digit", "zero-padded"],
    )
    def test_degenerate_per_project_bytes_override_falls_back_to_default(
        self, tmp_path, override_value
    ):
        """A malformed MEMORY_AUDIT_NUDGE_PER_PROJECT_BYTES override falls
        back to the shipped 25600 default rather than degrading the
        threshold toward 0 -- one byte under the default stays silent."""
        _write_memory_file(
            tmp_path,
            f"{SYNTHETIC_PROJECT_PREFIX}-override",
            "MEMORY.md",
            DEFAULT_PER_PROJECT_BYTES - 1,
        )
        result = _run_hook(
            _base_payload(),
            tmp_path,
            extra_env={"MEMORY_AUDIT_NUDGE_PER_PROJECT_BYTES": override_value},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    @pytest.mark.parametrize(
        "override_value",
        ["123456789", "1234567890"],
        ids=["nine-digits", "ten-digits"],
    )
    def test_oversized_per_project_bytes_override_falls_back_to_default(
        self, tmp_path, override_value
    ):
        """A 9+ digit override is rejected by the `?????????*` arm. A store
        sized exactly at the shipped default fires only if the fallback
        applied; an honored huge override would keep it silent."""
        _write_memory_file(
            tmp_path,
            f"{SYNTHETIC_PROJECT_PREFIX}-oversized",
            "MEMORY.md",
            DEFAULT_PER_PROJECT_BYTES,
        )
        result = _run_hook(
            _base_payload(),
            tmp_path,
            extra_env={"MEMORY_AUDIT_NUDGE_PER_PROJECT_BYTES": override_value},
        )
        assert result.returncode == 0
        assert result.stdout.strip() != ""
        fields = _parse_log_line(_log_path(tmp_path).read_text())
        assert fields["threshold"] == str(DEFAULT_PER_PROJECT_BYTES)

    def test_eight_digit_per_project_bytes_override_is_honored(self, tmp_path):
        """Positive control for the 9+-digit rejection above: an 8-digit
        override is honored, so a store past the 25600 default stays silent."""
        _write_memory_file(
            tmp_path,
            f"{SYNTHETIC_PROJECT_PREFIX}-eightdigit",
            "MEMORY.md",
            DEFAULT_PER_PROJECT_BYTES + 5000,
        )
        result = _run_hook(
            _base_payload(),
            tmp_path,
            extra_env={"MEMORY_AUDIT_NUDGE_PER_PROJECT_BYTES": "10000000"},  # smallest 8-digit value
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_no_override_uses_shipped_default_threshold(self, tmp_path):
        """With no override set at all, the fired threshold is exactly the
        shipped default (25600 x 1 project)."""
        _write_memory_file(
            tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-defaultcontrol", "MEMORY.md", DEFAULT_PER_PROJECT_BYTES
        )
        result = _run_hook(_base_payload(), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() != ""
        fields = _parse_log_line(_log_path(tmp_path).read_text())
        assert fields["threshold"] == str(DEFAULT_PER_PROJECT_BYTES)

    def _fire_then_grow_by_default_band(self, tmp_path, project, extra_env):
        """First fire at the shipped threshold, then grow the store by exactly
        DEFAULT_REARM_BYTES; returns the second run's result."""
        _write_memory_file(tmp_path, project, "MEMORY.md", DEFAULT_PER_PROJECT_BYTES)
        first = _run_hook(_base_payload(), tmp_path, extra_env=extra_env)
        assert first.stdout.strip() != ""
        _write_memory_file(tmp_path, project, "topic.md", DEFAULT_REARM_BYTES)
        return _run_hook(_base_payload(), tmp_path, extra_env=extra_env)

    @pytest.mark.parametrize(
        "override_value",
        ["", "0", "abc", "0100"],
        ids=["empty", "zero", "non-digit", "zero-padded"],
    )
    def test_degenerate_rearm_bytes_override_falls_back_to_default(self, tmp_path, override_value):
        """A malformed MEMORY_AUDIT_NUDGE_REARM_BYTES override falls back to
        the shipped 25600 band rather than degrading toward 0 -- growth one
        byte short of the band stays silent."""
        project = f"{SYNTHETIC_PROJECT_PREFIX}-rearm-override"
        extra_env = {"MEMORY_AUDIT_NUDGE_REARM_BYTES": override_value}
        _write_memory_file(tmp_path, project, "MEMORY.md", DEFAULT_PER_PROJECT_BYTES)
        assert _run_hook(_base_payload(), tmp_path, extra_env=extra_env).stdout.strip() != ""
        _write_memory_file(tmp_path, project, "topic.md", DEFAULT_REARM_BYTES - 1)
        result = _run_hook(_base_payload(), tmp_path, extra_env=extra_env)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    @pytest.mark.parametrize(
        "override_value",
        ["123456789", "1234567890"],
        ids=["nine-digits", "ten-digits"],
    )
    def test_oversized_rearm_bytes_override_falls_back_to_default(self, tmp_path, override_value):
        """A 9+ digit re-arm override is rejected: growth of exactly the
        shipped band fires again only if the fallback applied."""
        result = self._fire_then_grow_by_default_band(
            tmp_path,
            f"{SYNTHETIC_PROJECT_PREFIX}-rearm-oversized",
            {"MEMORY_AUDIT_NUDGE_REARM_BYTES": override_value},
        )
        assert result.returncode == 0
        assert result.stdout.strip() != ""

    def test_eight_digit_rearm_bytes_override_is_honored(self, tmp_path):
        """Positive control for the 9+-digit rejection above: an 8-digit
        re-arm band is honored, so growth of the default band stays silent."""
        result = self._fire_then_grow_by_default_band(
            tmp_path,
            f"{SYNTHETIC_PROJECT_PREFIX}-rearm-eightdigit",
            {"MEMORY_AUDIT_NUDGE_REARM_BYTES": "10000000"},  # smallest 8-digit value
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    # -- wc-total-row-and-project-count awk program -------------------------

    def test_wc_total_row_awk_excludes_multiple_total_rows(self):
        """The single-pass awk program, run from its own sidecar file exactly
        as the hook runs it, discriminates every 'total' row from real
        per-file lines even when a batched `find -exec` produces more than
        one -- summing $1 unconditionally would count those rows as if they
        were files. Its two-line output is the byte total (excluding total
        rows) then the distinct-project-memory-directory count."""
        synthetic_wc_output = (
            "     100 /config/projects/a/memory/MEMORY.md\n"
            "     200 /config/projects/b/memory/topic.md\n"
            "     300 total\n"
            "     400 /config/projects/c/memory/MEMORY.md\n"
            "     500 total\n"
        )
        result = subprocess.run(
            ["awk", "-f", str(WC_TOTAL_ROW_AWK_PROGRAM)],
            input=synthetic_wc_output,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert result.stdout.strip().splitlines() == ["700", "3"]

    def test_wc_total_row_awk_counts_project_once_across_multiple_files(self):
        """A project store contributing more than one file counts once
        toward the project-store total, not once per file -- pins the
        one-pass bucketing against a regression to a per-file tally."""
        synthetic_wc_output = (
            "     100 /config/projects/a/memory/MEMORY.md\n"
            "     200 /config/projects/a/memory/topic.md\n"
        )
        result = subprocess.run(
            ["awk", "-f", str(WC_TOTAL_ROW_AWK_PROGRAM)],
            input=synthetic_wc_output,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert result.stdout.strip().splitlines() == ["300", "1"]

    # -- Fail-open ------------------------------------------------------------

    def test_jq_absent_fails_open_silent(self, tmp_path):
        _write_memory_file(
            tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-nojq", "MEMORY.md", DEFAULT_PER_PROJECT_BYTES
        )
        farm_dir = tmp_path / "path-without-jq"
        farm_dir.mkdir()
        restricted_path = build_path_without("jq", farm_dir)
        result = _run_hook(_base_payload(), tmp_path, extra_env={"PATH": restricted_path})
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert result.stderr.strip() == ""

    def test_missing_lib_sh_sibling_fails_open(self, tmp_path):
        """A copy of the hook with no _lib.sh sibling exits 0 with no output
        rather than erroring. A control with the siblings present and the same
        over-threshold store fires, so the silence is attributable to the
        missing sibling and not to an empty store."""
        _write_memory_file(
            tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-no-lib", "MEMORY.md", DEFAULT_PER_PROJECT_BYTES * 3
        )
        env = {**os.environ, "HOME": str(tmp_path)}
        env.pop("CLAUDE_CONFIG_DIR", None)

        def run_copy(hook_path: Path) -> subprocess.CompletedProcess:
            return subprocess.run(
                [str(hook_path)],
                input=json.dumps(_base_payload()),
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )

        control = run_copy(_isolated_hooks_dir(tmp_path))
        assert control.stdout.strip() != "", "control with siblings present must fire"

        bare_dir = tmp_path / "bare-hooks"
        bare_dir.mkdir()
        bare_hook = bare_dir / NUDGE_HOOK.name
        bare_hook.write_text(NUDGE_HOOK.read_text())
        bare_hook.chmod(0o755)
        result = run_copy(bare_hook)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_missing_awk_sidecar_fails_open(self, tmp_path):
        """A copy of the hook with _lib.sh/_config.sh siblings present but no
        nudge-memory-store-audit.awk sidecar exits 0 with no output rather
        than erroring -- `awk -f` on an absent/unreadable program file fails
        the way this hook's fail-open contract requires. A control with the
        sidecar present and the same over-threshold store fires, so the
        silence is attributable to the missing sidecar and not to an empty
        store. Mirrors test_missing_lib_sh_sibling_fails_open above for the
        newer sidecar dependency introduced by the wc-total-row-and-project-
        count refactor."""
        _write_memory_file(
            tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-no-awk", "MEMORY.md", DEFAULT_PER_PROJECT_BYTES * 3
        )
        env = {**os.environ, "HOME": str(tmp_path)}
        env.pop("CLAUDE_CONFIG_DIR", None)

        def run_copy(hook_path: Path) -> subprocess.CompletedProcess:
            return subprocess.run(
                [str(hook_path)],
                input=json.dumps(_base_payload()),
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )

        control = run_copy(_isolated_hooks_dir(tmp_path))
        assert control.stdout.strip() != "", "control with the sidecar present must fire"

        no_awk_dir = tmp_path / "no-awk-sidecar-hooks"
        no_awk_dir.mkdir()
        (no_awk_dir / NUDGE_HOOK.name).symlink_to(NUDGE_HOOK)
        (no_awk_dir / "_lib.sh").symlink_to(HOOKS_DIR / "_lib.sh")
        (no_awk_dir / "_config.sh").symlink_to(HOOKS_DIR / "_config.sh")
        result = run_copy(no_awk_dir / NUDGE_HOOK.name)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_unresolvable_config_dir_fails_open(self, tmp_path):
        """Empty $HOME and no CLAUDE_CONFIG_DIR leaves _lib_config_dir
        unable to resolve -- the hook must exit 0 with no output, not crash
        on an empty config-dir path."""
        env = dict(os.environ)
        env.pop("CLAUDE_CONFIG_DIR", None)
        env["HOME"] = ""
        result = subprocess.run(
            [str(NUDGE_HOOK)],
            input=json.dumps(_base_payload()),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0
        assert not result.stdout.strip()

    def test_malformed_stdin_fails_open(self, tmp_path):
        env = {**os.environ, "HOME": str(tmp_path)}
        env.pop("CLAUDE_CONFIG_DIR", None)
        result = subprocess.run(
            [str(NUDGE_HOOK)],
            input="not-valid-json{{{",
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    # -- Redaction and scan-scope regressions --------------------------------

    def test_redaction_project_name_never_appears_in_output_or_log(self, tmp_path):
        """No project directory name may appear in the emitted
        additionalContext or the log line -- only aggregate counts."""
        distinctive_name = f"{SYNTHETIC_PROJECT_PREFIX}-distinctive-widget-corp"
        _write_memory_file(tmp_path, distinctive_name, "MEMORY.md", DEFAULT_PER_PROJECT_BYTES)
        result = _run_hook(_base_payload(), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() != ""
        assert distinctive_name not in result.stdout
        log_text = _log_path(tmp_path).read_text()
        assert distinctive_name not in log_text

    def test_scan_scope_excludes_files_outside_the_store_root(self, tmp_path):
        """Large files beside the store (a sibling transcript, and a `memory`
        directory nested under another project subdirectory) are never
        counted: only `projects/*/memory` start points are walked."""
        project = f"{SYNTHETIC_PROJECT_PREFIX}-scanscope"
        _write_memory_file(tmp_path, project, "MEMORY.md", DEFAULT_PER_PROJECT_BYTES - 1)
        project_dir = _config_dir(tmp_path) / "projects" / project
        (project_dir / f"{project}-transcript.jsonl").write_bytes(b"x" * (DEFAULT_PER_PROJECT_BYTES * 10))
        other_memory_dir = project_dir / "other-session" / "memory"
        other_memory_dir.mkdir(parents=True)
        (other_memory_dir / "big.md").write_bytes(b"x" * (DEFAULT_PER_PROJECT_BYTES * 10))
        result = _run_hook(_base_payload(), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    # -- Structural edge cases ------------------------------------------------

    def test_zero_match_glob_no_fire_no_crash(self, tmp_path):
        """A projects/ tree with no memory/ directory at all produces no
        fire and no crash -- exercises the nullglob restore path with zero
        matches."""
        project_dir = _config_dir(tmp_path) / "projects" / f"{SYNTHETIC_PROJECT_PREFIX}-nomemdir"
        project_dir.mkdir(parents=True)
        (project_dir / "session.jsonl").write_text("{}\n")
        result = _run_hook(_base_payload(), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert not _state_file(tmp_path).exists()

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root bypasses discretionary file-permission bits, so chmod(0o000) "
        "would not actually make the directory unreadable",
    )
    def test_unreadable_project_memory_dir_is_skipped_and_readable_store_still_measured(self, tmp_path):
        """find exits nonzero on a permission-denied directory; that benign
        status must not discard the measurement of the readable store."""
        _write_memory_file(
            tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-readable", "MEMORY.md", DEFAULT_PER_PROJECT_BYTES
        )
        unreadable_memory_dir = _memory_dir(tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-unreadable")
        (unreadable_memory_dir / "MEMORY.md").write_bytes(b"x" * 1000)
        unreadable_memory_dir.chmod(0o000)
        try:
            result = _run_hook(_base_payload(), tmp_path)
        finally:
            unreadable_memory_dir.chmod(0o755)
        assert result.returncode == 0
        fields = _parse_log_line(_log_path(tmp_path).read_text())
        assert (fields["total"], fields["projects"]) == (str(DEFAULT_PER_PROJECT_BYTES), "1")

    def test_corrupted_state_file_fires_rather_than_suppresses(self, tmp_path):
        """A non-numeric state-file record must trigger a fire (the inverse
        of the shrink-rewrite silence case) -- fail toward firing, never
        toward silent suppression."""
        _write_memory_file(
            tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-corrupt", "MEMORY.md", DEFAULT_PER_PROJECT_BYTES
        )
        _config_dir(tmp_path).mkdir(parents=True, exist_ok=True)
        _state_file(tmp_path).write_text("not-a-number\n")
        result = _run_hook(_base_payload(), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() != "", (
            "a corrupted state-file record must fire, not silently suppress"
        )

    def test_zero_byte_memory_md_counts_toward_project_store_denominator(self, tmp_path):
        """A present-but-empty MEMORY.md still counts as a project store
        holding memory content for the count-scaled denominator N -- an
        explicit design choice, asserted directly rather than left to fall
        out of the implementation."""
        first_project = f"{SYNTHETIC_PROJECT_PREFIX}-zerobyte"
        _write_memory_file(tmp_path, first_project, "MEMORY.md", 0)
        # N=1 (the zero-byte store counts), threshold=25600, total=0: silent.
        result = _run_hook(_base_payload(), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

        # A second store's own bytes alone (25600) would clear a threshold
        # scaled to N=1 but not one scaled to N=2 (51200) -- proving the
        # zero-byte store above was counted toward N.
        second_project = f"{SYNTHETIC_PROJECT_PREFIX}-zerobyte-2"
        _write_memory_file(tmp_path, second_project, "MEMORY.md", DEFAULT_PER_PROJECT_BYTES)
        result2 = _run_hook(_base_payload(), tmp_path)
        assert result2.returncode == 0
        assert result2.stdout.strip() == "", (
            "total=25600 must stay below threshold=51200 (N=2, counting the "
            "zero-byte store); if the zero-byte store did not count toward N, "
            "threshold would be 25600 and this would incorrectly fire"
        )

    def test_symlinked_file_inside_memory_dir_is_skipped_deterministically(self, tmp_path):
        """A symlink inside memory/ is excluded from both the byte total and
        the project-store count -- find's default (no -L) -type f test does
        not match a symlink, so a store containing only one never fires,
        never errors, and never silently mis-counts."""
        project = f"{SYNTHETIC_PROJECT_PREFIX}-symlink"
        memory_dir = _memory_dir(tmp_path, project)
        real_target = tmp_path / "outside-memory-target.md"
        real_target.write_bytes(b"x" * (DEFAULT_PER_PROJECT_BYTES * 5))
        (memory_dir / "linked.md").symlink_to(real_target)
        result = _run_hook(_base_payload(), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == "", (
            "a project store containing only a symlink must not count toward "
            "N or contribute bytes -- find's default -type f test skips it"
        )

    def test_symlinked_memory_dir_counts_toward_total_and_store_count(self, tmp_path):
        """A project's `memory` glob match that is itself a symlink to a
        directory is followed (find -H), so its bytes and its store both count."""
        project = f"{SYNTHETIC_PROJECT_PREFIX}-symlinked-memory-dir"
        project_dir = _config_dir(tmp_path) / "projects" / project
        project_dir.mkdir(parents=True)
        real_target = tmp_path / "real-memory-target"
        real_target.mkdir()
        (real_target / "MEMORY.md").write_bytes(b"x" * DEFAULT_PER_PROJECT_BYTES)
        (project_dir / "memory").symlink_to(real_target, target_is_directory=True)
        result = _run_hook(_base_payload(), tmp_path)
        assert result.returncode == 0
        fields = _parse_log_line(_log_path(tmp_path).read_text())
        assert (fields["total"], fields["projects"]) == (str(DEFAULT_PER_PROJECT_BYTES), "1")

    def test_symlinked_directory_inside_memory_dir_is_not_followed(self, tmp_path):
        """-H follows only command-line symlinks: a symlink to a directory met
        during traversal contributes no bytes."""
        project = f"{SYNTHETIC_PROJECT_PREFIX}-inner-dir-symlink"
        memory_dir = _memory_dir(tmp_path, project)
        (memory_dir / "MEMORY.md").write_bytes(b"x" * DEFAULT_PER_PROJECT_BYTES)
        outside_dir = tmp_path / "outside-dir"
        outside_dir.mkdir()
        (outside_dir / "big.md").write_bytes(b"x" * (DEFAULT_PER_PROJECT_BYTES * 5))
        (memory_dir / "linked-dir").symlink_to(outside_dir, target_is_directory=True)
        result = _run_hook(_base_payload(), tmp_path)
        assert result.returncode == 0
        fields = _parse_log_line(_log_path(tmp_path).read_text())
        assert fields["total"] == str(DEFAULT_PER_PROJECT_BYTES)

    # -- Newline-bearing paths ------------------------------------------------

    def test_newline_in_directory_name_cannot_forge_wc_rows(self, tmp_path):
        """`wc` prints a path verbatim, so a directory named
        `d<LF>99999999 x` would otherwise add a forged 99999999-byte row.
        Such paths are pruned: the total stays the real files' bytes."""
        project = f"{SYNTHETIC_PROJECT_PREFIX}-newline"
        memory_dir = _memory_dir(tmp_path, project)
        (memory_dir / "MEMORY.md").write_bytes(b"x" * DEFAULT_PER_PROJECT_BYTES)
        forging_dir = memory_dir / "d\n99999999 x"
        forging_dir.mkdir()
        (forging_dir / "f.md").write_bytes(b"x" * 10)
        (memory_dir / "g\n5 y.md").write_bytes(b"x" * 10)
        result = _run_hook(_base_payload(), tmp_path)
        assert result.returncode == 0
        fields = _parse_log_line(_log_path(tmp_path).read_text())
        assert (fields["total"], fields["projects"]) == (str(DEFAULT_PER_PROJECT_BYTES), "1")

    # -- Cap-killed and benign-nonzero measurement statuses -------------------

    def _seed_high_water_mark(self, tmp_path, project, recorded_total):
        _write_memory_file(tmp_path, project, "MEMORY.md", DEFAULT_PER_PROJECT_BYTES)
        _state_file(tmp_path).write_text(f"{recorded_total}\n")

    @pytest.mark.parametrize("killed_status", [124, 137, 143])
    def test_cap_killed_find_discards_partial_measurement(self, tmp_path, killed_status):
        """A find killed by its cap after partial output leaves the recorded
        high-water mark, the log, and stdout untouched."""
        self._seed_high_water_mark(tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-killed-find", 600000)
        shim_dir = tmp_path / "shims"
        _write_shim(
            shim_dir,
            "find",
            f"printf '40000 /x/projects/p/memory/a.md\\n40000 /x/projects/p/memory/b.md\\n'\nexit {killed_status}",
        )
        result = _run_hook(_base_payload(), tmp_path, extra_env={"PATH": _path_with_shims(shim_dir)})
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert _state_file(tmp_path).read_text().strip() == "600000"
        assert not _log_path(tmp_path).exists()

    @pytest.mark.parametrize("killed_status", [124, 137, 143])
    def test_cap_killed_awk_discards_partial_measurement(self, tmp_path, killed_status):
        """An awk killed by its cap leaves the recorded high-water mark, the
        log, and stdout untouched, even though it printed a total first."""
        self._seed_high_water_mark(tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-killed-awk", 600000)
        shim_dir = tmp_path / "shims"
        _write_shim(shim_dir, "awk", f"printf '80000\\n1\\n'\nexit {killed_status}")
        result = _run_hook(_base_payload(), tmp_path, extra_env={"PATH": _path_with_shims(shim_dir)})
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert _state_file(tmp_path).read_text().strip() == "600000"
        assert not _log_path(tmp_path).exists()

    def _install_stalling_shim(self, tmp_path, binary, match_condition, project):
        """Seed a high-water mark and install a shim that stalls `binary` past its
        scaled cap when `match_condition` holds; returns the shim dir. Skips
        without a GNU-coreutils timeout, like git_timeout_shim."""
        if not _real_timeout_is_gnu_coreutils():
            pytest.skip("neither timeout(1) nor gtimeout(1) is GNU coreutils")
        real_binary = shutil.which(binary)
        assert real_binary is not None
        self._seed_high_water_mark(tmp_path, project, 600000)
        shim_dir = tmp_path / "shims"
        shim_dir.mkdir()
        _write_conditional_sleep_shim(shim_dir, binary, real_binary, match_condition)
        return shim_dir

    def _assert_silent_and_stateless(self, tmp_path, result):
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert _state_file(tmp_path).read_text().strip() == "600000"
        assert not _log_path(tmp_path).exists()

    @pytest.mark.timing
    @pytest.mark.parametrize("stalled_binary", ["find", "awk"])
    def test_stalled_scan_binary_is_capped_and_discards_the_measurement(self, tmp_path, stalled_binary):
        """A `find` or `awk` that stalls is killed by the real scaled cap, so the
        hook exits 0 with no stdout, no log line, and the state file untouched."""
        shim_dir = self._install_stalling_shim(
            tmp_path, stalled_binary, "true", f"{SYNTHETIC_PROJECT_PREFIX}-stalled-{stalled_binary}"
        )
        with assert_cap_engaged(shim_dir, production_cap=5, command=stalled_binary):
            result = _run_hook(_base_payload(), tmp_path, extra_env={"PATH": _path_with_shims(shim_dir)})
        self._assert_silent_and_stateless(tmp_path, result)

    @pytest.mark.timing
    def test_stalled_source_parse_jq_is_capped_and_exits_silently(self, tmp_path):
        """A `jq` that stalls on the `.source` parse is killed by the cap, leaving
        SOURCE empty, so the hook exits 0 with no output before any scan."""
        shim_dir = self._install_stalling_shim(
            tmp_path, "jq", '[ "$1" = "-r" ]', f"{SYNTHETIC_PROJECT_PREFIX}-stalled-jq"
        )
        with assert_cap_engaged(shim_dir, production_cap=5, command="jq"):
            result = _run_hook(_base_payload(), tmp_path, extra_env={"PATH": _path_with_shims(shim_dir)})
        self._assert_silent_and_stateless(tmp_path, result)

    def test_benign_nonzero_find_status_keeps_the_measurement(self, tmp_path):
        """A find that exits 1 (unreadable or vanished file) after a complete
        listing is not a cap kill: the measurement stands and fires."""
        _write_memory_file(
            tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-benign-find", "MEMORY.md", DEFAULT_PER_PROJECT_BYTES
        )
        real_find = shutil.which("find")
        assert real_find is not None
        shim_dir = tmp_path / "shims"
        _write_shim(shim_dir, "find", f'{real_find} "$@"\nexit 1')
        result = _run_hook(_base_payload(), tmp_path, extra_env={"PATH": _path_with_shims(shim_dir)})
        assert result.returncode == 0
        assert result.stdout.strip() != ""
        fields = _parse_log_line(_log_path(tmp_path).read_text())
        assert fields["total"] == str(DEFAULT_PER_PROJECT_BYTES)

    def test_scan_runs_uncapped_when_neither_timeout_nor_gtimeout_is_on_path(self, tmp_path):
        """Without timeout(1) and gtimeout(1) the scan runs uncapped, bounded
        only by the settings.json registration, and still measures and fires."""
        _write_memory_file(
            tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-no-timeout", "MEMORY.md", DEFAULT_PER_PROJECT_BYTES
        )
        farm_dir = tmp_path / "path-without-timeout"
        farm_dir.mkdir()
        restricted_path = build_path_without("timeout", farm_dir)
        # build_path_without omits one binary; the hook probes both spellings.
        (farm_dir / "gtimeout").unlink(missing_ok=True)
        assert shutil.which("timeout", path=restricted_path) is None
        assert shutil.which("gtimeout", path=restricted_path) is None
        result = _run_hook(_base_payload(), tmp_path, extra_env={"PATH": restricted_path})
        assert result.returncode == 0
        assert json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"] != ""

    def test_message_states_the_effective_per_store_bytes_under_override(self, tmp_path):
        """The nudge message derives its per-store figure from the effective
        override, not a fixed literal."""
        _write_memory_file(tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-message-override", "MEMORY.md", 5000)
        result = _run_hook(
            _base_payload(),
            tmp_path,
            extra_env={"MEMORY_AUDIT_NUDGE_PER_PROJECT_BYTES": "4000"},
        )
        assert result.returncode == 0
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "past the 4000-byte size threshold (4000 bytes per store;" in context
        assert "25 KB" not in context
        assert "startup-load size" not in context


class TestMemoryAuditGitignoreEntries:
    """The memory-audit runtime files and the quarantine directory are all
    git-ignored; the quarantine holds unredacted memory files."""

    @pytest.mark.parametrize(
        "ignored_path",
        [
            "claude/.claude/.memory-audit-nudge-fired",
            "claude/.claude/.memory-audit-nudge.log",
            "claude/.claude/.memory-audit-nudge-disabled",
            "claude/.claude/.memory-audit-quarantine/2000-01-01T00-00-00Z/project/topic.md",
        ],
    )
    def test_memory_audit_path_is_git_ignored(self, ignored_path):
        repo_root = HOOKS_DIR.parents[2]
        result = subprocess.run(
            ["git", "check-ignore", "-q", ignored_path],
            cwd=repo_root,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, f"{ignored_path} is not git-ignored"
