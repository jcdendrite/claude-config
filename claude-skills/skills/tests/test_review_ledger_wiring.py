"""Review-ledger literals, block delimiters and prose controls that skill and agent bodies carry."""
from __future__ import annotations

import re
import subprocess

import pytest
from _skill_md_helpers import (
    _AGENTS_DIR,
    _GLOBAL_CLAUDE_MD,
    _heading_section_text,
    _skill_body,
    _skill_file,
)

# pyproject.toml's pythonpath also puts claude/.claude/tests on the import
# path, where these shared test helpers live.
from helpers import SCRIPTS_DIR

# Single source of truth for SKILL.md structural rules — the commit-gate hook
# shells out to the same module. pyproject.toml's [tool.pytest.ini_options]
# pythonpath puts plugins/skill-management/scripts and evals/ on the import path.
from validate_skill_structure import parse_frontmatter


class TestReviewLedgerAuthoringEffortLiteral:
    """Asserts code-review's `--authoring-effort` literal equals
    `code-writer`'s own `effort:` frontmatter. Effort is subagent-definition
    frontmatter, not a per-dispatch parameter, so a literal that drifted from
    the agent's actual value would silently mislabel every ledger line."""

    def test_authoring_effort_literal_matches_code_writer_frontmatter(self):
        # Substring check, not a regex extraction -- catches drift on either
        # side without hand-parsing prose text (test-conventions §9).
        code_writer_effort = parse_frontmatter(_AGENTS_DIR / "code-writer.md")["effort"]
        literal = f"--authoring-effort {code_writer_effort}"
        skill_body = _skill_body("code-review")
        assert literal in skill_body, (
            f"code-review/SKILL.md's --authoring-effort literal must match "
            f"code-writer.md's own effort: frontmatter ({code_writer_effort!r})"
        )
        # An exact count, not a bare substring check, catches a stale
        # literal on any one of the three known call sites even when the
        # others are correct.
        assert skill_body.count(literal) == 3, (
            f"code-review/SKILL.md must carry {literal!r} at exactly its three "
            "known --authoring-effort call sites (Step 0.1 CLEAN short-circuit, "
            "main finding append, main-flow CLEAN append) -- found "
            f"{skill_body.count(literal)}"
        )


class TestReviewLedgerReviewerLaneLiteral:
    """The per-finding append template carries `--reviewer '<lane>'` once, in the
    code span that also names the ADDRESS|DEFER|SETTLED dispositions, so moving
    the flag onto the CLEAN template, or duplicating it, fails."""

    def test_reviewer_flag_sits_once_on_the_per_finding_append_template(self):
        skill_body = _skill_body("code-review")
        literal = "--reviewer '<lane>'"

        assert skill_body.count(literal) == 1, (
            f"code-review/SKILL.md must carry {literal!r} exactly once, on the per-finding append "
            f"template -- found {skill_body.count(literal)}"
        )
        template_span = re.search(r"`([^`\n]*" + re.escape(literal) + r"[^`\n]*)`", skill_body)
        assert template_span is not None and "--disposition ADDRESS|DEFER|SETTLED" in template_span.group(1), (
            f"{literal!r} must sit in the inline code span of the ADDRESS|DEFER|SETTLED append template"
        )


_REVIEW_LEDGER_LIB = SCRIPTS_DIR / "_review-ledger-lib.sh"
_CODE_REVIEW_BLOCK_DELIMITER_PATTERN = re.compile(r"<!-- code-review:[a-z]+:(?:start|end) -->")


