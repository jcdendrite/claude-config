"""Stratified recall/precision scorer for the `Explore` Haiku-pin adoption gate.

See .claude/plans/haiku-routing-surfaces.md's "Step 3 — Measure recall (the
gate)" section for the full design: the two fixture strata, the answer
contract, the matching and arithmetic rules, and the three-term tri-state
gate this module's evaluate_gate() implements exactly. Every threshold and
formula below is fixed there, not re-derived here.

LOCAL USE ONLY — never run in CI. Same subscription-auth `claude -p`
subprocess-launch shape and never-CI posture as
measure_subagent_model_resolution.py — see evals/README.md's "Why local
only — never CI" section.

Reuses measure_subagent_model_resolution.py (imported as msmr) rather than
re-deriving:

- The --agents override payloads and dispatch labels
  (DISPATCH_EXPLORE_HAIKU/_SONNET, _EXPLORE_OVERRIDE_BY_DISPATCH,
  EXPLORE_HAIKU_OVERRIDE_TOOLS).
- The subprocess-launch and sidecar-polling machinery
  (_run_claude_to_completion, _wait_for_subagent_sidecar,
  _compute_sidecar_poll_timed_out, _extract_total_cost_usd,
  _resolved_temp_project_dir).
- The arm-mismatch discriminator (parse_subagent_dispatches /
  DispatchObservation.observed_model_family).
- PER_RUN_BUDGET_CAP_USD.
- The no-explicit-model-param prompt clause
  (DISPATCH_PROMPT_EXPLORE_HAIKU_SUFFIX).

New in this module: the fixture schema and floor check, the final-answer
sidecar reader, fenced-JSON extraction and normalization, Stratum A/B
matching against accepted_near_misses, the aggregation arithmetic, the
hand-rolled Wilson interval, the tri-state gate function, the UNCALLABLE
K-raise loop, and the cumulative $300 stop.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import signal
import subprocess
import sys
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import measure_subagent_model_resolution as msmr

# --- Fixture schema ----------------------------------------------------------

STRATUM_A = "A"
STRATUM_B = "B"

REQUIRED_TOP_LEVEL_KEYS = frozenset({"pinned_commit", "cases"})

# git's own default abbreviation floor (git-rev-parse(1) --short,
# git-config(1) core.abbrev). A 32+ character hex run is separately blocked
# by this repo's own commit-redaction gate in a staged diff, which this
# floor stays well under. Lowercase only: git's own --short output is
# always lowercase.
MIN_PINNED_COMMIT_HEX_LEN = 7
_ABBREVIATED_COMMIT_RE = re.compile(rf"^[0-9a-f]{{{MIN_PINNED_COMMIT_HEX_LEN},}}$")

# Fixture floor rationale: Stratum A needs recall to be exercisable (a
# model must be able to return a subset), Stratum B needs more than one
# ground-truth site so a single-site case doesn't degenerate recall to a
# coin flip.
STRATUM_SITE_FLOOR = 5
MIN_STRATUM_A_CASES = 8
MIN_STRATUM_A_WIDE_CASES = 5
MIN_STRATUM_B_CASES = 5
MIN_STRATUM_B_WIDE_CASES = 3


class FixtureSchemaError(ValueError):
    """Raised by load_fixture/parse_fixture_cases/validate_fixture_floors on
    a malformed fixture or one that doesn't meet the fixture's minimum
    case/site-coverage requirements."""


@dataclass(frozen=True)
class RecallCase:
    id: str
    stratum: str
    question: str
    key: frozenset[str]
    accepted_near_misses: frozenset[str] = frozenset()
    grep_pattern: str | None = None
    grep_mode: str | None = None
    second_pass_confirmed: bool | None = None


def parse_fixture_cases(raw_cases: object) -> tuple[RecallCase, ...]:
    """Builds one RecallCase per fixture entry, raising FixtureSchemaError on
    a missing/malformed required field — including a Stratum B case lacking
    `second_pass_confirmed: true`, which the fixture-schema test pins."""
    if not isinstance(raw_cases, list):
        raise FixtureSchemaError("fixture 'cases' must be a JSON array")
    cases: list[RecallCase] = []
    for raw in raw_cases:
        if not isinstance(raw, dict):
            raise FixtureSchemaError(f"each fixture case must be a JSON object, got {raw!r}")
        case_id = raw.get("id")
        stratum = raw.get("stratum")
        question = raw.get("question")
        key_raw = raw.get("key")
        if not isinstance(case_id, str) or not case_id:
            raise FixtureSchemaError(f"case missing a non-empty string 'id': {raw!r}")
        if not isinstance(question, str) or not question:
            raise FixtureSchemaError(f"case {case_id!r} missing a non-empty string 'question'")
        if not isinstance(key_raw, list) or not key_raw or not all(isinstance(k, str) for k in key_raw):
            raise FixtureSchemaError(f"case {case_id!r} missing a non-empty string-array 'key'")
        key = frozenset(key_raw)
        if stratum == STRATUM_A:
            grep_pattern = raw.get("grep_pattern")
            grep_mode = raw.get("grep_mode")
            if not isinstance(grep_pattern, str) or not grep_pattern:
                raise FixtureSchemaError(f"Stratum A case {case_id!r} missing a non-empty string 'grep_pattern'")
            if not isinstance(grep_mode, str) or not grep_mode:
                raise FixtureSchemaError(f"Stratum A case {case_id!r} missing a non-empty string 'grep_mode'")
            cases.append(RecallCase(
                id=case_id, stratum=STRATUM_A, question=question, key=key,
                grep_pattern=grep_pattern, grep_mode=grep_mode,
            ))
        elif stratum == STRATUM_B:
            near_misses_raw = raw.get("accepted_near_misses", [])
            if not isinstance(near_misses_raw, list) or not all(isinstance(m, str) for m in near_misses_raw):
                raise FixtureSchemaError(f"Stratum B case {case_id!r} has a malformed 'accepted_near_misses'")
            if raw.get("second_pass_confirmed") is not True:
                raise FixtureSchemaError(
                    f"Stratum B case {case_id!r} is missing 'second_pass_confirmed: true' — an "
                    "under-curated key inflates recall for both models identically and the gate "
                    "cannot detect it, so an unconfirmed key is rejected outright."
                )
            cases.append(RecallCase(
                id=case_id, stratum=STRATUM_B, question=question, key=key,
                accepted_near_misses=frozenset(near_misses_raw), second_pass_confirmed=True,
            ))
        else:
            raise FixtureSchemaError(f"case {case_id!r} has unknown stratum {stratum!r} (expected 'A' or 'B')")
    return tuple(cases)


def validate_fixture_floors(cases: tuple[RecallCase, ...]) -> None:
    """Fails loudly when the fixture doesn't meet the minimum case/site
    coverage above, rather than silently running a gate that can't exercise
    recall."""
    stratum_a = [c for c in cases if c.stratum == STRATUM_A]
    stratum_b = [c for c in cases if c.stratum == STRATUM_B]
    if len(stratum_a) < MIN_STRATUM_A_CASES:
        raise FixtureSchemaError(
            f"fixture has {len(stratum_a)} Stratum A case(s), below the floor of {MIN_STRATUM_A_CASES}"
        )
    wide_a = sum(1 for c in stratum_a if len(c.key) >= STRATUM_SITE_FLOOR)
    if wide_a < MIN_STRATUM_A_WIDE_CASES:
        raise FixtureSchemaError(
            f"fixture has {wide_a} Stratum A case(s) with >= {STRATUM_SITE_FLOOR} ground-truth files, "
            f"below the floor of {MIN_STRATUM_A_WIDE_CASES}"
        )
    if len(stratum_b) < MIN_STRATUM_B_CASES:
        raise FixtureSchemaError(
            f"fixture has {len(stratum_b)} Stratum B case(s), below the floor of {MIN_STRATUM_B_CASES}"
        )
    wide_b = sum(1 for c in stratum_b if len(c.key) >= STRATUM_SITE_FLOOR)
    if wide_b < MIN_STRATUM_B_WIDE_CASES:
        raise FixtureSchemaError(
            f"fixture has {wide_b} Stratum B case(s) with >= {STRATUM_SITE_FLOOR} ground-truth sites, "
            f"below the floor of {MIN_STRATUM_B_WIDE_CASES}"
        )


def load_fixture(path: Path) -> tuple[str, tuple[RecallCase, ...]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise FixtureSchemaError("fixture root must be a JSON object")
    missing_top = REQUIRED_TOP_LEVEL_KEYS - data.keys()
    if missing_top:
        raise FixtureSchemaError(f"fixture missing top-level keys: {sorted(missing_top)}")
    pinned_commit = data["pinned_commit"]
    if not isinstance(pinned_commit, str) or not _ABBREVIATED_COMMIT_RE.match(pinned_commit):
        raise FixtureSchemaError(
            f"fixture 'pinned_commit' must be a lowercase-hex commit SHA (full or abbreviated to "
            f"at least {MIN_PINNED_COMMIT_HEX_LEN} characters), got {pinned_commit!r}"
        )
    cases = parse_fixture_cases(data["cases"])
    validate_fixture_floors(cases)
    return pinned_commit, cases


# --- --repo-dir validation ----------------------------------------------------


class RepoDirValidationError(ValueError):
    """Raised by validate_repo_dir when --repo-dir isn't a clean checkout at
    the fixture's pinned_commit."""


