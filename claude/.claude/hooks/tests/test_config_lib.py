"""Tests for _config.sh's config-key reader/writer primitives.

Covers _config_enabled/_config_value's resolution semantics (schema
default, legacy-file fallback per legacy-polarity, the config-dir-or-home
union, and the state-file-row-is-authoritative precedence over a
disagreeing legacy file), _config_set's comment/key-order-preserving
rewrite and refuse-on-malformed-content contract, and _config_scaffold's
additive-only and exclude-list contracts.

Drives the real _config.sh via a bash subprocess that sources _lib.sh (the
canonical caller pattern -- _lib.sh sources _config.sh transitively, see
that file's own header), mirroring test_marker_lib.py's _run_lib_fn
pattern. Uses the `isolated_home` fixture (conftest.py) for HOME isolation
and CLAUDE_CONFIG_DIR clearing, then runs the bash subprocess with the
default (ambient, monkeypatched) environment -- the same env-then-inherit
shape run_hook's own home-only call path uses.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, REPO_ROOT, SCRIPTS_DIR

_LIB_SH = HOOKS_DIR / "_lib.sh"
_CONFIG_SH = HOOKS_DIR / "_config.sh"
_CONFIG_KEYS_PSV = HOOKS_DIR / "config-keys.psv"
_INSTALL_SH = REPO_ROOT / "install.sh"


def _first_available_non_c_utf8_locale() -> str | None:
    """Returns the first non-C/POSIX UTF-8 locale name `locale -a` reports
    as installed, or None if none is -- a minimal container image may ship
    only the C/POSIX locale. Used to skip (not silently no-op) the
    ambient-locale guard test below on a machine that can't exercise it."""
    try:
        result = subprocess.run(["locale", "-a"], capture_output=True, text=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    for name in result.stdout.splitlines():
        if name.strip().lower() in ("c", "posix"):
            continue
        if "utf8" in name.lower() or "utf-8" in name.lower():
            return name.strip()
    return None


_NON_C_UTF8_LOCALE = _first_available_non_c_utf8_locale()


def _run(script: str) -> subprocess.CompletedProcess:
    """Source _lib.sh, then run `script`, inheriting the current (test-
    monkeypatched) process environment -- HOME/CLAUDE_CONFIG_DIR are set up
    by each test via the isolated_home fixture and monkeypatch."""
    return subprocess.run(
        ["bash", "-c", f'set -uo pipefail; . "{_LIB_SH}"; {script}'],
        capture_output=True,
        text=True,
    )


def _write_state_file(home, content: str, config_dir=None) -> None:
    target = (config_dir or (home / ".claude")) / "claude-config.toml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def _schema_row_call_count_wrapper(count_file: Path) -> str:
    """A shell prefix that renames the real _config_schema_row aside, then
    redefines it to count into `count_file` before delegating. Counts into
    a file rather than a shell variable because _config_location_value can
    run inside a `$(...)` subshell capture, whose variable mutations don't
    propagate back to the counting caller's shell."""
    return (
        'eval "$(declare -f _config_schema_row | '
        "sed '1s/.*/_config_schema_row_real ()/')\"; "
        f'_config_schema_row() {{ printf x >> "{count_file}"; _config_schema_row_real "$@"; }}; '
    )


def _run_with_schema(hooks_dir: Path, script: str) -> subprocess.CompletedProcess:
    """Source an isolated _config.sh symlink from `hooks_dir` (which carries
    its own config-keys.psv), bypassing _lib.sh entirely -- _config_scaffold
    needs only _config.sh's own functions, matching
    test_config_parser_parity.py's TestMissingSchemaFile isolation
    technique. Used for schema shapes (e.g. a key with no legacy-polarity
    value) that none of today's other real 16 keys carry.
    test_selection_tracking is the one real key with that shape. It is
    exercised directly against the real schema by TestConfigScaffold."""
    return subprocess.run(
        ["bash", "-c", f'set -uo pipefail; . "{hooks_dir / "_config.sh"}"; {script}'],
        capture_output=True,
        text=True,
    )


def _isolated_hooks_dir_missing_key_row(tmp_path: Path, key: str) -> Path:
    """Copies the real config-keys.psv into an isolated hooks dir with KEY's
    own row entirely removed -- every other row is left untouched, so this
    exercises the interrupted-stow-relink/git-pull shape (a readable,
    non-empty file missing exactly one row), not a wholly synthetic or
    empty schema."""
    isolated_hooks_dir = tmp_path / "isolated-hooks"
    isolated_hooks_dir.mkdir()
    (isolated_hooks_dir / "_config.sh").symlink_to(_CONFIG_SH)
    lines = [line for line in _CONFIG_KEYS_PSV.read_text().splitlines() if not line.startswith(f"{key}|")]
    (isolated_hooks_dir / "config-keys.psv").write_text("\n".join(lines) + "\n")
    return isolated_hooks_dir


def _isolated_hooks_dir_with_truncated_key_row(tmp_path: Path, key: str, truncated_row: str) -> Path:
    """Copies the real config-keys.psv into an isolated hooks dir, replacing
    KEY's own row with TRUNCATED_ROW (missing one or more trailing columns)
    -- every other row is left untouched, so this exercises a schema file
    truncated mid-row (an interrupted stow-relink/git-pull caught partway
    through rewriting one row), not a wholly synthetic or empty schema."""
    isolated_hooks_dir = tmp_path / "isolated-hooks"
    isolated_hooks_dir.mkdir()
    (isolated_hooks_dir / "_config.sh").symlink_to(_CONFIG_SH)
    lines = [
        truncated_row if line.startswith(f"{key}|") else line
        for line in _CONFIG_KEYS_PSV.read_text().splitlines()
    ]
    (isolated_hooks_dir / "config-keys.psv").write_text("\n".join(lines) + "\n")
    return isolated_hooks_dir


def _isolated_hooks_dir_with_legacy_polarity_override(tmp_path: Path, key: str, polarity: str) -> Path:
    """Copies the real config-keys.psv into an isolated hooks dir, replacing
    KEY's own legacy-polarity column (field 8 of 11) with POLARITY --
    every other row and field is left untouched, so this exercises the
    corrupted-single-row shape a config-keys.psv typo would produce, not a
    wholly synthetic schema."""
    isolated_hooks_dir = tmp_path / "isolated-hooks"
    isolated_hooks_dir.mkdir()
    (isolated_hooks_dir / "_config.sh").symlink_to(_CONFIG_SH)
    lines = []
    for line in _CONFIG_KEYS_PSV.read_text().splitlines():
        if line.startswith(f"{key}|"):
            fields = line.split("|")
            fields[7] = polarity
            line = "|".join(fields)
        lines.append(line)
    (isolated_hooks_dir / "config-keys.psv").write_text("\n".join(lines) + "\n")
    return isolated_hooks_dir


def _isolated_hooks_dir_with_empty_default_column(tmp_path: Path, key: str) -> Path:
    """Copies the real config-keys.psv into an isolated hooks dir, blanking
    KEY's own default column (field 3 of 11).
    Every other column, including resolution and
    legacy-probe-on-resolution-failure, is left intact.
    Every other row is left untouched.
    _isolated_hooks_dir_with_truncated_key_row drops trailing columns
    wholesale and so also empties resolution/legacy-probe-on-resolution-
    failure. This exercises a single mid-row column blanked out on its own,
    the shape a bad find-and-replace or merge-conflict resolution would
    produce."""
    isolated_hooks_dir = tmp_path / "isolated-hooks"
    isolated_hooks_dir.mkdir()
    (isolated_hooks_dir / "_config.sh").symlink_to(_CONFIG_SH)
    lines = []
    for line in _CONFIG_KEYS_PSV.read_text().splitlines():
        if line.startswith(f"{key}|"):
            fields = line.split("|")
            fields[2] = ""
            line = "|".join(fields)
        lines.append(line)
    (isolated_hooks_dir / "config-keys.psv").write_text("\n".join(lines) + "\n")
    return isolated_hooks_dir


def _isolated_hooks_dir_with_legacy_probe_override(tmp_path: Path, key: str, legacy_probe: str) -> Path:
    """Copies the real config-keys.psv into an isolated hooks dir, replacing
    KEY's own legacy-probe-on-resolution-failure column (field 4 of 11)
    with LEGACY_PROBE.
    Every other row and field is left untouched.
    This exercises a synthetic opt-in to the legacy-probe fail-safe arm on
    a key that doesn't carry it today.
    Same field-index-override technique
    _isolated_hooks_dir_with_legacy_polarity_override uses for a different
    column."""
    isolated_hooks_dir = tmp_path / "isolated-hooks"
    isolated_hooks_dir.mkdir()
    (isolated_hooks_dir / "_config.sh").symlink_to(_CONFIG_SH)
    lines = []
    for line in _CONFIG_KEYS_PSV.read_text().splitlines():
        if line.startswith(f"{key}|"):
            fields = line.split("|")
            fields[4] = legacy_probe
            line = "|".join(fields)
        lines.append(line)
    (isolated_hooks_dir / "config-keys.psv").write_text("\n".join(lines) + "\n")
    return isolated_hooks_dir


def _write_write_attempt_shims(bin_dir: Path, marker: Path) -> None:
    """Shadow mkdir/mktemp/mv on PATH with stubs that touch `marker` then
    exit 1, so a broken _config_set/_config_scaffold root guard is caught by
    the marker's existence regardless of the test runner's own filesystem
    privileges against the real `/` -- unlike relying on a real write to `/`
    failing, which only happens for a non-root runner."""
    for real_binary in ("mkdir", "mktemp", "mv"):
        shim = bin_dir / real_binary
        shim.write_text(f"#!/bin/bash\ntouch {shlex.quote(str(marker))}\nexit 1\n")
        shim.chmod(0o755)


# ---------------------------------------------------------------------------
# _config_value / _config_enabled: default resolution (no state file, no legacy)
# ---------------------------------------------------------------------------


class TestDefaultResolution:
    def test_bool_key_defaulting_false_resolves_false(self, isolated_home):
        result = _run("_config_value worktree_required")
        assert result.stdout == "false"
        assert result.returncode == 0

    def test_bool_key_defaulting_true_resolves_true(self, isolated_home):
        result = _run("_config_value commit_stall_block")
        assert result.stdout == "true"
        assert result.returncode == 0

    def test_config_enabled_maps_true_value_to_exit_0(self, isolated_home):
        assert _run("_config_enabled commit_stall_block").returncode == 0

    def test_config_enabled_maps_false_value_to_exit_1(self, isolated_home):
        assert _run("_config_enabled worktree_required").returncode == 1


# ---------------------------------------------------------------------------
# _config_enabled's five-way exit code contract
# ---------------------------------------------------------------------------


class TestExitCodeContract:
    def test_unresolvable_config_dir_returns_exit_2(self, isolated_home, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "relative/not-absolute")
        result = _run("_config_enabled round_consult_gate")
        assert result.returncode == 2
        assert result.stdout == ""

    def test_unreadable_schema_returns_exit_3(self, tmp_path):
        """config-keys.psv itself missing/unreadable must propagate exit 3
        unchanged from both _config_value and _config_enabled, not collapse
        into exit 1 ("KEY has no schema row") -- the two mean different
        things to an enforcement-critical caller (see _config_value's own
        exit-3 comment). Mirrors test_config_parser_parity.py's
        TestMissingSchemaFile isolation technique, lighter than
        _lib_sh_with_unreadable_schema's since _config_value/_config_enabled
        need only _config.sh itself, not _lib.sh's own sourcing chain:
        symlink _config.sh alone into a directory with no config-keys.psv
        sibling."""
        isolated_hooks_dir = tmp_path / "isolated-hooks"
        isolated_hooks_dir.mkdir()
        (isolated_hooks_dir / "_config.sh").symlink_to(_CONFIG_SH)
        value_result = _run_with_schema(isolated_hooks_dir, "_config_value worktree_required")
        assert value_result.returncode == 3
        assert value_result.stdout == ""
        enabled_result = _run_with_schema(isolated_hooks_dir, "_config_enabled worktree_required")
        assert enabled_result.returncode == 3
        assert enabled_result.stdout == ""

    def test_schema_row_missing_from_readable_file_returns_exit_4(self, tmp_path):
        """A config-keys.psv that is readable and parses at least one row,
        but not KEY's own -- the interrupted stow-relink/git-pull shape,
        distinct from the wholly-unreadable file above -- must propagate a
        distinct exit 4 unchanged from both _config_value and
        _config_enabled, not collapse into exit 1 (indistinguishable from
        an ordinary typo'd key) or exit 3 (wholly unreadable file)."""
        isolated_hooks_dir = _isolated_hooks_dir_missing_key_row(tmp_path, "worktree_required")
        value_result = _run_with_schema(isolated_hooks_dir, "_config_value worktree_required")
        assert value_result.returncode == 4
        assert value_result.stdout == ""
        assert "worktree_required" in value_result.stderr
        enabled_result = _run_with_schema(isolated_hooks_dir, "_config_enabled worktree_required")
        assert enabled_result.returncode == 4
        assert enabled_result.stdout == ""

    def test_readable_empty_schema_also_returns_exit_4(self, tmp_path):
        """A readable schema file that parses to zero rows at all (comments/
        blank-lines-only, or truly empty) is the same interrupted-write race
        exit 4's missing-single-row shape covers, just caught at an earlier
        truncation point, so it must also return exit 4 with a stderr
        warning naming the schema file, rather than the silent exit 1 an
        ordinary typo'd key gets. Bash-side counterpart to
        test_config_parser_parity.py's TestReadableButEmptySchemaFile."""
        isolated_hooks_dir = tmp_path / "isolated-hooks"
        isolated_hooks_dir.mkdir()
        (isolated_hooks_dir / "config-keys.psv").write_text("")
        (isolated_hooks_dir / "_config.sh").symlink_to(_CONFIG_SH)
        result = _run_with_schema(isolated_hooks_dir, "_config_value worktree_required")
        assert result.returncode == 4
        assert result.stdout == ""
        assert "parsed zero rows" in result.stderr

    def test_mid_row_truncation_returns_exit_4_not_a_silent_grant(self, tmp_path):
        """Security regression: a config-keys.psv row present but cut off
        after the key/type fields (an interrupted stow-relink/git-pull
        caught mid-row, rather than mid-file) must not resolve as an empty
        string that _config_enabled's any-value-but-false rule would then
        read as enabled. autonomous_shipping is the key this matters most
        for: every other enforcement-critical key's safe direction is
        "stays armed" regardless of this bug, but autonomous_shipping's safe
        direction is "not shipping" -- a silent exit 0 with an empty
        resolution/default here would silently grant it."""
        isolated_hooks_dir = _isolated_hooks_dir_with_truncated_key_row(
            tmp_path, "autonomous_shipping", "autonomous_shipping|bool"
        )
        value_result = _run_with_schema(isolated_hooks_dir, "_config_value autonomous_shipping")
        assert value_result.returncode == 4
        assert value_result.stdout == ""
        assert "autonomous_shipping" in value_result.stderr
        enabled_result = _run_with_schema(isolated_hooks_dir, "_config_enabled autonomous_shipping")
        assert enabled_result.returncode == 4
        assert enabled_result.stdout == ""

    def test_truncation_after_resolution_column_still_returns_exit_4(self, tmp_path):
        """Security regression: a row truncated one column later than the
        case above -- key/type/default/resolution all intact, only
        legacy-probe-on-resolution-failure and beyond missing -- must still
        return exit 4, not fall through to exit 2 (config-dir-resolution-
        failure). worktree_required is the key this matters most for: its
        row is the one real row whose legacy-probe-on-resolution-failure
        column is `true`, and _lib_worktree_enforcement_active treats exit 2
        as "not enforced" but exit 3/4 as "stays enforced" -- so this exact
        truncation shape would otherwise silently disarm worktree_required
        enforcement."""
        isolated_hooks_dir = _isolated_hooks_dir_with_truncated_key_row(
            tmp_path, "worktree_required", "worktree_required|bool|false|config-dir-or-home"
        )
        value_result = _run_with_schema(isolated_hooks_dir, "_config_value worktree_required")
        assert value_result.returncode == 4
        assert value_result.stdout == ""
        assert "worktree_required" in value_result.stderr
        enabled_result = _run_with_schema(isolated_hooks_dir, "_config_enabled worktree_required")
        assert enabled_result.returncode == 4
        assert enabled_result.stdout == ""

    def test_empty_default_column_returns_exit_4_not_a_silent_grant(self, tmp_path):
        """Security regression: a config-keys.psv row can have its default
        column alone blanked. Resolution and legacy-probe-on-resolution-
        failure stay intact. That row must not resolve as an empty string.
        _config_enabled's any-value-but-false rule would then read that
        empty string as enabled. autonomous_shipping's safe direction is
        "not shipping", so a silent exit 0 with an empty default here would
        silently grant it. This is the very failure path
        _config_resolve_fail_safe's own default fallback exists to close."""
        isolated_hooks_dir = _isolated_hooks_dir_with_empty_default_column(
            tmp_path, "autonomous_shipping"
        )
        value_result = _run_with_schema(isolated_hooks_dir, "_config_value autonomous_shipping")
        assert value_result.returncode == 4
        assert value_result.stdout == ""
        assert "autonomous_shipping" in value_result.stderr
        enabled_result = _run_with_schema(isolated_hooks_dir, "_config_enabled autonomous_shipping")
        assert enabled_result.returncode == 4
        assert enabled_result.stdout == ""

    def test_state_file_backed_key_with_corrupted_default_still_returns_exit_4(
        self, tmp_path, monkeypatch
    ):
        """_config_resolve validates default eagerly, right after the schema
        row is read -- before any state-file lookup -- matching
        _config.py's config_value(), which raises ConfigSchemaRowTruncatedError
        the same way regardless of whether the key's own resolution would
        ever consume default (claude/.claude/scripts/_config.py:376-386).
        handoff_nudge has a real, valid claude-config.toml entry here and
        would resolve fine on its own -- default is never on its resolution
        path -- but a truncated schema row is still a truncated schema row.
        Pins that this exit-4 case reaches state-file-backed keys too, not
        only the no-state-file fallback case
        test_empty_default_column_returns_exit_4_not_a_silent_grant covers."""
        home = tmp_path / "home"
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        _write_state_file(home, "handoff_nudge = true\n")
        isolated_hooks_dir = _isolated_hooks_dir_with_empty_default_column(tmp_path, "handoff_nudge")
        value_result = _run_with_schema(isolated_hooks_dir, "_config_value handoff_nudge")
        assert value_result.returncode == 4
        assert value_result.stdout == ""
        assert "handoff_nudge" in value_result.stderr


# ---------------------------------------------------------------------------
# CONFIG_DIR_OVERRIDE positional argument to _config_value/_config_enabled.
# ---------------------------------------------------------------------------


class TestConfigDirOverrideArgument:
    def test_override_is_read_from_instead_of_the_environment(self, isolated_home, monkeypatch):
        """A caller-supplied CONFIG_DIR_OVERRIDE skips CLAUDE_CONFIG_DIR/$HOME
        resolution entirely -- an override pointed at a directory with its
        own state file must win even when CLAUDE_CONFIG_DIR names a
        different, non-conforming one."""
        override_dir = isolated_home / "override-config"
        override_dir.mkdir()
        _write_state_file(isolated_home, "handoff_nudge = false\n", config_dir=override_dir)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(isolated_home / "unrelated-config"))
        result = _run(f'_config_value handoff_nudge "{override_dir}"')
        assert result.stdout == "false"
        assert result.returncode == 0

    def test_empty_string_override_falls_through_to_normal_resolution(self, isolated_home):
        """CONFIG_DIR_OVERRIDE="" must be treated the same as no override at
        all, matching bash's `[ -n "$config_dir_override" ]` test. See
        test_config_parser_parity.py for the Python-side differential
        coverage of this same parameter."""
        _write_state_file(isolated_home, "handoff_nudge = false\n")
        result = _run('_config_value handoff_nudge ""')
        assert result.stdout == "false"
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# legacy-probe-on-resolution-failure divergence between worktree_required
# (true) and autonomous_shipping (false)
# ---------------------------------------------------------------------------


