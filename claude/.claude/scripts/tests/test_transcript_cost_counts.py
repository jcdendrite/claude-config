"""Tests for transcript_analysis/subagent_mix.py's cmd_cost_counts: refusals, rendering,
nested-spawn counting and attribution, disclosure allowlist, charset pin.

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
    _attr_ts,
    _skill_use,
    _slash_user,
    _tool_result,
    _user_msg,
    _write_jsonl,
    _write_subagent_dispatch,
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
    """_spawn_counts_by_agent_type: raw spawn counts, main-thread and nested,
    feeding cost-counts's ### Subagent spawns table."""

    def test_spawn_nested_inside_a_subagent_is_counted(self):
        """A record flagged isSidechain: true counts alongside a main-thread one."""
        records = [
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("a1", "staff-sdet")]),
            _asst("claude-opus-4-7", branch="feat", sidechain=True, content=[_agent_use("a2", "staff-sdet")]),
        ]
        counts = _mod.subagent_mix._spawn_counts_by_agent_type([(Path("sess.jsonl"), records)], None)
        assert counts == {"staff-sdet": 2}

    def test_nested_spawn_is_attributed_to_the_main_thread_branch_not_its_own_tag(self):
        """A sidechain record's own gitBranch never decides its branch. With no
        timestamps it takes the first main-thread branch in file order."""
        records = [
            _asst("claude-opus-4-7", branch="feat-a"),
            _asst(
                "claude-opus-4-7", branch="feat-b", sidechain=True,
                content=[_agent_use("a2", "ciso-reviewer")],
            ),
        ]
        sessions = [(Path("sess.jsonl"), records)]
        assert _mod.subagent_mix._spawn_counts_by_agent_type(sessions, {"feat-a"}) == {"ciso-reviewer": 1}
        assert _mod.subagent_mix._spawn_counts_by_agent_type(sessions, {"feat-b"}) == {}

    def test_nested_spawn_counts_under_the_main_thread_branch_in_effect_at_its_timestamp(self):
        """Main records enter feat-a at t0 and feat-b at t2. A sidechain spawn at t1 counts
        under feat-a only, and one at t3 under feat-b only, whatever tag the sidechain record carries."""
        records = [
            _asst("claude-opus-4-7", branch="feat-a", ts=_attr_ts(0)),
            _asst(
                "claude-opus-4-7", branch="worktree-agent-ignored", sidechain=True, ts=_attr_ts(1),
                content=[_agent_use("a1", "staff-sdet")],
            ),
            _asst("claude-opus-4-7", branch="feat-b", ts=_attr_ts(2)),
            _asst(
                "claude-opus-4-7", branch="worktree-agent-ignored", sidechain=True, ts=_attr_ts(3),
                content=[_agent_use("a2", "ciso-reviewer")],
            ),
        ]
        sessions = [(Path("sess.jsonl"), records)]
        assert _mod.subagent_mix._spawn_counts_by_agent_type(sessions, {"feat-a"}) == {"staff-sdet": 1}
        assert _mod.subagent_mix._spawn_counts_by_agent_type(sessions, {"feat-b"}) == {"ciso-reviewer": 1}

    def test_nested_spawn_during_the_leading_default_branch_run_counts_toward_the_first_branch_entered(self):
        """A sidechain spawn resolved to the main thread's leading `main` run counts toward feat-x,
        the first real branch the session enters, and not toward main."""
        records = [
            _asst("claude-opus-4-7", branch="main", ts=_attr_ts(0)),
            _asst(
                "claude-opus-4-7", branch="main", sidechain=True, ts=_attr_ts(1),
                content=[_agent_use("a1", "staff-sdet")],
            ),
            _asst("claude-opus-4-7", branch="feat-x", ts=_attr_ts(2)),
        ]
        sessions = [(Path("sess.jsonl"), records)]
        assert _mod.subagent_mix._spawn_counts_by_agent_type(sessions, {"feat-x"}) == {"staff-sdet": 1}
        assert _mod.subagent_mix._spawn_counts_by_agent_type(sessions, {"main"}) == {}

    def test_sidechain_only_session_is_not_counted_under_a_branch_filter(self):
        """With no branch-bearing main-thread record there is no main-thread branch to attribute to,
        so the sidechain record's own tag does not place its spawn under a filter naming that tag."""
        records = [
            _asst(
                "claude-opus-4-7", branch="feat", sidechain=True, ts=_attr_ts(0),
                content=[_agent_use("a1", "staff-sdet")],
            ),
        ]
        sessions = [(Path("agent-x.jsonl"), records)]
        assert _mod.subagent_mix._spawn_counts_by_agent_type(sessions, {"feat"}) == {}

    @pytest.mark.parametrize(
        "malformed_input",
        [
            "not-a-dict",
            ["staff-sdet"],
            {"subagent_type": ["staff-sdet"]},
            {"subagent_type": {"name": "staff-sdet"}},
            {"subagent_type": 7},
            {"subagent_type": ""},
            {},
            None,
        ],
        ids=[
            "string-input", "list-input", "list-subagent-type", "dict-subagent-type",
            "int-subagent-type", "empty-subagent-type", "empty-input", "null-input",
        ],
    )
    def test_malformed_spawn_input_counts_as_unknown_and_does_not_abort_the_count(self, malformed_input):
        """A malformed spawn counts as unknown; the well-formed spawn beside it still counts."""
        malformed_block = {"type": "tool_use", "id": "bad", "name": "Agent", "input": malformed_input}
        records = [
            _asst("claude-opus-4-7", branch="feat", sidechain=True, content=[malformed_block]),
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("ok", "staff-sdet")]),
        ]
        counts = _mod.subagent_mix._spawn_counts_by_agent_type([(Path("sess.jsonl"), records)], None)
        assert counts == {_mod.subagent_mix._UNKNOWN_SUBAGENT_TYPE: 1, "staff-sdet": 1}

    def test_branch_filter_counts_only_the_named_branchs_spawn_of_a_shared_agent_type(self):
        """A filter naming one of two branches counts only that branch's spawn of a shared agent type."""
        sessions = [
            (Path("a.jsonl"), [
                _asst("claude-opus-4-7", branch="feat-a", content=[_agent_use("a1", "staff-sdet")]),
            ]),
            (Path("b.jsonl"), [
                _asst("claude-opus-4-7", branch="feat-b", content=[_agent_use("b1", "staff-sdet")]),
                _asst("claude-opus-4-7", branch="feat-b", content=[_agent_use("b2", "staff-sdet")]),
            ]),
        ]
        counts = _mod.subagent_mix._spawn_counts_by_agent_type(sessions, {"feat-a"})
        assert counts == {"staff-sdet": 1}

    def test_nested_spawn_is_attributed_within_its_own_session_when_session_timestamps_interleave(self):
        """Each session resolves a sidechain spawn against its own main-thread timeline. Session A's
        spawn at t3 stays under feat-a although session B's main thread enters feat-b at t1, and a
        timeline merged across sessions would count it under feat-b."""
        sessions = [
            (Path("a.jsonl"), [
                _asst("claude-opus-4-7", branch="feat-a", ts=_attr_ts(0)),
                _asst(
                    "claude-opus-4-7", branch="worktree-agent-ignored", sidechain=True, ts=_attr_ts(3),
                    content=[_agent_use("a1", "staff-sdet")],
                ),
            ]),
            (Path("b.jsonl"), [
                _asst("claude-opus-4-7", branch="feat-b", ts=_attr_ts(1)),
                _asst(
                    "claude-opus-4-7", branch="worktree-agent-ignored", sidechain=True, ts=_attr_ts(4),
                    content=[_agent_use("b1", "ciso-reviewer")],
                ),
            ]),
        ]
        assert _mod.subagent_mix._spawn_counts_by_agent_type(sessions, {"feat-a"}) == {"staff-sdet": 1}
        assert _mod.subagent_mix._spawn_counts_by_agent_type(sessions, {"feat-b"}) == {"ciso-reviewer": 1}

    def test_empty_branch_filter_set_excludes_every_spawn(self):
        """An empty (not None) filter set names no branch, so it counts nothing rather than everything."""
        sessions = [
            (Path("a.jsonl"), [
                _asst("claude-opus-4-7", branch="feat-a", content=[_agent_use("a1", "staff-sdet")]),
            ]),
        ]
        assert _mod.subagent_mix._spawn_counts_by_agent_type(sessions, set()) == {}


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
        """Seeds a real round-opening record (a Skill tool_use matching a
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

    def test_main_thread_review_orchestrator_dispatch_renders_as_a_code_review_round_with_its_caption(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """Both caption texts are literals here, not read from
        _COST_COUNTS_ROUNDS_CAPTION or _COST_COUNTS_SPAWNS_CAPTION, so a
        reworded or reverted caption turns this red. The captions are public
        PR-body text."""
        self._isolate_allowlist(tmp_path, monkeypatch, tracked=["review-orchestrator"])
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("o1", "review-orchestrator")]),
        ])
        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="feat", this_repo_slugs=["-home-user-testrepo"],
        ))
        out = capsys.readouterr().out
        assert "| code-review | 1 |" in out
        assert out.count("| review-orchestrator | 1 |") == 1
        assert "(withheld" not in out
        assert (
            "Each invocation of a review skill, and each main-thread `review-orchestrator` dispatch,"
            " is one round, whether or not it produced findings."
            " Counts reflect this section's last render; a review round that ran afterward may not"
            " be included yet."
        ) in out
        assert (
            "Counts recorded spawn calls in each session's main transcript and its subagent transcripts"
            " (not those nested in workflow directories), including agents dispatched by another agent,"
            " such as the reviewers a `review-orchestrator` spawns."
        ) in out

    def test_review_orchestrator_dispatch_from_inside_a_subagent_is_a_spawn_but_not_a_round(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """A sidechain record's dispatch adds a spawn row but opens no code-review round."""
        self._isolate_allowlist(tmp_path, monkeypatch, tracked=["review-orchestrator"])
        _write_jsonl(fake_projects / "sess.jsonl", [
            # Carries branch "feat" to the sidechain record; without it the
            # round-open guard is never reached and that half passes vacuously.
            _asst("claude-opus-4-7", branch="feat"),
            _asst(
                "claude-opus-4-7", branch="feat", sidechain=True,
                content=[_agent_use("o1", "review-orchestrator")],
            ),
        ])
        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="feat", this_repo_slugs=["-home-user-testrepo"],
        ))
        out = capsys.readouterr().out
        assert "| review-orchestrator | 1 |" in out
        assert "| code-review | 0 |" in out

    def test_reviewers_spawned_inside_subagent_files_count_alongside_the_main_thread_orchestrator(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """Real layout: the main-thread review-orchestrator dispatch sits in sess.jsonl and the
        reviewers it spawns sit in sess/subagents/*.jsonl, flagged isSidechain.
        Both the main-thread dispatch and the nested reviewers count as spawns.
        Only the main-thread dispatch is a round.
        A flagged review-orchestrator dispatch and a flagged Skill code-review block inside a
        subagents/ file leave the round count unchanged.
        The flagged user prompt and tool_result records in the subagents/ file are layout realism only.
        They mirror what the harness writes and guard no asserted value.
        test_flagged_slash_command_record_in_a_subagent_file_opens_no_round owns the round guard.
        Reads the subagents/ directory, so it fails if cmd_cost_counts loads main transcripts alone."""
        self._isolate_allowlist(
            tmp_path, monkeypatch, tracked=["review-orchestrator", "staff-sdet", "ciso-reviewer"],
        )
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst(
                "claude-opus-4-7", branch="feat", ts=_attr_ts(0),
                content=[_agent_use("o1", "review-orchestrator")],
            ),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-orchestrator", "o1",
            [
                {**_user_msg("run the review round", branch="feat", ts=_attr_ts(1)), "isSidechain": True},
                _asst(
                    "claude-opus-4-7", branch="feat", sidechain=True, ts=_attr_ts(1),
                    content=[
                        _agent_use("r1", "staff-sdet"),
                        _agent_use("r2", "ciso-reviewer"),
                        _agent_use("o2", "review-orchestrator"),
                        _skill_use("k1", "code-review"),
                    ],
                ),
                {
                    **_user_msg([_tool_result("r1", "done")], branch="feat", ts=_attr_ts(2)),
                    "isSidechain": True,
                },
            ],
            agent_type="review-orchestrator",
        )
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-second-reviewer", "r1",
            [
                {**_user_msg("review the diff", branch="feat", ts=_attr_ts(2)), "isSidechain": True},
                _asst(
                    "claude-opus-4-7", branch="feat", sidechain=True, ts=_attr_ts(2),
                    content=[_agent_use("r3", "staff-sdet")],
                ),
                {
                    **_user_msg([_tool_result("r3", "done")], branch="feat", ts=_attr_ts(3)),
                    "isSidechain": True,
                },
            ],
            agent_type="staff-sdet",
            spawn_depth=2,
        )
        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="feat", this_repo_slugs=["-home-user-testrepo"],
        ))
        out = capsys.readouterr().out
        assert "| code-review | 1 |" in out
        assert "| **total** | **1** |" in out
        assert "| staff-sdet | 2 |" in out
        assert "| ciso-reviewer | 1 |" in out
        assert "| review-orchestrator | 2 |" in out
        assert "| **total** | **5** |" in out

    def test_spawn_and_round_rows_sum_across_sessions(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """Two sessions each hold a main-thread review-orchestrator dispatch and a subagents/
        reviewer spawn of the same tracked type. Rows and totals add across sessions, so a count
        that kept only the last session's contribution fails."""
        self._isolate_allowlist(tmp_path, monkeypatch, tracked=["review-orchestrator", "staff-sdet"])
        for session_id in ("sess-one", "sess-two"):
            _write_jsonl(fake_projects / f"{session_id}.jsonl", [
                _asst(
                    "claude-opus-4-7", branch="feat", ts=_attr_ts(0),
                    content=[_agent_use("o1", "review-orchestrator")],
                ),
            ])
            _write_subagent_dispatch(
                fake_projects, session_id, "agent-orchestrator", "o1",
                [
                    _asst(
                        "claude-opus-4-7", branch="feat", sidechain=True, ts=_attr_ts(1),
                        content=[_agent_use("r1", "staff-sdet")],
                    ),
                ],
                agent_type="review-orchestrator",
            )
        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="feat", this_repo_slugs=["-home-user-testrepo"],
        ))
        out = capsys.readouterr().out
        assert "| code-review | 2 |" in out
        assert "| **total** | **2** |" in out
        assert "| review-orchestrator | 2 |" in out
        assert "| staff-sdet | 2 |" in out
        assert "| **total** | **4** |" in out

    def test_nested_spawns_count_under_the_main_thread_branch_in_effect_at_their_timestamp(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """A session switches feat-a to feat-b. A subagents/ spawn stamped before the switch counts
        under feat-a only, and one stamped after counts under feat-b only."""
        self._isolate_allowlist(tmp_path, monkeypatch, tracked=["staff-sdet", "ciso-reviewer"])
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat-a", ts=_attr_ts(0)),
            _asst("claude-opus-4-7", branch="feat-b", ts=_attr_ts(2)),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-before-switch", "t1",
            [
                _asst(
                    "claude-opus-4-7", branch="feat-b", sidechain=True, ts=_attr_ts(1),
                    content=[_agent_use("n1", "staff-sdet")],
                ),
            ],
        )
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-after-switch", "t2",
            [
                _asst(
                    "claude-opus-4-7", branch="feat-a", sidechain=True, ts=_attr_ts(3),
                    content=[_agent_use("n2", "ciso-reviewer")],
                ),
            ],
        )

        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="feat-a", this_repo_slugs=["-home-user-testrepo"],
        ))
        feat_a_out = capsys.readouterr().out
        assert "| staff-sdet | 1 |" in feat_a_out
        assert "| **total** | **1** |" in feat_a_out.split("### Subagent spawns")[1]
        assert "ciso-reviewer" not in feat_a_out

        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="feat-b", this_repo_slugs=["-home-user-testrepo"],
        ))
        feat_b_out = capsys.readouterr().out
        assert "| ciso-reviewer | 1 |" in feat_b_out
        assert "| **total** | **1** |" in feat_b_out.split("### Subagent spawns")[1]
        assert "staff-sdet" not in feat_b_out

    def test_nested_spawn_after_a_branch_switch_leaves_its_dispatching_round_on_the_earlier_branch(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """A main-thread review-orchestrator dispatch on feat-a opens a round and a spawn there. The main
        thread then switches to feat-b, and a reviewer the orchestrator spawns after the switch counts
        under feat-b, so the round and its nested reviewer split across branches. Attributing the
        nested spawn by its dispatching record's tag would count it under feat-a instead."""
        self._isolate_allowlist(tmp_path, monkeypatch, tracked=["review-orchestrator", "staff-sdet"])
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst(
                "claude-opus-4-7", branch="feat-a", ts=_attr_ts(0),
                content=[_agent_use("o1", "review-orchestrator")],
            ),
            _asst("claude-opus-4-7", branch="feat-b", ts=_attr_ts(2)),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-orchestrator", "o1",
            [
                _asst(
                    "claude-opus-4-7", branch="feat-a", sidechain=True, ts=_attr_ts(3),
                    content=[_agent_use("r1", "staff-sdet")],
                ),
            ],
            agent_type="review-orchestrator",
        )

        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="feat-a", this_repo_slugs=["-home-user-testrepo"],
        ))
        feat_a_out = capsys.readouterr().out
        assert "| code-review | 1 |" in feat_a_out
        assert "| review-orchestrator | 1 |" in feat_a_out
        assert "| staff-sdet |" not in feat_a_out

        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="feat-b", this_repo_slugs=["-home-user-testrepo"],
        ))
        feat_b_out = capsys.readouterr().out
        assert "| code-review | 0 |" in feat_b_out
        assert "| staff-sdet | 1 |" in feat_b_out
        assert "| review-orchestrator |" not in feat_b_out

    def test_flagged_slash_command_record_in_a_subagent_file_opens_no_round(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """Real layout: a `/code-review` user record flagged isSidechain inside sess/subagents/*.jsonl
        opens no round, because cost-counts feeds subagent files to the round detector and only the
        per-record isSidechain flag keeps them out."""
        self._isolate_allowlist(tmp_path, monkeypatch, tracked=["staff-sdet"])
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat", ts=_attr_ts(0)),
        ])
        flagged_slash_record = {
            **_slash_user("code-review", branch="feat", ts=_attr_ts(1)),
            "isSidechain": True,
        }
        _write_subagent_dispatch(fake_projects, "sess", "agent-nested", "t1", [flagged_slash_record])

        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="feat", this_repo_slugs=["-home-user-testrepo"],
        ))
        out = capsys.readouterr().out
        assert "| code-review | 0 |" in out
        assert "| **total** | **0** |" in out

    def test_sidechain_spawn_in_a_subagent_file_beside_an_empty_main_transcript_is_not_counted(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """Real layout: sess.jsonl holds no records, so no main-thread branch exists. A flagged spawn in
        sess/subagents/*.jsonl carrying its own `feat` tag is not counted under --branches feat."""
        self._isolate_allowlist(tmp_path, monkeypatch, tracked=["staff-sdet"])
        _write_jsonl(fake_projects / "sess.jsonl", [])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-nested", "t1",
            [
                _asst(
                    "claude-opus-4-7", branch="feat", sidechain=True, ts=_attr_ts(1),
                    content=[_agent_use("n1", "staff-sdet")],
                ),
            ],
        )
        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="feat", this_repo_slugs=["-home-user-testrepo"],
        ))
        out = capsys.readouterr().out
        assert "staff-sdet" not in out
        assert "No subagent spawns found in scope." in out

    def test_hostile_subagent_type_spawned_inside_a_subagent_file_folds_without_reaching_the_table(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """A subagent_type carrying a pipe, a newline and markdown is untracked, so it folds into the
        withheld row: none of its text reaches the table."""
        self._isolate_allowlist(tmp_path, monkeypatch, tracked=["staff-sdet"])
        hostile_type = "evil|cell\n| injected | 99 |\n# [link](http://example.invalid/x)"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat", ts=_attr_ts(0)),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-nested", "t1",
            [
                _asst(
                    "claude-opus-4-7", branch="feat", sidechain=True, ts=_attr_ts(1),
                    content=[_agent_use("n1", hostile_type)],
                ),
            ],
        )
        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="feat", this_repo_slugs=["-home-user-testrepo"],
        ))
        out = capsys.readouterr().out
        assert "| (withheld — untracked agent type) | 1 |" in out
        assert "| **total** | **1** |" in out.split("### Subagent spawns")[1]
        for fragment in ("evil", "injected", "[link]", "example.invalid"):
            assert fragment not in out

    def test_subagent_file_record_without_the_sidechain_flag_is_treated_as_main_thread(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """Pins current behavior for a record in sess/subagents/*.jsonl that lacks isSidechain (see
        docs/transcript-analysis.md, Limits): it is treated as a main-thread record and keeps its own
        gitBranch tag. A flagless review-orchestrator dispatch there therefore opens a code-review
        round and counts as a spawn under that tag, and under no other branch."""
        self._isolate_allowlist(tmp_path, monkeypatch, tracked=["review-orchestrator"])
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat", ts=_attr_ts(0)),
        ])
        flagless_record = _asst(
            "claude-opus-4-7", branch="feat-sub", ts=_attr_ts(1),
            content=[_agent_use("n1", "review-orchestrator")],
        )
        flagless_record.pop("isSidechain")
        _write_subagent_dispatch(fake_projects, "sess", "agent-flagless", "t1", [flagless_record])

        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="feat-sub", this_repo_slugs=["-home-user-testrepo"],
        ))
        own_tag_out = capsys.readouterr().out
        assert "| code-review | 1 |" in own_tag_out
        assert "| review-orchestrator | 1 |" in own_tag_out

        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="feat", this_repo_slugs=["-home-user-testrepo"],
        ))
        main_thread_branch_out = capsys.readouterr().out
        assert "| code-review | 0 |" in main_thread_branch_out
        assert "| review-orchestrator |" not in main_thread_branch_out
        assert "No subagent spawns found in scope." in main_thread_branch_out

    def test_prefix_round_and_prefix_spawn_count_under_the_entered_branch(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """A round and a spawn recorded on the default branch before the
        session enters feat-x count toward feat-x, and not toward main."""
        self._isolate_allowlist(tmp_path, monkeypatch, tracked=["staff-sdet"])
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst(
                "claude-opus-4-7", branch="main", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_use("s1", "code-review")],
            ),
            _asst(
                "claude-opus-4-7", branch="main", ts="2026-08-01T10:01:00.000Z",
                content=[_agent_use("a1", "staff-sdet")],
            ),
            _asst("claude-opus-4-7", branch="feat-x", ts="2026-08-01T10:03:00.000Z"),
        ])

        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="feat-x", this_repo_slugs=["-home-user-testrepo"],
        ))
        entered_out = capsys.readouterr().out
        assert "| code-review | 1 |" in entered_out
        assert "| staff-sdet | 1 |" in entered_out

        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="main", this_repo_slugs=["-home-user-testrepo"],
        ))
        default_out = capsys.readouterr().out
        assert "| code-review | 0 |" in default_out
        assert "staff-sdet" not in default_out

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
        with pytest.raises(
            AssertionError,
            match=r"spawn row 0 is neither the withheld label nor a repo-tracked agent type",
        ) as exc_info:
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
            _asst("claude-opus-4-7", branch="feat", content=[_skill_use("s1", "code-review")]),
        ])
        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="feat", this_repo_slugs=["-home-user-testrepo"],
        ))
        out = capsys.readouterr().out
        assert "| code-review | 1 |" in out
        assert "No subagent spawns found in scope." in out
        assert "| Agent type | Spawns |" not in out

    def test_all_zero_state_renders_zero_round_rows_and_the_no_spawns_sentence(self, fake_projects, capsys):
        """Real cmd_cost_counts renders the zero review-round rows and the no-spawns sentence as one body.
        The other-branch run proves the seeded corpus is read."""
        _write_jsonl(fake_projects / "sess-other.jsonl", [
            _asst("claude-opus-4-7", branch="other", content=[_skill_use("s1", "code-review")]),
        ])
        _write_jsonl(fake_projects / "sess-feat.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[]),
        ])

        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="other", this_repo_slugs=["-home-user-testrepo"],
        ))
        assert "| code-review | 1 |" in capsys.readouterr().out

        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="feat", this_repo_slugs=["-home-user-testrepo"],
        ))
        out = capsys.readouterr().out
        assert (
            "| code-review | 0 |\n"
            "| plan-review | 0 |\n"
            "| ready-for-review | 0 |\n"
            "| **total** | **0** |\n"
            "\n"
            "### Subagent spawns\n"
            "\n"
            f"{_mod.subagent_mix._COST_COUNTS_SPAWNS_CAPTION}\n"
            "\n"
            "No subagent spawns found in scope.\n"
        ) in out
        assert "| Agent type | Spawns |" not in out

    def test_empty_branches_list_renders_the_all_zero_state_not_corpus_wide_counts(self, fake_projects, capsys):
        """`--branches ","` parses to an empty branch set, which excludes every record: the output is the
        all-zero state, never a corpus-wide count."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst(
                "claude-opus-4-7", branch="feat",
                content=[_skill_use("s1", "code-review"), _agent_use("a1", "staff-sdet")],
            ),
        ])

        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="feat", this_repo_slugs=["-home-user-testrepo"],
        ))
        assert "| code-review | 1 |" in capsys.readouterr().out

        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches=",", this_repo_slugs=["-home-user-testrepo"],
        ))
        out = capsys.readouterr().out
        assert (
            "| code-review | 0 |\n"
            "| plan-review | 0 |\n"
            "| ready-for-review | 0 |\n"
            "| **total** | **0** |\n"
            "\n"
            "### Subagent spawns\n"
            "\n"
            f"{_mod.subagent_mix._COST_COUNTS_SPAWNS_CAPTION}\n"
            "\n"
            "No subagent spawns found in scope.\n"
        ) in out
        assert "| Agent type | Spawns |" not in out

    def test_no_branch_name_appears_anywhere_in_output(self, fake_projects, tmp_path, monkeypatch, capsys):
        self._isolate_allowlist(tmp_path, monkeypatch, tracked=["staff-sdet"])
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst(
                "claude-opus-4-7", branch="my-secret-branch-name",
                content=[_skill_use("s1", "code-review"), _agent_use("a1", "staff-sdet")],
            ),
        ])
        _mod.subagent_mix.cmd_cost_counts(_cost_counts_args(
            this_repo=True, branches="my-secret-branch-name", this_repo_slugs=["-home-user-testrepo"],
        ))
        out = capsys.readouterr().out
        assert "| code-review | 1 |" in out
        assert "| staff-sdet | 1 |" in out
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

    @pytest.mark.parametrize("subcommand", ["cost-counts", "period-cost"])
    def test_top_level_config_dir_message_omits_flag_recommendation(self, monkeypatch, tmp_path, capsys, subcommand):
        """cost-counts and period-cost register no --config-dir flag of their
        own, unlike every other _SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR
        member -- the shared refusal message must not recommend a flag that
        doesn't exist on their own parsers."""
        other_account = tmp_path / "other-account"
        (other_account / "projects").mkdir(parents=True)
        monkeypatch.setattr(
            sys, "argv", ["transcript-analysis.py", "--config-dir", str(other_account), subcommand],
        )
        with pytest.raises(SystemExit) as exc_info:
            _mod.main()
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert subcommand in err
        assert "use that instead" not in err
        assert "with no override" in err
