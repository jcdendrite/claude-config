"""The review-orchestrator protocol and agent body, the routing gate's unrouted args, and review-pr."""
from __future__ import annotations

import json
import re

import pytest
from _skill_md_helpers import (
    _AGENTS_DIR,
    _CODE_REVIEW_STEP_0_1_HEADING,
    _agent_body,
    _assert_citation_resolves_to_heading,
    _heading_section_text,
    _skill_file,
)

# pyproject.toml's pythonpath also puts claude/.claude/tests on the import
# path, where these shared test helpers live.
from helpers import (
    REPO_ROOT,
    normalize_heading,
)

_SUBAGENT_DELEGATION_GATE_LOOP_HEADING = "### Exception: gate/review loops stay orchestrator-driven"
_REVIEW_ORCHESTRATOR_RETURN_ITEMS = ("Fix paths:", "Marker:", "Not addressed:", "HALT:")
_REVIEW_ORCHESTRATOR_DESIGN_RECORD = (
    "docs/design-decisions/review-orchestrator-gate-release-decoupling.md"
)
_ROUTING_GATE_DESIGN_RECORD = "docs/design-decisions/review-orchestrator-routing-gate.md"
_CODE_REVIEW_OUTPUT_FORMAT_HEADING = "## Output format"

# The `review-orchestrator` agent body and the dispatching session's protocol
# (subagent-delegation's gate/review-loop exception) share one return contract,
# and the agent body in turn cites the `code-review` skill's headings and
# that same gate/review-loop heading.
# Renaming either end of a boundary fails below. Every other rule in the
# protocol is deliberately unpinned.


def _review_orchestrator_protocol_section() -> str:
    return _heading_section_text(
        _skill_file("subagent-delegation"), _SUBAGENT_DELEGATION_GATE_LOOP_HEADING
    )


def test_review_orchestrator_protocol_marker_check_is_the_whole_allowlisted_command() -> None:
    settings = json.loads((REPO_ROOT / "claude/.claude/settings.json").read_text())
    marker_check_rules = [
        rule
        for rule in settings["permissions"]["allow"]
        if "marker.sh check code-review" in rule
    ]
    assert len(marker_check_rules) == 1, (
        "settings.json permissions.allow must list exactly one rule for "
        f"`marker.sh check code-review`, found {marker_check_rules!r}. The "
        "dispatch protocol quotes that rule's command verbatim, so a second "
        "spelling leaves it without a single allowlisted form to quote. See "
        f"{_REVIEW_ORCHESTRATOR_DESIGN_RECORD}."
    )
    marker_check_command = " ".join(
        re.fullmatch(r"Bash\((.*)\)", marker_check_rules[0]).group(1).split()
    )

    section = _review_orchestrator_protocol_section()
    # The whole backtick span, so text appended inside it (`; echo $?`) fails.
    assert f"`{marker_check_command}`" in section, (
        f"subagent-delegation/SKILL.md {_SUBAGENT_DELEGATION_GATE_LOOP_HEADING!r} "
        f"no longer quotes {marker_check_command!r} as its own complete code span. "
        "The parent's marker read must be exactly the command permissions.allow "
        "pre-approves, so it runs with no prompt and the marker-script-shape gate "
        f"accepts it. See {_REVIEW_ORCHESTRATOR_DESIGN_RECORD}."
    )


