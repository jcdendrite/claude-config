"""Tests for scripts/venv-run.sh.

Runs a copy of the wrapper as a subprocess against a real git repository and a
linked worktree four levels deep, with one case per exit branch.

Fixture defaults, applied by `_make_fixture`:
- The main checkout sits under a directory whose name contains a space.
- The wrapper is committed at `scripts/venv-run.sh`, so the linked worktree
  checks out its own copy at the committed mode.
- `core.hooksPath` points at an empty directory, so the contributor's global
  hooks do not run on the fixture's commit.
- The main checkout's `.git/info/exclude` lists `.claude/worktrees/` and `.venv/`.
- `<main>/.venv/bin/python3` is a symlink to a stub that prints `$0`, its
  working directory and each argument, one per line.

Isolation: conftest's `_clean_env` removes the four repository-selection variables
(`GIT_DIR`, `GIT_WORK_TREE`, `GIT_COMMON_DIR`, `GIT_INDEX_FILE`) and the DIRENV
and gh-credential variables. Every git call in this file goes through conftest's `_git`,
which applies it.
"""
from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import NamedTuple

import pytest

from .conftest import _clean_env, _git

_SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "venv-run.sh"

_WORKTREE_BRANCH = "T-1/topic-slug"

_ARGUMENT_WITH_SPACE = "sentinel arg"

_STUB_EXIT_CODE = 3

_PROCESS_TIMEOUT_SECONDS = 30

_REPORT_STUB_SOURCE = textwrap.dedent("""\
    #!/usr/bin/env bash
    printf '%s\\n' "$0"
    pwd -P
    printf '%s\\n' "$@"
""")

_EXIT_STUB_SOURCE = f"#!/usr/bin/env bash\nexit {_STUB_EXIT_CODE}\n"

_PID_STUB_SOURCE = "#!/usr/bin/env bash\nprintf '%s\\n' \"$$\"\n"


class _Fixture(NamedTuple):
    main: Path
    worktree: Path
    bin_dir: Path


class _StubReport(NamedTuple):
    invoked_as: Path
    working_directory: Path
    arguments: list[str]


