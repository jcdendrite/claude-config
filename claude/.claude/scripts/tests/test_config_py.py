"""Tests for _config.py's config_value()/config_enabled()/schema() -- the
Python-runtime counterpart to claude/.claude/hooks/_config.sh, plus a
config-get.sh-specific test pinning its four-way exit-code contract and
confirming it has no writing verb.

test_config_parser_parity.py is the differential suite proving _config.py
and _config.sh agree byte-for-byte across an adversarial fixture corpus;
this file covers _config.py's own contract in isolation.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import SCRIPTS_DIR

sys.path.insert(0, str(Path(__file__).parent.parent))
from _config import config_enabled, config_value, schema  # noqa: E402

_CONFIG_GET_SH = SCRIPTS_DIR / "config-get.sh"


def _make_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return home


# ---------------------------------------------------------------------------
# schema()
# ---------------------------------------------------------------------------


class TestSchema:
    def test_returns_all_fourteen_keys(self):
        # 14 is config-keys.psv's own current row count -- update this
        # literal deliberately whenever a key is added or removed there.
        assert len(schema()) == 14

    def test_worktree_required_row_matches_known_columns(self):
        row = schema()["worktree_required"]
        assert row.type == "bool"
        assert row.default == "false"
        assert row.resolution == "config-dir-or-home"
        assert row.legacy_probe_on_resolution_failure is True
        assert row.legacy_filename == "worktree-required"
        assert row.legacy_polarity == "presence-enables"

    def test_pr_cost_disclosure_row_is_enum_typed(self):
        row = schema()["pr_cost_disclosure"]
        assert row.type == "enum:dollars"
        assert row.legacy_polarity == "content-matches"


# ---------------------------------------------------------------------------
# config_value() / config_enabled()
# ---------------------------------------------------------------------------


class TestConfigValue:
    def test_default_resolution_bool_key_defaulting_false(self, tmp_path, monkeypatch):
        _make_home(tmp_path, monkeypatch)
        assert config_value("worktree_required") == "false"
        assert config_enabled("worktree_required") is False

    def test_default_resolution_bool_key_defaulting_true(self, tmp_path, monkeypatch):
        _make_home(tmp_path, monkeypatch)
        assert config_value("commit_stall_block") == "true"
        assert config_enabled("commit_stall_block") is True

    def test_unresolvable_config_dir_returns_none(self, tmp_path, monkeypatch):
        _make_home(tmp_path, monkeypatch)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "relative/not-absolute")
        assert config_value("round_consult_gate") is None
        assert config_enabled("round_consult_gate") is None

    def test_unknown_key_raises_key_error(self, tmp_path, monkeypatch):
        _make_home(tmp_path, monkeypatch)
        with pytest.raises(KeyError):
            config_value("not_a_real_key")

    def test_config_dir_override_skips_environment_resolution(self, tmp_path, monkeypatch):
        _make_home(tmp_path, monkeypatch)
        account_dir = tmp_path / "account-2"
        account_dir.mkdir()
        (account_dir / "claude-config.toml").write_text("pr_cost_recording = true\n")
        assert config_value("pr_cost_recording", config_dir_override=account_dir) == "true"
        # The environment's own (unset) config dir must be unaffected.
        assert config_value("pr_cost_recording") == "false"

    def test_config_dir_override_skips_the_config_dir_or_home_union(self, tmp_path, monkeypatch):
        """An override caller (e.g. transcript-analysis.py's --all-accounts
        loop) has already opted out of the config-dir-or-home union -- a
        legacy file at $HOME/.claude must not leak into an overridden
        account's own resolution."""
        home = _make_home(tmp_path, monkeypatch)
        (home / ".claude" / "worktree-required").touch()
        account_dir = tmp_path / "account-2"
        account_dir.mkdir()
        assert config_value("worktree_required", config_dir_override=account_dir) == "false"

    def test_state_file_row_wins_over_disagreeing_legacy_file(self, tmp_path, monkeypatch):
        home = _make_home(tmp_path, monkeypatch)
        (home / ".claude" / "claude-config.toml").write_text("pr_cost_disclosure = dollars\n")
        (home / ".claude" / "pr-cost-disclosure").write_text("notdollars\n")
        assert config_value("pr_cost_disclosure") == "dollars"

    def test_content_matches_crlf_authored_file_still_resolves(self, tmp_path, monkeypatch):
        home = _make_home(tmp_path, monkeypatch)
        (home / ".claude" / "pr-cost-disclosure").write_bytes(b"dollars\r\n")
        assert config_value("pr_cost_disclosure") == "dollars"

    @pytest.mark.parametrize("content", ["DOLLARS", "Dollars", "DoLLaRs\n"])
    def test_content_matches_case_folded(self, tmp_path, monkeypatch, content):
        home = _make_home(tmp_path, monkeypatch)
        (home / ".claude" / "pr-cost-disclosure").write_text(content)
        assert config_value("pr_cost_disclosure") == "dollars"

    def test_content_matches_whitespace_only_resolves_default(self, tmp_path, monkeypatch):
        home = _make_home(tmp_path, monkeypatch)
        (home / ".claude" / "pr-cost-disclosure").write_text(" \n")
        assert config_value("pr_cost_disclosure") == "false"

    def test_content_matches_second_line_of_junk_resolves_default(self, tmp_path, monkeypatch):
        """Fail-open shape: reading only the first line would treat
        "dollars\\nallowance" as a match -- the whole (trimmed) content must
        equal the expected literal, not just its first line."""
        home = _make_home(tmp_path, monkeypatch)
        (home / ".claude" / "pr-cost-disclosure").write_text("dollars\nallowance\n")
        assert config_value("pr_cost_disclosure") == "false"

    @pytest.mark.parametrize("content", ["dollars123", "xdollars"])
    def test_content_matches_glued_extra_characters_resolves_default(self, tmp_path, monkeypatch, content):
        """Fail-open shape: an unanchored substring compare would match
        extra characters glued onto either end of the expected literal --
        the compare must be an anchored equality test."""
        home = _make_home(tmp_path, monkeypatch)
        (home / ".claude" / "pr-cost-disclosure").write_text(content)
        assert config_value("pr_cost_disclosure") == "false"

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root bypasses discretionary file-permission bits (CAP_DAC_OVERRIDE on Linux), "
        "so chmod(0o000) does not make the file unreadable and this would resolve enabled instead of default",
    )
    def test_content_matches_unreadable_legacy_file_resolves_default(self, tmp_path, monkeypatch):
        """Proves the guarded read (OSError -> "") degrades to the schema
        default rather than raising or resolving as enabled."""
        home = _make_home(tmp_path, monkeypatch)
        sentinel_path = home / ".claude" / "pr-cost-disclosure"
        sentinel_path.write_text("dollars\n")
        sentinel_path.chmod(0o000)
        try:
            value = config_value("pr_cost_disclosure")
        finally:
            sentinel_path.chmod(0o644)
        assert value == "false"

    def test_home_only_legacy_file_does_not_activate_a_diverged_config_dir(self, tmp_path, monkeypatch):
        """pr_cost_disclosure's `resolution` column is `config-dir`, not
        `config-dir-or-home` -- unlike worktree_required/autonomous_shipping,
        a legacy file present only at $HOME/.claude must never activate it
        once CLAUDE_CONFIG_DIR diverges from $HOME/.claude."""
        home = _make_home(tmp_path, monkeypatch)
        (home / ".claude" / "pr-cost-disclosure").write_text("dollars\n")
        config_dir = tmp_path / "altconfig"
        config_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        assert config_value("pr_cost_disclosure") == "false"

    def test_malformed_key_shape_is_skipped_like_a_malformed_value(self, tmp_path, monkeypatch, capsys):
        """A line with a valid `=` but a key that fails the
        `[A-Za-z0-9_-]+` grammar (a space, here) is malformed -- distinct
        from a malformed value -- and must be skipped the same way, leaving
        the remaining valid key resolvable. Mirrors _config.sh's own
        test_malformed_key_shape_is_skipped_like_a_malformed_value."""
        home = _make_home(tmp_path, monkeypatch)
        (home / ".claude" / "claude-config.toml").write_text("bad key = true\nhandoff_nudge = false\n")
        assert config_value("handoff_nudge") == "false"
        err = capsys.readouterr().err
        assert "malformed line" in err
        assert "bad key = true" in err