class TestLegacyProbeOnResolutionFailure:
    def test_worktree_required_probes_home_legacy_file_on_resolution_failure(self, isolated_home, monkeypatch):
        (isolated_home / ".claude" / "worktree-required").touch()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "relative/not-absolute")
        result = _run("_config_value worktree_required")
        assert result.returncode == 0
        assert result.stdout == "true"

    def test_autonomous_shipping_does_not_probe_on_resolution_failure(self, isolated_home, monkeypatch):
        (isolated_home / ".claude" / "autonomous-shipping-required").touch()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "relative/not-absolute")
        result = _run("_config_value autonomous_shipping")
        assert result.returncode == 2
        assert result.stdout == ""


# ---------------------------------------------------------------------------
# config-dir-or-home union semantics
# ---------------------------------------------------------------------------


class TestUnionSemantics:
    def test_explicit_false_in_config_dir_does_not_defeat_true_under_home_legacy(self, isolated_home, monkeypatch):
        """An explicit `false` state-file row at the resolved config dir must
        not defeat a `true` produced by $HOME/.claude's legacy file -- the
        union is OR'd across each location's own independently-resolved
        effective value, not "first location found wins"."""
        config_dir = isolated_home / "altconfig"
        config_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        _write_state_file(isolated_home, "worktree_required = false\n", config_dir=config_dir)
        (isolated_home / ".claude" / "worktree-required").touch()

        result = _run("_config_value worktree_required")
        assert result.stdout == "true"
        assert result.returncode == 0

    def test_state_file_present_but_key_absent_still_unions_with_home_legacy(self, isolated_home, monkeypatch):
        """A state file present at the resolved config dir but with no row
        for this key falls through to that location's own legacy check
        (absent here), then still unions against $HOME/.claude's legacy
        file for a config-dir-or-home key."""
        config_dir = isolated_home / "altconfig"
        config_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        _write_state_file(isolated_home, "commit_stall_block = true\n", config_dir=config_dir)
        (isolated_home / ".claude" / "worktree-required").touch()

        result = _run("_config_value worktree_required")
        assert result.stdout == "true"


# ---------------------------------------------------------------------------
# State-file-row precedence over a disagreeing legacy file, for a
# content-matches key (pr_cost_disclosure) -- not just a boolean presence
# check, since content-matches resolution compares trimmed file content
# against an expected literal rather than mere file existence.
# ---------------------------------------------------------------------------


class TestLegacyPrecedence:
    def test_state_file_value_wins_over_disagreeing_legacy_file(self, isolated_home):
        _write_state_file(isolated_home, 'pr_cost_disclosure = "dollars"\n')
        (isolated_home / ".claude" / "pr-cost-disclosure").write_text("notdollars\n")

        result = _run("_config_value pr_cost_disclosure")
        assert result.stdout == "dollars"

    def test_legacy_file_only_consulted_when_key_entirely_absent_from_state_file(self, isolated_home):
        _write_state_file(isolated_home, "handoff_nudge = true\n")
        (isolated_home / ".claude" / ".commit-stall-block-disabled").touch()

        result = _run("_config_value commit_stall_block")
        assert result.stdout == "false"


# ---------------------------------------------------------------------------
# The enum literal's own quoting requirement -- genuine TOML writes a
# boolean bare and any other scalar quoted, so pr_cost_disclosure's own
# literal ("dollars") must be quoted to parse; a bare literal is malformed,
# not silently accepted.
# ---------------------------------------------------------------------------


class TestEnumValueQuotingGrammar:
    def test_bare_enum_literal_is_rejected_as_malformed(self, isolated_home):
        _write_state_file(isolated_home, "pr_cost_disclosure = dollars\n")
        result = _run("_config_value pr_cost_disclosure")
        assert result.stdout == "false"
        assert "malformed line" in result.stderr
        assert "pr_cost_disclosure = dollars" in result.stderr

    def test_quoted_enum_literal_resolves(self, isolated_home):
        _write_state_file(isolated_home, 'pr_cost_disclosure = "dollars"\n')
        result = _run("_config_value pr_cost_disclosure")
        assert result.stdout == "dollars"
        assert result.stderr == ""

    def test_bare_boolean_value_on_enum_key_is_rejected_as_malformed(self, isolated_home):
        """`true`/`false` are always grammar-valid bare tokens, so a bare
        `true` on an enum-typed key passes the bareness gate the sibling
        test above exercises and instead reaches the enum:* schema-type
        check, which must reject it too -- pins today's behavior against a
        future loosening of that case arm that might otherwise accept a
        bare boolean as meaning "enabled"."""
        _write_state_file(isolated_home, "pr_cost_disclosure = true\n")
        result = _run("_config_value pr_cost_disclosure")
        assert result.stdout == "false"
        assert "malformed line" in result.stderr
        assert "pr_cost_disclosure = true" in result.stderr


# ---------------------------------------------------------------------------
# Legacy-polarity coverage: presence-enables, presence-disables,
# content-matches.
# ---------------------------------------------------------------------------


class TestLegacyPolarity:
    def test_presence_enables(self, isolated_home):
        assert _run("_config_value permission_prompt_tracking").stdout == "false"
        (isolated_home / ".claude" / "track-permission-prompts").touch()
        assert _run("_config_value permission_prompt_tracking").stdout == "true"

    def test_presence_disables(self, isolated_home):
        assert _run("_config_value round_consult_gate").stdout == "true"
        (isolated_home / ".claude" / ".round-consult-gate-disabled").touch()
        assert _run("_config_value round_consult_gate").stdout == "false"

    def test_content_matches_wrong_content_resolves_default(self, isolated_home):
        (isolated_home / ".claude" / "pr-cost-disclosure").write_text("euros\n")
        assert _run("_config_value pr_cost_disclosure").stdout == "false"

    def test_content_matches_crlf_authored_file_still_resolves(self, isolated_home):
        """A CRLF-authored one-line sentinel must resolve identically to an
        LF one -- [:space:] trimming (not [:blank:]) strips the trailing
        CR."""
        (isolated_home / ".claude" / "pr-cost-disclosure").write_bytes(b"dollars\r\n")
        assert _run("_config_value pr_cost_disclosure").stdout == "dollars"

    @pytest.mark.parametrize("content", ["DOLLARS", "Dollars", "DoLLaRs\n"])
    def test_content_matches_case_folded(self, isolated_home, content):
        (isolated_home / ".claude" / "pr-cost-disclosure").write_text(content)
        assert _run("_config_value pr_cost_disclosure").stdout == "dollars"

    @pytest.mark.skipif(
        _NON_C_UTF8_LOCALE is None,
        reason="no non-C UTF-8 locale installed on this machine to exercise the ambient-locale guard",
    )
    @pytest.mark.parametrize("content", ["DOLLARS", "Dollars", "DoLLaRs\n"])
    def test_content_matches_case_folded_under_non_c_ambient_locale(self, isolated_home, content, monkeypatch):
        """Targets _config_location_value's own `local LC_ALL=C`, which
        scopes its `[A-Z]` guard to ASCII regardless of the caller's
        ambient locale.
        This class's other cases run under the test runner's own
        (typically C or POSIX) ambient locale, so only a real non-C UTF-8
        locale here can exercise that scoping.
        Distinct from test_config_parser_parity.py's U+212A fixture, which
        targets _config_line_key_value's own (already-locale-scoped) guard
        instead."""
        monkeypatch.setenv("LC_ALL", _NON_C_UTF8_LOCALE)
        (isolated_home / ".claude" / "pr-cost-disclosure").write_text(content)
        assert _run("_config_value pr_cost_disclosure").stdout == "dollars"

    def test_content_matches_whitespace_only_resolves_default(self, isolated_home):
        (isolated_home / ".claude" / "pr-cost-disclosure").write_text(" \n")
        assert _run("_config_value pr_cost_disclosure").stdout == "false"

    def test_content_matches_second_line_of_junk_resolves_default(self, isolated_home):
        """Fail-open shape: reading only the first line would treat
        "dollars\\nallowance" as a match -- the whole (trimmed) content must
        equal the expected literal, not just its first line."""
        (isolated_home / ".claude" / "pr-cost-disclosure").write_text("dollars\nallowance\n")
        assert _run("_config_value pr_cost_disclosure").stdout == "false"

    @pytest.mark.parametrize("content", ["dollars123", "xdollars"])
    def test_content_matches_glued_extra_characters_resolves_default(self, isolated_home, content):
        """Fail-open shape: an unanchored substring compare would match
        extra characters glued onto either end of the expected literal --
        the compare must be an anchored equality test."""
        (isolated_home / ".claude" / "pr-cost-disclosure").write_text(content)
        assert _run("_config_value pr_cost_disclosure").stdout == "false"

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root bypasses discretionary file-permission bits (CAP_DAC_OVERRIDE on Linux), "
        "so chmod(0o000) does not make the file unreadable and this would resolve enabled instead of default",
    )
    def test_content_matches_unreadable_legacy_file_resolves_default(self, isolated_home):
        """Proves the guarded read (`|| raw=""`) degrades to the schema
        default rather than aborting or resolving as enabled."""
        sentinel_path = isolated_home / ".claude" / "pr-cost-disclosure"
        sentinel_path.write_text("dollars\n")
        sentinel_path.chmod(0o000)
        try:
            result = _run("_config_value pr_cost_disclosure")
        finally:
            sentinel_path.chmod(0o644)
        assert result.stdout == "false"


