"""Tests for transcript-analysis.py."""
import argparse
import errno
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

import pytest
from helpers import SKILLS_DIR

from ._audit_routing_helpers import _audit_routing_samples_args, _audit_routing_shape_args
from ._cache_efficiency_helpers import _cache_efficiency_args
from ._cache_rebuild_helpers import _cache_rebuild_args
from ._commit_gate_helpers import _gate_args
from ._handoff_nudge_helpers import _rearm_backtest_args, _spend_over_threshold_args
from ._session_signal_helpers import _SONNET, _branch_args, _ui_user, _user_input_args
from ._skill_helpers import (
    _TS_FIXED,
    _judgment_pair_args,
    _review_asst,
    _skill_inv_args,
    _skill_pair_args,
    _user_reply,
)
from ._subagent_helpers import _subagents_args
from .conftest import (
    _TASK_NOTIFICATION_TEXT,
    _agent_use,
    _asst,
    _attr_ts,
    _audit_routing_args,
    _context_distribution_args,
    _cost_args,
    _cost_ledger_args,
    _cost_trend_args,
    _extract_grand_total,
    _opus,
    _priced,
    _read_use,
    _review_trace_args,
    _reviewer_yield_args,
    _skill_use,
    _skill_use_rec,
    _table_cols,
    _task_notification_msg,
    _tool_result,
    _two_declared_roots,
    _user_msg,
    _write_cost_root,
    _write_jsonl,
    _write_subagent_dispatch,
    _write_subagent_jsonl,
)

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


def test_projects_dir_honors_claude_config_dir(monkeypatch, tmp_path):
    """scope.PROJECTS_DIR is computed at import time from config_dir(); a fresh
    import of transcript_analysis.scope with CLAUDE_CONFIG_DIR set resolves
    under that directory instead of ~/.claude.

    Loaded by path (not a plain `import transcript_analysis.scope`, which
    would hit sys.modules' already-imported copy) for the same reason _mod
    above is: the module-level PROJECTS_DIR assignment only re-runs on a
    genuinely fresh exec.
    """
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    scope_path = _SCRIPT.parent / "transcript_analysis" / "scope.py"
    spec = importlib.util.spec_from_file_location("transcript_analysis_scope_config_dir_case", scope_path)
    scope_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scope_mod)
    assert tmp_path / "projects" == scope_mod.PROJECTS_DIR


def test_scope_import_does_not_crash_when_home_unset(monkeypatch):
    """Importing transcript_analysis.scope must not raise even when $HOME is
    unset/empty and CLAUDE_CONFIG_DIR is not set -- PROJECTS_DIR resolves
    lazily, on first access, not at import time, so an importer that never
    touches PROJECTS_DIR (e.g. analyze-context.py's own `--help` path) never
    pays for a $HOME-unset resolution failure it doesn't need to hit."""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", "")
    scope_path = _SCRIPT.parent / "transcript_analysis" / "scope.py"
    spec = importlib.util.spec_from_file_location(
        "transcript_analysis_scope_home_unset_case", scope_path
    )
    scope_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scope_mod)  # must not raise


def test_resolve_scan_roots_default_root_when_home_unset(monkeypatch, tmp_path):
    """resolve_scan_roots' own internal `PROJECTS_DIR` references -- converted
    to `_projects_dir()` calls so they go through PEP 562 lazy resolution --
    must still resolve correctly when $HOME is unset. A bare-name reference
    left unconverted after PROJECTS_DIR became a lazy module attribute would
    raise NameError here: a bare global reference inside the module's own
    function bodies never reaches scope.py's __getattr__, unlike external
    `scope.PROJECTS_DIR` attribute access.
    """
    monkeypatch.setenv("HOME", "")
    config_dir = tmp_path / "active-account"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    scope_path = _SCRIPT.parent / "transcript_analysis" / "scope.py"
    spec = importlib.util.spec_from_file_location(
        "transcript_analysis_scope_resolve_roots_home_unset_case", scope_path
    )
    scope_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scope_mod)

    roots = scope_mod.resolve_scan_roots(argparse.Namespace())

    assert roots == [config_dir / "projects"]


class TestConfigDirFlag:
    """--config-dir reassigns scope.PROJECTS_DIR after argument parsing,
    distinct from the CLAUDE_CONFIG_DIR-at-import-time behavior
    test_projects_dir_honors_claude_config_dir covers above. Every test here
    registers _mod.scope.PROJECTS_DIR with monkeypatch before calling main()
    (even when just re-setting it to its current value) so main()'s direct
    `scope.PROJECTS_DIR = ...` reassignment — which bypasses monkeypatch — is
    still restored to its pre-test value at teardown, protecting the rest of
    this shared, once-imported module from leaking state across tests."""

    def test_flag_set_enumerates_sessions_from_the_fixture_dir(self, monkeypatch, tmp_path, capsys):
        """The reassignment's core invariant: a subcommand actually finds
        sessions under PATH/projects, not the module's default. The no-flag
        and error-path tests below can both pass while this one is broken.

        Asserts on captured stdout, not just the PROJECTS_DIR attribute --
        the attribute alone would stay green even if `buckets`' own
        session-iteration path captured PROJECTS_DIR into a local before
        main()'s post-parse reassignment, silently reporting zero sessions
        (the exact regression class this feature exists to close)."""
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", _mod.scope.PROJECTS_DIR)
        config_dir = tmp_path / "other-account"
        proj = config_dir / "projects" / "-home-user-testrepo"
        proj.mkdir(parents=True)
        _write_jsonl(proj / "s.jsonl", [_asst("claude-sonnet-4-6", branch="feat-config-dir")])

        monkeypatch.setattr(sys, "argv", ["transcript-analysis.py", "--config-dir", str(config_dir), "buckets"])
        _mod.main()

        assert config_dir / "projects" == _mod.scope.PROJECTS_DIR
        out = capsys.readouterr().out
        assert "feat-config-dir" in out, (
            f"the seeded session's branch never surfaced in `buckets` output: {out!r}"
        )

    def test_no_flag_leaves_projects_dir_unchanged_after_parsing(self, monkeypatch, tmp_path):
        """Without --config-dir, main()'s post-parse reassignment must not
        run at all. test_projects_dir_honors_claude_config_dir only covers
        import time; the regression risk this guards is in the reassignment
        main() performs after parsing, e.g. an unguarded `Path(None)`."""
        fixture_projects_dir = tmp_path / "projects"
        fixture_projects_dir.mkdir()
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", fixture_projects_dir)

        monkeypatch.setattr(sys, "argv", ["transcript-analysis.py", "buckets"])
        _mod.main()

        assert fixture_projects_dir == _mod.scope.PROJECTS_DIR

    def test_this_repo_loud_error_on_zero_matches(self, monkeypatch, tmp_path, capsys):
        """--config-dir + --this-repo, resolved against a config dir with no
        matching project directories, errors loudly instead of silently
        reporting an empty scope -- closes the original reported symptom
        (declaring no sessions exist for a container that has them)."""
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", _mod.scope.PROJECTS_DIR)
        config_dir = tmp_path / "other-account"
        (config_dir / "projects").mkdir(parents=True)  # exists, but empty

        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        monkeypatch.setattr(
            sys, "argv",
            ["transcript-analysis.py", "--config-dir", str(config_dir), "buckets", "--this-repo"],
        )
        with pytest.raises(SystemExit):
            _mod.main()

        err = capsys.readouterr().err
        assert "--config-dir" in err
        assert "buckets" in err

    @pytest.mark.parametrize("subcommand", _mod.scope._SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR)
    def test_top_level_config_dir_refused_for_subcommands_with_their_own(
        self, monkeypatch, tmp_path, capsys, subcommand
    ):
        """Every subcommand in _SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR
        refuses the top-level --config-dir outright. Most resolve their own
        scan roots via their own --config-dir (_resolve_cost_roots ->
        config_dir() + declared_transcript_roots()), never reading the
        module-global PROJECTS_DIR this top-level flag reassigns. Letting the
        top-level flag through silently would reassign an unused global while
        the actual scan root stays whatever config_dir() resolves to -- an
        operator typing --config-dir /other-account cost would see no error
        and would silently scan their own default account instead. main()
        refuses the combination outright, matching every other subcommand's
        actually-effective top-level --config-dir. The refusal is
        unconditional on subcommand alone, checked before args.this_repo is
        ever read, so a bare subcommand invocation (no --this-repo) is the
        correct, strictly-scoped regression pin -- a --this-repo variant
        would hit the identical check with no new branch coverage.
        Parametrized directly off the tuple (not a hand-maintained list) so a
        future entry is covered automatically."""
        other_account = tmp_path / "other-account"
        (other_account / "projects").mkdir(parents=True)
        active_config_dir = tmp_path / "active-account"
        (active_config_dir / "projects").mkdir(parents=True)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(active_config_dir))

        monkeypatch.setattr(
            sys, "argv",
            ["transcript-analysis.py", "--config-dir", str(other_account), subcommand],
        )
        with pytest.raises(SystemExit) as exc_info:
            _mod.main()

        assert exc_info.value.code == 2
        assert other_account / "projects" != _mod.scope.PROJECTS_DIR  # refusal happens before reassignment
        err = capsys.readouterr().err
        assert "--config-dir" in err
        assert subcommand in err

    def test_message_recommends_the_subcommands_own_flag_when_it_has_one(self, monkeypatch, tmp_path, capsys):
        """Regression coverage for the other branch of main()'s hasattr
        check: a subcommand that DOES register its own --config-dir (e.g.
        "cost") must still get the "use that instead" recommendation --
        test_transcript_cost_counts.py's
        test_top_level_config_dir_message_omits_flag_recommendation only pins
        the branch that omits it."""
        other_account = tmp_path / "other-account"
        (other_account / "projects").mkdir(parents=True)
        monkeypatch.setattr(
            sys, "argv", ["transcript-analysis.py", "--config-dir", str(other_account), "cost"],
        )
        with pytest.raises(SystemExit) as exc_info:
            _mod.main()
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "use that instead: transcript-analysis.py cost --config-dir PATH" in err


class TestConfigDirRouting:
    def test_context_composition_registered_in_subcommands_with_own_config_dir(self):
        assert "context-composition" in _mod._SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR

    def test_top_level_config_dir_refused_end_to_end(self, monkeypatch, tmp_path, capsys):
        other_account = tmp_path / "other-account"
        (other_account / "projects").mkdir(parents=True)
        active_config_dir = tmp_path / "active-account"
        (active_config_dir / "projects").mkdir(parents=True)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(active_config_dir))

        monkeypatch.setattr(
            sys, "argv",
            ["transcript-analysis.py", "--config-dir", str(other_account), "context-composition"],
        )
        with pytest.raises(SystemExit) as exc_info:
            _mod.main()

        assert exc_info.value.code == 2
        assert other_account / "projects" != _mod.scope.PROJECTS_DIR
        err = capsys.readouterr().err
        assert "--config-dir" in err
        assert "context-composition" in err


# ---------------------------------------------------------------------------
# _table_cols self-tests
# ---------------------------------------------------------------------------


class TestTableColsHelper:
    """Unit tests for the _table_cols helper — verifies the safety properties
    the refactor depends on."""

    def test_basic_column_lookup(self):
        out = "Branch Lead Main\nfeat   1    0  \n"
        cols = _table_cols(out, header_contains="Lead", row_contains="feat")
        assert cols["Lead"] == "1"
        assert cols["Main"] == "0"

    def test_drop_leading_labels(self):
        out = "Branch Thread Opus Sonnet\nmain   main   1    2\n       sidechain 0  1\n"
        # sidechain row has Branch suppressed — one fewer leading token
        cols = _table_cols(out, header_contains="Thread", row_contains="sidechain",
                           drop_leading_labels=1)
        assert cols["Thread"] == "sidechain"
        assert cols["Opus"] == "0"
        assert cols["Sonnet"] == "1"

    def test_row_not_unique_raises(self):
        out = "Branch Lead\nfeat   1\nfeat   2\n"
        with pytest.raises(AssertionError, match="not unique"):
            _table_cols(out, header_contains="Lead", row_contains="feat")

    def test_row_too_few_tokens_raises(self):
        out = "Branch Lead Main\nfeat   1\n"
        with pytest.raises(AssertionError, match="fewer cells"):
            _table_cols(out, header_contains="Lead", row_contains="feat")

    def test_max_labels_excludes_trailing_multiword_header(self):
        # "Top subagent types" is 3 header tokens but 1 data token (—); max_labels=4
        # limits to Branch/Sess/Spawns/CR so the assertion len(values)>=len(labels) passes.
        out = "Branch Sess Spawns CR Top subagent types\nfeat   1    0      2 —\n"
        cols = _table_cols(out, header_contains="Spawns", row_contains="feat", max_labels=4)
        assert cols["Spawns"] == "0"
        assert cols["CR"] == "2"

    def test_row_startswith_excludes_indented_lines(self):
        # "code-review" appears at column 0 in the data row and indented in the summary.
        out = "skill  top-level user-slash\ncode-review  1  0\n  code-review (1 top)\n"
        cols = _table_cols(out, header_contains="user-slash", row_contains="code-review",
                           row_startswith=True)
        assert cols["top-level"] == "1"
        assert cols["user-slash"] == "0"

    def test_header_not_unique_raises(self):
        # Two lines both contain "Lead" → header ambiguity must raise, not silently pick one.
        out = "Bin Lead Main\n2026-W20 1 0\nLead row duplicate 0 1\n"
        with pytest.raises(AssertionError, match="not unique"):
            _table_cols(out, header_contains="Lead", row_contains="2026-W20")

    def test_occurrence_scoping_excludes_content_after_the_selected_block(self):
        """occurrence=N must stop its row/header search at its own table's
        boundary (next blank line or next header-containing line), not
        search to the end of `out` — every existing occurrence= call site
        (reviewer-yield's Table 2, always the last table printed, with a
        composite row_contains already globally unique) would stay green
        even if that boundary were dropped, since there is nothing after
        Table 2 for an unscoped search to leak into. This fixture puts a
        second occurrence directly after the first's true boundary, with a
        row that would also satisfy `row_contains` if the boundary weren't
        enforced — occurrence=1 must select only the first table's row."""
        out = "\n".join([
            "Header      Count",
            "------------------",
            "alpha           1",
            "",
            "Header      Count",
            "------------------",
            "alpha           2",
        ])
        cols = _table_cols(out, header_contains="Header", row_contains="alpha", occurrence=1)
        assert cols["Count"] == "1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_fam_opus(self):
        assert _mod._fam("claude-opus-4-7") == "opus"

    def test_fam_sonnet(self):
        assert _mod._fam("claude-sonnet-4-6") == "sonnet"

    def test_fam_haiku(self):
        assert _mod._fam("claude-haiku-4-5") == "haiku"

    def test_fam_unknown_model(self):
        assert _mod._fam("gpt-4") == "other"

    def test_content_text_plain_string(self):
        assert _mod._content_text("hello world") == "hello world"

    def test_content_text_list_joins_text_blocks(self):
        content = [{"type": "text", "text": "hello"}, {"type": "tool_use", "name": "Bash"}, {"type": "text", "text": "world"}]
        assert _mod._content_text(content) == "hello world"

    def test_content_text_none_returns_empty(self):
        assert _mod._content_text(None) == ""

    def test_parse_ts_valid_iso_z(self):
        ts = _mod._parse_ts("2024-06-01T12:00:00.000Z")
        assert ts is not None and ts > 0

    def test_parse_ts_none_input(self):
        assert _mod._parse_ts(None) is None

    def test_parse_ts_invalid_string(self):
        assert _mod._parse_ts("not-a-date") is None

    def test_iso_date_valid_returns_string(self):
        assert _mod._iso_date("2026-05-19") == "2026-05-19"

    def test_iso_date_invalid_month_raises_type_error(self):
        with pytest.raises(argparse.ArgumentTypeError):
            _mod._iso_date("2026-13-01")

    def test_iso_date_garbage_raises_type_error(self):
        with pytest.raises(argparse.ArgumentTypeError):
            _mod._iso_date("garbage")

    def test_strip_task_notifications_multiline_envelope_removed(self):
        text = "<task-notification>\n<status>completed</status>\n<summary>done</summary>\n</task-notification>"
        assert _mod._strip_task_notifications(text) == " "

    def test_strip_task_notifications_two_envelopes_both_removed(self):
        text = "<task-notification>first</task-notification> and <task-notification>second</task-notification>"
        assert _mod._strip_task_notifications(text) == "  and  "

    def test_strip_task_notifications_adjacent_envelopes_both_removed(self):
        text = "<task-notification>a</task-notification><task-notification>b</task-notification>"
        assert _mod._strip_task_notifications(text) == "  "

    def test_strip_task_notifications_envelope_free_string_unchanged(self):
        text = "just a plain user prompt with no envelope"
        assert _mod._strip_task_notifications(text) == text

    def test_strip_does_not_weld_words_across_envelope_boundary(self):
        """Single-space substitution keeps 'try' and 'again' separated across a stripped envelope boundary."""
        text = "...try <task-notification>still failing</task-notification>again..."
        result = _mod._strip_task_notifications(text)
        assert "try again" not in result
        assert result == "...try  again..."

    def test_strip_task_notifications_unterminated_opener_left_in_place(self):
        text = "<task-notification><summary>no closing tag here"
        assert _mod._strip_task_notifications(text) == text

    def test_strip_task_notifications_unterminated_opener_preserves_trailing_turn_content(self):
        text = "<task-notification><summary>no closing tag here" + "\nuser: please retry the deploy"
        assert _mod._strip_task_notifications(text) == text

    def test_strip_task_notifications_orphan_closer_with_no_opener_left_in_place(self):
        text = "no opener here</task-notification>"
        assert _mod._strip_task_notifications(text) == text

    def test_strip_task_notifications_orphan_closer_adjacent_to_real_envelope(self):
        """A match requires the opener literal first, so the stray closer with no preceding
        opener is inert. Only the well-formed envelope after it is stripped to a single space."""
        text = "stray</task-notification> then <task-notification><summary>real</summary></task-notification> after"
        assert _mod._strip_task_notifications(text) == "stray</task-notification> then   after"

    def test_strip_task_notifications_nested_self_quoting_stops_at_first_closer(self):
        """A `<summary>` field quotes a full envelope inline. The non-greedy match stops at the
        first `</task-notification>` (the inner envelope's own closer), leaving the outer envelope's
        closing tags dangling in the remainder."""
        text = (
            "<task-notification><summary>Sample record: "
            "<task-notification><summary>inner</summary></task-notification>"
            "</summary></task-notification>"
        )
        assert _mod._strip_task_notifications(text) == " </summary></task-notification>"

    def test_strip_task_notifications_preserves_non_ascii_text_around_envelope(self):
        text = (
            "café 🎉 —before "
            "<task-notification><summary>結果: café</summary></task-notification>"
            " after— 北京 😀"
        )
        result = _mod._strip_task_notifications(text)
        assert result == "café 🎉 —before   after— 北京 😀"

    def test_strip_task_notifications_empty_string_unchanged(self):
        assert _mod._strip_task_notifications("") == ""

    def test_strip_task_notifications_case_sensitive_uppercase_unchanged(self):
        """Case-sensitive match is deliberate: an uppercase-tagged string is left in
        place rather than trading a hypothetical miss for a real over-strip of
        user-typed text."""
        text = "<TASK-NOTIFICATION><summary>shout-cased</summary></TASK-NOTIFICATION>"
        assert _mod._strip_task_notifications(text) == text


