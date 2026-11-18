"""Tests for restore-authorization-boundary-on-compact.sh.

The hook is a SessionStart hook (matcher: compact) that emits
hookSpecificOutput.additionalContext restating the irreversible-action
authorization boundary — the harness-generated compact summary carries no
trace of it, and its final "Optional Next Step" section names no distinction
between a reversible step and one that merges a PR, force-pushes, or deletes
in bulk.

Output is emitted only when `.source == "compact"`; every other source
(including a missing/non-string one) produces no output. This is advisory
context, not a gate: it cannot be used with run_hook_session_start, which
asserts the exact {"hookEventName", "sessionTitle"} key set a sessionTitle
hook emits.
"""
from __future__ import annotations

import functools
import json
import re
from pathlib import Path

import pytest
from helpers import (
    AUTONOMOUS_SHIPPING_STATEMENT_LEAD,
    HOOKS_DIR,
    build_path_without,
    hook_beside_degraded_schema,
    init_git_repo_with_commit,
    install_recording_shims,
    run_additional_context_session_start_hook,
    session_start_additional_context,
    session_start_registered_timeouts,
)

RESTORE_BOUNDARY_HOOK = HOOKS_DIR / "restore-authorization-boundary-on-compact.sh"
_REPO_ROOT = Path(__file__).resolve().parents[4]
_HANDOFF_SKILL = _REPO_ROOT / "claude-skills" / "skills" / "handoff" / "SKILL.md"

# Literal command/verb tokens the hook's injected text names — anchored on
# the literal token rather than a descriptive clause so a prose reword does
# not false-fail this suite.
_NAMED_TOKENS = [
    "gh pr close",
    "git branch -d",
    "gh release create",
    "git push --force",
    "rm -rf",
]


_run_hook = functools.partial(run_additional_context_session_start_hook, RESTORE_BOUNDARY_HOOK)


