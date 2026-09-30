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


def arm_recall(counts_by_defect: Mapping[str, DefectRecallCounts], defect_ids: Sequence[str], arm: str) -> float:
    """An arm's recall is the mean of its per-defect detection rates across
    defect_ids."""
    if not defect_ids:
        return 0.0
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
) -> float:
    """VALID findings divided by all adjudicated findings, pooled across
    every defect in defect_ids."""
    total_valid = sum(counts_by_defect[d].valid_by_arm.get(arm, 0) for d in defect_ids if d in counts_by_defect)
    total_all = sum(counts_by_defect[d].total_by_arm.get(arm, 0) for d in defect_ids if d in counts_by_defect)
    return total_valid / total_all if total_all else 0.0


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


def bootstrap_interval(
    defect_ids: Sequence[str], statistic_fn: Callable[[Sequence[str]], float], *,
    resamples: int = BOOTSTRAP_RESAMPLES, seed: int = BOOTSTRAP_SEED, confidence: float = 1 - 2 * ALPHA_ONE_SIDED,
) -> tuple[float, float]:
    """A paired cluster bootstrap over defects: each of `resamples` draws
    len(defect_ids) defect IDs with replacement,
    and statistic_fn computes the wanted statistic over that resampled
    defect list -- carrying both arms' data for each resampled defect
    together, since statistic_fn's own callers (e.g. arm_recall) look both
    arms up from the same resampled ID. Returns the percentile interval at
    `confidence` (0.95 by default -> the 2.5/97.5 percentiles)."""
    if not defect_ids:
        raise ValueError("bootstrap_interval: no defects to resample")
    rng = random.Random(seed)
    n = len(defect_ids)
    stats = sorted(statistic_fn([defect_ids[rng.randrange(n)] for _ in range(n)]) for _ in range(resamples))
    lower_q = (1 - confidence) / 2
    return _percentile(stats, lower_q), _percentile(stats, 1 - lower_q)


# --- Verdicts ----------------------------------------------------------------

SENSITIVITY_SENSITIVE = "sensitive"
SENSITIVITY_NOT_SENSITIVE = "not-sensitive"

NONINFERIORITY_PASS = "pass"
NONINFERIORITY_FAIL = "fail"

CERTIFICATION_CERTIFIED = "certified"
CERTIFICATION_NOT_CERTIFIED = "not-certified"
CERTIFICATION_INCONCLUSIVE = "inconclusive"


def baseline_sensitivity_verdict(
    recall_counts_by_defect: Mapping[str, DefectRecallCounts], kept_defect_ids: Sequence[str],
    arm_1: str, arm_2: str, *, delta: float = DELTA, resamples: int = BOOTSTRAP_RESAMPLES, seed: int = BOOTSTRAP_SEED,
) -> tuple[str, tuple[float, float]]:
    """ICH E10's assay sensitivity: sensitive when the lower limit of the
    two-sided 95% interval for recall_1 - recall_2 exceeds delta. Never
    grounds to revise the defect set."""

    def statistic(resample_ids: Sequence[str]) -> float:
        return arm_recall(recall_counts_by_defect, resample_ids, arm_1) - arm_recall(
            recall_counts_by_defect, resample_ids, arm_2
        )

    lower, upper = bootstrap_interval(kept_defect_ids, statistic, resamples=resamples, seed=seed)
    verdict = SENSITIVITY_SENSITIVE if lower > delta else SENSITIVITY_NOT_SENSITIVE
    return verdict, (lower, upper)