# ---------------------------------------------------------------------------
# A line whose key is grammatically valid but has no row at all in
# config-keys.psv (a case- or spelling-typo'd key) is a distinct case from a
# malformed line -- it parses cleanly and would otherwise sit silently
# ignored forever. Mirrors _config.sh's own TestUnrecognizedKeyWarning.
# ---------------------------------------------------------------------------


class TestUnrecognizedKeyWarning:
    def test_unrecognized_key_line_is_skipped_with_its_own_warning(self, tmp_path, monkeypatch, capsys):
        home = _make_home(tmp_path, monkeypatch)
        (home / ".claude" / "claude-config.toml").write_text("handoff_nudge = false\nWorktree_Required = true\n")
        assert config_value("handoff_nudge") == "false"
        err = capsys.readouterr().err
        assert "unrecognized key" in err
        assert "Worktree_Required = true" in err
        assert "malformed line" not in err

    def test_unrecognized_key_does_not_shadow_a_similarly_named_real_key(self, tmp_path, monkeypatch, capsys):
        home = _make_home(tmp_path, monkeypatch)
        (home / ".claude" / "claude-config.toml").write_text("pr_cost_disclosur = dollars\n")
        assert config_value("pr_cost_disclosure") == "false"
        err = capsys.readouterr().err
        assert "unrecognized key" in err
        assert "pr_cost_disclosur = dollars" in err


