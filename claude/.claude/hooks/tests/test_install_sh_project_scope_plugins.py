"""Tests for the project-scope plugin-install block in install.sh: matches
claude-config's own committed .claude/settings.json enabledPlugins against
already-installed project-scope plugins and installs whatever is missing.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import textwrap
from pathlib import Path

_INSTALL_SH = Path(__file__).resolve().parents[4] / "install.sh"
_BASH = shutil.which("bash") or "/bin/bash"

_MATCH_START = "# INSTALL_TEST_FIXTURE: project-plugin-match — start\n"
_MATCH_END = "# INSTALL_TEST_FIXTURE: project-plugin-match — end"

_INSTALL_START = "# INSTALL_TEST_FIXTURE: project-scope-plugin-install — start\n"
_INSTALL_END = "# INSTALL_TEST_FIXTURE: project-scope-plugin-install — end"


def _extract_block(start_marker: str, end_marker: str) -> str:
    """Same marker-delimited extraction strategy as
    test_install_sh_repo_relocation_support.py: syntax-matching the block
    would silently pick up unrelated logic (or drop a guard) whenever the
    block is reordered, and a test extracting the wrong text would still
    pass.
    """
    install_text = _INSTALL_SH.read_text()
    start = install_text.find(start_marker)
    assert start != -1, f"{start_marker!r} not found in {_INSTALL_SH}"
    end = install_text.find(end_marker, start)
    assert end != -1, f"{end_marker!r} not found after start marker in {_INSTALL_SH}"
    return install_text[start + len(start_marker) : end]


# ---------------------------------------------------------------------------
# Pure matching-function tests: _project_plugin_already_installed
# ---------------------------------------------------------------------------


def _run_match(repo_dir: Path, plugin_id: str, existing_tsv: str) -> subprocess.CompletedProcess:
    script = (
        "set -e\n"
        + _extract_block(_MATCH_START, _MATCH_END)
        + f"\n_project_plugin_already_installed {shlex.quote(plugin_id)} {shlex.quote(existing_tsv)}\n"
    )
    env = dict(os.environ)
    env["REPO_DIR"] = str(repo_dir)
    return subprocess.run(
        [_BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


class TestProjectPluginAlreadyInstalled:
    def test_matches_on_exact_id_and_path(self, tmp_path: Path) -> None:
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        existing_tsv = f"skill-management@claude-config\t{repo_dir}"

        result = _run_match(repo_dir, "skill-management@claude-config", existing_tsv)

        assert result.returncode == 0, f"stderr={result.stderr!r}"

    def test_no_match_on_right_id_wrong_path(self, tmp_path: Path) -> None:
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        other_repo = tmp_path / "other-repo"
        other_repo.mkdir()
        existing_tsv = f"skill-management@claude-config\t{other_repo}"

        result = _run_match(repo_dir, "skill-management@claude-config", existing_tsv)

        assert result.returncode == 1

    def test_no_match_on_wrong_id(self, tmp_path: Path) -> None:
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        existing_tsv = f"plugin-semver@claude-config\t{repo_dir}"

        result = _run_match(repo_dir, "skill-management@claude-config", existing_tsv)

        assert result.returncode == 1

    def test_canonicalization_normalizes_dotdot_path_to_match(self, tmp_path: Path) -> None:
        """A recorded projectPath spelled with a `..` segment that still
        resolves to REPO_DIR must be treated as a match, not compared
        byte-for-byte — mirrors the marketplace-registration idempotency
        check's own canonicalized-path comparison."""
        repo_dir = tmp_path / "repo"
        (repo_dir / "sub").mkdir(parents=True)
        noncanonical_path = repo_dir / "sub" / ".."
        existing_tsv = f"skill-management@claude-config\t{noncanonical_path}"

        result = _run_match(repo_dir, "skill-management@claude-config", existing_tsv)

        assert result.returncode == 0, f"stderr={result.stderr!r}"

    def test_dangling_path_falls_back_to_raw_string_comparison(self, tmp_path: Path) -> None:
        """A recorded projectPath that no longer exists on disk must not
        crash the match — it falls back to comparing the raw recorded
        string against REPO_DIR, same as the marketplace block's own
        dangling-target fallback."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        dangling_path = tmp_path / "moved-away-repo"
        existing_tsv = f"skill-management@claude-config\t{dangling_path}"

        result = _run_match(repo_dir, "skill-management@claude-config", existing_tsv)

        assert result.returncode == 1

        result_self = _run_match(dangling_path, "skill-management@claude-config", existing_tsv)

        assert result_self.returncode == 0, f"stderr={result_self.stderr!r}"

    def test_matches_a_later_row_after_a_non_matching_row(self, tmp_path: Path) -> None:
        """The scan must continue past a non-matching row rather than
        stopping at the first one — a single-row fixture can't tell an
        early-return-on-mismatch bug from correct behavior."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        other_repo = tmp_path / "other-repo"
        other_repo.mkdir()
        existing_tsv = (
            f"skill-management@claude-config\t{other_repo}\n"
            f"skill-management@claude-config\t{repo_dir}"
        )

        result = _run_match(repo_dir, "skill-management@claude-config", existing_tsv)

        assert result.returncode == 0, f"stderr={result.stderr!r}"