def _review_ledger_block_delimiters() -> tuple[str, str]:
    """The PR-body block's start and end delimiters, read by sourcing the
    ledger library that renders them. Fails at the read, with the shell's
    stderr, when a constant is unset, empty or not shaped like an HTML-comment
    delimiter, so a caller's `in` check is never run against a degenerate
    string."""
    completed = subprocess.run(
        [
            "bash",
            "-c",
            'set -u; . "$1" && printf "%s\\n%s\\n" "$_REVIEW_LEDGER_DELIM_START" "$_REVIEW_LEDGER_DELIM_END"',
            "bash",
            str(_REVIEW_LEDGER_LIB),
        ],
        capture_output=True,
        text=True,
    )
    delimiters = completed.stdout.splitlines()
    assert completed.returncode == 0 and len(delimiters) == 2 and all(delimiters), (
        f"reading _REVIEW_LEDGER_DELIM_START/_END from {_REVIEW_LEDGER_LIB} gave "
        f"exit {completed.returncode}, stdout {completed.stdout!r}, stderr {completed.stderr!r}"
    )
    start, end = delimiters
    assert (
        _CODE_REVIEW_BLOCK_DELIMITER_PATTERN.fullmatch(start)
        and _CODE_REVIEW_BLOCK_DELIMITER_PATTERN.fullmatch(end)
        and start != end
    ), f"{_REVIEW_LEDGER_LIB} renders {start!r} / {end!r}, not two distinct `<!-- code-review:<name>:start|end -->` delimiters"
    return start, end


class TestReviewLedgerBlockDelimiters:
    """`review-ledger.sh render` replaces the PR-body block located by its
    delimiters, and `/code-review` and `pr-description` each name the same
    literals in prose. Drift in the library's constants strands the old block
    and makes the next render append a second one. Drift in a SKILL.md literal
    leaves `/code-review` unable to recognize an existing block and leaves the
    block unprotected by `pr-description`'s lift-out carve-outs.
    """

    @pytest.mark.parametrize("skill_name", ["code-review", "pr-description"])
    def test_skill_delimiter_literals_equal_the_script_constants(self, skill_name: str) -> None:
        start, end = _review_ledger_block_delimiters()

        named_literals = set(_CODE_REVIEW_BLOCK_DELIMITER_PATTERN.findall(_skill_body(skill_name)))

        assert named_literals == {start, end}, (
            f"{skill_name}/SKILL.md names {sorted(named_literals)} as block delimiters; "
            f"the review-ledger library renders {[start, end]}"
        )

    @pytest.mark.parametrize("heading", ["## Prose tightening pass", "## Checks"])
    def test_pr_description_carve_outs_name_the_delimiters(self, heading: str) -> None:
        start, end = _review_ledger_block_delimiters()

        section_text = _heading_section_text(_skill_file("pr-description"), heading)

        assert start in section_text and end in section_text, (
            f"pr-description/SKILL.md {heading!r} must name the block by both delimiters, "
            f"{start!r} and {end!r}"
        )


