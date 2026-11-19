"""transcript-analysis's scope sentence, and per-account state-path and stowed-path contracts over skill, agent and doc prose."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from _skill_md_helpers import (
    _AGENTS_DIR,
    _GLOBAL_CLAUDE_MD,
    _PLUGINS_DIR,
    _all_doc_paths,
    _all_skill_md_paths,
    _fence_excluded_by_marker,
    _fenced_code_blocks,
    _skill_body,
)

# Pinned verbatim: transcript-analysis/SKILL.md's "Scope confirmation"
# section sentence. A prose caveat with no enforcement is the same failure
# class as the transcript-config-dirs-unaware caveat that preceded it -- this
# sentence must appear in the skill body exactly, not merely in spirit.
_SCOPE_CONFIRMATION_SENTENCE = (
    "Before quoting a corpus-wide statistic from this toolkit's output, include "
    "the resolved-scope header line verbatim in what you report, and if that "
    "line reads \"1 root (no ~/.claude/transcript-config-dirs declared)\", ask "
    "the user whether other Claude accounts exist before treating the number "
    "as complete."
)


class TestTranscriptAnalysisScopeConfirmationContract:
    """transcript-analysis/SKILL.md must carry its "Scope confirmation"
    section's pinned sentence verbatim -- see _SCOPE_CONFIRMATION_SENTENCE."""

    def test_pinned_scope_confirmation_sentence_present_verbatim(self):
        body = _skill_body("transcript-analysis")
        assert _SCOPE_CONFIRMATION_SENTENCE in body, (
            "transcript-analysis/SKILL.md is missing its pinned scope-"
            f"confirmation sentence verbatim:\n{_SCOPE_CONFIRMATION_SENTENCE!r}"
        )


# A literal ~/.claude/$HOME/.claude/${HOME}/.claude prefix on a per-account-
# state path — a state subdirectory, a log, or claude-config.toml (the one
# state file config-keys.psv's schema-driven keys resolve through) — is a
# functional bug under a non-personal CLAUDE_CONFIG_DIR account, unlike a
# stowed path (agents/, hooks/, rules/, scripts/, skills/), which resolves
# identically under every account; the directory alternation mirrors
# enforce-marker-script-shape.sh's own _marker_shape_match glob pair.
# worktree_required and autonomous_shipping are deliberately excluded —
# those two config-keys.psv keys carry resolution: config-dir-or-home, a
# union with the literal $HOME/.claude location rather than a fully
# migrated-away path (see docs/config-file.md and README's "Worktree
# enforcement").
_PER_ACCOUNT_STATE_PATH_RE = re.compile(
    r"(~|\$HOME|\$\{HOME\})/\.claude/"
    r"(handoffs/|briefs/|issue-triage/|plans/|projects/|sessions/"
    r"|[^/\s\"'`]*-markers/|\.[^/\s\"'`]*\.d/|output-preferences\.md"
    r"|pii-patterns\.md|credential-file-guard\.md|credential-value-patterns\.md"
    r"|data-file-read-guard\.md|private-projects\.md"
    r"|\.permission-prompt-log\.jsonl|\.error-mode-nudge\.log"
    r"|\.handoff-nudge\.log|\.commit-stall-block\.log"
    r"|claude-config\.toml)"
)

