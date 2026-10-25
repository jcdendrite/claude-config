"""Unit tests for _lib.sh's _lib_command_words and the extractors built on it
(_lib_extract_git_subcmd and its siblings).
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    _sourced_value,
    assert_cap_engaged,
    build_path_without,
    scaled_shim_sleep,
    write_scaled_timeout_shim,
    xargs_is_gnu,
)

from ._lib_sh_helpers import _LIB_SH, _run_lib_call

# --- _lib_command_words and the extractors built on it -----------------
#
# _lib_command_words runs `xargs -n1` first and retries with
# split-command-words.py only when xargs exits nonzero. The tokenizer's own
# word-splitting cases live in test_split_command_words.py; these tests pin
# the shell-side contract: the fast path, the retry stream layout, the
# failure statuses, and the tokenizer-path resolution.

_SPLITTER = HOOKS_DIR / "split-command-words.py"

# Cases marked `requires_gnu_xargs` skip on any other xargs. On GNU, each asserts its xargs
# precondition so a changed xargs fails with this message instead of a bare mismatch.
_GNU_XARGS_ASSUMPTION = "this case assumes GNU xargs behavior, which BSD xargs does not share"
requires_gnu_xargs = pytest.mark.skipif(not xargs_is_gnu(), reason="needs GNU xargs")

# Each command makes plain `xargs -n1` fail after it has printed the early flag's path.
_XARGS_FAILING_COMMANDS_WITH_EARLY_FLAG = {
    "bare-opener-then-apostrophe-in-body": (
        "git commit -F /early/msg -m x\ncat <<'EOF'\nit's\nEOF\ngit commit -F /late/msg"
    ),
    "escaped-double-quote-inside-double-quotes": r'git commit -F /early/msg -m "say \"hi\" now" -F /late/msg',
    "newline-inside-double-quotes": 'git commit -F /early/msg -m "line one\nline two" -F /late/msg',
}

# One word just past GNU xargs's per-argument limit of 128 KiB (131072 bytes).
# It is small enough for shlex to finish in well under the 5 s cap.
_PAST_XARGS_LIMIT_COMMAND = "git commit -m " + "x" * 140_000 + " -F /late/msg"

_SINGLE_LINE_BODY_COMMAND = "gh pr create --body-file /x"
# A newline inside a quoted argument is a grammar-level xargs failure on every implementation.
_XARGS_FAILING_BODY_COMMAND = 'gh pr create --title "a\nb" --body-file /x'

# _LIB_COMMAND_WORDS_NO_WORDS_STATUS in _lib.sh: the fallback tokenizer exited 0 with no words.
_NO_WORDS_STATUS = int(_sourced_value("_LIB_COMMAND_WORDS_NO_WORDS_STATUS"))

# Every xargs-failing command the shell-side tests use; the retry-stream layout must hold for each.
_ALL_XARGS_FAILING_COMMANDS = {
    **_XARGS_FAILING_COMMANDS_WITH_EARLY_FLAG,
    "newline-inside-double-quotes-gh-body": _XARGS_FAILING_BODY_COMMAND,
    "word-past-the-xargs-limit": _PAST_XARGS_LIMIT_COMMAND,
}


def _write_command_file(tmp_path: Path, command: str) -> Path:
    command_file = tmp_path / "command.txt"
    command_file.write_text(command)
    return command_file


def _run_lib_helper_on_command(
    call_prefix: str,
    command: str,
    tmp_path: Path,
    env: dict | None = None,
    lib_path: Path = _LIB_SH,
) -> subprocess.CompletedProcess:
    """Run `call_prefix "<command>"` after sourcing lib_path.

    The command travels through a file, not the `bash -c` string, because a single
    argv or environment string is capped at 128 KiB on Linux.
    """
    command_file = _write_command_file(tmp_path, command)
    harness = f'. {shlex.quote(str(lib_path))}; {call_prefix} "$(cat {shlex.quote(str(command_file))})"'
    return subprocess.run(
        ["bash", "-c", harness],
        capture_output=True,
        text=True,
        check=False,
        env=dict(os.environ) if env is None else env,
    )


def _run_lib_script_on_command(
    script: str,
    command: str,
    tmp_path: Path,
    env: dict | None = None,
    lib_path: Path = _LIB_SH,
) -> subprocess.CompletedProcess:
    """Run `script` after sourcing lib_path, with the command text in the shell variable `command`."""
    command_file = _write_command_file(tmp_path, command)
    harness = f'. {shlex.quote(str(lib_path))}; command="$(cat {shlex.quote(str(command_file))})"; {script}'
    return subprocess.run(
        ["bash", "-c", harness],
        capture_output=True,
        text=True,
        check=False,
        env=dict(os.environ) if env is None else env,
    )


def _plain_xargs(command: str) -> subprocess.CompletedProcess:
    if shutil.which("xargs") is None:
        pytest.skip("xargs not on PATH")
    return subprocess.run(["xargs", "-n1"], input=command + "\n", capture_output=True, text=True, check=False)


def _plain_xargs_stdout(command: str) -> str:
    return _plain_xargs(command).stdout


def _python3_shim_dir(tmp_path: Path, shim_body: str) -> Path:
    """A directory holding a `python3` shell shim, for prepending to PATH."""
    shim_dir = tmp_path / "stub-bin-python3-shim"
    shim_dir.mkdir()
    shim = shim_dir / "python3"
    shim.write_text(f"#!/bin/bash\n{shim_body}\n")
    shim.chmod(0o755)
    return shim_dir


def _env_with_path_prefix(directory: Path) -> dict:
    env = dict(os.environ)
    env["PATH"] = f"{directory}:{os.environ['PATH']}"
    return env


def _splitter_stdout(command: str) -> str:
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(_SPLITTER)], input=command, capture_output=True, text=True, check=True
    )
    return result.stdout


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m 'a b' --file=/x -F '/p q/r'",
        'gh pr create --body-file /x --title "two words" --repo owner/name',
        "git commit -F /x",
    ],
)
def test_command_words_single_line_equals_plain_xargs_output(command: str, tmp_path: Path) -> None:
    result = _run_lib_helper_on_command("_lib_command_words", command, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == _plain_xargs_stdout(command)


def test_command_words_single_line_does_not_need_python3(tmp_path: Path) -> None:
    """The fast path never spawns the fallback tokenizer, so a PATH without python3 changes nothing."""
    farm_dir = tmp_path / "path-without-python3"
    farm_dir.mkdir()
    env = dict(os.environ)
    env["PATH"] = build_path_without("python3", farm_dir)
    command = "git commit -m 'a b' -F /x"
    result = _run_lib_helper_on_command("_lib_command_words", command, tmp_path, env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout == _plain_xargs_stdout(command)


@requires_gnu_xargs
@pytest.mark.parametrize("command_name", sorted(_ALL_XARGS_FAILING_COMMANDS))
def test_command_words_after_xargs_failure_prints_xargs_words_then_blank_line_then_retry_words(
    command_name: str, tmp_path: Path
) -> None:
    """Monotonicity: the retry only adds words after what xargs printed, so an extractor never finds fewer paths."""
    command = _ALL_XARGS_FAILING_COMMANDS[command_name]
    plain_xargs = _plain_xargs(command)
    assert plain_xargs.returncode != 0, f"precondition: {_GNU_XARGS_ASSUMPTION}; xargs should fail on this command"

    result = _run_lib_helper_on_command("_lib_command_words", command, tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == plain_xargs.stdout + "\n" + _splitter_stdout(command)


@requires_gnu_xargs
@pytest.mark.parametrize("command_name", sorted(_XARGS_FAILING_COMMANDS_WITH_EARLY_FLAG))
def test_command_words_after_xargs_failure_keeps_the_early_path_and_adds_the_late_path(
    command_name: str, tmp_path: Path
) -> None:
    command = _XARGS_FAILING_COMMANDS_WITH_EARLY_FLAG[command_name]
    xargs_lines = _plain_xargs_stdout(command).split("\n")
    assert "/early/msg" in xargs_lines, f"precondition: {_GNU_XARGS_ASSUMPTION}; xargs should print the early path"
    assert "/late/msg" not in xargs_lines, f"precondition: {_GNU_XARGS_ASSUMPTION}; xargs should drop the late path"

    result = _run_lib_helper_on_command("_lib_command_words", command, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "/early/msg" in result.stdout.split("\n")
    assert "/late/msg" in result.stdout.split("\n")


def test_git_commit_message_source_paths_after_xargs_failure_prints_late_file_path(tmp_path: Path) -> None:
    command = _XARGS_FAILING_COMMANDS_WITH_EARLY_FLAG["escaped-double-quote-inside-double-quotes"]
    result = _run_lib_helper_on_command("_lib_git_commit_message_source_paths", command, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "/late/msg" in result.stdout.split("\n")


def test_gh_body_source_paths_after_multiline_value_prints_late_file_path(tmp_path: Path) -> None:
    command = 'gh pr create --title "line one\nline two" --body-file /late/body.md -T=/late/template.md'
    result = _run_lib_helper_on_command("_lib_gh_body_source_paths", command, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split("\n") == ["/late/body.md", "/late/template.md", ""]


@requires_gnu_xargs
def test_xargs_failing_pending_flag_is_closed_by_a_blank_path_before_the_retry_words(tmp_path: Path) -> None:
    """xargs's partial output ends in -F, so the blank separator is that flag's empty path."""
    command = 'git commit -F "line one\nline two" -m x'
    xargs_lines = _plain_xargs_stdout(command).split("\n")
    assert xargs_lines[-2] == "-F", f"precondition: {_GNU_XARGS_ASSUMPTION}; xargs should stop right after -F"

    result = _run_lib_helper_on_command("_lib_git_commit_message_source_paths", command, tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "\nline one line two\n"


@requires_gnu_xargs
def test_unrelated_tools_flag_after_the_xargs_failure_point_reaches_the_extractor(tmp_path: Path) -> None:
    """Documented over-deny: the retry exposes `ssh -T host` to a body-flag extractor that xargs never reached."""
    command = 'gh pr create --title "line one\nline two" --repo r && ssh -T host'
    assert "host" not in _plain_xargs_stdout(command).split("\n"), (
        f"precondition: {_GNU_XARGS_ASSUMPTION}; xargs should fail before reaching host"
    )

    result = _run_lib_helper_on_command("_lib_gh_body_source_paths", command, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "host" in result.stdout.split("\n")


@requires_gnu_xargs
@pytest.mark.parametrize(
    ("extractor", "source_flag"),
    [("_lib_gh_body_source_paths", "--body-file"), ("_lib_git_commit_message_source_paths", "-F")],
)
def test_backslash_newline_between_a_flag_and_its_path_hides_the_path_from_the_extractor(
    extractor: str, source_flag: str, tmp_path: Path
) -> None:
    """Pins the extractor's output for the gap recorded in the `_lib_command_words` header in _lib.sh.

    An extractor-side fix flips this test on purpose. A caller-side fix would need its own test.
    """
    command = f"tool {source_flag} \\\n  /tmp/b.md --repo r"
    plain_xargs = _plain_xargs(command)
    assert plain_xargs.returncode == 0, f"precondition: {_GNU_XARGS_ASSUMPTION}; xargs should accept the command"
    xargs_lines = plain_xargs.stdout.split("\n")
    flag_index = xargs_lines.index(source_flag)
    assert xargs_lines[flag_index + 1] == "", (
        f"precondition: {_GNU_XARGS_ASSUMPTION}; xargs should print an empty word after {source_flag}"
    )

    result = _run_lib_helper_on_command(extractor, command, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "/tmp/b.md" not in result.stdout.split("\n")


@requires_gnu_xargs
def test_command_words_one_word_past_the_xargs_limit_then_flag_prints_the_flags_path(tmp_path: Path) -> None:
    """GNU xargs rejects one argument this long, so the retry runs."""
    plain_xargs = _plain_xargs(_PAST_XARGS_LIMIT_COMMAND)
    assert plain_xargs.returncode != 0, f"precondition: {_GNU_XARGS_ASSUMPTION}; xargs should reject the long word"
    assert "/late/msg" not in plain_xargs.stdout.split("\n")

    result = _run_lib_helper_on_command("_lib_git_commit_message_source_paths", _PAST_XARGS_LIMIT_COMMAND, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "/late/msg" in result.stdout.split("\n")


def test_command_words_unsplittable_command_returns_status_3_with_empty_stdout(tmp_path: Path) -> None:
    command = 'git commit -m "unterminated -F /x'
    result = _run_lib_helper_on_command("_lib_command_words", command, tmp_path)
    assert result.returncode == 3, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("extractor", ["_lib_gh_body_source_paths", "_lib_git_commit_message_source_paths"])
def test_extractors_return_the_command_words_status_unchanged_for_an_unsplittable_command(
    extractor: str, tmp_path: Path
) -> None:
    result = _run_lib_helper_on_command(extractor, 'gh pr create --title "unterminated --body-file /x', tmp_path)
    assert result.returncode == 3, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("extractor", ["_lib_gh_body_source_paths", "_lib_git_commit_message_source_paths"])
@pytest.mark.parametrize("python3_status", [3, 124], ids=["untokenizable", "cap-kill"])
def test_extractors_return_the_tokenizers_nonzero_status_unchanged(
    extractor: str, python3_status: int, tmp_path: Path
) -> None:
    shim_dir = _python3_shim_dir(tmp_path, f"exit {python3_status}")
    result = _run_lib_helper_on_command(
        extractor, _XARGS_FAILING_BODY_COMMAND, tmp_path, env=_env_with_path_prefix(shim_dir)
    )
    assert result.returncode == python3_status, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("silent_tokenizer", ["python3-shim-exits-0", "zero-byte-tokenizer-script"])
def test_command_words_denies_when_the_fallback_tokenizer_exits_0_with_empty_stdout(
    silent_tokenizer: str, tmp_path: Path
) -> None:
    """A silent fallback gets its own status, not a command with no further words."""
    if silent_tokenizer == "python3-shim-exits-0":
        shim_dir = _python3_shim_dir(tmp_path, "exit 0")
        result = _run_lib_helper_on_command(
            "_lib_command_words", _XARGS_FAILING_BODY_COMMAND, tmp_path, env=_env_with_path_prefix(shim_dir)
        )
    else:
        lib_copy = _copy_lib_with_tokenizer(tmp_path / "hooks-copy")
        (lib_copy.parent / _SPLITTER.name).write_bytes(b"")
        result = _run_lib_helper_on_command(
            "_lib_command_words", _XARGS_FAILING_BODY_COMMAND, tmp_path, lib_path=lib_copy
        )
    assert result.returncode == _NO_WORDS_STATUS, result.stderr
    assert result.stdout == ""


def test_command_words_denies_a_comment_only_command_that_xargs_cannot_split(tmp_path: Path) -> None:
    """Intended denial: the fallback drops comment lines, so nothing remains and the empty output is denied."""
    command = "# it's"
    assert _plain_xargs(command).returncode != 0, "precondition: xargs should fail on the unmatched apostrophe"

    result = _run_lib_helper_on_command("_lib_command_words", command, tmp_path)

    assert result.returncode == _NO_WORDS_STATUS, result.stderr
    assert result.stdout == ""


def test_command_words_runs_the_tokenizer_under_isolated_python_flags(tmp_path: Path) -> None:
    """-I and -S keep PYTHONPATH, user-site modules, and site-packages startup code out of the gate."""
    argv_record = tmp_path / "python3-argv.txt"
    real_python3 = shutil.which("python3")
    assert real_python3, "python3 not found in PATH"
    shim_dir = _python3_shim_dir(
        tmp_path,
        f"printf '%s\\n' \"$@\" > {shlex.quote(str(argv_record))}\nexec {shlex.quote(real_python3)} \"$@\"",
    )
    result = _run_lib_helper_on_command(
        "_lib_command_words", _XARGS_FAILING_BODY_COMMAND, tmp_path, env=_env_with_path_prefix(shim_dir)
    )
    assert result.returncode == 0, result.stderr
    assert argv_record.read_text().split("\n")[:2] == ["-I", "-S"]


def test_command_words_ignores_a_planted_shlex_on_pythonpath(tmp_path: Path) -> None:
    planted_dir = tmp_path / "planted-modules"
    planted_dir.mkdir()
    (planted_dir / "shlex.py").write_text('raise RuntimeError("planted shlex imported")\n')
    env = dict(os.environ)
    env["PYTHONPATH"] = str(planted_dir)
    command = _XARGS_FAILING_COMMANDS_WITH_EARLY_FLAG["newline-inside-double-quotes"]

    result = _run_lib_helper_on_command("_lib_command_words", command, tmp_path, env=env)

    assert result.returncode == 0, result.stderr
    assert result.stdout == _plain_xargs_stdout(command) + "\n" + _splitter_stdout(command)


def test_command_words_ignores_a_planted_re_beside_the_tokenizer(tmp_path: Path) -> None:
    lib_copy = _copy_lib_with_tokenizer(tmp_path / "hooks-copy")
    (lib_copy.parent / "re.py").write_text('raise RuntimeError("planted re.py imported")\n')
    command = _XARGS_FAILING_COMMANDS_WITH_EARLY_FLAG["newline-inside-double-quotes"]

    result = _run_lib_helper_on_command("_lib_command_words", command, tmp_path, lib_path=lib_copy)

    assert result.returncode == 0, result.stderr
    assert result.stdout == _plain_xargs_stdout(command) + "\n" + _splitter_stdout(command)


# Expected status per (missing tool, command shape) for _lib_gh_body_source_paths:
#   xargs missing: the fast path fails (127) and the retry splits the command, so both shapes succeed.
#   python3 missing: a single-line command never needs it; an xargs-failing command has no fallback (127).
#   awk missing: the extractor itself cannot run (127), whichever tokenizer produced the words.


@pytest.mark.parametrize(
    ("missing_binary", "command", "expected_status"),
    [
        ("xargs", _SINGLE_LINE_BODY_COMMAND, 0),
        ("xargs", _XARGS_FAILING_BODY_COMMAND, 0),
        ("python3", _SINGLE_LINE_BODY_COMMAND, 0),
        ("python3", _XARGS_FAILING_BODY_COMMAND, 127),
        ("awk", _SINGLE_LINE_BODY_COMMAND, 127),
        ("awk", _XARGS_FAILING_BODY_COMMAND, 127),
    ],
    ids=[
        "xargs-missing-single-line",
        "xargs-missing-xargs-failing",
        "python3-missing-single-line",
        "python3-missing-xargs-failing",
        "awk-missing-single-line",
        "awk-missing-xargs-failing",
    ],
)
def test_gh_body_source_paths_status_when_a_tool_is_missing(
    missing_binary: str, command: str, expected_status: int, tmp_path: Path
) -> None:
    farm_dir = tmp_path / f"path-without-{missing_binary}"
    farm_dir.mkdir()
    env = dict(os.environ)
    env["PATH"] = build_path_without(missing_binary, farm_dir)
    result = _run_lib_helper_on_command("_lib_gh_body_source_paths", command, tmp_path, env=env)
    assert result.returncode == expected_status, result.stderr
    if expected_status == 0:
        assert result.stdout == "/x\n"
    else:
        assert result.stdout == ""


@pytest.mark.parametrize(
    "command",
    ["git commit -F /x", 'git commit -m "a\nb" -F /x'],
    ids=["single-line", "xargs-failing"],
)
def test_git_commit_message_source_paths_fails_when_awk_is_missing(command: str, tmp_path: Path) -> None:
    """A missing awk must read as a failure, not as a command with no message-source files."""
    farm_dir = tmp_path / "path-without-awk"
    farm_dir.mkdir()
    env = dict(os.environ)
    env["PATH"] = build_path_without("awk", farm_dir)
    result = _run_lib_helper_on_command("_lib_git_commit_message_source_paths", command, tmp_path, env=env)
    assert result.returncode == 127, result.stderr
    assert result.stdout == ""


def _copy_lib_without_tokenizer(destination: Path) -> Path:
    """Copy _lib.sh and the files it sources into destination, leaving out split-command-words.py."""
    destination.mkdir()
    for name in ("_lib.sh", "_config.sh", "config-keys.psv"):
        shutil.copy(HOOKS_DIR / name, destination / name)
    return destination / "_lib.sh"


def _copy_lib_with_tokenizer(destination: Path) -> Path:
    """Copy _lib.sh, the files it sources, and split-command-words.py into destination."""
    lib_copy = _copy_lib_without_tokenizer(destination)
    shutil.copy(_SPLITTER, destination / _SPLITTER.name)
    return lib_copy


def _linked_hooks_dir(tmp_path: Path, *, absolute_targets: bool, target_has_tokenizer: bool) -> tuple[Path, Path]:
    """Build a stow-style directory whose _lib.sh, _config.sh, and config-keys.psv are links with no tokenizer link.

    Returns (link directory, real directory the links point into).
    """
    real_dir = tmp_path / "real-hooks"
    if target_has_tokenizer:
        _copy_lib_with_tokenizer(real_dir)
    else:
        _copy_lib_without_tokenizer(real_dir)
    link_dir = tmp_path / "linked-hooks"
    link_dir.mkdir()
    for name in ("_lib.sh", "_config.sh", "config-keys.psv"):
        target = real_dir / name if absolute_targets else Path("..") / real_dir.name / name
        (link_dir / name).symlink_to(target)
    return link_dir, real_dir


def _tokenizer_path_printed_by(lib_path: Path) -> str:
    result = subprocess.run(
        ["bash", "-c", f". {shlex.quote(str(lib_path))}; _lib_command_words_tokenizer_path"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def test_failure_clause_names_install_when_lib_is_a_real_copy_without_the_tokenizer(tmp_path: Path) -> None:
    lib_copy = _copy_lib_without_tokenizer(tmp_path / "hooks-copy")
    clause = subprocess.run(
        ["bash", "-c", f". {shlex.quote(str(lib_copy))}; _lib_command_words_failure_clause 2"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert "./install.sh" in clause
    assert "claude-config checkout" in clause
    assert "stop and ask" in clause


def test_command_words_with_unlinked_tokenizer_returns_nonzero_for_an_xargs_failing_command(tmp_path: Path) -> None:
    lib_copy = _copy_lib_without_tokenizer(tmp_path / "hooks-copy")
    result = _run_lib_helper_on_command(
        "_lib_command_words", 'git commit -m "a\nb" -F /x', tmp_path, lib_path=lib_copy
    )
    assert result.returncode != 0
    assert result.stdout == ""


def test_command_words_single_line_still_works_with_unlinked_tokenizer(tmp_path: Path) -> None:
    lib_copy = _copy_lib_without_tokenizer(tmp_path / "hooks-copy")
    result = _run_lib_helper_on_command("_lib_command_words", "git commit -F /x", tmp_path, lib_path=lib_copy)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "git\ncommit\n-F\n/x\n"


@pytest.mark.parametrize("absolute_targets", [False, True], ids=["relative-link-target", "absolute-link-target"])
def test_tokenizer_path_readlink_fallback_finds_the_tokenizer_beside_the_link_target(
    absolute_targets: bool, tmp_path: Path
) -> None:
    """With _lib.sh a stow-style symlink and no tokenizer link beside it, the link target's directory supplies it."""
    link_dir, real_dir = _linked_hooks_dir(tmp_path, absolute_targets=absolute_targets, target_has_tokenizer=True)
    assert not (link_dir / _SPLITTER.name).exists(), "precondition: no tokenizer link beside the link"

    result = _run_lib_helper_on_command(
        "_lib_command_words", 'git commit -m "a\nb" -F /x', tmp_path, lib_path=link_dir / "_lib.sh"
    )
    printed_path = _tokenizer_path_printed_by(link_dir / "_lib.sh")

    assert result.returncode == 0, result.stderr
    assert "/x" in result.stdout.split("\n")
    assert printed_path != str(link_dir / _SPLITTER.name)
    assert Path(printed_path).resolve() == (real_dir / _SPLITTER.name).resolve()


