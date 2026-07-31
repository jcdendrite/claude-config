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
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "relocate-claude-config.sh"


# ---------------------------------------------------------------------------
# Fixtures: a fake claude-config checkout, real stow, and a claude CLI shim
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: Path, name: str = "repo") -> Path:
    """A fake claude-config checkout with just enough shape for the script's
    canary check (.claude-plugin/marketplace.json), its own copy of the
    script under test (what the final `install -m 755` self-copy step
    reads from), one hook file (per-entry symlink probe target), and one
    ~/.local/bin wrapper (exercises stow's second target tree)."""
    repo = tmp_path / name
    hooks_dir = repo / "claude" / ".claude" / "hooks"
    scripts_dir = repo / "claude" / ".claude" / "scripts"
    local_bin_dir = repo / "claude" / ".local" / "bin"
    plugin_dir = repo / ".claude-plugin"
    hooks_dir.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    local_bin_dir.mkdir(parents=True)
    plugin_dir.mkdir(parents=True)

    (hooks_dir / "_lib.sh").write_text("# fake _lib.sh\n")
    shutil.copy2(_SCRIPT, scripts_dir / "relocate-claude-config.sh")
    (scripts_dir / "relocate-claude-config.sh").chmod(0o755)
    (local_bin_dir / "some-wrapper").write_text("#!/bin/bash\necho wrapper\n")
    (local_bin_dir / "some-wrapper").chmod(0o755)
    (plugin_dir / "marketplace.json").write_text('{"plugins": []}\n')
    return repo


def _stow(repo: Path, home: Path) -> subprocess.CompletedProcess:
    """Stow `repo`'s claude package into `home`, per-entry form (both
    target directories pre-created, matching install.sh's own tree-fold
    guard)."""
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".local" / "bin").mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        ["stow", "-v", "--adopt", "-t", str(home), "claude"],
        cwd=repo, check=True, capture_output=True, text=True,
    )


def _stow_tree_folded(repo: Path, home: Path) -> subprocess.CompletedProcess:
    """Stow with ~/.claude deliberately NOT pre-created, so stow tree-folds
    it into a single symlink — the legacy form (row2b)."""
    (home / ".local" / "bin").mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        ["stow", "-v", "--adopt", "-t", str(home), "claude"],
        cwd=repo, check=True, capture_output=True, text=True,
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
    explicit error-handling around those calls (row G)."""
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


def _run_script(*args: str, home: Path, bin_dir: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HOME"] = str(home)
    if bin_dir is not None:
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return subprocess.run(
        [str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


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
        _stow(old_repo, home)
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
        assert "mv failed" in result.stderr
        assert not new_repo.exists()
        # The old location must be restored and re-stowed -- not left
        # unstowed with the checkout stranded at its original path.
        assert old_repo.exists()
        assert (home / ".claude" / "hooks").resolve() == (old_repo / "claude" / ".claude" / "hooks").resolve()
        assert (home / ".local" / "bin" / "some-wrapper").resolve() == (
            old_repo / "claude" / ".local" / "bin" / "some-wrapper"
        ).resolve()
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


class TestBackupDirSymlinkGuard:
    def test_repair_refuses_when_backup_dir_is_a_pre_planted_symlink(self, tmp_path: Path) -> None:
        """BACKUP_DIR is a fixed, predictable path -- a pre-planted symlink
        there must not be silently followed when quarantining dangling
        entries into it."""
        home = tmp_path / "home"
        old_repo = _make_repo(tmp_path, "old-repo")
        _stow(old_repo, home)

        new_repo = tmp_path / "new-repo"
        old_repo.rename(new_repo)  # out-of-band move -- leaves dangling entries to quarantine

        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (home / ".claude-config-relocate-backup").symlink_to(elsewhere)

        bin_dir, _, _ = _claude_shim(tmp_path)
        result = _run_script("--repair", "--allow-outside-home", str(new_repo), home=home, bin_dir=bin_dir)

        assert result.returncode != 0
        assert "already exists as a symlink" in result.stderr
        assert list(elsewhere.iterdir()) == []


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


class TestRepairQuarantineInvariant:
    def test_live_symlink_and_real_directory_left_untouched(self, tmp_path: Path) -> None:
        """Only a confirmed-broken symlink is quarantined — a live
        (non-broken) symlink and a real directory sitting alongside the
        dangling stow-managed entries must survive untouched (row2c)."""
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
        an unknown flag rather than the destination path (row2d)."""
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
        # dirname, basename, mv, mkdir, stow, install, cat, head, tr) but no
        # claude -- so `command -v claude` fails. Mirrors
        # test_cleanup_merged_branches.py's TestGhMissing technique.
        min_bin = tmp_path / "min_bin"
        min_bin.mkdir()
        for tool in ("readlink", "dirname", "basename", "mv", "mkdir", "stow", "install", "cat", "head", "tr"):
            tool_path = shutil.which(tool)
            if tool_path:
                (min_bin / tool).symlink_to(tool_path)

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
        """Pins row G's fail-and-continue decision: a failing `claude plugin
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
