"""Tests for the account preflight. A fake `direnv` and a fake `claude` on a `tmp_path` `PATH` stand in for
the real tools, so the real probe snippet runs. No test launches the real CLI.
"""
from __future__ import annotations

import os
import shlex
import time
from pathlib import Path

import pytest
import run_replay_bench
from replay_bench import account, attempt
from review_bench import runner as review_runner
from review_bench.runner import HarnessInvalidatedError
from test_replay_bench_launch import PROXY_WITH_CREDENTIALS
from test_replay_bench_runner_support import (
    CODE_WRITER_MARKDOWN,
    Scenario,
    build_config_dir,
    build_replay_world,
    make_block,
    make_settings,
    write_executable,
)

PASSTHROUGH_DIRENV = "shift 2\nexec \"$@\"\n"
CLAUDE_PRINTING_VERSION = 'echo "2.1.322 (Claude Code)"\n'


class Preflight:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.home = tmp_path / "home"
        self.config_dir = self.home / ".claude"
        build_config_dir(self.config_dir)
        self.repo = tmp_path / "repo"
        self.repo.mkdir()
        self.bin = tmp_path / "bin"
        self.claude_ran = tmp_path / "claude-ran"
        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        self.install_direnv(PASSTHROUGH_DIRENV)
        self.install_claude(CLAUDE_PRINTING_VERSION)

    def install_direnv(self, body: str) -> None:
        write_executable(self.bin / "direnv", body)
        self.monkeypatch.setenv("PATH", f"{self.bin}:/usr/bin:/bin")

    def install_claude(self, body: str) -> None:
        write_executable(self.bin / "claude", f"touch {shlex.quote(str(self.claude_ran))}\n{body}")

    def run(self, **kwargs) -> account.Account:
        return account.preflight_repo(self.repo, **kwargs)

    def refusal(self, **kwargs) -> str:
        with pytest.raises(HarnessInvalidatedError) as raised:
            self.run(**kwargs)
        return str(raised.value)


@pytest.fixture
def preflight(tmp_path, monkeypatch):
    return Preflight(tmp_path, monkeypatch)


class TestTheProbe:
    def test_a_fake_that_keeps_the_callers_working_directory_passes(self, preflight):
        resolved = preflight.run()

        assert resolved.config_dir == preflight.config_dir
        assert resolved.key == os.path.realpath(preflight.config_dir)
        assert resolved.config_dir_value is None
        assert resolved.code_writer_tools == frozenset({"Read", "Edit", "Write", "Bash", "Grep", "Glob"})

    def test_a_fake_that_changes_into_the_source_repo_first_is_refused(self, preflight):
        preflight.install_direnv('cd "$2" && shift 2 && exec "$@"\n')

        assert "working directory" in preflight.refusal()

    def test_a_set_config_dir_resolves_to_its_value_and_an_unset_one_to_the_home_default(self, preflight, tmp_path):
        other = tmp_path / "other-config"
        build_config_dir(other)
        preflight.monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(other))
        assert preflight.run().config_dir == other

        preflight.monkeypatch.delenv("CLAUDE_CONFIG_DIR")
        assert preflight.run().config_dir == preflight.config_dir

    def test_stdout_that_is_not_json_is_refused_without_echoing_it(self, preflight):
        preflight.install_direnv('echo "sentinel-not-json"\n')

        message = preflight.refusal()

        assert "sentinel-not-json" not in message and "expected JSON" in message

    @pytest.mark.timing
    def test_a_probe_that_sleeps_past_the_limit_names_the_limit_and_the_flag(self, preflight):
        preflight.install_direnv("exec sleep 10\n")
        started = time.monotonic()

        message = preflight.refusal(direnv_timeout_s=1)

        assert "timed out after 1 s; raise --direnv-timeout-s" in message
        assert time.monotonic() - started < 5

    def test_a_failed_probe_carries_the_hint_and_none_of_the_probes_stderr(self, preflight):
        preflight.install_direnv('echo "sentinel-stderr" >&2\nexit 3\n')

        message = preflight.refusal()

        assert "exit 3" in message and "direnv exec" in message and "true" in message
        assert "sentinel-stderr" not in message

    def test_a_multi_line_profile_value_prints_only_the_variable_name(self, preflight):
        preflight.install_direnv(
            'export SENTINEL_PROFILE="first line\nLEAKED_NAME=sentinel-value\nthird line"\nshift 2\nexec "$@"\n'
        )

        resolved = preflight.run()

        assert "SENTINEL_PROFILE" in resolved.dropped_variables
        assert "LEAKED_NAME" not in resolved.dropped_variables
        assert not any("sentinel-value" in name for name in resolved.dropped_variables)

    def test_an_ambient_variable_is_listed_and_no_allowlisted_name_is(self, preflight):
        preflight.monkeypatch.setenv("AMBIENT_PROFILE", "sentinel-value")
        preflight.monkeypatch.setenv("HTTPS_PROXY", PROXY_WITH_CREDENTIALS)

        dropped = preflight.run().dropped_variables

        assert "AMBIENT_PROFILE" in dropped
        assert not {"PATH", "HOME", "HTTPS_PROXY", "CLAUDE_CONFIG_DIR"} & set(dropped)

    def test_a_path_with_an_empty_or_a_relative_entry_is_refused_before_any_probe(self, preflight):
        for bad_path in (f"{preflight.bin}::/usr/bin", f"{preflight.bin}:relative/bin"):
            preflight.monkeypatch.setenv("PATH", bad_path)
            assert "PATH" in preflight.refusal()
        assert not preflight.claude_ran.exists()

    def test_a_relative_source_repo_path_is_refused_before_any_probe(self, preflight):
        with pytest.raises(HarnessInvalidatedError, match="not absolute"):
            account.preflight_repo(Path("relative/repo"))

    def test_a_missing_direnv_is_refused(self, preflight):
        (preflight.bin / "direnv").unlink()

        assert "direnv is not on PATH" in preflight.refusal()