class TestParseSinceNdArg:
    """The shared --since Nd parser behind cmd_audit_routing, _cost_report,
    cmd_audit_routing_shape, and cmd_audit_routing_samples."""

    @pytest.mark.timing
    def test_valid_nd_value_returns_timestamp_and_raw_string(self):
        since_ts, since_raw = _mod._parse_since_nd_arg(
            argparse.Namespace(since="1d"), "cost"
        )
        assert since_raw == "1d"
        assert since_ts == pytest.approx(time.time() - 86400, abs=1)

    def test_absent_since_returns_none_for_both(self):
        since_ts, since_raw = _mod._parse_since_nd_arg(
            argparse.Namespace(since=None), "cost"
        )
        assert since_ts is None
        assert since_raw is None

    def test_malformed_value_exits_nonzero_with_subcommand_in_message(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            _mod._parse_since_nd_arg(argparse.Namespace(since="not-a-window"), "cost")
        assert exc_info.value.code == 1
        assert "cost: --since: expected Nd like '35d'" in capsys.readouterr().err


class TestParseAbsoluteWindowArgs:
    """The shared inclusive-day absolute --since/--until DATE parser extracted
    from cmd_user_input/cmd_review_trace/cmd_judgment_pair/cmd_subagent_mix's
    identical six-line conversion."""

    def test_default_attrs_parse_since_and_until_as_inclusive_day_bounds(self):
        since_ts, until_ts = _mod._parse_absolute_window_args(
            argparse.Namespace(since="2026-05-01", until="2026-05-03"), "review-trace",
        )
        assert since_ts == _mod._parse_ts("2026-05-01T00:00:00Z")
        assert until_ts == _mod._parse_ts("2026-05-03T00:00:00Z") + 86400

    def test_absent_since_and_until_returns_none_for_both(self):
        since_ts, until_ts = _mod._parse_absolute_window_args(
            argparse.Namespace(since=None, until=None), "review-trace",
        )
        assert since_ts is None
        assert until_ts is None

    def test_parameterized_attrs_read_subagent_mix_since_date_until_date(self):
        """cmd_subagent_mix's own dest names (since_date/until_date) resolve
        through the same helper as the default since/until attrs -- the
        parameterized call cmd_subagent_mix's own call site uses."""
        since_ts, until_ts = _mod._parse_absolute_window_args(
            argparse.Namespace(since_date="2026-07-01", until_date="2026-07-02"), "subagent-mix",
            since_attr="since_date", until_attr="until_date",
        )
        assert since_ts == _mod._parse_ts("2026-07-01T00:00:00Z")
        assert until_ts == _mod._parse_ts("2026-07-02T00:00:00Z") + 86400


# ---------------------------------------------------------------------------
# _project_family (redaction.py)
# ---------------------------------------------------------------------------


class TestProjectFamily:
    def test_raw_slug_with_worktree_suffix_collapses_to_base_slug(self):
        assert _mod._project_family("-home-u-repo-a--claude-worktrees-branch-x") == "-home-u-repo-a"

    def test_raw_slug_without_worktree_suffix_returns_unchanged(self):
        assert _mod._project_family("-home-u-repo-a") == "-home-u-repo-a"


# ---------------------------------------------------------------------------
# redaction: root-scoped display labels and the --this-repo agent-type allowlist
# ---------------------------------------------------------------------------


class TestRootScopedDisplayLabel:
    """Direct unit coverage of redaction._root_scoped_display_label --
    cmd_subagents/cmd_subagent_mix exercise it only through their own
    redacted/disclosed output, so this class pins the disclosed path and its
    never-writes-into-redact_map invariant on their own."""

    @pytest.mark.parametrize("kind", ["branch", "agent-type"])
    def test_disclosed_label_is_account_prefixed_raw_value_and_leaves_map_empty(self, kind):
        redact_map: dict[tuple[int, str], str] = {}
        label = _mod.redaction._root_scoped_display_label(kind, 1, "feat-x", redact_map, disclose=True)
        assert label == "account-1/feat-x"
        assert redact_map == {}

    @pytest.mark.parametrize("kind", ["branch", "agent-type"])
    def test_non_disclosed_label_delegates_to_assign_root_scoped_redact_label(self, kind):
        redact_map: dict[tuple[int, str], str] = {}
        label = _mod.redaction._root_scoped_display_label(kind, 1, "feat-x", redact_map, disclose=False)
        assert label == _mod.redaction._assign_root_scoped_redact_label(kind, 1, "feat-x", {})
        assert redact_map == {(1, "feat-x"): label}

    def test_disclosed_call_interleaved_between_redacted_calls_does_not_shift_counter(self):
        """The direct proof of the "never writes into redact_map" invariant
        -- no integration test pins this unconditionally, since a real
        multi-root corpus can't isolate the counter position from the data
        it's built from."""
        redact_map: dict[tuple[int, str], str] = {}
        first = _mod.redaction._root_scoped_display_label("branch", 1, "a", redact_map, disclose=False)
        _mod.redaction._root_scoped_display_label("branch", 1, "disclosed-b", redact_map, disclose=True)
        second = _mod.redaction._root_scoped_display_label("branch", 1, "c", redact_map, disclose=False)
        assert first == "account-1/branch-1"
        assert second == "account-1/branch-2"

    def test_disclosed_and_redacted_labels_share_the_account_prefix_format(self):
        """Format-drift pin: a future change to either function's namespace
        string must fail here, substituting for extracting the format
        string into a third shared helper."""
        redact_map: dict[tuple[int, str], str] = {}
        disclosed = _mod.redaction._root_scoped_display_label("branch", 3, "feat-x", redact_map, disclose=True)
        redacted = _mod.redaction._assign_root_scoped_redact_label("branch", 3, "feat-y", {})
        assert disclosed.split("/", 1)[0] == redacted.split("/", 1)[0] == "account-3"

    def test_disclosed_label_strips_control_characters(self):
        """A gitBranch value is transcript-sourced, not git-validated -- an
        OSC-injection payload (the same fixture shape used for
        _format_cost_ledger_row's own control-character test) must not reach
        the disclosed label raw."""
        redact_map: dict[tuple[int, str], str] = {}
        label = _mod.redaction._root_scoped_display_label(
            "branch", 1, "\x1b]0;PWNED\x07\x1b[2J\x1b[H\x1b[31mFAKE-ROW\x1b[0m", redact_map, disclose=True
        )
        assert label == "account-1/]0;PWNED[2J[H[31mFAKE-ROW[0m"
        assert not re.search(r"[\x00-\x1f\x7f]", label)

    def test_disclosed_branch_value_containing_slash_stays_unambiguous(self):
        """Git branch names legitimately contain "/" (e.g. feature/foo) --
        the disclosed format itself uses "/" as the account-<K>/<value>
        separator, so this pins that a "/"-bearing value composes without
        truncation or reinterpretation."""
        redact_map: dict[tuple[int, str], str] = {}
        label = _mod.redaction._root_scoped_display_label("branch", 1, "feature/foo", redact_map, disclose=True)
        assert label == "account-1/feature/foo"
        assert label.split("/", 1) == ["account-1", "feature/foo"]


class TestRepoTrackedAgentTypeNames:
    """_repo_tracked_agent_type_names -- the --this-repo subagent_type
    disclosure allowlist accessor."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        """lru_cache(maxsize=1) is process-global; cleared before and after
        every test in this class so a monkeypatched _REPO_AGENT_DEFINITIONS_DIR
        from one test can never leak a stale cached result into the next,
        including the real-directory regression test below."""
        _mod._repo_tracked_agent_type_names.cache_clear()
        yield
        _mod._repo_tracked_agent_type_names.cache_clear()

    def _init_agents_repo(self, tmp_path: Path, *, tracked: list[str], untracked: list[str] = ()) -> Path:
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=agents_dir, check=True)
        for name in tracked:
            (agents_dir / f"{name}.md").write_text("---\nname: x\n---\n")
        if tracked:
            subprocess.run(["git", "add", "--", *(f"{n}.md" for n in tracked)], cwd=agents_dir, check=True)
        for name in untracked:
            (agents_dir / f"{name}.md").write_text("---\nname: x\n---\n")
        return agents_dir

    def _patch_dir(self, monkeypatch, agents_dir: Path) -> None:
        monkeypatch.setattr(_mod.redaction, "_REPO_AGENT_DEFINITIONS_DIR", agents_dir)

    def test_tracked_md_stem_is_allowlisted(self, tmp_path, monkeypatch):
        agents_dir = self._init_agents_repo(tmp_path, tracked=["my-agent"])
        self._patch_dir(monkeypatch, agents_dir)
        assert "my-agent" in _mod._repo_tracked_agent_type_names()

    def test_untracked_md_file_in_same_directory_is_not_allowlisted(self, tmp_path, monkeypatch):
        agents_dir = self._init_agents_repo(tmp_path, tracked=["tracked-agent"], untracked=["scratch-agent"])
        self._patch_dir(monkeypatch, agents_dir)
        names = _mod._repo_tracked_agent_type_names()
        assert "tracked-agent" in names
        assert "scratch-agent" not in names

    def test_differently_cased_query_against_tracked_stem_is_excluded(self, tmp_path, monkeypatch):
        agents_dir = self._init_agents_repo(tmp_path, tracked=["code-writer"])
        self._patch_dir(monkeypatch, agents_dir)
        names = _mod._repo_tracked_agent_type_names()
        assert "code-writer" in names
        assert "Code-Writer" not in names

    def test_directory_outside_any_git_repo_yields_built_ins_alone(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / "not-a-repo"
        agents_dir.mkdir()
        self._patch_dir(monkeypatch, agents_dir)
        assert _mod._repo_tracked_agent_type_names() == _mod._BUILT_IN_AGENT_TYPES

    @pytest.mark.parametrize("agents_dir_kind", ["tracked", "untracked", "not_a_repo"])
    def test_built_ins_present_in_every_case(self, tmp_path, monkeypatch, agents_dir_kind):
        if agents_dir_kind == "not_a_repo":
            agents_dir = tmp_path / "not-a-repo"
            agents_dir.mkdir()
        elif agents_dir_kind == "tracked":
            agents_dir = self._init_agents_repo(tmp_path, tracked=["tracked-agent"])
        else:
            agents_dir = self._init_agents_repo(tmp_path, tracked=[], untracked=["scratch-agent"])
        self._patch_dir(monkeypatch, agents_dir)
        assert _mod._repo_tracked_agent_type_names().issuperset(_mod._BUILT_IN_AGENT_TYPES)

    def test_git_binary_missing_falls_back_to_built_ins_only(self, tmp_path, monkeypatch):
        """Matches TestRepoScopedProjectSlugsGuard's own FileNotFoundError
        precedent for _repo_scoped_project_slugs, diverging in outcome: this
        accessor deliberately fails closed to the built-ins frozenset rather
        than sys.exit, since failing closed here means more redaction, and
        an operator's report should not die because git is unavailable."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        self._patch_dir(monkeypatch, agents_dir)

        def boom(cmd, *a, **k):
            raise FileNotFoundError("git")
        monkeypatch.setattr(subprocess, "run", boom)
        assert _mod._repo_tracked_agent_type_names() == _mod._BUILT_IN_AGENT_TYPES

    def test_git_call_timeout_falls_back_to_built_ins_only(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        self._patch_dir(monkeypatch, agents_dir)

        def boom(cmd, *a, **k):
            raise subprocess.TimeoutExpired(cmd, 10)
        monkeypatch.setattr(subprocess, "run", boom)
        assert _mod._repo_tracked_agent_type_names() == _mod._BUILT_IN_AGENT_TYPES

    def test_nested_tracked_agent_file_is_not_allowlisted(self, tmp_path, monkeypatch):
        """_repo_tracked_agent_type_names matches top-level entries only
        (no "/" in the path), matching _declared_pin's own flat
        agents_dir / f"{agent_type}.md" resolution -- a nested tracked file
        over-redacts, the safe direction."""
        agents_dir = self._init_agents_repo(tmp_path, tracked=["top-level-agent"])
        nested_dir = agents_dir / "nested"
        nested_dir.mkdir()
        (nested_dir / "nested-agent.md").write_text("---\nname: x\n---\n")
        subprocess.run(["git", "add", "--", "nested/nested-agent.md"], cwd=agents_dir, check=True)
        self._patch_dir(monkeypatch, agents_dir)
        names = _mod._repo_tracked_agent_type_names()
        assert "top-level-agent" in names
        assert "nested-agent" not in names

    def test_non_ascii_tracked_filename_is_allowlisted(self, tmp_path, monkeypatch):
        """Pins the docstring's -z rationale: without -z, git quotes and
        escapes unusual path names, corrupting the stem."""
        agents_dir = self._init_agents_repo(tmp_path, tracked=["café-agent"])
        self._patch_dir(monkeypatch, agents_dir)
        assert "café-agent" in _mod._repo_tracked_agent_type_names()

    def test_real_agents_directory_allowlists_code_writer(self):
        """The ticket's own motivating scenario, against the real,
        unmonkeypatched agents/ directory -- deriving the name dynamically
        (next(dir.glob('*.md')).stem) would make both this test's intent
        and its failure message unreadable, so the name is hardcoded; the
        unit tests above already carry the "real derivation works" fact."""
        assert "code-writer" in _mod._repo_tracked_agent_type_names()


# ---------------------------------------------------------------------------
# _sanitize_table_cell (render.py)
# ---------------------------------------------------------------------------


class TestSanitizeTableCell:
    """Direct unit coverage for _sanitize_table_cell — the defense-in-depth
    control-character strip every --deny-summary table cell passes through.
    No CLI-layer test can reach this with live malicious input today, since
    every caller already passes an allowlist-bounded label (_DENIAL_HOOK_LABELS,
    _DENIAL_COMMAND_SUBCOMMANDS, _FRICTION_KINDS) — this pins the helper's own
    behavior directly instead."""

    def test_strips_esc_byte(self):
        """Only the ESC control byte itself is stripped, not surrounding
        printable characters — e.g. an ANSI escape sequence's bracket/digit
        payload is left in place, just no longer interpretable as an escape."""
        assert _mod._sanitize_table_cell("git\x1b[31mcommit") == "git[31mcommit"

    def test_strips_null_and_del_bytes(self):
        assert _mod._sanitize_table_cell("a\x00b\x7fc") == "abc"

    def test_plain_label_unchanged(self):
        assert _mod._sanitize_table_cell("git commit") == "git commit"

    def test_empty_string_unchanged(self):
        assert _mod._sanitize_table_cell("") == ""


class TestScanRootTranscripts:
    """Direct unit tests for _scan_root_transcripts — neither its glob-count
    behavior nor its per-file skipped-counting was exercised at this layer
    before review; every prior assertion on 'skipped' asserted 0."""

    def test_unreadable_root_raises_permission_error(self, tmp_path):
        """os.access is an explicit probe, not reliance on Path.glob to raise
        — glob silently swallows OSError while walking an unreadable
        directory rather than propagating it (verified empirically during
        review), so this pins the probe itself, not glob's behavior."""
        root = tmp_path / "locked"
        root.mkdir()
        os.chmod(root, 0o000)
        try:
            with pytest.raises(PermissionError):
                _mod._scan_root_transcripts(root, "*")
        finally:
            os.chmod(root, 0o755)

    def test_readable_root_with_no_matches_returns_zero_zero(self, tmp_path):
        root = tmp_path / "empty"
        root.mkdir()
        assert _mod._scan_root_transcripts(root, "*") == (0, 0)

    def test_skipped_counts_unreadable_file_separately_from_scanned(self, tmp_path):
        proj = tmp_path / "-home-user-repo"
        proj.mkdir()
        readable = proj / "readable.jsonl"
        readable.write_text("{}\n")
        locked = proj / "locked.jsonl"
        locked.write_text("{}\n")
        os.chmod(locked, 0o000)
        try:
            scanned, skipped = _mod._scan_root_transcripts(tmp_path, "*")
        finally:
            os.chmod(locked, 0o644)
        assert (scanned, skipped) == (2, 1)

    def test_slugs_mode_dedupes_symlinked_alias_by_resolved_path(self, tmp_path):
        """The slugs branch (--this-repo's path) must dedupe the same way the
        glob branch does via _iter_scoped_sessions — a symlinked slug aliasing
        another slug's directory must not double the transcript count."""
        real_proj = tmp_path / "-home-user-repo"
        real_proj.mkdir()
        (real_proj / "sess.jsonl").write_text("{}\n")
        alias = tmp_path / "-home-user-repo-alias"
        alias.symlink_to(real_proj)
        scanned, _skipped = _mod._scan_root_transcripts(
            tmp_path, "*", slugs=["-home-user-repo", "-home-user-repo-alias"]
        )
        assert scanned == 1

    def test_glob_mode_dedupes_symlinked_alias_by_resolved_path(self, tmp_path):
        """The glob branch (--projects' default path) must dedupe the same
        way the slugs branch does — review found this branch's own dedup was
        missing, an asymmetry between the two branches of one function
        introduced when the slugs branch was added later for --this-repo."""
        real_proj = tmp_path / "-home-user-repo"
        real_proj.mkdir()
        (real_proj / "sess.jsonl").write_text("{}\n")
        alias = tmp_path / "-home-user-repo-alias"
        alias.symlink_to(real_proj)
        scanned, _skipped = _mod._scan_root_transcripts(tmp_path, "*")
        assert scanned == 1

    def test_glob_mode_rejects_a_parent_traversing_projects_value(self, tmp_path):
        """A '..' component in `projects_glob` is a real parent-directory step
        for Path.glob, not a no-op: this function must count nothing for it
        instead of counting a sibling account's own transcripts -- the same
        guarantee corpus.iter_sessions gives its own glob match."""
        root = tmp_path / "acct-a"
        root.mkdir()
        sibling = tmp_path / "acct-b"
        sibling.mkdir()
        (sibling / "secret.jsonl").write_text("{}\n")
        scanned, skipped = _mod._scan_root_transcripts(root, "../acct-b")
        assert (scanned, skipped) == (0, 0)

    def test_symlink_loop_project_dir_is_silently_skipped_not_raised(self, tmp_path):
        """A symlink-loop project dir reads as absent (its stat fails with ELOOP), so
        _scan_root_transcripts skips it and counts the healthy sibling
        without raising. Its two callers in cost.py only catch
        PermissionError."""
        proj_open = tmp_path / "-repo-open"
        proj_open.mkdir(parents=True)
        (proj_open / "sess.jsonl").write_text("{}\n")
        loop_link = tmp_path / "-repo-loop"
        loop_link.symlink_to(loop_link)
        scanned, skipped = _mod._scan_root_transcripts(tmp_path, "*")
        assert (scanned, skipped) == (1, 0)

    @pytest.mark.parametrize("mode", ["glob", "slugs"])
    def test_project_dir_symlinked_outside_the_root_is_counted(self, tmp_path, mode):
        """Only a '..' in the raw glob is rejected. With a single scan root,
        a project directory symlinked to a readable location outside `root`
        stays in scope, in both the glob and the exact-slug branch, matching
        iter_sessions. Multi-root consumers that attribute each session to a
        root do not tolerate it (see the pooled-layer tests in
        test_transcript_review_rounds.py)."""
        root = tmp_path / "acct-a"
        root.mkdir()
        outside = tmp_path / "elsewhere" / "-repo-relocated"
        outside.mkdir(parents=True)
        (outside / "sess.jsonl").write_text("{}\n")
        (root / "-repo-relocated").symlink_to(outside)
        slugs = ["-repo-relocated"] if mode == "slugs" else None
        assert _mod._scan_root_transcripts(root, "*", slugs=slugs) == (1, 0)

    def test_symlink_loop_transcript_is_counted_as_skipped_not_raised(self, tmp_path):
        """A looped .jsonl symlink fails open() with OSError, so it is
        counted under `skipped` alongside the healthy transcript instead of
        aborting the scan."""
        proj = tmp_path / "-repo-main"
        proj.mkdir()
        (proj / "sess.jsonl").write_text("{}\n")
        loop_link = proj / "loop.jsonl"
        loop_link.symlink_to(loop_link)
        assert _mod._scan_root_transcripts(tmp_path, "*") == (2, 1)

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_project_dir_through_sealed_ancestor_is_silently_skipped_not_raised(self, tmp_path):
        """A project dir reached through a sealed intermediate directory makes
        its stat raise PermissionError (an OSError subclass).
        _scan_root_transcripts skips it without raising and still counts the
        healthy sibling project dir."""
        root = tmp_path / "acct-a"
        proj_open = root / "-repo-open"
        proj_open.mkdir(parents=True)
        (proj_open / "sess.jsonl").write_text("{}\n")
        sealed_ancestor = tmp_path / "sealed-ancestor"
        target = sealed_ancestor / "target-dir"
        target.mkdir(parents=True)
        proj_through_sealed = root / "-repo-through-sealed"
        proj_through_sealed.symlink_to(target)
        os.chmod(sealed_ancestor, 0o000)
        try:
            scanned, skipped = _mod._scan_root_transcripts(root, "*")
        finally:
            os.chmod(sealed_ancestor, 0o755)
        assert (scanned, skipped) == (1, 0)


class TestPriceTurnArity:
    def test_price_turn_returns_exactly_three_values(self):
        """Pins _price_turn's (dollars_by_class, context_at_turn, unpriced_tokens)
        three-tuple return signature — three other call sites (audit-routing,
        context-distribution, cost-trend) consume it positionally; a future
        edit widening it for one caller would silently break the rest.
        audit-routing-shape and audit-routing-samples classify Opus turn
        shape only and never call _price_turn."""
        usage = _priced("claude-sonnet-5", input=100)["message"]["usage"]
        result = _mod._price_turn("claude-sonnet-5", usage)
        assert len(result) == 3
        dollars, context_at_turn, unpriced_tokens = result
        assert isinstance(dollars, dict)
        assert isinstance(context_at_turn, int)
        assert isinstance(unpriced_tokens, int)

    def test_context_at_turn_extraction_matches_price_turns_own_return_value(self):
        """_context_at_turn was extracted out of _price_turn as a pure
        refactor -- pins that the extracted computation still equals the sum
        _price_turn's own docstring specifies (input + cache_read +
        ephemeral_1h + ephemeral_5m) for a representative usage record."""
        usage = {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_input_tokens": 50,
            "cache_creation_input_tokens": 15,
            "cache_creation": {"ephemeral_1h_input_tokens": 10, "ephemeral_5m_input_tokens": 5},
        }
        _dollars, context_at_turn, _unpriced_tokens = _mod._price_turn("claude-sonnet-5", usage)
        assert context_at_turn == 100 + 50 + 10 + 5


class TestPriceTurnSpeedGeoMultipliers:
    """_price_turn's speed:"fast" (2x) and inference_geo:"us" (1.1x) dollar
    multipliers -- vendor-billed outcome fields, applied regardless of the
    model's own fast-mode/data-residency eligibility (see the regression-
    anchor test below)."""

    def test_speed_fast_multiplies_dollars_by_two(self):
        usage = _priced("claude-sonnet-5", input=1_000_000, speed="fast")["message"]["usage"]
        dollars, _context_at_turn, unpriced = _mod._price_turn("claude-sonnet-5", usage)
        assert unpriced == 0
        assert dollars["input"] == pytest.approx(1_000_000 / 1_000_000 * 2.0 * 2)

    def test_inference_geo_us_multiplies_dollars_by_1_1(self):
        usage = _priced("claude-sonnet-5", input=1_000_000, inference_geo="us")["message"]["usage"]
        dollars, _context_at_turn, unpriced = _mod._price_turn("claude-sonnet-5", usage)
        assert unpriced == 0
        assert dollars["input"] == pytest.approx(1_000_000 / 1_000_000 * 2.0 * 1.1)

    def test_speed_and_inference_geo_stack_multiplicatively(self):
        """Both multipliers present on the same turn compose to 2 * 1.1 = 2.2x,
        not just the larger of the two."""
        usage = _priced(
            "claude-sonnet-5", input=1_000_000, speed="fast", inference_geo="us",
        )["message"]["usage"]
        dollars, _context_at_turn, unpriced = _mod._price_turn("claude-sonnet-5", usage)
        assert unpriced == 0
        assert dollars["input"] == pytest.approx(1_000_000 / 1_000_000 * 2.0 * 2.2)

    def test_multiplier_applies_regardless_of_model_eligibility(self):
        """Regression anchor, not a new runtime check: _price_turn trusts the
        API's own reported speed/inference_geo outcome rather than
        hand-maintaining a per-model eligibility list, so a model that isn't
        actually fast-mode/data-residency-eligible still gets the multiplier
        if the usage record carries the field."""
        usage = _priced(
            "claude-sonnet-4-6", input=1_000_000, speed="fast", inference_geo="us",
        )["message"]["usage"]
        dollars, _context_at_turn, unpriced = _mod._price_turn("claude-sonnet-4-6", usage)
        assert unpriced == 0
        assert dollars["input"] == pytest.approx(1_000_000 / 1_000_000 * 3.0 * 2.2)


_PRICING_VARIANT_USAGE_CASES = [
    pytest.param({}, "standard", id="neither-field-present"),
    pytest.param({"speed": "fast"}, "fast", id="fast"),
    pytest.param({"inference_geo": "us"}, "us_geo", id="us-geo"),
    pytest.param({"speed": "fast", "inference_geo": "us"}, "fast_us_geo", id="fast-and-us-geo"),
    pytest.param({"speed": "standard"}, "standard", id="speed-standard"),
    pytest.param({"speed": None}, "standard", id="speed-none"),
    pytest.param({"speed": "FAST"}, "standard", id="speed-uppercase"),
    pytest.param({"inference_geo": "global"}, "standard", id="geo-global"),
    pytest.param({"inference_geo": "US"}, "standard", id="geo-uppercase"),
    pytest.param({"inference_geo": ""}, "standard", id="geo-empty"),
    pytest.param({"inference_geo": None}, "standard", id="geo-none"),
    pytest.param({"speed": "FAST", "inference_geo": "us"}, "us_geo", id="uppercase-speed-with-us-geo"),
    pytest.param({"speed": "fast", "inference_geo": "US"}, "fast", id="fast-with-uppercase-geo"),
]


class TestPricingVariant:
    """pricing._pricing_variant labels a turn by the multiplier conditions _price_turn applies, so
    pr-cost's per-model cells file each turn under the variant its dollars were priced with."""

    @pytest.mark.parametrize("extra_usage_fields,expected_label", _PRICING_VARIANT_USAGE_CASES)
    def test_label_for_each_speed_and_geo_combination_and_near_miss(self, extra_usage_fields, expected_label):
        usage = {**_priced("claude-sonnet-5", input=1_000)["message"]["usage"], **extra_usage_fields}
        assert _mod.pricing._pricing_variant(usage) == expected_label

    @pytest.mark.parametrize("extra_usage_fields,expected_label", _PRICING_VARIANT_USAGE_CASES)
    def test_price_turn_dollars_follow_the_label_implied_multiplier_under_the_listed_usage_perturbations(
        self, extra_usage_fields, expected_label,
    ):
        """For the listed perturbations, dollars equal standard dollars times the label's multiplier. A new
        multiplier keyed on one of those fields moves the dollars while the label stays unchanged, which
        fails this test. Dollars are compared with a relative tolerance, so a multiplication-order
        refactor does not."""
        label_multiplier = {
            "standard": 1,
            "fast": _mod.pricing._FAST_MODE_RATE_MULTIPLIER,
            "us_geo": _mod.pricing._INFERENCE_GEO_US_RATE_MULTIPLIER,
            "fast_us_geo": _mod.pricing._FAST_MODE_RATE_MULTIPLIER * _mod.pricing._INFERENCE_GEO_US_RATE_MULTIPLIER,
        }
        realistic_usage = {
            **_priced(
                "claude-sonnet-5", input=123_457, output=7_891, cache_read=3_333_333,
                ephemeral_1h=45_671, ephemeral_5m=98_765,
            )["message"]["usage"],
            "service_tier": "standard",
            "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
        }
        standard_dollars, _context, _unpriced = _mod._price_turn("claude-sonnet-5", realistic_usage)
        perturbations = [
            {},
            {"service_tier": "priority"},
            {"service_tier": "batch"},
            {"service_tier": None},
            {"server_tool_use": {"web_search_requests": 3, "web_fetch_requests": 2}},
            {"server_tool_use": None},
            {"not_a_known_usage_field": "some-future-value"},
            {"iterations": [{"type": "message", "input_tokens": 1, "output_tokens": 1}]},
        ]

        for perturbation in perturbations:
            usage = {**realistic_usage, **perturbation, **extra_usage_fields}
            assert _mod.pricing._pricing_variant(usage) == expected_label, perturbation
            actual_dollars, _context, _unpriced = _mod._price_turn("claude-sonnet-5", usage)
            for token_class in _mod.pricing._TOKEN_CLASSES:
                assert actual_dollars[token_class] == pytest.approx(
                    standard_dollars[token_class] * label_multiplier[expected_label], rel=1e-9,
                ), (perturbation, token_class)


class TestReportableUnpricedModelIds:
    """Direct unit tests for pricing._reportable_unpriced_model_ids, the
    shared predicate TestExcludedSpendBanner's _cost_report fixtures also
    exercise -- these pin its branches by direct call so a bug in the
    predicate itself is distinguishable from a print-wording bug in the
    banner that consumes it."""

    def test_empty_dict_reports_nothing(self):
        assert _mod.pricing._reportable_unpriced_model_ids({}) == []

    def test_priced_looking_key_with_nonzero_tokens_is_reportable(self):
        assert _mod.pricing._reportable_unpriced_model_ids({"claude-example-9": 500}) == ["claude-example-9"]

    def test_synthetic_at_zero_tokens_is_not_reportable(self):
        """A corpus with no synthetic records at all still seeds this key at
        0 in some callers -- a $0 entry is not spend excluded from anything."""
        assert _mod.pricing._reportable_unpriced_model_ids({_mod.pricing._SYNTHETIC_MODEL_ID: 0}) == []

    def test_synthetic_at_nonzero_tokens_is_reportable(self):
        """Forward-looking guard, not a case observed in a real corpus (see
        the production docstring) -- pins that a naive `!= _SYNTHETIC_MODEL_ID`
        filter, which would silently drop this branch, is rejected."""
        assert _mod.pricing._reportable_unpriced_model_ids({_mod.pricing._SYNTHETIC_MODEL_ID: 100}) == [
            _mod.pricing._SYNTHETIC_MODEL_ID
        ]

    def test_multiple_unpriced_ids_all_reported(self):
        result = _mod.pricing._reportable_unpriced_model_ids({"claude-example-9": 100, "claude-example-10": 200})
        assert sorted(result) == ["claude-example-10", "claude-example-9"]


class TestFormatDriftDetected:
    """Direct unit tests for pricing._format_drift_detected, the OR of the
    two module-level drift flags TestPricingIntegrityBanner's _cost_report
    fixtures also exercise through the full report."""

    def test_false_when_neither_flag_set(self, monkeypatch):
        monkeypatch.setattr(_mod.pricing, "_usage_drift_warned", False)
        monkeypatch.setattr(_mod.pricing, "_subagent_format_drift_detected", False)
        assert _mod.pricing._format_drift_detected() is False

    def test_true_when_only_usage_drift_flag_set(self, monkeypatch):
        monkeypatch.setattr(_mod.pricing, "_usage_drift_warned", True)
        monkeypatch.setattr(_mod.pricing, "_subagent_format_drift_detected", False)
        assert _mod.pricing._format_drift_detected() is True

    def test_true_when_only_subagent_drift_flag_set(self, monkeypatch):
        monkeypatch.setattr(_mod.pricing, "_usage_drift_warned", False)
        monkeypatch.setattr(_mod.pricing, "_subagent_format_drift_detected", True)
        assert _mod.pricing._format_drift_detected() is True


class TestDedupTurnsByRequestId:
    """Direct tests for _dedup_turns_by_request_id, the shared turn iterator
    cmd_audit_routing, _cost_report, _context_distribution_report,
    _cost_trend_report, and cmd_subagents all apply to their own per-session
    records before their per-record loops -- Claude Code writes one JSONL
    record per assistant content block, and every record from one API call
    shares a requestId. input_tokens and the cache_* classes are identical
    across a run's records; output_tokens ascends within the run and
    completes only on the last record."""

    def test_single_record_request_is_a_no_op(self):
        """A lone assistant record (no run to merge) is returned unchanged."""
        rec = _priced("claude-sonnet-5", input=100, request_id="req-1")
        assert _mod._dedup_turns_by_request_id([rec]) == [rec]

    def test_multi_record_run_merges_content_in_order_and_keeps_last_usage(self):
        """Three records sharing one requestId collapse into one turn whose
        content is the concatenation of all three blocks in original order,
        and whose usage is the run's LAST record's usage -- so a caller
        pricing the merged record prices the run's final (billed) usage,
        not an earlier record's."""
        block_a = {"type": "thinking", "thinking": "..."}
        block_b = {"type": "tool_use", "id": "t1", "name": "Read", "input": {}}
        block_c = {"type": "text", "text": "done"}
        recs = [
            _asst("claude-sonnet-5", content=[block_a], request_id="req-1"),
            _asst("claude-sonnet-5", content=[block_b], request_id="req-1"),
            _asst("claude-sonnet-5", content=[block_c], request_id="req-1"),
        ]
        recs[0]["message"]["usage"] = {"input_tokens": 100, "output_tokens": 3}
        recs[1]["message"]["usage"] = {"input_tokens": 100, "output_tokens": 3}
        recs[2]["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        result = _mod._dedup_turns_by_request_id(recs)
        assert len(result) == 1
        assert result[0]["message"]["content"] == [block_a, block_b, block_c]
        assert result[0]["message"]["usage"] == {"input_tokens": 100, "output_tokens": 50}

    def test_ascending_output_tokens_within_run_prices_using_last_record(self):
        """The run's output_tokens ascends record-to-record and completes on
        the last one (measured across 15,653 multi-record runs, 100% of
        which peak on the last record) -- a merged turn's usage must reflect
        that final, billed value. Taking the first record's stub value here
        would undercount output tokens, the regression this test guards."""
        recs = [
            _asst("claude-sonnet-5", content=[{"type": "thinking", "thinking": "..."}], request_id="req-1"),
            _asst("claude-sonnet-5", content=[{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
                  request_id="req-1"),
            _asst("claude-sonnet-5", content=[{"type": "text", "text": "done"}], request_id="req-1"),
        ]
        recs[0]["message"]["usage"] = {"input_tokens": 100, "output_tokens": 3}
        recs[1]["message"]["usage"] = {"input_tokens": 100, "output_tokens": 3}
        recs[2]["message"]["usage"] = {"input_tokens": 100, "output_tokens": 3111}
        result = _mod._dedup_turns_by_request_id(recs)
        assert result[0]["message"]["usage"]["output_tokens"] == 3111

    def test_non_identical_input_usage_within_run_emits_stderr_warning(self, monkeypatch, capsys):
        """A future transcript format emitting non-identical input_tokens
        within one requestId run is a silent-mispricing risk with no signal
        today -- this canary fires a stderr WARNING when
        _merge_assistant_run's input/cache-invariant-usage assumption is
        violated, mirroring _warn_if_subagent_format_drift's pattern.
        _usage_drift_warned is reset here since the canary is rate-limited to
        one warning per process (see _warn_if_run_usage_drift) and other
        tests in this module-scoped process may have already tripped it."""
        monkeypatch.setattr(_mod.pricing, "_usage_drift_warned", False)
        rec1 = _asst("claude-sonnet-5", content=[{"type": "thinking", "thinking": "..."}], request_id="req-1")
        rec1["message"]["usage"] = {"input_tokens": 100, "output_tokens": 3}
        rec2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "done"}], request_id="req-1")
        rec2["message"]["usage"] = {"input_tokens": 999, "output_tokens": 50}
        _mod._dedup_turns_by_request_id([rec1, rec2])
        assert "WARNING" in capsys.readouterr().err

    def test_ascending_output_tokens_within_run_emits_no_warning(self, monkeypatch, capsys):
        """The normal case -- output_tokens ascends within a run while
        input_tokens and the cache_* classes stay identical -- never fires
        the drift canary; an ascending output_tokens is the documented norm,
        not drift."""
        monkeypatch.setattr(_mod.pricing, "_usage_drift_warned", False)
        rec1 = _asst("claude-sonnet-5", content=[{"type": "thinking", "thinking": "..."}], request_id="req-1")
        rec1["message"]["usage"] = {"input_tokens": 100, "output_tokens": 3}
        rec2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "done"}], request_id="req-1")
        rec2["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        _mod._dedup_turns_by_request_id([rec1, rec2])
        assert "WARNING" not in capsys.readouterr().err

    def test_missing_request_id_records_each_count_separately(self):
        """Two assistant records with no requestId key at all never merge
        with each other — each is returned as its own one-record turn."""
        rec1 = _priced("claude-sonnet-5", input=100)
        rec2 = _priced("claude-sonnet-5", input=200)
        assert "requestId" not in rec1
        assert "requestId" not in rec2
        assert _mod._dedup_turns_by_request_id([rec1, rec2]) == [rec1, rec2]

    def test_null_and_empty_request_id_records_each_count_separately(self):
        """A null requestId and an empty-string requestId are both treated as
        'missing' — neither merges with the other or with a truly absent
        requestId, matching the never-merge-two-missing-ids requirement."""
        rec1 = _priced("claude-sonnet-5", input=100)
        rec1["requestId"] = None
        rec2 = _priced("claude-sonnet-5", input=200)
        rec2["requestId"] = ""
        assert _mod._dedup_turns_by_request_id([rec1, rec2]) == [rec1, rec2]

    def test_non_contiguous_run_with_matching_usage_merges_across_user_record(self):
        """A same-requestId assistant run interleaved with a user tool_result
        record -- the harness's own shape for a multi-tool_use response
        dispatched one tool call at a time -- merges into one turn when
        every record's usage matches, with the intervening user record
        passing through unchanged at its own position. Treating the
        intervening user record as ending the run and never revisiting the
        second assistant block would inflate turn counts and dollar totals
        for this shape, the regression this test guards. This is the
        canonical pin for dedup_turns_by_request_id's non-contiguous merge
        behavior -- extend it rather than adding a second pin elsewhere."""
        rec1 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}], request_id="req-1")
        rec1["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        user = _user_msg([_tool_result("t1", "tool result")])
        rec2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}], request_id="req-1")
        rec2["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        result = _mod._dedup_turns_by_request_id([rec1, user, rec2])
        assert len(result) == 2
        assert result[0]["message"]["content"] == [
            {"type": "text", "text": "a"}, {"type": "text", "text": "b"},
        ]
        assert result[0]["message"]["usage"] == {"input_tokens": 100, "output_tokens": 50}
        assert result[1] == user

    def test_non_contiguous_run_with_full_cache_usage_matching_merges(self):
        """The merge-approval path exercised above uses a 2-key usage dict
        (input_tokens/output_tokens only), where the two omitted keys
        (cache_creation_input_tokens/cache_read_input_tokens) trivially
        agree via None == None regardless of whether the comparison logic
        actually checks them. This test populates all four
        _NON_CONTIGUOUS_MERGE_REQUIRED_MATCH_KEYS with real matching values
        -- the shape every real transcript record carries per this
        function's own docstring -- so a regression that broke comparison
        specifically for populated cache values on the merge-approval path
        would fail here even though the 2-key tests above would not catch
        it."""
        usage = {
            "input_tokens": 100, "cache_creation_input_tokens": 20,
            "cache_read_input_tokens": 10, "output_tokens": 50,
        }
        rec1 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}], request_id="req-1")
        rec1["message"]["usage"] = dict(usage)
        user = _user_msg([_tool_result("t1", "tool result")])
        rec2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}], request_id="req-1")
        rec2["message"]["usage"] = dict(usage)
        result = _mod._dedup_turns_by_request_id([rec1, user, rec2])
        assert len(result) == 2
        assert result[0]["message"]["content"] == [
            {"type": "text", "text": "a"}, {"type": "text", "text": "b"},
        ]

    def test_non_contiguous_run_with_mismatched_output_tokens_does_not_merge(self):
        """Two assistant records share a requestId but diverge on
        output_tokens -- the signature of two genuinely separate API calls
        that happen to collide on requestId (e.g. a hook-denial retry),
        rather than one once-billed non-contiguous run. The corroboration
        bar rejects the merge and all three records stay separate turns in
        original order."""
        rec1 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}], request_id="req-1")
        rec1["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        user = _user_msg([_tool_result("t1", "continue")])
        rec2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}], request_id="req-1")
        rec2["message"]["usage"] = {"input_tokens": 100, "output_tokens": 999}
        result = _mod._dedup_turns_by_request_id([rec1, user, rec2])
        assert result == [rec1, user, rec2]

    def test_three_record_non_contiguous_run_merges(self):
        """The merge isn't hardcoded to pairs: a 3-record non-contiguous run
        with matching usage on every record merges into one turn, with both
        intervening user records passing through unchanged."""
        rec1 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}], request_id="req-1")
        rec1["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        user1 = _user_msg([_tool_result("t1", "tool result 1")])
        rec2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}], request_id="req-1")
        rec2["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        user2 = _user_msg([_tool_result("t2", "tool result 2")])
        rec3 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "c"}], request_id="req-1")
        rec3["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        result = _mod._dedup_turns_by_request_id([rec1, user1, rec2, user2, rec3])
        assert len(result) == 3
        assert result[0]["message"]["content"] == [
            {"type": "text", "text": "a"}, {"type": "text", "text": "b"}, {"type": "text", "text": "c"},
        ]
        assert result[1] == user1
        assert result[2] == user2

    def test_three_record_non_contiguous_run_with_one_divergent_member_does_not_merge(self):
        """Pins that _non_contiguous_run_usage_matches compares every member
        against the run's first record, not just adjacent pairs or a
        majority: a 3-record non-contiguous run where two members match and
        the third diverges on one invariant key (cache_read_input_tokens)
        fails the corroboration bar, so all three records stay separate
        turns regardless of the divergent member's position in the group."""
        rec1 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}], request_id="req-1")
        rec1["message"]["usage"] = {
            "input_tokens": 100, "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 10, "output_tokens": 50,
        }
        user1 = _user_msg([_tool_result("t1", "tool result 1")])
        rec2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}], request_id="req-1")
        rec2["message"]["usage"] = {
            "input_tokens": 100, "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 10, "output_tokens": 50,
        }
        user2 = _user_msg([_tool_result("t2", "tool result 2")])
        rec3 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "c"}], request_id="req-1")
        rec3["message"]["usage"] = {
            "input_tokens": 100, "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 999, "output_tokens": 50,
        }
        result = _mod._dedup_turns_by_request_id([rec1, user1, rec2, user2, rec3])
        assert result == [rec1, user1, rec2, user2, rec3]

    def test_interleaved_different_request_id_group_leaves_other_run_record_untouched(self):
        """Records ordered [A req-1, B req-2, C req-1], where req-1's usage
        matches across A and C: the two-pass indexer groups by requestId
        across the full input, so B -- a different run's own record sitting
        between A and C -- passes through unchanged at its own position
        while A and C merge at A's original position, pinning that indexing
        by requestId across the full input doesn't disturb an unrelated
        run's record that happens to sit between this run's members."""
        rec_a = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}], request_id="req-1")
        rec_a["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        rec_b = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}], request_id="req-2")
        rec_b["message"]["usage"] = {"input_tokens": 200, "output_tokens": 20}
        rec_c = _asst("claude-sonnet-5", content=[{"type": "text", "text": "c"}], request_id="req-1")
        rec_c["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        result = _mod._dedup_turns_by_request_id([rec_a, rec_b, rec_c])
        assert len(result) == 2
        assert result[0]["requestId"] == "req-1"
        assert result[0]["message"]["content"] == [
            {"type": "text", "text": "a"}, {"type": "text", "text": "c"},
        ]
        assert result[1] == rec_b

    def test_two_multi_member_non_contiguous_groups_mutually_interleaved_both_merge(self):
        """Records ordered [A1 req-1, B1 req-2, A2 req-1, B2 req-2] -- two
        *each*-multi-member non-contiguous groups mutually interleaved,
        rather than one multi-member group interleaved with a single-record
        different-id record (the prior test's shape). Both groups merge
        independently at their own first member's position, pinning that
        merge_start_idx/skip_idx reconstruction handles two simultaneously
        in-progress groups rather than only one."""
        rec_a1 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a1"}], request_id="req-1")
        rec_a1["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        rec_b1 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b1"}], request_id="req-2")
        rec_b1["message"]["usage"] = {"input_tokens": 200, "output_tokens": 20}
        rec_a2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a2"}], request_id="req-1")
        rec_a2["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        rec_b2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b2"}], request_id="req-2")
        rec_b2["message"]["usage"] = {"input_tokens": 200, "output_tokens": 20}
        result = _mod._dedup_turns_by_request_id([rec_a1, rec_b1, rec_a2, rec_b2])
        assert len(result) == 2
        assert result[0]["requestId"] == "req-1"
        assert result[0]["message"]["content"] == [
            {"type": "text", "text": "a1"}, {"type": "text", "text": "a2"},
        ]
        assert result[1]["requestId"] == "req-2"
        assert result[1]["message"]["content"] == [
            {"type": "text", "text": "b1"}, {"type": "text", "text": "b2"},
        ]

    def test_non_contiguous_merge_and_rejection_notices_are_independently_rate_limited(
        self, monkeypatch, capsys
    ):
        """Both non-contiguous merge and rejection decisions log one stderr
        NOTICE per process, independently rate-limited by decision kind
        (_non_contiguous_merge_notices_logged) -- a merge NOTICE firing must
        not suppress a later rejection NOTICE, and each message names its
        own decision kind so the two are distinguishable by grep, not just a
        generic 'NOTICE' substring. Also asserts the interpolated requestId
        and record_count values, not just the decision-kind keyword -- the
        NOTICE's stated purpose is auditing which run triggered which
        decision, so a transposition bug in the f-string (wrong requestId,
        wrong count) must fail this test even though the keyword-only
        assertions would still pass.
        _non_contiguous_merge_notices_logged is reset here since it's
        rate-limited across the whole process and other tests in this
        module-scoped run may have already tripped either kind (mirrors
        test_non_identical_input_usage_within_run_emits_stderr_warning's
        reset of _usage_drift_warned)."""
        monkeypatch.setattr(_mod.pricing, "_non_contiguous_merge_notices_logged", set())

        merge_a = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}], request_id="req-1")
        merge_a["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        merge_b = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}], request_id="req-1")
        merge_b["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        _mod._dedup_turns_by_request_id([merge_a, _user_msg([_tool_result("t1", "tool result")]), merge_b])
        merge_stderr = capsys.readouterr().err
        assert "NOTICE" in merge_stderr
        assert "merged" in merge_stderr
        assert "rejected" not in merge_stderr
        assert "req-1" in merge_stderr
        assert "2" in merge_stderr

        reject_a = _asst("claude-sonnet-5", content=[{"type": "text", "text": "x"}], request_id="req-2")
        reject_a["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        reject_b = _asst("claude-sonnet-5", content=[{"type": "text", "text": "y"}], request_id="req-2")
        reject_b["message"]["usage"] = {"input_tokens": 100, "output_tokens": 999}
        _mod._dedup_turns_by_request_id([reject_a, _user_msg([_tool_result("t2", "tool result")]), reject_b])
        reject_stderr = capsys.readouterr().err
        assert "NOTICE" in reject_stderr
        assert "rejected" in reject_stderr
        assert "req-2" in reject_stderr
        assert "2" in reject_stderr

    def test_non_contiguous_merge_notice_suppressed_on_repeat_same_kind(self, monkeypatch, capsys):
        """A second non-contiguous merge (different requestId, same 'merged'
        decision kind) after the first must not print a second NOTICE --
        pinning the rate-limit half of 'independently rate-limited', not
        just the cross-kind independence the prior test covers."""
        monkeypatch.setattr(_mod.pricing, "_non_contiguous_merge_notices_logged", set())

        first_a = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}], request_id="req-1")
        first_a["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        first_b = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}], request_id="req-1")
        first_b["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        _mod._dedup_turns_by_request_id([first_a, _user_msg([_tool_result("t1", "tool result")]), first_b])
        capsys.readouterr()  # drain the first NOTICE

        second_a = _asst("claude-sonnet-5", content=[{"type": "text", "text": "x"}], request_id="req-2")
        second_a["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        second_b = _asst("claude-sonnet-5", content=[{"type": "text", "text": "y"}], request_id="req-2")
        second_b["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        _mod._dedup_turns_by_request_id([second_a, _user_msg([_tool_result("t2", "tool result")]), second_b])
        assert capsys.readouterr().err == ""

    def test_non_contiguous_run_with_missing_usage_does_not_merge(self):
        """Two same-requestId assistant records with no usage dict at all
        have zero evidence to corroborate on -- treating that absence as
        agreement (None == None on every key) would merge two records with
        no actual usage match, the failure mode this test guards against."""
        rec1 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}], request_id="req-1")
        del rec1["message"]["usage"]
        user = _user_msg([_tool_result("t1", "tool result")])
        rec2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}], request_id="req-1")
        del rec2["message"]["usage"]
        result = _mod._dedup_turns_by_request_id([rec1, user, rec2])
        assert result == [rec1, user, rec2]

    def test_request_id_does_not_merge_across_session_files(self, fake_projects, capsys):
        """The same requestId string appearing in two separate session files
        prices as two separate turns, not one merged turn — the dedup helper
        is applied fresh to each session's own records list, so a requestId
        collision across sessions (however unlikely with real UUIDs) can't
        collapse them."""
        _write_jsonl(fake_projects / "sess-a.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, request_id="shared-req"),  # $2.00
        ])
        _write_jsonl(fake_projects / "sess-b.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, request_id="shared-req"),  # $2.00
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert _extract_grand_total(out) == pytest.approx(4.00)

    def test_non_contiguous_run_prices_once_through_cost_report(self, fake_projects, capsys):
        """The motivating production bug: a session file where the harness
        interleaves a tool_result between two same-requestId assistant
        records prices as one turn through an actual downstream call site
        (_cost_report), not two -- pinning the fix's real symptom end-to-end
        rather than only the shared function's branch logic in isolation.
        Before this fix, the interleaved second record priced as its own
        turn, double-counting both the turn count and the dollar total."""
        first = _priced("claude-sonnet-5", input=1_000_000, request_id="req-1")  # $2.00
        second = _priced("claude-sonnet-5", input=1_000_000, request_id="req-1")  # $2.00, same usage as first
        _write_jsonl(fake_projects / "sess.jsonl", [
            first, _user_msg([_tool_result("t1", "tool result")]), second,
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert _extract_grand_total(out) == pytest.approx(2.00)

    def test_shared_request_id_merges_across_main_and_sidechain_records(self):
        """A requestId shared by a main-thread record and a sidechain record
        merges into one turn — the merge condition checks requestId equality
        alone, not isSidechain, relying on requestId's global uniqueness (see
        this function's own docstring). include_subagents=True concatenates a
        session's main file with its subagent files into one records list
        before dedup runs, so this boundary is reachable in that concatenated
        list even though it never occurs within a single raw JSONL file."""
        main_rec = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}],
                          request_id="req-1", sidechain=False)
        side_rec = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}],
                          request_id="req-1", sidechain=True)
        result = _mod._dedup_turns_by_request_id([main_rec, side_rec])
        assert len(result) == 1
        assert result[0]["message"]["content"] == [
            {"type": "text", "text": "a"}, {"type": "text", "text": "b"},
        ]


