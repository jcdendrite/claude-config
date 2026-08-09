"""Tests for the SENTINEL_INVENTORY array and report_sentinel_inventory in
install.sh."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_INSTALL_SH = Path(__file__).resolve().parents[4] / "install.sh"
_BASH = shutil.which("bash") or "/bin/bash"

_OPT_INS_START = "# INSTALL_TEST_FIXTURE: machine-level-opt-ins — start\n"
_OPT_INS_END = "# INSTALL_TEST_FIXTURE: machine-level-opt-ins — end"
_INVENTORY_START = "# INSTALL_TEST_FIXTURE: sentinel-inventory — start\n"
_INVENTORY_END = "# INSTALL_TEST_FIXTURE: sentinel-inventory — end"


def _extract_block(start_marker: str, end_marker: str, must_contain: str) -> str:
    """Delimited extraction by marker comment, not shell-syntax matching --
    same strategy as every other test_install_sh_*.py file, so a future
    reorder can't silently pick up the wrong text while the test keeps
    passing."""
    install_text = _INSTALL_SH.read_text()
    start = install_text.find(start_marker)
    assert start != -1, f"{start_marker!r} not found in {_INSTALL_SH}"
    end = install_text.find(end_marker, start)
    assert end != -1, f"{end_marker!r} not found after start marker in {_INSTALL_SH}"
    block = install_text[start + len(start_marker) : end]
    assert must_contain in block, (
        f"extracted block is missing {must_contain!r}; markers in {_INSTALL_SH} "
        f"are probably misplaced. Got: {block!r}"
    )
    return block


def _extract_inventory_block() -> str:
    """SENTINEL_INVENTORY + report_sentinel_inventory (and their small
    helpers) — self-contained, since report_sentinel_inventory's only
    dependency is the array declared in the same block."""
    return _extract_block(_INVENTORY_START, _INVENTORY_END, "SENTINEL_INVENTORY=(")


def _extract_opt_ins_and_inventory_blocks() -> str:
    """configure_machine_level_opt_ins (machine-level-opt-ins block) plus
    the array it now reads (sentinel-inventory block) -- needed together for
    any test exercising configure_machine_level_opt_ins's real prompting
    behavior, since the array it iterates lives in the later block."""
    opt_ins = _extract_block(_OPT_INS_START, _OPT_INS_END, "configure_machine_level_opt_ins")
    inventory = _extract_block(_INVENTORY_START, _INVENTORY_END, "SENTINEL_INVENTORY=(")
    return opt_ins + "\n" + inventory


def _fake_gh(tmp_path: Path, script_body: str) -> Path:
    """A fake `gh` on its own PATH-prepended directory, so tests never hit
    the network or depend on a real repo/auth state. `script_body` is the
    body of the fake gh's case statement over "$@"."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text(f"#!/bin/bash\n{script_body}\n")
    gh.chmod(0o755)
    return bin_dir


def _base_env(home: Path, repo_dir: Path, gh_bin_dir: Path | None = None) -> dict:
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["REPO_DIR"] = str(repo_dir)
    env.pop("CLAUDE_CONFIG_DIR", None)
    if gh_bin_dir is not None:
        env["PATH"] = f"{gh_bin_dir}:{env['PATH']}"
    return env


