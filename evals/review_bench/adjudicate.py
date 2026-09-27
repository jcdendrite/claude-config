"""Judge-input construction, `.bench/` path normalization, blind ordering,
tolerant answer parsing, judge-run execution, and the human spot-check for
A-bench.

See .claude/plans/measure-review-quality.md's Approach > "Runs and
adjudication" for the full design this module follows.
`evals/review_bench/judges/bench-judge-recall.md` and
`bench-judge-precision.md` hold each judge's own prompt and rubric; this
module builds their per-defect input files, launches their runs, and parses
their answers mechanically.

Reuses runner.py's generic per-run primitives -- RunStore, RunRecord,
build_dispatcher_prompt, build_dispatch_command, evaluate_run_validity,
read_environment_record, and session-store lookup by session ID -- rather
than runner.execute_run/run_one_with_retry, which are hardwired to the
reviewer's own REVIEW_PROMPT_TEMPLATE. runner.py's own module docstring
scopes only its per-run validity checks and RunRecord schema as generic
across reviewer and judge runs, not its review-specific orchestration, so
this module writes its own judge-run orchestration around those primitives
instead of touching runner.py.
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import time
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from review_bench import fixture_repo, runner
from review_bench.defects import ConfirmedDefect

JUDGES_DIR = Path(__file__).resolve().parent / "judges"
RECALL_JUDGE_AGENT_FILE = JUDGES_DIR / "bench-judge-recall.md"
PRECISION_JUDGE_AGENT_FILE = JUDGES_DIR / "bench-judge-precision.md"
RECALL_JUDGE_AGENT_NAME = "bench-judge-recall"
PRECISION_JUDGE_AGENT_NAME = "bench-judge-precision"
RECALL_JUDGE_TOOLS: frozenset[str] = frozenset({"Read"})
PRECISION_JUDGE_TOOLS: frozenset[str] = frozenset({"Read", "Grep", "Glob"})

RECALL_DATA_FILE_NAME = "judge-recall.md"
PRECISION_DATA_FILE_NAME = "judge-precision.md"

# fixture_repo.py's own ".bench" directory name, duplicated here rather than
# importing its private _BENCH_DIR_NAME -- the small-duplicated-value
# exception runner.py's/fixture_repo.py's own _READ_SCOPE_CHARS_PER_TOKEN
# already uses across this package.
_BENCH_DIR_NAME = ".bench"

# The directory-creation half of a write-ahead record predates any run
# launching against it (Approach > "Cleanup"), so no session ID exists yet
# -- duplicated from runner.py's own private _NO_SESSION_ID_YET for the same
# small-duplicated-value reason as _BENCH_DIR_NAME above.
_NO_SESSION_ID_YET = ""

JUDGE_ARM_RECALL = "judge-recall"
JUDGE_ARM_PRECISION = "judge-precision"

# Local git call, no network I/O -- mirrors defects.py's/fixture_repo.py's
# own _LOCAL_GIT_TIMEOUT_S rationale (guards a hung local git blocking a
# judge-input build with no exit).
_LOCAL_GIT_TIMEOUT_S = 10.0


# --- .bench/ path normalization (Approach > "Normalization") -----------------

# Matches a `.bench/<path>` artifact reference so it can be replaced with one
# neutral token before either judge or the spot-check sheet sees it -- arm
# 2's read clause names `.bench/change-function-context.diff` by name, so an
# unredacted citation would otherwise hint at its arm. Excludes ':', so a
# trailing line reference (e.g. ":42") survives normalization unchanged, an
# accepted residual (Approach > "Normalization").
_BENCH_PATH_RE = re.compile(r"\.bench/[\w.\-/]+")

# A finding may instead cite a bench artifact's bare filename with no
# `.bench/` prefix (e.g. "per change-function-context.diff, lines 10-20"),
# which would otherwise leave an arm-correlated token unblinded.
_BENCH_ARTIFACT_BASENAMES = (
    "change-function-context.diff",
    "change.diff",
    "changed-files.tsv",
    RECALL_DATA_FILE_NAME,
    PRECISION_DATA_FILE_NAME,
)
_BENCH_BASENAME_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(name) for name in _BENCH_ARTIFACT_BASENAMES) + r")\b"
)
BENCH_PATH_TOKEN = "[bench file]"


def normalize_bench_paths(text: str) -> str:
    text = _BENCH_PATH_RE.sub(BENCH_PATH_TOKEN, text)
    return _BENCH_BASENAME_RE.sub(BENCH_PATH_TOKEN, text)


# --- Blinding: arm-free seeded ordering (Approach > "Blinding") --------------


def order_by_opaque_id(opaque_ids: Iterable[str], *, seed: int) -> tuple[str, ...]:
    """Deterministic given (the set of opaque_ids, seed) alone -- no arm
    input, so a run's position in a judge's or the spot-check's input
    carries no arm signal. Permuting which arm produced which ID therefore
    leaves this order unchanged (Verification: "Permuting the arm fields of
    a defect's runs leaves both judges' input order unchanged")."""
    ordered = sorted(set(opaque_ids))
    random.Random(f"{seed}:{'|'.join(ordered)}").shuffle(ordered)
    return tuple(ordered)


def _render_run_sections(order: Sequence[str], normalized_findings_by_id: Mapping[str, str]) -> str:
    return "\n\n".join(f"### Run {opaque_id}\n\n{normalized_findings_by_id[opaque_id]}" for opaque_id in order)


@dataclass(frozen=True)
class JudgeInput:
    """One judge run's own data-file text and the run order it presents."""

    text: str
    order: tuple[str, ...]


def _completed_findings_by_id(records: Sequence[runner.RunRecord]) -> dict[str, str]:
    """Every STATUS_OK run's own findings text, `.bench/`-normalized -- a
    missing run has nothing for a judge to label (Approach > Analysis >
    "Recall": "A missing run counts in neither the numerator nor the
    denominator"), so it is never included in a judge's input at all."""
    return {
        record.opaque_run_id: normalize_bench_paths(record.findings_text or "")
        for record in records
        if record.status == runner.STATUS_OK
    }


def _git_show(commit: str, *, repo_dir: Path) -> str:
    result = subprocess.run(
        ["git", "show", commit], cwd=repo_dir, capture_output=True, text=True,
        timeout=_LOCAL_GIT_TIMEOUT_S, check=True,
    )
    return result.stdout


def changed_relpaths_for(source_repo: Path, defect: ConfirmedDefect) -> tuple[str, ...]:
    """The defect's own changed files, for the judge run's live-checkout-leak
    check -- the same relpaths fixture_repo.build_defect_fixture would
    compute from the fixture tree, computed here directly against
    source_repo since a judge run may hold no fixture tree at all."""
    result = subprocess.run(
        ["git", "diff", "--name-only", defect.base_commit, defect.head_commit], cwd=source_repo,
        capture_output=True, text=True, timeout=_LOCAL_GIT_TIMEOUT_S, check=True,
    )
    return tuple(line for line in result.stdout.splitlines() if line)


def build_recall_judge_input(
    defect: ConfirmedDefect, records: Sequence[runner.RunRecord], *, source_repo: Path, seed: int,
) -> JudgeInput:
    """`.bench/judge-recall.md`'s own content (Approach > Runs and
    adjudication > "Recall judge"): the confirmed description, the defect's
    lines (the introducing commit's own diff -- Source 1 maps head_commit to
    the introducing commit itself, Approach > Defect set), the fix diff,
    then every completed run's normalized findings under its opaque ID, in
    blind order."""
    findings_by_id = _completed_findings_by_id(records)
    order = order_by_opaque_id(findings_by_id, seed=seed)
    text = (
        "## Confirmed defect description\n\n"
        f"{defect.description}\n\n"
        "## The defect's lines\n\n"
        f"```\n{_git_show(defect.head_commit, repo_dir=source_repo)}\n```\n\n"
        "## Fix diff\n\n"
        f"```\n{_git_show(defect.fix_commit, repo_dir=source_repo)}\n```\n\n"
        "## Runs to label\n\n"
        f"{_render_run_sections(order, findings_by_id)}\n"
    )
    return JudgeInput(text=text, order=order)


def build_precision_judge_input(records: Sequence[runner.RunRecord], *, seed: int) -> JudgeInput:
    """`.bench/judge-precision.md`'s own content (Approach > Runs and
    adjudication > "Precision judge"): every completed run's normalized
    findings under its opaque ID, in blind order -- no description or diff,
    since the precision judge inspects the real code through its own
    Read/Grep/Glob access instead."""
    findings_by_id = _completed_findings_by_id(records)
    order = order_by_opaque_id(findings_by_id, seed=seed)
    text = "## Runs to label\n\n" + _render_run_sections(order, findings_by_id) + "\n"
    return JudgeInput(text=text, order=order)


def install_recall_judge_fixture(
    dest_dir: Path, defect: ConfirmedDefect, records: Sequence[runner.RunRecord], *, source_repo: Path, seed: int,
) -> JudgeInput:
    """Builds the recall judge's own working directory: no fixture tree, just
    the judge agent file and `.bench/judge-recall.md` (Approach > "Recall
    judge")."""
    fixture_repo.build_recall_judge_dir(dest_dir)
    judge_input = build_recall_judge_input(defect, records, source_repo=source_repo, seed=seed)
    bench_dir = dest_dir / _BENCH_DIR_NAME
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / RECALL_DATA_FILE_NAME).write_text(judge_input.text)
    agents_dir = dest_dir / ".claude" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(RECALL_JUDGE_AGENT_FILE, agents_dir / RECALL_JUDGE_AGENT_FILE.name)
    return judge_input


