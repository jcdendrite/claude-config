"""Tests for relocate-claude-config.sh.

Exercises the primary and --repair flows against a fake claude-config
checkout and an isolated $HOME, using the REAL `stow`/`stow -D` binary
(not a stub) — stow's own symlink-adopt semantics are exactly what this
script's correctness depends on, and a fake stow would let a wrong working
directory or -t/-d targeting pass silently. Only the `claude` CLI is
stubbed on PATH, matching test_update_claude_config_plugins.py's precedent.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from helpers import link_bash_into

_SCRIPT = Path(__file__).resolve().parents[1] / "relocate-claude-config.sh"

# chmod 0o555 does not stop root from writing, so the unwritable-directory cases cannot fail as intended.
requires_non_root = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the read-only modes this case relies on")


# ---------------------------------------------------------------------------
# Fixtures: a fake claude-config checkout, real stow, and a claude CLI shim
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: Path, name: str = "repo") -> Path:
    """A fake claude-config checkout with just enough shape for the script's
    canary check (.claude-plugin/marketplace.json), its own copy of the
    script under test (what the final `install -m 755` self-copy step
    reads from), one hook file (per-entry symlink probe target), and one
    ~/.local/bin wrapper (exercises stow's second target tree), plus one
    claude-skills package entry (the package every run also re-links)."""
    repo = tmp_path / name
    hooks_dir = repo / "claude" / ".claude" / "hooks"
    scripts_dir = repo / "claude" / ".claude" / "scripts"
    local_bin_dir = repo / "claude" / ".local" / "bin"
    plugin_dir = repo / ".claude-plugin"
    skill_dir = repo / "claude-skills" / "skills" / "example"
    hooks_dir.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    local_bin_dir.mkdir(parents=True)
    plugin_dir.mkdir(parents=True)
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# example skill\n")

    (hooks_dir / "_lib.sh").write_text("# fake _lib.sh\n")
    shutil.copy2(_SCRIPT, scripts_dir / "relocate-claude-config.sh")
    (scripts_dir / "relocate-claude-config.sh").chmod(0o755)
    (local_bin_dir / "some-wrapper").write_text("#!/bin/bash\necho wrapper\n")
    (local_bin_dir / "some-wrapper").chmod(0o755)
    (plugin_dir / "marketplace.json").write_text('{"plugins": []}\n')
    return repo


def _stow(repo: Path, home: Path, *, with_claude_skills: bool = False) -> subprocess.CompletedProcess:
    """Stow `repo`'s claude package into `home`, per-entry form (both
    target directories pre-created, matching install.sh's own tree-fold
    guard). `with_claude_skills` also stows the claude-skills package into
    ~/.claude with plain stow, as install.sh does, so ~/.claude/skills is one
    folded link into `repo`."""
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".local" / "bin").mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["stow", "-v", "--adopt", "-t", str(home), "claude"],
        cwd=repo, check=True, capture_output=True, text=True, env=_script_env(home),
    )
    if with_claude_skills:
        # A real ~/.claude holds untracked state. With only stow-managed links
        # in it, `stow -D claude` refolds ~/.claude into a link to the
        # claude-skills package.
        (home / ".claude" / "untracked-state.txt").write_text("untracked\n")
        subprocess.run(
            ["stow", "-v", "-t", str(home / ".claude"), "claude-skills"],
            cwd=repo, check=True, capture_output=True, text=True, env=_script_env(home),
        )
    return result


def _stow_tree_folded(repo: Path, home: Path) -> subprocess.CompletedProcess:
    """Stow with ~/.claude deliberately NOT pre-created, so stow tree-folds
    it into a single symlink — the legacy form."""
    (home / ".local" / "bin").mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        ["stow", "-v", "--adopt", "-t", str(home), "claude"],
        cwd=repo, check=True, capture_output=True, text=True, env=_script_env(home),
    )


def _make_claude_shim(
    bin_dir: Path,
    marketplace_entries: list[dict],
    remove_log: Path,
    add_log: Path,
    fail_subcommands: frozenset[str] = frozenset(),
) -> None:
    """`fail_subcommands` names which of {"remove", "add"} should exit
    non-zero instead of logging -- for pinning sync_marketplace_registration's
    explicit error-handling around those calls."""
    marketplace_list_json = json.dumps(marketplace_entries)
    fail_subcommands_repr = repr(set(fail_subcommands))
    shim = textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys

        args = sys.argv[1:]
        fail_subcommands = {fail_subcommands_repr}

        def is_subcommand(*parts):
            return args[:len(parts)] == list(parts)

        if is_subcommand("plugin", "marketplace", "list") and "--json" in args:
            print({marketplace_list_json!r})
            sys.exit(0)

        if is_subcommand("plugin", "marketplace", "remove"):
            if "remove" in fail_subcommands:
                print("simulated remove failure", file=sys.stderr)
                sys.exit(1)
            with open({str(remove_log)!r}, "a") as f:
                f.write(args[3] + chr(10))
            sys.exit(0)

        if is_subcommand("plugin", "marketplace", "add"):
            if "add" in fail_subcommands:
                print("simulated add failure", file=sys.stderr)
                sys.exit(1)
            # The path is passed as `--scope user -- <path>` (row H) so it is
            # always the last argument, regardless of flag order.
            with open({str(add_log)!r}, "a") as f:
                f.write(args[-1] + chr(10))
            sys.exit(0)

        print("Unhandled: " + str(args), file=sys.stderr)
        sys.exit(1)
    """)
    shim_path = bin_dir / "claude"
    shim_path.write_text(shim)
    shim_path.chmod(0o755)


def _read_log(log: Path) -> list[str]:
    if not log.exists():
        return []
    return [line for line in log.read_text().splitlines() if line]


def _script_env(home: Path, *path_dirs: Path) -> dict[str, str]:
    """The environment for a run against the fake `home`, with `path_dirs`
    (the claude shim dir, the installed-copy dir) ahead of the real PATH."""
    env = dict(os.environ)
    env["HOME"] = str(home)
    if path_dirs:
        env["PATH"] = ":".join([*(str(path_dir) for path_dir in path_dirs), env["PATH"]])
    return env


