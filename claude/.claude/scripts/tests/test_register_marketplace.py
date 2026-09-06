"""Tests for register-marketplace.sh: settings-file resolution and
self-location, driven by running the whole script against a `claude` PATH
shim.

test_install_sh_repo_relocation_support.py covers the same script's
marker-delimited self-registration block, which it extracts from
register-marketplace.sh and runs standalone. The logic exercised here lives
outside that block, so it needs the whole script under test.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

_REGISTER_MARKETPLACE_SH = Path(__file__).resolve().parent.parent / "register-marketplace.sh"
_BASH = shutil.which("bash") or "/bin/bash"


def _read_log(log: Path) -> list[str]:
    if not log.exists():
        return []
    return [line for line in log.read_text().splitlines() if line]


def _make_claude_full_shim(
    bin_dir: Path,
    marketplace_entries: list[dict],
    marketplace_remove_log: Path,
    marketplace_add_log: Path,
    plugin_install_log: Path | None = None,
) -> Path:
    """Handles marketplace list/remove/add plus `plugin list --json` and
    `plugin install` — the full set of `claude` subcommands
    register-marketplace.sh's whole script invokes. `plugin list --json`
    always reports no installed user-scope plugins. `plugin install` is a
    no-op that also logs the installed plugin id when `plugin_install_log` is
    given, so tests can assert on the `enabledPlugins` install loop; callers
    that only care about marketplace registration can omit it."""
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
    # CLAUDE_CONFIG_DIR is popped, not merely left alone: conftest.py's autouse
    # _isolate_transcript_corpus_lookups sets it for every test in this
    # directory, which would otherwise mask the config-dir-unset case below.
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

    def test_refuses_relative_config_dir(self, tmp_path: Path) -> None:
        """A relative CLAUDE_CONFIG_DIR names a different directory per
        invocation cwd. The refusal is what separates this case from the
        unprovisioned-profile case below: both leave the marketplace logs
        empty, and only the non-zero exit distinguishes a refusal from a
        clean no-op."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "settings.json").write_text("{}\n")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        add_log = tmp_path / "add.log"
        remove_log = tmp_path / "remove.log"
        # The refusal fires before the script's first `claude` call, so the
        # shim goes unexercised on the expected path — it is here to contain a
        # regression: without it a lost refusal would reach the real CLI, and
        # the empty logs below would no longer prove nothing was registered.
        _make_claude_full_shim(bin_dir, [], remove_log, add_log)

        result = _run_register_marketplace_script(
            _REGISTER_MARKETPLACE_SH,
            home=home,
            extra_env={
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "CLAUDE_CONFIG_DIR": "relative-profile-dir",
            },
        )

        assert result.returncode != 0
        assert _read_log(add_log) == []
        assert _read_log(remove_log) == []
        assert "names a different directory per invocation cwd" in result.stderr

    def test_refuses_when_home_empty_and_config_dir_unset(self, tmp_path: Path) -> None:
        """The resolver's other failure cause. It reaches the same abort, but
        by a separate message branch, so it needs its own assertion — a
        regression collapsing the two would otherwise go unnoticed."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        add_log = tmp_path / "add.log"
        remove_log = tmp_path / "remove.log"
        _make_claude_full_shim(bin_dir, [], remove_log, add_log)

        result = _run_register_marketplace_script(
            _REGISTER_MARKETPLACE_SH,
            home=tmp_path / "unused-home",
            extra_env={"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": ""},
        )

        assert result.returncode != 0
        assert _read_log(add_log) == []
        assert _read_log(remove_log) == []
        assert "$HOME is unset or empty" in result.stderr

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
        """The enabledPlugins install loop needs a settings.json that actually
        declares one — the other TestSettingsFileResolution fixtures all write
        an empty `{}`, which only exercises this loop's empty-input branch."""
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
        non-portable (a directory source) must not reach `claude plugin
        install` at all — that call is guaranteed to fail, and under set -e
        would abort the whole loop, silently skipping every enabledPlugins
        entry listed after it. Also proves the loop keeps going past the skip:
        a legitimate claude-config-sourced entry listed after it still
        installs."""
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

        # _REGISTER_MARKETPLACE_SH is .resolve()'d so this compares against the
        # same canonical form the script's own readlink -f emits — an
        # unresolved path would mismatch on any checkout reached via a symlink.
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

    def test_dangling_readlink_garbage_does_not_corrupt_repo_dir(self, tmp_path: Path) -> None:
        """A readlink -f that fails but still writes partial garbage to
        stdout (the documented BSD dangling-symlink bug) must not corrupt
        REPO_DIR via string concatenation -- same regression shape and fix
        as stow-packages.sh's own TestSelfLocationSurvivesReadlinkCorruption."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "settings.json").write_text("{}\n")

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        stub_readlink = bin_dir / "readlink"
        stub_readlink.write_text('#!/bin/sh\nprintf "%s" "/garbage-partial-path"\nexit 1\n')
        stub_readlink.chmod(0o755)
        add_log = tmp_path / "add.log"
        remove_log = tmp_path / "remove.log"
        _make_claude_full_shim(bin_dir, [], remove_log, add_log)

        result = _run_register_marketplace_script(
            _REGISTER_MARKETPLACE_SH,
            home=home,
            extra_env={"PATH": f"{bin_dir}:{os.environ['PATH']}"},
        )

        real_repo_dir = _REGISTER_MARKETPLACE_SH.parents[3]
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert _read_log(add_log) == [str(real_repo_dir)]