def _read_tool_use(tool_id: str, *, file_path: str, offset: int | None = None, limit: int | None = None) -> dict:
    tool_input: dict = {"file_path": file_path}
    if offset is not None:
        tool_input["offset"] = offset
    if limit is not None:
        tool_input["limit"] = limit
    return {"type": "tool_use", "id": tool_id, "name": "Read", "input": tool_input}


class TestCorpusSubagentDirAndUtf8ErrorHandling:
    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_read_session_file_partitioned_treats_unreadable_subagents_dir_as_absent(self, fake_projects):
        """A subagents/ directory reached through a sealed session-id
        ancestor makes `subagent_dir.is_dir()` raise `PermissionError`.
        (`PermissionError` is an `OSError` subclass.)
        `_read_session_file_partitioned` must treat that the same as an
        absent directory, not crash. The only observable difference from a
        readable-empty directory is that no subagent records are merged."""
        _write_jsonl(fake_projects / "sess.jsonl", [_opus([_read_tool_use("r1", file_path="/a.py")])])
        _write_subagent_jsonl(fake_projects, "sess", "agent-a", [_opus([_read_tool_use("r2", file_path="/b.py")])])
        session_dir = fake_projects / "sess"
        os.chmod(session_dir, 0o000)
        try:
            result = _mod._read_session_file_partitioned(fake_projects / "sess.jsonl", include_subagents=True)
        finally:
            os.chmod(session_dir, 0o755)

        expected = [[_opus([_read_tool_use("r1", file_path="/a.py")])]]
        assert result == expected

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_index_subagent_dispatches_returns_empty_index_for_unreadable_subagents_dir(self, fake_projects):
        """A subagents/ directory reached through a sealed session-id
        ancestor makes subagent_dir.is_dir() raise PermissionError (an
        OSError) -- corpus._index_subagent_dispatches must return an empty
        index and no read errors, not crash, so every dispatch in that
        session reads as dangling. Mirrors
        test_read_session_file_partitioned_treats_unreadable_subagents_dir_as_absent's
        chmod-based direct-call pattern for the sibling function."""
        _write_jsonl(fake_projects / "sess.jsonl", [_opus([_read_tool_use("r1", file_path="/a.py")])])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a", "toolu_1",
            [_opus([_read_tool_use("r2", file_path="/b.py")])],
        )
        session_dir = fake_projects / "sess"
        os.chmod(session_dir, 0o000)
        try:
            index, meta_read_errors = _mod.corpus._index_subagent_dispatches(fake_projects / "sess.jsonl")
        finally:
            os.chmod(session_dir, 0o755)

        assert index == {}
        assert meta_read_errors == 0

    def test_index_subagent_dispatches_counts_non_utf8_meta_as_read_error(self, fake_projects):
        """A meta.json containing non-UTF-8 bytes must be counted under
        meta_read_errors, identical to an invalid-JSON or unreadable
        meta.json -- corpus._index_subagent_dispatches must not raise
        UnicodeDecodeError uncaught and abort the whole run. Uses the same
        non-UTF-8 bytes as
        test_parse_jsonl_records_returns_empty_list_for_a_file_of_only_non_utf8_bytes."""
        _write_jsonl(fake_projects / "sess.jsonl", [_opus([_read_tool_use("r1", file_path="/a.py")])])
        subdir = fake_projects / "sess" / _mod.SUBAGENT_SUBDIR
        subdir.mkdir(parents=True, exist_ok=True)
        (subdir / "agent-1.meta.json").write_bytes(b"\xff\xfe\x00\x01")

        index, meta_read_errors = _mod.corpus._index_subagent_dispatches(fake_projects / "sess.jsonl")

        assert index == {}
        assert meta_read_errors == 1

    def test_parse_jsonl_records_skips_a_non_utf8_line_and_keeps_the_valid_ones(self, tmp_path):
        """A line with non-UTF-8 bytes is skipped on its own like a
        malformed-JSON line, so the valid records before and after it
        survive instead of the whole file being discarded."""
        mixed_jsonl = tmp_path / "mixed.jsonl"
        mixed_jsonl.write_bytes(
            b'{"type": "user", "n": 1}\n\xff\xfe\x00\x01\n{"type": "user", "n": 2}\n'
        )
        assert _mod.corpus._parse_jsonl_records(mixed_jsonl) == [
            {"type": "user", "n": 1}, {"type": "user", "n": 2},
        ]

    def test_parse_jsonl_records_returns_empty_list_for_a_file_of_only_non_utf8_bytes(self, tmp_path):
        """A readable file with no decodable record is empty ([]), not
        unreadable (None), so an all-corrupt transcript is not mistaken for
        a failed read."""
        bad_jsonl = tmp_path / "bad.jsonl"
        bad_jsonl.write_bytes(b"\xff\xfe\x00\x01")
        assert _mod.corpus._parse_jsonl_records(bad_jsonl) == []

    def test_parse_jsonl_records_returns_none_when_the_file_cannot_be_opened(self, tmp_path):
        assert _mod.corpus._parse_jsonl_records(tmp_path / "missing.jsonl") is None

    def test_parse_jsonl_records_keeps_valid_multibyte_utf8_lines_on_both_sides_of_a_corrupt_line(self, tmp_path):
        """A transcript written as raw UTF-8 (not ASCII-escaped, unlike
        _write_jsonl's output) round-trips every record intact. The
        assertion compares whole records, so a codec that cannot decode
        the multi-byte characters, or decodes them wrongly, fails it."""
        multibyte_text = "café 🎉 — 北京"
        multibyte_jsonl = tmp_path / "multibyte.jsonl"
        multibyte_jsonl.write_bytes(
            json.dumps({"type": "user", "n": 1, "text": multibyte_text}, ensure_ascii=False).encode("utf-8")
            + b"\n\xff\xfe\x00\x01\n"
            + json.dumps({"type": "user", "n": 2, "text": multibyte_text}, ensure_ascii=False).encode("utf-8")
            + b"\n"
        )
        assert _mod.corpus._parse_jsonl_records(multibyte_jsonl) == [
            {"type": "user", "n": 1, "text": multibyte_text},
            {"type": "user", "n": 2, "text": multibyte_text},
        ]

    def test_parse_jsonl_records_drops_a_record_with_a_truncated_multibyte_character_and_keeps_its_neighbours(
        self, tmp_path
    ):
        """An invalid byte inside an otherwise valid record's JSON string
        drops that whole record rather than keeping it with a replacement
        character, so a lossy decode (errors="replace") fails this test."""
        truncated_multibyte_jsonl = tmp_path / "truncated.jsonl"
        truncated_multibyte_jsonl.write_bytes(
            b'{"type": "user", "n": 1}\n'
            + b'{"type": "user", "n": 2, "text": "caf\xc3"}\n'
            + b'{"type": "user", "n": 3}\n'
        )
        assert _mod.corpus._parse_jsonl_records(truncated_multibyte_jsonl) == [
            {"type": "user", "n": 1}, {"type": "user", "n": 3},
        ]

    def test_index_subagent_dispatches_reads_a_multibyte_utf8_meta_json_without_a_read_error(self, fake_projects):
        """A meta.json written as raw UTF-8 with non-ASCII text next to a
        valid toolUseId is indexed, not counted under meta_read_errors."""
        _write_jsonl(fake_projects / "sess.jsonl", [_opus([_read_tool_use("r1", file_path="/a.py")])])
        subdir = fake_projects / "sess" / _mod.SUBAGENT_SUBDIR
        subdir.mkdir(parents=True, exist_ok=True)
        meta = {"agentType": "reviewer", "description": "café 🎉 — 北京", "toolUseId": "toolu_1", "spawnDepth": 1}
        (subdir / "agent-1.meta.json").write_bytes(json.dumps(meta, ensure_ascii=False).encode("utf-8"))

        index, meta_read_errors = _mod.corpus._index_subagent_dispatches(fake_projects / "sess.jsonl")

        assert index == {"toolu_1": (subdir / "agent-1.jsonl", None)}
        assert meta_read_errors == 0


