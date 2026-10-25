"""Tests for reconcile-project-plugins.sh.

Runs the whole script synchronously against a `claude` PATH shim and real
scratch git repos. The detached-child launch (`</dev/null >/dev/null 2>&1 &`)
is bootstrap-project-plugins.sh's own invocation contract, exercised by that
hook's tests (claude/.claude/hooks/tests/test_bootstrap_project_plugins.py).
`_lib_missing_plugin_ids` and `_lib_validate_plugin_id`, the shared `_lib.sh`
helpers this script calls, have their own direct unit tests in
claude/.claude/hooks/tests/test_lib.py.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

import pytest
from helpers import (
    RECONCILE_SH,
    REPO_ROOT,
    _sourced_value,
    ambient_umask,
    assert_cap_engaged,
    file_mode,
    git_isolation_env,
    init_reconciler_repo,
    install_log_entries,
    installed_locale_where,
    locale_that_widens_ascii_ranges,
    make_reconciler_claude_shim,
    plugin_registry_file,
    reconciler_audit_lines,
    run_reconciler,
    scaled_shim_sleep,
    v2_registry_entry,
    write_plugin_registry,
    write_scaled_timeout_shim,
)

_BASH = shutil.which("bash") or "/bin/bash"


# Kept in sync with reconcile-project-plugins.sh's _RECONCILER_CREDENTIAL_DENYLIST.
_DENYLISTED_PLUGIN_ID = "issue-triage@claude-config"

# The shim's production cap for one `claude` call, in seconds (_RECONCILER_CLAUDE_CALL_CAP_SECONDS).
_CLAUDE_CALL_CAP_SECONDS = 30
# The default cap `_lib_capped` puts on the lock probe, in seconds.
_LOCK_PROBE_CAP_SECONDS = 5
# A bounded-cost run finishes within this multiple of a control run on the same machine.
# An unbounded sanitize or `##*@` pass over a hostile id costs orders of magnitude more.
_HOSTILE_ID_COST_RATIO_LIMIT = 10
# The size, in bytes, past which `_audit_log` empties the audit log before appending.
_LIB_SIZE_THRESHOLD_BYTES = int(_sourced_value("_LIB_SIZE_THRESHOLD_BYTES"))


# ---------------------------------------------------------------------------
# Fixtures: scratch repo, registry, and a `claude` PATH shim tailored to the
# reconciler's own two subcommands (marketplace list, plugin install).
# ---------------------------------------------------------------------------


# Everything the reconciler needs before its first `claude` call: sourcing _lib.sh, resolving $0, writing an audit line.
_PREFLIGHT_TOOLS = ["bash", "cat", "date", "dirname", "jq", "mkdir", "readlink", "rm", "sleep", "stat", "tr", "wc"]


def _preflight_path(
    tmp_path: Path, *, include_claude: bool, include_timeout: bool, extra_tools: list[str] | None = None
) -> Path:
    """A PATH carrying only the preflight tools and any `extra_tools`, plus a `claude` stub and the
    real `timeout` when asked, so a case removes exactly one of the two
    binaries the preflight checks for."""
    bin_dir = tmp_path / "preflight-bin"
    bin_dir.mkdir(exist_ok=True)
    tools = [*_PREFLIGHT_TOOLS, *(extra_tools or [])]
    if include_timeout:
        tools.append("timeout")
    for tool in tools:
        tool_path = shutil.which(tool)
        assert tool_path, f"{tool} not found on this machine's PATH"
        (bin_dir / tool).symlink_to(tool_path)
    if include_claude:
        claude_stub = bin_dir / "claude"
        claude_stub.write_text("#!/bin/sh\nexit 0\n")
        claude_stub.chmod(0o755)
    return bin_dir


def _run_with_exact_path(repo: Path, *, config_dir: Path, bin_dir: Path, home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_BASH, str(RECONCILE_SH), str(repo)],
        capture_output=True, text=True, timeout=30,
        env={"PATH": str(bin_dir), "HOME": str(home), "CLAUDE_CONFIG_DIR": str(config_dir)},
    )


def _locale_that_folds_ascii_capital_i_to_a_non_ascii_letter() -> str:
    """An installed UTF-8 locale (Turkish or Azeri) under which bash's `${var,,}` turns `I` into dotless `\u0131`,
    the exposure the denylist's `LC_ALL=C` pin removes."""
    return installed_locale_where(
        'folded="${1,,}"; [ "$folded" != "i" ]', "I",
        absent_message="no installed locale lower-cases ASCII I to a non-ASCII letter",
    )


def _launch_concurrently(repos: list[Path], *, config_dir: Path, bin_dir: Path, tmp_path: Path) -> None:
    """Start one reconciler per repo at once against one config dir and wait for all to exit 0."""
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CLAUDE_CONFIG_DIR": str(config_dir),
        **git_isolation_env(tmp_path),
    }
    procs = [
        subprocess.Popen(
            [_BASH, str(RECONCILE_SH), str(repo)], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        for repo in repos
    ]
    for proc in procs:
        _, err = proc.communicate(timeout=30)
        assert proc.returncode == 0, err


def _write_python3_wrapper_for_dash_c_calls(bin_dir: Path, *, on_dash_c: str) -> None:
    """A `python3` PATH wrapper that runs the shell snippet `on_dash_c` for a `python3 ... -c` call, then execs the
    real python3. The reconciler's lock probe is its only `-c` call, so the snippet targets that probe alone."""
    real_python3 = shutil.which("python3")
    assert real_python3, "python3 not found on this machine's PATH"
    wrapper = bin_dir / "python3"
    wrapper.write_text(
        "#!/bin/sh\n"
        'for arg in "$@"; do\n'
        f'  if [ "$arg" = "-c" ]; then {on_dash_c}; break; fi\n'
        "done\n"
        f'exec {shlex.quote(real_python3)} "$@"\n'
    )
    wrapper.chmod(0o755)


@contextlib.contextmanager
def _held_reconciler_lock(config_dir: Path):
    """Hold an exclusive flock on the reconciler lock file, as a running reconciler does.
    Raises BlockingIOError when another process already holds it."""
    lock_file = config_dir / "plugin-bootstrap" / "reconciler.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_file, "a") as lock_handle:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


# ---------------------------------------------------------------------------
# No-op paths
# ---------------------------------------------------------------------------

class TestNoOpPaths:
    def test_exit_zero_when_no_enabled_plugins(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        make_reconciler_claude_shim(bin_dir)

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert reconciler_audit_lines(config_dir) == []

    def test_exit_zero_when_everything_already_installed(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        write_plugin_registry(plugin_registry_file(config_dir), {"foo@bar": [v2_registry_entry(str(repo))]})
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(bin_dir, install_log=install_log)

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert reconciler_audit_lines(config_dir) == []
        assert install_log_entries(install_log) == []


class TestSettingsShape:
    def test_two_top_level_json_values_install_nothing_and_log_nothing(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={})
        (repo / ".claude" / "settings.json").write_text(
            '{"enabledPlugins": {"foo@bar": true}} {"enabledPlugins": {"baz@bar": true}}'
        )
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir, marketplace_entries=[{"name": "bar", "source": "directory"}], install_log=install_log
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert reconciler_audit_lines(config_dir) == []
        assert install_log_entries(install_log) == []

    def test_symlinked_settings_file_installs_nothing(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={})
        real_settings = tmp_path / "real-settings.json"
        real_settings.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        settings_file = repo / ".claude" / "settings.json"
        settings_file.unlink()
        settings_file.symlink_to(real_settings)
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir, marketplace_entries=[{"name": "bar", "source": "directory"}], install_log=install_log
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert install_log_entries(install_log) == []


class TestRegistryCorruption:
    def test_present_but_unparseable_registry_logs_and_exits_zero(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        registry_dir = config_dir / "plugins"
        registry_dir.mkdir(parents=True)
        (registry_dir / "installed_plugins.json").write_text("{not valid json")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(bin_dir, install_log=install_log)

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in reconciler_audit_lines(config_dir)]
        assert decisions == ["registry_unparseable_or_version_mismatch"]
        assert install_log_entries(install_log) == []


# ---------------------------------------------------------------------------
# Preflight: refuse before ever reaching a `claude` call.
# ---------------------------------------------------------------------------

class TestPreconditionRefusals:
    def test_claude_absent_from_path_exits_zero_and_logs_one_preflight_line(self, tmp_path: Path) -> None:
        """The real `timeout` is on PATH, so only the `claude` guard can refuse."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = _preflight_path(tmp_path, include_claude=False, include_timeout=True)

        result = _run_with_exact_path(repo, config_dir=config_dir, bin_dir=bin_dir, home=tmp_path / "home")

        assert result.returncode == 0, repr(result)
        [entry] = reconciler_audit_lines(config_dir)
        assert (entry["decision"], entry["outcome"], entry["plugin_id"]) == ("preflight_failed", "missing_claude", "-")

    def test_neither_timeout_nor_gtimeout_present_exits_zero_and_logs_one_preflight_line(
        self, tmp_path: Path
    ) -> None:
        """A `claude` stub is on PATH, so only the timeout guard can refuse."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = _preflight_path(tmp_path, include_claude=True, include_timeout=False)

        result = _run_with_exact_path(repo, config_dir=config_dir, bin_dir=bin_dir, home=tmp_path / "home")

        assert result.returncode == 0, repr(result)
        [entry] = reconciler_audit_lines(config_dir)
        assert (entry["decision"], entry["outcome"]) == ("preflight_failed", "missing_timeout_and_gtimeout")

    def test_sha256sum_absent_exits_zero_and_logs_one_preflight_line(self, tmp_path: Path) -> None:
        """`claude` and `timeout` are on PATH, so only the digest guard can refuse.
        Without `sha256sum` no failed install is stamped."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = _preflight_path(tmp_path, include_claude=True, include_timeout=True)

        result = _run_with_exact_path(repo, config_dir=config_dir, bin_dir=bin_dir, home=tmp_path / "home")

        assert result.returncode == 0, repr(result)
        [entry] = reconciler_audit_lines(config_dir)
        assert (entry["decision"], entry["outcome"], entry["plugin_id"]) == ("preflight_failed", "missing_sha256sum", "-")

    def test_all_required_binaries_present_does_not_log_a_preflight_line(self, tmp_path: Path) -> None:
        """Control for the three refusals above: with `claude`, `timeout`, and `sha256sum` present the guards pass."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = _preflight_path(tmp_path, include_claude=True, include_timeout=True)
        for tool in ("git", "sha256sum", "awk", "touch"):
            (bin_dir / tool).symlink_to(shutil.which(tool))

        result = _run_with_exact_path(repo, config_dir=config_dir, bin_dir=bin_dir, home=tmp_path / "home")

        assert result.returncode == 0, repr(result)
        # The exact PATH has no `python3`, so the run ends at the lock step, past every preflight guard.
        assert [line["decision"] for line in reconciler_audit_lines(config_dir)] == ["lock_unavailable"]

    def test_uncreatable_bootstrap_directory_exits_zero_without_a_log(self, tmp_path: Path) -> None:
        """A directory that cannot be created leaves nowhere to log, so the run exits silently."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        (config_dir / "plugin-bootstrap").write_text("a file where the directory should be")
        bin_dir = _preflight_path(tmp_path, include_claude=False, include_timeout=True)

        result = _run_with_exact_path(repo, config_dir=config_dir, bin_dir=bin_dir, home=tmp_path / "home")

        assert result.returncode == 0, repr(result)
        assert result.stderr == ""
        assert (config_dir / "plugin-bootstrap").read_text() == "a file where the directory should be"


# ---------------------------------------------------------------------------
# Plugin-id re-validation at the reconciler's own point of use.
# ---------------------------------------------------------------------------

class TestPluginIdRevalidation:
    def test_invalid_id_is_skipped_and_valid_sibling_still_installs(self, tmp_path: Path) -> None:
        """A hand-run or stale-hook invocation could carry an id that never
        passed validation -- the reconciler must still catch it, independent
        of whatever validation the hook already did, without derailing a
        legitimate sibling id in the same run."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"-bad@bar": True, "foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        installed_ids = [entry["plugin"] for entry in install_log_entries(install_log)]
        assert installed_ids == ["foo@bar"]
        audit_entries = {line["plugin_id"]: line["decision"] for line in reconciler_audit_lines(config_dir)}
        assert audit_entries[f"invalid-id(len={len('-bad@bar')})"] == "m3_invalid_id_skip"
        assert audit_entries["foo@bar"] == "install_succeeded"


# ---------------------------------------------------------------------------
# Local opt-out: a repo's settings.local.json can veto an id; an unreadable one
# vetoes the whole repo.
# ---------------------------------------------------------------------------

class TestLocalOptOut:
    def test_local_false_id_is_not_installed_but_sibling_id_is(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True, "baz@bar": True})
        (repo / ".claude" / "settings.local.json").write_text(
            json.dumps({"enabledPlugins": {"foo@bar": False}})
        )
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert [entry["plugin"] for entry in install_log_entries(install_log)] == ["baz@bar"]
        audit_entries = {line["plugin_id"]: line["decision"] for line in reconciler_audit_lines(config_dir)}
        assert audit_entries == {"baz@bar": "install_succeeded"}

    def test_unreadable_local_settings_installs_nothing_and_logs_the_skip(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True, "baz@bar": True})
        (repo / ".claude" / "settings.local.json").write_text("{not valid json")
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in reconciler_audit_lines(config_dir)]
        assert decisions == ["m10_local_optout_unreadable"]
        assert install_log_entries(install_log) == []