def test_tokenizer_path_is_the_unreadable_sibling_when_the_link_target_lacks_the_tokenizer(tmp_path: Path) -> None:
    link_dir, _real_dir = _linked_hooks_dir(tmp_path, absolute_targets=True, target_has_tokenizer=False)

    printed_path = _tokenizer_path_printed_by(link_dir / "_lib.sh")

    assert printed_path == str(link_dir / _SPLITTER.name)
    assert not Path(printed_path).exists()


def test_tokenizer_path_for_the_in_tree_lib_resolves_to_the_tokenizer_in_the_hooks_directory() -> None:
    result = _run_lib_call("_lib_command_words_tokenizer_path", env=dict(os.environ))
    assert Path(result.stdout.strip()).resolve() == _SPLITTER.resolve()


@pytest.mark.parametrize(
    ("status", "expected_fragment", "absent_fragment"),
    [
        ("124", "killed (exit 124)", "tool in the extraction step"),
        ("137", "killed (exit 137)", "tool in the extraction step"),
        ("3", "tokenizer could split it", "tool in the extraction step"),
        ("3", "over 2 MiB", "tool in the extraction step"),
        ("2", "tool in the extraction step failed (exit 2)", "killed"),
        ("127", "tool in the extraction step failed (exit 127)", "killed"),
        (str(_NO_WORDS_STATUS), "found no command words to check", "tool in the extraction step"),
        (str(_NO_WORDS_STATUS), "holds only comments", "killed"),
        ("1", "tool in the extraction step failed (exit 1)", "killed"),
    ],
)
def test_failure_clause_wording_by_status(status: str, expected_fragment: str, absent_fragment: str) -> None:
    result = _run_lib_call(f"_lib_command_words_failure_clause {status}", env=dict(os.environ))
    assert expected_fragment in result.stdout
    assert absent_fragment not in result.stdout


