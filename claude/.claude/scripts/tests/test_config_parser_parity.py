"""Differential test: _config.sh (bash) and _config.py (Python) must return
byte-identical verdicts for every key, over a shared adversarial fixture
corpus. A shared schema data file (config-keys.psv) makes the *schema*
divergence structurally impossible, but bash and Python are still two
independently maintained parsers of the same claude-config.toml grammar,
and this suite is what catches them silently disagreeing.

CONFIG_KEYS_PSV and CONFIG_SH below are declared as module-level path
constants (not inline strings) so TestCrossDomainReadCompleteness
(test_select_tests.py) can see this file's dependency on them and enforce
select-tests.py's own CONFIG_KEYS_PSV cross-domain-exception row.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from helpers import HOOKS_DIR

sys.path.insert(0, str(Path(__file__).parent.parent))
from _config import schema  # noqa: E402

CONFIG_SH = HOOKS_DIR / "_config.sh"
CONFIG_KEYS_PSV = HOOKS_DIR / "config-keys.psv"

_ALL_KEYS = sorted(schema().keys())


def _bash_values() -> dict[str, str | None]:
    """Runs _config_value for every schema key in one bash subprocess,
    inheriting the current (test-monkeypatched) environment."""
    lines = [f'. "{CONFIG_SH}"']
    for key in _ALL_KEYS:
        lines.append(
            f'v=$(_config_value {key}); s=$?; '
            f'if [ "$s" -eq 2 ]; then printf "%s\\tUNRESOLVED\\n" "{key}"; '
            f'else printf "%s\\t%s\\n" "{key}" "$v"; fi'
        )
    result = subprocess.run(["bash", "-c", "; ".join(lines)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    values: dict[str, str | None] = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition("\t")
        values[key] = None if value == "UNRESOLVED" else value
    return values


def _python_values() -> dict[str, str | None]:
    # Re-import fresh each call: _config.py's schema()/config_value() re-read
    # from disk every time (no caching), matching _config.sh's own behavior,
    # so no explicit reload is needed here either.
    from _config import config_value

    return {key: config_value(key) for key in _ALL_KEYS}


def _assert_parity() -> dict[str, str | None]:
    bash_values = _bash_values()
    python_values = _python_values()
    assert bash_values == python_values, (
        f"bash/python parser parity failure:\nbash:   {bash_values}\npython: {python_values}"
    )
    return bash_values


def _write_state(home: Path, content: bytes) -> None:
    state_file = home / ".claude" / "claude-config.toml"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_bytes(content)


def _make_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return home


# ---------------------------------------------------------------------------
# Adversarial state-file content fixtures -- each writes claude-config.toml,
# then asserts every one of the 14 keys resolves identically in both readers.
# ---------------------------------------------------------------------------

ADVERSARIAL_FIXTURES: dict[str, bytes] = {
    "crlf-line-endings": b"handoff_nudge = false\r\ncommit_stall_block = true\r\n",
    "trailing-whitespace": b"handoff_nudge = false   \ncommit_stall_block = true\t\n",
    "trailing-nbsp-on-value": "handoff_nudge = false \n".encode(),
    "trailing-ideographic-space-on-value": "handoff_nudge = false　\n".encode(),
    "trailing-form-feed-on-value": b"handoff_nudge = false\x0c\n",
    "trailing-vertical-tab-on-value": b"handoff_nudge = false\x0b\n",
    "leading-utf8-bom": b"\xef\xbb\xbfhandoff_nudge = false\n",
    "mixed-crlf-and-lf": b"handoff_nudge = false\r\ncommit_stall_block = true\n",
    "hash-inside-quoted-value": b'handoff_nudge = "true#123" \n',
    "duplicate-keys-last-wins": b"handoff_nudge = false\nhandoff_nudge = true\n",
    "uppercase-boolean-literal": b"handoff_nudge = TRUE\ncommit_stall_block = True\n",
    "toml-table-header": b"[section]\nhandoff_nudge = false\n",
    "toml-array-value": b"handoff_nudge = [true, false]\n",
    "toml-multiline-string-value": b'handoff_nudge = """\nmulti\nline\n"""\n',
    "empty-file": b"",
    "comments-and-blank-lines-only": b"# just a comment\n\n   \n",
}


class TestAdversarialFixtureParity:
    @pytest.mark.parametrize("label", sorted(ADVERSARIAL_FIXTURES))
    def test_fixture_parity(self, label, tmp_path, monkeypatch):
        home = _make_home(tmp_path, monkeypatch)
        _write_state(home, ADVERSARIAL_FIXTURES[label])
        _assert_parity()


class TestSpecificFixtureExpectations:
    """A handful of the fixtures above have one obviously-correct expected
    value worth pinning directly, beyond "both readers agree with each
    other" -- agreeing on a wrong answer would still pass the parity-only
    check above."""

    def test_form_feed_is_stripped_not_rejected(self, tmp_path, monkeypatch):
        """Unlike NBSP/ideographic space, form-feed IS part of both readers'
        whitespace-trim set (POSIX [:space:] / _ASCII_WHITESPACE) -- so the
        value parses as valid, trimmed "false", not as malformed."""
        home = _make_home(tmp_path, monkeypatch)
        _write_state(home, b"handoff_nudge = false\x0c\n")
        values = _assert_parity()
        assert values["handoff_nudge"] == "false"

    def test_vertical_tab_is_stripped_not_rejected(self, tmp_path, monkeypatch):
        """Unlike NBSP/ideographic space, vertical-tab IS part of both
        readers' whitespace-trim set (POSIX [:space:] / _ASCII_WHITESPACE) --
        so the value parses as valid, trimmed "false", not as malformed."""
        home = _make_home(tmp_path, monkeypatch)
        _write_state(home, b"handoff_nudge = false\x0b\n")
        values = _assert_parity()
        assert values["handoff_nudge"] == "false"

    def test_empty_file_resolves_to_schema_default(self, tmp_path, monkeypatch):
        """An empty state file has no row for any key, so every key falls
        through to its config-keys.psv default -- handoff_nudge defaults to
        "true"."""
        home = _make_home(tmp_path, monkeypatch)
        _write_state(home, b"")
        values = _assert_parity()
        assert values["handoff_nudge"] == "true"

    def test_comments_and_blank_lines_only_resolves_to_schema_default(self, tmp_path, monkeypatch):
        """A state file with no conforming key=value row at all -- only
        comments and blank lines -- resolves the same as an empty file: the
        config-keys.psv default."""
        home = _make_home(tmp_path, monkeypatch)
        _write_state(home, b"# just a comment\n\n   \n")
        values = _assert_parity()
        assert values["handoff_nudge"] == "true"

    def test_nbsp_on_value_is_rejected_not_silently_trimmed(self, tmp_path, monkeypatch):
        home = _make_home(tmp_path, monkeypatch)
        _write_state(home, "handoff_nudge = false \n".encode())
        values = _assert_parity()
        # Malformed -- falls through to the schema default (true), not "false".
        assert values["handoff_nudge"] == "true"

    def test_duplicate_key_last_occurrence_wins(self, tmp_path, monkeypatch):
        home = _make_home(tmp_path, monkeypatch)
        _write_state(home, b"handoff_nudge = false\nhandoff_nudge = true\n")
        values = _assert_parity()
        assert values["handoff_nudge"] == "true"

    def test_uppercase_boolean_literal_is_case_folded(self, tmp_path, monkeypatch):
        home = _make_home(tmp_path, monkeypatch)
        _write_state(home, b"handoff_nudge = TRUE\n")
        values = _assert_parity()
        assert values["handoff_nudge"] == "true"

    def test_leading_bom_does_not_corrupt_the_first_key(self, tmp_path, monkeypatch):
        home = _make_home(tmp_path, monkeypatch)
        _write_state(home, b"\xef\xbb\xbfhandoff_nudge = false\n")
        values = _assert_parity()
        assert values["handoff_nudge"] == "false"

    def test_toml_table_header_line_is_skipped_but_sibling_key_still_parses(self, tmp_path, monkeypatch):
        home = _make_home(tmp_path, monkeypatch)
        _write_state(home, b"[section]\nhandoff_nudge = false\n")
        values = _assert_parity()
        assert values["handoff_nudge"] == "false"


# ---------------------------------------------------------------------------
# The partial-file fixture -- N valid lines plus exactly one
# subset-violating line, asserting the valid keys still resolve correctly in
# BOTH readers (not just one -- test_config_lib.py's own partial-file test
# covers bash alone and the warning-emission side; this is the cross-reader
# agreement side).
# ---------------------------------------------------------------------------


class TestPartialFileFixtureParity:
    def test_valid_keys_resolve_identically_in_both_readers_despite_one_bad_line(self, tmp_path, monkeypatch):
        home = _make_home(tmp_path, monkeypatch)
        _write_state(
            home,
            b"# a comment\n"
            b"handoff_nudge = false\n"
            b"this line has no equals sign\n"
            b"commit_stall_block = true\n",
        )
        values = _assert_parity()
        assert values["handoff_nudge"] == "false"
        assert values["commit_stall_block"] == "true"


# ---------------------------------------------------------------------------
# Legacy-file fixtures -- state file absent entirely (or, for the union
# test, present at a different location), only a legacy sentinel file
# present, run through the same byte-identical _assert_parity() check as the
# state-file fixtures above. Union/legacy-probe/legacy-polarity logic is
# independently implemented in each reader (_config.sh's
# _config_location_value/_config_value, _config.py's _location_value/
# config_value); test_config_lib.py (bash) and test_config_py.py (python)
# each separately pin their own reader's expected value against a fixture
# shape like this, but neither runs both readers against ONE shared fixture
# the way this class does.
# ---------------------------------------------------------------------------

_SCHEMA = schema()


def _write_legacy_file(directory: Path, key: str, content: str | None = None) -> None:
    """Writes KEY's own config-keys.psv-declared legacy file at DIRECTORY --
    content=None touches an empty file (presence-enables/presence-disables
    polarity, where only presence matters); a content-matches key
    (pr_cost_disclosure) passes its intended raw content instead."""
    directory.mkdir(parents=True, exist_ok=True)
    legacy_path = directory / _SCHEMA[key].legacy_filename
    if content is None:
        legacy_path.touch()
    else:
        legacy_path.write_text(content)


class TestLegacyFileFixtureParity:
    def test_presence_enables_legacy_file_present(self, tmp_path, monkeypatch):
        home = _make_home(tmp_path, monkeypatch)
        _write_legacy_file(home / ".claude", "worktree_required")
        values = _assert_parity()
        assert values["worktree_required"] == "true"

    def test_presence_enables_legacy_file_absent(self, tmp_path, monkeypatch):
        _make_home(tmp_path, monkeypatch)
        values = _assert_parity()
        assert values["worktree_required"] == "false"

    def test_presence_disables_legacy_file_present(self, tmp_path, monkeypatch):
        home = _make_home(tmp_path, monkeypatch)
        _write_legacy_file(home / ".claude", "handoff_nudge")
        values = _assert_parity()
        assert values["handoff_nudge"] == "false"

    def test_presence_disables_legacy_file_absent(self, tmp_path, monkeypatch):
        _make_home(tmp_path, monkeypatch)
        values = _assert_parity()
        assert values["handoff_nudge"] == "true"

    @pytest.mark.parametrize(
        "content,expected",
        [
            ("dollars\n", "dollars"),
            ("DOLLARS\n", "dollars"),
            ("garbled-not-dollars\n", "false"),
        ],
        ids=["valid", "case-folded", "invalid"],
    )
    def test_content_matches_legacy_file(self, tmp_path, monkeypatch, content, expected):
        home = _make_home(tmp_path, monkeypatch)
        _write_legacy_file(home / ".claude", "pr_cost_disclosure", content=content)
        values = _assert_parity()
        assert values["pr_cost_disclosure"] == expected

    def test_config_dir_or_home_union_disagreeing_locations(self, tmp_path, monkeypatch):
        """A config-dir-or-home key's legacy file present ONLY at
        $HOME/.claude, with an explicit `false` row at the (different)
        resolved config dir's own state file, must still resolve true in
        both readers -- the union is OR'd across both locations'
        independently-resolved effective value, not first-location-wins."""
        home = _make_home(tmp_path, monkeypatch)
        config_dir = tmp_path / "altconfig"
        config_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        (config_dir / "claude-config.toml").write_text("worktree_required = false\n")
        _write_legacy_file(home / ".claude", "worktree_required")
        values = _assert_parity()
        assert values["worktree_required"] == "true"

    def test_legacy_probe_on_resolution_failure(self, tmp_path, monkeypatch):
        """worktree_required is the sole key whose schema row carries
        legacy-probe-on-resolution-failure: true -- with an unresolvable
        primary config dir (a relative CLAUDE_CONFIG_DIR) and its own legacy
        file present at the literal $HOME/.claude, both readers must still
        resolve true rather than propagating the resolution failure."""
        home = _make_home(tmp_path, monkeypatch)
        _write_legacy_file(home / ".claude", "worktree_required")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "relative/not-absolute")
        values = _assert_parity()
        assert values["worktree_required"] == "true"


# ---------------------------------------------------------------------------
# CLAUDE_CONFIG_DIR variants: unset, relative, absolute -- and an empty $HOME.
# ---------------------------------------------------------------------------


class TestConfigDirEnvironmentVariants:
    def test_unset_claude_config_dir(self, tmp_path, monkeypatch):
        _make_home(tmp_path, monkeypatch)
        _assert_parity()

    def test_relative_claude_config_dir(self, tmp_path, monkeypatch):
        _make_home(tmp_path, monkeypatch)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "relative/not-absolute")
        values = _assert_parity()
        # round_consult_gate has no legacy-probe-on-resolution-failure, so a
        # relative CLAUDE_CONFIG_DIR must resolve to unresolvable (None) in
        # both readers.
        assert values["round_consult_gate"] is None

    def test_absolute_claude_config_dir(self, tmp_path, monkeypatch):
        home = _make_home(tmp_path, monkeypatch)
        config_dir = home.parent / "altconfig"
        config_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        _assert_parity()

    def test_empty_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", "")
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        values = _assert_parity()
        assert values["round_consult_gate"] is None


class TestConfigDirOverrideEmptyString:
    def test_empty_string_override_resolves_same_as_no_override(self, tmp_path, monkeypatch):
        """config_dir_override="" must resolve identically to no override, in
        both readers. Bash's `[ -n "$config_dir_override" ]` already treats ""
        as unprovided, falling through to normal resolution. Python's
        `config_value`/`config_enabled` must match that for a `str` argument
        (a `Path("")` argument is a separate, untested case -- see the
        docstring caveat on `config_value`)."""
        home = _make_home(tmp_path, monkeypatch)
        _write_state(home, b"handoff_nudge = false\n")

        result = subprocess.run(
            ["bash", "-c", f'. "{CONFIG_SH}"; _config_value handoff_nudge ""'],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        bash_value = result.stdout.strip()

        from _config import config_value

        python_value = config_value("handoff_nudge", "")

        assert bash_value == python_value == "false"


class TestMissingSchemaFile:
    def test_missing_schema_file_fails_clearly_in_both_readers(self, tmp_path, monkeypatch, capsys):
        """config-keys.psv itself absent (a partial stow-relink, an
        interrupted `git pull`) must fail loudly and distinctly in both
        readers -- not bash's same-return-code "unknown key" mis-signal, and
        not Python's uncaught FileNotFoundError."""
        _make_home(tmp_path, monkeypatch)
        isolated_hooks_dir = tmp_path / "isolated-hooks"
        isolated_hooks_dir.mkdir()
        # _config.sh's schema-file path resolves via BASH_SOURCE relative to
        # wherever it was sourced from (not realpath-followed) -- the same
        # isolation technique conftest.py's isolated_home fixture uses.
        # config-keys.psv is deliberately never symlinked in here.
        (isolated_hooks_dir / "_config.sh").symlink_to(CONFIG_SH)

        bash_result = subprocess.run(
            ["bash", "-c", f'. "{isolated_hooks_dir / "_config.sh"}"; _config_value worktree_required'],
            capture_output=True, text=True,
        )
        assert "schema file not found or unreadable" in bash_result.stderr

        import _config

        monkeypatch.setattr(_config, "_SCHEMA_FILE", isolated_hooks_dir / "config-keys.psv")
        with pytest.raises(KeyError):
            _config.config_value("worktree_required")
        assert "schema file not found or unreadable" in capsys.readouterr().err
