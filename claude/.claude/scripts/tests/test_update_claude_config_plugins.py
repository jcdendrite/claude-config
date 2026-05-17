"""Tests for update-claude-config-plugins.sh.

The claude CLI is replaced in every test by a PATH shim that returns canned
JSON for marketplace list / plugin list and records plugin update calls.
A fixture marketplace directory provides the plugin.json files the script
reads to determine latest versions.
"""
from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

_SCRIPT = Path(__file__).parent.parent / "update-claude-config-plugins.sh"


# ---------------------------------------------------------------------------
# Fixtures: fake marketplace directory
# ---------------------------------------------------------------------------

def _make_marketplace_dir(
    root: Path,
    plugins: list[dict],
) -> Path:
    """Create a fake claude-config marketplace directory at root.

    plugins is a list of dicts with keys:
      name        str   plugin name
      version     str   latest version
      description str   plugin description
      source      str   source subdir relative to root (default: "plugins/<name>")
    """
    marketplace_plugins = []
    for p in plugins:
        source = p.get("source", f"plugins/{p['name']}")
        marketplace_plugins.append({"name": p["name"], "source": f"./{source}"})

        plugin_dir = root / source / ".claude-plugin"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "plugin.json").write_text(json.dumps({
            "name": p["name"],
            "version": p["version"],
            "description": p.get("description", f"Description of {p['name']}"),
        }))

    manifest_dir = root / ".claude-plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "marketplace.json").write_text(json.dumps({
        "plugins": marketplace_plugins,
    }))
    return root


# ---------------------------------------------------------------------------
# Fixtures: fake claude CLI shim
# ---------------------------------------------------------------------------

