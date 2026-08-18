"""Tests for prune-otel-raw-bodies.sh.

SessionStart hook (matcher: startup) that bounds ~/.claude/otel-raw-bodies/
-- the raw wire-format API request/response capture directory written when
OTEL_LOG_RAW_API_BODIES=file:<dir> is armed -- by age (7 days, `-mtime +6`)
and by total size (5 GiB ceiling, oldest-first eviction). The path is a
pinned constant; nothing here or in the hook reads config to locate it.
Every path exits 0 -- this hook must never block session startup.

Age fixtures use cutoff-2 / cutoff+2 (5 days / 9 days), never the exact
7-day boundary: BSD `-mtime` truncates to 24-hour periods measured from the
hook's own invocation time, so a boundary fixture would race the wall clock
between fixture setup and subprocess start under pytest-xdist.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from helpers import HOOKS_DIR

PRUNE_HOOK = HOOKS_DIR / "prune-otel-raw-bodies.sh"

# 5 GiB, matching the hook's PRODUCTION_MAX_BYTES literal in bytes and MiB --
# pinned independently of the hook source so a units error in the real
# constant can't hide behind an injected test ceiling.
PRODUCTION_MAX_BYTES = 5368709120
PRODUCTION_CEILING_MIB = PRODUCTION_MAX_BYTES // 1048576


def _run_hook(home: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(PRUNE_HOOK)], capture_output=True, text=True, env=env, check=False
    )


def _backdate(path: Path, days_ago: int) -> None:
    """Set path's mtime to `days_ago` days before now via `touch -mt` --
    matches how the hook's own BSD `find -mtime` day-truncation semantics
    are exercised in practice, rather than an epoch timestamp computed a
    different way."""
    stamp = (datetime.now() - timedelta(days=days_ago)).strftime("%Y%m%d%H%M.%S")
    subprocess.run(["touch", "-mt", stamp, str(path)], check=True)


def _capture_dir(home: Path) -> Path:
    capture_dir = home / ".claude" / "otel-raw-bodies"
    capture_dir.mkdir(parents=True, exist_ok=True)
    return capture_dir


def _write_capture_file(
    capture_dir: Path, name: str, days_ago: int, size_bytes: int = 10
) -> Path:
    path = capture_dir / name
    path.write_bytes(b"x" * size_bytes)
    _backdate(path, days_ago)
    return path


def _path_with_stat_emitting_malformed_size(farm_dir: Path, target_name: str) -> str:
    """Build a PATH whose `stat` emits a non-numeric size field for
    `target_name` only, when invoked with the hook's own `%m %z %N`
    size-pass format string -- standing in for a raced or truncated real
    stat read. Every other file in the same batched `stat -f ... {} +`
    call, and every other stat invocation (the hook's own `%z`-only
    post-prune tally), passes through to the real binary untouched.
    """
    farm_dir.mkdir(parents=True, exist_ok=True)
    real_stat = shutil.which("stat")
    wrapper = farm_dir / "stat"
    wrapper.write_text(
        "#!/bin/bash\n"
        'if [ "$1" = "-f" ] && [ "$2" = "%m %z %N" ]; then\n'
        "  shift 2\n"
        '  for f in "$@"; do\n'
        f'    if [ "$(basename "$f")" = "{target_name}" ]; then\n'
        '      echo "1700000000 notanumber $f"\n'
        "    else\n"
        f'      "{real_stat}" -f "%m %z %N" "$f"\n'
        "    fi\n"
        "  done\n"
        "else\n"
        f'  exec "{real_stat}" "$@"\n'
        "fi\n"
    )
    wrapper.chmod(0o755)
    return f"{farm_dir}{os.pathsep}{os.environ.get('PATH', '')}"


def _path_with_stat_omitting_one_file(farm_dir: Path, missing_name: str) -> str:
    """Build a PATH whose `stat` reproduces real BSD stat's actual
    partial-batch-failure shape (verified empirically: `stat -f '%m %z %N'
    present.txt missing.txt` prints a well-formed line for every file it
    can still see and only errors -- to stderr, exit 1 -- for the one that
    vanished, rather than corrupting that file's line or dropping the rest
    of the batch) -- the real race the hook's own size-pass comment
    describes ("A file stat can no longer see... drops out of the listing
    rather than aborting the walk"), as opposed to
    `_path_with_stat_emitting_malformed_size` above, which pins a
    different, non-race code path (a defensively-guarded corrupted field).
    `missing_name` is silently omitted from stdout; every other file in the
    same batched `stat -f ... {} +` call passes through to the real binary
    untouched.
    """
    farm_dir.mkdir(parents=True, exist_ok=True)
    real_stat = shutil.which("stat")
    wrapper = farm_dir / "stat"
    wrapper.write_text(
        "#!/bin/bash\n"
        'if [ "$1" = "-f" ] && [ "$2" = "%m %z %N" ]; then\n'
        "  shift 2\n"
        '  for f in "$@"; do\n'
        f'    if [ "$(basename "$f")" = "{missing_name}" ]; then\n'
        '      echo "stat: missing (simulated race)" >&2\n'
        "    else\n"
        f'      "{real_stat}" -f "%m %z %N" "$f"\n'
        "    fi\n"
        "  done\n"
        "else\n"
        f'  exec "{real_stat}" "$@"\n'
        "fi\n"
    )
    wrapper.chmod(0o755)
    return f"{farm_dir}{os.pathsep}{os.environ.get('PATH', '')}"


class TestPruneOtelRawBodies:
    # ------------------------------------------------------------------ #
    # Allow-path: age and size eviction                                   #
    # ------------------------------------------------------------------ #

    def test_age_pass_deletes_past_cap_keeps_recent(self, isolated_home):
        """A file aged past the 7-day cutoff (9 days) is deleted; one still
        inside it (5 days) survives -- pins assumption 6's verified `-mtime
        +6` boundary at the hook-behavior level, not just the source. A
        third fixture at exactly 7 days old is also deleted -- this is the
        actual `+6`-vs-`+7` discriminating point (a future edit that
        "corrects" +6 to +7 would retain this file for an extra day and
        this fixture is the only one of the three that would catch it);
        7 days carries a wide enough margin over the fixture-setup-to-
        subprocess-start gap that it doesn't hit the razor's-edge flakiness
        the module docstring's cutoff-2/cutoff+2 policy is guarding against."""
        capture_dir = _capture_dir(isolated_home)
        old = _write_capture_file(capture_dir, "old-uuid.request.json", days_ago=9)
        at_boundary = _write_capture_file(capture_dir, "boundary-uuid.request.json", days_ago=7)
        recent = _write_capture_file(capture_dir, "recent-uuid.response.json", days_ago=5)
        result = _run_hook(isolated_home)
        assert result.returncode == 0
        assert not old.exists()
        assert not at_boundary.exists()
        assert recent.exists()

    def test_size_pass_evicts_oldest_first_even_within_age_window(self, isolated_home):
        """Three files all well inside the 7-day age window (1-3 days old)
        are evicted oldest-first by the size pass alone once the total
        exceeds an injected ceiling -- the case an age-only bound would
        miss entirely."""
        capture_dir = _capture_dir(isolated_home)
        oldest = _write_capture_file(capture_dir, "a.request.json", days_ago=3, size_bytes=100)
        middle = _write_capture_file(capture_dir, "b.request.json", days_ago=2, size_bytes=100)
        newest = _write_capture_file(capture_dir, "c.request.json", days_ago=1, size_bytes=100)
        result = _run_hook(isolated_home, extra_env={"OTEL_PRUNE_MAX_BYTES": "250"})
        assert result.returncode == 0
        assert not oldest.exists()
        assert middle.exists()
        assert newest.exists()

    # ------------------------------------------------------------------ #
    # Deny-path: directory guard                                          #
    # ------------------------------------------------------------------ #

    def test_directory_absent_is_noop(self, isolated_home):
        """No capture directory -- exit 0, silent, and the hook does not
        create one itself (arming, not pruning, owns that decision)."""
        result = _run_hook(isolated_home)
        assert result.returncode == 0
        assert result.stdout == ""
        assert not (isolated_home / ".claude" / "otel-raw-bodies").exists()

    def test_symlinked_capture_directory_is_not_followed(self, isolated_home):
        """A symlink at the capture-directory path is refused outright,
        mirroring track-permission-prompts.sh's own write-target symlink
        refusal -- the walk never reaches whatever the symlink points at."""
        elsewhere = isolated_home / "elsewhere"
        elsewhere.mkdir()
        canary = elsewhere / "canary.request.json"
        canary.write_bytes(b"x")
        _backdate(canary, 30)
        claude_dir = isolated_home / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "otel-raw-bodies").symlink_to(elsewhere)
        result = _run_hook(isolated_home)
        assert result.returncode == 0
        assert canary.exists()

    # ------------------------------------------------------------------ #
    # Deny-path: blast-radius scoping                                     #
    # ------------------------------------------------------------------ #

    def test_files_outside_capture_directory_survive(self, isolated_home):
        """An aged-out canary in $HOME and in a sibling directory both
        survive a normal run -- the walk never leaves the pinned path."""
        capture_dir = _capture_dir(isolated_home)
        _write_capture_file(capture_dir, "old.request.json", days_ago=9)
        home_canary = isolated_home / "old-home-file.request.json"
        home_canary.write_bytes(b"x")
        _backdate(home_canary, 30)
        sibling_dir = isolated_home / ".claude" / "otel-raw-bodies-sibling"
        sibling_dir.mkdir(parents=True)
        sibling_canary = sibling_dir / "old.request.json"
        sibling_canary.write_bytes(b"x")
        _backdate(sibling_canary, 30)
        result = _run_hook(isolated_home)
        assert result.returncode == 0
        assert home_canary.exists()
        assert sibling_canary.exists()

    def test_non_matching_filename_survives_regardless_of_age(self, isolated_home):
        """A file inside the capture directory that doesn't match either
        vendor filename shape survives, no matter how old."""
        capture_dir = _capture_dir(isolated_home)
        stray = _write_capture_file(capture_dir, "stray.txt", days_ago=30)
        result = _run_hook(isolated_home)
        assert result.returncode == 0
        assert stray.exists()

    # ------------------------------------------------------------------ #
    # Deny-path: one file fails, the rest still prune                     #
    # ------------------------------------------------------------------ #

    @pytest.mark.skipif(
        sys.platform != "darwin",
        reason="chflags uchg is macOS/BSD-specific -- this hook's only "
        "verified environment (plan assumption 7); no non-root portable "
        "equivalent exists on Linux CI.",
    )
    def test_unremovable_file_mid_pass_does_not_abort_other_deletions(self, isolated_home):
        """A file the hook can't delete (simulated via the macOS immutable
        flag, standing in for a permission race) doesn't stop the walk --
        the hook still exits 0 and still prunes every other eligible
        file."""
        capture_dir = _capture_dir(isolated_home)
        locked = _write_capture_file(capture_dir, "locked-uuid.request.json", days_ago=9)
        other = _write_capture_file(capture_dir, "other-uuid.request.json", days_ago=9)
        subprocess.run(["chflags", "uchg", str(locked)], check=True)
        try:
            result = _run_hook(isolated_home)
            assert result.returncode == 0
            assert locked.exists()
            assert not other.exists()
        finally:
            subprocess.run(["chflags", "nouchg", str(locked)], check=True)

    def test_malformed_stat_size_field_does_not_abort_the_hook(self, isolated_home):
        """Defensive-guard unit check, not a race simulation: real stat
        never emits a line with a corrupted field for a file it can name
        (see test_missing_stat_line_mid_batch_does_not_abort_the_hook below
        for the actual race shape). This pins _is_size_field's own
        behavior directly -- a stat line whose size field isn't a plain
        integer is skipped rather than fed into arithmetic, where under
        set -u `$((TOTAL + SIZE))` with a non-numeric SIZE would abort the
        whole hook non-zero. The malformed entry is invisible to size
        accounting and survives unconditionally; a well-formed sibling in
        the same batched stat call is still evaluated and evicted
        normally."""
        capture_dir = _capture_dir(isolated_home)
        malformed = _write_capture_file(
            capture_dir, "malformed-uuid.request.json", days_ago=1, size_bytes=100
        )
        healthy = _write_capture_file(
            capture_dir, "healthy-uuid.request.json", days_ago=1, size_bytes=100
        )
        farm_dir = isolated_home / "stat-shim"
        path_with_shim = _path_with_stat_emitting_malformed_size(farm_dir, malformed.name)
        result = _run_hook(
            isolated_home,
            extra_env={"PATH": path_with_shim, "OTEL_PRUNE_MAX_BYTES": "1"},
        )
        assert result.returncode == 0
        assert malformed.exists()
        assert not healthy.exists()

    def test_missing_stat_line_mid_batch_does_not_abort_the_hook(self, isolated_home):
        """The actual race the hook's size-pass comment describes: a file
        present in find's snapshot is gone by the time the batched
        `stat -f ... {} +` call reaches it, so its line is silently absent
        from stdout (real BSD stat's verified behavior for a missing file
        mid-batch -- stderr-only, exit 1, every other file's line still
        printed). The missing file is invisible to size accounting and
        survives unconditionally; a well-formed sibling in the same batch
        is still evaluated and evicted normally."""
        capture_dir = _capture_dir(isolated_home)
        raced_away = _write_capture_file(
            capture_dir, "raced-away-uuid.request.json", days_ago=1, size_bytes=100
        )
        healthy = _write_capture_file(
            capture_dir, "healthy-uuid.request.json", days_ago=1, size_bytes=100
        )
        farm_dir = isolated_home / "stat-omit-shim"
        path_with_shim = _path_with_stat_omitting_one_file(farm_dir, raced_away.name)
        result = _run_hook(
            isolated_home,
            extra_env={"PATH": path_with_shim, "OTEL_PRUNE_MAX_BYTES": "1"},
        )
        assert result.returncode == 0
        assert raced_away.exists()
        assert not healthy.exists()

    # ------------------------------------------------------------------ #
    # Production ceiling literal                                          #
    # ------------------------------------------------------------------ #

    def test_production_ceiling_literal_is_5_gib(self, isolated_home):
        """With no OTEL_PRUNE_MAX_BYTES override, the reported ceiling is
        5120 MiB (5 GiB) -- pins the real production literal end-to-end so
        an injected test ceiling elsewhere can't hide a units error here."""
        _capture_dir(isolated_home)
        result = _run_hook(isolated_home)
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert f"ceiling {PRODUCTION_CEILING_MIB} MiB" in payload["systemMessage"]

    # ------------------------------------------------------------------ #
    # OTEL_PRUNE_MAX_BYTES fallback                                       #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "bad_value", ["not-a-number", "-100", "", "0", "3.5"], ids=["non-numeric", "negative", "empty", "zero", "float"]
    )
    def test_invalid_override_falls_back_to_production_ceiling(self, isolated_home, bad_value):
        """A non-positive-integer OTEL_PRUNE_MAX_BYTES (typo, negative,
        empty, zero, float) falls back to the production literal rather
        than deleting everything or nothing."""
        _capture_dir(isolated_home)
        result = _run_hook(isolated_home, extra_env={"OTEL_PRUNE_MAX_BYTES": bad_value})
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert f"ceiling {PRODUCTION_CEILING_MIB} MiB" in payload["systemMessage"]

    def test_valid_override_is_honored(self, isolated_home):
        """A well-formed OTEL_PRUNE_MAX_BYTES actually changes the reported
        ceiling -- confirms the fallback tests above are exercising real
        validation, not an override path that never wires through."""
        _capture_dir(isolated_home)
        result = _run_hook(isolated_home, extra_env={"OTEL_PRUNE_MAX_BYTES": "1048576"})
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert "ceiling 1 MiB" in payload["systemMessage"]
