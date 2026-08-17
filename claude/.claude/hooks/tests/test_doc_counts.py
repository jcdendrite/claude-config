"""Lock derivable numeric doc-count claims to disk ground truth.

Each fact registered here asserts that one or more sentences in project
documentation claim a specific count (e.g., "eight reviewer personas",
"Ten bundled skills are disabled"). The test computes the actual count
from the canonical source (the agents roster list, the settings.json)
and fails if the documentation claim diverges.

Why hooks/tests/ instead of tests/ or skills/tests/:
  This module imports REVIEWER_AGENTS from the sibling test_agent_roster.py.
  Moving it out of hooks/tests/ would break that import.

Design:
  - Registry of (ground_truth_fn, occurrences) pairs.
  - Each occurrence is (rel_path_from_repo_root, regex, description).
  - Per occurrence: re.findall(pattern, doc_text) must return exactly one match.
    - Zero matches → sentence was reworded; the claim is no longer detectable.
    - More than one match → duplicate or relocated sentence.
  - The single captured group is the count token (English word or digit).
  - Token parsing: digit string → int(); else lowercase + dict lookup.
    Dict miss raises ValueError naming the unparseable token.
  - Assert claimed == actual with a message naming file, counts, and label.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import pytest
from helpers import CLAUDE_DIR
from test_agent_roster import REVIEWER_AGENTS

# CLAUDE_DIR is defined in helpers.py as Path(__file__).resolve().parent.parent,
# anchored to the stow-source path, not the symlink target (~/.claude/).
# Chain: CLAUDE_DIR (claude/.claude/) → .parent (claude/) → .parent (repo root).
REPO_ROOT = CLAUDE_DIR.parent.parent

# English number words keyed lowercase, one through twenty.
_ENGLISH_NUMBERS: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}


def _parse_count_token(token: str) -> int:
    """Parse a count token (English word or digit string) to an integer.

    Raises ValueError if the token is neither a digit string nor a recognised
    English number word, naming the unparseable token explicitly.
    """
    if token.isdigit():
        return int(token)
    lowered = token.lower()
    if lowered in _ENGLISH_NUMBERS:
        return _ENGLISH_NUMBERS[lowered]
    raise ValueError(
        f"Unparseable count token {token!r}: not a digit string and not in the "
        "English-number dictionary (one..twenty). Add it to _ENGLISH_NUMBERS or "
        "fix the regex capturing group."
    )


class Occurrence(NamedTuple):
    """A single sentence in a doc file that claims a specific count."""

    rel_path: str  # relative to REPO_ROOT
    pattern: str   # regex with exactly one capturing group — the count token
    description: str  # human-readable label for failure messages


class DocCountFact(NamedTuple):
    """A registered fact: a ground-truth function paired with its doc occurrences."""

    ground_truth_fn: Callable[[], int]
    occurrences: list[Occurrence]
    label: str  # human-readable label for the ground-truth source


# ---------------------------------------------------------------------------
# Ground-truth functions
# ---------------------------------------------------------------------------

def _count_reviewer_agents() -> int:
    """Return the number of reviewer agents per the authoritative roster list."""
    return len(REVIEWER_AGENTS)


def _count_skill_overrides_off() -> int:
    """Return the count of skillOverrides entries set to "off" in settings.base.json."""
    settings_path = CLAUDE_DIR / "settings.base.json"
    settings = json.loads(settings_path.read_text())
    return sum(
        1
        for value in settings.get("skillOverrides", {}).values()
        if value == "off"
    )


# Builtin Claude Code skills carried in skillOverrides: name-only despite having
# no repo SKILL.md. Mirrors BUILTIN_NAME_ONLY_SKILLS in
# claude/.claude/skills/tests/test_skills.py — duplicated locally rather than
# imported across test directories, since pytest only adds a collected file's
# own directory to sys.path.
_BUILTIN_NAME_ONLY_SKILLS = {"loop", "simplify"}


def _count_name_only_skills() -> int:
    """Return the count of skillOverrides entries set to "name-only" in
    settings.base.json, excluding builtin skills with no repo SKILL.md."""
    settings_path = CLAUDE_DIR / "settings.base.json"
    settings = json.loads(settings_path.read_text())
    return sum(
        1
        for name, value in settings.get("skillOverrides", {}).items()
        if value == "name-only" and name not in _BUILTIN_NAME_ONLY_SKILLS
    )


_GROUND_EVERY_CHOICE_BULLET = "- **Ground every choice.**"
_GLOBAL_CLAUDE_MD = "claude/.claude/CLAUDE.md"
_NUDGE_HOOK_REL_PATH = "claude/.claude/hooks/nudge-handoff-near-context-cap.sh"


def _count_ground_every_choice_categories() -> int:
    """Return the number of decision categories nested under "Ground every choice".

    Unlike the other ground truths here, the source is prose rather than a
    roster or a config file — the categories exist only as nested list items,
    so this fact compares one part of CLAUDE.md against another part of the
    same file. That is weaker than the sibling facts, which check a doc claim
    against an independent structured source: a single edit that changed both
    the count word and the bullet list would agree while both were wrong. It
    is the only ground truth this claim has, but do not read the registry as
    uniformly independent.

    The scan is bounded at both ends rather than counting indented bullets
    file-wide: it starts at the "Ground every choice" bullet and stops at the
    next top-level list item or heading, so an indented bold list added
    elsewhere in CLAUDE.md cannot silently move this count.

    Stopping at a heading alone would be enough today (this is the last
    bullet in its section) but would break the moment a bullet is appended
    after it, so both terminators are checked.
    """
    text = (REPO_ROOT / _GLOBAL_CLAUDE_MD).read_text()
    anchors = text.count(_GROUND_EVERY_CHOICE_BULLET)
    if anchors != 1:
        raise ValueError(
            f"Expected exactly one {_GROUND_EVERY_CHOICE_BULLET!r} anchor in "
            f"{_GLOBAL_CLAUDE_MD}, found {anchors}. Zero means the bullet was "
            "reworded and this ground truth no longer has a scan start; more "
            "than one means the scan boundary is ambiguous."
        )
    body = text.split(_GROUND_EVERY_CHOICE_BULLET, 1)[1]
    terminator = re.search(r"^(?:- |#)", body, re.MULTILINE)
    if terminator:
        body = body[: terminator.start()]
    return len(re.findall(r"^  - \*\*", body, re.MULTILINE))


def _count_handoff_nudge_threshold_percentage() -> int:
    """Return the handoff-nudge pct-arm percentage from the hook's PCT_THRESHOLD calculation."""
    hook_path = REPO_ROOT / _NUDGE_HOOK_REL_PATH
    text = hook_path.read_text()
    match = re.search(r"PCT_THRESHOLD=\$\(\( CONTEXT_WINDOW \* (\d+) / 100 \)\)", text)
    if match is None:
        raise ValueError(
            "Could not find 'PCT_THRESHOLD=$(( CONTEXT_WINDOW * N / 100 ))' in "
            f"{_NUDGE_HOOK_REL_PATH}; the hook's threshold calculation was "
            "reworded and this ground truth needs updating."
        )
    return int(match.group(1))