class TestUnrecognizedLegacyPolarityFallback:
    """An unrecognized config-keys.psv legacy-polarity value (a schema-file
    typo, since this column has no other writer) falls through to the
    key's schema default for four of the five enforcement-critical keys --
    already that key's fail-closed direction, pinned here explicitly.
    worktree_required is the one exception: its schema default ("false") is
    the permissive direction, so _config_location_value fails closed to
    "true" instead, per its own comment on that arm."""

    @pytest.mark.parametrize(
        "key,expected",
        [
            ("worktree_required", "true"),
            ("autonomous_shipping", "false"),
            ("commit_stall_block", "true"),
            ("round_consult_gate", "true"),
            ("authorization_boundary_restore", "true"),
        ],
    )
    def test_enforcement_critical_key_resolves_fail_closed(self, tmp_path, key, expected):
        isolated_hooks_dir = _isolated_hooks_dir_with_legacy_polarity_override(
            tmp_path, key, "not-a-real-polarity"
        )
        target_dir = tmp_path / "cfgdir"
        target_dir.mkdir()
        result = _run_with_schema(isolated_hooks_dir, f'_config_location_value {key} "{target_dir}"')
        assert result.stdout == expected
        assert "unrecognized legacy-polarity value" in result.stderr

    @pytest.mark.parametrize(
        "key,schema_default",
        [
            ("worktree_required", "false"),
            ("autonomous_shipping", "false"),
            ("commit_stall_block", "true"),
            ("round_consult_gate", "true"),
            ("authorization_boundary_restore", "true"),
        ],
    )
    def test_empty_legacy_polarity_falls_through_silently(self, tmp_path, key, schema_default):
        """An empty legacy-polarity is the pre-existing, tested schema shape
        for a plain key with no legacy file to protect (see
        TestConfigScaffold::test_plain_key_with_no_legacy_polarity_still_gets_its_default_row),
        not a corrupted schema value -- it must resolve to the key's own
        schema default with no warning, even for worktree_required, whose
        fail-closed override applies only to a non-empty unrecognized value."""
        isolated_hooks_dir = _isolated_hooks_dir_with_legacy_polarity_override(tmp_path, key, "")
        target_dir = tmp_path / "cfgdir"
        target_dir.mkdir()
        result = _run_with_schema(isolated_hooks_dir, f'_config_location_value {key} "{target_dir}"')
        assert result.stdout == schema_default
        assert result.stderr == ""


class TestPrCostDisclosureDoesNotUnion:
    """pr_cost_disclosure's `resolution` column is `config-dir`, not
    `config-dir-or-home` -- unlike worktree_required/autonomous_shipping, a
    legacy file present only at $HOME/.claude must never activate it once
    CLAUDE_CONFIG_DIR diverges from $HOME/.claude, since there is no union
    for this key to fall back on."""

    def test_home_only_legacy_file_does_not_activate_a_diverged_config_dir(self, isolated_home, monkeypatch):
        config_dir = isolated_home / "altconfig"
        config_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        (isolated_home / ".claude" / "pr-cost-disclosure").write_text("dollars\n")

        result = _run("_config_value pr_cost_disclosure")
        assert result.stdout == "false"


# ---------------------------------------------------------------------------
# A subset-violating line is skipped (with a warning), not treated as
# a whole-document parse failure -- the remaining valid keys still resolve.
# ---------------------------------------------------------------------------


class TestPartialFileFixture:
    def test_valid_keys_resolve_despite_one_malformed_line(self, isolated_home):
        _write_state_file(
            isolated_home,
            "# a comment\n"
            "handoff_nudge = false\n"
            "this line has no equals sign\n"
            "commit_stall_block = true\n",
        )
        handoff = _run("_config_value handoff_nudge")
        stall = _run("_config_value commit_stall_block")
        assert handoff.stdout == "false"
        assert stall.stdout == "true"

    def test_malformed_line_emits_a_warning(self, isolated_home):
        _write_state_file(
            isolated_home,
            "handoff_nudge = false\nthis line has no equals sign\n",
        )
        result = _run("_config_value handoff_nudge")
        assert "malformed line" in result.stderr
        assert "this line has no equals sign" in result.stderr

    def test_malformed_key_shape_is_skipped_like_a_malformed_value(self, isolated_home):
        """A line with a valid `=` but a key that fails the
        `[A-Za-z0-9_-]+` grammar (a space, here) is malformed -- distinct
        from a malformed value -- and must be skipped the same way, leaving
        the remaining valid key resolvable."""
        _write_state_file(
            isolated_home,
            "bad key = true\nhandoff_nudge = false\n",
        )
        result = _run("_config_value handoff_nudge")
        assert result.stdout == "false"
        assert "malformed line" in result.stderr
        assert "bad key = true" in result.stderr


# ---------------------------------------------------------------------------
# A line whose key is grammatically valid but has no row at all in
# config-keys.psv (a case- or spelling-typo'd key) is a distinct case from a
# malformed line -- it parses cleanly and would otherwise sit silently
# ignored forever.
# ---------------------------------------------------------------------------


class TestUnrecognizedKeyWarning:
    def test_unrecognized_key_line_is_skipped_with_its_own_warning(self, isolated_home):
        _write_state_file(
            isolated_home,
            "handoff_nudge = false\nWorktree_Required = true\n",
        )
        result = _run("_config_value handoff_nudge")
        assert result.stdout == "false"
        assert "unrecognized key" in result.stderr
        assert "Worktree_Required = true" in result.stderr
        assert "malformed line" not in result.stderr

    def test_unrecognized_key_does_not_shadow_a_similarly_named_real_key(self, isolated_home):
        """A typo'd key (missing the trailing 'e') never matches the real
        key it was meant to be, so the real key still falls through to its
        own legacy-file-then-default chain undisturbed."""
        _write_state_file(isolated_home, 'pr_cost_disclosur = "dollars"\n')
        result = _run("_config_value pr_cost_disclosure")
        assert result.stdout == "false"
        assert "unrecognized key" in result.stderr
        assert 'pr_cost_disclosur = "dollars"' in result.stderr


# ---------------------------------------------------------------------------
# config-keys.psv itself unreadable must not make _config_read_key_from_file
# treat every row as unrecognized -- a stow-relink race or interrupted
# `git pull` is transient, and the key's own row is still the real,
# authoritative value.
# ---------------------------------------------------------------------------


class TestReadKeyFromFileSchemaUnreadable:
    def test_existing_row_still_resolves_when_schema_is_unreadable(self, tmp_path):
        """Calls _config_read_key_from_file directly, not _config_value --
        _config_value's own earlier _config_schema_field("resolution") call
        already short-circuits before reaching this function, so it can't
        exercise this code path."""
        isolated_hooks_dir = tmp_path / "isolated-hooks"
        isolated_hooks_dir.mkdir()
        # config-keys.psv deliberately never created here, matching
        # TestMissingSchemaFile's isolation technique.
        (isolated_hooks_dir / "_config.sh").symlink_to(_CONFIG_SH)
        state_file = tmp_path / "claude-config.toml"
        state_file.write_text("commit_stall_block = true\n")

        result = _run_with_schema(
            isolated_hooks_dir,
            f'_config_read_key_from_file commit_stall_block "{state_file}"',
        )
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert result.stdout == "true"
        assert "unrecognized key" not in result.stderr

    def test_existing_row_still_resolves_when_schema_is_readable_but_empty(self, tmp_path):
        """A schema file that exists, passes [ -r ], and parses zero rows (a
        mid-write truncation race, not a fully-missing/unreadable file) must
        degrade the same way as an unreadable schema -- not reject every row
        as unrecognized, which would reproduce this class's original bug
        through a narrower trigger."""
        isolated_hooks_dir = tmp_path / "isolated-hooks"
        isolated_hooks_dir.mkdir()
        (isolated_hooks_dir / "config-keys.psv").write_text("")
        (isolated_hooks_dir / "_config.sh").symlink_to(_CONFIG_SH)
        state_file = tmp_path / "claude-config.toml"
        state_file.write_text("commit_stall_block = true\n")

        result = _run_with_schema(
            isolated_hooks_dir,
            f'_config_read_key_from_file commit_stall_block "{state_file}"',
        )
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert result.stdout == "true"
        assert "unrecognized key" not in result.stderr

    def test_type_invalid_value_is_rejected_not_accepted_when_schema_unreadable(self, tmp_path):
        """Regression test for a latent gap (not reachable by any current
        caller -- _config_value's own earlier _config_schema_field call
        already short-circuits on an unreadable schema before ever reaching
        this function, and migrate-legacy-config.sh's own `set -euo
        pipefail` schema read aborts first too): with the schema
        unreadable, key_type is left empty, and a bool key's own
        semantically-invalid value ("banana", not "true"/"false") must not
        resolve as authoritative just because its type couldn't be
        checked -- _config_enabled's any-value-but-false rule would
        otherwise treat "banana" as enabled. Insurance against a future
        caller reintroducing this as a live, reachable bug. Written quoted
        (`"banana"`) -- a bare `banana` now fails the value's own bareness
        grammar (only true/false may be bare) before ever reaching this
        schema-type check, which would exercise a different code path than
        the one this test targets."""
        isolated_hooks_dir = tmp_path / "isolated-hooks"
        isolated_hooks_dir.mkdir()
        # config-keys.psv deliberately never created here, matching this
        # class's other tests.
        (isolated_hooks_dir / "_config.sh").symlink_to(_CONFIG_SH)
        state_file = tmp_path / "claude-config.toml"
        state_file.write_text('autonomous_shipping = "banana"\n')

        result = _run_with_schema(
            isolated_hooks_dir,
            f'_config_read_key_from_file autonomous_shipping "{state_file}"; echo "status=$?"',
        )
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert result.stdout == "status=1\n", (
            "a type-invalid value must not print anything and must return 1 "
            f"(not found), not resolve as authoritative: stdout={result.stdout!r}"
        )
        assert "cannot validate its type" in result.stderr


# ---------------------------------------------------------------------------
# _config_read_key_from_file/_config_location_value dispatch on argument
# COUNT between a base 2-arg form and a full precomputed-args form. An
# intermediate count is a caller-side bug, not "no optional args passed",
# and must be rejected rather than silently self-deriving.
# ---------------------------------------------------------------------------


class TestPrecomputedArgsArityGuard:
    def test_read_key_from_file_rejects_three_args(self, tmp_path):
        state_file = tmp_path / "claude-config.toml"
        state_file.write_text("commit_stall_block = true\n")
        result = _run(
            f'_config_read_key_from_file commit_stall_block "{state_file}" some_known_keys'
        )
        assert result.returncode == 1
        assert "expected 2 or 4 args, got 3" in result.stderr

    def test_location_value_rejects_three_args(self, isolated_home):
        result = _run(
            f'_config_location_value commit_stall_block "{isolated_home}" some_known_keys'
        )
        assert result.returncode == 1
        assert "expected 2 or 6 args, got 3" in result.stderr

    def test_location_value_rejects_five_args(self, isolated_home):
        result = _run(
            f'_config_location_value commit_stall_block "{isolated_home}" '
            "known_keys bool worktree-required"
        )
        assert result.returncode == 1
        assert "expected 2 or 6 args, got 5" in result.stderr

    def test_read_key_from_file_with_full_arity_and_empty_known_keys_skips_membership_check(
        self, tmp_path
    ):
        """An empty KNOWN_KEYS via the 4-arg form must be treated as
        "schema unreadable" and skip the membership check. The 2-arg
        self-deriving form would instead re-derive KNOWN_KEYS and reject
        this key as unrecognized. This test pins that count-based dispatch
        contract, distinct from the wrong-count rejection tested above."""
        state_file = tmp_path / "claude-config.toml"
        state_file.write_text("totally_not_a_real_key = true\n")

        result = _run(f'_config_read_key_from_file totally_not_a_real_key "{state_file}" "" ""')

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert result.stdout == "true"
        assert "unrecognized key" not in result.stderr