def install_precision_judge_fixture(
    dest_dir: Path, defect: ConfirmedDefect, records: Sequence[runner.RunRecord], *, source_repo: Path, seed: int,
) -> JudgeInput:
    """Builds the arm-neutral precision-judge fixture: the real two-commit
    tree, with no `bench-<lens>.md` installed (Approach > "Precision
    judge")."""
    fixture_repo.build_precision_judge_fixture(source_repo, defect, dest_dir)
    judge_input = build_precision_judge_input(records, seed=seed)
    bench_dir = dest_dir / _BENCH_DIR_NAME
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / PRECISION_DATA_FILE_NAME).write_text(judge_input.text)
    agents_dir = dest_dir / ".claude" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(PRECISION_JUDGE_AGENT_FILE, agents_dir / PRECISION_JUDGE_AGENT_FILE.name)
    return judge_input


# --- Answer parsing (Approach > "Recall judge" / "Precision judge") ----------

# Tolerant of markdown emphasis/code-span decoration around a label or ID,
# following run_skill_evals.parse_disposition_answer's own strip-then-match
# pattern rather than requiring byte-exact judge output.
_MARKDOWN_DECORATION_RE = re.compile(r"[*`]")
_LABEL_SEPARATOR = r"[:\-–—]+"  # ':', '-', '--', en dash, em dash
_QUOTE_RE = re.compile(r"[\"'“”‘’]([^\"'“”‘’]+)[\"'“”‘’]")

