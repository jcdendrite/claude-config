"""Tests for the machine-level opt-in prompts in install.sh."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from _config import schema

_INSTALL_SH = Path(__file__).resolve().parents[4] / "install.sh"
_CONFIG_SH = Path(__file__).resolve().parents[1] / "_config.sh"
_BASH = shutil.which("bash") or "/bin/bash"

_FIXTURE_START = "# INSTALL_TEST_FIXTURE: machine-level-opt-ins — start\n"
_FIXTURE_END = "# INSTALL_TEST_FIXTURE: machine-level-opt-ins — end"


def _real_prompt_description(key: str) -> str:
    """config-keys.psv's prompt-description column for KEY, via _config.py's
    own schema() parser -- the single source of truth for config-keys.psv's
    column layout. Lets a TestRealSentinelPaths test pass the row's actual
    description through `_prompt_sentinel_opt_in`, instead of a synthetic
    placeholder -- the only way any test exercises the real
    prompt-description text through the real prompting function, since
    configure_machine_level_opt_ins's own TTY gate can't be driven from a
    subprocess pipe. This doesn't cover configure_machine_level_opt_ins's
    own field-routing loop (untested end-to-end, same TTY-gate limitation);
    test_install_sh_sentinel_inventory.py's
    TestSentinelInventoryArray.test_nonzero_entry_count covers a different,
    narrower seam -- command substitution at array-sourcing time, not the
    loop's runtime field order."""
    return schema()[key].prompt_description


def _config_sh_prelude() -> str:
    """install.sh:421's own `. "$REPO_DIR/claude/.claude/hooks/_config.sh"`
    line, sourced the real (not extracted) file at its absolute test-time
    path -- _prompt_sentinel_opt_in now reads/writes through
    _config_value/_config_set, both defined only in _config.sh."""
    return f'. "{_CONFIG_SH}"\n'


def _extract_opt_ins_block() -> str:
    """Return _prompt_sentinel_opt_in + configure_machine_level_opt_ins from
    install.sh.

    Same extraction strategy as test_install_sh_local_bin_path.py: delimited
    by marker comments rather than shell-syntax matching, so a future
    reorder or nested conditional can't silently pick up the wrong text
    while the test keeps passing.
    """
    install_text = _INSTALL_SH.read_text()
    start = install_text.find(_FIXTURE_START)
    assert start != -1, f"{_FIXTURE_START!r} not found in {_INSTALL_SH}"
    end = install_text.find(_FIXTURE_END, start)
    assert end != -1, f"{_FIXTURE_END!r} not found after start marker in {_INSTALL_SH}"
    block = install_text[start + len(_FIXTURE_START) : end]
    assert "_prompt_sentinel_opt_in" in block and "configure_machine_level_opt_ins" in block, (
        f"extracted block is missing a function; markers in {_INSTALL_SH} are "
        f"probably misplaced. Got: {block!r}"
    )
    return block


