"""Tests for _config.sh's config-key reader/writer primitives.

Covers _config_enabled/_config_value's resolution semantics (schema
default, legacy-file fallback per legacy-polarity, the config-dir-or-home
union, and row-19's state-file-row-is-authoritative precedence over a
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
import shlex
import subprocess
from pathlib import Path

from helpers import HOOKS_DIR

_LIB_SH = HOOKS_DIR / "_lib.sh"


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
# Row 13: _config_enabled's three-way exit code contract
# ---------------------------------------------------------------------------


class TestExitCodeContract:
    def test_unresolvable_config_dir_returns_exit_2(self, isolated_home, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "relative/not-absolute")
        result = _run("_config_enabled round_consult_gate")
        assert result.returncode == 2
        assert result.stdout == ""


# ---------------------------------------------------------------------------
# CONFIG_DIR_OVERRIDE positional argument to _config_value/_config_enabled --
# no bash-side coverage previously existed for this parameter at all.
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
        all -- bash's `[ -n "$config_dir_override" ]` test already does
        this, unlike Python's previous `override is not None` gate (see
        test_config_parser_parity.py's differential coverage of this same
        parameter)."""
        _write_state_file(isolated_home, "handoff_nudge = false\n")
        result = _run('_config_value handoff_nudge ""')
        assert result.stdout == "false"
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# Row 18: legacy-probe-on-resolution-failure divergence between
# worktree_required (true) and autonomous_shipping (false)
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
# Row 4/33: config-dir-or-home union semantics
# ---------------------------------------------------------------------------


class TestUnionSemantics:
    def test_explicit_false_in_config_dir_does_not_defeat_true_under_home_legacy(self, isolated_home, monkeypatch):
        """An explicit `false` state-file row at the resolved config dir must
        not defeat a `true` produced by $HOME/.claude's legacy file -- the
        union is OR'd across each location's own independently-resolved
        effective value, not "first location found wins" (row 4)."""
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
# Row 19: state-file-row precedence over a disagreeing legacy file, for a
# content-matches key (pr_cost_disclosure) -- not just a boolean presence
# check, since this migration's own motivating bug lives in that key.
# ---------------------------------------------------------------------------


class TestLegacyPrecedence:
    def test_state_file_value_wins_over_disagreeing_legacy_file(self, isolated_home):
        _write_state_file(isolated_home, "pr_cost_disclosure = dollars\n")
        (isolated_home / ".claude" / "pr-cost-disclosure").write_text("notdollars\n")

        result = _run("_config_value pr_cost_disclosure")
        assert result.stdout == "dollars"

    def test_legacy_file_only_consulted_when_key_entirely_absent_from_state_file(self, isolated_home):
        _write_state_file(isolated_home, "handoff_nudge = true\n")
        (isolated_home / ".claude" / ".commit-stall-block-disabled").touch()

        result = _run("_config_value commit_stall_block")
        assert result.stdout == "false"


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
        """The exact bug this migration exists to close: a CRLF-authored
        one-line sentinel must resolve identically to an LF one -- [:space:]
        trimming (not [:blank:]) strips the trailing CR."""
        (isolated_home / ".claude" / "pr-cost-disclosure").write_bytes(b"dollars\r\n")
        assert _run("_config_value pr_cost_disclosure").stdout == "dollars"


# ---------------------------------------------------------------------------
# Row 7: a subset-violating line is skipped (with a warning), not treated as
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
# A grammar-conforming but schema-invalid `bool` value must warn and fall
# through to the legacy-file-then-default chain, not resolve as authoritative
# -- otherwise a typo (or a value a hand-editor would believe disables it,
# e.g. "off") on autonomous_shipping would silently grant it, since
# _config_enabled treats any value other than the literal "false" as enabled.
# ---------------------------------------------------------------------------


class TestSchemaTypeValidationOnRead:
    def test_autonomous_shipping_non_boolean_value_warns_and_falls_through(self, isolated_home):
        _write_state_file(isolated_home, "autonomous_shipping = notabool\n")
        result = _run("_config_value autonomous_shipping")
        assert result.stdout == "false"
        assert result.returncode == 0
        assert "malformed line" in result.stderr
        assert "autonomous_shipping = notabool" in result.stderr
        assert _run("_config_enabled autonomous_shipping").returncode == 1

    def test_worktree_required_non_boolean_value_warns_and_falls_through(self, isolated_home):
        _write_state_file(isolated_home, "worktree_required = notabool\n")
        result = _run("_config_value worktree_required")
        assert result.stdout == "false"
        assert result.returncode == 0
        assert "malformed line" in result.stderr
        assert "worktree_required = notabool" in result.stderr
        assert _run("_config_enabled worktree_required").returncode == 1


# ---------------------------------------------------------------------------
# Row 9: _config_set's atomic, comment/order-preserving rewrite, and its
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
        result = _run("_config_set pr_cost_disclosure dollars")
        assert result.returncode == 0
        state_file = isolated_home / ".claude" / "claude-config.toml"
        assert state_file.read_text() == "pr_cost_disclosure = dollars\n"

    def test_enum_key_rejects_a_literal_other_than_its_own_or_false(self, isolated_home):
        """pr_cost_disclosure is `enum:dollars`-typed -- a write value other
        than `false` or `dollars` must be rejected before any write is
        attempted."""
        result = _run("_config_set pr_cost_disclosure euros")
        assert result.returncode != 0
        state_file = isolated_home / ".claude" / "claude-config.toml"
        assert not state_file.exists()


# ---------------------------------------------------------------------------
# Row 32: _config_scaffold is additive-only, and honors an exclude-list.
# ---------------------------------------------------------------------------


class TestConfigScaffold:
    def test_existing_hand_edited_row_survives_unchanged_while_missing_keys_fill_in(self, isolated_home):
        _write_state_file(isolated_home, "handoff_nudge = false\n")
        result = _run("_config_scaffold")
        assert result.returncode == 0

        state_file = isolated_home / ".claude" / "claude-config.toml"
        lines = state_file.read_text().splitlines()
        assert "handoff_nudge = false" in lines
        # Every other schema key must now have a row too (additive-only).
        assert "worktree_required = false" in lines
        assert "commit_stall_block = true" in lines
        assert len(lines) == 14

    def test_exclude_list_key_stays_absent_while_every_other_key_gets_its_default(self, isolated_home):
        result = _run("_config_scaffold 'worktree_required autonomous_shipping'")
        assert result.returncode == 0

        state_file = isolated_home / ".claude" / "claude-config.toml"
        lines = state_file.read_text().splitlines()
        assert not any(line.startswith("worktree_required") for line in lines)
        assert not any(line.startswith("autonomous_shipping") for line in lines)
        assert "commit_stall_block = true" in lines
        assert "handoff_nudge = true" in lines
        assert len(lines) == 12


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
