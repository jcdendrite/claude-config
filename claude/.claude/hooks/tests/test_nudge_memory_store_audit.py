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
import subprocess
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, build_path_without

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


def _run_hook(
    payload: dict, home: Path, extra_env: dict | None = None
) -> subprocess.CompletedProcess:
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


def _base_payload(source: str = "startup") -> dict:
    return {"source": source}


def _state_file(home: Path) -> Path:
    return _config_dir(home) / ".memory-audit-nudge-fired"


def _log_path(home: Path) -> Path:
    return _config_dir(home) / ".memory-audit-nudge.log"


def _parse_log_line(text: str) -> dict:
    """Split a `nudged key=value ...` log line into a field dict."""
    return dict(token.split("=", 1) for token in text.strip().split() if "=" in token)


def _extract_wc_total_row_awk_program() -> str:
    """Extract the wc-total-row-exclusion awk program verbatim from between
    its HOOK_TEST_FIXTURE sentinels in the hook source, for a standalone awk
    invocation outside the hook's own subprocess."""
    source = NUDGE_HOOK.read_text()
    start = source.index("# HOOK_TEST_FIXTURE: wc-total-row-awk — start")
    end = source.index("# HOOK_TEST_FIXTURE: wc-total-row-awk — end", start)
    block = source[start:end]
    program_start = block.index("awk '") + len("awk '")
    program_end = block.index("'", program_start)
    return block[program_start:program_end]