def validate_repo_dir(repo_dir: Path, pinned_commit: str) -> None:
    """Refuses a --repo-dir Explore can't trust to match the fixture's own
    ground truth. The headless parent launches in its own empty temp
    project dir (msmr._resolved_temp_project_dir), so Explore has nothing to
    search unless pointed at a real checkout. The fixture's keys were
    derived at exactly pinned_commit, so a checkout at a different commit,
    or one with uncommitted changes, can disagree with those keys.
    pinned_commit may be an abbreviated SHA: HEAD must start with it rather
    than equal it, so a full SHA still matches exactly."""
    if not repo_dir.is_dir():
        raise RepoDirValidationError(f"--repo-dir {repo_dir} does not exist or is not a directory")
    head = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "HEAD"], capture_output=True, text=True, check=False,
    )
    if head.returncode != 0:
        raise RepoDirValidationError(f"--repo-dir {repo_dir} is not a git repository: {head.stderr.strip()}")
    head_sha = head.stdout.strip()
    if not head_sha.startswith(pinned_commit):
        raise RepoDirValidationError(
            f"--repo-dir {repo_dir} is checked out at {head_sha}, which does not start with the "
            f"fixture's pinned_commit {pinned_commit}"
        )
    status = subprocess.run(
        ["git", "-C", str(repo_dir), "status", "--porcelain"], capture_output=True, text=True, check=False,
    )
    if status.returncode != 0:
        raise RepoDirValidationError(f"--repo-dir {repo_dir}: git status failed: {status.stderr.strip()}")
    if status.stdout.strip():
        raise RepoDirValidationError(
            f"--repo-dir {repo_dir} has uncommitted changes (git status --porcelain is non-empty) -- "
            "commit, stash, or discard them, or point --repo-dir at a separate clean checkout"
        )


# --- Dispatch prompt builder (the structured-answer contract) ----------------

ANSWER_CONTRACT_TEMPLATE = (
    "End your reply with one fenced ```json code block containing a JSON array "
    "of strings ({unit}) and nothing else in that block."
)

# Defines `symbol` for Stratum B precisely, since `path:symbol` alone leaves
# it ambiguous. Models have been observed substituting the called function's
# name, a bare line number, or a bare path for the containing function's own
# name.
STRATUM_B_SYMBOL_DEFINITION = (
    "`symbol` is the name of the function or shell function that CONTAINS the "
    "matching line, not the function being called there. For a line that is "
    "not inside any function, use the literal label `top-level-main`. Give "
    "one entry per containing function or top-level block, not per line."
)


def _answer_contract_unit(stratum: str) -> str:
    return "repo-relative file paths" if stratum == STRATUM_A else "`path:symbol` entries"


def build_dispatch_prompt(case: RecallCase, repo_dir: Path) -> str:
    """The case's own question plus where to search (repo_dir) plus the
    structured-answer contract, plus msmr.DISPATCH_PROMPT_EXPLORE_HAIKU_SUFFIX's
    "dispatch by name only, no model param" clause so the headless parent
    can't restore Sonnet via an explicit per-dispatch model param.
    The headless parent itself launches in an empty temp project dir, so
    Explore has nothing to search unless told where the real checkout is."""
    location = (
        f"The repository to search is the directory {repo_dir}. Use Grep/Glob/Read "
        "with that directory as the path. Report every path relative to that directory."
    )
    contract = ANSWER_CONTRACT_TEMPLATE.format(unit=_answer_contract_unit(case.stratum))
    if case.stratum == STRATUM_B:
        contract = f"{contract} {STRATUM_B_SYMBOL_DEFINITION}"
    instruction = f"{case.question} {location} {contract}"
    return (
        f'Use the Task tool to dispatch exactly one "Explore" subagent with this '
        f'instruction: "{instruction}" Wait for its reply and report it back to me '
        "verbatim. Do not look into the question or judge it yourself, and do not "
        "do any other work."
    ) + msmr.DISPATCH_PROMPT_EXPLORE_HAIKU_SUFFIX


# --- Sidecar final-answer reader ----------------------------------------------

