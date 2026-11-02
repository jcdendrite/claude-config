import re
import subprocess
from pathlib import Path

import pytest
from transcript_analysis import redaction, scope

from .conftest import _asst, _priced, _user_msg, _write_jsonl, _write_subagent_jsonl


class TestProjectFamily:
    def test_raw_slug_with_worktree_suffix_collapses_to_base_slug(self):
        assert redaction._project_family("-home-u-repo-a--claude-worktrees-branch-x") == "-home-u-repo-a"

    def test_raw_slug_without_worktree_suffix_returns_unchanged(self):
        assert redaction._project_family("-home-u-repo-a") == "-home-u-repo-a"


class TestRootScopedDisplayLabel:
    """Direct unit coverage of redaction._root_scoped_display_label --
    cmd_subagents/cmd_subagent_mix exercise it only through their own
    redacted/disclosed output, so this class pins the disclosed path and its
    never-writes-into-redact_map invariant on their own."""

    @pytest.mark.parametrize("kind", ["branch", "agent-type"])
    def test_disclosed_label_is_account_prefixed_raw_value_and_leaves_map_empty(self, kind):
        redact_map: dict[tuple[int, str], str] = {}
        label = redaction._root_scoped_display_label(kind, 1, "feat-x", redact_map, disclose=True)
        assert label == "account-1/feat-x"
        assert redact_map == {}

    @pytest.mark.parametrize("kind", ["branch", "agent-type"])
    def test_non_disclosed_label_delegates_to_assign_root_scoped_redact_label(self, kind):
        redact_map: dict[tuple[int, str], str] = {}
        label = redaction._root_scoped_display_label(kind, 1, "feat-x", redact_map, disclose=False)
        assert label == redaction._assign_root_scoped_redact_label(kind, 1, "feat-x", {})
        assert redact_map == {(1, "feat-x"): label}

    def test_disclosed_call_interleaved_between_redacted_calls_does_not_shift_counter(self):
        """The direct proof of the "never writes into redact_map" invariant
        -- no integration test pins this unconditionally, since a real
        multi-root corpus can't isolate the counter position from the data
        it's built from."""
        redact_map: dict[tuple[int, str], str] = {}
        first = redaction._root_scoped_display_label("branch", 1, "a", redact_map, disclose=False)
        redaction._root_scoped_display_label("branch", 1, "disclosed-b", redact_map, disclose=True)
        second = redaction._root_scoped_display_label("branch", 1, "c", redact_map, disclose=False)
        assert first == "account-1/branch-1"
        assert second == "account-1/branch-2"

    def test_disclosed_and_redacted_labels_share_the_account_prefix_format(self):
        """Format-drift pin: a future change to either function's namespace
        string must fail here, substituting for extracting the format
        string into a third shared helper."""
        redact_map: dict[tuple[int, str], str] = {}
        disclosed = redaction._root_scoped_display_label("branch", 3, "feat-x", redact_map, disclose=True)
        redacted = redaction._assign_root_scoped_redact_label("branch", 3, "feat-y", {})
        assert disclosed.split("/", 1)[0] == redacted.split("/", 1)[0] == "account-3"

    def test_disclosed_label_strips_control_characters(self):
        """A gitBranch value is transcript-sourced, not git-validated -- an
        OSC-injection payload (the same fixture shape used for
        _format_cost_ledger_row's own control-character test) must not reach
        the disclosed label raw."""
        redact_map: dict[tuple[int, str], str] = {}
        label = redaction._root_scoped_display_label(
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
        label = redaction._root_scoped_display_label("branch", 1, "feature/foo", redact_map, disclose=True)
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
        redaction._repo_tracked_agent_type_names.cache_clear()
        yield
        redaction._repo_tracked_agent_type_names.cache_clear()

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
        monkeypatch.setattr(redaction, "_REPO_AGENT_DEFINITIONS_DIR", agents_dir)

    def test_tracked_md_stem_is_allowlisted(self, tmp_path, monkeypatch):
        agents_dir = self._init_agents_repo(tmp_path, tracked=["my-agent"])
        self._patch_dir(monkeypatch, agents_dir)
        assert "my-agent" in redaction._repo_tracked_agent_type_names()

    def test_untracked_md_file_in_same_directory_is_not_allowlisted(self, tmp_path, monkeypatch):
        agents_dir = self._init_agents_repo(tmp_path, tracked=["tracked-agent"], untracked=["scratch-agent"])
        self._patch_dir(monkeypatch, agents_dir)
        names = redaction._repo_tracked_agent_type_names()
        assert "tracked-agent" in names
        assert "scratch-agent" not in names

    def test_differently_cased_query_against_tracked_stem_is_excluded(self, tmp_path, monkeypatch):
        agents_dir = self._init_agents_repo(tmp_path, tracked=["code-writer"])
        self._patch_dir(monkeypatch, agents_dir)
        names = redaction._repo_tracked_agent_type_names()
        assert "code-writer" in names
        assert "Code-Writer" not in names

    def test_directory_outside_any_git_repo_yields_built_ins_alone(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / "not-a-repo"
        agents_dir.mkdir()
        self._patch_dir(monkeypatch, agents_dir)
        assert redaction._repo_tracked_agent_type_names() == redaction._BUILT_IN_AGENT_TYPES

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
        assert redaction._repo_tracked_agent_type_names().issuperset(redaction._BUILT_IN_AGENT_TYPES)

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
        assert redaction._repo_tracked_agent_type_names() == redaction._BUILT_IN_AGENT_TYPES

    def test_git_call_timeout_falls_back_to_built_ins_only(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        self._patch_dir(monkeypatch, agents_dir)

        def boom(cmd, *a, **k):
            raise subprocess.TimeoutExpired(cmd, 10)
        monkeypatch.setattr(subprocess, "run", boom)
        assert redaction._repo_tracked_agent_type_names() == redaction._BUILT_IN_AGENT_TYPES

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
        names = redaction._repo_tracked_agent_type_names()
        assert "top-level-agent" in names
        assert "nested-agent" not in names

    def test_non_ascii_tracked_filename_is_allowlisted(self, tmp_path, monkeypatch):
        """Pins the docstring's -z rationale: without -z, git quotes and
        escapes unusual path names, corrupting the stem."""
        agents_dir = self._init_agents_repo(tmp_path, tracked=["café-agent"])
        self._patch_dir(monkeypatch, agents_dir)
        assert "café-agent" in redaction._repo_tracked_agent_type_names()

    def test_real_agents_directory_allowlists_code_writer(self):
        """The ticket's own motivating scenario, against the real,
        unmonkeypatched agents/ directory -- deriving the name dynamically
        (next(dir.glob('*.md')).stem) would make both this test's intent
        and its failure message unreadable, so the name is hardcoded; the
        unit tests above already carry the "real derivation works" fact."""
        assert "code-writer" in redaction._repo_tracked_agent_type_names()


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
        assert redaction._corpus_fingerprint(two_root_map) == redaction._corpus_fingerprint(one_root_map)


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

        ordinals = scope._redaction_ordinals([root_a, root_b])
        ordinal_a, ordinal_b = ordinals[root_a.resolve()], ordinals[root_b.resolve()]

        redact_map = redaction._build_redact_map([root_a, root_b])

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

        redact_map = redaction._build_redact_map([root])
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

        redact_map = redaction._build_redact_map([root])
        assert redact_map == {
            "aaa-phantom": "private-project-1",
            "zzz-phantom": "private-project-2",
        }