_RECALL_LABEL_LINE_RE = re.compile(
    rf"(?im)^\s*(?P<id>\S+?)\s*{_LABEL_SEPARATOR}\s*(?P<label>NOT[ _]FOUND|FOUND)\b(?P<rest>.*)$"
)


@dataclass(frozen=True)
class RecallLabel:
    opaque_run_id: str
    label: str  # "FOUND" or "NOT_FOUND"
    quoted_opening: str | None


def parse_recall_answer(
    raw_text: str, *, expected_ids: Sequence[str], normalized_findings_by_id: Mapping[str, str],
) -> dict[str, RecallLabel] | None:
    """None on any invalid condition (Approach > "Recall judge"): a missing
    ID, a duplicated ID, an ID not in expected_ids, a label other than FOUND
    or NOT_FOUND, or a FOUND whose quoted opening does not occur verbatim in
    that ID's own normalized findings."""
    text = _MARKDOWN_DECORATION_RE.sub("", raw_text)
    matches: dict[str, list[RecallLabel]] = defaultdict(list)
    for m in _RECALL_LABEL_LINE_RE.finditer(text):
        run_id = m.group("id").strip("\"'")
        label = m.group("label").upper().replace(" ", "_")
        quote_match = _QUOTE_RE.search(m.group("rest"))
        matches[run_id].append(
            RecallLabel(
                opaque_run_id=run_id, label=label,
                quoted_opening=quote_match.group(1) if quote_match else None,
            )
        )

    if set(matches) - set(expected_ids):
        return None  # names an ID not in the input

    result: dict[str, RecallLabel] = {}
    for expected_id in expected_ids:
        entries = matches.get(expected_id, [])
        if len(entries) != 1:
            return None  # missing, or labeled more than once
        label = entries[0]
        if label.label == "FOUND":
            findings = normalized_findings_by_id.get(expected_id, "")
            if not label.quoted_opening or label.quoted_opening not in findings:
                return None
        result[expected_id] = label
    return result


_RUN_HEADER_RE = re.compile(r"(?im)^\s*#{0,3}\s*Run[:\s]+(?P<id>\S+?)\s*:?\s*$")
_PRECISION_FINDING_RE = re.compile(r"(?im)^\s*(?:[-*\d.)]+\s*)?(?P<label>INVALID|VALID)\b(?P<rest>.*)$")


