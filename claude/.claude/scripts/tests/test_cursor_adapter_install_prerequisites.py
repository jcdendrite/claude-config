"""cursor-adapter.py prerequisites and the contract, the probes, the run-mode line and install's own prerequisite check."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import CLAUDE_DIR

from . import _cursor_install_helpers as install_helpers
from ._cursor_install_helpers import (
    HOOK_ENV_SURFACE,
    ORIGINAL_HOOKS,
    RECORDED_AFTER_PYTHON,
    SURFACES,
    World,
    find_entry,
    hooks_document,
    mutate_remove,
    replace_tool,
    run_surface,
    save_hooks,
)

sys.path.insert(0, str(CLAUDE_DIR))
from hooks.tests._cursor_payloads import (  # noqa: E402 (the import needs the sys.path entry above)
    ADAPTER_DIR,
    wrapper_name,
    write_sh_script,
)

# The fixtures live once in the helper module. An import would trip F811 on every test parameter that names one.
world = install_helpers.world
fast_world = install_helpers.fast_world


# --- prerequisites and the contract ---


class TestPrerequisitesAndContract:
    def _stub_python(self, world: World, version: tuple[int, int]) -> None:
        (world.bin_dir / "python3").unlink()
        reported = json.dumps([str(world.bin_dir / "python3"), "/p", "/p"])
        floor_program = world.adapter.PROBE_PYTHON_PROGRAM
        floor_output = f"{version[0]} {version[1]}"
        write_sh_script(
            world.bin_dir / "python3",
            f"case \"$*\" in *'sys.executable'*) echo '{reported}' ;; *'{floor_program}'*) echo '{floor_output}' ;; esac\n",
        )

    def test_the_refusal_renders_the_adapters_floor(self, world: World) -> None:
        # The constant's own value is tied to `_config.sh` in the hooks tests' floor parity class.
        major, minor = world.adapter.PYTHON_FLOOR
        text = world.adapter.floor_text("python3", "/p", world.adapter.PYTHON_FLOOR, "below", (major, minor - 1))
        assert text.endswith(f"below {major}.{minor} or later.")

    # Each version is derived from the floor, so a floor change moves the rows with it and they keep pinning the boundary.
    @pytest.mark.parametrize(
        "version_for",
        [lambda major, minor: (major, minor - 1), lambda major, minor: (major - 1, 99), lambda major, minor: (major, 9)],
        ids=["one-below", "major-below", "string-order"],
    )
    def test_a_recorded_python_below_the_floor_is_refused_with_its_path_and_version_and_nothing_is_written(
        self, fast_world: World, version_for
    ) -> None:
        world = fast_world
        major, minor = world.adapter.PYTHON_FLOOR
        assert minor > 10, "the string-order row needs a floor minor with two digits"
        version = version_for(major, minor)
        self._stub_python(world, version)
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert str(world.bin_dir / "python3") in result.err
        assert f"{version[0]}.{version[1]}" in result.err
        assert f"{major}.{minor} or later" in result.err
        assert world.snapshot() == before

    @pytest.mark.parametrize(
        "version_for",
        [lambda major, minor: (major, minor + 1), lambda major, minor: (major, 100), lambda major, minor: (major + 1, 0)],
        ids=["one-above", "string-order", "major-above"],
    )
    def test_a_recorded_python_above_the_floor_is_accepted(self, fast_world: World, version_for) -> None:
        world = fast_world
        major, minor = world.adapter.PYTHON_FLOOR
        self._stub_python(world, version_for(major, minor))
        result = world.install()
        assert result.status == 0, result.err

    @pytest.mark.parametrize("output", ["", "garbage", "3", "3 14 1", "a b", "1234567890 1"], ids=repr)
    def test_a_python_probe_without_exactly_two_digit_fields_gives_the_no_version_refusal(
        self, fast_world: World, output: str
    ) -> None:
        world = fast_world
        (world.bin_dir / "python3").unlink()
        reported = json.dumps([str(world.bin_dir / "python3"), "/p", "/p"])
        floor_program = world.adapter.PROBE_PYTHON_PROGRAM
        write_sh_script(
            world.bin_dir / "python3",
            f"case \"$*\" in *'sys.executable'*) echo '{reported}' ;; *'{floor_program}'*) printf '%s' '{output}' ;; esac\n",
        )
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert "printed no version" in result.err
        assert world.snapshot() == before

    def test_the_bare_name_python_in_another_directory_below_the_floor_is_refused_with_that_arm(self, fast_world: World) -> None:
        world = fast_world
        real = world.tmp_path / "real-python"
        real.mkdir()
        (real / "python3-real").symlink_to(os.path.realpath(sys.executable))
        (world.bin_dir / "python3").unlink()
        reported = json.dumps([str(real / "python3-real"), "/p", "/p"])
        floor_program = world.adapter.PROBE_PYTHON_PROGRAM
        write_sh_script(
            world.bin_dir / "python3",
            f"case \"$*\" in *'sys.executable'*) echo '{reported}' ;; *'{floor_program}'*) echo '3 13' ;; esac\n",
        )
        before = world.snapshot()
        # The recorded interpreter lives in `real`, which holds no file named python3, so the hooks run the next one.

        result = world.install()

        assert result.status == 1
        assert str(real) in result.err
        assert "holds none" in result.err
        assert world.snapshot() == before

    def test_the_bare_name_python_sharing_the_recorded_directory_names_the_first_fix(self, fast_world: World) -> None:
        world = fast_world
        directory = world.tmp_path / "shared-python"
        directory.mkdir()
        (directory / "python3-real").symlink_to(os.path.realpath(sys.executable))
        reported = json.dumps([str(directory / "python3-real"), "/p", "/p"])
        floor_program = world.adapter.PROBE_PYTHON_PROGRAM
        write_sh_script(
            directory / "python3",
            f"case \"$*\" in *'sys.executable'*) echo '{reported}' ;; *'{floor_program}'*) echo '3 13' ;; esac\n",
        )
        (world.bin_dir / "python3").unlink()
        (world.bin_dir / "python3").symlink_to(directory / "python3")

        result = world.install()

        assert result.status == 1
        assert f"Make {directory}'s `python3` 3.14 or later" in result.err
        assert "holds none" not in result.err

    @pytest.mark.parametrize(
        "first_line",
        ["BusyBox v1.36.1 multi-call binary.", "", "timeout (some coreutils) 1.0", "timeout (GNU) 9"],
        ids=["busybox", "empty", "coreutils-without-gnu", "gnu-without-coreutils"],
    )
    def test_a_wrapper_that_does_not_name_gnu_coreutils_gets_the_gnu_refusal_and_nothing_is_written(
        self, fast_world: World, first_line: str
    ) -> None:
        world = fast_world
        replace_tool(world, wrapper_name(), f"echo '{first_line}'\n")
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert "GNU coreutils" in result.err
        assert "stricter than the README's `timeout` requirement" in result.err
        assert "brew install coreutils" in result.err
        assert str(world.bin_dir / wrapper_name()) in result.err
        assert world.snapshot() == before

    def _stub_bash(self, world: World, probe_output: str) -> None:
        replace_tool(world, "bash", f"printf '%s\\n' '{probe_output}'\n")

    # Each version is derived from the floor, as in the python rows above.
    @pytest.mark.parametrize(
        "version_for",
        [lambda major, minor: (major, minor - 1), lambda major, minor: (major - 1, 99)],
        ids=["one-below", "major-below"],
    )
    def test_a_recorded_bash_below_the_floor_is_refused_with_its_path_and_version_and_nothing_is_written(
        self, fast_world: World, version_for
    ) -> None:
        world = fast_world
        major, minor = world.adapter.BASH_FLOOR
        version = version_for(major, minor)
        self._stub_bash(world, f"{version[0]} {version[1]}")
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert f"bash at {world.bin_dir / 'bash'} reports {version[0]}.{version[1]}, below {major}.{minor} or later" in result.err
        assert f"Put a bash {major}.{minor} or later first on PATH, then re-run `install`." in result.err
        assert world.snapshot() == before

    @pytest.mark.parametrize(
        "version_for",
        [
            lambda major, minor: (major, minor),
            lambda major, minor: (major, minor + 1),
            lambda major, minor: (major, 10),
            lambda major, minor: (major + 1, 0),
        ],
        ids=["at", "one-above", "string-order", "major-above"],
    )
    def test_a_recorded_bash_at_or_above_the_floor_is_accepted(self, fast_world: World, version_for) -> None:
        world = fast_world
        major, minor = world.adapter.BASH_FLOOR
        assert minor < 10, "the string-order row needs a floor minor with one digit"
        version = version_for(major, minor)
        self._stub_bash(world, f"{version[0]} {version[1]}")

        result = world.install()

        assert result.status == 0, result.err

    @pytest.mark.parametrize("output", ["", "garbage", "5", "5 2 1", "a b", "1234567890 1"], ids=repr)
    def test_a_bash_probe_without_exactly_two_digit_fields_gives_the_no_version_refusal_and_nothing_is_written(
        self, fast_world: World, output: str
    ) -> None:
        world = fast_world
        self._stub_bash(world, output)
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert f"bash at {world.bin_dir / 'bash'} printed no version it could read" in result.err
        assert world.snapshot() == before

    def test_a_bash_the_hooks_path_resolves_ahead_of_the_recorded_one_is_judged_and_refused_when_below_the_floor(
        self, fast_world: World
    ) -> None:
        world = fast_world
        major, minor = world.adapter.BASH_FLOOR
        shadow = world.tmp_path / "shadow"
        shadow.mkdir()
        (shadow / "python3-real").symlink_to(os.path.realpath(sys.executable))
        write_sh_script(shadow / "bash", f"echo '{major} {minor - 1}'\n")
        # The recorded python3 lives in `shadow`, which holds no file named python3, so the hooks run the stub in `bin`.
        (world.bin_dir / "python3").unlink()
        reported = json.dumps([str(shadow / "python3-real"), "/p", "/p"])
        floor_program = world.adapter.PROBE_PYTHON_PROGRAM
        floor_output = " ".join(str(part) for part in world.adapter.PYTHON_FLOOR)
        write_sh_script(
            world.bin_dir / "python3",
            f"case \"$*\" in *'sys.executable'*) echo '{reported}' ;; *'{floor_program}'*) echo '{floor_output}' ;; esac\n",
        )
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        below = f"reports {major}.{minor - 1}, below {major}.{minor} or later"
        assert f"The `bash` the hooks run at {shadow / 'bash'} {below}" in result.err
        assert world.snapshot() == before

    def test_a_hooks_path_that_resolves_no_bash_gives_the_no_bash_cause_and_one_that_does_gives_none(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        tool_paths = {"python3": str(world.bin_dir / "python3"), "bash": str(world.bin_dir / "bash")}
        assert world.adapter.bare_bash_check(tool_paths, str(world.config_dir)) is None
        real_find = world.adapter.find_tool

        def find_without_bash(name, **keywords):
            return None if name == "bash" else real_find(name, **keywords)

        monkeypatch.setattr(world.adapter, "find_tool", find_without_bash)

        cause = world.adapter.bare_bash_check(tool_paths, str(world.config_dir))

        assert cause is not None
        assert cause.startswith("No `bash` resolves on the PATH the hooks run under.")
        assert f"Put a bash {world.adapter.format_version(world.adapter.BASH_FLOOR)} or later first on PATH" in cause

    @pytest.mark.parametrize("tool", ["python3", *RECORDED_AFTER_PYTHON])
    def test_a_recorded_tool_absent_from_path_is_refused_by_name_and_nothing_is_written(
        self, fast_world: World, tool: str
    ) -> None:
        world = fast_world
        (world.bin_dir / tool).unlink()
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert f"`{tool}` is not on PATH." in result.err
        assert "re-run `install`" in result.err
        assert world.snapshot() == before

    def test_neither_wrapper_on_path_gives_the_gnu_refusal_naming_both_as_not_found(self, fast_world: World) -> None:
        world = fast_world
        (world.bin_dir / wrapper_name()).unlink()
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert "Neither `timeout` nor `gtimeout` names GNU coreutils" in result.err
        assert result.err.count(": not found") == 2
        assert world.snapshot() == before

    @pytest.mark.parametrize("surface", ["check", HOOK_ENV_SURFACE])
    @pytest.mark.parametrize("tool", ["python3", "bash", "jq", "sha256sum"])
    def test_a_report_names_a_tool_the_hooks_path_resolves_no_longer_and_names_none_before_it_vanishes(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch, surface: str, tool: str
    ) -> None:
        world = fast_world
        world.install()
        finding = f"No `{tool}` resolves on the PATH the hooks run under."
        assert finding not in run_surface(world, surface).out
        real_find = world.adapter.find_tool

        def find_without_the_tool_on_the_hooks_path(name, **keywords):
            return None if name == tool and keywords.get("search_path") is not None else real_find(name, **keywords)

        monkeypatch.setattr(world.adapter, "find_tool", find_without_the_tool_on_the_hooks_path)

        result = run_surface(world, surface)

        assert finding in result.out
        assert result.status == (1 if surface == "check" else 0)

    def test_a_wrapper_naming_gnu_coreutils_is_accepted_on_that_line(self, fast_world: World) -> None:
        world = fast_world
        replace_tool(world, wrapper_name(), "echo 'timeout (GNU coreutils) 9.4'\n")
        assert world.install().status == 0

    def test_a_wrapper_that_names_gnu_but_does_not_run_its_child_fails_the_self_check_with_the_child_failure_text(
        self, world: World
    ) -> None:
        replace_tool(world, wrapper_name(), "case \"$1\" in --version) echo 'timeout (GNU coreutils) 9.4' ;; *) exit 0 ;; esac\n")
        before = world.snapshot()

        result = world.install()

        wrapper = str(world.bin_dir / wrapper_name())
        assert result.status == 1
        assert f"The wrapper {wrapper} ran with status 0, needed status 3" in result.err
        assert "does not run hooks as the shim needs" in result.err
        assert "hook-run" not in result.err
        assert "GNU coreutils' `timeout`" not in result.err
        assert world.snapshot() == before

    def _copy_scripts_to_stow(self, world: World) -> Path:
        scripts = world.home / ".claude" / "scripts"
        scripts.unlink()
        shutil.copytree(CLAUDE_DIR / "scripts", scripts, ignore=shutil.ignore_patterns("__pycache__", "tests"))
        return scripts / "cursor-adapter"

    def test_a_self_check_that_fails_through_a_real_interpreter_names_the_recorded_path_flags_and_check_and_class(
        self, world: World
    ) -> None:
        (self._copy_scripts_to_stow(world) / "field-table.json").write_text("{not json")
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert str(world.bin_dir / "python3") in result.err
        assert "isolated=1" in result.err
        assert "no_site=1" in result.err
        assert "field-table: ValueError" in result.err
        assert world.snapshot() == before

    def test_a_failure_string_outside_the_grammar_is_rendered_as_other(self, world: World) -> None:
        for failure in (
            "nonsense",
            "hook-run: Refusal not-an-outcome",
            "hook-run: a b c d",
            "unknown-check: Error",
            "hook-run: Refusal sig nal",
        ):
            assert world.adapter.render_failure(failure, "/w") == "self-check: other", failure

    @pytest.mark.parametrize(
        "adapter_source",
        [
            None,
            "",
            "def broken(:\n",
            "import json\nprint(json.dumps({'interpreter': {}, 'failures': ['x: Y']}))\n",
            "import json, sys\nprint(json.dumps({'interpreter': {'executable': 1, 'version': 1, 'isolated': 1, 'no_site': 1}, "
            "'failures': []}))\nsys.exit(1)\n",
        ],
        ids=["removed", "empty", "does-not-compile", "no-interpreter-report", "nonzero-with-a-well-formed-document"],
    )
    def test_a_self_check_run_that_gives_no_result_refuses_install_naming_the_adapter_path(
        self, world: World, adapter_source: str | None
    ) -> None:
        stow_adapter = self._copy_scripts_to_stow(world) / "cursor-adapter.py"
        if adapter_source is None:
            stow_adapter.unlink()
        else:
            stow_adapter.write_text(adapter_source)
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert str(stow_adapter) in result.err
        assert world.snapshot() == before

    @pytest.mark.parametrize(
        ("failure", "expected_parts", "fix_kind"),
        [
            ("hook-run: SelfCheckMismatch status 0", ("ran with status 0, needed status 3",), "replace"),
            ("hook-run: SelfCheckMismatch streams", ("gave status 3 but its child's stdout or stderr",), "replace"),
            ("hook-run: Refusal start-failed ENOENT", ("could not be started at that path", "ENOENT"), "replace"),
            ("hook-run: Refusal start-failed EAGAIN", ("could not be started at that path", "EAGAIN"), "rerun"),
            ("hook-run: Refusal start-failed status:125", ("started and failed on its own", "status:125"), "rerun"),
            ("hook-run: Refusal timeout", ("did not finish within the self-check's deadline", "2 s"), "rerun"),
            ("hook-run: Refusal signal SIGTERM", ("ended by the signal SIGTERM",), "rerun"),
            ("hook-run: Refusal hook-output-cap", ("passed more output than the shim's cap",), "replace"),
        ],
        ids=lambda value: value if isinstance(value, str) and value.startswith("hook-run") else None,
    )
    def test_the_child_failure_text_has_its_outcomes_sentence_and_the_fix_the_split_gives_it(
        self, world: World, failure: str, expected_parts: tuple[str, ...], fix_kind: str
    ) -> None:
        text = world.adapter.child_failure_text("/w/timeout", failure)

        assert text is not None
        assert "/w/timeout" in text
        for part in expected_parts:
            assert part in text
        replace, rerun = world.adapter.REPLACE_WRAPPER_SENTENCE, world.adapter.RERUN_WRAPPER_SENTENCE
        assert text.endswith(replace if fix_kind == "replace" else rerun)
        assert (rerun in text) is (fix_kind == "rerun")

    def test_the_child_failure_text_gives_nothing_for_an_unenterable_directory_and_for_an_unnamed_failure(
        self, world: World
    ) -> None:
        assert world.adapter.child_failure_text("/w", "hook-run: Refusal unenterable-dir") is None
        assert world.adapter.child_failure_text("/w", "hook-run: RuntimeError") is None
        assert world.adapter.child_failure_text("/w", "translation: RuntimeError") is None

    def test_the_child_failure_texts_vocabulary_comes_from_the_shim(self, world: World) -> None:
        shim = world.adapter.load_shim()
        for outcome in shim.HOOK_RUN_OUTCOMES:
            failure = f"hook-run: Refusal {outcome}" + (" ENOENT" if outcome == "start-failed" else "")
            assert (world.adapter.child_failure_text("/w", failure) is None) is (outcome == "unenterable-dir"), outcome

    @pytest.mark.parametrize(
        ("version_output", "expect_in_context"),
        [("3 13", True), ("3 15", False), ("3 14", False), ("nonsense", True)],
        ids=["below", "above", "at", "unreadable"],
    )
    def test_the_hook_env_check_reports_a_bare_name_python_below_the_floor_or_unreadable_and_nothing_at_or_above_it(
        self, fast_world: World, version_output: str, expect_in_context: bool
    ) -> None:
        world = fast_world
        world.install()
        real = world.tmp_path / "real-python"
        real.mkdir()
        (real / "python3.real").symlink_to(os.path.realpath(sys.executable))
        record = world.install_record()
        others = {name: path for name, path in record["tool_paths"].items() if name != "python3"}
        record["tool_paths"] = {"python3": str(real / "python3.real"), **others}
        (world.record_dir / "install.json").write_text(json.dumps(record))
        floor_program = world.adapter.PROBE_PYTHON_PROGRAM
        (world.bin_dir / "python3").unlink()
        write_sh_script(world.bin_dir / "python3", f"case \"$*\" in *'{floor_program}'*) echo '{version_output}' ;; esac\n")

        result = world.run("check", "--hook-env")

        assert result.status == 0
        context = json.loads(result.out)["additional_context"]
        assert (f"`python3` the hooks run at {world.bin_dir / 'python3'}" in context) is expect_in_context


# --- probes ---


class TestProbeBound:
    def _recording_run(self, world: World, monkeypatch: pytest.MonkeyPatch, stalled) -> list[dict]:
        real_run = subprocess.run
        calls: list[dict] = []

        def run(argv, **keywords):
            calls.append({"argv": list(argv), **keywords})
            if stalled(list(argv)):
                raise subprocess.TimeoutExpired(argv, keywords["timeout"])
            return real_run(argv, **keywords)

        monkeypatch.setattr(world.adapter.subprocess, "run", run)
        return calls

    def test_every_probe_goes_out_with_null_stdin_the_bound_and_the_cursor_directory(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        calls = self._recording_run(world, monkeypatch, lambda argv: False)

        assert world.install().status == 0

        assert calls
        for call in calls:
            assert call["stdin"] == subprocess.DEVNULL, call["argv"]
            assert call["stderr"] == subprocess.DEVNULL, call["argv"]
            assert call["timeout"] == world.adapter.PROBE_BOUND_SECONDS, call["argv"]
            assert call["cwd"] == str(world.cursor_dir), call["argv"]

    def test_a_first_install_on_a_host_with_no_cursor_directory_probes_from_home_and_makes_private_files_that_uninstall_removes(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        shutil.rmtree(world.cursor_dir)
        calls = self._recording_run(world, monkeypatch, lambda argv: False)

        result = world.install()

        assert result.status == 0, result.err
        assert calls
        for call in calls:
            assert call["cwd"] == str(world.home), call["argv"]
        assert stat.S_IMODE(world.cursor_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(world.hooks_path.stat().st_mode) == 0o600
        assert world.run("uninstall").status == 0
        assert not world.record_dir.exists()
        assert not world.hooks_path.exists()

    def test_the_session_start_checks_worst_case_probe_time_stays_under_its_entry_timeout_with_headroom(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        world.install()
        calls = self._recording_run(world, monkeypatch, lambda argv: False)
        run_surface(world, HOOK_ENV_SURFACE)
        entry = next(item for item in world.adapter.generate_entries(python_path="/p") if item.label == "check")
        shim = world.adapter.load_shim()
        # A project choice: what the tamper comparison and the interpreter start may take beyond the stdin read and the probes.
        headroom_seconds = 1
        walk_calls = [call for call in calls if shim.HOST_WALK_PROGRAM in call["argv"]]
        probe_calls = [call for call in calls if call not in walk_calls]

        worst_case = (
            shim.MIN_DEADLINE_SECONDS
            + len(probe_calls) * world.adapter.PROBE_BOUND_SECONDS
            + sum(call["timeout"] for call in walk_calls)
        )

        assert probe_calls, "the sessionStart check ran no probe, so the count below would pass vacuously"
        assert [call["timeout"] for call in walk_calls] == [world.adapter.HOST_WALK_BOUND_SECONDS]
        assert entry.entry["timeout"] == world.adapter.CHECK_ENTRY_TIMEOUT_SECONDS
        assert worst_case + headroom_seconds <= world.adapter.CHECK_ENTRY_TIMEOUT_SECONDS

    def test_a_python_floor_probe_uses_the_isolated_c_form_and_the_bare_name_probe_gets_the_hooks_environment(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        calls = self._recording_run(world, monkeypatch, lambda argv: False)

        world.install()

        python_calls = [call for call in calls if call["argv"][1:4] == ["-I", "-S", "-c"]]
        assert {call["argv"][4] for call in python_calls} == {
            world.adapter.QUERY_PYTHON_PROGRAM,
            world.adapter.PROBE_PYTHON_PROGRAM,
        }
        expected_path = world.adapter.load_shim().hook_environment(world.install_record())["PATH"]
        bare = [
            call
            for call in python_calls
            if call["argv"][4] == world.adapter.PROBE_PYTHON_PROGRAM and call["env"]["PATH"] == expected_path
        ]
        assert bare
        assert all(call["env"]["CLAUDE_CONFIG_DIR"] == str(world.config_dir) for call in bare)

    @pytest.mark.parametrize(
        ("tool", "marker"),
        [
            ("python3", "sys.executable"),
            ("python3", "sys.version_info"),
            ("bash", "BASH_VERSINFO"),
            (None, "--version"),
        ],
        ids=["python-query", "python-floor", "bash-floor", "wrapper-version"],
    )
    def test_a_stalled_probe_is_reported_by_the_stall_text_and_install_writes_nothing(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch, tool: str | None, marker: str
    ) -> None:
        world = fast_world
        path = str(world.bin_dir / (tool or wrapper_name()))
        before = world.snapshot()
        self._recording_run(world, monkeypatch, lambda argv: argv[0] == path and any(marker in word for word in argv))

        result = world.install()

        assert result.status == 1
        assert f"{path} did not answer within {world.adapter.PROBE_BOUND_SECONDS} s" in result.err
        assert "`install`" in result.err
        assert "from your own terminal" in result.err
        assert "GNU coreutils" not in result.err
        assert world.snapshot() == before

    @pytest.mark.parametrize("surface", SURFACES)
    def test_a_report_starts_only_its_own_probes_each_with_null_stdin_the_bound_the_cursor_directory_and_an_environment(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch, surface: str
    ) -> None:
        world = fast_world
        world.install()
        calls = self._recording_run(world, monkeypatch, lambda argv: False)

        run_surface(world, surface)

        walk_program = world.adapter.load_shim().HOST_WALK_PROGRAM
        walk_calls = [call for call in calls if walk_program in call["argv"]]
        assert len(walk_calls) == (1 if surface == HOOK_ENV_SURFACE else 0), surface
        for call in walk_calls:
            assert call["stdin"] == subprocess.DEVNULL
            assert call["timeout"] == world.adapter.HOST_WALK_BOUND_SECONDS
            assert call["cwd"] == str(world.cursor_dir)
        calls = [call for call in calls if call not in walk_calls]
        probed = {(os.path.basename(call["argv"][0]), call["argv"][-1]) for call in calls}
        expected = (
            set()
            if surface == "status"
            else {
                ("python3", world.adapter.PROBE_PYTHON_PROGRAM),
                ("bash", world.adapter.PROBE_BASH_PROGRAM),
                (wrapper_name(), "--version"),
            }
        )
        assert probed == expected, surface
        for call in calls:
            assert call["stdin"] == subprocess.DEVNULL, call["argv"]
            assert call["timeout"] == world.adapter.PROBE_BOUND_SECONDS, call["argv"]
            assert call["cwd"] == str(world.cursor_dir), call["argv"]
        hooks_path = world.adapter.load_shim().hook_environment(world.install_record())["PATH"]
        bare_probes = [call for call in calls if call["env"].get("PATH") == hooks_path]
        assert len(bare_probes) == (0 if surface == "status" else 2), surface
        python_probes = [call for call in calls if call["argv"][-1] == world.adapter.PROBE_PYTHON_PROGRAM]
        assert len(python_probes) == {"status": 0, "check": 2, HOOK_ENV_SURFACE: 1}[surface]

    @pytest.mark.parametrize("surface", ["check", HOOK_ENV_SURFACE])
    @pytest.mark.parametrize(
        ("tool", "marker"),
        [("python3", "sys.version_info"), ("bash", "BASH_VERSINFO"), (None, "--version")],
        ids=["python-floor", "bash-floor", "wrapper-version"],
    )
    def test_a_stalled_probe_in_a_report_gives_the_stall_text_and_no_floor_or_gnu_text(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch, surface: str, tool: str | None, marker: str
    ) -> None:
        world = fast_world
        world.install()
        path = str(world.bin_dir / (tool or wrapper_name()))
        self._recording_run(world, monkeypatch, lambda argv: argv[0] == path and any(marker in word for word in argv))

        result = run_surface(world, surface)

        assert f"{path} did not answer within {world.adapter.PROBE_BOUND_SECONDS} s" in result.out
        assert "`check`" in result.out
        floor_name = world.adapter.format_version(world.adapter.PYTHON_FLOOR)
        for text in (f"below {floor_name}", "printed no version", "GNU coreutils", "could not be confirmed"):
            assert text not in result.out, (surface, tool, text)

    def test_a_stalled_tool_re_test_leaves_the_tamper_comparison_and_the_log_line_done(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        world.install()
        document = hooks_document(world)
        event, index = find_entry(world, document, "require-code-review")
        mutate_remove(document, event, index)
        save_hooks(world, document)
        path = str(world.bin_dir / "python3")
        self._recording_run(world, monkeypatch, lambda argv: argv[0] == path and "sys.version_info" in argv[-1])

        result = run_surface(world, HOOK_ENV_SURFACE)

        assert f"missing or altered entry: require-code-review on {event}" in result.out
        assert "did not answer within" in result.out
        assert [fields["reg"] for fields in world.adapter.read_log(str(world.state_dir)).lines] == ["check-hook-env"]

    @pytest.mark.parametrize("surface", ["check", HOOK_ENV_SURFACE])
    def test_a_python3_that_prints_on_both_streams_but_no_version_gets_the_no_version_text_and_neither_stream(
        self, fast_world: World, surface: str
    ) -> None:
        world = fast_world
        world.install()
        (world.bin_dir / "python3").unlink()
        write_sh_script(world.bin_dir / "python3", "echo SECRETOUT\necho SECRETERR >&2\n")

        result = run_surface(world, surface)

        assert "printed no version it could read" in result.out
        assert "SECRETOUT" not in result.out + result.err
        assert "SECRETERR" not in result.out + result.err

    @pytest.mark.timing
    def test_a_probe_kill_reaches_a_stalled_tool_that_execs_a_sleep_within_the_bound_plus_a_margin(self, world: World) -> None:
        import time

        stub = write_sh_script(world.tmp_path / "stalls", "exec /bin/sleep 20\n")
        started = time.monotonic()
        with pytest.raises(world.adapter.ProbeStalled):
            world.adapter.run_probe(str(stub), [])
        # A kill that reaches the sleep ends the run near the bound, and the margin covers a loaded runner's scheduling delay.
        assert time.monotonic() - started < world.adapter.PROBE_BOUND_SECONDS + 3

    @pytest.mark.timing
    def test_a_probe_returns_within_the_bound_while_a_child_of_the_stalled_tool_holds_its_output_pipe(self, world: World) -> None:
        import time

        stub = write_sh_script(world.tmp_path / "stalls-with-child", "/bin/sleep 8 &\nexec /bin/sleep 20\n")
        started = time.monotonic()
        with pytest.raises(world.adapter.ProbeStalled):
            world.adapter.run_probe(str(stub), [])
        # The child holds the pipe for 8 s, so a run that waited for it overshoots the bound by more than this margin.
        assert time.monotonic() - started < world.adapter.PROBE_BOUND_SECONDS + 3


# --- the run-mode line ---


RUN_MODE_FILES = {
    "absent": None,
    "unparseable": "{not json",
    "autorun-shape": json.dumps({"autoRun": {"mode": "allowlist", "allowlist": []}}),
    "g7-keys": json.dumps({"permissions": {"allow": ["Shell(ls)"]}, "sandbox": False}),
}


class TestRunModeWarning:
    def test_install_with_the_flag_records_the_acceptance_only_key(self, fast_world: World) -> None:
        assert fast_world.install().status == 0
        assert fast_world.install_record()["acceptance_only"] is True

    def test_install_over_an_acceptance_only_record_writes_a_record_without_the_key_and_no_channel_prints_the_warning(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        assert world.install_record()["acceptance_only"] is True

        installed = world.run("install", "--config-dir", str(world.config_dir))
        checked = world.run("check")
        status = world.run("status")
        context = json.loads(world.run("check", "--hook-env").out)["additional_context"]

        assert installed.status == 0
        assert "acceptance_only" not in world.install_record()
        for text in (installed.out, checked.out, status.out, context):
            assert world.adapter.RUN_MODE_LINE in text
            assert "acceptance-only" not in text
        assert world.adapter.read_log(str(world.state_dir)).lines[-1]["runmode"] == "unreadable"

    @pytest.mark.parametrize("permissions_file", list(RUN_MODE_FILES))
    def test_each_channel_prints_the_run_mode_line_and_the_acceptance_line_whatever_the_permissions_file_holds(
        self, fast_world: World, permissions_file: str
    ) -> None:
        world = fast_world
        content = RUN_MODE_FILES[permissions_file]
        if content is not None:
            (world.cursor_dir / "permissions.json").write_text(content)
        adapter = world.adapter

        installed = world.install()
        checked = world.run("check")
        status = world.run("status")
        context = json.loads(world.run("check", "--hook-env").out)["additional_context"]

        for text in (installed.out, checked.out, status.out, context):
            assert adapter.RUN_MODE_LINE in text
            assert adapter.ACCEPTANCE_ONLY_LINE in text

    def test_the_session_start_log_lines_run_mode_field_holds_the_closed_label(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        world.run("check", "--hook-env")
        lines = world.adapter.read_log(str(world.state_dir)).lines
        assert [fields["runmode"] for fields in lines if fields.get("reg") == "check-hook-env"] == ["unreadable+acceptance-only"]

    def test_a_record_without_the_acceptance_key_prints_no_acceptance_line_and_logs_the_plain_label(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        record = world.install_record()
        del record["acceptance_only"]
        (world.record_dir / "install.json").write_text(json.dumps(record))

        context = json.loads(world.run("check", "--hook-env").out)["additional_context"]

        assert world.adapter.ACCEPTANCE_ONLY_LINE not in context
        assert world.adapter.RUN_MODE_LINE in context
        assert world.adapter.read_log(str(world.state_dir)).lines[-1]["runmode"] == "unreadable"


# --- install's own prerequisite check ---


def stow_without_hook_script(world: World, script: str) -> None:
    stowed_hooks = world.home / ".claude" / "hooks"
    stowed_hooks.unlink()
    stowed_hooks.mkdir()
    for hook_file in (CLAUDE_DIR / "hooks").iterdir():
        if hook_file.name != script:
            (stowed_hooks / hook_file.name).symlink_to(hook_file)


def stow_without_shim(world: World) -> None:
    stowed_scripts = world.home / ".claude" / "scripts"
    stowed_scripts.unlink()
    stowed_scripts.mkdir()
    for item in (CLAUDE_DIR / "scripts").iterdir():
        if item.name != "cursor-adapter":
            (stowed_scripts / item.name).symlink_to(item)
    stowed_adapter = stowed_scripts / "cursor-adapter"
    stowed_adapter.mkdir()
    for item in ADAPTER_DIR.iterdir():
        if item.name != "hook-shim.py":
            (stowed_adapter / item.name).symlink_to(item)


class TestInstallRefusesAnEntryThatCouldNotRun:
    @pytest.mark.parametrize("missing", ["hook-script", "shim"])
    def test_install_names_the_stowed_file_an_entry_would_run_and_writes_nothing_when_it_is_absent(
        self, fast_world: World, missing: str
    ) -> None:
        world = fast_world
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        if missing == "hook-script":
            absent = world.adapter.stow_hook_path("require-code-review.sh")
            stow_without_hook_script(world, "require-code-review.sh")
        else:
            absent = world.adapter.stow_shim_path()
            stow_without_shim(world)
        assert not os.path.exists(absent)
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert f"{absent} is missing, so an entry the install would write could not run" in result.err
        assert world.snapshot() == before
