"""Tests for the repo-relocation additions to install.sh: the
~/.claude-config-source manifest write plus the real-file
relocate-claude-config copy, and the marketplace-registration idempotency
check's switch to comparing canonicalized .path instead of just the name.
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
_BASH = shutil.which("bash") or "/bin/bash"

_MANIFEST_START = "# INSTALL_TEST_FIXTURE: repo-relocation-manifest — start\n"
_MANIFEST_END = "# INSTALL_TEST_FIXTURE: repo-relocation-manifest — end"

_MARKETPLACE_START = "# INSTALL_TEST_FIXTURE: repo-relocation-marketplace — start\n"
_MARKETPLACE_END = "# INSTALL_TEST_FIXTURE: repo-relocation-marketplace — end"


def _extract_block(start_marker: str, end_marker: str) -> str:
    """Same marker-delimited extraction strategy as
    test_install_sh_continuity_hardening.py: syntax-matching the block would
    silently pick up unrelated logic (or drop a guard) whenever the block is
    reordered, and a test extracting the wrong text would still pass.
    """
    install_text = _INSTALL_SH.read_text()
    start = install_text.find(start_marker)
    assert start != -1, f"{start_marker!r} not found in {_INSTALL_SH}"
    end = install_text.find(end_marker, start)
    assert end != -1, f"{end_marker!r} not found after start marker in {_INSTALL_SH}"
    return install_text[start + len(start_marker) : end]


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
        _extract_block(_MARKETPLACE_START, _MARKETPLACE_END),
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