@dataclass(frozen=True)
class PrecisionFinding:
    label: str  # "VALID" or "INVALID"
    quoted_opening: str


def _split_precision_sections(raw_text: str, expected_ids: Sequence[str]) -> dict[str, str] | None:
    """One text span per run header, keyed by run ID. None when a header
    names an ID not in expected_ids, repeats one, or an expected ID has no
    header at all."""
    headers = list(_RUN_HEADER_RE.finditer(raw_text))
    if not headers:
        return None
    sections: dict[str, str] = {}
    for index, header in enumerate(headers):
        run_id = header.group("id").strip("\"'*_:")
        if run_id in sections:
            return None
        start = header.end()
        end = headers[index + 1].start() if index + 1 < len(headers) else len(raw_text)
        sections[run_id] = raw_text[start:end]
    if set(sections) != set(expected_ids):
        return None
    return sections


def parse_precision_findings(section_text: str) -> list[PrecisionFinding] | None:
    """The distinct findings one run's own section lists, in order. None
    when a recognized finding line carries no quoted opening -- a made-up
    or unparseable finding must never enter the pooled-precision denominator
    (Approach > "Precision judge")."""
    text = _MARKDOWN_DECORATION_RE.sub("", section_text)
    findings: list[PrecisionFinding] = []
    for m in _PRECISION_FINDING_RE.finditer(text):
        quote_match = _QUOTE_RE.search(m.group("rest"))
        if quote_match is None:
            return None
        findings.append(PrecisionFinding(label=m.group("label").upper(), quoted_opening=quote_match.group(1)))
    return findings


def check_precision_split(findings: Sequence[PrecisionFinding], normalized_run_text: str) -> bool:
    """True when every finding's quoted opening occurs in normalized_run_text
    in order, each one strictly after the previous (Approach > "Precision
    judge") -- a repeat of an opening the text contains only once fails
    here, since the second search starts past the first match's own end."""
    cursor = 0
    for finding in findings:
        index = normalized_run_text.find(finding.quoted_opening, cursor)
        if index == -1:
            return False
        cursor = index + len(finding.quoted_opening)
    return True


def parse_precision_answer(
    raw_text: str, *, expected_ids: Sequence[str], normalized_findings_by_id: Mapping[str, str],
) -> dict[str, list[PrecisionFinding]] | None:
    """None on any invalid condition: a missing/extra/duplicated run
    section, a malformed finding line, or a split-check failure in any
    run's own section."""
    sections = _split_precision_sections(raw_text, expected_ids)
    if sections is None:
        return None
    result: dict[str, list[PrecisionFinding]] = {}
    for run_id, section_text in sections.items():
        findings = parse_precision_findings(section_text)
        if findings is None:
            return None
        if not check_precision_split(findings, normalized_findings_by_id.get(run_id, "")):
            return None
        result[run_id] = findings
    return result


# --- Judge run execution (Approach > "Judge runs") ---------------------------

JUDGE_INNER_PROMPT_TEMPLATE = (
    "Read `.bench/{data_file_name}` in your working directory and follow "
    "the labeling instructions and output format in your own agent body."
)


def build_judge_inner_prompt(data_file_name: str) -> str:
    return JUDGE_INNER_PROMPT_TEMPLATE.format(data_file_name=data_file_name)


@dataclass(frozen=True)
class JudgeRunContext:
    """The judge-run analog of runner.RunContext -- kept separate because a
    judge's inner prompt is fixed per judge type, not rendered from a
    reviewed commit's subject the way runner.RunContext's is."""

    campaign_id: str
    defect_id: str
    judge_kind: str  # JUDGE_ARM_RECALL or JUDGE_ARM_PRECISION
    agent_name: str
    agent_declared_tools: frozenset[str]
    data_file_name: str
    fixture_dir: Path
    live_checkout_roots: tuple[Path, ...]
    changed_relpaths: tuple[str, ...]
    model_id: str
    budget_cap_usd: float
    timeout_s: int
    environment: runner.EnvironmentRecord
    expected_ids: tuple[str, ...]
    normalized_findings_by_id: Mapping[str, str]


