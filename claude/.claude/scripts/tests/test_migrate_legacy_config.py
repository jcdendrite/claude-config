"""Tests for migrate-legacy-config.sh -- the script that imports every
legacy sentinel file into claude-config.toml, then offers per-file
interactive deletion. Drives the real script directly via subprocess.run --
a real call-sequence exercise, not install.sh's INSTALL_TEST_FIXTURE
block-extraction harness, which exists only because install.sh itself
isn't decomposable.

The union-semantics and legacy-precedence *math* are not retested here --
those live in test_config_lib.py as direct _config_enabled/_config_value
fixtures. This file covers what's actually migration-script-specific:
import-on-first-encounter, the import-before-scaffold ordering, the
enforcement-critical-key-import-gating and per-key-failure-isolation
machinery, the interactive delete-confirmation phase, and the
pr_cost_disclosure two-legacy-location precedence tiebreak.

CONFIG_KEYS_PSV and MIGRATE_SCRIPT below are declared as module-level path
constants (not inline strings) so TestCrossDomainReadCompleteness
(test_select_tests.py) can see this file's dependency on them.
"""
from __future__ import annotations

import os
import pty
import re
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, SCRIPTS_DIR

sys.path.insert(0, str(Path(__file__).parent.parent))
from _config import SchemaRow, config_value, schema  # noqa: E402

MIGRATE_SCRIPT = SCRIPTS_DIR / "migrate-legacy-config.sh"
CONFIG_KEYS_PSV = HOOKS_DIR / "config-keys.psv"

_ENFORCEMENT_CRITICAL_KEYS = frozenset({
    "worktree_required",
    "autonomous_shipping",
    "round_consult_gate",
    "commit_stall_block",
    "authorization_boundary_restore",
})

# Mirrors migrate-legacy-config.sh's own
# _migrate_enforcement_critical_safe_value pair-list -- each value is that
# key's enforcement-stays-on (or kill-switch-stays-off) direction, not
# simply its schema default (see that function's own header for why it is
# not derived from config-keys.psv's default or
# legacy-probe-on-resolution-failure columns).
_ENFORCEMENT_CRITICAL_SAFE_VALUES = {
    "worktree_required": "true",
    "autonomous_shipping": "false",
    "round_consult_gate": "true",
    "commit_stall_block": "true",
    "authorization_boundary_restore": "true",
}

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
# The 6-cell-per-key migration matrix
# ---------------------------------------------------------------------------