@pytest.mark.parametrize(
    "phrase, invariant",
    [
        pytest.param(
            ":(top,literal)",
            "staged fix paths must be root-anchored literal pathspecs, so neither "
            "the working directory nor pathspec magic changes what is staged",
            id="staging-uses-root-anchored-literal-pathspec",
        ),
        pytest.param(
            "no single or double quote",
            "the staging step must refuse a path holding a quote, which no benign "
            "dogfood round exercises",
            id="staging-refuses-quoted-paths",
        ),
        pytest.param(
            "only when the user explicitly asks",
            "the agent is used only on an explicit user request until the "
            "post-merge dogfood passes, and a routing-gate denial on a key-on "
            "account counts as that request",
            id="agent-used-only-on-explicit-request",
        ),
        pytest.param(
            "a denial from the review-orchestrator routing gate is that request",
            "the explicit-request sentence names the gate whose denial counts "
            "as that request",
            id="request-sentence-names-the-routing-gate",
        ),
        pytest.param(
            'at once, invoking the skill with args "unrouted" without the quotation '
            "marks, and run the rest of "
            "this review loop the same way until a clean round writes the marker",
            "the HALT fallback must pass the escape word the routing gate lets "
            "through, and must carry it through the rest of that loop, or a "
            "key-on fallback's fix re-run is denied and routed to the agent that "
            "just HALTed. The span also pins the bound, so a later trim cannot "
            "leave the carry without an end, which would run every later loop inline",
            id="halt-fallback-passes-unrouted-for-the-rest-of-the-loop",
        ),
        pytest.param(
            "Review-findings persistence",
            "the parent runs the code-review skill's findings-persistence step, "
            "which the agent skips, so the protocol must still name it",
            id="parent-runs-findings-persistence",
        ),
    ],
)
def test_review_orchestrator_protocol_section_carries_phrase(
    phrase: str, invariant: str
) -> None:
    assert phrase in _review_orchestrator_protocol_section(), (
        f"subagent-delegation/SKILL.md {_SUBAGENT_DELEGATION_GATE_LOOP_HEADING!r} "
        f"no longer contains {phrase!r}: {invariant}. "
        f"See {_REVIEW_ORCHESTRATOR_DESIGN_RECORD}."
    )


_REVIEW_PR_STEP5_HEADING = "## Step 5 — Line-level pass"


@pytest.mark.parametrize(
    "skill, heading, phrase, invariant",
    [
        pytest.param(
            "review-pr",
            _REVIEW_PR_STEP5_HEADING,
            'with args "unrouted <path>" naming that file',
            "the line-level pass must lead its args with the word the routing "
            "gate lets through, or it is denied once and retried inline",
            id="review-pr-line-level-pass-passes-unrouted",
        ),
        pytest.param(
            "code-review",
            "## Ripple effect triage",
            "the path is what follows that word",
            "the skill must take the diff-file path after the leading word, or "
            "every diff-file pass hands comment-discipline-reviewer the word "
            "instead of the path",
            id="code-review-reads-the-path-after-the-unrouted-word",
        ),
    ],
)
def test_code_review_args_carry_unrouted_before_path(
    skill: str, heading: str, phrase: str, invariant: str
) -> None:
    """The review-pr row pins a span unique to the line-level-pass sentence,
    which keeps a dropped word from becoming routine cost. The code-review row
    pins that the skill reads the diff-file path after the leading word."""
    assert phrase in _heading_section_text(_skill_file(skill), heading), (
        f"{skill}/SKILL.md {heading!r} no longer contains {phrase!r}: {invariant}. "
        f"See {_ROUTING_GATE_DESIGN_RECORD}."
    )


_CUMULATIVE_PASS_SENTENCE = re.compile(
    r"Run `/code-review` against that diff.*?\(`unrouted` keeps this pass in this session\)"
)


def test_ready_for_review_unrouted_args_are_scoped_to_the_cumulative_pass_sentence() -> None:
    """Only the cumulative pass carries the word. A fix commit's staged-diff
    round stays routed, so `unrouted` anywhere else in the skill would make
    every routine round run inline."""
    skill_text = " ".join(_skill_file("ready-for-review").read_text(encoding="utf-8").split())
    cumulative_pass_sentence = _CUMULATIVE_PASS_SENTENCE.search(skill_text)
    assert cumulative_pass_sentence, (
        "ready-for-review/SKILL.md no longer contains the cumulative-pass sentence "
        "from 'Run `/code-review` against that diff' to "
        "'(`unrouted` keeps this pass in this session)'. "
        f"See {_ROUTING_GATE_DESIGN_RECORD}."
    )
    assert 'args "unrouted <path>"' in cumulative_pass_sentence.group(0), (
        "the cumulative pass must lead its args with the word the routing gate "
        "lets through, or it is denied once and retried inline. "
        f"See {_ROUTING_GATE_DESIGN_RECORD}."
    )
    remainder = _CUMULATIVE_PASS_SENTENCE.sub("", skill_text)
    assert "unrouted" not in remainder, (
        "ready-for-review/SKILL.md carries `unrouted` outside the "
        "cumulative-pass sentence, so a routine fix-commit round would run "
        f"inline. See {_ROUTING_GATE_DESIGN_RECORD}."
    )


