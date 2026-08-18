"""Tests for the contributor-intent prompt in install.sh."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

_INSTALL_SH = Path(__file__).resolve().parents[4] / "install.sh"
_BASH = shutil.which("bash") or "/bin/bash"

_FIXTURE_START = "# INSTALL_TEST_FIXTURE: contributor-intent-prompt — start\n"
_FIXTURE_END = "# INSTALL_TEST_FIXTURE: contributor-intent-prompt — end"


def _extract_prompt_block() -> str:
    """Return _print_default_contributor_hint + _prompt_contributor_intent +
    prompt_contributor_intent from install.sh.

    Same marker-delimited extraction strategy as
    test_install_sh_machine_level_opt_ins.py — a future reorder or nested
    conditional can't silently pick up the wrong text while the test keeps
    passing.
    """
    install_text = _INSTALL_SH.read_text()
    start = install_text.find(_FIXTURE_START)
    assert start != -1, f"{_FIXTURE_START!r} not found in {_INSTALL_SH}"
    end = install_text.find(_FIXTURE_END, start)
    assert end != -1, f"{_FIXTURE_END!r} not found after start marker in {_INSTALL_SH}"
    block = install_text[start + len(_FIXTURE_START) : end]
    for name in ("_print_default_contributor_hint", "_prompt_contributor_intent", "prompt_contributor_intent"):
        assert name in block, (
            f"extracted block is missing {name!r}; markers in {_INSTALL_SH} are "
            f"probably misplaced. Got: {block!r}"
        )
    return block


def _run_prompt_contributor_intent(stdin_text: str) -> subprocess.CompletedProcess:
    """Define the block and call the inner _prompt_contributor_intent
    directly — bypasses the `[ -t 0 ]` TTY gate, which only wraps the outer
    prompt_contributor_intent, the same way
    test_install_sh_machine_level_opt_ins.py's
    _run_prompt_sentinel_opt_in exercises _prompt_sentinel_opt_in around its
    own TTY gate."""
    script = "set -e\n" + _extract_prompt_block() + "\n_prompt_contributor_intent\n"
    return subprocess.run(
        [_BASH, "-c", script],
        input=stdin_text,
        capture_output=True,
        text=True,
        check=False,
    )


def _run_prompt_contributor_intent_wrapper(stdin: str | None) -> subprocess.CompletedProcess:
    """Define the block and call the outer prompt_contributor_intent, the
    real install.sh call site — used for the TTY-gate test, where stdin is
    None (closed pipe) rather than a piped answer string."""
    script = "set -e\n" + _extract_prompt_block() + "\nprompt_contributor_intent\n"
    return subprocess.run(
        [_BASH, "-c", script],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


class TestPromptContributorIntent:
    """Exercises the inner, ungated prompt-logic function directly with
    piped stdin — a subprocess fed via pipe is never a TTY regardless of
    what's piped, so driving this through the outer TTY-gated wrapper would
    always take the non-interactive branch."""

    def test_yes_points_at_install_dev_and_names_the_requirement(self) -> None:
        result = _run_prompt_contributor_intent("y\n")
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "install-dev.sh" in result.stdout
        assert "private-projects.md" in result.stdout

    def test_uppercase_yes_takes_the_same_branch(self) -> None:
        """[Yy]* is the sole differentiator — an uppercase Y must match it,
        not just lowercase y."""
        result = _run_prompt_contributor_intent("Y\n")
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "install-dev.sh" in result.stdout
        assert "private-projects.md" in result.stdout

    def test_no_keeps_the_static_hint(self) -> None:
        result = _run_prompt_contributor_intent("n\n")
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "Optional (contributors): run the hook test suite" in result.stdout
        assert "install-dev.sh" in result.stdout

    def test_bare_enter_defaults_to_the_static_hint(self) -> None:
        result = _run_prompt_contributor_intent("\n")
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "Optional (contributors): run the hook test suite" in result.stdout

    def test_eof_does_not_abort_under_set_e(self) -> None:
        """`input=""` (no trailing newline) forces read's true-EOF path,
        distinct from a bare Enter — install.sh runs under set -e, so an
        unguarded read here would otherwise abort silently."""
        result = _run_prompt_contributor_intent("")
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "Optional (contributors): run the hook test suite" in result.stdout


class TestPromptContributorIntentWrapper:
    def test_non_tty_stdin_prints_static_hint(self) -> None:
        """Closed/empty stdin (the `install.sh | somewhere` or CI shape)
        must not hang on `read -r -p`, and must fall back to the same
        static hint as answering no."""
        result = _run_prompt_contributor_intent_wrapper(stdin="")

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "Optional (contributors): run the hook test suite" in result.stdout


class TestCallSiteWiring:
    def test_real_install_sh_calls_prompt_contributor_intent_unconditionally(self) -> None:
        """The extraction tests above prove the functions behave correctly
        in isolation, but nothing else pins that install.sh's real tail
        still calls prompt_contributor_intent — a future edit that removes
        or conditionalizes that call site would leave every test above
        green while production silently stops prompting."""
        install_text = _INSTALL_SH.read_text()
        end = install_text.find(_FIXTURE_END)
        assert end != -1, f"{_FIXTURE_END!r} not found in {_INSTALL_SH}"
        tail = install_text[end:]
        call_lines = [line.strip() for line in tail.splitlines() if line.strip() == "prompt_contributor_intent"]
        assert call_lines, (
            "install.sh must call prompt_contributor_intent as a bare, "
            "unconditional statement after the fixture block; got no such "
            f"line in the tail:\n{tail!r}"
        )