class TestRestoreAuthorizationBoundaryOnCompact:
    def test_compact_source_emits_valid_json_with_principle_clause(self, isolated_home):
        """source: compact -> valid JSON payload whose additionalContext
        states the summary is a reconstruction, not authorization."""
        result = _run_hook({"source": "compact"}, isolated_home)
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        hook_output = parsed["hookSpecificOutput"]
        assert hook_output["hookEventName"] == "SessionStart"
        ctx = hook_output["additionalContext"]
        assert isinstance(ctx, str)
        assert "not engineer authorization" in ctx

    def test_emits_output_when_config_keys_psv_unreadable(self, isolated_home, tmp_path):
        """Cumulative-review finding: an unreadable config-keys.psv must not
        silently suppress this advisory restatement --
        authorization_boundary_restore's own safe direction is enforced (the
        boundary text still gets restated), unlike the accepted exit-2
        (config-dir-unresolvable) no-op this hook's own header already
        documents. Mirrors test_lib_config_gates.py's _lib_sh_with_unreadable_schema
        technique: symlink the hook and _lib.sh/_config.sh into a dir with
        no config-keys.psv sibling."""
        isolated_hook = hook_beside_degraded_schema(tmp_path, RESTORE_BOUNDARY_HOOK)
        result = run_additional_context_session_start_hook(isolated_hook, {"source": "compact"}, isolated_home)
        assert result.returncode == 0
        ctx = session_start_additional_context(result)
        assert "not engineer authorization" in ctx

    def test_emits_output_when_config_keys_psv_readable_but_missing_row(self, isolated_home, tmp_path):
        """A config-keys.psv that is readable and non-empty but missing
        authorization_boundary_restore's own row (the interrupted
        stow-relink/git-pull shape, distinct from the wholly-unreadable case
        above) must not silently suppress this advisory restatement either
        -- _config_enabled's exit 4 for this shape routes to the same
        stays-armed arm as exit 3. Copies the real config-keys.psv with
        only authorization_boundary_restore's own row removed."""
        isolated_hook = hook_beside_degraded_schema(
            tmp_path, RESTORE_BOUNDARY_HOOK, schema_without_key="authorization_boundary_restore"
        )
        result = run_additional_context_session_start_hook(isolated_hook, {"source": "compact"}, isolated_home)
        assert result.returncode == 0
        ctx = session_start_additional_context(result)
        assert "not engineer authorization" in ctx

    @pytest.mark.parametrize("token", _NAMED_TOKENS)
    def test_named_shape_token_present(self, isolated_home, token):
        """Each illustrative shape is named by its literal command/verb
        token, not only a descriptive clause."""
        result = _run_hook({"source": "compact"}, isolated_home)
        ctx = session_start_additional_context(result)
        assert token in ctx

    def test_gh_pr_merge_not_named(self, isolated_home):
        """gh pr merge is unconditionally gated by block-gh-pr-merge.sh, so
        this hook must not name it — a future edit pasting handoff/SKILL.md
        §3.5's full list in would violate that invariant silently without
        this assertion."""
        result = _run_hook({"source": "compact"}, isolated_home)
        ctx = session_start_additional_context(result)
        assert "gh pr merge" not in ctx

    def test_named_tokens_are_subset_of_handoff_skill_section_3_5(self, isolated_home):
        """Every command token the hook names must appear somewhere in
        handoff/SKILL.md's §3.5 categorization list — a subset check,
        deliberately not equality, since §3.5 may gain a shape that IS
        gated, which the hook should then correctly omit."""
        section_3_5 = _HANDOFF_SKILL.read_text()
        match = re.search(r"## §3\.5.*?(?=\n## §4)", section_3_5, re.DOTALL)
        assert match, "handoff/SKILL.md §3.5 section not found — has it been renamed?"
        section_3_5_text = match.group(0)
        for token in _NAMED_TOKENS:
            assert token in section_3_5_text, (
                f"hook names {token!r}, but it does not appear in handoff/SKILL.md §3.5 — "
                "the two lists have drifted apart"
            )

    @pytest.mark.parametrize("source", ["startup", "clear", "resume", "fork"])
    def test_non_compact_source_produces_no_output(self, isolated_home, source):
        """Every source other than compact stays silent — this covers the
        in-script filter independently of the settings.json matcher."""
        result = _run_hook({"source": source}, isolated_home)
        assert result.returncode == 0
        assert result.stdout == b""

    def test_missing_source_produces_no_output(self, isolated_home):
        result = _run_hook({}, isolated_home)
        assert result.returncode == 0
        assert result.stdout == b""

    @pytest.mark.parametrize("bad_source", [123, True, {"nested": "compact"}])
    def test_non_string_source_produces_no_output(self, isolated_home, bad_source):
        """A .source that parses but isn't a string (number, bool, object)
        is a distinct code path from malformed stdin — both must stay silent."""
        result = _run_hook({"source": bad_source}, isolated_home)
        assert result.returncode == 0
        assert result.stdout == b""

    def test_kill_switch_present_produces_no_output(self, isolated_home):
        marker_dir = isolated_home / ".claude"
        marker_dir.mkdir(parents=True, exist_ok=True)
        (marker_dir / ".authorization-boundary-disabled").touch()
        result = _run_hook({"source": "compact"}, isolated_home)
        assert result.returncode == 0
        assert result.stdout == b""

    def test_kill_switch_under_config_dir_produces_no_output(self, isolated_home, tmp_path):
        config_dir = tmp_path / "profile"
        config_dir.mkdir(parents=True)
        (config_dir / ".authorization-boundary-disabled").touch()
        result = _run_hook(
            {"source": "compact"},
            isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        )
        assert result.returncode == 0
        assert result.stdout == b""

    def test_legacy_home_kill_switch_ignored_when_config_dir_set(self, isolated_home, tmp_path):
        """Config-dir resolution is a swap, not a union: a kill switch at the
        legacy $HOME/.claude location produces no suppression once
        CLAUDE_CONFIG_DIR points elsewhere — the boundary text still emits."""
        marker_dir = isolated_home / ".claude"
        marker_dir.mkdir(parents=True, exist_ok=True)
        (marker_dir / ".authorization-boundary-disabled").touch()
        config_dir = tmp_path / "profile"
        config_dir.mkdir(parents=True)
        result = _run_hook(
            {"source": "compact"},
            isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        )
        assert result.returncode == 0
        ctx = session_start_additional_context(result)
        assert "not engineer authorization" in ctx

    def test_malformed_stdin_exits_0_with_no_output(self, isolated_home):
        result = _run_hook(None, isolated_home, raw_stdin=b"not json")
        assert result.returncode == 0
        assert result.stdout == b""

    def test_empty_stdin_exits_0_with_no_output(self, isolated_home):
        result = _run_hook(None, isolated_home, raw_stdin=b"")
        assert result.returncode == 0
        assert result.stdout == b""

    def test_missing_jq_exits_0_with_no_output(self, isolated_home, tmp_path):
        """A hook that cannot encode its output must fail open, not block
        session startup."""
        farm_dir = tmp_path / "path_without_jq"
        farm_dir.mkdir()
        path_without_jq = build_path_without("jq", farm_dir)
        result = _run_hook(
            {"source": "compact"}, isolated_home, extra_env={"PATH": path_without_jq}
        )
        assert result.returncode == 0
        assert result.stdout == b""

    def test_unresolvable_config_dir_exits_0_with_no_output(self, isolated_home):
        """A relative CLAUDE_CONFIG_DIR cannot be resolved to a kill-switch
        location, so the hook must fail open rather than probe a
        root-anchored path."""
        result = _run_hook(
            {"source": "compact"},
            isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": "relative/path"},
        )
        assert result.returncode == 0
        assert result.stdout == b""

    def test_exit_0_always(self, isolated_home):
        result = _run_hook({"source": "compact"}, isolated_home)
        assert result.returncode == 0


