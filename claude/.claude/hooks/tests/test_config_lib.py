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
import shlex
import subprocess
from pathlib import Path

import pytest
from helpers import HOOKS_DIR

_LIB_SH = HOOKS_DIR / "_lib.sh"
_CONFIG_SH = HOOKS_DIR / "_config.sh"


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


def _run_with_schema(hooks_dir: Path, script: str) -> subprocess.CompletedProcess:
    """Source an isolated _config.sh symlink from `hooks_dir` (which carries
    its own config-keys.psv), bypassing _lib.sh entirely -- _config_scaffold
    needs only _config.sh's own functions, matching
    test_config_parser_parity.py's TestMissingSchemaFile isolation
    technique. Used for schema shapes (e.g. a key with no legacy-polarity
    value) that none of today's real 14 keys carry."""
    return subprocess.run(
        ["bash", "-c", f'set -uo pipefail; . "{hooks_dir / "_config.sh"}"; {script}'],
        capture_output=True,
        text=True,
    )


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
# _config_enabled's three-way exit code contract
# ---------------------------------------------------------------------------


class TestExitCodeContract:
    def test_unresolvable_config_dir_returns_exit_2(self, isolated_home, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "relative/not-absolute")
        result = _run("_config_enabled round_consult_gate")
        assert result.returncode == 2
        assert result.stdout == ""


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
        """A CRLF-authored one-line sentinel must resolve identically to an
        LF one -- [:space:] trimming (not [:blank:]) strips the trailing
        CR."""
        (isolated_home / ".claude" / "pr-cost-disclosure").write_bytes(b"dollars\r\n")
        assert _run("_config_value pr_cost_disclosure").stdout == "dollars"

    @pytest.mark.parametrize("content", ["DOLLARS", "Dollars", "DoLLaRs\n"])
    def test_content_matches_case_folded(self, isolated_home, content):
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
        _write_state_file(isolated_home, "pr_cost_disclosur = dollars\n")
        result = _run("_config_value pr_cost_disclosure")
        assert result.stdout == "false"
        assert "unrecognized key" in result.stderr
        assert "pr_cost_disclosur = dollars" in result.stderr


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


# ---------------------------------------------------------------------------
# _config_value's union branch must check _config_location_value's own exit
# status before treating its captured stdout as authoritative -- an
# arity-guard trip on either call (unreachable today, since both union-branch
# calls always pass exactly 6 args) must not let the resulting empty stdout
# fall through to _config_enabled's any-value-but-false "enabled" reading.
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

        The _config_value assertion below is what actually discriminates
        this fix: pre-fix, the union branch's empty-stdout fallthrough also
        happens to print a value that resolves as "enabled" for this key, so
        the trailing _config_enabled assertion documents the contract but
        does not by itself catch a regression here."""
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
        assert state_file.read_text().splitlines() == ["handoff_nudge = false"]

    def test_presence_and_content_matches_polarity_keys_stay_absent(self, isolated_home):
        """Every one of today's 14 keys carries a legacy-polarity value, so
        scaffold over an empty state file must leave the file with no rows
        at all -- backfilling any of them would permanently shadow that
        key's own legacy file with zero warning."""
        result = _run("_config_scaffold")
        assert result.returncode == 0

        state_file = isolated_home / ".claude" / "claude-config.toml"
        assert not state_file.exists() or state_file.read_text() == ""

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
        assert not state_file.exists() or state_file.read_text() == ""

    def test_plain_key_with_no_legacy_polarity_still_gets_its_default_row(self, isolated_home, tmp_path):
        """A key with an empty legacy-polarity column has no legacy file to
        protect, so scaffold's original additive-only default-fill contract
        still applies to it -- a schema shape none of today's real 14 keys
        carry, exercised via an isolated config-keys.psv fixture."""
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