def _count_handoff_nudge_abs_cap_default() -> int:
    """Return the handoff-nudge absolute-token cap, derived behaviorally.

    Fires the hook against a synthetic 1M-window-model transcript whose
    estimate exceeds any plausible cap, with HANDOFF_NUDGE_ABS_CAP unset, and
    reads the computed threshold back out of the emitted additionalContext —
    avoiding a second source-scanning regex (on an ABS_CAP= literal) on top
    of the percentage one above.
    """
    hook_path = REPO_ROOT / _NUDGE_HOOK_REL_PATH
    transcript_record = {
        "type": "assistant",
        "message": {
            "model": "claude-sonnet-5",
            "usage": {
                "cache_read_input_tokens": 999_000_000,
                "cache_creation_input_tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            },
        },
    }
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        transcript_path = tmp_path / "t.jsonl"
        transcript_path.write_text(json.dumps(transcript_record) + "\n")
        payload = {
            "session_id": "doc-count-abs-cap-probe",
            "transcript_path": str(transcript_path),
            "hook_event_name": "UserPromptSubmit",
        }
        env = {**os.environ, "HOME": str(tmp_path)}
        env.pop("CLAUDE_CONFIG_DIR", None)
        env.pop("HANDOFF_NUDGE_ABS_CAP", None)
        result = subprocess.run(
            [str(hook_path)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
    match = re.search(r"threshold \((\d+) tokens\)", result.stdout)
    if match is None:
        raise ValueError(
            "Could not find 'threshold (N tokens)' in the hook's emitted "
            f"additionalContext (stdout: {result.stdout!r}); the nudge "
            "message wording changed and this ground truth needs updating."
        )
    return int(match.group(1))


def _count_handoff_nudge_block_after_default() -> int:
    """Return the handoff-nudge hard-block escalation count, derived behaviorally.

    Fires the hook twice (advisory, then a re-arm) with HANDOFF_NUDGE_BLOCK_AFTER
    unset and reads the default back from the hard-block stderr -- a source-scan
    of the fallback literal wouldn't prove the runtime path actually uses it.
    Keep both estimates under 9 digits: the marker's `?????????*` corrupt-value
    guard would otherwise reset the re-arm count instead of exercising it.
    """
    hook_path = REPO_ROOT / _NUDGE_HOOK_REL_PATH

    def _usage_record(cache_read: int) -> dict:
        return {
            "type": "assistant",
            "message": {
                "model": "claude-sonnet-5",
                "usage": {
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                },
            },
        }

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        transcript_path = tmp_path / "t.jsonl"
        transcript_path.write_text(json.dumps(_usage_record(400_000)) + "\n")
        payload = {
            "session_id": "doc-count-block-after-probe",
            "transcript_path": str(transcript_path),
            "hook_event_name": "PostToolBatch",
        }
        env = {**os.environ, "HOME": str(tmp_path)}
        env.pop("CLAUDE_CONFIG_DIR", None)
        env.pop("HANDOFF_NUDGE_BLOCK_AFTER", None)
        subprocess.run(
            [str(hook_path)], input=json.dumps(payload), capture_output=True, text=True, env=env, check=False,
        )
        with transcript_path.open("a") as transcript_fh:
            transcript_fh.write(json.dumps(_usage_record(900_000)) + "\n")
        result = subprocess.run(
            [str(hook_path)], input=json.dumps(payload), capture_output=True, text=True, env=env, check=False,
        )
    match = re.search(r"HANDOFF_NUDGE_BLOCK_AFTER=(\d+)\)", result.stderr)
    if match is None:
        raise ValueError(
            "Could not find 'HANDOFF_NUDGE_BLOCK_AFTER=N)' in the hook's hard-block "
            f"stderr message (stderr: {result.stderr!r}); the message wording "
            "changed and this ground truth needs updating."
        )
    return int(match.group(1))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTERED_FACTS: list[DocCountFact] = [
    DocCountFact(
        ground_truth_fn=_count_reviewer_agents,
        label='len(REVIEWER_AGENTS) from test_agent_roster.py',
        occurrences=[
            Occurrence(
                rel_path="README.md",
                pattern=r"\*\*Reviewer subagents\*\* — (\w+) stack-agnostic personas spawned by",
                description="README.md: reviewer subagent count",
            ),
            Occurrence(
                rel_path="docs/design-decisions.md",
                pattern=r"## 3\. Specialist reviewer roster \((\d+) personas\)",
                description="docs/design-decisions.md: section heading digit",
            ),
            Occurrence(
                rel_path="docs/design-decisions.md",
                pattern=r"(\w+) stack-specific agents \(CISO,",
                description="docs/design-decisions.md: N stack-specific agents prose",
            ),
            Occurrence(
                rel_path="docs/design-decisions.md",
                pattern=r"All (\w+) reviewer agents write structured",
                description="docs/design-decisions.md: All N reviewer agents prose",
            ),
        ],
    ),
    DocCountFact(
        ground_truth_fn=_count_skill_overrides_off,
        label='count of skillOverrides == "off" in settings.json',
        occurrences=[
            Occurrence(
                rel_path="docs/skills.md",
                pattern=r"(\w+) bundled skills are disabled in this repo",
                description="docs/skills.md: N bundled skills disabled",
            ),
        ],
    ),
    DocCountFact(
        ground_truth_fn=_count_ground_every_choice_categories,
        label='count of decision categories nested under "Ground every choice" in claude/.claude/CLAUDE.md',
        occurrences=[
            # Anchored on the bullet's own bold lead-in: a bare
            # "(\w+) categories" also matches Axis 3's "the following content
            # categories are read-only", which would break the
            # exactly-one-match invariant.
            Occurrence(
                rel_path=_GLOBAL_CLAUDE_MD,
                pattern=r"\*\*Ground every choice\.\*\* (\w+) categories of decision",
                description="claude/.claude/CLAUDE.md: N categories of decision requiring citation",
            ),
        ],
    ),
    DocCountFact(
        ground_truth_fn=_count_name_only_skills,
        label='count of skillOverrides == "name-only" in settings.json (excluding builtin loop/simplify)',
        occurrences=[
            Occurrence(
                rel_path="docs/skills.md",
                pattern=r"(\w+) skills in this repo use `skillOverrides: name-only`",
                description="docs/skills.md: N skills use skillOverrides: name-only",
            ),
        ],
    ),
    DocCountFact(
        ground_truth_fn=_count_handoff_nudge_threshold_percentage,
        label="handoff-nudge PCT_THRESHOLD percentage in nudge-handoff-near-context-cap.sh",
        occurrences=[
            Occurrence(
                rel_path="docs/handoff-nudge.md",
                pattern=r"total crosses (\d+)% of that window",
                description="docs/handoff-nudge.md: total crosses N% of that window",
            ),
            Occurrence(
                rel_path="docs/handoff-nudge.md",
                pattern=r"Threshold \((\d+)%\)",
                description="docs/handoff-nudge.md: table header threshold percentage",
            ),
        ],
    ),
    DocCountFact(
        ground_truth_fn=_count_handoff_nudge_abs_cap_default,
        label="handoff-nudge HANDOFF_NUDGE_ABS_CAP default, derived behaviorally",
        occurrences=[
            Occurrence(
                rel_path="docs/handoff-nudge.md",
                pattern=r"\| 1M \(default\) \| 400000 \| (\d+)",
                description="docs/handoff-nudge.md: table 1M-row effective threshold",
            ),
            Occurrence(
                rel_path="README.md",
                pattern=r"capped at (\d+) tokens",
                description="README.md: capped at N tokens",
            ),
            Occurrence(
                rel_path="README.md",
                pattern=r"past a (\d+)-token prefix on the largest context window",
                description="README.md: past a N-token prefix on the largest context window",
            ),
            Occurrence(
                rel_path="README.md",
                pattern=r"(\d+) tokens \(default\): the absolute-token cap",
                description="README.md: N tokens (default): the absolute-token cap",
            ),
        ],
    ),
    DocCountFact(
        ground_truth_fn=_count_handoff_nudge_block_after_default,
        label="handoff-nudge HANDOFF_NUDGE_BLOCK_AFTER default, derived behaviorally",
        occurrences=[
            Occurrence(
                rel_path="docs/handoff-nudge.md",
                pattern=r"`HANDOFF_NUDGE_BLOCK_AFTER` \(default (\d+)\)",
                description="docs/handoff-nudge.md: HANDOFF_NUDGE_BLOCK_AFTER (default N)",
            ),
        ],
    ),
]


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _assert_exactly_one_match(
    matches: list[str],
    occurrence: Occurrence,
) -> None:
    """Assert that re.findall returned exactly one match for this occurrence."""
    doc_path = REPO_ROOT / occurrence.rel_path
    assert len(matches) == 1, (
        f"{occurrence.rel_path}: expected exactly one match for pattern "
        f"{occurrence.pattern!r} ({occurrence.description}), "
        f"but got {len(matches)} match(es) in {doc_path}. "
        "Zero matches means the sentence was reworded or removed — update the "
        "pattern or remove this occurrence from the registry. "
        "More than one match means the sentence was duplicated or the pattern "
        "is too broad — tighten the pattern or update the registry."
    )


def _assert_claimed_matches_actual(
    claimed: int,
    actual: int,
    occurrence: Occurrence,
    ground_truth_label: str,
) -> None:
    """Assert that the claimed count in the doc matches the actual count."""
    assert claimed == actual, (
        f"{occurrence.rel_path}: claimed count {claimed!r} does not match "
        f"actual count {actual} from {ground_truth_label!r}. "
        f"({occurrence.description}) — update the documentation to reflect "
        "the current value, or update the source it references."
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDocCounts:
    """Assert that each registered doc-count claim matches its ground-truth source.

    Each DocCountFact in _REGISTERED_FACTS is a parametrized test case keyed by
    its label. Adding a new fact to the registry automatically creates a new test
    case without requiring a new method — and eliminates the index-based coupling
    that would silently test the wrong fact if the list were reordered.
    """

    @pytest.mark.parametrize("fact", _REGISTERED_FACTS, ids=lambda f: f.label)
    def test_doc_count_claim_matches_ground_truth(self, fact: DocCountFact) -> None:
        """Each registered doc-count claim must match the ground-truth count."""
        actual = fact.ground_truth_fn()
        for occurrence in fact.occurrences:
            doc_text = (REPO_ROOT / occurrence.rel_path).read_text()
            matches = re.findall(occurrence.pattern, doc_text)
            _assert_exactly_one_match(matches, occurrence)
            claimed = _parse_count_token(matches[0])
            _assert_claimed_matches_actual(claimed, actual, occurrence, fact.label)


class TestParseCountToken:
    """Unit tests for the _parse_count_token helper."""

    def test_digit_string_returns_int(self) -> None:
        assert _parse_count_token("8") == 8
        assert _parse_count_token("10") == 10

    def test_english_word_parsed_case_insensitively(self) -> None:
        assert _parse_count_token("eight") == 8
        assert _parse_count_token("Eight") == 8
        assert _parse_count_token("Ten") == 10
        assert _parse_count_token("twenty") == 20

    def test_unrecognised_token_raises_value_error_naming_the_token(self) -> None:
        """Error message must name the unparseable token so contributors know what to fix."""
        with pytest.raises(ValueError, match=r"twenty-one"):
            _parse_count_token("twenty-one")