def _run_report(env: dict) -> subprocess.CompletedProcess:
    script = "set -e\n" + _extract_inventory_block() + "\nreport_sentinel_inventory\n"
    return subprocess.run(
        [_BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


class TestBlockOrderingDependency:
    def test_real_call_site_follows_array_declaration_in_file_order(self) -> None:
        """configure_machine_level_opt_ins's definition lives in the
        machine-level-opt-ins block and reads SENTINEL_INVENTORY, but the
        array itself is declared in the separate sentinel-inventory block --
        the pre-existing test file's own extraction helper is hardcoded to
        the machine-level-opt-ins span alone and breaks if the function
        moves out of it, so the two are split by necessity, not preference
        (see the comment above configure_machine_level_opt_ins's definition
        in install.sh). This only works in real usage because bash evaluates
        a function body at call time, not definition time, and the array's
        top-level assignment runs before the function's real (non-block-
        scoped) call site later in the file. No functional test can observe
        a violation of this ordering: every test in this suite and in
        test_install_sh_machine_level_opt_ins.py either calls
        _prompt_sentinel_opt_in directly (bypassing the array) or hits the
        non-interactive short-circuit before SENTINEL_INVENTORY is ever
        read -- install.sh also runs without set -u, so a reordering would
        silently produce zero prompts and zero report output, not an error.
        This test pins the ordering directly against install.sh's own text."""
        install_text = _INSTALL_SH.read_text()
        array_decl_index = install_text.index("SENTINEL_INVENTORY=(")
        real_call_site_index = install_text.index("\nconfigure_machine_level_opt_ins\n")
        assert array_decl_index < real_call_site_index, (
            "SENTINEL_INVENTORY's top-level assignment must precede the real "
            "configure_machine_level_opt_ins call site in install.sh's file "
            "order, or the function's loop silently iterates zero times"
        )

    def test_report_call_site_follows_configure_call_site_in_file_order(self) -> None:
        """report_sentinel_inventory reads SENTINEL_INVENTORY_PROMPTED_INDICES,
        which configure_machine_level_opt_ins populates as it prompts, to
        suppress a just-prompted row's enable-hint. Every functional test that
        exercises both functions together hand-assembles its own script as
        "configure_machine_level_opt_ins\\nreport_sentinel_inventory\\n" --
        validating the functions' joint behavior under an order the test
        chose, not install.sh's own real order. A future edit that swapped
        the two real call sites at the bottom of install.sh would degrade
        silently to every enable-hint always showing, even for just-prompted
        rows, with no test in this file catching it. This pins that order
        directly against install.sh's own text, the same technique as the
        test above."""
        install_text = _INSTALL_SH.read_text()
        configure_call_site_index = install_text.index("\nconfigure_machine_level_opt_ins\n")
        report_call_site_index = install_text.index("\nreport_sentinel_inventory\n")
        assert configure_call_site_index < report_call_site_index, (
            "configure_machine_level_opt_ins's real call site must precede "
            "report_sentinel_inventory's, or a just-prompted row's enable-hint "
            "is never suppressed"
        )


class TestSentinelInventoryArray:
    def test_nonzero_entry_count(self, tmp_path: Path) -> None:
        """Both the array declaration and its consumers must be captured
        together by the sentinel-inventory marker block -- install.sh runs
        without `set -u`, so a block missing either half would silently
        iterate zero times rather than erroring. This pins the array itself
        is non-empty in the extracted text."""
        home = tmp_path / "home"
        home.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        script = (
            "set -e\n"
            + _extract_inventory_block()
            + '\nprintf \'%s\\n\' "${#SENTINEL_INVENTORY[@]}"\n'
        )
        result = subprocess.run(
            [_BASH, "-c", script],
            capture_output=True,
            text=True,
            check=False,
            env=_base_env(home, repo),
        )
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        count = int(result.stdout.strip())
        assert count > 0, "SENTINEL_INVENTORY must not be empty"

    def test_every_row_has_six_pipe_delimited_fields_with_no_surrounding_whitespace(
        self,
    ) -> None:
        """The schema comment requires no whitespace around any `|` --
        IFS='|' read -r bakes leading/trailing spaces into a field
        otherwise, a real drift the two original hardcoded prompt strings
        had no test guarding against."""
        install_text = _INSTALL_SH.read_text()
        start = install_text.find(_INVENTORY_START)
        end = install_text.find(_INVENTORY_END, start)
        block = install_text[start:end]
        array_start = block.find("SENTINEL_INVENTORY=(")
        array_end = block.find("\n)", array_start)
        array_body = block[array_start:array_end]
        rows = [
            line.strip()[1:-1]  # strip surrounding quotes
            for line in array_body.splitlines()
            if line.strip().startswith('"')
        ]
        assert rows, "no rows parsed out of the SENTINEL_INVENTORY array literal"
        for row in rows:
            fields = row.split("|")
            assert len(fields) == 6, f"row {row!r} does not have exactly 6 fields"
            for field in fields:
                assert field == field.strip(), (
                    f"field {field!r} in row {row!r} has leading/trailing whitespace"
                )

    def test_machine_promptable_rows_carry_a_prompt_description(self) -> None:
        install_text = _INSTALL_SH.read_text()
        start = install_text.find(_INVENTORY_START)
        end = install_text.find(_INVENTORY_END, start)
        block = install_text[start:end]
        rows = [line.strip()[1:-1] for line in block.splitlines() if line.strip().startswith('"')]
        promptable_rows = [r for r in rows if r.split("|")[1] == "machine-promptable"]
        assert promptable_rows, "expected at least one machine-promptable row"
        for row in promptable_rows:
            assert row.split("|")[3] != "", (
                f"machine-promptable row {row!r} must carry a non-empty prompt-description"
            )


class TestSentinelIndexPromptedThisRun:
    """Direct coverage of the word-boundary matching in
    _sentinel_index_prompted_this_run -- a plain substring check would let
    a prompted index "1" falsely suppress the hint for index "11" or "21"."""

    def _prompted(self, prompted_indices: str, index: str, tmp_path: Path) -> bool:
        script = (
            "set -e\n"
            + _extract_inventory_block()
            + f'\nSENTINEL_INVENTORY_PROMPTED_INDICES="{prompted_indices}"\n'
            + f'_sentinel_index_prompted_this_run "{index}"\n'
        )
        result = subprocess.run(
            [_BASH, "-c", script],
            capture_output=True,
            text=True,
            check=False,
            env=_base_env(tmp_path / "home", tmp_path / "repo"),
        )
        assert result.stderr == "", result.stderr
        return result.returncode == 0

    def test_exact_index_matches(self, tmp_path: Path) -> None:
        assert self._prompted("0 11", "11", tmp_path)

    def test_index_one_does_not_falsely_match_index_eleven(self, tmp_path: Path) -> None:
        assert not self._prompted("11", "1", tmp_path)

    def test_index_absent_from_list_does_not_match(self, tmp_path: Path) -> None:
        assert not self._prompted("0 2 4", "3", tmp_path)

    def test_empty_prompted_list_matches_nothing(self, tmp_path: Path) -> None:
        assert not self._prompted("", "0", tmp_path)


class TestConfigureMachineLevelOptInsNonInteractiveSnapshot:
    def test_non_tty_run_leaves_full_home_snapshot_byte_identical(
        self, tmp_path: Path
    ) -> None:
        """Strengthens the pre-existing non-TTY test (which only asserts two
        named sentinel paths stay absent) to a full recursive $HOME
        snapshot: report_sentinel_inventory is read-only by design, and
        configure_machine_level_opt_ins must still no-op under closed stdin
        now that it also consumes SENTINEL_INVENTORY."""
        home = tmp_path / "home"
        home.mkdir()
        (home / ".claude").mkdir()
        (home / ".claude" / "existing-file.txt").write_text("pre-existing content\n")
        repo = tmp_path / "repo"
        (repo / ".claude").mkdir(parents=True)
        (repo / ".claude" / "worktree-optout").write_text("")
        gh_bin_dir = _fake_gh(tmp_path, "exit 1")

        def snapshot() -> dict[str, tuple[bool, str | None]]:
            result = {}
            for path in home.rglob("*"):
                rel = str(path.relative_to(home))
                if path.is_file():
                    result[rel] = (True, path.read_bytes().hex())
                else:
                    result[rel] = (False, None)
            return result

        before = snapshot()
        script = (
            "set -e\n"
            + _extract_opt_ins_and_inventory_blocks()
            + "\nconfigure_machine_level_opt_ins\nreport_sentinel_inventory\n"
        )
        result = subprocess.run(
            [_BASH, "-c", script],
            input="",
            capture_output=True,
            text=True,
            check=False,
            env=_base_env(home, repo, gh_bin_dir),
        )
        after = snapshot()

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert after == before, (
            "a non-interactive run must not create, remove, or modify anything under $HOME"
        )


class TestReportSentinelInventory:
    def test_absent_machine_promptable_sentinel_reports_default_state(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        gh_bin_dir = _fake_gh(tmp_path, "exit 1")

        result = _run_report(_base_env(home, repo, gh_bin_dir))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "Worktree enforcement: disabled" in result.stdout
        assert f"{home}/.claude/worktree-required" in result.stdout

    def test_present_machine_sentinel_reports_enabled(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "worktree-required").write_text("")
        repo = tmp_path / "repo"
        repo.mkdir()
        gh_bin_dir = _fake_gh(tmp_path, "exit 1")

        result = _run_report(_base_env(home, repo, gh_bin_dir))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "Worktree enforcement: ENABLED" in result.stdout

    def test_repo_scope_reports_current_repo_only(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        repo = tmp_path / "repo"
        (repo / ".claude").mkdir(parents=True)
        (repo / ".claude" / "worktree-optout").write_text("")
        gh_bin_dir = _fake_gh(tmp_path, "exit 1")

        result = _run_report(_base_env(home, repo, gh_bin_dir))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "Worktree enforcement opt-out (this repo): ENABLED" in result.stdout
        assert ".claude/worktree-optout" in result.stdout

    def test_content_addressed_sentinel_absent(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        gh_bin_dir = _fake_gh(tmp_path, "exit 1")

        result = _run_report(_base_env(home, repo, gh_bin_dir))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "absent (.claude/pr-cost-disclosure)" in result.stdout

    def test_content_addressed_sentinel_present_and_matching(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        repo = tmp_path / "repo"
        (repo / ".claude").mkdir(parents=True)
        (repo / ".claude" / "pr-cost-disclosure").write_text("someorg/somerepo\n")
        gh_bin_dir = _fake_gh(
            tmp_path,
            'case "$*" in *"nameWithOwner"*) echo "someorg/somerepo" ;; *) exit 1 ;; esac',
        )

        result = _run_report(_base_env(home, repo, gh_bin_dir))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "present, matches this repo" in result.stdout

    def test_content_addressed_sentinel_present_but_mismatched(self, tmp_path: Path) -> None:
        """A .claude/ directory copied wholesale from another repo carries a
        pr-cost-disclosure file naming the *origin* repo -- must read as a
        warning, not as silently enabled."""
        home = tmp_path / "home"
        home.mkdir()
        repo = tmp_path / "repo"
        (repo / ".claude").mkdir(parents=True)
        (repo / ".claude" / "pr-cost-disclosure").write_text("otherorg/otherrepo\n")
        gh_bin_dir = _fake_gh(
            tmp_path,
            'case "$*" in *"nameWithOwner"*) echo "someorg/somerepo" ;; *) exit 1 ;; esac',
        )

        result = _run_report(_base_env(home, repo, gh_bin_dir))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "present but MISMATCHED" in result.stdout

    def test_diverged_config_dir_prints_both_paths(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        (home / ".claude").mkdir()
        (home / ".claude" / "worktree-required").write_text("")
        config_dir = tmp_path / "other-config-dir"
        config_dir.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        gh_bin_dir = _fake_gh(tmp_path, "exit 1")
        env = _base_env(home, repo, gh_bin_dir)
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)

        result = _run_report(env)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "DIVERGED" in result.stdout
        assert f"{home}/.claude/worktree-required: ENABLED" in result.stdout
        assert f"{config_dir}/worktree-required: disabled" in result.stdout

    def test_enable_hint_suppressed_for_index_prompted_this_run(self, tmp_path: Path) -> None:
        """A row configure_machine_level_opt_ins already prompted about this
        run must not also print the enable-hint -- the state is still shown,
        just without a redundant "here's the command" line seconds after the
        user was asked directly."""
        home = tmp_path / "home"
        home.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        gh_bin_dir = _fake_gh(tmp_path, "exit 1")
        script = (
            "set -e\n"
            + _extract_inventory_block()
            + '\nSENTINEL_INVENTORY_PROMPTED_INDICES="0"\nreport_sentinel_inventory\n'
        )
        result = subprocess.run(
            [_BASH, "-c", script],
            capture_output=True,
            text=True,
            check=False,
            env=_base_env(home, repo, gh_bin_dir),
        )

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        # Index 0 is the machine-scope worktree-required row -- "just
        # prompted" -- no hint for its $HOME/.claude path. (A distinct,
        # repo-scoped row also named "worktree-required" exists at a later
        # index and is untouched by this assertion; it keeps its own hint,
        # checked separately below.)
        home_hint = f"to enable: touch {home}/.claude/worktree-required"
        assert home_hint not in result.stdout, (
            f"unexpected enable-hint for a just-prompted row: {result.stdout!r}"
        )
        # A different, un-prompted machine-promptable row still gets its hint.
        assert any(
            "track-permission-prompts" in line and "to enable" in line
            for line in result.stdout.splitlines()
        ), "an un-prompted disabled row must still show its enable-hint"
