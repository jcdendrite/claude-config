"""Tests for migrate-legacy-config.sh -- the Phase 3 script that imports
every legacy sentinel file into claude-config.toml, then offers per-file
interactive deletion. Drives the real script directly via subprocess.run
(row 35's own test-seam rationale: a real call-sequence exercise, not
install.sh's INSTALL_TEST_FIXTURE block-extraction harness, which exists
only because install.sh itself isn't decomposable).

The union-semantics and row-19 legacy-precedence *math* are not retested
here (row 33) -- those live in test_config_lib.py as direct
_config_enabled/_config_value fixtures. This file covers what's actually
migration-script-specific: import-on-first-encounter, the
import-before-scaffold ordering (row 34), the row-45/46 gating machinery,
the interactive delete-confirmation phase (row 42), and the
pr_cost_disclosure two-legacy-location precedence tiebreak (row 31/41).

CONFIG_KEYS_PSV and MIGRATE_SCRIPT below are declared as module-level path
constants (not inline strings) so TestCrossDomainReadCompleteness
(test_select_tests.py) can see this file's dependency on them.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, SCRIPTS_DIR

sys.path.insert(0, str(Path(__file__).parent.parent))
from _config import SchemaRow, schema  # noqa: E402

MIGRATE_SCRIPT = SCRIPTS_DIR / "migrate-legacy-config.sh"
CONFIG_KEYS_PSV = HOOKS_DIR / "config-keys.psv"

_ENFORCEMENT_CRITICAL_KEYS = frozenset({
    "worktree_required",
    "autonomous_shipping",
    "round_consult_gate",
    "commit_stall_block",
    "authorization_boundary_restore",
})

_SCHEMA = schema()
_ALL_KEYS = sorted(_SCHEMA)


def _env(home: Path, *, config_dir: Path | None = None, unset_config_dir: bool = True) -> dict:
    """A subprocess environment with HOME pinned and CLAUDE_CONFIG_DIR
    either explicitly set or explicitly removed -- never left to whatever
    this test process's own ambient value happens to be (the scripts/tests
    suite's autouse _isolate_transcript_corpus_lookups fixture already
    pins CLAUDE_CONFIG_DIR to a per-test tmp dir at the os.environ level,
    which this function's explicit pop/set always overrides)."""
    env = dict(os.environ)
    env["HOME"] = str(home)
    if config_dir is not None:
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    elif unset_config_dir:
        env.pop("CLAUDE_CONFIG_DIR", None)
    return env


def _run(env: dict, stdin: str | None = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(MIGRATE_SCRIPT)], input=stdin, capture_output=True, text=True, env=env, check=False
    )


def _state_file(config_dir: Path) -> Path:
    return config_dir / "claude-config.toml"


def _read_state(config_dir: Path) -> dict[str, str]:
    """Parses claude-config.toml's `key = value` lines -- a test-local
    parser, not _config.sh's/_config.py's own, since asserting against the
    production parser's own output would make this file's assertions
    circular."""
    path = _state_file(config_dir)
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


def _legacy_derived_value(row: SchemaRow) -> str:
    if row.legacy_polarity == "presence-enables":
        return "true"
    if row.legacy_polarity == "presence-disables":
        return "false"
    return row.type.removeprefix("enum:")  # content-matches


def _distinguishing_existing_value(row: SchemaRow) -> str:
    """A valid value for ROW's own type that differs from what its own
    legacy file would derive, so a test asserting a pre-existing row
    survives untouched can tell "left alone" apart from "re-imported to
    the same value" -- a bool key's default and its own legacy-derived
    value are always each other's complement, so "opposite of default"
    would not distinguish; "opposite of legacy-derived" always does."""
    legacy_value = _legacy_derived_value(row)
    if row.type == "bool":
        return "false" if legacy_value == "true" else "true"
    return "false"  # pr_cost_disclosure's only other valid (enum:dollars) value


def _write_legacy_file(directory: Path, row: SchemaRow, present: bool) -> None:
    if not present:
        return
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / row.legacy_filename
    if row.legacy_polarity == "content-matches":
        path.write_text(row.type.removeprefix("enum:") + "\n")
    else:
        path.touch()


# ---------------------------------------------------------------------------
# The 6-cell-per-key migration matrix (Phase 3 test-plan bullet 1)
# ---------------------------------------------------------------------------


