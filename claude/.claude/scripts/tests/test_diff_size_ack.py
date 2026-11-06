"""Tests for diff-size-ack.sh, one case per branch. The count's input matrix is in hooks/tests/test_lib_diff_size.py.

Every repository is built against a bare `origin` whose default branch is `trunk`. Every subprocess gets one explicit
CLAUDE_CONFIG_DIR under `tmp_path`, except the case that pins the unset behavior, and every latch-path assertion
derives from it.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from helpers import (
    CLAUDE_DIR,
    _run_git,
    bash_input,
    git_toplevel,
    installed_locale_where,
    path_with_failing_tool_shim,
    reviewer_round_state_key,
    run_hook,
)

# claude/.claude/ itself, so `hooks.tests...` below resolves, as in test_review_ledger_lib.py.
sys.path.insert(0, str(CLAUDE_DIR))
from hooks.tests._lib_sh_helpers import (  # noqa: E402
    DIFF_SIZE_GATE_HOOK,
    HOSTILE_BASE_NAME_PARAMS,
    HOSTILE_BIDI_AND_TAG_CHARACTERS,
    _ack_env,
    _assert_hook_allows,
    _clone_with_staged_lines,
    _config_dir,
    _default_branch_arm_base,
    _git_env_recording_shim,
    _latch_path,
    _recorded_values,
    _run_diff_size_ack,
    isolated_git_config,  # noqa: F401 -- an autouse fixture that applies to every test here
)

_THRESHOLD_LINES = 400
_SECONDS_PER_DAY = 24 * 60 * 60


def _latch_directory(tmp_path: Path) -> Path:
    return _config_dir(tmp_path) / ".diff-size-prompt-latch.d"


def _entries(directory: Path) -> list[str]:
    return sorted(entry.name for entry in directory.iterdir()) if directory.exists() else []


class TestArguments:
    @pytest.mark.parametrize("arguments", [("--help",), ("some-path",), ("",)], ids=["flag", "path", "empty string"])
    def test_any_argument_exits_2_and_writes_nothing(self, tmp_path: Path, arguments: tuple[str, ...]) -> None:
        clone = _clone_with_staged_lines(tmp_path, _THRESHOLD_LINES)

        result = _run_diff_size_ack(clone, _ack_env(tmp_path), *arguments)

        assert result.returncode == 2, result.stderr
        assert "usage:" in result.stderr
        assert not _latch_directory(tmp_path).exists()


class TestRecordingTheAcknowledgment:
    def test_at_the_threshold_it_exits_0_and_writes_one_line_with_a_time_a_count_and_a_base(
        self, tmp_path: Path
    ) -> None:
        clone = _clone_with_staged_lines(tmp_path, _THRESHOLD_LINES)

        result = _run_diff_size_ack(clone, _ack_env(tmp_path))

        assert result.returncode == 0, result.stderr
        # The path the test derives is the path the ack wrote.
        latch_file = _latch_path(clone, tmp_path)
        assert _entries(_latch_directory(tmp_path)) == [latch_file.name]
        latch_lines = latch_file.read_text().splitlines()
        assert len(latch_lines) == 1
        recorded_time, recorded_count, recorded_base = latch_lines[0].split()
        assert datetime.strptime(recorded_time, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC) <= datetime.now(
            UTC
        )
        assert (recorded_count, recorded_base) == (str(_THRESHOLD_LINES), "origin/trunk")

    def test_a_present_latch_exits_0_and_stays_unchanged(self, tmp_path: Path) -> None:
        clone = _clone_with_staged_lines(tmp_path, _THRESHOLD_LINES)
        latch_file = _latch_path(clone, tmp_path)
        latch_file.parent.mkdir(parents=True)
        latch_file.write_text("recorded earlier\n")

        result = _run_diff_size_ack(clone, _ack_env(tmp_path))

        assert result.returncode == 0, result.stderr
        assert latch_file.read_text() == "recorded earlier\n"
        assert "already recorded" in result.stdout

    def test_neither_success_exit_names_the_latch_path_but_both_name_the_config_directory(
        self, tmp_path: Path
    ) -> None:
        clone = _clone_with_staged_lines(tmp_path, _THRESHOLD_LINES)
        latch_path = str(_latch_path(clone, tmp_path))
        config_dir = str(_config_dir(tmp_path))

        written = _run_diff_size_ack(clone, _ack_env(tmp_path))
        already_recorded = _run_diff_size_ack(clone, _ack_env(tmp_path))

        for result in (written, already_recorded):
            assert result.returncode == 0, result.stderr
            assert config_dir in result.stdout
            assert latch_path not in result.stdout + result.stderr
        assert "already recorded" in already_recorded.stdout, "premise: the second run took the status-3 exit"
        assert "already recorded" not in written.stdout, "premise: the first run wrote the latch"

    def test_with_the_config_dir_unset_the_latch_lands_under_home_and_the_gate_then_allows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        clone = _clone_with_staged_lines(tmp_path, _THRESHOLD_LINES)
        payload = bash_input("git commit -m x", cwd=git_toplevel(clone))
        ack_shim_env, ack_log = _git_env_recording_shim(tmp_path / "ack-side", ("CLAUDE_CONFIG_DIR",))
        hook_shim_env, hook_log = _git_env_recording_shim(tmp_path / "hook-side", ("CLAUDE_CONFIG_DIR",))
        denied_before_the_ack = run_hook(DIFF_SIZE_GATE_HOOK, payload, cwd=clone, extra_env=hook_shim_env)

        result = _run_diff_size_ack(clone, _ack_env(tmp_path, ack_shim_env, pin_config_dir=False))

        assert denied_before_the_ack == "deny", "premise: the hook measures with the config dir unset"
        assert result.returncode == 0, result.stderr
        assert _recorded_values(ack_log, "CLAUDE_CONFIG_DIR") <= {""}, "premise: the ack saw the variable unset"
        assert _recorded_values(hook_log, "CLAUDE_CONFIG_DIR") <= {""}, "premise: the hook saw the variable unset"
        latch_under_home = (
            tmp_path / "home" / ".claude" / ".diff-size-prompt-latch.d" / reviewer_round_state_key(clone)
        )
        assert latch_under_home.is_file()
        _assert_hook_allows(DIFF_SIZE_GATE_HOOK, payload, clone, hook_shim_env)


class TestNothingToRecord:
    def test_below_the_threshold_exits_1_and_the_stderr_names_the_threshold(self, tmp_path: Path) -> None:
        clone = _clone_with_staged_lines(tmp_path, _THRESHOLD_LINES - 1)

        result = _run_diff_size_ack(clone, _ack_env(tmp_path))

        assert result.returncode == 1
        assert f"below the {_THRESHOLD_LINES}-line threshold" in result.stderr
        assert not _latch_directory(tmp_path).exists()

    def test_a_detached_head_exits_1_and_the_stderr_says_the_state_could_not_be_read(self, tmp_path: Path) -> None:
        clone = _clone_with_staged_lines(tmp_path, _THRESHOLD_LINES)
        _run_git(clone, "checkout", "-q", "--detach")

        result = _run_diff_size_ack(clone, _ack_env(tmp_path))

        assert result.returncode == 1
        assert "could not be read" in result.stderr
        assert "below the" not in result.stderr
        assert not _latch_directory(tmp_path).exists()


class TestWriteFailures:
    def test_a_failing_mv_exits_3_and_leaves_no_latch_and_no_temp_file(self, tmp_path: Path) -> None:
        clone = _clone_with_staged_lines(tmp_path, _THRESHOLD_LINES)
        env = _ack_env(tmp_path, {"PATH": path_with_failing_tool_shim(tmp_path / "failing-mv-bin", "mv")})

        result = _run_diff_size_ack(clone, env)

        assert result.returncode == 3, result.stderr
        assert _entries(_latch_directory(tmp_path)) == []

    def test_a_failing_mktemp_exits_3_and_names_the_temporary_file_in_stderr(self, tmp_path: Path) -> None:
        clone = _clone_with_staged_lines(tmp_path, _THRESHOLD_LINES)
        env = _ack_env(tmp_path, {"PATH": path_with_failing_tool_shim(tmp_path / "failing-mktemp-bin", "mktemp")})

        result = _run_diff_size_ack(clone, env)

        assert result.returncode == 3, result.stderr
        assert "a temporary file could not be created" in result.stderr
        assert not _latch_path(clone, tmp_path).exists()
        assert _entries(_latch_directory(tmp_path)) == []

    def test_a_file_where_the_latch_directory_belongs_exits_3_with_no_latch(self, tmp_path: Path) -> None:
        clone = _clone_with_staged_lines(tmp_path, _THRESHOLD_LINES)
        _config_dir(tmp_path).mkdir()
        _latch_directory(tmp_path).write_text("not a directory\n")

        result = _run_diff_size_ack(clone, _ack_env(tmp_path))

        assert result.returncode == 3, result.stderr
        assert "latch directory under" in result.stderr
        assert _latch_directory(tmp_path).is_file()

    def test_a_directory_at_the_latch_path_exits_3_and_stays_empty(self, tmp_path: Path) -> None:
        clone = _clone_with_staged_lines(tmp_path, _THRESHOLD_LINES)
        latch_path = _latch_path(clone, tmp_path)
        latch_path.mkdir(parents=True)

        result = _run_diff_size_ack(clone, _ack_env(tmp_path))

        assert result.returncode == 3, result.stderr
        assert str(latch_path) in result.stderr
        assert _entries(latch_path) == []

    def test_a_mktemp_that_refuses_a_call_without_a_template_does_not_stop_the_write(self, tmp_path: Path) -> None:
        # BSD mktemp needs a template; this shim fails unless an argument ends in the template's X run.
        clone = _clone_with_staged_lines(tmp_path, _THRESHOLD_LINES)
        real_mktemp = shutil.which("mktemp")
        assert real_mktemp is not None
        shim_dir = tmp_path / "template-only-mktemp-bin"
        shim_dir.mkdir()
        shim = shim_dir / "mktemp"
        shim.write_text(
            "#!/bin/bash\n"
            'for argument in "$@"; do\n'
            f'  case "$argument" in *XXXXXX) exec {real_mktemp} "$@" ;; esac\n'
            "done\n"
            "exit 1\n"
        )
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
        env = _ack_env(tmp_path, {"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"})

        result = _run_diff_size_ack(clone, env)

        assert result.returncode == 0, result.stderr
        assert _latch_path(clone, tmp_path).is_file()


class TestSweep:
    def test_a_failing_find_still_leaves_the_latch_and_exits_0(self, tmp_path: Path) -> None:
        clone = _clone_with_staged_lines(tmp_path, _THRESHOLD_LINES)
        env = _ack_env(tmp_path, {"PATH": path_with_failing_tool_shim(tmp_path / "failing-find-bin", "find")})

        result = _run_diff_size_ack(clone, env)

        assert result.returncode == 0, result.stderr
        assert _latch_path(clone, tmp_path).is_file()

    def test_it_deletes_a_latch_of_another_branch_aged_32_days_and_keeps_one_aged_29_days_and_the_new_latch(
        self, tmp_path: Path
    ) -> None:
        clone = _clone_with_staged_lines(tmp_path, _THRESHOLD_LINES)
        latch_directory = _latch_directory(tmp_path)
        latch_directory.mkdir(parents=True)
        expired_latch = latch_directory / "other-repo.expired-branch"
        unexpired_latch = latch_directory / "other-repo.unexpired-branch"
        expired_latch.write_text("old\n")
        unexpired_latch.write_text("recent\n")
        expired_file_in_config_dir = _config_dir(tmp_path) / "expired-config-dir-file"
        sibling_directory = _config_dir(tmp_path) / ".some-other-latch.d"
        sibling_directory.mkdir()
        expired_file_in_sibling_directory = sibling_directory / "expired-sibling-file"
        expired_file_in_config_dir.write_text("old\n")
        expired_file_in_sibling_directory.write_text("old\n")
        for aged_file, age_in_days in (
            (expired_latch, 32),
            (unexpired_latch, 29),
            (expired_file_in_config_dir, 32),
            (expired_file_in_sibling_directory, 32),
        ):
            aged_mtime = time.time() - age_in_days * _SECONDS_PER_DAY
            os.utime(aged_file, (aged_mtime, aged_mtime))

        result = _run_diff_size_ack(clone, _ack_env(tmp_path))

        assert result.returncode == 0, result.stderr
        assert _entries(latch_directory) == sorted([unexpired_latch.name, _latch_path(clone, tmp_path).name])
        assert expired_file_in_config_dir.is_file()
        assert expired_file_in_sibling_directory.is_file()


class TestBaseNameOnTheDefaultBranchArm:
    @pytest.mark.parametrize("hostile_name", HOSTILE_BASE_NAME_PARAMS)
    def test_a_name_that_fails_the_shape_check_is_neither_printed_nor_stored(
        self, tmp_path: Path, hostile_name: str
    ) -> None:
        clone = _clone_with_staged_lines(tmp_path, 437)
        _default_branch_arm_base(clone, hostile_name)

        result = _run_diff_size_ack(clone, _ack_env(tmp_path))

        assert result.returncode == 0, result.stderr
        assert "the base branch" in result.stdout
        assert "origin/the base branch" not in result.stdout
        assert hostile_name not in result.stdout + result.stderr
        latch_line = _latch_path(clone, tmp_path).read_text()
        assert hostile_name not in latch_line
        recorded_time, recorded_count, recorded_base = latch_line.split()
        assert (recorded_count, recorded_base) == ("437", "-")

    def test_a_clean_name_on_the_same_arm_prints_and_stores_origin_slash_name(self, tmp_path: Path) -> None:
        clone = _clone_with_staged_lines(tmp_path, 437)
        _default_branch_arm_base(clone, "clean-base")

        result = _run_diff_size_ack(clone, _ack_env(tmp_path))

        assert result.returncode == 0, result.stderr
        assert "origin/clean-base" in result.stdout
        assert "the base branch" not in result.stdout
        assert _latch_path(clone, tmp_path).read_text().split()[2] == "origin/clean-base"


def _contains_bidi_control_or_tag_character(text: str) -> bool:
    return any(
        0x200E <= ord(character) <= 0x200F
        or 0x202A <= ord(character) <= 0x202E
        or 0x2066 <= ord(character) <= 0x2069
        or 0xE0000 <= ord(character) <= 0xE007F
        for character in text
    )


def _utf8_locale_where_printf_q_neutralizes_bidi_and_tag_characters() -> str:
    # $1 holds every hostile code point; a locale qualifies only when none appears raw in `printf %q "$1"`.
    return installed_locale_where(
        'quoted="$(printf %q "$1")"; for ((i = 0; i < ${#1}; i++)); do [[ "$quoted" != *"${1:i:1}"* ]] || exit 1; done',
        "".join(HOSTILE_BIDI_AND_TAG_CHARACTERS),
        absent_message="no installed UTF-8 locale makes printf %q neutralize every bidi control and tag character",
    )


def _set_hostile_stack_key_value(clone: Path, hostile_text: str) -> str:
    _run_git(clone, "config", "branch.feature.gh-merge-base", hostile_text)
    return "feature"


def _check_out_hostile_branch_with_unusable_stack_key(clone: Path, hostile_text: str) -> str:
    hostile_branch = f"feat-{hostile_text}"
    _run_git(clone, "checkout", "-q", "-b", hostile_branch)
    # origin/trunk starts with `origin/`, so the key is set but unusable, and the stderr line prints the %q-quoted branch.
    _run_git(clone, "config", f"branch.{hostile_branch}.gh-merge-base", "origin/trunk")
    return hostile_branch


class TestHostileTextInTheStderrLine:
    @pytest.mark.parametrize("locale_kind", ["C", "UTF-8 locale where printf %q neutralizes bidi and tag characters"])
    @pytest.mark.parametrize(
        "hostile_text",
        [
            pytest.param("a" + hostile_character + "b", id=character_id)
            for character_id, hostile_character in zip(
                ("bidi control", "tag character", "tag letter"), HOSTILE_BIDI_AND_TAG_CHARACTERS, strict=True
            )
        ],
    )
    @pytest.mark.parametrize(
        "make_unusable",
        [
            pytest.param(_set_hostile_stack_key_value, id="stack key value"),
            pytest.param(_check_out_hostile_branch_with_unusable_stack_key, id="branch name"),
        ],
    )
    def test_neither_raw_bidi_nor_tag_code_point_reaches_stdout_or_stderr(
        self, tmp_path: Path, make_unusable, hostile_text: str, locale_kind: str
    ) -> None:
        locale = "C" if locale_kind == "C" else _utf8_locale_where_printf_q_neutralizes_bidi_and_tag_characters()
        clone = _clone_with_staged_lines(tmp_path, _THRESHOLD_LINES)
        make_unusable(clone, hostile_text)
        shim_env, locale_log = _git_env_recording_shim(tmp_path, ("LC_ALL",))

        result = _run_diff_size_ack(clone, _ack_env(tmp_path, {**shim_env, "LC_ALL": locale}))

        assert _recorded_values(locale_log, "LC_ALL") == {locale}, "premise: LC_ALL took effect in the ack's environment"
        assert result.returncode == 1
        assert "cannot be used as the stack base" in result.stderr
        assert not _latch_directory(tmp_path).exists()
        assert not _contains_bidi_control_or_tag_character(result.stdout + result.stderr)
