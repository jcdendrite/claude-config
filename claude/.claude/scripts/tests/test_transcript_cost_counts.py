"""Tests for transcript_analysis/subagent_mix.py's cmd_cost_counts: refusals, rendering, disclosure allowlist, charset pin.

One exception: test_top_level_config_dir_message_omits_flag_recommendation drives the shim's main(),
so it pins the CLI layer's shared top-level --config-dir refusal rather than cmd_cost_counts.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import (
    _agent_use,
    _asst,
    _skill_use,
    _write_jsonl,
)

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


def _cost_counts_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    branches: str | None = None,
    this_repo_slugs: list[str] | None = None,
) -> object:
    """this_repo_slugs, when given, pre-seeds args._this_repo_slugs -- the
    cache _resolve_project_scope reads first (see its own docstring), so a
    direct cmd_cost_counts() call under --this-repo never shells out to git."""
    attrs = {"projects": projects, "this_repo": this_repo, "branches": branches}
    if this_repo_slugs is not None:
        attrs["_this_repo_slugs"] = this_repo_slugs
    return type("A", (), attrs)()


class TestTrackedAgentFilenamesMatchAgentTypeNameCharset:
    """cost-counts prints a disclosed agent-type label raw with no escaping
    step, on the assumption every tracked agents/*.md stem already matches
    _AGENT_TYPE_NAME_RE's charset. A future filename outside this charset
    must fail this test, not silently reach a GFM table cell raw once that
    agent is dispatched."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        _mod.redaction._repo_tracked_agent_type_names.cache_clear()
        yield
        _mod.redaction._repo_tracked_agent_type_names.cache_clear()

    def test_every_tracked_agent_stem_matches_the_charset(self):
        names = _mod.redaction._repo_tracked_agent_type_names() - _mod._BUILT_IN_AGENT_TYPES
        assert names, "expected at least one repo-tracked agent definition"
        for name in names:
            assert _mod.subagent_mix._AGENT_TYPE_NAME_RE.fullmatch(name), name


class TestSpawnCountsByAgentType:
    """_spawn_counts_by_agent_type: main-thread-only raw spawn counts,
    feeding cost-counts's ### Subagent spawns table."""

    def test_spawn_nested_inside_a_subagent_is_excluded(self):
        """A spawn dispatched from inside another agent's own transcript
        (isSidechain: true) is not counted. Only main-thread dispatches
        decide this count, mirroring cmd_subagent_mix's own exclusion order
        (see _spawn_counts_by_agent_type's own docstring)."""
        records = [
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("a1", "staff-sdet")]),
            _asst("claude-opus-4-7", branch="feat", sidechain=True, content=[_agent_use("a2", "staff-sdet")]),
        ]
        counts = _mod.subagent_mix._spawn_counts_by_agent_type([(Path("sess.jsonl"), records)], None)
        assert counts == {"staff-sdet": 1}


class TestCostCounts:
    """cost-counts: per-branch review-round and subagent-spawn counts for a
    public PR body -- counts only, no dollar attribution."""

    @pytest.fixture(autouse=True)
    def _clear_agent_type_cache(self):
        """Same process-global lru_cache isolation as
        TestRepoTrackedAgentTypeNames -- a monkeypatched
        _REPO_AGENT_DEFINITIONS_DIR from one test here must never leak a
        stale cached result into the next."""
        _mod.redaction._repo_tracked_agent_type_names.cache_clear()
        yield
        _mod.redaction._repo_tracked_agent_type_names.cache_clear()

    def _isolate_allowlist(self, tmp_path, monkeypatch, tracked: list[str]) -> None:
        agents_dir = tmp_path / "isolated-agents"
        agents_dir.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=agents_dir, check=True)
        for name in tracked:
            (agents_dir / f"{name}.md").write_text("---\nname: x\n---\n")
        if tracked:
            subprocess.run(["git", "add", "--", *(f"{n}.md" for n in tracked)], cwd=agents_dir, check=True)
        monkeypatch.setattr(_mod.redaction, "_REPO_AGENT_DEFINITIONS_DIR", agents_dir)
        _mod.redaction._repo_tracked_agent_type_names.cache_clear()

    def test_this_repo_required(self, fake_projects, capsys):
        with pytest.raises(SystemExit) as exc_info:
            _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(this_repo=False, branches="feat"))
        assert exc_info.value.code == 2
        assert "--this-repo" in capsys.readouterr().err

    def test_non_default_projects_refused(self, fake_projects, capsys):
        with pytest.raises(SystemExit) as exc_info:
            _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(this_repo=True, projects="some-glob", branches="feat"))
        assert exc_info.value.code == 2
        assert "--projects" in capsys.readouterr().err

    def test_branches_required(self, fake_projects, capsys):
        """--branches is refused at runtime, inside cmd_cost_counts, not made
        argparse-required -- an argparse-level requirement would fire during
        parse_args(), before main()'s own top-level --config-dir refusal
        check ever runs."""
        with pytest.raises(SystemExit) as exc_info:
            _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(this_repo=True, branches=None))
        assert exc_info.value.code == 2
        assert "--branches" in capsys.readouterr().err

    def test_review_round_table_renders_actual_round_counts(self, fake_projects, capsys):
        """Every other TestCostCounts fixture seeds spawn records only, so
        round_counts is all-zero in every one of them. This test seeds a
        real round-opening record (a Skill tool_use matching a
        REVIEW_SKILLS member) and asserts the ### Review rounds table's
        actual rendered row values and caption, through the real
        cmd_cost_counts rendering path."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[_skill_use("s1", "code-review")]),
        ])
        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="feat", this_repo_slugs=["-home-user-testrepo"],
        ))
        out = capsys.readouterr().out
        assert "| code-review | 1 |" in out
        assert "| plan-review | 0 |" in out
        assert "| ready-for-review | 0 |" in out
        assert "| **total** | **1** |" in out
        assert _mod.subagent_mix._COST_COUNTS_ROUNDS_CAPTION in out

    def test_untracked_subagent_type_is_withheld_under_this_repo(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        self._isolate_allowlist(tmp_path, monkeypatch, tracked=["staff-sdet"])
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("a1", "staff-sdet")]),
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("a2", "totally-untracked-agent")]),
        ])
        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="feat", this_repo_slugs=["-home-user-testrepo"],
        ))
        out = capsys.readouterr().out
        assert "staff-sdet" in out
        assert "totally-untracked-agent" not in out
        assert "(withheld — untracked agent type)" in out

    def test_withheld_row_stays_last_even_when_its_total_exceeds_a_disclosed_row(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """The withheld row is appended after the (-count, name) sort, never
        merged into it -- a withheld total that outweighs every disclosed
        row's own count must still render last, not sort ahead of a named
        row and get mistaken for one (see
        _partition_spawn_counts_by_disclosure's own docstring)."""
        self._isolate_allowlist(tmp_path, monkeypatch, tracked=["staff-sdet"])
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("a1", "staff-sdet")]),
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("a2", "untracked-agent-a")]),
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("a3", "untracked-agent-b")]),
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("a4", "untracked-agent-c")]),
        ])
        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="feat", this_repo_slugs=["-home-user-testrepo"],
        ))
        out = capsys.readouterr().out
        assert out.index("| staff-sdet | 1 |") < out.index("(withheld — untracked agent type)")

    def test_backstop_assertion_fires_on_bypassed_partition_step(
        self, fake_projects, tmp_path, monkeypatch,
    ):
        """cost-counts's own render-time backstop: a future regression to a
        _stype_label-style direct reuse (bypassing
        _partition_spawn_counts_by_disclosure's own allowlist gate) must
        fail loudly, not silently disclose an untracked subagent_type."""
        self._isolate_allowlist(tmp_path, monkeypatch, tracked=["staff-sdet"])
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("a1", "staff-sdet")]),
        ])
        monkeypatch.setattr(
            _mod.subagent_mix, "_partition_spawn_counts_by_disclosure",
            lambda raw_counts: [("some-untracked-agent-type", 1)],
        )
        with pytest.raises(AssertionError) as exc_info:
            _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
                this_repo=True, branches="feat", this_repo_slugs=["-home-user-testrepo"],
            ))
        assert "some-untracked-agent-type" not in str(exc_info.value)

    def test_more_than_five_distinct_agent_types_all_render(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """This is the test that would have caught a top-5-scrape
        reimplementation: cmd_subagent_mix's own table truncates to the top
        5 spawn types per branch, but cost-counts must not."""
        tracked = [f"agent-{i}" for i in range(6)]
        self._isolate_allowlist(tmp_path, monkeypatch, tracked=tracked)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use(f"a{i}", name)])
            for i, name in enumerate(tracked)
        ])
        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="feat", this_repo_slugs=["-home-user-testrepo"],
        ))
        out = capsys.readouterr().out
        for name in tracked:
            assert name in out

    def test_zero_spawns_renders_the_sentence_not_a_table(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[]),
        ])
        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="feat", this_repo_slugs=["-home-user-testrepo"],
        ))
        out = capsys.readouterr().out
        assert "No subagent spawns found in scope." in out
        assert "| Agent type | Spawns |" not in out

    def test_no_branch_name_appears_anywhere_in_output(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="my-secret-branch-name", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="my-secret-branch-name", this_repo_slugs=["-home-user-testrepo"],
        ))
        out = capsys.readouterr().out
        assert "my-secret-branch-name" not in out

    def test_second_declared_root_contributes_nothing(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """cost-counts always resolves to [config_dir() / "projects"] alone
        -- a populated ~/.claude/transcript-config-dirs must not pull
        another account's activity into a public PR body's counts."""
        self._isolate_allowlist(tmp_path, monkeypatch, tracked=["staff-sdet"])
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("a1", "staff-sdet")]),
        ])
        declared_root = tmp_path / "declared-root"
        declared_proj = declared_root / "projects" / "-home-user-other-repo"
        declared_proj.mkdir(parents=True)
        _write_jsonl(declared_proj / "sess-other.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("a2", "staff-sdet")]),
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("a3", "staff-sdet")]),
        ])
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{declared_root}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="feat", this_repo_slugs=["-home-user-testrepo"],
        ))
        out = capsys.readouterr().out
        assert "| staff-sdet | 1 |" in out

    def test_top_level_config_dir_message_omits_flag_recommendation(self, monkeypatch, tmp_path, capsys):
        """cost-counts registers no --config-dir flag of its own, unlike
        every other _SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR member -- the
        shared refusal message must not recommend a flag that doesn't exist
        on its own parser."""
        other_account = tmp_path / "other-account"
        (other_account / "projects").mkdir(parents=True)
        monkeypatch.setattr(
            sys, "argv", ["transcript-analysis.py", "--config-dir", str(other_account), "cost-counts"],
        )
        with pytest.raises(SystemExit) as exc_info:
            _mod.main()
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "cost-counts" in err
        assert "use that instead" not in err
        assert "with no override" in err
