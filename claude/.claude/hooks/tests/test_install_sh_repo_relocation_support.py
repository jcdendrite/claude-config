"""Tests for the repo-relocation additions to install.sh and
register-marketplace.sh: the ~/.claude-config-source manifest write plus the
real-file relocate-claude-config copy (install.sh), and the
marketplace-registration idempotency check's switch to comparing
canonicalized .path instead of just the name (register-marketplace.sh).
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

_INSTALL_SH = Path(__file__).resolve().parents[4] / "install.sh"
_REGISTER_MARKETPLACE_SH = (
    Path(__file__).resolve().parents[4] / "claude" / ".claude" / "scripts" / "register-marketplace.sh"
)
_BASH = shutil.which("bash") or "/bin/bash"

_MANIFEST_START = "# INSTALL_TEST_FIXTURE: repo-relocation-manifest — start\n"
_MANIFEST_END = "# INSTALL_TEST_FIXTURE: repo-relocation-manifest — end"

_MARKETPLACE_START = "# INSTALL_TEST_FIXTURE: repo-relocation-marketplace — start\n"
_MARKETPLACE_END = "# INSTALL_TEST_FIXTURE: repo-relocation-marketplace — end"


def _extract_block(start_marker: str, end_marker: str, source_file: Path = _INSTALL_SH) -> str:
    """Same marker-delimited extraction strategy as
    test_install_sh_continuity_hardening.py: syntax-matching the block would
    silently pick up unrelated logic (or drop a guard) whenever the block is
    reordered, and a test extracting the wrong text would still pass.
    """
    source_text = source_file.read_text()
    start = source_text.find(start_marker)
    assert start != -1, f"{start_marker!r} not found in {source_file}"
    end = source_text.find(end_marker, start)
    assert end != -1, f"{end_marker!r} not found after start marker in {source_file}"
    return source_text[start + len(start_marker) : end]


def _make_repo_dir(tmp_path: Path, name: str = "repo") -> Path:
    """A fake claude-config checkout holding the one file install.sh's
    manifest block actually copies: claude/.claude/scripts/relocate-claude-config.sh.
    """
    repo_dir = tmp_path / name
    script_dir = repo_dir / "claude" / ".claude" / "scripts"
    script_dir.mkdir(parents=True)
    (script_dir / "relocate-claude-config.sh").write_text("#!/bin/bash\necho canonical-source\n")
    return repo_dir


def _run_block(block: str, *, home: Path, repo_dir: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    """Run an extracted install.sh block with $HOME isolated and $REPO_DIR
    set, matching the variable install.sh's own top-level scope provides.
    `set -e` is prepended so the block runs under the same abort-on-error
    semantics install.sh itself uses (mirrors
    test_install_sh_continuity_hardening.py's `_run_hardening_block`).
    """
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["REPO_DIR"] = str(repo_dir)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [_BASH, "-c", "set -e\n" + block],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


class TestManifestAndSelfCopy:
    def test_manifest_written_with_repo_dir(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".local" / "bin").mkdir(parents=True)
        repo_dir = _make_repo_dir(tmp_path)

        result = _run_block(_extract_block(_MANIFEST_START, _MANIFEST_END), home=home, repo_dir=repo_dir)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        manifest = home / ".claude-config-source"
        assert manifest.read_text().strip() == str(repo_dir)

    def test_manifest_write_overwrites_stale_entry_idempotently(self, tmp_path: Path) -> None:
        """Re-running install.sh (e.g. after a relocation) must replace the
        old manifest line, not append a second one."""
        home = tmp_path / "home"
        (home / ".local" / "bin").mkdir(parents=True)
        (home / ".claude-config-source").write_text("/some/stale/old-path\n")
        repo_dir = _make_repo_dir(tmp_path)

        result = _run_block(_extract_block(_MANIFEST_START, _MANIFEST_END), home=home, repo_dir=repo_dir)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        lines = (home / ".claude-config-source").read_text().splitlines()
        assert lines == [str(repo_dir)]

    def test_relocate_claude_config_copied_as_real_executable_file(self, tmp_path: Path) -> None:
        """Real file copy (install -m 755), not a symlink — row2a: this
        wrapper's whole purpose is to keep working after the stow symlink
        chain it repairs has already broken, so it cannot itself be one."""
        home = tmp_path / "home"
        (home / ".local" / "bin").mkdir(parents=True)
        repo_dir = _make_repo_dir(tmp_path)

        result = _run_block(_extract_block(_MANIFEST_START, _MANIFEST_END), home=home, repo_dir=repo_dir)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        installed = home / ".local" / "bin" / "relocate-claude-config"
        assert installed.is_file()
        assert not installed.is_symlink()
        assert stat.S_IMODE(installed.stat().st_mode) == 0o755
        assert installed.read_text() == (repo_dir / "claude" / ".claude" / "scripts" / "relocate-claude-config.sh").read_text()


# ---------------------------------------------------------------------------
# Marketplace-registration idempotency
# ---------------------------------------------------------------------------
#
# The claude CLI is replaced by a PATH shim, matching this suite's existing
# test_update_claude_config_plugins.py precedent (stub only `claude`, run
# every other tool for real).


def _make_claude_marketplace_shim(
    bin_dir: Path,
    marketplace_entries: list[dict],
    remove_log: Path,
    add_log: Path,
) -> Path:
    """Handles only `plugin marketplace list --json`, `... remove <name>`,
    and `... add <path> --scope user` — the three subcommands the extracted
    marketplace block calls."""
    marketplace_list_json = json.dumps(marketplace_entries)
    shim = textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys

        args = sys.argv[1:]

        def is_subcommand(*parts):
            return args[:len(parts)] == list(parts)

        if is_subcommand("plugin", "marketplace", "list") and "--json" in args:
            print({marketplace_list_json!r})
            sys.exit(0)

        if is_subcommand("plugin", "marketplace", "remove"):
            with open({str(remove_log)!r}, "a") as f:
                f.write(args[3] + chr(10))
            sys.exit(0)

        if is_subcommand("plugin", "marketplace", "add"):
            with open({str(add_log)!r}, "a") as f:
                f.write(args[3] + chr(10))
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


def _run_marketplace_block(
    tmp_path: Path,
    repo_dir: Path,
    marketplace_entries: list[dict],
) -> tuple[subprocess.CompletedProcess, list[str], list[str]]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    remove_log = tmp_path / "remove.log"
    add_log = tmp_path / "add.log"
    _make_claude_marketplace_shim(bin_dir, marketplace_entries, remove_log, add_log)

    result = _run_block(
        _extract_block(_MARKETPLACE_START, _MARKETPLACE_END, _REGISTER_MARKETPLACE_SH),
        home=home,
        repo_dir=repo_dir,
        extra_env={"PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )
    return result, _read_log(remove_log), _read_log(add_log)


class TestMarketplaceRegistrationIdempotency:
    def test_not_registered_adds(self, tmp_path: Path) -> None:
        repo_dir = _make_repo_dir(tmp_path)

        result, removed, added = _run_marketplace_block(tmp_path, repo_dir, [])

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert removed == []
        assert added == [str(repo_dir)]
        assert "adding claude-config" in result.stdout

    def test_registered_at_correct_path_is_noop(self, tmp_path: Path) -> None:
        repo_dir = _make_repo_dir(tmp_path)
        entries = [{"name": "claude-config", "source": "directory", "path": str(repo_dir)}]

        result, removed, added = _run_marketplace_block(tmp_path, repo_dir, entries)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert removed == []
        assert added == []
        assert "already registered" in result.stdout

    def test_registered_at_stale_path_re_registers(self, tmp_path: Path) -> None:
        """The row3a defect this fix closes: a post-move registration whose
        recorded path no longer matches REPO_DIR must be removed and re-added,
        not reported as already registered."""
        repo_dir = _make_repo_dir(tmp_path, name="new-repo")
        stale_path = tmp_path / "old-repo-that-moved-away"
        entries = [{"name": "claude-config", "source": "directory", "path": str(stale_path)}]

        result, removed, added = _run_marketplace_block(tmp_path, repo_dir, entries)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert removed == ["claude-config"]
        assert added == [str(repo_dir)]
        assert "re-registering claude-config" in result.stdout

    def test_path_comparison_uses_canonicalized_form_not_raw_string_equality(
        self, tmp_path: Path
    ) -> None:
        """row3b: the recorded .path and REPO_DIR are compared via readlink
        -f on both sides, not byte-for-byte — a recorded path that resolves
        to the same real directory through a different (symlinked) spelling
        must still read as 'already registered', not thrash remove+re-add
        on every install.sh run."""
        repo_dir = _make_repo_dir(tmp_path, name="real-repo")
        symlinked_alias = tmp_path / "repo-alias"
        symlinked_alias.symlink_to(repo_dir)
        entries = [{"name": "claude-config", "source": "directory", "path": str(symlinked_alias)}]

        result, removed, added = _run_marketplace_block(tmp_path, repo_dir, entries)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert removed == []
        assert added == []
        assert "already registered" in result.stdout

    def test_non_directory_source_entry_ignored(self, tmp_path: Path) -> None:
        """A claude-config-named entry with no .path (e.g. a malformed or
        github-source registration) is treated as unregistered and gets a
        fresh directory-source add, rather than crashing on a missing field."""
        repo_dir = _make_repo_dir(tmp_path)
        entries = [{"name": "claude-config", "source": "github", "repo": "someone/claude-config"}]

        result, removed, added = _run_marketplace_block(tmp_path, repo_dir, entries)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert removed == []
        assert added == [str(repo_dir)]


# ---------------------------------------------------------------------------
# register-marketplace.sh: whole-script settings-file resolution and
# self-location
# ---------------------------------------------------------------------------
#
# The tests above extract only the marker-delimited self-registration block.
# The tests below run register-marketplace.sh in full — the settings-file
# resolution and self-location logic live outside that block, so they need
# the whole script under test, not just the extracted snippet.


def _make_claude_full_shim(
    bin_dir: Path,
    marketplace_entries: list[dict],
    marketplace_remove_log: Path,
    marketplace_add_log: Path,
    plugin_install_log: Path | None = None,
) -> Path:
    """Handles marketplace list/remove/add plus `plugin list --json` and
    `plugin install` — the full set of `claude` subcommands
    register-marketplace.sh's whole script invokes, unlike
    `_make_claude_marketplace_shim` which only covers the self-registration
    marker block. `plugin list --json` always reports no installed
    user-scope plugins. `plugin install` is a no-op that also logs the
    installed plugin id when `plugin_install_log` is given, so tests can
    assert on the `enabledPlugins` install loop; callers that only care
    about marketplace registration can omit it."""
    marketplace_list_json = json.dumps(marketplace_entries)
    install_log_repr = repr(str(plugin_install_log)) if plugin_install_log else "None"
    shim = textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys

        args = sys.argv[1:]
        install_log = {install_log_repr}

        def is_subcommand(*parts):
            return args[:len(parts)] == list(parts)

        if is_subcommand("plugin", "marketplace", "list") and "--json" in args:
            print({marketplace_list_json!r})
            sys.exit(0)

        if is_subcommand("plugin", "marketplace", "remove"):
            with open({str(marketplace_remove_log)!r}, "a") as f:
                f.write(args[3] + chr(10))
            sys.exit(0)

        if is_subcommand("plugin", "marketplace", "add"):
            with open({str(marketplace_add_log)!r}, "a") as f:
                f.write(args[3] + chr(10))
            sys.exit(0)

        if is_subcommand("plugin", "list") and "--json" in args:
            print("[]")
            sys.exit(0)

        if is_subcommand("plugin", "install"):
            if install_log:
                with open(install_log, "a") as f:
                    f.write(args[2] + chr(10))
            sys.exit(0)

        print("Unhandled: " + str(args), file=sys.stderr)
        sys.exit(1)
    """)
    shim_path = bin_dir / "claude"
    shim_path.write_text(shim)
    shim_path.chmod(0o755)
    return shim_path