# ---------------------------------------------------------------------------
# cache-efficiency
# ---------------------------------------------------------------------------


class TestCacheEfficiencyArgparseWiring:
    """Round-trips the real argparser, mirroring
    TestPlanBoundaryArgparseWiring -- every other new test in this section
    builds args via the hand-rolled _cache_efficiency_args() factory, which
    cannot catch a dest= typo or missing set_defaults in the real parser."""

    def test_registers_cache_efficiency_subcommand_with_expected_defaults(self):
        parser = _mod.build_parser()
        args = parser.parse_args(["cache-efficiency"])
        assert args.extra_config_dirs is None
        assert args.no_redact is False
        assert args.func == _mod.cmd_cache_efficiency

    def test_config_dir_and_no_redact_wire_to_expected_attributes(self):
        parser = _mod.build_parser()
        args = parser.parse_args(["cache-efficiency", "--config-dir", "X", "--no-redact"])
        assert args.func is _mod.cmd_cache_efficiency
        assert args.extra_config_dirs == ["X"]
        assert args.no_redact is True


class TestCacheRebuildCrossInstrumentReconciliation:
    """Verification item 10: cache-rebuild's new sidechain W5m accumulator
    must agree exactly with cache-efficiency's own sidechain Write5m column
    on an identical fixture -- both dedup by requestId per group and
    classify origin off the identical isSidechain expression, so a
    disagreement means the new accumulator has a bug, not that the corpus
    is odd."""

    def test_sidechain_w5m_matches_cache_efficiency_sidechain_write5m_on_identical_fixture(
        self, fake_projects, capsys
    ):
        session_id = "sess-reconcile"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _priced("claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z", request_id="main-1"),
        ])
        subagent_records = [
            _priced("claude-sonnet-5", ephemeral_5m=75_000, ts="2026-08-01T10:00:00.000Z", request_id="sub-1"),
            _priced(
                "claude-sonnet-5", ephemeral_5m=125_000,
                ts="2026-08-01T10:06:00.000Z", request_id="sub-2",
            ),
        ]
        for rec in subagent_records:
            rec["isSidechain"] = True
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", subagent_records)

        _mod.cache_rebuild._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        rebuild_out = capsys.readouterr().out
        rebuild_row = _table_cols(rebuild_out, header_contains="Ratio", row_contains="subagent")

        _mod._cache_efficiency_report(_cache_efficiency_args(), roots=[fake_projects.parent])
        efficiency_out = capsys.readouterr().out
        efficiency_row = _table_cols(efficiency_out, header_contains="Write5m", row_contains="sidechain")

        assert rebuild_row["W5m"] == efficiency_row["Write5m"]


# ---------------------------------------------------------------------------
# _repo_scoped_project_slugs guard, build_parser, and iter_sessions ordering
# ---------------------------------------------------------------------------


class TestRepoScopedProjectSlugsGuard:
    """_repo_scoped_project_slugs' three fail-closed sys.exit sites and the
    row-20 containment-plus-identity cwd guard, exercised via a non-
    skill-invocation caller label to cover the newly-generic path."""

    def _worktree_porcelain(self, *paths):
        return "\n".join(f"worktree {p}\nHEAD 0000\nbranch refs/heads/x\n" for p in paths)

    def test_fails_closed_when_worktree_list_command_fails(self, monkeypatch, capsys):
        def boom(cmd, *a, **k):
            raise FileNotFoundError("git")
        monkeypatch.setattr(subprocess, "run", boom)
        with pytest.raises(SystemExit):
            _mod._repo_scoped_project_slugs("buckets")
        assert "buckets:" in capsys.readouterr().err

    def test_fails_closed_when_worktree_list_returns_no_worktrees(self, monkeypatch, capsys):
        def fake_run(cmd, *a, **k):
            return subprocess.CompletedProcess(cmd, 0, "", "")
        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit):
            _mod._repo_scoped_project_slugs("buckets")
        assert "buckets:" in capsys.readouterr().err

    def test_fails_closed_when_cwd_outside_all_worktrees(self, monkeypatch, capsys):
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/somewhere/else")

        def fake_run(cmd, *a, **k):
            return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/repo"), "")
        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit):
            _mod._repo_scoped_project_slugs("duration")
        assert "duration:" in capsys.readouterr().err

    def test_cwd_inside_worktree_subdirectory_resolves_successfully(self, monkeypatch):
        """Containment (not slug equality) lets --this-repo run from a subdirectory
        of the worktree root."""
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/nested/dir")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/repo"), "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        slugs = _mod._repo_scoped_project_slugs("duration")
        assert slugs == [_mod.scope._path_to_project_slug("/repo")]

    def test_sibling_repo_sharing_string_prefix_is_rejected(self, monkeypatch, capsys):
        """cwd inside a sibling path sharing the same string prefix (<repo>-fork)
        must exit 1 — the case bare str.startswith containment would wrongly
        accept."""
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo-fork/nested")

        def fake_run(cmd, *a, **k):
            return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/repo"), "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit):
            _mod._repo_scoped_project_slugs("duration")
        assert capsys.readouterr().out == ""

    def test_fails_closed_when_cwd_repo_root_diverges_from_worktree_list(self, monkeypatch, capsys):
        """Identity guard: cwd sits under a listed worktree (containment passes)
        but `git rev-parse --show-toplevel` reports a different root — the case
        an environment override like GIT_WORK_TREE could produce by decoupling
        toplevel reporting from the worktree-list enumeration. Every other test
        in this class returns the same path from both git calls, so this is the
        only test that would catch a typo bug in the identity check itself."""
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/nested")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/repo"), "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/outer\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit):
            _mod._repo_scoped_project_slugs("duration")
        assert "repo root is not among the resolved worktrees" in capsys.readouterr().err

    def test_fails_closed_when_rev_parse_toplevel_command_fails(self, monkeypatch, capsys):
        """The identity-check subprocess call erroring must fail closed, not
        fall back to a machine-wide scope, even though worktree list succeeded."""
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/nested")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/repo"), "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            raise FileNotFoundError("git")
        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit):
            _mod._repo_scoped_project_slugs("duration")
        assert "duration:" in capsys.readouterr().err


class TestResolveProjectScope:
    """The scope-dispatch helper behind --projects/--this-repo on the 16
    non-skill-invocation subcommands."""

    def _worktree_porcelain(self, *paths):
        return "\n".join(f"worktree {p}\nHEAD 0000\nbranch refs/heads/x\n" for p in paths)

    def test_this_repo_unset_raises_rather_than_defaulting_to_machine_wide(self, fake_projects):
        """A subparser wired without _add_project_scope_args (missing this_repo
        entirely) must raise, not silently fall through to '*' scope."""
        args = type("A", (), {"projects": "*"})()  # no this_repo attribute at all
        with pytest.raises(AttributeError):
            _mod._resolve_project_scope(args, "buckets")

    def test_called_twice_from_one_args_yields_same_session_list_both_times(self, tmp_path, monkeypatch):
        """cmd_audit_routing's redact pass and main pass both call the scope helper
        on the same args object — a shared/exhausted generator would make the
        second pass silently empty."""
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "s.jsonl", [_asst("claude-sonnet-4-6", branch="main")])
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/repo/main"), "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        args = argparse.Namespace(this_repo=True, projects="*")
        iter1, label1 = _mod._resolve_project_scope(args, "audit-routing")
        iter2, label2 = _mod._resolve_project_scope(args, "audit-routing")
        sessions1 = list(iter1)
        sessions2 = list(iter2)
        assert len(sessions1) == 1
        assert len(sessions2) == 1
        assert label1 == label2

    def test_resolved_scope_header_renders_under_this_repo_and_under_a_glob(self, tmp_path, monkeypatch, capsys):
        """The resolved-scope header prints for a non-skill-invocation subcommand
        both under --this-repo and under the default machine-wide glob, so no
        output is scope-ambiguous."""
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "s.jsonl", [_asst("claude-sonnet-4-6", branch="feat")])
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/repo/main"), "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod.cmd_buckets(type("A", (), {"projects": "*", "this_repo": True, "branches": None})())
        out_repo = capsys.readouterr().out
        assert (
            "BUCKETS SOURCES (this repo (1 project dirs); "
            "1 root (no ~/.claude/transcript-config-dirs declared))"
        ) in out_repo

        _mod.cmd_buckets(type("A", (), {"projects": "*", "this_repo": False, "branches": None})())
        out_glob = capsys.readouterr().out
        assert "BUCKETS SOURCES (*; 1 root (no ~/.claude/transcript-config-dirs declared))" in out_glob


class TestBuildParser:
    """build_parser() as a testable seam — the argparse layer without executing
    a subcommand against the real ~/.claude/projects."""

    def test_this_repo_and_projects_mutually_exclusive_on_generic_subcommand(self):
        parser = _mod.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["buckets", "--this-repo", "--projects", "x"])

    def test_this_repo_and_projects_mutually_exclusive_on_skill_invocation(self):
        parser = _mod.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["skill-invocation", "--this-repo", "--projects", "x"])

    def test_bare_skill_invocation_leaves_projects_unset(self):
        """None, not "*", is what makes skill-invocation default to this repo's worktrees."""
        parsed = _mod.build_parser().parse_args(["skill-invocation"])
        assert parsed.projects is None

    @staticmethod
    def _subcommand_flags(parser):
        """Subcommand name -> its registered option strings, from the real registrar."""
        subparsers = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        return {name: set(sub._option_string_actions) for name, sub in subparsers.choices.items()}

    def test_subcommands_with_projects_but_no_this_repo_are_the_known_exemptions(self):
        """A subcommand silently losing --this-repo would drop out of the exclusivity walk
        below, so the set that has --projects without it is pinned explicitly."""
        flags = self._subcommand_flags(_mod.build_parser())
        assert {name for name, opts in flags.items() if "--projects" in opts and "--this-repo" not in opts} == {"user-input"}

    def test_every_subcommand_with_both_scope_flags_refuses_them_together(self, capsys):
        """Walks the real registrar, so a subcommand registered later is covered automatically.
        Asserts argparse's own refusal text: a SystemExit from a missing required flag would
        otherwise pass for the wrong reason. Collects every offender rather than stopping at the first."""
        parser = _mod.build_parser()
        both_flags = [name for name, opts in self._subcommand_flags(parser).items() if {"--projects", "--this-repo"} <= opts]
        # One subcommand per registration path: the shim's helper call, the hand-rolled group
        # in skill_invocation.py, and a module-registered subparser.
        assert {"subagents", "skill-invocation", "skill-pair"} <= set(both_flags)
        not_refused = []
        for name in both_flags:
            try:
                parser.parse_args([name, "--this-repo", "--projects", "x"])
            except SystemExit:
                refused = "not allowed with argument" in capsys.readouterr().err
            else:
                refused = False
            if not refused:
                not_refused.append(name)
        assert not not_refused, f"accepted both flags or exited for another reason: {not_refused}"

    def test_this_repo_alone_parses_without_error(self):
        parser = _mod.build_parser()
        parsed = parser.parse_args(["buckets", "--this-repo"])
        assert parsed.projects == "*"
        assert parsed.this_repo is True

    def test_pr_link_without_repo_flag_defaults_to_none(self):
        parser = _mod.build_parser()
        parsed = parser.parse_args(["pr-link", "--branches", "feat-y"])
        assert parsed.repo is None

    def test_pr_link_with_repo_flag_parses_value(self):
        parser = _mod.build_parser()
        parsed = parser.parse_args(["pr-link", "--branches", "feat-y", "--repo", "owner/repo"])
        assert parsed.repo == "owner/repo"


class TestIterSessionsOrdering:
    """iter_sessions must yield in a single flat sort over full file paths, NOT
    grouped by directory. cmd_audit_routing's redact-label first pass assigns
    Project-N labels by first-seen order, and this repo's own worktree dirs
    (-home-u-repo vs -home-u-repo--claude-worktrees-b) are the prefix-colliding
    pair whose flat-sort vs dir-grouped order differs."""

    def test_flat_path_order_across_prefix_colliding_dirs(self, tmp_path):
        projects = tmp_path / "projects"
        main_dir = projects / "-home-u-repo"
        wt_dir = projects / "-home-u-repo--claude-worktrees-b"
        main_dir.mkdir(parents=True)
        wt_dir.mkdir(parents=True)
        _write_jsonl(main_dir / "a.jsonl", [_asst("claude-sonnet-4-6", branch="main")])
        _write_jsonl(wt_dir / "m.jsonl", [_asst("claude-sonnet-4-6", branch="b")])
        yielded = [jsonl for jsonl, _records in _mod.iter_sessions(projects, "*")]
        # The flat full-path sort places the worktree dir's file first ('-' 0x2D <
        # '/' 0x2F at the byte after the shared prefix); a dir-grouped traversal
        # would place the main dir first. Pin the flat order explicitly.
        expected = sorted(projects.glob("*/*.jsonl"))
        assert yielded == expected, (
            f"iter_sessions must yield in flat full-path sort order; got "
            f"{[p.parent.name + '/' + p.name for p in yielded]}"
        )


class TestPathToProjectSlug:
    """The slug transform and its known, accepted lossiness."""

    def test_main_repo_path(self):
        assert _mod.scope._path_to_project_slug("/home/<user>/repo") == "-home-<user>-repo"

    def test_worktree_path(self):
        assert (_mod.scope._path_to_project_slug("/home/<user>/repo/.claude/worktrees/b")
                == "-home-<user>-repo--claude-worktrees-b")

    def test_known_slug_collision_is_accepted(self):
        """`/` and `.` both map to `-`, so distinct paths can collapse to one slug.
        This is Claude Code's own dir-naming scheme, not ours to change; the repo
        scoping accepts this residual (see _repo_scoped_project_slugs). Pinned so a
        later refactor cannot silently assume injectivity."""
        assert _mod.scope._path_to_project_slug("/home/<user>/a/b") == _mod.scope._path_to_project_slug("/home/<user>/a.b")


# ---------------------------------------------------------------------------
# iter_sessions subagent merge
# ---------------------------------------------------------------------------


class TestIterSessionsSubagentMerge:
    def test_include_subagents_false_ignores_subdir(self, fake_projects):
        """include_subagents=False must not read the subagents/ subdirectory."""
        session_id = "sess-abc"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main"),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="main", sidechain=True),
        ])
        results = list(_mod.iter_sessions(fake_projects.parent, include_subagents=False))
        assert len(results) == 1
        _path, records = results[0]
        # Only the main record; sidechain record must not appear.
        assert len(records) == 1
        assert records[0].get("isSidechain") is False

    def test_include_subagents_true_merges_subagent_records(self, fake_projects):
        """include_subagents=True appends subagent file records to the session's record list."""
        session_id = "sess-def"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main"),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="main", sidechain=True),
        ])
        results = list(_mod.iter_sessions(fake_projects.parent, include_subagents=True))
        assert len(results) == 1
        _path, records = results[0]
        assert len(records) == 2
        types = [r.get("isSidechain") for r in records]
        assert False in types
        assert True in types

    def test_missing_subagent_dir_is_safe(self, fake_projects):
        """A session with no subagents/ directory yields normally with include_subagents=True."""
        _write_jsonl(fake_projects / "lone-session.jsonl", [
            _asst("claude-opus-4-7", branch="main"),
        ])
        # No subagents/ dir for this session.
        results = list(_mod.iter_sessions(fake_projects.parent, include_subagents=True))
        assert len(results) == 1
        _path, records = results[0]
        assert len(records) == 1

    def test_multiple_subagent_files_all_merged(self, fake_projects):
        """All *.jsonl files in the subagents/ dir are merged into one record list."""
        session_id = "sess-multi"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main"),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="main", sidechain=True),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-2", [
            _asst("claude-haiku-4-5", branch="main", sidechain=True),
        ])
        results = list(_mod.iter_sessions(fake_projects.parent, include_subagents=True))
        assert len(results) == 1
        _path, records = results[0]
        assert len(records) == 3, "expected main + 2 subagent records"
        models = [(r.get("message") or {}).get("model") for r in records]
        assert "claude-opus-4-7" in models
        assert "claude-sonnet-4-6" in models
        assert "claude-haiku-4-5" in models

    def test_corrupt_line_in_subagent_file_skipped_gracefully(self, fake_projects):
        """A corrupt JSON line in a subagent file is skipped; valid records still merge."""
        session_id = "sess-corrupt"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main"),
        ])
        # Write a subagent file with one valid record and one corrupt line.
        subdir = fake_projects / session_id / _mod.SUBAGENT_SUBDIR
        subdir.mkdir(parents=True, exist_ok=True)
        (subdir / "agent-corrupt.jsonl").write_text(
            '{"type":"assistant","isSidechain":true,"gitBranch":"main",'
            '"message":{"model":"claude-sonnet-4-6","content":[],"usage":{}}}\n'
            'THIS IS NOT JSON\n'
        )
        results = list(_mod.iter_sessions(fake_projects.parent, include_subagents=True))
        assert len(results) == 1
        _path, records = results[0]
        sidechain = [r for r in records if r.get("isSidechain")]
        assert len(sidechain) == 1, "corrupt line skipped; valid sidechain record present"


# ---------------------------------------------------------------------------
# format-drift canary
# ---------------------------------------------------------------------------


class TestFormatDriftCanary:
    def test_drift_warning_emitted_when_spawns_but_no_sidechain_turns(
        self, fake_projects, capsys
    ):
        """Spawn tool_use with no subagent files → WARNING on stderr."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[
                _agent_use("a1", "staff-backend-engineer"),
            ]),
        ])
        _mod.cmd_subagents(_subagents_args())
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "isSidechain" in captured.err
        # The warning must not bleed into stdout table rows.
        assert "WARNING" not in captured.out

    def test_no_warning_when_no_spawns(self, fake_projects, capsys):
        """No Agent/Task tool_uses → no drift warning."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main"),
        ])
        _mod.cmd_subagents(_subagents_args())
        assert "WARNING" not in capsys.readouterr().err

    def test_no_warning_old_inlined_format(self, fake_projects, capsys):
        """Agent spawn + isSidechain records inlined in the top-level file → no warning."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[
                _agent_use("a1", "staff-backend-engineer"),
            ]),
            _asst("claude-sonnet-4-6", branch="main", sidechain=True),
        ])
        _mod.cmd_subagents(_subagents_args())
        assert "WARNING" not in capsys.readouterr().err

    def test_drift_warning_also_fires_in_skill_pair(self, fake_projects, capsys):
        """cmd_skill_pair also emits the drift warning when spawns have no sidechain turns."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
                _agent_use("a1", "staff-backend-engineer"),
            ]),
        ])
        _mod.cmd_skill_pair(_skill_pair_args())
        assert "WARNING" in capsys.readouterr().err

    def test_subagent_records_without_issidechain_field_treated_as_main_thread(
        self, fake_projects, capsys
    ):
        """Subagent records missing the isSidechain field are counted as main-thread.

        This documents the failure mode if the transcript format drifts to drop
        isSidechain — subagent turns silently appear in the main row, not sidechain.
        """
        session_id = "sess-no-sidechain-field"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[
                _agent_use("a1", "check-runner"),
            ]),
        ])
        # Subagent file record WITHOUT isSidechain field.
        subdir = fake_projects / session_id / _mod.SUBAGENT_SUBDIR
        subdir.mkdir(parents=True, exist_ok=True)
        rec = _asst("claude-sonnet-4-6", branch="main", sidechain=True)
        del rec["isSidechain"]  # simulate format drift
        (subdir / "agent-drifted.jsonl").write_text(json.dumps(rec) + "\n")
        _mod.cmd_subagents(_subagents_args())
        captured = capsys.readouterr()
        sidechain_lines = [ln for ln in captured.out.splitlines() if "sidechain" in ln]
        # Without isSidechain=True, the subagent record is not a sidechain turn.
        assert not sidechain_lines, (
            "subagent records without isSidechain field must not appear in sidechain row"
        )
        # The drift canary fires: spawns=1 (main-thread Agent), sidechain_turns=0
        # (subagent record has no isSidechain field → not counted as sidechain).
        assert "WARNING" in captured.err

    def test_drift_warning_also_fires_in_cache_efficiency(self, fake_projects, capsys):
        """cmd_cache_efficiency also emits the drift warning when spawns
        have no sidechain turns."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[
                _agent_use("a1", "staff-backend-engineer"),
            ]),
        ])
        _mod.cmd_cache_efficiency(_cache_efficiency_args())
        assert "WARNING" in capsys.readouterr().err

    def test_no_warning_in_cache_efficiency_on_healthy_corpus(self, fake_projects, capsys):
        """The negative counterpart for cmd_cache_efficiency: no spawns, no
        warning."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", cache_read=100),
        ])
        _mod.cmd_cache_efficiency(_cache_efficiency_args())
        assert "WARNING" not in capsys.readouterr().err

    def test_no_warning_in_cache_efficiency_with_spawn_and_real_sidechain_turn(self, fake_projects, capsys):
        """The true no-drift case: a spawn paired with an actual sidechain
        assistant turn in the subagents/ file. The spawn-only case above
        (sidechain=0) and the no-spawn case (sidechain=0) both leave
        total_sidechain_turns at 0 regardless of whether
        _scan_cache_efficiency_group's return value is summed correctly --
        only this spawn=1/sidechain=1 case proves the accumulation actually
        works, since a dropped or off-by-one return value would still print
        no warning in the other two cases but would wrongly warn here."""
        session_id = "sess-side"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[
                _agent_use("a1", "staff-backend-engineer"),
            ]),
        ])
        side_rec = _priced("claude-sonnet-5", cache_read=100)
        side_rec["isSidechain"] = True
        _write_subagent_jsonl(fake_projects, session_id, "a1", [side_rec])
        _mod.cmd_cache_efficiency(_cache_efficiency_args())
        assert "WARNING" not in capsys.readouterr().err

    def test_no_warning_in_cache_efficiency_with_spawn_and_unpriced_sidechain_turn(self, fake_projects, capsys):
        """The cache-efficiency counterpart to
        TestCostFormatDriftCanary.test_no_warning_in_cost_with_spawn_and_unpriced_sidechain_turn
        (test_transcript_cost.py): an unpriced sidechain assistant turn must
        still count toward
        _scan_cache_efficiency_group's returned sidechain_turns_read, since
        that count happens before the group scan's own `if not usage:
        continue` guard."""
        session_id = "sess-unpriced-side-ce"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[
                _agent_use("a1", "staff-backend-engineer"),
            ]),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "a1", [
            _asst("claude-opus-4-7", branch="main", sidechain=True),
        ])
        _mod.cmd_cache_efficiency(_cache_efficiency_args())
        assert "WARNING" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# subagent format contract
# ---------------------------------------------------------------------------


