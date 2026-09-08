"""Tests for the REPO_MARKER_INVENTORY array, report_sentinel_inventory, and
the legacy-config-migration execution step in install.sh."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from _config import schema

_INSTALL_SH = Path(__file__).resolve().parents[4] / "install.sh"
_CONFIG_SH = Path(__file__).resolve().parents[1] / "_config.sh"
_BASH = shutil.which("bash") or "/bin/bash"

_OPT_INS_START = "# INSTALL_TEST_FIXTURE: machine-level-opt-ins — start\n"
_OPT_INS_END = "# INSTALL_TEST_FIXTURE: machine-level-opt-ins — end"
_INVENTORY_START = "# INSTALL_TEST_FIXTURE: sentinel-inventory — start\n"
_INVENTORY_END = "# INSTALL_TEST_FIXTURE: sentinel-inventory — end"
_MIGRATION_START = "# INSTALL_TEST_FIXTURE: legacy-config-migration — start\n"
_MIGRATION_END = "# INSTALL_TEST_FIXTURE: legacy-config-migration — end"


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
    """REPO_MARKER_INVENTORY + report_sentinel_inventory (and their small
    helpers) -- self-contained apart from _config.sh, which every caller
    below sources first (see _config_sh_prelude)."""
    return _extract_block(_INVENTORY_START, _INVENTORY_END, "REPO_MARKER_INVENTORY=(")


def _extract_opt_ins_and_inventory_blocks() -> str:
    """configure_machine_level_opt_ins (machine-level-opt-ins block) plus
    the reporter (sentinel-inventory block) -- needed together for any test
    exercising configure_machine_level_opt_ins's real prompting behavior
    followed by a report of what it just wrote."""
    opt_ins = _extract_block(_OPT_INS_START, _OPT_INS_END, "configure_machine_level_opt_ins")
    inventory = _extract_block(_INVENTORY_START, _INVENTORY_END, "REPO_MARKER_INVENTORY=(")
    return opt_ins + "\n" + inventory


def _extract_migration_block() -> str:
    """The legacy-config-migration fixture block: a single statement that
    executes migrate-legacy-config.sh, plus its `||` non-abort fallback."""
    return _extract_block(_MIGRATION_START, _MIGRATION_END, "migrate-legacy-config.sh")


def _config_sh_prelude() -> str:
    """install.sh:421's own `. "$REPO_DIR/claude/.claude/hooks/_config.sh"`
    line, sourced the real (not extracted) file at its absolute test-time
    path -- every test below that touches configure_machine_level_opt_ins
    or report_sentinel_inventory needs this, since both now read
    _CONFIG_SCHEMA_FILE and call _config_value/_config_set/_lib_config_dir,
    all defined only in _config.sh."""
    return f'. "{_CONFIG_SH}"\n'


def _base_env(home: Path, repo_dir: Path) -> dict:
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["REPO_DIR"] = str(repo_dir)
    env.pop("CLAUDE_CONFIG_DIR", None)
    return env


def _run_report(env: dict) -> subprocess.CompletedProcess:
    script = (
        "set -e\n" + _config_sh_prelude() + _extract_inventory_block() + "\nreport_sentinel_inventory\n"
    )
    return subprocess.run(
        [_BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


class TestBlockOrderingDependency:
    def test_config_sh_sourced_before_configure_call_site_in_file_order(self) -> None:
        """configure_machine_level_opt_ins's body reads $_CONFIG_SCHEMA_FILE
        and calls _config_value/_config_set, all defined only by sourcing
        _config.sh -- install.sh:421 does that once, near the top of the
        file, well before either function is even defined. Unlike a bash
        array (populated at the point its own assignment runs) a function
        body defers evaluation to call time, so the real risk this test
        pins is a future edit moving the source line below the real
        configure_machine_level_opt_ins call site at the bottom of the
        file: under `set -e` (no `set -u`), `_CONFIG_SCHEMA_FILE` would
        then expand to an empty string, and `done < "$_CONFIG_SCHEMA_FILE"`
        against that empty path aborts the whole script with a redirection
        error rather than silently iterating zero times."""
        install_text = _INSTALL_SH.read_text()
        source_index = install_text.index(
            '. "$REPO_DIR/claude/.claude/hooks/_config.sh"'
        )
        real_call_site_index = install_text.index("\nconfigure_machine_level_opt_ins\n")
        assert source_index < real_call_site_index, (
            "_config.sh must be sourced before configure_machine_level_opt_ins's "
            "real call site, or its schema-driven loop aborts the script"
        )

    def test_report_call_site_follows_configure_call_site_in_file_order(self) -> None:
        """report_sentinel_inventory re-resolves every key's value fresh on
        each call (via _config_value, which re-reads claude-config.toml),
        so a value configure_machine_level_opt_ins just wrote via
        _config_set is only visible to the report if the report runs
        after. Every functional test that exercises both functions
        together hand-assembles its own script as
        "configure_machine_level_opt_ins\\nreport_sentinel_inventory\\n" --
        validating the functions' joint behavior under an order the test
        chose, not install.sh's own real order. This pins that order
        directly against install.sh's own text, the same technique as the
        test above."""
        install_text = _INSTALL_SH.read_text()
        configure_call_site_index = install_text.index("\nconfigure_machine_level_opt_ins\n")
        report_call_site_index = install_text.index("\nreport_sentinel_inventory\n")
        assert configure_call_site_index < report_call_site_index, (
            "configure_machine_level_opt_ins's real call site must precede "
            "report_sentinel_inventory's, or a value the user just opted "
            "into isn't reflected in the same run's report"
        )


class TestSentinelInventoryArray:
    def test_nonzero_entry_count(self, tmp_path: Path) -> None:
        """Both the array declaration and its consumers must be captured
        together by the sentinel-inventory marker block -- install.sh runs
        without `set -u`, so a block missing either half would silently
        iterate zero times rather than erroring. This pins the array itself
        is non-empty in the extracted text.

        Also asserts empty stderr: every row is a double-quoted bash string
        literal, so an unescaped backtick or `$(...)` in a field is live
        command substitution, not inert text, and a failed substitution
        under `set -e` aborts the array assignment itself. The count and
        field-shape checks in this class still pass in that case -- bash
        still populates the array before continuing -- so stderr is the
        only signal that would catch it."""
        home = tmp_path / "home"
        home.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        script = (
            "set -e\n"
            + _extract_inventory_block()
            + '\nprintf \'%s\\n\' "${#REPO_MARKER_INVENTORY[@]}"\n'
        )
        result = subprocess.run(
            [_BASH, "-c", script],
            capture_output=True,
            text=True,
            check=False,
            env=_base_env(home, repo),
        )
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert result.stderr == "", (
            f"sourcing REPO_MARKER_INVENTORY produced stderr -- likely an "
            f"unescaped backtick or $(...) in a row's field triggering "
            f"command substitution: {result.stderr!r}"
        )
        count = int(result.stdout.strip())
        assert count > 0, "REPO_MARKER_INVENTORY must not be empty"

    def test_every_row_has_exactly_three_pipe_delimited_fields_with_no_surrounding_whitespace(
        self,
    ) -> None:
        """The schema comment requires no whitespace around any `|` --
        IFS='|' read -r bakes leading/trailing spaces into a field
        otherwise. REPO_MARKER_INVENTORY carries only the four repo-scope
        committed markers now, each a fixed path-template|human-name|
        docs-anchor row -- unlike the pre-migration array, no row here
        carries an optional trailing field."""
        install_text = _INSTALL_SH.read_text()
        start = install_text.find(_INVENTORY_START)
        end = install_text.find(_INVENTORY_END, start)
        block = install_text[start:end]
        array_start = block.find("REPO_MARKER_INVENTORY=(")
        array_end = block.find("\n)", array_start)
        array_body = block[array_start:array_end]
        rows = [
            line.strip()[1:-1]  # strip surrounding quotes
            for line in array_body.splitlines()
            if line.strip().startswith('"')
        ]
        assert rows, "no rows parsed out of the REPO_MARKER_INVENTORY array literal"
        for row in rows:
            fields = row.split("|")
            assert len(fields) == 3, f"row {row!r} does not have exactly 3 fields"
            for field in fields:
                assert field == field.strip(), (
                    f"field {field!r} in row {row!r} has leading/trailing whitespace"
                )


class TestSentinelIndexPromptedThisRun:
    """Direct coverage of the word-boundary matching in
    _sentinel_index_prompted_this_run -- a plain substring check would let
    a prompted index "1" falsely suppress the hint for index "11" or "21".
    Unchanged by the migration: this function still keys off a
    space-delimited index list, now populated (never, in current practice
    -- see report_sentinel_inventory's own header comment) only for
    REPO_MARKER_INVENTORY rows."""

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


class TestLegacyConfigMigrationExecution:
    """Coverage for the legacy-config-migration fixture block: install.sh
    executes migrate-legacy-config.sh as a separate process (not `source`s
    it) before the interactive opt-in prompts run, and a non-zero exit
    from it must not abort the rest of install.sh."""

    def _stub_migrate_script(self, repo: Path, exit_code: int) -> None:
        script_path = repo / "claude" / ".claude" / "scripts" / "migrate-legacy-config.sh"
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(
            "#!/usr/bin/env bash\n"
            'echo "MIGRATION_RAN pid=$$"\n'
            f"exit {exit_code}\n"
        )
        script_path.chmod(0o755)

    def _run_migration_block(self, repo: Path, exit_code: int) -> subprocess.CompletedProcess:
        self._stub_migrate_script(repo, exit_code)
        script = (
            "set -e\n"
            'echo "OUTER_PID=$$"\n'
            + _extract_migration_block()
            + "\necho AFTER_MIGRATION\n"
        )
        env = dict(os.environ)
        env["REPO_DIR"] = str(repo)
        return subprocess.run(
            [_BASH, "-c", script],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def test_migration_script_runs_as_a_separate_process(self, tmp_path: Path) -> None:
        """Executed (`"$REPO_DIR/.../migrate-legacy-config.sh"`), not
        sourced (`. "$REPO_DIR/.../migrate-legacy-config.sh"`) -- a sourced
        script forks no new process, so its own $$ would equal the
        caller's; an executed one always gets a distinct PID."""
        result = self._run_migration_block(tmp_path, exit_code=0)
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        outer_pid = next(
            line for line in result.stdout.splitlines() if line.startswith("OUTER_PID=")
        ).split("=", 1)[1]
        migration_pid = next(
            line for line in result.stdout.splitlines() if line.startswith("MIGRATION_RAN pid=")
        ).rsplit("=", 1)[1]
        assert migration_pid != outer_pid, (
            "migrate-legacy-config.sh must run as its own process (executed, "
            "not sourced) -- a sourced script would report the same $$ as "
            "the caller"
        )

    def test_nonzero_exit_does_not_abort_the_rest_of_install_sh(self, tmp_path: Path) -> None:
        result = self._run_migration_block(tmp_path, exit_code=1)
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "AFTER_MIGRATION" in result.stdout, (
            "a failed legacy-config migration must not abort the rest of install.sh"
        )
        assert (
            "[install] warning: legacy config migration failed, run "
            "claude/.claude/scripts/migrate-legacy-config.sh directly to retry" in result.stderr
        )

    def test_zero_exit_prints_no_warning(self, tmp_path: Path) -> None:
        result = self._run_migration_block(tmp_path, exit_code=0)
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "AFTER_MIGRATION" in result.stdout
        assert result.stderr == ""

    def test_migration_execution_precedes_configure_opt_ins_call_site_in_file_order(
        self,
    ) -> None:
        """install.sh's own comment on this block states the ordering is
        deliberate: a freshly-imported legacy value must be what the
        interactive opt-in prompts (and the report) see. Pinned against
        install.sh's own text, the same technique
        TestBlockOrderingDependency uses above, since a functional test
        can't observe a real-call-site reordering (every test in this
        class runs the migration block in isolation, bypassing
        configure_machine_level_opt_ins's own TTY gate)."""
        install_text = _INSTALL_SH.read_text()
        migration_call_index = install_text.index(
            '"$REPO_DIR/claude/.claude/scripts/migrate-legacy-config.sh"'
        )
        configure_call_site_index = install_text.index("\nconfigure_machine_level_opt_ins\n")
        assert migration_call_index < configure_call_site_index, (
            "migrate-legacy-config.sh must execute before "
            "configure_machine_level_opt_ins's real call site, or a "
            "freshly-imported legacy value isn't visible to this run's prompts"
        )


class TestConfigureMachineLevelOptInsNonInteractiveSnapshot:
    def test_non_tty_run_leaves_full_home_snapshot_byte_identical(
        self, tmp_path: Path
    ) -> None:
        """report_sentinel_inventory is read-only by design, and
        configure_machine_level_opt_ins must still no-op under closed
        stdin now that both are schema-driven. The pr-cost-disclosure
        legacy file is included here so a reporter that rewrote it in
        place (rather than only reading it) would be caught."""
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
            + _config_sh_prelude()
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
    def test_absent_config_key_reports_default_state_and_docs_anchor(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "Worktree enforcement: false (source: default)" in result.stdout
        assert "docs: README.md § Worktree enforcement" in result.stdout

    def test_state_file_row_reports_config_file_source(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "claude-config.toml").write_text(
            "permission_prompt_tracking = true\n"
        )
        repo = tmp_path / "repo"
        repo.mkdir()

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "Permission-prompt tracking: true (source: config file)" in result.stdout

    def test_legacy_file_row_reports_legacy_file_source(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "worktree-required").write_text("")
        repo = tmp_path / "repo"
        repo.mkdir()

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "Worktree enforcement: true (source: legacy file)" in result.stdout

    def test_repo_scope_reports_current_repo_only(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        repo = tmp_path / "repo"
        (repo / ".claude").mkdir(parents=True)
        (repo / ".claude" / "worktree-optout").write_text("")

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "Worktree enforcement opt-out (this repo): ENABLED (.claude/worktree-optout)" in result.stdout
        assert "Worktree enforcement (committed, this repo): disabled" in result.stdout

    def test_pr_cost_disclosure_dollars_reports_enum_value_and_source(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "pr-cost-disclosure").write_text("dollars\n")
        repo = tmp_path / "repo"
        repo.mkdir()

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "PR cost disclosure: dollars (source: legacy file)" in result.stdout

    def test_pr_cost_disclosure_absent_reports_false_default(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "PR cost disclosure: false (source: default)" in result.stdout

    def test_unresolvable_config_dir_reports_could_not_resolve_for_non_probed_key(
        self, tmp_path: Path
    ) -> None:
        """A relative CLAUDE_CONFIG_DIR makes the config dir unresolvable.
        autonomous_shipping carries legacy-probe-on-resolution-failure:
        false (config-keys.psv), so it must surface the resolution failure
        rather than silently falling back."""
        home = tmp_path / "home"
        home.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        env = _base_env(home, repo)
        env["CLAUDE_CONFIG_DIR"] = "relative/not-absolute"

        result = _run_report(env)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert (
            "Autonomous shipping: could not resolve (CLAUDE_CONFIG_DIR is a "
            "relative path, or $HOME is unset/empty)" in result.stdout
        )

    def test_worktree_required_falls_back_to_home_legacy_probe_on_resolution_failure(
        self, tmp_path: Path
    ) -> None:
        """worktree_required is the one key with
        legacy-probe-on-resolution-failure: true -- on a resolution
        failure the reporter's own `else source="legacy file"` branch
        fires (there is no config-dir-side state/legacy file to check),
        since _config_value itself already resolved via a raw
        $HOME/.claude probe."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "worktree-required").write_text("")
        repo = tmp_path / "repo"
        repo.mkdir()
        env = _base_env(home, repo)
        env["CLAUDE_CONFIG_DIR"] = "relative/not-absolute"

        result = _run_report(env)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "Worktree enforcement: true (source: legacy file)" in result.stdout

    def test_config_dir_or_home_union_prints_home_value_when_it_differs(
        self, tmp_path: Path
    ) -> None:
        """worktree_required's config-dir-or-home resolution unions the
        resolved config dir with the literal $HOME/.claude -- when
        CLAUDE_CONFIG_DIR diverges from $HOME/.claude, the reporter prints
        $HOME/.claude's own value/source on a second, indented line so a
        home-side-only opt-in stays diagnosable."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "worktree-required").write_text("")
        config_dir = tmp_path / "other-config-dir"
        config_dir.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        env = _base_env(home, repo)
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)

        result = _run_report(env)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "Worktree enforcement: true (source: default)" in result.stdout
        assert f"{home}/.claude: true (source: legacy file)" in result.stdout

    def test_config_dir_or_home_union_prints_no_home_line_when_config_dir_unset(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        for line in result.stdout.splitlines():
            assert not line.strip().startswith(f"{home}/.claude:"), (
                "no home-divergence line expected when CLAUDE_CONFIG_DIR is unset "
                f"(resolved config dir already is $HOME/.claude): {line!r}"
            )

    def test_enable_hint_suppressed_for_repo_marker_index_prompted_this_run(
        self, tmp_path: Path
    ) -> None:
        """A repo-marker row configure_machine_level_opt_ins already
        prompted about this run must not also print the enable-hint -- the
        state is still shown, just without a redundant "here's the
        command" line. No config key needs this suppression any more (row
        39: none of their CTAs exist), so this test targets
        REPO_MARKER_INVENTORY's own index-0 row directly."""
        home = tmp_path / "home"
        home.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        script = (
            "set -e\n"
            + _config_sh_prelude()
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
        # Index 0 is REPO_MARKER_INVENTORY's own worktree-required row --
        # "just prompted" -- no hint for it.
        assert "to enable: touch .claude/worktree-required" not in result.stdout, (
            f"unexpected enable-hint for a just-prompted row: {result.stdout!r}"
        )
        # A different, un-prompted repo marker still gets its hint.
        assert "to enable: touch .claude/worktree-optout" in result.stdout, (
            "an un-prompted disabled repo marker must still show its enable-hint"
        )


class TestPrCostDisclosureExpectedContentField:
    """pr_cost_disclosure's "dollars" comparison lives in config-keys.psv's
    own `type` column (`enum:dollars`), not a literal hardcoded in
    install.sh's reporter -- TestReportSentinelInventory's
    test_pr_cost_disclosure_dollars_reports_enum_value_and_source already
    covers that the generalized _report_config_key/_config_key_source path
    still surfaces it correctly; this pins the schema-level field itself
    didn't silently drop to a bare `bool`, which would turn the check into
    presence-only."""

    def test_pr_cost_disclosure_row_declares_enum_dollars_type(self) -> None:
        assert schema()["pr_cost_disclosure"].type == "enum:dollars", (
            "expected pr_cost_disclosure's type column to be 'enum:dollars'"
        )


class TestProseTighteningOptOutSentinel:
    """Behavioral coverage for pr_description_tighten_prose: an
    opt-out-polarity key (legacy-polarity presence-disables) whose default
    is `true` -- absence is the default-on state, presence of its legacy
    file opts out."""

    def test_absent_reports_default_true_state(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "Prose-tightening pass: true (source: default)" in result.stdout

    def test_legacy_optout_file_present_reports_false_state(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "pr-description-tighten-prose-optout").write_text("")
        repo = tmp_path / "repo"
        repo.mkdir()

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "Prose-tightening pass: false (source: legacy file)" in result.stdout