_REVIEW_LEDGER_PROSE_CONTROLS = [
    pytest.param(
        "code-review", "## Ripple effect triage",
        "from the repo root, run `~/.claude/scripts/review-ledger.sh render --out agent-reviews/review-ledger-<suffix>.md`",
        id="digest-render-from-repo-root-into-agent-reviews",
    ),
    pytest.param(
        "code-review", "## Ripple effect triage",
        "every round, spawn or not, from the repo root, run `~/.claude/scripts/review-ledger.sh render --out",
        id="digest-render-runs-every-round-whether-or-not-anything-spawns",
    ),
    pytest.param(
        "code-review", "## Ripple effect triage",
        "or before the digest `render` below when nothing spawns, run `~/.claude/scripts/findings-path-suffix.sh` once",
        id="suffix-is-generated-before-the-digest-render-when-nothing-spawns",
    ),
    pytest.param(
        "code-review", "## Ripple effect triage",
        "`Read` it before dispositioning and pass its path to every spawn",
        id="parent-reads-the-digest-before-dispositioning",
    ),
    pytest.param(
        "code-review", "## Ripple effect triage",
        "A spawn never invokes `review-ledger.sh` or `marker.sh`",
        id="spawn-prompt-never-invokes-ledger-or-marker-scripts",
    ),
    pytest.param(
        "code-review", "## Finding disposition",
        "the consult never invokes `review-ledger.sh` or `marker.sh`",
        id="consult-prompt-never-invokes-ledger-or-marker-scripts",
    ),
    pytest.param(
        "code-review", "## Finding disposition",
        "A keep \"for now\" or \"just this file\", a decline, or an ask to be asked again is logged without `--carry-forward`.",
        id="limited-or-declined-keep-is-logged-without-carry-forward",
    ),
    pytest.param(
        "code-review", "## Review-narrative ledger",
        "An engineer quote keeps its qualifying clauses, or the engineer is asked for a shorter statement.",
        id="engineer-quote-keeps-qualifiers",
    ),
    pytest.param(
        "code-review", "## Review-narrative ledger",
        "An ADDRESS, DEFER or SETTLED append that still fails after the correction and retry above, a non-zero "
        "PR-body `render`, or a `gh pr edit` that fails "
        "for any reason, including a denial by the redaction or escaped-backtick gate, is a blocking stop",
        id="any-pr-edit-failure-is-a-blocking-stop",
    ),
    pytest.param(
        "code-review", "## Review-narrative ledger",
        "A rejected carry is not a failure.",
        id="rejected-carry-is-not-a-failure",
    ),
    pytest.param(
        "code-review", "## Review-narrative ledger",
        "After the superseding row, re-run the PR-open path.",
        id="gate-denial-recovery-reruns-the-pr-open-path",
    ),
    pytest.param(
        "code-review", "## Review-narrative ledger",
        "The orchestrator never edits a stored quote, a rendered row or the body file's block.",
        id="gate-denial-recovery-never-edits-stored-text",
    ),
    pytest.param(
        "code-review", "## Review-narrative ledger",
        "carrying the engineer's own restated words after they see the denial",
        id="gate-denial-recovery-uses-the-engineers-restated-words",
    ),
    pytest.param(
        "code-review", "## Review-narrative ledger",
        "A denied carry row leaves only by superseding its decision",
        id="gate-denial-recovery-denied-carry-row-route",
    ),
    pytest.param(
        "code-review", "## Review-narrative ledger",
        "Pass every value you fill in, paths included, in single quotes, writing an embedded `'` as `'\\''` "
        "and never using double quotes",
        id="filled-in-flag-values-are-single-quoted",
    ),
    pytest.param(
        "code-review", "## Review-narrative ledger",
        "--round <N> --source '<file:line>' [--decided-by",
        id="append-template-requires-and-quotes-the-source-path",
    ),
    pytest.param(
        "code-review", "## Review-narrative ledger",
        "--defer-criterion '<name>' --ref '<id>' --relog --cited-line '<file:line>']",
        id="append-template-quotes-criterion-ref-and-cited-line",
    ),
    pytest.param(
        "code-review", "## Finding disposition",
        "Finding | Lane | Disposition | Rationale",
        id="disposition-table-names-the-lane-column",
    ),
    pytest.param(
        "code-review", "## Finding disposition",
        "`skill-tool` marks a Skill-tool review's finding, `unrostered-agent` marks a finding a spawned reviewer "
        "agent outside the roster raised, and `orchestrator` marks any finding no reviewer agent or Skill-tool "
        "review raised.",
        id="lane-cell-defines-the-three-non-roster-literals",
    ),
    pytest.param(
        "code-review", "## Review-narrative ledger",
        "CLEAN and CONSULT take no lanes",
        id="clean-and-consult-appends-take-no-lanes",
    ),
    pytest.param(
        "code-review", "## Review-narrative ledger",
        "as a reopen, a gate-denial recovery and a `--carry-forward` withdrawal do",
        id="relog-is-for-a-row-that-only-re-logs-its-decision",
    ),
    pytest.param(
        "code-review", "## Review-narrative ledger",
        "--finding '<summary>' --disposition ADDRESS|DEFER|SETTLED --rationale '<one line>'",
        id="append-template-quotes-the-finding-and-rationale",
    ),
    pytest.param(
        "code-review", "## Review-narrative ledger",
        "--engineer-quote '<words>'",
        id="append-template-quotes-the-engineer-quote",
    ),
    pytest.param(
        "code-review", "## Review-findings persistence",
        "so never hand-write a row or paraphrase a quote",
        id="block-rows-come-only-from-render",
    ),
    pytest.param(
        "code-review", "## Review-findings persistence",
        "persist nothing here",
        id="no-pr-branch-persists-nothing",
    ),
    pytest.param(
        "code-review", "## Review-findings persistence",
        "from the repo root, run `gh pr view --json body |",
        id="pr-body-render-from-repo-root",
    ),
    pytest.param(
        "code-review", "## Review-findings persistence",
        "render --pr-json - --out agent-reviews/pr-body-<suffix>.md`",
        id="pr-body-render-into-agent-reviews",
    ),
    # `render`'s `changed: <path>` / `unchanged` stdout is pinned in
    # scripts/tests/test_review_ledger_lib.py, not here.
    pytest.param(
        "code-review", "## Review-findings persistence",
        "only after a `changed:` line (`unchanged` needs no edit)",
        id="pr-edit-only-after-changed-line",
    ),
    pytest.param(
        "code-review", "## Review-findings persistence",
        "Run `render` and `gh pr edit` as separate Bash calls, never chained",
        id="render-and-pr-edit-are-separate-bash-calls",
    ),
    pytest.param(
        "ready-for-review", "## 6. Final hygiene recheck (halt on fail)",
        "With a PR open, edited or not, then run the PR-open path in `code-review/SKILL.md` "
        "§ \"Review-findings persistence\": `unchanged` is expected, and `changed:` publishes the ledger's block "
        "over the body's copy. A failed `render` or `gh pr edit` halts this step.",
        id="step-6-runs-the-pr-open-path-whenever-a-pr-is-open",
    ),
    pytest.param(
        "ready-for-review", "## 8. Create PR if missing (skip if PR already exists)",
        "once that re-fetch succeeds run the PR-open path in `code-review/SKILL.md` "
        "§ \"Review-findings persistence\" with that number. A failed re-fetch, a failed `render`, or a failed or "
        "denied `gh pr edit` is a blocking stop per `code-review/SKILL.md` § \"Review-narrative ledger\": name the "
        "PR number, say it exists without the block, and neither launch the CI watch nor report ready.",
        id="step-8-runs-the-pr-open-path-after-the-confirm-refetch",
    ),
    pytest.param(
        "code-review", "## Review-narrative ledger",
        "before dispatching or applying any fix and before review-findings persistence below",
        id="ledger-appends-run-before-any-fix",
    ),
]