def _run_script(*args: str, home: Path, bin_dir: Path | None = None) -> subprocess.CompletedProcess:
    env = _script_env(home, *([bin_dir] if bin_dir is not None else []))
    return subprocess.run(
        [str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _last_nonempty_line(text: str) -> str:
    """The last line holding anything: the closing text ends with a newline."""
    return [line for line in text.splitlines() if line.strip()][-1]


def _claude_shim(
    tmp_path: Path,
    marketplace_entries: list[dict] | None = None,
    fail_subcommands: frozenset[str] = frozenset(),
) -> tuple[Path, Path, Path]:
    """Build a bin_dir with the claude shim installed; return (bin_dir, remove_log, add_log)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    remove_log = tmp_path / "remove.log"
    add_log = tmp_path / "add.log"
    _make_claude_shim(bin_dir, marketplace_entries or [], remove_log, add_log, fail_subcommands=fail_subcommands)
    return bin_dir, remove_log, add_log


# ---------------------------------------------------------------------------
# Primary flow (repo hasn't moved yet)
# ---------------------------------------------------------------------------


class TestRelocatePrimaryFlow:
    def test_relocate_moves_repo_and_restows(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home)
        bin_dir, remove_log, add_log = _claude_shim(tmp_path)

        new_repo = home / "new-repo"  # under $HOME: no --allow-outside-home needed
        result = _run_script(str(new_repo), home=home, bin_dir=bin_dir)

        assert result.returncode == 0, result.stderr
        assert new_repo.is_dir()
        assert not old_repo.exists()
        assert (home / ".claude" / "hooks").resolve() == (new_repo / "claude" / ".claude" / "hooks").resolve()
        assert (home / ".local" / "bin" / "some-wrapper").resolve() == (
            new_repo / "claude" / ".local" / "bin" / "some-wrapper"
        ).resolve()
        assert (home / ".claude-config-source").read_text().strip() == str(new_repo)
        installed = home / ".local" / "bin" / "relocate-claude-config"
        assert installed.is_file() and not installed.is_symlink()
        assert _read_log(add_log) == [str(new_repo)]
        assert _read_log(remove_log) == []
        assert f"Done. This checkout now lives at: {new_repo}" in result.stdout
        assert "Reminders:" in result.stdout
        assert f"'cd {new_repo}' manually" in result.stdout
        assert "while other Claude Code sessions are active" in result.stdout
        assert "Not done by this command:" in result.stdout

    def test_relocate_uses_manifest_when_valid(self, tmp_path: Path) -> None:
        """The manifest-hit branch of resolve_current_repo_dir: a correct,
        pre-existing ~/.claude-config-source is used directly rather than
        falling through to the live-symlink probe."""
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home)
        (home / ".claude-config-source").write_text(f"{old_repo}\n")
        bin_dir, remove_log, add_log = _claude_shim(tmp_path)

        new_repo = home / "new-repo"
        result = _run_script(str(new_repo), home=home, bin_dir=bin_dir)

        assert result.returncode == 0, result.stderr
        assert new_repo.is_dir()
        assert not old_repo.exists()

    def test_relocate_falls_back_to_live_symlink_when_manifest_stale(self, tmp_path: Path) -> None:
        """A manifest pointing at a path that no longer exists as a real
        directory must not be trusted -- resolve_current_repo_dir falls
        through to the live ~/.claude symlink probe instead of failing."""
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home)
        (home / ".claude-config-source").write_text(f"{tmp_path / 'stale-manifest-target'}\n")
        bin_dir, remove_log, add_log = _claude_shim(tmp_path)

        new_repo = home / "new-repo"
        result = _run_script(str(new_repo), home=home, bin_dir=bin_dir)

        assert result.returncode == 0, result.stderr
        assert new_repo.is_dir()
        assert not old_repo.exists()
        assert (home / ".claude-config-source").read_text().strip() == str(new_repo)

    def test_relocate_reregisters_marketplace_when_previously_registered_elsewhere(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home)
        stale_path = str(tmp_path / "some-other-old-location")
        bin_dir, remove_log, add_log = _claude_shim(
            tmp_path, [{"name": "claude-config", "source": "directory", "path": stale_path}]
        )

        new_repo = home / "new-repo"
        result = _run_script(str(new_repo), home=home, bin_dir=bin_dir)

        assert result.returncode == 0, result.stderr
        assert _read_log(remove_log) == ["claude-config"]
        assert _read_log(add_log) == [str(new_repo)]


class TestRelocateNoLocatableRepo:
    def test_errors_actionably_when_manifest_missing_and_no_live_symlink(self, tmp_path: Path) -> None:
        """No manifest, no stow-managed ~/.claude symlink — nothing tells
        the script where the current checkout lives. It must error with an
        actionable message, not proceed on a guess."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)  # real, empty — no stow-managed entries
        (home / ".local" / "bin").mkdir(parents=True)

        result = _run_script(str(home / "new-repo"), home=home)

        assert result.returncode != 0
        assert "could not locate the current claude-config checkout" in result.stderr


class TestRelocateManifestMismatch:
    def test_manifest_and_live_symlink_disagree_fails_closed(self, tmp_path: Path) -> None:
        """The manifest is a bare dotfile at $HOME with no hook restricting
        writes to it -- it must not be trusted on its own when a live
        ~/.claude symlink resolves somewhere else entirely (an attacker-
        planted manifest pointing away from the real, stowed checkout)."""
        home = tmp_path / "home"
        real_repo = _make_repo(tmp_path, "real-repo")
        _stow(real_repo, home)
        attacker_repo = _make_repo(tmp_path, "attacker-repo")
        (home / ".claude-config-source").write_text(f"{attacker_repo}\n")

        result = _run_script(str(home / "new-repo"), home=home)

        assert result.returncode != 0
        assert "disagrees with the live" in result.stderr
        assert str(attacker_repo) in result.stderr
        assert str(real_repo) in result.stderr
        # The real checkout must be untouched -- no unstow/mv attempted.
        assert real_repo.exists()
        assert (home / ".claude" / "hooks").resolve() == (real_repo / "claude" / ".claude" / "hooks").resolve()


class TestRelocateMvFailureRecovery:
    @requires_non_root
    def test_mv_failure_restores_previous_stow_state(self, tmp_path: Path) -> None:
        """A permission-denied mv (destination parent not writable) after
        stow -D has already unstowed the old location must not leave the
        checkout stow-less -- the script re-stows at the original location
        before exiting. Uses a real, non-writable directory (rather than a
        mocked mv) to force a realistic mv failure, matching this test
        suite's precedent of forcing failures via real filesystem/git state
        (see test_cleanup_merged_branches.py's locked-worktree case) instead
        of a test-only injection seam."""
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home, with_claude_skills=True)
        bin_dir, remove_log, add_log = _claude_shim(tmp_path)

        # Inside $HOME, not outside it -- this test forces mv itself to
        # fail, not the outside-$HOME destination check that runs earlier.
        readonly_parent = home / "readonly-parent"
        readonly_parent.mkdir()
        new_repo = readonly_parent / "new-repo"
        readonly_parent.chmod(0o555)
        try:
            result = _run_script(str(new_repo), home=home, bin_dir=bin_dir)
        finally:
            readonly_parent.chmod(0o755)

        assert result.returncode != 0
        assert "unstowing" in result.stdout
        assert "moving" in result.stdout
        assert "mv failed" in result.stderr
        assert not new_repo.exists()
        # The old location must be restored and re-stowed -- not left
        # unstowed with the checkout stranded at its original path.
        assert old_repo.exists()
        assert (home / ".claude" / "hooks").resolve() == (old_repo / "claude" / ".claude" / "hooks").resolve()
        assert (home / ".local" / "bin" / "some-wrapper").resolve() == (
            old_repo / "claude" / ".local" / "bin" / "some-wrapper"
        ).resolve()
        assert (home / ".claude" / "skills").resolve() == (old_repo / "claude-skills" / "skills").resolve()
        # finish_relocation must not have run -- manifest and marketplace
        # sync are downstream of a successful mv.
        assert _read_log(add_log) == []
        assert _read_log(remove_log) == []


# ---------------------------------------------------------------------------
# --repair flow (repo already moved outside any Claude Code session)
# ---------------------------------------------------------------------------


class TestRepairPerEntryForm:
    def test_repair_recovers_after_out_of_band_move(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home)

        # Simulate an out-of-band move: rename without unstowing first.
        new_repo = tmp_path / "new-repo"
        old_repo.rename(new_repo)
        assert (home / ".claude" / "hooks").is_symlink()
        assert not (home / ".claude" / "hooks").exists()  # dangling

        bin_dir, remove_log, add_log = _claude_shim(tmp_path)

        result = _run_script("--repair", "--allow-outside-home", str(new_repo), home=home, bin_dir=bin_dir)

        assert result.returncode == 0, result.stderr
        assert (home / ".claude" / "hooks").resolve() == (new_repo / "claude" / ".claude" / "hooks").resolve()
        assert (home / ".local" / "bin" / "some-wrapper").resolve() == (
            new_repo / "claude" / ".local" / "bin" / "some-wrapper"
        ).resolve()
        assert (home / ".claude-config-source").read_text().strip() == str(new_repo)
        assert _read_log(add_log) == [str(new_repo)]
        installed = home / ".local" / "bin" / "relocate-claude-config"
        assert installed.is_file() and not installed.is_symlink()
        assert f"Done. ~/.claude and ~/.local/bin now point at: {new_repo}" in result.stdout
        assert "Reminders:" not in result.stdout
        assert "Not done by this command:" in result.stdout
        assert "no dangling stow-managed entries found to quarantine" not in result.stdout


_BACKUP_DIR_REFUSAL_FRAGMENTS = {
    "symlink": "already exists as a symlink",
    "regular file": "exists and is not a directory",
}


def _plant_unusable_backup_dir(
    home: Path, tmp_path: Path, kind: str
) -> tuple[Callable[[], object], object]:
    """Make $BACKUP_DIR a symlink to an empty directory or a regular file.
    Returns `(observe, untouched)`: `observe()` reads what the guard must
    leave alone, and equals `untouched` until something writes there."""
    backup_path = home / ".claude-config-relocate-backup"
    if kind == "symlink":
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        backup_path.symlink_to(elsewhere)
        return (lambda: list(elsewhere.iterdir())), []
    backup_path.write_text("not a directory\n")
    return backup_path.read_text, "not a directory\n"


@pytest.mark.parametrize("kind", ["symlink", "regular file"])
class TestBackupDirSymlinkGuard:
    def test_repair_refuses_when_backup_dir_is_a_symlink_or_regular_file(
        self, tmp_path: Path, kind: str
    ) -> None:
        """BACKUP_DIR is a fixed, predictable path -- a pre-planted symlink
        there must not be silently followed when quarantining dangling
        entries into it, and a regular file there cannot hold them."""
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home)

        new_repo = tmp_path / "new-repo"
        old_repo.rename(new_repo)  # out-of-band move -- leaves dangling entries to quarantine

        observe_backup_dir, untouched_backup_dir = _plant_unusable_backup_dir(home, tmp_path, kind)

        bin_dir, _, _ = _claude_shim(tmp_path)
        result = _run_script("--repair", "--allow-outside-home", str(new_repo), home=home, bin_dir=bin_dir)

        assert result.returncode == 1
        assert _BACKUP_DIR_REFUSAL_FRAGMENTS[kind] in result.stderr
        assert observe_backup_dir() == untouched_backup_dir
        # The guard refused before the quarantine ran, so nothing moved.
        assert "could not move" not in result.stderr
        assert "quarantining dangling" not in result.stdout
        assert (home / ".claude" / "hooks").is_symlink()
        assert not (home / ".claude" / "hooks").exists()
        assert not (home / ".claude-config-source").exists()
        assert not (home / ".local" / "bin" / "relocate-claude-config").exists()

    def test_primary_refuses_before_anything_changes(self, tmp_path: Path, kind: str) -> None:
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home, with_claude_skills=True)
        observe_backup_dir, untouched_backup_dir = _plant_unusable_backup_dir(home, tmp_path, kind)
        bin_dir, _, _ = _claude_shim(tmp_path)

        new_repo = home / "new-repo"
        result = _run_script(str(new_repo), home=home, bin_dir=bin_dir)

        assert result.returncode == 1
        assert _BACKUP_DIR_REFUSAL_FRAGMENTS[kind] in result.stderr
        assert "unstowing" not in result.stdout
        assert "moving" not in result.stdout
        assert observe_backup_dir() == untouched_backup_dir
        assert old_repo.is_dir()
        assert not new_repo.exists()
        assert (home / ".claude" / "hooks").resolve() == (old_repo / "claude" / ".claude" / "hooks").resolve()
        assert (home / ".claude" / "skills").resolve() == (old_repo / "claude-skills" / "skills").resolve()

    def test_repair_refuses_for_tree_folded_claude(self, tmp_path: Path, kind: str) -> None:
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow_tree_folded(old_repo, home)
        new_repo = tmp_path / "new-repo"
        old_repo.rename(new_repo)
        observe_backup_dir, untouched_backup_dir = _plant_unusable_backup_dir(home, tmp_path, kind)
        bin_dir, _, _ = _claude_shim(tmp_path)

        result = _run_script("--repair", "--allow-outside-home", str(new_repo), home=home, bin_dir=bin_dir)

        assert result.returncode == 1
        assert _BACKUP_DIR_REFUSAL_FRAGMENTS[kind] in result.stderr
        assert observe_backup_dir() == untouched_backup_dir
        assert (home / ".claude").is_symlink()
        assert not (home / ".claude").exists()