# ---------------------------------------------------------------------------
# Marketplace precondition, source-type allowlist, credential denylist.
# ---------------------------------------------------------------------------

class TestMarketplacePrecondition:
    def test_skip_when_marketplace_not_registered(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "someone-else", "source": "directory"}],
            install_log=install_log,
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in reconciler_audit_lines(config_dir)]
        assert decisions == ["m4_marketplace_not_registered"]
        assert install_log_entries(install_log) == []

    def test_skip_all_when_marketplace_listing_exits_nonzero(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(bin_dir, marketplace_list_exit_code=1, install_log=install_log)

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in reconciler_audit_lines(config_dir)]
        assert decisions == ["m4_marketplace_list_failed"]
        assert install_log_entries(install_log) == []

    def test_claude_call_disables_the_git_terminal_prompt_and_leaves_askpass_alone(self, tmp_path: Path) -> None:
        """The recording shim exits 1, so the run skips on the failed listing after the shim has seen its env."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        env_log = tmp_path / "claude-env.log"
        claude_shim = bin_dir / "claude"
        claude_shim.write_text(
            "#!/bin/bash\n"
            f'echo "${{GIT_TERMINAL_PROMPT-unset}} ${{GIT_ASKPASS-unset}}" >> {shlex.quote(str(env_log))}\n'
            "exit 1\n"
        )
        claude_shim.chmod(0o755)

        result = run_reconciler(
            repo,
            config_dir=config_dir,
            bin_dir=bin_dir,
            extra_env={"GIT_TERMINAL_PROMPT": "1", "GIT_ASKPASS": "/ambient/git-askpass"},
        )

        assert result.returncode == 0, repr(result)
        assert env_log.read_text().splitlines() == ["0 /ambient/git-askpass"]
        decisions = [line["decision"] for line in reconciler_audit_lines(config_dir)]
        assert decisions == ["m4_marketplace_list_failed"]

    def test_skip_all_when_marketplace_listing_returns_empty_array(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(bin_dir, marketplace_entries=[], install_log=install_log)

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in reconciler_audit_lines(config_dir)]
        assert decisions == ["m4_marketplace_list_failed"]
        assert install_log_entries(install_log) == []

    def test_skip_all_when_marketplace_listing_output_is_unparseable(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(bin_dir, marketplace_raw_stdout="not json at all", install_log=install_log)

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in reconciler_audit_lines(config_dir)]
        assert decisions == ["m4_marketplace_list_failed"]
        assert install_log_entries(install_log) == []

    def test_skip_all_when_marketplace_listing_times_out(self, tmp_path: Path) -> None:
        """The shim outlasts the scaled 30s cap, so `_lib_capped_for`'s kill path, not a shim exiting
        nonzero, produces the skip. assert_cap_engaged names the cap value this call site used."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        write_scaled_timeout_shim(bin_dir)
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            marketplace_list_sleep_seconds=scaled_shim_sleep(_CLAUDE_CALL_CAP_SECONDS + 2),
            install_log=install_log,
        )

        with assert_cap_engaged(bin_dir, production_cap=_CLAUDE_CALL_CAP_SECONDS, command="claude"):
            result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir, timeout=60)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in reconciler_audit_lines(config_dir)]
        assert decisions == ["m4_marketplace_list_failed"]
        assert install_log_entries(install_log) == []

    def test_skip_when_only_near_miss_marketplace_names_are_registered(self, tmp_path: Path) -> None:
        """Membership is an exact, case-sensitive whole-name match: a registered name that contains
        the id's marketplace, extends it, or differs only in case does not admit `foo@bar`."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[
                {"name": "bar-extra", "source": "directory"},
                {"name": "Bar", "source": "directory"},
                {"name": "ba", "source": "directory"},
                {"name": "xbar", "source": "directory"},
            ],
            install_log=install_log,
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in reconciler_audit_lines(config_dir)]
        assert decisions == ["m4_marketplace_not_registered"]
        assert install_log_entries(install_log) == []

    def test_proceeds_when_the_exact_marketplace_name_sits_among_near_misses(self, tmp_path: Path) -> None:
        """Allow control for the near-miss case: the same list plus the exact name admits the install."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[
                {"name": "bar-extra", "source": "directory"},
                {"name": "Bar", "source": "directory"},
                {"name": "bar", "source": "directory"},
            ],
            install_log=install_log,
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert [entry["plugin"] for entry in install_log_entries(install_log)] == ["foo@bar"]

    @pytest.mark.parametrize(
        "source_value",
        ["url", "unrecognized-value", "", None, "git-subdir", "Git", "github-enterprise", "directory "],
        ids=[
            "url",
            "unrecognized",
            "empty-string",
            "missing-field",
            "git-subdir",
            "capitalized-git",
            "github-enterprise",
            "directory-trailing-space",
        ],
    )
    def test_skip_when_source_type_is_not_allowlisted(
        self, tmp_path: Path, source_value: str | None
    ) -> None:
        """Allowlist: proceed only for `github`/`directory`/`git`; every other
        value -- including an unrecognized one, an empty string, or the
        field missing outright -- takes the same fail-closed path, so a
        wrong or absent field name can never silently admit every
        marketplace the way a denylist would."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        entry = {"name": "bar"}
        if source_value is not None:
            entry["source"] = source_value
        make_reconciler_claude_shim(bin_dir, marketplace_entries=[entry], install_log=install_log)

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in reconciler_audit_lines(config_dir)]
        assert decisions == ["m4_source_type_not_allowed"]
        assert install_log_entries(install_log) == []

    @pytest.mark.parametrize("source_value", ["github", "directory", "git"])
    def test_proceeds_when_source_type_is_allowed(self, tmp_path: Path, source_value: str) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": source_value}],
            install_log=install_log,
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert [entry["plugin"] for entry in install_log_entries(install_log)] == ["foo@bar"]
        decisions = [line["decision"] for line in reconciler_audit_lines(config_dir)]
        assert decisions == ["install_succeeded"]

    @pytest.mark.parametrize("registered_first", ["good", "bad"])
    def test_a_refused_id_does_not_stop_the_ids_after_it_whatever_the_marketplace_list_order(
        self, tmp_path: Path, registered_first: str
    ) -> None:
        """One run mixes a failed install, an unregistered marketplace, a disallowed source type, and a
        success. Each id gets its own decision and only the two admitted ids reach `plugin install`."""
        repo = tmp_path / "repo"
        init_reconciler_repo(
            repo,
            enabled_plugins={"a-fails@good": True, "b@unregistered": True, "c@bad": True, "d-ok@good": True},
        )
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        marketplaces = {"good": "directory", "bad": "url"}
        ordered_names = [registered_first] + [name for name in marketplaces if name != registered_first]
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": name, "source": marketplaces[name]} for name in ordered_names],
            fail_install_ids=frozenset({"a-fails@good"}),
            install_log=install_log,
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert sorted(entry["plugin"] for entry in install_log_entries(install_log)) == ["a-fails@good", "d-ok@good"]
        decision_by_id = {line["plugin_id"]: line["decision"] for line in reconciler_audit_lines(config_dir)}
        assert decision_by_id == {
            "a-fails@good": "install_failed",
            "b@unregistered": "m4_marketplace_not_registered",
            "c@bad": "m4_source_type_not_allowed",
            "d-ok@good": "install_succeeded",
        }

    def test_credential_denylisted_id_is_skipped(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={_DENYLISTED_PLUGIN_ID: True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "claude-config", "source": "directory"}],
            install_log=install_log,
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in reconciler_audit_lines(config_dir)]
        assert decisions == ["m4_credential_denylist_skip"]
        assert install_log_entries(install_log) == []

    @pytest.mark.parametrize(
        "spelling",
        ["Issue-Triage@claude-config", "ISSUE-TRIAGE@Claude-Config"],
        ids=["name-half-capitalized", "name-half-uppercase"],
    )
    def test_credential_denylist_matches_ignoring_ascii_case(self, tmp_path: Path, spelling: str) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={spelling: True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": spelling.split("@")[1], "source": "directory"}],
            install_log=install_log,
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in reconciler_audit_lines(config_dir)]
        assert decisions == ["m4_credential_denylist_skip"]
        assert install_log_entries(install_log) == []

    def test_credential_denylist_matches_under_a_locale_that_folds_capital_i_to_a_non_ascii_letter(
        self, tmp_path: Path
    ) -> None:
        """The case fold runs under LC_ALL=C: unpinned, a Turkish or Azeri locale lower-cases `I` to dotless
        `\u0131`, so `Issue-Triage@claude-config` would no longer match the denylisted id."""
        spelling = "Issue-Triage@claude-config"
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={spelling: True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "claude-config", "source": "directory"}],
            install_log=install_log,
        )

        result = run_reconciler(
            repo,
            config_dir=config_dir,
            bin_dir=bin_dir,
            extra_env={"LC_ALL": _locale_that_folds_ascii_capital_i_to_a_non_ascii_letter()},
        )

        assert result.returncode == 0, repr(result)
        assert [line["decision"] for line in reconciler_audit_lines(config_dir)] == ["m4_credential_denylist_skip"]
        assert install_log_entries(install_log) == []

    def test_denylisted_name_from_another_marketplace_is_skipped(self, tmp_path: Path) -> None:
        """The denylist binds the plugin name, whichever marketplace carries it."""
        other_marketplace_id = "issue-triage@claude-config-two"
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={other_marketplace_id: True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "claude-config-two", "source": "directory"}],
            install_log=install_log,
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in reconciler_audit_lines(config_dir)]
        assert decisions == ["m4_credential_denylist_skip"]
        assert install_log_entries(install_log) == []

    def test_name_extending_the_denylisted_name_installs(self, tmp_path: Path) -> None:
        """Allow control: the denylist matches the whole name half, not a prefix."""
        longer_name_id = "issue-triage-two@claude-config"
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={longer_name_id: True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "claude-config", "source": "directory"}],
            install_log=install_log,
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert [entry["plugin"] for entry in install_log_entries(install_log)] == [longer_name_id]


class TestLocalFiltersRunBeforeMarketplaceListing:
    """Id validation, the credential denylist, and the failure stamp are local checks, so a run
    with no surviving candidate never spawns the `claude` CLI."""

    @pytest.mark.parametrize(
        "enabled_plugins",
        [
            {_DENYLISTED_PLUGIN_ID: True},
            {"-bad@bar": True},
            {_DENYLISTED_PLUGIN_ID: True, "-bad@bar": True},
        ],
        ids=["denylisted-only", "invalid-only", "denylisted-and-invalid"],
    )
    def test_run_with_no_surviving_candidate_never_lists_marketplaces(
        self, tmp_path: Path, enabled_plugins: dict
    ) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins=enabled_plugins)
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        listing_log = tmp_path / "listing.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}, {"name": "claude-config", "source": "directory"}],
            marketplace_list_log=listing_log,
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert not listing_log.exists(), "the marketplace listing ran with no candidate to check"
        assert len(reconciler_audit_lines(config_dir)) == len(enabled_plugins)

    def test_many_decisions_in_one_run_take_the_audit_lock_once_and_leave_stderr_clean(self, tmp_path: Path) -> None:
        """The audit-lock primitive may be called once per process, so a second attempt is a defect that shows on stderr."""
        repo = tmp_path / "repo"
        init_reconciler_repo(
            repo, enabled_plugins={_DENYLISTED_PLUGIN_ID: True, "-bad@bar": True, "-worse@bar": True}
        )
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        make_reconciler_claude_shim(bin_dir)

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert result.stderr == ""
        assert len(reconciler_audit_lines(config_dir)) == 3

    def test_stamped_id_is_suppressed_without_a_second_marketplace_listing(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        listing_log = tmp_path / "listing.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            fail_install_ids=frozenset({"foo@bar"}),
            marketplace_list_log=listing_log,
        )
        run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        second = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert second.returncode == 0, repr(second)
        assert [line["decision"] for line in reconciler_audit_lines(config_dir)] == ["install_failed", "m9_stamp_suppressed"]
        assert listing_log.read_text().count("list") == 1

    def test_one_surviving_candidate_among_filtered_ids_lists_marketplaces_once(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(
            repo,
            enabled_plugins={_DENYLISTED_PLUGIN_ID: True, "-bad@bar": True, "foo@bar": True, "baz@bar": True},
        )
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        listing_log = tmp_path / "listing.log"
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            marketplace_list_log=listing_log,
            install_log=install_log,
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert listing_log.read_text().count("list") == 1
        assert sorted(entry["plugin"] for entry in install_log_entries(install_log)) == ["baz@bar", "foo@bar"]


# ---------------------------------------------------------------------------
# Scope guard: `-s local` writes the repo's settings.local.json, so that file must be ignored and untracked.
# ---------------------------------------------------------------------------

class TestScopeGuard:
    def test_no_install_when_settings_local_not_gitignored(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True}, gitignore_settings_local=False)
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in reconciler_audit_lines(config_dir)]
        assert decisions == ["m6_not_ignored"]
        assert install_log_entries(install_log) == []

    def test_no_install_when_the_claude_directory_is_a_symlink(self, tmp_path: Path) -> None:
        """The settings guard checks only the final path component, so a symlinked `.claude`
        directory is read. git refuses a pathspec beyond a symlink, which keeps the write side closed."""
        repo = tmp_path / "repo"
        real_claude_dir = tmp_path / "real-claude-dir"
        real_claude_dir.mkdir()
        (real_claude_dir / "settings.json").write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        repo.mkdir()
        subprocess.run(
            ["git", "init", "-q"], cwd=repo, check=True, env={**os.environ, **git_isolation_env(tmp_path)}
        )
        (repo / ".gitignore").write_text(".claude/settings.local.json\n")
        (repo / ".claude").symlink_to(real_claude_dir)
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert [line["decision"] for line in reconciler_audit_lines(config_dir)] == ["m6_not_ignored"]
        assert install_log_entries(install_log) == []

    def test_no_install_when_settings_local_already_tracked(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        (repo / ".claude" / "settings.local.json").write_text("{}")
        subprocess.run(
            ["git", "add", "-f", ".claude/settings.local.json"], cwd=repo, check=True,
            env={**os.environ, **git_isolation_env(tmp_path)},
        )
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in reconciler_audit_lines(config_dir)]
        assert decisions == ["m6_already_tracked"]
        assert install_log_entries(install_log) == []

    def _init_package_inside_toplevel(self, tmp_path: Path) -> Path:
        """A package directory nested in a git toplevel that is neither tracking nor ignoring its own settings.local.json."""
        top = tmp_path / "top"
        init_reconciler_repo(top, enabled_plugins={}, gitignore_settings_local=False)
        package_dir = top / "pkg"
        (package_dir / ".claude").mkdir(parents=True)
        (package_dir / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        return package_dir

    def test_no_install_when_package_settings_local_is_tracked_below_the_git_toplevel(self, tmp_path: Path) -> None:
        package_dir = self._init_package_inside_toplevel(tmp_path)
        (package_dir / ".claude" / "settings.local.json").write_text("{}")
        subprocess.run(
            ["git", "add", "-f", "pkg/.claude/settings.local.json"], cwd=package_dir.parent, check=True,
            env={**os.environ, **git_isolation_env(tmp_path)},
        )
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )

        result = run_reconciler(package_dir, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        decisions = [line["decision"] for line in reconciler_audit_lines(config_dir)]
        assert decisions == ["m6_already_tracked"]
        assert install_log_entries(install_log) == []

    def test_installs_when_package_gitignore_ignores_its_settings_local_below_the_git_toplevel(
        self, tmp_path: Path
    ) -> None:
        package_dir = self._init_package_inside_toplevel(tmp_path)
        (package_dir / ".gitignore").write_text(".claude/settings.local.json\n")
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )

        result = run_reconciler(package_dir, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        entries = install_log_entries(install_log)
        assert [entry["plugin"] for entry in entries] == ["foo@bar"]
        assert entries[0]["cwd"] == str(package_dir.resolve())


# ---------------------------------------------------------------------------
# Hash-keyed failure stamp.
# ---------------------------------------------------------------------------

class TestFailureStamp:
    def test_failed_install_is_stamped_and_suppressed_until_settings_change(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            fail_install_ids=frozenset({"foo@bar"}),
            install_log=install_log,
        )

        first = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)
        assert first.returncode == 0, repr(first)
        assert len(install_log_entries(install_log)) == 1
        [failure_entry] = reconciler_audit_lines(config_dir)
        assert (failure_entry["decision"], failure_entry["outcome"]) == ("install_failed", "failure exit=1")
        assert len(list((config_dir / "plugin-bootstrap").glob("failure-stamp.*"))) == 1

        second = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)
        assert second.returncode == 0, repr(second)
        assert len(install_log_entries(install_log)) == 1, "a suppressed retry must not call install again"
        decisions = [line["decision"] for line in reconciler_audit_lines(config_dir)]
        assert decisions == ["install_failed", "m9_stamp_suppressed"]

        # Changing the repo's settings changes the stamp's hash, so a
        # once-failed id is retried rather than suppressed forever.
        (repo / ".claude" / "settings.json").write_text(
            json.dumps({"enabledPlugins": {"foo@bar": True, "extra-noop-key": False}})
        )
        third = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)
        assert third.returncode == 0, repr(third)
        assert len(install_log_entries(install_log)) == 2, "a settings change must lift the suppression"

    def test_id_skipped_before_its_install_is_not_stamped_and_installs_once_its_marketplace_is_listed(
        self, tmp_path: Path
    ) -> None:
        """Only a failed install stamps, so an id skipped for an unregistered marketplace is retried on
        unchanged settings while its failed sibling stays suppressed."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"failing@bar": True, "skipped@baz": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            fail_install_ids=frozenset({"failing@bar"}),
            install_log=install_log,
        )

        first = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert first.returncode == 0, repr(first)
        assert [(line["plugin_id"], line["decision"]) for line in reconciler_audit_lines(config_dir)] == [
            ("failing@bar", "install_failed"),
            ("skipped@baz", "m4_marketplace_not_registered"),
        ]
        assert len(list((config_dir / "plugin-bootstrap").glob("failure-stamp.*"))) == 1

        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}, {"name": "baz", "source": "directory"}],
            fail_install_ids=frozenset({"failing@bar"}),
            install_log=install_log,
        )
        second = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert second.returncode == 0, repr(second)
        assert [entry["plugin"] for entry in install_log_entries(install_log)] == ["failing@bar", "skipped@baz"]
        assert [(line["plugin_id"], line["decision"]) for line in reconciler_audit_lines(config_dir)[2:]] == [
            ("failing@bar", "m9_stamp_suppressed"),
            ("skipped@baz", "install_succeeded"),
        ]


