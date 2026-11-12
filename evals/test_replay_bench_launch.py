"""Tests for replay_bench/launch: the allowlisted environment, the instrument hook and the Read allow list.
Fake `claude` scripts stand in for the CLI. No test launches the real one.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import measure_subagent_model_resolution as msmr
import pytest
from replay_bench import arms, launch
from test_replay_bench_runner_support import write_executable

HOOK_TIMEOUT_S = 20  # a hook that has not exited by then is hung
PROXY_WITH_CREDENTIALS = "http://user:sentinel-credential@proxy.invalid"


def stow_style_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A home `.claude` and a separate config dir, each holding links into one third directory."""
    checkout = tmp_path / "checkout"
    (checkout / "agents" / "sub").mkdir(parents=True)
    (checkout / "agents" / "a.md").write_text("a")
    (checkout / "agents" / "sub" / "x.md").write_text("x")
    (checkout / "agents" / "notes.txt").write_text("not markdown")
    (checkout / "skills" / "demo").mkdir(parents=True)
    (checkout / "skills" / "demo" / "SKILL.md").write_text("skill")
    (checkout / "skills" / "demo" / "other.md").write_text("other")
    home_claude = tmp_path / "home" / ".claude"
    config_dir = tmp_path / "config"
    for root in (home_claude, config_dir):
        root.mkdir(parents=True)
        (root / "agents").symlink_to(checkout / "agents")
        (root / "skills").symlink_to(checkout / "skills")
    return home_claude.parent, config_dir, checkout


class TestReadAllowList:
    def test_each_agent_and_skill_file_appears_by_link_path_and_by_realpath_as_an_absolute_read_entry(self, tmp_path):
        home, config_dir, checkout = stow_style_tree(tmp_path)

        entries = launch.read_allow_entries(launch.read_allow_paths(config_dir, home))

        for link_root in (config_dir, home / ".claude"):
            assert f"Read(/{link_root / 'agents' / 'a.md'})" in entries
            assert f"Read(/{link_root / 'skills' / 'demo' / 'SKILL.md'})" in entries
        assert f"Read(/{checkout / 'agents' / 'a.md'})" in entries
        assert all(entry.startswith("Read(//") for entry in entries)
        assert len(entries) == len(set(entries))

    def test_nested_non_markdown_non_skill_and_dangling_files_are_left_out(self, tmp_path):
        home, config_dir, checkout = stow_style_tree(tmp_path)
        (checkout / "agents" / "dangling.md").symlink_to(tmp_path / "missing.md")

        joined = "\n".join(launch.read_allow_entries(launch.read_allow_paths(config_dir, home)))

        for left_out in ("sub/x.md", "notes.txt", "other.md", "dangling.md"):
            assert left_out not in joined

    def test_one_root_that_is_the_others_realpath_yields_no_duplicate(self, tmp_path):
        home, config_dir, _checkout = stow_style_tree(tmp_path)
        same_root_home = tmp_path / "home2"
        same_root_home.mkdir()
        (same_root_home / ".claude").symlink_to(config_dir)

        paths = launch.read_allow_paths(config_dir, same_root_home)

        assert len(paths) == len(set(paths))
        assert not any(str(same_root_home / ".claude") in str(path) for path in paths)

    def test_no_entry_holds_a_wildcard(self, tmp_path):
        home, config_dir, _checkout = stow_style_tree(tmp_path)

        entries = launch.read_allow_entries(launch.read_allow_paths(config_dir, home))

        assert not any("*" in entry for entry in entries)

    def test_the_tail_is_the_flag_then_each_entry_as_its_own_element(self, tmp_path):
        home, config_dir, _checkout = stow_style_tree(tmp_path)
        paths = launch.read_allow_paths(config_dir, home)

        assert launch.allowed_tools_tail(paths) == ["--allowedTools", *launch.read_allow_entries(paths)]