class TestRepairTreeFoldedForm:
    def test_repair_recovers_tree_folded_claude(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow_tree_folded(old_repo, home)
        assert (home / ".claude").is_symlink()  # sanity: tree-folded, not per-entry

        new_repo = tmp_path / "new-repo"
        old_repo.rename(new_repo)
        assert not (home / ".claude").exists()  # dangling

        bin_dir, remove_log, add_log = _claude_shim(tmp_path)

        result = _run_script("--repair", "--allow-outside-home", str(new_repo), home=home, bin_dir=bin_dir)

        assert result.returncode == 0, result.stderr
        assert (home / ".claude" / "hooks" / "_lib.sh").is_file()
        backup_dir = home / ".claude-config-relocate-backup"
        assert backup_dir.is_dir()
        assert any(entry.name.startswith(".claude.") for entry in backup_dir.iterdir())
        assert "no dangling stow-managed entries found to quarantine" not in result.stdout


def _remove_dangling_links(directory: Path) -> None:
    for entry in directory.iterdir():
        if entry.is_symlink() and not entry.exists():
            entry.unlink()


class TestRepairSweepsEachLocation:
    @pytest.mark.parametrize("dangling_location", ["tree-folded ~/.claude", "per-entry ~/.claude/*", "~/.local/bin/*"])
    def test_repair_quarantines_when_only_one_location_holds_a_dangling_link(
        self, tmp_path: Path, dangling_location: str
    ) -> None:
        """Each location's quarantine result feeds the sweep's "found
        something" report separately, so a run where only one location is
        dangling pins that location's own contribution."""
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        if dangling_location == "tree-folded ~/.claude":
            _stow_tree_folded(old_repo, home)
        else:
            _stow(old_repo, home)
        new_repo = tmp_path / "new-repo"
        old_repo.rename(new_repo)
        if dangling_location == "~/.local/bin/*":
            _remove_dangling_links(home / ".claude")
        else:
            _remove_dangling_links(home / ".local" / "bin")
        bin_dir, _, _ = _claude_shim(tmp_path)

        result = _run_script("--repair", "--allow-outside-home", str(new_repo), home=home, bin_dir=bin_dir)

        assert result.returncode == 0, result.stderr
        assert "quarantining dangling" in result.stdout
        assert "no dangling stow-managed entries found to quarantine" not in result.stdout


class TestRepairQuarantineInvariant:
    def test_dot_named_dangling_link_is_left_in_place(self, tmp_path: Path) -> None:
        """The sweep skips names that start with a dot, so a dangling
        dot-named link survives next to a non-dot one that is quarantined."""
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home)
        new_repo = tmp_path / "new-repo"
        old_repo.rename(new_repo)
        dot_named_link = home / ".claude" / ".dangling-dot-entry"
        dot_named_link_text = str(tmp_path / "nowhere")
        dot_named_link.symlink_to(dot_named_link_text)
        bin_dir, _, _ = _claude_shim(tmp_path)

        result = _run_script("--repair", "--allow-outside-home", str(new_repo), home=home, bin_dir=bin_dir)

        assert result.returncode == 0, result.stderr
        assert dot_named_link.is_symlink()
        assert os.readlink(dot_named_link) == dot_named_link_text
        backup_names = {entry.name for entry in (home / ".claude-config-relocate-backup").iterdir()}
        assert not any(name.startswith(".dangling-dot-entry") for name in backup_names)
        assert any(name.startswith("hooks.") for name in backup_names)

    def test_live_symlink_and_real_directory_left_untouched(self, tmp_path: Path) -> None:
        """Only a confirmed-broken symlink is quarantined — a live
        (non-broken) symlink and a real directory sitting alongside the
        dangling stow-managed entries must survive untouched."""
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home)

        new_repo = tmp_path / "new-repo"
        old_repo.rename(new_repo)

        live_target = tmp_path / "live-target"
        live_target.mkdir()
        (home / ".claude" / "live-thing").symlink_to(live_target)
        (home / ".claude" / "real-thing").mkdir()

        bin_dir, remove_log, add_log = _claude_shim(tmp_path)

        result = _run_script("--repair", "--allow-outside-home", str(new_repo), home=home, bin_dir=bin_dir)

        assert result.returncode == 0, result.stderr
        assert (home / ".claude" / "live-thing").resolve() == live_target.resolve()
        assert (home / ".claude" / "real-thing").is_dir()
        assert not (home / ".claude" / "real-thing").is_symlink()

        backup_dir = home / ".claude-config-relocate-backup"
        backup_basenames = {entry.name.split(".", 1)[0] for entry in backup_dir.iterdir()}
        assert "hooks" in backup_basenames
        assert "live-thing" not in backup_basenames
        assert "real-thing" not in backup_basenames

    def test_repair_refuses_when_new_path_is_not_a_legitimate_checkout(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home)
        new_repo = tmp_path / "new-repo"
        old_repo.rename(new_repo)
        (new_repo / ".claude-plugin" / "marketplace.json").unlink()  # break the canary

        bin_dir, _, _ = _claude_shim(tmp_path)
        result = _run_script("--repair", "--allow-outside-home", str(new_repo), home=home, bin_dir=bin_dir)

        assert result.returncode != 0
        assert "does not look like a claude-config checkout" in result.stderr
        # Nothing quarantined -- positive evidence must come BEFORE any ~/.claude write.
        assert not (home / ".claude-config-relocate-backup").exists()