def _path_without_timeout_or_gtimeout(fake_bin: Path) -> str:
    """Build a PATH with only the binaries this hook's fire path invokes
    (`dirname` to locate _lib.sh, `cat`/`jq` for the payload/output JSON,
    `find`/`wc` for the byte scan, `awk` for the total-and-project-count
    pass, `mkdir` for the config dir), omitting both timeout(1) and
    gtimeout(1). Skips (does not silently under-symlink) when a needed real
    binary is itself absent from the test machine."""
    for tool in ("awk", "cat", "dirname", "find", "jq", "mkdir", "wc"):
        real = shutil.which(tool)
        if not real:
            pytest.skip(f"{tool} not found in PATH")
        (fake_bin / tool).symlink_to(real)
    return str(fake_bin)


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

    @pytest.mark.parametrize("source", ["clear", "compact", "resume"])
    def test_non_startup_source_is_silent(self, tmp_path, source):
        _write_memory_file(
            tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-source", "MEMORY.md", DEFAULT_PER_PROJECT_BYTES * 3
        )
        result = _run_hook(_base_payload(source=source), tmp_path)
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
        ["", "0", "abc", "0100", "1234567890"],
        ids=["empty", "zero", "non-digit", "zero-padded", "ten-plus-digits"],
    )
    def test_malformed_per_project_bytes_override_falls_back_to_default(
        self, tmp_path, override_value
    ):
        """A malformed MEMORY_AUDIT_NUDGE_PER_PROJECT_BYTES override falls
        back to the shipped 25600 default rather than degrading the
        threshold toward 0 or negative."""
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
        # Still below the correctly-applied 25600 default -- a leaked
        # malformed override (toward 0) would fire here instead.
        assert result.stdout.strip() == ""

    def test_no_override_uses_shipped_default_threshold(self, tmp_path):
        """Positive control for the malformed-override table above: with no
        override set at all, the fired threshold is exactly the shipped
        default (25600 x 1 project) -- not merely "a bad string is ignored"."""
        _write_memory_file(
            tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-defaultcontrol", "MEMORY.md", DEFAULT_PER_PROJECT_BYTES
        )
        result = _run_hook(_base_payload(), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() != ""
        fields = _parse_log_line(_log_path(tmp_path).read_text())
        assert fields["threshold"] == str(DEFAULT_PER_PROJECT_BYTES)

    @pytest.mark.parametrize(
        "override_value",
        ["", "0", "abc", "0100", "1234567890"],
        ids=["empty", "zero", "non-digit", "zero-padded", "ten-plus-digits"],
    )
    def test_malformed_rearm_bytes_override_falls_back_to_default(self, tmp_path, override_value):
        """A malformed MEMORY_AUDIT_NUDGE_REARM_BYTES override falls back to
        the shipped 25600 default at the re-arm boundary, rather than
        degrading it toward 0 and re-firing before the real band is
        crossed."""
        project = f"{SYNTHETIC_PROJECT_PREFIX}-rearm-override"
        extra_env = {"MEMORY_AUDIT_NUDGE_REARM_BYTES": override_value}
        _write_memory_file(tmp_path, project, "MEMORY.md", DEFAULT_PER_PROJECT_BYTES)
        first = _run_hook(_base_payload(), tmp_path, extra_env=extra_env)
        assert first.stdout.strip() != ""

        _write_memory_file(tmp_path, project, "topic.md", DEFAULT_REARM_BYTES - 1)
        result = _run_hook(_base_payload(), tmp_path, extra_env=extra_env)
        assert result.returncode == 0
        # Still below the correctly-applied 25600 re-arm default -- a leaked
        # malformed override (toward 0) would fire here instead.
        assert result.stdout.strip() == ""

    # -- Timeout-binary precondition ------------------------------------------

    def test_no_fire_when_neither_timeout_nor_gtimeout_present(self, tmp_path):
        """The scan is skipped entirely -- no stdout, no log line, no
        state-file write -- when neither timeout(1) nor gtimeout(1) resolves,
        even against a store well past threshold."""
        _write_memory_file(
            tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-notimeout", "MEMORY.md", DEFAULT_PER_PROJECT_BYTES * 5
        )
        fake_bin = tmp_path / "fakebin-no-timeout-no-gtimeout"
        fake_bin.mkdir()
        restricted_path = _path_without_timeout_or_gtimeout(fake_bin)
        result = _run_hook(_base_payload(), tmp_path, extra_env={"PATH": restricted_path})
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert not _log_path(tmp_path).exists()
        assert not _state_file(tmp_path).exists()

    def test_fires_when_a_timeout_binary_is_present(self, tmp_path):
        """With a timeout-shaped binary resolvable on PATH, the same
        over-threshold tree fires -- pins the precondition's ordering (it
        precedes the scan, the same ordering property the kill-switch has)."""
        _write_memory_file(
            tmp_path, f"{SYNTHETIC_PROJECT_PREFIX}-withtimeout", "MEMORY.md", DEFAULT_PER_PROJECT_BYTES
        )
        fake_bin = tmp_path / "fakebin-with-timeout"
        fake_bin.mkdir()
        restricted_path = _path_without_timeout_or_gtimeout(fake_bin)
        timeout_target = shutil.which("timeout") or shutil.which("gtimeout")
        assert timeout_target is not None, (
            "no timeout-shaped binary found on this machine to exercise the "
            "precondition's positive branch"
        )
        (fake_bin / "timeout").symlink_to(timeout_target)
        result = _run_hook(_base_payload(), tmp_path, extra_env={"PATH": restricted_path})
        assert result.returncode == 0
        assert result.stdout.strip() != ""

    # -- wc-total-row-and-project-count awk program -------------------------

    def test_wc_total_row_awk_excludes_multiple_total_rows(self):
        """The single-pass awk program, extracted verbatim from the hook
        source, discriminates every 'total' row from real per-file lines
        even when a batched `find -exec` produces more than one -- summing
        $1 unconditionally would count those rows as if they were files.
        Its two-line output is the byte total (excluding total rows) then
        the distinct-project-memory-directory count."""
        program = _extract_wc_total_row_awk_program()
        synthetic_wc_output = (
            "     100 /config/projects/a/memory/MEMORY.md\n"
            "     200 /config/projects/b/memory/topic.md\n"
            "     300 total\n"
            "     400 /config/projects/c/memory/MEMORY.md\n"
            "     500 total\n"
        )
        result = subprocess.run(
            ["awk", program],
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
        program = _extract_wc_total_row_awk_program()
        synthetic_wc_output = (
            "     100 /config/projects/a/memory/MEMORY.md\n"
            "     200 /config/projects/a/memory/topic.md\n"
        )
        result = subprocess.run(
            ["awk", program],
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
        """Run a copy of the hook with no _lib.sh sibling in its directory --
        the `source` at the top of the script fails and the hook must exit 0
        with no output, not error."""
        isolated_hook_dir = tmp_path / "isolated-hooks"
        isolated_hook_dir.mkdir()
        isolated_hook = isolated_hook_dir / NUDGE_HOOK.name
        isolated_hook.write_text(NUDGE_HOOK.read_text())
        isolated_hook.chmod(0o755)
        home = tmp_path / "home-no-lib"
        home.mkdir()
        result = subprocess.run(
            [str(isolated_hook)],
            input=json.dumps(_base_payload()),
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(home)},
            check=False,
        )
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

    def test_scan_scope_excludes_sibling_transcript_file(self, tmp_path):
        """A large sibling file directly under projects/<project>/ (outside
        memory/) is never counted -- pins the .../memory glob start-point
        form against a regression to a `-path '*/memory/*'` walk of the
        whole projects tree."""
        project = f"{SYNTHETIC_PROJECT_PREFIX}-scanscope"
        _write_memory_file(tmp_path, project, "MEMORY.md", DEFAULT_PER_PROJECT_BYTES - 1)
        project_dir = _config_dir(tmp_path) / "projects" / project
        sibling = project_dir / f"{project}-transcript.jsonl"
        sibling.write_bytes(b"x" * (DEFAULT_PER_PROJECT_BYTES * 10))
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
    def test_unreadable_project_memory_dir_exits_cleanly(self, tmp_path):
        """One synthetic project's memory/ directory is unreadable
        (permission-denied mid-scan); the hook must still exit 0 with no
        crash and, if it emits anything, well-formed JSON."""
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
        if result.stdout.strip():
            payload = json.loads(result.stdout)
            assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"

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