class TestMigrationMatrixStateFileAbsent:
    """State file entirely absent, crossed with legacy-file-present. This is
    the cell that would fail under a scaffold-before-import regression
    (row 34): scaffold would populate every key's schema default before
    import ever runs, so import's own "no existing row" trigger would
    never fire."""

    @pytest.mark.parametrize("key", _ALL_KEYS)
    @pytest.mark.parametrize("legacy_present", [True, False], ids=["legacy-present", "legacy-absent"])
    def test_import_or_default(self, key: str, legacy_present: bool, tmp_path: Path) -> None:
        row = _SCHEMA[key]
        home = tmp_path / "home"
        config_dir = home / ".claude"
        config_dir.mkdir(parents=True)
        _write_legacy_file(config_dir, row, legacy_present)

        result = _run(_env(home))
        assert result.returncode == 0, f"stderr={result.stderr!r}"

        state = _read_state(config_dir)
        if not legacy_present:
            assert state.get(key) == row.default
        elif key in _ENFORCEMENT_CRITICAL_KEYS:
            assert key not in state, (
                "a non-TTY run must defer an enforcement-critical key's import (row 45), "
                f"not silently import it: state={state!r}"
            )
        else:
            assert state.get(key) == _legacy_derived_value(row)


class TestMigrationMatrixStateFilePresentKeyRowAbsent:
    """State file present (holding a different key's row), this key's own
    row absent -- disambiguates "file entirely absent" from "key absent
    from a present file"."""

    @pytest.mark.parametrize("key", _ALL_KEYS)
    @pytest.mark.parametrize("legacy_present", [True, False], ids=["legacy-present", "legacy-absent"])
    def test_import_or_default(self, key: str, legacy_present: bool, tmp_path: Path) -> None:
        row = _SCHEMA[key]
        home = tmp_path / "home"
        config_dir = home / ".claude"
        config_dir.mkdir(parents=True)
        other_key = next(k for k in _ALL_KEYS if k != key)
        _state_file(config_dir).write_text(f"{other_key} = {_SCHEMA[other_key].default}\n")
        _write_legacy_file(config_dir, row, legacy_present)

        result = _run(_env(home))
        assert result.returncode == 0, f"stderr={result.stderr!r}"

        state = _read_state(config_dir)
        assert state.get(other_key) == _SCHEMA[other_key].default, "an unrelated pre-existing row must survive"
        if not legacy_present:
            assert state.get(key) == row.default
        elif key in _ENFORCEMENT_CRITICAL_KEYS:
            assert key not in state
        else:
            assert state.get(key) == _legacy_derived_value(row)


class TestMigrationMatrixStateFilePresentKeyRowPresent:
    """State file present with this key's own row already populated -- row
    22: never touched via the legacy path again, whether that row came
    from a hand-edit, a prior import, or predates any run of this script.
    Asserted via the exact authored line surviving verbatim, not a value
    comparison (see _distinguishing_existing_value)."""

    @pytest.mark.parametrize("key", _ALL_KEYS)
    @pytest.mark.parametrize("legacy_present", [True, False], ids=["legacy-present", "legacy-absent"])
    def test_existing_row_survives(self, key: str, legacy_present: bool, tmp_path: Path) -> None:
        row = _SCHEMA[key]
        home = tmp_path / "home"
        config_dir = home / ".claude"
        config_dir.mkdir(parents=True)
        existing_value = _distinguishing_existing_value(row)
        authored_line = f"{key} = {existing_value}"
        _state_file(config_dir).write_text(authored_line + "\n")
        _write_legacy_file(config_dir, row, legacy_present)

        result = _run(_env(home))
        assert result.returncode == 0, f"stderr={result.stderr!r}"

        new_lines = _state_file(config_dir).read_text().splitlines()
        assert authored_line in new_lines, (
            f"a pre-existing row for {key!r} must survive untouched regardless of a legacy file: {new_lines!r}"
        )


# ---------------------------------------------------------------------------
# Hand-edit survives a re-run (row 22)
# ---------------------------------------------------------------------------