class TestSubagentFormatContract:
    """Pin the on-disk subagent transcript format the reader depends on.

    These assertions describe what the code EXPECTS from the JSONL on disk.
    If any of these fail on a real corpus run (not in CI), the transcript format
    has drifted — update the reader (and iter_sessions) before trusting metric output.
    """

    def test_subagent_dir_path_convention(self, fake_projects):
        """Subagent files live at <session_id>/subagents/*.jsonl relative to project dir."""
        session_id = "abc123"
        agent_id = "agent-xyz"
        _write_subagent_jsonl(
            fake_projects, session_id, agent_id,
            [_asst("claude-sonnet-4-6", sidechain=True)]
        )
        subdir = fake_projects / session_id / _mod.SUBAGENT_SUBDIR
        assert subdir.is_dir(), f"Expected subagent dir at {subdir}"
        files = list(subdir.glob("*.jsonl"))
        assert len(files) == 1

    def test_subagent_assistant_record_carries_required_fields(self, fake_projects):
        """Assistant-type subagent records carry isSidechain, gitBranch, type, message.model
        when read back through iter_sessions with include_subagents=True."""
        session_id = "contract-sess"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="feat"),
        ])
        _write_subagent_jsonl(
            fake_projects, session_id, "agent-contract",
            [_asst("claude-sonnet-4-6", branch="feat", sidechain=True)]
        )
        results = list(_mod.iter_sessions(fake_projects.parent, include_subagents=True))
        assert len(results) == 1
        _path, records = results[0]
        sidechain_records = [r for r in records if r.get("isSidechain")]
        assert len(sidechain_records) == 1, "expected one sidechain record from subagent file"
        rec = sidechain_records[0]
        assert rec.get("isSidechain") is True
        assert rec.get("gitBranch") == "feat"
        assert rec.get("type") == "assistant"
        assert (rec.get("message") or {}).get("model") == "claude-sonnet-4-6"

    def test_user_msg_helper_has_no_message_model(self):
        """User-type records from _user_msg() have no message.model.

        This documents the helper's shape, not an iter_sessions invariant — user
        records in subagent files are not relied on by the script's analysis paths.
        """
        rec = _user_msg("some content", branch="feat")
        # isSidechain may be absent on user records; message.model must not be asserted on them
        assert rec.get("type") == "user"
        assert (rec.get("message") or {}).get("model") is None


# ---------------------------------------------------------------------------
# _is_fresh_user_prompt (render.py)
# ---------------------------------------------------------------------------


class TestIsFreshUserPrompt:
    def test_plain_text_user_record_returns_true(self):
        rec = _user_msg("Please fix that", branch="main")
        assert _mod._is_fresh_user_prompt(rec) is True

    def test_tool_result_bearing_record_returns_false(self):
        rec = _user_msg([_tool_result("t1", "some result")], branch="main")
        assert _mod._is_fresh_user_prompt(rec) is False

    def test_ismeta_true_returns_false(self):
        rec = {
            "type": "user",
            "isMeta": True,
            "message": {"content": "injected meta"},
            "gitBranch": "main",
        }
        assert _mod._is_fresh_user_prompt(rec) is False

    def test_issidechain_true_returns_false(self):
        rec = {
            "type": "user",
            "isSidechain": True,
            "message": {"content": "sidechain msg"},
            "gitBranch": "main",
        }
        assert _mod._is_fresh_user_prompt(rec) is False

    def test_iscompactsummary_true_returns_false(self):
        rec = {
            "type": "user",
            "isCompactSummary": True,
            "message": {"content": "summary"},
            "gitBranch": "main",
        }
        assert _mod._is_fresh_user_prompt(rec) is False

    def test_empty_text_content_returns_false(self):
        rec = _user_msg("", branch="main")
        assert _mod._is_fresh_user_prompt(rec) is False

    def test_assistant_record_returns_false(self):
        rec = _asst("claude-sonnet-4-6", branch="main")
        assert _mod._is_fresh_user_prompt(rec) is False

    def test_task_notification_only_record_returns_false(self):
        """GH-1312: a harness-delivered task notification is not the human speaking."""
        rec = _task_notification_msg()
        assert _mod._is_fresh_user_prompt(rec) is False

    def test_human_text_beside_a_task_notification_returns_true(self):
        rec = _user_msg(f"{_TASK_NOTIFICATION_TEXT}\nplease also rerun the lint step")
        assert _mod._is_fresh_user_prompt(rec) is True

    def test_unterminated_task_notification_returns_true(self):
        """An envelope with no closing tag is left in place by the strip, so the record stays fresh."""
        rec = _user_msg("<task-notification>\n<summary>no closing tag here")
        assert _mod._is_fresh_user_prompt(rec) is True

    def test_self_quoting_task_notification_returns_true(self):
        """Known limitation: the strip stops at the first closer, so the outer envelope's tail survives as text.

        A check keyed on the record's `origin` would flip this on purpose.
        """
        rec = _user_msg(
            "<task-notification><summary>Sample record: "
            "<task-notification><summary>inner</summary></task-notification>"
            "</summary></task-notification>"
        )
        assert _mod._is_fresh_user_prompt(rec) is True


# ---------------------------------------------------------------------------
# _is_task_notification (render.py)
# ---------------------------------------------------------------------------


class TestIsTaskNotification:
    @pytest.mark.parametrize(
        "rec",
        [
            pytest.param(_task_notification_msg(), id="string-envelope"),
            pytest.param(
                _user_msg(f"{_TASK_NOTIFICATION_TEXT}\n\n  {_TASK_NOTIFICATION_TEXT}"),
                id="two-envelopes-with-whitespace-between",
            ),
            pytest.param(
                _user_msg([{"type": "text", "text": _TASK_NOTIFICATION_TEXT}]),
                id="envelope-in-list-text-block",
            ),
        ],
    )
    def test_envelope_only_record_returns_true(self, rec):
        assert _mod._is_task_notification(rec) is True

    @pytest.mark.parametrize(
        "rec",
        [
            pytest.param(_user_msg("Please fix that"), id="plain-prompt"),
            pytest.param(
                _user_msg(f"{_TASK_NOTIFICATION_TEXT}\nplease also rerun the lint step"),
                id="human-text-beside-envelope",
            ),
            pytest.param(
                _user_msg("<task-notification>\n<summary>no closing tag here"),
                id="unterminated-envelope",
            ),
            pytest.param(
                {**_task_notification_msg(), "isSidechain": True},
                id="sidechain-envelope",
            ),
            pytest.param(
                {**_task_notification_msg(), "isMeta": True},
                id="meta-envelope",
            ),
            pytest.param(
                _user_msg([_tool_result("t1", _TASK_NOTIFICATION_TEXT)]),
                id="tool-result-record",
            ),
            pytest.param(_user_msg("   "), id="blank-text-record"),
            pytest.param(_asst("claude-sonnet-4-6"), id="assistant-record"),
        ],
    )
    def test_record_that_is_not_envelope_only_main_thread_user_text_returns_false(self, rec):
        assert _mod._is_task_notification(rec) is False


# ---------------------------------------------------------------------------
# Multi-account scope (transcript-corpus-multi-account-scope plan) —
# cross-subcommand resolved-scope-header and roots-threading coverage.
# ---------------------------------------------------------------------------


def _fake_gh_pr_list_run(cmd, *a, **k):
    """A no-op `gh` double for subcommands (pr-link) whose branch-iteration
    loop shells out regardless of session content."""
    if cmd[:2] == ["gh", "pr"]:
        return subprocess.CompletedProcess(cmd, 0, "[]", "")
    return subprocess.CompletedProcess(cmd, 0, "", "")


# (cli_name, header_name, cmd_func, zero-arg args factory) for the
# subcommands whose resolved-scope header prints unconditionally, even over
# an empty scope. review-trace and skill-invocation print unconditionally
# too, but carry zero-match message text and branches the other rows don't, so
# they get their own tests below rather than a row here.
_UNCONDITIONAL_HEADER_CASES: list[tuple[str, str, object, object]] = [
    ("buckets", "BUCKETS", _mod.cmd_buckets,
     lambda: type("A", (), {"projects": "*", "this_repo": False, "branches": None})()),
    ("fail-seq", "FAIL SEQ", _mod.cmd_fail_seq,
     lambda: type("A", (), {"projects": "*", "this_repo": False, "branches": "main"})()),
    ("struggle", "STRUGGLE", _mod.cmd_struggle,
     lambda: type("A", (), {"projects": "*", "this_repo": False, "branches": None})()),
    ("duration", "DURATION", _mod.cmd_duration,
     lambda: type("A", (), {"projects": "*", "this_repo": False, "branches": None})()),
    ("subagents", "SUBAGENTS", _mod.cmd_subagents, _subagents_args),
    ("subagent-mix", "SUBAGENT MIX", _mod.cmd_subagent_mix,
     lambda: type("A", (), {"projects": "*", "this_repo": False, "branches": None, "per_session": False})()),
    ("reviewer-yield", "REVIEWER YIELD", _mod.cmd_reviewer_yield, _reviewer_yield_args),
    ("skill-pair", "SKILL PAIR", _mod.cmd_skill_pair, _skill_pair_args),
    ("pr-link", "PR LINK", _mod.cmd_pr_link,
     lambda: type("A", (), {
         "projects": "*", "this_repo": False, "branches": "main",
         "repo": "owner/repo", "author": None,
     })()),
    ("commit-gate", "COMMIT GATE", _mod.cmd_commit_gate, lambda: _gate_args("code-review")),
    ("audit-routing", "AUDIT ROUTING", _mod.cmd_audit_routing, _audit_routing_args),
    ("cost", "COST", _mod.cmd_cost, _cost_args),
    ("context-distribution", "CONTEXT DISTRIBUTION", _mod.cmd_context_distribution, _context_distribution_args),
    ("context-composition", "CONTEXT COMPOSITION", _mod.cmd_context_composition,
     lambda: type("A", (), {
         "projects": "*", "this_repo": False, "since": None, "no_redact": False, "extra_config_dirs": None,
     })()),
    ("cost-trend", "COST TREND", _mod.cmd_cost_trend, _cost_trend_args),
    ("cache-rebuild", "CACHE REBUILD", _mod.cmd_cache_rebuild, _cache_rebuild_args),
    ("spend-over-threshold", "SPEND OVER THRESHOLD", _mod.cmd_spend_over_threshold, _spend_over_threshold_args),
    ("rearm-backtest", "REARM BACKTEST", _mod.cmd_rearm_backtest, _rearm_backtest_args),
    ("audit-routing-shape", "AUDIT ROUTING SHAPE", _mod.cmd_audit_routing_shape, _audit_routing_shape_args),
    ("audit-routing-samples", "AUDIT ROUTING SAMPLES", _mod.cmd_audit_routing_samples, _audit_routing_samples_args),
    ("judgment-pair", "JUDGMENT PAIR", _mod.cmd_judgment_pair, _judgment_pair_args),
    ("edit-format", "EDIT FORMAT", _mod.cmd_edit_format,
     lambda: type("A", (), {"projects": "*", "this_repo": False, "no_redact": False, "extra_config_dirs": None})()),
    ("read-scope", "READ SCOPE", _mod.cmd_read_scope,
     lambda: type("A", (), {"projects": "*", "this_repo": False, "no_redact": False, "extra_config_dirs": None})()),
    ("instrument-authoring", "INSTRUMENT AUTHORING", _mod.cmd_instrument_authoring,
     lambda: type("A", (), {"projects": "*", "this_repo": False, "extra_config_dirs": None})()),
    ("cost-ledger", "COST LEDGER", _mod.cmd_cost_ledger, _cost_ledger_args),
    ("plan-boundary", "PLAN BOUNDARY", _mod.cmd_plan_boundary,
     lambda: type("A", (), {
         "projects": "*", "this_repo": False, "since": None, "no_redact": False, "extra_config_dirs": None,
     })()),
    ("sessions", "SESSIONS", _mod.cmd_sessions,
     lambda: type("A", (), {
         "projects": "*", "this_repo": False, "paths": True, "include_subagents": False,
     })()),
]


class TestAllSubcommandsSingleRootHeader:
    """Across every funnel subcommand, the resolved-scope header states the
    root count unconditionally — even at one root with nothing declared, the
    exact state that produced the original corpus undercount — and no
    per-root stderr progress line prints at one root (that line is gated on
    len(roots) > 1)."""

    _HEADER_SUFFIX = "1 root (no ~/.claude/transcript-config-dirs declared))"

    @pytest.mark.parametrize("subcommand,header_name,cmd_func,args_factory", _UNCONDITIONAL_HEADER_CASES)
    def test_header_states_one_root_and_no_progress_line_prints(
        self, fake_projects, cost_ledger_file, monkeypatch, capsys, subcommand, header_name, cmd_func, args_factory
    ):
        monkeypatch.setattr(subprocess, "run", _fake_gh_pr_list_run)
        cmd_func(args_factory())
        out, err = capsys.readouterr()
        combined = out + err
        assert f"{header_name} SOURCES (" in combined, f"{subcommand}: no resolved-scope header printed"
        assert self._HEADER_SUFFIX in combined, f"{subcommand}: header missing the unconditional root-count suffix"
        assert "scanning root" not in combined, f"{subcommand}: a single-root run must not print a per-root progress line"

    def test_review_trace_header_states_one_root_once_a_session_matches(self, fake_projects, capsys):
        """review-trace prints its header before the scan regardless of match
        count; seed one qualifying session so the matched path is covered here
        (the zero-match path has its own test)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        _mod.cmd_review_trace(_review_trace_args())
        out, err = capsys.readouterr()
        combined = out + err
        assert "REVIEW TRACE SOURCES (" in combined
        assert self._HEADER_SUFFIX in combined
        assert "scanning root" not in combined

    def test_skill_invocation_header_states_one_root_once_a_skill_matches(self, fake_projects, capsys):
        """skill-invocation prints its header before the zero-match return;
        this covers the matched path (the zero-match path has its own test)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        _mod.cmd_skill_invocation(_skill_inv_args())
        out, err = capsys.readouterr()
        combined = out + err
        assert "SKILL INVOCATION SOURCES (" in combined
        assert self._HEADER_SUFFIX in combined
        assert "scanning root" not in combined

    def test_review_trace_zero_match_still_states_its_scope(self, fake_projects, capsys):
        """A zero-match run is the case the header exists for: at one root no
        per-root progress line prints either, so without this the run is
        byte-for-byte silent and a mis-scoped scan reads as a genuine empty."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        _mod.cmd_review_trace(_review_trace_args(branches="no-such-branch"))
        out, err = capsys.readouterr()
        assert "REVIEW TRACE SOURCES (" in out
        assert self._HEADER_SUFFIX in out
        assert "No sessions matched in scope." in out
        assert "### " not in out, "no session block should be emitted on a zero-match run"
        assert "scanning root" not in out + err

    def test_review_trace_deny_summary_zero_match_still_states_its_scope(self, fake_projects, capsys):
        """--deny-summary's third state — scope matched no sessions at all, as
        distinct from matching sessions that carried no denial."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        _mod.cmd_review_trace(_review_trace_args(deny_summary=True, branches="no-such-branch"))
        out, err = capsys.readouterr()
        assert "REVIEW TRACE SOURCES (" in out
        assert self._HEADER_SUFFIX in out
        assert "No sessions matched in scope." in out
        assert "No denials found in scope." not in out, (
            "zero sessions matched is a different state from matched-but-no-denials"
        )
        assert "scanning root" not in out + err

    def test_skill_invocation_zero_match_still_states_its_scope(self, fake_projects, capsys):
        """The not-found message alone cannot distinguish a wrongly-scoped scan
        from a correctly-scoped empty one; the header is what separates them."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        _mod.cmd_skill_invocation(_skill_inv_args(branches="no-such-branch"))
        out, err = capsys.readouterr()
        assert "SKILL INVOCATION SOURCES (" in out
        assert self._HEADER_SUFFIX in out
        assert "No skill invocations found." in out
        assert "scanning root" not in out + err

    def test_skill_invocation_zero_match_header_names_the_subagent_thread_scope(
        self, fake_projects, capsys
    ):
        """The thread-scope clause is part of what a zero-match run must disclose —
        'searched main+subagents and found nothing' is a different claim from
        'searched the main thread and found nothing'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        _mod.cmd_skill_invocation(
            _skill_inv_args(branches="no-such-branch", include_subagents=True)
        )
        out = capsys.readouterr().out
        assert "main+subagents" in out
        assert "No skill invocations found." in out

    _DISTINCTIVE_GLOB = "-home-user-somerepo-worktrees-somebranch"

    def test_review_trace_zero_match_header_echoes_an_explicit_projects_glob_verbatim(
        self, fake_projects, capsys
    ):
        """review-trace's scope label IS the glob, so an operator's own --projects
        value reaches stdout even at zero matches — the reason the section's
        not-publish-safe warning has to cover empty runs. Pinned so a later
        'redact the empty case to be safe' change breaks a test, not a promise."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        _mod.cmd_review_trace(
            _review_trace_args(projects=self._DISTINCTIVE_GLOB, branches="no-such-branch")
        )
        out = capsys.readouterr().out
        assert "REVIEW TRACE SOURCES (" in out
        assert self._DISTINCTIVE_GLOB in out

    def test_skill_invocation_zero_match_header_does_not_echo_the_projects_glob(
        self, fake_projects, capsys
    ):
        """skill-invocation labels the escape hatch ('explicit --projects
        (not repo-scoped)') instead of interpolating the glob, so its output stays
        free of the operator's path even under --projects. That divergence from
        review-trace is a minimization property, not an oversight."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        _mod.cmd_skill_invocation(
            _skill_inv_args(projects=self._DISTINCTIVE_GLOB, branches="no-such-branch")
        )
        out = capsys.readouterr().out
        assert "explicit --projects (not repo-scoped)" in out
        assert self._DISTINCTIVE_GLOB not in out

    def test_review_trace_deny_only_does_not_mask_which_zero_match_state_was_reached(
        self, fake_projects, capsys
    ):
        """`any_session_matched` is set before the --deny-only skip, so a run whose
        sessions matched but carried no denial must still say 'No denials found',
        not 'No sessions matched'. Moving that assignment below the skip would
        silently misreport scope coverage as empty."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        _mod.cmd_review_trace(_review_trace_args(deny_summary=True, deny_only=True))
        out = capsys.readouterr().out
        assert "No denials found in scope." in out
        assert "No sessions matched in scope." not in out


class TestRootsThreadingSpy:
    """Every _resolve_project_scope call site threads the SAME `roots` list
    to both scope resolution and the header call —
    format-independent, catches a subcommand whose roots list isn't threaded
    identically to both call sites, regardless of what it prints. Wraps (not
    replaces) the real functions, so each subcommand's normal behavior and
    assertions from the header test above still apply; only the call
    arguments are additionally recorded."""

    @pytest.mark.parametrize("subcommand,header_name,cmd_func,args_factory", _UNCONDITIONAL_HEADER_CASES)
    def test_resolve_project_scope_and_header_receive_identical_multi_root_list(
        self, tmp_path, cost_ledger_file, monkeypatch, subcommand, header_name, cmd_func, args_factory
    ):
        expected_roots = _two_declared_roots(tmp_path, monkeypatch)
        scope_calls: list[list] = []
        header_calls: list[list] = []
        real_resolve = _mod._resolve_project_scope
        real_print = _mod._print_resolved_scope

        def spy_resolve(*a, **k):
            scope_calls.append(list(k["roots"]) if k.get("roots") is not None else None)
            return real_resolve(*a, **k)

        def spy_print(*a, **k):
            roots = a[2] if len(a) > 2 else k.get("roots")
            header_calls.append(list(roots))
            return real_print(*a, **k)

        monkeypatch.setattr(_mod, "_resolve_project_scope", spy_resolve)
        monkeypatch.setattr(_mod, "_print_resolved_scope", spy_print)
        # cost/cost-trend's cmd_* now live in cost.py and call scope._resolve_project_scope/
        # scope.print_resolved_scope by module attribute access rather than the bare shim
        # names above -- patched here too so the spy intercepts either call shape.
        monkeypatch.setattr(_mod.scope, "_resolve_project_scope", spy_resolve)
        monkeypatch.setattr(_mod.scope, "print_resolved_scope", spy_print)
        monkeypatch.setattr(subprocess, "run", _fake_gh_pr_list_run)

        cmd_func(args_factory())

        assert scope_calls, f"{subcommand}: _resolve_project_scope was never called"
        assert header_calls, f"{subcommand}: _print_resolved_scope was never called"
        assert scope_calls[-1] == expected_roots, (
            f"{subcommand}: scope resolution did not receive the multi-root list — {scope_calls[-1]!r}"
        )
        assert header_calls[-1] == expected_roots, (
            f"{subcommand}: header did not receive the multi-root list — {header_calls[-1]!r}"
        )

    def test_review_trace_scope_and_header_receive_identical_multi_root_list(self, tmp_path, monkeypatch):
        expected_roots = _two_declared_roots(tmp_path, monkeypatch)
        proj = expected_roots[0] / "-home-user-testrepo"
        proj.mkdir(parents=True)
        _write_jsonl(proj / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        scope_calls: list[list] = []
        header_calls: list[list] = []
        real_resolve = _mod._resolve_project_scope
        real_print = _mod._print_resolved_scope

        def spy_resolve(*a, **k):
            scope_calls.append(list(k["roots"]) if k.get("roots") is not None else None)
            return real_resolve(*a, **k)

        def spy_print(*a, **k):
            roots = a[2] if len(a) > 2 else k.get("roots")
            header_calls.append(list(roots))
            return real_print(*a, **k)

        monkeypatch.setattr(_mod, "_resolve_project_scope", spy_resolve)
        monkeypatch.setattr(_mod, "_print_resolved_scope", spy_print)
        # cmd_review_trace now lives in review_trace.py and calls scope._resolve_project_scope/
        # scope.print_resolved_scope by module attribute access rather than the bare shim
        # names above -- patched here too so the spy intercepts either call shape.
        monkeypatch.setattr(_mod.scope, "_resolve_project_scope", spy_resolve)
        monkeypatch.setattr(_mod.scope, "print_resolved_scope", spy_print)

        _mod.cmd_review_trace(_review_trace_args())

        assert scope_calls[-1] == expected_roots
        assert header_calls[-1] == expected_roots

    def test_skill_invocation_glob_scope_receives_the_multi_root_list_as_is(self, tmp_path, monkeypatch):
        """cmd_skill_invocation never calls _resolve_project_scope — its two
        call sites (:2146, :2148 in the plan's line numbering) route through
        _iter_glob_scoped_sessions/_iter_scoped_sessions directly. Above one
        root, the --projects branch takes the _iter_glob_scoped_sessions path
        with the full roots list (not iter_sessions' single-Path shape)."""
        expected_roots = _two_declared_roots(tmp_path, monkeypatch)
        calls: list[list] = []
        real_glob_scoped = _mod.scope._iter_glob_scoped_sessions

        def spy_glob_scoped(roots, *a, **k):
            calls.append(list(roots))
            return real_glob_scoped(roots, *a, **k)

        monkeypatch.setattr(_mod.scope, "_iter_glob_scoped_sessions", spy_glob_scoped)

        _mod.cmd_skill_invocation(_skill_inv_args())  # projects="*" -- the explicit-glob branch

        assert calls, "_iter_glob_scoped_sessions was never called"
        assert calls[-1] == expected_roots

    def test_skill_invocation_repo_scoped_call_receives_the_multi_root_list(self, tmp_path, monkeypatch):
        """The --this-repo-equivalent default branch (--projects unset) threads
        roots=roots into _iter_scoped_sessions rather than defaulting."""
        expected_roots = _two_declared_roots(tmp_path, monkeypatch)
        calls: list[list | None] = []
        real_scoped = _mod.scope._iter_scoped_sessions

        def spy_scoped(slugs, include_subagents, roots=None):
            calls.append(list(roots) if roots is not None else None)
            return real_scoped(slugs, include_subagents, roots=roots)

        monkeypatch.setattr(_mod.scope, "_iter_scoped_sessions", spy_scoped)
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_git_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_git_run)

        _mod.cmd_skill_invocation(_skill_inv_args(projects=None))  # unset -- the repo-scoped default branch

        assert calls, "_iter_scoped_sessions was never called"
        assert calls[-1] == expected_roots

    def test_single_root_skill_invocation_glob_call_passes_a_bare_path_not_a_list(self, fake_projects, monkeypatch):
        """At one root, cmd_skill_invocation's --projects branch keeps calling
        iter_sessions(roots[0], ...) directly (a single Path, not a list) —
        iter_sessions' documented flat-sort-over-full-paths ordering guarantee
        is unaffected by this plan at single root."""
        calls: list[Path] = []
        real_iter_sessions = _mod.corpus.iter_sessions

        def spy_iter_sessions(projects_dir, *a, **k):
            calls.append(projects_dir)
            return real_iter_sessions(projects_dir, *a, **k)

        monkeypatch.setattr(_mod.corpus, "iter_sessions", spy_iter_sessions)

        _mod.cmd_skill_invocation(_skill_inv_args())  # projects="*", single root (fake_projects' PROJECTS_DIR)

        assert calls == [_mod.scope.PROJECTS_DIR]


class TestThisRepoUnionsAcrossRoots:
    """--this-repo (and, at the _iter_scoped_sessions layer it shares, every
    other slug-scoped caller) unions across every resolved root, whether
    those roots came from a declared-roots file or from cost's own explicit
    --config-dir extras -- the union mechanism at this layer is the same
    regardless of which resolver assembled `roots`."""

    def test_multi_root_slug_match_is_name_only_and_dedupes_across_roots(self, tmp_path):
        """Direct coverage of _iter_scoped_sessions(roots=[a, b]): the same
        slug present under two roots unions both sessions, and a third root
        that's a symlink alias to one of the first two contributes no
        duplicate (dedup spans every root, not just identical-path roots)."""
        root_a = tmp_path / "acct-a"
        proj_a = root_a / "-repo-main"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [_asst("claude-sonnet-4-6", branch="from-root-a")])

        root_b = tmp_path / "acct-b"
        proj_b = root_b / "-repo-main"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [_asst("claude-sonnet-4-6", branch="from-root-b")])

        root_c_alias = tmp_path / "acct-a-alias"
        root_c_alias.symlink_to(root_a)

        sessions = list(_mod._iter_scoped_sessions(
            ["-repo-main"], False, roots=[root_a, root_b, root_c_alias],
        ))
        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-root-a", "from-root-b"}
        assert len(sessions) == 2  # root_c_alias contributes no duplicate of root_a's session

    def test_this_repo_excludes_foreign_project_dirs_under_extra_root(self, tmp_path):
        """The minimization guard that replaces the (out-of-scope-here)
        refusal on cost's --this-repo + --config-dir combination — must not
        be skipped: a directory under a declared
        root that isn't one of the resolved worktree slugs stays excluded,
        proving the union doesn't widen matching to every project on that root."""
        root_a = tmp_path / "acct-a"
        proj_a = root_a / "-repo-main"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [_asst("claude-sonnet-4-6", branch="from-root-a")])

        root_b = tmp_path / "acct-b"
        proj_b_matching = root_b / "-repo-main"
        proj_b_matching.mkdir(parents=True)
        _write_jsonl(proj_b_matching / "sess-b.jsonl", [_asst("claude-sonnet-4-6", branch="from-root-b")])
        proj_b_foreign = root_b / "-home-user-unrelated-secret-clientname"
        proj_b_foreign.mkdir(parents=True)
        _write_jsonl(proj_b_foreign / "sess-foreign.jsonl", [_asst("claude-sonnet-4-6", branch="foreign-branch")])

        sessions = list(_mod._iter_scoped_sessions(["-repo-main"], False, roots=[root_a, root_b]))
        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-root-a", "from-root-b"}
        assert "foreign-branch" not in branches_seen

    def test_this_repo_slug_collision_admits_foreign_project_with_identical_slug(
        self, tmp_path, monkeypatch
    ):
        """Pins a known, plan-accepted residual risk (see Step 21 of
        .claude/plans/transcript-corpus-multi-account-scope.md: "[/.]→- is
        not injective, so /a/b.c and /a/b/c collide"), not a bug to fix here.
        _iter_scoped_sessions matches by basename equality alone, so it
        cannot distinguish this repo's own project dir from a foreign
        account's unrelated project dir that happens to collide onto the
        same slug -- under --this-repo, both are admitted. A future change to
        slug derivation that silently alters this posture, in either
        direction, must fail this test."""
        this_repo_slug = _mod.scope._path_to_project_slug("/a/b/c")
        foreign_path_slug = _mod.scope._path_to_project_slug("/a/b.c")
        assert this_repo_slug == foreign_path_slug == "-a-b-c"  # the collision this test pins

        root_a = tmp_path / "acct-a"
        proj_a = root_a / this_repo_slug
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [_asst("claude-sonnet-4-6", branch="this-repo-session")])

        root_b = tmp_path / "acct-b"
        proj_b_colliding = root_b / foreign_path_slug
        proj_b_colliding.mkdir(parents=True)
        _write_jsonl(proj_b_colliding / "sess-foreign.jsonl",
                     [_asst("claude-sonnet-4-6", branch="foreign-colliding-session")])

        args = type("A", (), {"projects": "*", "this_repo": True})()
        args._this_repo_slugs = [this_repo_slug]
        monkeypatch.setattr(_mod.scope, "_repo_scoped_project_slugs", lambda *a, **k: args._this_repo_slugs)

        session_iter, _scope_label = _mod._resolve_project_scope(args, "buckets", roots=[root_a, root_b])
        branches_seen = {rec["gitBranch"] for _jsonl, records in session_iter for rec in records}
        assert branches_seen == {"this-repo-session", "foreign-colliding-session"}

    def test_this_repo_unions_same_slug_across_roots(self, tmp_path, monkeypatch, capsys):
        """End-to-end: buckets --this-repo, with a declared-roots file adding
        a second root that also contains this repo's own worktree slug --
        both roots' sessions appear in one report, not just the active
        profile's."""
        root_a = tmp_path / "acct-a"
        proj_a = root_a / "-repo-main"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [_asst("claude-sonnet-4-6", branch="feat-in-root-a")])
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", root_a)

        root_b = tmp_path / "acct-b-config"
        proj_b = root_b / "projects" / "-repo-main"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [_asst("claude-sonnet-4-6", branch="feat-in-root-b")])
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{root_b}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod.cmd_buckets(type("A", (), {"projects": "*", "this_repo": True, "branches": None})())
        out = capsys.readouterr().out
        assert "feat-in-root-a" in out
        assert "feat-in-root-b" in out
        assert "2 roots" in out

    def test_this_repo_fail_closed_across_all_roots(self, tmp_path):
        """The :2061-equivalent deny case: zero slug matches under EVERY
        resolved root still exits 1 — direct unit coverage of `any(...
        for root in roots for slug in slugs)` iterating every root, not just
        roots[0]. Calls _resolve_project_scope directly with a hand-built
        multi-element `roots` list (unreachable through the real CLI today,
        since an explicit top-level --config-dir always collapses `roots` to
        one element — see _resolve_scan_roots) as a robustness pin against a
        future precedence change, per the plan's own framing."""
        root_a = tmp_path / "acct-a"
        root_a.mkdir()
        root_b = tmp_path / "acct-b"
        root_b.mkdir()
        args = argparse.Namespace(this_repo=True, projects="*", config_dir=str(root_a), _this_repo_slugs=["-repo-main"])
        with pytest.raises(SystemExit) as exc_info:
            _mod._resolve_project_scope(args, "buckets", roots=[root_a, root_b])
        assert exc_info.value.code == 1

    def test_this_repo_allow_case_when_only_a_non_first_root_matches(self, tmp_path):
        """Companion allow-case to the deny test above: a match under the
        SECOND resolved root (not roots[0]) must not fail closed — proves the
        `any(...)` genuinely iterates every root rather than short-circuiting
        on the first."""
        root_a = tmp_path / "acct-a"
        root_a.mkdir()
        root_b = tmp_path / "acct-b"
        (root_b / "-repo-main").mkdir(parents=True)
        args = argparse.Namespace(this_repo=True, projects="*", config_dir=str(root_a), _this_repo_slugs=["-repo-main"])
        session_iter, scope_label = _mod._resolve_project_scope(args, "buckets", roots=[root_a, root_b])
        assert list(session_iter) == []  # no sessions written, but no SystemExit either
        assert scope_label == "this repo (1 project dirs)"