# ---------------------------------------------------------------------------
# A grammar-conforming but schema-invalid `bool` value must warn and fall
# through to the legacy-file-then-default chain, not resolve as authoritative
# -- otherwise a typo (or a value a hand-editor would believe disables it,
# e.g. "off") on autonomous_shipping would silently grant it, since
# config_enabled treats any value other than the literal "false" as enabled.
# ---------------------------------------------------------------------------


class TestSchemaTypeValidationOnRead:
    def test_autonomous_shipping_non_boolean_value_warns_and_falls_through(self, tmp_path, monkeypatch, capsys):
        home = _make_home(tmp_path, monkeypatch)
        (home / ".claude" / "claude-config.toml").write_text("autonomous_shipping = notabool\n")
        assert config_value("autonomous_shipping") == "false"
        assert config_enabled("autonomous_shipping") is False
        err = capsys.readouterr().err
        assert "malformed line" in err
        assert "autonomous_shipping = notabool" in err

    def test_worktree_required_non_boolean_value_warns_and_falls_through(self, tmp_path, monkeypatch, capsys):
        home = _make_home(tmp_path, monkeypatch)
        (home / ".claude" / "claude-config.toml").write_text("worktree_required = notabool\n")
        assert config_value("worktree_required") == "false"
        assert config_enabled("worktree_required") is False
        err = capsys.readouterr().err
        assert "malformed line" in err
        assert "worktree_required = notabool" in err


# ---------------------------------------------------------------------------
# config-get.sh: four-way exit-code contract, no writing verb
# ---------------------------------------------------------------------------


def _run_config_get(args: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(_CONFIG_GET_SH), *args],
        capture_output=True,
        text=True,
        env=env,
    )


class TestConfigGetShExitCodes:
    def test_enabled_key_exits_0_and_prints_value(self, tmp_path, monkeypatch):
        home = _make_home(tmp_path, monkeypatch)
        (home / ".claude" / "worktree-required").touch()
        result = _run_config_get(["worktree_required"], env={"HOME": str(home)})
        assert result.returncode == 0
        assert result.stdout == "true\n"

    def test_disabled_key_exits_1(self, tmp_path, monkeypatch):
        home = _make_home(tmp_path, monkeypatch)
        result = _run_config_get(["worktree_required"], env={"HOME": str(home)})
        assert result.returncode == 1
        assert result.stdout == "false\n"

    def test_unknown_key_exits_2(self, tmp_path, monkeypatch):
        home = _make_home(tmp_path, monkeypatch)
        result = _run_config_get(["not_a_real_key"], env={"HOME": str(home)})
        assert result.returncode == 2

    def test_unresolvable_config_dir_exits_3(self, tmp_path, monkeypatch):
        # round_consult_gate carries legacy-probe-on-resolution-failure:
        # false (row 18), unlike worktree_required -- it has no raw
        # $HOME/.claude fallback to fall through to on a resolution
        # failure, so it genuinely reaches "unresolvable" here.
        home = _make_home(tmp_path, monkeypatch)
        result = _run_config_get(
            ["round_consult_gate"], env={"HOME": str(home), "CLAUDE_CONFIG_DIR": "relative/not-absolute"},
        )
        assert result.returncode == 3

    def test_unknown_key_takes_precedence_over_unresolvable_config_dir(self, tmp_path, monkeypatch):
        """Row 26: unknown-key detection always runs first, independent of
        config-dir resolvability -- both failure modes are present here, and
        the unknown-key exit code (2) must win, not the unresolvable one (3)."""
        home = _make_home(tmp_path, monkeypatch)
        result = _run_config_get(
            ["not_a_real_key"], env={"HOME": str(home), "CLAUDE_CONFIG_DIR": "relative/not-absolute"},
        )
        assert result.returncode == 2

    def test_no_set_subcommand(self, tmp_path, monkeypatch):
        home = _make_home(tmp_path, monkeypatch)
        result = _run_config_get(["set"], env={"HOME": str(home)})
        assert result.returncode != 0
        assert "no set subcommand" in result.stderr.lower() or "no writing verb" in result.stderr.lower()

    def test_wrong_argument_count_is_rejected(self, tmp_path, monkeypatch):
        home = _make_home(tmp_path, monkeypatch)
        assert _run_config_get([], env={"HOME": str(home)}).returncode != 0
        assert _run_config_get(["a", "b"], env={"HOME": str(home)}).returncode != 0

    def test_is_executable(self):
        """test_skills.py's test_scripts_are_executable already globs
        SCRIPTS_DIR for the executable bit; this pins the same expectation
        locally so a regression here fails inside this file's own domain."""
        assert os.access(_CONFIG_GET_SH, os.X_OK)