class TestSingleLocationBranchSchemaRowCallCount:
    """Pins the call-count reduction the single-location branch gets from
    threading known_keys/key_type/legacy_filename/legacy_polarity into
    _config_location_value's 6-arg precomputed form. Wraps
    _config_schema_row via the declare-f-plus-sed rename technique used
    elsewhere in this file. Counts into a tmp_path file, not a shell
    variable, because _config_location_value runs inside _config_resolve's
    own `$(...)` subshell capture, whose variable mutations don't propagate
    back."""

    def test_schema_row_called_once_for_a_state_file_backed_key(self, isolated_home, tmp_path):
        """commit_stall_block's row is present in the state file, so
        _config_location_value's state-file read resolves it directly --
        the only schema pass is _config_resolve's own front-of-function
        _config_schema_row call."""
        _write_state_file(isolated_home, "commit_stall_block = true\n")
        count_file = tmp_path / "schema_row_calls"

        result = _run(
            f"{_schema_row_call_count_wrapper(count_file)}_config_value commit_stall_block"
        )

        assert result.stdout == "true", f"stderr={result.stderr!r}"
        assert count_file.read_text() == "x", (
            "a single-location key whose row is present in the state file "
            "must call _config_schema_row exactly once -- a second call "
            "means _config_location_value re-derived schema state instead "
            "of using the locals _config_resolve already threaded through: "
            f"{count_file.read_text()!r}"
        )

    def test_schema_row_called_twice_when_falling_through_to_the_schema_default(self, tmp_path):
        """Counterpart for the path that reaches _config_location_value's
        closing _config_schema_field "$key" default call (_config.sh:644),
        which is not one of the four threaded locals, so it still makes its
        own _config_schema_row pass. No real config-keys.psv row reaches
        this path today: every real legacy-polarity value
        (presence-enables/presence-disables/content-matches) resolves
        definitively regardless of legacy-file presence. Exercising the
        fallback needs a synthetic empty legacy-polarity
        (TestUnrecognizedLegacyPolarityFallback's own
        test_empty_legacy_polarity_falls_through_silently shape, via
        _isolated_hooks_dir_with_legacy_polarity_override)."""
        isolated_hooks_dir = _isolated_hooks_dir_with_legacy_polarity_override(
            tmp_path, "commit_stall_block", ""
        )
        target_dir = tmp_path / "cfgdir"
        target_dir.mkdir()
        count_file = tmp_path / "schema_row_calls"

        result = _run_with_schema(
            isolated_hooks_dir,
            f'{_schema_row_call_count_wrapper(count_file)}_config_value commit_stall_block "{target_dir}"',
        )

        assert result.stdout == "true", f"stderr={result.stderr!r}"
        assert count_file.read_text() == "xx", (
            "a single-location key falling through to the schema default "
            "must call _config_schema_row exactly twice: once from "
            "_config_resolve's own lookup, once from "
            "_config_location_value's closing _config_schema_field default "
            f"call: {count_file.read_text()!r}"
        )


class TestHomeOnlyFallbackBranchSchemaRowCallCount:
    """Same call-count property as TestSingleLocationBranchSchemaRowCallCount,
    pinned for the home-only-fallback branch instead. worktree_required is
    the only key that reaches this branch, when primary_dir is unresolvable.
    A relative CLAUDE_CONFIG_DIR makes _lib_config_dir fail, landing here --
    the same technique TestMemoizationHomeOnlyFallbackBranch uses above."""

    def test_schema_row_called_once_for_a_state_file_backed_key(
        self, isolated_home, monkeypatch, tmp_path
    ):
        """worktree_required's row is present in $HOME/.claude's state
        file, so _config_location_value's state-file read resolves it
        directly -- the only schema pass is _config_resolve's own
        front-of-function _config_schema_row call."""
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "relative/not-absolute")
        _write_state_file(isolated_home, "worktree_required = true\n")
        count_file = tmp_path / "schema_row_calls"

        result = _run(
            f"{_schema_row_call_count_wrapper(count_file)}_config_value worktree_required"
        )

        assert result.stdout == "true", f"stderr={result.stderr!r}"
        assert count_file.read_text() == "x", (
            "the home-only-fallback branch's success path must call "
            "_config_schema_row exactly once -- a second call means "
            "_config_location_value re-derived schema state instead of "
            "using the locals _config_resolve already threaded through: "
            f"{count_file.read_text()!r}"
        )


# ---------------------------------------------------------------------------
# _config_resolve's union branch must check _config_location_value's own exit
# status before treating its captured stdout as authoritative. The
# `_config_location_value() { return 1; }` override stub below forces a
# nonzero status on both union-branch calls, proving the fold fails closed
# to its per-key fail-safe value instead of reading the resulting empty
# stdout as "enabled".
# ---------------------------------------------------------------------------


class TestUnionBranchLocationValueFailure:
    def test_location_value_failure_resolves_autonomous_shipping_to_not_enabled(
        self, isolated_home, monkeypatch
    ):
        """Simulates an arity-guard trip (nonzero status, empty stdout) to
        confirm the union branch fails closed to "false" instead of reading
        the empty output as enabled."""
        config_dir = isolated_home / "altconfig"
        config_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        override = "_config_location_value() { return 1; }; "

        value_result = _run(f"{override}_config_value autonomous_shipping")
        assert value_result.stdout == "false"
        assert value_result.returncode == 0

        enabled_result = _run(f"{override}_config_enabled autonomous_shipping")
        assert enabled_result.returncode == 1

    def test_location_value_failure_resolves_worktree_required_to_enabled(
        self, isolated_home, monkeypatch
    ):
        """Mirrors the autonomous_shipping test above, but for
        worktree_required, whose failure-safe default is the opposite
        direction: "true", not "false". Resolving "false" here would
        silently disarm write-safety enforcement instead of merely
        withholding a permission grant.

        The _config_value assertion below is the one that actually
        discriminates a regression here: the trailing _config_enabled
        assertion alone would not catch a union-branch fallthrough that
        resolves to an "enabled" value for this key."""
        config_dir = isolated_home / "altconfig"
        config_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        override = "_config_location_value() { return 1; }; "

        value_result = _run(f"{override}_config_value worktree_required")
        assert value_result.stdout == "true"
        assert value_result.returncode == 0

        enabled_result = _run(f"{override}_config_enabled worktree_required")
        assert enabled_result.returncode == 0


# ---------------------------------------------------------------------------
# A grammar-conforming but schema-invalid `bool` value must warn and fall
# through to the legacy-file-then-default chain, not resolve as authoritative
# -- otherwise a typo (or a value a hand-editor would believe disables it,
# e.g. "off") on autonomous_shipping would silently grant it, since
# _config_enabled treats any value other than the literal "false" as enabled.
# ---------------------------------------------------------------------------


class TestSchemaTypeValidationOnRead:
    def test_autonomous_shipping_non_boolean_value_warns_and_falls_through(self, isolated_home):
        """Quoted, not bare: a bare `notabool` would hit the value-subset
        grammar's bareness gate first (`TestEnumValueQuotingGrammar` already
        covers that path), never reaching the schema-type check this test
        targets."""
        _write_state_file(isolated_home, 'autonomous_shipping = "notabool"\n')
        result = _run("_config_value autonomous_shipping")
        assert result.stdout == "false"
        assert result.returncode == 0
        assert "malformed line" in result.stderr
        assert 'autonomous_shipping = "notabool"' in result.stderr
        assert _run("_config_enabled autonomous_shipping").returncode == 1

    def test_worktree_required_non_boolean_value_warns_and_falls_through(self, isolated_home):
        """Quoted, not bare -- see the sibling test above for why."""
        _write_state_file(isolated_home, 'worktree_required = "notabool"\n')
        result = _run("_config_value worktree_required")
        assert result.stdout == "false"
        assert result.returncode == 0
        assert "malformed line" in result.stderr
        assert 'worktree_required = "notabool"' in result.stderr
        assert _run("_config_enabled worktree_required").returncode == 1

    @pytest.mark.parametrize(
        "key",
        ["round_consult_gate", "commit_stall_block", "authorization_boundary_restore"],
    )
    def test_presence_disables_key_non_boolean_value_falls_closed_without_legacy_file(
        self, isolated_home, key
    ):
        """A malformed row for a presence-disables key is skipped exactly
        like a fully-absent row, falling through to its own legacy-file
        check -- absent here, so it resolves "true" (armed), the same as
        the key's own fail-closed schema default."""
        _write_state_file(isolated_home, f'{key} = "notabool"\n')
        result = _run(f"_config_value {key}")
        assert result.stdout == "true"
        assert result.returncode == 0
        assert "malformed line" in result.stderr
        assert f'{key} = "notabool"' in result.stderr
        assert _run(f"_config_enabled {key}").returncode == 0

    @pytest.mark.parametrize(
        "key,legacy_filename",
        [
            ("round_consult_gate", ".round-consult-gate-disabled"),
            ("commit_stall_block", ".commit-stall-block-disabled"),
            ("authorization_boundary_restore", ".authorization-boundary-disabled"),
        ],
    )
    def test_presence_disables_key_non_boolean_value_resolves_legacy_opt_out(
        self, isolated_home, key, legacy_filename
    ):
        """The same malformed row, with its coexisting legacy opt-out file
        also present, resolves "false" (disarmed) -- the malformed row
        never becomes authoritative just because a legacy file happens to
        coexist with it."""
        _write_state_file(isolated_home, f'{key} = "notabool"\n')
        (isolated_home / ".claude" / legacy_filename).touch()
        result = _run(f"_config_value {key}")
        assert result.stdout == "false"
        assert result.returncode == 0
        assert "malformed line" in result.stderr
        assert _run(f"_config_enabled {key}").returncode == 1


# ---------------------------------------------------------------------------
# _config_set's atomic, comment/order-preserving rewrite, and its
# refuse-on-malformed-content contract.
# ---------------------------------------------------------------------------


class TestConfigSet:
    def test_rewrite_preserves_comments_blank_lines_and_key_order(self, isolated_home):
        _write_state_file(
            isolated_home,
            "# a leading comment\n"
            "commit_stall_block = true\n"
            "\n"
            "handoff_nudge = false\n",
        )
        result = _run("_config_set handoff_nudge true")
        assert result.returncode == 0

        state_file = isolated_home / ".claude" / "claude-config.toml"
        assert state_file.read_text() == (
            "# a leading comment\n"
            "commit_stall_block = true\n"
            "\n"
            "handoff_nudge = true\n"
        )

    def test_appends_a_new_row_when_key_absent(self, isolated_home):
        _write_state_file(isolated_home, "commit_stall_block = true\n")
        result = _run("_config_set handoff_nudge false")
        assert result.returncode == 0
        state_file = isolated_home / ".claude" / "claude-config.toml"
        assert state_file.read_text() == "commit_stall_block = true\nhandoff_nudge = false\n"

    def test_refuses_to_write_when_file_contains_a_malformed_line(self, isolated_home):
        original = "handoff_nudge = false\nthis line has no equals sign\n"
        _write_state_file(isolated_home, original)
        result = _run("_config_set handoff_nudge true")
        assert result.returncode != 0
        state_file = isolated_home / ".claude" / "claude-config.toml"
        assert state_file.read_text() == original

    def test_bool_key_rejects_a_non_bool_value(self, isolated_home):
        """handoff_nudge is `bool`-typed -- a write value other than
        true/false must be rejected before any write is attempted."""
        result = _run("_config_set handoff_nudge notabool")
        assert result.returncode != 0
        state_file = isolated_home / ".claude" / "claude-config.toml"
        assert not state_file.exists()

    def test_enum_key_accepts_false(self, isolated_home):
        result = _run("_config_set pr_cost_disclosure false")
        assert result.returncode == 0
        state_file = isolated_home / ".claude" / "claude-config.toml"
        assert state_file.read_text() == "pr_cost_disclosure = false\n"

    def test_enum_key_accepts_its_declared_literal(self, isolated_home):
        """The written line quotes the enum literal (`"dollars"`) -- a bare
        `dollars` is not valid TOML, even though the VALUE argument itself
        stays bare."""
        result = _run("_config_set pr_cost_disclosure dollars")
        assert result.returncode == 0
        state_file = isolated_home / ".claude" / "claude-config.toml"
        assert state_file.read_text() == 'pr_cost_disclosure = "dollars"\n'

    def test_enum_key_rejects_a_literal_other_than_its_own_or_false(self, isolated_home):
        """pr_cost_disclosure is `enum:dollars`-typed -- a write value other
        than `false` or `dollars` must be rejected before any write is
        attempted."""
        result = _run("_config_set pr_cost_disclosure euros")
        assert result.returncode != 0
        state_file = isolated_home / ".claude" / "claude-config.toml"
        assert not state_file.exists()

    def test_write_failure_leaves_state_file_untouched_and_cleans_up_temp_file(
        self, isolated_home, monkeypatch, tmp_path
    ):
        """The write block's own exit status is checked before `mv` -- a
        failed write (simulated here via a `mktemp` shim that hands back an
        already-read-only temp file, so the write block's own `>` redirect
        fails) must not install a truncated/empty file over the real state
        file, and must clean up the temp file rather than leaving it
        behind."""
        _write_state_file(isolated_home, "handoff_nudge = false\n")
        bin_dir = tmp_path / "readonly-mktemp-bin"
        bin_dir.mkdir()
        fixed_tmp_file = tmp_path / "pre-existing-readonly-tmp"
        shim = bin_dir / "mktemp"
        shim.write_text(
            "#!/bin/bash\n"
            f'touch "{fixed_tmp_file}"\n'
            f'chmod 400 "{fixed_tmp_file}"\n'
            f'printf "%s\\n" "{fixed_tmp_file}"\n'
        )
        shim.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
        result = _run("_config_set handoff_nudge true")
        assert result.returncode == 1
        state_file = isolated_home / ".claude" / "claude-config.toml"
        assert state_file.read_text() == "handoff_nudge = false\n"
        assert not fixed_tmp_file.exists()


# ---------------------------------------------------------------------------
# _config_scaffold is additive-only, honors an exclude-list, and -- for a
# key whose legacy-polarity is presence-enables/presence-disables/
# content-matches -- never backfills a default row over an absent one, so
# that key's own legacy file stays reachable via _config_location_value's
# read-time fallback for as long as the key has no state-file row of its
# own.
# ---------------------------------------------------------------------------