def execute_judge_run(ctx: JudgeRunContext, *, session_id: str, launch=None) -> runner.RunRecord:
    """Launch one judge run and evaluate its validity -- mirrors
    runner.execute_run's own shape, with a fixed judge inner prompt in place
    of a rendered review prompt. Never retries; the caller owns
    retry-then-missing (Approach > "Per-run validity checks")."""
    launch = launch if launch is not None else runner.msmr._run_claude_to_completion
    inner_prompt = build_judge_inner_prompt(ctx.data_file_name)
    dispatch_prompt = runner.build_dispatcher_prompt(ctx.agent_name, inner_prompt)
    cmd = runner.build_dispatch_command(
        dispatch_prompt, model_id=ctx.model_id, session_id=session_id, budget_cap_usd=ctx.budget_cap_usd,
    )

    start = time.monotonic()
    lines, timed_out = launch(cmd, ctx.fixture_dir, ctx.timeout_s)
    wall_clock_s = time.monotonic() - start

    projects_root = runner.config_dir() / "projects"
    session_jsonl = runner.find_session_jsonl_by_id(projects_root, session_id)

    if session_jsonl is None:
        validity = runner.RunValidity(
            ok=False, failure_reason=runner.VALIDITY_FAIL_SESSION_STORE_NOT_FOUND, observed_model=None,
            observed_tools=(), out_of_session_paths=(), findings_text=None, stats=runner.ReadStats.empty(),
        )
    else:
        subagent_dir = runner.msmr.subagent_dir_for_session(session_jsonl)
        validity = runner.evaluate_run_validity(
            dispatcher_session_jsonl=session_jsonl, stream_lines=lines, timed_out=timed_out,
            expected_agent_name=ctx.agent_name, expected_inner_prompt=inner_prompt,
            expected_model_id=ctx.model_id, agent_declared_tools=ctx.agent_declared_tools,
            fixture_dir=ctx.fixture_dir, own_dirs=(ctx.fixture_dir, session_jsonl, subagent_dir),
            projects_root=projects_root, own_session_paths=(session_jsonl, subagent_dir),
            live_checkout_roots=ctx.live_checkout_roots, changed_relpaths=ctx.changed_relpaths,
        )

    status = runner.STATUS_OK if validity.ok else runner.STATUS_MISSING
    stats = validity.stats
    return runner.RunRecord(
        campaign_id=ctx.campaign_id, defect_id=ctx.defect_id, arm=ctx.judge_kind, run_index=0,
        opaque_run_id=uuid.uuid4().hex[:12], status=status, missing_reason=validity.failure_reason,
        observed_model=validity.observed_model, observed_tools=validity.observed_tools,
        out_of_session_paths=validity.out_of_session_paths, findings_text=validity.findings_text,
        wall_clock_s=wall_clock_s, read_calls=stats.read_calls, read_tokens_est=stats.read_tokens_est,
        partial_view_reads=stats.partial_view_reads, paged_followups=stats.paged_followups,
        whole_file_reads_of_changed_files=stats.whole_file_reads_of_changed_files,
        dispatch_prompt_verbatim=validity.prompt_verbatim,
        cli_version=ctx.environment.cli_version, ambient_config_commit=ctx.environment.ambient_config_commit,
    )


def _validate_judge_answer(record: runner.RunRecord, ctx: JudgeRunContext) -> runner.RunRecord:
    """Downgrades an otherwise-valid judge run to missing when its answer
    fails the judge-specific parser (Approach > "Recall judge" / "Precision
    judge": "An invalid answer from either judge ... fails the judge run
    under the retry rule, with missing_reason: invalid-answer")."""
    if record.status != runner.STATUS_OK:
        return record
    text = record.findings_text or ""
    if ctx.judge_kind == JUDGE_ARM_RECALL:
        parsed = parse_recall_answer(
            text, expected_ids=ctx.expected_ids, normalized_findings_by_id=ctx.normalized_findings_by_id,
        )
    else:
        parsed = parse_precision_answer(
            text, expected_ids=ctx.expected_ids, normalized_findings_by_id=ctx.normalized_findings_by_id,
        )
    if parsed is None:
        record.status = runner.STATUS_MISSING
        record.missing_reason = runner.MISSING_REASON_INVALID_ANSWER
    return record


@dataclass(frozen=True)
class JudgeRunAttempt:
    record: runner.RunRecord
    session_id: str


def run_judge_with_retry(
    ctx: JudgeRunContext, *, launch=None, run_store: runner.RunStore | None = None,
) -> JudgeRunAttempt:
    """Retry-then-missing for one judge run, mirroring
    runner.run_one_with_retry's own two-attempt shape (Approach > "Per-run
    validity checks": "A failed run is retried once...")."""
    attempt: JudgeRunAttempt | None = None
    for _try in range(2):
        session_id = str(uuid.uuid4())
        if run_store is not None:
            run_store.record_directory(ctx.defect_id, ctx.fixture_dir, session_id)
        record = execute_judge_run(ctx, session_id=session_id, launch=launch)
        record = _validate_judge_answer(record, ctx)
        attempt = JudgeRunAttempt(record=record, session_id=session_id)
        if record.status == runner.STATUS_OK:
            return attempt
    return attempt