REFUSED = [
    pytest.param(" ", id="space"), pytest.param("\t", id="tab"), pytest.param("\n", id="newline"),
    pytest.param("\x1b", id="escape"), pytest.param("*", id="star"), pytest.param("?", id="question"),
    pytest.param("[", id="open-bracket"), pytest.param("]", id="close-bracket"), pytest.param("{", id="open-brace"),
    pytest.param("}", id="close-brace"), pytest.param("(", id="open-paren"), pytest.param(")", id="close-paren"),
    pytest.param(",", id="comma"), pytest.param("\\", id="backslash"),
]
KEPT = ["-", "_", ".", "+", "@", "~", "#", "!", ":", "=", "é"]


class TestTheSharedCharacterCheck:
    @pytest.mark.parametrize("character", REFUSED)
    def test_each_refused_class_raises_naming_the_path_in_repr_form(self, character):
        with pytest.raises(launch.UnsafeConfigPathError) as raised:
            launch.check_path_characters(f"/config/a{character}b.md")
        assert "\n" not in str(raised.value) and "\x1b" not in str(raised.value)

    @pytest.mark.parametrize("character", KEPT)
    def test_each_kept_lookalike_passes(self, character):
        launch.check_path_characters(f"/config/a{character}b.md")


class TestEachEmittedFormIsChecked:
    def test_the_exclude_generator_checks_a_link_path_and_a_realpath_alone(self, tmp_path):
        config_dir = tmp_path / "config"
        (config_dir / "rules").mkdir(parents=True)
        target = tmp_path / "targets" / "ok[1].md"
        target.parent.mkdir()
        target.write_text("rule")
        (config_dir / "rules" / "fine.md").symlink_to(target)

        with pytest.raises(launch.UnsafeConfigPathError, match=r"ok\[1\]"):
            arms.claude_md_excludes(config_dir)

    def test_the_read_allow_generator_checks_a_link_path_and_a_realpath_alone(self, tmp_path):
        config_dir = tmp_path / "config"
        (config_dir / "skills" / "we(ird)").mkdir(parents=True)
        (config_dir / "skills" / "we(ird)" / "SKILL.md").write_text("skill")
        with pytest.raises(launch.UnsafeConfigPathError, match=r"we\(ird\)"):
            launch.read_allow_paths(config_dir, tmp_path / "nohome")

        spaced_target = tmp_path / "spaced dir" / "SKILL.md"
        spaced_target.parent.mkdir()
        spaced_target.write_text("skill")
        other = tmp_path / "other"
        (other / "skills").mkdir(parents=True)
        (other / "skills" / "linked").symlink_to(spaced_target.parent)
        with pytest.raises(launch.UnsafeConfigPathError, match="spaced dir"):
            launch.read_allow_paths(other, tmp_path / "nohome")