# ---------------------------------------------------------------------------
# Destination validation (both modes)
# ---------------------------------------------------------------------------


class TestDestinationValidation:
    def test_dangling_symlink_destination_rejected(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".local" / "bin").mkdir(parents=True)
        dangling = home / "dangling-dest"
        dangling.symlink_to(home / "nonexistent-target")

        result = _run_script(str(dangling), home=home)

        assert result.returncode != 0
        assert "dangling symlink" in result.stderr

    def test_destination_outside_home_rejected_without_flag(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".local" / "bin").mkdir(parents=True)
        outside = tmp_path / "outside-home"

        result = _run_script(str(outside), home=home)

        assert result.returncode != 0
        assert "outside" in result.stderr

    def test_dash_prefixed_destination_not_parsed_as_flag(self, tmp_path: Path) -> None:
        """`--` ends flag parsing; without it a leading '-' would be read as
        an unknown flag rather than the destination path."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".local" / "bin").mkdir(parents=True)
        dashy = home / "-dashy-name"

        result = _run_script("--repair", "--", str(dashy), home=home)

        assert "unknown flag" not in result.stderr
        # Reached destination-validation/canary logic rather than erroring
        # on argument parsing -- proves '-dashy-name' was read as a path.
        assert "does not look like a claude-config checkout" in result.stderr

    def test_relocate_destination_already_exists_rejected(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home)
        already_there = home / "already-there"
        already_there.mkdir()

        result = _run_script(str(already_there), home=home)

        assert result.returncode != 0
        assert "already exists" in result.stderr

    def test_repair_destination_live_symlink_outside_home_rejected_without_flag(self, tmp_path: Path) -> None:
        """--repair allows the destination to already exist (it's the
        already-relocated checkout) -- but a LIVE symlink there pointing
        outside $HOME must still be rejected: the parent-only outside-$HOME
        check alone would miss it, since the symlink FILE itself sits inside
        $HOME even though its target does not."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".local" / "bin").mkdir(parents=True)
        outside_target = tmp_path / "outside-home-target"
        outside_target.mkdir()
        repair_dest = home / "repair-dest-symlink"
        repair_dest.symlink_to(outside_target)

        result = _run_script("--repair", str(repair_dest), home=home)

        assert result.returncode != 0
        assert "outside" in result.stderr


# ---------------------------------------------------------------------------
# claude CLI availability and failure handling (sync_marketplace_registration)
# ---------------------------------------------------------------------------


class TestClaudeCliMissing:
    def test_claude_missing_does_not_abort_relocation_and_names_manual_followup(self, tmp_path: Path) -> None:
        """sync_marketplace_registration's `command -v claude` branch must
        not abort the relocation -- the filesystem move/re-stow is the part
        that actually matters -- and must print the exact manual follow-up
        command so a user without `claude` on PATH yet knows what to run
        once it is available."""
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home)

        # A PATH containing every external tool the script calls (readlink,
        # dirname, basename, mv, mkdir, stow, install, cat, head, tr) and bash for
        # its shebang, but no claude -- so `command -v claude` fails. Mirrors
        # test_cleanup_merged_branches.py's TestGhMissing technique.
        min_bin = tmp_path / "min_bin"
        min_bin.mkdir()
        for tool in ("readlink", "dirname", "basename", "mv", "mkdir", "stow", "install", "cat", "head", "tr"):
            tool_path = shutil.which(tool)
            if tool_path:
                (min_bin / tool).symlink_to(tool_path)
        link_bash_into(min_bin)

        env = dict(os.environ)
        env["HOME"] = str(home)
        env["PATH"] = str(min_bin)
        new_repo = home / "new-repo"
        result = subprocess.run(
            [str(_SCRIPT), str(new_repo)],
            capture_output=True, text=True, env=env, check=False,
        )

        assert result.returncode == 0, result.stderr
        assert new_repo.is_dir()
        assert 'claude plugin marketplace add "' in result.stderr
        assert (home / ".claude-config-source").read_text().strip() == str(new_repo)