class TestConfigScaffold:
    def test_existing_hand_edited_row_survives_unchanged(self, isolated_home):
        _write_state_file(isolated_home, "handoff_nudge = false\n")
        result = _run("_config_scaffold")
        assert result.returncode == 0

        state_file = isolated_home / ".claude" / "claude-config.toml"
        # test_selection_tracking has no legacy-polarity (see
        # test_presence_and_content_matches_polarity_keys_stay_absent's own
        # docstring) so scaffold backfills its default row alongside the
        # untouched hand-edited one.
        assert state_file.read_text().splitlines() == [
            "handoff_nudge = false", "test_selection_tracking = false",
        ]

    def test_presence_and_content_matches_polarity_keys_stay_absent(self, isolated_home):
        """Every key but test_selection_tracking carries a legacy-polarity
        value and stays absent (backfilling would shadow its legacy file);
        test_selection_tracking has none, so scaffold backfills its default
        row alone."""
        result = _run("_config_scaffold")
        assert result.returncode == 0

        state_file = isolated_home / ".claude" / "claude-config.toml"
        assert state_file.read_text() == "test_selection_tracking = false\n"

    def test_legacy_file_created_after_scaffold_still_takes_effect(self, isolated_home):
        """A legacy kill switch touched after `install.sh`/
        migrate-legacy-config.sh has already scaffolded the state file must
        still flip its key's resolved value."""
        assert _run("_config_scaffold").returncode == 0
        assert _run("_config_value round_consult_gate").stdout == "true"
        (isolated_home / ".claude" / ".round-consult-gate-disabled").touch()
        assert _run("_config_value round_consult_gate").stdout == "false"

    def test_exclude_list_key_and_every_other_key_both_stay_absent(self, isolated_home):
        result = _run("_config_scaffold 'worktree_required autonomous_shipping'")
        assert result.returncode == 0

        state_file = isolated_home / ".claude" / "claude-config.toml"
        # Neither excluded key would have been backfilled anyway -- both
        # carry a legacy-polarity value. test_selection_tracking has none,
        # so it alone gets a default row.
        assert state_file.read_text() == "test_selection_tracking = false\n"

    def test_plain_key_with_no_legacy_polarity_still_gets_its_default_row(self, isolated_home, tmp_path):
        """A key with an empty legacy-polarity column has no legacy file to
        protect, so scaffold's original additive-only default-fill contract
        still applies to it. test_selection_tracking is the one real key
        with this shape and is covered directly against the real schema by
        TestConfigScaffold. This test exercises the same shape via an
        isolated config-keys.psv fixture."""
        isolated_hooks_dir = tmp_path / "isolated-hooks"
        isolated_hooks_dir.mkdir()
        (isolated_hooks_dir / "_config.sh").symlink_to(_CONFIG_SH)
        (isolated_hooks_dir / "config-keys.psv").write_text(
            "brand_new_capability|bool|true|config-dir|false||||Brand new capability|docs/x.md\n"
        )
        result = _run_with_schema(isolated_hooks_dir, "_config_scaffold")
        assert result.returncode == 0

        state_file = isolated_home / ".claude" / "claude-config.toml"
        assert state_file.read_text() == "brand_new_capability = true\n"

    def test_plain_key_with_no_legacy_polarity_can_still_be_excluded(self, isolated_home, tmp_path):
        isolated_hooks_dir = tmp_path / "isolated-hooks"
        isolated_hooks_dir.mkdir()
        (isolated_hooks_dir / "_config.sh").symlink_to(_CONFIG_SH)
        (isolated_hooks_dir / "config-keys.psv").write_text(
            "brand_new_capability|bool|true|config-dir|false||||Brand new capability|docs/x.md\n"
        )
        result = _run_with_schema(isolated_hooks_dir, "_config_scaffold brand_new_capability")
        assert result.returncode == 0

        state_file = isolated_home / ".claude" / "claude-config.toml"
        assert not state_file.exists() or state_file.read_text() == ""

    def test_enum_key_with_no_legacy_polarity_gets_a_quoted_default_row(self, isolated_home, tmp_path):
        """An enum-typed key with an empty legacy-polarity column (a schema
        shape none of today's real enum:* rows carry -- see
        test_every_enum_row_has_false_default in test_config_py.py for why
        that's currently true) reaches the default-fill loop, so its default
        must be quoted the same way _config_set quotes its own writes --
        _config_quote_value_for_write is the shared helper both call."""
        isolated_hooks_dir = tmp_path / "isolated-hooks"
        isolated_hooks_dir.mkdir()
        (isolated_hooks_dir / "_config.sh").symlink_to(_CONFIG_SH)
        (isolated_hooks_dir / "config-keys.psv").write_text(
            "brand_new_enum|enum:widgets|widgets|config-dir|false||||Brand new enum|docs/x.md\n"
        )
        result = _run_with_schema(isolated_hooks_dir, "_config_scaffold")
        assert result.returncode == 0

        state_file = isolated_home / ".claude" / "claude-config.toml"
        assert state_file.read_text() == 'brand_new_enum = "widgets"\n'

    def test_write_failure_leaves_state_file_untouched_and_cleans_up_temp_file(
        self, isolated_home, monkeypatch, tmp_path
    ):
        """Mirrors TestConfigSet's identical test: the write block's own
        exit status is checked before `mv` in _config_scaffold too."""
        _write_state_file(isolated_home, "handoff_nudge = false\n")
        bin_dir = tmp_path / "readonly-mktemp-bin"
        bin_dir.mkdir()
        fixed_tmp_file = tmp_path / "pre-existing-readonly-tmp"
        shim = bin_dir / "mktemp"
        shim.write_text(
            "#!/bin/bash\n"
            f'touch "{fixed_tmp_file}"\n'
            f'chmod 400 "{fixed_tmp_file}"\n'
            f'printf "%s\\n" "{fixed_tmp_file}"\n'
        )
        shim.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
        result = _run("_config_scaffold")
        assert result.returncode == 1
        state_file = isolated_home / ".claude" / "claude-config.toml"
        assert state_file.read_text() == "handoff_nudge = false\n"
        assert not fixed_tmp_file.exists()

    def test_write_failure_leaves_state_file_untouched_when_a_default_row_would_be_emitted(
        self, isolated_home, monkeypatch, tmp_path
    ):
        """Mirrors `test_plain_key_with_no_legacy_polarity_still_gets_its_default_row`'s
        fixture: a key with no legacy-polarity, so this run's write appends
        a default row. The sibling failure test above also reaches an
        unconditional write against the real schema, but its write fails
        before persisting anything -- not because every row carries a
        legacy-polarity value. This test pins that the failure check still
        fires on this different, row-appending path."""
        isolated_hooks_dir = tmp_path / "isolated-hooks"
        isolated_hooks_dir.mkdir()
        (isolated_hooks_dir / "_config.sh").symlink_to(_CONFIG_SH)
        (isolated_hooks_dir / "config-keys.psv").write_text(
            "brand_new_capability|bool|true|config-dir|false||||Brand new capability|docs/x.md\n"
        )
        bin_dir = tmp_path / "readonly-mktemp-bin"
        bin_dir.mkdir()
        fixed_tmp_file = tmp_path / "pre-existing-readonly-tmp"
        shim = bin_dir / "mktemp"
        shim.write_text(
            "#!/bin/bash\n"
            f'touch "{fixed_tmp_file}"\n'
            f'chmod 400 "{fixed_tmp_file}"\n'
            f'printf "%s\\n" "{fixed_tmp_file}"\n'
        )
        shim.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
        result = _run_with_schema(isolated_hooks_dir, "_config_scaffold")
        assert result.returncode == 1
        state_file = isolated_home / ".claude" / "claude-config.toml"
        assert not state_file.exists()
        assert not fixed_tmp_file.exists()


# ---------------------------------------------------------------------------
# _config_set/_config_scaffold's empty-or-`/`-resolved config dir guard --
# the first write in this repo routed through _lib_config_dir's resolver,
# which validates its own output no further than "absolute."
# ---------------------------------------------------------------------------


class TestConfigDirGuard:
    def test_config_set_refuses_when_config_dir_resolves_to_root(self, isolated_home, monkeypatch, tmp_path):
        """CLAUDE_CONFIG_DIR=/ resolves, via _lib_config_dir's own
        `${CLAUDE_CONFIG_DIR%/}` trailing-slash strip, to the empty string --
        _config_set's `""|/` guard must reject this before any mkdir/write
        is attempted. mkdir/mktemp/mv are shadowed on PATH so a guard
        regression is caught by the shim marker's existence, not by relying
        on a real write to `/` failing (which a root-privileged test runner
        would not catch)."""
        bin_dir = tmp_path / "guard-bin"
        bin_dir.mkdir()
        write_attempted = tmp_path / "write-attempted"
        _write_write_attempt_shims(bin_dir, write_attempted)
        monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/")
        result = _run("_config_set handoff_nudge true")
        assert result.returncode == 2
        assert not write_attempted.exists()
        assert not Path("/claude-config.toml").exists()

    def test_config_scaffold_refuses_when_config_dir_resolves_to_root(self, isolated_home, monkeypatch, tmp_path):
        bin_dir = tmp_path / "guard-bin"
        bin_dir.mkdir()
        write_attempted = tmp_path / "write-attempted"
        _write_write_attempt_shims(bin_dir, write_attempted)
        monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/")
        result = _run("_config_scaffold")
        assert result.returncode == 2
        assert not write_attempted.exists()
        assert not Path("/claude-config.toml").exists()


# ---------------------------------------------------------------------------
# _CONFIG_MEMO_CACHE: per-process memoization of an already-resolved key.
# Every test in this section drives two calls in a SINGLE bash process (one
# _run/_run_with_schema invocation, multiple statements joined by `;`) --
# a memo lives only for the lifetime of one process, so two separate
# subprocess invocations could never observe it either way.
# ---------------------------------------------------------------------------