# The marker triple (settings.json permission rules, hook command strings,
# and these SKILL.md invocation sites) is deliberately excluded from the
# contract below — see plan-review/SKILL.md's marker.sh comment for why
# these six invocation sites stay literal rather than config-dir-aware.
_MARKER_TRIPLE_SITES = [
    ("respond-pr", "~/.claude/scripts/marker.sh activate respond-pr"),
    ("respond-pr", "~/.claude/scripts/marker.sh deactivate respond-pr"),
    ("plan-review", "~/.claude/scripts/marker.sh activate plan-review"),
    ("plan-review", "~/.claude/scripts/marker.sh resolve-session-id"),
    ("plan-review", "~/.claude/scripts/marker.sh deactivate plan-review"),
    ("plan-review", "~/.claude/scripts/marker.sh write plan-review"),
    ("ready-for-review", "~/.claude/scripts/marker.sh activate ready-for-review"),
    ("ready-for-review", "~/.claude/scripts/marker.sh write ready-for-review"),
    ("ready-for-review", "~/.claude/scripts/marker.sh deactivate ready-for-review"),
    ("ai-instruction-and-memory-files", "~/.claude/scripts/marker.sh activate memory-skill"),
    ("ai-instruction-and-memory-files", "~/.claude/scripts/marker.sh deactivate memory-skill"),
    ("code-review", "~/.claude/scripts/marker.sh write code-review"),
    ("handoff", "~/.claude/scripts/marker.sh activate handoff"),
    ("handoff", "~/.claude/scripts/marker.sh deactivate handoff"),
    ("handoff", "~/.claude/scripts/marker.sh status"),
]


def _all_agent_md_paths() -> list[Path]:
    """Every agent body under claude/.claude/agents/ and plugin agents/ dirs —
    _agent_body() in _skill_md_helpers.py is by-name only, so a corpus-wide scan needs this."""
    paths = sorted(_AGENTS_DIR.glob("*.md"))
    if _PLUGINS_DIR.exists():
        paths += sorted(_PLUGINS_DIR.glob("*/agents/*.md"))
    return paths


