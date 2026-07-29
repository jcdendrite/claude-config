"""Tests for the claude-auto wrapper's model-selection precedence.

The wrapper has no launcher seam of its own — it calls `claude` by name — so
every test prepends a stub `claude` to PATH that records its argv. That stub is
what keeps these tests away from the real binary.

The property under test is which model backs the session: a caller-supplied
`--model` wins, then ANTHROPIC_MODEL, then the Sonnet fallback. Getting this
wrong is silent — the session still starts, just on a model the caller did not
ask for (or, when the wrapper injects a second `--model`, on whichever of the
two the argument parser happens to prefer).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

_REPO_ROOT = Path(
    subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=Path(__file__).resolve().parent,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
)

# Unlike its siblings under claude/.local/bin/, claude-auto carries its logic
# directly rather than dispatching to a claude/.claude/scripts/*.sh file, so
# this reaches across the tree from the repo root instead of climbing a fixed
# number of parents — a count that silently resolves elsewhere if either
# directory moves.
_SCRIPT = _REPO_ROOT / "claude" / ".local" / "bin" / "claude-auto"


def test_script_under_test_exists() -> None:
    """Fail with a clear message rather than a FileNotFoundError per test."""
    assert _SCRIPT.is_file(), f"claude-auto not found at {_SCRIPT} — did it move?"

_RECORDER_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "{recorder}"
"""


def _run(args: list[str], tmp_path: Path, env_extra: dict | None = None) -> list[str]:
    """Run claude-auto with a stubbed `claude`; return the argv it was handed."""
    recorder = tmp_path / "recorder.txt"
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "claude"
    stub.write_text(_RECORDER_STUB.format(recorder=recorder))
    stub.chmod(0o755)

    env = dict(os.environ)
    # Drop any ANTHROPIC_MODEL inherited from the developer's own shell, so a
    # test asserting the no-env fallback doesn't pass or fail by accident.
    env.pop("ANTHROPIC_MODEL", None)
    env["PATH"] = f"{stub_dir}{os.pathsep}{env['PATH']}"
    if env_extra:
        env.update(env_extra)

    result = subprocess.run(
        [str(_SCRIPT), *args], capture_output=True, text=True, env=env, check=False
    )
    assert result.returncode == 0, f"claude-auto failed: {result.stdout}{result.stderr}"
    return recorder.read_text().splitlines()


def _model_of(argv: list[str]) -> str | None:
    """Extract the model from either `--model X` or `--model=X` form."""
    for index, arg in enumerate(argv):
        if arg == "--model":
            return argv[index + 1]
        if arg.startswith("--model="):
            return arg.split("=", 1)[1]
    return None


class TestModelPrecedence:
    def test_no_model_anywhere_falls_back_to_sonnet(self, tmp_path: Path) -> None:
        """The fallback exists so an ineligible configured default can't stop
        the session from starting."""
        assert _model_of(_run([], tmp_path)) == "sonnet"

    def test_env_var_is_used_when_no_flag_is_given(self, tmp_path: Path) -> None:
        argv = _run([], tmp_path, {"ANTHROPIC_MODEL": "opus"})
        assert _model_of(argv) == "opus"

    def test_caller_flag_beats_the_env_var(self, tmp_path: Path) -> None:
        argv = _run(["--model", "opus"], tmp_path, {"ANTHROPIC_MODEL": "sonnet"})
        assert _model_of(argv) == "opus"

    def test_equals_form_is_recognized(self, tmp_path: Path) -> None:
        argv = _run(["--model=fable"], tmp_path, {"ANTHROPIC_MODEL": "opus"})
        assert _model_of(argv) == "fable"

    def test_flag_after_a_positional_prompt_still_wins(self, tmp_path: Path) -> None:
        argv = _run(["summarize the open PRs", "--model", "opus"], tmp_path)
        assert _model_of(argv) == "opus"


class TestNoDuplicateModelFlag:
    """Two `--model` flags would make the effective model parser-dependent."""

    def test_caller_flag_is_not_shadowed_by_an_injected_one(self, tmp_path: Path) -> None:
        argv = _run(["--model", "opus"], tmp_path)
        assert argv.count("--model") == 1, f"duplicate --model in {argv}"

    def test_equals_form_is_not_shadowed_by_an_injected_one(self, tmp_path: Path) -> None:
        argv = _run(["--model=opus"], tmp_path)
        assert not any(a == "--model" for a in argv), f"injected --model in {argv}"

    def test_caller_supplied_duplicates_are_passed_through_untouched(
        self, tmp_path: Path
    ) -> None:
        """Two caller `--model` flags are the caller's own ambiguity to own —
        the wrapper must not add a third and deepen it."""
        argv = _run(["--model", "opus", "--model", "sonnet"], tmp_path)
        assert argv.count("--model") == 2, f"wrapper added a --model: {argv}"
        assert argv[-4:] == ["--model", "opus", "--model", "sonnet"], argv


class TestNonMatchingArguments:
    """The scan matches whole arguments, so near-misses must fall through."""

    def test_a_flag_that_merely_resembles_model_does_not_suppress_the_default(
        self, tmp_path: Path
    ) -> None:
        """Guards against a future edit widening the match to a `*model*` glob,
        which would silently start swallowing unrelated flags."""
        argv = _run(["--fallback-model", "opus"], tmp_path)
        assert _model_of(argv) == "sonnet", f"--fallback-model suppressed it: {argv}"
        assert "--fallback-model" in argv, f"flag was consumed: {argv}"

    def test_an_empty_argument_is_preserved_and_matches_nothing(
        self, tmp_path: Path
    ) -> None:
        argv = _run([""], tmp_path)
        assert _model_of(argv) == "sonnet", argv
        assert argv[-1] == "", f"empty argument was dropped: {argv}"


class TestEndOfOptionsMarker:
    def test_model_after_a_double_dash_is_not_treated_as_a_flag(
        self, tmp_path: Path
    ) -> None:
        """`--` makes everything after it positional text, so a literal
        `--model` there is not a caller-supplied flag to defer to. Deferring
        anyway would drop the wrapper's own default and hand the session back
        to a configured default auto mode may reject."""
        argv = _run(["--", "--model"], tmp_path)
        assert argv[:2] == ["--model", "sonnet"], (
            f"wrapper should still inject its default ahead of `--`: {argv}"
        )


class TestPermissionMode:
    def test_auto_mode_is_passed_in_every_invocation_shape(self, tmp_path: Path) -> None:
        """Starting this wrapper without `--permission-mode auto` would silently
        drop the caller into their configured default mode."""
        shapes: list[list[str]] = [
            [],
            ["--model", "opus"],
            ["--model=opus"],
            ["a prompt"],
            ["--", "--model"],
        ]
        for shape in shapes:
            argv = _run(shape, tmp_path)
            index = argv.index("--permission-mode")
            assert argv[index + 1] == "auto", f"{shape} produced {argv}"

    def test_positional_prompt_survives_as_a_single_argument(
        self, tmp_path: Path
    ) -> None:
        argv = _run(["summarize the open PRs"], tmp_path)
        assert "summarize the open PRs" in argv, f"prompt was split or lost: {argv}"