# ---------------------------------------------------------------------------
# Install argv content and cap-kill classification.
# ---------------------------------------------------------------------------

class TestInstallArgv:
    def test_install_argv_uses_scope_local_and_omits_accept_flags(self, tmp_path: Path) -> None:
        """The argv sent to `claude plugin install` carries `-s local`, the id after `--`,
        and never `-y`, `--yes`, or `--accept-command`."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@claude-config": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "claude-config", "source": "directory"}],
            install_log=install_log,
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        [entry] = install_log_entries(install_log)
        assert entry["args"] == ["plugin", "install", "-s", "local", "--", "foo@claude-config"]

    def test_install_runs_under_the_original_umask_not_the_owner_only_one(self, tmp_path: Path) -> None:
        """The reconciler's own files are owner-only, but files the `claude` CLI writes keep the umask it was launched under."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir, marketplace_entries=[{"name": "bar", "source": "directory"}], install_log=install_log
        )

        with ambient_umask(0o022):
            result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        [entry] = install_log_entries(install_log)
        assert entry["umask"] == oct(0o022)

    def test_marketplace_listing_runs_under_the_original_umask_not_the_owner_only_one(self, tmp_path: Path) -> None:
        """Both `claude` spawns go through one wrapper, so the listing keeps the umask the reconciler was launched under too."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        listing_umask_log = tmp_path / "listing-umask.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            marketplace_list_umask_log=listing_umask_log,
        )

        with ambient_umask(0o022):
            result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert listing_umask_log.read_text().split() == [oct(0o022)]

    def test_every_install_in_a_multi_id_run_reads_stdin_from_dev_null(self, tmp_path: Path) -> None:
        """The reconciler's own stdin is a pipe here, so only an explicit redirect gives the CLI /dev/null."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"a@bar": True, "b@bar": True, "c@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir, marketplace_entries=[{"name": "bar", "source": "directory"}], install_log=install_log
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir, stdin_text="data on stdin")

        assert result.returncode == 0, repr(result)
        entries = install_log_entries(install_log)
        assert [entry["plugin"] for entry in entries] == ["a@bar", "b@bar", "c@bar"]
        assert [entry["stdin_is_dev_null"] for entry in entries] == [True, True, True]

    @pytest.mark.parametrize(
        ("marketplace_entries", "expected_install_ids"),
        [
            ([{"name": "bar", "source": "directory"}], ["ok@bar", "failing@bar"]),
            ([], []),
        ],
        ids=["listed-marketplace-with-failing-and-skipped-ids", "empty-listing"],
    )
    def test_reconciler_issues_only_a_marketplace_listing_and_local_installs(
        self, tmp_path: Path, marketplace_entries: list[dict], expected_install_ids: list[str]
    ) -> None:
        """Every `claude` call is one `plugin marketplace list --json` or one `plugin install -s local -- <id>`,
        so the reconciler never adds a marketplace, whether an install fails or an id is skipped."""
        repo = tmp_path / "repo"
        init_reconciler_repo(
            repo, enabled_plugins={"ok@bar": True, "failing@bar": True, "skipped@baz": True}
        )
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        call_log = tmp_path / "calls.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=marketplace_entries,
            fail_install_ids=frozenset({"failing@bar"}),
            call_log=call_log,
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        recorded_calls = [json.loads(line) for line in call_log.read_text().splitlines() if line]
        listing_call = ["plugin", "marketplace", "list", "--json"]
        install_calls = [["plugin", "install", "-s", "local", "--", plugin_id] for plugin_id in expected_install_ids]
        assert recorded_calls.count(listing_call) == 1
        assert sorted(call for call in recorded_calls if call != listing_call) == sorted(install_calls)