class TestMemoization:
    def test_config_set_invalidates_a_memoized_value_for_config_value(self, isolated_home):
        """Both _config_value calls below must stay bare/uncaptured (printed
        directly, not wrapped in an inner $(...)) -- a captured call forks
        a subshell that discards its own memo store before this script's
        top-level frame ever sees it, exactly like install.sh's own real
        _config_value calls.
        A captured rewrite of this test would pass unconditionally
        regardless of whether _config_memo_reset exists, testing nothing."""
        result = _run(
            "_config_value handoff_nudge; "
            "printf '|'; "
            "_config_set handoff_nudge false; "
            "_config_value handoff_nudge"
        )
        assert result.stdout == "true|false", (
            "the second bare call must see the write _config_set just made, "
            f"not a value memoized before it: stdout={result.stdout!r}"
        )

    def test_config_set_invalidates_a_memoized_value_for_config_enabled(self, isolated_home):
        """Mirrors the sibling test above for _config_enabled: covers the
        _config_enabled-frame store and its own _config_memo_reset-driven
        invalidation together. Same bare-call constraint as the sibling
        test -- $? after a bare call reads the real exit status, not a
        subshell's, so this still exercises the top-level frame's cache."""
        result = _run(
            "_config_enabled commit_stall_block; "
            "printf 'first=%d|' \"$?\"; "
            "_config_set commit_stall_block false; "
            "_config_enabled commit_stall_block; "
            "printf 'second=%d' \"$?\""
        )
        assert result.stdout == "first=0|second=1", (
            f"the second bare call must reflect _config_set's write: stdout={result.stdout!r}"
        )

    def test_legacy_file_created_mid_process_does_not_affect_memoized_value(self, isolated_home):
        """A same-process legacy-file creation between two bare
        _config_value calls for one key does not flip the second call's
        result. This is the documented per-process memo contract, not a
        bug: _config_value has no way to know a legacy sentinel file
        changed underneath it without re-reading the filesystem, which is
        exactly the cost memoization exists to skip. Only
        _config_set/_config_scaffold invalidate the memo."""
        legacy_path = isolated_home / ".claude" / "track-permission-prompts"
        result = _run(
            "_config_value permission_prompt_tracking; "
            "printf '|'; "
            f'touch "{legacy_path}"; '
            "_config_value permission_prompt_tracking"
        )
        assert result.stdout == "false|false", (
            "the second bare call must still return the value memoized before "
            f"the legacy file appeared, per the per-process memo contract: stdout={result.stdout!r}"
        )

    def test_two_different_keys_memoized_without_cross_key_corruption(self, isolated_home):
        """Two different keys memoized in one process, re-reading the first
        one last -- pins the memo cache's own membership/extraction logic
        against cross-key corruption, a shape a single-key test structurally
        can't catch."""
        result = _run(
            "_config_value autonomous_shipping; "
            "printf '|'; "
            "_config_value handoff_nudge; "
            "printf '|'; "
            "_config_value autonomous_shipping"
        )
        assert result.stdout == "false|true|false", (
            f"autonomous_shipping's own memoized value must survive handoff_nudge "
            f"being memoized in between: stdout={result.stdout!r}"
        )

    def test_transient_exit_3_is_not_memoized_so_a_later_schema_fix_resolves(self, isolated_home, tmp_path):
        """Only a successfully resolved value is memoized. A first
        _config_value call fails with a transient exit 3 (config-keys.psv
        missing), the schema file is then supplied in the same process, and
        a second bare call for the same key now resolves successfully. If
        the failed lookup had been memoized, this second call would either
        replay the failure or resolve to a corrupted empty value instead."""
        isolated_hooks_dir = tmp_path / "isolated-hooks"
        isolated_hooks_dir.mkdir()
        (isolated_hooks_dir / "_config.sh").symlink_to(_CONFIG_SH)
        schema_path = isolated_hooks_dir / "config-keys.psv"
        result = _run_with_schema(
            isolated_hooks_dir,
            "_config_value commit_stall_block; "
            "printf 'status1=%d|' \"$?\"; "
            f'cp "{_CONFIG_KEYS_PSV}" "{schema_path}"; '
            "_config_value commit_stall_block; "
            "printf '|status2=%d' \"$?\"",
        )
        assert result.stdout == "status1=3|true|status2=0", (
            f"exit 3 must not be memoized, so the fixed-schema call resolves "
            f"normally: stdout={result.stdout!r}"
        )

    def test_transient_exit_4_truncated_row_is_not_memoized_so_a_later_fix_resolves(
        self, isolated_home, tmp_path
    ):
        """Companion to the exit-3 test above, for _config_value's other
        transient-failure call site: a truncated resolution-critical
        column (empty `resolution`) is a different check at a different
        point in _config_value than a missing schema file, and is
        otherwise unexercised by any memo test."""
        isolated_hooks_dir = tmp_path / "isolated-hooks-truncated"
        isolated_hooks_dir.mkdir()
        (isolated_hooks_dir / "_config.sh").symlink_to(_CONFIG_SH)
        schema_path = isolated_hooks_dir / "config-keys.psv"
        # Empty `resolution` (the 4th field) is the truncation _config_value
        # itself checks; built via join rather than a hand-counted literal
        # so the field count (11, matching config-keys.psv's own grammar)
        # can't silently drift from a miscounted `|`.
        truncated_row = "|".join(
            ["commit_stall_block", "bool", "false", "", "false", "", "", "", "", "", ""]
        )
        schema_path.write_text(truncated_row + "\n")
        result = _run_with_schema(
            isolated_hooks_dir,
            "_config_value commit_stall_block; "
            "printf 'status1=%d|' \"$?\"; "
            f'cp "{_CONFIG_KEYS_PSV}" "{schema_path}"; '
            "_config_value commit_stall_block; "
            "printf '|status2=%d' \"$?\"",
        )
        assert result.stdout == "status1=4|true|status2=0", (
            f"exit 4 must not be memoized, so the fixed-schema call resolves "
            f"normally: stdout={result.stdout!r}"
        )

    def test_claude_config_dir_reassignment_mid_process_invalidates_the_memo(self, isolated_home):
        """Proves the _CONFIG_MEMO_ENV_DIR fingerprint invalidation: a
        CLAUDE_CONFIG_DIR reassignment between two bare _config_value calls
        for the same key must not replay the first dir's memoized value."""
        dir_a = isolated_home / "config-dir-a"
        dir_a.mkdir()
        dir_b = isolated_home / "config-dir-b"
        dir_b.mkdir()
        _write_state_file(isolated_home, "handoff_nudge = false\n", config_dir=dir_a)
        _write_state_file(isolated_home, "handoff_nudge = true\n", config_dir=dir_b)
        result = _run(
            f'export CLAUDE_CONFIG_DIR="{dir_a}"; '
            "_config_value handoff_nudge; "
            "printf '|'; "
            f'export CLAUDE_CONFIG_DIR="{dir_b}"; '
            "_config_value handoff_nudge"
        )
        assert result.stdout == "false|true", (
            "the second bare call must reflect dir_b's own state, not "
            f"dir_a's memoized value: stdout={result.stdout!r}"
        )

    def test_home_reassignment_mid_process_invalidates_the_memo(self, isolated_home, tmp_path):
        """Mirrors the CLAUDE_CONFIG_DIR test above for the $HOME fallback
        path: a HOME reassignment between two bare _config_value calls for
        the same key must not replay the first home's memoized value."""
        home_a = tmp_path / "home-a"
        home_b = tmp_path / "home-b"
        _write_state_file(home_a, "handoff_nudge = false\n")
        _write_state_file(home_b, "handoff_nudge = true\n")
        result = _run(
            f'export HOME="{home_a}"; '
            "_config_value handoff_nudge; "
            "printf '|'; "
            f'export HOME="{home_b}"; '
            "_config_value handoff_nudge"
        )
        assert result.stdout == "false|true", (
            "the second bare call must reflect home_b's own state, not "
            f"home_a's memoized value: stdout={result.stdout!r}"
        )

    def test_claude_config_dir_unset_mid_process_falls_back_to_home_and_invalidates_the_memo(
        self, isolated_home, tmp_path
    ):
        """Covers the one reassignment shape the two tests above don't:
        CLAUDE_CONFIG_DIR set then unset mid-process, falling back to $HOME.
        Must not replay the first call's CLAUDE_CONFIG_DIR-anchored memoized
        value."""
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        _write_state_file(tmp_path, "handoff_nudge = false\n", config_dir=config_dir)
        _write_state_file(isolated_home, "handoff_nudge = true\n")
        result = _run(
            f'export CLAUDE_CONFIG_DIR="{config_dir}"; '
            "_config_value handoff_nudge; "
            "printf '|'; "
            "unset CLAUDE_CONFIG_DIR; "
            "_config_value handoff_nudge"
        )
        assert result.stdout == "false|true", (
            "the second bare call must reflect $HOME's own state, not the "
            f"memoized CLAUDE_CONFIG_DIR-anchored value: stdout={result.stdout!r}"
        )

    def test_memo_fingerprint_independent_comparison_defeats_pipe_delimiter_aliasing(self, isolated_home):
        """Pins the property _config_memo_lookup's own header comment names as
        the reason CLAUDE_CONFIG_DIR/HOME are compared independently rather
        than concatenated: a literal '|' in one value must not let two
        distinct (dir, home) pairs alias to the same fingerprint. dir_a/home_a
        and dir_b/home_b concatenate to the identical "X|Y|Z" string despite
        dir_a != dir_b and home_a != home_b -- a naive concatenated
        fingerprint would treat the second call as a cache hit and replay
        dir_a's stale value instead of dir_b's real one."""
        dir_a = isolated_home / "X|Y"
        dir_a.mkdir()
        dir_b = isolated_home / "X"
        dir_b.mkdir()
        # home_a/home_b are plain strings, not paths under isolated_home --
        # handoff_nudge is a resolution=config-dir key, so $HOME is never
        # read for its value resolution, only for the memo fingerprint.
        # Nesting them under isolated_home too would prepend an identical
        # "isolated_home/" prefix to both dir and home before the pipe,
        # which breaks the collision this test needs to construct.
        home_a = "Z"
        home_b = "Y|Z"
        _write_state_file(isolated_home, "handoff_nudge = false\n", config_dir=dir_a)
        _write_state_file(isolated_home, "handoff_nudge = true\n", config_dir=dir_b)
        result = _run(
            f'export CLAUDE_CONFIG_DIR="{dir_a}"; export HOME="{home_a}"; '
            "_config_value handoff_nudge; "
            "printf '|'; "
            f'export CLAUDE_CONFIG_DIR="{dir_b}"; export HOME="{home_b}"; '
            "_config_value handoff_nudge"
        )
        assert result.stdout == "false|true", (
            "a naive concatenated fingerprint would alias these two distinct "
            "pairs and replay dir_a's memoized value; the independent "
            f"comparison must treat this as a miss: stdout={result.stdout!r}"
        )


# ---------------------------------------------------------------------------
# The union-active branch (_config_value's config-dir-or-home path with
# CLAUDE_CONFIG_DIR diverged from $HOME) holds most of this file's
# _config_memo_store call sites, including the OR-across-both-locations
# comparison -- the costliest lookup shape in the repo. TestMemoization's
# own isolated_home fixture always collapses primary_dir to $HOME, so none
# of those tests exercise this branch; this class does.
# ---------------------------------------------------------------------------


class TestMemoizationUnionBranch:
    def test_union_branch_memoizes_and_override_call_does_not_pollute_it(
        self, isolated_home, monkeypatch
    ):
        """Drives worktree_required (a config-dir-or-home key) through the
        union branch: a first bare call resolves and memoizes "true", a
        CONFIG_DIR_OVERRIDE call to a third, unrelated directory resolves
        its own "false" independently, a second no-override call still
        returns the memoized "true", and a _config_set write flips the
        primary location to "false" and resets the memo, so a final call
        re-resolves the union branch to "false" -- covering both the
        allow and deny direction through this branch, not just one.

        This proves only the read side, since a CONFIG_DIR_OVERRIDE call
        never enters this union branch (union_active requires an empty
        override). See
        TestMemoizationSingleLocationBranch::test_override_call_before_a_real_memoized_lookup_does_not_pollute_it
        for the write-side proof."""
        config_dir = isolated_home / "altconfig"
        config_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        _write_state_file(isolated_home, "worktree_required = true\n", config_dir=config_dir)

        override_dir = isolated_home / "override-config"
        override_dir.mkdir()
        _write_state_file(isolated_home, "worktree_required = false\n", config_dir=override_dir)

        result = _run(
            "_config_value worktree_required; "
            "printf '|'; "
            f'_config_value worktree_required "{override_dir}"; '
            "printf '|'; "
            "_config_value worktree_required; "
            "printf '|'; "
            "_config_set worktree_required false; "
            "_config_value worktree_required"
        )
        assert result.stdout == "true|false|true|false", (
            "expected: first union-branch call resolves and memoizes "
            "'true'; the override call resolves its own directory's "
            "'false' independently; the third call still returns the "
            "memoized 'true'; and _config_set's write resets the memo so "
            f"the union branch re-resolves to 'false': stdout={result.stdout!r}"
        )

    def test_internal_failure_fail_safe_is_not_memoized(self, isolated_home, monkeypatch):
        """The union fold's internal-failure fail-safe path (both
        _config_location_value calls fail) must not be memoized -- a
        same-process retry after the forced failure is lifted re-resolves
        fresh instead of replaying the stale fail-safe value.

        Renames the real _config_location_value to
        _config_location_value_real via `declare -f`+`sed`, so the forced
        failure can be lifted for the second call alone.
        TestUnionBranchLocationValueFailure's override stub above instead
        stays in effect for its whole (single-call) subprocess.
        worktree_required's fail-safe value is "true" (legacy_probe is
        "true"). This is genuinely different from the real state-file-backed
        value "false" this test drives it to -- the discriminating property
        a broken _CONFIG_RESOLVED_MEMOIZABLE gate would fail. A broken gate
        would replay the first call's fail-safe "true" for the second call
        instead of re-resolving "false"."""
        config_dir = isolated_home / "altconfig"
        config_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        _write_state_file(isolated_home, "worktree_required = false\n", config_dir=config_dir)

        result = _run(
            "eval \"$(declare -f _config_location_value | "
            "sed '1s/.*/_config_location_value_real ()/')\"; "
            "_config_location_value() { return 1; }; "
            "_config_value worktree_required; "
            "printf '|'; "
            "_config_enabled worktree_required; "
            "printf 'enabled1=%d|' \"$?\"; "
            '_config_location_value() { _config_location_value_real "$@"; }; '
            "_config_value worktree_required; "
            "printf '|'; "
            "_config_enabled worktree_required; "
            "printf 'enabled2=%d' \"$?\""
        )
        assert result.stdout == "true|enabled1=0|false|enabled2=1", (
            "the first call's union-fold fail-safe 'true' must not be "
            "memoized, so the second call re-resolves fresh against the "
            "real state file's 'false', and _config_enabled -- the real "
            "enforcement call site's own contract, not just _config_value's "
            "printed string -- must agree at both the fail-safe point and "
            f"after the state change: stdout={result.stdout!r}"
        )

    def test_internal_failure_fail_safe_is_not_memoized_autonomous_shipping(
        self, isolated_home, monkeypatch
    ):
        """autonomous_shipping counterpart to the worktree_required test
        above. legacy_probe is "false" for this key, so its fail-safe value
        is its own schema default, "false".
        This is the opposite polarity from worktree_required's. It drives
        the same _CONFIG_RESOLVED_MEMOIZABLE reset through the union fold's
        other branch.

        Catches a reset gated on only the legacy_probe="true" arm, which the
        sibling worktree_required test above can't -- it never exercises
        the "false"-default arm.

        The real state-file-backed value "true" this test drives it to is
        genuinely different from the fail-safe "false", the same
        discriminating property the worktree_required test above relies on."""
        config_dir = isolated_home / "altconfig"
        config_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        _write_state_file(isolated_home, "autonomous_shipping = true\n", config_dir=config_dir)

        result = _run(
            "eval \"$(declare -f _config_location_value | "
            "sed '1s/.*/_config_location_value_real ()/')\"; "
            "_config_location_value() { return 1; }; "
            "_config_value autonomous_shipping; "
            "printf '|'; "
            "_config_enabled autonomous_shipping; "
            "printf 'enabled1=%d|' \"$?\"; "
            '_config_location_value() { _config_location_value_real "$@"; }; '
            "_config_value autonomous_shipping; "
            "printf '|'; "
            "_config_enabled autonomous_shipping; "
            "printf 'enabled2=%d' \"$?\""
        )
        assert result.stdout == "false|enabled1=1|true|enabled2=0", (
            "the first call's union-fold fail-safe 'false' must not be "
            "memoized, so the second call re-resolves fresh against the "
            "real state file's 'true', and _config_enabled -- the real "
            "enforcement call site's own contract, not just _config_value's "
            "printed string -- must agree at both the fail-safe point and "
            f"after the state change: stdout={result.stdout!r}"
        )

    def test_union_branch_fail_safe_when_only_primary_location_fails(
        self, isolated_home, monkeypatch
    ):
        """Other union-branch failure tests fail both locations uniformly and
        can't distinguish `||` from an accidentally-flipped `&&`. This stubs
        only the primary location's failure (keyed on
        `_config_location_value`'s own `$2` dir argument, delegating to the
        real implementation for the home branch) and asserts the fail-safe
        value wins over home's real value."""
        config_dir = isolated_home / "altconfig"
        config_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        _write_state_file(isolated_home, "autonomous_shipping = true\n")

        result = _run(
            "eval \"$(declare -f _config_location_value | "
            "sed '1s/.*/_config_location_value_real ()/')\"; "
            f'_config_location_value() {{ [ "$2" = "{config_dir}" ] && return 1; '
            '_config_location_value_real "$@"; }; '
            "_config_value autonomous_shipping; "
            "printf '|'; "
            "_config_enabled autonomous_shipping; "
            "printf 'enabled=%d' \"$?\""
        )
        assert result.stdout == "false|enabled=1", (
            "a primary-location-only failure must still take the union's "
            "fail-safe path ('false', autonomous_shipping's own schema "
            "default) rather than falling through to home's real 'true': "
            f"stdout={result.stdout!r}"
        )