class TestShippingStatementOnCompact:
    """For an eligible `.cwd`, the compact hook appends the shared statement to the boundary text in one message.
    The statement depends only on the root guard, not on the boundary kill switch."""

    @staticmethod
    def _compact_payload(repo: Path | None) -> dict:
        payload: dict = {"source": "compact"}
        if repo is not None:
            payload["cwd"] = str(repo)
        return payload

    def test_eligible_compact_is_the_boundary_text_then_one_space_then_the_statement(
        self, isolated_home, shipping_repo, shipping_statement
    ):
        boundary_text = session_start_additional_context(
            _run_hook(self._compact_payload(None), isolated_home)
        )
        result = _run_hook(self._compact_payload(shipping_repo), isolated_home)
        assert result.returncode == 0
        ctx = session_start_additional_context(result)
        assert "not engineer authorization" in boundary_text
        assert ctx == f"{boundary_text} {shipping_statement}"
        assert "gh pr merge" not in ctx

    def test_kill_switch_silences_only_the_boundary_text(
        self, isolated_home, shipping_repo, shipping_statement
    ):
        (isolated_home / ".claude" / ".authorization-boundary-disabled").touch()
        result = _run_hook(self._compact_payload(shipping_repo), isolated_home)
        assert result.returncode == 0
        assert session_start_additional_context(result) == shipping_statement

    def test_kill_switch_with_empty_statement_part_is_silent(self, isolated_home, non_repo_dir):
        (isolated_home / ".claude" / ".authorization-boundary-disabled").touch()
        result = _run_hook(self._compact_payload(non_repo_dir), isolated_home)
        assert result.returncode == 0
        assert result.stdout == b""

    def test_cwd_outside_a_repo_leaves_the_boundary_text_alone(self, isolated_home, non_repo_dir):
        result = _run_hook(self._compact_payload(non_repo_dir), isolated_home)
        ctx = session_start_additional_context(result)
        assert "not engineer authorization" in ctx
        assert AUTONOMOUS_SHIPPING_STATEMENT_LEAD not in ctx

    def test_cwd_containing_a_newline_gets_the_statement_even_though_the_stop_hook_stays_silent(
        self, isolated_home, tmp_path, shipping_statement
    ):
        """The compact hook hands a multi-line `.cwd` to the root guard unfiltered, unlike the Stop hook
        (test_cwd_containing_a_newline_fails_closed), so a repo whose directory name holds a newline is eligible here."""
        newline_repo = tmp_path / "shipping\nrepo"
        try:
            init_git_repo_with_commit(newline_repo, branch="main")
        except OSError as filesystem_error:
            pytest.skip(f"filesystem cannot hold a newline in a directory name: {filesystem_error}")
        boundary_text = session_start_additional_context(
            _run_hook(self._compact_payload(None), isolated_home)
        )

        result = _run_hook(self._compact_payload(newline_repo), isolated_home)

        assert result.returncode == 0
        assert session_start_additional_context(result) == f"{boundary_text} {shipping_statement}"

    def test_missing_cwd_leaves_the_boundary_text_alone(self, isolated_home, shipping_repo):
        """The process cwd is an eligible repo, so a `$PWD` fallback would append the statement."""
        result = _run_hook(self._compact_payload(None), isolated_home, process_cwd=shipping_repo)
        ctx = session_start_additional_context(result)
        assert "not engineer authorization" in ctx
        assert AUTONOMOUS_SHIPPING_STATEMENT_LEAD not in ctx

    @pytest.mark.parametrize("bad_cwd", [42, ["/tmp"]], ids=["number", "array"])
    def test_non_string_payload_cwd_leaves_the_boundary_text_alone_even_when_process_cwd_is_an_eligible_repo(
        self, isolated_home, shipping_repo, bad_cwd
    ):
        """Pins end behavior, not the `(.cwd | type) == "string"` guard, because the root guard rejects both values
        with or without it. Missing, empty and relative cwd are rows of test_autonomous_shipping_grant_boundary.py."""
        result = _run_hook({"source": "compact", "cwd": bad_cwd}, isolated_home, process_cwd=shipping_repo)
        ctx = session_start_additional_context(result)
        assert "not engineer authorization" in ctx
        assert AUTONOMOUS_SHIPPING_STATEMENT_LEAD not in ctx

    def test_git_absent_from_path_still_emits_the_boundary_text(self, isolated_home, shipping_repo, tmp_path):
        """No git means no root resolves, so the statement drops while the boundary text stays."""
        path_farm = tmp_path / "path-without-git"
        path_farm.mkdir()
        result = _run_hook(
            self._compact_payload(shipping_repo),
            isolated_home,
            extra_env={"PATH": build_path_without("git", path_farm)},
        )
        assert result.returncode == 0
        ctx = session_start_additional_context(result)
        assert "not engineer authorization" in ctx
        assert AUTONOMOUS_SHIPPING_STATEMENT_LEAD not in ctx

    def test_relocated_config_dir_appends_the_statement(self, isolated_home, shipping_repo, tmp_path):
        config_dir = tmp_path / "profile"
        config_dir.mkdir()
        result = _run_hook(
            self._compact_payload(shipping_repo),
            isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        )
        ctx = session_start_additional_context(result)
        assert "not engineer authorization" in ctx
        assert AUTONOMOUS_SHIPPING_STATEMENT_LEAD in ctx

    def test_relative_config_dir_emits_the_statement_alone_with_no_boundary_text(
        self, isolated_home, shipping_repo, shipping_statement
    ):
        """An unresolvable config dir skips the boundary text, and the statement path reads no config."""
        result = _run_hook(
            self._compact_payload(shipping_repo),
            isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": "relative/path"},
        )
        assert result.returncode == 0
        assert session_start_additional_context(result) == shipping_statement

    @pytest.mark.parametrize(
        "schema_without_key", [None, "authorization_boundary_restore"], ids=["schema-absent", "row-missing"]
    )
    def test_degraded_schema_keeps_both_the_boundary_text_and_the_statement(
        self, isolated_home, shipping_repo, tmp_path, schema_without_key
    ):
        """The boundary text stays armed on a torn config-keys.psv, and the statement path reads no config."""
        degraded_hook = hook_beside_degraded_schema(
            tmp_path, RESTORE_BOUNDARY_HOOK, schema_without_key=schema_without_key
        )
        result = run_additional_context_session_start_hook(
            degraded_hook, self._compact_payload(shipping_repo), isolated_home
        )
        assert result.returncode == 0
        ctx = session_start_additional_context(result)
        assert "not engineer authorization" in ctx
        assert AUTONOMOUS_SHIPPING_STATEMENT_LEAD in ctx

    @pytest.mark.parametrize("source", ["startup", "clear", "resume", "fork"])
    def test_non_compact_sources_stay_silent(self, isolated_home, shipping_repo, source):
        """The hook emits only for `.source == "compact"`, whatever the `.cwd` names."""
        result = _run_hook({"source": source, "cwd": str(shipping_repo)}, isolated_home)
        assert result.returncode == 0
        assert result.stdout == b""

    def test_non_compact_source_spawns_one_jq_and_no_git(self, isolated_home, shipping_repo, tmp_path):
        """The `.source` read is the only spawn on a non-compact event, so a session start never pays for git."""
        shimmed_path, invocation_log = install_recording_shims(tmp_path)
        result = _run_hook(
            {"source": "startup", "cwd": str(shipping_repo)},
            isolated_home,
            extra_env={"PATH": shimmed_path},
        )
        assert result.returncode == 0
        assert result.stdout == b""
        assert invocation_log.read_text().split() == ["jq"]

    def test_shims_record_git_and_jq_spawns_on_a_compact_event_for_an_eligible_cwd(
        self, isolated_home, shipping_repo, tmp_path
    ):
        """Positive control: proves the shims detect spawns, so the non-compact run's lone jq entry means no git ran."""
        shimmed_path, invocation_log = install_recording_shims(tmp_path)
        result = _run_hook(
            self._compact_payload(shipping_repo),
            isolated_home,
            extra_env={"PATH": shimmed_path},
        )
        assert result.returncode == 0
        assert result.stdout != b""
        assert set(invocation_log.read_text().split()) == {"git", "jq"}


class TestRestoreAuthorizationBoundaryRegistration:
    def test_registered_with_a_ten_second_timeout(self):
        """The harness default is 600 s (docs/hooks.md § "Gate deadlock recovery"), so the registered cap is the binding bound."""
        assert session_start_registered_timeouts("restore-authorization-boundary-on-compact.sh") == [10]