class TestIterScopedSessionsUnreadableRoot:
    """Security regression: root.iterdir() raises PermissionError on an
    unreadable root with no exception handling in the pre-fix code -- an
    uncaught traceback that prints the raw path, bypassing --redact
    entirely. Reachable via --this-repo (now multi-root by default) on any
    subcommand funneling through _iter_scoped_sessions. Must be caught,
    reported to stderr without the raw path, and the scan must continue
    across the remaining roots rather than crash."""

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_root_is_skipped_without_crash_or_path_leak(self, tmp_path, capsys):
        root_a = tmp_path / "acct-a"
        proj_a = root_a / "-repo-main"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [_asst("claude-sonnet-4-6", branch="from-readable-root")])

        root_b = tmp_path / "acct-b"
        root_b.mkdir()
        os.chmod(root_b, 0o000)
        try:
            sessions = list(_mod._iter_scoped_sessions(["-repo-main"], False, roots=[root_a, root_b]))
        finally:
            os.chmod(root_b, 0o755)  # restore before tmp_path teardown

        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-readable-root"}  # root_a's session still found -- no crash

        err = capsys.readouterr().err
        assert str(root_b) not in err  # the raw unreadable path is never printed
        assert "skipping" in err


def _assert_scan_error_chain_suppressed(excinfo) -> None:
    """`_path_stripped_scan_error`'s callers raise `from None` so the raw
    path-bearing original exception never surfaces via Python's implicit
    exception-chain printer when the new exception reaches a CLI's
    uncaught top level. Both attributes must hold for `from None` to be
    doing that job."""
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True


class TestScanGapCounter:
    """scope.py's opt-in scan-gap counter (_list_dir_recording_gaps,
    _failed_transcript_read_is_gap, _iter_project_dir_sessions). Each level
    of the traversal (root, project dir, session file) records one tag per
    unreadable directory or transcript it skips. A missing path or a
    non-directory/non-regular entry stays an empty scope, not a gap."""

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_root_records_root_level_gap_and_still_yields_readable_root(self, tmp_path):
        root_a = tmp_path / "acct-a"
        proj_a = root_a / "-repo-main"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [_asst("claude-sonnet-4-6", branch="from-readable-root")])
        root_b = tmp_path / "acct-b"
        root_b.mkdir()
        os.chmod(root_b, 0o000)
        scan_gaps = Counter()
        try:
            sessions = list(_mod.scope._iter_glob_scoped_sessions(
                [root_a, root_b], "*", False, scan_gaps=scan_gaps,
            ))
        finally:
            os.chmod(root_b, 0o755)  # restore before tmp_path teardown

        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-readable-root"}
        assert scan_gaps == Counter({_mod.scope._SCAN_GAP_ROOT: 1})

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_project_dir_records_project_dir_level_gap(self, tmp_path):
        root = tmp_path / "acct-a"
        proj_open = root / "-repo-open"
        proj_open.mkdir(parents=True)
        _write_jsonl(proj_open / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="from-open-project")])
        proj_sealed = root / "-repo-sealed"
        proj_sealed.mkdir(parents=True)
        os.chmod(proj_sealed, 0o000)
        scan_gaps = Counter()
        try:
            sessions = list(_mod.scope._iter_glob_scoped_sessions([root], "*", False, scan_gaps=scan_gaps))
        finally:
            os.chmod(proj_sealed, 0o755)

        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-open-project"}
        assert scan_gaps == Counter({_mod.scope._SCAN_GAP_PROJECT_DIR: 1})

    def test_project_dir_symlink_loop_is_skipped_with_no_gap(self, tmp_path):
        """A self-referencing symlink makes the stat fail with ELOOP (which
        `_stat_mode_unless_absent` reads as absent), so `_dedup_new_project_dirs` skips it:
        no gap is recorded and the healthy sibling is still read. Distinct
        from the sealed-directory test above, where the OSError is
        recorded as a gap."""
        root = tmp_path / "acct-a"
        proj_open = root / "-repo-open"
        proj_open.mkdir(parents=True)
        _write_jsonl(proj_open / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="from-open-project")])
        loop_link = root / "-repo-loop"
        loop_link.symlink_to(loop_link)
        scan_gaps = Counter()
        sessions = list(_mod.scope._iter_glob_scoped_sessions([root], "*", False, scan_gaps=scan_gaps))

        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-open-project"}
        assert scan_gaps == Counter()

    def test_project_dir_symlink_loop_without_scan_gaps_is_skipped_without_raising(self, tmp_path):
        """Every non-pooled caller (_iter_scoped_sessions, and
        _iter_glob_scoped_sessions when called without scan_gaps) passes
        scan_gaps=None. A symlink-loop project dir reads as absent, so it is
        skipped there too instead of raising."""
        root = tmp_path / "acct-a"
        root.mkdir(parents=True)
        loop_link = root / "-repo-loop"
        loop_link.symlink_to(loop_link)
        assert list(_mod.scope._iter_glob_scoped_sessions([root], "*", False)) == []

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_project_dir_symlink_through_sealed_ancestor_records_project_dir_level_gap(self, tmp_path):
        """A candidate that is itself a readable child of an already-readable
        root can still fail its stat with PermissionError (an OSError
        subclass) when it symlinks through a sealed intermediate directory
        further down the target path. Distinct from
        the symlink-loop test above, where the stat's ELOOP reads as absent and the
        candidate is skipped without a gap."""
        root = tmp_path / "acct-a"
        proj_open = root / "-repo-open"
        proj_open.mkdir(parents=True)
        _write_jsonl(proj_open / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="from-open-project")])
        sealed_ancestor = tmp_path / "sealed-ancestor"
        target = sealed_ancestor / "target-dir"
        target.mkdir(parents=True)
        proj_through_sealed = root / "-repo-through-sealed"
        proj_through_sealed.symlink_to(target)
        os.chmod(sealed_ancestor, 0o000)
        scan_gaps = Counter()
        try:
            sessions = list(_mod.scope._iter_glob_scoped_sessions([root], "*", False, scan_gaps=scan_gaps))
        finally:
            os.chmod(sealed_ancestor, 0o755)

        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-open-project"}
        assert scan_gaps == Counter({_mod.scope._SCAN_GAP_PROJECT_DIR: 1})

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_project_dir_symlink_through_sealed_ancestor_without_scan_gaps_propagates_the_exception(self, tmp_path):
        """A non-pooled caller passing no scan_gaps must see the stat's
        PermissionError propagate uncaught, with the raw
        proj_through_sealed path stripped from the message."""
        root = tmp_path / "acct-a"
        root.mkdir(parents=True)
        sealed_ancestor = tmp_path / "sealed-ancestor"
        target = sealed_ancestor / "target-dir"
        target.mkdir(parents=True)
        proj_through_sealed = root / "-repo-through-sealed"
        proj_through_sealed.symlink_to(target)
        os.chmod(sealed_ancestor, 0o000)
        try:
            with pytest.raises(PermissionError) as excinfo:
                list(_mod.scope._iter_glob_scoped_sessions([root], "*", False))
            assert str(proj_through_sealed) not in str(excinfo.value)
            _assert_scan_error_chain_suppressed(excinfo)
        finally:
            os.chmod(sealed_ancestor, 0o755)

    def test_iter_scoped_sessions_symlink_loop_is_skipped_without_raising(self, tmp_path):
        """The slug-matched sibling of
        test_project_dir_symlink_loop_without_scan_gaps_is_skipped_without_raising:
        _iter_scoped_sessions selects project dirs by exact slug match rather
        than glob, but shares _dedup_new_project_dirs, so a symlink-loop
        project dir is skipped there too."""
        root = tmp_path / "acct-a"
        root.mkdir(parents=True)
        loop_link = root / "-repo-loop"
        loop_link.symlink_to(loop_link)
        assert list(_mod.scope._iter_scoped_sessions(["-repo-loop"], False, roots=[root])) == []

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_iter_scoped_sessions_symlink_through_sealed_ancestor_without_scan_gaps_propagates_the_exception(
        self, tmp_path,
    ):
        """The slug-matched sibling of
        test_project_dir_symlink_through_sealed_ancestor_without_scan_gaps_propagates_
        the_exception: _iter_scoped_sessions shares _dedup_new_project_dirs, so a
        non-pooled caller passing no scan_gaps must see the stat's PermissionError
        propagate uncaught here too, raw path stripped too."""
        root = tmp_path / "acct-a"
        root.mkdir(parents=True)
        sealed_ancestor = tmp_path / "sealed-ancestor"
        target = sealed_ancestor / "target-dir"
        target.mkdir(parents=True)
        proj_through_sealed = root / "-repo-through-sealed"
        proj_through_sealed.symlink_to(target)
        os.chmod(sealed_ancestor, 0o000)
        try:
            with pytest.raises(PermissionError) as excinfo:
                list(_mod.scope._iter_scoped_sessions(["-repo-through-sealed"], False, roots=[root]))
            assert str(proj_through_sealed) not in str(excinfo.value)
            _assert_scan_error_chain_suppressed(excinfo)
        finally:
            os.chmod(sealed_ancestor, 0o755)

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_transcript_records_session_file_gap_but_readable_empty_one_does_not(self, tmp_path):
        root = tmp_path / "acct-a"
        proj = root / "-repo-main"
        proj.mkdir(parents=True)
        sealed = proj / "sealed.jsonl"
        _write_jsonl(sealed, [_asst("claude-sonnet-4-6", branch="from-sealed")])
        os.chmod(sealed, 0o000)
        (proj / "empty.jsonl").write_text("")
        scan_gaps = Counter()
        try:
            sessions = list(_mod.scope._iter_glob_scoped_sessions([root], "*", False, scan_gaps=scan_gaps))
        finally:
            os.chmod(sealed, 0o644)

        assert sessions == []  # neither the unreadable nor the readable-empty file yields
        assert scan_gaps == Counter({_mod.scope._SCAN_GAP_SESSION_FILE: 1})

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_project_dir_without_search_permission_records_session_file_gap(self, tmp_path):
        """A directory with read but no search (x) permission lists its
        entries, but stat and open on each entry fail with EACCES. That is
        neither an absent entry nor a readable transcript, so it counts as
        a session-file gap and propagates no exception."""
        root = tmp_path / "acct-a"
        proj = root / "-repo-main"
        proj.mkdir(parents=True)
        _write_jsonl(proj / "held.jsonl", [_asst("claude-sonnet-4-6", branch="from-held")])
        os.chmod(proj, 0o444)
        scan_gaps = Counter()
        try:
            sessions = list(_mod.scope._iter_glob_scoped_sessions([root], "*", False, scan_gaps=scan_gaps))
        finally:
            os.chmod(proj, 0o755)

        assert sessions == []
        assert scan_gaps == Counter({_mod.scope._SCAN_GAP_SESSION_FILE: 1})

    def test_nonexistent_root_records_no_gap(self, tmp_path):
        missing_root = tmp_path / "never-created"
        scan_gaps = Counter()
        sessions = list(_mod.scope._iter_glob_scoped_sessions(
            [missing_root], "*", False, scan_gaps=scan_gaps,
        ))
        assert sessions == []
        assert scan_gaps == Counter()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_iter_scoped_sessions_unreadable_project_dir_records_project_dir_gap(self, tmp_path):
        """The structural sibling of the project-dir test above: _iter_scoped_sessions
        selects project dirs by exact slug match rather than glob, but shares
        _iter_project_dir_sessions."""
        root = tmp_path / "acct-a"
        proj_open = root / "-repo-open"
        proj_open.mkdir(parents=True)
        _write_jsonl(proj_open / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="from-open-project")])
        proj_sealed = root / "-repo-sealed"
        proj_sealed.mkdir(parents=True)
        os.chmod(proj_sealed, 0o000)
        scan_gaps = Counter()
        try:
            sessions = list(_mod.scope._iter_scoped_sessions(
                ["-repo-open", "-repo-sealed"], False, roots=[root], scan_gaps=scan_gaps,
            ))
        finally:
            os.chmod(proj_sealed, 0o755)

        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-open-project"}
        assert scan_gaps == Counter({_mod.scope._SCAN_GAP_PROJECT_DIR: 1})

    def test_resolve_project_scope_single_root_glob_branch_raises_on_scan_gaps(self, tmp_path):
        root = tmp_path / "acct-a" / "projects"
        root.mkdir(parents=True)
        args = argparse.Namespace(this_repo=False, projects="*")
        with pytest.raises(ValueError):
            _mod.scope._resolve_project_scope(args, "buckets", roots=[root], scan_gaps=Counter())

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_project_dir_without_scan_gaps_skips_it_silently(self, tmp_path):
        """Same fixture as the project-dir gap test above, with scan_gaps
        omitted -- a caller passing no counter skips the unreadable
        directory silently, with no exception and the same sessions
        yielded."""
        root = tmp_path / "acct-a"
        proj_open = root / "-repo-open"
        proj_open.mkdir(parents=True)
        _write_jsonl(proj_open / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="from-open-project")])
        proj_sealed = root / "-repo-sealed"
        proj_sealed.mkdir(parents=True)
        os.chmod(proj_sealed, 0o000)
        try:
            sessions = list(_mod.scope._iter_glob_scoped_sessions([root], "*", False))
        finally:
            os.chmod(proj_sealed, 0o755)

        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-open-project"}

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_gap_counter_keys_equal_the_three_level_constants(self, tmp_path):
        root_a = tmp_path / "acct-a"
        proj_a = root_a / "-repo-a"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [_asst("claude-sonnet-4-6", branch="from-a")])
        proj_c = root_a / "-repo-c"
        proj_c.mkdir(parents=True)
        os.chmod(proj_c, 0o000)
        sealed = proj_a / "sealed.jsonl"
        _write_jsonl(sealed, [_asst("claude-sonnet-4-6", branch="from-sealed")])
        os.chmod(sealed, 0o000)
        root_b = tmp_path / "acct-b"
        root_b.mkdir()
        os.chmod(root_b, 0o000)
        scan_gaps = Counter()
        try:
            list(_mod.scope._iter_glob_scoped_sessions([root_a, root_b], "*", False, scan_gaps=scan_gaps))
        finally:
            os.chmod(proj_c, 0o755)
            os.chmod(sealed, 0o644)
            os.chmod(root_b, 0o755)

        assert set(scan_gaps) == {
            _mod.scope._SCAN_GAP_ROOT, _mod.scope._SCAN_GAP_PROJECT_DIR, _mod.scope._SCAN_GAP_SESSION_FILE,
        }

    def test_root_that_is_a_regular_file_records_no_gap(self, tmp_path):
        """Catches a missing NotADirectoryError branch in
        _list_dir_recording_gaps: a scan root that exists as a regular file
        is an empty scope, not a gap."""
        root_a = tmp_path / "acct-a"
        proj_a = root_a / "-repo-a"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [_asst("claude-sonnet-4-6", branch="from-dir-root")])
        root_b = tmp_path / "acct-b-not-a-dir"
        root_b.write_text("not a directory")
        scan_gaps = Counter()
        sessions = list(_mod.scope._iter_glob_scoped_sessions(
            [root_a, root_b], "*", False, scan_gaps=scan_gaps,
        ))
        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-dir-root"}
        assert scan_gaps == Counter()

    def test_stray_regular_file_directly_under_root_records_no_gap(self, tmp_path):
        """Asserts _dedup_new_project_dirs' S_ISDIR skip filters a stray file
        (e.g. .DS_Store) before any project-dir listing, independent of the
        NotADirectoryError branch _list_dir_recording_gaps also has."""
        root = tmp_path / "acct-a"
        proj = root / "-repo-a"
        proj.mkdir(parents=True)
        _write_jsonl(proj / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="from-repo-a")])
        (root / ".DS_Store").write_text("")
        scan_gaps = Counter()
        sessions = list(_mod.scope._iter_glob_scoped_sessions([root], "*", False, scan_gaps=scan_gaps))
        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-repo-a"}
        assert scan_gaps == Counter()

    def test_directory_dangling_symlink_and_looped_symlink_named_jsonl_record_no_gap(self, tmp_path):
        """Pins _failed_transcript_read_is_gap's classification in both
        directions, paired with the unreadable-transcript test above: a
        directory (stray.jsonl), a dangling symlink (dangling.jsonl), or a
        looped symlink (loop.jsonl) is an empty scope, not a gap, since none
        is a regular file whose read failed."""
        root = tmp_path / "acct-a"
        proj = root / "-repo-a"
        proj.mkdir(parents=True)
        _write_jsonl(proj / "real.jsonl", [_asst("claude-sonnet-4-6", branch="from-real")])
        (proj / "stray.jsonl").mkdir()
        (proj / "dangling.jsonl").symlink_to(proj / "does-not-exist.jsonl")
        (proj / "loop.jsonl").symlink_to(proj / "loop.jsonl")
        scan_gaps = Counter()
        sessions = list(_mod.scope._iter_glob_scoped_sessions([root], "*", False, scan_gaps=scan_gaps))
        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-real"}
        assert scan_gaps == Counter()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_multiple_gaps_at_every_level_accumulate_exactly(self, tmp_path):
        """Two gaps at each of the three increment sites tell accumulation
        (+=1) from assignment (=1) apart at every site at once. An
        exact-equality counter check with only one gap per level would
        pass under either bug."""
        root_0 = tmp_path / "acct-sealed-root"
        root_0.mkdir()
        root_1 = tmp_path / "acct-open-root"
        proj_sealed_a = root_1 / "proj-sealed-a"
        proj_sealed_a.mkdir(parents=True)
        proj_sealed_b = root_1 / "proj-sealed-b"
        proj_sealed_b.mkdir(parents=True)
        proj_open = root_1 / "proj-open"
        proj_open.mkdir(parents=True)
        sealed_a = proj_open / "sealed-a.jsonl"
        _write_jsonl(sealed_a, [_asst("claude-sonnet-4-6", branch="from-sealed-a")])
        sealed_b = proj_open / "sealed-b.jsonl"
        _write_jsonl(sealed_b, [_asst("claude-sonnet-4-6", branch="from-sealed-b")])
        _write_jsonl(proj_open / "open.jsonl", [_asst("claude-sonnet-4-6", branch="from-open")])

        os.chmod(root_0, 0o000)
        os.chmod(proj_sealed_a, 0o000)
        os.chmod(proj_sealed_b, 0o000)
        os.chmod(sealed_a, 0o000)
        os.chmod(sealed_b, 0o000)
        scan_gaps = Counter()
        try:
            sessions = list(_mod.scope._iter_glob_scoped_sessions(
                [root_0, root_1], "*", False, scan_gaps=scan_gaps,
            ))
        finally:
            os.chmod(root_0, 0o755)
            os.chmod(proj_sealed_a, 0o755)
            os.chmod(proj_sealed_b, 0o755)
            os.chmod(sealed_a, 0o644)
            os.chmod(sealed_b, 0o644)

        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-open"}
        assert scan_gaps == Counter({
            _mod.scope._SCAN_GAP_ROOT: 1,
            _mod.scope._SCAN_GAP_PROJECT_DIR: 2,
            _mod.scope._SCAN_GAP_SESSION_FILE: 2,
        })

    def test_project_dir_symlink_alias_across_roots_records_no_gap_and_yields_once(self, tmp_path):
        """A project dir already visited under another root, reached here
        via symlink, is a candidate _dedup_new_project_dirs drops. It is
        skipped, not recorded as a gap."""
        root_a = tmp_path / "acct-a"
        proj = root_a / "-repo-shared"
        proj.mkdir(parents=True)
        _write_jsonl(proj / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="from-shared")])
        root_b = tmp_path / "acct-b"
        root_b.mkdir()
        (root_b / "-repo-shared-alias").symlink_to(proj, target_is_directory=True)
        scan_gaps = Counter()
        sessions = list(_mod.scope._iter_glob_scoped_sessions(
            [root_a, root_b], "*", False, scan_gaps=scan_gaps,
        ))
        branches_seen = [rec["gitBranch"] for _jsonl, records in sessions for rec in records]
        assert branches_seen == ["from-shared"]  # exactly once, not twice
        assert scan_gaps == Counter()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_iter_scoped_sessions_records_two_root_level_gaps(self, tmp_path):
        """The count of two distinguishes += 1 from = 1 at _iter_scoped_sessions'
        own root-level except OSError -- the one increment site outside
        _list_dir_recording_gaps and _iter_project_dir_sessions."""
        root_a = tmp_path / "acct-a"
        proj_a = root_a / "-repo-main"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [_asst("claude-sonnet-4-6", branch="from-readable-root")])
        root_b = tmp_path / "acct-b"
        root_b.mkdir()
        os.chmod(root_b, 0o000)
        root_c = tmp_path / "acct-c"
        root_c.mkdir()
        os.chmod(root_c, 0o000)
        scan_gaps = Counter()
        try:
            sessions = list(_mod.scope._iter_scoped_sessions(
                ["-repo-main"], False, roots=[root_a, root_b, root_c], scan_gaps=scan_gaps,
            ))
        finally:
            os.chmod(root_b, 0o755)
            os.chmod(root_c, 0o755)

        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-readable-root"}
        assert scan_gaps == Counter({_mod.scope._SCAN_GAP_ROOT: 2})

    @pytest.mark.parametrize("absent_errno", [errno.ENOENT, errno.ENOTDIR, errno.EBADF, errno.ELOOP])
    def test_stat_mode_unless_absent_returns_none_for_absent_entry_errnos(self, absent_errno):
        """The errno partition is pinned with a stub, not chmod, so it holds
        under root and on every interpreter."""
        class _StatRaises:
            def stat(self):
                raise OSError(absent_errno, os.strerror(absent_errno))

        assert _mod.scope._stat_mode_unless_absent(_StatRaises()) is None

    @pytest.mark.parametrize("propagated_errno", [errno.EACCES, errno.EIO])
    def test_stat_mode_unless_absent_reraises_every_other_errno(self, propagated_errno):
        class _StatRaises:
            def stat(self):
                raise OSError(propagated_errno, os.strerror(propagated_errno))

        with pytest.raises(OSError) as excinfo:
            _mod.scope._stat_mode_unless_absent(_StatRaises())
        assert excinfo.value.errno == propagated_errno

    def test_stat_mode_unless_absent_returns_the_mode_of_an_existing_entry(self, tmp_path):
        regular_file = tmp_path / "regular.jsonl"
        regular_file.write_text("{}\n")
        directory = tmp_path / "project-dir"
        directory.mkdir()

        regular_mode = _mod.scope._stat_mode_unless_absent(regular_file)
        directory_mode = _mod.scope._stat_mode_unless_absent(directory)

        assert stat.S_ISREG(regular_mode) and not stat.S_ISDIR(regular_mode)
        assert stat.S_ISDIR(directory_mode) and not stat.S_ISREG(directory_mode)

    @pytest.mark.parametrize("absent_errno", [errno.ENOENT, errno.ELOOP])
    def test_dedup_skips_a_candidate_whose_stat_reports_it_absent_without_a_gap(self, absent_errno):
        """The call-site arm is pinned with a stat stub, not chmod, so it holds under root."""
        class _StatRaises:
            def stat(self):
                raise OSError(absent_errno, os.strerror(absent_errno))

        scan_gaps = Counter()
        yielded = list(_mod.scope._dedup_new_project_dirs([_StatRaises()], set(), scan_gaps=scan_gaps))
        assert yielded == []
        assert scan_gaps == Counter()

    def test_dedup_records_a_project_dir_gap_when_stat_fails_with_eacces(self):
        class _StatRaises:
            def stat(self):
                raise OSError(errno.EACCES, os.strerror(errno.EACCES))

        scan_gaps = Counter()
        yielded = list(_mod.scope._dedup_new_project_dirs([_StatRaises()], set(), scan_gaps=scan_gaps))
        assert yielded == []
        assert scan_gaps == Counter({_mod.scope._SCAN_GAP_PROJECT_DIR: 1})

    def test_dedup_without_scan_gaps_raises_eacces_with_the_path_stripped(self):
        sensitive_path = "/private/acct-a/projects/-repo-sealed"

        class _StatRaises:
            def stat(self):
                raise OSError(errno.EACCES, os.strerror(errno.EACCES), sensitive_path)

        with pytest.raises(OSError) as excinfo:
            list(_mod.scope._dedup_new_project_dirs([_StatRaises()], set()))
        assert excinfo.value.errno == errno.EACCES
        assert sensitive_path not in str(excinfo.value)
        _assert_scan_error_chain_suppressed(excinfo)

    @pytest.mark.parametrize("absent_errno", [errno.ENOENT, errno.ELOOP])
    def test_failed_transcript_read_is_not_a_gap_when_stat_reports_it_absent(self, absent_errno):
        class _StatRaises:
            def stat(self):
                raise OSError(absent_errno, os.strerror(absent_errno))

        assert _mod.scope._failed_transcript_read_is_gap(_StatRaises()) is False

    def test_failed_transcript_read_is_a_gap_when_stat_fails_with_eacces(self):
        class _StatRaises:
            def stat(self):
                raise OSError(errno.EACCES, os.strerror(errno.EACCES))

        assert _mod.scope._failed_transcript_read_is_gap(_StatRaises()) is True

    def test_failed_transcript_read_is_a_gap_when_stat_reports_a_regular_file(self):
        """The regular-file arm, pinned with a stat stub so it holds under root."""
        class _StatsAsRegularFile:
            def stat(self):
                return os.stat_result((stat.S_IFREG | 0o644,) + (0,) * 9)

        assert _mod.scope._failed_transcript_read_is_gap(_StatsAsRegularFile()) is True

    def test_failed_transcript_read_is_not_a_gap_when_stat_reports_a_directory(self):
        class _StatsAsDirectory:
            def stat(self):
                return os.stat_result((stat.S_IFDIR | 0o755,) + (0,) * 9)

        assert _mod.scope._failed_transcript_read_is_gap(_StatsAsDirectory()) is False