class TestTheLaunchEnvironment:
    def test_the_prefix_passes_each_proxy_form_the_runner_sets(self):
        environ = {
            "PATH": "/usr/bin", "https_proxy": "a", "http_proxy": "b", "no_proxy": "c", "ALL_PROXY": "d", "all_proxy": "e",
        }
        prefix = launch.launch_prefix(environ, config_dir_value=None)

        assert prefix[:2] == ["/usr/bin/env", "-i"]
        for assignment in ("https_proxy=a", "http_proxy=b", "no_proxy=c", "ALL_PROXY=d", "all_proxy=e"):
            assert assignment in prefix

    def test_a_variable_the_runner_does_not_set_is_left_out_and_an_unset_config_dir_is_absent(self):
        prefix = launch.launch_prefix({"PATH": "/usr/bin"}, config_dir_value=None)

        assert prefix == ["/usr/bin/env", "-i", "PATH=/usr/bin"]

    def test_a_probed_config_dir_is_passed(self):
        assert launch.launch_prefix({}, config_dir_value="/cfg")[-1] == "CLAUDE_CONFIG_DIR=/cfg"

    def test_a_fake_claude_behind_the_prefix_sees_only_the_allowlist_and_the_probed_config_dir(self, tmp_path, monkeypatch):
        dump = tmp_path / "environment.txt"
        fake_bin = tmp_path / "bin"
        write_executable(fake_bin / "claude", f"env > {shlex.quote(str(dump))}\n")
        monkeypatch.setenv("SENTINEL_SESSION_TOKEN", "sentinel-value")
        environ = {**os.environ, "PATH": f"{fake_bin}:/usr/bin:/bin", "HTTPS_PROXY": PROXY_WITH_CREDENTIALS}

        prefix = launch.launch_prefix(environ, config_dir_value="/probed/config")
        subprocess.run([*prefix, "claude"], check=True, cwd=tmp_path)

        seen = dict(line.split("=", 1) for line in dump.read_text().splitlines() if "=" in line)
        shell_added = {"PWD", "SHLVL", "_", "OLDPWD"}  # set by `sh` itself, not by the launch
        assert set(seen) - shell_added <= {*launch.LAUNCH_ENV_ALLOWLIST, launch.CONFIG_DIR_VARIABLE}
        assert seen["HTTPS_PROXY"] == PROXY_WITH_CREDENTIALS
        assert seen["CLAUDE_CONFIG_DIR"] == "/probed/config"

    @pytest.mark.timing
    def test_the_group_kill_reaches_a_forked_child_behind_the_env_prefix(self, tmp_path, monkeypatch):
        pid_file = tmp_path / "child.pid"
        fake_bin = tmp_path / "bin"
        write_executable(
            fake_bin / "claude",
            f"sh -c 'echo $$ > {shlex.quote(str(pid_file))}; exec sleep 30' &\nexec sleep 30\n",
        )
        prefix = launch.launch_prefix({"PATH": f"{fake_bin}:/usr/bin:/bin"}, config_dir_value=None)

        _lines, timed_out = msmr._run_claude_to_completion([*prefix, "claude"], tmp_path, 3)

        assert timed_out
        child_pid = int(pid_file.read_text())
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.1)
        pytest.fail("the forked child outlived the group kill")


class TestTheInstrumentHook:
    def run_hook(self, log_path: Path, payload: str) -> None:
        command = json.loads(launch.settings_json(log_path))["hooks"]["InstructionsLoaded"][0]["hooks"][0]["command"]
        subprocess.run(["sh", "-c", command], input=payload.encode(), check=True, timeout=HOOK_TIMEOUT_S)

    def test_the_settings_json_carries_one_command_hook_and_nothing_else(self, tmp_path):
        settings = json.loads(launch.settings_json(tmp_path / "log.jsonl"))

        assert list(settings) == ["hooks"] and list(settings["hooks"]) == ["InstructionsLoaded"]
        assert len(settings["hooks"]["InstructionsLoaded"]) == 1
        assert settings["hooks"]["InstructionsLoaded"][0]["hooks"][0]["type"] == "command"

    def test_the_hook_appends_a_json_line_to_a_log_path_with_a_space_and_a_single_quote(self, tmp_path):
        log_path = tmp_path / "it's a dir" / "instructions-loaded.jsonl"
        log_path.parent.mkdir()

        self.run_hook(log_path, '{"file_path": "/synthetic"}\n')

        assert json.loads(log_path.read_text()) == {"file_path": "/synthetic"}

    @pytest.mark.timing
    def test_twenty_parallel_hooks_leave_a_log_whose_every_line_parses(self, tmp_path):
        log_path = tmp_path / "instructions-loaded.jsonl"
        payloads = [json.dumps({"index": index, "padding": "p" * 200}) for index in range(20)]

        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = [pool.submit(self.run_hook, log_path, payload) for payload in payloads]
            for future in futures:
                future.result()

        parsed = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert sorted(entry["index"] for entry in parsed) == list(range(20))