@pytest.mark.parametrize("item", _REVIEW_ORCHESTRATOR_RETURN_ITEMS)
def test_review_orchestrator_return_item_named_at_both_ends(item: str) -> None:
    agent_body = " ".join(_agent_body("review-orchestrator").split())
    for owner, text in (
        (
            "subagent-delegation/SKILL.md's gate/review-loop section",
            _review_orchestrator_protocol_section(),
        ),
        ("review-orchestrator's agent body", agent_body),
    ):
        assert item in text, (
            f"{owner} no longer names the return item {item!r}. The agent "
            "reports it and the dispatching session acts on it, so renaming "
            "it at one end leaves the other end reading a field that no "
            f"longer exists. See {_REVIEW_ORCHESTRATOR_DESIGN_RECORD}."
        )


@pytest.mark.parametrize(
    "cited_target, cited_heading",
    [
        ("code-review/SKILL.md", normalize_heading(_CODE_REVIEW_OUTPUT_FORMAT_HEADING)),
        ("code-review/SKILL.md", "Review-findings persistence"),
        (
            "subagent-delegation/SKILL.md",
            normalize_heading(_SUBAGENT_DELEGATION_GATE_LOOP_HEADING),
        ),
    ],
)
def test_review_orchestrator_agent_body_cites_skill_heading_that_exists(
    cited_target: str,
    cited_heading: str,
) -> None:
    # The agent body sits outside test_skill_citations_resolve_to_real_headings'
    # corpus, so its citations into the code-review and subagent-delegation
    # skills resolve only here.
    _assert_citation_resolves_to_heading(
        _AGENTS_DIR / "review-orchestrator.md",
        cited_target,
        cited_heading,
        repo_root=REPO_ROOT,
    )


def test_review_orchestrator_resume_path_names_ledger_show_in_code_review_step_0_1() -> None:
    section_text = _heading_section_text(
        _skill_file("code-review"), _CODE_REVIEW_STEP_0_1_HEADING
    )
    assert "review-ledger.sh show" in section_text, (
        f"code-review/SKILL.md's {_CODE_REVIEW_STEP_0_1_HEADING!r} section no longer "
        "names `review-ledger.sh show`, but review-orchestrator's body tells the "
        "agent to take that step's resume path on its first round. "
        f"See {_REVIEW_ORCHESTRATOR_DESIGN_RECORD}."
    )


def test_review_orchestrator_return_spawn_decisions_line_exists_in_code_review_output_format() -> None:
    section_text = _heading_section_text(
        _skill_file("code-review"), _CODE_REVIEW_OUTPUT_FORMAT_HEADING
    )
    assert "Spawn decisions:" in section_text, (
        f"code-review/SKILL.md's {_CODE_REVIEW_OUTPUT_FORMAT_HEADING!r} section no "
        "longer carries the `Spawn decisions:` line that review-orchestrator's "
        "return relays verbatim. "
        f"See {_REVIEW_ORCHESTRATOR_DESIGN_RECORD}."
    )


_REVIEW_PR_STEP_7_HEADING = "## Step 7 — Synthesize and record completion"