class TestInstallCapKill:
    def test_install_killed_by_the_cap_logs_its_status_and_writes_no_failure_stamp(self, tmp_path: Path) -> None:
        """A cap kill is likely transient, so it must not be stamped permanent: the next session retries."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        write_scaled_timeout_shim(bin_dir)
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
            install_sleep_seconds=scaled_shim_sleep(_CLAUDE_CALL_CAP_SECONDS + 2),
        )

        with assert_cap_engaged(bin_dir, production_cap=_CLAUDE_CALL_CAP_SECONDS, command="claude"):
            result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir, timeout=60)

        assert result.returncode == 0, repr(result)
        [entry] = reconciler_audit_lines(config_dir)
        assert entry["decision"] == "install_timed_out"
        assert entry["outcome"] in {f"failure exit={status}" for status in (124, 137, 143)}
        assert list((config_dir / "plugin-bootstrap").glob("failure-stamp.*")) == []

    def test_install_failing_on_its_own_still_writes_a_failure_stamp(self, tmp_path: Path) -> None:
        """Deny control for the cap-kill case: an ordinary non-zero exit is deterministic and stays stamped."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            fail_install_ids=frozenset({"foo@bar"}),
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        [entry] = reconciler_audit_lines(config_dir)
        assert (entry["decision"], entry["outcome"]) == ("install_failed", "failure exit=1")
        assert len(list((config_dir / "plugin-bootstrap").glob("failure-stamp.*"))) == 1