def _make_claude_shim(
    bin_dir: Path,
    marketplace_location: Path,
    installed_plugins: list[dict],
    update_log: Path,
) -> None:
    """Write a fake claude executable to bin_dir.

    Handles:
      claude plugin marketplace list --json
      claude plugin marketplace update claude-config
      claude plugin list --json
      claude plugin update <plugin> --scope <scope>

    update_log records each "plugin update" invocation as a JSON line.
    """
    installed_json = json.dumps(installed_plugins)
    marketplace_list_json = json.dumps([{
        "name": "claude-config",
        "source": "directory",
        "installLocation": str(marketplace_location),
    }])

    shim = textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json, sys

        args = sys.argv[1:]

        def is_subcommand(*parts):
            return args[:len(parts)] == list(parts)

        if is_subcommand("plugin", "marketplace", "list") and "--json" in args:
            print({repr(marketplace_list_json)})
            sys.exit(0)

        if is_subcommand("plugin", "marketplace", "update", "claude-config"):
            sys.exit(0)

        if is_subcommand("plugin", "list") and "--json" in args:
            print({repr(installed_json)})
            sys.exit(0)

        if is_subcommand("plugin", "update") and len(args) >= 5:
            plugin_id = args[2]
            # args: plugin update <id> --scope <scope>
            scope_idx = args.index("--scope") if "--scope" in args else -1
            scope = args[scope_idx + 1] if scope_idx >= 0 and scope_idx + 1 < len(args) else ""
            with open({repr(str(update_log))}, "a") as f:
                f.write(json.dumps({{"plugin": plugin_id, "scope": scope}}) + "\\n")
            sys.exit(0)

        print(f"Unhandled: {{args}}", file=sys.stderr)
        sys.exit(1)
    """)

    shim_path = bin_dir / "claude"
    shim_path.write_text(shim)
    shim_path.chmod(0o755)


def _run_script(
    *args: str,
    bin_dir: Path,
    cwd: Path,
    stdin_data: bytes | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    return subprocess.run(
        [str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=env,
        input=stdin_data.decode() if stdin_data else None,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_update_log(update_log: Path) -> list[dict]:
    if not update_log.exists():
        return []
    return [json.loads(line) for line in update_log.read_text().splitlines() if line.strip()]


def _make_installed_plugin(
    name: str,
    version: str,
    scope: str = "project",
    project_path: str = "/fake/repo",
) -> dict:
    entry: dict = {
        "id": f"{name}@claude-config",
        "version": version,
        "scope": scope,
        "enabled": True,
        "installPath": f"/fake/cache/{name}/{version}",
    }
    if scope == "project":
        entry["projectPath"] = project_path
    return entry


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_reports_outdated_plugin(self, tmp_path: Path) -> None:
        marketplace = _make_marketplace_dir(
            tmp_path / "marketplace",
            [{"name": "my-plugin", "version": "2.0.0", "description": "A test plugin"}],
        )
        update_log = tmp_path / "updates.log"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_claude_shim(
            bin_dir,
            marketplace,
            [_make_installed_plugin("my-plugin", "1.0.0", project_path=str(repo))],
            update_log,
        )

        result = _run_script("--dry-run", bin_dir=bin_dir, cwd=repo)

        assert result.returncode == 0
        assert "my-plugin" in result.stdout
        assert "1.0.0" in result.stdout
        assert "2.0.0" in result.stdout
        assert _read_update_log(update_log) == []

    def test_shows_description(self, tmp_path: Path) -> None:
        marketplace = _make_marketplace_dir(
            tmp_path / "marketplace",
            [{"name": "p", "version": "1.1.0", "description": "Does useful things"}],
        )
        update_log = tmp_path / "updates.log"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_claude_shim(
            bin_dir, marketplace,
            [_make_installed_plugin("p", "1.0.0", project_path=str(repo))],
            update_log,
        )

        result = _run_script("--dry-run", bin_dir=bin_dir, cwd=repo)

        assert "Does useful things" in result.stdout

    def test_locally_ahead_dev_copy_not_flagged(self, tmp_path: Path) -> None:
        """An installed version newer than the marketplace must not appear as outdated."""
        marketplace = _make_marketplace_dir(
            tmp_path / "marketplace",
            [{"name": "dev-plugin", "version": "1.0.0"}],
        )
        update_log = tmp_path / "updates.log"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_claude_shim(
            bin_dir, marketplace,
            [_make_installed_plugin("dev-plugin", "2.0.0-dev", project_path=str(repo))],
            update_log,
        )

        result = _run_script("--dry-run", bin_dir=bin_dir, cwd=repo)

        assert result.returncode == 0
        assert "dev-plugin" not in result.stdout

    def test_all_current_exits_zero(self, tmp_path: Path) -> None:
        marketplace = _make_marketplace_dir(
            tmp_path / "marketplace",
            [{"name": "stable", "version": "1.5.0"}],
        )
        update_log = tmp_path / "updates.log"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_claude_shim(
            bin_dir, marketplace,
            [_make_installed_plugin("stable", "1.5.0", project_path=str(repo))],
            update_log,
        )

        result = _run_script("--dry-run", bin_dir=bin_dir, cwd=repo)

        assert result.returncode == 0
        assert "up to date" in result.stdout


class TestAssumeYes:
    def test_updates_all_outdated_plugins(self, tmp_path: Path) -> None:
        marketplace = _make_marketplace_dir(
            tmp_path / "marketplace",
            [
                {"name": "alpha", "version": "2.0.0"},
                {"name": "beta", "version": "3.1.0"},
            ],
        )
        update_log = tmp_path / "updates.log"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_claude_shim(
            bin_dir, marketplace,
            [
                _make_installed_plugin("alpha", "1.0.0", project_path=str(repo)),
                _make_installed_plugin("beta", "3.0.0", project_path=str(repo)),
            ],
            update_log,
        )

        result = _run_script("--yes", bin_dir=bin_dir, cwd=repo)

        assert result.returncode == 0
        calls = _read_update_log(update_log)
        assert len(calls) == 2
        assert any(c["plugin"] == "alpha@claude-config" for c in calls)
        assert any(c["plugin"] == "beta@claude-config" for c in calls)

    def test_passes_correct_scope_to_update(self, tmp_path: Path) -> None:
        marketplace = _make_marketplace_dir(
            tmp_path / "marketplace",
            [{"name": "shared", "version": "2.0.0"}],
        )
        update_log = tmp_path / "updates.log"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        home = tmp_path / "home"
        home.mkdir()
        _make_claude_shim(
            bin_dir, marketplace,
            [_make_installed_plugin("shared", "1.0.0", scope="user")],
            update_log,
        )

        result = _run_script("--yes", bin_dir=bin_dir, cwd=home)

        assert result.returncode == 0
        calls = _read_update_log(update_log)
        assert len(calls) == 1
        assert calls[0]["scope"] == "user"

    def test_prints_restart_reminder(self, tmp_path: Path) -> None:
        marketplace = _make_marketplace_dir(
            tmp_path / "marketplace",
            [{"name": "p", "version": "2.0.0"}],
        )
        update_log = tmp_path / "updates.log"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_claude_shim(
            bin_dir, marketplace,
            [_make_installed_plugin("p", "1.0.0", project_path=str(repo))],
            update_log,
        )

        result = _run_script("--yes", bin_dir=bin_dir, cwd=repo)

        assert "Restart Claude Code" in result.stdout


class TestNonTTY:
    def test_skips_updates_without_yes(self, tmp_path: Path) -> None:
        """Non-TTY stdin without --yes must not run any updates."""
        marketplace = _make_marketplace_dir(
            tmp_path / "marketplace",
            [{"name": "p", "version": "2.0.0"}],
        )
        update_log = tmp_path / "updates.log"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_claude_shim(
            bin_dir, marketplace,
            [_make_installed_plugin("p", "1.0.0", project_path=str(repo))],
            update_log,
        )

        # Passing stdin_data forces a non-TTY stdin in subprocess
        result = _run_script(bin_dir=bin_dir, cwd=repo, stdin_data=b"")

        assert result.returncode == 0
        assert _read_update_log(update_log) == []
        assert "Skipped" in result.stdout


class TestProjectScopeFiltering:
    def test_other_repos_project_plugins_excluded(self, tmp_path: Path) -> None:
        marketplace = _make_marketplace_dir(
            tmp_path / "marketplace",
            [{"name": "tool", "version": "2.0.0"}],
        )
        update_log = tmp_path / "updates.log"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        this_repo = tmp_path / "this-repo"
        this_repo.mkdir()
        other_repo = tmp_path / "other-repo"
        other_repo.mkdir()
        _make_claude_shim(
            bin_dir, marketplace,
            [_make_installed_plugin("tool", "1.0.0", project_path=str(other_repo))],
            update_log,
        )

        result = _run_script("--dry-run", bin_dir=bin_dir, cwd=this_repo)

        assert result.returncode == 0
        assert "tool" not in result.stdout

    def test_user_scope_plugins_always_included(self, tmp_path: Path) -> None:
        marketplace = _make_marketplace_dir(
            tmp_path / "marketplace",
            [{"name": "global-tool", "version": "2.0.0"}],
        )
        update_log = tmp_path / "updates.log"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        cwd = tmp_path / "any-dir"
        cwd.mkdir()
        _make_claude_shim(
            bin_dir, marketplace,
            [_make_installed_plugin("global-tool", "1.0.0", scope="user")],
            update_log,
        )

        result = _run_script("--dry-run", bin_dir=bin_dir, cwd=cwd)

        assert result.returncode == 0
        assert "global-tool" in result.stdout


class TestMarketplaceNotConfigured:
    def test_exits_nonzero_with_message(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()

        # Shim returns empty marketplace list (no claude-config entry)
        shim = textwrap.dedent("""\
            #!/usr/bin/env python3
            import json, sys
            args = sys.argv[1:]
            if args[:3] == ["plugin", "marketplace", "list"] and "--json" in args:
                print("[]")
                sys.exit(0)
            sys.exit(1)
        """)
        shim_path = bin_dir / "claude"
        shim_path.write_text(shim)
        shim_path.chmod(0o755)

        result = _run_script("--dry-run", bin_dir=bin_dir, cwd=tmp_path)

        assert result.returncode == 1
        assert "claude-config" in result.stderr.lower() or "not configured" in result.stderr.lower()


class TestBadArguments:
    def test_unknown_flag_exits_2(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()

        result = _run_script("--unknown-flag", bin_dir=bin_dir, cwd=tmp_path)

        assert result.returncode == 2