class TestMigrationMatrixStateFileAbsent:
    """State file entirely absent, crossed with legacy-file-present. This is
    the cell that would fail under a scaffold-before-import regression:
    scaffold would populate every key's schema default before import ever
    runs, so import's own "no existing row" trigger would never fire."""

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
            # Every key here carries a legacy-polarity value, so scaffold
            # leaves it absent from the state file rather than backfilling
            # its default -- that default is still what it resolves to.
            assert key not in state
            # Documents the net resolved value; the line above already
            # pins scaffold's own exclusion logic.
            assert config_value(key, config_dir_override=config_dir) == row.default
        elif key in _ENFORCEMENT_CRITICAL_KEYS:
            if _legacy_derived_value(row) == _ENFORCEMENT_CRITICAL_SAFE_VALUES[key]:
                assert state.get(key) == _legacy_derived_value(row), (
                    f"a legacy value matching {key!r}'s fail-closed direction must import with no gate: "
                    f"state={state!r}"
                )
            else:
                assert key not in state, (
                    f"a permissive-direction legacy value for {key!r} must never be imported: state={state!r}"
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
            # Same reasoning as TestMigrationMatrixStateFileAbsent: this
            # key's own legacy-polarity value keeps it absent from the
            # state file, even though a sibling row is now present.
            assert key not in state
            # Documents the net resolved value; the line above already
            # pins scaffold's own exclusion logic.
            assert config_value(key, config_dir_override=config_dir) == row.default
        elif key in _ENFORCEMENT_CRITICAL_KEYS:
            if _legacy_derived_value(row) == _ENFORCEMENT_CRITICAL_SAFE_VALUES[key]:
                assert state.get(key) == _legacy_derived_value(row)
            else:
                assert key not in state
        else:
            assert state.get(key) == _legacy_derived_value(row)


class TestMigrationMatrixStateFilePresentKeyRowPresent:
    """State file present with this key's own row already populated -- that
    row is never touched via the legacy path again, whether it came from a
    hand-edit, a prior import, or predates any run of this script. Asserted
    via the exact authored line surviving verbatim, not a value comparison
    (see _distinguishing_existing_value)."""

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

    def test_existing_row_survives_when_schema_unreadable(self, tmp_path: Path) -> None:
        """_migrate_process_key's own has_existing_row check
        (_config_read_key_from_file) must not mistake an unreadable
        config-keys.psv (a stow-relink race or interrupted `git pull`) for
        "no existing row" -- that misread would let this key's legacy file
        re-import and silently overwrite an already-authoritative row.
        Direct invocation, with _config_schema_known_keys overridden to
        fail the way it does against a genuinely unreadable schema file --
        every other schema read in the same call (type validation, the
        eventual _config_set) is unaffected, since only the membership
        check this fix guards is exercised here.

        Deliberately a function-override stub, not a genuinely broken
        config-keys.psv (the technique test_config_lib.py's own
        TestReadKeyFromFileSchemaUnreadable uses): this script's own
        main() reads $_CONFIG_SCHEMA_FILE eagerly and aborts before any
        key is processed if it's genuinely missing, which would not model
        the transient single-call race this fix targets, and the real
        schema file is shared across parallel test workers, so a
        real-file break here would need a second layer of directory
        isolation for no added coverage over the hooks-level unit test."""
        key = "handoff_nudge"
        row = _SCHEMA[key]
        home = tmp_path / "home"
        config_dir = home / ".claude"
        config_dir.mkdir(parents=True)
        existing_value = _distinguishing_existing_value(row)
        authored_line = f"{key} = {existing_value}"
        _state_file(config_dir).write_text(authored_line + "\n")
        _write_legacy_file(config_dir, row, True)
        records_file = tmp_path / "records.psv"

        script = (
            '_MIGRATE_TTY=1\n'
            f'_MIGRATE_CONFIG_DIR="{config_dir}"\n'
            f'_MIGRATE_HOME_DIR="{config_dir}"\n'
            '_MIGRATE_STATE_FILE="$_MIGRATE_CONFIG_DIR/claude-config.toml"\n'
            '_MIGRATE_SCAFFOLD_EXCLUDE=""\n'
            '_MIGRATE_RECORDS=()\n'
            '_config_schema_known_keys() {\n'
            '  printf "_config.sh: warning: schema file not found or unreadable: %s\\n" "$_CONFIG_SCHEMA_FILE" >&2\n'
            '  return 1\n'
            '}\n'
            f'_migrate_process_key {key} {row.type} {row.legacy_import_locations} '
            f'{row.legacy_filename} {row.legacy_polarity} "{row.human_name}"\n'
            f'printf \'%s\\n\' "${{_MIGRATE_RECORDS[@]}}" > "{records_file}"\n'
        )
        result = _run_sourced(script, "", _env(home, config_dir=config_dir))
        assert result.returncode == 0, f"stderr={result.stderr!r}"

        new_lines = _state_file(config_dir).read_text().splitlines()
        assert authored_line in new_lines, (
            f"an unreadable schema must not let the legacy path overwrite an existing row: {new_lines!r}"
        )
        assert "skipped-existing-row" in records_file.read_text()


# ---------------------------------------------------------------------------
# Hand-edit survives a re-run
# ---------------------------------------------------------------------------


class TestHandEditSurvivesRerun:
    def test_second_run_does_not_revert_a_hand_edit_made_between_runs(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        config_dir = home / ".claude"
        config_dir.mkdir(parents=True)

        first = _run(_env(home))
        assert first.returncode == 0, f"stderr={first.stderr!r}"
        # No legacy file for handoff_nudge yet, so its legacy-polarity value
        # keeps scaffold from backfilling a row -- author one directly, as
        # a user hand-edit between two runs would, rather than relying on
        # scaffold to have created a row to then flip.
        assert "handoff_nudge" not in _read_state(config_dir)
        with _state_file(config_dir).open("a") as handle:
            handle.write("handoff_nudge = true\n")
        (config_dir / ".handoff-nudge-disabled").touch()  # would derive "false" too if (wrongly) re-read

        second = _run(_env(home))
        assert second.returncode == 0, f"stderr={second.stderr!r}"
        assert _read_state(config_dir)["handoff_nudge"] == "true", (
            "a hand-edit made between two runs must survive the second run"
        )

    def test_pre_first_run_hand_authored_row_survives_first_run(self, tmp_path: Path) -> None:
        """The hand-edit-survives guarantee is per-key-encounter, not
        per-run-number: a row authored before this script has ever run once
        must still survive that first run, and every other key must still
        import or resolve to its default alongside it."""
        home = tmp_path / "home"
        config_dir = home / ".claude"
        config_dir.mkdir(parents=True)
        _state_file(config_dir).write_text("round_consult_gate = false\n")
        (config_dir / ".round-consult-gate-disabled").touch()  # would derive "false" too if (wrongly) read

        result = _run(_env(home))
        assert result.returncode == 0, f"stderr={result.stderr!r}"

        state = _read_state(config_dir)
        assert state["round_consult_gate"] == "false"
        assert "commit_stall_block" not in state, "no legacy file present, so its own legacy-polarity keeps it absent"
        # Documents the net resolved value; the line above already pins
        # scaffold's own exclusion logic.
        assert config_value("commit_stall_block", config_dir_override=config_dir) == "true", (
            "other keys must still resolve to their default alongside it"
        )


# ---------------------------------------------------------------------------
# pr_cost_disclosure's two-legacy-location precedence
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
# Direct-invocation interactive tests (delete-confirmation) -- no
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


def _run_main_with_real_pty_stdin(env: dict, stdin_response: str | None = None) -> subprocess.CompletedProcess:
    """Runs migrate-legacy-config.sh's real main() with stdin connected to
    an actual pseudo-terminal (pty.openpty()'s slave side), not a pipe --
    the genuine `[ -t 0 ]`-satisfying shape a self-allocated pty
    (`script -qc ... /dev/null`) produces, as opposed to every other test
    in this file forcing $_MIGRATE_TTY as a plain variable. `stdin_response`
    is written to the master side before main() runs, queued in the pty's
    own input buffer for whatever `read -r -p` call consumes it -- omit it
    (the default) only when the caller's own fixture produces zero
    delete-confirmation prompts (only 'imported'/'lost-precedence' records
    reach that phase), since main() otherwise blocks waiting for input that
    never arrives. Writing a response rather than closing the master fd for
    EOF: closing it can raise SIGHUP on the slave instead of a clean EOF."""
    master_fd, slave_fd = pty.openpty()
    try:
        proc = subprocess.Popen(
            [str(MIGRATE_SCRIPT)],
            stdin=slave_fd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
        os.close(slave_fd)
        slave_fd = -1
        if stdin_response is not None:
            os.write(master_fd, stdin_response.encode())
        stdout, stderr = proc.communicate(timeout=10)
        return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)
    finally:
        if slave_fd != -1:
            os.close(slave_fd)
        os.close(master_fd)


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

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_delete_failure_does_not_abort_the_remaining_records(self, tmp_path: Path) -> None:
        """Review finding: _migrate_prompt_delete_legacy_file's
        case arm ended in `rm -f -- "$path" && echo ...` as the function's
        own last statement, called bare inside main's delete-confirmation
        loop under `set -euo pipefail` -- a real `rm -f` I/O failure (EACCES
        here, via a read-only parent directory) aborted the whole script,
        skipping the delete-offer for every subsequent record. Drives
        _migrate_run_delete_confirmation_phase directly with two
        manually-constructed records, matching
        test_home_worktree_required_is_never_offered_even_with_tty_and_yes_response's
        technique, so the first record's directory can be made read-only
        without affecting the second."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        undeletable_dir = tmp_path / "undeletable"
        undeletable_dir.mkdir()
        undeletable_file = undeletable_dir / "legacy-a"
        undeletable_file.touch()
        deletable_file = tmp_path / "legacy-b"
        deletable_file.touch()
        undeletable_dir.chmod(0o555)
        try:
            script = (
                '_MIGRATE_TTY=1\n'
                f'_MIGRATE_RECORDS=('
                f'"{undeletable_file}|autonomous_shipping|true|imported|false" '
                f'"{deletable_file}|handoff_nudge|true|imported|false")\n'
                '_migrate_run_delete_confirmation_phase\n'
            )
            result = _run_sourced(script, "y\ny\n", _env(home))
        finally:
            undeletable_dir.chmod(0o755)
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "could not delete" in result.stderr
        assert undeletable_file.exists(), "a failed rm must leave the file in place, not silently succeed"
        assert not deletable_file.exists(), (
            "the second record must still get its own delete-confirmation offer after the first record's rm failed"
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

    def test_permissive_direction_enforcement_critical_import_reaches_delete_phase_as_deferred(
        self, tmp_path: Path
    ) -> None:
        """A permissive-direction legacy value for an enforcement-critical
        key must record deferred-pending-confirmation for that file, and the
        delete phase must never offer it -- no TTY or answer of any kind
        changes this outcome."""
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
        result = _run_sourced(script, "", _env(home, config_dir=config_dir))
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "round_consult_gate" not in _read_state(config_dir), (
            "a permissive-direction enforcement-critical import must leave the key absent after scaffold, "
            "not just after import"
        )
        assert "deferred-pending-confirmation" in records_file.read_text()

    def test_legacy_file_read_failure_reaches_delete_phase_as_deferred(self, tmp_path: Path) -> None:
        """A TTY-present run that hits a legacy-file read failure on a
        non-enforcement-critical key must also record
        deferred-pending-confirmation, the same as a declined
        enforcement-critical import."""
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
# Enforcement-critical import gating: direction-aware, not TTY-gated
# ---------------------------------------------------------------------------


class TestTTYInvarianceOfEnforcementCriticalImport:
    def test_migrate_process_key_body_has_no_migrate_tty_reference(self) -> None:
        """Structural tripwire, not a behavioral proof: _migrate_process_key's
        own header explains why it must never read $_MIGRATE_TTY -- the
        enforcement-critical safe-value gate is TTY-blind by design, since
        `[ -t 0 ]` is evaluated inside a process a caller fully controls and
        can fabricate a pty for. This only pins that a future edit can't
        silently reintroduce a TTY branch into this one function's body;
        the test below drives main() end-to-end through a real pty to prove
        the actual behavioral property."""
        content = MIGRATE_SCRIPT.read_text()
        match = re.search(r"^_migrate_process_key\(\) \{\n(.*?)\n^\}$", content, re.DOTALL | re.MULTILINE)
        assert match, "could not locate _migrate_process_key's body in migrate-legacy-config.sh"
        assert "_MIGRATE_TTY" not in match.group(1)

    def test_real_pty_stdin_still_defers_a_permissive_direction_value(self, tmp_path: Path) -> None:
        """The real attack shape this script's TTY-blind design defends
        against: a caller self-allocates a real pseudo-terminal (e.g. via
        `script -qc`) so `[ -t 0 ]` inside the script's own process reports
        true. Unlike this file's other TTY
        tests (which force $_MIGRATE_TTY as a plain variable to drive
        _migrate_process_key directly), this runs the real main() through
        an actual pty as stdin -- closing the gap between what a forced
        variable proves and what an attacker actually controls. The
        permissive-direction fixture below (round_consult_gate's own
        presence-disables legacy file, reused from
        TestDeleteConfirmationPrompt.test_permissive_direction_enforcement_critical_import_reaches_delete_phase_as_deferred)
        produces zero 'imported'/'lost-precedence' records, so the delete
        phase issues no prompts and main() completes without blocking on
        stdin."""
        home = tmp_path / "home"
        config_dir = home / ".claude"
        config_dir.mkdir(parents=True)
        (config_dir / ".round-consult-gate-disabled").touch()

        result = _run_main_with_real_pty_stdin(_env(home, config_dir=config_dir))
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "round_consult_gate" not in _read_state(config_dir), (
            "a real pty as stdin must not unlock a permissive-direction enforcement-critical "
            "import any more than a non-tty run would"
        )

    def test_real_pty_stdin_still_imports_the_fail_closed_direction_value(self, tmp_path: Path) -> None:
        """The fail-closed counterpart to the permissive-direction test
        above: worktree_required is the sole enforcement-critical key whose
        legacy_polarity column (config-keys.psv) lets mere legacy-file
        presence derive the SAFE value -- the other four enforcement-
        critical keys can only ever derive the permissive value from
        presence, so only worktree_required can exercise this direction. A
        real pty as stdin must still import it; no `[ -t 0 ]`-satisfying pty
        defeats the fail-closed direction either. Unlike the permissive-
        direction test above, this "imported" record is not load-bearing
        here (the default $HOME/.claude has no distinct config-dir-and-home
        split, so _migrate_add_record's load-bearing flag never applies to
        it) -- it reaches phase 2's still-TTY-gated delete-confirmation
        prompt, so this answers "n" through the pty rather than leaving
        main() blocked on a prompt nothing ever responds to."""
        home = tmp_path / "home"
        config_dir = home / ".claude"
        config_dir.mkdir(parents=True)
        legacy_file = config_dir / "worktree-required"
        legacy_file.touch()

        result = _run_main_with_real_pty_stdin(_env(home, config_dir=config_dir), stdin_response="n\n")
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert _read_state(config_dir)["worktree_required"] == "true"
        assert legacy_file.exists(), "declining the delete-confirmation prompt must leave the legacy file in place"


def _migrate_enforcement_critical_keys_from_script() -> set[str]:
    """Sources migrate-legacy-config.sh and reads its own
    $_MIGRATE_ENFORCEMENT_CRITICAL_KEYS directly -- the actual bash source
    of truth -- rather than comparing against a third, independently
    maintained Python copy of the same list."""
    result = _run_sourced('printf \'%s\' "$_MIGRATE_ENFORCEMENT_CRITICAL_KEYS"\n', "", dict(os.environ))
    assert result.returncode == 0, f"stderr={result.stderr!r}"
    return set(result.stdout.split())


class TestEnforcementCriticalKeySet:
    def test_matches_config_keys_psv_header_comment(self) -> None:
        """config-keys.psv's own header comment is the second place (besides
        migrate-legacy-config.sh's _MIGRATE_ENFORCEMENT_CRITICAL_KEYS and
        _migrate_enforcement_critical_safe_value) that names the five
        enforcement-critical keys -- checked against the real bash variable
        itself, not a third, independently maintained Python copy, so a
        future key addition/removal can't silently drift the two apart."""
        text = CONFIG_KEYS_PSV.read_text()
        match = re.search(r"The five enforcement-critical keys \(([^)]+)\)", text, re.DOTALL)
        assert match, "config-keys.psv's header comment must name the five enforcement-critical keys"
        # The parenthesized list wraps across a `#`-continued comment line,
        # so each name is stripped of the leading `#` and whitespace a
        # mid-list line break leaves behind, not just surrounding spaces.
        named = {name.strip().lstrip("#").strip() for name in match.group(1).split(",")}
        assert named == _migrate_enforcement_critical_keys_from_script()


class TestEnforcementCriticalSafeValues:
    @pytest.mark.parametrize(("key", "safe_value"), sorted(_ENFORCEMENT_CRITICAL_SAFE_VALUES.items()))
    def test_pins_the_fail_closed_value(self, key: str, safe_value: str, tmp_path: Path) -> None:
        """Pins _migrate_enforcement_critical_safe_value's own pair-list
        content directly, independent of any legacy-file derivation."""
        home = tmp_path / "home"
        home.mkdir()
        result = _run_sourced(
            f'_migrate_enforcement_critical_safe_value {key} && printf \'%s\' "$_MIGRATE_SAFE_VALUE"\n',
            "",
            _env(home),
        )
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert result.stdout == safe_value


# Every enforcement-critical key except worktree_required pairs a
# presence-only legacy polarity with a safe value on the opposite boolean --
# so any legacy file for these four, merely by existing, always derives the
# permissive value; there is no legacy-file content that could exercise
# their own safe-import branch. worktree_required is the sole exception
# (presence-enables, safe value "true"), covered by its own test below.
_PERMISSIVE_VIA_LEGACY_FILE_KEYS = sorted(_ENFORCEMENT_CRITICAL_KEYS - {"worktree_required"})


class TestEnforcementCriticalDirectionAwareImport:
    """Import writes a legacy-derived value for one of the five
    enforcement-critical keys only when it matches that key's own
    fail-closed value; a permissive-direction value is never written -- it
    is deferred and printed for a human to hand-paste into
    claude-config.toml instead."""

    def test_worktree_required_legacy_presence_imports_with_no_gate(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        config_dir = home / ".claude"
        config_dir.mkdir(parents=True)
        (config_dir / "worktree-required").touch()

        result = _run(_env(home, config_dir=config_dir))
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert _read_state(config_dir)["worktree_required"] == "true"

    @pytest.mark.parametrize("key", _PERMISSIVE_VIA_LEGACY_FILE_KEYS)
    def test_permissive_direction_legacy_value_is_never_imported(self, key: str, tmp_path: Path) -> None:
        home = tmp_path / "home"
        config_dir = home / ".claude"
        config_dir.mkdir(parents=True)
        row = _SCHEMA[key]
        (config_dir / row.legacy_filename).touch()

        result = _run(_env(home, config_dir=config_dir))
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        state = _read_state(config_dir)
        assert key not in state, f"a permissive-direction legacy value for {key!r} must never be imported: {state!r}"
        # The deferred key still resolves via its ordinary legacy fallback --
        # deferring the import changes nothing about what the key means.
        assert config_value(key, config_dir_override=config_dir) == _legacy_derived_value(row)
        assert f"{key} = {_legacy_derived_value(row)}" in result.stderr, (
            "the permissive value must be printed for a human to hand-paste"
        )


# ---------------------------------------------------------------------------
# Per-key legacy-file read-failure isolation, run twice
# ---------------------------------------------------------------------------


class TestPerKeyFailureIsolation:
    """A single key's legacy-file read failure does not abort the script,
    and does not block any other key's own import/scaffold -- run once for
    a non-enforcement-critical key (pr_cost_disclosure) and once for an
    enforcement-critical one, since the exclude-list logic must generalize
    to both."""

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
        # The other 13 keys still resolve correctly -- neither has a legacy
        # file here, so their own legacy-polarity keeps their rows absent
        # too, resolving via their schema default.
        assert "commit_stall_block" not in state
        assert "worktree_required" not in state
        # The two lines below document the net resolved value; the two
        # lines above already pin scaffold's own exclusion logic.
        assert config_value("commit_stall_block", config_dir_override=config_dir) == "true"
        assert config_value("worktree_required", config_dir_override=config_dir) == "false"

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
        assert "commit_stall_block" not in state, "no legacy file present, so its own legacy-polarity keeps it absent"
        # Documents the net resolved value; the line above already pins
        # scaffold's own exclusion logic.
        assert config_value("commit_stall_block", config_dir_override=config_dir) == "true"


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