def test_failure_clause_names_every_step_of_the_extraction_for_a_tool_fault() -> None:
    result = _run_lib_call("_lib_command_words_failure_clause 127", env=dict(os.environ))
    for step in ("python3", "awk", "temp-file creation"):
        assert step in result.stdout
    assert "tokenized" not in result.stdout


def test_deny_reason_for_status_3_names_surface_flags_clause_and_workaround() -> None:
    result = _run_lib_call(
        "_lib_command_words_deny_reason 'gh pr create' '--body-file/-F' 3", env=dict(os.environ)
    )
    reason = result.stdout
    assert "could not be tokenized" in reason
    assert "gh pr create" in reason
    assert "--body-file/-F" in reason
    assert "tokenizer could split it" in reason
    assert "earlier Bash call" in reason


@pytest.mark.parametrize("status", ["2", "127", "124"])
def test_deny_reason_for_a_non_tokenizer_status_does_not_claim_a_tokenizing_failure(status: str) -> None:
    result = _run_lib_call(
        f"_lib_command_words_deny_reason 'git commit' '-F' {status}", env=dict(os.environ)
    )
    assert "git commit" in result.stdout
    assert "tokenized" not in result.stdout
    assert "earlier Bash call" in result.stdout


def test_deny_reason_for_the_no_words_status_does_not_blame_a_tool_or_offer_the_file_workaround() -> None:
    result = _run_lib_call(
        f"_lib_command_words_deny_reason 'git commit' '-F' {_NO_WORDS_STATUS}", env=dict(os.environ)
    )
    assert "git commit" in result.stdout
    assert "found no command words to check" in result.stdout
    assert "a tool in the extraction step failed" not in result.stdout
    assert "tokenized" not in result.stdout
    assert "earlier Bash call" not in result.stdout