class TestTheRefusals:
    def test_a_config_dir_without_claude_md_is_refused(self, preflight):
        (preflight.config_dir / "CLAUDE.md").unlink()
        assert "CLAUDE.md" in preflight.refusal()

    def test_a_code_writer_without_effort_is_refused_naming_the_field(self, preflight):
        text = CODE_WRITER_MARKDOWN.replace("effort: high\n", "")
        (preflight.config_dir / "agents" / "code-writer.md").write_text(text)

        assert "effort" in preflight.refusal()

    def test_a_settings_file_without_the_session_start_hook_is_refused(self, preflight):
        (preflight.config_dir / "settings.json").write_text("{}")

        assert "capture-session-id.sh" in preflight.refusal()

    def test_a_generator_refusal_names_the_path_after_the_probe_and_before_the_readiness_check(self, preflight):
        (preflight.config_dir / "rules" / "we*ird.md").write_text("rule")

        message = preflight.refusal()

        assert "we*ird" in message
        assert not preflight.claude_ran.exists(), "the readiness check must not have run"

    def test_the_ones_that_print_a_path_or_a_variable_name_do_so_in_repr_form(self, preflight, capsys):
        (preflight.config_dir / "rules" / "esc\x1b\nname*.md").write_text("rule")
        message = preflight.refusal()
        assert "\x1b" not in message and "\n" not in message.split(": ", 1)[1]

        (preflight.config_dir / "rules" / "esc\x1b\nname*.md").unlink()
        preflight.monkeypatch.setenv("WEIRD\x1b\nNAME", "value")
        args = run_replay_bench.build_parser().parse_args(["preflight", "--tasks", "x"])
        run_replay_bench._preflight_repos(args, [str(preflight.repo)])
        printed = capsys.readouterr().out
        assert "\x1b" not in printed and "WEIRD\\x1b\\nNAME" in printed