class TestMarketplaceSubcommandFailure:
    def test_remove_failure_is_reported_and_does_not_abort_relocation(self, tmp_path: Path) -> None:
        """Pins the fail-and-continue decision: a failing `claude plugin
        marketplace remove` must not fall through to bash's default
        uncaught-error trace, must not abort finish_relocation's remaining
        steps (manifest write, self-copy refresh), and must not attempt the
        follow-up `add` over a registration that was never actually removed."""
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home)
        stale_path = str(tmp_path / "some-other-old-location")
        bin_dir, remove_log, add_log = _claude_shim(
            tmp_path,
            [{"name": "claude-config", "source": "directory", "path": stale_path}],
            fail_subcommands=frozenset({"remove"}),
        )

        new_repo = home / "new-repo"
        result = _run_script(str(new_repo), home=home, bin_dir=bin_dir)

        assert result.returncode == 0, result.stderr
        assert new_repo.is_dir()
        assert "claude plugin marketplace remove claude-config" in result.stderr
        assert _read_log(remove_log) == []
        assert _read_log(add_log) == []
        assert (home / ".claude-config-source").read_text().strip() == str(new_repo)
        installed = home / ".local" / "bin" / "relocate-claude-config"
        assert installed.is_file() and not installed.is_symlink()

    def test_add_failure_is_reported_and_does_not_abort_relocation(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home)
        bin_dir, remove_log, add_log = _claude_shim(tmp_path, fail_subcommands=frozenset({"add"}))

        new_repo = home / "new-repo"
        result = _run_script(str(new_repo), home=home, bin_dir=bin_dir)

        assert result.returncode == 0, result.stderr
        assert new_repo.is_dir()
        assert "claude plugin marketplace add" in result.stderr
        assert _read_log(add_log) == []
        assert (home / ".claude-config-source").read_text().strip() == str(new_repo)


# ---------------------------------------------------------------------------
# claude-skills re-link (both modes), exit code 3 on a failed re-link
# ---------------------------------------------------------------------------

# Lead-in of the closing remedy sentence, which sits between the manual-steps
# list and the retry line.
_REMEDY_LEAD_IN = "Resolve each failure named above first"

# A space, a single quote, `$x` and one unpaired backtick: a double-quoted
# print of this path either expands `$x` or fails to parse.
_AWKWARD_DESTINATION_NAME = "new repo's $x`"


@dataclass
class _RealSkillsDirScenario:
    home: Path
    bin_dir: Path
    args: list[str]
    resolved_destination: Path
    expects_allow_flag: bool


def _build_real_skills_dir_failure(
    tmp_path: Path, mode: str, *, inside_home: bool = True, typed: str = "canonical"
) -> _RealSkillsDirScenario:
    """A checkout whose `~/.claude/skills` is a real directory holding user
    content, so the claude-skills re-link refuses it. `typed` is how the
    destination is spelled on the command line: canonical, with a `..`
    segment, or through a symlinked parent. The latter two resolve to the
    canonical form."""
    home = tmp_path / "home"
    old_repo = _make_repo(tmp_path, "old-repo")
    _stow(old_repo, home)
    user_skill_dir = home / ".claude" / "skills" / "mine"
    user_skill_dir.mkdir(parents=True)
    (user_skill_dir / "SKILL.md").write_text("# my own skill\n")
    bin_dir, _, _ = _claude_shim(tmp_path)

    resolved_destination = (home if inside_home else tmp_path) / _AWKWARD_DESTINATION_NAME
    typed_destination = str(resolved_destination)
    if typed == "dotdot":
        (home / "sub").mkdir()
        typed_destination = f"{home}/sub/../{_AWKWARD_DESTINATION_NAME}"
    elif typed == "symlinked-parent":
        (home / "real-parent").mkdir()
        (home / "link-parent").symlink_to(home / "real-parent")
        resolved_destination = home / "real-parent" / _AWKWARD_DESTINATION_NAME
        typed_destination = str(home / "link-parent" / _AWKWARD_DESTINATION_NAME)

    allow_flags = [] if inside_home else ["--allow-outside-home"]
    if mode == "repair":
        old_repo.rename(resolved_destination)
        args = ["--repair", *allow_flags, typed_destination]
    else:
        args = [*allow_flags, typed_destination]
    return _RealSkillsDirScenario(
        home=home,
        bin_dir=bin_dir,
        args=args,
        resolved_destination=resolved_destination,
        expects_allow_flag=not inside_home,
    )


def _assert_relink_failure_reported(stderr: str) -> None:
    """Both modes print these two lines only when a package's re-link failed.
    The bare name proves nothing, since the closing text's remedy sentence
    also names claude-skills."""
    assert "re-linking claude-skills failed" in stderr
    assert "re-linking failed for: claude-skills" in stderr


