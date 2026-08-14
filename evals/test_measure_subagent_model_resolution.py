"""Unit tests for evals/measure_subagent_model_resolution.py.

All deterministic — no `claude -p` call. Fixture-based, using synthetic
`subagents/*.meta.json` + paired `*.jsonl` pairs written under `tmp_path`,
mirroring the real on-disk shape confirmed in
claude/.claude/scripts/tests/test_transcript_analysis.py's
`_write_subagent_dispatch` helper. Lives beside the harness rather than in
claude/.claude/tests/ — that directory is stowed to every consumer of this
repo, and this harness imports a module (measure_subagent_model_resolution)
that is never stowed. See
.claude/plans/plan-mode-model-resolution-experiment.md's "Why the test does
not go in claude/.claude/tests/" note for the full rationale.

pyproject.toml adds 'evals' to the pytest pythonpath so `import
measure_subagent_model_resolution` resolves correctly.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import measure_subagent_model_resolution as msmr
import pytest


def _write_meta(
    subagent_dir: Path, agent_id: str, tool_use_id: str, *,
    agent_type: str = "staff-backend-engineer", requested_model: str | None = None,
) -> None:
    """Write one subagents/<agent_id>.meta.json, matching the real on-disk
    shape: {"agentType", "description", "toolUseId", "spawnDepth"}, plus
    "model" when the dispatch carried an explicit per-invocation param."""
    subagent_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "agentType": agent_type, "description": "task", "toolUseId": tool_use_id, "spawnDepth": 1,
    }
    if requested_model is not None:
        meta["model"] = requested_model
    (subagent_dir / f"{agent_id}.meta.json").write_text(json.dumps(meta))


def _assistant_record(model: str | None, tool_names: list[str] | None = None) -> dict:
    content: list[dict] = [
        {"type": "tool_use", "name": name, "input": {}} for name in (tool_names or [])
    ]
    content.append({"type": "text", "text": "done"})
    message: dict = {"content": content}
    if model is not None:
        message["model"] = model
    return {"type": "assistant", "message": message}


def _write_subagent_jsonl(subagent_dir: Path, agent_id: str, records: list[dict]) -> None:
    subagent_dir.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(r) for r in records) + "\n"
    (subagent_dir / f"{agent_id}.jsonl").write_text(text)


def _session_jsonl_path(tmp_path: Path, session_id: str = "session-1") -> Path:
    """A session_jsonl path whose parent/stem parse_subagent_dispatches derives
    its subagent dir from — the file itself need not exist."""
    return tmp_path / f"{session_id}.jsonl"


class TestParseSubagentDispatches:
    def test_agent_ran_as_requested(self, tmp_path: Path) -> None:
        """Observed toolset is a subset of the requested agent's own declared
        tools -> AGENT_IDENTITY_REQUESTED, observed model family sonnet."""
        session_jsonl = _session_jsonl_path(tmp_path)
        subagent_dir = session_jsonl.parent / session_jsonl.stem / msmr.SUBAGENT_SUBDIR
        _write_meta(subagent_dir, "agent-1", "toolu_1", agent_type="Explore")
        _write_subagent_jsonl(subagent_dir, "agent-1", [
            _assistant_record("claude-sonnet-5", ["Read", "Grep"]),
        ])

        result = msmr.parse_subagent_dispatches(
            session_jsonl, requested_agent_declared_tools=frozenset({"Read", "Grep", "Glob"})
        )

        assert len(result) == 1
        obs = result[0]
        assert obs.agent_identity == msmr.AGENT_IDENTITY_REQUESTED
        assert obs.observed_model_family == msmr.MODEL_FAMILY_SONNET
        assert obs.sidecar_missing is False
        assert obs.requested_agent_type == "Explore"

    def test_agent_substituted_to_plan(self, tmp_path: Path) -> None:
        """A Bash call from a dispatch that requested a Bash-less agent (the
        real Explore.md declares only Read/Grep/Glob) is evidence of
        substitution to the built-in Plan agent — M3 detection method (b)."""
        session_jsonl = _session_jsonl_path(tmp_path)
        subagent_dir = session_jsonl.parent / session_jsonl.stem / msmr.SUBAGENT_SUBDIR
        _write_meta(subagent_dir, "agent-1", "toolu_1", agent_type="Explore")
        _write_subagent_jsonl(subagent_dir, "agent-1", [
            _assistant_record("claude-opus-4-7", ["Read", "Bash"]),
        ])

        result = msmr.parse_subagent_dispatches(
            session_jsonl, requested_agent_declared_tools=frozenset({"Read", "Grep", "Glob"})
        )

        assert result[0].agent_identity == msmr.AGENT_IDENTITY_SUBSTITUTED_TO_PLAN
        assert result[0].observed_model_family == msmr.MODEL_FAMILY_OPUS

    def test_missing_sidecar_jsonl(self, tmp_path: Path) -> None:
        """meta.json exists with no paired .jsonl on disk — a dropped or
        still-in-flight dispatch, not a parser crash."""
        session_jsonl = _session_jsonl_path(tmp_path)
        subagent_dir = session_jsonl.parent / session_jsonl.stem / msmr.SUBAGENT_SUBDIR
        _write_meta(subagent_dir, "agent-1", "toolu_1")

        result = msmr.parse_subagent_dispatches(session_jsonl, requested_agent_declared_tools=None)

        assert len(result) == 1
        assert result[0].sidecar_missing is True
        assert result[0].observed_model_family is None
        assert result[0].agent_identity == msmr.AGENT_IDENTITY_UNKNOWN

    def test_missing_message_model(self, tmp_path: Path) -> None:
        """The paired .jsonl exists and has assistant records, but none carry
        a .message.model field — distinct from a missing sidecar entirely."""
        session_jsonl = _session_jsonl_path(tmp_path)
        subagent_dir = session_jsonl.parent / session_jsonl.stem / msmr.SUBAGENT_SUBDIR
        _write_meta(subagent_dir, "agent-1", "toolu_1")
        _write_subagent_jsonl(subagent_dir, "agent-1", [_assistant_record(None, ["Read"])])

        result = msmr.parse_subagent_dispatches(session_jsonl, requested_agent_declared_tools=None)

        assert result[0].sidecar_missing is False
        assert result[0].observed_model_ids == frozenset()
        assert result[0].observed_model_family is None

    def test_mixed_model_dispatch(self, tmp_path: Path) -> None:
        """Two distinct real model IDs across the dispatch's own turns bucket
        to 'mixed', never collapsed into either one."""
        session_jsonl = _session_jsonl_path(tmp_path)
        subagent_dir = session_jsonl.parent / session_jsonl.stem / msmr.SUBAGENT_SUBDIR
        _write_meta(subagent_dir, "agent-1", "toolu_1")
        _write_subagent_jsonl(subagent_dir, "agent-1", [
            _assistant_record("claude-sonnet-5"),
            _assistant_record("claude-opus-4-7"),
        ])

        result = msmr.parse_subagent_dispatches(session_jsonl, requested_agent_declared_tools=None)

        assert result[0].observed_model_family == msmr.MODEL_FAMILY_MIXED
        assert result[0].observed_model_ids == frozenset({"claude-sonnet-5", "claude-opus-4-7"})

    def test_no_subagent_dir_returns_empty(self, tmp_path: Path) -> None:
        """No subagents/ directory at all (e.g. the dispatch never happened) ->
        empty tuple, not an error."""
        session_jsonl = _session_jsonl_path(tmp_path)
        assert msmr.parse_subagent_dispatches(session_jsonl, requested_agent_declared_tools=None) == ()

    def test_unreadable_meta_json_skipped(self, tmp_path: Path) -> None:
        """Invalid JSON in a *.meta.json file is skipped, not raised —
        mirrors _index_subagent_dispatches's meta_read_errors exclusion."""
        session_jsonl = _session_jsonl_path(tmp_path)
        subagent_dir = session_jsonl.parent / session_jsonl.stem / msmr.SUBAGENT_SUBDIR
        subagent_dir.mkdir(parents=True)
        (subagent_dir / "agent-1.meta.json").write_text("{not valid json")

        result = msmr.parse_subagent_dispatches(session_jsonl, requested_agent_declared_tools=None)

        assert result == ()

    def test_meta_json_missing_tool_use_id_skipped(self, tmp_path: Path) -> None:
        session_jsonl = _session_jsonl_path(tmp_path)
        subagent_dir = session_jsonl.parent / session_jsonl.stem / msmr.SUBAGENT_SUBDIR
        subagent_dir.mkdir(parents=True)
        (subagent_dir / "agent-1.meta.json").write_text(json.dumps({"agentType": "Explore"}))

        result = msmr.parse_subagent_dispatches(session_jsonl, requested_agent_declared_tools=None)

        assert result == ()

    def test_requested_model_param_read_from_meta(self, tmp_path: Path) -> None:
        session_jsonl = _session_jsonl_path(tmp_path)
        subagent_dir = session_jsonl.parent / session_jsonl.stem / msmr.SUBAGENT_SUBDIR
        _write_meta(subagent_dir, "agent-1", "toolu_1", requested_model="sonnet")
        _write_subagent_jsonl(subagent_dir, "agent-1", [_assistant_record("claude-sonnet-5")])

        result = msmr.parse_subagent_dispatches(session_jsonl, requested_agent_declared_tools=None)

        assert result[0].requested_model_param == "sonnet"

    def test_non_string_model_field_excluded_not_crashed(self, tmp_path: Path) -> None:
        """A non-string meta.json['model'] (e.g. a list) is real-world
        corruption this harness's own docstring calls out as defended
        against — mirrors
        test_transcript_analysis.py::test_non_string_meta_model_does_not_crash_the_run.
        The dispatch itself is still returned (toolUseId is valid); only the
        requested-model-param read is coerced to None."""
        session_jsonl = _session_jsonl_path(tmp_path)
        subagent_dir = session_jsonl.parent / session_jsonl.stem / msmr.SUBAGENT_SUBDIR
        subagent_dir.mkdir(parents=True)
        (subagent_dir / "agent-1.meta.json").write_text(
            json.dumps({"agentType": "staff-backend-engineer", "toolUseId": "toolu_1", "model": ["opus"]})
        )

        result = msmr.parse_subagent_dispatches(session_jsonl, requested_agent_declared_tools=None)

        assert len(result) == 1
        assert result[0].requested_model_param is None

    def test_missing_agent_type_falls_back_to_unknown(self, tmp_path: Path) -> None:
        session_jsonl = _session_jsonl_path(tmp_path)
        subagent_dir = session_jsonl.parent / session_jsonl.stem / msmr.SUBAGENT_SUBDIR
        subagent_dir.mkdir(parents=True)
        (subagent_dir / "agent-1.meta.json").write_text(json.dumps({"toolUseId": "toolu_1"}))

        result = msmr.parse_subagent_dispatches(session_jsonl, requested_agent_declared_tools=None)

        assert result[0].requested_agent_type == "unknown"

    def test_string_content_scanned_without_raising(self, tmp_path: Path) -> None:
        """Real transcripts sometimes carry message.content as a plain string
        rather than a list of blocks (transcript-analysis.py handles this at
        multiple call sites). The isinstance(block, dict) guard in
        _scan_subagent_jsonl must not raise on a string content field."""
        session_jsonl = _session_jsonl_path(tmp_path)
        subagent_dir = session_jsonl.parent / session_jsonl.stem / msmr.SUBAGENT_SUBDIR
        _write_meta(subagent_dir, "agent-1", "toolu_1")
        _write_subagent_jsonl(subagent_dir, "agent-1", [
            {"type": "assistant", "message": {"content": "plain text reply", "model": "claude-sonnet-5"}}
        ])

        result = msmr.parse_subagent_dispatches(session_jsonl, requested_agent_declared_tools=None)

        assert result[0].observed_model_family == msmr.MODEL_FAMILY_SONNET
        assert result[0].observed_tools == frozenset()