def _run_prompt_sentinel_opt_in(
    home: Path,
    key: str,
    stdin_text: str,
    human_name: str = "Test sentinel",
    description: str = "A test sentinel description.",
    config_dir: Path | None = None,
) -> subprocess.CompletedProcess:
    """Source _config.sh, define _prompt_sentinel_opt_in, and call it
    directly with the three positional args, feeding `stdin_text` to its
    `read -r -p` prompt.

    Exercises the prompt logic directly (bypassing the `[ -t 0 ]` TTY gate,
    which only wraps configure_machine_level_opt_ins) the same way
    test_install_sh_local_bin_path.py exercises ensure_local_bin_on_path.

    `key` must be a real config-keys.psv key -- _config_value/_config_set
    both refuse (return non-zero) on an unknown key, so a synthetic
    path-shaped string no longer exercises this function meaningfully now
    that the prompt reads/writes through the schema instead of a
    caller-supplied path.

    `config_dir`: when set, exported as CLAUDE_CONFIG_DIR instead of the
    default unset (resolves to `home/.claude`) -- lets a
    TestDisableStillEnabledReport test diverge the resolved config dir from
    `home` to exercise a config-dir-or-home key's union.
    """
    env = dict(os.environ)
    env["HOME"] = str(home)
    if config_dir is not None:
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    else:
        env.pop("CLAUDE_CONFIG_DIR", None)
    script = (
        "set -e\n"
        + _config_sh_prelude()
        + _extract_opt_ins_block()
        + f'\n_prompt_sentinel_opt_in "{key}" "{human_name}" "{description}"\n'
    )
    return subprocess.run(
        [_BASH, "-c", script],
        input=stdin_text,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _run_configure_machine_level_opt_ins(
    env: dict, stdin: str | None
) -> subprocess.CompletedProcess:
    """Define both functions and call configure_machine_level_opt_ins, the
    real install.sh call site -- used for the TTY-gate test, where stdin is
    None (closed pipe) rather than a piped answer string. configure_
    machine_level_opt_ins's own `[ ! -t 0 ]` short-circuit fires before it
    ever touches $_CONFIG_SCHEMA_FILE, so this doesn't need _config.sh
    sourced first."""
    script = "set -e\n" + _extract_opt_ins_block() + "\nconfigure_machine_level_opt_ins\n"
    return subprocess.run(
        [_BASH, "-c", script],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


class TestPromptSentinelOptIn:
    """Generic y/n/bare-Enter/EOF logic, exercised via
    permission_prompt_tracking -- an ordinary promptable, non-enforcement-
    critical key. TestRealSentinelPaths below covers the other real
    promptable keys' own human-name/prompt-description text specifically."""

    _KEY = "permission_prompt_tracking"

    def test_absent_sentinel_y_creates_it(self, tmp_path: Path) -> None:
        home = tmp_path / "home"

        result = _run_prompt_sentinel_opt_in(home, self._KEY, "y\n")

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert (
            home / ".claude" / "claude-config.toml"
        ).read_text() == "permission_prompt_tracking = true\n", "answering y must write true"
        assert "→ enabled" in result.stdout

    def test_absent_sentinel_bare_enter_stays_absent(self, tmp_path: Path) -> None:
        home = tmp_path / "home"

        result = _run_prompt_sentinel_opt_in(home, self._KEY, "\n")

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert not (home / ".claude" / "claude-config.toml").exists(), (
            "a bare Enter on the absent-sentinel prompt must default to N — "
            "no filesystem change for a scripted 'yes \"\"' or an unanswered prompt"
        )
        assert "leaving" in result.stdout

    def test_absent_sentinel_eof_does_not_abort_under_set_e(self, tmp_path: Path) -> None:
        """`read` returns non-zero on true EOF (e.g. Ctrl-D), not just on a
        bare Enter (which is a valid empty-line read, exit 0). install.sh
        runs under `set -e`, so an unguarded `read` would silently abort the
        whole script at this point — marketplace/plugin registration would
        never run, with no diagnostic. `input=""` (no trailing newline)
        closes stdin immediately, forcing the true-EOF path rather than the
        empty-line path `\\n` alone exercises."""
        home = tmp_path / "home"

        result = _run_prompt_sentinel_opt_in(home, self._KEY, "")

        assert result.returncode == 0, (
            f"EOF on read must not abort the script under set -e; stderr={result.stderr!r}"
        )
        assert not (home / ".claude" / "claude-config.toml").exists()
        assert "leaving" in result.stdout

    def test_present_sentinel_bare_enter_stays_present(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "claude-config.toml").write_text(
            "permission_prompt_tracking = true\n"
        )

        result = _run_prompt_sentinel_opt_in(home, self._KEY, "\n")

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert (
            home / ".claude" / "claude-config.toml"
        ).read_text() == "permission_prompt_tracking = true\n", (
            "a bare Enter on the present-sentinel prompt must default to Y — "
            "an already-enabled setting must not be silently disabled"
        )
        assert "keeping" in result.stdout

    def test_present_sentinel_n_removes_it(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "claude-config.toml").write_text(
            "permission_prompt_tracking = true\n"
        )

        result = _run_prompt_sentinel_opt_in(home, self._KEY, "n\n")

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert (
            home / ".claude" / "claude-config.toml"
        ).read_text() == "permission_prompt_tracking = false\n", (
            "answering n must write false"
        )
        assert "→ disabled" in result.stdout

    def test_absent_sentinel_uppercase_y_creates_it(self, tmp_path: Path) -> None:
        """[Yy]* is the sole differentiator on the absent-sentinel branch
        (anything else falls through to the "leave disabled" default) — an
        uppercase Y must match it, not just lowercase y."""
        home = tmp_path / "home"

        result = _run_prompt_sentinel_opt_in(home, self._KEY, "Y\n")

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert (
            home / ".claude" / "claude-config.toml"
        ).read_text() == "permission_prompt_tracking = true\n"

    def test_present_sentinel_uppercase_n_removes_it(self, tmp_path: Path) -> None:
        """[Nn]* is the sole differentiator on the present-sentinel branch
        (anything else falls through to the "keep enabled" default) — an
        uppercase N must match it, not just lowercase n."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "claude-config.toml").write_text(
            "permission_prompt_tracking = true\n"
        )

        result = _run_prompt_sentinel_opt_in(home, self._KEY, "N\n")

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert (
            home / ".claude" / "claude-config.toml"
        ).read_text() == "permission_prompt_tracking = false\n"


class TestPathConfinementGuardRemoved:
    """_prompt_sentinel_opt_in's first argument is a config-keys.psv key
    name, not a caller-supplied path, so there is nothing to confine -- a
    bare key like "permission_prompt_tracking" must be accepted, not
    refused by any $HOME/.claude/-prefix check."""

    def test_bare_key_argument_is_accepted_not_refused(self, tmp_path: Path) -> None:
        home = tmp_path / "home"

        result = _run_prompt_sentinel_opt_in(home, "permission_prompt_tracking", "y\n")

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "refuses" not in result.stderr
        assert (home / ".claude" / "claude-config.toml").exists()


class TestConfigureMachineLevelOptIns:
    def test_non_tty_stdin_skips_both_prompts_no_filesystem_change(self, tmp_path: Path) -> None:
        """Closed/empty stdin (the `install.sh | somewhere` or CI shape) must
        not hang on `read -r -p`, and must leave both sentinels untouched."""
        home = tmp_path / "home"
        home.mkdir()
        env = dict(os.environ)
        env["HOME"] = str(home)

        result = _run_configure_machine_level_opt_ins(env, stdin="")

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert not (home / ".claude" / "worktree-required").exists()
        assert not (home / ".claude" / "autonomous-shipping-required").exists()
        assert "skipped" in result.stdout

    def test_non_tty_stdin_does_not_disable_existing_sentinels(self, tmp_path: Path) -> None:
        """Non-interactive skip must leave a pre-existing sentinel enabled —
        skipping is a no-op, not an implicit 'disable'."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "worktree-required").write_text("")
        (home / ".claude" / "autonomous-shipping-required").write_text("")
        env = dict(os.environ)
        env["HOME"] = str(home)

        result = _run_configure_machine_level_opt_ins(env, stdin="")

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert (home / ".claude" / "worktree-required").exists()
        assert (home / ".claude" / "autonomous-shipping-required").exists()


class TestRealSentinelPaths:
    """`_prompt_sentinel_opt_in` exercised with the exact keys/human-names
    configure_machine_level_opt_ins's schema-driven loop actually calls it
    with -- the generic prompt-logic tests in TestPromptSentinelOptIn use a
    single representative key; these pin four of the real promptable rows
    specifically."""

    def test_worktree_required_key_written_on_y(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        result = _run_prompt_sentinel_opt_in(
            home, "worktree_required", "y\n", "Worktree enforcement", "test description"
        )
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert (
            home / ".claude" / "claude-config.toml"
        ).read_text() == "worktree_required = true\n"

    def test_autonomous_shipping_key_written_on_y(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        result = _run_prompt_sentinel_opt_in(
            home, "autonomous_shipping", "y\n", "Autonomous shipping", "test description"
        )
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert (
            home / ".claude" / "claude-config.toml"
        ).read_text() == "autonomous_shipping = true\n"

    def test_error_mode_nudge_key_written_on_y(self, tmp_path: Path) -> None:
        """Passes the row's real prompt-description text (not a synthetic
        placeholder like the two tests above) — see
        _real_prompt_description."""
        home = tmp_path / "home"
        result = _run_prompt_sentinel_opt_in(
            home,
            "error_mode_nudge",
            "y\n",
            "Error-mode analysis nudge",
            _real_prompt_description("error_mode_nudge"),
        )
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert (
            home / ".claude" / "claude-config.toml"
        ).read_text() == "error_mode_nudge = true\n"

    def test_cost_ledger_recording_key_written_on_y(self, tmp_path: Path) -> None:
        """Passes the row's real prompt-description text (not a synthetic
        placeholder like the two tests above) — see
        _real_prompt_description."""
        home = tmp_path / "home"
        result = _run_prompt_sentinel_opt_in(
            home,
            "cost_ledger_recording",
            "y\n",
            "Cost ledger recording",
            _real_prompt_description("cost_ledger_recording"),
        )
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert (
            home / ".claude" / "claude-config.toml"
        ).read_text() == "cost_ledger_recording = true\n"


class TestDisableStillEnabledReport:
    """A config-dir-or-home key's opt-out only writes `false` to the
    resolved CLAUDE_CONFIG_DIR -- if $HOME/.claude's own legacy file is
    still present, the union (see docs/config-file.md) keeps resolving the
    key ENABLED even after that write. _prompt_sentinel_opt_in must report
    the actual post-write resolved state, not the bare fact that the write
    succeeded."""

    def test_disabling_at_config_dir_with_surviving_home_legacy_file_reports_still_enabled(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        config_dir = tmp_path / "profile-config"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "worktree-required").write_text("# machine-level sentinel\n")

        result = _run_prompt_sentinel_opt_in(
            home, "worktree_required", "n\n", "Worktree enforcement", config_dir=config_dir
        )

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert (config_dir / "claude-config.toml").read_text() == "worktree_required = false\n", (
            "the opt-out must still write false at the resolved config dir"
        )
        assert "→ disabled" not in result.stdout, (
            "must not falsely report success while the union still resolves true"
        )
        assert "still resolves ENABLED" in result.stdout
        assert str(home / ".claude" / "worktree-required") in result.stdout, (
            "must name the surviving legacy file path"
        )

    def test_disabling_at_config_dir_with_no_home_legacy_file_reports_disabled(
        self, tmp_path: Path
    ) -> None:
        """Same diverged-CLAUDE_CONFIG_DIR shape as the test above, but with
        no surviving legacy file at $HOME/.claude -- the opt-out must report
        plain success, not a false "still enabled" alarm."""
        home = tmp_path / "home"
        config_dir = tmp_path / "profile-config"
        config_dir.mkdir()
        (config_dir / "claude-config.toml").write_text("worktree_required = true\n")

        result = _run_prompt_sentinel_opt_in(
            home, "worktree_required", "n\n", "Worktree enforcement", config_dir=config_dir
        )

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert (config_dir / "claude-config.toml").read_text() == "worktree_required = false\n"
        assert "→ disabled" in result.stdout
        assert "still resolves ENABLED" not in result.stdout

    def test_disabling_at_config_dir_with_home_state_file_row_reports_generic_fallback(
        self, tmp_path: Path
    ) -> None:
        """Same diverged-CLAUDE_CONFIG_DIR shape as the tests above, but the
        surviving disagreement lives in $HOME/.claude/claude-config.toml
        itself (no legacy marker file present) -- exercises the `else`
        branch's generic fallback message, which names claude-config.toml
        directly rather than a specific legacy file path."""
        home = tmp_path / "home"
        config_dir = tmp_path / "profile-config"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "claude-config.toml").write_text("worktree_required = true\n")

        result = _run_prompt_sentinel_opt_in(
            home, "worktree_required", "n\n", "Worktree enforcement", config_dir=config_dir
        )

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert (config_dir / "claude-config.toml").read_text() == "worktree_required = false\n", (
            "the opt-out must still write false at the resolved config dir"
        )
        assert "→ disabled" not in result.stdout, (
            "must not falsely report success while the union still resolves true"
        )
        assert "still resolves ENABLED" in result.stdout
        assert f"check {home / '.claude'}/claude-config.toml for a disagreeing row" in result.stdout