def _words_bash_reads_from(line: str) -> list[str]:
    result = subprocess.run(
        ["bash", "-c", 'eval "set -- $1"; printf "%s\\0" "$@"', "_", line],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.split("\0")[:-1]


def _run_printed_retry_line(first_run_stderr: str, home: Path, bin_dir: Path) -> subprocess.CompletedProcess:
    """Run the line a failed run's stderr ends with, as a user would: bash
    resolves its command through PATH, so a missing installed copy fails here
    instead of falling through to a real copy elsewhere on PATH."""
    installed_copy = home / ".local" / "bin" / "relocate-claude-config"
    assert installed_copy.is_file() and not installed_copy.is_symlink()
    return subprocess.run(
        ["bash", "-c", _last_nonempty_line(first_run_stderr)],
        capture_output=True, text=True, check=False,
        env=_script_env(home, home / ".local" / "bin", bin_dir),
    )


def _backup_entry_names(home: Path) -> list[str]:
    backup_dir = home / ".claude-config-relocate-backup"
    return sorted(entry.name for entry in backup_dir.iterdir()) if backup_dir.exists() else []


def _relocation_args_for_mode(tmp_path: Path, home: Path, old_repo: Path, mode: str) -> tuple[Path, list[str]]:
    """The destination and script args for a relocation run. Primary moves the
    checkout itself; repair first renames it to the destination, as if moved
    outside a Claude Code session."""
    if mode == "primary":
        new_repo = home / "new-repo"
        return new_repo, [str(new_repo)]
    new_repo = tmp_path / "new-repo"
    old_repo.rename(new_repo)
    return new_repo, ["--repair", "--allow-outside-home", str(new_repo)]


class TestRelinkPackagesBatch:
    def test_a_failed_package_does_not_stop_the_next_and_the_failed_list_resets_per_call(
        self, tmp_path: Path
    ) -> None:
        """Runs relink_packages directly with two rows, the first refused by
        the unit's real-directory check and the second linkable into a
        different target than `.claude`."""
        home = tmp_path / "home"
        (home / ".claude" / "shared").mkdir(parents=True)
        (home / ".other-target").mkdir()
        checkout = tmp_path / "checkout"
        (checkout / "refused-pkg" / "shared").mkdir(parents=True)
        (checkout / "refused-pkg" / "shared" / "item.txt").write_text("refused\n")
        (checkout / "linked-pkg").mkdir()
        (checkout / "linked-pkg" / "entry.txt").write_text("linked\n")
        script = textwrap.dedent("""\
            source "$1"
            RELINK_PACKAGES=("$3" "$4")
            relink_packages "$2"
            printf 'failed-after-batch=%s\\n' "${RELINK_FAILED_PACKAGES[*]}"
            RELINK_PACKAGES=("$4")
            relink_packages "$2"
            printf 'failed-after-rerun=%s\\n' "${RELINK_FAILED_PACKAGES[*]}"
        """)

        result = subprocess.run(
            ["bash", "-c", script, "_", str(_SCRIPT), str(checkout), "refused-pkg\t.claude", "linked-pkg\t.other-target"],
            capture_output=True, text=True, check=False, env=_script_env(home),
        )

        assert result.returncode == 0, result.stderr
        assert f"linking refused-pkg at {checkout}" in result.stdout
        assert f"linking linked-pkg at {checkout}" in result.stdout
        assert "re-linking refused-pkg failed" in result.stderr
        assert "re-linking linked-pkg failed" not in result.stderr
        assert "failed-after-batch=refused-pkg\n" in result.stdout
        assert "failed-after-rerun=\n" in result.stdout
        assert (home / ".other-target" / "entry.txt").resolve() == (checkout / "linked-pkg" / "entry.txt").resolve()
        assert not (home / ".claude" / "shared" / "item.txt").exists()

    def test_a_symlinked_target_dir_is_refused_whatever_it_points_at(self, tmp_path: Path) -> None:
        """Run directly because the claude stow refuses a link it does not
        own before the re-link step is reached."""
        home = tmp_path / "home"
        home.mkdir()
        own_claude_dir = tmp_path / "own-claude-dir"
        own_claude_dir.mkdir()
        (home / ".claude").symlink_to(own_claude_dir)
        checkout = _make_repo(tmp_path, "checkout")

        script = 'source "$1"; relink_packages "$2"; echo "${RELINK_FAILED_PACKAGES[*]}"'

        result = subprocess.run(
            ["bash", "-c", script, "_", str(_SCRIPT), str(checkout)],
            capture_output=True, text=True, check=False, env=_script_env(home),
        )

        assert result.returncode == 0, result.stderr
        assert f"{home / '.claude'} is a symlink" in result.stderr
        assert f"link text: {own_claude_dir}" in result.stderr
        assert result.stdout.splitlines()[-1] == "claude-skills"
        assert list(own_claude_dir.iterdir()) == []


class TestPersonalSkillsRelink:
    @pytest.mark.parametrize("mode", ["primary", "repair"])
    def test_relinks_skills_into_new_checkout_and_backs_up_the_stale_link(
        self, tmp_path: Path, mode: str
    ) -> None:
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home, with_claude_skills=True)
        skills_link = home / ".claude" / "skills"
        stale_link_text = os.readlink(skills_link)
        bin_dir, _, _ = _claude_shim(tmp_path)

        new_repo, args = _relocation_args_for_mode(tmp_path, home, old_repo, mode)
        result = _run_script(*args, home=home, bin_dir=bin_dir)

        assert result.returncode == 0, result.stderr
        assert skills_link.resolve() == (new_repo / "claude-skills" / "skills").resolve()
        backed_up_skills = list((home / ".claude-config-relocate-backup").glob("skills.*"))
        assert len(backed_up_skills) == 1
        assert backed_up_skills[0].is_symlink()
        assert os.readlink(backed_up_skills[0]) == stale_link_text
        assert f"quarantining dangling {skills_link}" in result.stdout
        assert "no dangling stow-managed entries found to quarantine" not in result.stdout

    def test_primary_leaves_an_unrelated_dangling_link_in_place(self, tmp_path: Path) -> None:
        """Primary mode quarantines only the package's own top-level paths,
        not every dangling link under ~/.claude."""
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home, with_claude_skills=True)
        unrelated_link = home / ".claude" / "other-thing"
        unrelated_link.symlink_to(tmp_path / "unmounted-disk")
        bin_dir, _, _ = _claude_shim(tmp_path)

        result = _run_script(str(home / "new-repo"), home=home, bin_dir=bin_dir)

        assert result.returncode == 0, result.stderr
        assert os.readlink(unrelated_link) == str(tmp_path / "unmounted-disk")
        backup_dir = home / ".claude-config-relocate-backup"
        assert list(backup_dir.glob("other-thing.*")) == []


