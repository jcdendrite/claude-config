"""Statistics, freeze/invalidation checks, and the import-closure manifest
for the review bench. Standard library only (`statistics`, `math`, `random`,
`hashlib`, `ast`) -- no numerical dependency. evals/README.md's "Frozen
conditions and invalidation" section documents the manifest/precondition
checks' observable behavior.

Every check in this module raises HarnessInvalidatedError rather than
exiting the process directly, so each one is independently testable;
evals/run_review_bench.py's `analyze` and `freeze` subcommands catch it,
print its message, and return exit code 2. Every other subcommand lets it
reach `main()`'s top-level handler, which does the same.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import random
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import NormalDist, mean, stdev

from review_bench import adjudicate, defects, runner
from review_bench import arms as arms_mod
from review_bench.adjudicate import PrecisionFinding, RecallLabel

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EVALS_DIR = REPO_ROOT / "evals"
REVIEW_BENCH_DIR = EVALS_DIR / "review_bench"
CONFIG_SCRIPTS_DIR = REPO_ROOT / "claude" / ".claude" / "scripts"

# Defined in runner.py because runner.read_run_records is one of its raisers
# and analysis imports runner, not the reverse. Re-exported for
# run_review_bench.py's `analysis.HarnessInvalidatedError` catches.
HarnessInvalidatedError = runner.HarnessInvalidatedError

# --- Design constants -----------------------------------------------------

DELTA = 0.05  # the margin, 5 percentage points absolute, on both recall and pooled precision
ALPHA_ONE_SIDED = 0.025  # FDA (2016)'s one-sided convention; paired with a two-sided 95% interval
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 0
KAPPA_SUBSTANTIAL_FLOOR = 0.61  # Landis & Koch 1977's "substantial" threshold

# The planning variance's own two components: mean per-defect detection
# variance v = p(1-p) ~= 0.15, and between-defect true-difference variance
# tau^2 = 0.01. Frozen at design time -- never revised by a later campaign's
# own observed_sigma_d.
_PLANNING_V = 0.15
_PLANNING_TAU_SQUARED = 0.01


def planning_variance(k: int) -> float:
    """sigma_d^2(K) = 2v/K + tau^2 -- gives 0.04 at K=10, 0.03 at K=15,
    0.025 at K=20."""
    return (2 * _PLANNING_V) / k + _PLANNING_TAU_SQUARED


def n_min(k: int) -> int:
    """N_min = ceil((z_0.975 + z_0.80)^2 * sigma_d^2(k) / delta^2), using
    statistics.NormalDist for the z-values rather than a hardcoded 7.849 --
    n_min(10) == 126, n_min(15) == 95, n_min(20) == 79."""
    z_alpha = NormalDist().inv_cdf(1 - ALPHA_ONE_SIDED)  # z_0.975
    z_power = NormalDist().inv_cdf(0.80)  # z_0.80, 80% power
    variance = planning_variance(k)
    return math.ceil(((z_alpha + z_power) ** 2) * variance / (DELTA**2))


# --- Recall: per-defect detection rates and the sub-K/2 drop ---------------


@dataclass(frozen=True)
class DefectRecallCounts:
    """One defect's completed-run counts per arm. A run is completed when
    the recall judge labeled it FOUND or NOT_FOUND; a missing run, or one
    the recall judge never labeled, counts in neither the numerator nor the
    denominator."""

    defect_id: str
    found_by_arm: Mapping[str, int]
    completed_by_arm: Mapping[str, int]

    def detection_rate(self, arm: str) -> float:
        completed = self.completed_by_arm.get(arm, 0)
        return self.found_by_arm.get(arm, 0) / completed if completed else 0.0


def compute_recall_counts(
    reviewer_records: Sequence[runner.RunRecord], recall_labels_by_defect: Mapping[str, Mapping[str, RecallLabel]],
) -> dict[str, DefectRecallCounts]:
    """Per defect, per arm: FOUND count and completed (FOUND+NOT_FOUND)
    count, joining every reviewer run's own arm against the recall judge's
    per-run-ID label."""
    found_by_defect: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    completed_by_defect: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    touched_defects: set[str] = set()
    for record in reviewer_records:
        label = recall_labels_by_defect.get(record.defect_id, {}).get(record.opaque_run_id)
        if label is None:
            continue
        touched_defects.add(record.defect_id)
        completed_by_defect[record.defect_id][record.arm] += 1
        if label.label == "FOUND":
            found_by_defect[record.defect_id][record.arm] += 1
    return {
        defect_id: DefectRecallCounts(
            defect_id=defect_id, found_by_arm=dict(found_by_defect[defect_id]),
            completed_by_arm=dict(completed_by_defect[defect_id]),
        )
        for defect_id in touched_defects
    }


def kept_recall_defect_ids(
    counts_by_defect: Mapping[str, DefectRecallCounts], arms: Sequence[str], *, k: int,
) -> list[str]:
    """A defect with fewer than K/2 completed runs in EITHER arm is dropped
    from both arms; exactly K/2 is kept."""
    threshold = k / 2
    return sorted(
        defect_id
        for defect_id, counts in counts_by_defect.items()
        if all(counts.completed_by_arm.get(arm, 0) >= threshold for arm in arms)
    )


def arm_recall(
    counts_by_defect: Mapping[str, DefectRecallCounts], defect_ids: Sequence[str], arm: str,
) -> float | None:
    """An arm's recall is the mean of its per-defect detection rates across
    defect_ids. None when defect_ids is empty: an arm that finds nothing in a
    non-empty set has a real recall of 0.0, which is not the same as no
    defects to measure."""
    if not defect_ids:
        return None
    return mean(counts_by_defect[defect_id].detection_rate(arm) for defect_id in defect_ids)


# --- Precision: pooled ratio per arm ----------------------------------------


@dataclass(frozen=True)
class DefectPrecisionCounts:
    """One defect's adjudicated-finding counts per arm, across every
    completed run the precision judge labeled."""

    defect_id: str
    valid_by_arm: Mapping[str, int]
    total_by_arm: Mapping[str, int]


def compute_precision_counts(
    reviewer_records: Sequence[runner.RunRecord],
    precision_labels_by_defect: Mapping[str, Mapping[str, list[PrecisionFinding]]],
) -> dict[str, DefectPrecisionCounts]:
    """Per defect, per arm: VALID and total adjudicated-finding counts. A
    defect whose precision-judge run is missing entirely has no entry here
    at all -- kept_precision_defect_ids drops it from both arms."""
    valid_by_defect: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    total_by_defect: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    touched_defects: set[str] = set()
    for record in reviewer_records:
        findings = precision_labels_by_defect.get(record.defect_id, {}).get(record.opaque_run_id)
        if findings is None:
            continue
        touched_defects.add(record.defect_id)
        total_by_defect[record.defect_id][record.arm] += len(findings)
        valid_by_defect[record.defect_id][record.arm] += sum(1 for finding in findings if finding.label == "VALID")
    return {
        defect_id: DefectPrecisionCounts(
            defect_id=defect_id, valid_by_arm=dict(valid_by_defect[defect_id]),
            total_by_arm=dict(total_by_defect[defect_id]),
        )
        for defect_id in touched_defects
    }


def kept_precision_defect_ids(
    recall_kept_ids: Sequence[str], precision_counts_by_defect: Mapping[str, DefectPrecisionCounts],
) -> list[str]:
    """Precision analysis starts from recall's own kept-defect set -- every
    completed run of every defect the recall analysis keeps -- then drops a
    defect whose precision-judge run is missing entirely; reported as that
    drop count by the caller."""
    return [defect_id for defect_id in recall_kept_ids if defect_id in precision_counts_by_defect]


def pooled_precision(
    counts_by_defect: Mapping[str, DefectPrecisionCounts], defect_ids: Sequence[str], arm: str,
) -> float | None:
    """VALID findings divided by all adjudicated findings, pooled across
    every defect in defect_ids. None when the arm has no adjudicated finding
    there: precision over zero findings is undefined, not 0.0."""
    total_valid = sum(counts_by_defect[d].valid_by_arm.get(arm, 0) for d in defect_ids if d in counts_by_defect)
    total_all = sum(counts_by_defect[d].total_by_arm.get(arm, 0) for d in defect_ids if d in counts_by_defect)
    return total_valid / total_all if total_all else None


# --- Gating sets and fixture clusters ----------------------------------------

GATE_CODE = "code"
GATE_MARKDOWN = "markdown"
GATES = (GATE_CODE, GATE_MARKDOWN)
STRATUM_SECONDARY = "secondary"

# The one statement of gate membership: every (source, file_is_markdown) cell
# of defects.KNOWN_SOURCES lands in exactly one gate or in the secondary
# stratum, which is reported and never gates. A cell this table lacks is
# unclassified, and gate_of refuses it. Both `analyze` modes read it, and it
# is in the harness closure, so a post-freeze edit changes the manifest.
GATING_RULE: dict[tuple[str, bool], str] = {
    ("szz", False): GATE_CODE,
    ("review-round", False): GATE_CODE,
    ("review-round", True): GATE_MARKDOWN,
    ("pr-comment", True): GATE_MARKDOWN,
    ("pr-comment", False): STRATUM_SECONDARY,
    ("szz", True): STRATUM_SECONDARY,
}

# A fixture is the (base_commit, head_commit) pair: the reviewer's input is
# constant per fixture and lens, so defects on one fixture share their
# variance.
FIXTURE_KEY_FIELDS = ("base_commit", "head_commit")

FixtureKey = tuple[str, str]


def validate_gating_rule() -> None:
    """Raises HarnessInvalidatedError when GATING_RULE names a source outside
    defects.KNOWN_SOURCES or leaves a KNOWN_SOURCES cell unclassified."""
    rule_sources = {source for source, _is_markdown in GATING_RULE}
    unknown_sources = sorted(rule_sources - defects.KNOWN_SOURCES)
    unclassified_cells = sorted(
        (source, is_markdown)
        for source in defects.KNOWN_SOURCES for is_markdown in (False, True)
        if (source, is_markdown) not in GATING_RULE
    )
    if unknown_sources or unclassified_cells:
        raise HarnessInvalidatedError(
            f"gating rule is out of step with KNOWN_SOURCES (unknown sources: {unknown_sources}; "
            f"unclassified cells: {unclassified_cells})"
        )


def gating_rule_record() -> dict:
    """The gating rule as the JSON-ready record `freeze` writes to
    conditions.json. GATING_RULE here is the authority, so the record is
    informational."""
    validate_gating_rule()
    cells_by_assignment: dict[str, list[list]] = defaultdict(list)
    for (source, is_markdown), assignment in sorted(GATING_RULE.items()):
        cells_by_assignment[assignment].append([source, is_markdown])
    return {
        "cells_by_gate": {gate: cells_by_assignment[gate] for gate in GATES},
        "secondary_cells": cells_by_assignment[STRATUM_SECONDARY],
        "cluster_key": list(FIXTURE_KEY_FIELDS),
        "later_arm_rule": (
            "certified only when recall and pooled precision both pass non-inferiority at delta in the code gate "
            "and in the markdown gate, and neither gate is short or not sensitive; a failing test dominates, "
            "then short or not sensitive, then pass"
        ),
    }


def gate_of(defect: defects.ConfirmedDefect) -> str:
    """The gate, or STRATUM_SECONDARY, a defect belongs to."""
    try:
        return GATING_RULE[(defect.source, defect.file_is_markdown)]
    except KeyError:
        raise HarnessInvalidatedError(
            f"defect {defect.id!r} (source {defect.source!r}, file_is_markdown {defect.file_is_markdown!r}) "
            "falls in no cell of the gating rule"
        ) from None


def gate_defect_ids(
    confirmed: Sequence[defects.ConfirmedDefect], defect_ids: Sequence[str],
) -> dict[str, list[str]]:
    """defect_ids split by gate, keeping their order. Every key of GATES and
    STRATUM_SECONDARY is present, empty when no defect lands there."""
    defects_by_id = {defect.id: defect for defect in confirmed}
    split: dict[str, list[str]] = {gate: [] for gate in (*GATES, STRATUM_SECONDARY)}
    for defect_id in defect_ids:
        split[gate_of(defects_by_id[defect_id])].append(defect_id)
    return split


def source_defect_ids(
    confirmed: Sequence[defects.ConfirmedDefect], defect_ids: Sequence[str],
) -> dict[str, list[str]]:
    """defect_ids split by source, keeping their order. Every source in
    defects.KNOWN_SOURCES is present, empty when no defect has it."""
    defects_by_id = {defect.id: defect for defect in confirmed}
    split: dict[str, list[str]] = {source: [] for source in sorted(defects.KNOWN_SOURCES)}
    for defect_id in defect_ids:
        split[defects_by_id[defect_id].source].append(defect_id)
    return split


def fixture_clusters(confirmed: Sequence[defects.ConfirmedDefect]) -> dict[str, FixtureKey]:
    """Each defect ID's fixture key, the cluster map the bootstrap resamples by."""
    base_commit_field, head_commit_field = FIXTURE_KEY_FIELDS
    return {defect.id: (getattr(defect, base_commit_field), getattr(defect, head_commit_field)) for defect in confirmed}