class TestMemoizationSingleLocationBranch:
    def test_override_call_before_a_real_memoized_lookup_does_not_pollute_it(
        self, isolated_home, monkeypatch
    ):
        """The single-location branch is _config.sh's non-union path.
        Every CONFIG_DIR_OVERRIDE call reaches it, since union_active
        requires an empty override.
        It gates its own _config_memo_store call on override-emptiness.
        The override call below must run first.
        Memo lookup is leftmost-match, so writing the real entry first
        would mask a later polluting write instead of exposing it."""
        config_dir = isolated_home / "altconfig"
        config_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        _write_state_file(isolated_home, "commit_stall_block = true\n", config_dir=config_dir)

        override_dir = isolated_home / "override-config"
        override_dir.mkdir()
        _write_state_file(isolated_home, "commit_stall_block = false\n", config_dir=override_dir)

        result = _run(
            f'_config_value commit_stall_block "{override_dir}"; '
            "printf '|'; "
            "_config_value commit_stall_block"
        )
        assert result.stdout == "false|true", (
            "the override call's own 'false' must not leak into the "
            "shared no-override memo entry: a broken guard would make the "
            f"second call also read 'false': stdout={result.stdout!r}"
        )

    def test_internal_failure_is_not_memoized(self, isolated_home):
        """The single-location branch's own _config_location_value failure
        must not be memoized -- a same-process retry after the forced
        failure is lifted re-resolves fresh instead of replaying the first
        call's fail-safe value.

        commit_stall_block's own schema default ("true") is already its
        fail-closed direction, so an internal failure resolves to "true"
        (see _config_resolve_fail_safe). The state file below drives the
        real, post-recovery value to "false" instead, genuinely different
        from the fail-safe -- the discriminating property a broken
        _CONFIG_RESOLVED_MEMOIZABLE gate would fail.

        Same declare-f-plus-sed rename technique as
        TestMemoizationUnionBranch's own fail-safe test, so the forced
        failure can be lifted for the second call alone.
        Also checks _config_enabled's own exit code at both points, not
        just _config_value's printed string -- _config_enabled is the real
        enforcement call site in _lib.sh."""
        _write_state_file(isolated_home, "commit_stall_block = false\n")

        result = _run(
            "eval \"$(declare -f _config_location_value | "
            "sed '1s/.*/_config_location_value_real ()/')\"; "
            "_config_location_value() { return 1; }; "
            "_config_value commit_stall_block; "
            "printf '|'; "
            "_config_enabled commit_stall_block; "
            "printf 'enabled1=%d|' \"$?\"; "
            '_config_location_value() { _config_location_value_real "$@"; }; '
            "_config_value commit_stall_block; "
            "printf '|'; "
            "_config_enabled commit_stall_block; "
            "printf 'enabled2=%d' \"$?\""
        )
        assert result.stdout == "true|enabled1=0|false|enabled2=1", (
            "the first call's fail-safe 'true' must not be memoized, so "
            "the second call re-resolves fresh against the real state "
            "file's 'false', and _config_enabled must agree at both the "
            f"fail-safe point and after the state change: stdout={result.stdout!r}"
        )

    @pytest.mark.parametrize(
        "key, expected_value, expected_enabled_returncode",
        [
            # autonomous_shipping is the one enforcement-critical key whose
            # safe direction is deny, not armed -- its schema default
            # ("false") is already its own fail-closed direction.
            # A raw-passthrough regression (an empty value read as
            # "enabled") would flip its exit code from 1 to 0 here.
            ("autonomous_shipping", "false", 1),
            # round_consult_gate, authorization_boundary_restore, and
            # commit_stall_block are structurally identical (config-dir,
            # presence-disables, default "true") -- see this repo's own
            # "audit structural siblings" convention.
            ("round_consult_gate", "true", 0),
            ("authorization_boundary_restore", "true", 0),
            ("commit_stall_block", "true", 0),
            # worktree_required is the one enforcement-critical key whose
            # own default ("false") isn't already fail-closed, so its
            # fail-safe comes from legacy_probe's "true" override instead
            # (see _config_resolve_fail_safe).
            ("worktree_required", "true", 0),
        ],
    )
    def test_internal_failure_resolves_value_and_exit_code_for_enforcement_critical_keys(
        self, isolated_home, key, expected_value, expected_enabled_returncode
    ):
        """Asserts both the resolved value and the _config_enabled exit code
        on a forced _config_location_value failure through the single-
        location branch. test_internal_failure_is_not_memoized above already
        covers commit_stall_block's escape from memoization. This test adds
        the exit-code check across all five enforcement-critical keys. A
        raw-passthrough regression would read as enabled (exit 0) regardless
        of key. That's the wrong direction for autonomous_shipping.
        worktree_required and autonomous_shipping are both
        resolution=config-dir-or-home keys. They land on this branch's own
        code path only because isolated_home leaves CLAUDE_CONFIG_DIR
        unset, so union_active never activates for them here."""
        override = "_config_location_value() { return 1; }; "

        value_result = _run(f"{override}_config_value {key}")
        assert value_result.stdout == expected_value, (
            f"an internal _config_location_value failure for {key!r} must "
            f"fail safe to {expected_value!r}, not an empty passthrough: "
            f"stdout={value_result.stdout!r}"
        )
        assert value_result.returncode == 0

        enabled_result = _run(f"{override}_config_enabled {key}")
        assert enabled_result.returncode == expected_enabled_returncode, (
            f"an empty passthrough would always read as enabled (exit 0) "
            f"under _config_enabled's any-value-but-false rule -- {key!r} "
            f"must resolve to exit {expected_enabled_returncode}"
        )

    def test_config_enabled_and_bare_config_value_share_one_memo_entry(self, isolated_home):
        """_config_enabled and _config_value both call _config_lookup, the
        single memo gate that reads and writes _CONFIG_MEMO_CACHE. A
        same-process call to one must see, and not duplicate, the other's
        stored value. _config_set between the two calls proves the shared
        entry is invalidated for both, not just the function that wrote
        it."""
        result = _run(
            "_config_enabled commit_stall_block; "
            "printf 'first=%d|' \"$?\"; "
            "_config_value commit_stall_block; "
            "printf '|'; "
            "_config_set commit_stall_block false; "
            "_config_enabled commit_stall_block; "
            "printf 'second=%d' \"$?\""
        )
        assert result.stdout == "first=0|true|second=1", (
            f"a bare _config_value call must read _config_enabled's own "
            f"stored entry, and _config_set must invalidate it for both "
            f"functions: stdout={result.stdout!r}"
        )


class TestMemoizationHomeOnlyFallbackBranch:
    def test_internal_failure_is_not_memoized(self, isolated_home, monkeypatch):
        """The home-only fallback branch is reached only by worktree_required,
        when primary_dir is unresolvable and its legacy-probe-on-resolution-
        failure column is "true".
        It must not memoize its own _config_location_value failure -- a
        same-process retry after the forced failure is lifted re-resolves
        fresh instead of replaying the first call's fail-safe value.

        This branch's own guard requires legacy_probe = "true" to be
        reached, so its fail-safe value is always "true" (see
        _config_resolve_fail_safe).
        That is genuinely different from the real, state-file-backed value
        "false" this test drives it to -- the discriminating property a
        broken _CONFIG_RESOLVED_MEMOIZABLE gate would fail.

        A relative CLAUDE_CONFIG_DIR makes _lib_config_dir fail, landing in
        this branch. Same technique TestLegacyProbeOnResolutionFailure
        uses. Same declare-f-plus-sed rename technique as the sibling
        fail-safe tests above.
        Also checks _config_enabled's own exit code at both points, not
        just _config_value's printed string -- _config_enabled is the real
        enforcement call site in _lib.sh."""
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "relative/not-absolute")
        _write_state_file(isolated_home, "worktree_required = false\n")

        result = _run(
            "eval \"$(declare -f _config_location_value | "
            "sed '1s/.*/_config_location_value_real ()/')\"; "
            "_config_location_value() { return 1; }; "
            "_config_value worktree_required; "
            "printf '|'; "
            "_config_enabled worktree_required; "
            "printf 'enabled1=%d|' \"$?\"; "
            '_config_location_value() { _config_location_value_real "$@"; }; '
            "_config_value worktree_required; "
            "printf '|'; "
            "_config_enabled worktree_required; "
            "printf 'enabled2=%d' \"$?\""
        )
        assert result.stdout == "true|enabled1=0|false|enabled2=1", (
            "the first call's fail-safe 'true' must not be memoized, so "
            "the second call re-resolves fresh against the real state "
            "file's 'false', and _config_enabled must agree at both the "
            f"fail-safe point and after the state change: stdout={result.stdout!r}"
        )

    def test_success_is_memoized(self, isolated_home, monkeypatch):
        """The home-only fallback branch's success path is reached when
        primary_dir is unresolvable and _config_location_value then
        succeeds against $HOME/.claude. That success path must also
        memoize. The failure path above is
        already proven unmemoized. This test proves the positive
        direction, the perf property this branch exists to speed up.

        Same direct-filesystem-rewrite-mid-process technique as
        TestMemoization::test_legacy_file_created_mid_process_does_not_affect_memoized_value.
        A same-process rewrite of the state file between two bare calls
        must not change the second call's result, since a real re-read
        (not a cache hit) would pick up the new value instead."""
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "relative/not-absolute")
        _write_state_file(isolated_home, "worktree_required = true\n")
        state_file = isolated_home / ".claude" / "claude-config.toml"

        result = _run(
            "_config_value worktree_required; "
            "printf '|'; "
            f'printf "worktree_required = false\\n" > "{state_file}"; '
            "_config_value worktree_required"
        )
        assert result.stdout == "true|true", (
            "the second bare call must still return the value memoized by "
            "the first home-only-branch resolution, not a fresh read of "
            f"the state file rewritten in between: stdout={result.stdout!r}"
        )


# ---------------------------------------------------------------------------
# An unrecognized FIELD passed to _config_schema_field must return 1 only
# when KEY's own row was found, and 4 (propagated from _config_schema_row)
# when it wasn't.
# ---------------------------------------------------------------------------


class TestSchemaFieldBogusFieldOrdering:
    def test_bogus_field_on_an_existing_row_returns_1(self, isolated_home):
        result = _run("_config_schema_field commit_stall_block not_a_real_field")
        assert result.returncode == 1
        assert result.stdout == ""

    def test_bogus_field_on_a_missing_row_returns_4(self, tmp_path):
        isolated_hooks_dir = _isolated_hooks_dir_missing_key_row(tmp_path, "commit_stall_block")
        result = _run_with_schema(
            isolated_hooks_dir, "_config_schema_field commit_stall_block not_a_real_field"
        )
        assert result.returncode == 4
        assert result.stdout == ""


# ---------------------------------------------------------------------------
# _config_schema_row's own per-key correctness, for every real
# config-keys.psv key -- pins the consolidation's core correctness claim
# directly, rather than trusting the incidental breadth of the existing
# _config_value/_config_enabled behavioral suite (which wouldn't reliably
# catch a single shifted column on a key whose test coverage happens to
# route around that field).
#
# This oracle can't be _config_schema_field, since it delegates to
# _config_schema_row, so both would report the same wrong value on a
# column-swap bug.
# _config.py's already-tested schema() is reused here rather than
# hand-rolling a second PSV parser, per this repo's single-source-of-truth
# rule for parsing logic.
# TestConfigKeysPsvGrammarInvariants below covers a different gap both
# parsers share: row-shape/field-count leniency on a malformed row.
# ---------------------------------------------------------------------------

sys.path.insert(0, str(SCRIPTS_DIR))
from _config import schema  # noqa: E402

_SCHEMA_ROWS_BY_KEY = schema()

_SCHEMA_ROW_GLOBALS = [
    "_CONFIG_ROW_TYPE",
    "_CONFIG_ROW_DEFAULT",
    "_CONFIG_ROW_RESOLUTION",
    "_CONFIG_ROW_LEGACY_PROBE",
    "_CONFIG_ROW_LEGACY_IMPORT",
    "_CONFIG_ROW_LEGACY_FILENAME",
    "_CONFIG_ROW_LEGACY_POLARITY",
    "_CONFIG_ROW_HUMAN_NAME",
    "_CONFIG_ROW_DOCS_ANCHOR",
    "_CONFIG_ROW_PROMPT_DESCRIPTION",
]

# Same order as _SCHEMA_ROW_GLOBALS, mapped to SchemaRow's own field names.
# legacy_probe_on_resolution_failure_raw is the raw column string (not the
# coerced bool), matching _CONFIG_ROW_LEGACY_PROBE's own raw string value.
_SCHEMA_ROW_PY_FIELDS = [
    "type",
    "default",
    "resolution",
    "legacy_probe_on_resolution_failure_raw",
    "legacy_import_locations",
    "legacy_filename",
    "legacy_polarity",
    "human_name",
    "docs_anchor",
    "prompt_description",
]


class TestSchemaRowMatchesIndependentPsvParse:
    @pytest.mark.parametrize("key", list(_SCHEMA_ROWS_BY_KEY))
    def test_schema_row_globals_match_independent_psv_parse(self, key):
        row_globals_expr = " ".join(f'"${g}"' for g in _SCHEMA_ROW_GLOBALS)
        script = (
            f"_config_schema_row {key}; "
            f'printf "%s\\n" {row_globals_expr} "$_CONFIG_ROW_KNOWN_KEYS"'
        )
        result = _run(script)
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        lines = result.stdout.splitlines()
        assert len(lines) == 11, f"expected 11 lines, got {len(lines)}: {lines!r}"
        row_values, known_keys = lines[:10], lines[10]
        expected_row = _SCHEMA_ROWS_BY_KEY[key]
        expected_values = [getattr(expected_row, field) for field in _SCHEMA_ROW_PY_FIELDS]
        assert row_values == expected_values, (
            f"_config_schema_row's own globals for {key!r} must match an "
            f"independent Python parse of its config-keys.psv row: "
            f"bash={row_values!r} python={expected_values!r}"
        )
        expected_known_keys = " " + " ".join(_SCHEMA_ROWS_BY_KEY) + " "
        assert known_keys == expected_known_keys, (
            f"_CONFIG_ROW_KNOWN_KEYS must list every schema key: "
            f"got={known_keys!r} expected={expected_known_keys!r}"
        )