class TestRuntimeFilePermissions:
    def test_directory_and_files_are_owner_only_under_a_permissive_umask(self, tmp_path: Path) -> None:
        """The audit log records the absolute path of every repo opened, so group and world get no access."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"ok@bar": True, "broken@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            fail_install_ids=frozenset({"broken@bar"}),
        )

        with ambient_umask(0o022):
            result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        bootstrap_dir = config_dir / "plugin-bootstrap"
        [failure_stamp] = bootstrap_dir.glob("failure-stamp.*")
        assert file_mode(bootstrap_dir) == 0o700
        assert file_mode(bootstrap_dir / "audit.log") == 0o600
        assert file_mode(bootstrap_dir / ".reconciler-start") == 0o600
        assert file_mode(bootstrap_dir / "reconciler.lock") == 0o600
        assert file_mode(failure_stamp) == 0o600


# ---------------------------------------------------------------------------
# Invocation contract: the passed repo root wins, not process cwd; an
# adversarial-shaped path survives argv intact.
# ---------------------------------------------------------------------------

class TestInvocationContract:
    def test_installs_using_passed_repo_root_not_process_cwd(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir, cwd=elsewhere)

        assert result.returncode == 0, repr(result)
        entries = install_log_entries(install_log)
        assert len(entries) == 1
        # Real path, not `elsewhere`: `claude plugin install` has no
        # cwd-override flag, so the reconciler must `cd` into the resolved
        # repo root before invoking it.
        assert entries[0]["cwd"] == str(repo.resolve())

    def test_repo_root_with_space_and_metacharacter_survives_argv(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo dir $with metachar"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert [entry["plugin"] for entry in install_log_entries(install_log)] == ["foo@bar"]


# ---------------------------------------------------------------------------
# flock-based lock.
# ---------------------------------------------------------------------------

class TestLock:
    def test_second_invocation_exits_silently_when_lock_genuinely_held(self, tmp_path: Path) -> None:
        """A lock another process holds makes the run log `lock_held` and skip, with its own install never running."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )

        with _held_reconciler_lock(config_dir):
            result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert result.stdout == ""
        assert result.stderr == ""
        assert install_log_entries(install_log) == []
        [entry] = reconciler_audit_lines(config_dir)
        assert (entry["decision"], entry["outcome"]) == ("lock_held", "skipped")

    def test_lock_held_with_nothing_missing_still_logs_lock_held(self, tmp_path: Path) -> None:
        """The lock is taken before the registry is read, so a losing run logs the loss even with nothing to install."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        write_plugin_registry(plugin_registry_file(config_dir), {"foo@bar": [v2_registry_entry(str(repo))]})
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        make_reconciler_claude_shim(bin_dir)

        with _held_reconciler_lock(config_dir):
            result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert [line["decision"] for line in reconciler_audit_lines(config_dir)] == ["lock_held"]

    def test_lock_held_with_an_unparseable_registry_logs_lock_held_not_the_registry_decision(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        registry_file = plugin_registry_file(config_dir)
        registry_file.parent.mkdir(parents=True)
        registry_file.write_text("{not valid json")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        make_reconciler_claude_shim(bin_dir)

        with _held_reconciler_lock(config_dir):
            result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert [line["decision"] for line in reconciler_audit_lines(config_dir)] == ["lock_held"]

    def test_lock_held_with_unreadable_local_settings_logs_lock_held_not_the_optout_decision(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        (repo / ".claude" / "settings.local.json").write_text("{not valid json")
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        make_reconciler_claude_shim(bin_dir)

        with _held_reconciler_lock(config_dir):
            result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert [line["decision"] for line in reconciler_audit_lines(config_dir)] == ["lock_held"]

    def test_second_reconciler_launched_mid_install_logs_lock_held_and_installs_nothing(self, tmp_path: Path) -> None:
        """The first reconciler holds the lock across its install, so a real second one for the same
        config dir skips; a lock taken in a subshell, or on a descriptor python3 opens itself, would
        release before the install and let the second run install again."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        release_file = tmp_path / "release-install"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
            install_blocks_until_file_exists=release_file,
        )
        first_reconciler = subprocess.Popen(
            [_BASH, str(RECONCILE_SH), str(repo)],
            env={
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "CLAUDE_CONFIG_DIR": str(config_dir),
                **git_isolation_env(tmp_path),
            },
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            deadline = time.monotonic() + 30
            while not install_log_entries(install_log) and time.monotonic() < deadline:
                time.sleep(0.05)
            first_install_entries = install_log_entries(install_log)
            assert [entry["plugin"] for entry in first_install_entries] == ["foo@bar"], "the first install must be in flight"

            second_result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)
            installs_while_blocked = install_log_entries(install_log)
            audit_while_blocked = reconciler_audit_lines(config_dir)
        finally:
            release_file.touch()
            first_stdout, first_stderr = first_reconciler.communicate(timeout=30)

        assert second_result.returncode == 0, repr(second_result)
        assert [entry["plugin"] for entry in installs_while_blocked] == ["foo@bar"]
        assert [(line["decision"], line["outcome"]) for line in audit_while_blocked] == [("lock_held", "skipped")]
        assert first_reconciler.returncode == 0, first_stderr
        assert [entry["plugin"] for entry in install_log_entries(install_log)] == ["foo@bar"]
        assert [line["decision"] for line in reconciler_audit_lines(config_dir)] == ["lock_held", "install_succeeded"]

    def test_repo_planted_python_modules_in_cwd_and_pythonpath_are_never_imported_by_the_lock(self, tmp_path: Path) -> None:
        """The reconciler inherits the repo as its cwd, so the lock's python3 must not import a module planted there.
        The probe runs from the bootstrap directory, so the cwd half of the plant is also kept out by the `cd`.
        `fcntl` is a builtin on some Python builds and cannot be shadowed, so `sitecustomize` (which `site` imports
        from PYTHONPATH at startup) carries the `-I` check on those builds. It fires only for a `python3 -c`
        invocation, because the `claude` shim is also a python3 script and inherits the same PYTHONPATH.
        It reads `sys.orig_argv`, which needs Python 3.10 or newer; the repo floor is 3.11."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )
        decoy_dir = tmp_path / "decoy"
        decoy_dir.mkdir()
        import_marker = tmp_path / "decoy-fcntl-imported"
        plant_marker = f"open({str(import_marker)!r}, 'w').close()"
        (decoy_dir / "fcntl.py").write_text(plant_marker + "\n")
        (decoy_dir / "sitecustomize.py").write_text(f"import sys\nif '-c' in sys.orig_argv:\n    {plant_marker}\n")

        result = run_reconciler(
            repo, config_dir=config_dir, bin_dir=bin_dir, cwd=decoy_dir, extra_env={"PYTHONPATH": str(decoy_dir)}
        )

        assert result.returncode == 0, repr(result)
        assert not import_marker.exists(), "a planted module was imported"
        assert [entry["plugin"] for entry in install_log_entries(install_log)] == ["foo@bar"]
        assert [line["decision"] for line in reconciler_audit_lines(config_dir)] == ["install_succeeded"]

    @pytest.mark.parametrize(
        "leftover_lock_text",
        ["", "\n", "not-a-pid\tx\n", f"{os.getpid()}\tstale start time\n"],
        ids=["empty", "newline-only", "garbage", "live-pid-with-stale-stamp"],
    )
    def test_leftover_lock_file_with_any_content_does_not_block(self, tmp_path: Path, leftover_lock_text: str) -> None:
        """The flock is the lock, not the file, so a file left by a crashed run never blocks whatever it says."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bootstrap_dir = config_dir / "plugin-bootstrap"
        bootstrap_dir.mkdir(parents=True)
        (bootstrap_dir / "reconciler.lock").write_text(leftover_lock_text)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert [entry["plugin"] for entry in install_log_entries(install_log)] == ["foo@bar"]
        assert [line["decision"] for line in reconciler_audit_lines(config_dir)] == ["install_succeeded"]

    def test_lock_is_free_and_its_file_kept_after_a_normal_run(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        lock_file = config_dir / "plugin-bootstrap" / "reconciler.lock"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        make_reconciler_claude_shim(bin_dir, marketplace_entries=[{"name": "bar", "source": "directory"}])

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert lock_file.exists(), "the lock file is never unlinked"
        with _held_reconciler_lock(config_dir):
            pass

    def test_lock_held_by_a_sigkilled_process_is_free_for_the_next_run(self, tmp_path: Path) -> None:
        """The kernel drops a flock when its holder dies, so a SIGKILL leaves no stale lock to clear."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        lock_file = config_dir / "plugin-bootstrap" / "reconciler.lock"
        lock_file.parent.mkdir(parents=True)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )
        holder = subprocess.Popen(
            [
                sys.executable, "-I", "-c",
                "import fcntl, sys, time\n"
                "lock_handle = open(sys.argv[1], 'a')\n"
                "fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
                "print('locked', flush=True)\n"
                "time.sleep(60)",
                str(lock_file),
            ],
            stdout=subprocess.PIPE, text=True,
        )
        try:
            assert holder.stdout.readline().strip() == "locked"
            held_result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)
            holder.kill()
            holder.wait(timeout=30)
        finally:
            holder.kill()
            holder.stdout.close()
        released_result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert held_result.returncode == 0, repr(held_result)
        assert released_result.returncode == 0, repr(released_result)
        assert [entry["plugin"] for entry in install_log_entries(install_log)] == ["foo@bar"]
        assert [line["decision"] for line in reconciler_audit_lines(config_dir)] == ["lock_held", "install_succeeded"]

    @pytest.mark.parametrize("lingering_child_on", ["marketplace list", "install", "git"])
    def test_child_that_outlives_its_call_does_not_keep_the_lock(self, tmp_path: Path, lingering_child_on: str) -> None:
        """A `claude` or `git` call that leaves a detached child behind must not hand that child the lock descriptor."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        lingering_child_pid_file = tmp_path / "lingering-child.pid"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            lingering_child_pid_file=lingering_child_pid_file,
            lingering_child_on=lingering_child_on,
        )
        try:
            result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

            assert result.returncode == 0, repr(result)
            assert [line["decision"] for line in reconciler_audit_lines(config_dir)] == ["install_succeeded"]
            assert lingering_child_pid_file.exists(), f"the {lingering_child_on} call never left a child behind"
            os.kill(int(lingering_child_pid_file.read_text()), 0)
            try:
                with _held_reconciler_lock(config_dir):
                    pass
            except BlockingIOError:
                pytest.fail(f"a lingering child left by the {lingering_child_on} call kept the lock")
        finally:
            if lingering_child_pid_file.exists():
                with contextlib.suppress(ProcessLookupError):
                    os.kill(int(lingering_child_pid_file.read_text()), signal.SIGKILL)

    def test_lock_probe_runs_from_the_bootstrap_directory_not_the_repo(self, tmp_path: Path) -> None:
        """A version-manager shim for python3 reads .python-version from the cwd, so the probe must not run from the repo."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        make_reconciler_claude_shim(bin_dir, marketplace_entries=[{"name": "bar", "source": "directory"}])
        dash_c_cwd_log = tmp_path / "python3-dash-c-cwd.log"
        _write_python3_wrapper_for_dash_c_calls(bin_dir, on_dash_c=f"pwd -P >> {shlex.quote(str(dash_c_cwd_log))}")

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir, cwd=repo)

        assert result.returncode == 0, repr(result)
        assert dash_c_cwd_log.read_text().splitlines() == [str((config_dir / "plugin-bootstrap").resolve())]
        assert [line["decision"] for line in reconciler_audit_lines(config_dir)] == ["install_succeeded"]

    def test_lock_probe_that_hangs_is_killed_by_its_cap_and_logs_lock_unavailable(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        write_scaled_timeout_shim(bin_dir)
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir, marketplace_entries=[{"name": "bar", "source": "directory"}], install_log=install_log
        )
        _write_python3_wrapper_for_dash_c_calls(
            bin_dir, on_dash_c=f"exec sleep {scaled_shim_sleep(_LOCK_PROBE_CAP_SECONDS + 2)}"
        )

        with assert_cap_engaged(bin_dir, production_cap=_LOCK_PROBE_CAP_SECONDS, command="python3"):
            result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir, timeout=60)

        assert result.returncode == 0, repr(result)
        assert [line["decision"] for line in reconciler_audit_lines(config_dir)] == ["lock_unavailable"]
        assert install_log_entries(install_log) == []

    def test_lock_file_that_cannot_be_opened_logs_lock_unavailable_not_lock_held(self, tmp_path: Path) -> None:
        """A lock path that cannot be opened for append is not the same as another run holding the lock."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        (config_dir / "plugin-bootstrap" / "reconciler.lock").mkdir(parents=True)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert result.stderr == ""
        assert [line["decision"] for line in reconciler_audit_lines(config_dir)] == ["lock_unavailable"]
        assert install_log_entries(install_log) == []

    def test_lock_file_that_cannot_be_opened_with_nothing_missing_still_logs_lock_unavailable(self, tmp_path: Path) -> None:
        """The lock is attempted before the registry is read, so an unopenable lock path is logged with nothing to install."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        (config_dir / "plugin-bootstrap" / "reconciler.lock").mkdir(parents=True)
        write_plugin_registry(plugin_registry_file(config_dir), {"foo@bar": [v2_registry_entry(str(repo))]})
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        make_reconciler_claude_shim(bin_dir)

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert [line["decision"] for line in reconciler_audit_lines(config_dir)] == ["lock_unavailable"]

    def test_python3_that_fails_to_lock_logs_lock_unavailable_not_lock_held(self, tmp_path: Path) -> None:
        """A python3 that exits non-zero for any reason but contention, such as a filesystem without flock support."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )
        failing_python3 = bin_dir / "python3"
        failing_python3.write_text("#!/bin/sh\nexit 1\n")
        failing_python3.chmod(0o755)

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert [line["decision"] for line in reconciler_audit_lines(config_dir)] == ["lock_unavailable"]
        assert install_log_entries(install_log) == []

    def test_missing_python3_logs_lock_unavailable_not_lock_held(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = _preflight_path(
            tmp_path, include_claude=True, include_timeout=True, extra_tools=["awk", "grep", "head", "sha256sum"]
        )
        assert shutil.which("python3", path=str(bin_dir)) is None, "the exact PATH must carry no python3"

        result = _run_with_exact_path(repo, config_dir=config_dir, bin_dir=bin_dir, home=tmp_path)

        assert result.returncode == 0, repr(result)
        assert result.stderr == ""
        assert [line["decision"] for line in reconciler_audit_lines(config_dir)] == ["lock_unavailable"]

    def test_losing_contender_never_touches_the_marker(self, tmp_path: Path) -> None:
        """With the marker already in place from a prior lock-winning run, a
        second, losing invocation must not reset its mtime -- doing so would
        silently push out the wall-clock ceiling's baseline for whichever
        invocation actually holds the lock."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        marker_file = config_dir / "plugin-bootstrap" / ".reconciler-start"
        marker_file.parent.mkdir(parents=True)
        marker_file.write_text("")
        old_mtime = time.time() - 100
        os.utime(marker_file, (old_mtime, old_mtime))
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        make_reconciler_claude_shim(bin_dir, marketplace_entries=[{"name": "bar", "source": "directory"}])

        with _held_reconciler_lock(config_dir):
            result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert marker_file.stat().st_mtime == pytest.approx(old_mtime, abs=1)


# ---------------------------------------------------------------------------
# Total per-invocation wall-clock ceiling.
# ---------------------------------------------------------------------------

# Matches the 600s wall-clock ceiling, so a loaded runner's run duration cannot push a reset marker out of tolerance.
# Still far narrower than the 7200s offsets tested, so a reset marker remains distinguishable from a stale one.
_MARKER_RESET_TOLERANCE_SECONDS = 600


class TestWallClockCeiling:
    def test_ceiling_reached_skips_remaining_ids_without_an_actual_wait(self, tmp_path: Path) -> None:
        """The claude shim backdates the freshly-created marker file (via
        os.utime, mirroring test_advance_past_commit_stall.py's own idiom)
        as a side effect of the run's own marketplace-list call -- the
        first `claude` call after the marker is created -- so every id
        in this run sees an already-exceeded ceiling with no real 600s
        wait."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        marker_file = config_dir / "plugin-bootstrap" / ".reconciler-start"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
            backdate_marker=marker_file,
            backdate_marker_seconds_ago=700,
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert install_log_entries(install_log) == []
        decisions = [line["decision"] for line in reconciler_audit_lines(config_dir)]
        assert decisions == ["wall_clock_ceiling_reached"]

    @pytest.mark.parametrize("marker_offset_seconds", [-7200, 7200], ids=["aged", "future"])
    def test_run_that_wins_the_lock_resets_a_stale_marker_before_checking_the_ceiling(
        self, tmp_path: Path, marker_offset_seconds: int
    ) -> None:
        """A marker left by an earlier run, hours old or dated in the future, must not skip the install.
        Only the mtime assertion catches the future case: a future marker never reads as past the ceiling."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        marker_file = config_dir / "plugin-bootstrap" / ".reconciler-start"
        marker_file.parent.mkdir(parents=True)
        marker_file.write_text("")
        stale_mtime = time.time() + marker_offset_seconds
        os.utime(marker_file, (stale_mtime, stale_mtime))
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir, marketplace_entries=[{"name": "bar", "source": "directory"}], install_log=install_log
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert [entry["plugin"] for entry in install_log_entries(install_log)] == ["foo@bar"]
        decisions = [line["decision"] for line in reconciler_audit_lines(config_dir)]
        assert decisions == ["install_succeeded"]
        assert marker_file.stat().st_mtime == pytest.approx(time.time(), abs=_MARKER_RESET_TOLERANCE_SECONDS)

    @pytest.mark.parametrize("marker_change", ["backdate", "delete"])
    def test_ceiling_reached_mid_run_stops_after_the_in_flight_install_and_logs_one_line(
        self, tmp_path: Path, marker_change: str
    ) -> None:
        """The ceiling stays hit once hit, so a run that trips it between ids logs it once, for the
        next id, and stops iterating instead of paying a log append per remaining id. The shim changes
        the marker during the first install, so the ceiling trips after that install began."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"aaa@bar": True, "bbb@bar": True, "ccc@bar": True})
        config_dir = tmp_path / "cfg"
        marker_file = config_dir / "plugin-bootstrap" / ".reconciler-start"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
            install_changes_marker=(marker_file, marker_change),
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert [entry["plugin"] for entry in install_log_entries(install_log)] == ["aaa@bar"]
        assert [(line["plugin_id"], line["decision"]) for line in reconciler_audit_lines(config_dir)] == [
            ("aaa@bar", "install_succeeded"),
            ("bbb@bar", "wall_clock_ceiling_reached"),
        ]


# ---------------------------------------------------------------------------
# Audit-log fields: repo-controlled plugin ids reach the log unvalidated on
# m3_invalid_id_skip and wall_clock_ceiling_reached, so only an id that passes
# _lib_validate_plugin_id is logged as written.
# ---------------------------------------------------------------------------

# U+009B is the C1 control-sequence introducer; GNU `tr -d '[:cntrl:]'` leaves
# it in a UTF-8 locale. ESC is C0. U+00E9 is multibyte.
_C1_CSI = "\u009b"
_C0_ESC = "\x1b"
_MULTIBYTE_LETTER = "\u00e9"
_CONTROL_DENSE_FLOOD_SIZE = 6
_PLUGIN_ID_MAX_LENGTH = int(_sourced_value("_LIB_PLUGIN_ID_MAX_LENGTH"))

# Each case is the set of enabledPlugins keys; distinct lengths keep the logged markers distinct.
# These keys hold no control character, so they reach the reconciler and are logged as a length marker.
_HOSTILE_ID_SETS = {
    "200-kib-key": ["-" + "a" * (200 * 1024) + "@bar"],
    "multibyte-letters": [f"-bad{_MULTIBYTE_LETTER}\u65e5\u672c@bar"],
}
# A key with a control character is dropped by _lib_missing_plugin_ids, so it never reaches the reconciler or the log.
_CONTROL_CHARACTER_ID_SETS = {
    "c1-esc-del-multibyte": [f"-bad{_C1_CSI}{_C0_ESC}[31m{_MULTIBYTE_LETTER}\x7f@bar"],
    "newline": ["foo\nbar@baz"],
    "20000-control-character-flood": [
        "foo" + "\x01" * (20000 + offset) + "@bar" for offset in range(_CONTROL_DENSE_FLOOD_SIZE)
    ],
    "20000-c1-character-flood": [
        "foo" + _C1_CSI * (20000 + offset) + "@bar" for offset in range(_CONTROL_DENSE_FLOOD_SIZE)
    ],
}


def _assert_no_control_characters(field: str) -> None:
    offending = [hex(ord(char)) for char in field if unicodedata.category(char) == "Cc"]
    assert offending == [], f"control characters survived in {field!r}: {offending}"


@pytest.fixture(scope="module")
def max_length_id_run_seconds(tmp_path_factory) -> float:
    """The slowest of three runs over one maximum-length valid id: the cost
    baseline a hostile-id run is compared against as a ratio, so the bound
    holds on a loaded machine where an absolute one would not."""
    control_dir = tmp_path_factory.mktemp("max-length-id-control")
    max_length_id = "a@" + "b" * (_PLUGIN_ID_MAX_LENGTH - len("a@"))
    seconds = []
    for run_index in range(3):
        run_dir = control_dir / f"run-{run_index}"
        run_dir.mkdir()
        bin_dir = run_dir / "bin"
        bin_dir.mkdir()
        make_reconciler_claude_shim(bin_dir, marketplace_entries=[{"name": "someone-else", "source": "directory"}])
        init_reconciler_repo(run_dir / "repo", enabled_plugins={max_length_id: True})
        started = time.monotonic()
        result = run_reconciler(
            run_dir / "repo", config_dir=run_dir / "cfg", bin_dir=bin_dir, extra_env={"LC_ALL": "C"}
        )
        seconds.append(time.monotonic() - started)
        assert result.returncode == 0, repr(result)
    return max(seconds)


class TestAuditLogFields:
    @pytest.mark.parametrize("hostile_ids", list(_HOSTILE_ID_SETS.values()), ids=list(_HOSTILE_ID_SETS))
    @pytest.mark.timing
    def test_invalid_id_is_logged_as_a_length_marker_and_the_run_costs_about_a_control_run(
        self, tmp_path: Path, hostile_ids: list[str], max_length_id_run_seconds: float
    ) -> None:
        """An invalid id is repo-controlled text, so the log carries only its length.
        The run exits 0, processes the id after it, and costs within a fixed ratio of a max-length-id run."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={**dict.fromkeys(hostile_ids, True), "foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        make_reconciler_claude_shim(
            bin_dir, marketplace_entries=[{"name": "someone-else", "source": "directory"}]
        )

        started = time.monotonic()
        result = run_reconciler(
            repo, config_dir=config_dir, bin_dir=bin_dir, extra_env={"LC_ALL": "C"}
        )
        elapsed = time.monotonic() - started

        assert result.returncode == 0, repr(result)
        assert elapsed < max_length_id_run_seconds * _HOSTILE_ID_COST_RATIO_LIMIT, (
            f"run took {elapsed:.1f}s against a {max_length_id_run_seconds:.1f}s control"
        )
        raw_log = (config_dir / "plugin-bootstrap" / "audit.log").read_text(encoding="utf-8")
        for line in raw_log.splitlines():
            _assert_no_control_characters(line)
        decision_by_plugin_id = {
            entry["plugin_id"]: entry["decision"] for entry in reconciler_audit_lines(config_dir)
        }
        assert decision_by_plugin_id == {
            **{
                f"invalid-id(len={len(hostile_id.encode('utf-8'))})": "m3_invalid_id_skip"
                for hostile_id in hostile_ids
            },
            "foo@bar": "m4_marketplace_not_registered",
        }

    @pytest.mark.parametrize(
        "control_character_ids", list(_CONTROL_CHARACTER_ID_SETS.values()), ids=list(_CONTROL_CHARACTER_ID_SETS)
    )
    @pytest.mark.timing
    def test_key_with_a_control_character_never_reaches_the_log_and_the_run_costs_about_a_control_run(
        self, tmp_path: Path, control_character_ids: list[str], max_length_id_run_seconds: float
    ) -> None:
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={**dict.fromkeys(control_character_ids, True), "foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        make_reconciler_claude_shim(
            bin_dir, marketplace_entries=[{"name": "someone-else", "source": "directory"}]
        )

        started = time.monotonic()
        result = run_reconciler(
            repo, config_dir=config_dir, bin_dir=bin_dir, extra_env={"LC_ALL": "C"}
        )
        elapsed = time.monotonic() - started

        assert result.returncode == 0, repr(result)
        assert elapsed < max_length_id_run_seconds * _HOSTILE_ID_COST_RATIO_LIMIT, (
            f"run took {elapsed:.1f}s against a {max_length_id_run_seconds:.1f}s control"
        )
        raw_log = (config_dir / "plugin-bootstrap" / "audit.log").read_text(encoding="utf-8")
        for line in raw_log.splitlines():
            _assert_no_control_characters(line)
        assert [(entry["plugin_id"], entry["decision"]) for entry in reconciler_audit_lines(config_dir)] == [
            ("foo@bar", "m4_marketplace_not_registered")
        ]

    def test_wall_clock_ceiling_logs_the_pending_valid_id_and_an_invalid_id_logs_only_a_length_marker(
        self, tmp_path: Path
    ) -> None:
        """Local validation runs before the ceiling check, so an invalid id is logged as a
        length marker by its own skip and only a valid candidate reaches the ceiling line."""
        invalid_id = f"foo{_MULTIBYTE_LETTER}@bar"
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={invalid_id: True, "foo@bar": True})
        config_dir = tmp_path / "cfg"
        marker_file = config_dir / "plugin-bootstrap" / ".reconciler-start"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            backdate_marker=marker_file,
            backdate_marker_seconds_ago=700,
        )

        result = run_reconciler(
            repo, config_dir=config_dir, bin_dir=bin_dir, extra_env={"LC_ALL": "C"}
        )

        assert result.returncode == 0, repr(result)
        assert [(entry["plugin_id"], entry["decision"]) for entry in reconciler_audit_lines(config_dir)] == [
            (f"invalid-id(len={len(invalid_id.encode('utf-8'))})", "m3_invalid_id_skip"),
            ("foo@bar", "wall_clock_ceiling_reached"),
        ]

    def test_non_ascii_letter_id_is_invalid_even_under_a_locale_that_widens_bracket_ranges(
        self, tmp_path: Path
    ) -> None:
        """The id alphabet is ASCII-only only because the validator pins
        LC_ALL=C; under a widening locale an unpinned `[A-Za-z]` admits the
        accented letter and the id would be logged as written. N counts
        characters here because the ambient locale is UTF-8."""
        accented_id = f"caf{_MULTIBYTE_LETTER}@bar"
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={accented_id: True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        make_reconciler_claude_shim(
            bin_dir, marketplace_entries=[{"name": "bar", "source": "directory"}]
        )

        result = run_reconciler(
            repo, config_dir=config_dir, bin_dir=bin_dir,
            extra_env={"LC_ALL": locale_that_widens_ascii_ranges()},
        )

        assert result.returncode == 0, repr(result)
        [entry] = reconciler_audit_lines(config_dir)
        assert entry["decision"] == "m3_invalid_id_skip"
        assert entry["plugin_id"] == f"invalid-id(len={len(accented_id)})"

    @pytest.mark.timing
    def test_valid_shaped_id_over_the_max_length_is_invalid_and_the_run_costs_about_a_control_run(
        self, tmp_path: Path, max_length_id_run_seconds: float
    ) -> None:
        """A 96 KiB marketplace half is valid-shaped but past the length bound,
        so it is refused before the super-linear `##*@` removal runs."""
        long_id = "a@" + "b" * (96 * 1024)
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={long_id: True, "foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "bar", "source": "directory"}],
            install_log=install_log,
        )

        started = time.monotonic()
        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)
        elapsed = time.monotonic() - started

        assert result.returncode == 0, repr(result)
        assert elapsed < max_length_id_run_seconds * _HOSTILE_ID_COST_RATIO_LIMIT, (
            f"run took {elapsed:.1f}s against a {max_length_id_run_seconds:.1f}s control"
        )
        assert [entry["plugin"] for entry in install_log_entries(install_log)] == ["foo@bar"]
        decision_by_plugin_id = {
            entry["plugin_id"]: entry["decision"] for entry in reconciler_audit_lines(config_dir)
        }
        assert decision_by_plugin_id == {
            f"invalid-id(len={len(long_id)})": "m3_invalid_id_skip",
            "foo@bar": "install_succeeded",
        }

    def test_valid_id_is_logged_verbatim(self, tmp_path: Path) -> None:
        valid_id = "Foo.Bar_1-x@my-market.place_2"
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={valid_id: True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        make_reconciler_claude_shim(
            bin_dir, marketplace_entries=[{"name": "someone-else", "source": "directory"}]
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        [entry] = reconciler_audit_lines(config_dir)
        assert entry["plugin_id"] == valid_id
        assert entry["decision"] == "m4_marketplace_not_registered"

    def test_repo_root_control_characters_are_stripped_from_the_audit_line(self, tmp_path: Path) -> None:
        """The repo root is a directory name the repo's owner controls: C0 (ESC,
        newline) and C1 (U+009B) must not reach the shared log, and the entry
        must stay on one line."""
        repo = tmp_path / f"repo{_C0_ESC}[31m{_C1_CSI}\nend"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        make_reconciler_claude_shim(
            bin_dir, marketplace_entries=[{"name": "someone-else", "source": "directory"}]
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        raw_log = (config_dir / "plugin-bootstrap" / "audit.log").read_text(encoding="utf-8")
        [entry] = [json.loads(line) for line in raw_log.splitlines()]
        assert entry["decision"] == "m4_marketplace_not_registered"
        assert entry["repo_root"] == str(repo.parent / "repo[31mend")
        assert raw_log.count("\n") == 1

    def test_multibyte_repo_root_is_logged_intact(self, tmp_path: Path) -> None:
        repo = tmp_path / f"r{_MULTIBYTE_LETTER}po-\u65e5\u672c\u8a9e"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        make_reconciler_claude_shim(
            bin_dir, marketplace_entries=[{"name": "someone-else", "source": "directory"}]
        )

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        [entry] = reconciler_audit_lines(config_dir)
        assert entry["repo_root"] == str(repo)


# ---------------------------------------------------------------------------
# The shared, locked audit-log append survives concurrent writers.
# ---------------------------------------------------------------------------

class TestAuditLogConcurrency:
    def test_concurrent_reconcilers_on_different_repos_all_land_intact_and_distinct(self, tmp_path: Path) -> None:
        """A test-held lock makes every launch deterministically lose the
        reconciler lock and appends one `lock_held` line to the same audit log at once.
        `_lib_acquire_append_lock` is the append primitive they share."""
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir, marketplace_entries=[{"name": "bar", "source": "directory"}], install_log=install_log
        )
        repos = []
        for repo_name in ("repo-a", "repo-b"):
            repo = tmp_path / repo_name
            init_reconciler_repo(repo, enabled_plugins={f"{repo_name}@bar": True})
            repos.append(repo)

        with _held_reconciler_lock(config_dir):
            _launch_concurrently(repos, config_dir=config_dir, bin_dir=bin_dir, tmp_path=tmp_path)

        lines = reconciler_audit_lines(config_dir)
        assert sorted((line["decision"], line["repo_root"]) for line in lines) == sorted(
            ("lock_held", str(repo)) for repo in repos
        )
        assert install_log_entries(install_log) == []

    def test_lock_losers_on_the_same_repo_stay_distinct_via_pid(self, tmp_path: Path) -> None:
        """The scenario the `pid` field exists to disambiguate: three lock-losers on the *same*
        repo log an identical decision/outcome/repo_root/plugin_id, which would collapse under
        a whole-line dedup without `pid` making the lines byte-distinct."""
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        make_reconciler_claude_shim(bin_dir, marketplace_entries=[{"name": "bar", "source": "directory"}])
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})

        with _held_reconciler_lock(config_dir):
            _launch_concurrently([repo, repo, repo], config_dir=config_dir, bin_dir=bin_dir, tmp_path=tmp_path)

        lines = reconciler_audit_lines(config_dir)
        assert [line["decision"] for line in lines] == ["lock_held"] * 3
        assert {(line["repo_root"], line["plugin_id"], line["outcome"]) for line in lines} == {
            (str(repo), "-", "skipped")
        }
        assert len({line["pid"] for line in lines}) == 3, "identical-content lines must stay distinct via pid alone"


# ---------------------------------------------------------------------------
# Audit log's own size cap.
# ---------------------------------------------------------------------------

class TestAuditLogSizeCap:
    def test_audit_log_is_truncated_once_it_exceeds_the_size_threshold(self, tmp_path: Path) -> None:
        """_audit_log truncates the file before appending once it exceeds
        _LIB_SIZE_THRESHOLD_BYTES (5 MB, _lib.sh), rather than letting a
        shared, never-swept log grow without bound."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        make_reconciler_claude_shim(
            bin_dir,
            marketplace_entries=[{"name": "someone-else", "source": "directory"}],
            install_log=install_log,
        )
        audit_log = config_dir / "plugin-bootstrap" / "audit.log"
        audit_log.parent.mkdir(parents=True)
        oversized_bytes = _LIB_SIZE_THRESHOLD_BYTES + 1
        audit_log.write_text("x" * oversized_bytes)

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        assert audit_log.stat().st_size < oversized_bytes, (
            "the oversized pre-existing content must be truncated away, not appended to"
        )
        lines = reconciler_audit_lines(config_dir)
        assert len(lines) == 1
        assert lines[0]["decision"] == "m4_marketplace_not_registered"

    def test_audit_log_past_size_cap_is_not_truncated_when_another_writer_holds_the_lock(self, tmp_path: Path) -> None:
        """Only the lock holder truncates: a live holder makes the bounded retries
        run out, and the reconciler then appends without truncating."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        make_reconciler_claude_shim(
            bin_dir, marketplace_entries=[{"name": "someone-else", "source": "directory"}]
        )
        audit_log = config_dir / "plugin-bootstrap" / "audit.log"
        audit_log.parent.mkdir(parents=True)
        oversized_line = "x" * (_LIB_SIZE_THRESHOLD_BYTES + 1)
        audit_log.write_text(oversized_line + "\n")
        lock_file = audit_log.parent / "audit.log.lock"
        lock_file.write_text(f"{os.getpid()}\n")

        result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)

        assert result.returncode == 0, repr(result)
        logged_lines = audit_log.read_text().splitlines()
        assert logged_lines[0] == oversized_line
        assert json.loads(logged_lines[-1])["decision"] == "m4_marketplace_not_registered"
        assert lock_file.read_text().strip() == str(os.getpid()), "the reconciler must not release a lock it does not hold"

    @pytest.mark.skipif(os.geteuid() == 0, reason="root writes a read-only file")
    def test_failed_truncation_and_append_on_a_read_only_oversized_audit_log_keeps_the_run_clean(
        self, tmp_path: Path
    ) -> None:
        """A failing truncate or append must not abort the run or leak the shell's redirection error to stderr."""
        repo = tmp_path / "repo"
        init_reconciler_repo(repo, enabled_plugins={"foo@bar": True})
        config_dir = tmp_path / "cfg"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        make_reconciler_claude_shim(
            bin_dir, marketplace_entries=[{"name": "someone-else", "source": "directory"}]
        )
        audit_log = config_dir / "plugin-bootstrap" / "audit.log"
        audit_log.parent.mkdir(parents=True)
        oversized_content = "x" * (_LIB_SIZE_THRESHOLD_BYTES + 1) + "\n"
        audit_log.write_text(oversized_content)
        audit_log.chmod(0o444)
        try:
            result = run_reconciler(repo, config_dir=config_dir, bin_dir=bin_dir)
        finally:
            audit_log.chmod(0o644)

        assert result.returncode == 0, repr(result)
        assert result.stderr == ""
        assert audit_log.read_text() == oversized_content
        assert (audit_log.parent / ".reconciler-start").exists(), "the run must reach its critical section"