class TestHandEditSurvivesRerun:
    def test_second_run_does_not_revert_a_hand_edit_made_between_runs(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        config_dir = home / ".claude"
        config_dir.mkdir(parents=True)

        first = _run(_env(home))
        assert first.returncode == 0, f"stderr={first.stderr!r}"
        assert _read_state(config_dir)["commit_stall_block"] == "true"

        text = _state_file(config_dir).read_text()
        text = text.replace("commit_stall_block = true", "commit_stall_block = false")
        _state_file(config_dir).write_text(text)

        second = _run(_env(home))
        assert second.returncode == 0, f"stderr={second.stderr!r}"
        assert _read_state(config_dir)["commit_stall_block"] == "false", (
            "a hand-edit made between two runs must survive the second run"
        )

    def test_pre_first_run_hand_authored_row_survives_first_run(self, tmp_path: Path) -> None:
        """Row 22's guarantee is per-key-encounter, not per-run-number: a
        row authored before this script has ever run once must still
        survive that first run, and every other key must still import or
        scaffold normally alongside it."""
        home = tmp_path / "home"
        config_dir = home / ".claude"
        config_dir.mkdir(parents=True)
        _state_file(config_dir).write_text("round_consult_gate = false\n")
        (config_dir / ".round-consult-gate-disabled").touch()  # would derive "false" too if (wrongly) read

        result = _run(_env(home))
        assert result.returncode == 0, f"stderr={result.stderr!r}"

        state = _read_state(config_dir)
        assert state["round_consult_gate"] == "false"
        assert state["commit_stall_block"] == "true", "other keys must still scaffold normally"


# ---------------------------------------------------------------------------
# pr_cost_disclosure's two-legacy-location precedence (row 31/41)
# ---------------------------------------------------------------------------


class TestPrCostDisclosureTwoLocationPrecedence:
    def test_resolved_config_dir_wins_on_disagreement(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        config_dir = tmp_path / "other-config-dir"
        (home / ".claude").mkdir(parents=True)
        config_dir.mkdir(parents=True)
        (home / ".claude" / "pr-cost-disclosure").write_text("garbled-not-dollars\n")
        (config_dir / "pr-cost-disclosure").write_text("dollars\n")

        result = _run(_env(home, config_dir=config_dir))
        assert result.returncode == 0, f"stderr={result.stderr!r}"

        assert _read_state(config_dir)["pr_cost_disclosure"] == "dollars"


# ---------------------------------------------------------------------------
# Direct-invocation interactive tests (delete-confirmation, row 42) -- no
# pty/pexpect: source the script (its BASH_SOURCE guard skips main), then
# call a helper function directly with piped stdin, bypassing its own
# `[ -t 0 ]` gate the same way test_install_sh_stale_migration_copy_cleanup.py
# does for _prompt_delete_stale_migration_copy.
# ---------------------------------------------------------------------------


def _run_sourced(script_body: str, stdin: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. "{MIGRATE_SCRIPT}"\n{script_body}'],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


class TestDeleteConfirmationPrompt:
    def test_confirmed_yes_deletes_the_file(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        legacy_file = tmp_path / "legacy" / "some-file"
        legacy_file.parent.mkdir(parents=True)
        legacy_file.touch()

        result = _run_sourced(
            f'_migrate_prompt_delete_legacy_file "{legacy_file}" autonomous_shipping true imported\n',
            "y\n",
            _env(home),
        )
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert not legacy_file.exists()

    def test_declined_leaves_the_file_in_place(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        legacy_file = tmp_path / "legacy" / "some-file"
        legacy_file.parent.mkdir(parents=True)
        legacy_file.touch()

        result = _run_sourced(
            f'_migrate_prompt_delete_legacy_file "{legacy_file}" autonomous_shipping true imported\n',
            "n\n",
            _env(home),
        )
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert legacy_file.exists()

    def test_non_tty_calling_loop_leaves_every_legacy_file_in_place(self, tmp_path: Path) -> None:
        """The calling loop (main's own delete phase), not the prompt
        function directly -- a closed/empty stdin is never a TTY regardless
        of what it carries, matching test_install_sh_machine_level_opt_ins.py's
        identical technique for its own TTY-gate tests."""
        home = tmp_path / "home"
        config_dir = home / ".claude"
        config_dir.mkdir(parents=True)
        (config_dir / "autonomous-shipping-required").touch()

        result = _run(_env(home), stdin="")
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert (config_dir / "autonomous-shipping-required").exists()

    def test_key_not_imported_this_run_is_never_offered(self, tmp_path: Path) -> None:
        """A key that already had a state-file row before this run (so its
        legacy file's own derived value was never used) must not be offered
        for deletion -- proven with autonomous_shipping specifically, not
        worktree_required, so this condition is exercised independently of
        worktree_required's own unconditional exclusion."""
        home = tmp_path / "home"
        config_dir = home / ".claude"
        config_dir.mkdir(parents=True)
        _state_file(config_dir).write_text("autonomous_shipping = false\n")
        legacy_file = config_dir / "autonomous-shipping-required"
        legacy_file.touch()

        records_file = tmp_path / "records.psv"
        env = _env(home, config_dir=config_dir)
        env["MIGRATE_LEGACY_CONFIG_DEBUG_RECORDS_FILE"] = str(records_file)
        result = _run(env, stdin="")
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert legacy_file.exists(), "a key with an existing row must never be offered for deletion"
        records = records_file.read_text()
        assert "skipped-existing-row(false)" in records

    def test_home_worktree_required_is_never_offered_even_with_tty_and_yes_response(
        self, tmp_path: Path
    ) -> None:
        """The non-TTY case is already covered generically by
        test_non_tty_calling_loop_leaves_every_legacy_file_in_place -- the
        case that actually exercises worktree_required's own load-bearing
        exclusion is a TTY-present run that would otherwise say "y" to
        deleting it. Drives _migrate_run_delete_confirmation_phase directly
        (not _migrate_prompt_delete_legacy_file, which has no load-bearing
        awareness of its own -- the caller's record-filtering loop is what
        excludes it) with a manually-constructed load-bearing record, since
        a real TTY can't be simulated without a pty (see this file's own
        header)."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        legacy_file = home / ".claude" / "worktree-required"
        legacy_file.touch()

        script = (
            '_MIGRATE_TTY=1\n'
            f'_MIGRATE_RECORDS=("{legacy_file}|worktree_required|true|imported|true")\n'
            '_migrate_run_delete_confirmation_phase\n'
        )
        result = _run_sourced(script, "y\n", _env(home))
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert legacy_file.exists(), (
            "$HOME/.claude/worktree-required must never be offered for deletion, even with a TTY and a 'y' response"
        )

    def test_lost_precedence_file_shows_outcome_in_debug_record(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        config_dir = tmp_path / "other-config-dir"
        (home / ".claude").mkdir(parents=True)
        config_dir.mkdir(parents=True)
        (home / ".claude" / "pr-cost-disclosure").write_text("\n")  # derives "false"
        (config_dir / "pr-cost-disclosure").write_text("dollars\n")

        records_file = tmp_path / "records.psv"
        env = _env(home, config_dir=config_dir)
        env["MIGRATE_LEGACY_CONFIG_DEBUG_RECORDS_FILE"] = str(records_file)
        result = _run(env, stdin="")
        assert result.returncode == 0, f"stderr={result.stderr!r}"

        records = records_file.read_text()
        assert "lost-precedence(dollars)" in records

    def test_row_45_declined_import_reaches_delete_phase_as_deferred(self, tmp_path: Path) -> None:
        """A TTY-present run that declines row 45's import prompt for an
        enforcement-critical key must record deferred-pending-confirmation
        for that file, and the delete phase must never offer it."""
        home = tmp_path / "home"
        config_dir = home / ".claude"
        config_dir.mkdir(parents=True)
        legacy_file = config_dir / ".round-consult-gate-disabled"
        legacy_file.touch()

        records_file = tmp_path / "records.psv"
        script = (
            '_MIGRATE_TTY=1\n'
            f'_MIGRATE_CONFIG_DIR="{config_dir}"\n'
            f'_MIGRATE_HOME_DIR="{config_dir}"\n'
            '_MIGRATE_STATE_FILE="$_MIGRATE_CONFIG_DIR/claude-config.toml"\n'
            '_MIGRATE_SCAFFOLD_EXCLUDE=""\n'
            '_MIGRATE_RECORDS=()\n'
            '_migrate_process_key round_consult_gate bool config-dir '
            '.round-consult-gate-disabled presence-disables '
            '"Round-3 architect-consult gate"\n'
            '_config_scaffold "$_MIGRATE_SCAFFOLD_EXCLUDE"\n'
            f'printf \'%s\\n\' "${{_MIGRATE_RECORDS[@]}}" > "{records_file}"\n'
        )
        result = _run_sourced(script, "n\n", _env(home, config_dir=config_dir))
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "round_consult_gate" not in _read_state(config_dir), (
            "a declined enforcement-critical import must leave the key absent after scaffold, not just after import"
        )
        assert "deferred-pending-confirmation" in records_file.read_text()

    def test_row_46_read_failure_reaches_delete_phase_as_deferred(self, tmp_path: Path) -> None:
        """A TTY-present run that hits a row-46 read failure on a
        non-enforcement-critical key's legacy file must also record
        deferred-pending-confirmation, per round 6's widened scope."""
        home = tmp_path / "home"
        config_dir = home / ".claude"
        config_dir.mkdir(parents=True)
        legacy_file = config_dir / "pr-cost-disclosure"
        legacy_file.write_text("garbled-not-dollars\n")

        records_file = tmp_path / "records.psv"
        env = _env(home, config_dir=config_dir)
        env["MIGRATE_LEGACY_CONFIG_DEBUG_RECORDS_FILE"] = str(records_file)
        result = _run(env, stdin="y\n")
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "pr_cost_disclosure" not in _read_state(config_dir)
        assert "deferred-pending-confirmation" in records_file.read_text()


# ---------------------------------------------------------------------------
# Row 45: enforcement-critical import gating, one direct-invocation case
# per key
# ---------------------------------------------------------------------------


def _row_45_script(config_dir: Path, key: str, records_file: Path) -> str:
    """KEY's own schema-declared legacy_import_locations, not a hardcoded
    literal -- three of the five enforcement-critical keys
    (commit_stall_block, round_consult_gate, authorization_boundary_restore)
    are actually "config-dir" only, not "config-dir-and-home" like
    worktree_required/autonomous_shipping, so a hardcoded value here would
    silently test a call shape _migrate_process_key never actually receives
    for those three keys in production."""
    row = _SCHEMA[key]
    return (
        '_MIGRATE_TTY=1\n'
        f'_MIGRATE_CONFIG_DIR="{config_dir}"\n'
        f'_MIGRATE_HOME_DIR="{config_dir}"\n'
        '_MIGRATE_STATE_FILE="$_MIGRATE_CONFIG_DIR/claude-config.toml"\n'
        '_MIGRATE_SCAFFOLD_EXCLUDE=""\n'
        '_MIGRATE_RECORDS=()\n'
        f'_migrate_process_key {key} {row.type} {row.legacy_import_locations} '
        f'{row.legacy_filename} {row.legacy_polarity} "{row.human_name}"\n'
        '_config_scaffold "$_MIGRATE_SCAFFOLD_EXCLUDE"\n'
        f'printf \'%s\\n\' "${{_MIGRATE_RECORDS[@]}}" > "{records_file}"\n'
    )


class TestEnforcementCriticalImportGating:
    """Row 45: for each of the five enforcement-critical keys, the import
    decision is `[ -t 0 ]`-gated and defaults to No -- exercised via direct
    invocation (source the script, set the TTY flag manually, call the
    processing function), not a pty, per this file's own header."""

    @pytest.mark.parametrize("key", sorted(_ENFORCEMENT_CRITICAL_KEYS))
    def test_non_tty_invocation_leaves_key_absent_after_full_run_including_scaffold(
        self, key: str, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        config_dir = home / ".claude"
        config_dir.mkdir(parents=True)
        row = _SCHEMA[key]
        (config_dir / row.legacy_filename).touch()

        result = _run(_env(home, config_dir=config_dir), stdin=None)
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        state = _read_state(config_dir)
        assert key not in state, (
            f"{key} must stay absent after the full run (import deferred, scaffold excluded it): {state!r}"
        )

    @pytest.mark.parametrize("key", sorted(_ENFORCEMENT_CRITICAL_KEYS))
    def test_direct_invoked_y_writes_the_imported_value(self, key: str, tmp_path: Path) -> None:
        home = tmp_path / "home"
        config_dir = home / ".claude"
        config_dir.mkdir(parents=True)
        row = _SCHEMA[key]
        (config_dir / row.legacy_filename).touch()
        records_file = tmp_path / "records.psv"

        result = _run_sourced(_row_45_script(config_dir, key, records_file), "y\n", _env(home, config_dir=config_dir))
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert _read_state(config_dir)[key] == _legacy_derived_value(row)

    @pytest.mark.parametrize("key", sorted(_ENFORCEMENT_CRITICAL_KEYS))
    @pytest.mark.parametrize("answer", ["n\n", ""], ids=["declined", "eof"])
    def test_direct_invoked_n_or_eof_leaves_key_absent_after_full_run(
        self, key: str, answer: str, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        config_dir = home / ".claude"
        config_dir.mkdir(parents=True)
        row = _SCHEMA[key]
        (config_dir / row.legacy_filename).touch()
        records_file = tmp_path / "records.psv"

        result = _run_sourced(_row_45_script(config_dir, key, records_file), answer, _env(home, config_dir=config_dir))
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert key not in _read_state(config_dir), (
            "scaffold must not backfill a default over a deferred enforcement-critical key"
        )


# ---------------------------------------------------------------------------
# Row 46: per-key legacy-file read-failure isolation, run twice
# ---------------------------------------------------------------------------


class TestPerKeyFailureIsolation:
    """A single key's legacy-file read failure does not abort the script,
    and does not block any other key's own import/scaffold -- run once for
    a non-enforcement-critical key (pr_cost_disclosure, row 46's own worked
    example) and once for an enforcement-critical one, closing the
    exclude-list generalization gap each of those two `/plan-review`
    rounds independently found."""

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root bypasses discretionary file-permission bits (CAP_DAC_OVERRIDE on Linux), "
        "so chmod(0o000) does not make the file unreadable and the read would not fail",
    )
    def test_pr_cost_disclosure_unrecognized_content_isolated(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        config_dir = home / ".claude"
        config_dir.mkdir(parents=True)
        (config_dir / "pr-cost-disclosure").write_text("garbled-not-dollars\n")

        result = _run(_env(home, config_dir=config_dir), stdin="")
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        state = _read_state(config_dir)
        assert "pr_cost_disclosure" not in state
        # The other 13 keys still import or scaffold correctly.
        assert state["commit_stall_block"] == "true"
        assert state["worktree_required"] == "false"

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root bypasses discretionary file-permission bits (CAP_DAC_OVERRIDE on Linux), "
        "so chmod(0o000) does not make the file unreadable and the read would not fail",
    )
    def test_enforcement_critical_key_unreadable_legacy_file_isolated(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        config_dir = home / ".claude"
        config_dir.mkdir(parents=True)
        legacy_file = config_dir / ".round-consult-gate-disabled"
        legacy_file.touch()
        legacy_file.chmod(0o000)

        try:
            result = _run(_env(home, config_dir=config_dir), stdin="")
        finally:
            legacy_file.chmod(0o644)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        state = _read_state(config_dir)
        assert "round_consult_gate" not in state, (
            "an unreadable legacy file must exclude the key after the full run including scaffold, not just import"
        )
        assert state["commit_stall_block"] == "true"


# ---------------------------------------------------------------------------
# Schema-row validation: an out-of-subset legacy-import-locations value is
# rejected loudly, not silently defaulted
# ---------------------------------------------------------------------------


class TestSchemaRowValidation:
    def test_unrecognized_legacy_import_locations_value_warns_and_falls_back_safely(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        result = _run_sourced(
            '_MIGRATE_TTY=0\n'
            f'_MIGRATE_CONFIG_DIR="{home}/.claude"\n'
            f'_MIGRATE_HOME_DIR="{home}/.claude"\n'
            'mkdir -p "$_MIGRATE_CONFIG_DIR"\n'
            'touch "$_MIGRATE_CONFIG_DIR/worktree-required"\n'
            '_MIGRATE_SCAFFOLD_EXCLUDE=""\n'
            '_MIGRATE_RECORDS=()\n'
            '_migrate_process_key worktree_required bool bogus-value worktree-required '
            'presence-enables "Worktree enforcement"\n',
            "",
            _env(home),
        )
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "unrecognized legacy-import-locations value" in result.stderr
        assert "'bogus-value'" in result.stderr
