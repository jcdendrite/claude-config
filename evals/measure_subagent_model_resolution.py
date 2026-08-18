"""Measurement harness for the plan-mode subagent model resolution experiment.

See .claude/plans/plan-mode-model-resolution-experiment.md (Mechanisms M1,
M1a, M2, M3, M8) for the full design. In short: launches short headless
`claude -p` runs across an explicit (session model x permission mode x
dispatch shape) matrix, then reads each run's own `subagents/*.meta.json` +
paired `.jsonl` to report, per run: requested agent type, requested model
param, frontmatter-declared model pin, and observed `.message.model`.

LOCAL USE ONLY — never run in CI. See evals/README.md's "Why local only —
never CI" section: this uses the session's Claude subscription auth (no
ANTHROPIC_API_KEY required) and produces a single-sample measurement, not a
deterministic pass/fail gate.

Reuses from evals/run_skill_evals.py rather than re-deriving: the
subprocess.Popen(["claude", "-p", ...]) launch shape, SAMPLE_TIMEOUT_S,
config_dir(), compute_session_store_dir() (transcripts live outside the
launched project dir; shutil.rmtree(project_dir) alone does not remove
them), and detect_dispatch_in_lines() for the attempted-dispatch signal.
The meta.json/jsonl join mirrors (does not import) _index_subagent_dispatches
and _dispatch_usage_summary's observed-model walk in
claude/.claude/scripts/transcript-analysis.py — this harness only
ever has one dispatch per run to join, so the multi-root/pricing generality
those functions carry for the full corpus tool doesn't apply here.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import run_skill_evals

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO_ROOT / "claude" / ".claude" / "agents"

# subagents/*.meta.json + sibling *.jsonl live under
# <session_jsonl.parent>/<session_jsonl.stem>/<SUBAGENT_SUBDIR>/ — matches
# transcript-analysis.py's SUBAGENT_SUBDIR ("subagents").
SUBAGENT_SUBDIR = "subagents"

# --- M8: per-run spend cap -------------------------------------------------
# Derived 2026-08-13 via `.venv/bin/python3
# claude/.claude/scripts/transcript-analysis.py subagent-mix --this-repo`
# (read-only, this repo's own corpus): staff-backend-engineer read 79 runs /
# $91.06 actual $ -> ~$1.15/dispatch. Cap is deliberately far above that so it
# can never truncate a decisive run mid-flight — those cells (2/3, 4/5) repeat
# only twice, so a truncated run is not recoverable within budget (plan M8).
REPRESENTATIVE_DISPATCH_COST_USD = 1.15
BUDGET_CAP_MULTIPLIER = 10
PER_RUN_BUDGET_CAP_USD = round(REPRESENTATIVE_DISPATCH_COST_USD * BUDGET_CAP_MULTIPLIER, 2)

# Sentinel permission-mode label for "omit --permission-mode entirely" — the
# CLI's own --permission-mode choices (acceptEdits, auto, bypassPermissions,
# manual, dontAsk, plan) do not include a literal "default", so run 1's
# default-mode baseline is expressed by not passing the flag at all.
DEFAULT_PERMISSION_MODE_LABEL = "default"

DISPATCH_STAFF_BACKEND_ENGINEER = "staff-backend-engineer"
DISPATCH_EXPLORE_HAIKU = "explore-haiku"

STAFF_BACKEND_ENGINEER_AGENT_FILE = AGENTS_DIR / "staff-backend-engineer.md"

# M9: the Explore override supplied via --agents (CLI priority 2), never by
# editing the committed, stowed claude/.claude/agents/Explore.md (priority
# 4) — see plan M9 point 2. --agents provably outranks both the project (3)
# and stowed user (4) scope definitions regardless of which the ambient
# config dir happens to resolve.
EXPLORE_HAIKU_OVERRIDE_AGENT_NAME = "Explore"
EXPLORE_HAIKU_OVERRIDE_MODEL = "haiku"
EXPLORE_HAIKU_OVERRIDE_TOOLS = frozenset({"Read", "Grep", "Glob"})
EXPLORE_HAIKU_OVERRIDE = {
    EXPLORE_HAIKU_OVERRIDE_AGENT_NAME: {
        "description": (
            "A fast, read-only agent for searching and analyzing codebases — "
            "file discovery, code search, and codebase exploration without "
            "making changes."
        ),
        "prompt": (
            "You are `Explore`, a fast read-only search agent. Locate files, "
            "symbols, and patterns; report what you find. You have no "
            "`Write`/`Edit`/`Bash` access."
        ),
        "tools": sorted(EXPLORE_HAIKU_OVERRIDE_TOOLS),
        "model": EXPLORE_HAIKU_OVERRIDE_MODEL,
    }
}

_DISPATCH_AGENT_TYPE = {
    DISPATCH_STAFF_BACKEND_ENGINEER: "staff-backend-engineer",
    DISPATCH_EXPLORE_HAIKU: EXPLORE_HAIKU_OVERRIDE_AGENT_NAME,
}

# One fixed prompt template across runs 2-5 (plan M2) — only --model and
# --permission-mode vary, so an observed difference can never be confounded
# with prompt wording.
#
# Plan mode's own system prompt states its read-only restriction supersedes
# any instruction embedded in the user's own prompt — no prompt wording can
# talk the model out of declining an action-shaped dispatch request. The
# task must read as genuine plan-mode research (context-gathering toward a
# plan) or the dispatch is refused outright, independent of wording. This
# template frames the dispatch as reviewing MARKER_FILE_NAME for the plan
# about to be presented, rather than an arbitrary classification task with
# no connection to planning — matching both plan mode's own legitimacy
# criterion and staff-backend-engineer's actual declared purpose (a
# code-review specialist, not a general classifier).
MARKER_FILE_NAME = "example.py"
MARKER_FILE_CONTENT = "def add(a, b):\n    return a + b\n"

DISPATCH_PROMPT_TEMPLATE = (
    "As part of gathering context before presenting a plan, use the Task "
    'tool to dispatch exactly one "{agent_type}" subagent with this '
    'instruction: "Look at {marker_file} and report whether the code '
    'appears SOUND or has CONCERNS. Reply with only that one word." Wait '
    "for its reply and report that single word back to me verbatim. Do not "
    "look at the file or judge it yourself, and do not do any other work."
)

# Runs 6-7 (the Explore/Haiku --agents pin test) append one line diverging
# from the runs-2-5 template above: the dispatcher can spontaneously pass an
# explicit model param sourced from ambient CLAUDE.md's Explore-pinning
# instruction, which loads regardless of the harness's temp-project cwd and
# always overrides an --agents-supplied frontmatter pin — this line forbids
# that so the run measures whether the pin survives plan mode unforced.
DISPATCH_PROMPT_EXPLORE_HAIKU_SUFFIX = (
    " Do not pass an explicit model parameter on the Task call — dispatch "
    "the agent by name only."
)


def build_dispatch_prompt(dispatch: str) -> str:
    prompt = DISPATCH_PROMPT_TEMPLATE.format(
        agent_type=_DISPATCH_AGENT_TYPE[dispatch], marker_file=MARKER_FILE_NAME
    )
    if dispatch == DISPATCH_EXPLORE_HAIKU:
        prompt += DISPATCH_PROMPT_EXPLORE_HAIKU_SUFFIX
    return prompt


@dataclass(frozen=True)
class MatrixRun:
    number: int
    model: str
    permission_mode: str
    dispatch: str
    purpose: str
    repeat_of: int | None = None


# M2's seven-run matrix, encoded as data. Run 1 is the instrument self-check
# and must run first — see run1_self_check_passed(). Runs 2/3 and 4/5 are
# the two decisive cells and their repeats; runs 6/7 are M9's Explore/Haiku
# pin test and its repeat.
RUN_MATRIX: tuple[MatrixRun, ...] = (
    MatrixRun(
        1, "opus", DEFAULT_PERMISSION_MODE_LABEL, DISPATCH_STAFF_BACKEND_ENGINEER,
        "Instrument self-check, must run first. Parent Opus, pin Sonnet, no plan "
        "mode => must return Sonnet. If it returns Opus the harness is reading "
        "the parent rather than the subagent and every other run is void.",
    ),
    MatrixRun(
        2, "sonnet", "plan", DISPATCH_STAFF_BACKEND_ENGINEER,
        "Decisive A: Opus observed here means plan mode forces Opus regardless "
        "of the parent's model; Sonnet is consistent with every other reading "
        "(see the plan's 'four rival mechanisms' table).",
    ),
    MatrixRun(
        3, "sonnet", "plan", DISPATCH_STAFF_BACKEND_ENGINEER,
        "Repeat of run 2.", repeat_of=2,
    ),
    MatrixRun(
        4, "opus", "plan", DISPATCH_STAFF_BACKEND_ENGINEER,
        "Decisive B: Sonnet observed here means plan mode does not affect "
        "resolution at all; Opus means the parent's model is winning through, "
        "one way or another (see the plan's 'four rival mechanisms' table).",
    ),
    MatrixRun(
        5, "opus", "plan", DISPATCH_STAFF_BACKEND_ENGINEER,
        "Repeat of run 4.", repeat_of=4,
    ),
    MatrixRun(
        6, "opus", "plan", DISPATCH_EXPLORE_HAIKU,
        "Explore pin test — dispatches Explore against a model: haiku definition "
        "supplied via --agents instead of staff-backend-engineer.",
    ),
    MatrixRun(
        7, "opus", "plan", DISPATCH_EXPLORE_HAIKU,
        "Repeat of run 6.", repeat_of=6,
    ),
)


def build_run_command(run: MatrixRun, *, session_id: str, budget_cap_usd: float) -> list[str]:
    cmd = [
        "claude", "-p", build_dispatch_prompt(run.dispatch),
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--model", run.model,
        "--session-id", session_id,
        "--max-budget-usd", str(budget_cap_usd),
    ]
    if run.permission_mode != DEFAULT_PERMISSION_MODE_LABEL:
        cmd += ["--permission-mode", run.permission_mode]
    if run.dispatch == DISPATCH_EXPLORE_HAIKU:
        cmd += ["--agents", json.dumps(EXPLORE_HAIKU_OVERRIDE)]
    return cmd


# --- Agent frontmatter reading ----------------------------------------------
# Mirrors _agent_frontmatter_model's leading-YAML-block scoping in
# claude/.claude/scripts/transcript-analysis.py (never matches a
# "model:"/"tools:" mention inside the agent's prose body).

_AGENT_FRONTMATTER_MODEL_RE = re.compile(r"(?m)^model:\s*(\S+)\s*$")
_AGENT_FRONTMATTER_TOOLS_RE = re.compile(r"(?m)^tools:\s*(.+?)\s*$")


def _frontmatter_block(agent_file_text: str) -> str | None:
    if not agent_file_text.startswith("---"):
        return None
    end = agent_file_text.find("\n---", 3)
    if end == -1:
        return None
    return agent_file_text[3:end]


def agent_frontmatter_model(agent_file_text: str) -> str | None:
    block = _frontmatter_block(agent_file_text)
    if block is None:
        return None
    match = _AGENT_FRONTMATTER_MODEL_RE.search(block)
    return match.group(1) if match else None


def agent_frontmatter_tools(agent_file_text: str) -> frozenset[str] | None:
    block = _frontmatter_block(agent_file_text)
    if block is None:
        return None
    match = _AGENT_FRONTMATTER_TOOLS_RE.search(block)
    if not match:
        return None
    return frozenset(t.strip() for t in match.group(1).split(",") if t.strip())


def declared_model_pin_for_dispatch(dispatch: str) -> str | None:
    if dispatch == DISPATCH_EXPLORE_HAIKU:
        return EXPLORE_HAIKU_OVERRIDE_MODEL
    try:
        text = STAFF_BACKEND_ENGINEER_AGENT_FILE.read_text()
    except OSError:
        return None
    return agent_frontmatter_model(text)


def declared_tools_for_dispatch(dispatch: str) -> frozenset[str] | None:
    if dispatch == DISPATCH_EXPLORE_HAIKU:
        return EXPLORE_HAIKU_OVERRIDE_TOOLS
    try:
        text = STAFF_BACKEND_ENGINEER_AGENT_FILE.read_text()
    except OSError:
        return None
    return agent_frontmatter_tools(text)


# --- Observed-model bucketing ------------------------------------------------

MODEL_FAMILY_OPUS = "opus"
MODEL_FAMILY_SONNET = "sonnet"
MODEL_FAMILY_HAIKU = "haiku"
MODEL_FAMILY_OTHER = "other"
MODEL_FAMILY_MIXED = "mixed"


def model_family(model_id: str) -> str:
    """Bucket a raw model ID to its family name. Mirrors _fam() in
    claude/.claude/scripts/transcript_analysis/render.py."""
    lowered = model_id.lower()
    if "opus" in lowered:
        return MODEL_FAMILY_OPUS
    if "sonnet" in lowered:
        return MODEL_FAMILY_SONNET
    if "haiku" in lowered:
        return MODEL_FAMILY_HAIKU
    return MODEL_FAMILY_OTHER


# --- M3: agent-identity discriminator ---------------------------------------
#
# Detection method (a) — an explicit agent-name/system-prompt field — is not
# implemented: it requires a manual probe of a real plan-mode dispatch to
# confirm the field exists first (plan M3), which has not been run; only
# method (b), the observed-toolset heuristic below, is implemented.

AGENT_IDENTITY_REQUESTED = "requested"
AGENT_IDENTITY_SUBSTITUTED_TO_PLAN = "substituted-to-plan"
AGENT_IDENTITY_INCONCLUSIVE = "inconclusive"
AGENT_IDENTITY_UNKNOWN = "unknown"  # sidecar missing — nothing to classify

# Tools unique to the built-in Plan agent's wider read-only set (Bash, web
# tools) that a narrower requested agent wouldn't declare; a call to one of
# these outside the requested agent's own declared tools is evidence of
# substitution, but any other undeclared tool is inconclusive by this method
# alone (plan M3's "may never observe a true positive" validity threat).
PLAN_DISCRIMINATING_TOOLS = frozenset({"Bash", "WebFetch", "WebSearch"})


def classify_agent_identity(
    requested_declared_tools: frozenset[str] | None, observed_tools: frozenset[str]
) -> str:
    """M3 detection method (b): compare the observed toolset against the
    requested agent's own declared `tools:` frontmatter (or, for the M9
    --agents override, its own declared tools list).

    Returns AGENT_IDENTITY_SUBSTITUTED_TO_PLAN only when a used tool is both
    outside the declared set AND in PLAN_DISCRIMINATING_TOOLS — a narrower
    bar than "any undeclared tool", since a requested agent that already
    declares a broad toolset (e.g. staff-backend-engineer's Bash) can look
    identical to Plan on toolset alone; see PLAN_DISCRIMINATING_TOOLS above.
    """
    if requested_declared_tools is None:
        return AGENT_IDENTITY_INCONCLUSIVE
    extra = observed_tools - requested_declared_tools
    if extra & PLAN_DISCRIMINATING_TOOLS:
        return AGENT_IDENTITY_SUBSTITUTED_TO_PLAN
    return AGENT_IDENTITY_REQUESTED if not extra else AGENT_IDENTITY_INCONCLUSIVE


# --- Transcript parsing ------------------------------------------------------


@dataclass(frozen=True)
class DispatchObservation:
    tool_use_id: str
    requested_agent_type: str
    requested_model_param: str | None
    sidecar_missing: bool
    observed_model_ids: frozenset[str]
    observed_model_family: str | None  # None: no model observed (dangling or no .message.model)
    observed_tools: frozenset[str]
    agent_identity: str


@dataclass(frozen=True)
class RunResult:
    run: MatrixRun
    session_id: str
    attempted_dispatch: bool
    timed_out: bool
    declared_model_pin: str | None
    dispatches: tuple[DispatchObservation, ...]
    # True only when attempted_dispatch was True and the sidecar poll below
    # never found a meta.json — distinguishes "the sidecar-flush race timed
    # out" from "no dispatch was ever attempted" (attempted_dispatch=False)
    # and from "sidecar found, dispatches legitimately empty." Without this,
    # dispatches == () means the same thing in all three cases, and a poll
    # timeout would misreport as a genuine model-resolution negative.
    # Describes the poll's own outcome, not a guarantee about dispatches: a
    # sidecar landing between this poll's check and parse_subagent_dispatches's
    # own independent glob can leave this True alongside a non-empty
    # dispatches tuple — print_run_result's `if not dispatches` guard means
    # that case is reported as data found, not as a timeout.
    sidecar_poll_timed_out: bool = False
    # Only available in the stream's own "result" event — never persisted to
    # the on-disk transcript; None if the process was killed before emitting one.
    total_cost_usd: float | None = None


def _scan_subagent_jsonl(path: Path) -> tuple[frozenset[str], frozenset[str]]:
    """Return (distinct real model IDs, distinct observed tool_use names)
    from one subagent's own transcript.

    Mirrors _dispatch_usage_summary's observed-model walk
    (claude/.claude/scripts/transcript-analysis.py) — every assistant
    record's message.model, excluding the literal "<synthetic>" placeholder
    — and collects tool_use block names in the same pass for M3's
    discriminator. Returns two empty frozensets on any read error, matching
    that function's "dangling" convention: absence of data, not a crash.
    """
    model_ids: set[str] = set()
    tools: set[str] = set()
    try:
        with open(path) as fh:
            for raw in fh:
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "assistant":
                    continue
                msg = rec.get("message") or {}
                model = msg.get("model")
                if model and model != "<synthetic>":
                    model_ids.add(model)
                for block in msg.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        name = block.get("name")
                        if isinstance(name, str) and name:
                            tools.add(name)
    except OSError:
        return frozenset(), frozenset()
    return frozenset(model_ids), frozenset(tools)


def subagent_dir_for_session(session_jsonl: Path) -> Path:
    return session_jsonl.parent / session_jsonl.stem / SUBAGENT_SUBDIR


def parse_subagent_dispatches(
    session_jsonl: Path, *, requested_agent_declared_tools: frozenset[str] | None
) -> tuple[DispatchObservation, ...]:
    """Read every subagents/*.meta.json + paired *.jsonl under session_jsonl's
    own subagent directory and return one DispatchObservation per readable
    dispatch.

    Mirrors _index_subagent_dispatches's meta.json read
    (claude/.claude/scripts/transcript-analysis.py) — toolUseId,
    requested model, and (unlike that function, which doesn't need it since
    it joins agentType from the parent's own tool_use block) agentType read
    directly from meta.json, which carries it as its own field. A meta.json
    that is unreadable or missing a string toolUseId is skipped, matching
    that function's meta_read_errors exclusion.
    """
    subagent_dir = subagent_dir_for_session(session_jsonl)
    if not subagent_dir.is_dir():
        return ()
    observations: list[DispatchObservation] = []
    for meta_path in sorted(subagent_dir.glob("*.meta.json")):
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        tool_use_id = meta.get("toolUseId")
        if not isinstance(tool_use_id, str) or not tool_use_id:
            continue
        requested_agent_type = meta.get("agentType") if isinstance(meta.get("agentType"), str) else "unknown"
        requested_model_param = meta.get("model") if isinstance(meta.get("model"), str) else None

        agent_id = meta_path.name.removesuffix(".meta.json")
        paired_jsonl = meta_path.parent / f"{agent_id}.jsonl"
        sidecar_missing = not paired_jsonl.is_file()

        if sidecar_missing:
            model_ids: frozenset[str] = frozenset()
            observed_tools: frozenset[str] = frozenset()
        else:
            model_ids, observed_tools = _scan_subagent_jsonl(paired_jsonl)

        if len(model_ids) >= 2:
            family: str | None = MODEL_FAMILY_MIXED
        elif model_ids:
            family = model_family(next(iter(model_ids)))
        else:
            family = None

        identity = (
            AGENT_IDENTITY_UNKNOWN
            if sidecar_missing
            else classify_agent_identity(requested_agent_declared_tools, observed_tools)
        )

        observations.append(
            DispatchObservation(
                tool_use_id=tool_use_id,
                requested_agent_type=requested_agent_type,
                requested_model_param=requested_model_param,
                sidecar_missing=sidecar_missing,
                observed_model_ids=model_ids,
                observed_model_family=family,
                observed_tools=observed_tools,
                agent_identity=identity,
            )
        )
    return tuple(observations)


def run1_self_check_passed(result: RunResult) -> bool:
    """Plan M2's Verification gate: run 1 (opus parent, staff-backend-engineer
    pinned to sonnet, default mode, no per-dispatch model param) must observe
    sonnet. Any other outcome means the harness is reading the parent's
    model rather than the subagent's, voiding every other run.

    Known gap: does not distinguish a sidecar-poll timeout
    (result.sidecar_poll_timed_out) from a genuine self-check failure — both
    read as empty dispatches here and both stop the --all matrix the same
    way. Stopping is the conservative choice either way, so this is a
    messaging gap in the abort path, not a spend-safety one; a caller running
    --all who wants to tell the two apart before deciding whether to retry
    run 1 must read sidecar_poll_timed_out on the RunResult itself."""
    if not result.dispatches:
        return False
    return all(d.observed_model_family == MODEL_FAMILY_SONNET for d in result.dispatches)


def should_stop_matrix_after(run: MatrixRun, result: RunResult) -> bool:
    """The --all loop's one safety-critical decision, extracted from main() so
    it's unit-testable without a live subprocess: stop before spending budget
    on runs 2-7 if run 1's self-check failed."""
    return run.number == 1 and not run1_self_check_passed(result)


# --- M1a: ambient-environment preflight --------------------------------------


def abort_if_subagent_model_env_set() -> None:
    """CLAUDE_CODE_SUBAGENT_MODEL sits above every other step in the documented
    subagent model-resolution order (the plan's G1), so if set it would decide
    every run's observed model by itself and make the matrix's seven cells
    unable to distinguish between rival explanations — with nothing in the
    harness output to show it happened (plan M1a). Fails loudly before any
    subprocess launches, rather than surfacing only as a field in the report."""
    value = os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL")
    if value:
        sys.exit(
            f"ERROR: CLAUDE_CODE_SUBAGENT_MODEL is set ({value!r}). This overrides "
            "every model-resolution step in all seven runs, so every run would "
            "observe whatever model this variable names regardless of parent, "
            "permission mode, or dispatch — making the runs unable to tell "
            "apart the different explanations this experiment exists to "
            "distinguish. Unset it "
            "before running the matrix."
        )


def gather_environment_report() -> dict:
    """Record the ambient environment rather than replace it (plan M1a):
    claude --version, the effective CLAUDE_CONFIG_DIR, the model:
    frontmatter read for each dispatched agent, and
    CLAUDE_CODE_SUBAGENT_MODEL's set/unset state."""
    version_proc = subprocess.run(
        ["claude", "--version"], capture_output=True, text=True, check=False
    )
    return {
        "claude_version": (version_proc.stdout or version_proc.stderr).strip(),
        "config_dir": str(run_skill_evals.config_dir()),
        "claude_code_subagent_model_env": os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL"),
        "staff_backend_engineer_declared_model": declared_model_pin_for_dispatch(
            DISPATCH_STAFF_BACKEND_ENGINEER
        ),
        "explore_haiku_override_declared_model": EXPLORE_HAIKU_OVERRIDE_MODEL,
    }


# --- Subprocess execution -----------------------------------------------------


def _run_claude_to_completion(cmd: list[str], cwd: Path, timeout_s: int) -> tuple[list[bytes], bool]:
    """Run cmd to completion (or timeout_s), returning (raw stdout lines,
    timed_out).

    Unlike run_skill_evals.py's detect_dispatch_in_stream, this never
    early-terminates on the first Agent/Task tool_use: a subagent's own
    model resolution is only observable after the parent process has run
    the dispatch to completion and written its subagents/ sidecar, so the
    process must be allowed to finish (or hit the timeout) before parsing.
    """
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    proc = subprocess.Popen(
        cmd, cwd=str(cwd), env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    buf = b""
    all_lines: list[bytes] = []
    deadline = time.monotonic() + timeout_s
    timed_out = False
    try:
        while True:
            if time.monotonic() > deadline:
                timed_out = True
                break
            remaining = max(0.1, deadline - time.monotonic())
            rlist, _, _ = select.select([proc.stdout], [], [], remaining)
            if not rlist:
                continue
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                all_lines.append(line)
    finally:
        try:
            if timed_out:
                proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass
    return all_lines, timed_out


# The subagent's own subagents/*.meta.json + *.jsonl sidecar can still be
# missing from disk for a moment after the parent `claude -p` process has
# fully exited (proc.wait() returned) — an async on-disk flush that trails
# process termination, observed empirically against v2.1.228. Bounded poll,
# not a fixed sleep, so a normal-speed write doesn't cost the full timeout.
SIDECAR_POLL_TIMEOUT_S = 10.0
SIDECAR_POLL_INTERVAL_S = 0.25


def _wait_for_subagent_sidecar(session_jsonl: Path, *, timeout_s: float = SIDECAR_POLL_TIMEOUT_S) -> bool:
    """Block until at least one *.meta.json appears under the session's
    subagents/ directory, or timeout_s elapses. Returns True if found, False
    on timeout — the caller surfaces a timeout as its own outcome rather than
    letting it look identical to a genuine no-dispatch result. Only called
    when the stream already showed an attempted dispatch. Checks once more
    after the final sleep rather than re-testing the deadline first, so a
    sidecar that lands in the last poll interval still counts as found."""
    subagent_dir = subagent_dir_for_session(session_jsonl)
    deadline = time.monotonic() + timeout_s
    while True:
        if subagent_dir.is_dir() and any(subagent_dir.glob("*.meta.json")):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(SIDECAR_POLL_INTERVAL_S)


def _resolved_temp_project_dir(prefix: str) -> Path:
    """tempfile.mkdtemp() returns an unresolved path — on macOS this is
    /var/folders/..., but /var is a symlink to /private/var, and Claude Code
    hashes the resolved cwd when computing its session-store directory.
    Passing the unresolved path to compute_session_store_dir() elsewhere
    hashes a different string than Claude Code did and silently finds
    nothing, so every caller must go through this rather than mkdtemp()
    directly."""
    return Path(tempfile.mkdtemp(prefix=prefix)).resolve()


def _compute_sidecar_poll_timed_out(attempted: bool, sidecar_found: bool | None) -> bool:
    """sidecar_found is None when the poll was never run (no attempted
    dispatch — the caller must skip the 10s poll call itself in that case,
    this function doesn't do that skipping). Only an explicit False — an
    attempted dispatch whose poll genuinely ran out the clock — counts as a
    timeout; None never does, regardless of attempted's value."""
    return attempted and sidecar_found is False


def _extract_total_cost_usd(lines: list[bytes]) -> float | None:
    """Return the last stream "result" event's total_cost_usd (scanned in
    reverse — if the stream ever carries more than one, the last one in
    stream order wins), or None if no result event was emitted or the field
    is missing/non-numeric."""
    for raw in reversed(lines):
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("type") == "result":
            cost = rec.get("total_cost_usd")
            return cost if isinstance(cost, int | float) else None
    return None


def execute_matrix_cell(run: MatrixRun, *, budget_cap_usd: float, timeout_s: int) -> RunResult:
    """Launch one matrix cell's `claude -p` session, parse its result, and
    clean up both the throwaway project dir and its session store (mirrors
    run_skill_evals.py's own cleanup — shutil.rmtree(project_dir) alone does
    not remove transcripts, which land outside it at
    compute_session_store_dir())."""
    session_id = str(uuid.uuid4())
    tmp_project = _resolved_temp_project_dir("subagent-model-resolution-")
    try:
        # A real file for the dispatched agent to look at — the prompt frames
        # the dispatch as plan-mode research grounded in the repo, so there
        # must be a repo (however trivial) for that framing to be genuine.
        (tmp_project / MARKER_FILE_NAME).write_text(MARKER_FILE_CONTENT)
        cmd = build_run_command(run, session_id=session_id, budget_cap_usd=budget_cap_usd)
        lines, timed_out = _run_claude_to_completion(cmd, cwd=tmp_project, timeout_s=timeout_s)
        attempted = run_skill_evals.detect_dispatch_in_lines(lines)
        total_cost_usd = _extract_total_cost_usd(lines)

        session_jsonl = run_skill_evals.compute_session_store_dir(tmp_project) / f"{session_id}.jsonl"
        sidecar_found = _wait_for_subagent_sidecar(session_jsonl) if attempted else None
        sidecar_poll_timed_out = _compute_sidecar_poll_timed_out(attempted, sidecar_found)
        declared_tools = declared_tools_for_dispatch(run.dispatch)
        dispatches = parse_subagent_dispatches(session_jsonl, requested_agent_declared_tools=declared_tools)

        return RunResult(
            run=run,
            session_id=session_id,
            attempted_dispatch=attempted,
            timed_out=timed_out,
            declared_model_pin=declared_model_pin_for_dispatch(run.dispatch),
            dispatches=dispatches,
            sidecar_poll_timed_out=sidecar_poll_timed_out,
            total_cost_usd=total_cost_usd,
        )
    finally:
        shutil.rmtree(tmp_project, ignore_errors=True)
        session_store_dir = run_skill_evals.compute_session_store_dir(tmp_project)
        if session_store_dir.exists():
            shutil.rmtree(session_store_dir, ignore_errors=True)


# --- Reporting -----------------------------------------------------------------


def observation_to_dict(obs: DispatchObservation) -> dict:
    return {
        "tool_use_id": obs.tool_use_id,
        "requested_agent_type": obs.requested_agent_type,
        "requested_model_param": obs.requested_model_param,
        "sidecar_missing": obs.sidecar_missing,
        "observed_model_ids": sorted(obs.observed_model_ids),
        "observed_model_family": obs.observed_model_family,
        "observed_tools": sorted(obs.observed_tools),
        "agent_identity": obs.agent_identity,
    }


def result_to_dict(result: RunResult) -> dict:
    return {
        "run_number": result.run.number,
        "model": result.run.model,
        "permission_mode": result.run.permission_mode,
        "dispatch": result.run.dispatch,
        "repeat_of": result.run.repeat_of,
        "session_id": result.session_id,
        "attempted_dispatch": result.attempted_dispatch,
        "timed_out": result.timed_out,
        "declared_model_pin": result.declared_model_pin,
        "dispatches": [observation_to_dict(d) for d in result.dispatches],
        "sidecar_poll_timed_out": result.sidecar_poll_timed_out,
        "total_cost_usd": result.total_cost_usd,
    }


def format_aggregate_spend_line(results: list[RunResult]) -> str:
    """One-line total spend across a batch of runs, e.g. from --all — missing
    per-run costs (a process killed on timeout before its result event) are
    excluded from the sum and called out by count rather than silently
    treated as zero-cost."""
    priced = [r.total_cost_usd for r in results if r.total_cost_usd is not None]
    missing = len(results) - len(priced)
    missing_note = f" ({missing} run(s) missing cost data — no result event)" if missing else ""
    return f"--- total spend across {len(results)} run(s): ${sum(priced):.4f}{missing_note}"


def print_matrix() -> None:
    print(f"{'#':>2}  {'model':<8} {'perm-mode':<10} {'dispatch':<24} purpose")
    for run in RUN_MATRIX:
        print(f"{run.number:>2}  {run.model:<8} {run.permission_mode:<10} {run.dispatch:<24} {run.purpose}")


def print_environment_report(report: dict) -> None:
    print("--- environment ---")
    for key, value in report.items():
        print(f"  {key}: {value}")


def print_run_result(result: RunResult) -> None:
    print(
        f"--- run {result.run.number} ({result.run.model} / {result.run.permission_mode} / "
        f"{result.run.dispatch}) ---"
    )
    print(f"  session_id: {result.session_id}")
    print(f"  attempted_dispatch: {result.attempted_dispatch}  timed_out: {result.timed_out}")
    cost_label = (
        f"${result.total_cost_usd:.4f}"
        if result.total_cost_usd is not None
        else "unknown (no result event — process likely killed on timeout)"
    )
    print(f"  total_cost_usd: {cost_label}")
    print(f"  declared_model_pin: {result.declared_model_pin}")
    if not result.dispatches:
        if result.sidecar_poll_timed_out:
            print(
                "  dispatches: NONE — sidecar poll timed out (a dispatch was attempted but its "
                "subagents/ sidecar never appeared on disk). This is a dropped trial, not a "
                "genuine no-dispatch result — re-run this cell."
            )
        else:
            print("  dispatches: none observed")
        return
    for obs in result.dispatches:
        print(
            f"  dispatch {obs.tool_use_id}: requested={obs.requested_agent_type} "
            f"({obs.requested_model_param or '(none)'})  "
            f"observed_family={obs.observed_model_family}  "
            f"identity={obs.agent_identity}  sidecar_missing={obs.sidecar_missing}"
        )


# --- CLI -----------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Measure subagent model resolution across the plan-mode experiment "
            "matrix (local only, never CI)."
        )
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--list", action="store_true", help="Print the run matrix and exit without launching anything"
    )
    group.add_argument(
        "--run", type=int, metavar="N", help="Run a single matrix cell by its run number (1-7)"
    )
    group.add_argument(
        "--all", action="store_true",
        help="Run the full seven-run matrix in order, stopping if run 1's self-check fails",
    )
    parser.add_argument(
        "--budget-cap-usd", type=float, default=PER_RUN_BUDGET_CAP_USD,
        help=f"Per-run --max-budget-usd cap (default: derived, ${PER_RUN_BUDGET_CAP_USD})",
    )
    parser.add_argument(
        "--timeout-s", type=int, default=run_skill_evals.SAMPLE_TIMEOUT_S,
        help=f"Per-run wall-clock timeout in seconds (default: {run_skill_evals.SAMPLE_TIMEOUT_S})",
    )
    parser.add_argument("--json", dest="json_out", metavar="PATH", help="Write full results JSON to PATH")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()

    if args.list:
        print_matrix()
        return 0

    abort_if_subagent_model_env_set()
    print_environment_report(gather_environment_report())

    if args.run is not None:
        matches = [r for r in RUN_MATRIX if r.number == args.run]
        if not matches:
            print(f"ERROR: no run numbered {args.run} in the matrix (valid: 1-7)", file=sys.stderr)
            return 2
        result = execute_matrix_cell(matches[0], budget_cap_usd=args.budget_cap_usd, timeout_s=args.timeout_s)
        print_run_result(result)
        if args.json_out:
            Path(args.json_out).write_text(json.dumps(result_to_dict(result), indent=2))
        return 0

    # args.all
    results: list[RunResult] = []
    for run in RUN_MATRIX:
        result = execute_matrix_cell(run, budget_cap_usd=args.budget_cap_usd, timeout_s=args.timeout_s)
        results.append(result)
        print_run_result(result)
        if should_stop_matrix_after(run, result):
            print(
                "ERROR: run 1 self-check failed — the harness appears to be reading "
                "the parent's model rather than the subagent's. Stopping before "
                "spending budget on runs 2-7.",
                file=sys.stderr,
            )
            break
    print(format_aggregate_spend_line(results))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps([result_to_dict(r) for r in results], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
