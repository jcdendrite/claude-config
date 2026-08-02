"""Tests for the machine-level opt-in prompts in install.sh."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_INSTALL_SH = Path(__file__).resolve().parents[4] / "install.sh"
_BASH = shutil.which("bash") or "/bin/bash"

_FIXTURE_START = "# INSTALL_TEST_FIXTURE: machine-level-opt-ins — start\n"
_FIXTURE_END = "# INSTALL_TEST_FIXTURE: machine-level-opt-ins — end"


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
    sentinel_path: Path,
    stdin_text: str,
    human_name: str = "Test sentinel",
    description: str = "A test sentinel description.",
    home: Path | None = None,
) -> subprocess.CompletedProcess:
    """Define _prompt_sentinel_opt_in and call it directly with the three
    positional args, feeding `stdin_text` to its `read -r -p` prompt.

    Exercises the prompt logic directly (bypassing the `[ -t 0 ]` TTY gate,
    which only wraps configure_machine_level_opt_ins) the same way
    test_install_sh_local_bin_path.py exercises ensure_local_bin_on_path.

    home: overrides $HOME in the subprocess so it matches sentinel_path's
    own "<home>/.claude/..." prefix — the function's own $HOME/.claude
    path-confinement guardrail needs the two to agree, same as real usage.
    Defaults to sentinel_path's own grandparent (".../home/.claude/x" ->
    ".../home") when the caller doesn't pass one explicitly.
    """
    env = dict(os.environ)
    env["HOME"] = str(home if home is not None else sentinel_path.parent.parent)
    script = (
        "set -e\n"
        + _extract_opt_ins_block()
        + f'\n_prompt_sentinel_opt_in "{sentinel_path}" "{human_name}" "{description}"\n'
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
    real install.sh call site — used for the TTY-gate test, where stdin is
    None (closed pipe) rather than a piped answer string."""
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
    def test_path_outside_home_claude_refused(self, tmp_path: Path) -> None:
        """Defense-in-depth: both real call sites hardcode a safe
        $HOME/.claude/... path, but the function itself must still refuse a
        path outside that prefix rather than silently touching/removing it —
        a future non-hardcoded call site should fail loudly, not silently."""
        home = tmp_path / "home"
        outside_path = tmp_path / "elsewhere" / "not-under-home-claude"

        result = _run_prompt_sentinel_opt_in(outside_path, "y\n", home=home)

        assert result.returncode != 0, (
            "a sentinel_path outside $HOME/.claude must be refused, not silently written"
        )
        assert not outside_path.exists()
        assert "refuses" in result.stderr

    def test_absent_sentinel_y_creates_it(self, tmp_path: Path) -> None:
        sentinel = tmp_path / "home" / ".claude" / "sentinel-required"

        result = _run_prompt_sentinel_opt_in(sentinel, "y\n")

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert sentinel.is_file(), "answering y must create the sentinel"
        assert "enabled: created" in result.stdout

    def test_absent_sentinel_bare_enter_stays_absent(self, tmp_path: Path) -> None:
        sentinel = tmp_path / "home" / ".claude" / "sentinel-required"

        result = _run_prompt_sentinel_opt_in(sentinel, "\n")

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert not sentinel.exists(), (
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
        sentinel = tmp_path / "home" / ".claude" / "sentinel-required"

        result = _run_prompt_sentinel_opt_in(sentinel, "")

        assert result.returncode == 0, (
            f"EOF on read must not abort the script under set -e; stderr={result.stderr!r}"
        )
        assert not sentinel.exists()
        assert "leaving" in result.stdout

    def test_present_sentinel_bare_enter_stays_present(self, tmp_path: Path) -> None:
        sentinel = tmp_path / "home" / ".claude" / "sentinel-required"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_text("")

        result = _run_prompt_sentinel_opt_in(sentinel, "\n")

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert sentinel.is_file(), (
            "a bare Enter on the present-sentinel prompt must default to Y — "
            "an already-enabled setting must not be silently disabled"
        )
        assert "keeping" in result.stdout

    def test_present_sentinel_n_removes_it(self, tmp_path: Path) -> None:
        sentinel = tmp_path / "home" / ".claude" / "sentinel-required"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_text("")

        result = _run_prompt_sentinel_opt_in(sentinel, "n\n")

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert not sentinel.exists(), "answering n must remove the sentinel"
        assert "disabled: removed" in result.stdout

    def test_absent_sentinel_uppercase_y_creates_it(self, tmp_path: Path) -> None:
        """[Yy]* is the sole differentiator on the absent-sentinel branch
        (anything else falls through to the "leave disabled" default) — an
        uppercase Y must match it, not just lowercase y."""
        sentinel = tmp_path / "home" / ".claude" / "sentinel-required"

        result = _run_prompt_sentinel_opt_in(sentinel, "Y\n")

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert sentinel.is_file()

    def test_present_sentinel_uppercase_n_removes_it(self, tmp_path: Path) -> None:
        """[Nn]* is the sole differentiator on the present-sentinel branch
        (anything else falls through to the "keep enabled" default) — an
        uppercase N must match it, not just lowercase n."""
        sentinel = tmp_path / "home" / ".claude" / "sentinel-required"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_text("")

        result = _run_prompt_sentinel_opt_in(sentinel, "N\n")

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert not sentinel.exists()


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
    """`_prompt_sentinel_opt_in` exercised with the exact paths/names
    configure_machine_level_opt_ins actually calls it with — the generic
    prompt-logic tests above use a synthetic path; these pin the real call
    site's two invocations specifically."""

    def test_worktree_required_sentinel_created_on_y(self, tmp_path: Path) -> None:
        sentinel = tmp_path / "home" / ".claude" / "worktree-required"
        result = _run_prompt_sentinel_opt_in(
            sentinel, "y\n", "Worktree enforcement", "test description"
        )
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert sentinel.is_file()

    def test_autonomous_shipping_required_sentinel_created_on_y(self, tmp_path: Path) -> None:
        sentinel = tmp_path / "home" / ".claude" / "autonomous-shipping-required"
        result = _run_prompt_sentinel_opt_in(
            sentinel, "y\n", "Autonomous shipping", "test description"
        )
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert sentinel.is_file()