class TestPersonalSkillsRelinkFailure:
    @pytest.mark.parametrize("mode", ["primary", "repair"])
    def test_real_skills_directory_is_left_unmerged_and_exits_3(self, tmp_path: Path, mode: str) -> None:
        scenario = _build_real_skills_dir_failure(tmp_path, mode)
        home = scenario.home

        result = _run_script(*scenario.args, home=home, bin_dir=scenario.bin_dir)

        assert result.returncode == 3, result.stderr
        _assert_relink_failure_reported(result.stderr)
        assert "is a real directory" in result.stderr
        assert "Done." not in result.stdout
        assert str(home / ".claude" / "skills") in result.stderr
        assert (home / ".claude" / "skills" / "mine" / "SKILL.md").read_text() == "# my own skill\n"
        assert not (home / ".claude" / "skills" / "example").exists()
        assert (home / ".claude" / "hooks").resolve() == (
            scenario.resolved_destination / "claude" / ".claude" / "hooks"
        ).resolve()
        assert Path((home / ".claude-config-source").read_text().strip()).resolve() == (
            scenario.resolved_destination.resolve()
        )

    def test_exit_3_closing_text_names_the_retry_and_what_stays_manual(self, tmp_path: Path) -> None:
        scenario = _build_real_skills_dir_failure(tmp_path, "primary")

        result = _run_script(*scenario.args, home=scenario.home, bin_dir=scenario.bin_dir)

        assert result.returncode == 3, result.stderr
        assert "must not be re-run" in result.stderr
        assert (
            f"The checkout moved to {scenario.resolved_destination} and the claude package is linked"
            in result.stderr
        )
        assert "Reminders:" in result.stderr
        assert "git worktree repair" in result.stderr
        assert "also moves other dangling entries" in result.stderr
        assert _REMEDY_LEAD_IN in result.stderr

    @pytest.mark.parametrize(
        ("inside_home", "typed"),
        [
            (True, "canonical"),
            (False, "canonical"),
            (True, "dotdot"),
            (True, "symlinked-parent"),
        ],
        ids=["inside-home", "outside-home", "inside-home-dotdot", "inside-home-symlinked-parent"],
    )
    def test_retry_line_reads_back_as_the_resolved_repair_command(
        self, tmp_path: Path, inside_home: bool, typed: str
    ) -> None:
        scenario = _build_real_skills_dir_failure(tmp_path, "primary", inside_home=inside_home, typed=typed)

        result = _run_script(*scenario.args, home=scenario.home, bin_dir=scenario.bin_dir)

        assert result.returncode == 3, result.stderr
        expected_words = ["relocate-claude-config", "--repair"]
        if scenario.expects_allow_flag:
            expected_words.append("--allow-outside-home")
        expected_words.append(str(scenario.resolved_destination.resolve()))
        assert _words_bash_reads_from(_last_nonempty_line(result.stderr)) == expected_words

    @pytest.mark.parametrize("inside_home", [True, False], ids=["inside-home", "outside-home"])
    def test_printed_retry_line_succeeds_once_the_real_directory_is_moved_aside(
        self, tmp_path: Path, inside_home: bool
    ) -> None:
        scenario = _build_real_skills_dir_failure(tmp_path, "primary", inside_home=inside_home)
        home = scenario.home
        first_run = _run_script(*scenario.args, home=home, bin_dir=scenario.bin_dir)
        assert first_run.returncode == 3, first_run.stderr
        (home / ".claude" / "skills").rename(tmp_path / "skills-moved-aside")

        retry = _run_printed_retry_line(first_run.stderr, home, scenario.bin_dir)

        assert retry.returncode == 0, retry.stderr
        assert (home / ".claude" / "skills").resolve() == (
            scenario.resolved_destination / "claude-skills" / "skills"
        ).resolve()
        assert "no dangling stow-managed entries found to quarantine" in retry.stdout
        assert not (home / ".claude-config-relocate-backup").exists()

    def test_conflicting_real_file_is_kept_not_adopted(self, tmp_path: Path) -> None:
        """A plain stow refuses a real file at a package path, where --adopt
        would move it into the package and link it in its place."""
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        (old_repo / "claude-skills" / "notes.md").write_text("package notes\n")
        _stow(old_repo, home)
        user_notes = home / ".claude" / "notes.md"
        user_notes.write_text("my own notes\n")
        bin_dir, _, _ = _claude_shim(tmp_path)

        result = _run_script(str(home / "new-repo"), home=home, bin_dir=bin_dir)

        assert result.returncode == 3, result.stderr
        assert user_notes.is_file() and not user_notes.is_symlink()
        assert user_notes.read_text() == "my own notes\n"

    def test_live_skills_link_outside_the_checkout_is_left_in_place(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        skills_link = home / ".claude" / "skills"
        skills_link.symlink_to(elsewhere)
        bin_dir, _, _ = _claude_shim(tmp_path)

        result = _run_script(str(home / "new-repo"), home=home, bin_dir=bin_dir)

        assert result.returncode == 3, result.stderr
        _assert_relink_failure_reported(result.stderr)
        # The script's own real-directory refusal, absent here, would mean the live link was mistaken for one.
        assert "is a real directory" not in result.stderr
        assert skills_link.resolve() == elsewhere.resolve()
        assert list((home / ".claude-config-relocate-backup").glob("skills.*")) == []

    @pytest.mark.parametrize("mode", ["primary", "repair"])
    def test_checkout_without_claude_skills_exits_3_with_claude_linked(self, tmp_path: Path, mode: str) -> None:
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home)
        shutil.rmtree(old_repo / "claude-skills")
        bin_dir, _, _ = _claude_shim(tmp_path)

        new_repo, args = _relocation_args_for_mode(tmp_path, home, old_repo, mode)
        result = _run_script(*args, home=home, bin_dir=bin_dir)

        assert result.returncode == 3, result.stderr
        _assert_relink_failure_reported(result.stderr)
        assert (home / ".claude" / "hooks").resolve() == (new_repo / "claude" / ".claude" / "hooks").resolve()
        assert (home / ".claude-config-source").read_text().strip() == str(new_repo)
        assert _last_nonempty_line(result.stderr).startswith("relocate-claude-config --repair")

    def test_repair_over_live_tree_folded_claude_refuses_to_write_through_the_link(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        new_repo = _make_repo(tmp_path, "new-repo")
        _stow_tree_folded(new_repo, home)
        assert (home / ".claude").is_symlink() and (home / ".claude").exists()
        bin_dir, _, _ = _claude_shim(tmp_path)

        result = _run_script("--repair", "--allow-outside-home", str(new_repo), home=home, bin_dir=bin_dir)

        assert result.returncode == 3, result.stderr
        _assert_relink_failure_reported(result.stderr)
        assert f"{home / '.claude'} is a symlink" in result.stderr
        assert f"link text: {os.readlink(home / '.claude')}" in result.stderr
        assert "Reminders:" not in result.stderr
        assert f"The claude package is linked from {new_repo}" in result.stderr
        assert "The checkout moved to" not in result.stderr
        assert not (new_repo / "claude" / ".claude" / "skills").exists()

    def test_printed_retry_succeeds_once_the_symlinked_claude_dir_is_replaced(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        new_repo = _make_repo(tmp_path, "new-repo")
        _stow_tree_folded(new_repo, home)
        bin_dir, _, _ = _claude_shim(tmp_path)
        first_run = _run_script("--repair", "--allow-outside-home", str(new_repo), home=home, bin_dir=bin_dir)
        assert first_run.returncode == 3, first_run.stderr
        # The replacement the output describes: a real directory where the link was.
        (home / ".claude").unlink()
        (home / ".claude").mkdir()

        retry = _run_printed_retry_line(first_run.stderr, home, bin_dir)

        assert retry.returncode == 0, retry.stderr
        assert (home / ".claude").is_dir() and not (home / ".claude").is_symlink()
        assert (home / ".claude" / "skills").resolve() == (new_repo / "claude-skills" / "skills").resolve()
        assert (home / ".claude" / "hooks").resolve() == (new_repo / "claude" / ".claude" / "hooks").resolve()

    @pytest.mark.parametrize("mode", ["primary", "repair"])
    def test_printed_retry_succeeds_once_the_checkout_has_claude_skills(self, tmp_path: Path, mode: str) -> None:
        """The remedy applied in its printed order: the checkout gains the
        package, `./install.sh` refreshes the installed script, then the
        printed line runs. The claim that a retry before the install links
        nothing is not exercised, since the fixture's checkout and installed
        copy both hold the current script."""
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home)
        shutil.rmtree(old_repo / "claude-skills")
        bin_dir, _, _ = _claude_shim(tmp_path)
        new_repo, args = _relocation_args_for_mode(tmp_path, home, old_repo, mode)
        first_run = _run_script(*args, home=home, bin_dir=bin_dir)
        assert first_run.returncode == 3, first_run.stderr
        # The update the output asks for: the checkout gains the package.
        skill_dir = new_repo / "claude-skills" / "skills" / "example"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# example skill\n")
        # What ./install.sh does for this script: a real-file copy at mode 755.
        installed_copy = home / ".local" / "bin" / "relocate-claude-config"
        installed_copy.unlink()
        shutil.copy2(new_repo / "claude" / ".claude" / "scripts" / "relocate-claude-config.sh", installed_copy)
        installed_copy.chmod(0o755)

        retry = _run_printed_retry_line(first_run.stderr, home, bin_dir)

        assert retry.returncode == 0, retry.stderr
        assert (home / ".claude" / "skills").resolve() == (new_repo / "claude-skills" / "skills").resolve()

    def test_repair_over_live_claude_symlink_outside_the_checkout_writes_nothing_through_it(
        self, tmp_path: Path
    ) -> None:
        """The claude stow refuses a link it does not own, so the run stops
        before any re-link and nothing lands in the link's target."""
        home = tmp_path / "home"
        new_repo = _make_repo(tmp_path, "new-repo")
        own_claude_dir = tmp_path / "own-claude-dir"
        own_claude_dir.mkdir()
        home.mkdir()
        (home / ".claude").symlink_to(own_claude_dir)
        (home / ".local" / "bin").mkdir(parents=True)
        bin_dir, _, _ = _claude_shim(tmp_path)

        result = _run_script("--repair", "--allow-outside-home", str(new_repo), home=home, bin_dir=bin_dir)

        assert result.returncode != 0, result.stderr
        assert f"stowing at {new_repo}" in result.stdout
        assert "linking claude-skills" not in result.stdout
        assert (home / ".claude").readlink() == own_claude_dir
        assert list(own_claude_dir.iterdir()) == []

    @pytest.mark.parametrize("mode", ["primary", "repair"])
    def test_refused_claude_stow_runs_no_follow_up(self, tmp_path: Path, mode: str) -> None:
        """The claude stow runs before any re-link, so a conflict there
        stops the run with nothing linked and nothing recorded."""
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home)
        foreign_dir = tmp_path / "foreign"
        foreign_dir.mkdir()
        wrapper_link = home / ".local" / "bin" / "some-wrapper"
        wrapper_link.unlink()
        wrapper_link.symlink_to(foreign_dir)
        bin_dir, _, _ = _claude_shim(tmp_path)

        new_repo, args = _relocation_args_for_mode(tmp_path, home, old_repo, mode)
        result = _run_script(*args, home=home, bin_dir=bin_dir)

        # Stow's status and wording are not this script's contract, so only non-zero and the last step reached are pinned.
        assert result.returncode != 0, result.stderr
        assert "mv failed" not in result.stderr
        assert f"stowing at {new_repo}" in result.stdout
        assert "linking claude-skills" not in result.stdout
        assert not (home / ".claude" / "skills").exists()
        assert not (home / ".claude-config-source").exists()
        assert not (home / ".local" / "bin" / "relocate-claude-config").exists()