class TestConfigKeysPsvGrammarInvariants:
    """Reads config-keys.psv directly, through neither _config_schema_row
    (bash) nor _config.py's schema() -- both apply the same padding/
    truncation leniency to a malformed row, so a row-shape bug is invisible
    to TestSchemaRowMatchesIndependentPsvParse's comparison above."""

    def test_every_row_has_exactly_eleven_fields(self):
        offending_lines = [
            (lineno, line)
            for lineno, line in enumerate(_CONFIG_KEYS_PSV.read_text().splitlines(), start=1)
            if line.strip() and not line.strip().startswith("#") and line.count("|") != 10
        ]
        assert offending_lines == [], (
            "a config-keys.psv row does not have exactly 11 pipe-separated "
            f"fields: {offending_lines!r}"
        )

    def test_grammar_comment_column_order_matches_schema_row_globals(self):
        """Three-way check: the PSV file's own grammar comment, this
        hand-typed `expected_fields`, and `_SCHEMA_ROW_PY_FIELDS`'s order
        must all agree. `expected_fields` is hand-typed, not derived from
        `_SCHEMA_ROW_PY_FIELDS`, so a lockstep reorder of both real lists
        doesn't pass silently."""
        grammar_line = next(
            line for line in _CONFIG_KEYS_PSV.read_text().splitlines() if line.startswith("#   key|")
        )
        fields = grammar_line.lstrip("#").strip().split("|")[1:]  # drop the leading `key` field
        expected_fields = [
            "type",
            "default",
            "resolution",
            "legacy-probe-on-resolution-failure",
            "legacy-import-locations",
            "legacy-filename",
            "legacy-polarity",
            "human-name",
            "docs-anchor",
            "prompt-description",
        ]
        assert fields == expected_fields, (
            f"config-keys.psv's own grammar comment line's column order "
            f"({fields!r}) no longer matches this test's independently "
            f"hand-typed expected order ({expected_fields!r})"
        )
        # Normalizes _SCHEMA_ROW_PY_FIELDS's order to the grammar comment's
        # hyphenated spelling (dropping the one "_raw" suffix) and checks it
        # against expected_fields too.
        # This is the leg that ties _SCHEMA_ROW_PY_FIELDS/_SCHEMA_ROW_GLOBALS
        # to the independent oracle -- without it, a lockstep reorder of both
        # real lists would go undetected.
        normalized_py_fields = [
            field.removesuffix("_raw").replace("_", "-") for field in _SCHEMA_ROW_PY_FIELDS
        ]
        assert normalized_py_fields == expected_fields, (
            f"_SCHEMA_ROW_PY_FIELDS's own order ({_SCHEMA_ROW_PY_FIELDS!r}, "
            f"normalized to {normalized_py_fields!r}) no longer matches "
            f"expected_fields ({expected_fields!r}) -- update whichever "
            f"one drifted so all three stay in agreement"
        )


class TestSingleLocationBranchNeverExercisesLegacyProbeArm:
    def test_no_config_dir_only_row_opts_into_legacy_probe_on_resolution_failure(self):
        """_config_resolve's single-location branch (resolution=config-dir)
        threads legacy_probe into the shared _config_resolve_fail_safe
        helper the same way the union and home-only branches do.
        No real config-dir-only key exercises that helper's
        legacy_probe="true" arm today -- only worktree_required
        (resolution=config-dir-or-home) does, via the other two branches.
        This pins that assumption directly
        against config-keys.psv via the independent parser oracle above, so
        a future config-dir row that opts into legacy-probe-on-resolution-
        failure=true flags the gap in the single-location branch's own test
        coverage instead of silently inheriting it."""
        offending_rows = [
            row.key
            for row in _SCHEMA_ROWS_BY_KEY.values()
            if row.resolution == "config-dir" and row.legacy_probe_on_resolution_failure_raw == "true"
        ]
        assert offending_rows == [], (
            "a resolution=config-dir row now has legacy-probe-on-resolution-"
            "failure=true -- add a TestMemoizationSingleLocationBranch case "
            "driving that key's own legacy_probe=true fail-safe arm before "
            f"landing this schema change: {offending_rows!r}"
        )

    def test_synthetic_config_dir_row_with_legacy_probe_true_overrides_its_own_default(
        self, tmp_path
    ):
        """The tripwire above only inspects config-keys.psv's current rows.
        It can't discriminate a call-site regression in _config_resolve's
        single-location branch (_config.sh's
        `_config_resolve_fail_safe "$legacy_probe" "$default"` call) from
        correct behavior, since no real config-dir-only key exercises that
        call's legacy_probe="true" arm today.

        Exercises it directly instead, via a synthetic schema row.
        Overrides permission_prompt_tracking's own
        legacy-probe-on-resolution-failure column to "true". Its real
        default stays "false" -- the opposite direction. Forces a
        _config_location_value failure to reach the fail-safe path.

        A call-site argument swap or a hardcoded fail-safe would resolve
        "false" here instead of "true", since permission_prompt_tracking's
        own default never overrides to "true" on its own."""
        isolated_hooks_dir = _isolated_hooks_dir_with_legacy_probe_override(
            tmp_path, "permission_prompt_tracking", "true"
        )
        override = "_config_location_value() { return 1; }; "
        value_result = _run_with_schema(
            isolated_hooks_dir, f"{override}_config_value permission_prompt_tracking"
        )
        assert value_result.stdout == "true", (
            "legacy_probe=true must override permission_prompt_tracking's "
            "own default ('false') on an internal resolution failure: "
            f"stdout={value_result.stdout!r}"
        )
        enabled_result = _run_with_schema(
            isolated_hooks_dir, f"{override}_config_enabled permission_prompt_tracking"
        )
        assert enabled_result.returncode == 0, (
            "_config_enabled must agree with _config_value's fail-safe "
            f"value: returncode={enabled_result.returncode}"
        )


class TestConfigDirOrHomeRowsAreAllBoolTyped:
    def test_no_config_dir_or_home_row_has_a_non_bool_type(self):
        """_config_resolve's union branch (_config.sh:824-827) compares
        each location's resolved value against the literal "true" to decide
        the OR, which only works for a bool-typed key. Pins that assumption
        against config-keys.psv via the independent parser oracle above, so
        a future non-bool config-dir-or-home row flags here instead of
        silently miscomputing the union."""
        offending_rows = [
            row.key
            for row in _SCHEMA_ROWS_BY_KEY.values()
            if row.resolution == "config-dir-or-home" and row.type != "bool"
        ]
        assert offending_rows == [], (
            "a resolution=config-dir-or-home row is not bool-typed -- "
            "_config_resolve's union branch's 'true'-literal comparison "
            "does not generalize to an enum value; update that branch "
            f"before landing this schema change: {offending_rows!r}"
        )


# ---------------------------------------------------------------------------
# No function anywhere in this file's scanned scope may declare a
# `local`/`declare` sharing one of these global names, since bash's dynamic
# (not lexical) scoping would silently intercept the plain global assignment
# into that shadow. The scan splits into two, since the two groups don't
# share the same risk shape:
#
# _CONFIG_ROW_*/_CONFIG_RESOLVED_*/_CONFIG_LOOKUP_* are read immediately
# after being written within _config.sh's own functions, so scanning
# _config.sh alone is sufficient for that group.
#
# _CONFIG_MEMO_* persists across separate top-level calls in any caller, so
# every hook/script sourcing this file needs scanning too, since a shadow
# there can desync it from a legitimate _config_set-triggered reset.
#
# Both scans are fail-fast checks, not a completeness guarantee.
# ---------------------------------------------------------------------------


def _join_backslash_continuations(text: str) -> list[tuple[int, str]]:
    """Joins a backslash-line-continued statement into one logical line, so
    a `local`/`declare` declaration split across physical lines (or a
    compound one-liner like `[ cond ] && local x=1`) is scanned as the
    single bash simple-command it actually is. Each returned tuple's line
    number is the first physical line of its logical line."""
    logical_lines: list[tuple[int, str]] = []
    buffer = ""
    start_lineno: int | None = None
    for lineno, line in enumerate(text.splitlines(), start=1):
        if start_lineno is None:
            start_lineno = lineno
        if line.endswith("\\"):
            buffer += line[:-1] + " "
            continue
        buffer += line
        logical_lines.append((start_lineno, buffer))
        buffer = ""
        start_lineno = None
    if buffer:
        logical_lines.append((start_lineno, buffer))
    return logical_lines


# Not anchored to line start -- a shadowing declaration can follow `;`,
# `&&`, `||`, `then`, or `do` on the same physical or joined logical line
# (e.g. `[ -n "$x" ] && local _CONFIG_ROW_TYPE=1`), an idiom already used
# elsewhere in this file. `declare` is function-scoped by default too.
_LOCAL_OR_DECLARE_RE = re.compile(r"\b(?:local|declare)\b")
# A real declaration target is a bare token, optionally followed by
# `=value`. A value-position usage is always written `$_CONFIG_ROW_*`/
# `${_CONFIG_ROW_*}` in this file. Excluding a token immediately preceded by
# `$` or `${` distinguishes the two. Split into two regexes matching the two
# scans' own prefix groups (see the class-level comment above): ROW/
# RESOLVED/LOOKUP for the narrow, _config.sh-only scan, MEMO for the wide,
# repo-scan.
_ROW_SHADOWING_TOKEN_RE = re.compile(r"(?<!\$)(?<!\$\{)_CONFIG_(?:ROW|RESOLVED|LOOKUP)_[A-Z_]+")
_MEMO_SHADOWING_TOKEN_RE = re.compile(r"(?<!\$)(?<!\$\{)_CONFIG_MEMO_[A-Z_]+")


class TestGlobalReturnShadowingInvariant:
    def test_no_local_declares_a_config_row_resolved_or_lookup_global(self):
        # Scoped to _config.sh alone -- a `local`/`declare` shadow declared
        # by a caller elsewhere (_lib.sh, install.sh, a hook, a script)
        # doesn't break the global-return contract for this same-call-chain
        # group; only a shadow inside _config.sh's own writer functions does
        # (see the class-level comment above).
        scanned_files = [_CONFIG_SH]
        violations = [
            (path.name, lineno, logical_line)
            for path in scanned_files
            for lineno, logical_line in _join_backslash_continuations(path.read_text())
            if _LOCAL_OR_DECLARE_RE.search(logical_line) and _ROW_SHADOWING_TOKEN_RE.search(logical_line)
        ]
        assert not violations, (
            "a `local`/`declare` declaration shadows a _CONFIG_ROW_*/"
            f"_CONFIG_RESOLVED_*/_CONFIG_LOOKUP_* global, which bash's "
            f"dynamic scoping would silently redirect that global's "
            f"assignment into: {violations!r}"
        )

    def test_no_local_declares_a_memo_global_anywhere_it_could_run(self):
        # Wide scan -- _CONFIG_MEMO_CACHE/_CONFIG_MEMO_ENV_DIR/
        # _CONFIG_MEMO_ENV_HOME persist across separate top-level calls, so a
        # caller-declared shadow anywhere in this process's own call
        # surface (not only inside _config.sh) can desync the real global
        # from a legitimate reset (see the class-level comment above).
        scanned_files = sorted(
            {_CONFIG_SH, _LIB_SH, _INSTALL_SH, *HOOKS_DIR.glob("**/*.sh"), *SCRIPTS_DIR.glob("**/*.sh")}
        )
        violations = [
            (path.name, lineno, logical_line)
            for path in scanned_files
            for lineno, logical_line in _join_backslash_continuations(path.read_text())
            if _LOCAL_OR_DECLARE_RE.search(logical_line) and _MEMO_SHADOWING_TOKEN_RE.search(logical_line)
        ]
        assert not violations, (
            "a `local`/`declare` declaration shadows a _CONFIG_MEMO_* "
            f"global, which bash's dynamic scoping would silently redirect "
            f"that global's assignment into, desyncing it from a real "
            f"_config_set-triggered reset: {violations!r}"
        )

    @pytest.mark.parametrize(
        "shadowing_line,token_re",
        [
            ('[ -n "$x" ] && local _CONFIG_ROW_TYPE="shadow"', _ROW_SHADOWING_TOKEN_RE),
            ("for k in a b; do local _CONFIG_ROW_TYPE=\"$k\"; done", _ROW_SHADOWING_TOKEN_RE),
            ('if true; then local _CONFIG_MEMO_CACHE=1; fi', _MEMO_SHADOWING_TOKEN_RE),
            ('if true; then local _CONFIG_MEMO_ENV_DIR=1; fi', _MEMO_SHADOWING_TOKEN_RE),
        ],
    )
    def test_regex_catches_compound_statement_shadowing(self, shadowing_line, token_re):
        """A shadowing `local` need not be the first token on its line --
        e.g. a compound one-liner like `[ cond ] && local x=1`, an idiom
        already used elsewhere in this file. Covers both split regexes, so
        each is proven to still fire on its own prefix group."""
        assert _LOCAL_OR_DECLARE_RE.search(shadowing_line)
        assert token_re.search(shadowing_line)

    def test_regex_catches_backslash_continued_shadowing(self):
        text = 'local \\\n  _CONFIG_ROW_TYPE="shadow"\n'
        joined = _join_backslash_continuations(text)
        assert any(
            _LOCAL_OR_DECLARE_RE.search(logical_line) and _ROW_SHADOWING_TOKEN_RE.search(logical_line)
            for _, logical_line in joined
        )

    def test_row_regex_does_not_match_memo_prefixed_names(self):
        """The narrow-scan regex must not widen onto _CONFIG_MEMO_* names --
        those are covered by the separate wide scan instead."""
        assert not _ROW_SHADOWING_TOKEN_RE.search("local _CONFIG_MEMO_CACHE=1")
        assert not _ROW_SHADOWING_TOKEN_RE.search("local _CONFIG_MEMO_ENV_DIR=1")

    def test_memo_regex_does_not_match_row_resolved_or_lookup_prefixed_names(self):
        """The wide-scan regex must not narrow onto
        _CONFIG_ROW_*/_CONFIG_RESOLVED_*/_CONFIG_LOOKUP_* names -- those are
        covered by the separate narrow scan instead."""
        assert not _MEMO_SHADOWING_TOKEN_RE.search("local _CONFIG_ROW_TYPE=1")
        assert not _MEMO_SHADOWING_TOKEN_RE.search("local _CONFIG_RESOLVED_VALUE=1")
        assert not _MEMO_SHADOWING_TOKEN_RE.search("local _CONFIG_LOOKUP_VALUE=1")