class TestTheReadinessCheck:
    def test_a_missing_claude_is_refused_before_any_launch(self, preflight):
        (preflight.bin / "claude").unlink()

        assert "claude --version" in preflight.refusal()

    def test_a_claude_that_exits_nonzero_is_refused_without_its_stderr(self, preflight):
        preflight.install_claude('echo "sentinel-stderr" >&2\nexit 1\n')

        message = preflight.refusal()

        assert "exit 1" in message and "sentinel-stderr" not in message

    @pytest.mark.parametrize(("printed", "expected_refusal"), [
        ("2.1.270 (Claude Code)", "older than 2.1.271"), ("no version here", "cannot parse"),
        ("2.2.0", None), ("2.1.271 (Claude Code)", None),
    ])
    def test_the_version_floor(self, preflight, printed, expected_refusal):
        preflight.install_claude(f'echo "{printed}"\n')

        if expected_refusal is None:
            preflight.run()
        else:
            assert expected_refusal in preflight.refusal()

    @pytest.mark.timing
    def test_a_claude_that_sleeps_past_the_limit_is_refused_within_five_seconds(self, preflight):
        preflight.install_claude("exec sleep 10\n")
        started = time.monotonic()

        message = preflight.refusal(readiness_timeout_s=1)

        assert "timed out after 1 s" in message and time.monotonic() - started < 5

    def test_the_readiness_timeout_defaults_to_the_review_benchs_environment_read_timeout_read_at_call_time(
        self, preflight, monkeypatch,
    ):
        seen = []
        monkeypatch.setattr(review_runner, "_ENVIRONMENT_READ_TIMEOUT_S", 42.0)
        real_check = account.check_readiness

        def spy(prefix, probe_dir, *, repo, timeout_s):
            seen.append(timeout_s)
            return real_check(prefix, probe_dir, repo=repo, timeout_s=timeout_s)

        monkeypatch.setattr(account, "check_readiness", spy)
        preflight.run()

        assert seen == [42.0]


class TestPrintHygiene:
    """With a proxy URL carrying credentials in the runner's environment, no refusal or record carries it."""

    @pytest.fixture(autouse=True)
    def proxy(self, preflight):
        preflight.monkeypatch.setenv("HTTPS_PROXY", PROXY_WITH_CREDENTIALS)

    def test_a_claude_version_that_prints_the_sentinel_and_fails_is_refused_without_it(self, preflight, capsys):
        preflight.install_claude(f'echo "{PROXY_WITH_CREDENTIALS}" >&2\necho "{PROXY_WITH_CREDENTIALS}"\nexit 1\n')

        message = preflight.refusal()

        captured = capsys.readouterr()
        assert "sentinel-credential" not in message + captured.out + captured.err

    @pytest.mark.timing
    def test_a_claude_version_that_sleeps_past_its_limit_is_refused_without_it(self, preflight, capsys):
        preflight.install_claude("exec sleep 10\n")

        message = preflight.refusal(readiness_timeout_s=1)

        captured = capsys.readouterr()
        assert "sentinel-credential" not in message + captured.out + captured.err



class TestLaunchExceptionHygiene:
    def test_a_launch_that_raises_oserror_records_a_detail_without_the_sentinel(self, tmp_path, monkeypatch, capsys):
        world = build_replay_world(tmp_path, monkeypatch)

        def failing_launch(cmd, _cwd, _timeout_s):
            raise OSError(2, "No such file or directory", " ".join(cmd) + PROXY_WITH_CREDENTIALS)

        from replay_bench.runner import execute_run

        settings = make_settings(world, failing_launch)
        monkeypatch.setenv("HTTPS_PROXY", PROXY_WITH_CREDENTIALS)
        outcome = execute_run(
            attempt.RunSettings(**{**settings.__dict__, "runner_environ": {**os.environ}}), make_block(world), "bare", 0,
        )

        captured = capsys.readouterr()
        assert outcome.record.missing_reason == attempt.REASON_LAUNCH_ERROR
        assert "sentinel-credential" not in outcome.record.missing_detail + captured.out + captured.err


class TestAFixtureUnderHome:
    def test_it_is_refused_and_the_attempt_leaves_no_directory(self, tmp_path, monkeypatch):
        import tempfile

        world = build_replay_world(tmp_path, monkeypatch)
        under_home = world.home / "tmp"
        under_home.mkdir()
        monkeypatch.setattr(tempfile, "tempdir", str(under_home))
        launch_stub = world.launch(lambda _n, _a: Scenario())

        from replay_bench.runner import execute_run

        with pytest.raises(HarnessInvalidatedError, match="under \\$HOME"):
            execute_run(make_settings(world, launch_stub), make_block(world), "bare", 0)

        assert launch_stub.calls == [] and list(under_home.iterdir()) == []