def recall_noninferiority_verdict(
    recall_counts_by_defect: Mapping[str, DefectRecallCounts], kept_defect_ids: Sequence[str],
    arm_baseline: str, arm_x: str, *, delta: float = DELTA, resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[str, tuple[float, float]]:
    """Arm X passes when the lower limit of the two-sided 95% interval for
    recall_X - recall_1 exceeds -delta."""

    def statistic(resample_ids: Sequence[str]) -> float:
        return arm_recall(recall_counts_by_defect, resample_ids, arm_x) - arm_recall(
            recall_counts_by_defect, resample_ids, arm_baseline
        )

    lower, upper = bootstrap_interval(kept_defect_ids, statistic, resamples=resamples, seed=seed)
    verdict = NONINFERIORITY_PASS if lower > -delta else NONINFERIORITY_FAIL
    return verdict, (lower, upper)


def precision_noninferiority_verdict(
    precision_counts_by_defect: Mapping[str, DefectPrecisionCounts], kept_defect_ids: Sequence[str],
    arm_baseline: str, arm_x: str, *, delta: float = DELTA, resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[str, tuple[float, float]]:
    """The same rule as recall_noninferiority_verdict, on pooled precision."""

    def statistic(resample_ids: Sequence[str]) -> float:
        return pooled_precision(precision_counts_by_defect, resample_ids, arm_x) - pooled_precision(
            precision_counts_by_defect, resample_ids, arm_baseline
        )

    lower, upper = bootstrap_interval(kept_defect_ids, statistic, resamples=resamples, seed=seed)
    verdict = NONINFERIORITY_PASS if lower > -delta else NONINFERIORITY_FAIL
    return verdict, (lower, upper)


def certify_later_arm(recall_verdict: str, precision_verdict: str, *, meets_n_min: bool) -> str:
    """A later arm is certified only when both the recall gate and the
    precision gate pass. Because both must pass, each gate keeps one-sided
    alpha = ALPHA_ONE_SIDED with no multiplicity adjustment. Passing both
    gates below N_min is inconclusive, not certified; a failing gate stays
    not-certified at any N."""
    if recall_verdict != NONINFERIORITY_PASS or precision_verdict != NONINFERIORITY_PASS:
        return CERTIFICATION_NOT_CERTIFIED
    return CERTIFICATION_CERTIFIED if meets_n_min else CERTIFICATION_INCONCLUSIVE


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
) -> dict[str, float]:
    """Recall split at the median fix date of the kept defects -- an
    observable proxy for memorization exposure, never gating."""
    dated = sorted(kept_defect_ids, key=lambda defect_id: datetime.fromisoformat(fix_dates_by_defect[defect_id]))
    midpoint = len(dated) // 2
    return {
        "earlier_half": arm_recall(recall_counts_by_defect, dated[:midpoint], arm),
        "later_half": arm_recall(recall_counts_by_defect, dated[midpoint:], arm),
    }


def recall_diff_over_read_cap_stratum(
    recall_counts_by_defect: Mapping[str, DefectRecallCounts], kept_defect_ids: Sequence[str],
    over_cap_defect_ids: Sequence[str], arm_baseline: str, arm_x: str,
) -> float:
    """recall_X - recall_baseline, restricted to kept defects flagged
    over_read_cap -- files over one Read call, where the function-context rule changes
    behavior the most."""
    stratum = [defect_id for defect_id in kept_defect_ids if defect_id in set(over_cap_defect_ids)]
    return arm_recall(recall_counts_by_defect, stratum, arm_x) - arm_recall(recall_counts_by_defect, stratum, arm_baseline)


def observed_sigma_d(
    recall_counts_by_defect: Mapping[str, DefectRecallCounts], kept_defect_ids: Sequence[str],
    arm_baseline: str, arm_x: str,
) -> float:
    """The observed per-defect-difference standard deviation -- reported for
    the record, never used to revise N_min or delta."""
    diffs = [
        recall_counts_by_defect[defect_id].detection_rate(arm_x)
        - recall_counts_by_defect[defect_id].detection_rate(arm_baseline)
        for defect_id in kept_defect_ids
    ]
    return stdev(diffs) if len(diffs) > 1 else 0.0


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


def compute_frozen_fields(defects_path: Path, arms_root: Path) -> dict:
    """Every conditions.json field derived from files on disk. `freeze`
    records these; `analyze`, `run`, and `judge` recompute them and compare
    (evals/README.md's "Frozen conditions and invalidation" section). The
    harness closure is computed here too, so all of them hash the same set."""
    try:
        defects_json_bytes = defects_path.read_bytes()
    except OSError as exc:
        raise HarnessInvalidatedError(f"defects file {defects_path} is unreadable: {exc}") from exc
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
        "defects_json_hash": hashlib.sha256(defects_json_bytes).hexdigest(),
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