def fixture_count(defect_ids: Sequence[str], cluster_by_defect: Mapping[str, FixtureKey]) -> int:
    return len({cluster_by_defect[defect_id] for defect_id in defect_ids})


def largest_cluster_size(defect_ids: Sequence[str], cluster_by_defect: Mapping[str, FixtureKey]) -> int:
    """The defect count of the fixture holding the most of defect_ids, 0 when empty."""
    sizes: dict[FixtureKey, int] = defaultdict(int)
    for defect_id in defect_ids:
        sizes[cluster_by_defect[defect_id]] += 1
    return max(sizes.values(), default=0)


def effective_n_meets_n_min(
    defect_ids: Sequence[str], cluster_by_defect: Mapping[str, FixtureKey], *, n_min_value: int,
) -> bool:
    """The short-gate test: a gate meets N_min when its kept defects span at
    least n_min_value distinct fixtures. An empty set is below N_min."""
    return fixture_count(defect_ids, cluster_by_defect) >= n_min_value


# --- Intervals: paired cluster bootstrap --------------------------------------


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile (numpy's default "linear" method),
    over already-sorted values."""
    if not sorted_values:
        raise ValueError("_percentile: no values to summarize")
    index = q * (len(sorted_values) - 1)
    lower_index = math.floor(index)
    upper_index = math.ceil(index)
    if lower_index == upper_index:
        return sorted_values[int(index)]
    fraction = index - lower_index
    return sorted_values[lower_index] + (sorted_values[upper_index] - sorted_values[lower_index]) * fraction


def bootstrap_interval_with_drops(
    defect_ids: Sequence[str], statistic_fn: Callable[[Sequence[str]], float | None], *,
    cluster_by_defect: Mapping[str, FixtureKey], resamples: int = BOOTSTRAP_RESAMPLES, seed: int = BOOTSTRAP_SEED,
    confidence: float = 1 - 2 * ALPHA_ONE_SIDED,
) -> tuple[tuple[float, float] | None, int]:
    """A paired cluster bootstrap over fixtures: defect_ids are grouped by
    their fixture key (in order of first appearance), each of `resamples`
    draws as many fixtures as there are with replacement, and statistic_fn
    computes the wanted statistic over the member defect IDs of the drawn
    fixtures. Both arms' data travel together because statistic_fn's callers
    (e.g. arm_recall) look both arms up from the same resampled ID. With
    every fixture a singleton and in defect_ids order, the draws are those of
    a plain per-defect bootstrap.

    Returns (percentile interval at `confidence`, dropped resample count). A
    resample whose statistic is None is dropped. The interval is None with
    fewer than two fixtures, because one cluster has zero variance, and when
    every resample drops."""
    clusters: dict[FixtureKey, list[str]] = {}
    for defect_id in defect_ids:
        clusters.setdefault(cluster_by_defect[defect_id], []).append(defect_id)
    fixtures = list(clusters.values())
    if len(fixtures) < 2:
        return None, 0
    rng = random.Random(seed)
    n = len(fixtures)
    resampled_statistics: list[float] = []
    dropped = 0
    for _ in range(resamples):
        value = statistic_fn([defect_id for _ in range(n) for defect_id in fixtures[rng.randrange(n)]])
        if value is None:
            dropped += 1
        else:
            resampled_statistics.append(value)
    if not resampled_statistics:
        return None, dropped
    resampled_statistics.sort()
    lower_q = (1 - confidence) / 2
    return (_percentile(resampled_statistics, lower_q), _percentile(resampled_statistics, 1 - lower_q)), dropped


def bootstrap_interval(
    defect_ids: Sequence[str], statistic_fn: Callable[[Sequence[str]], float | None], *,
    cluster_by_defect: Mapping[str, FixtureKey], resamples: int = BOOTSTRAP_RESAMPLES, seed: int = BOOTSTRAP_SEED,
    confidence: float = 1 - 2 * ALPHA_ONE_SIDED,
) -> tuple[float, float] | None:
    """bootstrap_interval_with_drops' interval alone, for a statistic that is
    never None."""
    return bootstrap_interval_with_drops(
        defect_ids, statistic_fn, cluster_by_defect=cluster_by_defect, resamples=resamples, seed=seed,
        confidence=confidence,
    )[0]


# --- Verdicts ----------------------------------------------------------------

SENSITIVITY_SENSITIVE = "sensitive"
SENSITIVITY_NOT_SENSITIVE = "not-sensitive"

NONINFERIORITY_PASS = "pass"
NONINFERIORITY_FAIL = "fail"

CERTIFICATION_CERTIFIED = "certified"
CERTIFICATION_NOT_CERTIFIED = "not-certified"
CERTIFICATION_INCONCLUSIVE = "inconclusive"

GATE_OUTCOME_FAIL = "fail"
GATE_OUTCOME_INCONCLUSIVE = "inconclusive"
GATE_OUTCOME_PASS = "pass"


def recall_difference(
    recall_counts_by_defect: Mapping[str, DefectRecallCounts], defect_ids: Sequence[str], arm_minuend: str,
    arm_subtrahend: str,
) -> float | None:
    """recall_minuend - recall_subtrahend over defect_ids, None when it is empty."""
    minuend = arm_recall(recall_counts_by_defect, defect_ids, arm_minuend)
    subtrahend = arm_recall(recall_counts_by_defect, defect_ids, arm_subtrahend)
    return None if minuend is None or subtrahend is None else minuend - subtrahend


def precision_difference(
    precision_counts_by_defect: Mapping[str, DefectPrecisionCounts], defect_ids: Sequence[str], arm_minuend: str,
    arm_subtrahend: str,
) -> float | None:
    """Pooled precision_minuend - precision_subtrahend over defect_ids, None
    when either arm has no adjudicated finding there."""
    minuend = pooled_precision(precision_counts_by_defect, defect_ids, arm_minuend)
    subtrahend = pooled_precision(precision_counts_by_defect, defect_ids, arm_subtrahend)
    return None if minuend is None or subtrahend is None else minuend - subtrahend


def baseline_sensitivity_verdict(
    recall_counts_by_defect: Mapping[str, DefectRecallCounts], kept_defect_ids: Sequence[str],
    arm_1: str, arm_2: str, *, cluster_by_defect: Mapping[str, FixtureKey], delta: float = DELTA,
    resamples: int = BOOTSTRAP_RESAMPLES, seed: int = BOOTSTRAP_SEED,
) -> tuple[str | None, tuple[float, float] | None]:
    """ICH E10's assay sensitivity: sensitive when the lower limit of the
    two-sided 95% interval for recall_1 - recall_2 exceeds delta. Never
    grounds to revise the defect set. (None, None) when the interval is
    undefined."""
    interval = bootstrap_interval(
        kept_defect_ids,
        lambda resample_ids: recall_difference(recall_counts_by_defect, resample_ids, arm_1, arm_2),
        cluster_by_defect=cluster_by_defect, resamples=resamples, seed=seed,
    )
    if interval is None:
        return None, None
    verdict = SENSITIVITY_SENSITIVE if interval[0] > delta else SENSITIVITY_NOT_SENSITIVE
    return verdict, interval


def recall_noninferiority_verdict(
    recall_counts_by_defect: Mapping[str, DefectRecallCounts], kept_defect_ids: Sequence[str],
    arm_baseline: str, arm_x: str, *, cluster_by_defect: Mapping[str, FixtureKey], delta: float = DELTA,
    resamples: int = BOOTSTRAP_RESAMPLES, seed: int = BOOTSTRAP_SEED,
) -> tuple[str | None, tuple[float, float] | None]:
    """Arm X passes when the lower limit of the two-sided 95% interval for
    recall_X - recall_1 exceeds -delta. (None, None) when the interval is
    undefined."""
    interval = bootstrap_interval(
        kept_defect_ids,
        lambda resample_ids: recall_difference(recall_counts_by_defect, resample_ids, arm_x, arm_baseline),
        cluster_by_defect=cluster_by_defect, resamples=resamples, seed=seed,
    )
    if interval is None:
        return None, None
    return (NONINFERIORITY_PASS if interval[0] > -delta else NONINFERIORITY_FAIL), interval


def precision_noninferiority_verdict(
    precision_counts_by_defect: Mapping[str, DefectPrecisionCounts], kept_defect_ids: Sequence[str],
    arm_baseline: str, arm_x: str, *, cluster_by_defect: Mapping[str, FixtureKey], delta: float = DELTA,
    resamples: int = BOOTSTRAP_RESAMPLES, seed: int = BOOTSTRAP_SEED,
) -> tuple[str | None, tuple[float, float] | None, int]:
    """The same rule as recall_noninferiority_verdict, on pooled precision.
    A resample in which either arm has no adjudicated finding has no
    difference and is dropped; the third element is that drop count. The
    verdict and interval are None when the interval is undefined, including
    when every resample drops."""
    interval, dropped_resamples = bootstrap_interval_with_drops(
        kept_defect_ids,
        lambda resample_ids: precision_difference(precision_counts_by_defect, resample_ids, arm_x, arm_baseline),
        cluster_by_defect=cluster_by_defect, resamples=resamples, seed=seed,
    )
    if interval is None:
        return None, None, dropped_resamples
    verdict = NONINFERIORITY_PASS if interval[0] > -delta else NONINFERIORITY_FAIL
    return verdict, interval, dropped_resamples


@dataclass(frozen=True)
class GateResult:
    """One gate's later-arm evidence. A verdict is None when its interval is
    undefined. `baseline_sensitivity` is the baseline report's verdict for
    the gate, None when undefined there. `short` is True when the gate's
    distinct fixture count is below N_min."""

    recall_verdict: str | None
    precision_verdict: str | None
    baseline_sensitivity: str | None
    short: bool


def gate_outcome(result: GateResult) -> str:
    """One precedence: a failing test dominates at any N, then a gate that is
    short, not sensitive, or lacks a verdict, then pass. A gate lacking a
    verdict or a baseline verdict is short by construction, and never fails
    for it."""
    if NONINFERIORITY_FAIL in (result.recall_verdict, result.precision_verdict):
        return GATE_OUTCOME_FAIL
    cannot_certify = (
        result.short
        or result.recall_verdict is None
        or result.precision_verdict is None
        or result.baseline_sensitivity != SENSITIVITY_SENSITIVE
    )
    return GATE_OUTCOME_INCONCLUSIVE if cannot_certify else GATE_OUTCOME_PASS


def certify_later_arm(gate_results: Mapping[str, GateResult]) -> str:
    """A later arm is certified only when it passes recall and precision in
    the code gate and in the markdown gate, and neither gate is short or not
    sensitive. The same precedence applies across gates as within one: any
    failing gate gives not-certified, whatever the other gate shows; else any
    short or not-sensitive gate gives inconclusive; else certified. Each
    component test keeps alpha = ALPHA_ONE_SIDED with no multiplicity
    adjustment, because every one must pass. A stratum outside both gates
    has no entry here and never changes the outcome."""
    if set(gate_results) != set(GATES):
        raise ValueError(f"certify_later_arm needs exactly the gates {list(GATES)}, got {sorted(gate_results)}")
    outcomes = {gate_outcome(result) for result in gate_results.values()}
    if GATE_OUTCOME_FAIL in outcomes:
        return CERTIFICATION_NOT_CERTIFIED
    if GATE_OUTCOME_INCONCLUSIVE in outcomes:
        return CERTIFICATION_INCONCLUSIVE
    return CERTIFICATION_CERTIFIED


# The `freeze_identity` keys beyond the `_FROZEN_DIGEST_FIELDS` entries.
_FREEZE_IDENTITY_SCALAR_KEYS = ("harness_closure_hash", "k", "environment")


def load_baseline_gate_verdicts(path: Path, frozen: Mapping) -> dict[str, str | None]:
    """Each gate's sensitivity verdict from the baseline-mode report at path.
    Raises HarnessInvalidatedError, never reading a verdict as sensitive, for:
    an unreadable file, a missing gate or verdict key, a missing or malformed
    `freeze_identity`, a verdict outside {sensitive, not-sensitive, null}, a
    null verdict beside two or more fixtures, and a report whose freeze
    identity differs from `frozen` in any field (the closure, every frozen
    digest, K, or the campaign environment). A null verdict is valid only when
    the report's own fixture count for the gate is below two, an empty gate
    included."""
    try:
        report = json.loads(path.read_text())
        gate_verdicts: dict[str, str | None] = {}
        for gate in GATES:
            gate_report = report["gates"][gate]
            verdict = gate_report["baseline_sensitivity"]["verdict"]
            fixtures = gate_report["fixture_count"]
            if verdict not in (SENSITIVITY_SENSITIVE, SENSITIVITY_NOT_SENSITIVE, None):
                raise ValueError(f"{gate} gate verdict {verdict!r} is not a known verdict")
            if verdict is None and (not isinstance(fixtures, int) or fixtures >= 2):
                raise ValueError(f"{gate} gate has a null verdict beside a fixture count of {fixtures!r}")
            gate_verdicts[gate] = verdict
        identity = report["freeze_identity"]
        if not isinstance(identity, dict):
            raise TypeError(f"freeze_identity must be an object, got {type(identity).__name__}")
        absent = [key for key in (*_FROZEN_DIGEST_FIELDS, *_FREEZE_IDENTITY_SCALAR_KEYS) if key not in identity]
        if absent:
            raise KeyError(absent)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HarnessInvalidatedError(
            f"baseline report ({path}) is unreadable, or is missing or malformed in a field a later arm reads: {exc!r}"
        ) from exc
    differing = _differing_freeze_identity_fields(frozen, identity)
    if differing:
        # Regenerating over the earlier freeze's records would pass this check on stale records.
        raise HarnessInvalidatedError(
            f"invalidated -- rerun all arms: baseline report ({path}) was computed under a different freeze than "
            f"conditions.json records (differing: {differing}) -- rerun all arms under the new freeze, "
            "then regenerate the baseline report"
        )
    return gate_verdicts


# --- Secondary columns, never gating (evals/README.md's "Reading the report"
# section) -----------------------------------------------------------------


def read_token_stats(records: Sequence[runner.RunRecord]) -> dict[str, float]:
    totals: dict[str, list[int]] = defaultdict(list)
    for record in records:
        totals[record.arm].append(record.read_tokens_est)
    return {arm: mean(values) for arm, values in totals.items() if values}


def partial_and_paged_counts(records: Sequence[runner.RunRecord]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"partial_view_reads": 0, "paged_followups": 0})
    for record in records:
        counts[record.arm]["partial_view_reads"] += record.partial_view_reads
        counts[record.arm]["paged_followups"] += record.paged_followups
    return dict(counts)


def whole_file_read_adherence(records: Sequence[runner.RunRecord]) -> dict[str, float]:
    """Mean whole-file-reads-of-changed-files per run, per arm -- the
    function-context arm's reads are the rule-violation diagnostic, the
    current-rule arm's are the coverage diagnostic."""
    totals: dict[str, list[int]] = defaultdict(list)
    for record in records:
        totals[record.arm].append(record.whole_file_reads_of_changed_files)
    return {arm: mean(values) for arm, values in totals.items() if values}


def missing_run_counts_by_reason(records: Sequence[runner.RunRecord]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for record in records:
        if record.status != runner.STATUS_OK:
            counts[record.arm][record.missing_reason or "unknown"] += 1
    return {arm: dict(reasons) for arm, reasons in counts.items()}


def cost_totals_by_arm(records: Sequence[runner.RunRecord]) -> dict[str, dict[str, float | int]]:
    """Per-arm summed total_cost_usd over the runs that recorded one, beside
    how many of the arm's runs that covers -- a run with no cost is left out
    of the sum, never counted as zero."""
    totals: dict[str, dict[str, float | int]] = defaultdict(lambda: {"total_cost_usd": 0.0, "runs_priced": 0, "runs": 0})
    for record in records:
        arm_totals = totals[record.arm]
        arm_totals["runs"] += 1
        if record.total_cost_usd is not None:
            arm_totals["total_cost_usd"] += record.total_cost_usd
            arm_totals["runs_priced"] += 1
    return dict(totals)


def out_of_session_counts_by_arm(records: Sequence[runner.RunRecord]) -> dict[str, int]:
    """Per-arm count only -- never the paths themselves (evals/README.md's
    "Out-of-session reads" section: committed results carry only the
    per-arm count, never a path)."""
    counts: dict[str, int] = defaultdict(int)
    for record in records:
        counts[record.arm] += len(record.out_of_session_paths)
    return dict(counts)


def recall_by_fix_date_half(
    recall_counts_by_defect: Mapping[str, DefectRecallCounts], kept_defect_ids: Sequence[str],
    fix_dates_by_defect: Mapping[str, str], arm: str,
) -> dict[str, float | None]:
    """Recall split at the median fix date of the kept defects -- an
    observable proxy for memorization exposure, never gating. A half with no
    defects is None."""
    dated = sorted(kept_defect_ids, key=lambda defect_id: datetime.fromisoformat(fix_dates_by_defect[defect_id]))
    midpoint = len(dated) // 2
    return {
        "earlier_half": arm_recall(recall_counts_by_defect, dated[:midpoint], arm),
        "later_half": arm_recall(recall_counts_by_defect, dated[midpoint:], arm),
    }


def recall_diff_over_read_cap_stratum(
    recall_counts_by_defect: Mapping[str, DefectRecallCounts], kept_defect_ids: Sequence[str],
    over_cap_defect_ids: Sequence[str], arm_baseline: str, arm_x: str,
) -> float | None:
    """recall_X - recall_baseline, restricted to kept defects flagged
    over_read_cap -- files over one Read call, where the function-context rule changes
    behavior the most. None when no kept defect is flagged."""
    stratum = [defect_id for defect_id in kept_defect_ids if defect_id in set(over_cap_defect_ids)]
    return recall_difference(recall_counts_by_defect, stratum, arm_x, arm_baseline)


def observed_sigma_d(
    recall_counts_by_defect: Mapping[str, DefectRecallCounts], kept_defect_ids: Sequence[str],
    arm_baseline: str, arm_x: str, *, cluster_by_defect: Mapping[str, FixtureKey],
) -> float | None:
    """The observed per-defect-difference standard deviation -- reported for
    the record, never used to revise N_min or delta. None with fewer than two
    fixtures, the same bar the intervals use."""
    if fixture_count(kept_defect_ids, cluster_by_defect) < 2:
        return None
    diffs = [
        recall_counts_by_defect[defect_id].detection_rate(arm_x)
        - recall_counts_by_defect[defect_id].detection_rate(arm_baseline)
        for defect_id in kept_defect_ids
    ]
    return stdev(diffs)


# --- Harness closure (evals/README.md's "Frozen conditions and invalidation"
# section) ----------------------------------------------------------------

_CLOSURE_ROOTS: tuple[str, ...] = ("review_bench.runner", "review_bench.adjudicate", "review_bench.analysis")


def _resolve_first_party_module(module_name: str) -> Path | None:
    """module_name -> its own source file, for exactly this harness's fixed
    package layout -- never a generic import-machinery lookup (which would
    import a dotted name's parent package as a side effect just to probe a
    guessed submodule name). A name outside this layout (a stdlib or
    third-party import) returns None, which is correct: the closure only
    ever covers first-party files."""
    if module_name == "review_bench":
        candidate = REVIEW_BENCH_DIR / "__init__.py"
        return candidate if candidate.is_file() else None
    if module_name.startswith("review_bench."):
        relative = module_name.removeprefix("review_bench.").replace(".", "/")
        candidate = REVIEW_BENCH_DIR / f"{relative}.py"
        return candidate if candidate.is_file() else None
    for base_dir in (EVALS_DIR, CONFIG_SCRIPTS_DIR):
        candidate = base_dir / f"{module_name}.py"
        if candidate.is_file():
            return candidate
    return None


def _imported_module_names(source_path: Path) -> set[str]:
    """Every module name source_path's own `import X` / `from X import Y`
    statements name, anywhere in the file -- a plain ast.parse, never an
    execution of the file. `from X import Y` adds both X and the guessed
    submodule name `X.Y`, since AST alone cannot tell whether Y is a
    submodule or an attribute; _resolve_first_party_module silently drops
    whichever guess isn't a real file."""
    tree = ast.parse(source_path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level > 0:
                # This closure's only package is review_bench itself, so a
                # same-package relative import resolves against it directly.
                module = "review_bench" + (f".{module}" if module else "")
            if not module:
                continue
            names.add(module)
            names.update(f"{module}.{alias.name}" for alias in node.names)
    return names


def compute_harness_closure(*, repo_root: Path = REPO_ROOT) -> dict[str, str]:
    """Every first-party source file reachable from review_bench.runner,
    .adjudicate and .analysis, limited to files inside the repo. Computed by
    statically walking their import graph rather than from a hand-kept list.
    The closure excludes run_review_bench.py and the mine_*.py miners, so an
    edit to either goes undetected."""
    visited: set[str] = set()
    queue: list[str] = list(_CLOSURE_ROOTS)
    closure: dict[str, str] = {}
    repo_root = repo_root.resolve()
    while queue:
        module_name = queue.pop()
        if module_name in visited:
            continue
        visited.add(module_name)
        source_path = _resolve_first_party_module(module_name)
        if source_path is None:
            continue
        resolved = source_path.resolve()
        relpath = resolved.relative_to(repo_root)
        closure[str(relpath)] = hashlib.sha256(resolved.read_bytes()).hexdigest()
        queue.extend(_imported_module_names(resolved))
    return closure


def closure_manifest_hash(closure: Mapping[str, str]) -> str:
    """One combined hash over the whole closure, sorted by path for
    determinism -- what `freeze`/`analyze` actually compare (evals/README.md's
    "Frozen conditions and invalidation" section)."""
    canonical = "\n".join(f"{path}:{digest}" for path, digest in sorted(closure.items()))
    return hashlib.sha256(canonical.encode()).hexdigest()


# --- Freeze preconditions and invalidation (evals/README.md's "Frozen
# conditions and invalidation" section) -------------------------------------


def load_frozen_conditions(path: Path) -> dict:
    """The parsed conditions.json, with the `environment` and
    `harness_closure` fields every comparison reads checked for shape. Raises
    HarnessInvalidatedError -- naming the file and the unreadable/missing/
    malformed field -- rather than letting a raw OSError/ValueError/KeyError/
    TypeError escape to a subcommand."""
    try:
        conditions = json.loads(path.read_text())
        environment = conditions["environment"]
        absent = [key for key in ("cli_version", "ambient_config_commit") if key not in environment]
        if absent:
            raise KeyError(absent)
        harness_closure = conditions["harness_closure"]
        if not isinstance(harness_closure, dict):
            raise TypeError(f"harness_closure must be an object, got {type(harness_closure).__name__}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HarnessInvalidatedError(
            f"invalidated -- rerun all arms: baseline conditions file "
            f"({path}) is unreadable or missing an expected field: {exc!r}"
        ) from exc
    return conditions


def check_manifest_matches(current_closure: Mapping[str, str], frozen_closure: Mapping[str, str]) -> None:
    """Raises HarnessInvalidatedError naming the changed/added/removed
    path(s) when current_closure's hashes differ from the frozen manifest's."""
    if dict(current_closure) == dict(frozen_closure):
        return
    added = sorted(set(current_closure) - set(frozen_closure))
    removed = sorted(set(frozen_closure) - set(current_closure))
    changed = sorted(
        path for path in set(current_closure) & set(frozen_closure) if current_closure[path] != frozen_closure[path]
    )
    parts = []
    if changed:
        parts.append(f"changed: {changed}")
    if added:
        parts.append(f"added: {added}")
    if removed:
        parts.append(f"removed: {removed}")
    raise HarnessInvalidatedError(
        f"invalidated -- rerun all arms: harness closure manifest mismatch ({'; '.join(parts)})"
    )


def hash_directory(directory: Path) -> str:
    """One combined hash over every file under directory, sorted by relative
    path. Raises HarnessInvalidatedError for a missing or file-less directory,
    since an empty listing hashes to a constant that would freeze nothing."""
    files = sorted(path for path in directory.rglob("*") if path.is_file()) if directory.is_dir() else []
    if not files:
        raise HarnessInvalidatedError(f"{directory} is missing or holds no files -- there is nothing to hash")
    parts = [f"{path.relative_to(directory)}:{hashlib.sha256(path.read_bytes()).hexdigest()}" for path in files]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def defects_file_hash(defects_path: Path) -> str:
    """The sha256 of the defects file's bytes, the digest conditions.json and
    the baseline report both record. Raises HarnessInvalidatedError for an
    unreadable file."""
    try:
        return hashlib.sha256(defects_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise HarnessInvalidatedError(f"defects file {defects_path} is unreadable: {exc}") from exc


def compute_frozen_fields(defects_path: Path, arms_root: Path) -> dict:
    """Every conditions.json field derived from files on disk. `freeze`
    records these; `analyze`, `run`, and `judge` recompute them and compare
    (evals/README.md's "Frozen conditions and invalidation" section). The
    harness closure is computed here too, so all of them hash the same set."""
    defects_json_hash = defects_file_hash(defects_path)
    closure = compute_harness_closure()
    return {
        "defect_ids": sorted(defect.id for defect in defects.load_confirmed_defects(defects_path)),
        "harness_closure_hash": closure_manifest_hash(closure),
        "harness_closure": closure,
        "arm_dir_hashes": {
            arm: hash_directory(arms_root / arm) for arm in (arms_mod.ARM_CURRENT_RULE, arms_mod.ARM_FUNCTION_CONTEXT)
        },
        "judge_agent_hashes": {
            "bench-judge-recall": hashlib.sha256(adjudicate.RECALL_JUDGE_AGENT_FILE.read_bytes()).hexdigest(),
            "bench-judge-precision": hashlib.sha256(adjudicate.PRECISION_JUDGE_AGENT_FILE.read_bytes()).hexdigest(),
        },
        "defects_json_hash": defects_json_hash,
        "prompt_template_hashes": {
            "review_prompt": hashlib.sha256(runner.REVIEW_PROMPT_TEMPLATE.encode()).hexdigest(),
            "dispatch_prompt": hashlib.sha256(runner.DISPATCH_PROMPT_TEMPLATE.encode()).hexdigest(),
            "judge_inner_prompt": hashlib.sha256(adjudicate.JUDGE_INNER_PROMPT_TEMPLATE.encode()).hexdigest(),
        },
    }


# The compute_frozen_fields entries compared field by field; the closure is
# compared separately so a mismatch names the changed files.
_FROZEN_DIGEST_FIELDS = (
    "defects_json_hash", "defect_ids", "arm_dir_hashes", "judge_agent_hashes", "prompt_template_hashes",
)


def _differing_field_names(frozen: Mapping, current: Mapping) -> list[str]:
    names: list[str] = []
    for field in _FROZEN_DIGEST_FIELDS:
        frozen_value, current_value = frozen.get(field), current[field]
        if frozen_value == current_value:
            continue
        if isinstance(frozen_value, dict) and isinstance(current_value, dict):
            names.extend(
                f"{field}[{key}]"
                for key in sorted(set(frozen_value) | set(current_value))
                if frozen_value.get(key) != current_value.get(key)
            )
        else:
            names.append(field)
    return names


def baseline_report_freeze_identity(
    defects_path: Path, arms_root: Path, reviewer_records: Sequence[runner.RunRecord], *, k: int,
) -> dict:
    """The freeze a baseline-mode report is computed under: the harness closure
    hash, every `_FROZEN_DIGEST_FIELDS` entry, K, and the campaign environment
    read from `reviewer_records`. A later arm compares it with conditions.json
    (`load_baseline_gate_verdicts`). An empty record set records no
    environment, so that comparison cannot succeed."""
    frozen_fields = compute_frozen_fields(defects_path, arms_root)
    environment = None
    if reviewer_records:
        environment = {
            "cli_version": reviewer_records[0].cli_version,
            "ambient_config_commit": reviewer_records[0].ambient_config_commit,
        }
    return {
        "harness_closure_hash": frozen_fields["harness_closure_hash"],
        **{field: frozen_fields[field] for field in _FROZEN_DIGEST_FIELDS},
        "k": k,
        "environment": environment,
    }


def _differing_freeze_identity_fields(frozen: Mapping, identity: Mapping) -> list[str]:
    """The names of the `identity` fields that differ from the conditions.json
    record `frozen`. An environment that is not an object differs in whole."""
    names = _differing_field_names(frozen, identity)
    if identity["harness_closure_hash"] != frozen.get("harness_closure_hash"):
        names.append("harness_closure_hash")
    if identity["k"] != frozen.get("k"):
        names.append("k")
    recorded_environment, frozen_environment = identity["environment"], frozen.get("environment")
    if not isinstance(recorded_environment, dict) or not isinstance(frozen_environment, dict):
        names.append("environment")
    else:
        names.extend(
            f"environment[{key}]" for key in ("cli_version", "ambient_config_commit")
            if recorded_environment.get(key) != frozen_environment.get(key)
        )
    return names


def check_against_frozen_conditions(
    frozen: Mapping, current: Mapping, *, k: int | None = None, seed: int | None = None,
) -> None:
    """Raises HarnessInvalidatedError as "invalidated -- rerun all arms" naming
    every frozen field that differs from `current` (compute_frozen_fields'
    output). A differing `k` or campaign `seed`, when given, raises instead
    with the frozen value to pass."""
    check_manifest_matches(current["harness_closure"], frozen["harness_closure"])
    differing = _differing_field_names(frozen, current)
    if differing:
        raise HarnessInvalidatedError(f"invalidated -- rerun all arms: frozen field(s) differ: {differing}")
    if k is not None and frozen.get("k") != k:
        raise HarnessInvalidatedError(
            f"--k {k} differs from the frozen k {frozen.get('k')!r} -- pass --k {frozen.get('k')!r}"
        )
    if seed is not None and frozen.get("campaign_seed") != seed:
        raise HarnessInvalidatedError(
            f"--seed {seed} differs from the frozen campaign_seed {frozen.get('campaign_seed')!r}"
            f" -- pass --seed {frozen.get('campaign_seed')!r}"
        )


def check_environment_reading_matches_frozen(environment: runner.EnvironmentRecord, frozen: Mapping) -> None:
    """Raises HarnessInvalidatedError when the CLI version or ambient config
    commit read now differs from the frozen environment."""
    frozen_environment = frozen["environment"]
    if (
        environment.cli_version != frozen_environment["cli_version"]
        or environment.ambient_config_commit != frozen_environment["ambient_config_commit"]
    ):
        raise HarnessInvalidatedError(
            "invalidated -- rerun all arms: current environment "
            f"({environment.cli_version!r}, {environment.ambient_config_commit!r}) differs from the frozen one "
            f"({frozen_environment['cli_version']!r}, {frozen_environment['ambient_config_commit']!r})"
        )


def check_record_defect_ids_match(records: Sequence[runner.RunRecord], frozen_defect_ids: Sequence[str]) -> None:
    """Raises HarnessInvalidatedError when the records name a defect that is
    not frozen, or a frozen defect has no record."""
    recorded, frozen = {record.defect_id for record in records}, set(frozen_defect_ids)
    unfrozen, unrecorded = sorted(recorded - frozen), sorted(frozen - recorded)
    if unfrozen or unrecorded:
        raise HarnessInvalidatedError(
            "invalidated -- rerun all arms: reviewer records and frozen defects disagree "
            f"(records name unfrozen defects: {unfrozen}; frozen defects with no records: {unrecorded})"
        )


def check_records_name_confirmed_defects(
    records: Sequence[runner.RunRecord], confirmed_defect_ids: Sequence[str], *, record_kind: str,
) -> None:
    """Raises HarnessInvalidatedError when `records` name a defect that is not
    in the confirmed set. `record_kind` names the record file in the message."""
    unconfirmed = sorted({record.defect_id for record in records} - set(confirmed_defect_ids))
    if unconfirmed:
        raise HarnessInvalidatedError(
            f"invalidated -- {record_kind} records name defects absent from defects.json: {unconfirmed}"
        )


def check_freeze_preconditions(
    *, current_manifest_hash: str, last_smoke_manifest_hash: str, k_to_freeze: int, smoke_full_k: int,
    provenance_failures: Sequence[tuple[str, str]], review_round_defects_without_excerpt: Sequence[str],
) -> None:
    """Raises HarnessInvalidatedError naming the failing precondition.
    `review_round_defects_without_excerpt` holds the ID of each
    `source: review-round` record with no non-empty `.local/` excerpt: a
    shortlist file alone does not supply one, and an SZZ shortlist carries
    none."""
    if current_manifest_hash != last_smoke_manifest_hash:
        raise HarnessInvalidatedError(
            "freeze: current harness closure manifest does not match the last passing smoke campaign's"
        )
    if k_to_freeze != smoke_full_k:
        raise HarnessInvalidatedError(
            f"freeze: K being frozen ({k_to_freeze}) does not match the smoke campaign's full-K fixture ({smoke_full_k})"
        )
    if review_round_defects_without_excerpt:
        names = ", ".join(review_round_defects_without_excerpt)
        raise HarnessInvalidatedError(
            f"freeze: .local/ holds no excerpt for review-round record(s) {names} -- provenance cannot be checked"
        )
    if provenance_failures:
        names = ", ".join(f"{defect_id} ({reason})" for defect_id, reason in provenance_failures)
        raise HarnessInvalidatedError(f"freeze: defects.json record(s) failed provenance check: {names}")


def check_campaign_environment_consistency(records: Sequence[runner.RunRecord]) -> None:
    """Raises HarnessInvalidatedError naming each environment and its
    block(s) when a campaign's own records carry more than one (cli_version,
    ambient_config_commit) pair (evals/README.md's "Frozen conditions and
    invalidation" section, "Within one campaign, across its blocks")."""
    blocks_by_environment: dict[tuple[str, str], set[str]] = defaultdict(set)
    for record in records:
        blocks_by_environment[(record.cli_version, record.ambient_config_commit)].add(record.defect_id)
    if len(blocks_by_environment) <= 1:
        return
    detail = "; ".join(
        f"{cli_version!r}@{config_commit!r}: {sorted(defect_ids)}"
        for (cli_version, config_commit), defect_ids in sorted(blocks_by_environment.items())
    )
    raise HarnessInvalidatedError(f"campaign carries more than one environment: {detail}")


def check_environment_matches_baseline(
    records: Sequence[runner.RunRecord], *, baseline_cli_version: str, baseline_ambient_config_commit: str,
) -> None:
    """Raises HarnessInvalidatedError as "invalidated -- rerun all arms" when
    a later arm's own campaign environment differs from the baseline's, even
    when every hash still matches (evals/README.md's "Frozen conditions and
    invalidation" section, "Between campaigns")."""
    for record in records:
        if record.cli_version != baseline_cli_version or record.ambient_config_commit != baseline_ambient_config_commit:
            raise HarnessInvalidatedError(
                "invalidated -- rerun all arms: campaign environment "
                f"({record.cli_version!r}, {record.ambient_config_commit!r}) differs from baseline "
                f"({baseline_cli_version!r}, {baseline_ambient_config_commit!r})"
            )