# ---------------------------------------------------------------------------
# Stubbed-CLI tests: the full install loop
# ---------------------------------------------------------------------------
#
# The claude CLI is replaced by a PATH shim, matching
# test_install_sh_repo_relocation_support.py's marketplace-registration
# precedent (stub only `claude`, run every other tool for real).


def _make_claude_plugin_shim(bin_dir: Path, project_plugins: list[dict], install_log: Path) -> Path:
    """Handles only `plugin list --json` and `plugin install <id> -s
    project` — the two subcommands the extracted install loop calls."""
    plugin_list_json = json.dumps(project_plugins)
    shim = textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys

        args = sys.argv[1:]

        def is_subcommand(*parts):
            return args[:len(parts)] == list(parts)

        if is_subcommand("plugin", "list") and "--json" in args:
            print({plugin_list_json!r})
            sys.exit(0)

        if is_subcommand("plugin", "install"):
            with open({str(install_log)!r}, "a") as f:
                f.write(args[2] + chr(10))
            sys.exit(0)

        print("Unhandled: " + str(args), file=sys.stderr)
        sys.exit(1)
    """)
    shim_path = bin_dir / "claude"
    shim_path.write_text(shim)
    shim_path.chmod(0o755)
    return shim_path


def _read_log(log: Path) -> list[str]:
    if not log.exists():
        return []
    return [line for line in log.read_text().splitlines() if line]


def _run_install_block(
    tmp_path: Path,
    repo_dir: Path,
    enabled_plugins: dict,
    project_plugins: list[dict],
) -> tuple[subprocess.CompletedProcess, list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    install_log = tmp_path / "install.log"
    _make_claude_plugin_shim(bin_dir, project_plugins, install_log)

    settings_file = repo_dir / ".claude" / "settings.json"
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps({"enabledPlugins": enabled_plugins}))

    script = (
        "set -e\n"
        f"REPO_DIR={shlex.quote(str(repo_dir))}\n"
        f"PROJECT_SETTINGS_FILE={shlex.quote(str(settings_file))}\n"
        + _extract_block(_MATCH_START, _MATCH_END)
        + _extract_block(_INSTALL_START, _INSTALL_END)
    )
    result = subprocess.run(
        [_BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )
    return result, _read_log(install_log)


class TestProjectScopePluginInstallLoop:
    def test_fresh_machine_installs_all_declared_plugins(self, tmp_path: Path) -> None:
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        enabled_plugins = {
            "skill-management@claude-config": True,
            "claude-hook-review@claude-config": True,
            "plugin-semver@claude-config": True,
        }

        result, installed = _run_install_block(tmp_path, repo_dir, enabled_plugins, [])

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert sorted(installed) == sorted(enabled_plugins)
        assert "→ installing" in result.stdout

    def test_already_installed_machine_installs_nothing(self, tmp_path: Path) -> None:
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        enabled_plugins = {"skill-management@claude-config": True}
        project_plugins = [
            {
                "id": "skill-management@claude-config",
                "scope": "project",
                "projectPath": str(repo_dir),
            }
        ]

        result, installed = _run_install_block(tmp_path, repo_dir, enabled_plugins, project_plugins)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert installed == []
        assert "✓ skill-management@claude-config (already installed)" in result.stdout

    def test_user_scope_entry_at_matching_path_is_not_treated_as_project_scope_match(
        self, tmp_path: Path
    ) -> None:
        """The `.scope == "project"` jq filter must actually be load-bearing
        — a same-id, same-path entry installed at a different scope must
        not read as an already-installed project-scope match."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        enabled_plugins = {"skill-management@claude-config": True}
        mixed_scope_plugins = [
            {
                "id": "skill-management@claude-config",
                "scope": "user",
                "projectPath": str(repo_dir),
            }
        ]

        result, installed = _run_install_block(tmp_path, repo_dir, enabled_plugins, mixed_scope_plugins)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert installed == ["skill-management@claude-config"]

    def test_empty_enabled_plugins_is_a_noop(self, tmp_path: Path) -> None:
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        result, installed = _run_install_block(tmp_path, repo_dir, {}, [])

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert installed == []

    def test_disabled_plugin_entry_is_not_installed(self, tmp_path: Path) -> None:
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        enabled_plugins = {"skill-management@claude-config": False}

        result, installed = _run_install_block(tmp_path, repo_dir, enabled_plugins, [])

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert installed == []

    def test_malformed_project_settings_json_warns_and_does_not_crash(self, tmp_path: Path) -> None:
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        settings_file = repo_dir / ".claude" / "settings.json"
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text("{ not valid json")

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        _make_claude_plugin_shim(bin_dir, [], install_log)

        script = (
            "set -e\n"
            f"REPO_DIR={shlex.quote(str(repo_dir))}\n"
            f"PROJECT_SETTINGS_FILE={shlex.quote(str(settings_file))}\n"
            + _extract_block(_MATCH_START, _MATCH_END)
            + _extract_block(_INSTALL_START, _INSTALL_END)
        )
        result = subprocess.run(
            [_BASH, "-c", script],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        )

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "could not parse enabledPlugins" in result.stderr
        assert _read_log(install_log) == []

    def test_malformed_plugin_list_json_warns_and_proceeds_as_if_none_installed(
        self, tmp_path: Path
    ) -> None:
        """`claude plugin list --json` emitting non-JSON stdout (a CLI
        failure, an auth hiccup, a stale CLI version) makes `jq` itself fail
        — since install.sh has no `pipefail`, it's jq's exit status, not
        claude's, that the `if !` guard actually catches. Must not abort the
        rest of install.sh under `set -e` — must warn and fall back to
        attempting installs for every declared plugin."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        enabled_plugins = {"skill-management@claude-config": True}

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        shim = textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import sys

            args = sys.argv[1:]

            def is_subcommand(*parts):
                return args[:len(parts)] == list(parts)

            if is_subcommand("plugin", "list") and "--json" in args:
                print("not valid json")
                sys.exit(1)

            if is_subcommand("plugin", "install"):
                with open({str(install_log)!r}, "a") as f:
                    f.write(args[2] + chr(10))
                sys.exit(0)

            sys.exit(1)
        """)
        shim_path = bin_dir / "claude"
        shim_path.write_text(shim)
        shim_path.chmod(0o755)

        settings_file = repo_dir / ".claude" / "settings.json"
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text(json.dumps({"enabledPlugins": enabled_plugins}))

        script = (
            "set -e\n"
            f"REPO_DIR={shlex.quote(str(repo_dir))}\n"
            f"PROJECT_SETTINGS_FILE={shlex.quote(str(settings_file))}\n"
            + _extract_block(_MATCH_START, _MATCH_END)
            + _extract_block(_INSTALL_START, _INSTALL_END)
        )
        result = subprocess.run(
            [_BASH, "-c", script],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        )

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "could not read installed project-scope plugins" in result.stderr
        assert _read_log(install_log) == ["skill-management@claude-config"]

    def test_single_install_failure_does_not_abort_remaining_plugins(self, tmp_path: Path) -> None:
        """The new loop's install calls must be non-fatal — see the plan's
        row6: a single network blip must not abort install.sh before it
        reaches later, unrelated setup steps."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        enabled_plugins = {
            "skill-management@claude-config": True,
            "claude-hook-review@claude-config": True,
        }

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        install_log = tmp_path / "install.log"
        shim = textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import sys

            args = sys.argv[1:]

            def is_subcommand(*parts):
                return args[:len(parts)] == list(parts)

            if is_subcommand("plugin", "list") and "--json" in args:
                print("[]")
                sys.exit(0)

            if is_subcommand("plugin", "install"):
                if args[2] == "skill-management@claude-config":
                    sys.exit(1)
                with open({str(install_log)!r}, "a") as f:
                    f.write(args[2] + chr(10))
                sys.exit(0)

            sys.exit(1)
        """)
        shim_path = bin_dir / "claude"
        shim_path.write_text(shim)
        shim_path.chmod(0o755)

        settings_file = repo_dir / ".claude" / "settings.json"
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text(json.dumps({"enabledPlugins": enabled_plugins}))

        script = (
            "set -e\n"
            f"REPO_DIR={shlex.quote(str(repo_dir))}\n"
            f"PROJECT_SETTINGS_FILE={shlex.quote(str(settings_file))}\n"
            + _extract_block(_MATCH_START, _MATCH_END)
            + _extract_block(_INSTALL_START, _INSTALL_END)
        )
        result = subprocess.run(
            [_BASH, "-c", script],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        )

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "failed to install skill-management@claude-config" in result.stderr
        assert _read_log(install_log) == ["claude-hook-review@claude-config"]

    def test_issue_triage_install_prints_credential_exposure_warning(self, tmp_path: Path) -> None:
        """`issue-triage` is the only `enabledPlugins` entry whose agent
        holds live `gh` credentials and unrestricted `Bash` — unlike its
        three lint/guardrail neighbors, its auto-install carries a
        distinguishing warning visible at the point a contributor accepts
        it (`ciso-reviewer` finding, `/ready-for-review` cumulative pass on
        PR #807)."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        enabled_plugins = {"issue-triage@claude-config": True}

        result, installed = _run_install_block(tmp_path, repo_dir, enabled_plugins, [])

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert installed == ["issue-triage@claude-config"]
        assert "live gh credentials and unrestricted Bash" in result.stdout

    def test_other_plugins_install_without_the_credential_exposure_warning(self, tmp_path: Path) -> None:
        """Deny-side of the above: the warning must not fire for the three
        zero-credential guardrail plugins it ships alongside."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        enabled_plugins = {
            "skill-management@claude-config": True,
            "claude-hook-review@claude-config": True,
            "plugin-semver@claude-config": True,
        }

        result, installed = _run_install_block(tmp_path, repo_dir, enabled_plugins, [])

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert sorted(installed) == sorted(enabled_plugins)
        assert "live gh credentials and unrestricted Bash" not in result.stdout