def _strip_yaml_frontmatter(text: str) -> str:
    """Drop a leading '---'-delimited frontmatter block so a SKILL.md's or
    agent's deliberately-literal frontmatter `description:` (loads standalone
    for trigger-matching before any body is read, so a <config-dir>
    placeholder would have no definition in scope) doesn't false-fail the
    state-path contract below."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5 :]
    return text


class TestPerAccountStatePathContract:
    """No SKILL.md body, agent body, prose doc, or claude/.claude/CLAUDE.md
    may hold a literal ~/.claude (or $HOME/.claude, ${HOME}/.claude)
    per-account-state path. Covers the $HOME-form bypass a bare
    '~/.claude/' substring check would miss entirely."""

    def test_flags_issue_triage_state_path(self):
        match = _PER_ACCOUNT_STATE_PATH_RE.search("~/.claude/issue-triage/run.md")
        assert match is not None

    def test_does_not_flag_issue_triage_config_dir_prose(self):
        match = _PER_ACCOUNT_STATE_PATH_RE.search("<config-dir>/issue-triage/run.md")
        assert match is None

    def test_flags_claude_config_toml_state_path(self):
        match = _PER_ACCOUNT_STATE_PATH_RE.search("~/.claude/claude-config.toml")
        assert match is not None

    def test_does_not_flag_claude_config_toml_config_dir_prose(self):
        match = _PER_ACCOUNT_STATE_PATH_RE.search("<config-dir>/claude-config.toml")
        assert match is None

    @pytest.mark.parametrize("skill_md_path", _all_skill_md_paths(), ids=lambda p: str(p))
    def test_skill_body_has_no_state_path(self, skill_md_path):
        body = _strip_yaml_frontmatter(skill_md_path.read_text())
        match = _PER_ACCOUNT_STATE_PATH_RE.search(body)
        assert match is None, (
            f"{skill_md_path} contains a hardcoded per-account-state path "
            f"{match.group(0)!r} — use <config-dir>/... (skill/agent prose) "
            "or push resolution into the invoked script via its own "
            "config_dir()/_lib_config_dir call (a runnable command needing "
            "a config-dir-relative data path)"
        )

    @pytest.mark.parametrize("agent_md_path", _all_agent_md_paths(), ids=lambda p: str(p))
    def test_agent_body_has_no_state_path(self, agent_md_path):
        body = _strip_yaml_frontmatter(agent_md_path.read_text())
        match = _PER_ACCOUNT_STATE_PATH_RE.search(body)
        assert match is None, (
            f"{agent_md_path} contains a hardcoded per-account-state path "
            f"{match.group(0)!r} — use <config-dir>/..."
        )

    @pytest.mark.parametrize("doc_path", _all_doc_paths(), ids=lambda p: str(p))
    def test_doc_has_no_state_path(self, doc_path):
        body = doc_path.read_text()
        match = _PER_ACCOUNT_STATE_PATH_RE.search(body)
        assert match is None, (
            f"{doc_path} contains a hardcoded per-account-state path "
            f"{match.group(0)!r} — use <config-dir>/..., with one caveat "
            "sentence per file defining it"
        )

    def test_global_claude_md_has_no_state_path(self):
        body = _GLOBAL_CLAUDE_MD.read_text()
        match = _PER_ACCOUNT_STATE_PATH_RE.search(body)
        assert match is None, (
            f"claude/.claude/CLAUDE.md contains a hardcoded per-account-state "
            f"path {match.group(0)!r} — use <config-dir>/..."
        )

    @pytest.mark.parametrize("skill_name,expected_substring", _MARKER_TRIPLE_SITES)
    def test_marker_triple_site_stays_unmigrated(self, skill_name, expected_substring):
        assert expected_substring in _skill_body(skill_name), (
            f"{skill_name}/SKILL.md no longer contains the literal marker.sh "
            f"invocation {expected_substring!r} — the marker triple "
            "(settings.json allow-rule + enforce-marker-script-shape.sh's "
            "anchor + this literal form) must change in lockstep or not at "
            "all; see plan-review/SKILL.md's marker.sh comment"
        )


# A templated $CLAUDE_CONFIG_DIR expansion, or a <config-dir> prose
# placeholder, landing immediately in front of a scripts/ or hooks/ path --
# every script and hook under claude/.claude/ is stow-shared and
# machine-invariant (resolves into the same checkout under every account), so
# its own call-site path never needs runtime templating. Anchored on the
# literal /scripts/ or /hooks/ segment so the ~25 legitimate per-account
# *state* templating sites (handoffs/, briefs/, sentinels, logs --
# _PER_ACCOUNT_STATE_PATH_RE's own domain) are left untouched.
_STOWED_PATH_TEMPLATING_RE = re.compile(
    r"CLAUDE_CONFIG_DIR[^}]*\}/(?:scripts|hooks)/"
    r"|CLAUDE_CONFIG_DIR/(?:scripts|hooks)/"
    r"|(?<=[<{])config-dir[>}]?/(?:scripts|hooks)/"
)


def _stowed_path_templating_matches(markdown_text: str) -> list[str]:
    """Every _STOWED_PATH_TEMPLATING_RE match outside a
    HOOK_TEST_FIXTURE/HOOK_SCRIPT_CONTENT_EXAMPLE-excluded fenced block.

    Mirrors _trigger_a_matches's fence-exclusion structure (not
    TestPerAccountStatePathContract's plain whole-body search): this
    contract's corpus includes SKILL.md bodies with pytest-executed
    HOOK_TEST_FIXTURE fenced blocks that would otherwise false-positive
    against the regex.
    """
    lines = markdown_text.split("\n")
    excluded_lines: set[int] = set()
    for open_index, content in _fenced_code_blocks(lines):
        if _fence_excluded_by_marker(lines, open_index):
            close_index = open_index + 1 + len(content)
            excluded_lines.update(range(open_index, close_index + 1))
    matches: list[str] = []
    for index, line in enumerate(lines):
        if index in excluded_lines:
            continue
        matches.extend(match.group(0) for match in _STOWED_PATH_TEMPLATING_RE.finditer(line))
    return matches


class TestStowedScriptPathContract:
    """No SKILL.md body, agent body, prose doc, or claude/.claude/CLAUDE.md
    may template $CLAUDE_CONFIG_DIR (or a <config-dir> prose placeholder) in
    front of a scripts/ or hooks/ path. The other half of
    the invariant TestPerAccountStatePathContract guards from the state-path
    direction: stowed paths must be literal, state paths must stay
    config-dir-relative."""

    def test_flags_templated_expansion_in_fenced_block(self) -> None:
        text = '```bash\npython3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/foo.py"\n```'
        assert _stowed_path_templating_matches(text) == [
            'CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/'
        ]

    def test_flags_prose_config_dir_placeholder_outside_any_fence(self) -> None:
        text = "Run `<config-dir>/scripts/marker.sh status` and paste its output."
        assert _stowed_path_templating_matches(text) == ["config-dir>/scripts/"]

    def test_flags_hooks_path_as_well_as_scripts(self) -> None:
        text = "Run `<config-dir>/hooks/nudge-handoff-near-context-cap.sh --check`."
        assert _stowed_path_templating_matches(text) == ["config-dir>/hooks/"]

    def test_flags_brace_free_config_dir_expansion(self) -> None:
        text = "Resolves so `$CLAUDE_CONFIG_DIR/scripts/foo.py` runs."
        assert _stowed_path_templating_matches(text) == ["CLAUDE_CONFIG_DIR/scripts/"]

    def test_does_not_flag_unrelated_path_merely_ending_in_config_dir(self) -> None:
        """"own-config-dir/scripts/build" has no preceding "<" or "${" --
        it isn't a <config-dir> prose placeholder, just a path segment that
        happens to end in the literal word "config-dir"."""
        text = "own-config-dir/scripts/build"
        assert _stowed_path_templating_matches(text) == []

    def test_does_not_flag_marker_excluded_fence(self) -> None:
        text = (
            "<!-- HOOK_TEST_FIXTURE: example -->\n\n"
            '```bash\npython3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/foo.py"\n```'
        )
        assert _stowed_path_templating_matches(text) == []

    def test_does_not_flag_per_account_state_path(self) -> None:
        """A state path (handoffs/, briefs/) templated the same way is the
        legitimate _PER_ACCOUNT_STATE_PATH_RE case, not this contract's --
        the regex is anchored on the literal scripts/ or hooks/ segment,
        never handoffs/ or briefs/."""
        text = 'mkdir -p "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/handoffs"'
        assert _stowed_path_templating_matches(text) == []

    @pytest.mark.parametrize("skill_md_path", _all_skill_md_paths(), ids=lambda p: str(p))
    def test_skill_body_has_no_templated_stowed_path(self, skill_md_path):
        matches = _stowed_path_templating_matches(skill_md_path.read_text())
        assert not matches, (
            f"{skill_md_path} templates $CLAUDE_CONFIG_DIR or <config-dir> in "
            f"front of a scripts/hooks path {matches!r} — every script/hook "
            "under claude/.claude/ is stow-shared and machine-invariant, so "
            "name it literally (~/.claude/scripts/<name> or "
            "~/.claude/hooks/<name>) instead"
        )

    @pytest.mark.parametrize("agent_md_path", _all_agent_md_paths(), ids=lambda p: str(p))
    def test_agent_body_has_no_templated_stowed_path(self, agent_md_path):
        matches = _stowed_path_templating_matches(agent_md_path.read_text())
        assert not matches, (
            f"{agent_md_path} templates $CLAUDE_CONFIG_DIR or <config-dir> in "
            f"front of a scripts/hooks path {matches!r} — name it literally "
            "(~/.claude/scripts/<name> or ~/.claude/hooks/<name>) instead"
        )

    @pytest.mark.parametrize("doc_path", _all_doc_paths(), ids=lambda p: str(p))
    def test_doc_has_no_templated_stowed_path(self, doc_path):
        matches = _stowed_path_templating_matches(doc_path.read_text())
        assert not matches, (
            f"{doc_path} templates $CLAUDE_CONFIG_DIR or <config-dir> in "
            f"front of a scripts/hooks path {matches!r} — name it literally "
            "(~/.claude/scripts/<name> or ~/.claude/hooks/<name>) instead"
        )

    def test_global_claude_md_has_no_templated_stowed_path(self):
        matches = _stowed_path_templating_matches(_GLOBAL_CLAUDE_MD.read_text())
        assert not matches, (
            f"claude/.claude/CLAUDE.md templates $CLAUDE_CONFIG_DIR or "
            f"<config-dir> in front of a scripts/hooks path {matches!r} — "
            "name it literally (~/.claude/scripts/<name> or "
            "~/.claude/hooks/<name>) instead"
        )