@requires_non_root
class TestQuarantineMoveFailure:
    def test_primary_fails_the_unit_before_its_stow_and_exits_3(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home, with_claude_skills=True)
        skills_link = home / ".claude" / "skills"
        stale_link_text = os.readlink(skills_link)
        backup_dir = home / ".claude-config-relocate-backup"
        backup_dir.mkdir()
        bin_dir, _, _ = _claude_shim(tmp_path)

        new_repo = home / "new-repo"
        backup_dir.chmod(0o555)
        try:
            result = _run_script(str(new_repo), home=home, bin_dir=bin_dir)
        finally:
            backup_dir.chmod(0o755)

        assert result.returncode == 3, result.stderr
        _assert_relink_failure_reported(result.stderr)
        assert f"could not move {skills_link} into {backup_dir}" in result.stderr
        # Stow's output is the only sign the claude-skills stow ran here, so a Stow rewording makes this pass vacuously.
        assert "All operations aborted." not in result.stderr
        assert os.readlink(skills_link) == stale_link_text
        assert (home / ".claude" / "hooks").resolve() == (new_repo / "claude" / ".claude" / "hooks").resolve()
        assert (home / ".claude-config-source").read_text().strip() == str(new_repo)

    def test_printed_retry_succeeds_once_the_backup_dir_is_writable(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home, with_claude_skills=True)
        backup_dir = home / ".claude-config-relocate-backup"
        backup_dir.mkdir()
        bin_dir, _, _ = _claude_shim(tmp_path)
        new_repo = home / "new-repo"
        backup_dir.chmod(0o555)
        try:
            first_run = _run_script(str(new_repo), home=home, bin_dir=bin_dir)
        finally:
            # The fix the output names: the backup dir accepts moves again.
            backup_dir.chmod(0o755)
        assert first_run.returncode == 3, first_run.stderr

        retry = _run_printed_retry_line(first_run.stderr, home, bin_dir)

        assert retry.returncode == 0, retry.stderr
        assert (home / ".claude" / "skills").resolve() == (new_repo / "claude-skills" / "skills").resolve()
        assert len(list(backup_dir.glob("skills.*"))) == 1

    def test_repair_stops_before_the_claude_stow_and_exits_1(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home, with_claude_skills=True)
        new_repo = tmp_path / "new-repo"
        old_repo.rename(new_repo)
        backup_dir = home / ".claude-config-relocate-backup"
        backup_dir.mkdir()
        bin_dir, _, _ = _claude_shim(tmp_path)

        backup_dir.chmod(0o555)
        try:
            result = _run_script("--repair", "--allow-outside-home", str(new_repo), home=home, bin_dir=bin_dir)
        finally:
            backup_dir.chmod(0o755)

        assert result.returncode == 1, result.stderr
        assert f"could not move {home / '.claude' / 'hooks'} into {backup_dir}" in result.stderr
        assert "stowing at" not in result.stdout
        assert (home / ".claude" / "hooks").is_symlink()
        assert not (home / ".claude" / "hooks").exists()
        assert not (home / ".claude-config-source").exists()
        assert not (home / ".local" / "bin" / "relocate-claude-config").exists()

    def test_repair_stops_when_the_tree_folded_claude_link_cannot_be_moved(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow_tree_folded(old_repo, home)
        new_repo = tmp_path / "new-repo"
        old_repo.rename(new_repo)
        backup_dir = home / ".claude-config-relocate-backup"
        backup_dir.mkdir()
        bin_dir, _, _ = _claude_shim(tmp_path)

        backup_dir.chmod(0o555)
        try:
            result = _run_script("--repair", "--allow-outside-home", str(new_repo), home=home, bin_dir=bin_dir)
        finally:
            backup_dir.chmod(0o755)

        assert result.returncode == 1, result.stderr
        assert f"could not move {home / '.claude'} into {backup_dir}" in result.stderr
        assert (home / ".claude").is_symlink()
        assert not (home / ".claude").exists()
        assert not (home / ".claude-config-source").exists()


class TestFollowUpsRunBeforeFinishRelocation:
    @pytest.mark.parametrize("mode", ["primary", "repair"])
    def test_a_failing_manifest_write_still_leaves_every_package_linked(self, tmp_path: Path, mode: str) -> None:
        """finish_relocation aborts when the manifest path is a directory.
        The re-links run before it, so that abort cannot leave
        ~/.claude/skills dangling."""
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home, with_claude_skills=True)
        (home / ".claude-config-source").mkdir()
        bin_dir, _, _ = _claude_shim(tmp_path)

        new_repo, args = _relocation_args_for_mode(tmp_path, home, old_repo, mode)
        result = _run_script(*args, home=home, bin_dir=bin_dir)

        assert result.returncode == 1, result.stderr
        if mode == "primary":
            assert "moving" in result.stdout
            assert "mv failed" not in result.stderr
        assert (home / ".claude" / "skills").resolve() == (new_repo / "claude-skills" / "skills").resolve()
        assert (home / ".claude" / "hooks").resolve() == (new_repo / "claude" / ".claude" / "hooks").resolve()

    @pytest.mark.parametrize("mode", ["primary", "repair"])
    def test_repair_after_a_failed_manifest_write_converges_and_is_idempotent(
        self, tmp_path: Path, mode: str
    ) -> None:
        """The retry the header names after that abort, run twice, leaves the
        linked skills and the backup dir as the first run left them."""
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home, with_claude_skills=True)
        manifest_blocker = home / ".claude-config-source"
        manifest_blocker.mkdir()
        bin_dir, _, _ = _claude_shim(tmp_path)
        new_repo, first_run_args = _relocation_args_for_mode(tmp_path, home, old_repo, mode)
        first_run = _run_script(*first_run_args, home=home, bin_dir=bin_dir)
        assert first_run.returncode == 1, first_run.stderr
        skills_link = home / ".claude" / "skills"
        linked_skills_text = os.readlink(skills_link)
        backup_names_after_first_run = _backup_entry_names(home)
        manifest_blocker.rmdir()

        for _attempt in range(2):
            retry = _run_script("--repair", "--allow-outside-home", str(new_repo), home=home, bin_dir=bin_dir)

            assert retry.returncode == 0, retry.stderr
            assert os.readlink(skills_link) == linked_skills_text
            assert _backup_entry_names(home) == backup_names_after_first_run
            assert (home / ".claude-config-source").read_text().strip() == str(new_repo)
