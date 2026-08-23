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


# Frozen text constant, not `git show HEAD:install.sh` -- this repo
# squash-merges with branch deletion, so a `HEAD`-literal or pinned-SHA
# lookup would self-reference or dangle once the source commit is pruned.
_PRE_TIGHTEN_PROSE_SENTINEL_BLOCK = r"""
SENTINEL_INVENTORY=(
  "worktree-required|machine-promptable|Worktree enforcement|Denies git commit/push/etc. outside a linked worktree on every repo without a per-repo .claude/worktree-optout. See README 'Worktree enforcement'.|disabled|README.md § Worktree enforcement"
  "autonomous-shipping-required|machine-promptable|Autonomous shipping|Lets Claude Code commit, push, and open PRs without asking first, on every repo without a per-repo .claude/autonomous-shipping-optout. A repo cannot enable this by committing anything — only this machine-level file can. See README 'Autonomous shipping'.|disabled|README.md § Autonomous shipping"
  "track-permission-prompts|machine-promptable|Permission-prompt tracking|Logs each interactive permission-prompt Notification (credential-shaped values redacted) to ~/.claude/.permission-prompt-log.jsonl, so you can see which commands still trigger a prompt under auto permission mode. No per-repo opt-out.|disabled|docs/permission-prompt-tracking.md"
  ".error-mode-nudge-enabled|machine-promptable|Error-mode analysis nudge|Nudges you to run /error-mode-analysis after a repeated-failure sequence in a session, so a stuck debugging loop gets flagged instead of continuing silently. See docs/error-mode-nudge.md.|disabled|docs/error-mode-nudge.md"
  ".cost-ledger-enabled|machine-promptable|Cost ledger recording|Lets cost-ledger --record append this machine's weekly cost/efficiency figures to \$CLAUDE_CONFIG_DIR/cost-ledger.md (override via COST_LEDGER_PATH) — outlives the source transcripts once they age out and get deleted, though it's a single local file with no automatic backup. See docs/cost-ledger.md.|disabled|docs/cost-ledger.md"
  ".pr-cost-enabled|machine-promptable|PR cost ledger recording|Lets pr-cost --record durably append this machine's per-PR AI-tooling dollar cost rows to \$CLAUDE_CONFIG_DIR/pr-cost-ledger.tsv (override via PR_COST_LEDGER_PATH) — unlike the weekly cost ledger's aggregate-only rows, these carry branch names and repo identifiers, and outlive the source transcripts once they age out and get deleted, though it's a single local file with no automatic backup. See docs/pr-cost.md.|disabled|docs/pr-cost.md"
  ".handoff-nudge-disabled|machine|Handoff-near-cap nudge suppression||disabled|docs/handoff-nudge.md"
  ".consume-durable-continuity-disabled|machine|Durable-continuity auto-consume suppression||disabled|docs/hooks.md § Utility hooks"
  ".commit-stall-block-disabled|machine|Commit-stall auto-advance suppression||disabled|docs/commit-stall-block.md"
  ".session-title-disabled|machine|Branch-based session-title suppression (machine-wide)||disabled|docs/hooks.md § Utility hooks"
  ".claude/worktree-required|repo|Worktree enforcement (committed, this repo)||disabled|README.md § Worktree enforcement"
  ".claude/worktree-optout|repo|Worktree enforcement opt-out (this repo)||disabled|README.md § Worktree enforcement"
  ".claude/autonomous-shipping-optout|repo|Autonomous-shipping opt-out (this repo)||disabled|README.md § Autonomous shipping"
  ".claude/session-title-disabled|repo|Branch-based session-title suppression (this repo)||disabled|docs/hooks.md § Utility hooks"
  "pr-cost-disclosure|account|PR cost disclosure (this account)||disabled|README.md § PR cost disclosure"
)

# Whether $1 (a zero-based SENTINEL_INVENTORY index) was prompted by
# configure_machine_level_opt_ins during this run — report_sentinel_inventory
# uses this to suppress a redundant enable-hint for a sentinel the user was
# just asked about.
_sentinel_index_prompted_this_run() {
  case " ${SENTINEL_INVENTORY_PROMPTED_INDICES:-} " in
    *" $1 "*) return 0 ;;
    *) return 1 ;;
  esac
}

# Prints "ENABLED" when $1 exists, else $2 (the row's own default-state,
# always "disabled" per the schema comment above) — the same two labels
# _prompt_sentinel_opt_in's own prompts already use.
_sentinel_state_label() {
  if [ -f "$1" ]; then
    printf 'ENABLED'
  else
    printf '%s' "$2"
  fi
}

_report_machine_sentinel() {
  local sentinel_index="$1" path_template="$2" human_name="$3" default_state="$4" docs_anchor="$5" diverged_config_dir="$6"
  local home_path="$HOME/.claude/$path_template"
  if [ -n "$diverged_config_dir" ]; then
    local config_dir_path="$diverged_config_dir/$path_template"
    # shellcheck disable=SC2016 # single-quoted deliberately — $HOME must stay
    # unexpanded here, naming the literal env var in the diagnostic message,
    # not this run's own resolved value (already printed on the next line).
    printf '  %s: DIVERGED — CLAUDE_CONFIG_DIR and $HOME/.claude disagree\n' "$human_name"
    printf '    %s: %s\n' "$home_path" "$(_sentinel_state_label "$home_path" "$default_state")"
    printf '    %s: %s\n' "$config_dir_path" "$(_sentinel_state_label "$config_dir_path" "$default_state")"
    printf '    docs: %s\n' "$docs_anchor"
    return 0
  fi
  local state
  state="$(_sentinel_state_label "$home_path" "$default_state")"
  printf '  %s: %s (%s)\n' "$human_name" "$state" "$home_path"
  printf '    docs: %s\n' "$docs_anchor"
  if [ "$state" = "$default_state" ] && ! _sentinel_index_prompted_this_run "$sentinel_index"; then
    printf '    → to enable: touch %s\n' "$home_path"
  fi
}

_report_repo_sentinel() {
  local sentinel_index="$1" path_template="$2" human_name="$3" default_state="$4" docs_anchor="$5"
  local repo_path="$REPO_DIR/$path_template"
  local state
  state="$(_sentinel_state_label "$repo_path" "$default_state")"
  printf '  %s: %s (%s)\n' "$human_name" "$state" "$path_template"
  printf '    docs: %s\n' "$docs_anchor"
  if [ "$state" = "$default_state" ] && ! _sentinel_index_prompted_this_run "$sentinel_index"; then
    printf '    → to enable: touch %s\n' "$path_template"
  fi
}

# Content, not presence, is this row's state — see the mode grammar in
# claude/.claude/skills/pr-description/SKILL.md, whose gate this reporter
# mirrors byte-for-byte (same trim/lowercase/anchored-compare snippet, pinned
# in both places so they cannot silently diverge). Resolution, not union:
# $CLAUDE_CONFIG_DIR only when set and absolute, else $HOME/.claude — never
# both, so one account's opt-in cannot activate disclosure under another
# account's config dir.
_report_account_sentinel() {
  local sentinel_index="$1" path_template="$2" human_name="$3" docs_anchor="$4"
  local config_dir
  case "${CLAUDE_CONFIG_DIR:-}" in
    /*) config_dir="${CLAUDE_CONFIG_DIR%/}" ;;
    *) config_dir="$HOME/.claude" ;;
  esac
  local sentinel_path="$config_dir/$path_template"
  local state
  if [ ! -f "$sentinel_path" ]; then
    state="disabled"
  else
    local mode
    mode=$(cat "$sentinel_path" 2>/dev/null) || mode=""
    mode="${mode#"${mode%%[![:space:]]*}"}"
    mode="${mode%"${mode##*[![:space:]]}"}"
    mode=$(printf '%s' "$mode" | tr '[:upper:]' '[:lower:]')
    if [ "$mode" = "dollars" ]; then
      state="ENABLED (mode=dollars)"
    elif [ -z "$mode" ]; then
      state="disabled"
    else
      state="present but mode not recognized: \"$mode\" — treated as disabled"
    fi
  fi
  printf '  %s: %s (%s)\n' "$human_name" "$state" "$sentinel_path"
  # shellcheck disable=SC2016 # single-quoted deliberately — $HOME must stay
  # unexpanded here, naming the literal env var in the diagnostic message.
  printf '    the only path this scope checks — never falls back to $HOME/.claude when CLAUDE_CONFIG_DIR is set\n'
  printf '    docs: %s\n' "$docs_anchor"
  if [ ! -f "$sentinel_path" ] && ! _sentinel_index_prompted_this_run "$sentinel_index"; then
    printf '    → to enable: echo dollars > "%s"\n' "$sentinel_path"
  fi
}

# Read-only: creates and removes nothing. Called after
# configure_machine_level_opt_ins so a just-prompted row's hint can be
# suppressed. Resolves machine-scope state the way _lib_config_dir()
# (claude/.claude/hooks/_lib.sh) would: CLAUDE_CONFIG_DIR when it names a
# directory other than $HOME/.claude, else $HOME/.claude alone. When the two
# differ, both paths' state are printed and flagged as diverged rather than
# picking one — the prompt above only ever mutates $HOME/.claude, but some
# sentinel readers honor only CLAUDE_CONFIG_DIR with no fallback, so which
# copy is "the real one" genuinely depends on the specific sentinel.
report_sentinel_inventory() {
  echo ""
  echo "=== Opt-in sentinel inventory ==="
  local diverged_config_dir=""
  if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then
    local normalized_config_dir="${CLAUDE_CONFIG_DIR%/}"
    if [ "$normalized_config_dir" != "${HOME%/}/.claude" ]; then
      diverged_config_dir="$normalized_config_dir"
    fi
  fi

  local sentinel_index=0 entry path_template scope human_name prompt_description default_state docs_anchor
  for entry in "${SENTINEL_INVENTORY[@]}"; do
    IFS='|' read -r path_template scope human_name prompt_description default_state docs_anchor <<< "$entry"
    case "$scope" in
      machine-promptable | machine)
        _report_machine_sentinel "$sentinel_index" "$path_template" "$human_name" "$default_state" "$docs_anchor" "$diverged_config_dir"
        ;;
      repo)
        _report_repo_sentinel "$sentinel_index" "$path_template" "$human_name" "$default_state" "$docs_anchor"
        ;;
      account)
        _report_account_sentinel "$sentinel_index" "$path_template" "$human_name" "$docs_anchor"
        ;;
    esac
    sentinel_index=$((sentinel_index + 1))
  done
}
"""


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
        assert result.stderr == "", (
            f"sourcing SENTINEL_INVENTORY produced stderr -- likely an "
            f"unescaped backtick or $(...) in a row's field triggering "
            f"command substitution: {result.stderr!r}"
        )
        count = int(result.stdout.strip())
        assert count > 0, "SENTINEL_INVENTORY must not be empty"

    def test_every_row_has_six_to_eight_pipe_delimited_fields_with_no_surrounding_whitespace(
        self,
    ) -> None:
        """The schema comment requires no whitespace around any `|` --
        IFS='|' read -r bakes leading/trailing spaces into a field
        otherwise, a real drift the two original hardcoded prompt strings
        had no test guarding against. 6 fields is the original schema; 7
        (expected-content) and 8 (polarity) are optional trailing fields
        meaningful only for scope=account rows -- `read` fills them with
        empty strings for every row that omits them, so 6- and 7-field rows
        stay valid alongside the 8-field ones."""
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
            assert len(fields) in (6, 7, 8), (
                f"row {row!r} does not have 6, 7, or 8 fields"
            )
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


