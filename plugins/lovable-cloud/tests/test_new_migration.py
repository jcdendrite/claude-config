"""Tests for plugins/lovable-cloud/scripts/new-migration."""
from __future__ import annotations

import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

WORKTREE_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PLUGIN_ROOT = WORKTREE_ROOT / "plugins" / "lovable-cloud"
GENERATOR = PLUGIN_ROOT / "scripts" / "new-migration"


def _run_generator(slug: str, home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(GENERATOR), slug],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
    )


def _token_dir(home: Path) -> Path:
    return home / ".claude" / "lovable-cloud" / "migration-tokens"


class TestNewMigrationOutput:
    def test_normal_slug_emits_correct_filename(self, tmp_path):
        result = _run_generator("add co-parent index", tmp_path)
        assert result.returncode == 0, result.stderr
        filename = result.stdout.strip()
        assert re.match(r"^\d{14}_add-co-parent-index\.sql$", filename), (
            f"Unexpected filename: {filename!r}"
        )

    def test_messy_slug_sanitizes_cleanly(self, tmp_path):
        result = _run_generator("Fix RPC error_codes!!", tmp_path)
        assert result.returncode == 0, result.stderr
        filename = result.stdout.strip()
        slug_part = filename.split("_", 1)[1].replace(".sql", "")
        assert re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", slug_part), (
            f"Slug part has leading/trailing/double hyphens or invalid chars: {slug_part!r}"
        )
        assert not slug_part.startswith("-"), "slug starts with hyphen"
        assert not slug_part.endswith("-"), "slug ends with hyphen"
        assert "--" not in slug_part, "slug contains consecutive hyphens"

    def test_unicode_slug_stripped_by_c_locale(self, tmp_path):
        # LC_ALL=C strips non-ASCII bytes; "café migration" → "caf-migration"
        result = _run_generator("café migration", tmp_path)
        assert result.returncode == 0, result.stderr
        filename = result.stdout.strip()
        slug_part = filename.split("_", 1)[1].replace(".sql", "")
        # The é byte is stripped, space becomes hyphen → caf-migration
        assert slug_part == "caf-migration", (
            f"Expected 'caf-migration' from LC_ALL=C stripping, got {slug_part!r}"
        )

    def test_empty_sanitized_slug_exits_nonzero(self, tmp_path):
        result = _run_generator("!!!", tmp_path)
        assert result.returncode != 0, "Expected non-zero exit for empty-sanitized slug"
        assert not result.stdout.strip(), (
            f"Expected no filename on stdout for empty slug, got {result.stdout!r}"
        )
        assert result.stderr.strip(), "Expected error message on stderr"

    def test_empty_sanitized_slug_writes_no_token(self, tmp_path):
        _run_generator("!!!", tmp_path)
        token_dir = _token_dir(tmp_path)
        # No tokens should exist — the dir may not even be created.
        if token_dir.exists():
            assert list(token_dir.iterdir()) == [], (
                "No token files should exist for a rejected slug"
            )

    def test_timestamp_is_utc(self, tmp_path):
        before_utc = datetime.now(UTC)
        result = _run_generator("timestamp-check", tmp_path)
        after_utc = datetime.now(UTC)
        assert result.returncode == 0, result.stderr
        filename = result.stdout.strip()
        ts_str = filename[:14]
        ts = datetime.strptime(ts_str, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        # The timestamp must fall within a 5-second window of UTC time.
        before_floor = before_utc.replace(microsecond=0)
        assert before_floor <= ts <= after_utc, (
            f"Timestamp {ts_str} is outside UTC window [{before_floor}, {after_utc}]"
        )


class TestNewMigrationTokenSideEffect:
    def test_token_written_at_emitted_basename(self, tmp_path):
        result = _run_generator("add co-parent index", tmp_path)
        assert result.returncode == 0, result.stderr
        filename = result.stdout.strip()
        token_path = _token_dir(tmp_path) / filename
        assert token_path.exists(), (
            f"Token not written at {token_path}"
        )

    def test_same_second_same_slug_double_gen_is_idempotent(self, tmp_path):
        # Two generator calls with the same slug at the same UTC second must
        # produce the same filename and leave exactly one token file — the
        # second touch() is a no-op. Freeze time via a fake `date` binary
        # that always returns a fixed timestamp, so the test is deterministic
        # regardless of wall-clock speed.
        fake_bin = tmp_path / "fakebin"
        fake_bin.mkdir()
        fake_date = fake_bin / "date"
        fake_date.write_text("#!/bin/bash\nprintf '20260612120000\\n'\n")
        fake_date.chmod(0o755)

        slug = "idempotent-test"
        env_with_fake_date = {
            **os.environ,
            "HOME": str(tmp_path),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        }
        result1 = subprocess.run(
            [str(GENERATOR), slug], capture_output=True, text=True, env=env_with_fake_date
        )
        result2 = subprocess.run(
            [str(GENERATOR), slug], capture_output=True, text=True, env=env_with_fake_date
        )
        assert result1.returncode == 0, result1.stderr
        assert result2.returncode == 0, result2.stderr
        filename1 = result1.stdout.strip()
        filename2 = result2.stdout.strip()

        # Same second, same slug → identical filenames.
        assert filename1 == filename2, (
            f"Same-second calls must produce the same filename: {filename1!r} vs {filename2!r}"
        )
        # Exactly one token file — the second touch() was a no-op, not a new file.
        token_dir = _token_dir(tmp_path)
        token_files = list(token_dir.iterdir())
        assert len(token_files) == 1, f"Expected 1 token, got: {[f.name for f in token_files]}"
        assert token_files[0].name == filename1