def run_defect_judges(
    defect: ConfirmedDefect, records: Sequence[runner.RunRecord], *, source_repo: Path, campaign_id: str,
    seed: int, live_checkout_roots: tuple[Path, ...], run_store: runner.RunStore | None = None, launch=None,
    judge_records_path: Path | None = None, existing_recall_record: runner.RunRecord | None = None,
) -> tuple[runner.RunRecord, runner.RunRecord]:
    """Runs both judges for one defect, once each, over every arm's
    completed runs together (Approach > "Judge runs": "One judge run per
    defect"). Returns (recall_record, precision_record).

    Persists the recall record to judge_records_path as soon as it
    completes, before the precision fixture is built -- so a precision-side
    git failure (an unreachable commit) leaves the recall judge's own
    already-paid result durably recorded rather than silently discarded.
    existing_recall_record, when given, is that already-recorded result from
    a prior resumed attempt: it is returned as-is and the recall judge is
    never redispatched."""
    environment = runner.read_environment_record()
    changed_relpaths = changed_relpaths_for(source_repo, defect)
    findings_by_id = _completed_findings_by_id(records)
    projects_root = runner.config_dir() / "projects"

    if existing_recall_record is not None:
        recall_record = existing_recall_record
    else:
        recall_dir = runner.msmr._resolved_temp_project_dir(runner.FIXTURE_DIR_PREFIX)
        if run_store is not None:
            run_store.record_directory(defect.id, recall_dir, _NO_SESSION_ID_YET)
        recall_input = install_recall_judge_fixture(recall_dir, defect, records, source_repo=source_repo, seed=seed)
        recall_ctx = JudgeRunContext(
            campaign_id=campaign_id, defect_id=defect.id, judge_kind=JUDGE_ARM_RECALL,
            agent_name=RECALL_JUDGE_AGENT_NAME, agent_declared_tools=RECALL_JUDGE_TOOLS,
            data_file_name=RECALL_DATA_FILE_NAME, fixture_dir=recall_dir,
            live_checkout_roots=live_checkout_roots, changed_relpaths=changed_relpaths,
            model_id=runner.JUDGE_MODEL_ID, budget_cap_usd=runner.RECALL_JUDGE_BUDGET_CAP_USD,
            timeout_s=runner.RECALL_JUDGE_TIMEOUT_S, environment=environment,
            expected_ids=recall_input.order, normalized_findings_by_id=findings_by_id,
        )
        recall_attempt = run_judge_with_retry(recall_ctx, launch=launch, run_store=run_store)
        recall_record = recall_attempt.record

        store_dir = runner.session_store_dir_for(projects_root, recall_attempt.session_id)
        if store_dir is not None and store_dir.exists():
            shutil.rmtree(store_dir, ignore_errors=True)
        if recall_dir.exists():
            shutil.rmtree(recall_dir, ignore_errors=True)

        if judge_records_path is not None:
            runner.append_run_records(judge_records_path, (recall_record,))

    precision_dir = runner.msmr._resolved_temp_project_dir(runner.FIXTURE_DIR_PREFIX)
    if run_store is not None:
        run_store.record_directory(defect.id, precision_dir, _NO_SESSION_ID_YET)
    precision_input = install_precision_judge_fixture(
        precision_dir, defect, records, source_repo=source_repo, seed=seed,
    )
    precision_ctx = JudgeRunContext(
        campaign_id=campaign_id, defect_id=defect.id, judge_kind=JUDGE_ARM_PRECISION,
        agent_name=PRECISION_JUDGE_AGENT_NAME, agent_declared_tools=PRECISION_JUDGE_TOOLS,
        data_file_name=PRECISION_DATA_FILE_NAME, fixture_dir=precision_dir,
        live_checkout_roots=live_checkout_roots, changed_relpaths=changed_relpaths,
        model_id=runner.JUDGE_MODEL_ID, budget_cap_usd=runner.PRECISION_JUDGE_BUDGET_CAP_USD,
        timeout_s=runner.PRECISION_JUDGE_TIMEOUT_S, environment=environment,
        expected_ids=precision_input.order, normalized_findings_by_id=findings_by_id,
    )
    precision_attempt = run_judge_with_retry(precision_ctx, launch=launch, run_store=run_store)
    precision_record = precision_attempt.record

    store_dir = runner.session_store_dir_for(projects_root, precision_attempt.session_id)
    if store_dir is not None and store_dir.exists():
        shutil.rmtree(store_dir, ignore_errors=True)
    if precision_dir.exists():
        shutil.rmtree(precision_dir, ignore_errors=True)

    return recall_record, precision_record