class TestSixFieldRowBackwardCompatibility:
    """The field-count widening at report_sentinel_inventory's `IFS='|'
    read -r` site and `_report_account_sentinel`'s new parameters must not
    change what any pre-existing 6-field row reports. (The sibling read
    site in configure_machine_level_opt_ins is not exercised here -- see
    the comment above that function.) Runs the frozen pre-refactor
    SENTINEL_INVENTORY/report_sentinel_inventory (`_PRE_TIGHTEN_PROSE_
    SENTINEL_BLOCK`) and the current one against the same fixture, and
    checks every pre-existing line still appears in the new output --
    order-insensitive and one-directional (new output may contain
    additional lines for rows the old block didn't have; it must not be
    missing or alter any line the old block produced), not a hand-built
    "expected" string on either side."""

    def test_every_preexisting_line_survives(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        env = _base_env(home, repo)

        head_script = "set -e\n" + _PRE_TIGHTEN_PROSE_SENTINEL_BLOCK + "\nreport_sentinel_inventory\n"
        head_result = subprocess.run(
            [_BASH, "-c", head_script], capture_output=True, text=True, check=False, env=env
        )
        assert head_result.returncode == 0, f"stderr={head_result.stderr!r}"

        new_result = _run_report(env)
        assert new_result.returncode == 0, f"stderr={new_result.stderr!r}"

        missing = [line for line in head_result.stdout.splitlines() if line not in new_result.stdout.splitlines()]
        assert not missing, (
            f"post-change report_sentinel_inventory dropped or altered pre-change "
            f"line(s) for a 6-field row: {missing!r}"
        )

    def test_every_preexisting_line_survives_with_a_sentinel_present(self, tmp_path: Path) -> None:
        """Same comparison with a representative sentinel file present, so
        the regression check also covers the ENABLED/DIVERGED branches, not
        only the all-absent default state."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "worktree-required").write_text("")
        repo = tmp_path / "repo"
        (repo / ".claude").mkdir(parents=True)
        (repo / ".claude" / "worktree-optout").write_text("")

        env = _base_env(home, repo)
        head_script = "set -e\n" + _PRE_TIGHTEN_PROSE_SENTINEL_BLOCK + "\nreport_sentinel_inventory\n"
        head_result = subprocess.run(
            [_BASH, "-c", head_script], capture_output=True, text=True, check=False, env=env
        )
        assert head_result.returncode == 0, f"stderr={head_result.stderr!r}"

        new_result = _run_report(env)
        assert new_result.returncode == 0, f"stderr={new_result.stderr!r}"

        missing = [line for line in head_result.stdout.splitlines() if line not in new_result.stdout.splitlines()]
        assert not missing, (
            f"post-change report_sentinel_inventory dropped or altered pre-change "
            f"line(s) for a 6-field row: {missing!r}"
        )


class TestPrCostDisclosureExpectedContentField:
    """pr-cost-disclosure's "dollars" comparison lives in field 7 of its own
    row, not a literal hardcoded in _report_account_sentinel. Pin the row
    still carries it explicitly -- the existing dollars-mode behavioral
    tests above (test_account_sentinel_exact_dollars_reports_enabled and
    neighbors) already cover that the generalized function still enforces
    it correctly; this pins the source-level field itself didn't silently
    drop to empty (which would turn the check into presence-only)."""

    def test_pr_cost_disclosure_row_declares_dollars_as_expected_content(self) -> None:
        install_text = _INSTALL_SH.read_text()
        start = install_text.find(_INVENTORY_START)
        end = install_text.find(_INVENTORY_END, start)
        block = install_text[start:end]
        rows = [line.strip()[1:-1] for line in block.splitlines() if line.strip().startswith('"')]
        cost_row = next(r for r in rows if r.startswith("pr-cost-disclosure|"))
        fields = cost_row.split("|")
        assert len(fields) >= 7, f"expected pr-cost-disclosure row to carry field 7, row={cost_row!r}"
        assert fields[6] == "dollars", (
            f"expected field 7 (expected-content) to be exactly 'dollars', row={cost_row!r}"
        )


class TestProseTighteningOptOutSentinel:
    """Behavioral coverage for the opt-out-polarity account row: absence is
    the default-on state, presence opts out."""

    def test_absent_reports_default_enabled_state_with_disable_hint(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        sentinel_path = home / ".claude" / "pr-description-tighten-prose-optout"

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert (
            f"Prose-tightening pass opt-out (this account): enabled ({sentinel_path})" in result.stdout
        )
        assert f'to disable: touch "{sentinel_path}"' in result.stdout

    def test_present_reports_disabled_state_without_cta(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        sentinel_path = home / ".claude" / "pr-description-tighten-prose-optout"
        sentinel_path.write_text("")
        repo = tmp_path / "repo"
        repo.mkdir()

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert (
            f"Prose-tightening pass opt-out (this account): DISABLED ({sentinel_path})" in result.stdout
        )
        assert f'to disable: touch "{sentinel_path}"' not in result.stdout
        assert f'to enable: touch "{sentinel_path}"' not in result.stdout

    def test_present_with_content_still_reports_disabled(self, tmp_path: Path) -> None:
        """Presence-only check (field 7 empty) -- unlike pr-cost-disclosure's
        content-mode row, any content in the file still counts as present."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        sentinel_path = home / ".claude" / "pr-description-tighten-prose-optout"
        sentinel_path.write_text("because I said so\n")
        repo = tmp_path / "repo"
        repo.mkdir()

        result = _run_report(_base_env(home, repo))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert (
            f"Prose-tightening pass opt-out (this account): DISABLED ({sentinel_path})" in result.stdout
        )


class TestAccountSentinelPolarityFallback:
    """Pins the explicit design decision documented in install.sh's schema
    comment: an unrecognized polarity value falls back to opt-in behavior
    rather than being rejected at definition time."""

    def _report_with_polarity(
        self, polarity: str, present: bool, tmp_path: Path, default_state: str = "disabled"
    ) -> str:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        sentinel_path = home / ".claude" / "some-sentinel"
        if present:
            sentinel_path.write_text("")
        repo = tmp_path / "repo"
        repo.mkdir()
        script = (
            "set -e\n"
            + _extract_inventory_block()
            + f'\n_report_account_sentinel 0 some-sentinel "Some sentinel" "{default_state}" "docs/x.md" "" "{polarity}"\n'
        )
        result = subprocess.run(
            [_BASH, "-c", script],
            capture_output=True,
            text=True,
            check=False,
            env=_base_env(home, repo),
        )
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        return result.stdout

    def test_unrecognized_polarity_absent_behaves_as_opt_in(self, tmp_path: Path) -> None:
        stdout = self._report_with_polarity("sideways", present=False, tmp_path=tmp_path)
        assert "Some sentinel: disabled" in stdout
        assert "to enable: touch" in stdout

    def test_unrecognized_polarity_present_behaves_as_opt_in(self, tmp_path: Path) -> None:
        stdout = self._report_with_polarity("sideways", present=True, tmp_path=tmp_path)
        assert "Some sentinel: ENABLED" in stdout

    def test_unrecognized_polarity_on_enabled_default_state_row_keeps_cta_consistent(
        self, tmp_path: Path
    ) -> None:
        """The real opt-out row (pr-description-tighten-prose-optout) has
        default_state="enabled" -- an unrecognized polarity on a row shaped
        like that one must not print a "to enable" CTA next to a state that
        already reads "enabled"."""
        stdout = self._report_with_polarity(
            "Opt-Out", present=False, tmp_path=tmp_path, default_state="enabled"
        )
        assert "Some sentinel: enabled" in stdout
        assert "to disable: touch" in stdout
        assert "to enable" not in stdout


class TestContentModePolarityIsIgnored:
    """No current SENTINEL_INVENTORY row combines a non-empty expected-content
    with an opt-out polarity, but nothing in the schema forbids it -- pin
    that the CTA text stays consistent with the state text for this
    combination rather than instructing the opposite of what it produces.
    Content-mode's state computation never reads polarity (see the comment
    above _report_account_sentinel), so the CTA must not read it either."""

    def test_content_mode_cta_stays_to_enable_even_with_opt_out_polarity(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        script = (
            "set -e\n"
            + _extract_inventory_block()
            + '\n_report_account_sentinel 0 hybrid-sentinel "Hybrid sentinel" disabled '
            '"docs/x.md" "dollars" "opt-out"\n'
        )
        result = subprocess.run(
            [_BASH, "-c", script],
            capture_output=True,
            text=True,
            check=False,
            env=_base_env(home, repo),
        )
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "Hybrid sentinel: disabled" in result.stdout
        assert "to enable: echo dollars >" in result.stdout
        assert "to disable" not in result.stdout


class TestContentModeRowsUseDisabledDefaultState:
    """Schema-level pin for the invariant stated in the SENTINEL_INVENTORY
    schema comment: content-mode rows (non-empty expected-content) must use
    default_state="disabled". The CTA guard only keys presence-mode off
    default_state -- a content-mode row with default_state="enabled" would
    reproduce the same state/CTA desync TestAccountSentinelPolarityFallback
    pins for presence-mode, since content-mode's CTA never reaches that
    check at all (short-circuited by the expected_content guard)."""

    def test_every_content_mode_row_uses_disabled_default_state(self) -> None:
        install_text = _INSTALL_SH.read_text()
        start = install_text.find(_INVENTORY_START)
        end = install_text.find(_INVENTORY_END, start)
        block = install_text[start:end]
        rows = [line.strip()[1:-1] for line in block.splitlines() if line.strip().startswith('"')]
        for row in rows:
            fields = row.split("|")
            expected_content = fields[6] if len(fields) > 6 else ""
            if expected_content:
                assert fields[4] == "disabled", (
                    f"content-mode row {row!r} must use default_state=disabled -- "
                    "its CTA is never keyed off default_state, so default_state="
                    "enabled would desync the printed state from the CTA"
                )