class TestReviewLedgerPublishAndInjectionControls:
    """Narrow token pins for the `/code-review` and `/ready-for-review` prose
    that keeps spawns and consults off the ledger and marker scripts, quotes ledger arguments,
    orders a round's ledger appends before its fix, and gates what reaches
    the public PR body. Each token fails a test when a later trim drops the
    control it names, without pinning the surrounding paragraph.
    """

    @pytest.mark.parametrize("skill_name,heading,token", _REVIEW_LEDGER_PROSE_CONTROLS)
    def test_section_names_the_control(self, skill_name: str, heading: str, token: str) -> None:
        section_text = _heading_section_text(_skill_file(skill_name), heading)

        assert token in section_text, f"{skill_name}/SKILL.md {heading!r} no longer contains {token!r}"

    def test_global_instructions_point_a_resumed_session_at_render_for_live_decisions_and_a_bounded_show(self) -> None:
        body = " ".join(_GLOBAL_CLAUDE_MD.read_text().split())

        assert "`~/.claude/scripts/review-ledger.sh render` for this branch's live decisions" in body
        assert "`~/.claude/scripts/review-ledger.sh show | tail -n 15` lists the newest rows" in body

    def test_digest_path_reviewers_read_is_the_path_render_writes(self) -> None:
        section_text = _heading_section_text(_skill_file("code-review"), "## Ripple effect triage")

        read_paths = set(re.findall(r"digest at `([^`]+)`", section_text))
        written_paths = set(re.findall(r"review-ledger\.sh render --out (\S+?)`", section_text))

        assert read_paths and read_paths == written_paths, (
            f"digest read at {sorted(read_paths)} but `render --out` writes {sorted(written_paths)}"
        )