@pytest.mark.parametrize(
    "phrase, heading, consequence",
    [
        pytest.param(
            "report findings, change nothing, write no completion marker, edit no PR body",
            None,
            "a control that review-pr keeps itself rather than deferring to Agent Core.",
            id="standing-override-report-only",
        ),
        pytest.param(
            "apply a correction only after verifying it",
            _REVIEW_PR_STEP_7_HEADING,
            "a control that review-pr keeps itself rather than deferring to Agent Core.",
            id="verify-before-apply-correction",
        ),
        pytest.param(
            "replace the leading `agent-reviews` with the directory it prints, so each "
            "file is `<printed directory>/<agent-name>-<suffix>.md`",
            None,
            "the guard against a reviewer spawned from the main tree being given a "
            "relative `findings_path`, having its findings-file `Write` denied by the "
            "worktree gate, and losing its findings (REFERENCES.md § 'Why spawned "
            "reviewers write findings to a temporary directory').",
            id="reviewer-findings-dir",
        ),
        pytest.param(
            "reports a write whose file you cannot `Read`, or reports a path that "
            "names a different file, has not reviewed: re-dispatch it once with no "
            "`findings_path`",
            None,
            "the only fail-closed rule for a reviewer with no retrievable findings: "
            "if any one of its two triggers (an unreadable file, a mismatched reported "
            "path) or its re-dispatch action is lost, a reviewer whose findings cannot "
            "be read is counted as having reviewed, so its findings are silently "
            "absent from the review.",
            id="reviewer-findings-fallback",
        ),
        pytest.param(
            "`Read` only the path you handed that reviewer",
            None,
            "the guard against a steered reviewer's pointer line moving the session's "
            "`Read` to a file of the reviewer's choosing.",
            id="reviewer-findings-handed-path",
        ),
        pytest.param(
            "before any reviewer spawns, run the script below",
            None,
            "the instruction that obtains the findings directory: without it the "
            "session has no printed directory to put in place of `agent-reviews`, so "
            "reviewers keep the relative `findings_path` the worktree gate denies "
            "from the main tree.",
            id="reviewer-findings-dir-creation",
        ),
    ],
)
def test_review_pr_skill_carries_retained_control(
    phrase: str, heading: str | None, consequence: str
) -> None:
    """Wiring-presence tripwire: review-pr/SKILL.md carries the report-only
    standing override, the reviewer-findings-directory creation and rewrite,
    the re-dispatch rule, the handed-path `Read` rule, and the
    verify-before-apply rule. The override and the four findings-directory
    rules sit above the file's `##` headings, so they are checked file-wide;
    the verify rule is checked in step 7's consult section. Claims presence
    only, not that the agent obeys it."""
    skill_path = _skill_file("review-pr")
    if heading is None:
        searched_text = " ".join(skill_path.read_text().split())
        searched_where = "review-pr/SKILL.md"
    else:
        searched_text = _heading_section_text(skill_path, heading)
        searched_where = f"review-pr/SKILL.md's {heading!r} section"
    assert phrase in searched_text, (
        f"{searched_where} no longer carries {phrase!r}, {consequence}"
    )


def test_review_pr_skill_mentions_findings_path_only_in_the_preamble() -> None:
    """The literal `findings_path` token must appear somewhere above the first
    `## ` heading of review-pr/SKILL.md, where the standing override sits. It
    must appear on no line under a `## ` heading, because a step-level
    instruction there could contradict the override's reviewer findings
    directory. The rest of the preamble is unchecked. Only the literal token is
    scanned, so a paraphrase without it passes."""
    skill_lines = _skill_file("review-pr").read_text().splitlines()
    first_section_index = next(
        (index for index, line in enumerate(skill_lines) if line.startswith("## ")),
        len(skill_lines),
    )
    preamble_lines = skill_lines[:first_section_index]
    section_lines = skill_lines[first_section_index:]
    assert any("findings_path" in line for line in preamble_lines), (
        "review-pr/SKILL.md's preamble ahead of the first `## ` heading no longer "
        "mentions `findings_path`. Either the standing override that hands "
        "reviewers a findings file path is gone from the preamble, or a `## ` "
        "heading now sits above it so it falls under a section."
    )
    stray_lines = [line for line in section_lines if "findings_path" in line]
    assert not stray_lines, (
        "review-pr/SKILL.md mentions `findings_path` under a `##` section: "
        f"{stray_lines!r}. A step-level `findings_path` instruction can "
        "contradict the override's reviewer findings directory."
    )