def _write_executable(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    path.chmod(0o755)


def _make_fixture(tmp_path: Path) -> _Fixture:
    base = tmp_path.resolve() / "dir with space"
    main = base / "main"
    main.mkdir(parents=True)
    _git(main, "init", "-q", "-b", "main")
    _git(main, "config", "user.email", "t@t.com")
    _git(main, "config", "user.name", "t")
    hooks_dir = base / "hooks"
    hooks_dir.mkdir()
    _git(main, "config", "core.hooksPath", str(hooks_dir))
    with (main / ".git" / "info" / "exclude").open("a") as exclude_file:
        exclude_file.write(".claude/worktrees/\n.venv/\n")
    (main / "file.txt").write_text("init\n")
    _git(main, "add", "file.txt")
    _git(main, "commit", "-q", "-m", "init")

    # shutil.copy keeps the source mode; the copy is deliberately not chmod'ed, so a lost
    # exec bit on the real script surfaces as a failed invocation.
    (main / "scripts").mkdir()
    shutil.copy(_SCRIPT, main / "scripts" / _SCRIPT.name)
    _git(main, "add", f"scripts/{_SCRIPT.name}")
    _git(main, "commit", "-q", "-m", "add the wrapper")

    worktree = main / ".claude" / "worktrees" / _WORKTREE_BRANCH
    _git(main, "worktree", "add", "-q", "-b", _WORKTREE_BRANCH, str(worktree))

    stubs_dir = main / ".venv" / "stubs"
    bin_dir = main / ".venv" / "bin"
    _write_executable(stubs_dir / "report", _REPORT_STUB_SOURCE)
    _write_executable(stubs_dir / "exit-with-status", _EXIT_STUB_SOURCE)
    _write_executable(stubs_dir / "print-pid", _PID_STUB_SOURCE)
    bin_dir.mkdir()
    (bin_dir / "python3").symlink_to("../stubs/report")
    (bin_dir / "exit-with-status").symlink_to("../stubs/exit-with-status")
    (bin_dir / "print-pid").symlink_to("../stubs/print-pid")
    (bin_dir / "sub").mkdir()
    (bin_dir / "sub" / "tool").symlink_to("../../stubs/report")
    return _Fixture(main, worktree, bin_dir)


@pytest.fixture()
def venv_checkout(tmp_path: Path) -> _Fixture:
    return _make_fixture(tmp_path)


def _run_wrapper(
    wrapper: str | Path, *arguments: str, cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the wrapper directly, not through `bash`, so a lost exec bit fails the invocation."""
    return subprocess.run(
        [str(wrapper), *arguments],
        cwd=cwd,
        env=_clean_env() if env is None else env,
        capture_output=True,
        text=True,
    )


def _parse_stub_report(stdout: str) -> _StubReport:
    invoked_as, working_directory, *arguments = stdout.splitlines()
    return _StubReport(Path(invoked_as), Path(working_directory), arguments)


def _assert_wrapper_failure(
    result: subprocess.CompletedProcess[str], *, returncode: int, fragments: tuple[str, ...]
) -> None:
    assert result.returncode == returncode, result.stderr
    assert result.stdout == ""
    stderr_lines = result.stderr.splitlines()
    assert len(stderr_lines) == 1, result.stderr
    # startswith, not `in`: bash's own error line `scripts/venv-run.sh: line N:` contains the substring.
    assert stderr_lines[0].startswith("venv-run.sh: "), result.stderr
    for fragment in fragments:
        assert fragment in result.stderr
    assert _ARGUMENT_WITH_SPACE not in result.stderr
    # A pasteable deletion in a failure message is a hazard.
    # The `\b` boundaries keep words like "form" passing.
    assert re.search(r"\brm\b", result.stderr) is None, result.stderr


def _copy_wrapper_outside_any_repository(
    tmp_path: Path, parent_name: str = "outside"
) -> tuple[Path, Path, dict[str, str]]:
    """Return the scripts directory, the wrapper copied into it, and an env that stops git's upward search above it."""
    outside_scripts_dir = tmp_path.resolve() / parent_name / "scripts"
    outside_scripts_dir.mkdir(parents=True)
    outside_wrapper = outside_scripts_dir / "venv-run.sh"
    shutil.copy(_SCRIPT, outside_wrapper)
    return outside_scripts_dir, outside_wrapper, _clean_env(GIT_CEILING_DIRECTORIES=str(tmp_path.resolve()))


class TestSuccess:
    def test_runs_the_main_venv_tool_from_a_four_level_deep_linked_worktree(self, venv_checkout):
        """GH-1252: the documented command must work from a ticketed worktree, which sits four levels deep."""
        result = _run_wrapper(
            "scripts/venv-run.sh", "python3", "a", _ARGUMENT_WITH_SPACE, cwd=venv_checkout.worktree
        )

        assert result.returncode == 0, result.stderr
        report = _parse_stub_report(result.stdout)
        assert report.invoked_as.name == "python3"
        assert report.invoked_as.parent.resolve() == venv_checkout.bin_dir.resolve()
        assert report.working_directory == venv_checkout.worktree.resolve()
        assert report.arguments == ["a", _ARGUMENT_WITH_SPACE]

    def test_runs_the_venv_tool_from_the_main_checkout(self, venv_checkout):
        result = _run_wrapper("scripts/venv-run.sh", "python3", "a", cwd=venv_checkout.main)

        assert result.returncode == 0, result.stderr
        report = _parse_stub_report(result.stdout)
        assert report.invoked_as.parent.resolve() == venv_checkout.bin_dir.resolve()
        assert report.working_directory == venv_checkout.main.resolve()
        assert report.arguments == ["a"]

    def test_resolves_from_the_script_location_when_invoked_by_absolute_path_from_outside_the_repo(
        self, venv_checkout, tmp_path
    ):
        outside_directory = tmp_path.resolve() / "elsewhere"
        outside_directory.mkdir()

        result = _run_wrapper(
            venv_checkout.main / "scripts" / "venv-run.sh", "python3", "a", cwd=outside_directory
        )

        assert result.returncode == 0, result.stderr
        report = _parse_stub_report(result.stdout)
        assert report.invoked_as.parent.resolve() == venv_checkout.bin_dir.resolve()
        assert report.working_directory == outside_directory

    def test_propagates_the_tool_exit_status(self, venv_checkout):
        result = _run_wrapper("scripts/venv-run.sh", "exit-with-status", cwd=venv_checkout.main)

        assert result.returncode == _STUB_EXIT_CODE
        assert result.stdout == ""
        assert result.stderr == ""

    def test_replaces_the_wrapper_process_with_the_tool(self, venv_checkout):
        """Without `exec`, a signal sent to the wrapper's pid (e.g. by `timeout`) would not reach the tool."""
        wrapper_process = subprocess.Popen(
            ["scripts/venv-run.sh", "print-pid"],
            cwd=venv_checkout.main,
            env=_clean_env(),
            stdout=subprocess.PIPE,
            text=True,
        )
        stdout, _ = wrapper_process.communicate(timeout=_PROCESS_TIMEOUT_SECONDS)

        assert wrapper_process.returncode == 0
        assert int(stdout) == wrapper_process.pid

    def test_runs_a_real_venv_python_so_the_prefix_is_the_main_checkout_venv(self, venv_checkout):
        """Python derives its prefix from the path it was invoked by, so the wrapper must not resolve the venv symlink."""
        main_venv = venv_checkout.main / ".venv"
        shutil.rmtree(main_venv)
        venv_build = subprocess.run(
            [sys.executable, "-m", "venv", "--without-pip", str(main_venv)],
            env=_clean_env(),
            capture_output=True,
            text=True,
            timeout=_PROCESS_TIMEOUT_SECONDS,
        )
        assert venv_build.returncode == 0, venv_build.stderr

        result = _run_wrapper(
            "scripts/venv-run.sh",
            "python3",
            "-c",
            "import sys; print(sys.prefix)",
            cwd=venv_checkout.worktree,
        )

        assert result.returncode == 0, result.stderr
        assert Path(result.stdout.strip()) == main_venv


class TestUsageErrors:
    @pytest.mark.parametrize(
        "arguments",
        [
            pytest.param((), id="no-arguments"),
            pytest.param(("", _ARGUMENT_WITH_SPACE), id="empty-tool-name"),
        ],
    )
    def test_rejects_a_missing_tool_with_exit_2_before_resolving_the_main_checkout(self, tmp_path, arguments):
        """The wrapper sits outside any repository, so reaching the git lookup would exit 127 instead."""
        _, outside_wrapper, env = _copy_wrapper_outside_any_repository(tmp_path)

        result = _run_wrapper(outside_wrapper, *arguments, cwd=tmp_path, env=env)

        _assert_wrapper_failure(result, returncode=2, fragments=("usage",))

    @pytest.mark.parametrize(
        "tool_name",
        [
            pytest.param("../bin/python3", id="parent-segment-to-an-existing-tool"),
            pytest.param("sub/tool", id="inner-slash-to-an-existing-tool"),
        ],
    )
    def test_rejects_a_tool_name_containing_a_slash_with_exit_2_though_the_target_exists(
        self, venv_checkout, tool_name
    ):
        assert (venv_checkout.bin_dir / tool_name).is_file()

        result = _run_wrapper(
            "scripts/venv-run.sh", tool_name, _ARGUMENT_WITH_SPACE, cwd=venv_checkout.main
        )

        _assert_wrapper_failure(result, returncode=2, fragments=("bare name",))


def _prepare_tool_present_only_on_path(bin_dir: Path) -> None:
    assert shutil.which("git"), "the on-PATH case needs a git on PATH"
    assert not (bin_dir / "git").exists()


def _prepare_mode_644_file(bin_dir: Path) -> None:
    not_executable = bin_dir / "not-executable"
    not_executable.write_text("#!/usr/bin/env bash\n")
    not_executable.chmod(0o644)


def _prepare_directory(bin_dir: Path) -> None:
    (bin_dir / "a-directory").mkdir()


class TestResolutionFailures:
    def test_exits_127_naming_the_script_directory_when_it_is_outside_any_repository(
        self, venv_checkout, tmp_path
    ):
        outside_scripts_dir, outside_wrapper, env = _copy_wrapper_outside_any_repository(tmp_path)
        # Cwd is inside the main checkout, so a cwd-anchored lookup would succeed.
        result = _run_wrapper(outside_wrapper, "python3", _ARGUMENT_WITH_SPACE, cwd=venv_checkout.main, env=env)

        _assert_wrapper_failure(
            result,
            returncode=127,
            fragments=(
                "git", str(outside_scripts_dir),
                f"git -C {outside_scripts_dir} rev-parse --path-format=absolute --git-common-dir",
            ),
        )

    def test_rerun_command_is_shell_quoted_when_the_script_directory_contains_a_space(
        self, venv_checkout, tmp_path
    ):
        outside_scripts_dir, outside_wrapper, env = _copy_wrapper_outside_any_repository(
            tmp_path, parent_name="outside dir"
        )

        result = _run_wrapper(outside_wrapper, "python3", cwd=venv_checkout.main, env=env)

        _assert_wrapper_failure(result, returncode=127, fragments=("git",))
        _, _, rerun_command = result.stderr.strip().partition("rerun to see why: ")
        assert shlex.split(rerun_command) == [
            "git", "-C", str(outside_scripts_dir), "rev-parse", "--path-format=absolute", "--git-common-dir",
        ]

    def test_exits_127_naming_the_main_checkout_when_it_has_no_venv_despite_a_worktree_local_one(
        self, venv_checkout
    ):
        _write_executable(venv_checkout.worktree / ".venv" / "bin" / "python3", _REPORT_STUB_SOURCE)
        shutil.rmtree(venv_checkout.main / ".venv")

        result = _run_wrapper(
            "scripts/venv-run.sh", "python3", _ARGUMENT_WITH_SPACE, cwd=venv_checkout.worktree
        )

        _assert_wrapper_failure(
            result,
            returncode=127,
            fragments=("no .venv in the main checkout", str(venv_checkout.main), "install-dev.sh"),
        )
        # The tool is not the missing item; naming it would send the user to the wrong remedy.
        assert "python3" not in result.stderr

    @pytest.mark.parametrize(
        ("tool_name", "prepare_tool"),
        [
            pytest.param("git", _prepare_tool_present_only_on_path, id="on-path-but-not-in-venv"),
            pytest.param("not-executable", _prepare_mode_644_file, id="mode-644-file"),
            pytest.param("a-directory", _prepare_directory, id="directory"),
        ],
    )
    def test_exits_127_when_the_tool_is_not_an_executable_regular_file_in_venv_bin(
        self, venv_checkout, tool_name, prepare_tool
    ):
        prepare_tool(venv_checkout.bin_dir)

        result = _run_wrapper(
            "scripts/venv-run.sh", tool_name, _ARGUMENT_WITH_SPACE, cwd=venv_checkout.main
        )

        _assert_wrapper_failure(
            result,
            returncode=127,
            fragments=(tool_name, str(venv_checkout.bin_dir), "if it is a contributor tool", "install-dev.sh"),
        )