# --- Human spot-check (Approach > "Human spot-check") ------------------------

SPOT_CHECK_RECALL_SAMPLE_SIZE = 100
SPOT_CHECK_PRECISION_SAMPLE_SIZE = 100
# The kappa floor itself (analysis.KAPPA_SUBSTANTIAL_FLOOR) is one of
# analysis.py's own design constants -- this module only computes kappa,
# it does not gate on it.

SPOT_CHECK_KIND_RECALL = "recall"
SPOT_CHECK_KIND_PRECISION = "precision"

_SPAN_OPEN = ">>>"
_SPAN_CLOSE = "<<<"


@dataclass(frozen=True)
class SpotCheckCandidate:
    """One human-spot-checkable item, before sampling. judge_label and arm
    are never written to the exported sheet (Approach > "Human spot-check":
    "exported without arm or judge label") -- kept here only so the import
    step can score the human's answer against them, and the analysis can
    report split agreement per arm, afterward."""

    item_id: str
    kind: str  # SPOT_CHECK_KIND_RECALL or SPOT_CHECK_KIND_PRECISION
    judge_label: str
    display_text: str
    arm: str


def build_recall_spot_check_candidates(
    defect_id: str, labels_by_run_id: Mapping[str, RecallLabel], arm_by_run_id: Mapping[str, str],
    normalized_findings_by_id: Mapping[str, str],
) -> list[SpotCheckCandidate]:
    return [
        SpotCheckCandidate(
            item_id=f"{defect_id}:{run_id}", kind=SPOT_CHECK_KIND_RECALL, judge_label=label.label,
            display_text=normalized_findings_by_id.get(run_id, ""), arm=arm_by_run_id.get(run_id, ""),
        )
        for run_id, label in labels_by_run_id.items()
    ]


def build_precision_spot_check_candidates(
    defect_id: str, findings_by_run_id: Mapping[str, list[PrecisionFinding]], arm_by_run_id: Mapping[str, str],
    normalized_findings_by_id: Mapping[str, str],
) -> list[SpotCheckCandidate]:
    """One candidate per finding, its display_text the whole run output with
    that finding's own span marked (Approach > "Human spot-check": "Each
    precision item shows its finding inside the whole normalized run
    output, with the judge's split marked")."""
    candidates: list[SpotCheckCandidate] = []
    for run_id, findings in findings_by_run_id.items():
        run_text = normalized_findings_by_id.get(run_id, "")
        cursor = 0
        for finding_index, finding in enumerate(findings):
            start = run_text.find(finding.quoted_opening, cursor)
            end = start + len(finding.quoted_opening)
            cursor = end
            marked_text = f"{run_text[:start]}{_SPAN_OPEN}{run_text[start:end]}{_SPAN_CLOSE}{run_text[end:]}"
            candidates.append(
                SpotCheckCandidate(
                    item_id=f"{defect_id}:{run_id}:{finding_index}", kind=SPOT_CHECK_KIND_PRECISION,
                    judge_label=finding.label, display_text=marked_text, arm=arm_by_run_id.get(run_id, ""),
                )
            )
    return candidates


def select_spot_check_sample(
    candidates: Sequence[SpotCheckCandidate], *, sample_size: int, seed: int,
) -> list[SpotCheckCandidate]:
    """A seeded sample stratified by judge_label -- all of them if the pool
    is smaller than sample_size (Approach > "Human spot-check"). Each label
    stratum gets a share of sample_size proportional to its own size in the
    pool, with largest-remainder rounding so the total sample size is
    exact."""
    if len(candidates) <= sample_size:
        return list(candidates)

    by_label: dict[str, list[SpotCheckCandidate]] = defaultdict(list)
    for candidate in candidates:
        by_label[candidate.judge_label].append(candidate)

    raw_shares = {label: sample_size * len(group) / len(candidates) for label, group in by_label.items()}
    allocation = {label: int(share) for label, share in raw_shares.items()}
    remaining = sample_size - sum(allocation.values())
    remainder_order = sorted(raw_shares, key=lambda label: raw_shares[label] - allocation[label], reverse=True)
    for label in remainder_order[:remaining]:
        allocation[label] += 1

    rng = random.Random(seed)
    sample: list[SpotCheckCandidate] = []
    for label, group in by_label.items():
        ordered = sorted(group, key=lambda c: c.item_id)
        rng.shuffle(ordered)
        sample.extend(ordered[: allocation[label]])
    return sample