class TestGlobScopedSessionsSelection:
    """_iter_glob_scoped_sessions' own fnmatch selection of project directories
    and *.jsonl transcripts. The class covers the multi-root selection
    engine only; single-root scope uses corpus.iter_sessions (Path.glob),
    which is the reference engine."""

    @pytest.mark.parametrize("projects_glob", ["*", "feat-?", "feat-*", "[ab]*", "zeta", ".hidden*"])
    def test_multi_root_selection_equals_union_of_per_root_path_glob_selection(self, tmp_path, projects_glob):
        project_names_by_root = {
            "acct-a": ["alpha", "feat-a", "feat-bb"],
            "acct-b": ["beta", "feat-c", "zeta", ".hidden-x"],
        }
        roots = []
        for root_name, project_names in project_names_by_root.items():
            root = tmp_path / root_name
            for project_name in project_names:
                project_dir = root / project_name
                project_dir.mkdir(parents=True)
                _write_jsonl(project_dir / "sess.jsonl", [_asst("claude-sonnet-4-6", branch=project_name)])
            roots.append(root)
        stray_records = [_asst("claude-sonnet-4-6", branch="not-a-transcript")]
        backup_copy = roots[0] / "feat-a" / "sess.jsonl.bak"
        non_transcript = roots[0] / "feat-a" / "notes.txt"
        _write_jsonl(backup_copy, stray_records)
        _write_jsonl(non_transcript, stray_records)

        yielded_paths = {
            jsonl for jsonl, _records in _mod.scope._iter_glob_scoped_sessions(roots, projects_glob, False)
        }
        every_project_paths = {
            jsonl for jsonl, _records in _mod.scope._iter_glob_scoped_sessions(roots, "*", False)
        }

        expected_paths = {
            jsonl for root in roots for jsonl, _records in _mod.corpus.iter_sessions(root, projects_glob)
        }
        assert expected_paths
        assert yielded_paths == expected_paths
        if projects_glob != "*":
            assert yielded_paths < every_project_paths
        assert backup_copy not in every_project_paths
        assert non_transcript not in every_project_paths


class TestProjectsGlobStepsToParent:
    @pytest.mark.parametrize("projects_glob", ["..", "../x", "x/..", "a/../.."])
    def test_dotdot_path_component_steps_to_parent(self, projects_glob):
        assert _mod.corpus._projects_glob_steps_to_parent(projects_glob) is True

    @pytest.mark.parametrize("projects_glob", ["a..b", "..*", "...", ".hidden*", "*"])
    def test_dotdot_inside_a_component_does_not_step_to_parent(self, projects_glob):
        assert _mod.corpus._projects_glob_steps_to_parent(projects_glob) is False


class TestSingleLevelProjectsGlob:
    """_single_level_projects_glob: the runtime validator that rejects a
    --projects value Path.glob would read as more than one directory-name
    match, or a different shape entirely, than the root-level fnmatch
    matching in _iter_glob_scoped_sessions selects for. Called only from a
    multi-root branch (_resolve_project_scope's own, and cmd_skill_invocation's
    inline equivalent) -- never wired as an argparse type=, since whether the
    restriction applies depends on how many roots this invocation resolves at
    runtime, not on the flag's syntax alone. A single-root invocation reads
    corpus.iter_sessions, whose own Path.glob supports a nested-directory
    pattern and must keep accepting one unchanged."""

    @pytest.mark.parametrize("value", ["*", "-home-user-repo*", "feat-?", "[ab]*", ""])
    def test_accepts_single_level_glob_values_unchanged(self, value):
        assert _mod.scope._single_level_projects_glob(value) == value

    @pytest.mark.parametrize("value", ["a/b", "a/", "/a", "**", "a**", ".", ".."])
    def test_rejects_values_path_glob_would_read_differently(self, value):
        with pytest.raises(argparse.ArgumentTypeError):
            _mod.scope._single_level_projects_glob(value)

    @pytest.mark.parametrize(
        "cli_args",
        [["buckets", "--projects", "a/b"], ["skill-invocation", "--projects", "a/b"]],
        ids=["buckets", "skill-invocation"],
    )
    def test_cli_accepts_a_multi_segment_projects_value_at_parse_time(self, cli_args):
        """No subcommand's --projects carries an argparse type=, so
        parsing alone never rejects a nested-directory value -- rejection, when
        it applies, happens later, only under multi-root scope."""
        parser = _mod.build_parser()
        args = parser.parse_args(cli_args)
        assert args.projects == "a/b"

    def test_every_projects_registration_across_every_subcommand_has_no_argparse_type(self):
        """Walks every subparser build_parser() registers: --projects's
        one-level restriction must stay runtime-validated, since whether it
        applies depends on how many roots this invocation resolves, not on
        the flag's syntax alone."""
        parser = _mod.build_parser()
        subparsers_action = next(
            action for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        typed = [
            subcommand
            for subcommand, subparser in subparsers_action.choices.items()
            for action in subparser._actions
            if action.dest == "projects" and action.type is not None
        ]
        assert typed == []

    def test_multi_root_scope_rejects_a_multi_segment_projects_value_at_runtime(self, tmp_path):
        """The one-level `--projects` restriction applies in
        `_resolve_project_scope`'s multi-root branch: a nested-directory value
        under two roots exits 2."""
        root_a = tmp_path / "acct-a"
        root_a.mkdir()
        root_b = tmp_path / "acct-b"
        root_b.mkdir()
        args = argparse.Namespace(this_repo=False, projects="a/b")
        with pytest.raises(SystemExit) as exc:
            _mod._resolve_project_scope(args, "buckets", roots=[root_a, root_b])
        assert exc.value.code == 2

    def test_single_root_scope_accepts_a_multi_segment_projects_value_at_runtime(self, tmp_path):
        """The single-root branch reads corpus.iter_sessions, whose own
        Path.glob supports a nested-directory pattern, so this branch accepts
        a multi-segment `--projects` value."""
        root = tmp_path / "acct-a"
        nested = root / "sub" / "-repo-main"
        nested.mkdir(parents=True)
        _write_jsonl(nested / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="from-nested")])
        args = argparse.Namespace(this_repo=False, projects="sub/-repo-main")
        session_iter, _scope_label = _mod._resolve_project_scope(args, "buckets", roots=[root])
        branches_seen = {rec["gitBranch"] for _jsonl, records in session_iter for rec in records}
        assert branches_seen == {"from-nested"}

    def test_single_root_scope_rejects_a_parent_traversing_projects_value(self, tmp_path):
        """A '..' component in --projects is a real parent-directory step for
        Path.glob, not a no-op: iter_sessions must yield nothing for it
        instead of yielding a sibling account's own session file."""
        root = tmp_path / "acct-a"
        root.mkdir()
        sibling = tmp_path / "acct-b"
        sibling.mkdir()
        _write_jsonl(sibling / "secret.jsonl", [_asst("claude-sonnet-4-6", branch="from-sibling")])
        args = argparse.Namespace(this_repo=False, projects="../acct-b")
        session_iter, _scope_label = _mod._resolve_project_scope(args, "buckets", roots=[root])
        assert list(session_iter) == []

    def test_single_root_scope_reads_a_project_dir_symlinked_outside_the_root(self, tmp_path):
        """Only a '..' in the raw glob is rejected. With a single scan root,
        a project directory symlinked to a readable location outside the
        root stays in scope with the default glob. Under more than one root
        the same input has no owning root, so consumers that attribute each
        session to a root abort."""
        root = tmp_path / "acct-a"
        root.mkdir()
        outside = tmp_path / "elsewhere" / "-repo-relocated"
        outside.mkdir(parents=True)
        _write_jsonl(outside / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="from-relocated")])
        (root / "-repo-relocated").symlink_to(outside)
        args = argparse.Namespace(this_repo=False, projects="*")
        session_iter, _scope_label = _mod._resolve_project_scope(args, "buckets", roots=[root])
        branches_seen = {rec["gitBranch"] for _jsonl, records in session_iter for rec in records}
        assert branches_seen == {"from-relocated"}

    def test_single_root_scope_skips_a_symlink_loop_transcript_without_raising(self, tmp_path):
        """A looped .jsonl symlink fails open() with OSError, which the
        record parser treats as an unreadable file, so the healthy sibling
        transcript is still read and nothing raises."""
        root = tmp_path / "acct-a"
        proj = root / "-repo-main"
        proj.mkdir(parents=True)
        _write_jsonl(proj / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="from-healthy")])
        loop_link = proj / "loop.jsonl"
        loop_link.symlink_to(loop_link)
        args = argparse.Namespace(this_repo=False, projects="*")
        session_iter, _scope_label = _mod._resolve_project_scope(args, "buckets", roots=[root])
        branches_seen = {rec["gitBranch"] for _jsonl, records in session_iter for rec in records}
        assert branches_seen == {"from-healthy"}

    def test_cost_scan_diagnostic_rejects_a_parent_traversing_projects_value(
        self, tmp_path, monkeypatch, capsys,
    ):
        """cost's own per-root scan diagnostic (scope._scan_root_transcripts)
        is reached with the raw, unvalidated --projects value, a separate
        path from _resolve_project_scope's already-guarded iter_sessions
        call above: a '..' value must not count a sibling account's own
        transcripts there either."""
        default_config = tmp_path / "default-account"
        default_proj = default_config / "projects" / "-home-user-repo-a"
        default_proj.mkdir(parents=True)
        _write_jsonl(default_proj / "sess-a.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_config)

        sibling_proj = tmp_path / "secret-account" / "projects" / "-home-secret-repo"
        sibling_proj.mkdir(parents=True)
        _write_jsonl(sibling_proj / "secret.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])

        _mod.cmd_cost(_cost_args(projects="../../secret-account/projects/-home-secret-repo"))
        out = capsys.readouterr().out
        assert "cost: account-1: scanned 0 transcripts, 0 skipped (unreadable)" in out
        assert "WARNING: cost: account-1: no transcripts found for this scope" in out

    def test_skill_invocation_multi_root_rejects_a_multi_segment_projects_value_at_runtime(
        self, tmp_path, monkeypatch, capsys,
    ):
        """cmd_skill_invocation inlines the same len(roots) > 1 /
        _iter_glob_scoped_sessions branch _resolve_project_scope's own
        multi-root branch does, so it needs the identical runtime check."""
        root_a = tmp_path / "acct-a"
        root_a.mkdir()
        root_b = tmp_path / "acct-b"
        root_b.mkdir()
        args = argparse.Namespace(projects="a/b", branches=None, include_subagents=False, config_dir=None)
        monkeypatch.setattr(_mod.scope, "resolve_scan_roots", lambda _args: [root_a, root_b])
        with pytest.raises(SystemExit) as exc:
            _mod.cmd_skill_invocation(args)
        assert exc.value.code == 2
        assert "--projects" in capsys.readouterr().err


class TestPoisonedProjectsDirGlobal:
    """PROJECTS_DIR pointed at a nonexistent path, with one real root
    declared via TRANSCRIPT_CONFIG_DIRS_FILE — sessions are still found
    through the declared root, proving no funnel site still reads
    PROJECTS_DIR as an unthreaded, single-root default."""

    def test_sessions_found_via_declared_root_despite_nonexistent_projects_dir(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", tmp_path / "nonexistent-active-profile" / "projects")
        real_root = tmp_path / "real-account"
        proj = real_root / "projects" / "-home-user-testrepo"
        proj.mkdir(parents=True)
        _write_jsonl(proj / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat-declared-root")])
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{real_root}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        _mod.cmd_buckets(type("A", (), {"projects": "*", "this_repo": False, "branches": None})())
        out = capsys.readouterr().out
        assert "feat-declared-root" in out
        assert "2 roots" in out


class TestMultiRootFormatOutliers:
    """End-to-end multi-root output tests for the three format-outlier
    subcommands (judgment-pair writes --out,
    audit-routing-samples is a JSON stream with the header on stderr, cost
    redacts by default) — each reached through the declared-roots file (the
    default union _resolve_scan_roots builds from declared_transcript_roots()),
    not cost's own --config-dir extra (already covered by
    TestCostMultiRootRedaction)."""

    def test_cost_redacted_totals_sum_across_declared_roots(self, tmp_path, monkeypatch, capsys):
        default_config = tmp_path / "default-account"
        default_proj = default_config / "projects" / "-home-user-repo-a"
        default_proj.mkdir(parents=True)
        _write_jsonl(default_proj / "sess-a.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_config)

        declared_config = tmp_path / "declared-account"
        declared_proj = declared_config / "projects" / "-home-user-repo-b"
        declared_proj.mkdir(parents=True)
        _write_jsonl(declared_proj / "sess-b.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{declared_config}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        _mod.cmd_cost(_cost_args())
        out = capsys.readouterr().out
        assert "account-1/private-project-1" in out
        assert "account-2/private-project-1" in out
        assert _extract_grand_total(out) == pytest.approx(4.0)

    def test_audit_routing_samples_stdout_still_parses_as_json_across_declared_roots(
        self, tmp_path, monkeypatch, capsys
    ):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        for idx, root in enumerate(roots):
            proj = root / f"-home-user-repo-{idx}"
            proj.mkdir(parents=True)
            _write_jsonl(proj / f"sess-{idx}.jsonl", [_opus([_read_use("r1", "/a.py")], out=100)])

        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        out, err = capsys.readouterr()
        assert "2 roots" in err  # header routed to stderr, matching audit-routing-samples' convention
        # The per-root progress line is gated on len(roots) > 1, so above one
        # root (unlike TestAllSubcommandsSingleRootHeader's assertions) it must print.
        assert "scanning root 1/2..." in err
        assert "scanning root 2/2..." in err
        records = json.loads(out)
        assert {rec["session_id"] for rec in records} == {"sess-0", "sess-1"}

    def test_judgment_pair_out_file_still_one_header_line_across_declared_roots(
        self, tmp_path, monkeypatch
    ):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        for idx, root in enumerate(roots):
            proj = root / f"-home-user-repo-{idx}"
            proj.mkdir(parents=True)
            _write_jsonl(proj / f"sess-{idx}.jsonl", [
                _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
                _review_asst("Logic error in retry loop.", "2026-05-20T10:01:00.000Z"),
                _user_reply("Will fix the retry logic."),
            ])
        out_file = roots[0].parent / "out.txt"

        _mod.cmd_judgment_pair(_judgment_pair_args(out=str(out_file)))
        content = out_file.read_text()
        lines = content.splitlines()
        assert lines[0] == "JUDGMENT PAIR SOURCES (*; 2 roots)"
        header_lines = [ln for ln in lines if ln.startswith("JUDGMENT PAIR SOURCES")]
        assert len(header_lines) == 1
        assert content.count("Logic error in retry loop.") == 2  # one block per root's session


class TestResolveScanRoots:
    """_resolve_scan_roots as a directly callable, unit-testable function --
    the explicit top-level --config-dir precedence over a populated
    declared-roots file, and getattr-based tolerance of a hand-built `args`
    Namespace that predates the config_dir attribute entirely (this file's
    many such fixtures)."""

    def test_no_config_dir_no_declared_roots_returns_projects_dir_alone(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", tmp_path / "active" / "projects")
        args = argparse.Namespace(config_dir=None)
        assert _mod._resolve_scan_roots(args) == [tmp_path / "active" / "projects"]

    def test_no_config_dir_with_declared_roots_unions_both(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", tmp_path / "active" / "projects")
        declared = tmp_path / "declared-account"
        (declared / "projects").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{declared}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        args = argparse.Namespace(config_dir=None)
        assert _mod._resolve_scan_roots(args) == [
            tmp_path / "active" / "projects", declared / "projects",
        ]

    def test_explicit_top_level_config_dir_overrides_populated_declared_roots_file(
        self, monkeypatch, tmp_path
    ):
        """The flagship precedence rule: an explicit --config-dir wins outright,
        returning that one directory's projects/ subdirectory alone — the
        declared-roots file's entries are not unioned in on top of it."""
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", tmp_path / "active" / "projects")
        declared = tmp_path / "declared-account"
        (declared / "projects").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{declared}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        override_dir = tmp_path / "explicit-override"
        args = argparse.Namespace(config_dir=str(override_dir))
        assert _mod._resolve_scan_roots(args) == [override_dir / "projects"]

    def test_config_dir_attribute_absent_from_parsed_does_not_raise(self, monkeypatch, tmp_path):
        """A hand-built test `args` Namespace that predates the top-level
        --config-dir flag (this file's many such fixtures) must not raise
        AttributeError -- its absence means "not passed," the real default."""
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", tmp_path / "active" / "projects")
        args = type("A", (), {})()  # no config_dir attribute at all
        assert _mod._resolve_scan_roots(args) == [tmp_path / "active" / "projects"]

    def test_declared_root_matching_active_profile_is_deduped_not_double_listed(
        self, monkeypatch, tmp_path
    ):
        """PROJECTS_DIR is pre-seeded into seen_resolved before iterating
        declared roots, so a declared root that IS (or symlink-aliases) the
        active profile is deduped, not double-listed."""
        active_config = tmp_path / "active-account"
        (active_config / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", active_config / "projects")
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{active_config}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        args = argparse.Namespace(config_dir=None)
        assert _mod._resolve_scan_roots(args) == [active_config / "projects"]


class TestRedactionOrdinalStability:
    """_redaction_ordinals assigns the same ordinal to the same physical
    root regardless of which profile is active (list order) -- guards
    against the bug a local-sort-inside-_build_redact_map approach would
    reintroduce."""

    def test_same_two_roots_different_scan_order_yield_identical_ordinals(self, tmp_path):
        root_a = tmp_path / "acct-a"
        root_a.mkdir()
        root_b = tmp_path / "acct-b"
        root_b.mkdir()

        ordinals_a_first = _mod._redaction_ordinals([root_a, root_b])
        ordinals_b_first = _mod._redaction_ordinals([root_b, root_a])

        assert ordinals_a_first == ordinals_b_first
        assert ordinals_a_first[root_a.resolve()] == ordinals_b_first[root_a.resolve()]
        assert ordinals_a_first[root_b.resolve()] == ordinals_b_first[root_b.resolve()]

    def test_cost_report_assigns_same_account_label_regardless_of_which_profile_is_active(
        self, tmp_path, capsys
    ):
        """End-to-end version of the ordinal-stability fix: the same physical
        root reads as the same account-N whether it's scanned as the active
        profile (scanned first) or as a declared extra (scanned second)."""
        root_alpha = _write_cost_root(tmp_path, "acct-alpha", "-home-user-repo-alpha", "sess-alpha",
                                       [_priced("claude-sonnet-5", input=1_000_000)])
        root_zulu = _write_cost_root(tmp_path, "acct-zulu", "-home-user-repo-zulu", "sess-zulu",
                                      [_priced("claude-sonnet-5", input=1_000_000)])

        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_alpha, root_zulu])
        out_alpha_active = capsys.readouterr().out

        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_zulu, root_alpha])
        out_zulu_active = capsys.readouterr().out

        # root_alpha resolves alphabetically first, so it holds account-1 in both
        # runs regardless of which one was scanned first (which is active).
        assert "account-1/private-project-1" in out_alpha_active
        assert "account-2/private-project-1" in out_alpha_active
        assert "account-1/private-project-1" in out_zulu_active
        assert "account-2/private-project-1" in out_zulu_active


class TestCorpusFingerprintRootRemovalLimits:
    """_corpus_fingerprint hashes the raw-label SET, not root count --
    removing a root whose labels are a strict subset of a surviving root's
    own labels is invisible to the fingerprint, by design, not by bug."""

    def test_fingerprint_unchanged_when_removed_roots_labels_are_a_subset_of_a_surviving_root(self):
        two_root_map = {
            (0, "shared-label"): "account-1/private-project-1",
            (1, "shared-label"): "account-2/private-project-1",
        }
        one_root_map = {"shared-label": "private-project-1"}
        # Root 2's only label ("shared-label") is a strict subset of root 1's
        # own label set, so dropping root 2 does not change the label SET —
        # the fingerprint is identical even though the account count changed.
        assert _mod._corpus_fingerprint(two_root_map) == _mod._corpus_fingerprint(one_root_map)


class TestResolvedScopeHeaderDirectUnit:
    """Direct unit coverage of _resolved_scope_header's pure string
    formatting — the many end-to-end subcommand tests exercise this function
    only through a full report run; these pin its exact return value at each
    root-count branch."""

    def test_one_root_states_no_declared_roots_file(self, tmp_path):
        header = _mod._resolved_scope_header("buckets", "*", [tmp_path / "acct-a" / "projects"])
        assert header == (
            "BUCKETS SOURCES (*; 1 root (no ~/.claude/transcript-config-dirs declared))"
        )

    def test_n_roots_states_the_count(self, tmp_path):
        roots = [
            tmp_path / "acct-a" / "projects",
            tmp_path / "acct-b" / "projects",
            tmp_path / "acct-c" / "projects",
        ]
        header = _mod._resolved_scope_header("cost", "this repo (2 project dirs)", roots)
        assert header == "COST SOURCES (this repo (2 project dirs); 3 roots)"


class TestRootCountDescDirectUnit:
    """Mechanism 2: _root_count_desc's one-root branch must distinguish the
    roots file being absent from it being present but contributing no
    additional root -- claiming "no ... declared" about a file that exists
    would misrepresent it."""

    def test_one_root_absent_roots_file_states_no_declared_roots_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(tmp_path / "does-not-exist"))
        assert _mod.scope._root_count_desc([tmp_path / "acct-a" / "projects"]) == (
            "1 root (no ~/.claude/transcript-config-dirs declared)"
        )

    def test_one_root_present_but_additive_nothing_does_not_claim_undeclared(self, tmp_path, monkeypatch):
        """The populated-but-additive-nothing case: the roots file exists
        (comments-only, here) but every line was skipped, so exactly one
        root is in scope -- the text must not claim the file is undeclared."""
        roots_file = tmp_path / "roots"
        roots_file.write_text("# just a comment\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
        desc = _mod.scope._root_count_desc([tmp_path / "acct-a" / "projects"])
        assert "no ~/.claude/transcript-config-dirs declared" not in desc
        assert "~/.claude/transcript-config-dirs" in desc

    def test_one_root_unreadable_roots_file_states_unreadable_not_declared(self, tmp_path, monkeypatch):
        """A directory at the roots-file path raises OSError on read --
        "unreadable", not "absent", and distinct from "present" (a failed
        read never honored any declarations, so wording it as "declared"
        would misrepresent the read that never happened); see
        declared_roots_file_state()'s own docstring for why a directory (not
        chmod) simulates this without silently degrading under a
        root-executing test runner."""
        roots_file_as_dir = tmp_path / "roots-is-a-directory"
        roots_file_as_dir.mkdir()
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file_as_dir))
        assert _mod.scope._root_count_desc([tmp_path / "acct-a" / "projects"]) == (
            "1 root (~/.claude/transcript-config-dirs present but unreadable)"
        )

    def test_multi_root_never_names_the_roots_file(self, tmp_path, monkeypatch):
        """Extra roots may come from --config-dir, not only a declared-roots
        file, so the >1-root branch must not name the file at all."""
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(tmp_path / "does-not-exist"))
        roots = [tmp_path / "acct-a" / "projects", tmp_path / "acct-b" / "projects"]
        assert _mod.scope._root_count_desc(roots) == "2 roots"
        assert "transcript-config-dirs" not in _mod.scope._root_count_desc(roots)

    def test_absent_state_literal_appears_in_both_skill_files(self, tmp_path, monkeypatch):
        """Contract test, a tripwire not a full guarantee: derives
        _root_count_desc()'s absent-state literal and asserts it appears in
        both transcript-analysis/SKILL.md and transcript-narrative/SKILL.md
        source text. This only catches the rendered literal drifting out of
        sync with what the skills quote verbatim -- each skill's own
        verbatim positive pin on its own scope-confirmation sentence (see
        each skill's dedicated tests) is the real contract."""
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(tmp_path / "does-not-exist"))
        literal = _mod.scope._root_count_desc([tmp_path / "acct-a" / "projects"])
        for skill_name in ("transcript-analysis", "transcript-narrative"):
            skill_text = (SKILLS_DIR / skill_name / "SKILL.md").read_text()
            assert literal in skill_text, f"{skill_name}/SKILL.md does not quote _root_count_desc()'s literal"


class TestSkillFilesReportObservedScopeNotUnionGuarantee:
    """Mechanism 6: transcript-narrative/SKILL.md and transcript-analysis/SKILL.md
    must instruct the reader to record the *observed* scope rather than assert
    a multi-account union/unconditional-header guarantee that --summary makes
    false. Each positive pin is the real contract (source-scanning a SKILL.md
    is acceptable here -- it has no executable form to run, unlike the .py
    files this module tests elsewhere). Each negative-grep pin is a tripwire
    only: it catches the exact old sentence being re-added verbatim, not a
    reworded regression that reintroduces the same false claim in different
    words."""

    def test_transcript_narrative_names_the_sessions_sources_line_to_record(self):
        skill_text = (SKILLS_DIR / "transcript-narrative" / "SKILL.md").read_text()
        assert (
            "`sessions --paths` prints its resolved-scope header (`SESSIONS SOURCES (...)`) to stderr"
            " — record that line."
        ) in skill_text

    def test_transcript_narrative_no_longer_asserts_every_declared_account_union(self):
        skill_text = (SKILLS_DIR / "transcript-narrative" / "SKILL.md").read_text()
        assert "every declared account, not just the active one — and read only those files" not in skill_text
        assert "resolved against every declared account (`~/.claude/transcript-config-dirs`)" not in skill_text

    def test_transcript_analysis_names_the_summary_scope_line_as_the_carrier(self):
        skill_text = (SKILLS_DIR / "transcript-analysis" / "SKILL.md").read_text()
        assert "`cost --summary` prints no resolved-scope header" in skill_text
        assert "on its own `Scope:` line" in skill_text

    def test_transcript_analysis_no_longer_claims_the_header_is_unconditional_for_every_subcommand(self):
        skill_text = (SKILLS_DIR / "transcript-analysis" / "SKILL.md").read_text()
        assert (
            "not just the active profile. The resolved-scope header states the root count unconditionally,"
            " even at one root with nothing declared — see \"Scope confirmation\" above."
        ) not in skill_text


class TestBuildRedactMapDirectUnit:
    """Direct unit coverage of _build_redact_map's multi-root ordinal-
    assignment math — the many end-to-end subcommand tests exercise this
    function only through a full report run; this pins its returned dict's
    keys/values directly when the same raw project label collides across two
    declared roots."""

    def test_two_roots_with_colliding_raw_label_map_to_distinct_account_scoped_keys(self, tmp_path):
        root_a = tmp_path / "acct-a" / "projects"
        proj_a = root_a / "-home-user-testrepo"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [_asst("claude-sonnet-4-6", branch="from-a")])

        root_b = tmp_path / "acct-b" / "projects"
        proj_b = root_b / "-home-user-testrepo"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [_asst("claude-sonnet-4-6", branch="from-b")])

        ordinals = _mod._redaction_ordinals([root_a, root_b])
        ordinal_a, ordinal_b = ordinals[root_a.resolve()], ordinals[root_b.resolve()]

        redact_map = _mod._build_redact_map([root_a, root_b])

        assert redact_map == {
            (ordinal_a, "testrepo"): f"account-{ordinal_a}/private-project-1",
            (ordinal_b, "testrepo"): f"account-{ordinal_b}/private-project-1",
        }

    def test_subagent_only_project_with_unpriced_turns_still_shifts_sibling_ordinal(self, tmp_path):
        """A project whose main .jsonl is empty and whose only subagent turn
        carries no priced usage is still included in the label census
        (iter_sessions only requires a non-empty records list, not priced
        usage) — shifting a sorted-later priced sibling's ordinal even
        though the phantom project itself is never looked up
        (_cost_report's `if session_total:` gate skips zero-total
        sessions)."""
        root = tmp_path / "acct-a"
        phantom = root / "-home-user-aaa-phantom"
        phantom.mkdir(parents=True)
        _write_jsonl(phantom / "sess-phantom.jsonl", [])
        _write_subagent_jsonl(phantom, "sess-phantom", "agent-1", [_user_msg("hi")])

        priced = root / "-home-user-zzz-priced"
        priced.mkdir(parents=True)
        _write_jsonl(priced / "sess-priced.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])

        redact_map = _mod._build_redact_map([root])
        assert redact_map["zzz-priced"] == "private-project-2"

    def test_two_subagent_only_projects_get_distinct_sorted_labels(self, tmp_path):
        """Sibling of the single-phantom ordinal-shift case above: two
        projects visible only through subagent transcripts still get
        distinct labels in sorted order, not a collision or a shared slot."""
        root = tmp_path / "acct-a"
        proj_a = root / "-home-user-aaa-phantom"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [])
        _write_subagent_jsonl(proj_a, "sess-a", "agent-1", [_user_msg("hi")])

        proj_b = root / "-home-user-zzz-phantom"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [])
        _write_subagent_jsonl(proj_b, "sess-b", "agent-1", [_user_msg("hi")])

        redact_map = _mod._build_redact_map([root])
        assert redact_map == {
            "aaa-phantom": "private-project-1",
            "zzz-phantom": "private-project-2",
        }