# ---------------------------------------------------------------------------
# Runtime state must stay out of the public repo.
# ---------------------------------------------------------------------------

class TestRuntimeStateIsGitignored:
    """Under stow directory-fold, `<config-dir>/plugin-bootstrap/` can resolve inside this repo's
    `claude/.claude/`, and its audit log holds the absolute paths of private repos."""

    @pytest.mark.parametrize(
        "runtime_file",
        [
            "audit.log",
            "audit.log.lock",
            "reconciler.lock",
            ".reconciler-start",
            "failure-stamp.0123456789abcdef",
        ],
    )
    def test_runtime_file_under_plugin_bootstrap_is_ignored_by_git(self, tmp_path: Path, runtime_file: str) -> None:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "check-ignore", "-q", "--", f"claude/.claude/plugin-bootstrap/{runtime_file}"],
            env={**os.environ, **git_isolation_env(tmp_path)},
            capture_output=True, text=True, check=False,
        )

        assert result.returncode == 0, repr(result)

    def test_an_unrelated_path_beside_it_is_not_ignored(self, tmp_path: Path) -> None:
        """Control: check-ignore exits 1 for an untracked path no rule covers, so the exit 0 above is the rule's doing."""
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "check-ignore", "-q", "--", "claude/.claude/plugin-bootstrap-sibling/audit.log"],
            env={**os.environ, **git_isolation_env(tmp_path)},
            capture_output=True, text=True, check=False,
        )

        assert result.returncode == 1, repr(result)