def _atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` via a same-directory temp file plus
    `os.replace` (atomic on POSIX), so a crash mid-write leaves the previous
    complete file in place rather than a truncated one. Duplicated from
    review_bench.runner's own _atomic_write_text rather than imported, since
    that helper is that module's own private convention, not a public
    export."""
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp_path.write_text(text)
    os.replace(tmp_path, path)


def export_spot_check(sample: Sequence[SpotCheckCandidate], path: Path) -> None:
    """Writes the human-facing sheet: item_id, kind, display_text only --
    never judge_label or arm (Approach > "Human spot-check")."""
    payload = [{"item_id": c.item_id, "kind": c.kind, "display_text": c.display_text} for c in sample]
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, json.dumps(payload, indent=2) + "\n")


@dataclass(frozen=True)
class HumanSpotCheckLabel:
    item_id: str
    human_label: str
    split_ok: bool | None  # precision items only: whether the marked span is exactly one finding


def import_spot_check_labels(path: Path) -> list[HumanSpotCheckLabel]:
    data = json.loads(path.read_text())
    return [
        HumanSpotCheckLabel(
            item_id=item["item_id"], human_label=item["human_label"], split_ok=item.get("split_ok"),
        )
        for item in data
    ]


def cohens_kappa(labels_a: Sequence[str], labels_b: Sequence[str]) -> float:
    """Cohen's kappa (Landis & Koch 1977) between two label sequences over
    the same items, in the same order."""
    if len(labels_a) != len(labels_b):
        raise ValueError("cohens_kappa: label sequences must be the same length")
    n = len(labels_a)
    if n == 0:
        raise ValueError("cohens_kappa: no items to compare")
    categories = sorted(set(labels_a) | set(labels_b))
    observed_agreement = sum(a == b for a, b in zip(labels_a, labels_b, strict=True)) / n
    chance_agreement = sum((labels_a.count(c) / n) * (labels_b.count(c) / n) for c in categories)
    if chance_agreement >= 1.0:
        return 1.0  # both raters unanimous on one category -- no disagreement is possible to measure
    return (observed_agreement - chance_agreement) / (1 - chance_agreement)


def score_spot_check(
    candidates: Sequence[SpotCheckCandidate], human_labels: Sequence[HumanSpotCheckLabel],
) -> dict[str, float]:
    """Cohen's kappa per item kind (Approach > "Human spot-check": "Cohen's
    kappa is computed per label type"). Only items present in both the
    sampled candidates and the human's import are scored."""
    candidates_by_id = {c.item_id: c for c in candidates}
    judge_and_human_by_kind: dict[str, tuple[list[str], list[str]]] = defaultdict(lambda: ([], []))
    for human_label in human_labels:
        candidate = candidates_by_id.get(human_label.item_id)
        if candidate is None:
            continue
        judge_labels, human_labels_list = judge_and_human_by_kind[candidate.kind]
        judge_labels.append(candidate.judge_label)
        human_labels_list.append(human_label.human_label)
    return {
        kind: cohens_kappa(judge_labels, human_labels_list)
        for kind, (judge_labels, human_labels_list) in judge_and_human_by_kind.items()
    }


def split_agreement_by_arm(
    candidates: Sequence[SpotCheckCandidate], human_labels: Sequence[HumanSpotCheckLabel],
) -> dict[str, float]:
    """The fraction of precision spot-check items, per arm, where the human
    confirmed the judge's marked span was exactly one finding (Approach >
    "Human spot-check", "Split agreement"). Never gates."""
    candidates_by_id = {c.item_id: c for c in candidates}
    agree_and_total_by_arm: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for human_label in human_labels:
        if human_label.split_ok is None:
            continue
        candidate = candidates_by_id.get(human_label.item_id)
        if candidate is None or candidate.kind != SPOT_CHECK_KIND_PRECISION:
            continue
        counts = agree_and_total_by_arm[candidate.arm]
        counts[1] += 1
        if human_label.split_ok:
            counts[0] += 1
    return {arm: agree / total for arm, (agree, total) in agree_and_total_by_arm.items() if total}