class TestWaitForSubagentSidecar:
    """The subagent sidecar can materialize on disk slightly after the parent
    `claude -p` process has already exited — this poll must tolerate that lag
    without a fixed sleep. Returns True if found, False on timeout (the
    caller surfaces a timeout as its own outcome — see RunResult's
    sidecar_poll_timed_out)."""

    def test_returns_true_immediately_when_already_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session_jsonl = _session_jsonl_path(tmp_path)
        subagent_dir = session_jsonl.parent / session_jsonl.stem / msmr.SUBAGENT_SUBDIR
        _write_meta(subagent_dir, "agent-1", "toolu_1")

        def _fail_if_called(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("should not sleep when the sidecar is already present")

        monkeypatch.setattr(msmr.time, "sleep", _fail_if_called)
        assert msmr._wait_for_subagent_sidecar(session_jsonl, timeout_s=5.0) is True

    def test_returns_true_once_sidecar_appears_mid_poll(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Simulates the observed race: the sidecar doesn't exist yet, then
        appears partway through polling. Also pins the poll interval to a
        fixed, hardcoded-positive expectation (not a read of
        SIDECAR_POLL_INTERVAL_S itself, which would make the assertion
        circular against the very constant a regression might mutate) —
        asserting only call *count* would still pass if the interval
        regressed to a busy-spin (0.0)."""
        session_jsonl = _session_jsonl_path(tmp_path)
        subagent_dir = session_jsonl.parent / session_jsonl.stem / msmr.SUBAGENT_SUBDIR
        sleep_calls: list[float] = []

        def _create_on_second_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)
            if len(sleep_calls) >= 2:
                _write_meta(subagent_dir, "agent-1", "toolu_1")

        monkeypatch.setattr(msmr.time, "sleep", _create_on_second_sleep)
        assert msmr._wait_for_subagent_sidecar(session_jsonl, timeout_s=5.0) is True

        assert len(sleep_calls) == 2
        assert all(interval >= 0.1 for interval in sleep_calls), (
            f"poll interval regressed toward a busy-spin: {sleep_calls}"
        )

    def test_returns_false_after_timeout_without_raising(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A sidecar that never appears is itself a finding for the caller to
        report (RunResult.sidecar_poll_timed_out) — this function never
        raises on timeout, it returns False."""
        session_jsonl = _session_jsonl_path(tmp_path)
        clock = {"t": 0.0}
        monkeypatch.setattr(msmr.time, "monotonic", lambda: clock["t"])

        def _advance_past_deadline(_seconds: float) -> None:
            clock["t"] += 100.0

        monkeypatch.setattr(msmr.time, "sleep", _advance_past_deadline)
        assert msmr._wait_for_subagent_sidecar(session_jsonl, timeout_s=5.0) is False

    def test_sidecar_landing_in_final_poll_window_still_counts_as_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A sidecar that materializes during the sleep call that pushes the
        clock past the deadline must still be found on the next check, not
        discarded as a timeout — the loop re-checks before re-testing the
        deadline, precisely to give the last poll window credit."""
        session_jsonl = _session_jsonl_path(tmp_path)
        subagent_dir = session_jsonl.parent / session_jsonl.stem / msmr.SUBAGENT_SUBDIR
        clock = {"t": 0.0}
        monkeypatch.setattr(msmr.time, "monotonic", lambda: clock["t"])

        def _cross_deadline_and_write(_seconds: float) -> None:
            clock["t"] = 5.0 + 0.01  # past the 5.0s deadline
            _write_meta(subagent_dir, "agent-1", "toolu_1")

        monkeypatch.setattr(msmr.time, "sleep", _cross_deadline_and_write)
        assert msmr._wait_for_subagent_sidecar(session_jsonl, timeout_s=5.0) is True


class TestResolvedTempProjectDir:
    """Pins the macOS symlink-mismatch fix: a temp dir reached through a
    symlink must come back resolved, or compute_session_store_dir() later
    hashes a different string than Claude Code did and finds nothing.
    Constructs its own symlink layer under tmp_path rather than relying on
    the real /var -> /private/var symlink, so this runs on any platform."""

    def test_resolves_a_symlinked_temp_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        link_dir = tmp_path / "link"
        link_dir.symlink_to(real_dir)

        created_name = "subagent-model-resolution-abc123"
        (real_dir / created_name).mkdir()
        unresolved_via_symlink = link_dir / created_name

        monkeypatch.setattr(msmr.tempfile, "mkdtemp", lambda prefix: str(unresolved_via_symlink))

        result = msmr._resolved_temp_project_dir("subagent-model-resolution-")

        assert result == unresolved_via_symlink.resolve()
        assert str(result) != str(unresolved_via_symlink)


class TestComputeSidecarPollTimedOut:
    """Pins the short-circuit composition in execute_matrix_cell as its own
    testable unit — a plausible reordering there would run the 10s poll on
    every no-dispatch cell with nothing to catch it."""

    def test_no_attempted_dispatch_never_times_out(self) -> None:
        assert msmr._compute_sidecar_poll_timed_out(attempted=False, sidecar_found=None) is False

    def test_attempted_and_found_is_not_a_timeout(self) -> None:
        assert msmr._compute_sidecar_poll_timed_out(attempted=True, sidecar_found=True) is False

    def test_attempted_and_not_found_is_a_timeout(self) -> None:
        assert msmr._compute_sidecar_poll_timed_out(attempted=True, sidecar_found=False) is True


class TestModelFamily:
    @pytest.mark.parametrize(
        ("model_id", "expected"),
        [
            ("claude-opus-4-7", msmr.MODEL_FAMILY_OPUS),
            ("claude-sonnet-5", msmr.MODEL_FAMILY_SONNET),
            ("claude-haiku-4-5", msmr.MODEL_FAMILY_HAIKU),
            ("Claude-Opus-4-7", msmr.MODEL_FAMILY_OPUS),
            ("some-future-model", msmr.MODEL_FAMILY_OTHER),
        ],
    )
    def test_buckets_by_substring(self, model_id: str, expected: str) -> None:
        assert msmr.model_family(model_id) == expected


class TestClassifyAgentIdentity:
    def test_no_declared_tools_is_inconclusive(self) -> None:
        assert (
            msmr.classify_agent_identity(None, frozenset({"Read"}))
            == msmr.AGENT_IDENTITY_INCONCLUSIVE
        )

    def test_subset_of_declared_tools_is_requested(self) -> None:
        declared = frozenset({"Read", "Grep", "Glob"})
        assert (
            msmr.classify_agent_identity(declared, frozenset({"Read"}))
            == msmr.AGENT_IDENTITY_REQUESTED
        )

    def test_discriminating_extra_tool_is_substituted(self) -> None:
        declared = frozenset({"Read", "Grep", "Glob"})
        assert (
            msmr.classify_agent_identity(declared, frozenset({"Read", "WebFetch"}))
            == msmr.AGENT_IDENTITY_SUBSTITUTED_TO_PLAN
        )

    def test_non_discriminating_extra_tool_is_inconclusive(self) -> None:
        """An undeclared tool outside PLAN_DISCRIMINATING_TOOLS proves nothing
        about substitution — e.g. a hypothetical future tool this heuristic
        doesn't know about."""
        declared = frozenset({"Read", "Grep", "Glob"})
        assert (
            msmr.classify_agent_identity(declared, frozenset({"Read", "NotebookEdit"}))
            == msmr.AGENT_IDENTITY_INCONCLUSIVE
        )

    def test_already_broad_declared_toolset_hides_bash_substitution(self) -> None:
        """staff-backend-engineer already declares Bash, so a Plan
        substitution that only used Bash-or-narrower tools looks identical
        to an as-requested run by this method alone — the documented limit
        named in plan M3's 'may never observe a true positive' validity
        threat."""
        declared = frozenset({"Read", "Grep", "Glob", "Bash", "Write"})
        assert (
            msmr.classify_agent_identity(declared, frozenset({"Read", "Bash"}))
            == msmr.AGENT_IDENTITY_REQUESTED
        )


class TestRun1SelfCheck:
    def _result(self, families: list[str | None]) -> msmr.RunResult:
        run = msmr.RUN_MATRIX[0]
        dispatches = tuple(
            msmr.DispatchObservation(
                tool_use_id=f"toolu_{i}",
                requested_agent_type="staff-backend-engineer",
                requested_model_param=None,
                sidecar_missing=False,
                observed_model_ids=frozenset(),
                observed_model_family=family,
                observed_tools=frozenset(),
                agent_identity=msmr.AGENT_IDENTITY_REQUESTED,
            )
            for i, family in enumerate(families)
        )
        return msmr.RunResult(
            run=run, session_id="s", attempted_dispatch=True, timed_out=False,
            declared_model_pin="sonnet", dispatches=dispatches,
        )

    def test_all_sonnet_passes(self) -> None:
        assert msmr.run1_self_check_passed(self._result([msmr.MODEL_FAMILY_SONNET])) is True

    def test_opus_fails(self) -> None:
        """Run 1 returning Opus means the harness read the parent's model,
        not the pinned subagent's — the void-every-run signal."""
        assert msmr.run1_self_check_passed(self._result([msmr.MODEL_FAMILY_OPUS])) is False

    def test_no_dispatches_fails(self) -> None:
        assert msmr.run1_self_check_passed(self._result([])) is False


class TestShouldStopMatrixAfter:
    """The --all loop's safety-critical stop decision, extracted from main()
    so an off-by-one or a dropped break can't ship undetected — see
    staff-sdet's 'Testability' finding on this diff."""

    def _result(self, run_number: int, family: str | None) -> msmr.RunResult:
        run = msmr.RUN_MATRIX[run_number - 1]
        dispatches = (
            msmr.DispatchObservation(
                tool_use_id="toolu_1", requested_agent_type="staff-backend-engineer",
                requested_model_param=None, sidecar_missing=False,
                observed_model_ids=frozenset(), observed_model_family=family,
                observed_tools=frozenset(), agent_identity=msmr.AGENT_IDENTITY_REQUESTED,
            ),
        ) if family is not None else ()
        return msmr.RunResult(
            run=run, session_id="s", attempted_dispatch=True, timed_out=False,
            declared_model_pin="sonnet", dispatches=dispatches,
        )

    def test_stops_when_run_one_fails(self) -> None:
        result = self._result(1, msmr.MODEL_FAMILY_OPUS)
        assert msmr.should_stop_matrix_after(msmr.RUN_MATRIX[0], result) is True

    def test_continues_when_run_one_passes(self) -> None:
        result = self._result(1, msmr.MODEL_FAMILY_SONNET)
        assert msmr.should_stop_matrix_after(msmr.RUN_MATRIX[0], result) is False

    def test_never_stops_on_a_later_run_regardless_of_outcome(self) -> None:
        """The self-check gate is run-1-specific — a bad outcome on runs 2-7
        is data to report, not a reason to abort the matrix."""
        result = self._result(4, msmr.MODEL_FAMILY_SONNET)
        assert msmr.should_stop_matrix_after(msmr.RUN_MATRIX[3], result) is False


class TestBuildRunCommand:
    def test_default_mode_omits_permission_mode_flag(self) -> None:
        run = msmr.RUN_MATRIX[0]  # run 1: opus / default / staff-backend-engineer
        cmd = msmr.build_run_command(run, session_id="sid", budget_cap_usd=11.5)
        assert "--permission-mode" not in cmd

    def test_plan_mode_passes_permission_mode_flag(self) -> None:
        run = msmr.RUN_MATRIX[1]  # run 2: sonnet / plan / staff-backend-engineer
        cmd = msmr.build_run_command(run, session_id="sid", budget_cap_usd=11.5)
        assert "--permission-mode" in cmd
        assert cmd[cmd.index("--permission-mode") + 1] == "plan"

    def test_explore_haiku_dispatch_adds_agents_override(self) -> None:
        run = msmr.RUN_MATRIX[5]  # run 6: opus / plan / explore-haiku
        cmd = msmr.build_run_command(run, session_id="sid", budget_cap_usd=11.5)
        assert "--agents" in cmd
        payload = json.loads(cmd[cmd.index("--agents") + 1])
        assert payload["Explore"]["model"] == "haiku"
        assert set(payload["Explore"]["tools"]) == {"Read", "Grep", "Glob"}

    def test_staff_backend_engineer_dispatch_has_no_agents_override(self) -> None:
        run = msmr.RUN_MATRIX[1]
        cmd = msmr.build_run_command(run, session_id="sid", budget_cap_usd=11.5)
        assert "--agents" not in cmd

    def test_session_id_and_budget_cap_passed_through(self) -> None:
        run = msmr.RUN_MATRIX[0]
        cmd = msmr.build_run_command(run, session_id="my-session", budget_cap_usd=7.25)
        assert cmd[cmd.index("--session-id") + 1] == "my-session"
        assert cmd[cmd.index("--max-budget-usd") + 1] == "7.25"


class TestRunMatrix:
    def test_seven_runs_numbered_one_through_seven(self) -> None:
        assert [r.number for r in msmr.RUN_MATRIX] == list(range(1, 8))

    def test_run_one_is_instrument_self_check_shape(self) -> None:
        run = msmr.RUN_MATRIX[0]
        assert run.model == "opus"
        assert run.permission_mode == msmr.DEFAULT_PERMISSION_MODE_LABEL
        assert run.dispatch == msmr.DISPATCH_STAFF_BACKEND_ENGINEER

    def test_repeats_reference_their_decisive_run(self) -> None:
        by_number = {r.number: r for r in msmr.RUN_MATRIX}
        assert by_number[3].repeat_of == 2
        assert by_number[5].repeat_of == 4
        assert by_number[7].repeat_of == 6
        assert by_number[2].repeat_of is None

    def test_only_runs_six_seven_dispatch_explore_haiku(self) -> None:
        explore_runs = {r.number for r in msmr.RUN_MATRIX if r.dispatch == msmr.DISPATCH_EXPLORE_HAIKU}
        assert explore_runs == {6, 7}


class TestAgentFrontmatterParsing:
    """Config-loading checks against this repo's own real agent files —
    exercised read-only, no subprocess."""

    def test_staff_backend_engineer_declared_model_is_sonnet(self) -> None:
        assert msmr.declared_model_pin_for_dispatch(msmr.DISPATCH_STAFF_BACKEND_ENGINEER) == "sonnet"

    def test_staff_backend_engineer_declared_tools_include_bash(self) -> None:
        tools = msmr.declared_tools_for_dispatch(msmr.DISPATCH_STAFF_BACKEND_ENGINEER)
        assert tools is not None
        assert "Bash" in tools

    def test_explore_haiku_override_declared_model_is_haiku(self) -> None:
        assert msmr.declared_model_pin_for_dispatch(msmr.DISPATCH_EXPLORE_HAIKU) == "haiku"

    def test_explore_haiku_override_declared_tools(self) -> None:
        assert msmr.declared_tools_for_dispatch(msmr.DISPATCH_EXPLORE_HAIKU) == frozenset(
            {"Read", "Grep", "Glob"}
        )

    def test_frontmatter_model_regex_ignores_prose_mentions(self) -> None:
        text = "---\nmodel: sonnet\n---\n\nThis body mentions model: opus in prose.\n"
        assert msmr.agent_frontmatter_model(text) == "sonnet"

    def test_frontmatter_tools_parses_comma_separated_list(self) -> None:
        text = "---\ntools: Read, Grep, Glob\n---\n\nbody\n"
        assert msmr.agent_frontmatter_tools(text) == frozenset({"Read", "Grep", "Glob"})

    def test_no_frontmatter_block_returns_none(self) -> None:
        assert msmr.agent_frontmatter_model("no frontmatter here") is None
        assert msmr.agent_frontmatter_tools("no frontmatter here") is None


class TestBudgetCap:
    def test_per_run_cap_is_ten_times_representative_dispatch_cost(self) -> None:
        """Guards against the derived constants drifting apart silently — see
        the module's M8 comment for the corpus figure this was derived from."""
        assert round(
            msmr.REPRESENTATIVE_DISPATCH_COST_USD * msmr.BUDGET_CAP_MULTIPLIER, 2
        ) == msmr.PER_RUN_BUDGET_CAP_USD
        assert msmr.BUDGET_CAP_MULTIPLIER == 10


class TestAbortIfSubagentModelEnvSet:
    def test_raises_system_exit_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDE_CODE_SUBAGENT_MODEL", "opus")
        with pytest.raises(SystemExit):
            msmr.abort_if_subagent_model_env_set()

    def test_no_op_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLAUDE_CODE_SUBAGENT_MODEL", raising=False)
        msmr.abort_if_subagent_model_env_set()  # must not raise


class TestGatherEnvironmentReport:
    def test_reports_expected_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Stubs the one subprocess call (claude --version) so this stays a
        fixture-based test with no dependency on claude being installed."""

        def fake_run(cmd, **kwargs):
            assert cmd == ["claude", "--version"]
            return subprocess.CompletedProcess(cmd, 0, stdout="2.1.228 (Claude Code)\n", stderr="")

        monkeypatch.setattr(msmr.subprocess, "run", fake_run)
        monkeypatch.delenv("CLAUDE_CODE_SUBAGENT_MODEL", raising=False)

        report = msmr.gather_environment_report()

        assert report["claude_version"] == "2.1.228 (Claude Code)"
        assert report["claude_code_subagent_model_env"] is None
        assert report["staff_backend_engineer_declared_model"] == "sonnet"
        assert report["explore_haiku_override_declared_model"] == "haiku"
        assert "config_dir" in report


class TestPrintRunResult:
    """The one human-visible artifact an operator reads mid-experiment to
    decide whether an empty-dispatches cell is a real negative or a dropped
    trial to re-run — see RunResult.sidecar_poll_timed_out."""

    def _result(self, *, sidecar_poll_timed_out: bool) -> msmr.RunResult:
        return msmr.RunResult(
            run=msmr.RUN_MATRIX[0], session_id="s", attempted_dispatch=True, timed_out=False,
            declared_model_pin="sonnet", dispatches=(), sidecar_poll_timed_out=sidecar_poll_timed_out,
        )

    def test_timeout_prints_dropped_trial_message(self, capsys: pytest.CaptureFixture[str]) -> None:
        msmr.print_run_result(self._result(sidecar_poll_timed_out=True))
        out = capsys.readouterr().out
        assert "dropped trial" in out
        assert "re-run" in out

    def test_genuine_no_dispatch_prints_plain_message(self, capsys: pytest.CaptureFixture[str]) -> None:
        msmr.print_run_result(self._result(sidecar_poll_timed_out=False))
        out = capsys.readouterr().out
        assert "none observed" in out
        assert "dropped trial" not in out


class TestResultToDict:
    def test_includes_sidecar_poll_timed_out_key(self) -> None:
        result = msmr.RunResult(
            run=msmr.RUN_MATRIX[0], session_id="s", attempted_dispatch=True, timed_out=False,
            declared_model_pin="sonnet", dispatches=(), sidecar_poll_timed_out=True,
        )
        assert msmr.result_to_dict(result)["sidecar_poll_timed_out"] is True


class TestBuildArgParser:
    def test_requires_exactly_one_of_list_run_all(self) -> None:
        parser = msmr.build_arg_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_list_and_run_are_mutually_exclusive(self) -> None:
        parser = msmr.build_arg_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--list", "--run", "1"])

    def test_run_parses_run_number(self) -> None:
        parser = msmr.build_arg_parser()
        args = parser.parse_args(["--run", "3"])
        assert args.run == 3
        assert args.budget_cap_usd == msmr.PER_RUN_BUDGET_CAP_USD
        assert args.timeout_s == msmr.run_skill_evals.SAMPLE_TIMEOUT_S

    def test_all_with_overridden_cap_and_timeout(self) -> None:
        parser = msmr.build_arg_parser()
        args = parser.parse_args(["--all", "--budget-cap-usd", "5", "--timeout-s", "30"])
        assert args.all is True
        assert args.budget_cap_usd == 5.0
        assert args.timeout_s == 30