def test_deny_reason_omits_the_workaround_when_the_tokenizer_is_not_linked(tmp_path: Path) -> None:
    lib_copy = _copy_lib_without_tokenizer(tmp_path / "hooks-copy")
    result = subprocess.run(
        ["bash", "-c", f". {shlex.quote(str(lib_copy))}; _lib_command_words_deny_reason 'git commit' '-F' 2"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "./install.sh" in result.stdout
    assert "stop and ask" in result.stdout
    assert "earlier Bash call" not in result.stdout
    assert "split the command" not in result.stdout


def test_deny_reason_called_with_missing_arguments_still_produces_a_reason_under_nounset() -> None:
    result = _run_lib_call("set -u; _lib_command_words_deny_reason", env=dict(os.environ))
    assert result.returncode == 0, result.stderr
    assert result.stdout


def test_deny_reason_and_hook_stderr_do_not_echo_the_interpreters_stderr(tmp_path: Path) -> None:
    """The python3 shim writes a marker to stderr and fails; the marker must reach neither the reason nor the hook's stderr.

    Dropping `2>/dev/null` from the tokenizer call in _lib_command_words surfaces the marker on stderr.
    """
    marker = "DISTINCTIVE-MARKER-9f3a"
    shim_dir = _python3_shim_dir(tmp_path, f"echo {marker} >&2\nexit 1")
    script = (
        '_lib_command_words "$command" > /dev/null; status=$?; '
        "_lib_command_words_deny_reason 'git commit' '-F' \"$status\""
    )

    result = _run_lib_script_on_command(script, _XARGS_FAILING_BODY_COMMAND, tmp_path, env=_env_with_path_prefix(shim_dir))

    assert result.returncode == 0, result.stderr
    assert "exit 1" in result.stdout, "precondition: the shim's failure reached the reason builder"
    assert marker not in result.stdout
    assert marker not in result.stderr


@pytest.mark.timing
def test_command_words_python3_hang_is_capped_and_returns_the_killed_status(tmp_path: Path) -> None:
    """A python3 that outlasts the cap yields the cap-kill status instead of hanging the hook.

    The shim sleeps past the cap and only then execs the real python3, which would print the words;
    a working cap kills the shim first, so an uncapped call fails this on the status and the empty stdout.
    """
    real_python3 = shutil.which("python3")
    assert real_python3, "python3 not found in PATH"
    if not shutil.which("timeout") and not shutil.which("gtimeout"):
        pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")

    stub_dir = tmp_path / "stub-bin-python3"
    stub_dir.mkdir()
    write_scaled_timeout_shim(stub_dir)
    stub_python3 = stub_dir / "python3"
    stub_python3.write_text(f'#!/bin/bash\nsleep {scaled_shim_sleep(10)}\nexec {real_python3} "$@"\n')
    stub_python3.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{stub_dir}:{os.environ['PATH']}"
    with assert_cap_engaged(stub_dir, production_cap=5, command="python3"):
        result = _run_lib_helper_on_command("_lib_command_words", "git commit -m it's -F /x", tmp_path, env=env)

    assert result.returncode in (124, 137, 143), result.stderr
    assert result.stdout == ""