def _run_register_marketplace_script(
    script_path: Path, *, home: Path, extra_env: dict | None = None
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HOME"] = str(home)
    env.pop("CLAUDE_CONFIG_DIR", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [_BASH, str(script_path)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


class TestSettingsFileResolution:
    """Invokes the real worktree path (no symlink), so self-location
    trivially resolves to the real checkout — these tests isolate
    $SETTINGS_FILE resolution, not self-location (see TestSelfLocation for
    that)."""

    def test_uses_home_settings_when_config_dir_unset(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "settings.json").write_text("{}\n")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        add_log = tmp_path / "add.log"
        remove_log = tmp_path / "remove.log"
        _make_claude_full_shim(bin_dir, [], remove_log, add_log)

        result = _run_register_marketplace_script(
            _REGISTER_MARKETPLACE_SH,
            home=home,
            extra_env={"PATH": f"{bin_dir}:{os.environ['PATH']}"},
        )

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert _read_log(add_log) != []

    def test_uses_config_dir_settings_when_set(self, tmp_path: Path) -> None:
        """CLAUDE_CONFIG_DIR replaces the whole ~/.claude directory, not just
        $HOME (https://code.claude.com/docs/en/claude-directory) — settings.json
        lives directly at its root, with no nested .claude/ segment."""
        home = tmp_path / "home"
        home.mkdir()
        profile_dir = tmp_path / "profile"
        profile_dir.mkdir(parents=True)
        (profile_dir / "settings.json").write_text("{}\n")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        add_log = tmp_path / "add.log"
        remove_log = tmp_path / "remove.log"
        _make_claude_full_shim(bin_dir, [], remove_log, add_log)

        result = _run_register_marketplace_script(
            _REGISTER_MARKETPLACE_SH,
            home=home,
            extra_env={
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "CLAUDE_CONFIG_DIR": str(profile_dir),
            },
        )

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert _read_log(add_log) != []

    def test_exits_cleanly_when_settings_file_absent_at_either_location(self, tmp_path: Path) -> None:
        """No settings.json under $HOME and $CLAUDE_CONFIG_DIR unset: exit 0
        with no marketplace calls, not an error — an unprovisioned profile
        is an expected, not exceptional, state."""
        home = tmp_path / "home"
        home.mkdir()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        add_log = tmp_path / "add.log"
        remove_log = tmp_path / "remove.log"
        _make_claude_full_shim(bin_dir, [], remove_log, add_log)

        result = _run_register_marketplace_script(
            _REGISTER_MARKETPLACE_SH,
            home=home,
            extra_env={"PATH": f"{bin_dir}:{os.environ['PATH']}"},
        )

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert _read_log(add_log) == []
        assert _read_log(remove_log) == []
        assert "nothing to register" in result.stdout

    def test_enabled_plugins_are_installed(self, tmp_path: Path) -> None:
        """The enabledPlugins install loop (relocated verbatim from
        install.sh, not new logic) had no coverage before this diff — the
        other TestSettingsFileResolution fixtures all write an empty `{}`
        settings.json, which only exercises this loop's empty-input branch."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "settings.json").write_text(
            json.dumps({"enabledPlugins": {"some-plugin@claude-config": True}})
        )
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        add_log = tmp_path / "add.log"
        remove_log = tmp_path / "remove.log"
        install_log = tmp_path / "install.log"
        _make_claude_full_shim(bin_dir, [], remove_log, add_log, install_log)

        result = _run_register_marketplace_script(
            _REGISTER_MARKETPLACE_SH,
            home=home,
            extra_env={"PATH": f"{bin_dir}:{os.environ['PATH']}"},
        )

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert _read_log(install_log) == ["some-plugin@claude-config"]

    def test_enabled_plugins_from_unregistered_marketplace_are_skipped(
        self, tmp_path: Path
    ) -> None:
        """A plugin sourced from a marketplace this run just skipped as
        non-portable (a directory source, per test_non_directory_source_entry_ignored's
        sibling check above) must not reach `claude plugin install` at all —
        that call is guaranteed to fail, and under set -e would abort the
        whole loop, silently skipping every enabledPlugins entry listed after
        it. Also proves the loop keeps going past the skip: a legitimate
        claude-config-sourced entry listed after it still installs."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "settings.json").write_text(
            json.dumps(
                {
                    "extraKnownMarketplaces": {
                        "workstation-setup": {"source": {"source": "directory"}}
                    },
                    "enabledPlugins": {
                        "pat-rotation@workstation-setup": True,
                        "some-plugin@claude-config": True,
                    },
                }
            )
        )
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        add_log = tmp_path / "add.log"
        remove_log = tmp_path / "remove.log"
        install_log = tmp_path / "install.log"
        _make_claude_full_shim(bin_dir, [], remove_log, add_log, install_log)

        result = _run_register_marketplace_script(
            _REGISTER_MARKETPLACE_SH,
            home=home,
            extra_env={"PATH": f"{bin_dir}:{os.environ['PATH']}"},
        )

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "pat-rotation@workstation-setup" not in _read_log(install_log)
        assert _read_log(install_log) == ["some-plugin@claude-config"]
        assert "not registered for this profile" in result.stdout


class TestSelfLocation:
    def test_resolves_repo_dir_through_symlink_not_symlink_directory(self, tmp_path: Path) -> None:
        """Mirrors what `stow` produces at
        ~/.claude/scripts/register-marketplace.sh: a symlink whose own
        directory (tmp_path here) is unrelated to the checkout. REPO_DIR —
        observable via the marketplace-add call's path argument — must
        resolve to the real checkout root through readlink -f, not to the
        symlink's own directory."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "settings.json").write_text("{}\n")

        symlinked_script = tmp_path / "register-marketplace.sh"
        symlinked_script.symlink_to(_REGISTER_MARKETPLACE_SH)

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        add_log = tmp_path / "add.log"
        remove_log = tmp_path / "remove.log"
        _make_claude_full_shim(bin_dir, [], remove_log, add_log)

        result = _run_register_marketplace_script(
            symlinked_script,
            home=home,
            extra_env={"PATH": f"{bin_dir}:{os.environ['PATH']}"},
        )

        real_repo_dir = _REGISTER_MARKETPLACE_SH.parents[3]
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert _read_log(add_log) == [str(real_repo_dir)]

    def test_refuses_to_register_when_resolved_repo_dir_lacks_marketplace_json(
        self, tmp_path: Path
    ) -> None:
        """Deny-path counterpart to the test above. readlink -f fully
        canonicalizes a symlink to its ultimate target, so a symlinked
        invocation can never actually land self-location on the wrong
        directory — the only way to exercise the legitimacy canary's failure
        mode is a plain (non-symlink) copy of the script placed outside a
        real checkout, mimicking what a miscalculated resolution would
        produce."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "settings.json").write_text("{}\n")

        fake_root = tmp_path / "not-a-real-checkout"
        fake_scripts_dir = fake_root / "claude" / ".claude" / "scripts"
        fake_scripts_dir.mkdir(parents=True)
        fake_script = fake_scripts_dir / "register-marketplace.sh"
        fake_script.write_text(_REGISTER_MARKETPLACE_SH.read_text())
        fake_script.chmod(0o755)

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        add_log = tmp_path / "add.log"
        remove_log = tmp_path / "remove.log"
        _make_claude_full_shim(bin_dir, [], remove_log, add_log)

        result = _run_register_marketplace_script(
            fake_script,
            home=home,
            extra_env={"PATH": f"{bin_dir}:{os.environ['PATH']}"},
        )

        assert result.returncode == 1
        assert _read_log(add_log) == []
        assert "self-location must have resolved incorrectly" in result.stderr