# ---------------------------------------------------------------------------
# cache_miss_reason (pricing.py)
# ---------------------------------------------------------------------------


class TestCacheMissReason:
    """_cache_miss_reason parses message.diagnostics.cache_miss_reason."""

    def test_returns_reason_when_present(self):
        message = {
            "diagnostics": {
                "cache_miss_reason": {"type": "model_changed", "cache_missed_input_tokens": 12345},
            }
        }
        assert _mod._cache_miss_reason(message) == "model_changed"

    def test_returns_none_when_diagnostics_absent(self):
        assert _mod._cache_miss_reason({}) is None

    def test_returns_none_when_diagnostics_not_a_dict(self):
        assert _mod._cache_miss_reason({"diagnostics": "oops"}) is None

    def test_returns_none_when_cache_miss_reason_absent(self):
        assert _mod._cache_miss_reason({"diagnostics": {}}) is None

    def test_returns_none_when_cache_miss_reason_not_a_dict(self):
        assert _mod._cache_miss_reason({"diagnostics": {"cache_miss_reason": 42}}) is None

    def test_returns_none_when_cache_miss_reason_type_not_a_string(self):
        message = {"diagnostics": {"cache_miss_reason": {"type": 42}}}
        assert _mod._cache_miss_reason(message) is None

    def test_previous_message_not_found_reason_has_no_cache_missed_input_tokens_field(self):
        """The previous_message_not_found variant carries no
        cache_missed_input_tokens field at all -- the parser must not assume
        its presence."""
        message = {"diagnostics": {"cache_miss_reason": {"type": "previous_message_not_found"}}}
        assert _mod._cache_miss_reason(message) == "previous_message_not_found"

    def test_unavailable_reason_has_no_cache_missed_input_tokens_field(self):
        """The unavailable variant also carries no cache_missed_input_tokens
        field -- the parser must not assume its presence."""
        message = {"diagnostics": {"cache_miss_reason": {"type": "unavailable"}}}
        assert _mod._cache_miss_reason(message) == "unavailable"

    def test_merged_run_takes_cache_miss_reason_from_first_record(self):
        """_merge_assistant_run takes every non-content, non-usage field from
        a requestId run's first record unchanged -- a merged turn's own
        diagnostics survives with no extra collapsing logic added for it."""
        rec_a = _priced("claude-opus-5", output=10, request_id="req-1")
        rec_a["message"]["diagnostics"] = {
            "cache_miss_reason": {"type": "model_changed", "cache_missed_input_tokens": 999},
        }
        rec_b = _priced("claude-opus-5", output=20, request_id="req-1")
        rec_b["message"]["diagnostics"] = {"cache_miss_reason": {"type": "something_else"}}
        merged = _mod._dedup_turns_by_request_id([rec_a, rec_b])
        assert len(merged) == 1
        assert _mod._cache_miss_reason(merged[0]["message"]) == "model_changed"


# ---------------------------------------------------------------------------
# per-record --branches consumers: pre-branch and subagent attribution
# ---------------------------------------------------------------------------


class TestPerRecordConsumersCreditPrefixToTheBranchEntered:
    """A session that opens on the default branch and then enters a feature branch:
    each shim subcommand credits the leading default-branch records to that branch
    (docs/transcript-analysis.md, "Pre-branch default-branch attribution")."""

    def test_buckets_date_range_starts_at_prefix_and_main_row_counts_drop(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst(_SONNET, branch="main", ts="2026-06-01T10:00:00.000Z"),
            _asst(_SONNET, branch="feature-x", ts="2026-06-02T10:00:00.000Z"),
            _asst(_SONNET, branch="feature-x", ts="2026-06-02T10:05:00.000Z"),
            _asst(_SONNET, branch="main", ts="2026-06-03T10:00:00.000Z"),
        ])
        # Never returns to main, so its main row membership moves entirely to the branch.
        _write_jsonl(fake_projects / "sess-no-return.jsonl", [
            _asst(_SONNET, branch="main", ts="2026-06-01T11:00:00.000Z"),
            _asst(_SONNET, branch="feature-x", ts="2026-06-02T11:00:00.000Z"),
        ])
        _mod.cmd_buckets(_branch_args())
        out = capsys.readouterr().out
        feature_cols = _table_cols(out, header_contains="Branch", row_contains="feature-x", max_labels=8)
        main_cols = _table_cols(out, header_contains="Branch", row_contains="main", max_labels=8)
        assert feature_cols["Total"] == "5"  # each session's prefix turn plus its turns on the branch
        assert feature_cols["Sess"] == "2"
        assert main_cols["Total"] == "1"  # only the turn after the boundary stays
        assert main_cols["Sess"] == "1"
        assert "2026-06-01..2026-06-02" in out
        assert "2026-06-03..2026-06-03" in out

    def test_buckets_branch_filter_keeps_the_prefix_turn(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst(_SONNET, branch="main", ts=_attr_ts(1)),
            _asst(_SONNET, branch="feature-x", ts=_attr_ts(5)),
        ])
        _mod.cmd_buckets(_branch_args(branches="feature-x"))
        out = capsys.readouterr().out
        assert _table_cols(out, header_contains="Branch", row_contains="feature-x", max_labels=8)["Total"] == "2"

    def test_duration_span_includes_the_prefix(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst(_SONNET, branch="main", ts=_attr_ts(0)),
            _asst(_SONNET, branch="feature-x", ts=_attr_ts(5)),
            _asst(_SONNET, branch="feature-x", ts=_attr_ts(10)),
        ])
        _mod.cmd_duration(_branch_args(branches="feature-x"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Span(min)", row_contains="feature-x")
        assert cols["Span(min)"] == "10"  # 12:00 to 12:10, not 12:05 to 12:10
        assert cols["Active(min)"] == "10"

    def test_struggle_counts_a_prefix_correction_under_the_branch_entered(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", ts=_attr_ts(1)),
            _user_msg([{"type": "text", "text": "this is the wrong approach"}], branch="main", ts=_attr_ts(2)),
            _asst(_SONNET, branch="feature-x", ts=_attr_ts(5)),
        ])
        _mod.cmd_struggle(_branch_args(branches="feature-x"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Branch", row_contains="feature-x")
        assert cols["Opus"] == "1"

    def test_user_input_unfiltered_branchless_prompt_keeps_an_empty_label(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user("a prompt recorded with no branch", branch="", ts=_attr_ts(1)),
            _asst(_SONNET, branch="", ts=_attr_ts(2)),
        ])
        _mod.cmd_user_input(_user_input_args())
        out = capsys.readouterr().out
        assert "a prompt recorded with no branch" in out
        assert "None" not in out
        assert re.search(r"^### \S+ ·  · \d{4}-\d{2}-\d{2}$", out, re.MULTILINE)

    def test_user_input_branch_filter_counts_the_prefix_first_prompt_as_the_initial_prompt(
        self, fake_projects, capsys
    ):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user("prefix planning prompt", branch="main", ts=_attr_ts(1)),
            _asst(_SONNET, branch="main", ts=_attr_ts(2)),
            _ui_user("prompt after entering the branch", branch="feature-x", ts=_attr_ts(5)),
            _asst(_SONNET, branch="feature-x", ts=_attr_ts(6)),
        ])
        _mod.cmd_user_input(_user_input_args(branches="feature-x"))
        out = capsys.readouterr().out
        assert re.search(r"INITIAL · \w+\]\*\*\n~~~text\nprefix planning prompt\n~~~", out)
        assert re.search(r"FOLLOWUP · \w+\]\*\*\n~~~text\nprompt after entering the branch\n~~~", out)

    def test_judgment_pair_prints_the_attributed_branch_of_a_prefix_invocation(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", _attr_ts(1), branch="main"),
            _review_asst("Review text recorded before the branch existed.", _attr_ts(2), branch="main"),
            _user_reply("Acknowledged.", branch="main"),
            _review_asst("Work on the branch.", _attr_ts(5), branch="feature-x"),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args(branches="feature-x"))
        out = capsys.readouterr().out
        assert "Skill: code-review  branch=feature-x" in out

    def test_user_input_since_window_leaves_a_later_default_branch_prompt_on_the_default_branch(
        self, fake_projects, capsys
    ):
        """main -> X -> main -> Y with --since between X and the second main. The session
        context is built before the window filter; one built from in-window prompts would
        see the second main as the leading run and credit its prompt to Y."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user("prefix prompt", branch="main", ts="2026-06-01T10:00:00.000Z"),
            _ui_user("prompt on branch x", branch="feature-x", ts="2026-06-02T10:00:00.000Z"),
            _ui_user("later prompt on the default branch", branch="main", ts="2026-06-04T10:00:00.000Z"),
            _ui_user("prompt on branch y", branch="feature-y", ts="2026-06-06T10:00:00.000Z"),
        ])

        _mod.cmd_user_input(_user_input_args(branches="main", since="2026-06-03"))
        main_out = capsys.readouterr().out
        assert "later prompt on the default branch" in main_out
        _mod.cmd_user_input(_user_input_args(branches="feature-y", since="2026-06-03"))
        feature_y_out = capsys.readouterr().out
        assert "prompt on branch y" in feature_y_out
        assert "later prompt on the default branch" not in feature_y_out

    def test_judgment_pair_since_window_leaves_a_later_default_branch_invocation_on_the_default_branch(
        self, fake_projects, capsys
    ):
        """main -> X -> main -> Y with --since between X and the second main: the second
        main's invocation stays on main, as a context built from in-window records would
        not keep it."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-06-01T10:00:00.000Z", branch="main", tool_id="s1"),
            _review_asst("Review recorded before the branch existed.", "2026-06-01T10:01:00.000Z", branch="main"),
            _asst(_SONNET, branch="feature-x", ts="2026-06-02T10:00:00.000Z"),
            _skill_use_rec("code-review", "2026-06-04T10:00:00.000Z", branch="main", tool_id="s2"),
            _review_asst("Review recorded on the default branch later.", "2026-06-04T10:01:00.000Z", branch="main"),
            _asst(_SONNET, branch="feature-y", ts="2026-06-06T10:00:00.000Z"),
        ])

        _mod.cmd_judgment_pair(_judgment_pair_args(branches="main", since="2026-06-03"))
        main_out = capsys.readouterr().out
        assert "Skill: code-review  branch=main" in main_out
        assert "Review recorded on the default branch later." in main_out
        _mod.cmd_judgment_pair(_judgment_pair_args(branches="feature-y", since="2026-06-03"))
        assert "Review recorded on the default branch later." not in capsys.readouterr().out

    def test_pr_link_counts_the_prefix_turn_toward_the_branch_model_split(self, fake_projects, monkeypatch, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", ts=_attr_ts(1)),
            _asst(_SONNET, branch="feature-x", ts=_attr_ts(5)),
        ])

        def fake_run(cmd, *_, **__):
            class Completed:
                stdout = json.dumps([{"number": 77}]) if "list" in cmd else ""
                returncode = 0

            return Completed()

        monkeypatch.setattr(subprocess, "run", fake_run)
        _mod.cmd_pr_link(_branch_args(repo="owner/repo", branches="feature-x", author="alice"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Branch", row_contains="feature-x")
        assert (cols["Opus"], cols["Sonnet"]) == ("1", "1")


class TestSubagentRecordsFollowTheDispatchingBranch:
    """skill-invocation --include-subagents and skill-pair attribute every subagent
    record to the branch its dispatching main thread was on at the record's time."""

    @staticmethod
    def _run_skill_invocation(capsys, **kw) -> str:
        _mod.cmd_skill_invocation(_skill_inv_args(include_subagents=True, **kw))
        return capsys.readouterr().out

    def test_prefix_late_and_isolated_sidechain_invocations_count_under_the_branch_entered(
        self, fake_projects, capsys
    ):
        session_id = "sess-prefix"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst(_SONNET, branch="main", ts=_attr_ts(1)),
            _asst(_SONNET, branch="feature-x", ts=_attr_ts(5)),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst(_SONNET, branch="main", sidechain=True, ts=_attr_ts(2),
                  content=[_skill_use("p1", "plan-it")]),
            _asst(_SONNET, branch="main", sidechain=True, ts=_attr_ts(8),
                  content=[_skill_use("p2", "plan-review")]),
            _asst(_SONNET, branch="worktree-agent-abc123", sidechain=True, ts=_attr_ts(9),
                  content=[_skill_use("p3", "code-review")]),
        ])
        out = self._run_skill_invocation(capsys, branches="feature-x")
        for skill in ("plan-it", "plan-review", "code-review"):
            assert re.search(rf"^{skill}\s+sidechain", out, re.MULTILINE), f"{skill} missing: {out}"

    def test_sidechain_invocation_in_a_later_branch_stretch_counts_under_that_branch_whatever_its_tag(
        self, fake_projects, capsys
    ):
        session_id = "sess-switch"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst(_SONNET, branch="feature-x", ts=_attr_ts(1)),
            _asst(_SONNET, branch="feature-y", ts=_attr_ts(5)),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst(_SONNET, branch="feature-x", sidechain=True, ts=_attr_ts(8),
                  content=[_skill_use("s1", "code-review")]),
        ])
        under_y = self._run_skill_invocation(capsys, branches="feature-y")
        under_x = self._run_skill_invocation(capsys, branches="feature-x")
        assert "code-review" in under_y
        assert "No skill invocations found." in under_x

    def test_skill_pair_counts_isolated_and_foreign_tagged_sidechain_followers_under_the_branch_entered(
        self, fake_projects, capsys
    ):
        leader_use = [_skill_use("l1", "plan-it")]
        for session_id, follower_tag in (("sess-a", "worktree-agent-abc123"), ("sess-b", "unrelated-branch")):
            _write_jsonl(fake_projects / f"{session_id}.jsonl", [
                _asst(_SONNET, branch="main", ts=_attr_ts(1), content=leader_use),
                _asst(_SONNET, branch="feature-x", ts=_attr_ts(5)),
            ])
            _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
                _asst(_SONNET, branch=follower_tag, sidechain=True, ts=_attr_ts(8),
                      content=[_skill_use("f1", "plan-review")]),
            ])
        _mod.cmd_skill_pair(_skill_pair_args(branches="feature-x"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert (cols["Lead"], cols["Main"], cols["Side"]) == ("2", "0", "2")

    def test_foreign_cwd_sidechain_skill_name_counts_under_the_dispatching_branch(
        self, fake_projects, capsys
    ):
        """Pins a disclosed flow: a subagent dispatched into another repo has its skill
        names counted under the dispatching branch, so --branches X --include-subagents
        prints them whatever the subagent's own cwd (docs/transcript-analysis.md, Limits)."""
        session_id = "sess-cross-repo"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst(_SONNET, branch="feature-x", ts=_attr_ts(1)),
            _asst(_SONNET, branch="feature-x", ts=_attr_ts(5)),
        ])
        foreign_record = _asst(_SONNET, branch="other-repo-branch", sidechain=True, ts=_attr_ts(3),
                               content=[_skill_use("f1", "privateco-plugin:internal-runbook")])
        foreign_record["cwd"] = "/elsewhere/other-repo"
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [foreign_record])
        out = self._run_skill_invocation(capsys, branches="feature-x")
        assert re.search(r"^privateco-plugin:internal-runbook\s+sidechain", out, re.MULTILINE), out