_FENCED_JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)\n?```", re.DOTALL)


def _scan_subagent_final_answer_text(path: Path) -> str | None:
    """Beside msmr._scan_subagent_jsonl, which reads only tool_use blocks and
    model IDs: returns the last assistant record's own text content blocks,
    concatenated in order, tool_use blocks ignored. None if the file has no
    assistant record at all (not the same as an assistant record whose last
    turn carried no text, which returns "").

    Reads in binary mode and decodes per line, like corpus._parse_jsonl_records,
    so one line that is not valid UTF-8 is skipped rather than aborting the scan."""
    last_text: str | None = None
    seen_assistant = False
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if rec.get("type") != "assistant":
                    continue
                seen_assistant = True
                content = (rec.get("message") or {}).get("content")
                if isinstance(content, str):
                    last_text = content
                elif isinstance(content, list):
                    last_text = "".join(
                        block.get("text", "") for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    )
                else:
                    last_text = ""
    except OSError:
        return None
    return last_text if seen_assistant else None


def _last_fenced_json_block(text: str) -> str | None:
    matches = _FENCED_JSON_BLOCK_RE.findall(text)
    return matches[-1] if matches else None


def parse_answer_array(block_text: str) -> list[str] | None:
    try:
        parsed = json.loads(block_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        return None
    return parsed


# The dispatch prompt names `top-level-main` directly (STRATUM_B_SYMBOL_
# DEFINITION). This closed alias set additionally catches a model that
# paraphrases that label instead of using it verbatim.
STRATUM_B_TOP_LEVEL_SYMBOL_ALIASES = frozenset({
    "main", "top-level", "<top-level>", "toplevel", "top_level", "script",
    "script-body", "(top-level)", "global", "module",
})


def canonicalize_stratum_b_symbol(entry: str) -> str:
    """Maps a Stratum B `path:symbol` entry's symbol onto the fixture's own
    `top-level-main` label when it is a known paraphrase, after stripping
    surrounding backticks and a trailing call-style `()`. A bare path (no
    `:`) or a symbol that is purely numeric (a mis-reported line number)
    matches no alias, so it passes through unchanged and scores as a false
    positive -- deliberately, since neither has a convention to rescue it
    onto."""
    if ":" not in entry:
        return entry
    path, symbol = entry.split(":", 1)
    cleaned = symbol.strip()
    while True:
        stripped = cleaned
        if stripped.startswith("`") and stripped.endswith("`") and len(stripped) >= 2:
            stripped = stripped[1:-1]
        if stripped.endswith("()"):
            stripped = stripped[:-2]
        if stripped == cleaned:
            break
        cleaned = stripped
    if cleaned.lower() in STRATUM_B_TOP_LEVEL_SYMBOL_ALIASES:
        cleaned = "top-level-main"
    return f"{path}:{cleaned}"


def normalize_entry(entry: str, *, repo_dir: Path | None = None, stratum: str | None = None) -> str:
    """Strip a leading absolute `repo_dir/` prefix (a model returning
    absolute paths despite the prompt's relative-path instruction), then a
    leading './', collapse '//', drop surrounding whitespace. repo_dir must
    already be resolved -- this does only string-prefix stripping, no
    filesystem resolution of its own. stratum=STRATUM_B additionally runs
    the entry's symbol through canonicalize_stratum_b_symbol."""
    normalized = entry.strip()
    if repo_dir is not None:
        repo_dir_prefix = f"{repo_dir}/"
        if normalized.startswith(repo_dir_prefix):
            normalized = normalized[len(repo_dir_prefix):]
    normalized = normalized.removeprefix("./")
    normalized = re.sub(r"/+", "/", normalized)
    if stratum == STRATUM_B:
        normalized = canonicalize_stratum_b_symbol(normalized)
    return normalized


def normalize_and_dedup(
    entries: Iterable[str], *, repo_dir: Path | None = None, stratum: str | None = None,
) -> tuple[str, ...]:
    """Normalizes every entry and drops a repeat, keeping first-seen order —
    a repeated item counts once for both recall and precision. Stratum B's
    symbol canonicalization runs before dedup, so e.g. `path:main` and
    `path:top-level-main` collapse to one entry."""
    seen: set[str] = set()
    result: list[str] = []
    for entry in entries:
        normalized = normalize_entry(entry, repo_dir=repo_dir, stratum=stratum)
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return tuple(result)


@dataclass(frozen=True)
class AnswerExtraction:
    items: tuple[str, ...] | None  # None on a format failure
    format_failure: bool
    raw_block: str | None
    # The full text _scan_subagent_final_answer_text found, independent of
    # whether a fenced block was present in it -- None only when no
    # assistant record was found at all, for --keep-artifacts debugging.
    final_answer_text: str | None = None


def read_final_answer(
    subagent_jsonl: Path, *, repo_dir: Path | None = None, stratum: str | None = None,
) -> AnswerExtraction:
    """A sample with no parseable fenced block, or whose block parses to
    anything other than a JSON array of strings, is a format failure:
    recall 0, precision undefined."""
    text = _scan_subagent_final_answer_text(subagent_jsonl)
    if not text:
        return AnswerExtraction(items=None, format_failure=True, raw_block=None, final_answer_text=text)
    block = _last_fenced_json_block(text)
    if block is None:
        return AnswerExtraction(items=None, format_failure=True, raw_block=None, final_answer_text=text)
    parsed = parse_answer_array(block)
    if parsed is None:
        return AnswerExtraction(items=None, format_failure=True, raw_block=block, final_answer_text=text)
    return AnswerExtraction(
        items=normalize_and_dedup(parsed, repo_dir=repo_dir, stratum=stratum), format_failure=False,
        raw_block=block, final_answer_text=text,
    )


# --- Matching and per-sample scoring ------------------------------------------


@dataclass(frozen=True)
class SampleScore:
    recall: float
    precision: float | None
    matched: frozenset[str]
    false_positives: frozenset[str]
    near_miss: frozenset[str]
    format_failure: bool


def score_sample(
    items: tuple[str, ...] | None, *, key: frozenset[str],
    accepted_near_misses: frozenset[str] = frozenset(), format_failure: bool = False,
) -> SampleScore:
    """Per-sample recall = matched key sites / key sites. Per-sample
    precision = matched returned items / returned items, with a returned
    item in accepted_near_misses excluded from both the numerator and the
    denominator — it is neither a hit nor a false positive. Precision is
    undefined (None) on a format failure or when no non-near-miss item was
    returned at all."""
    if format_failure or items is None:
        return SampleScore(
            recall=0.0, precision=None, matched=frozenset(), false_positives=frozenset(),
            near_miss=frozenset(), format_failure=True,
        )
    returned = frozenset(items)
    matched = returned & key
    near_miss = (returned - key) & accepted_near_misses
    false_positives = returned - key - accepted_near_misses
    recall = (len(matched) / len(key)) if key else 0.0
    denominator = len(matched) + len(false_positives)
    precision = (len(matched) / denominator) if denominator > 0 else None
    return SampleScore(
        recall=recall, precision=precision, matched=matched, false_positives=false_positives,
        near_miss=near_miss, format_failure=False,
    )


# --- Aggregation ---------------------------------------------------------------


def per_case_recall_stats(samples: list[SampleScore]) -> tuple[float, float, float]:
    """(mean, min, max) recall over one case's K samples."""
    recalls = [s.recall for s in samples]
    return sum(recalls) / len(recalls), min(recalls), max(recalls)


def macro_average(case_values: Iterable[float]) -> float:
    """Per-stratum mean recall: a macro-average over cases, each weighted
    equally regardless of its own sample count."""
    values = list(case_values)
    return sum(values) / len(values)


def pooled_false_positives_and_n(scores: Iterable[SampleScore]) -> tuple[int, int]:
    """(x, n) for the Wilson precision interval's pooled unit of analysis: x
    is false positives, n is non-near-miss returned items, pooled across
    every case and every sample for one model. A format-failure sample
    contributes (0, 0) because its matched/false_positives sets are already
    empty, so it needs no special-casing here."""
    x = 0
    n = 0
    for score in scores:
        x += len(score.false_positives)
        n += len(score.matched) + len(score.false_positives)
    return x, n


def pooled_precision_point_estimate(false_positives: int, n: int) -> float | None:
    return ((n - false_positives) / n) if n > 0 else None


# --- Wilson score interval -----------------------------------------------------


class ScoringError(ValueError):
    """Raised by wilson_lower_bound on a non-positive n -- a scoring-layer
    precondition violation, distinct from FixtureSchemaError (malformed
    fixture input) and RepoDirValidationError (--repo-dir trust boundary)."""


def wilson_lower_bound(successes: int, n: int, *, z: float = 1.96) -> float:
    """Wilson (1927) score interval lower bound at confidence z, as
    presented in Brown, Cai & DasGupta, "Interval Estimation for a Binomial
    Proportion," Statistical Science 16(2), 2001, §4.1."""
    if n <= 0:
        raise ScoringError("n must be positive")
    phat = successes / n
    denom = 1 + z * z / n
    center = phat + z * z / (2 * n)
    margin = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))
    return (center - margin) / denom


def wilson_precision_lower_bound(false_positives: int, n: int, *, z: float = 1.96) -> float:
    """Precision's Wilson lower bound: the interval's lower bound for the
    "matched" proportion (n - false_positives successes out of n), which by
    the Wilson interval's symmetry equals one minus the false-positive
    proportion's own upper bound."""
    return wilson_lower_bound(n - false_positives, n, z=z)


# --- Adoption gate ---------------------------------------------------------------


class GateVerdict(Enum):
    PASS = "pass"
    FAIL = "fail"
    UNCALLABLE = "uncallable"


GATE_PER_STRATUM_RECALL_GAP_MAX = 0.05
GATE_PER_CASE_RECALL_GAP_MAX = 0.15
GATE_PRECISION_POINT_MIN = 0.98
GATE_PRECISION_LOWER_BOUND_MIN = 0.90


def evaluate_gate(
    *, stratum_recall_gaps: Mapping[str, float], min_per_case_recall_gap: float,
    haiku_precision_point_estimate: float, haiku_precision_wilson_lower_bound: float,
) -> GateVerdict:
    """The plan's three gate terms, checked in exactly the order it fixes
    them.

    Term 1 is symmetric (stratum_recall_gaps must already be each stratum's
    *absolute* Haiku-minus-Sonnet gap) — Haiku being notably better than
    Sonnet still fails it. Term 2 is one-directional by contrast:
    min_per_case_recall_gap must already be the most negative *signed*
    Haiku-minus-Sonnet per-case gap (i.e. Haiku's worst case relative to
    Sonnet, not its absolute value and not the largest/best-case gap),
    since only Haiku underperforming Sonnet is the cliff this term guards
    against.
    """
    if any(gap > GATE_PER_STRATUM_RECALL_GAP_MAX for gap in stratum_recall_gaps.values()):
        return GateVerdict.FAIL
    if min_per_case_recall_gap < -GATE_PER_CASE_RECALL_GAP_MAX:
        return GateVerdict.FAIL
    # A lower bound below the floor is UNCALLABLE regardless of the point
    # estimate, because N is too small to decide either way. This check must
    # therefore run before the point-estimate check below, not after it.
    if haiku_precision_wilson_lower_bound < GATE_PRECISION_LOWER_BOUND_MIN:
        return GateVerdict.UNCALLABLE
    if haiku_precision_point_estimate < GATE_PRECISION_POINT_MIN:
        return GateVerdict.FAIL
    return GateVerdict.PASS


def compute_gate_inputs(
    cases: tuple[RecallCase, ...], haiku: ModelAggregate, sonnet: ModelAggregate,
) -> tuple[dict[str, float], float]:
    """Reduces two ModelAggregates to evaluate_gate's two recall inputs:
    each stratum's absolute mean-recall gap, and the most negative signed
    per-case gap (Haiku's worst case relative to Sonnet) across every case
    both models scored."""
    stratum_gaps = {
        stratum: abs(haiku.stratum_recall_mean[stratum] - sonnet.stratum_recall_mean[stratum])
        for stratum in (STRATUM_A, STRATUM_B)
        if stratum in haiku.stratum_recall_mean and stratum in sonnet.stratum_recall_mean
    }
    common_case_ids = set(haiku.per_case_recall_mean) & set(sonnet.per_case_recall_mean)
    per_case_gaps = [
        haiku.per_case_recall_mean[case_id] - sonnet.per_case_recall_mean[case_id]
        for case_id in common_case_ids
    ]
    min_gap = min(per_case_gaps) if per_case_gaps else 0.0
    return stratum_gaps, min_gap


def _gate_data_coverage_is_complete(
    cases: tuple[RecallCase, ...], haiku: ModelAggregate, sonnet: ModelAggregate,
) -> bool:
    """False when any stratum present in `cases`, or any individual case in
    `cases`, has no common scored data between the two models -- the
    silent-pass condition compute_gate_inputs's own stratum_gaps dict
    comprehension and min_gap fallback would otherwise paper over by
    substituting "no gap" for "no data" (e.g. a total-discard dropout
    confined to one stratum or case)."""
    strata_present = {case.stratum for case in cases}
    strata_covered = all(
        stratum in haiku.stratum_recall_mean and stratum in sonnet.stratum_recall_mean
        for stratum in strata_present
    )
    cases_covered = all(
        case.id in haiku.per_case_recall_mean and case.id in sonnet.per_case_recall_mean for case in cases
    )
    return strata_covered and cases_covered


# --- Dispatch mechanics --------------------------------------------------------

# The plan's "Dispatch mechanics" section only fixes discard-and-re-run on
# an arm/observed-model mismatch, not a retry count -- 2 is this module's
# own bounded-retry choice so a persistently misresolving arm can't retry
# unboundedly.
MAX_ARM_MISMATCH_RETRIES = 2

ARM_EXPECTED_FAMILY = {
    msmr.DISPATCH_EXPLORE_HAIKU: msmr.MODEL_FAMILY_HAIKU,
    msmr.DISPATCH_EXPLORE_SONNET: msmr.MODEL_FAMILY_SONNET,
}

ARMS: tuple[str, ...] = (msmr.DISPATCH_EXPLORE_HAIKU, msmr.DISPATCH_EXPLORE_SONNET)

# Sized so the pooled-items unit (>=13 cases x K=5 pooled recalls) clears
# the Wilson lower bound's n >= 35 floor the gate's precision term needs.
DEFAULT_K = 5
# Aborts the whole batch once realized spend crosses $300 -- the plan's
# own fixed stop figure, not re-derived here. Checked once per logical
# sample, after it completes, not before or during one, so the batch can
# overshoot this by up to (MAX_ARM_MISMATCH_RETRIES + 1) * budget_cap_usd
# before the check blocks the next sample from starting.
DEFAULT_CUMULATIVE_STOP_USD = 300.0
# The UNCALLABLE retry path: raise every case's K by 3 on both models and
# re-evaluate once, still under the cumulative stop.
UNCALLABLE_K_INCREMENT = 3
# One K-raise retry. A smoke batch passes 0 so it never enters the
# UNCALLABLE K-raise loop at all.
DEFAULT_MAX_K_RAISES = 1

# The plan's first full K=5 run record: the inherited 90s default
# (msmr.run_skill_evals.SAMPLE_TIMEOUT_S, sized for a lighter dispatch)
# killed the parent mid-search on Stratum B's heavier cases (subagent spans
# measured at 64-82s), producing empty final answers scored as format
# failures. 300s is the corrected value the re-run used.
DEFAULT_TIMEOUT_S = 300

# Discriminator for SampleAttempt.cost_source: whether cost_usd came from
# the stream's own "result" event or was charged conservatively at the
# per-dispatch cap because that event was never emitted (a harness-level
# timeout, or any other path that kills the process first).
COST_SOURCE_REPORTED = "reported"
COST_SOURCE_CAP = "cap"


def build_run_command(
    dispatch: str, prompt: str, *, session_id: str, budget_cap_usd: float, repo_dir: Path,
) -> list[str]:
    """Mirrors msmr.build_run_command's flag shape for the fixed
    opus-parent/default-permission-mode cell measure_subagent_model_resolution.py's
    runs 8 and 9 already validated symmetrically for both --agents model
    values, with this case's own dispatch prompt in place of that module's
    fixed marker-file task, and msmr's own override payload for the
    --agents pin.

    Unlike that module's fixed marker-file task (which writes its own
    marker file inside the headless parent's own tmp_project cwd), this
    dispatch's prompt points Explore at repo_dir, a path outside that cwd.
    --add-dir grants Read/Grep/Glob access to it -- the default permission
    mode has nothing else to approve it under a non-interactive `-p` launch,
    so Explore is otherwise denied before it can search anything at all.
    """
    return [
        "claude", "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--model", "opus",
        "--session-id", session_id,
        "--max-budget-usd", str(budget_cap_usd),
        "--add-dir", str(repo_dir),
        "--agents", json.dumps(msmr._EXPLORE_OVERRIDE_BY_DISPATCH[dispatch]),
    ]


@dataclass(frozen=True)
class SampleAttempt:
    observed_model_family: str | None
    observed_model_ids: frozenset[str]
    # Always populated: either the stream's own reported total_cost_usd, or
    # (cost_source=COST_SOURCE_CAP) the per-dispatch budget cap, charged
    # conservatively when a timeout or any other path kills the process
    # before the "result" event is ever emitted.
    cost_usd: float
    score: SampleScore
    cost_source: str = COST_SOURCE_REPORTED
    timed_out: bool = False
    sidecar_poll_timed_out: bool = False
    # First 2000 chars of the subagent's own extracted text and its parsed
    # answer array, round-tripped into the --json report for debugging a
    # live run after cleanup has already deleted the session store. None
    # when no subagent transcript was read at all (e.g. no dispatch observed).
    final_answer_text: str | None = None
    parsed_items: tuple[str, ...] | None = None


_KEPT_ANSWER_TEXT_CHAR_LIMIT = 2000


# --- --keep-artifacts validation ----------------------------------------------


class KeepArtifactsValidationError(ValueError):
    """Raised by validate_keep_artifacts_dir when --keep-artifacts resolves
    inside a worktree of the repository."""


def _repo_roots_for_containment_check(start_dir: Path | None = None) -> tuple[Path, ...]:
    """Every worktree of this repository -- the main checkout and every
    linked worktree, from `git worktree list --porcelain`'s own `worktree
    <path>` lines -- resolved from start_dir (default: this module's own
    file location). All of them share the same tracked history, so a
    --keep-artifacts path must stay outside every one, not only the
    worktree this process happens to run from. Empty tuple if start_dir
    isn't inside a git checkout, or the git binary itself is unavailable, so
    there's nothing to guard against. start_dir is a test seam: production
    callers never pass it."""
    if start_dir is None:
        start_dir = Path(__file__).resolve().parent
    try:
        result = subprocess.run(
            ["git", "-C", str(start_dir), "worktree", "list", "--porcelain"],
            capture_output=True, text=True, check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ()
    return tuple(
        Path(line.removeprefix("worktree ")).resolve()
        for line in result.stdout.splitlines()
        if line.startswith("worktree ")
    )


def validate_keep_artifacts_dir(keep_artifacts_dir: Path, *, start_dir: Path | None = None) -> None:
    """Refuses a --keep-artifacts path inside any worktree of this
    repository, before any launch: each sample's copied session and
    subagent transcripts carry account-local session paths and must never
    be staged or committed. start_dir is a test seam, see
    _repo_roots_for_containment_check."""
    resolved = keep_artifacts_dir.resolve()
    for repo_root in _repo_roots_for_containment_check(start_dir):
        if resolved == repo_root or repo_root in resolved.parents:
            raise KeepArtifactsValidationError(
                f"--keep-artifacts {keep_artifacts_dir} resolves inside a worktree of this repository "
                f"({repo_root}) -- copied session transcripts carry account-local paths and must never "
                "be committed. Point --keep-artifacts outside the repository, e.g. under /tmp."
            )


def _copy_sample_artifacts(
    session_jsonl: Path, keep_artifacts_dir: Path, *, case_id: str, arm: str, sample_index: int,
) -> None:
    """Copies one sample's parent session .jsonl and its subagents/
    directory into keep_artifacts_dir/<case>-<arm>-<sample>/ before
    execute_one_sample_attempt's own `finally` block deletes both -- the
    only way to inspect a live sample's real transcript shape after the
    run finishes."""
    dest = keep_artifacts_dir / f"{case_id}-{arm}-{sample_index}"
    dest.mkdir(parents=True, exist_ok=True)
    if session_jsonl.is_file():
        shutil.copy2(session_jsonl, dest / session_jsonl.name)
    subagent_dir = msmr.subagent_dir_for_session(session_jsonl)
    if subagent_dir.is_dir():
        shutil.copytree(subagent_dir, dest / "subagents", dirs_exist_ok=True)


def execute_one_sample_attempt(
    case: RecallCase, arm: str, *, budget_cap_usd: float, timeout_s: int, repo_dir: Path,
    keep_artifacts_dir: Path | None = None, sample_index: int = 0,
) -> SampleAttempt:
    """One real `claude -p` launch attempt for one case/arm sample. Mirrors
    msmr.execute_matrix_cell's launch/parse/cleanup sequence, calling its
    helpers directly rather than re-deriving them. repo_dir is the clean,
    pinned-commit checkout Explore is told to search. The launch itself
    still runs in its own empty tmp_project cwd, msmr's own isolation that
    keeps the repo's hooks and CLAUDE.md out of the headless parent."""
    session_id = str(uuid.uuid4())
    tmp_project = msmr._resolved_temp_project_dir("explore-recall-")
    try:
        prompt = build_dispatch_prompt(case, repo_dir)
        cmd = build_run_command(
            arm, prompt, session_id=session_id, budget_cap_usd=budget_cap_usd, repo_dir=repo_dir,
        )
        lines, timed_out = msmr._run_claude_to_completion(cmd, cwd=tmp_project, timeout_s=timeout_s)
        attempted = msmr.run_skill_evals.detect_dispatch_in_lines(lines)
        extracted_cost_usd = msmr._extract_total_cost_usd(lines)
        if timed_out or extracted_cost_usd is None:
            # A harness-level timeout, or any other path that kills the
            # process before the stream's "result" event, incurs real API
            # spend the sidecar never reports -- charge the per-dispatch cap
            # as a conservative upper bound so the cumulative stop can't be
            # silently defeated by under-counted spend, the exact pattern
            # that masked real spend in the first K=5 run.
            cost_usd = budget_cap_usd
            cost_source = COST_SOURCE_CAP
        else:
            cost_usd = extracted_cost_usd
            cost_source = COST_SOURCE_REPORTED
        session_jsonl = msmr.run_skill_evals.compute_session_store_dir(tmp_project) / f"{session_id}.jsonl"
        sidecar_found = msmr._wait_for_subagent_sidecar(session_jsonl) if attempted else None
        sidecar_poll_timed_out = msmr._compute_sidecar_poll_timed_out(attempted, sidecar_found)
        dispatches = msmr.parse_subagent_dispatches(
            session_jsonl, requested_agent_declared_tools=msmr.EXPLORE_HAIKU_OVERRIDE_TOOLS,
        )
        if not dispatches:
            if keep_artifacts_dir is not None:
                _copy_sample_artifacts(
                    session_jsonl, keep_artifacts_dir, case_id=case.id, arm=arm, sample_index=sample_index,
                )
            return SampleAttempt(
                observed_model_family=None, observed_model_ids=frozenset(), cost_usd=cost_usd,
                cost_source=cost_source, timed_out=timed_out, sidecar_poll_timed_out=sidecar_poll_timed_out,
                score=score_sample(None, key=case.key, format_failure=True),
            )
        # dispatches[0] and jsonl_paths[0] are the same dispatch: both globs
        # are sorted, and a dispatch's meta.json and its paired .jsonl share
        # one filename stem, so index 0 in one glob is index 0 in the other.
        # The prompt asks for exactly one Explore dispatch. If the model
        # disobeys and makes more than one, this consistently picks the
        # first dispatch for both the observed family and the answer text,
        # rather than pairing one dispatch's family with another's answer.
        dispatch = dispatches[0]
        subagent_dir = msmr.subagent_dir_for_session(session_jsonl)
        jsonl_paths = sorted(subagent_dir.glob("*.jsonl"))
        extraction = (
            read_final_answer(jsonl_paths[0], repo_dir=repo_dir, stratum=case.stratum)
            if jsonl_paths else AnswerExtraction(None, True, None)
        )
        if keep_artifacts_dir is not None:
            _copy_sample_artifacts(
                session_jsonl, keep_artifacts_dir, case_id=case.id, arm=arm, sample_index=sample_index,
            )
        score = score_sample(
            extraction.items, key=case.key, accepted_near_misses=case.accepted_near_misses,
            format_failure=extraction.format_failure,
        )
        final_answer_text = (
            extraction.final_answer_text[:_KEPT_ANSWER_TEXT_CHAR_LIMIT]
            if extraction.final_answer_text is not None else None
        )
        return SampleAttempt(
            observed_model_family=dispatch.observed_model_family, observed_model_ids=dispatch.observed_model_ids,
            cost_usd=cost_usd, cost_source=cost_source, timed_out=timed_out,
            sidecar_poll_timed_out=sidecar_poll_timed_out, score=score,
            final_answer_text=final_answer_text, parsed_items=extraction.items,
        )
    finally:
        shutil.rmtree(tmp_project, ignore_errors=True)
        session_store_dir = msmr.run_skill_evals.compute_session_store_dir(tmp_project)
        if session_store_dir.exists():
            shutil.rmtree(session_store_dir, ignore_errors=True)


@dataclass(frozen=True)
class SampleOutcome:
    case_id: str
    arm: str
    sample_index: int
    attempt: SampleAttempt
    discarded: bool
    # Every attempt actually launched for this logical sample, in order,
    # including attempt's own earlier retries -- not just the retained
    # final one. Empty for an outcome a test constructs directly without
    # going through run_one_logical_sample; aggregate_model_results and
    # sample_outcome_to_dict both fall back to (attempt,) in that case.
    attempts: tuple[SampleAttempt, ...] = ()


def run_one_logical_sample(
    case: RecallCase, arm: str, sample_index: int, *, budget_cap_usd: float, timeout_s: int,
    launcher: Callable[..., SampleAttempt], max_retries: int = MAX_ARM_MISMATCH_RETRIES,
) -> tuple[SampleOutcome, float]:
    """Launches up to 1 + max_retries attempts, discarding and re-running on
    an arm/observed-model mismatch (an otherwise-silently-Sonnet "Haiku"
    sample would inflate the Haiku arm). Returns the final attempt plus the
    real cost summed across every attempt spent on this one logical sample.
    The outcome's own attempts field carries every attempt launched, not
    just the final one, so a retry-recovered timeout stays visible instead
    of being discarded along with the mismatched attempt that preceded it."""
    total_cost = 0.0
    attempts: list[SampleAttempt] = []
    attempt: SampleAttempt | None = None
    for _ in range(max_retries + 1):
        attempt = launcher(case, arm, budget_cap_usd=budget_cap_usd, timeout_s=timeout_s)
        attempts.append(attempt)
        total_cost += attempt.cost_usd
        if attempt.observed_model_family == ARM_EXPECTED_FAMILY[arm]:
            return SampleOutcome(case.id, arm, sample_index, attempt, discarded=False, attempts=tuple(attempts)), total_cost
    assert attempt is not None  # max_retries + 1 >= 1, so the loop ran at least once
    return SampleOutcome(case.id, arm, sample_index, attempt, discarded=True, attempts=tuple(attempts)), total_cost


@dataclass(frozen=True)
class BatchReport:
    outcomes: tuple[SampleOutcome, ...]
    realized_spend_usd: float
    cumulative_stop_triggered: bool
    k_used: int


def run_batch(
    cases: tuple[RecallCase, ...], arms: tuple[str, ...], *, k: int, cumulative_stop_usd: float,
    budget_cap_usd: float, timeout_s: int, launcher: Callable[..., SampleAttempt],
    starting_spend: float = 0.0, sample_index_offset: int = 0,
) -> BatchReport:
    """Runs k samples per case per arm, aborting the whole batch — not just
    the next dispatch — the moment the running total (continued from
    starting_spend, for the UNCALLABLE retry's continuation) reaches
    cumulative_stop_usd, and returning whatever was collected so far rather
    than raising."""
    outcomes: list[SampleOutcome] = []
    realized_spend = starting_spend
    stopped = False
    for case in cases:
        if stopped:
            break
        for arm in arms:
            if stopped:
                break
            for i in range(k):
                if stopped:
                    break
                outcome, cost = run_one_logical_sample(
                    case, arm, sample_index_offset + i, budget_cap_usd=budget_cap_usd,
                    timeout_s=timeout_s, launcher=launcher,
                )
                outcomes.append(outcome)
                realized_spend += cost
                if realized_spend >= cumulative_stop_usd:
                    stopped = True
    return BatchReport(tuple(outcomes), realized_spend, stopped, k)


# --- Reporting -----------------------------------------------------------------


@dataclass(frozen=True)
class ModelAggregate:
    arm: str
    per_case_recall_mean: dict[str, float]
    per_case_recall_min: dict[str, float]
    per_case_recall_max: dict[str, float]
    stratum_recall_mean: dict[str, float]
    pooled_false_positives: int
    pooled_returned_items: int
    precision_point_estimate: float | None
    precision_wilson_lower_bound: float | None
    format_failures: int
    discards: int
    timeouts: int
    observed_model_ids: frozenset[str]


def aggregate_model_results(
    cases: tuple[RecallCase, ...], arm: str, outcomes: tuple[SampleOutcome, ...],
) -> ModelAggregate:
    relevant = [o for o in outcomes if o.arm == arm]
    discards = sum(1 for o in relevant if o.discarded)
    # Every attempt actually launched for this arm that timed out, including
    # a discarded sample's and a retry-recovered sample's own earlier
    # attempts -- distinct from discards (an arm/observed-model mismatch),
    # since a timeout is a harness-level cause, not a model one. o.attempts
    # is the full retry chain. o.attempt alone only reflects the last one.
    timeouts = sum(1 for o in relevant for a in (o.attempts or (o.attempt,)) if a.timed_out)
    scored = [o for o in relevant if not o.discarded]

    by_case: dict[str, list[SampleScore]] = defaultdict(list)
    observed_model_ids: set[str] = set()
    format_failures = 0
    for outcome in scored:
        by_case[outcome.case_id].append(outcome.attempt.score)
        observed_model_ids.update(outcome.attempt.observed_model_ids)
        if outcome.attempt.score.format_failure:
            format_failures += 1

    per_case_recall_mean: dict[str, float] = {}
    per_case_recall_min: dict[str, float] = {}
    per_case_recall_max: dict[str, float] = {}
    for case in cases:
        scores = by_case.get(case.id)
        if not scores:
            continue
        mean, lo, hi = per_case_recall_stats(scores)
        per_case_recall_mean[case.id] = mean
        per_case_recall_min[case.id] = lo
        per_case_recall_max[case.id] = hi

    stratum_recall_mean: dict[str, float] = {}
    for stratum in (STRATUM_A, STRATUM_B):
        case_ids = [c.id for c in cases if c.stratum == stratum and c.id in per_case_recall_mean]
        if case_ids:
            stratum_recall_mean[stratum] = macro_average(per_case_recall_mean[cid] for cid in case_ids)

    all_scores = [score for scores in by_case.values() for score in scores]
    pooled_x, pooled_n = pooled_false_positives_and_n(all_scores)
    precision_point = pooled_precision_point_estimate(pooled_x, pooled_n)
    precision_lower = wilson_precision_lower_bound(pooled_x, pooled_n) if pooled_n > 0 else None

    return ModelAggregate(
        arm=arm, per_case_recall_mean=per_case_recall_mean, per_case_recall_min=per_case_recall_min,
        per_case_recall_max=per_case_recall_max, stratum_recall_mean=stratum_recall_mean,
        pooled_false_positives=pooled_x, pooled_returned_items=pooled_n,
        precision_point_estimate=precision_point, precision_wilson_lower_bound=precision_lower,
        format_failures=format_failures, discards=discards, timeouts=timeouts,
        observed_model_ids=frozenset(observed_model_ids),
    )


def _score_and_decide(
    cases: tuple[RecallCase, ...], outcomes: tuple[SampleOutcome, ...],
) -> tuple[GateVerdict, ModelAggregate, ModelAggregate]:
    haiku_agg = aggregate_model_results(cases, msmr.DISPATCH_EXPLORE_HAIKU, outcomes)
    sonnet_agg = aggregate_model_results(cases, msmr.DISPATCH_EXPLORE_SONNET, outcomes)
    # No pooled returned items at all (e.g. every sample discarded), or a
    # stratum/case with no common scored data between the two models (a
    # dropout confined to part of the fixture), is too little to decide
    # either way, the same as a too-low Wilson lower bound — evaluate_gate
    # is never called on data that can't actually compare the two models.
    if (
        haiku_agg.precision_point_estimate is None
        or haiku_agg.precision_wilson_lower_bound is None
        or not _gate_data_coverage_is_complete(cases, haiku_agg, sonnet_agg)
    ):
        return GateVerdict.UNCALLABLE, haiku_agg, sonnet_agg
    stratum_gaps, min_case_gap = compute_gate_inputs(cases, haiku_agg, sonnet_agg)
    verdict = evaluate_gate(
        stratum_recall_gaps=stratum_gaps, min_per_case_recall_gap=min_case_gap,
        haiku_precision_point_estimate=haiku_agg.precision_point_estimate,
        haiku_precision_wilson_lower_bound=haiku_agg.precision_wilson_lower_bound,
    )
    return verdict, haiku_agg, sonnet_agg


@dataclass(frozen=True)
class GateReport:
    verdict: GateVerdict
    haiku: ModelAggregate
    sonnet: ModelAggregate
    outcomes: tuple[SampleOutcome, ...]
    realized_spend_usd: float
    cumulative_stop_triggered: bool
    uncallable_retry_ran: bool
    k_used: int


def run_recall_gate(
    cases: tuple[RecallCase, ...], *, k: int, cumulative_stop_usd: float, budget_cap_usd: float,
    timeout_s: int, launcher: Callable[..., SampleAttempt], max_k_raises: int = DEFAULT_MAX_K_RAISES,
) -> GateReport:
    """The gate's full driver: one K-sample batch, then — only on an
    UNCALLABLE verdict that the cumulative stop didn't already cut short —
    up to max_k_raises additional UNCALLABLE_K_INCREMENT-sample batches per
    case per model, re-evaluated after each. A verdict that is still
    UNCALLABLE once max_k_raises is exhausted, or a cumulative stop that
    fires during a retry, is the plan's "not decidable within budget"
    outcome — the caller treats it the same as a refuse-branch FAIL.
    max_k_raises=0 lets a smoke batch pass without ever entering this loop."""
    batch = run_batch(
        cases, ARMS, k=k, cumulative_stop_usd=cumulative_stop_usd, budget_cap_usd=budget_cap_usd,
        timeout_s=timeout_s, launcher=launcher,
    )
    verdict, haiku_agg, sonnet_agg = _score_and_decide(cases, batch.outcomes)

    uncallable_retry_ran = False
    current_k = k
    raises_done = 0
    while (
        verdict == GateVerdict.UNCALLABLE
        and not batch.cumulative_stop_triggered
        and raises_done < max_k_raises
    ):
        uncallable_retry_ran = True
        raises_done += 1
        extra = run_batch(
            cases, ARMS, k=UNCALLABLE_K_INCREMENT, cumulative_stop_usd=cumulative_stop_usd,
            budget_cap_usd=budget_cap_usd, timeout_s=timeout_s, launcher=launcher,
            starting_spend=batch.realized_spend_usd, sample_index_offset=current_k,
        )
        combined_outcomes = batch.outcomes + extra.outcomes
        current_k += UNCALLABLE_K_INCREMENT
        verdict, haiku_agg, sonnet_agg = _score_and_decide(cases, combined_outcomes)
        batch = BatchReport(
            outcomes=combined_outcomes, realized_spend_usd=extra.realized_spend_usd,
            cumulative_stop_triggered=extra.cumulative_stop_triggered, k_used=current_k,
        )

    return GateReport(
        verdict=verdict, haiku=haiku_agg, sonnet=sonnet_agg, outcomes=batch.outcomes,
        realized_spend_usd=batch.realized_spend_usd, cumulative_stop_triggered=batch.cumulative_stop_triggered,
        uncallable_retry_ran=uncallable_retry_ran, k_used=batch.k_used,
    )


def build_dry_run_plan(
    cases: tuple[RecallCase, ...], arms: tuple[str, ...], k: int, *, budget_cap_usd: float, repo_dir: Path,
) -> tuple[dict, ...]:
    """Every (case, arm, sample) command and prompt a live run would launch,
    built with no subprocess invocation — what --dry-run reports."""
    plan: list[dict] = []
    for case in cases:
        for arm in arms:
            for sample_index in range(k):
                prompt = build_dispatch_prompt(case, repo_dir)
                session_id = f"dry-run-{case.id}-{arm}-{sample_index}"
                cmd = build_run_command(
                    arm, prompt, session_id=session_id, budget_cap_usd=budget_cap_usd, repo_dir=repo_dir,
                )
                plan.append({
                    "case_id": case.id, "arm": arm, "sample_index": sample_index,
                    "command": cmd, "prompt": prompt,
                })
    return tuple(plan)


def model_aggregate_to_dict(agg: ModelAggregate) -> dict:
    return {
        "arm": agg.arm,
        "per_case_recall_mean": agg.per_case_recall_mean,
        "per_case_recall_min": agg.per_case_recall_min,
        "per_case_recall_max": agg.per_case_recall_max,
        "stratum_recall_mean": agg.stratum_recall_mean,
        "pooled_false_positives": agg.pooled_false_positives,
        "pooled_returned_items": agg.pooled_returned_items,
        "precision_point_estimate": agg.precision_point_estimate,
        "precision_wilson_lower_bound": agg.precision_wilson_lower_bound,
        "format_failures": agg.format_failures,
        "discards": agg.discards,
        "timeouts": agg.timeouts,
        "observed_model_ids": sorted(agg.observed_model_ids),
    }


def sample_outcome_to_dict(outcome: SampleOutcome) -> dict:
    """Per-sample raw data for --json debugging: the final_answer_text and
    parsed_items that fed score_sample, surviving past execute_one_sample_attempt's
    own cleanup of the session store that produced them. attempts carries
    every attempt in this logical sample's retry chain (timed_out,
    sidecar_poll_timed_out, cost_usd, cost_source), not just the final one,
    so a retry-recovered timeout stays visible in the report."""
    attempt = outcome.attempt
    attempts = outcome.attempts or (attempt,)
    return {
        "case_id": outcome.case_id,
        "arm": outcome.arm,
        "sample_index": outcome.sample_index,
        "discarded": outcome.discarded,
        "observed_model_family": attempt.observed_model_family,
        "observed_model_ids": sorted(attempt.observed_model_ids),
        "cost_usd": attempt.cost_usd,
        "cost_source": attempt.cost_source,
        "timed_out": attempt.timed_out,
        "sidecar_poll_timed_out": attempt.sidecar_poll_timed_out,
        "format_failure": attempt.score.format_failure,
        "recall": attempt.score.recall,
        "final_answer_text": attempt.final_answer_text,
        "parsed_items": list(attempt.parsed_items) if attempt.parsed_items is not None else None,
        "attempts": [
            {
                "timed_out": a.timed_out,
                "sidecar_poll_timed_out": a.sidecar_poll_timed_out,
                "cost_usd": a.cost_usd,
                "cost_source": a.cost_source,
            }
            for a in attempts
        ],
    }


def gate_report_to_dict(report: GateReport, *, repo_dir: Path, pinned_commit: str) -> dict:
    """repo_dir and pinned_commit round-trip into the report so a run is
    reproducible: which checkout Explore searched, and the fixture commit
    its keys were derived at."""
    return {
        "verdict": report.verdict.value,
        "repo_dir": str(repo_dir),
        "pinned_commit": pinned_commit,
        "haiku": model_aggregate_to_dict(report.haiku),
        "sonnet": model_aggregate_to_dict(report.sonnet),
        "realized_spend_usd": report.realized_spend_usd,
        "cumulative_stop_triggered": report.cumulative_stop_triggered,
        "uncallable_retry_ran": report.uncallable_retry_ran,
        "k_used": report.k_used,
        "samples": [sample_outcome_to_dict(o) for o in report.outcomes],
    }


def print_gate_report(report: GateReport) -> None:
    print(f"--- verdict: {report.verdict.value} ---")
    for label, agg in (("haiku", report.haiku), ("sonnet", report.sonnet)):
        print(
            f"  {label}: precision={agg.precision_point_estimate} "
            f"wilson_lower_bound={agg.precision_wilson_lower_bound} "
            f"format_failures={agg.format_failures} discards={agg.discards} timeouts={agg.timeouts} "
            f"observed_model_ids={sorted(agg.observed_model_ids)}"
        )
        for stratum, mean in sorted(agg.stratum_recall_mean.items()):
            print(f"    stratum {stratum} mean recall: {mean:.3f}")
    print(f"  realized_spend_usd: ${report.realized_spend_usd:.4f}")
    print(f"  cumulative_stop_triggered: {report.cumulative_stop_triggered}")
    if report.uncallable_retry_ran:
        print("  UNCALLABLE retry ran (K raised by 3) before this verdict")


# --- CLI -----------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Measure Explore's Haiku-vs-Sonnet locate recall and evaluate the "
            "adoption gate (local only, never CI)."
        )
    )
    parser.add_argument("--fixtures", required=True, type=Path, metavar="PATH", help="Path to the recall-cases fixture JSON")
    parser.add_argument("--k", type=int, default=DEFAULT_K, help=f"Samples per case per model (default: {DEFAULT_K})")
    parser.add_argument(
        "--budget-cap-usd", type=float, default=msmr.PER_RUN_BUDGET_CAP_USD,
        help=f"Per-dispatch --max-budget-usd cap (default: ${msmr.PER_RUN_BUDGET_CAP_USD})",
    )
    parser.add_argument(
        "--timeout-s", type=int, default=DEFAULT_TIMEOUT_S,
        help=f"Per-dispatch wall-clock timeout in seconds (default: {DEFAULT_TIMEOUT_S})",
    )
    parser.add_argument(
        "--cumulative-stop-usd", type=float, default=DEFAULT_CUMULATIVE_STOP_USD,
        help=f"Abort the whole batch once realized spend reaches this (default: ${DEFAULT_CUMULATIVE_STOP_USD})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Build every dispatch command and prompt without launching anything",
    )
    parser.add_argument(
        "--repo-dir", type=Path, metavar="PATH",
        help=(
            "Clean checkout at the fixture's pinned_commit for Explore to search. Required "
            "unless --dry-run; under --dry-run it is still validated if given."
        ),
    )
    parser.add_argument("--json", dest="json_out", metavar="PATH", help="Write the full report JSON to PATH")
    parser.add_argument(
        "--cases", metavar="ID[,ID...]",
        help="Restrict the run to this comma-separated list of fixture case IDs",
    )
    parser.add_argument(
        "--max-k-raises", type=int, default=DEFAULT_MAX_K_RAISES,
        help=(
            "Number of times an UNCALLABLE verdict may raise K by "
            f"{UNCALLABLE_K_INCREMENT} and re-evaluate before giving up "
            f"(default: {DEFAULT_MAX_K_RAISES}; pass 0 for a smoke batch that never raises K)"
        ),
    )
    parser.add_argument(
        "--keep-artifacts", dest="keep_artifacts", type=Path, metavar="DIR",
        help=(
            "Copy each sample's parent session .jsonl and its subagents/ directory into "
            "DIR/<case>-<arm>-<sample>/ before cleanup, instead of discarding them"
        ),
    )
    return parser


def _build_launcher(
    *, repo_dir: Path, keep_artifacts_dir: Path | None,
) -> Callable[..., SampleAttempt]:
    """Binds repo_dir and keep_artifacts_dir for execute_one_sample_attempt
    and assigns each (case, arm) pair its own 0-based sample_index for
    artifact naming, while keeping the returned callable's own signature
    -- (case, arm, *, budget_cap_usd, timeout_s) -- identical to every
    fake launcher the test suite passes to run_batch/run_one_logical_sample,
    so this wrapper is the only place that needs to know about the
    sample_index/keep_artifacts_dir kwargs at all."""
    sample_counts: dict[tuple[str, str], int] = defaultdict(int)

    def launcher(case: RecallCase, arm: str, *, budget_cap_usd: float, timeout_s: int) -> SampleAttempt:
        sample_index = sample_counts[(case.id, arm)]
        sample_counts[(case.id, arm)] += 1
        return execute_one_sample_attempt(
            case, arm, budget_cap_usd=budget_cap_usd, timeout_s=timeout_s, repo_dir=repo_dir,
            keep_artifacts_dir=keep_artifacts_dir, sample_index=sample_index,
        )

    return launcher


# The dry-run plan's dispatch prompts are never sent to a real agent, so a
# missing --repo-dir under --dry-run gets this placeholder instead of a
# hard failure. A live run still requires --repo-dir.
_DRY_RUN_REPO_DIR_PLACEHOLDER = Path("<repo-dir-not-provided>")


def main() -> int:
    # Same rationale as msmr.main(): a `claude -p` child leads its own
    # session, so a terminal hangup never reaches it. This routes
    # SIGHUP/SIGTERM down the same KeyboardInterrupt path that kills the
    # child's process group. `nohup` starts the process with SIGHUP
    # ignored, which must stand or a detached run would abort at logout.
    if signal.getsignal(signal.SIGHUP) is not signal.SIG_IGN:
        signal.signal(signal.SIGHUP, signal.default_int_handler)
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    args = build_arg_parser().parse_args()

    msmr.abort_if_subagent_model_env_set()
    try:
        pinned_commit, cases = load_fixture(args.fixtures)
    except (FixtureSchemaError, OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        sys.exit(f"ERROR: {exc}")

    if args.cases:
        requested_ids = [case_id.strip() for case_id in args.cases.split(",") if case_id.strip()]
        cases_by_id = {case.id: case for case in cases}
        unknown_ids = [case_id for case_id in requested_ids if case_id not in cases_by_id]
        if unknown_ids:
            sys.exit(f"ERROR: --cases references unknown case id(s): {unknown_ids}")
        cases = tuple(cases_by_id[case_id] for case_id in requested_ids)

    if args.repo_dir is not None:
        try:
            validate_repo_dir(args.repo_dir, pinned_commit)
        except RepoDirValidationError as exc:
            sys.exit(f"ERROR: {exc}")
        resolved_repo_dir = args.repo_dir.resolve()
    elif not args.dry_run:
        sys.exit("ERROR: --repo-dir is required unless --dry-run is given.")
    else:
        resolved_repo_dir = _DRY_RUN_REPO_DIR_PLACEHOLDER

    if args.dry_run:
        plan = build_dry_run_plan(cases, ARMS, args.k, budget_cap_usd=args.budget_cap_usd, repo_dir=resolved_repo_dir)
        print(f"--- dry run: {len(plan)} dispatch(es) planned, 0 launched ---")
        for entry in plan:
            print(f"  case={entry['case_id']} arm={entry['arm']} sample={entry['sample_index']}")
        if args.json_out:
            Path(args.json_out).write_text(json.dumps(plan, indent=2))
        return 0

    if args.keep_artifacts is not None:
        try:
            validate_keep_artifacts_dir(args.keep_artifacts)
        except KeepArtifactsValidationError as exc:
            sys.exit(f"ERROR: {exc}")
    keep_artifacts_dir = args.keep_artifacts.resolve() if args.keep_artifacts else None
    if keep_artifacts_dir is not None:
        keep_artifacts_dir.mkdir(parents=True, exist_ok=True)
    launcher = _build_launcher(repo_dir=resolved_repo_dir, keep_artifacts_dir=keep_artifacts_dir)
    report = run_recall_gate(
        cases, k=args.k, cumulative_stop_usd=args.cumulative_stop_usd, budget_cap_usd=args.budget_cap_usd,
        timeout_s=args.timeout_s, launcher=launcher, max_k_raises=args.max_k_raises,
    )
    print_gate_report(report)
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(gate_report_to_dict(report, repo_dir=resolved_repo_dir, pinned_commit=pinned_commit), indent=2)
        )
    return 0 if report.verdict == GateVerdict.PASS else 1


if __name__ == "__main__":
    sys.exit(main())
