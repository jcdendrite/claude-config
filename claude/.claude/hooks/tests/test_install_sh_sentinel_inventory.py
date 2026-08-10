"""Tests for the SENTINEL_INVENTORY array and report_sentinel_inventory in
install.sh."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

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


def _base_env(home: Path, repo_dir: Path) -> dict:
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["REPO_DIR"] = str(repo_dir)
    env.pop("CLAUDE_CONFIG_DIR", None)
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
        now that it also consumes SENTINEL_INVENTORY. The account-scoped
        pr-cost-disclosure sentinel is included here so a reporter that
        rewrote it in place (rather than only reading it) would be caught --
        the array previously never created that file at all."""
        home = tmp_path / "home"
        home.mkdir()
        (home / ".claude").mkdir()
        (home / ".claude" / "existing-file.txt").write_text("pre-existing content\n")
        (home / ".claude" / "pr-cost-disclosure").write_text("dollars\n")
        repo = tmp_path / "repo"
        (repo / ".claude").mkdir(parents=True)
        (repo / ".claude" / "worktree-optout").write_text("")

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
            env=_base_env(home, repo),
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

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "Worktree enforcement: disabled" in result.stdout
        assert f"{home}/.claude/worktree-required" in result.stdout

    def test_present_machine_sentinel_reports_enabled(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "worktree-required").write_text("")
        repo = tmp_path / "repo"
        repo.mkdir()

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "Worktree enforcement: ENABLED" in result.stdout

    def test_repo_scope_reports_current_repo_only(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        repo = tmp_path / "repo"
        (repo / ".claude").mkdir(parents=True)
        (repo / ".claude" / "worktree-optout").write_text("")

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "Worktree enforcement opt-out (this repo): ENABLED" in result.stdout
        assert ".claude/worktree-optout" in result.stdout

    def test_account_sentinel_absent_reports_disabled_with_enable_hint(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        sentinel_path = home / ".claude" / "pr-cost-disclosure"

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert f"PR cost disclosure (this account): disabled ({sentinel_path})" in result.stdout
        assert f'to enable: echo dollars > "{sentinel_path}"' in result.stdout

    def test_account_sentinel_exact_dollars_reports_enabled(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "pr-cost-disclosure").write_text("dollars")
        repo = tmp_path / "repo"
        repo.mkdir()

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "PR cost disclosure (this account): ENABLED (mode=dollars)" in result.stdout

    def test_account_sentinel_trailing_newline_reports_enabled(self, tmp_path: Path) -> None:
        """Pins the documented enable command's own output: `echo dollars >
        <path>` writes a trailing newline, not a bare `dollars`."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "pr-cost-disclosure").write_text("dollars\n")
        repo = tmp_path / "repo"
        repo.mkdir()

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "PR cost disclosure (this account): ENABLED (mode=dollars)" in result.stdout

    def test_account_sentinel_leading_whitespace_reports_enabled(self, tmp_path: Path) -> None:
        """Pins that the trim is bidirectional, not right-only."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "pr-cost-disclosure").write_text(" dollars")
        repo = tmp_path / "repo"
        repo.mkdir()

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "PR cost disclosure (this account): ENABLED (mode=dollars)" in result.stdout

    def test_account_sentinel_uppercase_dollars_reports_enabled(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "pr-cost-disclosure").write_text("DOLLARS")
        repo = tmp_path / "repo"
        repo.mkdir()

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "PR cost disclosure (this account): ENABLED (mode=dollars)" in result.stdout

    def test_account_sentinel_mixed_case_dollars_reports_enabled(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "pr-cost-disclosure").write_text("Dollars")
        repo = tmp_path / "repo"
        repo.mkdir()

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "PR cost disclosure (this account): ENABLED (mode=dollars)" in result.stdout

    def test_account_sentinel_empty_file_reports_disabled(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "pr-cost-disclosure").write_text("")
        repo = tmp_path / "repo"
        repo.mkdir()

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "PR cost disclosure (this account): disabled" in result.stdout

    def test_account_sentinel_whitespace_only_reports_disabled(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "pr-cost-disclosure").write_text(" \n")
        repo = tmp_path / "repo"
        repo.mkdir()

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "PR cost disclosure (this account): disabled" in result.stdout

    def test_account_sentinel_interior_whitespace_not_deleted_reports_disabled(
        self, tmp_path: Path
    ) -> None:
        """Fail-open shape 1: `tr -d '[:space:]'` would collapse "dol lars"
        to "dollars" and enable disclosure -- the required grammar strips
        only leading/trailing whitespace, so this must stay disabled."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "pr-cost-disclosure").write_text("dol lars")
        repo = tmp_path / "repo"
        repo.mkdir()

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "ENABLED" not in result.stdout
        assert 'present but mode not recognized: "dol lars" — treated as disabled' in result.stdout

    def test_account_sentinel_second_line_ignored_reports_disabled(
        self, tmp_path: Path
    ) -> None:
        """Fail-open shape 2: `IFS= read -r mode < file` reads only the first
        line and would treat "dollars\\nallowance" as opt-in -- the whole
        file must be read and compared, not just its first line."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "pr-cost-disclosure").write_text("dollars\nallowance\n")
        repo = tmp_path / "repo"
        repo.mkdir()

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "ENABLED" not in result.stdout
        assert "treated as disabled" in result.stdout

    def test_account_sentinel_value_with_trailing_extra_chars_reports_disabled(
        self, tmp_path: Path
    ) -> None:
        """Fail-open shape 3: an unanchored compare (`[[ $mode == *dollars*
        ]]`) would enable on "dollarsx" -- the compare must be an anchored
        equality test."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "pr-cost-disclosure").write_text("dollarsx")
        repo = tmp_path / "repo"
        repo.mkdir()

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "ENABLED" not in result.stdout
        assert 'present but mode not recognized: "dollarsx" — treated as disabled' in result.stdout

    def test_account_sentinel_value_with_leading_extra_chars_reports_disabled(
        self, tmp_path: Path
    ) -> None:
        """Fail-open shape 3, the other direction: "xdollars" must not match
        an unanchored compare either."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "pr-cost-disclosure").write_text("xdollars")
        repo = tmp_path / "repo"
        repo.mkdir()

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "ENABLED" not in result.stdout
        assert 'present but mode not recognized: "xdollars" — treated as disabled' in result.stdout

    def test_account_sentinel_unrecognized_value_echoed_in_report(
        self, tmp_path: Path
    ) -> None:
        """A typo is not silent in practice -- install.sh's inventory
        reports the literal unrecognized value at install time."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "pr-cost-disclosure").write_text("allowance")
        repo = tmp_path / "repo"
        repo.mkdir()

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert (
            'present but mode not recognized: "allowance" — treated as disabled'
            in result.stdout
        )

    def test_account_sentinel_resolves_against_claude_config_dir_when_set(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "other-config-dir"
        (config_dir).mkdir()
        (config_dir / "pr-cost-disclosure").write_text("dollars\n")
        repo = tmp_path / "repo"
        repo.mkdir()
        env = _base_env(home, repo)
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)

        result = _run_report(env)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        sentinel_path = config_dir / "pr-cost-disclosure"
        assert f"PR cost disclosure (this account): ENABLED (mode=dollars) ({sentinel_path})" in result.stdout
        assert "the only path this scope checks" in result.stdout

    def test_account_sentinel_does_not_union_with_home_claude(self, tmp_path: Path) -> None:
        """Resolution, not union: a sentinel present only at $HOME/.claude
        must not activate disclosure once CLAUDE_CONFIG_DIR is set -- the
        two paths are never both checked."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "pr-cost-disclosure").write_text("dollars\n")
        config_dir = tmp_path / "other-config-dir"
        config_dir.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        env = _base_env(home, repo)
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)

        result = _run_report(env)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        sentinel_path = config_dir / "pr-cost-disclosure"
        assert f"PR cost disclosure (this account): disabled ({sentinel_path})" in result.stdout

    def test_account_sentinel_relative_claude_config_dir_reports_disabled(
        self, tmp_path: Path
    ) -> None:
        """A relative CLAUDE_CONFIG_DIR is invalid, not cwd-relative -- it
        must fall back to $HOME/.claude, exactly like an unset value."""
        home = tmp_path / "home"
        home.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        env = _base_env(home, repo)
        env["CLAUDE_CONFIG_DIR"] = "relative/config/dir"

        result = _run_report(env)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        sentinel_path = home / ".claude" / "pr-cost-disclosure"
        assert f"PR cost disclosure (this account): disabled ({sentinel_path})" in result.stdout

    def test_account_sentinel_nonexistent_config_dir_reports_disabled_without_aborting(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        env = _base_env(home, repo)
        env["CLAUDE_CONFIG_DIR"] = str(tmp_path / "does-not-exist")

        result = _run_report(env)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "PR cost disclosure (this account): disabled" in result.stdout

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root bypasses discretionary file-permission bits (CAP_DAC_OVERRIDE "
        "on Linux), so chmod(0o000) does not make the file unreadable and this "
        "sentinel would resolve to ENABLED instead of the asserted disabled",
    )
    def test_account_sentinel_unreadable_file_reports_disabled_without_aborting(
        self, tmp_path: Path
    ) -> None:
        """Proves the guarded read (`|| mode=""`) survives a permission
        failure -- an unguarded command substitution here would abort all of
        install.sh under set -e, not just this report line."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        sentinel_path = home / ".claude" / "pr-cost-disclosure"
        sentinel_path.write_text("dollars\n")
        sentinel_path.chmod(0o000)
        repo = tmp_path / "repo"
        repo.mkdir()

        try:
            result = _run_report(_base_env(home, repo))
        finally:
            sentinel_path.chmod(0o644)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert f"PR cost disclosure (this account): disabled ({sentinel_path})" in result.stdout

    def test_diverged_config_dir_prints_both_paths(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        (home / ".claude").mkdir()
        (home / ".claude" / "worktree-required").write_text("")
        config_dir = tmp_path / "other-config-dir"
        config_dir.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        env = _base_env(home, repo)
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
            env=_base_env(home, repo),
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
